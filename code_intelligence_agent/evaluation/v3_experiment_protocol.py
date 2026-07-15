from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


PROTOCOL_SCHEMA_VERSION = "3.0"
RUN_RECORD_SCHEMA_VERSION = "3.0"
STRATEGY_MODES = {"rule", "llm", "hybrid"}
GENERATOR_FAMILIES = {"rule", "llm"}
BENCHMARK_SPLITS = {"development", "validation", "test"}
GATE_STATUSES = {"pass", "fail", "not_run", "blocker"}
SEMANTIC_STATUSES = {"pass", "fail", "not_applicable", "not_run", "blocker"}
OUTCOME_STATUSES = {
    "verified_repair",
    "candidate",
    "unverified_suggestion",
    "failed",
    "provider_blocker",
    "environment_blocker",
    "safety_rejected",
}
FAILURE_LAYERS = {
    "none",
    "provider",
    "environment",
    "localization",
    "generation",
    "syntax",
    "safety",
    "targeted_test",
    "full_regression",
    "semantic_validation",
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
}
FORBIDDEN_FIELD_NAMES = {
    "api_key",
    "authorization",
    "raw",
    "raw_response",
    "response_body",
    "secret",
}
SECRET_PATTERN = re.compile(r"\bsk-[A-Za-z0-9._-]{16,}\b", re.IGNORECASE)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def load_experiment_protocol(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Experiment protocol must be a JSON object.")
    return value


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_experiment_protocol(
    protocol: dict[str, Any],
    *,
    root: str | Path,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    if str(protocol.get("schema_version") or "") != PROTOCOL_SCHEMA_VERSION:
        errors.append("schema_version_must_be_3.0")

    baseline = _dict(protocol.get("baseline"))
    if not COMMIT_PATTERN.fullmatch(str(baseline.get("commit_sha") or "")):
        errors.append("baseline.commit_sha_must_be_full_sha")
    if not str(baseline.get("tag") or ""):
        errors.append("baseline.tag_is_required")

    runtime = _dict(protocol.get("runtime"))
    requirements_path = str(runtime.get("requirements_path") or "")
    requirements_sha = str(runtime.get("requirements_sha256") or "")
    if not str(runtime.get("benchmark_default_python") or ""):
        errors.append("runtime.benchmark_default_python_is_required")
    _validate_hashed_path(
        root_path,
        requirements_path,
        requirements_sha,
        "runtime.requirements",
        errors,
    )

    model = _dict(protocol.get("model"))
    for field in ("provider", "model_id", "api_base", "endpoint"):
        if not str(model.get(field) or ""):
            errors.append(f"model.{field}_is_required")
    if model.get("temperature") != 0:
        errors.append("model.temperature_must_be_zero_for_v3")
    if str(model.get("api_key_source") or "") != "environment_only":
        errors.append("model.api_key_source_must_be_environment_only")
    if not _list(model.get("api_key_env_names")):
        errors.append("model.api_key_env_names_are_required")

    randomness = _dict(protocol.get("randomness"))
    if _int(randomness.get("rule_trials"), 0) != 1:
        errors.append("randomness.rule_trials_must_equal_one")
    if _int(randomness.get("llm_trials"), 0) < 3:
        errors.append("randomness.llm_trials_must_be_at_least_three")
    if _int(randomness.get("hybrid_trials"), 0) < 3:
        errors.append("randomness.hybrid_trials_must_be_at_least_three")
    if randomness.get("provider_retry_creates_new_trial") is not False:
        errors.append("provider_retry_must_not_create_new_trial")
    if randomness.get("share_candidate_history_between_trials") is not False:
        errors.append("candidate_history_must_not_cross_trials")
    if randomness.get("seed") is not None:
        warnings.append("configured_seed_requires_provider_support_evidence")

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
    source_urls = [str(item) for item in _list(pricing.get("source_urls"))]
    if not source_urls or any(not item.startswith("https://") for item in source_urls):
        errors.append("pricing.https_source_urls_are_required")

    prompt_hashes: dict[str, str] = {}
    prompt_ids: set[str] = set()
    prompts = [_dict(item) for item in _list(protocol.get("prompts"))]
    if not prompts:
        errors.append("prompts_are_required")
    for index, prompt in enumerate(prompts):
        prompt_id = str(prompt.get("id") or "")
        if not prompt_id:
            errors.append(f"prompts[{index}].id_is_required")
            continue
        if prompt_id in prompt_ids:
            errors.append(f"duplicate_prompt_id:{prompt_id}")
        prompt_ids.add(prompt_id)
        path = str(prompt.get("path") or "")
        expected_hash = str(prompt.get("sha256") or "")
        actual_hash = _validate_hashed_path(
            root_path,
            path,
            expected_hash,
            f"prompt:{prompt_id}",
            errors,
        )
        if actual_hash:
            prompt_hashes[prompt_id] = actual_hash

    metrics = {str(item) for item in _list(protocol.get("required_metrics"))}
    required_metrics = {
        "pass_at_1",
        "pass_at_3",
        "ast_valid_rate",
        "safety_pass_rate",
        "targeted_test_pass_rate",
        "full_regression_pass_rate",
        "verified_repair_rate",
        "reflection_recovery_rate",
        "token_usage",
        "actual_cost_usd",
        "latency_ms",
    }
    missing_metrics = sorted(required_metrics - metrics)
    if missing_metrics:
        errors.append("missing_required_metrics:" + ",".join(missing_metrics))

    safety = _dict(protocol.get("safety"))
    if safety.get("gold_patch_in_model_context") is not False:
        errors.append("gold_patch_must_be_excluded_from_model_context")
    if safety.get("fix_commit_content_in_model_context") is not False:
        errors.append("fix_commit_content_must_be_excluded_from_model_context")
    if safety.get("free_form_shell") is not False:
        errors.append("free_form_shell_must_be_disabled")
    if safety.get("raw_provider_response_in_run_record") is not False:
        errors.append("raw_provider_response_must_be_excluded_from_run_record")

    secret_hits = _find_sensitive_values(protocol)
    errors.extend(f"sensitive_protocol_field:{item}" for item in secret_hits)
    fingerprint_source = json.loads(json.dumps(protocol))
    fingerprint_source.pop("protocol_sha256", None)
    fingerprint = canonical_json_sha256(fingerprint_source)
    expected_fingerprint = str(protocol.get("protocol_sha256") or "")
    if expected_fingerprint and expected_fingerprint != fingerprint:
        errors.append("protocol_sha256_mismatch")
    if not expected_fingerprint:
        warnings.append("protocol_sha256_not_pinned")
    return {
        "status": "pass" if not errors else "fail",
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "prompt_hashes": prompt_hashes,
        "protocol_sha256": fingerprint,
    }


def run_record_schema() -> dict[str, Any]:
    return {
        "$id": "code-intelligence-agent-v3-run-record",
        "schema_version": RUN_RECORD_SCHEMA_VERSION,
        "required_sections": [
            "case",
            "strategy",
            "candidate",
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
        "strategy_modes": sorted(STRATEGY_MODES),
        "generator_families": sorted(GENERATOR_FAMILIES),
        "benchmark_splits": sorted(BENCHMARK_SPLITS),
        "outcome_statuses": sorted(OUTCOME_STATUSES),
        "failure_layers": sorted(FAILURE_LAYERS),
        "verified_repair_authority": [
            "ast_valid",
            "safety_gate",
            "targeted_tests",
            "full_regression",
            "semantic_validation",
        ],
        "forbidden_recursive_fields": sorted(FORBIDDEN_FIELD_NAMES),
    }


def validate_run_record(
    record: dict[str, Any],
    *,
    protocol: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if str(record.get("schema_version") or "") != RUN_RECORD_SCHEMA_VERSION:
        errors.append("schema_version_must_be_3.0")
    run_id = str(record.get("run_id") or "")
    if not _is_uuid(run_id):
        errors.append("run_id_must_be_uuid")

    required_sections = run_record_schema()["required_sections"]
    for section in required_sections:
        if not isinstance(record.get(section), dict):
            errors.append(f"{section}_section_is_required")

    case = _dict(record.get("case"))
    if not str(case.get("case_id") or ""):
        errors.append("case.case_id_is_required")
    if not str(case.get("repository") or ""):
        errors.append("case.repository_is_required")
    if not COMMIT_PATTERN.fullmatch(str(case.get("bug_commit_sha") or "")):
        errors.append("case.bug_commit_sha_must_be_full_sha")
    if str(case.get("benchmark_split") or "") not in BENCHMARK_SPLITS:
        errors.append("case.benchmark_split_is_invalid")

    strategy = _dict(record.get("strategy"))
    mode = str(strategy.get("mode") or "")
    if mode not in STRATEGY_MODES:
        errors.append("strategy.mode_is_invalid")
    trial_index = _int(strategy.get("trial_index"), 0)
    if trial_index < 1:
        errors.append("strategy.trial_index_must_be_positive")
    if not _is_uuid(str(strategy.get("trial_id") or "")):
        errors.append("strategy.trial_id_must_be_uuid")
    if strategy.get("independent_trial") is not True:
        errors.append("strategy.independent_trial_must_be_true")
    if mode == "rule" and trial_index != 1:
        errors.append("rule_strategy_trial_index_must_equal_one")

    candidate = _dict(record.get("candidate"))
    family = str(candidate.get("generator_family") or "")
    if family not in GENERATOR_FAMILIES:
        errors.append("candidate.generator_family_is_invalid")
    if _int(candidate.get("candidate_index"), 0) < 1:
        errors.append("candidate.candidate_index_must_be_positive")
    if not str(candidate.get("candidate_id") or ""):
        errors.append("candidate.candidate_id_is_required")
    generator_id = str(candidate.get("generator_id") or "")
    if not generator_id:
        errors.append("candidate.generator_id_is_required")
    if mode == "rule" and family != "rule":
        errors.append("rule_strategy_requires_rule_generator")
    if mode == "llm" and family != "llm":
        errors.append("llm_strategy_requires_llm_generator")
    reflection_round = _int(candidate.get("reflection_round"), -1)
    if reflection_round < 0:
        errors.append("candidate.reflection_round_must_be_non_negative")
    if reflection_round > 0 and not str(candidate.get("parent_candidate_id") or ""):
        errors.append("reflection_candidate_requires_parent_candidate_id")

    model = _dict(record.get("model"))
    usage = _dict(record.get("usage"))
    cost = _dict(record.get("cost"))
    token_fields = (
        "input_tokens",
        "cache_hit_input_tokens",
        "cache_miss_input_tokens",
        "output_tokens",
        "total_tokens",
    )
    for field in token_fields:
        if _int(usage.get(field), -1) < 0:
            errors.append(f"usage.{field}_must_be_non_negative")
    total_tokens = _int(usage.get("total_tokens"), 0)
    component_total = (
        _int(usage.get("input_tokens"), 0)
        + _int(usage.get("output_tokens"), 0)
    )
    if total_tokens != component_total:
        errors.append("usage.total_tokens_mismatch")
    input_tokens = _int(usage.get("input_tokens"), 0)
    split_input = (
        _int(usage.get("cache_hit_input_tokens"), 0)
        + _int(usage.get("cache_miss_input_tokens"), 0)
    )
    if input_tokens != split_input:
        errors.append("usage.input_cache_split_mismatch")

    if family == "llm":
        for field in ("provider", "model_id", "prompt_id", "prompt_sha256"):
            if not str(model.get(field) or ""):
                errors.append(f"model.{field}_is_required_for_llm_candidate")
        if model.get("temperature") != 0:
            errors.append("model.temperature_must_equal_zero")
        if not SHA256_PATTERN.fullmatch(str(model.get("prompt_sha256") or "")):
            errors.append("model.prompt_sha256_is_invalid")
        if str(usage.get("source") or "") != "provider_usage":
            warnings.append("llm_usage_not_from_provider")
    if family == "rule":
        if any(_int(usage.get(field), 0) != 0 for field in token_fields):
            errors.append("rule_candidate_tokens_must_equal_zero")
        if _float(cost.get("actual_cost_usd"), 0.0) != 0.0:
            errors.append("rule_candidate_cost_must_equal_zero")
        if any(str(model.get(field) or "") for field in ("provider", "model_id", "prompt_id")):
            errors.append("rule_candidate_model_fields_must_be_empty")

    if protocol is not None and family == "llm":
        protocol_model = _dict(protocol.get("model"))
        protocol_pricing = _dict(protocol.get("pricing"))
        protocol_prompts = {
            str(_dict(item).get("id") or ""): str(_dict(item).get("sha256") or "")
            for item in _list(protocol.get("prompts"))
        }
        if str(model.get("provider") or "") != str(protocol_model.get("provider") or ""):
            errors.append("model.provider_differs_from_protocol")
        if str(model.get("model_id") or "") != str(protocol_model.get("model_id") or ""):
            errors.append("model.model_id_differs_from_protocol")
        prompt_id = str(model.get("prompt_id") or "")
        if prompt_id not in protocol_prompts:
            errors.append("model.prompt_id_not_in_protocol")
        elif str(model.get("prompt_sha256") or "") != protocol_prompts[prompt_id]:
            errors.append("model.prompt_sha256_differs_from_protocol")
        if str(cost.get("pricing_snapshot_id") or "") != str(
            protocol_pricing.get("snapshot_id") or ""
        ):
            errors.append("cost.pricing_snapshot_id_differs_from_protocol")
        if str(cost.get("currency") or "") != str(
            protocol_pricing.get("currency") or ""
        ):
            errors.append("cost.currency_differs_from_protocol")
        expected_cost = compute_run_record_cost(record, protocol)
        actual_cost = _float(cost.get("actual_cost_usd"), -1.0)
        if actual_cost < 0 or abs(actual_cost - expected_cost) > 0.00000001:
            errors.append("cost.actual_cost_usd_mismatch")

    timing = _dict(record.get("timing"))
    if _float(timing.get("latency_ms"), -1.0) < 0:
        errors.append("timing.latency_ms_must_be_non_negative")
    retry_count = _int(timing.get("provider_retry_count"), -1)
    retry_reasons = [str(item) for item in _list(timing.get("provider_retry_reasons"))]
    if retry_count < 0:
        errors.append("timing.provider_retry_count_must_be_non_negative")
    if retry_count != len(retry_reasons):
        errors.append("timing.provider_retry_reasons_count_mismatch")

    validation = _dict(record.get("validation"))
    ast_valid = validation.get("ast_valid")
    if ast_valid not in {True, False, None}:
        errors.append("validation.ast_valid_is_invalid")
    for field in ("safety_gate", "targeted_tests", "full_regression"):
        if str(validation.get(field) or "") not in GATE_STATUSES:
            errors.append(f"validation.{field}_is_invalid")
    semantic_status = str(validation.get("semantic_validation") or "")
    if semantic_status not in SEMANTIC_STATUSES:
        errors.append("validation.semantic_validation_is_invalid")
    semantic_details = _dict(validation.get("semantic_validation_details"))
    if semantic_status == "pass":
        if not semantic_details:
            errors.append("semantic_pass_requires_validation_details")
        elif str(semantic_details.get("status") or "") != "pass":
            errors.append("semantic_status_must_match_validation_details")
        elif semantic_details.get("claim_eligible") is not True:
            errors.append("semantic_pass_requires_claim_eligible_details")
    elif semantic_details and str(semantic_details.get("status") or "") not in {
        semantic_status,
        "",
    }:
        errors.append("semantic_status_must_match_validation_details")
    if semantic_status == "not_applicable" and not str(
        validation.get("semantic_justification") or ""
    ):
        errors.append("semantic_not_applicable_requires_justification")

    outcome = _dict(record.get("outcome"))
    outcome_status = str(outcome.get("status") or "")
    if outcome_status not in OUTCOME_STATUSES:
        errors.append("outcome.status_is_invalid")
    verified = outcome_status == "verified_repair"
    if verified:
        if ast_valid is not True:
            errors.append("verified_repair_requires_ast_valid")
        for field in ("safety_gate", "targeted_tests", "full_regression"):
            if str(validation.get(field) or "") != "pass":
                errors.append(f"verified_repair_requires_{field}_pass")
        if semantic_status != "pass":
            errors.append("verified_repair_requires_semantic_validation_pass")
    direct_success = bool(outcome.get("direct_success", False))
    reflection_recovered = bool(outcome.get("reflection_recovered", False))
    if direct_success and reflection_round != 0:
        errors.append("direct_success_requires_reflection_round_zero")
    if reflection_recovered and (not verified or reflection_round < 1):
        errors.append("reflection_recovered_requires_verified_reflection_candidate")
    if direct_success and reflection_recovered:
        errors.append("success_cannot_be_direct_and_reflection_recovered")

    failure = _dict(record.get("failure"))
    failure_layer = str(failure.get("layer") or "")
    failure_category = str(failure.get("category") or "")
    if failure_layer not in FAILURE_LAYERS:
        errors.append("failure.layer_is_invalid")
    if verified and failure_layer != "none":
        errors.append("verified_repair_requires_no_failure")
    if failure_layer == "provider" and failure_category not in PROVIDER_FAILURE_CATEGORIES:
        errors.append("provider_failure_category_is_invalid")
    if failure_layer == "environment" and failure_category not in ENVIRONMENT_FAILURE_CATEGORIES:
        errors.append("environment_failure_category_is_invalid")
    if failure_layer not in {"none", "provider", "environment"} and not failure_category:
        errors.append("failure.category_is_required")
    if failure_layer != "none" and not str(failure.get("reason") or ""):
        errors.append("failure.reason_is_required")
    if outcome_status == "provider_blocker" and failure_layer != "provider":
        errors.append("provider_blocker_requires_provider_failure")
    if outcome_status == "environment_blocker" and failure_layer != "environment":
        errors.append("environment_blocker_requires_environment_failure")

    model_context = _dict(record.get("model_context"))
    if family == "llm" and not str(model_context.get("artifact_ref") or ""):
        errors.append("model_context.artifact_ref_is_required_for_llm_candidate")
    for field in ("contains_gold_patch", "contains_fix_commit", "contains_test_answer"):
        if model_context.get(field) is not False:
            errors.append(f"model_context.{field}_must_be_false")
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


def validate_run_records(
    records: list[dict[str, Any]],
    *,
    protocol: dict[str, Any],
    require_complete: bool = False,
) -> dict[str, Any]:
    audits = [validate_run_record(record, protocol=protocol) for record in records]
    errors = [
        f"record:{audit.get('run_id') or index}:{error}"
        for index, audit in enumerate(audits)
        for error in _list(audit.get("errors"))
    ]
    run_ids = [str(record.get("run_id") or "") for record in records]
    duplicate_run_ids = sorted({item for item in run_ids if run_ids.count(item) > 1})
    errors.extend(f"duplicate_run_id:{item}" for item in duplicate_run_ids)

    trial_keys: dict[tuple[str, str, int], set[str]] = {}
    trial_ids_to_keys: dict[str, set[tuple[str, str, int]]] = {}
    for record in records:
        case = _dict(record.get("case"))
        strategy = _dict(record.get("strategy"))
        key = (
            str(case.get("case_id") or ""),
            str(strategy.get("mode") or ""),
            _int(strategy.get("trial_index"), 0),
        )
        trial_id = str(strategy.get("trial_id") or "")
        trial_keys.setdefault(key, set()).add(trial_id)
        trial_ids_to_keys.setdefault(trial_id, set()).add(key)
    for key, trial_ids in trial_keys.items():
        if len(trial_ids) != 1:
            errors.append(f"trial_key_has_multiple_trial_ids:{key}")
    for trial_id, keys in trial_ids_to_keys.items():
        if trial_id and len(keys) != 1:
            errors.append(f"trial_id_reused_across_trials:{trial_id}")

    completeness: dict[str, Any] = {}
    if require_complete:
        randomness = _dict(protocol.get("randomness"))
        expected = {
            "rule": _int(randomness.get("rule_trials"), 1),
            "llm": _int(randomness.get("llm_trials"), 3),
            "hybrid": _int(randomness.get("hybrid_trials"), 3),
        }
        case_ids = sorted(
            {str(_dict(record.get("case")).get("case_id") or "") for record in records}
        )
        for case_id in case_ids:
            completeness[case_id] = {}
            for mode, expected_count in expected.items():
                indices = sorted(
                    {
                        _int(_dict(record.get("strategy")).get("trial_index"), 0)
                        for record in records
                        if str(_dict(record.get("case")).get("case_id") or "") == case_id
                        and str(_dict(record.get("strategy")).get("mode") or "") == mode
                    }
                )
                expected_indices = list(range(1, expected_count + 1))
                complete = indices == expected_indices
                completeness[case_id][mode] = {
                    "expected_trial_indices": expected_indices,
                    "observed_trial_indices": indices,
                    "complete": complete,
                }
                if not complete:
                    errors.append(f"incomplete_trials:{case_id}:{mode}")

    return {
        "status": "pass" if not errors else "fail",
        "record_count": len(records),
        "valid_record_count": sum(audit.get("status") == "pass" for audit in audits),
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
    cache_hit = _int(usage.get("cache_hit_input_tokens"), 0)
    cache_miss = _int(usage.get("cache_miss_input_tokens"), 0)
    output = _int(usage.get("output_tokens"), 0)
    cost = (
        cache_hit
        * _float(pricing.get("cache_hit_input_usd_per_million_tokens"), 0.0)
        + cache_miss
        * _float(pricing.get("cache_miss_input_usd_per_million_tokens"), 0.0)
        + output * _float(pricing.get("output_usd_per_million_tokens"), 0.0)
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
    baseline_commit = str(baseline.get("commit_sha") or "")
    baseline_tag = str(baseline.get("tag") or "")
    tag_commit = _git_output(root_path, ["rev-list", "-n", "1", baseline_tag])
    head_commit = _git_output(root_path, ["rev-parse", "HEAD"])
    branch = _git_output(root_path, ["branch", "--show-current"])
    tag_matches = bool(tag_commit) and tag_commit == baseline_commit
    errors = list(validation["errors"])
    if not tag_matches:
        errors.append("baseline_tag_does_not_resolve_to_pinned_commit")
    status = "pass" if not errors else "fail"
    return {
        "schema_version": PROTOCOL_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "reason": "v3_phase0_protocol_frozen" if status == "pass" else "v3_phase0_protocol_invalid",
        "protocol_path": _portable_path(Path(protocol_path), root_path),
        "protocol_sha256": validation["protocol_sha256"],
        "run_record_schema": run_record_schema(),
        "protocol_validation": {**validation, "errors": errors, "error_count": len(errors)},
        "baseline": {
            "tag": baseline_tag,
            "expected_commit": baseline_commit,
            "resolved_commit": tag_commit,
            "tag_matches": tag_matches,
        },
        "current_environment": {
            "head_commit": head_commit,
            "branch": branch,
            "python_version": sys.version.split()[0],
            "platform": sys.platform,
        },
        "frozen_model": _dict(protocol.get("model")),
        "frozen_pricing": _dict(protocol.get("pricing")),
        "prompt_hashes": validation["prompt_hashes"],
        "randomness": _dict(protocol.get("randomness")),
        "success_authority": _dict(protocol.get("success_authority")),
        "notes": [
            "The protocol audit does not call a model or claim a repair rate.",
            "Live trials require a fresh environment-injected API key.",
            "Provider retries stay inside one trial and are recorded separately.",
        ],
    }


def render_protocol_audit_markdown(audit: dict[str, Any]) -> str:
    validation = _dict(audit.get("protocol_validation"))
    baseline = _dict(audit.get("baseline"))
    model = _dict(audit.get("frozen_model"))
    pricing = _dict(audit.get("frozen_pricing"))
    randomness = _dict(audit.get("randomness"))
    lines = [
        "# V3 Phase 0 Experiment Protocol",
        "",
        f"- Status: `{audit.get('status')}`",
        f"- Protocol SHA-256: `{audit.get('protocol_sha256')}`",
        f"- Baseline: `{baseline.get('tag')}` -> `{baseline.get('expected_commit')}`",
        f"- Baseline tag verified: `{baseline.get('tag_matches')}`",
        f"- Provider/model: `{model.get('provider')}/{model.get('model_id')}`",
        f"- Temperature: `{model.get('temperature')}`",
        f"- Rule trials per case: `{randomness.get('rule_trials')}`",
        f"- LLM trials per case: `{randomness.get('llm_trials')}`",
        f"- Hybrid trials per case: `{randomness.get('hybrid_trials')}`",
        f"- Pricing snapshot: `{pricing.get('snapshot_id')}`",
        f"- Cache-hit input price: `${pricing.get('cache_hit_input_usd_per_million_tokens')}` per million tokens",
        f"- Cache-miss input price: `${pricing.get('cache_miss_input_usd_per_million_tokens')}` per million tokens",
        f"- Output price: `${pricing.get('output_usd_per_million_tokens')}` per million tokens",
        f"- Protocol errors: `{validation.get('error_count')}`",
        "",
        "## Frozen Prompts",
        "",
        "| Prompt | SHA-256 |",
        "| --- | --- |",
    ]
    for prompt_id, digest in sorted(_dict(audit.get("prompt_hashes")).items()):
        lines.append(f"| {prompt_id} | `{digest}` |")
    lines.extend(
        [
            "",
            "## Attribution Rules",
            "",
            "- Rule candidates have zero model tokens and zero model cost.",
            "- LLM and Hybrid trials use independent trial identifiers; provider retries do not create trials.",
            "- Hybrid success is credited to the winning candidate's generator family.",
            "- Provider and environment blockers are excluded from application repair failures and reported separately.",
            "- A verified repair requires AST validity, safety pass, targeted tests, full regression, and applicable semantic validation.",
            "",
            "## Sources",
            "",
            *[
                f"- [{url}]({url})"
                for url in _list(pricing.get("source_urls"))
            ],
            "",
            "## Boundary",
            "",
            "This artifact freezes the evaluation contract. It does not call a model and does not report a live repair rate.",
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
    json_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_path.write_text(render_protocol_audit_markdown(audit), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


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
    elif expected_hash != actual_hash:
        errors.append(f"{label}_sha256_mismatch")
    return actual_hash


def _safe_relative_path(root: Path, value: str) -> Path | None:
    pure = PurePosixPath(value.replace("\\", "/"))
    if not value or pure.is_absolute() or ".." in pure.parts:
        return None
    candidate = (root / Path(*pure.parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


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
    absolute = path if path.is_absolute() else (root / path)
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
        description="Validate and freeze the V3 real-bug experiment protocol."
    )
    parser.add_argument("protocol", help="Path to the V3 protocol JSON file.")
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
