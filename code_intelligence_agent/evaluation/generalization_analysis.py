from __future__ import annotations

from dataclasses import asdict, dataclass
from math import log2
from typing import Any

from code_intelligence_agent.evaluation.metrics import (
    LocalizationRun,
    average,
    mean_average_precision,
    mean_exam_score,
    mean_ndcg,
    mean_reciprocal_rank,
    patch_success_rate,
    top_k_accuracy,
)


@dataclass(frozen=True)
class GeneralizationGroupMetrics:
    case_count: int
    top1: float
    top3: float
    mrr: float
    map: float
    ndcg_at_3: float
    mean_exam_score: float
    patch_success_rate: float
    search_efficiency: float
    average_evaluated_nodes: float
    average_patch_risk: float
    bug_type_count: int
    expected_rule_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HoldoutSplitMetrics:
    holdout_group: str
    train_groups: list[str]
    train_metrics: GeneralizationGroupMetrics
    holdout_metrics: GeneralizationGroupMetrics
    top1_gap: float
    map_gap: float
    patch_success_gap: float
    search_efficiency_gap: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "holdout_group": self.holdout_group,
            "train_groups": self.train_groups,
            "train_metrics": self.train_metrics.to_dict(),
            "holdout_metrics": self.holdout_metrics.to_dict(),
            "top1_gap": self.top1_gap,
            "map_gap": self.map_gap,
            "patch_success_gap": self.patch_success_gap,
            "search_efficiency_gap": self.search_efficiency_gap,
        }


@dataclass(frozen=True)
class BenchmarkGeneralizationReport:
    case_count: int
    split_key: str
    source_group_count: int
    source_groups: dict[str, GeneralizationGroupMetrics]
    holdout_splits: list[HoldoutSplitMetrics]
    min_holdout_case_count: int
    source_balance_entropy: float
    source_imbalance_ratio: float
    max_top1_gap: float
    max_map_gap: float
    max_patch_success_gap: float
    max_search_efficiency_gap: float
    worst_holdout_group: str
    worst_holdout_gap_score: float
    stability_score: float
    risk_level: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_count": self.case_count,
            "split_key": self.split_key,
            "source_group_count": self.source_group_count,
            "source_groups": {
                name: metrics.to_dict()
                for name, metrics in self.source_groups.items()
            },
            "holdout_splits": [split.to_dict() for split in self.holdout_splits],
            "min_holdout_case_count": self.min_holdout_case_count,
            "source_balance_entropy": self.source_balance_entropy,
            "source_imbalance_ratio": self.source_imbalance_ratio,
            "max_top1_gap": self.max_top1_gap,
            "max_map_gap": self.max_map_gap,
            "max_patch_success_gap": self.max_patch_success_gap,
            "max_search_efficiency_gap": self.max_search_efficiency_gap,
            "worst_holdout_group": self.worst_holdout_group,
            "worst_holdout_gap_score": self.worst_holdout_gap_score,
            "stability_score": self.stability_score,
            "risk_level": self.risk_level,
        }


def benchmark_generalization_summary(report_or_cases: Any) -> dict[str, Any]:
    return benchmark_generalization_report(report_or_cases).to_dict()


def benchmark_generalization_report(
    report_or_cases: Any,
    split_key: str = "upstream",
) -> BenchmarkGeneralizationReport:
    cases = _cases(report_or_cases)
    groups = _group_by_source(cases, split_key=split_key)
    source_groups = {
        group: _group_metrics(items)
        for group, items in sorted(groups.items())
    }
    holdout_splits = _holdout_splits(groups)
    gap_summary = _holdout_gap_summary(holdout_splits)
    return BenchmarkGeneralizationReport(
        case_count=len(cases),
        split_key=split_key,
        source_group_count=len(groups),
        source_groups=source_groups,
        holdout_splits=holdout_splits,
        min_holdout_case_count=min(
            (split.holdout_metrics.case_count for split in holdout_splits),
            default=0,
        ),
        source_balance_entropy=_source_balance_entropy(groups),
        source_imbalance_ratio=_source_imbalance_ratio(groups),
        max_top1_gap=_max_abs_gap(split.top1_gap for split in holdout_splits),
        max_map_gap=_max_abs_gap(split.map_gap for split in holdout_splits),
        max_patch_success_gap=_max_abs_gap(
            split.patch_success_gap for split in holdout_splits
        ),
        max_search_efficiency_gap=_max_abs_gap(
            split.search_efficiency_gap for split in holdout_splits
        ),
        worst_holdout_group=str(gap_summary["worst_holdout_group"]),
        worst_holdout_gap_score=float(gap_summary["worst_holdout_gap_score"]),
        stability_score=float(gap_summary["stability_score"]),
        risk_level=str(gap_summary["risk_level"]),
    )


def _cases(report_or_cases: Any) -> list[Any]:
    if isinstance(report_or_cases, list):
        return report_or_cases
    if isinstance(report_or_cases, dict):
        return list(report_or_cases.get("cases", []))
    return list(getattr(report_or_cases, "cases", []))


def _group_by_source(cases: list[Any], split_key: str) -> dict[str, list[Any]]:
    groups: dict[str, list[Any]] = {}
    for case in cases:
        groups.setdefault(_source_group(case, split_key=split_key), []).append(case)
    return groups


def _source_group(case: Any, split_key: str) -> str:
    metadata = _metadata(case)
    candidates = (
        metadata.get(split_key),
        metadata.get("upstream"),
        metadata.get("source_project"),
        metadata.get("source_repo"),
        metadata.get("repo"),
        metadata.get("project"),
    )
    for candidate in candidates:
        if candidate:
            return str(candidate)
    return _infer_source_group(str(_get(case, "case_name", "")))


def _metadata(case: Any) -> dict[str, Any]:
    metadata = _get(case, "metadata", {})
    return metadata if isinstance(metadata, dict) else {}


def _infer_source_group(case_name: str) -> str:
    if case_name.startswith("cpython_"):
        return "python/cpython"
    if case_name.startswith("thealgorithms_"):
        return "TheAlgorithms/Python"
    if case_name.startswith("pluggy_"):
        return "pytest-dev/pluggy"
    return "unspecified"


def _holdout_splits(groups: dict[str, list[Any]]) -> list[HoldoutSplitMetrics]:
    if len(groups) <= 1:
        return []
    splits = []
    for holdout_group, holdout_items in sorted(groups.items()):
        train_groups = [
            group for group in sorted(groups) if group != holdout_group
        ]
        train_items = [
            case
            for group, items in groups.items()
            if group != holdout_group
            for case in items
        ]
        train_metrics = _group_metrics(train_items)
        holdout_metrics = _group_metrics(holdout_items)
        splits.append(
            HoldoutSplitMetrics(
                holdout_group=holdout_group,
                train_groups=train_groups,
                train_metrics=train_metrics,
                holdout_metrics=holdout_metrics,
                top1_gap=_gap(train_metrics.top1, holdout_metrics.top1),
                map_gap=_gap(train_metrics.map, holdout_metrics.map),
                patch_success_gap=_gap(
                    train_metrics.patch_success_rate,
                    holdout_metrics.patch_success_rate,
                ),
                search_efficiency_gap=_gap(
                    train_metrics.search_efficiency,
                    holdout_metrics.search_efficiency,
                ),
            )
        )
    return splits


def _group_metrics(items: list[Any]) -> GeneralizationGroupMetrics:
    runs = [
        LocalizationRun(
            ranked=list(_get(item, "ranked_functions", [])),
            ground_truth=set(_get(item, "ground_truth", set())),
        )
        for item in items
    ]
    return GeneralizationGroupMetrics(
        case_count=len(items),
        top1=round(top_k_accuracy(runs, 1), 4),
        top3=round(top_k_accuracy(runs, 3), 4),
        mrr=round(mean_reciprocal_rank(runs), 4),
        map=round(mean_average_precision(runs), 4),
        ndcg_at_3=round(mean_ndcg(runs, 3), 4),
        mean_exam_score=round(mean_exam_score(runs), 4),
        patch_success_rate=round(
            patch_success_rate(
                [bool(_get(item, "patch_success", False)) for item in items]
            ),
            4,
        ),
        search_efficiency=round(
            average(
                [
                    float(_search_analysis(item).get("efficiency", 0.0))
                    for item in items
                ]
            ),
            4,
        ),
        average_evaluated_nodes=round(
            average(
                [
                    float(_search_analysis(item).get("evaluated_nodes", 0.0))
                    for item in items
                ]
            ),
            4,
        ),
        average_patch_risk=round(
            average(
                [
                    float(_patch_risk(item).get("score", 0.0))
                    for item in items
                ]
            ),
            4,
        ),
        bug_type_count=len(
            {str(_get(item, "bug_type", "unspecified")) for item in items}
        ),
        expected_rule_count=len(
            {
                str(rule)
                for item in items
                for rule in list(_get(item, "expected_rule_ids", []))
            }
        ),
    )


def _search_analysis(item: Any) -> dict[str, Any]:
    analysis = _get(item, "search_analysis", {})
    return analysis if isinstance(analysis, dict) else {}


def _patch_risk(item: Any) -> dict[str, Any]:
    risk = _get(item, "best_patch_risk", {})
    return risk if isinstance(risk, dict) else {}


def _get(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _gap(train_value: float, holdout_value: float) -> float:
    return round(train_value - holdout_value, 4)


def _max_abs_gap(values: Any) -> float:
    return round(max((abs(float(value)) for value in values), default=0.0), 4)


def _source_balance_entropy(groups: dict[str, list[Any]]) -> float:
    if not groups:
        return 0.0
    if len(groups) == 1:
        return 1.0
    total = sum(len(items) for items in groups.values())
    if total <= 0:
        return 0.0
    entropy = 0.0
    for items in groups.values():
        probability = len(items) / total
        if probability > 0:
            entropy -= probability * log2(probability)
    return round(entropy / log2(len(groups)), 4)


def _source_imbalance_ratio(groups: dict[str, list[Any]]) -> float:
    counts = [len(items) for items in groups.values() if len(items) > 0]
    if not counts:
        return 0.0
    return round(max(counts) / min(counts), 4)


def _holdout_gap_summary(
    splits: list[HoldoutSplitMetrics],
) -> dict[str, object]:
    if not splits:
        return {
            "worst_holdout_group": "",
            "worst_holdout_gap_score": 0.0,
            "stability_score": 1.0,
            "risk_level": "low",
        }
    scored = [
        (
            _holdout_gap_score(split),
            split.holdout_metrics.case_count,
            split.holdout_group,
        )
        for split in splits
    ]
    worst_score, _, worst_group = max(scored)
    stability = round(max(0.0, 1.0 - worst_score), 4)
    return {
        "worst_holdout_group": worst_group,
        "worst_holdout_gap_score": worst_score,
        "stability_score": stability,
        "risk_level": _risk_level(worst_score),
    }


def _holdout_gap_score(split: HoldoutSplitMetrics) -> float:
    score = (
        0.35 * abs(split.top1_gap)
        + 0.30 * abs(split.map_gap)
        + 0.25 * abs(split.patch_success_gap)
        + 0.10 * min(1.0, abs(split.search_efficiency_gap))
    )
    return round(score, 4)


def _risk_level(worst_gap_score: float) -> str:
    if worst_gap_score >= 0.35:
        return "high"
    if worst_gap_score >= 0.15:
        return "medium"
    return "low"
