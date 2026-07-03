from __future__ import annotations

from dataclasses import dataclass
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
class BenchmarkDifficultyCase:
    case_name: str
    bucket: str
    score: int
    labels: list[str]
    ground_truth_count: int
    max_call_chain_hops: int
    cross_function_data_flow_edges: int
    cross_file_callers: int
    patch_candidates_count: int
    evaluated_nodes: int
    failures_before_success: int
    success_depth: int
    multi_patch_bundle_size: int
    patch_risk_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_name": self.case_name,
            "bucket": self.bucket,
            "score": self.score,
            "labels": self.labels,
            "ground_truth_count": self.ground_truth_count,
            "max_call_chain_hops": self.max_call_chain_hops,
            "cross_function_data_flow_edges": self.cross_function_data_flow_edges,
            "cross_file_callers": self.cross_file_callers,
            "patch_candidates_count": self.patch_candidates_count,
            "evaluated_nodes": self.evaluated_nodes,
            "failures_before_success": self.failures_before_success,
            "success_depth": self.success_depth,
            "multi_patch_bundle_size": self.multi_patch_bundle_size,
            "patch_risk_score": self.patch_risk_score,
        }


@dataclass(frozen=True)
class BenchmarkDifficultyReport:
    case_count: int
    bucket_counts: dict[str, int]
    bucket_metrics: dict[str, dict[str, float]]
    label_counts: dict[str, int]
    label_metrics: dict[str, dict[str, float]]
    cases: list[BenchmarkDifficultyCase]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_count": self.case_count,
            "bucket_counts": self.bucket_counts,
            "bucket_metrics": self.bucket_metrics,
            "label_counts": self.label_counts,
            "label_metrics": self.label_metrics,
            "cases": [case.to_dict() for case in self.cases],
        }


def benchmark_difficulty_report(report_or_cases: Any) -> BenchmarkDifficultyReport:
    cases = _cases(report_or_cases)
    rows = [case_difficulty(case) for case in cases]
    bucket_groups = _group_by_bucket(cases, rows)
    label_groups = _group_by_label(cases, rows)
    return BenchmarkDifficultyReport(
        case_count=len(cases),
        bucket_counts={
            bucket: len(items)
            for bucket, items in sorted(bucket_groups.items())
        },
        bucket_metrics={
            bucket: _group_metrics(items)
            for bucket, items in sorted(bucket_groups.items())
        },
        label_counts={
            label: len(items)
            for label, items in sorted(label_groups.items())
        },
        label_metrics={
            label: _group_metrics(items)
            for label, items in sorted(label_groups.items())
        },
        cases=rows,
    )


def benchmark_difficulty_summary(report_or_cases: Any) -> dict[str, Any]:
    return benchmark_difficulty_report(report_or_cases).to_dict()


def case_difficulty(case: Any) -> BenchmarkDifficultyCase:
    ground_truth_count = len(set(getattr(case, "ground_truth", set())))
    max_call_chain_hops = _max_call_chain_hops(case)
    cross_function_data_flow_edges = _cross_function_data_flow_edges(case)
    patch_risk = getattr(case, "best_patch_risk", None) or {}
    cross_file_callers = int(patch_risk.get("cross_file_callers", 0))
    patch_candidates_count = int(getattr(case, "patch_candidates_count", 0))
    search_analysis = getattr(case, "search_analysis", {}) or {}
    evaluated_nodes = int(search_analysis.get("evaluated_nodes", 0))
    failures_before_success = int(search_analysis.get("failures_before_success", 0))
    success_depth = int(search_analysis.get("first_success_depth", 0) or 0)
    multi_patch_bundle_size = int(getattr(case, "multi_patch_bundle_size", 0))
    patch_risk_score = round(float(patch_risk.get("score", 0.0)), 4)

    labels = _difficulty_labels(
        ground_truth_count=ground_truth_count,
        max_call_chain_hops=max_call_chain_hops,
        cross_function_data_flow_edges=cross_function_data_flow_edges,
        cross_file_callers=cross_file_callers,
        patch_candidates_count=patch_candidates_count,
        evaluated_nodes=evaluated_nodes,
        failures_before_success=failures_before_success,
        success_depth=success_depth,
        repair_rounds=int(getattr(case, "repair_rounds", 0)),
        multi_patch_bundle_size=multi_patch_bundle_size,
        patch_risk_score=patch_risk_score,
    )
    score = _difficulty_score(
        ground_truth_count=ground_truth_count,
        max_call_chain_hops=max_call_chain_hops,
        cross_function_data_flow_edges=cross_function_data_flow_edges,
        cross_file_callers=cross_file_callers,
        patch_candidates_count=patch_candidates_count,
        evaluated_nodes=evaluated_nodes,
        failures_before_success=failures_before_success,
        success_depth=success_depth,
        repair_rounds=int(getattr(case, "repair_rounds", 0)),
        multi_patch_bundle_size=multi_patch_bundle_size,
        patch_risk_score=patch_risk_score,
    )
    return BenchmarkDifficultyCase(
        case_name=str(getattr(case, "case_name", "")),
        bucket=_difficulty_bucket(score),
        score=score,
        labels=labels,
        ground_truth_count=ground_truth_count,
        max_call_chain_hops=max_call_chain_hops,
        cross_function_data_flow_edges=cross_function_data_flow_edges,
        cross_file_callers=cross_file_callers,
        patch_candidates_count=patch_candidates_count,
        evaluated_nodes=evaluated_nodes,
        failures_before_success=failures_before_success,
        success_depth=success_depth,
        multi_patch_bundle_size=multi_patch_bundle_size,
        patch_risk_score=patch_risk_score,
    )


def _cases(report_or_cases: Any) -> list[Any]:
    if isinstance(report_or_cases, list):
        return report_or_cases
    return list(getattr(report_or_cases, "cases", []))


def _difficulty_labels(
    *,
    ground_truth_count: int,
    max_call_chain_hops: int,
    cross_function_data_flow_edges: int,
    cross_file_callers: int,
    patch_candidates_count: int,
    evaluated_nodes: int,
    failures_before_success: int,
    success_depth: int,
    repair_rounds: int,
    multi_patch_bundle_size: int,
    patch_risk_score: float,
) -> list[str]:
    labels = []
    if ground_truth_count > 1:
        labels.append("multi_ground_truth")
    if max_call_chain_hops >= 2:
        labels.append("cross_function_trace")
    if cross_function_data_flow_edges > 0:
        labels.append("cross_function_data_flow")
    if cross_file_callers > 0:
        labels.append("cross_file_patch")
    if patch_candidates_count > 1:
        labels.append("patch_candidate_competition")
    if evaluated_nodes > 3:
        labels.append("wide_beam_search")
    if failures_before_success > 0:
        labels.append("failed_before_success")
    if success_depth > 0 or repair_rounds > 1:
        labels.append("reflection_depth")
    if multi_patch_bundle_size > 1:
        labels.append("multi_patch_bundle")
    if patch_risk_score >= 0.30:
        labels.append("high_patch_risk")
    return labels or ["single_function_direct"]


def _difficulty_score(
    *,
    ground_truth_count: int,
    max_call_chain_hops: int,
    cross_function_data_flow_edges: int,
    cross_file_callers: int,
    patch_candidates_count: int,
    evaluated_nodes: int,
    failures_before_success: int,
    success_depth: int,
    repair_rounds: int,
    multi_patch_bundle_size: int,
    patch_risk_score: float,
) -> int:
    score = 0
    if ground_truth_count > 1:
        score += 2
    if max_call_chain_hops >= 2:
        score += 1
    if cross_function_data_flow_edges > 0:
        score += 1
    if cross_file_callers > 0:
        score += 1
    if patch_candidates_count > 1:
        score += 1
    if evaluated_nodes > 3:
        score += 1
    if failures_before_success > 0:
        score += 1
    if success_depth > 0 or repair_rounds > 1:
        score += 1
    if multi_patch_bundle_size > 1:
        score += 2
    if patch_risk_score >= 0.30:
        score += 1
    return score


def _difficulty_bucket(score: int) -> str:
    if score >= 4:
        return "hard"
    if score >= 2:
        return "medium"
    return "easy"


def _max_call_chain_hops(case: Any) -> int:
    max_hops = 0
    for detail in getattr(case, "localization_details", []) or []:
        chain = detail.get("call_chain", [])
        if isinstance(chain, list) and chain:
            max_hops = max(max_hops, max(0, len(chain) - 1))
    return max_hops


def _cross_function_data_flow_edges(case: Any) -> int:
    total = 0
    for detail in getattr(case, "localization_details", []) or []:
        evidence = detail.get("data_flow_evidence", {})
        if isinstance(evidence, dict):
            total += int(evidence.get("cross_function_edges", 0))
    return total


def _group_by_bucket(
    cases: list[Any],
    rows: list[BenchmarkDifficultyCase],
) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {}
    for case, row in zip(cases, rows):
        grouped.setdefault(row.bucket, []).append(case)
    return grouped


def _group_by_label(
    cases: list[Any],
    rows: list[BenchmarkDifficultyCase],
) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {}
    for case, row in zip(cases, rows):
        for label in row.labels:
            grouped.setdefault(label, []).append(case)
    return grouped


def _group_metrics(items: list[Any]) -> dict[str, float]:
    runs = [
        LocalizationRun(
            ranked=list(getattr(item, "ranked_functions", [])),
            ground_truth=set(getattr(item, "ground_truth", set())),
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
        "patch_success_rate": round(
            patch_success_rate([bool(getattr(item, "patch_success", False)) for item in items]),
            4,
        ),
        "multi_patch_success_rate": round(
            patch_success_rate(
                [
                    bool(getattr(item, "multi_patch_success", False))
                    for item in items
                    if getattr(item, "multi_patch_results", [])
                ]
            ),
            4,
        ),
        "average_patch_candidates": round(
            average([int(getattr(item, "patch_candidates_count", 0)) for item in items]),
            4,
        ),
        "average_evaluated_nodes": round(
            average(
                [
                    int((getattr(item, "search_analysis", {}) or {}).get("evaluated_nodes", 0))
                    for item in items
                ]
            ),
            4,
        ),
        "average_failures_before_success": round(
            average(
                [
                    int(
                        (getattr(item, "search_analysis", {}) or {}).get(
                            "failures_before_success",
                            0,
                        )
                    )
                    for item in items
                ]
            ),
            4,
        ),
        "average_patch_risk": round(
            average(
                [
                    float((getattr(item, "best_patch_risk", None) or {}).get("score", 0.0))
                    for item in items
                ]
            ),
            4,
        ),
    }
