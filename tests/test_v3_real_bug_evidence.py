from __future__ import annotations

import json
from pathlib import Path

from code_intelligence_agent.evaluation.v3_real_bug_evidence import (
    aggregate_reproduction_evidence,
)


def test_evidence_aggregation_accepts_only_matching_three_gate_artifact(tmp_path):
    catalog = _catalog()
    artifact = _evidence(catalog["cases"][0])
    artifact_path = _write_evidence(tmp_path, artifact)

    accepted, audit = aggregate_reproduction_evidence(catalog, tmp_path)

    case = accepted["cases"][0]
    assert audit["status"] == "pass"
    assert audit["accepted_count"] == 1
    assert case["status"] == "accepted"
    assert case["reproduction"]["evidence_status"] == "validated"
    assert case["reproduction"]["bug_targeted"]["status"] == "fail"
    assert case["reproduction"]["fix_targeted"]["status"] == "pass"
    assert case["reproduction"]["fix_full_regression"]["test_count"] == 9
    assert case["reproduction"]["evidence_artifact"] == (
        "outputs_v3/reproduction/bugsinpy-demo-1/reproduction.json"
    )
    assert len(case["reproduction"]["evidence_sha256"]) == 64
    committed_payload = json.dumps(accepted)
    assert str(artifact_path.resolve()) not in committed_payload
    assert "stdout_preview" not in committed_payload
    assert "python_executable" not in committed_payload


def test_evidence_aggregation_rejects_commit_and_command_mismatch(tmp_path):
    catalog = _catalog()
    artifact = _evidence(catalog["cases"][0])
    artifact["preparation"]["fix_checkout"]["ref"] = "c" * 40
    artifact["fix_targeted"]["results"][0]["command_args"][-1] = "test_other"
    _write_evidence(tmp_path, artifact)

    result, audit = aggregate_reproduction_evidence(catalog, tmp_path)

    assert audit["status"] == "fail"
    assert result["cases"][0]["status"] == "candidate"
    errors = result["cases"][0]["reproduction"]["evidence_error_codes"]
    assert "fix_checkout_ref_mismatch" in errors
    assert "fix_targeted_command_args_mismatch" in errors


def test_evidence_aggregation_rejects_false_acceptance_gate(tmp_path):
    catalog = _catalog()
    artifact = _evidence(catalog["cases"][0])
    artifact["acceptance"]["fix_full_regression_passed"] = False
    _write_evidence(tmp_path, artifact)

    result, audit = aggregate_reproduction_evidence(catalog, tmp_path)

    assert audit["accepted_count"] == 0
    assert result["cases"][0]["status"] == "candidate"
    assert (
        "acceptance_gate_failed:fix_full_regression_passed"
        in audit["errors"][0]["errors"]
    )


def test_evidence_aggregation_preserves_explicit_rejection_without_artifact(tmp_path):
    catalog = _catalog()
    case = catalog["cases"][0]
    case["status"] = "rejected"
    case["rejection_reason"] = "full_regression_failed"
    case["rejection_evidence"] = {"summary": "Fixed revision regression failed."}

    result, audit = aggregate_reproduction_evidence(catalog, tmp_path)

    assert audit["status"] == "pass"
    assert audit["accepted_count"] == 0
    assert audit["rejected_count"] == 1
    assert result["cases"][0]["status"] == "rejected"


def _catalog() -> dict:
    return {
        "schema_version": "3.0",
        "catalog_id": "fixture",
        "cases": [
            {
                "case_id": "bugsinpy-demo-1",
                "status": "candidate",
                "repository": {"owner_repo": "example/demo"},
                "bug_commit_sha": "a" * 40,
                "fix_commit_sha": "b" * 40,
                "python_version": "3.11.9",
                "test_overlay_paths": ["tests/test_core.py"],
                "targeted_test_commands": [
                    [
                        "{python}",
                        "-m",
                        "pytest",
                        "-q",
                        "tests/test_core.py::test_bug",
                    ]
                ],
                "regression_command": ["{python}", "-m", "pytest", "-q", "tests"],
                "preparation_files": [],
                "reproduction": {},
            }
        ],
    }


def _evidence(case: dict) -> dict:
    targeted = case["targeted_test_commands"][0]
    regression = case["regression_command"]
    return {
        "schema_version": "3.0",
        "case_id": case["case_id"],
        "status": "pass",
        "reason": "real_bug_reproduced",
        "started_at": "2026-07-15T00:00:00+00:00",
        "completed_at": "2026-07-15T00:01:00+00:00",
        "runtime": {
            "status": "pass",
            "reason": "exact_python_version",
            "python_executable": r"C:\runtime\python.exe",
            "expected_version": case["python_version"],
            "observed_version": case["python_version"],
            "exact_match": True,
            "returncode": 0,
        },
        "preparation": {
            "status": "pass",
            "reason": "case_prepared",
            "bug_checkout": {
                "status": "pass",
                "ref": case["bug_commit_sha"],
                "checkout_method": "archive",
            },
            "fix_checkout": {
                "status": "pass",
                "ref": case["fix_commit_sha"],
                "checkout_method": "archive",
            },
            "test_overlay": {
                "status": "pass",
                "copied_count": 1,
                "files": [
                    {
                        "path": "tests/test_core.py",
                        "sha256": "d" * 64,
                        "size_bytes": 100,
                    }
                ],
            },
        },
        "bug_targeted": _group(targeted, status="fail", test_count=1),
        "fix_targeted": _group(targeted, status="pass", test_count=1),
        "fix_full_regression": _group(regression, status="pass", test_count=9),
        "acceptance": {
            "bug_targeted_failed": True,
            "fix_targeted_passed": True,
            "fix_full_regression_passed": True,
            "reproducible": True,
        },
        "blocker": {},
        "gold_patch_visible_to_execution": False,
    }


def _group(command: list[str], *, status: str, test_count: int) -> dict:
    failed = status == "fail"
    return {
        "status": status,
        "environment_blocker": False,
        "results": [
            {
                "status": status,
                "executed": True,
                "command_args": [r"C:\runtime\python.exe", *command[1:]],
                "returncode": 1 if failed else 0,
                "test_count": test_count,
                "passed": 0 if failed else test_count,
                "failed": 1 if failed else 0,
                "errors": 0,
                "skipped": 0,
                "failure_category": (
                    "test_assertion_failure" if failed else "none"
                ),
                "stdout_preview": "raw output that must not be committed",
            }
        ],
    }


def _write_evidence(root: Path, evidence: dict) -> Path:
    case_root = root / evidence["case_id"]
    case_root.mkdir(parents=True)
    path = case_root / "reproduction.json"
    path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return path
