from __future__ import annotations

from pathlib import Path

from code_intelligence_agent.evaluation.patch_strategy_evaluation import (
    evaluate_patch_strategies,
    render_patch_strategy_evaluation_markdown,
    write_patch_strategy_evaluation,
)


DATASET = Path("datasets/patch_evaluation/v2_patch_strategy_controlled_cases.json")


def test_patch_strategy_evaluation_separates_rule_llm_and_hybrid_metrics(tmp_path):
    payload = evaluate_patch_strategies(DATASET)

    assert payload["status"] == "pass"
    assert payload["case_count"] == 3
    assert payload["run_count"] == 9
    assert payload["success_authority"] == (
        "sandbox_targeted_and_full_regression_tests"
    )
    assert payload["strategies"]["rule"]["verified_repair_rate"] == 0.3333
    assert payload["strategies"]["llm"]["verified_repair_rate"] == 1.0
    assert payload["strategies"]["hybrid"]["verified_repair_rate"] == 1.0
    assert payload["strategies"]["llm"]["reflection_recovery_rate"] == 0.3333
    assert payload["strategies"]["hybrid"]["reflection_recovery_rate"] == 0.3333
    assert all(row["attribution_consistent"] for row in payload["runs"])
    assert all(row["expectation_matched"] for row in payload["runs"])
    assert all(
        not row["best_candidate_id"]
        or row["best_candidate_id"].startswith("sample.py::")
        for row in payload["runs"]
    )

    semantic_hybrid = next(
        row
        for row in payload["runs"]
        if row["case"] == "semantic_none_normalization"
        and row["patch_mode"] == "hybrid"
    )
    assert semantic_hybrid["generation_strategy"] == "adaptive_llm_first"
    assert semantic_hybrid["generation_order"] == ["llm", "rule"]
    assert semantic_hybrid["best_generator"] == "llm"

    paths = write_patch_strategy_evaluation(payload, tmp_path)
    assert Path(paths["json"]).exists()
    assert Path(paths["markdown"]).exists()
    markdown = render_patch_strategy_evaluation_markdown(payload)
    assert "Success Authority" in markdown
    assert "deterministic offline fixtures" in markdown
