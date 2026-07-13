from __future__ import annotations

import json

from code_intelligence_agent.agents.execution_trace import (
    build_agent_execution_trace,
    render_agent_execution_trace_markdown,
)


def test_execution_trace_records_controller_planned_action():
    trace = build_agent_execution_trace(
        {
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "blocker": "checkout:full_repo_not_materialized",
                "dynamic_evidence_level": "none",
            },
            "fault_localization": {"mode": "static_fallback", "status": "pass"},
            "agent_controller": {
                "selected_action": {
                    "id": "run_repository_tests_with_checkout",
                    "phase": "phase3",
                    "tool": "repository_test_execution_result",
                    "command": "python -m pytest",
                    "executable_now": True,
                    "reason": "Collect dynamic evidence.",
                },
                "verification": {
                    "success_condition": "repository test result is recorded"
                },
                "replan": {"next_action": "localize_fault_with_dynamic_evidence"},
            },
        }
    )

    assert trace["status"] == "pass"
    assert trace["source"] == "agent_controller"
    assert trace["action_count"] == 1
    assert trace["executed_action_count"] == 0
    assert trace["planned_action_count"] == 1
    assert trace["can_answer_real_execution_question"] is True
    assert trace["actions"][0]["execution_status"] == "planned"
    assert trace["actions"][0]["executed"] is False
    assert trace["actions"][0]["command"] == "python -m pytest"
    assert "No auto action was executed" in trace["real_execution_answer"]


def test_execution_trace_marks_auto_action_verified_with_returncode(tmp_path):
    result_path = tmp_path / "repository_test_execution_result.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "pass",
                "executed": True,
                "command": "python -m pytest tests",
                "returncode": 0,
                "passed": 3,
                "failed": 0,
            }
        ),
        encoding="utf-8",
    )

    trace = build_agent_execution_trace(
        {
            "repository_test_execution_result_json": str(result_path),
            "repository_test_execution_plan_json": str(
                tmp_path / "repository_test_execution_plan.json"
            ),
            "repository_test_dynamic_evidence_json": str(
                tmp_path / "repository_test_dynamic_evidence.json"
            ),
            "agent_auto_trace": [
                {
                    "iteration": 1,
                    "auto_executed": True,
                    "observe_stage": "phase3_repository_test_execution",
                    "observe_blocker": "dynamic_evidence_missing",
                    "observe_dynamic_evidence_level": "none",
                    "plan_selected_action": "run_repository_tests_with_checkout",
                    "plan_action_phase": "phase3",
                    "plan_action_tool": "repository_test_execution_result",
                    "plan_command": "python -m pytest tests",
                    "plan_executable_now": True,
                    "plan_reason": "Run tests to collect failure evidence.",
                    "verify_status": "pass",
                    "verify_outcome": "dynamic_evidence_collected",
                    "verify_progress": True,
                    "verify_agent_goal_readiness_status": "warning",
                    "reflect_status": "complete",
                    "reflect_strategy": "use_traceback_for_fault_localization",
                    "replan_policy": "continue_to_dynamic_localization",
                    "replan_next_action": "localize_fault_with_dynamic_evidence",
                }
            ],
            "agent_auto_actions": [
                {
                    "action_id": "run_repository_tests_with_checkout",
                    "command": "python -m pytest tests",
                    "loop_verify_progress": True,
                    "loop_verify_outcome": "dynamic_evidence_collected",
                }
            ],
        }
    )

    action = trace["actions"][0]
    assert trace["source"] == "agent_auto_trace"
    assert trace["executed_action_count"] == 1
    assert trace["verified_action_count"] == 1
    assert action["execution_status"] == "verified"
    assert action["returncode"] == 0
    assert action["verified"] is True
    assert "repository_test_execution_result.json" in action["evidence_files"][1]
    assert "1 action(s) were actually executed" in trace["real_execution_answer"]

    markdown = render_agent_execution_trace_markdown(trace)
    assert "Agent Execution Trace" in markdown
    assert "run_repository_tests_with_checkout" in markdown
    assert "verified" in markdown


def test_execution_trace_marks_stopped_action_blocked():
    trace = build_agent_execution_trace(
        {
            "agent_auto_trace": [
                {
                    "iteration": 1,
                    "auto_executed": False,
                    "observe_stage": "phase3_repository_test_execution",
                    "observe_blocker": "checkout:full_repo_not_materialized",
                    "plan_selected_action": "run_repository_tests_with_checkout",
                    "plan_action_phase": "phase3",
                    "plan_action_tool": "repository_test_execution_result",
                    "plan_executable_now": False,
                    "plan_reason": "Full repository checkout is required.",
                    "stop_reason": "selected_action_not_executable",
                    "stop_category": "manual_or_blocked",
                    "stop_recommended_next_action": "provide_repository_checkout",
                }
            ]
        }
    )

    action = trace["actions"][0]
    assert trace["blocked_action_count"] == 1
    assert trace["executed_action_count"] == 0
    assert action["execution_status"] == "blocked"
    assert action["blocked"] is True
    assert action["next_action"] == "provide_repository_checkout"
    assert "requires user input" in trace["real_execution_answer"]
