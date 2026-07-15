from __future__ import annotations

from code_intelligence_agent.evaluation.v3_localization_ground_truth import (
    resolve_v3_localization_ground_truth,
)


def test_ground_truth_maps_decorator_and_nested_diff_to_exact_bug_functions(tmp_path):
    bug = tmp_path / "bug"
    fix = tmp_path / "fix"
    bug.mkdir()
    fix.mkdir()
    (bug / "module.py").write_text(
        "def marker(value):\n"
        "    return value\n\n"
        "@marker('old')\n"
        "def main():\n"
        "    def inner(value):\n"
        "        return value + 1\n"
        "    return inner(1)\n",
        encoding="utf-8",
    )
    (fix / "module.py").write_text(
        "def marker(value):\n"
        "    return value\n\n"
        "@marker('new')\n"
        "def main():\n"
        "    def inner(value):\n"
        "        return value + 2\n"
        "    return inner(1)\n",
        encoding="utf-8",
    )

    result = resolve_v3_localization_ground_truth(
        case_id="case-1",
        bug_repository=bug,
        fix_repository=fix,
        source_files=["module.py"],
        ranking_snapshot_sha256="a" * 64,
    )

    assert result["function_rankable"] is True
    assert result["function_keys"] == ["module.py::main", "module.py::main.inner"]
    assert result["ground_truth_used_for_ranking"] is False
    assert result["resolved_after_ranking_frozen"] is True
    assert len(result["ground_truth_sha256"]) == 64


def test_ground_truth_marks_added_function_as_not_rankable_in_bug_revision(tmp_path):
    bug = tmp_path / "bug"
    fix = tmp_path / "fix"
    bug.mkdir()
    fix.mkdir()
    (bug / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (fix / "module.py").write_text(
        "VALUE = 1\n\n"
        "def newly_added():\n"
        "    return VALUE\n",
        encoding="utf-8",
    )

    result = resolve_v3_localization_ground_truth(
        case_id="case-added",
        bug_repository=bug,
        fix_repository=fix,
        source_files=["module.py"],
        ranking_snapshot_sha256="b" * 64,
    )

    assert result["function_rankable"] is False
    assert result["function_keys"] == []
    assert result["files"][0]["fix_function_keys"] == ["module.py::newly_added"]
    assert result["files"][0]["projected_fix_function_keys"] == []


def test_ground_truth_requires_frozen_ranking_evidence(tmp_path):
    try:
        resolve_v3_localization_ground_truth(
            case_id="case-unsafe",
            bug_repository=tmp_path,
            fix_repository=tmp_path,
            source_files=["module.py"],
            ranking_snapshot_sha256="",
        )
    except ValueError as exc:
        assert "ranking_snapshot_sha256" in str(exc)
    else:
        raise AssertionError("ground truth resolution must require a frozen ranking")
