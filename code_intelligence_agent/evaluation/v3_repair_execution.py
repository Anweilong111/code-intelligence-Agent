from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from code_intelligence_agent.agents.llm_client import (
    LLMClient,
    LLMRequestError,
    OpenAICompatibleLLMClient,
    RetryingLLMClient,
)
from code_intelligence_agent.evaluation.v3_experiment_protocol import (
    compute_run_record_cost,
    sha256_file,
)
from code_intelligence_agent.evaluation.v3_real_bug_reproduction import (
    execute_test_commands,
)
from code_intelligence_agent.evaluation.v3_repair_trial import (
    EditableRegion,
    apply_v3_patch_candidate,
    validate_v3_patch_candidate,
)
from code_intelligence_agent.evaluation.v3_semantic_validation import (
    validate_v3_semantic_candidate,
)


V3_PROVIDER_ACCESS_PREFLIGHT_PROMPT = (
    "Confirm provider access to this chat-completions endpoint. Return JSON now."
)
V3_PROVIDER_ACCESS_PREFLIGHT_SCHEMA_VERSION = "v3_provider_access_preflight_v1"
_PREFLIGHT_METADATA_FIELDS = (
    "status",
    "provider",
    "model",
    "response_model",
    "response_id",
    "response_object",
    "response_created",
    "response_choice_count",
    "finish_reason",
    "latency_ms",
    "timeout_seconds",
    "request_timeout_mode",
    "temperature",
    "max_tokens",
    "thinking",
    "reasoning_effort",
    "prompt_chars",
    "prompt_sha256",
    "system_prompt_sha256",
    "response_chars",
    "response_sha256",
    "provider_attempt_count",
    "provider_retry_count",
    "provider_retry_reasons",
    "provider_retry_delays_seconds",
    "provider_terminal_error_reason",
    "http_status",
    "error_type",
    "error_reason",
    "provider_payload_bytes",
    "provider_payload_sha256",
)


def create_v3_repair_client(
    protocol: dict[str, Any],
    *,
    root: str | Path,
    prompt_id: str,
    api_key: str | None = None,
    sleeper=time.sleep,
) -> RetryingLLMClient:
    model = _dict(protocol.get("model"))
    return _create_v3_protocol_client(
        protocol,
        root=root,
        prompt_id=prompt_id,
        api_key=api_key,
        sleeper=sleeper,
        max_output_tokens=_int(model.get("max_output_tokens"), 32768),
        thinking=str(model.get("thinking") or "") or None,
        reasoning_effort=str(model.get("reasoning_effort") or "") or None,
    )


def create_v3_provider_access_client(
    protocol: dict[str, Any],
    *,
    root: str | Path,
    api_key: str | None = None,
    sleeper=time.sleep,
) -> RetryingLLMClient:
    model = _dict(protocol.get("model"))
    preflight = _dict(model.get("access_preflight"))
    if preflight.get("enabled") is not True:
        raise ValueError("V3 provider access preflight is not enabled in protocol.")
    return _create_v3_protocol_client(
        protocol,
        root=root,
        prompt_id=str(preflight.get("prompt_id") or ""),
        api_key=api_key,
        sleeper=sleeper,
        max_output_tokens=_int(preflight.get("max_output_tokens"), 16),
        thinking=str(preflight.get("thinking") or "") or None,
        reasoning_effort=(
            str(preflight.get("reasoning_effort") or "") or None
        ),
    )


def _create_v3_protocol_client(
    protocol: dict[str, Any],
    *,
    root: str | Path,
    prompt_id: str,
    api_key: str | None,
    sleeper,
    max_output_tokens: int,
    thinking: str | None,
    reasoning_effort: str | None,
) -> RetryingLLMClient:
    root_path = Path(root).resolve()
    prompt = _prompt_spec(protocol, prompt_id)
    prompt_path = _safe_protocol_path(root_path, str(prompt.get("path") or ""))
    if prompt_path is None or not prompt_path.is_file():
        raise ValueError(f"V3 prompt file is missing or unsafe: {prompt_id}")
    expected_sha = str(prompt.get("sha256") or "")
    if sha256_file(prompt_path) != expected_sha:
        raise ValueError(f"V3 prompt hash mismatch: {prompt_id}")
    model = _dict(protocol.get("model"))
    retry = _dict(model.get("provider_retry"))
    api_key_env_names = [
        str(value)
        for value in _list(model.get("api_key_env_names"))
        if str(value)
    ]
    resolved_api_key = api_key or next(
        (
            str(os.environ[name])
            for name in api_key_env_names
            if os.environ.get(name)
        ),
        None,
    )
    client = OpenAICompatibleLLMClient(
        provider=str(model.get("provider") or ""),
        api_key=resolved_api_key,
        model=str(model.get("model_id") or ""),
        base_url=str(model.get("api_base") or ""),
        timeout=_int(model.get("request_timeout_seconds"), 300),
        system_prompt=prompt_path.read_text(encoding="utf-8"),
        api_key_env=(api_key_env_names[0] if api_key_env_names else "CIA_LLM_API_KEY"),
        temperature=_float(model.get("temperature"), 0.0),
        max_tokens=max(1, max_output_tokens),
        response_format={"type": "json_object"},
        thinking=thinking,
        reasoning_effort=reasoning_effort,
        isolate_request_timeout=True,
    )
    return RetryingLLMClient(
        client,
        max_retries=_int(retry.get("maximum_retries"), 0),
        backoff_seconds=tuple(
            _float(value, 0.0) for value in _list(retry.get("backoff_seconds"))
        ),
        sleeper=sleeper,
    )


def run_v3_provider_access_preflight(
    protocol: dict[str, Any],
    *,
    root: str | Path,
    api_key: str | None = None,
    llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    started_at = utc_now()
    started = time.perf_counter()
    model = _dict(protocol.get("model"))
    preflight = _dict(model.get("access_preflight"))
    prompt_id = str(preflight.get("prompt_id") or "")
    prompt_spec = _prompt_spec(protocol, prompt_id)
    expected_model = str(model.get("model_id") or "")
    request_prompt_sha256 = hashlib.sha256(
        V3_PROVIDER_ACCESS_PREFLIGHT_PROMPT.encode("utf-8")
    ).hexdigest()
    base = {
        "schema_version": V3_PROVIDER_ACCESS_PREFLIGHT_SCHEMA_VERSION,
        "performed": True,
        "counted_as_repair_trial": False,
        "cost_attribution": "provider_preflight_overhead",
        "provider": str(model.get("provider") or ""),
        "protocol_model_id": expected_model,
        "prompt_id": prompt_id,
        "prompt_template_sha256": str(prompt_spec.get("sha256") or ""),
        "request_prompt_sha256": request_prompt_sha256,
        "response_content_retained": False,
        "response_content_used_for_repair": False,
        "started_at": started_at,
    }
    try:
        if request_prompt_sha256 != str(
            preflight.get("request_prompt_sha256") or ""
        ):
            raise ValueError(
                "V3 provider access preflight request Prompt does not match the "
                "frozen protocol hash."
            )
        client = llm_client or create_v3_provider_access_client(
            protocol,
            root=root,
            api_key=api_key,
        )
        response = client.complete(V3_PROVIDER_ACCESS_PREFLIGHT_PROMPT)
    except LLMRequestError as exc:
        metadata = _safe_preflight_metadata(exc.metadata)
        usage = _run_record_usage(exc.metadata)
        return {
            **base,
            "status": "blocked",
            "reason": "provider_access_request_failed",
            "observed_model_id": str(metadata.get("response_model") or ""),
            "latency_ms": _int(
                metadata.get("latency_ms"),
                _elapsed_ms(started),
            ),
            "usage": usage,
            "actual_cost_usd": compute_run_record_cost(
                {"usage": usage},
                protocol,
            ),
            "request_metadata": metadata,
            "failure": {
                "layer": "provider",
                "category": _provider_failure_category(exc),
                "reason": str(exc),
            },
            "completed_at": utc_now(),
        }
    except ValueError as exc:
        message = str(exc)
        normalized_message = message.lower()
        category = (
            "authentication"
            if (
                "api key" in normalized_message
                or "api_key" in normalized_message
                or "required for llm requests" in normalized_message
            )
            else "invalid_provider_response"
        )
        return {
            **base,
            "status": "blocked",
            "reason": "provider_access_configuration_failed",
            "observed_model_id": "",
            "latency_ms": _elapsed_ms(started),
            "usage": _zero_usage(),
            "actual_cost_usd": 0.0,
            "request_metadata": {},
            "failure": {
                "layer": "provider",
                "category": category,
                "reason": message,
            },
            "completed_at": utc_now(),
        }

    metadata = _safe_preflight_metadata(response.metadata)
    usage = _run_record_usage(response.metadata)
    observed_model = str(metadata.get("response_model") or "")
    if not observed_model:
        status = "blocked"
        reason = "provider_response_model_missing"
        category = "invalid_provider_response"
    elif observed_model != expected_model:
        status = "blocked"
        reason = "provider_response_model_mismatch"
        category = "model_unavailable"
    else:
        status = "pass"
        reason = "provider_access_verified"
        category = "none"
    return {
        **base,
        "status": status,
        "reason": reason,
        "observed_model_id": observed_model,
        "latency_ms": _int(metadata.get("latency_ms"), _elapsed_ms(started)),
        "usage": usage,
        "actual_cost_usd": compute_run_record_cost(
            {"usage": usage},
            protocol,
        ),
        "request_metadata": metadata,
        "failure": {
            "layer": "none" if status == "pass" else "provider",
            "category": category,
            "reason": "" if status == "pass" else reason,
        },
        "completed_at": utc_now(),
    }


def copy_v3_trial_workspace(
    seed_repository: str | Path,
    destination: str | Path,
) -> dict[str, Any]:
    source = Path(seed_repository).resolve()
    target = Path(destination).resolve()
    if not source.is_dir() or source.is_symlink():
        return {
            "status": "fail",
            "reason": "seed_repository_missing_or_unsafe",
            "workspace": str(target),
        }
    if target == source or source in target.parents:
        return {
            "status": "fail",
            "reason": "trial_workspace_must_not_be_inside_seed_repository",
            "workspace": str(target),
        }
    first_symlink = next((path for path in source.rglob("*") if path.is_symlink()), None)
    if first_symlink is not None:
        return {
            "status": "fail",
            "reason": "seed_repository_contains_symlink",
            "symlink": first_symlink.relative_to(source).as_posix(),
            "workspace": str(target),
        }
    if target.exists():
        return {
            "status": "fail",
            "reason": "trial_workspace_already_exists",
            "workspace": str(target),
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(
            source,
            target,
            symlinks=False,
            ignore=shutil.ignore_patterns(
                ".git",
                ".cia-test-home",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
                "__pycache__",
                "*.pyc",
                "*.pyo",
            ),
        )
    except (OSError, shutil.Error) as exc:
        return {
            "status": "fail",
            "reason": "trial_workspace_copy_failed",
            "error_type": type(exc).__name__,
            "workspace": str(target),
        }
    return {
        "status": "pass",
        "reason": "independent_trial_workspace_created",
        "workspace": str(target),
        "seed_repository": str(source),
        "excluded_runtime_artifacts": True,
    }


def execute_v3_patch_candidate(
    candidate: dict[str, Any],
    *,
    editable_regions: list[EditableRegion],
    seed_repository: str | Path,
    trial_workspace: str | Path,
    case: dict[str, Any],
    python_executable: str | Path,
    targeted_timeout: int = 120,
    regression_timeout: int = 900,
    failed_diff_fingerprints: set[str] | None = None,
    failed_source_fingerprints: set[str] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    safety = validate_v3_patch_candidate(
        candidate,
        editable_regions=editable_regions,
        repository_root=seed_repository,
        failed_diff_fingerprints=failed_diff_fingerprints,
        failed_source_fingerprints=failed_source_fingerprints,
    )
    if safety["status"] != "pass":
        ast_valid = bool(safety.get("ast_valid", False))
        return _candidate_execution_result(
            validation={
                "ast_valid": ast_valid,
                "safety_gate": "fail",
                "targeted_tests": "not_run",
                "full_regression": "not_run",
                "semantic_validation": "not_run",
                "semantic_justification": "Candidate was rejected before execution.",
            },
            outcome_status="safety_rejected",
            failure_layer="syntax" if not ast_valid else "safety",
            failure_category=(
                "invalid_python_ast"
                if not ast_valid
                else str(_list(safety.get("reasons"))[0] or "safety_gate_rejected")
            ),
            failure_reason=";".join(str(item) for item in _list(safety.get("reasons"))),
            safety=safety,
            workspace={"status": "not_run", "reason": "safety_rejected"},
            application={"status": "not_run", "reason": "safety_rejected"},
            targeted={"status": "not_run", "reason": "safety_rejected"},
            regression={"status": "not_run", "reason": "safety_rejected"},
            semantic={"status": "not_run", "reason": "safety_rejected"},
            validation_latency_ms=_elapsed_ms(started),
        )

    workspace = copy_v3_trial_workspace(seed_repository, trial_workspace)
    if workspace["status"] != "pass":
        return _candidate_execution_result(
            validation={
                "ast_valid": True,
                "safety_gate": "pass",
                "targeted_tests": "blocker",
                "full_regression": "not_run",
                "semantic_validation": "blocker",
                "semantic_justification": "Independent trial workspace could not be created.",
            },
            outcome_status="environment_blocker",
            failure_layer="environment",
            failure_category="test_process",
            failure_reason=str(workspace.get("reason") or "workspace_copy_failed"),
            safety=safety,
            workspace=workspace,
            application={"status": "not_run", "reason": "workspace_blocker"},
            targeted={"status": "not_run", "reason": "workspace_blocker"},
            regression={"status": "not_run", "reason": "workspace_blocker"},
            semantic={"status": "not_run", "reason": "workspace_blocker"},
            validation_latency_ms=_elapsed_ms(started),
        )

    workspace_root = Path(str(workspace["workspace"])).resolve()
    workspace_regions = _workspace_regions(
        editable_regions,
        seed_repository=Path(seed_repository).resolve(),
        workspace=workspace_root,
    )
    application = apply_v3_patch_candidate(
        candidate,
        editable_regions=workspace_regions,
        repository_root=workspace_root,
    )
    if application["status"] != "pass":
        return _candidate_execution_result(
            validation={
                "ast_valid": False,
                "safety_gate": "pass",
                "targeted_tests": "not_run",
                "full_regression": "not_run",
                "semantic_validation": "not_run",
                "semantic_justification": "Patch application failed full-file AST validation.",
            },
            outcome_status="failed",
            failure_layer="syntax",
            failure_category="candidate_application_failed",
            failure_reason=";".join(
                str(item) for item in _list(application.get("errors"))
            ),
            safety=safety,
            workspace=workspace,
            application=application,
            targeted={"status": "not_run", "reason": "application_failed"},
            regression={"status": "not_run", "reason": "application_failed"},
            semantic={"status": "not_run", "reason": "application_failed"},
            validation_latency_ms=_elapsed_ms(started),
        )

    targeted_commands = [
        [str(part) for part in _list(command)]
        for command in _list(case.get("targeted_test_commands"))
    ]
    targeted = execute_test_commands(
        targeted_commands,
        repository_root=workspace_root,
        python_executable=python_executable,
        timeout=targeted_timeout,
        test_environment=_dict(case.get("test_environment")),
    )
    if targeted.get("status") != "pass":
        environment_blocker = bool(targeted.get("environment_blocker", False))
        failure_category = _test_failure_category(targeted)
        return _candidate_execution_result(
            validation={
                "ast_valid": True,
                "safety_gate": "pass",
                "targeted_tests": "blocker" if environment_blocker else "fail",
                "full_regression": "not_run",
                "semantic_validation": "not_run",
                "semantic_justification": "Targeted tests did not pass.",
            },
            outcome_status=("environment_blocker" if environment_blocker else "failed"),
            failure_layer=("environment" if environment_blocker else "targeted_test"),
            failure_category=(
                _environment_category(failure_category)
                if environment_blocker
                else failure_category
            ),
            failure_reason=str(targeted.get("reason") or failure_category),
            safety=safety,
            workspace=workspace,
            application=application,
            targeted=targeted,
            regression={"status": "not_run", "reason": "targeted_tests_failed"},
            semantic={"status": "not_run", "reason": "targeted_tests_failed"},
            validation_latency_ms=_elapsed_ms(started),
        )

    regression_command = [str(part) for part in _list(case.get("regression_command"))]
    regression = execute_test_commands(
        [regression_command],
        repository_root=workspace_root,
        python_executable=python_executable,
        timeout=regression_timeout,
        test_environment=_dict(case.get("test_environment")),
    )
    if regression.get("status") != "pass":
        environment_blocker = bool(regression.get("environment_blocker", False))
        failure_category = _test_failure_category(regression)
        return _candidate_execution_result(
            validation={
                "ast_valid": True,
                "safety_gate": "pass",
                "targeted_tests": "pass",
                "full_regression": "blocker" if environment_blocker else "fail",
                "semantic_validation": "not_run",
                "semantic_justification": "Full regression did not pass.",
            },
            outcome_status=("environment_blocker" if environment_blocker else "failed"),
            failure_layer=("environment" if environment_blocker else "full_regression"),
            failure_category=(
                _environment_category(failure_category)
                if environment_blocker
                else failure_category
            ),
            failure_reason=str(regression.get("reason") or failure_category),
            safety=safety,
            workspace=workspace,
            application=application,
            targeted=targeted,
            regression=regression,
            semantic={"status": "not_run", "reason": "full_regression_failed"},
            validation_latency_ms=_elapsed_ms(started),
        )

    semantic = validate_v3_semantic_candidate(
        candidate,
        editable_regions=workspace_regions,
        seed_repository=seed_repository,
        patched_repository=workspace_root,
        case=case,
        python_executable=python_executable,
        targeted_timeout=targeted_timeout,
        regression_timeout=regression_timeout,
        patched_target_execution=targeted,
    )
    semantic_status = str(semantic.get("status") or "blocker")
    semantic_reason = str(
        semantic.get("reason") or "semantic_validation_result_missing"
    )
    if semantic_status == "pass":
        outcome_status = "verified_repair"
        failure_layer = "none"
        failure_category = "none"
        failure_reason = ""
    elif semantic_status == "fail":
        outcome_status = "failed"
        failure_layer = "semantic_validation"
        failure_category = semantic_reason
        failure_reason = semantic_reason
    else:
        outcome_status = "unverified_suggestion"
        failure_layer = "semantic_validation"
        failure_category = semantic_reason
        failure_reason = semantic_reason
    return _candidate_execution_result(
        validation={
            "ast_valid": True,
            "safety_gate": "pass",
            "targeted_tests": "pass",
            "full_regression": "pass",
            "semantic_validation": semantic_status,
            "semantic_justification": semantic_reason,
            "semantic_validation_details": semantic,
        },
        outcome_status=outcome_status,
        failure_layer=failure_layer,
        failure_category=failure_category,
        failure_reason=failure_reason,
        safety=safety,
        workspace=workspace,
        application=application,
        targeted=targeted,
        regression=regression,
        semantic=semantic,
        validation_latency_ms=_elapsed_ms(started),
    )


def build_v3_run_record(
    protocol: dict[str, Any],
    *,
    case: dict[str, Any],
    strategy_mode: str,
    trial_index: int,
    trial_id: str,
    candidate_index: int,
    candidate_id: str,
    generator_family: str,
    generator_id: str,
    reflection_round: int,
    parent_candidate_id: str,
    prompt_id: str,
    llm_metadata: dict[str, Any] | None,
    execution: dict[str, Any],
    model_context_artifact: str,
    artifacts: dict[str, Any],
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    is_llm = generator_family == "llm"
    metadata = _dict(llm_metadata)
    usage = _run_record_usage(metadata) if is_llm else _zero_usage()
    model_config = _dict(protocol.get("model"))
    pricing = _dict(protocol.get("pricing"))
    prompt = _prompt_spec(protocol, prompt_id) if is_llm else {}
    validation = _dict(execution.get("validation"))
    outcome_status = str(execution.get("outcome_status") or "failed")
    verified = outcome_status == "verified_repair"
    record = {
        "schema_version": "3.0",
        "run_id": str(uuid.uuid4()),
        "case": {
            "case_id": str(case.get("case_id") or ""),
            "repository": str(_dict(case.get("repository")).get("owner_repo") or ""),
            "bug_commit_sha": str(case.get("bug_commit_sha") or ""),
            "benchmark_split": str(case.get("benchmark_split") or ""),
        },
        "strategy": {
            "mode": strategy_mode,
            "trial_index": trial_index,
            "trial_id": trial_id,
            "independent_trial": True,
        },
        "candidate": {
            "candidate_index": candidate_index,
            "candidate_id": candidate_id,
            "generator_family": generator_family,
            "generator_id": generator_id,
            "parent_candidate_id": parent_candidate_id,
            "reflection_round": reflection_round,
        },
        "model": {
            "provider": str(model_config.get("provider") or "") if is_llm else "",
            "model_id": str(model_config.get("model_id") or "") if is_llm else "",
            "prompt_id": str(prompt.get("id") or "") if is_llm else "",
            "prompt_sha256": str(prompt.get("sha256") or "") if is_llm else "",
            "request_prompt_sha256": (
                str(metadata.get("prompt_sha256") or "") if is_llm else ""
            ),
            "system_prompt_sha256": (
                str(metadata.get("system_prompt_sha256") or "") if is_llm else ""
            ),
            "temperature": model_config.get("temperature") if is_llm else None,
            "seed": _dict(protocol.get("randomness")).get("seed") if is_llm else None,
            "thinking": str(model_config.get("thinking") or "") if is_llm else "",
            "reasoning_effort": (
                str(model_config.get("reasoning_effort") or "") if is_llm else ""
            ),
            "response_id": str(metadata.get("response_id") or "") if is_llm else "",
            "provider_response_model": (
                str(metadata.get("response_model") or "") if is_llm else ""
            ),
            "finish_reason": (
                str(metadata.get("finish_reason") or "") if is_llm else ""
            ),
            "response_sha256": (
                str(metadata.get("response_sha256") or "") if is_llm else ""
            ),
        },
        "usage": usage,
        "cost": {
            "currency": str(pricing.get("currency") or "USD"),
            "pricing_snapshot_id": (
                str(pricing.get("snapshot_id") or "") if is_llm else ""
            ),
            "actual_cost_usd": 0.0,
        },
        "timing": {
            "latency_ms": _float(metadata.get("latency_ms"), 0.0)
            + _float(execution.get("validation_latency_ms"), 0.0),
            "model_latency_ms": _float(metadata.get("latency_ms"), 0.0),
            "validation_latency_ms": _float(
                execution.get("validation_latency_ms"), 0.0
            ),
            "provider_retry_count": (
                _int(metadata.get("provider_retry_count"), 0) if is_llm else 0
            ),
            "provider_retry_reasons": (
                [str(item) for item in _list(metadata.get("provider_retry_reasons"))]
                if is_llm
                else []
            ),
        },
        "validation": validation,
        "outcome": {
            "status": outcome_status,
            "direct_success": verified and reflection_round == 0,
            "reflection_recovered": verified and reflection_round > 0,
        },
        "failure": {
            "layer": str(execution.get("failure_layer") or "controller"),
            "category": str(execution.get("failure_category") or "unknown"),
            "reason": str(execution.get("failure_reason") or ""),
        },
        "model_context": {
            "artifact_ref": model_context_artifact if is_llm else "",
            "contains_gold_patch": False,
            "contains_fix_commit": False,
            "contains_test_answer": False,
        },
        "artifacts": dict(artifacts),
        "timestamps": {
            "started_at": started_at,
            "completed_at": completed_at,
        },
    }
    if is_llm:
        record["cost"]["actual_cost_usd"] = compute_run_record_cost(record, protocol)
    return record


def provider_blocker_execution(error: LLMRequestError) -> dict[str, Any]:
    category = _provider_failure_category(error)
    return {
        "validation": {
            "ast_valid": None,
            "safety_gate": "not_run",
            "targeted_tests": "not_run",
            "full_regression": "not_run",
            "semantic_validation": "not_run",
            "semantic_justification": "Model provider request did not produce a candidate.",
        },
        "outcome_status": "provider_blocker",
        "failure_layer": "provider",
        "failure_category": category,
        "failure_reason": str(error),
        "validation_latency_ms": 0.0,
    }


def write_v3_candidate_artifacts(
    output_dir: str | Path,
    *,
    candidate_id: str,
    safety: dict[str, Any],
    execution: dict[str, Any],
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in candidate_id
    )
    patch_path = root / f"{safe_id}.diff"
    validation_path = root / f"{safe_id}.validation.json"
    targeted_path = root / f"{safe_id}.targeted.json"
    regression_path = root / f"{safe_id}.regression.json"
    semantic_path = root / f"{safe_id}.semantic.json"
    patch_path.write_text(str(safety.get("combined_diff") or ""), encoding="utf-8")
    validation_path.write_text(
        json.dumps(
            {
                "validation": _dict(execution.get("validation")),
                "outcome_status": str(execution.get("outcome_status") or ""),
                "failure_layer": str(execution.get("failure_layer") or ""),
                "failure_category": str(execution.get("failure_category") or ""),
                "safety": safety,
                "application": _dict(execution.get("application")),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    targeted_path.write_text(
        json.dumps(_dict(execution.get("targeted")), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    regression_path.write_text(
        json.dumps(_dict(execution.get("regression")), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    semantic_path.write_text(
        json.dumps(_dict(execution.get("semantic")), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "patch": str(patch_path),
        "validation": str(validation_path),
        "targeted_test": str(targeted_path),
        "full_regression": str(regression_path),
        "semantic_validation": str(semantic_path),
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _candidate_execution_result(
    *,
    validation: dict[str, Any],
    outcome_status: str,
    failure_layer: str,
    failure_category: str,
    failure_reason: str,
    safety: dict[str, Any],
    workspace: dict[str, Any],
    application: dict[str, Any],
    targeted: dict[str, Any],
    regression: dict[str, Any],
    semantic: dict[str, Any],
    validation_latency_ms: int,
) -> dict[str, Any]:
    return {
        "validation": validation,
        "outcome_status": outcome_status,
        "failure_layer": failure_layer,
        "failure_category": failure_category,
        "failure_reason": failure_reason,
        "safety": safety,
        "workspace": workspace,
        "application": application,
        "targeted": targeted,
        "regression": regression,
        "semantic": semantic,
        "validation_latency_ms": validation_latency_ms,
    }


def _workspace_regions(
    regions: list[EditableRegion],
    *,
    seed_repository: Path,
    workspace: Path,
) -> list[EditableRegion]:
    del seed_repository, workspace
    return list(regions)


def _run_record_usage(metadata: dict[str, Any]) -> dict[str, Any]:
    provider_usage = _dict(metadata.get("usage"))
    input_tokens = _int(provider_usage.get("prompt_tokens"), 0)
    cache_hit = _int(provider_usage.get("prompt_cache_hit_tokens"), 0)
    cache_miss_value = provider_usage.get("prompt_cache_miss_tokens")
    cache_miss = (
        _int(cache_miss_value, 0)
        if cache_miss_value is not None
        else max(0, input_tokens - cache_hit)
    )
    if cache_hit + cache_miss != input_tokens:
        cache_miss = max(0, input_tokens - cache_hit)
    output_tokens = _int(provider_usage.get("completion_tokens"), 0)
    return {
        "source": str(provider_usage.get("source") or "unavailable"),
        "input_tokens": input_tokens,
        "cache_hit_input_tokens": cache_hit,
        "cache_miss_input_tokens": cache_miss,
        "output_tokens": output_tokens,
        "reasoning_tokens": _int(provider_usage.get("reasoning_tokens"), 0),
        "total_tokens": input_tokens + output_tokens,
    }


def _zero_usage() -> dict[str, Any]:
    return {
        "source": "not_applicable",
        "input_tokens": 0,
        "cache_hit_input_tokens": 0,
        "cache_miss_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }


def _test_failure_category(group: dict[str, Any]) -> str:
    for result_value in _list(group.get("results")):
        category = str(_dict(result_value).get("failure_category") or "")
        if category:
            return category
    return str(group.get("reason") or "test_process")


def _environment_category(category: str) -> str:
    if category in {"timeout", "resource_limit"}:
        return "resource_limit"
    if category in {"no_tests_collected", "pytest_collection_error"}:
        return "test_discovery"
    return "test_process"


def _provider_failure_category(error: LLMRequestError) -> str:
    status = _int(error.metadata.get("http_status"), 0)
    if status == 401:
        return "authentication"
    if status == 402:
        return "billing_or_quota"
    if status == 403:
        return "authorization"
    if status == 429:
        return "rate_limit"
    if status in {404, 410}:
        return "model_unavailable"
    if error.reason == "timeout" or status == 408:
        return "timeout"
    if error.reason == "url_error" or status >= 500:
        return "network"
    return "invalid_provider_response"


def _safe_preflight_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        field: metadata[field]
        for field in _PREFLIGHT_METADATA_FIELDS
        if field in metadata
    }


def _prompt_spec(protocol: dict[str, Any], prompt_id: str) -> dict[str, Any]:
    for value in _list(protocol.get("prompts")):
        prompt = _dict(value)
        if str(prompt.get("id") or "") == prompt_id:
            return prompt
    raise ValueError(f"V3 prompt is not frozen in protocol: {prompt_id}")


def _safe_protocol_path(root: Path, relative_path: str) -> Path | None:
    path = Path(relative_path.replace("\\", "/"))
    if not relative_path or path.is_absolute() or ".." in path.parts:
        return None
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _elapsed_ms(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000)))


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
