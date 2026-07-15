from __future__ import annotations

import hashlib
import json
from pathlib import Path

from code_intelligence_agent import main as cli_module
from code_intelligence_agent.evaluation.v3_memory_evaluation import (
    evaluate_v3_memory_generalization,
    write_v3_memory_artifacts,
)
from code_intelligence_agent.evaluation.v3_security_evaluation import (
    evaluate_v3_repository_security,
    write_v3_security_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]
MEMORY_DATASET = (
    ROOT
    / "datasets"
    / "memory_evaluation"
    / "v3_memory_generalization_cases.json"
)


def test_v3_memory_generalization_ablation_enforces_authority_and_scope():
    dataset = json.loads(MEMORY_DATASET.read_text(encoding="utf-8"))

    payload = evaluate_v3_memory_generalization(dataset)

    assert payload["status"] == "pass"
    assert payload["case_count"] == 7
    assert payload["metrics"]["without_memory"]["task_completion_rate"] < 1.0
    assert payload["metrics"]["structured_v2"]["task_completion_rate"] == 1.0
    assert payload["metrics"]["structured_v2"]["stale_reuse_count"] == 0
    assert (
        payload["metrics"]["structured_v2"][
            "conflict_execution_violation_count"
        ]
        == 0
    )
    assert (
        payload["metrics"]["structured_v2"][
            "advisory_execution_violation_count"
        ]
        == 0
    )
    assert payload["strategy_confidence"]["success_count"] == 3
    assert payload["strategy_confidence"]["failure_count"] == 2
    assert payload["strategy_confidence"]["decision_use"] == "advisory_only"
    assert payload["long_session_summary"]["status"] == "pass"
    assert payload["embedding_retrieval_decision"]["status"] == "not_retained"


def test_v3_hostile_repository_suite_controls_every_fixture():
    payload = evaluate_v3_repository_security()

    assert payload["status"] == "pass"
    assert payload["case_count"] == 8
    assert payload["passed_case_count"] == 8
    assert all(payload["acceptance_gates"].values())
    by_id = {item["case_id"]: item for item in payload["cases"]}
    assert by_id["legacy_setup_hook"]["evidence"]["process_start_count"] == 0
    assert by_id["sensitive_environment_read"]["evidence"]["canary_exposed"] is False
    assert by_id["python_network_exfiltration"]["evidence"][
        "policy_block_signal"
    ] is True
    assert by_id["resource_exhaustion_timeout"]["evidence"][
        "terminated_by_parent"
    ] is True


def test_v3_phase6_writers_and_top_level_cli(tmp_path, capsys):
    dataset = json.loads(MEMORY_DATASET.read_text(encoding="utf-8"))
    memory = evaluate_v3_memory_generalization(dataset)
    security = evaluate_v3_repository_security()

    memory_paths = write_v3_memory_artifacts(memory, tmp_path / "memory")
    security_paths = write_v3_security_artifacts(security, tmp_path / "security")
    cli_module.main(
        [
            "v3-memory-eval",
            str(MEMORY_DATASET),
            str(tmp_path / "cli-memory"),
            "--format",
            "json",
            "--require-pass",
        ]
    )
    printed = json.loads(capsys.readouterr().out)

    assert printed["status"] == "pass"
    assert all(Path(path).is_file() for path in memory_paths.values())
    assert all(Path(path).is_file() for path in security_paths.values())
    assert (tmp_path / "cli-memory" / "phase6_memory_evaluation.json").is_file()
    combined = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in [*memory_paths.values(), *security_paths.values()]
    )
    assert "phase6-canary-secret" not in combined


def test_committed_phase6_verification_hashes_current_artifacts():
    verification = json.loads(
        (ROOT / "docs" / "v3" / "phase6_verification.json").read_text(
            encoding="utf-8"
        )
    )

    assert verification["status"] == "pass"
    assert verification["memory_evaluation"]["status"] == "pass"
    assert verification["security_evaluation"]["status"] == "pass"
    for relative_path, expected_hash in verification["artifacts"].items():
        path = ROOT / relative_path
        assert path.is_file(), relative_path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash
    for relative_path in verification["artifact_audit"]["lf_normalized_files"]:
        assert b"\r\n" not in (ROOT / relative_path).read_bytes()
