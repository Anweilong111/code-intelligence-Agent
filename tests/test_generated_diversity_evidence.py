from code_intelligence_agent.evaluation.generated_diversity_evidence import (
    generated_diversity_budget_evidence,
)


def test_generated_diversity_evidence_validates_budget_sensitive_success():
    evidence = generated_diversity_budget_evidence(
        {
            "benchmark_report": {
                "cases": [
                    _case(
                        "generated_diversity_probe",
                        metadata={"expected_diversity_reranking": True},
                        evaluated_nodes=4,
                        nodes=[
                            _node("same_rule_decoy", success=False, base_rank=1, rank=1),
                            _node(
                                "distinct_success",
                                success=True,
                                base_rank=5,
                                rank=2,
                                bonus=0.55,
                            ),
                            _node("same_rule_decoy", success=False, base_rank=2, rank=3),
                            _node("same_rule_decoy", success=False, base_rank=3, rank=4),
                        ],
                    )
                ]
            }
        }
    )

    assert evidence["status"] == "validated"
    assert evidence["variant"] == "without_diversity_reranking"
    assert evidence["expected_cases"] == 1
    assert evidence["successful_expected_cases"] == 1
    assert evidence["budget_sensitive_successes"] == 1
    assert evidence["projected_without_diversity_failures"] == 1
    assert evidence["full_patch_success_rate"] == 1.0
    assert evidence["projected_without_diversity_patch_success_rate"] == 0.0
    assert evidence["projected_patch_success_delta"] == -1.0
    assert evidence["projected_beam_success_delta"] == -1.0
    assert evidence["average_success_base_rank"] == 5.0
    assert evidence["average_success_diversity_rank"] == 2.0
    assert evidence["average_success_diversity_lift"] == 3.0
    assert evidence["average_success_diversity_bonus"] == 0.55
    assert evidence["average_success_budget_gap_before_rerank"] == 1.0
    assert evidence["average_success_budget_margin_after_rerank"] == 2.0

    [row] = evidence["cases"]
    assert row["case"] == "generated_diversity_probe"
    assert row["evaluated_budget"] == 4
    assert row["success_rule"] == "distinct_success"
    assert row["success_base_rank"] == 5
    assert row["success_diversity_rank"] == 2
    assert row["success_actual_rank"] == 2
    assert row["budget_sensitive_success"] is True
    assert row["projected_without_diversity_success"] is False
    assert row["success_budget_gap_before_rerank"] == 1
    assert row["success_budget_margin_after_rerank"] == 2
    assert (
        row["counterfactual_condition"]
        == "base_rank_outside_budget_and_reranked_inside_budget"
    )


def test_generated_diversity_evidence_reports_partial_without_budget_loss():
    evidence = generated_diversity_budget_evidence(
        {
            "benchmark_report": {
                "cases": [
                    _case(
                        "generated_diversity_already_in_budget",
                        metadata={
                            "hard_case_target_signals": [
                                "search_diversity_reranking",
                            ]
                        },
                        evaluated_nodes=4,
                        nodes=[
                            _node(
                                "distinct_success",
                                success=True,
                                base_rank=3,
                                rank=2,
                                bonus=0.2,
                            )
                        ],
                    )
                ]
            }
        }
    )

    assert evidence["status"] == "partial"
    assert evidence["expected_cases"] == 1
    assert evidence["successful_expected_cases"] == 1
    assert evidence["budget_sensitive_successes"] == 0
    assert evidence["full_patch_success_rate"] == 1.0
    assert evidence["projected_without_diversity_patch_success_rate"] == 1.0
    assert evidence["projected_patch_success_delta"] == 0.0
    assert evidence["average_success_diversity_lift"] == 0.0
    assert evidence["average_success_budget_gap_before_rerank"] == 0.0
    assert evidence["average_success_budget_margin_after_rerank"] == 0.0

    [row] = evidence["cases"]
    assert row["success_base_rank"] == 3
    assert row["success_diversity_rank"] == 2
    assert row["budget_sensitive_success"] is False
    assert row["projected_without_diversity_success"] is True
    assert row["success_budget_gap_before_rerank"] == 0
    assert row["success_budget_margin_after_rerank"] == 2
    assert row["counterfactual_condition"] == "base_rank_already_inside_budget"


def test_generated_diversity_evidence_ignores_non_diversity_cases():
    evidence = generated_diversity_budget_evidence(
        {
            "benchmark_report": {
                "cases": [
                    _case(
                        "ordinary_case",
                        metadata={"hard_case_target_signal": "search_score_inversion"},
                        evaluated_nodes=4,
                        nodes=[_node("ordinary_success", success=True)],
                    )
                ]
            }
        }
    )

    assert evidence["status"] == "missing"
    assert evidence["expected_cases"] == 0
    assert evidence["successful_expected_cases"] == 0
    assert evidence["budget_sensitive_successes"] == 0
    assert evidence["cases"] == []


def _case(
    name,
    *,
    metadata,
    evaluated_nodes,
    nodes,
):
    return {
        "case_name": name,
        "metadata": metadata,
        "search_analysis": {"evaluated_nodes": evaluated_nodes},
        "beam_search_results": nodes,
    }


def _node(
    rule_id,
    *,
    success,
    base_rank=0,
    rank=0,
    bonus=0.0,
):
    return {
        "rule_id": rule_id,
        "variant": f"{rule_id}_variant",
        "success": success,
        "search_diversity": {
            "base_rank": base_rank,
            "rank": rank,
            "bonus": bonus,
        },
    }
