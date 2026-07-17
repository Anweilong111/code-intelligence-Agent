from __future__ import annotations

import copy
import subprocess
from pathlib import Path

from code_intelligence_agent.evaluation.v4_real_bug_benchmark import (
    catalog_fingerprint,
    load_json_object,
)
from code_intelligence_agent.evaluation.v4_real_bug_reproduction import (
    adapt_v4_case_for_reproduction,
    build_reproduction_plan,
    reproduction_evidence_fingerprint,
    reproduction_plan_fingerprint,
    run_reproduction_case,
    validate_reproduction_profiles,
)


ROOT = Path(__file__).resolve().parents[1]
PROFILES_PATH = (
    ROOT / "datasets" / "v4_agent_effectiveness" / "reproduction_profiles.json"
)


def test_repository_reproduction_profiles_never_execute_setup_scripts():
    profiles = load_json_object(PROFILES_PATH)

    errors = validate_reproduction_profiles(profiles)

    assert errors == []
    assert profiles["setup_script_policy"] == "never_execute"
    assert all(
        profile["execute_benchmark_setup_script"] is False
        for profile in profiles["project_profiles"].values()
    )
    assert all(
        profile["dependency_install_requires_authorization"] is True
        for profile in profiles["project_profiles"].values()
    )


def test_plan_preserves_frozen_candidate_order_and_reports_ready_runtime(tmp_path):
    catalog = _catalog()
    selection = _selection_plan()
    profiles = _profiles()
    executable = tmp_path / "cpython-3.11.9" / "python.exe"
    executable.parent.mkdir()
    executable.write_text("fixture", encoding="utf-8")

    plan = build_reproduction_plan(
        catalog=catalog,
        selection_plan=selection,
        profiles=profiles,
        runtime_root=tmp_path,
        runtime_probe=lambda python, version, modules: {
            "status": "pass",
            "reason": "fixture",
            "python": str(python),
            "version": version,
            "missing_modules": modules,
        },
    )

    assert [item["case_id"] for item in plan["items"]] == ["bugsinpy-demo-1"]
    assert plan["summary"]["ready_count"] == 1
    assert plan["summary"]["blocked_count"] == 0
    assert plan["items"][0]["execution_contract"]["setup_script_executed"] is False
    assert plan["items"][0]["execution_contract"]["gold_patch_visible"] is False
    assert reproduction_plan_fingerprint(plan) == plan["plan_sha256"]


def test_plan_blocks_missing_exact_runtime_without_checkout(tmp_path):
    plan = build_reproduction_plan(
        catalog=_catalog(),
        selection_plan=_selection_plan(),
        profiles=_profiles(),
        runtime_root=tmp_path,
        runtime_probe=lambda *_: {"status": "pass"},
    )
    item = plan["items"][0]
    calls = 0

    def forbidden_checkout(**kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError(kwargs)

    evidence = run_reproduction_case(
        case=_catalog()["cases"][0],
        plan_item=item,
        project_profile=_profiles()["project_profiles"]["demo"],
        output_dir=tmp_path / "evidence",
        checkout=forbidden_checkout,
    )

    assert item["readiness"] == "blocked"
    assert "exact_runtime_executable_missing" in item["blockers"]
    assert evidence["status"] == "blocked"
    assert evidence["bug_targeted"]["status"] == "not_run"
    assert calls == 0
    assert reproduction_evidence_fingerprint(evidence) == evidence["evidence_sha256"]


def test_plan_blocks_platform_specific_regression_on_wrong_host(tmp_path):
    profiles = _profiles()
    profiles["project_profiles"]["demo"]["required_execution_platform"] = "linux"
    executable = tmp_path / "cpython-3.11.9" / "python.exe"
    executable.parent.mkdir()
    executable.write_text("fixture", encoding="utf-8")

    plan = build_reproduction_plan(
        catalog=_catalog(),
        selection_plan=_selection_plan(),
        profiles=profiles,
        runtime_root=tmp_path,
        runtime_probe=lambda *_: {"status": "pass", "reason": "fixture"},
        execution_platform="windows",
    )

    item = plan["items"][0]
    assert item["readiness"] == "blocked"
    assert item["blockers"] == [
        "execution_platform_mismatch:required_linux:observed_windows"
    ]
    assert item["execution_contract"]["required_execution_platform"] == "linux"
    assert item["execution_contract"]["observed_execution_platform"] == "windows"


def test_tox_node_is_rewritten_to_bounded_pytest_command():
    case = _catalog()["cases"][0]
    case["targeted_tests"][0][2] = "tox"
    profile = _profiles()["project_profiles"]["demo"]
    profile["command_module_rewrites"] = [
        {"from": "tox", "to": "pytest", "reason": "preserve one test node"}
    ]

    adapted, audit = adapt_v4_case_for_reproduction(
        case,
        project_profile=profile,
    )

    assert audit["status"] == "pass"
    assert adapted["targeted_test_commands"][0][2] == "pytest"
    assert audit["applied_command_rewrites"] == [
        {"from": "tox", "to": "pytest", "reason": "preserve one test node"}
    ]


def test_ready_case_executes_three_gates_and_writes_hashed_evidence(tmp_path):
    executable = tmp_path / "runtimes" / "cpython-3.11.9" / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.write_text("fixture", encoding="utf-8")
    plan = build_reproduction_plan(
        catalog=_catalog(),
        selection_plan=_selection_plan(),
        profiles=_profiles(),
        runtime_root=tmp_path / "runtimes",
        runtime_probe=lambda *_: {"status": "pass", "reason": "fixture"},
    )
    checkout_calls: list[dict] = []

    def fake_checkout(**kwargs):
        checkout_calls.append(kwargs)
        root = Path(kwargs["output_dir"]) / "repository_checkout"
        (root / "tests").mkdir(parents=True)
        (root / "tests" / "test_core.py").write_text(
            "def test_bug():\n    assert True\n",
            encoding="utf-8",
        )
        return {
            "status": "pass",
            "reason": "fixture",
            "checkout_path": str(root),
            "ref": kwargs["ref"],
            "checkout_method": "fixture",
        }

    executions = 0

    def fake_runner(command, **kwargs):
        nonlocal executions
        del kwargs
        if "-c" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="3.11.9\n",
                stderr="",
            )
        executions += 1
        returncode = 1 if executions == 1 else 0
        output = "1 failed" if returncode else "1 passed"
        return subprocess.CompletedProcess(
            command,
            returncode,
            stdout=output,
            stderr="",
        )

    output = tmp_path / "evidence"
    evidence = run_reproduction_case(
        case=_catalog()["cases"][0],
        plan_item=plan["items"][0],
        project_profile=_profiles()["project_profiles"]["demo"],
        output_dir=output,
        checkout=fake_checkout,
        runner=fake_runner,
    )

    assert evidence["status"] == "pass"
    assert evidence["acceptance"]["reproducible"] is True
    assert evidence["execution_contract"]["benchmark_setup_script_executed"] is False
    assert evidence["execution_contract"]["gold_patch_visible_to_execution"] is False
    assert evidence["execution_contract"]["model_calls"] == 0
    assert [call["ref"] for call in checkout_calls] == ["b" * 40, "f" * 40]
    assert (output / "v4_reproduction.json").is_file()
    assert (output / "v4_reproduction.md").is_file()
    assert reproduction_evidence_fingerprint(evidence) == evidence["evidence_sha256"]


def test_plan_fingerprint_ignores_generation_time(tmp_path):
    executable = tmp_path / "cpython-3.11.9" / "python.exe"
    executable.parent.mkdir()
    executable.write_text("fixture", encoding="utf-8")
    plan = build_reproduction_plan(
        catalog=_catalog(),
        selection_plan=_selection_plan(),
        profiles=_profiles(),
        runtime_root=tmp_path,
        runtime_probe=lambda *_: {"status": "pass"},
    )
    changed = copy.deepcopy(plan)
    changed["generated_at"] = "2099-01-01T00:00:00+00:00"

    assert reproduction_plan_fingerprint(plan) == reproduction_plan_fingerprint(changed)


def _catalog() -> dict:
    case = {
        "case_id": "bugsinpy-demo-1",
        "status": "candidate",
        "benchmark_split": "validation",
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
        "regression_tests": [
            ["{python}", "-m", "pytest", "-q", "tests"]
        ],
        "ground_truth": {
            "patch_sha256": "a" * 64,
            "source_files": ["demo/core.py"],
            "test_files": ["tests/test_core.py"],
            "functions": ["demo/core.py:normalize"],
            "visible_to_model": False,
            "source": "fixture",
        },
        "difficulty_categories": [],
        "difficulty_evidence": {},
        "difficulty_review_status": "pending_manual_review",
        "environment": {
            "python_version": "3.11.9",
            "declared_test_paths": ["tests/test_core.py"],
        },
        "reproduction": {
            "status": "pending",
            "bug_targeted": {"status": "pending"},
            "fix_targeted": {"status": "pending"},
            "fix_full_regression": {"status": "pending"},
            "acceptance": {"reproducible": False},
        },
        "provenance": {
            "benchmark": "BugsInPy",
            "benchmark_case": "demo:1",
        },
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
        "selection_plan": {},
        "cases": [case],
    }
    catalog["manifest_sha256"] = catalog_fingerprint(catalog)
    catalog["summary"] = {}
    return catalog


def _selection_plan() -> dict:
    return {
        "schema_version": "4.0",
        "plan_id": "fixture",
        "catalog_id": "fixture",
        "inventory_sha256": "fixture-inventory",
        "projects": [
            {
                "name": "demo",
                "owner_repo": "example/demo",
                "benchmark_split": "validation",
                "target_accepted_count": 1,
                "candidate_bug_ids": [1],
                "license": {
                    "spdx": "MIT",
                    "path": "LICENSE",
                    "url_template": "https://github.com/example/demo/blob/{bug_commit_sha}/LICENSE",
                    "verification": {
                        "status": "verified_at_representative_bug_commit",
                        "method": "github_rest_license_endpoint",
                        "representative_bug_id": 1,
                        "commit_sha": "b" * 40,
                        "evidence_url": "https://github.com/example/demo/blob/"
                        + "b" * 40
                        + "/LICENSE",
                    },
                },
                "regression_command": ["{python}", "-m", "pytest", "-q", "tests"],
            }
        ],
    }


def _profiles() -> dict:
    return {
        "schema_version": "4.0",
        "profile_id": "fixture",
        "setup_script_policy": "never_execute",
        "runtime_profiles": {
            "3.11.9": {"relative_executable": "cpython-3.11.9/python.exe"}
        },
        "project_profiles": {
            "demo": {
                "execute_benchmark_setup_script": False,
                "dependency_install_requires_authorization": True,
                "native_build_adapter_required": False,
                "required_runtime_modules": [],
                "pythonpath_entries": ["."],
                "command_module_rewrites": [],
                "preparation_files": [],
            }
        },
    }
