import json
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.repository_test_repair_summary import (
    build_repository_test_repair_summary,
    render_repository_test_repair_summary_markdown,
    write_repository_test_repair_summary_artifacts,
)


def test_repository_test_repair_summary_marks_ready_patch_for_review():
    payload = build_repository_test_repair_summary(
        {
            "status": "pass",
            "reason": "patch_validation_success",
            "success_count": 1,
            "executed_count": 2,
            "repair_ready": True,
            "repair_validation_scope": "narrow_only",
            "best_patch": {
                "candidate_id": "candidate-1",
                "relative_file_path": "sample.py",
                "target_function_name": "shift_left",
                "rule_id": "possible_index_overrun",
                "variant": "shrink_range_upper_bound",
                "depth": 1,
                "parent_candidate_id": "candidate-0",
                "score": 0.91,
                "passed": 1,
                "failed": 0,
                "diff": "--- a/sample.py\n+++ b/sample.py\n",
            },
            "regression_validation": {"status": "skipped"},
        },
        output_paths={
            "repository_test_repair_patch": "out/repository_test_repair.patch"
        },
        patch_candidates={
            "recommended_validation_command": (
                "python -m pytest -q tests/test_sample.py::test_bug"
            )
        },
        fault_localization={"top_function": "shift_left"},
    )

    assert payload["status"] == "pass"
    assert payload["reason"] == "repair_ready"
    assert payload["conclusion"] == "ready_for_review"
    assert payload["repair_ready"] is True
    assert payload["repair_validation_scope"] == "narrow_only"
    assert payload["patch_path_present"] is True
    assert payload["top_function"] == "shift_left"
    assert payload["best_patch"]["has_diff"] is True
    assert payload["best_patch"]["relative_file_path"] == "sample.py"
    assert "broader repository regression tests" in "\n".join(
        payload["next_actions"]
    )

    markdown = render_repository_test_repair_summary_markdown(payload)
    assert "Repository Test Repair Summary" in markdown
    assert "ready_for_review" in markdown
    assert "out/repository_test_repair.patch" in markdown

    with tempfile.TemporaryDirectory() as tmp_dir:
        paths = write_repository_test_repair_summary_artifacts(payload, tmp_dir)
        assert Path(paths["repository_test_repair_summary_json"]).exists()
        assert Path(paths["repository_test_repair_summary_markdown"]).exists()
        saved = json.loads(
            Path(paths["repository_test_repair_summary_json"]).read_text(
                encoding="utf-8"
            )
        )
        assert saved["conclusion"] == "ready_for_review"


def test_repository_test_repair_summary_blocks_regression_failed_patch():
    payload = build_repository_test_repair_summary(
        {
            "status": "pass",
            "reason": "patch_validation_success",
            "success_count": 1,
            "executed_count": 2,
            "repair_ready": False,
            "repair_validation_scope": "regression_failed",
            "regression_validation": {
                "status": "fail",
                "reason": "regression_tests_failed",
                "validation_command": "python -m pytest -q tests",
                "passed": 8,
                "failed": 1,
                "failure_type": "assertion",
            },
        },
        output_paths={
            "repository_test_repair_patch": "out/repository_test_repair.patch"
        },
    )

    assert payload["status"] == "fail"
    assert payload["reason"] == "regression_validation_failed"
    assert payload["conclusion"] == "not_ready"
    assert payload["repair_ready"] is False
    assert payload["regression_validation"]["failed"] == 1
    assert "Do not promote" in payload["next_actions"][0]


def test_repository_test_repair_summary_skips_without_patch_validation():
    payload = build_repository_test_repair_summary(None)

    assert payload["status"] == "skipped"
    assert payload["reason"] == "patch_validation_missing"
    assert payload["conclusion"] == "not_ready"
    assert payload["patch_path_present"] is False
