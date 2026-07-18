from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from code_intelligence_agent.evaluation.v4_real_bug_benchmark import (
    catalog_fingerprint,
    summarize_catalog,
    validate_v4_catalog,
)
from code_intelligence_agent.evaluation.v4_real_bug_reproduction import (
    reproduction_evidence_fingerprint,
    reproduction_plan_fingerprint,
)


SCHEMA_VERSION = "4.0"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
MAX_ARTIFACT_MEMBERS = 32
MAX_ARTIFACT_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_EXPANDED_BYTES = 8 * 1024 * 1024
MAX_MEMBER_COMPRESSION_RATIO = 200
MAX_BATCH_ACCEPTANCE_CASES = 16
ACCEPTANCE_GATES = {
    "bug_targeted_failed": True,
    "fix_targeted_passed": True,
    "fix_full_regression_passed": True,
    "reproducible": True,
}


def accept_v4_reproduction_artifact(
    catalog: dict[str, Any],
    artifact_archive: str | Path,
    attestation: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    before_summary = summarize_catalog(catalog)
    before_manifest = str(catalog.get("manifest_sha256") or "")
    catalog_audit = validate_v4_catalog(catalog)
    errors = [f"catalog:{value}" for value in _list(catalog_audit.get("errors"))]
    case_id = str(_dict(attestation.get("reproduction")).get("case_id") or "")
    case = _find_case(catalog, case_id)
    if case is None:
        errors.append("catalog_case_missing")
    elif case.get("status") != "candidate":
        errors.append("catalog_case_must_be_candidate")

    archive = _read_artifact_archive(artifact_archive, attestation)
    errors.extend(_list(archive.get("errors")))
    plan = _dict(archive.get("plan"))
    evidence = _dict(archive.get("evidence"))
    if case is not None and plan and evidence:
        errors.extend(
            validate_v4_reproduction_evidence(
                case,
                plan=plan,
                evidence=evidence,
                catalog_manifest_sha256=before_manifest,
                selection_plan_sha256=str(
                    _dict(catalog.get("selection_plan")).get("plan_sha256") or ""
                ),
            )
        )
    errors.extend(
        _validate_attestation(
            attestation,
            archive=archive,
            plan=plan,
            evidence=evidence,
            case=case or {},
        )
    )
    errors = sorted(set(str(value) for value in errors if str(value)))
    if errors:
        return copy.deepcopy(catalog), _acceptance_audit(
            status="fail",
            case_id=case_id,
            errors=errors,
            before_summary=before_summary,
            after_summary=before_summary,
            before_manifest=before_manifest,
            after_manifest=before_manifest,
            attestation=attestation,
            archive=archive,
        )

    result = copy.deepcopy(catalog)
    accepted_case = _find_case(result, case_id)
    assert accepted_case is not None
    review = _dict(attestation.get("difficulty_review"))
    accepted_case["status"] = "accepted"
    accepted_case["difficulty_evidence"] = copy.deepcopy(
        _dict(review.get("evidence"))
    )
    accepted_case["difficulty_review_status"] = "verified"
    accepted_case["reproduction"] = _summarize_reproduction(
        evidence,
        plan=plan,
        attestation=attestation,
        archive=archive,
    )
    accepted_case["rejection_reason"] = ""
    accepted_case["rejection_evidence"] = {}
    result["summary"] = summarize_catalog(result)
    result["manifest_sha256"] = catalog_fingerprint(result)
    final_audit = validate_v4_catalog(result)
    final_errors = [
        f"updated_catalog:{value}" for value in _list(final_audit.get("errors"))
    ]
    if final_errors:
        return copy.deepcopy(catalog), _acceptance_audit(
            status="fail",
            case_id=case_id,
            errors=final_errors,
            before_summary=before_summary,
            after_summary=before_summary,
            before_manifest=before_manifest,
            after_manifest=before_manifest,
            attestation=attestation,
            archive=archive,
        )
    return result, _acceptance_audit(
        status="pass",
        case_id=case_id,
        errors=[],
        before_summary=before_summary,
        after_summary=_dict(result.get("summary")),
        before_manifest=before_manifest,
        after_manifest=str(result.get("manifest_sha256") or ""),
        attestation=attestation,
        archive=archive,
    )


def accept_v4_reproduction_artifact_batch(
    catalog: dict[str, Any],
    artifact_archive: str | Path,
    attestations: list[Any],
    *,
    manifest_schema_version: str = SCHEMA_VERSION,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate several cases against one pre-update manifest, then commit together."""
    before_summary = summarize_catalog(catalog)
    before_manifest = str(catalog.get("manifest_sha256") or "")
    normalized = [_dict(value) for value in attestations]
    case_ids = [
        str(_dict(value.get("reproduction")).get("case_id") or "")
        for value in normalized
    ]
    errors: list[str] = []
    if manifest_schema_version != SCHEMA_VERSION:
        errors.append("batch_manifest_schema_version_mismatch")
    if not attestations:
        errors.append("batch_attestations_are_required")
    if len(attestations) > MAX_BATCH_ACCEPTANCE_CASES:
        errors.append("batch_attestation_count_exceeded")
    for index, value in enumerate(attestations):
        if not isinstance(value, dict):
            errors.append(f"batch_attestation_is_not_object:{index}")
    duplicates = sorted(
        {case_id for case_id in case_ids if case_ids.count(case_id) > 1}
    )
    errors.extend(f"batch_case_id_is_duplicate:{value}" for value in duplicates)

    identities = [_batch_attestation_identity(value) for value in normalized]
    if identities and any(value != identities[0] for value in identities[1:]):
        errors.append("batch_artifact_or_workflow_identity_mismatch")
    if errors:
        return copy.deepcopy(catalog), _batch_acceptance_audit(
            status="fail",
            case_ids=case_ids,
            errors=sorted(set(errors)),
            before_summary=before_summary,
            after_summary=before_summary,
            before_manifest=before_manifest,
            after_manifest=before_manifest,
            attestations=normalized,
            individual_audits=[],
        )

    individual_results: list[dict[str, Any]] = []
    individual_audits: list[dict[str, Any]] = []
    for attestation in normalized:
        result, audit = accept_v4_reproduction_artifact(
            catalog,
            artifact_archive,
            attestation,
        )
        individual_results.append(result)
        individual_audits.append(audit)
        if audit.get("status") != "pass":
            case_id = str(audit.get("case_id") or "")
            errors.extend(
                f"case:{case_id}:{value}"
                for value in _list(audit.get("errors"))
            )
    if errors:
        return copy.deepcopy(catalog), _batch_acceptance_audit(
            status="fail",
            case_ids=case_ids,
            errors=sorted(set(errors)),
            before_summary=before_summary,
            after_summary=before_summary,
            before_manifest=before_manifest,
            after_manifest=before_manifest,
            attestations=normalized,
            individual_audits=individual_audits,
        )

    merged = copy.deepcopy(catalog)
    for case_id, individual_result in zip(case_ids, individual_results):
        source_case = _find_case(individual_result, case_id)
        target_case = _find_case(merged, case_id)
        if source_case is None or target_case is None:
            errors.append(f"batch_validated_case_missing:{case_id}")
            continue
        target_case.clear()
        target_case.update(copy.deepcopy(source_case))
    if not errors:
        merged["summary"] = summarize_catalog(merged)
        merged["manifest_sha256"] = catalog_fingerprint(merged)
        final_audit = validate_v4_catalog(merged)
        errors.extend(
            f"updated_catalog:{value}"
            for value in _list(final_audit.get("errors"))
        )
    if errors:
        return copy.deepcopy(catalog), _batch_acceptance_audit(
            status="fail",
            case_ids=case_ids,
            errors=sorted(set(errors)),
            before_summary=before_summary,
            after_summary=before_summary,
            before_manifest=before_manifest,
            after_manifest=before_manifest,
            attestations=normalized,
            individual_audits=individual_audits,
        )
    return merged, _batch_acceptance_audit(
        status="pass",
        case_ids=case_ids,
        errors=[],
        before_summary=before_summary,
        after_summary=_dict(merged.get("summary")),
        before_manifest=before_manifest,
        after_manifest=str(merged.get("manifest_sha256") or ""),
        attestations=normalized,
        individual_audits=individual_audits,
    )


def validate_v4_reproduction_evidence(
    case: dict[str, Any],
    *,
    plan: dict[str, Any],
    evidence: dict[str, Any],
    catalog_manifest_sha256: str,
    selection_plan_sha256: str,
) -> list[str]:
    errors: list[str] = []
    case_id = str(case.get("case_id") or "")
    if str(plan.get("schema_version") or "") != SCHEMA_VERSION:
        errors.append("plan_schema_version_mismatch")
    if reproduction_plan_fingerprint(plan) != str(plan.get("plan_sha256") or ""):
        errors.append("plan_sha256_mismatch")
    if str(plan.get("catalog_manifest_sha256") or "") != catalog_manifest_sha256:
        errors.append("plan_catalog_manifest_sha256_mismatch")
    if str(plan.get("selection_plan_sha256") or "") != selection_plan_sha256:
        errors.append("plan_selection_plan_sha256_mismatch")
    if not SHA256_PATTERN.fullmatch(str(plan.get("profiles_sha256") or "")):
        errors.append("plan_profiles_sha256_is_invalid")
    if plan.get("runtime_root_committed") is not False:
        errors.append("plan_runtime_root_must_remain_uncommitted")
    if plan.get("repository_setup_scripts_executed") is not False:
        errors.append("plan_repository_setup_script_must_not_execute")
    plan_items = [
        _dict(value)
        for value in _list(plan.get("items"))
        if str(_dict(value).get("case_id") or "") == case_id
    ]
    if len(plan_items) != 1:
        errors.append("plan_requires_exactly_one_case_item")
        return sorted(set(errors))
    item = plan_items[0]
    if item.get("readiness") != "ready" or _list(item.get("blockers")):
        errors.append("plan_case_must_be_ready_without_blockers")
    for field in ("bug_commit_sha", "fix_commit_sha"):
        if str(item.get(field) or "") != str(case.get(field) or ""):
            errors.append(f"plan_{field}_mismatch")
    errors.extend(_validate_plan_case_contract(case, plan=plan, item=item))
    errors.extend(_validate_plan_runtime_contract(case, item=item, evidence=evidence))

    if str(evidence.get("schema_version") or "") != SCHEMA_VERSION:
        errors.append("evidence_schema_version_mismatch")
    if reproduction_evidence_fingerprint(evidence) != str(
        evidence.get("evidence_sha256") or ""
    ):
        errors.append("evidence_sha256_mismatch")
    if str(evidence.get("case_id") or "") != case_id:
        errors.append("evidence_case_id_mismatch")
    for field in ("bug_commit_sha", "fix_commit_sha"):
        if str(evidence.get(field) or "") != str(case.get(field) or ""):
            errors.append(f"evidence_{field}_mismatch")
    if evidence.get("status") != "pass":
        errors.append("evidence_status_must_pass")
    if evidence.get("reason") != "real_bug_reproduced":
        errors.append("evidence_reason_mismatch")
    if _dict(evidence.get("blocker")):
        errors.append("accepted_evidence_cannot_have_blocker")

    runtime = _dict(evidence.get("runtime"))
    expected_version = str(_dict(case.get("environment")).get("python_version") or "")
    if runtime.get("status") != "pass" or runtime.get("exact_match") is not True:
        errors.append("runtime_exact_match_required")
    if str(runtime.get("expected_version") or "") != expected_version:
        errors.append("runtime_expected_version_mismatch")
    if str(runtime.get("observed_version") or "") != expected_version:
        errors.append("runtime_observed_version_mismatch")

    execution = _dict(evidence.get("execution_contract"))
    if execution.get("benchmark_setup_script_executed") is not False:
        errors.append("benchmark_setup_script_must_not_execute")
    if execution.get("gold_patch_visible_to_execution") is not False:
        errors.append("gold_patch_must_remain_hidden")
    if _int(execution.get("model_calls"), -1) != 0:
        errors.append("reproduction_model_calls_must_be_zero")
    adaptation = _dict(execution.get("adaptation"))
    expected_contract = _dict(item.get("execution_contract"))
    expected_environment = _dict(expected_contract.get("test_environment"))
    if adaptation.get("status") != "pass" or _list(adaptation.get("errors")):
        errors.append("evidence_adaptation_must_pass")
    if _int(adaptation.get("preparation_file_count"), -1) != len(
        _list(expected_contract.get("preparation_files"))
    ):
        errors.append("evidence_preparation_file_count_mismatch")
    if [str(value) for value in _list(adaptation.get("repository_pytest_plugins"))] != [
        str(value)
        for value in _list(expected_environment.get("repository_pytest_plugins"))
    ]:
        errors.append("evidence_repository_pytest_plugins_mismatch")

    preparation = _dict(evidence.get("preparation"))
    if preparation.get("status") != "pass":
        errors.append("preparation_must_pass")
    _validate_checkout(
        errors,
        "bug",
        _dict(preparation.get("bug_checkout")),
        str(case.get("bug_commit_sha") or ""),
    )
    _validate_checkout(
        errors,
        "fix",
        _dict(preparation.get("fix_checkout")),
        str(case.get("fix_commit_sha") or ""),
    )
    _validate_test_overlay(
        errors,
        _dict(preparation.get("test_overlay")),
        [str(value) for value in _list(expected_contract.get("test_overlay_paths"))],
    )
    expected_preparation = [
        _dict(value) for value in _list(expected_contract.get("preparation_files"))
    ]
    for name in ("bug_preparation_files", "fix_preparation_files"):
        _validate_preparation_files(
            errors,
            name,
            _dict(preparation.get(name)),
            expected_preparation,
        )

    targeted_commands = [
        [str(part) for part in _list(command)]
        for command in _list(expected_contract.get("targeted_test_commands"))
    ]
    regression_command = [
        str(part) for part in _list(expected_contract.get("regression_command"))
    ]
    errors.extend(
        _validate_group(
            "bug_targeted",
            _dict(evidence.get("bug_targeted")),
            expected_status="fail",
            expected_commands=targeted_commands,
            require_assertion_failure=True,
        )
    )
    errors.extend(
        _validate_group(
            "fix_targeted",
            _dict(evidence.get("fix_targeted")),
            expected_status="pass",
            expected_commands=targeted_commands,
        )
    )
    errors.extend(
        _validate_group(
            "fix_full_regression",
            _dict(evidence.get("fix_full_regression")),
            expected_status="pass",
            expected_commands=[regression_command],
        )
    )
    acceptance = _dict(evidence.get("acceptance"))
    for gate, expected in ACCEPTANCE_GATES.items():
        if acceptance.get(gate) is not expected:
            errors.append(f"acceptance_gate_failed:{gate}")
    for field in ("started_at", "completed_at"):
        if not _valid_iso_datetime(str(evidence.get(field) or "")):
            errors.append(f"{field}_must_be_iso_datetime")
    return sorted(set(errors))


def write_v4_acceptance_artifacts(
    catalog: dict[str, Any],
    audit: dict[str, Any],
    *,
    catalog_output: str | Path,
    audit_output: str | Path,
) -> dict[str, str]:
    catalog_path = Path(catalog_output)
    audit_path = Path(audit_output)
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    if catalog_path.resolve() == audit_path.resolve():
        raise ValueError("catalog_output and audit_output must be different files")
    _write_lf_atomic(
        audit_path,
        json.dumps(audit, indent=2, ensure_ascii=False) + "\n",
    )
    _write_lf_atomic(
        catalog_path,
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
    )
    return {"catalog": str(catalog_path), "audit": str(audit_path)}


def _read_artifact_archive(
    artifact_archive: str | Path,
    attestation: dict[str, Any],
) -> dict[str, Any]:
    path = Path(artifact_archive)
    errors: list[str] = []
    result: dict[str, Any] = {"errors": errors, "plan": {}, "evidence": {}}
    artifact = _dict(attestation.get("artifact"))
    if not path.is_file() or path.is_symlink():
        errors.append("artifact_archive_missing_or_symlink")
        return result
    observed_size = path.stat().st_size
    result["artifact_size_bytes"] = observed_size
    if observed_size > MAX_ARTIFACT_ARCHIVE_BYTES:
        errors.append("artifact_archive_size_exceeded")
        return result
    observed_sha256 = _sha256_file(path)
    result["artifact_sha256"] = observed_sha256
    result["artifact_size_bytes"] = observed_size
    if observed_sha256 != str(artifact.get("sha256") or ""):
        errors.append("artifact_archive_sha256_mismatch")
    if observed_size != _int(artifact.get("size_bytes"), -1):
        errors.append("artifact_archive_size_mismatch")
    plan_member = str(artifact.get("plan_member") or "")
    evidence_member = str(artifact.get("evidence_member") or "")
    required = {plan_member, evidence_member}
    if not all(_safe_artifact_member(value) for value in required):
        errors.append("artifact_required_member_is_unsafe")
        return result
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARTIFACT_MEMBERS:
                errors.append("artifact_member_count_exceeded")
            expanded = 0
            observed_names: set[str] = set()
            for info in infos:
                name = str(info.filename)
                if not _safe_artifact_member(name):
                    errors.append(f"artifact_member_is_unsafe:{name}")
                    continue
                if name in observed_names:
                    errors.append(f"artifact_member_is_duplicate:{name}")
                observed_names.add(name)
                if info.flag_bits & 0x1:
                    errors.append(f"artifact_member_is_encrypted:{name}")
                mode = (info.external_attr >> 16) & 0xFFFF
                if mode and (stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                    errors.append(f"artifact_member_type_is_unsafe:{name}")
                expanded += max(0, int(info.file_size))
                if info.file_size > 0 and info.compress_size == 0:
                    errors.append(f"artifact_member_compression_is_invalid:{name}")
                elif info.compress_size > 0 and (
                    info.file_size / info.compress_size > MAX_MEMBER_COMPRESSION_RATIO
                ):
                    errors.append(f"artifact_member_compression_ratio_exceeded:{name}")
            if expanded > MAX_ARTIFACT_EXPANDED_BYTES:
                errors.append("artifact_expanded_size_exceeded")
            for member in required:
                if member not in observed_names:
                    errors.append(f"artifact_required_member_missing:{member}")
            if errors:
                return result
            plan_bytes = archive.read(plan_member)
            evidence_bytes = archive.read(evidence_member)
    except (OSError, zipfile.BadZipFile, KeyError):
        errors.append("artifact_archive_unreadable")
        return result
    result["plan_file_sha256"] = hashlib.sha256(plan_bytes).hexdigest()
    result["evidence_file_sha256"] = hashlib.sha256(evidence_bytes).hexdigest()
    try:
        plan = json.loads(plan_bytes.decode("utf-8"))
        evidence = json.loads(evidence_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        errors.append("artifact_required_json_is_unreadable")
        return result
    if not isinstance(plan, dict) or not isinstance(evidence, dict):
        errors.append("artifact_required_json_must_be_object")
        return result
    result["plan"] = plan
    result["evidence"] = evidence
    return result


def _validate_attestation(
    attestation: dict[str, Any],
    *,
    archive: dict[str, Any],
    plan: dict[str, Any],
    evidence: dict[str, Any],
    case: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if str(attestation.get("schema_version") or "") != SCHEMA_VERSION:
        errors.append("attestation_schema_version_mismatch")
    reference = str(attestation.get("attestation_reference") or "")
    if not _safe_relative_reference(reference):
        errors.append("attestation_reference_is_unsafe")
    workflow = _dict(attestation.get("workflow_run"))
    if _int(workflow.get("run_id"), 0) <= 0 or _int(workflow.get("job_id"), 0) <= 0:
        errors.append("attestation_workflow_identity_is_invalid")
    if workflow.get("conclusion") != "success":
        errors.append("attestation_workflow_must_succeed")
    if not COMMIT_PATTERN.fullmatch(str(workflow.get("head_sha") or "")):
        errors.append("attestation_workflow_head_sha_is_invalid")
    if not str(workflow.get("url") or "").startswith(
        "https://github.com/Anweilong111/code-intelligence-Agent/actions/runs/"
    ):
        errors.append("attestation_workflow_url_is_invalid")
    artifact = _dict(attestation.get("artifact"))
    if _int(artifact.get("artifact_id"), 0) <= 0 or not str(artifact.get("name") or ""):
        errors.append("attestation_artifact_identity_is_invalid")
    if not SHA256_PATTERN.fullmatch(str(artifact.get("sha256") or "")):
        errors.append("attestation_artifact_sha256_is_invalid")
    if _int(artifact.get("size_bytes"), 0) <= 0:
        errors.append("attestation_artifact_size_is_invalid")
    if str(archive.get("plan_file_sha256") or "") != str(
        artifact.get("plan_file_sha256") or ""
    ):
        errors.append("attestation_plan_file_sha256_mismatch")
    reproduction = _dict(attestation.get("reproduction"))
    if str(reproduction.get("case_id") or "") != str(case.get("case_id") or ""):
        errors.append("attestation_case_id_mismatch")
    if str(reproduction.get("evidence_sha256") or "") != str(
        evidence.get("evidence_sha256") or ""
    ):
        errors.append("attestation_evidence_sha256_mismatch")
    if str(reproduction.get("evidence_file_sha256") or "") != str(
        archive.get("evidence_file_sha256") or ""
    ):
        errors.append("attestation_evidence_file_sha256_mismatch")
    if str(reproduction.get("plan_sha256") or "") != str(plan.get("plan_sha256") or ""):
        errors.append("attestation_plan_sha256_mismatch")
    if str(reproduction.get("profiles_sha256") or "") != str(
        plan.get("profiles_sha256") or ""
    ):
        errors.append("attestation_profiles_sha256_mismatch")
    if reproduction.get("reproducible") is not True:
        errors.append("attestation_reproducible_must_be_true")
    evidence_reference = str(reproduction.get("evidence_reference") or "")
    if not _safe_relative_reference(evidence_reference):
        errors.append("attestation_evidence_reference_is_unsafe")
    review = _dict(attestation.get("difficulty_review"))
    review_evidence = _dict(review.get("evidence"))
    expected_categories = sorted(
        str(value) for value in _list(case.get("difficulty_categories"))
    )
    if review.get("status") != "verified":
        errors.append("difficulty_review_must_be_verified")
    if sorted(review_evidence) != expected_categories:
        errors.append("difficulty_review_categories_mismatch")
    for category in expected_categories:
        if not str(review_evidence.get(category) or ""):
            errors.append(f"difficulty_review_evidence_missing:{category}")
    safety = _dict(attestation.get("safety"))
    for field in (
        "repository_setup_script_executed",
        "repository_project_installed",
        "source_build_executed",
        "tests_modified_or_excluded",
        "shared_base_runtime_mutated",
        "raw_artifact_committed",
    ):
        if safety.get(field) is not False:
            errors.append(f"attestation_safety_gate_failed:{field}")
    if _int(safety.get("model_calls"), -1) != 0:
        errors.append("attestation_safety_gate_failed:model_calls")
    return errors


def _validate_plan_case_contract(
    case: dict[str, Any],
    *,
    plan: dict[str, Any],
    item: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    contract = _dict(item.get("execution_contract"))
    if contract.get("setup_script_executed") is not False:
        errors.append("plan_setup_script_must_not_execute")
    if contract.get("gold_patch_visible") is not False:
        errors.append("plan_gold_patch_must_remain_hidden")
    observed_platform = str(contract.get("observed_execution_platform") or "")
    required_platform = str(contract.get("required_execution_platform") or "")
    if observed_platform != str(plan.get("execution_platform") or ""):
        errors.append("plan_execution_platform_mismatch")
    if required_platform not in {"any", observed_platform}:
        errors.append("plan_required_execution_platform_mismatch")
    if str(item.get("benchmark_split") or "") != str(
        case.get("benchmark_split") or ""
    ):
        errors.append("plan_benchmark_split_mismatch")
    if str(item.get("owner_repo") or "") != str(
        _dict(case.get("repository")).get("owner_repo") or ""
    ):
        errors.append("plan_owner_repo_mismatch")
    if "catalog_status" in item and item.get("catalog_status") != case.get("status"):
        errors.append("plan_catalog_status_mismatch")

    expected_overlay = [
        str(value)
        for value in _list(_dict(case.get("environment")).get("declared_test_paths"))
    ]
    observed_overlay = [str(value) for value in _list(contract.get("test_overlay_paths"))]
    if observed_overlay != expected_overlay:
        errors.append("plan_test_overlay_paths_mismatch")

    regressions = [
        [str(part) for part in _list(command)]
        for command in _list(case.get("regression_tests"))
    ]
    observed_regression = [
        str(part) for part in _list(contract.get("regression_command"))
    ]
    if len(regressions) != 1 or observed_regression != regressions[0]:
        errors.append("plan_regression_command_mismatch")

    expected_targeted = [
        [str(part) for part in _list(command)]
        for command in _list(case.get("targeted_tests"))
    ]
    observed_targeted = [
        [str(part) for part in _list(command)]
        for command in _list(contract.get("targeted_test_commands"))
    ]
    rewrites = [
        _dict(value)
        for value in _list(_dict(item.get("adaptation")).get("applied_command_rewrites"))
    ]
    remaining_rewrites = list(rewrites)
    if len(observed_targeted) != len(expected_targeted):
        errors.append("plan_targeted_command_count_mismatch")
    else:
        for expected, observed in zip(expected_targeted, observed_targeted):
            if observed == expected:
                continue
            rewrite_index = next(
                (
                    index
                    for index, rewrite in enumerate(remaining_rewrites)
                    if len(expected) >= 3
                    and len(observed) == len(expected)
                    and observed[:2] == expected[:2] == ["{python}", "-m"]
                    and observed[3:] == expected[3:]
                    and str(rewrite.get("from") or "") == expected[2]
                    and str(rewrite.get("to") or "") == observed[2]
                    and bool(str(rewrite.get("reason") or ""))
                ),
                None,
            )
            if rewrite_index is None:
                errors.append("plan_targeted_commands_mismatch")
            else:
                remaining_rewrites.pop(rewrite_index)
    if remaining_rewrites:
        errors.append("plan_command_rewrite_audit_mismatch")
    return errors


def _validate_plan_runtime_contract(
    case: dict[str, Any],
    *,
    item: dict[str, Any],
    evidence: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    case_id = str(case.get("case_id") or "")
    environment = _dict(case.get("environment"))
    expected_version = str(environment.get("python_version") or "")
    expected_requirements_sha256 = str(
        _dict(environment.get("requirements")).get("sha256") or ""
    )
    runtime = _dict(item.get("runtime"))
    probe = _dict(runtime.get("probe"))
    version = _dict(probe.get("version"))
    if runtime.get("status") != "available":
        errors.append("plan_runtime_must_be_available")
    if str(runtime.get("expected_version") or "") != expected_version:
        errors.append("plan_runtime_expected_version_mismatch")
    if probe.get("status") != "pass" or _list(probe.get("missing_modules")):
        errors.append("plan_runtime_probe_must_pass")
    if (
        version.get("status") != "pass"
        or version.get("exact_match") is not True
        or str(version.get("expected_version") or "") != expected_version
        or str(version.get("observed_version") or "") != expected_version
    ):
        errors.append("plan_runtime_probe_version_mismatch")
    planned_python = str(runtime.get("python_executable") or "")
    evidence_python = str(_dict(evidence.get("runtime")).get("python_executable") or "")
    if not planned_python or planned_python != evidence_python:
        errors.append("plan_runtime_python_mismatch")

    variant = _dict(item.get("runtime_variant"))
    if variant.get("status") != "pass":
        errors.append("plan_runtime_variant_must_pass")
    if str(variant.get("case_id") or "") != case_id:
        errors.append("plan_runtime_variant_case_id_mismatch")
    variant_id = str(variant.get("variant_id") or "")
    if not variant_id:
        errors.append("plan_runtime_variant_id_is_required")
    if variant_id != "project_default":
        if str(variant.get("requirements_sha256") or "") != (
            expected_requirements_sha256
        ):
            errors.append("plan_runtime_variant_requirements_sha256_mismatch")
        if variant.get("requirements_line_ending") not in {"crlf", "lf"}:
            errors.append("plan_runtime_variant_line_ending_is_invalid")
    return errors


def _validate_checkout(
    errors: list[str],
    name: str,
    checkout: dict[str, Any],
    expected_ref: str,
) -> None:
    if checkout.get("status") != "pass":
        errors.append(f"{name}_checkout_must_pass")
    if str(checkout.get("ref") or "") != expected_ref:
        errors.append(f"{name}_checkout_ref_mismatch")
    if checkout.get("checkout_method") != "archive":
        errors.append(f"{name}_checkout_must_use_archive")


def _validate_test_overlay(
    errors: list[str],
    overlay: dict[str, Any],
    expected_paths: list[str],
) -> None:
    if overlay.get("status") != "pass" or _list(overlay.get("errors")):
        errors.append("test_overlay_must_pass")
    files = [_dict(value) for value in _list(overlay.get("files"))]
    if sorted(str(value.get("path") or "") for value in files) != sorted(expected_paths):
        errors.append("test_overlay_paths_mismatch")
    for value in files:
        if not SHA256_PATTERN.fullmatch(str(value.get("sha256") or "")):
            errors.append("test_overlay_sha256_invalid")
        if _int(value.get("size_bytes"), 0) <= 0:
            errors.append("test_overlay_file_must_be_nonempty")


def _validate_preparation_files(
    errors: list[str],
    name: str,
    observed: dict[str, Any],
    expected: list[dict[str, Any]],
) -> None:
    if observed.get("status") != "pass" or _list(observed.get("errors")):
        errors.append(f"{name}_must_pass")
    if observed.get("repository_code_executed") is not False:
        errors.append(f"{name}_cannot_execute_repository_code")
    if _int(observed.get("requested_count"), -1) != len(expected):
        errors.append(f"{name}_requested_count_mismatch")
    if _int(observed.get("written_count"), -1) != len(expected):
        errors.append(f"{name}_written_count_mismatch")
    expected_by_path = {str(value.get("path") or ""): value for value in expected}
    observed_values = [_dict(value) for value in _list(observed.get("files"))]
    observed_by_path = {
        str(value.get("path") or ""): value for value in observed_values
    }
    if len(observed_by_path) != len(observed_values) or set(observed_by_path) != set(
        expected_by_path
    ):
        errors.append(f"{name}_paths_mismatch")
        return
    for path, expected_value in expected_by_path.items():
        observed_value = observed_by_path[path]
        if str(observed_value.get("sha256") or "") != str(
            expected_value.get("sha256") or ""
        ):
            errors.append(f"{name}_sha256_mismatch:{path}")
        expected_source_path = str(expected_value.get("source_path") or "")
        expected_source_sha256 = str(expected_value.get("source_text_sha256") or "")
        source = _dict(observed_value.get("source_assertion"))
        if expected_source_path and (
            source.get("status") != "pass"
            or str(source.get("path") or "") != expected_source_path
            or str(source.get("text_sha256") or "") != expected_source_sha256
        ):
            errors.append(f"{name}_source_assertion_mismatch:{path}")


def _validate_group(
    name: str,
    group: dict[str, Any],
    *,
    expected_status: str,
    expected_commands: list[list[str]],
    require_assertion_failure: bool = False,
) -> list[str]:
    errors: list[str] = []
    if group.get("status") != expected_status:
        errors.append(f"{name}_status_must_{expected_status}")
    if group.get("environment_blocker") is not False:
        errors.append(f"{name}_cannot_have_environment_blocker")
    results = [_dict(value) for value in _list(group.get("results"))]
    if len(results) != len(expected_commands):
        errors.append(f"{name}_command_count_mismatch")
    assertion_failure_seen = False
    for index, expected_command in enumerate(expected_commands):
        if index >= len(results):
            break
        result = results[index]
        if result.get("executed") is not True:
            errors.append(f"{name}_command_not_executed")
        observed_args = [str(value) for value in _list(result.get("command_args"))]
        if len(observed_args) < 2 or observed_args[1:] != expected_command[1:]:
            errors.append(f"{name}_command_args_mismatch")
        if _int(result.get("test_count"), 0) <= 0:
            errors.append(f"{name}_zero_tests")
        if expected_status == "pass" and (
            result.get("status") != "pass" or _int(result.get("returncode"), -1) != 0
        ):
            errors.append(f"{name}_command_must_pass")
        if (
            result.get("status") == "fail"
            and result.get("failure_category") == "test_assertion_failure"
            and _int(result.get("returncode"), 0) != 0
            and (
                _int(result.get("failed"), 0) > 0
                or _int(result.get("errors"), 0) > 0
            )
        ):
            assertion_failure_seen = True
    if require_assertion_failure and not assertion_failure_seen:
        errors.append(f"{name}_requires_assertion_failure")
    return errors


def _summarize_reproduction(
    evidence: dict[str, Any],
    *,
    plan: dict[str, Any],
    attestation: dict[str, Any],
    archive: dict[str, Any],
) -> dict[str, Any]:
    runtime = _dict(evidence.get("runtime"))
    artifact = _dict(attestation.get("artifact"))
    workflow = _dict(attestation.get("workflow_run"))
    reproduction = _dict(attestation.get("reproduction"))
    return {
        "status": "pass",
        "evidence_status": "validated",
        "evidence_artifact": str(reproduction.get("evidence_reference") or ""),
        "evidence_sha256": str(evidence.get("evidence_sha256") or ""),
        "evidence_file_sha256": str(archive.get("evidence_file_sha256") or ""),
        "completed_at": str(evidence.get("completed_at") or ""),
        "runtime": {
            "status": str(runtime.get("status") or ""),
            "expected_version": str(runtime.get("expected_version") or ""),
            "observed_version": str(runtime.get("observed_version") or ""),
            "exact_match": runtime.get("exact_match") is True,
        },
        "bug_targeted": _summarize_group(_dict(evidence.get("bug_targeted"))),
        "fix_targeted": _summarize_group(_dict(evidence.get("fix_targeted"))),
        "fix_full_regression": _summarize_group(
            _dict(evidence.get("fix_full_regression"))
        ),
        "acceptance": {
            gate: _dict(evidence.get("acceptance")).get(gate) is expected
            for gate, expected in ACCEPTANCE_GATES.items()
        },
        "plan": {
            "plan_sha256": str(plan.get("plan_sha256") or ""),
            "profiles_sha256": str(plan.get("profiles_sha256") or ""),
        },
        "workflow": {
            "run_id": _int(workflow.get("run_id"), 0),
            "job_id": _int(workflow.get("job_id"), 0),
            "head_sha": str(workflow.get("head_sha") or ""),
            "url": str(workflow.get("url") or ""),
            "conclusion": str(workflow.get("conclusion") or ""),
        },
        "artifact": {
            "artifact_id": _int(artifact.get("artifact_id"), 0),
            "name": str(artifact.get("name") or ""),
            "sha256": str(archive.get("artifact_sha256") or ""),
            "size_bytes": _int(archive.get("artifact_size_bytes"), 0),
            "plan_file_sha256": str(archive.get("plan_file_sha256") or ""),
        },
        "verification_artifact": str(attestation.get("attestation_reference") or ""),
        "test_overlay_policy": "copy_declared_tests_from_fix_to_bug",
        "gold_patch_visible_to_execution": False,
        "repository_setup_script_executed": False,
        "repository_project_installed": False,
        "model_calls": 0,
        "raw_artifact_committed": False,
        "source": "v4_linux_ci_reproduction",
    }


def _summarize_group(group: dict[str, Any]) -> dict[str, Any]:
    results = [_dict(value) for value in _list(group.get("results"))]
    return {
        "status": str(group.get("status") or ""),
        "command_count": len(results),
        "test_count": sum(_int(value.get("test_count"), 0) for value in results),
        "passed": sum(_int(value.get("passed"), 0) for value in results),
        "failed": sum(_int(value.get("failed"), 0) for value in results),
        "errors": sum(_int(value.get("errors"), 0) for value in results),
        "skipped": sum(_int(value.get("skipped"), 0) for value in results),
        "environment_blocker": bool(group.get("environment_blocker", False)),
    }


def _acceptance_audit(
    *,
    status: str,
    case_id: str,
    errors: list[str],
    before_summary: dict[str, Any],
    after_summary: dict[str, Any],
    before_manifest: str,
    after_manifest: str,
    attestation: dict[str, Any],
    archive: dict[str, Any],
) -> dict[str, Any]:
    workflow = _dict(attestation.get("workflow_run"))
    artifact = _dict(attestation.get("artifact"))
    return {
        "schema_version": SCHEMA_VERSION,
        "audit_id": f"v4-reproduction-acceptance:{case_id}",
        "status": status,
        "case_id": case_id,
        "error_count": len(errors),
        "errors": errors,
        "before_summary": copy.deepcopy(before_summary),
        "after_summary": copy.deepcopy(after_summary),
        "before_manifest_sha256": before_manifest,
        "after_manifest_sha256": after_manifest,
        "workflow": {
            "run_id": _int(workflow.get("run_id"), 0),
            "head_sha": str(workflow.get("head_sha") or ""),
            "conclusion": str(workflow.get("conclusion") or ""),
        },
        "artifact": {
            "artifact_id": _int(artifact.get("artifact_id"), 0),
            "expected_sha256": str(artifact.get("sha256") or ""),
            "observed_sha256": str(archive.get("artifact_sha256") or ""),
            "expected_size_bytes": _int(artifact.get("size_bytes"), 0),
            "observed_size_bytes": _int(archive.get("artifact_size_bytes"), 0),
        },
        "raw_artifact_committed": False,
        "gold_patch_visible_to_execution": False,
    }


def _batch_attestation_identity(attestation: dict[str, Any]) -> dict[str, Any]:
    workflow = _dict(attestation.get("workflow_run"))
    artifact = _dict(attestation.get("artifact"))
    return {
        "workflow_run": {
            "run_id": _int(workflow.get("run_id"), 0),
            "job_id": _int(workflow.get("job_id"), 0),
            "head_sha": str(workflow.get("head_sha") or ""),
            "conclusion": str(workflow.get("conclusion") or ""),
        },
        "artifact": {
            "artifact_id": _int(artifact.get("artifact_id"), 0),
            "name": str(artifact.get("name") or ""),
            "size_bytes": _int(artifact.get("size_bytes"), -1),
            "sha256": str(artifact.get("sha256") or ""),
            "plan_member": str(artifact.get("plan_member") or ""),
            "plan_file_sha256": str(artifact.get("plan_file_sha256") or ""),
        },
    }


def _batch_acceptance_audit(
    *,
    status: str,
    case_ids: list[str],
    errors: list[str],
    before_summary: dict[str, Any],
    after_summary: dict[str, Any],
    before_manifest: str,
    after_manifest: str,
    attestations: list[dict[str, Any]],
    individual_audits: list[dict[str, Any]],
) -> dict[str, Any]:
    identity = _batch_attestation_identity(attestations[0]) if attestations else {}
    identity_hash = hashlib.sha256(
        json.dumps(case_ids, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "schema_version": SCHEMA_VERSION,
        "audit_id": f"v4-reproduction-batch-acceptance:{identity_hash}",
        "status": status,
        "case_ids": case_ids,
        "case_count": len(case_ids),
        "accepted_case_count": len(case_ids) if status == "pass" else 0,
        "error_count": len(errors),
        "errors": errors,
        "before_summary": copy.deepcopy(before_summary),
        "after_summary": copy.deepcopy(after_summary),
        "before_manifest_sha256": before_manifest,
        "after_manifest_sha256": after_manifest,
        "shared_identity": identity,
        "individual_audits": copy.deepcopy(individual_audits),
        "atomic_all_or_nothing": True,
        "raw_artifact_committed": False,
        "gold_patch_visible_to_execution": False,
    }


def _find_case(catalog: dict[str, Any], case_id: str) -> dict[str, Any] | None:
    matches = [
        _dict(value)
        for value in _list(catalog.get("cases"))
        if str(_dict(value).get("case_id") or "") == case_id
    ]
    return matches[0] if len(matches) == 1 else None


def _safe_artifact_member(value: str) -> bool:
    if not value or "\\" in value or "\x00" in value:
        return False
    if re.match(r"^[A-Za-z]:", value):
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts


def _safe_relative_reference(value: str) -> bool:
    return _safe_artifact_member(value) and not value.endswith("/")


def _valid_iso_datetime(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_lf_atomic(path: Path, value: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
            temporary_path = Path(stream.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
