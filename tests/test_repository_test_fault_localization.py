from pathlib import Path

from code_intelligence_agent.evaluation.repository_test_dynamic_evidence import (
    build_repository_test_dynamic_evidence,
)
from code_intelligence_agent.evaluation.repository_test_fault_localization import (
    build_repository_test_fault_localization,
    dynamic_evidence_to_test_summary,
    render_repository_test_fault_localization_markdown,
    write_repository_test_fault_localization_artifacts,
)
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.program_graph import build_program_graph
from code_intelligence_agent.core.repo_parser import RepoParser


def test_repository_test_fault_localization_ranks_function_from_failing_nodeid(tmp_path):
    _write_fault_localization_repo(tmp_path)
    payload = build_repository_test_fault_localization(
        _dynamic_evidence(),
        repository_root=tmp_path,
        top_k=3,
    )
    paths = write_repository_test_fault_localization_artifacts(
        payload,
        tmp_path / "out",
    )
    markdown = render_repository_test_fault_localization_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["reason"] == "localized_from_dynamic_evidence"
    assert payload["matched_failed_test_count"] == 1
    assert payload["unmatched_failed_test_count"] == 0
    assert payload["ranking_count"] >= 1
    assert payload["top_function"] == "pick"
    assert payload["scoring_profile"] == "evidence_v2"
    assert payload["rankings"][0]["signals"]["test_failure"] == 1.0
    assert payload["rankings"][0]["signals"]["contribution_test_failure"] > 0.0
    assert payload["rankings"][0]["signals"]["dynamic_test_evidence"] == 1.0
    assert payload["rankings"][0]["signals"]["test_coverage"] == 1.0
    assert payload["public_api_evidence"]["trigger_expression"] == "pick([1])"
    assert payload["overlay_case_context"]["function_name"] == "pick"
    assert "Repository Test Fault Localization" in markdown
    assert "FinalScore Contribution Decomposition" in markdown
    assert "Git Change History Evidence" in markdown
    assert "Public API Evidence" in markdown
    assert "direct_function: pick([1]) -> pick" in markdown
    assert "pick" in markdown
    assert Path(paths["repository_test_fault_localization_json"]).exists()
    assert Path(paths["repository_test_fault_localization_markdown"]).exists()


def test_dynamic_evidence_to_test_summary_records_unmatched_nodeids(tmp_path):
    _write_fault_localization_repo(tmp_path)
    parsed = RepoParser().parse(tmp_path)
    graph = build_program_graph(
        parsed,
        build_call_graph(parsed.functions, parsed.calls, parsed.imports),
    )
    summary, metadata = dynamic_evidence_to_test_summary(
        graph,
        {
            **_dynamic_evidence(),
            "failing_tests": [
                {
                    "nodeid": "tests/test_missing.py::test_missing",
                    "path": "tests/test_missing.py",
                    "test_name": "test_missing",
                    "source_line": "FAILED tests/test_missing.py::test_missing",
                }
            ],
        },
    )

    assert metadata["matched_failed_test_count"] == 0
    assert metadata["unmatched_failed_test_count"] == 1
    assert "tests/test_missing.py::test_missing" in (
        summary.dynamic_evidence_unmatched_nodeids
    )
    assert summary.failed_tests == {
        "dynamic_test::tests/test_missing.py::test_missing"
    }


def test_dynamic_evidence_to_test_summary_matches_parameterized_nodeid(tmp_path):
    _write_fault_localization_repo(tmp_path)
    parsed = RepoParser().parse(tmp_path)
    graph = build_program_graph(
        parsed,
        build_call_graph(parsed.functions, parsed.calls, parsed.imports),
    )
    summary, metadata = dynamic_evidence_to_test_summary(
        graph,
        {
            **_dynamic_evidence(),
            "failing_tests": [
                {
                    "nodeid": "tests/test_service.py::test_pick_short_values[short]",
                    "path": "tests/test_service.py",
                    "test_name": "test_pick_short_values[short]",
                    "source_line": (
                        "FAILED tests/test_service.py::test_pick_short_values[short]"
                    ),
                }
            ],
        },
    )

    assert metadata["matched_failed_test_count"] == 1
    assert metadata["unmatched_failed_test_count"] == 0
    matched = metadata["matched_failing_tests"][0]
    assert matched["name"] == "test_pick_short_values"
    assert summary.dynamic_evidence_nodeids[matched["function_id"]] == (
        "tests/test_service.py::test_pick_short_values[short]"
    )


def test_dynamic_evidence_to_test_summary_matches_parameterized_nodeid_with_colons(
    tmp_path,
):
    _write_fault_localization_repo(tmp_path)
    parsed = RepoParser().parse(tmp_path)
    graph = build_program_graph(
        parsed,
        build_call_graph(parsed.functions, parsed.calls, parsed.imports),
    )
    summary, metadata = dynamic_evidence_to_test_summary(
        graph,
        {
            **_dynamic_evidence(),
            "failing_tests": [
                {
                    "nodeid": (
                        "tests/test_service.py::test_pick_short_values[pkg::short]"
                    ),
                    "path": "tests/test_service.py",
                    "test_name": "test_pick_short_values[pkg::short]",
                    "source_line": (
                        "FAILED "
                        "tests/test_service.py::test_pick_short_values[pkg::short]"
                    ),
                }
            ],
        },
    )

    assert metadata["matched_failed_test_count"] == 1
    assert metadata["unmatched_failed_test_count"] == 0
    matched = metadata["matched_failing_tests"][0]
    assert matched["name"] == "test_pick_short_values"
    assert summary.dynamic_evidence_nodeids[matched["function_id"]] == (
        "tests/test_service.py::test_pick_short_values[pkg::short]"
    )


def test_repository_test_fault_localization_uses_traceback_frame_without_nodeid(tmp_path):
    _write_fault_localization_repo(tmp_path)
    payload = build_repository_test_fault_localization(
        {
            "status": "pass",
            "evidence_level": "traceback",
            "usable_for_localization": True,
            "recommended_validation_command": "python -m pytest -q tests",
            "failure_category": "test_assertion_failure",
            "failure_signal": "AssertionError: index error",
            "diagnostic_summary": "A broad pytest command failed without nodeids.",
            "failing_test_count": 0,
            "failing_tests": [],
            "traceback_frame_count": 1,
            "traceback_frames": [
                {
                    "path": "service.py",
                    "line": 2,
                    "function_name": "pick",
                    "source_line": 'File "service.py", line 2, in pick',
                }
            ],
        },
        repository_root=tmp_path,
        top_k=3,
    )
    markdown = render_repository_test_fault_localization_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["reason"] == "localized_from_dynamic_evidence"
    assert payload["failed_test_count"] == 1
    assert payload["matched_failed_test_count"] == 0
    assert payload["matched_traceback_frame_count"] == 1
    assert payload["unmatched_traceback_frame_count"] == 0
    assert payload["ranking_count"] >= 1
    assert payload["top_function"] == "pick"
    assert payload["rankings"][0]["signals"]["traceback_hit"] == 1.0
    assert payload["rankings"][0]["signals"]["traceback"] == 1.0
    assert payload["rankings"][0]["signals"]["traceback_available"] == 1.0
    assert payload["rankings"][0]["signals"]["test_coverage"] == 1.0
    assert payload["rankings"][0]["signals"]["dynamic_test_evidence"] == 1.0
    assert "Matched Traceback Frames" in markdown
    assert "pick" in markdown


def test_repository_test_fault_localization_uses_unittest_failure_identifier(tmp_path):
    (tmp_path / "service.py").write_text(
        "def pick(values):\n"
        "    return values[1]\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_service.py").write_text(
        "import unittest\n"
        "from service import pick\n\n"
        "class TestService(unittest.TestCase):\n"
        "    def test_pick_short_values(self):\n"
        "        pick([1])\n",
        encoding="utf-8",
    )
    evidence = build_repository_test_dynamic_evidence(
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

    payload = build_repository_test_fault_localization(
        evidence,
        repository_root=tmp_path,
        top_k=3,
    )

    assert evidence["failing_tests"][0]["nodeid"] == (
        "tests/test_service.py::TestService::test_pick_short_values"
    )
    assert payload["status"] == "pass"
    assert payload["reason"] == "localized_from_dynamic_evidence"
    assert payload["matched_failed_test_count"] == 1
    assert payload["unmatched_failed_test_count"] == 0
    assert payload["top_function"] == "pick"
    assert payload["rankings"][0]["signals"]["dynamic_test_evidence"] == 1.0


def test_repository_test_fault_localization_skips_without_usable_evidence(tmp_path):
    payload = build_repository_test_fault_localization(
        {
            "evidence_level": "passing_tests",
            "usable_for_localization": False,
            "failing_test_count": 0,
        },
        repository_root=tmp_path,
    )

    assert payload["status"] == "skipped"
    assert payload["reason"] == "dynamic_evidence_not_usable"
    assert payload["ranking_count"] == 0


def _write_fault_localization_repo(root):
    (root / "service.py").write_text(
        "def pick(values):\n"
        "    return values[1]\n",
        encoding="utf-8",
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_service.py").write_text(
        "from service import pick\n\n"
        "def test_pick_short_values():\n"
        "    pick([1])\n",
        encoding="utf-8",
    )


def _dynamic_evidence():
    return {
        "status": "pass",
        "evidence_level": "failing_tests",
        "usable_for_localization": True,
        "recommended_validation_command": (
            "python -m pytest -q tests/test_service.py::test_pick_short_values"
        ),
        "failure_category": "test_assertion_failure",
        "failure_signal": "FAILED tests/test_service.py::test_pick_short_values",
        "diagnostic_summary": "IndexError from failing repository test.",
        "failing_test_count": 1,
        "public_api_evidence": {
            "trigger_scope": "direct_function",
            "internal_target": "pick",
            "public_entrypoint": "pick",
            "public_call_args": ["[1]"],
            "trigger_expression": "pick([1])",
            "call_style": "call",
            "callable_kind": "function",
            "is_nested_target": False,
            "entrypoint_differs_from_internal_target": False,
        },
        "overlay_case_context": {
            "rule_id": "possible_index_overrun",
            "function_name": "pick",
            "qualified_name": "pick",
            "callable_kind": "function",
            "relative_file_path": "service.py",
            "expected_exception": "IndexError",
        },
        "failing_tests": [
            {
                "nodeid": "tests/test_service.py::test_pick_short_values",
                "path": "tests/test_service.py",
                "test_name": "test_pick_short_values",
                "source_line": (
                    "FAILED tests/test_service.py::test_pick_short_values - IndexError"
                ),
            }
        ],
    }
