from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from code_intelligence_agent.evaluation.v3_repair_evaluation import (
    audit_v3_reproduction_seed,
    resolve_v3_case_runtime,
)
from code_intelligence_agent.evaluation.v3_repair_trial import EditableRegion
from code_intelligence_agent.evaluation.v3_semantic_validation import (
    SCHEMA_VERSION as SEMANTIC_SCHEMA_VERSION,
    validate_v3_semantic_candidate,
)


SCHEMA_VERSION = "v3_semantic_calibration_v1"


def run_v3_semantic_calibration(
    *,
    project_root: str | Path,
    catalog_path: str | Path,
    environment_profiles_path: str | Path,
    reproduction_root: str | Path,
    output_dir: str | Path,
    case_ids: list[str],
) -> dict[str, Any]:
    """Calibrate semantic gates on human fixes without making Agent repair claims."""
    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    catalog = _read_json(catalog_path)
    profiles = _read_json(environment_profiles_path)
    profile_by_id = {
        str(_dict(value).get("profile_id") or ""): _dict(value)
        for value in _list(profiles.get("profiles"))
    }
    requested = {str(value) for value in case_ids if str(value)}
    cases = [
        _dict(value)
        for value in _list(catalog.get("cases"))
        if str(_dict(value).get("status") or "") == "accepted"
        and str(_dict(value).get("case_id") or "") in requested
    ]
    cases.sort(key=lambda value: str(value.get("case_id") or ""))
    missing = sorted(
        requested.difference(str(case.get("case_id") or "") for case in cases)
    )
    results = []
    for case in cases:
        results.append(
            _calibrate_case(
                root=root,
                case=case,
                profile_by_id=profile_by_id,
                reproduction_root=Path(reproduction_root).resolve(),
            )
        )
    pass_count = sum(result.get("status") == "pass" for result in results)
    fail_count = sum(result.get("status") == "fail" for result in results)
    blocker_count = sum(result.get("status") == "blocker" for result in results)
    mutation_checks = [
        check
        for result in results
        for check in _list(_dict(result.get("semantic_validation")).get("checks"))
        if str(_dict(check).get("check_id") or "")
        == "reverse_mutation_sensitivity"
    ]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "semantic_schema_version": SEMANTIC_SCHEMA_VERSION,
        "status": (
            "pass"
            if results and pass_count == len(results) and not missing
            else "warning"
        ),
        "case_count": len(results),
        "requested_case_ids": sorted(requested),
        "missing_case_ids": missing,
        "pass_count": pass_count,
        "false_rejection_count": fail_count,
        "blocker_count": blocker_count,
        "reverse_mutation_count": sum(
            _int(_dict(check).get("mutation_count"), 0)
            for check in mutation_checks
        ),
        "reverse_mutation_killed_count": sum(
            _int(_dict(check).get("killed_mutation_count"), 0)
            for check in mutation_checks
        ),
        "reverse_mutation_surviving_count": sum(
            _int(_dict(check).get("surviving_mutation_count"), 0)
            for check in mutation_checks
        ),
        "cases": results,
        "claim_boundary": (
            "Human fix content is used only to calibrate post-generation semantic "
            "validation. These results are not Agent-generated repairs, pass@k, "
            "or evidence available to localization, planning, or an LLM."
        ),
    }
    write_v3_semantic_calibration_artifacts(payload, output)
    return payload


def _calibrate_case(
    *,
    root: Path,
    case: dict[str, Any],
    profile_by_id: dict[str, dict[str, Any]],
    reproduction_root: Path,
) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "")
    reproduction_dir = reproduction_root / case_id
    seed_audit = audit_v3_reproduction_seed(
        case,
        reproduction_dir=reproduction_dir,
    )
    reproduction_path = reproduction_dir / "reproduction.json"
    reproduction = (
        _read_json(reproduction_path) if reproduction_path.is_file() else {}
    )
    acceptance = _dict(reproduction.get("acceptance"))
    preparation = _dict(reproduction.get("preparation"))
    fix_checkout = _dict(preparation.get("fix_checkout"))
    bug_root = reproduction_dir / "bug" / "repository_checkout"
    fix_root = reproduction_dir / "fix" / "repository_checkout"
    runtime = resolve_v3_case_runtime(
        root,
        case,
        profile_by_id=profile_by_id,
    )
    blockers = []
    if seed_audit.get("status") != "pass":
        blockers.extend(str(value) for value in _list(seed_audit.get("errors")))
    if str(fix_checkout.get("ref") or "") != str(case.get("fix_commit_sha") or ""):
        blockers.append("fix_checkout_commit_mismatch")
    if not fix_root.is_dir() or fix_root.is_symlink():
        blockers.append("fix_checkout_missing_or_unsafe")
    for field in (
        "bug_targeted_failed",
        "fix_targeted_passed",
        "fix_full_regression_passed",
    ):
        if acceptance.get(field) is not True:
            blockers.append(f"reproduction_acceptance_missing:{field}")
    if runtime.get("status") != "pass":
        blockers.append(str(runtime.get("reason") or "runtime_unavailable"))
    candidate, regions, source_audit = _human_fix_module_candidate(
        case,
        bug_root=bug_root,
        fix_root=fix_root,
    )
    blockers.extend(source_audit.get("errors", []))
    if blockers:
        return {
            "case_id": case_id,
            "repository": str(_dict(case.get("repository")).get("owner_repo") or ""),
            "status": "blocker",
            "blockers": sorted(set(blockers)),
            "source_file_count": len(regions),
            "source_audit": source_audit,
            "semantic_validation": {},
            "human_fix_oracle_used": True,
            "agent_repair_claim": False,
        }
    semantic = validate_v3_semantic_candidate(
        candidate,
        editable_regions=regions,
        seed_repository=bug_root,
        patched_repository=fix_root,
        case=case,
        python_executable=str(runtime.get("python_executable") or ""),
        targeted_timeout=180,
        regression_timeout=900,
    )
    semantic_status = str(semantic.get("status") or "blocker")
    return {
        "case_id": case_id,
        "repository": str(_dict(case.get("repository")).get("owner_repo") or ""),
        "benchmark_split": str(case.get("benchmark_split") or ""),
        "status": (
            "pass"
            if semantic_status == "pass"
            else "fail"
            if semantic_status == "fail"
            else "blocker"
        ),
        "semantic_status": semantic_status,
        "source_file_count": len(regions),
        "runtime_profile_id": str(runtime.get("profile_id") or ""),
        "expected_python_version": str(runtime.get("expected_python_version") or ""),
        "source_audit": source_audit,
        "semantic_validation": semantic,
        "human_fix_oracle_used": True,
        "gold_patch_visible_to_model": False,
        "agent_repair_claim": False,
    }


def _human_fix_module_candidate(
    case: dict[str, Any],
    *,
    bug_root: Path,
    fix_root: Path,
) -> tuple[dict[str, Any], list[EditableRegion], dict[str, Any]]:
    source_files = sorted(
        {
            _normalize_relative_path(str(value))
            for value in _list(_dict(case.get("ground_truth")).get("source_files"))
            if _normalize_relative_path(str(value))
        }
    )
    declared_test_files = {
        _normalize_relative_path(str(value))
        for value in _list(_dict(case.get("ground_truth")).get("test_files"))
        if _normalize_relative_path(str(value))
    }
    regions = []
    edits = []
    errors = []
    files = []
    for relative in source_files:
        if relative in declared_test_files:
            errors.append(f"declared_test_source_not_allowed:{relative}")
            continue
        if not _is_production_python_source(relative):
            errors.append(f"non_production_python_source_not_allowed:{relative}")
            continue
        bug_path = _safe_file(bug_root, relative)
        fix_path = _safe_file(fix_root, relative)
        if (
            bug_path is None
            or fix_path is None
            or not bug_path.is_file()
            or not fix_path.is_file()
            or bug_path.is_symlink()
            or fix_path.is_symlink()
        ):
            errors.append(f"source_file_missing_or_unsafe:{relative}")
            continue
        try:
            old = _normalize_newlines(
                bug_path.read_text(encoding="utf-8")
            ).rstrip("\n")
            new = _normalize_newlines(
                fix_path.read_text(encoding="utf-8")
            ).rstrip("\n")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"source_file_unreadable:{relative}:{type(exc).__name__}")
            continue
        if old == new:
            errors.append(f"source_file_has_no_fix_delta:{relative}")
            continue
        original_sha = _sha256_text(old)
        region = EditableRegion(
            path=relative,
            function_id=f"{relative}::<module>",
            function_name="<module>",
            start_line=1,
            end_line=max(1, len(old.splitlines())),
            rank=1,
            score=1.0,
            original_sha256=original_sha,
            source=old,
            selection_reason="human_fix_semantic_calibration_only",
            region_kind="module",
        )
        regions.append(region)
        edits.append(
            {
                "path": relative,
                "original_sha256": original_sha,
                "replacement": new,
                "function_id": region.function_id,
                "function_name": region.function_name,
                "start_line": region.start_line,
                "end_line": region.end_line,
                "region_kind": "module",
            }
        )
        files.append(
            {
                "path": relative,
                "bug_source_sha256": _sha256_text(old),
                "fix_source_sha256": _sha256_text(new),
                "changed_line_count": _changed_line_count(old, new),
            }
        )
    return (
        {
            "files": edits,
            "risk": "human_fix_calibration",
            "allow_signature_change": False,
            "semantic_rule_ids": [],
        },
        regions,
        {
            "status": "pass" if regions and not errors else "fail",
            "errors": errors,
            "files": files,
            "ground_truth_source_files_used": True,
        },
    )


def write_v3_semantic_calibration_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "v3_semantic_calibration.json"
    markdown_path = output / "v3_semantic_calibration.md"
    _write_text(json_path, json.dumps(payload, indent=2, ensure_ascii=False))
    _write_text(markdown_path, render_v3_semantic_calibration_markdown(payload))
    return {"json": json_path.as_posix(), "markdown": markdown_path.as_posix()}


def write_v3_semantic_calibration_release(
    payload: dict[str, Any],
    docs_dir: str | Path,
) -> dict[str, str]:
    docs = Path(docs_dir)
    docs.mkdir(parents=True, exist_ok=True)
    json_path = docs / "phase5_semantic_calibration.json"
    markdown_path = docs / "phase5_semantic_calibration.md"
    _write_text(json_path, json.dumps(payload, indent=2, ensure_ascii=False))
    _write_text(markdown_path, render_v3_semantic_calibration_markdown(payload))
    return {"json": json_path.as_posix(), "markdown": markdown_path.as_posix()}


def render_v3_semantic_calibration_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# V3 Phase 5 Semantic Validation Calibration",
        "",
        f"- Status: `{payload.get('status', '')}`",
        f"- Human-fix cases: `{payload.get('case_count', 0)}`",
        f"- Semantic passes: `{payload.get('pass_count', 0)}`",
        f"- False rejections: `{payload.get('false_rejection_count', 0)}`",
        f"- Blockers: `{payload.get('blocker_count', 0)}`",
        f"- Reverse mutations killed: `{payload.get('reverse_mutation_killed_count', 0)}/{payload.get('reverse_mutation_count', 0)}`",
        "",
        "## Case Results",
        "",
        "| Case | Repository | Source files | Semantic | Result |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for value in _list(payload.get("cases")):
        case = _dict(value)
        lines.append(
            f"| `{case.get('case_id', '')}` | {case.get('repository', '')} | "
            f"{case.get('source_file_count', 0)} | "
            f"`{case.get('semantic_status', 'not_run')}` | "
            f"`{case.get('status', '')}` |"
        )
    lines.extend(
        [
            "",
            "## Claim Boundary",
            "",
            str(payload.get("claim_boundary") or ""),
            "",
            "The fix-side source is an oracle used only after generation to measure "
            "validator false rejection. It is never model context and these cases "
            "do not count as Agent repair successes.",
        ]
    )
    return "\n".join(lines) + "\n"


def _changed_line_count(old: str, new: str) -> int:
    import difflib

    matcher = difflib.SequenceMatcher(
        a=old.splitlines(),
        b=new.splitlines(),
        autojunk=False,
    )
    return sum(
        max(old_end - old_start, new_end - new_start)
        for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes()
        if tag != "equal"
    )


def _safe_file(root: Path, relative_path: str) -> Path | None:
    normalized = _normalize_relative_path(relative_path)
    relative = PurePosixPath(normalized)
    if (
        not relative.parts
        or "\x00" in normalized
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.parts[0].endswith(":")
    ):
        return None
    root_resolved = root.resolve()
    target = root_resolved / Path(*relative.parts)
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return None
    current = target
    while current != root_resolved:
        if current.is_symlink():
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return target


def _normalize_relative_path(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").strip()
    return PurePosixPath(normalized).as_posix() if normalized else ""


def _is_production_python_source(relative_path: str) -> bool:
    path = PurePosixPath(_normalize_relative_path(relative_path))
    if not path.parts or path.suffix.lower() != ".py":
        return False
    lower_parts = tuple(part.lower() for part in path.parts)
    name = path.name.lower()
    return not (
        "tests" in lower_parts
        or "test" in lower_parts
        or name == "conftest.py"
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _write_text(path: str | Path, value: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(value)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_arg_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Calibrate V3 semantic gates on isolated human-fix oracles."
    )
    parser.add_argument("output_dir")
    parser.add_argument(
        "--catalog",
        default=str(root / "docs" / "v3" / "phase1_real_bug_catalog.json"),
    )
    parser.add_argument(
        "--environment-profiles",
        default=str(
            root
            / "datasets"
            / "v3_real_bugs"
            / "environment_profile_sources.json"
        ),
    )
    parser.add_argument(
        "--reproduction-root",
        default=str(root / "outputs_v3" / "reproduction"),
    )
    parser.add_argument("--case-id", action="append", required=True)
    parser.add_argument("--release-docs-dir", default="")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--require-pass", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    payload = run_v3_semantic_calibration(
        project_root=root,
        catalog_path=args.catalog,
        environment_profiles_path=args.environment_profiles,
        reproduction_root=args.reproduction_root,
        output_dir=args.output_dir,
        case_ids=args.case_id,
    )
    if args.release_docs_dir:
        write_v3_semantic_calibration_release(payload, args.release_docs_dir)
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_v3_semantic_calibration_markdown(payload), end="")
    if args.require_pass and payload.get("status") != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
