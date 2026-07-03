from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from code_intelligence_agent.agents.action_registry import (
    build_agent_action_registry,
    build_agent_policy_trace,
    render_agent_action_registry_markdown,
    render_agent_policy_trace_markdown,
)


AGENT_LOOP = ["observe", "plan", "act", "verify", "reflect", "replan"]


def build_agent_controller_plan(
    intelligence_summary: dict[str, Any],
) -> dict[str, Any]:
    readiness = _dict(intelligence_summary.get("analysis_readiness"))
    fault = _dict(intelligence_summary.get("fault_localization"))
    selected_action = _select_action(intelligence_summary, readiness, fault)
    observations = _observations(intelligence_summary, readiness, fault)
    plan = _plan_steps(selected_action, readiness, fault)
    verification = _verification(selected_action, readiness, fault)
    reflection = _reflection(selected_action, readiness, fault)
    replan = _replan(
        selected_action,
        readiness,
        reflection,
        verification,
        _dict(intelligence_summary.get("agent_goal_readiness")),
    )
    termination = _termination(selected_action, readiness)
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
    return {
        "agent_type": "code_intelligence_controller",
        "control_loop": AGENT_LOOP,
        "objective": "analyze_localize_and_repair_repository",
        "status": _controller_status(selected_action),
        "current_stage": str(readiness.get("current_stage") or ""),
        "next_stage": str(readiness.get("next_stage") or ""),
        "primary_blocker": str(readiness.get("blocker") or ""),
        "selected_action": selected_action,
        "observations": observations,
        "plan": plan,
        "verification": verification,
        "reflection": reflection,
        "replan": replan,
        "termination": termination,
        "auto_controller": auto_controller,
        "llm_repair_action_audit": llm_repair_action_audit,
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


def render_agent_controller_markdown(payload: dict[str, Any]) -> str:
    selected = _dict(payload.get("selected_action"))
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
        "## Selected Action",
        "",
        f"- ID: `{_markdown_cell(selected.get('id'))}`",
        f"- Phase: `{_markdown_cell(selected.get('phase'))}`",
        f"- Tool: `{_markdown_cell(selected.get('tool'))}`",
        f"- Risk: `{_markdown_cell(selected.get('risk'))}`",
        f"- Executable Now: {str(bool(selected.get('executable_now', False))).lower()}",
        f"- Reason: {_markdown_cell(selected.get('reason'))}",
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
    blocked = bool(
        summary.get("repository_llm_patch_blocked", False)
        or llm_audit.get("blocked", False)
        or llm_status in {"blocked", "unavailable", "failed"}
        or llm_reason.startswith("missing_api_key:")
        or llm_reason == "missing_llm_api_key"
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
    repair_action_id = _llm_repair_action_id(
        patch_mode=patch_mode,
        blocked=blocked,
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
            f"key_present={str(key_present).lower()}"
        ),
        "plan": (
            f"repair_action={repair_action_id or 'none'}; "
            f"reflection_action={reflection_action_id or 'none'}; "
            "LLM output is constrained to localized Top-k functions and must pass "
            "JSON/AST/scope/safety gates before sandbox validation"
        ),
        "act": (
            f"llm_candidates={llm_candidate_count}; "
            f"rule_candidates={rule_candidate_count}; "
            f"llm_reason={llm_reason or 'none'}"
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
            f"{blocker or 'none'}" if blocked else next_action
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
        or blocker == "missing_llm_api_key"
        or blocker.startswith("missing_api_key:")
    )
    if mode == "llm" and blocked:
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
        return (
            f" LLM patch generation `{provider}/{model}` failed with "
            f"`{reason or 'unknown_error'}`."
        )
    if status == "pass":
        return (
            f" LLM patch generation `{provider}/{model}` produced candidates "
            "that remain subject to safety gates and sandbox validation."
        )
    return ""


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
    if status == "verified":
        trigger = "phase_completed"
        next_policy = "advance_to_next_stage"
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


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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
