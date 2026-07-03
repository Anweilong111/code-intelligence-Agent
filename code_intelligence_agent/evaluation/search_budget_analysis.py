from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


DEFAULT_SEARCH_BUDGETS = (1, 2, 3, 5, 8)


@dataclass(frozen=True)
class SearchBudgetCaseRow:
    case: str
    evaluated_nodes: int
    deduplicated_candidates: int
    effective_candidate_pool: int
    duplicate_pressure: float
    first_success_rank: int | None
    success: bool
    normalized_effort: float
    wasted_nodes_after_success: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SearchBudgetPoint:
    budget: int
    success_count: int
    case_count: int
    success_rate: float
    marginal_success_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SearchBudgetAnalysisReport:
    case_count: int
    evaluated_case_count: int
    successful_case_count: int
    dedupe_affected_case_count: int
    total_deduplicated_candidates: int
    max_budget: int
    max_deduplicated_candidates: int
    budget_auc: float
    success_at_budget: dict[str, float]
    first_success_rank_p50: float
    first_success_rank_p90: float
    average_normalized_effort: float
    average_wasted_nodes_after_success: float
    average_deduplicated_candidates: float
    average_duplicate_pressure: float
    budget_points: list[SearchBudgetPoint]
    rows: list[SearchBudgetCaseRow]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_count": self.case_count,
            "evaluated_case_count": self.evaluated_case_count,
            "successful_case_count": self.successful_case_count,
            "dedupe_affected_case_count": self.dedupe_affected_case_count,
            "total_deduplicated_candidates": self.total_deduplicated_candidates,
            "max_budget": self.max_budget,
            "max_deduplicated_candidates": self.max_deduplicated_candidates,
            "budget_auc": self.budget_auc,
            "success_at_budget": self.success_at_budget,
            "first_success_rank_p50": self.first_success_rank_p50,
            "first_success_rank_p90": self.first_success_rank_p90,
            "average_normalized_effort": self.average_normalized_effort,
            "average_wasted_nodes_after_success": (
                self.average_wasted_nodes_after_success
            ),
            "average_deduplicated_candidates": self.average_deduplicated_candidates,
            "average_duplicate_pressure": self.average_duplicate_pressure,
            "budget_points": [item.to_dict() for item in self.budget_points],
            "rows": [item.to_dict() for item in self.rows],
        }


def search_budget_analysis_summary(report: Any) -> dict[str, Any]:
    return search_budget_analysis_report(report).to_dict()


def search_budget_analysis_report(
    report: Any,
    budgets: tuple[int, ...] = DEFAULT_SEARCH_BUDGETS,
) -> SearchBudgetAnalysisReport:
    rows = search_budget_case_rows(report)
    evaluated_rows = [row for row in rows if row.evaluated_nodes > 0]
    evaluated_case_count = len(evaluated_rows)
    successful_case_count = sum(1 for row in evaluated_rows if row.success)
    max_budget = max((row.evaluated_nodes for row in evaluated_rows), default=0)
    budget_values = _budget_values(budgets, max_budget)
    points = [
        _budget_point(evaluated_rows, budget, previous_budget)
        for previous_budget, budget in zip([0, *budget_values[:-1]], budget_values)
    ]
    success_at_budget = {
        str(point.budget): point.success_rate
        for point in points
    }
    first_success_ranks = [
        row.first_success_rank
        for row in evaluated_rows
        if row.first_success_rank is not None
    ]
    return SearchBudgetAnalysisReport(
        case_count=len(rows),
        evaluated_case_count=evaluated_case_count,
        successful_case_count=successful_case_count,
        dedupe_affected_case_count=sum(
            1 for row in evaluated_rows if row.deduplicated_candidates > 0
        ),
        total_deduplicated_candidates=sum(
            row.deduplicated_candidates for row in evaluated_rows
        ),
        max_budget=max_budget,
        max_deduplicated_candidates=max(
            (row.deduplicated_candidates for row in evaluated_rows),
            default=0,
        ),
        budget_auc=round(_average([point.success_rate for point in points]), 4),
        success_at_budget=success_at_budget,
        first_success_rank_p50=_rank_percentile(first_success_ranks, 0.50),
        first_success_rank_p90=_rank_percentile(first_success_ranks, 0.90),
        average_normalized_effort=round(
            _average([row.normalized_effort for row in evaluated_rows]),
            4,
        ),
        average_wasted_nodes_after_success=round(
            _average([row.wasted_nodes_after_success for row in evaluated_rows]),
            4,
        ),
        average_deduplicated_candidates=round(
            _average([row.deduplicated_candidates for row in evaluated_rows]),
            4,
        ),
        average_duplicate_pressure=round(
            _average([row.duplicate_pressure for row in evaluated_rows]),
            4,
        ),
        budget_points=points,
        rows=rows,
    )


def search_budget_case_rows(report: Any) -> list[SearchBudgetCaseRow]:
    rows: list[SearchBudgetCaseRow] = []
    for case in _get_cases(report):
        analysis = _get(case, "search_analysis", {})
        if not isinstance(analysis, dict):
            analysis = {}
        evaluated_nodes = max(0, _int(analysis.get("evaluated_nodes", 0)))
        deduplicated_candidates = max(
            0,
            _int(analysis.get("deduplicated_candidates", 0)),
        )
        effective_candidate_pool = max(
            evaluated_nodes + deduplicated_candidates,
            _int(analysis.get("effective_candidate_pool", 0)),
        )
        first_success_rank = _optional_int(analysis.get("first_success_rank"))
        success = first_success_rank is not None
        normalized_effort = _normalized_effort(
            evaluated_nodes=evaluated_nodes,
            first_success_rank=first_success_rank,
        )
        rows.append(
            SearchBudgetCaseRow(
                case=str(_get(case, "case_name", _get(case, "name", ""))),
                evaluated_nodes=evaluated_nodes,
                deduplicated_candidates=deduplicated_candidates,
                effective_candidate_pool=effective_candidate_pool,
                duplicate_pressure=_duplicate_pressure(
                    deduplicated_candidates=deduplicated_candidates,
                    effective_candidate_pool=effective_candidate_pool,
                ),
                first_success_rank=first_success_rank,
                success=success,
                normalized_effort=normalized_effort,
                wasted_nodes_after_success=_wasted_nodes_after_success(
                    evaluated_nodes=evaluated_nodes,
                    first_success_rank=first_success_rank,
                ),
            )
        )
    return rows


def _budget_point(
    rows: list[SearchBudgetCaseRow],
    budget: int,
    previous_budget: int,
) -> SearchBudgetPoint:
    if not rows:
        return SearchBudgetPoint(
            budget=budget,
            success_count=0,
            case_count=0,
            success_rate=0.0,
            marginal_success_count=0,
        )
    success_count = sum(
        1
        for row in rows
        if row.first_success_rank is not None
        and row.first_success_rank <= budget
    )
    marginal_success_count = sum(
        1
        for row in rows
        if row.first_success_rank is not None
        and previous_budget < row.first_success_rank <= budget
    )
    return SearchBudgetPoint(
        budget=budget,
        success_count=success_count,
        case_count=len(rows),
        success_rate=round(success_count / len(rows), 4),
        marginal_success_count=marginal_success_count,
    )


def _budget_values(budgets: tuple[int, ...], max_budget: int) -> list[int]:
    if max_budget <= 0:
        return []
    values = {
        int(budget)
        for budget in budgets
        if int(budget) > 0 and int(budget) <= max_budget
    }
    values.add(max_budget)
    return sorted(values)


def _normalized_effort(
    *,
    evaluated_nodes: int,
    first_success_rank: int | None,
) -> float:
    if evaluated_nodes <= 0:
        return 0.0
    if first_success_rank is None:
        return 1.0
    return round(min(1.0, max(0.0, first_success_rank / evaluated_nodes)), 4)


def _wasted_nodes_after_success(
    *,
    evaluated_nodes: int,
    first_success_rank: int | None,
) -> int:
    if evaluated_nodes <= 0:
        return 0
    if first_success_rank is None:
        return evaluated_nodes
    return max(0, evaluated_nodes - first_success_rank)


def _duplicate_pressure(
    *,
    deduplicated_candidates: int,
    effective_candidate_pool: int,
) -> float:
    if effective_candidate_pool <= 0:
        return 0.0
    return round(
        min(1.0, max(0.0, deduplicated_candidates / effective_candidate_pool)),
        4,
    )


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


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _average(values: list[float | int]) -> float:
    return sum(float(value) for value in values) / len(values) if values else 0.0


def _rank_percentile(values: list[int], quantile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    position = max(0.0, min(1.0, quantile)) * (len(sorted_values) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = position - lower_index
    value = (
        sorted_values[lower_index] * (1.0 - fraction)
        + sorted_values[upper_index] * fraction
    )
    return round(value, 4)
