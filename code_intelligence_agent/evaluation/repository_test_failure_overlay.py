from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

from code_intelligence_agent.agents.bug_detector import RuleBasedBugDetector
from code_intelligence_agent.core.models import BugFinding, CodeEntity, RepoParseResult
from code_intelligence_agent.core.repo_parser import DEFAULT_EXCLUDED_DIRS, RepoParser
from code_intelligence_agent.evaluation.repository_test_dynamic_evidence import (
    build_repository_test_dynamic_evidence,
)
from code_intelligence_agent.evaluation.repository_test_execution_result import (
    _diagnose_execution_result,
    _extract_count,
    _preview,
)


SUPPORTED_OVERLAY_RULES = {
    "possible_index_overrun",
    "missing_len_zero_guard",
    "dict_missing_key_guard",
    "inplace_api_return_value",
    "stringified_numeric_value",
    "iterator_double_consumption",
    "always_true_len_check",
    "mutable_default_arg",
    "broad_exception_pass",
    "identity_comparison_literal",
    "enumerate_start_zero_counter",
    "inverted_empty_guard",
}

OVERLAY_RULE_TRIGGER_PRIORS = {
    "possible_index_overrun": 0.95,
    "missing_len_zero_guard": 0.92,
    "dict_missing_key_guard": 0.90,
    "inplace_api_return_value": 0.86,
    "stringified_numeric_value": 0.84,
    "iterator_double_consumption": 0.82,
    "always_true_len_check": 0.81,
    "mutable_default_arg": 0.80,
    "broad_exception_pass": 0.78,
    "identity_comparison_literal": 0.76,
    "enumerate_start_zero_counter": 0.74,
    "inverted_empty_guard": 0.72,
}


def build_repository_test_failure_overlay(
    *,
    repository_root: str | Path | None,
    output_dir: str | Path,
    timeout: int = 20,
    candidate_limit: int = 5,
    analysis_paths: list[str | Path] | None = None,
    parser: RepoParser | None = None,
    detector: RuleBasedBugDetector | None = None,
    runner=None,
) -> dict[str, Any]:
    if repository_root is None:
        return _skipped(
            reason="repository_root_missing",
            message="Failure overlay requires a local repository checkout.",
        )
    root = Path(repository_root)
    if not root.exists() or not root.is_dir():
        return _skipped(
            reason="repository_root_missing",
            message="Repository test root does not exist or is not a directory.",
            repository_root=str(root),
        )

    output_root = Path(output_dir)
    overlay_root = output_root / "repository_test_failure_overlay_checkout"
    if overlay_root.exists():
        shutil.rmtree(overlay_root)

    analysis_scope = _build_analysis_scope(root, analysis_paths)
    active_parser = parser or RepoParser()
    parsed = _parse_analysis_scope(
        root=root,
        parser=active_parser,
        analysis_scope=analysis_scope,
    )
    findings = (detector or RuleBasedBugDetector()).detect(parsed.functions)
    functions_by_id = {function.id: function for function in parsed.functions}
    case_specs, candidate_audit = _overlay_case_selection(
        root=root,
        findings=findings,
        functions_by_id=functions_by_id,
        limit=candidate_limit,
    )

    output_root.mkdir(parents=True, exist_ok=True)
    _copy_repository(root, overlay_root)

    attempts = []
    run = runner or subprocess.run
    for spec in case_specs:
        test_path = _write_overlay_test(overlay_root, spec)
        nodeid = f"{_relative_posix(test_path, overlay_root)}::{spec['test_name']}"
        command = f"python -m pytest -q {nodeid}"
        execution_result = _run_overlay_test(
            command=command,
            command_args=[sys.executable, "-m", "pytest", "-q", nodeid],
            cwd=overlay_root,
            timeout=timeout,
            runner=run,
        )
        dynamic_evidence = build_repository_test_dynamic_evidence(
            execution_result,
            None,
            execution_plan={"recommended_execution_command": command},
        )
        dynamic_evidence = _overlay_dynamic_evidence_with_case_context(
            dynamic_evidence,
            spec,
        )
        expected_exception = str(spec.get("expected_exception") or "")
        expected_triggered = (
            str(dynamic_evidence.get("evidence_level") or "") == "failing_tests"
            and expected_exception in _combined_output(execution_result)
        )
        attempt = {
            "case": spec,
            "test_path": str(test_path),
            "nodeid": nodeid,
            "command": command,
            "execution_result": execution_result,
            "dynamic_evidence": dynamic_evidence,
            "expected_exception_triggered": expected_triggered,
        }
        attempts.append(attempt)
        if expected_triggered and bool(
            dynamic_evidence.get("usable_for_localization", False)
        ):
            return {
                "status": "pass",
                "reason": "overlay_dynamic_evidence_generated",
                "message": (
                    "A controlled pytest overlay reproduced a static finding as "
                    "usable failing-test evidence."
                ),
                "repository_root": str(root),
                "overlay_root": str(overlay_root),
                "analysis_scope": analysis_scope,
                "static_finding_count": len(findings),
                "supported_candidate_count": len(case_specs),
                "attempted_case_count": len(attempts),
                "selected_case": spec,
                "recommended_validation_command": command,
                "execution_result": execution_result,
                "dynamic_evidence": dynamic_evidence,
                "attempts": attempts,
                "strategy_summary": _overlay_strategy_summary(
                    findings=findings,
                    case_specs=case_specs,
                    attempts=attempts,
                    selected_case=spec,
                    candidate_limit=candidate_limit,
                    candidate_audit=candidate_audit,
                ),
                "next_actions": [
                    "Use overlay_root as the repository root for fault localization.",
                    "Validate generated patches with the overlay failing-test command.",
                ],
            }

    if not case_specs:
        return _skipped(
            reason="no_supported_overlay_candidates",
            message=(
                "No top-level function or no-argument class-method findings "
                "matched the currently supported failure-overlay rules."
            ),
            repository_root=str(root),
            overlay_root=str(overlay_root),
            analysis_scope=analysis_scope,
            static_finding_count=len(findings),
            strategy_summary=_overlay_strategy_summary(
                findings=findings,
                case_specs=case_specs,
                attempts=attempts,
                selected_case=None,
                candidate_limit=candidate_limit,
                candidate_audit=candidate_audit,
            ),
        )
    return {
        "status": "warning",
        "reason": "overlay_tests_did_not_trigger_expected_failure",
        "message": (
            "Supported overlay tests were generated, but none produced the "
            "expected deterministic failure signal."
        ),
        "repository_root": str(root),
        "overlay_root": str(overlay_root),
        "analysis_scope": analysis_scope,
        "static_finding_count": len(findings),
        "supported_candidate_count": len(case_specs),
        "attempted_case_count": len(attempts),
        "selected_case": {},
        "recommended_validation_command": "",
        "execution_result": attempts[-1]["execution_result"] if attempts else {},
        "dynamic_evidence": attempts[-1]["dynamic_evidence"] if attempts else {},
        "attempts": attempts,
        "strategy_summary": _overlay_strategy_summary(
            findings=findings,
            case_specs=case_specs,
            attempts=attempts,
            selected_case=None,
            candidate_limit=candidate_limit,
            candidate_audit=candidate_audit,
        ),
        "next_actions": [
            "Inspect overlay attempts before using synthetic dynamic evidence.",
            "Provide a hand-written failing test if the generated overlay is too weak.",
        ],
    }


def _overlay_strategy_summary(
    *,
    findings: list[BugFinding],
    case_specs: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    selected_case: dict[str, Any] | None,
    candidate_limit: int,
    candidate_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected = _dict(selected_case)
    audit = _dict(candidate_audit)
    selected_rank = 0
    if selected:
        for index, case in enumerate(case_specs, start=1):
            if case == selected:
                selected_rank = index
                break
    attempted_cases = [_dict(_dict(attempt).get("case")) for attempt in attempts]
    triggered_cases = [
        _dict(_dict(attempt).get("case"))
        for attempt in attempts
        if bool(_dict(attempt).get("expected_exception_triggered", False))
    ]
    rejection_counts = _dict(audit.get("candidate_rejection_counts"))
    rejection_recommendations = _candidate_rejection_recommendations(rejection_counts)
    next_overlay_extension = (
        dict(rejection_recommendations[0]) if rejection_recommendations else {}
    )
    next_actionable_overlay_extension = _next_actionable_overlay_extension(
        rejection_recommendations
    )
    return {
        "policy": "rule_diverse_confidence_ordered_first_triggering_candidate",
        "candidate_limit": max(1, _int(candidate_limit)),
        "supported_rule_count": len(SUPPORTED_OVERLAY_RULES),
        "supported_rules": sorted(SUPPORTED_OVERLAY_RULES),
        "static_finding_rule_counts": _finding_rule_counts(findings),
        "supported_static_finding_rule_counts": _finding_rule_counts(
            [
                finding
                for finding in findings
                if finding.rule_id in SUPPORTED_OVERLAY_RULES
            ]
        ),
        "candidate_rule_counts": _case_rule_counts(case_specs),
        "candidate_callable_kind_counts": _case_callable_kind_counts(case_specs),
        "candidate_rejection_count": _int(audit.get("candidate_rejection_count", 0)),
        "candidate_rejection_counts": rejection_counts,
        "candidate_rejection_rule_counts": _dict(
            audit.get("candidate_rejection_rule_counts")
        ),
        "candidate_rejection_examples": _list(
            audit.get("candidate_rejection_examples")
        ),
        "dominant_candidate_rejection_reason": str(
            next_overlay_extension.get("reason") or ""
        ),
        "dominant_candidate_rejection_count": _int(
            next_overlay_extension.get("count", 0)
        ),
        "candidate_rejection_recommendations": rejection_recommendations,
        "next_overlay_extension": next_overlay_extension,
        "next_actionable_overlay_extension": next_actionable_overlay_extension,
        "candidate_score_preview": _candidate_score_preview(case_specs),
        "selected_score": _float(selected.get("overlay_score", 0.0)),
        "selected_score_breakdown": _dict(selected.get("score_breakdown")),
        "average_candidate_score": _average_case_score(case_specs),
        "attempted_rule_counts": _case_rule_counts(attempted_cases),
        "triggered_rule_counts": _case_rule_counts(triggered_cases),
        "selected_rule_id": str(selected.get("rule_id") or ""),
        "selected_function": str(selected.get("function_name") or ""),
        "selected_candidate_rank": selected_rank,
        "attempted_case_count": len(attempts),
        "supported_candidate_count": len(case_specs),
    }


def render_repository_test_failure_overlay_markdown(payload: dict[str, Any]) -> str:
    selected = _dict(payload.get("selected_case"))
    dynamic_evidence = _dict(payload.get("dynamic_evidence"))
    analysis_scope = _dict(payload.get("analysis_scope"))
    lines = [
        "# Repository Test Failure Overlay",
        "",
        f"- Status: `{_markdown_cell(payload.get('status', ''))}`",
        f"- Reason: `{_markdown_cell(payload.get('reason', ''))}`",
        f"- Repository Root: `{_markdown_cell(payload.get('repository_root') or 'none')}`",
        f"- Overlay Root: `{_markdown_cell(payload.get('overlay_root') or 'none')}`",
        f"- Scoped Analysis: {str(bool(analysis_scope.get('enabled', False))).lower()}",
        (
            "- Analysis Files: "
            f"`{_markdown_cell(_format_list(_list(analysis_scope.get('existing_files'))))}`"
        ),
        (
            "- Missing Analysis Paths: "
            f"`{_markdown_cell(_format_list(_list(analysis_scope.get('missing_paths'))))}`"
        ),
        f"- Static Findings: {_int(payload.get('static_finding_count', 0))}",
        f"- Supported Candidates: {_int(payload.get('supported_candidate_count', 0))}",
        f"- Attempted Cases: {_int(payload.get('attempted_case_count', 0))}",
        (
            "- Recommended Validation Command: "
            f"`{_markdown_cell(payload.get('recommended_validation_command') or 'none')}`"
        ),
        "",
        "## Strategy Summary",
        "",
        f"- Policy: `{_markdown_cell(_dict(payload.get('strategy_summary')).get('policy') or 'none')}`",
        (
            "- Static Rule Counts: "
            f"`{_markdown_cell(_format_counts(_dict(_dict(payload.get('strategy_summary')).get('static_finding_rule_counts'))))}`"
        ),
        (
            "- Candidate Rule Counts: "
            f"`{_markdown_cell(_format_counts(_dict(_dict(payload.get('strategy_summary')).get('candidate_rule_counts'))))}`"
        ),
        (
            "- Candidate Rejection Counts: "
            f"`{_markdown_cell(_format_counts(_dict(_dict(payload.get('strategy_summary')).get('candidate_rejection_counts'))))}`"
        ),
        (
            "- Candidate Rejection Examples: "
            f"`{_markdown_cell(_format_rejection_examples(_list(_dict(payload.get('strategy_summary')).get('candidate_rejection_examples'))))}`"
        ),
        (
            "- Dominant Candidate Rejection: "
            f"`{_markdown_cell(_format_dominant_rejection(_dict(payload.get('strategy_summary'))))}`"
        ),
        (
            "- Next Overlay Extension: "
            f"`{_markdown_cell(_format_next_overlay_extension(_dict(payload.get('strategy_summary'))))}`"
        ),
        (
            "- Next Actionable Overlay Extension: "
            f"`{_markdown_cell(_format_next_actionable_overlay_extension(_dict(payload.get('strategy_summary'))))}`"
        ),
        (
            "- Candidate Score Preview: "
            f"`{_markdown_cell(_format_score_preview(_list(_dict(payload.get('strategy_summary')).get('candidate_score_preview'))))}`"
        ),
        (
            "- Selected Score: "
            f"{_float(_dict(payload.get('strategy_summary')).get('selected_score', 0.0)):.4f}"
        ),
        (
            "- Average Candidate Score: "
            f"{_float(_dict(payload.get('strategy_summary')).get('average_candidate_score', 0.0)):.4f}"
        ),
        (
            "- Attempted Rule Counts: "
            f"`{_markdown_cell(_format_counts(_dict(_dict(payload.get('strategy_summary')).get('attempted_rule_counts'))))}`"
        ),
        (
            "- Triggered Rule Counts: "
            f"`{_markdown_cell(_format_counts(_dict(_dict(payload.get('strategy_summary')).get('triggered_rule_counts'))))}`"
        ),
        (
            "- Selected Candidate Rank: "
            f"{_int(_dict(payload.get('strategy_summary')).get('selected_candidate_rank', 0))}"
        ),
        "",
        "## Selected Case",
        "",
        f"- Rule: `{_markdown_cell(selected.get('rule_id') or 'none')}`",
        f"- Function: `{_markdown_cell(selected.get('function_name') or 'none')}`",
        f"- Callable Kind: `{_markdown_cell(selected.get('callable_kind') or 'none')}`",
        f"- Class: `{_markdown_cell(selected.get('class_name') or 'none')}`",
        f"- Relative File: `{_markdown_cell(selected.get('relative_file_path') or 'none')}`",
        (
            "- Public API Evidence: "
            f"`{_markdown_cell(_format_public_api_evidence(_dict(selected.get('public_api_evidence'))))}`"
        ),
        f"- Expected Exception: `{_markdown_cell(selected.get('expected_exception') or 'none')}`",
        f"- Overlay Score: {_float(selected.get('overlay_score', 0.0)):.4f}",
        f"- Test Name: `{_markdown_cell(selected.get('test_name') or 'none')}`",
        "",
        "## Dynamic Evidence",
        "",
        f"- Evidence Level: `{_markdown_cell(dynamic_evidence.get('evidence_level') or 'none')}`",
        (
            "- Usable For Localization: "
            f"{str(bool(dynamic_evidence.get('usable_for_localization', False))).lower()}"
        ),
        (
            "- Failing Tests: "
            f"{_int(dynamic_evidence.get('failing_test_count', 0))}"
        ),
        "",
        "## Attempts",
        "",
        "| Index | Rule | Function | Expected Exception | Triggered | Command |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    for index, item in enumerate(_list(payload.get("attempts")), start=1):
        row = _dict(item)
        case = _dict(row.get("case"))
        lines.append(
            "| "
            f"{index} | "
            f"`{_markdown_cell(case.get('rule_id') or '')}` | "
            f"`{_markdown_cell(case.get('function_name') or '')}` | "
            f"`{_markdown_cell(case.get('expected_exception') or '')}` | "
            f"{str(bool(row.get('expected_exception_triggered', False))).lower()} | "
            f"`{_markdown_cell(row.get('command') or '')}` |"
        )
    if not _list(payload.get("attempts")):
        lines.append("| 0 | none | none | none | false | none |")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    return "\n".join(lines)


def write_repository_test_failure_overlay_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_failure_overlay.json"
    markdown_path = root / "repository_test_failure_overlay.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_failure_overlay_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_failure_overlay_json": str(json_path),
        "repository_test_failure_overlay_markdown": str(markdown_path),
    }


def _overlay_dynamic_evidence_with_case_context(
    dynamic_evidence: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any]:
    evidence = dict(_dict(dynamic_evidence))
    public_api_evidence = _dict(spec.get("public_api_evidence"))
    overlay_case_context = {
        "rule_id": str(spec.get("rule_id") or ""),
        "function_name": str(spec.get("function_name") or ""),
        "qualified_name": str(spec.get("qualified_name") or ""),
        "callable_kind": str(spec.get("callable_kind") or ""),
        "relative_file_path": str(spec.get("relative_file_path") or ""),
        "expected_exception": str(spec.get("expected_exception") or ""),
        "public_api_evidence": public_api_evidence,
    }
    evidence["overlay_case_context"] = overlay_case_context
    if public_api_evidence:
        evidence["public_api_evidence"] = public_api_evidence
    return evidence


def _overlay_case_specs(
    *,
    root: Path,
    findings: list[BugFinding],
    functions_by_id: dict[str, CodeEntity],
    limit: int,
) -> list[dict[str, Any]]:
    selected, _audit = _overlay_case_selection(
        root=root,
        findings=findings,
        functions_by_id=functions_by_id,
        limit=limit,
    )
    return selected


def _build_analysis_scope(
    root: Path,
    analysis_paths: list[str | Path] | None,
) -> dict[str, Any]:
    requested = [
        str(path).strip()
        for path in list(analysis_paths or [])
        if str(path).strip()
    ]
    if not requested:
        return {
            "enabled": False,
            "requested_path_count": 0,
            "requested_paths": [],
            "existing_file_count": 0,
            "existing_files": [],
            "missing_path_count": 0,
            "missing_paths": [],
        }

    root_resolved = root.resolve()
    existing_files: list[Path] = []
    missing_paths: list[str] = []
    seen_files: set[str] = set()
    for requested_path in requested:
        resolved_files = _resolve_analysis_path(
            root=root,
            root_resolved=root_resolved,
            requested_path=requested_path,
        )
        if not resolved_files:
            missing_paths.append(requested_path)
            continue
        for file_path in resolved_files:
            key = file_path.resolve().as_posix()
            if key in seen_files:
                continue
            seen_files.add(key)
            existing_files.append(file_path)

    return {
        "enabled": True,
        "requested_path_count": len(requested),
        "requested_paths": requested,
        "existing_file_count": len(existing_files),
        "existing_files": [_relative_posix(path, root) for path in existing_files],
        "missing_path_count": len(missing_paths),
        "missing_paths": missing_paths,
    }


def _resolve_analysis_path(
    *,
    root: Path,
    root_resolved: Path,
    requested_path: str,
) -> list[Path]:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", requested_path):
        return []
    candidates: list[Path] = []
    raw_path = Path(requested_path)
    if raw_path.is_absolute():
        candidates.append(raw_path)
    normalized = requested_path.replace("\\", "/").lstrip("/")
    if normalized:
        candidates.append(root / normalized)

    resolved_files: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            continue
        if not _is_under_root(resolved, root_resolved):
            continue
        if resolved.is_file() and resolved.suffix == ".py":
            key = resolved.as_posix()
            if key not in seen:
                seen.add(key)
                resolved_files.append(resolved)
        elif resolved.is_dir():
            for file_path in sorted(resolved.rglob("*.py")):
                if any(part in DEFAULT_EXCLUDED_DIRS for part in file_path.parts):
                    continue
                key = file_path.resolve().as_posix()
                if key not in seen:
                    seen.add(key)
                    resolved_files.append(file_path)
    return resolved_files


def _is_under_root(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _parse_analysis_scope(
    *,
    root: Path,
    parser: RepoParser,
    analysis_scope: dict[str, Any],
) -> RepoParseResult:
    if not bool(analysis_scope.get("enabled", False)):
        return parser.parse(root)

    files = []
    for relative_file in _list(analysis_scope.get("existing_files")):
        file_path = root / str(relative_file)
        if not file_path.exists() or not file_path.is_file():
            continue
        files.extend(parser.parse(file_path).files)
    return RepoParseResult(root_path=str(root), files=files)


def _overlay_case_selection(
    *,
    root: Path,
    findings: list[BugFinding],
    functions_by_id: dict[str, CodeEntity],
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates = []
    audit: dict[str, Any] = {
        "candidate_rejection_count": 0,
        "candidate_rejection_counts": {},
        "candidate_rejection_rule_counts": {},
        "candidate_rejection_examples": [],
    }
    for finding in sorted(
        findings,
        key=lambda item: (
            _clamp_float(item.confidence),
            OVERLAY_RULE_TRIGGER_PRIORS.get(item.rule_id, 0.50),
            item.rule_id,
        ),
        reverse=True,
    ):
        if finding.rule_id not in SUPPORTED_OVERLAY_RULES:
            continue
        function = functions_by_id.get(finding.function_id)
        if function is None:
            _record_overlay_rejection(
                audit,
                finding=finding,
                function=None,
                reason="function_not_found",
            )
            continue
        unsupported_reason = _unsupported_callable_reason(function)
        if unsupported_reason:
            spec = _case_spec_for_unsupported_finding(
                root=root,
                finding=finding,
                function=function,
            )
            if spec is not None:
                candidates.append(spec)
                continue
            _record_overlay_rejection(
                audit,
                finding=finding,
                function=function,
                reason=(
                    _unsupported_callable_overlay_rejection_reason(
                        finding=finding,
                        function=function,
                    )
                    or unsupported_reason
                ),
            )
            continue
        spec = _case_spec_for_finding(root=root, finding=finding, function=function)
        if spec is None:
            _record_overlay_rejection(
                audit,
                finding=finding,
                function=function,
                reason=_overlay_rejection_reason(
                    root=root,
                    finding=finding,
                    function=function,
                ),
            )
            continue
        candidates.append(spec)
    candidates.sort(
        key=lambda item: (
            _float(item.get("overlay_score", 0.0)),
            _float(_dict(item.get("score_breakdown")).get("static_confidence", 0.0)),
            str(item.get("rule_id") or ""),
            str(item.get("function_name") or ""),
        ),
        reverse=True,
    )
    return _select_rule_diverse_specs(candidates, limit=max(1, limit)), audit


def _record_overlay_rejection(
    audit: dict[str, Any],
    *,
    finding: BugFinding,
    function: CodeEntity | None,
    reason: str,
) -> None:
    reason = reason or "rule_specific_oracle_shape_unsupported"
    counts = _dict(audit.get("candidate_rejection_counts"))
    counts[reason] = _int(counts.get(reason, 0)) + 1
    audit["candidate_rejection_counts"] = counts

    rule_counts = _dict(audit.get("candidate_rejection_rule_counts"))
    rule_counts[finding.rule_id] = _int(rule_counts.get(finding.rule_id, 0)) + 1
    audit["candidate_rejection_rule_counts"] = rule_counts
    audit["candidate_rejection_count"] = _int(audit.get("candidate_rejection_count", 0)) + 1

    examples = _list(audit.get("candidate_rejection_examples"))
    if len(examples) >= 10:
        return
    examples.append(
        {
            "rule_id": finding.rule_id,
            "function_name": function.name if function is not None else finding.function_id,
            "qualified_name": str(
                _dict(function.metadata).get("qualified_name") if function else ""
            ),
            "file_path": str(function.file_path) if function is not None else "",
            "reason": reason,
            "confidence": _float(finding.confidence),
        }
    )
    audit["candidate_rejection_examples"] = examples


def _overlay_rejection_reason(
    *,
    root: Path,
    finding: BugFinding,
    function: CodeEntity,
) -> str:
    function_def = _function_def(function)
    if function_def is None:
        return "function_ast_unavailable"
    context = _callable_context(root, function, function_def)
    if context is None:
        context = _contextmanager_callable_context(root, function, function_def)
    if context is None:
        context_reason = _callable_context_rejection_reason(
            root,
            function,
            function_def,
        )
        if context_reason:
            return context_reason
        return "callable_context_unsupported"
    args = [arg.arg for arg in function_def.args.args]
    invocation_args = list(context["invocation_args"])
    if not args:
        if finding.rule_id == "broad_exception_pass" and _broad_exception_has_fallback_flow(function_def):
            return "broad_exception_fallback_flow_unsupported"
        if str(context.get("callable_kind") or "") == "returned_nested_function":
            return "returned_nested_function_no_invocation_arguments_unsupported"
        return "no_invocation_arguments"
    if finding.rule_id == "possible_index_overrun":
        sequence_arg = _index_overrun_sequence_arg(function_def)
        if not sequence_arg:
            return "index_overrun_sequence_arg_missing"
        if sequence_arg not in args:
            return "index_overrun_sequence_arg_not_invocable"
    elif finding.rule_id == "missing_len_zero_guard":
        sequence_arg = str(finding.evidence.get("len_source") or "")
        if not sequence_arg:
            return "len_source_missing"
        if sequence_arg not in args:
            return "len_source_not_invocable"
    elif finding.rule_id == "dict_missing_key_guard":
        mapping_arg = str(finding.evidence.get("mapping") or "")
        key_arg = str(finding.evidence.get("key") or "")
        if not mapping_arg or not key_arg:
            return "dict_key_guard_evidence_incomplete"
        if mapping_arg not in args or key_arg not in args:
            return "dict_key_guard_args_not_invocable"
    elif finding.rule_id == "inplace_api_return_value":
        return "inplace_receiver_not_invocable"
    elif finding.rule_id == "stringified_numeric_value":
        return "stringified_numeric_shape_unsupported"
    elif finding.rule_id == "iterator_double_consumption":
        return "iterator_double_consumption_shape_unsupported"
    elif finding.rule_id == "always_true_len_check":
        if _always_true_len_check_overlay_spec(function_def, invocation_args) is None:
            return "always_true_len_check_shape_unsupported"
    elif finding.rule_id == "mutable_default_arg":
        return "mutable_default_shape_unsupported"
    elif finding.rule_id == "inverted_empty_guard":
        if _inverted_empty_guard_overlay_spec(finding, invocation_args) is None:
            return "inverted_empty_guard_shape_unsupported"
    elif finding.rule_id == "broad_exception_pass":
        if not any(
            isinstance(node, ast.Try)
            and any(_handler_is_broad_pass(handler) for handler in node.handlers)
            for node in ast.walk(function_def)
        ):
            return "broad_exception_try_pass_missing"
        if str(context.get("callable_kind") or "").startswith(
            "contextmanager_"
        ) and _function_has_yield(function_def):
            return "broad_exception_contextmanager_lifecycle_unsupported"
        if _broad_exception_has_fallback_flow(function_def):
            return "broad_exception_fallback_flow_unsupported"
        return "broad_exception_empty_guard_or_raise_unsupported"
    elif finding.rule_id == "identity_comparison_literal":
        if _identity_literal_overlay_spec(finding, invocation_args) is None:
            return "identity_literal_string_or_arg_shape_unsupported"
    elif finding.rule_id == "enumerate_start_zero_counter":
        if (
            _enumerate_start_zero_overlay_spec(
                finding,
                function_def,
                invocation_args,
            )
            is None
        ):
            return "enumerate_start_zero_shape_unsupported"
    return "rule_specific_oracle_shape_unsupported"


def _select_rule_diverse_specs(
    candidates: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()
    selected_rules: set[str] = set()
    for index, candidate in enumerate(candidates):
        rule_id = str(candidate.get("rule_id") or "")
        if not rule_id or rule_id in selected_rules:
            continue
        selected.append(candidate)
        selected_ids.add(index)
        selected_rules.add(rule_id)
        if len(selected) >= limit:
            return selected
    for index, candidate in enumerate(candidates):
        if index in selected_ids:
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def _finding_rule_counts(findings: list[BugFinding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.rule_id] = counts.get(finding.rule_id, 0) + 1
    return dict(sorted(counts.items()))


def _case_rule_counts(cases: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        rule_id = str(_dict(case).get("rule_id") or "")
        if not rule_id:
            continue
        counts[rule_id] = counts.get(rule_id, 0) + 1
    return dict(sorted(counts.items()))


def _case_callable_kind_counts(cases: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        kind = str(_dict(case).get("callable_kind") or "")
        if not kind:
            continue
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def _candidate_score_preview(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preview = []
    for index, case in enumerate(cases[:10], start=1):
        item = _dict(case)
        preview.append(
            {
                "rank": index,
                "rule_id": str(item.get("rule_id") or ""),
                "function_name": str(item.get("function_name") or ""),
                "callable_kind": str(item.get("callable_kind") or ""),
                "overlay_score": _float(item.get("overlay_score", 0.0)),
                "score_breakdown": _dict(item.get("score_breakdown")),
            }
        )
    return preview


def _average_case_score(cases: list[dict[str, Any]]) -> float:
    if not cases:
        return 0.0
    return round(
        sum(_float(_dict(case).get("overlay_score", 0.0)) for case in cases)
        / len(cases),
        4,
    )


def _candidate_rejection_recommendations(
    counts: dict[str, Any],
) -> list[dict[str, Any]]:
    total = sum(_int(value) for value in counts.values())
    if total <= 0:
        return []
    rows = []
    for reason, count_value in sorted(
        counts.items(),
        key=lambda item: (-_int(item[1]), str(item[0])),
    ):
        count = _int(count_value)
        if count <= 0:
            continue
        rows.append(
            {
                "reason": str(reason),
                "count": count,
                "share": round(count / total, 4),
                "actionable": _overlay_rejection_is_actionable(str(reason)),
                "recommended_extension": _overlay_extension_for_rejection(
                    str(reason)
                ),
            }
        )
    return rows[:5]


def _next_actionable_overlay_extension(
    recommendations: list[dict[str, Any]],
) -> dict[str, Any]:
    for item in recommendations:
        row = _dict(item)
        if bool(row.get("actionable")):
            return dict(row)
    return {}


def _overlay_rejection_is_actionable(reason: str) -> bool:
    audit_only_reasons = {
        "broad_exception_fallback_flow_unsupported",
        "broad_exception_contextmanager_lifecycle_unsupported",
        "decorator_wrapper_exception_policy_unsupported",
        "lifecycle_dunder_method_unsupported",
    }
    return reason not in audit_only_reasons


def _overlay_extension_for_rejection(reason: str) -> str:
    mapping = {
        "broad_exception_fallback_flow_unsupported": (
            "Keep defensive fallback flow audit-only unless a user-supplied oracle "
            "or stronger semantic proof is available."
        ),
        "broad_exception_empty_guard_or_raise_unsupported": (
            "Extend broad-exception oracle mining beyond empty-input raise guards."
        ),
        "broad_exception_contextmanager_lifecycle_unsupported": (
            "Keep contextmanager setup/teardown broad-exception handling audit-only "
            "unless a deterministic lifecycle oracle is available."
        ),
        "contextmanager_method_unsupported": (
            "Add contextmanager invocation support with generated with-block oracles."
        ),
        "decorator_wrapper_function_scope_unsupported": (
            "Add decorator-wrapper unwrapping before generating overlay tests."
        ),
        "decorator_wrapper_exception_policy_unsupported": (
            "Keep exception-swallowing decorator wrappers audit-only unless the "
            "wrapper contract requires exception propagation."
        ),
        "returned_nested_function_scope_unsupported": (
            "Add factory or closure realization before generating overlay tests."
        ),
        "returned_nested_factory_arguments_unsupported": (
            "Add safe placeholder synthesis for returned-closure factory arguments."
        ),
        "returned_nested_factory_callable_arguments_unsupported": (
            "Add callable-aware factory fixtures before realizing returned closures."
        ),
        "returned_nested_factory_varargs_unsupported": (
            "Add vararg and keyword-vararg modeling for returned-closure factories."
        ),
        "returned_nested_factory_body_unsupported": (
            "Add side-effect modeling for returned-closure factory setup logic."
        ),
        "returned_nested_function_no_invocation_arguments_unsupported": (
            "Add rule-specific oracles for returned closures without invocation arguments."
        ),
        "returned_nested_factory_decorator_unsupported": (
            "Add decorator effect analysis for returned-closure factories."
        ),
        "nested_function_scope_unsupported": (
            "Add closure and returned-callable discovery before generating overlay tests."
        ),
        "nested_method_scope_unsupported": (
            "Add nested-class method resolution before generating overlay tests."
        ),
        "method_decorator_unknown_unsupported": (
            "Add decorator effect analysis or unwrap provably safe decorators."
        ),
        "property_method_unsupported": (
            "Add property-access or cached-property oracles with safe instance state setup."
        ),
        "method_receiver_not_self": (
            "Add receiver-role inference before treating the callable as an instance method."
        ),
        "class_base_unsupported_for_instantiation": (
            "Add external, multiple, or side-effecting base-class modeling, or synthesize fixtures."
        ),
        "class_init_body_unsupported": (
            "Add constructor side-effect modeling or fixture synthesis."
        ),
        "class_init_varargs_unsupported": (
            "Add constructor vararg modeling or fixture synthesis."
        ),
        "class_init_requires_keyword_only_arguments": (
            "Add keyword-only constructor placeholder synthesis."
        ),
        "class_ast_unavailable": (
            "Improve class AST recovery before attempting safe instantiation."
        ),
        "lifecycle_dunder_method_unsupported": (
            "Keep object lifecycle hooks audit-only; synthesize explicit user-facing "
            "methods or tests instead of invoking dunder lifecycle methods directly."
        ),
        "index_overrun_nested_shape_unsupported": (
            "Add safe outer-call mapping for this nested index-overrun helper shape."
        ),
        "iterator_double_consumption_nested_shape_unsupported": (
            "Add safe outer-call mapping for this nested iterator double-consumption helper shape."
        ),
        "broad_exception_nested_shape_unsupported": (
            "Add safe outer-call mapping for this nested broad-exception propagation shape."
        ),
        "stringified_numeric_nested_shape_unsupported": (
            "Add safe outer-call mapping for this nested stringified numeric helper shape."
        ),
        "dict_missing_key_nested_shape_unsupported": (
            "Add safe outer-call mapping for this nested mapping key helper shape."
        ),
        "inplace_api_nested_shape_unsupported": (
            "Add safe outer-call mapping for this nested in-place API return-value shape."
        ),
        "mutable_default_nested_shape_unsupported": (
            "Add safe outer-call mapping for this nested mutable-default state leak shape."
        ),
        "identity_literal_nested_shape_unsupported": (
            "Add safe outer-call mapping for this nested literal identity helper shape."
        ),
        "inverted_empty_guard_shape_unsupported": (
            "Add a non-empty input oracle for this inverted empty-guard shape."
        ),
        "always_true_len_check_shape_unsupported": (
            "Add an empty-input exception oracle for this always-true len guard shape."
        ),
        "inverted_empty_guard_nested_shape_unsupported": (
            "Add safe outer-call mapping for this nested inverted empty-guard helper shape."
        ),
        "always_true_len_check_nested_shape_unsupported": (
            "Add safe outer-call mapping for this nested always-true len guard helper shape."
        ),
        "missing_len_zero_nested_shape_unsupported": (
            "Add safe outer-call mapping for this nested len-denominator helper shape."
        ),
        "enumerate_start_zero_shape_unsupported": (
            "Add an observable counter oracle or average-style one-item iterator oracle "
            "for this enumerate loop shape."
        ),
        "enumerate_start_zero_average_shape_unsupported": (
            "Add a safe outer-call adapter for this nested enumerate generator shape."
        ),
    }
    return mapping.get(
        reason,
        "Inspect this blocker and add a rule-specific oracle or callable-shape adapter.",
    )


def _overlay_score_breakdown(
    *,
    finding: BugFinding,
    callable_kind: str,
    expected_exception: str,
    assertion_lines: list[str],
) -> dict[str, float]:
    static_confidence = _clamp_float(finding.confidence)
    rule_trigger_prior = OVERLAY_RULE_TRIGGER_PRIORS.get(finding.rule_id, 0.50)
    callable_kind_weight = 1.0 if callable_kind == "function" else 0.92
    if callable_kind == "returned_nested_function":
        callable_kind_weight = 0.90
    oracle_specificity = 1.0 if expected_exception else 0.70
    assertion_oracle_bonus = 0.02 if assertion_lines else 0.0
    score = min(
        1.0,
        0.55 * static_confidence
        + 0.25 * rule_trigger_prior
        + 0.10 * callable_kind_weight
        + 0.10 * oracle_specificity
        + assertion_oracle_bonus,
    )
    return {
        "score": round(score, 4),
        "static_confidence": round(static_confidence, 4),
        "rule_trigger_prior": round(rule_trigger_prior, 4),
        "callable_kind_weight": round(callable_kind_weight, 4),
        "oracle_specificity": round(oracle_specificity, 4),
        "assertion_oracle_bonus": round(assertion_oracle_bonus, 4),
    }


def _public_api_evidence(
    *,
    context: dict[str, Any],
    qualified_name: str,
    call_args: list[Any],
    call_style: str = "call",
) -> dict[str, Any]:
    callable_kind = str(context.get("callable_kind") or "")
    public_entrypoint = str(context.get("call_target") or "")
    public_call_args = [str(arg) for arg in _list(call_args)]
    trigger_expression = _public_api_trigger_expression(
        public_entrypoint=public_entrypoint,
        public_call_args=public_call_args,
        call_style=call_style,
    )
    return {
        "trigger_scope": _public_api_trigger_scope(callable_kind, call_style),
        "internal_target": qualified_name,
        "public_entrypoint": public_entrypoint,
        "public_call_args": public_call_args,
        "trigger_expression": trigger_expression,
        "call_style": call_style,
        "callable_kind": callable_kind,
        "is_nested_target": callable_kind.startswith("nested_"),
        "entrypoint_differs_from_internal_target": (
            public_entrypoint != qualified_name
        ),
    }


def _public_api_trigger_scope(callable_kind: str, call_style: str) -> str:
    if callable_kind.startswith("nested_"):
        return "public_entrypoint_to_nested_target"
    if callable_kind == "returned_nested_function":
        return "factory_to_returned_callable"
    if callable_kind == "property" or call_style == "property_access":
        return "property_public_entrypoint"
    if callable_kind.startswith("contextmanager_") or call_style == "contextmanager":
        return "contextmanager_public_entrypoint"
    if callable_kind in {"method", "staticmethod", "classmethod"}:
        return "class_public_api"
    return "direct_function"


def _public_api_trigger_expression(
    *,
    public_entrypoint: str,
    public_call_args: list[str],
    call_style: str,
) -> str:
    if call_style == "property_access":
        return public_entrypoint
    return f"{public_entrypoint}({', '.join(public_call_args)})"


def _case_spec_for_finding(
    *,
    root: Path,
    finding: BugFinding,
    function: CodeEntity,
) -> dict[str, Any] | None:
    function_def = _function_def(function)
    if function_def is None:
        return None
    context = _callable_context(root, function, function_def)
    if context is None and finding.rule_id == "broad_exception_pass":
        context = _property_callable_context(root, function, function_def)
    if context is None:
        context = _contextmanager_callable_context(root, function, function_def)
    if context is None:
        return None
    args = [arg.arg for arg in function_def.args.args]
    invocation_args = list(context["invocation_args"])
    if not args:
        return None
    success_exception = ""
    call_style = str(context.get("call_style") or "call")
    if finding.rule_id == "possible_index_overrun":
        sequence_arg = _index_overrun_sequence_arg(function_def)
        if not sequence_arg or sequence_arg not in args:
            return None
        call_args = _call_args(invocation_args, {sequence_arg: "[1]"})
        expected_exception = "IndexError"
        assertion_lines: list[str] = []
    elif finding.rule_id == "missing_len_zero_guard":
        sequence_arg = str(finding.evidence.get("len_source") or "")
        if not sequence_arg or sequence_arg not in args:
            return None
        call_args = _call_args(invocation_args, {sequence_arg: "[]"})
        expected_exception = "ZeroDivisionError"
        assertion_lines = []
    elif finding.rule_id == "dict_missing_key_guard":
        mapping_arg = str(finding.evidence.get("mapping") or "")
        key_arg = str(finding.evidence.get("key") or "")
        if not mapping_arg or not key_arg:
            return None
        if mapping_arg not in args or key_arg not in args:
            return None
        call_args = _call_args(
            invocation_args,
            {
                mapping_arg: "{}",
                key_arg: repr("__cia_missing_key__"),
            },
        )
        expected_exception = "KeyError"
        assertion_lines = []
    elif finding.rule_id == "inplace_api_return_value":
        inplace_spec = _inplace_api_overlay_spec(finding, invocation_args)
        if inplace_spec is None:
            return None
        call_args = inplace_spec["call_args"]
        expected_exception = "AssertionError"
        assertion_lines = list(inplace_spec["assertion_lines"])
        setup_lines = []
    elif finding.rule_id == "stringified_numeric_value":
        numeric_spec = _stringified_numeric_overlay_spec(
            finding,
            function_def,
            invocation_args,
        )
        if numeric_spec is None:
            return None
        call_args = numeric_spec["call_args"]
        expected_exception = "TypeError"
        assertion_lines = list(numeric_spec["assertion_lines"])
        setup_lines = list(numeric_spec["setup_lines"])
    elif finding.rule_id == "iterator_double_consumption":
        iterator_spec = _iterator_double_consumption_overlay_spec(
            finding,
            function_def,
            invocation_args,
        )
        if iterator_spec is None:
            return None
        call_args = iterator_spec["call_args"]
        expected_exception = "ZeroDivisionError"
        assertion_lines = list(iterator_spec["assertion_lines"])
        setup_lines = list(iterator_spec["setup_lines"])
    elif finding.rule_id == "always_true_len_check":
        always_true_spec = _always_true_len_check_overlay_spec(
            function_def,
            invocation_args,
        )
        if always_true_spec is None:
            return None
        call_args = always_true_spec["call_args"]
        expected_exception = "AssertionError"
        assertion_lines = []
        setup_lines = list(always_true_spec["setup_lines"])
        success_exception = str(always_true_spec["success_exception"])
    elif finding.rule_id == "mutable_default_arg":
        mutable_spec = _mutable_default_overlay_spec(
            function_def,
            invocation_args,
            call_target=str(context["call_target"]),
        )
        if mutable_spec is None:
            return None
        call_args = mutable_spec["call_args"]
        expected_exception = "AssertionError"
        assertion_lines = list(mutable_spec["assertion_lines"])
        setup_lines = list(mutable_spec["setup_lines"])
    elif finding.rule_id == "inverted_empty_guard":
        guard_spec = _inverted_empty_guard_overlay_spec(finding, invocation_args)
        if guard_spec is None:
            return None
        call_args = guard_spec["call_args"]
        expected_exception = str(guard_spec["expected_exception"])
        assertion_lines = []
    elif finding.rule_id == "broad_exception_pass":
        if str(context.get("callable_kind") or "") == "property":
            exception_spec = _broad_exception_property_overlay_spec(function_def)
            call_style = "property_access"
        else:
            exception_spec = _broad_exception_overlay_spec(
                function_def,
                invocation_args,
            )
        if exception_spec is None:
            return None
        call_args = exception_spec["call_args"]
        expected_exception = "AssertionError"
        assertion_lines = []
        setup_lines = list(exception_spec.get("setup_lines", []))
        success_exception = str(exception_spec["success_exception"])
    elif finding.rule_id == "identity_comparison_literal":
        identity_spec = _identity_literal_overlay_spec(finding, invocation_args)
        if identity_spec is None:
            return None
        call_args = identity_spec["call_args"]
        expected_exception = "AssertionError"
        assertion_lines = list(identity_spec["assertion_lines"])
        setup_lines = list(identity_spec["setup_lines"])
    elif finding.rule_id == "enumerate_start_zero_counter":
        enumerate_spec = _enumerate_start_zero_overlay_spec(
            finding,
            function_def,
            invocation_args,
        )
        if enumerate_spec is None:
            return None
        call_args = enumerate_spec["call_args"]
        expected_exception = "AssertionError"
        assertion_lines = list(enumerate_spec["assertion_lines"])
        setup_lines = list(enumerate_spec["setup_lines"])
    else:
        return None
    if finding.rule_id not in {
        "stringified_numeric_value",
        "iterator_double_consumption",
        "always_true_len_check",
        "mutable_default_arg",
        "broad_exception_pass",
        "identity_comparison_literal",
        "enumerate_start_zero_counter",
    }:
        setup_lines = []

    module_name = _module_name_for_file(root, Path(function.file_path))
    if not module_name:
        return None
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    function_name = qualified_name
    test_name = f"test_cia_overlay_{_slug(qualified_name)}_{_slug(finding.rule_id)}"
    relative_file = _relative_posix(Path(function.file_path), root)
    score = _overlay_score_breakdown(
        finding=finding,
        callable_kind=str(context["callable_kind"]),
        expected_exception=expected_exception,
        assertion_lines=assertion_lines,
    )
    return {
        "rule_id": finding.rule_id,
        "bug_type": finding.bug_type,
        "function_id": function.id,
        "function_name": function_name,
        "qualified_name": qualified_name,
        "callable_kind": context["callable_kind"],
        "class_name": context["class_name"],
        "import_name": context["import_name"],
        "call_target": context["call_target"],
        "setup_lines": [*list(context["setup_lines"]), *setup_lines],
        "relative_file_path": relative_file,
        "module_name": module_name,
        "call_args": call_args,
        "call_style": call_style,
        "public_api_evidence": _public_api_evidence(
            context=context,
            qualified_name=qualified_name,
            call_args=call_args,
            call_style=call_style,
        ),
        "expected_exception": expected_exception,
        "success_exception": success_exception,
        "assertion_lines": assertion_lines,
        "test_name": test_name,
        "finding": finding.to_dict(),
        "overlay_score": score["score"],
        "score_breakdown": score,
    }


def _case_spec_for_unsupported_finding(
    *,
    root: Path,
    finding: BugFinding,
    function: CodeEntity,
) -> dict[str, Any] | None:
    if finding.rule_id == "possible_index_overrun":
        return _possible_index_overrun_nested_case_spec(
            root=root,
            finding=finding,
            function=function,
        )
    if finding.rule_id == "iterator_double_consumption":
        return _iterator_double_consumption_nested_case_spec(
            root=root,
            finding=finding,
            function=function,
        )
    if finding.rule_id == "broad_exception_pass":
        return _broad_exception_nested_case_spec(
            root=root,
            finding=finding,
            function=function,
        )
    if finding.rule_id == "stringified_numeric_value":
        return _stringified_numeric_nested_case_spec(
            root=root,
            finding=finding,
            function=function,
        )
    if finding.rule_id == "dict_missing_key_guard":
        return _dict_missing_key_nested_case_spec(
            root=root,
            finding=finding,
            function=function,
        )
    if finding.rule_id == "inplace_api_return_value":
        return _inplace_api_nested_case_spec(
            root=root,
            finding=finding,
            function=function,
        )
    if finding.rule_id == "mutable_default_arg":
        return _mutable_default_nested_case_spec(
            root=root,
            finding=finding,
            function=function,
        )
    if finding.rule_id == "identity_comparison_literal":
        return _identity_literal_nested_case_spec(
            root=root,
            finding=finding,
            function=function,
        )
    if finding.rule_id == "inverted_empty_guard":
        return _inverted_empty_guard_nested_case_spec(
            root=root,
            finding=finding,
            function=function,
        )
    if finding.rule_id == "always_true_len_check":
        return _always_true_len_check_nested_case_spec(
            root=root,
            finding=finding,
            function=function,
        )
    if finding.rule_id == "missing_len_zero_guard":
        return _missing_len_zero_nested_case_spec(
            root=root,
            finding=finding,
            function=function,
        )
    if finding.rule_id == "enumerate_start_zero_counter":
        return _enumerate_start_zero_average_case_spec(
            root=root,
            finding=finding,
            function=function,
        )
    return None


def _unsupported_callable_overlay_rejection_reason(
    *,
    finding: BugFinding,
    function: CodeEntity,
) -> str:
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    parts = [part for part in qualified_name.split(".") if part]
    if finding.rule_id == "possible_index_overrun" and len(parts) >= 2:
        return _possible_index_overrun_nested_rejection_reason(
            function,
            qualified_name,
        )
    if finding.rule_id == "iterator_double_consumption" and len(parts) >= 2:
        return _iterator_double_consumption_nested_rejection_reason(
            function,
            qualified_name,
        )
    if finding.rule_id == "broad_exception_pass" and len(parts) >= 2:
        return _broad_exception_nested_rejection_reason(
            function,
            qualified_name,
        )
    if finding.rule_id == "stringified_numeric_value" and len(parts) >= 2:
        return "stringified_numeric_nested_shape_unsupported"
    if finding.rule_id == "dict_missing_key_guard" and len(parts) >= 2:
        return "dict_missing_key_nested_shape_unsupported"
    if finding.rule_id == "inplace_api_return_value" and len(parts) >= 2:
        return "inplace_api_nested_shape_unsupported"
    if finding.rule_id == "mutable_default_arg" and len(parts) >= 2:
        return "mutable_default_nested_shape_unsupported"
    if finding.rule_id == "identity_comparison_literal" and len(parts) >= 2:
        return "identity_literal_nested_shape_unsupported"
    if finding.rule_id == "inverted_empty_guard" and len(parts) >= 2:
        return "inverted_empty_guard_nested_shape_unsupported"
    if finding.rule_id == "always_true_len_check" and len(parts) >= 2:
        return "always_true_len_check_nested_shape_unsupported"
    if finding.rule_id == "missing_len_zero_guard" and len(parts) >= 2:
        return "missing_len_zero_nested_shape_unsupported"
    if finding.rule_id == "enumerate_start_zero_counter" and len(parts) >= 2:
        return "enumerate_start_zero_average_shape_unsupported"
    return ""


def _possible_index_overrun_nested_rejection_reason(
    function: CodeEntity,
    qualified_name: str,
) -> str:
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) not in {2, 3}:
        return "index_overrun_nested_shape_unsupported"
    outer_name = parts[-2]
    inner_name = parts[-1]
    context = _nested_function_context(function.file_path, outer_name, inner_name)
    if context is None:
        return "nested_function_scope_unsupported"
    outer_def, inner_def = context
    if _outer_returns_nested_function(outer_def, inner_name):
        return _returned_nested_factory_rejection_reason(outer_def, inner_def)
    return "index_overrun_nested_shape_unsupported"


def _iterator_double_consumption_nested_rejection_reason(
    function: CodeEntity,
    qualified_name: str,
) -> str:
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) not in {2, 3}:
        return "iterator_double_consumption_nested_shape_unsupported"
    outer_name = parts[-2]
    inner_name = parts[-1]
    context = _nested_function_context(function.file_path, outer_name, inner_name)
    if context is None:
        return "nested_function_scope_unsupported"
    outer_def, inner_def = context
    if _outer_returns_nested_function(outer_def, inner_name):
        return _returned_nested_factory_rejection_reason(outer_def, inner_def)
    return "iterator_double_consumption_nested_shape_unsupported"


def _broad_exception_nested_rejection_reason(
    function: CodeEntity,
    qualified_name: str,
) -> str:
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) not in {2, 3}:
        return "broad_exception_nested_shape_unsupported"
    outer_name = parts[-2]
    inner_name = parts[-1]
    context = _nested_function_context(function.file_path, outer_name, inner_name)
    if context is None:
        return "nested_function_scope_unsupported"
    outer_def, inner_def = context
    if _outer_returns_nested_function(outer_def, inner_name):
        if _looks_like_decorator_wrapper(outer_def, inner_def):
            if _has_broad_exception_swallow(inner_def):
                return "decorator_wrapper_exception_policy_unsupported"
            return "decorator_wrapper_function_scope_unsupported"
        return _returned_nested_factory_rejection_reason(outer_def, inner_def)
    return "broad_exception_nested_shape_unsupported"


def _possible_index_overrun_nested_case_spec(
    *,
    root: Path,
    finding: BugFinding,
    function: CodeEntity,
) -> dict[str, Any] | None:
    context = _possible_index_overrun_nested_container_context(function)
    if context is None:
        return None
    outer_def = context["outer_def"]
    inner_def = context["inner_def"]
    invocation_args = list(context["invocation_args"])
    index_spec = _possible_index_overrun_nested_overlay_spec(
        outer_def,
        inner_def,
        invocation_args,
    )
    if index_spec is None:
        return None
    module_name = _module_name_for_file(root, Path(function.file_path))
    if not module_name:
        return None
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    relative_file = _relative_posix(Path(function.file_path), root)
    expected_exception = "IndexError"
    assertion_lines: list[str] = []
    score = _overlay_score_breakdown(
        finding=finding,
        callable_kind=str(context["callable_kind"]),
        expected_exception=expected_exception,
        assertion_lines=assertion_lines,
    )
    return {
        "rule_id": finding.rule_id,
        "bug_type": finding.bug_type,
        "function_id": function.id,
        "function_name": qualified_name,
        "qualified_name": qualified_name,
        "callable_kind": context["callable_kind"],
        "class_name": context["class_name"],
        "import_name": context["import_name"],
        "call_target": context["call_target"],
        "setup_lines": list(context["setup_lines"]),
        "relative_file_path": relative_file,
        "module_name": module_name,
        "call_args": index_spec["call_args"],
        "call_style": "call",
        "public_api_evidence": _public_api_evidence(
            context=context,
            qualified_name=qualified_name,
            call_args=index_spec["call_args"],
        ),
        "expected_exception": expected_exception,
        "success_exception": "",
        "assertion_lines": assertion_lines,
        "test_name": (
            f"test_cia_overlay_{_slug(qualified_name)}_{_slug(finding.rule_id)}"
        ),
        "finding": finding.to_dict(),
        "overlay_score": score["score"],
        "score_breakdown": score,
    }


def _iterator_double_consumption_nested_case_spec(
    *,
    root: Path,
    finding: BugFinding,
    function: CodeEntity,
) -> dict[str, Any] | None:
    context = _iterator_double_consumption_nested_container_context(function)
    if context is None:
        return None
    outer_def = context["outer_def"]
    inner_def = context["inner_def"]
    invocation_args = list(context["invocation_args"])
    iterator_spec = _iterator_double_consumption_nested_overlay_spec(
        finding,
        outer_def,
        inner_def,
        invocation_args,
    )
    if iterator_spec is None:
        return None
    module_name = _module_name_for_file(root, Path(function.file_path))
    if not module_name:
        return None
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    relative_file = _relative_posix(Path(function.file_path), root)
    assertion_lines = list(iterator_spec["assertion_lines"])
    expected_exception = "ZeroDivisionError"
    score = _overlay_score_breakdown(
        finding=finding,
        callable_kind=str(context["callable_kind"]),
        expected_exception=expected_exception,
        assertion_lines=assertion_lines,
    )
    return {
        "rule_id": finding.rule_id,
        "bug_type": finding.bug_type,
        "function_id": function.id,
        "function_name": qualified_name,
        "qualified_name": qualified_name,
        "callable_kind": context["callable_kind"],
        "class_name": context["class_name"],
        "import_name": context["import_name"],
        "call_target": context["call_target"],
        "setup_lines": [
            *list(context["setup_lines"]),
            *list(iterator_spec["setup_lines"]),
        ],
        "relative_file_path": relative_file,
        "module_name": module_name,
        "call_args": iterator_spec["call_args"],
        "call_style": "call",
        "public_api_evidence": _public_api_evidence(
            context=context,
            qualified_name=qualified_name,
            call_args=iterator_spec["call_args"],
        ),
        "expected_exception": expected_exception,
        "success_exception": "",
        "assertion_lines": assertion_lines,
        "test_name": (
            f"test_cia_overlay_{_slug(qualified_name)}_{_slug(finding.rule_id)}"
        ),
        "finding": finding.to_dict(),
        "overlay_score": score["score"],
        "score_breakdown": score,
    }


def _broad_exception_nested_case_spec(
    *,
    root: Path,
    finding: BugFinding,
    function: CodeEntity,
) -> dict[str, Any] | None:
    context = _broad_exception_nested_container_context(function)
    if context is None:
        return None
    outer_def = context["outer_def"]
    inner_def = context["inner_def"]
    invocation_args = list(context["invocation_args"])
    exception_spec = _broad_exception_nested_overlay_spec(
        outer_def,
        inner_def,
        invocation_args,
    )
    if exception_spec is None:
        return None
    module_name = _module_name_for_file(root, Path(function.file_path))
    if not module_name:
        return None
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    relative_file = _relative_posix(Path(function.file_path), root)
    expected_exception = "AssertionError"
    assertion_lines: list[str] = []
    score = _overlay_score_breakdown(
        finding=finding,
        callable_kind=str(context["callable_kind"]),
        expected_exception=expected_exception,
        assertion_lines=assertion_lines,
    )
    return {
        "rule_id": finding.rule_id,
        "bug_type": finding.bug_type,
        "function_id": function.id,
        "function_name": qualified_name,
        "qualified_name": qualified_name,
        "callable_kind": context["callable_kind"],
        "class_name": context["class_name"],
        "import_name": context["import_name"],
        "call_target": context["call_target"],
        "setup_lines": [
            *list(context["setup_lines"]),
            *list(exception_spec.get("setup_lines", [])),
        ],
        "relative_file_path": relative_file,
        "module_name": module_name,
        "call_args": exception_spec["call_args"],
        "call_style": "call",
        "public_api_evidence": _public_api_evidence(
            context=context,
            qualified_name=qualified_name,
            call_args=exception_spec["call_args"],
        ),
        "expected_exception": expected_exception,
        "success_exception": str(exception_spec["success_exception"]),
        "assertion_lines": assertion_lines,
        "test_name": (
            f"test_cia_overlay_{_slug(qualified_name)}_{_slug(finding.rule_id)}"
        ),
        "finding": finding.to_dict(),
        "overlay_score": score["score"],
        "score_breakdown": score,
    }


def _stringified_numeric_nested_case_spec(
    *,
    root: Path,
    finding: BugFinding,
    function: CodeEntity,
) -> dict[str, Any] | None:
    context = _stringified_numeric_nested_container_context(function)
    if context is None:
        return None
    outer_def = context["outer_def"]
    inner_def = context["inner_def"]
    invocation_args = list(context["invocation_args"])
    numeric_spec = _stringified_numeric_nested_overlay_spec(
        finding,
        outer_def,
        inner_def,
        invocation_args,
    )
    if numeric_spec is None:
        return None
    module_name = _module_name_for_file(root, Path(function.file_path))
    if not module_name:
        return None
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    relative_file = _relative_posix(Path(function.file_path), root)
    assertion_lines = list(numeric_spec["assertion_lines"])
    expected_exception = "TypeError"
    score = _overlay_score_breakdown(
        finding=finding,
        callable_kind=str(context["callable_kind"]),
        expected_exception=expected_exception,
        assertion_lines=assertion_lines,
    )
    return {
        "rule_id": finding.rule_id,
        "bug_type": finding.bug_type,
        "function_id": function.id,
        "function_name": qualified_name,
        "qualified_name": qualified_name,
        "callable_kind": context["callable_kind"],
        "class_name": context["class_name"],
        "import_name": context["import_name"],
        "call_target": context["call_target"],
        "setup_lines": [
            *list(context["setup_lines"]),
            *list(numeric_spec["setup_lines"]),
        ],
        "relative_file_path": relative_file,
        "module_name": module_name,
        "call_args": numeric_spec["call_args"],
        "call_style": "call",
        "public_api_evidence": _public_api_evidence(
            context=context,
            qualified_name=qualified_name,
            call_args=numeric_spec["call_args"],
        ),
        "expected_exception": expected_exception,
        "success_exception": "",
        "assertion_lines": assertion_lines,
        "test_name": (
            f"test_cia_overlay_{_slug(qualified_name)}_{_slug(finding.rule_id)}"
        ),
        "finding": finding.to_dict(),
        "overlay_score": score["score"],
        "score_breakdown": score,
    }


def _dict_missing_key_nested_case_spec(
    *,
    root: Path,
    finding: BugFinding,
    function: CodeEntity,
) -> dict[str, Any] | None:
    context = _dict_missing_key_nested_container_context(function)
    if context is None:
        return None
    outer_def = context["outer_def"]
    inner_def = context["inner_def"]
    invocation_args = list(context["invocation_args"])
    dict_spec = _dict_missing_key_nested_overlay_spec(
        finding,
        outer_def,
        inner_def,
        invocation_args,
    )
    if dict_spec is None:
        return None
    module_name = _module_name_for_file(root, Path(function.file_path))
    if not module_name:
        return None
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    relative_file = _relative_posix(Path(function.file_path), root)
    expected_exception = "KeyError"
    score = _overlay_score_breakdown(
        finding=finding,
        callable_kind=str(context["callable_kind"]),
        expected_exception=expected_exception,
        assertion_lines=[],
    )
    return {
        "rule_id": finding.rule_id,
        "bug_type": finding.bug_type,
        "function_id": function.id,
        "function_name": qualified_name,
        "qualified_name": qualified_name,
        "callable_kind": context["callable_kind"],
        "class_name": context["class_name"],
        "import_name": context["import_name"],
        "call_target": context["call_target"],
        "setup_lines": list(context["setup_lines"]),
        "relative_file_path": relative_file,
        "module_name": module_name,
        "call_args": dict_spec["call_args"],
        "call_style": "call",
        "public_api_evidence": _public_api_evidence(
            context=context,
            qualified_name=qualified_name,
            call_args=dict_spec["call_args"],
        ),
        "expected_exception": expected_exception,
        "success_exception": "",
        "assertion_lines": [],
        "test_name": (
            f"test_cia_overlay_{_slug(qualified_name)}_{_slug(finding.rule_id)}"
        ),
        "finding": finding.to_dict(),
        "overlay_score": score["score"],
        "score_breakdown": score,
    }


def _inplace_api_nested_case_spec(
    *,
    root: Path,
    finding: BugFinding,
    function: CodeEntity,
) -> dict[str, Any] | None:
    context = _inplace_api_nested_container_context(function)
    if context is None:
        return None
    outer_def = context["outer_def"]
    inner_def = context["inner_def"]
    invocation_args = list(context["invocation_args"])
    inplace_spec = _inplace_api_nested_overlay_spec(
        finding,
        outer_def,
        inner_def,
        invocation_args,
    )
    if inplace_spec is None:
        return None
    module_name = _module_name_for_file(root, Path(function.file_path))
    if not module_name:
        return None
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    relative_file = _relative_posix(Path(function.file_path), root)
    assertion_lines = list(inplace_spec["assertion_lines"])
    expected_exception = "AssertionError"
    score = _overlay_score_breakdown(
        finding=finding,
        callable_kind=str(context["callable_kind"]),
        expected_exception=expected_exception,
        assertion_lines=assertion_lines,
    )
    return {
        "rule_id": finding.rule_id,
        "bug_type": finding.bug_type,
        "function_id": function.id,
        "function_name": qualified_name,
        "qualified_name": qualified_name,
        "callable_kind": context["callable_kind"],
        "class_name": context["class_name"],
        "import_name": context["import_name"],
        "call_target": context["call_target"],
        "setup_lines": list(context["setup_lines"]),
        "relative_file_path": relative_file,
        "module_name": module_name,
        "call_args": inplace_spec["call_args"],
        "call_style": "call",
        "public_api_evidence": _public_api_evidence(
            context=context,
            qualified_name=qualified_name,
            call_args=inplace_spec["call_args"],
        ),
        "expected_exception": expected_exception,
        "success_exception": "",
        "assertion_lines": assertion_lines,
        "test_name": (
            f"test_cia_overlay_{_slug(qualified_name)}_{_slug(finding.rule_id)}"
        ),
        "finding": finding.to_dict(),
        "overlay_score": score["score"],
        "score_breakdown": score,
    }


def _mutable_default_nested_case_spec(
    *,
    root: Path,
    finding: BugFinding,
    function: CodeEntity,
) -> dict[str, Any] | None:
    context = _mutable_default_nested_container_context(function)
    if context is None:
        return None
    outer_def = context["outer_def"]
    inner_def = context["inner_def"]
    invocation_args = list(context["invocation_args"])
    mutable_spec = _mutable_default_nested_overlay_spec(
        outer_def,
        inner_def,
        invocation_args,
    )
    if mutable_spec is None:
        return None
    module_name = _module_name_for_file(root, Path(function.file_path))
    if not module_name:
        return None
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    relative_file = _relative_posix(Path(function.file_path), root)
    assertion_lines = list(mutable_spec["assertion_lines"])
    expected_exception = "AssertionError"
    score = _overlay_score_breakdown(
        finding=finding,
        callable_kind=str(context["callable_kind"]),
        expected_exception=expected_exception,
        assertion_lines=assertion_lines,
    )
    return {
        "rule_id": finding.rule_id,
        "bug_type": finding.bug_type,
        "function_id": function.id,
        "function_name": qualified_name,
        "qualified_name": qualified_name,
        "callable_kind": context["callable_kind"],
        "class_name": context["class_name"],
        "import_name": context["import_name"],
        "call_target": context["call_target"],
        "setup_lines": [
            *list(context["setup_lines"]),
            *list(mutable_spec["setup_lines"]),
        ],
        "relative_file_path": relative_file,
        "module_name": module_name,
        "call_args": mutable_spec["call_args"],
        "call_style": "call",
        "public_api_evidence": _public_api_evidence(
            context=context,
            qualified_name=qualified_name,
            call_args=mutable_spec["call_args"],
        ),
        "expected_exception": expected_exception,
        "success_exception": "",
        "assertion_lines": assertion_lines,
        "test_name": (
            f"test_cia_overlay_{_slug(qualified_name)}_{_slug(finding.rule_id)}"
        ),
        "finding": finding.to_dict(),
        "overlay_score": score["score"],
        "score_breakdown": score,
    }


def _identity_literal_nested_case_spec(
    *,
    root: Path,
    finding: BugFinding,
    function: CodeEntity,
) -> dict[str, Any] | None:
    context = _identity_literal_nested_container_context(function)
    if context is None:
        return None
    outer_def = context["outer_def"]
    inner_def = context["inner_def"]
    invocation_args = list(context["invocation_args"])
    identity_spec = _identity_literal_nested_overlay_spec(
        finding,
        outer_def,
        inner_def,
        invocation_args,
    )
    if identity_spec is None:
        return None
    module_name = _module_name_for_file(root, Path(function.file_path))
    if not module_name:
        return None
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    relative_file = _relative_posix(Path(function.file_path), root)
    assertion_lines = list(identity_spec["assertion_lines"])
    expected_exception = "AssertionError"
    score = _overlay_score_breakdown(
        finding=finding,
        callable_kind=str(context["callable_kind"]),
        expected_exception=expected_exception,
        assertion_lines=assertion_lines,
    )
    return {
        "rule_id": finding.rule_id,
        "bug_type": finding.bug_type,
        "function_id": function.id,
        "function_name": qualified_name,
        "qualified_name": qualified_name,
        "callable_kind": context["callable_kind"],
        "class_name": context["class_name"],
        "import_name": context["import_name"],
        "call_target": context["call_target"],
        "setup_lines": [
            *list(context["setup_lines"]),
            *list(identity_spec["setup_lines"]),
        ],
        "relative_file_path": relative_file,
        "module_name": module_name,
        "call_args": identity_spec["call_args"],
        "call_style": "call",
        "public_api_evidence": _public_api_evidence(
            context=context,
            qualified_name=qualified_name,
            call_args=identity_spec["call_args"],
        ),
        "expected_exception": expected_exception,
        "success_exception": "",
        "assertion_lines": assertion_lines,
        "test_name": (
            f"test_cia_overlay_{_slug(qualified_name)}_{_slug(finding.rule_id)}"
        ),
        "finding": finding.to_dict(),
        "overlay_score": score["score"],
        "score_breakdown": score,
    }


def _inverted_empty_guard_nested_case_spec(
    *,
    root: Path,
    finding: BugFinding,
    function: CodeEntity,
) -> dict[str, Any] | None:
    context = _inverted_empty_guard_nested_container_context(function)
    if context is None:
        return None
    outer_def = context["outer_def"]
    inner_def = context["inner_def"]
    invocation_args = list(context["invocation_args"])
    guard_spec = _inverted_empty_guard_nested_overlay_spec(
        finding,
        outer_def,
        inner_def,
        invocation_args,
    )
    if guard_spec is None:
        return None
    module_name = _module_name_for_file(root, Path(function.file_path))
    if not module_name:
        return None
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    relative_file = _relative_posix(Path(function.file_path), root)
    expected_exception = str(guard_spec["expected_exception"])
    score = _overlay_score_breakdown(
        finding=finding,
        callable_kind=str(context["callable_kind"]),
        expected_exception=expected_exception,
        assertion_lines=[],
    )
    return {
        "rule_id": finding.rule_id,
        "bug_type": finding.bug_type,
        "function_id": function.id,
        "function_name": qualified_name,
        "qualified_name": qualified_name,
        "callable_kind": context["callable_kind"],
        "class_name": context["class_name"],
        "import_name": context["import_name"],
        "call_target": context["call_target"],
        "setup_lines": list(context["setup_lines"]),
        "relative_file_path": relative_file,
        "module_name": module_name,
        "call_args": guard_spec["call_args"],
        "call_style": "call",
        "public_api_evidence": _public_api_evidence(
            context=context,
            qualified_name=qualified_name,
            call_args=guard_spec["call_args"],
        ),
        "expected_exception": expected_exception,
        "success_exception": "",
        "assertion_lines": [],
        "test_name": (
            f"test_cia_overlay_{_slug(qualified_name)}_{_slug(finding.rule_id)}"
        ),
        "finding": finding.to_dict(),
        "overlay_score": score["score"],
        "score_breakdown": score,
    }


def _missing_len_zero_nested_case_spec(
    *,
    root: Path,
    finding: BugFinding,
    function: CodeEntity,
) -> dict[str, Any] | None:
    context = _missing_len_zero_nested_container_context(function)
    if context is None:
        return None
    outer_def = context["outer_def"]
    inner_def = context["inner_def"]
    invocation_args = list(context["invocation_args"])
    guard_spec = _missing_len_zero_nested_overlay_spec(
        finding,
        outer_def,
        inner_def,
        invocation_args,
    )
    if guard_spec is None:
        return None
    module_name = _module_name_for_file(root, Path(function.file_path))
    if not module_name:
        return None
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    relative_file = _relative_posix(Path(function.file_path), root)
    expected_exception = "ZeroDivisionError"
    score = _overlay_score_breakdown(
        finding=finding,
        callable_kind=str(context["callable_kind"]),
        expected_exception=expected_exception,
        assertion_lines=[],
    )
    return {
        "rule_id": finding.rule_id,
        "bug_type": finding.bug_type,
        "function_id": function.id,
        "function_name": qualified_name,
        "qualified_name": qualified_name,
        "callable_kind": context["callable_kind"],
        "class_name": context["class_name"],
        "import_name": context["import_name"],
        "call_target": context["call_target"],
        "setup_lines": list(context["setup_lines"]),
        "relative_file_path": relative_file,
        "module_name": module_name,
        "call_args": guard_spec["call_args"],
        "call_style": "call",
        "public_api_evidence": _public_api_evidence(
            context=context,
            qualified_name=qualified_name,
            call_args=guard_spec["call_args"],
        ),
        "expected_exception": expected_exception,
        "success_exception": "",
        "assertion_lines": [],
        "test_name": (
            f"test_cia_overlay_{_slug(qualified_name)}_{_slug(finding.rule_id)}"
        ),
        "finding": finding.to_dict(),
        "overlay_score": score["score"],
        "score_breakdown": score,
    }


def _always_true_len_check_nested_case_spec(
    *,
    root: Path,
    finding: BugFinding,
    function: CodeEntity,
) -> dict[str, Any] | None:
    context = _always_true_len_check_nested_container_context(function)
    if context is None:
        return None
    outer_def = context["outer_def"]
    inner_def = context["inner_def"]
    invocation_args = list(context["invocation_args"])
    guard_spec = _always_true_len_check_nested_overlay_spec(
        outer_def,
        inner_def,
        invocation_args,
    )
    if guard_spec is None:
        return None
    module_name = _module_name_for_file(root, Path(function.file_path))
    if not module_name:
        return None
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    relative_file = _relative_posix(Path(function.file_path), root)
    expected_exception = "AssertionError"
    assertion_lines: list[str] = []
    score = _overlay_score_breakdown(
        finding=finding,
        callable_kind=str(context["callable_kind"]),
        expected_exception=expected_exception,
        assertion_lines=assertion_lines,
    )
    return {
        "rule_id": finding.rule_id,
        "bug_type": finding.bug_type,
        "function_id": function.id,
        "function_name": qualified_name,
        "qualified_name": qualified_name,
        "callable_kind": context["callable_kind"],
        "class_name": context["class_name"],
        "import_name": context["import_name"],
        "call_target": context["call_target"],
        "setup_lines": list(context["setup_lines"]),
        "relative_file_path": relative_file,
        "module_name": module_name,
        "call_args": guard_spec["call_args"],
        "call_style": "call",
        "public_api_evidence": _public_api_evidence(
            context=context,
            qualified_name=qualified_name,
            call_args=guard_spec["call_args"],
        ),
        "expected_exception": expected_exception,
        "success_exception": str(guard_spec["success_exception"]),
        "assertion_lines": assertion_lines,
        "test_name": (
            f"test_cia_overlay_{_slug(qualified_name)}_{_slug(finding.rule_id)}"
        ),
        "finding": finding.to_dict(),
        "overlay_score": score["score"],
        "score_breakdown": score,
    }


def _enumerate_start_zero_average_case_spec(
    *,
    root: Path,
    finding: BugFinding,
    function: CodeEntity,
) -> dict[str, Any] | None:
    context = _enumerate_start_zero_average_container_context(function)
    if context is None:
        return None
    outer_def = context["outer_def"]
    inner_def = context["inner_def"]
    invocation_args = list(context["invocation_args"])
    average_spec = _enumerate_start_zero_average_overlay_spec(
        finding,
        outer_def,
        inner_def,
        invocation_args,
    )
    if average_spec is None:
        return None
    module_name = _module_name_for_file(root, Path(function.file_path))
    if not module_name:
        return None
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    relative_file = _relative_posix(Path(function.file_path), root)
    assertion_lines = list(average_spec["assertion_lines"])
    expected_exception = "ZeroDivisionError"
    score = _overlay_score_breakdown(
        finding=finding,
        callable_kind=str(context["callable_kind"]),
        expected_exception=expected_exception,
        assertion_lines=assertion_lines,
    )
    return {
        "rule_id": finding.rule_id,
        "bug_type": finding.bug_type,
        "function_id": function.id,
        "function_name": qualified_name,
        "qualified_name": qualified_name,
        "callable_kind": context["callable_kind"],
        "class_name": context["class_name"],
        "import_name": context["import_name"],
        "call_target": context["call_target"],
        "setup_lines": [
            *list(context["setup_lines"]),
            *list(average_spec["setup_lines"]),
        ],
        "relative_file_path": relative_file,
        "module_name": module_name,
        "call_args": average_spec["call_args"],
        "call_style": "call",
        "public_api_evidence": _public_api_evidence(
            context=context,
            qualified_name=qualified_name,
            call_args=average_spec["call_args"],
        ),
        "expected_exception": expected_exception,
        "success_exception": "",
        "assertion_lines": assertion_lines,
        "test_name": (
            f"test_cia_overlay_{_slug(qualified_name)}_{_slug(finding.rule_id)}"
        ),
        "finding": finding.to_dict(),
        "overlay_score": score["score"],
        "score_breakdown": score,
    }


def _write_overlay_test(overlay_root: Path, spec: dict[str, Any]) -> Path:
    tests_dir = overlay_root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    path = tests_dir / f"test_cia_overlay_{_slug(str(spec.get('rule_id') or 'case'))}.py"
    import_name = str(spec["import_name"])
    call_expression = _overlay_call_expression(spec)
    call_style = str(spec.get("call_style") or "call")
    exception_imports = _overlay_exception_import_lines(spec)
    if exception_imports:
        exception_imports += "\n"
    setup_lines = "\n".join(
        f"        {line}" for line in _list(spec.get("setup_lines"))
    )
    if setup_lines:
        setup_lines += "\n"
    assertion_lines = "\n".join(
        f"{_overlay_assertion_indent(call_style)}{line}"
        for line in _list(spec.get("assertion_lines"))
    )
    success_exception = str(spec.get("success_exception") or "")
    if success_exception:
        call_block = _overlay_statement_block(
            call_expression,
            call_style=call_style,
            result_target="",
        )
        call_block += (
            f"    except {success_exception}:\n"
            "        return\n"
            "    except Exception as exc:\n"
            "        raise AssertionError(\n"
            f"            'CIA overlay expected {success_exception} propagation, got '\n"
            "            + type(exc).__name__\n"
            "        ) from exc\n"
            "    raise AssertionError(\n"
            f"        'CIA overlay expected {success_exception} propagation, but the function returned.'\n"
            "    )\n"
        )
        source = (
            "import sys\n"
            "from pathlib import Path\n"
            f"{exception_imports}"
            "\n"
            "_CIA_REPO_ROOT = Path(__file__).resolve().parents[1]\n"
            "for _cia_path in (_CIA_REPO_ROOT, _CIA_REPO_ROOT / 'src'):\n"
            "    _cia_path_text = str(_cia_path)\n"
            "    if _cia_path.exists() and _cia_path_text not in sys.path:\n"
            "        sys.path.insert(0, _cia_path_text)\n\n"
            f"from {spec['module_name']} import {import_name}\n\n\n"
            f"def {spec['test_name']}():\n"
            "    try:\n"
            f"{setup_lines}"
            f"{call_block}"
        )
        path.write_text(source, encoding="utf-8")
        return path
    if assertion_lines:
        call_block = _overlay_statement_block(
            call_expression,
            call_style=call_style,
            result_target="__cia_result",
            body=assertion_lines,
        )
    else:
        call_block = _overlay_statement_block(
            call_expression,
            call_style=call_style,
            result_target="",
        )
    source = (
        "import sys\n"
        "from pathlib import Path\n"
        f"{exception_imports}"
        "\n"
        "_CIA_REPO_ROOT = Path(__file__).resolve().parents[1]\n"
        "for _cia_path in (_CIA_REPO_ROOT, _CIA_REPO_ROOT / 'src'):\n"
        "    _cia_path_text = str(_cia_path)\n"
        "    if _cia_path.exists() and _cia_path_text not in sys.path:\n"
        "        sys.path.insert(0, _cia_path_text)\n\n"
        f"from {spec['module_name']} import {import_name}\n\n\n"
        f"def {spec['test_name']}():\n"
        "    try:\n"
        f"{setup_lines}"
        f"{call_block}"
        f"    except {spec['expected_exception']} as exc:\n"
        "        raise AssertionError(\n"
        "            'CIA overlay expected boundary-safe behavior, but the '\n"
        f"            'function raised {spec['expected_exception']}.'\n"
        "        ) from exc\n"
    )
    path.write_text(source, encoding="utf-8")
    return path


def _overlay_exception_import_lines(spec: dict[str, Any]) -> str:
    exception_names = {
        str(spec.get("expected_exception") or ""),
        str(spec.get("success_exception") or ""),
    }
    lines: list[str] = []
    if "StatisticsError" in exception_names:
        lines.append("from statistics import StatisticsError")
    return "\n".join(lines)


def _overlay_call_expression(spec: dict[str, Any]) -> str:
    call_target = str(spec["call_target"])
    if str(spec.get("call_style") or "call") == "property_access":
        return call_target
    call_args = ", ".join(str(arg) for arg in _list(spec.get("call_args")))
    return f"{call_target}({call_args})"


def _overlay_statement_block(
    call_expression: str,
    *,
    call_style: str,
    result_target: str,
    body: str = "",
) -> str:
    if call_style == "contextmanager":
        if result_target:
            return f"        with {call_expression} as {result_target}:\n{body}\n"
        return f"        with {call_expression}:\n            pass\n"
    if result_target:
        return f"        {result_target} = {call_expression}\n{body}\n"
    return f"        {call_expression}\n"


def _overlay_assertion_indent(call_style: str) -> str:
    return "            " if call_style == "contextmanager" else "        "


def _run_overlay_test(
    *,
    command: str,
    command_args: list[str],
    cwd: Path,
    timeout: int,
    runner,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    src_path = cwd / "src"
    python_path_parts = [str(cwd)]
    if src_path.exists():
        python_path_parts.append(str(src_path))
    if env.get("PYTHONPATH"):
        python_path_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path_parts)
    try:
        completed = runner(
            command_args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "fail",
            "executed": True,
            "reason": "timeout",
            "message": f"Overlay pytest command exceeded {timeout}s timeout.",
            "command": command,
            "command_args": command_args,
            "execution_level": "overlay",
            "execution_risk": "low",
            "execution_scope": "single_generated_test",
            "cwd": str(cwd),
            "python_executable": sys.executable,
            "python_executable_source": "current_interpreter",
            "returncode": -1,
            "timeout": True,
            "passed": 0,
            "failed": 0,
            "failure_category": "timeout",
            "failure_signal": f"timeout>{timeout}s",
            "diagnostic_summary": "The generated overlay test did not finish before timeout.",
            "stdout_preview": _preview(exc.stdout or ""),
            "stderr_preview": _preview(exc.stderr or ""),
            "next_actions": ["Try a narrower overlay candidate or increase timeout."],
        }
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    success = completed.returncode == 0
    diagnostic = _diagnose_execution_result(
        stdout=stdout,
        stderr=stderr,
        success=success,
        returncode=completed.returncode,
    )
    return {
        "status": "pass" if success else "fail",
        "executed": True,
        "reason": "overlay_command_returncode",
        "message": (
            "Generated overlay pytest command passed."
            if success
            else "Generated overlay pytest command returned a non-zero exit code."
        ),
        "command": command,
        "command_args": command_args,
        "execution_level": "overlay",
        "execution_risk": "low",
        "execution_scope": "single_generated_test",
        "cwd": str(cwd),
        "python_executable": sys.executable,
        "python_executable_source": "current_interpreter",
        "returncode": completed.returncode,
        "timeout": False,
        "passed": _extract_count(stdout, "passed"),
        "failed": _extract_count(stdout, "failed"),
        "failure_category": diagnostic["failure_category"],
        "failure_signal": diagnostic["failure_signal"],
        "diagnostic_summary": diagnostic["diagnostic_summary"],
        "stdout_preview": _preview(stdout),
        "stderr_preview": _preview(stderr),
        "next_actions": diagnostic["next_actions"],
    }


def _copy_repository(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    ignored_names = set(DEFAULT_EXCLUDED_DIRS) | {
        ".coverage",
        ".nox",
        ".ruff_cache",
        "htmlcov",
    }
    source_root = source.resolve()
    ignored_paths: set[Path] = {destination.resolve()}
    try:
        output_root = destination.parent.resolve()
        output_root.relative_to(source_root)
    except ValueError:
        output_root = None
    if output_root is not None and output_root != source_root:
        ignored_paths.add(output_root)

    def ignore(dir_path: str, names: list[str]) -> set[str]:
        current = Path(dir_path).resolve()
        ignored = {name for name in names if name in ignored_names}
        for name in names:
            child = (current / name).resolve()
            if child in ignored_paths:
                ignored.add(name)
        return ignored

    shutil.copytree(
        source,
        destination,
        ignore=ignore,
    )


def _function_def(function: CodeEntity) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    try:
        tree = ast.parse(textwrap.dedent(function.source))
    except SyntaxError:
        return None
    if not tree.body or not isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    root = tree.body[0]
    if isinstance(root, ast.AsyncFunctionDef):
        return None
    return root


def _unsupported_callable(function: CodeEntity) -> bool:
    return bool(_unsupported_callable_reason(function))


def _unsupported_callable_reason(function: CodeEntity) -> str:
    metadata = function.metadata
    qualified_name = str(metadata.get("qualified_name") or function.name)
    if bool(metadata.get("is_async")):
        return "async_callable_unsupported"
    if not bool(metadata.get("is_method")):
        if "." not in qualified_name:
            return ""
        if _returned_nested_factory_spec(function, qualified_name) is not None:
            return ""
        return _nested_function_scope_rejection_reason(function, qualified_name)
    return (
        "nested_method_scope_unsupported"
        if len(qualified_name.split(".")) != 2
        else ""
    )


def _nested_function_scope_rejection_reason(
    function: CodeEntity,
    qualified_name: str,
) -> str:
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) < 2:
        return ""
    outer_name = parts[-2]
    inner_name = parts[-1]
    context = _nested_function_context(function.file_path, outer_name, inner_name)
    if context is None:
        return "nested_function_scope_unsupported"

    outer_def, inner_def = context
    if _outer_returns_nested_function(outer_def, inner_name):
        if _looks_like_decorator_wrapper(outer_def, inner_def):
            if _has_broad_exception_swallow(inner_def):
                return "decorator_wrapper_exception_policy_unsupported"
            return "decorator_wrapper_function_scope_unsupported"
        return _returned_nested_factory_rejection_reason(outer_def, inner_def)
    return "nested_function_scope_unsupported"


def _returned_nested_factory_rejection_reason(
    outer_def: ast.FunctionDef | ast.AsyncFunctionDef,
    inner_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str:
    if not isinstance(outer_def, ast.FunctionDef) or not isinstance(
        inner_def,
        ast.FunctionDef,
    ):
        return "returned_nested_function_scope_unsupported"
    if outer_def.decorator_list or inner_def.decorator_list:
        return "returned_nested_factory_decorator_unsupported"
    if _safe_returned_nested_factory_call_args(outer_def) is None:
        return _returned_nested_factory_argument_rejection_reason(outer_def)
    return "returned_nested_factory_body_unsupported"


def _returned_nested_factory_spec(
    function: CodeEntity,
    qualified_name: str,
) -> dict[str, Any] | None:
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) != 2:
        return None
    outer_name, inner_name = parts
    context = _nested_function_context(function.file_path, outer_name, inner_name)
    if context is None:
        return None
    outer_def, inner_def = context
    if not isinstance(outer_def, ast.FunctionDef):
        return None
    if not isinstance(inner_def, ast.FunctionDef):
        return None
    if _looks_like_decorator_wrapper(outer_def, inner_def):
        return None
    if not _safe_returned_nested_factory_shape(outer_def, inner_def):
        return None
    return {
        "outer_name": outer_name,
        "inner_name": inner_name,
        "outer_def": outer_def,
        "inner_def": inner_def,
    }


def _safe_returned_nested_factory_shape(
    outer_def: ast.FunctionDef,
    inner_def: ast.FunctionDef,
) -> bool:
    if outer_def.decorator_list or inner_def.decorator_list:
        return False
    if _safe_returned_nested_factory_call_args(outer_def) is None:
        return False
    body = _body_without_docstring(outer_def.body)
    if len(body) < 2:
        return False
    if body[-2] is not inner_def:
        return False
    if not isinstance(body[-1], ast.Return) or not _is_name(
        body[-1].value,
        inner_def.name,
    ):
        return False
    return all(_safe_returned_nested_factory_setup_statement(statement) for statement in body[:-2])


def _safe_returned_nested_factory_setup_statement(statement: ast.stmt) -> bool:
    if isinstance(statement, ast.Pass):
        return True
    if isinstance(statement, ast.Assign):
        if not statement.targets:
            return False
        if not all(isinstance(target, ast.Name) for target in statement.targets):
            return False
        return _safe_returned_nested_factory_setup_value(statement.value)
    if isinstance(statement, ast.AnnAssign):
        if not isinstance(statement.target, ast.Name):
            return False
        if statement.value is None:
            return True
        return _safe_returned_nested_factory_setup_value(statement.value)
    return False


def _safe_returned_nested_factory_setup_value(node: ast.AST) -> bool:
    if _literal_source(node):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
        return True
    if isinstance(node, ast.Call):
        if node.args or node.keywords:
            return False
        if isinstance(node.func, ast.Name):
            return node.func.id in {
                "dict",
                "list",
                "set",
                "tuple",
                "WeakKeyDictionary",
                "WeakValueDictionary",
            }
    return False


def _safe_returned_nested_factory_call_args(function_def: ast.FunctionDef) -> list[str] | None:
    spec = _safe_returned_nested_factory_call_spec(function_def)
    if spec is None:
        return None
    return list(spec["call_args"])


def _safe_returned_nested_factory_call_spec(
    function_def: ast.FunctionDef,
) -> dict[str, Any] | None:
    if function_def.args.vararg is not None or function_def.args.kwarg is not None:
        return None
    positional = [*function_def.args.posonlyargs, *function_def.args.args]
    defaults = list(function_def.args.defaults)
    required_count = max(0, len(positional) - len(defaults))
    call_args: list[str] = []
    setup_lines: list[str] = []
    for arg in positional[:required_count]:
        argument = _safe_factory_argument(arg)
        if argument is None:
            return None
        setup_lines.extend(argument["setup_lines"])
        call_args.append(str(argument["argument"]))
    for arg, default in zip(function_def.args.kwonlyargs, function_def.args.kw_defaults):
        if default is not None:
            continue
        argument = _safe_factory_argument(arg)
        if argument is None:
            return None
        setup_lines.extend(argument["setup_lines"])
        call_args.append(f"{arg.arg}={argument['argument']}")
    return {"call_args": call_args, "setup_lines": setup_lines}


def _returned_nested_factory_argument_rejection_reason(
    function_def: ast.FunctionDef,
) -> str:
    if function_def.args.vararg is not None or function_def.args.kwarg is not None:
        return "returned_nested_factory_varargs_unsupported"
    required_args = _required_factory_args(function_def)
    if any(_safe_factory_placeholder(arg) is None for arg in required_args):
        return "returned_nested_factory_callable_arguments_unsupported"
    return "returned_nested_factory_arguments_unsupported"


def _required_factory_args(function_def: ast.FunctionDef) -> list[ast.arg]:
    positional = [*function_def.args.posonlyargs, *function_def.args.args]
    defaults = list(function_def.args.defaults)
    required_count = max(0, len(positional) - len(defaults))
    required = list(positional[:required_count])
    required.extend(
        arg
        for arg, default in zip(function_def.args.kwonlyargs, function_def.args.kw_defaults)
        if default is None
    )
    return required


def _safe_factory_placeholder(arg: ast.arg) -> str | None:
    argument = _safe_factory_argument(arg)
    if argument is None:
        return None
    return str(argument["argument"])


def _safe_factory_argument(arg: ast.arg) -> dict[str, Any] | None:
    annotation = _annotation_name(arg.annotation)
    if annotation == "Callable":
        return _safe_callable_factory_argument(arg)
    if annotation in {"Awaitable", "Iterator", "Generator"}:
        return None
    return {"argument": _safe_constructor_placeholder(arg), "setup_lines": []}


def _safe_callable_factory_argument(arg: ast.arg) -> dict[str, Any] | None:
    signature = _callable_annotation_signature(arg.annotation)
    if signature is None:
        return None
    arg_count, return_annotation = signature
    if arg_count != 0:
        return None
    return_spec = _safe_return_spec_for_annotation(return_annotation)
    if return_spec is None:
        return None
    stub_name = _safe_factory_stub_name(arg.arg)
    return {
        "argument": stub_name,
        "setup_lines": [
            *list(return_spec["setup_lines"]),
            f"def {stub_name}():",
            f"    return {return_spec['return_expression']}",
        ],
    }


def _callable_annotation_signature(node: ast.AST | None) -> tuple[int, ast.AST | None] | None:
    if not isinstance(node, ast.Subscript):
        return None
    if _annotation_name(node.value) != "Callable":
        return None
    slice_node = node.slice
    if not isinstance(slice_node, ast.Tuple) or len(slice_node.elts) != 2:
        return None
    params_node, return_node = slice_node.elts
    if isinstance(params_node, ast.List):
        return len(params_node.elts), return_node
    if isinstance(params_node, ast.Tuple):
        return len(params_node.elts), return_node
    return None


def _safe_return_spec_for_annotation(node: ast.AST | None) -> dict[str, Any] | None:
    annotation = _annotation_name(node)
    if annotation in {"bool"}:
        return {"return_expression": "False", "setup_lines": []}
    if annotation in {"int"}:
        return {"return_expression": "0", "setup_lines": []}
    if annotation in {"float"}:
        return {"return_expression": "0.0", "setup_lines": []}
    if annotation in {"str"}:
        return {"return_expression": "''", "setup_lines": []}
    if annotation in {"bytes"}:
        return {"return_expression": "b''", "setup_lines": []}
    if annotation in {"list", "List", "Sequence", "Iterable"}:
        return {"return_expression": "[]", "setup_lines": []}
    if annotation in {"tuple", "Tuple"}:
        return {"return_expression": "()", "setup_lines": []}
    if annotation in {"set", "Set"}:
        return {"return_expression": "set()", "setup_lines": []}
    if annotation in {"dict", "Dict", "Mapping"}:
        return {"return_expression": "{}", "setup_lines": []}
    if annotation in {"None", "Optional"}:
        return {"return_expression": "None", "setup_lines": []}
    if annotation in {"Path", "PathLike"}:
        return {"return_expression": "Path('.')", "setup_lines": []}
    if annotation in {"IO", "TextIO", "StringIO"}:
        return {"return_expression": "io.StringIO()", "setup_lines": ["import io"]}
    return None


def _safe_factory_stub_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_").lower()
    if not stem or not stem[0].isalpha() and stem[0] != "_":
        stem = f"arg_{stem}"
    return f"__cia_factory_{stem}"


def _function_has_no_parameters(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    args = function_def.args
    return not (
        args.posonlyargs
        or args.args
        or args.kwonlyargs
        or args.vararg is not None
        or args.kwarg is not None
    )


def _nested_function_context(
    file_path: str,
    outer_name: str,
    inner_name: str,
) -> tuple[
    ast.FunctionDef | ast.AsyncFunctionDef,
    ast.FunctionDef | ast.AsyncFunctionDef,
] | None:
    try:
        tree = ast.parse(Path(file_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return None

    for outer in ast.walk(tree):
        if not isinstance(outer, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if outer.name != outer_name:
            continue
        for child in ast.walk(outer):
            if child is outer:
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child.name == inner_name:
                    return outer, child
    return None


def _outer_returns_nested_function(
    outer_def: ast.FunctionDef | ast.AsyncFunctionDef,
    inner_name: str,
) -> bool:
    for node in ast.walk(outer_def):
        if isinstance(node, ast.Return) and node.value is not None:
            if _return_value_references_name(node.value, inner_name):
                return True
    return False


def _return_value_references_name(node: ast.AST, name: str) -> bool:
    return any(isinstance(child, ast.Name) and child.id == name for child in ast.walk(node))


def _looks_like_decorator_wrapper(
    outer_def: ast.FunctionDef | ast.AsyncFunctionDef,
    inner_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    outer_params = set(_function_parameter_names(outer_def))
    if not outer_params:
        return False
    if inner_def.args.vararg is None and inner_def.args.kwarg is None:
        return False
    for node in ast.walk(inner_def):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in outer_params:
                return True
    return False


def _function_parameter_names(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    args = function_def.args
    return [
        arg.arg
        for arg in [
            *args.posonlyargs,
            *args.args,
            *args.kwonlyargs,
        ]
    ]


def _callable_context(
    root: Path,
    function: CodeEntity,
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, Any] | None:
    del root
    args = [arg.arg for arg in function_def.args.args]
    metadata = function.metadata
    qualified_name = str(metadata.get("qualified_name") or function.name)
    decorators = [str(item) for item in _list(metadata.get("decorators"))]
    decorator_set = set(decorators)
    if not bool(metadata.get("is_method")):
        if _has_decorator(decorator_set, "contextmanager"):
            return None
        if "." in qualified_name:
            nested_context = _returned_nested_callable_context(function, qualified_name)
            if nested_context is not None:
                return nested_context
            return None
        return {
            "callable_kind": "function",
            "class_name": "",
            "import_name": function.name,
            "call_target": function.name,
            "setup_lines": [],
            "invocation_args": args,
        }

    parts = qualified_name.split(".")
    if len(parts) != 2 or parts[1] != function.name:
        return None
    if _is_lifecycle_dunder_method(function.name):
        return None
    class_name = parts[0]
    if _has_decorator(decorator_set, "staticmethod"):
        if not _decorators_are_only(decorator_set, {"staticmethod"}):
            return None
        return {
            "callable_kind": "staticmethod",
            "class_name": class_name,
            "import_name": class_name,
            "call_target": f"{class_name}.{function.name}",
            "setup_lines": [],
            "invocation_args": args,
        }
    if _has_decorator(decorator_set, "classmethod"):
        if not _decorators_are_only(decorator_set, {"classmethod"}):
            return None
        if not args or args[0] != "cls":
            return None
        return {
            "callable_kind": "classmethod",
            "class_name": class_name,
            "import_name": class_name,
            "call_target": f"{class_name}.{function.name}",
            "setup_lines": [],
            "invocation_args": args[1:],
        }
    identity_decorated = bool(decorators) and _decorators_are_safe_identity(
        Path(function.file_path),
        decorator_set,
    )
    transparent_wrapper_decorated = bool(decorators) and (
        not identity_decorated
    ) and _decorators_are_safe_transparent_wrappers(
        Path(function.file_path),
        decorator_set,
    )
    if decorators and not (identity_decorated or transparent_wrapper_decorated):
        return None
    if not args or args[0] != "self":
        return None
    instantiation = _class_instantiation_spec(Path(function.file_path), class_name)
    if instantiation is None:
        return None
    return {
        "callable_kind": (
            "decorated_identity_method"
            if identity_decorated
            else (
                "decorated_transparent_wrapper_method"
                if transparent_wrapper_decorated
                else "method"
            )
        ),
        "class_name": class_name,
        "import_name": class_name,
        "call_target": f"__cia_instance.{function.name}",
        "setup_lines": [f"__cia_instance = {instantiation['expression']}"],
        "invocation_args": args[1:],
        }


def _returned_nested_callable_context(
    function: CodeEntity,
    qualified_name: str,
) -> dict[str, Any] | None:
    spec = _returned_nested_factory_spec(function, qualified_name)
    if spec is None:
        return None
    function_def = spec["inner_def"]
    if not isinstance(function_def, ast.FunctionDef):
        return None
    invocation_args = [arg.arg for arg in function_def.args.args]
    outer_name = str(spec["outer_name"])
    factory_call = _safe_returned_nested_factory_call_spec(spec["outer_def"])
    if factory_call is None:
        return None
    factory_args = list(factory_call["call_args"])
    return {
        "callable_kind": "returned_nested_function",
        "class_name": "",
        "import_name": outer_name,
        "call_target": "__cia_callable",
        "setup_lines": [
            *list(factory_call["setup_lines"]),
            f"__cia_callable = {outer_name}({', '.join(factory_args)})",
        ],
        "invocation_args": invocation_args,
    }


def _contextmanager_callable_context(
    root: Path,
    function: CodeEntity,
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, Any] | None:
    del root
    args = [arg.arg for arg in function_def.args.args]
    metadata = function.metadata
    decorators = [str(item) for item in _list(metadata.get("decorators"))]
    decorator_set = set(decorators)
    if not _has_decorator(decorator_set, "contextmanager"):
        return None
    if not _decorators_are_only(decorator_set, {"contextmanager"}):
        return None
    qualified_name = str(metadata.get("qualified_name") or function.name)
    if not bool(metadata.get("is_method")):
        if "." in qualified_name:
            return None
        return {
            "callable_kind": "contextmanager_function",
            "class_name": "",
            "import_name": function.name,
            "call_target": function.name,
            "setup_lines": [],
            "invocation_args": args,
            "call_style": "contextmanager",
        }

    parts = qualified_name.split(".")
    if len(parts) != 2 or parts[1] != function.name:
        return None
    class_name = parts[0]
    if not args or args[0] != "self":
        return None
    instantiation = _class_instantiation_spec(Path(function.file_path), class_name)
    if instantiation is None:
        return None
    return {
        "callable_kind": "contextmanager_method",
        "class_name": class_name,
        "import_name": class_name,
        "call_target": f"__cia_instance.{function.name}",
        "setup_lines": [f"__cia_instance = {instantiation['expression']}"],
        "invocation_args": args[1:],
        "call_style": "contextmanager",
    }


def _property_callable_context(
    root: Path,
    function: CodeEntity,
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, Any] | None:
    del root
    args = [arg.arg for arg in function_def.args.args]
    metadata = function.metadata
    if not bool(metadata.get("is_method")):
        return None
    qualified_name = str(metadata.get("qualified_name") or function.name)
    parts = qualified_name.split(".")
    if len(parts) != 2 or parts[1] != function.name:
        return None
    class_name = parts[0]
    decorators = [str(item) for item in _list(metadata.get("decorators"))]
    decorator_set = set(decorators)
    if not (
        _has_decorator(decorator_set, "property")
        or _has_decorator(decorator_set, "cached_property")
    ):
        return None
    if not _decorators_are_only(decorator_set, {"property", "cached_property"}):
        return None
    if not args or args[0] != "self":
        return None
    instantiation = _class_instantiation_spec(Path(function.file_path), class_name)
    if instantiation is None:
        return None
    return {
        "callable_kind": "property",
        "class_name": class_name,
        "import_name": class_name,
        "call_target": f"__cia_instance.{function.name}",
        "setup_lines": [f"__cia_instance = {instantiation['expression']}"],
        "invocation_args": [],
    }


def _callable_context_rejection_reason(
    root: Path,
    function: CodeEntity,
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str:
    del root
    args = [arg.arg for arg in function_def.args.args]
    metadata = function.metadata
    qualified_name = str(metadata.get("qualified_name") or function.name)
    if not bool(metadata.get("is_method")):
        return ""

    parts = qualified_name.split(".")
    if len(parts) != 2 or parts[1] != function.name:
        return "nested_method_scope_unsupported"
    if _is_lifecycle_dunder_method(function.name):
        return "lifecycle_dunder_method_unsupported"
    class_name = parts[0]
    decorators = [str(item) for item in _list(metadata.get("decorators"))]
    decorator_set = set(decorators)
    if _has_decorator(decorator_set, "staticmethod"):
        return (
            ""
            if _decorators_are_only(decorator_set, {"staticmethod"})
            else "staticmethod_decorator_stack_unsupported"
        )
    if _has_decorator(decorator_set, "classmethod"):
        if not _decorators_are_only(decorator_set, {"classmethod"}):
            return "classmethod_decorator_stack_unsupported"
        if not args or args[0] != "cls":
            return "classmethod_receiver_not_cls"
        return ""
    identity_decorated = bool(decorators) and _decorators_are_safe_identity(
        Path(function.file_path),
        decorator_set,
    )
    transparent_wrapper_decorated = bool(decorators) and (
        not identity_decorated
    ) and _decorators_are_safe_transparent_wrappers(
        Path(function.file_path),
        decorator_set,
    )
    if decorators and not (identity_decorated or transparent_wrapper_decorated):
        return _method_decorator_rejection_reason(decorator_set)
    if not args:
        return "method_receiver_missing"
    if args[0] != "self":
        return "method_receiver_not_self"
    return _class_instantiation_rejection_reason(
        Path(function.file_path),
        class_name,
    )


def _class_accepts_no_arg_instantiation(file_path: Path, class_name: str) -> bool:
    spec = _class_instantiation_spec(file_path, class_name)
    return bool(spec and spec.get("expression") == f"{class_name}()")


def _is_lifecycle_dunder_method(name: str) -> bool:
    return name in {
        "__del__",
        "__enter__",
        "__exit__",
        "__aenter__",
        "__aexit__",
    }


def _class_instantiation_spec(file_path: Path, class_name: str) -> dict[str, Any] | None:
    class_def = _class_def_for_instantiation(file_path, class_name)
    if class_def is None:
        return None
    init = _class_own_init(class_def)
    if init is None:
        inherited_args = _inherited_safe_constructor_call_args(file_path, class_def)
        if inherited_args is None:
            return None
        if inherited_args:
            return {
                "expression": f"{class_name}({', '.join(inherited_args)})",
                "safety": "inherited_literal_assignment_constructor",
            }
        return {"expression": f"{class_name}()", "safety": "no_init"}
    if not _safe_bases_for_instantiation(file_path, class_def):
        return None
    no_arg_reason = _function_no_arg_rejection_reason(init, receiver_count=1)
    if not no_arg_reason:
        return {"expression": f"{class_name}()", "safety": "optional_init_args"}
    constructor_args = _safe_constructor_call_args(init)
    if constructor_args is None:
        return None
    return {
        "expression": f"{class_name}({', '.join(constructor_args)})",
        "safety": "literal_assignment_constructor",
    }


def _class_instantiation_rejection_reason(
    file_path: Path,
    class_name: str,
) -> str:
    class_def = _class_def_for_instantiation(file_path, class_name)
    if class_def is None:
        try:
            ast.parse(file_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            return "class_ast_unavailable"
        return "class_definition_not_found"
    init = _class_own_init(class_def)
    if init is None:
        if _inherited_safe_constructor_call_args(file_path, class_def) is not None:
            return ""
        return "class_base_unsupported_for_instantiation"
    if not _safe_bases_for_instantiation(file_path, class_def):
        return "class_base_unsupported_for_instantiation"
    no_arg_reason = _function_no_arg_rejection_reason(init, receiver_count=1)
    if not no_arg_reason:
        return ""
    constructor_args = _safe_constructor_call_args(init)
    if constructor_args is not None:
        return ""
    return _safe_constructor_rejection_reason(init) or no_arg_reason


def _class_def_for_instantiation(
    file_path: Path,
    class_name: str,
) -> ast.ClassDef | None:
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    return next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        ),
        None,
    )


def _safe_bases_for_instantiation(
    file_path: Path,
    class_def: ast.ClassDef,
    seen: set[str] | None = None,
) -> bool:
    seen = set(seen or set())
    if class_def.name in seen:
        return False
    seen.add(class_def.name)
    for base in class_def.bases:
        if isinstance(base, ast.Name) and base.id == "object":
            continue
        if not isinstance(base, ast.Name):
            return False
        base_def = _class_def_for_instantiation(file_path, base.id)
        if base_def is None:
            return False
        if not _class_def_accepts_no_arg_init(base_def):
            return False
        if not _safe_bases_for_instantiation(file_path, base_def, seen):
            return False
    return True


def _inherited_safe_constructor_call_args(
    file_path: Path,
    class_def: ast.ClassDef,
    seen: set[str] | None = None,
) -> list[str] | None:
    seen = set(seen or set())
    if class_def.name in seen:
        return None
    seen.add(class_def.name)
    bases = [
        base
        for base in class_def.bases
        if not (isinstance(base, ast.Name) and base.id == "object")
    ]
    if not bases:
        return []
    if len(bases) != 1:
        return None
    base = bases[0]
    if not isinstance(base, ast.Name):
        return None
    base_def = _class_def_for_instantiation(file_path, base.id)
    if base_def is None:
        return None
    base_init = _class_own_init(base_def)
    if base_init is None:
        return _inherited_safe_constructor_call_args(file_path, base_def, seen)
    if not _safe_bases_for_instantiation(file_path, base_def, seen):
        return None
    no_arg_reason = _function_no_arg_rejection_reason(base_init, receiver_count=1)
    if not no_arg_reason:
        return []
    return _safe_constructor_call_args(base_init)


def _class_def_accepts_no_arg_init(class_def: ast.ClassDef) -> bool:
    init = _class_own_init(class_def)
    if init is None:
        return True
    return not _function_no_arg_rejection_reason(init, receiver_count=1)


def _class_own_init(class_def: ast.ClassDef) -> ast.FunctionDef | None:
    return next(
        (
            node
            for node in class_def.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        ),
        None,
    )


def _function_accepts_only_optional_args(
    function_def: ast.FunctionDef,
    *,
    receiver_count: int,
) -> bool:
    return not _function_no_arg_rejection_reason(
        function_def,
        receiver_count=receiver_count,
    )


def _function_no_arg_rejection_reason(
    function_def: ast.FunctionDef,
    *,
    receiver_count: int,
) -> str:
    positional = [*function_def.args.posonlyargs, *function_def.args.args]
    required_positional = max(0, len(positional) - len(function_def.args.defaults))
    if required_positional > receiver_count:
        return "class_init_requires_arguments"
    if not all(default is not None for default in function_def.args.kw_defaults):
        return "class_init_requires_keyword_only_arguments"
    return ""


def _safe_constructor_call_args(function_def: ast.FunctionDef) -> list[str] | None:
    if function_def.args.vararg is not None or function_def.args.kwarg is not None:
        return None
    if any(default is None for default in function_def.args.kw_defaults):
        return None
    if not _constructor_body_is_literal_assignments(function_def):
        return None
    positional = [*function_def.args.posonlyargs, *function_def.args.args]
    defaults = list(function_def.args.defaults)
    required_count = max(0, len(positional) - len(defaults))
    call_args: list[str] = []
    for index, arg in enumerate(positional[1:], start=1):
        if index >= required_count:
            break
        call_args.append(_safe_constructor_placeholder(arg))
    return call_args


def _safe_constructor_rejection_reason(function_def: ast.FunctionDef) -> str:
    if function_def.args.vararg is not None or function_def.args.kwarg is not None:
        return "class_init_varargs_unsupported"
    if any(default is None for default in function_def.args.kw_defaults):
        return "class_init_requires_keyword_only_arguments"
    if not _constructor_body_is_literal_assignments(function_def):
        return "class_init_body_unsupported"
    return "class_init_requires_arguments"


def _constructor_body_is_literal_assignments(function_def: ast.FunctionDef) -> bool:
    for statement in function_def.body:
        if isinstance(statement, ast.Pass):
            continue
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            continue
        if isinstance(statement, ast.Assign):
            if not statement.targets:
                return False
            if not all(_is_self_attribute(target) for target in statement.targets):
                return False
            if not _constructor_value_is_safe(statement.value, function_def):
                return False
            continue
        if isinstance(statement, ast.AnnAssign):
            if not _is_self_attribute(statement.target):
                return False
            if statement.value is not None and not _constructor_value_is_safe(
                statement.value,
                function_def,
            ):
                return False
            continue
        return False
    return True


def _constructor_value_is_safe(
    node: ast.AST,
    function_def: ast.FunctionDef,
) -> bool:
    parameter_names = {
        arg.arg
        for arg in [
            *function_def.args.posonlyargs,
            *function_def.args.args,
            *function_def.args.kwonlyargs,
        ]
    }
    if isinstance(node, ast.Name):
        return node.id in parameter_names
    return bool(_literal_source(node))


def _is_self_attribute(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _safe_constructor_placeholder(arg: ast.arg) -> str:
    annotation = _annotation_name(arg.annotation)
    name = arg.arg.lower()
    if annotation in {"bool"} or name.startswith(("is_", "has_", "can_")):
        return "False"
    if annotation in {"int"} or any(
        token in name for token in ("size", "count", "index", "limit", "width", "height")
    ):
        return "0"
    if annotation in {"float"} or any(token in name for token in ("rate", "ratio")):
        return "0.0"
    if annotation in {"str"} or any(token in name for token in ("name", "label", "path", "text")):
        return "''"
    if annotation in {"list", "List", "Sequence", "Iterable"} or any(
        token in name for token in ("items", "values", "rows", "entries")
    ):
        return "[]"
    if annotation in {"dict", "Dict", "Mapping"} or "mapping" in name:
        return "{}"
    return "None"


def _annotation_name(node: ast.AST | None) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _annotation_name(node.value)
    if isinstance(node, ast.Constant):
        return str(node.value)
    return ""


def _has_decorator(decorators: set[str], name: str) -> bool:
    return name in decorators or any(
        decorator.endswith(f".{name}") for decorator in decorators
    )


def _decorators_are_only(decorators: set[str], allowed: set[str]) -> bool:
    return all(any(_has_decorator({decorator}, name) for name in allowed) for decorator in decorators)


def _decorators_are_safe_identity(file_path: Path, decorators: set[str]) -> bool:
    if not decorators:
        return False
    if any("." in decorator or not decorator.isidentifier() for decorator in decorators):
        return False
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    identity_decorators = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and _is_identity_decorator(node)
    }
    return decorators.issubset(identity_decorators)


def _decorators_are_safe_transparent_wrappers(
    file_path: Path,
    decorators: set[str],
) -> bool:
    if not decorators:
        return False
    if any("." in decorator or not decorator.isidentifier() for decorator in decorators):
        return False
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    safe_decorators = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and (_is_identity_decorator(node) or _is_transparent_wrapper_decorator(node))
    }
    return decorators.issubset(safe_decorators)


def _is_identity_decorator(function_def: ast.FunctionDef) -> bool:
    positional = [*function_def.args.posonlyargs, *function_def.args.args]
    if len(positional) != 1:
        return False
    wrapped_name = positional[0].arg
    body = _body_without_docstring(function_def.body)
    return (
        len(body) == 1
        and isinstance(body[0], ast.Return)
        and _is_name(body[0].value, wrapped_name)
    )


def _is_transparent_wrapper_decorator(function_def: ast.FunctionDef) -> bool:
    positional = [*function_def.args.posonlyargs, *function_def.args.args]
    if len(positional) != 1:
        return False
    if function_def.args.vararg is not None or function_def.args.kwarg is not None:
        return False
    if function_def.args.kwonlyargs or function_def.args.defaults:
        return False
    wrapped_name = positional[0].arg
    body = _body_without_docstring(function_def.body)
    if len(body) != 2:
        return False
    inner_def, return_stmt = body
    if not isinstance(inner_def, ast.FunctionDef):
        return False
    if not isinstance(return_stmt, ast.Return) or not _is_name(
        return_stmt.value,
        inner_def.name,
    ):
        return False
    return _is_transparent_wrapper_function(inner_def, wrapped_name)


def _is_transparent_wrapper_function(
    function_def: ast.FunctionDef,
    wrapped_name: str,
) -> bool:
    if function_def.decorator_list:
        return False
    args = function_def.args
    if args.posonlyargs or args.args or args.kwonlyargs or args.defaults:
        return False
    if args.vararg is None or args.kwarg is None:
        return False
    body = _body_without_docstring(function_def.body)
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return False
    value = body[0].value
    if not isinstance(value, ast.Call) or not _is_name(value.func, wrapped_name):
        return False
    return _call_forwards_varargs_and_kwargs(
        value,
        vararg=args.vararg.arg,
        kwarg=args.kwarg.arg,
    )


def _call_forwards_varargs_and_kwargs(
    call: ast.Call,
    *,
    vararg: str,
    kwarg: str,
) -> bool:
    return (
        len(call.args) == 1
        and isinstance(call.args[0], ast.Starred)
        and _is_name(call.args[0].value, vararg)
        and len(call.keywords) == 1
        and call.keywords[0].arg is None
        and _is_name(call.keywords[0].value, kwarg)
    )


def _body_without_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            return body[1:]
    return list(body)


def _method_decorator_rejection_reason(decorators: set[str]) -> str:
    if any(_has_decorator({decorator}, "contextmanager") for decorator in decorators):
        return "contextmanager_method_unsupported"
    if any(_has_decorator({decorator}, "property") for decorator in decorators):
        return "property_method_unsupported"
    if any(_has_decorator({decorator}, "cached_property") for decorator in decorators):
        return "property_method_unsupported"
    return "method_decorator_unknown_unsupported"


def _inplace_api_overlay_spec(
    finding: BugFinding,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    method = str(finding.evidence.get("method") or "")
    receiver = str(finding.evidence.get("receiver") or "")
    if receiver not in invocation_args:
        return None
    case = _inplace_api_receiver_case(method)
    if case is None:
        return None
    return {
        "call_args": _call_args(
            invocation_args,
            {receiver: str(case["receiver_input"])},
        ),
        "assertion_lines": _inplace_api_assertion_lines(
            method,
            expected=str(case["expected"]),
        ),
    }


def _inplace_api_nested_container_context(
    function: CodeEntity,
) -> dict[str, Any] | None:
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) not in {2, 3}:
        return None
    inner_name = parts[-1]
    outer_name = parts[-2]
    if inner_name != function.name:
        return None
    context = _nested_function_context(function.file_path, outer_name, inner_name)
    if context is None:
        return None
    outer_def, inner_def = context
    if not isinstance(outer_def, ast.FunctionDef) or not isinstance(
        inner_def,
        ast.FunctionDef,
    ):
        return None

    if len(parts) == 2:
        return {
            "callable_kind": "nested_inplace_api_function",
            "class_name": "",
            "import_name": outer_name,
            "call_target": outer_name,
            "setup_lines": [],
            "invocation_args": [arg.arg for arg in outer_def.args.args],
            "outer_def": outer_def,
            "inner_def": inner_def,
        }

    class_name = parts[0]
    if not outer_def.args.args or outer_def.args.args[0].arg != "self":
        return None
    instantiation = _class_instantiation_spec(Path(function.file_path), class_name)
    if instantiation is None:
        return None
    return {
        "callable_kind": "nested_inplace_api_method",
        "class_name": class_name,
        "import_name": class_name,
        "call_target": f"__cia_instance.{outer_name}",
        "setup_lines": [f"__cia_instance = {instantiation['expression']}"],
        "invocation_args": [arg.arg for arg in outer_def.args.args[1:]],
        "outer_def": outer_def,
        "inner_def": inner_def,
    }


def _inplace_api_nested_overlay_spec(
    finding: BugFinding,
    outer_def: ast.FunctionDef,
    inner_def: ast.FunctionDef,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    method = str(finding.evidence.get("method") or "")
    receiver = str(finding.evidence.get("receiver") or "")
    if not receiver:
        return None
    case = _inplace_api_receiver_case(method)
    if case is None:
        return None
    outer_arg = _outer_returned_inner_call_argument(
        outer_def,
        inner_def=inner_def,
        inner_arg=receiver,
        invocation_args=invocation_args,
    )
    if not outer_arg:
        return None
    return {
        "call_args": _call_args_with_signature_defaults(
            outer_def,
            invocation_args,
            {outer_arg: str(case["receiver_input"])},
        ),
        "assertion_lines": _inplace_api_assertion_lines(
            method,
            expected=str(case["expected"]),
        ),
    }


def _inplace_api_receiver_case(method: str) -> dict[str, str] | None:
    if method == "sort":
        return {"receiver_input": "[3, 1, 2]", "expected": "[1, 2, 3]"}
    if method == "reverse":
        return {"receiver_input": "[1, 2, 3]", "expected": "[3, 2, 1]"}
    return None


def _inplace_api_assertion_lines(method: str, *, expected: str) -> list[str]:
    return [
        "if __cia_result is None:",
        (
            "    raise AssertionError("
            "'CIA overlay expected in-place API repair to return the mutated receiver, got None.'"
            ")"
        ),
        f"if __cia_result != {expected}:",
        (
            f"    raise AssertionError('CIA overlay expected {expected} after "
            f"{method}(), got ' + repr(__cia_result))"
        ),
    ]


def _identity_literal_overlay_spec(
    finding: BugFinding,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    compared = str(finding.evidence.get("compared_expression") or "")
    operator = str(finding.evidence.get("operator") or "")
    literal_source = str(finding.evidence.get("literal") or "")
    if compared not in invocation_args or operator not in {"is", "is not"}:
        return None
    try:
        literal_value = ast.literal_eval(literal_source)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(literal_value, str) or not literal_value:
        return None
    expected = "True" if operator == "is" else "False"
    return {
        "setup_lines": [
            f"__cia_literal = {literal_value!r}",
            f"__cia_value = {_dynamic_string_literal_expression(literal_value)}",
            "assert __cia_value == __cia_literal",
            "assert __cia_value is not __cia_literal",
        ],
        "call_args": _call_args(invocation_args, {compared: "__cia_value"}),
        "assertion_lines": [
            f"if __cia_result is not {expected}:",
            (
                "    raise AssertionError("
                "'CIA overlay expected literal equality semantics to return "
                f"{expected}, got ' + repr(__cia_result)"
                ")"
            ),
        ],
    }


def _dynamic_string_literal_expression(literal: str) -> str:
    split_at = max(1, len(literal) // 2)
    return f"''.join([{literal[:split_at]!r}, {literal[split_at:]!r}])"


def _identity_literal_nested_container_context(
    function: CodeEntity,
) -> dict[str, Any] | None:
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) not in {2, 3}:
        return None
    inner_name = parts[-1]
    outer_name = parts[-2]
    if inner_name != function.name:
        return None
    context = _nested_function_context(function.file_path, outer_name, inner_name)
    if context is None:
        return None
    outer_def, inner_def = context
    if not isinstance(outer_def, ast.FunctionDef) or not isinstance(
        inner_def,
        ast.FunctionDef,
    ):
        return None

    if len(parts) == 2:
        return {
            "callable_kind": "nested_identity_literal_function",
            "class_name": "",
            "import_name": outer_name,
            "call_target": outer_name,
            "setup_lines": [],
            "invocation_args": [arg.arg for arg in outer_def.args.args],
            "outer_def": outer_def,
            "inner_def": inner_def,
        }

    class_name = parts[0]
    if not outer_def.args.args or outer_def.args.args[0].arg != "self":
        return None
    instantiation = _class_instantiation_spec(Path(function.file_path), class_name)
    if instantiation is None:
        return None
    return {
        "callable_kind": "nested_identity_literal_method",
        "class_name": class_name,
        "import_name": class_name,
        "call_target": f"__cia_instance.{outer_name}",
        "setup_lines": [f"__cia_instance = {instantiation['expression']}"],
        "invocation_args": [arg.arg for arg in outer_def.args.args[1:]],
        "outer_def": outer_def,
        "inner_def": inner_def,
    }


def _identity_literal_nested_overlay_spec(
    finding: BugFinding,
    outer_def: ast.FunctionDef,
    inner_def: ast.FunctionDef,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    compared = str(finding.evidence.get("compared_expression") or "")
    operator = str(finding.evidence.get("operator") or "")
    literal_source = str(finding.evidence.get("literal") or "")
    if not compared or operator not in {"is", "is not"}:
        return None
    try:
        literal_value = ast.literal_eval(literal_source)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(literal_value, str) or not literal_value:
        return None
    outer_arg = _outer_returned_inner_call_argument(
        outer_def,
        inner_def=inner_def,
        inner_arg=compared,
        invocation_args=invocation_args,
    )
    if not outer_arg:
        return None
    expected = "True" if operator == "is" else "False"
    return {
        "setup_lines": [
            f"__cia_literal = {literal_value!r}",
            f"__cia_value = {_dynamic_string_literal_expression(literal_value)}",
            "assert __cia_value == __cia_literal",
            "assert __cia_value is not __cia_literal",
        ],
        "call_args": _call_args_with_signature_defaults(
            outer_def,
            invocation_args,
            {outer_arg: "__cia_value"},
        ),
        "assertion_lines": [
            f"if __cia_result is not {expected}:",
            (
                "    raise AssertionError("
                "'CIA overlay expected nested literal equality semantics to return "
                f"{expected}, got ' + repr(__cia_result)"
                ")"
            ),
        ],
    }


def _stringified_numeric_nested_container_context(
    function: CodeEntity,
) -> dict[str, Any] | None:
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) not in {2, 3}:
        return None
    inner_name = parts[-1]
    outer_name = parts[-2]
    if inner_name != function.name:
        return None
    context = _nested_function_context(function.file_path, outer_name, inner_name)
    if context is None:
        return None
    outer_def, inner_def = context
    if not isinstance(outer_def, ast.FunctionDef) or not isinstance(
        inner_def,
        ast.FunctionDef,
    ):
        return None

    if len(parts) == 2:
        return {
            "callable_kind": "nested_stringified_numeric_function",
            "class_name": "",
            "import_name": outer_name,
            "call_target": outer_name,
            "setup_lines": [],
            "invocation_args": [arg.arg for arg in outer_def.args.args],
            "outer_def": outer_def,
            "inner_def": inner_def,
        }

    class_name = parts[0]
    if not outer_def.args.args or outer_def.args.args[0].arg != "self":
        return None
    instantiation = _class_instantiation_spec(Path(function.file_path), class_name)
    if instantiation is None:
        return None
    return {
        "callable_kind": "nested_stringified_numeric_method",
        "class_name": class_name,
        "import_name": class_name,
        "call_target": f"__cia_instance.{outer_name}",
        "setup_lines": [f"__cia_instance = {instantiation['expression']}"],
        "invocation_args": [arg.arg for arg in outer_def.args.args[1:]],
        "outer_def": outer_def,
        "inner_def": inner_def,
    }


def _stringified_numeric_nested_overlay_spec(
    finding: BugFinding,
    outer_def: ast.FunctionDef,
    inner_def: ast.FunctionDef,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    variable = str(finding.evidence.get("variable") or "")
    if not variable:
        return None
    inner_expression = _stringified_numeric_inner_expression(inner_def, variable)
    if inner_expression is None:
        return None
    inner_invocation_args = [arg.arg for arg in inner_def.args.args]
    sequence_arg = _stringified_numeric_subscript_sequence(
        inner_def,
        variable=variable,
        invocation_args=inner_invocation_args,
    )
    if not sequence_arg:
        return None
    outer_arg = _outer_returned_inner_call_argument(
        outer_def,
        inner_def=inner_def,
        inner_arg=sequence_arg,
        invocation_args=invocation_args,
    )
    if not outer_arg:
        return None
    sample_sequence = [1, 2, 3]
    expected_index = _safe_eval_index_expression(
        inner_expression,
        sequence_arg=sequence_arg,
        sample_sequence=sample_sequence,
    )
    if expected_index is None:
        return None
    expected_value = sample_sequence[expected_index]
    return {
        "setup_lines": ["__cia_sequence = [1, 2, 3]"],
        "call_args": _call_args_with_signature_defaults(
            outer_def,
            invocation_args,
            {outer_arg: "__cia_sequence"},
        ),
        "assertion_lines": [
            f"__cia_expected = {expected_value!r}",
            "if __cia_result != __cia_expected:",
            (
                "    raise AssertionError("
                "'CIA overlay expected nested numeric index repair to return '"
                " + repr(__cia_expected) + ', got ' + repr(__cia_result)"
                ")"
            ),
        ],
    }


def _dict_missing_key_nested_container_context(
    function: CodeEntity,
) -> dict[str, Any] | None:
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) not in {2, 3}:
        return None
    inner_name = parts[-1]
    outer_name = parts[-2]
    if inner_name != function.name:
        return None
    context = _nested_function_context(function.file_path, outer_name, inner_name)
    if context is None:
        return None
    outer_def, inner_def = context
    if not isinstance(outer_def, ast.FunctionDef) or not isinstance(
        inner_def,
        ast.FunctionDef,
    ):
        return None

    if len(parts) == 2:
        return {
            "callable_kind": "nested_dict_missing_key_function",
            "class_name": "",
            "import_name": outer_name,
            "call_target": outer_name,
            "setup_lines": [],
            "invocation_args": [arg.arg for arg in outer_def.args.args],
            "outer_def": outer_def,
            "inner_def": inner_def,
        }

    class_name = parts[0]
    if not outer_def.args.args or outer_def.args.args[0].arg != "self":
        return None
    instantiation = _class_instantiation_spec(Path(function.file_path), class_name)
    if instantiation is None:
        return None
    return {
        "callable_kind": "nested_dict_missing_key_method",
        "class_name": class_name,
        "import_name": class_name,
        "call_target": f"__cia_instance.{outer_name}",
        "setup_lines": [f"__cia_instance = {instantiation['expression']}"],
        "invocation_args": [arg.arg for arg in outer_def.args.args[1:]],
        "outer_def": outer_def,
        "inner_def": inner_def,
    }


def _dict_missing_key_nested_overlay_spec(
    finding: BugFinding,
    outer_def: ast.FunctionDef,
    inner_def: ast.FunctionDef,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    mapping_arg = str(finding.evidence.get("mapping") or "")
    key_arg = str(finding.evidence.get("key") or "")
    if not mapping_arg or not key_arg:
        return None
    mapped_args = _outer_returned_inner_call_arguments(
        outer_def,
        inner_def=inner_def,
        inner_args=[mapping_arg, key_arg],
        invocation_args=invocation_args,
    )
    outer_mapping = mapped_args.get(mapping_arg, "")
    outer_key = mapped_args.get(key_arg, "")
    if not outer_mapping or not outer_key or outer_mapping == outer_key:
        return None
    return {
        "call_args": _call_args_with_signature_defaults(
            outer_def,
            invocation_args,
            {
                outer_mapping: "{}",
                outer_key: repr("__cia_missing_key__"),
            },
        ),
    }


def _inverted_empty_guard_overlay_spec(
    finding: BugFinding,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    guard_name = str(finding.evidence.get("guard_name") or "")
    exception_name = str(finding.evidence.get("exception") or "")
    if guard_name not in invocation_args:
        return None
    if exception_name not in {"ValueError", "StatisticsError"}:
        return None
    return {
        "call_args": _call_args(invocation_args, {guard_name: "[1, 2, 3]"}),
        "expected_exception": exception_name,
    }


def _inverted_empty_guard_nested_container_context(
    function: CodeEntity,
) -> dict[str, Any] | None:
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) not in {2, 3}:
        return None
    inner_name = parts[-1]
    outer_name = parts[-2]
    if inner_name != function.name:
        return None
    context = _nested_function_context(function.file_path, outer_name, inner_name)
    if context is None:
        return None
    outer_def, inner_def = context
    if not isinstance(outer_def, ast.FunctionDef) or not isinstance(
        inner_def,
        ast.FunctionDef,
    ):
        return None

    if len(parts) == 2:
        return {
            "callable_kind": "nested_inverted_empty_guard_function",
            "class_name": "",
            "import_name": outer_name,
            "call_target": outer_name,
            "setup_lines": [],
            "invocation_args": [arg.arg for arg in outer_def.args.args],
            "outer_def": outer_def,
            "inner_def": inner_def,
        }

    class_name = parts[0]
    if not outer_def.args.args or outer_def.args.args[0].arg != "self":
        return None
    instantiation = _class_instantiation_spec(Path(function.file_path), class_name)
    if instantiation is None:
        return None
    return {
        "callable_kind": "nested_inverted_empty_guard_method",
        "class_name": class_name,
        "import_name": class_name,
        "call_target": f"__cia_instance.{outer_name}",
        "setup_lines": [f"__cia_instance = {instantiation['expression']}"],
        "invocation_args": [arg.arg for arg in outer_def.args.args[1:]],
        "outer_def": outer_def,
        "inner_def": inner_def,
    }


def _inverted_empty_guard_nested_overlay_spec(
    finding: BugFinding,
    outer_def: ast.FunctionDef,
    inner_def: ast.FunctionDef,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    guard_name = str(finding.evidence.get("guard_name") or "")
    exception_name = str(finding.evidence.get("exception") or "")
    if not guard_name or exception_name not in {"ValueError", "StatisticsError"}:
        return None
    outer_arg = _outer_returned_inner_call_argument(
        outer_def,
        inner_def=inner_def,
        inner_arg=guard_name,
        invocation_args=invocation_args,
    )
    if not outer_arg:
        return None
    return {
        "call_args": _call_args_with_signature_defaults(
            outer_def,
            invocation_args,
            {outer_arg: "[1, 2, 3]"},
        ),
        "expected_exception": exception_name,
    }


def _missing_len_zero_nested_container_context(
    function: CodeEntity,
) -> dict[str, Any] | None:
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) not in {2, 3}:
        return None
    inner_name = parts[-1]
    outer_name = parts[-2]
    if inner_name != function.name:
        return None
    context = _nested_function_context(function.file_path, outer_name, inner_name)
    if context is None:
        return None
    outer_def, inner_def = context
    if not isinstance(outer_def, ast.FunctionDef) or not isinstance(
        inner_def,
        ast.FunctionDef,
    ):
        return None

    if len(parts) == 2:
        return {
            "callable_kind": "nested_missing_len_guard_function",
            "class_name": "",
            "import_name": outer_name,
            "call_target": outer_name,
            "setup_lines": [],
            "invocation_args": [arg.arg for arg in outer_def.args.args],
            "outer_def": outer_def,
            "inner_def": inner_def,
        }

    class_name = parts[0]
    if not outer_def.args.args or outer_def.args.args[0].arg != "self":
        return None
    instantiation = _class_instantiation_spec(Path(function.file_path), class_name)
    if instantiation is None:
        return None
    return {
        "callable_kind": "nested_missing_len_guard_method",
        "class_name": class_name,
        "import_name": class_name,
        "call_target": f"__cia_instance.{outer_name}",
        "setup_lines": [f"__cia_instance = {instantiation['expression']}"],
        "invocation_args": [arg.arg for arg in outer_def.args.args[1:]],
        "outer_def": outer_def,
        "inner_def": inner_def,
    }


def _missing_len_zero_nested_overlay_spec(
    finding: BugFinding,
    outer_def: ast.FunctionDef,
    inner_def: ast.FunctionDef,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    len_source = str(finding.evidence.get("len_source") or "")
    if not len_source:
        return None
    outer_arg = _outer_returned_inner_call_argument(
        outer_def,
        inner_def=inner_def,
        inner_arg=len_source,
        invocation_args=invocation_args,
    )
    if not outer_arg:
        return None
    return {
        "call_args": _call_args_with_signature_defaults(
            outer_def,
            invocation_args,
            {outer_arg: "[]"},
        ),
    }


def _always_true_len_check_nested_container_context(
    function: CodeEntity,
) -> dict[str, Any] | None:
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) not in {2, 3}:
        return None
    inner_name = parts[-1]
    outer_name = parts[-2]
    if inner_name != function.name:
        return None
    context = _nested_function_context(function.file_path, outer_name, inner_name)
    if context is None:
        return None
    outer_def, inner_def = context
    if not isinstance(outer_def, ast.FunctionDef) or not isinstance(
        inner_def,
        ast.FunctionDef,
    ):
        return None

    if len(parts) == 2:
        return {
            "callable_kind": "nested_always_true_len_check_function",
            "class_name": "",
            "import_name": outer_name,
            "call_target": outer_name,
            "setup_lines": [],
            "invocation_args": [arg.arg for arg in outer_def.args.args],
            "outer_def": outer_def,
            "inner_def": inner_def,
        }

    class_name = parts[0]
    if not outer_def.args.args or outer_def.args.args[0].arg != "self":
        return None
    instantiation = _class_instantiation_spec(Path(function.file_path), class_name)
    if instantiation is None:
        return None
    return {
        "callable_kind": "nested_always_true_len_check_method",
        "class_name": class_name,
        "import_name": class_name,
        "call_target": f"__cia_instance.{outer_name}",
        "setup_lines": [f"__cia_instance = {instantiation['expression']}"],
        "invocation_args": [arg.arg for arg in outer_def.args.args[1:]],
        "outer_def": outer_def,
        "inner_def": inner_def,
    }


def _always_true_len_check_nested_overlay_spec(
    outer_def: ast.FunctionDef,
    inner_def: ast.FunctionDef,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    inner_args = [arg.arg for arg in inner_def.args.args]
    inner_spec = _always_true_len_check_overlay_spec(inner_def, inner_args)
    if inner_spec is None:
        return None
    inner_guard = str(inner_spec.get("guard_name") or "")
    if not inner_guard:
        return None
    outer_arg = _outer_returned_inner_call_argument(
        outer_def,
        inner_def=inner_def,
        inner_arg=inner_guard,
        invocation_args=invocation_args,
    )
    if not outer_arg:
        return None
    return {
        "call_args": _call_args_with_signature_defaults(
            outer_def,
            invocation_args,
            {outer_arg: _empty_len_guard_call_value(outer_def, outer_arg)},
        ),
        "success_exception": str(inner_spec["success_exception"]),
    }


def _always_true_len_check_overlay_spec(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    for index, statement in enumerate(function_def.body):
        if not isinstance(statement, ast.If):
            continue
        guard_name = _always_true_len_guard_name(
            statement.test,
            invocation_args,
        )
        if not guard_name:
            continue
        success_exception = _always_true_len_success_exception(
            statement,
            function_def.body[index + 1 :],
        )
        if success_exception not in {"ValueError", "StatisticsError"}:
            continue
        return {
            "call_args": _call_args_with_signature_defaults(
                function_def,
                invocation_args,
                {guard_name: _empty_len_guard_call_value(function_def, guard_name)},
            ),
            "setup_lines": [],
            "guard_name": guard_name,
            "success_exception": success_exception,
        }
    return None


def _always_true_len_guard_name(
    node: ast.AST,
    invocation_args: list[str],
) -> str:
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return ""
    left = node.left
    right = node.comparators[0]
    op = node.ops[0]
    for argument in invocation_args:
        if (
            _is_len_call_for_arg(left, argument)
            and isinstance(op, ast.GtE)
            and _is_zero_constant(right)
        ):
            return argument
        if (
            _is_zero_constant(left)
            and isinstance(op, ast.LtE)
            and _is_len_call_for_arg(right, argument)
        ):
            return argument
    return ""


def _always_true_len_success_exception(
    statement: ast.If,
    following: list[ast.stmt],
) -> str:
    if statement.orelse:
        return _single_direct_raise_name(statement.orelse)
    if not following:
        return ""
    return _direct_raise_name(following[0])


def _single_direct_raise_name(body: list[ast.stmt]) -> str:
    if len(body) != 1:
        return ""
    return _direct_raise_name(body[0])


def _direct_raise_name(statement: ast.stmt) -> str:
    if not isinstance(statement, ast.Raise) or statement.exc is None:
        return ""
    exc = statement.exc
    if isinstance(exc, ast.Call):
        exc = exc.func
    if isinstance(exc, ast.Name):
        return exc.id
    return ""


def _empty_len_guard_call_value(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    guard_name: str,
) -> str:
    argument = _argument_node(function_def, guard_name)
    annotation = _annotation_name(argument.annotation) if argument is not None else ""
    lower_name = guard_name.lower()
    if annotation in {"str"} or any(
        token in lower_name
        for token in (
            "url",
            "uri",
            "path",
            "host",
            "scheme",
            "domain",
            "text",
            "string",
            "name",
        )
    ):
        return '""'
    if annotation in {"dict", "Dict", "Mapping"} or any(
        token in lower_name for token in ("headers", "mapping", "dict")
    ):
        return "{}"
    return "[]"


def _argument_node(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
) -> ast.arg | None:
    arguments = [
        *function_def.args.posonlyargs,
        *function_def.args.args,
        *function_def.args.kwonlyargs,
    ]
    return next((argument for argument in arguments if argument.arg == name), None)


def _outer_prior_then_returned_inner_call_argument(
    outer_def: ast.FunctionDef,
    *,
    inner_def: ast.FunctionDef,
    inner_arg: str,
    invocation_args: list[str],
) -> str:
    inner_args = [arg.arg for arg in inner_def.args.args]
    if inner_arg not in inner_args:
        return ""
    inner_arg_index = inner_args.index(inner_arg)
    invocation_arg_set = set(invocation_args)
    seen_prior_inner_call = False
    for statement in outer_def.body:
        if statement is inner_def or isinstance(statement, ast.FunctionDef):
            continue
        if isinstance(statement, ast.Return) and statement.value is not None:
            for child in ast.walk(statement.value):
                if not _is_call_to_name(child, inner_def.name):
                    continue
                mapped = _call_argument_name_for_parameter(
                    child,
                    parameter_name=inner_arg,
                    parameter_index=inner_arg_index,
                )
                if seen_prior_inner_call and mapped in invocation_arg_set:
                    return mapped
        if _statement_calls_inner_with_parameter(
            statement,
            inner_def=inner_def,
            inner_arg=inner_arg,
            inner_arg_index=inner_arg_index,
        ):
            seen_prior_inner_call = True
    return ""


def _statement_calls_inner_with_parameter(
    statement: ast.stmt,
    *,
    inner_def: ast.FunctionDef,
    inner_arg: str,
    inner_arg_index: int,
) -> bool:
    for child in ast.walk(statement):
        if not _is_call_to_name(child, inner_def.name):
            continue
        if _call_has_argument_for_parameter(
            child,
            parameter_name=inner_arg,
            parameter_index=inner_arg_index,
        ):
            return True
    return False


def _call_has_argument_for_parameter(
    call: ast.Call,
    *,
    parameter_name: str,
    parameter_index: int,
) -> bool:
    if any(keyword.arg == parameter_name for keyword in call.keywords):
        return True
    return len(call.args) > parameter_index


def _outer_returned_inner_call_argument(
    outer_def: ast.FunctionDef,
    *,
    inner_def: ast.FunctionDef,
    inner_arg: str,
    invocation_args: list[str],
) -> str:
    inner_args = [arg.arg for arg in inner_def.args.args]
    if inner_arg not in inner_args:
        return ""
    inner_arg_index = inner_args.index(inner_arg)
    invocation_arg_set = set(invocation_args)
    for node in ast.walk(outer_def):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        for child in ast.walk(node.value):
            if not _is_call_to_name(child, inner_def.name):
                continue
            mapped = _call_argument_name_for_parameter(
                child,
                parameter_name=inner_arg,
                parameter_index=inner_arg_index,
            )
            if mapped in invocation_arg_set:
                return mapped
    return ""


def _outer_returned_inner_call_arguments(
    outer_def: ast.FunctionDef,
    *,
    inner_def: ast.FunctionDef,
    inner_args: list[str],
    invocation_args: list[str],
) -> dict[str, str]:
    inner_parameters = [arg.arg for arg in inner_def.args.args]
    if any(name not in inner_parameters for name in inner_args):
        return {}
    invocation_arg_set = set(invocation_args)
    for node in ast.walk(outer_def):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        for child in ast.walk(node.value):
            if not _is_call_to_name(child, inner_def.name):
                continue
            mapped: dict[str, str] = {}
            for inner_arg in inner_args:
                parameter_index = inner_parameters.index(inner_arg)
                outer_arg = _call_argument_name_for_parameter(
                    child,
                    parameter_name=inner_arg,
                    parameter_index=parameter_index,
                )
                if outer_arg not in invocation_arg_set:
                    mapped = {}
                    break
                mapped[inner_arg] = outer_arg
            if mapped:
                return mapped
    return {}


def _possible_index_overrun_nested_container_context(
    function: CodeEntity,
) -> dict[str, Any] | None:
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) not in {2, 3}:
        return None
    inner_name = parts[-1]
    outer_name = parts[-2]
    if inner_name != function.name:
        return None
    context = _nested_function_context(function.file_path, outer_name, inner_name)
    if context is None:
        return None
    outer_def, inner_def = context
    if not isinstance(outer_def, ast.FunctionDef) or not isinstance(
        inner_def,
        ast.FunctionDef,
    ):
        return None

    if len(parts) == 2:
        return {
            "callable_kind": "nested_index_overrun_function",
            "class_name": "",
            "import_name": outer_name,
            "call_target": outer_name,
            "setup_lines": [],
            "invocation_args": [arg.arg for arg in outer_def.args.args],
            "outer_def": outer_def,
            "inner_def": inner_def,
        }

    class_name = parts[0]
    if not outer_def.args.args or outer_def.args.args[0].arg != "self":
        return None
    instantiation = _class_instantiation_spec(Path(function.file_path), class_name)
    if instantiation is None:
        return None
    return {
        "callable_kind": "nested_index_overrun_method",
        "class_name": class_name,
        "import_name": class_name,
        "call_target": f"__cia_instance.{outer_name}",
        "setup_lines": [f"__cia_instance = {instantiation['expression']}"],
        "invocation_args": [arg.arg for arg in outer_def.args.args[1:]],
        "outer_def": outer_def,
        "inner_def": inner_def,
    }


def _possible_index_overrun_nested_overlay_spec(
    outer_def: ast.FunctionDef,
    inner_def: ast.FunctionDef,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    sequence_arg = _index_overrun_sequence_arg(inner_def)
    if not sequence_arg:
        return None
    outer_arg = _outer_returned_inner_call_argument(
        outer_def,
        inner_def=inner_def,
        inner_arg=sequence_arg,
        invocation_args=invocation_args,
    )
    if not outer_arg:
        return None
    return {
        "call_args": _call_args_with_signature_defaults(
            outer_def,
            invocation_args,
            {outer_arg: "[1]"},
        ),
    }


def _iterator_double_consumption_nested_container_context(
    function: CodeEntity,
) -> dict[str, Any] | None:
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) not in {2, 3}:
        return None
    inner_name = parts[-1]
    outer_name = parts[-2]
    if inner_name != function.name:
        return None
    context = _nested_function_context(function.file_path, outer_name, inner_name)
    if context is None:
        return None
    outer_def, inner_def = context
    if not isinstance(outer_def, ast.FunctionDef) or not isinstance(
        inner_def,
        ast.FunctionDef,
    ):
        return None

    if len(parts) == 2:
        return {
            "callable_kind": "nested_iterator_double_consumption_function",
            "class_name": "",
            "import_name": outer_name,
            "call_target": outer_name,
            "setup_lines": [],
            "invocation_args": [arg.arg for arg in outer_def.args.args],
            "outer_def": outer_def,
            "inner_def": inner_def,
        }

    class_name = parts[0]
    if not outer_def.args.args or outer_def.args.args[0].arg != "self":
        return None
    instantiation = _class_instantiation_spec(Path(function.file_path), class_name)
    if instantiation is None:
        return None
    return {
        "callable_kind": "nested_iterator_double_consumption_method",
        "class_name": class_name,
        "import_name": class_name,
        "call_target": f"__cia_instance.{outer_name}",
        "setup_lines": [f"__cia_instance = {instantiation['expression']}"],
        "invocation_args": [arg.arg for arg in outer_def.args.args[1:]],
        "outer_def": outer_def,
        "inner_def": inner_def,
    }


def _iterator_double_consumption_nested_overlay_spec(
    finding: BugFinding,
    outer_def: ast.FunctionDef,
    inner_def: ast.FunctionDef,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    inner_args = [arg.arg for arg in inner_def.args.args]
    iterator_spec = _iterator_double_consumption_overlay_spec(
        finding,
        inner_def,
        inner_args,
    )
    if iterator_spec is None:
        return None
    iterable = str(finding.evidence.get("iterable") or "")
    if not iterable:
        return None
    outer_arg = _outer_returned_inner_call_argument(
        outer_def,
        inner_def=inner_def,
        inner_arg=iterable,
        invocation_args=invocation_args,
    )
    if not outer_arg:
        return None
    return {
        "setup_lines": list(iterator_spec["setup_lines"]),
        "call_args": _call_args_with_signature_defaults(
            outer_def,
            invocation_args,
            {outer_arg: "__cia_iterator"},
        ),
        "assertion_lines": list(iterator_spec["assertion_lines"]),
    }


def _broad_exception_nested_container_context(
    function: CodeEntity,
) -> dict[str, Any] | None:
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) not in {2, 3}:
        return None
    inner_name = parts[-1]
    outer_name = parts[-2]
    if inner_name != function.name:
        return None
    context = _nested_function_context(function.file_path, outer_name, inner_name)
    if context is None:
        return None
    outer_def, inner_def = context
    if not isinstance(outer_def, ast.FunctionDef) or not isinstance(
        inner_def,
        ast.FunctionDef,
    ):
        return None

    if len(parts) == 2:
        return {
            "callable_kind": "nested_broad_exception_function",
            "class_name": "",
            "import_name": outer_name,
            "call_target": outer_name,
            "setup_lines": [],
            "invocation_args": [arg.arg for arg in outer_def.args.args],
            "outer_def": outer_def,
            "inner_def": inner_def,
        }

    class_name = parts[0]
    if not outer_def.args.args or outer_def.args.args[0].arg != "self":
        return None
    instantiation = _class_instantiation_spec(Path(function.file_path), class_name)
    if instantiation is None:
        return None
    return {
        "callable_kind": "nested_broad_exception_method",
        "class_name": class_name,
        "import_name": class_name,
        "call_target": f"__cia_instance.{outer_name}",
        "setup_lines": [f"__cia_instance = {instantiation['expression']}"],
        "invocation_args": [arg.arg for arg in outer_def.args.args[1:]],
        "outer_def": outer_def,
        "inner_def": inner_def,
    }


def _broad_exception_nested_overlay_spec(
    outer_def: ast.FunctionDef,
    inner_def: ast.FunctionDef,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    inner_args = [arg.arg for arg in inner_def.args.args]
    exception_spec = _broad_exception_overlay_spec(inner_def, inner_args)
    if exception_spec is None:
        return None
    inner_explicit = {
        name: value
        for name, value in zip(inner_args, list(exception_spec["call_args"]))
        if value != "None"
    }
    mapped_explicit: dict[str, str] = {}
    for inner_arg, call_value in inner_explicit.items():
        outer_arg = _outer_returned_inner_call_argument(
            outer_def,
            inner_def=inner_def,
            inner_arg=inner_arg,
            invocation_args=invocation_args,
        )
        if not outer_arg:
            return None
        mapped_explicit[outer_arg] = call_value
    if not mapped_explicit:
        return None
    return {
        "setup_lines": list(exception_spec.get("setup_lines", [])),
        "call_args": _call_args_with_signature_defaults(
            outer_def,
            invocation_args,
            mapped_explicit,
        ),
        "success_exception": str(exception_spec["success_exception"]),
    }


def _is_call_to_name(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    )


def _call_argument_name_for_parameter(
    call: ast.Call,
    *,
    parameter_name: str,
    parameter_index: int,
) -> str:
    for keyword in call.keywords:
        if keyword.arg != parameter_name:
            continue
        return keyword.value.id if isinstance(keyword.value, ast.Name) else ""
    if parameter_index < len(call.args):
        argument = call.args[parameter_index]
        if isinstance(argument, ast.Name):
            return argument.id
    return ""


def _enumerate_start_zero_overlay_spec(
    finding: BugFinding,
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    counter = str(finding.evidence.get("counter") or "")
    if not counter:
        return None
    loop = _observable_enumerate_start_zero_loop(function_def, counter)
    if loop is None:
        return None
    iterable = _enumerate_iterable_argument(loop)
    if not iterable or iterable not in invocation_args:
        return None
    return {
        "setup_lines": ["__cia_sequence = ['item']"],
        "call_args": _call_args(invocation_args, {iterable: "__cia_sequence"}),
        "assertion_lines": [
            "__cia_items = list(__cia_result)",
            "if not __cia_items:",
            (
                "    raise AssertionError("
                "'CIA overlay expected one yielded item from one input item.'"
                ")"
            ),
            "__cia_first = __cia_items[0]",
            "if isinstance(__cia_first, (tuple, list)):",
            "    __cia_observed = __cia_first[0] if __cia_first else None",
            "else:",
            "    __cia_observed = __cia_first",
            "if __cia_observed != 1:",
            (
                "    raise AssertionError("
                "'CIA overlay expected one-based enumerate counter 1, got '"
                " + repr(__cia_observed)"
                ")"
            ),
        ],
    }


def _enumerate_start_zero_average_container_context(
    function: CodeEntity,
) -> dict[str, Any] | None:
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) not in {2, 3}:
        return None
    inner_name = parts[-1]
    outer_name = parts[-2]
    if inner_name != function.name:
        return None
    context = _nested_function_context(function.file_path, outer_name, inner_name)
    if context is None:
        return None
    outer_def, inner_def = context
    if not isinstance(outer_def, ast.FunctionDef) or not isinstance(
        inner_def,
        ast.FunctionDef,
    ):
        return None

    if len(parts) == 2:
        return {
            "callable_kind": "nested_generator_average_function",
            "class_name": "",
            "import_name": outer_name,
            "call_target": outer_name,
            "setup_lines": [],
            "invocation_args": [arg.arg for arg in outer_def.args.args],
            "outer_def": outer_def,
            "inner_def": inner_def,
        }

    class_name = parts[0]
    if not outer_def.args.args or outer_def.args.args[0].arg != "self":
        return None
    instantiation = _class_instantiation_spec(Path(function.file_path), class_name)
    if instantiation is None:
        return None
    return {
        "callable_kind": "nested_generator_average_method",
        "class_name": class_name,
        "import_name": class_name,
        "call_target": f"__cia_instance.{outer_name}",
        "setup_lines": [f"__cia_instance = {instantiation['expression']}"],
        "invocation_args": [arg.arg for arg in outer_def.args.args[1:]],
        "outer_def": outer_def,
        "inner_def": inner_def,
    }


def _enumerate_start_zero_average_overlay_spec(
    finding: BugFinding,
    outer_def: ast.FunctionDef,
    inner_def: ast.FunctionDef,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    counter = str(finding.evidence.get("counter") or "")
    if not counter or not _inner_declares_nonlocal(inner_def, counter):
        return None
    loop = _enumerate_start_zero_loop(inner_def, counter)
    if loop is None or not _loop_body_yields_value(loop):
        return None
    iterable = _enumerate_iterable_argument(loop)
    if not iterable or iterable not in invocation_args:
        return None
    if not _outer_computes_average_with_counter(
        outer_def,
        inner_name=inner_def.name,
        counter=counter,
    ):
        return None
    return {
        "setup_lines": [
            "def __cia_one_item():",
            "    yield 4.0",
        ],
        "call_args": _call_args_with_signature_defaults(
            outer_def,
            invocation_args,
            {iterable: "__cia_one_item()"},
        ),
        "assertion_lines": [
            "__cia_expected = 4.0",
            "if __cia_result != __cia_expected:",
            (
                "    raise AssertionError("
                "'CIA overlay expected one-item iterator average to return '"
                " + repr(__cia_expected) + ', got ' + repr(__cia_result)"
                ")"
            ),
        ],
    }


def _enumerate_start_zero_loop(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    counter: str,
) -> ast.For | None:
    for node in ast.walk(function_def):
        if isinstance(node, ast.For) and _enumerate_start_zero_counter_name(node) == counter:
            return node
    return None


def _inner_declares_nonlocal(function_def: ast.FunctionDef, name: str) -> bool:
    return any(
        isinstance(statement, ast.Nonlocal) and name in statement.names
        for statement in function_def.body
    )


def _loop_body_yields_value(node: ast.For) -> bool:
    return any(
        isinstance(child, (ast.Yield, ast.YieldFrom))
        for statement in node.body
        for child in ast.walk(statement)
    )


def _outer_computes_average_with_counter(
    function_def: ast.FunctionDef,
    *,
    inner_name: str,
    counter: str,
) -> bool:
    return _calls_sum_of_inner_generator(
        function_def,
        inner_name,
    ) and _returns_division_by_counter(function_def, counter)


def _calls_sum_of_inner_generator(
    function_def: ast.FunctionDef,
    inner_name: str,
) -> bool:
    for node in ast.walk(function_def):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "sum":
            continue
        if len(node.args) != 1 or node.keywords:
            continue
        if _is_zero_arg_call_to_name(node.args[0], inner_name):
            return True
    return False


def _is_zero_arg_call_to_name(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
        and not node.args
        and not node.keywords
    )


def _returns_division_by_counter(
    function_def: ast.FunctionDef,
    counter: str,
) -> bool:
    for node in ast.walk(function_def):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        for child in ast.walk(node.value):
            if not isinstance(child, ast.BinOp) or not isinstance(
                child.op,
                (ast.Div, ast.FloorDiv),
            ):
                continue
            if _expression_mentions_name(child.right, counter):
                return True
    return False


def _observable_enumerate_start_zero_loop(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    counter: str,
) -> ast.For | None:
    for node in ast.walk(function_def):
        if not isinstance(node, ast.For):
            continue
        if _enumerate_start_zero_counter_name(node) != counter:
            continue
        if _loop_yields_counter_as_observable_value(node, counter):
            return node
    return None


def _enumerate_start_zero_counter_name(node: ast.For) -> str | None:
    if not isinstance(node.target, ast.Tuple) or not node.target.elts:
        return None
    counter = node.target.elts[0]
    if not isinstance(counter, ast.Name):
        return None
    call = node.iter
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "enumerate"
    ):
        return None
    for keyword in call.keywords:
        if keyword.arg == "start" and _numeric_constant_value(keyword.value) == 0:
            return counter.id
    if len(call.args) >= 2 and _numeric_constant_value(call.args[1]) == 0:
        return counter.id
    return None


def _numeric_constant_value(node: ast.AST) -> int | float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if isinstance(node.value, bool):
            return None
        return node.value
    return None


def _enumerate_iterable_argument(node: ast.For) -> str | None:
    call = node.iter
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "enumerate"
        and call.args
    ):
        return None
    iterable = call.args[0]
    if isinstance(iterable, ast.Name):
        return iterable.id
    return None


def _loop_yields_counter_as_observable_value(node: ast.For, counter: str) -> bool:
    for statement in node.body:
        for child in ast.walk(statement):
            if isinstance(child, ast.Yield) and child.value is not None:
                if _yield_value_exposes_counter_first(child.value, counter):
                    return True
            if isinstance(child, ast.YieldFrom):
                if _yield_value_exposes_counter_first(child.value, counter):
                    return True
    return False


def _yield_value_exposes_counter_first(node: ast.AST, counter: str) -> bool:
    if isinstance(node, ast.Name):
        return node.id == counter
    if isinstance(node, (ast.Tuple, ast.List)) and node.elts:
        return _expression_mentions_name(node.elts[0], counter)
    return False


def _expression_mentions_name(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(child, ast.Name) and child.id == name
        for child in ast.walk(node)
    )


def _stringified_numeric_overlay_spec(
    finding: BugFinding,
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    variable = str(finding.evidence.get("variable") or "")
    if not variable:
        return None
    inner_expression = _stringified_numeric_inner_expression(function_def, variable)
    if inner_expression is None:
        return None
    sequence_arg = _stringified_numeric_subscript_sequence(
        function_def,
        variable=variable,
        invocation_args=invocation_args,
    )
    if not sequence_arg:
        return None
    sample_sequence = [1, 2, 3]
    expected_index = _safe_eval_index_expression(
        inner_expression,
        sequence_arg=sequence_arg,
        sample_sequence=sample_sequence,
    )
    if expected_index is None:
        return None
    expected_value = sample_sequence[expected_index]
    return {
        "setup_lines": ["__cia_sequence = [1, 2, 3]"],
        "call_args": _call_args(invocation_args, {sequence_arg: "__cia_sequence"}),
        "assertion_lines": [
            f"__cia_expected = {expected_value!r}",
            "if __cia_result != __cia_expected:",
            (
                "    raise AssertionError("
                "'CIA overlay expected numeric index repair to return '"
                " + repr(__cia_expected) + ', got ' + repr(__cia_result)"
                ")"
            ),
        ],
    }


def _stringified_numeric_inner_expression(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    variable: str,
) -> ast.expr | None:
    for node in ast.walk(function_def):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id != variable:
            continue
        if not isinstance(node.value, ast.Call):
            continue
        if not isinstance(node.value.func, ast.Name) or node.value.func.id != "str":
            continue
        if len(node.value.args) != 1 or node.value.keywords:
            continue
        return node.value.args[0]
    return None


def _stringified_numeric_subscript_sequence(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    variable: str,
    invocation_args: list[str],
) -> str:
    invocation_arg_set = set(invocation_args)
    for node in ast.walk(function_def):
        if not isinstance(node, ast.Subscript):
            continue
        if not _is_name(node.slice, variable):
            continue
        if isinstance(node.value, ast.Name) and node.value.id in invocation_arg_set:
            return node.value.id
    return ""


def _safe_eval_index_expression(
    expression: ast.expr,
    *,
    sequence_arg: str,
    sample_sequence: list[int],
) -> int | None:
    if not _safe_index_expression(expression, allowed_name=sequence_arg):
        return None
    try:
        value = eval(
            compile(ast.Expression(expression), "<cia-overlay-index>", "eval"),
            {"__builtins__": {}, "len": len},
            {sequence_arg: sample_sequence},
        )
    except Exception:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value >= len(sample_sequence):
        return None
    return value


def _safe_index_expression(node: ast.AST, *, allowed_name: str) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, int) and not isinstance(node.value, bool)
    if isinstance(node, ast.Name):
        return node.id == allowed_name
    if isinstance(node, ast.UnaryOp):
        return isinstance(node.op, (ast.UAdd, ast.USub)) and _safe_index_expression(
            node.operand,
            allowed_name=allowed_name,
        )
    if isinstance(node, ast.BinOp):
        return isinstance(
            node.op,
            (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod),
        ) and _safe_index_expression(
            node.left,
            allowed_name=allowed_name,
        ) and _safe_index_expression(node.right, allowed_name=allowed_name)
    if isinstance(node, ast.Call):
        return (
            isinstance(node.func, ast.Name)
            and node.func.id == "len"
            and len(node.args) == 1
            and not node.keywords
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == allowed_name
        )
    return False


def _iterator_double_consumption_overlay_spec(
    finding: BugFinding,
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    iterable = str(finding.evidence.get("iterable") or "")
    consumer = str(finding.evidence.get("consumer") or "")
    if consumer != "sum" or iterable not in invocation_args:
        return None
    if not _returns_division_by_len_list_count(function_def, iterable):
        return None
    return {
        "setup_lines": ["__cia_iterator = iter([1, 2, 3])"],
        "call_args": _call_args(invocation_args, {iterable: "__cia_iterator"}),
        "assertion_lines": [
            "__cia_expected = 2",
            "if __cia_result != __cia_expected:",
            (
                "    raise AssertionError("
                "'CIA overlay expected iterator materialization repair to return '"
                " + repr(__cia_expected) + ', got ' + repr(__cia_result)"
                ")"
            ),
        ],
    }


def _returns_division_by_len_list_count(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    iterable: str,
) -> bool:
    count_names: set[str] = set()
    for node in ast.walk(function_def):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if _is_len_list_call(node.value, iterable):
            count_names.add(node.targets[0].id)
    if not count_names:
        return False
    for node in ast.walk(function_def):
        if isinstance(node, ast.Return) and node.value is not None:
            if _expression_divides_by_any_name(node.value, count_names):
                return True
    return False


def _is_len_list_call(node: ast.AST, iterable: str) -> bool:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "len"
        and len(node.args) == 1
        and not node.keywords
    ):
        return False
    list_call = node.args[0]
    return (
        isinstance(list_call, ast.Call)
        and isinstance(list_call.func, ast.Name)
        and list_call.func.id == "list"
        and len(list_call.args) == 1
        and not list_call.keywords
        and _is_name(list_call.args[0], iterable)
    )


def _expression_divides_by_any_name(node: ast.AST, names: set[str]) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.BinOp) or not isinstance(
            child.op,
            (ast.Div, ast.FloorDiv),
        ):
            continue
        if isinstance(child.right, ast.Name) and child.right.id in names:
            return True
    return False


def _broad_exception_overlay_spec(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    if not invocation_args:
        return None
    for node in ast.walk(function_def):
        if not isinstance(node, ast.Try):
            continue
        if not any(_handler_is_broad_pass(handler) for handler in node.handlers):
            continue
        for argument in invocation_args:
            exception_name = _empty_guard_exception_name(node.body, argument)
            if exception_name:
                return {
                    "call_args": _call_args_with_signature_defaults(
                        function_def,
                        invocation_args,
                        {argument: "[]"},
                    ),
                    "success_exception": exception_name,
                    "trigger_argument": argument,
                }
    return None


def _broad_exception_property_overlay_spec(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, Any] | None:
    for node in ast.walk(function_def):
        if not isinstance(node, ast.Try):
            continue
        if not any(_handler_is_broad_pass(handler) for handler in node.handlers):
            continue
        exception_name, attribute = _empty_self_attribute_guard_exception(node.body)
        if exception_name:
            return {
                "call_args": [],
                "setup_lines": [f"__cia_instance.{attribute} = []"],
                "success_exception": exception_name,
                "trigger_attribute": attribute,
            }
    return None


def _handler_is_broad_pass(handler: ast.ExceptHandler) -> bool:
    if not (handler.type is None or _is_name(handler.type, "Exception") or _is_name(handler.type, "BaseException")):
        return False
    return len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass)


def _has_broad_exception_swallow(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    return any(
        isinstance(node, ast.Try)
        and any(_handler_is_broad_pass(handler) for handler in node.handlers)
        for node in ast.walk(function_def)
    )


def _function_has_yield(function_def: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(node, ast.Yield) or isinstance(node, ast.YieldFrom)
        for node in ast.walk(function_def)
    )


def _broad_exception_has_fallback_flow(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    return _broad_exception_pass_followed_by_continuation(function_def.body)


def _broad_exception_pass_followed_by_continuation(body: list[ast.stmt]) -> bool:
    for index, statement in enumerate(body):
        if isinstance(statement, ast.Try):
            if any(_handler_is_broad_pass(handler) for handler in statement.handlers):
                if _has_value_continuation(body[index + 1 :]):
                    return True
            if _broad_exception_pass_followed_by_continuation(statement.body):
                return True
            if _broad_exception_pass_followed_by_continuation(statement.orelse):
                return True
            if _broad_exception_pass_followed_by_continuation(statement.finalbody):
                return True
            for handler in statement.handlers:
                if _broad_exception_pass_followed_by_continuation(handler.body):
                    return True
            continue
        nested_bodies = []
        if isinstance(statement, ast.If):
            nested_bodies.extend([statement.body, statement.orelse])
        elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
            nested_bodies.extend([statement.body, statement.orelse])
        elif isinstance(statement, (ast.With, ast.AsyncWith)):
            nested_bodies.append(statement.body)
        for nested in nested_bodies:
            if _broad_exception_pass_followed_by_continuation(nested):
                return True
    return False


def _has_value_continuation(statements: list[ast.stmt]) -> bool:
    for statement in statements:
        if isinstance(statement, ast.Return):
            return statement.value is not None
        if isinstance(statement, (ast.Raise, ast.Pass)):
            continue
        return True
    return False


def _empty_guard_exception_name(body: list[ast.stmt], argument: str) -> str:
    for statement in body:
        if isinstance(statement, ast.If):
            if not _is_empty_guard_for_arg(statement.test, argument):
                continue
            exception_name = _first_raise_name(statement.body)
            if exception_name in {"ValueError", "StatisticsError"}:
                return exception_name
        for child in ast.iter_child_nodes(statement):
            nested = _empty_guard_exception_name(
                [child] if isinstance(child, ast.stmt) else [],
                argument,
            )
            if nested:
                return nested
    return ""


def _empty_self_attribute_guard_exception(body: list[ast.stmt]) -> tuple[str, str]:
    for statement in body:
        if isinstance(statement, ast.If):
            attribute = _empty_guard_self_attribute(statement.test)
            if not attribute:
                continue
            exception_name = _first_raise_name(statement.body)
            if exception_name in {"ValueError", "StatisticsError"}:
                return exception_name, attribute
        for child in ast.iter_child_nodes(statement):
            nested = _empty_self_attribute_guard_exception(
                [child] if isinstance(child, ast.stmt) else [],
            )
            if nested[0]:
                return nested
    return "", ""


def _empty_guard_self_attribute(node: ast.AST) -> str:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _self_attribute_name(node.operand)
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return ""
    left = node.left
    right = node.comparators[0]
    op = node.ops[0]
    left_attr = _len_self_attribute_name(left)
    right_attr = _len_self_attribute_name(right)
    if left_attr and _is_zero_constant(right) and isinstance(op, (ast.Eq, ast.LtE)):
        return left_attr
    if right_attr and _is_zero_constant(left) and isinstance(op, (ast.Eq, ast.GtE)):
        return right_attr
    return ""


def _len_self_attribute_name(node: ast.AST) -> str:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "len"
        and len(node.args) == 1
        and not node.keywords
    ):
        return ""
    return _self_attribute_name(node.args[0])


def _self_attribute_name(node: ast.AST) -> str:
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and _valid_identifier(node.attr)
    ):
        return node.attr
    return ""


def _is_empty_guard_for_arg(node: ast.AST, argument: str) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _is_name(node.operand, argument)
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return False
    left = node.left
    right = node.comparators[0]
    op = node.ops[0]
    if _is_len_call_for_arg(left, argument) and _is_zero_constant(right):
        return isinstance(op, (ast.Eq, ast.LtE))
    if _is_zero_constant(left) and _is_len_call_for_arg(right, argument):
        return isinstance(op, (ast.Eq, ast.GtE))
    return False


def _is_len_call_for_arg(node: ast.AST, argument: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "len"
        and len(node.args) == 1
        and not node.keywords
        and _is_name(node.args[0], argument)
    )


def _is_zero_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == 0


def _first_raise_name(body: list[ast.stmt]) -> str:
    for statement in body:
        for child in ast.walk(statement):
            if not isinstance(child, ast.Raise) or child.exc is None:
                continue
            exc = child.exc
            if isinstance(exc, ast.Call):
                exc = exc.func
            if isinstance(exc, ast.Name):
                return exc.id
    return ""


def _mutable_default_overlay_spec(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    invocation_args: list[str],
    *,
    call_target: str,
) -> dict[str, Any] | None:
    default_arg = _empty_list_default_arg(function_def, invocation_args)
    if not default_arg:
        return None
    value_arg = _single_required_arg_before_default(
        function_def,
        invocation_args,
        default_arg,
    )
    if not value_arg:
        return None
    if not _appends_value_and_returns_default(function_def, default_arg, value_arg):
        return None
    first_args = _call_args([value_arg], {value_arg: repr("__cia_first__")})
    second_args = _call_args([value_arg], {value_arg: repr("__cia_second__")})
    return {
        "setup_lines": [
            f"__cia_first_result = {call_target}({', '.join(first_args)})",
        ],
        "call_args": second_args,
        "assertion_lines": [
            "__cia_expected = ['__cia_second__']",
            "if __cia_result != __cia_expected:",
            (
                "    raise AssertionError("
                "'CIA overlay expected mutable default repair to isolate calls, got '"
                " + repr(__cia_result)"
                ")"
            ),
        ],
    }


def _mutable_default_nested_container_context(
    function: CodeEntity,
) -> dict[str, Any] | None:
    qualified_name = str(function.metadata.get("qualified_name") or function.name)
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) not in {2, 3}:
        return None
    inner_name = parts[-1]
    outer_name = parts[-2]
    if inner_name != function.name:
        return None
    context = _nested_function_context(function.file_path, outer_name, inner_name)
    if context is None:
        return None
    outer_def, inner_def = context
    if not isinstance(outer_def, ast.FunctionDef) or not isinstance(
        inner_def,
        ast.FunctionDef,
    ):
        return None

    if len(parts) == 2:
        return {
            "callable_kind": "nested_mutable_default_function",
            "class_name": "",
            "import_name": outer_name,
            "call_target": outer_name,
            "setup_lines": [],
            "invocation_args": [arg.arg for arg in outer_def.args.args],
            "outer_def": outer_def,
            "inner_def": inner_def,
        }

    class_name = parts[0]
    if not outer_def.args.args or outer_def.args.args[0].arg != "self":
        return None
    instantiation = _class_instantiation_spec(Path(function.file_path), class_name)
    if instantiation is None:
        return None
    return {
        "callable_kind": "nested_mutable_default_method",
        "class_name": class_name,
        "import_name": class_name,
        "call_target": f"__cia_instance.{outer_name}",
        "setup_lines": [f"__cia_instance = {instantiation['expression']}"],
        "invocation_args": [arg.arg for arg in outer_def.args.args[1:]],
        "outer_def": outer_def,
        "inner_def": inner_def,
    }


def _mutable_default_nested_overlay_spec(
    outer_def: ast.FunctionDef,
    inner_def: ast.FunctionDef,
    invocation_args: list[str],
) -> dict[str, Any] | None:
    inner_args = [arg.arg for arg in inner_def.args.args]
    default_arg = _empty_list_default_arg(inner_def, inner_args)
    if not default_arg:
        return None
    value_arg = _single_required_arg_before_default(
        inner_def,
        inner_args,
        default_arg,
    )
    if not value_arg:
        return None
    if not _appends_value_and_returns_default(inner_def, default_arg, value_arg):
        return None
    outer_value = _outer_prior_then_returned_inner_call_argument(
        outer_def,
        inner_def=inner_def,
        inner_arg=value_arg,
        invocation_args=invocation_args,
    )
    if not outer_value:
        return None
    second_args = _call_args_with_signature_defaults(
        outer_def,
        invocation_args,
        {outer_value: repr("__cia_second__")},
    )
    return {
        "setup_lines": [],
        "call_args": second_args,
        "assertion_lines": [
            "__cia_expected = ['__cia_second__']",
            "if __cia_result != __cia_expected:",
            (
                "    raise AssertionError("
                "'CIA overlay expected nested mutable default repair to isolate calls, got '"
                " + repr(__cia_result)"
                ")"
            ),
        ],
    }


def _empty_list_default_arg(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    invocation_args: list[str],
) -> str:
    positional = [arg.arg for arg in function_def.args.args]
    defaults = list(function_def.args.defaults)
    if not positional or not defaults:
        return ""
    default_start = len(positional) - len(defaults)
    invocation_arg_set = set(invocation_args)
    for index, default in enumerate(defaults, start=default_start):
        if index < 0 or index >= len(positional):
            continue
        name = positional[index]
        if name not in invocation_arg_set:
            continue
        if isinstance(default, ast.List) and not default.elts:
            return name
    return ""


def _single_required_arg_before_default(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    invocation_args: list[str],
    default_arg: str,
) -> str:
    positional = [arg.arg for arg in function_def.args.args]
    if default_arg not in positional:
        return ""
    default_index = positional.index(default_arg)
    defaults = list(function_def.args.defaults)
    default_start = len(positional) - len(defaults)
    required_names = [
        name
        for index, name in enumerate(positional[:default_index])
        if name in invocation_args and index < default_start
    ]
    return required_names[0] if len(required_names) == 1 else ""


def _appends_value_and_returns_default(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    default_arg: str,
    value_arg: str,
) -> bool:
    appends_value = False
    returns_default = False
    for node in ast.walk(function_def):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                node.func.attr == "append"
                and _is_name(node.func.value, default_arg)
                and len(node.args) == 1
                and not node.keywords
                and _is_name(node.args[0], value_arg)
            ):
                appends_value = True
        if isinstance(node, ast.Return) and node.value is not None:
            if _is_name(node.value, default_arg):
                returns_default = True
    return appends_value and returns_default


def _index_overrun_sequence_arg(function_def: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    for node in ast.walk(function_def):
        if not isinstance(node, ast.For):
            continue
        if not isinstance(node.target, ast.Name):
            continue
        range_arg = _range_len_arg(node.iter)
        if not range_arg:
            continue
        if _body_reads_positive_offset(
            body=node.body,
            sequence_arg=range_arg,
            index_name=node.target.id,
        ):
            return range_arg
    return ""


def _range_len_arg(node: ast.AST) -> str:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        return ""
    if node.func.id != "range" or len(node.args) != 1:
        return ""
    length_call = node.args[0]
    if not isinstance(length_call, ast.Call):
        return ""
    if not isinstance(length_call.func, ast.Name) or length_call.func.id != "len":
        return ""
    if len(length_call.args) != 1 or not isinstance(length_call.args[0], ast.Name):
        return ""
    return length_call.args[0].id


def _body_reads_positive_offset(
    *,
    body: list[ast.stmt],
    sequence_arg: str,
    index_name: str,
) -> bool:
    for node in body:
        for child in ast.walk(node):
            if not isinstance(child, ast.Subscript):
                continue
            if not isinstance(child.value, ast.Name) or child.value.id != sequence_arg:
                continue
            if _positive_index_offset(child.slice, index_name):
                return True
    return False


def _positive_index_offset(slice_node: ast.AST, index_name: str) -> bool:
    if isinstance(slice_node, ast.BinOp) and isinstance(slice_node.op, ast.Add):
        return (
            isinstance(slice_node.left, ast.Name)
            and slice_node.left.id == index_name
            and isinstance(slice_node.right, ast.Constant)
            and isinstance(slice_node.right.value, int)
            and slice_node.right.value > 0
        )
    return False


def _is_name(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _call_args(args: list[str], explicit: dict[str, str]) -> list[str]:
    return [explicit.get(name, "None") for name in args]


def _call_args_with_signature_defaults(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    args: list[str],
    explicit: dict[str, str],
) -> list[str]:
    defaults = _signature_default_literals(function_def)
    return [explicit.get(name, defaults.get(name, "None")) for name in args]


def _signature_default_literals(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, str]:
    positional = [arg.arg for arg in function_def.args.posonlyargs + function_def.args.args]
    defaults = list(function_def.args.defaults)
    default_start = len(positional) - len(defaults)
    result: dict[str, str] = {}
    for index, default in enumerate(defaults, start=default_start):
        if index < 0 or index >= len(positional):
            continue
        literal = _literal_source(default)
        if literal:
            result[positional[index]] = literal
    for arg, default in zip(function_def.args.kwonlyargs, function_def.args.kw_defaults):
        if default is None:
            continue
        literal = _literal_source(default)
        if literal:
            result[arg.arg] = literal
    return result


def _literal_source(node: ast.AST) -> str:
    try:
        return repr(ast.literal_eval(node))
    except (ValueError, TypeError):
        return ""


def _module_name_for_file(root: Path, file_path: Path) -> str:
    try:
        relative = file_path.resolve().relative_to(root.resolve())
    except ValueError:
        try:
            relative = file_path.relative_to(root)
        except ValueError:
            return ""
    if relative.suffix != ".py":
        return ""
    parts = list(relative.with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(_valid_identifier(part) for part in parts):
        return ""
    return ".".join(parts)


def _valid_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value))


def _relative_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").lower()
    return text or "case"


def _combined_output(execution_result: dict[str, Any]) -> str:
    return (
        str(execution_result.get("stdout_preview") or "")
        + "\n"
        + str(execution_result.get("stderr_preview") or "")
    )


def _skipped(
    *,
    reason: str,
    message: str,
    repository_root: str = "",
    overlay_root: str = "",
    analysis_scope: dict[str, Any] | None = None,
    static_finding_count: int = 0,
    strategy_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "skipped",
        "reason": reason,
        "message": message,
        "repository_root": repository_root,
        "overlay_root": overlay_root,
        "analysis_scope": analysis_scope or {},
        "static_finding_count": static_finding_count,
        "supported_candidate_count": 0,
        "attempted_case_count": 0,
        "selected_case": {},
        "recommended_validation_command": "",
        "execution_result": {},
        "dynamic_evidence": {},
        "attempts": [],
        "strategy_summary": strategy_summary or {},
        "next_actions": _skipped_next_actions(reason),
    }


def _skipped_next_actions(reason: str) -> list[str]:
    if reason == "repository_root_missing":
        return ["Provide a local repository checkout before generating an overlay test."]
    if reason == "no_supported_overlay_candidates":
        return [
            "Add support for another static rule or provide a hand-written failing test.",
            "Run source mining to identify benchmarkable candidate functions.",
        ]
    return []


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


def _clamp_float(value: Any) -> float:
    return max(0.0, min(1.0, _float(value)))


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={_int(value)}" for key, value in sorted(counts.items()))


def _format_list(values: list[Any]) -> str:
    rows = [str(value) for value in values if str(value)]
    return ", ".join(rows) if rows else "none"


def _format_public_api_evidence(evidence: dict[str, Any]) -> str:
    if not evidence:
        return "none"
    scope = str(evidence.get("trigger_scope") or "unknown")
    trigger_expression = str(evidence.get("trigger_expression") or "unknown")
    internal_target = str(evidence.get("internal_target") or "unknown")
    return f"{scope}: {trigger_expression} -> {internal_target}"


def _format_dominant_rejection(summary: dict[str, Any]) -> str:
    reason = str(summary.get("dominant_candidate_rejection_reason") or "")
    if not reason:
        return "none"
    count = _int(summary.get("dominant_candidate_rejection_count", 0))
    return f"{reason}:{count}"


def _format_next_overlay_extension(summary: dict[str, Any]) -> str:
    extension = _dict(summary.get("next_overlay_extension"))
    reason = str(extension.get("reason") or "")
    recommendation = str(extension.get("recommended_extension") or "")
    if not reason and not recommendation:
        return "none"
    if not recommendation:
        return reason
    return f"{reason} -> {recommendation}"


def _format_next_actionable_overlay_extension(summary: dict[str, Any]) -> str:
    extension = _dict(summary.get("next_actionable_overlay_extension"))
    reason = str(extension.get("reason") or "")
    recommendation = str(extension.get("recommended_extension") or "")
    if not reason and not recommendation:
        return "none"
    if not recommendation:
        return reason
    return f"{reason} -> {recommendation}"


def _format_score_preview(items: list[Any]) -> str:
    rows = []
    for item in items[:5]:
        row = _dict(item)
        rule = str(row.get("rule_id") or "none")
        function_name = str(row.get("function_name") or "none")
        score = _float(row.get("overlay_score", 0.0))
        rows.append(f"{rule}:{function_name}:{score:.4f}")
    return ", ".join(rows) if rows else "none"


def _format_rejection_examples(items: list[Any]) -> str:
    rows = []
    for item in items[:5]:
        row = _dict(item)
        rule = str(row.get("rule_id") or "none")
        function_name = str(
            row.get("qualified_name")
            or row.get("function_name")
            or "none"
        )
        reason = str(row.get("reason") or "none")
        rows.append(f"{rule}:{function_name}:{reason}")
    return ", ".join(rows) if rows else "none"


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")
