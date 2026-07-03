from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterable

from code_intelligence_agent.agents.bug_detector import RuleBasedBugDetector
from code_intelligence_agent.agents.patch_generator import PatchGenerator
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.fault_localizer import FaultLocalizer
from code_intelligence_agent.core.program_graph import build_program_graph
from code_intelligence_agent.core.repo_parser import RepoParser
from code_intelligence_agent.evaluation.benchmark_loader import (
    BenchmarkCase,
    BenchmarkLoader,
)
from code_intelligence_agent.evaluation.benchmark_runner import (
    _build_test_summary,
    _functions_by_name,
    _resolve_names,
)
from code_intelligence_agent.evaluation.metrics import average, patch_success_rate
from code_intelligence_agent.search.patch_risk import PatchRiskAnalyzer, annotate_patch_risk
from code_intelligence_agent.search.patch_search import PatchSearch, PatchSearchResult
from code_intelligence_agent.search.patch_judge import (
    PatchJudge,
    PatchJudgment,
    apply_patch_judgment_score,
    calibrate_patch_judgment,
)
from code_intelligence_agent.search.scoring import (
    DEFAULT_PATCH_SCORE_WEIGHTS,
    PatchScoreWeights,
    score_patch,
)
from code_intelligence_agent.tools.coverage_runner import CoverageRunner
from code_intelligence_agent.tools.sandbox import Sandbox


PATCH_PARETO_MAXIMIZE_FIELDS = (
    "validation_score",
    "top1_success",
    "mrr",
    "average_success_score_margin",
)
PATCH_PARETO_MINIMIZE_FIELDS = (
    "average_first_success_rank",
)
PATCH_PARETO_EPSILON = 1e-9


@dataclass(frozen=True)
class PatchWeightProfile:
    name: str
    weights: PatchScoreWeights
    patch_judge_weight: float = 0.0


@dataclass(frozen=True)
class PatchWeightSearchResult:
    profile: str
    weights: PatchScoreWeights
    validation_score: float
    top1_success: float
    mrr: float
    average_first_success_rank: float
    average_success_score_margin: float
    case_count: int
    patch_judge_weight: float = 0.0
    pareto_optimal: bool = True
    dominates_count: int = 0
    dominated_by_count: int = 0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["weights"] = self.weights.to_dict()
        return data


@dataclass(frozen=True)
class PatchJudgeFusionSummary:
    status: str
    profile_count: int
    judge_profile_count: int
    baseline_profile: str
    best_judge_profile: str
    best_judge_weight: float
    validation_delta: float
    top1_delta: float
    mrr_delta: float
    success_margin_delta: float
    first_success_rank_delta: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _PreparedPatchCase:
    results: list[PatchSearchResult]
    localization_scores: dict[str, float]


class PatchWeightSearchRunner:
    def __init__(
        self,
        parser: RepoParser | None = None,
        detector: RuleBasedBugDetector | None = None,
        localizer: FaultLocalizer | None = None,
        patch_generator: PatchGenerator | None = None,
        sandbox: Sandbox | None = None,
        coverage_runner: CoverageRunner | None = None,
        patch_judge: PatchJudge | None = None,
        use_dynamic_coverage: bool = True,
    ) -> None:
        self.parser = parser or RepoParser()
        self.detector = detector or RuleBasedBugDetector()
        self.localizer = localizer or FaultLocalizer()
        self.patch_generator = patch_generator or PatchGenerator()
        self.sandbox = sandbox or Sandbox(timeout=10)
        self.coverage_runner = coverage_runner or CoverageRunner(timeout=10)
        self.patch_judge = patch_judge
        self.use_dynamic_coverage = use_dynamic_coverage

    def search_manifest(
        self,
        manifest_path: str | Path,
        profiles: Iterable[PatchWeightProfile] | None = None,
    ) -> list[PatchWeightSearchResult]:
        cases = BenchmarkLoader().load_manifest(manifest_path)
        return self.search_cases(cases, profiles=profiles)

    def search_cases(
        self,
        cases: list[BenchmarkCase],
        profiles: Iterable[PatchWeightProfile] | None = None,
    ) -> list[PatchWeightSearchResult]:
        prepared_cases = [self._prepare_case(case) for case in cases]
        search_profiles = list(profiles or generate_patch_weight_grid())
        if profiles is None and self.patch_judge is not None:
            search_profiles = _with_patch_judge_profiles(search_profiles)
        results = [
            _evaluate_patch_profile(prepared_cases, profile)
            for profile in search_profiles
        ]
        results = annotate_patch_weight_search_pareto_frontier(results)
        return sorted(
            results,
            key=lambda item: (
                item.pareto_optimal,
                item.validation_score,
                item.top1_success,
                item.mrr,
                item.average_success_score_margin,
                -item.average_first_success_rank,
                item.dominates_count,
                -item.dominated_by_count,
            ),
            reverse=True,
        )

    def _prepare_case(self, case: BenchmarkCase) -> _PreparedPatchCase:
        parsed = self.parser.parse(case.repo_path)
        call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
        program_graph = build_program_graph(parsed, call_graph)
        findings = self.detector.detect(parsed.functions)
        functions_by_name = _functions_by_name(parsed.functions)
        ground_truth_ids = _resolve_names(case.buggy_functions, functions_by_name)
        test_summary = None
        if self.use_dynamic_coverage and (case.failing_tests or case.passed_tests):
            dynamic_summary = self.coverage_runner.build_summary(
                case.repo_path,
                parsed.functions,
                failing_tests=case.failing_tests,
                passed_tests=case.passed_tests,
            )
            if any(dynamic_summary.coverage.values()):
                test_summary = dynamic_summary
        if test_summary is None:
            test_summary = _build_test_summary(
                case=case,
                functions_by_name=functions_by_name,
                ground_truth_ids=ground_truth_ids,
            )
        ranked = self.localizer.rank(program_graph, findings, test_summary)
        localization_scores = {item.function_id: item.score for item in ranked}
        candidates = [
            annotate_patch_risk(
                candidate,
                PatchRiskAnalyzer().analyze(candidate, program_graph),
            )
            for candidate in self.patch_generator.generate(
                case.repo_path,
                parsed.functions,
                ranked,
            )
        ]
        if not candidates:
            return _PreparedPatchCase(results=[], localization_scores=localization_scores)
        results = PatchSearch(
            sandbox=self.sandbox,
            beam_width=len(candidates),
        ).search(
            case.repo_path,
            candidates,
            localization_scores=localization_scores,
            program_graph=program_graph,
            test_args=case.test_args,
        )
        if self.patch_judge is not None:
            results = [
                _with_patch_judgment(
                    result,
                    patch_judge=self.patch_judge,
                    localization_scores=localization_scores,
                )
                for result in results
            ]
        return _PreparedPatchCase(
            results=results,
            localization_scores=localization_scores,
        )


def generate_patch_weight_grid() -> list[PatchWeightProfile]:
    profiles = [
        PatchWeightProfile("default", DEFAULT_PATCH_SCORE_WEIGHTS),
    ]
    seen = {_weights_key(DEFAULT_PATCH_SCORE_WEIGHTS)}
    for feedback in (0.0, 0.04, 0.08, 0.12, 0.16):
        for diff_penalty in (0.03, 0.06, 0.10):
            for risk_penalty in (0.0, 0.03, 0.06):
                weights = PatchScoreWeights(
                    execution_feedback=feedback,
                    diff_penalty=diff_penalty,
                    risk_penalty=risk_penalty,
                )
                key = _weights_key(weights)
                if key in seen:
                    continue
                seen.add(key)
                profiles.append(
                    PatchWeightProfile(
                        name=_patch_profile_name(weights),
                        weights=weights,
                    )
                )
    return profiles


def rerank_patch_results(
    prepared_case: _PreparedPatchCase,
    profile: PatchWeightProfile,
) -> list[tuple[float, PatchSearchResult]]:
    scored = []
    for index, result in enumerate(prepared_case.results):
        candidate = result.candidate
        risk = candidate.metadata.get("risk", {})
        patch_risk = (
            float(risk.get("score", 0.0))
            if isinstance(risk, dict)
            else 0.0
        )
        score = score_patch(
            candidate=candidate,
            result=result.execution_result,
            localization_confidence=prepared_case.localization_scores.get(
                candidate.target_function_id,
                0.0,
            ),
            patch_risk=patch_risk,
            weights=profile.weights,
        )
        score = apply_patch_judgment_score(
            score,
            _patch_judgment(candidate),
            profile.patch_judge_weight,
        )
        scored.append((score, result.feedback_score, index, result))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [(score, result) for score, _, _, result in scored]


def annotate_patch_weight_search_pareto_frontier(
    results: list[PatchWeightSearchResult],
) -> list[PatchWeightSearchResult]:
    annotated: list[PatchWeightSearchResult] = []
    for result in results:
        dominates_count = 0
        dominated_by_count = 0
        for other in results:
            if other is result:
                continue
            if _dominates_patch_weight_profile(result, other):
                dominates_count += 1
            if _dominates_patch_weight_profile(other, result):
                dominated_by_count += 1
        annotated.append(
            replace(
                result,
                pareto_optimal=dominated_by_count == 0,
                dominates_count=dominates_count,
                dominated_by_count=dominated_by_count,
            )
        )
    return annotated


def patch_judge_fusion_summary(
    results: list[PatchWeightSearchResult],
) -> PatchJudgeFusionSummary:
    baseline_profiles = [
        result for result in results if float(result.patch_judge_weight) <= 0.0
    ]
    judge_profiles = [
        result for result in results if float(result.patch_judge_weight) > 0.0
    ]
    if not results:
        return PatchJudgeFusionSummary(
            status="missing",
            profile_count=0,
            judge_profile_count=0,
            baseline_profile="",
            best_judge_profile="",
            best_judge_weight=0.0,
            validation_delta=0.0,
            top1_delta=0.0,
            mrr_delta=0.0,
            success_margin_delta=0.0,
            first_success_rank_delta=0.0,
        )
    if not judge_profiles:
        baseline = _best_patch_weight_result(baseline_profiles or results)
        return PatchJudgeFusionSummary(
            status="not_evaluated",
            profile_count=len(results),
            judge_profile_count=0,
            baseline_profile=baseline.profile,
            best_judge_profile="",
            best_judge_weight=0.0,
            validation_delta=0.0,
            top1_delta=0.0,
            mrr_delta=0.0,
            success_margin_delta=0.0,
            first_success_rank_delta=0.0,
        )

    baseline = _best_patch_weight_result(baseline_profiles or results)
    best_judge = _best_patch_weight_result(judge_profiles)
    validation_delta = best_judge.validation_score - baseline.validation_score
    top1_delta = best_judge.top1_success - baseline.top1_success
    mrr_delta = best_judge.mrr - baseline.mrr
    margin_delta = (
        best_judge.average_success_score_margin
        - baseline.average_success_score_margin
    )
    rank_delta = (
        baseline.average_first_success_rank
        - best_judge.average_first_success_rank
    )
    status = "improved" if any(
        delta > 0
        for delta in (
            validation_delta,
            top1_delta,
            mrr_delta,
            margin_delta,
            rank_delta,
        )
    ) else "no_gain"
    return PatchJudgeFusionSummary(
        status=status,
        profile_count=len(results),
        judge_profile_count=len(judge_profiles),
        baseline_profile=baseline.profile,
        best_judge_profile=best_judge.profile,
        best_judge_weight=round(best_judge.patch_judge_weight, 4),
        validation_delta=round(validation_delta, 4),
        top1_delta=round(top1_delta, 4),
        mrr_delta=round(mrr_delta, 4),
        success_margin_delta=round(margin_delta, 4),
        first_success_rank_delta=round(rank_delta, 4),
    )


def _best_patch_weight_result(
    results: list[PatchWeightSearchResult],
) -> PatchWeightSearchResult:
    return max(
        results,
        key=lambda item: (
            item.validation_score,
            item.top1_success,
            item.mrr,
            item.average_success_score_margin,
            -item.average_first_success_rank,
            item.pareto_optimal,
            item.dominates_count,
            -item.dominated_by_count,
        ),
    )


def _dominates_patch_weight_profile(
    candidate: PatchWeightSearchResult,
    other: PatchWeightSearchResult,
) -> bool:
    at_least_as_good = all(
        float(getattr(candidate, field)) + PATCH_PARETO_EPSILON
        >= float(getattr(other, field))
        for field in PATCH_PARETO_MAXIMIZE_FIELDS
    ) and all(
        float(getattr(candidate, field))
        <= float(getattr(other, field)) + PATCH_PARETO_EPSILON
        for field in PATCH_PARETO_MINIMIZE_FIELDS
    )
    if not at_least_as_good:
        return False
    strictly_better = any(
        float(getattr(candidate, field))
        > float(getattr(other, field)) + PATCH_PARETO_EPSILON
        for field in PATCH_PARETO_MAXIMIZE_FIELDS
    ) or any(
        float(getattr(candidate, field))
        < float(getattr(other, field)) - PATCH_PARETO_EPSILON
        for field in PATCH_PARETO_MINIMIZE_FIELDS
    )
    return strictly_better


def _evaluate_patch_profile(
    cases: list[_PreparedPatchCase],
    profile: PatchWeightProfile,
) -> PatchWeightSearchResult:
    top1_successes: list[bool] = []
    reciprocal_ranks: list[float] = []
    first_success_ranks: list[int] = []
    success_margins: list[float] = []
    for case in cases:
        reranked = rerank_patch_results(case, profile)
        if not reranked:
            top1_successes.append(False)
            reciprocal_ranks.append(0.0)
            success_margins.append(0.0)
            continue
        top1_successes.append(reranked[0][1].success)
        first_success = next(
            (
                (rank, score, result)
                for rank, (score, result) in enumerate(reranked, start=1)
                if result.success
            ),
            None,
        )
        if first_success is None:
            reciprocal_ranks.append(0.0)
            success_margins.append(0.0)
            continue
        rank, success_score, _ = first_success
        first_success_ranks.append(rank)
        reciprocal_ranks.append(1.0 / rank)
        failed_scores = [
            score
            for score, result in reranked
            if not result.success
        ]
        margin = success_score - max(failed_scores) if failed_scores else 0.0
        success_margins.append(max(0.0, margin))

    top1 = patch_success_rate(top1_successes)
    mrr = average(reciprocal_ranks)
    average_rank = average(first_success_ranks)
    average_margin = average(success_margins)
    validation_score = 0.55 * top1 + 0.35 * mrr + 0.10 * min(1.0, average_margin)
    return PatchWeightSearchResult(
        profile=profile.name,
        weights=profile.weights,
        validation_score=round(validation_score, 4),
        top1_success=round(top1, 4),
        mrr=round(mrr, 4),
        average_first_success_rank=round(average_rank, 4),
        average_success_score_margin=round(average_margin, 4),
        case_count=len(cases),
        patch_judge_weight=round(profile.patch_judge_weight, 4),
    )


def _patch_profile_name(weights: PatchScoreWeights) -> str:
    name = (
        f"fb{_pct(weights.execution_feedback)}_"
        f"d{_pct(weights.diff_penalty)}_"
        f"r{_pct(weights.risk_penalty)}"
    )
    if weights.prior:
        name = f"{name}_p{_pct(weights.prior)}"
    return name


def _with_patch_judge_profiles(
    profiles: list[PatchWeightProfile],
) -> list[PatchWeightProfile]:
    output = list(profiles)
    default_weights = DEFAULT_PATCH_SCORE_WEIGHTS
    existing = {(profile.name, profile.patch_judge_weight) for profile in output}
    for judge_weight in (0.04, 0.08, 0.12):
        name = f"default_judge{_pct(judge_weight)}"
        key = (name, judge_weight)
        if key in existing:
            continue
        output.append(
            PatchWeightProfile(
                name=name,
                weights=default_weights,
                patch_judge_weight=judge_weight,
            )
        )
    return output


def _with_patch_judgment(
    result: PatchSearchResult,
    *,
    patch_judge: PatchJudge,
    localization_scores: dict[str, float],
) -> PatchSearchResult:
    candidate = result.candidate
    risk = candidate.metadata.get("risk", {})
    patch_risk = float(risk.get("score", 0.0)) if isinstance(risk, dict) else 0.0
    judgment = patch_judge.judge_patch(
        candidate=candidate,
        execution_result=result.execution_result,
        localization_confidence=localization_scores.get(
            candidate.target_function_id,
            0.0,
        ),
        patch_risk=patch_risk,
    )
    judgment = calibrate_patch_judgment(
        judgment,
        candidate=candidate,
        execution_result=result.execution_result,
        patch_risk=patch_risk,
    )
    judged_candidate = replace(
        candidate,
        metadata={
            **candidate.metadata,
            "patch_judgment": judgment.to_dict(),
        },
    )
    return replace(result, candidate=judged_candidate)


def _patch_judgment(candidate) -> PatchJudgment | None:
    judgment = candidate.metadata.get("patch_judgment", {})
    if not isinstance(judgment, dict):
        return None
    if "score" not in judgment:
        return None
    return PatchJudgment(
        score=float(judgment.get("score", 0.0)),
        verdict=str(judgment.get("verdict", "reject")),
        reason=str(judgment.get("reason", "")),
        model=str(judgment.get("model")) if judgment.get("model") else None,
        calibrated_score=(
            float(judgment["calibrated_score"])
            if judgment.get("calibrated_score") is not None
            else None
        ),
        agreement=str(judgment.get("agreement", "")),
        calibration_reasons=[
            str(item)
            for item in judgment.get("calibration_reasons", [])
            if item
        ],
    )


def _weights_key(weights: PatchScoreWeights) -> tuple[float, ...]:
    return (
        weights.tests_passed,
        weights.localization,
        weights.static_check,
        weights.prior,
        weights.execution_feedback,
        weights.diff_penalty,
        weights.risk_penalty,
        weights.warning_penalty,
        weights.success_bonus,
    )


def _pct(value: float) -> str:
    return f"{int(round(value * 100)):02d}"
