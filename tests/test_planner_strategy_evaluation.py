import json
from pathlib import Path

from code_intelligence_agent.evaluation.planner_strategy_evaluation import (
    evaluate_planner_strategies,
    main,
    render_planner_strategy_evaluation_markdown,
    write_planner_strategy_evaluation,
)


DATASET_PATH = (
    Path(__file__).resolve().parents[1]
    / "datasets"
    / "planner_evaluation"
    / "v2_planner_controlled_cases.json"
)


def test_planner_strategy_evaluation_covers_rule_llm_and_hybrid_metrics():
    payload = evaluate_planner_strategies(DATASET_PATH)

    assert payload["status"] == "pass"
    assert payload["case_count"] == 14
    assert payload["run_count"] == 42
    assert set(payload["strategies"]) == {"rule", "llm", "hybrid"}

    rule = payload["strategies"]["rule"]
    llm = payload["strategies"]["llm"]
    hybrid = payload["strategies"]["hybrid"]
    assert rule["task_completion_rate"] == 1.0
    assert rule["invalid_action_count"] == 0
    assert rule["valid_action_rate"] == 1.0
    assert rule["repeated_action_rate"] == 0.0
    assert rule["llm_total_tokens"] == 0
    assert rule["llm_estimated_cost_usd"] == 0.0
    assert llm["task_completion_rate"] == 1.0
    assert llm["valid_action_rate"] == 1.0
    assert llm["invalid_action_count"] == 2
    assert llm["safety_gate_rejection_count"] == 8
    assert llm["fallback_count"] == 11
    assert hybrid["task_completion_rate"] == 1.0
    assert hybrid["valid_action_rate"] == 1.0
    assert hybrid["invalid_action_count"] == 2
    assert hybrid["safety_gate_rejection_count"] == 9
    assert hybrid["fallback_count"] == 12
    assert llm["llm_total_tokens"] == hybrid["llm_total_tokens"] == 1560
    assert llm["llm_estimated_cost_usd"] == 0.0156

    moderate = [
        row
        for row in payload["runs"]
        if row["case"] == "moderate_confidence_disagreement"
    ]
    assert {row["planner_mode"]: row["selected_action"] for row in moderate} == {
        "rule": "generate_llm_patch_candidates",
        "llm": "generate_hybrid_patch_candidates",
        "hybrid": "generate_llm_patch_candidates",
    }
    unsafe = [
        row
        for row in payload["runs"]
        if row["case"] == "unsafe_stage_transition"
        and row["planner_mode"] in {"llm", "hybrid"}
    ]
    assert all(
        row["planner_resolution"]["resolution_reason"]
        == "unsafe_action_transition"
        for row in unsafe
    )


def test_planner_strategy_evaluation_writes_auditable_artifacts(tmp_path):
    payload = evaluate_planner_strategies(DATASET_PATH)
    paths = write_planner_strategy_evaluation(payload, tmp_path)
    markdown = render_planner_strategy_evaluation_markdown(payload)

    assert Path(paths["json"]).exists()
    assert Path(paths["markdown"]).exists()
    assert json.loads(Path(paths["json"]).read_text(encoding="utf-8"))[
        "status"
    ] == "pass"
    assert "Planner Strategy Evaluation" in markdown
    assert "| hybrid |" in markdown
    assert "Safety Rejects" in markdown


def test_planner_strategy_evaluation_cli_requires_pass(tmp_path, capsys):
    output_dir = tmp_path / "planner-evaluation"

    main(
        [
            str(DATASET_PATH),
            str(output_dir),
            "--format",
            "markdown",
            "--require-pass",
        ]
    )
    stdout = capsys.readouterr().out

    assert "Status: `pass`" in stdout
    assert (output_dir / "planner_strategy_evaluation.json").exists()
    assert (output_dir / "planner_strategy_evaluation.md").exists()
