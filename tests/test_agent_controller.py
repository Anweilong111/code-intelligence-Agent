import json
from pathlib import Path

from code_intelligence_agent.agents.action_registry import (
    REQUIRED_ACTION_IDS,
    action_execution_policy,
    validate_action_arguments,
)
from code_intelligence_agent.agents.llm_client import StaticLLMClient
from code_intelligence_agent.agents.controller import (
    build_agent_controller_plan,
    render_agent_controller_markdown,
    write_agent_controller_artifacts,
)


def _repair_ready_summary() -> dict:
    return {
        "repo": "example/project",
        "repo_spec": "example/project",
        "analysis_readiness": {
            "current_stage": "phase3_patch_validation",
            "next_stage": "phase4_search_and_evaluation",
            "blocker": "",
            "repair_ready": True,
            "repair_validation_scope": "narrow_and_unchanged_regression_baseline",
        },
        "fault_localization": {
            "mode": "dynamic",
            "status": "pass",
            "top_function": "fix_me",
        },
    }


def _llm_patch_planning_summary() -> dict:
    return {
        "repo": "example/project",
        "repo_spec": "example/project",
        "repository_patch_generation_mode": "llm",
        "analysis_readiness": {
            "current_stage": "phase2_dynamic_fault_localization",
            "next_stage": "phase3_patch_validation",
            "blocker": "",
            "can_attempt_patch_repair": True,
        },
        "fault_localization": {
            "mode": "dynamic",
            "status": "pass",
            "top_function": "pkg.core.load_user",
            "rankings": [
                {
                    "function": "pkg.core.load_user",
                    "final_score": 0.91,
                }
            ],
        },
        "repository_llm_patch_generation_audit": {
            "status": "ready",
            "provider": "test",
            "model": "planner-test",
        },
    }


def test_agent_controller_reflects_verified_repair_without_false_blocker():
    controller = build_agent_controller_plan(
        _repair_ready_summary()
    )
    trace = {item["phase"]: item for item in controller["decision_trace"]}

    assert controller["control_loop"] == [
        "observe",
        "plan",
        "act",
        "verify",
        "reflect",
        "replan",
    ]
    assert controller["selected_action"]["id"] == "run_search_and_ablation_evaluation"
    assert controller["verification"]["status"] == "verified"
    assert controller["reflection"]["status"] == "verified_progress"
    assert controller["reflection"]["fallback_action"] == (
        "run_search_and_ablation_evaluation"
    )
    assert "no corrective reflection is required" in (
        controller["reflection"]["failure_hypothesis"]
    )
    assert "Current blocker needs" not in controller["reflection"][
        "failure_hypothesis"
    ]
    assert trace["reflect"]["status"] == "verified_progress"
    assert trace["reflect"]["output"] == "run_search_and_ablation_evaluation"
    assert trace["replan"]["decision"] == "advance_to_next_stage"
    loop_audit = controller["loop_iteration_audit"]
    assert loop_audit["status"] == "pass"
    assert loop_audit["source"] == "agent_controller"
    assert loop_audit["iteration_count"] == 1
    assert loop_audit["complete_iteration_count"] == 1
    assert loop_audit["executed_iteration_count"] == 0
    assert loop_audit["iterations"][0]["action_id"] == (
        "run_search_and_ablation_evaluation"
    )
    assert loop_audit["iterations"][0]["act_status"] == "planned"
    assert "stage=phase3_patch_validation" in (
        loop_audit["iterations"][0]["observe"]
    )
    assert "policy=advance_to_next_stage" in (
        loop_audit["iterations"][0]["replan"]
    )

    markdown = render_agent_controller_markdown(controller)

    assert "Status: `verified_progress`" in markdown
    assert "no corrective reflection is required" in markdown
    assert "Current blocker needs" not in markdown
    assert "## Loop Iteration Audit" in markdown
    assert "run_search_and_ablation_evaluation" in markdown


def test_agent_controller_llm_replan_advisor_disabled_by_default():
    controller = build_agent_controller_plan(_repair_ready_summary())

    advisor = controller["llm_replan_advisor"]
    assert advisor["status"] == "disabled"
    assert advisor["reason"] == "llm_replan_disabled"
    assert advisor["authority"] == "advisory_only_controller_policy_decides"

    markdown = render_agent_controller_markdown(controller)

    assert "## LLM Replan Advisor" in markdown
    assert "Status: `disabled`" in markdown


def test_agent_controller_llm_replan_advisor_records_static_advice():
    client = StaticLLMClient(
        json.dumps(
            {
                "recommended_action": "run_search_and_ablation_evaluation",
                "rationale": "Patch validation is ready, so quantify search behavior.",
                "confidence": 0.91,
                "risk": "low",
                "arguments": {},
                "blocker": "",
                "required_evidence": ["repository_test_patch_validation.json"],
                "expected_outcome": "Search and ablation evidence is recorded.",
                "fallback_action": "generate_final_agent_report",
                "termination_condition": "Stop after evaluation artifacts are written.",
                "memory_used": [],
                "next_plan": "Run Phase 4 search metrics and ablation.",
                "should_override_controller": False,
            }
        )
    )

    controller = build_agent_controller_plan(
        _repair_ready_summary(),
        llm_replan_client=client,
    )

    advisor = controller["llm_replan_advisor"]
    assert advisor["status"] == "pass"
    assert advisor["reason"] == "llm_replan_advice_recorded"
    assert advisor["authority"] == "advisory_only_controller_policy_decides"
    assert advisor["planner_type"] == "llm_planner_replanner"
    assert advisor["controller_authority"] == "rules_and_sandbox_gate_decide"
    assert advisor["advice"]["recommended_action"] == (
        "run_search_and_ablation_evaluation"
    )
    assert advisor["planner_decision"]["selected_action"] == (
        "run_search_and_ablation_evaluation"
    )
    assert advisor["planner_decision"]["required_evidence"] == [
        "repository_test_patch_validation.json"
    ]
    assert advisor["safety_gate"]["status"] == "pass"
    assert advisor["safety_gate"]["controller_action_match"] is True
    assert advisor["safety_gate"]["override_allowed"] is False
    assert advisor["advice"]["confidence"] == 0.91
    assert advisor["advice"]["should_override_controller"] is False
    assert client.prompts
    assert "advise_on_next_agent_replan_action" in client.prompts[0]
    assert "sandbox pytest" in client.prompts[0]

    markdown = render_agent_controller_markdown(controller)

    assert "Status: `pass`" in markdown
    assert "Recommended Action: `run_search_and_ablation_evaluation`" in markdown
    assert "Safety Gate: `pass`" in markdown
    assert "Run Phase 4 search metrics and ablation." in markdown


def test_agent_controller_blocks_unsafe_llm_planner_transition():
    client = StaticLLMClient(
        json.dumps(
            {
                "selected_action": "run_repository_tests_with_checkout",
                "reason": "The model wants to rerun tests before evaluation.",
                "confidence": 0.77,
                "risk": "medium",
                "arguments": {},
                "required_evidence": ["repository_test_execution_result.json"],
                "expected_outcome": "Fresh repository test evidence is recorded.",
                "fallback_action": "generate_final_agent_report",
                "termination_condition": "Stop after tests or a terminal blocker.",
                "memory_used": [],
                "next_plan": "Rerun repository tests before phase 4.",
                "should_override_controller": True,
            }
        )
    )

    controller = build_agent_controller_plan(
        _repair_ready_summary(),
        llm_replan_client=client,
    )

    advisor = controller["llm_replan_advisor"]

    assert controller["selected_action"]["id"] == "run_search_and_ablation_evaluation"
    assert advisor["status"] == "pass"
    assert advisor["planner_decision"]["selected_action"] == (
        "run_repository_tests_with_checkout"
    )
    assert advisor["safety_gate"]["status"] == "blocked"
    assert advisor["safety_gate"]["reason"] == (
        "unsafe_action_transition"
    )
    assert advisor["safety_gate"]["override_requested"] is True
    assert advisor["safety_gate"]["override_allowed"] is False
    assert advisor["safety_gate"]["adopted_action"] == (
        "run_search_and_ablation_evaluation"
    )


def test_agent_controller_llm_replan_advisor_blocks_without_env_key(monkeypatch):
    for name in [
        "CIA_REPLAN_LLM_API_KEY",
        "CIA_LLM_API_KEY",
        "DEEPSEEK_API_KEY",
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CIA_LLM_REPLAN_ENABLED", "1")

    controller = build_agent_controller_plan(_repair_ready_summary())

    advisor = controller["llm_replan_advisor"]
    assert advisor["status"] == "blocked"
    assert advisor["reason"] == "missing_llm_replan_api_key"
    assert advisor["blocker"] == "missing_llm_replan_api_key"
    assert "CIA_REPLAN_LLM_API_KEY" in advisor["checked_api_key_envs"]
    assert "CIA_LLM_API_KEY" in advisor["checked_api_key_envs"]
    assert "DEEPSEEK_API_KEY" in advisor["checked_api_key_envs"]
    assert advisor["config"]["api_key_present"] is False


def test_agent_auto_defaults_llm_planner_with_rule_fallback(monkeypatch):
    for name in [
        "CIA_REPLAN_LLM_API_KEY",
        "CIA_LLM_API_KEY",
        "DEEPSEEK_API_KEY",
        "CIA_LLM_REPLAN_ENABLED",
    ]:
        monkeypatch.delenv(name, raising=False)
    summary = _repair_ready_summary()
    summary["agent_invocation"] = {
        "effective_execution_profile": "agent-auto",
        "agent_mode": True,
        "auto_controller_actions": True,
    }

    controller = build_agent_controller_plan(summary)

    advisor = controller["llm_replan_advisor"]
    assert advisor["enabled"] is True
    assert advisor["status"] == "blocked"
    assert advisor["reason"] == "missing_llm_replan_api_key"
    assert advisor["fallback_to_rule_planner"] is True
    assert advisor["planner_decision"]["proposal_source"] == "rule_fallback"
    assert advisor["llm_planner_proposal"] == advisor["planner_decision"]
    assert advisor["planner_decision"]["selected_action"] == (
        controller["selected_action"]["id"]
    )
    assert advisor["safety_gate"]["status"] == "fallback"
    assert advisor["safety_gate"]["adopted_action"] == (
        controller["selected_action"]["id"]
    )

    markdown = render_agent_controller_markdown(controller)

    assert "Fallback To Rule Planner: true" in markdown
    assert "Proposal Source: `rule_fallback`" in markdown


def test_agent_controller_llm_planner_prompt_uses_memory_context():
    client = StaticLLMClient(
        json.dumps(
            {
                "selected_action": "run_search_and_ablation_evaluation",
                "reason": "Memory shows a failed patch was already tried.",
                "confidence": 0.86,
                "risk": "low",
                "arguments": {},
                "required_evidence": ["repository_test_patch_validation.json"],
                "expected_outcome": "Evaluation is recorded without retrying the failed patch.",
                "fallback_action": "generate_final_agent_report",
                "termination_condition": "Stop after evaluation artifacts are written.",
                "memory_used": [
                    "repair_memory",
                    "repair_strategy_preferences",
                ],
                "next_plan": "Avoid the failed guard-shape patch and evaluate.",
                "should_override_controller": False,
            }
        )
    )
    summary = _repair_ready_summary()
    summary["agent_invocation"] = {
        "effective_execution_profile": "agent-auto",
        "agent_mode": True,
    }
    summary["agent_session"] = {
        "session_id": "example-session",
        "turn_count": 3,
    }
    summary["agent_memory_report"] = {
        "memory_layers": {
            "session_memory": {
                "session_id": "example-session",
                "turn_count": 3,
                "constraints": ["do not modify public API"],
                "repair_strategy_preferences": ["prefer guard clause"],
            },
            "repo_memory": {
                "repo": "example/project",
                "test_command": "python -m pytest tests",
                "test_status": "fail",
            },
            "repair_memory": {
                "failed_patch_count": 1,
                "patch_attempt_count": 1,
                "failed_patch_fingerprints": ["diff-fp-1"],
                "strategy_preferences": ["prefer guard clause"],
                "user_constraints": ["do not modify public API"],
                "latest_failure_category": "assertion_failure",
            },
        }
    }

    controller = build_agent_controller_plan(summary, llm_replan_client=client)

    prompt = json.loads(client.prompts[0])
    memory = prompt["memory_context"]
    advisor = controller["llm_replan_advisor"]

    assert memory["available"] is True
    assert memory["session_memory"]["session_id"] == "example-session"
    assert memory["repo_memory"]["test_command"] == "python -m pytest tests"
    assert memory["repair_memory"]["failed_patch_count"] == 1
    assert memory["repair_memory"]["failed_patch_fingerprints"] == ["diff-fp-1"]
    assert memory["repair_memory"]["constraints"] == ["do not modify public API"]
    assert memory["repair_memory"]["repair_strategy_preferences"] == [
        "prefer guard clause"
    ]
    assert prompt["response_schema"]["memory_used"] == [
        "string memory source or fact used for the decision"
    ]
    assert advisor["planner_decision"]["memory_used"] == [
        "repair_memory",
        "repair_strategy_preferences",
    ]
    assert advisor["llm_planner_proposal"] == advisor["planner_decision"]
    assert advisor["safety_gate"]["status"] == "pass"


def test_agent_controller_rejects_incomplete_planner_schema_and_falls_back():
    client = StaticLLMClient(
        json.dumps(
            {
                "selected_action": "generate_hybrid_patch_candidates",
                "reason": "Try a hybrid candidate.",
                "next_plan": "Generate candidates.",
            }
        )
    )

    controller = build_agent_controller_plan(
        _llm_patch_planning_summary(),
        llm_replan_client=client,
        planner_mode="hybrid",
    )

    advisor = controller["llm_replan_advisor"]
    assert controller["selected_action"]["id"] == "generate_llm_patch_candidates"
    assert advisor["status"] == "error"
    assert advisor["reason"] == "missing_required_replan_fields"
    assert advisor["provider_failure_class"] == "response_schema"
    assert advisor["fallback_to_rule_planner"] is True
    assert controller["planner_metrics"]["fallback_count"] == 1


def test_hybrid_planner_adopts_safe_registered_llm_alternative():
    client = StaticLLMClient(
        json.dumps(
            {
                "selected_action": "generate_hybrid_patch_candidates",
                "arguments": {
                    "candidate_limit": 3,
                    "strategy": "combine rule and model candidates",
                },
                "reason": "Hybrid generation keeps a deterministic fallback.",
                "confidence": 0.93,
                "risk": "medium",
                "required_evidence": ["repository_test_fault_localization.json"],
                "expected_outcome": "At least one AST-valid patch candidate.",
                "fallback_action": "generate_llm_patch_candidates",
                "termination_condition": "Stop after verified repair or exhausted budget.",
                "memory_used": ["repo_memory"],
                "next_plan": "Generate candidates, then validate in the sandbox.",
            }
        )
    )

    controller = build_agent_controller_plan(
        _llm_patch_planning_summary(),
        llm_replan_client=client,
        planner_mode="hybrid",
    )

    assert controller["rule_selected_action"]["id"] == "generate_llm_patch_candidates"
    assert controller["selected_action"]["id"] == "generate_hybrid_patch_candidates"
    assert controller["selected_action"]["proposal_source"] == "llm"
    assert controller["selected_action"]["arguments"] == {
        "candidate_limit": 3,
        "strategy": "combine rule and model candidates",
    }
    assert controller["planner_resolution"]["adopted_source"] == "llm"
    assert controller["planner_resolution"]["disagreement"] is True
    gate = controller["llm_replan_advisor"]["safety_gate"]
    assert gate["status"] == "pass"
    assert gate["reason"] == "safe_registered_llm_alternative_adopted"
    assert gate["override_allowed"] is True
    assert gate["adopted_action"] == "generate_hybrid_patch_candidates"


def test_hybrid_planner_uses_stricter_disagreement_threshold_than_llm_mode():
    response = json.dumps(
        {
            "selected_action": "generate_hybrid_patch_candidates",
            "arguments": {},
            "reason": "Try a registered alternative with moderate confidence.",
            "confidence": 0.7,
            "risk": "medium",
            "required_evidence": ["repository_test_fault_localization.json"],
            "expected_outcome": "Generate a distinct patch candidate.",
            "fallback_action": "generate_llm_patch_candidates",
            "termination_condition": "Stop on verified success or budget exhaustion.",
            "memory_used": [],
            "next_plan": "Generate and validate one candidate batch.",
        }
    )

    llm_controller = build_agent_controller_plan(
        _llm_patch_planning_summary(),
        llm_replan_client=StaticLLMClient(response),
        planner_mode="llm",
    )
    hybrid_controller = build_agent_controller_plan(
        _llm_patch_planning_summary(),
        llm_replan_client=StaticLLMClient(response),
        planner_mode="hybrid",
    )

    llm_gate = llm_controller["llm_replan_advisor"]["safety_gate"]
    hybrid_gate = hybrid_controller["llm_replan_advisor"]["safety_gate"]
    assert llm_controller["selected_action"]["id"] == (
        "generate_hybrid_patch_candidates"
    )
    assert llm_gate["confidence_threshold"] == 0.65
    assert hybrid_controller["selected_action"]["id"] == (
        "generate_llm_patch_candidates"
    )
    assert hybrid_gate["confidence_threshold"] == 0.75
    assert hybrid_gate["reason"] == "llm_planner_confidence_below_threshold"
    assert hybrid_controller["planner_metrics"]["fallback_count"] == 1


def test_planner_high_risk_action_requires_confirmation_and_keeps_rule_action():
    client = StaticLLMClient(
        json.dumps(
            {
                "selected_action": "prepare_repository_test_environment",
                "arguments": {"timeout_seconds": 120},
                "reason": "Install or repair the repository test environment.",
                "confidence": 0.9,
                "risk": "low",
                "required_evidence": ["repository_test_environment.json"],
                "expected_outcome": "A runnable test environment.",
                "fallback_action": "emit_blocker_report",
                "termination_condition": "Stop if external dependencies remain unavailable.",
                "memory_used": [],
                "next_plan": "Prepare the environment, then rerun tests.",
            }
        )
    )

    summary = {
        "repo": "example/project",
        "repo_spec": "example/project",
        "analysis_readiness": {
            "current_stage": "phase2_static_graph_fault_localization",
            "next_stage": "phase3_repository_test_execution",
            "blocker": "test_execution_failed",
            "dynamic_evidence_level": "collection_failure",
            "planned_repository_test_result_status": "fail",
            "planned_repository_test_failure_category": "collection_failure",
            "planned_repository_test_result_errors": 1,
        },
        "fault_localization": {
            "mode": "static_fallback",
            "status": "pass",
            "top_function": "pkg.core.load_user",
        },
    }

    controller = build_agent_controller_plan(
        summary,
        llm_replan_client=client,
        planner_mode="hybrid",
    )

    gate = controller["llm_replan_advisor"]["safety_gate"]
    assert controller["selected_action"]["id"] == "diagnose_test_execution_failure"
    assert gate["status"] == "requires_confirmation"
    assert gate["policy_risk"] == "high"
    assert gate["model_claimed_risk"] == "low"
    assert gate["requires_confirmation"] is True
    assert gate["override_allowed"] is False


def test_planner_budget_and_repeated_state_prevent_unbounded_action_choice():
    response = json.dumps(
        {
            "selected_action": "generate_hybrid_patch_candidates",
            "arguments": {},
            "reason": "Try hybrid candidates.",
            "confidence": 0.9,
            "risk": "medium",
            "required_evidence": [],
            "expected_outcome": "New candidates.",
            "fallback_action": "generate_llm_patch_candidates",
            "termination_condition": "Stop on budget exhaustion.",
            "memory_used": [],
            "next_plan": "Generate candidates.",
        }
    )
    budget_summary = _llm_patch_planning_summary()
    budget_summary["agent_invocation"] = {
        "auto_controller_max_actions": 1,
        "planner_mode": "hybrid",
    }
    budget_summary["agent_auto_action_count"] = 1
    budget_client = StaticLLMClient(response)
    budget_controller = build_agent_controller_plan(
        budget_summary,
        llm_replan_client=budget_client,
    )

    repeated_summary = _llm_patch_planning_summary()
    repeated_summary["agent_auto_trace"] = [
        {
            "observe_stage": "phase2_dynamic_fault_localization",
            "observe_blocker": "",
            "plan_selected_action": "generate_hybrid_patch_candidates",
        }
    ]
    repeated_controller = build_agent_controller_plan(
        repeated_summary,
        llm_replan_client=StaticLLMClient(response),
        planner_mode="hybrid",
    )

    budget_gate = budget_controller["llm_replan_advisor"]["safety_gate"]
    repeated_gate = repeated_controller["llm_replan_advisor"]["safety_gate"]
    assert budget_gate["status"] == "blocked"
    assert budget_gate["reason"] == "planner_budget_exhausted"
    assert budget_client.prompts == []
    assert budget_controller["planner_metrics"]["llm_called"] is False
    assert budget_controller["planner_resolution"]["adopted_source"] == "rule"
    assert repeated_gate["status"] == "blocked"
    assert repeated_gate["reason"] == "repeated_action_in_unchanged_failure_state"
    assert repeated_controller["selected_action"]["id"] == "generate_llm_patch_candidates"


def test_planner_allows_same_action_after_observation_state_changes():
    response = json.dumps(
        {
            "selected_action": "generate_hybrid_patch_candidates",
            "arguments": {},
            "reason": "New evidence supports trying the action again.",
            "confidence": 0.9,
            "risk": "medium",
            "required_evidence": ["repository_test_execution_result.json"],
            "expected_outcome": "A candidate grounded in the new failure evidence.",
            "fallback_action": "generate_llm_patch_candidates",
            "termination_condition": "Stop on verified success or exhausted budget.",
            "memory_used": [],
            "next_plan": "Generate candidates from the updated observation.",
        }
    )
    summary = _llm_patch_planning_summary()
    summary["agent_auto_trace"] = [
        {
            "observe_stage": "phase2_dynamic_fault_localization",
            "observe_blocker": "",
            "observe_state_fingerprint": "different-observation",
            "plan_selected_action": "generate_hybrid_patch_candidates",
        }
    ]

    controller = build_agent_controller_plan(
        summary,
        llm_replan_client=StaticLLMClient(response),
        planner_mode="hybrid",
    )

    gate = controller["llm_replan_advisor"]["safety_gate"]
    assert gate["repeated_state_action"] is False
    assert gate["status"] == "pass"
    assert controller["selected_action"]["id"] == "generate_hybrid_patch_candidates"
    assert len(controller["planner_state_fingerprint"]) == 16


def test_action_registry_validates_planner_arguments_and_risk_policy():
    policy = action_execution_policy("prepare_repository_test_environment")
    valid, valid_errors = validate_action_arguments(
        "generate_hybrid_patch_candidates",
        {"candidate_limit": 4, "strategy": "minimal diff"},
    )
    invalid, invalid_errors = validate_action_arguments(
        "generate_hybrid_patch_candidates",
        {"candidate_limit": 1000, "shell": "rm -rf"},
    )

    assert policy["registered"] is True
    assert policy["risk"] == "high"
    assert policy["requires_confirmation"] is True
    assert valid == {"candidate_limit": 4, "strategy": "minimal diff"}
    assert valid_errors == []
    assert invalid == {}
    assert "unknown_argument_keys" in invalid_errors
    assert "invalid_candidate_limit" in invalid_errors


def test_planner_prompt_contains_required_evidence_and_budget_inputs():
    client = StaticLLMClient(
        json.dumps(
            {
                "selected_action": "generate_llm_patch_candidates",
                "arguments": {},
                "reason": "Use the current dynamic localization evidence.",
                "confidence": 0.9,
                "risk": "medium",
                "required_evidence": ["fault_localization.json"],
                "expected_outcome": "Patch candidates are generated.",
                "fallback_action": "generate_hybrid_patch_candidates",
                "termination_condition": "Stop after sandbox success.",
                "memory_used": ["repo_memory"],
                "next_plan": "Generate and validate candidates.",
            }
        )
    )
    summary = _llm_patch_planning_summary()
    summary["agent_invocation"] = {
        "planner_mode": "hybrid",
        "auto_controller_max_actions": 4,
        "agent_time_budget_seconds": 600,
        "agent_llm_cost_budget_usd": 1.5,
        "user_goal": "repair the failing repository test",
    }
    summary["repository_structure"] = {
        "layout": "src_layout",
        "function_count": 10,
        "repo_graph": {
            "program_graph": {"available": True, "node_count": 20, "edge_count": 30},
            "top_function_nodes": [{"function": "pkg.core.load_user", "degree": 5}],
        },
    }
    summary["planned_repository_test_result_status"] = "fail"
    summary["planned_repository_test_failure_signal"] = "AssertionError: missing user"

    build_agent_controller_plan(summary, llm_replan_client=client)

    prompt = json.loads(client.prompts[0])
    assert prompt["user_goal"] == "repair the failing repository test"
    assert prompt["repository_profile"]["layout"] == "src_layout"
    assert prompt["fault_localization"]["top_k"][0]["function"] == "pkg.core.load_user"
    assert prompt["fault_localization"]["program_graph_neighborhood"]["available"] is True
    assert prompt["pytest_evidence"]["status"] == "fail"
    assert "AssertionError" in prompt["pytest_evidence"]["traceback_tail"]
    assert prompt["budget_context"]["remaining_actions"] == 4
    assert prompt["budget_context"]["remaining_time_seconds"] == 600
    assert prompt["budget_context"]["remaining_llm_cost_usd"] == 1.5
    assert any(
        item["action_id"] == "generate_hybrid_patch_candidates"
        for item in prompt["action_registry_candidates"]
    )
    assert set(prompt["response_schema"]).issuperset(
        {
            "selected_action",
            "arguments",
            "reason",
            "confidence",
            "risk",
            "required_evidence",
            "expected_outcome",
            "fallback_action",
            "termination_condition",
            "memory_used",
        }
    )


def test_agent_controller_embeds_action_registry_and_policy_trace():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "example/project",
            "analysis_readiness": {
                "current_stage": "phase3_patch_validation",
                "next_stage": "phase4_search_and_evaluation",
                "blocker": "",
                "repair_ready": True,
                "repair_validation_scope": "narrow_and_unchanged_regression_baseline",
            },
            "fault_localization": {
                "mode": "dynamic",
                "status": "pass",
                "top_function": "fix_me",
            },
        }
    )

    registry = controller["action_registry"]
    policy = controller["policy_trace"]

    assert registry["status"] == "pass"
    assert set(REQUIRED_ACTION_IDS) <= set(registry["required_action_ids"])
    assert all(registry["required_action_coverage"][item] for item in REQUIRED_ACTION_IDS)
    assert policy["status"] == "pass"
    assert policy["selected_action_id"] == "run_search_and_ablation_evaluation"
    assert policy["canonical_action_id"] == "generate_final_agent_report"
    assert policy["action_spec"]["expected_artifact"]
    assert "observe" in policy["loop"]
    assert "replan" in policy["loop"]

    markdown = render_agent_controller_markdown(controller)

    assert "## Action Registry / Policy Trace" in markdown
    assert "generate_final_agent_report" in markdown


def test_agent_controller_writes_action_registry_and_policy_trace_artifacts(tmp_path):
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "example/project",
            "analysis_readiness": {
                "current_stage": "phase3_patch_validation",
                "next_stage": "phase4_search_and_evaluation",
                "blocker": "",
                "repair_ready": True,
            },
            "fault_localization": {
                "mode": "dynamic",
                "status": "pass",
                "top_function": "fix_me",
            },
        }
    )

    paths = write_agent_controller_artifacts(controller, tmp_path)

    for key in [
        "agent_controller_json",
        "agent_controller_markdown",
        "agent_action_registry_json",
        "agent_action_registry_markdown",
        "agent_policy_trace_json",
        "agent_policy_trace_markdown",
    ]:
        assert Path(paths[key]).exists()
    registry = json.loads(Path(paths["agent_action_registry_json"]).read_text())
    policy = json.loads(Path(paths["agent_policy_trace_json"]).read_text())
    assert registry["status"] == "pass"
    assert policy["canonical_action_id"] == "generate_final_agent_report"


def test_agent_controller_records_action_decision_audit_for_selected_action():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "",
                "dynamic_evidence_level": "not_executed",
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "target",
            },
        }
    )

    audit = controller["action_decision_audit"]
    action = audit["actions"][0]

    assert audit["status"] == "pass"
    assert audit["source"] == "agent_controller"
    assert audit["required_fields"] == [
        "why_selected",
        "confidence",
        "risk",
        "input",
        "output",
        "blocker",
        "next_plan",
    ]
    assert action["action_id"] == "run_repository_tests_with_checkout"
    assert action["canonical_action_id"] == "run_repository_tests"
    assert action["registered"] is True
    assert action["complete"] is True
    assert action["confidence"] > 0
    assert action["risk"] == "medium"
    assert "stage=phase2_static_graph_fault_localization" in action["input"]
    assert "repository_test_dynamic_evidence.json" in action["output"]
    assert action["blocker_present"] is False
    assert "--execution-profile checkout" in action["next_plan"]
    assert controller["selected_action"]["confidence"] == action["confidence"]
    assert controller["selected_action"]["expected_output"] == action["output"]

    markdown = render_agent_controller_markdown(controller)

    assert "## Action Decision Audit" in markdown
    assert "run_repository_tests_with_checkout" in markdown
    assert "repository_test_dynamic_evidence.json" in markdown


def test_agent_controller_action_decision_audit_covers_auto_trace_actions():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "dynamic_evidence_not_usable:passing_tests",
                "dynamic_evidence_level": "passing_tests",
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "target",
            },
            "agent_auto_trace": [
                {
                    "iteration": 1,
                    "observe_stage": "phase2_static_graph_fault_localization",
                    "observe_blocker": "dynamic_evidence_not_usable:passing_tests",
                    "observe_dynamic_evidence_level": "passing_tests",
                    "observe_fault_localization_mode": "static_fallback",
                    "observe_fault_localization_status": "pass",
                    "plan_selected_action": "generate_controlled_failure_overlay",
                    "auto_executed": True,
                    "verify_outcome": "failure_overlay_attempted",
                    "verify_progress": True,
                    "replan_policy": "continue_observe_plan_act",
                    "verify_dynamic_evidence_level": "passing_tests",
                }
            ],
        }
    )

    audit = controller["action_decision_audit"]
    action = audit["actions"][0]

    assert audit["status"] == "pass"
    assert audit["source"] == "agent_auto_trace"
    assert action["action_id"] == "generate_controlled_failure_overlay"
    assert action["canonical_action_id"] == "run_repository_tests"
    assert action["registered"] is True
    assert action["complete"] is True
    assert action["confidence"] > 0.8
    assert action["risk"] == "medium"
    assert "Auto controller selected this action" in action["why_selected"]
    assert "dynamic=passing_tests" in action["input"]
    assert action["output"] == "failure_overlay_attempted"
    assert action["blocker"] == "dynamic_evidence_not_usable:passing_tests"
    assert action["next_plan"] == "continue_observe_plan_act"
    assert controller["policy_trace"]["canonical_action_id"] == "run_repository_tests"
    assert controller["policy_trace"]["status"] == "pass"


def test_agent_controller_prioritizes_environment_blocker_from_setup_doctor():
    controller = build_agent_controller_plan(
        {
            "repo": "pypa/sampleproject",
            "repo_spec": "https://github.com/pypa/sampleproject",
            "output_dir": "outputs/sampleproject",
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "dynamic_evidence_not_usable:collection_failure",
                "dynamic_evidence_level": "collection_failure",
                "repository_test_setup_doctor_status": "blocked",
                "repository_test_setup_doctor_blocker": (
                    "environment:test_tool_missing"
                ),
                "repository_test_setup_doctor_next_action": (
                    "Install or prepare `nox` before executing repository tests."
                ),
                "repository_test_setup_doctor_check_count": 8,
                "repository_test_setup_doctor_passed_check_count": 4,
                "repository_test_setup_doctor_warning_check_count": 1,
                "repository_test_setup_doctor_blocked_check_count": 3,
                "repository_test_setup_doctor_check_status_counts": {
                    "blocked": 3,
                    "pass": 4,
                    "warning": 1,
                },
                "repository_test_setup_doctor_blocked_check_names": [
                    "test_environment",
                    "environment_setup",
                    "execution_plan",
                ],
                "repository_test_setup_doctor_warning_check_names": [
                    "dynamic_evidence",
                ],
                "planned_repository_test_command": (
                    "python -m unittest discover -s tests -p __init__.py"
                ),
                "planned_repository_test_executable_now": True,
                "planned_repository_test_result_status": "fail",
            },
            "repository_test_environment_repair_plan": {
                "status": "pass",
                "blocker": "environment:test_tool_missing",
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "build_and_check_dists",
            },
        }
    )
    observations = {
        item["signal"]: item["value"] for item in controller["observations"]
    }

    assert controller["selected_action"]["id"] == "await_environment_repair"
    assert controller["selected_action"]["executable_now"] is False
    assert controller["status"] == "blocked"
    assert controller["verification"]["status"] == "manual_or_blocked"
    assert controller["reflection"]["fallback_action"] == (
        "prepare_repository_test_environment"
    )
    assert controller["replan"]["trigger"] == "environment:test_tool_missing"
    assert observations["repository_test_setup_doctor_status"] == "blocked"
    assert observations["repository_test_setup_doctor_blocker"] == (
        "environment:test_tool_missing"
    )
    assert observations["repository_test_setup_doctor_checks"] == (
        "pass=4/8, warning=1, blocked=3"
    )
    assert observations["repository_test_setup_doctor_check_status_counts"] == (
        "blocked=3, pass=4, warning=1"
    )
    assert observations["repository_test_setup_doctor_blocked_check_names"] == (
        "test_environment, environment_setup, execution_plan"
    )
    assert observations["repository_test_setup_doctor_warning_check_names"] == (
        "dynamic_evidence"
    )
    trace = {item["phase"]: item for item in controller["decision_trace"]}
    assert trace["plan"]["decision"] == "select `await_environment_repair`"


def test_agent_controller_observes_dependency_and_packaging_profile():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "repository_structure": {
                "project_config": {
                    "project_config_files": [
                        "pyproject.toml",
                        "uv.lock",
                        "tox.ini",
                    ],
                    "dependency_tool_signals": ["pyproject", "tox", "uv"],
                    "dependency_file_count": 2,
                    "packaging_file_count": 1,
                }
            },
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "dynamic_evidence_not_usable:passing_tests",
                "dynamic_evidence_level": "passing_tests",
                "dynamic_evidence_usable_for_localization": False,
                "static_signal_count": 2,
                "planned_repository_test_result_status": "pass",
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "target",
            },
        }
    )
    observations = {
        item["signal"]: item["value"] for item in controller["observations"]
    }

    assert observations["project_config_files"] == "pyproject.toml, uv.lock, tox.ini"
    assert observations["dependency_tool_signals"] == "pyproject, tox, uv"
    assert observations["dependency_file_count"] == "2"
    assert observations["packaging_file_count"] == "1"
    assert controller["decision_trace"][0]["phase"] == "observe"


def test_agent_controller_replans_from_agent_goal_readiness_gap():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "analysis_readiness": {
                "current_stage": "phase2_static_bug_signal_mining",
                "next_stage": "phase2_static_graph_fault_localization",
                "blocker": "",
                "dynamic_evidence_level": "not_executed",
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "warning",
                "top_function": "",
            },
            "agent_goal_readiness": {
                "status": "warning",
                "criteria_count": 13,
                "passed_criteria_count": 11,
                "failed_criteria_count": 2,
                "failed_criteria": [
                    "topk_fault_localization_or_actionable_blocker",
                    "test_environment_diagnosis",
                ],
            },
        }
    )
    observations = {
        item["signal"]: item["value"] for item in controller["observations"]
    }
    trace = {item["phase"]: item for item in controller["decision_trace"]}

    assert controller["selected_action"]["id"] == "build_static_graph_fault_ranking"
    assert observations["agent_goal_readiness_status"] == "warning"
    assert observations["agent_goal_readiness_criteria"] == (
        "pass=11/13, failed=2"
    )
    assert observations["agent_goal_readiness_failed_criteria"] == (
        "topk_fault_localization_or_actionable_blocker, test_environment_diagnosis"
    )
    assert controller["replan"]["trigger"] == (
        "agent_goal_readiness:topk_fault_localization_or_actionable_blocker"
    )
    assert controller["replan"]["next_policy"] == "close_agent_goal_readiness_gap"
    assert trace["replan"]["decision"] == "close_agent_goal_readiness_gap"

    markdown = render_agent_controller_markdown(controller)

    assert "agent_goal_readiness_failed_criteria" in markdown
    assert "close_agent_goal_readiness_gap" in markdown


def test_agent_controller_runs_executable_runner_fallback_before_environment_repair():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "dynamic_tests_not_executed",
                "dynamic_evidence_level": "not_executed",
                "repository_test_setup_doctor_status": "blocked",
                "repository_test_setup_doctor_blocker": (
                    "environment:test_tool_missing"
                ),
                "planned_repository_test_command": (
                    "python -m pytest -q tests/test_sample.py"
                ),
                "planned_repository_test_runner": "pytest",
                "planned_repository_test_preferred_runner": "tox",
                "planned_repository_test_runner_fallback_used": True,
                "planned_repository_test_runner_fallback_reason": (
                    "missing_runner:tox"
                ),
                "planned_repository_test_runner_fallback_from": "tox",
                "planned_repository_test_runner_fallback_to": "pytest",
                "planned_repository_test_executable_now": True,
            },
            "repository_test_environment_repair_plan": {
                "status": "pass",
                "blocker": "environment:test_tool_missing",
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "target",
            },
        }
    )
    observations = {
        item["signal"]: item["value"] for item in controller["observations"]
    }
    trace = {item["phase"]: item for item in controller["decision_trace"]}

    assert controller["selected_action"]["id"] == "run_repository_tests_with_checkout"
    assert controller["selected_action"]["executable_now"] is True
    assert "missing_runner:tox" in controller["selected_action"]["reason"]
    assert "python -m pytest -q tests/test_sample.py" in (
        controller["selected_action"]["reason"]
    )
    assert controller["verification"]["status"] == "pending_action"
    assert controller["reflection"]["fallback_action"] == (
        "collect_dynamic_failure_evidence"
    )
    assert controller["replan"]["trigger"] == "dynamic_tests_not_executed"
    assert observations["planned_repository_test_runner"] == "pytest"
    assert observations["planned_repository_test_runner_fallback"] == "True"
    assert observations["planned_repository_test_runner_fallback_reason"] == (
        "missing_runner:tox"
    )
    assert trace["act"]["decision"] == controller["selected_action"]["reason"]


def test_agent_controller_plans_timeout_narrowing_after_repository_timeout():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "dynamic_evidence_not_usable:timeout",
                "dynamic_evidence_level": "timeout",
                "dynamic_evidence_usable_for_localization": False,
                "planned_repository_test_command": "python -m pytest -q tests",
                "planned_repository_test_result_status": "timeout",
                "planned_repository_test_failure_category": "timeout",
                "repository_test_timeout_narrowing_status": "",
                "repository_test_timeout_narrowing_reason": "",
                "repository_test_timeout_narrowing_attempt_count": 0,
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "target",
            },
        }
    )
    observations = {
        item["signal"]: item["value"] for item in controller["observations"]
    }
    trace = {item["phase"]: item for item in controller["decision_trace"]}
    loop = controller["loop_iteration_audit"]["iterations"][0]

    assert controller["selected_action"]["id"] == (
        "narrow_repository_tests_after_timeout"
    )
    assert controller["selected_action"]["tool"] == (
        "repository_test_timeout_narrowing"
    )
    assert controller["selected_action"]["executable_now"] is True
    assert "repository_test_timeout_narrowing" in (
        controller["selected_action"]["reason"]
    )
    assert controller["verification"]["status"] == "pending_action"
    assert controller["verification"]["expected_artifact"] == (
        "repository_test_timeout_narrowing.json"
    )
    assert controller["reflection"]["fallback_action"] == (
        "narrow_repository_tests_after_timeout"
    )
    assert "narrower pytest" in controller["reflection"]["failure_hypothesis"]
    assert controller["replan"]["trigger"] == (
        "dynamic_evidence_not_usable:timeout"
    )
    assert observations["repository_test_timeout_narrowing_status"] == (
        "not_attempted"
    )
    assert observations["repository_test_timeout_narrowing_reason"] == "none"
    assert observations["repository_test_timeout_narrowing_attempts"] == (
        "attempts=0, selected=none, failure_category=none"
    )
    assert trace["plan"]["decision"] == (
        "select `narrow_repository_tests_after_timeout`"
    )
    assert "timeout_narrowing=none" in loop["observe"]

    markdown = render_agent_controller_markdown(controller)

    assert "narrow_repository_tests_after_timeout" in markdown
    assert "repository_test_timeout_narrowing.json" in markdown


def test_agent_controller_markdown_summarizes_auto_timeout_narrowing():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "dynamic_evidence_not_usable:timeout",
                "dynamic_evidence_level": "timeout",
                "dynamic_evidence_usable_for_localization": False,
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "target",
            },
            "agent_auto_actions": [
                {
                    "action_id": "narrow_repository_tests_after_timeout",
                    "before_blocker": "dynamic_evidence_not_usable:timeout",
                    "loop_verify_outcome": "timeout_narrowing_executed",
                    "loop_replan_policy": "continue_observe_plan_act",
                    "after_dynamic_evidence_level": "timeout",
                    "after_timeout_narrowing_status": "fail",
                    "after_timeout_narrowing_reason": (
                        "timeout_narrowing_selected_non_timeout_result"
                    ),
                    "after_timeout_narrowing_attempt_count": 2,
                    "after_timeout_narrowing_selected_failure_category": (
                        "test_assertion_failure"
                    ),
                    "after_patch_validation_status": "skipped",
                    "after_patch_validation_success_count": 0,
                    "after_repair_ready": False,
                }
            ],
            "agent_auto_trace": [
                {
                    "iteration": 1,
                    "observe_stage": "phase2_static_graph_fault_localization",
                    "observe_blocker": "dynamic_evidence_not_usable:timeout",
                    "plan_selected_action": "narrow_repository_tests_after_timeout",
                    "auto_executed": True,
                    "verify_outcome": "timeout_narrowing_executed",
                    "verify_progress": True,
                    "replan_policy": "continue_observe_plan_act",
                    "verify_dynamic_evidence_level": "timeout",
                    "verify_timeout_narrowing_status": "fail",
                    "verify_timeout_narrowing_reason": (
                        "timeout_narrowing_selected_non_timeout_result"
                    ),
                    "verify_timeout_narrowing_attempt_count": 2,
                    "verify_timeout_narrowing_selected_failure_category": (
                        "test_assertion_failure"
                    ),
                }
            ],
        }
    )

    markdown = render_agent_controller_markdown(controller)

    assert "Timeout Narrowing" in markdown
    assert "timeout_narrowing_executed" in markdown
    assert "timeout_narrowing_selected_non_timeout_result" in markdown
    assert "attempts=2, selected=test_assertion_failure" in markdown


def test_agent_controller_replans_failed_runner_fallback_to_environment_repair():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "dynamic_evidence_not_usable:collection_failure",
                "dynamic_evidence_level": "collection_failure",
                "repository_test_setup_doctor_status": "blocked",
                "repository_test_setup_doctor_blocker": (
                    "environment:test_tool_missing"
                ),
                "repository_test_setup_doctor_next_action": (
                    "Install or prepare `tox` before executing repository tests."
                ),
                "planned_repository_test_command": (
                    "python -m pytest -q tests/test_sample.py"
                ),
                "planned_repository_test_runner": "pytest",
                "planned_repository_test_preferred_runner": "tox",
                "planned_repository_test_runner_fallback_used": True,
                "planned_repository_test_runner_fallback_reason": (
                    "missing_runner:tox"
                ),
                "planned_repository_test_runner_fallback_from": "tox",
                "planned_repository_test_runner_fallback_to": "pytest",
                "planned_repository_test_executable_now": True,
                "planned_repository_test_result_status": "fail",
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "target",
            },
        }
    )
    observations = {
        item["signal"]: item["value"] for item in controller["observations"]
    }
    trace = {item["phase"]: item for item in controller["decision_trace"]}

    assert controller["selected_action"]["id"] == (
        "prepare_repository_test_environment"
    )
    assert controller["selected_action"]["executable_now"] is True
    assert controller["verification"]["status"] == "pending_action"
    assert controller["reflection"]["fallback_action"] == (
        "prepare_repository_test_environment"
    )
    assert controller["replan"]["trigger"] == "environment:test_tool_missing"
    assert observations["planned_repository_test_runner_fallback"] == "True"
    assert observations["planned_repository_test_runner_fallback_reason"] == (
        "missing_runner:tox"
    )
    assert trace["plan"]["decision"] == (
        "select `prepare_repository_test_environment`"
    )


def test_agent_controller_discovers_tests_when_command_is_missing():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "test_command:no_recommended_test_command",
                "dynamic_evidence_level": "not_executed",
                "repository_test_setup_doctor_status": "blocked",
                "repository_test_setup_doctor_blocker": (
                    "test_command:no_recommended_test_command"
                ),
                "repository_test_setup_doctor_next_action": (
                    "Add or infer a python -m pytest style repository test command."
                ),
                "planned_repository_test_command": "",
                "planned_repository_test_executable_now": False,
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "target",
            },
        }
    )
    trace = {item["phase"]: item for item in controller["decision_trace"]}

    assert controller["selected_action"]["id"] == "discover_repository_tests"
    assert controller["selected_action"]["executable_now"] is True
    assert controller["verification"]["expected_artifact"] == (
        "repository_test_execution_plan.json"
    )
    assert controller["verification"]["success_condition"] == (
        "repository profiling discovers a safe pytest/unittest/tox/nox command "
        "or records a no-test blocker"
    )
    assert controller["replan"]["trigger"] == (
        "test_command:no_recommended_test_command"
    )
    assert trace["plan"]["decision"] == "select `discover_repository_tests`"
    assert "repository test entrypoint is missing" in (
        controller["selected_action"]["reason"]
    )


def test_agent_controller_discovers_tests_for_p3_no_test_command_blocker():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "no_test_command",
                "dynamic_evidence_level": "not_executed",
                "planned_repository_test_command": "",
                "planned_repository_test_executable_now": False,
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "target",
            },
        }
    )

    assert controller["selected_action"]["id"] == "discover_repository_tests"
    assert controller["verification"]["expected_artifact"] == (
        "repository_test_execution_plan.json"
    )
    assert controller["replan"]["trigger"] == "no_test_command"


def test_agent_controller_retries_checkout_or_cache_for_checkout_failed_blocker():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "analysis_readiness": {
                "current_stage": "source_import_blocked",
                "next_stage": "source_discovery",
                "blocker": "checkout_failed",
                "repository_checkout_failure_reason": (
                    "git clone exited with status 128"
                ),
            },
            "fault_localization": {
                "mode": "none",
                "status": "skipped",
                "top_function": "",
            },
        }
    )
    trace = {item["phase"]: item for item in controller["decision_trace"]}

    assert controller["selected_action"]["id"] == (
        "retry_repository_checkout_or_use_cache"
    )
    assert controller["selected_action"]["tool"] == "github_repo_intelligence"
    assert controller["selected_action"]["executable_now"] is True
    assert "--prefer-cached-discovery" in controller["selected_action"]["command"]
    assert "git clone exited with status 128" in (
        controller["selected_action"]["reason"]
    )
    assert controller["verification"]["expected_artifact"] == (
        "github_repo_intelligence.json"
    )
    assert controller["reflection"]["fallback_action"] == "adjust_source_filters"
    assert controller["replan"]["trigger"] == "checkout_failed"
    assert trace["plan"]["decision"] == (
        "select `retry_repository_checkout_or_use_cache`"
    )


def test_agent_controller_routes_dependency_setup_failed_to_environment_repair():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "dynamic_evidence_not_usable:collection_failure",
                "dynamic_evidence_level": "collection_failure",
                "repository_test_setup_doctor_status": "blocked",
                "repository_test_setup_doctor_blocker": "dependency_setup_failed",
                "repository_test_setup_doctor_next_action": (
                    "Install package dependencies before executing tests."
                ),
                "planned_repository_test_command": "python -m pytest -q",
                "planned_repository_test_result_status": "fail",
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "target",
            },
        }
    )

    assert controller["selected_action"]["id"] == (
        "prepare_repository_test_environment"
    )
    assert controller["selected_action"]["executable_now"] is True
    assert controller["reflection"]["fallback_action"] == (
        "prepare_repository_test_environment"
    )
    assert controller["replan"]["trigger"] == "dependency_setup_failed"


def test_agent_controller_diagnoses_test_execution_failed_without_dynamic_signal():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "test_execution_failed",
                "dynamic_evidence_level": "collection_failure",
                "dynamic_evidence_usable_for_localization": False,
                "planned_repository_test_command": "python -m pytest -q",
                "planned_repository_test_result_status": "fail",
                "planned_repository_test_failure_category": "collection_failure",
                "planned_repository_test_result_failed": 0,
                "planned_repository_test_result_errors": 1,
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "target",
            },
        }
    )

    assert controller["selected_action"]["id"] == "diagnose_test_execution_failure"
    assert controller["selected_action"]["tool"] == "repository_test_execution_result"
    assert "category=collection_failure" in controller["selected_action"]["reason"]
    assert controller["verification"]["expected_artifact"] == (
        "repository_test_execution_result.json"
    )
    assert controller["reflection"]["fallback_action"] == (
        "collect_dynamic_failure_evidence"
    )
    assert controller["replan"]["trigger"] == "test_execution_failed"


def test_agent_controller_generates_patch_candidates_when_not_ready():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "analysis_readiness": {
                "current_stage": "phase3_patch_validation",
                "next_stage": "phase3_patch_validation",
                "blocker": "patch_candidates_not_ready",
                "dynamic_evidence_level": "failing_tests",
                "patch_validation_status": "skipped",
                "patch_validation_reason": "patch_candidates_not_ready",
                "patch_validation_input_candidate_count": 0,
                "patch_validation_candidate_count": 0,
                "repair_ready": False,
                "can_attempt_patch_repair": True,
            },
            "fault_localization": {
                "mode": "dynamic",
                "status": "pass",
                "top_function": "target",
            },
        }
    )

    assert controller["selected_action"]["id"] == "generate_and_validate_patches"
    assert controller["selected_action"]["executable_now"] is True
    assert "without a ready candidate set" in controller["selected_action"]["reason"]
    assert controller["verification"]["expected_artifact"] == (
        "repository_test_patch_validation.json"
    )
    assert controller["replan"]["trigger"] == "patch_candidates_not_ready"


def test_agent_controller_adjusts_source_filters_for_p3_no_python_source():
    controller = build_agent_controller_plan(
        {
            "repo": "example/docs",
            "repo_spec": "https://github.com/example/docs",
            "output_dir": "outputs/docs",
            "analysis_readiness": {
                "current_stage": "source_import_blocked",
                "next_stage": "phase1_repo_understanding",
                "blocker": "no_python_source",
            },
            "fault_localization": {
                "mode": "none",
                "status": "skipped",
                "top_function": "",
            },
        }
    )

    assert controller["control_loop"] == [
        "observe",
        "plan",
        "act",
        "verify",
        "reflect",
        "replan",
    ]
    assert controller["selected_action"]["id"] == "adjust_source_filters"
    assert controller["verification"]["expected_artifact"] == (
        "repository_structure.json"
    )
    assert controller["replan"]["trigger"] == "no_python_source"


def test_agent_controller_replans_when_patch_candidates_fail_safety_gate():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "analysis_readiness": {
                "current_stage": "phase3_patch_validation",
                "next_stage": "phase3_patch_reflection_or_expansion",
                "blocker": "patch_candidates_blocked_by_safety_gate",
                "dynamic_evidence_level": "assertion_failure",
                "patch_validation_status": "skipped",
                "patch_validation_reason": "all_candidates_blocked_by_safety_gate",
                "patch_validation_input_candidate_count": 1,
                "patch_validation_candidate_count": 0,
                "patch_validation_safety_blocked_candidate_count": 1,
                "repair_ready": False,
                "can_attempt_patch_repair": True,
            },
            "fault_localization": {
                "mode": "dynamic",
                "status": "pass",
                "top_function": "target",
            },
        }
    )
    observations = {
        item["signal"]: item["value"] for item in controller["observations"]
    }
    trace = {item["phase"]: item for item in controller["decision_trace"]}

    assert controller["selected_action"]["id"] == (
        "regenerate_safe_patch_candidates"
    )
    assert controller["selected_action"]["executable_now"] is True
    assert controller["verification"]["status"] == "repair_not_verified"
    assert controller["reflection"]["fallback_action"] == (
        "regenerate_safe_patch_candidates"
    )
    assert "pre-sandbox safety gate" in controller["reflection"][
        "failure_hypothesis"
    ]
    assert controller["replan"]["trigger"] == (
        "patch_candidates_blocked_by_safety_gate"
    )
    assert observations["patch_validation_candidates"] == (
        "input=1, validated=0, safety_blocked=1"
    )
    assert trace["plan"]["decision"] == (
        "select `regenerate_safe_patch_candidates`"
    )


def test_agent_controller_includes_primary_reflection_strategy_on_patch_failure():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "analysis_readiness": {
                "current_stage": "phase3_patch_validation",
                "next_stage": "phase3_patch_reflection_or_expansion",
                "blocker": "no_candidate_passed_repository_tests",
                "dynamic_evidence_level": "failing_tests",
                "patch_validation_status": "fail",
                "patch_validation_reason": "no_candidate_passed_repository_tests",
                "repair_ready": False,
                "can_attempt_patch_repair": True,
            },
            "reflection_summary": {
                "reflection_candidate_count": 0,
                "max_depth_executed": 0,
                "primary_reflection_strategy_id": "regenerate_ast_valid_patch",
                "primary_reflection_strategy_action": (
                    "Regenerate an AST-valid patch and run syntax parsing before sandbox validation."
                ),
            },
            "fault_localization": {
                "mode": "dynamic",
                "status": "pass",
                "top_function": "target",
            },
        }
    )

    selected = controller["selected_action"]
    assert selected["id"] == "run_patch_reflection_loop"
    assert selected["executable_now"] is True
    assert "regenerate_ast_valid_patch" in selected["reason"]
    assert "Regenerate an AST-valid patch" in selected["reason"]
    assert controller["reflection"]["fallback_action"] == (
        "expand_patch_candidates_or_reflection"
    )


def test_agent_controller_explains_hybrid_llm_patch_key_blocker():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "repository_patch_generation_mode": "hybrid",
            "repository_llm_patch_generation_status": "blocked",
            "repository_llm_patch_generation_reason": "missing_llm_api_key",
            "repository_llm_patch_generation_fallback_used": True,
            "repository_llm_patch_provider": "deepseek",
            "repository_llm_patch_model": "deepseek-v4-pro",
            "repository_llm_patch_api_key_env": "CIA_LLM_API_KEY",
            "repository_llm_patch_generation_audit": {
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "api_key_env": "CIA_LLM_API_KEY",
                "checked_api_key_envs": [
                    "CIA_LLM_API_KEY",
                    "DEEPSEEK_API_KEY",
                ],
                "fallback_used": True,
            },
            "analysis_readiness": {
                "current_stage": "phase2_dynamic_fault_localization",
                "next_stage": "phase3_patch_validation",
                "blocker": "",
                "dynamic_evidence_level": "failing_tests",
                "patch_validation_status": "",
                "repair_ready": False,
                "can_attempt_patch_repair": True,
            },
            "fault_localization": {
                "mode": "dynamic",
                "status": "pass",
                "top_function": "target",
            },
        }
    )

    selected = controller["selected_action"]
    observations = {
        item["signal"]: item["value"] for item in controller["observations"]
    }
    assert selected["id"] == "generate_hybrid_patch_candidates"
    assert selected["tool"] == "repository_test_patch_candidates"
    assert "deepseek/deepseek-v4-pro" in selected["reason"]
    assert "missing one of `CIA_LLM_API_KEY`, `DEEPSEEK_API_KEY`" in (
        selected["reason"]
    )
    assert "hybrid mode has rule-based fallback candidates" in selected["reason"]
    assert controller["verification"]["expected_artifact"] == (
        "repository_test_patch_candidates.json"
    )
    assert observations["repository_patch_generation_mode"] == "hybrid"
    assert observations["repository_llm_patch_generation_status"] == "blocked"
    assert observations["repository_llm_patch_generation_reason"] == (
        "missing_llm_api_key"
    )
    assert observations["repository_llm_patch_generation_fallback"] == (
        "blocked=True, fallback_used=True, provider=deepseek, "
        "model=deepseek-v4-pro"
    )
    audit = controller["llm_repair_action_audit"]
    assert audit["status"] == "blocked"
    assert audit["repair_action_id"] == "generate_hybrid_patch_candidates"
    assert audit["blocker"] == "missing_llm_api_key"
    assert "repair_action=generate_hybrid_patch_candidates" in (
        audit["agent_loop_evidence"]["plan"]
    )
    assert "authority=sandbox_pytest_decides_success" in (
        audit["agent_loop_evidence"]["verify"]
    )
    policy = controller["policy_trace"]
    assert policy["status"] == "pass"
    assert policy["selected_action_id"] == "generate_hybrid_patch_candidates"
    assert policy["canonical_action_id"] == "generate_hybrid_patch_candidates"
    assert policy["policy_rule"] == (
        "hybrid_mode_preserves_rule_fallback_and_records_llm_blocker"
    )


def test_agent_controller_blocks_llm_only_patch_generation_without_key():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "repository_patch_generation_mode": "llm",
            "repository_llm_patch_generation_status": "blocked",
            "repository_llm_patch_generation_reason": "missing_llm_api_key",
            "repository_llm_patch_provider": "deepseek",
            "repository_llm_patch_model": "deepseek-v4-pro",
            "repository_llm_patch_api_key_env": "CIA_LLM_API_KEY",
            "repository_llm_patch_generation_audit": {
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "api_key_env": "CIA_LLM_API_KEY",
                "checked_api_key_envs": [
                    "CIA_LLM_API_KEY",
                    "DEEPSEEK_API_KEY",
                ],
                "blocked": True,
                "blocker": "missing_llm_api_key",
            },
            "analysis_readiness": {
                "current_stage": "phase2_dynamic_fault_localization",
                "next_stage": "phase3_patch_validation",
                "blocker": "",
                "dynamic_evidence_level": "failing_tests",
                "patch_validation_status": "",
                "repair_ready": False,
                "can_attempt_patch_repair": True,
            },
            "fault_localization": {
                "mode": "dynamic",
                "status": "pass",
                "top_function": "target",
            },
        }
    )

    selected = controller["selected_action"]
    assert controller["status"] == "blocked"
    assert selected["id"] == "configure_llm_patch_api_key"
    assert selected["tool"] == "llm_config_audit"
    assert selected["executable_now"] is False
    assert "Set one of `CIA_LLM_API_KEY`, `DEEPSEEK_API_KEY`" in (
        selected["command"]
    )
    assert "LLM-only patch generation is requested" in selected["reason"]
    assert "missing_llm_api_key" in selected["reason"]
    assert "must not report rule or empty candidates as LLM repair success" in (
        selected["reason"]
    )
    assert controller["verification"]["success_condition"] == (
        "LLM patch configuration records a missing-key blocker without leaking "
        "raw secrets"
    )
    audit = controller["llm_repair_action_audit"]
    assert audit["status"] == "blocked"
    assert audit["repair_action_id"] == "configure_llm_patch_api_key"
    assert audit["api_key_present"] is False
    assert audit["sandbox_authority"] == "sandbox_pytest_decides_success"


def test_agent_controller_llm_repair_audit_uses_generator_counts():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "repository_patch_generation_mode": "llm",
            "repository_patch_generator_counts": {"llm": 1, "rule": 0},
            "repository_llm_patch_generation_status": "pass",
            "repository_llm_patch_generation_reason": (
                "llm_patch_candidates_generated"
            ),
            "repository_llm_patch_provider": "deepseek",
            "repository_llm_patch_model": "deepseek-v4-pro",
            "repository_llm_patch_api_key_present": True,
            "repository_test_patch_validation_status": "pass",
            "repository_test_patch_validation_executed_count": 1,
            "repository_test_patch_validation_success_count": 1,
            "repository_test_patch_judge_mode": "llm",
            "repository_test_patch_judge_status": "ready",
            "analysis_readiness": {
                "current_stage": "phase3_patch_validation",
                "next_stage": "phase4_evaluation",
                "blocker": "",
                "dynamic_evidence_level": "failing_tests",
                "patch_validation_status": "pass",
                "repair_ready": True,
                "can_attempt_patch_repair": True,
            },
            "fault_localization": {
                "mode": "dynamic",
                "status": "pass",
                "top_function": "target",
            },
        }
    )

    audit = controller["llm_repair_action_audit"]
    assert audit["status"] == "pass"
    assert audit["repair_action_id"] == "generate_llm_patch_candidates"
    assert audit["llm_candidate_count"] == 1
    assert audit["rule_candidate_count"] == 0
    assert "judge=llm/ready" in audit["agent_loop_evidence"]["verify"]


def test_agent_controller_treats_llm_request_error_telemetry_as_blocker():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "repository_patch_generation_mode": "llm",
            "repository_patch_generator_counts": {"llm": 0, "rule": 0},
            "repository_llm_patch_generation_status": "error",
            "repository_llm_patch_generation_reason": "http_error",
            "repository_llm_patch_provider": "deepseek",
            "repository_llm_patch_model": "deepseek-v4-pro",
            "repository_llm_patch_api_key_present": True,
            "repository_llm_patch_request_count": 1,
            "repository_llm_patch_failure_count": 1,
            "repository_llm_patch_total_tokens": 0,
            "repository_llm_patch_estimated_total_tokens": 32,
            "repository_llm_patch_error_reason_counts": {"http_401": 1},
            "analysis_readiness": {
                "current_stage": "phase2_dynamic_fault_localization",
                "next_stage": "phase3_patch_validation",
                "blocker": "",
                "dynamic_evidence_level": "failing_tests",
                "patch_validation_status": "",
                "repair_ready": False,
                "can_attempt_patch_repair": True,
            },
            "fault_localization": {
                "mode": "dynamic",
                "status": "pass",
                "top_function": "target",
            },
        }
    )
    observations = {
        item["signal"]: item["value"]
        for item in controller["observations"]
    }
    audit = controller["llm_repair_action_audit"]

    assert audit["status"] == "blocked"
    assert audit["reason"] == "http_error"
    assert audit["blocker"] == "http_error"
    assert audit["repair_action_id"] == "diagnose_llm_provider_failure"
    assert audit["llm_request_count"] == 1
    assert audit["llm_failure_count"] == 1
    assert audit["llm_estimated_total_tokens"] == 32
    assert audit["llm_error_reason_counts"] == {"http_401": 1}
    assert audit["llm_provider_failure_class"] == "credential"
    assert audit["llm_provider_primary_error"] == "http_401"
    assert audit["llm_provider_recovery_policy"] == (
        "refresh_llm_credentials_or_model_access"
    )
    assert "requests=1; failures=1" in audit["agent_loop_evidence"]["observe"]
    assert "failure_class=credential" in audit["agent_loop_evidence"]["observe"]
    assert "estimated_tokens=32" in audit["agent_loop_evidence"]["act"]
    assert "stop_with_blocker=http_error" in audit["agent_loop_evidence"]["replan"]
    assert "policy=refresh_llm_credentials_or_model_access" in (
        audit["agent_loop_evidence"]["replan"]
    )
    assert observations["repository_llm_patch_generation_telemetry"] == (
        "requests=1, failures=1, tokens=0, estimated_tokens=32, "
        "errors=http_401=1"
    )
    assert controller["status"] == "blocked"
    assert controller["selected_action"]["id"] == "diagnose_llm_provider_failure"
    assert controller["selected_action"]["executable_now"] is False
    assert "http_error" in controller["selected_action"]["reason"]
    assert controller["replan"]["trigger"] == "llm_patch_provider_failure:credential"
    assert controller["replan"]["next_policy"] == "repair_llm_provider_configuration"
    assert controller["policy_trace"]["canonical_action_id"] == (
        "diagnose_llm_provider_failure"
    )
    assert controller["policy_trace"]["policy_rule"] == (
        "llm_provider_failure_requires_classified_recovery"
    )


def test_agent_controller_retries_recoverable_llm_timeout_with_budget_controls():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "repository_patch_generation_mode": "llm",
            "repository_patch_generator_counts": {"llm": 0, "rule": 0},
            "repository_llm_patch_generation_status": "error",
            "repository_llm_patch_generation_reason": "timeout",
            "repository_llm_patch_provider": "deepseek",
            "repository_llm_patch_model": "deepseek-v4-pro",
            "repository_llm_patch_api_key_present": True,
            "repository_llm_patch_request_count": 1,
            "repository_llm_patch_failure_count": 1,
            "repository_llm_patch_total_tokens": 0,
            "repository_llm_patch_estimated_total_tokens": 128,
            "repository_llm_patch_error_reason_counts": {"timeout": 1},
            "analysis_readiness": {
                "current_stage": "phase2_dynamic_fault_localization",
                "next_stage": "phase3_patch_validation",
                "blocker": "",
                "dynamic_evidence_level": "failing_tests",
                "patch_validation_status": "",
                "repair_ready": False,
                "can_attempt_patch_repair": True,
            },
            "fault_localization": {
                "mode": "dynamic",
                "status": "pass",
                "top_function": "target",
            },
        }
    )

    selected = controller["selected_action"]
    audit = controller["llm_repair_action_audit"]

    assert controller["status"] == "ready"
    assert selected["id"] == "retry_llm_patch_generation"
    assert selected["executable_now"] is True
    assert selected["llm_provider_failure_class"] == "timeout"
    assert selected["llm_provider_recovery_policy"] == (
        "retry_with_smaller_context_or_higher_timeout"
    )
    assert audit["repair_action_id"] == "retry_llm_patch_generation"
    assert audit["llm_provider_failure_class"] == "timeout"
    assert audit["llm_provider_primary_error"] == "timeout"
    assert "failure_class=timeout" in audit["agent_loop_evidence"]["observe"]
    assert "llm_recovery_policy=retry_with_smaller_context_or_higher_timeout" in (
        audit["agent_loop_evidence"]["plan"]
    )
    assert controller["replan"]["trigger"] == "llm_patch_provider_failure:timeout"
    assert controller["replan"]["next_policy"] == (
        "reduce_llm_context_then_retry_generation"
    )
    assert controller["policy_trace"]["canonical_action_id"] == (
        "retry_llm_patch_generation"
    )
    assert controller["policy_trace"]["policy_rule"] == (
        "recoverable_llm_provider_failure_retry_with_budget_controls"
    )


def test_agent_controller_llm_repair_audit_ready_when_patch_action_selected():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "repository_patch_generation_mode": "llm",
            "repository_llm_patch_provider": "deepseek",
            "repository_llm_patch_model": "deepseek-v4-pro",
            "repository_llm_patch_api_key_present": True,
            "repository_llm_patch_generation_audit": {
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "api_key_present": True,
            },
            "analysis_readiness": {
                "current_stage": "phase2_dynamic_fault_localization",
                "next_stage": "phase3_patch_validation",
                "blocker": "",
                "dynamic_evidence_level": "failing_tests",
                "patch_validation_status": "",
                "repair_ready": False,
                "can_attempt_patch_repair": True,
            },
            "fault_localization": {
                "mode": "dynamic",
                "status": "pass",
                "top_function": "target",
            },
        }
    )

    assert controller["selected_action"]["id"] == "generate_llm_patch_candidates"
    audit = controller["llm_repair_action_audit"]
    assert audit["status"] == "ready"
    assert audit["reason"] == "llm_patch_generation_action_selected"
    assert audit["repair_action_id"] == "generate_llm_patch_candidates"
    policy = controller["policy_trace"]
    assert policy["status"] == "pass"
    assert policy["selected_action_id"] == "generate_llm_patch_candidates"
    assert policy["canonical_action_id"] == "generate_llm_patch_candidates"
    assert policy["policy_rule"] == "topk_dynamic_evidence_with_llm_patch_config"


def test_agent_controller_explains_llm_reflection_key_blocker():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "repository_test_patch_validation_reflection_mode": "llm",
            "repository_llm_reflection_status": "unavailable",
            "repository_llm_reflection_reason": "missing_api_key:CIA_LLM_API_KEY",
            "repository_llm_reflection_provider": "deepseek",
            "repository_llm_reflection_model": "deepseek-v4-pro",
            "repository_llm_reflection_api_key_env": "CIA_LLM_API_KEY",
            "repository_llm_reflection_audit": {
                "mode": "llm",
                "status": "unavailable",
                "reason": "missing_api_key:CIA_LLM_API_KEY",
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "api_key_env": "CIA_LLM_API_KEY",
                "checked_api_key_envs": [
                    "CIA_LLM_API_KEY",
                    "DEEPSEEK_API_KEY",
                ],
                "blocked": True,
            },
            "analysis_readiness": {
                "current_stage": "phase3_patch_validation",
                "next_stage": "phase3_patch_reflection_or_expansion",
                "blocker": "no_candidate_passed_repository_tests",
                "dynamic_evidence_level": "failing_tests",
                "patch_validation_status": "fail",
                "patch_validation_reason": "no_candidate_passed_repository_tests",
                "repair_ready": False,
                "can_attempt_patch_repair": True,
            },
            "reflection_summary": {
                "reflection_mode": "llm",
                "reflection_refiner_status": "unavailable",
                "reflection_refiner_reason": "missing_api_key:CIA_LLM_API_KEY",
                "reflection_candidate_count": 0,
                "max_depth_executed": 0,
            },
            "fault_localization": {
                "mode": "dynamic",
                "status": "pass",
                "top_function": "target",
            },
        }
    )

    selected = controller["selected_action"]
    observations = {
        item["signal"]: item["value"] for item in controller["observations"]
    }
    assert selected["id"] == "run_patch_reflection_loop"
    assert "LLM reflection `deepseek/deepseek-v4-pro`" in selected["reason"]
    assert "missing one of `CIA_LLM_API_KEY`, `DEEPSEEK_API_KEY`" in (
        selected["reason"]
    )
    assert "use rule reflection" in selected["reason"]
    assert observations["repository_llm_reflection_status"] == "unavailable"
    assert observations["repository_llm_reflection_reason"] == (
        "missing_api_key:CIA_LLM_API_KEY"
    )
    assert observations["repository_llm_reflection_blocker"] == (
        "blocked=True, blocker=missing_api_key:CIA_LLM_API_KEY, provider=deepseek, "
        "model=deepseek-v4-pro"
    )


def test_agent_controller_selects_llm_reflection_action_when_ready():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "repository_test_patch_validation_reflection_mode": "llm",
            "repository_llm_reflection_status": "ready",
            "repository_llm_reflection_reason": "llm_refiner",
            "repository_llm_reflection_provider": "deepseek",
            "repository_llm_reflection_model": "deepseek-v4-pro",
            "repository_llm_reflection_api_key_env": "CIA_LLM_API_KEY",
            "repository_llm_reflection_audit": {
                "mode": "llm",
                "status": "ready",
                "reason": "llm_refiner",
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "api_key_env": "CIA_LLM_API_KEY",
                "api_key_present": True,
            },
            "analysis_readiness": {
                "current_stage": "phase3_patch_validation",
                "next_stage": "phase3_patch_reflection_or_expansion",
                "blocker": "no_candidate_passed_repository_tests",
                "dynamic_evidence_level": "failing_tests",
                "patch_validation_status": "fail",
                "patch_validation_reason": "no_candidate_passed_repository_tests",
                "repair_ready": False,
                "can_attempt_patch_repair": True,
            },
            "reflection_summary": {
                "reflection_mode": "llm",
                "reflection_refiner_status": "ready",
                "reflection_refiner_reason": "llm_refiner",
                "reflection_candidate_count": 0,
                "max_depth_executed": 0,
            },
            "fault_localization": {
                "mode": "dynamic",
                "status": "pass",
                "top_function": "target",
            },
        }
    )

    selected = controller["selected_action"]
    assert selected["id"] == "run_llm_patch_reflection_loop"
    assert selected["executable_now"] is True
    assert "LLM reflection enabled" in selected["command"]
    assert "deepseek/deepseek-v4-pro" in selected["reason"]
    assert controller["verification"]["expected_artifact"] == "reflection_trace.json"
    assert controller["verification"]["success_condition"] == (
        "reflection_trace records reflected candidates and updated patch validation"
    )
    audit = controller["llm_repair_action_audit"]
    assert audit["reflection_action_id"] == "run_llm_patch_reflection_loop"
    assert audit["reflection_mode"] == "llm"
    assert audit["reflection_status"] == "ready"
    policy = controller["policy_trace"]
    assert policy["status"] == "pass"
    assert policy["selected_action_id"] == "run_llm_patch_reflection_loop"
    assert policy["canonical_action_id"] == "run_llm_patch_reflection_loop"
    assert policy["policy_rule"] == "patch_validation_failed_and_llm_reflection_ready"


def test_agent_controller_generates_failure_overlay_after_passing_regression_guard():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "selected_signal_count": 2,
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "dynamic_evidence_not_usable:passing_tests",
                "dynamic_evidence_level": "passing_tests",
                "dynamic_evidence_usable_for_localization": False,
                "static_signal_count": 2,
                "planned_repository_test_result_status": "pass",
            },
            "repository_test_regression_guard": {
                "status": "pass",
                "reason": "repository_tests_passed_registered_as_regression_guard",
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "target",
            },
        }
    )
    observations = {
        item["signal"]: item["value"] for item in controller["observations"]
    }
    trace = {item["phase"]: item for item in controller["decision_trace"]}

    assert controller["selected_action"]["id"] == (
        "generate_controlled_failure_overlay"
    )
    assert controller["selected_action"]["executable_now"] is True
    assert controller["selected_action"]["tool"] == "repository_test_failure_overlay"
    assert controller["verification"]["expected_artifact"] == (
        "repository_test_failure_overlay.json"
    )
    assert controller["reflection"]["fallback_action"] == (
        "generate_controlled_failure_overlay"
    )
    assert observations["repository_test_failure_overlay_status"] == ""
    assert trace["plan"]["decision"] == (
        "select `generate_controlled_failure_overlay`"
    )


def test_agent_controller_reports_failure_overlay_extension_blocker():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "selected_signal_count": 2,
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "dynamic_evidence_not_usable:passing_tests",
                "dynamic_evidence_level": "passing_tests",
                "dynamic_evidence_usable_for_localization": False,
                "static_signal_count": 2,
                "planned_repository_test_result_status": "pass",
            },
            "repository_test_regression_guard": {
                "status": "pass",
                "reason": "repository_tests_passed_registered_as_regression_guard",
            },
            "repository_test_failure_overlay_status": "skipped",
            "repository_test_failure_overlay_reason": "no_supported_overlay_candidates",
            "repository_test_failure_overlay_supported_candidates": 0,
            "repository_test_failure_overlay_attempted_cases": 0,
            "repository_test_failure_overlay_candidate_limit": 5,
            "repository_test_failure_overlay_next_actionable_extension": {
                "recommendation": (
                    "Add a deterministic overlay builder for the dominant static rule."
                )
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "target",
            },
        }
    )
    observations = {
        item["signal"]: item["value"] for item in controller["observations"]
    }
    trace = {item["phase"]: item for item in controller["decision_trace"]}

    assert controller["selected_action"]["id"] == (
        "extend_failure_overlay_or_provide_bug_report"
    )
    assert controller["selected_action"]["executable_now"] is False
    assert controller["status"] == "blocked"
    assert controller["termination"]["reason"] == "failure_overlay_not_usable"
    assert "no_supported_overlay_candidates" in controller["selected_action"]["reason"]
    assert "Add a deterministic overlay builder" in (
        controller["selected_action"]["command"]
    )
    assert observations["repository_test_failure_overlay_status"] == "skipped"
    assert observations["repository_test_failure_overlay_reason"] == (
        "no_supported_overlay_candidates"
    )
    assert observations["repository_test_failure_overlay_candidates"] == (
        "supported=0, attempted=0, limit=5"
    )
    assert trace["plan"]["decision"] == (
        "select `extend_failure_overlay_or_provide_bug_report`"
    )


def test_agent_controller_adjusts_source_focus_when_topk_has_no_application_code():
    controller = build_agent_controller_plan(
        {
            "repo": "pypa/sampleproject",
            "repo_spec": "https://github.com/pypa/sampleproject",
            "output_dir": "outputs/sampleproject",
            "selected_signal_count": 3,
            "repository_structure": {
                "package_structure": {
                    "src_layout_packages": ["sample"],
                    "package_roots": ["src"],
                    "recommended_target_prefix": "sample",
                }
            },
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "dynamic_evidence_not_usable:passing_tests",
                "dynamic_evidence_level": "passing_tests",
                "dynamic_evidence_usable_for_localization": False,
                "static_signal_count": 3,
                "planned_repository_test_result_status": "pass",
            },
            "repository_test_regression_guard": {
                "status": "pass",
                "reason": "repository_tests_passed_registered_as_regression_guard",
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "ranked_function_count": 3,
                "top_function": "build_and_check_dists",
                "top_source_role": "test_automation",
                "source_role_counts": {"test_automation": 3},
                "application_candidate_count": 0,
                "non_application_topk_only": True,
                "rankings": [
                    {
                        "rank": 1,
                        "function_name": "build_and_check_dists",
                        "file_path": "noxfile.py",
                        "source_role": "test_automation",
                    }
                ],
            },
        }
    )
    observations = {
        item["signal"]: item["value"] for item in controller["observations"]
    }
    trace = {item["phase"]: item for item in controller["decision_trace"]}
    markdown = render_agent_controller_markdown(controller)

    assert controller["selected_action"]["id"] == "adjust_application_source_focus"
    assert controller["selected_action"]["phase"] == "phase2"
    assert controller["selected_action"]["tool"] == "github_repo_intelligence"
    assert controller["selected_action"]["executable_now"] is True
    assert "top_source_role=test_automation" in controller["selected_action"][
        "reason"
    ]
    assert "application_hints=sample, src" in controller["selected_action"][
        "reason"
    ]
    assert "--max-sources 200" in controller["selected_action"]["command"]
    assert controller["verification"]["expected_artifact"] == "fault_localization.json"
    assert controller["reflection"]["fallback_action"] == (
        "collect_dynamic_failure_evidence"
    )
    assert observations["fault_top_source_role"] == "test_automation"
    assert observations["fault_source_role_counts"] == "test_automation=3"
    assert observations["fault_application_candidate_count"] == "0"
    assert observations["fault_non_application_topk_only"] == "True"
    assert trace["plan"]["decision"] == (
        "select `adjust_application_source_focus`"
    )
    assert "adjust_application_source_focus" in markdown
    assert "Static Top-k localization is dominated by non-application" in markdown


def test_agent_controller_collects_dynamic_evidence_after_source_focus_is_broad():
    controller = build_agent_controller_plan(
        {
            "repo": "pypa/sampleproject",
            "repo_spec": "https://github.com/pypa/sampleproject",
            "output_dir": "outputs/sampleproject",
            "agent_invocation": {
                "include": [],
                "exclude": [],
                "target_prefix": "",
                "max_sources": 50,
                "max_candidates": 20,
            },
            "repository_structure": {
                "package_structure": {
                    "src_layout_packages": ["sample"],
                    "package_roots": ["src"],
                    "recommended_target_prefix": "sample",
                }
            },
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "checkout:full_repo_not_materialized",
                "dynamic_evidence_level": "not_executed",
                "dynamic_evidence_usable_for_localization": False,
                "static_signal_count": 3,
                "planned_repository_test_command": "python -m pytest",
                "planned_repository_test_executable_now": False,
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "ranked_function_count": 3,
                "top_function": "build_and_check_dists",
                "top_source_role": "test_automation",
                "source_role_counts": {"test_automation": 3},
                "application_candidate_count": 0,
                "non_application_topk_only": True,
                "rankings": [
                    {
                        "rank": 1,
                        "function_name": "build_and_check_dists",
                        "file_path": "noxfile.py",
                        "source_role": "test_automation",
                    }
                ],
            },
        }
    )

    assert controller["selected_action"]["id"] == "run_repository_tests_with_checkout"
    assert controller["selected_action"]["phase"] == "phase3"
    assert controller["verification"]["expected_artifact"] == (
        "repository_test_dynamic_evidence.json"
    )


def test_agent_controller_prioritizes_usable_failing_tests_over_static_scope():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "repository_test_fault_localization_status": "skipped",
            "repository_test_fault_localization_reason": "repository_root_missing",
            "repository_structure": {
                "package_structure": {
                    "src_layout_packages": ["pkg"],
                    "package_roots": ["src"],
                    "recommended_target_prefix": "pkg",
                }
            },
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "dynamic_evidence_not_usable:collection_failure",
                "dynamic_evidence_level": "failing_tests",
                "dynamic_evidence_usable_for_localization": True,
                "dynamic_fault_localization_reason": "repository_root_missing",
                "planned_repository_test_result_status": "fail",
                "planned_repository_test_result_failed": 1,
                "planned_repository_test_result_errors": 0,
                "static_signal_count": 3,
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "ranked_function_count": 3,
                "top_function": "build_and_check_dists",
                "top_source_role": "test_automation",
                "source_role_counts": {"test_automation": 3},
                "application_candidate_count": 0,
                "non_application_topk_only": True,
                "rankings": [
                    {
                        "rank": 1,
                        "function_name": "build_and_check_dists",
                        "file_path": "noxfile.py",
                        "source_role": "test_automation",
                    }
                ],
            },
        }
    )
    observations = {
        item["signal"]: item["value"] for item in controller["observations"]
    }
    trace = {item["phase"]: item for item in controller["decision_trace"]}

    assert controller["selected_action"]["id"] == "build_dynamic_fault_localization"
    assert controller["selected_action"]["tool"] == (
        "repository_test_fault_localization"
    )
    assert "level=failing_tests" in controller["selected_action"]["reason"]
    assert "failed=1" in controller["selected_action"]["reason"]
    assert observations["dynamic_evidence_usable_for_localization"] == "True"
    assert observations["dynamic_fault_localization_status"] == "skipped"
    assert observations["planned_repository_test_failures"] == (
        "failed=1, errors=0"
    )
    assert trace["plan"]["decision"] == (
        "select `build_dynamic_fault_localization`"
    )


def test_agent_controller_markdown_summarizes_auto_failure_overlay_route():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "https://github.com/example/project",
            "output_dir": "outputs/project",
            "selected_signal_count": 2,
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "dynamic_evidence_not_usable:passing_tests",
                "dynamic_evidence_level": "passing_tests",
                "dynamic_evidence_usable_for_localization": False,
                "planned_repository_test_result_status": "pass",
            },
            "repository_test_regression_guard": {
                "status": "pass",
                "reason": "repository_tests_passed_registered_as_regression_guard",
            },
            "repository_test_failure_overlay_status": "skipped",
            "repository_test_failure_overlay_reason": "no_supported_overlay_candidates",
            "repository_test_failure_overlay_supported_candidates": 0,
            "repository_test_failure_overlay_attempted_cases": 0,
            "repository_test_failure_overlay_candidate_limit": 5,
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "target",
            },
            "agent_auto_actions": [
                {
                    "action_id": "generate_controlled_failure_overlay",
                    "before_blocker": "dynamic_evidence_not_usable:passing_tests",
                    "loop_verify_outcome": "failure_overlay_attempted",
                    "loop_replan_policy": "continue_observe_plan_act",
                    "after_dynamic_evidence_level": "passing_tests",
                    "after_failure_overlay_status": "skipped",
                    "after_failure_overlay_reason": "no_supported_overlay_candidates",
                    "after_failure_overlay_supported_candidates": 0,
                    "after_failure_overlay_attempted_cases": 0,
                    "after_patch_validation_status": "skipped",
                    "after_patch_validation_success_count": 0,
                    "after_repair_ready": False,
                }
            ],
            "agent_auto_trace": [
                {
                    "iteration": 1,
                    "observe_stage": "phase2_static_graph_fault_localization",
                    "observe_blocker": "dynamic_evidence_not_usable:passing_tests",
                    "plan_selected_action": "generate_controlled_failure_overlay",
                    "auto_executed": True,
                    "verify_outcome": "failure_overlay_attempted",
                    "verify_progress": True,
                    "replan_policy": "continue_observe_plan_act",
                    "verify_dynamic_evidence_level": "passing_tests",
                    "verify_failure_overlay_status": "skipped",
                    "verify_failure_overlay_reason": "no_supported_overlay_candidates",
                }
            ],
        }
    )

    markdown = render_agent_controller_markdown(controller)

    assert "Failure Overlay" in markdown
    assert (
        "skipped(no_supported_overlay_candidates); supported=0, attempted=0"
        in markdown
    )
    assert "failure_overlay_attempted" in markdown


def test_agent_controller_prepares_environment_plan_before_collecting_more_evidence():
    controller = build_agent_controller_plan(
        {
            "repo": "pypa/sampleproject",
            "repo_spec": "https://github.com/pypa/sampleproject",
            "output_dir": "outputs/sampleproject",
            "analysis_readiness": {
                "current_stage": "phase2_static_graph_fault_localization",
                "next_stage": "phase3_repository_test_execution",
                "blocker": "dynamic_evidence_not_usable:collection_failure",
                "dynamic_evidence_level": "collection_failure",
                "repository_test_setup_doctor_status": "blocked",
                "repository_test_setup_doctor_blocker": (
                    "environment:test_tool_missing"
                ),
                "planned_repository_test_command": "python -m nox",
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "build_and_check_dists",
            },
        }
    )

    assert controller["selected_action"]["id"] == (
        "prepare_repository_test_environment"
    )
    assert controller["verification"]["expected_artifact"] == (
        "repository_test_environment_repair_plan.json"
    )
