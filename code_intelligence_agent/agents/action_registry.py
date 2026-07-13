from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REQUIRED_ACTION_IDS = [
    "clone_or_load_repository",
    "discover_repository_structure",
    "discover_tests",
    "diagnose_environment",
    "run_repository_tests",
    "localize_fault",
    "generate_llm_patch_candidates",
    "diagnose_llm_provider_failure",
    "retry_llm_patch_generation",
    "generate_hybrid_patch_candidates",
    "validate_patch_in_sandbox",
    "run_llm_patch_reflection_loop",
    "run_llm_patch_judge",
    "emit_blocker_report",
    "generate_final_agent_report",
]


ACTION_ALIASES = {
    "rerun_repository_tests_from_session": "run_repository_tests",
    "narrow_repository_scope": "discover_repository_structure",
    "change_repair_strategy": "generate_hybrid_patch_candidates",
    "continue_repair_with_patch_memory": "generate_hybrid_patch_candidates",
    "retry_repository_checkout_or_use_cache": "clone_or_load_repository",
    "retry_with_github_token_or_cache": "clone_or_load_repository",
    "adjust_source_filters": "discover_repository_structure",
    "mine_static_bug_signals": "localize_fault",
    "build_static_graph_fault_ranking": "localize_fault",
    "build_dynamic_fault_localization": "localize_fault",
    "run_dynamic_fault_localization": "localize_fault",
    "adjust_application_source_focus": "localize_fault",
    "expand_static_candidate_search": "localize_fault",
    "collect_dynamic_failure_evidence": "run_repository_tests",
    "run_repository_tests_with_checkout": "run_repository_tests",
    "narrow_repository_tests_after_timeout": "run_repository_tests",
    "generate_controlled_failure_overlay": "run_repository_tests",
    "convert_passing_tests_to_regression_guard": "run_repository_tests",
    "discover_repository_tests": "discover_tests",
    "diagnose_test_execution_failure": "diagnose_environment",
    "prepare_repository_test_environment": "diagnose_environment",
    "await_environment_repair": "emit_blocker_report",
    "await_failing_test_or_bug_report": "emit_blocker_report",
    "configure_llm_patch_api_key": "emit_blocker_report",
    "refresh_llm_patch_credentials": "diagnose_llm_provider_failure",
    "switch_llm_patch_provider_or_model": "diagnose_llm_provider_failure",
    "retry_llm_patch_generation_with_backoff": "retry_llm_patch_generation",
    "retry_llm_patch_generation_with_smaller_context": "retry_llm_patch_generation",
    "extend_failure_overlay_or_provide_bug_report": "emit_blocker_report",
    "inspect_generated_artifacts": "emit_blocker_report",
    "generate_and_validate_patches": "validate_patch_in_sandbox",
    "run_patch_reflection_loop": "run_llm_patch_reflection_loop",
    "expand_patch_candidates_or_reflection": "run_llm_patch_reflection_loop",
    "regenerate_safe_patch_candidates": "generate_hybrid_patch_candidates",
    "run_search_and_ablation_evaluation": "generate_final_agent_report",
}


AUTO_EXECUTABLE_ACTION_IDS = {
    "adjust_source_filters",
    "expand_static_candidate_search",
    "mine_static_bug_signals",
    "build_static_graph_fault_ranking",
    "adjust_application_source_focus",
    "discover_repository_tests",
    "run_repository_tests_with_checkout",
    "collect_dynamic_failure_evidence",
    "generate_controlled_failure_overlay",
    "build_dynamic_fault_localization",
    "generate_and_validate_patches",
    "generate_llm_patch_candidates",
    "generate_hybrid_patch_candidates",
    "run_patch_reflection_loop",
    "run_llm_patch_reflection_loop",
    "regenerate_safe_patch_candidates",
    "narrow_repository_tests_after_timeout",
    "run_search_and_ablation_evaluation",
    "convert_passing_tests_to_regression_guard",
    "prepare_repository_test_environment",
}

HIGH_RISK_ACTION_IDS = {
    "prepare_repository_test_environment",
}

MEDIUM_RISK_CANONICAL_ACTION_IDS = {
    "clone_or_load_repository",
    "run_repository_tests",
    "generate_llm_patch_candidates",
    "retry_llm_patch_generation",
    "generate_hybrid_patch_candidates",
    "validate_patch_in_sandbox",
    "run_llm_patch_reflection_loop",
    "run_llm_patch_judge",
}

ACTION_ARGUMENT_ALLOWLIST = {
    "clone_or_load_repository": {"ref"},
    "discover_repository_structure": {"scope"},
    "discover_tests": {"scope"},
    "diagnose_environment": {"timeout_seconds"},
    "run_repository_tests": {"timeout_seconds"},
    "localize_fault": {"scope", "top_k"},
    "generate_llm_patch_candidates": {"candidate_limit", "strategy"},
    "diagnose_llm_provider_failure": {"provider", "model"},
    "retry_llm_patch_generation": {"candidate_limit", "strategy"},
    "generate_hybrid_patch_candidates": {"candidate_limit", "strategy"},
    "validate_patch_in_sandbox": {
        "candidate_ids",
        "timeout_seconds",
    },
    "run_llm_patch_reflection_loop": {
        "reflection_rounds",
        "reflection_width",
        "strategy",
    },
    "run_llm_patch_judge": {"candidate_ids"},
    "emit_blocker_report": set(),
    "generate_final_agent_report": set(),
}


@dataclass(frozen=True)
class AgentActionSpec:
    action_id: str
    phase: str
    tool: str
    module: str
    input_requirements: list[str]
    expected_artifact: str
    success_condition: str
    failure_condition: str
    blocker_type: str
    retry_policy: str
    next_possible_actions: list[str]
    aliases: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ACTION_SPECS = [
    AgentActionSpec(
        action_id="clone_or_load_repository",
        phase="source_discovery",
        tool="github_repo_intelligence",
        module="code_intelligence_agent.evaluation.github_repo_intelligence",
        input_requirements=["repo_spec", "output_dir"],
        expected_artifact="repository_checkout.json or discovery/raw-source artifacts",
        success_condition="Repository sources are materialized or cached raw sources are available.",
        failure_condition="GitHub fetch, checkout, or cache discovery cannot provide analyzable files.",
        blocker_type="repository_checkout_blocker",
        retry_policy="retry with token/cache, then emit blocker if sources remain unavailable",
        next_possible_actions=["discover_repository_structure", "emit_blocker_report"],
        aliases=["retry_repository_checkout_or_use_cache", "retry_with_github_token_or_cache"],
    ),
    AgentActionSpec(
        action_id="discover_repository_structure",
        phase="phase1",
        tool="github_repo_intelligence",
        module="code_intelligence_agent.evaluation.github_repo_intelligence",
        input_requirements=["repository_sources"],
        expected_artifact="repository_structure.json",
        success_condition="Python sources, package layout, and project config signals are summarized.",
        failure_condition="No analyzable Python source or project structure is found.",
        blocker_type="source_import_blocker",
        retry_policy="relax include/exclude/target-prefix filters before blocker",
        next_possible_actions=["discover_tests", "localize_fault", "emit_blocker_report"],
        aliases=["adjust_source_filters", "narrow_repository_scope"],
    ),
    AgentActionSpec(
        action_id="discover_tests",
        phase="phase3",
        tool="github_repo_intelligence",
        module="code_intelligence_agent.evaluation.github_repo_intelligence",
        input_requirements=["repository_structure", "project_config"],
        expected_artifact="repository_test_discovery.json/md or repository_test_execution_plan.json/md",
        success_condition="A safe pytest/unittest/tox/nox command or no-test blocker is recorded.",
        failure_condition="No runnable or discoverable test entrypoint can be found.",
        blocker_type="test_discovery_blocker",
        retry_policy="retry with checkout and broadened test discovery; otherwise emit blocker",
        next_possible_actions=["diagnose_environment", "run_repository_tests", "emit_blocker_report"],
        aliases=["discover_repository_tests"],
    ),
    AgentActionSpec(
        action_id="diagnose_environment",
        phase="phase3",
        tool="github_repo_intelligence",
        module="code_intelligence_agent.evaluation.github_repo_intelligence",
        input_requirements=["test_command_candidate", "project_config"],
        expected_artifact="repository_test_environment.json/md or repository_test_environment_repair_plan.json/md",
        success_condition="Dependency, runner, checkout, or setup blocker is classified with a recovery plan.",
        failure_condition="The test environment cannot be made executable without external changes.",
        blocker_type="environment_blocker",
        retry_policy="create setup/repair plan, then rerun tests after environment changes",
        next_possible_actions=["run_repository_tests", "emit_blocker_report"],
        aliases=["diagnose_test_execution_failure", "prepare_repository_test_environment", "await_environment_repair"],
    ),
    AgentActionSpec(
        action_id="run_repository_tests",
        phase="phase3",
        tool="github_repo_intelligence",
        module="code_intelligence_agent.evaluation.github_repo_intelligence",
        input_requirements=["repository_checkout", "safe_test_command"],
        expected_artifact="repository_test_execution_result.json/md",
        success_condition="Repository tests produce passing guard evidence or failing dynamic evidence.",
        failure_condition="Tests fail before localization, time out, collect no tests, or require missing dependencies.",
        blocker_type="test_execution_blocker",
        retry_policy="narrow timeout/test scope or diagnose environment before blocker",
        next_possible_actions=["localize_fault", "diagnose_environment", "emit_blocker_report"],
        aliases=[
            "rerun_repository_tests_from_session",
            "run_repository_tests_with_checkout",
            "collect_dynamic_failure_evidence",
            "convert_passing_tests_to_regression_guard",
            "generate_controlled_failure_overlay",
            "narrow_repository_tests_after_timeout",
        ],
    ),
    AgentActionSpec(
        action_id="localize_fault",
        phase="phase2",
        tool="github_repo_intelligence",
        module="code_intelligence_agent.evaluation.github_repo_intelligence",
        input_requirements=["static_signals", "program_graph", "dynamic_evidence_optional"],
        expected_artifact="fault_localization.json/md",
        success_condition="Top-k suspicious functions are ranked with signal contributions.",
        failure_condition="No localizable function has enough static or dynamic evidence.",
        blocker_type="fault_localization_blocker",
        retry_policy="collect dynamic evidence or broaden source focus before blocker",
        next_possible_actions=["generate_llm_patch_candidates", "generate_hybrid_patch_candidates", "emit_blocker_report"],
        aliases=[
            "adjust_application_source_focus",
            "build_dynamic_fault_localization",
            "build_static_graph_fault_ranking",
            "expand_static_candidate_search",
            "run_dynamic_fault_localization",
            "mine_static_bug_signals",
        ],
    ),
    AgentActionSpec(
        action_id="generate_llm_patch_candidates",
        phase="phase3",
        tool="repository_test_patch_candidates",
        module="code_intelligence_agent.evaluation.repository_test_patch_candidates",
        input_requirements=["topk_suspicious_functions", "failing_test_or_oracle", "CIA_LLM_API_KEY_or_DEEPSEEK_API_KEY"],
        expected_artifact="repository_test_patch_candidates.json/md",
        success_condition="LLM candidates are generated, parsed, scoped, and safety-gated.",
        failure_condition="LLM config is missing, JSON is invalid, patch is unsafe, or no candidate is produced.",
        blocker_type="llm_patch_blocker",
        retry_policy="retry with corrected key/context or switch to hybrid/blocker",
        next_possible_actions=["validate_patch_in_sandbox", "run_llm_patch_reflection_loop", "emit_blocker_report"],
        aliases=[],
    ),
    AgentActionSpec(
        action_id="diagnose_llm_provider_failure",
        phase="phase3",
        tool="llm_provider_diagnostics",
        module="code_intelligence_agent.agents.controller",
        input_requirements=[
            "llm_generation_telemetry",
            "provider",
            "model",
            "base_url",
            "api_key_fingerprint_optional",
        ],
        expected_artifact="agent_policy_trace.json/md with classified LLM provider blocker",
        success_condition=(
            "LLM provider failure is classified as credential, rate-limit, "
            "network, provider, timeout, or response-schema blocker with a "
            "safe recovery action."
        ),
        failure_condition="The Agent cannot classify why the provider request failed.",
        blocker_type="llm_provider_blocker",
        retry_policy=(
            "refresh credentials or provider configuration for auth failures; "
            "otherwise retry only through explicit provider recovery policy"
        ),
        next_possible_actions=[
            "retry_llm_patch_generation",
            "generate_hybrid_patch_candidates",
            "emit_blocker_report",
        ],
        aliases=[
            "refresh_llm_patch_credentials",
            "switch_llm_patch_provider_or_model",
        ],
    ),
    AgentActionSpec(
        action_id="retry_llm_patch_generation",
        phase="phase3",
        tool="repository_test_patch_candidates",
        module="code_intelligence_agent.evaluation.repository_test_patch_candidates",
        input_requirements=[
            "topk_suspicious_functions",
            "failing_test_or_oracle",
            "llm_generation_telemetry",
            "retry_budget",
        ],
        expected_artifact="repository_test_patch_candidates.json/md",
        success_condition=(
            "LLM patch generation is retried with bounded candidate count, "
            "provider backoff, or stricter JSON repair instructions."
        ),
        failure_condition="Provider failure repeats or retry budget is exhausted.",
        blocker_type="llm_patch_retry_blocker",
        retry_policy=(
            "retry transient failures once with smaller context/backoff; switch "
            "to hybrid fallback or blocker if the same class repeats"
        ),
        next_possible_actions=[
            "validate_patch_in_sandbox",
            "diagnose_llm_provider_failure",
            "generate_hybrid_patch_candidates",
            "emit_blocker_report",
        ],
        aliases=[
            "retry_llm_patch_generation_with_backoff",
            "retry_llm_patch_generation_with_smaller_context",
        ],
    ),
    AgentActionSpec(
        action_id="generate_hybrid_patch_candidates",
        phase="phase3",
        tool="repository_test_patch_candidates",
        module="code_intelligence_agent.evaluation.repository_test_patch_candidates",
        input_requirements=["topk_suspicious_functions", "failing_test_or_oracle"],
        expected_artifact="repository_test_patch_candidates.json/md",
        success_condition="Rule and/or LLM candidates are generated with generator provenance preserved.",
        failure_condition="All candidate generators fail or LLM is blocked and no safe rule fallback exists.",
        blocker_type="hybrid_patch_blocker",
        retry_policy="keep rule fallback distinct from LLM blocker; do not count rule patch as LLM success",
        next_possible_actions=["validate_patch_in_sandbox", "run_llm_patch_reflection_loop", "emit_blocker_report"],
        aliases=[
            "regenerate_safe_patch_candidates",
            "change_repair_strategy",
            "continue_repair_with_patch_memory",
        ],
    ),
    AgentActionSpec(
        action_id="validate_patch_in_sandbox",
        phase="phase3",
        tool="repository_test_patch_validation",
        module="code_intelligence_agent.evaluation.repository_test_patch_validation",
        input_requirements=["patch_candidates", "repository_checkout", "narrow_pytest_args"],
        expected_artifact="repository_test_patch_validation.json/md",
        success_condition="At least one candidate passes sandbox pytest after safety gates.",
        failure_condition="All candidates fail safety gate, patch apply, pytest, or timeout.",
        blocker_type="patch_validation_blocker",
        retry_policy="reflect failed patch or expand candidate generation",
        next_possible_actions=["run_llm_patch_reflection_loop", "run_llm_patch_judge", "generate_final_agent_report", "emit_blocker_report"],
        aliases=["generate_and_validate_patches"],
    ),
    AgentActionSpec(
        action_id="run_llm_patch_reflection_loop",
        phase="phase3",
        tool="repository_test_patch_validation",
        module="code_intelligence_agent.evaluation.repository_test_patch_validation",
        input_requirements=["failed_patch", "pytest_feedback", "target_function_context", "CIA_LLM_API_KEY_or_DEEPSEEK_API_KEY"],
        expected_artifact="reflection_trace.json/md",
        success_condition="Refined LLM candidates are generated, safety-gated, and sandbox validated.",
        failure_condition="Reflection key is missing, refined patches repeat failures, or attempts are exhausted.",
        blocker_type="llm_reflection_blocker",
        retry_policy="avoid repeated diff fingerprints; stop with blocker when attempts are exhausted",
        next_possible_actions=["validate_patch_in_sandbox", "run_llm_patch_judge", "emit_blocker_report"],
        aliases=["expand_patch_candidates_or_reflection", "run_patch_reflection_loop"],
    ),
    AgentActionSpec(
        action_id="run_llm_patch_judge",
        phase="phase3",
        tool="repository_test_patch_validation",
        module="code_intelligence_agent.search.patch_judge",
        input_requirements=["candidate_summary", "localization_score", "safety_gate_summary", "execution_feedback_summary"],
        expected_artifact="patch_judgment fields in repository_test_patch_validation.json",
        success_condition="Judge emits score/verdict/reason and sandbox remains the final authority.",
        failure_condition="Judge config is unavailable or output cannot be parsed.",
        blocker_type="llm_judge_blocker",
        retry_policy="disable judge without changing sandbox success semantics",
        next_possible_actions=["validate_patch_in_sandbox", "generate_final_agent_report"],
        aliases=[],
    ),
    AgentActionSpec(
        action_id="emit_blocker_report",
        phase="terminal",
        tool="github_repo_agent_controller",
        module="code_intelligence_agent.agents.controller",
        input_requirements=["classified_blocker", "next_action_guidance"],
        expected_artifact="github_repo_agent_controller.json/md and agent_policy_trace.json/md",
        success_condition="Blocker, recovery policy, and next action are explicitly recorded without fake success.",
        failure_condition="The Agent cannot classify why repair or analysis stopped.",
        blocker_type="terminal_blocker",
        retry_policy="request external input or environment change matching blocker type",
        next_possible_actions=["clone_or_load_repository", "discover_tests", "diagnose_environment"],
        aliases=[
            "inspect_generated_artifacts",
            "await_failing_test_or_bug_report",
            "await_environment_repair",
            "configure_llm_patch_api_key",
            "extend_failure_overlay_or_provide_bug_report",
        ],
    ),
    AgentActionSpec(
        action_id="generate_final_agent_report",
        phase="phase4",
        tool="github_repo_intelligence",
        module="code_intelligence_agent.evaluation.github_repo_intelligence",
        input_requirements=["verified_repair_or_terminal_blocker", "artifact_inventory"],
        expected_artifact="github_repo_intelligence.json/md",
        success_condition="Final report links localization, patch, sandbox, reflection, judge, and blocker evidence.",
        failure_condition="Required evidence artifacts are missing or contradict the reported status.",
        blocker_type="reporting_blocker",
        retry_policy="refresh inventory and acceptance gate before final report",
        next_possible_actions=["run_llm_patch_judge", "emit_blocker_report"],
        aliases=["run_search_and_ablation_evaluation"],
    ),
]


def build_agent_action_registry() -> dict[str, Any]:
    actions = [spec.to_dict() for spec in ACTION_SPECS]
    action_ids = {spec.action_id for spec in ACTION_SPECS}
    policy_action_ids = sorted(action_ids | set(ACTION_ALIASES))
    execution_policies = [
        action_execution_policy(action_id) for action_id in policy_action_ids
    ]
    required_status = {
        action_id: action_id in action_ids for action_id in REQUIRED_ACTION_IDS
    }
    return {
        "status": "pass" if all(required_status.values()) else "incomplete",
        "reason": (
            "all_required_agent_actions_registered"
            if all(required_status.values())
            else "missing_required_agent_actions"
        ),
        "required_action_ids": REQUIRED_ACTION_IDS,
        "required_action_coverage": required_status,
        "action_count": len(actions),
        "alias_count": sum(len(spec.aliases) for spec in ACTION_SPECS),
        "actions": actions,
        "execution_policy_count": len(execution_policies),
        "execution_policies": execution_policies,
    }


def canonical_action_id(action_id: Any) -> str:
    text = str(action_id or "")
    return ACTION_ALIASES.get(text, text)


def action_spec_for(action_id: Any) -> dict[str, Any]:
    canonical = canonical_action_id(action_id)
    for spec in ACTION_SPECS:
        if spec.action_id == canonical:
            return spec.to_dict()
    return {}


def action_execution_policy(action_id: Any) -> dict[str, Any]:
    requested = str(action_id or "")
    canonical = canonical_action_id(requested)
    registered = bool(action_spec_for(requested))
    if requested in HIGH_RISK_ACTION_IDS:
        risk = "high"
    elif canonical in MEDIUM_RISK_CANONICAL_ACTION_IDS:
        risk = "medium"
    else:
        risk = "low"
    return {
        "action_id": requested,
        "canonical_action_id": canonical,
        "registered": registered,
        "risk": risk,
        "requires_confirmation": risk == "high",
        "auto_executable": requested in AUTO_EXECUTABLE_ACTION_IDS,
        "allowed_argument_keys": sorted(
            ACTION_ARGUMENT_ALLOWLIST.get(canonical, set())
        ),
    }


def validate_action_arguments(
    action_id: Any,
    arguments: Any,
) -> tuple[dict[str, Any], list[str]]:
    policy = action_execution_policy(action_id)
    if not isinstance(arguments, dict):
        return {}, ["arguments_not_object"]
    allowed = set(policy["allowed_argument_keys"])
    unknown = sorted(set(arguments) - allowed)
    errors = ["unknown_argument_keys"] if unknown else []
    normalized: dict[str, Any] = {}
    for key, value in arguments.items():
        if key not in allowed:
            continue
        if key == "scope":
            path = _safe_relative_path(value)
            if path:
                normalized[key] = path
            elif value:
                errors.append(f"unsafe_{key}")
            continue
        if key in {
            "timeout_seconds",
            "top_k",
            "candidate_limit",
            "reflection_rounds",
            "reflection_width",
        }:
            number = _bounded_integer(key, value)
            if number is None:
                errors.append(f"invalid_{key}")
            else:
                normalized[key] = number
            continue
        if key == "candidate_ids":
            if not isinstance(value, list):
                errors.append("candidate_ids_not_list")
                continue
            candidates = [
                item
                for item in (_safe_identifier(item) for item in value[:20])
                if item
            ]
            if len(candidates) != len(value[:20]):
                errors.append("unsafe_candidate_id")
            if candidates:
                normalized[key] = candidates
            continue
        if key == "ref":
            ref = str(value or "").strip()
            if re.fullmatch(r"[A-Za-z0-9_./-]{1,160}", ref) and ".." not in ref:
                normalized[key] = ref
            elif value:
                errors.append("unsafe_ref")
            continue
        if key in {"strategy", "provider", "model"}:
            text = _safe_text(value, limit=300)
            if text:
                normalized[key] = text
            elif value:
                errors.append(f"unsafe_{key}")
    return normalized, list(dict.fromkeys(errors))


def _safe_relative_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or len(text) > 300:
        return ""
    if text.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", text):
        return ""
    if ".." in text.split("/"):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_./*-]+", text):
        return ""
    return text.strip("/")


def _bounded_integer(key: str, value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    limits = {
        "timeout_seconds": (1, 3600),
        "top_k": (1, 100),
        "candidate_limit": (1, 20),
        "reflection_rounds": (1, 5),
        "reflection_width": (1, 10),
    }
    minimum, maximum = limits[key]
    return number if minimum <= number <= maximum else None


def _safe_identifier(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", text) else ""


def _safe_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text or len(text) > limit or any(ord(char) < 32 for char in text):
        return ""
    if re.search(r"[;&|<>`]", text):
        return ""
    return text


def build_agent_policy_trace(
    *,
    observations: list[dict[str, str]],
    selected_action: dict[str, Any],
    verification: dict[str, Any],
    reflection: dict[str, Any],
    replan: dict[str, Any],
    termination: dict[str, Any],
    llm_repair_action_audit: dict[str, Any] | None = None,
    action_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    registry = action_registry or build_agent_action_registry()
    action_id = str(selected_action.get("id") or "")
    canonical = canonical_action_id(action_id)
    spec = action_spec_for(action_id)
    registered = bool(spec)
    loop_steps = {
        "observe": _observe_summary(observations),
        "plan": _plan_summary(selected_action, canonical, spec),
        "act": _act_summary(selected_action, spec),
        "verify": _verify_summary(verification, spec),
        "reflect": _reflect_summary(reflection),
        "replan": _replan_summary(replan, termination),
    }
    missing_loop_steps = [key for key, value in loop_steps.items() if not value]
    status = "pass" if registered and not missing_loop_steps else "warning"
    return {
        "status": status,
        "reason": (
            "policy_trace_complete"
            if status == "pass"
            else "policy_trace_incomplete_or_unregistered_action"
        ),
        "selected_action_id": action_id,
        "canonical_action_id": canonical,
        "selected_action_registered": registered,
        "policy_rule": _policy_rule_for(canonical, selected_action, observations),
        "registry_status": str(registry.get("status") or ""),
        "registry_action_count": _int(registry.get("action_count", 0)),
        "missing_loop_steps": missing_loop_steps,
        "action_spec": spec,
        "loop": loop_steps,
        "llm_repair_action_audit": llm_repair_action_audit or {},
    }


def render_agent_action_registry_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Agent Action Registry",
        "",
        f"- Status: `{_markdown_cell(payload.get('status'))}`",
        f"- Reason: `{_markdown_cell(payload.get('reason'))}`",
        f"- Action Count: {_int(payload.get('action_count', 0))}",
        f"- Alias Count: {_int(payload.get('alias_count', 0))}",
        "",
        "## Required Coverage",
        "",
        "| Action | Registered |",
        "| --- | ---: |",
    ]
    coverage = _dict(payload.get("required_action_coverage"))
    for action_id in _list(payload.get("required_action_ids")):
        lines.append(
            f"| `{_markdown_cell(action_id)}` | "
            f"{str(bool(coverage.get(str(action_id), False))).lower()} |"
        )
    lines.extend(
        [
            "",
            "## Actions",
            "",
            "| Action | Phase | Tool | Expected Artifact | Blocker | Retry Policy | Next Actions |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in _list(payload.get("actions")):
        row = _dict(item)
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{_markdown_cell(row.get('action_id'))}`",
                    _markdown_cell(row.get("phase")),
                    _markdown_cell(row.get("tool")),
                    _markdown_cell(row.get("expected_artifact")),
                    _markdown_cell(row.get("blocker_type")),
                    _markdown_cell(row.get("retry_policy")),
                    _markdown_cell(", ".join(str(x) for x in _list(row.get("next_possible_actions")))),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Execution Policies",
            "",
            "| Action | Canonical | Risk | Auto Executable | Confirmation | Allowed Arguments |",
            "| --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for item in _list(payload.get("execution_policies")):
        row = _dict(item)
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{_markdown_cell(row.get('action_id'))}`",
                    f"`{_markdown_cell(row.get('canonical_action_id'))}`",
                    _markdown_cell(row.get("risk")),
                    str(bool(row.get("auto_executable", False))).lower(),
                    str(bool(row.get("requires_confirmation", False))).lower(),
                    _markdown_cell(
                        ", ".join(
                            str(value)
                            for value in _list(row.get("allowed_argument_keys"))
                        )
                        or "none"
                    ),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def render_agent_policy_trace_markdown(payload: dict[str, Any]) -> str:
    spec = _dict(payload.get("action_spec"))
    loop = _dict(payload.get("loop"))
    audit = _dict(payload.get("llm_repair_action_audit"))
    lines = [
        "# Agent Policy Trace",
        "",
        f"- Status: `{_markdown_cell(payload.get('status'))}`",
        f"- Reason: `{_markdown_cell(payload.get('reason'))}`",
        f"- Selected Action: `{_markdown_cell(payload.get('selected_action_id'))}`",
        f"- Canonical Action: `{_markdown_cell(payload.get('canonical_action_id'))}`",
        f"- Registered: {str(bool(payload.get('selected_action_registered', False))).lower()}",
        f"- Policy Rule: `{_markdown_cell(payload.get('policy_rule'))}`",
        f"- Registry Status: `{_markdown_cell(payload.get('registry_status'))}`",
        "",
        "## Action Contract",
        "",
        f"- Phase: `{_markdown_cell(spec.get('phase'))}`",
        f"- Tool: `{_markdown_cell(spec.get('tool'))}`",
        f"- Module: `{_markdown_cell(spec.get('module'))}`",
        f"- Expected Artifact: `{_markdown_cell(spec.get('expected_artifact'))}`",
        f"- Success Condition: {_markdown_cell(spec.get('success_condition'))}",
        f"- Failure Condition: {_markdown_cell(spec.get('failure_condition'))}",
        f"- Blocker Type: `{_markdown_cell(spec.get('blocker_type'))}`",
        f"- Retry Policy: {_markdown_cell(spec.get('retry_policy'))}",
        "",
        "## Observe -> Plan -> Act -> Verify -> Reflect -> Replan",
        "",
        "| Step | Evidence |",
        "| --- | --- |",
    ]
    for step in ["observe", "plan", "act", "verify", "reflect", "replan"]:
        lines.append(f"| {step} | {_markdown_cell(loop.get(step))} |")
    if audit:
        lines.extend(
            [
                "",
                "## LLM Repair Action Audit",
                "",
                f"- Status: `{_markdown_cell(audit.get('status'))}`",
                f"- Repair Action: `{_markdown_cell(audit.get('repair_action_id'))}`",
                f"- Reflection Action: `{_markdown_cell(audit.get('reflection_action_id'))}`",
                f"- Blocker: `{_markdown_cell(audit.get('blocker') or 'none')}`",
            ]
        )
    return "\n".join(lines) + "\n"


def write_agent_action_registry_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "agent_action_registry.json"
    markdown_path = root / "agent_action_registry.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_path.write_text(render_agent_action_registry_markdown(payload), encoding="utf-8")
    return {
        "agent_action_registry_json": str(json_path),
        "agent_action_registry_markdown": str(markdown_path),
    }


def write_agent_policy_trace_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "agent_policy_trace.json"
    markdown_path = root / "agent_policy_trace.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_path.write_text(render_agent_policy_trace_markdown(payload), encoding="utf-8")
    return {
        "agent_policy_trace_json": str(json_path),
        "agent_policy_trace_markdown": str(markdown_path),
    }


def _policy_rule_for(
    canonical_action: str,
    selected_action: dict[str, Any],
    observations: list[dict[str, str]],
) -> str:
    obs = {str(item.get("signal") or ""): str(item.get("value") or "") for item in observations}
    if canonical_action == "generate_llm_patch_candidates":
        return "topk_dynamic_evidence_with_llm_patch_config"
    if canonical_action == "diagnose_llm_provider_failure":
        return "llm_provider_failure_requires_classified_recovery"
    if canonical_action == "retry_llm_patch_generation":
        return "recoverable_llm_provider_failure_retry_with_budget_controls"
    if canonical_action == "generate_hybrid_patch_candidates":
        if obs.get("repository_llm_patch_generation_status") == "blocked":
            return "hybrid_mode_preserves_rule_fallback_and_records_llm_blocker"
        return "hybrid_patch_generation_after_fault_localization"
    if canonical_action == "run_llm_patch_reflection_loop":
        return "patch_validation_failed_and_llm_reflection_ready"
    if canonical_action == "emit_blocker_report":
        return f"classified_blocker:{obs.get('blocker') or selected_action.get('reason') or 'unknown'}"
    if canonical_action == "diagnose_environment":
        return "test_environment_or_execution_blocker_requires_diagnosis"
    if canonical_action == "discover_tests":
        return "test_entrypoint_missing_or_not_collected"
    if canonical_action == "run_repository_tests":
        return "static_or_repository_state_needs_dynamic_test_evidence"
    if canonical_action == "localize_fault":
        return "source_and_signal_state_needs_fault_localization"
    if canonical_action == "generate_final_agent_report":
        return "repair_or_terminal_state_ready_for_final_report"
    return canonical_action or "unclassified_policy_rule"


def _observe_summary(observations: list[dict[str, str]]) -> str:
    obs = {str(item.get("signal") or ""): str(item.get("value") or "") for item in observations}
    parts = [
        f"stage={obs.get('current_stage') or 'unknown'}",
        f"blocker={obs.get('blocker') or 'none'}",
        f"dynamic={obs.get('dynamic_evidence_level') or 'none'}",
        f"fault={obs.get('fault_localization_mode') or 'none'}/{obs.get('fault_localization_status') or 'none'}",
        f"llm_patch={obs.get('repository_llm_patch_generation_status') or 'none'}",
        f"llm_reflection={obs.get('repository_llm_reflection_status') or 'none'}",
    ]
    return "; ".join(parts)


def _plan_summary(
    selected_action: dict[str, Any],
    canonical_action: str,
    spec: dict[str, Any],
) -> str:
    return (
        f"selected={selected_action.get('id') or 'none'}; "
        f"canonical={canonical_action or 'none'}; "
        f"inputs={', '.join(str(x) for x in _list(spec.get('input_requirements'))) or 'none'}; "
        f"reason={selected_action.get('reason') or 'none'}"
    )


def _act_summary(selected_action: dict[str, Any], spec: dict[str, Any]) -> str:
    return (
        f"tool={selected_action.get('tool') or spec.get('tool') or 'none'}; "
        f"executable={str(bool(selected_action.get('executable_now', False))).lower()}; "
        f"command={selected_action.get('command') or 'none'}; "
        f"expected_artifact={spec.get('expected_artifact') or 'none'}"
    )


def _verify_summary(verification: dict[str, Any], spec: dict[str, Any]) -> str:
    return (
        f"status={verification.get('status') or 'none'}; "
        f"condition={verification.get('success_condition') or spec.get('success_condition') or 'none'}; "
        f"artifact={verification.get('expected_artifact') or spec.get('expected_artifact') or 'none'}"
    )


def _reflect_summary(reflection: dict[str, Any]) -> str:
    return (
        f"status={reflection.get('status') or 'ready'}; "
        f"hypothesis={reflection.get('failure_hypothesis') or 'none'}; "
        f"fallback={reflection.get('fallback_action') or 'none'}"
    )


def _replan_summary(replan: dict[str, Any], termination: dict[str, Any]) -> str:
    return (
        f"trigger={replan.get('trigger') or 'none'}; "
        f"policy={replan.get('next_policy') or 'none'}; "
        f"next={termination.get('next_action') or replan.get('fallback_action') or 'none'}"
    )


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
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").strip()
