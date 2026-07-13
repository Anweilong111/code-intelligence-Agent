from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from code_intelligence_agent.agents.llm_client import (
    LLMClient,
    LLMRequestError,
    create_replan_client,
    llm_config_audit,
)
from code_intelligence_agent.agents.action_registry import (
    action_execution_policy,
    action_spec_for,
    build_agent_action_registry,
    build_agent_policy_trace,
    canonical_action_id,
    render_agent_action_registry_markdown,
    render_agent_policy_trace_markdown,
    validate_action_arguments,
)


AGENT_LOOP = ["observe", "plan", "act", "verify", "reflect", "replan"]
ACTION_DECISION_REQUIRED_FIELDS = [
    "why_selected",
    "confidence",
    "risk",
    "input",
    "output",
    "blocker",
    "next_plan",
]


def build_agent_controller_plan(
    intelligence_summary: dict[str, Any],
    *,
    llm_replan_client: LLMClient | None = None,
    enable_llm_replan: bool | None = None,
    planner_mode: str | None = None,
) -> dict[str, Any]:
    readiness = _dict(intelligence_summary.get("analysis_readiness"))
    fault = _dict(intelligence_summary.get("fault_localization"))
    rule_selected_action = _select_action(intelligence_summary, readiness, fault)
    observations = _observations(intelligence_summary, readiness, fault)
    cycle = _controller_cycle(
        summary=intelligence_summary,
        readiness=readiness,
        fault=fault,
        selected_action=rule_selected_action,
        observations=observations,
    )
    rule_selected_action = cycle["selected_action"]
    strategy = _planner_strategy(
        intelligence_summary,
        client=llm_replan_client,
        enabled=enable_llm_replan,
        explicit=planner_mode,
    )
    budget = _planner_budget_context(intelligence_summary)
    llm_replan_advisor = _llm_replan_advisor(
        intelligence_summary,
        readiness=readiness,
        fault=fault,
        selected_action=rule_selected_action,
        observations=observations,
        verification=cycle["verification"],
        reflection=cycle["reflection"],
        replan=cycle["replan"],
        termination=cycle["termination"],
        client=llm_replan_client,
        enabled=enable_llm_replan,
        planner_mode=strategy,
        budget_context=budget,
    )
    selected_action, planner_resolution = _resolve_planner_selection(
        intelligence_summary,
        rule_selected_action=rule_selected_action,
        advisor=llm_replan_advisor,
        planner_mode=strategy,
    )
    if selected_action != rule_selected_action:
        cycle = _controller_cycle(
            summary=intelligence_summary,
            readiness=readiness,
            fault=fault,
            selected_action=selected_action,
            observations=observations,
        )
        selected_action = cycle["selected_action"]
    plan = cycle["plan"]
    verification = cycle["verification"]
    reflection = cycle["reflection"]
    replan = cycle["replan"]
    termination = cycle["termination"]
    auto_controller = _auto_controller_summary(intelligence_summary)
    llm_repair_action_audit = _llm_repair_action_audit(
        intelligence_summary,
        readiness=readiness,
        selected_action=selected_action,
    )
    action_registry = build_agent_action_registry()
    policy_trace = build_agent_policy_trace(
        observations=observations,
        selected_action=selected_action,
        verification=verification,
        reflection=reflection,
        replan=replan,
        termination=termination,
        llm_repair_action_audit=llm_repair_action_audit,
        action_registry=action_registry,
    )
    policy_trace["planner_strategy"] = strategy
    policy_trace["planner_budget"] = budget
    policy_trace["planner_resolution"] = planner_resolution
    action_decision_audit = _action_decision_audit(
        intelligence_summary,
        readiness=readiness,
        fault=fault,
        selected_action=selected_action,
        observations=observations,
        verification=verification,
        reflection=reflection,
        replan=replan,
        termination=termination,
    )
    loop_iteration_audit = _loop_iteration_audit(
        intelligence_summary,
        readiness=readiness,
        fault=fault,
        selected_action=selected_action,
        verification=verification,
        reflection=reflection,
        replan=replan,
        termination=termination,
    )
    planner_trace = _planner_trace(
        observations=observations,
        advisor=llm_replan_advisor,
        resolution=planner_resolution,
        selected_action=selected_action,
    )
    planner_metrics = _planner_run_metrics(
        advisor=llm_replan_advisor,
        resolution=planner_resolution,
    )
    planner_state_fingerprint = _planner_state_fingerprint(intelligence_summary)
    return {
        "agent_type": "code_intelligence_controller",
        "control_loop": AGENT_LOOP,
        "objective": "analyze_localize_and_repair_repository",
        "status": _controller_status(selected_action),
        "current_stage": str(readiness.get("current_stage") or ""),
        "next_stage": str(readiness.get("next_stage") or ""),
        "primary_blocker": str(readiness.get("blocker") or ""),
        "selected_action": selected_action,
        "rule_selected_action": rule_selected_action,
        "planner_strategy": strategy,
        "planner_budget": budget,
        "planner_resolution": planner_resolution,
        "planner_trace": planner_trace,
        "planner_metrics": planner_metrics,
        "planner_state_fingerprint": planner_state_fingerprint,
        "observations": observations,
        "plan": plan,
        "verification": verification,
        "reflection": reflection,
        "replan": replan,
        "termination": termination,
        "auto_controller": auto_controller,
        "llm_repair_action_audit": llm_repair_action_audit,
        "llm_replan_advisor": llm_replan_advisor,
        "action_decision_audit": action_decision_audit,
        "action_registry": action_registry,
        "policy_trace": policy_trace,
        "loop_iteration_audit": loop_iteration_audit,
        "auto_actions": _list(intelligence_summary.get("agent_auto_actions")),
        "auto_trace": _list(intelligence_summary.get("agent_auto_trace")),
        "decision_trace": _decision_trace(
            observations=observations,
            selected_action=selected_action,
            verification=verification,
            reflection=reflection,
            replan=replan,
            termination=termination,
        ),
    }


def _controller_cycle(
    *,
    summary: dict[str, Any],
    readiness: dict[str, Any],
    fault: dict[str, Any],
    selected_action: dict[str, Any],
    observations: list[dict[str, str]],
) -> dict[str, Any]:
    plan = _plan_steps(selected_action, readiness, fault)
    verification = _verification(selected_action, readiness, fault)
    reflection = _reflection(selected_action, readiness, fault)
    replan = _replan(
        selected_action,
        readiness,
        reflection,
        verification,
        _dict(summary.get("agent_goal_readiness")),
    )
    termination = _termination(selected_action, readiness)
    selected_action = _enrich_selected_action_decision(
        summary=summary,
        readiness=readiness,
        fault=fault,
        selected_action=selected_action,
        observations=observations,
        verification=verification,
        reflection=reflection,
        replan=replan,
        termination=termination,
    )
    return {
        "selected_action": selected_action,
        "plan": plan,
        "verification": verification,
        "reflection": reflection,
        "replan": replan,
        "termination": termination,
    }


def render_agent_controller_markdown(payload: dict[str, Any]) -> str:
    selected = _dict(payload.get("selected_action"))
    rule_selected = _dict(payload.get("rule_selected_action"))
    planner_resolution = _dict(payload.get("planner_resolution"))
    planner_budget = _dict(payload.get("planner_budget"))
    planner_metrics = _dict(payload.get("planner_metrics"))
    lines = [
        "# GitHub Repo Agent Controller",
        "",
        f"- Agent Type: `{_markdown_cell(payload.get('agent_type'))}`",
        f"- Status: `{_markdown_cell(payload.get('status'))}`",
        f"- Objective: `{_markdown_cell(payload.get('objective'))}`",
        (
            "- Loop: "
            f"{_markdown_cell(' -> '.join(str(item) for item in _list(payload.get('control_loop'))))}"
        ),
        f"- Current Stage: `{_markdown_cell(payload.get('current_stage'))}`",
        f"- Next Stage: `{_markdown_cell(payload.get('next_stage'))}`",
        f"- Primary Blocker: `{_markdown_cell(payload.get('primary_blocker'))}`",
        "",
        "## Planner Resolution",
        "",
        f"- Strategy: `{_markdown_cell(payload.get('planner_strategy') or 'rule')}`",
        f"- Rule Action: `{_markdown_cell(rule_selected.get('id') or 'none')}`",
        f"- LLM Proposed Action: `{_markdown_cell(planner_resolution.get('llm_proposed_action') or 'none')}`",
        f"- Adopted Action: `{_markdown_cell(planner_resolution.get('adopted_action') or selected.get('id') or 'none')}`",
        f"- Adopted Source: `{_markdown_cell(planner_resolution.get('adopted_source') or 'rule')}`",
        f"- Resolution Reason: `{_markdown_cell(planner_resolution.get('resolution_reason') or 'none')}`",
        f"- Remaining Actions: `{_markdown_cell(planner_budget.get('remaining_actions'))}`",
        f"- Remaining Time Seconds: `{_markdown_cell(planner_budget.get('remaining_time_seconds'))}`",
        f"- Remaining LLM Cost USD: `{_markdown_cell(planner_budget.get('remaining_llm_cost_usd'))}`",
        f"- Safety Rejections: {_int(planner_metrics.get('safety_gate_rejection_count', 0))}",
        f"- Planner Fallbacks: {_int(planner_metrics.get('fallback_count', 0))}",
        "",
        "## Selected Action",
        "",
        f"- ID: `{_markdown_cell(selected.get('id'))}`",
        f"- Phase: `{_markdown_cell(selected.get('phase'))}`",
        f"- Tool: `{_markdown_cell(selected.get('tool'))}`",
        f"- Risk: `{_markdown_cell(selected.get('risk'))}`",
        f"- Confidence: `{_markdown_cell(selected.get('confidence'))}`",
        f"- Confidence Reason: {_markdown_cell(selected.get('confidence_reason'))}",
        f"- Executable Now: {str(bool(selected.get('executable_now', False))).lower()}",
        f"- Reason: {_markdown_cell(selected.get('reason'))}",
        f"- Input: {_markdown_cell(selected.get('input_summary'))}",
        f"- Expected Output: `{_markdown_cell(selected.get('expected_output'))}`",
        f"- Blocker: `{_markdown_cell(selected.get('blocker') or 'none')}`",
        f"- Next Plan: {_markdown_cell(selected.get('next_plan'))}",
        f"- Command: `{_markdown_cell(selected.get('command'))}`",
        "",
        "## LLM Repair Action Audit",
        "",
    ]
    llm_repair_audit = _dict(payload.get("llm_repair_action_audit"))
    loop = _dict(llm_repair_audit.get("agent_loop_evidence"))
    lines.extend(
        [
            f"- Status: `{_markdown_cell(llm_repair_audit.get('status'))}`",
            f"- Reason: `{_markdown_cell(llm_repair_audit.get('reason'))}`",
            f"- Repair Action: `{_markdown_cell(llm_repair_audit.get('repair_action_id'))}`",
            f"- Reflection Action: `{_markdown_cell(llm_repair_audit.get('reflection_action_id'))}`",
            f"- Blocker: `{_markdown_cell(llm_repair_audit.get('blocker') or 'none')}`",
            f"- Provider/Model: `{_markdown_cell(llm_repair_audit.get('provider'))}` / `{_markdown_cell(llm_repair_audit.get('model'))}`",
            f"- API Key Present: {str(bool(llm_repair_audit.get('api_key_present', False))).lower()}",
            f"- Sandbox Authority: `{_markdown_cell(llm_repair_audit.get('sandbox_authority') or 'sandbox_pytest_decides_success')}`",
            "",
            "| Loop Step | Evidence |",
            "| --- | --- |",
        ]
    )
    for step in AGENT_LOOP:
        lines.append(
            "| "
            f"{_markdown_cell(step)} | "
            f"{_markdown_cell(loop.get(step) or 'none')} |"
        )
    llm_replan = _dict(payload.get("llm_replan_advisor"))
    llm_replan_config = _dict(llm_replan.get("config"))
    llm_replan_advice = _dict(llm_replan.get("advice"))
    llm_planner_decision = _dict(llm_replan.get("planner_decision"))
    llm_planner_gate = _dict(llm_replan.get("safety_gate"))
    lines.extend(
        [
            "",
            "## LLM Replan Advisor",
            "",
            f"- Status: `{_markdown_cell(llm_replan.get('status') or 'none')}`",
            f"- Reason: `{_markdown_cell(llm_replan.get('reason') or 'none')}`",
            f"- Authority: `{_markdown_cell(llm_replan.get('authority') or 'advisory_only_controller_policy_decides')}`",
            f"- Provider/Model: `{_markdown_cell(llm_replan_config.get('provider') or 'none')}` / `{_markdown_cell(llm_replan_config.get('model') or 'none')}`",
            f"- API Key Present: {str(bool(llm_replan_config.get('api_key_present', False))).lower()}",
            f"- Recommended Action: `{_markdown_cell(llm_replan_advice.get('recommended_action') or 'none')}`",
            f"- Planner Selected Action: `{_markdown_cell(llm_planner_decision.get('selected_action') or 'none')}`",
            f"- Proposal Source: `{_markdown_cell(llm_planner_decision.get('proposal_source') or 'none')}`",
            f"- Confidence: `{_markdown_cell(llm_planner_decision.get('confidence') if llm_planner_decision else 'none')}`",
            f"- Risk: `{_markdown_cell(llm_planner_decision.get('risk') or 'none')}`",
            f"- Blocker: `{_markdown_cell(llm_replan_advice.get('blocker') or llm_replan.get('blocker') or 'none')}`",
            f"- Required Evidence: `{_markdown_cell(', '.join(str(item) for item in _list(llm_planner_decision.get('required_evidence'))) or 'none')}`",
            f"- Memory Used: `{_markdown_cell(', '.join(str(item) for item in _list(llm_planner_decision.get('memory_used'))) or 'none')}`",
            f"- Next Plan: {_markdown_cell(llm_planner_decision.get('next_plan') or 'none')}",
            f"- Fallback To Rule Planner: {str(bool(llm_replan.get('fallback_to_rule_planner', False))).lower()}",
            f"- Safety Gate: `{_markdown_cell(llm_planner_gate.get('status') or 'none')}` / `{_markdown_cell(llm_planner_gate.get('reason') or 'none')}`",
            f"- Controller Action Match: {str(bool(llm_planner_gate.get('controller_action_match', False))).lower()}",
            f"- Override Requested/Allowed: {str(bool(llm_planner_gate.get('override_requested', False))).lower()} / {str(bool(llm_planner_gate.get('override_allowed', False))).lower()}",
        ]
    )
    lines.extend(
        [
            "",
        "## Observations",
        "",
        "| Signal | Value |",
        "| --- | --- |",
        ]
    )
    for item_value in _list(payload.get("observations")):
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('signal'))} | "
            f"{_markdown_cell(item.get('value'))} |"
        )
    if not _list(payload.get("observations")):
        lines.append("| none | none |")
    lines.extend(["", "## Plan", "", "| Step | Mode | Action | Tool | Expected Outcome |"])
    lines.append("| ---: | --- | --- | --- | --- |")
    for item_value in _list(payload.get("plan")):
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_int(item.get('step', 0))} | "
            f"{_markdown_cell(item.get('mode'))} | "
            f"{_markdown_cell(item.get('action'))} | "
            f"{_markdown_cell(item.get('tool'))} | "
            f"{_markdown_cell(item.get('expected_outcome'))} |"
        )
    if not _list(payload.get("plan")):
        lines.append("| 0 | none | none | none | none |")
    action_audit = _dict(payload.get("action_decision_audit"))
    lines.extend(
        [
            "",
            "## Action Decision Audit",
            "",
            f"- Status: `{_markdown_cell(action_audit.get('status') or 'none')}`",
            f"- Reason: `{_markdown_cell(action_audit.get('reason') or 'none')}`",
            f"- Actions: {_int(action_audit.get('complete_action_count', 0))}/"
            f"{_int(action_audit.get('action_count', 0))} complete",
            f"- Source: `{_markdown_cell(action_audit.get('source') or 'none')}`",
            "",
            "| Iteration | Action | Why Selected | Confidence | Risk | Input | Output | Blocker | Next Plan |",
            "| ---: | --- | --- | ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for item_value in _list(action_audit.get("actions")):
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_int(item.get('iteration', 0))} | "
            f"{_markdown_cell(item.get('action_id'))} | "
            f"{_markdown_cell(item.get('why_selected'))} | "
            f"{_float(item.get('confidence', 0.0)):.2f} | "
            f"{_markdown_cell(item.get('risk'))} | "
            f"{_markdown_cell(item.get('input'))} | "
            f"{_markdown_cell(item.get('output'))} | "
            f"{_markdown_cell(item.get('blocker') or 'none')} | "
            f"{_markdown_cell(item.get('next_plan'))} |"
        )
    if not _list(action_audit.get("actions")):
        lines.append("| 0 | none | none | 0.00 | none | none | none | none | none |")
    auto_controller = _dict(payload.get("auto_controller"))
    lines.extend(
        [
            "",
            "## Auto Controller",
            "",
            f"- Enabled: {str(bool(auto_controller.get('enabled', False))).lower()}",
            f"- Max Actions: {_int(auto_controller.get('max_actions', 0))}",
            f"- Executed Actions: {_int(auto_controller.get('action_count', 0))}",
            f"- Progressed Actions: {_int(auto_controller.get('progress_count', 0))}",
            f"- No Progress Actions: {_int(auto_controller.get('no_progress_count', 0))}",
            f"- Complete Loop Recorded: {str(bool(auto_controller.get('complete_loop_recorded', False))).lower()}",
            f"- Verify Outcomes: {_markdown_cell(_format_counts(_dict(auto_controller.get('verify_outcome_counts'))))}",
            f"- Replan Policies: {_markdown_cell(_format_counts(_dict(auto_controller.get('replan_policy_counts'))))}",
            f"- Goal Readiness Statuses: {_markdown_cell(_format_counts(_dict(auto_controller.get('goal_readiness_status_counts'))))}",
            f"- Goal Readiness Passed Actions: {_int(auto_controller.get('goal_readiness_passed_action_count', 0))}",
            f"- Final Goal Readiness: `{_markdown_cell(auto_controller.get('final_goal_readiness_status') or 'none')}`",
            f"- Stop Reason: `{_markdown_cell(auto_controller.get('stop_reason') or 'none')}`",
            f"- Stop Category: `{_markdown_cell(auto_controller.get('stop_category') or 'none')}`",
            f"- Stop Action: `{_markdown_cell(auto_controller.get('stop_action_id') or 'none')}`",
            f"- Stop Recovery Policy: `{_markdown_cell(auto_controller.get('stop_recovery_policy') or 'none')}`",
            f"- Requires User Action: {str(bool(auto_controller.get('stop_requires_user_action', False))).lower()}",
            f"- Requires Environment Change: {str(bool(auto_controller.get('stop_requires_environment_change', False))).lower()}",
            f"- External Input Kind: `{_markdown_cell(auto_controller.get('stop_external_input_kind') or 'none')}`",
            f"- Recommended Next Action: {_markdown_cell(auto_controller.get('stop_recommended_next_action') or 'none')}",
            f"- Recommended Next Command: `{_markdown_cell(auto_controller.get('stop_recommended_next_command') or 'none')}`",
            "",
            "## Action Registry / Policy Trace",
            "",
            f"- Action Registry: `{_markdown_cell(_dict(payload.get('action_registry')).get('status') or 'none')}`",
            f"- Policy Trace: `{_markdown_cell(_dict(payload.get('policy_trace')).get('status') or 'none')}`",
            f"- Canonical Action: `{_markdown_cell(_dict(payload.get('policy_trace')).get('canonical_action_id') or 'none')}`",
            f"- Policy Rule: `{_markdown_cell(_dict(payload.get('policy_trace')).get('policy_rule') or 'none')}`",
            "",
            "### Auto Trace",
            "",
            "| Iteration | Stage | Blocker | Selected Action | Executed | Verify Outcome | Progress | Replan Policy | Verify Dynamic | Failure Overlay | Regression Guard | Environment Repair | Timeout Narrowing | Stop Category | Recovery Policy | Stop Reason |",
            "| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    auto_trace = _list(payload.get("auto_trace"))
    for item_value in auto_trace:
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_int(item.get('iteration', 0))} | "
            f"{_markdown_cell(item.get('observe_stage'))} | "
            f"{_markdown_cell(item.get('observe_blocker'))} | "
            f"{_markdown_cell(item.get('plan_selected_action'))} | "
            f"{str(bool(item.get('auto_executed', False))).lower()} | "
            f"{_markdown_cell(item.get('verify_outcome') or item.get('verify_stage') or 'none')} | "
            f"{str(bool(item.get('verify_progress', False))).lower()} | "
            f"{_markdown_cell(item.get('replan_policy') or 'none')} | "
            f"{_markdown_cell(item.get('verify_dynamic_evidence_level') or 'none')} | "
            f"{_markdown_cell(_auto_trace_failure_overlay_cell(item))} | "
            f"{_markdown_cell(item.get('verify_regression_guard_status') or 'none')} | "
            f"{_markdown_cell(item.get('verify_environment_repair_plan_status') or 'none')} | "
            f"{_markdown_cell(_auto_trace_timeout_narrowing_cell(item))} | "
            f"{_markdown_cell(item.get('stop_category') or 'none')} | "
            f"{_markdown_cell(item.get('stop_recovery_policy') or 'none')} | "
            f"{_markdown_cell(item.get('stop_reason') or 'continue')} |"
        )
    if not auto_trace:
        lines.append("| 0 | none | none | none | false | none | false | none | none | none | none | none | none | none | none | none |")
    auto_actions = _list(payload.get("auto_actions"))
    if auto_actions:
        lines.extend(
            [
                "",
                "### Auto Actions",
                "",
                "| Action | Before Blocker | Verify Outcome | Replan Policy | After Dynamic | Failure Overlay | Regression Guard | Environment Repair | Timeout Narrowing | Patch Validation | Repair Ready |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item_value in auto_actions:
            item = _dict(item_value)
            patch_cell = (
                f"{item.get('after_patch_validation_status') or 'none'}"
                f"({_int(item.get('after_patch_validation_success_count', 0))} success)"
            )
            guard_cell = str(item.get("after_regression_guard_status") or "none")
            environment_repair_cell = str(
                item.get("after_environment_repair_plan_status") or "none"
            )
            lines.append(
                "| "
                f"{_markdown_cell(item.get('action_id'))} | "
                f"{_markdown_cell(item.get('before_blocker'))} | "
                f"{_markdown_cell(item.get('loop_verify_outcome') or 'none')} | "
                f"{_markdown_cell(item.get('loop_replan_policy') or 'none')} | "
                f"{_markdown_cell(item.get('after_dynamic_evidence_level'))} | "
                f"{_markdown_cell(_auto_action_failure_overlay_cell(item))} | "
                f"{_markdown_cell(guard_cell)} | "
                f"{_markdown_cell(environment_repair_cell)} | "
                f"{_markdown_cell(_auto_action_timeout_narrowing_cell(item))} | "
                f"{_markdown_cell(patch_cell)} | "
                f"{str(bool(item.get('after_repair_ready', False))).lower()} |"
            )
    loop_audit = _dict(payload.get("loop_iteration_audit"))
    lines.extend(
        [
            "",
            "## Loop Iteration Audit",
            "",
            f"- Status: `{_markdown_cell(loop_audit.get('status') or 'none')}`",
            f"- Source: `{_markdown_cell(loop_audit.get('source') or 'none')}`",
            f"- Complete Iterations: {_int(loop_audit.get('complete_iteration_count', 0))}/{_int(loop_audit.get('iteration_count', 0))}",
            f"- Executed Iterations: {_int(loop_audit.get('executed_iteration_count', 0))}",
            f"- Stopped Iterations: {_int(loop_audit.get('stopped_iteration_count', 0))}",
            "",
            "| Iteration | Observe | Plan | Act | Verify | Reflect | Replan | Complete |",
            "| ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item_value in _list(loop_audit.get("iterations")):
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_int(item.get('iteration', 0))} | "
            f"{_markdown_cell(item.get('observe'))} | "
            f"{_markdown_cell(item.get('plan'))} | "
            f"{_markdown_cell(item.get('act'))} | "
            f"{_markdown_cell(item.get('verify'))} | "
            f"{_markdown_cell(item.get('reflect'))} | "
            f"{_markdown_cell(item.get('replan'))} | "
            f"{str(bool(item.get('complete', False))).lower()} |"
        )
    if not _list(loop_audit.get("iterations")):
        lines.append("| 0 | none | none | none | none | none | none | false |")
    lines.extend(
        [
            "",
            "## Decision Trace",
            "",
            "| Phase | Status | Evidence | Decision | Output |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for item_value in _list(payload.get("decision_trace")):
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('phase'))} | "
            f"{_markdown_cell(item.get('status'))} | "
            f"{_markdown_cell(item.get('evidence'))} | "
            f"{_markdown_cell(item.get('decision'))} | "
            f"{_markdown_cell(item.get('output'))} |"
        )
    if not _list(payload.get("decision_trace")):
        lines.append("| none | none | none | none | none |")
    verification = _dict(payload.get("verification"))
    reflection = _dict(payload.get("reflection"))
    replan = _dict(payload.get("replan"))
    termination = _dict(payload.get("termination"))
    lines.extend(
        [
            "",
            "## Verification",
            "",
            f"- Status: `{_markdown_cell(verification.get('status'))}`",
            f"- Evidence: {_markdown_cell(verification.get('evidence'))}",
            f"- Success Condition: {_markdown_cell(verification.get('success_condition'))}",
            f"- Expected Artifact: `{_markdown_cell(verification.get('expected_artifact'))}`",
            "",
            "## Reflection",
            "",
            f"- Status: `{_markdown_cell(reflection.get('status') or 'ready')}`",
            f"- Failure Hypothesis: {_markdown_cell(reflection.get('failure_hypothesis'))}",
            f"- Replan Trigger: `{_markdown_cell(reflection.get('replan_trigger'))}`",
            f"- Fallback Action: `{_markdown_cell(reflection.get('fallback_action'))}`",
            "",
            "## Replan",
            "",
            f"- Status: `{_markdown_cell(replan.get('status'))}`",
            f"- Trigger: `{_markdown_cell(replan.get('trigger'))}`",
            f"- Next Policy: `{_markdown_cell(replan.get('next_policy'))}`",
            f"- Fallback Action: `{_markdown_cell(replan.get('fallback_action'))}`",
            "",
            "## Termination",
            "",
            f"- Status: `{_markdown_cell(termination.get('status'))}`",
            f"- Reason: {_markdown_cell(termination.get('reason'))}",
            f"- Next Action: {_markdown_cell(termination.get('next_action'))}",
        ]
    )
    return "\n".join(lines) + "\n"


def write_agent_controller_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "github_repo_agent_controller.json"
    markdown_path = root / "github_repo_agent_controller.md"
    registry_path = root / "agent_action_registry.json"
    registry_markdown_path = root / "agent_action_registry.md"
    policy_trace_path = root / "agent_policy_trace.json"
    policy_trace_markdown_path = root / "agent_policy_trace.md"
    registry_payload = _dict(payload.get("action_registry")) or build_agent_action_registry()
    policy_trace_payload = _dict(payload.get("policy_trace"))
    if not policy_trace_payload:
        policy_trace_payload = build_agent_policy_trace(
            observations=[_dict(item) for item in _list(payload.get("observations"))],
            selected_action=_dict(payload.get("selected_action")),
            verification=_dict(payload.get("verification")),
            reflection=_dict(payload.get("reflection")),
            replan=_dict(payload.get("replan")),
            termination=_dict(payload.get("termination")),
            llm_repair_action_audit=_dict(payload.get("llm_repair_action_audit")),
            action_registry=registry_payload,
        )
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_agent_controller_markdown(payload),
        encoding="utf-8",
    )
    registry_path.write_text(
        json.dumps(registry_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    registry_markdown_path.write_text(
        render_agent_action_registry_markdown(registry_payload),
        encoding="utf-8",
    )
    policy_trace_path.write_text(
        json.dumps(policy_trace_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    policy_trace_markdown_path.write_text(
        render_agent_policy_trace_markdown(policy_trace_payload),
        encoding="utf-8",
    )
    return {
        "agent_controller_json": str(json_path),
        "agent_controller_markdown": str(markdown_path),
        "agent_action_registry_json": str(registry_path),
        "agent_action_registry_markdown": str(registry_markdown_path),
        "agent_policy_trace_json": str(policy_trace_path),
        "agent_policy_trace_markdown": str(policy_trace_markdown_path),
    }


def _select_action(
    summary: dict[str, Any],
    readiness: dict[str, Any],
    fault: dict[str, Any],
) -> dict[str, Any]:
    current_stage = str(readiness.get("current_stage") or "")
    blocker = str(readiness.get("blocker") or "")
    setup_blocker = str(readiness.get("repository_test_setup_doctor_blocker") or "")
    effective_blocker = _effective_blocker(readiness)
    dynamic_level = str(readiness.get("dynamic_evidence_level") or "none")
    repo_spec = str(summary.get("repo_spec") or summary.get("repo") or "owner/repo")
    output_dir = str(summary.get("output_dir") or "outputs/repo_intelligence")
    base = (
        "python -m code_intelligence_agent.evaluation.github_repo_intelligence "
        f"{repo_spec} {output_dir}"
    )
    if _is_checkout_failure_blocker(effective_blocker):
        return _retry_repository_checkout_or_use_cache_action(base, readiness)
    if current_stage == "source_import_blocked":
        if blocker.startswith("github_fetch"):
            return _action(
                "retry_with_github_token_or_cache",
                "source_discovery",
                "github_repo_intelligence",
                (
                    f"{base} --format markdown --token-env GITHUB_TOKEN "
                    "or rerun with --prefer-cached-discovery after a matching discovery.json is available"
                ),
                (
                    "GitHub discovery failed before source import; provide an "
                    "authenticated token, wait for the API limit reset, or reuse "
                    "a cached discovery artifact."
                ),
                "low",
                executable_now=False,
            )
        return _action(
            "adjust_source_filters",
            "phase1",
            "github_repo_intelligence",
            (
                f"{base} --format markdown "
                "# auto action reruns with relaxed include/target-prefix filters"
            ),
            (
                "Repository source import or parsing did not produce "
                "analyzable Python files; relax narrow include or target-prefix "
                "filters and rerun source discovery."
            ),
            "low",
        )
    if current_stage == "phase1_repo_understanding":
        if _can_collect_repository_test_evidence(readiness):
            return _action(
                "run_repository_tests_with_checkout",
                "phase3",
                "github_repo_intelligence",
                f"{base} --execution-profile checkout --format markdown --require-analysis-ready",
                (
                    "Repository structure exists but static bug-signal mining "
                    "did not produce candidates; collect repository-test "
                    "evidence to drive dynamic localization."
                ),
                "medium",
            )
        if blocker == "no_static_candidates":
            return _action(
                "expand_static_candidate_search",
                "phase2",
                "github_repo_intelligence",
                (
                    f"{base} --preset mining --max-sources 200 "
                    "--max-candidates 50 --format markdown --require-analysis-ready"
                ),
                (
                    "Repository structure is available but no static bug "
                    "candidates were mined and dynamic tests are not currently "
                    "collectable; broaden source scope and candidate mining "
                    "before declaring a terminal blocker."
                ),
                "low",
            )
        return _action(
            "mine_static_bug_signals",
            "phase2",
            "github_repo_intelligence",
            f"{base} --preset mining --format markdown --require-analysis-ready",
            "Repository structure exists but static bug-signal mining is not ready.",
            "low",
        )
    if current_stage == "phase2_static_bug_signal_mining":
        return _action(
            "build_static_graph_fault_ranking",
            "phase2",
            "github_repo_intelligence",
            f"{base} --preset mining --format markdown --require-analysis-ready",
            "Static candidates exist but graph-backed Top-k localization is not ready.",
            "low",
        )
    if current_stage == "phase2_static_graph_fault_localization":
        if (
            blocker.startswith("dynamic_fault_localization_not_ready")
            or _dynamic_fault_localization_needed(summary, readiness, fault)
        ):
            return _dynamic_fault_localization_action(summary, readiness)
        if _runner_fallback_executable(readiness):
            fallback_reason = str(
                readiness.get("planned_repository_test_runner_fallback_reason") or ""
            )
            fallback_from = str(
                readiness.get("planned_repository_test_runner_fallback_from") or ""
            )
            fallback_to = str(
                readiness.get("planned_repository_test_runner_fallback_to") or ""
            )
            planned_command = str(
                readiness.get("planned_repository_test_command") or ""
            )
            return _action(
                "run_repository_tests_with_checkout",
                "phase3",
                "github_repo_intelligence",
                f"{base} --execution-profile checkout --format markdown --require-analysis-ready",
                (
                    "Execution planning selected a safe fallback runner "
                    f"`{fallback_from or 'preferred'}` -> `{fallback_to or 'selected'}` "
                    f"({fallback_reason or 'runner_fallback'}); collect dynamic "
                    f"test evidence with `{planned_command or 'the planned command'}`."
                ),
                "medium",
            )
        if _timeout_narrowing_needed(summary, readiness):
            return _timeout_narrowing_action(base, summary, readiness)
        if _is_test_execution_failure_blocker(readiness, effective_blocker):
            return _test_execution_failure_action(base, readiness)
        if (
            blocker in {"", "dynamic_tests_not_executed"}
            or setup_blocker.startswith("checkout:")
            or "checkout" in blocker
        ):
            return _action(
                "run_repository_tests_with_checkout",
                "phase3",
                "github_repo_intelligence",
                f"{base} --execution-profile checkout --format markdown --require-analysis-ready",
                "Static localization is ready; collect repository-test evidence next.",
                "medium",
            )
        if dynamic_level == "passing_tests":
            regression_guard = _dict(summary.get("repository_test_regression_guard"))
            if str(regression_guard.get("status") or "") == "pass":
                if _application_source_focus_needed(summary, fault):
                    return _application_source_focus_action(base, summary, fault)
                if _failure_overlay_can_be_generated(summary, readiness):
                    return _failure_overlay_action(base)
                if _failure_overlay_exhausted(summary):
                    return _failure_overlay_blocker_action(summary)
                return _action(
                    "await_failing_test_or_bug_report",
                    "phase3",
                    "github_repo_intelligence",
                    (
                        "Provide a failing test, mutation ground truth, bug report, "
                        "or controlled failure overlay before repair."
                    ),
                    (
                        "Passing tests have been registered as regression guards, "
                        "but they do not identify a concrete repair target."
                    ),
                    "low",
                    executable_now=False,
                )
            return _action(
                "convert_passing_tests_to_regression_guard",
                "phase3",
                "repository_test_patch_validation",
                "Write repository_test_regression_guard.json/md from passing repository tests.",
                "Repository tests pass, so they are useful as regression guards but not localization evidence.",
                "low",
                executable_now=True,
            )
        if _application_source_focus_needed(summary, fault):
            return _application_source_focus_action(base, summary, fault)
        if _is_environment_blocker(effective_blocker):
            environment_repair = _dict(
                summary.get("repository_test_environment_repair_plan")
            )
            if str(environment_repair.get("status") or "") == "pass":
                return _action(
                    "await_environment_repair",
                    "phase3",
                    "github_repo_intelligence",
                    (
                        "Apply the environment repair plan, then rerun with "
                        "--execution-profile phase3-fast or agent-auto."
                    ),
                    (
                        "Repository test environment repair advice has been "
                        "recorded; external dependency or tool setup is required "
                        "before dynamic localization can continue."
                    ),
                    "medium",
                    executable_now=False,
                )
            return _action(
                "prepare_repository_test_environment",
                "phase3",
                "github_repo_intelligence",
                "Write repository_test_environment_repair_plan.json/md from setup doctor and environment diagnostics.",
                "Repository tests reached execution planning but test tooling is missing.",
                "medium",
            )
        if _is_test_discovery_blocker(readiness, effective_blocker):
            return _action(
                "discover_repository_tests",
                "phase3",
                "github_repo_intelligence",
                (
                    f"{base} --execution-profile checkout --format markdown "
                    "--require-analysis-ready"
                ),
                (
                    "Static localization is available, but the repository test "
                    "entrypoint is missing or collected no tests; retry with a "
                    "full shallow checkout and broadened source/test discovery "
                    "before switching to synthetic overlay or external bug input."
                ),
                "low",
            )
        if _failure_overlay_can_be_generated(summary, readiness):
            return _failure_overlay_action(base)
        if _failure_overlay_exhausted(summary):
            return _failure_overlay_blocker_action(summary)
        return _action(
            "collect_dynamic_failure_evidence",
            "phase3",
            "github_repo_intelligence",
            f"{base} --execution-profile phase3-fast --format markdown --require-analysis-ready",
            "Static localization needs failing dynamic evidence before repair.",
            "medium",
        )
    if current_stage == "phase2_dynamic_fault_localization":
        patch_status = str(readiness.get("patch_validation_status") or "")
        patch_blocker = str(readiness.get("blocker") or "")
        reflection_strategy = _reflection_strategy_hint(summary)
        llm_reflection_hint = _llm_reflection_hint(summary)
        llm_patch_hint = _llm_patch_generation_hint(summary)
        if _patch_candidates_blocked_by_safety_gate(readiness):
            return _action(
                "regenerate_safe_patch_candidates",
                "phase3",
                "repository_test_patch_candidates",
                (
                    "Inspect safety_blocked_candidates in "
                    "repository_test_patch_validation.md, then regenerate "
                    "smaller AST-valid, scope-limited patch candidates before "
                    "sandbox validation."
                ),
                (
                    "Patch validation did not execute because every candidate "
                    "was blocked by the pre-sandbox safety gate."
                ),
                "medium",
            )
        if (
            patch_status == "fail"
            or "no_candidate_passed" in patch_blocker
            or patch_blocker.startswith("patch_validation_not_repair_ready")
        ):
            reflection = _dict(summary.get("reflection_summary"))
            reflection_count = _int(
                reflection.get("reflection_candidate_count")
            )
            max_depth = _int(reflection.get("max_depth_executed"))
            if reflection_count <= 0 and max_depth <= 0:
                return _action(
                    _reflection_loop_action_id(summary),
                    "phase3",
                    "repository_test_patch_validation",
                    _reflection_loop_command(summary),
                    (
                        "Patch validation failed before any reflected candidate "
                        "was generated; run the reflection loop next."
                        + reflection_strategy
                        + llm_reflection_hint
                        + llm_patch_hint
                    ),
                    "medium",
                    executable_now=bool(readiness.get("can_attempt_patch_repair", False)),
                )
            return _action(
                "expand_patch_candidates_or_reflection",
                "phase3",
                "repository_test_patch_validation",
                (
                    "Inspect reflection_trace.md, increase reflection rounds/width, "
                    "switch reflection_mode to llm, or expand patch candidates."
                ),
                (
                    "Patch validation ran but no candidate produced a verified repair."
                    + reflection_strategy
                    + llm_reflection_hint
                    + llm_patch_hint
                ),
                "medium",
                executable_now=False,
            )
        return _patch_generation_action(
            summary,
            readiness,
            command=(
                "Use repository_test_fault_localization.json to generate patch "
                "candidates and validate them."
            ),
            reason=(
                "Dynamic localization is available; patch generation and "
                "validation are the next repair step."
            ),
            llm_patch_hint=llm_patch_hint,
        )
    if current_stage == "phase3_patch_validation":
        if not bool(readiness.get("repair_ready", False)):
            reflection_strategy = _reflection_strategy_hint(summary)
            llm_reflection_hint = _llm_reflection_hint(summary)
            llm_patch_hint = _llm_patch_generation_hint(summary)
            if _patch_candidates_not_ready(readiness):
                return _patch_generation_action(
                    summary,
                    readiness,
                    command=(
                        "Use repository_test_fault_localization.json to "
                        "generate patch candidates and validate them."
                    ),
                    reason=(
                        "Patch validation reached Phase 3 without a ready "
                        "candidate set; generate controlled candidates from "
                        "the current Top-k localization before reflection."
                    ),
                    llm_patch_hint=llm_patch_hint,
                )
            if _patch_candidates_blocked_by_safety_gate(readiness):
                return _action(
                    "regenerate_safe_patch_candidates",
                    "phase3",
                    "repository_test_patch_candidates",
                    (
                        "Inspect safety_blocked_candidates in "
                        "repository_test_patch_validation.md, then regenerate "
                        "smaller AST-valid, scope-limited patch candidates before "
                        "sandbox validation."
                    ),
                    (
                        "Patch validation reached the safety gate, but no "
                        "candidate was safe enough to execute in sandbox."
                    ),
                    "medium",
                )
            reflection = _dict(summary.get("reflection_summary"))
            reflection_count = _int(
                reflection.get("reflection_candidate_count")
            )
            max_depth = _int(reflection.get("max_depth_executed"))
            if reflection_count <= 0 and max_depth <= 0:
                return _action(
                    _reflection_loop_action_id(summary),
                    "phase3",
                    "repository_test_patch_validation",
                    _reflection_loop_command(summary),
                    (
                        "Patch validation produced candidate evidence, but "
                        "the repair is not fully verified; run reflection "
                        "before advancing."
                        + reflection_strategy
                        + llm_reflection_hint
                        + llm_patch_hint
                    ),
                    "medium",
                    executable_now=bool(readiness.get("can_attempt_patch_repair", False)),
                )
            return _action(
                "expand_patch_candidates_or_reflection",
                "phase3",
                "repository_test_patch_validation",
                (
                    "Inspect reflection_trace.md, regression failure output, "
                    "increase reflection rounds/width, switch reflection_mode "
                    "to llm, or expand patch candidates."
                ),
                (
                    "Patch validation ran, but regression validation did not "
                    "produce a verified repair."
                    + reflection_strategy
                    + llm_reflection_hint
                    + llm_patch_hint
                ),
                "medium",
                executable_now=False,
            )
        return _action(
            "run_search_and_ablation_evaluation",
            "phase4",
            "run_experiment_suite",
            "python -m code_intelligence_agent.evaluation.run_experiment_suite",
            "Patch validation is ready; evaluate search strategy and ablations.",
            "low",
        )
    return _action(
        "inspect_generated_artifacts",
        "unknown",
        "github_repo_intelligence",
        str(readiness.get("next_action") or ""),
        "No specific controller policy matched the current state.",
        "low",
        executable_now=False,
    )


def _plan_steps(
    selected_action: dict[str, Any],
    readiness: dict[str, Any],
    fault: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _step(
            1,
            "observe",
            (
                "Read repository structure, graph, static signals, dynamic "
                "evidence, setup doctor status, and Agent goal readiness."
            ),
            "github_repo_intelligence.json",
            f"current_stage={readiness.get('current_stage') or 'unknown'}",
        ),
        _step(
            2,
            "plan",
            f"Select action `{selected_action.get('id')}` from readiness and fault-localization signals.",
            "AgentController",
            f"next_stage={readiness.get('next_stage') or 'unknown'}",
        ),
        _step(
            3,
            "act",
            str(selected_action.get("command") or "Inspect generated artifacts."),
            str(selected_action.get("tool") or "none"),
            str(selected_action.get("reason") or ""),
        ),
        _step(
            4,
            "verify",
            "Check updated status, fault-localization mode, test result, patch validation, or blocker.",
            "github_repo_intelligence.json",
            f"fault_mode={fault.get('mode') or 'none'}",
        ),
        _step(
            5,
            "reflect",
            "If verification fails, classify the blocker and choose fallback action.",
            "AgentController",
            "new blocker, retry plan, or terminal diagnosis",
        ),
        _step(
            6,
            "replan",
            "Use the updated blocker or verification result to select the next controller action.",
            "AgentController",
            "next selected action or terminal blocked/complete state",
        ),
    ]


def _observations(
    summary: dict[str, Any],
    readiness: dict[str, Any],
    fault: dict[str, Any],
) -> list[dict[str, str]]:
    goal_readiness = _dict(summary.get("agent_goal_readiness"))
    repo_input = _dict(summary.get("repo_input"))
    llm_patch_audit = _dict(summary.get("repository_llm_patch_generation_audit"))
    llm_reflection_audit = _dict(summary.get("repository_llm_reflection_audit"))
    llm_patch_status = str(
        summary.get("repository_llm_patch_generation_status")
        or llm_patch_audit.get("status")
        or ""
    )
    llm_patch_reason = str(
        summary.get("repository_llm_patch_generation_reason")
        or llm_patch_audit.get("reason")
        or ""
    )
    llm_patch_blocked = bool(
        summary.get("repository_llm_patch_blocked", False)
        or llm_patch_audit.get("blocked", False)
        or llm_patch_status == "blocked"
        or llm_patch_reason.startswith("missing_api_key:")
        or llm_patch_reason == "missing_llm_api_key"
    )
    llm_reflection_status = str(
        summary.get("repository_llm_reflection_status")
        or llm_reflection_audit.get("status")
        or ""
    )
    llm_reflection_reason = str(
        summary.get("repository_llm_reflection_reason")
        or llm_reflection_audit.get("reason")
        or ""
    )
    llm_reflection_blocked = bool(
        summary.get("repository_llm_reflection_blocked", False)
        or llm_reflection_audit.get("blocked", False)
        or llm_reflection_status in {"blocked", "unavailable", "unsupported", "error"}
        or llm_reflection_reason.startswith("missing_api_key:")
    )
    llm_reflection_blocker = str(
        summary.get("repository_llm_reflection_blocker")
        or llm_reflection_audit.get("blocker")
        or (llm_reflection_reason if llm_reflection_blocked else "")
    )
    project_config = _dict(
        _dict(summary.get("repository_structure")).get("project_config")
    )
    return [
        _observation("repo", summary.get("repo")),
        _observation("repo_spec", summary.get("repo_spec")),
        _observation("repo_input_kind", repo_input.get("kind")),
        _observation(
            "repo_input_ref_selection_source",
            repo_input.get("ref_selection_source"),
        ),
        _observation("repo_input_url_inferred_ref", repo_input.get("url_inferred_ref")),
        _observation("repository_ref", summary.get("repository_ref") or "default"),
        _observation("requested_ref", summary.get("requested_ref") or "default"),
        _observation(
            "ref_source",
            summary.get("ref_source") or "default_branch_discovery",
        ),
        _observation("source_cache_dir", summary.get("source_cache_dir")),
        _observation("agent_auto_enabled", summary.get("agent_auto_enabled")),
        _observation(
            "agent_auto_action_count",
            summary.get("agent_auto_action_count"),
        ),
        _observation(
            "agent_auto_stop_reason",
            summary.get("agent_auto_stop_reason"),
        ),
        _observation(
            "agent_goal_readiness_status",
            goal_readiness.get("status") or "none",
        ),
        _observation(
            "agent_goal_readiness_criteria",
            (
                f"pass={_int(goal_readiness.get('passed_criteria_count', 0))}/"
                f"{_int(goal_readiness.get('criteria_count', 0))}, "
                f"failed={_int(goal_readiness.get('failed_criteria_count', 0))}"
            ),
        ),
        _observation(
            "agent_goal_readiness_failed_criteria",
            _format_list(_list(goal_readiness.get("failed_criteria"))),
        ),
        _observation(
            "project_config_files",
            _format_list(_list(project_config.get("project_config_files"))),
        ),
        _observation(
            "dependency_tool_signals",
            _format_list(_list(project_config.get("dependency_tool_signals"))),
        ),
        _observation(
            "dependency_file_count",
            project_config.get("dependency_file_count"),
        ),
        _observation(
            "packaging_file_count",
            project_config.get("packaging_file_count"),
        ),
        _observation("current_stage", readiness.get("current_stage")),
        _observation("next_stage", readiness.get("next_stage")),
        _observation("blocker", readiness.get("blocker")),
        _observation("static_signal_count", readiness.get("static_signal_count")),
        _observation("fault_localization_mode", fault.get("mode")),
        _observation("fault_localization_status", fault.get("status")),
        _observation("top_function", fault.get("top_function")),
        _observation("fault_top_source_role", fault.get("top_source_role")),
        _observation(
            "fault_source_role_counts",
            _format_counts(_dict(fault.get("source_role_counts"))),
        ),
        _observation(
            "fault_application_candidate_count",
            fault.get("application_candidate_count"),
        ),
        _observation(
            "fault_non_application_topk_only",
            fault.get("non_application_topk_only"),
        ),
        _observation("dynamic_evidence_level", readiness.get("dynamic_evidence_level")),
        _observation(
            "dynamic_evidence_usable_for_localization",
            readiness.get("dynamic_evidence_usable_for_localization"),
        ),
        _observation(
            "dynamic_fault_localization_status",
            summary.get("repository_test_fault_localization_status"),
        ),
        _observation(
            "dynamic_fault_localization_reason",
            readiness.get("dynamic_fault_localization_reason"),
        ),
        _observation(
            "planned_repository_test_failures",
            (
                f"failed={_int(readiness.get('planned_repository_test_result_failed', 0))}, "
                f"errors={_int(readiness.get('planned_repository_test_result_errors', 0))}"
            ),
        ),
        _observation(
            "planned_repository_test_result",
            readiness.get("planned_repository_test_result_status"),
        ),
        _observation(
            "repository_test_timeout_narrowing_status",
            _timeout_narrowing_value(
                summary,
                readiness,
                "repository_test_timeout_narrowing_status",
            )
            or "not_attempted",
        ),
        _observation(
            "repository_test_timeout_narrowing_reason",
            _timeout_narrowing_value(
                summary,
                readiness,
                "repository_test_timeout_narrowing_reason",
            )
            or "none",
        ),
        _observation(
            "repository_test_timeout_narrowing_attempts",
            (
                "attempts="
                f"{_int(_timeout_narrowing_value(summary, readiness, 'repository_test_timeout_narrowing_attempt_count'))}, "
                "selected="
                f"{_timeout_narrowing_value(summary, readiness, 'repository_test_timeout_narrowing_selected_command') or 'none'}, "
                "failure_category="
                f"{_timeout_narrowing_value(summary, readiness, 'repository_test_timeout_narrowing_selected_failure_category') or 'none'}"
            ),
        ),
        _observation(
            "planned_repository_test_runner",
            readiness.get("planned_repository_test_runner"),
        ),
        _observation(
            "planned_repository_test_runner_fallback",
            readiness.get("planned_repository_test_runner_fallback_used"),
        ),
        _observation(
            "planned_repository_test_runner_fallback_reason",
            readiness.get("planned_repository_test_runner_fallback_reason"),
        ),
        _observation(
            "repository_test_setup_doctor_status",
            readiness.get("repository_test_setup_doctor_status")
            or summary.get("repository_test_setup_doctor_status"),
        ),
        _observation(
            "repository_test_setup_doctor_blocker",
            readiness.get("repository_test_setup_doctor_blocker")
            or summary.get("repository_test_setup_doctor_blocker"),
        ),
        _observation(
            "repository_test_setup_doctor_checks",
            (
                f"pass={_int(readiness.get('repository_test_setup_doctor_passed_check_count', summary.get('repository_test_setup_doctor_passed_check_count', 0)))}/"
                f"{_int(readiness.get('repository_test_setup_doctor_check_count', summary.get('repository_test_setup_doctor_check_count', 0)))}, "
                f"warning={_int(readiness.get('repository_test_setup_doctor_warning_check_count', summary.get('repository_test_setup_doctor_warning_check_count', 0)))}, "
                f"blocked={_int(readiness.get('repository_test_setup_doctor_blocked_check_count', summary.get('repository_test_setup_doctor_blocked_check_count', 0)))}"
            ),
        ),
        _observation(
            "repository_test_setup_doctor_check_status_counts",
            _format_counts(
                _dict(readiness.get("repository_test_setup_doctor_check_status_counts"))
                or _dict(summary.get("repository_test_setup_doctor_check_status_counts"))
            ),
        ),
        _observation(
            "repository_test_setup_doctor_blocked_check_names",
            _format_list(
                _list(readiness.get("repository_test_setup_doctor_blocked_check_names"))
                or _list(summary.get("repository_test_setup_doctor_blocked_check_names"))
            ),
        ),
        _observation(
            "repository_test_setup_doctor_warning_check_names",
            _format_list(
                _list(readiness.get("repository_test_setup_doctor_warning_check_names"))
                or _list(summary.get("repository_test_setup_doctor_warning_check_names"))
            ),
        ),
        _observation(
            "repository_test_regression_guard_status",
            _dict(summary.get("repository_test_regression_guard")).get("status"),
        ),
        _observation(
            "repository_test_analysis_source",
            summary.get("repository_test_analysis_source"),
        ),
        _observation(
            "repository_test_overlay_trigger_reason",
            summary.get("repository_test_overlay_trigger_reason"),
        ),
        _observation(
            "repository_test_failure_overlay_status",
            summary.get("repository_test_failure_overlay_status"),
        ),
        _observation(
            "repository_test_failure_overlay_reason",
            summary.get("repository_test_failure_overlay_reason"),
        ),
        _observation(
            "repository_test_failure_overlay_candidates",
            (
                f"supported={_int(summary.get('repository_test_failure_overlay_supported_candidates', 0))}, "
                f"attempted={_int(summary.get('repository_test_failure_overlay_attempted_cases', 0))}, "
                f"limit={_int(summary.get('repository_test_failure_overlay_candidate_limit', 0))}"
            ),
        ),
        _observation(
            "repository_test_failure_overlay_selected",
            (
                f"rule={summary.get('repository_test_failure_overlay_selected_rule') or 'none'}, "
                f"function={summary.get('repository_test_failure_overlay_selected_function') or 'none'}"
            ),
        ),
        _observation(
            "repository_patch_generation_mode",
            summary.get("repository_patch_generation_mode"),
        ),
        _observation(
            "repository_llm_patch_generation_status",
            llm_patch_status,
        ),
        _observation(
            "repository_llm_patch_generation_reason",
            llm_patch_reason,
        ),
        _observation(
            "repository_llm_patch_generation_fallback",
            (
                f"blocked={llm_patch_blocked}, "
                "fallback_used="
                f"{bool(summary.get('repository_llm_patch_generation_fallback_used', False) or llm_patch_audit.get('fallback_used', False))}, "
                f"provider={summary.get('repository_llm_patch_provider') or llm_patch_audit.get('provider') or 'none'}, "
                f"model={summary.get('repository_llm_patch_model') or llm_patch_audit.get('model') or 'none'}"
            ),
        ),
        _observation(
            "repository_llm_patch_generation_telemetry",
            _llm_patch_telemetry_summary(summary, llm_patch_audit),
        ),
        _observation(
            "repository_llm_reflection_status",
            llm_reflection_status,
        ),
        _observation(
            "repository_llm_reflection_reason",
            llm_reflection_reason,
        ),
        _observation(
            "repository_llm_reflection_blocker",
            (
                f"blocked={llm_reflection_blocked}, "
                f"blocker={llm_reflection_blocker or 'none'}, "
                f"provider={summary.get('repository_llm_reflection_provider') or llm_reflection_audit.get('provider') or 'none'}, "
                f"model={summary.get('repository_llm_reflection_model') or llm_reflection_audit.get('model') or 'none'}"
            ),
        ),
        _observation(
            "repository_test_patch_judge_status",
            summary.get("repository_test_patch_judge_status"),
        ),
        _observation(
            "repository_test_patch_judge_reason",
            summary.get("repository_test_patch_judge_reason"),
        ),
        _observation(
            "repository_test_patch_judge_authority",
            (
                f"mode={summary.get('repository_test_patch_judge_mode') or 'none'}, "
                f"judged={_int(summary.get('repository_test_patch_judge_candidate_count', 0))}, "
                f"authority={summary.get('repository_test_patch_judge_authority') or 'sandbox_pytest_decides_success'}"
            ),
        ),
        _observation(
            "patch_validation_status",
            readiness.get("patch_validation_status"),
        ),
        _observation(
            "patch_validation_reason",
            readiness.get("patch_validation_reason"),
        ),
        _observation(
            "patch_validation_candidates",
            (
                f"input={_int(readiness.get('patch_validation_input_candidate_count', 0))}, "
                f"validated={_int(readiness.get('patch_validation_candidate_count', 0))}, "
                f"safety_blocked={_int(readiness.get('patch_validation_safety_blocked_candidate_count', 0))}"
            ),
        ),
        _observation(
            "repository_test_environment_repair_plan_status",
            _dict(summary.get("repository_test_environment_repair_plan")).get("status"),
        ),
    ]


def _llm_repair_action_audit(
    summary: dict[str, Any],
    *,
    readiness: dict[str, Any],
    selected_action: dict[str, Any],
) -> dict[str, Any]:
    patch_mode = str(summary.get("repository_patch_generation_mode") or "none").lower()
    llm_audit = _dict(summary.get("repository_llm_patch_generation_audit"))
    reflection_audit = _dict(summary.get("repository_llm_reflection_audit"))
    provider = str(
        summary.get("repository_llm_patch_provider")
        or llm_audit.get("provider")
        or "none"
    )
    model = str(
        summary.get("repository_llm_patch_model")
        or llm_audit.get("model")
        or "none"
    )
    llm_status = str(
        summary.get("repository_llm_patch_generation_status")
        or llm_audit.get("status")
        or "none"
    )
    llm_reason = str(
        summary.get("repository_llm_patch_generation_reason")
        or llm_audit.get("reason")
        or ""
    )
    key_present = bool(
        summary.get("repository_llm_patch_api_key_present", False)
        or llm_audit.get("api_key_present", False)
    )
    key_fingerprint = str(
        summary.get("repository_llm_patch_api_key_fingerprint")
        or llm_audit.get("api_key_fingerprint")
        or ""
    )
    failure_policy = _llm_patch_failure_policy(summary, llm_audit)
    blocked = bool(
        summary.get("repository_llm_patch_blocked", False)
        or llm_audit.get("blocked", False)
        or llm_status in {"blocked", "unavailable", "failed", "error"}
        or llm_reason.startswith("missing_api_key:")
        or llm_reason == "missing_llm_api_key"
        or _int(
            summary.get("repository_llm_patch_failure_count")
            or llm_audit.get("failure_count")
            or 0
        )
        > 0
    )
    blocker = str(
        summary.get("repository_llm_patch_blocker")
        or llm_audit.get("blocker")
        or (llm_reason if blocked else "")
    )
    generator_counts = _dict(summary.get("repository_patch_generator_counts"))
    llm_candidate_count = _int(
        summary.get("repository_patch_generator_llm_candidate_count")
        or summary.get("repository_patch_generator_llm_count")
        or summary.get("repository_test_patch_generator_llm_candidate_count")
        or generator_counts.get("llm")
        or 0
    )
    rule_candidate_count = _int(
        summary.get("repository_patch_generator_rule_count")
        or summary.get("repository_patch_generator_rule_candidate_count")
        or generator_counts.get("rule")
        or 0
    )
    llm_request_count = _int(
        summary.get("repository_llm_patch_request_count")
        or llm_audit.get("request_count")
        or 0
    )
    llm_failure_count = _int(
        summary.get("repository_llm_patch_failure_count")
        or llm_audit.get("failure_count")
        or 0
    )
    llm_total_tokens = _int(
        summary.get("repository_llm_patch_total_tokens")
        or llm_audit.get("total_tokens")
        or 0
    )
    llm_estimated_tokens = _int(
        summary.get("repository_llm_patch_estimated_total_tokens")
        or llm_audit.get("estimated_total_tokens")
        or 0
    )
    llm_error_reason_counts = _dict(
        summary.get("repository_llm_patch_error_reason_counts")
    ) or _dict(llm_audit.get("error_reason_counts"))
    validation_status = str(
        summary.get("repository_test_patch_validation_status")
        or readiness.get("patch_validation_status")
        or "none"
    )
    validation_success_count = _int(
        summary.get("repository_test_patch_validation_success_count") or 0
    )
    validation_executed_count = _int(
        summary.get("repository_test_patch_validation_executed_count") or 0
    )
    reflection_mode = str(
        summary.get("repository_test_patch_validation_reflection_mode")
        or summary.get("repository_test_reflection_mode")
        or reflection_audit.get("mode")
        or "none"
    ).lower()
    reflection_status = str(
        summary.get("repository_llm_reflection_status")
        or reflection_audit.get("status")
        or "none"
    )
    reflection_candidate_count = _int(
        summary.get("repository_test_patch_validation_reflection_candidate_count")
        or 0
    )
    successful_reflection_count = _int(
        summary.get("repository_test_patch_validation_successful_reflection_count")
        or summary.get(
            "repository_test_patch_validation_successful_regression_reflection_count"
        )
        or 0
    )
    judge_mode = str(summary.get("repository_test_patch_judge_mode") or "none")
    judge_status = str(summary.get("repository_test_patch_judge_status") or "none")
    repair_action_id = (
        str(failure_policy.get("action_id") or "")
        if failure_policy and patch_mode == "llm"
        else _llm_repair_action_id(
            patch_mode=patch_mode,
            blocked=blocked,
        )
    )
    selected_action_id = str(selected_action.get("id") or "")
    reflection_action_id = (
        "run_llm_patch_reflection_loop"
        if (
            selected_action_id == "run_llm_patch_reflection_loop"
            or (
                reflection_mode == "llm"
                and (
                    reflection_candidate_count > 0
                    or successful_reflection_count > 0
                )
            )
        )
        else ""
    )
    if patch_mode not in {"llm", "hybrid"}:
        status = "not_applicable"
        reason = "patch_generation_mode_is_not_llm_or_hybrid"
    elif blocked:
        status = "blocked"
        reason = blocker or "llm_patch_key_missing"
    elif llm_status == "pass" and llm_candidate_count > 0:
        status = "pass"
        reason = "llm_patch_candidates_generated"
    elif selected_action_id in {
        "generate_llm_patch_candidates",
        "generate_hybrid_patch_candidates",
    }:
        status = "ready"
        reason = "llm_patch_generation_action_selected"
    else:
        status = "warning"
        reason = llm_reason or "llm_patch_generation_not_proven"
    next_action = str(
        summary.get("agent_answers_next_action")
        or summary.get("analysis_next_action")
        or selected_action.get("reason")
        or selected_action.get("command")
        or "none"
    )
    agent_loop_evidence = {
        "observe": (
            f"patch_mode={patch_mode}; llm_status={llm_status}; "
            f"dynamic={readiness.get('dynamic_evidence_level') or 'none'}; "
            f"provider={provider}; model={model}; "
            f"key_present={str(key_present).lower()}; "
            f"requests={llm_request_count}; failures={llm_failure_count}; "
            f"failure_class={failure_policy.get('failure_class') or 'none'}"
        ),
        "plan": (
            f"repair_action={repair_action_id or 'none'}; "
            f"reflection_action={reflection_action_id or 'none'}; "
            f"llm_recovery_policy={failure_policy.get('recovery_policy') or 'none'}; "
            "LLM output is constrained to localized Top-k functions and must pass "
            "JSON/AST/scope/safety gates before sandbox validation"
        ),
        "act": (
            f"llm_candidates={llm_candidate_count}; "
            f"rule_candidates={rule_candidate_count}; "
            f"llm_reason={llm_reason or 'none'}; "
            f"tokens={llm_total_tokens}; estimated_tokens={llm_estimated_tokens}"
        ),
        "verify": (
            f"sandbox_validation={validation_status}; "
            f"executed={validation_executed_count}; "
            f"successes={validation_success_count}; "
            f"judge={judge_mode}/{judge_status}; "
            "authority=sandbox_pytest_decides_success"
        ),
        "reflect": (
            f"reflection_mode={reflection_mode}; "
            f"reflection_status={reflection_status}; "
            f"reflection_candidates={reflection_candidate_count}; "
            f"successful_reflections={successful_reflection_count}"
        ),
        "replan": (
            "stop_with_blocker="
            f"{blocker or _format_counts(llm_error_reason_counts) or 'none'}; "
            f"failure_class={failure_policy.get('failure_class') or 'none'}; "
            f"policy={failure_policy.get('recovery_policy') or 'none'}"
            if blocked
            else next_action
        ),
    }
    return {
        "status": status,
        "reason": reason,
        "patch_generation_mode": patch_mode,
        "repair_action_id": repair_action_id,
        "reflection_action_id": reflection_action_id,
        "selected_next_action_id": selected_action_id,
        "provider": provider,
        "model": model,
        "api_key_present": key_present,
        "api_key_fingerprint": key_fingerprint,
        "blocker": blocker,
        "llm_candidate_count": llm_candidate_count,
        "rule_candidate_count": rule_candidate_count,
        "llm_request_count": llm_request_count,
        "llm_failure_count": llm_failure_count,
        "llm_total_tokens": llm_total_tokens,
        "llm_estimated_total_tokens": llm_estimated_tokens,
        "llm_error_reason_counts": llm_error_reason_counts,
        "llm_provider_failure_class": str(
            failure_policy.get("failure_class") or ""
        ),
        "llm_provider_primary_error": str(
            failure_policy.get("primary_error") or ""
        ),
        "llm_provider_recovery_policy": str(
            failure_policy.get("recovery_policy") or ""
        ),
        "llm_provider_recovery_action_id": str(
            failure_policy.get("action_id") or ""
        ),
        "validation_status": validation_status,
        "validation_executed_count": validation_executed_count,
        "validation_success_count": validation_success_count,
        "reflection_mode": reflection_mode,
        "reflection_status": reflection_status,
        "reflection_candidate_count": reflection_candidate_count,
        "successful_reflection_count": successful_reflection_count,
        "patch_judge_mode": judge_mode,
        "patch_judge_status": judge_status,
        "sandbox_authority": "sandbox_pytest_decides_success",
        "agent_loop_evidence": agent_loop_evidence,
    }


def _llm_repair_action_id(*, patch_mode: str, blocked: bool) -> str:
    if patch_mode == "llm":
        return "configure_llm_patch_api_key" if blocked else "generate_llm_patch_candidates"
    if patch_mode == "hybrid":
        return "generate_hybrid_patch_candidates"
    return ""


def _enrich_selected_action_decision(
    *,
    summary: dict[str, Any],
    readiness: dict[str, Any],
    fault: dict[str, Any],
    selected_action: dict[str, Any],
    observations: list[dict[str, str]],
    verification: dict[str, Any],
    reflection: dict[str, Any],
    replan: dict[str, Any],
    termination: dict[str, Any],
) -> dict[str, Any]:
    item = _selected_action_decision_item(
        summary=summary,
        readiness=readiness,
        fault=fault,
        selected_action=selected_action,
        observations=observations,
        verification=verification,
        reflection=reflection,
        replan=replan,
        termination=termination,
        iteration=1,
    )
    enriched = dict(selected_action)
    enriched.update(
        {
            "canonical_id": item["canonical_action_id"],
            "why_selected": item["why_selected"],
            "confidence": item["confidence"],
            "confidence_reason": item["confidence_reason"],
            "input_summary": item["input"],
            "expected_output": item["output"],
            "blocker": item["blocker"],
            "blocker_present": item["blocker_present"],
            "next_plan": item["next_plan"],
        }
    )
    return enriched


def _action_decision_audit(
    summary: dict[str, Any],
    *,
    readiness: dict[str, Any],
    fault: dict[str, Any],
    selected_action: dict[str, Any],
    observations: list[dict[str, str]],
    verification: dict[str, Any],
    reflection: dict[str, Any],
    replan: dict[str, Any],
    termination: dict[str, Any],
) -> dict[str, Any]:
    auto_trace = [_dict(item) for item in _list(summary.get("agent_auto_trace"))]
    if auto_trace:
        source = "agent_auto_trace"
        actions = [
            _auto_trace_action_decision_item(item, iteration=index + 1)
            for index, item in enumerate(auto_trace)
        ]
    else:
        source = "agent_controller"
        actions = [
            _selected_action_decision_item(
                summary=summary,
                readiness=readiness,
                fault=fault,
                selected_action=selected_action,
                observations=observations,
                verification=verification,
                reflection=reflection,
                replan=replan,
                termination=termination,
                iteration=1,
            )
        ]
    complete_count = sum(
        1 for item in actions if bool(item.get("complete", False))
    )
    status = (
        "pass"
        if actions and complete_count == len(actions)
        else "warning"
    )
    return {
        "status": status,
        "reason": (
            "action_decision_audit_complete"
            if status == "pass"
            else "action_decision_audit_incomplete"
        ),
        "source": source,
        "required_fields": ACTION_DECISION_REQUIRED_FIELDS,
        "action_count": len(actions),
        "complete_action_count": complete_count,
        "incomplete_action_count": len(actions) - complete_count,
        "actions": actions,
    }


def _selected_action_decision_item(
    *,
    summary: dict[str, Any],
    readiness: dict[str, Any],
    fault: dict[str, Any],
    selected_action: dict[str, Any],
    observations: list[dict[str, str]],
    verification: dict[str, Any],
    reflection: dict[str, Any],
    replan: dict[str, Any],
    termination: dict[str, Any],
    iteration: int,
) -> dict[str, Any]:
    del reflection
    action_id = str(selected_action.get("id") or "")
    canonical_id = canonical_action_id(action_id)
    spec = action_spec_for(action_id)
    blocker = _effective_blocker(readiness)
    confidence, confidence_reason = _action_confidence(
        readiness=readiness,
        fault=fault,
        selected_action=selected_action,
        verification=verification,
        blocker=blocker,
        registered=bool(spec),
    )
    output = _action_output_summary(
        action_id=action_id,
        spec=spec,
        verification=verification,
    )
    next_plan = _action_next_plan_summary(
        selected_action=selected_action,
        replan=replan,
        termination=termination,
        spec=spec,
    )
    item = {
        "iteration": iteration,
        "source": "agent_controller",
        "action_id": action_id,
        "canonical_action_id": canonical_id,
        "registered": bool(spec),
        "phase": str(selected_action.get("phase") or spec.get("phase") or ""),
        "tool": str(selected_action.get("tool") or spec.get("tool") or ""),
        "why_selected": str(selected_action.get("reason") or ""),
        "confidence": confidence,
        "confidence_reason": confidence_reason,
        "risk": str(selected_action.get("risk") or _risk_for_action(action_id)),
        "input": _controller_action_input_summary(
            summary=summary,
            readiness=readiness,
            fault=fault,
            observation_count=len(observations),
        ),
        "output": output,
        "blocker": blocker,
        "blocker_present": bool(blocker),
        "next_plan": next_plan,
        "executable_now": bool(selected_action.get("executable_now", False)),
        "verify_status": str(verification.get("status") or ""),
        "replan_policy": str(replan.get("next_policy") or ""),
    }
    item["complete"] = _action_decision_item_complete(item)
    return item


def _auto_trace_action_decision_item(
    item: dict[str, Any],
    *,
    iteration: int,
) -> dict[str, Any]:
    action_id = str(item.get("plan_selected_action") or "")
    canonical_id = canonical_action_id(action_id)
    spec = action_spec_for(action_id)
    blocker = str(
        item.get("observe_blocker")
        or item.get("stop_reason")
        or item.get("stop_category")
        or ""
    )
    confidence, confidence_reason = _auto_action_confidence(item)
    why = str(item.get("plan_reason") or "")
    if not why:
        why = (
            "Auto controller selected this action from the observed stage, "
            "blocker, dynamic-evidence level, and goal-readiness gap."
        )
    output = str(
        item.get("verify_outcome")
        or item.get("verify_stage")
        or item.get("stop_reason")
        or spec.get("expected_artifact")
        or ""
    )
    next_plan = str(
        item.get("replan_next_action")
        or item.get("stop_reason")
        or item.get("replan_policy")
        or _format_list(_list(spec.get("next_possible_actions")))
    )
    decision = {
        "iteration": _int(item.get("iteration", iteration)),
        "source": "agent_auto_trace",
        "action_id": action_id,
        "canonical_action_id": canonical_id,
        "registered": bool(spec),
        "phase": str(item.get("plan_action_phase") or spec.get("phase") or ""),
        "tool": str(item.get("plan_action_tool") or spec.get("tool") or ""),
        "why_selected": why,
        "confidence": confidence,
        "confidence_reason": confidence_reason,
        "risk": _risk_for_action(action_id),
        "input": _auto_trace_input_summary(item),
        "output": output,
        "blocker": blocker,
        "blocker_present": bool(blocker),
        "next_plan": next_plan,
        "executable_now": bool(item.get("plan_executable_now", False)),
        "executed": bool(item.get("auto_executed", False)),
        "verify_status": str(item.get("verify_status") or item.get("verify_outcome") or ""),
        "replan_policy": str(item.get("replan_policy") or ""),
    }
    decision["complete"] = _action_decision_item_complete(decision)
    return decision


def _controller_action_input_summary(
    *,
    summary: dict[str, Any],
    readiness: dict[str, Any],
    fault: dict[str, Any],
    observation_count: int,
) -> str:
    goal = _dict(summary.get("agent_goal_readiness"))
    return (
        f"stage={readiness.get('current_stage') or 'unknown'}; "
        f"blocker={_effective_blocker(readiness) or 'none'}; "
        f"dynamic={readiness.get('dynamic_evidence_level') or 'none'}; "
        f"fault={fault.get('mode') or 'none'}/{fault.get('status') or 'none'}; "
        f"top={fault.get('top_function') or 'none'}; "
        f"goal={goal.get('status') or 'none'}; "
        f"observations={observation_count}"
    )


def _auto_trace_input_summary(item: dict[str, Any]) -> str:
    return (
        f"stage={item.get('observe_stage') or 'unknown'}; "
        f"blocker={item.get('observe_blocker') or 'none'}; "
        f"dynamic={item.get('observe_dynamic_evidence_level') or 'none'}; "
        "fault="
        f"{item.get('observe_fault_localization_mode') or 'none'}/"
        f"{item.get('observe_fault_localization_status') or 'none'}; "
        f"goal={item.get('observe_agent_goal_readiness_status') or 'none'}"
    )


def _action_output_summary(
    *,
    action_id: str,
    spec: dict[str, Any],
    verification: dict[str, Any],
) -> str:
    expected = str(
        verification.get("expected_artifact")
        or spec.get("expected_artifact")
        or ""
    )
    condition = str(
        verification.get("success_condition")
        or spec.get("success_condition")
        or ""
    )
    if expected and condition:
        return f"{expected}; success={condition}"
    return expected or condition or f"{action_id or 'action'} updates artifacts"


def _action_next_plan_summary(
    *,
    selected_action: dict[str, Any],
    replan: dict[str, Any],
    termination: dict[str, Any],
    spec: dict[str, Any],
) -> str:
    next_action = str(termination.get("next_action") or "")
    if next_action:
        return next_action
    fallback = str(replan.get("fallback_action") or "")
    policy = str(replan.get("next_policy") or "")
    if fallback and policy:
        return f"{policy} -> {fallback}"
    if policy:
        return policy
    possible = _format_list(_list(spec.get("next_possible_actions")))
    if possible != "none":
        return possible
    return str(selected_action.get("command") or selected_action.get("id") or "")


def _action_confidence(
    *,
    readiness: dict[str, Any],
    fault: dict[str, Any],
    selected_action: dict[str, Any],
    verification: dict[str, Any],
    blocker: str,
    registered: bool,
) -> tuple[float, str]:
    action_id = str(selected_action.get("id") or "")
    status = str(verification.get("status") or "")
    score = 0.66 if registered else 0.52
    reasons = ["registered" if registered else "unregistered_action"]
    if status == "verified":
        score = max(score, 0.95)
        reasons.append("verified_repair_or_phase_completion")
    elif bool(selected_action.get("executable_now", False)):
        score += 0.10
        reasons.append("executable_now")
    else:
        reasons.append("external_or_blocked_action")
    if _is_environment_blocker(blocker) and action_id in {
        "await_environment_repair",
        "prepare_repository_test_environment",
    }:
        score += 0.12
        reasons.append("matches_environment_blocker")
    elif blocker and canonical_action_id(action_id) == "emit_blocker_report":
        score += 0.14
        reasons.append("terminal_blocker_route")
    elif blocker:
        score -= 0.04
        reasons.append("blocker_present")
    if str(fault.get("mode") or "") == "dynamic" and str(
        fault.get("status") or ""
    ) == "pass":
        score += 0.08
        reasons.append("dynamic_localization_ready")
    elif str(fault.get("mode") or "") == "static_fallback":
        score += 0.04
        reasons.append("static_fallback_available")
    return max(0.05, min(0.99, round(score, 2))), ",".join(reasons)


def _auto_action_confidence(item: dict[str, Any]) -> tuple[float, str]:
    executed = bool(item.get("auto_executed", False))
    progress = bool(item.get("verify_progress", False))
    stop_reason = str(item.get("stop_reason") or "")
    registered = bool(action_spec_for(item.get("plan_selected_action")))
    score = 0.66 if registered else 0.52
    reasons = ["registered" if registered else "unregistered_action"]
    if executed and progress:
        score += 0.22
        reasons.append("executed_with_progress")
    elif executed:
        score += 0.10
        reasons.append("executed")
    elif stop_reason:
        score -= 0.12
        reasons.append("stopped_with_reason")
    else:
        reasons.append("planned_or_stopped")
    if str(item.get("observe_blocker") or ""):
        score -= 0.02
        reasons.append("blocker_present")
    return max(0.05, min(0.99, round(score, 2))), ",".join(reasons)


def _risk_for_action(action_id: Any) -> str:
    canonical = canonical_action_id(action_id)
    if canonical in {
        "run_repository_tests",
        "diagnose_environment",
        "validate_patch_in_sandbox",
        "generate_llm_patch_candidates",
        "generate_hybrid_patch_candidates",
        "run_llm_patch_reflection_loop",
        "clone_or_load_repository",
    }:
        return "medium"
    return "low"


def _action_decision_item_complete(item: dict[str, Any]) -> bool:
    return bool(
        str(item.get("action_id") or "")
        and str(item.get("why_selected") or "")
        and _float(item.get("confidence", 0.0)) > 0.0
        and str(item.get("risk") or "")
        and str(item.get("input") or "")
        and str(item.get("output") or "")
        and "blocker_present" in item
        and str(item.get("next_plan") or "")
    )


def _loop_iteration_audit(
    summary: dict[str, Any],
    *,
    readiness: dict[str, Any],
    fault: dict[str, Any],
    selected_action: dict[str, Any],
    verification: dict[str, Any],
    reflection: dict[str, Any],
    replan: dict[str, Any],
    termination: dict[str, Any],
) -> dict[str, Any]:
    auto_trace = [_dict(item) for item in _list(summary.get("agent_auto_trace"))]
    if auto_trace:
        source = "agent_auto_trace"
        iterations = [_loop_iteration_from_auto_trace(item) for item in auto_trace]
    else:
        source = "agent_controller"
        iterations = [
            _loop_iteration_from_controller(
                readiness=readiness,
                fault=fault,
                selected_action=selected_action,
                verification=verification,
                reflection=reflection,
                replan=replan,
                termination=termination,
            )
        ]
    complete_count = sum(1 for item in iterations if bool(item.get("complete", False)))
    executed_count = sum(
        1 for item in iterations if str(item.get("act_status") or "") == "executed"
    )
    stopped_count = sum(
        1 for item in iterations if str(item.get("act_status") or "") == "stopped"
    )
    status = "pass" if complete_count == len(iterations) else "warning"
    return {
        "status": status,
        "reason": (
            "controller_loop_iteration_audit_complete"
            if status == "pass"
            else "controller_loop_iteration_audit_incomplete"
        ),
        "source": source,
        "loop": AGENT_LOOP,
        "iteration_count": len(iterations),
        "complete_iteration_count": complete_count,
        "executed_iteration_count": executed_count,
        "stopped_iteration_count": stopped_count,
        "iterations": iterations,
    }


def _loop_iteration_from_auto_trace(item: dict[str, Any]) -> dict[str, Any]:
    iteration = _int(item.get("iteration", 0))
    action_id = str(item.get("plan_selected_action") or "")
    executed = bool(item.get("auto_executed", False))
    stop_reason = str(item.get("stop_reason") or "")
    stop_category = str(item.get("stop_category") or "")
    act_status = "executed" if executed else "stopped"
    observe = (
        f"stage={item.get('observe_stage') or 'unknown'}; "
        f"blocker={item.get('observe_blocker') or 'none'}; "
        f"dynamic={item.get('observe_dynamic_evidence_level') or 'none'}; "
        "llm_patch="
        f"{item.get('observe_repository_llm_patch_generation_status') or 'none'}; "
        "llm_reflection="
        f"{item.get('observe_repository_llm_reflection_status') or 'none'}; "
        f"goal={item.get('observe_agent_goal_readiness_status') or 'none'}"
    )
    plan = (
        f"action={action_id or 'none'}; "
        f"phase={item.get('plan_action_phase') or 'none'}; "
        f"tool={item.get('plan_action_tool') or 'none'}; "
        f"executable={str(bool(item.get('plan_executable_now', False))).lower()}"
    )
    act = (
        f"status={act_status}; executed={str(executed).lower()}; "
        f"stop={stop_category or stop_reason or 'none'}"
    )
    verify_outcome = str(
        item.get("verify_outcome")
        or item.get("verify_stage")
        or stop_reason
        or ""
    )
    verify = (
        f"outcome={verify_outcome or 'none'}; "
        f"progress={str(bool(item.get('verify_progress', False))).lower()}; "
        f"goal={item.get('verify_agent_goal_readiness_status') or 'none'}"
    )
    reflect = (
        f"status={item.get('reflect_status') or ('stopped' if stop_reason else 'none')}; "
        f"failure={item.get('reflect_failure_type') or 'none'}; "
        f"strategy={item.get('reflect_strategy') or 'none'}"
    )
    replan = (
        f"policy={item.get('replan_policy') or stop_reason or 'none'}; "
        f"next={item.get('replan_next_action') or stop_reason or 'none'}"
    )
    complete = _loop_iteration_complete(
        observe=observe,
        plan=plan,
        act=act,
        verify=verify,
        reflect=reflect,
        replan=replan,
    )
    return {
        "iteration": iteration,
        "source": "agent_auto_trace",
        "action_id": action_id,
        "act_status": act_status,
        "complete": complete,
        "observe": observe,
        "plan": plan,
        "act": act,
        "verify": verify,
        "reflect": reflect,
        "replan": replan,
    }


def _loop_iteration_from_controller(
    *,
    readiness: dict[str, Any],
    fault: dict[str, Any],
    selected_action: dict[str, Any],
    verification: dict[str, Any],
    reflection: dict[str, Any],
    replan: dict[str, Any],
    termination: dict[str, Any],
) -> dict[str, Any]:
    action_id = str(selected_action.get("id") or "")
    observe = (
        f"stage={readiness.get('current_stage') or 'unknown'}; "
        f"blocker={readiness.get('blocker') or 'none'}; "
        f"dynamic={readiness.get('dynamic_evidence_level') or 'none'}; "
        "timeout_narrowing="
        f"{readiness.get('repository_test_timeout_narrowing_status') or 'none'}; "
        f"fault={fault.get('mode') or 'none'}/{fault.get('status') or 'none'}"
    )
    plan = (
        f"action={action_id or 'none'}; "
        f"phase={selected_action.get('phase') or 'none'}; "
        f"tool={selected_action.get('tool') or 'none'}; "
        f"executable={str(bool(selected_action.get('executable_now', False))).lower()}"
    )
    act = (
        "status=planned; executed=false; "
        f"command={selected_action.get('command') or 'none'}"
    )
    verify = (
        f"status={verification.get('status') or 'none'}; "
        f"condition={verification.get('success_condition') or 'none'}; "
        f"artifact={verification.get('expected_artifact') or 'none'}"
    )
    reflect = (
        f"status={reflection.get('status') or 'ready'}; "
        f"hypothesis={reflection.get('failure_hypothesis') or 'none'}; "
        f"fallback={reflection.get('fallback_action') or 'none'}"
    )
    replan_text = (
        f"policy={replan.get('next_policy') or 'none'}; "
        f"trigger={replan.get('trigger') or 'none'}; "
        f"next={termination.get('next_action') or selected_action.get('command') or 'none'}"
    )
    complete = _loop_iteration_complete(
        observe=observe,
        plan=plan,
        act=act,
        verify=verify,
        reflect=reflect,
        replan=replan_text,
    )
    return {
        "iteration": 1,
        "source": "agent_controller",
        "action_id": action_id,
        "act_status": "planned",
        "complete": complete,
        "observe": observe,
        "plan": plan,
        "act": act,
        "verify": verify,
        "reflect": reflect,
        "replan": replan_text,
    }


def _loop_iteration_complete(
    *,
    observe: str,
    plan: str,
    act: str,
    verify: str,
    reflect: str,
    replan: str,
) -> bool:
    return all(
        bool(value and "none" != value.strip())
        for value in (observe, plan, act, verify, reflect, replan)
    )


def _auto_controller_summary(summary: dict[str, Any]) -> dict[str, Any]:
    loop_audit = _dict(summary.get("agent_auto_loop_audit"))
    stop_state = _dict(summary.get("agent_auto_stop_state"))
    return {
        "enabled": bool(summary.get("agent_auto_enabled", False)),
        "max_actions": _int(summary.get("agent_auto_max_actions", 0)),
        "action_count": _int(summary.get("agent_auto_action_count", 0)),
        "stop_reason": str(summary.get("agent_auto_stop_reason") or ""),
        "stop_state": stop_state,
        "stop_category": str(stop_state.get("category") or ""),
        "stop_action_id": str(stop_state.get("action_id") or ""),
        "stop_recovery_policy": str(stop_state.get("recovery_policy") or ""),
        "stop_requires_user_action": bool(
            stop_state.get("requires_user_action", False)
        ),
        "stop_requires_environment_change": bool(
            stop_state.get("requires_environment_change", False)
        ),
        "stop_external_input_kind": str(
            stop_state.get("external_input_kind") or ""
        ),
        "stop_recommended_next_action": str(
            stop_state.get("recommended_next_action") or ""
        ),
        "stop_recommended_next_command": str(
            stop_state.get("recommended_next_command") or ""
        ),
        "trace_count": len(_list(summary.get("agent_auto_trace"))),
        "progress_count": _int(loop_audit.get("progress_count", 0)),
        "no_progress_count": _int(loop_audit.get("no_progress_count", 0)),
        "complete_loop_recorded": bool(
            loop_audit.get("complete_loop_recorded", False)
        ),
        "verify_outcome_counts": _dict(loop_audit.get("verify_outcome_counts")),
        "replan_policy_counts": _dict(loop_audit.get("replan_policy_counts")),
        "goal_readiness_status_counts": _dict(
            loop_audit.get("goal_readiness_status_counts")
        ),
        "goal_readiness_passed_action_count": _int(
            loop_audit.get("goal_readiness_passed_action_count", 0)
        ),
        "final_goal_readiness_status": str(
            loop_audit.get("final_goal_readiness_status") or ""
        ),
    }


def _auto_trace_failure_overlay_cell(item: dict[str, Any]) -> str:
    status = str(item.get("verify_failure_overlay_status") or "")
    reason = str(item.get("verify_failure_overlay_reason") or "")
    if not status and not reason:
        return "none"
    if reason:
        return f"{status or 'unknown'}({reason})"
    return status


def _auto_action_failure_overlay_cell(item: dict[str, Any]) -> str:
    status = str(item.get("after_failure_overlay_status") or "")
    reason = str(item.get("after_failure_overlay_reason") or "")
    supported = _int(item.get("after_failure_overlay_supported_candidates", 0))
    attempted = _int(item.get("after_failure_overlay_attempted_cases", 0))
    dynamic_level = str(item.get("after_failure_overlay_dynamic_evidence_level") or "")
    if not any([status, reason, supported, attempted, dynamic_level]):
        return "none"
    label = status or "unknown"
    if reason:
        label = f"{label}({reason})"
    counts = f"supported={supported}, attempted={attempted}"
    if dynamic_level:
        counts = f"{counts}, dynamic={dynamic_level}"
    return f"{label}; {counts}"


def _auto_trace_timeout_narrowing_cell(item: dict[str, Any]) -> str:
    status = str(item.get("verify_timeout_narrowing_status") or "")
    reason = str(item.get("verify_timeout_narrowing_reason") or "")
    attempts = _int(item.get("verify_timeout_narrowing_attempt_count", 0))
    selected = str(item.get("verify_timeout_narrowing_selected_failure_category") or "")
    if not any([status, reason, attempts, selected]):
        return "none"
    label = status or "unknown"
    if reason:
        label = f"{label}({reason})"
    return f"{label}; attempts={attempts}, selected={selected or 'none'}"


def _auto_action_timeout_narrowing_cell(item: dict[str, Any]) -> str:
    status = str(item.get("after_timeout_narrowing_status") or "")
    reason = str(item.get("after_timeout_narrowing_reason") or "")
    attempts = _int(item.get("after_timeout_narrowing_attempt_count", 0))
    selected = str(item.get("after_timeout_narrowing_selected_failure_category") or "")
    if not any([status, reason, attempts, selected]):
        return "none"
    label = status or "unknown"
    if reason:
        label = f"{label}({reason})"
    return f"{label}; attempts={attempts}, selected={selected or 'none'}"


def _dynamic_fault_localization_needed(
    summary: dict[str, Any],
    readiness: dict[str, Any],
    fault: dict[str, Any],
) -> bool:
    if str(fault.get("mode") or "") == "dynamic" and str(
        fault.get("status") or ""
    ) == "pass":
        return False
    dynamic_fault_status = str(
        summary.get("repository_test_fault_localization_status") or ""
    )
    if dynamic_fault_status == "pass":
        return False
    if bool(readiness.get("dynamic_evidence_usable_for_localization", False)):
        return True
    dynamic_level = str(readiness.get("dynamic_evidence_level") or "")
    failed = _int(readiness.get("planned_repository_test_result_failed", 0))
    errors = _int(readiness.get("planned_repository_test_result_errors", 0))
    result_status = str(readiness.get("planned_repository_test_result_status") or "")
    return bool(
        dynamic_level in {"failing_tests", "traceback", "assertion_failure"}
        and (failed > 0 or errors > 0 or result_status in {"fail", "error"})
    )


def _dynamic_fault_localization_action(
    summary: dict[str, Any],
    readiness: dict[str, Any],
) -> dict[str, Any]:
    dynamic_level = str(readiness.get("dynamic_evidence_level") or "none")
    dynamic_fault_status = str(
        summary.get("repository_test_fault_localization_status") or "not_ready"
    )
    dynamic_fault_reason = str(
        readiness.get("dynamic_fault_localization_reason")
        or summary.get("repository_test_fault_localization_reason")
        or ""
    )
    failed = _int(readiness.get("planned_repository_test_result_failed", 0))
    errors = _int(readiness.get("planned_repository_test_result_errors", 0))
    return _action(
        "build_dynamic_fault_localization",
        "phase2",
        "repository_test_fault_localization",
        (
            "Rerun repository-test analysis to rebuild "
            "repository_test_fault_localization.json from usable dynamic "
            "evidence."
        ),
        (
            "Usable failing-test dynamic evidence is available "
            f"(level={dynamic_level}, failed={failed}, errors={errors}), "
            "but dynamic Top-k localization is not ready "
            f"(status={dynamic_fault_status}, reason={dynamic_fault_reason or 'none'})."
        ),
        "medium",
    )


def _timeout_narrowing_value(
    summary: dict[str, Any],
    readiness: dict[str, Any],
    key: str,
) -> Any:
    value = readiness.get(key)
    if value is not None and value != "":
        return value
    return summary.get(key)


def _timeout_narrowing_needed(
    summary: dict[str, Any],
    readiness: dict[str, Any],
) -> bool:
    if bool(readiness.get("dynamic_evidence_usable_for_localization", False)):
        return False
    selected_category = str(
        _timeout_narrowing_value(
            summary,
            readiness,
            "repository_test_timeout_narrowing_selected_failure_category",
        )
        or ""
    )
    narrowing_reason = str(
        _timeout_narrowing_value(
            summary,
            readiness,
            "repository_test_timeout_narrowing_reason",
        )
        or ""
    )
    if (
        narrowing_reason == "timeout_narrowing_selected_non_timeout_result"
        and selected_category != "timeout"
    ):
        return False
    text = " ".join(
        [
            str(readiness.get("blocker") or ""),
            str(readiness.get("planned_repository_test_failure_category") or ""),
            str(readiness.get("planned_repository_test_result_status") or ""),
            str(readiness.get("planned_repository_test_failure_signal") or ""),
            narrowing_reason,
        ]
    ).lower()
    return "timeout" in text


def _timeout_narrowing_action(
    base: str,
    summary: dict[str, Any],
    readiness: dict[str, Any],
) -> dict[str, Any]:
    planned_command = str(readiness.get("planned_repository_test_command") or "")
    narrowing_status = str(
        _timeout_narrowing_value(
            summary,
            readiness,
            "repository_test_timeout_narrowing_status",
        )
        or "not_attempted"
    )
    narrowing_reason = str(
        _timeout_narrowing_value(
            summary,
            readiness,
            "repository_test_timeout_narrowing_reason",
        )
        or "none"
    )
    attempts = _int(
        _timeout_narrowing_value(
            summary,
            readiness,
            "repository_test_timeout_narrowing_attempt_count",
        )
    )
    return _action(
        "narrow_repository_tests_after_timeout",
        "phase3",
        "repository_test_timeout_narrowing",
        (
            f"{base} --execution-profile phase3-fast --checkout-repository-tests "
            "--run-repository-test-command --auto-repository-test-retry "
            "--run-repository-test-retry --format markdown "
            "--require-analysis-ready"
        ),
        (
            "Repository test execution timed out before producing usable "
            "dynamic evidence; build or refresh repository_test_timeout_narrowing "
            "with narrower pytest file/nodeid attempts "
            f"(planned_command={planned_command or 'none'}, "
            f"status={narrowing_status}, reason={narrowing_reason}, "
            f"attempts={attempts})."
        ),
        "medium",
    )


def _retry_repository_checkout_or_use_cache_action(
    base: str,
    readiness: dict[str, Any],
) -> dict[str, Any]:
    checkout_reason = str(
        readiness.get("repository_checkout_failure_reason")
        or readiness.get("planned_repository_test_failure_signal")
        or readiness.get("blocker")
        or "checkout_failed"
    )
    return _action(
        "retry_repository_checkout_or_use_cache",
        "source_discovery",
        "github_repo_intelligence",
        (
            f"{base} --execution-profile checkout --repository-checkout-depth 1 "
            "--prefer-cached-discovery --format markdown "
            "# if checkout still fails, rerun source-only analysis from cached discovery/raw sources"
        ),
        (
            "Repository checkout failed before tests or full materialization "
            f"could proceed (reason={checkout_reason}); retry a shallow "
            "checkout, reuse cached discovery, or fall back to raw/tree source "
            "analysis before declaring the repo unanalyzable."
        ),
        "medium",
    )


def _test_execution_failure_action(
    base: str,
    readiness: dict[str, Any],
) -> dict[str, Any]:
    result_status = str(readiness.get("planned_repository_test_result_status") or "")
    failure_category = str(
        readiness.get("planned_repository_test_failure_category") or "unknown"
    )
    return _action(
        "diagnose_test_execution_failure",
        "phase3",
        "repository_test_execution_result",
        (
            f"{base} --execution-profile phase3-fast --checkout-repository-tests "
            "--run-repository-test-command --auto-repository-test-retry "
            "--format markdown --require-analysis-ready"
        ),
        (
            "Repository tests failed or errored, but the current evidence is "
            "not yet usable for localization "
            f"(status={result_status or 'unknown'}, category={failure_category}). "
            "Refresh the execution result, classify the failure, and build "
            "dynamic localization if the failure is a real test assertion."
        ),
        "medium",
    )


def _application_source_focus_needed(
    summary: dict[str, Any],
    fault: dict[str, Any],
) -> bool:
    if str(fault.get("mode") or "") != "static_fallback":
        return False
    if str(fault.get("status") or "") not in {"pass", "warning"}:
        return False
    if _int(fault.get("ranked_function_count", 0)) <= 0 and not _list(
        fault.get("rankings")
    ):
        return False
    role_counts = _dict(fault.get("source_role_counts"))
    top_role = str(fault.get("top_source_role") or "")
    if not role_counts and not top_role:
        return False
    if _int(fault.get("application_candidate_count", 0)) > 0:
        return False
    if top_role == "application":
        return False
    if not top_role and role_counts.get("application"):
        return False
    if not _application_source_focus_can_broaden(summary):
        return False
    return bool(role_counts or top_role)


def _application_source_focus_action(
    base_command: str,
    summary: dict[str, Any],
    fault: dict[str, Any],
) -> dict[str, Any]:
    top_role = str(fault.get("top_source_role") or "non_application")
    role_counts = _format_counts(_dict(fault.get("source_role_counts")))
    source_hints = _application_source_hints(summary)
    hints_text = _format_list(source_hints) if source_hints else "none"
    command = (
        f"{base_command} --preset mining --max-sources 200 --max-candidates 50 "
        "--format markdown --require-analysis-ready "
        "# if Top-k still has no application candidates, rerun with "
        "--include/--exclude/--target-prefix aimed at package/src code or provide failing evidence"
    )
    return _action(
        "adjust_application_source_focus",
        "phase2",
        "github_repo_intelligence",
        command,
        (
            "Static Top-k currently contains no application-source candidates "
            f"(top_source_role={top_role}, roles={role_counts or 'none'}, "
            f"application_hints={hints_text}). Broaden or retarget source "
            "mining before treating automation/test findings as application bugs."
        ),
        "low",
    )


def _application_source_hints(summary: dict[str, Any]) -> list[str]:
    structure = _dict(summary.get("repository_structure"))
    package_structure = _dict(structure.get("package_structure"))
    hints: list[str] = []
    for item in [
        *_list(package_structure.get("src_layout_packages")),
        *_list(package_structure.get("package_roots")),
        package_structure.get("recommended_target_prefix"),
    ]:
        text = str(item or "")
        if text and text not in hints:
            hints.append(text)
    return hints


def _application_source_focus_can_broaden(summary: dict[str, Any]) -> bool:
    for item in _list(summary.get("agent_auto_actions")):
        if str(_dict(item).get("action_id") or "") == "adjust_application_source_focus":
            return False
    invocation = _dict(summary.get("agent_invocation"))
    if not invocation:
        return True
    if _list(invocation.get("include")) or _list(invocation.get("exclude")):
        return True
    if str(invocation.get("target_prefix") or ""):
        return True
    max_sources = _int(invocation.get("max_sources", 0))
    max_candidates = _int(invocation.get("max_candidates", 0))
    if max_sources and max_sources < 50:
        return True
    if max_candidates and max_candidates < 20:
        return True
    return False


def _failure_overlay_action(base_command: str) -> dict[str, Any]:
    return _action(
        "generate_controlled_failure_overlay",
        "phase3",
        "repository_test_failure_overlay",
        (
            f"{base_command} --execution-profile phase3-fast "
            "--repository-test-failure-overlay-candidate-limit 5 "
            "--format markdown --require-analysis-ready"
        ),
        (
            "Natural repository tests do not provide a localizable failing "
            "test; generate a controlled failure overlay from static findings "
            "to create dynamic localization evidence."
        ),
        "medium",
    )


def _failure_overlay_blocker_action(summary: dict[str, Any]) -> dict[str, Any]:
    reason = str(summary.get("repository_test_failure_overlay_reason") or "")
    supported = _int(
        summary.get("repository_test_failure_overlay_supported_candidates", 0)
    )
    attempted = _int(
        summary.get("repository_test_failure_overlay_attempted_cases", 0)
    )
    extension = _dict(
        summary.get("repository_test_failure_overlay_next_actionable_extension")
    )
    extension_text = str(
        extension.get("recommendation")
        or extension.get("action")
        or extension.get("reason")
        or ""
    )
    next_action = (
        extension_text
        or "Provide a failing test, mutation ground truth, bug report, or extend the controlled failure-overlay rule support."
    )
    return _action(
        "extend_failure_overlay_or_provide_bug_report",
        "phase3",
        "repository_test_failure_overlay",
        next_action,
        (
            "Controlled failure overlay was attempted but did not produce "
            f"usable failing-test evidence ({reason or 'overlay_not_usable'}; "
            f"supported={supported}, attempted={attempted})."
        ),
        "medium",
        executable_now=False,
    )


def _failure_overlay_can_be_generated(
    summary: dict[str, Any],
    readiness: dict[str, Any],
) -> bool:
    if _failure_overlay_attempted(summary):
        return False
    dynamic_level = str(readiness.get("dynamic_evidence_level") or "")
    if dynamic_level in {"", "none", "not_executed", "failing_tests"}:
        return False
    if bool(readiness.get("dynamic_evidence_usable_for_localization", False)):
        return False
    if _is_environment_blocker(_effective_blocker(readiness)):
        return False
    static_signal_count = _int(readiness.get("static_signal_count", 0))
    if static_signal_count <= 0:
        static_signal_count = _int(summary.get("selected_signal_count", 0))
    if static_signal_count <= 0:
        static_signal_count = _int(summary.get("total_signal_count", 0))
    return static_signal_count > 0


def _failure_overlay_exhausted(summary: dict[str, Any]) -> bool:
    status = str(summary.get("repository_test_failure_overlay_status") or "")
    if status == "pass":
        return False
    if status in {"skipped", "fail", "error", "blocked"}:
        return _failure_overlay_attempted(summary)
    return False


def _failure_overlay_attempted(summary: dict[str, Any]) -> bool:
    status = str(summary.get("repository_test_failure_overlay_status") or "")
    reason = str(summary.get("repository_test_failure_overlay_reason") or "")
    attempted = _int(
        summary.get("repository_test_failure_overlay_attempted_cases", 0)
    )
    supported = _int(
        summary.get("repository_test_failure_overlay_supported_candidates", 0)
    )
    return bool(status or reason or attempted > 0 or supported > 0)


def _reflection_strategy_hint(summary: dict[str, Any]) -> str:
    reflection = _dict(summary.get("reflection_summary"))
    strategy_id = str(reflection.get("primary_reflection_strategy_id") or "")
    action = str(reflection.get("primary_reflection_strategy_action") or "")
    if not strategy_id and not action:
        return ""
    if strategy_id and action:
        return f" Primary reflection strategy `{strategy_id}`: {action}"
    if strategy_id:
        return f" Primary reflection strategy `{strategy_id}`."
    return f" Primary reflection strategy: {action}"


def _reflection_loop_action_id(summary: dict[str, Any]) -> str:
    return (
        "run_llm_patch_reflection_loop"
        if _llm_reflection_ready(summary)
        else "run_patch_reflection_loop"
    )


def _reflection_loop_command(summary: dict[str, Any]) -> str:
    if _llm_reflection_ready(summary):
        return (
            "Rerun repository_test_patch_validation with LLM reflection enabled "
            "and at least one reflection round."
        )
    return (
        "Rerun repository_test_patch_validation with rule reflection enabled "
        "and at least one reflection round."
    )


def _llm_reflection_ready(summary: dict[str, Any]) -> bool:
    audit = _dict(summary.get("repository_llm_reflection_audit"))
    reflection = _dict(summary.get("reflection_summary"))
    mode = str(
        summary.get("repository_test_patch_validation_reflection_mode")
        or summary.get("repository_test_reflection_mode")
        or reflection.get("reflection_mode")
        or audit.get("mode")
        or ""
    ).lower()
    if mode != "llm":
        return False
    status = str(
        summary.get("repository_llm_reflection_status")
        or reflection.get("reflection_refiner_status")
        or audit.get("status")
        or ""
    )
    reason = str(
        summary.get("repository_llm_reflection_reason")
        or reflection.get("reflection_refiner_reason")
        or audit.get("reason")
        or ""
    )
    blocked = bool(
        summary.get("repository_llm_reflection_blocked", False)
        or audit.get("blocked", False)
        or reason.startswith("missing_api_key:")
        or reason == "missing_llm_api_key"
    )
    return status in {"ready", "pass"} and not blocked


def _patch_generation_action(
    summary: dict[str, Any],
    readiness: dict[str, Any],
    *,
    command: str,
    reason: str,
    llm_patch_hint: str,
) -> dict[str, Any]:
    mode = str(summary.get("repository_patch_generation_mode") or "rule").lower()
    if mode not in {"llm", "hybrid"}:
        return _action(
            "generate_and_validate_patches",
            "phase3",
            "repository_test_patch_validation",
            command,
            reason + llm_patch_hint,
            "medium",
            executable_now=bool(readiness.get("can_attempt_patch_repair", False)),
        )

    audit = _dict(summary.get("repository_llm_patch_generation_audit"))
    status = str(
        summary.get("repository_llm_patch_generation_status")
        or audit.get("status")
        or ""
    )
    blocker = str(
        summary.get("repository_llm_patch_blocker")
        or audit.get("blocker")
        or summary.get("repository_llm_patch_generation_reason")
        or audit.get("reason")
        or ""
    )
    api_key_env = str(
        summary.get("repository_llm_patch_api_key_env")
        or audit.get("api_key_env")
        or "CIA_LLM_API_KEY"
    )
    api_key_env_options = _llm_api_key_env_options(
        summary,
        audit,
        primary_env=api_key_env,
        summary_key="repository_llm_patch_checked_api_key_envs",
    )
    api_key_env_text = _format_env_options(api_key_env_options)
    provider = str(
        summary.get("repository_llm_patch_provider")
        or audit.get("provider")
        or "deepseek"
    )
    model = str(
        summary.get("repository_llm_patch_model")
        or audit.get("model")
        or "deepseek-v4-pro"
    )
    blocked = bool(
        summary.get("repository_llm_patch_blocked", False)
        or audit.get("blocked", False)
        or status == "blocked"
        or status == "error"
        or blocker == "missing_llm_api_key"
        or blocker.startswith("missing_api_key:")
        or _int(summary.get("repository_llm_patch_failure_count") or 0) > 0
    )
    failure_policy = _llm_patch_failure_policy(summary, audit)
    if mode == "llm" and blocked:
        if failure_policy:
            return _llm_provider_failure_action(
                policy=failure_policy,
                command=command,
                reason=reason,
                llm_patch_hint=llm_patch_hint,
                readiness=readiness,
            )
        return _action(
            "configure_llm_patch_api_key",
            "phase3",
            "llm_config_audit",
            (
                f"Set one of {api_key_env_text}, then rerun "
                "LLM patch generation."
            ),
            (
                f"LLM-only patch generation is requested, but `{provider}/{model}` "
                f"is blocked by `{blocker or 'llm_patch_key_missing'}`. "
                "The Agent must not report rule or empty candidates as LLM repair "
                "success."
            ),
            "low",
            executable_now=False,
        )
    generator_counts = _dict(summary.get("repository_patch_generator_counts"))
    rule_fallback_count = _int(
        summary.get("repository_patch_generator_rule_count")
        or summary.get("repository_patch_generator_rule_candidate_count")
        or generator_counts.get("rule")
        or 0
    )
    if mode == "hybrid" and failure_policy and rule_fallback_count <= 0:
        return _llm_provider_failure_action(
            policy=failure_policy,
            command=command,
            reason=reason,
            llm_patch_hint=llm_patch_hint,
            readiness=readiness,
        )

    action_id = (
        "generate_llm_patch_candidates"
        if mode == "llm"
        else "generate_hybrid_patch_candidates"
    )
    action_reason = (
        f"{reason} Patch generation mode is `{mode}`; AgentController must "
        f"invoke `{provider}/{model}` for LLM candidates and keep all generated "
        "patches behind AST/scope/safety gates and sandbox pytest validation."
    )
    if blocked:
        action_reason += (
            f" LLM candidate generation is currently blocked by "
            f"`{blocker or 'llm_patch_key_missing'}`; hybrid mode may keep "
            "rule fallback candidates, but the report must not count them as "
            "LLM repair."
        )
    return _action(
        action_id,
        "phase3",
        "repository_test_patch_candidates",
        command,
        action_reason + llm_patch_hint,
        "medium",
        executable_now=bool(readiness.get("can_attempt_patch_repair", False)),
    )


def _llm_provider_failure_action(
    *,
    policy: dict[str, Any],
    command: str,
    reason: str,
    llm_patch_hint: str,
    readiness: dict[str, Any],
) -> dict[str, Any]:
    action_id = str(policy.get("action_id") or "diagnose_llm_provider_failure")
    recovery_command = str(policy.get("command") or command)
    recovery_reason = (
        f"{reason} LLM provider failure is classified as "
        f"`{policy.get('failure_class') or 'unknown'}` from "
        f"`{policy.get('primary_error') or 'unknown'}`; "
        f"recovery policy is `{policy.get('recovery_policy') or 'none'}`. "
        + str(policy.get("reason") or "")
        + llm_patch_hint
    )
    action = _action(
        action_id,
        "phase3",
        (
            "repository_test_patch_candidates"
            if action_id == "retry_llm_patch_generation"
            else "llm_provider_diagnostics"
        ),
        recovery_command,
        recovery_reason,
        str(policy.get("risk") or "medium"),
        executable_now=bool(
            policy.get("executable_now", False)
            and readiness.get("can_attempt_patch_repair", False)
        ),
    )
    action["llm_provider_failure_class"] = str(policy.get("failure_class") or "")
    action["llm_provider_primary_error"] = str(policy.get("primary_error") or "")
    action["llm_provider_recovery_policy"] = str(
        policy.get("recovery_policy") or ""
    )
    action["replan_trigger"] = str(policy.get("replan_trigger") or "")
    action["replan_policy"] = str(policy.get("replan_policy") or "")
    return action


def _llm_patch_generation_hint(summary: dict[str, Any]) -> str:
    mode = str(summary.get("repository_patch_generation_mode") or "").lower()
    if mode not in {"llm", "hybrid"}:
        return ""
    audit = _dict(summary.get("repository_llm_patch_generation_audit"))
    status = str(
        summary.get("repository_llm_patch_generation_status")
        or audit.get("status")
        or ""
    )
    reason = str(
        summary.get("repository_llm_patch_generation_reason")
        or audit.get("reason")
        or ""
    )
    provider = str(
        summary.get("repository_llm_patch_provider")
        or audit.get("provider")
        or "unknown-provider"
    )
    model = str(
        summary.get("repository_llm_patch_model")
        or audit.get("model")
        or "unknown-model"
    )
    api_key_env = str(
        summary.get("repository_llm_patch_api_key_env")
        or audit.get("api_key_env")
        or "CIA_LLM_API_KEY"
    )
    api_key_env_text = _format_env_options(
        _llm_api_key_env_options(
            summary,
            audit,
            primary_env=api_key_env,
            summary_key="repository_llm_patch_checked_api_key_envs",
        )
    )
    fallback_used = bool(
        summary.get("repository_llm_patch_generation_fallback_used", False)
        or audit.get("fallback_used", False)
    )
    if status == "blocked" and reason == "missing_llm_api_key":
        if fallback_used:
            return (
                f" LLM patch generation `{provider}/{model}` is blocked by "
                f"missing one of {api_key_env_text}, but hybrid mode has rule-based "
                "fallback candidates."
            )
        return (
            f" LLM patch generation `{provider}/{model}` is blocked by "
            f"missing one of {api_key_env_text}; configure the key or switch to rule/hybrid "
            "fallback before expecting LLM candidates."
        )
    if status == "error":
        policy = _llm_patch_failure_policy(summary, audit)
        return (
            f" LLM patch generation `{provider}/{model}` failed with "
            f"`{reason or 'unknown_error'}`"
            f" ({policy.get('failure_class') or 'unknown'}); "
            f"{_llm_patch_telemetry_summary(summary, audit)}."
        )
    if status == "pass":
        return (
            f" LLM patch generation `{provider}/{model}` produced candidates "
            "that remain subject to safety gates and sandbox validation."
        )
    return ""


def _llm_patch_telemetry_summary(
    summary: dict[str, Any],
    audit: dict[str, Any] | None = None,
) -> str:
    audit = _dict(audit)
    telemetry = _dict(
        summary.get("repository_llm_patch_generation_telemetry")
    ) or _dict(audit.get("telemetry"))
    request_count = _int(
        summary.get("repository_llm_patch_request_count")
        or audit.get("request_count")
        or telemetry.get("request_count")
        or 0
    )
    failure_count = _int(
        summary.get("repository_llm_patch_failure_count")
        or audit.get("failure_count")
        or telemetry.get("failure_count")
        or 0
    )
    total_tokens = _int(
        summary.get("repository_llm_patch_total_tokens")
        or audit.get("total_tokens")
        or telemetry.get("total_tokens")
        or 0
    )
    estimated_tokens = _int(
        summary.get("repository_llm_patch_estimated_total_tokens")
        or audit.get("estimated_total_tokens")
        or telemetry.get("estimated_total_tokens")
        or 0
    )
    error_counts = _dict(
        summary.get("repository_llm_patch_error_reason_counts")
    ) or _dict(audit.get("error_reason_counts")) or _dict(
        telemetry.get("error_reason_counts")
    )
    return (
        f"requests={request_count}, failures={failure_count}, "
        f"tokens={total_tokens}, estimated_tokens={estimated_tokens}, "
        f"errors={_format_counts(error_counts) or 'none'}"
    )


def _llm_patch_failure_policy(
    summary: dict[str, Any],
    audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    audit = _dict(audit)
    status = str(
        summary.get("repository_llm_patch_generation_status")
        or audit.get("status")
        or ""
    ).lower()
    reason = str(
        summary.get("repository_llm_patch_generation_reason")
        or audit.get("reason")
        or ""
    )
    telemetry = _dict(
        summary.get("repository_llm_patch_generation_telemetry")
    ) or _dict(audit.get("telemetry"))
    error_counts = _dict(
        summary.get("repository_llm_patch_error_reason_counts")
    ) or _dict(audit.get("error_reason_counts")) or _dict(
        telemetry.get("error_reason_counts")
    )
    failure_count = _int(
        summary.get("repository_llm_patch_failure_count")
        or audit.get("failure_count")
        or telemetry.get("failure_count")
        or 0
    )
    if not (
        status in {"error", "failed", "unavailable"}
        or failure_count > 0
        or error_counts
    ):
        return {}
    primary_error = _primary_llm_error_reason(reason, error_counts)
    failure_class = _llm_provider_failure_class(primary_error, reason)
    provider = str(
        summary.get("repository_llm_patch_provider")
        or audit.get("provider")
        or "unknown-provider"
    )
    model = str(
        summary.get("repository_llm_patch_model")
        or audit.get("model")
        or "unknown-model"
    )
    telemetry_text = _llm_patch_telemetry_summary(summary, audit)
    if failure_class in {"credential", "provider_config"}:
        policy = {
            "action_id": "diagnose_llm_provider_failure",
            "recovery_policy": (
                "refresh_llm_credentials_or_model_access"
                if failure_class == "credential"
                else "verify_llm_base_url_model_and_request_schema"
            ),
            "replan_policy": "repair_llm_provider_configuration",
            "risk": "low",
            "executable_now": False,
            "command": (
                f"Verify `{provider}/{model}` credentials, base URL, model "
                "access, and environment-variable wiring; then rerun bounded "
                "LLM patch generation."
            ),
            "reason": (
                "This failure requires provider configuration or credential "
                "repair before the Agent can safely call the model again."
            ),
        }
    elif failure_class == "rate_limit":
        policy = {
            "action_id": "retry_llm_patch_generation",
            "recovery_policy": "retry_after_backoff_with_smaller_candidate_budget",
            "replan_policy": "retry_llm_generation_after_provider_backoff",
            "risk": "medium",
            "executable_now": True,
            "command": (
                "Rerun repository_test_patch_candidates after provider backoff "
                "with repository_llm_patch_candidate_limit=1 and the same "
                "Top-k localized context."
            ),
            "reason": (
                "Rate-limit failures are transient, so the Agent should retry "
                "with a bounded candidate budget before switching to hybrid "
                "fallback or blocker."
            ),
        }
    elif failure_class == "timeout":
        policy = {
            "action_id": "retry_llm_patch_generation",
            "recovery_policy": "retry_with_smaller_context_or_higher_timeout",
            "replan_policy": "reduce_llm_context_then_retry_generation",
            "risk": "medium",
            "executable_now": True,
            "command": (
                "Rerun repository_test_patch_candidates with candidate_limit=1, "
                "a narrower Top-k function slice, or an increased CIA_LLM_TIMEOUT."
            ),
            "reason": (
                "Timeout indicates the prompt or provider latency exceeded the "
                "current budget; retry must reduce context or explicitly raise "
                "the timeout."
            ),
        }
    elif failure_class == "response_schema":
        policy = {
            "action_id": "retry_llm_patch_generation",
            "recovery_policy": "retry_with_strict_json_patch_schema",
            "replan_policy": "tighten_llm_output_schema_then_retry",
            "risk": "medium",
            "executable_now": True,
            "command": (
                "Rerun LLM patch generation with strict JSON-only instructions "
                "and keep AST/scope/safety gates before sandbox validation."
            ),
            "reason": (
                "The provider responded, but the response was not usable as a "
                "structured patch candidate."
            ),
        }
    elif failure_class in {"network", "provider_transient"}:
        policy = {
            "action_id": "retry_llm_patch_generation",
            "recovery_policy": "retry_transient_provider_or_network_failure_once",
            "replan_policy": "retry_or_switch_provider_after_transient_failure",
            "risk": "medium",
            "executable_now": True,
            "command": (
                "Rerun bounded LLM patch generation once; if the same provider "
                "failure repeats, switch provider/model or fall back to hybrid "
                "rule candidates."
            ),
            "reason": (
                "The failure is likely outside patch semantics, so the Agent "
                "should retry once with telemetry preserved."
            ),
        }
    else:
        policy = {
            "action_id": "diagnose_llm_provider_failure",
            "recovery_policy": "classify_unknown_llm_provider_failure",
            "replan_policy": "classify_llm_failure_before_retry",
            "risk": "medium",
            "executable_now": False,
            "command": (
                "Inspect llm_generation_telemetry and provider configuration "
                "before retrying LLM patch generation."
            ),
            "reason": "The provider failure class is unknown and needs diagnosis.",
        }
    return {
        **policy,
        "failure_class": failure_class,
        "primary_error": primary_error,
        "telemetry": telemetry,
        "error_reason_counts": error_counts,
        "replan_trigger": f"llm_patch_provider_failure:{failure_class}",
        "provider": provider,
        "model": model,
        "telemetry_summary": telemetry_text,
    }


def _primary_llm_error_reason(reason: str, error_counts: dict[str, Any]) -> str:
    if error_counts:
        items = sorted(
            ((str(key), _int(value)) for key, value in error_counts.items()),
            key=lambda item: (-item[1], item[0]),
        )
        if items and items[0][0]:
            return items[0][0]
    return reason or "unknown"


def _llm_provider_failure_class(primary_error: str, reason: str) -> str:
    text = f"{primary_error} {reason}".lower()
    if "missing_llm_api_key" in text or "missing_api_key" in text:
        return "credential"
    if "http_401" in text or "http_403" in text:
        return "credential"
    if "http_404" in text or "model_not_found" in text:
        return "provider_config"
    if "http_429" in text or "rate_limit" in text:
        return "rate_limit"
    if "timeout" in text:
        return "timeout"
    if (
        "invalid_json_response" in text
        or "missing_chat_completion_content" in text
        or "invalid_text_response" in text
    ):
        return "response_schema"
    if (
        "url_error" in text
        or "connection" in text
        or "dns" in text
        or "network" in text
        or "proxy" in text
    ):
        return "network"
    if any(f"http_5{digit}" in text for digit in range(10)):
        return "provider_transient"
    if "http_400" in text:
        return "provider_config"
    return "unknown"


def _llm_reflection_hint(summary: dict[str, Any]) -> str:
    audit = _dict(summary.get("repository_llm_reflection_audit"))
    mode = str(
        summary.get("repository_test_patch_validation_reflection_mode")
        or audit.get("mode")
        or ""
    ).lower()
    if mode != "llm":
        return ""
    status = str(
        summary.get("repository_llm_reflection_status")
        or audit.get("status")
        or ""
    )
    reason = str(
        summary.get("repository_llm_reflection_reason")
        or audit.get("reason")
        or ""
    )
    provider = str(
        summary.get("repository_llm_reflection_provider")
        or audit.get("provider")
        or "unknown-provider"
    )
    model = str(
        summary.get("repository_llm_reflection_model")
        or audit.get("model")
        or "unknown-model"
    )
    api_key_env = str(
        summary.get("repository_llm_reflection_api_key_env")
        or audit.get("api_key_env")
        or "CIA_LLM_API_KEY"
    )
    api_key_env_text = _format_env_options(
        _llm_api_key_env_options(
            summary,
            audit,
            primary_env=api_key_env,
            summary_key="repository_llm_reflection_checked_api_key_envs",
        )
    )
    if bool(audit.get("blocked", False)) or reason.startswith("missing_api_key:"):
        if reason.startswith("missing_api_key:"):
            return (
                f" LLM reflection `{provider}/{model}` is blocked by missing "
                f"one of {api_key_env_text}; configure the key or use rule reflection "
                "before expecting refined LLM patches."
            )
        return (
            f" LLM reflection `{provider}/{model}` is blocked by "
            f"`{reason or status or 'reflection_refiner_unavailable'}`."
        )
    if status == "ready":
        return (
            f" LLM reflection `{provider}/{model}` is configured, but refined "
            "patches still need sandbox evidence."
        )
    return ""


def _can_collect_repository_test_evidence(readiness: dict[str, Any]) -> bool:
    if bool(readiness.get("can_attempt_dynamic_tests", False)):
        return True
    blocker = str(readiness.get("repository_test_setup_doctor_blocker") or "")
    if blocker.startswith("checkout:"):
        return True
    command = str(readiness.get("planned_repository_test_command") or "")
    return bool(command)


def _runner_fallback_executable(readiness: dict[str, Any]) -> bool:
    return bool(
        readiness.get("planned_repository_test_runner_fallback_used", False)
        and readiness.get("planned_repository_test_executable_now", False)
        and str(readiness.get("planned_repository_test_command") or "")
        and not _dynamic_test_attempted(readiness)
    )


def _dynamic_test_attempted(readiness: dict[str, Any]) -> bool:
    dynamic_level = str(readiness.get("dynamic_evidence_level") or "")
    result_status = str(readiness.get("planned_repository_test_result_status") or "")
    return bool(
        dynamic_level not in {"", "none", "not_executed"}
        or result_status not in {"", "skipped", "not_executed"}
    )


def _patch_candidates_blocked_by_safety_gate(readiness: dict[str, Any]) -> bool:
    blocker = str(readiness.get("blocker") or "")
    reason = str(readiness.get("patch_validation_reason") or "")
    safe_candidate_count = _int(readiness.get("patch_validation_candidate_count", 0))
    blocked_count = _int(
        readiness.get("patch_validation_safety_blocked_candidate_count", 0)
    )
    return bool(
        blocker.startswith("patch_candidates_blocked_by_safety_gate")
        or reason == "all_candidates_blocked_by_safety_gate"
        or (blocked_count > 0 and safe_candidate_count <= 0)
    )


def _patch_candidates_not_ready(readiness: dict[str, Any]) -> bool:
    blocker = str(readiness.get("blocker") or "")
    reason = str(readiness.get("patch_validation_reason") or "")
    status = str(readiness.get("patch_validation_status") or "")
    input_count = _int(readiness.get("patch_validation_input_candidate_count", 0))
    candidate_count = _int(readiness.get("patch_validation_candidate_count", 0))
    return bool(
        blocker == "patch_candidates_not_ready"
        or reason == "patch_candidates_not_ready"
        or (
            status in {"", "skipped", "not_ready"}
            and input_count <= 0
            and candidate_count <= 0
            and blocker.startswith("patch_candidates")
        )
    )


def _effective_blocker(readiness: dict[str, Any]) -> str:
    if _runner_fallback_executable(readiness):
        return str(readiness.get("blocker") or "")
    primary = str(readiness.get("blocker") or "")
    setup_blocker = str(readiness.get("repository_test_setup_doctor_blocker") or "")
    if _is_environment_blocker(setup_blocker):
        return setup_blocker
    return primary or setup_blocker


def _is_environment_blocker(blocker: str) -> bool:
    return bool(
        "environment" in blocker
        or "test_tool_missing" in blocker
        or "missing_dependency" in blocker
        or "dependency_setup_failed" in blocker
        or blocker.startswith("setup_install_failure:")
        or blocker.startswith("execution_failure:missing_dependency")
    )


def _is_checkout_failure_blocker(blocker: str) -> bool:
    return bool(
        blocker == "checkout_failed"
        or blocker.startswith("checkout_failed:")
        or blocker.startswith("checkout_failure:")
        or blocker.startswith("repository_checkout_failed")
    )


def _is_test_execution_failure_blocker(
    readiness: dict[str, Any],
    blocker: str,
) -> bool:
    if blocker not in {
        "test_execution_failed",
        "dynamic_evidence_not_usable:collection_failure",
    } and not blocker.startswith("test_execution_failed:"):
        return False
    if bool(readiness.get("dynamic_evidence_usable_for_localization", False)):
        return False
    text = " ".join(
        [
            str(readiness.get("planned_repository_test_result_status") or ""),
            str(readiness.get("planned_repository_test_failure_category") or ""),
            str(readiness.get("planned_repository_test_failure_signal") or ""),
        ]
    ).lower()
    return bool(
        any(token in text for token in ["fail", "error", "collection"])
        and "timeout" not in text
    )


def _is_test_discovery_blocker(
    readiness: dict[str, Any],
    blocker: str,
) -> bool:
    text = " ".join(
        [
            str(blocker or ""),
            str(readiness.get("repository_test_setup_doctor_blocker") or ""),
            str(readiness.get("planned_repository_test_failure_category") or ""),
            str(readiness.get("planned_repository_test_result_status") or ""),
        ]
    )
    command = str(readiness.get("planned_repository_test_command") or "")
    return bool(
        "test_command:" in text
        or "no_test_command" in text
        or "no_recommended_test_command" in text
        or "no_tests_collected" in text
        or (
            str(readiness.get("current_stage") or "")
            == "phase2_static_graph_fault_localization"
            and str(readiness.get("dynamic_evidence_level") or "") in {
                "",
                "none",
                "not_executed",
            }
            and not command
        )
    )


def _verification(
    selected_action: dict[str, Any],
    readiness: dict[str, Any],
    fault: dict[str, Any],
) -> dict[str, str]:
    current_stage = str(readiness.get("current_stage") or "")
    action_id = str(selected_action.get("id") or "")
    if current_stage == "phase3_patch_validation" and bool(
        readiness.get("repair_ready", False)
    ):
        status = "verified"
        evidence = "Patch validation produced a fully repair-ready candidate."
    elif current_stage == "phase3_patch_validation":
        status = "repair_not_verified"
        evidence = (
            "Patch validation artifact is available, but repair_ready is false "
            f"({readiness.get('repair_validation_scope') or readiness.get('blocker') or 'not_verified'})."
        )
    elif bool(selected_action.get("executable_now", False)):
        status = "pending_action"
        evidence = "Selected action must produce or refresh downstream artifacts."
    else:
        status = "manual_or_blocked"
        evidence = "Selected action requires external inspection or environment changes."
    return {
        "status": status,
        "evidence": evidence,
        "success_condition": _verification_success_condition(action_id),
        "expected_artifact": _verification_expected_artifact(action_id, fault),
    }


def _reflection(
    selected_action: dict[str, Any],
    readiness: dict[str, Any],
    fault: dict[str, Any],
) -> dict[str, str]:
    blocker = _effective_blocker(readiness)
    current_stage = str(readiness.get("current_stage") or "")
    action_id = str(selected_action.get("id") or "")
    status = "ready"
    trigger = "selected action fails, produces no new artifact, or blocker changes"
    if current_stage == "phase3_patch_validation" and bool(
        readiness.get("repair_ready", False)
    ):
        status = "verified_progress"
        hypothesis = (
            "Patch validation already produced a repair-ready candidate; no "
            "corrective reflection is required before Phase 4 evaluation."
        )
        trigger = "phase completed or new validation evidence changes"
        fallback = "run_search_and_ablation_evaluation"
    elif _is_environment_blocker(blocker):
        hypothesis = "Repository test runner or dependency setup is incomplete."
        fallback = "prepare_repository_test_environment"
    elif action_id == "retry_repository_checkout_or_use_cache":
        hypothesis = (
            "Repository checkout failed before full materialization, so the "
            "Agent should retry shallow checkout or fall back to cached/raw "
            "source discovery."
        )
        fallback = "adjust_source_filters"
    elif action_id == "diagnose_test_execution_failure":
        hypothesis = (
            "Repository tests failed, but the current failure evidence has not "
            "yet been converted into localizable dynamic evidence."
        )
        fallback = "collect_dynamic_failure_evidence"
    elif action_id == "generate_controlled_failure_overlay":
        hypothesis = (
            "Natural dynamic evidence is not localizable, so a controlled "
            "failure overlay may be needed to create a precise failing pytest scope."
        )
        fallback = "generate_controlled_failure_overlay"
    elif action_id == "extend_failure_overlay_or_provide_bug_report":
        hypothesis = (
            "Controlled failure overlay did not produce usable failing-test "
            "evidence with the currently supported rule set."
        )
        fallback = "await_failing_test_or_bug_report"
    elif action_id == "expand_static_candidate_search":
        hypothesis = (
            "Static candidate search may be too narrow for the selected source "
            "slice or rule family."
        )
        fallback = "adjust_source_filters"
    elif action_id == "adjust_application_source_focus":
        hypothesis = (
            "Static Top-k localization is dominated by non-application source "
            "roles, so the current source focus may not expose the real "
            "business-code target."
        )
        fallback = "collect_dynamic_failure_evidence"
    elif action_id == "narrow_repository_tests_after_timeout":
        hypothesis = (
            "The broad repository test command timed out before yielding "
            "localizable evidence, so the Agent should try narrower pytest "
            "file/nodeid scopes or adjust the timeout before repair."
        )
        fallback = "narrow_repository_tests_after_timeout"
    elif action_id == "diagnose_llm_provider_failure":
        hypothesis = (
            "LLM patch generation reached the provider but failed before usable "
            "candidate generation, so credentials, model access, base URL, or "
            "response contract must be diagnosed before another unsafe retry."
        )
        fallback = "emit_blocker_report"
    elif action_id == "retry_llm_patch_generation":
        hypothesis = (
            "LLM patch generation failed with a recoverable provider class; a "
            "bounded retry should reduce context, apply provider backoff, or "
            "tighten the JSON patch schema."
        )
        fallback = "diagnose_llm_provider_failure"
    elif str(fault.get("mode") or "") == "static_fallback":
        hypothesis = "Only static suspicious-function evidence is available."
        fallback = "collect_dynamic_failure_evidence"
    elif _patch_candidates_blocked_by_safety_gate(readiness):
        hypothesis = (
            "Patch candidates violated the pre-sandbox safety gate, so sandbox "
            "validation was intentionally skipped."
        )
        fallback = "regenerate_safe_patch_candidates"
    elif blocker.startswith("patch_validation_not_repair_ready"):
        hypothesis = (
            "A patch candidate passed partial validation, but regression "
            "validation did not prove a safe repair."
        )
        fallback = "run_patch_reflection_loop"
    elif action_id in {
        "generate_and_validate_patches",
        "run_patch_reflection_loop",
        "run_llm_patch_reflection_loop",
        "expand_patch_candidates_or_reflection",
    }:
        hypothesis = "Patch generation may fail if localized function has no supported repair rule."
        fallback = "expand_patch_candidates_or_reflection"
    else:
        hypothesis = "Current blocker needs a narrower diagnostic artifact."
        fallback = "inspect_generated_artifacts"
    return {
        "status": status,
        "failure_hypothesis": hypothesis,
        "replan_trigger": trigger,
        "fallback_action": fallback,
    }


def _replan(
    selected_action: dict[str, Any],
    readiness: dict[str, Any],
    reflection: dict[str, Any],
    verification: dict[str, Any],
    goal_readiness: dict[str, Any] | None = None,
) -> dict[str, str]:
    blocker = _effective_blocker(readiness) or "none"
    status = str(verification.get("status") or "")
    goal = _dict(goal_readiness)
    failed_goal_criteria = _list(goal.get("failed_criteria"))
    action_replan_trigger = str(selected_action.get("replan_trigger") or "")
    action_replan_policy = str(selected_action.get("replan_policy") or "")
    if status == "verified":
        trigger = "phase_completed"
        next_policy = "advance_to_next_stage"
    elif action_replan_trigger:
        trigger = action_replan_trigger
        next_policy = action_replan_policy or "recover_from_selected_action_blocker"
    elif blocker and blocker != "none":
        trigger = blocker
        next_policy = "classify_blocker_and_select_recovery_action"
    elif failed_goal_criteria:
        trigger = f"agent_goal_readiness:{failed_goal_criteria[0]}"
        next_policy = "close_agent_goal_readiness_gap"
    else:
        trigger = "artifact_state_changed"
        next_policy = "rerun_observe_plan_after_action"
    return {
        "status": "ready",
        "trigger": trigger,
        "next_policy": next_policy,
        "fallback_action": str(reflection.get("fallback_action") or ""),
        "current_action": str(selected_action.get("id") or ""),
    }


def _termination(
    selected_action: dict[str, Any],
    readiness: dict[str, Any],
) -> dict[str, str]:
    if str(readiness.get("current_stage") or "") == "phase3_patch_validation" and bool(
        readiness.get("repair_ready", False)
    ):
        return {
            "status": "ready_for_phase4",
            "reason": "Patch validation produced a fully repair-ready candidate.",
            "next_action": str(selected_action.get("command") or ""),
        }
    if str(readiness.get("current_stage") or "") == "phase3_patch_validation":
        return {
            "status": "blocked",
            "reason": str(readiness.get("blocker") or "patch_repair_not_verified"),
            "next_action": str(selected_action.get("command") or ""),
        }
    if str(selected_action.get("id") or "") == "inspect_generated_artifacts":
        return {
            "status": "blocked",
            "reason": str(readiness.get("blocker") or "unknown_blocker"),
            "next_action": str(readiness.get("next_action") or ""),
        }
    if str(selected_action.get("id") or "") == "await_failing_test_or_bug_report":
        return {
            "status": "blocked",
            "reason": "passing_tests_registered_without_failing_evidence",
            "next_action": str(selected_action.get("command") or ""),
        }
    if str(selected_action.get("id") or "") == "extend_failure_overlay_or_provide_bug_report":
        return {
            "status": "blocked",
            "reason": "failure_overlay_not_usable",
            "next_action": str(selected_action.get("command") or ""),
        }
    if str(selected_action.get("id") or "") == "retry_with_github_token_or_cache":
        return {
            "status": "blocked",
            "reason": str(readiness.get("blocker") or "github_fetch_blocked"),
            "next_action": str(selected_action.get("command") or ""),
        }
    if str(selected_action.get("id") or "") == "diagnose_llm_provider_failure":
        return {
            "status": "blocked",
            "reason": str(
                selected_action.get("llm_provider_failure_class")
                or "llm_provider_failure"
            ),
            "next_action": str(selected_action.get("command") or ""),
        }
    if str(selected_action.get("id") or "") == "await_environment_repair":
        return {
            "status": "blocked",
            "reason": "environment_repair_plan_recorded",
            "next_action": str(selected_action.get("command") or ""),
        }
    return {
        "status": "continue",
        "reason": "Controller selected a next action; rerun after the action updates artifacts.",
        "next_action": str(selected_action.get("command") or ""),
    }


def _decision_trace(
    *,
    observations: list[dict[str, str]],
    selected_action: dict[str, Any],
    verification: dict[str, Any],
    reflection: dict[str, Any],
    replan: dict[str, Any],
    termination: dict[str, Any],
) -> list[dict[str, str]]:
    action_id = str(selected_action.get("id") or "")
    return [
        {
            "phase": "observe",
            "status": "complete",
            "evidence": f"{len(observations)} controller observations",
            "decision": "summarize repository, analysis, test, repair, and blocker state",
            "output": "normalized readiness state",
        },
        {
            "phase": "plan",
            "status": "complete",
            "evidence": "analysis_readiness + fault_localization",
            "decision": f"select `{action_id or 'none'}`",
            "output": str(selected_action.get("phase") or "unknown"),
        },
        {
            "phase": "act",
            "status": "selected",
            "evidence": str(selected_action.get("tool") or "none"),
            "decision": str(selected_action.get("reason") or ""),
            "output": str(selected_action.get("command") or ""),
        },
        {
            "phase": "verify",
            "status": str(verification.get("status") or ""),
            "evidence": str(verification.get("evidence") or ""),
            "decision": str(verification.get("success_condition") or ""),
            "output": str(verification.get("expected_artifact") or ""),
        },
        {
            "phase": "reflect",
            "status": str(reflection.get("status") or "ready"),
            "evidence": str(reflection.get("failure_hypothesis") or ""),
            "decision": str(reflection.get("replan_trigger") or ""),
            "output": str(reflection.get("fallback_action") or ""),
        },
        {
            "phase": "replan",
            "status": str(replan.get("status") or ""),
            "evidence": str(replan.get("trigger") or ""),
            "decision": str(replan.get("next_policy") or ""),
            "output": str(termination.get("next_action") or ""),
        },
    ]


def _planner_strategy(
    summary: dict[str, Any],
    *,
    client: LLMClient | None,
    enabled: bool | None,
    explicit: str | None,
) -> str:
    invocation = _dict(summary.get("agent_invocation"))
    configured = str(
        explicit
        or summary.get("planner_mode")
        or invocation.get("planner_mode")
        or os.environ.get("CIA_AGENT_PLANNER_MODE")
        or ""
    ).strip().lower()
    if configured:
        if configured not in {"rule", "llm", "hybrid"}:
            return "rule"
        return configured
    if enabled is False:
        return "rule"
    if (
        client is not None
        or enabled is True
        or _env_flag("CIA_LLM_REPLAN_ENABLED")
        or _agent_auto_llm_planner_enabled(summary)
    ):
        return "hybrid"
    return "rule"


def _planner_budget_context(summary: dict[str, Any]) -> dict[str, Any]:
    invocation = _dict(summary.get("agent_invocation"))
    action_limit = _int(
        summary.get("agent_auto_max_actions")
        or invocation.get("auto_controller_max_actions")
        or 0
    )
    actions_used = _int(
        summary.get("agent_auto_action_count")
        or len(_list(summary.get("agent_auto_actions")))
    )
    time_limit = _float(
        summary.get("agent_time_budget_seconds")
        or invocation.get("agent_time_budget_seconds")
        or 0.0
    )
    elapsed = _float(summary.get("agent_elapsed_seconds") or 0.0)
    cost_limit = _float(
        summary.get("agent_llm_cost_budget_usd")
        or invocation.get("agent_llm_cost_budget_usd")
        or 0.0
    )
    cost_used = _float(summary.get("agent_llm_cost_used_usd") or 0.0)
    remaining_actions = max(0, action_limit - actions_used) if action_limit else None
    remaining_time = max(0.0, time_limit - elapsed) if time_limit else None
    remaining_cost = max(0.0, cost_limit - cost_used) if cost_limit else None
    exhausted_reasons = []
    if remaining_actions == 0:
        exhausted_reasons.append("action_budget_exhausted")
    if remaining_time == 0.0:
        exhausted_reasons.append("time_budget_exhausted")
    if remaining_cost == 0.0:
        exhausted_reasons.append("llm_cost_budget_exhausted")
    return {
        "action_limit": action_limit,
        "actions_used": actions_used,
        "remaining_actions": remaining_actions,
        "time_limit_seconds": time_limit,
        "elapsed_seconds": elapsed,
        "remaining_time_seconds": remaining_time,
        "llm_cost_limit_usd": cost_limit,
        "llm_cost_used_usd": cost_used,
        "remaining_llm_cost_usd": remaining_cost,
        "exhausted": bool(exhausted_reasons),
        "exhausted_reasons": exhausted_reasons,
    }


def _resolve_planner_selection(
    summary: dict[str, Any],
    *,
    rule_selected_action: dict[str, Any],
    advisor: dict[str, Any],
    planner_mode: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rule_action_id = str(rule_selected_action.get("id") or "")
    proposal = _dict(advisor.get("planner_decision"))
    gate = _dict(advisor.get("safety_gate"))
    proposal_source = str(proposal.get("proposal_source") or "")
    proposed_action = str(proposal.get("selected_action") or "")
    adopted_action = str(gate.get("adopted_action") or rule_action_id)
    use_llm = bool(
        planner_mode in {"llm", "hybrid"}
        and advisor.get("status") == "pass"
        and gate.get("status") == "pass"
        and adopted_action
        and adopted_action != rule_action_id
    )
    if use_llm:
        selected = _planner_selected_action(
            summary,
            proposal=proposal,
            action_id=adopted_action,
        )
        if not selected:
            use_llm = False
            selected = rule_selected_action
            resolution_reason = "adopted_action_could_not_be_materialized"
        else:
            resolution_reason = "safe_llm_action_adopted"
    else:
        selected = rule_selected_action
        resolution_reason = str(
            advisor.get("fallback_reason")
            or gate.get("reason")
            or "rule_planner_selected"
        )
    disagreement = bool(
        proposal_source == "llm"
        and proposed_action
        and canonical_action_id(proposed_action)
        != canonical_action_id(rule_action_id)
    )
    gate_status = str(gate.get("status") or "")
    safety_fallback = bool(
        proposal_source == "llm"
        and not use_llm
        and gate_status in {"blocked", "requires_confirmation", "advisory_only"}
    )
    fallback_used = bool(
        advisor.get("fallback_to_rule_planner", False) or safety_fallback
    )
    return selected, {
        "planner_mode": planner_mode,
        "rule_action": rule_action_id,
        "proposal_source": proposal_source,
        "llm_proposed_action": proposed_action if proposal_source == "llm" else "",
        "adopted_action": str(selected.get("id") or rule_action_id),
        "adopted_source": "llm" if use_llm else "rule",
        "disagreement": disagreement,
        "resolution_reason": resolution_reason,
        "safety_gate_status": gate_status,
        "fallback_used": fallback_used,
    }


def _planner_selected_action(
    summary: dict[str, Any],
    *,
    proposal: dict[str, Any],
    action_id: str,
) -> dict[str, Any]:
    del summary
    spec = action_spec_for(action_id)
    policy = action_execution_policy(action_id)
    if not spec or not policy.get("registered"):
        return {}
    arguments, errors = validate_action_arguments(
        action_id,
        proposal.get("arguments", {}),
    )
    if errors:
        return {}
    action = _action(
        action_id,
        str(spec.get("phase") or ""),
        str(spec.get("tool") or ""),
        f"Action Registry dispatch: {action_id}",
        str(proposal.get("reason") or "LLM planner selected this registered action."),
        str(policy.get("risk") or "medium"),
        executable_now=bool(
            policy.get("auto_executable")
            and not policy.get("requires_confirmation")
        ),
    )
    action.update(
        {
            "arguments": arguments,
            "confidence": _float(proposal.get("confidence", 0.0)),
            "expected_outcome": str(proposal.get("expected_outcome") or ""),
            "fallback_action": str(proposal.get("fallback_action") or ""),
            "termination_condition": str(
                proposal.get("termination_condition") or ""
            ),
            "required_evidence": _list(proposal.get("required_evidence")),
            "memory_used": _list(proposal.get("memory_used")),
            "proposal_source": "llm",
        }
    )
    return action


def _planner_trace(
    *,
    observations: list[dict[str, str]],
    advisor: dict[str, Any],
    resolution: dict[str, Any],
    selected_action: dict[str, Any],
) -> list[dict[str, Any]]:
    proposal = _dict(advisor.get("planner_decision"))
    gate = _dict(advisor.get("safety_gate"))
    return [
        {
            "step": "observe",
            "status": "complete",
            "evidence": observations[:40],
        },
        {
            "step": "propose",
            "status": str(advisor.get("status") or "disabled"),
            "source": str(proposal.get("proposal_source") or "rule"),
            "selected_action": str(proposal.get("selected_action") or ""),
            "reason": str(proposal.get("reason") or advisor.get("reason") or ""),
        },
        {
            "step": "safety_gate",
            "status": str(gate.get("status") or "not_requested"),
            "reason": str(gate.get("reason") or ""),
            "registered": bool(gate.get("recommended_registered", False)),
            "argument_errors": _list(gate.get("argument_errors")),
            "blocked_reasons": _list(gate.get("blocked_reasons")),
        },
        {
            "step": "adopt",
            "status": "complete",
            "selected_action": str(selected_action.get("id") or ""),
            "source": str(resolution.get("adopted_source") or "rule"),
            "reason": str(resolution.get("resolution_reason") or ""),
        },
    ]


def _planner_run_metrics(
    *,
    advisor: dict[str, Any],
    resolution: dict[str, Any],
) -> dict[str, Any]:
    metadata = _dict(advisor.get("request_metadata"))
    usage = _dict(metadata.get("usage"))
    cost = _dict(metadata.get("cost_estimate"))
    gate = _dict(advisor.get("safety_gate"))
    proposal = _dict(advisor.get("planner_decision"))
    proposal_source = str(proposal.get("proposal_source") or "")
    gate_status = str(gate.get("status") or "")
    llm_called = bool(metadata or advisor.get("status") in {"pass", "error"})
    safety_rejected = gate_status in {
        "blocked",
        "requires_confirmation",
        "advisory_only",
    }
    fallback_used = bool(advisor.get("fallback_to_rule_planner", False)) or bool(
        proposal_source == "llm"
        and str(resolution.get("adopted_source") or "rule") == "rule"
        and safety_rejected
    )
    return {
        "planner_mode": str(resolution.get("planner_mode") or "rule"),
        "selected_source": str(resolution.get("adopted_source") or "rule"),
        "llm_called": llm_called,
        "llm_total_tokens": _int(usage.get("total_tokens", 0)),
        "llm_estimated_cost_usd": _float(cost.get("estimated_cost_usd", 0.0)),
        "safety_gate_rejection_count": int(safety_rejected),
        "fallback_count": int(fallback_used),
        "invalid_action_count": int(
            proposal_source == "llm"
            and (
                "action_not_registered" in _list(gate.get("blocked_reasons"))
                or "invalid_action_arguments" in _list(gate.get("blocked_reasons"))
            )
        ),
        "provider_failure_class": str(advisor.get("provider_failure_class") or ""),
        "disagreement": bool(resolution.get("disagreement", False)),
    }


def _llm_replan_advisor(
    summary: dict[str, Any],
    *,
    readiness: dict[str, Any],
    fault: dict[str, Any],
    selected_action: dict[str, Any],
    observations: list[dict[str, str]],
    verification: dict[str, Any],
    reflection: dict[str, Any],
    replan: dict[str, Any],
    termination: dict[str, Any],
    client: LLMClient | None = None,
    enabled: bool | None = None,
    planner_mode: str = "hybrid",
    budget_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    budget_context = _dict(budget_context)
    explicit_enabled = enabled
    if explicit_enabled is None:
        explicit_enabled = (
            bool(summary.get("llm_replan_enabled", False))
            or _env_flag("CIA_LLM_REPLAN_ENABLED")
            or _agent_auto_llm_planner_enabled(summary)
        )
    active = bool(
        planner_mode in {"llm", "hybrid"}
        and (client is not None or explicit_enabled)
    )
    audit = llm_config_audit("replan", enabled=active).to_dict()
    memory_context = _llm_planner_memory_context(summary)
    base = {
        "enabled": active,
        "status": "disabled",
        "reason": "llm_replan_disabled",
        "authority": "advisory_only_controller_policy_decides",
        "planner_type": "llm_planner_replanner",
        "planner_schema_version": 1,
        "controller_authority": "rules_and_sandbox_gate_decide",
        "config": audit,
        "advice": {},
        "planner_decision": {},
        "llm_planner_proposal": {},
        "safety_gate": _llm_planner_safety_gate(
            {},
            selected_action,
            summary=summary,
            planner_mode=planner_mode,
            budget_context=budget_context,
        ),
        "request_metadata": {},
        "memory_context": memory_context,
        "fallback_to_rule_planner": False,
        "planner_mode": planner_mode,
        "budget_context": budget_context,
        "provider_failure_class": "",
    }
    if not active:
        return base
    if budget_context.get("exhausted"):
        fallback = _rule_fallback_planner_proposal(
            selected_action=selected_action,
            verification=verification,
            memory_context=memory_context,
            reason="planner_budget_exhausted",
        )
        return {
            **base,
            "status": "blocked",
            "reason": "planner_budget_exhausted",
            "blocker": "planner_budget_exhausted",
            "planner_decision": fallback,
            "llm_planner_proposal": fallback,
            "safety_gate": _llm_planner_safety_gate(
                fallback,
                selected_action,
                summary=summary,
                planner_mode=planner_mode,
                budget_context=budget_context,
            ),
            "fallback_to_rule_planner": True,
            "fallback_reason": "planner_budget_exhausted",
        }
    if client is None and not bool(audit.get("api_key_present", False)):
        fallback = _rule_fallback_planner_proposal(
            selected_action=selected_action,
            verification=verification,
            memory_context=memory_context,
            reason="missing_llm_replan_api_key",
        )
        return {
            **base,
            "status": "blocked",
            "reason": "missing_llm_replan_api_key",
            "blocker": "missing_llm_replan_api_key",
            "checked_api_key_envs": _list(audit.get("checked_api_key_envs")),
            "planner_decision": fallback,
            "llm_planner_proposal": fallback,
            "safety_gate": _llm_planner_fallback_gate(
                selected_action,
                reason="missing_llm_replan_api_key",
            ),
            "fallback_to_rule_planner": True,
            "fallback_reason": "missing_llm_replan_api_key",
            "provider_failure_class": "credential",
        }
    try:
        active_client = client or create_replan_client()
        response = active_client.complete(
            _llm_replan_prompt(
                summary,
                readiness=readiness,
                fault=fault,
                selected_action=selected_action,
                observations=observations,
                verification=verification,
                reflection=reflection,
                replan=replan,
                termination=termination,
                memory_context=memory_context,
                budget_context=budget_context,
            )
        )
    except LLMRequestError as exc:
        provider_failure_class = _llm_provider_failure_class(
            str(_dict(exc.metadata).get("error_reason") or exc.reason),
            exc.reason,
        )
        fallback = _rule_fallback_planner_proposal(
            selected_action=selected_action,
            verification=verification,
            memory_context=memory_context,
            reason=exc.reason,
        )
        return {
            **base,
            "status": "error",
            "reason": exc.reason,
            "blocker": exc.reason,
            "request_metadata": _safe_llm_metadata(exc.metadata),
            "planner_decision": fallback,
            "llm_planner_proposal": fallback,
            "fallback_to_rule_planner": True,
            "fallback_reason": exc.reason,
            "provider_failure_class": provider_failure_class,
        }
    except Exception as exc:  # pragma: no cover - defensive artifact path
        reason = type(exc).__name__
        fallback = _rule_fallback_planner_proposal(
            selected_action=selected_action,
            verification=verification,
            memory_context=memory_context,
            reason=reason,
        )
        return {
            **base,
            "status": "error",
            "reason": reason,
            "blocker": reason,
            "message": str(exc),
            "planner_decision": fallback,
            "llm_planner_proposal": fallback,
            "fallback_to_rule_planner": True,
            "fallback_reason": reason,
            "provider_failure_class": "client_error",
        }
    advice, parse_reason = _parse_llm_replan_advice(response.text)
    if not advice:
        fallback = _rule_fallback_planner_proposal(
            selected_action=selected_action,
            verification=verification,
            memory_context=memory_context,
            reason=parse_reason,
        )
        return {
            **base,
            "status": "error",
            "reason": parse_reason,
            "blocker": parse_reason,
            "request_metadata": _safe_llm_metadata(response.metadata),
            "planner_decision": fallback,
            "llm_planner_proposal": fallback,
            "fallback_to_rule_planner": True,
            "fallback_reason": parse_reason,
            "provider_failure_class": "response_schema",
        }
    planner_decision = _llm_planner_decision_from_advice(advice, memory_context)
    safety_gate = _llm_planner_safety_gate(
        advice,
        selected_action,
        summary=summary,
        planner_mode=planner_mode,
        budget_context=budget_context,
    )
    return {
        **base,
        "status": "pass",
        "reason": "llm_replan_advice_recorded",
        "advice": advice,
        "planner_decision": planner_decision,
        "llm_planner_proposal": planner_decision,
        "safety_gate": safety_gate,
        "request_metadata": _safe_llm_metadata(response.metadata),
    }


def _agent_auto_llm_planner_enabled(summary: dict[str, Any]) -> bool:
    invocation = _dict(summary.get("agent_invocation"))
    profile = str(invocation.get("effective_execution_profile") or "").lower()
    return bool(
        profile == "agent-auto"
        or invocation.get("agent_mode", False)
        or invocation.get("auto_controller_actions", False)
        or summary.get("auto_controller_actions", False)
    )


def _llm_planner_memory_context(summary: dict[str, Any]) -> dict[str, Any]:
    memory_report = _dict(summary.get("agent_memory_report"))
    layers = _dict(memory_report.get("memory_layers"))
    session_layer = _dict(layers.get("session_memory"))
    repo_layer = _dict(layers.get("repo_memory"))
    repair_layer = _dict(layers.get("repair_memory"))
    agent_session = _dict(summary.get("agent_session"))
    patch_attempts = _list(summary.get("patch_attempt_history"))
    failed_patch_count = _int(
        repair_layer.get("failed_patch_count")
        or summary.get("repository_test_patch_validation_failed_count")
        or summary.get("repository_patch_failed_count")
        or 0
    )
    constraints = _unique_strings(
        [
            *[str(item) for item in _list(session_layer.get("constraints"))],
            *[str(item) for item in _list(repair_layer.get("user_constraints"))],
            *[str(item) for item in _list(summary.get("constraints"))],
        ]
    )
    strategy_preferences = _unique_strings(
        [
            *[
                str(item)
                for item in _list(session_layer.get("repair_strategy_preferences"))
            ],
            *[str(item) for item in _list(repair_layer.get("strategy_preferences"))],
            *[
                str(item)
                for item in _list(summary.get("repair_strategy_preferences"))
            ],
        ]
    )
    failed_fingerprints = _unique_strings(
        [
            *[
                str(item)
                for item in _list(repair_layer.get("failed_patch_fingerprints"))
            ],
            *[
                str(_dict(item).get("diff_fingerprint") or "")
                for item in patch_attempts
            ],
        ]
    )[:10]
    memory_used = []
    if agent_session:
        memory_used.append("session_memory")
    if repo_layer:
        memory_used.append("repo_memory")
    if repair_layer or failed_patch_count or failed_fingerprints:
        memory_used.append("repair_memory")
    if constraints:
        memory_used.append("user_constraints")
    if strategy_preferences:
        memory_used.append("repair_strategy_preferences")
    if failed_fingerprints:
        memory_used.append("failed_patch_fingerprints")
    return {
        "available": bool(
            agent_session
            or session_layer
            or repo_layer
            or repair_layer
            or constraints
            or strategy_preferences
            or failed_fingerprints
        ),
        "sources": memory_used,
        "memory_used": memory_used,
        "session_memory": {
            "session_id": str(agent_session.get("session_id") or session_layer.get("session_id") or ""),
            "turn_count": _int(agent_session.get("turn_count") or session_layer.get("turn_count") or 0),
            "last_intent": str(session_layer.get("last_intent") or ""),
            "active_scope": str(session_layer.get("active_scope") or ""),
        },
        "repo_memory": {
            "repo": str(repo_layer.get("repo") or summary.get("repo") or summary.get("repo_spec") or ""),
            "test_command": str(repo_layer.get("test_command") or summary.get("planned_repository_test_command") or ""),
            "test_status": str(repo_layer.get("test_status") or summary.get("planned_repository_test_result_status") or ""),
            "top_suspicious_functions": _list(repo_layer.get("top_suspicious_functions"))[:5],
        },
        "repair_memory": {
            "failed_patch_count": failed_patch_count,
            "patch_attempt_count": _int(repair_layer.get("patch_attempt_count") or len(patch_attempts)),
            "failed_patch_fingerprints": failed_fingerprints,
            "latest_failure_category": str(
                repair_layer.get("latest_failure_category")
                or summary.get("planned_repository_test_failure_category")
                or ""
            ),
            "constraints": constraints,
            "repair_strategy_preferences": strategy_preferences,
        },
    }


def _rule_fallback_planner_proposal(
    *,
    selected_action: dict[str, Any],
    verification: dict[str, Any],
    memory_context: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    required = []
    expected_artifact = str(verification.get("expected_artifact") or "")
    if expected_artifact:
        required.append(expected_artifact)
    return {
        "selected_action": str(selected_action.get("id") or ""),
        "arguments": _dict(selected_action.get("arguments")),
        "reason": (
            f"LLM planner unavailable ({reason}); using rule-selected action "
            "after controller policy and sandbox safety gates."
        ),
        "confidence": _float(selected_action.get("confidence", 0.0)),
        "risk": str(selected_action.get("risk") or "medium"),
        "required_evidence": required,
        "expected_outcome": str(verification.get("success_condition") or ""),
        "fallback_action": str(selected_action.get("fallback_action") or ""),
        "termination_condition": "verified success, terminal blocker, or exhausted budget",
        "next_plan": str(selected_action.get("next_plan") or ""),
        "memory_used": _list(memory_context.get("memory_used")),
        "proposal_source": "rule_fallback",
        "fallback_reason": reason,
    }


def _llm_planner_fallback_gate(
    selected_action: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    controller_action = str(selected_action.get("id") or "")
    controller_canonical = canonical_action_id(controller_action)
    return {
        "status": "fallback",
        "reason": "llm_unavailable_rule_planner_fallback",
        "recommended_action": controller_action,
        "recommended_canonical_action": controller_canonical,
        "recommended_registered": bool(action_spec_for(controller_action)),
        "controller_action": controller_action,
        "controller_canonical_action": controller_canonical,
        "controller_registered": bool(action_spec_for(controller_action)),
        "controller_action_match": True,
        "override_requested": False,
        "override_allowed": False,
        "adopted_action": controller_action,
        "authority": "rules_and_sandbox_gate_decide",
        "fallback_reason": reason,
    }


def _llm_replan_prompt(
    summary: dict[str, Any],
    *,
    readiness: dict[str, Any],
    fault: dict[str, Any],
    selected_action: dict[str, Any],
    observations: list[dict[str, str]],
    verification: dict[str, Any],
    reflection: dict[str, Any],
    replan: dict[str, Any],
    termination: dict[str, Any],
    memory_context: dict[str, Any],
    budget_context: dict[str, Any],
) -> str:
    evidence_context = _planner_evidence_context(
        summary,
        readiness=readiness,
        fault=fault,
        memory_context=memory_context,
        budget_context=budget_context,
    )
    payload = {
        "objective": "advise_on_next_agent_replan_action",
        "user_goal": evidence_context["user_goal"],
        "repo": summary.get("repo") or summary.get("repo_spec") or "",
        "repository_profile": evidence_context["repository_profile"],
        "current_stage": readiness.get("current_stage") or "",
        "next_stage": readiness.get("next_stage") or "",
        "blocker": readiness.get("blocker") or "",
        "fault_localization": {
            "mode": fault.get("mode") or "",
            "status": fault.get("status") or "",
            "top_function": fault.get("top_function") or "",
            "top_k": evidence_context["top_k"],
            "static_rule_signals": evidence_context["static_rule_signals"],
            "program_graph_neighborhood": evidence_context[
                "program_graph_neighborhood"
            ],
        },
        "pytest_evidence": evidence_context["pytest_evidence"],
        "executed_actions": evidence_context["executed_actions"],
        "selected_action": {
            "id": selected_action.get("id") or "",
            "reason": selected_action.get("reason") or "",
            "risk": selected_action.get("risk") or "",
            "executable_now": bool(selected_action.get("executable_now", False)),
        },
        "action_registry_candidates": _planner_action_registry_candidates(
            selected_action
        ),
        "verification": verification,
        "reflection": reflection,
        "rule_replan": replan,
        "termination": termination,
        "memory_context": memory_context,
        "budget_context": budget_context,
        "observations": observations[:40],
        "response_schema": {
            "selected_action": "string action id; use same value as recommended_action when both are present",
            "recommended_action": "string",
            "arguments": "object containing only Action Registry parameters",
            "reason": "string",
            "rationale": "string fallback for reason",
            "confidence": "number from 0 to 1",
            "risk": "low|medium|high",
            "blocker": "string",
            "required_evidence": ["string evidence needed before execution"],
            "expected_outcome": "string describing verifiable post-action evidence",
            "fallback_action": "registered action id used if verification fails",
            "termination_condition": "string success, blocker, or budget stop condition",
            "next_plan": "string",
            "memory_used": ["string memory source or fact used for the decision"],
            "should_override_controller": "boolean",
        },
        "constraints": [
            "Do not invent repair success.",
            "Do not override sandbox pytest as the success authority.",
            "Prefer blocker reporting when evidence is insufficient.",
            "Use memory_context when it contains failed patches, user constraints, or repair strategy preferences.",
            "Select only actions listed in the Action Registry and never emit shell commands.",
            "Respect remaining action, time, and LLM cost budgets.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _planner_evidence_context(
    summary: dict[str, Any],
    *,
    readiness: dict[str, Any],
    fault: dict[str, Any],
    memory_context: dict[str, Any],
    budget_context: dict[str, Any],
) -> dict[str, Any]:
    invocation = _dict(summary.get("agent_invocation"))
    session = _dict(summary.get("agent_session"))
    structure = _dict(summary.get("repository_structure"))
    repo_graph = _dict(structure.get("repo_graph"))
    program_graph = _dict(repo_graph.get("program_graph"))
    answers = _dict(summary.get("agent_answers"))
    top_k = _list(fault.get("rankings")) or _list(
        answers.get("top_suspicious_functions")
    )
    static_signals = _list(fault.get("static_signals")) or _list(
        summary.get("static_rule_signals")
    )
    test_result = _dict(summary.get("repository_test_execution_result"))
    traceback = str(
        test_result.get("traceback")
        or summary.get("planned_repository_test_failure_signal")
        or summary.get("repository_test_failure_signal")
        or ""
    )
    executed_actions = []
    for item_value in _list(summary.get("agent_auto_actions"))[-20:]:
        item = _dict(item_value)
        executed_actions.append(
            {
                "action_id": str(item.get("action_id") or item.get("id") or ""),
                "status": str(
                    item.get("status")
                    or item.get("verification_status")
                    or item.get("after_status")
                    or ""
                ),
                "blocker": str(item.get("blocker") or item.get("after_blocker") or ""),
            }
        )
    return {
        "user_goal": str(
            session.get("user_goal")
            or invocation.get("user_goal")
            or summary.get("user_goal")
            or "analyze, localize, and safely repair the repository"
        ),
        "repository_profile": {
            "layout": str(structure.get("layout") or ""),
            "function_count": _int(structure.get("function_count", 0)),
            "class_count": _int(structure.get("class_count", 0)),
            "loc": _int(structure.get("loc", 0)),
            "python_file_count": _int(
                structure.get("python_file_count")
                or summary.get("source_file_count")
                or 0
            ),
        },
        "top_k": [_dict(item) for item in top_k[:10]],
        "static_rule_signals": [_dict(item) for item in static_signals[:20]],
        "program_graph_neighborhood": {
            "available": bool(program_graph.get("available", False)),
            "node_count": _int(program_graph.get("node_count", 0)),
            "edge_count": _int(program_graph.get("edge_count", 0)),
            "top_function_nodes": [
                _dict(item) for item in _list(repo_graph.get("top_function_nodes"))[:10]
            ],
        },
        "pytest_evidence": {
            "status": str(
                test_result.get("status")
                or summary.get("planned_repository_test_result_status")
                or ""
            ),
            "command": str(
                test_result.get("command")
                or summary.get("planned_repository_test_command")
                or ""
            ),
            "passed": _int(
                test_result.get("passed")
                or summary.get("planned_repository_test_result_passed")
                or 0
            ),
            "failed": _int(
                test_result.get("failed")
                or summary.get("planned_repository_test_result_failed")
                or 0
            ),
            "failure_category": str(
                test_result.get("failure_category")
                or summary.get("planned_repository_test_failure_category")
                or ""
            ),
            "traceback_tail": traceback[-4000:],
        },
        "current_blocker": str(readiness.get("blocker") or ""),
        "executed_actions": executed_actions,
        "failed_patch_fingerprints": _list(
            _dict(memory_context.get("repair_memory")).get(
                "failed_patch_fingerprints"
            )
        )[:10],
        "user_constraints": _list(
            _dict(memory_context.get("repair_memory")).get("constraints")
        )[:20],
        "budget_context": budget_context,
    }


def _planner_action_registry_candidates(
    selected_action: dict[str, Any],
) -> list[dict[str, Any]]:
    selected_id = str(selected_action.get("id") or "")
    registry = build_agent_action_registry()
    candidates = []
    for policy_value in _list(registry.get("execution_policies")):
        policy = _dict(policy_value)
        action_id = str(policy.get("action_id") or "")
        if not action_id:
            continue
        if not policy.get("auto_executable") and action_id != selected_id:
            continue
        spec = action_spec_for(action_id)
        candidates.append(
            {
                "action_id": action_id,
                "canonical_action_id": str(policy.get("canonical_action_id") or ""),
                "phase": str(spec.get("phase") or ""),
                "tool": str(spec.get("tool") or ""),
                "risk": str(policy.get("risk") or ""),
                "requires_confirmation": bool(policy.get("requires_confirmation")),
                "allowed_argument_keys": _list(
                    policy.get("allowed_argument_keys")
                ),
            }
        )
    return candidates


def _parse_llm_replan_advice(text: str) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(str(text or ""))
    except json.JSONDecodeError:
        return {}, "invalid_json_response"
    if not isinstance(payload, dict):
        return {}, "invalid_replan_schema"
    action = str(
        payload.get("selected_action") or payload.get("recommended_action") or ""
    ).strip()
    next_plan = str(payload.get("next_plan") or "").strip()
    rationale = str(payload.get("reason") or payload.get("rationale") or "").strip()
    required_fields = {
        "arguments",
        "confidence",
        "risk",
        "required_evidence",
        "expected_outcome",
        "fallback_action",
        "termination_condition",
        "memory_used",
        "next_plan",
    }
    if (
        not action
        or not rationale
        or not next_plan
        or not required_fields.issubset(payload)
    ):
        return {}, "missing_required_replan_fields"
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        return {}, "invalid_replan_arguments"
    confidence_value = payload.get("confidence")
    if (
        isinstance(confidence_value, bool)
        or not isinstance(confidence_value, (int, float))
        or not 0.0 <= float(confidence_value) <= 1.0
    ):
        return {}, "invalid_replan_confidence"
    risk_value = payload.get("risk")
    if not isinstance(risk_value, str):
        return {}, "invalid_replan_risk"
    risk = risk_value.strip().lower()
    if risk not in {"low", "medium", "high"}:
        return {}, "invalid_replan_risk"
    if not isinstance(payload.get("required_evidence"), list):
        return {}, "invalid_replan_required_evidence"
    if not isinstance(payload.get("memory_used"), list):
        return {}, "invalid_replan_memory_used"
    expected_outcome = payload.get("expected_outcome")
    fallback_action = payload.get("fallback_action")
    termination_condition = payload.get("termination_condition")
    if not isinstance(expected_outcome, str) or not expected_outcome.strip():
        return {}, "invalid_replan_expected_outcome"
    if not isinstance(fallback_action, str):
        return {}, "invalid_replan_fallback_action"
    if (
        not isinstance(termination_condition, str)
        or not termination_condition.strip()
    ):
        return {}, "invalid_replan_termination_condition"
    required_evidence = [
        str(item)
        for item in _list(payload.get("required_evidence"))
        if str(item or "").strip()
    ]
    memory_used = [
        str(item)
        for item in _list(payload.get("memory_used"))
        if str(item or "").strip()
    ]
    return (
        {
            "recommended_action": action,
            "selected_action": action,
            "arguments": arguments,
            "rationale": rationale,
            "reason": rationale,
            "confidence": float(confidence_value),
            "risk": risk,
            "blocker": str(payload.get("blocker") or "").strip(),
            "required_evidence": required_evidence,
            "expected_outcome": expected_outcome.strip(),
            "fallback_action": fallback_action.strip(),
            "termination_condition": termination_condition.strip(),
            "memory_used": memory_used,
            "next_plan": next_plan,
            "should_override_controller": bool(
                payload.get("should_override_controller", False)
            ),
        },
        "pass",
    )


def _llm_planner_decision_from_advice(
    advice: dict[str, Any],
    memory_context: dict[str, Any],
) -> dict[str, Any]:
    action = str(
        advice.get("selected_action") or advice.get("recommended_action") or ""
    )
    memory_used = [
        str(item)
        for item in _list(advice.get("memory_used"))
        if str(item or "").strip()
    ] or _list(memory_context.get("memory_used"))
    return {
        "selected_action": action,
        "arguments": _dict(advice.get("arguments")),
        "reason": str(advice.get("reason") or advice.get("rationale") or ""),
        "confidence": max(0.0, min(1.0, _float(advice.get("confidence", 0.0)))),
        "risk": str(advice.get("risk") or "medium"),
        "required_evidence": [
            str(item)
            for item in _list(advice.get("required_evidence"))
            if str(item or "").strip()
        ],
        "expected_outcome": str(advice.get("expected_outcome") or ""),
        "fallback_action": str(advice.get("fallback_action") or ""),
        "termination_condition": str(advice.get("termination_condition") or ""),
        "next_plan": str(advice.get("next_plan") or ""),
        "memory_used": memory_used,
        "proposal_source": "llm",
    }


def _llm_planner_safety_gate(
    advice: dict[str, Any],
    selected_action: dict[str, Any],
    *,
    summary: dict[str, Any] | None = None,
    planner_mode: str = "hybrid",
    budget_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = _dict(summary)
    budget_context = _dict(budget_context)
    recommended = str(
        advice.get("selected_action") or advice.get("recommended_action") or ""
    )
    controller_action = str(selected_action.get("id") or "")
    recommended_canonical = canonical_action_id(recommended)
    controller_canonical = canonical_action_id(controller_action)
    recommended_registered = bool(action_spec_for(recommended))
    controller_registered = bool(action_spec_for(controller_action))
    recommended_policy = action_execution_policy(recommended)
    normalized_arguments, argument_errors = validate_action_arguments(
        recommended,
        advice.get("arguments", {}),
    )
    confidence = max(0.0, min(1.0, _float(advice.get("confidence", 0.0))))
    action_match = bool(
        recommended
        and controller_action
        and (
            recommended == controller_action
            or recommended_canonical == controller_canonical
        )
    )
    override_requested = bool(advice.get("should_override_controller", False))
    exact_action_change = bool(recommended and recommended != controller_action)
    confidence_threshold = (
        0.75 if planner_mode == "hybrid" and exact_action_change else 0.65
    )
    transition_allowed = _planner_transition_allowed(
        recommended,
        selected_action,
    )
    repeated_state_action = _planner_repeated_state_action(summary, recommended)
    fallback_action = str(advice.get("fallback_action") or "")
    fallback_registered = not fallback_action or bool(action_spec_for(fallback_action))
    blocked_reasons = []
    if not recommended_registered:
        blocked_reasons.append("action_not_registered")
    if argument_errors:
        blocked_reasons.append("invalid_action_arguments")
    if confidence < confidence_threshold:
        blocked_reasons.append("planner_confidence_below_threshold")
    if not fallback_registered:
        blocked_reasons.append("fallback_action_not_registered")
    if budget_context.get("exhausted"):
        blocked_reasons.append("planner_budget_exhausted")
    if repeated_state_action and exact_action_change:
        blocked_reasons.append("repeated_action_in_unchanged_failure_state")
    if exact_action_change and not transition_allowed:
        blocked_reasons.append("unsafe_action_transition")
    if exact_action_change and not recommended_policy.get("auto_executable"):
        blocked_reasons.append("action_has_no_auto_executor")
    requires_confirmation = bool(recommended_policy.get("requires_confirmation"))
    if exact_action_change and requires_confirmation:
        blocked_reasons.append("high_risk_action_requires_confirmation")
    can_adopt_override = bool(
        planner_mode in {"llm", "hybrid"}
        and exact_action_change
        and not blocked_reasons
    )
    if not recommended:
        status = "not_requested"
        reason = "llm_planner_not_available"
    elif not recommended_registered:
        status = "blocked"
        reason = "llm_recommended_action_not_registered"
    elif argument_errors:
        status = "blocked"
        reason = "llm_recommended_arguments_rejected"
    elif confidence < confidence_threshold:
        status = "blocked"
        reason = "llm_planner_confidence_below_threshold"
    elif budget_context.get("exhausted"):
        status = "blocked"
        reason = "planner_budget_exhausted"
    elif repeated_state_action and exact_action_change:
        status = "blocked"
        reason = "repeated_action_in_unchanged_failure_state"
    elif not fallback_registered:
        status = "blocked"
        reason = "fallback_action_not_registered"
    elif exact_action_change and not transition_allowed:
        status = "blocked"
        reason = "unsafe_action_transition"
    elif requires_confirmation and exact_action_change:
        status = "requires_confirmation"
        reason = "high_risk_action_requires_confirmation"
    elif exact_action_change and not recommended_policy.get("auto_executable"):
        status = "blocked"
        reason = "action_has_no_auto_executor"
    elif action_match and not exact_action_change:
        status = "pass"
        reason = "llm_recommendation_matches_controller_policy"
    elif can_adopt_override:
        status = "pass"
        reason = "safe_registered_llm_alternative_adopted"
    else:
        status = "advisory_only"
        reason = "controller_policy_retains_rule_selected_action"
    adopted_action = recommended if can_adopt_override else controller_action
    return {
        "status": status,
        "reason": reason,
        "recommended_action": recommended,
        "recommended_canonical_action": recommended_canonical,
        "recommended_registered": recommended_registered,
        "controller_action": controller_action,
        "controller_canonical_action": controller_canonical,
        "controller_registered": controller_registered,
        "controller_action_match": action_match,
        "exact_action_change": exact_action_change,
        "transition_allowed": transition_allowed,
        "override_requested": bool(override_requested or exact_action_change),
        "override_allowed": can_adopt_override,
        "adopted_action": adopted_action,
        "planner_mode": planner_mode,
        "confidence": confidence,
        "confidence_threshold": confidence_threshold,
        "normalized_arguments": normalized_arguments,
        "argument_errors": argument_errors,
        "policy_risk": str(recommended_policy.get("risk") or ""),
        "model_claimed_risk": str(advice.get("risk") or ""),
        "requires_confirmation": requires_confirmation,
        "auto_executable": bool(recommended_policy.get("auto_executable")),
        "fallback_action": fallback_action,
        "fallback_registered": fallback_registered,
        "repeated_state_action": repeated_state_action,
        "budget_exhausted": bool(budget_context.get("exhausted")),
        "blocked_reasons": blocked_reasons,
        "authority": "rules_and_sandbox_gate_decide",
    }


def _planner_transition_allowed(
    recommended_action: str,
    selected_action: dict[str, Any],
) -> bool:
    controller_action = str(selected_action.get("id") or "")
    if not recommended_action or not controller_action:
        return False
    if canonical_action_id(recommended_action) == canonical_action_id(controller_action):
        return True
    recommended_spec = action_spec_for(recommended_action)
    controller_spec = action_spec_for(controller_action)
    return bool(
        recommended_spec
        and controller_spec
        and str(recommended_spec.get("phase") or "")
        == str(controller_spec.get("phase") or "")
        and str(recommended_spec.get("tool") or "")
        == str(controller_spec.get("tool") or "")
        and str(recommended_spec.get("module") or "")
        == str(controller_spec.get("module") or "")
    )


def _planner_repeated_state_action(
    summary: dict[str, Any],
    action_id: str,
) -> bool:
    if not action_id:
        return False
    readiness = _dict(summary.get("analysis_readiness"))
    stage = str(readiness.get("current_stage") or "")
    blocker = str(readiness.get("blocker") or "")
    canonical = canonical_action_id(action_id)
    current_fingerprint = _planner_state_fingerprint(summary)
    for item_value in reversed(_list(summary.get("agent_auto_trace"))[-20:]):
        item = _dict(item_value)
        previous_action = str(
            item.get("plan_selected_action")
            or item.get("selected_action")
            or item.get("action_id")
            or ""
        )
        if canonical_action_id(previous_action) != canonical:
            continue
        previous_fingerprint = str(
            item.get("observe_state_fingerprint")
            or item.get("planner_state_fingerprint")
            or ""
        )
        if previous_fingerprint:
            return previous_fingerprint == current_fingerprint
        previous_stage = str(item.get("observe_stage") or "")
        previous_blocker = str(item.get("observe_blocker") or "")
        if previous_stage == stage and previous_blocker == blocker:
            return True
    return False


def _planner_state_fingerprint(summary: dict[str, Any]) -> str:
    readiness = _dict(summary.get("analysis_readiness"))
    fault = _dict(summary.get("fault_localization"))
    test_result = _dict(summary.get("repository_test_execution_result"))
    memory_context = _llm_planner_memory_context(summary)
    state = {
        "stage": str(readiness.get("current_stage") or ""),
        "blocker": str(readiness.get("blocker") or ""),
        "dynamic_evidence_level": str(
            readiness.get("dynamic_evidence_level") or ""
        ),
        "fault_mode": str(fault.get("mode") or ""),
        "fault_status": str(fault.get("status") or ""),
        "top_function": str(fault.get("top_function") or ""),
        "test_status": str(
            test_result.get("status")
            or summary.get("planned_repository_test_result_status")
            or ""
        ),
        "test_failure_category": str(
            test_result.get("failure_category")
            or summary.get("planned_repository_test_failure_category")
            or ""
        ),
        "patch_validation_status": str(
            summary.get("repository_test_patch_validation_status") or ""
        ),
        "failed_patch_fingerprints": _list(
            _dict(memory_context.get("repair_memory")).get(
                "failed_patch_fingerprints"
            )
        )[:10],
    }
    encoded = json.dumps(
        state,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _safe_llm_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe = {}
    for key in [
        "status",
        "provider",
        "model",
        "base_url",
        "timeout_seconds",
        "latency_ms",
        "prompt_chars",
        "response_chars",
        "usage",
        "cost_estimate",
        "api_key_present",
        "api_key_fingerprint",
        "error_type",
        "error_reason",
        "http_status",
        "response_preview",
        "mode",
        "index",
    ]:
        if key in metadata:
            safe[key] = metadata[key]
    return safe


def _env_flag(name: str) -> bool:
    value = os.environ.get(name, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _verification_success_condition(action_id: str) -> str:
    if action_id == "run_repository_tests_with_checkout":
        return "repository_test_execution_result and dynamic evidence are refreshed"
    if action_id == "prepare_repository_test_environment":
        return "repository_test_environment_repair_plan records the concrete dependency or tool blocker"
    if action_id == "discover_repository_tests":
        return "repository profiling discovers a safe pytest/unittest/tox/nox command or records a no-test blocker"
    if action_id == "narrow_repository_tests_after_timeout":
        return "repository_test_timeout_narrowing records a non-timeout result or a concrete timeout blocker"
    if action_id == "retry_repository_checkout_or_use_cache":
        return "repository checkout succeeds or cached/raw source discovery records a concrete checkout blocker"
    if action_id == "diagnose_test_execution_failure":
        return "repository_test_execution_result classifies the failure and either enables dynamic localization or records a terminal test blocker"
    if action_id == "generate_controlled_failure_overlay":
        return "repository_test_failure_overlay produces usable failing-test evidence or a concrete overlay blocker"
    if action_id == "extend_failure_overlay_or_provide_bug_report":
        return "failure-overlay extension guidance or external failing evidence is provided"
    if action_id == "adjust_application_source_focus":
        return "Top-k fault localization includes application-source candidates or records that external failing evidence is required"
    if action_id == "build_dynamic_fault_localization":
        return "repository_test_fault_localization has a non-empty Top-k ranking"
    if action_id == "configure_llm_patch_api_key":
        return "LLM patch configuration records a missing-key blocker without leaking raw secrets"
    if action_id == "diagnose_llm_provider_failure":
        return "LLM provider failure is classified with a concrete recovery policy and no raw secret leakage"
    if action_id == "retry_llm_patch_generation":
        return "LLM patch generation is retried with bounded context, retry budget, and preserved telemetry"
    if action_id in {
        "generate_and_validate_patches",
        "generate_llm_patch_candidates",
        "generate_hybrid_patch_candidates",
    }:
        return "repository_test_patch_validation contains a verified passing candidate"
    if action_id == "regenerate_safe_patch_candidates":
        return "repository_test_patch_candidates contains AST-valid, scope-limited candidates that pass the safety gate"
    if action_id in {"run_patch_reflection_loop", "run_llm_patch_reflection_loop"}:
        return "reflection_trace records reflected candidates and updated patch validation"
    if action_id == "expand_patch_candidates_or_reflection":
        return "reflection_trace records refined candidates or a terminal repair blocker"
    if action_id == "convert_passing_tests_to_regression_guard":
        return "repository_test_regression_guard records passing tests as regression-only evidence"
    if action_id == "build_static_graph_fault_ranking":
        return "fault_localization.json contains static_fallback Top-k rankings"
    if action_id == "expand_static_candidate_search":
        return "broadened static mining produces candidate functions or an auditable no-candidate blocker"
    if action_id == "mine_static_bug_signals":
        return "static bug-signal mining produces candidate functions"
    if action_id == "adjust_source_filters":
        return "source import discovers analyzable Python files"
    if action_id == "retry_with_github_token_or_cache":
        return "GitHub discovery succeeds with a token, cached discovery, or after rate-limit reset"
    return "new artifacts update current blocker or terminal status"


def _verification_expected_artifact(
    action_id: str,
    fault: dict[str, Any],
) -> str:
    if action_id == "run_repository_tests_with_checkout":
        return "repository_test_dynamic_evidence.json"
    if action_id == "prepare_repository_test_environment":
        return "repository_test_environment_repair_plan.json"
    if action_id == "discover_repository_tests":
        return "repository_test_execution_plan.json"
    if action_id == "narrow_repository_tests_after_timeout":
        return "repository_test_timeout_narrowing.json"
    if action_id == "retry_repository_checkout_or_use_cache":
        return "github_repo_intelligence.json"
    if action_id == "diagnose_test_execution_failure":
        return "repository_test_execution_result.json"
    if action_id == "generate_controlled_failure_overlay":
        return "repository_test_failure_overlay.json"
    if action_id == "extend_failure_overlay_or_provide_bug_report":
        return "repository_test_failure_overlay.json"
    if action_id == "adjust_application_source_focus":
        return "fault_localization.json"
    if action_id == "build_dynamic_fault_localization":
        return "repository_test_fault_localization.json"
    if action_id == "configure_llm_patch_api_key":
        return "repository_test_patch_candidates.json"
    if action_id == "diagnose_llm_provider_failure":
        return "agent_policy_trace.json"
    if action_id == "retry_llm_patch_generation":
        return "repository_test_patch_candidates.json"
    if action_id in {
        "generate_llm_patch_candidates",
        "generate_hybrid_patch_candidates",
    }:
        return "repository_test_patch_candidates.json"
    if action_id == "generate_and_validate_patches":
        return "repository_test_patch_validation.json"
    if action_id == "regenerate_safe_patch_candidates":
        return "repository_test_patch_candidates.json"
    if action_id in {"run_patch_reflection_loop", "run_llm_patch_reflection_loop"}:
        return "reflection_trace.json"
    if action_id == "expand_patch_candidates_or_reflection":
        return "reflection_trace.json"
    if action_id == "convert_passing_tests_to_regression_guard":
        return "repository_test_regression_guard.json"
    if action_id == "build_static_graph_fault_ranking":
        return "fault_localization.json"
    if action_id == "expand_static_candidate_search":
        return "source_mining.json"
    if action_id == "mine_static_bug_signals":
        return "github_repo_intelligence.json"
    if action_id == "adjust_source_filters":
        return "repository_structure.json"
    if action_id == "retry_with_github_token_or_cache":
        return "github_repo_intelligence.json"
    if bool(fault.get("static_fallback_available", False)):
        return "fault_localization.json"
    return "github_repo_agent_controller.json"


def _controller_status(selected_action: dict[str, Any]) -> str:
    action_id = str(selected_action.get("id") or "")
    if action_id in {
        "inspect_generated_artifacts",
        "await_failing_test_or_bug_report",
        "await_environment_repair",
        "extend_failure_overlay_or_provide_bug_report",
        "retry_with_github_token_or_cache",
        "configure_llm_patch_api_key",
        "diagnose_llm_provider_failure",
    }:
        return "blocked"
    return "ready"


def _action(
    action_id: str,
    phase: str,
    tool: str,
    command: str,
    reason: str,
    risk: str,
    *,
    executable_now: bool = True,
) -> dict[str, Any]:
    return {
        "id": action_id,
        "phase": phase,
        "tool": tool,
        "command": command,
        "reason": reason,
        "risk": risk,
        "executable_now": executable_now,
    }


def _step(
    index: int,
    mode: str,
    action: str,
    tool: str,
    expected_outcome: str,
) -> dict[str, Any]:
    return {
        "step": index,
        "mode": mode,
        "action": action,
        "tool": tool,
        "expected_outcome": expected_outcome,
    }


def _observation(signal: str, value: Any) -> dict[str, str]:
    return {"signal": signal, "value": str(value if value is not None else "")}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(
        f"{key}={_int(value)}" for key, value in sorted(counts.items())
    )


def _format_list(values: list[Any]) -> str:
    items = [str(value) for value in values if str(value)]
    return ", ".join(items) if items else "none"


def _llm_api_key_env_options(
    summary: dict[str, Any],
    audit: dict[str, Any],
    *,
    primary_env: str,
    summary_key: str,
) -> list[str]:
    envs = [str(value) for value in _list(summary.get(summary_key)) if str(value)]
    if not envs:
        envs = [
            str(value)
            for value in _list(audit.get("checked_api_key_envs"))
            if str(value)
        ]
    if primary_env:
        envs.insert(0, primary_env)
    deduped: list[str] = []
    for env in envs:
        if env not in deduped:
            deduped.append(env)
    return deduped or ["CIA_LLM_API_KEY"]


def _format_env_options(envs: list[str]) -> str:
    return ", ".join(f"`{env}`" for env in envs if env)


def _markdown_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")
