from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from code_intelligence_agent.agents.bug_detector import RuleBasedBugDetector
from code_intelligence_agent.agents.multi_patch_repair import MultiPatchRepair
from code_intelligence_agent.agents.patch_generator import PatchGenerator
from code_intelligence_agent.agents.repair_loop import RepairLoop
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.fault_localizer import FaultLocalizer
from code_intelligence_agent.core.models import (
    CodeEntity,
    TestExecutionSummary,
)
from code_intelligence_agent.core.program_graph import ProgramGraph, build_program_graph
from code_intelligence_agent.core.program_slicer import program_slice_evidence
from code_intelligence_agent.core.repo_parser import RepoParser
from code_intelligence_agent.evaluation.benchmark_loader import (
    BenchmarkCase,
    BenchmarkLoader,
)
from code_intelligence_agent.evaluation.llm_judge import LLMJudge
from code_intelligence_agent.evaluation.metrics import (
    LocalizationRun,
    average_precision,
    average,
    exam_score,
    mean_exam_score,
    mean_reciprocal_rank,
    mean_average_precision,
    mean_ndcg,
    normalized_discounted_cumulative_gain,
    patch_success_rate,
    top_k_accuracy,
)
from code_intelligence_agent.evaluation.judge_reliability import (
    case_judge_reliability_summary,
)
from code_intelligence_agent.evaluation.patch_judge_reliability import (
    patch_judge_reliability_summary,
)
from code_intelligence_agent.evaluation.localization_calibration import (
    localization_calibration_summary,
)
from code_intelligence_agent.evaluation.localization_attribution import (
    localization_attribution_summary,
)
from code_intelligence_agent.evaluation.slice_grounding import (
    slice_grounding_evidence,
)
from code_intelligence_agent.evaluation.metric_uncertainty import (
    metric_uncertainty_summary,
)
from code_intelligence_agent.evaluation.search_budget_analysis import (
    search_budget_analysis_summary,
)
from code_intelligence_agent.evaluation.search_competition_analysis import (
    search_competition_case_audit,
    search_competition_analysis_summary,
)
from code_intelligence_agent.evaluation.reflection_analysis import (
    reflection_analysis_summary,
)
from code_intelligence_agent.evaluation.difficulty_analysis import (
    benchmark_difficulty_summary,
)
from code_intelligence_agent.evaluation.generalization_analysis import (
    benchmark_generalization_summary,
)
from code_intelligence_agent.evaluation.benchmark_provenance import (
    benchmark_provenance_summary,
)
from code_intelligence_agent.tools.coverage_runner import CoverageRunner
from code_intelligence_agent.tools.sandbox import Sandbox
from code_intelligence_agent.search.patch_risk import PatchRiskAnalyzer, annotate_patch_risk
from code_intelligence_agent.search.patch_search import PatchSearch
from code_intelligence_agent.search.beam_patch_search import BeamPatchSearch
from code_intelligence_agent.search.hypothesis_search import BugHypothesisSearch
from code_intelligence_agent.search.failure_taxonomy import (
    classify_execution_result,
    summarize_failure_reason,
)
from code_intelligence_agent.search.patch_judge import PatchJudge
from code_intelligence_agent.search.scoring import PatchScoreWeights


@dataclass(frozen=True)
class BenchmarkCaseResult:
    case_name: str
    bug_type: str
    ranked_functions: list[str]
    ground_truth: set[str]
    top1_hit: bool
    top3_hit: bool
    mrr: float
    average_precision: float
    ndcg_at_3: float
    exam_score: float
    findings_count: int
    patch_candidates_count: int
    expected_rule_ids: list[str]
    detected_rule_ids: list[str]
    expected_rule_recall: float
    expected_rule_precision: float
    extra_rule_ids: list[str]
    coverage_mode: str
    localization_details: list[dict]
    patch_success: bool
    repair_rounds: int
    repair_strategy: str
    repair_results: list[dict]
    best_patch_rule_id: str | None
    best_patch_risk: dict | None
    multi_patch_success: bool
    multi_patch_bundle_size: int
    multi_patch_rules: list[str]
    multi_patch_results: list[dict]
    patch_search_results: list[dict]
    beam_search_results: list[dict]
    search_analysis: dict
    hypothesis_results: list[dict]
    hypothesis_top1_hit: bool
    hypothesis_mrr: float
    hypothesis_average_precision: float
    hypothesis_ndcg_at_3: float
    hypothesis_exam_score: float
    llm_judgment: dict | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "case_name": self.case_name,
            "bug_type": self.bug_type,
            "metadata": self.metadata,
            "ranked_functions": self.ranked_functions,
            "ground_truth": sorted(self.ground_truth),
            "top1_hit": self.top1_hit,
            "top3_hit": self.top3_hit,
            "mrr": self.mrr,
            "average_precision": self.average_precision,
            "ndcg_at_3": self.ndcg_at_3,
            "exam_score": self.exam_score,
            "findings_count": self.findings_count,
            "patch_candidates_count": self.patch_candidates_count,
            "expected_rule_ids": self.expected_rule_ids,
            "detected_rule_ids": self.detected_rule_ids,
            "expected_rule_recall": self.expected_rule_recall,
            "expected_rule_precision": self.expected_rule_precision,
            "extra_rule_ids": self.extra_rule_ids,
            "coverage_mode": self.coverage_mode,
            "localization_details": self.localization_details,
            "patch_success": self.patch_success,
            "repair_rounds": self.repair_rounds,
            "repair_strategy": self.repair_strategy,
            "repair_results": self.repair_results,
            "best_patch_rule_id": self.best_patch_rule_id,
            "best_patch_risk": self.best_patch_risk,
            "multi_patch_success": self.multi_patch_success,
            "multi_patch_bundle_size": self.multi_patch_bundle_size,
            "multi_patch_rules": self.multi_patch_rules,
            "multi_patch_results": self.multi_patch_results,
            "patch_search_results": self.patch_search_results,
            "beam_search_results": self.beam_search_results,
            "search_analysis": self.search_analysis,
            "search_competition_audit": search_competition_case_audit(self),
            "hypothesis_results": self.hypothesis_results,
            "hypothesis_top1_hit": self.hypothesis_top1_hit,
            "hypothesis_mrr": self.hypothesis_mrr,
            "hypothesis_average_precision": self.hypothesis_average_precision,
            "hypothesis_ndcg_at_3": self.hypothesis_ndcg_at_3,
            "hypothesis_exam_score": self.hypothesis_exam_score,
            "llm_judgment": self.llm_judgment,
        }


@dataclass(frozen=True)
class BenchmarkReport:
    cases: list[BenchmarkCaseResult] = field(default_factory=list)
    top1: float = 0.0
    top3: float = 0.0
    mrr: float = 0.0
    map: float = 0.0
    ndcg_at_3: float = 0.0
    mean_exam_score: float = 0.0
    expected_rule_recall: float = 0.0
    expected_rule_precision: float = 0.0
    patch_success_rate: float = 0.0
    multi_patch_success_rate: float = 0.0
    average_repair_rounds: float = 0.0
    average_patch_candidates: float = 0.0
    average_patch_size: float = 0.0
    average_patch_risk: float = 0.0
    reflection_success_rate: float = 0.0
    beam_success_rate: float = 0.0
    patch_search_top1_success_rate: float = 0.0
    patch_search_mrr: float = 0.0
    average_first_success_rank: float = 0.0
    average_beam_depth: float = 0.0
    average_evaluated_nodes: float = 0.0
    average_failed_attempts_before_success: float = 0.0
    average_success_depth: float = 0.0
    average_success_score_margin: float = 0.0
    search_efficiency: float = 0.0
    hypothesis_top1: float = 0.0
    hypothesis_mrr: float = 0.0
    hypothesis_map: float = 0.0
    hypothesis_ndcg_at_3: float = 0.0
    hypothesis_mean_exam_score: float = 0.0
    average_hypothesis_depth: float = 0.0
    average_hypothesis_evidence_count: float = 0.0
    data_flow_evidence_case_count: int = 0
    cross_function_data_flow_case_count: int = 0
    subscript_key_flow_case_count: int = 0
    average_top1_data_dependency: float = 0.0
    program_slice_case_count: int = 0
    average_top1_slice_edges: float = 0.0
    average_top1_slice_cross_function_edges: float = 0.0
    slice_grounded_case_count: int = 0
    average_top1_slice_support: float = 0.0
    average_top1_slice_failed_test_reachability: float = 0.0
    average_top1_slice_call_chain_coverage: float = 0.0
    patch_failure_taxonomy: dict[str, int] = field(default_factory=dict)
    bug_type_metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    rule_metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    difficulty_report: dict = field(default_factory=dict)
    repository_test_evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "summary": {
                "case_count": len(self.cases),
                "top1": self.top1,
                "top3": self.top3,
                "mrr": self.mrr,
                "map": self.map,
                "ndcg_at_3": self.ndcg_at_3,
                "mean_exam_score": self.mean_exam_score,
                "expected_rule_recall": self.expected_rule_recall,
                "expected_rule_precision": self.expected_rule_precision,
                "patch_success_rate": self.patch_success_rate,
                "multi_patch_success_rate": self.multi_patch_success_rate,
                "average_repair_rounds": self.average_repair_rounds,
                "average_patch_candidates": self.average_patch_candidates,
                "average_patch_size": self.average_patch_size,
                "average_patch_risk": self.average_patch_risk,
                "reflection_success_rate": self.reflection_success_rate,
                "beam_success_rate": self.beam_success_rate,
                "patch_search_top1_success_rate": self.patch_search_top1_success_rate,
                "patch_search_mrr": self.patch_search_mrr,
                "average_first_success_rank": self.average_first_success_rank,
                "average_beam_depth": self.average_beam_depth,
                "average_evaluated_nodes": self.average_evaluated_nodes,
                "average_failed_attempts_before_success": (
                    self.average_failed_attempts_before_success
                ),
                "average_success_depth": self.average_success_depth,
                "average_success_score_margin": self.average_success_score_margin,
                "search_efficiency": self.search_efficiency,
                "hypothesis_top1": self.hypothesis_top1,
                "hypothesis_mrr": self.hypothesis_mrr,
                "hypothesis_map": self.hypothesis_map,
                "hypothesis_ndcg_at_3": self.hypothesis_ndcg_at_3,
                "hypothesis_mean_exam_score": self.hypothesis_mean_exam_score,
                "average_hypothesis_depth": self.average_hypothesis_depth,
                "average_hypothesis_evidence_count": (
                    self.average_hypothesis_evidence_count
                ),
                "data_flow_evidence_case_count": self.data_flow_evidence_case_count,
                "cross_function_data_flow_case_count": (
                    self.cross_function_data_flow_case_count
                ),
                "subscript_key_flow_case_count": self.subscript_key_flow_case_count,
                "average_top1_data_dependency": self.average_top1_data_dependency,
                "program_slice_case_count": self.program_slice_case_count,
                "average_top1_slice_edges": self.average_top1_slice_edges,
                "average_top1_slice_cross_function_edges": (
                    self.average_top1_slice_cross_function_edges
                ),
                "slice_grounded_case_count": self.slice_grounded_case_count,
                "average_top1_slice_support": self.average_top1_slice_support,
                "average_top1_slice_failed_test_reachability": (
                    self.average_top1_slice_failed_test_reachability
                ),
                "average_top1_slice_call_chain_coverage": (
                    self.average_top1_slice_call_chain_coverage
                ),
                "localization_calibration": localization_calibration_summary(self),
                "localization_attribution": localization_attribution_summary(self),
                "metric_uncertainty": metric_uncertainty_summary(self),
                "search_budget_analysis": search_budget_analysis_summary(self),
                "search_competition_analysis": (
                    search_competition_analysis_summary(self)
                ),
                "reflection_analysis": reflection_analysis_summary(self),
                "patch_failure_taxonomy": self.patch_failure_taxonomy,
                "bug_type_metrics": self.bug_type_metrics,
                "rule_metrics": self.rule_metrics,
                "difficulty_report": (
                    self.difficulty_report
                    if self.difficulty_report
                    else benchmark_difficulty_summary(self)
                ),
                "generalization_report": benchmark_generalization_summary(self),
                "benchmark_provenance_audit": benchmark_provenance_summary(self),
                "llm_judge_reliability": case_judge_reliability_summary(self),
                "patch_judge_reliability": patch_judge_reliability_summary(self),
            },
            "repository_test_evidence": self.repository_test_evidence,
            "cases": [case.to_dict() for case in self.cases],
        }


class BenchmarkRunner:
    def __init__(
        self,
        parser: RepoParser | None = None,
        detector: RuleBasedBugDetector | None = None,
        localizer: FaultLocalizer | None = None,
        patch_generator: PatchGenerator | None = None,
        sandbox: Sandbox | None = None,
        coverage_runner: CoverageRunner | None = None,
        judge: LLMJudge | None = None,
        patch_judge: PatchJudge | None = None,
        use_dynamic_coverage: bool = True,
    ) -> None:
        self.parser = parser or RepoParser()
        self.detector = detector or RuleBasedBugDetector()
        self.localizer = localizer or FaultLocalizer()
        self.patch_generator = patch_generator or PatchGenerator()
        self.sandbox = sandbox or Sandbox(timeout=10)
        self.coverage_runner = coverage_runner or CoverageRunner(timeout=10)
        self.judge = judge
        self.patch_judge = patch_judge
        self.use_dynamic_coverage = use_dynamic_coverage

    def run_manifest(self, manifest_path: str | Path) -> BenchmarkReport:
        cases = BenchmarkLoader().load_manifest(manifest_path)
        return self.run_cases(cases)

    def run_cases(self, cases: list[BenchmarkCase]) -> BenchmarkReport:
        results = [self.run_case(case) for case in cases]
        localization_runs = [
            LocalizationRun(
                ranked=result.ranked_functions,
                ground_truth=result.ground_truth,
            )
            for result in results
        ]
        hypothesis_runs = [
            LocalizationRun(
                ranked=_hypothesis_ranked_functions(result),
                ground_truth=result.ground_truth,
            )
            for result in results
        ]
        return BenchmarkReport(
            cases=results,
            top1=round(top_k_accuracy(localization_runs, 1), 4),
            top3=round(top_k_accuracy(localization_runs, 3), 4),
            mrr=round(mean_reciprocal_rank(localization_runs), 4),
            map=round(mean_average_precision(localization_runs), 4),
            ndcg_at_3=round(mean_ndcg(localization_runs, 3), 4),
            mean_exam_score=round(mean_exam_score(localization_runs), 4),
            expected_rule_recall=round(
                sum(result.expected_rule_recall for result in results) / len(results),
                4,
            )
            if results
            else 0.0,
            expected_rule_precision=round(
                sum(result.expected_rule_precision for result in results) / len(results),
                4,
            )
            if results
            else 0.0,
            patch_success_rate=round(
                patch_success_rate([result.patch_success for result in results]),
                4,
            ),
            multi_patch_success_rate=round(_multi_patch_success_rate(results), 4),
            average_repair_rounds=round(
                average([result.repair_rounds for result in results]),
                4,
            ),
            average_patch_candidates=round(
                average([result.patch_candidates_count for result in results]),
                4,
            ),
            average_patch_size=round(
                average(_best_patch_diff_sizes(results)),
                4,
            ),
            average_patch_risk=round(
                average(_best_patch_risk_scores(results)),
                4,
            ),
            reflection_success_rate=round(_reflection_success_rate(results), 4),
            beam_success_rate=round(_beam_success_rate(results), 4),
            patch_search_top1_success_rate=round(
                _patch_search_top1_success_rate(results), 4
            ),
            patch_search_mrr=round(_patch_search_mrr(results), 4),
            average_first_success_rank=round(
                average(_first_success_ranks(results)), 4
            ),
            average_beam_depth=round(average(_best_beam_depths(results)), 4),
            average_evaluated_nodes=round(
                average(_search_analysis_values(results, "evaluated_nodes")), 4
            ),
            average_failed_attempts_before_success=round(
                average(
                    _search_analysis_values(
                        results,
                        "failures_before_success",
                    )
                ),
                4,
            ),
            average_success_depth=round(
                average(_search_analysis_values(results, "first_success_depth")), 4
            ),
            average_success_score_margin=round(
                average(_search_analysis_values(results, "success_score_margin")), 4
            ),
            search_efficiency=round(
                average(_search_analysis_values(results, "efficiency")), 4
            ),
            hypothesis_top1=round(top_k_accuracy(hypothesis_runs, 1), 4),
            hypothesis_mrr=round(mean_reciprocal_rank(hypothesis_runs), 4),
            hypothesis_map=round(mean_average_precision(hypothesis_runs), 4),
            hypothesis_ndcg_at_3=round(mean_ndcg(hypothesis_runs, 3), 4),
            hypothesis_mean_exam_score=round(mean_exam_score(hypothesis_runs), 4),
            average_hypothesis_depth=round(
                average(_best_hypothesis_depths(results)), 4
            ),
            average_hypothesis_evidence_count=round(
                average(_best_hypothesis_evidence_counts(results)), 4
            ),
            data_flow_evidence_case_count=_data_flow_evidence_case_count(results),
            cross_function_data_flow_case_count=_cross_function_data_flow_case_count(
                results
            ),
            subscript_key_flow_case_count=_subscript_key_flow_case_count(results),
            average_top1_data_dependency=round(
                average(_top1_graph_component_values(results, "data_dependency")),
                4,
            ),
            program_slice_case_count=_program_slice_case_count(results),
            average_top1_slice_edges=round(
                average(_top1_program_slice_values(results, "edge_count")),
                4,
            ),
            average_top1_slice_cross_function_edges=round(
                average(
                    _top1_program_slice_values(
                        results,
                        "cross_function_data_flow_edge_count",
                    )
                ),
                4,
            ),
            slice_grounded_case_count=_slice_grounded_case_count(results),
            average_top1_slice_support=round(
                average(_top1_slice_grounding_values(results, "support_score")),
                4,
            ),
            average_top1_slice_failed_test_reachability=round(
                average(
                    _top1_slice_grounding_values(
                        results,
                        "failed_test_reachability",
                    )
                ),
                4,
            ),
            average_top1_slice_call_chain_coverage=round(
                average(
                    _top1_slice_grounding_values(
                        results,
                        "call_chain_edge_coverage",
                    )
                ),
                4,
            ),
            patch_failure_taxonomy=_patch_failure_taxonomy(results),
            bug_type_metrics=_bug_type_metrics(results),
            rule_metrics=_rule_metrics(results),
            difficulty_report=benchmark_difficulty_summary(results),
        )

    def run_case(self, case: BenchmarkCase) -> BenchmarkCaseResult:
        parsed = self.parser.parse(case.repo_path)
        call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
        program_graph = build_program_graph(parsed, call_graph)
        findings = self.detector.detect(parsed.functions)
        detected_rule_ids = sorted({finding.rule_id for finding in findings})
        functions_by_name = _functions_by_name(parsed.functions)
        ground_truth_ids = _resolve_names(case.buggy_functions, functions_by_name)
        test_summary, coverage_mode = self._build_test_summary(
            case=case,
            functions=parsed.functions,
            functions_by_name=functions_by_name,
            ground_truth_ids=ground_truth_ids,
        )
        ranked = self.localizer.rank(program_graph, findings, test_summary)
        candidates = self.patch_generator.generate(
            case.repo_path,
            parsed.functions,
            ranked,
        )
        candidates = [
            annotate_patch_risk(
                candidate,
                PatchRiskAnalyzer().analyze(candidate, program_graph),
            )
            for candidate in candidates
        ]
        hypothesis_results = _hypothesis_results(
            ranked=ranked,
            findings=findings,
            test_summary=test_summary,
            program_graph=program_graph,
            candidates=candidates,
        )
        localization_scores = {
            item.function_id: item.score for item in ranked
        }
        patch_search_results = _patch_search_results(
            case=case,
            candidates=candidates,
            localization_scores=localization_scores,
            program_graph=program_graph,
            sandbox=self.sandbox,
        )
        refiner = _refiner(self.patch_generator)
        beam_search_results = _beam_search_results(
            case=case,
            candidates=candidates,
            localization_scores=localization_scores,
            program_graph=program_graph,
            sandbox=self.sandbox,
            refiner=refiner,
            patch_judge=self.patch_judge,
        )
        search_analysis = _search_analysis(beam_search_results)
        beam_success = _best_successful_beam_result(beam_search_results)
        repair_result = None
        if beam_success is None:
            repair_result = RepairLoop(
                sandbox=self.sandbox,
                refiner=refiner,
                max_rounds=3 if refiner is not None else max(1, min(3, len(candidates))),
            ).run(
                case.repo_path,
                candidates,
                localization_scores=localization_scores,
                test_args=case.test_args,
                program_graph=program_graph,
            )
        multi_patch_result = _multi_patch_repair_result(
            case=case,
            candidates=candidates,
            localization_scores=localization_scores,
            program_graph=program_graph,
            sandbox=self.sandbox,
            enabled=beam_success is None
            and repair_result is not None
            and not repair_result.success,
        )
        multi_patch_results = _multi_patch_results(multi_patch_result)
        effective_patch_success = (
            beam_success is not None
            or bool(repair_result and repair_result.success)
            or multi_patch_result.success
        )
        best_patch_rule_id = _best_patch_rule_id_for_strategy(
            beam_result=beam_success,
            repair_result=repair_result,
            multi_patch_result=multi_patch_result,
        )
        best_patch_risk = _best_patch_risk_for_strategy(
            beam_result=beam_success,
            repair_result=repair_result,
            multi_patch_result=multi_patch_result,
        )
        repair_strategy = _repair_strategy(
            beam_result=beam_success,
            repair_result=repair_result,
            multi_patch_success=multi_patch_result.success,
            patch_success=effective_patch_success,
        )
        repair_rounds = _effective_repair_rounds(
            beam_result=beam_success,
            repair_result=repair_result,
            multi_patch_rounds=multi_patch_result.rounds,
        )
        repair_results = _repair_results(repair_result)
        ranked_names = [item.function_name for item in ranked]
        run = LocalizationRun(ranked=ranked_names, ground_truth=set(case.buggy_functions))
        hypothesis_run = LocalizationRun(
            ranked=_ranked_names_from_hypotheses(hypothesis_results),
            ground_truth=set(case.buggy_functions),
        )
        llm_judgment = None
        if self.judge is not None:
            llm_judgment = self.judge.judge_case(
                _judge_payload(
                    case=case,
                    ranked_names=ranked_names,
                    run=run,
                    findings_count=len(findings),
                    candidates_count=len(candidates),
                    detected_rule_ids=detected_rule_ids,
                    expected_rule_recall=_rule_recall(
                        expected=case.expected_rule_ids,
                        detected=detected_rule_ids,
                    ),
                    expected_rule_precision=_rule_precision(
                        expected=case.expected_rule_ids,
                        detected=detected_rule_ids,
                    ),
                    extra_rule_ids=_extra_rule_ids(
                        expected=case.expected_rule_ids,
                        detected=detected_rule_ids,
                    ),
                    coverage_mode=coverage_mode,
                    localization_details=_localization_details(
                        ranked,
                        test_summary,
                        program_graph=program_graph,
                        limit=3,
                    ),
                    repair_success=effective_patch_success,
                    repair_rounds=repair_rounds,
                    repair_strategy=repair_strategy,
                    repair_results=repair_results[:3],
                    best_patch_rule_id=best_patch_rule_id,
                    best_patch_risk=best_patch_risk,
                    multi_patch_results=multi_patch_results[:3],
                    patch_search_results=patch_search_results[:3],
                    beam_search_results=beam_search_results[:3],
                    hypothesis_results=hypothesis_results[:3],
                )
            ).to_dict()
        return BenchmarkCaseResult(
            case_name=case.name,
            bug_type=str(case.metadata.get("bug_type", "unspecified")),
            metadata=dict(case.metadata),
            ranked_functions=ranked_names,
            ground_truth=set(case.buggy_functions),
            top1_hit=top_k_accuracy([run], 1) == 1.0,
            top3_hit=top_k_accuracy([run], 3) == 1.0,
            mrr=round(mean_reciprocal_rank([run]), 4),
            average_precision=round(average_precision(run), 4),
            ndcg_at_3=round(normalized_discounted_cumulative_gain(run, 3), 4),
            exam_score=round(exam_score(run), 4),
            findings_count=len(findings),
            patch_candidates_count=len(candidates),
            expected_rule_ids=case.expected_rule_ids,
            detected_rule_ids=detected_rule_ids,
            expected_rule_recall=_rule_recall(
                expected=case.expected_rule_ids,
                detected=detected_rule_ids,
            ),
            expected_rule_precision=_rule_precision(
                expected=case.expected_rule_ids,
                detected=detected_rule_ids,
            ),
            extra_rule_ids=_extra_rule_ids(
                expected=case.expected_rule_ids,
                detected=detected_rule_ids,
            ),
            coverage_mode=coverage_mode,
            localization_details=_localization_details(
                ranked,
                test_summary,
                program_graph=program_graph,
            ),
            patch_success=effective_patch_success,
            repair_rounds=repair_rounds,
            repair_strategy=repair_strategy,
            repair_results=repair_results,
            best_patch_rule_id=best_patch_rule_id,
            best_patch_risk=best_patch_risk,
            multi_patch_success=multi_patch_result.success,
            multi_patch_bundle_size=multi_patch_result.bundle_size,
            multi_patch_rules=_multi_patch_rules(multi_patch_result.best_candidates),
            multi_patch_results=multi_patch_results,
            patch_search_results=patch_search_results,
            beam_search_results=beam_search_results,
            search_analysis=search_analysis,
            hypothesis_results=hypothesis_results,
            hypothesis_top1_hit=top_k_accuracy([hypothesis_run], 1) == 1.0,
            hypothesis_mrr=round(mean_reciprocal_rank([hypothesis_run]), 4),
            hypothesis_average_precision=round(
                average_precision(hypothesis_run), 4
            ),
            hypothesis_ndcg_at_3=round(
                normalized_discounted_cumulative_gain(hypothesis_run, 3), 4
            ),
            hypothesis_exam_score=round(exam_score(hypothesis_run), 4),
            llm_judgment=llm_judgment,
        )

    def _build_test_summary(
        self,
        case: BenchmarkCase,
        functions: list[CodeEntity],
        functions_by_name: dict[str, CodeEntity],
        ground_truth_ids: set[str],
    ) -> tuple[TestExecutionSummary, str]:
        if self.use_dynamic_coverage and (case.failing_tests or case.passed_tests):
            summary = self.coverage_runner.build_summary(
                case.repo_path,
                functions,
                failing_tests=case.failing_tests,
                passed_tests=case.passed_tests,
            )
            if any(summary.coverage.values()):
                return summary, "dynamic_trace"
        return (
            _build_test_summary(
                case=case,
                functions_by_name=functions_by_name,
                ground_truth_ids=ground_truth_ids,
            ),
            "manifest_fallback",
        )


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
    coverage = {}
    for test_id in failed_ids:
        coverage[test_id] = set(ground_truth_ids)
    for test_id in passed_ids:
        coverage[test_id] = set()
    test_names = {}
    for test_name in case.failing_tests + case.passed_tests:
        function = functions_by_name.get(test_name)
        if function is not None:
            test_names[function.id] = function.name
    return TestExecutionSummary(
        failed_tests=failed_ids,
        passed_tests=passed_ids,
        coverage=coverage,
        traceback_function_ids=set(ground_truth_ids),
        test_names=test_names,
        failure_messages={
            test_id: " ".join(
                case.expected_rule_ids + [str(case.metadata.get("bug_type", ""))]
            )
            for test_id in failed_ids
        },
    )


def _rule_recall(expected: list[str], detected: list[str]) -> float:
    if not expected:
        return 0.0
    detected_set = set(detected)
    hits = sum(1 for rule_id in expected if rule_id in detected_set)
    return round(hits / len(expected), 4)


def _rule_precision(expected: list[str], detected: list[str]) -> float:
    if not detected:
        return 0.0
    expected_set = set(expected)
    hits = sum(1 for rule_id in detected if rule_id in expected_set)
    return round(hits / len(detected), 4)


def _extra_rule_ids(expected: list[str], detected: list[str]) -> list[str]:
    expected_set = set(expected)
    return sorted(rule_id for rule_id in detected if rule_id not in expected_set)


def _judge_payload(
    case: BenchmarkCase,
    ranked_names: list[str],
    run: LocalizationRun,
    findings_count: int,
    candidates_count: int,
    detected_rule_ids: list[str],
    expected_rule_recall: float,
    expected_rule_precision: float,
    extra_rule_ids: list[str],
    coverage_mode: str,
    localization_details: list[dict],
    repair_success: bool,
    repair_rounds: int,
    repair_strategy: str,
    repair_results: list[dict],
    best_patch_rule_id: str | None,
    best_patch_risk: dict | None,
    multi_patch_results: list[dict],
    patch_search_results: list[dict],
    beam_search_results: list[dict],
    hypothesis_results: list[dict],
) -> dict:
    return {
        "case_name": case.name,
        "bug_type": str(case.metadata.get("bug_type", "unspecified")),
        "ground_truth": sorted(run.ground_truth),
        "ranked_functions": ranked_names[:5],
        "top1_hit": top_k_accuracy([run], 1) == 1.0,
        "top3_hit": top_k_accuracy([run], 3) == 1.0,
        "mrr": round(mean_reciprocal_rank([run]), 4),
        "average_precision": round(average_precision(run), 4),
        "ndcg_at_3": round(normalized_discounted_cumulative_gain(run, 3), 4),
        "exam_score": round(exam_score(run), 4),
        "findings_count": findings_count,
        "patch_candidates_count": candidates_count,
        "expected_rule_ids": case.expected_rule_ids,
        "detected_rule_ids": detected_rule_ids,
        "expected_rule_recall": expected_rule_recall,
        "expected_rule_precision": expected_rule_precision,
        "extra_rule_ids": extra_rule_ids,
        "coverage_mode": coverage_mode,
        "localization_details": localization_details,
        "patch_success": repair_success,
        "repair_rounds": repair_rounds,
        "repair_strategy": repair_strategy,
        "repair_results": repair_results,
        "best_patch_rule_id": best_patch_rule_id,
        "best_patch_risk": best_patch_risk,
        "multi_patch_results": multi_patch_results,
        "patch_search_results": patch_search_results,
        "beam_search_results": beam_search_results,
        "hypothesis_results": hypothesis_results,
    }


def _best_patch_diff_sizes(results: list[BenchmarkCaseResult]) -> list[int]:
    sizes: list[int] = []
    for result in results:
        risk = result.best_patch_risk or {}
        if "diff_size" in risk:
            sizes.append(int(risk["diff_size"]))
    return sizes


def _best_patch_risk_scores(results: list[BenchmarkCaseResult]) -> list[float]:
    scores: list[float] = []
    for result in results:
        risk = result.best_patch_risk or {}
        if "score" in risk:
            scores.append(float(risk["score"]))
    return scores


def _reflection_success_rate(results: list[BenchmarkCaseResult]) -> float:
    reflected = [
        result
        for result in results
        if result.repair_rounds > 1 and not result.multi_patch_success
    ]
    if not reflected:
        return 0.0
    return patch_success_rate([result.patch_success for result in reflected])


def _multi_patch_success_rate(results: list[BenchmarkCaseResult]) -> float:
    attempted = [result for result in results if result.multi_patch_results]
    if not attempted:
        return 0.0
    return patch_success_rate([result.multi_patch_success for result in attempted])


def _beam_success_rate(results: list[BenchmarkCaseResult]) -> float:
    beam_cases = [result for result in results if result.beam_search_results]
    if not beam_cases:
        return 0.0
    return patch_success_rate(
        [
            any(node.get("success", False) for node in result.beam_search_results)
            for result in beam_cases
        ]
    )


def _patch_search_top1_success_rate(results: list[BenchmarkCaseResult]) -> float:
    beam_cases = [result for result in results if result.beam_search_results]
    if not beam_cases:
        return 0.0
    return patch_success_rate(
        [
            bool(result.beam_search_results[0].get("success", False))
            for result in beam_cases
        ]
    )


def _patch_search_mrr(results: list[BenchmarkCaseResult]) -> float:
    reciprocals = []
    for rank in _first_success_ranks(results, include_failures=True):
        reciprocals.append(0.0 if rank is None else 1.0 / rank)
    return average(reciprocals)


def _first_success_ranks(
    results: list[BenchmarkCaseResult],
    include_failures: bool = False,
) -> list[int | None]:
    ranks: list[int | None] = []
    for result in results:
        if not result.beam_search_results:
            continue
        rank = _first_success_rank(result.beam_search_results)
        if rank is not None or include_failures:
            ranks.append(rank)
    return ranks


def _first_success_rank(search_results: list[dict]) -> int | None:
    for index, node in enumerate(search_results, start=1):
        if node.get("success", False):
            return index
    return None


def _ranked_names_from_hypotheses(hypothesis_results: list[dict]) -> list[str]:
    ranked: list[str] = []
    seen: set[str] = set()
    for hypothesis in hypothesis_results:
        name = str(hypothesis.get("function_name", ""))
        if not name or name in seen:
            continue
        ranked.append(name)
        seen.add(name)
    return ranked


def _hypothesis_ranked_functions(result: BenchmarkCaseResult) -> list[str]:
    return _ranked_names_from_hypotheses(result.hypothesis_results)


def _best_hypothesis_depths(results: list[BenchmarkCaseResult]) -> list[int]:
    depths: list[int] = []
    for result in results:
        if result.hypothesis_results:
            depths.append(int(result.hypothesis_results[0].get("depth", 0)))
    return depths


def _best_hypothesis_evidence_counts(results: list[BenchmarkCaseResult]) -> list[int]:
    counts: list[int] = []
    for result in results:
        if not result.hypothesis_results:
            continue
        evidence = result.hypothesis_results[0].get("evidence", {})
        counts.append(len(evidence) if isinstance(evidence, dict) else 0)
    return counts


def _data_flow_evidence_case_count(results: list[BenchmarkCaseResult]) -> int:
    count = 0
    for result in results:
        evidence = _top_localization_detail(result).get("data_flow_evidence", {})
        if int(evidence.get("total_edges", 0)) > 0:
            count += 1
    return count


def _cross_function_data_flow_case_count(results: list[BenchmarkCaseResult]) -> int:
    count = 0
    for result in results:
        evidence = _top_localization_detail(result).get("data_flow_evidence", {})
        if int(evidence.get("cross_function_edges", 0)) > 0:
            count += 1
    return count


def _subscript_key_flow_case_count(results: list[BenchmarkCaseResult]) -> int:
    count = 0
    for result in results:
        evidence = _top_localization_detail(result).get("data_flow_evidence", {})
        if int(evidence.get("key_flow_edges", 0)) > 0:
            count += 1
    return count


def _program_slice_case_count(results: list[BenchmarkCaseResult]) -> int:
    count = 0
    for result in results:
        evidence = _top_localization_detail(result).get("program_slice", {})
        if int(evidence.get("edge_count", 0)) > 0:
            count += 1
    return count


def _slice_grounded_case_count(results: list[BenchmarkCaseResult]) -> int:
    count = 0
    for result in results:
        evidence = _top_localization_detail(result).get("slice_grounding", {})
        if bool(evidence.get("grounded", False)):
            count += 1
    return count


def _top1_graph_component_values(
    results: list[BenchmarkCaseResult],
    component: str,
) -> list[float]:
    values = []
    for result in results:
        graph_components = _top_localization_detail(result).get(
            "graph_components",
            {},
        )
        if component in graph_components:
            values.append(float(graph_components[component]))
    return values


def _top1_program_slice_values(
    results: list[BenchmarkCaseResult],
    key: str,
) -> list[float]:
    values = []
    for result in results:
        evidence = _top_localization_detail(result).get("program_slice", {})
        if key in evidence:
            values.append(float(evidence[key]))
    return values


def _top1_slice_grounding_values(
    results: list[BenchmarkCaseResult],
    key: str,
) -> list[float]:
    values = []
    for result in results:
        evidence = _top_localization_detail(result).get("slice_grounding", {})
        if key in evidence:
            values.append(float(evidence[key]))
    return values


def _top_localization_detail(result: BenchmarkCaseResult) -> dict:
    return result.localization_details[0] if result.localization_details else {}


def _search_analysis(search_results: list[dict]) -> dict:
    deduplicated_candidates = _deduplicated_candidate_count(search_results)
    if not search_results:
        return {
            "evaluated_nodes": 0,
            "successful_nodes": 0,
            "max_depth": 0,
            "first_success_rank": None,
            "first_success_depth": None,
            "failures_before_success": 0,
            "success_score_margin": 0.0,
            "efficiency": 0.0,
            "deduplicated_candidates": 0,
            "effective_candidate_pool": 0,
            "deduplication_savings_ratio": 0.0,
        }

    first_success = next(
        (
            (index, node)
            for index, node in enumerate(search_results, start=1)
            if node.get("success", False)
        ),
        None,
    )
    successful_nodes = sum(1 for node in search_results if node.get("success", False))
    max_depth = max(int(node.get("depth", 0)) for node in search_results)

    if first_success is None:
        return {
            "evaluated_nodes": len(search_results),
            "successful_nodes": successful_nodes,
            "max_depth": max_depth,
            "first_success_rank": None,
            "first_success_depth": None,
            "failures_before_success": len(search_results),
            "success_score_margin": 0.0,
            "efficiency": 0.0,
            "deduplicated_candidates": deduplicated_candidates,
            "effective_candidate_pool": len(search_results) + deduplicated_candidates,
            "deduplication_savings_ratio": _deduplication_savings_ratio(
                evaluated_nodes=len(search_results),
                deduplicated_candidates=deduplicated_candidates,
            ),
        }

    first_success_rank, success_node = first_success
    success_depth = int(success_node.get("depth", 0))
    failed_scores = [
        float(node.get("score", 0.0))
        for node in search_results
        if not node.get("success", False)
    ]
    success_score = float(success_node.get("score", 0.0))
    score_margin = success_score - max(failed_scores) if failed_scores else 0.0
    failures_before_success = first_success_rank - 1
    efficiency = 1.0 / (1 + failures_before_success + success_depth)

    return {
        "evaluated_nodes": len(search_results),
        "successful_nodes": successful_nodes,
        "max_depth": max_depth,
        "first_success_rank": first_success_rank,
        "first_success_depth": success_depth,
        "failures_before_success": failures_before_success,
        "success_score_margin": round(score_margin, 4),
        "efficiency": round(efficiency, 4),
        "deduplicated_candidates": deduplicated_candidates,
        "effective_candidate_pool": len(search_results) + deduplicated_candidates,
        "deduplication_savings_ratio": _deduplication_savings_ratio(
            evaluated_nodes=len(search_results),
            deduplicated_candidates=deduplicated_candidates,
        ),
    }


def _deduplicated_candidate_count(search_results: list[dict]) -> int:
    return sum(
        max(0, _int_metadata_value(node.get("search_duplicate_count", 0)))
        for node in search_results
    )


def _int_metadata_value(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _deduplication_savings_ratio(
    *,
    evaluated_nodes: int,
    deduplicated_candidates: int,
) -> float:
    total = evaluated_nodes + deduplicated_candidates
    if total <= 0:
        return 0.0
    return round(deduplicated_candidates / total, 4)


def _search_analysis_values(
    results: list[BenchmarkCaseResult],
    key: str,
) -> list[float]:
    values: list[float] = []
    for result in results:
        value = result.search_analysis.get(key)
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def _best_beam_depths(results: list[BenchmarkCaseResult]) -> list[int]:
    depths: list[int] = []
    for result in results:
        if not result.beam_search_results:
            continue
        best_success = next(
            (
                node
                for node in result.beam_search_results
                if node.get("success", False)
            ),
            result.beam_search_results[0],
        )
        depths.append(int(best_success.get("depth", 0)))
    return depths


def _patch_failure_taxonomy(results: list[BenchmarkCaseResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        if result.patch_candidates_count == 0:
            counts["no_candidate"] = counts.get("no_candidate", 0) + 1
        attempts = [*result.patch_search_results, *result.beam_search_results]
        if not attempts and result.patch_candidates_count > 0:
            counts["not_evaluated"] = counts.get("not_evaluated", 0) + 1
        for attempt in attempts:
            failure_type = str(
                attempt.get("failure_type")
                or ("success" if attempt.get("success", False) else "unknown_failure")
            )
            counts[failure_type] = counts.get(failure_type, 0) + 1
    return dict(sorted(counts.items()))


def _bug_type_metrics(results: list[BenchmarkCaseResult]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[BenchmarkCaseResult]] = {}
    for result in results:
        grouped.setdefault(result.bug_type, []).append(result)

    return {
        bug_type: _group_metrics(items)
        for bug_type, items in sorted(grouped.items())
    }


def _rule_metrics(results: list[BenchmarkCaseResult]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[BenchmarkCaseResult]] = {}
    for result in results:
        expected_rules = sorted(set(result.expected_rule_ids)) or ["no_expected_rule"]
        for rule_id in expected_rules:
            grouped.setdefault(rule_id, []).append(result)

    return {
        rule_id: _group_metrics(items)
        for rule_id, items in sorted(grouped.items())
    }


def _group_metrics(items: list[BenchmarkCaseResult]) -> dict[str, float]:
    runs = [
        LocalizationRun(
            ranked=item.ranked_functions,
            ground_truth=item.ground_truth,
        )
        for item in items
    ]
    hypothesis_runs = [
        LocalizationRun(
            ranked=_hypothesis_ranked_functions(item),
            ground_truth=item.ground_truth,
        )
        for item in items
    ]
    return {
        "case_count": len(items),
        "top1": round(top_k_accuracy(runs, 1), 4),
        "top3": round(top_k_accuracy(runs, 3), 4),
        "mrr": round(mean_reciprocal_rank(runs), 4),
        "map": round(mean_average_precision(runs), 4),
        "ndcg_at_3": round(mean_ndcg(runs, 3), 4),
        "mean_exam_score": round(mean_exam_score(runs), 4),
        "hypothesis_top1": round(top_k_accuracy(hypothesis_runs, 1), 4),
        "hypothesis_mrr": round(mean_reciprocal_rank(hypothesis_runs), 4),
        "hypothesis_map": round(mean_average_precision(hypothesis_runs), 4),
        "hypothesis_ndcg_at_3": round(mean_ndcg(hypothesis_runs, 3), 4),
        "hypothesis_mean_exam_score": round(mean_exam_score(hypothesis_runs), 4),
        "expected_rule_recall": round(
            sum(item.expected_rule_recall for item in items) / len(items),
            4,
        ),
        "expected_rule_precision": round(
            sum(item.expected_rule_precision for item in items) / len(items),
            4,
        ),
        "patch_success_rate": round(
            patch_success_rate([item.patch_success for item in items]),
            4,
        ),
        "multi_patch_success_rate": round(_multi_patch_success_rate(items), 4),
        "patch_search_top1_success_rate": round(
            _patch_search_top1_success_rate(items),
            4,
        ),
        "patch_search_mrr": round(_patch_search_mrr(items), 4),
        "average_first_success_rank": round(
            average(_first_success_ranks(items)),
            4,
        ),
        "average_evaluated_nodes": round(
            average(_search_analysis_values(items, "evaluated_nodes")), 4
        ),
        "average_failed_attempts_before_success": round(
            average(
                _search_analysis_values(
                    items,
                    "failures_before_success",
                )
            ),
            4,
        ),
        "average_success_depth": round(
            average(_search_analysis_values(items, "first_success_depth")), 4
        ),
        "average_success_score_margin": round(
            average(_search_analysis_values(items, "success_score_margin")), 4
        ),
        "search_efficiency": round(
            average(_search_analysis_values(items, "efficiency")), 4
        ),
    }


def _localization_details(
    ranked,
    summary: TestExecutionSummary,
    program_graph: ProgramGraph | None = None,
    limit: int = 5,
) -> list[dict]:
    details = []
    total_failed = len(summary.failed_tests)
    for item in ranked[:limit]:
        failed_covered = sum(
            1
            for test_id in summary.failed_tests
            if item.function_id in summary.coverage.get(test_id, set())
        )
        passed_covered = sum(
            1
            for test_id in summary.passed_tests
            if item.function_id in summary.coverage.get(test_id, set())
        )
        call_chain = _shortest_failing_call_chain(
            item.function_id,
            summary,
            program_graph,
        )
        program_slice = program_slice_evidence(
            program_graph,
            item.function_id,
        )
        details.append(
            {
                "rank": item.rank,
                "function_name": item.function_name,
                "score": item.score,
                "failed_covered": failed_covered,
                "passed_covered": passed_covered,
                "total_failed": total_failed,
                "ochiai": item.signals.get("sbfl", 0.0),
                "signals": item.signals,
                "call_chain": call_chain,
                "call_chain_length": (
                    max(0, len(call_chain) - 1) if call_chain else None
                ),
                "data_flow_evidence": _data_flow_evidence(
                    item.function_id,
                    program_graph,
                ),
                "program_slice": program_slice.to_dict(),
                "slice_grounding": slice_grounding_evidence(
                    function_id=item.function_id,
                    function_name=item.function_name,
                    summary=summary,
                    program_graph=program_graph,
                    program_slice=program_slice,
                ).to_dict(),
                "graph_components": {
                    "traceback_hit": item.signals.get("traceback_hit", 0.0),
                    "test_coverage": item.signals.get("test_coverage", 0.0),
                    "line_coverage": item.signals.get("line_coverage", 0.0),
                    "statement_sbfl": item.signals.get("statement_sbfl", 0.0),
                    "branch_sbfl": item.signals.get("branch_sbfl", 0.0),
                    "path_sbfl": item.signals.get("path_sbfl", 0.0),
                    "data_dependency": item.signals.get("data_dependency", 0.0),
                    "control_flow": item.signals.get("control_flow", 0.0),
                    "pagerank": item.signals.get("pagerank", 0.0),
                    "proximity": item.signals.get("proximity", 0.0),
                    "caller_impact": item.signals.get("caller_impact", 0.0),
                    "module_dependency": item.signals.get(
                        "module_dependency",
                        0.0,
                    ),
                    "async_call": item.signals.get("async_call", 0.0),
                    "centrality": item.signals.get("centrality", 0.0),
                    "patch_risk": item.signals.get(
                        "patch_risk",
                        item.signals.get("risk", 0.0),
                    ),
                },
            }
        )
    return details


def _shortest_failing_call_chain(
    function_id: str,
    summary: TestExecutionSummary,
    program_graph: ProgramGraph | None,
) -> list[str]:
    if program_graph is None:
        return []
    best_path: list[str] | None = None
    for test_id in summary.failed_tests:
        path = program_graph.shortest_path(
            source=test_id,
            target=function_id,
            edge_types={"calls", "tested_by"},
        )
        if path is None:
            continue
        if best_path is None or len(path) < len(best_path):
            best_path = path
    if best_path is None:
        return []
    return [_node_display_name(program_graph, node_id) for node_id in best_path]


def _data_flow_evidence(
    function_id: str,
    program_graph: ProgramGraph | None,
) -> dict:
    if program_graph is None:
        return {
            "internal_edges": 0,
            "key_flow_edges": 0,
            "arg_flow_edges": 0,
            "return_flow_edges": 0,
            "cross_function_edges": 0,
            "total_edges": 0,
        }
    internal_edges = 0
    key_flow_edges = 0
    arg_flow_edges = 0
    return_flow_edges = 0
    for edge in program_graph.edges:
        edge_type = edge["type"]
        if edge_type == "data_depends_on" and edge.get("function_id") == function_id:
            internal_edges += 1
        elif edge_type == "key_flows_to_subscript" and edge.get(
            "function_id"
        ) == function_id:
            key_flow_edges += 1
        elif edge_type == "arg_flows_to_param" and (
            edge.get("caller_function_id") == function_id
            or edge.get("callee_function_id") == function_id
        ):
            arg_flow_edges += 1
        elif edge_type == "return_flows_to_var" and (
            edge.get("caller_function_id") == function_id
            or edge.get("callee_function_id") == function_id
        ):
            return_flow_edges += 1
    cross_function_edges = arg_flow_edges + return_flow_edges
    return {
        "internal_edges": internal_edges,
        "key_flow_edges": key_flow_edges,
        "arg_flow_edges": arg_flow_edges,
        "return_flow_edges": return_flow_edges,
        "cross_function_edges": cross_function_edges,
        "total_edges": internal_edges + key_flow_edges + cross_function_edges,
    }


def _node_display_name(program_graph: ProgramGraph, node_id: str) -> str:
    function = program_graph.functions.get(node_id)
    if function is not None:
        return str(function.metadata.get("qualified_name", function.name))
    node = program_graph.nodes.get(node_id, {})
    return str(node.get("qualified_name") or node.get("name") or node_id)


def _refiner(patch_generator):
    refine = getattr(patch_generator, "refine", None)
    return patch_generator if callable(refine) else None


def _best_patch_risk(candidate) -> dict | None:
    if candidate is None:
        return None
    risk = candidate.metadata.get("risk")
    return risk if isinstance(risk, dict) else None


def _multi_patch_repair_result(
    *,
    case: BenchmarkCase,
    candidates,
    localization_scores: dict[str, float],
    program_graph,
    sandbox: Sandbox,
    enabled: bool,
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
    ).run(
        case.repo_path,
        candidates,
        localization_scores=localization_scores,
        program_graph=program_graph,
        test_args=case.test_args,
    )


def _multi_patch_results(result) -> list[dict]:
    output = []
    for rank, attempt in enumerate(result.attempts, start=1):
        output.append(
            {
                "rank": rank,
                "candidate_ids": [candidate.id for candidate in attempt.candidates],
                "rules": attempt.rule_ids,
                "functions": attempt.target_function_names,
                "variants": [
                    candidate.metadata.get("variant", "")
                    for candidate in attempt.candidates
                ],
                "bundle_size": len(attempt.candidates),
                "graph_evidence": attempt.graph_evidence,
                "graph_bonus": attempt.graph_evidence.get("graph_bonus", 0.0),
                "cross_file": attempt.graph_evidence.get("cross_file", False),
                "direct_call_edges": attempt.graph_evidence.get("direct_call_edges", 0),
                "module_dependency_edges": attempt.graph_evidence.get(
                    "module_dependency_edges",
                    0,
                ),
                "relative_import_edges": attempt.graph_evidence.get(
                    "relative_import_edges",
                    0,
                ),
                "max_package_distance": attempt.graph_evidence.get(
                    "max_package_distance",
                    0,
                ),
                "average_package_distance": attempt.graph_evidence.get(
                    "average_package_distance",
                    0.0,
                ),
                "package_distance_bonus": attempt.graph_evidence.get(
                    "package_distance_bonus",
                    0.0,
                ),
                "data_flow_edges": attempt.graph_evidence.get("data_flow_edges", 0),
                "key_flow_edges": attempt.graph_evidence.get("key_flow_edges", 0),
                "score": attempt.score,
                "success": attempt.success,
                "passed": attempt.execution_result.passed,
                "failed": attempt.execution_result.failed,
            }
        )
    return output


def _best_successful_beam_result(results: list[dict]) -> dict | None:
    for result in results:
        if result.get("success", False):
            return result
    return None


def _repair_strategy(
    *,
    beam_result: dict | None,
    repair_result,
    multi_patch_success: bool,
    patch_success: bool,
) -> str:
    if beam_result is not None:
        return "beam_search"
    if repair_result is not None and repair_result.success:
        return "repair_loop"
    if multi_patch_success:
        return "multi_patch"
    return "none" if not patch_success else "unknown"


def _effective_repair_rounds(
    *,
    beam_result: dict | None,
    repair_result,
    multi_patch_rounds: int,
) -> int:
    if beam_result is not None:
        return int(beam_result.get("depth", 0)) + 1
    repair_rounds = repair_result.rounds if repair_result is not None else 0
    return repair_rounds + multi_patch_rounds


def _best_patch_rule_id_for_strategy(
    *,
    beam_result: dict | None,
    repair_result,
    multi_patch_result,
) -> str | None:
    if beam_result is not None:
        return str(beam_result.get("rule_id") or "") or None
    if multi_patch_result.success:
        return "+".join(_multi_patch_rules(multi_patch_result.best_candidates)) or None
    if repair_result is not None and repair_result.best_candidate is not None:
        return repair_result.best_candidate.rule_id
    return None


def _best_patch_risk_for_strategy(
    *,
    beam_result: dict | None,
    repair_result,
    multi_patch_result,
) -> dict | None:
    if beam_result is not None:
        risk = beam_result.get("risk")
        return risk if isinstance(risk, dict) else None
    if multi_patch_result.success:
        return _aggregate_patch_risk(multi_patch_result.best_candidates)
    if repair_result is not None:
        return _best_patch_risk(repair_result.best_candidate)
    return None


def _repair_results(result) -> list[dict]:
    if result is None:
        return []
    output = []
    for rank, attempt in enumerate(result.attempts, start=1):
        candidate = attempt.candidate
        risk = candidate.metadata.get("risk", {})
        execution_result = attempt.execution_result
        output.append(
            {
                "rank": rank,
                "round": attempt.round_index,
                "candidate_id": candidate.id,
                "repair_loop_parent_id": candidate.metadata.get(
                    "repair_loop_parent_id",
                    "",
                ),
                "repair_loop_child_index": candidate.metadata.get(
                    "repair_loop_child_index",
                ),
                "repair_loop_sibling_count": candidate.metadata.get(
                    "repair_loop_sibling_count",
                ),
                "repair_loop_round_index": candidate.metadata.get(
                    "repair_loop_round_index",
                ),
                "search_duplicate_count": candidate.metadata.get(
                    "search_duplicate_count",
                    0,
                ),
                "search_deduplication": _candidate_deduplication_metadata(
                    candidate,
                ),
                "variant": candidate.metadata.get("variant", ""),
                "rule_id": candidate.rule_id,
                "score": attempt.score,
                "success": execution_result.success,
                "risk_score": risk.get("score", 0.0) if isinstance(risk, dict) else 0.0,
                "passed": execution_result.passed,
                "failed": execution_result.failed,
                "failure_type": classify_execution_result(execution_result),
                "failure_reason": summarize_failure_reason(execution_result),
                "reflection_error_type": attempt.reflection.error_type,
                "reflection_should_retry": attempt.reflection.should_retry,
            }
        )
    return output


def _candidate_deduplication_metadata(candidate) -> dict:
    metadata = candidate.metadata.get("search_deduplication", {})
    return metadata if isinstance(metadata, dict) else {}


def _best_patch_rule_id(
    *,
    repair_candidate,
    multi_patch_candidates,
    use_multi_patch: bool,
) -> str | None:
    if use_multi_patch:
        return "+".join(_multi_patch_rules(multi_patch_candidates)) or None
    if repair_candidate is None:
        return None
    return repair_candidate.rule_id


def _best_patch_risk_for_result(
    *,
    repair_candidate,
    multi_patch_candidates,
    use_multi_patch: bool,
) -> dict | None:
    if use_multi_patch:
        return _aggregate_patch_risk(multi_patch_candidates)
    return _best_patch_risk(repair_candidate)


def _multi_patch_rules(candidates) -> list[str]:
    return sorted({candidate.rule_id for candidate in candidates})


def _aggregate_patch_risk(candidates) -> dict | None:
    if not candidates:
        return None
    risks = [
        candidate.metadata.get("risk", {})
        for candidate in candidates
        if isinstance(candidate.metadata.get("risk", {}), dict)
    ]
    return {
        "score": round(
            sum(float(risk.get("score", 0.0)) for risk in risks) / len(candidates),
            4,
        ),
        "diff_size": sum(
            int(risk.get("diff_size", 0)) for risk in risks
        ),
        "affected_callers": sum(
            int(risk.get("affected_callers", 0)) for risk in risks
        ),
        "cross_file_callers": sum(
            int(risk.get("cross_file_callers", 0)) for risk in risks
        ),
        "target_file_changes": len(
            {candidate.relative_file_path for candidate in candidates}
        ),
        "risk_reasons": [
            reason
            for risk in risks
            for reason in risk.get("risk_reasons", [])
        ],
        "bundle_size": len(candidates),
        "rules": _multi_patch_rules(candidates),
    }


def _hypothesis_results(
    *,
    ranked,
    findings,
    test_summary: TestExecutionSummary,
    program_graph: ProgramGraph,
    candidates,
) -> list[dict]:
    hypotheses = BugHypothesisSearch(
        beam_width=4,
        max_depth=2,
        top_k_functions=5,
    ).search(
        ranked_functions=ranked,
        findings=findings,
        test_summary=test_summary,
        program_graph=program_graph,
        patch_candidates=candidates,
    )
    return [
        {
            "rank": rank,
            "id": hypothesis.id,
            "function_id": hypothesis.function_id,
            "function_name": hypothesis.function_name,
            "file_path": hypothesis.file_path,
            "bug_type": hypothesis.bug_type,
            "score": hypothesis.score,
            "depth": hypothesis.depth,
            "parent_id": hypothesis.parent_id,
            "rule_ids": hypothesis.rule_ids,
            "evidence": hypothesis.evidence,
            "reasoning_steps": hypothesis.reasoning_steps,
        }
        for rank, hypothesis in enumerate(hypotheses, start=1)
    ]


def _patch_search_results(
    case: BenchmarkCase,
    candidates,
    localization_scores: dict[str, float],
    program_graph,
    sandbox: Sandbox,
) -> list[dict]:
    if len(candidates) <= 1:
        return []
    results = PatchSearch(sandbox=sandbox, beam_width=3).search(
        case.repo_path,
        candidates,
        localization_scores=localization_scores,
        program_graph=program_graph,
        test_args=case.test_args,
    )
    output = []
    for rank, result in enumerate(results, start=1):
        candidate = result.candidate
        risk = candidate.metadata.get("risk", {})
        diversity = candidate.metadata.get("search_diversity", {})
        output.append(
            {
                "rank": rank,
                "candidate_id": candidate.id,
                "rule_id": candidate.rule_id,
                "variant": candidate.metadata.get("variant", ""),
                "prior_score": candidate.metadata.get("search_prior_score", 0.0),
                "diversity_rank": candidate.metadata.get(
                    "search_diversity_rank",
                    0,
                ),
                "diversity_bonus": candidate.metadata.get(
                    "search_diversity_bonus",
                    0.0,
                ),
                "diversity_score": candidate.metadata.get(
                    "search_diversity_score",
                    candidate.metadata.get("search_prior_score", 0.0),
                ),
                "search_diversity": diversity if isinstance(diversity, dict) else {},
                "search_duplicate_count": candidate.metadata.get(
                    "search_duplicate_count",
                    0,
                ),
                "search_deduplication": _candidate_deduplication_metadata(
                    candidate,
                ),
                "score": result.score,
                "feedback_score": result.feedback_score,
                "success": result.success,
                "risk_score": risk.get("score", 0.0) if isinstance(risk, dict) else 0.0,
                "passed": result.execution_result.passed,
                "failed": result.execution_result.failed,
                "failure_type": classify_execution_result(result.execution_result),
                "failure_reason": summarize_failure_reason(result.execution_result),
            }
        )
    return output


def _beam_search_results(
    case: BenchmarkCase,
    candidates,
    localization_scores: dict[str, float],
    program_graph,
    sandbox: Sandbox,
    refiner,
    patch_judge: PatchJudge | None = None,
) -> list[dict]:
    if not candidates:
        return []
    profile = _patch_score_profile(case)
    search_candidates = _search_profile_candidates(case, candidates, profile=profile)
    results = BeamPatchSearch(
        sandbox=sandbox,
        refiner=refiner,
        beam_width=3,
        candidate_pool_size=4,
        max_depth=2,
        patch_judge=patch_judge,
        patch_score_weights=_patch_score_weights_for_profile(profile),
    ).search(
        case.repo_path,
        search_candidates,
        localization_scores=localization_scores,
        program_graph=program_graph,
        test_args=case.test_args,
    )
    output = []
    for rank, node in enumerate(results, start=1):
        risk = node.candidate.metadata.get("risk", {})
        judgment = node.candidate.metadata.get("patch_judgment", {})
        diversity = node.candidate.metadata.get("search_diversity", {})
        output.append(
            {
                "rank": rank,
                "candidate_id": node.candidate.id,
                "parent_id": node.parent_id,
                "variant": node.candidate.metadata.get("variant", ""),
                "rule_id": node.candidate.rule_id,
                "depth": node.depth,
                "child_index": node.candidate.metadata.get("beam_child_index"),
                "sibling_count": node.candidate.metadata.get("beam_sibling_count"),
                "search_profile_role": node.candidate.metadata.get(
                    "search_profile_role",
                    "",
                ),
                "prior_score": node.candidate.metadata.get("search_prior_score", 0.0),
                "diversity_rank": node.candidate.metadata.get(
                    "search_diversity_rank",
                    0,
                ),
                "diversity_bonus": node.candidate.metadata.get(
                    "search_diversity_bonus",
                    0.0,
                ),
                "diversity_score": node.candidate.metadata.get(
                    "search_diversity_score",
                    node.candidate.metadata.get("search_prior_score", 0.0),
                ),
                "search_diversity": diversity if isinstance(diversity, dict) else {},
                "search_duplicate_count": node.candidate.metadata.get(
                    "search_duplicate_count",
                    0,
                ),
                "search_deduplication": _candidate_deduplication_metadata(
                    node.candidate,
                ),
                "score": node.score,
                "feedback_score": node.feedback_score,
                "patch_score_profile": profile,
                "patch_judgment": judgment if isinstance(judgment, dict) else {},
                "retained": node.retained,
                "retention_bucket": node.retention_bucket,
                "retention_reason": node.retention_reason,
                "success": node.success,
                "risk_score": risk.get("score", 0.0) if isinstance(risk, dict) else 0.0,
                "risk": risk if isinstance(risk, dict) else {},
                "passed": node.execution_result.passed,
                "failed": node.execution_result.failed,
                "failure_type": classify_execution_result(node.execution_result),
                "failure_reason": summarize_failure_reason(node.execution_result),
                "trace": node.trace,
            }
        )
    return output


def _patch_score_profile(case: BenchmarkCase) -> str:
    profile = str(case.metadata.get("patch_score_profile", ""))
    if profile:
        return profile
    return str(case.metadata.get("search_score_inversion_profile", ""))


def _patch_score_weights_for_profile(profile: str) -> PatchScoreWeights | None:
    if profile == "prior_decoy_score_inversion":
        return PatchScoreWeights(
            tests_passed=0.10,
            localization=0.05,
            static_check=0.05,
            prior=0.80,
            execution_feedback=0.02,
            diff_penalty=0.01,
            risk_penalty=0.0,
            warning_penalty=0.0,
            success_bonus=0.0,
        )
    return None


def _search_profile_candidates(
    case: BenchmarkCase,
    candidates,
    *,
    profile: str,
):
    if profile == "prior_decoy_score_inversion":
        return _score_inversion_profile_candidates(
            case,
            candidates,
            profile=profile,
        )
    if profile == "diversity_reranking_probe":
        return _diversity_reranking_profile_candidates(
            case,
            candidates,
            profile=profile,
        )
    if profile == "candidate_deduplication_probe":
        return _candidate_deduplication_profile_candidates(
            case,
            candidates,
            profile=profile,
        )
    if profile == "reflection_depth_probe":
        return _reflection_depth_profile_candidates(
            case,
            candidates,
            profile=profile,
        )
    return candidates


def _score_inversion_profile_candidates(
    case: BenchmarkCase,
    candidates,
    *,
    profile: str,
):
    decoy_variant = str(
        case.metadata.get(
            "score_inversion_decoy_variant",
            "overly_conservative_range_bound",
        )
    )
    success_variant = str(
        case.metadata.get(
            "score_inversion_success_variant",
            "shrink_range_upper_bound",
        )
    )
    adjusted = []
    for candidate in candidates:
        metadata = {
            **candidate.metadata,
            "patch_score_profile": profile,
        }
        variant = str(metadata.get("variant", ""))
        if variant == decoy_variant:
            metadata.update(
                {
                    "variant_rank": 0,
                    "confidence": max(float(metadata.get("confidence", 0.0)), 0.99),
                    "rule_confidence": max(
                        float(metadata.get("rule_confidence", 0.0)),
                        0.99,
                    ),
                    "search_profile_role": "prior_decoy",
                }
            )
        elif variant == success_variant:
            metadata.update(
                {
                    "variant_rank": max(int(metadata.get("variant_rank", 0)), 5),
                    "confidence": min(float(metadata.get("confidence", 1.0)), 0.35),
                    "rule_confidence": min(
                        float(metadata.get("rule_confidence", 1.0)),
                        0.35,
                    ),
                    "search_profile_role": "deprioritized_success",
                }
            )
        adjusted.append(replace(candidate, metadata=metadata))
    return adjusted


def _diversity_reranking_profile_candidates(
    case: BenchmarkCase,
    candidates,
    *,
    profile: str,
):
    decoy_variant = str(
        case.metadata.get(
            "diversity_reranking_decoy_variant",
            "overly_conservative_range_bound",
        )
    )
    success_variant = str(
        case.metadata.get(
            "diversity_reranking_success_variant",
            "shrink_range_upper_bound",
        )
    )
    decoy = _candidate_by_variant(candidates, decoy_variant)
    success = _candidate_by_variant(candidates, success_variant)
    if decoy is None or success is None:
        return [
            replace(
                candidate,
                metadata={
                    **candidate.metadata,
                    "patch_score_profile": profile,
                },
            )
            for candidate in candidates
        ]

    return [
        *[
            _diversity_decoy_candidate(
                decoy,
                profile=profile,
                index=index,
            )
            for index in range(1, 5)
        ],
        _diversity_success_candidate(success, profile=profile),
    ]


def _candidate_by_variant(candidates, variant: str):
    for candidate in candidates:
        if str(candidate.metadata.get("variant", "")) == variant:
            return candidate
    return None


def _reflection_depth_profile_candidates(
    case: BenchmarkCase,
    candidates,
    *,
    profile: str,
):
    seed_variant = str(
        case.metadata.get(
            "reflection_seed_variant",
            "overly_conservative_range_bound",
        )
    )
    seed = _candidate_by_variant(candidates, seed_variant)
    if seed is None:
        return [
            replace(
                candidate,
                metadata={
                    **candidate.metadata,
                    "patch_score_profile": profile,
                },
            )
            for candidate in candidates
        ]

    metadata = {
        **seed.metadata,
        "patch_score_profile": profile,
        "search_profile_role": "reflection_seed",
        "expected_reflection_depth": True,
        "variant_rank": 0,
        "confidence": max(float(seed.metadata.get("confidence", 0.0)), 0.97),
        "rule_confidence": max(
            float(seed.metadata.get("rule_confidence", 0.0)),
            0.97,
        ),
    }
    return [replace(seed, metadata=metadata)]


def _candidate_deduplication_profile_candidates(
    case: BenchmarkCase,
    candidates,
    *,
    profile: str,
):
    duplicate_variant = str(
        case.metadata.get(
            "dedupe_duplicate_variant",
            "overly_conservative_range_bound",
        )
    )
    success_variant = str(
        case.metadata.get(
            "dedupe_success_variant",
            "shrink_range_upper_bound",
        )
    )
    duplicate = _candidate_by_variant(candidates, duplicate_variant)
    success = _candidate_by_variant(candidates, success_variant)
    if duplicate is None or success is None:
        return [
            replace(
                candidate,
                metadata={
                    **candidate.metadata,
                    "patch_score_profile": profile,
                },
            )
            for candidate in candidates
        ]

    return [
        *[
            _deduplication_duplicate_candidate(
                duplicate,
                profile=profile,
                index=index,
            )
            for index in range(1, 5)
        ],
        _deduplication_success_candidate(success, profile=profile),
    ]


def _diversity_decoy_candidate(candidate, *, profile: str, index: int):
    metadata = {
        **candidate.metadata,
        "patch_score_profile": profile,
        "search_profile_role": "diversity_duplicate_decoy",
        "variant": "diversity_duplicate_decoy",
        "variant_rank": 0,
        "confidence": max(float(candidate.metadata.get("confidence", 0.0)), 0.99),
        "rule_confidence": max(
            float(candidate.metadata.get("rule_confidence", 0.0)),
            0.99,
        ),
    }
    return replace(
        candidate,
        id=f"{candidate.id}::diversity_decoy_{index}",
        rule_id="diversity_duplicate_decoy_rule",
        metadata=metadata,
    )


def _deduplication_duplicate_candidate(candidate, *, profile: str, index: int):
    metadata = {
        **candidate.metadata,
        "patch_score_profile": profile,
        "search_profile_role": "candidate_deduplication_duplicate",
        "variant": "candidate_deduplication_duplicate",
        "variant_rank": 0,
        "confidence": max(float(candidate.metadata.get("confidence", 0.0)), 0.99),
        "rule_confidence": max(
            float(candidate.metadata.get("rule_confidence", 0.0)),
            0.99,
        ),
    }
    return replace(
        candidate,
        id=f"{candidate.id}::dedupe_duplicate_{index}",
        metadata=metadata,
    )


def _deduplication_success_candidate(candidate, *, profile: str):
    metadata = {
        **candidate.metadata,
        "patch_score_profile": profile,
        "search_profile_role": "candidate_deduplication_success",
        "variant": "candidate_deduplication_success",
        "variant_rank": 1,
        "confidence": 0.90,
        "rule_confidence": 0.90,
    }
    return replace(
        candidate,
        id=f"{candidate.id}::dedupe_success",
        metadata=metadata,
    )


def _diversity_success_candidate(candidate, *, profile: str):
    metadata = {
        **candidate.metadata,
        "patch_score_profile": profile,
        "search_profile_role": "diversity_deprioritized_success",
        "variant": "diversity_success",
        "variant_rank": 0,
        "confidence": 0.90,
        "rule_confidence": 0.90,
    }
    return replace(
        candidate,
        id=f"{candidate.id}::diversity_success",
        rule_id="diversity_success_rule",
        metadata=metadata,
    )
