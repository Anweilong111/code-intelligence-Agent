from __future__ import annotations

import copy
import uuid
from pathlib import Path

from code_intelligence_agent.evaluation.v4_experiment_protocol import (
    EXPERIMENT_CONTRACTS,
    POLICY_CONTRACTS,
    compute_run_record_cost,
    load_experiment_protocol,
    validate_experiment_protocol,
    validate_run_record,
    validate_run_records,
    write_protocol_audit_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = (
    ROOT / "datasets" / "v4_agent_effectiveness" / "experiment_protocol.json"
)


def test_repository_v4_protocol_is_frozen_and_valid():
    protocol = load_experiment_protocol(PROTOCOL_PATH)

    audit = validate_experiment_protocol(protocol, root=ROOT)

    assert audit["status"] == "pass", audit["errors"]
    assert audit["warning_count"] == 0
    assert set(protocol["policy_variants"]) == set(POLICY_CONTRACTS)
    assert set(protocol["experiments"]) == set(EXPERIMENT_CONTRACTS)
    assert protocol["benchmark"]["split_case_counts"] == {
        "development": 10,
        "validation": 15,
        "test": 25,
    }
    assert len(audit["prompt_hashes"]) == 7


def test_protocol_detects_prompt_drift():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    protocol["prompts"][0]["sha256"] = "0" * 64
    protocol.pop("protocol_sha256")

    audit = validate_experiment_protocol(protocol, root=ROOT)

    assert audit["status"] == "fail"
    assert "prompt:agent_policy_v4_sha256_mismatch" in audit["errors"]


def test_protocol_rejects_non_disjoint_repository_splits():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    protocol["benchmark"]["split_policy"] = "case_random"
    protocol["benchmark"]["repository_overlap_allowed"] = True
    protocol.pop("protocol_sha256")

    audit = validate_experiment_protocol(protocol, root=ROOT)

    assert "benchmark.split_policy_must_be_repository_disjoint" in audit["errors"]
    assert "benchmark.repository_overlap_must_be_false" in audit["errors"]


def test_protocol_rejects_live_calls_before_phase0_gates():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    protocol["live_launch_gates"]["live_calls_allowed_in_phase0"] = True
    protocol["live_launch_gates"]["pilot_case_count"] = 50
    protocol.pop("protocol_sha256")

    audit = validate_experiment_protocol(protocol, root=ROOT)

    assert (
        "live_launch_gates.live_calls_allowed_in_phase0_must_be_false"
        in audit["errors"]
    )
    assert "live_launch_gates.pilot_case_count_must_equal_20" in audit["errors"]


def test_protocol_rejects_unequal_primary_budgets():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    budgets = protocol["budgets"]["groups"]
    budgets["larger_budget"] = copy.deepcopy(budgets["repair_equal_budget_v4"])
    budgets["larger_budget"]["maximum_cost_usd"] = 2.0
    protocol["experiments"]["primary_agent_effectiveness"][
        "allocation_budget_groups"
    ]["full_agent"] = "larger_budget"
    protocol.pop("protocol_sha256")

    audit = validate_experiment_protocol(protocol, root=ROOT)

    assert (
        "experiment_allocations_have_unequal_budgets:primary_agent_effectiveness"
        in audit["errors"]
    )


def test_valid_full_agent_trial_record_passes():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    record = _valid_record(protocol)

    audit = validate_run_record(record, protocol=protocol)

    assert audit["status"] == "pass", audit["errors"]
    assert audit["warning_count"] == 0


def test_run_record_rejects_unregistered_action_and_private_reasoning():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    record = _valid_record(protocol)
    record["action_trace"]["actions"][0]["action_id"] = "run_arbitrary_shell"
    record["action_trace"]["actions"][0]["private_reasoning"] = "hidden"

    audit = validate_run_record(record, protocol=protocol)

    assert "action_trace.unregistered_action:run_arbitrary_shell" in audit["errors"]
    assert any(
        item.startswith("sensitive_run_record_field:") for item in audit["errors"]
    )


def test_verified_repair_requires_every_semantic_gate():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    record = _valid_record(protocol)
    record["validation"]["mutation_sensitivity"] = "fail"

    audit = validate_run_record(record, protocol=protocol)

    assert "verified_repair_requires_mutation_sensitivity_pass" in audit["errors"]


def test_run_record_rejects_consumption_above_frozen_budget():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    record = _valid_record(protocol)
    record["budget"]["consumed"]["actions"] = 13

    audit = validate_run_record(record, protocol=protocol)

    assert "budget.exceeded:actions" in audit["errors"]
    assert "action_trace.count_differs_from_budget" in audit["errors"]


def test_no_reflection_policy_rejects_reflection_candidate():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    record = _valid_record(
        protocol,
        experiment_id="component_ablation",
        variant="no_reflection",
    )
    record["candidates"][0]["reflection_round"] = 1
    record["candidates"][0]["parent_candidate_id"] = "parent-candidate"
    record["budget"]["consumed"]["reflection_rounds"] = 1

    audit = validate_run_record(record, protocol=protocol)

    assert "reflection_disabled_policy_contains_reflection_candidate" in audit["errors"]


def test_provider_blocker_remains_a_valid_denominator_trial():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    record = _valid_record(protocol)
    record["candidates"] = []
    record["budget"]["consumed"].update(
        {
            "candidates": 0,
            "actions": 0,
            "model_input_tokens": 0,
            "model_output_tokens": 0,
            "total_model_tokens": 0,
            "actual_cost_usd": 0.0,
        }
    )
    record["action_trace"]["actions"] = []
    record["usage"].update(
        {
            "input_tokens": 0,
            "cache_hit_input_tokens": 0,
            "cache_miss_input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
    )
    record["cost"]["actual_cost_usd"] = 0.0
    record["validation"] = {
        "ast_valid": None,
        "safety_gate": "not_run",
        "targeted_tests": "not_run",
        "full_regression": "not_run",
        "hidden_boundary_tests": "not_run",
        "api_type_compatibility": "not_run",
        "differential_execution": "not_run",
        "mutation_sensitivity": "not_run",
        "semantic_oracle_complete": False,
        "semantic_claim_eligible": False,
    }
    record["outcome"] = {
        "status": "provider_blocker",
        "winning_candidate_id": "",
        "direct_success": False,
        "reflection_recovered": False,
    }
    record["failure"] = {
        "layer": "provider",
        "category": "network",
        "reason_code": "provider_unreachable",
    }

    audit = validate_run_record(record, protocol=protocol)

    assert audit["status"] == "pass", audit["errors"]
    assert record["experiment"]["denominator_included"] is True


def test_duplicate_run_id_is_rejected_across_records():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    first = _valid_record(protocol)
    second = copy.deepcopy(first)

    audit = validate_run_records([first, second], protocol=protocol)

    assert audit["status"] == "fail"
    assert f"duplicate_run_id:{first['run_id']}" in audit["errors"]


def test_protocol_audit_writer_uses_lf(tmp_path):
    paths = write_protocol_audit_artifacts(
        {"status": "pass", "protocol_validation": {}, "notes": []},
        tmp_path / "phase0_verification",
    )

    assert b"\r\n" not in Path(paths["json"]).read_bytes()
    assert b"\r\n" not in Path(paths["markdown"]).read_bytes()


def _valid_record(
    protocol: dict,
    *,
    experiment_id: str = "primary_agent_effectiveness",
    variant: str = "full_agent",
    patch_strategy: str = "llm_only",
) -> dict:
    policy = copy.deepcopy(protocol["policy_variants"][variant])
    policy["variant"] = variant
    policy["patch_strategy"] = patch_strategy
    limits = copy.deepcopy(protocol["budgets"]["groups"]["repair_equal_budget_v4"])
    prompts = {item["id"]: item for item in protocol["prompts"]}
    usage = {
        "source": "provider_usage",
        "input_tokens": 100,
        "cache_hit_input_tokens": 0,
        "cache_miss_input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
    }
    selected_by = {
        "fixed": "fixed_workflow",
        "rule": "rule_controller",
        "llm": "llm_planner",
    }[policy["planner_mode"]]
    record = {
        "schema_version": "4.0",
        "run_id": str(uuid.uuid4()),
        "case": {
            "case_id": "case-001",
            "repository": "owner/project",
            "bug_commit_sha": "b" * 40,
            "fix_commit_sha": "f" * 40,
            "benchmark_split": "development",
        },
        "experiment": {
            "experiment_id": experiment_id,
            "trial_index": 1,
            "trial_id": str(uuid.uuid4()),
            "independent_trial": True,
            "denominator_included": True,
        },
        "policy": policy,
        "budget": {
            "group_id": "repair_equal_budget_v4",
            "limits": limits,
            "consumed": {
                "model_input_tokens": 100,
                "model_output_tokens": 20,
                "total_model_tokens": 120,
                "candidates": 1,
                "actions": 2,
                "reflection_rounds": 0,
                "wall_time_seconds": 1.0,
                "actual_cost_usd": 0.0,
            },
            "exhausted_dimensions": [],
        },
        "action_trace": {
            "trace_id": str(uuid.uuid4()),
            "contains_private_reasoning": False,
            "complete": True,
            "actions": [
                {
                    "step": 1,
                    "action_id": "localize_fault",
                    "selected_by": selected_by,
                    "reason_code": "missing_localization",
                    "evidence_refs": ["evidence/test-failure.json"],
                    "safety_gate": "not_required",
                    "verify_status": "pass",
                    "output_artifact_ref": "evidence/localization.json",
                },
                {
                    "step": 2,
                    "action_id": "validate_patch_in_sandbox",
                    "selected_by": selected_by,
                    "reason_code": "candidate_ready",
                    "evidence_refs": ["patches/candidate-1.diff"],
                    "safety_gate": "pass",
                    "verify_status": "pass",
                    "output_artifact_ref": "validation/candidate-1.json",
                },
            ],
        },
        "candidates": [
            {
                "candidate_index": 1,
                "candidate_id": "candidate-1",
                "generator_family": "llm",
                "generator_id": "deepseek_patch_generation_v4",
                "parent_candidate_id": "",
                "reflection_round": 0,
                "patch_sha256": "a" * 64,
                "touched_files": ["src/project/core.py"],
            }
        ],
        "model": {
            "provider": protocol["model"]["provider"],
            "model_id": protocol["model"]["model_id"],
            "temperature": protocol["model"]["temperature"],
            "prompts_used": [
                {
                    "id": "agent_policy_v4",
                    "sha256": prompts["agent_policy_v4"]["sha256"],
                },
                {
                    "id": "patch_generation_v4",
                    "sha256": prompts["patch_generation_v4"]["sha256"],
                },
            ],
        },
        "usage": usage,
        "cost": {
            "currency": protocol["pricing"]["currency"],
            "pricing_snapshot_id": protocol["pricing"]["snapshot_id"],
            "actual_cost_usd": 0.0,
        },
        "timing": {
            "latency_ms": 1000.0,
            "provider_retry_count": 0,
            "provider_retry_reasons": [],
        },
        "validation": {
            "ast_valid": True,
            "safety_gate": "pass",
            "targeted_tests": "pass",
            "full_regression": "pass",
            "hidden_boundary_tests": "pass",
            "api_type_compatibility": "pass",
            "differential_execution": "pass",
            "mutation_sensitivity": "pass",
            "semantic_oracle_complete": True,
            "semantic_claim_eligible": True,
        },
        "outcome": {
            "status": "verified_repair",
            "winning_candidate_id": "candidate-1",
            "direct_success": True,
            "reflection_recovered": False,
        },
        "failure": {
            "layer": "none",
            "category": "none",
            "reason_code": "",
        },
        "model_context": {
            "artifact_ref": "contexts/case-001.json",
            "contains_gold_patch": False,
            "contains_fix_commit": False,
            "contains_hidden_test_answer": False,
            "contains_repository_instruction_as_authority": False,
        },
        "artifacts": {
            "patch": "patches/candidate-1.diff",
            "action_trace": "traces/case-001.json",
            "validation": "validation/candidate-1.json",
        },
        "timestamps": {
            "started_at": "2026-07-17T00:00:00+00:00",
            "completed_at": "2026-07-17T00:00:01+00:00",
        },
    }
    cost = compute_run_record_cost(record, protocol)
    record["cost"]["actual_cost_usd"] = cost
    record["budget"]["consumed"]["actual_cost_usd"] = cost
    return record
