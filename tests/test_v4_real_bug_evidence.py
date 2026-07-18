from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from code_intelligence_agent.evaluation.v4_real_bug_benchmark import (
    catalog_fingerprint,
    summarize_catalog,
    validate_v4_catalog,
)
from code_intelligence_agent.evaluation.v4_real_bug_evidence import (
    accept_v4_reproduction_artifact,
    accept_v4_reproduction_artifact_batch,
)
from code_intelligence_agent.evaluation.v4_real_bug_reproduction import (
    reproduction_evidence_fingerprint,
    reproduction_plan_fingerprint,
)


ROOT = Path(__file__).resolve().parents[1]


def test_acceptance_ingests_only_hash_bound_three_gate_artifact(tmp_path):
    catalog = _catalog()
    archive, attestation = _artifact(tmp_path, catalog)

    accepted, audit = accept_v4_reproduction_artifact(
        catalog,
        archive,
        attestation,
    )

    assert audit["status"] == "pass", audit["errors"]
    assert audit["before_summary"]["accepted_case_count"] == 0
    assert audit["after_summary"]["accepted_case_count"] == 1
    case = accepted["cases"][0]
    assert case["status"] == "accepted"
    assert case["difficulty_review_status"] == "verified"
    assert case["reproduction"]["evidence_status"] == "validated"
    assert case["reproduction"]["bug_targeted"]["status"] == "fail"
    assert case["reproduction"]["fix_targeted"]["status"] == "pass"
    assert case["reproduction"]["fix_full_regression"]["test_count"] == 9
    assert case["reproduction"]["artifact"]["sha256"] == attestation[
        "artifact"
    ]["sha256"]
    assert case["reproduction"]["raw_artifact_committed"] is False
    assert validate_v4_catalog(accepted)["status"] == "pass"
    assert accepted["manifest_sha256"] == catalog_fingerprint(accepted)


def test_acceptance_rejects_archive_digest_mismatch_without_mutating_catalog(tmp_path):
    catalog = _catalog()
    archive, attestation = _artifact(tmp_path, catalog)
    with archive.open("ab") as stream:
        stream.write(b"tampered")

    result, audit = accept_v4_reproduction_artifact(catalog, archive, attestation)

    assert audit["status"] == "fail"
    assert "artifact_archive_sha256_mismatch" in audit["errors"]
    assert result == catalog
    assert result["cases"][0]["status"] == "candidate"


def test_acceptance_rejects_unsafe_zip_member_even_with_matching_digest(tmp_path):
    catalog = _catalog()
    archive, attestation = _artifact(tmp_path, catalog, unsafe_member="../escape")

    result, audit = accept_v4_reproduction_artifact(catalog, archive, attestation)

    assert audit["status"] == "fail"
    assert "artifact_member_is_unsafe:../escape" in audit["errors"]
    assert result == catalog
    assert (tmp_path / "escape").exists() is False


def test_acceptance_rejects_command_mismatch_after_all_hashes_are_recomputed(tmp_path):
    catalog = _catalog()

    def mutate(evidence):
        evidence["fix_targeted"]["results"][0]["command_args"][-1] = (
            "tests/test_core.py::test_other"
        )

    archive, attestation = _artifact(tmp_path, catalog, mutate_evidence=mutate)

    result, audit = accept_v4_reproduction_artifact(catalog, archive, attestation)

    assert audit["status"] == "fail"
    assert "fix_targeted_command_args_mismatch" in audit["errors"]
    assert result["cases"][0]["status"] == "candidate"


def test_acceptance_rejects_plan_that_substitutes_catalog_target(tmp_path):
    catalog = _catalog()

    def mutate(plan):
        plan["items"][0]["execution_contract"]["targeted_test_commands"][0][
            -1
        ] = "tests/test_core.py::test_other"

    archive, attestation = _artifact(tmp_path, catalog, mutate_plan=mutate)

    result, audit = accept_v4_reproduction_artifact(catalog, archive, attestation)

    assert audit["status"] == "fail"
    assert "plan_targeted_commands_mismatch" in audit["errors"]
    assert result["cases"][0]["status"] == "candidate"


def test_acceptance_rejects_runtime_variant_not_bound_to_catalog_requirements(
    tmp_path,
):
    catalog = _catalog()

    def mutate(plan):
        plan["items"][0]["runtime_variant"] = {
            "status": "pass",
            "variant_id": "legacy-a",
            "case_id": "bugsinpy-demo-1",
            "requirements_sha256": "f" * 64,
            "requirements_line_ending": "lf",
            "errors": [],
        }

    archive, attestation = _artifact(tmp_path, catalog, mutate_plan=mutate)

    result, audit = accept_v4_reproduction_artifact(catalog, archive, attestation)

    assert audit["status"] == "fail"
    assert "plan_runtime_variant_requirements_sha256_mismatch" in audit["errors"]
    assert result == catalog


def test_accept_cli_writes_validated_catalog_and_audit(tmp_path):
    catalog = _catalog()
    archive, attestation = _artifact(tmp_path, catalog)
    catalog_input = tmp_path / "catalog.json"
    attestation_input = tmp_path / "attestation.json"
    catalog_output = tmp_path / "accepted_catalog.json"
    audit_output = tmp_path / "acceptance_audit.json"
    catalog_input.write_text(json.dumps(catalog), encoding="utf-8")
    attestation_input.write_text(json.dumps(attestation), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_intelligence_agent",
            "v4-reproduce",
            "accept",
            str(catalog_input),
            str(archive),
            str(attestation_input),
            str(catalog_output),
            str(audit_output),
            "--require-pass",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    accepted = json.loads(catalog_output.read_text(encoding="utf-8"))
    audit = json.loads(audit_output.read_text(encoding="utf-8"))
    assert accepted["cases"][0]["status"] == "accepted"
    assert audit["status"] == "pass"


def test_batch_acceptance_validates_original_manifest_then_updates_atomically(
    tmp_path,
):
    catalog = _batch_catalog()
    archive, attestations = _batch_artifact(tmp_path, catalog)

    accepted, audit = accept_v4_reproduction_artifact_batch(
        catalog,
        archive,
        attestations,
    )

    assert audit["status"] == "pass", audit["errors"]
    assert audit["atomic_all_or_nothing"] is True
    assert audit["accepted_case_count"] == 2
    assert audit["before_summary"]["accepted_case_count"] == 0
    assert audit["after_summary"]["accepted_case_count"] == 2
    assert all(item["status"] == "pass" for item in audit["individual_audits"])
    assert {item["status"] for item in accepted["cases"]} == {"accepted"}
    assert accepted["manifest_sha256"] == catalog_fingerprint(accepted)
    assert validate_v4_catalog(accepted)["status"] == "pass"


def test_batch_acceptance_rejects_all_cases_when_one_evidence_is_invalid(tmp_path):
    catalog = _batch_catalog()
    archive, attestations = _batch_artifact(
        tmp_path,
        catalog,
        invalid_case_id="bugsinpy-demo-2",
    )

    result, audit = accept_v4_reproduction_artifact_batch(
        catalog,
        archive,
        attestations,
    )

    assert audit["status"] == "fail"
    assert audit["accepted_case_count"] == 0
    assert any(
        value.startswith(
            "case:bugsinpy-demo-2:fix_targeted_command_args_mismatch"
        )
        for value in audit["errors"]
    )
    assert result == catalog
    assert {item["status"] for item in result["cases"]} == {"candidate"}


def test_batch_acceptance_rejects_mixed_artifact_identity_before_catalog_update(
    tmp_path,
):
    catalog = _batch_catalog()
    archive, attestations = _batch_artifact(tmp_path, catalog)
    attestations[1]["artifact"]["artifact_id"] = 99

    result, audit = accept_v4_reproduction_artifact_batch(
        catalog,
        archive,
        attestations,
    )

    assert audit["status"] == "fail"
    assert "batch_artifact_or_workflow_identity_mismatch" in audit["errors"]
    assert audit["individual_audits"] == []
    assert result == catalog


def test_batch_acceptance_rejects_unknown_manifest_schema(tmp_path):
    catalog = _batch_catalog()
    archive, attestations = _batch_artifact(tmp_path, catalog)

    result, audit = accept_v4_reproduction_artifact_batch(
        catalog,
        archive,
        attestations,
        manifest_schema_version="5.0",
    )

    assert audit["status"] == "fail"
    assert "batch_manifest_schema_version_mismatch" in audit["errors"]
    assert audit["individual_audits"] == []
    assert result == catalog


def test_accept_batch_cli_writes_one_atomic_catalog_and_audit(tmp_path):
    catalog = _batch_catalog()
    archive, attestations = _batch_artifact(tmp_path, catalog)
    catalog_input = tmp_path / "catalog.json"
    manifest_input = tmp_path / "batch_attestations.json"
    catalog_output = tmp_path / "accepted_catalog.json"
    audit_output = tmp_path / "acceptance_audit.json"
    catalog_input.write_text(json.dumps(catalog), encoding="utf-8")
    manifest_input.write_text(
        json.dumps({"schema_version": "4.0", "attestations": attestations}),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_intelligence_agent",
            "v4-reproduce",
            "accept-batch",
            str(catalog_input),
            str(archive),
            str(manifest_input),
            str(catalog_output),
            str(audit_output),
            "--require-pass",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    accepted = json.loads(catalog_output.read_text(encoding="utf-8"))
    audit = json.loads(audit_output.read_text(encoding="utf-8"))
    assert audit["status"] == "pass"
    assert audit["accepted_case_count"] == 2
    assert {item["status"] for item in accepted["cases"]} == {"accepted"}


def _catalog() -> dict:
    case = {
        "case_id": "bugsinpy-demo-1",
        "status": "candidate",
        "benchmark_split": "development",
        "repository": {
            "url": "https://github.com/example/demo",
            "owner_repo": "example/demo",
            "license_spdx": "MIT",
            "license_url": "https://github.com/example/demo/blob/" + "b" * 40 + "/LICENSE",
        },
        "source_url": "https://github.com/example/demo/commit/" + "f" * 40,
        "bug_commit_sha": "b" * 40,
        "fix_commit_sha": "f" * 40,
        "targeted_tests": [
            ["{python}", "-m", "pytest", "tests/test_core.py::test_bug"]
        ],
        "regression_tests": [["{python}", "-m", "pytest", "-q", "tests"]],
        "ground_truth": {
            "patch_sha256": "a" * 64,
            "source_files": ["demo/core.py"],
            "test_files": ["tests/test_core.py"],
            "functions": ["demo/core.py:normalize"],
            "visible_to_model": False,
            "source": "fixture",
        },
        "difficulty_categories": ["multi_file"],
        "difficulty_evidence": {"multi_file": "Pending reproduction review."},
        "difficulty_review_status": "pending_manual_review",
        "environment": {
            "python_version": "3.11.9",
            "declared_test_paths": ["tests/test_core.py"],
            "requirements": {"sha256": "7" * 64},
        },
        "reproduction": {
            "status": "pending",
            "bug_targeted": {"status": "pending"},
            "fix_targeted": {"status": "pending"},
            "fix_full_regression": {"status": "pending"},
            "acceptance": {"reproducible": False},
            "evidence_artifact": "",
        },
        "provenance": {"benchmark": "BugsInPy", "benchmark_case": "demo:1"},
        "model_context_audit": {
            "contains_gold_patch": False,
            "contains_fix_commit_content": False,
            "contains_hidden_test_answer": False,
        },
        "selection": {"inventory_eligibility": {"status": "eligible"}},
        "rejection_reason": "",
        "rejection_evidence": {},
    }
    catalog = {
        "schema_version": "4.0",
        "catalog_id": "fixture",
        "generated_at": "2026-07-18T00:00:00+00:00",
        "status": "seed_unlocked",
        "locked": False,
        "sources": [],
        "target": {},
        "selection_plan": {"plan_sha256": "3" * 64},
        "cases": [case],
        "summary": {},
        "manifest_sha256": "",
    }
    catalog["summary"] = summarize_catalog(catalog)
    catalog["manifest_sha256"] = catalog_fingerprint(catalog)
    assert validate_v4_catalog(catalog)["status"] == "pass"
    return catalog


def _batch_catalog() -> dict:
    catalog = _catalog()
    second = copy.deepcopy(catalog["cases"][0])
    second["case_id"] = "bugsinpy-demo-2"
    second["source_url"] = "https://github.com/example/demo/commit/" + "d" * 40
    second["bug_commit_sha"] = "c" * 40
    second["fix_commit_sha"] = "d" * 40
    second["repository"]["license_url"] = (
        "https://github.com/example/demo/blob/" + "c" * 40 + "/LICENSE"
    )
    second["ground_truth"]["patch_sha256"] = "e" * 64
    second["provenance"]["benchmark_case"] = "demo:2"
    catalog["cases"].append(second)
    catalog["summary"] = summarize_catalog(catalog)
    catalog["manifest_sha256"] = catalog_fingerprint(catalog)
    assert validate_v4_catalog(catalog)["status"] == "pass"
    return catalog


def _batch_artifact(
    tmp_path: Path,
    catalog: dict,
    *,
    invalid_case_id: str = "",
) -> tuple[Path, list[dict]]:
    preparation_file = {
        "path": ".cia-runtime-support/fixture.py",
        "sha256": "1" * 64,
        "reason": "Fixture adapter.",
        "source_path": "setup.py",
        "source_text_sha256": "2" * 64,
    }
    items = []
    for case in catalog["cases"]:
        items.append(
            {
                "case_id": case["case_id"],
                "project": "demo",
                "owner_repo": "example/demo",
                "benchmark_split": "development",
                "bug_commit_sha": case["bug_commit_sha"],
                "fix_commit_sha": case["fix_commit_sha"],
                "readiness": "ready",
                "blockers": [],
                "runtime": {
                    "status": "available",
                    "reason": "exact_runtime_executable_present",
                    "expected_version": "3.11.9",
                    "python_executable": "/runtime/python",
                    "relative_executable": "demo-py3.11.9/bin/python",
                    "probe": {
                        "status": "pass",
                        "reason": "runtime_ready",
                        "version": {
                            "status": "pass",
                            "reason": "exact_python_version",
                            "python_executable": "/runtime/python",
                            "expected_version": "3.11.9",
                            "observed_version": "3.11.9",
                            "exact_match": True,
                            "returncode": 0,
                        },
                        "available_modules": ["fixture"],
                        "missing_modules": [],
                    },
                },
                "runtime_variant": {
                    "status": "pass",
                    "variant_id": "project_default",
                    "case_id": case["case_id"],
                    "requirements_sha256": "",
                    "requirements_line_ending": "",
                },
                "execution_contract": {
                    "setup_script_executed": False,
                    "gold_patch_visible": False,
                    "required_execution_platform": "linux",
                    "observed_execution_platform": "linux",
                    "test_overlay_paths": ["tests/test_core.py"],
                    "targeted_test_commands": copy.deepcopy(
                        case["targeted_tests"]
                    ),
                    "regression_command": copy.deepcopy(
                        case["regression_tests"][0]
                    ),
                    "preparation_files": [copy.deepcopy(preparation_file)],
                    "test_environment": {
                        "pythonpath_entries": [".cia-runtime-support", "."],
                        "optional_pythonpath_entries": [],
                        "required_tools": [],
                        "repository_pytest_plugins": ["fixture"],
                    },
                },
            }
        )
    plan = {
        "schema_version": "4.0",
        "plan_id": "fixture-batch-plan",
        "generated_at": "2026-07-18T00:00:00+00:00",
        "catalog_manifest_sha256": catalog["manifest_sha256"],
        "selection_plan_sha256": "3" * 64,
        "profiles_sha256": "4" * 64,
        "runtime_root_committed": False,
        "repository_setup_scripts_executed": False,
        "execution_platform": "linux",
        "filters": {},
        "items": items,
        "summary": {
            "case_count": len(items),
            "ready_count": len(items),
            "blocked_count": 0,
        },
    }
    plan["plan_sha256"] = reproduction_plan_fingerprint(plan)
    evidence_by_case = {}
    for case in catalog["cases"]:
        evidence = _evidence(case, plan, preparation_file)
        if case["case_id"] == invalid_case_id:
            evidence["fix_targeted"]["results"][0]["command_args"][-1] = (
                "tests/test_core.py::test_other"
            )
            evidence["evidence_sha256"] = reproduction_evidence_fingerprint(
                evidence
            )
        evidence_by_case[case["case_id"]] = evidence

    plan_bytes = (json.dumps(plan, indent=2) + "\n").encode("utf-8")
    evidence_bytes = {
        case_id: (json.dumps(evidence, indent=2) + "\n").encode("utf-8")
        for case_id, evidence in evidence_by_case.items()
    }
    plan_member = "thefuck_reproduction_plan.json"
    archive = tmp_path / "batch_artifact.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as stream:
        _write_zip_member(stream, plan_member, plan_bytes)
        for case_id, content in evidence_bytes.items():
            _write_zip_member(
                stream,
                f"reproduction/{case_id}/v4_reproduction.json",
                content,
            )
    artifact_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    attestations = []
    for case in catalog["cases"]:
        case_id = case["case_id"]
        evidence = evidence_by_case[case_id]
        content = evidence_bytes[case_id]
        attestations.append(
            {
                "schema_version": "4.0",
                "attestation_reference": f"docs/v4/{case_id}.json",
                "workflow_run": {
                    "run_id": 1,
                    "job_id": 2,
                    "url": (
                        "https://github.com/Anweilong111/"
                        "code-intelligence-Agent/actions/runs/1"
                    ),
                    "head_sha": "5" * 40,
                    "conclusion": "success",
                },
                "artifact": {
                    "artifact_id": 3,
                    "name": "batch-fixture",
                    "size_bytes": archive.stat().st_size,
                    "sha256": artifact_sha256,
                    "plan_member": plan_member,
                    "plan_file_sha256": hashlib.sha256(plan_bytes).hexdigest(),
                    "evidence_member": (
                        f"reproduction/{case_id}/v4_reproduction.json"
                    ),
                },
                "reproduction": {
                    "case_id": case_id,
                    "plan_sha256": plan["plan_sha256"],
                    "profiles_sha256": plan["profiles_sha256"],
                    "evidence_sha256": evidence["evidence_sha256"],
                    "evidence_file_sha256": hashlib.sha256(content).hexdigest(),
                    "evidence_reference": (
                        f"outputs_v4/fixture/{case_id}/v4_reproduction.json"
                    ),
                    "reproducible": True,
                },
                "difficulty_review": {
                    "status": "verified",
                    "evidence": {
                        "multi_file": "The fixture patch spans multiple files."
                    },
                },
                "safety": {
                    "repository_setup_script_executed": False,
                    "repository_project_installed": False,
                    "source_build_executed": False,
                    "tests_modified_or_excluded": False,
                    "shared_base_runtime_mutated": False,
                    "raw_artifact_committed": False,
                    "model_calls": 0,
                },
            }
        )
    return archive, attestations


def _artifact(
    tmp_path: Path,
    catalog: dict,
    *,
    mutate_evidence=None,
    mutate_plan=None,
    unsafe_member: str = "",
) -> tuple[Path, dict]:
    case = catalog["cases"][0]
    preparation_file = {
        "path": ".cia-runtime-support/fixture.py",
        "sha256": "1" * 64,
        "reason": "Fixture adapter.",
        "source_path": "setup.py",
        "source_text_sha256": "2" * 64,
    }
    targeted = copy.deepcopy(case["targeted_tests"])
    regression = copy.deepcopy(case["regression_tests"][0])
    plan = {
        "schema_version": "4.0",
        "plan_id": "fixture-plan",
        "generated_at": "2026-07-18T00:00:00+00:00",
        "catalog_manifest_sha256": catalog["manifest_sha256"],
        "selection_plan_sha256": "3" * 64,
        "profiles_sha256": "4" * 64,
        "runtime_root_committed": False,
        "repository_setup_scripts_executed": False,
        "execution_platform": "linux",
        "filters": {},
        "items": [
            {
                "case_id": case["case_id"],
                "project": "demo",
                "owner_repo": "example/demo",
                "benchmark_split": "development",
                "bug_commit_sha": case["bug_commit_sha"],
                "fix_commit_sha": case["fix_commit_sha"],
                "readiness": "ready",
                "blockers": [],
                "runtime": {
                    "status": "available",
                    "reason": "exact_runtime_executable_present",
                    "expected_version": "3.11.9",
                    "python_executable": "/runtime/python",
                    "relative_executable": "demo-py3.11.9/bin/python",
                    "probe": {
                        "status": "pass",
                        "reason": "runtime_ready",
                        "version": {
                            "status": "pass",
                            "reason": "exact_python_version",
                            "python_executable": "/runtime/python",
                            "expected_version": "3.11.9",
                            "observed_version": "3.11.9",
                            "exact_match": True,
                            "returncode": 0,
                        },
                        "available_modules": ["fixture"],
                        "missing_modules": [],
                    },
                },
                "runtime_variant": {
                    "status": "pass",
                    "variant_id": "project_default",
                    "case_id": case["case_id"],
                    "requirements_sha256": "",
                    "requirements_line_ending": "",
                },
                "execution_contract": {
                    "setup_script_executed": False,
                    "gold_patch_visible": False,
                    "required_execution_platform": "linux",
                    "observed_execution_platform": "linux",
                    "test_overlay_paths": ["tests/test_core.py"],
                    "targeted_test_commands": targeted,
                    "regression_command": regression,
                    "preparation_files": [preparation_file],
                    "test_environment": {
                        "pythonpath_entries": [".cia-runtime-support", "."],
                        "optional_pythonpath_entries": [],
                        "required_tools": [],
                        "repository_pytest_plugins": ["fixture"],
                    },
                },
            }
        ],
        "summary": {"case_count": 1, "ready_count": 1, "blocked_count": 0},
    }
    if mutate_plan is not None:
        mutate_plan(plan)
    plan["plan_sha256"] = reproduction_plan_fingerprint(plan)
    evidence = _evidence(case, plan, preparation_file)
    if mutate_evidence is not None:
        mutate_evidence(evidence)
        evidence["evidence_sha256"] = reproduction_evidence_fingerprint(evidence)
    plan_bytes = (json.dumps(plan, indent=2) + "\n").encode("utf-8")
    evidence_bytes = (json.dumps(evidence, indent=2) + "\n").encode("utf-8")
    plan_member = "thefuck_reproduction_plan.json"
    evidence_member = "reproduction/bugsinpy-demo-1/v4_reproduction.json"
    archive = tmp_path / "artifact.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as stream:
        _write_zip_member(stream, plan_member, plan_bytes)
        _write_zip_member(stream, evidence_member, evidence_bytes)
        if unsafe_member:
            _write_zip_member(stream, unsafe_member, b"unsafe")
    artifact_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    attestation = {
        "schema_version": "4.0",
        "attestation_reference": "docs/v4/fixture.json",
        "workflow_run": {
            "run_id": 1,
            "job_id": 2,
            "url": "https://github.com/Anweilong111/code-intelligence-Agent/actions/runs/1",
            "head_sha": "5" * 40,
            "conclusion": "success",
        },
        "artifact": {
            "artifact_id": 3,
            "name": "fixture",
            "size_bytes": archive.stat().st_size,
            "sha256": artifact_sha256,
            "plan_member": plan_member,
            "plan_file_sha256": hashlib.sha256(plan_bytes).hexdigest(),
            "evidence_member": evidence_member,
        },
        "reproduction": {
            "case_id": case["case_id"],
            "plan_sha256": plan["plan_sha256"],
            "profiles_sha256": plan["profiles_sha256"],
            "evidence_sha256": evidence["evidence_sha256"],
            "evidence_file_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
            "evidence_reference": "outputs_v4/fixture/v4_reproduction.json",
            "reproducible": True,
        },
        "difficulty_review": {
            "status": "verified",
            "evidence": {"multi_file": "The fixture patch spans multiple files."},
        },
        "safety": {
            "repository_setup_script_executed": False,
            "repository_project_installed": False,
            "source_build_executed": False,
            "tests_modified_or_excluded": False,
            "shared_base_runtime_mutated": False,
            "raw_artifact_committed": False,
            "model_calls": 0,
        },
    }
    return archive, attestation


def _evidence(case: dict, plan: dict, preparation_file: dict) -> dict:
    targeted = case["targeted_tests"][0]
    regression = case["regression_tests"][0]
    prepared_file = {
        "path": preparation_file["path"],
        "sha256": preparation_file["sha256"],
        "size_bytes": 10,
        "reason": preparation_file["reason"],
        "source_assertion": {
            "path": preparation_file["source_path"],
            "text_sha256": preparation_file["source_text_sha256"],
            "status": "pass",
        },
    }
    evidence = {
        "schema_version": "4.0",
        "evidence_id": "fixture",
        "case_id": case["case_id"],
        "bug_commit_sha": case["bug_commit_sha"],
        "fix_commit_sha": case["fix_commit_sha"],
        "status": "pass",
        "reason": "real_bug_reproduced",
        "started_at": "2026-07-18T00:00:00+00:00",
        "completed_at": "2026-07-18T00:01:00+00:00",
        "runtime": {
            "status": "pass",
            "python_executable": "/runtime/python",
            "expected_version": "3.11.9",
            "observed_version": "3.11.9",
            "exact_match": True,
        },
        "preparation": {
            "status": "pass",
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
                "files": [
                    {
                        "path": "tests/test_core.py",
                        "sha256": "6" * 64,
                        "size_bytes": 100,
                    }
                ],
                "errors": [],
            },
            "bug_preparation_files": _prepared_group(prepared_file),
            "fix_preparation_files": _prepared_group(prepared_file),
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
        "execution_contract": {
            "benchmark_setup_script_executed": False,
            "gold_patch_visible_to_execution": False,
            "model_calls": 0,
            "adaptation": {
                "status": "pass",
                "errors": [],
                "preparation_file_count": 1,
                "repository_pytest_plugins": ["fixture"],
            },
        },
    }
    evidence["evidence_sha256"] = reproduction_evidence_fingerprint(evidence)
    assert evidence["case_id"] in {
        item["case_id"] for item in plan["items"]
    }
    return evidence


def _prepared_group(prepared_file: dict) -> dict:
    return {
        "status": "pass",
        "requested_count": 1,
        "written_count": 1,
        "files": [copy.deepcopy(prepared_file)],
        "errors": [],
        "repository_code_executed": False,
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
                "command_args": ["/runtime/python", *command[1:]],
                "returncode": 1 if failed else 0,
                "test_count": test_count,
                "passed": 0 if failed else test_count,
                "failed": 1 if failed else 0,
                "errors": 0,
                "skipped": 0,
                "failure_category": "test_assertion_failure" if failed else "none",
            }
        ],
    }


def _write_zip_member(stream: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name)
    info.external_attr = 0o100644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    stream.writestr(info, content)
