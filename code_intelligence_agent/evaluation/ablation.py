from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass
from pathlib import Path

from code_intelligence_agent.agents.bug_detector import RuleBasedBugDetector
from code_intelligence_agent.agents.multi_patch_repair import MultiPatchRepair
from code_intelligence_agent.agents.patch_generator import PatchGenerator
from code_intelligence_agent.agents.repair_loop import RepairLoop
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.fault_localizer import (
    FaultLocalizationConfig,
    FaultLocalizer,
)
from code_intelligence_agent.core.models import BugFinding, CodeEntity, TestExecutionSummary
from code_intelligence_agent.core.program_graph import build_program_graph
from code_intelligence_agent.core.repo_parser import RepoParser
from code_intelligence_agent.evaluation.benchmark_loader import (
    BenchmarkCase,
    BenchmarkLoader,
)
from code_intelligence_agent.evaluation.metrics import (
    LocalizationRun,
    average,
    mean_exam_score,
    mean_average_precision,
    mean_ndcg,
    mean_reciprocal_rank,
    patch_success_rate,
    top_k_accuracy,
)
from code_intelligence_agent.search.beam_patch_search import BeamPatchSearch
from code_intelligence_agent.search.patch_risk import PatchRiskAnalyzer, annotate_patch_risk
from code_intelligence_agent.tools.coverage_runner import CoverageRunner
from code_intelligence_agent.tools.sandbox import Sandbox


@dataclass(frozen=True)
class AblationResult:
    variant: str
    case_count: int
    top1: float
    top3: float
    mrr: float
    map: float = 0.0
    ndcg_at_3: float = 0.0
    mean_exam_score: float = 0.0
    expected_rule_recall: float = 0.0
    expected_rule_precision: float = 0.0
    average_extra_rules: float = 0.0
    patch_success_rate: float | None = None
    beam_success_rate: float | None = None
    multi_patch_success_rate: float | None = None
    average_repair_rounds: float | None = None
    localization_calibration_cases: int = 0
    localization_brier_score: float = 0.0
    localization_expected_calibration_error: float = 0.0
    localization_calibrated_brier_score: float = 0.0
    localization_calibrated_expected_calibration_error: float = 0.0
    localization_brier_score_improvement: float = 0.0
    localization_expected_calibration_error_improvement: float = 0.0


@dataclass(frozen=True)
class RuleEvaluationRun:
    expected: set[str]
    detected: set[str]


@dataclass(frozen=True)
class RepairEvaluationRun:
    patch_success: bool
    beam_success: bool
    multi_patch_success: bool
    repair_rounds: int


@dataclass(frozen=True)
class CalibrationEvaluationRun:
    confidence: float
    top1_hit: bool


@dataclass(frozen=True)
class _AblationCaseResult:
    localization: LocalizationRun
    calibration: CalibrationEvaluationRun
    rule_evaluation: RuleEvaluationRun
    repair_evaluation: RepairEvaluationRun | None = None


class AblationRunner:
    def summarize(
        self,
        rankings_by_variant: dict[str, list[LocalizationRun]],
        rule_runs_by_variant: dict[str, list[RuleEvaluationRun]] | None = None,
        repair_runs_by_variant: dict[str, list[RepairEvaluationRun]] | None = None,
        calibration_runs_by_variant: (
            dict[str, list[CalibrationEvaluationRun]] | None
        ) = None,
    ) -> list[AblationResult]:
        results = []
        rule_runs_by_variant = rule_runs_by_variant or {}
        repair_runs_by_variant = repair_runs_by_variant or {}
        calibration_runs_by_variant = calibration_runs_by_variant or {}
        for variant, runs in rankings_by_variant.items():
            rule_runs = rule_runs_by_variant.get(variant, [])
            repair_runs = repair_runs_by_variant.get(variant, [])
            calibration = _calibration_summary(
                calibration_runs_by_variant.get(variant, [])
            )
            results.append(
                AblationResult(
                    variant=variant,
                    case_count=len(runs),
                    top1=top_k_accuracy(runs, 1),
                    top3=top_k_accuracy(runs, 3),
                    mrr=mean_reciprocal_rank(runs),
                    map=mean_average_precision(runs),
                    ndcg_at_3=mean_ndcg(runs, 3),
                    mean_exam_score=mean_exam_score(runs),
                    expected_rule_recall=_expected_rule_recall(rule_runs),
                    expected_rule_precision=_expected_rule_precision(rule_runs),
                    average_extra_rules=_average_extra_rules(rule_runs),
                    patch_success_rate=_patch_success_rate(repair_runs),
                    beam_success_rate=_beam_success_rate(repair_runs),
                    multi_patch_success_rate=_multi_patch_success_rate(repair_runs),
                    average_repair_rounds=_average_repair_rounds(repair_runs),
                    localization_calibration_cases=calibration["case_count"],
                    localization_brier_score=calibration["brier_score"],
                    localization_expected_calibration_error=(
                        calibration["expected_calibration_error"]
                    ),
                    localization_calibrated_brier_score=(
                        calibration["calibrated_brier_score"]
                    ),
                    localization_calibrated_expected_calibration_error=(
                        calibration["calibrated_expected_calibration_error"]
                    ),
                    localization_brier_score_improvement=(
                        calibration["brier_score_improvement"]
                    ),
                    localization_expected_calibration_error_improvement=(
                        calibration["expected_calibration_error_improvement"]
                    ),
                )
            )
        return sorted(results, key=lambda item: (item.top1, item.mrr), reverse=True)


class BenchmarkAblationRunner:
    def __init__(
        self,
        parser: RepoParser | None = None,
        detector: RuleBasedBugDetector | None = None,
        localizer: FaultLocalizer | None = None,
        patch_generator: PatchGenerator | None = None,
        sandbox: Sandbox | None = None,
        coverage_runner: CoverageRunner | None = None,
        use_dynamic_coverage: bool = True,
    ) -> None:
        self.parser = parser or RepoParser()
        self.detector = detector or RuleBasedBugDetector()
        self.localizer = localizer or FaultLocalizer()
        self.patch_generator = patch_generator or PatchGenerator()
        self.sandbox = sandbox or Sandbox(timeout=10)
        self.coverage_runner = coverage_runner or CoverageRunner(timeout=10)
        self.use_dynamic_coverage = use_dynamic_coverage

    def run_manifest(self, manifest_path: str | Path) -> list[AblationResult]:
        cases = BenchmarkLoader().load_manifest(manifest_path)
        return self.run_cases(cases)

    def run_cases(self, cases: list[BenchmarkCase]) -> list[AblationResult]:
        rankings_by_variant = {
            "full": [],
            "without_rule_precision_filter": [],
            "without_reflection": [],
            "without_beam_search": [],
            "without_patch_prior": [],
            "without_diversity_reranking": [],
            "without_candidate_deduplication": [],
            "without_multi_patch_repair": [],
            "without_graph_bundle_search": [],
            "without_static_rules": [],
            "without_test_signals": [],
            "without_line_coverage": [],
            "without_branch_coverage": [],
            "without_path_coverage": [],
            "without_data_dependency": [],
            "without_control_flow": [],
            "without_pagerank": [],
            "without_caller_impact": [],
            "without_module_dependency": [],
            "without_async_call_graph": [],
            "without_semantic_similarity": [],
            "without_llm_score": [],
        }
        rule_runs_by_variant = {variant: [] for variant in rankings_by_variant}
        repair_runs_by_variant = {variant: [] for variant in rankings_by_variant}
        calibration_runs_by_variant = {variant: [] for variant in rankings_by_variant}
        for case in cases:
            case_results = self._run_case_variants(case)
            for variant, result in case_results.items():
                rankings_by_variant[variant].append(result.localization)
                calibration_runs_by_variant[variant].append(result.calibration)
                rule_runs_by_variant[variant].append(result.rule_evaluation)
                if result.repair_evaluation is not None:
                    repair_runs_by_variant[variant].append(result.repair_evaluation)
        return AblationRunner().summarize(
            rankings_by_variant,
            rule_runs_by_variant,
            repair_runs_by_variant,
            calibration_runs_by_variant,
        )

    def _run_case_variants(self, case: BenchmarkCase) -> dict[str, _AblationCaseResult]:
        parsed = self.parser.parse(case.repo_path)
        call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
        program_graph = build_program_graph(parsed, call_graph)
        findings = self.detector.detect(parsed.functions)
        unfiltered_findings = _with_unfiltered_rule_findings(
            parsed.functions,
            findings,
        )
        functions_by_name = _functions_by_name(parsed.functions)
        ground_truth_ids = _resolve_names(case.buggy_functions, functions_by_name)
        test_summary = self._build_test_summary(
            case=case,
            functions=parsed.functions,
            functions_by_name=functions_by_name,
            ground_truth_ids=ground_truth_ids,
        )
        empty_summary = TestExecutionSummary()
        llm_scorer = self.localizer.llm_scorer

        variants = {
            "full": self.localizer.rank(program_graph, findings, test_summary),
            "without_rule_precision_filter": self.localizer.rank(
                program_graph,
                unfiltered_findings,
                test_summary,
            ),
            "without_static_rules": self.localizer.rank(program_graph, [], test_summary),
            "without_test_signals": self.localizer.rank(
                program_graph,
                findings,
                empty_summary,
            ),
            "without_line_coverage": FaultLocalizer(
                FaultLocalizationConfig(use_line_coverage=False),
                llm_scorer=llm_scorer,
            ).rank(program_graph, findings, test_summary),
            "without_branch_coverage": FaultLocalizer(
                FaultLocalizationConfig(use_branch_coverage=False),
                llm_scorer=llm_scorer,
            ).rank(program_graph, findings, test_summary),
            "without_path_coverage": FaultLocalizer(
                FaultLocalizationConfig(use_path_coverage=False),
                llm_scorer=llm_scorer,
            ).rank(program_graph, findings, test_summary),
            "without_data_dependency": FaultLocalizer(
                FaultLocalizationConfig(use_data_dependency=False),
                llm_scorer=llm_scorer,
            ).rank(program_graph, findings, test_summary),
            "without_control_flow": FaultLocalizer(
                FaultLocalizationConfig(use_control_flow=False),
                llm_scorer=llm_scorer,
            ).rank(program_graph, findings, test_summary),
            "without_pagerank": FaultLocalizer(
                FaultLocalizationConfig(use_pagerank=False),
                llm_scorer=llm_scorer,
            ).rank(program_graph, findings, test_summary),
            "without_caller_impact": FaultLocalizer(
                FaultLocalizationConfig(use_caller_impact=False),
                llm_scorer=llm_scorer,
            ).rank(program_graph, findings, test_summary),
            "without_module_dependency": FaultLocalizer(
                FaultLocalizationConfig(use_module_dependency=False),
                llm_scorer=llm_scorer,
            ).rank(program_graph, findings, test_summary),
            "without_async_call_graph": FaultLocalizer(
                FaultLocalizationConfig(use_async_calls=False),
                llm_scorer=llm_scorer,
            ).rank(program_graph, findings, test_summary),
            "without_semantic_similarity": FaultLocalizer(
                FaultLocalizationConfig(use_semantic_similarity=False),
                llm_scorer=llm_scorer,
            ).rank(program_graph, findings, test_summary),
            "without_llm_score": FaultLocalizer(
                FaultLocalizationConfig(use_llm_score=False),
                llm_scorer=llm_scorer,
            ).rank(program_graph, findings, test_summary),
        }
        variants["without_reflection"] = variants["full"]
        variants["without_beam_search"] = variants["full"]
        variants["without_patch_prior"] = variants["full"]
        variants["without_diversity_reranking"] = variants["full"]
        variants["without_candidate_deduplication"] = variants["full"]
        variants["without_multi_patch_repair"] = variants["full"]
        variants["without_graph_bundle_search"] = variants["full"]
        findings_by_variant = {
            name: findings for name in variants
        }
        findings_by_variant["without_rule_precision_filter"] = unfiltered_findings
        findings_by_variant["without_static_rules"] = []
        repair_by_variant = _repair_ablation_variants(
            case=case,
            functions=parsed.functions,
            ranked=variants["full"],
            candidates=_patch_candidates(
                repo_path=case.repo_path,
                functions=parsed.functions,
                ranked=variants["full"],
                patch_generator=self.patch_generator,
                program_graph=program_graph,
            ),
            program_graph=program_graph,
            sandbox=self.sandbox,
        )
        return {
            name: _AblationCaseResult(
                localization=LocalizationRun(
                    ranked=[item.function_name for item in ranked],
                    ground_truth=set(case.buggy_functions),
                ),
                calibration=_calibration_run(ranked, set(case.buggy_functions)),
                rule_evaluation=RuleEvaluationRun(
                    expected=set(case.expected_rule_ids),
                    detected={
                        finding.rule_id
                        for finding in findings_by_variant.get(name, [])
                    },
                ),
                repair_evaluation=repair_by_variant.get(name),
            )
            for name, ranked in variants.items()
        }

    def _build_test_summary(
        self,
        *,
        case: BenchmarkCase,
        functions: list[CodeEntity],
        functions_by_name: dict[str, CodeEntity],
        ground_truth_ids: set[str],
    ) -> TestExecutionSummary:
        if self.use_dynamic_coverage and (case.failing_tests or case.passed_tests):
            summary = self.coverage_runner.build_summary(
                case.repo_path,
                functions,
                failing_tests=case.failing_tests,
                passed_tests=case.passed_tests,
            )
            if any(summary.coverage.values()):
                return summary
        return _build_test_summary(case, functions_by_name, ground_truth_ids)


def _expected_rule_recall(runs: list[RuleEvaluationRun]) -> float:
    if not runs:
        return 0.0
    scores = []
    for run in runs:
        if not run.expected:
            scores.append(0.0)
            continue
        scores.append(len(run.expected.intersection(run.detected)) / len(run.expected))
    return sum(scores) / len(scores)


def _expected_rule_precision(runs: list[RuleEvaluationRun]) -> float:
    if not runs:
        return 0.0
    scores = []
    for run in runs:
        if not run.detected:
            scores.append(0.0)
            continue
        scores.append(len(run.expected.intersection(run.detected)) / len(run.detected))
    return sum(scores) / len(scores)


def _average_extra_rules(runs: list[RuleEvaluationRun]) -> float:
    if not runs:
        return 0.0
    return sum(len(run.detected - run.expected) for run in runs) / len(runs)


def _patch_success_rate(runs: list[RepairEvaluationRun]) -> float | None:
    if not runs:
        return None
    return patch_success_rate([run.patch_success for run in runs])


def _beam_success_rate(runs: list[RepairEvaluationRun]) -> float | None:
    if not runs:
        return None
    return patch_success_rate([run.beam_success for run in runs])


def _multi_patch_success_rate(runs: list[RepairEvaluationRun]) -> float | None:
    if not runs:
        return None
    return patch_success_rate([run.multi_patch_success for run in runs])


def _average_repair_rounds(runs: list[RepairEvaluationRun]) -> float | None:
    if not runs:
        return None
    return average([run.repair_rounds for run in runs])


def _calibration_run(
    ranked,
    ground_truth: set[str],
) -> CalibrationEvaluationRun:
    if not ranked:
        return CalibrationEvaluationRun(confidence=0.0, top1_hit=False)
    top = ranked[0]
    return CalibrationEvaluationRun(
        confidence=max(0.0, min(1.0, float(getattr(top, "score", 0.0) or 0.0))),
        top1_hit=str(getattr(top, "function_name", "")) in ground_truth,
    )


def _calibration_summary(runs: list[CalibrationEvaluationRun]) -> dict[str, float | int]:
    if not runs:
        return {
            "case_count": 0,
            "brier_score": 0.0,
            "expected_calibration_error": 0.0,
            "calibrated_brier_score": 0.0,
            "calibrated_expected_calibration_error": 0.0,
            "brier_score_improvement": 0.0,
            "expected_calibration_error_improvement": 0.0,
        }
    labels = [1.0 if run.top1_hit else 0.0 for run in runs]
    confidences = [max(0.0, min(1.0, run.confidence)) for run in runs]
    raw_brier = _brier(confidences, labels)
    raw_ece = _ece(confidences, labels)
    calibrated = _calibrated_confidences(runs)
    calibrated_brier = _brier(calibrated, labels)
    calibrated_ece = _ece(calibrated, labels)
    return {
        "case_count": len(runs),
        "brier_score": raw_brier,
        "expected_calibration_error": raw_ece,
        "calibrated_brier_score": calibrated_brier,
        "calibrated_expected_calibration_error": calibrated_ece,
        "brier_score_improvement": round(raw_brier - calibrated_brier, 4),
        "expected_calibration_error_improvement": round(raw_ece - calibrated_ece, 4),
    }


def _calibrated_confidences(
    runs: list[CalibrationEvaluationRun],
    bin_count: int = 10,
    prior_strength: float = 10.0,
) -> list[float]:
    if not runs:
        return []
    global_accuracy = sum(1.0 for run in runs if run.top1_hit) / len(runs)
    prior_mean = max(0.05, min(0.95, global_accuracy))
    counts: dict[int, int] = {}
    positives: dict[int, float] = {}
    bins = []
    for run in runs:
        index = _bin_index(run.confidence, bin_count)
        bins.append(index)
        counts[index] = counts.get(index, 0) + 1
        positives[index] = positives.get(index, 0.0) + (1.0 if run.top1_hit else 0.0)
    output = []
    for run, index in zip(runs, bins):
        label = 1.0 if run.top1_hit else 0.0
        output.append(
            max(
                0.0,
                min(
                    1.0,
                    (
                        positives[index]
                        - label
                        + prior_strength * prior_mean
                    )
                    / (counts[index] - 1 + prior_strength),
                ),
            )
        )
    return output


def _brier(confidences: list[float], labels: list[float]) -> float:
    if not confidences:
        return 0.0
    return round(
        sum((confidence - label) ** 2 for confidence, label in zip(confidences, labels))
        / len(confidences),
        4,
    )


def _ece(
    confidences: list[float],
    labels: list[float],
    bin_count: int = 10,
) -> float:
    if not confidences:
        return 0.0
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(bin_count)]
    for confidence, label in zip(confidences, labels):
        buckets[_bin_index(confidence, bin_count)].append((confidence, label))
    total = len(confidences)
    ece = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        avg_confidence = sum(confidence for confidence, _ in bucket) / len(bucket)
        accuracy = sum(label for _, label in bucket) / len(bucket)
        ece += len(bucket) / total * abs(avg_confidence - accuracy)
    return round(ece, 4)


def _bin_index(confidence: float, bin_count: int) -> int:
    return min(bin_count - 1, int(max(0.0, min(1.0, confidence)) * bin_count))


def _patch_candidates(
    *,
    repo_path: str | Path,
    functions: list[CodeEntity],
    ranked,
    patch_generator: PatchGenerator,
    program_graph,
):
    return [
        annotate_patch_risk(
            candidate,
            PatchRiskAnalyzer().analyze(candidate, program_graph),
        )
        for candidate in patch_generator.generate(repo_path, functions, ranked)
    ]


def _repair_ablation_variants(
    *,
    case: BenchmarkCase,
    functions: list[CodeEntity],
    ranked,
    candidates,
    program_graph,
    sandbox: Sandbox,
) -> dict[str, RepairEvaluationRun]:
    del functions
    if not candidates:
        empty = RepairEvaluationRun(
            patch_success=False,
            beam_success=False,
            multi_patch_success=False,
            repair_rounds=0,
        )
        return {
            "full": empty,
            "without_reflection": empty,
            "without_beam_search": empty,
            "without_patch_prior": empty,
            "without_diversity_reranking": empty,
            "without_candidate_deduplication": empty,
            "without_multi_patch_repair": empty,
            "without_graph_bundle_search": empty,
        }

    localization_scores = {item.function_id: item.score for item in ranked}
    beam_success = _beam_success(
        case=case,
        candidates=candidates,
        localization_scores=localization_scores,
        sandbox=sandbox,
    )
    no_prior_beam_success = _beam_success(
        case=case,
        candidates=candidates,
        localization_scores=localization_scores,
        sandbox=sandbox,
        use_prior_ranking=False,
    )
    no_diversity_beam_success = _beam_success(
        case=case,
        candidates=candidates,
        localization_scores=localization_scores,
        sandbox=sandbox,
        use_diversity_reranking=False,
    )
    no_dedup_beam_success = _beam_success(
        case=case,
        candidates=candidates,
        localization_scores=localization_scores,
        sandbox=sandbox,
        use_candidate_deduplication=False,
    )
    full_repair = RepairLoop(
        sandbox=sandbox,
        max_rounds=max(1, min(3, len(candidates))),
    ).run(
        case.repo_path,
        candidates,
        localization_scores=localization_scores,
        test_args=case.test_args,
    )
    no_prior_repair = RepairLoop(
        sandbox=sandbox,
        max_rounds=max(1, min(3, len(candidates))),
        use_prior_ranking=False,
    ).run(
        case.repo_path,
        candidates,
        localization_scores=localization_scores,
        test_args=case.test_args,
    )
    no_diversity_repair = RepairLoop(
        sandbox=sandbox,
        max_rounds=max(1, min(3, len(candidates))),
        use_diversity_reranking=False,
    ).run(
        case.repo_path,
        candidates,
        localization_scores=localization_scores,
        test_args=case.test_args,
    )
    no_dedup_repair = RepairLoop(
        sandbox=sandbox,
        max_rounds=max(1, min(3, len(candidates))),
        use_candidate_deduplication=False,
    ).run(
        case.repo_path,
        candidates,
        localization_scores=localization_scores,
        test_args=case.test_args,
    )
    no_prior_multi = _multi_patch_result(
        case=case,
        candidates=candidates,
        localization_scores=localization_scores,
        program_graph=program_graph,
        sandbox=sandbox,
        enabled=not no_prior_repair.success,
    )
    no_diversity_multi = _multi_patch_result(
        case=case,
        candidates=candidates,
        localization_scores=localization_scores,
        program_graph=program_graph,
        sandbox=sandbox,
        enabled=not no_diversity_repair.success,
    )
    no_dedup_multi = _multi_patch_result(
        case=case,
        candidates=candidates,
        localization_scores=localization_scores,
        program_graph=program_graph,
        sandbox=sandbox,
        enabled=not no_dedup_repair.success,
    )
    full_multi = _multi_patch_result(
        case=case,
        candidates=candidates,
        localization_scores=localization_scores,
        program_graph=program_graph,
        sandbox=sandbox,
        enabled=not full_repair.success,
    )
    no_graph_bundle_multi = _multi_patch_result(
        case=case,
        candidates=candidates,
        localization_scores=localization_scores,
        program_graph=program_graph,
        sandbox=sandbox,
        enabled=not full_repair.success,
        use_graph_bundle_ranking=False,
    )
    no_reflection_repair = RepairLoop(
        sandbox=sandbox,
        max_rounds=1,
    ).run(
        case.repo_path,
        candidates,
        localization_scores=localization_scores,
        test_args=case.test_args,
    )
    no_reflection_multi = _multi_patch_result(
        case=case,
        candidates=candidates,
        localization_scores=localization_scores,
        program_graph=program_graph,
        sandbox=sandbox,
        enabled=not no_reflection_repair.success,
    )
    full = RepairEvaluationRun(
        patch_success=full_repair.success or full_multi.success,
        beam_success=beam_success,
        multi_patch_success=full_multi.success,
        repair_rounds=full_repair.rounds + full_multi.rounds,
    )
    no_reflection = RepairEvaluationRun(
        patch_success=no_reflection_repair.success or no_reflection_multi.success,
        beam_success=beam_success,
        multi_patch_success=no_reflection_multi.success,
        repair_rounds=no_reflection_repair.rounds + no_reflection_multi.rounds,
    )
    without_beam = RepairEvaluationRun(
        patch_success=full.patch_success,
        beam_success=False,
        multi_patch_success=full.multi_patch_success,
        repair_rounds=full.repair_rounds,
    )
    without_patch_prior = RepairEvaluationRun(
        patch_success=no_prior_repair.success or no_prior_multi.success,
        beam_success=no_prior_beam_success,
        multi_patch_success=no_prior_multi.success,
        repair_rounds=no_prior_repair.rounds + no_prior_multi.rounds,
    )
    without_diversity = RepairEvaluationRun(
        patch_success=no_diversity_repair.success or no_diversity_multi.success,
        beam_success=no_diversity_beam_success,
        multi_patch_success=no_diversity_multi.success,
        repair_rounds=no_diversity_repair.rounds + no_diversity_multi.rounds,
    )
    without_candidate_deduplication = RepairEvaluationRun(
        patch_success=no_dedup_repair.success or no_dedup_multi.success,
        beam_success=no_dedup_beam_success,
        multi_patch_success=no_dedup_multi.success,
        repair_rounds=no_dedup_repair.rounds + no_dedup_multi.rounds,
    )
    without_multi_patch = RepairEvaluationRun(
        patch_success=full_repair.success,
        beam_success=beam_success,
        multi_patch_success=False,
        repair_rounds=full_repair.rounds,
    )
    without_graph_bundle = RepairEvaluationRun(
        patch_success=full_repair.success or no_graph_bundle_multi.success,
        beam_success=beam_success,
        multi_patch_success=no_graph_bundle_multi.success,
        repair_rounds=full_repair.rounds + no_graph_bundle_multi.rounds,
    )
    return {
        "full": full,
        "without_reflection": no_reflection,
        "without_beam_search": without_beam,
        "without_patch_prior": without_patch_prior,
        "without_diversity_reranking": without_diversity,
        "without_candidate_deduplication": without_candidate_deduplication,
        "without_multi_patch_repair": without_multi_patch,
        "without_graph_bundle_search": without_graph_bundle,
    }


def _beam_success(
    *,
    case: BenchmarkCase,
    candidates,
    localization_scores: dict[str, float],
    sandbox: Sandbox,
    use_prior_ranking: bool = True,
    use_diversity_reranking: bool = True,
    use_candidate_deduplication: bool = True,
) -> bool:
    results = BeamPatchSearch(
        sandbox=sandbox,
        beam_width=3,
        candidate_pool_size=4,
        max_depth=2,
        use_prior_ranking=use_prior_ranking,
        use_diversity_reranking=use_diversity_reranking,
        use_candidate_deduplication=use_candidate_deduplication,
    ).search(
        case.repo_path,
        candidates,
        localization_scores=localization_scores,
        test_args=case.test_args,
    )
    return any(node.success for node in results)


def _multi_patch_result(
    *,
    case: BenchmarkCase,
    candidates,
    localization_scores: dict[str, float],
    program_graph,
    sandbox: Sandbox,
    enabled: bool,
    use_graph_bundle_ranking: bool = True,
):
    if not enabled:
        return MultiPatchRepair(sandbox=sandbox, max_attempts=0).run(
            case.repo_path,
            [],
            localization_scores=localization_scores,
            program_graph=program_graph,
            test_args=case.test_args,
        )
    return MultiPatchRepair(
        sandbox=sandbox,
        max_bundle_size=max(2, min(3, len(set(case.buggy_functions)) or 2)),
        variants_per_function=2,
        max_attempts=8,
        use_graph_bundle_ranking=use_graph_bundle_ranking,
    ).run(
        case.repo_path,
        candidates,
        localization_scores=localization_scores,
        program_graph=program_graph,
        test_args=case.test_args,
    )


def _with_unfiltered_rule_findings(
    functions: list[CodeEntity],
    findings: list[BugFinding],
) -> list[BugFinding]:
    output = list(findings)
    existing = {_finding_key(finding) for finding in findings}
    for function in functions:
        for finding in [
            *_unfiltered_len_denominator_findings(function),
            *_unfiltered_stringified_numeric_findings(function),
            *_unfiltered_inplace_api_findings(function),
        ]:
            key = _finding_key(finding)
            if key in existing:
                continue
            existing.add(key)
            output.append(finding)
    return output


def _finding_key(finding: BugFinding) -> tuple:
    return (
        finding.function_id,
        finding.rule_id,
        finding.line,
        tuple(sorted((str(key), str(value)) for key, value in finding.evidence.items())),
    )


def _unfiltered_len_denominator_findings(function: CodeEntity) -> list[BugFinding]:
    try:
        tree = ast.parse(textwrap.dedent(function.source))
    except SyntaxError:
        return []
    root = tree.body[0] if tree.body else None
    if not isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []

    len_assignments: dict[str, ast.Assign] = {}
    for node in ast.walk(root):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if _is_len_call(node.value):
            len_assignments[node.targets[0].id] = node
    if not len_assignments:
        return []

    denominator_uses = _len_denominator_uses(root, set(len_assignments))
    output = []
    for name, use_line in sorted(denominator_uses.items()):
        assignment = len_assignments[name]
        if use_line <= getattr(assignment, "lineno", 0):
            continue
        output.append(
            BugFinding(
                rule_id="missing_len_zero_guard",
                bug_type="zero division error",
                message=(
                    "len-derived denominator is used without the precision "
                    "filter that recognizes positive-threshold guards."
                ),
                file_path=Path(function.file_path).as_posix(),
                function_id=function.id,
                function_name=function.metadata.get(
                    "qualified_name",
                    function.name,
                ),
                line=function.start_line + getattr(assignment, "lineno", 1) - 1,
                confidence=0.74,
                evidence={"variable": name, "denominator_line": use_line},
            )
        )
    return output


def _unfiltered_stringified_numeric_findings(function: CodeEntity) -> list[BugFinding]:
    try:
        tree = ast.parse(textwrap.dedent(function.source))
    except SyntaxError:
        return []
    root = tree.body[0] if tree.body else None
    if not isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    candidates: dict[str, ast.Assign] = {}
    for node in ast.walk(root):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if _is_str_wrapped_numeric_expression(node.value):
            candidates[node.targets[0].id] = node
    if not candidates:
        return []

    visitor = _UnfilteredNumericUseVisitor(set(candidates))
    visitor.visit(root)
    output = []
    for name in sorted(visitor.names):
        assignment = candidates[name]
        output.append(
            BugFinding(
                rule_id="stringified_numeric_value",
                bug_type="type error",
                message=(
                    "Numeric value is converted to str and later used in a context "
                    "that the precision filter would check for mapping lookups."
                ),
                file_path=Path(function.file_path).as_posix(),
                function_id=function.id,
                function_name=function.metadata.get(
                    "qualified_name",
                    function.name,
                ),
                line=function.start_line + getattr(assignment, "lineno", 1) - 1,
                confidence=0.76,
                evidence={"variable": name},
            )
        )
    return output


def _unfiltered_inplace_api_findings(function: CodeEntity) -> list[BugFinding]:
    try:
        tree = ast.parse(textwrap.dedent(function.source))
    except SyntaxError:
        return []
    root = tree.body[0] if tree.body else None
    if not isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    output = []
    for node in ast.walk(root):
        if not isinstance(node, ast.Assign) or not _is_unfiltered_inplace_assignment(
            node
        ):
            continue
        call = node.value
        assert isinstance(call, ast.Call)
        assert isinstance(call.func, ast.Attribute)
        output.append(
            BugFinding(
                rule_id="inplace_api_return_value",
                bug_type="api misuse",
                message=(
                    "Result of a mutating-looking API is assigned without the "
                    "precision filter that ignores self/cls attributes."
                ),
                file_path=Path(function.file_path).as_posix(),
                function_id=function.id,
                function_name=function.metadata.get(
                    "qualified_name",
                    function.name,
                ),
                line=function.start_line + getattr(node, "lineno", 1) - 1,
                confidence=0.78,
                evidence={
                    "method": call.func.attr,
                    "receiver": _expr_source(call.func.value),
                },
            )
        )
    return output


def _len_denominator_uses(
    root: ast.FunctionDef | ast.AsyncFunctionDef,
    candidate_names: set[str],
) -> dict[str, int]:
    visitor = _LenDenominatorUseVisitor(candidate_names)
    visitor.visit(root)
    return visitor.names


class _LenDenominatorUseVisitor(ast.NodeVisitor):
    def __init__(self, candidate_names: set[str]) -> None:
        self.candidate_names = candidate_names
        self.names: dict[str, int] = {}

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            if isinstance(node.right, ast.Name) and node.right.id in self.candidate_names:
                self.names.setdefault(node.right.id, getattr(node.right, "lineno", 0))
        self.generic_visit(node)


def _is_len_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "len"
        and len(node.args) == 1
    )


_INPLACE_RETURN_NONE_METHODS = {
    "add",
    "append",
    "clear",
    "discard",
    "extend",
    "insert",
    "remove",
    "reverse",
    "sort",
    "update",
}


def _is_unfiltered_inplace_assignment(node: ast.Assign) -> bool:
    if len(node.targets) != 1:
        return False
    if not isinstance(node.targets[0], ast.Name):
        return False
    call = node.value
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr in _INPLACE_RETURN_NONE_METHODS
        and isinstance(call.func.value, (ast.Name, ast.Attribute))
    )


def _is_str_wrapped_numeric_expression(node: ast.AST) -> bool:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "str"
        and len(node.args) == 1
        and not node.keywords
    ):
        return False
    return _looks_numeric_expression(node.args[0])


def _looks_numeric_expression(node: ast.AST) -> bool:
    if _is_len_call(node):
        return True
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return True
    if isinstance(node, ast.BinOp) and isinstance(
        node.op,
        (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod),
    ):
        return True
    return False


class _UnfilteredNumericUseVisitor(ast.NodeVisitor):
    def __init__(self, candidate_names: set[str]) -> None:
        self.candidate_names = candidate_names
        self.names: set[str] = set()

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(
            node.op,
            (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod),
        ):
            self._record_name(node.left)
            self._record_name(node.right)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        values = [node.left, *node.comparators]
        if any(_is_numeric_constant(value) for value in values):
            for value in values:
                self._record_name(value)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self._record_name(node.slice)
        self.generic_visit(node)

    def _record_name(self, node: ast.AST) -> None:
        if isinstance(node, ast.Name) and node.id in self.candidate_names:
            self.names.add(node.id)


def _is_numeric_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float))


def _expr_source(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _functions_by_name(functions: list[CodeEntity]) -> dict[str, CodeEntity]:
    result = {}
    for function in functions:
        qualified = function.metadata.get("qualified_name", function.name)
        result[qualified] = function
        result.setdefault(function.name, function)
    return result


def _resolve_names(
    names: list[str],
    functions_by_name: dict[str, CodeEntity],
) -> set[str]:
    return {
        functions_by_name[name].id
        for name in names
        if name in functions_by_name
    }


def _build_test_summary(
    case: BenchmarkCase,
    functions_by_name: dict[str, CodeEntity],
    ground_truth_ids: set[str],
) -> TestExecutionSummary:
    failed_ids = _resolve_names(case.failing_tests, functions_by_name)
    passed_ids = _resolve_names(case.passed_tests, functions_by_name)
    coverage = {test_id: set(ground_truth_ids) for test_id in failed_ids}
    line_coverage = {
        test_id: {function_id: 1.0 for function_id in ground_truth_ids}
        for test_id in failed_ids
    }
    for test_id in passed_ids:
        coverage[test_id] = set()
        line_coverage[test_id] = {}
    test_names = {}
    for test_name in case.failing_tests + case.passed_tests:
        if test_name in functions_by_name:
            function = functions_by_name[test_name]
            test_names[function.id] = function.name
    return TestExecutionSummary(
        failed_tests=failed_ids,
        passed_tests=passed_ids,
        coverage=coverage,
        line_coverage=line_coverage,
        traceback_function_ids=set(ground_truth_ids),
        test_names=test_names,
        failure_messages={
            test_id: " ".join(
                case.expected_rule_ids + [str(case.metadata.get("bug_type", ""))]
            )
            for test_id in failed_ids
        },
    )
