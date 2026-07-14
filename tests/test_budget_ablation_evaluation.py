from pathlib import Path

from code_intelligence_agent.evaluation.budget_ablation_evaluation import (
    evaluate_budget_ablations,
    render_budget_ablation_markdown,
    write_budget_ablation,
)


SYSTEM_DATASET = Path(
    "datasets/system_evaluation/v2_system_ablation_cases.json"
)
PATCH_DATASET = Path(
    "datasets/patch_evaluation/v2_patch_strategy_controlled_cases.json"
)


def test_budget_ablations_execute_real_patch_validation_and_controller_planning(
    tmp_path,
):
    payload = evaluate_budget_ablations(SYSTEM_DATASET, PATCH_DATASET)

    assert payload["status"] == "pass"
    dimensions = payload["dimensions"]
    reflection = dimensions["reflection"]["runs"]
    candidates = dimensions["candidate_budget"]["runs"]
    top_k = dimensions["top_k_context"]["runs"]
    actions = dimensions["action_budget"]["runs"]

    assert {row["value"]: row["verified_repair"] for row in reflection} == {
        0: False,
        1: True,
    }
    assert {row["value"]: row["verified_repair"] for row in candidates} == {
        1: False,
        2: False,
        3: True,
    }
    assert {row["value"]: row["verified_repair"] for row in top_k} == {
        1: False,
        3: True,
        5: True,
    }
    assert {row["action_budget"]: row["task_completed"] for row in actions} == {
        1: False,
        2: False,
        3: True,
    }
    assert all(row["all_actions_registered"] for row in actions)
    assert all(row["valid_action_rate"] == 1.0 for row in actions)
    assert all(row["repeated_action_rate"] == 0.0 for row in actions)
    assert actions[-1]["selected_actions"] == [
        "generate_llm_patch_candidates",
        "run_patch_reflection_loop",
        "run_search_and_ablation_evaluation",
    ]
    assert all(
        step["act"]["controlled_tool_outcome"] == "completed"
        for step in actions[-1]["trace"]
    )

    paths = write_budget_ablation(payload, tmp_path)
    assert Path(paths["json"]).is_file()
    assert Path(paths["markdown"]).is_file()
    markdown = render_budget_ablation_markdown(payload)
    assert "Action Budget" in markdown
    assert "production controller and Action Registry" in markdown
