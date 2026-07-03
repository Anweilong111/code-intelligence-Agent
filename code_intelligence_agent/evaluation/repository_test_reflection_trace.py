from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def build_repository_test_reflection_trace(
    patch_validation: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = _dict(patch_validation)
    if not payload:
        return _skipped(
            "patch_validation_missing",
            "Repository test patch validation is not available.",
        )
    results = [_dict(item) for item in _list(payload.get("results"))]
    if not results:
        return {
            "status": "skipped",
            "reason": "patch_validation_not_executed",
            "message": "No patch candidate execution results were available.",
            **_common_fields(payload),
            "initial_failure_type_counts": {},
            "reflection_failure_type_counts": {},
            "reflection_parent_failure_type_counts": {},
            "successful_reflection_parent_failure_type_counts": {},
            "initial_failures": [],
            "reflection_steps": [],
            "final_outcome": _final_outcome(payload),
            "next_actions": _next_actions(payload, [], []),
            "initial_strategy_counts": {},
            "recommended_reflection_strategies": [],
        }

    initial_failures = [
        _trace_row(row)
        for row in results
        if _int(row.get("depth", 0)) == 0 and not bool(row.get("success", False))
    ]
    result_by_id = {str(row.get("candidate_id") or ""): row for row in results}
    reflection_steps = [
        _reflection_step(row, result_by_id)
        for row in results
        if _int(row.get("depth", 0)) > 0
    ]
    successful_reflections = [
        row for row in reflection_steps if bool(row.get("success", False))
    ]
    initial_failure_type_counts = _counts_by_field(
        initial_failures,
        "failure_type",
    )
    initial_strategy_counts = _counts_by_field(
        initial_failures,
        "reflection_strategy_id",
    )
    recommended_strategies = _recommended_strategies(initial_failures)
    reflection_failure_type_counts = _counts_by_field(
        reflection_steps,
        "failure_type",
    )
    reflection_parent_failure_type_counts = _counts_by_field(
        reflection_steps,
        "parent_failure_type",
    )
    successful_reflection_parent_failure_type_counts = _counts_by_field(
        successful_reflections,
        "parent_failure_type",
    )
    if successful_reflections:
        reason = "reflection_repaired_candidate"
    elif reflection_steps:
        reason = "reflection_attempted_no_success"
    elif bool(payload.get("repair_ready", False)):
        reason = "depth0_success_no_reflection_needed"
    elif initial_failures and bool(payload.get("reflection_enabled", False)):
        reason = "reflection_enabled_but_no_child_candidate"
    elif initial_failures:
        reason = "depth0_failures_without_reflection"
    else:
        reason = "depth0_success_no_reflection_needed"
    return {
        "status": "pass" if str(payload.get("status") or "") == "pass" else "review",
        "reason": reason,
        "message": (
            "Reflection trace extracted from repository-test patch validation "
            "results."
        ),
        **_common_fields(payload),
        "initial_failure_type_counts": initial_failure_type_counts,
        "initial_strategy_counts": initial_strategy_counts,
        "recommended_reflection_strategies": recommended_strategies,
        "reflection_failure_type_counts": reflection_failure_type_counts,
        "reflection_parent_failure_type_counts": reflection_parent_failure_type_counts,
        "successful_reflection_parent_failure_type_counts": (
            successful_reflection_parent_failure_type_counts
        ),
        "initial_failures": initial_failures,
        "reflection_steps": reflection_steps,
        "final_outcome": _final_outcome(payload),
        "next_actions": _next_actions(payload, initial_failures, reflection_steps),
    }


def render_repository_test_reflection_trace_markdown(payload: dict[str, Any]) -> str:
    final_outcome = _dict(payload.get("final_outcome"))
    lines = [
        "# Repository Test Reflection Trace",
        "",
        f"- Status: `{_markdown_cell(payload.get('status') or '')}`",
        f"- Reason: `{_markdown_cell(payload.get('reason') or '')}`",
        f"- Patch Validation Status: `{_markdown_cell(payload.get('patch_validation_status') or '')}`",
        f"- Reflection Enabled: {str(bool(payload.get('reflection_enabled', False))).lower()}",
        f"- Reflection Mode: `{_markdown_cell(payload.get('reflection_mode') or 'none')}`",
        f"- Refiner Status: `{_markdown_cell(payload.get('reflection_refiner_status') or 'none')}`",
        f"- Refiner Reason: `{_markdown_cell(payload.get('reflection_refiner_reason') or 'none')}`",
        (
            "- LLM Reflection Config: "
            f"provider=`{_markdown_cell(_dict(payload.get('llm_reflection_config_audit')).get('provider') or 'none')}`, "
            f"model=`{_markdown_cell(_dict(payload.get('llm_reflection_config_audit')).get('model') or 'none')}`, "
            "api_key_present="
            f"{str(bool(_dict(payload.get('llm_reflection_config_audit')).get('api_key_present', False))).lower()}"
        ),
        f"- Reflection Rounds: {_int(payload.get('reflection_rounds', 0))}",
        f"- Reflection Width: {_int(payload.get('reflection_width', 0))}",
        f"- Max Depth Executed: {_int(payload.get('max_depth_executed', 0))}",
        f"- Executed Candidates: {_int(payload.get('executed_count', 0))}",
        f"- Successful Candidates: {_int(payload.get('success_count', 0))}",
        f"- Reflection Candidates: {_int(payload.get('reflection_candidate_count', 0))}",
        f"- Successful Reflection Candidates: {_int(payload.get('successful_reflection_candidate_count', 0))}",
        f"- Regression Reflection Candidates: {_int(payload.get('regression_reflection_candidate_count', 0))}",
        f"- Successful Regression Reflection Candidates: {_int(payload.get('successful_regression_reflection_candidate_count', 0))}",
        "",
        "## Initial Failures",
        "",
        "| Candidate | Rule | Variant | Failure Type | Failure Reason | Passed | Failed |",
        "| --- | --- | --- | --- | --- | ---: | ---: |",
    ]
    for item in _list(payload.get("initial_failures")):
        row = _dict(item)
        lines.append(
            "| "
            f"`{_markdown_cell(row.get('candidate_id'))}` | "
            f"`{_markdown_cell(row.get('rule_id'))}` | "
            f"`{_markdown_cell(row.get('variant') or 'none')}` | "
            f"`{_markdown_cell(row.get('failure_type') or 'none')}` | "
            f"{_markdown_cell(row.get('failure_reason') or '')} | "
            f"{_int(row.get('passed', 0))} | "
            f"{_int(row.get('failed', 0))} |"
        )
    if not _list(payload.get("initial_failures")):
        lines.append("| none | none | none | none | none | 0 | 0 |")
    lines.extend(
        [
            "",
            "## Reflection Steps",
            "",
            (
                "| Depth | Candidate | Parent | Parent Failure | Rule | Variant | "
                "Success | Failure Type | Feedback |"
            ),
            "| ---: | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in _list(payload.get("reflection_steps")):
        row = _dict(item)
        lines.append(
            "| "
            f"{_int(row.get('depth', 0))} | "
            f"`{_markdown_cell(row.get('candidate_id'))}` | "
            f"`{_markdown_cell(row.get('parent_candidate_id') or 'none')}` | "
            f"`{_markdown_cell(row.get('parent_failure_type') or 'none')}` | "
            f"`{_markdown_cell(row.get('rule_id'))}` | "
            f"`{_markdown_cell(row.get('variant') or 'none')}` | "
            f"{str(bool(row.get('success', False))).lower()} | "
            f"`{_markdown_cell(row.get('failure_type') or 'none')}` | "
            f"{_markdown_cell(row.get('feedback_summary') or '')} |"
        )
    if not _list(payload.get("reflection_steps")):
        lines.append("| 0 | none | none | none | none | none | false | none | none |")
    lines.extend(
        [
            "",
            "## Final Outcome",
            "",
            f"- Repair Ready: {str(bool(final_outcome.get('repair_ready', False))).lower()}",
            f"- Regression Ready: {str(bool(final_outcome.get('regression_ready', False))).lower()}",
            f"- Best Candidate: `{_markdown_cell(final_outcome.get('best_candidate_id') or 'none')}`",
            f"- Best Rule: `{_markdown_cell(final_outcome.get('best_candidate_rule_id') or 'none')}`",
            f"- Best Variant: `{_markdown_cell(final_outcome.get('best_candidate_variant') or 'none')}`",
            f"- Best Depth: {_int(final_outcome.get('best_patch_depth', 0))}",
            f"- Best Parent: `{_markdown_cell(final_outcome.get('best_patch_parent_candidate_id') or 'none')}`",
            "",
            "## Failure Type Counts",
            "",
        ]
    )
    counts = _dict(payload.get("failure_type_counts"))
    for failure_type, count in sorted(counts.items()):
        lines.append(f"- `{_markdown_cell(failure_type)}`: {_int(count)}")
    if not counts:
        lines.append("- none")
    lines.extend(["", "## Reflection Failure Taxonomy", ""])
    lines.append(
        "- Initial Failure Types: "
        f"{_format_counts(_dict(payload.get('initial_failure_type_counts')))}"
    )
    lines.append(
        "- Initial Reflection Strategies: "
        f"{_format_counts(_dict(payload.get('initial_strategy_counts')))}"
    )
    lines.append(
        "- Reflection Failure Types: "
        f"{_format_counts(_dict(payload.get('reflection_failure_type_counts')))}"
    )
    lines.append(
        "- Reflection Parent Failure Types: "
        f"{_format_counts(_dict(payload.get('reflection_parent_failure_type_counts')))}"
    )
    lines.append(
        "- Successful Reflection Parent Failure Types: "
        f"{_format_counts(_dict(payload.get('successful_reflection_parent_failure_type_counts')))}"
    )
    lines.extend(["", "## Recommended Reflection Strategies", ""])
    strategies = _list(payload.get("recommended_reflection_strategies"))
    if strategies:
        lines.append("| Strategy | Failure Types | Action |")
        lines.append("| --- | --- | --- |")
        for item in strategies:
            row = _dict(item)
            lines.append(
                "| "
                f"`{_markdown_cell(row.get('id') or '')}` | "
                f"`{_markdown_cell(', '.join(str(value) for value in _list(row.get('failure_types'))))}` | "
                f"{_markdown_cell(row.get('action') or '')} |"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    return "\n".join(lines) + "\n"


def write_repository_test_reflection_trace_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_reflection_trace.json"
    markdown_path = root / "repository_test_reflection_trace.md"
    alias_json_path = root / "reflection_trace.json"
    alias_markdown_path = root / "reflection_trace.md"
    json_text = json.dumps(payload, indent=2, ensure_ascii=False)
    markdown_text = render_repository_test_reflection_trace_markdown(payload)
    json_path.write_text(
        json_text,
        encoding="utf-8",
    )
    markdown_path.write_text(
        markdown_text,
        encoding="utf-8",
    )
    alias_json_path.write_text(
        json_text,
        encoding="utf-8",
    )
    alias_markdown_path.write_text(
        markdown_text,
        encoding="utf-8",
    )
    return {
        "repository_test_reflection_trace_json": str(json_path),
        "repository_test_reflection_trace_markdown": str(markdown_path),
        "reflection_trace_json": str(alias_json_path),
        "reflection_trace_markdown": str(alias_markdown_path),
    }


def _common_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "patch_validation_status": str(payload.get("status") or ""),
        "patch_validation_reason": str(payload.get("reason") or ""),
        "reflection_enabled": bool(payload.get("reflection_enabled", False)),
        "reflection_mode": str(payload.get("reflection_mode") or ""),
        "reflection_refiner_status": str(
            payload.get("reflection_refiner_status") or ""
        ),
        "reflection_refiner_reason": str(
            payload.get("reflection_refiner_reason") or ""
        ),
        "llm_reflection_config_audit": _dict(
            payload.get("llm_reflection_config_audit")
        ),
        "reflection_rounds": _int(payload.get("reflection_rounds", 0)),
        "reflection_width": _int(payload.get("reflection_width", 0)),
        "reflection_candidate_count": _int(
            payload.get("reflection_candidate_count", 0)
        ),
        "successful_reflection_candidate_count": _int(
            payload.get("successful_reflection_candidate_count", 0)
        ),
        "regression_reflection_candidate_count": _int(
            payload.get("regression_reflection_candidate_count", 0)
        ),
        "successful_regression_reflection_candidate_count": _int(
            payload.get("successful_regression_reflection_candidate_count", 0)
        ),
        "max_depth_executed": _int(payload.get("max_depth_executed", 0)),
        "executed_count": _int(payload.get("executed_count", 0)),
        "success_count": _int(payload.get("success_count", 0)),
        "failed_count": _int(payload.get("failed_count", 0)),
        "failure_type_counts": _dict(payload.get("failure_type_counts")),
    }


def _counts_by_field(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts = Counter(
        str(_dict(row).get(field) or "unknown")
        for row in rows
        if str(_dict(row).get(field) or "")
    )
    return dict(sorted(counts.items()))


def _final_outcome(payload: dict[str, Any]) -> dict[str, Any]:
    best_patch = _dict(payload.get("best_patch"))
    return {
        "repair_ready": bool(payload.get("repair_ready", False)),
        "regression_ready": bool(payload.get("regression_ready", False)),
        "repair_validation_scope": str(payload.get("repair_validation_scope") or ""),
        "best_candidate_id": str(payload.get("best_candidate_id") or ""),
        "best_candidate_rule_id": str(payload.get("best_candidate_rule_id") or ""),
        "best_candidate_variant": str(payload.get("best_candidate_variant") or ""),
        "best_candidate_success": bool(
            payload.get("best_candidate_success", False)
        ),
        "best_patch_depth": _int(best_patch.get("depth", 0)),
        "best_patch_parent_candidate_id": str(
            best_patch.get("parent_candidate_id") or ""
        ),
        "best_patch_relative_file_path": str(
            best_patch.get("relative_file_path") or ""
        ),
        "best_patch_has_diff": bool(best_patch.get("diff")),
    }


def _reflection_step(
    row: dict[str, Any],
    result_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    trace = _trace_row(row)
    parent_id = str(row.get("parent_candidate_id") or "")
    parent = _dict(result_by_id.get(parent_id))
    feedback = _dict(row.get("execution_feedback"))
    trace.update(
        {
            "parent_candidate_id": parent_id,
            "parent_failure_type": str(parent.get("failure_type") or ""),
            "parent_failure_reason": str(parent.get("failure_reason") or ""),
            "feedback_summary": _feedback_summary(feedback),
            "refinement_hints": _list(feedback.get("refinement_hints")),
            "recoverability": str(feedback.get("recoverability") or ""),
        }
    )
    return trace


def _trace_row(row: dict[str, Any]) -> dict[str, Any]:
    feedback = _dict(row.get("execution_feedback"))
    strategy = _reflection_strategy_for_failure_type(
        str(row.get("failure_type") or "")
    )
    return {
        "candidate_id": str(row.get("candidate_id") or ""),
        "target_function_id": str(row.get("target_function_id") or ""),
        "target_function_name": str(row.get("target_function_name") or ""),
        "relative_file_path": str(row.get("relative_file_path") or ""),
        "rule_id": str(row.get("rule_id") or ""),
        "variant": str(row.get("variant") or ""),
        "depth": _int(row.get("depth", 0)),
        "success": bool(row.get("success", False)),
        "returncode": _int(row.get("returncode", 0)),
        "passed": _int(row.get("passed", 0)),
        "failed": _int(row.get("failed", 0)),
        "timeout": bool(row.get("timeout", False)),
        "failure_type": str(row.get("failure_type") or ""),
        "failure_reason": str(row.get("failure_reason") or ""),
        "score": _float(row.get("score", 0.0)),
        "feedback_score": _float(row.get("feedback_score", 0.0)),
        "failure_stage": str(feedback.get("failure_stage") or ""),
        "reflection_strategy_id": str(strategy.get("id") or ""),
        "reflection_strategy_action": str(strategy.get("action") or ""),
        "reflection_strategy_reason": str(strategy.get("reason") or ""),
        "stdout_preview": str(row.get("stdout_preview") or ""),
        "stderr_preview": str(row.get("stderr_preview") or ""),
        "traceback_preview": str(row.get("traceback_preview") or ""),
    }


def _feedback_summary(feedback: dict[str, Any]) -> str:
    if not feedback:
        return ""
    hints = _list(feedback.get("refinement_hints"))
    hint_text = "; ".join(str(item) for item in hints[:2] if str(item))
    parts = [
        str(feedback.get("failure_stage") or ""),
        str(feedback.get("recoverability") or ""),
        hint_text,
    ]
    return " | ".join(part for part in parts if part)


def _next_actions(
    payload: dict[str, Any],
    initial_failures: list[dict[str, Any]],
    reflection_steps: list[dict[str, Any]],
) -> list[str]:
    if bool(payload.get("repair_ready", False)):
        return ["Review and apply the verified repository_test_repair.patch."]
    if reflection_steps and not any(bool(row.get("success", False)) for row in reflection_steps):
        return _unique(
            [
                "Increase reflection rounds or width, switch reflection_mode to llm, or expand patch candidates.",
                *_strategy_actions(initial_failures),
            ]
        )
    if initial_failures and not bool(payload.get("reflection_enabled", False)):
        return _unique(
            [
                "Enable reflection_rounds or a supported reflection refiner.",
                *_strategy_actions(initial_failures),
            ]
        )
    if str(payload.get("status") or "") == "skipped":
        return [str(action) for action in _list(payload.get("next_actions"))]
    return _unique(
        [
            "Inspect failed candidates and expand supported repair rules.",
            *_strategy_actions(initial_failures),
        ]
    )


def _skipped(reason: str, message: str) -> dict[str, Any]:
    return {
        "status": "skipped",
        "reason": reason,
        "message": message,
        "patch_validation_status": "",
        "patch_validation_reason": "",
        "reflection_enabled": False,
        "reflection_mode": "",
        "reflection_refiner_status": "",
        "reflection_refiner_reason": "",
        "reflection_rounds": 0,
        "reflection_width": 0,
        "reflection_candidate_count": 0,
        "successful_reflection_candidate_count": 0,
        "max_depth_executed": 0,
        "executed_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "failure_type_counts": {},
        "initial_failure_type_counts": {},
        "initial_strategy_counts": {},
        "recommended_reflection_strategies": [],
        "reflection_failure_type_counts": {},
        "reflection_parent_failure_type_counts": {},
        "successful_reflection_parent_failure_type_counts": {},
        "initial_failures": [],
        "reflection_steps": [],
        "final_outcome": {},
        "next_actions": [],
    }


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
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _reflection_strategy_for_failure_type(failure_type: str) -> dict[str, str]:
    normalized = str(failure_type or "").strip().lower()
    strategies = {
        "syntax_error": {
            "id": "regenerate_ast_valid_patch",
            "action": "Regenerate an AST-valid patch and run syntax parsing before sandbox validation.",
            "reason": "The candidate failed before tests could run because the patched source is syntactically invalid.",
        },
        "patch_apply_error": {
            "id": "regenerate_minimal_applicable_diff",
            "action": "Regenerate a minimal diff against the current checkout and verify the original source block still matches.",
            "reason": "The patch could not be applied to the checkout, so reflection must realign the diff with current source.",
        },
        "test_failure": {
            "id": "refine_logic_against_failing_assertion",
            "action": "Compare the failing assertion or nodeid against the patched behavior and refine only the localized logic.",
            "reason": "The patch applied and tests ran, but behavior still violates the failing test expectation.",
        },
        "timeout": {
            "id": "repair_loop_or_narrow_timeout_scope",
            "action": "Inspect loop bounds or recursion termination and rerun a narrower pytest scope before broad validation.",
            "reason": "The candidate timed out, so reflection should reduce nontermination risk before retrying.",
        },
        "import_error": {
            "id": "separate_import_environment_from_logic_patch",
            "action": "Determine whether the failure is an environment dependency or a patch-introduced import change before mutating logic.",
            "reason": "Import failures can be caused by environment setup or by an invalid patch import path.",
        },
        "attribute_error": {
            "id": "repair_runtime_contract_mismatch",
            "action": "Use traceback receiver/value evidence to repair the object contract without widening patch scope.",
            "reason": "The candidate created or exposed an attribute contract mismatch at runtime.",
        },
        "type_error": {
            "id": "repair_runtime_contract_mismatch",
            "action": "Use traceback argument/value evidence to repair the type contract without widening patch scope.",
            "reason": "The candidate created or exposed a type contract mismatch at runtime.",
        },
        "runtime_error": {
            "id": "repair_runtime_exception_path",
            "action": "Use traceback frames to refine the exceptional path while keeping the patch inside the localized function.",
            "reason": "The candidate raised a runtime exception outside simple assertion mismatch.",
        },
        "execution_error": {
            "id": "inspect_sandbox_execution_error",
            "action": "Inspect sandbox stdout/stderr and rerun only after the execution setup or command issue is isolated.",
            "reason": "The sandbox command failed without a precise Python failure category.",
        },
        "unknown_failure": {
            "id": "inspect_execution_feedback",
            "action": "Inspect stdout, stderr, traceback, and execution feedback before choosing a new repair rule.",
            "reason": "The failure type is not specific enough for a targeted automatic refinement.",
        },
    }
    return strategies.get(
        normalized,
        {
            "id": "inspect_execution_feedback",
            "action": "Inspect stdout, stderr, traceback, and execution feedback before choosing a new repair rule.",
            "reason": f"No specialized reflection strategy is registered for {normalized or 'unknown'}.",
        },
    )


def _recommended_strategies(
    initial_failures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in initial_failures:
        strategy_id = str(row.get("reflection_strategy_id") or "")
        if not strategy_id:
            continue
        item = by_id.setdefault(
            strategy_id,
            {
                "id": strategy_id,
                "action": str(row.get("reflection_strategy_action") or ""),
                "reason": str(row.get("reflection_strategy_reason") or ""),
                "failure_types": [],
                "candidate_count": 0,
            },
        )
        item["candidate_count"] = _int(item.get("candidate_count", 0)) + 1
        failure_type = str(row.get("failure_type") or "")
        failure_types = item["failure_types"]
        if failure_type and failure_type not in failure_types:
            failure_types.append(failure_type)
    return sorted(
        by_id.values(),
        key=lambda item: (
            -_int(item.get("candidate_count", 0)),
            str(item.get("id") or ""),
        ),
    )


def _strategy_actions(rows: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    for strategy in _recommended_strategies(rows):
        action = str(strategy.get("action") or "")
        if action:
            actions.append(action)
    return actions


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(
        f"{key}={_int(value)}"
        for key, value in sorted(counts.items())
    )
