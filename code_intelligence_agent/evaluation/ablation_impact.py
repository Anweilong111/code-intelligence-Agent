from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from code_intelligence_agent.evaluation.ablation import AblationResult


@dataclass(frozen=True)
class AblationImpactRow:
    variant: str
    baseline_case_count: int
    variant_case_count: int
    paired_case_count: int
    delta_top1: float
    delta_top3: float
    delta_mrr: float
    delta_map: float
    delta_ndcg_at_3: float
    delta_exam_improvement: float
    delta_rule_recall: float
    delta_rule_precision: float
    delta_patch_success: float | None
    delta_beam_success: float | None
    delta_multi_patch_success: float | None
    delta_calibrated_ece_improvement: float
    delta_calibrated_brier_improvement: float
    impact_score: float
    direction: str
    dominant_signal: str
    dominant_contribution: float
    regression_signal_count: int
    improvement_signal_count: int
    neutral_signal_count: int
    signal_contributions: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AblationImpactReport:
    baseline_variant: str
    impacted_variant_count: int
    rows: list[AblationImpactRow]

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_variant": self.baseline_variant,
            "impacted_variant_count": self.impacted_variant_count,
            "rows": [row.to_dict() for row in self.rows],
        }


def ablation_impact_report(
    results: list[AblationResult],
    baseline_variant: str = "full",
) -> AblationImpactReport:
    baseline = next(
        (result for result in results if result.variant == baseline_variant),
        None,
    )
    if baseline is None:
        return AblationImpactReport(
            baseline_variant=baseline_variant,
            impacted_variant_count=0,
            rows=[],
        )

    rows = [
        _impact_row(result, baseline)
        for result in results
        if result.variant != baseline_variant
    ]
    rows.sort(
        key=lambda row: (
            abs(row.impact_score),
            abs(row.delta_patch_success or 0.0),
            abs(row.delta_map),
            row.variant,
        ),
        reverse=True,
    )
    return AblationImpactReport(
        baseline_variant=baseline_variant,
        impacted_variant_count=len(rows),
        rows=rows,
    )


def _impact_row(
    result: AblationResult,
    baseline: AblationResult,
) -> AblationImpactRow:
    delta_top1 = _delta(result.top1, baseline.top1)
    delta_top3 = _delta(result.top3, baseline.top3)
    delta_mrr = _delta(result.mrr, baseline.mrr)
    delta_map = _delta(result.map, baseline.map)
    delta_ndcg = _delta(result.ndcg_at_3, baseline.ndcg_at_3)
    # Lower EXAM is better, so invert the delta to keep positive values beneficial.
    delta_exam_improvement = _delta(baseline.mean_exam_score, result.mean_exam_score)
    delta_rule_recall = _delta(result.expected_rule_recall, baseline.expected_rule_recall)
    delta_rule_precision = _delta(
        result.expected_rule_precision,
        baseline.expected_rule_precision,
    )
    delta_patch_success = _optional_delta(
        result.patch_success_rate,
        baseline.patch_success_rate,
    )
    delta_beam_success = _optional_delta(
        result.beam_success_rate,
        baseline.beam_success_rate,
    )
    delta_multi_patch_success = _optional_delta(
        result.multi_patch_success_rate,
        baseline.multi_patch_success_rate,
    )
    # Lower Brier/ECE is better, so invert the delta to keep positive values beneficial.
    delta_calibrated_ece_improvement = _delta(
        baseline.localization_calibrated_expected_calibration_error,
        result.localization_calibrated_expected_calibration_error,
    )
    delta_calibrated_brier_improvement = _delta(
        baseline.localization_calibrated_brier_score,
        result.localization_calibrated_brier_score,
    )
    signal_contributions = _signal_contributions(
        delta_top1=delta_top1,
        delta_mrr=delta_mrr,
        delta_map=delta_map,
        delta_ndcg=delta_ndcg,
        delta_exam_improvement=delta_exam_improvement,
        delta_rule_recall=delta_rule_recall,
        delta_rule_precision=delta_rule_precision,
        delta_patch_success=delta_patch_success,
        delta_beam_success=delta_beam_success,
        delta_multi_patch_success=delta_multi_patch_success,
        delta_calibrated_ece_improvement=delta_calibrated_ece_improvement,
        delta_calibrated_brier_improvement=delta_calibrated_brier_improvement,
    )
    impact_score = _impact_score(signal_contributions)
    dominant_signal, dominant_contribution = _dominant_signal(
        signal_contributions
    )
    regression_signal_count = sum(
        1 for value in signal_contributions.values() if value < 0.0
    )
    improvement_signal_count = sum(
        1 for value in signal_contributions.values() if value > 0.0
    )
    return AblationImpactRow(
        variant=result.variant,
        baseline_case_count=baseline.case_count,
        variant_case_count=result.case_count,
        paired_case_count=min(baseline.case_count, result.case_count),
        delta_top1=delta_top1,
        delta_top3=delta_top3,
        delta_mrr=delta_mrr,
        delta_map=delta_map,
        delta_ndcg_at_3=delta_ndcg,
        delta_exam_improvement=delta_exam_improvement,
        delta_rule_recall=delta_rule_recall,
        delta_rule_precision=delta_rule_precision,
        delta_patch_success=delta_patch_success,
        delta_beam_success=delta_beam_success,
        delta_multi_patch_success=delta_multi_patch_success,
        delta_calibrated_ece_improvement=delta_calibrated_ece_improvement,
        delta_calibrated_brier_improvement=delta_calibrated_brier_improvement,
        impact_score=impact_score,
        direction=_direction(impact_score),
        dominant_signal=dominant_signal,
        dominant_contribution=dominant_contribution,
        regression_signal_count=regression_signal_count,
        improvement_signal_count=improvement_signal_count,
        neutral_signal_count=(
            len(signal_contributions)
            - regression_signal_count
            - improvement_signal_count
        ),
        signal_contributions=signal_contributions,
    )


def _signal_contributions(
    *,
    delta_top1: float,
    delta_mrr: float,
    delta_map: float,
    delta_ndcg: float,
    delta_exam_improvement: float,
    delta_rule_recall: float,
    delta_rule_precision: float,
    delta_patch_success: float | None,
    delta_beam_success: float | None,
    delta_multi_patch_success: float | None,
    delta_calibrated_ece_improvement: float,
    delta_calibrated_brier_improvement: float,
) -> dict[str, float]:
    contributions = {
        "top1": 0.18 * delta_top1,
        "map": 0.16 * delta_map,
        "mrr": 0.14 * delta_mrr,
        "ndcg_at_3": 0.12 * delta_ndcg,
        "exam_improvement": 0.10 * delta_exam_improvement,
        "rule_recall": 0.10 * delta_rule_recall,
        "rule_precision": 0.10 * delta_rule_precision,
        "calibrated_ece_improvement": 0.04 * delta_calibrated_ece_improvement,
        "calibrated_brier_improvement": (
            0.02 * delta_calibrated_brier_improvement
        ),
    }
    optional_terms = [
        ("patch_success", 0.18, delta_patch_success),
        ("beam_success", 0.06, delta_beam_success),
        ("multi_patch_success", 0.06, delta_multi_patch_success),
    ]
    for name, weight, value in optional_terms:
        if value is not None:
            contributions[name] = weight * value
    return {
        name: _normalize_contribution(value)
        for name, value in contributions.items()
    }


def _impact_score(signal_contributions: dict[str, float]) -> float:
    return round(sum(signal_contributions.values()), 4)


def _dominant_signal(signal_contributions: dict[str, float]) -> tuple[str, float]:
    if not signal_contributions:
        return "", 0.0
    name, value = max(
        signal_contributions.items(),
        key=lambda item: (abs(item[1]), item[0]),
    )
    return name, value


def _direction(score: float) -> str:
    if score <= -0.05:
        return "regression"
    if score >= 0.05:
        return "improvement"
    return "neutral"


def _optional_delta(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline is None:
        return None
    return _delta(value, baseline)


def _delta(value: float, baseline: float) -> float:
    return round(float(value) - float(baseline), 4)


def _normalize_contribution(value: float) -> float:
    rounded = round(value, 4)
    return 0.0 if rounded == -0.0 else rounded
