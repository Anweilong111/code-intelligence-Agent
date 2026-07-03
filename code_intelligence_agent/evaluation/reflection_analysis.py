from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ReflectionCaseRow:
    case: str
    beam_node_count: int
    repair_loop_attempt_count: int
    reflection_candidate_count: int
    retained_reflection_candidate_count: int
    successful_reflection_candidate_count: int
    reflection_success: bool
    max_reflection_depth: int
    first_success_reflection_depth: int | None
    average_score_delta_from_parent: float
    parent_failure_types: list[str]
    parent_retention_buckets: list[str]
    success_parent_failure_type: str
    success_parent_retention_bucket: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReflectionAnalysisReport:
    case_count: int
    reflection_case_count: int
    reflection_success_case_count: int
    reflection_candidate_count: int
    retained_reflection_candidate_count: int
    successful_reflection_candidate_count: int
    reflection_case_success_rate: float
    reflection_candidate_success_rate: float
    average_reflection_depth: float
    average_success_reflection_depth: float
    average_score_delta_from_parent: float
    parent_failure_type_counts: dict[str, int]
    parent_retention_bucket_counts: dict[str, int]
    success_parent_failure_type_counts: dict[str, int]
    success_parent_retention_bucket_counts: dict[str, int]
    rows: list[ReflectionCaseRow]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_count": self.case_count,
            "reflection_case_count": self.reflection_case_count,
            "reflection_success_case_count": self.reflection_success_case_count,
            "reflection_candidate_count": self.reflection_candidate_count,
            "retained_reflection_candidate_count": (
                self.retained_reflection_candidate_count
            ),
            "successful_reflection_candidate_count": (
                self.successful_reflection_candidate_count
            ),
            "reflection_case_success_rate": self.reflection_case_success_rate,
            "reflection_candidate_success_rate": (
                self.reflection_candidate_success_rate
            ),
            "average_reflection_depth": self.average_reflection_depth,
            "average_success_reflection_depth": (
                self.average_success_reflection_depth
            ),
            "average_score_delta_from_parent": (
                self.average_score_delta_from_parent
            ),
            "parent_failure_type_counts": self.parent_failure_type_counts,
            "parent_retention_bucket_counts": self.parent_retention_bucket_counts,
            "success_parent_failure_type_counts": (
                self.success_parent_failure_type_counts
            ),
            "success_parent_retention_bucket_counts": (
                self.success_parent_retention_bucket_counts
            ),
            "rows": [row.to_dict() for row in self.rows],
        }


def reflection_analysis_summary(report: Any) -> dict[str, Any]:
    return reflection_analysis_report(report).to_dict()


def reflection_analysis_report(report: Any) -> ReflectionAnalysisReport:
    rows = reflection_case_rows(report)
    reflection_rows = [
        row for row in rows if row.reflection_candidate_count > 0
    ]
    success_rows = [row for row in reflection_rows if row.reflection_success]
    reflection_candidate_count = sum(
        row.reflection_candidate_count for row in reflection_rows
    )
    successful_reflection_candidate_count = sum(
        row.successful_reflection_candidate_count for row in reflection_rows
    )
    score_deltas = [
        row.average_score_delta_from_parent
        for row in reflection_rows
        if row.reflection_candidate_count > 0
    ]
    return ReflectionAnalysisReport(
        case_count=len(rows),
        reflection_case_count=len(reflection_rows),
        reflection_success_case_count=len(success_rows),
        reflection_candidate_count=reflection_candidate_count,
        retained_reflection_candidate_count=sum(
            row.retained_reflection_candidate_count for row in reflection_rows
        ),
        successful_reflection_candidate_count=(
            successful_reflection_candidate_count
        ),
        reflection_case_success_rate=_ratio(len(success_rows), len(reflection_rows)),
        reflection_candidate_success_rate=_ratio(
            successful_reflection_candidate_count,
            reflection_candidate_count,
        ),
        average_reflection_depth=round(
            _average(row.max_reflection_depth for row in reflection_rows),
            4,
        ),
        average_success_reflection_depth=round(
            _average(
                row.first_success_reflection_depth
                for row in success_rows
                if row.first_success_reflection_depth is not None
            ),
            4,
        ),
        average_score_delta_from_parent=round(_average(score_deltas), 4),
        parent_failure_type_counts=_count_values(
            failure_type
            for row in reflection_rows
            for failure_type in row.parent_failure_types
            if failure_type
        ),
        parent_retention_bucket_counts=_count_values(
            bucket
            for row in reflection_rows
            for bucket in row.parent_retention_buckets
            if bucket
        ),
        success_parent_failure_type_counts=_count_values(
            row.success_parent_failure_type
            for row in success_rows
            if row.success_parent_failure_type
        ),
        success_parent_retention_bucket_counts=_count_values(
            row.success_parent_retention_bucket
            for row in success_rows
            if row.success_parent_retention_bucket
        ),
        rows=rows,
    )


def reflection_case_rows(report: Any) -> list[ReflectionCaseRow]:
    rows: list[ReflectionCaseRow] = []
    for case in _get_cases(report):
        beam_nodes = [
            node
            for node in _list(_get(case, "beam_search_results", []))
            if isinstance(node, dict)
        ]
        repair_attempts = [
            attempt
            for attempt in _list(_get(case, "repair_results", []))
            if isinstance(attempt, dict)
        ]
        row = _case_row(
            case_name=str(_get(case, "case_name", _get(case, "name", ""))),
            beam_nodes=beam_nodes,
            repair_attempts=repair_attempts,
        )
        rows.append(row)
    return rows


def _case_row(
    *,
    case_name: str,
    beam_nodes: list[dict[str, Any]],
    repair_attempts: list[dict[str, Any]],
) -> ReflectionCaseRow:
    observations = [
        *_beam_reflection_observations(beam_nodes),
        *_repair_loop_reflection_observations(repair_attempts),
    ]
    successful = [
        item for item in observations if bool(item.get("success", False))
    ]
    depths = [_int(item.get("depth", 0)) for item in observations]
    score_deltas = [
        _float(item.get("score_delta_from_parent", 0.0))
        for item in observations
    ]
    return ReflectionCaseRow(
        case=case_name,
        beam_node_count=len(beam_nodes),
        repair_loop_attempt_count=len(repair_attempts),
        reflection_candidate_count=len(observations),
        retained_reflection_candidate_count=sum(
            1 for item in observations if bool(item.get("retained", True))
        ),
        successful_reflection_candidate_count=len(successful),
        reflection_success=bool(successful),
        max_reflection_depth=max(depths, default=0),
        first_success_reflection_depth=(
            min(_int(item.get("depth", 0)) for item in successful)
            if successful
            else None
        ),
        average_score_delta_from_parent=round(_average(score_deltas), 4),
        parent_failure_types=[
            str(item.get("parent_failure_type", ""))
            for item in observations
            if str(item.get("parent_failure_type", ""))
        ],
        parent_retention_buckets=[
            str(item.get("parent_retention_bucket", ""))
            for item in observations
            if str(item.get("parent_retention_bucket", ""))
        ],
        success_parent_failure_type=(
            str(successful[0].get("parent_failure_type", "")) if successful else ""
        ),
        success_parent_retention_bucket=(
            str(successful[0].get("parent_retention_bucket", ""))
            if successful
            else ""
        ),
    )


def _beam_reflection_observations(
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {
        str(node.get("candidate_id", "")): node
        for node in nodes
        if str(node.get("candidate_id", ""))
    }
    observations = []
    for node in nodes:
        parent_id = str(node.get("parent_id", "") or "")
        depth = _int(node.get("depth", 0))
        if not parent_id and depth <= 0:
            continue
        parent = by_id.get(parent_id, {})
        observations.append(
            {
                "success": bool(node.get("success", False)),
                "retained": bool(node.get("retained", True)),
                "depth": max(1, depth),
                "parent_failure_type": str(parent.get("failure_type", "")),
                "parent_retention_bucket": str(
                    parent.get("retention_bucket", "")
                ),
                "score_delta_from_parent": _float(node.get("score", 0.0))
                - _float(parent.get("score", 0.0)),
            }
        )
    return observations


def _repair_loop_reflection_observations(
    attempts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {
        str(attempt.get("candidate_id", "")): attempt
        for attempt in attempts
        if str(attempt.get("candidate_id", ""))
    }
    observations = []
    for attempt in attempts:
        parent_id = str(attempt.get("repair_loop_parent_id", "") or "")
        if not parent_id:
            continue
        parent = by_id.get(parent_id, {})
        observations.append(
            {
                "success": bool(attempt.get("success", False)),
                "retained": True,
                "depth": max(1, _int(attempt.get("round", 0))),
                "parent_failure_type": str(parent.get("failure_type", "")),
                "parent_retention_bucket": "repair_loop",
                "score_delta_from_parent": _float(attempt.get("score", 0.0))
                - _float(parent.get("score", 0.0)),
            }
        )
    return observations


def _get_cases(report: Any) -> list[Any]:
    if isinstance(report, dict):
        cases = report.get("cases", [])
    else:
        cases = getattr(report, "cases", [])
    return cases if isinstance(cases, list) else []


def _get(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _count_values(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _average(values: Any) -> float:
    collected = [float(value) for value in values if value is not None]
    if not collected:
        return 0.0
    return sum(collected) / len(collected)


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
