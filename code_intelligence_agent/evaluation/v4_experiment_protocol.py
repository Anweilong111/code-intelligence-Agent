from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from code_intelligence_agent.evaluation.v3_experiment_protocol import (
    canonical_json_sha256,
    sha256_file,
)


PROTOCOL_SCHEMA_VERSION = "4.0"
RUN_RECORD_SCHEMA_VERSION = "4.0"
V3_BASELINE_TAG = "v3-baseline"
V3_BASELINE_COMMIT = "43268748cbfb4abb1f54c2e8d41da96e5ba1d92a"

POLICY_CONTRACTS: dict[str, dict[str, Any]] = {
    "fixed_workflow": {
        "planner_mode": "fixed",
        "action_selection": "static_sequence",
        "reflection_enabled": False,
        "memory_mode": "none",
        "planner_advisory": False,
    },
    "rule_planner": {
        "planner_mode": "rule",
        "action_selection": "adaptive",
        "reflection_enabled": False,
        "memory_mode": "none",
        "planner_advisory": False,
    },
    "llm_planner": {
        "planner_mode": "llm",
        "action_selection": "adaptive",
        "reflection_enabled": False,
        "memory_mode": "none",
        "planner_advisory": True,
    },
    "no_reflection": {
        "planner_mode": "llm",
        "action_selection": "adaptive",
        "reflection_enabled": False,
        "memory_mode": "structured",
        "planner_advisory": True,
    },
    "no_memory": {
        "planner_mode": "llm",
        "action_selection": "adaptive",
        "reflection_enabled": True,
        "memory_mode": "none",
        "planner_advisory": True,
    },
    "full_agent": {
        "planner_mode": "llm",
        "action_selection": "adaptive",
        "reflection_enabled": True,
        "memory_mode": "structured",
        "planner_advisory": True,
    },
}

EXPERIMENT_CONTRACTS: dict[str, dict[str, Any]] = {
    "primary_agent_effectiveness": {
        "case_count": 50,
        "trials_per_allocation": 3,
        "allocation_axis": "policy_variant",
        "allocations": ["fixed_workflow", "full_agent"],
    },
    "component_ablation": {
        "case_count": 20,
        "trials_per_allocation": 3,
        "allocation_axis": "policy_variant",
        "allocations": [
            "fixed_workflow",
            "rule_planner",
            "llm_planner",
            "no_reflection",
            "no_memory",
            "full_agent",
        ],
    },
    "routed_hybrid": {
        "case_count": 50,
        "trials_per_allocation": 3,
        "allocation_axis": "patch_strategy",
        "allocations": ["llm_only", "naive_hybrid", "routed_hybrid"],
    },
}

PATCH_STRATEGIES = {"llm_only", "naive_hybrid", "routed_hybrid"}
GENERATOR_FAMILIES = {"rule", "llm"}
BENCHMARK_SPLITS = {"development", "validation", "test"}
ACTION_SELECTORS = {
    "fixed_workflow",
    "rule_controller",
    "llm_planner",
    "safety_controller",
}
ACTION_GATE_STATUSES = {"pass", "reject", "not_required", "not_run"}
VERIFY_STATUSES = {"pass", "fail", "blocker", "not_run", "not_applicable"}
VALIDATION_STATUSES = {"pass", "fail", "blocker", "not_run", "not_applicable"}
OUTCOME_STATUSES = {
    "verified_repair",
    "unverified_suggestion",
    "failed",
    "provider_blocker",
    "environment_blocker",
    "safety_rejected",
    "budget_exhausted",
}
FAILURE_LAYERS = {
    "none",
    "provider",
    "environment",
    "localization",
    "planning",
    "generation",
    "syntax",
    "safety",
    "targeted_test",
    "full_regression",
    "semantic_validation",
    "budget",
    "controller",
}
PROVIDER_FAILURE_CATEGORIES = {
    "authentication",
    "authorization",
    "billing_or_quota",
    "rate_limit",
    "network",
    "timeout",
    "invalid_provider_response",
    "model_unavailable",
}
ENVIRONMENT_FAILURE_CATEGORIES = {
    "dependency_install",
    "python_version",
    "test_discovery",
    "test_process",
    "resource_limit",
    "unsafe_build_hook",
    "external_service",
}
REQUIRED_PROMPTS = {
    "agent_policy_v4",
    "patch_generation_v4",
    "reflection_v4",
    "localization_v4",
    "router_v4",
    "semantic_risk_v4",
    "provider_access_preflight_v4",
}
REQUIRED_ACTIONS = {
    "clone_or_load_repository",
    "discover_repository_structure",
    "discover_tests",
    "diagnose_environment",
    "run_repository_tests",
    "localize_fault",
    "generate_llm_patch_candidates",
    "generate_hybrid_patch_candidates",
    "validate_patch_in_sandbox",
    "run_llm_patch_reflection_loop",
    "emit_blocker_report",
    "generate_final_agent_report",
}
REQUIRED_METRICS = {
    "verified_pass_at_1",
    "verified_pass_at_3",
    "blocker_resolution_rate",
    "invalid_action_rate",
    "mean_action_count",
    "reflection_recovery_rate",
    "token_usage",
    "actual_cost_usd",
    "cost_per_verified_repair_usd",
    "latency_ms",
    "top_1",
    "top_3",
    "top_5",
    "mrr",
    "map",
    "ndcg",
    "exam",
    "semantic_pass_rate",
    "overfitting_rejection_rate",
}
SEMANTIC_GATES = (
    "hidden_boundary_tests",
    "api_type_compatibility",
    "differential_execution",
    "mutation_sensitivity",
)
BUDGET_LIMIT_FIELDS = (
    "maximum_model_input_tokens",
    "maximum_model_output_tokens",
    "maximum_total_model_tokens",
    "maximum_candidates",
    "maximum_actions",
    "maximum_reflection_rounds",
    "maximum_wall_time_seconds",
    "maximum_cost_usd",
)
BUDGET_CONSUMED_FIELDS = (
    "model_input_tokens",
    "model_output_tokens",
    "total_model_tokens",
    "candidates",
    "actions",
    "reflection_rounds",
    "wall_time_seconds",
    "actual_cost_usd",
)
FORBIDDEN_FIELD_NAMES = {
    "api_key",
    "authorization",
    "chain_of_thought",
    "private_reasoning",
    "raw",
    "raw_response",
    "response_body",
    "secret",
    "shell_command",
}
SECRET_PATTERN = re.compile(r"\bsk-[A-Za-z0-9._-]{16,}\b", re.IGNORECASE)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def load_experiment_protocol(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Experiment protocol must be a JSON object.")
    return value


def validate_experiment_protocol(
    protocol: dict[str, Any],
    *,
    root: str | Path,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    errors: list[str] = []
    warnings: list[str] = []

    if str(protocol.get("schema_version") or "") != PROTOCOL_SCHEMA_VERSION:
        errors.append("schema_version_must_be_4.0")

    baseline = _dict(protocol.get("baseline"))
    if str(baseline.get("tag") or "") != V3_BASELINE_TAG:
        errors.append("baseline.tag_must_equal_v3-baseline")
    if str(baseline.get("commit_sha") or "") != V3_BASELINE_COMMIT:
        errors.append("baseline.commit_sha_must_equal_frozen_v3_commit")

    runtime = _dict(protocol.get("runtime"))
    if not str(runtime.get("controller_python") or ""):
        errors.append("runtime.controller_python_is_required")
    _validate_hashed_path(
        root_path,
        str(runtime.get("requirements_path") or ""),
        str(runtime.get("requirements_sha256") or ""),
        "runtime.requirements",
        errors,
    )
    _validate_frozen_artifacts(protocol, root_path, errors)
    _validate_benchmark(protocol, errors)
    prompt_hashes = _validate_model_prompts_and_pricing(
        protocol,
        root_path,
        errors,
        warnings,
    )
    _validate_policy_contracts(protocol, errors)
    _validate_experiments_and_budgets(protocol, errors)
    _validate_actions_metrics_and_safety(protocol, errors)

    sensitive_hits = _find_sensitive_values(protocol)
    errors.extend(f"sensitive_protocol_field:{item}" for item in sensitive_hits)

    fingerprint_source = json.loads(json.dumps(protocol))
    fingerprint_source.pop("protocol_sha256", None)
    fingerprint = canonical_json_sha256(fingerprint_source)
    expected_fingerprint = str(protocol.get("protocol_sha256") or "")
    if not expected_fingerprint:
        warnings.append("protocol_sha256_not_pinned")
    elif expected_fingerprint != fingerprint:
        errors.append("protocol_sha256_mismatch")

    return {
        "status": "pass" if not errors else "fail",
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "prompt_hashes": prompt_hashes,
        "protocol_sha256": fingerprint,
    }


def _validate_frozen_artifacts(
    protocol: dict[str, Any],
    root: Path,
    errors: list[str],
) -> None:
    artifacts = [_dict(item) for item in _list(protocol.get("frozen_artifacts"))]
    if not artifacts:
        errors.append("frozen_artifacts_are_required")
        return
    artifact_ids: set[str] = set()
    for index, artifact in enumerate(artifacts):
        artifact_id = str(artifact.get("id") or "")
        if not artifact_id:
            errors.append(f"frozen_artifacts[{index}].id_is_required")
            continue
        if artifact_id in artifact_ids:
            errors.append(f"duplicate_frozen_artifact_id:{artifact_id}")
        artifact_ids.add(artifact_id)
        _validate_hashed_path(
            root,
            str(artifact.get("path") or ""),
            str(artifact.get("sha256") or ""),
            f"frozen_artifact:{artifact_id}",
            errors,
        )


def _validate_benchmark(protocol: dict[str, Any], errors: list[str]) -> None:
    benchmark = _dict(protocol.get("benchmark"))
    minimum_cases = _int(benchmark.get("minimum_cases"), 0)
    minimum_repositories = _int(benchmark.get("minimum_repositories"), 0)
    split_counts = _dict(benchmark.get("split_case_counts"))
    if minimum_cases < 50:
        errors.append("benchmark.minimum_cases_must_be_at_least_50")
    if minimum_repositories < 15:
        errors.append("benchmark.minimum_repositories_must_be_at_least_15")
    expected_splits = {"development": 10, "validation": 15, "test": 25}
    for split, minimum in expected_splits.items():
        if _int(split_counts.get(split), 0) < minimum:
            errors.append(f"benchmark.{split}_case_count_is_too_small")
    observed_total = sum(_int(split_counts.get(item), 0) for item in expected_splits)
    if observed_total != minimum_cases:
        errors.append("benchmark.split_case_counts_must_sum_to_minimum_cases")
    if str(benchmark.get("split_policy") or "") != "repository_disjoint":
        errors.append("benchmark.split_policy_must_be_repository_disjoint")
    if benchmark.get("repository_overlap_allowed") is not False:
        errors.append("benchmark.repository_overlap_must_be_false")
    if benchmark.get("blind_test_locked_before_live_calls") is not True:
        errors.append("benchmark.blind_test_must_be_locked_before_live_calls")
    if (
        str(benchmark.get("manifest_lock_policy") or "")
        != "separate_hashed_manifest_required_before_live_trials"
    ):
        errors.append("benchmark.manifest_lock_policy_is_invalid")

    required_categories = {
        "static_negative",
        "cross_function",
        "dataflow",
        "multi_file",
        "root_error_separated",
        "high_similarity_candidates",
        "real_traceback",
    }
    categories = set(map(str, _list(benchmark.get("required_difficulty_categories"))))
    missing_categories = sorted(required_categories - categories)
    if missing_categories:
        errors.append("benchmark.missing_difficulty_categories:" + ",".join(missing_categories))
    excluded = set(map(str, _list(benchmark.get("model_context_excludes"))))
    required_exclusions = {"gold_patch", "fix_commit_content", "hidden_test_answer"}
    if not required_exclusions.issubset(excluded):
        errors.append("benchmark.model_context_exclusions_are_incomplete")

    launch = _dict(protocol.get("live_launch_gates"))
    required_true = (
        "offline_protocol_audit_required",
        "benchmark_manifest_lock_required",
        "provider_preflight_required",
        "explicit_user_authorization_required",
        "pilot_excludes_blind_test",
        "pilot_must_pass_run_record_audit_before_final",
    )
    for field in required_true:
        if launch.get(field) is not True:
            errors.append(f"live_launch_gates.{field}_must_be_true")
    if launch.get("live_calls_allowed_in_phase0") is not False:
        errors.append("live_launch_gates.live_calls_allowed_in_phase0_must_be_false")
    if _int(launch.get("pilot_case_count"), 0) != 20:
        errors.append("live_launch_gates.pilot_case_count_must_equal_20")

    cold = _dict(protocol.get("cold_repository_evaluation"))
    if _int(cold.get("repository_count"), 0) < 50:
        errors.append("cold_repository_evaluation.repository_count_must_be_at_least_50")
    if _int(cold.get("startup_or_accurate_blocker_target"), 0) < 45:
        errors.append("cold_repository_evaluation.target_must_be_at_least_45")
    if cold.get("disjoint_from_benchmark_and_development") is not True:
        errors.append("cold_repository_evaluation.must_be_disjoint")
    if _int(cold.get("maximum_unauthorized_dangerous_actions"), -1) != 0:
        errors.append("cold_repository_evaluation.dangerous_action_target_must_be_zero")
    required_environments = {
        "poetry",
        "uv",
        "tox",
        "nox",
        "monorepo",
        "multi_package",
        "multiple_python_versions",
        "native_extension",
        "external_service",
    }
    observed_environments = set(map(str, _list(cold.get("required_environment_types"))))
    missing_environments = sorted(required_environments - observed_environments)
    if missing_environments:
        errors.append(
            "cold_repository_evaluation.missing_environment_types:"
            + ",".join(missing_environments)
        )


def _validate_model_prompts_and_pricing(
    protocol: dict[str, Any],
    root: Path,
    errors: list[str],
    warnings: list[str],
) -> dict[str, str]:
    model = _dict(protocol.get("model"))
    for field in ("provider", "model_id", "api_base", "endpoint"):
        if not str(model.get(field) or ""):
            errors.append(f"model.{field}_is_required")
    if model.get("temperature") != 0:
        errors.append("model.temperature_must_be_zero")
    if str(model.get("api_key_source") or "") != "environment_only":
        errors.append("model.api_key_source_must_be_environment_only")
    if not _list(model.get("api_key_env_names")):
        errors.append("model.api_key_env_names_are_required")

    prompts = [_dict(item) for item in _list(protocol.get("prompts"))]
    prompt_hashes: dict[str, str] = {}
    prompt_ids: set[str] = set()
    for index, prompt in enumerate(prompts):
        prompt_id = str(prompt.get("id") or "")
        if not prompt_id:
            errors.append(f"prompts[{index}].id_is_required")
            continue
        if prompt_id in prompt_ids:
            errors.append(f"duplicate_prompt_id:{prompt_id}")
        prompt_ids.add(prompt_id)
        actual_hash = _validate_hashed_path(
            root,
            str(prompt.get("path") or ""),
            str(prompt.get("sha256") or ""),
            f"prompt:{prompt_id}",
            errors,
        )
        if actual_hash:
            prompt_hashes[prompt_id] = actual_hash
    missing_prompts = sorted(REQUIRED_PROMPTS - prompt_ids)
    if missing_prompts:
        errors.append("missing_required_prompts:" + ",".join(missing_prompts))

    preflight = _dict(model.get("access_preflight"))
    preflight_id = str(preflight.get("prompt_id") or "")
    if preflight.get("enabled") is not True:
        errors.append("model.access_preflight.enabled_must_be_true")
    if preflight_id != "provider_access_preflight_v4":
        errors.append("model.access_preflight.prompt_id_is_invalid")
    if str(preflight.get("request_prompt_sha256") or "") != prompt_hashes.get(preflight_id):
        errors.append("model.access_preflight.request_prompt_sha256_mismatch")
    if preflight.get("counts_as_repair_trial") is not False:
        errors.append("model.access_preflight_must_not_count_as_trial")
    if preflight.get("runs_once_per_evaluation") is not True:
        errors.append("model.access_preflight_must_run_once_per_evaluation")

    pricing = _dict(protocol.get("pricing"))
    if str(pricing.get("currency") or "") != "USD":
        errors.append("pricing.currency_must_be_USD")
    for field in (
        "cache_hit_input_usd_per_million_tokens",
        "cache_miss_input_usd_per_million_tokens",
        "output_usd_per_million_tokens",
    ):
        if _float(pricing.get(field), -1.0) < 0:
            errors.append(f"pricing.{field}_must_be_non_negative")
    if not str(pricing.get("snapshot_id") or ""):
        errors.append("pricing.snapshot_id_is_required")
    urls = [str(item) for item in _list(pricing.get("source_urls"))]
    if not urls or any(not item.startswith("https://") for item in urls):
        errors.append("pricing.https_source_urls_are_required")

    randomness = _dict(protocol.get("randomness"))
    if _int(randomness.get("independent_trials_per_allocation"), 0) != 3:
        errors.append("randomness.independent_trials_per_allocation_must_equal_three")
    if randomness.get("share_state_between_trials") is not False:
        errors.append("randomness.state_must_not_cross_trials")
    if randomness.get("provider_retry_creates_new_trial") is not False:
        errors.append("randomness.provider_retry_must_not_create_trial")
    if randomness.get("seed") is not None:
        warnings.append("configured_seed_requires_provider_support_evidence")
    return prompt_hashes


def _validate_policy_contracts(protocol: dict[str, Any], errors: list[str]) -> None:
    variants = _dict(protocol.get("policy_variants"))
    if set(variants) != set(POLICY_CONTRACTS):
        errors.append("policy_variants_must_match_frozen_ablation_set")
    for variant, expected in POLICY_CONTRACTS.items():
        actual = _dict(variants.get(variant))
        for field, expected_value in expected.items():
            if actual.get(field) != expected_value:
                errors.append(f"policy_variant_mismatch:{variant}:{field}")
        if actual.get("registered_actions_only") is not True:
            errors.append(f"policy_variant_requires_registered_actions:{variant}")
        if str(actual.get("safety_controller") or "") != "deterministic_authoritative":
            errors.append(f"policy_variant_requires_authoritative_safety:{variant}")


def _validate_experiments_and_budgets(
    protocol: dict[str, Any],
    errors: list[str],
) -> None:
    budget_groups = _dict(_dict(protocol.get("budgets")).get("groups"))
    if not budget_groups:
        errors.append("budgets.groups_are_required")
    for group_id, raw_group in budget_groups.items():
        group = _dict(raw_group)
        for field in BUDGET_LIMIT_FIELDS:
            value = _float(group.get(field), 0.0)
            if value <= 0:
                errors.append(f"budget_limit_must_be_positive:{group_id}:{field}")
        if (
            _int(group.get("maximum_total_model_tokens"), 0)
            != _int(group.get("maximum_model_input_tokens"), 0)
            + _int(group.get("maximum_model_output_tokens"), 0)
        ):
            errors.append(f"budget_total_tokens_mismatch:{group_id}")

    experiments = _dict(protocol.get("experiments"))
    if set(experiments) != set(EXPERIMENT_CONTRACTS):
        errors.append("experiments_must_match_frozen_v4_set")
    for experiment_id, expected in EXPERIMENT_CONTRACTS.items():
        experiment = _dict(experiments.get(experiment_id))
        for field in ("case_count", "trials_per_allocation", "allocation_axis"):
            if experiment.get(field) != expected[field]:
                errors.append(f"experiment_contract_mismatch:{experiment_id}:{field}")
        allocations = [str(item) for item in _list(experiment.get("allocations"))]
        if allocations != expected["allocations"]:
            errors.append(f"experiment_allocations_mismatch:{experiment_id}")
        mappings = _dict(experiment.get("allocation_budget_groups"))
        if set(mappings) != set(allocations):
            errors.append(f"experiment_budget_mapping_incomplete:{experiment_id}")
            continue
        group_ids = {str(mappings.get(item) or "") for item in allocations}
        if len(group_ids) != 1:
            errors.append(f"experiment_allocations_have_unequal_budgets:{experiment_id}")
        if not group_ids.issubset(set(budget_groups)):
            errors.append(f"experiment_references_unknown_budget_group:{experiment_id}")
        if experiment.get("same_model_for_all_allocations") is not True:
            errors.append(f"experiment_requires_same_model:{experiment_id}")
        if experiment.get("retain_all_failures_in_denominator") is not True:
            errors.append(f"experiment_must_retain_failures:{experiment_id}")
    ablation = _dict(experiments.get("component_ablation"))
    if ablation.get("selection_frozen_before_live_calls") is not True:
        errors.append("component_ablation_selection_must_be_frozen")
    if ablation.get("selection_uses_trial_outcomes") is not False:
        errors.append("component_ablation_must_not_select_on_outcomes")


def _validate_actions_metrics_and_safety(
    protocol: dict[str, Any],
    errors: list[str],
) -> None:
    action_registry = [_dict(item) for item in _list(protocol.get("action_registry"))]
    action_ids = {str(item.get("action_id") or "") for item in action_registry}
    missing_actions = sorted(REQUIRED_ACTIONS - action_ids)
    if missing_actions:
        errors.append("action_registry_missing_required_actions:" + ",".join(missing_actions))
    for action in action_registry:
        action_id = str(action.get("action_id") or "")
        if not action_id:
            errors.append("action_registry_contains_empty_id")
        if action.get("allows_free_form_shell") is not False:
            errors.append(f"action_allows_free_form_shell:{action_id}")

    metrics: set[str] = set()
    for values in _dict(protocol.get("required_metrics")).values():
        metrics.update(map(str, _list(values)))
    missing_metrics = sorted(REQUIRED_METRICS - metrics)
    if missing_metrics:
        errors.append("missing_required_metrics:" + ",".join(missing_metrics))

    authority = _dict(protocol.get("success_authority"))
    required_gates = list(map(str, _list(authority.get("required_for_verified_repair"))))
    expected_gates = [
        "ast_valid",
        "safety_gate",
        "targeted_tests",
        "full_regression",
        *SEMANTIC_GATES,
    ]
    if required_gates != expected_gates:
        errors.append("success_authority_gates_are_incomplete_or_unordered")
    if str(authority.get("without_complete_oracle") or "") != "unverified_suggestion":
        errors.append("success_authority_requires_unverified_suggestion_without_oracle")

    safety = _dict(protocol.get("safety"))
    required_false = (
        "gold_patch_in_model_context",
        "fix_commit_content_in_model_context",
        "hidden_test_answer_in_model_context",
        "free_form_shell",
        "raw_provider_response_in_run_record",
        "model_may_override_safety_controller",
        "model_key_in_repository_process",
    )
    for field in required_false:
        if safety.get(field) is not False:
            errors.append(f"safety.{field}_must_be_false")
    if safety.get("repository_text_is_untrusted") is not True:
        errors.append("safety.repository_text_must_be_untrusted")
    if safety.get("registered_actions_only") is not True:
        errors.append("safety.registered_actions_only_must_be_true")
    disclosure = _dict(protocol.get("data_disclosure"))
    if disclosure.get("live_external_calls_require_explicit_user_authorization") is not True:
        errors.append("data_disclosure_requires_explicit_user_authorization")
    if disclosure.get("authorization_is_not_persistent_across_major_evaluations") is not True:
        errors.append("data_disclosure_authorization_must_not_persist")


def run_record_schema() -> dict[str, Any]:
    return {
        "$id": "code-intelligence-agent-v4-trial-run-record",
        "schema_version": RUN_RECORD_SCHEMA_VERSION,
        "record_granularity": "one_case_allocation_trial",
        "required_sections": [
            "case",
            "experiment",
            "policy",
            "budget",
            "action_trace",
            "model",
            "usage",
            "cost",
            "timing",
            "validation",
            "outcome",
            "failure",
            "model_context",
            "artifacts",
            "timestamps",
        ],
        "required_lists": ["candidates"],
        "policy_variants": sorted(POLICY_CONTRACTS),
        "patch_strategies": sorted(PATCH_STRATEGIES),
        "benchmark_splits": sorted(BENCHMARK_SPLITS),
        "outcome_statuses": sorted(OUTCOME_STATUSES),
        "failure_layers": sorted(FAILURE_LAYERS),
        "semantic_gates": list(SEMANTIC_GATES),
        "forbidden_recursive_fields": sorted(FORBIDDEN_FIELD_NAMES),
        "private_reasoning_policy": "never_persisted",
    }


def validate_run_record(
    record: dict[str, Any],
    *,
    protocol: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if str(record.get("schema_version") or "") != RUN_RECORD_SCHEMA_VERSION:
        errors.append("schema_version_must_be_4.0")
    run_id = str(record.get("run_id") or "")
    if not _is_uuid(run_id):
        errors.append("run_id_must_be_uuid")
    for section in run_record_schema()["required_sections"]:
        if not isinstance(record.get(section), dict):
            errors.append(f"{section}_section_is_required")
    if not isinstance(record.get("candidates"), list):
        errors.append("candidates_list_is_required")

    _validate_run_case(record, errors)
    experiment_id, allocation = _validate_run_experiment_policy(record, protocol, errors)
    consumed = _validate_run_budget(record, protocol, experiment_id, allocation, errors)
    _validate_run_actions(record, protocol, consumed, errors)
    candidates = _validate_run_candidates(record, errors)
    _validate_run_model_usage_cost(record, protocol, consumed, errors, warnings)
    _validate_run_outcome(record, candidates, errors)
    _validate_run_failure_and_context(record, errors)

    sensitive_hits = _find_sensitive_values(record)
    errors.extend(f"sensitive_run_record_field:{item}" for item in sensitive_hits)
    return {
        "status": "pass" if not errors else "fail",
        "run_id": run_id,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }


def _validate_run_case(record: dict[str, Any], errors: list[str]) -> None:
    case = _dict(record.get("case"))
    if not str(case.get("case_id") or ""):
        errors.append("case.case_id_is_required")
    if not str(case.get("repository") or ""):
        errors.append("case.repository_is_required")
    for field in ("bug_commit_sha", "fix_commit_sha"):
        if not COMMIT_PATTERN.fullmatch(str(case.get(field) or "")):
            errors.append(f"case.{field}_must_be_full_sha")
    if str(case.get("benchmark_split") or "") not in BENCHMARK_SPLITS:
        errors.append("case.benchmark_split_is_invalid")


def _validate_run_experiment_policy(
    record: dict[str, Any],
    protocol: dict[str, Any],
    errors: list[str],
) -> tuple[str, str]:
    experiment = _dict(record.get("experiment"))
    experiment_id = str(experiment.get("experiment_id") or "")
    frozen_experiment = _dict(_dict(protocol.get("experiments")).get(experiment_id))
    if not frozen_experiment:
        errors.append("experiment.experiment_id_is_invalid")
    trial_index = _int(experiment.get("trial_index"), 0)
    maximum_trials = _int(frozen_experiment.get("trials_per_allocation"), 0)
    if not 1 <= trial_index <= maximum_trials:
        errors.append("experiment.trial_index_is_invalid")
    if not _is_uuid(str(experiment.get("trial_id") or "")):
        errors.append("experiment.trial_id_must_be_uuid")
    if experiment.get("independent_trial") is not True:
        errors.append("experiment.independent_trial_must_be_true")
    if experiment.get("denominator_included") is not True:
        errors.append("experiment.denominator_included_must_be_true")

    policy = _dict(record.get("policy"))
    variant = str(policy.get("variant") or "")
    frozen_policy = _dict(_dict(protocol.get("policy_variants")).get(variant))
    if not frozen_policy:
        errors.append("policy.variant_is_invalid")
    for field in (
        "planner_mode",
        "action_selection",
        "reflection_enabled",
        "memory_mode",
        "planner_advisory",
        "registered_actions_only",
        "safety_controller",
    ):
        if policy.get(field) != frozen_policy.get(field):
            errors.append(f"policy.differs_from_protocol:{field}")
    strategy = str(policy.get("patch_strategy") or "")
    if strategy not in PATCH_STRATEGIES:
        errors.append("policy.patch_strategy_is_invalid")

    axis = str(frozen_experiment.get("allocation_axis") or "")
    allocation = variant if axis == "policy_variant" else strategy
    allocations = [str(item) for item in _list(frozen_experiment.get("allocations"))]
    if allocation not in allocations:
        errors.append("policy.allocation_not_enabled_for_experiment")
    if axis == "policy_variant" and strategy != "llm_only":
        errors.append("agent_policy_experiments_require_llm_only_patch_strategy")
    if axis == "patch_strategy" and variant != "full_agent":
        errors.append("routed_hybrid_experiment_requires_full_agent_policy")
    return experiment_id, allocation


def _validate_run_budget(
    record: dict[str, Any],
    protocol: dict[str, Any],
    experiment_id: str,
    allocation: str,
    errors: list[str],
) -> dict[str, Any]:
    budget = _dict(record.get("budget"))
    experiment = _dict(_dict(protocol.get("experiments")).get(experiment_id))
    expected_group_id = str(
        _dict(experiment.get("allocation_budget_groups")).get(allocation) or ""
    )
    group_id = str(budget.get("group_id") or "")
    if group_id != expected_group_id:
        errors.append("budget.group_id_differs_from_protocol")
    expected_limits = _dict(
        _dict(_dict(protocol.get("budgets")).get("groups")).get(group_id)
    )
    limits = _dict(budget.get("limits"))
    for field in BUDGET_LIMIT_FIELDS:
        if limits.get(field) != expected_limits.get(field):
            errors.append(f"budget.limit_differs_from_protocol:{field}")
    consumed = _dict(budget.get("consumed"))
    field_pairs = (
        ("model_input_tokens", "maximum_model_input_tokens"),
        ("model_output_tokens", "maximum_model_output_tokens"),
        ("total_model_tokens", "maximum_total_model_tokens"),
        ("candidates", "maximum_candidates"),
        ("actions", "maximum_actions"),
        ("reflection_rounds", "maximum_reflection_rounds"),
        ("wall_time_seconds", "maximum_wall_time_seconds"),
        ("actual_cost_usd", "maximum_cost_usd"),
    )
    for consumed_field, limit_field in field_pairs:
        value = _float(consumed.get(consumed_field), -1.0)
        limit = _float(limits.get(limit_field), -1.0)
        if value < 0:
            errors.append(f"budget.consumed_must_be_non_negative:{consumed_field}")
        elif value > limit:
            errors.append(f"budget.exceeded:{consumed_field}")
    if (
        _int(consumed.get("total_model_tokens"), 0)
        != _int(consumed.get("model_input_tokens"), 0)
        + _int(consumed.get("model_output_tokens"), 0)
    ):
        errors.append("budget.consumed_total_tokens_mismatch")
    exhausted = set(map(str, _list(budget.get("exhausted_dimensions"))))
    if not exhausted.issubset(set(BUDGET_CONSUMED_FIELDS)):
        errors.append("budget.exhausted_dimensions_are_invalid")
    return consumed


def _validate_run_actions(
    record: dict[str, Any],
    protocol: dict[str, Any],
    consumed: dict[str, Any],
    errors: list[str],
) -> None:
    trace = _dict(record.get("action_trace"))
    if not _is_uuid(str(trace.get("trace_id") or "")):
        errors.append("action_trace.trace_id_must_be_uuid")
    if trace.get("contains_private_reasoning") is not False:
        errors.append("action_trace.contains_private_reasoning_must_be_false")
    if trace.get("complete") is not True:
        errors.append("action_trace.complete_must_be_true")
    actions = [_dict(item) for item in _list(trace.get("actions"))]
    if len(actions) != _int(consumed.get("actions"), -1):
        errors.append("action_trace.count_differs_from_budget")
    registered = {
        str(_dict(item).get("action_id") or "")
        for item in _list(protocol.get("action_registry"))
    }
    policy = _dict(record.get("policy"))
    planner_mode = str(policy.get("planner_mode") or "")
    llm_selected = False
    for index, action in enumerate(actions, start=1):
        if _int(action.get("step"), 0) != index:
            errors.append(f"action_trace.non_sequential_step:{index}")
        action_id = str(action.get("action_id") or "")
        if action_id not in registered:
            errors.append(f"action_trace.unregistered_action:{action_id}")
        selected_by = str(action.get("selected_by") or "")
        if selected_by not in ACTION_SELECTORS:
            errors.append(f"action_trace.invalid_selector:{index}")
        llm_selected = llm_selected or selected_by == "llm_planner"
        if not str(action.get("reason_code") or ""):
            errors.append(f"action_trace.reason_code_is_required:{index}")
        if not isinstance(action.get("evidence_refs"), list):
            errors.append(f"action_trace.evidence_refs_are_required:{index}")
        if str(action.get("safety_gate") or "") not in ACTION_GATE_STATUSES:
            errors.append(f"action_trace.safety_gate_is_invalid:{index}")
        if str(action.get("verify_status") or "") not in VERIFY_STATUSES:
            errors.append(f"action_trace.verify_status_is_invalid:{index}")
        if not str(action.get("output_artifact_ref") or ""):
            errors.append(f"action_trace.output_artifact_ref_is_required:{index}")
    if planner_mode == "fixed" and any(
        str(item.get("selected_by") or "") not in {"fixed_workflow", "safety_controller"}
        for item in actions
    ):
        errors.append("fixed_workflow_contains_adaptive_action_selection")
    if planner_mode == "rule" and any(
        str(item.get("selected_by") or "") not in {"rule_controller", "safety_controller"}
        for item in actions
    ):
        errors.append("rule_planner_contains_non_rule_action_selection")
    outcome_status = str(_dict(record.get("outcome")).get("status") or "")
    if (
        planner_mode == "llm"
        and actions
        and outcome_status not in {"provider_blocker", "environment_blocker"}
        and not llm_selected
    ):
        errors.append("llm_planner_trial_requires_llm_selected_action")


def _validate_run_candidates(
    record: dict[str, Any],
    errors: list[str],
) -> list[dict[str, Any]]:
    candidates = [_dict(item) for item in _list(record.get("candidates"))]
    consumed_count = _int(_dict(_dict(record.get("budget")).get("consumed")).get("candidates"), -1)
    if len(candidates) != consumed_count:
        errors.append("candidate_count_differs_from_budget")
    ids: set[str] = set()
    policy = _dict(record.get("policy"))
    strategy = str(policy.get("patch_strategy") or "")
    reflection_enabled = policy.get("reflection_enabled") is True
    for index, candidate in enumerate(candidates, start=1):
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id:
            errors.append(f"candidate_id_is_required:{index}")
        elif candidate_id in ids:
            errors.append(f"duplicate_candidate_id:{candidate_id}")
        ids.add(candidate_id)
        if _int(candidate.get("candidate_index"), 0) != index:
            errors.append(f"candidate_index_is_not_sequential:{index}")
        family = str(candidate.get("generator_family") or "")
        if family not in GENERATOR_FAMILIES:
            errors.append(f"candidate_generator_family_is_invalid:{index}")
        if strategy == "llm_only" and family != "llm":
            errors.append("llm_only_strategy_contains_non_llm_candidate")
        reflection_round = _int(candidate.get("reflection_round"), -1)
        if reflection_round < 0:
            errors.append(f"candidate_reflection_round_is_invalid:{index}")
        if reflection_round > 0 and not str(candidate.get("parent_candidate_id") or ""):
            errors.append(f"reflection_candidate_requires_parent:{index}")
        if not reflection_enabled and reflection_round > 0:
            errors.append("reflection_disabled_policy_contains_reflection_candidate")
        patch_hash = str(candidate.get("patch_sha256") or "")
        if not SHA256_PATTERN.fullmatch(patch_hash):
            errors.append(f"candidate_patch_sha256_is_invalid:{index}")
        touched_files = [str(item) for item in _list(candidate.get("touched_files"))]
        if not touched_files or any(_unsafe_relative_value(item) for item in touched_files):
            errors.append(f"candidate_touched_files_are_invalid:{index}")
    return candidates


def _validate_run_model_usage_cost(
    record: dict[str, Any],
    protocol: dict[str, Any],
    consumed: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> None:
    model = _dict(record.get("model"))
    frozen_model = _dict(protocol.get("model"))
    for field in ("provider", "model_id", "temperature"):
        if model.get(field) != frozen_model.get(field):
            errors.append(f"model.{field}_differs_from_protocol")
    frozen_prompts = {
        str(_dict(item).get("id") or ""): str(_dict(item).get("sha256") or "")
        for item in _list(protocol.get("prompts"))
    }
    for index, prompt in enumerate(_list(model.get("prompts_used"))):
        prompt_ref = _dict(prompt)
        prompt_id = str(prompt_ref.get("id") or "")
        if prompt_id not in frozen_prompts:
            errors.append(f"model.prompt_is_not_frozen:{index}")
        elif str(prompt_ref.get("sha256") or "") != frozen_prompts[prompt_id]:
            errors.append(f"model.prompt_hash_differs_from_protocol:{index}")

    usage = _dict(record.get("usage"))
    for field in (
        "input_tokens",
        "cache_hit_input_tokens",
        "cache_miss_input_tokens",
        "output_tokens",
        "total_tokens",
    ):
        if _int(usage.get(field), -1) < 0:
            errors.append(f"usage.{field}_must_be_non_negative")
    if _int(usage.get("input_tokens"), 0) != (
        _int(usage.get("cache_hit_input_tokens"), 0)
        + _int(usage.get("cache_miss_input_tokens"), 0)
    ):
        errors.append("usage.input_cache_split_mismatch")
    if _int(usage.get("total_tokens"), 0) != (
        _int(usage.get("input_tokens"), 0) + _int(usage.get("output_tokens"), 0)
    ):
        errors.append("usage.total_tokens_mismatch")
    if _int(usage.get("input_tokens"), 0) != _int(consumed.get("model_input_tokens"), 0):
        errors.append("usage.input_tokens_differ_from_budget")
    if _int(usage.get("output_tokens"), 0) != _int(consumed.get("model_output_tokens"), 0):
        errors.append("usage.output_tokens_differ_from_budget")
    if str(usage.get("source") or "") != "provider_usage":
        warnings.append("usage_not_from_provider")

    cost = _dict(record.get("cost"))
    pricing = _dict(protocol.get("pricing"))
    if str(cost.get("currency") or "") != str(pricing.get("currency") or ""):
        errors.append("cost.currency_differs_from_protocol")
    if str(cost.get("pricing_snapshot_id") or "") != str(pricing.get("snapshot_id") or ""):
        errors.append("cost.pricing_snapshot_id_differs_from_protocol")
    expected_cost = compute_run_record_cost(record, protocol)
    actual_cost = _float(cost.get("actual_cost_usd"), -1.0)
    if actual_cost < 0 or abs(actual_cost - expected_cost) > 0.00000001:
        errors.append("cost.actual_cost_usd_mismatch")
    if abs(actual_cost - _float(consumed.get("actual_cost_usd"), -1.0)) > 0.00000001:
        errors.append("cost.actual_cost_usd_differs_from_budget")

    timing = _dict(record.get("timing"))
    if _float(timing.get("latency_ms"), -1.0) < 0:
        errors.append("timing.latency_ms_must_be_non_negative")
    if _int(timing.get("provider_retry_count"), -1) < 0:
        errors.append("timing.provider_retry_count_must_be_non_negative")
    reasons = _list(timing.get("provider_retry_reasons"))
    if len(reasons) != _int(timing.get("provider_retry_count"), -1):
        errors.append("timing.provider_retry_reasons_count_mismatch")


def _validate_run_outcome(
    record: dict[str, Any],
    candidates: list[dict[str, Any]],
    errors: list[str],
) -> None:
    validation = _dict(record.get("validation"))
    if validation.get("ast_valid") not in {True, False, None}:
        errors.append("validation.ast_valid_is_invalid")
    for field in ("safety_gate", "targeted_tests", "full_regression", *SEMANTIC_GATES):
        if str(validation.get(field) or "") not in VALIDATION_STATUSES:
            errors.append(f"validation.{field}_is_invalid")
    if validation.get("semantic_oracle_complete") not in {True, False}:
        errors.append("validation.semantic_oracle_complete_must_be_boolean")
    if validation.get("semantic_claim_eligible") not in {True, False}:
        errors.append("validation.semantic_claim_eligible_must_be_boolean")

    outcome = _dict(record.get("outcome"))
    status = str(outcome.get("status") or "")
    if status not in OUTCOME_STATUSES:
        errors.append("outcome.status_is_invalid")
    candidate_by_id = {str(item.get("candidate_id") or ""): item for item in candidates}
    winning_id = str(outcome.get("winning_candidate_id") or "")
    if status == "verified_repair":
        if winning_id not in candidate_by_id:
            errors.append("verified_repair_requires_winning_candidate")
        required = {
            "ast_valid": validation.get("ast_valid") is True,
            "safety_gate": validation.get("safety_gate") == "pass",
            "targeted_tests": validation.get("targeted_tests") == "pass",
            "full_regression": validation.get("full_regression") == "pass",
            **{field: validation.get(field) == "pass" for field in SEMANTIC_GATES},
        }
        for gate, passed in required.items():
            if not passed:
                errors.append(f"verified_repair_requires_{gate}_pass")
        if validation.get("semantic_oracle_complete") is not True:
            errors.append("verified_repair_requires_complete_semantic_oracle")
        if validation.get("semantic_claim_eligible") is not True:
            errors.append("verified_repair_requires_semantic_claim_eligibility")
    if validation.get("semantic_oracle_complete") is False and status == "verified_repair":
        errors.append("incomplete_oracle_cannot_produce_verified_repair")
    winning_candidate = candidate_by_id.get(winning_id, {})
    reflection_round = _int(winning_candidate.get("reflection_round"), 0)
    if outcome.get("reflection_recovered") is True and reflection_round < 1:
        errors.append("reflection_recovery_requires_reflection_candidate")
    if outcome.get("direct_success") is True and reflection_round != 0:
        errors.append("direct_success_requires_round_zero_candidate")


def _validate_run_failure_and_context(
    record: dict[str, Any],
    errors: list[str],
) -> None:
    outcome_status = str(_dict(record.get("outcome")).get("status") or "")
    failure = _dict(record.get("failure"))
    layer = str(failure.get("layer") or "")
    category = str(failure.get("category") or "")
    if layer not in FAILURE_LAYERS:
        errors.append("failure.layer_is_invalid")
    if outcome_status == "verified_repair" and layer != "none":
        errors.append("verified_repair_requires_no_failure")
    if outcome_status == "provider_blocker":
        if layer != "provider" or category not in PROVIDER_FAILURE_CATEGORIES:
            errors.append("provider_blocker_requires_provider_failure_category")
    if outcome_status == "environment_blocker":
        if layer != "environment" or category not in ENVIRONMENT_FAILURE_CATEGORIES:
            errors.append("environment_blocker_requires_environment_failure_category")

    context = _dict(record.get("model_context"))
    for field in (
        "contains_gold_patch",
        "contains_fix_commit",
        "contains_hidden_test_answer",
        "contains_repository_instruction_as_authority",
    ):
        if context.get(field) is not False:
            errors.append(f"model_context.{field}_must_be_false")
    if not str(context.get("artifact_ref") or ""):
        errors.append("model_context.artifact_ref_is_required")


def validate_run_records(
    records: list[dict[str, Any]],
    *,
    protocol: dict[str, Any],
    require_complete: bool = False,
) -> dict[str, Any]:
    audits = [validate_run_record(item, protocol=protocol) for item in records]
    errors = [
        f"record:{audit.get('run_id') or index}:{error}"
        for index, audit in enumerate(audits)
        for error in _list(audit.get("errors"))
    ]
    run_ids = [str(item.get("run_id") or "") for item in records]
    duplicate_ids = sorted({item for item in run_ids if run_ids.count(item) > 1})
    errors.extend(f"duplicate_run_id:{item}" for item in duplicate_ids)

    trial_keys: dict[tuple[str, str, str, int], set[str]] = {}
    for record in records:
        case = _dict(record.get("case"))
        experiment = _dict(record.get("experiment"))
        policy = _dict(record.get("policy"))
        experiment_id = str(experiment.get("experiment_id") or "")
        frozen = _dict(_dict(protocol.get("experiments")).get(experiment_id))
        axis = str(frozen.get("allocation_axis") or "")
        allocation = (
            str(policy.get("variant") or "")
            if axis == "policy_variant"
            else str(policy.get("patch_strategy") or "")
        )
        key = (
            str(case.get("case_id") or ""),
            experiment_id,
            allocation,
            _int(experiment.get("trial_index"), 0),
        )
        trial_keys.setdefault(key, set()).add(str(experiment.get("trial_id") or ""))
    for key, trial_ids in trial_keys.items():
        if len(trial_ids) != 1:
            errors.append(f"trial_key_has_multiple_trial_ids:{key}")

    completeness: dict[str, Any] = {}
    if require_complete:
        for experiment_id, experiment_raw in _dict(protocol.get("experiments")).items():
            experiment = _dict(experiment_raw)
            allocations = [str(item) for item in _list(experiment.get("allocations"))]
            expected_trials = list(range(1, _int(experiment.get("trials_per_allocation"), 0) + 1))
            case_ids = sorted(
                {
                    str(_dict(item.get("case")).get("case_id") or "")
                    for item in records
                    if str(_dict(item.get("experiment")).get("experiment_id") or "")
                    == experiment_id
                }
            )
            completeness[experiment_id] = {}
            if len(case_ids) != _int(experiment.get("case_count"), 0):
                errors.append(f"incomplete_case_count:{experiment_id}")
            for case_id in case_ids:
                completeness[experiment_id][case_id] = {}
                for allocation in allocations:
                    indices = sorted(
                        key[3]
                        for key in trial_keys
                        if key[:3] == (case_id, experiment_id, allocation)
                    )
                    complete = indices == expected_trials
                    completeness[experiment_id][case_id][allocation] = {
                        "expected_trial_indices": expected_trials,
                        "observed_trial_indices": indices,
                        "complete": complete,
                    }
                    if not complete:
                        errors.append(f"incomplete_trials:{experiment_id}:{case_id}:{allocation}")

    return {
        "status": "pass" if not errors else "fail",
        "record_count": len(records),
        "valid_record_count": sum(item.get("status") == "pass" for item in audits),
        "error_count": len(errors),
        "errors": errors,
        "record_audits": audits,
        "completeness": completeness,
    }


def compute_run_record_cost(
    record: dict[str, Any],
    protocol: dict[str, Any],
) -> float:
    usage = _dict(record.get("usage"))
    pricing = _dict(protocol.get("pricing"))
    cost = (
        _int(usage.get("cache_hit_input_tokens"), 0)
        * _float(pricing.get("cache_hit_input_usd_per_million_tokens"), 0.0)
        + _int(usage.get("cache_miss_input_tokens"), 0)
        * _float(pricing.get("cache_miss_input_usd_per_million_tokens"), 0.0)
        + _int(usage.get("output_tokens"), 0)
        * _float(pricing.get("output_usd_per_million_tokens"), 0.0)
    ) / 1_000_000.0
    return round(cost, 10)


def build_protocol_audit(
    protocol_path: str | Path,
    *,
    root: str | Path,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    protocol = load_experiment_protocol(protocol_path)
    validation = validate_experiment_protocol(protocol, root=root_path)
    baseline = _dict(protocol.get("baseline"))
    tag = str(baseline.get("tag") or "")
    expected_commit = str(baseline.get("commit_sha") or "")
    resolved_commit = _git_output(root_path, ["rev-list", "-n", "1", tag])
    tag_matches = bool(resolved_commit) and resolved_commit == expected_commit
    errors = list(validation["errors"])
    if not tag_matches:
        errors.append("baseline_tag_does_not_resolve_to_pinned_commit")
    status = "pass" if not errors else "fail"
    return {
        "schema_version": PROTOCOL_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "reason": "v4_phase0_protocol_frozen" if status == "pass" else "v4_phase0_protocol_invalid",
        "protocol_path": _portable_path(Path(protocol_path), root_path),
        "protocol_sha256": validation["protocol_sha256"],
        "protocol_validation": {**validation, "errors": errors, "error_count": len(errors)},
        "run_record_schema": run_record_schema(),
        "baseline": {
            "tag": tag,
            "expected_commit": expected_commit,
            "resolved_commit": resolved_commit,
            "tag_matches": tag_matches,
        },
        "current_environment": {
            "head_commit": _git_output(root_path, ["rev-parse", "HEAD"]),
            "branch": _git_output(root_path, ["branch", "--show-current"]),
            "python_version": sys.version.split()[0],
            "platform": sys.platform,
        },
        "benchmark": _dict(protocol.get("benchmark")),
        "experiments": _dict(protocol.get("experiments")),
        "budgets": _dict(protocol.get("budgets")),
        "frozen_model": _dict(protocol.get("model")),
        "frozen_pricing": _dict(protocol.get("pricing")),
        "prompt_hashes": validation["prompt_hashes"],
        "notes": [
            "This audit is offline and does not call a model.",
            "A separate hashed benchmark manifest must be locked before live V4 trials.",
            "Every comparison allocation shares one frozen budget group and model.",
            "Run records persist structured evidence and actions, never private reasoning or raw provider responses.",
            "Live external calls require a new explicit user authorization for V4.",
        ],
    }


def render_protocol_audit_markdown(audit: dict[str, Any]) -> str:
    validation = _dict(audit.get("protocol_validation"))
    baseline = _dict(audit.get("baseline"))
    model = _dict(audit.get("frozen_model"))
    benchmark = _dict(audit.get("benchmark"))
    lines = [
        "# V4 Phase 0 Experiment Protocol Audit",
        "",
        f"- Status: `{audit.get('status')}`",
        f"- Protocol SHA-256: `{audit.get('protocol_sha256')}`",
        f"- V3 baseline: `{baseline.get('tag')}` -> `{baseline.get('expected_commit')}`",
        f"- Baseline tag verified: `{baseline.get('tag_matches')}`",
        f"- Provider/model: `{model.get('provider')}/{model.get('model_id')}`",
        f"- Benchmark target: `{benchmark.get('minimum_cases')}` cases / `{benchmark.get('minimum_repositories')}` repositories",
        f"- Split policy: `{benchmark.get('split_policy')}`",
        f"- Protocol errors: `{validation.get('error_count')}`",
        "",
        "## Equal-Budget Experiments",
        "",
        "| Experiment | Cases | Allocation axis | Allocations | Trials |",
        "| --- | ---: | --- | --- | ---: |",
    ]
    for experiment_id, raw_experiment in _dict(audit.get("experiments")).items():
        experiment = _dict(raw_experiment)
        allocations = ", ".join(map(str, _list(experiment.get("allocations"))))
        lines.append(
            f"| {experiment_id} | {experiment.get('case_count')} | "
            f"{experiment.get('allocation_axis')} | {allocations} | "
            f"{experiment.get('trials_per_allocation')} |"
        )
    lines.extend(
        [
            "",
            "## Frozen Prompts",
            "",
            "| Prompt | SHA-256 |",
            "| --- | --- |",
        ]
    )
    for prompt_id, digest in sorted(_dict(audit.get("prompt_hashes")).items()):
        lines.append(f"| {prompt_id} | `{digest}` |")
    lines.extend(
        [
            "",
            "## Claim Boundary",
            "",
            "This artifact freezes the V4 comparison contract. It does not call a model, lock the future 50-case manifest, or claim an Agent improvement.",
            "The Full Agent claim is allowed only after repository-disjoint, equal-budget, complete-denominator trials pass RunRecord audit.",
            "",
        ]
    )
    return "\n".join(lines)


def write_protocol_audit_artifacts(
    audit: dict[str, Any],
    output_prefix: str | Path,
) -> dict[str, str]:
    prefix = Path(output_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = prefix.with_suffix(".json")
    markdown_path = prefix.with_suffix(".md")
    _write_text_lf(json_path, json.dumps(audit, indent=2, ensure_ascii=False) + "\n")
    _write_text_lf(markdown_path, render_protocol_audit_markdown(audit))
    return {"json": str(json_path), "markdown": str(markdown_path)}


def _write_text_lf(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _validate_hashed_path(
    root: Path,
    relative_path: str,
    expected_hash: str,
    label: str,
    errors: list[str],
) -> str:
    safe_path = _safe_relative_path(root, relative_path)
    if safe_path is None:
        errors.append(f"{label}_path_is_unsafe")
        return ""
    if not safe_path.is_file():
        errors.append(f"{label}_file_is_missing")
        return ""
    actual_hash = sha256_file(safe_path)
    if not SHA256_PATTERN.fullmatch(expected_hash):
        errors.append(f"{label}_sha256_is_invalid")
    elif actual_hash != expected_hash:
        errors.append(f"{label}_sha256_mismatch")
    return actual_hash


def _safe_relative_path(root: Path, value: str) -> Path | None:
    if _unsafe_relative_value(value):
        return None
    pure = PurePosixPath(value.replace("\\", "/"))
    candidate = (root / Path(*pure.parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _unsafe_relative_value(value: str) -> bool:
    pure = PurePosixPath(value.replace("\\", "/"))
    return not value or pure.is_absolute() or ".." in pure.parts


def _find_sensitive_values(value: Any, path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            item_path = f"{path}.{key_text}"
            if key_text.lower() in FORBIDDEN_FIELD_NAMES:
                hits.append(item_path)
            hits.extend(_find_sensitive_values(item, item_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(_find_sensitive_values(item, f"{path}[{index}]"))
    elif isinstance(value, str) and SECRET_PATTERN.search(value):
        hits.append(path)
    return sorted(set(hits))


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False


def _git_output(root: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _portable_path(path: Path, root: Path) -> str:
    absolute = path if path.is_absolute() else root / path
    try:
        return absolute.resolve().relative_to(root).as_posix()
    except ValueError:
        return absolute.resolve().as_posix()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and audit the V4 Agent-effectiveness protocol."
    )
    parser.add_argument("protocol", help="Path to the V4 protocol JSON file.")
    parser.add_argument("output_prefix", help="Output prefix for JSON and Markdown artifacts.")
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument("--require-pass", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    audit = build_protocol_audit(args.protocol, root=args.root)
    write_protocol_audit_artifacts(audit, args.output_prefix)
    if args.format == "json":
        print(json.dumps(audit, indent=2, ensure_ascii=False))
    else:
        print(render_protocol_audit_markdown(audit))
    if args.require_pass and audit["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
