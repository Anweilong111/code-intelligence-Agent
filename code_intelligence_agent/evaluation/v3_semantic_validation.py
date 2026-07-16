from __future__ import annotations

import ast
import difflib
import gc
import shutil
import tempfile
import time
import warnings
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from code_intelligence_agent.core.models import PatchCandidate
from code_intelligence_agent.evaluation.v3_real_bug_reproduction import (
    execute_test_commands,
)
from code_intelligence_agent.evaluation.v3_repair_trial import EditableRegion
from code_intelligence_agent.tools.boundary_probe import run_boundary_probe
from code_intelligence_agent.tools.diff_utils import render_unified_diff
from code_intelligence_agent.tools.semantic_patch_validation import (
    validate_semantic_module_patch,
    validate_semantic_patch,
)


SCHEMA_VERSION = "v3_semantic_validation_v1"
SUPPORTED_SEMANTIC_TEST_MODULES = frozenset({"pytest", "unittest"})
_TEMPORARY_CLEANUP_DELAYS = (0.0, 0.05, 0.2)


def validate_v3_semantic_candidate(
    candidate: dict[str, Any],
    *,
    editable_regions: list[EditableRegion],
    seed_repository: str | Path,
    patched_repository: str | Path,
    case: dict[str, Any],
    python_executable: str | Path,
    targeted_timeout: int,
    regression_timeout: int,
    patched_target_execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run post-regression semantic gates without consulting the gold patch."""
    seed = Path(seed_repository).resolve()
    patched = Path(patched_repository).resolve()
    checks = [
        _api_contract_check(candidate, seed=seed, patched=patched),
        _static_semantic_check(candidate, editable_regions=editable_regions),
        _workspace_consistency_check(
            candidate,
            editable_regions=editable_regions,
            seed=seed,
            patched=patched,
        ),
        _minimality_check(candidate, editable_regions=editable_regions),
        _boundary_property_check(
            candidate,
            editable_regions=editable_regions,
            python_executable=python_executable,
        ),
        _target_differential_check(
            case,
            seed=seed,
            patched=patched,
            python_executable=python_executable,
            timeout=targeted_timeout,
            patched_execution=patched_target_execution,
        ),
        _reverse_mutation_check(
            candidate,
            editable_regions=editable_regions,
            patched=patched,
            case=case,
            python_executable=python_executable,
            targeted_timeout=targeted_timeout,
            regression_timeout=regression_timeout,
        ),
        _configured_semantic_commands_check(
            case,
            patched=patched,
            python_executable=python_executable,
            timeout=targeted_timeout,
        ),
    ]
    required = [check for check in checks if check.get("required") is True]
    failures = [check for check in required if check.get("status") == "fail"]
    blockers = [check for check in required if check.get("status") == "blocker"]
    incomplete = [
        check
        for check in required
        if check.get("status") in {"not_applicable", "not_run"}
    ]
    if failures:
        status = "fail"
        reason = str(failures[0].get("reason") or failures[0].get("check_id"))
    elif blockers:
        status = "blocker"
        reason = str(blockers[0].get("reason") or blockers[0].get("check_id"))
    elif incomplete:
        status = "not_applicable"
        reason = "required_semantic_oracle_incomplete"
    else:
        status = "pass"
        reason = "all_required_semantic_gates_passed"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "claim_eligible": status == "pass",
        "required_check_count": len(required),
        "passed_required_check_count": sum(
            check.get("status") == "pass" for check in required
        ),
        "failed_check_ids": [
            str(check.get("check_id") or "") for check in failures
        ],
        "blocker_check_ids": [
            str(check.get("check_id") or "") for check in blockers
        ],
        "incomplete_check_ids": [
            str(check.get("check_id") or "") for check in incomplete
        ],
        "checks": checks,
        "gold_patch_used": False,
        "fix_commit_content_used": False,
        "llm_judge_authoritative": False,
    }


def _api_contract_check(
    candidate: dict[str, Any],
    *,
    seed: Path,
    patched: Path,
) -> dict[str, Any]:
    changed_paths = sorted(
        {
            _normalize_relative_path(str(_dict(value).get("path") or ""))
            for value in _list(candidate.get("files"))
            if _normalize_relative_path(str(_dict(value).get("path") or ""))
        }
    )
    removed: list[str] = []
    changed: list[str] = []
    added: list[str] = []
    parse_errors: list[str] = []
    for relative in changed_paths:
        old_path = _safe_file(seed, relative)
        new_path = _safe_file(patched, relative)
        if old_path is None or new_path is None:
            parse_errors.append(f"unsafe_path:{relative}")
            continue
        old_contracts, old_error = _module_contracts(old_path)
        new_contracts, new_error = _module_contracts(new_path)
        if old_error:
            parse_errors.append(f"seed:{relative}:{old_error}")
        if new_error:
            parse_errors.append(f"patched:{relative}:{new_error}")
        if old_error or new_error:
            continue
        old_names = set(old_contracts)
        new_names = set(new_contracts)
        removed.extend(f"{relative}::{name}" for name in old_names - new_names)
        added.extend(f"{relative}::{name}" for name in new_names - old_names)
        changed.extend(
            f"{relative}::{name}"
            for name in old_names.intersection(new_names)
            if old_contracts[name] != new_contracts[name]
        )
    signature_change_allowed = bool(candidate.get("allow_signature_change", False))
    disallowed_changes = [] if signature_change_allowed else sorted(changed)
    failures = sorted(removed) + disallowed_changes
    if parse_errors:
        status = "blocker"
        reason = "api_contract_snapshot_unavailable"
    elif failures:
        status = "fail"
        reason = "api_or_type_contract_changed"
    else:
        status = "pass"
        reason = "api_and_type_contracts_compatible"
    return {
        "check_id": "api_contract_compatibility",
        "status": status,
        "required": True,
        "reason": reason,
        "changed_file_count": len(changed_paths),
        "removed_contracts": sorted(removed),
        "changed_contracts": sorted(changed),
        "added_contracts": sorted(added),
        "signature_change_allowed": signature_change_allowed,
        "allowed_contract_changes": sorted(changed) if signature_change_allowed else [],
        "parse_errors": parse_errors,
    }


def _static_semantic_check(
    candidate: dict[str, Any],
    *,
    editable_regions: list[EditableRegion],
) -> dict[str, Any]:
    regions = {
        (region.path, region.original_sha256): region for region in editable_regions
    }
    rows: list[dict[str, Any]] = []
    blocked: list[str] = []
    for index, value in enumerate(_list(candidate.get("files"))):
        item = _dict(value)
        key = (
            _normalize_relative_path(str(item.get("path") or "")),
            str(item.get("original_sha256") or ""),
        )
        region = regions.get(key)
        if region is None:
            blocked.append(f"files[{index}].region_not_authorized")
            continue
        replacement = str(item.get("replacement") or "")
        validation = (
            validate_semantic_module_patch(region.source, replacement)
            if region.region_kind == "module"
            else validate_semantic_patch(region.source, replacement)
        )
        row = validation.to_dict()
        row.update(
            {
                "path": region.path,
                "function_id": region.function_id,
                "region_kind": region.region_kind,
            }
        )
        rows.append(row)
        blocked.extend(str(reason) for reason in validation.blocked_reasons)
    return {
        "check_id": "static_semantic_diff",
        "status": "fail" if blocked else "pass",
        "required": True,
        "reason": (
            "static_semantic_risk_blocked"
            if blocked
            else "static_semantic_checks_not_blocked"
        ),
        "blocked_reasons": sorted(set(blocked)),
        "warning_count": sum(len(_list(row.get("warnings"))) for row in rows),
        "files": rows,
    }


def _workspace_consistency_check(
    candidate: dict[str, Any],
    *,
    editable_regions: list[EditableRegion],
    seed: Path,
    patched: Path,
) -> dict[str, Any]:
    regions = {
        (region.path, region.original_sha256): region for region in editable_regions
    }
    errors: list[str] = []
    parse_errors: list[str] = []
    rows: list[dict[str, Any]] = []
    changed_modules: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(_list(candidate.get("files"))):
        item = _dict(value)
        relative = _normalize_relative_path(str(item.get("path") or ""))
        key = (relative, str(item.get("original_sha256") or ""))
        region = regions.get(key)
        if region is None:
            errors.append(f"files[{index}].region_not_authorized")
            continue
        old_path = _safe_file(seed, relative)
        new_path = _safe_file(patched, relative)
        if (
            old_path is None
            or new_path is None
            or not old_path.is_file()
            or not new_path.is_file()
            or old_path.is_symlink()
            or new_path.is_symlink()
        ):
            errors.append(f"files[{index}].workspace_path_missing_or_unsafe")
            continue
        try:
            current = _normalize_newlines(new_path.read_text(encoding="utf-8"))
            replacement = _normalize_newlines(
                str(item.get("replacement") or "")
            ).rstrip("\n")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"files[{index}].workspace_source_unreadable:{type(exc).__name__}")
            continue
        if region.region_kind == "module":
            reflected = current.rstrip("\n") == replacement
            occurrence_count = 1 if reflected else 0
        else:
            occurrence_count = current.count(replacement) if replacement else 0
            reflected = occurrence_count > 0
        if not reflected:
            errors.append(f"files[{index}].replacement_not_reflected")
        try:
            old_tree = ast.parse(old_path.read_text(encoding="utf-8"), filename=relative)
            new_tree = ast.parse(current, filename=relative)
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
            parse_errors.append(f"files[{index}]:{type(exc).__name__}")
            old_tree = None
            new_tree = None
        module_name = _module_name_for_path(relative)
        if module_name and old_tree is not None and new_tree is not None:
            changed_modules[module_name] = {
                "old_symbols": _top_level_symbols(old_tree),
                "new_symbols": _top_level_symbols(new_tree),
                "dynamic_exports": "__getattr__" in _top_level_symbols(new_tree),
            }
        rows.append(
            {
                "index": index,
                "path": relative,
                "region_kind": region.region_kind,
                "replacement_reflected": reflected,
                "replacement_occurrence_count": occurrence_count,
            }
        )
    broken_imports, import_warnings = _removed_symbol_imports(
        patched,
        changed_modules=changed_modules,
    )
    if broken_imports:
        errors.append("removed_symbol_still_imported")
    if parse_errors:
        status = "blocker"
        reason = "patched_workspace_ast_unavailable"
    elif errors:
        status = "fail"
        reason = "patched_workspace_or_cross_file_inconsistent"
    else:
        status = "pass"
        reason = "candidate_reflected_and_cross_file_imports_consistent"
    return {
        "check_id": "patched_workspace_consistency",
        "status": status,
        "required": True,
        "reason": reason,
        "edit_count": len(rows),
        "changed_module_count": len(changed_modules),
        "cross_file_broken_import_count": len(broken_imports),
        "errors": sorted(set(errors)),
        "parse_errors": sorted(set(parse_errors)),
        "import_scan_warnings": import_warnings,
        "broken_imports": broken_imports,
        "edits": rows,
    }


def _minimality_check(
    candidate: dict[str, Any],
    *,
    editable_regions: list[EditableRegion],
) -> dict[str, Any]:
    regions = {
        (region.path, region.original_sha256): region for region in editable_regions
    }
    rows = []
    total_changed_lines = 0
    original_line_count = 0
    paths: set[str] = set()
    errors: list[str] = []
    for index, value in enumerate(_list(candidate.get("files"))):
        item = _dict(value)
        key = (
            _normalize_relative_path(str(item.get("path") or "")),
            str(item.get("original_sha256") or ""),
        )
        region = regions.get(key)
        if region is None:
            errors.append(f"files[{index}].region_not_authorized")
            continue
        changed_lines = _changed_line_count(
            region.source,
            str(item.get("replacement") or ""),
        )
        old_lines = max(1, len(region.source.splitlines()))
        total_changed_lines += changed_lines
        original_line_count += old_lines
        paths.add(region.path)
        rows.append(
            {
                "path": region.path,
                "function_id": region.function_id,
                "changed_lines": changed_lines,
                "original_lines": old_lines,
                "line_change_ratio": round(changed_lines / old_lines, 6),
            }
        )
    edit_count = len(rows)
    budget = max(1, 80 * max(1, edit_count))
    cross_file_penalty = 0.05 * max(0, len(paths) - 1)
    score = max(
        0.0,
        1.0 - min(1.0, total_changed_lines / budget) - cross_file_penalty,
    )
    if errors or edit_count == 0 or total_changed_lines == 0:
        status = "fail"
        reason = "patch_minimality_evidence_invalid"
    elif total_changed_lines > budget:
        status = "fail"
        reason = "patch_exceeds_minimality_budget"
    else:
        status = "pass"
        reason = "patch_within_static_minimality_budget"
    return {
        "check_id": "patch_minimality",
        "status": status,
        "required": True,
        "reason": reason,
        "edit_count": edit_count,
        "changed_file_count": len(paths),
        "changed_line_count": total_changed_lines,
        "original_line_count": original_line_count,
        "minimality_score": round(score, 6),
        "score_formula": "max(0,1-changed_lines/(80*edits)-0.05*(files-1))",
        "errors": errors,
        "edits": rows,
    }


def _boundary_property_check(
    candidate: dict[str, Any],
    *,
    editable_regions: list[EditableRegion],
    python_executable: str | Path,
) -> dict[str, Any]:
    regions = {
        (region.path, region.original_sha256): region for region in editable_regions
    }
    rule_ids = [
        str(value)
        for value in _list(candidate.get("semantic_rule_ids"))
        if str(value)
    ]
    rows = []
    for index, value in enumerate(_list(candidate.get("files"))):
        item = _dict(value)
        key = (
            _normalize_relative_path(str(item.get("path") or "")),
            str(item.get("original_sha256") or ""),
        )
        region = regions.get(key)
        if region is None or region.region_kind == "module":
            continue
        replacement = str(item.get("replacement") or "")
        rule_id = rule_ids[0] if rule_ids else "v3_semantic_candidate"
        patch = PatchCandidate(
            id=f"v3-semantic-{index}",
            target_file=region.path,
            relative_file_path=region.path,
            target_function_id=region.function_id,
            target_function_name=region.function_name,
            rule_id=rule_id,
            description="V3 generated boundary/property semantic probe.",
            old_source=region.source,
            new_source=replacement,
            diff=render_unified_diff(region.source, replacement, region.path),
            metadata={"static_rule_ids": rule_ids},
        )
        probe = run_boundary_probe(
            patch,
            python_executable=python_executable,
        ).to_dict()
        rows.append(
            {
                "status": str(probe.get("status") or ""),
                "reason": str(probe.get("reason") or ""),
                "rule_id": str(probe.get("rule_id") or ""),
                "case_count": _int(probe.get("case_count"), 0),
                "forbidden_exceptions": [
                    str(value) for value in probe.get("forbidden_exceptions", ())
                ],
                "results": [
                    {
                        str(key): value
                        for key, value in _dict(result).items()
                        if key
                        in {
                            "case_index",
                            "status",
                            "result_type",
                            "exception_type",
                        }
                    }
                    for result in probe.get("results", ())
                ],
                "timeout": bool(probe.get("timeout", False)),
                "returncode": _int(probe.get("returncode"), 0),
                "path": region.path,
                "function_id": region.function_id,
            }
        )
    executed = [row for row in rows if row.get("status") != "not_run"]
    blockers = [row for row in executed if row.get("status") == "blocker"]
    failures = [row for row in executed if row.get("status") == "fail"]
    return {
        "check_id": "generated_boundary_property_probe",
        "status": (
            "blocker"
            if blockers
            else "fail"
            if failures
            else "pass"
            if executed
            else "not_applicable"
        ),
        "required": bool(executed),
        "reason": (
            "generated_boundary_probe_blocked"
            if blockers
            else "generated_boundary_probe_failed"
            if failures
            else "generated_boundary_probe_passed"
            if executed
            else "no_supported_generated_boundary_probe"
        ),
        "probe_count": len(executed),
        "case_count": sum(_int(row.get("case_count"), 0) for row in executed),
        "probes": rows,
    }


def _target_differential_check(
    case: dict[str, Any],
    *,
    seed: Path,
    patched: Path,
    python_executable: str | Path,
    timeout: int,
    patched_execution: dict[str, Any] | None,
) -> dict[str, Any]:
    commands = _test_commands(case.get("targeted_test_commands"))
    if not commands:
        return {
            "check_id": "target_behavior_differential",
            "status": "not_applicable",
            "required": True,
            "reason": "targeted_test_oracle_missing",
        }
    baseline = execute_test_commands(
        commands,
        repository_root=seed,
        python_executable=python_executable,
        timeout=timeout,
        test_environment=_dict(case.get("test_environment")),
    )
    current = (
        patched_execution
        if isinstance(patched_execution, dict)
        else execute_test_commands(
            commands,
            repository_root=patched,
            python_executable=python_executable,
            timeout=timeout,
            test_environment=_dict(case.get("test_environment")),
        )
    )
    if baseline.get("environment_blocker") is True:
        status = "blocker"
        reason = "bug_seed_targeted_test_environment_blocker"
    elif current.get("environment_blocker") is True:
        status = "blocker"
        reason = "patched_targeted_test_environment_blocker"
    elif baseline.get("status") == "pass":
        status = "fail"
        reason = "bug_seed_no_longer_reproduces_target_failure"
    elif current.get("status") != "pass":
        status = "fail"
        reason = "patched_workspace_does_not_pass_targeted_test"
    else:
        status = "pass"
        reason = "bug_seed_fails_while_patched_target_passes"
    return {
        "check_id": "target_behavior_differential",
        "status": status,
        "required": True,
        "reason": reason,
        "expected_relation": "bug_seed_fail_and_patched_workspace_pass",
        "bug_seed_execution": _compact_test_group(baseline),
        "patched_execution": _compact_test_group(current),
        "patched_execution_reused": isinstance(patched_execution, dict),
    }


def _reverse_mutation_check(
    candidate: dict[str, Any],
    *,
    editable_regions: list[EditableRegion],
    patched: Path,
    case: dict[str, Any],
    python_executable: str | Path,
    targeted_timeout: int,
    regression_timeout: int,
) -> dict[str, Any]:
    regions = {
        (region.path, region.original_sha256): region for region in editable_regions
    }
    commands = _test_commands(case.get("targeted_test_commands"))
    regression_command = [
        str(value) for value in _list(case.get("regression_command"))
    ]
    if not commands or not regression_command:
        return {
            "check_id": "reverse_mutation_sensitivity",
            "status": "not_applicable",
            "required": True,
            "reason": "complete_target_and_regression_oracle_required",
            "mutations": [],
        }
    symlink = next((path for path in patched.rglob("*") if path.is_symlink()), None)
    if symlink is not None:
        return {
            "check_id": "reverse_mutation_sensitivity",
            "status": "blocker",
            "required": True,
            "reason": "patched_workspace_contains_symlink",
            "symlink": symlink.relative_to(patched).as_posix(),
            "mutations": [],
        }
    mutations = []
    for index, value in enumerate(_list(candidate.get("files"))):
        item = _dict(value)
        key = (
            _normalize_relative_path(str(item.get("path") or "")),
            str(item.get("original_sha256") or ""),
        )
        region = regions.get(key)
        if region is None:
            mutations.append(
                {
                    "index": index,
                    "status": "blocker",
                    "reason": "region_not_authorized",
                }
            )
            continue
        with _temporary_workspace(
            prefix=f"cia_v3_reverse_{index}_",
            dir=patched.parent,
        ) as temporary:
            mutant = temporary / "repository"
            try:
                shutil.copytree(
                    patched,
                    mutant,
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
                mutations.append(
                    {
                        "index": index,
                        "path": region.path,
                        "status": "blocker",
                        "reason": "reverse_mutation_workspace_copy_failed",
                        "error_type": type(exc).__name__,
                    }
                )
                continue
            revert = _revert_one_edit(
                mutant,
                region=region,
                replacement=str(item.get("replacement") or ""),
            )
            if revert["status"] != "pass":
                mutations.append({"index": index, "path": region.path, **revert})
                continue
            targeted = execute_test_commands(
                commands,
                repository_root=mutant,
                python_executable=python_executable,
                timeout=targeted_timeout,
                test_environment=_dict(case.get("test_environment")),
            )
            if targeted.get("environment_blocker") is True:
                mutations.append(
                    {
                        "index": index,
                        "path": region.path,
                        "status": "blocker",
                        "reason": "reverse_mutation_target_environment_blocker",
                        "targeted": _compact_test_group(targeted),
                    }
                )
                continue
            if targeted.get("status") != "pass":
                mutations.append(
                    {
                        "index": index,
                        "path": region.path,
                        "status": "pass",
                        "reason": "reverse_mutation_killed_by_targeted_tests",
                        "killed_by": "targeted_tests",
                        "targeted": _compact_test_group(targeted),
                    }
                )
                continue
            regression = execute_test_commands(
                [regression_command],
                repository_root=mutant,
                python_executable=python_executable,
                timeout=regression_timeout,
                test_environment=_dict(case.get("test_environment")),
            )
            if regression.get("environment_blocker") is True:
                status = "blocker"
                reason = "reverse_mutation_regression_environment_blocker"
                killed_by = ""
            elif regression.get("status") != "pass":
                status = "pass"
                reason = "reverse_mutation_killed_by_full_regression"
                killed_by = "full_regression"
            else:
                status = "fail"
                reason = "reverse_mutation_survived_complete_test_oracle"
                killed_by = ""
            mutations.append(
                {
                    "index": index,
                    "path": region.path,
                    "status": status,
                    "reason": reason,
                    "killed_by": killed_by,
                    "targeted": _compact_test_group(targeted),
                    "full_regression": _compact_test_group(regression),
                }
            )
    blockers = [row for row in mutations if row.get("status") == "blocker"]
    survivors = [row for row in mutations if row.get("status") == "fail"]
    if not mutations:
        status = "not_applicable"
        reason = "candidate_has_no_reversible_edits"
    elif blockers:
        status = "blocker"
        reason = str(blockers[0].get("reason") or "reverse_mutation_blocker")
    elif survivors:
        status = "fail"
        reason = "one_or_more_reverse_mutations_survived"
    else:
        status = "pass"
        reason = "every_reverse_mutation_was_killed"
    return {
        "check_id": "reverse_mutation_sensitivity",
        "status": status,
        "required": True,
        "reason": reason,
        "mutation_count": len(mutations),
        "killed_mutation_count": sum(
            row.get("status") == "pass" for row in mutations
        ),
        "surviving_mutation_count": len(survivors),
        "mutations": mutations,
    }


@contextmanager
def _temporary_workspace(*, prefix: str, dir: Path) -> Iterator[Path]:
    temporary = Path(tempfile.mkdtemp(prefix=prefix, dir=dir))
    try:
        yield temporary
    finally:
        _cleanup_temporary_workspace(temporary)


def _cleanup_temporary_workspace(
    path: Path,
    *,
    delays: tuple[float, ...] = _TEMPORARY_CLEANUP_DELAYS,
    sleeper: Any = time.sleep,
) -> bool:
    last_error: OSError | None = None
    for delay in delays or (0.0,):
        if delay > 0:
            sleeper(delay)
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return True
        except OSError as exc:
            last_error = exc
            gc.collect()
        else:
            return True
    warnings.warn(
        "temporary semantic workspace cleanup failed after retries: "
        f"{type(last_error).__name__ if last_error else 'OSError'}",
        RuntimeWarning,
        stacklevel=2,
    )
    return False


def _configured_semantic_commands_check(
    case: dict[str, Any],
    *,
    patched: Path,
    python_executable: str | Path,
    timeout: int,
) -> dict[str, Any]:
    config = _dict(case.get("semantic_validation"))
    specs = [_dict(value) for value in _list(config.get("commands"))]
    if not specs:
        return {
            "check_id": "manifest_semantic_commands",
            "status": "not_applicable",
            "required": False,
            "reason": "no_manifest_semantic_commands",
            "commands": [],
        }
    rows = []
    for index, spec in enumerate(specs):
        kind = str(spec.get("kind") or "").strip().lower()
        command = [str(value) for value in _list(spec.get("command"))]
        validation_error = _semantic_command_error(kind, command)
        if validation_error:
            rows.append(
                {
                    "index": index,
                    "kind": kind,
                    "status": "blocker",
                    "reason": validation_error,
                    "command": command,
                }
            )
            continue
        execution = execute_test_commands(
            [command],
            repository_root=patched,
            python_executable=python_executable,
            timeout=timeout,
            test_environment=_dict(case.get("test_environment")),
        )
        rows.append(
            {
                "index": index,
                "kind": kind,
                "status": (
                    "blocker"
                    if execution.get("environment_blocker") is True
                    else "pass"
                    if execution.get("status") == "pass"
                    else "fail"
                ),
                "reason": str(execution.get("reason") or "semantic_command_result"),
                "command": command,
                "execution": _compact_test_group(execution),
            }
        )
    blockers = [row for row in rows if row.get("status") == "blocker"]
    failures = [row for row in rows if row.get("status") == "fail"]
    return {
        "check_id": "manifest_semantic_commands",
        "status": "blocker" if blockers else "fail" if failures else "pass",
        "required": True,
        "reason": (
            str(blockers[0].get("reason"))
            if blockers
            else "manifest_semantic_command_failed"
            if failures
            else "all_manifest_semantic_commands_passed"
        ),
        "commands": rows,
    }


def _revert_one_edit(
    repository: Path,
    *,
    region: EditableRegion,
    replacement: str,
) -> dict[str, Any]:
    target = _safe_file(repository, region.path)
    if target is None or not target.is_file() or target.is_symlink():
        return {"status": "blocker", "reason": "mutation_target_missing_or_unsafe"}
    text = _normalize_newlines(target.read_text(encoding="utf-8"))
    replacement_text = _normalize_newlines(replacement).rstrip("\n")
    original_text = _normalize_newlines(region.source).rstrip("\n")
    occurrence_count = text.count(replacement_text)
    if not replacement_text or occurrence_count != 1:
        return {
            "status": "blocker",
            "reason": "patched_region_not_uniquely_reversible",
            "replacement_occurrence_count": occurrence_count,
        }
    reverted = text.replace(replacement_text, original_text, 1)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            ast.parse(reverted, filename=region.path)
    except SyntaxError as exc:
        return {
            "status": "blocker",
            "reason": "reverse_mutation_ast_invalid",
            "line": exc.lineno or 0,
            "offset": exc.offset or 0,
        }
    with target.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(reverted)
    return {"status": "pass", "reason": "candidate_edit_reverted_once"}


def _module_contracts(path: Path) -> tuple[dict[str, tuple[str, str]], str]:
    try:
        source = path.read_text(encoding="utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source, filename=path.name)
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
        return {}, type(exc).__name__
    contracts: dict[str, tuple[str, str]] = {}

    def collect(body: list[ast.stmt], prefix: str = "") -> None:
        for node in body:
            if not isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                continue
            name = f"{prefix}.{node.name}" if prefix else node.name
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                signature = ast.dump(
                    ast.Tuple(
                        elts=[
                            node.args,
                            node.returns or ast.Constant(value=None),
                            ast.Constant(value=node.type_comment),
                            ast.List(
                                elts=list(getattr(node, "type_params", [])),
                                ctx=ast.Load(),
                            ),
                        ],
                        ctx=ast.Load(),
                    ),
                    include_attributes=False,
                )
            else:
                signature = repr(
                    {
                        "bases": [
                            ast.dump(value, include_attributes=False)
                            for value in node.bases
                        ],
                        "keywords": [
                            (
                                keyword.arg,
                                ast.dump(keyword.value, include_attributes=False),
                            )
                            for keyword in node.keywords
                        ],
                        "type_params": [
                            ast.dump(value, include_attributes=False)
                            for value in getattr(node, "type_params", [])
                        ],
                    }
                )
            decorators = repr(
                [
                    ast.dump(value, include_attributes=False)
                    for value in node.decorator_list
                ]
            )
            contracts[name] = (signature, decorators)
            if isinstance(node, ast.ClassDef):
                collect(node.body, name)

    collect(tree.body)
    return contracts, ""


def _removed_symbol_imports(
    repository: Path,
    *,
    changed_modules: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, str]], list[str]]:
    removed_by_module = {
        module: set(_list(details.get("old_symbols"))).difference(
            _list(details.get("new_symbols"))
        )
        for module, details in changed_modules.items()
    }
    removed_by_module = {
        module: symbols for module, symbols in removed_by_module.items() if symbols
    }
    if not removed_by_module:
        return [], []
    broken: list[dict[str, str]] = []
    warnings_seen: list[str] = []
    for path in sorted(repository.rglob("*.py")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            relative = path.relative_to(repository).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
            if len(warnings_seen) < 50:
                warnings_seen.append(
                    f"import_scan_skipped:{path.name}:{type(exc).__name__}"
                )
            continue
        importer_module = _module_name_for_path(relative)
        importer_is_package = PurePosixPath(relative).name == "__init__.py"
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            imported_module = _resolve_import_module(
                importer_module,
                importer_is_package=importer_is_package,
                level=node.level,
                module=str(node.module or ""),
            )
            removed = removed_by_module.get(imported_module, set())
            if not removed:
                continue
            details = changed_modules.get(imported_module, {})
            for alias in node.names:
                if alias.name == "*" or alias.name not in removed:
                    continue
                row = {
                    "importer": relative,
                    "module": imported_module,
                    "symbol": alias.name,
                }
                if details.get("dynamic_exports") is True:
                    if len(warnings_seen) < 50:
                        warnings_seen.append(
                            "dynamic_export_requires_runtime_evidence:"
                            f"{relative}:{imported_module}:{alias.name}"
                        )
                else:
                    broken.append(row)
    broken.sort(key=lambda row: (row["module"], row["symbol"], row["importer"]))
    return broken, sorted(set(warnings_seen))


def _module_name_for_path(relative_path: str) -> str:
    path = PurePosixPath(_normalize_relative_path(relative_path))
    if not path.parts or path.suffix.lower() != ".py":
        return ""
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_import_module(
    importer_module: str,
    *,
    importer_is_package: bool,
    level: int,
    module: str,
) -> str:
    if level <= 0:
        return module
    importer_parts = [part for part in importer_module.split(".") if part]
    package_parts = importer_parts if importer_is_package else importer_parts[:-1]
    climb = max(0, level - 1)
    if climb > len(package_parts):
        return module
    prefix = package_parts[: len(package_parts) - climb]
    suffix = [part for part in module.split(".") if part]
    return ".".join([*prefix, *suffix])


def _top_level_symbols(tree: ast.Module) -> list[str]:
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                symbols.update(_assigned_names(target))
        elif isinstance(node, ast.AnnAssign):
            symbols.update(_assigned_names(node.target))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                symbols.add(alias.asname or alias.name.split(".", 1)[0])
    return sorted(symbols)


def _assigned_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        return {
            name
            for child in node.elts
            for name in _assigned_names(child)
        }
    return set()


def _semantic_command_error(kind: str, command: list[str]) -> str:
    if kind not in {"boundary", "property", "mutation", "differential"}:
        return "unsupported_semantic_command_kind"
    if len(command) < 3 or command[1] != "-m":
        return "semantic_command_must_use_python_module_form"
    if command[0] != "{python}":
        return "semantic_command_must_use_pinned_python_placeholder"
    if command[2] not in SUPPORTED_SEMANTIC_TEST_MODULES:
        return "semantic_command_module_not_allowed"
    return ""


def _compact_test_group(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": str(group.get("status") or ""),
        "reason": str(group.get("reason") or ""),
        "environment_blocker": bool(group.get("environment_blocker", False)),
        "result_count": len(_list(group.get("results"))),
        "results": [
            {
                "status": str(_dict(value).get("status") or ""),
                "returncode": _int(_dict(value).get("returncode"), -1),
                "test_count": _int(_dict(value).get("test_count"), 0),
                "passed": _int(_dict(value).get("passed"), 0),
                "failed": _int(_dict(value).get("failed"), 0),
                "timeout": bool(_dict(value).get("timeout", False)),
            }
            for value in _list(group.get("results"))
        ],
    }


def _test_commands(value: Any) -> list[list[str]]:
    return [
        [str(part) for part in _list(command)]
        for command in _list(value)
        if _list(command)
    ]


def _safe_file(root: Path, relative_path: str) -> Path | None:
    normalized = _normalize_relative_path(relative_path)
    relative = PurePosixPath(normalized)
    if (
        not normalized
        or "\x00" in normalized
        or relative.is_absolute()
        or ".." in relative.parts
        or (relative.parts and relative.parts[0].endswith(":"))
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
    if _has_symlink_component(target, root=root_resolved):
        return None
    return target


def _has_symlink_component(path: Path, *, root: Path) -> bool:
    current = path
    while current != root:
        if current.is_symlink():
            return True
        parent = current.parent
        if parent == current:
            return True
        current = parent
    return False


def _normalize_relative_path(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").strip()
    return PurePosixPath(normalized).as_posix() if normalized else ""


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _changed_line_count(old: str, new: str) -> int:
    matcher = difflib.SequenceMatcher(
        a=_normalize_newlines(old).splitlines(),
        b=_normalize_newlines(new).splitlines(),
        autojunk=False,
    )
    return sum(
        max(old_end - old_start, new_end - new_start)
        for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes()
        if tag != "equal"
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
