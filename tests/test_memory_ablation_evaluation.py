from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_intelligence_agent.evaluation.memory_ablation_evaluation import (
    evaluate_memory_ablation,
    main,
    render_memory_ablation_markdown,
    write_memory_ablation,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets" / "memory_evaluation" / "v2_memory_ablation_cases.json"


def test_memory_ablation_quantifies_retrieval_and_policy_value():
    payload = evaluate_memory_ablation(json.loads(DATASET.read_text(encoding="utf-8")))

    assert payload["status"] == "pass"
    assert payload["case_count"] == 8
    assert payload["run_count"] == 16
    without_memory = payload["metrics"]["without_memory"]
    with_memory = payload["metrics"]["with_memory"]
    assert with_memory["task_completion_rate"] == 1.0
    assert with_memory["task_completion_rate"] > without_memory["task_completion_rate"]
    assert with_memory["constraint_preservation_rate"] == 1.0
    assert with_memory["failed_patch_avoidance_rate"] == 1.0
    assert with_memory["repeated_failed_patch_rate"] == 0.0
    assert without_memory["repeated_failed_patch_rate"] == 1.0
    assert with_memory["stale_memory_reuse_rate"] == 0.0
    assert without_memory["average_retrieved_record_count"] == 0.0


def test_memory_ablation_writes_auditable_json_and_markdown(tmp_path):
    payload = evaluate_memory_ablation(json.loads(DATASET.read_text(encoding="utf-8")))

    paths = write_memory_ablation(payload, tmp_path)
    markdown = render_memory_ablation_markdown(payload)

    assert Path(paths["memory_ablation_json"]).exists()
    assert Path(paths["memory_ablation_markdown"]).exists()
    assert "With vs Without Memory" in markdown
    assert "Repeated Patch Rate" in markdown
    assert "with_memory" in markdown


def test_memory_ablation_cli_requires_pass(tmp_path, capsys):
    main(
        [
            str(DATASET),
            str(tmp_path),
            "--format",
            "json",
            "--require-pass",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "pass"


def test_memory_ablation_cli_rejects_failed_gate(tmp_path):
    broken = json.loads(DATASET.read_text(encoding="utf-8"))
    broken["cases"][0]["expected_memory_ids"] = ["missing-memory-id"]
    broken_path = tmp_path / "broken.json"
    broken_path.write_text(json.dumps(broken), encoding="utf-8")

    with pytest.raises(SystemExit) as raised:
        main([str(broken_path), str(tmp_path / "out"), "--require-pass"])

    assert raised.value.code == 1
