from __future__ import annotations

import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterable

from code_intelligence_agent.agents.bug_detector import RuleBasedBugDetector
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.fault_localizer import (
    DEFAULT_EVIDENCE_V2_COVERAGE_WEIGHTS,
    DEFAULT_EVIDENCE_V2_STATIC_ONLY_WEIGHTS,
    FaultLocalizationConfig,
    FaultLocalizer,
    ScoreWeights,
    score_with_weights,
)
from code_intelligence_agent.core.models import FaultLocalizationResult
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
from code_intelligence_agent.evaluation.metrics import (
    LocalizationRun,
    mean_exam_score,
    mean_average_precision,
    mean_ndcg,
    mean_reciprocal_rank,
    top_k_accuracy,
)
from code_intelligence_agent.tools.coverage_runner import CoverageRunner


ROBUST_TOP1_GAP_PENALTY = 0.10
ROBUST_MAP_GAP_PENALTY = 0.10
PARETO_MAXIMIZE_FIELDS = (
    "robust_validation_score",
    "validation_score",
    "top1",
    "top3",
    "mrr",
    "map",
    "ndcg_at_3",
)
PARETO_MINIMIZE_FIELDS = (
    "mean_exam_score",
    "max_top1_gap",
    "max_map_gap",
)
PARETO_EPSILON = 1e-9


@dataclass(frozen=True)
class WeightProfile:
    name: str
    coverage_weights: ScoreWeights
    static_only_weights: ScoreWeights | None = None


@dataclass(frozen=True)
class WeightSearchResult:
    profile: str
    coverage_weights: ScoreWeights
    static_only_weights: ScoreWeights
    validation_score: float
    robust_validation_score: float
    source_group_count: int
    min_source_group_cases: int
    source_groups: dict[str, dict[str, float | int]]
    holdout_splits: list[dict[str, object]]
    max_top1_gap: float
    max_map_gap: float
    top1: float
    top3: float
    top5: float
    mrr: float
    map: float
    ndcg_at_3: float
    mean_exam_score: float
    mean_localization_latency_ms: float
    case_count: int
    pareto_optimal: bool = True
    dominates_count: int = 0
    dominated_by_count: int = 0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["coverage_weights"] = self.coverage_weights.to_dict()
        data["static_only_weights"] = self.static_only_weights.to_dict()
        return data


@dataclass(frozen=True)
class _PreparedCase:
    ranked: list[FaultLocalizationResult]
    ground_truth: set[str]
    has_coverage: bool
    source_group: str
    localization_latency_ms: float = 0.0


@dataclass(frozen=True)
class _EvaluatedRun:
    run: LocalizationRun
    source_group: str


@dataclass(frozen=True)
class _LocalizationMetrics:
    top1: float
    top3: float
    top5: float
    mrr: float
    map: float
    ndcg_at_3: float
    mean_exam_score: float
    validation_score: float


class WeightSearchRunner:
    def __init__(
        self,
        parser: RepoParser | None = None,
        detector: RuleBasedBugDetector | None = None,
        localizer: FaultLocalizer | None = None,
        coverage_runner: CoverageRunner | None = None,
        use_dynamic_coverage: bool = True,
    ) -> None:
        self.parser = parser or RepoParser()
        self.detector = detector or RuleBasedBugDetector()
        self.localizer = localizer or FaultLocalizer()
        self.coverage_runner = coverage_runner or CoverageRunner(timeout=10)
        self.use_dynamic_coverage = use_dynamic_coverage

    def search_manifest(
        self,
        manifest_path: str | Path,
        profiles: Iterable[WeightProfile] | None = None,
    ) -> list[WeightSearchResult]:
        cases = BenchmarkLoader().load_manifest(manifest_path)
        return self.search_cases(cases, profiles=profiles)

    def search_cases(
        self,
        cases: list[BenchmarkCase],
        profiles: Iterable[WeightProfile] | None = None,
    ) -> list[WeightSearchResult]:
        prepared_cases = [self._prepare_case(case) for case in cases]
        search_profiles = list(profiles or generate_weight_grid())
        results = [
            _evaluate_profile(prepared_cases, profile)
            for profile in search_profiles
        ]
        results = annotate_weight_search_pareto_frontier(results)
        return sorted(
            results,
            key=lambda item: (
                item.pareto_optimal,
                item.robust_validation_score,
                item.validation_score,
                item.map,
                item.mrr,
                item.top1,
                item.top3,
                item.dominates_count,
                -item.dominated_by_count,
                -item.max_top1_gap,
                -item.max_map_gap,
            ),
            reverse=True,
        )

    def _prepare_case(self, case: BenchmarkCase) -> _PreparedCase:
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
        started = time.perf_counter()
        ranked = self.localizer.rank(program_graph, findings, test_summary)
        localization_latency_ms = (time.perf_counter() - started) * 1000.0
        return _PreparedCase(
            ranked=ranked,
            ground_truth=set(case.buggy_functions),
            has_coverage=test_summary.has_coverage(),
            source_group=_source_group(case),
            localization_latency_ms=round(localization_latency_ms, 4),
        )


def generate_weight_grid() -> list[WeightProfile]:
    profiles = [
        WeightProfile("default", ScoreWeights()),
    ]
    seen = {_profile_key(profiles[0])}
    for sbfl in (0.20, 0.30, 0.40):
        for graph in (0.15, 0.25, 0.35):
            for static in (0.10, 0.15, 0.25):
                for semantic in (0.05, 0.10):
                    for llm in (0.0, 0.15):
                        for risk in (0.0, 0.05, 0.10):
                            weights = ScoreWeights(
                                sbfl=sbfl,
                                graph=graph,
                                static=static,
                                semantic=semantic,
                                llm=llm,
                                risk=risk,
                            )
                            profile = WeightProfile(
                                name=_profile_name(weights),
                                coverage_weights=weights,
                            )
                            key = _profile_key(profile)
                            if key in seen:
                                continue
                            seen.add(key)
                            profiles.append(profile)
    return profiles


def generate_evidence_v2_weight_profiles() -> list[WeightProfile]:
    static_weights = DEFAULT_EVIDENCE_V2_STATIC_ONLY_WEIGHTS
    return [
        WeightProfile(
            "evidence_v2_default",
            DEFAULT_EVIDENCE_V2_COVERAGE_WEIGHTS,
            static_weights,
        ),
        WeightProfile(
            "evidence_v2_dynamic_heavy",
            ScoreWeights(
                sbfl=0.28,
                graph=0.12,
                static=0.12,
                semantic=0.04,
                llm=0.04,
                risk=0.05,
                test_failure=0.20,
                traceback=0.12,
                complexity=0.04,
                change_history=0.04,
            ),
            static_weights,
        ),
        WeightProfile(
            "evidence_v2_program_heavy",
            ScoreWeights(
                sbfl=0.18,
                graph=0.24,
                static=0.22,
                semantic=0.05,
                llm=0.04,
                risk=0.05,
                test_failure=0.10,
                traceback=0.07,
                complexity=0.06,
                change_history=0.04,
            ),
            static_weights,
        ),
        WeightProfile(
            "evidence_v2_balanced_low_prior",
            ScoreWeights(
                sbfl=0.24,
                graph=0.18,
                static=0.16,
                semantic=0.04,
                llm=0.04,
                risk=0.05,
                test_failure=0.16,
                traceback=0.10,
                complexity=0.04,
                change_history=0.04,
            ),
            static_weights,
        ),
    ]


def evidence_v2_ablation_profiles(
    fusion_profile: WeightProfile | None = None,
) -> list[WeightProfile]:
    zero = ScoreWeights(
        sbfl=0.0,
        graph=0.0,
        static=0.0,
        semantic=0.0,
        llm=0.0,
        risk=0.0,
    )
    fusion = fusion_profile or WeightProfile(
        "fusion",
        DEFAULT_EVIDENCE_V2_COVERAGE_WEIGHTS,
        DEFAULT_EVIDENCE_V2_STATIC_ONLY_WEIGHTS,
    )
    return [
        WeightProfile("rule_only", replace(zero, static=1.0), replace(zero, static=1.0)),
        WeightProfile("graph_only", replace(zero, graph=1.0), replace(zero, graph=1.0)),
        WeightProfile(
            "dynamic_only",
            replace(zero, sbfl=0.45, test_failure=0.35, traceback=0.20),
            zero,
        ),
        WeightProfile("llm_only", replace(zero, llm=1.0), replace(zero, llm=1.0)),
        WeightProfile(
            "without_graph",
            replace(fusion.coverage_weights, graph=0.0),
            replace(fusion.static_only_weights, graph=0.0),
        ),
        WeightProfile(
            "without_dynamic",
            replace(
                fusion.coverage_weights,
                sbfl=0.0,
                test_failure=0.0,
                traceback=0.0,
            ),
            replace(
                fusion.static_only_weights,
                sbfl=0.0,
                test_failure=0.0,
                traceback=0.0,
            ),
        ),
        fusion,
    ]


def rerank_with_weights(
    ranked: list[FaultLocalizationResult],
    weights: ScoreWeights,
) -> list[FaultLocalizationResult]:
    scored = [
        (
            score_with_weights(item.signals, weights),
            item.function_name,
            item.file_path,
            item,
        )
        for item in ranked
    ]
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [
        replace(
            item,
            score=round(score, 4),
            rank=index + 1,
        )
        for index, (score, _, _, item) in enumerate(scored)
    ]


def annotate_weight_search_pareto_frontier(
    results: list[WeightSearchResult],
) -> list[WeightSearchResult]:
    annotated: list[WeightSearchResult] = []
    for result in results:
        dominates_count = 0
        dominated_by_count = 0
        for other in results:
            if other is result:
                continue
            if _dominates_weight_profile(result, other):
                dominates_count += 1
            if _dominates_weight_profile(other, result):
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


def _dominates_weight_profile(
    candidate: WeightSearchResult,
    other: WeightSearchResult,
) -> bool:
    at_least_as_good = all(
        float(getattr(candidate, field)) + PARETO_EPSILON
        >= float(getattr(other, field))
        for field in PARETO_MAXIMIZE_FIELDS
    ) and all(
        float(getattr(candidate, field))
        <= float(getattr(other, field)) + PARETO_EPSILON
        for field in PARETO_MINIMIZE_FIELDS
    )
    if not at_least_as_good:
        return False
    strictly_better = any(
        float(getattr(candidate, field))
        > float(getattr(other, field)) + PARETO_EPSILON
        for field in PARETO_MAXIMIZE_FIELDS
    ) or any(
        float(getattr(candidate, field))
        < float(getattr(other, field)) - PARETO_EPSILON
        for field in PARETO_MINIMIZE_FIELDS
    )
    return strictly_better


def _evaluate_profile(
    cases: list[_PreparedCase],
    profile: WeightProfile,
) -> WeightSearchResult:
    static_only_weights = (
        profile.static_only_weights
        or FaultLocalizationConfig().static_only_weights
    )
    evaluated_runs = []
    for case in cases:
        weights = (
            profile.coverage_weights
            if case.has_coverage
            else static_only_weights
        )
        reranked = rerank_with_weights(case.ranked, weights)
        evaluated_runs.append(
            _EvaluatedRun(
                run=LocalizationRun(
                    ranked=[item.function_name for item in reranked],
                    ground_truth=case.ground_truth,
                ),
                source_group=case.source_group,
            )
        )
    metrics = _localization_metrics([item.run for item in evaluated_runs])
    robustness = _holdout_robustness(evaluated_runs, metrics.validation_score)
    return WeightSearchResult(
        profile=profile.name,
        coverage_weights=profile.coverage_weights,
        static_only_weights=static_only_weights,
        validation_score=metrics.validation_score,
        robust_validation_score=robustness["robust_validation_score"],
        source_group_count=robustness["source_group_count"],
        min_source_group_cases=robustness["min_source_group_cases"],
        source_groups=robustness["source_groups"],
        holdout_splits=robustness["holdout_splits"],
        max_top1_gap=robustness["max_top1_gap"],
        max_map_gap=robustness["max_map_gap"],
        top1=metrics.top1,
        top3=metrics.top3,
        top5=metrics.top5,
        mrr=metrics.mrr,
        map=metrics.map,
        ndcg_at_3=metrics.ndcg_at_3,
        mean_exam_score=metrics.mean_exam_score,
        mean_localization_latency_ms=round(
            _average([case.localization_latency_ms for case in cases]),
            4,
        ),
        case_count=len(cases),
    )


def _localization_metrics(runs: list[LocalizationRun]) -> _LocalizationMetrics:
    top1 = top_k_accuracy(runs, 1)
    top3 = top_k_accuracy(runs, 3)
    top5 = top_k_accuracy(runs, 5)
    mrr = mean_reciprocal_rank(runs)
    map_score = mean_average_precision(runs)
    ndcg_at_3 = mean_ndcg(runs, 3)
    exam = mean_exam_score(runs)
    validation_score = (
        0.25 * map_score
        + 0.25 * mrr
        + 0.20 * ndcg_at_3
        + 0.15 * top1
        + 0.10 * top3
        + 0.05 * (1.0 - exam)
    )
    return _LocalizationMetrics(
        top1=round(top1, 4),
        top3=round(top3, 4),
        top5=round(top5, 4),
        mrr=round(mrr, 4),
        map=round(map_score, 4),
        ndcg_at_3=round(ndcg_at_3, 4),
        mean_exam_score=round(exam, 4),
        validation_score=round(validation_score, 4),
    )


def _holdout_robustness(
    evaluated_runs: list[_EvaluatedRun],
    validation_score: float,
) -> dict[str, object]:
    groups = _runs_by_source_group(evaluated_runs)
    if not groups:
        return {
            "robust_validation_score": 0.0,
            "source_group_count": 0,
            "min_source_group_cases": 0,
            "source_groups": {},
            "holdout_splits": [],
            "max_top1_gap": 0.0,
            "max_map_gap": 0.0,
        }
    source_groups = {
        group: _metrics_dict(runs)
        for group, runs in sorted(groups.items())
    }
    holdout_splits: list[dict[str, object]] = []
    max_top1_gap = 0.0
    max_map_gap = 0.0
    if len(groups) > 1:
        all_runs = [item.run for item in evaluated_runs]
        for holdout_group, holdout_runs in sorted(groups.items()):
            train_groups = [
                group for group in sorted(groups) if group != holdout_group
            ]
            train_runs = [
                item.run
                for item in evaluated_runs
                if item.source_group != holdout_group
            ]
            if not train_runs or not holdout_runs:
                continue
            train_metrics = _localization_metrics(train_runs)
            holdout_metrics = _localization_metrics(holdout_runs)
            top1_gap = round(train_metrics.top1 - holdout_metrics.top1, 4)
            map_gap = round(train_metrics.map - holdout_metrics.map, 4)
            holdout_splits.append(
                {
                    "holdout_group": holdout_group,
                    "train_groups": train_groups,
                    "train_metrics": _metrics_dict(train_runs),
                    "holdout_metrics": _metrics_dict(holdout_runs),
                    "top1_gap": top1_gap,
                    "map_gap": map_gap,
                }
            )
            max_top1_gap = max(
                max_top1_gap,
                abs(top1_gap),
            )
            max_map_gap = max(
                max_map_gap,
                abs(map_gap),
            )
        if len(all_runs) != sum(len(items) for items in groups.values()):
            raise ValueError("Grouped holdout runs do not match evaluated run count.")
    robust_validation_score = max(
        0.0,
        validation_score
        - ROBUST_TOP1_GAP_PENALTY * max_top1_gap
        - ROBUST_MAP_GAP_PENALTY * max_map_gap,
    )
    return {
        "robust_validation_score": round(robust_validation_score, 4),
        "source_group_count": len(groups),
        "min_source_group_cases": min(len(items) for items in groups.values()),
        "source_groups": source_groups,
        "holdout_splits": holdout_splits,
        "max_top1_gap": round(max_top1_gap, 4),
        "max_map_gap": round(max_map_gap, 4),
    }


def _metrics_dict(runs: list[LocalizationRun]) -> dict[str, float | int]:
    metrics = _localization_metrics(runs)
    return {
        "case_count": len(runs),
        "top1": metrics.top1,
        "top3": metrics.top3,
        "top5": metrics.top5,
        "mrr": metrics.mrr,
        "map": metrics.map,
        "ndcg_at_3": metrics.ndcg_at_3,
        "mean_exam_score": metrics.mean_exam_score,
        "validation_score": metrics.validation_score,
    }


def _runs_by_source_group(
    evaluated_runs: list[_EvaluatedRun],
) -> dict[str, list[LocalizationRun]]:
    groups: dict[str, list[LocalizationRun]] = {}
    for item in evaluated_runs:
        groups.setdefault(item.source_group, []).append(item.run)
    return groups


def _source_group(case: BenchmarkCase) -> str:
    metadata = case.metadata if isinstance(case.metadata, dict) else {}
    for key in (
        "upstream",
        "source_project",
        "source_repo",
        "repo",
        "project",
    ):
        value = metadata.get(key)
        if value:
            return str(value)
    return _infer_source_group(case.name)


def _infer_source_group(case_name: str) -> str:
    if case_name.startswith("cpython_"):
        return "python/cpython"
    if case_name.startswith("thealgorithms_"):
        return "TheAlgorithms/Python"
    if case_name.startswith("pluggy_"):
        return "pytest-dev/pluggy"
    if case_name.startswith("click_"):
        return "pallets/click"
    return "unspecified"


def _profile_name(weights: ScoreWeights) -> str:
    return (
        f"s{_pct(weights.sbfl)}_g{_pct(weights.graph)}_"
        f"r{_pct(weights.static)}_m{_pct(weights.semantic)}_"
        f"l{_pct(weights.llm)}_p{_pct(weights.risk)}_"
        f"t{_pct(weights.test_failure)}_x{_pct(weights.traceback)}_"
        f"c{_pct(weights.complexity)}_h{_pct(weights.change_history)}"
    )


def _profile_key(profile: WeightProfile) -> tuple[tuple[float, ...], tuple[float, ...] | None]:
    static_weights = profile.static_only_weights
    return (
        _weights_key(profile.coverage_weights),
        _weights_key(static_weights) if static_weights else None,
    )


def _weights_key(weights: ScoreWeights) -> tuple[float, ...]:
    return (
        weights.sbfl,
        weights.graph,
        weights.static,
        weights.semantic,
        weights.llm,
        weights.risk,
        weights.test_failure,
        weights.traceback,
        weights.complexity,
        weights.change_history,
    )


def _pct(value: float) -> str:
    return f"{int(round(value * 100)):02d}"


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
