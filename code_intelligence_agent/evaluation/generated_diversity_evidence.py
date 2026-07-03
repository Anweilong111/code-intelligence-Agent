from __future__ import annotations

from typing import Any


def generated_diversity_budget_evidence(
    generated_benchmark: dict[str, Any],
) -> dict[str, Any]:
    report = _dict(generated_benchmark.get("benchmark_report"))
    cases = _list(report.get("cases"))
    rows = []
    expected_cases = 0
    successful_expected_cases = 0

    for case in cases:
        if not isinstance(case, dict):
            continue
        metadata = _dict(case.get("metadata"))
        if not _expects_diversity_reranking(metadata):
            continue
        expected_cases += 1
        nodes = [
            node
            for node in _list(case.get("beam_search_results"))
            if isinstance(node, dict)
        ]
        budget = _evaluated_budget(case, nodes)
        success_nodes = [
            (index, node)
            for index, node in enumerate(nodes, start=1)
            if bool(node.get("success", False))
        ]
        if success_nodes:
            successful_expected_cases += 1
        best = _best_success_candidate(success_nodes, budget)
        if best:
            rows.append(_case_evidence_row(case, best))

    budget_sensitive_successes = sum(
        1 for row in rows if row["budget_sensitive_success"]
    )
    full_rate = _ratio(successful_expected_cases, expected_cases)
    projected_rate = _ratio(
        successful_expected_cases - budget_sensitive_successes,
        expected_cases,
    )
    budget_rows = [row for row in rows if row["budget_sensitive_success"]]
    return {
        "status": (
            "validated"
            if budget_sensitive_successes > 0
            else "partial"
            if expected_cases > 0
            else "missing"
        ),
        "variant": "without_diversity_reranking",
        "expected_cases": expected_cases,
        "successful_expected_cases": successful_expected_cases,
        "budget_sensitive_successes": budget_sensitive_successes,
        "projected_without_diversity_failures": budget_sensitive_successes,
        "full_patch_success_rate": full_rate,
        "projected_without_diversity_patch_success_rate": projected_rate,
        "projected_patch_success_delta": round(projected_rate - full_rate, 4),
        "projected_beam_success_delta": round(projected_rate - full_rate, 4),
        "average_success_base_rank": _avg_metric(budget_rows, "success_base_rank"),
        "average_success_diversity_rank": _avg_metric(
            budget_rows,
            "success_diversity_rank",
        ),
        "average_success_diversity_lift": _avg_metric(
            budget_rows,
            "success_diversity_lift",
        ),
        "average_success_diversity_bonus": _avg_metric(
            budget_rows,
            "success_diversity_bonus",
        ),
        "average_success_budget_gap_before_rerank": _avg_metric(
            budget_rows,
            "success_budget_gap_before_rerank",
        ),
        "average_success_budget_margin_after_rerank": _avg_metric(
            budget_rows,
            "success_budget_margin_after_rerank",
        ),
        "cases": rows,
    }


def _case_evidence_row(
    case: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    node = _dict(candidate.get("node"))
    return {
        "case": str(case.get("case_name", case.get("name", ""))),
        "evaluated_budget": candidate["evaluated_budget"],
        "success_base_rank": candidate["base_rank"],
        "success_diversity_rank": candidate["diversity_rank"],
        "success_actual_rank": candidate["actual_rank"],
        "success_diversity_lift": candidate["lift"],
        "success_diversity_bonus": candidate["bonus"],
        "success_budget_gap_before_rerank": candidate[
            "budget_gap_before_rerank"
        ],
        "success_budget_margin_after_rerank": candidate[
            "budget_margin_after_rerank"
        ],
        "success_rule": str(node.get("rule_id", "")),
        "success_variant": str(node.get("variant", "")),
        "budget_sensitive_success": candidate["budget_sensitive"],
        "projected_without_diversity_success": not candidate["budget_sensitive"],
        "counterfactual_condition": candidate["counterfactual_condition"],
    }


def _best_success_candidate(
    success_nodes: list[tuple[int, dict[str, Any]]],
    budget: int,
) -> dict[str, Any]:
    candidates = []
    for actual_rank, node in success_nodes:
        diversity = _dict(node.get("search_diversity"))
        base_rank = _int(diversity.get("base_rank", node.get("base_rank", 0)))
        diversity_rank = _int(
            diversity.get(
                "rank",
                node.get("diversity_rank", node.get("rank", actual_rank)),
            )
        )
        bonus = _float(diversity.get("bonus", node.get("diversity_bonus", 0.0)))
        effective_rank = diversity_rank if diversity_rank > 0 else actual_rank
        lift = max(0, base_rank - effective_rank) if base_rank else 0
        budget_gap = max(0, base_rank - budget) if base_rank else 0
        budget_margin = budget - effective_rank
        budget_sensitive = bool(
            base_rank > budget
            and effective_rank <= budget
        )
        candidates.append(
            {
                "node": node,
                "actual_rank": actual_rank,
                "evaluated_budget": budget,
                "base_rank": base_rank,
                "diversity_rank": diversity_rank,
                "lift": lift,
                "bonus": round(bonus, 4),
                "budget_gap_before_rerank": budget_gap,
                "budget_margin_after_rerank": budget_margin,
                "budget_sensitive": budget_sensitive,
                "counterfactual_condition": _counterfactual_condition(
                    base_rank=base_rank,
                    effective_rank=effective_rank,
                    budget=budget,
                ),
            }
        )
    if not candidates:
        return {}
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


def _evaluated_budget(case: dict[str, Any], nodes: list[dict[str, Any]]) -> int:
    search = _dict(case.get("search_analysis"))
    budget = _int(search.get("evaluated_nodes", len(nodes)))
    return budget if budget > 0 else len(nodes)


def _expects_diversity_reranking(metadata: dict[str, Any]) -> bool:
    if metadata.get("expected_diversity_reranking") is True:
        return True
    if metadata.get("expected_diversity_assisted_success") is True:
        return True
    if metadata.get("search_diversity_profile"):
        return True
    if metadata.get("hard_case_target_signal") == "search_diversity_reranking":
        return True
    for key in ("hard_case_target_signals", "target_benchmark_signals"):
        values = metadata.get(key, [])
        if isinstance(values, list) and "search_diversity_reranking" in {
            str(value) for value in values
        }:
            return True
    return False


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _avg_metric(rows: list[dict[str, Any]], key: str) -> float:
    values = [_float(row.get(key, 0.0)) for row in rows]
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)
