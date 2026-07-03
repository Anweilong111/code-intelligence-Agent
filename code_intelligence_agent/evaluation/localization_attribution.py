from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from code_intelligence_agent.core.fault_localizer import (
    DEFAULT_COVERAGE_WEIGHTS,
    DEFAULT_STATIC_ONLY_WEIGHTS,
    ScoreWeights,
)


CORE_COMPONENTS = ("sbfl", "graph", "static", "semantic", "llm", "risk")
FRAGILE_MARGIN_THRESHOLD = 0.02


@dataclass(frozen=True)
class LocalizationAttributionRow:
    case: str
    top_function: str
    top1_hit: bool
    top_score: float
    runner_up_function: str
    runner_up_score: float
    top1_margin: float
    coverage_mode: str
    primary_component: str
    primary_component_contribution: float
    positive_contribution_total: float
    risk_penalty: float
    reconstructed_score: float
    reconstruction_error: float
    fragile_top1: bool
    counterfactual_flip_components: list[str]
    component_contributions: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LocalizationAttributionReport:
    case_count: int
    attributed_case_count: int
    attribution_coverage: float
    mean_top1_margin: float
    min_top1_margin: float
    fragile_top1_case_count: int
    fragile_top1_rate: float
    counterfactual_flip_case_count: int
    counterfactual_flip_rate: float
    mean_positive_contribution: float
    mean_risk_penalty: float
    average_reconstruction_error: float
    high_reconstruction_error_count: int
    primary_component_counts: dict[str, int]
    primary_component_entropy: float
    average_component_contributions: dict[str, float]
    rows: list[LocalizationAttributionRow]
    top_fragile_cases: list[LocalizationAttributionRow]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["rows"] = [row.to_dict() for row in self.rows]
        data["top_fragile_cases"] = [
            row.to_dict() for row in self.top_fragile_cases
        ]
        return data


def localization_attribution_summary(report_or_cases: Any) -> dict[str, Any]:
    return localization_attribution_report(report_or_cases).to_dict()


def localization_attribution_report(
    report_or_cases: Any,
    *,
    fragile_margin_threshold: float = FRAGILE_MARGIN_THRESHOLD,
) -> LocalizationAttributionReport:
    cases = _cases(report_or_cases)
    rows = [
        row
        for row in (
            _case_attribution_row(
                case,
                fragile_margin_threshold=fragile_margin_threshold,
            )
            for case in cases
        )
        if row is not None
    ]
    attributed_count = len(rows)
    primary_counts = _primary_component_counts(rows)
    flip_count = sum(1 for row in rows if row.counterfactual_flip_components)
    fragile_count = sum(1 for row in rows if row.fragile_top1)
    average_contributions = {
        component: round(
            _average(
                [
                    row.component_contributions.get(component, 0.0)
                    for row in rows
                ]
            ),
            4,
        )
        for component in CORE_COMPONENTS
    }
    return LocalizationAttributionReport(
        case_count=len(cases),
        attributed_case_count=attributed_count,
        attribution_coverage=_ratio(attributed_count, len(cases)),
        mean_top1_margin=round(_average([row.top1_margin for row in rows]), 4),
        min_top1_margin=round(
            min((row.top1_margin for row in rows), default=0.0),
            4,
        ),
        fragile_top1_case_count=fragile_count,
        fragile_top1_rate=_ratio(fragile_count, attributed_count),
        counterfactual_flip_case_count=flip_count,
        counterfactual_flip_rate=_ratio(flip_count, attributed_count),
        mean_positive_contribution=round(
            _average([row.positive_contribution_total for row in rows]),
            4,
        ),
        mean_risk_penalty=round(_average([row.risk_penalty for row in rows]), 4),
        average_reconstruction_error=round(
            _average([row.reconstruction_error for row in rows]),
            4,
        ),
        high_reconstruction_error_count=sum(
            1 for row in rows if row.reconstruction_error > 0.05
        ),
        primary_component_counts=primary_counts,
        primary_component_entropy=_normalized_entropy(primary_counts),
        average_component_contributions=average_contributions,
        rows=rows,
        top_fragile_cases=sorted(
            (row for row in rows if row.fragile_top1),
            key=lambda row: (row.top1_margin, -len(row.counterfactual_flip_components)),
        )[:10],
    )


def _case_attribution_row(
    case: Any,
    *,
    fragile_margin_threshold: float,
) -> LocalizationAttributionRow | None:
    details = [
        item for item in _list(_get(case, "localization_details", []))
        if isinstance(item, dict)
    ]
    if not details:
        return None
    top = details[0]
    signals = _dict(top.get("signals"))
    if not signals:
        return None
    weights = _weights_for_case(case)
    contributions = _component_contributions(signals, weights)
    runner_up = details[1] if len(details) > 1 else {}
    top_score = _clamp(_float(top.get("score", 0.0)))
    runner_up_score = _clamp(_float(runner_up.get("score", 0.0)))
    top1_margin = round(top_score - runner_up_score, 4) if runner_up else top_score
    reconstructed_score = _clamp(sum(contributions.values()))
    reconstruction_error = round(abs(top_score - reconstructed_score), 4)
    flip_components = _counterfactual_flip_components(
        top=top,
        competitors=details[1:],
        weights=weights,
    )
    primary_component, primary_contribution = _primary_component(contributions)
    return LocalizationAttributionRow(
        case=_case_name(case),
        top_function=str(top.get("function_name", top.get("function", ""))),
        top1_hit=_top1_hit(case, top),
        top_score=round(top_score, 4),
        runner_up_function=str(
            runner_up.get("function_name", runner_up.get("function", ""))
        ),
        runner_up_score=round(runner_up_score, 4) if runner_up else 0.0,
        top1_margin=top1_margin,
        coverage_mode=str(_get(case, "coverage_mode", "")),
        primary_component=primary_component,
        primary_component_contribution=primary_contribution,
        positive_contribution_total=round(
            sum(value for value in contributions.values() if value > 0),
            4,
        ),
        risk_penalty=round(abs(min(0.0, contributions.get("risk", 0.0))), 4),
        reconstructed_score=round(reconstructed_score, 4),
        reconstruction_error=reconstruction_error,
        fragile_top1=(
            top1_margin <= fragile_margin_threshold or bool(flip_components)
        ),
        counterfactual_flip_components=flip_components,
        component_contributions=contributions,
    )


def _component_contributions(
    signals: dict[str, Any],
    weights: ScoreWeights,
) -> dict[str, float]:
    risk_signal = _float(signals.get("risk", signals.get("patch_risk", 0.0)))
    return {
        "sbfl": round(weights.sbfl * _float(signals.get("sbfl", 0.0)), 4),
        "graph": round(weights.graph * _float(signals.get("graph", 0.0)), 4),
        "static": round(weights.static * _float(signals.get("static", 0.0)), 4),
        "semantic": round(
            weights.semantic * _float(signals.get("semantic", 0.0)),
            4,
        ),
        "llm": round(weights.llm * _float(signals.get("llm", 0.0)), 4),
        "risk": round(-weights.risk * risk_signal, 4),
    }


def _counterfactual_flip_components(
    *,
    top: dict[str, Any],
    competitors: list[dict[str, Any]],
    weights: ScoreWeights,
) -> list[str]:
    if not competitors:
        return []
    top_score = _clamp(_float(top.get("score", 0.0)))
    top_contributions = _component_contributions(_dict(top.get("signals")), weights)
    flips = []
    for component in CORE_COMPONENTS:
        adjusted_top = _clamp(top_score - top_contributions.get(component, 0.0))
        adjusted_competitors = []
        for competitor in competitors:
            competitor_score = _clamp(_float(competitor.get("score", 0.0)))
            competitor_contributions = _component_contributions(
                _dict(competitor.get("signals")),
                weights,
            )
            adjusted_competitors.append(
                _clamp(
                    competitor_score
                    - competitor_contributions.get(component, 0.0)
                )
            )
        if adjusted_competitors and adjusted_top < max(adjusted_competitors):
            flips.append(component)
    return flips


def _weights_for_case(case: Any) -> ScoreWeights:
    coverage_mode = str(_get(case, "coverage_mode", "")).lower()
    if coverage_mode in {"static_only", "no_coverage"}:
        return DEFAULT_STATIC_ONLY_WEIGHTS
    return DEFAULT_COVERAGE_WEIGHTS


def _primary_component(contributions: dict[str, float]) -> tuple[str, float]:
    positive_items = {
        component: value
        for component, value in contributions.items()
        if component != "risk" and value > 0.0
    }
    if positive_items:
        component = max(
            positive_items,
            key=lambda name: (positive_items[name], name),
        )
        return component, round(positive_items[component], 4)
    component = max(
        contributions,
        key=lambda name: (abs(contributions[name]), name),
        default="none",
    )
    return component, round(contributions.get(component, 0.0), 4)


def _primary_component_counts(
    rows: list[LocalizationAttributionRow],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.primary_component] = counts.get(row.primary_component, 0) + 1
    return dict(sorted(counts.items()))


def _normalized_entropy(counts: dict[str, int]) -> float:
    total = sum(counts.values())
    if total <= 0 or len(counts) <= 1:
        return 0.0
    entropy = 0.0
    for count in counts.values():
        probability = count / total
        entropy -= probability * math.log(probability, 2)
    return round(entropy / math.log(len(counts), 2), 4)


def _top1_hit(case: Any, top: dict[str, Any]) -> bool:
    value = _get(case, "top1_hit", None)
    if value is not None:
        return bool(value)
    truth = {str(item) for item in _list(_get(case, "ground_truth", []))}
    top_name = str(top.get("function_name", top.get("function", "")))
    return top_name in truth if truth else False


def _cases(report_or_cases: Any) -> list[Any]:
    if isinstance(report_or_cases, list):
        return report_or_cases
    if isinstance(report_or_cases, dict):
        return list(report_or_cases.get("cases", []))
    return list(getattr(report_or_cases, "cases", []))


def _case_name(case: Any) -> str:
    return str(_get(case, "case_name", _get(case, "name", "")))


def _get(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, (set, tuple)):
        return list(value)
    return []


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
