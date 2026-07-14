from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from code_intelligence_agent.evaluation.v3_real_bug_benchmark import (
    SCHEMA_VERSION,
    catalog_sha256,
    load_json_object,
    render_real_bug_catalog_markdown,
    validate_real_bug_catalog,
    write_real_bug_catalog_artifacts,
)


EVIDENCE_FILENAME = "reproduction.json"
ACCEPTANCE_GATES = {
    "bug_targeted_failed": True,
    "fix_targeted_passed": True,
    "fix_full_regression_passed": True,
    "reproducible": True,
}


def aggregate_reproduction_evidence(
    catalog: dict[str, Any],
    evidence_root: str | Path,
    *,
    evidence_reference_root: str = "outputs_v3/reproduction",
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not _safe_relative_reference(evidence_reference_root):
        raise ValueError("evidence_reference_root_must_be_repository_relative")
    result = copy.deepcopy(catalog)
    root = Path(evidence_root)
    evidence_errors: list[dict[str, Any]] = []
    accepted_count = 0
    rejected_count = 0
    unresolved_count = 0
    completion_times: list[str] = []

    for case_value in _list(result.get("cases")):
        case = _dict(case_value)
        case_id = str(case.get("case_id") or "")
        if case.get("status") == "rejected":
            rejected_count += 1
            continue
        artifact_path = root / case_id / EVIDENCE_FILENAME
        artifact_reference = (
            PurePosixPath(evidence_reference_root) / case_id / EVIDENCE_FILENAME
        ).as_posix()
        evidence, load_errors = _load_evidence(artifact_path)
        errors = load_errors + validate_reproduction_evidence(case, evidence)
        if errors:
            unresolved_count += 1
            case["status"] = "candidate"
            case["reproduction"] = {
                **_dict(case.get("reproduction")),
                "evidence_status": "invalid" if artifact_path.is_file() else "missing",
                "evidence_artifact": artifact_reference,
                "evidence_error_codes": errors,
            }
            evidence_errors.append({"case_id": case_id, "errors": errors})
            continue
        case["status"] = "accepted"
        case["reproduction"] = summarize_reproduction_evidence(
            evidence,
            artifact_reference=artifact_reference,
            artifact_sha256=_sha256_file(artifact_path),
        )
        accepted_count += 1
        completion_times.append(str(evidence.get("completed_at") or ""))

    aggregation_status = "pass" if unresolved_count == 0 else "fail"
    result["evidence_aggregation"] = {
        "schema_version": SCHEMA_VERSION,
        "status": aggregation_status,
        "evidence_reference_root": PurePosixPath(evidence_reference_root).as_posix(),
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "unresolved_count": unresolved_count,
        "raw_artifacts_committed": False,
        "gold_patch_visible_to_execution": False,
    }
    if completion_times:
        result["evidence_aggregated_at"] = max(completion_times)
    result["catalog_sha256"] = catalog_sha256(result)
    audit = {
        "schema_version": SCHEMA_VERSION,
        "status": aggregation_status,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "unresolved_count": unresolved_count,
        "error_count": len(evidence_errors),
        "errors": evidence_errors,
        "catalog_sha256": result["catalog_sha256"],
    }
    return result, audit


def validate_reproduction_evidence(
    case: dict[str, Any],
    evidence: dict[str, Any],
) -> list[str]:
    if not evidence:
        return []
    errors: list[str] = []
    case_id = str(case.get("case_id") or "")
    if str(evidence.get("schema_version") or "") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    if str(evidence.get("case_id") or "") != case_id:
        errors.append("case_id_mismatch")
    if evidence.get("status") != "pass":
        errors.append("reproduction_status_must_pass")
    if evidence.get("reason") != "real_bug_reproduced":
        errors.append("reproduction_reason_mismatch")
    if evidence.get("gold_patch_visible_to_execution") is not False:
        errors.append("gold_patch_visibility_must_be_false")
    if _dict(evidence.get("blocker")):
        errors.append("accepted_evidence_cannot_have_blocker")

    runtime = _dict(evidence.get("runtime"))
    expected_version = str(case.get("python_version") or "")
    if runtime.get("status") != "pass" or runtime.get("exact_match") is not True:
        errors.append("runtime_exact_match_required")
    if str(runtime.get("expected_version") or "") != expected_version:
        errors.append("runtime_expected_version_mismatch")
    if str(runtime.get("observed_version") or "") != expected_version:
        errors.append("runtime_observed_version_mismatch")

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
    _validate_overlay(
        errors,
        _dict(preparation.get("test_overlay")),
        [str(item) for item in _list(case.get("test_overlay_paths"))],
    )
    if _list(case.get("preparation_files")):
        for name in ("bug_preparation_files", "fix_preparation_files"):
            prepared = _dict(preparation.get(name))
            if prepared.get("status") != "pass":
                errors.append(f"{name}_must_pass")
            if prepared.get("repository_code_executed") is not False:
                errors.append(f"{name}_cannot_execute_repository_code")

    expected_targeted = [
        [str(part) for part in _list(command)]
        for command in _list(case.get("targeted_test_commands"))
    ]
    expected_regression = [
        [str(part) for part in _list(case.get("regression_command"))]
    ]
    errors.extend(
        _validate_group(
            "bug_targeted",
            _dict(evidence.get("bug_targeted")),
            expected_status="fail",
            expected_commands=expected_targeted,
            require_assertion_failure=True,
        )
    )
    errors.extend(
        _validate_group(
            "fix_targeted",
            _dict(evidence.get("fix_targeted")),
            expected_status="pass",
            expected_commands=expected_targeted,
        )
    )
    errors.extend(
        _validate_group(
            "fix_full_regression",
            _dict(evidence.get("fix_full_regression")),
            expected_status="pass",
            expected_commands=expected_regression,
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


def summarize_reproduction_evidence(
    evidence: dict[str, Any],
    *,
    artifact_reference: str,
    artifact_sha256: str,
) -> dict[str, Any]:
    runtime = _dict(evidence.get("runtime"))
    return {
        "evidence_status": "validated",
        "evidence_artifact": artifact_reference,
        "evidence_sha256": artifact_sha256,
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
        "test_overlay_policy": "copy_listed_test_files_from_fix_commit_to_bug_commit",
        "gold_patch_visible_to_execution": False,
        "raw_artifact_committed": False,
    }


def render_evidence_audit_markdown(
    catalog: dict[str, Any],
    evidence_audit: dict[str, Any],
    catalog_audit: dict[str, Any],
) -> str:
    return "\n".join(
        [
            render_real_bug_catalog_markdown(catalog, catalog_audit).rstrip(),
            "",
            "## Reproduction Evidence",
            "",
            f"- Evidence status: `{evidence_audit.get('status')}`",
            f"- Accepted: `{evidence_audit.get('accepted_count')}`",
            f"- Rejected: `{evidence_audit.get('rejected_count')}`",
            f"- Unresolved: `{evidence_audit.get('unresolved_count')}`",
            "- Raw checkout and execution logs remain ignored; committed cases contain only checksummed summaries.",
            "",
        ]
    )


def _load_evidence(path: Path) -> tuple[dict[str, Any], list[str]]:
    if not path.is_file():
        return {}, ["reproduction_artifact_missing"]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, ["reproduction_artifact_unreadable"]
    if not isinstance(value, dict):
        return {}, ["reproduction_artifact_must_be_object"]
    return value, []


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


def _validate_overlay(
    errors: list[str],
    overlay: dict[str, Any],
    expected_paths: list[str],
) -> None:
    if overlay.get("status") != "pass":
        errors.append("test_overlay_must_pass")
    files = [_dict(item) for item in _list(overlay.get("files"))]
    observed_paths = sorted(str(item.get("path") or "") for item in files)
    if observed_paths != sorted(expected_paths):
        errors.append("test_overlay_paths_mismatch")
    for item in files:
        if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256") or "")):
            errors.append("test_overlay_sha256_invalid")
        if _int(item.get("size_bytes")) <= 0:
            errors.append("test_overlay_file_must_be_nonempty")


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
    results = [_dict(item) for item in _list(group.get("results"))]
    if len(results) != len(expected_commands):
        errors.append(f"{name}_command_count_mismatch")
    assertion_failure_seen = False
    for index, expected_command in enumerate(expected_commands):
        if index >= len(results):
            break
        result = results[index]
        if result.get("executed") is not True:
            errors.append(f"{name}_command_not_executed")
        observed_args = [str(item) for item in _list(result.get("command_args"))]
        if len(observed_args) < 2 or observed_args[1:] != expected_command[1:]:
            errors.append(f"{name}_command_args_mismatch")
        if _int(result.get("test_count")) <= 0:
            errors.append(f"{name}_zero_tests")
        if expected_status == "pass":
            if result.get("status") != "pass" or _int(result.get("returncode"), -1) != 0:
                errors.append(f"{name}_command_must_pass")
        elif (
            result.get("status") == "fail"
            and result.get("failure_category") == "test_assertion_failure"
        ):
            assertion_failure_seen = True
    if require_assertion_failure and not assertion_failure_seen:
        errors.append(f"{name}_requires_assertion_failure")
    return errors


def _summarize_group(group: dict[str, Any]) -> dict[str, Any]:
    results = [_dict(item) for item in _list(group.get("results"))]
    return {
        "status": str(group.get("status") or ""),
        "command_count": len(results),
        "test_count": sum(_int(item.get("test_count")) for item in results),
        "passed": sum(_int(item.get("passed")) for item in results),
        "failed": sum(_int(item.get("failed")) for item in results),
        "errors": sum(_int(item.get("errors")) for item in results),
        "skipped": sum(_int(item.get("skipped")) for item in results),
        "environment_blocker": bool(group.get("environment_blocker", False)),
    }


def _safe_relative_reference(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if not normalized or normalized.startswith("//") or re.match(r"^[A-Za-z]:", normalized):
        return False
    path = PurePosixPath(normalized)
    return not path.is_absolute() and ".." not in path.parts


def _valid_iso_datetime(value: str) -> bool:
    if not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate ignored V3 reproduction artifacts and accept proven cases."
    )
    parser.add_argument("catalog", help="Candidate real-bug catalog JSON.")
    parser.add_argument("evidence_root", help="Ignored reproduction artifact root.")
    parser.add_argument("output_prefix", help="Output prefix for accepted catalog artifacts.")
    parser.add_argument(
        "--evidence-reference-root",
        default="outputs_v3/reproduction",
        help="Repository-relative reference recorded in committed summaries.",
    )
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--require-pass", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    catalog = load_json_object(args.catalog)
    accepted_catalog, evidence_audit = aggregate_reproduction_evidence(
        catalog,
        args.evidence_root,
        evidence_reference_root=args.evidence_reference_root,
    )
    catalog_audit = validate_real_bug_catalog(
        accepted_catalog,
        require_complete=args.require_complete,
    )
    paths = write_real_bug_catalog_artifacts(
        accepted_catalog,
        args.output_prefix,
        require_complete=args.require_complete,
    )
    prefix = Path(args.output_prefix)
    evidence_audit_path = prefix.with_name(prefix.name + "_evidence_audit").with_suffix(
        ".json"
    )
    evidence_audit_path.write_text(
        json.dumps(evidence_audit, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown = render_evidence_audit_markdown(
        accepted_catalog,
        evidence_audit,
        catalog_audit,
    )
    Path(paths["catalog_markdown"]).write_text(markdown, encoding="utf-8")
    print(markdown)
    if args.require_pass and (
        evidence_audit["status"] != "pass" or catalog_audit["status"] != "pass"
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
