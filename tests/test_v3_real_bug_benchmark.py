from __future__ import annotations

import copy
from pathlib import Path

import pytest

from code_intelligence_agent.evaluation.v3_real_bug_benchmark import (
    import_bugsinpy_selection,
    inspect_setup_script,
    parse_assignment_file,
    parse_patch_ground_truth,
    parse_test_commands,
    validate_real_bug_catalog,
)


def test_assignment_parser_accepts_data_and_rejects_shell(tmp_path):
    safe = tmp_path / "bug.info"
    safe.write_text(
        'buggy_commit_id="' + "a" * 40 + '"\nfixed_commit_id = "' + "b" * 40 + '"\n',
        encoding="utf-8",
    )

    assert parse_assignment_file(safe)["buggy_commit_id"] == "a" * 40
    assert parse_assignment_file(safe)["fixed_commit_id"] == "b" * 40

    unsafe = tmp_path / "unsafe.info"
    unsafe.write_text('value="ok"\n$(touch escaped)\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported assignment syntax"):
        parse_assignment_file(unsafe)


def test_test_command_parser_normalizes_python_and_pytest_without_shell(tmp_path):
    commands = tmp_path / "run_test.sh"
    commands.write_text(
        "python -m unittest -q tests.test_case.TestCase.test_bug\n"
        "pytest -q tests/test_case.py::test_bug\n"
        "python -m nose -q tests/test_legacy.py\n",
        encoding="utf-8",
    )

    assert parse_test_commands(commands) == [
        ["{python}", "-m", "unittest", "-q", "tests.test_case.TestCase.test_bug"],
        ["{python}", "-m", "pytest", "-q", "tests/test_case.py::test_bug"],
        ["{python}", "-m", "nose", "-q", "tests/test_legacy.py"],
    ]

    commands.write_text("pytest -q tests/test_case.py && echo escaped\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Shell control token"):
        parse_test_commands(commands)


def test_setup_script_is_inspected_but_not_executed(tmp_path):
    marker = tmp_path / "should_not_exist"
    setup = tmp_path / "setup.sh"
    setup.write_text(
        "touch tests/__init__.py\n"
        "python setup.py install\n"
        f"touch {marker.as_posix()}\n",
        encoding="utf-8",
    )

    observation = inspect_setup_script(setup)

    assert observation["executed"] is False
    assert observation["risk_level"] == "high"
    assert [item["kind"] for item in observation["actions"]] == [
        "file_touch",
        "local_build_install",
        "unsupported",
    ]
    assert marker.exists() is False


def test_patch_parser_separates_source_and_test_ground_truth(tmp_path):
    patch = tmp_path / "bug_patch.txt"
    patch.write_text(
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n+++ b/pkg/core.py\n"
        "@@ -1,2 +1,2 @@ def calculate(value):\n"
        "-    return value - 1\n+    return value + 1\n"
        "diff --git a/tests/test_core.py b/tests/test_core.py\n"
        "--- a/tests/test_core.py\n+++ b/tests/test_core.py\n"
        "@@ -1,2 +1,2 @@ def test_calculate():\n",
        encoding="utf-8",
    )

    ground_truth = parse_patch_ground_truth(patch)

    assert ground_truth["source_files"] == ["pkg/core.py"]
    assert ground_truth["test_files"] == ["tests/test_core.py"]
    assert ground_truth["functions"] == [
        "pkg/core.py:calculate",
        "tests/test_core.py:test_calculate",
    ]


def test_bugsinpy_importer_builds_candidate_without_executing_scripts(tmp_path):
    root = _source_fixture(tmp_path)
    selection = _selection_fixture()

    catalog = import_bugsinpy_selection(root, selection)
    audit = validate_real_bug_catalog(catalog)

    assert audit["status"] == "pass", audit["errors"]
    assert audit["warning_count"] == 1
    case = catalog["cases"][0]
    assert case["case_id"] == "bugsinpy-demo-1"
    assert case["status"] == "candidate"
    assert case["reproduction"]["test_overlay_policy"].startswith("copy_listed")
    assert case["ground_truth"]["visible_to_model"] is False
    assert case["setup_observation"]["executed"] is False


def test_catalog_rejects_repository_split_leakage_and_false_acceptance(tmp_path):
    catalog = import_bugsinpy_selection(_source_fixture(tmp_path), _selection_fixture())
    second = copy.deepcopy(catalog["cases"][0])
    second["case_id"] = "bugsinpy-demo-2"
    second["bug_commit_sha"] = "c" * 40
    second["benchmark_split"] = "test"
    catalog["cases"].append(second)
    catalog["case_count"] = 2
    catalog.pop("catalog_sha256")

    audit = validate_real_bug_catalog(catalog)

    assert audit["status"] == "fail"
    assert any(item.startswith("repository_split_leakage:") for item in audit["errors"])

    catalog["cases"] = [catalog["cases"][0]]
    catalog["case_count"] = 1
    catalog["cases"][0]["status"] = "accepted"
    audit = validate_real_bug_catalog(catalog)

    assert "case[0].accepted_case_requires_issue_or_pr_url" in audit["errors"]
    assert "case[0].accepted_reproduction_bug_targeted_must_fail" in audit["errors"]


def test_bugsinpy_importer_preserves_explicit_rejection(tmp_path):
    selection = _selection_fixture()
    selected_case = selection["projects"][0]["cases"][0]
    selected_case["status"] = "rejected"
    selected_case["rejection_reason"] = "full_regression_incompatible"
    selected_case["rejection_evidence"] = {
        "summary": "The fixed revision cannot provide a clean full-regression oracle."
    }

    catalog = import_bugsinpy_selection(_source_fixture(tmp_path), selection)
    audit = validate_real_bug_catalog(catalog)

    assert audit["status"] == "pass", audit["errors"]
    assert catalog["cases"][0]["status"] == "rejected"
    assert catalog["cases"][0]["rejection_reason"] == "full_regression_incompatible"
    assert catalog["cases"][0]["rejection_evidence"]["summary"].startswith(
        "The fixed revision"
    )


def test_bugsinpy_importer_allows_safe_case_specific_regression_command(tmp_path):
    selection = _selection_fixture()
    selected_case = selection["projects"][0]["cases"][0]
    selected_case["regression_command"] = [
        "{python}",
        "-m",
        "unittest",
        "-q",
        "tests.test_core",
    ]
    selected_case["regression_provenance"] = {"case_specific": True}

    catalog = import_bugsinpy_selection(_source_fixture(tmp_path), selection)

    assert catalog["cases"][0]["regression_command"] == selected_case[
        "regression_command"
    ]
    assert catalog["cases"][0]["regression_provenance"]["case_specific"] is True


def test_bugsinpy_importer_records_reasoned_test_support_overlay(tmp_path):
    selection = _selection_fixture()
    selected_case = selection["projects"][0]["cases"][0]
    selected_case["test_overlay_additions"] = [
        {
            "path": "tests/helpers.py",
            "reason": "The fixed targeted test imports this test-only helper.",
        }
    ]

    catalog = import_bugsinpy_selection(_source_fixture(tmp_path), selection)
    case = catalog["cases"][0]

    assert case["test_overlay_paths"] == [
        "tests/test_core.py",
        "tests/helpers.py",
    ]
    assert case["test_overlay_provenance"]["additional_support_files"] == [
        selected_case["test_overlay_additions"][0]
    ]


def test_catalog_rejects_platform_exclusion_that_hides_target_test(tmp_path):
    selection = _selection_fixture()
    selected_case = selection["projects"][0]["cases"][0]
    selected_case["regression_command"] = [
        "{python}",
        "-m",
        "pytest",
        "-q",
        "tests",
        "--deselect=tests/test_core.py::test_calculate",
    ]
    selected_case["regression_provenance"] = {
        "platform_exclusions": [
            {
                "test": "tests/test_core.py::test_calculate",
                "platform": "win32",
                "reason": "Fixture exclusion that must be rejected.",
            }
        ]
    }

    catalog = import_bugsinpy_selection(_source_fixture(tmp_path), selection)
    audit = validate_real_bug_catalog(catalog)

    assert (
        "case[0].platform_exclusion_overlaps_target_or_ground_truth"
        in audit["errors"]
    )


def test_catalog_accepts_declared_unrelated_platform_exclusion(tmp_path):
    selection = _selection_fixture()
    selected_case = selection["projects"][0]["cases"][0]
    selected_case["regression_command"] = [
        "{python}",
        "-m",
        "pytest",
        "-q",
        "tests",
        "--deselect=tests/test_windows_only.py::test_clock",
    ]
    selected_case["regression_provenance"] = {
        "platform_exclusions": [
            {
                "test": "tests/test_windows_only.py::test_clock",
                "platform": "win32",
                "reason": "Independent platform clock behavior.",
            }
        ]
    }

    catalog = import_bugsinpy_selection(_source_fixture(tmp_path), selection)
    audit = validate_real_bug_catalog(catalog)

    assert audit["status"] == "pass", audit["errors"]


def _source_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "BugsInPy"
    project = root / "projects" / "demo"
    case = project / "bugs" / "1"
    case.mkdir(parents=True)
    (project / "project.info").write_text(
        'github_url="https://github.com/example/demo"\n', encoding="utf-8"
    )
    (case / "bug.info").write_text(
        'python_version="3.11.9"\n'
        'buggy_commit_id="' + "a" * 40 + '"\n'
        'fixed_commit_id="' + "b" * 40 + '"\n'
        'test_file="tests/test_core.py"\n',
        encoding="utf-8",
    )
    (case / "run_test.sh").write_text(
        "pytest -q tests/test_core.py::test_calculate\n", encoding="utf-8"
    )
    (case / "setup.sh").write_text("touch tests/__init__.py\n", encoding="utf-8")
    (case / "bug_patch.txt").write_text(
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n+++ b/pkg/core.py\n"
        "@@ -1 +1 @@ def calculate(value):\n"
        "-    return value - 1\n+    return value + 1\n",
        encoding="utf-8",
    )
    return root


def _selection_fixture() -> dict:
    return {
        "schema_version": "3.0",
        "catalog_id": "fixture",
        "source": {
            "repository_url": "https://github.com/soarsmu/BugsInPy",
            "commit_sha": "d" * 40,
            "license_status": "not_declared",
        },
        "projects": [
            {
                "name": "demo",
                "environment_profile_id": "demo-py3.11.9",
                "benchmark_split": "development",
                "license": {
                    "spdx": "MIT",
                    "url": "https://github.com/example/demo/blob/main/LICENSE",
                },
                "regression_command": ["{python}", "-m", "pytest", "-q"],
                "cases": [
                    {
                        "bug_id": 1,
                        "difficulty_tags": ["static_negative"],
                        "difficulty_tag_evidence": {
                            "static_negative": "Fixture semantic arithmetic defect."
                        },
                    }
                ],
            }
        ],
    }
