from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path
from typing import Any

from code_intelligence_agent.agents.llm_client import llm_config_audit
from code_intelligence_agent.agents.controller import (
    build_agent_controller_plan,
    write_agent_controller_artifacts,
)
from code_intelligence_agent.core.ast_analyzer import ASTAnalyzer
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.models import RepoParseResult
from code_intelligence_agent.core.program_graph import build_program_graph
from code_intelligence_agent.evaluation.github_repo_agent import (
    GitHubRepoAgentReport,
    _build_fetch_error_report,
    _write_agent_report,
    parse_github_repo_spec,
    run_github_repo_agent,
)
from code_intelligence_agent.evaluation.github_discovery_fetcher import (
    GitHubAPIError,
)
from code_intelligence_agent.evaluation.github_fetcher import (
    _source_cache_path,
    source_from_dict,
)
from code_intelligence_agent.evaluation.repository_test_patch_validation import (
    _patch_candidate_from_dict,
    _validation_result_to_dict,
)
from code_intelligence_agent.search.beam_patch_search import BeamPatchSearch
from code_intelligence_agent.tools.sandbox import Sandbox


DEFAULT_MAX_SOURCES = 50
DEFAULT_MAX_CANDIDATES = 20
DEFAULT_REPOSITORY_TEST_TIMEOUT = 20


def run_github_repo_intelligence(
    repo_spec: str,
    output_dir: str | Path,
    *,
    ref: str | None = None,
    token: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    target_prefix: str = "",
    recipes: list[str] | None = None,
    source_cache_dir: str | Path | None = None,
    max_sources: int = DEFAULT_MAX_SOURCES,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    preset: str = "mining",
    auto_fallback: bool = True,
    repository_test_root: str | Path | None = None,
    repository_test_timeout: int = 20,
    repository_test_failure_overlay_candidate_limit: int = 5,
    repository_patch_generation_mode: str = "rule",
    repository_llm_patch_candidate_limit: int | None = None,
    repository_patch_candidate_variant_allowlist: list[str] | None = None,
    repository_test_reflection_mode: str = "rule",
    repository_test_reflection_rounds: int = 1,
    repository_test_reflection_width: int = 1,
    patch_judge_mode: str = "none",
    run_repository_test_command: bool = True,
    run_repository_test_environment_setup: bool = False,
    run_repository_test_retry: bool = False,
    run_repository_test_retry_prerequisites: bool = False,
    auto_repository_test_retry: bool = False,
    auto_repository_test_retry_max_risk: str = "low",
    auto_repository_test_retry_allowed_runners: list[str] | None = None,
    repository_test_environment_setup_timeout: int = 120,
    checkout_repository_tests: bool = False,
    repository_checkout_timeout: int = 120,
    repository_checkout_depth: int = 1,
    prefer_cached_discovery: bool = False,
    auto_controller_actions: bool = False,
    auto_controller_max_actions: int = 2,
    auto_phase4_evaluation: bool = False,
    auto_phase4_strategy_reruns: bool = False,
    phase4_strategy_rerun_limit: int = 3,
    phase4_strategy_rerun_timeout: int | None = None,
    execution_profile: str = "",
    agent_shortcut: bool = False,
    output_dir_defaulted: bool = False,
    api_base_url: str = "https://api.github.com",
    timeout: int = 20,
    opener=None,
) -> GitHubRepoAgentReport:
    agent_kwargs = {
        "ref": ref,
        "token": token,
        "recursive": True,
        "api_base_url": api_base_url,
        "timeout": timeout,
        "include": include,
        "exclude": exclude,
        "target_prefix": target_prefix,
        "recipes": recipes,
        "source_cache_dir": source_cache_dir,
        "max_sources": max_sources,
        "max_candidates": max_candidates,
        "auto_dependency_sources": True,
        "preset": preset,
        "repository_test_root": repository_test_root,
        "repository_test_timeout": repository_test_timeout,
        "repository_test_failure_overlay_candidate_limit": (
            repository_test_failure_overlay_candidate_limit
        ),
        "repository_patch_generation_mode": repository_patch_generation_mode,
        "repository_llm_patch_candidate_limit": repository_llm_patch_candidate_limit,
        "repository_patch_candidate_variant_allowlist": (
            repository_patch_candidate_variant_allowlist
        ),
        "repository_test_reflection_mode": repository_test_reflection_mode,
        "repository_test_reflection_rounds": repository_test_reflection_rounds,
        "repository_test_reflection_width": repository_test_reflection_width,
        "patch_judge_mode": patch_judge_mode,
        "run_repository_test_command": run_repository_test_command,
        "run_repository_test_environment_setup": run_repository_test_environment_setup,
        "run_repository_test_retry": run_repository_test_retry,
        "run_repository_test_retry_prerequisites": run_repository_test_retry_prerequisites,
        "auto_repository_test_retry": auto_repository_test_retry,
        "auto_repository_test_retry_max_risk": auto_repository_test_retry_max_risk,
        "auto_repository_test_retry_allowed_runners": (
            auto_repository_test_retry_allowed_runners
        ),
        "repository_test_environment_setup_timeout": (
            repository_test_environment_setup_timeout
        ),
        "checkout_repository_tests": checkout_repository_tests,
        "repository_checkout_timeout": repository_checkout_timeout,
        "repository_checkout_depth": repository_checkout_depth,
        "prefer_cached_discovery": prefer_cached_discovery,
        "auto_fallback": auto_fallback,
        "fallback_min_generated_candidates": 1,
        "fallback_preset": "mining",
        "fallback_max_sources": max(max_sources, DEFAULT_MAX_SOURCES),
        "fallback_max_candidates": max(max_candidates, DEFAULT_MAX_CANDIDATES),
        "opener": opener,
    }
    try:
        report = run_github_repo_agent(
            repo_spec,
            output_dir,
            **agent_kwargs,
        )
    except GitHubAPIError as exc:
        report = _build_fetch_error_report(
            repo_spec=repo_spec,
            output_dir=Path(output_dir),
            preset=preset,
            error=exc,
        )
        _write_agent_report(report)
    if auto_controller_actions:
        report = _run_auto_controller_actions(
            report,
            repo_spec=repo_spec,
            output_dir=output_dir,
            agent_kwargs=agent_kwargs,
            max_actions=auto_controller_max_actions,
            auto_phase4_evaluation=auto_phase4_evaluation,
            auto_phase4_strategy_reruns=auto_phase4_strategy_reruns,
            phase4_strategy_rerun_limit=phase4_strategy_rerun_limit,
            phase4_strategy_rerun_timeout=(
                repository_test_timeout
                if phase4_strategy_rerun_timeout is None
                else phase4_strategy_rerun_timeout
            ),
        )
    else:
        report.summary.setdefault("agent_auto_enabled", False)
        report.summary.setdefault("agent_auto_action_count", 0)
        report.summary.setdefault("agent_auto_actions", [])
        report.summary.setdefault("agent_auto_trace", [])
        report.summary.setdefault("agent_auto_stop_reason", "disabled")
        report.summary.setdefault("agent_auto_max_actions", 0)
        report.summary.setdefault(
            "agent_auto_loop_audit",
            _auto_loop_audit([], [], "disabled"),
        )
    report.summary["agent_invocation"] = _agent_invocation_summary(
        repo_spec=repo_spec,
        output_dir=output_dir,
        ref=ref,
        token_present=bool(token),
        include=include,
        exclude=exclude,
        target_prefix=target_prefix,
        recipes=recipes,
        source_cache_dir=source_cache_dir,
        max_sources=max_sources,
        max_candidates=max_candidates,
        preset=preset,
        auto_fallback=auto_fallback,
        repository_test_root=repository_test_root,
        repository_test_timeout=repository_test_timeout,
        repository_patch_generation_mode=repository_patch_generation_mode,
        repository_llm_patch_candidate_limit=repository_llm_patch_candidate_limit,
        repository_patch_candidate_variant_allowlist=(
            repository_patch_candidate_variant_allowlist
        ),
        repository_test_reflection_mode=repository_test_reflection_mode,
        repository_test_reflection_rounds=repository_test_reflection_rounds,
        repository_test_reflection_width=repository_test_reflection_width,
        patch_judge_mode=patch_judge_mode,
        run_repository_test_command=run_repository_test_command,
        run_repository_test_environment_setup=run_repository_test_environment_setup,
        run_repository_test_retry=run_repository_test_retry,
        run_repository_test_retry_prerequisites=(
            run_repository_test_retry_prerequisites
        ),
        auto_repository_test_retry=auto_repository_test_retry,
        auto_repository_test_retry_max_risk=auto_repository_test_retry_max_risk,
        auto_repository_test_retry_allowed_runners=(
            auto_repository_test_retry_allowed_runners
        ),
        checkout_repository_tests=checkout_repository_tests,
        repository_checkout_depth=repository_checkout_depth,
        prefer_cached_discovery=prefer_cached_discovery,
        auto_controller_actions=auto_controller_actions,
        auto_controller_max_actions=auto_controller_max_actions,
        auto_phase4_evaluation=auto_phase4_evaluation,
        auto_phase4_strategy_reruns=auto_phase4_strategy_reruns,
        phase4_strategy_rerun_limit=phase4_strategy_rerun_limit,
        execution_profile=execution_profile,
        agent_shortcut=agent_shortcut,
        output_dir_defaulted=output_dir_defaulted,
        api_base_url=api_base_url,
        timeout=timeout,
    )
    return report


def _agent_invocation_summary(
    *,
    repo_spec: str,
    output_dir: str | Path,
    ref: str | None,
    token_present: bool,
    include: list[str] | None,
    exclude: list[str] | None,
    target_prefix: str,
    recipes: list[str] | None,
    source_cache_dir: str | Path | None,
    max_sources: int,
    max_candidates: int,
    preset: str,
    auto_fallback: bool,
    repository_test_root: str | Path | None,
    repository_test_timeout: int,
    repository_patch_generation_mode: str,
    repository_llm_patch_candidate_limit: int | None,
    repository_patch_candidate_variant_allowlist: list[str] | None,
    repository_test_reflection_mode: str,
    repository_test_reflection_rounds: int,
    repository_test_reflection_width: int,
    patch_judge_mode: str,
    run_repository_test_command: bool,
    run_repository_test_environment_setup: bool,
    run_repository_test_retry: bool,
    run_repository_test_retry_prerequisites: bool,
    auto_repository_test_retry: bool,
    auto_repository_test_retry_max_risk: str,
    auto_repository_test_retry_allowed_runners: list[str] | None,
    checkout_repository_tests: bool,
    repository_checkout_depth: int,
    prefer_cached_discovery: bool,
    auto_controller_actions: bool,
    auto_controller_max_actions: int,
    auto_phase4_evaluation: bool,
    auto_phase4_strategy_reruns: bool,
    phase4_strategy_rerun_limit: int,
    execution_profile: str,
    agent_shortcut: bool,
    output_dir_defaulted: bool,
    api_base_url: str,
    timeout: int,
) -> dict[str, Any]:
    requested_profile = str(execution_profile or "")
    effective_profile = requested_profile or _infer_execution_profile(
        auto_controller_actions=auto_controller_actions,
        checkout_repository_tests=checkout_repository_tests,
        auto_repository_test_retry=auto_repository_test_retry,
        run_repository_test_retry_prerequisites=(
            run_repository_test_retry_prerequisites
        ),
    )
    return {
        "repo_spec": str(repo_spec),
        "output_dir": str(output_dir),
        "output_dir_defaulted": bool(output_dir_defaulted),
        "default_output_dir": str(output_dir) if output_dir_defaulted else "",
        "requested_ref": str(ref or ""),
        "token_configured": bool(token_present),
        "requested_execution_profile": requested_profile or "inferred",
        "effective_execution_profile": effective_profile,
        "agent_shortcut": bool(agent_shortcut),
        "agent_mode": bool(agent_shortcut or auto_controller_actions),
        "preset": str(preset or ""),
        "include": [str(item) for item in _list(include)],
        "exclude": [str(item) for item in _list(exclude)],
        "target_prefix": str(target_prefix or ""),
        "recipes": [str(item) for item in _list(recipes)],
        "source_cache_dir": str(source_cache_dir or ""),
        "max_sources": _int(max_sources),
        "max_candidates": _int(max_candidates),
        "auto_fallback": bool(auto_fallback),
        "repository_test_root": str(repository_test_root or ""),
        "repository_test_timeout": _int(repository_test_timeout),
        "run_repository_test_command": bool(run_repository_test_command),
        "run_repository_test_environment_setup": bool(
            run_repository_test_environment_setup
        ),
        "run_repository_test_retry": bool(run_repository_test_retry),
        "run_repository_test_retry_prerequisites": bool(
            run_repository_test_retry_prerequisites
        ),
        "auto_repository_test_retry": bool(auto_repository_test_retry),
        "auto_repository_test_retry_max_risk": str(
            auto_repository_test_retry_max_risk or ""
        ),
        "auto_repository_test_retry_allowed_runners": [
            str(item) for item in _list(auto_repository_test_retry_allowed_runners)
        ],
        "checkout_repository_tests": bool(checkout_repository_tests),
        "repository_checkout_depth": _int(repository_checkout_depth),
        "prefer_cached_discovery": bool(prefer_cached_discovery),
        "repository_patch_generation_mode": str(
            repository_patch_generation_mode or ""
        ),
        "repository_llm_patch_candidate_limit": (
            None
            if repository_llm_patch_candidate_limit is None
            else _int(repository_llm_patch_candidate_limit)
        ),
        "repository_patch_candidate_variant_allowlist": [
            str(item)
            for item in (repository_patch_candidate_variant_allowlist or [])
            if str(item)
        ],
        "repository_test_reflection_mode": str(
            repository_test_reflection_mode or ""
        ),
        "repository_test_reflection_rounds": _int(
            repository_test_reflection_rounds
        ),
        "repository_test_reflection_width": _int(repository_test_reflection_width),
        "patch_judge_mode": str(patch_judge_mode or ""),
        "auto_controller_actions": bool(auto_controller_actions),
        "auto_controller_max_actions": _int(auto_controller_max_actions),
        "auto_phase4_evaluation": bool(auto_phase4_evaluation),
        "auto_phase4_strategy_reruns": bool(auto_phase4_strategy_reruns),
        "phase4_strategy_rerun_limit": _int(phase4_strategy_rerun_limit),
        "api_base_url": str(api_base_url or ""),
        "timeout": _int(timeout),
    }


def _infer_execution_profile(
    *,
    auto_controller_actions: bool,
    checkout_repository_tests: bool,
    auto_repository_test_retry: bool,
    run_repository_test_retry_prerequisites: bool,
) -> str:
    if auto_controller_actions:
        return "agent-auto"
    if checkout_repository_tests and (
        auto_repository_test_retry or run_repository_test_retry_prerequisites
    ):
        return "phase3-fast"
    if checkout_repository_tests:
        return "checkout"
    return "static"


def _run_auto_controller_actions(
    report: GitHubRepoAgentReport,
    *,
    repo_spec: str,
    output_dir: str | Path,
    agent_kwargs: dict[str, Any],
    max_actions: int,
    auto_phase4_evaluation: bool = False,
    auto_phase4_strategy_reruns: bool = False,
    phase4_strategy_rerun_limit: int = 3,
    phase4_strategy_rerun_timeout: int = DEFAULT_REPOSITORY_TEST_TIMEOUT,
) -> GitHubRepoAgentReport:
    current_report = report
    current_kwargs = dict(agent_kwargs)
    output_root = Path(output_dir)
    actions: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    max_auto_actions = max(0, _int(max_actions))
    stop_reason = "max_actions_zero" if max_auto_actions <= 0 else ""

    while not stop_reason:
        iteration = len(trace) + 1
        current_report.summary["agent_auto_actions"] = list(actions)
        current_report.summary["agent_auto_trace"] = list(trace)
        summary = github_repo_intelligence_summary(current_report)
        controller = _dict(summary.get("agent_controller"))
        selected_action = _dict(controller.get("selected_action"))
        action_id = str(selected_action.get("id") or "")
        trace_item = _auto_trace_item(
            iteration=iteration,
            summary=summary,
            controller=controller,
            selected_action=selected_action,
        )

        if len(actions) >= max_auto_actions:
            limit_reason = _auto_stop_reason(
                action_id,
                selected_action,
                current_kwargs,
            )
            stop_reason = (
                "max_actions_reached"
                if limit_reason.startswith("unsupported_auto_action:")
                else limit_reason
            )
            trace_item["auto_executed"] = False
            trace_item["stop_reason"] = stop_reason
            trace_item.update(
                _auto_trace_stop_fields(
                    stop_reason=stop_reason,
                    summary=summary,
                    controller=controller,
                    selected_action=selected_action,
                    action_count=len(actions),
                    max_actions=max_auto_actions,
                )
            )
            trace.append(trace_item)
            break

        local_action = _auto_local_action_kind(
            action_id,
            current_report,
            auto_phase4_evaluation=auto_phase4_evaluation,
        )
        if local_action is not None:
            action_index = len(actions) + 1
            snapshot_paths = _write_auto_controller_snapshot(
                summary,
                output_root,
                suffix=f"pre_auto_action_{action_index}",
            )
            trace_item["auto_executed"] = True
            trace_item["stop_reason"] = ""
            trace_item["action_index"] = action_index
            trace_item.update(snapshot_paths)
            current_report = _apply_auto_local_action(
                local_action=local_action,
                report=current_report,
                summary=summary,
                controller=controller,
                selected_action=selected_action,
                output_root=output_root,
                snapshot_paths=snapshot_paths,
                auto_phase4_strategy_reruns=auto_phase4_strategy_reruns,
                phase4_strategy_rerun_limit=phase4_strategy_rerun_limit,
                phase4_strategy_rerun_timeout=phase4_strategy_rerun_timeout,
            )
            after_summary = github_repo_intelligence_summary(current_report)
            action_record = _auto_local_action_record(
                action_id=action_id,
                selected_action=selected_action,
                controller=controller,
                before_summary=summary,
                after_report=current_report,
                after_summary=after_summary,
                snapshot_paths=snapshot_paths,
            )
            actions.append(action_record)
            trace_item.update(_auto_trace_after_fields(after_summary, current_report))
            trace_item.update(_auto_trace_loop_fields(action_record))
            trace.append(trace_item)
            continue

        rerun_kwargs = _auto_action_rerun_kwargs(action_id, current_kwargs)
        if rerun_kwargs is None:
            stop_reason = _auto_stop_reason(action_id, selected_action, current_kwargs)
            trace_item["auto_executed"] = False
            trace_item["stop_reason"] = stop_reason
            trace_item.update(
                _auto_trace_stop_fields(
                    stop_reason=stop_reason,
                    summary=summary,
                    controller=controller,
                    selected_action=selected_action,
                    action_count=len(actions),
                    max_actions=max_auto_actions,
                )
            )
            trace.append(trace_item)
            break

        action_index = len(actions) + 1
        snapshot_paths = _write_auto_controller_snapshot(
            summary,
            output_root,
            suffix=f"pre_auto_action_{action_index}",
        )
        trace_item["auto_executed"] = True
        trace_item["stop_reason"] = ""
        trace_item["action_index"] = action_index
        trace_item.update(snapshot_paths)

        rerun_report = run_github_repo_agent(repo_spec, output_dir, **rerun_kwargs)
        after_summary = github_repo_intelligence_summary(rerun_report)
        action_record = _auto_action_record(
            action_id=action_id,
            selected_action=selected_action,
            controller=controller,
            before_summary=summary,
            after_report=rerun_report,
            after_summary=after_summary,
            rerun_kwargs=rerun_kwargs,
            snapshot_paths=snapshot_paths,
        )
        actions.append(action_record)
        trace_item.update(_auto_trace_after_fields(after_summary, rerun_report))
        trace_item.update(_auto_trace_loop_fields(action_record))
        trace.append(trace_item)
        current_report = rerun_report
        current_kwargs = rerun_kwargs

    final_summary = github_repo_intelligence_summary(current_report)
    final_controller = _dict(final_summary.get("agent_controller"))
    final_selected_action = _dict(final_controller.get("selected_action"))
    stop_state = _auto_stop_state(
        stop_reason=stop_reason,
        summary=final_summary,
        controller=final_controller,
        selected_action=final_selected_action,
        action_count=len(actions),
        max_actions=max_auto_actions,
    )
    if not trace and stop_reason:
        trace_item = _auto_trace_item(
            iteration=1,
            summary=final_summary,
            controller=final_controller,
            selected_action=final_selected_action,
        )
        trace_item["auto_executed"] = False
        trace_item["stop_reason"] = stop_reason
        trace_item.update(
            _auto_trace_stop_fields(
                stop_reason=stop_reason,
                summary=final_summary,
                controller=final_controller,
                selected_action=final_selected_action,
                action_count=len(actions),
                max_actions=max_auto_actions,
            )
        )
        trace.append(trace_item)
    current_report.summary["agent_auto_enabled"] = True
    current_report.summary["agent_auto_max_actions"] = max_auto_actions
    current_report.summary["agent_auto_action_count"] = len(actions)
    current_report.summary["agent_auto_actions"] = actions
    current_report.summary["agent_auto_trace"] = trace
    current_report.summary["agent_auto_stop_reason"] = stop_reason
    current_report.summary["agent_auto_stop_state"] = stop_state
    current_report.summary["agent_auto_loop_audit"] = _auto_loop_audit(
        actions,
        trace,
        stop_reason,
    )
    return current_report


def _auto_trace_stop_fields(
    *,
    stop_reason: str,
    summary: dict[str, Any],
    controller: dict[str, Any],
    selected_action: dict[str, Any],
    action_count: int,
    max_actions: int,
) -> dict[str, Any]:
    stop_state = _auto_stop_state(
        stop_reason=stop_reason,
        summary=summary,
        controller=controller,
        selected_action=selected_action,
        action_count=action_count,
        max_actions=max_actions,
    )
    return {
        "stop_category": str(stop_state.get("category") or ""),
        "stop_action_id": str(stop_state.get("action_id") or ""),
        "stop_action_executable_now": bool(
            stop_state.get("action_executable_now", False)
        ),
        "stop_agent_goal_readiness_status": str(
            stop_state.get("agent_goal_readiness_status") or ""
        ),
        "stop_agent_goal_readiness_failed_criteria": [
            str(item)
            for item in _list(stop_state.get("agent_goal_readiness_failed_criteria"))
        ],
        "stop_blocker": str(stop_state.get("blocker") or ""),
        "stop_next_action": str(stop_state.get("next_action") or ""),
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
    }


def _auto_stop_state(
    *,
    stop_reason: str,
    summary: dict[str, Any],
    controller: dict[str, Any],
    selected_action: dict[str, Any],
    action_count: int,
    max_actions: int,
) -> dict[str, Any]:
    readiness = _dict(summary.get("analysis_readiness"))
    goal_readiness = _dict(summary.get("agent_goal_readiness"))
    action_id = str(selected_action.get("id") or "")
    executable_now = bool(selected_action.get("executable_now", False))
    category = _auto_stop_category(stop_reason)
    next_action = (
        str(selected_action.get("command") or "")
        or str(readiness.get("next_action") or "")
    )
    recovery = _auto_stop_recovery(
        category=category,
        stop_reason=stop_reason,
        readiness=readiness,
        goal_readiness=goal_readiness,
        selected_action=selected_action,
        executable_now=executable_now,
        next_action=next_action,
    )
    return {
        "reason": str(stop_reason or ""),
        "category": category,
        "action_id": action_id,
        "action_phase": str(selected_action.get("phase") or ""),
        "action_tool": str(selected_action.get("tool") or ""),
        "action_executable_now": executable_now,
        "action_reason": str(selected_action.get("reason") or ""),
        "controller_status": str(controller.get("status") or ""),
        "current_stage": str(readiness.get("current_stage") or ""),
        "next_stage": str(readiness.get("next_stage") or ""),
        "blocker": str(
            readiness.get("blocker") or controller.get("primary_blocker") or ""
        ),
        "next_action": next_action,
        "action_count": _int(action_count),
        "max_actions": _int(max_actions),
        "agent_goal_readiness_status": str(goal_readiness.get("status") or ""),
        "agent_goal_readiness_failed_criteria_count": _int(
            goal_readiness.get("failed_criteria_count", 0)
        ),
        "agent_goal_readiness_failed_criteria": [
            str(item) for item in _list(goal_readiness.get("failed_criteria"))
        ],
        "requires_external_input": bool(
            recovery.get("requires_user_action", False)
            or category == "manual_or_blocked"
            or not executable_now
        ),
        "recovery": recovery,
        "recovery_policy": str(recovery.get("policy") or ""),
        "requires_user_action": bool(
            recovery.get("requires_user_action", False)
        ),
        "requires_environment_change": bool(
            recovery.get("requires_environment_change", False)
        ),
        "external_input_kind": str(recovery.get("external_input_kind") or ""),
        "recommended_next_action": str(
            recovery.get("recommended_next_action") or ""
        ),
        "recommended_next_command": str(
            recovery.get("recommended_next_command") or ""
        ),
        "recovery_reason": str(recovery.get("reason") or ""),
    }


def _auto_stop_category(stop_reason: str) -> str:
    reason = str(stop_reason or "")
    if reason == "disabled":
        return "disabled"
    if reason in {"max_actions_zero", "max_actions_reached"}:
        return "budget_exhausted"
    if reason.startswith("phase_goal_reached:"):
        return "phase_goal_reached"
    if reason == "selected_action_not_executable":
        return "manual_or_blocked"
    if reason in {
        "selected_action_already_applied",
        "source_filters_already_broad",
        "static_mining_already_broad",
        "application_source_focus_already_broad",
        "test_discovery_already_broad",
    }:
        return "no_additional_auto_action"
    if reason.startswith("unsupported_auto_action:"):
        return "unsupported_action"
    if reason:
        return "stopped"
    return "running"


def _auto_stop_recovery(
    *,
    category: str,
    stop_reason: str,
    readiness: dict[str, Any],
    goal_readiness: dict[str, Any],
    selected_action: dict[str, Any],
    executable_now: bool,
    next_action: str,
) -> dict[str, Any]:
    action_id = str(selected_action.get("id") or "")
    blocker = str(
        readiness.get("blocker")
        or readiness.get("repository_test_setup_doctor_blocker")
        or ""
    )
    external_input_kind = _auto_stop_external_input_kind(
        category=category,
        action_id=action_id,
        blocker=blocker,
        stop_reason=stop_reason,
    )
    requires_environment_change = external_input_kind == "environment"
    requires_user_action = bool(
        category in {
            "manual_or_blocked",
            "budget_exhausted",
            "unsupported_action",
            "no_additional_auto_action",
        }
        or requires_environment_change
        or (not executable_now and category != "phase_goal_reached")
    )
    policy = _auto_stop_recovery_policy(
        category=category,
        stop_reason=stop_reason,
        action_id=action_id,
        external_input_kind=external_input_kind,
    )
    recommended_command = str(selected_action.get("command") or next_action or "")
    recommended_action = _auto_stop_recommended_next_action(
        category=category,
        action_id=action_id,
        external_input_kind=external_input_kind,
        recommended_command=recommended_command,
    )
    return {
        "policy": policy,
        "reason": _auto_stop_recovery_reason(
            category=category,
            stop_reason=stop_reason,
            action_id=action_id,
            blocker=blocker,
            failed_criteria=[
                str(item)
                for item in _list(goal_readiness.get("failed_criteria"))
            ],
        ),
        "requires_user_action": requires_user_action,
        "requires_environment_change": requires_environment_change,
        "external_input_kind": external_input_kind,
        "recommended_next_action": recommended_action,
        "recommended_next_command": recommended_command,
        "can_continue_automatically": bool(
            not requires_user_action
            and executable_now
            and category not in {"phase_goal_reached", "disabled"}
        ),
    }


def _auto_stop_external_input_kind(
    *,
    category: str,
    action_id: str,
    blocker: str,
    stop_reason: str,
) -> str:
    combined = " ".join([action_id, blocker, stop_reason]).lower()
    if category == "disabled":
        return "agent_auto_disabled"
    if category == "budget_exhausted":
        return "controller_budget"
    if "github_fetch" in combined or "github_token" in combined:
        return "github_token_or_cache"
    if "environment" in combined or "dependency" in combined or "test_tool" in combined:
        return "environment"
    if action_id in {
        "await_failing_test_or_bug_report",
        "extend_failure_overlay_or_provide_bug_report",
    }:
        return "failing_test_or_bug_report"
    if category == "unsupported_action":
        return "manual_command"
    if category == "no_additional_auto_action":
        return "analysis_scope_or_external_evidence"
    if category == "manual_or_blocked":
        return "manual_input"
    return "none"


def _auto_stop_recovery_policy(
    *,
    category: str,
    stop_reason: str,
    action_id: str,
    external_input_kind: str,
) -> str:
    if category == "phase_goal_reached":
        return "stop_phase_goal_reached"
    if category == "budget_exhausted":
        return "increase_auto_controller_budget_or_run_next_action"
    if external_input_kind == "environment":
        return "apply_environment_repair_then_rerun_agent"
    if external_input_kind == "github_token_or_cache":
        return "provide_github_token_or_matching_cache_then_rerun"
    if external_input_kind == "failing_test_or_bug_report":
        return "provide_failing_test_bug_report_or_overlay_rule"
    if category == "unsupported_action":
        return f"run_action_manually_or_add_auto_handler:{action_id or 'none'}"
    if category == "no_additional_auto_action":
        return "change_analysis_scope_or_supply_external_evidence"
    if category == "disabled":
        return "enable_agent_auto_controller"
    if stop_reason:
        return "inspect_stop_state_and_replan"
    return "continue_observe_plan_act"


def _auto_stop_recommended_next_action(
    *,
    category: str,
    action_id: str,
    external_input_kind: str,
    recommended_command: str,
) -> str:
    if category == "phase_goal_reached":
        return "Review generated artifacts and proceed to optional Phase 4 evaluation."
    if external_input_kind == "controller_budget":
        return "Rerun with a larger --auto-controller-max-actions value or execute the selected command manually."
    if external_input_kind == "environment":
        return "Apply repository_test_environment_repair_plan.json/md, then rerun the Agent."
    if external_input_kind == "github_token_or_cache":
        return "Set GITHUB_TOKEN or rerun with a matching cached discovery artifact."
    if external_input_kind == "failing_test_or_bug_report":
        return "Provide a failing test, bug report, mutation ground truth, or add a supported overlay rule."
    if external_input_kind == "analysis_scope_or_external_evidence":
        return "Broaden include/exclude/target-prefix settings or provide external dynamic evidence."
    if external_input_kind == "manual_command":
        return recommended_command or f"Run selected action `{action_id}` manually."
    if recommended_command:
        return recommended_command
    return "Inspect generated artifacts and rerun the Agent with adjusted inputs."


def _auto_stop_recovery_reason(
    *,
    category: str,
    stop_reason: str,
    action_id: str,
    blocker: str,
    failed_criteria: list[str],
) -> str:
    parts = [
        f"category={category or 'none'}",
        f"reason={stop_reason or 'none'}",
        f"action={action_id or 'none'}",
    ]
    if blocker:
        parts.append(f"blocker={blocker}")
    if failed_criteria:
        parts.append("failed_criteria=" + ",".join(failed_criteria[:5]))
    return "; ".join(parts)


def _auto_action_rerun_kwargs(
    action_id: str,
    current_kwargs: dict[str, Any],
) -> dict[str, Any] | None:
    if action_id != "run_repository_tests_with_checkout":
        if action_id == "adjust_source_filters":
            return _source_filter_rerun_kwargs(current_kwargs)
        if action_id in {
            "expand_static_candidate_search",
            "mine_static_bug_signals",
            "build_static_graph_fault_ranking",
            "adjust_application_source_focus",
        }:
            return _static_mining_rerun_kwargs(current_kwargs)
        if action_id == "discover_repository_tests":
            return _test_discovery_rerun_kwargs(current_kwargs)
        if action_id == "collect_dynamic_failure_evidence":
            rerun_kwargs = _phase3_rerun_kwargs(
                current_kwargs,
                enable_retry=True,
            )
            return rerun_kwargs if rerun_kwargs != current_kwargs else None
        if action_id == "generate_controlled_failure_overlay":
            return _failure_overlay_rerun_kwargs(current_kwargs)
        if action_id == "build_dynamic_fault_localization":
            return _phase3_rerun_kwargs(
                current_kwargs,
                enable_retry=True,
            )
        if action_id == "generate_and_validate_patches":
            return _phase3_rerun_kwargs(
                current_kwargs,
                enable_retry=True,
            )
        if action_id == "generate_llm_patch_candidates":
            return _safe_patch_candidate_rerun_kwargs(
                current_kwargs,
                target_patch_mode="llm",
            )
        if action_id == "generate_hybrid_patch_candidates":
            return _safe_patch_candidate_rerun_kwargs(
                current_kwargs,
                target_patch_mode="hybrid",
            )
        if action_id == "run_patch_reflection_loop":
            return _phase3_rerun_kwargs(
                current_kwargs,
                enable_retry=True,
                force_reflection=True,
            )
        if action_id == "run_llm_patch_reflection_loop":
            return _phase3_rerun_kwargs(
                current_kwargs,
                enable_retry=True,
                force_reflection=True,
                force_llm_reflection=True,
            )
        if action_id == "regenerate_safe_patch_candidates":
            return _safe_patch_candidate_rerun_kwargs(current_kwargs)
        if action_id == "narrow_repository_tests_after_timeout":
            return _timeout_narrowing_rerun_kwargs(current_kwargs)
        return None
    if bool(current_kwargs.get("checkout_repository_tests", False)):
        return None
    return _phase3_rerun_kwargs(
        current_kwargs,
        enable_retry=False,
    )


def _source_filter_rerun_kwargs(
    current_kwargs: dict[str, Any],
) -> dict[str, Any] | None:
    rerun_kwargs = dict(current_kwargs)
    changed = False
    if rerun_kwargs.get("include"):
        rerun_kwargs["include"] = None
        changed = True
    if rerun_kwargs.get("exclude"):
        rerun_kwargs["exclude"] = None
        changed = True
    if str(rerun_kwargs.get("target_prefix") or ""):
        rerun_kwargs["target_prefix"] = ""
        changed = True
    if _int(rerun_kwargs.get("max_sources", 0)) < DEFAULT_MAX_SOURCES:
        rerun_kwargs["max_sources"] = DEFAULT_MAX_SOURCES
        changed = True
    if _int(rerun_kwargs.get("max_candidates", 0)) < DEFAULT_MAX_CANDIDATES:
        rerun_kwargs["max_candidates"] = DEFAULT_MAX_CANDIDATES
        changed = True
    return rerun_kwargs if changed else None


def _static_mining_rerun_kwargs(
    current_kwargs: dict[str, Any],
) -> dict[str, Any] | None:
    rerun_kwargs = dict(current_kwargs)
    changed = False
    if str(rerun_kwargs.get("preset") or "") != "mining":
        rerun_kwargs["preset"] = "mining"
        changed = True
    if rerun_kwargs.get("include"):
        rerun_kwargs["include"] = None
        changed = True
    if rerun_kwargs.get("exclude"):
        rerun_kwargs["exclude"] = None
        changed = True
    if str(rerun_kwargs.get("target_prefix") or ""):
        rerun_kwargs["target_prefix"] = ""
        changed = True
    if _int(rerun_kwargs.get("max_sources", 0)) < DEFAULT_MAX_SOURCES:
        rerun_kwargs["max_sources"] = DEFAULT_MAX_SOURCES
        changed = True
    if _int(rerun_kwargs.get("max_candidates", 0)) < DEFAULT_MAX_CANDIDATES:
        rerun_kwargs["max_candidates"] = DEFAULT_MAX_CANDIDATES
        changed = True
    return rerun_kwargs if changed else None


def _test_discovery_rerun_kwargs(
    current_kwargs: dict[str, Any],
) -> dict[str, Any] | None:
    rerun_kwargs = dict(current_kwargs)
    changed = False
    if not bool(rerun_kwargs.get("run_repository_test_command", False)):
        rerun_kwargs["run_repository_test_command"] = True
        changed = True
    if not bool(rerun_kwargs.get("checkout_repository_tests", False)) and not rerun_kwargs.get(
        "repository_test_root"
    ):
        rerun_kwargs["checkout_repository_tests"] = True
        changed = True
    if rerun_kwargs.get("include"):
        rerun_kwargs["include"] = None
        changed = True
    if rerun_kwargs.get("exclude"):
        rerun_kwargs["exclude"] = None
        changed = True
    if str(rerun_kwargs.get("target_prefix") or ""):
        rerun_kwargs["target_prefix"] = ""
        changed = True
    if _int(rerun_kwargs.get("max_sources", 0)) < DEFAULT_MAX_SOURCES:
        rerun_kwargs["max_sources"] = DEFAULT_MAX_SOURCES
        changed = True
    if _int(rerun_kwargs.get("max_candidates", 0)) < DEFAULT_MAX_CANDIDATES:
        rerun_kwargs["max_candidates"] = DEFAULT_MAX_CANDIDATES
        changed = True
    if _int(rerun_kwargs.get("repository_test_timeout", 0)) == (
        DEFAULT_REPOSITORY_TEST_TIMEOUT
    ):
        rerun_kwargs["repository_test_timeout"] = 30
        changed = True
    return rerun_kwargs if changed else None


def _phase3_rerun_kwargs(
    current_kwargs: dict[str, Any],
    *,
    enable_retry: bool,
    force_reflection: bool = False,
    force_llm_reflection: bool = False,
) -> dict[str, Any]:
    rerun_kwargs = dict(current_kwargs)
    rerun_kwargs["run_repository_test_command"] = True
    if not rerun_kwargs.get("repository_test_root"):
        rerun_kwargs["checkout_repository_tests"] = True
    if _int(rerun_kwargs.get("repository_test_timeout", 0)) == (
        DEFAULT_REPOSITORY_TEST_TIMEOUT
    ):
        rerun_kwargs["repository_test_timeout"] = 30
    if enable_retry:
        rerun_kwargs["run_repository_test_retry_prerequisites"] = True
        rerun_kwargs["auto_repository_test_retry"] = True
        if str(rerun_kwargs.get("auto_repository_test_retry_max_risk") or "") == "low":
            rerun_kwargs["auto_repository_test_retry_max_risk"] = "medium"
        allowed_runners = [
            str(item)
            for item in _list(
                rerun_kwargs.get("auto_repository_test_retry_allowed_runners")
            )
            if str(item)
        ]
        for runner in ("pytest", "unittest"):
            if runner not in allowed_runners:
                allowed_runners.append(runner)
        rerun_kwargs["auto_repository_test_retry_allowed_runners"] = allowed_runners
    if force_reflection:
        reflection_mode = str(
            rerun_kwargs.get("repository_test_reflection_mode") or ""
        ).strip().lower()
        if force_llm_reflection:
            rerun_kwargs["repository_test_reflection_mode"] = "llm"
        elif reflection_mode in {"", "none"}:
            rerun_kwargs["repository_test_reflection_mode"] = "rule"
        elif reflection_mode == "llm" and _llm_reflection_key_missing():
            rerun_kwargs["repository_test_reflection_mode"] = "rule"
        if _int(rerun_kwargs.get("repository_test_reflection_rounds", 0)) <= 0:
            rerun_kwargs["repository_test_reflection_rounds"] = 1
        if _int(rerun_kwargs.get("repository_test_reflection_width", 0)) <= 0:
            rerun_kwargs["repository_test_reflection_width"] = 1
    return rerun_kwargs


def _llm_reflection_key_missing() -> bool:
    return not llm_config_audit("patch_generation", enabled=True).api_key_present


def _safe_patch_candidate_rerun_kwargs(
    current_kwargs: dict[str, Any],
    *,
    target_patch_mode: str | None = None,
) -> dict[str, Any] | None:
    rerun_kwargs = _phase3_rerun_kwargs(
        current_kwargs,
        enable_retry=True,
        force_reflection=False,
    )
    normalized_target = str(target_patch_mode or "").strip().lower().replace("-", "_")
    if normalized_target in {"llm", "model"}:
        rerun_kwargs["repository_patch_generation_mode"] = "llm"
    elif normalized_target in {"hybrid", "llm_with_rules", "rules_with_llm"}:
        rerun_kwargs["repository_patch_generation_mode"] = "hybrid"
    else:
        mode = str(rerun_kwargs.get("repository_patch_generation_mode") or "rule")
        normalized_mode = mode.strip().lower().replace("-", "_")
        if normalized_mode in {"", "rule", "rule_based", "rules"}:
            rerun_kwargs["repository_patch_generation_mode"] = "hybrid"
        elif normalized_mode in {"llm", "model"}:
            rerun_kwargs["repository_patch_generation_mode"] = "llm"
        else:
            rerun_kwargs["repository_patch_generation_mode"] = "hybrid"

    llm_limit = rerun_kwargs.get("repository_llm_patch_candidate_limit")
    if llm_limit is None or _int(llm_limit) < 3:
        rerun_kwargs["repository_llm_patch_candidate_limit"] = 3
    if _int(rerun_kwargs.get("max_candidates", 0)) < DEFAULT_MAX_CANDIDATES:
        rerun_kwargs["max_candidates"] = DEFAULT_MAX_CANDIDATES
    return rerun_kwargs if rerun_kwargs != current_kwargs else None


def _timeout_narrowing_rerun_kwargs(
    current_kwargs: dict[str, Any],
) -> dict[str, Any] | None:
    rerun_kwargs = _phase3_rerun_kwargs(
        current_kwargs,
        enable_retry=True,
        force_reflection=False,
    )
    rerun_kwargs["run_repository_test_retry"] = True
    allowed_runners = [
        str(item)
        for item in _list(rerun_kwargs.get("auto_repository_test_retry_allowed_runners"))
        if str(item)
    ]
    if "pytest" not in allowed_runners:
        allowed_runners.insert(0, "pytest")
    rerun_kwargs["auto_repository_test_retry_allowed_runners"] = allowed_runners
    return rerun_kwargs if rerun_kwargs != current_kwargs else None


def _failure_overlay_rerun_kwargs(
    current_kwargs: dict[str, Any],
) -> dict[str, Any] | None:
    rerun_kwargs = _phase3_rerun_kwargs(
        current_kwargs,
        enable_retry=True,
        force_reflection=False,
    )
    if _int(rerun_kwargs.get("repository_test_failure_overlay_candidate_limit", 0)) < 5:
        rerun_kwargs["repository_test_failure_overlay_candidate_limit"] = 5
    return rerun_kwargs if rerun_kwargs != current_kwargs else None


def _auto_local_action_kind(
    action_id: str,
    report: GitHubRepoAgentReport,
    *,
    auto_phase4_evaluation: bool = False,
) -> str | None:
    if action_id == "run_search_and_ablation_evaluation":
        if not auto_phase4_evaluation:
            return None
        if bool(report.summary.get("phase4_search_evaluation_executed", False)):
            return None
        return "phase4_search_evaluation_execution"
    if action_id == "convert_passing_tests_to_regression_guard":
        if str(report.summary.get("repository_test_regression_guard_status") or "") == "pass":
            return None
        return "repository_test_regression_guard"
    if action_id == "prepare_repository_test_environment":
        if str(report.summary.get("repository_test_environment_repair_plan_status") or "") == "pass":
            return None
        return "repository_test_environment_repair_plan"
    return None


def _apply_auto_local_action(
    *,
    local_action: str,
    report: GitHubRepoAgentReport,
    summary: dict[str, Any],
    controller: dict[str, Any],
    selected_action: dict[str, Any],
    output_root: Path,
    snapshot_paths: dict[str, str],
    auto_phase4_strategy_reruns: bool = False,
    phase4_strategy_rerun_limit: int = 3,
    phase4_strategy_rerun_timeout: int = DEFAULT_REPOSITORY_TEST_TIMEOUT,
) -> GitHubRepoAgentReport:
    if local_action == "repository_test_environment_repair_plan":
        return _apply_environment_repair_plan_action(
            report=report,
            summary=summary,
            controller=controller,
            selected_action=selected_action,
            output_root=output_root,
            snapshot_paths=snapshot_paths,
        )
    if local_action == "phase4_search_evaluation_execution":
        return _apply_phase4_search_evaluation_action(
            report=report,
            summary=summary,
            controller=controller,
            selected_action=selected_action,
            output_root=output_root,
            snapshot_paths=snapshot_paths,
            run_strategy_reruns=auto_phase4_strategy_reruns,
            strategy_rerun_limit=phase4_strategy_rerun_limit,
            strategy_rerun_timeout=phase4_strategy_rerun_timeout,
        )
    if local_action != "repository_test_regression_guard":
        return report
    guard = _build_repository_test_regression_guard(
        summary,
        controller=controller,
        selected_action=selected_action,
        snapshot_paths=snapshot_paths,
    )
    paths = _write_repository_test_regression_guard_artifacts(guard, output_root)
    report.summary.update(
        {
            "repository_test_regression_guard_status": str(
                guard.get("status") or ""
            ),
            "repository_test_regression_guard_reason": str(
                guard.get("reason") or ""
            ),
            "repository_test_regression_guard_command": str(
                guard.get("command") or ""
            ),
            "repository_test_regression_guard_dynamic_evidence_level": str(
                guard.get("dynamic_evidence_level") or ""
            ),
            "repository_test_regression_guard_usable_for_localization": bool(
                guard.get("usable_for_localization", False)
            ),
            "repository_test_regression_guard_usable_for_patch_validation": bool(
                guard.get("usable_for_patch_validation", False)
            ),
        }
    )
    report.output_paths.update(paths)
    if isinstance(report.onboarding_report, dict):
        report.onboarding_report["repository_test_regression_guard"] = guard
        output_paths = _dict(report.onboarding_report.get("output_paths"))
        output_paths.update(paths)
        report.onboarding_report["output_paths"] = output_paths
    return report


def _apply_environment_repair_plan_action(
    *,
    report: GitHubRepoAgentReport,
    summary: dict[str, Any],
    controller: dict[str, Any],
    selected_action: dict[str, Any],
    output_root: Path,
    snapshot_paths: dict[str, str],
) -> GitHubRepoAgentReport:
    plan = _build_repository_test_environment_repair_plan(
        summary,
        controller=controller,
        selected_action=selected_action,
        snapshot_paths=snapshot_paths,
    )
    paths = _write_repository_test_environment_repair_plan_artifacts(
        plan,
        output_root,
    )
    report.summary.update(
        {
            "repository_test_environment_repair_plan_status": str(
                plan.get("status") or ""
            ),
            "repository_test_environment_repair_plan_reason": str(
                plan.get("reason") or ""
            ),
            "repository_test_environment_repair_plan_blocker": str(
                plan.get("blocker") or ""
            ),
            "repository_test_environment_repair_plan_recommended_install_command": str(
                plan.get("recommended_install_command") or ""
            ),
            "repository_test_environment_repair_plan_missing_dependency_modules": [
                str(item)
                for item in _list(plan.get("missing_dependency_modules"))
            ],
            "repository_test_environment_repair_plan_missing_dependency_install_hint": str(
                plan.get("missing_dependency_install_hint") or ""
            ),
        }
    )
    report.output_paths.update(paths)
    if isinstance(report.onboarding_report, dict):
        report.onboarding_report["repository_test_environment_repair_plan"] = plan
        output_paths = _dict(report.onboarding_report.get("output_paths"))
        output_paths.update(paths)
        report.onboarding_report["output_paths"] = output_paths
    return report


def _apply_phase4_search_evaluation_action(
    *,
    report: GitHubRepoAgentReport,
    summary: dict[str, Any],
    controller: dict[str, Any],
    selected_action: dict[str, Any],
    output_root: Path,
    snapshot_paths: dict[str, str],
    run_strategy_reruns: bool = False,
    strategy_rerun_limit: int = 3,
    strategy_rerun_timeout: int = DEFAULT_REPOSITORY_TEST_TIMEOUT,
) -> GitHubRepoAgentReport:
    execution = _build_phase4_search_evaluation_execution(
        summary,
        controller=controller,
        selected_action=selected_action,
        snapshot_paths=snapshot_paths,
        run_strategy_reruns=run_strategy_reruns,
        strategy_rerun_limit=strategy_rerun_limit,
        strategy_rerun_timeout=strategy_rerun_timeout,
    )
    paths = _write_phase4_search_evaluation_execution_artifacts(
        execution,
        output_root,
    )
    report.summary.update(
        {
            "phase4_search_evaluation_executed": bool(
                execution.get("executed", False)
            ),
            "phase4_search_evaluation_execution_status": str(
                execution.get("status") or ""
            ),
            "phase4_search_evaluation_execution_reason": str(
                execution.get("reason") or ""
            ),
            "phase4_search_evaluation_execution_mode": str(
                execution.get("evaluation_mode") or ""
            ),
            "phase4_search_evaluation_execution_ready": bool(
                execution.get("ready_for_phase4", False)
            ),
            "phase4_search_evaluation_execution_success_rate": _float(
                _dict(execution.get("search_budget")).get("success_rate", 0.0)
            ),
            "phase4_search_evaluation_execution_baseline_caveat": bool(
                execution.get("baseline_regression_caveat", False)
            ),
            "phase4_search_evaluation_execution_full_suite_green_claim_allowed": bool(
                execution.get("full_suite_green_claim_allowed", False)
            ),
            "phase4_strategy_rerun_status": str(
                _dict(execution.get("strategy_rerun")).get("status") or ""
            ),
            "phase4_strategy_rerun_reason": str(
                _dict(execution.get("strategy_rerun")).get("reason") or ""
            ),
            "phase4_strategy_rerun_strategy_count": _int(
                _dict(execution.get("strategy_rerun")).get("strategy_count", 0)
            ),
            "phase4_strategy_rerun_total_evaluated_count": _int(
                _dict(execution.get("strategy_rerun")).get(
                    "total_evaluated_count",
                    0,
                )
            ),
        }
    )
    report.output_paths.update(paths)
    if isinstance(report.onboarding_report, dict):
        report.onboarding_report["phase4_search_evaluation_execution"] = execution
        output_paths = _dict(report.onboarding_report.get("output_paths"))
        output_paths.update(paths)
        report.onboarding_report["output_paths"] = output_paths
    return report


def _build_phase4_search_evaluation_execution(
    summary: dict[str, Any],
    *,
    controller: dict[str, Any],
    selected_action: dict[str, Any],
    snapshot_paths: dict[str, str],
    run_strategy_reruns: bool = False,
    strategy_rerun_limit: int = 3,
    strategy_rerun_timeout: int = DEFAULT_REPOSITORY_TEST_TIMEOUT,
) -> dict[str, Any]:
    phase4 = _dict(summary.get("phase4_search_evaluation"))
    ready = bool(phase4.get("ready_for_phase4", False))
    search_evaluation = _repository_phase4_search_evaluation(
        summary=summary,
        phase4=phase4,
        run_strategy_reruns=run_strategy_reruns,
        strategy_rerun_limit=strategy_rerun_limit,
        strategy_rerun_timeout=strategy_rerun_timeout,
    )
    evaluation_status = str(search_evaluation.get("status") or "blocked")
    evidence_level = str(search_evaluation.get("evidence_level") or "none")
    status = "pass" if ready and evaluation_status != "blocked" else "blocked"
    if ready and evidence_level == "validation_results":
        reason = "repository_phase4_search_ablation_evaluated"
    elif ready:
        reason = "repository_phase4_search_ablation_summary_only"
    else:
        reason = str(phase4.get("reason") or "phase4_not_ready")
    return {
        "status": status,
        "reason": reason,
        "executed": True,
        "evaluation_mode": (
            "repository_search_ablation_phase4"
            if evidence_level == "validation_results"
            else "repository_search_ablation_summary_only"
        ),
        "action_id": str(selected_action.get("id") or ""),
        "action_phase": str(selected_action.get("phase") or ""),
        "action_tool": str(selected_action.get("tool") or ""),
        "command": str(selected_action.get("command") or ""),
        "controller_status": str(controller.get("status") or ""),
        "ready_for_phase4": ready,
        "phase4_status": str(phase4.get("status") or ""),
        "phase4_reason": str(phase4.get("reason") or ""),
        "repair_ready": bool(phase4.get("repair_ready", False)),
        "repair_validation_scope": str(phase4.get("repair_validation_scope") or ""),
        "baseline_regression_caveat": bool(
            phase4.get("baseline_regression_caveat", False)
        ),
        "full_suite_green_claim_allowed": bool(
            phase4.get("full_suite_green_claim_allowed", False)
        ),
        "search_budget": _dict(phase4.get("search_budget")),
        "search_evaluation": search_evaluation,
        "candidate_ranking": _list(search_evaluation.get("candidate_ranking")),
        "strategy_evaluation": _dict(search_evaluation.get("strategy_evaluation")),
        "ablation_variants": _list(search_evaluation.get("ablation_variants")),
        "component_contribution": _dict(
            search_evaluation.get("component_contribution")
        ),
        "search_claim": _dict(search_evaluation.get("search_claim")),
        "strategy_rerun": _dict(search_evaluation.get("strategy_rerun")),
        "evaluation_gates": _list(phase4.get("evaluation_gates")),
        "evidence_artifacts": _dict(phase4.get("evidence_artifacts")),
        "recommended_commands": _list(phase4.get("recommended_commands")),
        "next_actions": _list(phase4.get("next_actions")),
        "pre_action_intelligence_json": str(
            snapshot_paths.get("pre_action_intelligence_json") or ""
        ),
        "pre_action_controller_json": str(
            snapshot_paths.get("pre_action_controller_json") or ""
        ),
    }


def _write_phase4_search_evaluation_execution_artifacts(
    payload: dict[str, Any],
    output_dir: Path,
) -> dict[str, str]:
    json_path = output_dir / "phase4_search_evaluation_execution.json"
    markdown_path = output_dir / "phase4_search_evaluation_execution.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        _render_phase4_search_evaluation_execution_markdown(payload),
        encoding="utf-8",
    )
    return {
        "phase4_search_evaluation_execution_json": str(json_path),
        "phase4_search_evaluation_execution_markdown": str(markdown_path),
    }


def _render_phase4_search_evaluation_execution_markdown(
    payload: dict[str, Any],
) -> str:
    budget = _dict(payload.get("search_budget"))
    search_evaluation = _dict(payload.get("search_evaluation"))
    strategy = _dict(payload.get("strategy_evaluation"))
    rerun = _dict(payload.get("strategy_rerun"))
    claim = _dict(payload.get("search_claim"))
    lines = [
        "# Phase 4 Search Evaluation Execution",
        "",
        f"- Status: `{_markdown_cell(payload.get('status') or '')}`",
        f"- Reason: `{_markdown_cell(payload.get('reason') or '')}`",
        f"- Evaluation Mode: `{_markdown_cell(payload.get('evaluation_mode') or '')}`",
        f"- Evidence Level: `{_markdown_cell(search_evaluation.get('evidence_level') or 'none')}`",
        f"- Action: `{_markdown_cell(payload.get('action_id') or '')}`",
        (
            "- Ready For Phase 4: "
            f"{str(bool(payload.get('ready_for_phase4', False))).lower()}"
        ),
        (
            "- Baseline Regression Caveat: "
            f"{str(bool(payload.get('baseline_regression_caveat', False))).lower()}"
        ),
        (
            "- Full-Suite Green Claim Allowed: "
            f"{str(bool(payload.get('full_suite_green_claim_allowed', False))).lower()}"
        ),
        "",
        "## Search Budget",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Candidate Count | {_int(budget.get('candidate_count', 0))} |",
        f"| Executed Count | {_int(budget.get('executed_count', 0))} |",
        f"| Success Count | {_int(budget.get('success_count', 0))} |",
        f"| Success Rate | {_float(budget.get('success_rate', 0.0)):.4f} |",
        "",
        "## Strategy Evaluation",
        "",
        "| Strategy | Evaluated | Success | First Success Rank | Success Rate | Notes |",
        "| --- | ---: | ---: | --- | ---: | --- |",
    ]
    for name, row_value in strategy.items():
        row = _dict(row_value)
        lines.append(
            "| "
            f"{_markdown_cell(name)} | "
            f"{_int(row.get('evaluated_count', 0))} | "
            f"{_int(row.get('success_count', 0))} | "
            f"{_markdown_cell(row.get('first_success_rank') or 'none')} | "
            f"{_float(row.get('success_rate', 0.0)):.4f} | "
            f"{_markdown_cell(row.get('notes') or '')} |"
        )
    if not strategy:
        lines.append("| none | 0 | 0 | none | 0.0000 | no validation rows |")
    lines.extend(
        [
            "",
            "## Strategy Rerun",
            "",
            f"- Enabled: {str(bool(rerun.get('enabled', False))).lower()}",
            f"- Status: `{_markdown_cell(rerun.get('status') or 'none')}`",
            f"- Reason: `{_markdown_cell(rerun.get('reason') or 'none')}`",
            f"- Best Strategy: `{_markdown_cell(rerun.get('best_strategy') or 'none')}`",
            "",
            "| Strategy | Component | Evaluated | Success | First Success Rank | Success Rate |",
            "| --- | --- | ---: | ---: | --- | ---: |",
        ]
    )
    for row_value in _list(rerun.get("strategies")):
        row = _dict(row_value)
        lines.append(
            "| "
            f"{_markdown_cell(row.get('name'))} | "
            f"{_markdown_cell(row.get('component'))} | "
            f"{_int(row.get('evaluated_count', 0))} | "
            f"{_int(row.get('success_count', 0))} | "
            f"{_markdown_cell(row.get('first_success_rank') or 'none')} | "
            f"{_float(row.get('success_rate', 0.0)):.4f} |"
        )
    if not _list(rerun.get("strategies")):
        lines.append("| none | none | 0 | 0 | none | 0.0000 |")
    lines.extend(
        [
            "",
            "| Variant | Component | Status | Delta First Success | Delta Success Rate |",
            "| --- | --- | --- | ---: | ---: |",
        ]
    )
    for row_value in _list(rerun.get("ablation_deltas")):
        row = _dict(row_value)
        lines.append(
            "| "
            f"{_markdown_cell(row.get('variant'))} | "
            f"{_markdown_cell(row.get('component'))} | "
            f"{_markdown_cell(row.get('status'))} | "
            f"{_int(row.get('delta_first_success_rank', 0))} | "
            f"{_float(row.get('delta_success_rate', 0.0)):.4f} |"
        )
    if not _list(rerun.get("ablation_deltas")):
        lines.append("| none | none | not_available | 0 | 0.0000 |")
    lines.extend(
        [
            "",
            "## Ablation Variants",
            "",
            "| Variant | Component | Status | Delta First Success | Delta Success Rate | Rationale |",
            "| --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for row_value in _list(payload.get("ablation_variants")):
        row = _dict(row_value)
        lines.append(
            "| "
            f"{_markdown_cell(row.get('variant'))} | "
            f"{_markdown_cell(row.get('component'))} | "
            f"{_markdown_cell(row.get('status'))} | "
            f"{_int(row.get('delta_first_success_rank', 0))} | "
            f"{_float(row.get('delta_success_rate', 0.0)):.4f} | "
            f"{_markdown_cell(row.get('rationale'))} |"
        )
    if not _list(payload.get("ablation_variants")):
        lines.append("| none | none | not_available | 0 | 0.0000 | no validation rows |")
    lines.extend(
        [
            "",
            "## Candidate Ranking",
            "",
            "| Rank | Candidate | Rule | Variant | Depth | Score | Success | Failure Type |",
            "| ---: | --- | --- | --- | ---: | ---: | --- | --- |",
        ]
    )
    for row_value in _list(payload.get("candidate_ranking"))[:10]:
        row = _dict(row_value)
        lines.append(
            "| "
            f"{_int(row.get('rank', 0))} | "
            f"`{_markdown_cell(row.get('candidate_id') or '')}` | "
            f"`{_markdown_cell(row.get('rule_id') or '')}` | "
            f"`{_markdown_cell(row.get('variant') or '')}` | "
            f"{_int(row.get('depth', 0))} | "
            f"{_float(row.get('score', 0.0)):.4f} | "
            f"{str(bool(row.get('success', False))).lower()} | "
            f"`{_markdown_cell(row.get('failure_type') or '')}` |"
        )
    if not _list(payload.get("candidate_ranking")):
        lines.append("| 0 | none | none | none | 0 | 0.0000 | false | none |")
    component = _dict(payload.get("component_contribution"))
    lines.extend(
        [
            "",
            "## Component Contribution",
            "",
            "| Component | Value |",
            "| --- | --- |",
        ]
    )
    for key, value in component.items():
        lines.append(f"| {_markdown_cell(key)} | {_markdown_cell(value)} |")
    if not component:
        lines.append("| none | no component contribution computed |")
    lines.extend(
        [
            "",
            "## Search Claim",
            "",
            f"- Claim: `{_markdown_cell(claim.get('claim') or 'none')}`",
            f"- Evidence: {_markdown_cell(claim.get('evidence') or 'none')}",
            (
                "- Full-Suite Green Claim Allowed: "
                f"{str(bool(claim.get('full_suite_green_claim_allowed', False))).lower()}"
            ),
            "",
        ]
    )
    lines.extend(
        [
        "## Evaluation Gates",
        "",
        "| Gate | Passed | Evidence |",
        "| --- | --- | --- |",
        ]
    )
    for item_value in _list(payload.get("evaluation_gates")):
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('name'))} | "
            f"{str(bool(item.get('passed', False))).lower()} | "
            f"{_markdown_cell(item.get('evidence'))} |"
        )
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Snapshots",
            "",
            (
                "- Pre-action Intelligence JSON: "
                f"`{_markdown_cell(payload.get('pre_action_intelligence_json') or 'none')}`"
            ),
            (
                "- Pre-action Controller JSON: "
                f"`{_markdown_cell(payload.get('pre_action_controller_json') or 'none')}`"
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def _repository_phase4_search_evaluation(
    *,
    summary: dict[str, Any],
    phase4: dict[str, Any],
    run_strategy_reruns: bool = False,
    strategy_rerun_limit: int = 3,
    strategy_rerun_timeout: int = DEFAULT_REPOSITORY_TEST_TIMEOUT,
) -> dict[str, Any]:
    evidence = _dict(phase4.get("evidence_artifacts"))
    patch_validation = _read_json(
        str(
            evidence.get("patch_validation_json")
            or summary.get("repository_test_patch_validation_json")
            or ""
        )
    )
    patch_candidates = _read_json(
        str(
            evidence.get("patch_candidates_json")
            or summary.get("repository_test_patch_candidates_json")
            or ""
        )
    )
    results = [_dict(item) for item in _list(patch_validation.get("results"))]
    candidates = [_dict(item) for item in _list(patch_candidates.get("candidates"))]
    if not results:
        budget = _dict(phase4.get("search_budget"))
        strategy = {
            "full_search": _phase4_strategy_row(
                _int(budget.get("executed_count", 0)),
                _int(budget.get("success_count", 0)),
                budget.get("first_success_rank"),
                notes="summary-only counts; validation rows unavailable",
            )
        }
        return {
            "status": "summary_only"
            if _int(budget.get("executed_count", 0)) > 0
            else "blocked",
            "reason": "validation_rows_missing",
            "evidence_level": "summary_only",
            "candidate_count": max(
                _int(budget.get("candidate_count", 0)),
                len(candidates),
            ),
            "validation_result_count": 0,
            "candidate_ranking": [],
            "strategy_evaluation": strategy,
            "ablation_variants": [],
            "component_contribution": {
                "candidate_generation": len(candidates),
                "validation_results_available": False,
            },
            "search_claim": _phase4_search_claim(
                success_count=_int(budget.get("success_count", 0)),
                first_success_rank=budget.get("first_success_rank"),
                full_suite_green_claim_allowed=bool(
                    phase4.get("full_suite_green_claim_allowed", False)
                ),
                baseline_caveat=bool(
                    phase4.get("baseline_regression_caveat", False)
                ),
                evidence_level="summary_only",
            ),
            "strategy_rerun": _phase4_strategy_rerun_skipped(
                enabled=run_strategy_reruns,
                reason="validation_rows_missing",
            ),
        }
    ranking = [
        _phase4_candidate_ranking_row(row, rank=index)
        for index, row in enumerate(results, start=1)
    ]
    strategy = _phase4_strategy_evaluation(ranking)
    ablations = _phase4_ablation_variants(ranking, strategy)
    success_count = sum(1 for row in ranking if bool(row.get("success", False)))
    first_success_rank = _phase4_first_success_rank(ranking)
    return {
        "status": "pass",
        "reason": "validation_results_evaluated",
        "evidence_level": "validation_results",
        "candidate_count": max(
            _int(patch_candidates.get("candidate_count", 0)),
            len(candidates),
            _int(patch_validation.get("candidate_count", 0)),
        ),
        "validation_result_count": len(ranking),
        "candidate_ranking": ranking,
        "strategy_evaluation": strategy,
        "ablation_variants": ablations,
        "component_contribution": _phase4_component_contribution(
            ranking,
            patch_validation=patch_validation,
            phase4=phase4,
        ),
        "search_claim": _phase4_search_claim(
            success_count=success_count,
            first_success_rank=first_success_rank,
            full_suite_green_claim_allowed=bool(
                phase4.get("full_suite_green_claim_allowed", False)
            ),
            baseline_caveat=bool(phase4.get("baseline_regression_caveat", False)),
            evidence_level="validation_results",
        ),
        "strategy_rerun": _phase4_strategy_rerun(
            enabled=run_strategy_reruns,
            patch_candidates=patch_candidates,
            patch_validation=patch_validation,
            strategy_rerun_limit=strategy_rerun_limit,
            strategy_rerun_timeout=strategy_rerun_timeout,
        ),
    }


def _phase4_candidate_ranking_row(
    row: dict[str, Any],
    *,
    rank: int,
) -> dict[str, Any]:
    return {
        "rank": rank,
        "candidate_id": str(row.get("candidate_id") or ""),
        "target_function_id": str(row.get("target_function_id") or ""),
        "target_function_name": str(row.get("target_function_name") or ""),
        "relative_file_path": str(row.get("relative_file_path") or ""),
        "rule_id": str(row.get("rule_id") or ""),
        "variant": str(row.get("variant") or ""),
        "depth": _int(row.get("depth", 0)),
        "parent_candidate_id": str(row.get("parent_candidate_id") or ""),
        "retained": bool(row.get("retained", True)),
        "retention_reason": str(row.get("retention_reason") or ""),
        "success": bool(row.get("success", False)),
        "failure_type": str(row.get("failure_type") or "unknown"),
        "score": _float(row.get("score", 0.0)),
        "feedback_score": _float(row.get("feedback_score", 0.0)),
        "search_prior_score": _float(row.get("search_prior_score", 0.0)),
        "regression_reflection": bool(row.get("regression_reflection", False)),
    }


def _phase4_strategy_evaluation(
    ranking: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    depth0 = [row for row in ranking if _int(row.get("depth", 0)) == 0]
    reflection = [row for row in ranking if _int(row.get("depth", 0)) > 0]
    top1 = ranking[:1]
    prior_sorted = sorted(
        ranking,
        key=lambda row: (
            -_float(row.get("search_prior_score", 0.0)),
            -_float(row.get("score", 0.0)),
            _int(row.get("rank", 0)),
        ),
    )
    rule_diverse = _phase4_first_per_key(ranking, "rule_id")
    return {
        "full_search": _phase4_strategy_row_from_rows(
            ranking,
            notes="observed validation order with controller-selected repair search",
        ),
        "top1_only": _phase4_strategy_row_from_rows(
            top1,
            notes="only the highest ranked patch candidate is allowed",
        ),
        "depth0_only": _phase4_strategy_row_from_rows(
            depth0,
            notes="reflection/refined candidates removed",
        ),
        "reflection_only": _phase4_strategy_row_from_rows(
            reflection,
            notes="only refined candidates after failed attempts",
        ),
        "prior_score_order": _phase4_strategy_row_from_rows(
            prior_sorted,
            notes="ranking uses prior score before execution feedback",
        ),
        "rule_diverse_first": _phase4_strategy_row_from_rows(
            rule_diverse,
            notes="first candidate per rule family retained",
        ),
    }


def _phase4_strategy_row_from_rows(
    rows: list[dict[str, Any]],
    *,
    notes: str,
) -> dict[str, Any]:
    success_count = sum(1 for row in rows if bool(row.get("success", False)))
    return _phase4_strategy_row(
        len(rows),
        success_count,
        _phase4_first_success_rank(rows),
        notes=notes,
    )


def _phase4_strategy_row(
    evaluated_count: int,
    success_count: int,
    first_success_rank: Any,
    *,
    notes: str,
) -> dict[str, Any]:
    evaluated = max(0, evaluated_count)
    successes = max(0, success_count)
    return {
        "evaluated_count": evaluated,
        "success_count": successes,
        "success_rate": round(successes / evaluated, 4) if evaluated else 0.0,
        "first_success_rank": first_success_rank,
        "failures_before_first_success": (
            _int(first_success_rank) - 1 if first_success_rank else evaluated
        ),
        "notes": notes,
    }


def _phase4_ablation_variants(
    ranking: list[dict[str, Any]],
    strategy: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    full = _dict(strategy.get("full_search"))
    baseline_rank = _int(full.get("first_success_rank", 0))
    baseline_rate = _float(full.get("success_rate", 0.0))
    variant_specs = [
        (
            "top1_only",
            "candidate_ranking",
            "Measures whether ranking alone puts a valid patch first.",
        ),
        (
            "depth0_only",
            "reflection_loop",
            "Removes refined candidates to estimate reflection contribution.",
        ),
        (
            "prior_score_order",
            "search_scoring",
            "Uses prior score ordering before execution feedback.",
        ),
        (
            "rule_diverse_first",
            "diversity_reranking",
            "Keeps one candidate per rule family to estimate diversity pressure.",
        ),
    ]
    rows: list[dict[str, Any]] = []
    for name, component, rationale in variant_specs:
        row = _dict(strategy.get(name))
        variant_rank = _int(row.get("first_success_rank", 0))
        if not ranking:
            status = "not_available"
        elif variant_rank and baseline_rank:
            status = "same" if variant_rank == baseline_rank else (
                "regression" if variant_rank > baseline_rank else "improvement"
            )
        elif baseline_rank and not variant_rank:
            status = "regression"
        elif variant_rank and not baseline_rank:
            status = "improvement"
        else:
            status = "same"
        rows.append(
            {
                "variant": name,
                "component": component,
                "status": status,
                "evaluated_count": _int(row.get("evaluated_count", 0)),
                "success_count": _int(row.get("success_count", 0)),
                "first_success_rank": row.get("first_success_rank"),
                "success_rate": _float(row.get("success_rate", 0.0)),
                "delta_first_success_rank": (
                    variant_rank - baseline_rank
                    if variant_rank and baseline_rank
                    else 0
                ),
                "delta_success_rate": round(
                    _float(row.get("success_rate", 0.0)) - baseline_rate,
                    4,
                ),
                "rationale": rationale,
            }
        )
    return rows


def _phase4_component_contribution(
    ranking: list[dict[str, Any]],
    *,
    patch_validation: dict[str, Any],
    phase4: dict[str, Any],
) -> dict[str, Any]:
    depth0_success = any(
        bool(row.get("success", False)) and _int(row.get("depth", 0)) == 0
        for row in ranking
    )
    reflection_success = any(
        bool(row.get("success", False)) and _int(row.get("depth", 0)) > 0
        for row in ranking
    )
    return {
        "validated_candidate_count": len(ranking),
        "successful_candidate_count": sum(
            1 for row in ranking if bool(row.get("success", False))
        ),
        "first_success_rank": _phase4_first_success_rank(ranking) or "none",
        "depth0_success": depth0_success,
        "reflection_success": reflection_success,
        "reflection_helped": bool(reflection_success and not depth0_success),
        "regression_validation_status": str(
            _dict(patch_validation.get("regression_validation")).get("status") or ""
        ),
        "baseline_regression_caveat": bool(
            phase4.get("baseline_regression_caveat", False)
        ),
        "full_suite_green_claim_allowed": bool(
            phase4.get("full_suite_green_claim_allowed", False)
        ),
    }


def _phase4_search_claim(
    *,
    success_count: int,
    first_success_rank: Any,
    full_suite_green_claim_allowed: bool,
    baseline_caveat: bool,
    evidence_level: str,
) -> dict[str, Any]:
    if success_count <= 0:
        claim = "no_validated_repair_found"
        evidence = "No candidate passed the available validation command."
    elif full_suite_green_claim_allowed:
        claim = "full_suite_repair_validated"
        evidence = "At least one candidate passed target validation and regression."
    elif baseline_caveat:
        claim = "target_repair_validated_with_baseline_regression_caveat"
        evidence = (
            "A candidate fixed the target failure, but broad regression still has "
            "an unchanged baseline failure."
        )
    else:
        claim = "target_repair_validated"
        evidence = "A candidate passed the available target validation command."
    return {
        "claim": claim,
        "evidence": evidence,
        "success_count": max(0, success_count),
        "first_success_rank": first_success_rank,
        "evidence_level": evidence_level,
        "baseline_regression_caveat": baseline_caveat,
        "full_suite_green_claim_allowed": full_suite_green_claim_allowed,
    }


def _phase4_first_success_rank(rows: list[dict[str, Any]]) -> int | None:
    for index, row in enumerate(rows, start=1):
        if bool(row.get("success", False)):
            return index
    return None


def _phase4_first_per_key(
    rows: list[dict[str, Any]],
    key: str,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    selected: list[dict[str, Any]] = []
    for row in rows:
        value = str(row.get(key) or "unknown")
        if value in seen:
            continue
        seen.add(value)
        selected.append(row)
    return selected


def _phase4_strategy_rerun(
    *,
    enabled: bool,
    patch_candidates: dict[str, Any],
    patch_validation: dict[str, Any],
    strategy_rerun_limit: int,
    strategy_rerun_timeout: int,
) -> dict[str, Any]:
    if not enabled:
        return _phase4_strategy_rerun_skipped(
            enabled=False,
            reason="strategy_rerun_disabled",
        )
    repository_root = Path(str(patch_validation.get("repository_root") or ""))
    if not repository_root.exists():
        return _phase4_strategy_rerun_skipped(
            enabled=True,
            reason="repository_root_missing",
        )
    test_args = [
        str(item)
        for item in _list(patch_validation.get("recommended_pytest_args"))
        if str(item)
    ]
    if not test_args:
        return _phase4_strategy_rerun_skipped(
            enabled=True,
            reason="recommended_pytest_args_missing",
        )
    candidates = [
        candidate
        for candidate in (
            _patch_candidate_from_dict(row)
            for row in _list(patch_candidates.get("candidates"))
        )
        if candidate is not None
    ]
    limit = max(1, min(_int(strategy_rerun_limit), len(candidates)))
    if not candidates:
        return _phase4_strategy_rerun_skipped(
            enabled=True,
            reason="valid_patch_candidates_missing",
        )
    localization_scores = _phase4_localization_scores_from_validation(
        patch_validation,
    )
    specs = [
        {
            "name": "beam_full",
            "component": "beam_search",
            "config": {
                "use_prior_ranking": True,
                "use_diversity_reranking": True,
                "use_candidate_deduplication": True,
            },
        },
        {
            "name": "without_prior_ranking",
            "component": "candidate_ranking",
            "config": {
                "use_prior_ranking": False,
                "use_diversity_reranking": True,
                "use_candidate_deduplication": True,
            },
        },
        {
            "name": "without_diversity_reranking",
            "component": "diversity_reranking",
            "config": {
                "use_prior_ranking": True,
                "use_diversity_reranking": False,
                "use_candidate_deduplication": True,
            },
        },
        {
            "name": "without_candidate_deduplication",
            "component": "candidate_deduplication",
            "config": {
                "use_prior_ranking": True,
                "use_diversity_reranking": True,
                "use_candidate_deduplication": False,
            },
        },
    ]
    strategies: list[dict[str, Any]] = []
    for spec in specs:
        strategies.append(
            _phase4_run_one_search_strategy(
                repository_root=repository_root,
                candidates=candidates,
                localization_scores=localization_scores,
                test_args=test_args,
                limit=limit,
                timeout=max(1, _int(strategy_rerun_timeout)),
                spec=spec,
            )
        )
    baseline = _dict(next((row for row in strategies if row.get("name") == "beam_full"), {}))
    return {
        "enabled": True,
        "status": "pass",
        "reason": "strategy_rerun_completed",
        "repository_root": str(repository_root),
        "test_args": test_args,
        "candidate_count": len(candidates),
        "rerun_limit": limit,
        "timeout": max(1, _int(strategy_rerun_timeout)),
        "strategy_count": len(strategies),
        "total_evaluated_count": sum(
            _int(row.get("evaluated_count", 0)) for row in strategies
        ),
        "success_count": sum(_int(row.get("success_count", 0)) for row in strategies),
        "best_strategy": _phase4_best_strategy(strategies),
        "strategies": strategies,
        "ablation_deltas": _phase4_strategy_rerun_deltas(
            baseline=baseline,
            strategies=strategies,
        ),
    }


def _phase4_run_one_search_strategy(
    *,
    repository_root: Path,
    candidates: list[Any],
    localization_scores: dict[str, float],
    test_args: list[str],
    limit: int,
    timeout: int,
    spec: dict[str, Any],
) -> dict[str, Any]:
    config = _dict(spec.get("config"))
    try:
        search = BeamPatchSearch(
            sandbox=Sandbox(timeout=timeout),
            beam_width=limit,
            max_depth=0,
            candidate_pool_size=limit,
            use_prior_ranking=bool(config.get("use_prior_ranking", True)),
            use_diversity_reranking=bool(
                config.get("use_diversity_reranking", True)
            ),
            use_candidate_deduplication=bool(
                config.get("use_candidate_deduplication", True)
            ),
        )
        nodes = search.search(
            repository_root,
            candidates,
            localization_scores=localization_scores,
            test_args=test_args,
        )
    except Exception as exc:  # pragma: no cover - defensive artifact path
        return {
            "name": str(spec.get("name") or ""),
            "component": str(spec.get("component") or ""),
            "status": "error",
            "reason": type(exc).__name__,
            "message": str(exc),
            "config": config,
            "evaluated_count": 0,
            "success_count": 0,
            "success_rate": 0.0,
            "first_success_rank": None,
            "best_candidate_id": "",
            "results": [],
        }
    results = [
        _phase4_candidate_ranking_row(
            _validation_result_to_dict(node),
            rank=index,
        )
        for index, node in enumerate(nodes, start=1)
    ]
    first_success_rank = _phase4_first_success_rank(results)
    success_count = sum(1 for row in results if bool(row.get("success", False)))
    best = _dict(results[0] if results else {})
    return {
        "name": str(spec.get("name") or ""),
        "component": str(spec.get("component") or ""),
        "status": "pass",
        "reason": "strategy_evaluated",
        "config": config,
        "evaluated_count": len(results),
        "success_count": success_count,
        "success_rate": round(success_count / len(results), 4) if results else 0.0,
        "first_success_rank": first_success_rank,
        "failures_before_first_success": (
            first_success_rank - 1 if first_success_rank is not None else len(results)
        ),
        "best_candidate_id": str(best.get("candidate_id") or ""),
        "best_rule_id": str(best.get("rule_id") or ""),
        "best_variant": str(best.get("variant") or ""),
        "results": results,
    }


def _phase4_strategy_rerun_deltas(
    *,
    baseline: dict[str, Any],
    strategies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    baseline_rank = _int(baseline.get("first_success_rank", 0))
    baseline_rate = _float(baseline.get("success_rate", 0.0))
    rows: list[dict[str, Any]] = []
    for row in strategies:
        name = str(row.get("name") or "")
        if name == "beam_full":
            continue
        rank = _int(row.get("first_success_rank", 0))
        if baseline_rank and rank:
            status = "same" if rank == baseline_rank else (
                "regression" if rank > baseline_rank else "improvement"
            )
        elif baseline_rank and not rank:
            status = "regression"
        elif rank and not baseline_rank:
            status = "improvement"
        else:
            status = "same"
        rows.append(
            {
                "variant": name,
                "component": str(row.get("component") or ""),
                "status": status,
                "baseline_first_success_rank": baseline.get("first_success_rank"),
                "variant_first_success_rank": row.get("first_success_rank"),
                "delta_first_success_rank": rank - baseline_rank
                if rank and baseline_rank
                else 0,
                "baseline_success_rate": baseline_rate,
                "variant_success_rate": _float(row.get("success_rate", 0.0)),
                "delta_success_rate": round(
                    _float(row.get("success_rate", 0.0)) - baseline_rate,
                    4,
                ),
            }
        )
    return rows


def _phase4_best_strategy(strategies: list[dict[str, Any]]) -> str:
    if not strategies:
        return ""
    best = max(
        strategies,
        key=lambda row: (
            _int(row.get("success_count", 0)),
            -_int(row.get("first_success_rank", 999999) or 999999),
            _float(row.get("success_rate", 0.0)),
        ),
    )
    return str(best.get("name") or "")


def _phase4_localization_scores_from_validation(
    patch_validation: dict[str, Any],
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for row_value in _list(patch_validation.get("results")):
        row = _dict(row_value)
        function_id = str(row.get("target_function_id") or "")
        if not function_id:
            continue
        scores[function_id] = max(
            scores.get(function_id, 0.0),
            _float(row.get("score", 0.0)),
        )
    return scores


def _phase4_strategy_rerun_skipped(
    *,
    enabled: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "status": "skipped" if not enabled else "blocked",
        "reason": reason,
        "repository_root": "",
        "test_args": [],
        "candidate_count": 0,
        "rerun_limit": 0,
        "timeout": 0,
        "strategy_count": 0,
        "total_evaluated_count": 0,
        "success_count": 0,
        "best_strategy": "",
        "strategies": [],
        "ablation_deltas": [],
    }


def _build_repository_test_environment_repair_plan(
    summary: dict[str, Any],
    *,
    controller: dict[str, Any],
    selected_action: dict[str, Any],
    snapshot_paths: dict[str, str],
) -> dict[str, Any]:
    readiness = _dict(summary.get("analysis_readiness"))
    blocker = str(
        readiness.get("repository_test_setup_doctor_blocker")
        or readiness.get("blocker")
        or controller.get("primary_blocker")
        or "unknown_environment_blocker"
    )
    recommended_install = str(
        summary.get("repository_test_recommended_install_command")
        or summary.get("recommended_install_command")
        or ""
    )
    recommended_test = str(
        readiness.get("planned_repository_test_command")
        or summary.get("planned_repository_test_command")
        or summary.get("recommended_test_command")
        or ""
    )
    setup_next_action = str(
        readiness.get("repository_test_setup_doctor_next_action")
        or summary.get("repository_test_setup_doctor_next_action")
        or ""
    )
    missing_dependency_modules = _missing_dependency_modules_from_summary(summary)
    missing_dependency_install_hint = _missing_dependency_install_hint(
        missing_dependency_modules
    )
    status = "pass"
    reason = "environment_repair_plan_recorded"
    actions = _environment_repair_actions(
        blocker=blocker,
        recommended_install=recommended_install,
        recommended_test=recommended_test,
        setup_next_action=setup_next_action,
        missing_dependency_modules=missing_dependency_modules,
        missing_dependency_install_hint=missing_dependency_install_hint,
    )
    return {
        "status": status,
        "reason": reason,
        "blocker": blocker,
        "message": (
            "Repository tests are blocked by environment or test-tool setup. "
            "This artifact records repair guidance only; no dependency install "
            "or test execution was performed by this auto action."
        ),
        "selected_controller_action": str(selected_action.get("id") or ""),
        "selected_controller_reason": str(selected_action.get("reason") or ""),
        "controller_stage": str(controller.get("current_stage") or ""),
        "controller_blocker": str(controller.get("primary_blocker") or ""),
        "setup_doctor_status": str(
            readiness.get("repository_test_setup_doctor_status") or ""
        ),
        "setup_doctor_blocker": str(
            readiness.get("repository_test_setup_doctor_blocker") or ""
        ),
        "setup_doctor_next_action": setup_next_action,
        "environment_status": str(
            summary.get("repository_test_environment_status") or ""
        ),
        "environment_reason": str(
            summary.get("repository_test_environment_reason") or ""
        ),
        "setup_status": str(
            summary.get("repository_test_environment_setup_status") or ""
        ),
        "setup_reason": str(
            summary.get("repository_test_environment_setup_reason") or ""
        ),
        "setup_supported": bool(
            summary.get("repository_test_environment_setup_supported", False)
        ),
        "setup_result_status": str(
            summary.get("repository_test_environment_setup_result_status") or ""
        ),
        "setup_result_reason": str(
            summary.get("repository_test_environment_setup_result_reason") or ""
        ),
        "test_tool_available": summary.get("repository_test_tool_available"),
        "recommended_test_command": recommended_test,
        "recommended_install_command": recommended_install,
        "planned_failure_category": str(
            summary.get("planned_repository_test_failure_category") or ""
        ),
        "planned_failure_signal": str(
            summary.get("planned_repository_test_failure_signal") or ""
        ),
        "missing_dependency_modules": missing_dependency_modules,
        "missing_dependency_install_hint": missing_dependency_install_hint,
        "ci_install_command_candidates": [
            str(item)
            for item in _list(summary.get("repository_test_ci_install_command_candidates"))
        ],
        "planned_environment_variable_names": [
            str(item)
            for item in _list(summary.get("planned_repository_test_environment_variable_names"))
        ],
        "repository_test_environment_json": str(
            summary.get("repository_test_environment_json") or ""
        ),
        "repository_test_environment_markdown": str(
            summary.get("repository_test_environment_markdown") or ""
        ),
        "repository_test_setup_doctor_json": str(
            summary.get("repository_test_setup_doctor_json") or ""
        ),
        "pre_action_controller_json": str(
            snapshot_paths.get("pre_action_controller_json") or ""
        ),
        "auto_installed_dependencies": False,
        "next_actions": actions,
    }


def _environment_repair_actions(
    *,
    blocker: str,
    recommended_install: str,
    recommended_test: str,
    setup_next_action: str,
    missing_dependency_modules: list[str],
    missing_dependency_install_hint: str,
) -> list[str]:
    actions: list[str] = []
    if setup_next_action:
        actions.append(setup_next_action)
    if recommended_install:
        actions.append(f"Prepare dependencies with: {recommended_install}")
    if missing_dependency_modules:
        actions.append(
            "Install or declare the package that provides missing module(s): "
            f"{', '.join(missing_dependency_modules)}."
        )
    if missing_dependency_install_hint and not recommended_install:
        actions.append(
            "Best-effort direct install hint: "
            f"{missing_dependency_install_hint}"
        )
    if "test_tool_missing" in blocker:
        actions.append("Install the missing test runner or use an available fallback runner.")
    if "missing_dependency" in blocker:
        actions.append("Install missing project dependencies before rerunning tests.")
    if "framework" in blocker:
        actions.append("Configure required framework test settings or environment variables.")
    if recommended_test:
        actions.append(f"Rerun repository tests with: {recommended_test}")
    actions.append(
        "Rerun github_repo_intelligence with --execution-profile agent-auto after the environment is repaired."
    )
    deduped: list[str] = []
    for action in actions:
        if action and action not in deduped:
            deduped.append(action)
    return deduped


def _missing_dependency_modules_from_summary(summary: dict[str, Any]) -> list[str]:
    signals = [
        str(summary.get("planned_repository_test_failure_signal") or ""),
    ]
    execution_result = _read_json(
        str(summary.get("repository_test_execution_result_json") or "")
    )
    if execution_result:
        signals.append(str(execution_result.get("failure_signal") or ""))
    modules: list[str] = []
    for signal in signals:
        module = _missing_dependency_module_from_signal(signal)
        if module and module not in modules:
            modules.append(module)
    return modules


def _missing_dependency_module_from_signal(signal: str) -> str:
    prefix = "missing_module:"
    if not signal.startswith(prefix):
        return ""
    module = signal[len(prefix) :].strip()
    if not module:
        return ""
    return module.split()[0].strip("`'\"")


def _missing_dependency_install_hint(modules: list[str]) -> str:
    safe_modules = [
        module
        for module in modules
        if module.replace("_", "").replace("-", "").replace(".", "").isalnum()
    ]
    if not safe_modules:
        return ""
    return "python -m pip install " + " ".join(safe_modules)


def _write_repository_test_environment_repair_plan_artifacts(
    payload: dict[str, Any],
    output_dir: Path,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "repository_test_environment_repair_plan.json"
    markdown_path = output_dir / "repository_test_environment_repair_plan.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        _render_repository_test_environment_repair_plan_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_environment_repair_plan_json": str(json_path),
        "repository_test_environment_repair_plan_markdown": str(markdown_path),
    }


def _render_repository_test_environment_repair_plan_markdown(
    payload: dict[str, Any],
) -> str:
    lines = [
        "# Repository Test Environment Repair Plan",
        "",
        f"- Status: `{_markdown_cell(payload.get('status') or '')}`",
        f"- Reason: `{_markdown_cell(payload.get('reason') or '')}`",
        f"- Blocker: `{_markdown_cell(payload.get('blocker') or 'none')}`",
        f"- Setup Doctor: `{_markdown_cell(payload.get('setup_doctor_status') or 'none')}`/`{_markdown_cell(payload.get('setup_doctor_blocker') or 'none')}`",
        f"- Environment: `{_markdown_cell(payload.get('environment_status') or 'none')}`/`{_markdown_cell(payload.get('environment_reason') or 'none')}`",
        f"- Setup Plan: `{_markdown_cell(payload.get('setup_status') or 'none')}`/`{_markdown_cell(payload.get('setup_reason') or 'none')}`",
        f"- Setup Supported: {str(bool(payload.get('setup_supported', False))).lower()}",
        (
            "- Recommended Install Command: "
            f"`{_markdown_cell(payload.get('recommended_install_command') or 'none')}`"
        ),
        (
            "- Recommended Test Command: "
            f"`{_markdown_cell(payload.get('recommended_test_command') or 'none')}`"
        ),
        (
            "- Planned Failure: "
            f"`{_markdown_cell(payload.get('planned_failure_category') or 'none')}`/"
            f"`{_markdown_cell(payload.get('planned_failure_signal') or 'none')}`"
        ),
        (
            "- Missing Dependency Modules: "
            f"`{_markdown_cell(', '.join(_list(payload.get('missing_dependency_modules'))) or 'none')}`"
        ),
        (
            "- Missing Dependency Install Hint: "
            f"`{_markdown_cell(payload.get('missing_dependency_install_hint') or 'none')}`"
        ),
        (
            "- Auto Installed Dependencies: "
            f"{str(bool(payload.get('auto_installed_dependencies', False))).lower()}"
        ),
        f"- Message: {_markdown_cell(payload.get('message') or '')}",
        "",
        "## Source Artifacts",
        "",
        (
            "- Repository Test Environment JSON: "
            f"`{_markdown_cell(payload.get('repository_test_environment_json') or 'none')}`"
        ),
        (
            "- Repository Test Setup Doctor JSON: "
            f"`{_markdown_cell(payload.get('repository_test_setup_doctor_json') or 'none')}`"
        ),
        (
            "- Pre-action Controller JSON: "
            f"`{_markdown_cell(payload.get('pre_action_controller_json') or 'none')}`"
        ),
        "",
        "## CI Install Command Candidates",
        "",
    ]
    candidates = _list(payload.get("ci_install_command_candidates"))
    for command in candidates:
        lines.append(f"- `{_markdown_cell(command)}`")
    if not candidates:
        lines.append("- none")
    lines.extend(["", "## Planned Environment Variables", ""])
    env_names = _list(payload.get("planned_environment_variable_names"))
    for name in env_names:
        lines.append(f"- `{_markdown_cell(name)}`")
    if not env_names:
        lines.append("- none")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _build_repository_test_regression_guard(
    summary: dict[str, Any],
    *,
    controller: dict[str, Any],
    selected_action: dict[str, Any],
    snapshot_paths: dict[str, str],
) -> dict[str, Any]:
    readiness = _dict(summary.get("analysis_readiness"))
    dynamic_level = str(readiness.get("dynamic_evidence_level") or "none")
    result_status = str(readiness.get("planned_repository_test_result_status") or "")
    command = str(readiness.get("planned_repository_test_command") or "")
    status = "pass" if dynamic_level == "passing_tests" and result_status == "pass" else "skipped"
    reason = (
        "repository_tests_passed_registered_as_regression_guard"
        if status == "pass"
        else "passing_repository_tests_missing"
    )
    message = (
        "Passing repository tests were registered as regression-only guard "
        "evidence. They are not failing-test localization evidence and do not "
        "prove any repair."
    )
    return {
        "status": status,
        "reason": reason,
        "message": message,
        "guard_role": "regression_validation_only",
        "command": command,
        "result_status": result_status,
        "dynamic_evidence_level": dynamic_level,
        "usable_for_localization": False,
        "usable_for_patch_validation": status == "pass",
        "repair_claim_allowed": False,
        "selected_controller_action": str(selected_action.get("id") or ""),
        "selected_controller_reason": str(selected_action.get("reason") or ""),
        "controller_stage": str(controller.get("current_stage") or ""),
        "controller_blocker": str(controller.get("primary_blocker") or ""),
        "dynamic_evidence_json": str(
            summary.get("repository_test_dynamic_evidence_json") or ""
        ),
        "dynamic_evidence_markdown": str(
            summary.get("repository_test_dynamic_evidence_markdown") or ""
        ),
        "test_execution_result_json": str(
            summary.get("repository_test_execution_result_json") or ""
        ),
        "pre_action_controller_json": str(
            snapshot_paths.get("pre_action_controller_json") or ""
        ),
        "next_actions": [
            "Use this command as a regression check after future patch generation.",
            (
                "Provide a failing test, mutation ground truth, bug report, or "
                "controlled failure overlay before repair localization."
            ),
        ],
    }


def _write_repository_test_regression_guard_artifacts(
    payload: dict[str, Any],
    output_dir: Path,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "repository_test_regression_guard.json"
    markdown_path = output_dir / "repository_test_regression_guard.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        _render_repository_test_regression_guard_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_regression_guard_json": str(json_path),
        "repository_test_regression_guard_markdown": str(markdown_path),
    }


def _render_repository_test_regression_guard_markdown(
    payload: dict[str, Any],
) -> str:
    lines = [
        "# Repository Test Regression Guard",
        "",
        f"- Status: `{_markdown_cell(payload.get('status') or '')}`",
        f"- Reason: `{_markdown_cell(payload.get('reason') or '')}`",
        f"- Guard Role: `{_markdown_cell(payload.get('guard_role') or '')}`",
        f"- Command: `{_markdown_cell(payload.get('command') or 'none')}`",
        f"- Test Result: `{_markdown_cell(payload.get('result_status') or 'none')}`",
        (
            "- Dynamic Evidence Level: "
            f"`{_markdown_cell(payload.get('dynamic_evidence_level') or 'none')}`"
        ),
        (
            "- Usable For Localization: "
            f"{str(bool(payload.get('usable_for_localization', False))).lower()}"
        ),
        (
            "- Usable For Patch Validation: "
            f"{str(bool(payload.get('usable_for_patch_validation', False))).lower()}"
        ),
        (
            "- Repair Claim Allowed: "
            f"{str(bool(payload.get('repair_claim_allowed', False))).lower()}"
        ),
        f"- Message: {_markdown_cell(payload.get('message') or '')}",
        "",
        "## Source Artifacts",
        "",
        (
            "- Dynamic Evidence JSON: "
            f"`{_markdown_cell(payload.get('dynamic_evidence_json') or 'none')}`"
        ),
        (
            "- Test Execution Result JSON: "
            f"`{_markdown_cell(payload.get('test_execution_result_json') or 'none')}`"
        ),
        (
            "- Pre-action Controller JSON: "
            f"`{_markdown_cell(payload.get('pre_action_controller_json') or 'none')}`"
        ),
        "",
        "## Next Actions",
        "",
    ]
    for item in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(item)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _auto_stop_reason(
    action_id: str,
    selected_action: dict[str, Any],
    current_kwargs: dict[str, Any],
) -> str:
    if action_id == "run_search_and_ablation_evaluation":
        return "phase_goal_reached:patch_validation_ready"
    if action_id == "adjust_source_filters" and _source_filter_rerun_kwargs(
        current_kwargs
    ) is None:
        return "source_filters_already_broad"
    if action_id in {
        "expand_static_candidate_search",
        "mine_static_bug_signals",
        "build_static_graph_fault_ranking",
    } and _static_mining_rerun_kwargs(current_kwargs) is None:
        return "static_mining_already_broad"
    if (
        action_id == "adjust_application_source_focus"
        and _static_mining_rerun_kwargs(current_kwargs) is None
    ):
        return "application_source_focus_already_broad"
    if action_id == "discover_repository_tests" and _test_discovery_rerun_kwargs(
        current_kwargs
    ) is None:
        return "test_discovery_already_broad"
    if action_id == "run_repository_tests_with_checkout" and bool(
        current_kwargs.get("checkout_repository_tests", False)
    ):
        return "selected_action_already_applied"
    if action_id == "collect_dynamic_failure_evidence" and bool(
        current_kwargs.get("checkout_repository_tests", False)
    ) and bool(current_kwargs.get("auto_repository_test_retry", False)):
        return "selected_action_already_applied"
    if (
        action_id == "generate_controlled_failure_overlay"
        and _failure_overlay_rerun_kwargs(current_kwargs) is None
    ):
        return "selected_action_already_applied"
    if (
        action_id == "regenerate_safe_patch_candidates"
        and _safe_patch_candidate_rerun_kwargs(current_kwargs) is None
    ):
        return "selected_action_already_applied"
    if (
        action_id == "generate_llm_patch_candidates"
        and _safe_patch_candidate_rerun_kwargs(
            current_kwargs,
            target_patch_mode="llm",
        )
        is None
    ):
        return "selected_action_already_applied"
    if (
        action_id == "generate_hybrid_patch_candidates"
        and _safe_patch_candidate_rerun_kwargs(
            current_kwargs,
            target_patch_mode="hybrid",
        )
        is None
    ):
        return "selected_action_already_applied"
    if (
        action_id == "narrow_repository_tests_after_timeout"
        and _timeout_narrowing_rerun_kwargs(current_kwargs) is None
    ):
        return "selected_action_already_applied"
    if not bool(selected_action.get("executable_now", False)):
        return "selected_action_not_executable"
    return f"unsupported_auto_action:{action_id or 'none'}"


def _auto_trace_item(
    *,
    iteration: int,
    summary: dict[str, Any],
    controller: dict[str, Any],
    selected_action: dict[str, Any],
) -> dict[str, Any]:
    readiness = _dict(summary.get("analysis_readiness"))
    fault = _dict(summary.get("fault_localization"))
    goal_readiness = _dict(summary.get("agent_goal_readiness"))
    return {
        "iteration": iteration,
        "observe_stage": str(readiness.get("current_stage") or ""),
        "observe_blocker": str(readiness.get("blocker") or ""),
        "observe_dynamic_evidence_level": str(
            readiness.get("dynamic_evidence_level") or "none"
        ),
        "observe_fault_localization_mode": str(fault.get("mode") or ""),
        "observe_fault_localization_status": str(fault.get("status") or ""),
        "observe_repository_llm_patch_generation_status": str(
            summary.get("repository_llm_patch_generation_status") or ""
        ),
        "observe_repository_llm_patch_generation_reason": str(
            summary.get("repository_llm_patch_generation_reason") or ""
        ),
        "observe_repository_llm_patch_generation_fallback_used": bool(
            summary.get("repository_llm_patch_generation_fallback_used", False)
        ),
        "observe_repository_llm_reflection_status": str(
            summary.get("repository_llm_reflection_status") or ""
        ),
        "observe_repository_llm_reflection_reason": str(
            summary.get("repository_llm_reflection_reason") or ""
        ),
        "observe_repository_llm_reflection_blocked": bool(
            summary.get("repository_llm_reflection_blocked", False)
        ),
        "observe_agent_goal_readiness_status": str(
            goal_readiness.get("status") or ""
        ),
        "observe_agent_goal_readiness_failed_criteria_count": _int(
            goal_readiness.get("failed_criteria_count", 0)
        ),
        "observe_agent_goal_readiness_failed_criteria": [
            str(item) for item in _list(goal_readiness.get("failed_criteria"))
        ],
        "plan_selected_action": str(selected_action.get("id") or ""),
        "plan_action_phase": str(selected_action.get("phase") or ""),
        "plan_action_tool": str(selected_action.get("tool") or ""),
        "plan_executable_now": bool(selected_action.get("executable_now", False)),
        "plan_reason": str(selected_action.get("reason") or ""),
        "controller_status": str(controller.get("status") or ""),
    }


def _auto_action_record(
    *,
    action_id: str,
    selected_action: dict[str, Any],
    controller: dict[str, Any],
    before_summary: dict[str, Any],
    after_report: GitHubRepoAgentReport,
    after_summary: dict[str, Any],
    rerun_kwargs: dict[str, Any],
    snapshot_paths: dict[str, str],
) -> dict[str, Any]:
    before_goal_readiness = _dict(before_summary.get("agent_goal_readiness"))
    after_goal_readiness = _dict(after_summary.get("agent_goal_readiness"))
    after_readiness = _dict(after_summary.get("analysis_readiness"))
    after_fault = _dict(after_summary.get("fault_localization"))
    after_reflection = _dict(after_summary.get("reflection_summary"))
    after_guard = _dict(after_summary.get("repository_test_regression_guard"))
    after_environment_repair = _dict(
        after_summary.get("repository_test_environment_repair_plan")
    )
    after_timeout_status = str(
        _timeout_narrowing_field(after_summary, "status") or ""
    )
    after_timeout_reason = str(
        _timeout_narrowing_field(after_summary, "reason") or ""
    )
    rerun_agent_summary = after_report.summary
    loop_audit = _auto_transition_audit(
        action_id=action_id,
        selected_action=selected_action,
        before_summary=before_summary,
        after_summary=after_summary,
        after_report=after_report,
    )
    return {
        "action_id": action_id,
        "phase": str(selected_action.get("phase") or ""),
        "tool": str(selected_action.get("tool") or ""),
        "reason": str(selected_action.get("reason") or ""),
        "command": str(selected_action.get("command") or ""),
        "before_stage": str(controller.get("current_stage") or ""),
        "before_blocker": str(controller.get("primary_blocker") or ""),
        "before_agent_goal_readiness_status": str(
            before_goal_readiness.get("status") or ""
        ),
        "before_agent_goal_readiness_failed_criteria_count": _int(
            before_goal_readiness.get("failed_criteria_count", 0)
        ),
        "before_agent_goal_readiness_failed_criteria": [
            str(item) for item in _list(before_goal_readiness.get("failed_criteria"))
        ],
        "before_llm_patch_generation_status": str(
            before_summary.get("repository_llm_patch_generation_status") or ""
        ),
        "before_llm_reflection_status": str(
            before_summary.get("repository_llm_reflection_status") or ""
        ),
        "before_llm_reflection_blocked": bool(
            before_summary.get("repository_llm_reflection_blocked", False)
        ),
        "after_status": after_report.status,
        "after_stage": str(after_readiness.get("current_stage") or ""),
        "after_blocker": str(after_readiness.get("blocker") or ""),
        "after_agent_goal_readiness_status": str(
            after_goal_readiness.get("status") or ""
        ),
        "after_agent_goal_readiness_failed_criteria_count": _int(
            after_goal_readiness.get("failed_criteria_count", 0)
        ),
        "after_agent_goal_readiness_failed_criteria": [
            str(item) for item in _list(after_goal_readiness.get("failed_criteria"))
        ],
        "after_llm_patch_generation_status": str(
            after_summary.get("repository_llm_patch_generation_status") or ""
        ),
        "after_llm_reflection_status": str(
            after_summary.get("repository_llm_reflection_status") or ""
        ),
        "after_llm_reflection_blocked": bool(
            after_summary.get("repository_llm_reflection_blocked", False)
        ),
        "after_dynamic_evidence_level": str(
            after_readiness.get("dynamic_evidence_level") or "none"
        ),
        "after_fault_localization_mode": str(after_fault.get("mode") or ""),
        "after_fault_localization_status": str(after_fault.get("status") or ""),
        "after_fault_localization_top_function": str(
            after_fault.get("top_function") or ""
        ),
        "after_repository_checkout_status": str(
            rerun_agent_summary.get("repository_checkout_status") or ""
        ),
        "after_repository_test_result_status": str(
            rerun_agent_summary.get("planned_repository_test_result_status") or ""
        ),
        "after_failure_overlay_status": str(
            rerun_agent_summary.get("repository_test_failure_overlay_status") or ""
        ),
        "after_failure_overlay_reason": str(
            rerun_agent_summary.get("repository_test_failure_overlay_reason") or ""
        ),
        "after_failure_overlay_supported_candidates": _int(
            rerun_agent_summary.get(
                "repository_test_failure_overlay_supported_candidates",
                0,
            )
        ),
        "after_failure_overlay_attempted_cases": _int(
            rerun_agent_summary.get(
                "repository_test_failure_overlay_attempted_cases",
                0,
            )
        ),
        "after_failure_overlay_dynamic_evidence_level": str(
            rerun_agent_summary.get(
                "repository_test_failure_overlay_dynamic_evidence_level"
            )
            or ""
        ),
        "rerun_include": [
            str(item) for item in _list(rerun_kwargs.get("include"))
        ],
        "rerun_exclude": [
            str(item) for item in _list(rerun_kwargs.get("exclude"))
        ],
        "rerun_target_prefix": str(rerun_kwargs.get("target_prefix") or ""),
        "rerun_max_sources": _int(rerun_kwargs.get("max_sources", 0)),
        "rerun_max_candidates": _int(rerun_kwargs.get("max_candidates", 0)),
        "after_regression_guard_status": str(after_guard.get("status") or ""),
        "after_regression_guard_reason": str(after_guard.get("reason") or ""),
        "after_regression_guard_command": str(after_guard.get("command") or ""),
        "after_environment_repair_plan_status": str(
            after_environment_repair.get("status") or ""
        ),
        "after_environment_repair_plan_reason": str(
            after_environment_repair.get("reason") or ""
        ),
        "after_environment_repair_plan_blocker": str(
            after_environment_repair.get("blocker") or ""
        ),
        "after_timeout_narrowing_status": after_timeout_status,
        "after_timeout_narrowing_reason": after_timeout_reason,
        "after_timeout_narrowing_executed": bool(
            _timeout_narrowing_field(after_summary, "executed")
        ),
        "after_timeout_narrowing_attempt_count": _int(
            _timeout_narrowing_field(after_summary, "attempt_count")
        ),
        "after_timeout_narrowing_selected_failure_category": str(
            _timeout_narrowing_field(after_summary, "selected_failure_category")
            or ""
        ),
        "after_patch_candidates_status": str(
            rerun_agent_summary.get("repository_test_patch_candidates_status") or ""
        ),
        "after_patch_safety_gate_status": str(
            rerun_agent_summary.get("repository_patch_safety_gate_status") or ""
        ),
        "after_patch_safety_gate_blocked_count": _int(
            rerun_agent_summary.get("repository_patch_safety_gate_blocked_count", 0)
        ),
        "after_patch_validation_status": str(
            rerun_agent_summary.get("repository_test_patch_validation_status") or ""
        ),
        "after_patch_validation_candidate_count": _int(
            rerun_agent_summary.get(
                "repository_test_patch_validation_candidate_count",
                0,
            )
        ),
        "after_patch_validation_safety_blocked_candidate_count": _int(
            rerun_agent_summary.get(
                "repository_test_patch_validation_safety_blocked_candidate_count",
                0,
            )
        ),
        "after_patch_validation_success_count": _int(
            rerun_agent_summary.get("repository_test_patch_validation_success_count", 0)
        ),
        "after_repair_ready": bool(
            rerun_agent_summary.get("repository_test_repair_ready", False)
        ),
        "after_reflection_candidate_count": _int(
            rerun_agent_summary.get(
                "repository_test_patch_validation_reflection_candidate_count",
                0,
            )
        ),
        "after_successful_reflection_count": _int(
            rerun_agent_summary.get(
                "repository_test_patch_validation_successful_reflection_count",
                0,
            )
        ),
        "after_reflection_trace_status": str(after_reflection.get("status") or ""),
        "after_reflection_trace_reason": str(after_reflection.get("reason") or ""),
        "after_reflection_failure_type_counts": _dict(
            after_reflection.get("failure_type_counts")
        ),
        "after_reflection_repair_ready": bool(
            after_reflection.get("repair_ready", False)
        ),
        "checkout_repository_tests": bool(
            rerun_kwargs.get("checkout_repository_tests", False)
        ),
        "run_repository_test_retry_prerequisites": bool(
            rerun_kwargs.get("run_repository_test_retry_prerequisites", False)
        ),
        "run_repository_test_retry": bool(
            rerun_kwargs.get("run_repository_test_retry", False)
        ),
        "auto_repository_test_retry": bool(
            rerun_kwargs.get("auto_repository_test_retry", False)
        ),
        "repository_patch_generation_mode": str(
            rerun_kwargs.get("repository_patch_generation_mode") or ""
        ),
        "repository_llm_patch_candidate_limit": _int(
            rerun_kwargs.get("repository_llm_patch_candidate_limit", 0)
        ),
        "repository_test_reflection_mode": str(
            rerun_kwargs.get("repository_test_reflection_mode") or ""
        ),
        "repository_test_reflection_rounds": _int(
            rerun_kwargs.get("repository_test_reflection_rounds", 0)
        ),
        "repository_test_reflection_width": _int(
            rerun_kwargs.get("repository_test_reflection_width", 0)
        ),
        "repository_test_timeout": _int(
            rerun_kwargs.get("repository_test_timeout", 0)
        ),
        **loop_audit,
        **snapshot_paths,
    }


def _auto_local_action_record(
    *,
    action_id: str,
    selected_action: dict[str, Any],
    controller: dict[str, Any],
    before_summary: dict[str, Any],
    after_report: GitHubRepoAgentReport,
    after_summary: dict[str, Any],
    snapshot_paths: dict[str, str],
) -> dict[str, Any]:
    before_goal_readiness = _dict(before_summary.get("agent_goal_readiness"))
    after_goal_readiness = _dict(after_summary.get("agent_goal_readiness"))
    after_readiness = _dict(after_summary.get("analysis_readiness"))
    after_fault = _dict(after_summary.get("fault_localization"))
    after_reflection = _dict(after_summary.get("reflection_summary"))
    after_guard = _dict(after_summary.get("repository_test_regression_guard"))
    after_environment_repair = _dict(
        after_summary.get("repository_test_environment_repair_plan")
    )
    after_timeout_status = str(
        _timeout_narrowing_field(after_summary, "status") or ""
    )
    after_timeout_reason = str(
        _timeout_narrowing_field(after_summary, "reason") or ""
    )
    after_phase4 = _dict(after_summary.get("phase4_search_evaluation"))
    after_phase4_execution = _dict(after_phase4.get("execution"))
    agent_summary = after_report.summary
    loop_audit = _auto_transition_audit(
        action_id=action_id,
        selected_action=selected_action,
        before_summary=before_summary,
        after_summary=after_summary,
        after_report=after_report,
    )
    return {
        "action_id": action_id,
        "phase": str(selected_action.get("phase") or ""),
        "tool": str(selected_action.get("tool") or ""),
        "reason": str(selected_action.get("reason") or ""),
        "command": str(selected_action.get("command") or ""),
        "before_stage": str(controller.get("current_stage") or ""),
        "before_blocker": str(controller.get("primary_blocker") or ""),
        "before_agent_goal_readiness_status": str(
            before_goal_readiness.get("status") or ""
        ),
        "before_agent_goal_readiness_failed_criteria_count": _int(
            before_goal_readiness.get("failed_criteria_count", 0)
        ),
        "before_agent_goal_readiness_failed_criteria": [
            str(item) for item in _list(before_goal_readiness.get("failed_criteria"))
        ],
        "after_status": after_report.status,
        "after_stage": str(after_readiness.get("current_stage") or ""),
        "after_blocker": str(after_readiness.get("blocker") or ""),
        "after_agent_goal_readiness_status": str(
            after_goal_readiness.get("status") or ""
        ),
        "after_agent_goal_readiness_failed_criteria_count": _int(
            after_goal_readiness.get("failed_criteria_count", 0)
        ),
        "after_agent_goal_readiness_failed_criteria": [
            str(item) for item in _list(after_goal_readiness.get("failed_criteria"))
        ],
        "after_dynamic_evidence_level": str(
            after_readiness.get("dynamic_evidence_level") or "none"
        ),
        "after_fault_localization_mode": str(after_fault.get("mode") or ""),
        "after_fault_localization_status": str(after_fault.get("status") or ""),
        "after_fault_localization_top_function": str(
            after_fault.get("top_function") or ""
        ),
        "after_repository_checkout_status": str(
            agent_summary.get("repository_checkout_status") or ""
        ),
        "after_repository_test_result_status": str(
            agent_summary.get("planned_repository_test_result_status") or ""
        ),
        "after_failure_overlay_status": str(
            agent_summary.get("repository_test_failure_overlay_status") or ""
        ),
        "after_failure_overlay_reason": str(
            agent_summary.get("repository_test_failure_overlay_reason") or ""
        ),
        "after_failure_overlay_supported_candidates": _int(
            agent_summary.get("repository_test_failure_overlay_supported_candidates", 0)
        ),
        "after_failure_overlay_attempted_cases": _int(
            agent_summary.get("repository_test_failure_overlay_attempted_cases", 0)
        ),
        "after_failure_overlay_dynamic_evidence_level": str(
            agent_summary.get("repository_test_failure_overlay_dynamic_evidence_level")
            or ""
        ),
        "after_regression_guard_status": str(after_guard.get("status") or ""),
        "after_regression_guard_reason": str(after_guard.get("reason") or ""),
        "after_regression_guard_command": str(after_guard.get("command") or ""),
        "after_environment_repair_plan_status": str(
            after_environment_repair.get("status") or ""
        ),
        "after_environment_repair_plan_reason": str(
            after_environment_repair.get("reason") or ""
        ),
        "after_environment_repair_plan_blocker": str(
            after_environment_repair.get("blocker") or ""
        ),
        "after_phase4_evaluation_status": str(after_phase4.get("status") or ""),
        "after_phase4_evaluation_executed": bool(
            after_phase4_execution.get("executed", False)
        ),
        "after_phase4_evaluation_execution_status": str(
            after_phase4_execution.get("status") or ""
        ),
        "after_patch_candidates_status": str(
            agent_summary.get("repository_test_patch_candidates_status") or ""
        ),
        "after_patch_validation_status": str(
            agent_summary.get("repository_test_patch_validation_status") or ""
        ),
        "after_patch_validation_success_count": _int(
            agent_summary.get("repository_test_patch_validation_success_count", 0)
        ),
        "after_repair_ready": bool(
            agent_summary.get("repository_test_repair_ready", False)
        ),
        "after_reflection_candidate_count": _int(
            agent_summary.get(
                "repository_test_patch_validation_reflection_candidate_count",
                0,
            )
        ),
        "after_successful_reflection_count": _int(
            agent_summary.get(
                "repository_test_patch_validation_successful_reflection_count",
                0,
            )
        ),
        "after_reflection_trace_status": str(after_reflection.get("status") or ""),
        "after_reflection_trace_reason": str(after_reflection.get("reason") or ""),
        "after_reflection_failure_type_counts": _dict(
            after_reflection.get("failure_type_counts")
        ),
        "after_reflection_repair_ready": bool(
            after_reflection.get("repair_ready", False)
        ),
        **loop_audit,
        **snapshot_paths,
    }


def _auto_trace_after_fields(
    after_summary: dict[str, Any],
    after_report: GitHubRepoAgentReport,
) -> dict[str, Any]:
    after_readiness = _dict(after_summary.get("analysis_readiness"))
    after_fault = _dict(after_summary.get("fault_localization"))
    after_goal_readiness = _dict(after_summary.get("agent_goal_readiness"))
    after_reflection = _dict(after_summary.get("reflection_summary"))
    after_guard = _dict(after_summary.get("repository_test_regression_guard"))
    after_environment_repair = _dict(
        after_summary.get("repository_test_environment_repair_plan")
    )
    after_timeout_status = str(
        _timeout_narrowing_field(after_summary, "status") or ""
    )
    after_timeout_reason = str(
        _timeout_narrowing_field(after_summary, "reason") or ""
    )
    after_phase4 = _dict(after_summary.get("phase4_search_evaluation"))
    after_phase4_execution = _dict(after_phase4.get("execution"))
    return {
        "verify_status": after_report.status,
        "verify_stage": str(after_readiness.get("current_stage") or ""),
        "verify_blocker": str(after_readiness.get("blocker") or ""),
        "verify_dynamic_evidence_level": str(
            after_readiness.get("dynamic_evidence_level") or "none"
        ),
        "verify_fault_localization_mode": str(after_fault.get("mode") or ""),
        "verify_fault_localization_status": str(after_fault.get("status") or ""),
        "verify_agent_goal_readiness_status": str(
            after_goal_readiness.get("status") or ""
        ),
        "verify_agent_goal_readiness_failed_criteria_count": _int(
            after_goal_readiness.get("failed_criteria_count", 0)
        ),
        "verify_agent_goal_readiness_failed_criteria": [
            str(item) for item in _list(after_goal_readiness.get("failed_criteria"))
        ],
        "verify_patch_validation_status": str(
            after_reflection.get("patch_validation_status") or ""
        ),
        "verify_patch_validation_candidate_count": _int(
            after_readiness.get("patch_validation_candidate_count", 0)
        ),
        "verify_patch_validation_safety_blocked_candidate_count": _int(
            after_readiness.get("patch_validation_safety_blocked_candidate_count", 0)
        ),
        "verify_regression_guard_status": str(after_guard.get("status") or ""),
        "verify_failure_overlay_status": str(
            after_summary.get("repository_test_failure_overlay_status") or ""
        ),
        "verify_failure_overlay_reason": str(
            after_summary.get("repository_test_failure_overlay_reason") or ""
        ),
        "verify_environment_repair_plan_status": str(
            after_environment_repair.get("status") or ""
        ),
        "verify_timeout_narrowing_status": after_timeout_status,
        "verify_timeout_narrowing_reason": after_timeout_reason,
        "verify_timeout_narrowing_executed": bool(
            _timeout_narrowing_field(after_summary, "executed")
        ),
        "verify_timeout_narrowing_attempt_count": _int(
            _timeout_narrowing_field(after_summary, "attempt_count")
        ),
        "verify_timeout_narrowing_selected_failure_category": str(
            _timeout_narrowing_field(after_summary, "selected_failure_category")
            or ""
        ),
        "verify_phase4_evaluation_status": str(after_phase4.get("status") or ""),
        "verify_phase4_evaluation_executed": bool(
            after_phase4_execution.get("executed", False)
        ),
        "verify_phase4_evaluation_execution_status": str(
            after_phase4_execution.get("status") or ""
        ),
        "verify_repair_ready": bool(after_reflection.get("repair_ready", False)),
        "reflect_reason": str(after_reflection.get("reason") or ""),
    }


def _auto_trace_loop_fields(action_record: dict[str, Any]) -> dict[str, Any]:
    return {
        "verify_outcome": str(action_record.get("loop_verify_outcome") or ""),
        "verify_progress": bool(action_record.get("loop_verify_progress", False)),
        "verify_evidence": str(action_record.get("loop_verify_evidence") or ""),
        "reflect_status": str(action_record.get("loop_reflect_status") or ""),
        "reflect_failure_type": str(
            action_record.get("loop_reflect_failure_type") or ""
        ),
        "reflect_strategy": str(action_record.get("loop_reflect_strategy") or ""),
        "replan_policy": str(action_record.get("loop_replan_policy") or ""),
        "replan_next_action": str(
            action_record.get("loop_replan_next_action") or ""
        ),
    }


def _timeout_narrowing_field(summary: dict[str, Any], field: str) -> Any:
    nested = _dict(summary.get("repository_test_timeout_narrowing"))
    if field in nested:
        value = nested.get(field)
        if value is not None and value != "":
            return value
    return summary.get(f"repository_test_timeout_narrowing_{field}")


def _auto_transition_audit(
    *,
    action_id: str,
    selected_action: dict[str, Any],
    before_summary: dict[str, Any],
    after_summary: dict[str, Any],
    after_report: GitHubRepoAgentReport,
) -> dict[str, Any]:
    before_readiness = _dict(before_summary.get("analysis_readiness"))
    after_readiness = _dict(after_summary.get("analysis_readiness"))
    before_fault = _dict(before_summary.get("fault_localization"))
    after_fault = _dict(after_summary.get("fault_localization"))
    before_guard = _dict(before_summary.get("repository_test_regression_guard"))
    after_guard = _dict(after_summary.get("repository_test_regression_guard"))
    before_overlay_status = str(
        before_summary.get("repository_test_failure_overlay_status") or ""
    )
    after_overlay_status = str(
        after_summary.get("repository_test_failure_overlay_status") or ""
    )
    after_overlay_reason = str(
        after_summary.get("repository_test_failure_overlay_reason") or ""
    )
    after_overlay_level = str(
        after_summary.get("repository_test_failure_overlay_dynamic_evidence_level")
        or ""
    )
    before_environment_repair = _dict(
        before_summary.get("repository_test_environment_repair_plan")
    )
    after_environment_repair = _dict(
        after_summary.get("repository_test_environment_repair_plan")
    )
    before_timeout_status = str(
        _timeout_narrowing_field(before_summary, "status") or ""
    )
    after_timeout_status = str(
        _timeout_narrowing_field(after_summary, "status") or ""
    )
    after_timeout_reason = str(
        _timeout_narrowing_field(after_summary, "reason") or ""
    )
    after_timeout_attempt_count = _int(
        _timeout_narrowing_field(after_summary, "attempt_count")
    )
    after_timeout_selected_failure = str(
        _timeout_narrowing_field(after_summary, "selected_failure_category") or ""
    )
    before_phase4 = _dict(before_summary.get("phase4_search_evaluation"))
    after_phase4 = _dict(after_summary.get("phase4_search_evaluation"))
    before_phase4_execution = _dict(before_phase4.get("execution"))
    after_phase4_execution = _dict(after_phase4.get("execution"))
    before_reflection = _dict(before_summary.get("reflection_summary"))
    after_reflection = _dict(after_summary.get("reflection_summary"))
    before_goal_readiness = _dict(before_summary.get("agent_goal_readiness"))
    after_goal_readiness = _dict(after_summary.get("agent_goal_readiness"))
    after_controller = _dict(after_summary.get("agent_controller"))
    next_selected_action = _dict(after_controller.get("selected_action"))

    before_stage = str(before_readiness.get("current_stage") or "")
    after_stage = str(after_readiness.get("current_stage") or "")
    before_blocker = str(before_readiness.get("blocker") or "")
    after_blocker = str(after_readiness.get("blocker") or "")
    before_dynamic = str(before_readiness.get("dynamic_evidence_level") or "none")
    after_dynamic = str(after_readiness.get("dynamic_evidence_level") or "none")
    before_fault_mode = str(before_fault.get("mode") or "")
    after_fault_mode = str(after_fault.get("mode") or "")
    before_application_candidate_count = _int(
        before_fault.get("application_candidate_count", 0)
    )
    after_application_candidate_count = _int(
        after_fault.get("application_candidate_count", 0)
    )
    after_patch_status = str(
        after_report.summary.get("repository_test_patch_validation_status") or ""
    )
    after_patch_success = _int(
        after_report.summary.get("repository_test_patch_validation_success_count", 0)
    )
    after_repair_ready = bool(
        after_report.summary.get("repository_test_repair_ready", False)
    )
    after_repair_scope = str(
        after_report.summary.get("repository_test_repair_validation_scope") or ""
    )
    before_reflection_count = _int(
        before_reflection.get("reflection_candidate_count", 0)
    )
    after_reflection_count = _int(
        after_reflection.get("reflection_candidate_count", 0)
    )
    before_goal_status = str(before_goal_readiness.get("status") or "")
    after_goal_status = str(after_goal_readiness.get("status") or "")
    before_goal_failed_count = _int(
        before_goal_readiness.get("failed_criteria_count", 0)
    )
    after_goal_failed_count = _int(
        after_goal_readiness.get("failed_criteria_count", 0)
    )

    progress = True
    if (
        bool(after_phase4_execution.get("executed", False))
        and not bool(before_phase4_execution.get("executed", False))
    ):
        outcome = "phase4_evaluation_recorded"
        evidence = (
            "Repository Phase 4 search/ablation evaluation artifact was executed "
            f"with status {after_phase4_execution.get('status') or 'unknown'}."
        )
    elif after_repair_ready:
        outcome = "phase_goal_reached:patch_validation_ready"
        evidence = "Patch validation produced a verified repair-ready artifact."
    elif after_patch_status == "pass" and after_patch_success > 0:
        outcome = "patch_validation_not_repair_ready"
        evidence = (
            "Patch validation produced passing candidate evidence, but "
            f"repair_ready is false ({after_repair_scope or 'not_verified'})."
        )
    elif after_fault_mode == "dynamic" and before_fault_mode != "dynamic":
        outcome = "dynamic_fault_localization_ready"
        evidence = "Dynamic evidence was fused into a Top-k fault-localization ranking."
    elif after_application_candidate_count > before_application_candidate_count:
        outcome = "application_source_candidates_ranked"
        evidence = (
            "Application-source candidates in Top-k increased from "
            f"{before_application_candidate_count} to "
            f"{after_application_candidate_count}."
        )
    elif after_dynamic not in {"", "none", "not_executed"} and after_dynamic != before_dynamic:
        outcome = "dynamic_evidence_collected"
        evidence = f"Dynamic evidence changed from {before_dynamic} to {after_dynamic}."
    elif (
        str(after_guard.get("status") or "") == "pass"
        and str(before_guard.get("status") or "") != "pass"
    ):
        outcome = "regression_guard_recorded"
        evidence = "Passing repository tests were recorded as regression guards."
    elif after_overlay_status and after_overlay_status != before_overlay_status:
        outcome = (
            "failure_overlay_evidence_ready"
            if after_overlay_status == "pass"
            else "failure_overlay_attempted"
        )
        evidence = (
            "Controlled failure overlay status changed from "
            f"{before_overlay_status or 'none'} to {after_overlay_status} "
            f"({after_overlay_reason or after_overlay_level or 'no_reason'})."
        )
    elif (
        str(after_environment_repair.get("status") or "") == "pass"
        and str(before_environment_repair.get("status") or "") != "pass"
    ):
        outcome = "environment_repair_plan_recorded"
        evidence = "Repository test environment repair plan artifact was written."
    elif after_timeout_status and after_timeout_status != before_timeout_status:
        outcome = (
            "timeout_narrowing_executed"
            if bool(_timeout_narrowing_field(after_summary, "executed"))
            else "timeout_narrowing_recorded"
        )
        evidence = (
            "Repository test timeout narrowing changed from "
            f"{before_timeout_status or 'none'} to {after_timeout_status} "
            f"({after_timeout_reason or 'no_reason'}, "
            f"attempts={after_timeout_attempt_count}, "
            f"selected_failure={after_timeout_selected_failure or 'none'})."
        )
    elif after_reflection_count > before_reflection_count:
        outcome = "reflection_candidates_generated"
        evidence = (
            "Reflection loop generated "
            f"{after_reflection_count - before_reflection_count} new candidate(s)."
        )
    elif after_goal_status == "pass" and before_goal_status != "pass":
        outcome = "agent_goal_readiness_passed"
        evidence = "Agent goal readiness changed to pass after the selected action."
    elif (
        before_goal_failed_count > 0
        and after_goal_failed_count < before_goal_failed_count
    ):
        outcome = "agent_goal_readiness_improved"
        evidence = (
            "Agent goal readiness failed criteria decreased from "
            f"{before_goal_failed_count} to {after_goal_failed_count}."
        )
    elif after_stage and after_stage != before_stage:
        outcome = "stage_changed"
        evidence = f"Agent stage changed from {before_stage} to {after_stage}."
    elif after_blocker != before_blocker:
        outcome = "blocker_changed"
        evidence = f"Blocker changed from {before_blocker or 'none'} to {after_blocker or 'none'}."
    else:
        progress = False
        outcome = "no_progress_detected"
        evidence = "No stage, blocker, dynamic evidence, patch, or reflection progress was detected."

    if outcome == "patch_validation_not_repair_ready":
        failure_type = after_repair_scope or "repair_not_verified"
        reflect_status = "needs_replan"
    else:
        failure_type = "none" if progress else _auto_reflection_failure_type(
            after_summary,
            after_report,
        )
        reflect_status = "verified_progress" if progress else "needs_replan"
    reflect_strategy = _auto_reflection_strategy(
        failure_type=failure_type,
        outcome=outcome,
        action_id=action_id,
    )
    replan_policy = _auto_replan_policy(
        after_stage=after_stage,
        repair_ready=after_repair_ready,
        progress=progress,
        next_selected_action=next_selected_action,
        goal_readiness_passed=after_goal_status == "pass",
    )
    return {
        "loop_observe_stage": before_stage,
        "loop_observe_blocker": before_blocker,
        "loop_plan_action": action_id,
        "loop_plan_reason": str(selected_action.get("reason") or ""),
        "loop_verify_outcome": outcome,
        "loop_verify_progress": progress,
        "loop_verify_evidence": evidence,
        "loop_verify_agent_goal_readiness_status": after_goal_status,
        "loop_verify_agent_goal_readiness_failed_criteria_count": (
            after_goal_failed_count
        ),
        "loop_verify_agent_goal_readiness_failed_criteria": [
            str(item) for item in _list(after_goal_readiness.get("failed_criteria"))
        ],
        "loop_reflect_status": reflect_status,
        "loop_reflect_failure_type": failure_type,
        "loop_reflect_strategy": reflect_strategy,
        "loop_replan_policy": replan_policy,
        "loop_replan_next_action": str(next_selected_action.get("id") or ""),
        "loop_replan_next_action_executable": bool(
            next_selected_action.get("executable_now", False)
        ),
    }


def _auto_reflection_failure_type(
    after_summary: dict[str, Any],
    after_report: GitHubRepoAgentReport,
) -> str:
    readiness = _dict(after_summary.get("analysis_readiness"))
    blocker = str(readiness.get("blocker") or "")
    patch_status = str(
        after_report.summary.get("repository_test_patch_validation_status") or ""
    )
    if patch_status == "fail":
        failure_counts = _dict(
            after_report.summary.get(
                "repository_test_patch_validation_failure_type_counts"
            )
        )
        if failure_counts:
            return sorted(
                failure_counts.items(),
                key=lambda item: (-_int(item[1]), str(item[0])),
            )[0][0]
        return "patch_validation_failed"
    result_status = str(
        after_report.summary.get("planned_repository_test_result_status") or ""
    )
    if result_status in {"timeout", "import_error", "fail", "error"}:
        return result_status
    if "environment" in blocker or "test_tool_missing" in blocker:
        return "environment_blocker"
    if "checkout" in blocker:
        return "checkout_blocker"
    if blocker:
        return blocker
    return "no_new_evidence"


def _auto_reflection_strategy(
    *,
    failure_type: str,
    outcome: str,
    action_id: str,
) -> str:
    if failure_type == "none":
        return f"Continue from verified outcome `{outcome}`."
    if failure_type in {"environment_blocker", "import_error"}:
        return "Prepare repository test environment repair advice before rerunning dynamic evidence collection."
    if failure_type == "timeout":
        return "Narrow the repository test command or increase timeout before retrying."
    if failure_type in {"test_failure", "patch_validation_failed"}:
        return "Run or expand patch reflection, then validate refined candidates in sandbox."
    if failure_type in {"regression_failed", "repair_not_verified"}:
        return (
            "Inspect regression failure output, run patch reflection, and "
            "validate refined candidates before declaring repair success."
        )
    if failure_type == "checkout_blocker":
        return "Retry with checkout enabled or provide repository_test_root."
    return f"Re-observe artifacts after `{action_id}` and choose a narrower recovery action."


def _auto_replan_policy(
    *,
    after_stage: str,
    repair_ready: bool,
    progress: bool,
    next_selected_action: dict[str, Any],
    goal_readiness_passed: bool = False,
) -> str:
    next_action_id = str(next_selected_action.get("id") or "")
    if after_stage == "phase3_patch_validation" and repair_ready:
        return "stop_phase_goal_reached"
    if not progress:
        return "classify_failure_and_replan"
    if next_action_id and bool(next_selected_action.get("executable_now", False)):
        return "continue_observe_plan_act"
    if goal_readiness_passed:
        return "stop_agent_goal_readiness_passed"
    if next_action_id:
        return "manual_or_blocked_next_action"
    return "inspect_generated_artifacts"


def _auto_loop_audit(
    actions: list[dict[str, Any]],
    trace: list[dict[str, Any]],
    stop_reason: str,
) -> dict[str, Any]:
    outcome_counts: dict[str, int] = {}
    reflect_counts: dict[str, int] = {}
    replan_counts: dict[str, int] = {}
    goal_readiness_counts: dict[str, int] = {}
    progress_count = 0
    goal_readiness_passed_action_count = 0
    for action in actions:
        if bool(action.get("loop_verify_progress", False)):
            progress_count += 1
        _increment(outcome_counts, str(action.get("loop_verify_outcome") or ""))
        _increment(reflect_counts, str(action.get("loop_reflect_status") or ""))
        _increment(replan_counts, str(action.get("loop_replan_policy") or ""))
        goal_status = str(
            action.get("loop_verify_agent_goal_readiness_status")
            or action.get("after_agent_goal_readiness_status")
            or ""
        )
        _increment(goal_readiness_counts, goal_status)
        if goal_status == "pass":
            goal_readiness_passed_action_count += 1
    return {
        "loop": [
            "observe",
            "plan",
            "act",
            "verify",
            "reflect",
            "replan",
        ],
        "stop_reason": str(stop_reason or ""),
        "action_count": len(actions),
        "trace_count": len(trace),
        "progress_count": progress_count,
        "no_progress_count": max(0, len(actions) - progress_count),
        "verify_outcome_counts": _top_counts(outcome_counts),
        "reflect_status_counts": _top_counts(reflect_counts),
        "replan_policy_counts": _top_counts(replan_counts),
        "goal_readiness_status_counts": _top_counts(goal_readiness_counts),
        "goal_readiness_passed_action_count": goal_readiness_passed_action_count,
        "final_goal_readiness_status": str(
            actions[-1].get("loop_verify_agent_goal_readiness_status")
            if actions
            else ""
        ),
        "last_replan_next_action": str(
            actions[-1].get("loop_replan_next_action") if actions else ""
        ),
        "complete_loop_recorded": all(
            key in action
            for action in actions
            for key in (
                "loop_observe_stage",
                "loop_plan_action",
                "loop_verify_outcome",
                "loop_reflect_strategy",
                "loop_replan_policy",
            )
        )
        if actions
        else False,
    }


def _agent_decision_timeline_summary(payload: dict[str, Any]) -> dict[str, Any]:
    controller = _dict(payload.get("agent_controller"))
    auto_trace = [_dict(item) for item in _list(payload.get("agent_auto_trace"))]
    loop = [str(item) for item in _list(controller.get("control_loop"))]
    if not loop:
        loop = ["observe", "plan", "act", "verify", "reflect", "replan"]
    steps = (
        [_agent_timeline_step_from_auto_trace(item) for item in auto_trace]
        if auto_trace
        else [_agent_timeline_step_from_controller(payload, controller)]
    )
    complete_steps = sum(1 for step in steps if bool(step.get("complete", False)))
    executed_steps = sum(1 for step in steps if bool(_dict(step.get("act")).get("executed", False)))
    blocked_steps = sum(
        1
        for step in steps
        if str(_dict(step.get("act")).get("status") or "") in {"blocked", "stopped"}
    )
    loop_complete = loop == ["observe", "plan", "act", "verify", "reflect", "replan"]
    status = "pass" if loop_complete and complete_steps == len(steps) else "warning"
    return {
        "status": status,
        "reason": (
            "agent_decision_timeline_complete"
            if status == "pass"
            else "agent_decision_timeline_incomplete"
        ),
        "loop": loop,
        "source": "agent_auto_trace" if auto_trace else "agent_controller",
        "step_count": len(steps),
        "complete_step_count": complete_steps,
        "executed_step_count": executed_steps,
        "blocked_step_count": blocked_steps,
        "complete": bool(loop_complete and complete_steps == len(steps)),
        "steps": steps,
    }


def _agent_timeline_step_from_auto_trace(item: dict[str, Any]) -> dict[str, Any]:
    executed = bool(item.get("auto_executed", False))
    stop_reason = str(item.get("stop_reason") or "")
    step = {
        "iteration": _int(item.get("iteration", 0)),
        "observe": {
            "stage": str(item.get("observe_stage") or ""),
            "blocker": str(item.get("observe_blocker") or ""),
            "dynamic_evidence_level": str(
                item.get("observe_dynamic_evidence_level") or "none"
            ),
            "agent_goal_readiness_status": str(
                item.get("observe_agent_goal_readiness_status") or "none"
            ),
            "agent_goal_readiness_failed_criteria_count": _int(
                item.get("observe_agent_goal_readiness_failed_criteria_count", 0)
            ),
            "agent_goal_readiness_failed_criteria": [
                str(value)
                for value in _list(
                    item.get("observe_agent_goal_readiness_failed_criteria")
                )
            ],
            "fault_localization": (
                f"{item.get('observe_fault_localization_mode') or 'none'}/"
                f"{item.get('observe_fault_localization_status') or 'none'}"
            ),
        },
        "plan": {
            "selected_action": str(item.get("plan_selected_action") or ""),
            "phase": str(item.get("plan_action_phase") or ""),
            "tool": str(item.get("plan_action_tool") or ""),
            "executable_now": bool(item.get("plan_executable_now", False)),
            "reason": str(item.get("plan_reason") or ""),
        },
        "act": {
            "executed": executed,
            "status": "executed" if executed else "stopped",
            "snapshot_json": str(item.get("pre_action_controller_json") or ""),
            "stop_reason": stop_reason,
            "stop_category": str(item.get("stop_category") or ""),
        },
        "verify": {
            "status": str(item.get("verify_status") or ""),
            "stage": str(item.get("verify_stage") or ""),
            "outcome": str(
                item.get("verify_outcome")
                or item.get("verify_stage")
                or stop_reason
                or ""
            ),
            "progress": bool(item.get("verify_progress", False)),
            "dynamic_evidence_level": str(
                item.get("verify_dynamic_evidence_level") or "none"
            ),
            "agent_goal_readiness_status": str(
                item.get("verify_agent_goal_readiness_status") or "none"
            ),
            "agent_goal_readiness_failed_criteria_count": _int(
                item.get("verify_agent_goal_readiness_failed_criteria_count", 0)
            ),
            "agent_goal_readiness_failed_criteria": [
                str(value)
                for value in _list(
                    item.get("verify_agent_goal_readiness_failed_criteria")
                )
            ],
            "regression_guard_status": str(
                item.get("verify_regression_guard_status") or ""
            ),
            "environment_repair_plan_status": str(
                item.get("verify_environment_repair_plan_status") or ""
            ),
        },
        "reflect": {
            "status": str(item.get("reflect_status") or ("stopped" if stop_reason else "")),
            "failure_type": str(item.get("reflect_failure_type") or ""),
            "strategy": str(item.get("reflect_strategy") or ""),
            "reason": str(item.get("reflect_reason") or stop_reason or ""),
        },
        "replan": {
            "policy": str(item.get("replan_policy") or stop_reason or ""),
            "next_action": str(item.get("replan_next_action") or ""),
            "stop_reason": stop_reason,
        },
    }
    step["complete"] = _agent_timeline_step_complete(step)
    return step


def _agent_timeline_step_from_controller(
    payload: dict[str, Any],
    controller: dict[str, Any],
) -> dict[str, Any]:
    readiness = _dict(payload.get("analysis_readiness"))
    selected_action = _dict(controller.get("selected_action"))
    verification = _dict(controller.get("verification"))
    reflection = _dict(controller.get("reflection"))
    replan = _dict(controller.get("replan"))
    step = {
        "iteration": 1,
        "observe": {
            "stage": str(readiness.get("current_stage") or controller.get("current_stage") or ""),
            "blocker": str(readiness.get("blocker") or controller.get("primary_blocker") or ""),
            "dynamic_evidence_level": str(
                readiness.get("dynamic_evidence_level") or "none"
            ),
            "fault_localization": (
                f"{readiness.get('fault_localization_mode') or 'none'}/"
                f"{readiness.get('fault_localization_status') or 'none'}"
            ),
        },
        "plan": {
            "selected_action": str(selected_action.get("id") or ""),
            "phase": str(selected_action.get("phase") or ""),
            "tool": str(selected_action.get("tool") or ""),
            "executable_now": bool(selected_action.get("executable_now", False)),
            "reason": str(selected_action.get("reason") or ""),
        },
        "act": {
            "executed": False,
            "status": "planned",
            "snapshot_json": "",
            "stop_reason": "",
        },
        "verify": {
            "status": "planned",
            "stage": str(readiness.get("current_stage") or ""),
            "outcome": str(verification.get("success_condition") or ""),
            "progress": False,
            "dynamic_evidence_level": str(
                readiness.get("dynamic_evidence_level") or "none"
            ),
            "regression_guard_status": "",
            "environment_repair_plan_status": "",
        },
        "reflect": {
            "status": "planned",
            "failure_type": str(reflection.get("failure_type") or ""),
            "strategy": str(reflection.get("strategy") or ""),
            "reason": str(reflection.get("fallback_action") or ""),
        },
        "replan": {
            "policy": str(replan.get("next_policy") or ""),
            "next_action": str(replan.get("next_action") or selected_action.get("id") or ""),
            "stop_reason": "",
        },
    }
    step["complete"] = _agent_timeline_step_complete(step)
    return step


def _agent_timeline_step_complete(step: dict[str, Any]) -> bool:
    observe = _dict(step.get("observe"))
    plan = _dict(step.get("plan"))
    act = _dict(step.get("act"))
    verify = _dict(step.get("verify"))
    reflect = _dict(step.get("reflect"))
    replan = _dict(step.get("replan"))
    return bool(
        str(observe.get("stage") or "")
        and str(plan.get("selected_action") or "")
        and str(act.get("status") or "")
        and str(verify.get("outcome") or verify.get("status") or "")
        and str(reflect.get("status") or reflect.get("strategy") or "")
        and str(replan.get("policy") or replan.get("next_action") or replan.get("stop_reason") or "")
    )


def _write_auto_controller_snapshot(
    summary: dict[str, Any],
    output_dir: Path,
    *,
    suffix: str,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    intelligence_json = output_dir / f"github_repo_intelligence_{suffix}.json"
    intelligence_markdown = output_dir / f"github_repo_intelligence_{suffix}.md"
    controller_json = output_dir / f"github_repo_agent_controller_{suffix}.json"
    controller_markdown = output_dir / f"github_repo_agent_controller_{suffix}.md"
    controller = _dict(summary.get("agent_controller"))
    intelligence_json.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    intelligence_markdown.write_text(
        _render_github_repo_intelligence_payload(summary),
        encoding="utf-8",
    )
    controller_json.write_text(
        json.dumps(controller, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    from code_intelligence_agent.agents.controller import (
        render_agent_controller_markdown,
    )

    controller_markdown.write_text(
        render_agent_controller_markdown(controller),
        encoding="utf-8",
    )
    return {
        "pre_action_intelligence_json": str(intelligence_json),
        "pre_action_intelligence_markdown": str(intelligence_markdown),
        "pre_action_controller_json": str(controller_json),
        "pre_action_controller_markdown": str(controller_markdown),
    }


def github_repo_intelligence_summary(
    report: GitHubRepoAgentReport,
) -> dict[str, Any]:
    summary = report.summary
    structure = _repository_structure_summary(report)
    repo_graph = _dict(structure.get("repo_graph"))
    static_fault_localization = _static_fault_localization_summary(
        report,
        repo_graph,
    )
    fault_localization = _unified_fault_localization_summary(
        report,
        static_fault_localization,
    )
    analysis_readiness = _analysis_readiness_summary(
        report,
        structure,
        fault_localization,
    )
    repository_test_discovery = _repository_test_discovery_summary(structure)
    reflection_summary = _reflection_trace_summary(report, summary)
    llm_reflection_audit = _repository_llm_reflection_audit(
        reflection_summary,
    )
    regression_guard = _repository_test_regression_guard_summary(report)
    environment_repair_plan = _repository_test_environment_repair_plan_summary(
        report,
    )
    patch_candidates_payload = _read_json(
        str(report.output_paths.get("repository_test_patch_candidates_json") or "")
    )
    llm_patch_audit = _repository_llm_patch_generation_audit(
        patch_candidates_payload,
        summary,
    )
    output_root = Path(report.output_dir)
    payload = {
        "repo": f"{report.owner}/{report.repo}",
        "repo_spec": report.repo_spec,
        "output_dir": report.output_dir,
        "status": report.status,
        "passed": report.passed,
        "status_reason": "upstream_agent_status",
        "status_source": "github_repo_agent",
        "upstream_agent_status": report.status,
        "upstream_agent_passed": report.passed,
        "preset": report.preset,
        "agent_invocation": _dict(summary.get("agent_invocation")),
        "repo_input": _dict(summary.get("repo_input")),
        "repository_ref": str(summary.get("repository_ref") or ""),
        "requested_ref": str(summary.get("requested_ref") or ""),
        "ref_source": str(summary.get("ref_source") or ""),
        "source_cache_dir": str(summary.get("source_cache_dir") or ""),
        "discovery_source": str(
            summary.get("discovery_source") or summary.get("source") or ""
        ),
        "discovery_cache_reuse": bool(
            summary.get("discovery_cache_reuse", False)
        ),
        "discovery_cache_reuse_reason": str(
            summary.get("discovery_cache_reuse_reason") or ""
        ),
        "discovery_cache_reuse_source": str(
            summary.get("discovery_cache_reuse_source") or ""
        ),
        "discovery_cache_preferred": bool(
            summary.get("discovery_cache_preferred", False)
        ),
        "discovery_cache_preferred_source": str(
            summary.get("discovery_cache_preferred_source") or ""
        ),
        "discovery_cache_fallback": bool(
            summary.get("discovery_cache_fallback", False)
        ),
        "discovery_cache_fallback_source": str(
            summary.get("discovery_cache_fallback_source") or ""
        ),
        "discovery_api_rate_limit_checkout_fallback": bool(
            summary.get("discovery_api_rate_limit_checkout_fallback", False)
        ),
        "discovery_api_rate_limit_status_code": summary.get(
            "discovery_api_rate_limit_status_code"
        ),
        "discovery_api_rate_limit_remaining": str(
            summary.get("discovery_api_rate_limit_remaining") or ""
        ),
        "github_error": _dict(summary.get("github_error")),
        "github_error_next_actions": [
            str(item) for item in _list(summary.get("next_actions"))
        ],
        "agent_auto_enabled": bool(summary.get("agent_auto_enabled", False)),
        "agent_auto_action_count": _int(
            summary.get("agent_auto_action_count", 0)
        ),
        "agent_auto_actions": _list(summary.get("agent_auto_actions")),
        "agent_auto_trace": _list(summary.get("agent_auto_trace")),
        "agent_auto_stop_reason": str(
            summary.get("agent_auto_stop_reason") or ""
        ),
        "agent_auto_stop_state": _dict(summary.get("agent_auto_stop_state")),
        "agent_auto_max_actions": _int(
            summary.get("agent_auto_max_actions", 0)
        ),
        "agent_auto_loop_audit": (
            _dict(summary.get("agent_auto_loop_audit"))
            or _auto_loop_audit(
                _list(summary.get("agent_auto_actions")),
                _list(summary.get("agent_auto_trace")),
                str(summary.get("agent_auto_stop_reason") or ""),
            )
        ),
        "static_intelligence_status": str(
            summary.get("static_intelligence_status") or ""
        ),
        "static_intelligence_level": str(
            summary.get("static_intelligence_level") or ""
        ),
        "static_intelligence_reason": str(
            summary.get("static_intelligence_reason") or ""
        ),
        "imported_source_count": _int(
            summary.get("static_intelligence_imported_source_count", 0)
        ),
        "selected_source_count": _int(
            summary.get("static_intelligence_selected_source_count", 0)
        ),
        "selected_signal_count": _int(
            summary.get("static_intelligence_selected_signal_count", 0)
        ),
        "total_signal_count": _int(
            summary.get("static_intelligence_total_signal_count", 0)
        ),
        "candidate_limit_applied": bool(
            summary.get("static_intelligence_candidate_limit_applied", False)
        ),
        "rule_counts": _dict(summary.get("static_intelligence_rule_counts")),
        "bug_type_counts": _dict(
            summary.get("static_intelligence_bug_type_counts")
        ),
        "quality_score": _float(
            summary.get("static_intelligence_quality_score", 0.0)
        ),
        "dynamic_validation_level": str(
            summary.get("static_intelligence_dynamic_validation_level")
            or "none"
        ),
        "repository_test_setup_doctor_status": str(
            summary.get("repository_test_setup_doctor_status") or ""
        ),
        "repository_test_setup_doctor_blocker": str(
            summary.get("repository_test_setup_doctor_blocker") or ""
        ),
        "repository_test_setup_doctor_next_action": str(
            summary.get("repository_test_setup_doctor_next_action") or ""
        ),
        "repository_test_setup_doctor_check_count": _int(
            summary.get("repository_test_setup_doctor_check_count", 0)
        ),
        "repository_test_setup_doctor_passed_check_count": _int(
            summary.get("repository_test_setup_doctor_passed_check_count", 0)
        ),
        "repository_test_setup_doctor_warning_check_count": _int(
            summary.get("repository_test_setup_doctor_warning_check_count", 0)
        ),
        "repository_test_setup_doctor_blocked_check_count": _int(
            summary.get("repository_test_setup_doctor_blocked_check_count", 0)
        ),
        "repository_test_setup_doctor_skipped_check_count": _int(
            summary.get("repository_test_setup_doctor_skipped_check_count", 0)
        ),
        "repository_test_setup_doctor_check_status_counts": _dict(
            summary.get("repository_test_setup_doctor_check_status_counts")
        ),
        "repository_test_setup_doctor_blocked_check_names": _list(
            summary.get("repository_test_setup_doctor_blocked_check_names")
        ),
        "repository_test_setup_doctor_warning_check_names": _list(
            summary.get("repository_test_setup_doctor_warning_check_names")
        ),
        "planned_repository_test_command": str(
            summary.get("planned_repository_test_command") or ""
        ),
        "planned_repository_test_runner": str(
            summary.get("planned_repository_test_runner") or ""
        ),
        "planned_repository_test_level": str(
            summary.get("planned_repository_test_level") or ""
        ),
        "planned_repository_test_source": str(
            summary.get("planned_repository_test_source") or ""
        ),
        "planned_repository_test_failure_context_line_count": _int(
            summary.get("planned_repository_test_failure_context_line_count", 0)
        ),
        "planned_repository_test_failure_category": str(
            summary.get("planned_repository_test_failure_category") or ""
        ),
        "planned_repository_test_failure_signal": str(
            summary.get("planned_repository_test_failure_signal") or ""
        ),
        "planned_repository_test_preferred_runner": str(
            summary.get("planned_repository_test_preferred_runner") or ""
        ),
        "planned_repository_test_runner_fallback_used": bool(
            summary.get("planned_repository_test_runner_fallback_used", False)
        ),
        "planned_repository_test_runner_fallback_reason": str(
            summary.get("planned_repository_test_runner_fallback_reason") or ""
        ),
        "planned_repository_test_runner_fallback_from": str(
            summary.get("planned_repository_test_runner_fallback_from") or ""
        ),
        "planned_repository_test_runner_fallback_to": str(
            summary.get("planned_repository_test_runner_fallback_to") or ""
        ),
        "recommended_install_command": str(
            summary.get("recommended_install_command") or ""
        ),
        "repository_test_tool_available": summary.get(
            "repository_test_tool_available"
        ),
        "repository_test_environment_status": str(
            summary.get("repository_test_environment_status") or ""
        ),
        "repository_test_environment_reason": str(
            summary.get("repository_test_environment_reason") or ""
        ),
        "repository_test_environment_setup_status": str(
            summary.get("repository_test_environment_setup_status") or ""
        ),
        "repository_test_environment_setup_reason": str(
            summary.get("repository_test_environment_setup_reason") or ""
        ),
        "repository_test_environment_setup_supported": bool(
            summary.get("repository_test_environment_setup_supported", False)
        ),
        "repository_test_environment_setup_result_status": str(
            summary.get("repository_test_environment_setup_result_status") or ""
        ),
        "repository_test_environment_setup_result_reason": str(
            summary.get("repository_test_environment_setup_result_reason") or ""
        ),
        "repository_test_timeout_narrowing_json": str(
            report.output_paths.get("repository_test_timeout_narrowing_json") or ""
        ),
        "repository_test_timeout_narrowing_markdown": str(
            report.output_paths.get("repository_test_timeout_narrowing_markdown")
            or ""
        ),
        "repository_test_timeout_narrowing_status": str(
            summary.get("repository_test_timeout_narrowing_status") or ""
        ),
        "repository_test_timeout_narrowing_reason": str(
            summary.get("repository_test_timeout_narrowing_reason") or ""
        ),
        "repository_test_timeout_narrowing_executed": bool(
            summary.get("repository_test_timeout_narrowing_executed", False)
        ),
        "repository_test_timeout_narrowing_attempt_count": _int(
            summary.get("repository_test_timeout_narrowing_attempt_count", 0)
        ),
        "repository_test_timeout_narrowing_selected_command": str(
            summary.get("repository_test_timeout_narrowing_selected_command") or ""
        ),
        "repository_test_timeout_narrowing_selected_failure_category": str(
            summary.get(
                "repository_test_timeout_narrowing_selected_failure_category"
            )
            or ""
        ),
        "repository_test_ci_install_command_candidates": [
            str(item)
            for item in _list(
                summary.get("repository_test_ci_install_command_candidates")
            )
        ],
        "planned_repository_test_environment_variable_names": [
            str(item)
            for item in _list(
                summary.get("planned_repository_test_environment_variable_names")
            )
        ],
        "repository_structure": structure,
        "repository_test_discovery": repository_test_discovery,
        "repo_graph": repo_graph,
        "analysis_readiness": analysis_readiness,
        "fault_localization": fault_localization,
        "static_fault_localization": static_fault_localization,
        "reflection_summary": reflection_summary,
        "repository_llm_reflection_audit": llm_reflection_audit,
        "repository_llm_reflection_config_audit": _dict(
            llm_reflection_audit.get("config_audit")
        ),
        "repository_llm_reflection_provider": str(
            llm_reflection_audit.get("provider") or ""
        ),
        "repository_llm_reflection_model": str(
            llm_reflection_audit.get("model") or ""
        ),
        "repository_llm_reflection_api_key_env": str(
            llm_reflection_audit.get("api_key_env") or ""
        ),
        "repository_llm_reflection_checked_api_key_envs": _list(
            llm_reflection_audit.get("checked_api_key_envs")
        ),
        "repository_llm_reflection_api_key_present": bool(
            llm_reflection_audit.get("api_key_present", False)
        ),
        "repository_llm_reflection_api_key_source": str(
            llm_reflection_audit.get("api_key_source") or ""
        ),
        "repository_llm_reflection_status": str(
            llm_reflection_audit.get("status") or ""
        ),
        "repository_llm_reflection_reason": str(
            llm_reflection_audit.get("reason") or ""
        ),
        "repository_llm_reflection_blocked": bool(
            llm_reflection_audit.get("blocked", False)
        ),
        "repository_llm_reflection_blocker": str(
            llm_reflection_audit.get("blocker") or ""
        ),
        "repository_test_regression_guard": regression_guard,
        "repository_test_environment_repair_plan": environment_repair_plan,
        "next_action": str(
            summary.get("static_intelligence_next_action") or ""
        ),
        "agent_json": str(report.output_paths.get("agent_json") or ""),
        "agent_markdown": str(report.output_paths.get("agent_markdown") or ""),
        "execution_plan_json": str(
            report.output_paths.get("agent_execution_plan_json") or ""
        ),
        "execution_plan_markdown": str(
            report.output_paths.get("agent_execution_plan_markdown") or ""
        ),
        "source_mining_markdown": str(
            summary.get("static_intelligence_primary_artifact") or ""
        ),
        "source_mining_json": str(report.output_paths.get("source_mining_json") or ""),
        "intelligence_json": str(
            output_root / "github_repo_intelligence.json"
        ),
        "intelligence_markdown": str(
            output_root / "github_repo_intelligence.md"
        ),
        "agent_controller_json": str(
            output_root / "github_repo_agent_controller.json"
        ),
        "agent_controller_markdown": str(
            output_root / "github_repo_agent_controller.md"
        ),
        "agent_action_registry_json": str(output_root / "agent_action_registry.json"),
        "agent_action_registry_markdown": str(output_root / "agent_action_registry.md"),
        "agent_policy_trace_json": str(output_root / "agent_policy_trace.json"),
        "agent_policy_trace_markdown": str(output_root / "agent_policy_trace.md"),
        "agent_invocation_json": str(output_root / "agent_invocation.json"),
        "agent_invocation_markdown": str(output_root / "agent_invocation.md"),
        "agent_goal_readiness_json": str(output_root / "agent_goal_readiness.json"),
        "agent_goal_readiness_markdown": str(output_root / "agent_goal_readiness.md"),
        "agent_decision_timeline_json": str(
            output_root / "agent_decision_timeline.json"
        ),
        "agent_decision_timeline_markdown": str(
            output_root / "agent_decision_timeline.md"
        ),
        "final_report_json": str(output_root / "final_report.json"),
        "final_report_markdown": str(output_root / "final_report.md"),
        "repository_structure_json": str(output_root / "repository_structure.json"),
        "repository_structure_markdown": str(output_root / "repository_structure.md"),
        "repository_test_discovery_json": str(
            output_root / "repository_test_discovery.json"
        ),
        "repository_test_discovery_markdown": str(
            output_root / "repository_test_discovery.md"
        ),
        "repo_graph_json": str(output_root / "repo_graph.json"),
        "repo_graph_markdown": str(output_root / "repo_graph.md"),
        "fault_localization_json": str(output_root / "fault_localization.json"),
        "fault_localization_markdown": str(output_root / "fault_localization.md"),
        "analysis_readiness_json": str(output_root / "analysis_readiness.json"),
        "analysis_readiness_markdown": str(output_root / "analysis_readiness.md"),
        "phase4_search_evaluation_json": str(
            output_root / "phase4_search_evaluation.json"
        ),
        "phase4_search_evaluation_markdown": str(
            output_root / "phase4_search_evaluation.md"
        ),
        "phase4_search_evaluation_execution_json": str(
            report.output_paths.get("phase4_search_evaluation_execution_json") or ""
        ),
        "phase4_search_evaluation_execution_markdown": str(
            report.output_paths.get("phase4_search_evaluation_execution_markdown")
            or ""
        ),
        "phase4_search_evaluation_executed": bool(
            summary.get("phase4_search_evaluation_executed", False)
        ),
        "phase4_search_evaluation_execution_status": str(
            summary.get("phase4_search_evaluation_execution_status") or ""
        ),
        "phase4_search_evaluation_execution_reason": str(
            summary.get("phase4_search_evaluation_execution_reason") or ""
        ),
        "phase4_search_evaluation_execution_mode": str(
            summary.get("phase4_search_evaluation_execution_mode") or ""
        ),
        "phase4_strategy_rerun_status": str(
            summary.get("phase4_strategy_rerun_status") or ""
        ),
        "phase4_strategy_rerun_reason": str(
            summary.get("phase4_strategy_rerun_reason") or ""
        ),
        "phase4_strategy_rerun_strategy_count": _int(
            summary.get("phase4_strategy_rerun_strategy_count", 0)
        ),
        "phase4_strategy_rerun_total_evaluated_count": _int(
            summary.get("phase4_strategy_rerun_total_evaluated_count", 0)
        ),
        "artifact_inventory_json": str(output_root / "artifact_inventory.json"),
        "artifact_inventory_markdown": str(output_root / "artifact_inventory.md"),
        "repository_test_environment_json": str(
            report.output_paths.get("repository_test_environment_json") or ""
        ),
        "repository_test_environment_markdown": str(
            report.output_paths.get("repository_test_environment_markdown") or ""
        ),
        "repository_test_setup_doctor_json": str(
            report.output_paths.get("repository_test_setup_doctor_json") or ""
        ),
        "repository_test_setup_doctor_markdown": str(
            report.output_paths.get("repository_test_setup_doctor_markdown") or ""
        ),
        "repository_test_environment_repair_plan_json": str(
            report.output_paths.get("repository_test_environment_repair_plan_json")
            or ""
        ),
        "repository_test_environment_repair_plan_markdown": str(
            report.output_paths.get(
                "repository_test_environment_repair_plan_markdown"
            )
            or ""
        ),
        "repository_test_execution_plan_json": str(
            report.output_paths.get("repository_test_execution_plan_json") or ""
        ),
        "repository_test_execution_plan_markdown": str(
            report.output_paths.get("repository_test_execution_plan_markdown") or ""
        ),
        "repository_test_execution_result_json": str(
            report.output_paths.get("repository_test_execution_result_json") or ""
        ),
        "repository_test_execution_result_markdown": str(
            report.output_paths.get("repository_test_execution_result_markdown") or ""
        ),
        "repository_test_dynamic_evidence_json": str(
            report.output_paths.get("repository_test_dynamic_evidence_json") or ""
        ),
        "repository_test_dynamic_evidence_markdown": str(
            report.output_paths.get("repository_test_dynamic_evidence_markdown") or ""
        ),
        "repository_test_failure_overlay_json": str(
            report.output_paths.get("repository_test_failure_overlay_json") or ""
        ),
        "repository_test_failure_overlay_markdown": str(
            report.output_paths.get("repository_test_failure_overlay_markdown") or ""
        ),
        "repository_test_analysis_source": str(
            summary.get("repository_test_analysis_source") or ""
        ),
        "repository_test_overlay_trigger_reason": str(
            summary.get("repository_test_overlay_trigger_reason") or ""
        ),
        "repository_test_failure_overlay_status": str(
            summary.get("repository_test_failure_overlay_status") or ""
        ),
        "repository_test_failure_overlay_reason": str(
            summary.get("repository_test_failure_overlay_reason") or ""
        ),
        "repository_test_failure_overlay_attempted_cases": _int(
            summary.get("repository_test_failure_overlay_attempted_cases", 0)
        ),
        "repository_test_failure_overlay_supported_candidates": _int(
            summary.get("repository_test_failure_overlay_supported_candidates", 0)
        ),
        "repository_test_failure_overlay_candidate_limit": _int(
            summary.get("repository_test_failure_overlay_candidate_limit", 0)
        ),
        "repository_test_failure_overlay_strategy_policy": str(
            summary.get("repository_test_failure_overlay_strategy_policy") or ""
        ),
        "repository_test_failure_overlay_candidate_rule_counts": _dict(
            summary.get("repository_test_failure_overlay_candidate_rule_counts")
        ),
        "repository_test_failure_overlay_attempted_rule_counts": _dict(
            summary.get("repository_test_failure_overlay_attempted_rule_counts")
        ),
        "repository_test_failure_overlay_triggered_rule_counts": _dict(
            summary.get("repository_test_failure_overlay_triggered_rule_counts")
        ),
        "repository_test_failure_overlay_candidate_rejection_count": _int(
            summary.get("repository_test_failure_overlay_candidate_rejection_count", 0)
        ),
        "repository_test_failure_overlay_candidate_rejection_counts": _dict(
            summary.get("repository_test_failure_overlay_candidate_rejection_counts")
        ),
        "repository_test_failure_overlay_dominant_rejection_reason": str(
            summary.get("repository_test_failure_overlay_dominant_rejection_reason")
            or ""
        ),
        "repository_test_failure_overlay_dominant_rejection_count": _int(
            summary.get("repository_test_failure_overlay_dominant_rejection_count", 0)
        ),
        "repository_test_failure_overlay_next_actionable_extension": _dict(
            summary.get("repository_test_failure_overlay_next_actionable_extension")
        ),
        "repository_test_failure_overlay_selected_rule": str(
            summary.get("repository_test_failure_overlay_selected_rule") or ""
        ),
        "repository_test_failure_overlay_selected_function": str(
            summary.get("repository_test_failure_overlay_selected_function") or ""
        ),
        "repository_test_failure_overlay_dynamic_evidence_level": str(
            summary.get("repository_test_failure_overlay_dynamic_evidence_level") or ""
        ),
        "repository_test_failure_overlay_validation_command": str(
            summary.get("repository_test_failure_overlay_validation_command") or ""
        ),
        "repository_test_regression_guard_json": str(
            report.output_paths.get("repository_test_regression_guard_json") or ""
        ),
        "repository_test_regression_guard_markdown": str(
            report.output_paths.get("repository_test_regression_guard_markdown") or ""
        ),
        "repository_test_patch_candidates_json": str(
            report.output_paths.get("repository_test_patch_candidates_json") or ""
        ),
        "repository_test_patch_candidates_markdown": str(
            report.output_paths.get("repository_test_patch_candidates_markdown") or ""
        ),
        "repository_test_patch_candidates_status": str(
            summary.get("repository_test_patch_candidates_status")
            or patch_candidates_payload.get("status")
            or ""
        ),
        "repository_test_patch_candidates_reason": str(
            summary.get("repository_test_patch_candidates_reason")
            or patch_candidates_payload.get("reason")
            or ""
        ),
        "repository_test_patch_candidate_count": _int(
            summary.get(
                "repository_test_patch_candidate_count",
                patch_candidates_payload.get("candidate_count", 0),
            )
        ),
        "repository_test_patch_recommended_pytest_args": [
            str(item)
            for item in _list(
                patch_candidates_payload.get("recommended_pytest_args")
            )
        ],
        "repository_test_patch_recommended_pytest_args_source": str(
            patch_candidates_payload.get("recommended_pytest_args_source")
            or summary.get("repository_test_patch_recommended_pytest_args_source")
            or ""
        ),
        "repository_patch_generation_mode": str(
            summary.get("repository_patch_generation_mode")
            or patch_candidates_payload.get("patch_generation_mode")
            or ""
        ),
        "repository_patch_generator_counts": _dict(
            summary.get("repository_patch_generator_counts")
        ) or _dict(patch_candidates_payload.get("generator_counts")),
        "repository_patch_candidate_variant_filter": _dict(
            summary.get("repository_patch_candidate_variant_filter")
        ) or _dict(patch_candidates_payload.get("candidate_variant_filter")),
        "repository_llm_patch_generation_status": str(
            summary.get("repository_llm_patch_generation_status")
            or patch_candidates_payload.get("llm_generation_status")
            or ""
        ),
        "repository_llm_patch_generation_reason": str(
            summary.get("repository_llm_patch_generation_reason")
            or patch_candidates_payload.get("llm_generation_reason")
            or ""
        ),
        "repository_llm_patch_generation_audit": llm_patch_audit,
        "repository_llm_patch_config_audit": _dict(
            llm_patch_audit.get("config_audit")
        ),
        "repository_llm_patch_provider": str(llm_patch_audit.get("provider") or ""),
        "repository_llm_patch_model": str(llm_patch_audit.get("model") or ""),
        "repository_llm_patch_base_url": str(llm_patch_audit.get("base_url") or ""),
        "repository_llm_patch_api_key_env": str(
            llm_patch_audit.get("api_key_env") or ""
        ),
        "repository_llm_patch_checked_api_key_envs": _list(
            llm_patch_audit.get("checked_api_key_envs")
        ),
        "repository_llm_patch_api_key_present": bool(
            llm_patch_audit.get("api_key_present", False)
        ),
        "repository_llm_patch_api_key_source": str(
            llm_patch_audit.get("api_key_source") or ""
        ),
        "repository_llm_patch_blocked": bool(
            llm_patch_audit.get("blocked", False)
        ),
        "repository_llm_patch_blocker": str(
            llm_patch_audit.get("blocker") or ""
        ),
        "repository_llm_patch_generation_fallback_used": bool(
            llm_patch_audit.get("fallback_used", False)
        ),
        "repository_llm_patch_generation_fallback_reason": str(
            llm_patch_audit.get("fallback_reason") or ""
        ),
        "repository_patch_safety_gate_status": str(
            summary.get("repository_patch_safety_gate_status") or ""
        ),
        "repository_patch_safety_gate_blocked_count": _int(
            summary.get("repository_patch_safety_gate_blocked_count", 0)
        ),
        "repository_test_patch_target_function_count": _int(
            summary.get("repository_test_patch_target_function_count", 0)
        ),
        "repository_test_patch_validation_json": str(
            report.output_paths.get("repository_test_patch_validation_json") or ""
        ),
        "repository_test_patch_validation_markdown": str(
            report.output_paths.get("repository_test_patch_validation_markdown") or ""
        ),
        "repository_test_patch_validation_status": str(
            summary.get("repository_test_patch_validation_status") or ""
        ),
        "repository_test_patch_validation_reason": str(
            summary.get("repository_test_patch_validation_reason") or ""
        ),
        "repository_test_patch_validation_input_candidate_count": _int(
            summary.get(
                "repository_test_patch_validation_input_candidate_count",
                0,
            )
        ),
        "repository_test_patch_validation_candidate_count": _int(
            summary.get("repository_test_patch_validation_candidate_count", 0)
        ),
        "repository_test_patch_validation_safety_blocked_candidate_count": _int(
            summary.get(
                "repository_test_patch_validation_safety_blocked_candidate_count",
                0,
            )
        ),
        "repository_test_patch_validation_executed_count": _int(
            summary.get("repository_test_patch_validation_executed_count", 0)
        ),
        "repository_test_patch_validation_success_count": _int(
            summary.get("repository_test_patch_validation_success_count", 0)
        ),
        "repository_test_repair_ready": bool(
            summary.get("repository_test_repair_ready", False)
        ),
        "repository_test_repair_validation_scope": str(
            summary.get("repository_test_repair_validation_scope") or ""
        ),
        "repository_test_patch_validation_reflection_mode": str(
            summary.get("repository_test_patch_validation_reflection_mode") or ""
        ),
        "repository_test_patch_validation_refiner_status": str(
            summary.get("repository_test_patch_validation_refiner_status") or ""
        ),
        "repository_test_patch_validation_refiner_reason": str(
            summary.get("repository_test_patch_validation_refiner_reason") or ""
        ),
        "repository_test_patch_validation_reflection_candidate_count": _int(
            summary.get("repository_test_patch_validation_reflection_candidate_count", 0)
        ),
        "repository_test_patch_validation_successful_reflection_count": _int(
            summary.get(
                "repository_test_patch_validation_successful_reflection_count",
                0,
            )
        ),
        "repository_test_patch_validation_regression_reflection_candidate_count": _int(
            summary.get(
                "repository_test_patch_validation_regression_reflection_candidate_count",
                0,
            )
        ),
        "repository_test_patch_validation_successful_regression_reflection_count": _int(
            summary.get(
                "repository_test_patch_validation_successful_regression_reflection_count",
                0,
            )
        ),
        "repository_test_patch_validation_max_depth": _int(
            summary.get("repository_test_patch_validation_max_depth", 0)
        ),
        "repository_test_patch_validation_failure_type_counts": _dict(
            summary.get("repository_test_patch_validation_failure_type_counts")
        ),
        "repository_test_patch_judge_mode": str(
            summary.get("repository_test_patch_judge_mode") or ""
        ),
        "repository_test_patch_judge_status": str(
            summary.get("repository_test_patch_judge_status") or ""
        ),
        "repository_test_patch_judge_reason": str(
            summary.get("repository_test_patch_judge_reason") or ""
        ),
        "repository_test_patch_judge_enabled": bool(
            summary.get("repository_test_patch_judge_enabled", False)
        ),
        "repository_test_patch_judge_candidate_count": _int(
            summary.get("repository_test_patch_judge_candidate_count", 0)
        ),
        "repository_test_patch_judge_verdict_counts": _dict(
            summary.get("repository_test_patch_judge_verdict_counts")
        ),
        "repository_test_patch_judge_agreement_counts": _dict(
            summary.get("repository_test_patch_judge_agreement_counts")
        ),
        "repository_test_patch_judge_authority": str(
            summary.get("repository_test_patch_judge_authority") or ""
        ),
        "repository_test_patch_judge_config_audit": _dict(
            summary.get("repository_test_patch_judge_config_audit")
        ),
        "repository_test_reflection_trace_json": str(
            report.output_paths.get("repository_test_reflection_trace_json") or ""
        ),
        "repository_test_reflection_trace_markdown": str(
            report.output_paths.get("repository_test_reflection_trace_markdown") or ""
        ),
        "reflection_trace_json": str(
            report.output_paths.get("reflection_trace_json") or ""
        ),
        "reflection_trace_markdown": str(
            report.output_paths.get("reflection_trace_markdown") or ""
        ),
    }
    payload["agent_controller"] = build_agent_controller_plan(payload)
    payload["agent_decision_timeline"] = _agent_decision_timeline_summary(payload)
    payload["phase4_search_evaluation"] = _phase4_search_evaluation_summary(payload)
    payload["artifact_inventory"] = _artifact_inventory_summary(payload)
    payload.update(_github_repo_intelligence_status_summary(payload))
    payload["agent_answers"] = _agent_answers_summary(payload)
    payload["acceptance_gate"] = _acceptance_gate_summary(payload)
    _refresh_agent_goal_readiness_and_controller(payload)
    payload["final_report"] = _final_agent_report_summary(payload)
    return payload


def _github_repo_intelligence_status_summary(
    payload: dict[str, Any],
) -> dict[str, Any]:
    upstream_status = str(
        payload.get("upstream_agent_status") or payload.get("status") or ""
    )
    patch_status = str(payload.get("repository_test_patch_validation_status") or "")
    patch_reason = str(payload.get("repository_test_patch_validation_reason") or "")
    repair_ready = bool(payload.get("repository_test_repair_ready", False))
    inventory = _dict(payload.get("artifact_inventory"))
    inventory_status = str(inventory.get("status") or "")
    readiness = _dict(payload.get("analysis_readiness"))
    current_stage = str(readiness.get("current_stage") or "")

    if repair_ready and patch_status == "pass" and inventory_status == "pass":
        return {
            "status": "pass",
            "passed": True,
            "status_reason": patch_reason or "patch_validation_success",
            "status_source": "repository_test_patch_validation",
            "upstream_agent_status": upstream_status,
            "upstream_agent_passed": upstream_status == "pass",
        }

    if inventory_status == "pass" and current_stage == "source_import_blocked":
        return {
            "status": "pass",
            "passed": True,
            "status_reason": "source_import_blocked_report_ready",
            "status_source": "analysis_readiness",
            "upstream_agent_status": upstream_status,
            "upstream_agent_passed": upstream_status == "pass",
        }

    if (
        inventory_status == "pass"
        and current_stage == "phase1_repo_understanding"
        and str(readiness.get("blocker") or "") == "no_static_candidates"
    ):
        return {
            "status": "pass",
            "passed": True,
            "status_reason": "no_static_candidates_report_ready",
            "status_source": "analysis_readiness",
            "upstream_agent_status": upstream_status,
            "upstream_agent_passed": upstream_status == "pass",
        }

    status = upstream_status or "fail"
    return {
        "status": status,
        "passed": status == "pass",
        "status_reason": "upstream_agent_status",
        "status_source": "github_repo_agent",
        "upstream_agent_status": upstream_status,
        "upstream_agent_passed": upstream_status == "pass",
    }


def refresh_github_repo_intelligence_summary_status(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Reapply current derived report/status semantics to a saved payload."""
    refreshed = dict(payload)
    if not _dict(refreshed.get("agent_answers")):
        refreshed["agent_answers"] = _agent_answers_summary(refreshed)
    refreshed["acceptance_gate"] = _acceptance_gate_summary(refreshed)
    controller = _dict(refreshed.get("agent_controller"))
    selected_action = _dict(controller.get("selected_action"))
    if not controller or not selected_action.get("id"):
        refreshed["agent_controller"] = build_agent_controller_plan(refreshed)
    refreshed["agent_decision_timeline"] = _agent_decision_timeline_summary(
        refreshed
    )
    refreshed["agent_goal_readiness"] = _agent_goal_readiness_summary(refreshed)
    refreshed.update(_github_repo_intelligence_status_summary(refreshed))
    refreshed["final_report"] = _final_agent_report_summary(refreshed)
    return refreshed


def render_github_repo_intelligence_summary(
    report: GitHubRepoAgentReport,
) -> str:
    return _render_github_repo_intelligence_payload(
        github_repo_intelligence_summary(report)
    )


def _repository_llm_patch_generation_audit(
    patch_candidates_payload: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any]:
    patch_candidates_payload = _dict(patch_candidates_payload)
    summary = _dict(summary)
    mode = str(
        summary.get("repository_patch_generation_mode")
        or patch_candidates_payload.get("patch_generation_mode")
        or "rule"
    ).lower()
    generator_counts = _dict(
        summary.get("repository_patch_generator_counts")
    ) or _dict(patch_candidates_payload.get("generator_counts"))
    status = str(
        summary.get("repository_llm_patch_generation_status")
        or patch_candidates_payload.get("llm_generation_status")
        or ("disabled" if mode == "rule" else "")
    )
    reason = str(
        summary.get("repository_llm_patch_generation_reason")
        or patch_candidates_payload.get("llm_generation_reason")
        or ("patch_generation_mode_rule" if mode == "rule" else "")
    )
    config_audit = _dict(
        summary.get("repository_llm_patch_config_audit")
    ) or _dict(patch_candidates_payload.get("llm_config_audit"))
    enabled = bool(config_audit.get("enabled", mode in {"llm", "hybrid"}))
    api_key_present = bool(config_audit.get("api_key_present", False))
    rule_count = _int(generator_counts.get("rule", 0))
    llm_count = _int(generator_counts.get("llm", 0))
    blocked = bool(
        status == "blocked"
        or reason in {"missing_llm_api_key", "fault_localization_not_ready"}
        or reason.startswith("missing_api_key:")
    )
    fallback_used = bool(
        mode == "hybrid"
        and blocked
        and rule_count > 0
        and llm_count <= 0
    )
    if fallback_used:
        fallback_reason = "hybrid_rule_fallback_after_llm_blocker"
    elif mode == "hybrid" and status == "skipped" and rule_count > 0:
        fallback_reason = reason or "hybrid_rule_candidates_satisfied_limit"
    else:
        fallback_reason = ""
    blocker = reason if blocked or status == "error" else ""
    return {
        "enabled": enabled,
        "mode": mode,
        "status": status,
        "reason": reason,
        "blocked": blocked,
        "blocker": blocker,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "rule_candidate_count": rule_count,
        "llm_candidate_count": llm_count,
        "configuration_complete": (not enabled) or api_key_present,
        "provider": str(config_audit.get("provider") or ""),
        "model": str(config_audit.get("model") or ""),
        "base_url": str(config_audit.get("base_url") or ""),
        "api_key_env": str(config_audit.get("api_key_env") or ""),
        "checked_api_key_envs": [
            str(item) for item in _list(config_audit.get("checked_api_key_envs"))
        ],
        "api_key_present": api_key_present,
        "api_key_source": str(config_audit.get("api_key_source") or ""),
        "api_key_fingerprint": str(config_audit.get("api_key_fingerprint") or ""),
        "warnings": [str(item) for item in _list(config_audit.get("warnings"))],
        "config_audit": config_audit,
    }


def _repository_llm_reflection_audit(
    reflection_summary: dict[str, Any],
) -> dict[str, Any]:
    reflection = _dict(reflection_summary)
    mode = str(reflection.get("reflection_mode") or "").lower()
    status = str(reflection.get("reflection_refiner_status") or "")
    reason = str(reflection.get("reflection_refiner_reason") or "")
    config_audit = _dict(reflection.get("llm_reflection_config_audit"))
    enabled = mode == "llm"
    api_key_present = bool(config_audit.get("api_key_present", False))
    blocked = bool(
        enabled
        and (
            status in {"unavailable", "unsupported", "error"}
            or reason.startswith("missing_api_key:")
        )
    )
    blocker = reason if blocked else ""
    return {
        "enabled": enabled,
        "mode": mode,
        "status": status,
        "reason": reason,
        "blocked": blocked,
        "blocker": blocker,
        "configuration_complete": (not enabled) or api_key_present,
        "provider": str(config_audit.get("provider") or ""),
        "model": str(config_audit.get("model") or ""),
        "base_url": str(config_audit.get("base_url") or ""),
        "api_key_env": str(config_audit.get("api_key_env") or ""),
        "checked_api_key_envs": [
            str(item) for item in _list(config_audit.get("checked_api_key_envs"))
        ],
        "api_key_present": api_key_present,
        "api_key_source": str(config_audit.get("api_key_source") or ""),
        "api_key_fingerprint": str(config_audit.get("api_key_fingerprint") or ""),
        "warnings": [str(item) for item in _list(config_audit.get("warnings"))],
        "reflection_candidate_count": _int(
            reflection.get("reflection_candidate_count", 0)
        ),
        "successful_reflection_candidate_count": _int(
            reflection.get("successful_reflection_candidate_count", 0)
        ),
        "config_audit": config_audit,
    }


def _reflection_trace_summary(
    report: GitHubRepoAgentReport,
    summary: dict[str, Any],
) -> dict[str, Any]:
    trace_path = str(
        report.output_paths.get("reflection_trace_json")
        or report.output_paths.get("repository_test_reflection_trace_json")
        or summary.get("reflection_trace_path")
        or summary.get("repository_test_reflection_trace_path")
        or ""
    )
    trace = _read_json(trace_path)
    patch_validation_payload = _read_json(
        str(report.output_paths.get("repository_test_patch_validation_json") or "")
    )
    final_outcome = _dict(trace.get("final_outcome"))
    initial_failures = _list(trace.get("initial_failures"))
    reflection_steps = _list(trace.get("reflection_steps"))
    initial_failure_type_counts = _dict(
        trace.get("initial_failure_type_counts")
    ) or _counts_by_field(initial_failures, "failure_type")
    reflection_parent_failure_type_counts = _dict(
        trace.get("reflection_parent_failure_type_counts")
    ) or _counts_by_field(
        reflection_steps,
        "parent_failure_type",
    )
    successful_reflection_parent_failure_type_counts = _dict(
        trace.get("successful_reflection_parent_failure_type_counts")
    ) or _counts_by_field(
        [
            item
            for item in reflection_steps
            if bool(_dict(item).get("success", False))
        ],
        "parent_failure_type",
    )
    reflection_failure_type_counts = _dict(
        trace.get("reflection_failure_type_counts")
    ) or _counts_by_field(
        reflection_steps,
        "failure_type",
    )
    initial_strategy_counts = _dict(
        trace.get("initial_strategy_counts")
    ) or _counts_by_field(
        initial_failures,
        "reflection_strategy_id",
    )
    recommended_strategies = [
        _dict(item)
        for item in _list(trace.get("recommended_reflection_strategies"))
    ]
    primary_strategy = recommended_strategies[0] if recommended_strategies else {}
    reflection_count = max(
        _int(trace.get("reflection_candidate_count", 0)),
        _int(
            summary.get(
                "repository_test_patch_validation_reflection_candidate_count",
                0,
            )
        ),
    )
    successful_reflection_count = max(
        _int(trace.get("successful_reflection_candidate_count", 0)),
        _int(
            summary.get(
                "repository_test_patch_validation_successful_reflection_count",
                0,
            )
        )
    )
    regression_reflection_count = max(
        _int(trace.get("regression_reflection_candidate_count", 0)),
        _int(
            summary.get(
                "repository_test_patch_validation_regression_reflection_candidate_count",
                0,
            )
        ),
    )
    successful_regression_reflection_count = max(
        _int(trace.get("successful_regression_reflection_candidate_count", 0)),
        _int(
            summary.get(
                "repository_test_patch_validation_successful_regression_reflection_count",
                0,
            )
        ),
    )
    return {
        "available": bool(trace) or bool(trace_path),
        "status": str(trace.get("status") or ""),
        "reason": str(trace.get("reason") or ""),
        "path": trace_path,
        "markdown": str(
            report.output_paths.get("reflection_trace_markdown")
            or report.output_paths.get("repository_test_reflection_trace_markdown")
            or summary.get("reflection_trace_markdown")
            or summary.get("repository_test_reflection_trace_markdown")
            or ""
        ),
        "patch_validation_status": str(
            summary.get("repository_test_patch_validation_status")
            or trace.get("patch_validation_status")
            or ""
        ),
        "patch_validation_reason": str(
            summary.get("repository_test_patch_validation_reason")
            or trace.get("patch_validation_reason")
            or ""
        ),
        "reflection_enabled": bool(
            trace.get(
                "reflection_enabled",
                summary.get("repository_test_patch_validation_reflection_candidate_count", 0),
            )
        ),
        "reflection_mode": str(
            trace.get("reflection_mode")
            or summary.get("repository_test_patch_validation_reflection_mode")
            or ""
        ),
        "reflection_refiner_status": str(
            trace.get("reflection_refiner_status")
            or summary.get("repository_test_patch_validation_refiner_status")
            or ""
        ),
        "reflection_refiner_reason": str(
            trace.get("reflection_refiner_reason")
            or summary.get("repository_test_patch_validation_refiner_reason")
            or ""
        ),
        "llm_reflection_config_audit": (
            _dict(trace.get("llm_reflection_config_audit"))
            or _dict(patch_validation_payload.get("llm_reflection_config_audit"))
            or _dict(summary.get("repository_test_patch_validation_llm_reflection_config_audit"))
        ),
        "initial_failure_count": max(
            len(initial_failures),
            _int(summary.get("repository_test_reflection_trace_initial_failure_count", 0)),
        ),
        "reflection_step_count": max(
            len(reflection_steps),
            _int(summary.get("repository_test_reflection_trace_step_count", 0)),
        ),
        "reflection_candidate_count": reflection_count,
        "successful_reflection_candidate_count": successful_reflection_count,
        "regression_reflection_candidate_count": regression_reflection_count,
        "successful_regression_reflection_candidate_count": (
            successful_regression_reflection_count
        ),
        "max_depth_executed": max(
            _int(trace.get("max_depth_executed", 0)),
            _int(summary.get("repository_test_patch_validation_max_depth", 0)),
        ),
        "repair_ready": bool(
            final_outcome.get(
                "repair_ready",
                summary.get("repository_test_repair_ready", False),
            )
        ),
        "regression_ready": bool(final_outcome.get("regression_ready", False)),
        "best_candidate_id": str(final_outcome.get("best_candidate_id") or ""),
        "best_candidate_rule_id": str(
            final_outcome.get("best_candidate_rule_id")
            or summary.get("repository_test_patch_validation_best_rule")
            or ""
        ),
        "best_candidate_variant": str(
            final_outcome.get("best_candidate_variant")
            or summary.get("repository_test_patch_validation_best_variant")
            or ""
        ),
        "best_depth": _int(final_outcome.get("best_patch_depth", 0)),
        "initial_failure_type_counts": initial_failure_type_counts,
        "initial_strategy_counts": initial_strategy_counts,
        "recommended_reflection_strategies": recommended_strategies,
        "primary_reflection_strategy_id": str(primary_strategy.get("id") or ""),
        "primary_reflection_strategy_action": str(
            primary_strategy.get("action") or ""
        ),
        "primary_reflection_strategy_reason": str(
            primary_strategy.get("reason") or ""
        ),
        "reflection_parent_failure_type_counts": (
            reflection_parent_failure_type_counts
        ),
        "successful_reflection_parent_failure_type_counts": (
            successful_reflection_parent_failure_type_counts
        ),
        "reflection_failure_type_counts": reflection_failure_type_counts,
        "failure_type_counts": _dict(trace.get("failure_type_counts"))
        or _dict(summary.get("repository_test_patch_validation_failure_type_counts")),
    }


def _repository_test_regression_guard_summary(
    report: GitHubRepoAgentReport,
) -> dict[str, Any]:
    path = str(report.output_paths.get("repository_test_regression_guard_json") or "")
    payload = _read_json(path)
    if payload:
        return payload
    status = str(report.summary.get("repository_test_regression_guard_status") or "")
    if not status:
        return {}
    return {
        "status": status,
        "reason": str(report.summary.get("repository_test_regression_guard_reason") or ""),
        "command": str(
            report.summary.get("repository_test_regression_guard_command") or ""
        ),
        "dynamic_evidence_level": str(
            report.summary.get(
                "repository_test_regression_guard_dynamic_evidence_level"
            )
            or ""
        ),
        "usable_for_localization": bool(
            report.summary.get(
                "repository_test_regression_guard_usable_for_localization",
                False,
            )
        ),
        "usable_for_patch_validation": bool(
            report.summary.get(
                "repository_test_regression_guard_usable_for_patch_validation",
                False,
            )
        ),
    }


def _repository_test_environment_repair_plan_summary(
    report: GitHubRepoAgentReport,
) -> dict[str, Any]:
    path = str(
        report.output_paths.get("repository_test_environment_repair_plan_json")
        or ""
    )
    payload = _read_json(path)
    if payload:
        return payload
    status = str(
        report.summary.get("repository_test_environment_repair_plan_status") or ""
    )
    if not status:
        return {}
    return {
        "status": status,
        "reason": str(
            report.summary.get("repository_test_environment_repair_plan_reason") or ""
        ),
        "blocker": str(
            report.summary.get("repository_test_environment_repair_plan_blocker") or ""
        ),
        "recommended_install_command": str(
            report.summary.get(
                "repository_test_environment_repair_plan_recommended_install_command"
            )
            or ""
        ),
        "missing_dependency_modules": [
            str(item)
            for item in _list(
                report.summary.get(
                    "repository_test_environment_repair_plan_missing_dependency_modules"
                )
            )
        ],
        "missing_dependency_install_hint": str(
            report.summary.get(
                "repository_test_environment_repair_plan_missing_dependency_install_hint"
            )
            or ""
        ),
    }


def _phase4_search_evaluation_summary(payload: dict[str, Any]) -> dict[str, Any]:
    readiness = _dict(payload.get("analysis_readiness"))
    controller = _dict(payload.get("agent_controller"))
    selected_action = _dict(controller.get("selected_action"))
    patch_validation = _read_json(
        str(payload.get("repository_test_patch_validation_json") or "")
    )
    regression = _dict(patch_validation.get("regression_validation"))
    best_patch = _dict(patch_validation.get("best_patch"))
    results = [_dict(item) for item in _list(patch_validation.get("results"))]

    patch_status = str(payload.get("repository_test_patch_validation_status") or "")
    patch_reason = str(payload.get("repository_test_patch_validation_reason") or "")
    repair_ready = bool(payload.get("repository_test_repair_ready", False))
    repair_scope = str(payload.get("repository_test_repair_validation_scope") or "")
    phase4_selected = str(selected_action.get("phase") or "") == "phase4"
    phase4_next = str(readiness.get("next_stage") or "").startswith("phase4")
    baseline_caveat = bool(
        repair_scope == "narrow_and_unchanged_regression_baseline"
        or regression.get("status") == "baseline_failed_unchanged"
        or regression.get("baseline_failed_unchanged")
    )
    full_suite_green_claim_allowed = bool(
        repair_ready
        and repair_scope == "narrow_and_regression"
        and str(regression.get("status") or "") == "pass"
    )
    ready = bool(repair_ready and patch_status == "pass" and (phase4_selected or phase4_next))
    status = "ready" if ready else "blocked" if patch_status else "not_ready"
    if ready and baseline_caveat:
        reason = "target_repair_ready_with_unchanged_regression_baseline"
    elif ready:
        reason = "patch_validation_ready_for_phase4"
    elif patch_status == "pass":
        reason = "patch_validation_not_repair_ready"
    elif patch_status:
        reason = patch_reason or patch_status
    else:
        reason = "patch_validation_not_executed"

    executed_count = _int(
        payload.get(
            "repository_test_patch_validation_executed_count",
            patch_validation.get("executed_count", 0),
        )
    )
    success_count = _int(
        payload.get(
            "repository_test_patch_validation_success_count",
            patch_validation.get("success_count", 0),
        )
    )
    candidate_count = max(
        _int(payload.get("repository_test_patch_candidate_count", 0)),
        _int(patch_validation.get("candidate_count", 0)),
    )
    first_success_rank = _first_success_rank(results)
    failure_type_counts = (
        _dict(payload.get("repository_test_patch_validation_failure_type_counts"))
        or _dict(patch_validation.get("failure_type_counts"))
    )
    search_budget = {
        "candidate_count": candidate_count,
        "executed_count": executed_count,
        "success_count": success_count,
        "success_rate": round(success_count / executed_count, 4)
        if executed_count > 0
        else 0.0,
        "first_success_rank": first_success_rank,
        "failures_before_first_success": (
            first_success_rank - 1 if first_success_rank is not None else executed_count
        ),
        "max_depth_executed": _int(
            payload.get(
                "repository_test_patch_validation_max_depth",
                patch_validation.get("max_depth_executed", 0),
            )
        ),
        "reflection_candidate_count": _int(
            payload.get("repository_test_patch_validation_reflection_candidate_count", 0)
        ),
        "successful_reflection_candidate_count": _int(
            payload.get("repository_test_patch_validation_successful_reflection_count", 0)
        ),
        "regression_reflection_candidate_count": _int(
            payload.get(
                "repository_test_patch_validation_regression_reflection_candidate_count",
                0,
            )
        ),
        "successful_regression_reflection_candidate_count": _int(
            payload.get(
                "repository_test_patch_validation_successful_regression_reflection_count",
                0,
            )
        ),
        "failure_type_counts": failure_type_counts,
    }
    gates = [
        _phase4_gate("patch_validation_executed", executed_count > 0, patch_status or "not_executed"),
        _phase4_gate("patch_validation_passed", patch_status == "pass", patch_reason or patch_status),
        _phase4_gate("repair_ready", repair_ready, repair_scope or "not_repair_ready"),
        _phase4_gate(
            "full_suite_green_claim_allowed",
            full_suite_green_claim_allowed,
            "full regression passed"
            if full_suite_green_claim_allowed
            else "target repair is not full-suite green",
        ),
    ]
    evidence_artifacts = {
        "patch_candidates_json": str(
            payload.get("repository_test_patch_candidates_json") or ""
        ),
        "patch_validation_json": str(
            payload.get("repository_test_patch_validation_json") or ""
        ),
        "reflection_trace_json": str(payload.get("reflection_trace_json") or ""),
        "controller_json": str(payload.get("agent_controller_json") or ""),
    }
    execution = {
        "executed": bool(payload.get("phase4_search_evaluation_executed", False)),
        "status": str(
            payload.get("phase4_search_evaluation_execution_status")
            or "not_executed"
        ),
        "reason": str(
            payload.get("phase4_search_evaluation_execution_reason") or ""
        ),
        "mode": str(payload.get("phase4_search_evaluation_execution_mode") or ""),
        "json": str(payload.get("phase4_search_evaluation_execution_json") or ""),
        "markdown": str(
            payload.get("phase4_search_evaluation_execution_markdown") or ""
        ),
        "strategy_rerun_status": str(
            payload.get("phase4_strategy_rerun_status") or ""
        ),
        "strategy_rerun_reason": str(
            payload.get("phase4_strategy_rerun_reason") or ""
        ),
        "strategy_rerun_strategy_count": _int(
            payload.get("phase4_strategy_rerun_strategy_count", 0)
        ),
        "strategy_rerun_total_evaluated_count": _int(
            payload.get("phase4_strategy_rerun_total_evaluated_count", 0)
        ),
    }
    return {
        "status": status,
        "reason": reason,
        "execution": execution,
        "ready_for_phase4": ready,
        "controller_action_id": str(selected_action.get("id") or ""),
        "controller_action_phase": str(selected_action.get("phase") or ""),
        "current_stage": str(readiness.get("current_stage") or ""),
        "next_stage": str(readiness.get("next_stage") or ""),
        "patch_validation_status": patch_status,
        "patch_validation_reason": patch_reason,
        "repair_ready": repair_ready,
        "repair_validation_scope": repair_scope,
        "baseline_regression_caveat": baseline_caveat,
        "full_suite_green_claim_allowed": full_suite_green_claim_allowed,
        "best_candidate_id": str(best_patch.get("candidate_id") or ""),
        "best_candidate_rule_id": str(best_patch.get("rule_id") or ""),
        "best_candidate_variant": str(best_patch.get("variant") or ""),
        "regression_validation_status": str(regression.get("status") or ""),
        "regression_validation_reason": str(regression.get("reason") or ""),
        "regression_baseline_status": str(regression.get("baseline_status") or ""),
        "regression_baseline_failed_unchanged": bool(
            regression.get("baseline_failed_unchanged", False)
        ),
        "search_budget": search_budget,
        "evaluation_gates": gates,
        "evidence_artifacts": evidence_artifacts,
        "recommended_commands": _phase4_recommended_commands(selected_action),
        "next_actions": _phase4_next_actions(
            ready=ready,
            baseline_caveat=baseline_caveat,
            repair_ready=repair_ready,
            patch_status=patch_status,
        ),
    }


def _phase4_gate(name: str, passed: bool, evidence: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "evidence": evidence,
    }


def _first_success_rank(results: list[dict[str, Any]]) -> int | None:
    for index, row in enumerate(results, start=1):
        if bool(row.get("success", False)):
            return index
    return None


def _phase4_recommended_commands(
    selected_action: dict[str, Any],
) -> list[str]:
    commands: list[str] = []
    command = str(selected_action.get("command") or "")
    generic_experiment_command = (
        "python -m code_intelligence_agent.evaluation.run_experiment_suite"
    )
    if command and command != generic_experiment_command:
        commands.append(command)
    commands.append(
        f"{generic_experiment_command} <benchmark_manifest> <output_dir> --format markdown --run-quality-gate"
    )
    commands.append(
        "python -m code_intelligence_agent.evaluation.run_ablation <benchmark_manifest> --format markdown"
    )
    return commands


def _phase4_next_actions(
    *,
    ready: bool,
    baseline_caveat: bool,
    repair_ready: bool,
    patch_status: str,
) -> list[str]:
    if ready and baseline_caveat:
        return [
            "Treat the target-failure repair as validated with a baseline-regression caveat.",
            "Fix or narrow the unchanged broad regression command before reporting full-suite green status.",
            "Run search/ablation evaluation to quantify patch search behavior and component contribution.",
        ]
    if ready:
        return [
            "Run search/ablation evaluation over benchmark cases.",
            "Record search budget, beam success, reflection depth, and ablation deltas.",
        ]
    if patch_status and not repair_ready:
        return [
            "Inspect repository_test_patch_validation and reflection_trace.",
            "Run additional reflection rounds or expand patch candidates before Phase 4.",
        ]
    return [
        "Reach patch validation before Phase 4 search and ablation evaluation.",
    ]


def _artifact_inventory_summary(
    payload: dict[str, Any],
    *,
    check_files: bool = False,
) -> dict[str, Any]:
    core_artifacts = [
        _artifact_item(
            "github_repo_intelligence.json",
            payload.get("intelligence_json"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "github_repo_intelligence.md",
            payload.get("intelligence_markdown"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "github_repo_agent_controller.json",
            payload.get("agent_controller_json"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "github_repo_agent_controller.md",
            payload.get("agent_controller_markdown"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "agent_action_registry.json",
            payload.get("agent_action_registry_json"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "agent_action_registry.md",
            payload.get("agent_action_registry_markdown"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "agent_policy_trace.json",
            payload.get("agent_policy_trace_json"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "agent_policy_trace.md",
            payload.get("agent_policy_trace_markdown"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "agent_invocation.json",
            payload.get("agent_invocation_json"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "agent_invocation.md",
            payload.get("agent_invocation_markdown"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "agent_goal_readiness.json",
            payload.get("agent_goal_readiness_json"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "agent_goal_readiness.md",
            payload.get("agent_goal_readiness_markdown"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "agent_decision_timeline.json",
            payload.get("agent_decision_timeline_json"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "agent_decision_timeline.md",
            payload.get("agent_decision_timeline_markdown"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "final_report.json",
            payload.get("final_report_json"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "final_report.md",
            payload.get("final_report_markdown"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "repository_structure.json",
            payload.get("repository_structure_json"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "repository_structure.md",
            payload.get("repository_structure_markdown"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "repository_test_discovery.json",
            payload.get("repository_test_discovery_json"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "repository_test_discovery.md",
            payload.get("repository_test_discovery_markdown"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "repo_graph.json",
            payload.get("repo_graph_json"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "repo_graph.md",
            payload.get("repo_graph_markdown"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "fault_localization.json",
            payload.get("fault_localization_json"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "fault_localization.md",
            payload.get("fault_localization_markdown"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "analysis_readiness.json",
            payload.get("analysis_readiness_json"),
            "always",
            check_files=check_files,
        ),
        _artifact_item(
            "analysis_readiness.md",
            payload.get("analysis_readiness_markdown"),
            "always",
            check_files=check_files,
        ),
    ]
    test_artifacts = [
        _artifact_item("repository_test_environment.json", payload.get("repository_test_environment_json"), "if_tests_planned", check_files=check_files),
        _artifact_item("repository_test_environment.md", payload.get("repository_test_environment_markdown"), "if_tests_planned", check_files=check_files),
        _artifact_item("repository_test_execution_plan.json", payload.get("repository_test_execution_plan_json"), "if_tests_planned", check_files=check_files),
        _artifact_item("repository_test_execution_plan.md", payload.get("repository_test_execution_plan_markdown"), "if_tests_planned", check_files=check_files),
        _artifact_item("repository_test_execution_result.json", payload.get("repository_test_execution_result_json"), "if_tests_executed_or_skipped", check_files=check_files),
        _artifact_item("repository_test_execution_result.md", payload.get("repository_test_execution_result_markdown"), "if_tests_executed_or_skipped", check_files=check_files),
        _artifact_item("repository_test_timeout_narrowing.json", payload.get("repository_test_timeout_narrowing_json"), "if_timeout_narrowing_attempted", check_files=check_files),
        _artifact_item("repository_test_timeout_narrowing.md", payload.get("repository_test_timeout_narrowing_markdown"), "if_timeout_narrowing_attempted", check_files=check_files),
        _artifact_item("repository_test_dynamic_evidence.json", payload.get("repository_test_dynamic_evidence_json"), "if_tests_executed_or_skipped", check_files=check_files),
        _artifact_item("repository_test_dynamic_evidence.md", payload.get("repository_test_dynamic_evidence_markdown"), "if_tests_executed_or_skipped", check_files=check_files),
        _artifact_item("repository_test_failure_overlay.json", payload.get("repository_test_failure_overlay_json"), "if_failure_overlay_attempted", check_files=check_files),
        _artifact_item("repository_test_failure_overlay.md", payload.get("repository_test_failure_overlay_markdown"), "if_failure_overlay_attempted", check_files=check_files),
        _artifact_item("repository_test_environment_repair_plan.json", payload.get("repository_test_environment_repair_plan_json"), "if_environment_blocked", check_files=check_files),
        _artifact_item("repository_test_environment_repair_plan.md", payload.get("repository_test_environment_repair_plan_markdown"), "if_environment_blocked", check_files=check_files),
        _artifact_item("repository_test_regression_guard.json", payload.get("repository_test_regression_guard_json"), "if_tests_pass_without_failure_evidence", check_files=check_files),
        _artifact_item("repository_test_regression_guard.md", payload.get("repository_test_regression_guard_markdown"), "if_tests_pass_without_failure_evidence", check_files=check_files),
    ]
    repair_artifacts = [
        _artifact_item("repository_test_patch_candidates.json", payload.get("repository_test_patch_candidates_json"), "if_repair_entered", check_files=check_files),
        _artifact_item("repository_test_patch_candidates.md", payload.get("repository_test_patch_candidates_markdown"), "if_repair_entered", check_files=check_files),
        _artifact_item("repository_test_patch_validation.json", payload.get("repository_test_patch_validation_json"), "if_repair_entered", check_files=check_files),
        _artifact_item("repository_test_patch_validation.md", payload.get("repository_test_patch_validation_markdown"), "if_repair_entered", check_files=check_files),
        _artifact_item("reflection_trace.json", payload.get("reflection_trace_json"), "if_patch_validation_entered", check_files=check_files),
        _artifact_item("reflection_trace.md", payload.get("reflection_trace_markdown"), "if_patch_validation_entered", check_files=check_files),
    ]
    phase4_artifacts = [
        _artifact_item("phase4_search_evaluation.json", payload.get("phase4_search_evaluation_json"), "if_phase4_ready", check_files=check_files),
        _artifact_item("phase4_search_evaluation.md", payload.get("phase4_search_evaluation_markdown"), "if_phase4_ready", check_files=check_files),
        _artifact_item("phase4_search_evaluation_execution.json", payload.get("phase4_search_evaluation_execution_json"), "if_phase4_evaluation_executed", check_files=check_files),
        _artifact_item("phase4_search_evaluation_execution.md", payload.get("phase4_search_evaluation_execution_markdown"), "if_phase4_evaluation_executed", check_files=check_files),
    ]
    groups = {
        "core": core_artifacts,
        "test": test_artifacts,
        "repair": repair_artifacts,
        "phase4": phase4_artifacts,
    }
    all_items = [item for rows in groups.values() for item in rows]
    for item in all_items:
        item["required_now"] = _artifact_required_now(
            str(item.get("required_when") or ""),
            payload,
        )
    available_count = sum(1 for item in all_items if item["available"])
    missing_core = [item["name"] for item in core_artifacts if not item["available"]]
    required_items = [item for item in all_items if bool(item.get("required_now"))]
    missing_required = [
        str(item["name"]) for item in required_items if not bool(item.get("available"))
    ]
    required_available_count = sum(
        1 for item in required_items if bool(item.get("available"))
    )
    required_status = "pass" if not missing_required else "warning"
    status = "pass" if not missing_core and not missing_required else "warning"
    return {
        "status": status,
        "reason": (
            "core_artifacts_written"
            if check_files and not missing_core and not missing_required
            else "core_artifacts_missing"
            if check_files and missing_core
            else "required_artifacts_missing"
            if check_files and missing_required
            else "core_artifact_paths_recorded"
        ),
        "file_check_enabled": check_files,
        "available_count": available_count,
        "artifact_count": len(all_items),
        "required_status": required_status,
        "required_available_count": required_available_count,
        "required_count": len(required_items),
        "missing_required_artifacts": missing_required,
        "missing_core_artifacts": missing_core,
        "groups": groups,
    }


def _artifact_required_now(required_when: str, payload: dict[str, Any]) -> bool:
    if required_when == "always":
        return True
    if required_when == "if_tests_planned":
        return _tests_planned(payload)
    if required_when == "if_tests_executed_or_skipped":
        return _tests_executed_or_skipped(payload)
    if required_when == "if_timeout_narrowing_attempted":
        return _timeout_narrowing_attempted(payload)
    if required_when == "if_environment_blocked":
        return _environment_repair_required(payload)
    if required_when == "if_tests_pass_without_failure_evidence":
        return _regression_guard_required(payload)
    if required_when == "if_failure_overlay_attempted":
        return _failure_overlay_attempted_for_inventory(payload)
    if required_when == "if_repair_entered":
        return _repair_entered(payload)
    if required_when == "if_patch_validation_entered":
        return _patch_validation_entered(payload)
    if required_when == "if_phase4_ready":
        return _phase4_ready(payload)
    if required_when == "if_phase4_evaluation_executed":
        return _phase4_evaluation_executed(payload)
    return False


def _tests_planned(payload: dict[str, Any]) -> bool:
    readiness = _dict(payload.get("analysis_readiness"))
    return bool(
        str(readiness.get("planned_repository_test_command") or "")
        or str(payload.get("planned_repository_test_command") or "")
        or str(payload.get("recommended_test_command") or "")
        or str(payload.get("repository_test_environment_status") or "")
        or str(payload.get("repository_test_execution_plan_status") or "")
    )


def _tests_executed_or_skipped(payload: dict[str, Any]) -> bool:
    readiness = _dict(payload.get("analysis_readiness"))
    return bool(
        str(readiness.get("planned_repository_test_result_status") or "")
        or str(payload.get("planned_repository_test_result_status") or "")
        or str(payload.get("repository_test_dynamic_evidence_level") or "")
        or str(payload.get("repository_test_execution_result_status") or "")
    )


def _timeout_narrowing_attempted(payload: dict[str, Any]) -> bool:
    return bool(
        str(payload.get("repository_test_timeout_narrowing_status") or "")
        or str(payload.get("repository_test_timeout_narrowing_reason") or "")
        or str(payload.get("repository_test_timeout_narrowing_json") or "")
        or str(payload.get("repository_test_timeout_narrowing_markdown") or "")
        or bool(payload.get("repository_test_timeout_narrowing_executed", False))
        or _int(payload.get("repository_test_timeout_narrowing_attempt_count", 0))
        > 0
    )


def _environment_repair_required(payload: dict[str, Any]) -> bool:
    readiness = _dict(payload.get("analysis_readiness"))
    dynamic_level = str(
        payload.get("repository_test_dynamic_evidence_level")
        or readiness.get("dynamic_evidence_level")
        or ""
    )
    if dynamic_level in {"passing_tests", "failing_tests"}:
        return False
    blocker = " ".join(
        [
            str(readiness.get("blocker") or ""),
            str(readiness.get("repository_test_setup_doctor_blocker") or ""),
            str(payload.get("repository_test_environment_repair_plan_status") or ""),
            str(payload.get("repository_test_environment_repair_plan_blocker") or ""),
        ]
    )
    return bool(
        "environment" in blocker
        or "test_tool_missing" in blocker
        or "missing_dependency" in blocker
    )


def _regression_guard_required(payload: dict[str, Any]) -> bool:
    return bool(
        str(payload.get("repository_test_regression_guard_status") or "") == "pass"
        or str(payload.get("repository_test_dynamic_evidence_level") or "")
        == "passing_tests"
    )


def _failure_overlay_attempted_for_inventory(payload: dict[str, Any]) -> bool:
    return bool(
        str(payload.get("repository_test_failure_overlay_status") or "")
        or str(payload.get("repository_test_failure_overlay_reason") or "")
        or str(payload.get("repository_test_failure_overlay_json") or "")
        or str(payload.get("repository_test_failure_overlay_markdown") or "")
        or _int(payload.get("repository_test_failure_overlay_attempted_cases", 0)) > 0
        or _int(payload.get("repository_test_failure_overlay_supported_candidates", 0)) > 0
    )


def _repair_entered(payload: dict[str, Any]) -> bool:
    readiness = _dict(payload.get("analysis_readiness"))
    return bool(
        _patch_validation_entered(payload)
        or _status_entered(payload.get("repository_test_patch_candidates_status"))
        or str(readiness.get("current_stage") or "") == "phase3_patch_validation"
        or str(readiness.get("next_stage") or "").startswith("phase3_patch")
    )


def _patch_validation_entered(payload: dict[str, Any]) -> bool:
    return bool(
        _status_entered(payload.get("repository_test_patch_validation_status"))
        or _int(payload.get("repository_test_patch_validation_executed_count", 0)) > 0
        or _int(payload.get("repository_test_patch_validation_success_count", 0)) > 0
    )


def _phase4_ready(payload: dict[str, Any]) -> bool:
    readiness = _dict(payload.get("analysis_readiness"))
    phase4 = _dict(payload.get("phase4_search_evaluation"))
    return bool(
        phase4.get("ready_for_phase4", False)
        or str(readiness.get("next_stage") or "").startswith("phase4")
        or str(readiness.get("current_stage") or "").startswith("phase4")
    )


def _phase4_evaluation_executed(payload: dict[str, Any]) -> bool:
    phase4 = _dict(payload.get("phase4_search_evaluation"))
    execution = _dict(phase4.get("execution"))
    return bool(
        payload.get("phase4_search_evaluation_executed", False)
        or execution.get("executed", False)
    )


def _status_entered(value: Any) -> bool:
    status = str(value or "")
    return status not in {"", "skipped", "not_executed"}


def _artifact_item(
    name: str,
    path_value: Any,
    required_when: str,
    *,
    check_files: bool = False,
) -> dict[str, Any]:
    path_text = str(path_value or "")
    path_recorded = bool(path_text)
    file_exists = False
    file_size_bytes = 0
    if check_files and path_recorded:
        path = Path(path_text)
        file_exists = path.is_file()
        if file_exists:
            file_size_bytes = path.stat().st_size
    return {
        "name": name,
        "path": path_text,
        "path_recorded": path_recorded,
        "file_checked": check_files,
        "file_exists": file_exists if check_files else None,
        "file_size_bytes": file_size_bytes if check_files else None,
        "file_nonempty": (file_size_bytes > 0) if check_files else None,
        "available": path_recorded and (not check_files or file_exists),
        "required_when": required_when,
    }


def _agent_answers_summary(payload: dict[str, Any]) -> dict[str, Any]:
    structure = _dict(payload.get("repository_structure"))
    fault = _dict(payload.get("fault_localization"))
    readiness = _dict(payload.get("analysis_readiness"))
    reflection = _dict(payload.get("reflection_summary"))
    artifact_inventory = _dict(payload.get("artifact_inventory"))
    controller = _dict(payload.get("agent_controller"))
    selected_action = _dict(controller.get("selected_action"))
    top_suspicious = _agent_answer_suspicious_functions(fault)
    application_coverage = _agent_answer_application_candidate_coverage(
        structure,
        fault,
    )
    structure_answer = _agent_answer_repository_structure(structure)
    testability = _agent_answer_testability(readiness, payload)
    repairability = _agent_answer_repairability(
        readiness,
        reflection,
        top_suspicious,
        payload,
    )
    artifact_audit = _agent_answer_artifact_audit(artifact_inventory)
    blocker = str(
        readiness.get("blocker")
        or controller.get("primary_blocker")
        or "none"
    )
    next_action = str(
        selected_action.get("reason")
        or readiness.get("next_action")
        or payload.get("next_action")
        or "Inspect generated intelligence artifacts."
    )
    top_function = (
        str(top_suspicious[0].get("function") or "none")
        if top_suspicious
        else "none"
    )
    suspicious_answer = (
        f"Top-{len(top_suspicious)} suspicious functions are ranked by "
        f"{fault.get('mode') or 'unknown'} localization; top function is "
        f"{top_function}."
        if top_suspicious
        else "No suspicious function ranking is available yet."
    )
    why_answer = (
        str(top_suspicious[0].get("why") or "")
        if top_suspicious
        else str(fault.get("reason") or "fault localization did not produce rankings")
    )
    executive_summary = (
        f"{structure_answer} Top suspicious function: {top_function}. "
        f"Testability: {testability['status']}. "
        f"Repairability: {repairability['status']}. "
        f"Artifacts: {artifact_audit['status']}. "
        f"Blocker: {blocker}. Next action: {next_action}"
    )
    answers = {
        "executive_summary": executive_summary,
        "repository_structure_answer": structure_answer,
        "most_suspicious_functions_answer": suspicious_answer,
        "why_suspicious_answer": why_answer,
        "testability_answer": str(testability.get("answer") or ""),
        "repairability_answer": str(repairability.get("answer") or ""),
        "artifact_inventory_answer": str(artifact_audit.get("answer") or ""),
        "blocker_answer": blocker,
        "next_action_answer": next_action,
        "selected_controller_action": str(selected_action.get("id") or ""),
        "selected_controller_command": str(selected_action.get("command") or ""),
        "repository_structure": {
            "analyzed_files": _int(structure.get("analyzed_file_count", 0)),
            "functions": _int(structure.get("function_count", 0)),
            "classes": _int(structure.get("class_count", 0)),
            "loc": _int(structure.get("total_loc", 0)),
            "max_cyclomatic_complexity": _int(
                structure.get("max_cyclomatic_complexity", 0)
            ),
            "top_directories": _dict(structure.get("directory_file_counts")),
            "package_structure": _dict(structure.get("package_structure")),
            "test_structure": _dict(structure.get("test_structure")),
            "project_config": _dict(structure.get("project_config")),
            "answer": structure_answer,
        },
        "top_suspicious_functions": top_suspicious,
        "application_candidate_coverage": application_coverage,
        "application_candidate_coverage_answer": str(
            application_coverage.get("answer") or ""
        ),
        "testability": testability,
        "repairability": repairability,
        "artifact_inventory": artifact_audit,
        "blocker": blocker,
        "next_action": next_action,
    }
    coverage = _agent_answer_coverage(answers)
    answers["answer_coverage"] = coverage
    answers["answer_coverage_complete"] = bool(coverage.get("complete", False))
    answers["answer_coverage_answered_count"] = _int(
        coverage.get("answered_question_count", 0)
    )
    answers["answer_coverage_required_count"] = _int(
        coverage.get("required_question_count", 0)
    )
    answers["answer_coverage_missing_questions"] = _list(
        coverage.get("missing_questions")
    )
    return answers


def _final_agent_report_summary(payload: dict[str, Any]) -> dict[str, Any]:
    answers = _dict(payload.get("agent_answers"))
    readiness = _dict(payload.get("analysis_readiness"))
    controller = _dict(payload.get("agent_controller"))
    selected_action = _dict(controller.get("selected_action"))
    acceptance = _dict(payload.get("acceptance_gate"))
    goal = _dict(payload.get("agent_goal_readiness"))
    coverage = _dict(answers.get("answer_coverage"))
    artifact_inventory = _dict(payload.get("artifact_inventory"))
    top_suspicious = [
        _dict(item) for item in _list(answers.get("top_suspicious_functions"))
    ]
    patch_status = str(payload.get("repository_test_patch_validation_status") or "")
    patch_success_count = _int(
        payload.get("repository_test_patch_validation_success_count", 0)
    )
    repair_ready = bool(payload.get("repository_test_repair_ready", False))
    verified_repair = bool(
        repair_ready and patch_status == "pass" and patch_success_count > 0
    )
    evidence_artifacts = {
        "intelligence_json": str(payload.get("intelligence_json") or ""),
        "intelligence_markdown": str(payload.get("intelligence_markdown") or ""),
        "controller_json": str(payload.get("agent_controller_json") or ""),
        "controller_markdown": str(payload.get("agent_controller_markdown") or ""),
        "repository_structure_json": str(payload.get("repository_structure_json") or ""),
        "repo_graph_json": str(payload.get("repo_graph_json") or ""),
        "fault_localization_json": str(payload.get("fault_localization_json") or ""),
        "analysis_readiness_json": str(payload.get("analysis_readiness_json") or ""),
        "dynamic_evidence_json": str(
            payload.get("repository_test_dynamic_evidence_json") or ""
        ),
        "patch_candidates_json": str(
            payload.get("repository_test_patch_candidates_json") or ""
        ),
        "patch_validation_json": str(
            payload.get("repository_test_patch_validation_json") or ""
        ),
        "reflection_trace_json": str(payload.get("reflection_trace_json") or ""),
    }
    report_questions = []
    for item_value in _list(coverage.get("questions")):
        item = _dict(item_value)
        report_questions.append(
            {
                "id": str(item.get("id") or ""),
                "question": str(item.get("question") or ""),
                "answered": bool(item.get("answered", False)),
                "answer_field": str(item.get("answer_field") or ""),
                "answer_preview": str(item.get("answer_preview") or ""),
            }
        )
    objective_compliance = _objective_compliance_summary(payload)
    return {
        "status": str(payload.get("status") or ""),
        "status_reason": str(payload.get("status_reason") or ""),
        "repo": str(payload.get("repo") or ""),
        "repo_spec": str(payload.get("repo_spec") or ""),
        "repository_ref": str(payload.get("repository_ref") or ""),
        "executive_summary": str(answers.get("executive_summary") or ""),
        "repository_structure": _dict(answers.get("repository_structure")),
        "top_suspicious_functions": top_suspicious,
        "top_suspicious_function": (
            str(top_suspicious[0].get("function") or "")
            if top_suspicious
            else ""
        ),
        "why_suspicious": str(answers.get("why_suspicious_answer") or ""),
        "application_candidate_coverage": _dict(
            answers.get("application_candidate_coverage")
        ),
        "testability": _dict(answers.get("testability")),
        "repairability": _dict(answers.get("repairability")),
        "blocker": str(
            answers.get("blocker") or readiness.get("blocker") or "none"
        ),
        "next_action": str(
            answers.get("next_action")
            or readiness.get("next_action")
            or selected_action.get("reason")
            or ""
        ),
        "controller": {
            "status": str(controller.get("status") or ""),
            "selected_action": str(selected_action.get("id") or ""),
            "selected_action_phase": str(selected_action.get("phase") or ""),
            "selected_action_executable_now": bool(
                selected_action.get("executable_now", False)
            ),
            "selected_action_reason": str(selected_action.get("reason") or ""),
            "termination_status": str(
                _dict(controller.get("termination")).get("status") or ""
            ),
            "loop": [str(item) for item in _list(controller.get("control_loop"))],
        },
        "readiness": {
            "current_stage": str(readiness.get("current_stage") or ""),
            "next_stage": str(readiness.get("next_stage") or ""),
            "completed_phases": [
                str(item) for item in _list(readiness.get("completed_phases"))
            ],
            "dynamic_evidence_level": str(
                readiness.get("dynamic_evidence_level") or ""
            ),
            "can_attempt_dynamic_tests": bool(
                readiness.get("can_attempt_dynamic_tests", False)
            ),
            "can_attempt_patch_repair": bool(
                readiness.get("can_attempt_patch_repair", False)
            ),
        },
        "verification": {
            "acceptance_gate_status": str(acceptance.get("status") or ""),
            "acceptance_gate_passed": bool(acceptance.get("passed", False)),
            "acceptance_gate_passed_checks": _int(
                acceptance.get("passed_check_count", 0)
            ),
            "acceptance_gate_total_checks": _int(acceptance.get("check_count", 0)),
            "agent_goal_readiness_status": str(goal.get("status") or ""),
            "agent_goal_readiness_passed": bool(goal.get("passed", False)),
            "agent_goal_readiness_passed_criteria": _int(
                goal.get("passed_criteria_count", 0)
            ),
            "agent_goal_readiness_total_criteria": _int(
                goal.get("criteria_count", 0)
            ),
            "answer_coverage_complete": bool(coverage.get("complete", False)),
            "artifact_inventory_status": str(artifact_inventory.get("status") or ""),
            "required_artifacts_available": _int(
                artifact_inventory.get("required_available_count", 0)
            ),
            "required_artifacts_total": _int(
                artifact_inventory.get("required_count", 0)
            ),
            "verified_repair_claim": verified_repair,
            "repair_success_claim": "verified" if verified_repair else "not_claimed",
        },
        "report_questions": report_questions,
        "objective_compliance": objective_compliance,
        "evidence_artifacts": evidence_artifacts,
    }


def _objective_compliance_summary(payload: dict[str, Any]) -> dict[str, Any]:
    goal = _dict(payload.get("agent_goal_readiness"))
    inventory = _dict(payload.get("artifact_inventory"))
    criteria_by_name = {
        str(item.get("name") or ""): _dict(item)
        for item in _list(goal.get("criteria"))
    }
    groups = [
        (
            "github_input_checkout_and_cache",
            [
                "one_command_input",
                "github_ref_provenance",
                "source_cache_and_filter_controls",
                "shallow_checkout_policy",
            ],
        ),
        (
            "repo_understanding_and_graph_modeling",
            [
                "repo_understanding_or_actionable_blocker",
                "graph_modeling_or_actionable_blocker",
            ],
        ),
        (
            "static_signals_and_topk_localization",
            [
                "static_signal_fallback",
                "topk_fault_localization_or_actionable_blocker",
                "application_candidate_coverage_audited",
                "fault_score_decomposition",
            ],
        ),
        (
            "test_diagnosis_and_dynamic_evidence",
            [
                "test_environment_diagnosis",
                "dynamic_evidence_policy",
                "failure_overlay_route_audited",
            ],
        ),
        (
            "patch_validation_and_reflection",
            [
                "repair_decision_audit",
                "repair_claim_requires_validation",
                "reflection_loop_when_patch_fails",
            ],
        ),
        (
            "agent_controller_and_auditable_reports",
            [
                "controller_observe_plan_act_verify_reflect_replan",
                "core_artifacts_auditable",
                "final_answers_complete",
                "acceptance_gate_passed",
            ],
        ),
    ]
    section_rows = []
    for section_id, names in groups:
        rows = [criteria_by_name.get(name, {"name": name, "passed": False, "evidence": "missing"}) for name in names]
        failed = [row for row in rows if not bool(row.get("passed", False))]
        section_rows.append(
            {
                "id": section_id,
                "passed": not failed,
                "passed_criteria_count": len(rows) - len(failed),
                "criteria_count": len(rows),
                "failed_criteria": [str(row.get("name") or "") for row in failed],
                "criteria": rows,
            }
        )
    missing_required = [
        str(item) for item in _list(inventory.get("missing_required_artifacts"))
    ]
    missing_core = [
        str(item) for item in _list(inventory.get("missing_core_artifacts"))
    ]
    failed_sections = [
        str(row.get("id") or "")
        for row in section_rows
        if not bool(row.get("passed", False))
    ]
    return {
        "status": "pass" if not failed_sections else "warning",
        "passed": not failed_sections,
        "section_count": len(section_rows),
        "passed_section_count": len(section_rows) - len(failed_sections),
        "failed_section_count": len(failed_sections),
        "failed_sections": failed_sections,
        "goal_readiness_status": str(goal.get("status") or ""),
        "goal_readiness_passed": bool(goal.get("passed", False)),
        "goal_readiness_failed_criteria": [
            str(item) for item in _list(goal.get("failed_criteria"))
        ],
        "required_artifact_status": str(inventory.get("required_status") or ""),
        "required_artifacts_available": _int(
            inventory.get("required_available_count", 0)
        ),
        "required_artifacts_total": _int(inventory.get("required_count", 0)),
        "missing_required_artifacts": missing_required,
        "missing_core_artifacts": missing_core,
        "sections": section_rows,
    }


def _agent_answer_repository_structure(structure: dict[str, Any]) -> str:
    top_dirs = _format_counts(_dict(structure.get("directory_file_counts")))
    package_structure = _dict(structure.get("package_structure"))
    test_structure = _dict(structure.get("test_structure"))
    project_config = _dict(structure.get("project_config"))
    package_roots = _format_list(_list(package_structure.get("package_roots")))
    src_packages = _format_list(_list(package_structure.get("src_layout_packages")))
    test_dirs = _format_list(_list(test_structure.get("test_directories")))
    config_files = _format_list(_list(project_config.get("project_config_files")))
    return (
        f"Analyzed {_int(structure.get('analyzed_file_count', 0))} Python files, "
        f"{_int(structure.get('function_count', 0))} functions, "
        f"{_int(structure.get('class_count', 0))} classes, "
        f"{_int(structure.get('total_loc', 0))} LOC; "
        f"max cyclomatic complexity is "
        f"{_int(structure.get('max_cyclomatic_complexity', 0))}; "
        f"top directories: {top_dirs}; "
        f"package roots: {package_roots}; "
        f"src layout packages: {src_packages}; "
        f"test directories: {test_dirs}; "
        f"project config files: {config_files}."
    )


def _agent_answer_suspicious_functions(
    fault: dict[str, Any],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    mode = str(fault.get("mode") or "unknown")
    rows: list[dict[str, Any]] = []
    for row_value in _list(fault.get("rankings"))[:limit]:
        row = _dict(row_value)
        rows.append(
            {
                "rank": _int(row.get("rank", len(rows) + 1)),
                "function": str(row.get("function_name") or ""),
                "function_id": str(row.get("function_id") or ""),
                "file": str(row.get("file_path") or ""),
                "start_line": _int(row.get("start_line", 0)),
                "end_line": _int(row.get("end_line", 0)),
                "mode": mode,
                "static_rule_score": _float(row.get("static_rule_score", 0.0)),
                "graph_score": _float(row.get("graph_score", 0.0)),
                "source_role": str(row.get("source_role") or ""),
                "source_role_score": _float(row.get("source_role_score", 0.0)),
                "sbfl_score": _float(row.get("sbfl_score", 0.0)),
                "dynamic_evidence_score": _float(
                    row.get("dynamic_test_evidence_score", 0.0)
                ),
                "final_score": _float(row.get("final_score", 0.0)),
                "rules": _list(row.get("rule_ids")),
                "bug_types": _list(row.get("bug_types")),
                "why": _agent_answer_suspicious_why(row, mode),
            }
        )
    return rows


def _agent_answer_application_candidate_coverage(
    structure: dict[str, Any],
    fault: dict[str, Any],
) -> dict[str, Any]:
    rankings = [_dict(item) for item in _list(fault.get("rankings"))]
    role_counts = _dict(fault.get("source_role_counts"))
    if not role_counts:
        role_counts = _dict(_fault_localization_role_audit(rankings).get("source_role_counts"))
    package_structure = _dict(structure.get("package_structure"))
    application_count = _int(fault.get("application_candidate_count", 0))
    if not application_count:
        application_count = _int(role_counts.get("application", 0))
    top_source_role = str(
        fault.get("top_source_role")
        or (_dict(rankings[0]).get("source_role") if rankings else "")
        or "none"
    )
    recommended_target_prefix = str(
        package_structure.get("recommended_target_prefix") or ""
    )
    source_hints: list[str] = []
    for item in [
        *_list(package_structure.get("src_layout_packages")),
        *_list(package_structure.get("package_roots")),
        recommended_target_prefix,
    ]:
        text = str(item)
        if text and text not in source_hints:
            source_hints.append(text)
    if not rankings:
        status = "no_fault_ranking"
        answer = "No Top-k fault-localization ranking is available yet."
    elif application_count > 0:
        status = "application_candidates_ranked"
        top_application = str(fault.get("top_application_function") or "")
        answer = (
            "Top-k includes application-source candidates "
            f"({application_count}/{len(rankings)} ranked); "
            f"top application function is {top_application or 'not recorded'}."
        )
    elif source_hints:
        status = "no_application_candidates_ranked"
        answer = (
            "Top-k currently contains no application-source candidates even though "
            f"application/package hints exist ({_format_list(source_hints)}); "
            f"top source role is {top_source_role}. Collect failing-test evidence "
            "or adjust source focus before treating automation/test findings as "
            "application bugs."
        )
    else:
        status = "non_application_candidates_only"
        answer = (
            "Top-k currently contains no application-source candidates; "
            f"top source role is {top_source_role}."
        )
    return {
        "status": status,
        "application_candidate_count": application_count,
        "ranked_function_count": len(rankings),
        "top_source_role": top_source_role,
        "source_role_counts": role_counts,
        "recommended_target_prefix": recommended_target_prefix,
        "application_source_hints": source_hints,
        "answer": answer,
    }


def _agent_answer_suspicious_why(row: dict[str, Any], mode: str) -> str:
    parts: list[str] = []
    rules = [str(item) for item in _list(row.get("rule_ids")) if str(item)]
    bug_types = [str(item) for item in _list(row.get("bug_types")) if str(item)]
    if rules:
        parts.append(f"rule hits={', '.join(rules)}")
    if bug_types:
        parts.append(f"bug types={', '.join(bug_types)}")
    static_score = _float(row.get("static_rule_score", 0.0))
    graph_score = _float(row.get("graph_score", 0.0))
    source_role = str(row.get("source_role") or "")
    source_role_score = _float(row.get("source_role_score", 0.0))
    sbfl_score = _float(row.get("sbfl_score", 0.0))
    dynamic_score = _float(row.get("dynamic_test_evidence_score", 0.0))
    if static_score > 0:
        parts.append(f"StaticRuleScore={static_score:.4f}")
    if graph_score > 0:
        parts.append(f"GraphScore={graph_score:.4f}")
    if source_role:
        parts.append(f"SourceRole={source_role}")
    if source_role_score > 0:
        parts.append(f"SourceRoleScore={source_role_score:.4f}")
    if sbfl_score > 0:
        parts.append(f"SBFLScore={sbfl_score:.4f}")
    if dynamic_score > 0:
        parts.append(f"DynamicEvidenceScore={dynamic_score:.4f}")
    reason = str(row.get("reason") or "")
    if reason:
        parts.append(f"reason={reason}")
    if not parts:
        parts.append(f"{mode} localization ranking evidence")
    return "; ".join(parts)


def _agent_answer_testability(
    readiness: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    command = str(readiness.get("planned_repository_test_command") or "")
    result_status = str(readiness.get("planned_repository_test_result_status") or "")
    test_count = _int(readiness.get("planned_repository_test_result_test_count", 0))
    passed_count = _int(readiness.get("planned_repository_test_result_passed", 0))
    failed_count = _int(readiness.get("planned_repository_test_result_failed", 0))
    error_count = _int(readiness.get("planned_repository_test_result_errors", 0))
    skipped_count = _int(readiness.get("planned_repository_test_result_skipped", 0))
    test_count_source = str(
        readiness.get("planned_repository_test_result_test_count_source") or ""
    )
    dynamic_level = str(readiness.get("dynamic_evidence_level") or "none")
    overlay_status = str(payload.get("repository_test_failure_overlay_status") or "")
    overlay_level = str(
        payload.get("repository_test_failure_overlay_dynamic_evidence_level") or ""
    )
    overlay_rule = str(
        payload.get("repository_test_failure_overlay_selected_rule") or ""
    )
    overlay_function = str(
        payload.get("repository_test_failure_overlay_selected_function") or ""
    )
    overlay_command = str(
        payload.get("repository_test_failure_overlay_validation_command") or ""
    )
    overlay_reason = str(
        payload.get("repository_test_failure_overlay_reason") or ""
    )
    overlay_supported = _int(
        payload.get("repository_test_failure_overlay_supported_candidates", 0)
    )
    overlay_attempted = _int(
        payload.get("repository_test_failure_overlay_attempted_cases", 0)
    )
    overlay_limit = _int(
        payload.get("repository_test_failure_overlay_candidate_limit", 0)
    )
    overlay_next_extension = _dict(
        payload.get("repository_test_failure_overlay_next_actionable_extension")
    )
    overlay_next_action = str(
        overlay_next_extension.get("recommendation")
        or overlay_next_extension.get("action")
        or overlay_next_extension.get("reason")
        or ""
    )
    setup_blocker = str(
        readiness.get("repository_test_setup_doctor_blocker") or ""
    )
    runner_fallback = _agent_answer_runner_fallback(readiness, payload)
    executable_now = bool(
        readiness.get("planned_repository_test_executable_now", False)
    )
    can_attempt = bool(readiness.get("can_attempt_dynamic_tests", False))
    has_dynamic_evidence = dynamic_level not in {
        "",
        "none",
        "not_executed",
        "skipped",
    }
    if overlay_status == "pass" and overlay_level == "failing_tests":
        status = "overlay_failing_tests_available"
        target = overlay_function or "a selected function"
        rule_text = f" for rule {overlay_rule}" if overlay_rule else ""
        command_text = (
            f" Validation command: `{overlay_command}`."
            if overlay_command
            else ""
        )
        answer = (
            "The original repository test command did not produce localizable "
            f"failing-test evidence at level {dynamic_level}, but the Agent's "
            f"controlled failure overlay produced usable failing_tests for {target}"
            f"{rule_text}; this evidence can drive fault localization and patch "
            f"validation.{command_text}"
        )
    elif overlay_status and overlay_status != "pass":
        status = "overlay_not_usable"
        candidate_text = (
            f"supported={overlay_supported}, attempted={overlay_attempted}"
            + (f", limit={overlay_limit}" if overlay_limit else "")
        )
        next_text = (
            f" Next overlay action: {overlay_next_action}"
            if overlay_next_action
            else ""
        )
        answer = (
            "Repository tests did not produce localizable failing-test evidence "
            f"at level {dynamic_level}. The Agent also attempted controlled "
            f"failure overlay, but overlay status is {overlay_status}"
            f" ({overlay_reason or 'no_reason'}; {candidate_text})."
            f"{next_text}"
        )
    elif result_status == "fail" and dynamic_level == "failing_tests":
        status = "tests_failed"
        answer = (
            "Repository tests executed and failed, so failing-test evidence is "
            f"available at level {dynamic_level}."
        )
    elif result_status == "fail":
        status = "tests_failed_without_localizable_evidence"
        answer = (
            "Repository test execution failed but did not produce usable "
            f"failing-test evidence for localization; evidence level is {dynamic_level}."
        )
    elif result_status == "pass":
        status = "tests_passed"
        answer = "Repository tests executed and passed; no failing-test evidence was found."
    elif has_dynamic_evidence:
        status = "dynamic_evidence_available"
        answer = f"Dynamic evidence is available at level {dynamic_level}."
    elif executable_now:
        status = "can_execute_now"
        answer = f"Tests can be executed now with `{command}`."
    elif can_attempt:
        status = "can_attempt_with_checkout_or_setup"
        answer = (
            "A repository test command or runner signal exists, but checkout or "
            "environment preparation may still be required."
        )
    elif setup_blocker:
        status = "blocked"
        answer = f"Testing is blocked by {setup_blocker}."
    else:
        status = "not_available"
        answer = "No executable repository test path is available yet."
    if runner_fallback:
        answer = f"{answer} {runner_fallback}"
    test_count_answer = _agent_answer_test_count(
        test_count=test_count,
        passed_count=passed_count,
        failed_count=failed_count,
        error_count=error_count,
        skipped_count=skipped_count,
        source=test_count_source,
    )
    if test_count_answer:
        answer = f"{answer} {test_count_answer}"
    return {
        "status": status,
        "can_test": status not in {"blocked", "not_available"},
        "command": command,
        "result_status": result_status,
        "test_count": test_count,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "error_count": error_count,
        "skipped_count": skipped_count,
        "test_count_source": test_count_source,
        "dynamic_evidence_level": dynamic_level,
        "failure_overlay_status": overlay_status,
        "failure_overlay_reason": overlay_reason,
        "failure_overlay_dynamic_evidence_level": overlay_level,
        "failure_overlay_selected_rule": overlay_rule,
        "failure_overlay_selected_function": overlay_function,
        "failure_overlay_validation_command": overlay_command,
        "failure_overlay_supported_candidates": overlay_supported,
        "failure_overlay_attempted_cases": overlay_attempted,
        "failure_overlay_candidate_limit": overlay_limit,
        "failure_overlay_next_action": overlay_next_action,
        "setup_blocker": setup_blocker,
        "runner_fallback_used": bool(runner_fallback),
        "runner_fallback_reason": str(
            readiness.get("planned_repository_test_runner_fallback_reason")
            or payload.get("planned_repository_test_runner_fallback_reason")
            or ""
        ),
        "runner_fallback_from": str(
            readiness.get("planned_repository_test_runner_fallback_from")
            or payload.get("planned_repository_test_runner_fallback_from")
            or ""
        ),
        "runner_fallback_to": str(
            readiness.get("planned_repository_test_runner_fallback_to")
            or payload.get("planned_repository_test_runner_fallback_to")
            or ""
        ),
        "answer": answer,
    }


def _agent_answer_test_count(
    *,
    test_count: int,
    passed_count: int,
    failed_count: int,
    error_count: int,
    skipped_count: int,
    source: str,
) -> str:
    if not any([test_count, passed_count, failed_count, error_count, skipped_count]):
        return ""
    source_text = f", source={source}" if source else ""
    return (
        "Test counts: "
        f"total={test_count}, passed={passed_count}, failed={failed_count}, "
        f"errors={error_count}, skipped={skipped_count}{source_text}."
    )


def _agent_answer_runner_fallback(
    readiness: dict[str, Any],
    payload: dict[str, Any],
) -> str:
    used = bool(
        readiness.get("planned_repository_test_runner_fallback_used", False)
        or payload.get("planned_repository_test_runner_fallback_used", False)
    )
    if not used:
        return ""
    fallback_from = str(
        readiness.get("planned_repository_test_runner_fallback_from")
        or payload.get("planned_repository_test_runner_fallback_from")
        or readiness.get("planned_repository_test_preferred_runner")
        or payload.get("planned_repository_test_preferred_runner")
        or "the preferred runner"
    )
    fallback_to = str(
        readiness.get("planned_repository_test_runner_fallback_to")
        or payload.get("planned_repository_test_runner_fallback_to")
        or readiness.get("planned_repository_test_runner")
        or payload.get("planned_repository_test_runner")
        or "the selected runner"
    )
    reason = str(
        readiness.get("planned_repository_test_runner_fallback_reason")
        or payload.get("planned_repository_test_runner_fallback_reason")
        or "runner_fallback"
    )
    return (
        "The Agent selected a runner fallback "
        f"`{fallback_from}` -> `{fallback_to}` because `{reason}`."
    )


def _agent_answer_repairability(
    readiness: dict[str, Any],
    reflection: dict[str, Any],
    suspicious_functions: list[dict[str, Any]],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _dict(payload)
    patch_status = str(readiness.get("patch_validation_status") or "")
    patch_reason = str(readiness.get("patch_validation_reason") or "")
    repair_scope = str(readiness.get("repair_validation_scope") or "")
    repair_ready = bool(
        readiness.get("repair_ready", False)
        or reflection.get("repair_ready", False)
    )
    can_attempt_patch = bool(readiness.get("can_attempt_patch_repair", False))
    input_candidate_count = _int(
        readiness.get("patch_validation_input_candidate_count", 0)
    )
    candidate_count = _int(readiness.get("patch_validation_candidate_count", 0))
    safety_blocked_count = _int(
        readiness.get("patch_validation_safety_blocked_candidate_count", 0)
    )
    reflection_count = _int(reflection.get("reflection_candidate_count", 0))
    successful_reflection_count = _int(
        reflection.get("successful_reflection_candidate_count", 0)
    )
    initial_failure_type_counts = _dict(
        reflection.get("initial_failure_type_counts")
    )
    reflection_failure_type_counts = _dict(
        reflection.get("reflection_failure_type_counts")
    )
    reflection_parent_failure_type_counts = _dict(
        reflection.get("reflection_parent_failure_type_counts")
    )
    successful_reflection_parent_failure_type_counts = _dict(
        reflection.get("successful_reflection_parent_failure_type_counts")
    )
    initial_strategy_counts = _dict(reflection.get("initial_strategy_counts"))
    recommended_strategies = [
        _dict(item)
        for item in _list(reflection.get("recommended_reflection_strategies"))
    ]
    primary_strategy_id = str(
        reflection.get("primary_reflection_strategy_id") or ""
    )
    primary_strategy_action = str(
        reflection.get("primary_reflection_strategy_action") or ""
    )
    primary_strategy_reason = str(
        reflection.get("primary_reflection_strategy_reason") or ""
    )
    if not primary_strategy_id and recommended_strategies:
        primary_strategy = recommended_strategies[0]
        primary_strategy_id = str(primary_strategy.get("id") or "")
        primary_strategy_action = str(primary_strategy.get("action") or "")
        primary_strategy_reason = str(primary_strategy.get("reason") or "")
    llm_patch_audit = _dict(payload.get("repository_llm_patch_generation_audit"))
    llm_note = _llm_patch_generation_answer_note(payload)
    reflection_note = _llm_reflection_answer_note(payload)
    if repair_ready:
        status = "repair_ready"
        answer = (
            "At least one patch candidate passed validation; the repair has "
            "sandbox evidence."
        )
    elif patch_status == "pass":
        status = "patch_validated_but_not_repair_ready"
        answer = (
            "Patch validation produced candidate evidence, but the repair is "
            "not fully verified"
            f" ({repair_scope or patch_reason or 'repair_not_verified'})."
        )
    elif (
        patch_reason == "all_candidates_blocked_by_safety_gate"
        or (safety_blocked_count > 0 and candidate_count <= 0)
    ):
        status = "patch_candidates_blocked_by_safety_gate"
        answer = (
            "Patch validation stopped before sandbox execution because every "
            "available candidate was blocked by the pre-sandbox safety gate "
            f"({safety_blocked_count}/{input_candidate_count or safety_blocked_count} "
            "candidate(s)); regenerate smaller AST-valid, scope-limited "
            "patch candidates before claiming a repair."
        )
    elif patch_status == "fail" and reflection_count > 0:
        status = "reflection_attempted_but_not_repaired"
        failure_notes: list[str] = []
        if initial_failure_type_counts:
            failure_notes.append(
                f"initial failures={_format_counts(initial_failure_type_counts)}"
            )
        if reflection_failure_type_counts:
            failure_notes.append(
                "reflection failures="
                f"{_format_counts(reflection_failure_type_counts)}"
            )
        if reflection_parent_failure_type_counts:
            failure_notes.append(
                "reflection parent failures="
                f"{_format_counts(reflection_parent_failure_type_counts)}"
            )
        if primary_strategy_id:
            strategy_note = f"primary reflection strategy `{primary_strategy_id}`"
            if primary_strategy_action:
                strategy_note = f"{strategy_note}: {primary_strategy_action}"
            if primary_strategy_reason:
                strategy_note = f"{strategy_note} ({primary_strategy_reason})"
            failure_notes.append(strategy_note)
        detail = (
            "; ".join(failure_notes)
            if failure_notes
            else "inspect reflection_trace for failed candidates and failure types"
        )
        answer = (
            "Patch validation failed after "
            f"{reflection_count} reflection candidate(s); {detail}."
        )
    elif patch_status == "fail":
        status = "patch_validation_failed"
        answer = f"Patch validation failed: {patch_reason or 'unknown reason'}."
        if initial_failure_type_counts:
            answer = (
                f"{answer} Failure types: "
                f"{_format_counts(initial_failure_type_counts)}."
            )
        if primary_strategy_id:
            strategy_note = f"Reflection should start with strategy `{primary_strategy_id}`"
            if primary_strategy_action:
                strategy_note = f"{strategy_note}: {primary_strategy_action}"
            if primary_strategy_reason:
                strategy_note = f"{strategy_note} ({primary_strategy_reason})"
            answer = f"{answer} {strategy_note}."
    elif can_attempt_patch:
        status = "patch_generation_ready"
        answer = "Dynamic localization is ready, so the agent can generate and validate patches."
    elif suspicious_functions:
        status = "needs_dynamic_evidence_or_patch_context"
        answer = (
            "Suspicious functions are ranked, but repair is not ready until "
            "dynamic evidence or repository-test patch context is available."
        )
    else:
        status = "not_ready"
        answer = "No repairable suspicious function has been established yet."
    if llm_note:
        answer = f"{answer} {llm_note}"
    if reflection_note:
        answer = f"{answer} {reflection_note}"
    return {
        "status": status,
        "can_repair": repair_ready or can_attempt_patch,
        "patch_validation_status": patch_status,
        "patch_validation_reason": patch_reason,
        "patch_validation_input_candidate_count": input_candidate_count,
        "patch_validation_candidate_count": candidate_count,
        "patch_validation_safety_blocked_candidate_count": safety_blocked_count,
        "repair_ready": repair_ready,
        "repair_validation_scope": repair_scope,
        "reflection_candidate_count": reflection_count,
        "successful_reflection_candidate_count": successful_reflection_count,
        "initial_failure_type_counts": initial_failure_type_counts,
        "reflection_failure_type_counts": reflection_failure_type_counts,
        "reflection_parent_failure_type_counts": (
            reflection_parent_failure_type_counts
        ),
        "successful_reflection_parent_failure_type_counts": (
            successful_reflection_parent_failure_type_counts
        ),
        "initial_strategy_counts": initial_strategy_counts,
        "recommended_reflection_strategy_count": len(recommended_strategies),
        "primary_reflection_strategy_id": primary_strategy_id,
        "primary_reflection_strategy_action": primary_strategy_action,
        "primary_reflection_strategy_reason": primary_strategy_reason,
        "patch_generation_mode": str(
            payload.get("repository_patch_generation_mode") or ""
        ),
        "patch_generator_counts": _dict(
            payload.get("repository_patch_generator_counts")
        ),
        "llm_patch_generation_status": str(
            payload.get("repository_llm_patch_generation_status") or ""
        ),
        "llm_patch_generation_reason": str(
            payload.get("repository_llm_patch_generation_reason") or ""
        ),
        "llm_patch_provider": str(llm_patch_audit.get("provider") or ""),
        "llm_patch_model": str(llm_patch_audit.get("model") or ""),
        "llm_patch_api_key_present": bool(
            llm_patch_audit.get("api_key_present", False)
        ),
        "llm_patch_blocked": bool(llm_patch_audit.get("blocked", False)),
        "llm_patch_blocker": str(llm_patch_audit.get("blocker") or ""),
        "llm_patch_fallback_used": bool(
            llm_patch_audit.get("fallback_used", False)
        ),
        "llm_patch_fallback_reason": str(
            llm_patch_audit.get("fallback_reason") or ""
        ),
        "llm_reflection_status": str(
            payload.get("repository_llm_reflection_status") or ""
        ),
        "llm_reflection_reason": str(
            payload.get("repository_llm_reflection_reason") or ""
        ),
        "llm_reflection_provider": str(
            payload.get("repository_llm_reflection_provider") or ""
        ),
        "llm_reflection_model": str(
            payload.get("repository_llm_reflection_model") or ""
        ),
        "llm_reflection_api_key_present": bool(
            payload.get("repository_llm_reflection_api_key_present", False)
        ),
        "llm_reflection_blocked": bool(
            payload.get("repository_llm_reflection_blocked", False)
        ),
        "llm_reflection_blocker": str(
            payload.get("repository_llm_reflection_blocker") or ""
        ),
        "answer": answer,
    }


def _llm_patch_generation_answer_note(payload: dict[str, Any]) -> str:
    mode = str(payload.get("repository_patch_generation_mode") or "").lower()
    if mode not in {"llm", "hybrid"}:
        return ""
    audit = _dict(payload.get("repository_llm_patch_generation_audit"))
    status = str(
        payload.get("repository_llm_patch_generation_status")
        or audit.get("status")
        or ""
    )
    reason = str(
        payload.get("repository_llm_patch_generation_reason")
        or audit.get("reason")
        or ""
    )
    provider = str(audit.get("provider") or "unknown-provider")
    model = str(audit.get("model") or "unknown-model")
    api_key_env = str(audit.get("api_key_env") or "CIA_LLM_API_KEY")
    if status == "blocked" and reason == "missing_llm_api_key":
        if bool(audit.get("fallback_used", False)):
            return (
                f"LLM patch generation ({provider}/{model}) was blocked because "
                f"`{api_key_env}` was not configured, so hybrid mode continued "
                "with rule-based candidates instead of claiming LLM repair."
            )
        return (
            f"LLM patch generation ({provider}/{model}) is blocked because "
            f"`{api_key_env}` is not configured; configure the key or switch to "
            "rule/hybrid mode before expecting LLM repair candidates."
        )
    if status == "error":
        return (
            f"LLM patch generation ({provider}/{model}) errored with "
            f"`{reason or 'unknown_error'}`; the report keeps this as an "
            "auditable repair blocker."
        )
    if status == "pass":
        return (
            f"LLM patch generation ({provider}/{model}) produced auditable "
            "candidate evidence."
        )
    if status == "skipped" and mode == "hybrid":
        return (
            "Hybrid patch generation skipped the LLM branch because "
            f"`{reason or 'candidate_limit_or_policy'}`; rule candidates remain "
            "the audited repair path."
        )
    return ""


def _llm_reflection_answer_note(payload: dict[str, Any]) -> str:
    audit = _dict(payload.get("repository_llm_reflection_audit"))
    mode = str(audit.get("mode") or "").lower()
    if mode != "llm":
        return ""
    status = str(
        payload.get("repository_llm_reflection_status")
        or audit.get("status")
        or ""
    )
    reason = str(
        payload.get("repository_llm_reflection_reason")
        or audit.get("reason")
        or ""
    )
    provider = str(audit.get("provider") or "unknown-provider")
    model = str(audit.get("model") or "unknown-model")
    api_key_env = str(audit.get("api_key_env") or "CIA_LLM_API_KEY")
    if bool(audit.get("blocked", False)) and reason.startswith("missing_api_key:"):
        return (
            f"LLM reflection ({provider}/{model}) is blocked because "
            f"`{api_key_env}` is not configured; no LLM-refined patch should "
            "be claimed until that key is configured or rule reflection is used."
        )
    if status == "ready":
        return (
            f"LLM reflection ({provider}/{model}) is configured; refined "
            "patches still require AST/scope/minimal-diff gates and sandbox "
            "validation before any repair claim."
        )
    if status in {"unavailable", "unsupported", "error"}:
        return (
            f"LLM reflection ({provider}/{model}) is not available "
            f"(`{reason or status}`); the report keeps this as an auditable "
            "reflection blocker."
        )
    return ""


def _agent_answer_artifact_audit(inventory: dict[str, Any]) -> dict[str, Any]:
    groups = _dict(inventory.get("groups"))
    core_rows = [_dict(item) for item in _list(groups.get("core"))]
    test_rows = [_dict(item) for item in _list(groups.get("test"))]
    repair_rows = [_dict(item) for item in _list(groups.get("repair"))]
    phase4_rows = [_dict(item) for item in _list(groups.get("phase4"))]
    missing_core = [str(item) for item in _list(inventory.get("missing_core_artifacts"))]
    missing_required = [
        str(item) for item in _list(inventory.get("missing_required_artifacts"))
    ]
    core_available = sum(1 for item in core_rows if bool(item.get("available", False)))
    test_available = sum(1 for item in test_rows if bool(item.get("available", False)))
    repair_available = sum(1 for item in repair_rows if bool(item.get("available", False)))
    phase4_available = sum(1 for item in phase4_rows if bool(item.get("available", False)))
    required_available = _int(inventory.get("required_available_count", 0))
    required_count = _int(inventory.get("required_count", 0))
    status = str(inventory.get("status") or "unknown")
    reason = str(inventory.get("reason") or "")
    if status == "pass":
        if reason == "core_artifacts_written":
            answer = (
                "All core intelligence artifacts were written and verified on disk; "
                f"{required_available}/{required_count} currently required "
                "artifacts are available; "
                f"{test_available}/{len(test_rows)} test artifacts and "
                f"{repair_available}/{len(repair_rows)} repair artifacts are present "
                "when their conditions were reached; "
                f"{phase4_available}/{len(phase4_rows)} phase4 artifacts are available."
            )
        else:
            answer = (
                "All core intelligence artifacts have recorded paths; "
                f"{required_available}/{required_count} currently required "
                "artifacts are available; "
                f"{test_available}/{len(test_rows)} test artifacts and "
                f"{repair_available}/{len(repair_rows)} repair artifacts are present "
                "when their conditions were reached; "
                f"{phase4_available}/{len(phase4_rows)} phase4 artifacts are available."
            )
    elif missing_core:
        answer = (
            "Core artifact coverage is incomplete; missing core artifacts: "
            f"{', '.join(missing_core)}."
        )
    elif missing_required:
        answer = (
            "Current-stage artifact coverage is incomplete; missing required "
            f"artifacts: {', '.join(missing_required)}."
        )
    else:
        answer = "Artifact inventory is not available in this report."
    return {
        "status": status,
        "core_ready": not missing_core,
        "required_ready": not missing_required,
        "available_count": _int(inventory.get("available_count", 0)),
        "artifact_count": _int(inventory.get("artifact_count", 0)),
        "required_available_count": required_available,
        "required_artifact_count": required_count,
        "core_available_count": core_available,
        "core_artifact_count": len(core_rows),
        "test_available_count": test_available,
        "test_artifact_count": len(test_rows),
        "repair_available_count": repair_available,
        "repair_artifact_count": len(repair_rows),
        "phase4_available_count": phase4_available,
        "phase4_artifact_count": len(phase4_rows),
        "missing_required_artifacts": missing_required,
        "missing_core_artifacts": missing_core,
        "answer": answer,
    }


def _agent_answer_coverage(answers: dict[str, Any]) -> dict[str, Any]:
    required_questions = [
        (
            "repository_structure",
            "What is the repository structure?",
            "repository_structure_answer",
        ),
        (
            "suspicious_functions",
            "Which functions are most suspicious?",
            "most_suspicious_functions_answer",
        ),
        (
            "suspicious_reason",
            "Why are those functions suspicious?",
            "why_suspicious_answer",
        ),
        ("testability", "Can the repository be tested?", "testability_answer"),
        ("repairability", "Can the repository be repaired?", "repairability_answer"),
        ("blocker", "What is the current blocker?", "blocker_answer"),
        ("next_action", "What should the agent do next?", "next_action_answer"),
    ]
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for question_id, question, field in required_questions:
        value = str(answers.get(field) or "").strip()
        answered = bool(value)
        if not answered:
            missing.append(question_id)
        rows.append(
            {
                "id": question_id,
                "question": question,
                "answer_field": field,
                "answered": answered,
                "answer_preview": value[:240],
            }
        )
    answered_count = sum(1 for row in rows if bool(row.get("answered", False)))
    return {
        "complete": answered_count == len(required_questions),
        "answered_question_count": answered_count,
        "required_question_count": len(required_questions),
        "missing_questions": missing,
        "questions": rows,
    }


def _acceptance_gate_summary(payload: dict[str, Any]) -> dict[str, Any]:
    inventory = _dict(payload.get("artifact_inventory"))
    answers = _dict(payload.get("agent_answers"))
    coverage = _dict(answers.get("answer_coverage"))
    controller = _dict(payload.get("agent_controller"))
    readiness = _dict(payload.get("analysis_readiness"))
    structure = _dict(payload.get("repository_structure"))
    fault = _dict(payload.get("fault_localization"))
    timeline = _dict(payload.get("agent_decision_timeline"))
    loop_audit = _dict(controller.get("loop_iteration_audit"))
    loop_audit_count = _int(loop_audit.get("iteration_count", 0))
    loop_audit_complete_count = _int(
        loop_audit.get("complete_iteration_count", 0)
    )
    loop_audit_complete = bool(
        str(loop_audit.get("status") or "") == "pass"
        and loop_audit_count > 0
        and loop_audit_complete_count == loop_audit_count
    )
    blocker = str(readiness.get("blocker") or answers.get("blocker") or "")
    next_action = str(
        readiness.get("next_action")
        or answers.get("next_action_answer")
        or payload.get("next_action")
        or ""
    )
    patch_status = str(payload.get("repository_test_patch_validation_status") or "")
    patch_reason = str(payload.get("repository_test_patch_validation_reason") or "")
    repair_ready = bool(payload.get("repository_test_repair_ready", False))
    patch_success_count = _int(
        payload.get("repository_test_patch_validation_success_count", 0)
    )
    safety_blocked_count = _int(
        payload.get(
            "repository_test_patch_validation_safety_blocked_candidate_count",
            0,
        )
    )
    validated_candidate_count = _int(
        payload.get("repository_test_patch_validation_candidate_count", 0)
    )
    repair_decision_audit = _repair_decision_audit_summary(payload)
    has_artifact_inventory = _int(inventory.get("artifact_count", 0)) > 0

    checks = [
        _acceptance_check(
            "core_artifacts",
            has_artifact_inventory
            and not _list(inventory.get("missing_core_artifacts")),
            (
                f"{_int(inventory.get('available_count', 0))}/"
                f"{_int(inventory.get('artifact_count', 0))} artifacts available"
            ),
        ),
        _acceptance_check(
            "current_stage_required_artifacts",
            has_artifact_inventory
            and not _list(inventory.get("missing_required_artifacts")),
            (
                f"{_int(inventory.get('required_available_count', 0))}/"
                f"{_int(inventory.get('required_count', 0))} required artifacts available"
            ),
        ),
        _acceptance_check(
            "controller_loop",
            _list(controller.get("control_loop"))
            == ["observe", "plan", "act", "verify", "reflect", "replan"]
            and bool(_dict(controller.get("selected_action")).get("id"))
            and loop_audit_complete,
            (
                f"{str(_dict(controller.get('selected_action')).get('id') or 'none')}; "
                f"loop_audit={loop_audit_complete_count}/{loop_audit_count}"
            ),
        ),
        _acceptance_check(
            "decision_timeline",
            str(timeline.get("status") or "") == "pass",
            (
                f"{_int(timeline.get('complete_step_count', 0))}/"
                f"{_int(timeline.get('step_count', 0))} complete"
            ),
        ),
        _acceptance_check(
            "structure_or_blocker",
            _int(structure.get("analyzed_file_count", 0)) > 0
            or bool(blocker and next_action),
            (
                f"files={_int(structure.get('analyzed_file_count', 0))}, "
                f"blocker={blocker or 'none'}"
            ),
        ),
        _acceptance_check(
            "fault_localization_or_blocker",
            bool(_list(fault.get("rankings"))) or bool(blocker and next_action),
            (
                f"rankings={len(_list(fault.get('rankings')))}, "
                f"blocker={blocker or 'none'}"
            ),
        ),
        _acceptance_check(
            "agent_answers",
            bool(coverage.get("complete", False)),
            (
                f"{_int(coverage.get('answered_question_count', 0))}/"
                f"{_int(coverage.get('required_question_count', 0))} answers"
            ),
        ),
        _acceptance_check(
            "conditional_test_artifacts",
            _conditional_artifact_group_ready(inventory, "test"),
            _conditional_artifact_group_evidence(inventory, "test"),
        ),
        _acceptance_check(
            "conditional_repair_artifacts",
            _conditional_artifact_group_ready(inventory, "repair"),
            _conditional_artifact_group_evidence(inventory, "repair"),
        ),
        _acceptance_check(
            "repair_decision_audit",
            bool(repair_decision_audit.get("passed", False)),
            str(repair_decision_audit.get("evidence") or ""),
        ),
        _acceptance_check(
            "no_unverified_repair_claim",
            (
                not repair_ready
                or (
                    patch_status == "pass"
                    and patch_success_count > 0
                )
            ),
            (
                f"repair_ready={str(repair_ready).lower()}, "
                f"patch_status={patch_status or 'none'}, "
                f"success_count={patch_success_count}"
            ),
        ),
        _acceptance_check(
            "safety_blocker_not_misreported",
            not (
                patch_reason == "all_candidates_blocked_by_safety_gate"
                and (
                    repair_ready
                    or patch_success_count > 0
                    or validated_candidate_count > 0
                )
            ),
            (
                f"safety_blocked={safety_blocked_count}, "
                f"validated={validated_candidate_count}, "
                f"repair_ready={str(repair_ready).lower()}"
            ),
        ),
    ]
    failed = [row for row in checks if not bool(row.get("passed", False))]
    return {
        "status": "pass" if not failed else "warning",
        "reason": "acceptance_gate_passed" if not failed else "acceptance_gate_failed",
        "passed": not failed,
        "check_count": len(checks),
        "passed_check_count": len(checks) - len(failed),
        "failed_check_count": len(failed),
        "failed_checks": [str(row.get("name") or "") for row in failed],
        "checks": checks,
    }


def _agent_goal_readiness_summary(payload: dict[str, Any]) -> dict[str, Any]:
    readiness = _dict(payload.get("analysis_readiness"))
    controller = _dict(payload.get("agent_controller"))
    inventory = _dict(payload.get("artifact_inventory"))
    answers = _dict(payload.get("agent_answers"))
    acceptance = _dict(payload.get("acceptance_gate"))
    invocation = _dict(payload.get("agent_invocation"))
    structure = _dict(payload.get("repository_structure"))
    repo_graph = _dict(payload.get("repo_graph"))
    program_graph = _dict(repo_graph.get("program_graph"))
    fault = _dict(payload.get("fault_localization"))
    reflection = _dict(payload.get("reflection_summary"))
    blocker = str(readiness.get("blocker") or answers.get("blocker") or "")
    next_action = str(
        readiness.get("next_action")
        or answers.get("next_action_answer")
        or payload.get("next_action")
        or ""
    )
    has_blocker_path = bool(blocker and next_action)
    loop_audit = _dict(controller.get("loop_iteration_audit"))
    loop_audit_count = _int(loop_audit.get("iteration_count", 0))
    loop_audit_complete_count = _int(
        loop_audit.get("complete_iteration_count", 0)
    )
    loop_audit_complete = bool(
        str(loop_audit.get("status") or "") == "pass"
        and loop_audit_count > 0
        and loop_audit_complete_count == loop_audit_count
    )
    repo_input = _dict(payload.get("repo_input"))
    repo_input_kind = str(repo_input.get("kind") or "")
    repo_ref_selection_source = str(repo_input.get("ref_selection_source") or "")
    repository_ref = str(payload.get("repository_ref") or "")
    requested_ref = str(payload.get("requested_ref") or "")
    ref_source = str(payload.get("ref_source") or "")
    source_cache_dir = str(
        payload.get("source_cache_dir") or invocation.get("source_cache_dir") or ""
    )
    output_dir_defaulted = bool(invocation.get("output_dir_defaulted", False))
    default_output_dir = str(invocation.get("default_output_dir") or "")
    invocation_include = [str(item) for item in _list(invocation.get("include"))]
    invocation_exclude = [str(item) for item in _list(invocation.get("exclude"))]
    invocation_target_prefix = str(invocation.get("target_prefix") or "")
    invocation_max_sources = _int(invocation.get("max_sources", 0))
    invocation_max_candidates = _int(invocation.get("max_candidates", 0))
    auto_checkout_action_count = sum(
        1
        for item in _list(payload.get("agent_auto_actions"))
        if bool(_dict(item).get("checkout_repository_tests", False))
    )
    checkout_depth = _int(invocation.get("repository_checkout_depth", 0))
    checkout_status = str(payload.get("repository_checkout_status") or "")
    dynamic_level = str(readiness.get("dynamic_evidence_level") or "")
    patch_status = str(payload.get("repository_test_patch_validation_status") or "")
    repair_ready = bool(payload.get("repository_test_repair_ready", False))
    patch_success_count = _int(
        payload.get("repository_test_patch_validation_success_count", 0)
    )
    reflection_candidate_count = _int(
        payload.get("repository_test_patch_validation_reflection_candidate_count", 0)
    )
    patch_failed = patch_status == "fail"
    controller_action_id = str(
        _dict(controller.get("selected_action")).get("id") or ""
    )
    reflection_initial_failure_counts = _dict(
        reflection.get("initial_failure_type_counts")
    )
    reflection_recommended_strategies = _list(
        reflection.get("recommended_reflection_strategies")
    )
    reflection_refiner_status = str(
        reflection.get("reflection_refiner_status") or ""
    )
    reflection_refiner_reason = str(
        reflection.get("reflection_refiner_reason") or ""
    )
    reflection_trace_available = bool(_dict(reflection).get("available", False))
    reflection_refiner_blocked = (
        reflection_refiner_status in {"blocked", "unavailable", "unsupported", "error"}
        or reflection_refiner_reason.startswith("missing_api_key:")
        or reflection_refiner_reason == "missing_llm_api_key"
        or bool(payload.get("repository_llm_reflection_blocked", False))
    )
    reflection_failure_diagnosed = bool(
        reflection_trace_available
        and reflection_initial_failure_counts
        and (reflection_recommended_strategies or reflection_refiner_blocked)
    )
    reflection_action_planned = controller_action_id in {
        "run_patch_reflection_loop",
        "run_llm_patch_reflection_loop",
    }
    reflection_response_ready = bool(
        reflection_candidate_count > 0
        or reflection_action_planned
        or reflection_failure_diagnosed
    )
    fault_rankings = [_dict(item) for item in _list(fault.get("rankings"))]
    fault_source_role_counts = _dict(fault.get("source_role_counts"))
    score_decomposition_fields = [
        "static_rule_score",
        "graph_score",
        "source_role_score",
        "sbfl_score",
        "dynamic_test_evidence_score",
        "final_score",
    ]
    score_decomposed_rankings = [
        row
        for row in fault_rankings
        if all(field in row for field in score_decomposition_fields)
    ]
    top_decomposed_row = (
        score_decomposed_rankings[0] if score_decomposed_rankings else {}
    )
    overlay_status = str(payload.get("repository_test_failure_overlay_status") or "")
    overlay_reason = str(payload.get("repository_test_failure_overlay_reason") or "")
    overlay_json = str(payload.get("repository_test_failure_overlay_json") or "")
    overlay_markdown = str(
        payload.get("repository_test_failure_overlay_markdown") or ""
    )
    overlay_attempted_cases = _int(
        payload.get("repository_test_failure_overlay_attempted_cases", 0)
    )
    overlay_supported_candidates = _int(
        payload.get("repository_test_failure_overlay_supported_candidates", 0)
    )
    overlay_attempted = bool(
        overlay_status
        or overlay_reason
        or overlay_json
        or overlay_markdown
        or overlay_attempted_cases > 0
        or overlay_supported_candidates > 0
    )
    overlay_testability_status = str(
        _dict(answers.get("testability")).get("status") or ""
    )
    overlay_route_answered = overlay_testability_status in {
        "overlay_failing_tests_available",
        "overlay_not_usable",
    }
    repair_decision_audit = _repair_decision_audit_summary(payload)

    criteria = [
        _agent_goal_check(
            "one_command_input",
            bool(payload.get("repo_spec")) and bool(payload.get("repo")),
            (
                f"repo_spec={payload.get('repo_spec') or 'none'}, "
                f"repo={payload.get('repo') or 'none'}, "
                f"output_dir_defaulted={str(output_dir_defaulted).lower()}, "
                f"default_output_dir={default_output_dir or 'none'}"
            ),
        ),
        _agent_goal_check(
            "controller_observe_plan_act_verify_reflect_replan",
            _list(controller.get("control_loop"))
            == ["observe", "plan", "act", "verify", "reflect", "replan"]
            and bool(_dict(controller.get("selected_action")).get("id"))
            and loop_audit_complete,
            (
                f"{str(_dict(controller.get('selected_action')).get('id') or 'none')}; "
                f"loop_audit={loop_audit_complete_count}/{loop_audit_count}"
            ),
        ),
        _agent_goal_check(
            "github_ref_provenance",
            bool(repository_ref and ref_source and repo_input_kind)
            or has_blocker_path,
            (
                f"input_kind={repo_input_kind or 'none'}, "
                f"ref_selection={repo_ref_selection_source or 'none'}, "
                f"repository_ref={repository_ref or 'none'}, "
                f"requested_ref={requested_ref or 'default'}, "
                f"ref_source={ref_source or 'none'}"
            ),
        ),
        _agent_goal_check(
            "source_cache_and_filter_controls",
            bool(
                source_cache_dir
                and invocation_max_sources > 0
                and invocation_max_candidates > 0
            )
            or has_blocker_path,
            (
                f"cache_dir={source_cache_dir or 'none'}, "
                f"cache_reuse={str(bool(payload.get('discovery_cache_reuse', False))).lower()}, "
                f"prefer_cached={str(bool(invocation.get('prefer_cached_discovery', False))).lower()}, "
                f"include={_format_list(invocation_include)}, "
                f"exclude={_format_list(invocation_exclude)}, "
                f"target_prefix={invocation_target_prefix or 'none'}, "
                f"limits={invocation_max_sources}/{invocation_max_candidates}"
            ),
        ),
        _agent_goal_check(
            "shallow_checkout_policy",
            checkout_depth > 0 or bool(checkout_status) or has_blocker_path,
            (
                f"requested={str(bool(invocation.get('checkout_repository_tests', False))).lower()}, "
                f"depth={checkout_depth}, "
                f"status={checkout_status or 'none'}, "
                f"auto_checkout_actions={auto_checkout_action_count}"
            ),
        ),
        _agent_goal_check(
            "core_artifacts_auditable",
            not _list(inventory.get("missing_core_artifacts"))
            and _int(inventory.get("artifact_count", 0)) > 0,
            (
                f"missing_core={_format_list(_list(inventory.get('missing_core_artifacts')))}, "
                f"artifacts={_int(inventory.get('available_count', 0))}/"
                f"{_int(inventory.get('artifact_count', 0))}"
            ),
        ),
        _agent_goal_check(
            "repo_understanding_or_actionable_blocker",
            _int(structure.get("analyzed_file_count", 0)) > 0 or has_blocker_path,
            (
                f"files={_int(structure.get('analyzed_file_count', 0))}, "
                f"functions={_int(structure.get('function_count', 0))}, "
                f"blocker={blocker or 'none'}"
            ),
        ),
        _agent_goal_check(
            "graph_modeling_or_actionable_blocker",
            (
                _int(repo_graph.get("file_node_count", 0)) > 0
                or _int(repo_graph.get("function_node_count", 0)) > 0
                or has_blocker_path
            ),
            (
                f"files={_int(repo_graph.get('file_node_count', 0))}, "
                f"functions={_int(repo_graph.get('function_node_count', 0))}, "
                f"program_graph={str(bool(program_graph.get('available', False))).lower()}"
            ),
        ),
        _agent_goal_check(
            "static_signal_fallback",
            _int(payload.get("selected_signal_count", 0)) > 0
            or _int(payload.get("total_signal_count", 0)) > 0
            or has_blocker_path,
            (
                f"selected={_int(payload.get('selected_signal_count', 0))}, "
                f"total={_int(payload.get('total_signal_count', 0))}, "
                f"status={payload.get('static_intelligence_status') or 'none'}"
            ),
        ),
        _agent_goal_check(
            "topk_fault_localization_or_actionable_blocker",
            bool(fault_rankings) or has_blocker_path,
            (
                f"rankings={len(fault_rankings)}, "
                f"mode={fault.get('mode') or 'none'}, "
                f"top={fault.get('top_function') or 'none'}"
            ),
        ),
        _agent_goal_check(
            "application_candidate_coverage_audited",
            (not fault_rankings)
            or bool(fault_source_role_counts)
            or has_blocker_path,
            (
                f"rankings={len(fault_rankings)}, "
                f"roles={_format_counts(fault_source_role_counts)}, "
                f"application_candidates={_int(fault.get('application_candidate_count', 0))}, "
                f"top_role={fault.get('top_source_role') or 'none'}"
            ),
        ),
        _agent_goal_check(
            "fault_score_decomposition",
            (
                bool(fault_rankings)
                and len(score_decomposed_rankings) == len(fault_rankings)
            )
            or has_blocker_path,
            (
                f"rankings={len(fault_rankings)}, "
                f"decomposed={len(score_decomposed_rankings)}, "
                f"fields={_format_list(score_decomposition_fields)}, "
                f"top_final={_float(top_decomposed_row.get('final_score', 0.0)):.4f}"
            ),
        ),
        _agent_goal_check(
            "test_environment_diagnosis",
            bool(
                payload.get("repository_test_setup_doctor_status")
                or payload.get("repository_test_environment_status")
                or readiness.get("planned_repository_test_command")
                or has_blocker_path
            ),
            (
                f"setup_doctor={payload.get('repository_test_setup_doctor_status') or 'none'}, "
                f"env={payload.get('repository_test_environment_status') or 'none'}, "
                f"runner={readiness.get('planned_repository_test_runner') or 'none'}"
            ),
        ),
        _agent_goal_check(
            "dynamic_evidence_policy",
            dynamic_level in {
                "failing_tests",
                "passing_tests",
                "collection_failure",
                "not_executed",
                "none",
                "",
            }
            or has_blocker_path,
            (
                f"dynamic_level={dynamic_level or 'none'}, "
                f"usable={str(bool(readiness.get('dynamic_evidence_usable_for_localization', False))).lower()}"
            ),
        ),
        _agent_goal_check(
            "failure_overlay_route_audited",
            (
                not overlay_attempted
                or (
                    bool(overlay_status)
                    and bool(overlay_json)
                    and bool(overlay_markdown)
                    and overlay_route_answered
                )
            ),
            (
                f"attempted={str(overlay_attempted).lower()}, "
                f"status={overlay_status or 'none'}, "
                f"reason={overlay_reason or 'none'}, "
                f"artifacts={str(bool(overlay_json and overlay_markdown)).lower()}, "
                f"testability={overlay_testability_status or 'none'}"
            ),
        ),
        _agent_goal_check(
            "repair_decision_audit",
            bool(repair_decision_audit.get("passed", False)),
            str(repair_decision_audit.get("evidence") or ""),
        ),
        _agent_goal_check(
            "repair_claim_requires_validation",
            not repair_ready
            or (patch_status == "pass" and patch_success_count > 0),
            (
                f"repair_ready={str(repair_ready).lower()}, "
                f"patch_status={patch_status or 'none'}, "
                f"success_count={patch_success_count}"
            ),
        ),
        _agent_goal_check(
            "reflection_loop_when_patch_fails",
            not patch_failed
            or reflection_response_ready
            or has_blocker_path,
            (
                f"patch_failed={str(patch_failed).lower()}, "
                f"reflection_candidates={reflection_candidate_count}, "
                f"trace={str(reflection_trace_available).lower()}, "
                f"action={controller_action_id or 'none'}, "
                f"initial_failure_types={_format_counts(reflection_initial_failure_counts)}, "
                f"strategies={len(reflection_recommended_strategies)}, "
                f"refiner={reflection_refiner_status or 'none'}/"
                f"{reflection_refiner_reason or 'none'}"
            ),
        ),
        _agent_goal_check(
            "final_answers_complete",
            bool(_dict(answers.get("answer_coverage")).get("complete", False)),
            (
                f"{_int(_dict(answers.get('answer_coverage')).get('answered_question_count', 0))}/"
                f"{_int(_dict(answers.get('answer_coverage')).get('required_question_count', 0))} answers"
            ),
        ),
        _agent_goal_check(
            "acceptance_gate_passed",
            bool(acceptance.get("passed", False)),
            (
                f"{_int(acceptance.get('passed_check_count', 0))}/"
                f"{_int(acceptance.get('check_count', 0))} checks"
            ),
        ),
    ]
    failed = [row for row in criteria if not bool(row.get("passed", False))]
    return {
        "status": "pass" if not failed else "warning",
        "reason": (
            "agent_goal_readiness_passed"
            if not failed
            else "agent_goal_readiness_has_gaps"
        ),
        "passed": not failed,
        "criteria_count": len(criteria),
        "passed_criteria_count": len(criteria) - len(failed),
        "failed_criteria_count": len(failed),
        "failed_criteria": [str(row.get("name") or "") for row in failed],
        "criteria": criteria,
    }


def _agent_goal_check(name: str, passed: bool, evidence: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "evidence": evidence,
    }


def _refresh_agent_goal_readiness_and_controller(
    payload: dict[str, Any],
) -> dict[str, Any]:
    payload["agent_goal_readiness"] = _agent_goal_readiness_summary(payload)
    payload["agent_controller"] = build_agent_controller_plan(payload)
    payload["agent_decision_timeline"] = _agent_decision_timeline_summary(payload)
    payload["acceptance_gate"] = _acceptance_gate_summary(payload)
    payload["agent_goal_readiness"] = _agent_goal_readiness_summary(payload)
    payload["final_report"] = _final_agent_report_summary(payload)
    return payload


def _acceptance_check(name: str, passed: bool, evidence: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "evidence": evidence,
    }


def _conditional_artifact_group_ready(
    inventory: dict[str, Any],
    group_name: str,
) -> bool:
    rows = [_dict(item) for item in _list(_dict(inventory.get("groups")).get(group_name))]
    required = [row for row in rows if bool(row.get("required_now", False))]
    return all(bool(row.get("available", False)) for row in required)


def _conditional_artifact_group_evidence(
    inventory: dict[str, Any],
    group_name: str,
) -> str:
    rows = [_dict(item) for item in _list(_dict(inventory.get("groups")).get(group_name))]
    required = [row for row in rows if bool(row.get("required_now", False))]
    available = sum(1 for row in required if bool(row.get("available", False)))
    missing = [
        str(row.get("name") or "")
        for row in required
        if not bool(row.get("available", False))
    ]
    if missing:
        return f"{available}/{len(required)} available; missing={', '.join(missing)}"
    return f"{available}/{len(required)} available"


def _repair_decision_audit_summary(payload: dict[str, Any]) -> dict[str, Any]:
    llm_patch_audit = _dict(payload.get("repository_llm_patch_generation_audit"))
    llm_reflection_audit = _dict(payload.get("repository_llm_reflection_audit"))
    patch_mode = str(
        payload.get("repository_patch_generation_mode")
        or llm_patch_audit.get("mode")
        or ""
    ).lower()
    patch_generation_status = str(
        payload.get("repository_llm_patch_generation_status")
        or llm_patch_audit.get("status")
        or ""
    )
    patch_generation_reason = str(
        payload.get("repository_llm_patch_generation_reason")
        or llm_patch_audit.get("reason")
        or ""
    )
    patch_candidate_status = str(
        payload.get("repository_test_patch_candidates_status") or ""
    )
    patch_candidate_count = _int(
        payload.get("repository_test_patch_candidate_count", 0)
    )
    repair_entered = _repair_entered(payload)
    patch_validation_entered = _patch_validation_entered(payload)
    patch_generation_required = bool(
        repair_entered
        or patch_mode in {"llm", "hybrid"}
        or patch_generation_status not in {"", "disabled"}
        or patch_candidate_status not in {"", "skipped", "not_executed"}
        or patch_candidate_count > 0
    )
    llm_patch_required = bool(
        patch_mode in {"llm", "hybrid"}
        or patch_generation_status not in {"", "disabled"}
        or patch_generation_reason
        not in {"", "patch_generation_mode_rule"}
    )
    llm_patch_blocked = bool(
        payload.get("repository_llm_patch_blocked", False)
        or llm_patch_audit.get("blocked", False)
        or patch_generation_status == "blocked"
        or patch_generation_reason == "missing_llm_api_key"
        or patch_generation_reason.startswith("missing_api_key:")
    )
    llm_patch_provider = str(
        payload.get("repository_llm_patch_provider")
        or llm_patch_audit.get("provider")
        or ""
    )
    llm_patch_model = str(
        payload.get("repository_llm_patch_model")
        or llm_patch_audit.get("model")
        or ""
    )
    llm_patch_api_key_env = str(
        payload.get("repository_llm_patch_api_key_env")
        or llm_patch_audit.get("api_key_env")
        or ""
    )
    llm_reflection_mode = str(
        payload.get("repository_test_patch_validation_reflection_mode")
        or llm_reflection_audit.get("mode")
        or ""
    ).lower()
    llm_reflection_status = str(
        payload.get("repository_llm_reflection_status")
        or llm_reflection_audit.get("status")
        or ""
    )
    llm_reflection_reason = str(
        payload.get("repository_llm_reflection_reason")
        or llm_reflection_audit.get("reason")
        or ""
    )
    llm_reflection_required = bool(
        llm_reflection_mode == "llm"
        or llm_reflection_audit.get("enabled", False)
        or (
            llm_reflection_mode not in {"", "rule"}
            and llm_reflection_status
            in {"blocked", "unavailable", "unsupported", "error"}
        )
        or llm_reflection_reason.startswith("missing_api_key:")
    )
    llm_reflection_blocked = bool(
        payload.get("repository_llm_reflection_blocked", False)
        or llm_reflection_audit.get("blocked", False)
        or llm_reflection_status in {"blocked", "unavailable", "unsupported", "error"}
        or llm_reflection_reason.startswith("missing_api_key:")
    )
    llm_reflection_provider = str(
        payload.get("repository_llm_reflection_provider")
        or llm_reflection_audit.get("provider")
        or ""
    )
    llm_reflection_model = str(
        payload.get("repository_llm_reflection_model")
        or llm_reflection_audit.get("model")
        or ""
    )
    llm_reflection_api_key_env = str(
        payload.get("repository_llm_reflection_api_key_env")
        or llm_reflection_audit.get("api_key_env")
        or ""
    )

    failed: list[str] = []
    if patch_generation_required and patch_mode not in {"rule", "llm", "hybrid"}:
        failed.append("patch_generation_mode")
    if patch_validation_entered and not str(
        payload.get("repository_test_patch_validation_status") or ""
    ):
        failed.append("patch_validation_status")
    if llm_patch_required:
        if not llm_patch_audit:
            failed.append("llm_patch_audit_missing")
        if patch_mode not in {"llm", "hybrid"}:
            failed.append("llm_patch_mode")
        if not (patch_generation_status or patch_generation_reason):
            failed.append("llm_patch_status")
        if bool(llm_patch_audit.get("enabled", True)) and not (
            llm_patch_provider and llm_patch_model
        ):
            failed.append("llm_patch_provider_model")
        if llm_patch_blocked and not (
            str(llm_patch_audit.get("blocker") or "")
            or patch_generation_reason
        ):
            failed.append("llm_patch_blocker")
        if llm_patch_blocked and not llm_patch_api_key_env:
            failed.append("llm_patch_api_key_env")
    if llm_reflection_required:
        if not llm_reflection_audit:
            failed.append("llm_reflection_audit_missing")
        if llm_reflection_mode != "llm":
            failed.append("llm_reflection_mode")
        if not (llm_reflection_status or llm_reflection_reason):
            failed.append("llm_reflection_status")
        if bool(llm_reflection_audit.get("enabled", True)) and not (
            llm_reflection_provider and llm_reflection_model
        ):
            failed.append("llm_reflection_provider_model")
        if llm_reflection_blocked and not (
            str(
                payload.get("repository_llm_reflection_blocker")
                or llm_reflection_audit.get("blocker")
                or ""
            )
            or llm_reflection_reason
        ):
            failed.append("llm_reflection_blocker")
        if llm_reflection_blocked and not llm_reflection_api_key_env:
            failed.append("llm_reflection_api_key_env")

    evidence = (
        f"repair_entered={str(repair_entered).lower()}, "
        f"patch_mode={patch_mode or 'none'}, "
        f"patch_status={patch_candidate_status or 'none'}, "
        f"patch_validation={payload.get('repository_test_patch_validation_status') or 'none'}, "
        f"llm_patch_required={str(llm_patch_required).lower()}, "
        f"llm_patch={patch_generation_status or 'none'}/"
        f"{patch_generation_reason or 'none'}, "
        f"llm_patch_blocked={str(llm_patch_blocked).lower()}, "
        f"llm_reflection_required={str(llm_reflection_required).lower()}, "
        f"llm_reflection={llm_reflection_status or 'none'}/"
        f"{llm_reflection_reason or 'none'}, "
        f"llm_reflection_blocked={str(llm_reflection_blocked).lower()}"
    )
    if failed:
        evidence = f"{evidence}, missing={_format_list(failed)}"
    return {
        "passed": not failed,
        "failed_checks": failed,
        "evidence": evidence,
        "patch_generation_required": patch_generation_required,
        "llm_patch_required": llm_patch_required,
        "llm_reflection_required": llm_reflection_required,
    }


def _final_agent_report_markdown_lines(report: dict[str, Any]) -> list[str]:
    controller = _dict(report.get("controller"))
    readiness = _dict(report.get("readiness"))
    verification = _dict(report.get("verification"))
    structure = _dict(report.get("repository_structure"))
    testability = _dict(report.get("testability"))
    repairability = _dict(report.get("repairability"))
    top_suspicious = [_dict(item) for item in _list(report.get("top_suspicious_functions"))]
    artifacts = _dict(report.get("evidence_artifacts"))
    compliance = _dict(report.get("objective_compliance"))
    lines = [
        "## Final Auditable Report",
        "",
        f"- Executive Summary: {_markdown_cell(report.get('executive_summary') or '')}",
        f"- Current Stage: `{_markdown_cell(readiness.get('current_stage') or 'none')}`",
        f"- Blocker: `{_markdown_cell(report.get('blocker') or 'none')}`",
        f"- Next Action: {_markdown_cell(report.get('next_action') or '')}",
        (
            "- Controller: "
            f"`{_markdown_cell(controller.get('status') or 'none')}` / "
            f"`{_markdown_cell(controller.get('selected_action') or 'none')}` "
            f"(executable={str(bool(controller.get('selected_action_executable_now', False))).lower()})"
        ),
        (
            "- Verification: "
            f"acceptance={_markdown_cell(verification.get('acceptance_gate_status') or 'none')} "
            f"({_int(verification.get('acceptance_gate_passed_checks', 0))}/"
            f"{_int(verification.get('acceptance_gate_total_checks', 0))}), "
            f"goal={_markdown_cell(verification.get('agent_goal_readiness_status') or 'none')} "
            f"({_int(verification.get('agent_goal_readiness_passed_criteria', 0))}/"
            f"{_int(verification.get('agent_goal_readiness_total_criteria', 0))})"
        ),
        (
            "- Repair Claim: "
            f"`{_markdown_cell(verification.get('repair_success_claim') or 'not_claimed')}`"
        ),
        (
            "- Objective Compliance: "
            f"`{_markdown_cell(compliance.get('status') or 'none')}` "
            f"({_int(compliance.get('passed_section_count', 0))}/"
            f"{_int(compliance.get('section_count', 0))} sections)"
        ),
        "",
        "| Required Question | Answer |",
        "| --- | --- |",
        (
            "| Repository structure | "
            f"{_markdown_cell(structure.get('answer') or '')} |"
        ),
        (
            "| Most suspicious functions | "
            f"{_markdown_cell(_format_top_suspicious_names(top_suspicious))} |"
        ),
        (
            "| Why suspicious | "
            f"{_markdown_cell(report.get('why_suspicious') or '')} |"
        ),
        (
            "| Can test | "
            f"{_markdown_cell(testability.get('answer') or testability.get('status') or '')} |"
        ),
        (
            "| Can repair | "
            f"{_markdown_cell(repairability.get('answer') or repairability.get('status') or '')} |"
        ),
        (
            "| Current blocker | "
            f"`{_markdown_cell(report.get('blocker') or 'none')}` |"
        ),
        (
            "| Agent next step | "
            f"{_markdown_cell(report.get('next_action') or '')} |"
        ),
        "",
        "### Objective Compliance",
        "",
        "| Section | Status | Criteria | Failed Criteria |",
        "| --- | --- | ---: | --- |",
    ]
    for section_value in _list(compliance.get("sections")):
        section = _dict(section_value)
        lines.append(
            "| "
            f"{_markdown_cell(section.get('id') or '')} | "
            f"{'pass' if bool(section.get('passed', False)) else 'warning'} | "
            f"{_int(section.get('passed_criteria_count', 0))}/"
            f"{_int(section.get('criteria_count', 0))} | "
            f"{_markdown_cell(_format_list(_list(section.get('failed_criteria'))))} |"
        )
    if not _list(compliance.get("sections")):
        lines.append("| none | warning | 0/0 | none |")
    lines.extend(
        [
        "",
        "### Final Report Evidence Artifacts",
        "",
        "| Artifact | Path |",
        "| --- | --- |",
        ]
    )
    for name in (
        "intelligence_json",
        "controller_json",
        "repository_structure_json",
        "repo_graph_json",
        "fault_localization_json",
        "analysis_readiness_json",
        "dynamic_evidence_json",
        "patch_candidates_json",
        "patch_validation_json",
        "reflection_trace_json",
    ):
        value = str(artifacts.get(name) or "")
        if value:
            lines.append(f"| {_markdown_cell(name)} | `{_markdown_cell(value)}` |")
    lines.append("")
    return lines


def _format_top_suspicious_names(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "none"
    names = []
    for row in rows[:5]:
        rank = _int(row.get("rank", len(names) + 1))
        name = str(row.get("function") or "unknown")
        score = _float(row.get("final_score", 0.0))
        names.append(f"#{rank} {name} ({score:.4f})")
    return "; ".join(names)


def _agent_answers_markdown_lines(answers: dict[str, Any]) -> list[str]:
    top_suspicious = _list(answers.get("top_suspicious_functions"))
    coverage = _dict(answers.get("answer_coverage"))
    application_coverage = _dict(answers.get("application_candidate_coverage"))
    lines = [
        "## Agent Answers",
        "",
        f"- Repository Structure: {_markdown_cell(answers.get('repository_structure_answer') or '')}",
        (
            "- Most Suspicious Functions: "
            f"{_markdown_cell(answers.get('most_suspicious_functions_answer') or '')}"
        ),
        (
            "- Application Candidate Coverage: "
            f"{_markdown_cell(application_coverage.get('answer') or '')}"
        ),
        f"- Why Suspicious: {_markdown_cell(answers.get('why_suspicious_answer') or '')}",
        f"- Can Test: {_markdown_cell(answers.get('testability_answer') or '')}",
        f"- Can Repair: {_markdown_cell(answers.get('repairability_answer') or '')}",
        f"- Audit Artifacts: {_markdown_cell(answers.get('artifact_inventory_answer') or '')}",
        f"- Blocker: `{_markdown_cell(answers.get('blocker_answer') or 'none')}`",
        f"- Next Action: {_markdown_cell(answers.get('next_action_answer') or '')}",
        (
            "- Selected Controller Action: "
            f"`{_markdown_cell(answers.get('selected_controller_action') or 'none')}`"
        ),
        "",
        "### Answer Coverage",
        "",
        f"- Complete: {str(bool(coverage.get('complete', False))).lower()}",
        (
            "- Answered Questions: "
            f"{_int(coverage.get('answered_question_count', 0))}/"
            f"{_int(coverage.get('required_question_count', 0))}"
        ),
        "",
        "| Question | Answered | Evidence Field |",
        "| --- | --- | --- |",
    ]
    for item_value in _list(coverage.get("questions")):
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('id'))} | "
            f"{str(bool(item.get('answered', False))).lower()} | "
            f"`{_markdown_cell(item.get('answer_field'))}` |"
        )
    if not _list(coverage.get("questions")):
        lines.append("| none | false | none |")
    lines.extend(
        [
            "",
            "### Application Candidate Coverage",
            "",
            f"- Status: `{_markdown_cell(application_coverage.get('status') or 'none')}`",
            (
                "- Application Candidates: "
                f"{_int(application_coverage.get('application_candidate_count', 0))}/"
                f"{_int(application_coverage.get('ranked_function_count', 0))}"
            ),
            (
                "- Source Roles: "
                f"{_markdown_cell(_format_counts(_dict(application_coverage.get('source_role_counts'))))}"
            ),
            (
                "- Recommended Target Prefix: "
                f"`{_markdown_cell(application_coverage.get('recommended_target_prefix') or 'none')}`"
            ),
            "",
            "### Top Suspicious Functions",
            "",
            "| Rank | Function | File | FinalScore | Why |",
            "| ---: | --- | --- | ---: | --- |",
        ]
    )
    for row_value in top_suspicious:
        row = _dict(row_value)
        lines.append(
            "| "
            f"{_int(row.get('rank', 0))} | "
            f"{_markdown_cell(row.get('function'))} | "
            f"{_markdown_cell(row.get('file'))} | "
            f"{_float(row.get('final_score', 0.0)):.4f} | "
            f"{_markdown_cell(row.get('why'))} |"
        )
    if not top_suspicious:
        lines.append("| 0 | none |  | 0.0000 | none |")
    lines.append("")
    return lines


def _acceptance_gate_markdown_lines(gate: dict[str, Any]) -> list[str]:
    lines = [
        "## Acceptance Gate",
        "",
        f"- Status: `{_markdown_cell(gate.get('status') or 'none')}`",
        f"- Reason: `{_markdown_cell(gate.get('reason') or 'none')}`",
        (
            "- Checks: "
            f"{_int(gate.get('passed_check_count', 0))}/"
            f"{_int(gate.get('check_count', 0))} passed"
        ),
        (
            "- Failed Checks: "
            f"`{_markdown_cell(_format_list(_list(gate.get('failed_checks'))))}`"
        ),
        "",
        "| Check | Passed | Evidence |",
        "| --- | --- | --- |",
    ]
    for item_value in _list(gate.get("checks")):
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('name'))} | "
            f"{str(bool(item.get('passed', False))).lower()} | "
            f"{_markdown_cell(item.get('evidence'))} |"
        )
    if not _list(gate.get("checks")):
        lines.append("| none | false | none |")
    lines.append("")
    return lines


def _agent_goal_readiness_markdown_lines(readiness: dict[str, Any]) -> list[str]:
    lines = [
        "## Agent Goal Readiness",
        "",
        f"- Status: `{_markdown_cell(readiness.get('status') or 'none')}`",
        f"- Reason: `{_markdown_cell(readiness.get('reason') or 'none')}`",
        (
            "- Criteria: "
            f"{_int(readiness.get('passed_criteria_count', 0))}/"
            f"{_int(readiness.get('criteria_count', 0))} passed"
        ),
        (
            "- Failed Criteria: "
            f"`{_markdown_cell(_format_list(_list(readiness.get('failed_criteria'))))}`"
        ),
        "",
        "| Criterion | Passed | Evidence |",
        "| --- | --- | --- |",
    ]
    for item_value in _list(readiness.get("criteria")):
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('name'))} | "
            f"{str(bool(item.get('passed', False))).lower()} | "
            f"{_markdown_cell(item.get('evidence'))} |"
        )
    if not _list(readiness.get("criteria")):
        lines.append("| none | false | none |")
    lines.append("")
    return lines


def _render_github_repo_intelligence_payload(payload: dict[str, Any]) -> str:
    repo_input = _dict(payload.get("repo_input"))
    lines = [
        "# GitHub Repository Intelligence Summary",
        "",
        f"- Repo: `{_markdown_cell(payload['repo'])}`",
        f"- Input: `{_markdown_cell(payload['repo_spec'])}`",
        f"- Input Kind: `{_markdown_cell(repo_input.get('kind') or 'unknown')}`",
        (
            "- Ref Selection Source: "
            f"`{_markdown_cell(repo_input.get('ref_selection_source') or 'none')}`"
        ),
        (
            "- URL Inferred Ref: "
            f"`{_markdown_cell(repo_input.get('url_inferred_ref') or 'none')}`"
        ),
        (
            "- Ref Fallback Used: "
            f"{str(bool(repo_input.get('ref_fallback_used', False))).lower()}"
        ),
        (
            "- Ref Fallback Attempts: "
            f"{_int(repo_input.get('ref_fallback_attempt_count', 0))}"
        ),
        (
            "- Invocation: "
            f"profile=`{_markdown_cell(_dict(payload.get('agent_invocation')).get('effective_execution_profile') or 'unknown')}`, "
            f"agent={str(bool(_dict(payload.get('agent_invocation')).get('agent_mode', False))).lower()}, "
            f"shortcut={str(bool(_dict(payload.get('agent_invocation')).get('agent_shortcut', False))).lower()}, "
            f"output-defaulted={str(bool(_dict(payload.get('agent_invocation')).get('output_dir_defaulted', False))).lower()}, "
            f"auto-actions={_int(_dict(payload.get('agent_invocation')).get('auto_controller_max_actions', 0))}"
        ),
        f"- Repository Ref: `{_markdown_cell(payload.get('repository_ref') or 'default')}`",
        f"- Requested Ref: `{_markdown_cell(payload.get('requested_ref') or 'default')}`",
        f"- Ref Source: `{_markdown_cell(payload.get('ref_source') or 'default_branch_discovery')}`",
        f"- Source Cache Dir: `{_markdown_cell(payload.get('source_cache_dir') or 'none')}`",
        f"- Discovery Source: `{_markdown_cell(payload.get('discovery_source') or 'github_tree')}`",
        (
            "- Discovery Cache Fallback: "
            f"{str(bool(payload.get('discovery_cache_fallback', False))).lower()}"
        ),
        (
            "- Discovery Cache Reuse: "
            f"{str(bool(payload.get('discovery_cache_reuse', False))).lower()}"
        ),
        (
            "- Discovery Cache Reuse Reason: "
            f"`{_markdown_cell(payload.get('discovery_cache_reuse_reason') or 'none')}`"
        ),
        (
            "- API Rate Limit Checkout Fallback: "
            f"{str(bool(payload.get('discovery_api_rate_limit_checkout_fallback', False))).lower()}"
        ),
        (
            "- Agent Auto Actions: "
            f"{_int(payload.get('agent_auto_action_count', 0))} "
            f"(enabled={str(bool(payload.get('agent_auto_enabled', False))).lower()}, "
            f"max={_int(payload.get('agent_auto_max_actions', 0))}, "
            f"stop={_markdown_cell(payload.get('agent_auto_stop_reason') or 'none')})"
        ),
        (
            "- Agent Auto Stop State: "
            f"{_markdown_cell(_dict(payload.get('agent_auto_stop_state')).get('category') or 'none')} "
            f"via `{_markdown_cell(_dict(payload.get('agent_auto_stop_state')).get('action_id') or 'none')}`, "
            f"recovery=`{_markdown_cell(_dict(payload.get('agent_auto_stop_state')).get('recovery_policy') or 'none')}`, "
            "external="
            f"`{_markdown_cell(_dict(payload.get('agent_auto_stop_state')).get('external_input_kind') or 'none')}`"
        ),
        (
            "- Agent Auto Recommended Next Action: "
            f"{_markdown_cell(_dict(payload.get('agent_auto_stop_state')).get('recommended_next_action') or 'none')}"
        ),
        (
            "- Agent Loop Progress: "
            f"{_int(_dict(payload.get('agent_auto_loop_audit')).get('progress_count', 0))} progressed / "
            f"{_int(_dict(payload.get('agent_auto_loop_audit')).get('no_progress_count', 0))} no-progress, "
            f"complete-loop={str(bool(_dict(payload.get('agent_auto_loop_audit')).get('complete_loop_recorded', False))).lower()}"
        ),
        (
            "- Agent Loop Verify Outcomes: "
            f"{_markdown_cell(_format_counts(_dict(_dict(payload.get('agent_auto_loop_audit')).get('verify_outcome_counts'))))}"
        ),
        (
            "- Agent Loop Replan Policies: "
            f"{_markdown_cell(_format_counts(_dict(_dict(payload.get('agent_auto_loop_audit')).get('replan_policy_counts'))))}"
        ),
        (
            "- Agent Loop Goal Readiness: "
            f"{_markdown_cell(_format_counts(_dict(_dict(payload.get('agent_auto_loop_audit')).get('goal_readiness_status_counts'))))}"
        ),
        (
            "- Agent Decision Timeline: "
            f"`{_markdown_cell(_dict(payload.get('agent_decision_timeline')).get('status') or 'none')}` "
            f"({_int(_dict(payload.get('agent_decision_timeline')).get('complete_step_count', 0))}/"
            f"{_int(_dict(payload.get('agent_decision_timeline')).get('step_count', 0))} complete)"
        ),
        (
            "- Acceptance Gate: "
            f"`{_markdown_cell(_dict(payload.get('acceptance_gate')).get('status') or 'none')}` "
            f"({_int(_dict(payload.get('acceptance_gate')).get('passed_check_count', 0))}/"
            f"{_int(_dict(payload.get('acceptance_gate')).get('check_count', 0))} checks)"
        ),
        (
            "- Agent Goal Readiness: "
            f"`{_markdown_cell(_dict(payload.get('agent_goal_readiness')).get('status') or 'none')}` "
            f"({_int(_dict(payload.get('agent_goal_readiness')).get('passed_criteria_count', 0))}/"
            f"{_int(_dict(payload.get('agent_goal_readiness')).get('criteria_count', 0))} criteria)"
        ),
        f"- Output Dir: `{_markdown_cell(payload['output_dir'])}`",
        f"- Agent Status: `{_markdown_cell(payload['status'])}`",
        f"- Preset: `{_markdown_cell(payload['preset'])}`",
        (
            "- Static Intelligence: "
            f"`{_markdown_cell(payload['static_intelligence_status'])}`/"
            f"`{_markdown_cell(payload['static_intelligence_level'])}` "
            f"({_markdown_cell(payload['static_intelligence_reason'])})"
        ),
        (
            "- Sources: "
            f"{_int(payload['selected_source_count'])} selected / "
            f"{_int(payload['imported_source_count'])} imported"
        ),
        (
            "- Static Signals: "
            f"{_int(payload['selected_signal_count'])} selected / "
            f"{_int(payload['total_signal_count'])} total"
        ),
        (
            "- Candidate Limit Applied: "
            f"{str(bool(payload['candidate_limit_applied'])).lower()}"
        ),
        f"- Rule Counts: {_markdown_cell(_format_counts(_dict(payload['rule_counts'])))}",
        (
            "- Bug Type Counts: "
            f"{_markdown_cell(_format_counts(_dict(payload['bug_type_counts'])))}"
        ),
        f"- Quality Score: {_float(payload['quality_score']):.4f}",
        (
            "- Dynamic Validation: "
            f"`{_markdown_cell(payload['dynamic_validation_level'])}`"
        ),
        f"- Next Action: {_markdown_cell(payload['next_action'])}",
        "",
    ]
    github_error = _dict(payload.get("github_error"))
    if github_error:
        lines.extend(
            [
                "## GitHub Fetch Error",
                "",
                f"- Type: `{_markdown_cell(github_error.get('type') or 'unknown')}`",
                f"- Status Code: `{_markdown_cell(github_error.get('status_code') or 'unknown')}`",
                f"- URL: `{_markdown_cell(github_error.get('url') or 'unknown')}`",
                (
                    "- Rate Limit Remaining: "
                    f"`{_markdown_cell(github_error.get('rate_limit_remaining') or 'unknown')}`"
                ),
                (
                    "- Rate Limit Reset: "
                    f"`{_markdown_cell(github_error.get('rate_limit_reset') or 'unknown')}`"
                ),
                f"- Message: {_markdown_cell(github_error.get('message') or '')}",
                "",
                "### GitHub Fetch Next Actions",
                "",
            ]
        )
        for action in _list(payload.get("github_error_next_actions")):
            lines.append(f"- {_markdown_cell(action)}")
        if not _list(payload.get("github_error_next_actions")):
            lines.append("- Set GITHUB_TOKEN or rerun with a cached discovery artifact.")
        lines.append("")
    lines.extend(_agent_answers_markdown_lines(_dict(payload.get("agent_answers"))))
    lines.extend(_acceptance_gate_markdown_lines(_dict(payload.get("acceptance_gate"))))
    lines.extend(
        _agent_goal_readiness_markdown_lines(
            _dict(payload.get("agent_goal_readiness"))
        )
    )
    lines.extend(_final_agent_report_markdown_lines(_dict(payload.get("final_report"))))
    lines.extend(
        [
            "## Analysis Readiness",
            "",
        ]
    )
    readiness = _dict(payload.get("analysis_readiness"))
    lines.extend(
        [
            f"- Current Stage: `{_markdown_cell(readiness.get('current_stage', ''))}`",
            f"- Stage Number: {_int(readiness.get('stage_number', 0))}",
            f"- Next Stage: `{_markdown_cell(readiness.get('next_stage', ''))}`",
            f"- Blocker: `{_markdown_cell(readiness.get('blocker', ''))}`",
            f"- Next Action: {_markdown_cell(readiness.get('next_action', ''))}",
            (
                "- Completed Phases: "
                f"{_markdown_cell(_format_list(_list(readiness.get('completed_phases'))))}"
            ),
            (
                "- Capabilities: "
                f"static_report={str(bool(readiness.get('can_generate_static_report', False))).lower()}, "
                f"dynamic_tests={str(bool(readiness.get('can_attempt_dynamic_tests', False))).lower()}, "
                f"patch_repair={str(bool(readiness.get('can_attempt_patch_repair', False))).lower()}"
            ),
            "",
            "| Signal | Value |",
            "| --- | --- |",
            (
                "| Setup Doctor | "
                f"{_markdown_cell(readiness.get('repository_test_setup_doctor_status', '') or 'none')}"
                f"/{_markdown_cell(readiness.get('repository_test_setup_doctor_blocker', '') or 'none')}, "
                f"checks={_int(readiness.get('repository_test_setup_doctor_passed_check_count', 0))}/"
                f"{_int(readiness.get('repository_test_setup_doctor_check_count', 0))}, "
                f"warning={_int(readiness.get('repository_test_setup_doctor_warning_check_count', 0))}, "
                f"blocked={_int(readiness.get('repository_test_setup_doctor_blocked_check_count', 0))} |"
            ),
            (
                "| Planned Test Runner | "
                f"{_markdown_cell(readiness.get('planned_repository_test_runner', '') or 'none')}"
                f" fallback={str(bool(readiness.get('planned_repository_test_runner_fallback_used', False))).lower()}"
                f" reason={_markdown_cell(readiness.get('planned_repository_test_runner_fallback_reason', '') or 'none')} |"
            ),
            (
                "| Dynamic Evidence | "
                f"{_markdown_cell(readiness.get('dynamic_evidence_level', '') or 'none')} |"
            ),
            (
                "| Fault Localization | "
                f"{_markdown_cell(readiness.get('fault_localization_mode', '') or 'none')}"
                f"/{_markdown_cell(readiness.get('fault_localization_status', '') or 'none')} |"
            ),
            (
                "| Agent Plan Blocker | "
                f"{_markdown_cell(readiness.get('agent_execution_plan_primary_blocker', '') or 'none')} |"
            ),
            "",
        ]
    )
    controller = _dict(payload.get("agent_controller"))
    selected_action = _dict(controller.get("selected_action"))
    lines.extend(
        [
            "## Agent Controller",
            "",
            f"- Status: `{_markdown_cell(controller.get('status', ''))}`",
            f"- Objective: `{_markdown_cell(controller.get('objective', ''))}`",
            (
                "- Loop: "
                f"{_markdown_cell(' -> '.join(str(item) for item in _list(controller.get('control_loop'))))}"
            ),
            f"- Current Stage: `{_markdown_cell(controller.get('current_stage', ''))}`",
            f"- Next Stage: `{_markdown_cell(controller.get('next_stage', ''))}`",
            f"- Primary Blocker: `{_markdown_cell(controller.get('primary_blocker', ''))}`",
            f"- Selected Action: `{_markdown_cell(selected_action.get('id', ''))}`",
            f"- Action Phase: `{_markdown_cell(selected_action.get('phase', ''))}`",
            f"- Action Tool: `{_markdown_cell(selected_action.get('tool', ''))}`",
            (
                "- Executable Now: "
                f"{str(bool(selected_action.get('executable_now', False))).lower()}"
            ),
            f"- Reason: {_markdown_cell(selected_action.get('reason', ''))}",
            f"- Command: `{_markdown_cell(selected_action.get('command', ''))}`",
            "",
        ]
    )
    auto_actions = _list(payload.get("agent_auto_actions"))
    if auto_actions:
        lines.extend(
            [
                "### Controller Auto Actions",
                "",
                (
                    "| Action | Before Stage | Before Blocker | After Stage | "
                    "Verify Outcome | Replan Policy | Dynamic Evidence | Fault Localization | Failure Overlay | Regression Guard | Environment Repair | Patch Validation | "
                    "Repair Ready | Snapshot |"
                ),
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item_value in auto_actions:
            item = _dict(item_value)
            fault_cell = (
                f"{item.get('after_fault_localization_mode') or 'none'}"
                f"/{item.get('after_fault_localization_status') or 'none'}"
            )
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
                f"{_markdown_cell(item.get('before_stage'))} | "
                f"{_markdown_cell(item.get('before_blocker'))} | "
                f"{_markdown_cell(item.get('after_stage'))} | "
                f"{_markdown_cell(item.get('loop_verify_outcome') or 'none')} | "
                f"{_markdown_cell(item.get('loop_replan_policy') or 'none')} | "
                f"{_markdown_cell(item.get('after_dynamic_evidence_level'))} | "
                f"{_markdown_cell(fault_cell)} | "
                f"{_markdown_cell(_auto_action_failure_overlay_cell(item))} | "
                f"{_markdown_cell(guard_cell)} | "
                f"{_markdown_cell(environment_repair_cell)} | "
                f"{_markdown_cell(patch_cell)} | "
                f"{str(bool(item.get('after_repair_ready', False))).lower()} | "
                f"{_markdown_cell(item.get('pre_action_controller_json'))} |"
            )
        lines.append("")
    auto_trace = _list(payload.get("agent_auto_trace"))
    if auto_trace:
        lines.extend(
            [
                "### Controller Auto Trace",
                "",
                (
                    "| Iteration | Observed Stage | Blocker | Selected Action | "
                    "Executed | Verify Outcome | Progress | Replan Policy | Verify Dynamic | Failure Overlay | Regression Guard | Environment Repair | Stop Reason |"
                ),
                "| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
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
                f"{_markdown_cell(item.get('stop_reason') or 'continue')} |"
            )
        lines.append("")
    lines.extend(
        [
            "### Controller Decision Trace",
            "",
            "| Phase | Status | Evidence | Decision | Output |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    controller_trace = _list(controller.get("decision_trace"))
    for item_value in controller_trace:
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('phase'))} | "
            f"{_markdown_cell(item.get('status'))} | "
            f"{_markdown_cell(item.get('evidence'))} | "
            f"{_markdown_cell(item.get('decision'))} | "
            f"{_markdown_cell(item.get('output'))} |"
        )
    if not controller_trace:
        lines.append("| none | none | none | none | none |")
    lines.append("")
    reflection = _dict(payload.get("reflection_summary"))
    lines.extend(
        [
            "### Reflection Summary",
            "",
            f"- Available: {str(bool(reflection.get('available', False))).lower()}",
            f"- Status: `{_markdown_cell(reflection.get('status') or 'none')}`",
            f"- Reason: `{_markdown_cell(reflection.get('reason') or 'none')}`",
            f"- Patch Validation: `{_markdown_cell(reflection.get('patch_validation_status') or 'none')}`/`{_markdown_cell(reflection.get('patch_validation_reason') or 'none')}`",
            (
                "- Reflection: "
                f"enabled={str(bool(reflection.get('reflection_enabled', False))).lower()}, "
                f"mode={_markdown_cell(reflection.get('reflection_mode') or 'none')}, "
                f"refiner={_markdown_cell(reflection.get('reflection_refiner_status') or 'none')}"
            ),
            (
                "- LLM Reflection Config: "
                f"provider=`{_markdown_cell(payload.get('repository_llm_reflection_provider') or 'none')}`, "
                f"model=`{_markdown_cell(payload.get('repository_llm_reflection_model') or 'none')}`, "
                "api_key_present="
                f"{str(bool(payload.get('repository_llm_reflection_api_key_present', False))).lower()}, "
                f"blocked={str(bool(payload.get('repository_llm_reflection_blocked', False))).lower()}"
            ),
            (
                "- Counts: "
                f"initial_failures={_int(reflection.get('initial_failure_count', 0))}, "
                f"steps={_int(reflection.get('reflection_step_count', 0))}, "
                f"candidates={_int(reflection.get('reflection_candidate_count', 0))}, "
                f"successful={_int(reflection.get('successful_reflection_candidate_count', 0))}, "
                f"regression_candidates={_int(reflection.get('regression_reflection_candidate_count', 0))}, "
                f"successful_regression={_int(reflection.get('successful_regression_reflection_candidate_count', 0))}, "
                f"max_depth={_int(reflection.get('max_depth_executed', 0))}"
            ),
            (
                "- Final: "
                f"repair_ready={str(bool(reflection.get('repair_ready', False))).lower()}, "
                f"regression_ready={str(bool(reflection.get('regression_ready', False))).lower()}, "
                f"best={_markdown_cell(reflection.get('best_candidate_rule_id') or 'none')}/"
                f"{_markdown_cell(reflection.get('best_candidate_variant') or 'none')}"
            ),
            (
                "- Failure Types: "
                f"{_markdown_cell(_format_counts(_dict(reflection.get('failure_type_counts'))))}"
            ),
            (
                "- Initial Failure Types: "
                f"{_markdown_cell(_format_counts(_dict(reflection.get('initial_failure_type_counts'))))}"
            ),
            (
                "- Reflection Parent Failure Types: "
                f"{_markdown_cell(_format_counts(_dict(reflection.get('reflection_parent_failure_type_counts'))))}"
            ),
            (
                "- Successful Reflection Parent Failure Types: "
                f"{_markdown_cell(_format_counts(_dict(reflection.get('successful_reflection_parent_failure_type_counts'))))}"
            ),
            f"- Trace: `{_markdown_cell(reflection.get('markdown') or reflection.get('path') or 'none')}`",
            "",
        ]
    )
    guard = _dict(payload.get("repository_test_regression_guard"))
    if guard:
        lines.extend(
            [
                "### Regression Guard",
                "",
                f"- Status: `{_markdown_cell(guard.get('status') or 'none')}`",
                f"- Reason: `{_markdown_cell(guard.get('reason') or 'none')}`",
                f"- Command: `{_markdown_cell(guard.get('command') or 'none')}`",
                (
                    "- Dynamic Evidence: "
                    f"`{_markdown_cell(guard.get('dynamic_evidence_level') or 'none')}`"
                ),
                (
                    "- Role: "
                    f"`{_markdown_cell(guard.get('guard_role') or 'regression_validation_only')}`"
                ),
                (
                    "- Usable For Localization: "
                    f"{str(bool(guard.get('usable_for_localization', False))).lower()}"
                ),
                (
                    "- Usable For Patch Validation: "
                    f"{str(bool(guard.get('usable_for_patch_validation', False))).lower()}"
                ),
                "",
            ]
        )
    environment_repair = _dict(payload.get("repository_test_environment_repair_plan"))
    if environment_repair:
        lines.extend(
            [
                "### Environment Repair Plan",
                "",
                f"- Status: `{_markdown_cell(environment_repair.get('status') or 'none')}`",
                f"- Reason: `{_markdown_cell(environment_repair.get('reason') or 'none')}`",
                f"- Blocker: `{_markdown_cell(environment_repair.get('blocker') or 'none')}`",
                (
                    "- Recommended Install Command: "
                    f"`{_markdown_cell(environment_repair.get('recommended_install_command') or 'none')}`"
                ),
                (
                    "- Recommended Test Command: "
                    f"`{_markdown_cell(environment_repair.get('recommended_test_command') or 'none')}`"
                ),
                (
                    "- Missing Dependency Modules: "
                    f"`{_markdown_cell(', '.join(_list(environment_repair.get('missing_dependency_modules'))) or 'none')}`"
                ),
                (
                    "- Missing Dependency Install Hint: "
                    f"`{_markdown_cell(environment_repair.get('missing_dependency_install_hint') or 'none')}`"
                ),
                (
                    "- Auto Installed Dependencies: "
                    f"{str(bool(environment_repair.get('auto_installed_dependencies', False))).lower()}"
                ),
                "",
            ]
        )
    phase4 = _dict(payload.get("phase4_search_evaluation"))
    if phase4:
        search_budget = _dict(phase4.get("search_budget"))
        phase4_execution = _dict(phase4.get("execution"))
        lines.extend(
            [
                "### Phase 4 Search Evaluation",
                "",
                f"- Status: `{_markdown_cell(phase4.get('status') or 'none')}`",
                f"- Reason: `{_markdown_cell(phase4.get('reason') or 'none')}`",
                (
                    "- Ready For Phase 4: "
                    f"{str(bool(phase4.get('ready_for_phase4', False))).lower()}"
                ),
                (
                    "- Repair Scope: "
                    f"`{_markdown_cell(phase4.get('repair_validation_scope') or 'none')}`"
                ),
                (
                    "- Baseline Regression Caveat: "
                    f"{str(bool(phase4.get('baseline_regression_caveat', False))).lower()}"
                ),
                (
                    "- Full-Suite Green Claim Allowed: "
                    f"{str(bool(phase4.get('full_suite_green_claim_allowed', False))).lower()}"
                ),
                (
                    "- Lightweight Evaluation Executed: "
                    f"{str(bool(phase4_execution.get('executed', False))).lower()} "
                    f"(`{_markdown_cell(phase4_execution.get('status') or 'not_executed')}`)"
                ),
                (
                    "- Search Budget: "
                    f"candidates={_int(search_budget.get('candidate_count', 0))}, "
                    f"executed={_int(search_budget.get('executed_count', 0))}, "
                    f"success={_int(search_budget.get('success_count', 0))}, "
                    f"rate={_float(search_budget.get('success_rate', 0.0)):.4f}"
                ),
                "",
            ]
        )
    inventory = _dict(payload.get("artifact_inventory"))
    if inventory:
        groups = _dict(inventory.get("groups"))
        lines.extend(
            [
                "### Artifact Inventory",
                "",
                f"- Status: `{_markdown_cell(inventory.get('status') or 'none')}`",
                f"- Reason: `{_markdown_cell(inventory.get('reason') or 'none')}`",
                (
                    "- File Check Enabled: "
                    f"{str(bool(inventory.get('file_check_enabled', False))).lower()}"
                ),
                (
                    "- Coverage: "
                    f"{_int(inventory.get('available_count', 0))}/"
                    f"{_int(inventory.get('artifact_count', 0))}"
                ),
                (
                    "- Required Coverage: "
                    f"{_int(inventory.get('required_available_count', 0))}/"
                    f"{_int(inventory.get('required_count', 0))}"
                ),
                (
                    "- Missing Core Artifacts: "
                    f"{_markdown_cell(_format_list(_list(inventory.get('missing_core_artifacts'))))}"
                ),
                (
                    "- Missing Required Artifacts: "
                    f"{_markdown_cell(_format_list(_list(inventory.get('missing_required_artifacts'))))}"
                ),
                "",
                "| Group | Required | Available | Written | Total |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for group_name in ("core", "test", "repair", "phase4"):
            rows = [_dict(item) for item in _list(groups.get(group_name))]
            lines.append(
                "| "
                f"{_markdown_cell(group_name)} | "
                f"{sum(1 for item in rows if bool(item.get('required_now', False)))} | "
                f"{sum(1 for item in rows if bool(item.get('available', False)))} | "
                f"{sum(1 for item in rows if bool(item.get('file_exists', False)))} | "
                f"{len(rows)} |"
            )
        lines.append("")
    lines.extend(
        [
        "## Repository Structure",
        "",
        f"- Analyzed Files: {_int(_dict(payload['repository_structure']).get('analyzed_file_count', 0))}",
        f"- Parse Errors: {_int(_dict(payload['repository_structure']).get('parse_error_count', 0))}",
        f"- Total LOC: {_int(_dict(payload['repository_structure']).get('total_loc', 0))}",
        f"- Functions: {_int(_dict(payload['repository_structure']).get('function_count', 0))}",
        f"- Classes: {_int(_dict(payload['repository_structure']).get('class_count', 0))}",
        f"- Imports: {_int(_dict(payload['repository_structure']).get('import_count', 0))}",
        f"- Call Sites: {_int(_dict(payload['repository_structure']).get('call_site_count', 0))}",
        f"- Max Cyclomatic Complexity: {_int(_dict(payload['repository_structure']).get('max_cyclomatic_complexity', 0))}",
        (
            "- Top Directories: "
            f"{_markdown_cell(_format_counts(_dict(_dict(payload['repository_structure']).get('directory_file_counts'))))}"
        ),
        (
            "- Top Imported Modules: "
            f"{_markdown_cell(_format_counts(_dict(_dict(payload['repository_structure']).get('import_module_counts'))))}"
        ),
        "",
        "### Package And Test Layout",
        "",
        (
            "- Package Roots: "
            f"{_markdown_cell(_format_list(_list(_dict(_dict(payload['repository_structure']).get('package_structure')).get('package_roots'))))}"
        ),
        (
            "- Src Layout Packages: "
            f"{_markdown_cell(_format_list(_list(_dict(_dict(payload['repository_structure']).get('package_structure')).get('src_layout_packages'))))}"
        ),
        (
            "- Recommended Target Prefix: "
            f"`{_markdown_cell(_dict(_dict(payload['repository_structure']).get('package_structure')).get('recommended_target_prefix') or 'none')}`"
        ),
        (
            "- Test Sources: "
            f"{_int(_dict(_dict(payload['repository_structure']).get('test_structure')).get('test_source_count', 0))}"
        ),
        (
            "- Test Directories: "
            f"{_markdown_cell(_format_list(_list(_dict(_dict(payload['repository_structure']).get('test_structure')).get('test_directories'))))}"
        ),
        (
            "- Test Framework Signals: "
            f"{_markdown_cell(_format_list(_list(_dict(_dict(payload['repository_structure']).get('test_structure')).get('test_framework_signals'))))}"
        ),
        (
            "- Test Command Candidate Runners: "
            f"{_markdown_cell(_format_counts(_dict(_dict(_dict(payload['repository_structure']).get('test_structure')).get('test_command_runner_counts'))))}"
        ),
        (
            "- Recommended Test Command: "
            f"`{_markdown_cell(_dict(_dict(payload['repository_structure']).get('test_structure')).get('recommended_test_command') or 'none')}`"
        ),
        (
            "- Project Config Files: "
            f"{_markdown_cell(_format_list(_list(_dict(_dict(payload['repository_structure']).get('project_config')).get('project_config_files'))))}"
        ),
        (
            "- Dependency Tool Signals: "
            f"{_markdown_cell(_format_list(_list(_dict(_dict(payload['repository_structure']).get('project_config')).get('dependency_tool_signals'))))}"
        ),
        "",
        "### Test Command Candidates",
        "",
        "| Rank | Runner | Command | Reason | Confidence |",
        "| ---: | --- | --- | --- | ---: |",
    ]
    )
    for item_value in _list(
        _dict(_dict(payload["repository_structure"]).get("test_structure")).get(
            "test_command_candidates"
        )
    ):
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_int(item.get('rank', 0))} | "
            f"{_markdown_cell(item.get('runner') or 'none')} | "
            f"`{_markdown_cell(item.get('command') or 'none')}` | "
            f"{_markdown_cell(item.get('reason') or 'none')} | "
            f"{_float(item.get('confidence', 0.0)):.4f} |"
        )
    if not _list(
        _dict(_dict(payload["repository_structure"]).get("test_structure")).get(
            "test_command_candidates"
        )
    ):
        lines.append("| 0 | none | `none` | none | 0.0000 |")
    lines.extend(
        [
        "## Complexity Hotspots",
        "",
        "| Function | File | Lines | Complexity | Calls |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    )
    hotspots = _list(
        _dict(payload["repository_structure"]).get("top_complexity_functions")
    )
    for item_value in hotspots:
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('name', ''))} | "
            f"{_markdown_cell(item.get('file_path', ''))} | "
            f"{_int(item.get('line_count', 0))} | "
            f"{_int(item.get('cyclomatic_complexity', 0))} | "
            f"{_int(item.get('call_count', 0))} |"
        )
    if not hotspots:
        lines.append("| none |  | 0 | 0 | 0 |")
    graph = _dict(payload.get("repo_graph"))
    program_graph = _dict(graph.get("program_graph"))
    lines.extend(
        [
            "",
            "## Repo Graph",
            "",
            f"- File Nodes: {_int(graph.get('file_node_count', 0))}",
            f"- Function Nodes: {_int(graph.get('function_node_count', 0))}",
            f"- File Dependency Edges: {_int(graph.get('file_dependency_edge_count', 0))}",
            f"- Function Call Edges: {_int(graph.get('function_call_edge_count', 0))}",
            f"- Unresolved Call Sites: {_int(graph.get('unresolved_call_site_count', 0))}",
            "",
            "### Program Graph",
            "",
            f"- Available: {str(bool(program_graph.get('available', False))).lower()}",
            f"- Reason: `{_markdown_cell(program_graph.get('reason') or 'none')}`",
            f"- Nodes: {_int(program_graph.get('node_count', 0))}",
            f"- Edges: {_int(program_graph.get('edge_count', 0))}",
            f"- Node Types: {_markdown_cell(_format_counts(_dict(program_graph.get('node_type_counts'))))}",
            f"- Edge Types: {_markdown_cell(_format_counts(_dict(program_graph.get('edge_type_counts'))))}",
            f"- Data-flow Edges: {_int(program_graph.get('data_flow_edge_count', 0))}",
            (
                "- Cross-function Data-flow Edges: "
                f"{_int(program_graph.get('cross_function_data_flow_edge_count', 0))}"
            ),
            f"- Control-flow Edges: {_int(program_graph.get('control_flow_edge_count', 0))}",
            f"- CFG Edges: {_int(program_graph.get('cfg_edge_count', 0))}",
            (
                "- Module Dependency Edges: "
                f"{_int(program_graph.get('module_dependency_edge_count', 0))}"
            ),
            "",
            "### Top Function Nodes",
            "",
            "| Function | File | In | Out | Complexity | Score |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    top_function_nodes = _list(graph.get("top_function_nodes"))
    for item_value in top_function_nodes:
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('name', ''))} | "
            f"{_markdown_cell(item.get('file_path', ''))} | "
            f"{_int(item.get('in_degree', 0))} | "
            f"{_int(item.get('out_degree', 0))} | "
            f"{_int(item.get('cyclomatic_complexity', 0))} | "
            f"{_float(item.get('score', 0.0)):.4f} |"
        )
    if not top_function_nodes:
        lines.append("| none |  | 0 | 0 | 0 | 0.0000 |")
    fault = _dict(payload.get("fault_localization"))
    lines.extend(
        [
            "",
            "## Fault Localization",
            "",
            f"- Mode: `{_markdown_cell(fault.get('mode', ''))}`",
            f"- Status: `{_markdown_cell(fault.get('status', ''))}`",
            f"- Reason: `{_markdown_cell(fault.get('reason', ''))}`",
            f"- Source: `{_markdown_cell(fault.get('source', ''))}`",
            f"- Ranked Functions: {_int(fault.get('ranked_function_count', 0))}",
            f"- Top Function: `{_markdown_cell(fault.get('top_function', ''))}`",
            "",
            (
                "| Rank | Function | File | StaticRuleScore | GraphScore | "
                "SourceRole | SourceRoleScore | SBFL | DynamicEvidence | FinalScore |"
            ),
            "| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    fault_rankings = _list(fault.get("rankings"))
    for item_value in fault_rankings:
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_int(item.get('rank', 0))} | "
            f"{_markdown_cell(item.get('function_name', ''))} | "
            f"{_markdown_cell(item.get('file_path', ''))} | "
            f"{_float(item.get('static_rule_score', 0.0)):.4f} | "
            f"{_float(item.get('graph_score', 0.0)):.4f} | "
            f"{_markdown_cell(item.get('source_role', ''))} | "
            f"{_float(item.get('source_role_score', 0.0)):.4f} | "
            f"{_float(item.get('sbfl_score', 0.0)):.4f} | "
            f"{_float(item.get('dynamic_test_evidence_score', 0.0)):.4f} | "
            f"{_float(item.get('final_score', 0.0)):.4f} |"
        )
    if not fault_rankings:
        lines.append(
            "| 0 | none |  | 0.0000 | 0.0000 | none | 0.0000 | "
            "0.0000 | 0.0000 | 0.0000 |"
        )
    localization = _dict(payload.get("static_fault_localization"))
    lines.extend(
        [
            "",
            "## Static Fault Localization",
            "",
            f"- Status: `{_markdown_cell(localization.get('status', ''))}`",
            f"- Reason: `{_markdown_cell(localization.get('reason', ''))}`",
            f"- Candidate Functions: {_int(localization.get('candidate_function_count', 0))}",
            f"- Ranked Functions: {_int(localization.get('ranked_function_count', 0))}",
            f"- Top Function: `{_markdown_cell(localization.get('top_function', ''))}`",
            "",
            (
                "| Rank | Function | File | StaticRuleScore | GraphScore | "
                "SourceRole | SourceRoleScore | FinalScore | Rules |"
            ),
            "| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- |",
        ]
    )
    rankings = _list(localization.get("rankings"))
    for item_value in rankings:
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_int(item.get('rank', 0))} | "
            f"{_markdown_cell(item.get('function_name', ''))} | "
            f"{_markdown_cell(item.get('file_path', ''))} | "
            f"{_float(item.get('static_rule_score', 0.0)):.4f} | "
            f"{_float(item.get('graph_score', 0.0)):.4f} | "
            f"{_markdown_cell(item.get('source_role', ''))} | "
            f"{_float(item.get('source_role_score', 0.0)):.4f} | "
            f"{_float(item.get('final_score', 0.0)):.4f} | "
            f"{_markdown_cell(', '.join(str(rule) for rule in _list(item.get('rule_ids'))))} |"
        )
    if not rankings:
        lines.append(
            "| 0 | none |  | 0.0000 | 0.0000 | none | 0.0000 | 0.0000 |  |"
        )
    lines.extend(
        [
            "",
            "## Patch Generation Audit",
            "",
            (
                "- Candidate Status: "
                f"`{_markdown_cell(payload.get('repository_test_patch_candidates_status') or 'none')}`/"
                f"`{_markdown_cell(payload.get('repository_test_patch_candidates_reason') or 'none')}`"
            ),
            f"- Candidate Count: {_int(payload.get('repository_test_patch_candidate_count', 0))}",
            (
                "- Patch Validation Args: "
                f"`{_markdown_cell(_list(payload.get('repository_test_patch_recommended_pytest_args')) or 'none')}`"
            ),
            (
                "- Patch Validation Args Source: "
                f"`{_markdown_cell(payload.get('repository_test_patch_recommended_pytest_args_source') or 'none')}`"
            ),
            (
                "- Patch Generation Mode: "
                f"`{_markdown_cell(payload.get('repository_patch_generation_mode') or 'rule')}`"
            ),
            (
                "- Generator Counts: "
                f"{_markdown_cell(_format_counts(_dict(payload.get('repository_patch_generator_counts'))))}"
            ),
            (
                "- Candidate Variant Filter: "
                f"{_markdown_cell(_format_candidate_variant_filter(_dict(payload.get('repository_patch_candidate_variant_filter'))))}"
            ),
            (
                "- LLM Generation: "
                f"`{_markdown_cell(payload.get('repository_llm_patch_generation_status') or 'disabled')}`/"
                f"`{_markdown_cell(payload.get('repository_llm_patch_generation_reason') or 'none')}`"
            ),
            (
                "- LLM Patch Config: "
                f"provider=`{_markdown_cell(payload.get('repository_llm_patch_provider') or 'none')}`, "
                f"model=`{_markdown_cell(payload.get('repository_llm_patch_model') or 'none')}`, "
                "api_key_present="
                f"{str(bool(payload.get('repository_llm_patch_api_key_present', False))).lower()}"
            ),
            (
                "- LLM Patch Fallback: "
                f"blocked={str(bool(payload.get('repository_llm_patch_blocked', False))).lower()}, "
                f"fallback_used={str(bool(payload.get('repository_llm_patch_generation_fallback_used', False))).lower()}, "
                f"reason=`{_markdown_cell(payload.get('repository_llm_patch_generation_fallback_reason') or payload.get('repository_llm_patch_blocker') or 'none')}`"
            ),
            (
                "- Safety Gate: "
                f"`{_markdown_cell(payload.get('repository_patch_safety_gate_status') or 'none')}` "
                f"(blocked={_int(payload.get('repository_patch_safety_gate_blocked_count', 0))})"
            ),
            (
                "- Patch Judge: "
                f"`{_markdown_cell(payload.get('repository_test_patch_judge_mode') or 'none')}`/"
                f"`{_markdown_cell(payload.get('repository_test_patch_judge_status') or 'disabled')}` "
                f"judged={_int(payload.get('repository_test_patch_judge_candidate_count', 0))}, "
                f"authority=`{_markdown_cell(payload.get('repository_test_patch_judge_authority') or 'sandbox_pytest_decides_success')}`"
            ),
        ]
    )
    lines.extend(
        [
        "",
        "## Artifacts",
        "",
        f"- Agent JSON: `{_markdown_cell(payload['agent_json'])}`",
        f"- Agent Markdown: `{_markdown_cell(payload['agent_markdown'])}`",
        (
            "- Execution Plan JSON: "
            f"`{_markdown_cell(payload['execution_plan_json'])}`"
        ),
        (
            "- Execution Plan Markdown: "
            f"`{_markdown_cell(payload['execution_plan_markdown'])}`"
        ),
        (
            "- Source Mining Markdown: "
            f"`{_markdown_cell(payload['source_mining_markdown'])}`"
        ),
        (
            "- Intelligence JSON: "
            f"`{_markdown_cell(payload['intelligence_json'])}`"
        ),
        (
            "- Intelligence Markdown: "
            f"`{_markdown_cell(payload['intelligence_markdown'])}`"
        ),
        (
            "- Agent Controller JSON: "
            f"`{_markdown_cell(payload['agent_controller_json'])}`"
        ),
        (
            "- Agent Controller Markdown: "
            f"`{_markdown_cell(payload['agent_controller_markdown'])}`"
        ),
        (
            "- Agent Action Registry JSON: "
            f"`{_markdown_cell(payload['agent_action_registry_json'])}`"
        ),
        (
            "- Agent Policy Trace JSON: "
            f"`{_markdown_cell(payload['agent_policy_trace_json'])}`"
        ),
        (
            "- Agent Invocation JSON: "
            f"`{_markdown_cell(payload['agent_invocation_json'])}`"
        ),
        (
            "- Agent Invocation Markdown: "
            f"`{_markdown_cell(payload['agent_invocation_markdown'])}`"
        ),
        (
            "- Agent Goal Readiness JSON: "
            f"`{_markdown_cell(payload['agent_goal_readiness_json'])}`"
        ),
        (
            "- Agent Goal Readiness Markdown: "
            f"`{_markdown_cell(payload['agent_goal_readiness_markdown'])}`"
        ),
        (
            "- Final Report JSON: "
            f"`{_markdown_cell(payload['final_report_json'])}`"
        ),
        (
            "- Final Report Markdown: "
            f"`{_markdown_cell(payload['final_report_markdown'])}`"
        ),
        (
            "- Repository Structure JSON: "
            f"`{_markdown_cell(payload['repository_structure_json'])}`"
        ),
        (
            "- Repository Test Discovery JSON: "
            f"`{_markdown_cell(payload['repository_test_discovery_json'])}`"
        ),
        (
            "- Repo Graph JSON: "
            f"`{_markdown_cell(payload['repo_graph_json'])}`"
        ),
        (
            "- Fault Localization JSON: "
            f"`{_markdown_cell(payload['fault_localization_json'])}`"
        ),
        (
            "- Analysis Readiness JSON: "
            f"`{_markdown_cell(payload['analysis_readiness_json'])}`"
        ),
        (
            "- Artifact Inventory JSON: "
            f"`{_markdown_cell(payload['artifact_inventory_json'])}`"
        ),
        (
            "- Artifact Inventory Markdown: "
            f"`{_markdown_cell(payload['artifact_inventory_markdown'])}`"
        ),
        (
            "- Phase 4 Search Evaluation JSON: "
            f"`{_markdown_cell(payload['phase4_search_evaluation_json'])}`"
        ),
        (
            "- Phase 4 Search Evaluation Markdown: "
            f"`{_markdown_cell(payload['phase4_search_evaluation_markdown'])}`"
        ),
        (
            "- Phase 4 Search Evaluation Execution JSON: "
            f"`{_markdown_cell(payload['phase4_search_evaluation_execution_json'] or 'none')}`"
        ),
        (
            "- Phase 4 Search Evaluation Execution Markdown: "
            f"`{_markdown_cell(payload['phase4_search_evaluation_execution_markdown'] or 'none')}`"
        ),
        (
            "- Repository Test Environment JSON: "
            f"`{_markdown_cell(payload['repository_test_environment_json'] or 'none')}`"
        ),
        (
            "- Repository Test Environment Repair Plan JSON: "
            f"`{_markdown_cell(payload['repository_test_environment_repair_plan_json'] or 'none')}`"
        ),
        (
            "- Repository Test Execution Plan JSON: "
            f"`{_markdown_cell(payload['repository_test_execution_plan_json'] or 'none')}`"
        ),
        (
            "- Repository Test Execution Result JSON: "
            f"`{_markdown_cell(payload['repository_test_execution_result_json'] or 'none')}`"
        ),
        (
            "- Repository Test Dynamic Evidence JSON: "
            f"`{_markdown_cell(payload['repository_test_dynamic_evidence_json'] or 'none')}`"
        ),
        (
            "- Repository Test Regression Guard JSON: "
            f"`{_markdown_cell(payload['repository_test_regression_guard_json'] or 'none')}`"
        ),
        (
            "- Repository Test Patch Candidates JSON: "
            f"`{_markdown_cell(payload['repository_test_patch_candidates_json'] or 'none')}`"
        ),
        (
            "- Repository Test Patch Validation JSON: "
            f"`{_markdown_cell(payload['repository_test_patch_validation_json'] or 'none')}`"
        ),
        (
            "- Reflection Trace JSON: "
            f"`{_markdown_cell(payload['reflection_trace_json'] or 'none')}`"
        ),
        (
            "- Reflection Trace Markdown: "
            f"`{_markdown_cell(payload['reflection_trace_markdown'] or 'none')}`"
        ),
        (
            "- Agent Decision Timeline JSON: "
            f"`{_markdown_cell(payload['agent_decision_timeline_json'] or 'none')}`"
        ),
        (
            "- Agent Decision Timeline Markdown: "
            f"`{_markdown_cell(payload['agent_decision_timeline_markdown'] or 'none')}`"
        ),
        ]
    )
    return "\n".join(lines) + "\n"


def write_github_repo_intelligence_artifacts(
    report: GitHubRepoAgentReport,
    payload: dict[str, Any] | None = None,
) -> dict[str, str]:
    summary = payload if payload is not None else github_repo_intelligence_summary(report)
    root = Path(report.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "github_repo_intelligence.json"
    markdown_path = root / "github_repo_intelligence.md"
    controller_paths: dict[str, str] = {}
    component_paths: dict[str, str] = {}
    environment_repair_paths = _ensure_repository_test_environment_repair_plan_artifacts(
        summary,
        root,
    )
    for _ in range(8):
        _refresh_agent_goal_readiness_and_controller(summary)
        json_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        markdown_path.write_text(
            _render_github_repo_intelligence_payload(summary),
            encoding="utf-8",
        )
        controller_paths = write_agent_controller_artifacts(
            _dict(summary.get("agent_controller")),
            root,
        )
        component_paths = _write_intelligence_component_artifacts(summary, root)
        audited_inventory = _artifact_inventory_summary(summary, check_files=True)
        if audited_inventory == _dict(summary.get("artifact_inventory")):
            audited_acceptance = _acceptance_gate_summary(summary)
            if audited_acceptance == _dict(summary.get("acceptance_gate")):
                break
            summary["acceptance_gate"] = audited_acceptance
            _refresh_agent_goal_readiness_and_controller(summary)
            continue
        summary["artifact_inventory"] = audited_inventory
        summary.update(_github_repo_intelligence_status_summary(summary))
        summary["agent_answers"] = _agent_answers_summary(summary)
        summary["acceptance_gate"] = _acceptance_gate_summary(summary)
        _refresh_agent_goal_readiness_and_controller(summary)
    else:
        final_inventory = _artifact_inventory_summary(summary, check_files=True)
        if final_inventory != _dict(summary.get("artifact_inventory")):
            summary["artifact_inventory"] = final_inventory
            summary.update(_github_repo_intelligence_status_summary(summary))
            summary["agent_answers"] = _agent_answers_summary(summary)
            summary["acceptance_gate"] = _acceptance_gate_summary(summary)
            _refresh_agent_goal_readiness_and_controller(summary)
        elif not _dict(summary.get("acceptance_gate")):
            summary["acceptance_gate"] = _acceptance_gate_summary(summary)
            _refresh_agent_goal_readiness_and_controller(summary)
        else:
            _refresh_agent_goal_readiness_and_controller(summary)
        json_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        markdown_path.write_text(
            _render_github_repo_intelligence_payload(summary),
            encoding="utf-8",
        )
        controller_paths = write_agent_controller_artifacts(
            _dict(summary.get("agent_controller")),
            root,
        )
        component_paths = _write_intelligence_component_artifacts(summary, root)
        return {
            "github_repo_intelligence_json": str(json_path),
            "github_repo_intelligence_markdown": str(markdown_path),
            **controller_paths,
            **component_paths,
            **environment_repair_paths,
        }
    return {
        "github_repo_intelligence_json": str(json_path),
        "github_repo_intelligence_markdown": str(markdown_path),
        **controller_paths,
        **component_paths,
        **environment_repair_paths,
    }


def _ensure_repository_test_environment_repair_plan_artifacts(
    summary: dict[str, Any],
    output_dir: Path,
) -> dict[str, str]:
    if not _environment_repair_required(summary):
        return {}
    existing_json = str(
        summary.get("repository_test_environment_repair_plan_json") or ""
    )
    existing_markdown = str(
        summary.get("repository_test_environment_repair_plan_markdown") or ""
    )
    if existing_json and existing_markdown:
        if Path(existing_json).is_file() and Path(existing_markdown).is_file():
            return {
                "repository_test_environment_repair_plan_json": existing_json,
                "repository_test_environment_repair_plan_markdown": existing_markdown,
            }
    controller = _dict(summary.get("agent_controller"))
    selected_action = _dict(controller.get("selected_action"))
    plan = _build_repository_test_environment_repair_plan(
        summary,
        controller=controller,
        selected_action=selected_action,
        snapshot_paths={},
    )
    paths = _write_repository_test_environment_repair_plan_artifacts(
        plan,
        output_dir,
    )
    summary["repository_test_environment_repair_plan"] = plan
    summary.update(
        {
            "repository_test_environment_repair_plan_json": paths[
                "repository_test_environment_repair_plan_json"
            ],
            "repository_test_environment_repair_plan_markdown": paths[
                "repository_test_environment_repair_plan_markdown"
            ],
            "repository_test_environment_repair_plan_status": str(
                plan.get("status") or ""
            ),
            "repository_test_environment_repair_plan_reason": str(
                plan.get("reason") or ""
            ),
            "repository_test_environment_repair_plan_blocker": str(
                plan.get("blocker") or ""
            ),
            "repository_test_environment_repair_plan_recommended_install_command": str(
                plan.get("recommended_install_command") or ""
            ),
            "repository_test_environment_repair_plan_missing_dependency_modules": [
                str(item)
                for item in _list(plan.get("missing_dependency_modules"))
            ],
            "repository_test_environment_repair_plan_missing_dependency_install_hint": str(
                plan.get("missing_dependency_install_hint") or ""
            ),
        }
    )
    summary["agent_controller"] = build_agent_controller_plan(summary)
    summary["artifact_inventory"] = _artifact_inventory_summary(summary)
    summary["agent_answers"] = _agent_answers_summary(summary)
    summary["acceptance_gate"] = _acceptance_gate_summary(summary)
    _refresh_agent_goal_readiness_and_controller(summary)
    return paths


def _write_intelligence_component_artifacts(
    summary: dict[str, Any],
    output_dir: Path,
) -> dict[str, str]:
    components = {
        "agent_invocation": _dict(summary.get("agent_invocation")),
        "agent_goal_readiness": _dict(summary.get("agent_goal_readiness")),
        "repository_structure": _dict(summary.get("repository_structure")),
        "repository_test_discovery": _dict(summary.get("repository_test_discovery")),
        "repo_graph": _dict(summary.get("repo_graph")),
        "fault_localization": _dict(summary.get("fault_localization")),
        "analysis_readiness": _dict(summary.get("analysis_readiness")),
        "agent_decision_timeline": _dict(summary.get("agent_decision_timeline")),
        "final_report": _dict(summary.get("final_report")),
        "phase4_search_evaluation": _dict(
            summary.get("phase4_search_evaluation")
        ),
        "artifact_inventory": _dict(summary.get("artifact_inventory")),
    }
    paths: dict[str, str] = {}
    for name, payload in components.items():
        json_path = output_dir / f"{name}.json"
        markdown_path = output_dir / f"{name}.md"
        json_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        markdown = (
            _render_phase4_search_evaluation_markdown(payload)
            if name == "phase4_search_evaluation"
            else _render_agent_decision_timeline_markdown(payload)
            if name == "agent_decision_timeline"
            else _render_component_markdown(name, payload)
        )
        markdown_path.write_text(markdown, encoding="utf-8")
        paths[f"{name}_json"] = str(json_path)
        paths[f"{name}_markdown"] = str(markdown_path)
    return paths


def _render_component_markdown(name: str, payload: dict[str, Any]) -> str:
    title = name.replace("_", " ").title()
    lines = [f"# {title}", "", "| Field | Value |", "| --- | --- |"]
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False)
            if len(rendered) > 240:
                rendered = rendered[:237] + "..."
        else:
            rendered = str(value)
        lines.append(f"| {_markdown_cell(key)} | {_markdown_cell(rendered)} |")
    if not payload:
        lines.append("| none | none |")
    return "\n".join(lines) + "\n"


def _render_agent_decision_timeline_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Agent Decision Timeline",
        "",
        f"- Status: `{_markdown_cell(payload.get('status') or 'none')}`",
        f"- Reason: `{_markdown_cell(payload.get('reason') or 'none')}`",
        f"- Source: `{_markdown_cell(payload.get('source') or 'none')}`",
        f"- Loop: `{_markdown_cell(' -> '.join(str(item) for item in _list(payload.get('loop'))))}`",
        (
            "- Steps: "
            f"{_int(payload.get('complete_step_count', 0))}/"
            f"{_int(payload.get('step_count', 0))} complete"
        ),
        f"- Executed Steps: {_int(payload.get('executed_step_count', 0))}",
        f"- Blocked/Stopped Steps: {_int(payload.get('blocked_step_count', 0))}",
        "",
        "| Iteration | Observe | Plan | Act | Verify | Reflect | Replan | Complete |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for step_value in _list(payload.get("steps")):
        step = _dict(step_value)
        observe = _dict(step.get("observe"))
        plan = _dict(step.get("plan"))
        act = _dict(step.get("act"))
        verify = _dict(step.get("verify"))
        reflect = _dict(step.get("reflect"))
        replan = _dict(step.get("replan"))
        observe_cell = (
            f"{observe.get('stage') or 'none'}"
            f"/{observe.get('blocker') or 'none'}"
            f"/goal={observe.get('agent_goal_readiness_status') or 'none'}"
        )
        plan_cell = (
            f"{plan.get('selected_action') or 'none'}"
            f" ({plan.get('phase') or 'none'})"
        )
        act_cell = (
            f"{act.get('status') or 'none'}"
            f"/executed={str(bool(act.get('executed', False))).lower()}"
            f"/stop={act.get('stop_category') or 'none'}"
        )
        verify_cell = (
            f"{verify.get('outcome') or verify.get('status') or 'none'}"
            f"/progress={str(bool(verify.get('progress', False))).lower()}"
            f"/goal={verify.get('agent_goal_readiness_status') or 'none'}"
        )
        reflect_cell = (
            f"{reflect.get('status') or 'none'}"
            f"/{reflect.get('failure_type') or 'none'}"
        )
        replan_cell = (
            f"{replan.get('policy') or 'none'}"
            f" -> {replan.get('next_action') or replan.get('stop_reason') or 'none'}"
        )
        lines.append(
            "| "
            f"{_int(step.get('iteration', 0))} | "
            f"{_markdown_cell(observe_cell)} | "
            f"{_markdown_cell(plan_cell)} | "
            f"{_markdown_cell(act_cell)} | "
            f"{_markdown_cell(verify_cell)} | "
            f"{_markdown_cell(reflect_cell)} | "
            f"{_markdown_cell(replan_cell)} | "
            f"{str(bool(step.get('complete', False))).lower()} |"
        )
    if not _list(payload.get("steps")):
        lines.append("| 0 | none | none | none | none | none | none | false |")
    return "\n".join(lines) + "\n"


def _render_phase4_search_evaluation_markdown(payload: dict[str, Any]) -> str:
    search_budget = _dict(payload.get("search_budget"))
    evidence = _dict(payload.get("evidence_artifacts"))
    execution = _dict(payload.get("execution"))
    lines = [
        "# Phase 4 Search Evaluation",
        "",
        f"- Status: `{_markdown_cell(payload.get('status') or 'unknown')}`",
        f"- Reason: `{_markdown_cell(payload.get('reason') or '')}`",
        (
            "- Ready For Phase 4: "
            f"{str(bool(payload.get('ready_for_phase4', False))).lower()}"
        ),
        (
            "- Controller Action: "
            f"`{_markdown_cell(payload.get('controller_action_id') or 'none')}`/"
            f"`{_markdown_cell(payload.get('controller_action_phase') or 'none')}`"
        ),
        (
            "- Repair: "
            f"ready={str(bool(payload.get('repair_ready', False))).lower()}, "
            f"scope=`{_markdown_cell(payload.get('repair_validation_scope') or 'none')}`"
        ),
        (
            "- Baseline Regression Caveat: "
            f"{str(bool(payload.get('baseline_regression_caveat', False))).lower()}"
        ),
        (
            "- Full-Suite Green Claim Allowed: "
            f"{str(bool(payload.get('full_suite_green_claim_allowed', False))).lower()}"
        ),
        (
            "- Lightweight Evaluation Executed: "
            f"{str(bool(execution.get('executed', False))).lower()}"
        ),
        f"- Execution Status: `{_markdown_cell(execution.get('status') or 'not_executed')}`",
        f"- Execution Reason: `{_markdown_cell(execution.get('reason') or 'none')}`",
        "",
        "## Search Budget Proxy",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Candidate Count | {_int(search_budget.get('candidate_count', 0))} |",
        f"| Executed Count | {_int(search_budget.get('executed_count', 0))} |",
        f"| Success Count | {_int(search_budget.get('success_count', 0))} |",
        f"| Success Rate | {_float(search_budget.get('success_rate', 0.0)):.4f} |",
        (
            "| First Success Rank | "
            f"{_markdown_cell(search_budget.get('first_success_rank') or 'none')} |"
        ),
        (
            "| Failures Before First Success | "
            f"{_int(search_budget.get('failures_before_first_success', 0))} |"
        ),
        f"| Max Depth Executed | {_int(search_budget.get('max_depth_executed', 0))} |",
        "",
        "## Evaluation Gates",
        "",
        "| Gate | Passed | Evidence |",
        "| --- | --- | --- |",
    ]
    for item_value in _list(payload.get("evaluation_gates")):
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('name'))} | "
            f"{str(bool(item.get('passed', False))).lower()} | "
            f"{_markdown_cell(item.get('evidence'))} |"
        )
    lines.extend(["", "## Evidence Artifacts", "", "| Artifact | Path |", "| --- | --- |"])
    for key, value in evidence.items():
        lines.append(f"| {_markdown_cell(key)} | `{_markdown_cell(value or 'none')}` |")
    lines.extend(["", "## Recommended Commands", ""])
    for command in _list(payload.get("recommended_commands")):
        lines.append(f"- `{_markdown_cell(command)}`")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- Inspect generated intelligence artifacts.")
    return "\n".join(lines) + "\n"


def _repository_profile_payload(report: GitHubRepoAgentReport) -> dict[str, Any]:
    payload = _read_json(str(report.output_paths.get("repository_profile_json") or ""))
    if payload:
        return payload
    return _dict(_dict(report.onboarding_report).get("repository_profile"))


def _unique_directories(paths: list[Any]) -> list[str]:
    directories = {
        _directory(str(path))
        for path in paths
        if str(path)
    }
    return sorted(directories)


def _repository_structure_summary(report: GitHubRepoAgentReport) -> dict[str, Any]:
    source_import_path = str(report.output_paths.get("source_import_json") or "")
    source_import = _read_json(source_import_path)
    repository_profile = _repository_profile_payload(report)
    source_entries = _list(source_import.get("source_entries")) or _list(
        _dict(source_import.get("sources_payload")).get("sources")
    )
    cache_dir_text = str(report.output_paths.get("source_cache_dir") or "")
    cache_dir = Path(cache_dir_text) if cache_dir_text else None
    analyzer = ASTAnalyzer()
    directory_file_counts: dict[str, int] = {}
    import_module_counts: dict[str, int] = {}
    file_summaries: list[dict[str, Any]] = []
    graph_files: list[dict[str, Any]] = []
    hotspots: list[dict[str, Any]] = []
    parse_errors: list[dict[str, str]] = []
    source_read_errors: list[dict[str, str]] = []
    total_loc = 0
    function_count = 0
    class_count = 0
    import_count = 0
    call_site_count = 0

    for entry_value in source_entries:
        entry = _dict(entry_value)
        target_path = str(
            entry.get("target_path") or entry.get("source_path") or ""
        )
        if not target_path.endswith(".py"):
            continue
        source_text = _read_cached_source_text(entry, cache_dir)
        if source_text is None:
            source_read_errors.append(
                {
                    "file_path": target_path,
                    "reason": "source_not_available_in_cache",
                }
            )
            continue
        try:
            analysis = analyzer.analyze_file(target_path, source_text)
            tree = ast.parse(source_text, filename=target_path)
        except SyntaxError as exc:
            parse_errors.append(
                {
                    "file_path": target_path,
                    "reason": f"SyntaxError:{exc.lineno or 0}",
                }
            )
            continue
        except Exception as exc:  # pragma: no cover - defensive report boundary
            parse_errors.append(
                {
                    "file_path": target_path,
                    "reason": f"{type(exc).__name__}:{exc}",
                }
            )
            continue

        lines = source_text.splitlines()
        loc = len(lines)
        file_functions = len(analysis.functions)
        file_classes = len(analysis.classes)
        file_imports = len(analysis.imports)
        file_calls = len(analysis.calls)
        function_metrics = _function_complexity_metrics(tree, target_path)
        max_complexity = max(
            (_int(item.get("cyclomatic_complexity", 0)) for item in function_metrics),
            default=0,
        )
        total_loc += loc
        function_count += file_functions
        class_count += file_classes
        import_count += file_imports
        call_site_count += file_calls
        _increment(directory_file_counts, _directory(target_path))
        for import_info in analysis.imports:
            for module in _import_roots(import_info):
                _increment(import_module_counts, module)
        hotspots.extend(function_metrics)
        graph_files.append(
            {
                "file_path": target_path,
                "analysis": analysis,
                "function_metrics": function_metrics,
            }
        )
        file_summaries.append(
            {
                "file_path": target_path,
                "directory": _directory(target_path),
                "loc": loc,
                "function_count": file_functions,
                "class_count": file_classes,
                "import_count": file_imports,
                "call_site_count": file_calls,
                "max_cyclomatic_complexity": max_complexity,
            }
        )

    top_hotspots = sorted(
        hotspots,
        key=lambda item: (
            -_int(item.get("cyclomatic_complexity", 0)),
            -_int(item.get("line_count", 0)),
            str(item.get("file_path") or ""),
            str(item.get("name") or ""),
        ),
    )[:10]
    test_source_paths = [
        str(item) for item in _list(repository_profile.get("test_source_paths"))
    ]
    package_roots = [
        str(item) for item in _list(repository_profile.get("package_roots"))
    ]
    src_layout_packages = [
        str(item) for item in _list(repository_profile.get("src_layout_packages"))
    ]
    project_config_files = [
        str(item) for item in _list(repository_profile.get("project_config_files"))
    ]
    test_framework_signals = [
        str(item) for item in _list(repository_profile.get("test_framework_signals"))
    ]
    test_command_candidates = [
        _test_command_candidate_summary(item)
        for item in _list(repository_profile.get("test_command_candidates"))
    ]
    test_command_runner_counts: dict[str, int] = {}
    for candidate in test_command_candidates:
        _increment(test_command_runner_counts, str(candidate.get("runner") or "unknown"))
    framework_signals = [
        str(item) for item in _list(repository_profile.get("framework_signals"))
    ]
    dependency_manager_profile = _dict(
        repository_profile.get("dependency_manager_profile")
    )
    dependency_tool_signals = [
        str(item) for item in _list(repository_profile.get("dependency_tool_signals"))
    ]
    return {
        "source_entry_count": len(source_entries),
        "analyzed_file_count": len(file_summaries),
        "source_read_error_count": len(source_read_errors),
        "parse_error_count": len(parse_errors),
        "total_loc": total_loc,
        "function_count": function_count,
        "class_count": class_count,
        "import_count": import_count,
        "call_site_count": call_site_count,
        "max_cyclomatic_complexity": max(
            (
                _int(item.get("cyclomatic_complexity", 0))
                for item in top_hotspots
            ),
            default=0,
        ),
        "directory_file_counts": _top_counts(directory_file_counts),
        "import_module_counts": _top_counts(import_module_counts),
        "package_structure": {
            "package_init_count": _int(
                repository_profile.get("package_init_count", 0)
            ),
            "package_roots": package_roots,
            "src_layout_packages": src_layout_packages,
            "recommended_target_prefix": str(
                repository_profile.get("recommended_target_prefix") or ""
            ),
            "layout_hints": [
                str(item) for item in _list(repository_profile.get("layout_hints"))
            ],
        },
        "test_structure": {
            "test_source_count": _int(
                repository_profile.get("test_source_count", len(test_source_paths))
            ),
            "test_source_paths": test_source_paths[:20],
            "test_directories": _unique_directories(test_source_paths),
            "test_framework_signals": test_framework_signals,
            "recommended_test_command": str(
                repository_profile.get("recommended_test_command") or ""
            ),
            "test_command_candidates": test_command_candidates[:12],
            "test_command_runner_counts": dict(sorted(test_command_runner_counts.items())),
            "test_command_runner_kind_count": len(test_command_runner_counts),
            "test_command_candidate_count": _int(
                repository_profile.get("test_command_candidate_count", 0)
            ),
        },
        "project_config": {
            "project_config_count": _int(
                repository_profile.get(
                    "project_config_count",
                    len(project_config_files),
                )
            ),
            "project_config_files": project_config_files[:20],
            "framework_signals": framework_signals,
            "dependency_tool_signals": dependency_tool_signals,
            "dependency_manager_profile": dependency_manager_profile,
            "dependency_file_count": _int(
                repository_profile.get("dependency_file_count", 0)
            ),
            "packaging_file_count": _int(
                repository_profile.get("packaging_file_count", 0)
            ),
        },
        "repo_graph": _repo_graph_summary(graph_files, file_summaries),
        "top_complexity_functions": top_hotspots,
        "file_summaries": sorted(
            file_summaries,
            key=lambda item: (
                -_int(item.get("max_cyclomatic_complexity", 0)),
                str(item.get("file_path") or ""),
            ),
        )[:20],
        "source_read_errors": source_read_errors[:10],
        "parse_errors": parse_errors[:10],
    }


def _repository_test_discovery_summary(
    repository_structure: dict[str, Any],
) -> dict[str, Any]:
    structure = _dict(repository_structure)
    test_structure = _dict(structure.get("test_structure"))
    project_config = _dict(structure.get("project_config"))
    package_structure = _dict(structure.get("package_structure"))
    test_sources = [
        str(item) for item in _list(test_structure.get("test_source_paths"))
    ]
    test_directories = [
        str(item) for item in _list(test_structure.get("test_directories"))
    ]
    framework_signals = [
        str(item) for item in _list(test_structure.get("test_framework_signals"))
    ]
    candidates = [
        _test_command_candidate_summary(item)
        for item in _list(test_structure.get("test_command_candidates"))
    ]
    runner_counts: dict[str, int] = {}
    for candidate in candidates:
        _increment(runner_counts, str(candidate.get("runner") or "unknown"))
    config_files = [
        str(item) for item in _list(project_config.get("project_config_files"))
    ]
    dependency_tool_signals = [
        str(item) for item in _list(project_config.get("dependency_tool_signals"))
    ]
    test_source_count = _int(
        test_structure.get("test_source_count", len(test_sources))
    )
    candidate_count = _int(
        test_structure.get("test_command_candidate_count", len(candidates))
    )
    has_tests = bool(test_source_count > 0 or test_sources)
    has_command = bool(str(test_structure.get("recommended_test_command") or ""))
    if has_tests:
        status = "pass"
        reason = "test_sources_discovered"
        blocker = ""
        next_action = "diagnose_environment"
    elif candidate_count > 0 or has_command:
        status = "warning"
        reason = "test_command_candidates_without_test_sources"
        blocker = "oracle:no_test_sources"
        next_action = "diagnose_environment"
    else:
        status = "blocked"
        reason = "no_tests_discovered"
        blocker = "oracle:no_tests"
        next_action = "emit_blocker_report"
    return {
        "status": status,
        "reason": reason,
        "blocker": blocker,
        "next_action": next_action,
        "test_source_count": test_source_count,
        "test_source_paths": test_sources[:20],
        "test_directories": test_directories,
        "test_framework_signals": framework_signals,
        "recommended_test_command": str(
            test_structure.get("recommended_test_command") or ""
        ),
        "test_command_candidate_count": candidate_count,
        "test_command_candidates": candidates[:12],
        "test_command_runner_counts": dict(sorted(runner_counts.items())),
        "test_command_runner_kind_count": len(runner_counts),
        "project_config_files": config_files[:20],
        "dependency_tool_signals": dependency_tool_signals,
        "package_roots": [
            str(item) for item in _list(package_structure.get("package_roots"))
        ],
        "src_layout_packages": [
            str(item)
            for item in _list(package_structure.get("src_layout_packages"))
        ],
        "recommended_target_prefix": str(
            package_structure.get("recommended_target_prefix") or ""
        ),
        "pytest_configured": "pytest" in framework_signals
        or "pytest.ini" in config_files
        or "pyproject.toml" in config_files,
        "tox_or_nox_configured": bool({"tox.ini", "noxfile.py"} & set(config_files)),
    }


def _test_command_candidate_summary(value: Any) -> dict[str, Any]:
    candidate = _dict(value)
    return {
        "rank": _int(candidate.get("rank", 0)),
        "command": str(candidate.get("command") or ""),
        "runner": str(candidate.get("runner") or ""),
        "reason": str(candidate.get("reason") or ""),
        "confidence": _float(candidate.get("confidence", 0.0)),
        "scope": str(candidate.get("scope") or ""),
        "evidence": [str(item) for item in _list(candidate.get("evidence"))[:8]],
        "recommended": bool(candidate.get("recommended", False)),
    }


def _read_cached_source_text(
    source_entry: dict[str, Any],
    cache_dir: Path | None,
) -> str | None:
    if isinstance(source_entry.get("content"), str):
        return str(source_entry["content"])
    if cache_dir is not None:
        try:
            cache_path = _source_cache_path(cache_dir, source_from_dict(source_entry))
        except (KeyError, ValueError):
            cache_path = None
        if cache_path is not None and cache_path.exists():
            return cache_path.read_text(encoding="utf-8")
    raw_url = str(source_entry.get("raw_url") or "")
    if raw_url and not raw_url.startswith(("http://", "https://", "file://")):
        raw_path = Path(raw_url)
        if raw_path.exists():
            return raw_path.read_text(encoding="utf-8")
    return None


def _repo_graph_summary(
    graph_files: list[dict[str, Any]],
    file_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    module_to_file = {
        _module_name_from_path(str(item.get("file_path") or "")): str(
            item.get("file_path") or ""
        )
        for item in graph_files
        if str(item.get("file_path") or "")
    }
    module_to_file = {module: path for module, path in module_to_file.items() if module}
    function_nodes: dict[str, dict[str, Any]] = {}
    file_function_names: dict[str, dict[str, str]] = {}
    repo_function_names: dict[str, list[str]] = {}
    for item in graph_files:
        file_path = str(item.get("file_path") or "")
        analysis = item.get("analysis")
        metrics_by_name = {
            str(metric.get("name") or ""): metric
            for metric in _list(item.get("function_metrics"))
        }
        file_function_names[file_path] = {}
        for function in getattr(analysis, "functions", []):
            qualified_name = str(function.metadata.get("qualified_name") or function.name)
            metric = _dict(metrics_by_name.get(qualified_name))
            node_id = str(function.id)
            node = {
                "id": node_id,
                "name": qualified_name,
                "file_path": file_path,
                "start_line": _int(function.start_line),
                "end_line": _int(function.end_line),
                "line_count": max(
                    1,
                    _int(function.end_line) - _int(function.start_line) + 1,
                ),
                "cyclomatic_complexity": _int(
                    metric.get("cyclomatic_complexity", 1)
                ),
                "call_count": _int(metric.get("call_count", 0)),
            }
            function_nodes[node_id] = node
            for alias in {function.name, qualified_name, qualified_name.split(".")[-1]}:
                if alias:
                    file_function_names[file_path][alias] = node_id
                    repo_function_names.setdefault(alias, []).append(node_id)

    file_dependency_edges: list[dict[str, Any]] = []
    file_edge_keys: set[tuple[str, str, str]] = set()
    function_call_edges: list[dict[str, Any]] = []
    function_edge_keys: set[tuple[str, str, int, str]] = set()
    unresolved_call_site_count = 0
    file_in_degree: dict[str, int] = {}
    file_out_degree: dict[str, int] = {}
    function_in_degree: dict[str, int] = {}
    function_out_degree: dict[str, int] = {}
    import_aliases_by_file = _import_aliases_by_file(graph_files, module_to_file)

    for item in graph_files:
        file_path = str(item.get("file_path") or "")
        analysis = item.get("analysis")
        current_module = _module_name_from_path(file_path)
        for import_info in getattr(analysis, "imports", []):
            for target in _resolve_import_target_files(
                import_info,
                current_module=current_module,
                module_to_file=module_to_file,
            ):
                if target == file_path:
                    continue
                module_name = str(getattr(import_info, "module", "") or "")
                key = (file_path, target, module_name)
                if key in file_edge_keys:
                    continue
                file_edge_keys.add(key)
                file_dependency_edges.append(
                    {
                        "source_file": file_path,
                        "target_file": target,
                        "import_module": module_name,
                        "line": _int(getattr(import_info, "line", 0)),
                    }
                )
                _increment(file_out_degree, file_path)
                _increment(file_in_degree, target)

        for call in getattr(analysis, "calls", []):
            target_function_id = _resolve_call_target(
                call,
                file_path=file_path,
                file_function_names=file_function_names,
                repo_function_names=repo_function_names,
                import_aliases=import_aliases_by_file.get(file_path, {}),
            )
            if not target_function_id:
                unresolved_call_site_count += 1
                continue
            if target_function_id == str(call.caller_id):
                continue
            edge_key = (
                str(call.caller_id),
                target_function_id,
                _int(call.line),
                str(call.callee),
            )
            if edge_key in function_edge_keys:
                continue
            function_edge_keys.add(edge_key)
            target_node = _dict(function_nodes.get(target_function_id))
            function_call_edges.append(
                {
                    "caller_id": str(call.caller_id),
                    "caller_name": str(call.caller_name),
                    "caller_file": file_path,
                    "callee_id": target_function_id,
                    "callee_name": str(target_node.get("name") or ""),
                    "callee_file": str(target_node.get("file_path") or ""),
                    "callee_expr": str(call.callee),
                    "line": _int(call.line),
                }
            )
            _increment(function_out_degree, str(call.caller_id))
            _increment(function_in_degree, target_function_id)

    file_nodes = _rank_file_nodes(
        file_summaries,
        in_degree=file_in_degree,
        out_degree=file_out_degree,
    )
    function_rank = _rank_function_nodes(
        function_nodes,
        in_degree=function_in_degree,
        out_degree=function_out_degree,
    )
    program_graph = _program_graph_summary(graph_files)
    return {
        "file_node_count": len(file_summaries),
        "function_node_count": len(function_nodes),
        "file_dependency_edge_count": len(file_dependency_edges),
        "function_call_edge_count": len(function_call_edges),
        "unresolved_call_site_count": unresolved_call_site_count,
        "file_dependency_edges_preview": file_dependency_edges[:20],
        "function_call_edges_preview": function_call_edges[:20],
        "top_file_nodes": file_nodes[:10],
        "top_function_nodes": function_rank[:10],
        "program_graph": program_graph,
        "function_nodes": function_rank,
    }


def _program_graph_summary(graph_files: list[dict[str, Any]]) -> dict[str, Any]:
    analyses = [
        item.get("analysis")
        for item in graph_files
        if item.get("analysis") is not None
    ]
    if not analyses:
        return {
            "available": False,
            "reason": "no_file_analyses",
            "node_count": 0,
            "edge_count": 0,
            "node_type_counts": {},
            "edge_type_counts": {},
        }
    try:
        parsed = RepoParseResult(root_path=".", files=analyses)
        call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
        graph = build_program_graph(parsed, call_graph)
    except Exception as exc:  # pragma: no cover - report boundary
        return {
            "available": False,
            "reason": f"{type(exc).__name__}:{exc}",
            "node_count": 0,
            "edge_count": 0,
            "node_type_counts": {},
            "edge_type_counts": {},
        }
    node_type_counts: dict[str, int] = {}
    edge_type_counts: dict[str, int] = {}
    for node in graph.nodes.values():
        _increment(node_type_counts, str(node.get("type") or "unknown"))
    for edge in graph.edges:
        _increment(edge_type_counts, str(edge.get("type") or "unknown"))
    data_flow_types = {
        "data_depends_on",
        "key_flows_to_subscript",
        "arg_flows_to_param",
        "return_flows_to_var",
    }
    cfg_types = {
        "cfg_entry",
        "cfg_next",
        "cfg_branch",
        "cfg_loop",
        "cfg_exception",
    }
    return {
        "available": True,
        "reason": "built_from_repo_parse_result",
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "function_count": len(graph.functions),
        "node_type_counts": _top_counts(node_type_counts, limit=20),
        "edge_type_counts": _top_counts(edge_type_counts, limit=30),
        "call_edge_count": _int(edge_type_counts.get("calls", 0)),
        "import_edge_count": _int(edge_type_counts.get("imports", 0)),
        "tested_by_edge_count": _int(edge_type_counts.get("tested_by", 0)),
        "module_dependency_edge_count": _int(
            edge_type_counts.get("module_depends_on", 0)
        ),
        "data_flow_edge_count": sum(
            _int(edge_type_counts.get(edge_type, 0))
            for edge_type in data_flow_types
        ),
        "cross_function_data_flow_edge_count": (
            _int(edge_type_counts.get("arg_flows_to_param", 0))
            + _int(edge_type_counts.get("return_flows_to_var", 0))
        ),
        "control_flow_edge_count": _int(edge_type_counts.get("controls", 0)),
        "cfg_edge_count": sum(
            _int(edge_type_counts.get(edge_type, 0)) for edge_type in cfg_types
        ),
        "module_dependency_edges_preview": _program_graph_edge_preview(
            graph.edges,
            {"module_depends_on"},
        ),
        "cross_function_data_flow_edges_preview": _program_graph_edge_preview(
            graph.edges,
            {"arg_flows_to_param", "return_flows_to_var"},
        ),
        "cfg_edges_preview": _program_graph_edge_preview(graph.edges, cfg_types),
    }


def _program_graph_edge_preview(
    edges: list[dict[str, Any]],
    edge_types: set[str],
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    preview: list[dict[str, Any]] = []
    for edge in edges:
        if str(edge.get("type") or "") not in edge_types:
            continue
        preview.append(
            {
                "source": str(edge.get("source") or ""),
                "target": str(edge.get("target") or ""),
                "type": str(edge.get("type") or ""),
                "line": _int(edge.get("line", 0)),
                "caller_file": str(edge.get("caller_file") or ""),
                "callee_file": str(edge.get("callee_file") or ""),
                "source_variable": str(edge.get("source_variable") or ""),
                "target_variable": str(edge.get("target_variable") or ""),
                "branch": str(edge.get("branch") or ""),
            }
        )
        if len(preview) >= limit:
            break
    return preview


def _analysis_readiness_summary(
    report: GitHubRepoAgentReport,
    repository_structure: dict[str, Any],
    fault_localization: dict[str, Any],
) -> dict[str, Any]:
    summary = report.summary
    graph = _dict(repository_structure.get("repo_graph"))
    completed_phases: list[str] = []
    analyzed_files = _int(repository_structure.get("analyzed_file_count", 0))
    static_status = str(summary.get("static_intelligence_status") or "")
    selected_signals = _int(summary.get("static_intelligence_selected_signal_count", 0))
    graph_ready = (
        _int(graph.get("file_node_count", 0)) > 0
        and _int(graph.get("function_node_count", 0)) > 0
    )
    static_ready = static_status == "analysis_ready" and selected_signals > 0
    localization_ready = (
        str(fault_localization.get("status") or "") == "pass"
        and _int(fault_localization.get("ranked_function_count", 0)) > 0
    )
    localization_mode = str(fault_localization.get("mode") or "")
    dynamic_localization_ready = localization_ready and localization_mode == "dynamic"
    static_localization_ready = (
        localization_ready and localization_mode == "static_fallback"
    )
    repair_ready = bool(summary.get("repository_test_repair_ready", False))
    patch_validation_status = str(
        summary.get("repository_test_patch_validation_status") or ""
    )
    patch_validation_safety_blocked = _int(
        summary.get(
            "repository_test_patch_validation_safety_blocked_candidate_count",
            0,
        )
    )
    patch_validation_executed = (
        patch_validation_status not in {"", "skipped", "not_executed"}
        or _int(summary.get("repository_test_patch_validation_executed_count", 0))
        > 0
        or _int(summary.get("repository_test_patch_validation_success_count", 0))
        > 0
        or patch_validation_safety_blocked > 0
    )

    if analyzed_files > 0 and graph_ready:
        completed_phases.append("phase1_repo_understanding")
    if static_ready:
        completed_phases.append("phase2_static_bug_signal_mining")
    if static_localization_ready:
        completed_phases.append("phase2_static_graph_fault_localization")
    if dynamic_localization_ready:
        completed_phases.append("phase2_dynamic_fault_localization")
    if patch_validation_executed:
        completed_phases.append("phase3_patch_validation")

    if repair_ready:
        current_stage = "phase3_patch_validation"
        stage_number = 3
        next_stage = "phase4_search_and_evaluation"
        blocker = ""
    elif patch_validation_executed and dynamic_localization_ready:
        current_stage = "phase3_patch_validation"
        stage_number = 3
        next_stage = "phase3_patch_reflection_or_expansion"
        blocker = _patch_blocker(summary)
    elif dynamic_localization_ready:
        current_stage = "phase2_dynamic_fault_localization"
        stage_number = 2
        next_stage = "phase3_patch_generation"
        blocker = _patch_blocker(summary)
    elif static_localization_ready:
        current_stage = "phase2_static_graph_fault_localization"
        stage_number = 2
        next_stage = "phase3_repository_test_execution"
        blocker = _repository_test_blocker(summary)
    elif static_ready:
        current_stage = "phase2_static_bug_signal_mining"
        stage_number = 2
        next_stage = "phase2_graph_fault_localization"
        blocker = "fault_localization_not_ready"
    elif analyzed_files > 0:
        current_stage = "phase1_repo_understanding"
        stage_number = 1
        next_stage = "phase2_static_bug_signal_mining"
        blocker = str(summary.get("static_intelligence_reason") or "static_signals_missing")
    else:
        current_stage = "source_import_blocked"
        stage_number = 0
        next_stage = "phase1_repo_understanding"
        if _dict(summary.get("github_error")):
            blocker = "github_fetch:github_api_error"
        else:
            blocker = "source_import_or_parse_missing"

    next_action = _analysis_readiness_next_action(
        summary,
        blocker=blocker,
        fallback=str(summary.get("static_intelligence_next_action") or ""),
    )
    return {
        "current_stage": current_stage,
        "stage_number": stage_number,
        "next_stage": next_stage,
        "blocker": blocker,
        "next_action": next_action,
        "completed_phases": completed_phases,
        "can_generate_static_report": analyzed_files > 0 and graph_ready,
        "can_attempt_dynamic_tests": _can_attempt_dynamic_tests(summary),
        "can_attempt_patch_repair": dynamic_localization_ready,
        "analyzed_file_count": analyzed_files,
        "static_signal_count": selected_signals,
        "repo_graph_function_count": _int(graph.get("function_node_count", 0)),
        "fault_localization_mode": localization_mode,
        "fault_localization_status": str(fault_localization.get("status") or ""),
        "fault_localization_top_function": str(
            fault_localization.get("top_function") or ""
        ),
        "dynamic_evidence_level": str(
            summary.get("repository_test_dynamic_evidence_level")
            or _dict(fault_localization.get("dynamic_fault_localization")).get(
                "dynamic_evidence_level"
            )
            or summary.get("static_intelligence_dynamic_validation_level")
            or "none"
        ),
        "dynamic_evidence_usable_for_localization": bool(
            summary.get("repository_test_dynamic_usable_for_localization", False)
        ),
        "dynamic_fault_localization_reason": str(
            summary.get("repository_test_fault_localization_reason") or ""
        ),
        "repository_test_setup_doctor_status": str(
            summary.get("repository_test_setup_doctor_status") or ""
        ),
        "repository_test_setup_doctor_blocker": str(
            summary.get("repository_test_setup_doctor_blocker") or ""
        ),
        "repository_test_setup_doctor_next_action": str(
            summary.get("repository_test_setup_doctor_next_action") or ""
        ),
        "repository_test_setup_doctor_check_count": _int(
            summary.get("repository_test_setup_doctor_check_count", 0)
        ),
        "repository_test_setup_doctor_passed_check_count": _int(
            summary.get("repository_test_setup_doctor_passed_check_count", 0)
        ),
        "repository_test_setup_doctor_warning_check_count": _int(
            summary.get("repository_test_setup_doctor_warning_check_count", 0)
        ),
        "repository_test_setup_doctor_blocked_check_count": _int(
            summary.get("repository_test_setup_doctor_blocked_check_count", 0)
        ),
        "planned_repository_test_command": str(
            summary.get("planned_repository_test_command") or ""
        ),
        "planned_repository_test_runner": str(
            summary.get("planned_repository_test_runner") or ""
        ),
        "planned_repository_test_preferred_runner": str(
            summary.get("planned_repository_test_preferred_runner") or ""
        ),
        "planned_repository_test_runner_fallback_used": bool(
            summary.get("planned_repository_test_runner_fallback_used", False)
        ),
        "planned_repository_test_runner_fallback_reason": str(
            summary.get("planned_repository_test_runner_fallback_reason") or ""
        ),
        "planned_repository_test_runner_fallback_from": str(
            summary.get("planned_repository_test_runner_fallback_from") or ""
        ),
        "planned_repository_test_runner_fallback_to": str(
            summary.get("planned_repository_test_runner_fallback_to") or ""
        ),
        "planned_repository_test_executable_now": bool(
            summary.get("planned_repository_test_executable_now", False)
        ),
        "planned_repository_test_result_status": str(
            summary.get("planned_repository_test_result_status") or ""
        ),
        "planned_repository_test_result_passed": _int(
            summary.get("planned_repository_test_result_passed", 0)
        ),
        "planned_repository_test_result_failed": _int(
            summary.get("planned_repository_test_result_failed", 0)
        ),
        "planned_repository_test_result_errors": _int(
            summary.get("planned_repository_test_result_errors", 0)
        ),
        "planned_repository_test_result_skipped": _int(
            summary.get("planned_repository_test_result_skipped", 0)
        ),
        "planned_repository_test_result_test_count": _int(
            summary.get("planned_repository_test_result_test_count", 0)
        ),
        "planned_repository_test_result_test_count_source": str(
            summary.get("planned_repository_test_result_test_count_source") or ""
        ),
        "patch_validation_status": str(
            summary.get("repository_test_patch_validation_status") or ""
        ),
        "patch_validation_reason": str(
            summary.get("repository_test_patch_validation_reason") or ""
        ),
        "patch_validation_input_candidate_count": _int(
            summary.get(
                "repository_test_patch_validation_input_candidate_count",
                0,
            )
        ),
        "patch_validation_candidate_count": _int(
            summary.get("repository_test_patch_validation_candidate_count", 0)
        ),
        "patch_validation_safety_blocked_candidate_count": _int(
            summary.get(
                "repository_test_patch_validation_safety_blocked_candidate_count",
                0,
            )
        ),
        "patch_validation_reflection_candidate_count": _int(
            summary.get("repository_test_patch_validation_reflection_candidate_count", 0)
        ),
        "patch_validation_successful_reflection_count": _int(
            summary.get("repository_test_patch_validation_successful_reflection_count", 0)
        ),
        "repair_ready": repair_ready,
        "repair_validation_scope": str(
            summary.get("repository_test_repair_validation_scope") or ""
        ),
        "reflection_trace_path": str(summary.get("reflection_trace_path") or ""),
        "reflection_trace_markdown": str(
            summary.get("reflection_trace_markdown") or ""
        ),
        "agent_execution_plan_primary_blocker": str(
            summary.get("agent_execution_plan_primary_blocker") or ""
        ),
        "agent_execution_plan_next_action": str(
            summary.get("agent_execution_plan_next_action") or ""
        ),
        "agent_execution_plan_next_command": str(
            summary.get("agent_execution_plan_next_command") or ""
        ),
    }


def _repository_test_blocker(summary: dict[str, Any]) -> str:
    dynamic_level = str(summary.get("repository_test_dynamic_evidence_level") or "")
    dynamic_usable = bool(
        summary.get("repository_test_dynamic_usable_for_localization", False)
    )
    dynamic_fault_status = str(
        summary.get("repository_test_fault_localization_status") or ""
    )
    dynamic_fault_reason = str(
        summary.get("repository_test_fault_localization_reason") or ""
    )
    setup_blocker = str(summary.get("repository_test_setup_doctor_blocker") or "")
    plan_blocker = str(summary.get("agent_execution_plan_primary_blocker") or "")
    if dynamic_usable and dynamic_fault_status not in {"", "pass"}:
        return f"dynamic_fault_localization_not_ready:{dynamic_fault_reason or dynamic_fault_status}"
    if dynamic_usable:
        return "dynamic_fault_localization_not_ready"
    if dynamic_level and dynamic_level not in {"none", "not_executed"}:
        return f"dynamic_evidence_not_usable:{dynamic_level}"
    if setup_blocker:
        return setup_blocker
    if plan_blocker:
        return plan_blocker
    return "dynamic_tests_not_executed"


def _patch_blocker(summary: dict[str, Any]) -> str:
    status = str(summary.get("repository_test_patch_validation_status") or "")
    reason = str(summary.get("repository_test_patch_validation_reason") or "")
    safety_blocked_count = _int(
        summary.get(
            "repository_test_patch_validation_safety_blocked_candidate_count",
            0,
        )
    )
    repair_ready = bool(summary.get("repository_test_repair_ready", False))
    repair_scope = str(summary.get("repository_test_repair_validation_scope") or "")
    if status in {"skipped", "not_executed"}:
        if reason == "all_candidates_blocked_by_safety_gate" or safety_blocked_count > 0:
            return "patch_candidates_blocked_by_safety_gate"
        status = ""
    if status and status != "pass":
        return reason or status
    if status == "pass" and not repair_ready:
        return (
            "patch_validation_not_repair_ready:"
            f"{repair_scope or reason or 'repair_not_verified'}"
        )
    candidate_status = str(summary.get("repository_test_patch_candidates_status") or "")
    candidate_reason = str(summary.get("repository_test_patch_candidates_reason") or "")
    if candidate_status in {"skipped", "not_executed"}:
        candidate_status = ""
    if candidate_status and candidate_status != "pass":
        return candidate_reason or candidate_status
    return "patch_generation_not_executed"


def _analysis_readiness_next_action(
    summary: dict[str, Any],
    *,
    blocker: str,
    fallback: str,
) -> str:
    if bool(summary.get("repository_test_repair_ready", False)):
        repair_scope = str(summary.get("repository_test_repair_validation_scope") or "")
        if repair_scope == "narrow_and_unchanged_regression_baseline":
            return (
                "Patch validation is repair-ready for the target failure, but "
                "broad regression has an unchanged baseline failure; fix or narrow "
                "that regression command before claiming full-suite green status."
            )
        return (
            "Patch validation is repair-ready; run search strategy evaluation "
            "and ablation analysis."
        )
    if blocker.startswith("dynamic_fault_localization_not_ready"):
        return (
            "Build repository_test_fault_localization from usable dynamic evidence "
            "or inspect unmatched traceback/nodeid evidence."
        )
    if blocker.startswith("github_fetch"):
        actions = [str(item) for item in _list(summary.get("next_actions"))]
        if actions:
            return actions[0]
        return (
            "Set GITHUB_TOKEN, pass --token-env, wait for the API limit reset, "
            "or rerun with --prefer-cached-discovery and a matching discovery.json."
        )
    if blocker == "patch_generation_not_executed":
        return "Generate patch candidates from the dynamic fault-localization ranking."
    if blocker.startswith("patch_validation_not_repair_ready"):
        return (
            "Patch validation produced candidate evidence, but repair is not "
            "fully verified; inspect regression failures and run reflection or "
            "expand patch candidates."
        )
    if blocker.startswith("patch_candidates_blocked_by_safety_gate"):
        return (
            "Patch validation did not execute because every candidate was blocked "
            "by the pre-sandbox safety gate; regenerate smaller AST-valid, "
            "scope-limited candidates before sandbox validation."
        )
    for key in (
        "repository_test_setup_doctor_next_action",
        "agent_execution_plan_next_action",
    ):
        action = str(summary.get(key) or "")
        if action:
            return action
    if blocker == "dynamic_tests_not_executed":
        return "Run with --checkout-repository-tests or pass repository_test_root."
    return fallback or "Inspect generated intelligence artifacts."


def _can_attempt_dynamic_tests(summary: dict[str, Any]) -> bool:
    if bool(summary.get("planned_repository_test_executable_now", False)):
        return True
    return bool(
        str(summary.get("planned_repository_test_command") or "")
        or str(summary.get("recommended_test_command") or "")
        or str(summary.get("repository_test_retry_command") or "")
    )


def _unified_fault_localization_summary(
    report: GitHubRepoAgentReport,
    static_fault_localization: dict[str, Any],
) -> dict[str, Any]:
    dynamic = _dynamic_fault_localization_summary(report)
    static_rankings = [
        _normalize_static_fault_row(item)
        for item in _list(static_fault_localization.get("rankings"))
    ]
    if bool(dynamic.get("available", False)):
        dynamic_role_audit = _fault_localization_role_audit(
            _list(dynamic.get("rankings"))
        )
        return {
            "mode": "dynamic",
            "status": str(dynamic.get("status") or ""),
            "reason": str(dynamic.get("reason") or ""),
            "source": "repository_test_fault_localization",
            "ranked_function_count": _int(dynamic.get("ranked_function_count", 0)),
            "matched_failed_test_count": _int(
                dynamic.get("matched_failed_test_count", 0)
            ),
            "unmatched_failed_test_count": _int(
                dynamic.get("unmatched_failed_test_count", 0)
            ),
            "traceback_frame_count": _int(dynamic.get("traceback_frame_count", 0)),
            "matched_traceback_frame_count": _int(
                dynamic.get("matched_traceback_frame_count", 0)
            ),
            "unmatched_traceback_frame_count": _int(
                dynamic.get("unmatched_traceback_frame_count", 0)
            ),
            "top_function": str(dynamic.get("top_function") or ""),
            "top_function_id": str(dynamic.get("top_function_id") or ""),
            "top_final_score": _float(dynamic.get("top_final_score", 0.0)),
            "rankings": _list(dynamic.get("rankings")),
            **dynamic_role_audit,
            "dynamic_fault_localization": dynamic,
            "static_fallback_available": bool(static_rankings),
            "static_fallback_top_function": str(
                static_fault_localization.get("top_function") or ""
            ),
        }
    static_role_audit = _fault_localization_role_audit(static_rankings)
    return {
        "mode": "static_fallback",
        "status": str(static_fault_localization.get("status") or "skipped"),
        "reason": "static_fallback_no_dynamic_ranking",
        "source": "static_fault_localization",
        "ranked_function_count": _int(
            static_fault_localization.get("ranked_function_count", 0)
        ),
        "top_function": str(static_fault_localization.get("top_function") or ""),
        "top_function_id": str(
            static_fault_localization.get("top_function_id") or ""
        ),
        "top_final_score": _float(
            static_fault_localization.get("top_final_score", 0.0)
        ),
        "rankings": static_rankings,
        **static_role_audit,
        "dynamic_fault_localization": dynamic,
        "dynamic_reason": str(dynamic.get("reason") or ""),
        "static_reason": str(static_fault_localization.get("reason") or ""),
        "static_fallback_available": bool(static_rankings),
    }


def _dynamic_fault_localization_summary(
    report: GitHubRepoAgentReport,
) -> dict[str, Any]:
    onboarding = _dict(report.onboarding_report)
    payload = _dict(onboarding.get("repository_test_fault_localization"))
    if not payload:
        payload = _read_json(
            str(report.output_paths.get("repository_test_fault_localization_json") or "")
        )
    rankings = [
        _normalize_dynamic_fault_row(item)
        for item in _list(payload.get("rankings"))
    ]
    status = str(payload.get("status") or "skipped")
    reason = str(payload.get("reason") or "repository_test_fault_localization_missing")
    available = status == "pass" and bool(rankings)
    top_row = rankings[0] if rankings else {}
    return {
        "available": available,
        "status": status,
        "reason": reason,
        "source": "repository_test_fault_localization",
        "ranked_function_count": _int(payload.get("ranking_count", len(rankings))),
        "matched_failed_test_count": _int(
            payload.get("matched_failed_test_count", 0)
        ),
        "unmatched_failed_test_count": _int(
            payload.get("unmatched_failed_test_count", 0)
        ),
        "traceback_frame_count": _int(payload.get("traceback_frame_count", 0)),
        "matched_traceback_frame_count": _int(
            payload.get("matched_traceback_frame_count", 0)
        ),
        "unmatched_traceback_frame_count": _int(
            payload.get("unmatched_traceback_frame_count", 0)
        ),
        "dynamic_evidence_level": str(payload.get("dynamic_evidence_level") or ""),
        "top_function": str(
            payload.get("top_function") or top_row.get("function_name") or ""
        ),
        "top_function_id": str(
            payload.get("top_function_id") or top_row.get("function_id") or ""
        ),
        "top_final_score": _float(
            payload.get("top_score", top_row.get("final_score", 0.0))
        ),
        "rankings": rankings,
    }


def _normalize_dynamic_fault_row(row_value: Any) -> dict[str, Any]:
    row = _dict(row_value)
    signals = _dict(row.get("signals"))
    return {
        "rank": _int(row.get("rank", 0)),
        "function_id": str(row.get("function_id") or ""),
        "function_name": str(row.get("function_name") or ""),
        "file_path": str(row.get("file_path") or ""),
        "start_line": _int(row.get("start_line", 0)),
        "end_line": _int(row.get("end_line", 0)),
        "static_rule_score": _float(signals.get("static", 0.0)),
        "graph_score": _float(signals.get("graph", 0.0)),
        "source_role": _source_role_for_path(str(row.get("file_path") or "")),
        "source_role_score": _source_role_score(
            _source_role_for_path(str(row.get("file_path") or ""))
        ),
        "sbfl_score": _float(signals.get("sbfl", 0.0)),
        "dynamic_test_evidence_score": _float(
            signals.get("dynamic_test_evidence", 0.0)
        ),
        "final_score": _float(row.get("score", 0.0)),
        "reason": str(row.get("reason") or ""),
        "signals": signals,
    }


def _normalize_static_fault_row(row_value: Any) -> dict[str, Any]:
    row = dict(_dict(row_value))
    row.setdefault("sbfl_score", 0.0)
    row.setdefault("dynamic_test_evidence_score", 0.0)
    row.setdefault(
        "source_role",
        _source_role_for_path(str(row.get("file_path") or "")),
    )
    row.setdefault(
        "source_role_score",
        _source_role_score(str(row.get("source_role") or "")),
    )
    row.setdefault("evidence_mode", "static")
    return row


def _fault_localization_role_audit(rankings: list[Any]) -> dict[str, Any]:
    rows = [_dict(item) for item in rankings]
    role_counts: dict[str, int] = {}
    application_rows: list[dict[str, Any]] = []
    for row in rows:
        role = str(row.get("source_role") or "") or _source_role_for_path(
            str(row.get("file_path") or "")
        )
        if not role:
            role = "unknown"
        row["source_role"] = role
        role_counts[role] = role_counts.get(role, 0) + 1
        if role == "application":
            application_rows.append(row)
    top_row = rows[0] if rows else {}
    top_application = application_rows[0] if application_rows else {}
    return {
        "source_role_counts": dict(sorted(role_counts.items())),
        "top_source_role": str(top_row.get("source_role") or ""),
        "application_candidate_count": len(application_rows),
        "application_candidate_available": bool(application_rows),
        "top_application_function": str(top_application.get("function_name") or ""),
        "top_application_function_id": str(top_application.get("function_id") or ""),
        "top_application_final_score": _float(
            top_application.get("final_score", 0.0)
        ),
        "non_application_top_ranked": bool(
            rows and str(top_row.get("source_role") or "") != "application"
        ),
        "non_application_topk_only": bool(rows and not application_rows),
    }


def _static_fault_localization_summary(
    report: GitHubRepoAgentReport,
    repo_graph: dict[str, Any],
    *,
    top_k: int = 10,
) -> dict[str, Any]:
    source_mining = _read_json(str(report.output_paths.get("source_mining_json") or ""))
    candidates = [_dict(item) for item in _list(source_mining.get("candidates"))]
    if not candidates:
        return {
            "status": "skipped",
            "reason": "static_candidates_missing",
            "weights": _static_fault_weights(),
            "candidate_count": 0,
            "candidate_function_count": 0,
            "ranked_function_count": 0,
            "top_function": "",
            "rankings": [],
        }
    function_nodes = [_dict(item) for item in _list(repo_graph.get("function_nodes"))]
    if not function_nodes:
        return {
            "status": "skipped",
            "reason": "repo_graph_function_nodes_missing",
            "weights": _static_fault_weights(),
            "candidate_count": len(candidates),
            "candidate_function_count": 0,
            "ranked_function_count": 0,
            "top_function": "",
            "rankings": [],
        }

    by_file_name: dict[tuple[str, str], dict[str, Any]] = {}
    for node in function_nodes:
        file_path = str(node.get("file_path") or "")
        name = str(node.get("name") or "")
        for alias in {name, name.split(".")[-1]}:
            if alias:
                by_file_name[(file_path, alias)] = node

    grouped: dict[str, dict[str, Any]] = {}
    unmatched: list[dict[str, Any]] = []
    for candidate in candidates:
        target_path = str(candidate.get("target_path") or "")
        function_name = str(
            candidate.get("function_name")
            or _dict(candidate.get("source_summary")).get("function")
            or ""
        )
        node = by_file_name.get((target_path, function_name))
        if node is None:
            node = _match_function_node_by_suffix(
                function_nodes,
                target_path=target_path,
                function_name=function_name,
            )
        if node is None:
            unmatched.append(
                {
                    "candidate_id": str(candidate.get("id") or ""),
                    "target_path": target_path,
                    "function_name": function_name,
                }
            )
            continue
        function_id = str(node.get("id") or "")
        bucket = grouped.setdefault(
            function_id,
            {
                "node": node,
                "candidates": [],
                "rule_ids": set(),
                "bug_types": set(),
                "candidate_ids": [],
            },
        )
        bucket["candidates"].append(candidate)
        bucket["candidate_ids"].append(str(candidate.get("id") or ""))
        for rule in _list(candidate.get("rule_ids")):
            bucket["rule_ids"].add(str(rule))
        bug_type = str(candidate.get("bug_type") or "")
        if bug_type:
            bucket["bug_types"].add(bug_type)

    if not grouped:
        return {
            "status": "warning",
            "reason": "static_candidates_unmapped_to_functions",
            "weights": _static_fault_weights(),
            "candidate_count": len(candidates),
            "candidate_function_count": 0,
            "ranked_function_count": 0,
            "top_function": "",
            "unmatched_candidate_count": len(unmatched),
            "unmatched_candidates": unmatched[:10],
            "rankings": [],
        }

    static_raw_values = {
        function_id: (
            len(_list(bucket.get("candidates")))
            + 0.50 * len(bucket.get("rule_ids", set()))
            + 0.25 * len(bucket.get("bug_types", set()))
        )
        for function_id, bucket in grouped.items()
    }
    max_static_raw = max(static_raw_values.values(), default=1.0)
    max_graph_score = max(
        (_float(_dict(bucket.get("node")).get("score", 0.0)) for bucket in grouped.values()),
        default=1.0,
    ) or 1.0
    weights = _static_fault_weights()
    ranked_rows: list[dict[str, Any]] = []
    for function_id, bucket in grouped.items():
        node = _dict(bucket.get("node"))
        static_rule_score = _safe_ratio(static_raw_values[function_id], max_static_raw)
        graph_score = _safe_ratio(_float(node.get("score", 0.0)), max_graph_score)
        source_role = _source_role_for_path(str(node.get("file_path") or ""))
        source_role_score = _source_role_score(source_role)
        final_score = (
            weights["static_rule"] * static_rule_score
            + weights["graph"] * graph_score
            + weights["source_role"] * source_role_score
        )
        ranked_rows.append(
            {
                "rank": 0,
                "function_id": function_id,
                "function_name": str(node.get("name") or ""),
                "file_path": str(node.get("file_path") or ""),
                "start_line": _int(node.get("start_line", 0)),
                "end_line": _int(node.get("end_line", 0)),
                "static_rule_score": round(static_rule_score, 4),
                "graph_score": round(graph_score, 4),
                "source_role": source_role,
                "source_role_score": round(source_role_score, 4),
                "final_score": round(final_score, 4),
                "candidate_count": len(_list(bucket.get("candidates"))),
                "rule_ids": sorted(bucket.get("rule_ids", set())),
                "bug_types": sorted(bucket.get("bug_types", set())),
                "candidate_ids": [
                    item for item in _list(bucket.get("candidate_ids")) if item
                ],
                "graph_signals": {
                    "in_degree": _int(node.get("in_degree", 0)),
                    "out_degree": _int(node.get("out_degree", 0)),
                    "cyclomatic_complexity": _int(
                        node.get("cyclomatic_complexity", 0)
                    ),
                    "line_count": _int(node.get("line_count", 0)),
                    "structural_score": _float(node.get("score", 0.0)),
                },
            }
        )
    ranked_rows.sort(
        key=lambda item: (
            -_float(item.get("final_score", 0.0)),
            -_float(item.get("static_rule_score", 0.0)),
            -_float(item.get("graph_score", 0.0)),
            -_float(item.get("source_role_score", 0.0)),
            str(item.get("file_path") or ""),
            str(item.get("function_name") or ""),
        )
    )
    ranked_rows = [
        {**item, "rank": index + 1}
        for index, item in enumerate(ranked_rows)
    ]
    top_rows = ranked_rows[:top_k]
    role_audit = _fault_localization_role_audit(ranked_rows)
    return {
        "status": "pass",
        "reason": "ranked_static_candidates_with_repo_graph",
        "weights": weights,
        "candidate_count": len(candidates),
        "candidate_function_count": len(grouped),
        "ranked_function_count": len(ranked_rows),
        "top_k": top_k,
        "top_function": str(top_rows[0].get("function_name") or "") if top_rows else "",
        "top_function_id": str(top_rows[0].get("function_id") or "") if top_rows else "",
        "top_final_score": _float(top_rows[0].get("final_score", 0.0)) if top_rows else 0.0,
        "unmatched_candidate_count": len(unmatched),
        "unmatched_candidates": unmatched[:10],
        "rankings": top_rows,
        **role_audit,
    }


def _match_function_node_by_suffix(
    function_nodes: list[dict[str, Any]],
    *,
    target_path: str,
    function_name: str,
) -> dict[str, Any] | None:
    target_suffix = target_path.replace("\\", "/").strip("/")
    matches = []
    for node in function_nodes:
        file_path = str(node.get("file_path") or "").replace("\\", "/").strip("/")
        name = str(node.get("name") or "")
        if not file_path.endswith(target_suffix):
            continue
        if function_name not in {name, name.split(".")[-1]}:
            continue
        matches.append(node)
    return matches[0] if len(matches) == 1 else None


def _static_fault_weights() -> dict[str, float]:
    return {"static_rule": 0.55, "graph": 0.30, "source_role": 0.15}


def _source_role_for_path(path_text: str) -> str:
    normalized = path_text.replace("\\", "/").strip("/")
    lower = normalized.lower()
    name = lower.rsplit("/", 1)[-1]
    parts = [part for part in lower.split("/") if part]
    if not lower:
        return "unknown"
    if (
        name.startswith("test_")
        or name.endswith("_test.py")
        or "tests" in parts
        or "test" in parts
    ):
        return "test"
    if name in {"noxfile.py", "toxfile.py", "conftest.py"}:
        return "test_automation"
    if name in {"setup.py", "manage.py", "fabfile.py", "tasks.py"}:
        return "project_automation"
    if any(part in {"scripts", "tools", "examples", "docs", "doc"} for part in parts):
        return "support"
    if parts and parts[0] == "src":
        return "application"
    return "application"


def _source_role_score(role: str) -> float:
    return {
        "application": 1.0,
        "support": 0.75,
        "project_automation": 0.65,
        "test_automation": 0.55,
        "test": 0.35,
        "unknown": 0.80,
    }.get(role, 0.80)


def _import_aliases_by_file(
    graph_files: list[dict[str, Any]],
    module_to_file: dict[str, str],
) -> dict[str, dict[str, str]]:
    function_by_file_and_name: dict[tuple[str, str], str] = {}
    for item in graph_files:
        file_path = str(item.get("file_path") or "")
        analysis = item.get("analysis")
        for function in getattr(analysis, "functions", []):
            function_by_file_and_name[(file_path, str(function.name))] = str(function.id)
            qualified_name = str(function.metadata.get("qualified_name") or "")
            if qualified_name:
                function_by_file_and_name[(file_path, qualified_name)] = str(function.id)
                function_by_file_and_name[
                    (file_path, qualified_name.split(".")[-1])
                ] = str(function.id)

    aliases_by_file: dict[str, dict[str, str]] = {}
    for item in graph_files:
        file_path = str(item.get("file_path") or "")
        current_module = _module_name_from_path(file_path)
        aliases: dict[str, str] = {}
        analysis = item.get("analysis")
        for import_info in getattr(analysis, "imports", []):
            module = str(getattr(import_info, "module", "") or "")
            names = [str(name) for name in getattr(import_info, "names", [])]
            if module:
                target_files = _resolve_import_target_files(
                    import_info,
                    current_module=current_module,
                    module_to_file=module_to_file,
                )
                if len(target_files) != 1:
                    continue
                target_file = target_files[0]
                for name in names:
                    target_id = function_by_file_and_name.get((target_file, name))
                    if target_id:
                        aliases[name] = target_id
            else:
                for name in names:
                    target_file = module_to_file.get(name)
                    if target_file:
                        aliases[name] = target_file
        aliases_by_file[file_path] = aliases
    return aliases_by_file


def _resolve_import_target_files(
    import_info: Any,
    *,
    current_module: str,
    module_to_file: dict[str, str],
) -> list[str]:
    module = str(getattr(import_info, "module", "") or "")
    level = _int(getattr(import_info, "level", 0))
    names = [str(name) for name in getattr(import_info, "names", [])]
    candidates: list[str] = []
    if level > 0:
        base = _relative_import_base(current_module, level)
        if module:
            candidates.append(".".join(part for part in [base, module] if part))
        else:
            candidates.append(base)
    elif module:
        candidates.append(module)
    else:
        candidates.extend(names)

    resolved: list[str] = []
    for candidate in candidates:
        for module_name in _module_candidates(candidate):
            target = module_to_file.get(module_name)
            if target and target not in resolved:
                resolved.append(target)
                break
    return resolved


def _resolve_call_target(
    call: Any,
    *,
    file_path: str,
    file_function_names: dict[str, dict[str, str]],
    repo_function_names: dict[str, list[str]],
    import_aliases: dict[str, str],
) -> str:
    callee = str(getattr(call, "callee", "") or "")
    if not callee:
        return ""
    alias_target = import_aliases.get(callee)
    if alias_target and alias_target.endswith(".py"):
        return ""
    if alias_target:
        return alias_target
    local_names = file_function_names.get(file_path, {})
    for candidate in _callee_name_candidates(callee):
        if candidate in local_names:
            return local_names[candidate]
        matches = repo_function_names.get(candidate, [])
        unique_matches = sorted(set(matches))
        if len(unique_matches) == 1:
            return unique_matches[0]
    return ""


def _rank_file_nodes(
    file_summaries: list[dict[str, Any]],
    *,
    in_degree: dict[str, int],
    out_degree: dict[str, int],
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for item in file_summaries:
        path = str(item.get("file_path") or "")
        incoming = _int(in_degree.get(path, 0))
        outgoing = _int(out_degree.get(path, 0))
        score = (
            incoming * 2.0
            + outgoing
            + _int(item.get("function_count", 0)) * 0.5
            + _int(item.get("max_cyclomatic_complexity", 0)) * 0.25
        )
        ranked.append(
            {
                "file_path": path,
                "in_degree": incoming,
                "out_degree": outgoing,
                "function_count": _int(item.get("function_count", 0)),
                "loc": _int(item.get("loc", 0)),
                "score": round(score, 4),
            }
        )
    return sorted(
        ranked,
        key=lambda item: (
            -_float(item.get("score", 0.0)),
            str(item.get("file_path") or ""),
        ),
    )


def _rank_function_nodes(
    function_nodes: dict[str, dict[str, Any]],
    *,
    in_degree: dict[str, int],
    out_degree: dict[str, int],
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for node_id, item in function_nodes.items():
        incoming = _int(in_degree.get(node_id, 0))
        outgoing = _int(out_degree.get(node_id, 0))
        complexity = _int(item.get("cyclomatic_complexity", 0))
        score = incoming * 2.0 + outgoing + complexity * 0.5
        ranked.append(
            {
                "id": node_id,
                "name": str(item.get("name") or ""),
                "file_path": str(item.get("file_path") or ""),
                "start_line": _int(item.get("start_line", 0)),
                "end_line": _int(item.get("end_line", 0)),
                "in_degree": incoming,
                "out_degree": outgoing,
                "cyclomatic_complexity": complexity,
                "line_count": _int(item.get("line_count", 0)),
                "score": round(score, 4),
            }
        )
    return sorted(
        ranked,
        key=lambda item: (
            -_float(item.get("score", 0.0)),
            str(item.get("file_path") or ""),
            str(item.get("name") or ""),
        ),
    )


def _module_name_from_path(path_text: str) -> str:
    normalized = path_text.replace("\\", "/").strip("/")
    if normalized.endswith(".py"):
        normalized = normalized[:-3]
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return ""
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _relative_import_base(current_module: str, level: int) -> str:
    parts = [part for part in current_module.split(".") if part]
    if not parts:
        return ""
    if parts[-1] != "__init__":
        parts = parts[:-1]
    keep = max(0, len(parts) - max(0, level - 1))
    return ".".join(parts[:keep])


def _module_candidates(module_name: str) -> list[str]:
    parts = [part for part in module_name.split(".") if part]
    candidates = [".".join(parts[:index]) for index in range(len(parts), 0, -1)]
    return candidates


def _callee_name_candidates(callee: str) -> list[str]:
    parts = [part for part in callee.split(".") if part]
    if not parts:
        return []
    candidates = [callee, parts[-1]]
    if len(parts) >= 2:
        candidates.append(".".join(parts[-2:]))
    return list(dict.fromkeys(candidates))


def _function_complexity_metrics(
    tree: ast.AST,
    file_path: str,
) -> list[dict[str, Any]]:
    visitor = _FunctionComplexityVisitor(file_path)
    visitor.visit(tree)
    return visitor.metrics


class _FunctionComplexityVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.metrics: list[dict[str, Any]] = []
        self._class_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record(node, is_async=True)

    def _record(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        is_async: bool,
    ) -> None:
        name_parts = [*self._class_stack, node.name]
        self.metrics.append(
            {
                "name": ".".join(name_parts),
                "file_path": self.file_path,
                "start_line": _int(getattr(node, "lineno", 0)),
                "end_line": _int(
                    getattr(node, "end_lineno", getattr(node, "lineno", 0))
                ),
                "line_count": max(
                    1,
                    _int(getattr(node, "end_lineno", getattr(node, "lineno", 0)))
                    - _int(getattr(node, "lineno", 0))
                    + 1,
                ),
                "cyclomatic_complexity": _cyclomatic_complexity(node),
                "call_count": _call_count(node),
                "is_async": is_async,
                "is_method": bool(self._class_stack),
            }
        )


def _cyclomatic_complexity(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> int:
    complexity = 1
    for child in ast.walk(node):
        if child is node:
            continue
        if isinstance(
            child,
            (
                ast.If,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.ExceptHandler,
                ast.IfExp,
                ast.Assert,
            ),
        ):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += max(0, len(child.values) - 1)
        elif isinstance(child, ast.Match):
            complexity += len(child.cases)
    return complexity


def _call_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    return sum(1 for child in ast.walk(node) if isinstance(child, ast.Call))


def _import_roots(import_info: Any) -> list[str]:
    module = str(getattr(import_info, "module", "") or "")
    names = [str(name) for name in getattr(import_info, "names", [])]
    if module:
        return [module.split(".", 1)[0]]
    return [name.split(".", 1)[0] for name in names if name]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run arbitrary-GitHub-repository code intelligence analysis and "
            "optionally drive the AgentController with --agent."
        )
    )
    parser.add_argument("repo", help="GitHub owner/repo or github.com URL.")
    parser.add_argument(
        "output_dir",
        nargs="?",
        help=(
            "Directory for generated artifacts. Defaults to an outputs/"
            "repo_intelligence* directory derived from the repo and profile."
        ),
    )
    parser.add_argument("--ref", help="Commit, tag, or branch. Defaults to default_branch.")
    parser.add_argument("--include", action="append")
    parser.add_argument("--exclude", action="append")
    parser.add_argument("--target-prefix", default="")
    parser.add_argument("--recipe", action="append")
    parser.add_argument("--source-cache-dir")
    parser.add_argument("--max-sources", type=int, default=DEFAULT_MAX_SOURCES)
    parser.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES)
    parser.add_argument("--preset", choices=["mining", "smoke"], default="mining")
    parser.add_argument(
        "--execution-profile",
        choices=["static", "checkout", "phase3-fast", "agent-auto"],
        default="static",
        help=(
            "Convenience profile for arbitrary-repo runs. static keeps the "
            "default source/graph analysis path; checkout enables full-repo "
            "checkout-backed test planning/execution; phase3-fast also enables "
            "controlled repository-test auto retry with common Python runners; "
            "agent-auto lets the controller observe static state and execute "
            "the first safe follow-up action automatically."
        ),
    )
    parser.add_argument(
        "--agent",
        action="store_true",
        help=(
            "Shortcut for one-command Agent mode. Equivalent to the "
            "agent-auto execution profile with a broader default action budget."
        ),
    )
    parser.add_argument("--auto-controller-actions", action="store_true")
    parser.add_argument("--auto-controller-max-actions", type=int, default=2)
    parser.add_argument(
        "--auto-phase4-evaluation",
        action="store_true",
        help=(
            "When the auto controller reaches the Phase 4 search/evaluation "
            "action, execute a repository-level search/ablation evaluation "
            "artifact instead of only stopping at patch-validation readiness."
        ),
    )
    parser.add_argument(
        "--auto-phase4-strategy-reruns",
        action="store_true",
        help=(
            "When --auto-phase4-evaluation is enabled, rerun a small set of "
            "BeamPatchSearch strategy variants against the repository sandbox."
        ),
    )
    parser.add_argument("--phase4-strategy-rerun-limit", type=int, default=3)
    parser.add_argument("--phase4-strategy-rerun-timeout", type=int)
    parser.add_argument("--repository-test-root")
    parser.add_argument(
        "--repository-test-timeout",
        type=int,
        default=DEFAULT_REPOSITORY_TEST_TIMEOUT,
    )
    parser.add_argument(
        "--repository-test-failure-overlay-candidate-limit",
        type=int,
        default=5,
    )
    parser.add_argument(
        "--repository-patch-generation-mode",
        choices=["rule", "llm", "hybrid"],
        default="rule",
        help=(
            "Patch candidate source for repository repair: rule-based, LLM, "
            "or hybrid rule+LLM. LLM mode reads CIA_LLM_* env vars."
        ),
    )
    parser.add_argument("--repository-llm-patch-candidate-limit", type=int)
    parser.add_argument(
        "--repository-patch-candidate-variant",
        action="append",
        default=[],
        help=(
            "Restrict repository patch candidates to a variant name; repeat "
            "for multiple allowed variants. Useful for auditable reflection "
            "hard-case probes."
        ),
    )
    parser.add_argument(
        "--repository-test-reflection-mode",
        choices=["rule", "llm", "none"],
        default="rule",
    )
    parser.add_argument("--repository-test-reflection-rounds", type=int, default=1)
    parser.add_argument("--repository-test-reflection-width", type=int, default=1)
    parser.add_argument(
        "--patch-judge-mode",
        choices=["none", "llm"],
        default="none",
        help=(
            "Optional patch-level LLM judge for repository repair validation. "
            "The judge can rank/audit candidates, but sandbox pytest decides success."
        ),
    )
    parser.add_argument("--no-repository-test-command", action="store_true")
    parser.add_argument("--run-repository-test-environment-setup", action="store_true")
    parser.add_argument("--run-repository-test-retry", action="store_true")
    parser.add_argument(
        "--run-repository-test-retry-prerequisites",
        action="store_true",
    )
    parser.add_argument("--auto-repository-test-retry", action="store_true")
    parser.add_argument(
        "--auto-repository-test-retry-max-risk",
        choices=["low", "medium", "high"],
        default="low",
    )
    parser.add_argument(
        "--auto-repository-test-retry-runner",
        action="append",
        default=[],
    )
    parser.add_argument(
        "--repository-test-environment-setup-timeout",
        type=int,
        default=120,
    )
    parser.add_argument("--checkout-repository-tests", action="store_true")
    parser.add_argument("--repository-checkout-timeout", type=int, default=120)
    parser.add_argument("--repository-checkout-depth", type=int, default=1)
    parser.add_argument(
        "--prefer-cached-discovery",
        action="store_true",
        help=(
            "Reuse output_dir/discovery.json before GitHub discovery when it "
            "matches the requested repository/ref."
        ),
    )
    parser.add_argument("--no-auto-fallback", action="store_true")
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--api-base-url", default="https://api.github.com")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--output-summary")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument(
        "--require-analysis-ready",
        action="store_true",
        help="Exit non-zero unless static intelligence reaches analysis_ready.",
    )
    return parser


def main(argv: list[str] | None = None, opener=None) -> None:
    parser = build_arg_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_argv)
    output_dir_defaulted = False
    if not args.output_dir:
        args.output_dir = str(
            _default_output_dir_for_repo(
                args.repo,
                execution_profile=args.execution_profile,
                agent_shortcut=bool(args.agent),
            )
        )
        output_dir_defaulted = True
    _apply_execution_profile(
        args,
        repository_test_timeout_explicit=_argv_has_option(
            raw_argv,
            "--repository-test-timeout",
        ),
    )
    report = run_github_repo_intelligence(
        args.repo,
        args.output_dir,
        ref=args.ref,
        token=_token_from_env(args.token_env),
        include=args.include,
        exclude=args.exclude,
        target_prefix=args.target_prefix,
        recipes=args.recipe,
        source_cache_dir=args.source_cache_dir,
        max_sources=args.max_sources,
        max_candidates=args.max_candidates,
        preset=args.preset,
        auto_fallback=not args.no_auto_fallback,
        repository_test_root=args.repository_test_root,
        repository_test_timeout=args.repository_test_timeout,
        repository_test_failure_overlay_candidate_limit=(
            args.repository_test_failure_overlay_candidate_limit
        ),
        repository_patch_generation_mode=args.repository_patch_generation_mode,
        repository_llm_patch_candidate_limit=args.repository_llm_patch_candidate_limit,
        repository_patch_candidate_variant_allowlist=(
            args.repository_patch_candidate_variant
        ),
        repository_test_reflection_mode=args.repository_test_reflection_mode,
        repository_test_reflection_rounds=args.repository_test_reflection_rounds,
        repository_test_reflection_width=args.repository_test_reflection_width,
        patch_judge_mode=args.patch_judge_mode,
        run_repository_test_command=not args.no_repository_test_command,
        run_repository_test_environment_setup=(
            args.run_repository_test_environment_setup
        ),
        run_repository_test_retry=args.run_repository_test_retry,
        run_repository_test_retry_prerequisites=(
            args.run_repository_test_retry_prerequisites
        ),
        auto_repository_test_retry=args.auto_repository_test_retry,
        auto_repository_test_retry_max_risk=args.auto_repository_test_retry_max_risk,
        auto_repository_test_retry_allowed_runners=(
            args.auto_repository_test_retry_runner
        ),
        repository_test_environment_setup_timeout=(
            args.repository_test_environment_setup_timeout
        ),
        checkout_repository_tests=args.checkout_repository_tests,
        repository_checkout_timeout=args.repository_checkout_timeout,
        repository_checkout_depth=args.repository_checkout_depth,
        prefer_cached_discovery=args.prefer_cached_discovery,
        auto_controller_actions=args.auto_controller_actions,
        auto_controller_max_actions=args.auto_controller_max_actions,
        auto_phase4_evaluation=args.auto_phase4_evaluation,
        auto_phase4_strategy_reruns=args.auto_phase4_strategy_reruns,
        phase4_strategy_rerun_limit=args.phase4_strategy_rerun_limit,
        phase4_strategy_rerun_timeout=args.phase4_strategy_rerun_timeout,
        execution_profile=args.execution_profile,
        agent_shortcut=args.agent,
        output_dir_defaulted=output_dir_defaulted,
        api_base_url=args.api_base_url,
        timeout=args.timeout,
        opener=opener,
    )
    summary = github_repo_intelligence_summary(report)
    write_github_repo_intelligence_artifacts(report, summary)
    markdown = _render_github_repo_intelligence_payload(summary)
    if args.format == "json":
        rendered = json.dumps(
            summary,
            indent=2,
            ensure_ascii=False,
        )
    else:
        rendered = markdown
    if args.output_summary:
        Path(args.output_summary).write_text(rendered, encoding="utf-8")
    print(rendered)
    ready = report.summary.get("static_intelligence_status") == "analysis_ready"
    raise SystemExit(0 if ready or not args.require_analysis_ready else 1)


def _apply_execution_profile(
    args: argparse.Namespace,
    *,
    repository_test_timeout_explicit: bool = False,
) -> None:
    profile = str(getattr(args, "execution_profile", "static") or "static")
    if bool(getattr(args, "agent", False)):
        profile = "agent-auto"
        args.execution_profile = "agent-auto"
        if _int(getattr(args, "auto_controller_max_actions", 0)) < 4:
            args.auto_controller_max_actions = 4
    if profile == "static":
        return
    if profile == "checkout":
        args.checkout_repository_tests = True
        if (
            not repository_test_timeout_explicit
            and args.repository_test_timeout == DEFAULT_REPOSITORY_TEST_TIMEOUT
        ):
            args.repository_test_timeout = 30
        return
    if profile == "phase3-fast":
        args.checkout_repository_tests = True
        args.run_repository_test_retry_prerequisites = True
        args.auto_repository_test_retry = True
        if (
            not repository_test_timeout_explicit
            and args.repository_test_timeout == DEFAULT_REPOSITORY_TEST_TIMEOUT
        ):
            args.repository_test_timeout = 30
        if str(args.auto_repository_test_retry_max_risk or "") == "low":
            args.auto_repository_test_retry_max_risk = "medium"
        if not args.auto_repository_test_retry_runner:
            args.auto_repository_test_retry_runner = ["pytest", "unittest"]
        return
    if profile == "agent-auto":
        args.auto_controller_actions = True
        if not getattr(args, "source_cache_dir", None):
            args.source_cache_dir = str(Path(str(args.output_dir)) / "source_cache")
        if (
            not repository_test_timeout_explicit
            and args.repository_test_timeout == DEFAULT_REPOSITORY_TEST_TIMEOUT
        ):
            args.repository_test_timeout = 30
        return
    raise ValueError(f"unsupported execution profile: {profile}")


def _default_output_dir_for_repo(
    repo_spec: str,
    *,
    execution_profile: str = "static",
    agent_shortcut: bool = False,
) -> Path:
    owner, repo = parse_github_repo_spec(repo_spec)
    slug = _repo_output_slug(f"{owner}_{repo}")
    profile = str(execution_profile or "static")
    if agent_shortcut or profile == "agent-auto":
        prefix = "repo_intelligence_agent"
    elif profile == "phase3-fast":
        prefix = "repo_intelligence_phase3"
    elif profile == "checkout":
        prefix = "repo_intelligence_checkout"
    else:
        prefix = "repo_intelligence"
    return Path("outputs") / f"{prefix}_{slug}"


def _repo_output_slug(value: str) -> str:
    chars = [ch.lower() if ch.isalnum() else "_" for ch in value.strip()]
    slug = "".join(chars).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "repo"


def _argv_has_option(argv: list[str], option: str) -> bool:
    prefix = f"{option}="
    return any(item == option or item.startswith(prefix) for item in argv)


def _token_from_env(name: str) -> str | None:
    if not name:
        return None
    token = os.environ.get(name)
    return token or None


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(
        f"{key}={_int(value)}" for key, value in sorted(counts.items())
    )


def _format_candidate_variant_filter(payload: dict[str, Any]) -> str:
    if not payload or not bool(payload.get("enabled", False)):
        return "disabled"
    allowlist = _format_list(_list(payload.get("allowlist")))
    return (
        f"enabled allowlist={allowlist} "
        f"kept={_int(payload.get('kept_count', 0))}/"
        f"{_int(payload.get('input_count', 0))} "
        f"dropped={_int(payload.get('dropped_count', 0))}"
    )


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


def _counts_by_field(rows: list[Any], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in rows:
        value = str(_dict(item).get(field) or "")
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _format_list(values: list[Any]) -> str:
    items = [str(item) for item in values if str(item)]
    return ", ".join(items) if items else "none"


def _top_counts(counts: dict[str, int], *, limit: int = 10) -> dict[str, int]:
    return {
        key: value
        for key, value in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:limit]
    }


def _read_json(path_text: str) -> dict[str, Any]:
    if not path_text:
        return {}
    path = Path(path_text)
    if not path.exists():
        return {}
    try:
        return _dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}


def _increment(counts: dict[str, int], key: str) -> None:
    normalized = key or "unknown"
    counts[normalized] = counts.get(normalized, 0) + 1


def _safe_ratio(value: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return max(0.0, min(1.0, value / denominator))


def _directory(path_text: str) -> str:
    normalized = path_text.replace("\\", "/").strip("/")
    if "/" not in normalized:
        return "."
    directory = normalized.rsplit("/", 1)[0]
    return directory or "."


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


if __name__ == "__main__":
    main()
