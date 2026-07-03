import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.quality_gate import (
    QualityGateThresholds,
    evaluate_quality_gate,
    render_quality_gate_markdown,
)


def test_quality_gate_accepts_suite_artifact_shape():
    result = evaluate_quality_gate(_passing_suite_payload())

    assert result.passed
    assert {check.name for check in result.checks} == {
        "benchmark_cases",
        "top1_localization",
        "top3_localization",
        "patch_success_rate",
        "slice_grounded_case_ratio",
        "average_top1_slice_support",
        "average_top1_slice_failed_test_reachability",
        "average_top1_slice_call_chain_coverage",
        "patch_sandbox_evidence",
        "score_decomposition",
        "patch_score_weight_pareto_metrics",
        "patch_score_weight_pareto_optimal",
        "patch_score_weight_top1_success",
        "patch_score_weight_mrr",
        "patch_score_feedback_weight",
    }


def test_quality_gate_fails_numeric_thresholds():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["top1"] = 0.4

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["top1_localization"].passed is False
    assert by_name["top1_localization"].actual == "0.4000"


def test_quality_gate_fails_weak_slice_grounding_metrics():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"].update(
        {
            "slice_grounded_case_count": 10,
            "average_top1_slice_support": 0.40,
            "average_top1_slice_failed_test_reachability": 0.30,
            "average_top1_slice_call_chain_coverage": 0.20,
        }
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["slice_grounded_case_ratio"].passed is False
    assert by_name["slice_grounded_case_ratio"].actual == "0.2000"
    assert by_name["average_top1_slice_support"].passed is False
    assert by_name["average_top1_slice_failed_test_reachability"].passed is False
    assert by_name["average_top1_slice_call_chain_coverage"].passed is False


def test_quality_gate_fails_missing_patch_evidence():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["cases"][0]["beam_search_results"] = []
    payload["benchmark_report"]["cases"][0]["patch_search_results"] = []

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["patch_sandbox_evidence"].passed is False
    assert by_name["patch_sandbox_evidence"].details == ["case_0"]


def test_quality_gate_accepts_repair_loop_patch_evidence():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["cases"][0]["beam_search_results"] = []
    payload["benchmark_report"]["cases"][0]["patch_search_results"] = []
    payload["benchmark_report"]["cases"][0]["repair_results"] = [
        {
            "success": True,
            "failure_type": "success",
            "passed": 1,
            "failed": 0,
        }
    ]

    result = evaluate_quality_gate(payload)

    assert result.passed


def test_quality_gate_fails_missing_score_components():
    payload = _passing_suite_payload()
    del payload["benchmark_report"]["cases"][0]["localization_details"][0]["signals"]["llm"]

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["score_decomposition"].passed is False
    assert "missing llm" in by_name["score_decomposition"].details[0]


def test_quality_gate_fails_patch_score_weight_search_regression():
    payload = _passing_suite_payload()
    payload["patch_weight_search_results"][0]["top1_success"] = 0.2
    payload["patch_weight_search_results"][0]["mrr"] = 0.2
    payload["patch_weight_search_results"][0]["pareto_optimal"] = False
    payload["patch_weight_search_results"][0]["dominated_by_count"] = 1
    payload["patch_weight_search_results"][0]["weights"]["execution_feedback"] = 0.0

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["patch_score_weight_pareto_metrics"].passed
    assert by_name["patch_score_weight_pareto_optimal"].passed is False
    assert by_name["patch_score_weight_top1_success"].passed is False
    assert by_name["patch_score_weight_mrr"].passed is False
    assert by_name["patch_score_feedback_weight"].passed is False


def test_quality_gate_checks_final_score_weight_search_when_present():
    payload = _passing_suite_payload()
    payload["settings"]["run_weight_search"] = True
    payload["weight_search_results"] = [
        _final_score_weight_result(
            robust_score=0.91,
            source_group_count=3,
            top1_gap=0.04,
            map_gap=0.05,
        )
    ]

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["final_score_weight_robust_metrics"].passed
    assert by_name["final_score_weight_pareto_optimal"].passed
    assert by_name["final_score_weight_top1"].actual == "1.0000"
    assert by_name["final_score_weight_robust_score"].actual == "0.9100"
    assert by_name["final_score_weight_source_groups"].actual == "3"
    assert by_name["final_score_weight_top1_gap"].passed
    assert by_name["final_score_weight_map_gap"].passed


def test_quality_gate_fails_weak_final_score_weight_search():
    payload = _passing_suite_payload()
    payload["settings"]["run_weight_search"] = True
    payload["weight_search_results"] = [
        {
            "profile": "old_format",
            "validation_score": 0.40,
            "top1": 0.20,
        },
        _final_score_weight_result(
            profile="unstable",
            top1=0.40,
            robust_score=0.30,
            source_group_count=0,
            top1_gap=0.55,
            map_gap=0.50,
            pareto_optimal=False,
        ),
    ]

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["final_score_weight_robust_metrics"].passed is False
    assert by_name["final_score_weight_pareto_optimal"].passed is False
    assert by_name["final_score_weight_top1"].passed is False
    assert by_name["final_score_weight_robust_score"].passed is False
    assert by_name["final_score_weight_source_groups"].passed is False
    assert by_name["final_score_weight_top1_gap"].passed is False
    assert by_name["final_score_weight_map_gap"].passed is False


def test_quality_gate_checks_llm_judge_reliability_when_enabled():
    payload = _passing_suite_payload()
    payload["settings"]["judge_mode"] = "llm"
    payload["benchmark_report"]["summary"]["llm_judge_reliability"] = {
        "judged_case_count": 50,
        "brier_score": 0.08,
        "expected_calibration_error": 0.12,
        "agreement_rate": 0.90,
    }

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["llm_judge_cases"].actual == "50"
    assert by_name["llm_judge_brier_score"].passed
    assert by_name["llm_judge_expected_calibration_error"].passed
    assert by_name["llm_judge_agreement_rate"].passed


def test_quality_gate_fails_missing_llm_judge_reliability_for_llm_mode():
    payload = _passing_suite_payload()
    payload["settings"]["judge_mode"] = "llm"

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["llm_judge_reliability"].passed is False
    assert by_name["llm_judge_reliability"].actual == "missing or empty"


def test_quality_gate_fails_bad_llm_judge_reliability_metrics():
    payload = _passing_suite_payload()
    payload["settings"]["judge_mode"] = "llm"
    payload["benchmark_report"]["summary"]["llm_judge_reliability"] = {
        "judged_case_count": 0,
        "brier_score": 0.41,
        "expected_calibration_error": 0.35,
        "agreement_rate": 0.40,
    }

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["llm_judge_cases"].passed is False
    assert by_name["llm_judge_brier_score"].passed is False
    assert by_name["llm_judge_expected_calibration_error"].passed is False
    assert by_name["llm_judge_agreement_rate"].passed is False


def test_quality_gate_checks_patch_judge_reliability_when_enabled():
    payload = _passing_suite_payload()
    payload["settings"]["patch_judge_mode"] = "llm"
    payload["patch_weight_search_results"].append(
        {
            **payload["patch_weight_search_results"][0],
            "profile": "default_judge08",
            "patch_judge_weight": 0.08,
        }
    )
    payload["patch_judge_fusion_summary"] = {
        "status": "no_gain",
        "profile_count": 2,
        "judge_profile_count": 1,
        "baseline_profile": "default",
        "best_judge_profile": "default_judge08",
        "best_judge_weight": 0.08,
        "validation_delta": 0.0,
        "top1_delta": 0.0,
        "mrr_delta": 0.0,
        "success_margin_delta": 0.0,
        "first_success_rank_delta": 0.0,
    }
    payload["benchmark_report"]["summary"]["patch_judge_reliability"] = {
        "judged_candidate_count": 50,
        "brier_score": 0.10,
        "expected_calibration_error": 0.12,
        "agreement_rate": 0.70,
    }
    payload["benchmark_mining"] = _benchmark_mining_payload(
        judged_candidate_count=50,
        cluster_count=2,
        suggestion_count=2,
        template_seed_count=2,
        preview_case_count=2,
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["patch_judge_candidates"].actual == "50"
    assert by_name["patch_judge_brier_score"].passed
    assert by_name["patch_judge_expected_calibration_error"].passed
    assert by_name["patch_judge_agreement_rate"].passed
    assert by_name["patch_judge_mining_candidate_coverage"].actual == "50"
    assert by_name["patch_judge_mining_cluster_count"].passed
    assert by_name["patch_judge_mining_template_seed_count"].passed
    assert by_name["patch_judge_mining_seed_preview"].passed
    assert by_name["patch_judge_fusion_profiles"].actual == "1"
    assert by_name["patch_judge_fusion_status"].actual == "no_gain"
    assert by_name["patch_judge_fusion_validation_delta"].passed
    assert by_name["patch_judge_fusion_top1_delta"].passed
    assert by_name["patch_judge_fusion_mrr_delta"].passed
    assert by_name["patch_judge_fusion_success_margin_delta"].passed
    assert by_name["patch_judge_fusion_first_success_rank_delta"].passed


def test_quality_gate_fails_patch_judge_fusion_regression():
    payload = _passing_suite_payload()
    payload["settings"]["patch_judge_mode"] = "llm"
    payload["patch_weight_search_results"].append(
        {
            **payload["patch_weight_search_results"][0],
            "profile": "default_judge08",
            "patch_judge_weight": 0.08,
        }
    )
    payload["patch_judge_fusion_summary"] = {
        "status": "no_gain",
        "profile_count": 2,
        "judge_profile_count": 1,
        "baseline_profile": "default",
        "best_judge_profile": "default_judge08",
        "best_judge_weight": 0.08,
        "validation_delta": -0.08,
        "top1_delta": -0.10,
        "mrr_delta": -0.10,
        "success_margin_delta": -0.09,
        "first_success_rank_delta": -0.40,
    }
    payload["benchmark_report"]["summary"]["patch_judge_reliability"] = {
        "judged_candidate_count": 50,
        "brier_score": 0.10,
        "expected_calibration_error": 0.12,
        "agreement_rate": 0.70,
    }
    payload["benchmark_mining"] = _benchmark_mining_payload(
        judged_candidate_count=50,
        cluster_count=2,
        suggestion_count=2,
        template_seed_count=2,
        preview_case_count=2,
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["patch_judge_fusion_validation_delta"].passed is False
    assert by_name["patch_judge_fusion_top1_delta"].passed is False
    assert by_name["patch_judge_fusion_mrr_delta"].passed is False
    assert by_name["patch_judge_fusion_success_margin_delta"].passed is False
    assert by_name["patch_judge_fusion_first_success_rank_delta"].passed is False


def test_quality_gate_fails_missing_patch_judge_reliability_for_llm_mode():
    payload = _passing_suite_payload()
    payload["settings"]["patch_judge_mode"] = "llm"

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["patch_judge_reliability"].passed is False
    assert by_name["patch_judge_reliability"].actual == "missing or empty"


def test_quality_gate_fails_missing_benchmark_mining_for_patch_judge_mode():
    payload = _passing_suite_payload()
    payload["settings"]["patch_judge_mode"] = "llm"
    payload["benchmark_report"]["summary"]["patch_judge_reliability"] = {
        "judged_candidate_count": 4,
        "brier_score": 0.10,
        "expected_calibration_error": 0.12,
        "agreement_rate": 0.70,
    }

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["patch_judge_benchmark_mining"].passed is False
    assert by_name["patch_judge_benchmark_mining"].actual == "missing or empty"


def test_quality_gate_fails_bad_patch_judge_reliability_metrics():
    payload = _passing_suite_payload()
    payload["settings"]["patch_judge_mode"] = "llm"
    payload["benchmark_report"]["summary"]["patch_judge_reliability"] = {
        "judged_candidate_count": 0,
        "brier_score": 0.80,
        "expected_calibration_error": 0.70,
        "agreement_rate": 0.10,
    }
    payload["benchmark_mining"] = _benchmark_mining_payload(
        judged_candidate_count=0,
        cluster_count=0,
        suggestion_count=0,
        template_seed_count=0,
        preview_case_count=0,
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["patch_judge_candidates"].passed is False
    assert by_name["patch_judge_brier_score"].passed is False
    assert by_name["patch_judge_expected_calibration_error"].passed is False
    assert by_name["patch_judge_agreement_rate"].passed is False


def test_quality_gate_fails_inconsistent_patch_judge_benchmark_mining():
    payload = _passing_suite_payload()
    payload["settings"]["patch_judge_mode"] = "llm"
    payload["benchmark_report"]["summary"]["patch_judge_reliability"] = {
        "judged_candidate_count": 5,
        "brier_score": 0.10,
        "expected_calibration_error": 0.12,
        "agreement_rate": 0.70,
    }
    payload["benchmark_mining"] = _benchmark_mining_payload(
        judged_candidate_count=3,
        cluster_count=4,
        suggestion_count=2,
        template_seed_count=1,
        preview_case_count=0,
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["patch_judge_mining_candidate_coverage"].passed is False
    assert by_name["patch_judge_mining_candidate_coverage"].actual == "3"
    assert by_name["patch_judge_mining_cluster_count"].passed is False
    assert by_name["patch_judge_mining_template_seed_count"].passed is False
    assert by_name["patch_judge_mining_seed_preview"].passed is False


def test_quality_gate_checks_metric_uncertainty_when_present():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["metric_uncertainty"] = (
        _metric_uncertainty_payload(
            case_count=50,
            top1_width=0.12,
            map_width=0.10,
            patch_width=0.08,
        )
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["metric_uncertainty_cases"].actual == "50"
    assert by_name["metric_uncertainty_top1_width"].passed
    assert by_name["metric_uncertainty_map_width"].passed
    assert by_name["metric_uncertainty_patch_success_rate_width"].passed
    assert by_name["metric_uncertainty_top1_lower"].passed
    assert by_name["metric_uncertainty_map_lower"].passed
    assert by_name["metric_uncertainty_patch_success_rate_lower"].passed


def test_quality_gate_checks_search_budget_analysis_when_present():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["search_budget_analysis"] = (
        _search_budget_analysis_payload(
            evaluated_case_count=50,
            success_at_1=0.72,
            budget_auc=0.81,
        )
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["search_budget_cases"].actual == "50"
    assert by_name["search_budget_success_at_1"].actual == "0.7200"
    assert by_name["search_budget_auc"].actual == "0.8100"
    assert by_name["search_budget_first_success_rank_p90"].passed


def test_quality_gate_checks_search_budget_deduplication_when_present():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["search_budget_analysis"] = (
        _search_budget_analysis_payload(
            evaluated_case_count=50,
            success_at_1=0.72,
            budget_auc=0.81,
            dedupe_affected_case_count=5,
            total_deduplicated_candidates=11,
            average_duplicate_pressure=0.07,
        )
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["search_budget_dedupe_affected_cases"].actual == "5"
    assert by_name["search_budget_deduplicated_candidates"].actual == "11"
    assert by_name["search_budget_average_duplicate_pressure"].actual == "0.0700"


def test_quality_gate_fails_weak_search_budget_analysis():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["search_budget_analysis"] = (
        _search_budget_analysis_payload(
            evaluated_case_count=0,
            success_at_1=0.20,
            budget_auc=0.30,
            first_success_rank_p90=5.0,
        )
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["search_budget_cases"].passed is False
    assert by_name["search_budget_success_at_1"].passed is False
    assert by_name["search_budget_auc"].passed is False
    assert by_name["search_budget_first_success_rank_p90"].passed is False


def test_quality_gate_fails_weak_search_budget_deduplication_when_required():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["search_budget_analysis"] = (
        _search_budget_analysis_payload(
            evaluated_case_count=50,
            success_at_1=0.72,
            budget_auc=0.81,
            dedupe_affected_case_count=0,
            total_deduplicated_candidates=1,
            average_duplicate_pressure=0.02,
        )
    )

    result = evaluate_quality_gate(
        payload,
        thresholds=QualityGateThresholds(
            min_search_budget_dedupe_affected_cases=2,
            min_search_budget_deduplicated_candidates=5,
            min_search_budget_average_duplicate_pressure=0.10,
        ),
    )
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["search_budget_dedupe_affected_cases"].passed is False
    assert by_name["search_budget_deduplicated_candidates"].passed is False
    assert by_name["search_budget_average_duplicate_pressure"].passed is False


def test_quality_gate_checks_search_competition_analysis_when_present():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["search_competition_analysis"] = (
        _search_competition_analysis_payload(
            multi_candidate_case_count=6,
            rule_diversity=1.25,
            failure_type_diversity=0.75,
            retention_bucket_diversity=1.50,
        )
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["search_competition_multi_candidate_cases"].actual == "6"
    assert by_name["search_competition_multi_candidate_rule_diversity"].passed
    assert by_name["search_competition_multi_candidate_failure_type_diversity"].passed
    assert by_name["search_competition_multi_candidate_retention_bucket_diversity"].passed
    assert by_name["search_competition_diversity_assisted_successes"].actual == "2"
    assert by_name["search_competition_average_success_diversity_lift"].actual == (
        "1.5000"
    )
    assert by_name["search_competition_average_success_diversity_bonus"].actual == (
        "0.2500"
    )


def test_quality_gate_fails_weak_search_competition_analysis():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["search_competition_analysis"] = (
        _search_competition_analysis_payload(
            multi_candidate_case_count=0,
            rule_diversity=0.0,
            failure_type_diversity=0.0,
            retention_bucket_diversity=0.0,
            diversity_assisted_successes=0,
            average_success_diversity_lift=0.0,
            average_success_diversity_bonus=0.0,
        )
    )

    result = evaluate_quality_gate(
        payload,
        QualityGateThresholds(
            min_search_competition_diversity_assisted_successes=1,
            min_search_competition_average_success_diversity_lift=1.0,
            min_search_competition_average_success_diversity_bonus=0.1,
        ),
    )
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["search_competition_multi_candidate_cases"].passed is False
    assert by_name["search_competition_multi_candidate_rule_diversity"].passed is False
    assert (
        by_name["search_competition_multi_candidate_failure_type_diversity"].passed
        is False
    )
    assert (
        by_name["search_competition_multi_candidate_retention_bucket_diversity"].passed
        is False
    )
    assert by_name["search_competition_diversity_assisted_successes"].passed is False
    assert (
        by_name["search_competition_average_success_diversity_lift"].passed
        is False
    )
    assert (
        by_name["search_competition_average_success_diversity_bonus"].passed
        is False
    )


def test_quality_gate_fails_wide_metric_uncertainty_intervals():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["metric_uncertainty"] = (
        _metric_uncertainty_payload(
            case_count=0,
            top1_width=0.55,
            map_width=0.50,
            patch_width=0.45,
        )
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["metric_uncertainty_cases"].passed is False
    assert by_name["metric_uncertainty_top1_width"].passed is False
    assert by_name["metric_uncertainty_map_width"].passed is False
    assert by_name["metric_uncertainty_patch_success_rate_width"].passed is False


def test_quality_gate_fails_low_metric_uncertainty_lower_bounds():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["metric_uncertainty"] = {
        "case_count": 50,
        "confidence_level": 0.95,
        "bootstrap_samples": 1000,
        "seed": 1729,
        "metrics": {
            "top1": {
                "metric": "top1",
                "mean": 0.70,
                "lower": 0.55,
                "upper": 0.75,
                "width": 0.20,
                "sample_count": 50,
            },
            "map": {
                "metric": "map",
                "mean": 0.55,
                "lower": 0.45,
                "upper": 0.60,
                "width": 0.15,
                "sample_count": 50,
            },
            "patch_success_rate": {
                "metric": "patch_success_rate",
                "mean": 0.55,
                "lower": 0.45,
                "upper": 0.60,
                "width": 0.15,
                "sample_count": 50,
            },
        },
    }

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["metric_uncertainty_top1_width"].passed
    assert by_name["metric_uncertainty_map_width"].passed
    assert by_name["metric_uncertainty_patch_success_rate_width"].passed
    assert by_name["metric_uncertainty_top1_lower"].passed is False
    assert by_name["metric_uncertainty_top1_lower"].actual == "0.5500"
    assert by_name["metric_uncertainty_map_lower"].passed is False
    assert by_name["metric_uncertainty_patch_success_rate_lower"].passed is False


def test_quality_gate_checks_localization_calibration_when_present():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["localization_calibration"] = (
        _localization_calibration_payload(
            case_count=50,
            calibrated_ece=0.04,
            holdout_splits=3,
            min_train_cases=12,
            max_holdout_ece=0.06,
        )
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["localization_calibration_cases"].actual == "50"
    assert by_name["localization_calibrated_expected_calibration_error"].passed
    assert by_name["localization_source_holdout_splits"].actual == "3"
    assert by_name["localization_min_holdout_train_cases"].actual == "12"
    assert by_name[
        "localization_holdout_calibrated_expected_calibration_error"
    ].passed


def test_quality_gate_fails_weak_localization_calibration():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["localization_calibration"] = (
        _localization_calibration_payload(
            case_count=0,
            calibrated_ece=0.20,
            holdout_splits=0,
            min_train_cases=0,
            max_holdout_ece=0.30,
        )
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["localization_calibration_cases"].passed is False
    assert (
        by_name["localization_calibrated_expected_calibration_error"].passed
        is False
    )
    assert by_name["localization_source_holdout_splits"].passed is False
    assert by_name["localization_min_holdout_train_cases"].passed is False
    assert (
        by_name["localization_holdout_calibrated_expected_calibration_error"].passed
        is False
    )


def test_quality_gate_checks_ablation_impact_when_present():
    payload = _passing_suite_payload()
    payload["ablation_impact"] = {
        "baseline_variant": "full",
        "impacted_variant_count": 2,
        "rows": [
            {
                "variant": "without_static_rules",
                "impact_score": -0.35,
                "direction": "regression",
                "dominant_signal": "rule_recall",
                "dominant_contribution": -0.20,
                "regression_signal_count": 2,
                "improvement_signal_count": 0,
                "signal_contributions": {
                    "rule_recall": -0.20,
                    "rule_precision": -0.15,
                },
            },
            {
                "variant": "without_llm_score",
                "impact_score": 0.0,
                "direction": "neutral",
                "dominant_signal": "top1",
                "dominant_contribution": 0.0,
                "regression_signal_count": 0,
                "improvement_signal_count": 0,
                "signal_contributions": {"top1": 0.0},
            },
        ],
    }

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["ablation_impact_baseline"].passed
    assert by_name["ablation_impact_variants"].actual == "2"
    assert by_name["ablation_impact_regressions"].actual == "1"
    assert by_name["ablation_impact_signal"].actual == "0.3500"
    assert by_name["ablation_impact_attribution"].passed
    assert by_name["ablation_impact_regression_attribution"].passed


def test_quality_gate_fails_weak_ablation_impact_signal():
    payload = _passing_suite_payload()
    payload["ablation_impact"] = {
        "baseline_variant": "baseline",
        "impacted_variant_count": 1,
        "rows": [
            {
                "variant": "without_static_rules",
                "impact_score": -0.01,
                "direction": "neutral",
                "dominant_signal": "",
                "dominant_contribution": 0.0,
                "regression_signal_count": 0,
                "improvement_signal_count": 0,
                "signal_contributions": {},
            }
        ],
    }

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["ablation_impact_baseline"].passed is False
    assert by_name["ablation_impact_regressions"].passed is False
    assert by_name["ablation_impact_signal"].passed is False
    assert by_name["ablation_impact_attribution"].passed is False
    assert by_name["ablation_impact_regression_attribution"].passed is False


def test_quality_gate_links_generated_hard_cases_to_ablation_rows():
    payload = _passing_suite_payload()
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=2,
        template_case_count=2,
        selected_candidate_count=2,
        selected_rule_count=3,
        selected_function_count=2,
        selected_source_count=2,
        average_candidate_score=23.5,
        average_diversity_bonus=1.0,
        target_signals=[
            "search_score_inversion",
            "search_diversity_reranking",
        ],
    )
    payload["ablation_impact"] = {
        "baseline_variant": "full",
        "impacted_variant_count": 2,
        "rows": [
            _ablation_row("without_beam_search", -0.18),
            _ablation_row("without_diversity_reranking", -0.11),
        ],
    }

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["hard_case_generated_ablation_links"].passed
    assert by_name["hard_case_generated_ablation_links"].actual == "2/2"


def test_quality_gate_links_subscript_key_flow_generation_to_data_dependency_ablation():
    payload = _passing_suite_payload()
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=1,
        template_case_count=1,
        selected_candidate_count=1,
        selected_rule_count=3,
        selected_function_count=1,
        selected_source_count=1,
        average_candidate_score=18.5,
        average_diversity_bonus=0.0,
        target_signals=["subscript_key_flow"],
    )
    payload["ablation_impact"] = {
        "baseline_variant": "full",
        "impacted_variant_count": 1,
        "rows": [_ablation_row("without_data_dependency", -0.16)],
    }

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["hard_case_generated_ablation_links"].passed
    assert by_name["hard_case_generated_ablation_links"].actual == "1/1"


def test_quality_gate_links_rule_precision_filter_generation_to_ablation():
    payload = _passing_suite_payload()
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=1,
        template_case_count=1,
        selected_candidate_count=1,
        selected_rule_count=4,
        selected_function_count=1,
        selected_source_count=1,
        average_candidate_score=28.0,
        average_diversity_bonus=0.0,
        target_signals=["without_rule_precision_filter"],
    )
    payload["ablation_impact"] = {
        "baseline_variant": "full",
        "impacted_variant_count": 1,
        "rows": [_ablation_row("without_rule_precision_filter", -0.09)],
    }

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["hard_case_generated_ablation_links"].passed
    assert by_name["hard_case_generated_ablation_links"].actual == "1/1"


def test_quality_gate_fails_generated_hard_case_without_ablation_link():
    payload = _passing_suite_payload()
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=2,
        template_case_count=2,
        selected_candidate_count=2,
        selected_rule_count=3,
        selected_function_count=2,
        selected_source_count=2,
        average_candidate_score=23.5,
        average_diversity_bonus=1.0,
        target_signals=[
            "search_diversity_reranking",
            "without_static_rules",
        ],
    )
    payload["ablation_impact"] = {
        "baseline_variant": "full",
        "impacted_variant_count": 1,
        "rows": [_ablation_row("without_beam_search", -0.18)],
    }

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["hard_case_generated_ablation_links"].passed is False
    assert by_name["hard_case_generated_ablation_links"].actual == "1/2"
    assert by_name["hard_case_generated_ablation_links"].details == [
        "missing_signals=without_static_rules"
    ]


def test_quality_gate_checks_difficulty_coverage_when_present():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["difficulty_report"] = (
        _difficulty_report_payload(
            case_count=50,
            medium=12,
            hard=2,
            cross_file=2,
            patch_competition=6,
            cross_function_data_flow=40,
        )
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["difficulty_case_coverage"].actual == "50"
    assert by_name["difficulty_medium_cases"].actual == "12"
    assert by_name["difficulty_hard_cases"].actual == "2"
    assert by_name["difficulty_cross_file_patch_cases"].actual == "2"
    assert by_name["difficulty_patch_competition_cases"].actual == "6"
    assert by_name["difficulty_cross_function_data_flow_cases"].actual == "40"


def test_quality_gate_fails_weak_difficulty_coverage():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["difficulty_report"] = (
        _difficulty_report_payload(
            case_count=49,
            medium=0,
            hard=0,
            cross_file=0,
            patch_competition=0,
            cross_function_data_flow=0,
        )
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["difficulty_case_coverage"].passed is False
    assert by_name["difficulty_medium_cases"].passed is False
    assert by_name["difficulty_hard_cases"].passed is False
    assert by_name["difficulty_cross_file_patch_cases"].passed is False
    assert by_name["difficulty_patch_competition_cases"].passed is False
    assert by_name["difficulty_cross_function_data_flow_cases"].passed is False


def test_quality_gate_checks_benchmark_diversity_when_present():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"].update(
        _diversity_metrics_payload(bug_type_count=8, expected_rule_count=8)
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["bug_type_count"].actual == "8"
    assert by_name["expected_rule_count"].actual == "8"
    assert by_name["bug_type_case_floor"].actual == "weak=0"
    assert by_name["expected_rule_case_floor"].actual == "weak=0"


def test_quality_gate_fails_weak_benchmark_diversity():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"].update(
        {
            "bug_type_metrics": {"boundary error": {"case_count": 0}},
            "rule_metrics": {"possible_index_overrun": {"case_count": 0}},
        }
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["bug_type_count"].passed is False
    assert by_name["expected_rule_count"].passed is False
    assert by_name["bug_type_case_floor"].passed is False
    assert by_name["expected_rule_case_floor"].passed is False


def test_quality_gate_checks_generalization_when_present():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["generalization_report"] = (
        _generalization_report_payload(
            case_count=50,
            source_group_count=3,
            min_holdout_case_count=3,
            gap=0.05,
            holdout_split_count=3,
        )
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["generalization_case_coverage"].actual == "50"
    assert by_name["generalization_source_groups"].actual == "3"
    assert by_name["generalization_min_holdout_cases"].actual == "3"
    assert by_name["generalization_source_balance_entropy"].actual == "1.0000"
    assert by_name["generalization_holdout_splits"].actual == "3/3"
    assert by_name["generalization_top1_gap"].passed
    assert by_name["generalization_map_gap"].passed
    assert by_name["generalization_patch_success_gap"].passed
    assert by_name["generalization_search_efficiency_gap"].passed
    assert by_name["generalization_worst_holdout_gap_score"].passed
    assert by_name["generalization_stability_score"].actual == "0.9500"


def test_quality_gate_fails_weak_generalization_report():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["generalization_report"] = (
        _generalization_report_payload(
            case_count=49,
            source_group_count=2,
            min_holdout_case_count=0,
            gap=0.55,
            holdout_split_count=1,
        )
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["generalization_case_coverage"].passed is False
    assert by_name["generalization_source_groups"].passed is False
    assert by_name["generalization_min_holdout_cases"].passed is False
    assert by_name["generalization_holdout_splits"].passed is False
    assert by_name["generalization_top1_gap"].passed is False
    assert by_name["generalization_map_gap"].passed is False
    assert by_name["generalization_patch_success_gap"].passed is False
    assert by_name["generalization_search_efficiency_gap"].passed is False
    assert by_name["generalization_source_balance_entropy"].passed is False
    assert by_name["generalization_worst_holdout_gap_score"].passed is False
    assert by_name["generalization_stability_score"].passed is False


def test_quality_gate_checks_benchmark_provenance_when_present():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["benchmark_provenance_audit"] = (
        _benchmark_provenance_payload(
            case_coverage=1.0,
            mutation_coverage=1.0,
            sha_coverage=1.0,
            stable_ref_coverage=1.0,
            license_coverage=1.0,
            duplicate_signatures=0,
            source_concentration=0.50,
            leakage_risk=0.02,
            sha_present=50,
        )
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["benchmark_provenance_case_coverage"].actual == "1.0000"
    assert by_name["benchmark_provenance_mutation_coverage"].passed
    assert by_name["benchmark_provenance_license_coverage"].passed
    assert by_name["benchmark_provenance_source_sha256_coverage"].passed
    assert by_name["benchmark_provenance_stable_ref_coverage"].passed
    assert by_name["benchmark_provenance_duplicate_signatures"].passed
    assert by_name["benchmark_provenance_source_concentration"].passed
    assert by_name["benchmark_provenance_leakage_risk_score"].passed


def test_quality_gate_fails_weak_benchmark_provenance():
    payload = _passing_suite_payload()
    payload["benchmark_report"]["summary"]["benchmark_provenance_audit"] = (
        _benchmark_provenance_payload(
            case_coverage=0.60,
            mutation_coverage=0.40,
            sha_coverage=0.50,
            stable_ref_coverage=0.25,
            license_coverage=0.30,
            duplicate_signatures=2,
            source_concentration=0.95,
            leakage_risk=0.55,
            sha_present=10,
        )
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["benchmark_provenance_case_coverage"].passed is False
    assert by_name["benchmark_provenance_mutation_coverage"].passed is False
    assert by_name["benchmark_provenance_license_coverage"].passed is False
    assert by_name["benchmark_provenance_source_sha256_coverage"].passed is False
    assert by_name["benchmark_provenance_stable_ref_coverage"].passed is False
    assert by_name["benchmark_provenance_duplicate_signatures"].passed is False
    assert by_name["benchmark_provenance_source_concentration"].passed is False
    assert by_name["benchmark_provenance_leakage_risk_score"].passed is False


def test_quality_gate_checks_hard_case_generation_when_present():
    payload = _passing_suite_payload()
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=2,
        template_case_count=2,
        selected_candidate_count=4,
        selected_rule_count=4,
        selected_function_count=4,
        selected_source_count=4,
        average_candidate_score=23.5,
        average_diversity_bonus=3.0,
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["hard_case_generation_count"].actual == "2/2"
    assert by_name["hard_case_generation_audit"].passed
    assert by_name["hard_case_generation_selected_candidates"].actual == "4"
    assert by_name["hard_case_generation_rule_coverage"].actual == "4"
    assert by_name["hard_case_generation_function_coverage"].actual == "4"
    assert by_name["hard_case_generation_source_coverage"].actual == "4"
    assert by_name["hard_case_generation_candidate_score"].actual == "23.5000"
    assert by_name["hard_case_generation_diversity_bonus"].actual == "3.0000"
    assert (
        by_name["hard_case_generation_provenance_selected_ratio"].actual
        == "1.0000"
    )
    assert by_name["hard_case_generation_provenance_bonus"].actual == "1.0000"
    assert (
        by_name["hard_case_generation_provenance_source_sha256"].actual
        == "1.0000"
    )
    assert by_name["hard_case_generation_provenance_stable_ref"].actual == "1.0000"
    assert (
        by_name["hard_case_generation_provenance_leakage_risk"].actual
        == "0.0000"
    )


def test_quality_gate_counts_reused_hard_case_candidate_references():
    payload = _passing_suite_payload()
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=3,
        template_case_count=3,
        selected_candidate_count=2,
        selected_rule_count=3,
        selected_function_count=3,
        selected_source_count=3,
        average_candidate_score=23.5,
        average_diversity_bonus=0.0,
    )
    payload["hard_case_generation"]["rows"] = [
        {
            "status": "generated",
            "selected_candidate_ids": ["candidate_a"],
        },
        {
            "status": "generated",
            "selected_candidate_ids": ["candidate_b"],
        },
        {
            "status": "generated",
            "selected_candidate_ids": ["candidate_a"],
        },
    ]

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["hard_case_generation_selected_candidates"].actual == "3"


def test_quality_gate_checks_generated_hard_case_benchmark_when_present():
    payload = _passing_suite_payload()
    payload["settings"] = {"run_hard_case_generated_benchmark": True}
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=5,
        template_case_count=5,
        selected_candidate_count=5,
        selected_rule_count=5,
        selected_function_count=5,
        selected_source_count=5,
        average_candidate_score=23.5,
        average_diversity_bonus=3.0,
        target_signals=["search_score_inversion", "search_failure_pressure"],
    )
    payload["hard_case_generated_benchmark"] = _hard_case_generated_benchmark_payload(
        case_count=5,
        patch_success_rate=1.0,
        multi_candidate_case_count=5,
        score_inversion_count=2,
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["hard_case_generated_benchmark_cases"].actual == "5"
    assert by_name["hard_case_generated_patch_success_rate"].actual == "1.0000"
    assert by_name["hard_case_generated_multi_candidate_cases"].actual == "5"
    assert by_name["hard_case_generated_benchmark_count"].actual == "5/5"
    assert by_name["hard_case_generated_score_inversions"].actual == "2"


def test_quality_gate_checks_generated_diversity_reranking_evidence():
    payload = _passing_suite_payload()
    payload["settings"] = {"run_hard_case_generated_benchmark": True}
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=5,
        template_case_count=5,
        selected_candidate_count=5,
        selected_rule_count=5,
        selected_function_count=5,
        selected_source_count=5,
        average_candidate_score=23.5,
        average_diversity_bonus=3.0,
        target_signals=["search_diversity_reranking"],
    )
    payload["hard_case_generated_benchmark"] = _hard_case_generated_benchmark_payload(
        case_count=5,
        patch_success_rate=1.0,
        multi_candidate_case_count=5,
        score_inversion_count=0,
        diversity_assisted_success_count=1,
        average_success_diversity_lift=3.0,
        average_success_diversity_bonus=0.9,
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["hard_case_generated_diversity_assisted_successes"].actual == "1"
    assert by_name["hard_case_generated_success_diversity_lift"].actual == "3.0000"
    assert by_name["hard_case_generated_success_diversity_bonus"].actual == "0.9000"
    assert (
        by_name[
            "hard_case_generated_diversity_budget_sensitive_successes"
        ].actual
        == "1"
    )


def test_quality_gate_fails_generated_diversity_reranking_without_lift():
    payload = _passing_suite_payload()
    payload["settings"] = {"run_hard_case_generated_benchmark": True}
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=5,
        template_case_count=5,
        selected_candidate_count=5,
        selected_rule_count=5,
        selected_function_count=5,
        selected_source_count=5,
        average_candidate_score=23.5,
        average_diversity_bonus=3.0,
        target_signals=["search_diversity_reranking"],
    )
    payload["hard_case_generated_benchmark"] = _hard_case_generated_benchmark_payload(
        case_count=5,
        patch_success_rate=1.0,
        multi_candidate_case_count=5,
        score_inversion_count=0,
        diversity_assisted_success_count=0,
        average_success_diversity_lift=0.0,
        average_success_diversity_bonus=0.0,
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["hard_case_generated_diversity_assisted_successes"].passed is False
    assert by_name["hard_case_generated_success_diversity_lift"].passed is False
    assert by_name["hard_case_generated_success_diversity_bonus"].passed is False
    assert (
        by_name[
            "hard_case_generated_diversity_budget_sensitive_successes"
        ].passed
        is False
    )


def test_quality_gate_checks_generated_candidate_deduplication_evidence():
    payload = _passing_suite_payload()
    payload["settings"] = {"run_hard_case_generated_benchmark": True}
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=5,
        template_case_count=5,
        selected_candidate_count=5,
        selected_rule_count=5,
        selected_function_count=5,
        selected_source_count=5,
        average_candidate_score=23.5,
        average_diversity_bonus=1.0,
        target_signals=["without_candidate_deduplication"],
    )
    payload["hard_case_generated_benchmark"] = _hard_case_generated_benchmark_payload(
        case_count=5,
        patch_success_rate=1.0,
        multi_candidate_case_count=5,
        score_inversion_count=0,
        dedupe_affected_case_count=1,
        total_deduplicated_candidates=3,
        average_duplicate_pressure=0.20,
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["hard_case_generated_dedupe_affected_cases"].actual == "1"
    assert by_name["hard_case_generated_deduplicated_candidates"].actual == "3"
    assert by_name["hard_case_generated_duplicate_pressure"].actual == "0.2000"


def test_quality_gate_fails_generated_candidate_deduplication_without_budget_evidence():
    payload = _passing_suite_payload()
    payload["settings"] = {"run_hard_case_generated_benchmark": True}
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=5,
        template_case_count=5,
        selected_candidate_count=5,
        selected_rule_count=5,
        selected_function_count=5,
        selected_source_count=5,
        average_candidate_score=23.5,
        average_diversity_bonus=1.0,
        target_signals=["candidate_deduplication_pressure"],
    )
    payload["hard_case_generated_benchmark"] = _hard_case_generated_benchmark_payload(
        case_count=5,
        patch_success_rate=1.0,
        multi_candidate_case_count=5,
        score_inversion_count=0,
        dedupe_affected_case_count=0,
        total_deduplicated_candidates=0,
        average_duplicate_pressure=0.0,
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["hard_case_generated_dedupe_affected_cases"].passed is False
    assert by_name["hard_case_generated_deduplicated_candidates"].passed is False
    assert by_name["hard_case_generated_duplicate_pressure"].passed is False


def test_quality_gate_checks_generated_reflection_depth_evidence():
    payload = _passing_suite_payload()
    payload["settings"] = {"run_hard_case_generated_benchmark": True}
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=5,
        template_case_count=5,
        selected_candidate_count=5,
        selected_rule_count=3,
        selected_function_count=3,
        selected_source_count=3,
        average_candidate_score=23.5,
        average_diversity_bonus=1.0,
        target_signals=["reflection_depth"],
    )
    payload["hard_case_generated_benchmark"] = _hard_case_generated_benchmark_payload(
        case_count=5,
        patch_success_rate=1.0,
        multi_candidate_case_count=5,
        score_inversion_count=0,
        reflection_success_case_count=1,
        reflection_candidate_count=1,
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["hard_case_generated_reflection_success_cases"].actual == "1"
    assert by_name["hard_case_generated_reflection_candidates"].actual == "1"


def test_quality_gate_fails_generated_reflection_depth_without_depth_recovery():
    payload = _passing_suite_payload()
    payload["settings"] = {"run_hard_case_generated_benchmark": True}
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=5,
        template_case_count=5,
        selected_candidate_count=5,
        selected_rule_count=3,
        selected_function_count=3,
        selected_source_count=3,
        average_candidate_score=23.5,
        average_diversity_bonus=1.0,
        target_signals=["reflection_depth"],
    )
    payload["hard_case_generated_benchmark"] = _hard_case_generated_benchmark_payload(
        case_count=5,
        patch_success_rate=1.0,
        multi_candidate_case_count=5,
        score_inversion_count=0,
        reflection_success_case_count=0,
        reflection_candidate_count=0,
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["hard_case_generated_reflection_success_cases"].passed is False
    assert by_name["hard_case_generated_reflection_candidates"].passed is False


def test_quality_gate_checks_generated_slice_grounding_evidence():
    payload = _passing_suite_payload()
    payload["settings"] = {"run_hard_case_generated_benchmark": True}
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=5,
        template_case_count=5,
        selected_candidate_count=5,
        selected_rule_count=3,
        selected_function_count=3,
        selected_source_count=3,
        average_candidate_score=23.5,
        average_diversity_bonus=1.0,
        target_signals=["weak_failed_test_reachability"],
    )
    payload["hard_case_generated_benchmark"] = _hard_case_generated_benchmark_payload(
        case_count=5,
        patch_success_rate=1.0,
        multi_candidate_case_count=5,
        score_inversion_count=0,
    )
    first_case = payload["hard_case_generated_benchmark"]["benchmark_report"][
        "cases"
    ][0]
    first_case["metadata"]["hard_case_target_signals"] = [
        "weak_failed_test_reachability"
    ]
    first_case["localization_details"] = [
        {
            "rank": 1,
            "function_name": "service.target",
            "slice_grounding": {
                "grounded": True,
                "support_score": 0.91,
                "failed_test_reachability": 1.0,
                "call_chain_edge_coverage": 1.0,
            },
        }
    ]

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["hard_case_generated_slice_grounding"].passed
    assert by_name["hard_case_generated_slice_grounding"].actual == "1/1"


def test_quality_gate_fails_generated_slice_grounding_without_evidence():
    payload = _passing_suite_payload()
    payload["settings"] = {"run_hard_case_generated_benchmark": True}
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=5,
        template_case_count=5,
        selected_candidate_count=5,
        selected_rule_count=3,
        selected_function_count=3,
        selected_source_count=3,
        average_candidate_score=23.5,
        average_diversity_bonus=1.0,
        target_signals=["weak_failed_test_reachability"],
    )
    payload["hard_case_generated_benchmark"] = _hard_case_generated_benchmark_payload(
        case_count=5,
        patch_success_rate=1.0,
        multi_candidate_case_count=5,
        score_inversion_count=0,
    )
    first_case = payload["hard_case_generated_benchmark"]["benchmark_report"][
        "cases"
    ][0]
    first_case["metadata"]["hard_case_target_signals"] = [
        "weak_failed_test_reachability"
    ]
    first_case["localization_details"] = [
        {
            "rank": 1,
            "function_name": "service.target",
            "slice_grounding": {
                "grounded": False,
                "support_score": 0.20,
                "failed_test_reachability": 0.10,
                "call_chain_edge_coverage": 0.10,
            },
        }
    ]

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["hard_case_generated_slice_grounding"].passed is False
    assert by_name["hard_case_generated_slice_grounding"].actual == "0/1"
    assert by_name["hard_case_generated_slice_grounding"].details == [
        "missing_signals=weak_failed_test_reachability",
        (
            "generated_case_0:weak_failed_test_reachability "
            "grounded=False support=0.2000 reachability=0.1000 "
            "call_chain=0.1000"
        ),
    ]


def test_quality_gate_fails_generated_hard_case_benchmark_regression():
    payload = _passing_suite_payload()
    payload["settings"] = {"run_hard_case_generated_benchmark": True}
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=2,
        template_case_count=2,
        selected_candidate_count=4,
        selected_rule_count=4,
        selected_function_count=4,
        selected_source_count=4,
        average_candidate_score=23.5,
        average_diversity_bonus=3.0,
        target_signals=["search_score_inversion"],
    )
    payload["hard_case_generated_benchmark"] = _hard_case_generated_benchmark_payload(
        case_count=1,
        patch_success_rate=0.25,
        multi_candidate_case_count=0,
        score_inversion_count=0,
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["hard_case_generated_benchmark_cases"].passed is False
    assert by_name["hard_case_generated_patch_success_rate"].passed is False
    assert by_name["hard_case_generated_multi_candidate_cases"].passed is False
    assert by_name["hard_case_generated_benchmark_count"].passed is False
    assert by_name["hard_case_generated_score_inversions"].passed is False


def test_quality_gate_requires_generated_benchmark_when_configured():
    payload = _passing_suite_payload()
    payload["settings"] = {"run_hard_case_generated_benchmark": True}
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=1,
        template_case_count=1,
        selected_candidate_count=1,
        selected_rule_count=1,
        selected_function_count=1,
        selected_source_count=1,
        average_candidate_score=23.5,
        average_diversity_bonus=0.0,
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["hard_case_generated_benchmark"].passed is False


def test_quality_gate_accepts_empty_hard_case_generation_when_consistent():
    payload = _passing_suite_payload()
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=0,
        template_case_count=0,
        selected_candidate_count=0,
        selected_rule_count=0,
        selected_function_count=0,
        selected_source_count=0,
        average_candidate_score=0.0,
        average_diversity_bonus=0.0,
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed
    assert by_name["hard_case_generation_count"].actual == "0/0"
    assert by_name["hard_case_generation_empty_consistency"].passed


def test_quality_gate_fails_weak_hard_case_generation_audit():
    payload = _passing_suite_payload()
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=1,
        template_case_count=2,
        selected_candidate_count=0,
        selected_rule_count=0,
        selected_function_count=0,
        selected_source_count=0,
        average_candidate_score=0.0,
        average_diversity_bonus=0.0,
    )

    result = evaluate_quality_gate(payload)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["hard_case_generation_count"].passed is False
    assert by_name["hard_case_generation_selected_candidates"].passed is False
    assert by_name["hard_case_generation_rule_coverage"].passed is False
    assert by_name["hard_case_generation_function_coverage"].passed is False
    assert by_name["hard_case_generation_source_coverage"].passed is False
    assert by_name["hard_case_generation_candidate_score"].passed is False
    assert (
        by_name["hard_case_generation_provenance_selected_ratio"].passed
        is False
    )
    assert by_name["hard_case_generation_provenance_bonus"].passed is False
    assert (
        by_name["hard_case_generation_provenance_source_sha256"].passed
        is False
    )
    assert by_name["hard_case_generation_provenance_stable_ref"].passed is False


def test_quality_gate_supports_custom_hard_case_generation_thresholds():
    payload = _passing_suite_payload()
    payload["hard_case_generation"] = _hard_case_generation_payload(
        generated_count=2,
        template_case_count=2,
        selected_candidate_count=4,
        selected_rule_count=2,
        selected_function_count=2,
        selected_source_count=2,
        average_candidate_score=0.5,
        average_diversity_bonus=0.1,
    )
    thresholds = QualityGateThresholds(
        min_hard_case_generation_selected_candidates_per_case=3,
        min_hard_case_generation_rule_coverage=3,
        min_hard_case_generation_function_coverage=3,
        min_hard_case_generation_source_coverage=3,
        min_hard_case_generation_candidate_score=0.8,
        min_hard_case_generation_diversity_bonus=0.2,
        min_hard_case_generation_provenance_bonus=1.2,
        max_hard_case_generation_provenance_leakage_risk=-0.01,
    )

    result = evaluate_quality_gate(payload, thresholds=thresholds)
    by_name = {check.name: check for check in result.checks}

    assert result.passed is False
    assert by_name["hard_case_generation_selected_candidates"].passed is False
    assert by_name["hard_case_generation_selected_candidates"].expected == ">= 6"
    assert by_name["hard_case_generation_rule_coverage"].passed is False
    assert by_name["hard_case_generation_rule_coverage"].expected == ">= 3"
    assert by_name["hard_case_generation_function_coverage"].passed is False
    assert by_name["hard_case_generation_source_coverage"].passed is False
    assert by_name["hard_case_generation_candidate_score"].passed is False
    assert by_name["hard_case_generation_candidate_score"].expected == ">= 0.8000"
    assert by_name["hard_case_generation_diversity_bonus"].passed is False
    assert by_name["hard_case_generation_diversity_bonus"].expected == ">= 0.2000"
    assert by_name["hard_case_generation_provenance_bonus"].passed is False
    assert (
        by_name["hard_case_generation_provenance_bonus"].expected
        == ">= 1.2000"
    )
    assert by_name["hard_case_generation_provenance_leakage_risk"].passed is False
    assert (
        by_name["hard_case_generation_provenance_leakage_risk"].expected
        == "<= -0.01"
    )


def test_quality_gate_accepts_benchmark_only_artifact_without_patch_weight_search():
    payload = _passing_suite_payload()["benchmark_report"]

    result = evaluate_quality_gate(payload)
    check_names = {check.name for check in result.checks}

    assert result.passed
    assert "patch_score_weight_top1_success" not in check_names


def test_quality_gate_supports_custom_thresholds_and_markdown():
    payload = _passing_suite_payload()
    thresholds = QualityGateThresholds(min_cases=1, min_top1=1.0, min_top3=1.0)
    result = evaluate_quality_gate(payload, thresholds=thresholds)
    markdown = render_quality_gate_markdown(result)

    assert result.passed
    assert "# Quality Gate" in markdown
    assert "PASS" in markdown


def test_quality_gate_cli_exit_codes_and_json_output():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        passing = root / "passing.json"
        failing = root / "failing.json"
        output_json = root / "gate.json"
        passing.write_text(json.dumps(_passing_suite_payload()), encoding="utf-8")
        failing_payload = _passing_suite_payload()
        failing_payload["benchmark_report"]["summary"]["patch_success_rate"] = 0.1
        failing.write_text(json.dumps(failing_payload), encoding="utf-8")

        passed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.quality_gate",
                str(passing),
                "--format",
                "json",
                "--output-json",
                str(output_json),
                "--min-hard-case-generation-selected-candidates-per-case",
                "2",
                "--min-hard-case-generation-diversity-bonus",
                "0.25",
                "--max-patch-judge-fusion-validation-regression",
                "0.03",
                "--max-patch-judge-fusion-top1-regression",
                "0.04",
                "--max-patch-judge-fusion-mrr-regression",
                "0.05",
                "--max-patch-judge-fusion-success-margin-regression",
                "0.06",
                "--max-patch-judge-fusion-first-success-rank-regression",
                "0.30",
                "--min-metric-uncertainty-top1-lower",
                "0.60",
                "--min-metric-uncertainty-map-lower",
                "0.45",
                "--min-metric-uncertainty-patch-success-lower",
                "0.40",
                "--max-search-budget-first-success-rank-p90",
                "4",
                "--min-search-competition-multi-candidate-cases",
                "3",
                "--min-search-competition-multi-candidate-rule-diversity",
                "1.25",
                "--min-search-competition-multi-candidate-failure-type-diversity",
                "0.70",
                "--min-search-competition-multi-candidate-retention-bucket-diversity",
                "1.40",
                "--min-hard-case-generation-provenance-selected-ratio",
                "0.75",
                "--min-hard-case-generation-provenance-bonus",
                "0.35",
                "--min-hard-case-generation-provenance-source-sha-coverage",
                "0.85",
                "--min-hard-case-generation-provenance-stable-ref-coverage",
                "0.90",
                "--max-hard-case-generation-provenance-leakage-risk",
                "0.40",
                "--max-localization-holdout-calibrated-ece",
                "0.08",
                "--min-slice-grounded-case-ratio",
                "0.80",
                "--min-average-top1-slice-support",
                "0.60",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        failed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.quality_gate",
                str(failing),
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert passed.returncode == 0
        passed_report = json.loads(passed.stdout)
        output_report = json.loads(output_json.read_text(encoding="utf-8"))
        assert passed_report["passed"] is True
        assert (
            passed_report["thresholds"][
                "min_hard_case_generation_selected_candidates_per_case"
            ]
            == 2
        )
        assert (
            passed_report["thresholds"]["min_hard_case_generation_diversity_bonus"]
            == 0.25
        )
        assert (
            passed_report["thresholds"][
                "max_patch_judge_fusion_validation_regression"
            ]
            == 0.03
        )
        assert (
            passed_report["thresholds"]["max_patch_judge_fusion_top1_regression"]
            == 0.04
        )
        assert (
            passed_report["thresholds"]["max_patch_judge_fusion_mrr_regression"]
            == 0.05
        )
        assert (
            passed_report["thresholds"][
                "max_patch_judge_fusion_success_margin_regression"
            ]
            == 0.06
        )
        assert (
            passed_report["thresholds"][
                "max_patch_judge_fusion_first_success_rank_regression"
            ]
            == 0.30
        )
        assert (
            passed_report["thresholds"]["min_metric_uncertainty_top1_lower"]
            == 0.60
        )
        assert (
            passed_report["thresholds"]["min_metric_uncertainty_map_lower"]
            == 0.45
        )
        assert (
            passed_report["thresholds"][
                "min_metric_uncertainty_patch_success_lower"
            ]
            == 0.40
        )
        assert (
            passed_report["thresholds"][
                "max_search_budget_first_success_rank_p90"
            ]
            == 4.0
        )
        assert (
            passed_report["thresholds"][
                "min_search_competition_multi_candidate_cases"
            ]
            == 3
        )
        assert (
            passed_report["thresholds"][
                "min_search_competition_multi_candidate_rule_diversity"
            ]
            == 1.25
        )
        assert (
            passed_report["thresholds"][
                "min_search_competition_multi_candidate_failure_type_diversity"
            ]
            == 0.70
        )
        assert (
            passed_report["thresholds"][
                "min_search_competition_multi_candidate_retention_bucket_diversity"
            ]
            == 1.40
        )
        assert (
            passed_report["thresholds"][
                "min_hard_case_generation_provenance_selected_ratio"
            ]
            == 0.75
        )
        assert (
            passed_report["thresholds"][
                "min_hard_case_generation_provenance_bonus"
            ]
            == 0.35
        )
        assert (
            passed_report["thresholds"][
                "min_hard_case_generation_provenance_source_sha_coverage"
            ]
            == 0.85
        )
        assert (
            passed_report["thresholds"][
                "min_hard_case_generation_provenance_stable_ref_coverage"
            ]
            == 0.90
        )
        assert (
            passed_report["thresholds"][
                "max_hard_case_generation_provenance_leakage_risk"
            ]
            == 0.40
        )
        assert (
            passed_report["thresholds"][
                "max_localization_holdout_calibrated_ece"
            ]
            == 0.08
        )
        assert passed_report["thresholds"]["min_slice_grounded_case_ratio"] == 0.80
        assert passed_report["thresholds"]["min_average_top1_slice_support"] == 0.60
        assert output_report["passed"] is True
        assert failed.returncode == 1
        assert json.loads(failed.stdout)["passed"] is False


def _passing_suite_payload() -> dict:
    case = {
        "case_name": "case_0",
        "patch_success": True,
        "localization_details": [
            {
                "rank": 1,
                "signals": {
                    "sbfl": 1.0,
                    "graph": 0.8,
                    "static": 0.7,
                    "risk": 0.0,
                    "llm": 0.0,
                },
            }
        ],
        "patch_search_results": [],
        "beam_search_results": [
            {
                "success": True,
                "failure_type": "success",
                "score": 1.0,
            }
        ],
        "multi_patch_results": [],
    }
    cases = [
        {
            **case,
            "case_name": f"case_{index}",
        }
        for index in range(50)
    ]
    return {
        "settings": {
            "run_patch_weight_search": True,
        },
        "benchmark_report": {
            "summary": {
                "case_count": 50,
                "top1": 1.0,
                "top3": 1.0,
                "patch_success_rate": 1.0,
                "slice_grounded_case_count": 50,
                "average_top1_slice_support": 0.95,
                "average_top1_slice_failed_test_reachability": 1.0,
                "average_top1_slice_call_chain_coverage": 1.0,
            },
            "cases": cases,
        },
        "patch_weight_search_results": [
            {
                "profile": "default",
                "validation_score": 1.0,
                "top1_success": 1.0,
                "mrr": 1.0,
                "average_first_success_rank": 1.0,
                "average_success_score_margin": 0.2,
                "case_count": 50,
                "pareto_optimal": True,
                "dominates_count": 0,
                "dominated_by_count": 0,
                "weights": {
                    "tests_passed": 0.6,
                    "localization": 0.2,
                    "static_check": 0.1,
                    "execution_feedback": 0.08,
                    "diff_penalty": 0.06,
                    "risk_penalty": 0.03,
                    "warning_penalty": 0.01,
                    "success_bonus": 0.12,
                },
            }
        ],
    }


def _final_score_weight_result(
    *,
    robust_score: float,
    source_group_count: int,
    top1_gap: float,
    map_gap: float,
    profile: str = "default",
    top1: float = 1.0,
    pareto_optimal: bool = True,
) -> dict:
    source_groups = {
        f"repo_{index}": {
            "case_count": 1,
            "top1": top1,
            "top3": 1.0,
            "mrr": 1.0,
            "map": 1.0,
            "ndcg_at_3": 1.0,
            "mean_exam_score": 0.0,
            "validation_score": min(1.0, robust_score + 0.02),
        }
        for index in range(source_group_count)
    }
    holdout_splits = [
        {
            "holdout_group": f"repo_{index}",
            "train_groups": [
                f"repo_{other}"
                for other in range(source_group_count)
                if other != index
            ],
            "train_metrics": {
                "case_count": max(0, source_group_count - 1),
                "top1": max(0.0, top1 - top1_gap),
                "map": max(0.0, 1.0 - map_gap),
            },
            "holdout_metrics": {
                "case_count": 1,
                "top1": top1,
                "map": 1.0,
            },
            "top1_gap": top1_gap,
            "map_gap": map_gap,
        }
        for index in range(source_group_count)
        if source_group_count > 1
    ]
    return {
        "profile": profile,
        "coverage_weights": {
            "sbfl": 0.30,
            "graph": 0.25,
            "static": 0.15,
            "semantic": 0.10,
            "llm": 0.15,
            "risk": 0.05,
        },
        "static_only_weights": {
            "sbfl": 0.0,
            "graph": 0.20,
            "static": 0.60,
            "semantic": 0.10,
            "llm": 0.10,
            "risk": 0.0,
        },
        "validation_score": min(1.0, robust_score + 0.02),
        "robust_validation_score": robust_score,
        "source_group_count": source_group_count,
        "min_source_group_cases": 1,
        "source_groups": source_groups,
        "holdout_splits": holdout_splits,
        "max_top1_gap": top1_gap,
        "max_map_gap": map_gap,
        "pareto_optimal": pareto_optimal,
        "dominates_count": 3 if pareto_optimal else 0,
        "dominated_by_count": 0 if pareto_optimal else 1,
        "top1": top1,
        "top3": 1.0,
        "mrr": 1.0,
        "map": 1.0,
        "ndcg_at_3": 1.0,
        "mean_exam_score": 0.0,
        "case_count": 50,
    }


def _metric_uncertainty_payload(
    case_count: int,
    top1_width: float,
    map_width: float,
    patch_width: float,
) -> dict:
    return {
        "case_count": case_count,
        "confidence_level": 0.95,
        "bootstrap_samples": 1000,
        "seed": 1729,
        "metrics": {
            "top1": {
                "metric": "top1",
                "mean": 1.0,
                "lower": 1.0 - top1_width,
                "upper": 1.0,
                "width": top1_width,
                "sample_count": case_count,
            },
            "map": {
                "metric": "map",
                "mean": 1.0,
                "lower": 1.0 - map_width,
                "upper": 1.0,
                "width": map_width,
                "sample_count": case_count,
            },
            "patch_success_rate": {
                "metric": "patch_success_rate",
                "mean": 1.0,
                "lower": 1.0 - patch_width,
                "upper": 1.0,
                "width": patch_width,
                "sample_count": case_count,
            },
        },
    }


def _localization_calibration_payload(
    *,
    case_count: int,
    calibrated_ece: float,
    holdout_splits: int,
    min_train_cases: int,
    max_holdout_ece: float,
) -> dict:
    holdout_rows = []
    for index in range(holdout_splits):
        train_cases = min_train_cases + index
        holdout_ece = max_holdout_ece if index == 0 else max(0.0, max_holdout_ece - 0.01)
        holdout_rows.append(
            {
                "holdout_group": f"repo_{index}",
                "train_case_count": train_cases,
                "holdout_case_count": 2,
                "holdout_top1_accuracy": 1.0,
                "holdout_average_confidence": 0.6,
                "holdout_brier_score": 0.16,
                "holdout_expected_calibration_error": 0.40,
                "holdout_calibrated_average_confidence": 0.95,
                "holdout_calibrated_brier_score": 0.02,
                "holdout_calibrated_expected_calibration_error": holdout_ece,
                "brier_score_improvement": 0.14,
                "expected_calibration_error_improvement": 0.30,
            }
        )
    return {
        "case_count": case_count,
        "top1_positive_count": case_count,
        "top1_accuracy": 1.0,
        "brier_score": 0.18,
        "expected_calibration_error": 0.42,
        "calibrated_brier_score": 0.01,
        "calibrated_expected_calibration_error": calibrated_ece,
        "source_group_holdout_splits": holdout_rows,
    }


def _search_budget_analysis_payload(
    *,
    evaluated_case_count: int,
    success_at_1: float,
    budget_auc: float,
    first_success_rank_p90: float = 1.0,
    dedupe_affected_case_count: int | None = None,
    total_deduplicated_candidates: int | None = None,
    average_duplicate_pressure: float | None = None,
) -> dict:
    payload = {
        "case_count": evaluated_case_count,
        "evaluated_case_count": evaluated_case_count,
        "successful_case_count": int(round(evaluated_case_count * success_at_1)),
        "max_budget": 3,
        "budget_auc": budget_auc,
        "success_at_budget": {
            "1": success_at_1,
            "2": max(success_at_1, budget_auc),
            "3": max(success_at_1, budget_auc),
        },
        "first_success_rank_p50": min(first_success_rank_p90, 1.0),
        "first_success_rank_p90": first_success_rank_p90,
        "average_normalized_effort": 0.5,
        "average_wasted_nodes_after_success": 0.2,
        "budget_points": [],
        "rows": [],
    }
    if dedupe_affected_case_count is not None:
        payload["dedupe_affected_case_count"] = dedupe_affected_case_count
    if total_deduplicated_candidates is not None:
        payload["total_deduplicated_candidates"] = total_deduplicated_candidates
    if average_duplicate_pressure is not None:
        payload["average_duplicate_pressure"] = average_duplicate_pressure
    return payload


def _search_competition_analysis_payload(
    *,
    multi_candidate_case_count: int,
    rule_diversity: float,
    failure_type_diversity: float,
    retention_bucket_diversity: float,
    diversity_assisted_successes: int = 2,
    average_success_diversity_lift: float = 1.5,
    average_success_diversity_bonus: float = 0.25,
) -> dict:
    return {
        "case_count": 50,
        "beam_case_count": 50,
        "multi_candidate_case_count": multi_candidate_case_count,
        "successful_case_count": 45,
        "top_rank_success_count": 40,
        "score_inversion_count": 2,
        "score_inversion_rate": 0.0444,
        "average_failure_pressure": 0.20,
        "average_rule_diversity": 1.10,
        "average_failure_type_diversity": 0.70,
        "average_retention_bucket_diversity": 1.20,
        "multi_candidate_average_rule_diversity": rule_diversity,
        "multi_candidate_average_failure_type_diversity": failure_type_diversity,
        "multi_candidate_average_retention_bucket_diversity": (
            retention_bucket_diversity
        ),
        "average_competing_failures_before_success": 0.20,
        "diversity_lift_case_count": max(0, diversity_assisted_successes),
        "diversity_lift_case_rate": 0.04 if diversity_assisted_successes else 0.0,
        "diversity_assisted_success_count": diversity_assisted_successes,
        "diversity_assisted_success_rate": (
            round(diversity_assisted_successes / 45, 4)
            if diversity_assisted_successes
            else 0.0
        ),
        "average_diversity_lift": average_success_diversity_lift,
        "average_success_diversity_lift": average_success_diversity_lift,
        "average_diversity_bonus": average_success_diversity_bonus,
        "average_success_diversity_bonus": average_success_diversity_bonus,
        "max_diversity_lift": int(average_success_diversity_lift),
        "top_failure_type_counts": {"test_failure": 2},
        "rows": [],
    }


def _difficulty_report_payload(
    *,
    case_count: int,
    medium: int,
    hard: int,
    cross_file: int,
    patch_competition: int,
    cross_function_data_flow: int,
) -> dict:
    return {
        "case_count": case_count,
        "bucket_counts": {
            "easy": max(0, case_count - medium - hard),
            "medium": medium,
            "hard": hard,
        },
        "bucket_metrics": {},
        "label_counts": {
            "cross_file_patch": cross_file,
            "cross_function_data_flow": cross_function_data_flow,
            "patch_candidate_competition": patch_competition,
        },
        "label_metrics": {},
        "cases": [],
    }


def _diversity_metrics_payload(
    *,
    bug_type_count: int,
    expected_rule_count: int,
    cases_per_item: int = 1,
) -> dict:
    return {
        "bug_type_metrics": {
            f"bug_type_{index}": {"case_count": cases_per_item}
            for index in range(bug_type_count)
        },
        "rule_metrics": {
            f"rule_{index}": {"case_count": cases_per_item}
            for index in range(expected_rule_count)
        },
    }


def _generalization_report_payload(
    *,
    case_count: int,
    source_group_count: int,
    min_holdout_case_count: int,
    gap: float,
    holdout_split_count: int,
) -> dict:
    source_balance_entropy = 1.0 if source_group_count >= 3 else 0.25
    worst_holdout_gap_score = gap
    stability_score = max(0.0, 1.0 - worst_holdout_gap_score)
    return {
        "case_count": case_count,
        "split_key": "upstream",
        "source_group_count": source_group_count,
        "source_groups": {
            f"repo_{index}": {"case_count": min_holdout_case_count}
            for index in range(source_group_count)
        },
        "holdout_splits": [
            {
                "holdout_group": f"repo_{index}",
                "train_groups": [],
                "train_metrics": {"case_count": case_count - min_holdout_case_count},
                "holdout_metrics": {"case_count": min_holdout_case_count},
                "top1_gap": gap,
                "map_gap": gap,
                "patch_success_gap": gap,
                "search_efficiency_gap": gap,
            }
            for index in range(holdout_split_count)
        ],
        "min_holdout_case_count": min_holdout_case_count,
        "source_balance_entropy": source_balance_entropy,
        "source_imbalance_ratio": 1.0 if source_group_count >= 3 else 4.0,
        "max_top1_gap": gap,
        "max_map_gap": gap,
        "max_patch_success_gap": gap,
        "max_search_efficiency_gap": gap,
        "worst_holdout_group": "repo_0" if holdout_split_count else "",
        "worst_holdout_gap_score": worst_holdout_gap_score,
        "stability_score": stability_score,
        "risk_level": "low" if worst_holdout_gap_score < 0.15 else "high",
    }


def _benchmark_provenance_payload(
    *,
    case_coverage: float,
    mutation_coverage: float,
    sha_coverage: float,
    duplicate_signatures: int,
    source_concentration: float,
    leakage_risk: float,
    sha_present: int,
    stable_ref_coverage: float = 1.0,
    license_coverage: float | None = None,
) -> dict:
    return {
        "case_count": 50,
        "source_group_count": 4,
        "source_ref_count": 50,
        "source_sha256_present_count": sha_present,
        "stable_ref_count": int(round(stable_ref_coverage * 50)),
        "floating_ref_count": 50 - int(round(stable_ref_coverage * 50)),
        "case_provenance_coverage": case_coverage,
        "source_sha256_coverage": sha_coverage,
        "stable_ref_coverage": stable_ref_coverage,
        "license_coverage": (
            case_coverage if license_coverage is None else license_coverage
        ),
        "materialized_mutation_coverage": mutation_coverage,
        "duplicate_case_name_count": 0,
        "duplicate_signature_count": duplicate_signatures,
        "duplicate_signature_case_count": duplicate_signatures * 2,
        "max_source_group_case_share": min(source_concentration, 1.0),
        "max_source_file_case_share": source_concentration,
        "leakage_risk_score": leakage_risk,
        "risk_level": "low" if leakage_risk < 0.15 else "high",
        "missing_provenance_cases": [],
        "missing_sha256_sources": [],
        "floating_ref_sources": [],
        "duplicate_signatures": [],
        "source_groups": {"repo/a": 25, "repo/b": 25},
        "top_source_files": [],
    }


def _hard_case_generation_payload(
    *,
    generated_count: int,
    template_case_count: int,
    selected_candidate_count: int,
    selected_rule_count: int,
    selected_function_count: int,
    selected_source_count: int,
    average_candidate_score: float,
    average_diversity_bonus: float,
    target_signals: list[str] | None = None,
    provenance_bonus: float = 1.0,
    provenance_source_sha256: float = 1.0,
    provenance_stable_ref: float = 1.0,
    provenance_leakage_risk: float = 0.0,
) -> dict:
    target_signals = target_signals or []
    selected_candidate_ids = [
        f"candidate_{index}"
        for index in range(selected_candidate_count)
    ]
    selected_candidate_reasons = {}
    if selected_candidate_ids:
        selected_candidate_reasons = {
            selected_candidate_ids[0]: [
                "rule_overlap=rule_0",
                f"provenance_bonus={provenance_bonus:.4f}",
                (
                    "provenance_metrics="
                    "case_provenance=1.0000;"
                    f"source_sha256={provenance_source_sha256:.4f};"
                    f"stable_ref={provenance_stable_ref:.4f};"
                    "license=1.0000;"
                    f"leakage_risk={provenance_leakage_risk:.4f}"
                ),
            ]
        }
    return {
        "suggestion_count": 1 if generated_count else 0,
        "generated_count": generated_count,
        "skipped_count": 0,
        "rows": [
            {
                "target_signal": signal,
                "status": "generated",
            }
            for signal in target_signals
        ],
        "template": {
            "cases": [
                {
                    "name": f"generated_hard_case_{index}",
                    "benchmark": {
                        "metadata": {
                            "hard_case_target_signals": target_signals,
                            "expected_score_inversion": (
                                "search_score_inversion" in target_signals
                            ),
                            "hard_case_selected_candidate_ids": (
                                selected_candidate_ids[:1]
                            ),
                            "hard_case_selected_candidate_reasons": (
                                selected_candidate_reasons
                            ),
                        }
                    },
                }
                for index in range(template_case_count)
            ]
        },
        "selection_audit": {
            "selected_candidate_count": selected_candidate_count,
            "selected_candidate_ids": selected_candidate_ids,
            "selected_rule_count": selected_rule_count,
            "selected_rules": [
                f"rule_{index}"
                for index in range(selected_rule_count)
            ],
            "selected_function_count": selected_function_count,
            "selected_functions": [
                f"function_{index}"
                for index in range(selected_function_count)
            ],
            "selected_source_count": selected_source_count,
            "selected_sources": [
                f"source_{index}.py"
                for index in range(selected_source_count)
            ],
            "average_candidate_score": average_candidate_score,
            "average_diversity_bonus": average_diversity_bonus,
            "max_diversity_bonus": average_diversity_bonus,
            "target_signals": target_signals,
        },
    }


def _ablation_row(variant: str, impact_score: float) -> dict:
    return {
        "variant": variant,
        "impact_score": impact_score,
        "direction": "regression" if impact_score < 0 else "neutral",
        "dominant_signal": "patch_success",
        "dominant_contribution": impact_score,
        "regression_signal_count": 1 if impact_score < 0 else 0,
        "improvement_signal_count": 0,
        "signal_contributions": {
            "patch_success": impact_score,
        },
    }


def _hard_case_generated_benchmark_payload(
    *,
    case_count: int,
    patch_success_rate: float,
    multi_candidate_case_count: int,
    score_inversion_count: int,
    diversity_assisted_success_count: int = 0,
    average_success_diversity_lift: float = 0.0,
    average_success_diversity_bonus: float = 0.0,
    dedupe_affected_case_count: int = 0,
    total_deduplicated_candidates: int = 0,
    average_duplicate_pressure: float = 0.0,
    reflection_success_case_count: int = 0,
    reflection_candidate_count: int = 0,
) -> dict:
    cases = []
    for index in range(case_count):
        metadata = {
            "expected_score_inversion": score_inversion_count > 0,
            "expected_diversity_reranking": diversity_assisted_success_count > 0,
            "expected_candidate_deduplication": dedupe_affected_case_count > 0,
            "expected_reflection_depth": reflection_candidate_count > 0,
        }
        case = {
            "case_name": f"generated_case_{index}",
            "metadata": metadata,
        }
        if diversity_assisted_success_count > 0 and index == 0:
            case["metadata"] = {
                **metadata,
                "expected_diversity_assisted_success": True,
                "hard_case_target_signal": "search_diversity_reranking",
            }
            case["search_analysis"] = {"evaluated_nodes": 4}
            case["beam_search_results"] = [
                {
                    "rule_id": "diversity_success_rule",
                    "variant": "diversity_success",
                    "success": True,
                    "search_diversity": {
                        "base_rank": 5,
                        "rank": 2,
                        "bonus": average_success_diversity_bonus,
                    },
                },
                {
                    "rule_id": "diversity_duplicate_decoy_rule",
                    "variant": "diversity_duplicate_decoy",
                    "success": False,
                },
            ]
        if reflection_candidate_count > 0 and index == 0:
            case["metadata"] = {
                **case["metadata"],
                "hard_case_target_signal": "reflection_depth",
                "patch_score_profile": "reflection_depth_probe",
            }
            case["beam_search_results"] = [
                {
                    "candidate_id": "reflection_seed",
                    "variant": "reflection_seed_variant",
                    "success": False,
                    "depth": 0,
                },
                {
                    "candidate_id": "reflection_depth_1",
                    "parent_id": "reflection_seed",
                    "variant": "reflection_success_variant",
                    "success": reflection_success_case_count > 0,
                    "depth": 1,
                },
            ]
        if dedupe_affected_case_count > 0 and index == 0:
            case["metadata"] = {
                **case["metadata"],
                "hard_case_target_signal": "without_candidate_deduplication",
                "patch_score_profile": "candidate_deduplication_probe",
                "target_benchmark_signals": ["candidate_deduplication_pressure"],
            }
        cases.append(case)
    return {
        "benchmark_report": {
            "summary": {
                "case_count": case_count,
                "patch_success_rate": patch_success_rate,
                "search_competition_analysis": {
                    "multi_candidate_case_count": multi_candidate_case_count,
                    "score_inversion_count": score_inversion_count,
                    "diversity_assisted_success_count": (
                        diversity_assisted_success_count
                    ),
                    "average_success_diversity_lift": (
                        average_success_diversity_lift
                    ),
                    "average_success_diversity_bonus": (
                        average_success_diversity_bonus
                    ),
                },
                "reflection_analysis": {
                    "reflection_success_case_count": (
                        reflection_success_case_count
                    ),
                    "reflection_candidate_count": reflection_candidate_count,
                },
                "search_budget_analysis": {
                    "dedupe_affected_case_count": dedupe_affected_case_count,
                    "total_deduplicated_candidates": total_deduplicated_candidates,
                    "average_duplicate_pressure": average_duplicate_pressure,
                },
            },
            "cases": cases,
        }
    }


def _benchmark_mining_payload(
    *,
    judged_candidate_count: int,
    cluster_count: int,
    suggestion_count: int,
    template_seed_count: int,
    preview_case_count: int,
) -> dict:
    return {
        "source_path": "suite.json",
        "judged_candidate_count": judged_candidate_count,
        "cluster_count": cluster_count,
        "suggestion_count": suggestion_count,
        "suggestions": [
            {
                "priority": "high",
                "benchmark_focus": "judge false-positive hardening",
                "failure_type": "test_failure",
                "pattern": "capped_by_execution_evidence",
                "suggested_case_shape": "Add near-miss semantic repair cases.",
                "rationale": "Synthetic mining fixture.",
                "evidence_count": 1,
                "examples": ["case#1:patch"],
            }
            for _ in range(suggestion_count)
        ],
        "template_seeds": [
            {
                "seed_name": f"seed_{index}",
                "priority": "high",
                "benchmark_focus": "judge false-positive hardening",
                "failure_type": "test_failure",
                "pattern": "capped_by_execution_evidence",
                "template_case": {"name": f"seed_{index}"},
            }
            for index in range(template_seed_count)
        ],
        "template_seed_preview": {
            "cases": [
                {"name": f"seed_{index}"}
                for index in range(preview_case_count)
            ]
        },
    }
