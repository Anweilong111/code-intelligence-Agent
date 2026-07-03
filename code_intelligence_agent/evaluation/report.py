from __future__ import annotations

from code_intelligence_agent.evaluation.ablation import AblationResult
from code_intelligence_agent.evaluation.ablation_impact import (
    AblationImpactReport,
    ablation_impact_report,
)
from code_intelligence_agent.evaluation.benchmark_runner import BenchmarkReport
from code_intelligence_agent.evaluation.patch_weight_search import (
    PatchWeightSearchResult,
    patch_judge_fusion_summary,
)
from code_intelligence_agent.evaluation.weight_search import WeightSearchResult
from code_intelligence_agent.evaluation.judge_cluster_mining import (
    PatchJudgeAuditRow,
    benchmark_mining_suggestions,
    patch_judge_audit_rows,
    patch_judge_failure_clusters,
)
from code_intelligence_agent.evaluation.judge_reliability import (
    CaseJudgeReliabilityReport,
    case_judge_reliability_report,
)
from code_intelligence_agent.evaluation.patch_judge_reliability import (
    PatchJudgeReliabilityReport,
    patch_judge_reliability_report,
)
from code_intelligence_agent.evaluation.localization_calibration import (
    LocalizationCalibrationReport,
    localization_calibration_report,
)
from code_intelligence_agent.evaluation.localization_attribution import (
    localization_attribution_summary,
)
from code_intelligence_agent.evaluation.metric_uncertainty import (
    BenchmarkMetricUncertaintyReport,
    benchmark_metric_uncertainty_report,
)
from code_intelligence_agent.evaluation.search_budget_analysis import (
    SearchBudgetAnalysisReport,
    search_budget_analysis_report,
)
from code_intelligence_agent.evaluation.search_competition_analysis import (
    SearchCompetitionAnalysisReport,
    search_competition_analysis_report,
)
from code_intelligence_agent.evaluation.reflection_analysis import (
    ReflectionAnalysisReport,
    reflection_analysis_report,
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


def render_ablation_markdown(results: list[AblationResult]) -> str:
    lines = [
        "| Variant | Top-1 | Top-3 | MRR | MAP | nDCG@3 | Mean EXAM Score | Rule Recall | Rule Precision | Extra Rules | Loc Cal ECE | Loc Cal Brier | Patch Success | Beam Success | Multi-Patch Success | Repair Rounds |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in results:
        lines.append(
            "| "
            f"{result.variant} | "
            f"{result.top1:.3f} | "
            f"{result.top3:.3f} | "
            f"{result.mrr:.3f} | "
            f"{result.map:.3f} | "
            f"{result.ndcg_at_3:.3f} | "
            f"{result.mean_exam_score:.3f} | "
            f"{result.expected_rule_recall:.3f} | "
            f"{result.expected_rule_precision:.3f} | "
            f"{result.average_extra_rules:.3f} | "
            f"{result.localization_calibrated_expected_calibration_error:.3f} | "
            f"{result.localization_calibrated_brier_score:.3f} | "
            f"{_optional_metric(result.patch_success_rate)} | "
            f"{_optional_metric(result.beam_success_rate)} | "
            f"{_optional_metric(result.multi_patch_success_rate)} | "
            f"{_optional_metric(result.average_repair_rounds)} |"
        )
    impact = ablation_impact_report(results)
    lines.extend(_render_ablation_impact(impact))
    return "\n".join(lines)


def _render_ablation_impact(impact: AblationImpactReport) -> list[str]:
    if not impact.rows:
        return []
    lines = [
        "",
        "## Ablation Impact",
        "",
        f"- Baseline: `{impact.baseline_variant}`; Compared Variants: {impact.impacted_variant_count}",
        "",
        "| Variant | Impact | Direction | Dominant Signal | Dominant Contribution | Regression Signals | Improvement Signals | dTop-1 | dMRR | dMAP | dnDCG@3 | dEXAM Improve | dRule Recall | dRule Precision | dCal ECE Improve | dCal Brier Improve | dPatch | dBeam | dMulti-Patch |",
        "| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in impact.rows:
        lines.append(
            "| "
            f"{row.variant} | "
            f"{row.impact_score:.3f} | "
            f"{row.direction} | "
            f"{row.dominant_signal} | "
            f"{row.dominant_contribution:.3f} | "
            f"{row.regression_signal_count} | "
            f"{row.improvement_signal_count} | "
            f"{row.delta_top1:.3f} | "
            f"{row.delta_mrr:.3f} | "
            f"{row.delta_map:.3f} | "
            f"{row.delta_ndcg_at_3:.3f} | "
            f"{row.delta_exam_improvement:.3f} | "
            f"{row.delta_rule_recall:.3f} | "
            f"{row.delta_rule_precision:.3f} | "
            f"{row.delta_calibrated_ece_improvement:.3f} | "
            f"{row.delta_calibrated_brier_improvement:.3f} | "
            f"{_optional_metric(row.delta_patch_success)} | "
            f"{_optional_metric(row.delta_beam_success)} | "
            f"{_optional_metric(row.delta_multi_patch_success)} |"
        )
    return lines


def _optional_metric(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.3f}"


def render_weight_search_markdown(
    results: list[WeightSearchResult],
    top_n: int = 10,
) -> str:
    lines = [
        "| Rank | Profile | Pareto | Dominates | Dominated By | Robust Score | Validation Score | Source Groups | Min Group Cases | Top-1 Gap | MAP Gap | Top-1 | Top-3 | MRR | MAP | nDCG@3 | Mean EXAM Score | SBFL | Graph | Static | Semantic | LLM | Risk |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for rank, result in enumerate(results[:top_n], start=1):
        weights = result.coverage_weights
        lines.append(
            "| "
            f"{rank} | "
            f"{result.profile} | "
            f"{'yes' if result.pareto_optimal else 'no'} | "
            f"{result.dominates_count} | "
            f"{result.dominated_by_count} | "
            f"{result.robust_validation_score:.3f} | "
            f"{result.validation_score:.3f} | "
            f"{result.source_group_count} | "
            f"{result.min_source_group_cases} | "
            f"{result.max_top1_gap:.3f} | "
            f"{result.max_map_gap:.3f} | "
            f"{result.top1:.3f} | "
            f"{result.top3:.3f} | "
            f"{result.mrr:.3f} | "
            f"{result.map:.3f} | "
            f"{result.ndcg_at_3:.3f} | "
            f"{result.mean_exam_score:.3f} | "
            f"{weights.sbfl:.2f} | "
            f"{weights.graph:.2f} | "
            f"{weights.static:.2f} | "
            f"{weights.semantic:.2f} | "
            f"{weights.llm:.2f} | "
            f"{weights.risk:.2f} |"
        )
    if results:
        lines.extend(_render_weight_search_pareto_summary(results))
        lines.extend(_render_best_weight_search_diagnostics(results[0]))
    return "\n".join(lines)


def _render_weight_search_pareto_summary(
    results: list[WeightSearchResult],
) -> list[str]:
    pareto_profiles = [result for result in results if result.pareto_optimal]
    lines = [
        "",
        "### FinalScore Pareto Frontier",
        "",
        f"- Pareto Profiles: {len(pareto_profiles)}/{len(results)}",
    ]
    if not pareto_profiles:
        return lines
    lines.extend(
        [
            "",
            "| Profile | Robust Score | Top-1 | MAP | Top-1 Gap | MAP Gap | Dominates |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for result in pareto_profiles[:5]:
        lines.append(
            "| "
            f"{_markdown_cell(result.profile)} | "
            f"{result.robust_validation_score:.3f} | "
            f"{result.top1:.3f} | "
            f"{result.map:.3f} | "
            f"{result.max_top1_gap:.3f} | "
            f"{result.max_map_gap:.3f} | "
            f"{result.dominates_count} |"
        )
    return lines


def _render_best_weight_search_diagnostics(result: WeightSearchResult) -> list[str]:
    lines: list[str] = []
    if result.source_groups:
        lines.extend(
            [
                "",
                "### Best Profile Source Groups",
                "",
                "| Source Group | Cases | Top-1 | MRR | MAP | nDCG@3 | EXAM | Validation Score |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for group, metrics in sorted(result.source_groups.items()):
            lines.append(
                "| "
                f"{_markdown_cell(group)} | "
                f"{int(metrics.get('case_count', 0))} | "
                f"{float(metrics.get('top1', 0.0)):.3f} | "
                f"{float(metrics.get('mrr', 0.0)):.3f} | "
                f"{float(metrics.get('map', 0.0)):.3f} | "
                f"{float(metrics.get('ndcg_at_3', 0.0)):.3f} | "
                f"{float(metrics.get('mean_exam_score', 0.0)):.3f} | "
                f"{float(metrics.get('validation_score', 0.0)):.3f} |"
            )
    if result.holdout_splits:
        lines.extend(
            [
                "",
                "### Best Profile Holdout Splits",
                "",
                "| Holdout | Train Groups | Train Cases | Holdout Cases | Train Top-1 | Holdout Top-1 | Top-1 Gap | Train MAP | Holdout MAP | MAP Gap |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for split in result.holdout_splits:
            train_metrics = split.get("train_metrics", {})
            holdout_metrics = split.get("holdout_metrics", {})
            if not isinstance(train_metrics, dict):
                train_metrics = {}
            if not isinstance(holdout_metrics, dict):
                holdout_metrics = {}
            train_groups = split.get("train_groups", [])
            if not isinstance(train_groups, list):
                train_groups = []
            lines.append(
                "| "
                f"{_markdown_cell(split.get('holdout_group', ''))} | "
                f"{_markdown_cell(', '.join(str(item) for item in train_groups))} | "
                f"{int(train_metrics.get('case_count', 0))} | "
                f"{int(holdout_metrics.get('case_count', 0))} | "
                f"{float(train_metrics.get('top1', 0.0)):.3f} | "
                f"{float(holdout_metrics.get('top1', 0.0)):.3f} | "
                f"{float(split.get('top1_gap', 0.0)):.3f} | "
                f"{float(train_metrics.get('map', 0.0)):.3f} | "
                f"{float(holdout_metrics.get('map', 0.0)):.3f} | "
                f"{float(split.get('map_gap', 0.0)):.3f} |"
            )
    return lines


def render_patch_weight_search_markdown(
    results: list[PatchWeightSearchResult],
    top_n: int = 10,
) -> str:
    lines = [
        "| Rank | Profile | Pareto | Dominates | Dominated By | Validation Score | Top-1 Success | MRR | First Success Rank | Success Margin | Test | Loc | Static | Prior | Feedback | Diff Penalty | Risk Penalty | Bonus | Judge Weight |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for rank, result in enumerate(results[:top_n], start=1):
        weights = result.weights
        lines.append(
            "| "
            f"{rank} | "
            f"{result.profile} | "
            f"{'yes' if result.pareto_optimal else 'no'} | "
            f"{result.dominates_count} | "
            f"{result.dominated_by_count} | "
            f"{result.validation_score:.3f} | "
            f"{result.top1_success:.3f} | "
            f"{result.mrr:.3f} | "
            f"{result.average_first_success_rank:.3f} | "
            f"{result.average_success_score_margin:.3f} | "
            f"{weights.tests_passed:.2f} | "
            f"{weights.localization:.2f} | "
            f"{weights.static_check:.2f} | "
            f"{weights.prior:.2f} | "
            f"{weights.execution_feedback:.2f} | "
            f"{weights.diff_penalty:.2f} | "
            f"{weights.risk_penalty:.2f} | "
            f"{weights.success_bonus:.2f} | "
            f"{result.patch_judge_weight:.2f} |"
        )
    if results:
        lines.extend(_render_patch_judge_fusion_summary(results))
        lines.extend(_render_patch_weight_search_pareto_summary(results))
    return "\n".join(lines)


def _render_patch_judge_fusion_summary(
    results: list[PatchWeightSearchResult],
) -> list[str]:
    summary = patch_judge_fusion_summary(results)
    payload = summary.to_dict()
    lines = [
        "",
        "### Patch Judge Fusion Summary",
        "",
        "| Status | Profiles | Judge Profiles | Baseline | Best Judge | Judge Weight | Validation Delta | Top-1 Delta | MRR Delta | Margin Delta | Rank Delta |",
        "| --- | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        "| "
        f"{payload['status']} | "
        f"{payload['profile_count']} | "
        f"{payload['judge_profile_count']} | "
        f"{_markdown_cell(payload['baseline_profile'])} | "
        f"{_markdown_cell(payload['best_judge_profile'])} | "
        f"{payload['best_judge_weight']:.3f} | "
        f"{payload['validation_delta']:.3f} | "
        f"{payload['top1_delta']:.3f} | "
        f"{payload['mrr_delta']:.3f} | "
        f"{payload['success_margin_delta']:.3f} | "
        f"{payload['first_success_rank_delta']:.3f} |",
    ]
    return lines


def _render_patch_weight_search_pareto_summary(
    results: list[PatchWeightSearchResult],
) -> list[str]:
    pareto_profiles = [result for result in results if result.pareto_optimal]
    lines = [
        "",
        "### PatchScore Pareto Frontier",
        "",
        f"- Pareto Profiles: {len(pareto_profiles)}/{len(results)}",
    ]
    if not pareto_profiles:
        return lines
    lines.extend(
        [
            "",
            "| Profile | Validation Score | Top-1 Success | MRR | First Success Rank | Success Margin | Dominates |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for result in pareto_profiles[:5]:
        lines.append(
            "| "
            f"{_markdown_cell(result.profile)} | "
            f"{result.validation_score:.3f} | "
            f"{result.top1_success:.3f} | "
            f"{result.mrr:.3f} | "
            f"{result.average_first_success_rank:.3f} | "
            f"{result.average_success_score_margin:.3f} | "
            f"{result.dominates_count} |"
        )
    return lines


def render_benchmark_markdown(report: BenchmarkReport) -> str:
    localization_calibration = localization_calibration_report(report)
    metric_uncertainty = benchmark_metric_uncertainty_report(report)
    search_budget = search_budget_analysis_report(report)
    search_competition = search_competition_analysis_report(report)
    reflection_analysis = reflection_analysis_report(report)
    difficulty_report = (
        report.difficulty_report
        if report.difficulty_report
        else benchmark_difficulty_summary(report)
    )
    generalization_report = benchmark_generalization_summary(report)
    provenance_audit = benchmark_provenance_summary(report)
    localization_attribution = localization_attribution_summary(report)
    bucket_counts = difficulty_report.get("bucket_counts", {})
    lines = [
        "# Benchmark Report",
        "",
        f"- Cases: {len(report.cases)}",
        f"- Top-1: {report.top1:.3f}",
        f"- Top-3: {report.top3:.3f}",
        f"- MRR: {report.mrr:.3f}",
        f"- MAP: {report.map:.3f}",
        f"- nDCG@3: {report.ndcg_at_3:.3f}",
        f"- Mean EXAM Score: {report.mean_exam_score:.3f}",
        f"- Expected Rule Recall: {report.expected_rule_recall:.3f}",
        f"- Expected Rule Precision: {report.expected_rule_precision:.3f}",
        f"- Patch Success Rate: {report.patch_success_rate:.3f}",
        f"- Multi-Patch Success Rate: {report.multi_patch_success_rate:.3f}",
        f"- Average Repair Rounds: {report.average_repair_rounds:.3f}",
        f"- Average Patch Candidates: {report.average_patch_candidates:.3f}",
        f"- Average Patch Size: {report.average_patch_size:.3f}",
        f"- Average Patch Risk: {report.average_patch_risk:.3f}",
        f"- Reflection Success Rate: {report.reflection_success_rate:.3f}",
        "- Reflection Cases: "
        f"{reflection_analysis.reflection_case_count}",
        "- Reflection Candidate Success Rate: "
        f"{reflection_analysis.reflection_candidate_success_rate:.3f}",
        f"- Beam Success Rate: {report.beam_success_rate:.3f}",
        f"- Patch Search Top-1 Success Rate: {report.patch_search_top1_success_rate:.3f}",
        f"- Patch Search MRR: {report.patch_search_mrr:.3f}",
        f"- Patch Failure Taxonomy: {_format_taxonomy(report.patch_failure_taxonomy)}",
        f"- Average First Success Rank: {report.average_first_success_rank:.3f}",
        f"- Average Beam Depth: {report.average_beam_depth:.3f}",
        f"- Average Evaluated Nodes: {report.average_evaluated_nodes:.3f}",
        "- Average Failed Attempts Before Success: "
        f"{report.average_failed_attempts_before_success:.3f}",
        f"- Average Success Depth: {report.average_success_depth:.3f}",
        f"- Average Success Score Margin: {report.average_success_score_margin:.3f}",
        f"- Search Efficiency: {report.search_efficiency:.3f}",
        f"- Search Budget AUC: {search_budget.budget_auc:.3f}",
        "- Search Success@1: "
        f"{search_budget.success_at_budget.get('1', 0.0):.3f}",
        "- Search Deduplicated Candidates: "
        f"{search_budget.total_deduplicated_candidates}",
        "- Search Competition Cases: "
        f"{search_competition.multi_candidate_case_count}",
        "- Search Score Inversion Rate: "
        f"{search_competition.to_dict()['score_inversion_rate']:.3f}",
        "- Search Diversity-Assisted Successes: "
        f"{search_competition.diversity_assisted_success_count}",
        "- Search Average Diversity Lift: "
        f"{search_competition.average_diversity_lift:.3f}",
        f"- Hypothesis Top-1: {report.hypothesis_top1:.3f}",
        f"- Hypothesis MRR: {report.hypothesis_mrr:.3f}",
        f"- Hypothesis MAP: {report.hypothesis_map:.3f}",
        f"- Hypothesis nDCG@3: {report.hypothesis_ndcg_at_3:.3f}",
        f"- Hypothesis Mean EXAM Score: {report.hypothesis_mean_exam_score:.3f}",
        f"- Average Hypothesis Depth: {report.average_hypothesis_depth:.3f}",
        "- Average Hypothesis Evidence Count: "
        f"{report.average_hypothesis_evidence_count:.3f}",
        f"- Data-flow Evidence Cases: {report.data_flow_evidence_case_count}",
        "- Cross-function Data-flow Cases: "
        f"{report.cross_function_data_flow_case_count}",
        "- Subscript Key-flow Cases: "
        f"{report.subscript_key_flow_case_count}",
        "- Average Top-1 Data Dependency: "
        f"{report.average_top1_data_dependency:.3f}",
        f"- Program Slice Cases: {report.program_slice_case_count}",
        f"- Average Top-1 Slice Edges: {report.average_top1_slice_edges:.3f}",
        "- Average Top-1 Slice Cross-function Edges: "
        f"{report.average_top1_slice_cross_function_edges:.3f}",
        f"- Slice-grounded Cases: {report.slice_grounded_case_count}",
        f"- Average Top-1 Slice Support: {report.average_top1_slice_support:.3f}",
        "- Average Top-1 Slice Failed-test Reachability: "
        f"{report.average_top1_slice_failed_test_reachability:.3f}",
        "- Average Top-1 Slice Call-chain Coverage: "
        f"{report.average_top1_slice_call_chain_coverage:.3f}",
        f"- Difficulty Buckets: {_format_taxonomy(bucket_counts)}",
        "- Source Groups: "
        f"{int(generalization_report.get('source_group_count', 0))}",
        "- Localization Attribution Coverage: "
        f"{float(localization_attribution.get('attribution_coverage', 0.0)):.3f}",
        "- Fragile Top-1 Rate: "
        f"{float(localization_attribution.get('fragile_top1_rate', 0.0)):.3f}",
    ]
    if report.repository_test_evidence:
        lines.append(
            "- Repository Test Evidence: "
            f"`{_markdown_cell(_format_repository_test_evidence(report.repository_test_evidence))}`"
        )
    if localization_calibration.case_count:
        lines.extend(
            [
                "- Localization Calibration Brier Score: "
                f"{localization_calibration.brier_score:.3f}",
                "- Localization Calibration ECE: "
                f"{localization_calibration.expected_calibration_error:.3f}",
            ]
        )
    lines.extend(
        [
            "",
            "| Case | Bug Type | Coverage | Top-1 | Top-3 | MRR | AP | nDCG@3 | EXAM | Hyp Top-1 | Hyp MRR | Hyp AP | Hyp nDCG@3 | Hyp EXAM | Rule Recall | Rule Precision | Extra Rules | Patch | Strategy | Multi-Patch | Bundle | Rounds | Best Rule | Patch Risk |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- | ---: | ---: | ---: | --- | ---: |",
        ]
    )
    for case in report.cases:
        patch_risk = case.best_patch_risk or {}
        lines.append(
            "| "
            f"{case.case_name} | "
            f"{case.bug_type} | "
            f"{case.coverage_mode} | "
            f"{int(case.top1_hit)} | "
            f"{int(case.top3_hit)} | "
            f"{case.mrr:.3f} | "
            f"{case.average_precision:.3f} | "
            f"{case.ndcg_at_3:.3f} | "
            f"{case.exam_score:.3f} | "
            f"{int(case.hypothesis_top1_hit)} | "
            f"{case.hypothesis_mrr:.3f} | "
            f"{case.hypothesis_average_precision:.3f} | "
            f"{case.hypothesis_ndcg_at_3:.3f} | "
            f"{case.hypothesis_exam_score:.3f} | "
            f"{case.expected_rule_recall:.3f} | "
            f"{case.expected_rule_precision:.3f} | "
            f"{', '.join(case.extra_rule_ids)} | "
            f"{int(case.patch_success)} | "
            f"{case.repair_strategy} | "
            f"{int(case.multi_patch_success)} | "
            f"{case.multi_patch_bundle_size} | "
            f"{case.repair_rounds} | "
            f"{case.best_patch_rule_id or ''} | "
            f"{patch_risk.get('score', 0.0):.4f} |"
        )
    lines.extend(_render_difficulty_report(difficulty_report))
    lines.extend(_render_generalization_report(generalization_report))
    lines.extend(_render_benchmark_provenance_audit(provenance_audit))
    lines.extend(_render_localization_attribution(localization_attribution))
    lines.extend(_render_metric_uncertainty(metric_uncertainty))
    lines.extend(_render_search_budget_analysis(search_budget))
    lines.extend(_render_search_competition_analysis(search_competition))
    lines.extend(_render_localization_calibration(localization_calibration))
    judged_cases = [case for case in report.cases if case.llm_judgment]
    if judged_cases:
        reliability = case_judge_reliability_report(report)
        lines.extend(
            [
                "",
                "## LLM Judge Results",
                "",
                "| Case | Verdict | Score | Model | Reason |",
                "| --- | --- | ---: | --- | --- |",
            ]
        )
        for case in judged_cases:
            judgment = case.llm_judgment or {}
            score = float(judgment.get("score", 0.0))
            lines.append(
                "| "
                f"{_markdown_cell(case.case_name)} | "
                f"{_markdown_cell(judgment.get('verdict', ''))} | "
                f"{score:.3f} | "
                f"{_markdown_cell(judgment.get('model', ''))} | "
                f"{_markdown_cell(judgment.get('reason', ''))} |"
            )
        lines.extend(_render_case_judge_reliability(reliability))
    lines.extend(
        [
            "",
            "## Metrics by Bug Type",
            "",
            "| Bug Type | Cases | Top-1 | Top-3 | MRR | MAP | nDCG@3 | EXAM | Hyp Top-1 | Hyp MRR | Hyp MAP | Hyp nDCG@3 | Hyp EXAM | Rule Recall | Rule Precision | Patch Success | Multi-Patch | Patch Top-1 | Patch MRR | First Success Rank | Search Efficiency |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for bug_type, metrics in report.bug_type_metrics.items():
        lines.append(
            "| "
            f"{bug_type} | "
            f"{int(metrics.get('case_count', 0))} | "
            f"{metrics.get('top1', 0.0):.3f} | "
            f"{metrics.get('top3', 0.0):.3f} | "
            f"{metrics.get('mrr', 0.0):.3f} | "
            f"{metrics.get('map', 0.0):.3f} | "
            f"{metrics.get('ndcg_at_3', 0.0):.3f} | "
            f"{metrics.get('mean_exam_score', 0.0):.3f} | "
            f"{metrics.get('hypothesis_top1', 0.0):.3f} | "
            f"{metrics.get('hypothesis_mrr', 0.0):.3f} | "
            f"{metrics.get('hypothesis_map', 0.0):.3f} | "
            f"{metrics.get('hypothesis_ndcg_at_3', 0.0):.3f} | "
            f"{metrics.get('hypothesis_mean_exam_score', 0.0):.3f} | "
            f"{metrics.get('expected_rule_recall', 0.0):.3f} | "
            f"{metrics.get('expected_rule_precision', 0.0):.3f} | "
            f"{metrics.get('patch_success_rate', 0.0):.3f} | "
            f"{metrics.get('multi_patch_success_rate', 0.0):.3f} | "
            f"{metrics.get('patch_search_top1_success_rate', 0.0):.3f} | "
            f"{metrics.get('patch_search_mrr', 0.0):.3f} | "
            f"{metrics.get('average_first_success_rank', 0.0):.3f} | "
            f"{metrics.get('search_efficiency', 0.0):.3f} |"
        )
    lines.extend(
        [
            "",
            "## Metrics by Expected Rule",
            "",
            "| Rule | Cases | Top-1 | Top-3 | MRR | MAP | nDCG@3 | EXAM | Hyp Top-1 | Hyp MRR | Hyp MAP | Hyp nDCG@3 | Hyp EXAM | Rule Recall | Rule Precision | Patch Success | Multi-Patch | Patch Top-1 | Patch MRR | First Success Rank | Search Efficiency |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for rule_id, metrics in report.rule_metrics.items():
        lines.append(
            "| "
            f"{rule_id} | "
            f"{int(metrics.get('case_count', 0))} | "
            f"{metrics.get('top1', 0.0):.3f} | "
            f"{metrics.get('top3', 0.0):.3f} | "
            f"{metrics.get('mrr', 0.0):.3f} | "
            f"{metrics.get('map', 0.0):.3f} | "
            f"{metrics.get('ndcg_at_3', 0.0):.3f} | "
            f"{metrics.get('mean_exam_score', 0.0):.3f} | "
            f"{metrics.get('hypothesis_top1', 0.0):.3f} | "
            f"{metrics.get('hypothesis_mrr', 0.0):.3f} | "
            f"{metrics.get('hypothesis_map', 0.0):.3f} | "
            f"{metrics.get('hypothesis_ndcg_at_3', 0.0):.3f} | "
            f"{metrics.get('hypothesis_mean_exam_score', 0.0):.3f} | "
            f"{metrics.get('expected_rule_recall', 0.0):.3f} | "
            f"{metrics.get('expected_rule_precision', 0.0):.3f} | "
            f"{metrics.get('patch_success_rate', 0.0):.3f} | "
            f"{metrics.get('multi_patch_success_rate', 0.0):.3f} | "
            f"{metrics.get('patch_search_top1_success_rate', 0.0):.3f} | "
            f"{metrics.get('patch_search_mrr', 0.0):.3f} | "
            f"{metrics.get('average_first_success_rank', 0.0):.3f} | "
            f"{metrics.get('search_efficiency', 0.0):.3f} |"
        )
    lines.extend(
        [
            "",
            "## Data-flow Evidence",
            "",
            "| Case | Top Function | Data Dependency | Internal Edges | Key Flow Edges | Arg Flow Edges | Return Flow Edges | Cross-function Edges | Total Edges |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for case in report.cases:
        if not case.localization_details:
            continue
        detail = case.localization_details[0]
        evidence = detail.get("data_flow_evidence", {})
        graph_components = detail.get("graph_components", {})
        lines.append(
            "| "
            f"{case.case_name} | "
            f"{detail.get('function_name', '')} | "
            f"{graph_components.get('data_dependency', 0.0):.4f} | "
            f"{int(evidence.get('internal_edges', 0))} | "
            f"{int(evidence.get('key_flow_edges', 0))} | "
            f"{int(evidence.get('arg_flow_edges', 0))} | "
            f"{int(evidence.get('return_flow_edges', 0))} | "
            f"{int(evidence.get('cross_function_edges', 0))} | "
            f"{int(evidence.get('total_edges', 0))} |"
        )
    lines.extend(
        [
            "",
            "## Program Slice Evidence",
            "",
            "| Case | Top Function | Nodes | Edges | Calls | Data-flow | XFunc Data-flow | Control | CFG | Module Deps | Variables | Callers | Callees |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for case in report.cases:
        if not case.localization_details:
            continue
        detail = case.localization_details[0]
        program_slice = detail.get("program_slice", {})
        if not isinstance(program_slice, dict):
            program_slice = {}
        variables = program_slice.get("variables", [])
        callers = program_slice.get("incoming_callers", [])
        callees = program_slice.get("outgoing_callees", [])
        if not isinstance(variables, list):
            variables = []
        if not isinstance(callers, list):
            callers = []
        if not isinstance(callees, list):
            callees = []
        lines.append(
            "| "
            f"{case.case_name} | "
            f"{detail.get('function_name', '')} | "
            f"{int(program_slice.get('node_count', 0))} | "
            f"{int(program_slice.get('edge_count', 0))} | "
            f"{int(program_slice.get('call_edge_count', 0))} | "
            f"{int(program_slice.get('data_flow_edge_count', 0))} | "
            f"{int(program_slice.get('cross_function_data_flow_edge_count', 0))} | "
            f"{int(program_slice.get('control_flow_edge_count', 0))} | "
            f"{int(program_slice.get('cfg_edge_count', 0))} | "
            f"{int(program_slice.get('module_dependency_edge_count', 0))} | "
            f"{_markdown_cell(', '.join(str(item) for item in variables[:6]))} | "
            f"{_markdown_cell(', '.join(str(item) for item in callers[:4]))} | "
            f"{_markdown_cell(', '.join(str(item) for item in callees[:4]))} |"
        )
    lines.extend(
        [
            "",
            "## Slice-grounded Localization",
            "",
            "| Case | Top Function | Support | Grounded | Failed-test Reach | Coverage | Call-chain Coverage | Data-flow | Control/CFG | Cross-boundary | Dimensions | Reasons | Shortest Failed Chain |",
            "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for case in report.cases:
        if not case.localization_details:
            continue
        detail = case.localization_details[0]
        grounding = detail.get("slice_grounding", {})
        if not isinstance(grounding, dict):
            grounding = {}
        reasons = grounding.get("support_reasons", [])
        if not isinstance(reasons, list):
            reasons = []
        chain = grounding.get("shortest_failed_call_chain", [])
        if not isinstance(chain, list):
            chain = []
        lines.append(
            "| "
            f"{case.case_name} | "
            f"{detail.get('function_name', '')} | "
            f"{float(grounding.get('support_score', 0.0)):.4f} | "
            f"{bool(grounding.get('grounded', False))} | "
            f"{float(grounding.get('failed_test_reachability', 0.0)):.4f} | "
            f"{float(grounding.get('failing_coverage_ratio', 0.0)):.4f} | "
            f"{float(grounding.get('call_chain_edge_coverage', 0.0)):.4f} | "
            f"{float(grounding.get('data_flow_support', 0.0)):.4f} | "
            f"{float(grounding.get('control_flow_support', 0.0)):.4f} | "
            f"{float(grounding.get('cross_boundary_support', 0.0)):.4f} | "
            f"{int(grounding.get('evidence_dimension_count', 0))} | "
            f"{_markdown_cell(', '.join(str(item) for item in reasons))} | "
            f"{_markdown_cell(' -> '.join(str(item) for item in chain))} |"
        )
    lines.extend(
        [
            "",
            "## Localization Details",
            "",
            "| Case | Rank | Function | Score | Failed Covered | Passed Covered | Total Failed | Ochiai | Static | Graph | Semantic | LLM | Trace | Coverage | Line Coverage | Statement SBFL | Branch SBFL | Path SBFL | Data Dependency | Control Flow | PageRank | Proximity | Caller Impact | Module Dependency | Async Call | Centrality | Patch Risk | Call Chain |",
            "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for case in report.cases:
        for detail in case.localization_details:
            signals = detail.get("signals", {})
            graph_components = detail.get("graph_components", {})
            call_chain = " -> ".join(detail.get("call_chain", []))
            lines.append(
                "| "
                f"{case.case_name} | "
                f"{detail['rank']} | "
                f"{detail['function_name']} | "
                f"{detail['score']:.4f} | "
                f"{detail['failed_covered']} | "
                f"{detail['passed_covered']} | "
                f"{detail['total_failed']} | "
                f"{detail['ochiai']:.4f} | "
                f"{signals.get('static', 0.0):.4f} | "
                f"{signals.get('graph', 0.0):.4f} | "
                f"{signals.get('semantic', 0.0):.4f} | "
                f"{signals.get('llm', 0.0):.4f} | "
                f"{graph_components.get('traceback_hit', 0.0):.4f} | "
                f"{graph_components.get('test_coverage', 0.0):.4f} | "
                f"{graph_components.get('line_coverage', 0.0):.4f} | "
                f"{graph_components.get('statement_sbfl', 0.0):.4f} | "
                f"{graph_components.get('branch_sbfl', 0.0):.4f} | "
                f"{graph_components.get('path_sbfl', 0.0):.4f} | "
                f"{graph_components.get('data_dependency', 0.0):.4f} | "
                f"{graph_components.get('control_flow', 0.0):.4f} | "
                f"{graph_components.get('pagerank', 0.0):.4f} | "
                f"{graph_components.get('proximity', 0.0):.4f} | "
                f"{graph_components.get('caller_impact', 0.0):.4f} | "
                f"{graph_components.get('module_dependency', 0.0):.4f} | "
                f"{graph_components.get('async_call', 0.0):.4f} | "
                f"{graph_components.get('centrality', 0.0):.4f} | "
                f"{graph_components.get('patch_risk', 0.0):.4f} | "
                f"{call_chain} |"
            )
    lines.extend(
        [
            "",
            "## Patch Risk Details",
            "",
            "| Case | Risk | Diff Size | Affected Callers | Cross-file Callers | Files Changed | Data Fanout | Changed Vars | Return/Control | Reasons |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for case in report.cases:
        risk = case.best_patch_risk or {}
        changed_variables = risk.get("changed_variables", [])
        if not isinstance(changed_variables, list):
            changed_variables = []
        lines.append(
            "| "
            f"{case.case_name} | "
            f"{risk.get('score', 0.0):.4f} | "
            f"{risk.get('diff_size', 0)} | "
            f"{risk.get('affected_callers', 0)} | "
            f"{risk.get('cross_file_callers', 0)} | "
            f"{risk.get('target_file_changes', 0)} | "
            f"{risk.get('data_dependency_fanout', 0)} | "
            f"{_markdown_cell(', '.join(str(item) for item in changed_variables))} | "
            f"{risk.get('return_or_control_changed', False)} | "
            f"{_markdown_cell(', '.join(risk.get('risk_reasons', [])))} |"
        )
    lines.extend(
        [
            "",
            "## Repair Results",
            "",
            "| Case | Rank | Round | Variant | Rule | Score | Success | Risk | Passed | Failed | Failure Type | Reflection | Reason |",
            "| --- | ---: | ---: | --- | --- | ---: | --- | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for case in report.cases:
        for result in case.repair_results:
            lines.append(
                "| "
                f"{case.case_name} | "
                f"{result['rank']} | "
                f"{result['round']} | "
                f"{result['variant']} | "
                f"{result['rule_id']} | "
                f"{result['score']:.4f} | "
                f"{result['success']} | "
                f"{result['risk_score']:.4f} | "
                f"{result['passed']} | "
                f"{result['failed']} | "
                f"{_markdown_cell(result.get('failure_type', ''))} | "
                f"{_markdown_cell(result.get('reflection_error_type', ''))} | "
                f"{_markdown_cell(result.get('failure_reason', ''))} |"
            )
    lines.extend(_render_reflection_analysis(reflection_analysis))
    lines.extend(
        [
            "",
            "## Patch Search Results",
            "",
            "| Case | Rank | Variant | Prior | Diversity Rank | Diversity Bonus | Diversity Score | Score | Feedback | Success | Risk | Passed | Failed | Failure Type | Reason |",
            "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for case in report.cases:
        for result in case.patch_search_results:
            lines.append(
                "| "
                f"{case.case_name} | "
                f"{result['rank']} | "
                f"{result['variant']} | "
                f"{float(result.get('prior_score', 0.0)):.4f} | "
                f"{_nullable_metric(result.get('diversity_rank'))} | "
                f"{float(result.get('diversity_bonus', 0.0)):.4f} | "
                f"{float(result.get('diversity_score', 0.0)):.4f} | "
                f"{result['score']:.4f} | "
                f"{float(result.get('feedback_score', 0.0)):.4f} | "
                f"{result['success']} | "
                f"{result['risk_score']:.4f} | "
                f"{result['passed']} | "
                f"{result['failed']} | "
                f"{_markdown_cell(result.get('failure_type', ''))} | "
                f"{_markdown_cell(result.get('failure_reason', ''))} |"
            )
    lines.extend(
        [
            "",
            "## Multi-Patch Results",
            "",
            "| Case | Rank | Bundle | Functions | Rules | Variants | Score | Graph Bonus | Cross File | Calls | Module Deps | Relative Imports | Max Package Distance | Package Distance Bonus | Data Flow | Key Flow | Success | Passed | Failed |",
            "| --- | ---: | ---: | --- | --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
        ]
    )
    for case in report.cases:
        for result in case.multi_patch_results:
            lines.append(
                "| "
                f"{case.case_name} | "
                f"{result['rank']} | "
                f"{result['bundle_size']} | "
                f"{_markdown_cell(', '.join(result['functions']))} | "
                f"{_markdown_cell(', '.join(result['rules']))} | "
                f"{_markdown_cell(', '.join(result['variants']))} | "
                f"{result['score']:.4f} | "
                f"{float(result.get('graph_bonus', 0.0)):.4f} | "
                f"{result.get('cross_file', False)} | "
                f"{result.get('direct_call_edges', 0)} | "
                f"{result.get('module_dependency_edges', 0)} | "
                f"{result.get('relative_import_edges', 0)} | "
                f"{result.get('max_package_distance', 0)} | "
                f"{float(result.get('package_distance_bonus', 0.0)):.4f} | "
                f"{result.get('data_flow_edges', 0)} | "
                f"{result.get('key_flow_edges', 0)} | "
                f"{result['success']} | "
                f"{result['passed']} | "
                f"{result['failed']} |"
            )
    lines.extend(
        [
            "",
            "## Beam Search Results",
            "",
            "| Case | Rank | Depth | Child | Siblings | Variant | Rule | Prior | Diversity Rank | Diversity Bonus | Diversity Score | Score | Feedback | Patch Judge | Calibrated Judge | Judge Verdict | Judge Agreement | Retained | Bucket | Success | Passed | Failed | Failure Type | Parent | Reason |",
            "| --- | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- |",
        ]
    )
    for case in report.cases:
        for result in case.beam_search_results:
            patch_judgment = result.get("patch_judgment", {})
            if not isinstance(patch_judgment, dict):
                patch_judgment = {}
            lines.append(
                "| "
                f"{case.case_name} | "
                f"{result['rank']} | "
                f"{result['depth']} | "
                f"{_nullable_metric(result.get('child_index'))} | "
                f"{_nullable_metric(result.get('sibling_count'))} | "
                f"{result['variant']} | "
                f"{result['rule_id']} | "
                f"{float(result.get('prior_score', 0.0)):.4f} | "
                f"{_nullable_metric(result.get('diversity_rank'))} | "
                f"{float(result.get('diversity_bonus', 0.0)):.4f} | "
                f"{float(result.get('diversity_score', 0.0)):.4f} | "
                f"{result['score']:.4f} | "
                f"{float(result.get('feedback_score', 0.0)):.4f} | "
                f"{float(patch_judgment.get('score', 0.0)):.4f} | "
                f"{float(patch_judgment.get('calibrated_score', 0.0)):.4f} | "
                f"{_markdown_cell(str(patch_judgment.get('verdict', '')))} | "
                f"{_markdown_cell(str(patch_judgment.get('agreement', '')))} | "
                f"{result.get('retained', True)} | "
                f"{_markdown_cell(result.get('retention_bucket', ''))} | "
                f"{result['success']} | "
                f"{result.get('passed', 0)} | "
                f"{result.get('failed', 0)} | "
                f"{_markdown_cell(result.get('failure_type', ''))} | "
                f"{result['parent_id'] or ''} | "
                f"{_markdown_cell(result.get('retention_reason', ''))} |"
            )
    patch_judge_rows = patch_judge_audit_rows(report)
    if patch_judge_rows:
        patch_judge_reliability = patch_judge_reliability_report(report)
        lines.extend(
            [
                "",
                "## Patch Judge Audit",
                "",
                _patch_judge_audit_summary(patch_judge_rows),
                "",
                "| Case | Rank | Candidate | Raw | Calibrated | Delta | Agreement | Verdict | Reasons |",
                "| --- | ---: | --- | ---: | ---: | ---: | --- | --- | --- |",
            ]
        )
        for row in patch_judge_rows:
            lines.append(
                "| "
                f"{_markdown_cell(row.case)} | "
                f"{row.rank} | "
                f"{_markdown_cell(row.candidate_id)} | "
                f"{row.raw_score:.4f} | "
                f"{row.calibrated_score:.4f} | "
                f"{row.delta:.4f} | "
                f"{_markdown_cell(row.agreement)} | "
                f"{_markdown_cell(row.verdict)} | "
                f"{_markdown_cell(row.reasons)} |"
            )
        lines.extend(_render_patch_judge_reliability(patch_judge_reliability))
        cluster_rows = patch_judge_failure_clusters(patch_judge_rows)
        if cluster_rows:
            lines.extend(
                [
                    "",
                    "## Patch Judge Failure Clusters",
                    "",
                    "| Failure Type | Bucket | Agreement | Calibration Pattern | Count | Avg Raw | Avg Calibrated | Avg Delta | Examples |",
                    "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
                ]
            )
            for row in cluster_rows:
                lines.append(
                    "| "
                    f"{_markdown_cell(row.failure_type)} | "
                    f"{_markdown_cell(row.bucket)} | "
                    f"{_markdown_cell(row.agreement)} | "
                    f"{_markdown_cell(row.pattern)} | "
                    f"{row.count} | "
                    f"{row.average_raw:.4f} | "
                    f"{row.average_calibrated:.4f} | "
                    f"{row.average_delta:.4f} | "
                    f"{_markdown_cell(', '.join(row.examples))} |"
                )
            mining_rows = benchmark_mining_suggestions(cluster_rows)
            if mining_rows:
                lines.extend(
                    [
                        "",
                        "## Patch Judge Benchmark Mining",
                        "",
                        "| Priority | Focus | Failure Type | Pattern | Suggested Case Shape | Rationale | Evidence | Examples |",
                        "| --- | --- | --- | --- | --- | --- | ---: | --- |",
                    ]
                )
                for row in mining_rows:
                    lines.append(
                        "| "
                        f"{_markdown_cell(row.priority)} | "
                        f"{_markdown_cell(row.benchmark_focus)} | "
                        f"{_markdown_cell(row.failure_type)} | "
                        f"{_markdown_cell(row.pattern)} | "
                        f"{_markdown_cell(row.suggested_case_shape)} | "
                        f"{_markdown_cell(row.rationale)} | "
                        f"{row.evidence_count} | "
                        f"{_markdown_cell(', '.join(row.examples))} |"
                    )
    lines.extend(
        [
            "",
            "## Search Analysis",
            "",
            "| Case | Nodes | Success Nodes | Max Depth | First Success Rank | Success Depth | Failed Before Success | Score Margin | Efficiency |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for case in report.cases:
        analysis = case.search_analysis
        lines.append(
            "| "
            f"{case.case_name} | "
            f"{analysis.get('evaluated_nodes', 0)} | "
            f"{analysis.get('successful_nodes', 0)} | "
            f"{analysis.get('max_depth', 0)} | "
            f"{_nullable_metric(analysis.get('first_success_rank'))} | "
            f"{_nullable_metric(analysis.get('first_success_depth'))} | "
            f"{analysis.get('failures_before_success', 0)} | "
            f"{float(analysis.get('success_score_margin', 0.0)):.4f} | "
            f"{float(analysis.get('efficiency', 0.0)):.4f} |"
        )
    lines.extend(
        [
            "",
            "## Hypothesis Search Results",
            "",
            "| Case | Rank | Depth | Function | Bug Type | Score | Rules | Evidence | Reasoning Steps |",
            "| --- | ---: | ---: | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for case in report.cases:
        for hypothesis in case.hypothesis_results[:5]:
            evidence = hypothesis.get("evidence", {})
            compact_evidence = _compact_evidence(evidence)
            steps = " / ".join(hypothesis.get("reasoning_steps", []))
            lines.append(
                "| "
                f"{case.case_name} | "
                f"{hypothesis.get('rank', 0)} | "
                f"{hypothesis.get('depth', 0)} | "
                f"{_markdown_cell(hypothesis.get('function_name', ''))} | "
                f"{_markdown_cell(hypothesis.get('bug_type', ''))} | "
                f"{float(hypothesis.get('score', 0.0)):.4f} | "
                f"{_markdown_cell(', '.join(hypothesis.get('rule_ids', [])))} | "
                f"{_markdown_cell(compact_evidence)} | "
                f"{_markdown_cell(steps)} |"
            )
    return "\n".join(lines)


def _markdown_cell(value) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def _format_repository_test_evidence(evidence: dict) -> str:
    if not evidence:
        return "none"
    failure_overlay = evidence.get("failure_overlay")
    if not isinstance(failure_overlay, dict):
        failure_overlay = {}
    public_api = failure_overlay.get("public_api_evidence")
    if not isinstance(public_api, dict):
        public_api = {}
    scope = str(public_api.get("trigger_scope") or "unknown")
    trigger_expression = str(public_api.get("trigger_expression") or "unknown")
    internal_target = str(public_api.get("internal_target") or "unknown")
    return f"{scope}: {trigger_expression} -> {internal_target}"


def _nullable_metric(value) -> str:
    if value is None:
        return ""
    return str(value)


def _format_taxonomy(taxonomy: dict[str, int]) -> str:
    if not taxonomy:
        return ""
    return ", ".join(f"{name}={count}" for name, count in sorted(taxonomy.items()))


def _render_reflection_analysis(
    report: ReflectionAnalysisReport,
) -> list[str]:
    if report.case_count == 0:
        return []
    lines = [
        "",
        "## Reflection Analysis",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Cases | {report.case_count} |",
        f"| Reflection Cases | {report.reflection_case_count} |",
        f"| Reflection Success Cases | {report.reflection_success_case_count} |",
        f"| Reflection Candidates | {report.reflection_candidate_count} |",
        (
            "| Retained Reflection Candidates | "
            f"{report.retained_reflection_candidate_count} |"
        ),
        (
            "| Successful Reflection Candidates | "
            f"{report.successful_reflection_candidate_count} |"
        ),
        (
            "| Case Success Rate | "
            f"{report.reflection_case_success_rate:.3f} |"
        ),
        (
            "| Candidate Success Rate | "
            f"{report.reflection_candidate_success_rate:.3f} |"
        ),
        f"| Average Reflection Depth | {report.average_reflection_depth:.3f} |",
        (
            "| Average Success Reflection Depth | "
            f"{report.average_success_reflection_depth:.3f} |"
        ),
        (
            "| Average Score Delta From Parent | "
            f"{report.average_score_delta_from_parent:.3f} |"
        ),
    ]
    if report.parent_failure_type_counts:
        lines.extend(
            [
                (
                    "| Parent Failure Types | "
                    f"{_markdown_cell(_format_taxonomy(report.parent_failure_type_counts))} |"
                ),
                (
                    "| Success Parent Failure Types | "
                    f"{_markdown_cell(_format_taxonomy(report.success_parent_failure_type_counts))} |"
                ),
            ]
        )
    reflection_rows = [
        row for row in report.rows if row.reflection_candidate_count > 0
    ]
    if not reflection_rows:
        return lines
    lines.extend(
        [
            "",
            "| Case | Reflection Candidates | Successes | Max Depth | First Success Depth | Avg Score Delta | Parent Failures | Parent Buckets | Success Parent Failure |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in reflection_rows:
        lines.append(
            "| "
            f"{_markdown_cell(row.case)} | "
            f"{row.reflection_candidate_count} | "
            f"{row.successful_reflection_candidate_count} | "
            f"{row.max_reflection_depth} | "
            f"{_nullable_metric(row.first_success_reflection_depth)} | "
            f"{row.average_score_delta_from_parent:.4f} | "
            f"{_markdown_cell(', '.join(row.parent_failure_types))} | "
            f"{_markdown_cell(', '.join(row.parent_retention_buckets))} | "
            f"{_markdown_cell(row.success_parent_failure_type)} |"
        )
    return lines


def _render_difficulty_report(report: dict) -> list[str]:
    if not report or not report.get("case_count"):
        return []
    lines = [
        "",
        "## Benchmark Difficulty",
        "",
        f"- Cases: {int(report.get('case_count', 0))}",
        "",
        "| Bucket | Cases | Top-1 | Top-3 | MRR | MAP | nDCG@3 | EXAM | Patch Success | Multi-Patch | Avg Candidates | Avg Nodes | Avg Failed Before Success | Avg Patch Risk |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for bucket, metrics in report.get("bucket_metrics", {}).items():
        lines.append(_difficulty_metric_row(bucket, metrics))
    label_metrics = report.get("label_metrics", {})
    if label_metrics:
        lines.extend(
            [
                "",
                "| Label | Cases | Top-1 | Top-3 | MRR | MAP | nDCG@3 | EXAM | Patch Success | Multi-Patch | Avg Candidates | Avg Nodes | Avg Failed Before Success | Avg Patch Risk |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for label, metrics in label_metrics.items():
            lines.append(_difficulty_metric_row(label, metrics))
    rows = report.get("cases", [])
    if rows:
        lines.extend(
            [
                "",
                "| Case | Bucket | Score | Labels | GT | Hops | XFunc Data | XFile Callers | Candidates | Nodes | Failed Before Success | Success Depth | Bundle | Patch Risk |",
                "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in rows:
            labels = row.get("labels", [])
            if not isinstance(labels, list):
                labels = []
            lines.append(
                "| "
                f"{_markdown_cell(row.get('case_name', ''))} | "
                f"{_markdown_cell(row.get('bucket', ''))} | "
                f"{int(row.get('score', 0))} | "
                f"{_markdown_cell(', '.join(str(item) for item in labels))} | "
                f"{int(row.get('ground_truth_count', 0))} | "
                f"{int(row.get('max_call_chain_hops', 0))} | "
                f"{int(row.get('cross_function_data_flow_edges', 0))} | "
                f"{int(row.get('cross_file_callers', 0))} | "
                f"{int(row.get('patch_candidates_count', 0))} | "
                f"{int(row.get('evaluated_nodes', 0))} | "
                f"{int(row.get('failures_before_success', 0))} | "
                f"{int(row.get('success_depth', 0))} | "
                f"{int(row.get('multi_patch_bundle_size', 0))} | "
                f"{float(row.get('patch_risk_score', 0.0)):.4f} |"
            )
    return lines


def _render_generalization_report(report: dict) -> list[str]:
    if not report or not report.get("case_count"):
        return []
    lines = [
        "",
        "## Benchmark Generalization",
        "",
        (
            f"- Source Groups: {int(report.get('source_group_count', 0))}; "
            f"Split Key: `{_markdown_cell(report.get('split_key', ''))}`; "
            "Source Balance Entropy: "
            f"{float(report.get('source_balance_entropy', 0.0)):.3f}; "
            "Source Imbalance Ratio: "
            f"{float(report.get('source_imbalance_ratio', 0.0)):.3f}; "
            f"Max Top-1 Gap: {float(report.get('max_top1_gap', 0.0)):.3f}; "
            f"Max MAP Gap: {float(report.get('max_map_gap', 0.0)):.3f}; "
            "Max Patch Success Gap: "
            f"{float(report.get('max_patch_success_gap', 0.0)):.3f}; "
            "Worst Holdout: "
            f"`{_markdown_cell(report.get('worst_holdout_group', ''))}`; "
            "Worst Gap Score: "
            f"{float(report.get('worst_holdout_gap_score', 0.0)):.3f}; "
            f"Stability Score: {float(report.get('stability_score', 0.0)):.3f}; "
            f"Risk: `{_markdown_cell(report.get('risk_level', ''))}`"
        ),
        "",
        "| Source Group | Cases | Bug Types | Rules | Top-1 | MAP | Patch Success | Search Efficiency | Avg Nodes | Avg Patch Risk |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    source_groups = report.get("source_groups", {})
    if not isinstance(source_groups, dict):
        source_groups = {}
    for group, metrics in source_groups.items():
        if not isinstance(metrics, dict):
            metrics = {}
        lines.append(
            "| "
            f"{_markdown_cell(group)} | "
            f"{int(metrics.get('case_count', 0))} | "
            f"{int(metrics.get('bug_type_count', 0))} | "
            f"{int(metrics.get('expected_rule_count', 0))} | "
            f"{float(metrics.get('top1', 0.0)):.3f} | "
            f"{float(metrics.get('map', 0.0)):.3f} | "
            f"{float(metrics.get('patch_success_rate', 0.0)):.3f} | "
            f"{float(metrics.get('search_efficiency', 0.0)):.3f} | "
            f"{float(metrics.get('average_evaluated_nodes', 0.0)):.3f} | "
            f"{float(metrics.get('average_patch_risk', 0.0)):.3f} |"
        )
    holdout_splits = report.get("holdout_splits", [])
    if isinstance(holdout_splits, list) and holdout_splits:
        lines.extend(
            [
                "",
                "| Holdout | Train Groups | Train Cases | Holdout Cases | Train Top-1 | Holdout Top-1 | Top-1 Gap | Train MAP | Holdout MAP | MAP Gap | Train Patch | Holdout Patch | Patch Gap |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for split in holdout_splits:
            if not isinstance(split, dict):
                continue
            train_metrics = split.get("train_metrics", {})
            holdout_metrics = split.get("holdout_metrics", {})
            if not isinstance(train_metrics, dict):
                train_metrics = {}
            if not isinstance(holdout_metrics, dict):
                holdout_metrics = {}
            train_groups = split.get("train_groups", [])
            if not isinstance(train_groups, list):
                train_groups = []
            lines.append(
                "| "
                f"{_markdown_cell(split.get('holdout_group', ''))} | "
                f"{_markdown_cell(', '.join(str(item) for item in train_groups))} | "
                f"{int(train_metrics.get('case_count', 0))} | "
                f"{int(holdout_metrics.get('case_count', 0))} | "
                f"{float(train_metrics.get('top1', 0.0)):.3f} | "
                f"{float(holdout_metrics.get('top1', 0.0)):.3f} | "
                f"{float(split.get('top1_gap', 0.0)):.3f} | "
                f"{float(train_metrics.get('map', 0.0)):.3f} | "
                f"{float(holdout_metrics.get('map', 0.0)):.3f} | "
                f"{float(split.get('map_gap', 0.0)):.3f} | "
                f"{float(train_metrics.get('patch_success_rate', 0.0)):.3f} | "
                f"{float(holdout_metrics.get('patch_success_rate', 0.0)):.3f} | "
                f"{float(split.get('patch_success_gap', 0.0)):.3f} |"
            )
    return lines


def _render_benchmark_provenance_audit(report: dict) -> list[str]:
    if not report or not report.get("case_count"):
        return []
    lines = [
        "",
        "## Benchmark Provenance Audit",
        "",
        (
            f"- Cases: {int(report.get('case_count', 0))}; "
            f"Source Groups: {int(report.get('source_group_count', 0))}; "
            f"Source Refs: {int(report.get('source_ref_count', 0))}; "
            "Case Provenance Coverage: "
            f"{float(report.get('case_provenance_coverage', 0.0)):.3f}; "
            "Source SHA256 Coverage: "
            f"{float(report.get('source_sha256_coverage', 0.0)):.3f}; "
            "Stable Ref Coverage: "
            f"{float(report.get('stable_ref_coverage', 0.0)):.3f}; "
            "License Coverage: "
            f"{float(report.get('license_coverage', 0.0)):.3f}; "
            "Mutation Coverage: "
            f"{float(report.get('materialized_mutation_coverage', 0.0)):.3f}; "
            "Duplicate Signatures: "
            f"{int(report.get('duplicate_signature_count', 0))}; "
            "Max Source File Share: "
            f"{float(report.get('max_source_file_case_share', 0.0)):.3f}; "
            "Leakage Risk Score: "
            f"{float(report.get('leakage_risk_score', 0.0)):.3f}; "
            f"Risk: `{_markdown_cell(report.get('risk_level', ''))}`"
        ),
    ]
    source_groups = report.get("source_groups", {})
    if isinstance(source_groups, dict) and source_groups:
        lines.extend(
            [
                "",
                "| Source Group | Cases |",
                "| --- | ---: |",
            ]
        )
        for group, count in sorted(source_groups.items()):
            lines.append(f"| {_markdown_cell(group)} | {int(count)} |")
    top_source_files = report.get("top_source_files", [])
    if isinstance(top_source_files, list) and top_source_files:
        lines.extend(
            [
                "",
                "| Source File | Cases | Example Cases |",
                "| --- | ---: | --- |",
            ]
        )
        for row in top_source_files[:10]:
            if not isinstance(row, dict):
                continue
            cases = row.get("cases", [])
            if not isinstance(cases, list):
                cases = []
            lines.append(
                "| "
                f"{_markdown_cell(row.get('source_file', ''))} | "
                f"{int(row.get('case_count', 0))} | "
                f"{_markdown_cell(', '.join(str(item) for item in cases[:5]))} |"
            )
    duplicate_signatures = report.get("duplicate_signatures", [])
    if isinstance(duplicate_signatures, list) and duplicate_signatures:
        lines.extend(
            [
                "",
                "| Duplicate Signature | Cases | Source | Ground Truth | Rules |",
                "| --- | ---: | --- | --- | --- |",
            ]
        )
        for row in duplicate_signatures[:10]:
            if not isinstance(row, dict):
                continue
            ground_truth = row.get("ground_truth", [])
            expected_rules = row.get("expected_rules", [])
            lines.append(
                "| "
                f"{_markdown_cell(row.get('signature_hash', ''))} | "
                f"{int(row.get('case_count', 0))} | "
                f"{_markdown_cell(row.get('source_group', ''))}:"
                f"{_markdown_cell(row.get('source_path', ''))} | "
                f"{_markdown_cell(', '.join(str(item) for item in ground_truth))} | "
                f"{_markdown_cell(', '.join(str(item) for item in expected_rules))} |"
            )
    return lines


def _render_localization_attribution(report: dict) -> list[str]:
    if not report or not report.get("case_count"):
        return []
    lines = [
        "",
        "## FinalScore Attribution",
        "",
        (
            f"- Cases: {int(report.get('case_count', 0))}; "
            f"Attributed Cases: {int(report.get('attributed_case_count', 0))}; "
            "Attribution Coverage: "
            f"{float(report.get('attribution_coverage', 0.0)):.3f}; "
            f"Mean Top-1 Margin: {float(report.get('mean_top1_margin', 0.0)):.3f}; "
            f"Min Top-1 Margin: {float(report.get('min_top1_margin', 0.0)):.3f}; "
            "Counterfactual Flip Rate: "
            f"{float(report.get('counterfactual_flip_rate', 0.0)):.3f}; "
            f"Fragile Top-1 Rate: {float(report.get('fragile_top1_rate', 0.0)):.3f}; "
            "Primary Component Entropy: "
            f"{float(report.get('primary_component_entropy', 0.0)):.3f}; "
            "Average Reconstruction Error: "
            f"{float(report.get('average_reconstruction_error', 0.0)):.3f}"
        ),
        "",
        "| Primary Component | Cases |",
        "| --- | ---: |",
    ]
    counts = report.get("primary_component_counts", {})
    if isinstance(counts, dict) and counts:
        for component, count in sorted(counts.items()):
            lines.append(f"| {_markdown_cell(component)} | {int(count)} |")
    else:
        lines.append("| none | 0 |")

    contributions = report.get("average_component_contributions", {})
    if isinstance(contributions, dict) and contributions:
        lines.extend(
            [
                "",
                "| Component | Average Contribution |",
                "| --- | ---: |",
            ]
        )
        for component, value in sorted(contributions.items()):
            lines.append(f"| {_markdown_cell(component)} | {float(value):.4f} |")

    rows = report.get("top_fragile_cases", [])
    if isinstance(rows, list) and rows:
        lines.extend(
            [
                "",
                "| Case | Top Function | Top-1 Hit | Margin | Primary Component | Contribution | Flip Components | Reconstruction Error |",
                "| --- | --- | --- | ---: | --- | ---: | --- | ---: |",
            ]
        )
        for row in rows[:10]:
            if not isinstance(row, dict):
                continue
            flip_components = row.get("counterfactual_flip_components", [])
            if not isinstance(flip_components, list):
                flip_components = []
            lines.append(
                "| "
                f"{_markdown_cell(row.get('case', ''))} | "
                f"{_markdown_cell(row.get('top_function', ''))} | "
                f"{row.get('top1_hit', False)} | "
                f"{float(row.get('top1_margin', 0.0)):.4f} | "
                f"{_markdown_cell(row.get('primary_component', ''))} | "
                f"{float(row.get('primary_component_contribution', 0.0)):.4f} | "
                f"{_markdown_cell(', '.join(str(item) for item in flip_components))} | "
                f"{float(row.get('reconstruction_error', 0.0)):.4f} |"
            )
    return lines


def _difficulty_metric_row(name: str, metrics: dict) -> str:
    return (
        "| "
        f"{_markdown_cell(name)} | "
        f"{int(metrics.get('case_count', 0))} | "
        f"{float(metrics.get('top1', 0.0)):.3f} | "
        f"{float(metrics.get('top3', 0.0)):.3f} | "
        f"{float(metrics.get('mrr', 0.0)):.3f} | "
        f"{float(metrics.get('map', 0.0)):.3f} | "
        f"{float(metrics.get('ndcg_at_3', 0.0)):.3f} | "
        f"{float(metrics.get('mean_exam_score', 0.0)):.3f} | "
        f"{float(metrics.get('patch_success_rate', 0.0)):.3f} | "
        f"{float(metrics.get('multi_patch_success_rate', 0.0)):.3f} | "
        f"{float(metrics.get('average_patch_candidates', 0.0)):.3f} | "
        f"{float(metrics.get('average_evaluated_nodes', 0.0)):.3f} | "
        f"{float(metrics.get('average_failures_before_success', 0.0)):.3f} | "
        f"{float(metrics.get('average_patch_risk', 0.0)):.3f} |"
    )


def _patch_judge_audit_summary(rows: list[PatchJudgeAuditRow]) -> str:
    count = len(rows)
    average_raw = sum(row.raw_score for row in rows) / count
    average_calibrated = sum(row.calibrated_score for row in rows) / count
    average_delta = sum(row.delta for row in rows) / count
    agreement_counts: dict[str, int] = {}
    for row in rows:
        agreement = row.agreement or "unknown"
        agreement_counts[agreement] = agreement_counts.get(agreement, 0) + 1
    agreements = ", ".join(
        f"{name}={value}" for name, value in sorted(agreement_counts.items())
    )
    return (
        f"- Judged Candidates: {count}; "
        f"Average Raw: {average_raw:.3f}; "
        f"Average Calibrated: {average_calibrated:.3f}; "
        f"Average Delta: {average_delta:.3f}; "
        f"Agreement: {agreements}"
    )


def _render_case_judge_reliability(
    reliability: CaseJudgeReliabilityReport,
) -> list[str]:
    if reliability.judged_case_count == 0:
        return []
    lines = [
        "",
        "## LLM Judge Reliability",
        "",
        (
            f"- Judged Cases: {reliability.judged_case_count}; "
            f"Positive Evidence Cases: {reliability.positive_case_count}; "
            f"Agreement Rate: {reliability.agreement_rate:.3f}; "
            f"Brier Score: {reliability.brier_score:.3f}; "
            "Expected Calibration Error: "
            f"{reliability.expected_calibration_error:.3f}; "
            f"Mean Absolute Error: {reliability.mean_absolute_error:.3f}; "
            f"Average Optimism Gap: {reliability.average_optimism_gap:.3f}"
        ),
        "",
        "| Case | Verdict | Judge Score | Evidence Label | Evidence Score | Brier | Abs Error | Optimism Gap | Agreement |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in reliability.rows:
        lines.append(
            "| "
            f"{_markdown_cell(row.case)} | "
            f"{_markdown_cell(row.verdict)} | "
            f"{row.judge_score:.3f} | "
            f"{row.evidence_label} | "
            f"{row.evidence_score:.3f} | "
            f"{row.brier:.3f} | "
            f"{row.absolute_error:.3f} | "
            f"{row.optimism_gap:.3f} | "
            f"{_markdown_cell(row.agreement)} |"
        )
    if reliability.bins:
        lines.extend(
            [
                "",
                "| Score Bin | Cases | Average Score | Evidence Accuracy | Calibration Gap |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for item in reliability.bins:
            lines.append(
                "| "
                f"[{item.lower:.2f}, {item.upper:.2f}) | "
                f"{item.count} | "
                f"{item.average_score:.3f} | "
                f"{item.accuracy:.3f} | "
                f"{item.gap:.3f} |"
            )
    return lines


def _render_patch_judge_reliability(
    reliability: PatchJudgeReliabilityReport,
) -> list[str]:
    if reliability.judged_candidate_count == 0:
        return []
    lines = [
        "",
        "## Patch Judge Reliability",
        "",
        (
            f"- Judged Candidates: {reliability.judged_candidate_count}; "
            f"Successful Candidates: {reliability.successful_candidate_count}; "
            f"Agreement Rate: {reliability.agreement_rate:.3f}; "
            f"Brier Score: {reliability.brier_score:.3f}; "
            "Expected Calibration Error: "
            f"{reliability.expected_calibration_error:.3f}; "
            f"Mean Absolute Error: {reliability.mean_absolute_error:.3f}; "
            f"Average Optimism Gap: {reliability.average_optimism_gap:.3f}"
        ),
        "",
        "| Case | Rank | Candidate | Success | Failure Type | Verdict | Raw | Calibrated | Evidence | Brier | Abs Error | Optimism Gap | Agreement |",
        "| --- | ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in reliability.rows:
        lines.append(
            "| "
            f"{_markdown_cell(row.case)} | "
            f"{row.rank} | "
            f"{_markdown_cell(row.candidate_id)} | "
            f"{row.success} | "
            f"{_markdown_cell(row.failure_type)} | "
            f"{_markdown_cell(row.verdict)} | "
            f"{row.raw_score:.3f} | "
            f"{row.calibrated_score:.3f} | "
            f"{row.evidence_score:.3f} | "
            f"{row.brier:.3f} | "
            f"{row.absolute_error:.3f} | "
            f"{row.optimism_gap:.3f} | "
            f"{_markdown_cell(row.agreement)} |"
        )
    if reliability.bins:
        lines.extend(
            [
                "",
                "| Score Bin | Candidates | Average Score | Success Rate | Calibration Gap |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for item in reliability.bins:
            lines.append(
                "| "
                f"[{item.lower:.2f}, {item.upper:.2f}) | "
                f"{item.count} | "
                f"{item.average_score:.3f} | "
                f"{item.success_rate:.3f} | "
                f"{item.gap:.3f} |"
            )
    return lines


def _render_localization_calibration(
    calibration: LocalizationCalibrationReport,
) -> list[str]:
    if calibration.case_count == 0:
        return []
    lines = [
        "",
        "## Localization Confidence Calibration",
        "",
        (
            f"- Cases: {calibration.case_count}; "
            f"Top-1 Accuracy: {calibration.top1_accuracy:.3f}; "
            f"Average Confidence: {calibration.average_confidence:.3f}; "
            f"Brier Score: {calibration.brier_score:.3f}; "
            "Expected Calibration Error: "
            f"{calibration.expected_calibration_error:.3f}; "
            "Calibrated Average Confidence: "
            f"{calibration.calibrated_average_confidence:.3f}; "
            "Calibrated Brier Score: "
            f"{calibration.calibrated_brier_score:.3f}; "
            "Calibrated Expected Calibration Error: "
            f"{calibration.calibrated_expected_calibration_error:.3f}; "
            f"Mean Absolute Error: {calibration.mean_absolute_error:.3f}; "
            f"Overconfidence Rate: {calibration.overconfidence_rate:.3f}; "
            f"Underconfidence Rate: {calibration.underconfidence_rate:.3f}"
        ),
        "",
        "| Case | Top Function | Confidence | Calibrated Confidence | Top-1 Hit | Brier | Calibrated Brier | Abs Error | Calibrated Abs Error | Gap | Calibrated Gap | Agreement |",
        "| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in calibration.rows:
        lines.append(
            "| "
            f"{_markdown_cell(row.case)} | "
            f"{_markdown_cell(row.top_function)} | "
            f"{row.confidence:.3f} | "
            f"{row.calibrated_confidence:.3f} | "
            f"{row.top1_hit} | "
            f"{row.brier:.3f} | "
            f"{row.calibrated_brier:.3f} | "
            f"{row.absolute_error:.3f} | "
            f"{row.calibrated_absolute_error:.3f} | "
            f"{row.calibration_gap:.3f} | "
            f"{row.calibrated_gap:.3f} | "
            f"{_markdown_cell(row.agreement)} |"
        )
    if calibration.bins:
        lines.extend(
            [
                "",
                "| Confidence Bin | Cases | Average Confidence | Top-1 Accuracy | Calibration Gap |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for item in calibration.bins:
            lines.append(
                "| "
                f"[{item.lower:.2f}, {item.upper:.2f}) | "
                f"{item.count} | "
                f"{item.average_confidence:.3f} | "
                f"{item.top1_accuracy:.3f} | "
                f"{item.gap:.3f} |"
            )
    if calibration.calibrated_bins:
        lines.extend(
            [
                "",
                "| Calibrated Confidence Bin | Cases | Average Calibrated Confidence | Top-1 Accuracy | Calibration Gap |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for item in calibration.calibrated_bins:
            lines.append(
                "| "
                f"[{item.lower:.2f}, {item.upper:.2f}) | "
                f"{item.count} | "
                f"{item.average_confidence:.3f} | "
                f"{item.top1_accuracy:.3f} | "
                f"{item.gap:.3f} |"
            )
    if calibration.stratified_groups:
        lines.extend(
            [
                "",
                "### Localization Calibration Stratification",
                "",
                "| Dimension | Group | Cases | Top-1 | Raw Brier | Calibrated Brier | Raw ECE | Calibrated ECE | Brier Improvement | ECE Improvement |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for item in calibration.stratified_groups:
            lines.append(
                "| "
                f"{_markdown_cell(item.dimension)} | "
                f"{_markdown_cell(item.group)} | "
                f"{item.case_count} | "
                f"{item.top1_accuracy:.3f} | "
                f"{item.brier_score:.3f} | "
                f"{item.calibrated_brier_score:.3f} | "
                f"{item.expected_calibration_error:.3f} | "
                f"{item.calibrated_expected_calibration_error:.3f} | "
                f"{item.brier_score_improvement:.3f} | "
                f"{item.expected_calibration_error_improvement:.3f} |"
            )
    if calibration.source_group_holdout_splits:
        lines.extend(
            [
                "",
                "### Source-Group Holdout Calibration",
                "",
                "| Holdout Group | Train Cases | Holdout Cases | Top-1 | Raw Brier | Holdout Calibrated Brier | Raw ECE | Holdout Calibrated ECE | Brier Improvement | ECE Improvement |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for item in calibration.source_group_holdout_splits:
            lines.append(
                "| "
                f"{_markdown_cell(item.holdout_group)} | "
                f"{item.train_case_count} | "
                f"{item.holdout_case_count} | "
                f"{item.holdout_top1_accuracy:.3f} | "
                f"{item.holdout_brier_score:.3f} | "
                f"{item.holdout_calibrated_brier_score:.3f} | "
                f"{item.holdout_expected_calibration_error:.3f} | "
                f"{item.holdout_calibrated_expected_calibration_error:.3f} | "
                f"{item.brier_score_improvement:.3f} | "
                f"{item.expected_calibration_error_improvement:.3f} |"
            )
    return lines


def _render_metric_uncertainty(
    uncertainty: BenchmarkMetricUncertaintyReport,
) -> list[str]:
    if uncertainty.case_count == 0:
        return []
    lines = [
        "",
        "## Metric Uncertainty",
        "",
        (
            f"- Cases: {uncertainty.case_count}; "
            f"Bootstrap Samples: {uncertainty.bootstrap_samples}; "
            f"Confidence Level: {uncertainty.confidence_level:.2f}; "
            f"Seed: {uncertainty.seed}"
        ),
        "",
        "| Metric | Mean | CI Lower | CI Upper | Width | Samples |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, interval in uncertainty.metrics.items():
        lines.append(
            "| "
            f"{_markdown_cell(name)} | "
            f"{interval.mean:.3f} | "
            f"{interval.lower:.3f} | "
            f"{interval.upper:.3f} | "
            f"{interval.width:.3f} | "
            f"{interval.sample_count} |"
        )
    return lines


def _render_search_budget_analysis(
    report: SearchBudgetAnalysisReport,
) -> list[str]:
    if report.evaluated_case_count == 0:
        return []
    lines = [
        "",
        "## Search Budget Analysis",
        "",
        (
            f"- Evaluated Cases: {report.evaluated_case_count}; "
            f"Successful Cases: {report.successful_case_count}; "
            f"Max Budget: {report.max_budget}; "
            f"Budget AUC: {report.budget_auc:.3f}; "
            f"First Success Rank p50/p90: "
            f"{report.first_success_rank_p50:.3f}/"
            f"{report.first_success_rank_p90:.3f}; "
            "Average Normalized Effort: "
            f"{report.average_normalized_effort:.3f}; "
            "Average Wasted Nodes After Success: "
            f"{report.average_wasted_nodes_after_success:.3f}; "
            "Deduped Candidates: "
            f"{report.total_deduplicated_candidates}; "
            "Dedupe-Affected Cases: "
            f"{report.dedupe_affected_case_count}; "
            "Average Duplicate Pressure: "
            f"{report.average_duplicate_pressure:.3f}"
        ),
        "",
        "| Budget | Success Rate | Success Count | Marginal Success | Cases |",
        "| ---: | ---: | ---: | ---: | ---: |",
    ]
    for point in report.budget_points:
        lines.append(
            "| "
            f"{point.budget} | "
            f"{point.success_rate:.3f} | "
            f"{point.success_count} | "
            f"{point.marginal_success_count} | "
            f"{point.case_count} |"
        )
    lines.extend(
        [
            "",
            "| Case | Evaluated Nodes | Deduped Candidates | Effective Pool | Duplicate Pressure | First Success Rank | Success | Normalized Effort | Wasted Nodes After Success |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
        ]
    )
    for row in report.rows:
        if row.evaluated_nodes <= 0:
            continue
        lines.append(
            "| "
            f"{_markdown_cell(row.case)} | "
            f"{row.evaluated_nodes} | "
            f"{row.deduplicated_candidates} | "
            f"{row.effective_candidate_pool} | "
            f"{row.duplicate_pressure:.3f} | "
            f"{_nullable_metric(row.first_success_rank)} | "
            f"{row.success} | "
            f"{row.normalized_effort:.3f} | "
            f"{row.wasted_nodes_after_success} |"
        )
    return lines


def _render_search_competition_analysis(
    report: SearchCompetitionAnalysisReport,
) -> list[str]:
    if report.beam_case_count == 0:
        return []
    payload = report.to_dict()
    lines = [
        "",
        "## Search Competition Analysis",
        "",
        (
            f"- Beam Cases: {report.beam_case_count}; "
            f"Multi-Candidate Cases: {report.multi_candidate_case_count}; "
            f"Successful Cases: {report.successful_case_count}; "
            f"Top-Rank Successes: {report.top_rank_success_count}; "
            f"Score Inversions: {report.score_inversion_count}; "
            f"Score Inversion Rate: {payload['score_inversion_rate']:.3f}; "
            f"Average Failure Pressure: {report.average_failure_pressure:.3f}; "
            f"Average Rule Diversity: {report.average_rule_diversity:.3f}; "
            "Multi-Candidate Rule Diversity: "
            f"{report.multi_candidate_average_rule_diversity:.3f}; "
            "Multi-Candidate Failure Diversity: "
            f"{report.multi_candidate_average_failure_type_diversity:.3f}; "
            "Multi-Candidate Bucket Diversity: "
            f"{report.multi_candidate_average_retention_bucket_diversity:.3f}; "
            "Diversity-Assisted Successes: "
            f"{report.diversity_assisted_success_count}; "
            "Average Diversity Lift: "
            f"{report.average_diversity_lift:.3f}; "
            "Average Success Diversity Lift: "
            f"{report.average_success_diversity_lift:.3f}; "
            "Average Success Diversity Bonus: "
            f"{report.average_success_diversity_bonus:.3f}; "
            "Budget-Sensitive Diversity Successes: "
            f"{report.budget_sensitive_diversity_success_count}; "
            "Projected Without-Diversity Success Delta: "
            f"{payload['projected_without_diversity_success_delta']:.3f}; "
            "Average Success Budget Gap Before Rerank: "
            f"{report.average_success_budget_gap_before_rerank:.3f}; "
            "Average Success Budget Margin After Rerank: "
            f"{report.average_success_budget_margin_after_rerank:.3f}"
        ),
        "",
        "| Top Failure Type | Count |",
        "| --- | ---: |",
    ]
    if report.top_failure_type_counts:
        for failure_type, count in report.top_failure_type_counts.items():
            lines.append(f"| {_markdown_cell(failure_type)} | {count} |")
    else:
        lines.append("| none | 0 |")
    lines.extend(
        [
            "",
            "| Case | Nodes | Failed | Successes | First Success | Top Success | Score Inversion | Rule Diversity | Failure Types | Buckets | Failure Pressure | Max Diversity Lift | Success Diversity Lift | Success Diversity Bonus | Budget-Sensitive | Base Rank | Rerank Rank | Budget Gap | Budget Margin | Counterfactual |",
            "| --- | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in report.rows:
        if row.evaluated_nodes <= 0:
            continue
        lines.append(
            "| "
            f"{_markdown_cell(row.case)} | "
            f"{row.evaluated_nodes} | "
            f"{row.failed_nodes} | "
            f"{row.successful_nodes} | "
            f"{_nullable_metric(row.first_success_rank)} | "
            f"{row.top_rank_success} | "
            f"{row.score_inversion} | "
            f"{row.rule_diversity} | "
            f"{row.failure_type_diversity} | "
            f"{row.retention_bucket_diversity} | "
            f"{row.failure_pressure:.3f} | "
            f"{row.max_diversity_lift} | "
            f"{row.success_diversity_lift} | "
            f"{row.success_diversity_bonus:.3f} | "
            f"{row.budget_sensitive_diversity_success} | "
            f"{row.success_base_rank} | "
            f"{row.success_diversity_rank} | "
            f"{row.success_budget_gap_before_rerank} | "
            f"{row.success_budget_margin_after_rerank} | "
            f"{_markdown_cell(row.counterfactual_condition)} |"
        )
    return lines


def _compact_evidence(evidence) -> str:
    if not isinstance(evidence, dict):
        return ""
    keys = [
        "lens",
        "localization_score",
        "rule_confidence",
        "failed_covered",
        "passed_covered",
        "proximity",
        "caller_impact",
        "candidate_count",
        "min_patch_risk",
    ]
    parts = []
    for key in keys:
        if key in evidence:
            parts.append(f"{key}={evidence[key]}")
    call_chain = evidence.get("call_chain")
    if call_chain:
        parts.append(f"call_chain={' -> '.join(call_chain)}")
    return "; ".join(parts)
