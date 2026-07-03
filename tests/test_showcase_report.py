import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.showcase_report import (
    build_showcase_report,
    render_resume_showcase_markdown,
    render_showcase_markdown,
)


def test_showcase_report_summarizes_algorithm_evidence():
    report = build_showcase_report(_suite_payload())
    markdown = render_showcase_markdown(report)
    resume_markdown = render_resume_showcase_markdown(report)
    phases = {row["phase"]: row for row in report["phase_readiness"]}

    assert report["artifact_kind"] == "experiment_suite"
    assert report["readiness_score"] == 100.0
    assert report["headline"]["case_count"] == 62
    assert phases["Phase 1"]["status"] == "ready"
    assert phases["Phase 2"]["status"] == "ready"
    assert phases["Phase 3"]["status"] == "ready"
    assert phases["Phase 4"]["status"] == "ready"
    trace_summary = report["case_evidence_trace_summary"]
    assert trace_summary["traced_cases"] == 1
    assert trace_summary["top1_hits"] == 1
    assert trace_summary["patch_successes"] == 1
    assert trace_summary["score_inversions"] == 1
    assert trace_summary["data_flow_evidence_cases"] == 1
    trace = report["case_evidence_traces"][0]
    assert trace["case"] == "case_0"
    assert trace["top_localization"]["function"] == "case_0.target"
    assert trace["top_localization"]["signals"]["static"] == 1.0
    assert trace["top_localization"]["graph_evidence"]["cross_function_edges"] == 1
    assert trace["top_localization"]["graph_evidence"]["key_flow_edges"] == 1
    assert trace["top_localization"]["graph_evidence"]["slice_edges"] == 12
    assert trace["top_localization"]["graph_evidence"]["slice_support"] == 0.88
    assert trace["top_localization"]["graph_evidence"]["slice_grounded"] is True
    assert trace["search_trace"]["first_success_rank"] == 2
    assert trace["search_trace"]["score_inversion"] is True
    assert trace["patch_trace"]["strategy"] == "beam_search"
    assert trace["best_candidate"]["variant"] == "insert_len_zero_guard"
    milestones = {
        (row["phase"], row["milestone"]): row
        for row in report["phase_milestone_audit"]
    }
    assert (
        milestones[("Phase 1", "Repo parser and AST analyzer")]["status"]
        == "ready"
    )
    assert (
        milestones[
            ("Phase 2", "Static/Graph/FinalScore suspicious ranking")
        ]["status"]
        == "ready"
    )
    assert (
        milestones[
            ("Phase 3", "Patch generation and sandbox pytest validation")
        ]["status"]
        == "ready"
    )
    assert (
        milestones[
            ("Phase 4", "Hard-case generation and generated benchmark")
        ]["status"]
        == "ready"
    )
    assert (
        report["algorithm_evidence"]["static_graph_reasoning"][
            "cross_function_data_flow_cases"
        ]
        == 22
    )
    assert (
        report["algorithm_evidence"]["static_graph_reasoning"][
            "program_slice_cases"
        ]
        == 62
    )
    assert (
        report["algorithm_evidence"]["static_graph_reasoning"][
            "slice_grounded_cases"
        ]
        == 61
    )
    assert (
        report["algorithm_evidence"]["static_graph_reasoning"][
            "average_top1_slice_support"
        ]
        == 0.91
    )
    assert (
        report["algorithm_evidence"]["weight_and_ablation_search"][
            "best_patch_score_top1_success"
        ]
        == 0.95
    )
    assert (
        report["algorithm_evidence"]["search_and_repair"]["search_budget_auc"]
        == 0.88
    )
    assert (
        report["algorithm_evidence"]["search_and_repair"]["search_success_at_1"]
        == 0.76
    )
    assert (
        report["algorithm_evidence"]["search_and_repair"][
            "search_dedupe_affected_cases"
        ]
        == 5
    )
    assert (
        report["algorithm_evidence"]["search_and_repair"][
            "search_total_deduplicated_candidates"
        ]
        == 11
    )
    assert (
        report["algorithm_evidence"]["search_and_repair"][
            "search_average_duplicate_pressure"
        ]
        == 0.07
    )
    assert (
        report["algorithm_evidence"]["search_and_repair"][
            "search_competition_cases"
        ]
        == 9
    )
    assert (
        report["algorithm_evidence"]["search_and_repair"][
            "search_score_inversion_rate"
        ]
        == 0.14
    )
    assert (
        report["algorithm_evidence"]["search_and_repair"][
            "search_diversity_assisted_successes"
        ]
        == 4
    )
    assert (
        report["algorithm_evidence"]["search_and_repair"][
            "search_average_success_diversity_lift"
        ]
        == 2.25
    )
    assert (
        report["algorithm_evidence"]["search_and_repair"]["reflection_cases"]
        == 2
    )
    assert (
        report["algorithm_evidence"]["search_and_repair"][
            "reflection_candidates"
        ]
        == 3
    )
    assert (
        report["algorithm_evidence"]["search_and_repair"][
            "reflection_candidate_success_rate"
        ]
        == 0.3333
    )
    assert (
        report["algorithm_evidence"]["search_and_repair"][
            "generated_reflection_candidates"
        ]
        == 1
    )
    assert (
        report["algorithm_evidence"]["search_and_repair"][
            "generated_reflection_success_cases"
        ]
        == 1
    )
    assert (
        report["algorithm_evidence"]["search_and_repair"][
            "combined_reflection_candidates"
        ]
        == 4
    )
    assert (
        report["algorithm_evidence"]["search_and_repair"][
            "combined_reflection_candidate_success_rate"
        ]
        == 0.5
    )
    phase3_reflection = next(
        row
        for row in report["phase_milestone_audit"]
        if row["phase"] == "Phase 3"
        and row["milestone"] == "Patch search and reflection loop"
    )
    assert "reflection_candidates=3" in phase3_reflection["evidence"]
    assert "generated_reflection_candidates=1" in phase3_reflection["evidence"]
    assert (
        "generated_reflection_success_cases=1"
        in phase3_reflection["evidence"]
    )
    diversity = report["algorithm_evidence"]["search_diversity_reranking"]
    assert diversity["status"] == "validated"
    assert diversity["variant"] == "without_diversity_reranking"
    assert diversity["delta_patch_success"] == -0.2
    assert diversity["delta_beam_success"] == -0.1
    assert diversity["diversity_assisted_successes"] == 4
    assert diversity["average_success_diversity_lift"] == 2.25
    assert diversity["combined_proof"] is True
    assert diversity["proof_source"] == "main_ablation_and_search_competition"
    assert diversity["generated_counterfactual_proof"] is True
    assert diversity["generated_budget_sensitive_successes"] == 1
    assert diversity["generated_projected_patch_success_delta"] == -1.0
    assert "search_total_deduplicated_candidates" in markdown
    assert "deduped candidates=11" in resume_markdown
    assert "duplicate pressure=0.070" in resume_markdown
    assert "diversity-assisted successes=4" in resume_markdown
    assert "Validated diversity-aware patch reranking" in resume_markdown
    assert "proof_source=main_ablation_and_search_competition" in resume_markdown
    robust = report["algorithm_evidence"]["robustness_and_generalization"]
    assert robust["source_group_count"] == 4
    assert robust["source_balance_entropy"] == 0.88
    assert robust["source_imbalance_ratio"] == 3.1
    assert robust["worst_holdout_group"] == "repo/b"
    assert robust["worst_holdout_gap_score"] == 0.08
    assert robust["stability_score"] == 0.92
    assert robust["risk_level"] == "low"
    provenance = report["algorithm_evidence"]["benchmark_provenance"]
    assert provenance["case_provenance_coverage"] == 1.0
    assert provenance["source_sha256_coverage"] == 1.0
    assert provenance["stable_ref_coverage"] == 1.0
    assert provenance["license_coverage"] == 1.0
    assert provenance["duplicate_signature_count"] == 0
    assert provenance["leakage_risk_score"] == 0.02
    assert "provenance_stable_ref_coverage" in resume_markdown
    assert "stable_ref_coverage=1.0000" in markdown
    attribution = report["algorithm_evidence"]["localization_attribution"]
    assert attribution["attribution_coverage"] == 1.0
    assert attribution["mean_top1_margin"] == 0.22
    assert attribution["fragile_top1_rate"] == 0.08
    calibration = report["algorithm_evidence"]["confidence_calibration"]
    assert calibration["localization_case_count"] == 62
    assert calibration["localization_top1_accuracy"] == 0.97
    assert calibration["localization_average_confidence"] == 0.88
    assert calibration["localization_brier_score"] == 0.09
    assert calibration["localization_expected_calibration_error"] == 0.08
    assert calibration["localization_calibration_model"] == (
        "leave_one_out_beta_binning"
    )
    assert calibration["localization_calibrated_average_confidence"] == 0.96
    assert calibration["localization_calibrated_brier_score"] == 0.02
    assert (
        calibration["localization_calibrated_expected_calibration_error"]
        == 0.03
    )
    assert calibration["localization_brier_score_improvement"] == 0.07
    assert (
        calibration["localization_expected_calibration_error_improvement"]
        == 0.05
    )
    assert calibration["localization_bin_count"] == 2
    assert calibration["localization_calibrated_bin_count"] == 1
    assert calibration["localization_stratified_group_count"] == 3
    assert calibration["localization_source_group_calibration_count"] == 1
    assert calibration["localization_bug_type_calibration_count"] == 1
    assert calibration["localization_expected_rule_calibration_count"] == 1
    assert calibration["localization_max_group_calibrated_ece"] == 0.04
    assert calibration["localization_worst_calibrated_group"] == (
        "expected_rule:missing_len_zero_guard"
    )
    assert calibration["localization_source_holdout_split_count"] == 2
    assert calibration["localization_min_holdout_train_cases"] == 31
    assert calibration["localization_max_holdout_raw_ece"] == 0.1
    assert calibration["localization_max_holdout_calibrated_ece"] == 0.05
    assert calibration["localization_average_holdout_calibrated_ece"] == 0.04
    assert calibration["localization_worst_holdout_group"] == "repo/b"
    assert calibration["localization_worst_holdout_group_cases"] == 31
    assert (
        report["algorithm_evidence"]["weight_and_ablation_search"][
            "patch_score_pareto_profiles"
        ]
        == 1
    )
    assert (
        report["algorithm_evidence"]["weight_and_ablation_search"][
            "best_patch_score_pareto_optimal"
        ]
        is True
    )
    assert (
        report["algorithm_evidence"]["weight_and_ablation_search"][
            "best_final_score_robust_score"
        ]
        == 0.97
    )
    assert (
        report["algorithm_evidence"]["weight_and_ablation_search"][
            "final_score_source_groups"
        ]
        == 4
    )
    assert (
        report["algorithm_evidence"]["weight_and_ablation_search"][
            "final_score_source_group_rows"
        ]
        == 2
    )
    assert (
        report["algorithm_evidence"]["weight_and_ablation_search"][
            "final_score_holdout_splits"
        ]
        == 2
    )
    assert (
        report["algorithm_evidence"]["weight_and_ablation_search"][
            "final_score_pareto_profiles"
        ]
        == 1
    )
    assert (
        report["algorithm_evidence"]["weight_and_ablation_search"][
            "best_final_score_pareto_optimal"
        ]
        is True
    )
    assert (
        report["algorithm_evidence"]["weight_and_ablation_search"][
            "best_final_score_dominates_count"
        ]
        == 5
    )
    assert (
        report["algorithm_evidence"]["weight_and_ablation_search"][
            "patch_judge_fusion_status"
        ]
        == "improved"
    )
    assert (
        report["algorithm_evidence"]["weight_and_ablation_search"][
            "patch_judge_fusion_profiles"
        ]
        == 1
    )
    assert (
        report["algorithm_evidence"]["weight_and_ablation_search"][
            "patch_judge_fusion_top1_delta"
        ]
        == 0.04
    )
    assert (
        report["algorithm_evidence"]["weight_and_ablation_search"][
            "calibration_ablation_variant_count"
        ]
        == 5
    )
    assert (
        report["algorithm_evidence"]["weight_and_ablation_search"][
            "calibration_ablation_regression_count"
        ]
        == 2
    )
    assert (
        report["algorithm_evidence"]["weight_and_ablation_search"][
            "max_calibrated_ece_regression"
        ]
        == 0.04
    )
    assert (
        report["algorithm_evidence"]["weight_and_ablation_search"][
            "strongest_calibration_variant"
        ]
        == "without_graph_bundle_search"
    )
    assert (
        report["algorithm_evidence"]["judge_reliability"][
            "patch_judged_candidate_count"
        ]
        == 7
    )
    assert (
        report["algorithm_evidence"]["judge_reliability"][
            "patch_judge_brier_score"
        ]
        == 0.11
    )
    assert (
        report["algorithm_evidence"]["benchmark_expansion"][
            "judge_mining_judged_candidates"
        ]
        == 7
    )
    assert (
        report["algorithm_evidence"]["benchmark_expansion"][
            "judge_mining_failure_clusters"
        ]
        == 2
    )
    assert (
        report["algorithm_evidence"]["benchmark_expansion"][
            "judge_mining_template_seeds"
        ]
        == 2
    )
    expansion = report["algorithm_evidence"]["benchmark_expansion"]
    assert expansion["generated_hard_cases"] == 5
    assert expansion["generated_benchmark_cases"] == 5
    assert expansion["generated_benchmark_patch_success_rate"] == 1.0
    assert expansion["generated_benchmark_multi_candidate_cases"] == 4
    assert expansion["generated_benchmark_score_inversions"] == 2
    assert expansion["generated_benchmark_score_inversion_rate"] == 0.6667
    assert expansion["generated_benchmark_failure_pressure"] == 0.44
    assert expansion["generated_benchmark_dedupe_affected_cases"] == 1
    assert expansion["generated_benchmark_deduplicated_candidates"] == 3
    assert expansion["generated_benchmark_duplicate_pressure"] == 0.12
    assert expansion["generated_benchmark_expected_deduplication_cases"] == 1
    assert expansion["generated_benchmark_deduplication_evidence_cases"] == 1
    assert expansion["generated_benchmark_diversity_assisted_successes"] == 1
    assert expansion["generated_benchmark_average_success_diversity_lift"] == 3.0
    assert expansion["generated_benchmark_average_success_diversity_bonus"] == 0.9
    assert expansion["generated_benchmark_expected_reflection_depth_cases"] == 1
    assert expansion["generated_benchmark_reflection_success_cases"] == 1
    assert expansion["generated_benchmark_reflection_candidates"] == 1
    assert expansion["generated_benchmark_successful_reflection_candidates"] == 1
    assert expansion["generated_benchmark_reflection_candidate_success_rate"] == 1.0
    assert expansion["generated_provenance_selected_cases"] == 1
    assert expansion["generated_average_provenance_bonus"] == 1.9
    assert expansion["generated_average_provenance_stable_ref"] == 1.0
    generated_diversity = report["algorithm_evidence"][
        "generated_search_diversity_reranking"
    ]
    assert generated_diversity["status"] == "validated"
    assert generated_diversity["variant"] == "without_diversity_reranking"
    assert generated_diversity["expected_cases"] == 1
    assert generated_diversity["budget_sensitive_successes"] == 1
    assert generated_diversity["projected_patch_success_delta"] == -1.0
    assert generated_diversity["projected_beam_success_delta"] == -1.0
    assert generated_diversity["average_success_base_rank"] == 5.0
    assert generated_diversity["average_success_diversity_rank"] == 2.0
    generated_deduplication = report["algorithm_evidence"][
        "generated_candidate_deduplication"
    ]
    assert generated_deduplication["status"] == "validated"
    assert generated_deduplication["expected_cases"] == 1
    assert generated_deduplication["evidence_cases"] == 1
    assert generated_deduplication["dedupe_affected_cases"] == 1
    assert generated_deduplication["total_deduplicated_candidates"] == 3
    assert generated_deduplication["average_duplicate_pressure"] == 0.12
    assert generated_deduplication["budget_pressure_proof"] is True
    assert "generated_deduplicated_candidates" in resume_markdown
    assert "generated_deduplicated_candidates=3" in resume_markdown
    assert "Validated generated candidate deduplication pressure" in resume_markdown
    assert "generated_reflection_success_cases" in resume_markdown
    assert "generated_reflection_success_cases=1" in resume_markdown
    assert "generated_reflection_candidate_success_rate" in resume_markdown
    assert "expected_reflection_depth_cases" in markdown
    assert "Reflection Pressure" in markdown
    breakdown = report["generated_hard_case_breakdown"]
    by_signal = {row["signal"]: row for row in breakdown["by_signal"]}
    by_rule = {row["rule"]: row for row in breakdown["by_rule"]}
    assert breakdown["case_count"] == 5
    assert breakdown["signal_count"] == 5
    assert breakdown["rule_count"] == 2
    assert by_signal["search_score_inversion"]["score_inversion_count"] == 1
    assert by_signal["search_diversity_reranking"]["score_inversion_count"] == 0
    assert by_signal["candidate_deduplication_pressure"]["patch_success_rate"] == 1.0
    assert by_signal["reflection_depth"]["patch_success_rate"] == 1.0
    assert by_signal["reflection_depth"]["score_inversion_count"] == 0
    assert by_signal["without_static_rules"]["rules"] == [
        "missing_len_zero_guard"
    ]
    assert by_rule["possible_index_overrun"]["signals"] == [
        "candidate_deduplication_pressure",
        "reflection_depth",
        "search_diversity_reranking",
        "search_score_inversion",
    ]
    assert by_rule["missing_len_zero_guard"]["patch_success_rate"] == 1.0
    generated_trace_summary = report["generated_hard_case_evidence_summary"]
    assert generated_trace_summary["generated_cases"] == 5
    assert generated_trace_summary["target_signals"] == 5
    assert generated_trace_summary["score_inversions"] == 3
    assert generated_trace_summary["expected_diversity_reranking_cases"] == 1
    assert generated_trace_summary["diversity_assisted_successes"] == 1
    assert generated_trace_summary["average_success_diversity_lift"] == 3.0
    assert generated_trace_summary["average_success_diversity_bonus"] == 0.9
    assert generated_trace_summary["expected_reflection_depth_cases"] == 1
    assert generated_trace_summary["reflection_success_cases"] == 1
    assert generated_trace_summary["reflection_candidates"] == 1
    assert generated_trace_summary["successful_reflection_candidates"] == 1
    assert generated_trace_summary["reflection_candidate_success_rate"] == 1.0
    assert generated_trace_summary["average_success_reflection_depth"] == 1.0
    assert generated_trace_summary["candidate_competition_cases"] == 5
    assert generated_trace_summary["expected_candidate_deduplication_cases"] == 1
    assert generated_trace_summary["candidate_deduplication_evidence_cases"] == 1
    assert generated_trace_summary["deduplicated_candidates"] == 3
    assert generated_trace_summary["max_deduplicated_candidates"] == 3
    assert (
        generated_trace_summary["average_candidate_deduplication_pressure"]
        == 0.6
    )
    assert generated_trace_summary["provenance_selected_cases"] == 1
    assert generated_trace_summary["average_provenance_bonus"] == 1.9
    assert generated_trace_summary["average_provenance_stable_ref"] == 1.0
    assert generated_trace_summary["average_provenance_license"] == 1.0
    assert generated_trace_summary["average_provenance_leakage_risk"] == 0.0
    generated_traces = {
        row["case"]: row
        for row in report["generated_hard_case_evidence_traces"]
    }
    score_probe = generated_traces["generated_score_inversion_probe"]
    assert score_probe["target_signals"] == ["search_score_inversion"]
    assert score_probe["generation_strategy"] == (
        "search_competition_score_inversion_probe"
    )
    assert score_probe["selection_score"] == 38.5
    assert score_probe["selection_provenance"]["bonus"] == 1.9
    assert score_probe["selection_provenance"]["source_sha256"] == 1.0
    assert score_probe["selection_provenance"]["stable_ref"] == 1.0
    assert score_probe["search_pressure"]["score_inversion"] is True
    assert score_probe["search_pressure"]["failure_pressure"] == 0.5
    assert score_probe["decoy_variant"] == "overly_conservative_range_bound"
    assert score_probe["success_variant"] == "shrink_range_upper_bound"
    diversity_probe = generated_traces["generated_diversity_reranking_probe"]
    assert diversity_probe["target_signals"] == ["search_diversity_reranking"]
    assert diversity_probe["generation_strategy"] == (
        "search_competition_diversity_reranking_probe"
    )
    assert diversity_probe["search_pressure"]["expected_diversity_reranking"] is True
    assert diversity_probe["search_pressure"]["score_inversion"] is False
    dedupe_probe = generated_traces["generated_candidate_deduplication_probe"]
    assert dedupe_probe["target_signals"] == ["candidate_deduplication_pressure"]
    assert dedupe_probe["generation_strategy"] == (
        "search_budget_candidate_deduplication_probe"
    )
    assert (
        dedupe_probe["search_pressure"]["expected_candidate_deduplication"]
        is True
    )
    assert dedupe_probe["search_pressure"]["deduplicated_candidates"] == 3
    assert dedupe_probe["search_pressure"]["effective_candidate_pool"] == 5
    assert dedupe_probe["search_pressure"]["duplicate_pressure"] == 0.6
    assert dedupe_probe["search_pressure"]["max_search_duplicate_count"] == 3
    reflection_probe = generated_traces["generated_reflection_depth_probe"]
    assert reflection_probe["target_signals"] == ["reflection_depth"]
    assert reflection_probe["generation_strategy"] == "reflection_depth_probe"
    assert reflection_probe["reflection_pressure"]["expected_reflection_depth"] is True
    assert reflection_probe["reflection_pressure"]["reflection_candidate_count"] == 1
    assert (
        reflection_probe["reflection_pressure"][
            "successful_reflection_candidate_count"
        ]
        == 1
    )
    assert reflection_probe["reflection_pressure"]["reflection_success"] is True
    assert reflection_probe["reflection_pressure"]["max_reflection_depth"] == 1
    assert (
        reflection_probe["reflection_pressure"]["first_success_reflection_depth"]
        == 1
    )
    link_summary = report["generated_hard_case_ablation_link_summary"]
    assert link_summary["linked_cases"] == 5
    assert link_summary["regression_linked_cases"] == 5
    assert link_summary["direct_variant_links"] == 1
    assert link_summary["component_proxy_links"] == 3
    assert link_summary["generated_counterfactual_links"] == 1
    link_by_signal = {row["signal"]: row for row in link_summary["by_signal"]}
    link_by_component = {
        row["component"]: row for row in link_summary["by_component"]
    }
    assert link_by_signal["without_static_rules"]["components"] == [
        "static_rule_reasoning"
    ]
    assert link_by_signal["search_diversity_reranking"][
        "generated_counterfactual_links"
    ] == 1
    assert link_by_signal["candidate_deduplication_pressure"][
        "linked_cases"
    ] == 1
    assert link_by_signal["candidate_deduplication_pressure"]["variants"] == [
        "without_candidate_deduplication"
    ]
    assert link_by_component["search_and_repair"]["linked_cases"] == 4
    assert link_by_component["search_and_repair"]["regression_linked_cases"] == 4
    assert "without_beam_search" in link_by_component["search_and_repair"][
        "variants"
    ]
    assert link_by_component["static_rule_reasoning"]["linked_cases"] == 1
    ablation_links = {
        row["case"]: row
        for row in report["generated_hard_case_ablation_links"]
    }
    assert ablation_links["generated_missing_len_probe"]["link_type"] == (
        "direct_variant"
    )
    assert ablation_links["generated_missing_len_probe"]["variant"] == (
        "without_static_rules"
    )
    assert ablation_links["generated_score_inversion_probe"]["link_type"] == (
        "component_proxy"
    )
    assert ablation_links["generated_score_inversion_probe"]["variant"] == (
        "without_beam_search"
    )
    assert ablation_links["generated_diversity_reranking_probe"]["variant"] == (
        "without_diversity_reranking"
    )
    assert ablation_links["generated_diversity_reranking_probe"]["link_type"] == (
        "generated_counterfactual"
    )
    assert ablation_links["generated_diversity_reranking_probe"]["direction"] == (
        "regression"
    )
    assert ablation_links["generated_diversity_reranking_probe"][
        "main_delta_metric"
    ] == "projected_patch_success_delta"
    assert ablation_links["generated_diversity_reranking_probe"][
        "main_delta"
    ] == -1.0
    assert ablation_links["generated_diversity_reranking_probe"][
        "success_base_rank"
    ] == 5
    assert ablation_links["generated_diversity_reranking_probe"][
        "counterfactual_condition"
    ] == "base_rank_outside_budget_and_reranked_inside_budget"
    assert ablation_links["generated_candidate_deduplication_probe"][
        "variant"
    ] == "without_candidate_deduplication"
    assert ablation_links["generated_candidate_deduplication_probe"][
        "link_type"
    ] == "component_proxy"
    assert ablation_links["generated_candidate_deduplication_probe"][
        "deduplicated_candidates"
    ] == 3
    assert ablation_links["generated_candidate_deduplication_probe"][
        "duplicate_pressure"
    ] == 0.6
    assert ablation_links["generated_reflection_depth_probe"]["variant"] == (
        "without_beam_search"
    )
    assert ablation_links["generated_reflection_depth_probe"]["link_type"] == (
        "component_proxy"
    )
    assert ablation_links["generated_reflection_depth_probe"]["component"] == (
        "search_and_repair"
    )
    components = {
        row["component"]: row for row in report["ablation_component_summary"]
    }
    assert components["program_graph_reasoning"]["strongest_variant"] == (
        "without_graph_bundle_search"
    )
    assert components["program_graph_reasoning"]["strongest_delta_metric"] == (
        "delta_patch_success"
    )
    assert components["static_rule_reasoning"]["regression_count"] == 1
    assert components["static_rule_reasoning"]["strongest_delta_metric"] == (
        "delta_rule_recall"
    )
    assert report["ablation_highlights"][0]["variant"] == (
        "without_graph_bundle_search"
    )
    assert report["ablation_highlights"][0]["main_delta_metric"] == (
        "delta_patch_success"
    )
    assert report["quality_summary"]["status"] == "pass"
    assert report["gaps"] == []
    assert "graph-guided code intelligence agent" in report["resume_bullets"][0]
    assert any(
        "generated_score_inversions=2" in item
        for item in report["resume_bullets"]
    )
    assert any(
        "generated_diversity_assisted_successes=1" in item
        and "generated_avg_diversity_lift=3.0000" in item
        and "generated_avg_diversity_bonus=0.9000" in item
        and "generated_budget_sensitive_successes=1" in item
        and "projected_without_diversity_delta=-1.0000" in item
        and "generated_deduplicated_candidates=3" in item
        and "generated_duplicate_pressure=0.1200" in item
        for item in report["resume_bullets"]
    )
    assert any(
        "Validated generated candidate deduplication pressure" in item
        and "deduplicated_candidates=3" in item
        and "duplicate_pressure=0.1200" in item
        for item in report["resume_bullets"]
    )
    assert "# Algorithm Showcase Report" in markdown
    assert "## Phase Readiness" in markdown
    assert "## Phase Milestone Audit" in markdown
    assert "## Case-Level Evidence Trace" in markdown
    assert "### Confidence Calibration" in markdown
    assert "localization_brier_score" in markdown
    assert "localization_calibrated_brier_score" in markdown
    assert "localization_stratified_group_count" in markdown
    assert "localization_source_holdout_split_count" in markdown
    assert "## Generated Hard-Case Breakdown" in markdown
    assert "## Generated Hard-Case Evidence Trace" in markdown
    assert "diversity_assisted_successes" in markdown
    assert "average_success_diversity_lift" in markdown
    assert "average_provenance_stable_ref" in markdown
    assert "provenance=bonus=1.9000" in markdown
    assert "## Generated Hard-Case Ablation Links" in markdown
    assert "### Generated Ablation Links By Signal" in markdown
    assert "### Generated Ablation Links By Component" in markdown
    assert "## Ablation Component Summary" in markdown
    assert "static_rule_reasoning" in markdown
    assert "search_score_inversion" in markdown
    assert "missing_len_zero_guard" in markdown
    assert "without_graph_bundle_search" in markdown
    assert "calibration_ablation_variant_count" in markdown
    assert "# Code Intelligence Agent Resume Showcase" in resume_markdown
    assert "## Representative Case Traces" in resume_markdown
    assert "## Confidence Calibration" in resume_markdown
    assert "localization_expected_calibration_error" in resume_markdown
    assert "localization_calibrated_expected_calibration_error" in (
        resume_markdown
    )
    assert "generated_diversity_assisted_successes" in resume_markdown
    assert "## Ablation-Linked Hard Cases" in resume_markdown
    assert "| search_and_repair | 4 | 4 |" in resume_markdown
    assert "| static_rule_reasoning | 1 | 1 |" in resume_markdown
    assert "generated_provenance_selected_cases" in resume_markdown
    assert "provenance_selected=1" in resume_markdown
    assert "benchmark_cases" in resume_markdown
    assert "slice_grounded_cases" in resume_markdown
    assert "average_top1_slice_support" in resume_markdown
    assert "source_balance_entropy" in resume_markdown
    assert "program_slice_cases" in resume_markdown
    assert "generalization_stability_score" in resume_markdown
    assert "provenance_case_coverage" in resume_markdown
    assert "provenance_leakage_risk_score" in resume_markdown
    assert "attribution_mean_top1_margin" in resume_markdown
    assert "FinalScore decisions" in resume_markdown
    assert "Benchmark Provenance" in markdown
    assert "generated_score_inversions" in resume_markdown


def test_showcase_report_uses_generated_reflection_when_main_reflection_is_empty():
    payload = json.loads(json.dumps(_suite_payload()))
    payload["benchmark_report"]["summary"]["reflection_analysis"] = {
        "case_count": 62,
        "reflection_case_count": 0,
        "reflection_success_case_count": 0,
        "reflection_candidate_count": 0,
        "retained_reflection_candidate_count": 0,
        "successful_reflection_candidate_count": 0,
        "reflection_candidate_success_rate": 0.0,
        "average_success_reflection_depth": 0.0,
        "rows": [],
    }

    report = build_showcase_report(payload)
    search = report["algorithm_evidence"]["search_and_repair"]
    phase3_reflection = next(
        row
        for row in report["phase_milestone_audit"]
        if row["phase"] == "Phase 3"
        and row["milestone"] == "Patch search and reflection loop"
    )

    assert phase3_reflection["status"] == "ready"
    assert "reflection_candidates=0" in phase3_reflection["evidence"]
    assert "generated_reflection_candidates=1" in phase3_reflection["evidence"]
    assert (
        "generated_reflection_candidate_success_rate=1.0000"
        in phase3_reflection["evidence"]
    )
    assert search["reflection_candidates"] == 0
    assert search["generated_reflection_candidates"] == 1
    assert search["generated_reflection_success_cases"] == 1
    assert search["combined_reflection_candidates"] == 1
    assert search["combined_reflection_candidate_success_rate"] == 1.0


def test_showcase_report_validates_diversity_with_generated_counterfactual():
    payload = json.loads(json.dumps(_suite_payload()))
    payload["benchmark_report"]["summary"]["search_competition_analysis"] = {
        "beam_case_count": 62,
        "multi_candidate_case_count": 9,
        "score_inversion_rate": 0.14,
        "average_failure_pressure": 0.19,
        "diversity_lift_case_rate": 0.0,
        "diversity_assisted_success_rate": 0.0,
        "diversity_assisted_success_count": 0,
        "average_success_diversity_lift": 0.0,
        "average_success_diversity_bonus": 0.0,
    }
    for row in payload["ablation_impact"]["rows"]:
        if row.get("variant") == "without_diversity_reranking":
            row["direction"] = "neutral"
            row["impact_score"] = 0.0
            row["delta_patch_success"] = 0.0
            row["delta_beam_success"] = 0.0
            row["delta_multi_patch_success"] = 0.0

    report = build_showcase_report(payload)
    resume_markdown = render_resume_showcase_markdown(report)
    diversity = report["algorithm_evidence"]["search_diversity_reranking"]

    assert diversity["status"] == "validated"
    assert diversity["variant"] == "without_diversity_reranking"
    assert diversity["ablation_direction"] == "neutral"
    assert diversity["diversity_assisted_successes"] == 0
    assert diversity["average_success_diversity_lift"] == 0.0
    assert diversity["generated_counterfactual_proof"] is True
    assert diversity["combined_proof"] is True
    assert diversity["proof_source"] == "generated_budget_counterfactual"
    assert diversity["generated_budget_sensitive_successes"] == 1
    assert diversity["generated_projected_patch_success_delta"] == -1.0
    assert diversity["generated_projected_beam_success_delta"] == -1.0
    assert diversity["generated_average_success_diversity_lift"] == 3.0
    assert "proof_source=generated_budget_counterfactual" in resume_markdown
    assert "generated_budget_sensitive_successes=1" in resume_markdown
    assert "generated_projected_patch_delta=-1.0000" in resume_markdown


def test_showcase_report_identifies_missing_evaluation_layers():
    payload = {
        "summary": {
            "case_count": 1,
            "top1": 1.0,
            "top3": 1.0,
            "expected_rule_recall": 1.0,
            "expected_rule_precision": 1.0,
            "patch_success_rate": 1.0,
        },
        "cases": [{"case_name": "toy"}],
    }

    report = build_showcase_report(payload)

    assert report["artifact_kind"] == "benchmark_report"
    assert report["readiness_score"] == 75.0
    assert "Quality gate artifact is missing." in report["gaps"]
    assert "Ablation results are missing or skipped." in report["gaps"]
    assert "Generalization report has fewer than 3 source groups." in report["gaps"]


def test_showcase_report_links_slice_and_margin_generated_signals():
    payload = {
        "benchmark_report": {
            "summary": {"case_count": 2},
            "cases": [],
        },
        "ablation_results": [{"variant": "full"}],
        "ablation_impact": {
            "rows": [
                {
                    "variant": "without_data_dependency",
                    "direction": "regression",
                    "impact_score": -0.31,
                    "delta_map": -0.20,
                },
                {
                    "variant": "without_semantic_similarity",
                    "direction": "regression",
                    "impact_score": -0.42,
                    "delta_top1": -0.10,
                },
                {
                    "variant": "without_test_signals",
                    "direction": "neutral",
                    "impact_score": -0.12,
                    "delta_calibrated_ece_improvement": -0.02,
                },
            ]
        },
        "hard_case_generated_benchmark": {
            "benchmark_report": {
                "summary": {"case_count": 2, "patch_success_rate": 1.0},
                "cases": [
                    {
                        "case_name": "generated_weak_slice_probe",
                        "metadata": {
                            "hard_case_target_signal": "weak_slice_grounding",
                            "hard_case_target_signals": [
                                "weak_slice_grounding"
                            ],
                            "hard_case_generation_strategy": (
                                "slice_grounding_cross_file_composition"
                            ),
                        },
                        "patch_success": True,
                        "localization_details": [
                            {
                                "rank": 1,
                                "function_name": "pkg.service.target",
                                "score": 0.71,
                                "signals": {"graph": 0.5, "static": 1.0},
                                "graph_components": {
                                    "data_dependency": 0.2,
                                    "control_flow": 0.1,
                                },
                                "program_slice": {"edge_count": 3},
                                "slice_grounding": {
                                    "support_score": 0.22,
                                    "grounded": False,
                                },
                                "call_chain_length": 2,
                            }
                        ],
                    },
                    {
                        "case_name": "generated_fragile_margin_probe",
                        "metadata": {
                            "hard_case_target_signal": "fragile_top1_margin",
                            "hard_case_target_signals": [
                                "fragile_top1_margin"
                            ],
                            "hard_case_generation_strategy": (
                                "fragile_localization_catalog_selection"
                            ),
                        },
                        "patch_success": True,
                        "localization_details": [
                            {
                                "rank": 1,
                                "function_name": "pkg.rank.target",
                                "score": 0.62,
                                "signals": {
                                    "graph": 0.35,
                                    "semantic": 0.30,
                                },
                                "program_slice": {"edge_count": 5},
                                "slice_grounding": {
                                    "support_score": 0.95,
                                    "grounded": True,
                                },
                            }
                        ],
                    },
                    {
                        "case_name": "generated_key_flow_probe",
                        "metadata": {
                            "hard_case_target_signal": "subscript_key_flow",
                            "hard_case_target_signals": [
                                "subscript_key_flow"
                            ],
                            "hard_case_generation_strategy": (
                                "subscript_key_flow_catalog_selection"
                            ),
                        },
                        "patch_success": True,
                        "localization_details": [
                            {
                                "rank": 1,
                                "function_name": "pkg.lookup.score_for",
                                "score": 0.75,
                                "signals": {"graph": 0.45, "static": 1.0},
                                "graph_components": {
                                    "data_dependency": 0.4,
                                },
                                "data_flow_evidence": {
                                    "internal_edges": 0,
                                    "key_flow_edges": 1,
                                    "cross_function_edges": 0,
                                    "total_edges": 1,
                                },
                                "program_slice": {"edge_count": 4},
                                "slice_grounding": {
                                    "support_score": 0.9,
                                    "grounded": True,
                                },
                            }
                        ],
                    },
                ],
            }
        },
    }

    report = build_showcase_report(payload)
    markdown = render_showcase_markdown(report)
    trace_by_case = {
        row["case"]: row
        for row in report["generated_hard_case_evidence_traces"]
    }
    links = {
        row["case"]: row
        for row in report["generated_hard_case_ablation_links"]
    }

    summary = report["generated_hard_case_evidence_summary"]
    assert summary["generated_cases"] == 3
    assert summary["target_signals"] == 3
    assert summary["slice_grounding_pressure_cases"] == 1
    assert summary["average_slice_grounding_pressure"] == 0.26
    assert summary["slice_grounding_target_cases"] == 1
    assert summary["slice_grounding_evidence_cases"] == 0
    assert summary["slice_grounding_evidence_rate"] == 0.0
    assert summary["average_slice_grounding_target_pressure"] == 0.78
    slice_pressure = trace_by_case["generated_weak_slice_probe"][
        "slice_grounding_pressure"
    ]
    assert slice_pressure["weak_slice_target"] is True
    assert slice_pressure["pressure"] == 0.78
    assert slice_pressure["slice_grounded"] is False
    assert slice_pressure["execution_evidence"] is False
    assert links["generated_weak_slice_probe"]["variant"] == (
        "without_data_dependency"
    )
    assert links["generated_weak_slice_probe"]["component"] == (
        "program_graph_reasoning"
    )
    assert "slice-grounded evidence stability" in (
        links["generated_weak_slice_probe"]["rationale"]
    )
    assert links["generated_fragile_margin_probe"]["variant"] == (
        "without_semantic_similarity"
    )
    assert links["generated_fragile_margin_probe"]["component"] == (
        "semantic_llm_scoring"
    )
    assert "ranking margin" in (
        links["generated_fragile_margin_probe"]["rationale"]
    )
    assert links["generated_key_flow_probe"]["variant"] == (
        "without_data_dependency"
    )
    assert links["generated_key_flow_probe"]["component"] == (
        "program_graph_reasoning"
    )
    assert "key-to-mapping access evidence" in (
        links["generated_key_flow_probe"]["rationale"]
    )
    link_summary = report["generated_hard_case_ablation_link_summary"]
    assert link_summary["linked_cases"] == 3
    assert link_summary["component_proxy_links"] == 3
    assert "program_graph_reasoning" in link_summary["linked_components"]
    assert "semantic_llm_scoring" in link_summary["linked_components"]
    assert "average_slice_grounding_pressure" in markdown
    assert "weak_slice_grounding" in markdown
    assert "fragile_top1_margin" in markdown
    assert "subscript_key_flow" in markdown


def test_showcase_report_splits_slice_pressure_from_execution_evidence():
    payload = {
        "benchmark_report": {
            "summary": {"case_count": 0},
            "cases": [],
        },
        "hard_case_generated_benchmark": {
            "benchmark_report": {
                "summary": {"case_count": 2, "patch_success_rate": 1.0},
                "cases": [
                    {
                        "case_name": "generated_weak_reachability_probe",
                        "metadata": {
                            "hard_case_target_signal": (
                                "weak_failed_test_reachability"
                            ),
                            "hard_case_target_signals": [
                                "weak_failed_test_reachability"
                            ],
                        },
                        "patch_success": True,
                        "localization_details": [
                            {
                                "rank": 1,
                                "function_name": "pkg.reach.weak",
                                "score": 0.72,
                                "signals": {"graph": 0.5, "static": 1.0},
                                "program_slice": {"edge_count": 4},
                                "slice_grounding": {
                                    "support_score": 1.0,
                                    "failed_test_reachability": 0.2,
                                    "call_chain_edge_coverage": 1.0,
                                    "grounded": True,
                                },
                            }
                        ],
                    },
                    {
                        "case_name": "generated_strong_reachability_probe",
                        "metadata": {
                            "hard_case_target_signal": (
                                "weak_failed_test_reachability"
                            ),
                            "hard_case_target_signals": [
                                "weak_failed_test_reachability"
                            ],
                        },
                        "patch_success": True,
                        "localization_details": [
                            {
                                "rank": 1,
                                "function_name": "pkg.reach.strong",
                                "score": 0.82,
                                "signals": {"graph": 0.7, "static": 1.0},
                                "program_slice": {"edge_count": 9},
                                "slice_grounding": {
                                    "support_score": 0.95,
                                    "failed_test_reachability": 0.9,
                                    "call_chain_edge_coverage": 0.8,
                                    "grounded": True,
                                },
                            }
                        ],
                    },
                ],
            }
        },
    }

    report = build_showcase_report(payload)
    summary = report["generated_hard_case_evidence_summary"]
    traces = {
        row["case"]: row
        for row in report["generated_hard_case_evidence_traces"]
    }
    expansion = report["algorithm_evidence"]["benchmark_expansion"]

    assert summary["slice_grounding_target_cases"] == 2
    assert summary["slice_grounding_evidence_cases"] == 1
    assert summary["slice_grounding_evidence_rate"] == 0.5
    assert summary["slice_grounding_pressure_cases"] == 1
    assert summary["average_slice_grounding_target_pressure"] == 0.45
    weak_pressure = traces["generated_weak_reachability_probe"][
        "slice_grounding_pressure"
    ]
    strong_pressure = traces["generated_strong_reachability_probe"][
        "slice_grounding_pressure"
    ]
    assert weak_pressure["pressure"] == 0.8
    assert weak_pressure["failed_test_reachability"] == 0.2
    assert weak_pressure["execution_evidence"] is False
    assert strong_pressure["pressure"] == 0.1
    assert strong_pressure["execution_evidence"] is True
    assert expansion["generated_benchmark_slice_grounding_target_cases"] == 2
    assert expansion["generated_benchmark_slice_grounding_evidence_cases"] == 1
    assert expansion["generated_benchmark_slice_grounding_evidence_rate"] == 0.5


def test_showcase_report_preserves_direct_ablation_and_probe_target_signals():
    payload = {
        "benchmark_report": {
            "summary": {"case_count": 1},
            "cases": [],
        },
        "ablation_results": [{"variant": "full"}],
        "ablation_impact": {
            "rows": [
                {
                    "variant": "without_reflection",
                    "direction": "regression",
                    "impact_score": -0.24,
                    "delta_patch_success": -1.0,
                },
                {
                    "variant": "without_beam_search",
                    "direction": "regression",
                    "impact_score": -0.12,
                    "delta_beam_success": -1.0,
                },
            ]
        },
        "hard_case_generated_benchmark": {
            "benchmark_report": {
                "summary": {"case_count": 1, "patch_success_rate": 1.0},
                "cases": [
                    {
                        "case_name": "generated_direct_reflection_probe",
                        "metadata": {
                            "hard_case_target_signal": "without_reflection",
                            "hard_case_target_signals": ["without_reflection"],
                            "target_benchmark_signals": ["reflection_depth"],
                            "hard_case_generation_strategy": (
                                "ablation_reflection_depth_probe"
                            ),
                            "expected_reflection_depth": True,
                            "patch_score_profile": "reflection_depth_probe",
                        },
                        "patch_success": True,
                        "beam_search_results": [
                            {
                                "candidate_id": "seed",
                                "rule_id": "possible_index_overrun",
                                "variant": "overly_conservative_range_bound",
                                "success": False,
                                "depth": 0,
                            },
                            {
                                "candidate_id": "refined",
                                "parent_id": "seed",
                                "rule_id": "possible_index_overrun",
                                "variant": (
                                    "reflection_shrink_range_upper_bound"
                                ),
                                "success": True,
                                "depth": 1,
                            },
                        ],
                    }
                ],
            }
        },
    }

    report = build_showcase_report(payload)
    markdown = render_showcase_markdown(report)
    trace = report["generated_hard_case_evidence_traces"][0]
    link = report["generated_hard_case_ablation_links"][0]

    assert trace["target_signals"] == ["reflection_depth", "without_reflection"]
    assert trace["reflection_pressure"]["expected_reflection_depth"] is True
    assert trace["reflection_pressure"]["first_success_reflection_depth"] == 1
    assert link["case"] == "generated_direct_reflection_probe"
    assert link["target_signal"] == "without_reflection"
    assert link["variant"] == "without_reflection"
    assert link["link_type"] == "direct_variant"
    assert link["component"] == "search_and_repair"
    assert "reflection_depth" in markdown
    assert "without_reflection" in markdown


def test_showcase_report_links_rule_precision_filter_generated_case():
    payload = {
        "benchmark_report": {
            "summary": {"case_count": 1},
            "cases": [],
        },
        "ablation_impact": {
            "rows": [
                {
                    "variant": "without_rule_precision_filter",
                    "direction": "regression",
                    "impact_score": -0.09,
                    "delta_rule_precision": -0.75,
                    "delta_rule_recall": 0.0,
                },
            ]
        },
        "hard_case_generated_benchmark": {
            "benchmark_report": {
                "summary": {"case_count": 1, "patch_success_rate": 1.0},
                "cases": [
                    {
                        "case_name": "generated_rule_precision_filter_pressure",
                        "metadata": {
                            "hard_case_target_signal": (
                                "without_rule_precision_filter"
                            ),
                            "hard_case_target_signals": [
                                "without_rule_precision_filter"
                            ],
                            "target_benchmark_signals": [
                                "without_rule_precision_filter"
                            ],
                            "hard_case_generation_strategy": (
                                "ablation_rule_precision_filter_pressure_synthetic"
                            ),
                            "expected_rule_precision_pressure": True,
                            "expected_filtered_false_positive_rules": [
                                "missing_len_zero_guard",
                                "stringified_numeric_value",
                                "inplace_api_return_value",
                            ],
                        },
                        "expected_rule_ids": ["possible_index_overrun"],
                        "patch_success": True,
                        "beam_search_results": [
                            {
                                "rule_id": "possible_index_overrun",
                                "variant": "shrink_range_upper_bound",
                                "success": True,
                            },
                        ],
                    }
                ],
            }
        },
    }

    report = build_showcase_report(payload)
    markdown = render_showcase_markdown(report)
    resume_markdown = render_resume_showcase_markdown(report)
    link = report["generated_hard_case_ablation_links"][0]
    link_summary = report["generated_hard_case_ablation_link_summary"]
    by_signal = {row["signal"]: row for row in link_summary["by_signal"]}
    by_component = {
        row["component"]: row for row in link_summary["by_component"]
    }

    assert link["case"] == "generated_rule_precision_filter_pressure"
    assert link["target_signal"] == "without_rule_precision_filter"
    assert link["variant"] == "without_rule_precision_filter"
    assert link["link_type"] == "direct_variant"
    assert link["component"] == "static_rule_reasoning"
    assert link["main_delta_metric"] == "delta_rule_precision"
    assert link["main_delta"] == -0.75
    assert by_signal["without_rule_precision_filter"]["strongest_delta_metric"] == (
        "delta_rule_precision"
    )
    assert by_signal["without_rule_precision_filter"]["components"] == [
        "static_rule_reasoning"
    ]
    assert by_component["static_rule_reasoning"]["target_signals"] == [
        "without_rule_precision_filter"
    ]
    assert "main_delta=delta_rule_precision:-0.7500" in markdown
    assert "### Generated Ablation Links By Signal" in markdown
    assert "### Generated Ablation Links By Component" in markdown
    assert "without_rule_precision_filter" in markdown
    assert "without_rule_precision_filter" in resume_markdown
    assert "delta_rule_precision=-0.7500" in resume_markdown


def test_showcase_report_prefers_multi_patch_bundle_trace():
    payload = _suite_payload()
    case = payload["benchmark_report"]["cases"][0]
    case["multi_patch_success"] = True
    case["repair_strategy"] = "multi_patch"
    case["best_patch_rule_id"] = (
        "missing_len_zero_guard+possible_index_overrun"
    )
    case["multi_patch_results"] = [
        {
            "rank": 1,
            "rules": [
                "missing_len_zero_guard",
                "possible_index_overrun",
            ],
            "functions": ["case_0.target", "case_0.helper"],
            "variants": [
                "insert_len_zero_guard",
                "shrink_range_upper_bound",
            ],
            "bundle_size": 2,
            "score": 0.97,
            "success": True,
            "passed": 2,
            "failed": 0,
            "cross_file": True,
            "data_flow_edges": 3,
            "key_flow_edges": 1,
        }
    ]

    report = build_showcase_report(payload)
    trace = report["case_evidence_traces"][0]

    assert trace["best_candidate"]["kind"] == "multi_patch_bundle"
    assert trace["best_candidate"]["bundle_size"] == 2
    assert trace["best_candidate"]["success"] is True
    assert trace["best_candidate"]["key_flow_edges"] == 1
    assert trace["best_candidate"]["variant"] == (
        "insert_len_zero_guard+shrink_range_upper_bound"
    )


def test_showcase_report_cli_writes_json_and_markdown():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        artifact = root / "suite.json"
        output_json = root / "showcase.json"
        output_markdown = root / "showcase.md"
        output_resume_markdown = root / "resume_showcase.md"
        artifact.write_text(json.dumps(_suite_payload()), encoding="utf-8")

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.showcase_report",
                str(artifact),
                "--format",
                "json",
                "--output-json",
                str(output_json),
                "--output-markdown",
                str(output_markdown),
                "--output-resume-markdown",
                str(output_resume_markdown),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        payload = json.loads(completed.stdout)

        assert completed.returncode == 0
        assert payload["artifact_kind"] == "experiment_suite"
        assert json.loads(output_json.read_text(encoding="utf-8"))[
            "readiness_score"
        ] == 100.0
        assert "# Algorithm Showcase Report" in output_markdown.read_text(
            encoding="utf-8"
        )
        assert "# Code Intelligence Agent Resume Showcase" in (
            output_resume_markdown.read_text(encoding="utf-8")
        )


def _suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 62,
                "top1": 1.0,
                "top3": 1.0,
                "mrr": 1.0,
                "map": 1.0,
                "ndcg_at_3": 1.0,
                "expected_rule_recall": 1.0,
                "expected_rule_precision": 1.0,
                "patch_success_rate": 1.0,
                "multi_patch_success_rate": 0.9,
                "beam_success_rate": 0.95,
                "patch_search_top1_success_rate": 0.94,
                "patch_search_mrr": 0.94,
                "search_efficiency": 0.91,
                "average_evaluated_nodes": 2.5,
                "data_flow_evidence_case_count": 62,
                "cross_function_data_flow_case_count": 22,
                "average_top1_data_dependency": 0.48,
                "program_slice_case_count": 62,
                "average_top1_slice_edges": 14.2,
                "average_top1_slice_cross_function_edges": 1.7,
                "slice_grounded_case_count": 61,
                "average_top1_slice_support": 0.91,
                "average_top1_slice_failed_test_reachability": 0.98,
                "average_top1_slice_call_chain_coverage": 0.97,
                "difficulty_report": {
                    "case_count": 62,
                    "bucket_counts": {"easy": 40, "medium": 17, "hard": 5},
                },
                "generalization_report": {
                    "case_count": 62,
                    "source_group_count": 4,
                    "source_balance_entropy": 0.88,
                    "source_imbalance_ratio": 3.1,
                    "max_top1_gap": 0.05,
                    "max_patch_success_gap": 0.10,
                    "worst_holdout_group": "repo/b",
                    "worst_holdout_gap_score": 0.08,
                    "stability_score": 0.92,
                    "risk_level": "low",
                },
                "benchmark_provenance_audit": {
                    "case_count": 62,
                    "source_group_count": 4,
                    "source_ref_count": 62,
                    "source_sha256_present_count": 62,
                    "stable_ref_count": 62,
                    "floating_ref_count": 0,
                    "case_provenance_coverage": 1.0,
                    "source_sha256_coverage": 1.0,
                    "stable_ref_coverage": 1.0,
                    "license_coverage": 1.0,
                    "materialized_mutation_coverage": 1.0,
                    "duplicate_case_name_count": 0,
                    "duplicate_signature_count": 0,
                    "duplicate_signature_case_count": 0,
                    "max_source_group_case_share": 0.5,
                    "max_source_file_case_share": 0.5,
                    "leakage_risk_score": 0.02,
                    "risk_level": "low",
                },
                "localization_attribution": {
                    "case_count": 62,
                    "attributed_case_count": 62,
                    "attribution_coverage": 1.0,
                    "mean_top1_margin": 0.22,
                    "min_top1_margin": 0.03,
                    "fragile_top1_case_count": 5,
                    "fragile_top1_rate": 0.08,
                    "counterfactual_flip_case_count": 4,
                    "counterfactual_flip_rate": 0.06,
                    "primary_component_entropy": 0.74,
                    "average_reconstruction_error": 0.02,
                },
                "localization_calibration": {
                    "calibration_model": "leave_one_out_beta_binning",
                    "case_count": 62,
                    "top1_positive_count": 60,
                    "top1_accuracy": 0.97,
                    "average_confidence": 0.88,
                    "brier_score": 0.09,
                    "expected_calibration_error": 0.08,
                    "calibrated_average_confidence": 0.96,
                    "calibrated_brier_score": 0.02,
                    "calibrated_expected_calibration_error": 0.03,
                    "brier_score_improvement": 0.07,
                    "expected_calibration_error_improvement": 0.05,
                    "maximum_calibration_error": 0.12,
                    "mean_absolute_error": 0.11,
                    "overconfidence_rate": 0.02,
                    "underconfidence_rate": 0.03,
                    "agreement_counts": {
                        "aligned": 59,
                        "overconfident": 1,
                        "underconfident": 2,
                    },
                    "bins": [
                        {
                            "lower": 0.7,
                            "upper": 0.8,
                            "count": 10,
                            "average_confidence": 0.76,
                            "top1_accuracy": 0.8,
                            "gap": 0.04,
                        },
                        {
                            "lower": 0.8,
                            "upper": 0.9,
                            "count": 52,
                            "average_confidence": 0.9,
                            "top1_accuracy": 1.0,
                            "gap": 0.1,
                        },
                    ],
                    "calibrated_bins": [
                        {
                            "lower": 0.9,
                            "upper": 1.0,
                            "count": 62,
                            "average_confidence": 0.96,
                            "top1_accuracy": 0.97,
                            "gap": 0.01,
                        },
                    ],
                    "stratified_groups": [
                        {
                            "dimension": "source_group",
                            "group": "repo/a",
                            "case_count": 62,
                            "top1_accuracy": 0.97,
                            "average_confidence": 0.88,
                            "brier_score": 0.09,
                            "expected_calibration_error": 0.08,
                            "calibrated_average_confidence": 0.96,
                            "calibrated_brier_score": 0.02,
                            "calibrated_expected_calibration_error": 0.03,
                            "brier_score_improvement": 0.07,
                            "expected_calibration_error_improvement": 0.05,
                        },
                        {
                            "dimension": "bug_type",
                            "group": "empty_input_guard",
                            "case_count": 62,
                            "top1_accuracy": 0.97,
                            "average_confidence": 0.88,
                            "brier_score": 0.09,
                            "expected_calibration_error": 0.08,
                            "calibrated_average_confidence": 0.95,
                            "calibrated_brier_score": 0.03,
                            "calibrated_expected_calibration_error": 0.02,
                            "brier_score_improvement": 0.06,
                            "expected_calibration_error_improvement": 0.06,
                        },
                        {
                            "dimension": "expected_rule",
                            "group": "missing_len_zero_guard",
                            "case_count": 62,
                            "top1_accuracy": 0.97,
                            "average_confidence": 0.88,
                            "brier_score": 0.09,
                            "expected_calibration_error": 0.08,
                            "calibrated_average_confidence": 0.94,
                            "calibrated_brier_score": 0.04,
                            "calibrated_expected_calibration_error": 0.04,
                            "brier_score_improvement": 0.05,
                            "expected_calibration_error_improvement": 0.04,
                        },
                    ],
                    "source_group_holdout_splits": [
                        {
                            "holdout_group": "repo/a",
                            "train_case_count": 31,
                            "holdout_case_count": 31,
                            "holdout_top1_accuracy": 0.97,
                            "holdout_average_confidence": 0.88,
                            "holdout_brier_score": 0.09,
                            "holdout_expected_calibration_error": 0.08,
                            "holdout_calibrated_average_confidence": 0.95,
                            "holdout_calibrated_brier_score": 0.03,
                            "holdout_calibrated_expected_calibration_error": 0.03,
                            "brier_score_improvement": 0.06,
                            "expected_calibration_error_improvement": 0.05,
                        },
                        {
                            "holdout_group": "repo/b",
                            "train_case_count": 31,
                            "holdout_case_count": 31,
                            "holdout_top1_accuracy": 0.97,
                            "holdout_average_confidence": 0.86,
                            "holdout_brier_score": 0.11,
                            "holdout_expected_calibration_error": 0.10,
                            "holdout_calibrated_average_confidence": 0.94,
                            "holdout_calibrated_brier_score": 0.04,
                            "holdout_calibrated_expected_calibration_error": 0.05,
                            "brier_score_improvement": 0.07,
                            "expected_calibration_error_improvement": 0.05,
                        },
                    ],
                },
                "patch_judge_reliability": {
                    "judged_candidate_count": 7,
                    "brier_score": 0.11,
                    "expected_calibration_error": 0.12,
                    "agreement_rate": 0.86,
                },
                "search_budget_analysis": {
                    "evaluated_case_count": 62,
                    "budget_auc": 0.88,
                    "success_at_budget": {"1": 0.76, "3": 0.94},
                    "average_normalized_effort": 0.42,
                    "dedupe_affected_case_count": 5,
                    "total_deduplicated_candidates": 11,
                    "max_deduplicated_candidates": 4,
                    "average_deduplicated_candidates": 0.18,
                    "average_duplicate_pressure": 0.07,
                },
                "search_competition_analysis": {
                    "beam_case_count": 62,
                    "multi_candidate_case_count": 9,
                    "score_inversion_rate": 0.14,
                    "average_failure_pressure": 0.19,
                    "diversity_lift_case_rate": 0.11,
                    "diversity_assisted_success_rate": 0.08,
                    "diversity_assisted_success_count": 4,
                    "average_success_diversity_lift": 2.25,
                    "average_success_diversity_bonus": 0.42,
                },
                "reflection_analysis": {
                    "case_count": 62,
                    "reflection_case_count": 2,
                    "reflection_success_case_count": 1,
                    "reflection_candidate_count": 3,
                    "retained_reflection_candidate_count": 2,
                    "successful_reflection_candidate_count": 1,
                    "reflection_case_success_rate": 0.5,
                    "reflection_candidate_success_rate": 0.3333,
                    "average_reflection_depth": 1.5,
                    "average_success_reflection_depth": 1.0,
                    "average_score_delta_from_parent": 0.24,
                    "parent_failure_type_counts": {"test_failure": 2},
                    "success_parent_failure_type_counts": {"test_failure": 1},
                    "rows": [],
                },
            },
            "cases": [
                {
                    "case_name": "case_0",
                    "metadata": {
                        "upstream": "repo/a",
                        "bug_type": "empty_input_guard",
                    },
                    "expected_rule_ids": ["missing_len_zero_guard"],
                    "ground_truth": ["case_0.target"],
                    "top1_hit": True,
                    "top3_hit": True,
                    "patch_success": True,
                    "multi_patch_success": False,
                    "patch_candidates_count": 2,
                    "repair_strategy": "beam_search",
                    "best_patch_rule_id": "missing_len_zero_guard",
                    "repair_rounds": 1,
                    "best_patch_risk": {
                        "score": 0.21,
                        "diff_size": 2,
                        "affected_callers": 1,
                        "cross_file_callers": 0,
                        "risk_reasons": ["diff_size=2"],
                    },
                    "search_analysis": {
                        "evaluated_nodes": 2,
                        "successful_nodes": 1,
                        "first_success_rank": 2,
                        "first_success_depth": 1,
                        "failures_before_success": 1,
                        "success_score_margin": 0.25,
                        "efficiency": 0.5,
                    },
                    "localization_details": [
                        {
                            "rank": 1,
                            "function": "case_0.target",
                            "function_name": "case_0.target",
                            "score": 0.91,
                            "signals": {
                                "sbfl": 1.0,
                                "static": 1.0,
                                "graph": 0.82,
                                "semantic": 0.35,
                                "llm": 0.0,
                                "patch_risk": 0.21,
                            },
                            "graph_components": {
                                "traceback_hit": 1.0,
                                "test_coverage": 1.0,
                                "data_dependency": 0.4,
                                "control_flow": 0.5,
                                "pagerank": 0.2,
                                "patch_risk": 0.21,
                            },
                            "data_flow_evidence": {
                                "total_edges": 3,
                                "key_flow_edges": 1,
                                "cross_function_edges": 1,
                            },
                            "program_slice": {
                                "edge_count": 12,
                                "data_flow_edge_count": 3,
                                "cross_function_data_flow_edge_count": 1,
                                "cfg_edge_count": 4,
                            },
                            "slice_grounding": {
                                "support_score": 0.88,
                                "grounded": True,
                                "failed_test_reachability": 1.0,
                                "call_chain_edge_coverage": 1.0,
                                "support_reasons": [
                                    "failed_test_support",
                                    "call_chain_supported",
                                ],
                            },
                            "call_chain": ["test_case_0", "case_0.target"],
                            "call_chain_length": 1,
                        },
                    ],
                    "beam_search_results": [
                        {
                            "rank": 1,
                            "variant": "return_none_on_empty",
                            "rule_id": "missing_len_zero_guard",
                            "depth": 0,
                            "prior_score": 0.8,
                            "score": 0.74,
                            "feedback_score": 0.2,
                            "success": False,
                            "risk_score": 0.18,
                            "passed": 0,
                            "failed": 1,
                            "failure_type": "test_failure",
                            "retained": True,
                            "retention_bucket": "recoverable_failure",
                        },
                        {
                            "rank": 2,
                            "variant": "insert_len_zero_guard",
                            "rule_id": "missing_len_zero_guard",
                            "depth": 1,
                            "prior_score": 0.6,
                            "score": 0.99,
                            "feedback_score": 1.0,
                            "success": True,
                            "risk_score": 0.21,
                            "passed": 1,
                            "failed": 0,
                            "failure_type": "success",
                            "retained": True,
                            "retention_bucket": "success",
                        },
                    ],
                }
            ],
        },
        "ablation_results": [{"variant": "full"}, {"variant": "without_graph"}],
        "ablation_impact": {
            "baseline_variant": "full",
            "impacted_variant_count": 1,
            "rows": [
                {
                    "variant": "without_graph_bundle_search",
                    "direction": "regression",
                    "impact_score": -0.42,
                    "delta_top1": 0.0,
                    "delta_map": -0.2,
                    "delta_patch_success": -1.0,
                    "delta_multi_patch_success": -1.0,
                    "delta_beam_success": -0.5,
                    "delta_calibrated_ece_improvement": -0.04,
                    "delta_calibrated_brier_improvement": -0.02,
                },
                {
                    "variant": "without_static_rules",
                    "direction": "regression",
                    "impact_score": -0.20,
                    "delta_top1": 0.0,
                    "delta_map": 0.0,
                    "delta_rule_recall": -1.0,
                    "delta_rule_precision": -1.0,
                    "delta_calibrated_ece_improvement": -0.01,
                    "delta_calibrated_brier_improvement": -0.005,
                },
                {
                    "variant": "without_beam_search",
                    "direction": "regression",
                    "impact_score": -0.10,
                    "delta_top1": 0.0,
                    "delta_map": 0.0,
                    "delta_rule_recall": 0.0,
                    "delta_rule_precision": 0.0,
                    "delta_beam_success": -0.5,
                    "delta_calibrated_ece_improvement": 0.0,
                    "delta_calibrated_brier_improvement": 0.0,
                },
                {
                    "variant": "without_diversity_reranking",
                    "direction": "regression",
                    "impact_score": -0.08,
                    "delta_top1": 0.0,
                    "delta_map": 0.0,
                    "delta_rule_recall": 0.0,
                    "delta_rule_precision": 0.0,
                    "delta_patch_success": -0.2,
                    "delta_beam_success": -0.1,
                    "delta_multi_patch_success": 0.0,
                    "delta_calibrated_ece_improvement": 0.0,
                    "delta_calibrated_brier_improvement": 0.0,
                },
                {
                    "variant": "without_candidate_deduplication",
                    "direction": "regression",
                    "impact_score": -0.12,
                    "delta_top1": 0.0,
                    "delta_map": 0.0,
                    "delta_rule_recall": 0.0,
                    "delta_rule_precision": 0.0,
                    "delta_patch_success": -1.0,
                    "delta_beam_success": -1.0,
                    "delta_multi_patch_success": -1.0,
                    "delta_calibrated_ece_improvement": 0.0,
                    "delta_calibrated_brier_improvement": 0.0,
                }
            ],
        },
        "weight_search_results": [
            {
                "top1": 1.0,
                "validation_score": 0.98,
                "robust_validation_score": 0.97,
                "source_group_count": 4,
                "source_groups": {
                    "repo/a": {"case_count": 31, "top1": 1.0, "map": 1.0},
                    "repo/b": {"case_count": 31, "top1": 0.98, "map": 0.99},
                },
                "holdout_splits": [
                    {
                        "holdout_group": "repo/a",
                        "train_groups": ["repo/b"],
                        "top1_gap": 0.02,
                        "map_gap": 0.03,
                    },
                    {
                        "holdout_group": "repo/b",
                        "train_groups": ["repo/a"],
                        "top1_gap": -0.02,
                        "map_gap": -0.03,
                    },
                ],
                "max_top1_gap": 0.02,
                "max_map_gap": 0.03,
                "pareto_optimal": True,
                "dominates_count": 5,
                "dominated_by_count": 0,
            }
        ],
        "patch_weight_search_results": [
            {
                "top1_success": 0.95,
                "pareto_optimal": True,
                "dominates_count": 4,
                "dominated_by_count": 0,
            }
        ],
        "patch_judge_fusion_summary": {
            "status": "improved",
            "profile_count": 2,
            "judge_profile_count": 1,
            "baseline_profile": "patch_profile",
            "best_judge_profile": "patch_profile_judge08",
            "best_judge_weight": 0.08,
            "validation_delta": 0.03,
            "top1_delta": 0.04,
            "mrr_delta": 0.02,
            "success_margin_delta": 0.01,
            "first_success_rank_delta": 0.25,
        },
        "hard_case_mining": {"suggestion_count": 2},
        "benchmark_mining": {
            "judged_candidate_count": 7,
            "cluster_count": 2,
            "suggestion_count": 2,
            "template_seeds": [
                {"seed_name": "judge_mining_test_failure"},
                {"seed_name": "judge_mining_timeout"},
            ],
        },
        "hard_case_generation": {
            "generated_count": 5,
            "selection_audit": {
                "selected_candidate_count": 5,
                "selected_rule_count": 2,
                "average_candidate_score": 21.5,
            },
        },
        "hard_case_generated_benchmark": {
            "benchmark_report": {
                "summary": {
                    "case_count": 5,
                    "patch_success_rate": 1.0,
                    "search_competition_analysis": {
                        "multi_candidate_case_count": 4,
                        "score_inversion_count": 2,
                        "score_inversion_rate": 0.6667,
                        "average_failure_pressure": 0.44,
                        "diversity_assisted_success_count": 1,
                        "average_success_diversity_lift": 3.0,
                        "average_success_diversity_bonus": 0.9,
                    },
                    "search_budget_analysis": {
                        "evaluated_case_count": 5,
                        "budget_auc": 0.8,
                        "success_at_budget": {"1": 0.8, "3": 1.0},
                        "average_normalized_effort": 0.4,
                        "dedupe_affected_case_count": 1,
                        "total_deduplicated_candidates": 3,
                        "max_deduplicated_candidates": 3,
                        "average_deduplicated_candidates": 0.6,
                        "average_duplicate_pressure": 0.12,
                    },
                    "reflection_analysis": {
                        "reflection_success_case_count": 1,
                        "reflection_candidate_count": 1,
                        "successful_reflection_candidate_count": 1,
                        "reflection_candidate_success_rate": 1.0,
                        "average_success_reflection_depth": 1.0,
                    },
                },
                "cases": [
                    {
                        "case_name": "generated_score_inversion_probe",
                        "metadata": {
                            "hard_case_target_signal": "search_score_inversion",
                            "hard_case_target_signals": [
                                "search_score_inversion"
                            ],
                            "hard_case_generation_strategy": (
                                "search_competition_score_inversion_probe"
                            ),
                            "hard_case_generation_source": (
                                "search_competition_gap"
                            ),
                            "hard_case_generation_priority": "high",
                            "hard_case_generation_focus": (
                                "top-rank decoy patch pressure"
                            ),
                            "hard_case_selection_policy": (
                                "search_competition_candidate_priority"
                            ),
                            "source_candidate_id": "candidate_score_probe",
                            "expected_score_inversion": True,
                            "score_inversion_decoy_variant": (
                                "overly_conservative_range_bound"
                            ),
                            "score_inversion_success_variant": (
                                "shrink_range_upper_bound"
                            ),
                            "hard_case_selected_candidate_scores": {
                                "candidate_score_probe": 38.5,
                            },
                            "hard_case_selected_candidate_reasons": {
                                "candidate_score_probe": [
                                    "rule_overlap=possible_index_overrun",
                                    "search_score_inversion_rule_weight=14.0",
                                    "provenance_bonus=1.9000",
                                    (
                                        "provenance_metrics="
                                        "case_provenance=1.0000;"
                                        "source_sha256=1.0000;"
                                        "stable_ref=1.0000;"
                                        "license=1.0000;"
                                        "leakage_risk=0.0000"
                                    ),
                                ],
                            },
                        },
                        "expected_rule_ids": ["possible_index_overrun"],
                        "patch_success": True,
                        "beam_search_results": [
                            {
                                "rule_id": "possible_index_overrun",
                                "variant": "overly_conservative_range_bound",
                                "success": False,
                            },
                            {
                                "rule_id": "possible_index_overrun",
                                "variant": "shrink_range_upper_bound",
                                "success": True,
                            },
                        ],
                        "search_analysis": {"first_success_rank": 2},
                    },
                    {
                        "case_name": "generated_diversity_reranking_probe",
                        "metadata": {
                            "hard_case_target_signal": (
                                "search_diversity_reranking"
                            ),
                            "hard_case_target_signals": [
                                "search_diversity_reranking"
                            ],
                            "hard_case_generation_strategy": (
                                "search_competition_diversity_reranking_probe"
                            ),
                            "hard_case_generation_source": (
                                "search_competition_gap"
                            ),
                            "hard_case_generation_priority": "medium",
                            "hard_case_generation_focus": (
                                "diversity reranking lift pressure"
                            ),
                            "hard_case_selection_policy": (
                                "search_competition_candidate_priority"
                            ),
                            "source_candidate_id": "candidate_diversity_probe",
                            "expected_diversity_reranking": True,
                            "expected_diversity_assisted_success": True,
                        },
                        "expected_rule_ids": ["possible_index_overrun"],
                        "patch_success": True,
                        "beam_search_results": [
                            {
                                "rule_id": "diversity_success_rule",
                                "variant": "diversity_success",
                                "success": True,
                                "search_diversity": {
                                    "base_rank": 5,
                                    "rank": 2,
                                    "bonus": 0.9,
                                    "reasons": ["new_rule", "new_variant"],
                                },
                            },
                            {
                                "rule_id": "diversity_duplicate_decoy_rule",
                                "variant": "diversity_duplicate_decoy",
                                "success": False,
                            },
                        ],
                        "search_analysis": {"first_success_rank": 1},
                    },
                    {
                        "case_name": "generated_candidate_deduplication_probe",
                        "metadata": {
                            "hard_case_target_signal": (
                                "candidate_deduplication_pressure"
                            ),
                            "hard_case_target_signals": [
                                "candidate_deduplication_pressure"
                            ],
                            "hard_case_generation_strategy": (
                                "search_budget_candidate_deduplication_probe"
                            ),
                            "hard_case_generation_source": "search_budget_gap",
                            "hard_case_generation_priority": "high",
                            "hard_case_generation_focus": (
                                "candidate deduplication budget pressure"
                            ),
                            "hard_case_selection_policy": (
                                "search_competition_candidate_priority"
                            ),
                            "source_candidate_id": "candidate_dedupe_probe",
                            "expected_candidate_deduplication": True,
                            "patch_score_profile": (
                                "candidate_deduplication_probe"
                            ),
                        },
                        "expected_rule_ids": ["possible_index_overrun"],
                        "patch_success": True,
                        "patch_candidates_count": 5,
                        "beam_search_results": [
                            {
                                "candidate_id": "dedupe_canonical_failure",
                                "rule_id": "possible_index_overrun",
                                "variant": "duplicate_failed_patch",
                                "success": False,
                                "search_duplicate_count": 3,
                            },
                            {
                                "candidate_id": "dedupe_success_patch",
                                "rule_id": "possible_index_overrun",
                                "variant": "unique_success_patch",
                                "success": True,
                                "search_duplicate_count": 0,
                            },
                        ],
                        "search_analysis": {
                            "evaluated_nodes": 2,
                            "successful_nodes": 1,
                            "first_success_rank": 2,
                            "failures_before_success": 1,
                            "deduplicated_candidates": 3,
                            "effective_candidate_pool": 5,
                            "deduplication_savings_ratio": 0.6,
                        },
                    },
                    {
                        "case_name": "generated_missing_len_probe",
                        "metadata": {
                            "hard_case_target_signal": "without_static_rules",
                        },
                        "expected_rule_ids": ["missing_len_zero_guard"],
                        "patch_success": True,
                        "beam_search_results": [
                            {
                                "rule_id": "missing_len_zero_guard",
                                "variant": "return_default_on_empty",
                                "success": False,
                            },
                            {
                                "rule_id": "missing_len_zero_guard",
                                "variant": "insert_len_zero_guard",
                                "success": True,
                            },
                        ],
                        "search_analysis": {"first_success_rank": 2},
                    },
                    {
                        "case_name": "generated_reflection_depth_probe",
                        "metadata": {
                            "hard_case_target_signal": "reflection_depth",
                            "hard_case_target_signals": ["reflection_depth"],
                            "hard_case_generation_strategy": (
                                "reflection_depth_probe"
                            ),
                            "hard_case_generation_source": (
                                "reflection_analysis_gap"
                            ),
                            "hard_case_generation_priority": "high",
                            "hard_case_generation_focus": (
                                "depth-1 self-repair recovery"
                            ),
                            "hard_case_selection_policy": (
                                "reflection_depth_candidate_priority"
                            ),
                            "source_candidate_id": "candidate_reflection_probe",
                            "expected_reflection_depth": True,
                            "patch_score_profile": "reflection_depth_probe",
                        },
                        "expected_rule_ids": ["possible_index_overrun"],
                        "patch_success": True,
                        "beam_search_results": [
                            {
                                "candidate_id": "reflection_seed",
                                "rule_id": "possible_index_overrun",
                                "variant": "overly_conservative_range_bound",
                                "success": False,
                                "depth": 0,
                            },
                            {
                                "candidate_id": "reflection_depth_1",
                                "parent_id": "reflection_seed",
                                "rule_id": "possible_index_overrun",
                                "variant": (
                                    "reflection_shrink_range_upper_bound"
                                ),
                                "success": True,
                                "depth": 1,
                            },
                        ],
                        "search_analysis": {
                            "first_success_depth": 1,
                            "evaluated_nodes": 2,
                            "successful_nodes": 1,
                        },
                    },
                ],
            }
        },
        "quality_gate": {
            "passed": True,
            "checks": [{"name": "benchmark_cases", "passed": True}],
        },
    }
