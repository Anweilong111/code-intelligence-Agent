from __future__ import annotations

import copy
import hashlib
import subprocess
from pathlib import Path

import pytest

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
CATALOG_PATH = (
    ROOT / "datasets" / "v4_agent_effectiveness" / "real_bug_seed_catalog.json"
)
SELECTION_PATH = (
    ROOT / "datasets" / "v4_agent_effectiveness" / "selection_plan.json"
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


def test_thefuck_profile_binds_hashed_legacy_adapter_to_repository_paths():
    profiles = load_json_object(PROFILES_PATH)
    profile = profiles["project_profiles"]["thefuck"]

    assert validate_reproduction_profiles(profiles) == []
    assert profile["pythonpath_entries"][:2] == [
        ".cia-runtime-support",
        ".",
    ]
    assert profile["repository_pytest_plugins"] == ["cia_legacy_pytest"]
    assert all(len(item["sha256"]) == 64 for item in profile["preparation_files"])
    assert all(item["source_path"] for item in profile["preparation_files"])

    adapted, audit = adapt_v4_case_for_reproduction(
        _catalog()["cases"][0],
        project_profile=profile,
    )
    assert audit["status"] == "pass"
    assert audit["preparation_file_count"] == 3
    assert audit["repository_pytest_plugins"] == ["cia_legacy_pytest"]
    assert adapted["test_environment"]["repository_pytest_plugins"] == [
        "cia_legacy_pytest"
    ]


def test_thefuck_case_variants_bind_exact_historical_metadata():
    profiles = load_json_object(PROFILES_PATH)
    profile = profiles["project_profiles"]["thefuck"]
    catalog = load_json_object(CATALOG_PATH)
    cases = {item["case_id"]: item for item in catalog["cases"]}
    expected = {
        "bugsinpy-thefuck-17": (
            "3.3",
            "1470a42043a04e7769503350a5d5327c1fcc6b716a24d9bf5077f0fc355077c4",
            "d66aba432f3cafcabd3efd9a1928982d9d7c11677ef32899de249c680f254073",
        ),
        "bugsinpy-thefuck-8": (
            "3.23",
            "0ae67a7136f73c31c88436db464540bb60c525947b3263eaa3ad8194d3ebe5a2",
            "658fc95ab4bc7d6cfbaf0c3dda60dcbf3cd9330d0e606575f76f47613d1a76ce",
        ),
        "bugsinpy-thefuck-20": (
            "3.1",
            "671621c33969ddd3fb857507bae8028eae39b70d1aca8c9cb6a201821f06774c",
            "c84cd30a4151123531da5e814a3c46d75d49ec9fbba2421373737715f5d9a749",
        ),
        "bugsinpy-thefuck-1": (
            "3.29",
            "0ae67a7136f73c31c88436db464540bb60c525947b3263eaa3ad8194d3ebe5a2",
            "c4c857c806fa402a0fb593790ab4da9d524090c3e90e8eef1612d126c40f7e09",
        ),
    }

    for case_id, (version, test_hash, setup_hash) in expected.items():
        adapted, audit = adapt_v4_case_for_reproduction(
            cases[case_id],
            project_profile=profile,
        )
        files = adapted["preparation_files"]
        assert audit["status"] == "pass"
        assert audit["preparation_profile_scope"] == f"case:{case_id}"
        assert files[0]["source_text_sha256"] == test_hash
        assert files[1]["source_text_sha256"] == setup_hash
        assert files[2]["source_text_sha256"] == setup_hash
        assert f"thefuck-{version}.dist-info" in files[1]["path"]
        assert f"Version: {version}\n" in files[1]["content"]


def test_case_preparation_variant_rejects_tampering_and_missing_plugin():
    profiles = load_json_object(PROFILES_PATH)
    variants = profiles["project_profiles"]["thefuck"]["case_preparation_files"]
    variants["bugsinpy-thefuck-17"][1]["content"] += "changed\n"

    assert (
        "profiles.preparation_file_sha256_is_invalid:"
        "thefuck:bugsinpy-thefuck-17:1"
        in validate_reproduction_profiles(profiles)
    )

    profiles = load_json_object(PROFILES_PATH)
    variants = profiles["project_profiles"]["thefuck"]["case_preparation_files"]
    variants["bugsinpy-thefuck-17"].pop(0)
    assert (
        "profiles.repository_pytest_plugin_is_not_materialized:thefuck:0"
        in validate_reproduction_profiles(profiles)
    )


def test_runtime_variant_validation_rejects_hash_drift_and_duplicate_case():
    profiles = _profiles()
    profiles["project_profiles"]["demo"]["runtime_variants"] = {
        "legacy-a": {
            "case_ids": ["bugsinpy-demo-1"],
            "isolated_environment_template": "demo-a-py{version}",
            "bootstrap_requirements": ["pytest==7.4.4"],
            "requirements_line_ending": "lf",
            "requirements_sha256": "f" * 64,
        },
        "legacy-b": {
            "case_ids": ["bugsinpy-demo-1"],
            "isolated_environment_template": "demo-b-py{version}",
            "bootstrap_requirements": ["pytest==7.4.4"],
            "requirements_line_ending": "lf",
            "requirements_sha256": hashlib.sha256(
                b"pytest==7.4.4\n"
            ).hexdigest(),
        },
    }

    errors = validate_reproduction_profiles(profiles)

    assert (
        "profiles.runtime_variant_requirements_sha256_is_invalid:demo:legacy-a"
        in errors
    )
    assert (
        "profiles.runtime_variant_case_id_is_duplicate:demo:bugsinpy-demo-1"
        in errors
    )


def test_profile_validation_rejects_unmaterialized_plugin_and_tampered_file():
    profiles = _profiles()
    profiles["project_profiles"]["demo"]["repository_pytest_plugins"] = [
        "missing_plugin"
    ]

    errors = validate_reproduction_profiles(profiles)

    assert errors == [
        "profiles.repository_pytest_plugin_is_not_materialized:demo:0"
    ]

    repository_profiles = load_json_object(PROFILES_PATH)
    repository_profiles["project_profiles"]["thefuck"]["preparation_files"][0][
        "content"
    ] += "# changed\n"
    assert (
        "profiles.preparation_file_sha256_is_invalid:thefuck:0"
        in validate_reproduction_profiles(repository_profiles)
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
    assert plan["items"][0]["catalog_status"] == "candidate"
    assert plan["summary"]["ready_count"] == 1
    assert plan["summary"]["blocked_count"] == 0
    assert plan["summary"]["catalog_status_counts"] == {
        "candidate": 1,
        "accepted": 0,
    }
    assert plan["items"][0]["execution_contract"]["setup_script_executed"] is False
    assert plan["items"][0]["execution_contract"]["gold_patch_visible"] is False
    assert reproduction_plan_fingerprint(plan) == plan["plan_sha256"]


def test_plan_keeps_accepted_case_replayable_after_catalog_transition(tmp_path):
    plan = build_reproduction_plan(
        catalog=load_json_object(CATALOG_PATH),
        selection_plan=load_json_object(SELECTION_PATH),
        profiles=load_json_object(PROFILES_PATH),
        runtime_root=tmp_path,
        case_ids={"bugsinpy-thefuck-16"},
        execution_platform="linux",
    )

    assert len(plan["items"]) == 1
    assert plan["items"][0]["case_id"] == "bugsinpy-thefuck-16"
    assert plan["items"][0]["catalog_status"] == "accepted"
    assert plan["items"][0]["runtime_variant"]["variant_id"] == "project_default"
    assert "runtime_variant_source_requirements_sha256_mismatch" not in (
        plan["items"][0]["adaptation"]["errors"]
    )
    assert plan["summary"]["catalog_status_counts"] == {
        "candidate": 0,
        "accepted": 1,
    }


def test_httpie_selection_maps_five_cases_to_three_hashed_runtime_variants(
    tmp_path,
):
    variants = {
        "pytest32-requests200": ["bugsinpy-httpie-1"],
        "pytest54-requests223": [
            "bugsinpy-httpie-2",
            "bugsinpy-httpie-3",
        ],
        "pytest54-requests200": [
            "bugsinpy-httpie-4",
            "bugsinpy-httpie-5",
        ],
    }
    for variant_id in variants:
        python = tmp_path / f"httpie-{variant_id}-py3.7.3" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("fixture", encoding="utf-8")

    plan = build_reproduction_plan(
        catalog=load_json_object(CATALOG_PATH),
        selection_plan=load_json_object(SELECTION_PATH),
        profiles=load_json_object(PROFILES_PATH),
        runtime_root=tmp_path,
        projects={"httpie"},
        execution_platform="linux",
        runtime_probe=lambda python, version, modules: {
            "status": "pass",
            "reason": "fixture",
            "python": str(python),
            "version": version,
            "available_modules": modules,
            "missing_modules": [],
        },
    )

    assert plan["summary"]["ready_count"] == 5
    assert plan["summary"]["blocked_count"] == 0
    by_variant = {}
    for item in plan["items"]:
        by_variant.setdefault(item["runtime_variant"]["variant_id"], []).append(
            item["case_id"]
        )
        assert item["runtime_variant"]["requirements_sha256"] == (
            next(
                case["environment"]["requirements"]["sha256"]
                for case in load_json_object(CATALOG_PATH)["cases"]
                if case["case_id"] == item["case_id"]
            )
        )
    assert by_variant == variants


def test_plan_does_not_replay_rejected_case(tmp_path):
    catalog = _catalog()
    case = catalog["cases"][0]
    case["status"] = "rejected"
    case["rejection_reason"] = "fixture_rejection"
    case["rejection_evidence"] = {"summary": "Rejected by fixture policy."}
    catalog["manifest_sha256"] = catalog_fingerprint(catalog)

    with pytest.raises(ValueError, match="selection_plan.case_not_in_inventory"):
        build_reproduction_plan(
            catalog=catalog,
            selection_plan=_selection_plan(),
            profiles=_profiles(),
            runtime_root=tmp_path,
        )


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


def test_plan_uses_platform_specific_base_runtime_mapping(tmp_path):
    profiles = _profiles()
    profiles["project_profiles"]["demo"].pop(
        "isolated_environment_template",
        None,
    )
    profiles["runtime_profiles"]["3.11.9"]["relative_executables"] = {
        "windows": "cpython-3.11.9/python.exe",
        "linux": "cpython-3.11.9/bin/python",
    }
    executable = tmp_path / "cpython-3.11.9" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.write_text("fixture", encoding="utf-8")

    plan = build_reproduction_plan(
        catalog=_catalog(),
        selection_plan=_selection_plan(),
        profiles=profiles,
        runtime_root=tmp_path,
        runtime_probe=lambda *_: {"status": "pass", "reason": "fixture"},
        execution_platform="linux",
    )

    assert plan["items"][0]["readiness"] == "ready"
    assert plan["items"][0]["runtime"]["python_executable"] == str(
        executable.resolve()
    )


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
