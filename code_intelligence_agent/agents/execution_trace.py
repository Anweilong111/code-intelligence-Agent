from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


AGENT_EXECUTION_TRACE_SCHEMA_VERSION = 1
AGENT_EXECUTION_STATUSES = {
    "planned",
    "executed",
    "skipped",
    "blocked",
    "failed",
    "verified",
}


def build_agent_execution_trace(summary: dict[str, Any]) -> dict[str, Any]:
    """Build an auditable action-level execution trace from controller evidence."""

    payload = _dict(summary)
    controller = _dict(payload.get("agent_controller"))
    auto_trace = [_dict(item) for item in _list(payload.get("agent_auto_trace"))]
    auto_actions = [_dict(item) for item in _list(payload.get("agent_auto_actions"))]
    execution_result = _repository_test_execution_result(payload)

    if auto_trace:
        actions = [
            _action_from_auto_trace(
                item,
                auto_actions[index] if index < len(auto_actions) else {},
                payload,
                execution_result,
            )
            for index, item in enumerate(auto_trace)
        ]
        source = "agent_auto_trace"
    else:
        actions = [_action_from_controller(payload, controller, execution_result)]
        source = "agent_controller"

    status_counts = Counter(str(item.get("execution_status") or "") for item in actions)
    action_count = len(actions)
    executed_count = sum(1 for item in actions if bool(item.get("executed", False)))
    blocked_count = _count_status(actions, "blocked")
    skipped_count = _count_status(actions, "skipped")
    failed_count = _count_status(actions, "failed")
    verified_count = _count_status(actions, "verified")
    planned_count = _count_status(actions, "planned")
    complete = bool(
        action_count
        and all(str(item.get("execution_status") or "") in AGENT_EXECUTION_STATUSES for item in actions)
    )
    return {
        "schema_version": AGENT_EXECUTION_TRACE_SCHEMA_VERSION,
        "status": "pass" if complete else "warning",
        "reason": (
            "agent_execution_trace_complete"
            if complete
            else "agent_execution_trace_incomplete"
        ),
        "source": source,
        "loop": ["observe", "plan", "act", "verify", "reflect", "replan"],
        "action_count": action_count,
        "executed_action_count": executed_count,
        "planned_action_count": planned_count,
        "skipped_action_count": skipped_count,
        "blocked_action_count": blocked_count,
        "failed_action_count": failed_count,
        "verified_action_count": verified_count,
        "status_counts": dict(status_counts),
        "can_answer_real_execution_question": complete,
        "real_execution_answer": _real_execution_answer(
            actions,
            executed_count=executed_count,
            verified_count=verified_count,
            blocked_count=blocked_count,
            skipped_count=skipped_count,
        ),
        "actions": actions,
    }


def render_agent_execution_trace_markdown(payload: dict[str, Any]) -> str:
    payload = _dict(payload)
    lines = [
        "# Agent Execution Trace",
        "",
        f"- Status: `{_markdown_cell(payload.get('status') or 'none')}`",
        f"- Reason: `{_markdown_cell(payload.get('reason') or 'none')}`",
        f"- Source: `{_markdown_cell(payload.get('source') or 'none')}`",
        f"- Actions: {_int(payload.get('action_count', 0))}",
        f"- Executed: {_int(payload.get('executed_action_count', 0))}",
        f"- Verified: {_int(payload.get('verified_action_count', 0))}",
        f"- Blocked: {_int(payload.get('blocked_action_count', 0))}",
        f"- Skipped: {_int(payload.get('skipped_action_count', 0))}",
        f"- Failed: {_int(payload.get('failed_action_count', 0))}",
        (
            "- Real Execution Answer: "
            f"{_markdown_cell(payload.get('real_execution_answer') or '')}"
        ),
        "",
        "## Status Semantics",
        "",
        "| Status | Meaning |",
        "| --- | --- |",
        "| planned | Controller selected an action, but auto execution did not run it in this invocation. |",
        "| executed | The action was dispatched to a tool or rerun path. |",
        "| verified | The action executed and produced progress or phase-goal evidence. |",
        "| skipped | The action was intentionally not run because the mode or budget disabled it. |",
        "| blocked | The action could not run without user input, environment change, or an unsupported auto path. |",
        "| failed | The action ran but verification showed no useful progress or a failing result. |",
        "",
        "## Actions",
        "",
        "| Iteration | Action | Status | Executed | Command | Returncode | Verify | Evidence | Next Action |",
        "| ---: | --- | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for item_value in _list(payload.get("actions")):
        item = _dict(item_value)
        evidence = ", ".join(
            str(value)
            for value in _list(item.get("evidence_files"))
            if str(value or "")
        )
        if len(evidence) > 180:
            evidence = evidence[:177] + "..."
        returncode = item.get("returncode")
        lines.append(
            "| "
            f"{_int(item.get('iteration', 0))} | "
            f"`{_markdown_cell(item.get('action_id') or 'none')}` | "
            f"`{_markdown_cell(item.get('execution_status') or 'none')}` | "
            f"{str(bool(item.get('executed', False))).lower()} | "
            f"`{_markdown_cell(item.get('command') or 'none')}` | "
            f"{_markdown_cell(returncode if returncode is not None else 'n/a')} | "
            f"{_markdown_cell(item.get('verify_summary') or 'none')} | "
            f"{_markdown_cell(evidence or 'none')} | "
            f"{_markdown_cell(item.get('next_action') or 'none')} |"
        )
    if not _list(payload.get("actions")):
        lines.append("| 0 | `none` | `planned` | false | `none` | n/a | none | none | none |")
    return "\n".join(lines) + "\n"


def _action_from_controller(
    summary: dict[str, Any],
    controller: dict[str, Any],
    execution_result: dict[str, Any],
) -> dict[str, Any]:
    selected = _dict(controller.get("selected_action"))
    readiness = _dict(summary.get("analysis_readiness"))
    verification = _dict(controller.get("verification"))
    replan = _dict(controller.get("replan"))
    action_id = str(selected.get("id") or "")
    command = str(
        selected.get("command")
        or readiness.get("planned_repository_test_command")
        or summary.get("planned_repository_test_command")
        or ""
    )
    executable_now = bool(selected.get("executable_now", False))
    status = "planned" if executable_now else "blocked"
    if not action_id:
        status = "skipped"
    reason = (
        "auto_controller_not_enabled"
        if status == "planned"
        else "selected_action_not_executable"
        if status == "blocked"
        else "no_selected_action"
    )
    return _action_record(
        iteration=1,
        action_id=action_id,
        phase=str(selected.get("phase") or ""),
        tool=str(selected.get("tool") or ""),
        command=command,
        execution_status=status,
        executed=False,
        executable_now=executable_now,
        reason=reason,
        input_summary=_input_summary_from_readiness(readiness, summary),
        output_summary="not_executed",
        verify_summary=str(verification.get("success_condition") or status),
        returncode=None,
        evidence_files=_evidence_files_for_action(action_id, selected, summary),
        next_action=str(
            replan.get("next_action")
            or selected.get("next_plan")
            or selected.get("id")
            or ""
        ),
        raw={
            "selected_action": selected,
            "repository_test_execution_result": execution_result,
        },
    )


def _action_from_auto_trace(
    item: dict[str, Any],
    action: dict[str, Any],
    summary: dict[str, Any],
    execution_result: dict[str, Any],
) -> dict[str, Any]:
    action_id = str(item.get("plan_selected_action") or action.get("action_id") or "")
    executed = bool(item.get("auto_executed", False))
    stop_reason = str(item.get("stop_reason") or "")
    verify_status = str(item.get("verify_status") or action.get("after_status") or "")
    verify_outcome = str(item.get("verify_outcome") or action.get("loop_verify_outcome") or "")
    verify_progress = bool(
        item.get("verify_progress", False)
        or action.get("loop_verify_progress", False)
    )
    execution_status = _execution_status_from_auto_trace(
        executed=executed,
        executable_now=bool(item.get("plan_executable_now", False)),
        stop_reason=stop_reason,
        verify_status=verify_status,
        verify_outcome=verify_outcome,
        verify_progress=verify_progress,
        goal_status=str(item.get("verify_agent_goal_readiness_status") or ""),
        repair_ready=bool(item.get("verify_repair_ready", False)),
    )
    command = str(
        action.get("command")
        or item.get("plan_command")
        or _dict(summary.get("analysis_readiness")).get("planned_repository_test_command")
        or summary.get("planned_repository_test_command")
        or ""
    )
    returncode = None
    if execution_result:
        returncode = execution_result.get("returncode")
    return _action_record(
        iteration=_int(item.get("iteration", 0)),
        action_id=action_id,
        phase=str(item.get("plan_action_phase") or action.get("phase") or ""),
        tool=str(item.get("plan_action_tool") or action.get("tool") or ""),
        command=command,
        execution_status=execution_status,
        executed=executed,
        executable_now=bool(item.get("plan_executable_now", False)),
        reason=str(item.get("plan_reason") or action.get("reason") or stop_reason),
        input_summary=(
            f"stage={item.get('observe_stage') or 'none'}; "
            f"blocker={item.get('observe_blocker') or 'none'}; "
            f"dynamic={item.get('observe_dynamic_evidence_level') or 'none'}"
        ),
        output_summary=(
            str(item.get("verify_evidence") or "")
            or f"verify_status={verify_status or 'none'}; outcome={verify_outcome or stop_reason or 'none'}"
        ),
        verify_summary=verify_outcome or verify_status or stop_reason or execution_status,
        returncode=returncode,
        evidence_files=_evidence_files_for_action(action_id, action, summary, trace_item=item),
        next_action=str(
            item.get("replan_next_action")
            or item.get("stop_recommended_next_action")
            or action.get("loop_replan_next_action")
            or _dict(summary.get("agent_auto_stop_state")).get("recommended_next_action")
            or ""
        ),
        raw={
            "auto_trace_item": item,
            "auto_action": action,
            "repository_test_execution_result": execution_result,
        },
    )


def _action_record(
    *,
    iteration: int,
    action_id: str,
    phase: str,
    tool: str,
    command: str,
    execution_status: str,
    executed: bool,
    executable_now: bool,
    reason: str,
    input_summary: str,
    output_summary: str,
    verify_summary: str,
    returncode: Any,
    evidence_files: list[str],
    next_action: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    status = execution_status if execution_status in AGENT_EXECUTION_STATUSES else "planned"
    return {
        "iteration": _int(iteration),
        "action_id": action_id,
        "phase": phase,
        "tool": tool,
        "command": command,
        "execution_status": status,
        "planned": True,
        "executed": bool(executed),
        "executable_now": bool(executable_now),
        "skipped": status == "skipped",
        "blocked": status == "blocked",
        "failed": status == "failed",
        "verified": status == "verified",
        "status_flags": {
            "planned": True,
            "executed": bool(executed),
            "skipped": status == "skipped",
            "blocked": status == "blocked",
            "failed": status == "failed",
            "verified": status == "verified",
        },
        "reason": reason,
        "input_summary": input_summary,
        "output_summary": output_summary,
        "verify_summary": verify_summary,
        "returncode": returncode,
        "evidence_files": _unique_nonempty(evidence_files),
        "next_action": next_action,
        "raw": raw,
    }


def _execution_status_from_auto_trace(
    *,
    executed: bool,
    executable_now: bool,
    stop_reason: str,
    verify_status: str,
    verify_outcome: str,
    verify_progress: bool,
    goal_status: str,
    repair_ready: bool,
) -> str:
    if not executed:
        if stop_reason in {"disabled", "max_actions_zero"}:
            return "skipped"
        if stop_reason or not executable_now:
            return "blocked"
        return "planned"
    if verify_progress or repair_ready or goal_status == "pass" or verify_outcome.startswith("phase_goal_reached:"):
        return "verified"
    if verify_status == "fail" or "no_progress" in verify_outcome or "failed" in verify_outcome:
        return "failed"
    return "executed"


def _repository_test_execution_result(summary: dict[str, Any]) -> dict[str, Any]:
    inline = _dict(summary.get("repository_test_execution_result"))
    if inline:
        return _execution_result_excerpt(inline)
    path = str(summary.get("repository_test_execution_result_json") or "")
    if not path:
        return {}
    return _execution_result_excerpt(_read_json(path))


def _execution_result_excerpt(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    return {
        "status": str(payload.get("status") or ""),
        "reason": str(payload.get("reason") or ""),
        "executed": bool(payload.get("executed", False)),
        "command": str(payload.get("command") or ""),
        "cwd": str(payload.get("cwd") or ""),
        "returncode": payload.get("returncode"),
        "timeout": bool(payload.get("timeout", False)),
        "passed": _int(payload.get("passed", 0)),
        "failed": _int(payload.get("failed", 0)),
        "errors": _int(payload.get("errors", 0)),
        "failure_category": str(payload.get("failure_category") or ""),
    }


def _evidence_files_for_action(
    action_id: str,
    action: dict[str, Any],
    summary: dict[str, Any],
    *,
    trace_item: dict[str, Any] | None = None,
) -> list[str]:
    trace_item = _dict(trace_item)
    tool = str(action.get("tool") or trace_item.get("plan_action_tool") or "")
    candidates = [
        trace_item.get("pre_action_controller_json"),
        trace_item.get("pre_action_intelligence_json"),
        summary.get("agent_controller_json"),
        summary.get("agent_policy_trace_json"),
        summary.get("agent_decision_timeline_json"),
    ]
    if "repository_test_execution" in tool or "run_repository_tests" in action_id:
        candidates.extend(
            [
                summary.get("repository_test_execution_plan_json"),
                summary.get("repository_test_execution_result_json"),
                summary.get("repository_test_dynamic_evidence_json"),
            ]
        )
    if "patch_candidates" in tool or "patch_candidates" in action_id:
        candidates.append(summary.get("repository_test_patch_candidates_json"))
    if "patch_validation" in tool or "patch_validation" in action_id or "reflection" in action_id:
        candidates.extend(
            [
                summary.get("repository_test_patch_validation_json"),
                summary.get("reflection_trace_json"),
            ]
        )
    if "environment" in tool or "environment" in action_id:
        candidates.extend(
            [
                summary.get("repository_test_environment_json"),
                summary.get("repository_test_environment_repair_plan_json"),
            ]
        )
    if "phase4" in action_id:
        candidates.extend(
            [
                summary.get("phase4_search_evaluation_json"),
                summary.get("phase4_search_evaluation_execution_json"),
            ]
        )
    return _unique_nonempty([str(item or "") for item in candidates])


def _input_summary_from_readiness(readiness: dict[str, Any], summary: dict[str, Any]) -> str:
    fault = _dict(summary.get("fault_localization"))
    return (
        f"stage={readiness.get('current_stage') or 'none'}; "
        f"blocker={readiness.get('blocker') or 'none'}; "
        f"dynamic={readiness.get('dynamic_evidence_level') or 'none'}; "
        f"fault={fault.get('mode') or 'none'}/{fault.get('status') or 'none'}"
    )


def _real_execution_answer(
    actions: list[dict[str, Any]],
    *,
    executed_count: int,
    verified_count: int,
    blocked_count: int,
    skipped_count: int,
) -> str:
    if not actions:
        return "No Agent action was selected, so there is no execution evidence."
    if executed_count:
        return (
            f"{executed_count} action(s) were actually executed; "
            f"{verified_count} reached verified progress. Non-executed actions are "
            "explicitly marked as blocked or skipped."
        )
    if blocked_count:
        return (
            "No auto action was executed in this invocation; the selected action "
            "is blocked and requires user input, environment changes, or an "
            "unsupported auto path."
        )
    if skipped_count:
        return (
            "No auto action was executed because the current execution mode or "
            "budget skipped the action."
        )
    return (
        "No auto action was executed in this invocation; the selected action is "
        "recorded as planned."
    )


def _count_status(actions: list[dict[str, Any]], status: str) -> int:
    return sum(1 for item in actions if str(item.get("execution_status") or "") == status)


def _read_json(path: str) -> dict[str, Any]:
    try:
        return _dict(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _unique_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value or "")
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _markdown_cell(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", " ")
