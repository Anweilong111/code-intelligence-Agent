from __future__ import annotations

import json
import shlex
from dataclasses import replace
from pathlib import Path
from typing import Any

from code_intelligence_agent.agents.bug_detector import RuleBasedBugDetector
from code_intelligence_agent.agents.llm_client import llm_config_audit
from code_intelligence_agent.agents.llm_patch_generator import LLMPatchGenerator
from code_intelligence_agent.agents.patch_generator import PatchGenerator
from code_intelligence_agent.agents.patch_generator_factory import build_patch_generator
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.models import (
    BugFinding,
    FaultLocalizationResult,
    PatchCandidate,
)
from code_intelligence_agent.core.program_graph import build_program_graph
from code_intelligence_agent.core.repo_parser import RepoParser
from code_intelligence_agent.evaluation.repository_test_fault_localization import (
    _parse_repository_scope,
)
from code_intelligence_agent.tools.patch_validation import (
    allow_signature_change_for_rules,
    validate_function_patch,
)


def build_repository_test_patch_candidates(
    fault_localization: dict[str, Any] | None,
    *,
    repository_root: str | Path | None,
    candidate_limit: int = 10,
    analysis_paths: list[str | Path] | None = None,
    parser: RepoParser | None = None,
    detector: RuleBasedBugDetector | None = None,
    generator: PatchGenerator | None = None,
    patch_generation_mode: str = "rule",
    llm_generator: LLMPatchGenerator | None = None,
    llm_candidate_limit: int | None = None,
    candidate_variant_allowlist: list[str] | None = None,
) -> dict[str, Any]:
    mode = _normalize_patch_generation_mode(patch_generation_mode)
    variant_allowlist = _normalize_candidate_variant_allowlist(
        candidate_variant_allowlist
    )
    localization = _dict(fault_localization)
    if not localization:
        return _skipped(
            reason="fault_localization_missing",
            message="Repository test fault localization is not available.",
            patch_generation_mode=mode,
            llm_candidate_limit=llm_candidate_limit,
            candidate_variant_allowlist=variant_allowlist,
        )
    if str(localization.get("status") or "") != "pass":
        return _skipped(
            reason="fault_localization_not_ready",
            message="Patch candidate generation requires a passed fault-localization artifact.",
            fault_localization=localization,
            patch_generation_mode=mode,
            llm_candidate_limit=llm_candidate_limit,
            candidate_variant_allowlist=variant_allowlist,
        )
    if repository_root is None:
        return _skipped(
            reason="repository_root_missing",
            message="Patch candidate generation requires a local repository checkout.",
            fault_localization=localization,
            patch_generation_mode=mode,
            llm_candidate_limit=llm_candidate_limit,
            candidate_variant_allowlist=variant_allowlist,
        )
    root = Path(repository_root)
    if not root.exists() or not root.is_dir():
        return _skipped(
            reason="repository_root_missing",
            message="Repository test root does not exist or is not a directory.",
            fault_localization=localization,
            repository_root=str(root),
            patch_generation_mode=mode,
            llm_candidate_limit=llm_candidate_limit,
            candidate_variant_allowlist=variant_allowlist,
        )

    parsed, analysis_scope = _parse_repository_scope(
        root=root,
        parser=parser or RepoParser(),
        analysis_paths=analysis_paths,
    )
    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    findings = (detector or RuleBasedBugDetector()).detect(parsed.functions)
    ranked = _ranked_results_from_payload(
        localization,
        findings=findings,
    )
    repair_context = _llm_repair_context(localization)
    generation = _generate_candidates(
        mode=mode,
        root=root,
        functions=parsed.functions,
        ranked=ranked,
        candidate_limit=candidate_limit,
        rule_generator=generator,
        llm_generator=llm_generator,
        llm_candidate_limit=llm_candidate_limit,
        candidate_variant_allowlist=variant_allowlist,
        repair_context=repair_context,
    )
    candidates = _apply_candidate_safety_gates(generation["candidates"])
    safety_gate = _candidate_safety_gate_summary(candidates)
    validation_command = str(localization.get("recommended_validation_command") or "")
    validation_args, validation_args_source = _recommended_pytest_args(
        localization,
        validation_command=validation_command,
    )
    status = "pass" if candidates else "warning"
    reason = (
        "patch_candidates_generated"
        if candidates
        else "no_patch_candidates_generated"
    )
    return {
        "status": status,
        "reason": reason,
        "message": (
            "Repository test fault localization was converted into Phase 3 "
            "patch candidates."
        ),
        "repository_root": str(root),
        "analysis_scope": analysis_scope,
        "candidate_limit": candidate_limit,
        "candidate_count": len(candidates),
        "patch_generation_mode": mode,
        "generator_counts": generation["generator_counts"],
        "candidate_variant_filter": generation["candidate_variant_filter"],
        "llm_generation_status": generation["llm_generation_status"],
        "llm_generation_reason": generation["llm_generation_reason"],
        "llm_config_audit": generation["llm_config_audit"],
        "generation_errors": generation["generation_errors"],
        "safety_gate": safety_gate,
        "target_function_count": len(ranked),
        "static_finding_count": len(findings),
        "recommended_validation_command": validation_command,
        "recommended_pytest_args": validation_args,
        "recommended_pytest_args_source": validation_args_source,
        "top_function": str(localization.get("top_function") or ""),
        "top_score": _float(localization.get("top_score", 0.0)),
        "llm_repair_context": repair_context,
        "targets": [_target_summary(item) for item in ranked],
        "candidates": [candidate.to_dict() for candidate in candidates],
        "candidate_rule_counts": _candidate_rule_counts(candidates),
        "next_actions": _next_actions(
            candidate_count=len(candidates),
            validation_command=validation_command,
        ),
    }


def pytest_args_from_python_module_command(command: str) -> list[str]:
    try:
        args = shlex.split(command)
    except ValueError:
        return []
    if len(args) < 3:
        return []
    executable = Path(args[0]).name.lower()
    if executable not in {
        "python",
        "python.exe",
        "python3",
        "python3.exe",
        "py",
        "py.exe",
    }:
        return []
    if args[1] != "-m" or args[2] != "pytest":
        return []
    return [arg for arg in args[3:] if arg not in {"-q", "--quiet"}]


def _recommended_pytest_args(
    localization: dict[str, Any],
    *,
    validation_command: str,
) -> tuple[list[str], str]:
    args = pytest_args_from_python_module_command(validation_command)
    if args:
        return args, "validation_command"
    dynamic_args = _pytest_args_from_dynamic_nodeids(localization)
    if dynamic_args:
        return dynamic_args, "dynamic_evidence_nodeids"
    return [], "unavailable"


def _pytest_args_from_dynamic_nodeids(localization: dict[str, Any]) -> list[str]:
    nodeids: list[str] = []
    for value in _dict(localization.get("dynamic_evidence_nodeids")).values():
        nodeid = str(value or "").strip()
        if nodeid:
            nodeids.append(nodeid)
    for key in ("matched_failing_tests", "unmatched_failing_tests"):
        for item_value in _list(localization.get(key)):
            item = _dict(item_value)
            nodeid = str(item.get("nodeid") or "").strip()
            if nodeid:
                nodeids.append(nodeid)
    return _dedupe_pytest_args(nodeids)


def _dedupe_pytest_args(args: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for arg in args:
        text = str(arg or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _generate_candidates(
    *,
    mode: str,
    root: Path,
    functions,
    ranked: list[FaultLocalizationResult],
    candidate_limit: int,
    rule_generator: PatchGenerator | None,
    llm_generator: LLMPatchGenerator | None,
    llm_candidate_limit: int | None,
    candidate_variant_allowlist: list[str],
    repair_context: dict[str, Any],
) -> dict[str, Any]:
    candidates: list[PatchCandidate] = []
    generation_errors: list[dict[str, str]] = []
    generator_counts = {"rule": 0, "llm": 0}
    llm_audit = llm_config_audit("patch_generation", enabled=mode in {"llm", "hybrid"})
    llm_status = "disabled" if mode == "rule" else "not_run"
    llm_reason = "patch_generation_mode_rule" if mode == "rule" else ""

    if mode in {"rule", "hybrid"} and candidate_limit > 0:
        try:
            rule_candidates = (rule_generator or PatchGenerator()).generate(
                root,
                functions,
                ranked,
                limit=candidate_limit,
            )
            candidates.extend(rule_candidates)
            generator_counts["rule"] = len(rule_candidates)
        except Exception as exc:  # pragma: no cover - defensive artifact path
            generation_errors.append(
                {
                    "source": "rule",
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            )

    if mode in {"llm", "hybrid"}:
        remaining = (
            candidate_limit
            if mode == "llm"
            else max(0, candidate_limit - len(candidates))
        )
        if llm_candidate_limit is not None:
            remaining = min(remaining, max(0, _int(llm_candidate_limit)))
        if remaining <= 0:
            llm_status = "skipped"
            llm_reason = "candidate_limit_exhausted"
        elif not llm_audit.api_key_present and llm_generator is None:
            llm_status = "blocked"
            llm_reason = "missing_llm_api_key"
        else:
            try:
                active_llm_generator = llm_generator or build_patch_generator("llm")
                llm_candidates = active_llm_generator.generate(
                    root,
                    functions,
                    ranked,
                    limit=remaining,
                    repair_context=repair_context,
                )
                candidates.extend(llm_candidates)
                generator_counts["llm"] = len(llm_candidates)
                llm_status = "pass" if llm_candidates else "warning"
                llm_reason = (
                    "llm_patch_candidates_generated"
                    if llm_candidates
                    else "no_llm_patch_candidates_generated"
                )
            except Exception as exc:
                llm_status = "error"
                llm_reason = type(exc).__name__
                generation_errors.append(
                    {
                        "source": "llm",
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                )

    candidates = _dedupe_candidates(candidates)
    variant_filter = _candidate_variant_filter_summary(
        candidates,
        allowlist=candidate_variant_allowlist,
    )
    candidates = _filter_candidate_variants(
        candidates,
        allowlist=candidate_variant_allowlist,
    )[: max(0, candidate_limit)]
    generator_counts = _candidate_generator_counts(candidates)
    return {
        "candidates": candidates,
        "generator_counts": generator_counts,
        "candidate_variant_filter": variant_filter,
        "llm_generation_status": llm_status,
        "llm_generation_reason": llm_reason,
        "llm_config_audit": llm_audit.to_dict(),
        "generation_errors": generation_errors,
    }


def _apply_candidate_safety_gates(
    candidates: list[PatchCandidate],
) -> list[PatchCandidate]:
    gated: list[PatchCandidate] = []
    for candidate in candidates:
        validation = validate_function_patch(
            candidate.old_source,
            candidate.new_source,
            allow_signature_change=allow_signature_change_for_rules(
                [
                    candidate.rule_id,
                    *[
                        str(item)
                        for item in _list(
                            candidate.metadata.get("static_rule_ids")
                        )
                    ],
                ]
            ),
        )
        metadata = {
            **candidate.metadata,
            "validation": validation.to_dict(),
            "safety_gate": {
                "status": "pass" if validation.valid else "blocked",
                "ast_valid": validation.ast_valid,
                "scope_limited": validation.scope_limited,
                "minimal_diff": not (
                    "patch_too_large" in validation.reasons
                    or "patch_change_ratio_too_large" in validation.reasons
                ),
                "signature_change_allowed": validation.signature_change_allowed,
                "reasons": validation.reasons,
            },
        }
        gated.append(replace(candidate, metadata=metadata))
    return gated


def _candidate_safety_gate_summary(
    candidates: list[PatchCandidate],
) -> dict[str, Any]:
    passed = 0
    blocked = 0
    reason_counts: dict[str, int] = {}
    for candidate in candidates:
        safety = _dict(candidate.metadata.get("safety_gate"))
        if safety.get("status") == "pass":
            passed += 1
        else:
            blocked += 1
        for reason in _list(safety.get("reasons")):
            reason_text = str(reason or "unknown")
            reason_counts[reason_text] = reason_counts.get(reason_text, 0) + 1
    return {
        "status": "pass" if blocked == 0 else "blocked",
        "candidate_count": len(candidates),
        "passed_count": passed,
        "blocked_count": blocked,
        "all_candidates_safe": blocked == 0,
        "reason_counts": dict(sorted(reason_counts.items())),
        "required_checks": [
            "ast_valid",
            "scope_limited",
            "signature_guard",
            "minimal_diff",
        ],
    }


def _dedupe_candidates(
    candidates: list[PatchCandidate],
) -> list[PatchCandidate]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[PatchCandidate] = []
    for candidate in candidates:
        key = (
            candidate.target_function_id,
            candidate.relative_file_path,
            candidate.rule_id,
            candidate.new_source,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _candidate_generator_counts(
    candidates: list[PatchCandidate],
) -> dict[str, int]:
    counts: dict[str, int] = {"rule": 0, "llm": 0}
    for candidate in candidates:
        generator = str(candidate.metadata.get("generator") or "")
        if generator.startswith("llm"):
            counts["llm"] += 1
        else:
            counts["rule"] += 1
    return counts


def _normalize_candidate_variant_allowlist(
    values: list[str] | None,
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _filter_candidate_variants(
    candidates: list[PatchCandidate],
    *,
    allowlist: list[str],
) -> list[PatchCandidate]:
    if not allowlist:
        return candidates
    allowed = set(allowlist)
    return [
        candidate
        for candidate in candidates
        if str(candidate.metadata.get("variant") or "") in allowed
    ]


def _candidate_variant_filter_summary(
    candidates: list[PatchCandidate],
    *,
    allowlist: list[str],
) -> dict[str, Any]:
    input_count = len(candidates)
    if not allowlist:
        return {
            "enabled": False,
            "allowlist": [],
            "input_count": input_count,
            "kept_count": input_count,
            "dropped_count": 0,
            "dropped_variant_counts": {},
        }
    allowed = set(allowlist)
    kept = 0
    dropped_counts: dict[str, int] = {}
    for candidate in candidates:
        variant = str(candidate.metadata.get("variant") or "unknown")
        if variant in allowed:
            kept += 1
            continue
        dropped_counts[variant] = dropped_counts.get(variant, 0) + 1
    return {
        "enabled": True,
        "allowlist": allowlist,
        "input_count": input_count,
        "kept_count": kept,
        "dropped_count": input_count - kept,
        "dropped_variant_counts": dict(sorted(dropped_counts.items())),
    }


def _normalize_patch_generation_mode(mode: str) -> str:
    normalized = str(mode or "rule").strip().lower().replace("-", "_")
    if normalized in {"rule", "rule_based", "rules"}:
        return "rule"
    if normalized in {"llm", "model"}:
        return "llm"
    if normalized in {"hybrid", "rule_llm", "rule_plus_llm"}:
        return "hybrid"
    raise ValueError(f"Unsupported patch generation mode: {mode}")


def render_repository_test_patch_candidates_markdown(payload: dict[str, Any]) -> str:
    analysis_scope = _dict(payload.get("analysis_scope"))
    generator_counts = _dict(payload.get("generator_counts"))
    variant_filter = _dict(payload.get("candidate_variant_filter"))
    safety_gate = _dict(payload.get("safety_gate"))
    llm_audit = _dict(payload.get("llm_config_audit"))
    lines = [
        "# Repository Test Patch Candidates",
        "",
        f"- Status: `{_markdown_cell(payload.get('status', ''))}`",
        f"- Reason: `{_markdown_cell(payload.get('reason', ''))}`",
        f"- Repository Root: `{_markdown_cell(payload.get('repository_root') or 'none')}`",
        f"- Scoped Analysis: {str(bool(analysis_scope.get('enabled', False))).lower()}",
        (
            "- Analysis Files: "
            f"`{_markdown_cell(_format_list(_list(analysis_scope.get('existing_files'))))}`"
        ),
        f"- Candidate Count: {_int(payload.get('candidate_count', 0))}",
        f"- Patch Generation Mode: `{_markdown_cell(payload.get('patch_generation_mode') or 'rule')}`",
        (
            "- Generator Counts: "
            f"rule={_int(generator_counts.get('rule', 0))}, "
            f"llm={_int(generator_counts.get('llm', 0))}"
        ),
        (
            "- LLM Generation: "
            f"`{_markdown_cell(payload.get('llm_generation_status') or 'disabled')}` "
            f"({_markdown_cell(payload.get('llm_generation_reason') or 'none')})"
        ),
        (
            "- Candidate Variant Filter: "
            f"enabled={str(bool(variant_filter.get('enabled', False))).lower()}, "
            f"allowlist=`{_markdown_cell(_format_list(_list(variant_filter.get('allowlist'))))}`, "
            f"kept={_int(variant_filter.get('kept_count', 0))}/"
            f"{_int(variant_filter.get('input_count', 0))}, "
            f"dropped={_int(variant_filter.get('dropped_count', 0))}"
        ),
        (
            "- LLM Config: "
            f"enabled={str(bool(llm_audit.get('enabled', False))).lower()}, "
            f"provider=`{_markdown_cell(llm_audit.get('provider') or 'none')}`, "
            f"model=`{_markdown_cell(llm_audit.get('model') or 'none')}`, "
            f"api_key_present={str(bool(llm_audit.get('api_key_present', False))).lower()}"
        ),
        (
            "- Safety Gate: "
            f"`{_markdown_cell(safety_gate.get('status') or 'unknown')}` "
            f"passed={_int(safety_gate.get('passed_count', 0))}, "
            f"blocked={_int(safety_gate.get('blocked_count', 0))}"
        ),
        f"- Target Function Count: {_int(payload.get('target_function_count', 0))}",
        f"- Static Findings: {_int(payload.get('static_finding_count', 0))}",
        f"- Top Function: `{_markdown_cell(payload.get('top_function') or 'none')}`",
        (
            "- Recommended Validation Command: "
            f"`{_markdown_cell(payload.get('recommended_validation_command') or 'none')}`"
        ),
        (
            "- Recommended Pytest Args: "
            f"`{_markdown_cell(' '.join(str(item) for item in _list(payload.get('recommended_pytest_args'))) or 'none')}`"
        ),
        (
            "- Recommended Pytest Args Source: "
            f"`{_markdown_cell(payload.get('recommended_pytest_args_source') or 'none')}`"
        ),
        "",
        "## Targets",
        "",
        "| Rank | Function | Score | Finding Count |",
        "| ---: | --- | ---: | ---: |",
    ]
    for item in _list(payload.get("targets")):
        row = _dict(item)
        lines.append(
            "| "
            f"{_int(row.get('rank', 0))} | "
            f"`{_markdown_cell(row.get('function_name', ''))}` | "
            f"{_float(row.get('score', 0.0)):.4f} | "
            f"{_int(row.get('finding_count', 0))} |"
        )
    if not _list(payload.get("targets")):
        lines.append("| 0 | none | 0.0000 | 0 |")
    lines.extend(
        [
            "",
            "## Candidates",
            "",
            "| Index | Generator | Rule | Variant | Safety | Target Function | Relative File | Confidence |",
            "| ---: | --- | --- | --- | --- | --- | --- | ---: |",
        ]
    )
    for index, item in enumerate(_list(payload.get("candidates")), start=1):
        row = _dict(item)
        metadata = _dict(row.get("metadata"))
        safety = _dict(metadata.get("safety_gate"))
        lines.append(
            "| "
            f"{index} | "
            f"`{_markdown_cell(metadata.get('generator') or 'unknown')}` | "
            f"`{_markdown_cell(row.get('rule_id', ''))}` | "
            f"`{_markdown_cell(metadata.get('variant') or 'none')}` | "
            f"`{_markdown_cell(safety.get('status') or 'unknown')}` | "
            f"`{_markdown_cell(row.get('target_function_name', ''))}` | "
            f"`{_markdown_cell(row.get('relative_file_path', ''))}` | "
            f"{_float(metadata.get('confidence', 0.0)):.4f} |"
        )
    if not _list(payload.get("candidates")):
        lines.append("| 0 | none | none | none | none | none | none | 0.0000 |")
    lines.extend(["", "## Safety Gate", ""])
    lines.append(f"- Status: `{_markdown_cell(safety_gate.get('status') or 'unknown')}`")
    lines.append(f"- All Candidates Safe: {str(bool(safety_gate.get('all_candidates_safe', False))).lower()}")
    lines.append(f"- Required Checks: `{_markdown_cell(_format_list(_list(safety_gate.get('required_checks'))))}`")
    reason_counts = _dict(safety_gate.get("reason_counts"))
    if reason_counts:
        lines.append("")
        lines.append("| Reason | Count |")
        lines.append("| --- | ---: |")
        for reason, count in sorted(reason_counts.items()):
            lines.append(f"| `{_markdown_cell(reason)}` | {_int(count)} |")
    lines.extend(["", "## Candidate Rule Counts", ""])
    rule_counts = _dict(payload.get("candidate_rule_counts"))
    for rule, count in sorted(rule_counts.items()):
        lines.append(f"- `{_markdown_cell(rule)}`: {_int(count)}")
    if not rule_counts:
        lines.append("- none")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    return "\n".join(lines)


def write_repository_test_patch_candidates_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_patch_candidates.json"
    markdown_path = root / "repository_test_patch_candidates.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_patch_candidates_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_patch_candidates_json": str(json_path),
        "repository_test_patch_candidates_markdown": str(markdown_path),
    }


def _ranked_results_from_payload(
    payload: dict[str, Any],
    *,
    findings: list[BugFinding],
) -> list[FaultLocalizationResult]:
    findings_by_function: dict[str, list[BugFinding]] = {}
    for finding in findings:
        findings_by_function.setdefault(finding.function_id, []).append(finding)
    ranked = []
    for item in _list(payload.get("rankings")):
        row = _dict(item)
        function_id = str(row.get("function_id") or "")
        if not function_id:
            continue
        ranked.append(
            FaultLocalizationResult(
                function_id=function_id,
                function_name=str(row.get("function_name") or ""),
                file_path=str(row.get("file_path") or ""),
                start_line=_int(row.get("start_line", 0)),
                end_line=_int(row.get("end_line", 0)),
                score=_float(row.get("score", 0.0)),
                rank=_int(row.get("rank", 0)),
                signals={
                    str(name): _float(value)
                    for name, value in _dict(row.get("signals")).items()
                },
                findings=findings_by_function.get(function_id, []),
                reason=str(row.get("reason") or ""),
            )
        )
    return sorted(ranked, key=lambda item: item.rank or 10**9)


def _llm_repair_context(localization: dict[str, Any]) -> dict[str, Any]:
    overlay_context = _dict(localization.get("overlay_case_context"))
    public_api = _dict(localization.get("public_api_evidence"))
    context = {
        "dynamic_evidence_level": str(
            localization.get("dynamic_evidence_level") or ""
        ),
        "recommended_validation_command": str(
            localization.get("recommended_validation_command") or ""
        ),
        "dynamic_evidence_nodeids": _dict(
            localization.get("dynamic_evidence_nodeids")
        ),
        "public_api_evidence": public_api,
        "overlay_case_context": overlay_context,
        "matched_failing_tests": _compact_test_rows(
            localization.get("matched_failing_tests")
        ),
        "unmatched_failing_tests": _compact_test_rows(
            localization.get("unmatched_failing_tests")
        ),
    }
    if overlay_context:
        context["oracle_policy"] = {
            "expected_exception_semantics": (
                "legacy_failure_to_avoid_not_desired_exception"
            ),
            "repair_priority": (
                "Pass the recommended validation command while preserving the "
                "localized public API contract."
            ),
        }
    return {
        key: value
        for key, value in context.items()
        if value not in ("", [], {}, None)
    }


def _compact_test_rows(value: Any) -> list[dict[str, str]]:
    rows = []
    for item_value in _list(value):
        item = _dict(item_value)
        rows.append(
            {
                key: str(item.get(key) or "")
                for key in ("nodeid", "path", "test_name", "source_line")
                if str(item.get(key) or "")
            }
        )
    return rows


def _target_summary(item: FaultLocalizationResult) -> dict[str, Any]:
    return {
        "rank": item.rank,
        "function_id": item.function_id,
        "function_name": item.function_name,
        "file_path": item.file_path,
        "start_line": item.start_line,
        "end_line": item.end_line,
        "score": item.score,
        "finding_count": len(item.findings),
        "rule_ids": [finding.rule_id for finding in item.findings],
    }


def _candidate_rule_counts(candidates) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.rule_id] = counts.get(candidate.rule_id, 0) + 1
    return dict(sorted(counts.items()))


def _skipped(
    *,
    reason: str,
    message: str,
    fault_localization: dict[str, Any] | None = None,
    repository_root: str = "",
    patch_generation_mode: str = "rule",
    llm_candidate_limit: int | None = None,
    candidate_variant_allowlist: list[str] | None = None,
) -> dict[str, Any]:
    localization = _dict(fault_localization)
    mode = _normalize_patch_generation_mode(patch_generation_mode)
    llm_enabled = mode in {"llm", "hybrid"}
    return {
        "status": "skipped",
        "reason": reason,
        "message": message,
        "repository_root": repository_root,
        "candidate_limit": 0,
        "candidate_count": 0,
        "patch_generation_mode": mode,
        "generator_counts": {"rule": 0, "llm": 0},
        "candidate_variant_filter": _candidate_variant_filter_summary(
            [],
            allowlist=_normalize_candidate_variant_allowlist(
                candidate_variant_allowlist
            ),
        ),
        "llm_generation_status": "blocked" if llm_enabled else "disabled",
        "llm_generation_reason": (
            reason if llm_enabled else "patch_generation_mode_rule"
        ),
        "llm_candidate_limit": llm_candidate_limit,
        "llm_config_audit": llm_config_audit(
            "patch_generation",
            enabled=llm_enabled,
        ).to_dict(),
        "generation_errors": [],
        "safety_gate": {
            "status": "skipped",
            "candidate_count": 0,
            "passed_count": 0,
            "blocked_count": 0,
            "blocked_candidate_ids": [],
        },
        "target_function_count": _int(localization.get("ranking_count", 0)),
        "static_finding_count": 0,
        "recommended_validation_command": str(
            localization.get("recommended_validation_command") or ""
        ),
        "recommended_pytest_args": [],
        "recommended_pytest_args_source": "unavailable",
        "top_function": str(localization.get("top_function") or ""),
        "top_score": _float(localization.get("top_score", 0.0)),
        "targets": [],
        "candidates": [],
        "candidate_rule_counts": {},
        "next_actions": _skipped_next_actions(reason),
    }


def _next_actions(
    *,
    candidate_count: int,
    validation_command: str,
) -> list[str]:
    if candidate_count <= 0:
        return [
            "Inspect repository_test_fault_localization.md and static rule findings; no supported rule-based patch was generated for the localized functions."
        ]
    actions = [
        "Use these candidates as the Phase 3 repair-loop input.",
    ]
    if validation_command:
        actions.append(f"Validate candidates with: {validation_command}")
    return actions


def _skipped_next_actions(reason: str) -> list[str]:
    if reason == "fault_localization_not_ready":
        return ["Produce a passed repository_test_fault_localization artifact first."]
    if reason == "repository_root_missing":
        return ["Provide --repository-test-root or enable --checkout-repository-tests."]
    if reason == "fault_localization_missing":
        return ["Run repository test fault localization before patch candidate generation."]
    return []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _format_list(values: list[Any]) -> str:
    rows = [str(value) for value in values if str(value)]
    return ", ".join(rows) if rows else "none"


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
    return str(value).replace("\n", " ").replace("|", "\\|")
