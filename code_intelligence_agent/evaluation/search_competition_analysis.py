from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class SearchCompetitionCaseRow:
    case: str
    evaluated_nodes: int
    failed_nodes: int
    successful_nodes: int
    first_success_rank: int | None
    top_rank_success: bool
    top_rank_failure_type: str
    score_inversion: bool
    rule_diversity: int
    failure_type_diversity: int
    retention_bucket_diversity: int
    failure_pressure: float
    competing_failures_before_success: int
    max_diversity_lift: int
    success_diversity_lift: int
    average_diversity_bonus: float
    success_diversity_bonus: float
    diversity_assisted_success: bool
    success_base_rank: int
    success_diversity_rank: int
    success_actual_rank: int | None
    success_budget_gap_before_rerank: int
    success_budget_margin_after_rerank: int
    budget_sensitive_diversity_success: bool
    counterfactual_condition: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SearchCompetitionAnalysisReport:
    case_count: int
    beam_case_count: int
    multi_candidate_case_count: int
    successful_case_count: int
    top_rank_success_count: int
    score_inversion_count: int
    average_failure_pressure: float
    average_rule_diversity: float
    average_failure_type_diversity: float
    average_retention_bucket_diversity: float
    multi_candidate_average_rule_diversity: float
    multi_candidate_average_failure_type_diversity: float
    multi_candidate_average_retention_bucket_diversity: float
    average_competing_failures_before_success: float
    diversity_lift_case_count: int
    diversity_assisted_success_count: int
    average_diversity_lift: float
    average_success_diversity_lift: float
    average_diversity_bonus: float
    average_success_diversity_bonus: float
    budget_sensitive_diversity_success_count: int
    average_success_budget_gap_before_rerank: float
    average_success_budget_margin_after_rerank: float
    max_diversity_lift: int
    top_failure_type_counts: dict[str, int]
    rows: list[SearchCompetitionCaseRow]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_count": self.case_count,
            "beam_case_count": self.beam_case_count,
            "multi_candidate_case_count": self.multi_candidate_case_count,
            "successful_case_count": self.successful_case_count,
            "top_rank_success_count": self.top_rank_success_count,
            "score_inversion_count": self.score_inversion_count,
            "score_inversion_rate": _ratio(
                self.score_inversion_count,
                self.successful_case_count,
            ),
            "average_failure_pressure": self.average_failure_pressure,
            "average_rule_diversity": self.average_rule_diversity,
            "average_failure_type_diversity": self.average_failure_type_diversity,
            "average_retention_bucket_diversity": (
                self.average_retention_bucket_diversity
            ),
            "multi_candidate_average_rule_diversity": (
                self.multi_candidate_average_rule_diversity
            ),
            "multi_candidate_average_failure_type_diversity": (
                self.multi_candidate_average_failure_type_diversity
            ),
            "multi_candidate_average_retention_bucket_diversity": (
                self.multi_candidate_average_retention_bucket_diversity
            ),
            "average_competing_failures_before_success": (
                self.average_competing_failures_before_success
            ),
            "diversity_lift_case_count": self.diversity_lift_case_count,
            "diversity_lift_case_rate": _ratio(
                self.diversity_lift_case_count,
                self.beam_case_count,
            ),
            "diversity_assisted_success_count": (
                self.diversity_assisted_success_count
            ),
            "diversity_assisted_success_rate": _ratio(
                self.diversity_assisted_success_count,
                self.successful_case_count,
            ),
            "average_diversity_lift": self.average_diversity_lift,
            "average_success_diversity_lift": self.average_success_diversity_lift,
            "average_diversity_bonus": self.average_diversity_bonus,
            "average_success_diversity_bonus": self.average_success_diversity_bonus,
            "budget_sensitive_diversity_success_count": (
                self.budget_sensitive_diversity_success_count
            ),
            "budget_sensitive_diversity_success_rate": _ratio(
                self.budget_sensitive_diversity_success_count,
                self.successful_case_count,
            ),
            "projected_without_diversity_success_count": max(
                0,
                self.successful_case_count
                - self.budget_sensitive_diversity_success_count,
            ),
            "projected_without_diversity_success_rate": _ratio(
                max(
                    0,
                    self.successful_case_count
                    - self.budget_sensitive_diversity_success_count,
                ),
                self.beam_case_count,
            ),
            "projected_without_diversity_success_delta": round(
                _ratio(
                    max(
                        0,
                        self.successful_case_count
                        - self.budget_sensitive_diversity_success_count,
                    ),
                    self.beam_case_count,
                )
                - _ratio(self.successful_case_count, self.beam_case_count),
                4,
            ),
            "average_success_budget_gap_before_rerank": (
                self.average_success_budget_gap_before_rerank
            ),
            "average_success_budget_margin_after_rerank": (
                self.average_success_budget_margin_after_rerank
            ),
            "max_diversity_lift": self.max_diversity_lift,
            "top_failure_type_counts": self.top_failure_type_counts,
            "rows": [row.to_dict() for row in self.rows],
        }


def search_competition_analysis_summary(report: Any) -> dict[str, Any]:
    return search_competition_analysis_report(report).to_dict()


def search_competition_analysis_report(
    report: Any,
) -> SearchCompetitionAnalysisReport:
    rows = search_competition_case_rows(report)
    beam_rows = [row for row in rows if row.evaluated_nodes > 0]
    multi_candidate_rows = [row for row in beam_rows if row.evaluated_nodes > 1]
    successful_rows = [row for row in beam_rows if row.successful_nodes > 0]
    top_failure_type_counts = _count_by(
        row.top_rank_failure_type for row in beam_rows if row.top_rank_failure_type
    )
    return SearchCompetitionAnalysisReport(
        case_count=len(rows),
        beam_case_count=len(beam_rows),
        multi_candidate_case_count=len(multi_candidate_rows),
        successful_case_count=len(successful_rows),
        top_rank_success_count=sum(1 for row in beam_rows if row.top_rank_success),
        score_inversion_count=sum(1 for row in beam_rows if row.score_inversion),
        average_failure_pressure=round(
            _average(row.failure_pressure for row in beam_rows),
            4,
        ),
        average_rule_diversity=round(
            _average(row.rule_diversity for row in beam_rows),
            4,
        ),
        average_failure_type_diversity=round(
            _average(row.failure_type_diversity for row in beam_rows),
            4,
        ),
        average_retention_bucket_diversity=round(
            _average(row.retention_bucket_diversity for row in beam_rows),
            4,
        ),
        multi_candidate_average_rule_diversity=round(
            _average(row.rule_diversity for row in multi_candidate_rows),
            4,
        ),
        multi_candidate_average_failure_type_diversity=round(
            _average(row.failure_type_diversity for row in multi_candidate_rows),
            4,
        ),
        multi_candidate_average_retention_bucket_diversity=round(
            _average(
                row.retention_bucket_diversity for row in multi_candidate_rows
            ),
            4,
        ),
        average_competing_failures_before_success=round(
            _average(
                row.competing_failures_before_success for row in successful_rows
            ),
            4,
        ),
        diversity_lift_case_count=sum(
            1 for row in beam_rows if row.max_diversity_lift > 0
        ),
        diversity_assisted_success_count=sum(
            1 for row in beam_rows if row.diversity_assisted_success
        ),
        average_diversity_lift=round(
            _average(row.max_diversity_lift for row in beam_rows),
            4,
        ),
        average_success_diversity_lift=round(
            _average(
                row.success_diversity_lift
                for row in successful_rows
                if row.success_diversity_lift > 0
            ),
            4,
        ),
        average_diversity_bonus=round(
            _average(row.average_diversity_bonus for row in beam_rows),
            4,
        ),
        average_success_diversity_bonus=round(
            _average(
                row.success_diversity_bonus
                for row in successful_rows
                if row.success_diversity_bonus > 0.0
            ),
            4,
        ),
        budget_sensitive_diversity_success_count=sum(
            1 for row in successful_rows if row.budget_sensitive_diversity_success
        ),
        average_success_budget_gap_before_rerank=round(
            _average(
                row.success_budget_gap_before_rerank
                for row in successful_rows
                if row.budget_sensitive_diversity_success
            ),
            4,
        ),
        average_success_budget_margin_after_rerank=round(
            _average(
                row.success_budget_margin_after_rerank
                for row in successful_rows
                if row.budget_sensitive_diversity_success
            ),
            4,
        ),
        max_diversity_lift=max(
            (row.max_diversity_lift for row in beam_rows),
            default=0,
        ),
        top_failure_type_counts=top_failure_type_counts,
        rows=rows,
    )


def search_competition_case_rows(report: Any) -> list[SearchCompetitionCaseRow]:
    rows: list[SearchCompetitionCaseRow] = []
    for case in _get_cases(report):
        beam_nodes = _get(case, "beam_search_results", [])
        if not isinstance(beam_nodes, list):
            beam_nodes = []
        rows.append(_case_row(case, beam_nodes))
    return rows


def search_competition_case_audit(case: Any) -> dict[str, Any]:
    beam_nodes = _get(case, "beam_search_results", [])
    if not isinstance(beam_nodes, list):
        beam_nodes = []
    return _case_row(case, beam_nodes).to_dict()


def _case_row(case: Any, beam_nodes: list[Any]) -> SearchCompetitionCaseRow:
    nodes = [node for node in beam_nodes if isinstance(node, dict)]
    evaluated_nodes = _evaluated_budget(case, nodes)
    success_flags = [bool(node.get("success", False)) for node in nodes]
    successful_nodes = sum(1 for value in success_flags if value)
    failed_nodes = max(0, evaluated_nodes - successful_nodes)
    first_success_rank = next(
        (index for index, success in enumerate(success_flags, start=1) if success),
        None,
    )
    top_node = nodes[0] if nodes else {}
    top_rank_success = bool(top_node.get("success", False)) if top_node else False
    top_rank_failure_type = (
        str(top_node.get("failure_type", ""))
        if top_node and not top_rank_success
        else ""
    )
    rule_diversity = len(
        {
            str(node.get("rule_id", ""))
            for node in nodes
            if str(node.get("rule_id", ""))
        }
    )
    failure_type_diversity = len(
        {
            str(node.get("failure_type", ""))
            for node in nodes
            if str(node.get("failure_type", "")) and not node.get("success", False)
        }
    )
    retention_bucket_diversity = len(
        {
            str(node.get("retention_bucket", ""))
            for node in nodes
            if str(node.get("retention_bucket", ""))
        }
    )
    competing_failures_before_success = (
        max(0, first_success_rank - 1) if first_success_rank is not None else 0
    )
    diversity_lifts = [_diversity_lift(node) for node in nodes]
    diversity_bonuses = [_diversity_bonus(node) for node in nodes]
    success_nodes = [
        (index, node)
        for index, node in enumerate(nodes, start=1)
        if bool(node.get("success", False))
    ]
    success_diversity_lift = max(
        (_diversity_lift(node) for _, node in success_nodes),
        default=0,
    )
    success_diversity_bonus = max(
        (_diversity_bonus(node) for _, node in success_nodes),
        default=0.0,
    )
    selected_success = _selected_success_candidate(success_nodes, evaluated_nodes)
    return SearchCompetitionCaseRow(
        case=str(_get(case, "case_name", _get(case, "name", ""))),
        evaluated_nodes=evaluated_nodes,
        failed_nodes=failed_nodes,
        successful_nodes=successful_nodes,
        first_success_rank=first_success_rank,
        top_rank_success=top_rank_success,
        top_rank_failure_type=top_rank_failure_type,
        score_inversion=bool(first_success_rank is not None and first_success_rank > 1),
        rule_diversity=rule_diversity,
        failure_type_diversity=failure_type_diversity,
        retention_bucket_diversity=retention_bucket_diversity,
        failure_pressure=_ratio(failed_nodes, evaluated_nodes),
        competing_failures_before_success=competing_failures_before_success,
        max_diversity_lift=max(diversity_lifts, default=0),
        success_diversity_lift=success_diversity_lift,
        average_diversity_bonus=round(_average(diversity_bonuses), 4),
        success_diversity_bonus=round(success_diversity_bonus, 4),
        diversity_assisted_success=bool(success_diversity_lift > 0),
        success_base_rank=selected_success["base_rank"],
        success_diversity_rank=selected_success["diversity_rank"],
        success_actual_rank=selected_success["actual_rank"],
        success_budget_gap_before_rerank=selected_success[
            "budget_gap_before_rerank"
        ],
        success_budget_margin_after_rerank=selected_success[
            "budget_margin_after_rerank"
        ],
        budget_sensitive_diversity_success=selected_success["budget_sensitive"],
        counterfactual_condition=selected_success["counterfactual_condition"],
    )


def _get_cases(report: Any) -> list[Any]:
    cases = _get(report, "cases", [])
    return cases if isinstance(cases, list) else []


def _get(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _evaluated_budget(case: Any, nodes: list[dict[str, Any]]) -> int:
    search = _get(case, "search_analysis", {})
    if not isinstance(search, dict):
        search = {}
    budget = _int_value(search.get("evaluated_nodes", len(nodes)))
    return budget if budget > 0 else len(nodes)


def _selected_success_candidate(
    success_nodes: list[tuple[int, dict[str, Any]]],
    evaluated_nodes: int,
) -> dict[str, Any]:
    candidates = []
    for actual_rank, node in success_nodes:
        base_rank = _diversity_base_rank(node)
        diversity_rank = _diversity_rank(node, actual_rank)
        effective_rank = diversity_rank if diversity_rank > 0 else actual_rank
        lift = max(0, base_rank - effective_rank) if base_rank else 0
        budget_gap = max(0, base_rank - evaluated_nodes) if base_rank else 0
        budget_margin = evaluated_nodes - effective_rank
        budget_sensitive = bool(
            base_rank > evaluated_nodes
            and effective_rank <= evaluated_nodes
        )
        candidates.append(
            {
                "actual_rank": actual_rank,
                "base_rank": base_rank,
                "diversity_rank": diversity_rank,
                "lift": lift,
                "bonus": _diversity_bonus(node),
                "budget_gap_before_rerank": budget_gap,
                "budget_margin_after_rerank": budget_margin,
                "budget_sensitive": budget_sensitive,
                "counterfactual_condition": _counterfactual_condition(
                    base_rank=base_rank,
                    effective_rank=effective_rank,
                    budget=evaluated_nodes,
                ),
            }
        )
    if not candidates:
        return {
            "actual_rank": None,
            "base_rank": 0,
            "diversity_rank": 0,
            "budget_gap_before_rerank": 0,
            "budget_margin_after_rerank": 0,
            "budget_sensitive": False,
            "counterfactual_condition": "no_success_candidate",
        }
    return max(
        candidates,
        key=lambda item: (
            item["budget_sensitive"],
            item["lift"],
            item["bonus"],
            -item["actual_rank"],
        ),
    )


def _counterfactual_condition(
    *,
    base_rank: int,
    effective_rank: int,
    budget: int,
) -> str:
    if base_rank > budget and effective_rank <= budget:
        return "base_rank_outside_budget_and_reranked_inside_budget"
    if base_rank > budget:
        return "base_rank_outside_budget_and_still_outside_budget"
    if base_rank > 0:
        return "base_rank_already_inside_budget"
    return "missing_base_rank"


def _average(values: Any) -> float:
    items = [float(value) for value in values]
    if not items:
        return 0.0
    return sum(items) / len(items)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _count_by(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _diversity_lift(node: dict[str, Any]) -> int:
    base_rank = _diversity_base_rank(node)
    rank = _diversity_rank(node)
    if base_rank <= 0 or rank <= 0:
        return 0
    return max(0, base_rank - rank)


def _diversity_base_rank(node: dict[str, Any]) -> int:
    diversity = _diversity(node)
    return _int_value(diversity.get("base_rank", node.get("base_rank", 0)))


def _diversity_rank(
    node: dict[str, Any],
    default: int = 0,
) -> int:
    diversity = _diversity(node)
    return _int_value(
        diversity.get(
            "rank",
            node.get("diversity_rank", node.get("rank", default)),
        )
    )


def _diversity_bonus(node: dict[str, Any]) -> float:
    diversity = _diversity(node)
    return _float_value(
        diversity.get(
            "bonus",
            node.get("diversity_bonus", 0.0),
        )
    )


def _diversity(node: dict[str, Any]) -> dict[str, Any]:
    diversity = node.get("search_diversity", {})
    return diversity if isinstance(diversity, dict) else {}


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
