from pathlib import Path

from code_intelligence_agent.evaluation.repository_test_dynamic_evidence import (
    build_repository_test_dynamic_evidence,
    render_repository_test_dynamic_evidence_markdown,
    write_repository_test_dynamic_evidence_artifacts,
)


def test_repository_test_dynamic_evidence_extracts_pytest_failed_nodeids(tmp_path):
    payload = build_repository_test_dynamic_evidence(
        {
            "status": "fail",
            "executed": True,
            "reason": "command_returncode",
            "command": "python -m pytest -q tests/test_math.py",
            "returncode": 1,
            "passed": 2,
            "failed": 1,
            "failure_category": "test_assertion_failure",
            "failure_signal": "FAILED tests/test_math.py::test_average",
            "diagnostic_summary": "A test assertion failed.",
            "stdout_preview": (
                "FAILED tests/test_math.py::test_average - AssertionError\n"
                "tests/test_more.py::TestStats::test_mean FAILED [100%]\n"
            ),
            "stderr_preview": "",
        },
        execution_plan={
            "recommended_execution_command": "python -m pytest -q tests/test_math.py",
        },
    )
    paths = write_repository_test_dynamic_evidence_artifacts(payload, tmp_path)
    markdown = render_repository_test_dynamic_evidence_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["evidence_level"] == "failing_tests"
    assert payload["source"] == "planned_execution_result"
    assert payload["usable_for_localization"] is True
    assert payload["usable_for_regression_validation"] is False
    assert payload["usable_for_patch_validation"] is True
    assert payload["recommended_validation_command"] == (
        "python -m pytest -q tests/test_math.py"
    )
    assert payload["failing_test_count"] == 2
    assert payload["failing_tests"][0]["nodeid"] == (
        "tests/test_math.py::test_average"
    )
    assert payload["failing_tests"][1]["nodeid"] == (
        "tests/test_more.py::TestStats::test_mean"
    )
    assert "Repository Test Dynamic Evidence" in markdown
    assert "test_average" in markdown
    assert Path(paths["repository_test_dynamic_evidence_json"]).exists()
    assert Path(paths["repository_test_dynamic_evidence_markdown"]).exists()


def test_repository_test_dynamic_evidence_marks_missing_dependency_not_usable():
    payload = build_repository_test_dynamic_evidence(
        {
            "status": "fail",
            "executed": True,
            "command": "python -m pytest -q",
            "returncode": 2,
            "failure_category": "missing_dependency",
            "failure_signal": "missing_module:requests",
            "diagnostic_summary": "Dependency import failed.",
            "stdout_preview": "",
            "stderr_preview": "ModuleNotFoundError: No module named 'requests'",
        }
    )

    assert payload["status"] == "warning"
    assert payload["evidence_level"] == "environment_failure"
    assert payload["usable_for_localization"] is False
    assert payload["usable_for_regression_validation"] is False
    assert payload["usable_for_patch_validation"] is False
    assert payload["failing_test_count"] == 0
    assert any("dependency" in action for action in payload["next_actions"])


def test_repository_test_dynamic_evidence_extracts_nodeid_from_failure_signal():
    payload = build_repository_test_dynamic_evidence(
        {
            "status": "fail",
            "executed": True,
            "reason": "command_returncode",
            "command": "python -m pytest -q tests/test_large.py",
            "returncode": 1,
            "passed": 58,
            "failed": 1,
            "failure_category": "test_assertion_failure",
            "failure_signal": (
                "FAILED tests/test_large.py::TestParser::test_truncated_report"
            ),
            "diagnostic_summary": "A test assertion failed.",
            "stdout_preview": "",
            "stderr_preview": "",
        }
    )

    assert payload["status"] == "pass"
    assert payload["evidence_level"] == "failing_tests"
    assert payload["failing_test_count"] == 1
    assert payload["failing_tests"] == [
        {
            "nodeid": "tests/test_large.py::TestParser::test_truncated_report",
            "path": "tests/test_large.py",
            "test_name": "TestParser::test_truncated_report",
            "source_line": (
                "FAILED tests/test_large.py::TestParser::test_truncated_report"
            ),
        }
    ]
    assert payload["usable_for_localization"] is True
    assert payload["usable_for_patch_validation"] is True


def test_repository_test_dynamic_evidence_extracts_unittest_failure_identifier():
    payload = build_repository_test_dynamic_evidence(
        {
            "status": "fail",
            "executed": True,
            "reason": "command_returncode",
            "command": "python -m unittest discover",
            "returncode": 1,
            "passed": 0,
            "failed": 1,
            "failure_category": "test_assertion_failure",
            "failure_signal": (
                "FAIL: test_pick_short_values "
                "(tests.test_service.TestService.test_pick_short_values)"
            ),
            "diagnostic_summary": "A unittest assertion failed.",
            "stdout_preview": "",
            "stderr_preview": "",
        }
    )

    assert payload["status"] == "pass"
    assert payload["evidence_level"] == "failing_tests"
    assert payload["failing_test_count"] == 1
    assert payload["failing_tests"] == [
        {
            "nodeid": (
                "tests/test_service.py::TestService::test_pick_short_values"
            ),
            "path": "tests/test_service.py",
            "test_name": "TestService::test_pick_short_values",
            "source_line": (
                "FAIL: test_pick_short_values "
                "(tests.test_service.TestService.test_pick_short_values)"
            ),
        }
    ]
    assert payload["usable_for_localization"] is True
    assert payload["usable_for_patch_validation"] is True


def test_repository_test_dynamic_evidence_extracts_from_failure_context():
    payload = build_repository_test_dynamic_evidence(
        {
            "status": "fail",
            "executed": True,
            "reason": "command_returncode",
            "command": "python -m unittest discover",
            "returncode": 1,
            "passed": 0,
            "failed": 1,
            "failure_category": "test_assertion_failure",
            "failure_signal": "returncode:1",
            "diagnostic_summary": "A unittest assertion failed.",
            "failure_context": (
                "[stdout] FAIL: test_pick_short_values "
                "(tests.test_service.TestService.test_pick_short_values)\n"
                "[stdout] Traceback (most recent call last):\n"
                "[stdout]   File \"tests/test_service.py\", line 6, in "
                "test_pick_short_values\n"
                "[stdout]     self.assertEqual(pick([1]), 1)\n"
                "[stdout] AssertionError: 2 != 1\n"
            ),
            "failure_context_line_count": 5,
            "stdout_preview": "",
            "stderr_preview": "",
        }
    )
    markdown = render_repository_test_dynamic_evidence_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["evidence_level"] == "failing_tests"
    assert payload["failing_tests"][0]["nodeid"] == (
        "tests/test_service.py::TestService::test_pick_short_values"
    )
    assert payload["traceback_frames"] == [
        {
            "path": "tests/test_service.py",
            "line": 6,
            "function_name": "test_pick_short_values",
            "source_line": (
                "File \"tests/test_service.py\", line 6, in "
                "test_pick_short_values"
            ),
            "format": "python_traceback",
        }
    ]
    assert payload["usable_for_localization"] is True
    assert "Failure Context Lines: 5" in markdown
    assert "tests/test_service.py" in markdown


def test_repository_test_dynamic_evidence_preserves_parameterized_nodeid_with_spaces():
    payload = build_repository_test_dynamic_evidence(
        {
            "status": "fail",
            "executed": True,
            "reason": "command_returncode",
            "command": "python -m pytest -q tests/test_large.py",
            "returncode": 1,
            "passed": 58,
            "failed": 1,
            "failure_category": "test_assertion_failure",
            "failure_signal": (
                "FAILED tests/test_large.py::test_parse[empty value] - AssertionError"
            ),
            "diagnostic_summary": "A parameterized test assertion failed.",
            "stdout_preview": (
                "tests/test_large.py::test_render[case with spaces] FAILED [100%]\n"
            ),
            "stderr_preview": "",
        }
    )

    assert payload["status"] == "pass"
    assert payload["evidence_level"] == "failing_tests"
    assert payload["failing_tests"][0]["nodeid"] == (
        "tests/test_large.py::test_render[case with spaces]"
    )
    assert payload["failing_tests"][1]["nodeid"] == (
        "tests/test_large.py::test_parse[empty value]"
    )
    assert payload["failing_tests"][1]["test_name"] == "test_parse[empty value]"


def test_repository_test_dynamic_evidence_extracts_inline_nodeid_from_failure_signal():
    payload = build_repository_test_dynamic_evidence(
        {
            "status": "fail",
            "executed": True,
            "reason": "command_returncode",
            "command": "python -m pytest -q tests/test_large.py",
            "returncode": 1,
            "passed": 58,
            "failed": 1,
            "failure_category": "test_assertion_failure",
            "failure_signal": (
                "tests/test_large.py::TestParser::test_inline FAILED [100%]"
            ),
            "diagnostic_summary": "A test assertion failed.",
            "stdout_preview": "",
            "stderr_preview": "",
        }
    )

    assert payload["status"] == "pass"
    assert payload["evidence_level"] == "failing_tests"
    assert payload["failing_tests"] == [
        {
            "nodeid": "tests/test_large.py::TestParser::test_inline",
            "path": "tests/test_large.py",
            "test_name": "TestParser::test_inline",
            "source_line": (
                "tests/test_large.py::TestParser::test_inline FAILED [100%]"
            ),
        }
    ]
    assert payload["usable_for_localization"] is True


def test_repository_test_dynamic_evidence_extracts_nodeid_scoped_command_signal():
    payload = build_repository_test_dynamic_evidence(
        {
            "status": "fail",
            "executed": True,
            "reason": "command_returncode",
            "command": (
                "python -m pytest -q --maxfail=1 "
                "tests/test_retry.py::TestRetry::test_guard"
            ),
            "returncode": 1,
            "passed": 0,
            "failed": 1,
            "failure_category": "test_assertion_failure",
            "failure_signal": (
                "FAILED tests/test_retry.py::TestRetry::test_guard "
                "(nodeid-scoped pytest command)"
            ),
            "diagnostic_summary": "The nodeid-scoped pytest command failed.",
            "stdout_preview": "",
            "stderr_preview": "",
        }
    )

    assert payload["status"] == "pass"
    assert payload["evidence_level"] == "failing_tests"
    assert payload["failing_tests"] == [
        {
            "nodeid": "tests/test_retry.py::TestRetry::test_guard",
            "path": "tests/test_retry.py",
            "test_name": "TestRetry::test_guard",
            "source_line": (
                "FAILED tests/test_retry.py::TestRetry::test_guard "
                "(nodeid-scoped pytest command)"
            ),
        }
    ]
    assert payload["usable_for_patch_validation"] is True


def test_repository_test_dynamic_evidence_falls_back_to_command_nodeid():
    payload = build_repository_test_dynamic_evidence(
        {
            "status": "fail",
            "executed": True,
            "reason": "command_returncode",
            "command": (
                "python -m pytest -q --maxfail=1 "
                "tests/test_retry.py::TestRetry::test_guard"
            ),
            "returncode": 1,
            "passed": 0,
            "failed": 1,
            "failure_category": "test_assertion_failure",
            "failure_signal": "AssertionError: truncated output",
            "diagnostic_summary": "A nodeid-scoped pytest command failed.",
            "stdout_preview": "",
            "stderr_preview": "",
        }
    )

    assert payload["status"] == "pass"
    assert payload["evidence_level"] == "failing_tests"
    assert payload["failing_test_count"] == 1
    assert payload["failing_tests"] == [
        {
            "nodeid": "tests/test_retry.py::TestRetry::test_guard",
            "path": "tests/test_retry.py",
            "test_name": "TestRetry::test_guard",
            "source_line": (
                "pytest command target: "
                "tests/test_retry.py::TestRetry::test_guard"
            ),
        }
    ]
    assert payload["usable_for_localization"] is True
    assert payload["usable_for_patch_validation"] is True


def test_repository_test_dynamic_evidence_falls_back_to_quoted_parameterized_command_nodeid():
    payload = build_repository_test_dynamic_evidence(
        {
            "status": "fail",
            "executed": True,
            "reason": "command_returncode",
            "command": (
                "python -m pytest -q --maxfail=1 "
                "'tests/test_retry.py::TestRetry::test_guard[pkg::empty value]'"
            ),
            "returncode": 1,
            "passed": 0,
            "failed": 1,
            "failure_category": "test_assertion_failure",
            "failure_signal": "AssertionError: truncated output",
            "diagnostic_summary": "A nodeid-scoped pytest command failed.",
            "stdout_preview": "",
            "stderr_preview": "",
        }
    )

    assert payload["status"] == "pass"
    assert payload["evidence_level"] == "failing_tests"
    assert payload["failing_tests"] == [
        {
            "nodeid": (
                "tests/test_retry.py::TestRetry::test_guard[pkg::empty value]"
            ),
            "path": "tests/test_retry.py",
            "test_name": "TestRetry::test_guard[pkg::empty value]",
            "source_line": (
                "pytest command target: "
                "tests/test_retry.py::TestRetry::test_guard[pkg::empty value]"
            ),
        }
    ]


def test_repository_test_dynamic_evidence_does_not_mark_broad_assertion_without_nodeid_usable():
    payload = build_repository_test_dynamic_evidence(
        {
            "status": "fail",
            "executed": True,
            "reason": "command_returncode",
            "command": "python -m pytest -q tests",
            "returncode": 1,
            "passed": 3,
            "failed": 1,
            "failure_category": "test_assertion_failure",
            "failure_signal": "AssertionError: output truncated",
            "diagnostic_summary": "A broad pytest command failed without nodeids.",
            "stdout_preview": "",
            "stderr_preview": "",
        }
    )

    assert payload["status"] == "warning"
    assert payload["reason"] == "test_assertion_failure_without_nodeid"
    assert payload["evidence_level"] == "unknown_failure"
    assert payload["failing_test_count"] == 0
    assert payload["usable_for_localization"] is False
    assert payload["usable_for_patch_validation"] is False


def test_repository_test_dynamic_evidence_uses_traceback_without_nodeid():
    payload = build_repository_test_dynamic_evidence(
        {
            "status": "fail",
            "executed": True,
            "reason": "command_returncode",
            "command": "python -m pytest -q tests",
            "returncode": 1,
            "passed": 3,
            "failed": 1,
            "failure_category": "test_assertion_failure",
            "failure_signal": "AssertionError: index error",
            "diagnostic_summary": "A broad pytest command failed without nodeids.",
            "stdout_preview": (
                "Traceback (most recent call last):\n"
                "  File \"service.py\", line 2, in pick\n"
                "    return values[1]\n"
            ),
            "stderr_preview": "",
        }
    )
    markdown = render_repository_test_dynamic_evidence_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["reason"] == "test_assertion_failure_with_traceback"
    assert payload["evidence_level"] == "traceback"
    assert payload["failing_test_count"] == 0
    assert payload["traceback_frame_count"] == 1
    assert payload["traceback_frames"][0]["path"] == "service.py"
    assert payload["traceback_frames"][0]["line"] == 2
    assert payload["traceback_frames"][0]["function_name"] == "pick"
    assert payload["usable_for_localization"] is True
    assert payload["usable_for_patch_validation"] is True
    assert "Traceback Frames" in markdown


def test_repository_test_dynamic_evidence_marks_missing_fixture_as_environment_failure():
    payload = build_repository_test_dynamic_evidence(
        {
            "status": "fail",
            "executed": True,
            "command": "python -m pytest -q --maxfail=1 tests",
            "returncode": 1,
            "passed": 1,
            "failure_category": "missing_pytest_fixture",
            "failure_signal": "missing_fixture:mocker",
            "diagnostic_summary": "A pytest fixture plugin is missing.",
            "stdout_preview": "ERROR tests/test_help.py::test_idna_without_version_attribute",
            "stderr_preview": "",
        }
    )

    assert payload["status"] == "warning"
    assert payload["evidence_level"] == "environment_failure"
    assert payload["reason"] == "missing_pytest_fixture"
    assert payload["usable_for_localization"] is False
    assert payload["usable_for_patch_validation"] is False
    assert payload["recommended_validation_command"] == (
        "python -m pytest -q --maxfail=1 tests"
    )


def test_repository_test_dynamic_evidence_marks_warning_policy_not_patch_usable():
    payload = build_repository_test_dynamic_evidence(
        {
            "status": "fail",
            "executed": True,
            "command": "python -m pytest -q tests",
            "returncode": 2,
            "failure_category": "pytest_warning_as_error",
            "failure_signal": "pytest.PytestRemovedIn10Warning: deprecated",
            "diagnostic_summary": "warning policy failed collection",
        }
    )

    assert payload["status"] == "warning"
    assert payload["evidence_level"] == "environment_failure"
    assert payload["usable_for_localization"] is False
    assert payload["usable_for_regression_validation"] is False
    assert payload["usable_for_patch_validation"] is False


def test_repository_test_dynamic_evidence_prefers_retry_assertion_failure():
    payload = build_repository_test_dynamic_evidence(
        {
            "status": "fail",
            "executed": True,
            "command": "python -m pytest -q",
            "returncode": 1,
            "failure_category": "command_failed",
            "failure_signal": "returncode:1",
            "diagnostic_summary": "Broad command failed.",
            "stdout_preview": "",
            "stderr_preview": "",
        },
        {
            "status": "fail",
            "executed": True,
            "command": "python -m pytest -q tests/test_retry.py",
            "returncode": 1,
            "passed": 0,
            "failed": 1,
            "failure_category": "test_assertion_failure",
            "failure_signal": "FAILED tests/test_retry.py::test_guard",
            "diagnostic_summary": "Retry isolated a failing assertion.",
            "stdout_preview": "FAILED tests/test_retry.py::test_guard - AssertionError",
            "stderr_preview": "",
            "retry_command": "python -m pytest -q tests/test_retry.py",
        },
    )

    assert payload["source"] == "retry_execution_result"
    assert payload["evidence_level"] == "failing_tests"
    assert payload["recommended_validation_command"] == (
        "python -m pytest -q tests/test_retry.py"
    )
    assert payload["failing_tests"] == [
        {
            "nodeid": "tests/test_retry.py::test_guard",
            "path": "tests/test_retry.py",
            "test_name": "test_guard",
            "source_line": "FAILED tests/test_retry.py::test_guard - AssertionError",
        }
    ]


def test_repository_test_dynamic_evidence_prefers_retry_passing_tests():
    payload = build_repository_test_dynamic_evidence(
        {
            "status": "fail",
            "executed": True,
            "command": "python -m pytest -q",
            "returncode": 5,
            "failure_category": "no_tests_collected",
            "failure_signal": "collected 0 items",
            "diagnostic_summary": "Broad command collected no tests.",
            "stdout_preview": "collected 0 items\nno tests ran",
            "stderr_preview": "",
        },
        {
            "status": "pass",
            "executed": True,
            "command": "python -m pytest -q tests/test_retry.py",
            "returncode": 0,
            "passed": 1,
            "failed": 0,
            "failure_category": "none",
            "failure_signal": "",
            "diagnostic_summary": "Retry passed.",
            "stdout_preview": "1 passed",
            "stderr_preview": "",
            "retry_command": "python -m pytest -q tests/test_retry.py",
        },
    )

    assert payload["source"] == "retry_execution_result"
    assert payload["evidence_level"] == "passing_tests"
    assert payload["recommended_validation_command"] == (
        "python -m pytest -q tests/test_retry.py"
    )
    assert payload["usable_for_localization"] is False
    assert payload["usable_for_regression_validation"] is True
    assert payload["usable_for_patch_validation"] is False
    assert any("controlled failure overlay" in action for action in payload["next_actions"])
