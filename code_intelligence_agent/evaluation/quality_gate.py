from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.generated_ablation_links import (
    SLICE_GROUNDING_TARGET_SIGNALS,
    ablation_variants_for_generated_signal,
)
from code_intelligence_agent.evaluation.generated_diversity_evidence import (
    generated_diversity_budget_evidence,
)


@dataclass(frozen=True)
class QualityGateThresholds:
    min_cases: int = 50
    min_top1: float = 0.65
    min_top3: float = 0.85
    min_patch_success_rate: float = 0.50
    min_slice_grounded_case_ratio: float = 0.90
    min_average_top1_slice_support: float = 0.70
    min_average_top1_slice_failed_test_reachability: float = 0.70
    min_average_top1_slice_call_chain_coverage: float = 0.70
    min_weight_search_top1: float = 0.50
    min_weight_search_robust_score: float = 0.50
    min_weight_search_source_groups: int = 1
    max_weight_search_top1_gap: float = 0.20
    max_weight_search_map_gap: float = 0.20
    min_patch_weight_top1_success: float = 0.50
    min_patch_weight_mrr: float = 0.50
    min_patch_feedback_weight: float = 0.01
    min_llm_judge_cases: int = 1
    max_llm_judge_brier_score: float = 0.25
    max_llm_judge_ece: float = 0.20
    min_llm_judge_agreement_rate: float = 0.70
    min_patch_judge_candidates: int = 1
    max_patch_judge_brier_score: float = 0.35
    max_patch_judge_ece: float = 0.35
    min_patch_judge_agreement_rate: float = 0.50
    max_patch_judge_fusion_validation_regression: float = 0.02
    max_patch_judge_fusion_top1_regression: float = 0.05
    max_patch_judge_fusion_mrr_regression: float = 0.05
    max_patch_judge_fusion_success_margin_regression: float = 0.05
    max_patch_judge_fusion_first_success_rank_regression: float = 0.25
    min_search_budget_cases: int = 1
    min_search_budget_success_at_1: float = 0.50
    min_search_budget_auc: float = 0.50
    max_search_budget_first_success_rank_p90: float = 3.00
    min_search_budget_dedupe_affected_cases: int = 0
    min_search_budget_deduplicated_candidates: int = 0
    min_search_budget_average_duplicate_pressure: float = 0.0
    min_search_competition_multi_candidate_cases: int = 1
    min_search_competition_multi_candidate_rule_diversity: float = 1.00
    min_search_competition_multi_candidate_failure_type_diversity: float = 0.50
    min_search_competition_multi_candidate_retention_bucket_diversity: float = 1.00
    min_search_competition_diversity_assisted_successes: int = 0
    min_search_competition_average_success_diversity_lift: float = 0.0
    min_search_competition_average_success_diversity_bonus: float = 0.0
    min_metric_uncertainty_cases: int = 1
    max_metric_uncertainty_top1_width: float = 0.40
    max_metric_uncertainty_map_width: float = 0.40
    max_metric_uncertainty_patch_success_width: float = 0.40
    min_metric_uncertainty_top1_lower: float = 0.65
    min_metric_uncertainty_map_lower: float = 0.50
    min_metric_uncertainty_patch_success_lower: float = 0.50
    min_localization_calibration_cases: int = 1
    max_localization_calibrated_ece: float = 0.10
    min_localization_source_holdout_splits: int = 1
    min_localization_holdout_train_cases: int = 1
    max_localization_holdout_calibrated_ece: float = 0.10
    min_localization_attribution_coverage: float = 0.95
    max_localization_attribution_fragile_rate: float = 0.90
    max_localization_attribution_counterfactual_flip_rate: float = 0.90
    max_localization_attribution_reconstruction_error: float = 0.50
    min_ablation_impact_variants: int = 1
    min_ablation_regression_count: int = 1
    min_ablation_abs_impact_score: float = 0.05
    min_difficulty_medium_cases: int = 1
    min_difficulty_hard_cases: int = 1
    min_difficulty_cross_file_patch_cases: int = 1
    min_difficulty_patch_competition_cases: int = 1
    min_difficulty_cross_function_data_flow_cases: int = 1
    min_bug_type_count: int = 6
    min_expected_rule_count: int = 6
    min_cases_per_bug_type: int = 1
    min_cases_per_expected_rule: int = 1
    min_generalization_source_groups: int = 3
    min_generalization_holdout_cases: int = 1
    min_generalization_balance_entropy: float = 0.50
    max_generalization_top1_gap: float = 0.20
    max_generalization_map_gap: float = 0.20
    max_generalization_patch_success_gap: float = 0.20
    max_generalization_search_efficiency_gap: float = 0.40
    max_generalization_worst_holdout_gap_score: float = 0.30
    min_generalization_stability_score: float = 0.70
    min_benchmark_provenance_case_coverage: float = 0.95
    min_benchmark_provenance_mutation_coverage: float = 0.95
    min_benchmark_provenance_source_sha_coverage: float = 0.95
    min_benchmark_provenance_stable_ref_coverage: float = 0.95
    min_benchmark_provenance_license_coverage: float = 0.95
    max_benchmark_provenance_duplicate_signatures: int = 0
    max_benchmark_provenance_source_concentration: float = 0.80
    max_benchmark_provenance_leakage_risk_score: float = 0.30
    min_hard_case_generation_selected_candidates_per_case: int = 1
    min_hard_case_generation_rule_coverage: int = 3
    min_hard_case_generation_function_coverage: int = 1
    min_hard_case_generation_source_coverage: int = 1
    min_hard_case_generation_candidate_score: float = 0.0001
    min_hard_case_generation_diversity_bonus: float = 0.0
    min_hard_case_generation_provenance_selected_ratio: float = 0.80
    min_hard_case_generation_provenance_bonus: float = 0.50
    min_hard_case_generation_provenance_source_sha_coverage: float = 0.95
    min_hard_case_generation_provenance_stable_ref_coverage: float = 0.95
    max_hard_case_generation_provenance_leakage_risk: float = 0.30
    min_hard_case_generated_benchmark_cases: int = 5
    min_hard_case_generated_patch_success_rate: float = 0.50
    min_hard_case_generated_multi_candidate_cases: int = 1
    min_hard_case_generated_score_inversions: int = 2
    min_hard_case_generated_diversity_assisted_successes: int = 1
    min_hard_case_generated_diversity_budget_sensitive_successes: int = 1
    min_hard_case_generated_success_diversity_lift: float = 1.0
    min_hard_case_generated_success_diversity_bonus: float = 0.0001
    min_hard_case_generated_dedupe_affected_cases: int = 1
    min_hard_case_generated_deduplicated_candidates: int = 1
    min_hard_case_generated_duplicate_pressure: float = 0.0001
    min_hard_case_generated_reflection_success_cases: int = 1
    min_hard_case_generated_reflection_candidates: int = 1
    required_score_signals: tuple[str, ...] = (
        "sbfl",
        "graph",
        "static",
        "risk",
        "llm",
    )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["required_score_signals"] = list(self.required_score_signals)
        return data


@dataclass(frozen=True)
class QualityGateCheck:
    name: str
    passed: bool
    expected: str
    actual: str
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QualityGateResult:
    passed: bool
    thresholds: QualityGateThresholds
    checks: list[QualityGateCheck]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "thresholds": self.thresholds.to_dict(),
            "checks": [check.to_dict() for check in self.checks],
        }


def quality_gate_thresholds_from_dict(payload: dict[str, Any] | None) -> QualityGateThresholds:
    payload = payload or {}
    names = {item.name for item in fields(QualityGateThresholds)}
    values = {key: value for key, value in payload.items() if key in names}
    return QualityGateThresholds(**values)


def evaluate_quality_gate(
    payload: dict[str, Any],
    thresholds: QualityGateThresholds | None = None,
) -> QualityGateResult:
    thresholds = thresholds or QualityGateThresholds()
    report = _extract_benchmark_report(payload)
    summary = _dict(report.get("summary"))
    cases = _list(report.get("cases"))
    checks = [
        _numeric_check(
            "benchmark_cases",
            _float(summary.get("case_count", len(cases))),
            float(thresholds.min_cases),
            f">= {thresholds.min_cases}",
            integer=True,
        ),
        _numeric_check(
            "top1_localization",
            _float(summary.get("top1", 0.0)),
            thresholds.min_top1,
            f">= {thresholds.min_top1:.2f}",
        ),
        _numeric_check(
            "top3_localization",
            _float(summary.get("top3", 0.0)),
            thresholds.min_top3,
            f">= {thresholds.min_top3:.2f}",
        ),
        _numeric_check(
            "patch_success_rate",
            _float(summary.get("patch_success_rate", 0.0)),
            thresholds.min_patch_success_rate,
            f">= {thresholds.min_patch_success_rate:.2f}",
        ),
        _patch_evidence_check(cases),
        _score_decomposition_check(cases, thresholds.required_score_signals),
    ]
    checks.extend(_slice_grounding_checks(summary, thresholds))
    checks.extend(_weight_search_checks(payload, thresholds))
    checks.extend(_patch_weight_search_checks(payload, thresholds))
    checks.extend(_llm_judge_reliability_checks(payload, summary, thresholds))
    checks.extend(_patch_judge_reliability_checks(payload, summary, thresholds))
    checks.extend(_patch_judge_benchmark_mining_checks(payload, summary))
    checks.extend(_patch_judge_fusion_checks(payload, thresholds))
    checks.extend(_search_budget_checks(summary, thresholds))
    checks.extend(_search_competition_checks(summary, thresholds))
    checks.extend(_metric_uncertainty_checks(summary, thresholds))
    checks.extend(_localization_calibration_checks(summary, thresholds))
    checks.extend(_localization_attribution_checks(summary, thresholds))
    checks.extend(_ablation_impact_checks(payload, thresholds))
    checks.extend(_generated_hard_case_ablation_link_checks(payload))
    checks.extend(_difficulty_checks(summary, thresholds))
    checks.extend(_benchmark_diversity_checks(summary, thresholds))
    checks.extend(_generalization_checks(summary, thresholds))
    checks.extend(_benchmark_provenance_checks(summary, thresholds))
    checks.extend(_hard_case_generation_checks(payload, thresholds))
    checks.extend(_hard_case_generated_benchmark_checks(payload, thresholds))
    return QualityGateResult(
        passed=all(check.passed for check in checks),
        thresholds=thresholds,
        checks=checks,
    )


def render_quality_gate_markdown(result: QualityGateResult) -> str:
    lines = [
        "# Quality Gate",
        "",
        f"- Status: {'PASS' if result.passed else 'FAIL'}",
        "",
        "| Check | Status | Expected | Actual | Details |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in result.checks:
        lines.append(
            "| "
            f"{check.name} | "
            f"{'PASS' if check.passed else 'FAIL'} | "
            f"{_markdown_cell(check.expected)} | "
            f"{_markdown_cell(check.actual)} | "
            f"{_markdown_cell('; '.join(check.details[:5]))} |"
        )
    return "\n".join(lines)


def _extract_benchmark_report(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("benchmark_report"), dict):
        return payload["benchmark_report"]
    if "summary" in payload and "cases" in payload:
        return payload
    raise ValueError("Artifact must contain benchmark_report or summary/cases.")


def _patch_evidence_check(cases: list[Any]) -> QualityGateCheck:
    missing = []
    for case in cases:
        if not isinstance(case, dict) or not case.get("patch_success"):
            continue
        if _has_successful_patch_evidence(case):
            continue
        missing.append(str(case.get("case_name", case.get("name", "<unknown>"))))
    return QualityGateCheck(
        name="patch_sandbox_evidence",
        passed=not missing,
        expected="successful patches include sandbox evidence",
        actual=f"missing={len(missing)}",
        details=missing,
    )


def _has_successful_patch_evidence(case: dict[str, Any]) -> bool:
    for key in ("beam_search_results", "patch_search_results", "repair_results"):
        for item in _list(case.get(key)):
            if not isinstance(item, dict):
                continue
            if item.get("success") is True and (
                item.get("failure_type") == "success"
                or _int(item.get("passed", 0)) > 0
                or _int(item.get("failed", 1)) == 0
            ):
                return True
    for item in _list(case.get("multi_patch_results")):
        if isinstance(item, dict) and item.get("success") is True:
            return True
    return False


def _score_decomposition_check(
    cases: list[Any],
    required: tuple[str, ...],
) -> QualityGateCheck:
    missing = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        case_name = str(case.get("case_name", case.get("name", "<unknown>")))
        details = _list(case.get("localization_details"))
        if not details or not isinstance(details[0], dict):
            missing.append(f"{case_name}:missing localization_details")
            continue
        signals = details[0].get("signals", {})
        if not isinstance(signals, dict):
            signals = {}
        missing_signals = [name for name in required if name not in signals]
        if missing_signals:
            missing.append(f"{case_name}:missing {', '.join(missing_signals)}")
    return QualityGateCheck(
        name="score_decomposition",
        passed=not missing,
        expected="top localization result exposes required score signals",
        actual=f"missing={len(missing)}",
        details=missing[:10],
    )


def _slice_grounding_checks(
    summary: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    keys = {
        "slice_grounded_case_count",
        "average_top1_slice_support",
        "average_top1_slice_failed_test_reachability",
        "average_top1_slice_call_chain_coverage",
    }
    if not any(key in summary for key in keys):
        return []
    case_count = max(1, _int(summary.get("case_count", 0)))
    grounded_ratio = _int(summary.get("slice_grounded_case_count", 0)) / case_count
    return [
        _numeric_check(
            "slice_grounded_case_ratio",
            grounded_ratio,
            thresholds.min_slice_grounded_case_ratio,
            f">= {thresholds.min_slice_grounded_case_ratio:.2f}",
        ),
        _numeric_check(
            "average_top1_slice_support",
            _float(summary.get("average_top1_slice_support", 0.0)),
            thresholds.min_average_top1_slice_support,
            f">= {thresholds.min_average_top1_slice_support:.2f}",
        ),
        _numeric_check(
            "average_top1_slice_failed_test_reachability",
            _float(summary.get("average_top1_slice_failed_test_reachability", 0.0)),
            thresholds.min_average_top1_slice_failed_test_reachability,
            f">= {thresholds.min_average_top1_slice_failed_test_reachability:.2f}",
        ),
        _numeric_check(
            "average_top1_slice_call_chain_coverage",
            _float(summary.get("average_top1_slice_call_chain_coverage", 0.0)),
            thresholds.min_average_top1_slice_call_chain_coverage,
            f">= {thresholds.min_average_top1_slice_call_chain_coverage:.2f}",
        ),
    ]


def _weight_search_checks(
    payload: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    results = _list(payload.get("weight_search_results"))
    if not results and not _dict(payload.get("settings")).get("run_weight_search"):
        return []
    profiles = [item for item in results if isinstance(item, dict)]
    best = _best_weight_profile(profiles)
    if best is None:
        best = {}
    robust_keys = {
        "robust_validation_score",
        "source_group_count",
        "source_groups",
        "holdout_splits",
        "max_top1_gap",
        "max_map_gap",
        "pareto_optimal",
        "dominates_count",
        "dominated_by_count",
    }
    robust_metrics_passed = (
        bool(best)
        and robust_keys.issubset(best)
        and _float(best.get("robust_validation_score", 0.0))
        >= thresholds.min_weight_search_robust_score
        and _int(best.get("source_group_count", 0))
        >= thresholds.min_weight_search_source_groups
        and _float(best.get("max_top1_gap", 1.0))
        <= thresholds.max_weight_search_top1_gap
        and _float(best.get("max_map_gap", 1.0))
        <= thresholds.max_weight_search_map_gap
    )
    return [
        QualityGateCheck(
            name="final_score_weight_robust_metrics",
            passed=robust_metrics_passed,
            expected="best FinalScore profile includes robust holdout fields",
            actual="present" if robust_metrics_passed else "missing",
        ),
        QualityGateCheck(
            name="final_score_weight_pareto_optimal",
            passed=bool(best.get("pareto_optimal")),
            expected="Pareto-optimal=true",
            actual=str(bool(best.get("pareto_optimal"))),
        ),
        _numeric_check(
            "final_score_weight_top1",
            _float(best.get("top1", best.get("validation_score", 0.0))),
            thresholds.min_weight_search_top1,
            f">= {thresholds.min_weight_search_top1:.2f}",
        ),
        _numeric_check(
            "final_score_weight_robust_score",
            _float(best.get("robust_validation_score", 0.0)),
            thresholds.min_weight_search_robust_score,
            f">= {thresholds.min_weight_search_robust_score:.2f}",
        ),
        _numeric_check(
            "final_score_weight_source_groups",
            _float(best.get("source_group_count", 0)),
            float(thresholds.min_weight_search_source_groups),
            f">= {thresholds.min_weight_search_source_groups}",
            integer=True,
        ),
        _maximum_check(
            "final_score_weight_top1_gap",
            _float(best.get("max_top1_gap", 1.0)),
            thresholds.max_weight_search_top1_gap,
            f"<= {thresholds.max_weight_search_top1_gap:.2f}",
        ),
        _maximum_check(
            "final_score_weight_map_gap",
            _float(best.get("max_map_gap", 1.0)),
            thresholds.max_weight_search_map_gap,
            f"<= {thresholds.max_weight_search_map_gap:.2f}",
        ),
    ]


def _best_weight_profile(profiles: list[dict[str, Any]]) -> dict[str, Any] | None:
    robust = [item for item in profiles if "robust_validation_score" in item]
    if robust:
        return max(robust, key=lambda item: _float(item.get("robust_validation_score", 0.0)))
    return profiles[0] if profiles else None


def _patch_weight_search_checks(
    payload: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    results = _list(payload.get("patch_weight_search_results"))
    if not results and not _dict(payload.get("settings")).get("run_patch_weight_search"):
        return []
    profiles = [item for item in results if isinstance(item, dict)]
    best = profiles[0] if profiles else {}
    pareto_fields = {"pareto_optimal", "dominates_count", "dominated_by_count"}
    feedback_weight = _float(_dict(best.get("weights")).get("execution_feedback", 0.0))
    return [
        QualityGateCheck(
            name="patch_score_weight_pareto_metrics",
            passed=pareto_fields.issubset(best),
            expected="best PatchScore profile includes Pareto fields",
            actual="present" if pareto_fields.issubset(best) else "missing",
        ),
        QualityGateCheck(
            name="patch_score_weight_pareto_optimal",
            passed=bool(best.get("pareto_optimal")),
            expected="Pareto-optimal=true",
            actual=str(bool(best.get("pareto_optimal"))),
        ),
        _numeric_check(
            "patch_score_weight_top1_success",
            _float(best.get("top1_success", 0.0)),
            thresholds.min_patch_weight_top1_success,
            f">= {thresholds.min_patch_weight_top1_success:.2f}",
        ),
        _numeric_check(
            "patch_score_weight_mrr",
            _float(best.get("mrr", 0.0)),
            thresholds.min_patch_weight_mrr,
            f">= {thresholds.min_patch_weight_mrr:.2f}",
        ),
        _numeric_check(
            "patch_score_feedback_weight",
            feedback_weight,
            thresholds.min_patch_feedback_weight,
            f">= {thresholds.min_patch_feedback_weight:.4f}",
        ),
    ]


def _llm_judge_reliability_checks(
    payload: dict[str, Any],
    summary: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    if _dict(payload.get("settings")).get("judge_mode") != "llm":
        return []
    report = summary.get("llm_judge_reliability")
    if not isinstance(report, dict) or not report:
        return [
            QualityGateCheck(
                "llm_judge_reliability",
                False,
                "non-empty llm_judge_reliability for llm judge mode",
                "missing or empty",
            )
        ]
    return [
        _numeric_check(
            "llm_judge_cases",
            _float(report.get("judged_case_count", 0)),
            float(thresholds.min_llm_judge_cases),
            f">= {thresholds.min_llm_judge_cases}",
            integer=True,
        ),
        _maximum_check(
            "llm_judge_brier_score",
            _float(report.get("brier_score", 1.0)),
            thresholds.max_llm_judge_brier_score,
            f"<= {thresholds.max_llm_judge_brier_score:.2f}",
        ),
        _maximum_check(
            "llm_judge_expected_calibration_error",
            _float(report.get("expected_calibration_error", 1.0)),
            thresholds.max_llm_judge_ece,
            f"<= {thresholds.max_llm_judge_ece:.2f}",
        ),
        _numeric_check(
            "llm_judge_agreement_rate",
            _float(report.get("agreement_rate", 0.0)),
            thresholds.min_llm_judge_agreement_rate,
            f">= {thresholds.min_llm_judge_agreement_rate:.2f}",
        ),
    ]


def _patch_judge_reliability_checks(
    payload: dict[str, Any],
    summary: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    if _dict(payload.get("settings")).get("patch_judge_mode") != "llm":
        return []
    report = summary.get("patch_judge_reliability")
    if not isinstance(report, dict) or not report:
        return [
            QualityGateCheck(
                "patch_judge_reliability",
                False,
                "non-empty patch_judge_reliability for llm patch judge mode",
                "missing or empty",
            )
        ]
    return [
        _numeric_check(
            "patch_judge_candidates",
            _float(report.get("judged_candidate_count", 0)),
            float(thresholds.min_patch_judge_candidates),
            f">= {thresholds.min_patch_judge_candidates}",
            integer=True,
        ),
        _maximum_check(
            "patch_judge_brier_score",
            _float(report.get("brier_score", 1.0)),
            thresholds.max_patch_judge_brier_score,
            f"<= {thresholds.max_patch_judge_brier_score:.2f}",
        ),
        _maximum_check(
            "patch_judge_expected_calibration_error",
            _float(report.get("expected_calibration_error", 1.0)),
            thresholds.max_patch_judge_ece,
            f"<= {thresholds.max_patch_judge_ece:.2f}",
        ),
        _numeric_check(
            "patch_judge_agreement_rate",
            _float(report.get("agreement_rate", 0.0)),
            thresholds.min_patch_judge_agreement_rate,
            f">= {thresholds.min_patch_judge_agreement_rate:.2f}",
        ),
    ]


def _patch_judge_benchmark_mining_checks(
    payload: dict[str, Any],
    summary: dict[str, Any],
) -> list[QualityGateCheck]:
    if _dict(payload.get("settings")).get("patch_judge_mode") != "llm":
        return []
    mining = payload.get("benchmark_mining")
    if not isinstance(mining, dict) or not mining:
        return [
            QualityGateCheck(
                "patch_judge_benchmark_mining",
                False,
                "benchmark_mining artifact for llm patch judge mode",
                "missing or empty",
            )
        ]
    judged = _int(_dict(summary.get("patch_judge_reliability")).get("judged_candidate_count", 0))
    mining_judged = _int(mining.get("judged_candidate_count", 0))
    suggestion_count = _int(mining.get("suggestion_count", 0))
    cluster_count = _int(mining.get("cluster_count", 0))
    template_seed_count = _mining_template_seed_count(mining)
    preview_case_count = _preview_case_count(mining)
    expected_cluster_max = max(1, suggestion_count)
    expected_template_seed_count = max(1, suggestion_count)
    expected_preview_count = max(1, template_seed_count)
    return [
        _numeric_check(
            "patch_judge_mining_candidate_coverage",
            float(mining_judged),
            float(judged),
            f">= {judged}",
            integer=True,
        ),
        QualityGateCheck(
            "patch_judge_mining_cluster_count",
            1 <= cluster_count <= expected_cluster_max,
            f"1..{expected_cluster_max}",
            str(cluster_count),
        ),
        _numeric_check(
            "patch_judge_mining_template_seed_count",
            float(template_seed_count),
            float(expected_template_seed_count),
            f">= {expected_template_seed_count}",
            integer=True,
        ),
        _numeric_check(
            "patch_judge_mining_seed_preview",
            float(preview_case_count),
            float(expected_preview_count),
            f">= {expected_preview_count}",
            integer=True,
        ),
    ]


def _preview_case_count(mining: dict[str, Any]) -> int:
    if "preview_case_count" in mining:
        return _int(mining.get("preview_case_count", 0))
    preview = _dict(mining.get("template_seed_preview"))
    if preview:
        return len(_list(preview.get("cases")))
    return sum(len(_list(seed.get("preview_cases"))) for seed in _list(mining.get("template_seeds")) if isinstance(seed, dict))


def _mining_template_seed_count(mining: dict[str, Any]) -> int:
    if "template_seed_count" in mining:
        return _int(mining.get("template_seed_count", 0))
    return len(_list(mining.get("template_seeds")))


def _patch_judge_fusion_checks(
    payload: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    summary = payload.get("patch_judge_fusion_summary")
    if not isinstance(summary, dict) or not summary:
        return []
    judge_count = _int(summary.get("judge_profile_count", 0))
    if _dict(payload.get("settings")).get("patch_judge_mode") != "llm" and judge_count == 0:
        return []
    return [
        _numeric_check(
            "patch_judge_fusion_profiles",
            float(judge_count),
            1.0,
            ">= 1",
            integer=True,
        ),
        QualityGateCheck(
            "patch_judge_fusion_status",
            True,
            "reported",
            str(summary.get("status", "")),
        ),
        _maximum_check(
            "patch_judge_fusion_validation_delta",
            -_float(summary.get("validation_delta", 0.0)),
            thresholds.max_patch_judge_fusion_validation_regression,
            f">= -{thresholds.max_patch_judge_fusion_validation_regression:.4f}",
        ),
        _maximum_check(
            "patch_judge_fusion_top1_delta",
            -_float(summary.get("top1_delta", 0.0)),
            thresholds.max_patch_judge_fusion_top1_regression,
            f">= -{thresholds.max_patch_judge_fusion_top1_regression:.4f}",
        ),
        _maximum_check(
            "patch_judge_fusion_mrr_delta",
            -_float(summary.get("mrr_delta", 0.0)),
            thresholds.max_patch_judge_fusion_mrr_regression,
            f">= -{thresholds.max_patch_judge_fusion_mrr_regression:.4f}",
        ),
        _maximum_check(
            "patch_judge_fusion_success_margin_delta",
            -_float(summary.get("success_margin_delta", 0.0)),
            thresholds.max_patch_judge_fusion_success_margin_regression,
            f">= -{thresholds.max_patch_judge_fusion_success_margin_regression:.4f}",
        ),
        _maximum_check(
            "patch_judge_fusion_first_success_rank_delta",
            -_float(summary.get("first_success_rank_delta", 0.0)),
            thresholds.max_patch_judge_fusion_first_success_rank_regression,
            f">= -{thresholds.max_patch_judge_fusion_first_success_rank_regression:.4f}",
        ),
    ]


def _search_budget_checks(
    summary: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    report = summary.get("search_budget_analysis")
    if not isinstance(report, dict) or not report:
        return []
    success_at = _dict(report.get("success_at_budget"))
    checks = [
        _numeric_check("search_budget_cases", _float(report.get("evaluated_case_count", report.get("case_count", 0))), float(thresholds.min_search_budget_cases), f">= {thresholds.min_search_budget_cases}", integer=True),
        _numeric_check("search_budget_success_at_1", _float(success_at.get("1", 0.0)), thresholds.min_search_budget_success_at_1, f">= {thresholds.min_search_budget_success_at_1:.2f}"),
        _numeric_check("search_budget_auc", _float(report.get("budget_auc", 0.0)), thresholds.min_search_budget_auc, f">= {thresholds.min_search_budget_auc:.2f}"),
        _maximum_check("search_budget_first_success_rank_p90", _float(report.get("first_success_rank_p90", 999.0)), thresholds.max_search_budget_first_success_rank_p90, f"<= {thresholds.max_search_budget_first_success_rank_p90:.2f}"),
    ]
    if _search_budget_has_dedupe_metrics(report, thresholds):
        checks.extend(
            [
                _numeric_check(
                    "search_budget_dedupe_affected_cases",
                    _float(report.get("dedupe_affected_case_count", 0)),
                    float(thresholds.min_search_budget_dedupe_affected_cases),
                    f">= {thresholds.min_search_budget_dedupe_affected_cases}",
                    integer=True,
                ),
                _numeric_check(
                    "search_budget_deduplicated_candidates",
                    _float(report.get("total_deduplicated_candidates", 0)),
                    float(thresholds.min_search_budget_deduplicated_candidates),
                    f">= {thresholds.min_search_budget_deduplicated_candidates}",
                    integer=True,
                ),
                _numeric_check(
                    "search_budget_average_duplicate_pressure",
                    _float(report.get("average_duplicate_pressure", 0.0)),
                    thresholds.min_search_budget_average_duplicate_pressure,
                    f">= {thresholds.min_search_budget_average_duplicate_pressure:.4f}",
                ),
            ]
        )
    return checks


def _search_budget_has_dedupe_metrics(
    report: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> bool:
    dedupe_keys = {
        "dedupe_affected_case_count",
        "total_deduplicated_candidates",
        "average_deduplicated_candidates",
        "max_deduplicated_candidates",
        "average_duplicate_pressure",
    }
    return (
        any(key in report for key in dedupe_keys)
        or thresholds.min_search_budget_dedupe_affected_cases > 0
        or thresholds.min_search_budget_deduplicated_candidates > 0
        or thresholds.min_search_budget_average_duplicate_pressure > 0
    )


def _search_competition_checks(
    summary: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    report = summary.get("search_competition_analysis")
    if not isinstance(report, dict) or not report:
        return []
    return [
        _numeric_check("search_competition_multi_candidate_cases", _float(report.get("multi_candidate_case_count", 0)), float(thresholds.min_search_competition_multi_candidate_cases), f">= {thresholds.min_search_competition_multi_candidate_cases}", integer=True),
        _numeric_check("search_competition_multi_candidate_rule_diversity", _float(report.get("multi_candidate_average_rule_diversity", 0.0)), thresholds.min_search_competition_multi_candidate_rule_diversity, f">= {thresholds.min_search_competition_multi_candidate_rule_diversity:.2f}"),
        _numeric_check("search_competition_multi_candidate_failure_type_diversity", _float(report.get("multi_candidate_average_failure_type_diversity", 0.0)), thresholds.min_search_competition_multi_candidate_failure_type_diversity, f">= {thresholds.min_search_competition_multi_candidate_failure_type_diversity:.2f}"),
        _numeric_check("search_competition_multi_candidate_retention_bucket_diversity", _float(report.get("multi_candidate_average_retention_bucket_diversity", 0.0)), thresholds.min_search_competition_multi_candidate_retention_bucket_diversity, f">= {thresholds.min_search_competition_multi_candidate_retention_bucket_diversity:.2f}"),
        _numeric_check("search_competition_diversity_assisted_successes", _float(report.get("diversity_assisted_success_count", 0)), float(thresholds.min_search_competition_diversity_assisted_successes), f">= {thresholds.min_search_competition_diversity_assisted_successes}", integer=True),
        _numeric_check("search_competition_average_success_diversity_lift", _float(report.get("average_success_diversity_lift", 0.0)), thresholds.min_search_competition_average_success_diversity_lift, f">= {thresholds.min_search_competition_average_success_diversity_lift:.4f}"),
        _numeric_check("search_competition_average_success_diversity_bonus", _float(report.get("average_success_diversity_bonus", 0.0)), thresholds.min_search_competition_average_success_diversity_bonus, f">= {thresholds.min_search_competition_average_success_diversity_bonus:.4f}"),
    ]


def _metric_uncertainty_checks(
    summary: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    report = summary.get("metric_uncertainty")
    if not isinstance(report, dict) or not report:
        return []
    metrics = _dict(report.get("metrics"))
    top1 = _dict(metrics.get("top1"))
    map_metric = _dict(metrics.get("map"))
    patch = _dict(metrics.get("patch_success_rate"))
    return [
        _numeric_check("metric_uncertainty_cases", _float(report.get("case_count", 0)), float(thresholds.min_metric_uncertainty_cases), f">= {thresholds.min_metric_uncertainty_cases}", integer=True),
        _maximum_check("metric_uncertainty_top1_width", _float(top1.get("width", 1.0)), thresholds.max_metric_uncertainty_top1_width, f"<= {thresholds.max_metric_uncertainty_top1_width:.2f}"),
        _maximum_check("metric_uncertainty_map_width", _float(map_metric.get("width", 1.0)), thresholds.max_metric_uncertainty_map_width, f"<= {thresholds.max_metric_uncertainty_map_width:.2f}"),
        _maximum_check("metric_uncertainty_patch_success_rate_width", _float(patch.get("width", 1.0)), thresholds.max_metric_uncertainty_patch_success_width, f"<= {thresholds.max_metric_uncertainty_patch_success_width:.2f}"),
        _numeric_check("metric_uncertainty_top1_lower", _float(top1.get("lower", 0.0)), thresholds.min_metric_uncertainty_top1_lower, f">= {thresholds.min_metric_uncertainty_top1_lower:.2f}"),
        _numeric_check("metric_uncertainty_map_lower", _float(map_metric.get("lower", 0.0)), thresholds.min_metric_uncertainty_map_lower, f">= {thresholds.min_metric_uncertainty_map_lower:.2f}"),
        _numeric_check("metric_uncertainty_patch_success_rate_lower", _float(patch.get("lower", 0.0)), thresholds.min_metric_uncertainty_patch_success_lower, f">= {thresholds.min_metric_uncertainty_patch_success_lower:.2f}"),
    ]


def _localization_calibration_checks(
    summary: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    report = summary.get("localization_calibration")
    if not isinstance(report, dict) or not report:
        return []
    splits = [item for item in _list(report.get("source_group_holdout_splits")) if isinstance(item, dict)]
    min_train = min((_int(item.get("train_case_count", 0)) for item in splits), default=0)
    max_holdout_ece_default = (
        0.0 if thresholds.min_localization_source_holdout_splits <= 0 else 1.0
    )
    max_holdout_ece = max(
        (
            _float(item.get("holdout_calibrated_expected_calibration_error", 1.0))
            for item in splits
        ),
        default=max_holdout_ece_default,
    )
    return [
        _numeric_check("localization_calibration_cases", _float(report.get("case_count", 0)), float(thresholds.min_localization_calibration_cases), f">= {thresholds.min_localization_calibration_cases}", integer=True),
        _maximum_check("localization_calibrated_expected_calibration_error", _float(report.get("calibrated_expected_calibration_error", 1.0)), thresholds.max_localization_calibrated_ece, f"<= {thresholds.max_localization_calibrated_ece:.2f}"),
        _numeric_check("localization_source_holdout_splits", float(len(splits)), float(thresholds.min_localization_source_holdout_splits), f">= {thresholds.min_localization_source_holdout_splits}", integer=True),
        _numeric_check("localization_min_holdout_train_cases", float(min_train), float(thresholds.min_localization_holdout_train_cases), f">= {thresholds.min_localization_holdout_train_cases}", integer=True),
        _maximum_check("localization_holdout_calibrated_expected_calibration_error", max_holdout_ece, thresholds.max_localization_holdout_calibrated_ece, f"<= {thresholds.max_localization_holdout_calibrated_ece:.2f}"),
    ]


def _localization_attribution_checks(
    summary: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    report = summary.get("localization_attribution")
    if not isinstance(report, dict) or not report:
        return []
    return [
        _numeric_check("localization_attribution_coverage", _float(report.get("attribution_coverage", 0.0)), thresholds.min_localization_attribution_coverage, f">= {thresholds.min_localization_attribution_coverage:.2f}"),
        _maximum_check("localization_attribution_fragile_rate", _float(report.get("fragile_top1_rate", 1.0)), thresholds.max_localization_attribution_fragile_rate, f"<= {thresholds.max_localization_attribution_fragile_rate:.2f}"),
        _maximum_check("localization_attribution_counterfactual_flip_rate", _float(report.get("counterfactual_flip_rate", 1.0)), thresholds.max_localization_attribution_counterfactual_flip_rate, f"<= {thresholds.max_localization_attribution_counterfactual_flip_rate:.2f}"),
        _maximum_check("localization_attribution_reconstruction_error", _float(report.get("average_reconstruction_error", 1.0)), thresholds.max_localization_attribution_reconstruction_error, f"<= {thresholds.max_localization_attribution_reconstruction_error:.2f}"),
    ]


def _ablation_impact_checks(
    payload: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    impact = payload.get("ablation_impact")
    if not isinstance(impact, dict) or not impact:
        return []
    rows = [item for item in _list(impact.get("rows")) if isinstance(item, dict)]
    regression_rows = [item for item in rows if _ablation_row_is_regression(item)]
    attributed_rows = [item for item in rows if _ablation_row_has_attribution(item)]
    attributed_regression_rows = [item for item in regression_rows if _ablation_regression_has_attribution(item)]
    max_abs = max((_abs_float(item.get("impact_score", 0.0)) for item in rows), default=0.0)
    return [
        QualityGateCheck("ablation_impact_baseline", impact.get("baseline_variant") == "full", "baseline_variant=full", str(impact.get("baseline_variant", ""))),
        _numeric_check("ablation_impact_variants", _float(impact.get("impacted_variant_count", len(rows))), float(thresholds.min_ablation_impact_variants), f">= {thresholds.min_ablation_impact_variants}", integer=True),
        _numeric_check("ablation_impact_regressions", float(len(regression_rows)), float(thresholds.min_ablation_regression_count), f">= {thresholds.min_ablation_regression_count}", integer=True),
        _numeric_check("ablation_impact_signal", max_abs, thresholds.min_ablation_abs_impact_score, f">= {thresholds.min_ablation_abs_impact_score:.2f}"),
        _numeric_check("ablation_impact_attribution", float(len(attributed_rows)), float(len(rows)), f">= {len(rows)}", integer=True),
        _numeric_check("ablation_impact_regression_attribution", float(len(attributed_regression_rows)), float(thresholds.min_ablation_regression_count), f">= {thresholds.min_ablation_regression_count}", integer=True),
    ]


def _ablation_row_has_attribution(row: dict[str, Any]) -> bool:
    contributions = row.get("signal_contributions", {})
    if not isinstance(contributions, dict) or not contributions:
        return False
    dominant = str(row.get("dominant_signal", ""))
    return bool(dominant and dominant in contributions)


def _ablation_row_is_regression(row: dict[str, Any]) -> bool:
    return (
        str(row.get("direction", "")) == "regression"
        or _int(row.get("regression_signal_count", 0)) > 0
    )


def _ablation_regression_has_attribution(row: dict[str, Any]) -> bool:
    return _ablation_row_has_attribution(row) and _int(row.get("regression_signal_count", 0)) >= 1 and _float(row.get("dominant_contribution", 0.0)) <= 0.0


def _generated_hard_case_ablation_link_checks(
    payload: dict[str, Any],
) -> list[QualityGateCheck]:
    generation = payload.get("hard_case_generation")
    impact = payload.get("ablation_impact")
    if not isinstance(generation, dict) or not generation:
        return []
    if "ablation_impact" not in payload:
        return []
    expected = {
        signal
        for signal in _generated_target_signals(generation, [])
        if ablation_variants_for_generated_signal(signal)
    }
    if not expected:
        return []
    if not isinstance(impact, dict) or not impact:
        return [QualityGateCheck("hard_case_generated_ablation_links", False, "generated hard-case target signals link to ablation rows", "missing ablation_impact", [f"signals={','.join(sorted(expected))}"])]
    variants = {str(row.get("variant", "")) for row in _list(impact.get("rows")) if isinstance(row, dict)}
    linked = {
        signal
        for signal in expected
        if variants & set(ablation_variants_for_generated_signal(signal))
    }
    missing = sorted(expected - linked)
    return [
        QualityGateCheck(
            "hard_case_generated_ablation_links",
            not missing,
            "generated hard-case target signals link to ablation rows",
            f"{len(linked)}/{len(expected)}",
            [f"missing_signals={','.join(missing)}"] if missing else [],
        )
    ]


def _difficulty_checks(
    summary: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    report = summary.get("difficulty_report")
    if not isinstance(report, dict) or not report:
        return []
    labels = _dict(report.get("label_counts"))
    buckets = _dict(report.get("bucket_counts"))
    case_count = _float(report.get("case_count", 0))
    expected_cases = _float(summary.get("case_count", 0))
    return [
        _numeric_check("difficulty_case_coverage", case_count, expected_cases, f">= {int(expected_cases)}", integer=True),
        _numeric_check("difficulty_medium_cases", _float(buckets.get("medium", 0)), float(thresholds.min_difficulty_medium_cases), f">= {thresholds.min_difficulty_medium_cases}", integer=True),
        _numeric_check("difficulty_hard_cases", _float(buckets.get("hard", 0)), float(thresholds.min_difficulty_hard_cases), f">= {thresholds.min_difficulty_hard_cases}", integer=True),
        _numeric_check("difficulty_cross_file_patch_cases", _float(labels.get("cross_file_patch", 0)), float(thresholds.min_difficulty_cross_file_patch_cases), f">= {thresholds.min_difficulty_cross_file_patch_cases}", integer=True),
        _numeric_check("difficulty_patch_competition_cases", _float(labels.get("patch_candidate_competition", 0)), float(thresholds.min_difficulty_patch_competition_cases), f">= {thresholds.min_difficulty_patch_competition_cases}", integer=True),
        _numeric_check("difficulty_cross_function_data_flow_cases", _float(labels.get("cross_function_data_flow", 0)), float(thresholds.min_difficulty_cross_function_data_flow_cases), f">= {thresholds.min_difficulty_cross_function_data_flow_cases}", integer=True),
    ]


def _benchmark_diversity_checks(
    summary: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    if "bug_type_metrics" not in summary and "rule_metrics" not in summary:
        return []
    bug_metrics = _dict(summary.get("bug_type_metrics"))
    rule_metrics = _dict(summary.get("rule_metrics"))
    return [
        _numeric_check("bug_type_count", float(len(bug_metrics)), float(thresholds.min_bug_type_count), f">= {thresholds.min_bug_type_count}", integer=True),
        _numeric_check("expected_rule_count", float(len(rule_metrics)), float(thresholds.min_expected_rule_count), f">= {thresholds.min_expected_rule_count}", integer=True),
        _metric_case_floor_check("bug_type_case_floor", bug_metrics, thresholds.min_cases_per_bug_type, "bug type"),
        _metric_case_floor_check("expected_rule_case_floor", rule_metrics, thresholds.min_cases_per_expected_rule, "expected rule"),
    ]


def _generalization_checks(
    summary: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    report = summary.get("generalization_report")
    if not isinstance(report, dict) or not report:
        return []
    case_count = _float(report.get("case_count", 0))
    expected_cases = _float(summary.get("case_count", 0))
    splits = _list(report.get("holdout_splits"))
    valid_splits = sum(1 for item in splits if isinstance(item, dict) and _int(_dict(item.get("holdout_metrics")).get("case_count", 0)) >= thresholds.min_generalization_holdout_cases)
    expected_splits = (
        thresholds.min_generalization_source_groups
        if thresholds.min_generalization_holdout_cases > 0
        else 0
    )
    return [
        _numeric_check("generalization_case_coverage", case_count, expected_cases, f">= {int(expected_cases)}", integer=True),
        _numeric_check("generalization_source_groups", _float(report.get("source_group_count", 0)), float(thresholds.min_generalization_source_groups), f">= {thresholds.min_generalization_source_groups}", integer=True),
        _numeric_check("generalization_min_holdout_cases", _float(report.get("min_holdout_case_count", 0)), float(thresholds.min_generalization_holdout_cases), f">= {thresholds.min_generalization_holdout_cases}", integer=True),
        _numeric_check("generalization_source_balance_entropy", _float(report.get("source_balance_entropy", 0.0)), thresholds.min_generalization_balance_entropy, f">= {thresholds.min_generalization_balance_entropy:.2f}"),
        QualityGateCheck("generalization_holdout_splits", valid_splits >= expected_splits, f">= {expected_splits}", f"{valid_splits}/{expected_splits}"),
        _maximum_check("generalization_top1_gap", _float(report.get("max_top1_gap", 1.0)), thresholds.max_generalization_top1_gap, f"<= {thresholds.max_generalization_top1_gap:.2f}"),
        _maximum_check("generalization_map_gap", _float(report.get("max_map_gap", 1.0)), thresholds.max_generalization_map_gap, f"<= {thresholds.max_generalization_map_gap:.2f}"),
        _maximum_check("generalization_patch_success_gap", _float(report.get("max_patch_success_gap", 1.0)), thresholds.max_generalization_patch_success_gap, f"<= {thresholds.max_generalization_patch_success_gap:.2f}"),
        _maximum_check("generalization_search_efficiency_gap", _float(report.get("max_search_efficiency_gap", 1.0)), thresholds.max_generalization_search_efficiency_gap, f"<= {thresholds.max_generalization_search_efficiency_gap:.2f}"),
        _maximum_check("generalization_worst_holdout_gap_score", _float(report.get("worst_holdout_gap_score", 1.0)), thresholds.max_generalization_worst_holdout_gap_score, f"<= {thresholds.max_generalization_worst_holdout_gap_score:.2f}"),
        _numeric_check("generalization_stability_score", _float(report.get("stability_score", 0.0)), thresholds.min_generalization_stability_score, f">= {thresholds.min_generalization_stability_score:.2f}"),
    ]


def _benchmark_provenance_checks(
    summary: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    report = summary.get("benchmark_provenance_audit")
    if not isinstance(report, dict) or not report:
        return []
    return [
        _numeric_check("benchmark_provenance_case_coverage", _float(report.get("case_provenance_coverage", 0.0)), thresholds.min_benchmark_provenance_case_coverage, f">= {thresholds.min_benchmark_provenance_case_coverage:.2f}"),
        _numeric_check("benchmark_provenance_mutation_coverage", _float(report.get("materialized_mutation_coverage", 0.0)), thresholds.min_benchmark_provenance_mutation_coverage, f">= {thresholds.min_benchmark_provenance_mutation_coverage:.2f}"),
        _numeric_check("benchmark_provenance_license_coverage", _float(report.get("license_coverage", 0.0)), thresholds.min_benchmark_provenance_license_coverage, f">= {thresholds.min_benchmark_provenance_license_coverage:.2f}"),
        _numeric_check("benchmark_provenance_source_sha256_coverage", _float(report.get("source_sha256_coverage", 0.0)), thresholds.min_benchmark_provenance_source_sha_coverage, f">= {thresholds.min_benchmark_provenance_source_sha_coverage:.2f}"),
        _numeric_check("benchmark_provenance_stable_ref_coverage", _float(report.get("stable_ref_coverage", 0.0)), thresholds.min_benchmark_provenance_stable_ref_coverage, f">= {thresholds.min_benchmark_provenance_stable_ref_coverage:.2f}"),
        _maximum_check("benchmark_provenance_duplicate_signatures", _float(report.get("duplicate_signature_count", 0)), float(thresholds.max_benchmark_provenance_duplicate_signatures), f"<= {thresholds.max_benchmark_provenance_duplicate_signatures}", integer=True),
        _maximum_check("benchmark_provenance_source_concentration", _float(report.get("max_source_file_case_share", 1.0)), thresholds.max_benchmark_provenance_source_concentration, f"<= {thresholds.max_benchmark_provenance_source_concentration:.2f}"),
        _maximum_check("benchmark_provenance_leakage_risk_score", _float(report.get("leakage_risk_score", 1.0)), thresholds.max_benchmark_provenance_leakage_risk_score, f"<= {thresholds.max_benchmark_provenance_leakage_risk_score:.2f}"),
    ]


def _hard_case_generation_checks(
    payload: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    generation = payload.get("hard_case_generation")
    if not isinstance(generation, dict):
        return []
    generated_count = _int(generation.get("generated_count", 0))
    template_cases = _list(_dict(generation.get("template")).get("cases"))
    audit = _dict(generation.get("selection_audit"))
    checks = [
        QualityGateCheck("hard_case_generation_count", generated_count == len(template_cases), "generated_count matches template cases", f"{generated_count}/{len(template_cases)}"),
        QualityGateCheck("hard_case_generation_audit", bool(audit) or generated_count == 0, "selection_audit exists", "present" if audit else "missing"),
    ]
    if generated_count == 0:
        checks.append(QualityGateCheck("hard_case_generation_empty_consistency", len(template_cases) == 0, "empty generation has no template cases", f"{len(template_cases)} cases"))
        return checks
    selected_candidate_refs = _selected_candidate_reference_count(generation)
    selected_candidate_count = max(_int(audit.get("selected_candidate_count", 0)), selected_candidate_refs)
    min_selected = thresholds.min_hard_case_generation_selected_candidates_per_case * generated_count
    provenance = _hard_case_generation_provenance(generation, audit)
    checks.extend(
        [
            _numeric_check("hard_case_generation_selected_candidates", float(selected_candidate_count), float(min_selected), f">= {min_selected}", integer=True),
            _numeric_check("hard_case_generation_rule_coverage", _float(audit.get("selected_rule_count", 0)), float(thresholds.min_hard_case_generation_rule_coverage), f">= {thresholds.min_hard_case_generation_rule_coverage}", integer=True),
            _numeric_check("hard_case_generation_function_coverage", _float(audit.get("selected_function_count", 0)), float(thresholds.min_hard_case_generation_function_coverage), f">= {thresholds.min_hard_case_generation_function_coverage}", integer=True),
            _numeric_check("hard_case_generation_source_coverage", _float(audit.get("selected_source_count", 0)), float(thresholds.min_hard_case_generation_source_coverage), f">= {thresholds.min_hard_case_generation_source_coverage}", integer=True),
            _numeric_check("hard_case_generation_candidate_score", _float(audit.get("average_candidate_score", 0.0)), thresholds.min_hard_case_generation_candidate_score, f">= {thresholds.min_hard_case_generation_candidate_score:.4f}"),
            _numeric_check("hard_case_generation_diversity_bonus", _float(audit.get("average_diversity_bonus", 0.0)), thresholds.min_hard_case_generation_diversity_bonus, f">= {thresholds.min_hard_case_generation_diversity_bonus:.4f}"),
            _numeric_check("hard_case_generation_provenance_selected_ratio", provenance["selected_ratio"], thresholds.min_hard_case_generation_provenance_selected_ratio, f">= {thresholds.min_hard_case_generation_provenance_selected_ratio:.4f}"),
            _numeric_check("hard_case_generation_provenance_bonus", provenance["average_bonus"], thresholds.min_hard_case_generation_provenance_bonus, f">= {thresholds.min_hard_case_generation_provenance_bonus:.4f}"),
            _numeric_check("hard_case_generation_provenance_source_sha256", provenance["source_sha256"], thresholds.min_hard_case_generation_provenance_source_sha_coverage, f">= {thresholds.min_hard_case_generation_provenance_source_sha_coverage:.4f}"),
            _numeric_check("hard_case_generation_provenance_stable_ref", provenance["stable_ref"], thresholds.min_hard_case_generation_provenance_stable_ref_coverage, f">= {thresholds.min_hard_case_generation_provenance_stable_ref_coverage:.4f}"),
            _maximum_check("hard_case_generation_provenance_leakage_risk", provenance["leakage_risk"], thresholds.max_hard_case_generation_provenance_leakage_risk, f"<= {thresholds.max_hard_case_generation_provenance_leakage_risk:.2f}"),
        ]
    )
    return checks


def _hard_case_generated_benchmark_checks(
    payload: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> list[QualityGateCheck]:
    generation = payload.get("hard_case_generation")
    generated_count = _int(_dict(generation).get("generated_count", 0))
    settings = _dict(payload.get("settings"))
    generated_benchmark = payload.get("hard_case_generated_benchmark")
    if generated_benchmark is None:
        if settings.get("run_hard_case_generated_benchmark") and generated_count > 0:
            return [QualityGateCheck("hard_case_generated_benchmark", False, "generated hard-case benchmark when generated cases exist", "missing")]
        return []
    if not isinstance(generated_benchmark, dict) or not generated_benchmark:
        return [QualityGateCheck("hard_case_generated_benchmark", False, "non-empty hard_case_generated_benchmark when present", "missing or empty")]
    report = _dict(generated_benchmark.get("benchmark_report"))
    summary = _dict(report.get("summary"))
    cases = _list(report.get("cases"))
    case_count = _int(summary.get("case_count", len(cases)))
    competition = _dict(summary.get("search_competition_analysis"))
    checks = [
        _numeric_check("hard_case_generated_benchmark_cases", float(case_count), float(thresholds.min_hard_case_generated_benchmark_cases), f">= {thresholds.min_hard_case_generated_benchmark_cases}", integer=True),
        _numeric_check("hard_case_generated_patch_success_rate", _float(summary.get("patch_success_rate", 0.0)), thresholds.min_hard_case_generated_patch_success_rate, f">= {thresholds.min_hard_case_generated_patch_success_rate:.2f}"),
        _numeric_check("hard_case_generated_multi_candidate_cases", _float(competition.get("multi_candidate_case_count", 0)), float(thresholds.min_hard_case_generated_multi_candidate_cases), f">= {thresholds.min_hard_case_generated_multi_candidate_cases}", integer=True),
    ]
    if generated_count > 0:
        checks.append(QualityGateCheck("hard_case_generated_benchmark_count", case_count == generated_count, "generated benchmark case count matches generated_count", f"{case_count}/{generated_count}"))
    if _expects_generated_score_inversion(generation, cases):
        checks.append(_numeric_check("hard_case_generated_score_inversions", _float(competition.get("score_inversion_count", 0)), float(thresholds.min_hard_case_generated_score_inversions), f">= {thresholds.min_hard_case_generated_score_inversions}", integer=True))
    if _expects_generated_diversity_reranking(generation, cases):
        evidence = generated_diversity_budget_evidence(generated_benchmark)
        checks.extend(
            [
                _numeric_check("hard_case_generated_diversity_assisted_successes", _float(competition.get("diversity_assisted_success_count", 0)), float(thresholds.min_hard_case_generated_diversity_assisted_successes), f">= {thresholds.min_hard_case_generated_diversity_assisted_successes}", integer=True),
                _numeric_check("hard_case_generated_success_diversity_lift", _float(competition.get("average_success_diversity_lift", 0.0)), thresholds.min_hard_case_generated_success_diversity_lift, f">= {thresholds.min_hard_case_generated_success_diversity_lift:.4f}"),
                _numeric_check("hard_case_generated_success_diversity_bonus", _float(competition.get("average_success_diversity_bonus", 0.0)), thresholds.min_hard_case_generated_success_diversity_bonus, f">= {thresholds.min_hard_case_generated_success_diversity_bonus:.4f}"),
                _numeric_check("hard_case_generated_diversity_budget_sensitive_successes", _float(evidence.get("budget_sensitive_successes", 0)), float(thresholds.min_hard_case_generated_diversity_budget_sensitive_successes), f">= {thresholds.min_hard_case_generated_diversity_budget_sensitive_successes}", integer=True),
            ]
        )
    if _expects_generated_candidate_deduplication(generation, cases):
        budget = _dict(summary.get("search_budget_analysis"))
        checks.extend(
            [
                _numeric_check("hard_case_generated_dedupe_affected_cases", _float(budget.get("dedupe_affected_case_count", 0)), float(thresholds.min_hard_case_generated_dedupe_affected_cases), f">= {thresholds.min_hard_case_generated_dedupe_affected_cases}", integer=True),
                _numeric_check("hard_case_generated_deduplicated_candidates", _float(budget.get("total_deduplicated_candidates", 0)), float(thresholds.min_hard_case_generated_deduplicated_candidates), f">= {thresholds.min_hard_case_generated_deduplicated_candidates}", integer=True),
                _numeric_check("hard_case_generated_duplicate_pressure", _float(budget.get("average_duplicate_pressure", 0.0)), thresholds.min_hard_case_generated_duplicate_pressure, f">= {thresholds.min_hard_case_generated_duplicate_pressure:.4f}"),
            ]
        )
    if _expects_generated_reflection_depth(generation, cases):
        evidence = _generated_reflection_evidence(summary, cases)
        checks.extend(
            [
                _numeric_check("hard_case_generated_reflection_success_cases", _float(evidence.get("reflection_success_case_count", 0)), float(thresholds.min_hard_case_generated_reflection_success_cases), f">= {thresholds.min_hard_case_generated_reflection_success_cases}", integer=True),
                _numeric_check("hard_case_generated_reflection_candidates", _float(evidence.get("reflection_candidate_count", 0)), float(thresholds.min_hard_case_generated_reflection_candidates), f">= {thresholds.min_hard_case_generated_reflection_candidates}", integer=True),
            ]
        )
    slice_check = _generated_slice_grounding_check(generation, cases, thresholds)
    if slice_check is not None:
        checks.append(slice_check)
    return checks


def _expects_generated_score_inversion(generation: Any, cases: list[Any]) -> bool:
    return "search_score_inversion" in _generated_target_signals(generation, cases) or any(_case_metadata(case).get("expected_score_inversion") is True for case in cases if isinstance(case, dict))


def _expects_generated_diversity_reranking(generation: Any, cases: list[Any]) -> bool:
    return "search_diversity_reranking" in _generated_target_signals(generation, cases) or any(_case_metadata(case).get("expected_diversity_reranking") is True or _case_metadata(case).get("expected_diversity_assisted_success") is True for case in cases if isinstance(case, dict))


def _expects_generated_reflection_depth(generation: Any, cases: list[Any]) -> bool:
    if "reflection_depth" in _generated_target_signals(generation, cases):
        return True
    return any(_case_expects_reflection(case) for case in cases if isinstance(case, dict))


def _expects_generated_candidate_deduplication(
    generation: Any,
    cases: list[Any],
) -> bool:
    signals = _generated_target_signals(generation, cases)
    if (
        "candidate_deduplication_pressure" in signals
        or "without_candidate_deduplication" in signals
    ):
        return True
    return any(
        _case_expects_candidate_deduplication(case)
        for case in cases
        if isinstance(case, dict)
    )


def _case_expects_candidate_deduplication(case: dict[str, Any]) -> bool:
    metadata = _case_metadata(case)
    return (
        metadata.get("expected_candidate_deduplication") is True
        or metadata.get("patch_score_profile") == "candidate_deduplication_probe"
        or metadata.get("search_pressure") == "candidate_deduplication_probe"
        or metadata.get("hard_case_target_signal") == "without_candidate_deduplication"
        or "without_candidate_deduplication" in _string_set(metadata.get("hard_case_target_signals", []))
        or "candidate_deduplication_pressure" in _string_set(metadata.get("target_benchmark_signals", []))
    )


def _case_expects_reflection(case: dict[str, Any]) -> bool:
    metadata = _case_metadata(case)
    return (
        metadata.get("expected_reflection_depth") is True
        or metadata.get("patch_score_profile") == "reflection_depth_probe"
        or metadata.get("search_pressure") == "reflection_depth_probe"
        or metadata.get("hard_case_target_signal") == "reflection_depth"
        or "reflection_depth" in _string_set(metadata.get("hard_case_target_signals", []))
        or "reflection_depth" in _string_set(metadata.get("target_benchmark_signals", []))
    )


def _generated_reflection_evidence(
    summary: dict[str, Any],
    cases: list[Any],
) -> dict[str, int]:
    reflection = _dict(summary.get("reflection_analysis"))
    candidate_count = _int(reflection.get("reflection_candidate_count", 0))
    success_cases = _int(reflection.get("reflection_success_case_count", 0))
    if candidate_count or success_cases:
        return {
            "reflection_candidate_count": candidate_count,
            "reflection_success_case_count": success_cases,
        }
    candidate_count = 0
    success_cases = 0
    for case in cases:
        if not isinstance(case, dict):
            continue
        observations = _case_reflection_observations(case)
        candidate_count += len(observations)
        if any(item.get("success") is True for item in observations):
            success_cases += 1
    return {
        "reflection_candidate_count": candidate_count,
        "reflection_success_case_count": success_cases,
    }


def _case_reflection_observations(case: dict[str, Any]) -> list[dict[str, Any]]:
    observations = []
    for node in _list(case.get("beam_search_results")):
        if isinstance(node, dict) and (node.get("parent_id") or _int(node.get("depth", 0)) > 0):
            observations.append(node)
    for attempt in _list(case.get("repair_results")):
        if isinstance(attempt, dict) and attempt.get("repair_loop_parent_id"):
            observations.append(attempt)
    return observations


def _generated_slice_grounding_check(
    generation: Any,
    cases: list[Any],
    thresholds: QualityGateThresholds,
) -> QualityGateCheck | None:
    expected = _generated_target_signals(generation, cases) & SLICE_GROUNDING_TARGET_SIGNALS
    if not expected:
        return None
    covered: set[str] = set()
    weak_details = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        signals = _case_target_signals(case) & expected
        if not signals:
            continue
        evidence = _top_slice_grounding(case)
        if _slice_evidence_passes(evidence, thresholds):
            covered.update(signals)
        else:
            weak_details.append(_slice_detail(case, signals, evidence))
    missing = sorted(expected - covered)
    details = []
    if missing:
        details.append(f"missing_signals={','.join(missing)}")
    details.extend(weak_details[:5])
    return QualityGateCheck("hard_case_generated_slice_grounding", not missing, "generated slice-grounding target signals have executed Top-1 slice evidence", f"{len(covered)}/{len(expected)}", details)


def _generated_target_signals(generation: Any, cases: list[Any]) -> set[str]:
    signals: set[str] = set()
    if isinstance(generation, dict):
        audit = _dict(generation.get("selection_audit"))
        signals.update(_string_set(audit.get("target_signals", [])))
        for row in _list(generation.get("rows")):
            if not isinstance(row, dict):
                continue
            if row.get("target_signal"):
                signals.add(str(row.get("target_signal")))
            signals.update(_string_set(row.get("target_signals", [])))
        template_cases = _list(_dict(generation.get("template")).get("cases"))
        for case in template_cases:
            if isinstance(case, dict):
                signals.update(_case_target_signals(case))
    for case in cases:
        if isinstance(case, dict):
            signals.update(_case_target_signals(case))
    return signals


def _case_target_signals(case: dict[str, Any]) -> set[str]:
    metadata = _case_metadata(case)
    signals = _string_set(metadata.get("hard_case_target_signals", []))
    if metadata.get("hard_case_target_signal"):
        signals.add(str(metadata.get("hard_case_target_signal")))
    signals.update(_string_set(metadata.get("target_benchmark_signals", [])))
    return signals


def _case_metadata(case: dict[str, Any]) -> dict[str, Any]:
    metadata = _dict(case.get("metadata"))
    benchmark = _dict(case.get("benchmark"))
    benchmark_metadata = _dict(benchmark.get("metadata"))
    return {**benchmark_metadata, **metadata}


def _top_slice_grounding(case: dict[str, Any]) -> dict[str, Any]:
    details = _list(case.get("localization_details"))
    if not details or not isinstance(details[0], dict):
        return {}
    return _dict(details[0].get("slice_grounding"))


def _slice_evidence_passes(
    evidence: dict[str, Any],
    thresholds: QualityGateThresholds,
) -> bool:
    return (
        bool(evidence.get("grounded"))
        and _float(evidence.get("support_score", 0.0)) >= thresholds.min_average_top1_slice_support
        and _float(evidence.get("failed_test_reachability", 0.0)) >= thresholds.min_average_top1_slice_failed_test_reachability
        and _float(evidence.get("call_chain_edge_coverage", 0.0)) >= thresholds.min_average_top1_slice_call_chain_coverage
    )


def _slice_detail(case: dict[str, Any], signals: set[str], evidence: dict[str, Any]) -> str:
    return (
        f"{case.get('case_name', '<unknown>')}:{','.join(sorted(signals))} "
        f"grounded={bool(evidence.get('grounded', False))} "
        f"support={_float(evidence.get('support_score', 0.0)):.4f} "
        f"reachability={_float(evidence.get('failed_test_reachability', 0.0)):.4f} "
        f"call_chain={_float(evidence.get('call_chain_edge_coverage', 0.0)):.4f}"
    )


def _hard_case_generation_provenance(
    generation: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, float]:
    template_cases = [
        case
        for case in _list(_dict(generation.get("template")).get("cases"))
        if isinstance(case, dict)
    ]
    bonuses = []
    sha = []
    stable = []
    leakage = []
    provenance_case_count = 0
    for case in template_cases:
        metadata = _case_metadata(case)
        reasons = _dict(metadata.get("hard_case_selected_candidate_reasons"))
        case_has_provenance = False
        for reason_list in reasons.values():
            for reason in _list(reason_list):
                text = str(reason)
                if text.startswith("provenance_bonus="):
                    bonuses.append(_float(text.split("=", 1)[1]))
                    case_has_provenance = True
                if text.startswith("provenance_metrics="):
                    metrics = _parse_semicolon_metrics(text.split("=", 1)[1])
                    sha.append(_float(metrics.get("source_sha256", 0.0)))
                    stable.append(_float(metrics.get("stable_ref", 0.0)))
                    leakage.append(_float(metrics.get("leakage_risk", 1.0)))
                    case_has_provenance = True
        if case_has_provenance:
            provenance_case_count += 1
    case_denominator = len(template_cases)
    return {
        "selected_ratio": (provenance_case_count / case_denominator) if case_denominator else 0.0,
        "average_bonus": _mean(bonuses),
        "source_sha256": _mean(sha),
        "stable_ref": _mean(stable),
        "leakage_risk": _mean(leakage) if leakage else 1.0,
    }


def _selected_candidate_reference_count(generation: dict[str, Any]) -> int:
    total = 0
    for row in _list(generation.get("rows")):
        if not isinstance(row, dict) or row.get("status") != "generated":
            continue
        total += sum(1 for item in _list(row.get("selected_candidate_ids")) if str(item))
    return total


def _metric_case_floor_check(
    name: str,
    metrics: dict[str, Any],
    min_cases: int,
    label: str,
) -> QualityGateCheck:
    weak = [
        f"{key}={_int(_dict(value).get('case_count', 0))}"
        for key, value in sorted(metrics.items())
        if _int(_dict(value).get("case_count", 0)) < min_cases
    ]
    return QualityGateCheck(name, not weak, f"each {label} has >= {min_cases} cases", f"weak={len(weak)}", weak)


def _numeric_check(
    name: str,
    actual: float,
    threshold: float,
    label: str,
    integer: bool = False,
) -> QualityGateCheck:
    return QualityGateCheck(
        name=name,
        passed=actual >= threshold,
        expected=label,
        actual=str(int(actual)) if integer else f"{actual:.4f}",
    )


def _maximum_check(
    name: str,
    actual: float,
    threshold: float,
    label: str,
    integer: bool = False,
) -> QualityGateCheck:
    return QualityGateCheck(
        name=name,
        passed=actual <= threshold,
        expected=label,
        actual=str(int(actual)) if integer else f"{actual:.4f}",
    )


def _parse_semicolon_metrics(text: str) -> dict[str, str]:
    output = {}
    for item in text.split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        output[key] = value
    return output


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_set(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value if str(item)}
    return {str(value)} if value else set()


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _abs_float(value: Any) -> float:
    return abs(_float(value))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _markdown_cell(value: str) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate suite quality gate.")
    parser.add_argument("artifact", help="Path to suite.json or benchmark JSON")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    for item in fields(QualityGateThresholds):
        if item.name == "required_score_signals":
            continue
        option = "--" + item.name.replace("_", "-")
        default = item.default
        value_type = int if isinstance(default, int) and not isinstance(default, bool) else float
        parser.add_argument(option, type=value_type, default=default)
    parser.add_argument("--output-json", help="Optional path for gate JSON output")
    parser.add_argument("--output-markdown", help="Optional path for gate markdown output")
    return parser


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()
    payload = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
    threshold_values = {
        item.name: getattr(args, item.name)
        for item in fields(QualityGateThresholds)
        if item.name != "required_score_signals"
    }
    result = evaluate_quality_gate(
        payload,
        thresholds=QualityGateThresholds(**threshold_values),
    )
    json_report = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    markdown_report = render_quality_gate_markdown(result)
    if args.output_json:
        Path(args.output_json).write_text(json_report, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown_report, encoding="utf-8")
    print(json_report if args.format == "json" else markdown_report)
    raise SystemExit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
