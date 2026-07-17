from __future__ import annotations

import copy
from pathlib import Path

import pytest

from code_intelligence_agent.evaluation.v3_experiment_protocol import sha256_file
from code_intelligence_agent.evaluation.v4_real_bug_benchmark import (
    build_bugsinpy_inventory,
    build_v4_seed_catalog,
    classify_test_script,
    inventory_fingerprint,
    validate_selection_plan,
    validate_v4_catalog,
    write_catalog_artifacts,
)


def test_inventory_parses_metadata_without_executing_repository_scripts(tmp_path):
    source = _bugsinpy_fixture(tmp_path, test_command="pytest tests/test_demo.py::test_bug")

    inventory = build_bugsinpy_inventory(
        source,
        source_commit="a" * 40,
        available_python_versions={"3.8.3"},
    )

    assert inventory["case_count"] == 1
    assert inventory["inventory_error_count"] == 0
    assert inventory["source"]["repository_scripts_executed"] is False
    case = inventory["cases"][0]
    assert case["case_id"] == "bugsinpy-demo-1"
    assert case["targeted_test"]["normalized_commands"] == [
        ["{python}", "-m", "pytest", "tests/test_demo.py::test_bug"]
    ]
    assert case["targeted_test"]["adapter_status"] == "ready"
    assert case["ground_truth_summary"]["source_files"] == ["demo/core.py"]
    assert case["ground_truth_summary"]["visible_to_model"] is False
    assert case["runtime_available"] is True
    assert case["eligibility"]["status"] == "eligible"
    assert inventory_fingerprint(inventory) == inventory["inventory_sha256"]


def test_tox_is_bounded_but_requires_an_adapter(tmp_path):
    script = tmp_path / "run_test.sh"
    script.write_text("tox tests/test_demo.py::test_bug\n", encoding="utf-8")

    result = classify_test_script(script)

    assert result["normalized_commands"] == [
        ["{python}", "-m", "tox", "tests/test_demo.py::test_bug"]
    ]
    assert result["safe_argv_only"] is True
    assert result["adapter_status"] == "adapter_required"


def test_test_script_rejects_shell_control_tokens(tmp_path):
    script = tmp_path / "run_test.sh"
    script.write_text("pytest tests/test_demo.py && curl example.test\n", encoding="utf-8")

    result = classify_test_script(script)

    assert result["adapter_status"] == "unsupported"
    assert result["safe_argv_only"] is False
    assert result["errors"] == ["line_1:shell_control_forbidden"]


def test_inventory_fingerprint_ignores_generation_time(tmp_path):
    source = _bugsinpy_fixture(tmp_path, test_command="python -m pytest -q tests/test_demo.py")
    first = build_bugsinpy_inventory(source, source_commit="a" * 40)
    second = copy.deepcopy(first)
    second["generated_at"] = "2099-01-01T00:00:00+00:00"

    assert inventory_fingerprint(first) == inventory_fingerprint(second)


def test_inventory_reads_utf16_requirements_without_executing_them(tmp_path):
    source = _bugsinpy_fixture(tmp_path, test_command="pytest tests/test_demo.py")
    requirements = source / "projects" / "demo" / "bugs" / "1" / "requirements.txt"
    requirements.write_text("numpy==1.20\n", encoding="utf-16")

    inventory = build_bugsinpy_inventory(source, source_commit="a" * 40)

    assert inventory["inventory_error_count"] == 0
    assert inventory["cases"][0]["requirements"]["native_build_risk_packages"] == [
        "numpy"
    ]


def test_inventory_preserves_short_sha_as_blocked_until_resolution(tmp_path):
    source = _bugsinpy_fixture(tmp_path, test_command="pytest tests/test_demo.py")
    bug_info = source / "projects" / "demo" / "bugs" / "1" / "bug.info"
    bug_info.write_text(
        bug_info.read_text(encoding="utf-8").replace("b" * 40, "abc1234"),
        encoding="utf-8",
    )

    inventory = build_bugsinpy_inventory(source, source_commit="a" * 40)
    case = inventory["cases"][0]

    assert case["bug_commit_sha"] == "abc1234"
    assert case["commit_resolution"]["required"] is True
    assert case["eligibility"]["status"] == "blocked"
    assert "short_commit_sha_requires_resolution" in case["eligibility"][
        "blocking_reasons"
    ]


def test_seed_catalog_migrates_v3_evidence_and_adds_pending_candidate(tmp_path):
    source = _bugsinpy_fixture(tmp_path, test_command="pytest tests/test_demo.py::test_bug")
    inventory = build_bugsinpy_inventory(
        source,
        source_commit="a" * 40,
        available_python_versions={"3.8.3"},
    )
    v3_catalog = {
        "catalog_id": "v3-test",
        "catalog_sha256": "c" * 64,
        "cases": [_legacy_v3_case()],
    }
    plan = _selection_plan(inventory)

    catalog = build_v4_seed_catalog(
        v3_catalog=v3_catalog,
        inventory=inventory,
        selection_plan=plan,
    )
    audit = validate_v4_catalog(catalog)

    assert audit["status"] == "pass", audit["errors"]
    assert audit["summary"]["accepted_case_count"] == 1
    assert audit["summary"]["candidate_case_count"] == 1
    migrated = next(item for item in catalog["cases"] if item["status"] == "accepted")
    pending = next(item for item in catalog["cases"] if item["status"] == "candidate")
    assert migrated["difficulty_categories"] == ["dataflow", "real_traceback"]
    assert migrated["reproduction"]["acceptance"]["reproducible"] is True
    assert pending["reproduction"]["status"] == "pending"
    assert pending["model_context_audit"]["contains_gold_patch"] is False


def test_selection_plan_rejects_blocked_case(tmp_path):
    source = _bugsinpy_fixture(
        tmp_path,
        test_command="pytest tests/test_demo.py && echo unsafe",
    )
    inventory = build_bugsinpy_inventory(source, source_commit="a" * 40)
    plan = _selection_plan(inventory)

    errors = validate_selection_plan(plan, inventory=inventory)

    assert "selection_plan.blocked_case_selected:demo:1" in errors
    with pytest.raises(ValueError, match="blocked_case_selected"):
        build_v4_seed_catalog(
            v3_catalog={"catalog_id": "v3", "catalog_sha256": "c" * 64, "cases": []},
            inventory=inventory,
            selection_plan=plan,
        )


def test_enforced_target_contract_rejects_split_deficit_mismatch(tmp_path):
    source = _bugsinpy_fixture(tmp_path, test_command="pytest tests/test_demo.py")
    inventory = build_bugsinpy_inventory(source, source_commit="a" * 40)
    plan = _selection_plan(inventory)
    plan["target_contract"] = {
        "enforce": True,
        "baseline_accepted_split_counts": {
            "development": 1,
            "validation": 0,
            "test": 0,
        },
        "planned_accepted_additions": {
            "development": 9,
            "validation": 15,
            "test": 25,
        },
    }

    errors = validate_selection_plan(
        plan,
        inventory=inventory,
        v3_catalog={"cases": [_legacy_v3_case()]},
    )

    assert "selection_plan.project_targets_do_not_fill_split_deficits" in errors
    assert "selection_plan.cannot_reach_minimum_accepted_repository_count" in errors


def test_catalog_rejects_repository_split_leakage(tmp_path):
    source = _bugsinpy_fixture(tmp_path, test_command="pytest tests/test_demo.py")
    inventory = build_bugsinpy_inventory(source, source_commit="a" * 40)
    legacy = _legacy_v3_case()
    legacy["repository"]["owner_repo"] = "example/demo"
    legacy["repository"]["url"] = "https://github.com/example/demo"
    catalog = build_v4_seed_catalog(
        v3_catalog={"catalog_id": "v3", "catalog_sha256": "c" * 64, "cases": [legacy]},
        inventory=inventory,
        selection_plan=_selection_plan(inventory),
    )

    audit = validate_v4_catalog(catalog)

    assert any(item.startswith("repository_split_leakage:example/demo") for item in audit["errors"])


def test_locked_catalog_requires_final_counts_and_no_candidates(tmp_path):
    source = _bugsinpy_fixture(tmp_path, test_command="pytest tests/test_demo.py")
    inventory = build_bugsinpy_inventory(source, source_commit="a" * 40)
    catalog = build_v4_seed_catalog(
        v3_catalog={"catalog_id": "v3", "catalog_sha256": "c" * 64, "cases": [_legacy_v3_case()]},
        inventory=inventory,
        selection_plan=_selection_plan(inventory),
    )

    audit = validate_v4_catalog(catalog, require_locked=True)

    assert "catalog_must_be_locked" in audit["errors"]
    assert "locked_catalog_requires_exactly_50_accepted_cases" in audit["errors"]
    assert "locked_catalog_cannot_contain_candidates" in audit["errors"]


def test_accepted_case_requires_three_executable_reproduction_gates(tmp_path):
    source = _bugsinpy_fixture(tmp_path, test_command="pytest tests/test_demo.py")
    inventory = build_bugsinpy_inventory(source, source_commit="a" * 40)
    catalog = build_v4_seed_catalog(
        v3_catalog={"catalog_id": "v3", "catalog_sha256": "c" * 64, "cases": [_legacy_v3_case()]},
        inventory=inventory,
        selection_plan=_selection_plan(inventory),
    )
    candidate = next(item for item in catalog["cases"] if item["status"] == "candidate")
    candidate["status"] = "accepted"
    catalog["manifest_sha256"] = "0" * 64

    audit = validate_v4_catalog(catalog)

    assert any("accepted_case_requires_bug_target_failure" in item for item in audit["errors"])
    assert any("accepted_case_requires_fix_target_pass" in item for item in audit["errors"])
    assert any("accepted_case_requires_fix_regression_pass" in item for item in audit["errors"])


def test_catalog_writer_uses_lf(tmp_path):
    catalog = {
        "schema_version": "4.0",
        "catalog_id": "empty",
        "generated_at": "2026-07-17T00:00:00+00:00",
        "status": "seed_unlocked",
        "locked": False,
        "sources": [],
        "target": {},
        "selection_plan": {},
        "cases": [],
        "manifest_sha256": "",
        "summary": {},
    }
    from code_intelligence_agent.evaluation.v4_real_bug_benchmark import catalog_fingerprint

    catalog["manifest_sha256"] = catalog_fingerprint(catalog)
    paths = write_catalog_artifacts(catalog, tmp_path / "seed")

    for path in paths.values():
        assert b"\r\n" not in Path(path).read_bytes()


def _bugsinpy_fixture(tmp_path: Path, *, test_command: str) -> Path:
    root = tmp_path / "BugsInPy"
    project = root / "projects" / "demo"
    case = project / "bugs" / "1"
    case.mkdir(parents=True)
    (project / "project.info").write_text(
        'github_url="https://github.com/example/demo"\nstatus="OK"\n',
        encoding="utf-8",
    )
    (case / "bug.info").write_text(
        'python_version="3.8.3"\n'
        'pythonpath="demo/"\n'
        f'buggy_commit_id="{"b" * 40}"\n'
        f'fixed_commit_id="{"f" * 40}"\n'
        'test_file="tests/test_demo.py"\n',
        encoding="utf-8",
    )
    (case / "run_test.sh").write_text(test_command + "\n", encoding="utf-8")
    (case / "setup.sh").write_text("touch tests/__init__.py\n", encoding="utf-8")
    (case / "requirements.txt").write_text("pytest==8.0\n", encoding="utf-8")
    (case / "bug_patch.txt").write_text(
        "diff --git a/demo/core.py b/demo/core.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/demo/core.py\n"
        "+++ b/demo/core.py\n"
        "@@ -1,3 +1,3 @@ def normalize(value):\n"
        "-    return value + 1\n"
        "+    return value\n",
        encoding="utf-8",
    )
    return root


def _selection_plan(inventory: dict) -> dict:
    return {
        "schema_version": "4.0",
        "plan_id": "test-plan",
        "catalog_id": "test-v4-seed",
        "inventory_sha256": inventory["inventory_sha256"],
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
                "regression_command": ["{python}", "-m", "pytest", "-q"],
            }
        ],
    }


def _legacy_v3_case() -> dict:
    return {
        "case_id": "bugsinpy-legacy-1",
        "status": "accepted",
        "rejection_reason": "",
        "rejection_evidence": {},
        "benchmark_split": "development",
        "repository": {
            "url": "https://github.com/example/legacy",
            "owner_repo": "example/legacy",
            "license_spdx": "MIT",
            "license_url": "https://github.com/example/legacy/blob/" + "b" * 40 + "/LICENSE",
        },
        "bug_commit_sha": "b" * 40,
        "fix_commit_sha": "f" * 40,
        "python_version": "3.8.3",
        "environment_profile_id": "legacy-py3.8.3",
        "test_overlay_paths": ["tests/test_legacy.py"],
        "targeted_test_commands": [
            ["{python}", "-m", "pytest", "tests/test_legacy.py::test_bug"]
        ],
        "regression_command": ["{python}", "-m", "pytest", "-q"],
        "setup_observation": {
            "present": False,
            "source_path": "",
            "risk_level": "none",
            "actions": [],
            "executed": False,
        },
        "ground_truth": {
            "patch_sha256": sha256_file(__file__),
            "source_files": ["legacy/core.py"],
            "test_files": [],
            "functions": ["legacy/core.py:normalize"],
            "visible_to_model": False,
        },
        "difficulty_tags": ["data_flow"],
        "difficulty_tag_evidence": {"data_flow": "The failing value crosses a function boundary."},
        "provenance": {
            "benchmark": "BugsInPy",
            "benchmark_case": "legacy:1",
            "issue_or_pr_url": "https://github.com/example/legacy/issues/1",
            "bug_commit_url": "https://github.com/example/legacy/commit/" + "b" * 40,
            "fix_commit_url": "https://github.com/example/legacy/commit/" + "f" * 40,
        },
        "reproduction": {
            "evidence_status": "validated",
            "evidence_sha256": "e" * 64,
            "bug_targeted": {"status": "fail"},
            "fix_targeted": {"status": "pass"},
            "fix_full_regression": {"status": "pass"},
            "acceptance": {"reproducible": True},
            "raw_artifact_committed": False,
        },
    }
