from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_repository_test_repair_summary(
    patch_validation: dict[str, Any] | None,
    *,
    output_paths: dict[str, str] | None = None,
    patch_candidates: dict[str, Any] | None = None,
    fault_localization: dict[str, Any] | None = None,
    dynamic_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validation = _dict(patch_validation)
    paths = dict(output_paths or {})
    candidates = _dict(patch_candidates)
    localization = _dict(fault_localization)
    evidence = _dict(dynamic_evidence)
    best_patch = _dict(validation.get("best_patch"))
    regression = _dict(validation.get("regression_validation"))
    repair_ready = bool(validation.get("repair_ready", False))
    validation_scope = str(validation.get("repair_validation_scope") or "none")
    status, reason, conclusion = _summary_status(
        validation,
        repair_ready=repair_ready,
        validation_scope=validation_scope,
    )
    patch_path = str(paths.get("repository_test_repair_patch") or "")
    return {
        "status": status,
        "reason": reason,
        "conclusion": conclusion,
        "repair_ready": repair_ready,
        "repair_validation_scope": validation_scope,
        "patch_path": patch_path,
        "patch_path_present": bool(patch_path),
        "patch_validation_status": str(validation.get("status") or ""),
        "patch_validation_reason": str(validation.get("reason") or ""),
        "patch_validation_success_count": _int(validation.get("success_count", 0)),
        "patch_validation_executed_count": _int(
            validation.get("executed_count", 0)
        ),
        "recommended_validation_command": str(
            candidates.get("recommended_validation_command")
            or validation.get("recommended_validation_command")
            or evidence.get("recommended_validation_command")
            or ""
        ),
        "top_function": str(
            localization.get("top_function")
            or candidates.get("top_function")
            or best_patch.get("target_function_name")
            or ""
        ),
        "best_patch": _best_patch_summary(best_patch),
        "regression_validation": _regression_summary(regression),
        "next_actions": _next_actions(
            repair_ready=repair_ready,
            validation_scope=validation_scope,
            patch_path=patch_path,
            validation=validation,
            regression=regression,
        ),
    }


def render_repository_test_repair_summary_markdown(payload: dict[str, Any]) -> str:
    best_patch = _dict(payload.get("best_patch"))
    regression = _dict(payload.get("regression_validation"))
    lines = [
        "# Repository Test Repair Summary",
        "",
        f"- Status: `{_markdown_cell(payload.get('status') or '')}`",
        f"- Reason: `{_markdown_cell(payload.get('reason') or '')}`",
        f"- Conclusion: `{_markdown_cell(payload.get('conclusion') or '')}`",
        f"- Repair Ready: {str(bool(payload.get('repair_ready', False))).lower()}",
        (
            "- Validation Scope: "
            f"`{_markdown_cell(payload.get('repair_validation_scope') or 'none')}`"
        ),
        f"- Patch Path: `{_markdown_cell(payload.get('patch_path') or 'none')}`",
        (
            "- Recommended Validation Command: "
            f"`{_markdown_cell(payload.get('recommended_validation_command') or 'none')}`"
        ),
        f"- Top Function: `{_markdown_cell(payload.get('top_function') or 'none')}`",
        "",
        "## Best Patch",
        "",
    ]
    if best_patch:
        lines.extend(
            [
                f"- Candidate: `{_markdown_cell(best_patch.get('candidate_id') or 'none')}`",
                f"- File: `{_markdown_cell(best_patch.get('relative_file_path') or 'none')}`",
                f"- Rule: `{_markdown_cell(best_patch.get('rule_id') or 'none')}`",
                f"- Variant: `{_markdown_cell(best_patch.get('variant') or 'none')}`",
                f"- Depth: {_int(best_patch.get('depth', 0))}",
                (
                    "- Parent Candidate: "
                    f"`{_markdown_cell(best_patch.get('parent_candidate_id') or 'none')}`"
                ),
                f"- Has Diff: {str(bool(best_patch.get('has_diff', False))).lower()}",
            ]
        )
    else:
        lines.append("- none")
    lines.extend(["", "## Regression Validation", ""])
    if regression:
        lines.extend(
            [
                f"- Status: `{_markdown_cell(regression.get('status') or 'none')}`",
                f"- Reason: `{_markdown_cell(regression.get('reason') or 'none')}`",
                (
                    "- Command: "
                    f"`{_markdown_cell(regression.get('validation_command') or 'none')}`"
                ),
                f"- Passed: {_int(regression.get('passed', 0))}",
                f"- Failed: {_int(regression.get('failed', 0))}",
            ]
        )
    else:
        lines.append("- none")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    return "\n".join(lines)


def write_repository_test_repair_summary_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_repair_summary.json"
    markdown_path = root / "repository_test_repair_summary.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_repair_summary_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_repair_summary_json": str(json_path),
        "repository_test_repair_summary_markdown": str(markdown_path),
    }


def _summary_status(
    validation: dict[str, Any],
    *,
    repair_ready: bool,
    validation_scope: str,
) -> tuple[str, str, str]:
    if not validation:
        return "skipped", "patch_validation_missing", "not_ready"
    if repair_ready:
        return "pass", "repair_ready", "ready_for_review"
    if (
        _int(validation.get("success_count", 0)) > 0
        and validation_scope == "regression_failed"
    ):
        return "fail", "regression_validation_failed", "not_ready"
    validation_status = str(validation.get("status") or "")
    validation_reason = str(validation.get("reason") or "")
    if validation_status == "skipped":
        return "skipped", validation_reason or "patch_validation_skipped", "not_ready"
    if validation_status == "fail":
        return "fail", validation_reason or "patch_validation_failed", "not_ready"
    return "warning", validation_reason or "repair_not_ready", "not_ready"


def _best_patch_summary(best_patch: dict[str, Any]) -> dict[str, Any]:
    if not best_patch:
        return {}
    patch_diff = str(best_patch.get("diff") or "")
    return {
        "candidate_id": str(best_patch.get("candidate_id") or ""),
        "relative_file_path": str(best_patch.get("relative_file_path") or ""),
        "target_function_name": str(best_patch.get("target_function_name") or ""),
        "rule_id": str(best_patch.get("rule_id") or ""),
        "variant": str(best_patch.get("variant") or ""),
        "depth": _int(best_patch.get("depth", 0)),
        "parent_candidate_id": str(best_patch.get("parent_candidate_id") or ""),
        "score": _float(best_patch.get("score", 0.0)),
        "passed": _int(best_patch.get("passed", 0)),
        "failed": _int(best_patch.get("failed", 0)),
        "has_diff": bool(patch_diff),
        "diff_preview": patch_diff[:1200],
    }


def _regression_summary(regression: dict[str, Any]) -> dict[str, Any]:
    if not regression:
        return {}
    return {
        "status": str(regression.get("status") or ""),
        "reason": str(regression.get("reason") or ""),
        "validation_command": str(regression.get("validation_command") or ""),
        "pytest_args": [str(arg) for arg in _list(regression.get("pytest_args"))],
        "success": bool(regression.get("success", False)),
        "passed": _int(regression.get("passed", 0)),
        "failed": _int(regression.get("failed", 0)),
        "failure_type": str(regression.get("failure_type") or ""),
        "failure_reason": str(regression.get("failure_reason") or ""),
    }


def _next_actions(
    *,
    repair_ready: bool,
    validation_scope: str,
    patch_path: str,
    validation: dict[str, Any],
    regression: dict[str, Any],
) -> list[str]:
    if repair_ready:
        actions = ["Review the generated repair patch before applying it."]
        if patch_path:
            actions.append(f"Patch artifact: {patch_path}")
        if validation_scope == "narrow_only":
            actions.append("Run broader repository regression tests before merge.")
        return actions
    if validation_scope == "regression_failed":
        return [
            "Do not promote the patch yet; inspect regression_validation feedback.",
            "Refine the best narrow-scope patch and rerun regression validation.",
        ]
    if str(validation.get("status") or "") == "skipped":
        return ["Complete patch validation before producing a repair patch."]
    if str(regression.get("status") or "") == "fail":
        return ["Fix regression failures before marking the repair ready."]
    return ["Inspect patch validation failures and continue the reflection loop."]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
