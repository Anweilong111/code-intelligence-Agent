from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.v3_experiment_protocol import (
    load_experiment_protocol,
    sha256_file,
    validate_run_records,
)
from code_intelligence_agent.evaluation.v3_repair_orchestrator import (
    PreparedV3RepairCase,
    prepare_v3_repair_case,
    run_v3_repair_trial,
)


ALL_STRATEGIES = ("rule", "llm", "hybrid")
TRIAL_IMPLEMENTATION_FILES = (
    "agents/bug_detector.py",
    "agents/llm_client.py",
    "agents/patch_generator.py",
    "core/repo_parser.py",
    "evaluation/repository_test_dynamic_evidence.py",
    "evaluation/repository_test_fault_localization.py",
    "evaluation/repository_test_patch_candidates.py",
    "evaluation/v3_real_bug_reproduction.py",
    "evaluation/v3_experiment_protocol.py",
    "evaluation/v3_repair_evaluation.py",
    "evaluation/v3_repair_execution.py",
    "evaluation/v3_repair_orchestrator.py",
    "evaluation/v3_repair_scope.py",
    "evaluation/v3_repair_trial.py",
    "evaluation/v3_semantic_validation.py",
    "tools/boundary_probe.py",
    "tools/boundary_probe_bootstrap.py",
    "tools/patch_safety.py",
    "tools/patch_validation.py",
    "tools/semantic_patch_validation.py",
)


def run_v3_repair_evaluation(
    *,
    project_root: str | Path,
    protocol_path: str | Path,
    catalog_path: str | Path,
    environment_profiles_path: str | Path,
    reproduction_root: str | Path,
    output_dir: str | Path,
    strategies: list[str],
    case_ids: list[str] | None = None,
    max_cases: int = 0,
    prepare_only: bool = False,
    live_model: bool = False,
    resume: bool = True,
    retry_blockers: bool = False,
    max_workers: int = 1,
    rule_candidate_limit: int = 5,
    targeted_timeout: int = 120,
    regression_timeout: int = 900,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    protocol = load_experiment_protocol(protocol_path)
    catalog = _read_json(catalog_path)
    profiles = _read_json(environment_profiles_path)
    selected_strategies = _normalize_strategies(strategies)
    if not prepare_only and any(
        strategy in {"llm", "hybrid"} for strategy in selected_strategies
    ):
        if not live_model:
            raise ValueError(
                "LLM/Hybrid evaluation requires explicit live_model=True or --live-model."
            )
        if not _model_key_present(protocol):
            raise ValueError(
                "No V3 model API key is present in the current process environment."
            )
    accepted = [
        _dict(case)
        for case in _list(catalog.get("cases"))
        if str(_dict(case).get("status") or "") == "accepted"
    ]
    requested_case_ids = {str(item) for item in (case_ids or []) if str(item)}
    if requested_case_ids:
        accepted = [
            case
            for case in accepted
            if str(case.get("case_id") or "") in requested_case_ids
        ]
    accepted.sort(key=lambda case: str(case.get("case_id") or ""))
    if max_cases > 0:
        accepted = accepted[:max_cases]
    profile_by_id = {
        str(_dict(profile).get("profile_id") or ""): _dict(profile)
        for profile in _list(profiles.get("profiles"))
    }
    records: list[dict[str, Any]] = []
    case_results: list[dict[str, Any]] = []
    started_at = _utc_now()
    for case in accepted:
        case_id = str(case.get("case_id") or "")
        case_output = output / "cases" / case_id
        case_output.mkdir(parents=True, exist_ok=True)
        seed_audit = audit_v3_reproduction_seed(
            case,
            reproduction_dir=Path(reproduction_root) / case_id,
        )
        runtime = resolve_v3_case_runtime(
            root,
            case,
            profile_by_id=profile_by_id,
        )
        if seed_audit["status"] != "pass" or runtime["status"] != "pass":
            case_results.append(
                {
                    "case_id": case_id,
                    "status": "blocker",
                    "seed_audit": seed_audit,
                    "runtime": runtime,
                    "trials": [],
                }
            )
            continue
        reproduction = _read_json(seed_audit["reproduction_artifact"])
        baseline_execution = _first_baseline_execution(reproduction)
        if not baseline_execution:
            case_results.append(
                {
                    "case_id": case_id,
                    "status": "blocker",
                    "seed_audit": seed_audit,
                    "runtime": runtime,
                    "reason": "validated_bug_targeted_execution_missing",
                    "trials": [],
                }
            )
            continue
        prepared = prepare_v3_repair_case(
            case,
            seed_repository=seed_audit["seed_repository"],
            baseline_execution=baseline_execution,
            output_dir=case_output / "preparation",
        )
        if (
            prepared.model_context_audit.get("status") != "pass"
            or prepared.analysis_scope_ground_truth_audit.get("status") != "pass"
        ):
            case_results.append(
                {
                    "case_id": case_id,
                    "status": "blocker",
                    "seed_audit": seed_audit,
                    "runtime": runtime,
                    "reason": "preparation_audit_failed",
                    "preparation": {
                        "model_context_audit": prepared.model_context_audit,
                        "analysis_scope_ground_truth_audit": (
                            prepared.analysis_scope_ground_truth_audit
                        ),
                        "artifacts": prepared.preparation_artifacts,
                    },
                    "trials": [],
                }
            )
            continue
        trial_results = []
        trial_input_fingerprint = build_v3_trial_input_fingerprint(
            protocol,
            prepared,
        )
        if not prepare_only:
            ordered_trial_results: list[tuple[int, dict[str, Any]]] = []
            pending_jobs: list[dict[str, Any]] = []
            trial_order = 0
            for strategy in selected_strategies:
                for trial_index in _trial_indices(protocol, strategy):
                    trial_order += 1
                    trial_root = case_output / "trials" / strategy / f"trial-{trial_index}"
                    latest_path = trial_root / "latest.json"
                    resumed = _load_resumable_trial(
                        latest_path,
                        protocol=protocol,
                        expected_input_fingerprint=trial_input_fingerprint,
                        retry_blockers=retry_blockers,
                    ) if resume else None
                    if resumed is not None:
                        trial_result = dict(resumed)
                        trial_result["resumed"] = True
                        ordered_trial_results.append((trial_order, trial_result))
                    else:
                        attempt_dir = trial_root / f"attempt-{uuid.uuid4()}"
                        pending_jobs.append(
                            {
                                "order": trial_order,
                                "strategy": strategy,
                                "trial_index": trial_index,
                                "trial_root": trial_root,
                                "latest_path": latest_path,
                                "attempt_dir": attempt_dir,
                            }
                        )

            def execute_job(job: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                attempt_dir = Path(job["attempt_dir"])
                trial_result = run_v3_repair_trial(
                    protocol,
                    prepared,
                    project_root=root,
                    output_dir=attempt_dir,
                    strategy_mode=str(job["strategy"]),
                    trial_index=_int(job["trial_index"], 0),
                    python_executable=runtime["python_executable"],
                    rule_candidate_limit=rule_candidate_limit,
                    targeted_timeout=targeted_timeout,
                    regression_timeout=regression_timeout,
                )
                trial_result["input_fingerprint"] = trial_input_fingerprint
                trial_result["resumed"] = False
                trial_result["attempt_dir"] = attempt_dir.as_posix()
                attempt_dir.mkdir(parents=True, exist_ok=True)
                serialized = json.dumps(
                    trial_result,
                    indent=2,
                    ensure_ascii=False,
                )
                (attempt_dir / "trial_result.json").write_text(
                    serialized,
                    encoding="utf-8",
                )
                trial_root = Path(job["trial_root"])
                trial_root.mkdir(parents=True, exist_ok=True)
                Path(job["latest_path"]).write_text(
                    serialized,
                    encoding="utf-8",
                )
                return _int(job["order"], 0), trial_result

            worker_count = min(max(1, max_workers), max(1, len(pending_jobs)))
            if pending_jobs and worker_count > 1:
                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    ordered_trial_results.extend(executor.map(execute_job, pending_jobs))
            else:
                ordered_trial_results.extend(execute_job(job) for job in pending_jobs)
            for _, trial_result in sorted(ordered_trial_results, key=lambda item: item[0]):
                trial_results.append(_trial_summary(trial_result))
                records.extend(
                    _dict(record) for record in _list(trial_result.get("records"))
                )
        case_results.append(
            {
                "case_id": case_id,
                "status": (
                    "prepared"
                    if prepare_only
                    else "pass"
                    if any(bool(item.get("verified_repair", False)) for item in trial_results)
                    else "fail"
                ),
                "seed_audit": seed_audit,
                "runtime": runtime,
                "preparation": {
                    "dynamic_status": str(prepared.dynamic_evidence.get("status") or ""),
                    "analysis_scope_mode": str(
                        prepared.analysis_scope.get("mode") or ""
                    ),
                    "analysis_scope_file_count": _int(
                        prepared.analysis_scope.get("selected_file_count"), 0
                    ),
                    "analysis_scope_ground_truth_audit_status": str(
                        prepared.analysis_scope_ground_truth_audit.get("status") or ""
                    ),
                    "analysis_scope_ground_truth_file_recall": float(
                        prepared.analysis_scope_ground_truth_audit.get(
                            "analysis_scope_file_recall", 0.0
                        )
                    ),
                    "editable_ground_truth_file_recall": float(
                        prepared.analysis_scope_ground_truth_audit.get(
                            "editable_file_recall", 0.0
                        )
                    ),
                    "model_context_audit_status": str(
                        prepared.model_context_audit.get("status") or ""
                    ),
                    "localization_status": str(prepared.localization.get("status") or ""),
                    "localization_ranking_count": _int(
                        prepared.localization.get("ranking_count"), 0
                    ),
                    "editable_region_count": len(prepared.editable_regions),
                    "artifacts": prepared.preparation_artifacts,
                },
                "trials": trial_results,
            }
        )
    selected_case_ids = [str(case.get("case_id") or "") for case in accepted]
    record_audit = validate_run_records(
        records,
        protocol=protocol,
        require_complete=False,
    )
    if prepare_only:
        metrics = {}
        completeness = {
            "status": "not_applicable",
            "expected_trial_count": 0,
            "observed_trial_count": 0,
            "missing_trial_count": 0,
            "missing_trials": [],
            "reason": "preparation_only_mode",
        }
    else:
        metrics = build_v3_repair_metrics(
            records,
            case_ids=selected_case_ids,
            strategies=selected_strategies,
            protocol=protocol,
        )
        completeness = audit_v3_evaluation_completeness(
            records,
            case_ids=selected_case_ids,
            strategies=selected_strategies,
            protocol=protocol,
        )
    completed_at = _utc_now()
    if prepare_only:
        status = (
            "prepared"
            if case_results
            and all(str(item.get("status") or "") == "prepared" for item in case_results)
            else "fail"
        )
    else:
        status = (
            "pass"
            if record_audit["status"] == "pass"
            and completeness["status"] == "pass"
            else "fail"
        )
    result = {
        "schema_version": "3.0",
        "evaluation_id": str(uuid.uuid4()),
        "status": status,
        "prepare_only": prepare_only,
        "live_model": live_model,
        "max_workers": max(1, max_workers),
        "started_at": started_at,
        "completed_at": completed_at,
        "protocol_path": str(Path(protocol_path).resolve()),
        "protocol_sha256": str(protocol.get("protocol_sha256") or ""),
        "catalog_path": str(Path(catalog_path).resolve()),
        "catalog_sha256": str(catalog.get("catalog_sha256") or ""),
        "strategies": selected_strategies,
        "case_count": len(selected_case_ids),
        "record_count": len(records),
        "record_audit": record_audit,
        "provider_model_metadata": summarize_v3_model_metadata(records, protocol),
        "completeness": completeness,
        "metrics": metrics,
        "case_results": case_results,
        "boundary": (
            "Preparation-only mode does not call a model or claim a repair rate."
            if prepare_only
            else "Sandbox targeted and full regression tests determine verified repairs."
        ),
    }
    write_v3_repair_evaluation_artifacts(result, records, output)
    return result


def audit_v3_reproduction_seed(
    case: dict[str, Any],
    *,
    reproduction_dir: str | Path,
) -> dict[str, Any]:
    root = Path(reproduction_dir).resolve()
    artifact = root / "reproduction.json"
    seed = root / "bug" / "repository_checkout"
    errors: list[str] = []
    if not artifact.is_file():
        errors.append("reproduction_artifact_missing")
        reproduction = {}
    else:
        reproduction = _read_json(artifact)
    if not seed.is_dir() or seed.is_symlink():
        errors.append("bug_seed_repository_missing_or_unsafe")
    if str(reproduction.get("status") or "") != "pass":
        errors.append("reproduction_status_not_pass")
    acceptance = _dict(reproduction.get("acceptance"))
    if acceptance.get("reproducible") is not True:
        errors.append("reproduction_acceptance_not_reproducible")
    preparation = _dict(reproduction.get("preparation"))
    bug_checkout = _dict(preparation.get("bug_checkout"))
    if str(bug_checkout.get("ref") or "") != str(case.get("bug_commit_sha") or ""):
        errors.append("bug_seed_commit_mismatch")
    expected_artifact_sha = str(
        _dict(case.get("reproduction")).get("evidence_sha256") or ""
    )
    actual_artifact_sha = sha256_file(artifact) if artifact.is_file() else ""
    if expected_artifact_sha and actual_artifact_sha != expected_artifact_sha:
        errors.append("reproduction_artifact_sha256_mismatch")
    if seed.is_dir():
        for group_name in ("test_overlay", "bug_preparation_files"):
            group = _dict(preparation.get(group_name))
            for value in _list(group.get("files")):
                item = _dict(value)
                relative = str(item.get("path") or "")
                target = _safe_seed_file(seed, relative)
                if target is None or not target.is_file():
                    errors.append(f"seed_file_missing:{relative}")
                    continue
                if sha256_file(target) != str(item.get("sha256") or ""):
                    errors.append(f"seed_file_sha256_mismatch:{relative}")
    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "case_id": str(case.get("case_id") or ""),
        "seed_repository": seed.as_posix(),
        "reproduction_artifact": artifact.as_posix(),
        "reproduction_artifact_sha256": actual_artifact_sha,
        "fix_checkout_used_as_trial_seed": False,
    }


def resolve_v3_case_runtime(
    project_root: str | Path,
    case: dict[str, Any],
    *,
    profile_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    profile_id = str(case.get("environment_profile_id") or "")
    profile = _dict(profile_by_id.get(profile_id))
    relative_dir = str(profile.get("runtime_relative_dir") or "")
    runtime_dir = _safe_project_path(root, relative_dir)
    if runtime_dir is None:
        return {
            "status": "fail",
            "reason": "runtime_profile_path_unsafe_or_missing",
            "profile_id": profile_id,
            "python_executable": "",
        }
    candidates = [runtime_dir / "python.exe", runtime_dir / "bin" / "python"]
    executable = next((candidate for candidate in candidates if candidate.is_file()), None)
    return {
        "status": "pass" if executable else "fail",
        "reason": "pinned_runtime_resolved" if executable else "python_executable_missing",
        "profile_id": profile_id,
        "expected_python_version": str(profile.get("expected_python_version") or ""),
        "python_executable": executable.as_posix() if executable else "",
    }


def build_v3_repair_metrics(
    records: list[dict[str, Any]],
    *,
    case_ids: list[str],
    strategies: list[str],
    protocol: dict[str, Any],
) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    case_set = set(case_ids)
    for strategy in strategies:
        strategy_records = [
            record
            for record in records
            if str(_dict(record.get("strategy")).get("mode") or "") == strategy
        ]
        trial_groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
        for record in strategy_records:
            case_id = str(_dict(record.get("case")).get("case_id") or "")
            trial_index = _int(_dict(record.get("strategy")).get("trial_index"), 0)
            trial_groups[(case_id, trial_index)].append(record)
        expected_trials = len(_trial_indices(protocol, strategy))
        pass_at_1_cases = {
            case_id
            for case_id in case_set
            if _trial_verified(trial_groups.get((case_id, 1), []))
        }
        pass_at_3_cases = {
            case_id
            for case_id in case_set
            if any(
                _trial_verified(trial_groups.get((case_id, index), []))
                for index in range(1, min(3, expected_trials) + 1)
            )
        }
        verified_cases = {
            case_id
            for case_id in case_set
            if any(
                _trial_verified(trial_groups.get((case_id, index), []))
                for index in range(1, expected_trials + 1)
            )
        }
        reflection_cases = {
            str(_dict(record.get("case")).get("case_id") or "")
            for record in strategy_records
            if bool(_dict(record.get("outcome")).get("reflection_recovered", False))
        }
        executable_records = [
            record
            for record in strategy_records
            if _dict(record.get("validation")).get("ast_valid") in {True, False}
        ]
        safety_records = [
            record
            for record in strategy_records
            if str(_dict(record.get("validation")).get("safety_gate") or "")
            in {"pass", "fail"}
        ]
        targeted_records = [
            record
            for record in strategy_records
            if str(_dict(record.get("validation")).get("targeted_tests") or "")
            in {"pass", "fail"}
        ]
        regression_records = [
            record
            for record in strategy_records
            if str(_dict(record.get("validation")).get("full_regression") or "")
            in {"pass", "fail"}
        ]
        semantic_records = [
            record
            for record in strategy_records
            if str(_dict(record.get("validation")).get("semantic_validation") or "")
            in {"pass", "fail"}
        ]
        semantic_attempted_records = [
            record
            for record in strategy_records
            if str(_dict(record.get("validation")).get("semantic_validation") or "")
            in {"pass", "fail", "blocker", "not_applicable"}
        ]
        semantic_details = [
            _dict(_dict(record.get("validation")).get("semantic_validation_details"))
            for record in strategy_records
            if _dict(_dict(record.get("validation")).get("semantic_validation_details"))
        ]
        semantic_checks = [
            _dict(check)
            for details in semantic_details
            for check in _list(details.get("checks"))
        ]
        api_contract_checks = [
            check
            for check in semantic_checks
            if str(check.get("check_id") or "") == "api_contract_compatibility"
            and str(check.get("status") or "") in {"pass", "fail"}
        ]
        workspace_consistency_checks = [
            check
            for check in semantic_checks
            if str(check.get("check_id") or "")
            == "patched_workspace_consistency"
            and str(check.get("status") or "") in {"pass", "fail"}
        ]
        minimality_checks = [
            check
            for check in semantic_checks
            if str(check.get("check_id") or "") == "patch_minimality"
            and str(check.get("status") or "") in {"pass", "fail"}
        ]
        differential_checks = [
            check
            for check in semantic_checks
            if str(check.get("check_id") or "")
            == "target_behavior_differential"
            and str(check.get("status") or "") in {"pass", "fail"}
        ]
        boundary_probe_checks = [
            check
            for check in semantic_checks
            if str(check.get("check_id") or "")
            == "generated_boundary_property_probe"
        ]
        manifest_semantic_checks = [
            check
            for check in semantic_checks
            if str(check.get("check_id") or "") == "manifest_semantic_commands"
        ]
        reverse_mutation_checks = [
            check
            for check in semantic_checks
            if str(check.get("check_id") or "") == "reverse_mutation_sensitivity"
        ]
        failure_layers = Counter(
            str(_dict(record.get("failure")).get("layer") or "unknown")
            for record in strategy_records
            if str(_dict(record.get("failure")).get("layer") or "none") != "none"
        )
        failure_categories = Counter(
            f"{_dict(record.get('failure')).get('layer')}:"
            f"{_dict(record.get('failure')).get('category')}"
            for record in strategy_records
            if str(_dict(record.get("failure")).get("layer") or "none") != "none"
        )
        winning_families = Counter(
            str(_dict(record.get("candidate")).get("generator_family") or "unknown")
            for record in strategy_records
            if str(_dict(record.get("outcome")).get("status") or "")
            == "verified_repair"
        )
        metrics[strategy] = {
            "case_denominator": len(case_ids),
            "expected_trials_per_case": expected_trials,
            "observed_trial_count": len(trial_groups),
            "record_count": len(strategy_records),
            "pass_at_1": _ratio(len(pass_at_1_cases), len(case_ids)),
            "pass_at_1_count": len(pass_at_1_cases),
            "pass_at_3": _ratio(len(pass_at_3_cases), len(case_ids)),
            "pass_at_3_count": len(pass_at_3_cases),
            "verified_repair_rate": _ratio(len(verified_cases), len(case_ids)),
            "verified_repair_case_count": len(verified_cases),
            "reflection_recovery_rate": _ratio(
                len(reflection_cases), len(case_ids)
            ),
            "reflection_recovery_case_count": len(reflection_cases),
            "ast_valid_rate": _ratio(
                sum(
                    _dict(record.get("validation")).get("ast_valid") is True
                    for record in executable_records
                ),
                len(executable_records),
            ),
            "ast_valid_denominator": len(executable_records),
            "safety_pass_rate": _ratio(
                sum(
                    str(_dict(record.get("validation")).get("safety_gate")) == "pass"
                    for record in safety_records
                ),
                len(safety_records),
            ),
            "safety_denominator": len(safety_records),
            "targeted_test_pass_rate": _ratio(
                sum(
                    str(_dict(record.get("validation")).get("targeted_tests"))
                    == "pass"
                    for record in targeted_records
                ),
                len(targeted_records),
            ),
            "targeted_test_denominator": len(targeted_records),
            "full_regression_pass_rate": _ratio(
                sum(
                    str(_dict(record.get("validation")).get("full_regression"))
                    == "pass"
                    for record in regression_records
                ),
                len(regression_records),
            ),
            "full_regression_denominator": len(regression_records),
            "semantic_validation_pass_rate": _ratio(
                sum(
                    str(_dict(record.get("validation")).get("semantic_validation"))
                    == "pass"
                    for record in semantic_records
                ),
                len(semantic_records),
            ),
            "semantic_validation_denominator": len(semantic_records),
            "semantic_validation_attempted_denominator": len(
                semantic_attempted_records
            ),
            "semantic_validation_blocker_count": sum(
                str(_dict(record.get("validation")).get("semantic_validation"))
                == "blocker"
                for record in semantic_attempted_records
            ),
            "semantic_validation_not_applicable_count": sum(
                str(_dict(record.get("validation")).get("semantic_validation"))
                == "not_applicable"
                for record in semantic_attempted_records
            ),
            "semantic_claim_eligible_record_count": sum(
                details.get("claim_eligible") is True for details in semantic_details
            ),
            "semantic_claim_eligible_rate": _ratio(
                sum(
                    details.get("claim_eligible") is True
                    for details in semantic_details
                ),
                len(semantic_attempted_records),
            ),
            "api_contract_pass_rate": _ratio(
                sum(check.get("status") == "pass" for check in api_contract_checks),
                len(api_contract_checks),
            ),
            "api_contract_denominator": len(api_contract_checks),
            "workspace_consistency_pass_rate": _ratio(
                sum(
                    check.get("status") == "pass"
                    for check in workspace_consistency_checks
                ),
                len(workspace_consistency_checks),
            ),
            "workspace_consistency_denominator": len(
                workspace_consistency_checks
            ),
            "patch_minimality_pass_rate": _ratio(
                sum(check.get("status") == "pass" for check in minimality_checks),
                len(minimality_checks),
            ),
            "patch_minimality_denominator": len(minimality_checks),
            "target_differential_pass_rate": _ratio(
                sum(
                    check.get("status") == "pass"
                    for check in differential_checks
                ),
                len(differential_checks),
            ),
            "target_differential_denominator": len(differential_checks),
            "generated_boundary_probe_count": sum(
                _int(check.get("probe_count"), 0)
                for check in boundary_probe_checks
            ),
            "generated_boundary_case_count": sum(
                _int(check.get("case_count"), 0)
                for check in boundary_probe_checks
            ),
            "manifest_semantic_command_count": sum(
                len(_list(check.get("commands")))
                for check in manifest_semantic_checks
            ),
            "reverse_mutation_count": sum(
                _int(check.get("mutation_count"), 0)
                for check in reverse_mutation_checks
            ),
            "reverse_mutation_kill_rate": _ratio(
                sum(
                    _int(check.get("killed_mutation_count"), 0)
                    for check in reverse_mutation_checks
                ),
                sum(
                    _int(check.get("mutation_count"), 0)
                    for check in reverse_mutation_checks
                ),
            ),
            "reverse_mutation_killed_count": sum(
                _int(check.get("killed_mutation_count"), 0)
                for check in reverse_mutation_checks
            ),
            "reverse_mutation_surviving_count": sum(
                _int(check.get("surviving_mutation_count"), 0)
                for check in reverse_mutation_checks
            ),
            "token_usage": {
                "input_tokens": sum(
                    _int(_dict(record.get("usage")).get("input_tokens"), 0)
                    for record in strategy_records
                ),
                "cache_hit_input_tokens": sum(
                    _int(
                        _dict(record.get("usage")).get("cache_hit_input_tokens"),
                        0,
                    )
                    for record in strategy_records
                ),
                "cache_miss_input_tokens": sum(
                    _int(
                        _dict(record.get("usage")).get("cache_miss_input_tokens"),
                        0,
                    )
                    for record in strategy_records
                ),
                "output_tokens": sum(
                    _int(_dict(record.get("usage")).get("output_tokens"), 0)
                    for record in strategy_records
                ),
                "reasoning_tokens": sum(
                    _int(_dict(record.get("usage")).get("reasoning_tokens"), 0)
                    for record in strategy_records
                ),
            },
            "actual_cost_usd": round(
                sum(
                    _float(_dict(record.get("cost")).get("actual_cost_usd"), 0.0)
                    for record in strategy_records
                ),
                8,
            ),
            "latency_ms": sum(
                _float(_dict(record.get("timing")).get("latency_ms"), 0.0)
                for record in strategy_records
            ),
            "provider_retry_count": sum(
                _int(
                    _dict(record.get("timing")).get("provider_retry_count"), 0
                )
                for record in strategy_records
            ),
            "provider_blocker_record_count": sum(
                str(_dict(record.get("outcome")).get("status") or "")
                == "provider_blocker"
                for record in strategy_records
            ),
            "environment_blocker_record_count": sum(
                str(_dict(record.get("outcome")).get("status") or "")
                == "environment_blocker"
                for record in strategy_records
            ),
            "winning_generator_families": dict(sorted(winning_families.items())),
            "failure_layers": dict(sorted(failure_layers.items())),
            "failure_categories": dict(sorted(failure_categories.items())),
        }
    return metrics


def summarize_v3_model_metadata(
    records: list[dict[str, Any]],
    protocol: dict[str, Any],
) -> dict[str, Any]:
    model_records = [
        _dict(record)
        for record in records
        if str(_dict(_dict(record).get("candidate")).get("generator_family") or "")
        == "llm"
    ]
    protocol_model = _dict(protocol.get("model"))
    protocol_prompts = {
        str(_dict(value).get("id") or ""): str(_dict(value).get("sha256") or "")
        for value in _list(protocol.get("prompts"))
        if str(_dict(value).get("id") or "")
    }
    missing_core_metadata_count = 0
    request_hashes = set()
    system_hashes = set()
    providers = set()
    model_ids = set()
    provider_response_models = set()
    observed_call_dates = set()
    prompt_ids = set()
    for record in model_records:
        model = _dict(record.get("model"))
        timestamps = _dict(record.get("timestamps"))
        if not all(
            str(model.get(field) or "")
            for field in ("provider", "model_id", "prompt_id", "prompt_sha256")
        ):
            missing_core_metadata_count += 1
        providers.add(str(model.get("provider") or ""))
        model_ids.add(str(model.get("model_id") or ""))
        prompt_ids.add(str(model.get("prompt_id") or ""))
        if model.get("request_prompt_sha256"):
            request_hashes.add(str(model.get("request_prompt_sha256")))
        if model.get("system_prompt_sha256"):
            system_hashes.add(str(model.get("system_prompt_sha256")))
        if model.get("provider_response_model"):
            provider_response_models.add(str(model.get("provider_response_model")))
        completed_at = str(timestamps.get("completed_at") or "")
        if len(completed_at) >= 10:
            observed_call_dates.add(completed_at[:10])
    providers.discard("")
    model_ids.discard("")
    prompt_ids.discard("")
    status = (
        "not_applicable"
        if not model_records
        else "pass"
        if not missing_core_metadata_count
        else "fail"
    )
    return {
        "status": status,
        "model_record_count": len(model_records),
        "missing_core_metadata_count": missing_core_metadata_count,
        "protocol_provider": str(protocol_model.get("provider") or ""),
        "protocol_model_id": str(protocol_model.get("model_id") or ""),
        "temperature": protocol_model.get("temperature"),
        "thinking": str(protocol_model.get("thinking") or ""),
        "reasoning_effort": str(protocol_model.get("reasoning_effort") or ""),
        "protocol_prompt_hashes": dict(sorted(protocol_prompts.items())),
        "observed_providers": sorted(providers),
        "observed_model_ids": sorted(model_ids),
        "observed_prompt_ids": sorted(prompt_ids),
        "provider_response_models": sorted(provider_response_models),
        "observed_call_dates": sorted(observed_call_dates),
        "request_prompt_hash_count": len(request_hashes),
        "request_prompt_hash_set_sha256": _sha256_json(sorted(request_hashes)),
        "system_prompt_hash_count": len(system_hashes),
        "system_prompt_hash_set_sha256": _sha256_json(sorted(system_hashes)),
        "raw_prompts_persisted": False,
        "raw_provider_payloads_persisted": False,
    }


def audit_v3_evaluation_completeness(
    records: list[dict[str, Any]],
    *,
    case_ids: list[str],
    strategies: list[str],
    protocol: dict[str, Any],
) -> dict[str, Any]:
    observed = {
        (
            str(_dict(record.get("case")).get("case_id") or ""),
            str(_dict(record.get("strategy")).get("mode") or ""),
            _int(_dict(record.get("strategy")).get("trial_index"), 0),
        )
        for record in records
    }
    missing = []
    for case_id in case_ids:
        for strategy in strategies:
            for trial_index in _trial_indices(protocol, strategy):
                if (case_id, strategy, trial_index) not in observed:
                    missing.append(
                        {
                            "case_id": case_id,
                            "strategy": strategy,
                            "trial_index": trial_index,
                        }
                    )
    return {
        "status": "pass" if not missing else "fail",
        "expected_trial_count": sum(
            len(_trial_indices(protocol, strategy))
            for strategy in strategies
        )
        * len(case_ids),
        "observed_trial_count": len(observed),
        "missing_trial_count": len(missing),
        "missing_trials": missing,
    }


def write_v3_repair_evaluation_artifacts(
    result: dict[str, Any],
    records: list[dict[str, Any]],
    output_dir: str | Path,
) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "evaluation.json"
    markdown_path = output / "evaluation.md"
    records_path = output / "run_records.jsonl"
    _write_text_lf(
        json_path,
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
    )
    _write_text_lf(markdown_path, render_v3_repair_evaluation_markdown(result))
    _write_text_lf(
        records_path,
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
    )
    return {
        "json": json_path.as_posix(),
        "markdown": markdown_path.as_posix(),
        "run_records": records_path.as_posix(),
    }


def _write_text_lf(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def render_v3_repair_evaluation_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# V3 Real-Bug Repair Evaluation",
        "",
        f"- Status: `{result.get('status')}`",
        f"- Cases: `{result.get('case_count')}`",
        f"- Run records: `{result.get('record_count')}`",
        f"- Strategies: `{', '.join(str(item) for item in _list(result.get('strategies')))}`",
        f"- Live model: `{str(bool(result.get('live_model', False))).lower()}`",
        f"- Boundary: {result.get('boundary')}",
        "",
        "## Strategy Metrics",
        "",
        "| Strategy | pass@1 | pass@3 | Verified | Reflection | AST valid | Safety | Targeted | Regression | Semantic claim | Mutation kill | Cost USD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for strategy, value in _dict(result.get("metrics")).items():
        row = _dict(value)
        lines.append(
            f"| {strategy} | {_metric(row.get('pass_at_1'))} | "
            f"{_metric(row.get('pass_at_3'))} | "
            f"{_metric(row.get('verified_repair_rate'))} | "
            f"{_metric(row.get('reflection_recovery_rate'))} | "
            f"{_metric(row.get('ast_valid_rate'))} | "
            f"{_metric(row.get('safety_pass_rate'))} | "
            f"{_metric(row.get('targeted_test_pass_rate'))} | "
            f"{_metric(row.get('full_regression_pass_rate'))} | "
            f"{_metric(row.get('semantic_claim_eligible_rate'))} | "
            f"{_metric(row.get('reverse_mutation_kill_rate'))} | "
            f"{_float(row.get('actual_cost_usd'), 0.0):.6f} |"
        )
    lines.extend(
        [
            "",
            "## Completeness",
            "",
            f"- Status: `{_dict(result.get('completeness')).get('status')}`",
            f"- Expected trials: `{_dict(result.get('completeness')).get('expected_trial_count')}`",
            f"- Observed trials: `{_dict(result.get('completeness')).get('observed_trial_count')}`",
            f"- Missing trials: `{_dict(result.get('completeness')).get('missing_trial_count')}`",
            "",
            "## Case Outcomes",
            "",
            "| Case | Status | Editable regions | Trials |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    for value in _list(result.get("case_results")):
        case = _dict(value)
        preparation = _dict(case.get("preparation"))
        lines.append(
            f"| `{case.get('case_id')}` | `{case.get('status')}` | "
            f"{_int(preparation.get('editable_region_count'), 0)} | "
            f"{len(_list(case.get('trials')))} |"
        )
    lines.append("")
    return "\n".join(lines)


def _load_resumable_trial(
    path: Path,
    *,
    protocol: dict[str, Any],
    expected_input_fingerprint: str,
    retry_blockers: bool,
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        result = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    if str(result.get("input_fingerprint") or "") != expected_input_fingerprint:
        return None
    records = [_dict(record) for record in _list(result.get("records"))]
    audit = validate_run_records(records, protocol=protocol, require_complete=False)
    if audit["status"] != "pass" or not records:
        return None
    if retry_blockers and any(
        str(_dict(record.get("outcome")).get("status") or "")
        in {"provider_blocker", "environment_blocker"}
        for record in records
    ):
        return None
    return result


def build_v3_trial_input_fingerprint(
    protocol: dict[str, Any],
    prepared: PreparedV3RepairCase,
) -> str:
    payload = {
        "protocol_sha256": str(protocol.get("protocol_sha256") or ""),
        "case_id": str(_dict(prepared.case).get("case_id") or ""),
        "bug_commit_sha": str(
            _dict(prepared.case).get("bug_commit_sha") or ""
        ),
        "model_context_sha256": str(
            _dict(prepared.model_context_audit).get("context_sha256") or ""
        ),
        "analysis_selection_sha256": str(
            _dict(prepared.analysis_scope_ground_truth_audit).get(
                "selection_snapshot_sha256"
            )
            or ""
        ),
        "dynamic_evidence_sha256": _sha256_json(prepared.dynamic_evidence),
        "localization_sha256": _sha256_json(prepared.localization),
        "implementation_sha256": _v3_trial_implementation_sha256(),
    }
    return _sha256_json(payload)


def _first_baseline_execution(reproduction: dict[str, Any]) -> dict[str, Any]:
    results = _list(_dict(reproduction.get("bug_targeted")).get("results"))
    return _dict(results[0]) if results else {}


def _trial_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy_mode": str(result.get("strategy_mode") or ""),
        "trial_index": _int(result.get("trial_index"), 0),
        "trial_id": str(result.get("trial_id") or ""),
        "status": str(result.get("status") or ""),
        "verified_repair": bool(result.get("verified_repair", False)),
        "winning_run_id": str(result.get("winning_run_id") or ""),
        "record_count": _int(result.get("record_count"), 0),
        "resumed": bool(result.get("resumed", False)),
        "attempt_dir": str(result.get("attempt_dir") or ""),
        "input_fingerprint": str(result.get("input_fingerprint") or ""),
    }


def _trial_verified(records: list[dict[str, Any]]) -> bool:
    return any(
        str(_dict(record.get("outcome")).get("status") or "")
        == "verified_repair"
        for record in records
    )


def _trial_indices(protocol: dict[str, Any], strategy: str) -> list[int]:
    randomness = _dict(protocol.get("randomness"))
    count = {
        "rule": _int(randomness.get("rule_trials"), 1),
        "llm": _int(randomness.get("llm_trials"), 3),
        "hybrid": _int(randomness.get("hybrid_trials"), 3),
    }[strategy]
    return list(range(1, max(0, count) + 1))


def _model_key_present(protocol: dict[str, Any]) -> bool:
    return any(
        bool(os.environ.get(str(name)))
        for name in _list(_dict(protocol.get("model")).get("api_key_env_names"))
    )


def _normalize_strategies(values: list[str]) -> list[str]:
    normalized = []
    for value in values:
        for item in str(value).split(","):
            strategy = item.strip().lower()
            if not strategy:
                continue
            if strategy not in ALL_STRATEGIES:
                raise ValueError(f"Unsupported V3 strategy: {strategy}")
            if strategy not in normalized:
                normalized.append(strategy)
    if not normalized:
        raise ValueError("At least one V3 repair strategy is required")
    return normalized


def _safe_seed_file(root: Path, relative_path: str) -> Path | None:
    relative = Path(relative_path.replace("\\", "/"))
    if not relative_path or relative.is_absolute() or ".." in relative.parts:
        return None
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def _safe_project_path(root: Path, relative_path: str) -> Path | None:
    relative = Path(relative_path.replace("\\", "/"))
    if not relative_path or relative.is_absolute() or ".." in relative.parts:
        return None
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _sha256_json(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _v3_trial_implementation_sha256() -> str:
    package_root = Path(__file__).resolve().parents[1]
    hashes = {
        relative_path: sha256_file(package_root / relative_path)
        for relative_path in TRIAL_IMPLEMENTATION_FILES
    }
    return _sha256_json(hashes)


def _metric(value: Any) -> str:
    return "n/a" if value is None else f"{_float(value, 0.0):.3f}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        description="Run the V3 real-bug Rule/LLM/Hybrid repair evaluation."
    )
    parser.add_argument(
        "output_dir",
        help="Output directory for contexts, isolated trials, records, and metrics.",
    )
    parser.add_argument("--root", default=".", help="Project repository root.")
    parser.add_argument(
        "--protocol",
        default="datasets/v3_real_bugs/experiment_protocol.json",
    )
    parser.add_argument(
        "--catalog",
        default="docs/v3/phase1_real_bug_catalog.json",
    )
    parser.add_argument(
        "--environment-profiles",
        default="datasets/v3_real_bugs/environment_profile_sources.json",
    )
    parser.add_argument(
        "--reproduction-root",
        default="outputs_v3/reproduction",
    )
    parser.add_argument(
        "--strategies",
        default="rule",
        help="Comma-separated subset of rule,llm,hybrid. Default: rule.",
    )
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--live-model",
        action="store_true",
        help="Explicitly authorize paid model calls for LLM/Hybrid strategies.",
    )
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--retry-blockers", action="store_true")
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Maximum independent trial workers per case. Default: 1.",
    )
    parser.add_argument("--rule-candidate-limit", type=int, default=5)
    parser.add_argument("--targeted-timeout", type=int, default=120)
    parser.add_argument("--regression-timeout", type=int, default=900)
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument("--require-pass", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    root = Path(args.root).resolve()
    result = run_v3_repair_evaluation(
        project_root=root,
        protocol_path=root / args.protocol,
        catalog_path=root / args.catalog,
        environment_profiles_path=root / args.environment_profiles,
        reproduction_root=root / args.reproduction_root,
        output_dir=args.output_dir,
        strategies=[args.strategies],
        case_ids=args.case_id,
        max_cases=max(0, args.max_cases),
        prepare_only=args.prepare_only,
        live_model=args.live_model,
        resume=not args.no_resume,
        retry_blockers=args.retry_blockers,
        max_workers=max(1, args.max_workers),
        rule_candidate_limit=max(0, args.rule_candidate_limit),
        targeted_timeout=max(1, args.targeted_timeout),
        regression_timeout=max(1, args.regression_timeout),
    )
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(render_v3_repair_evaluation_markdown(result))
    if args.require_pass and result["status"] not in {"pass", "prepared"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
