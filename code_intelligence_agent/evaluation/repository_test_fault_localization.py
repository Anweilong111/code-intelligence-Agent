from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from code_intelligence_agent.agents.bug_detector import RuleBasedBugDetector
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.fault_localizer import (
    FaultLocalizer,
    evidence_v2_localization_config,
)
from code_intelligence_agent.core.git_change_history import (
    GitChangeHistoryAnalyzer,
    GitChangeHistoryResult,
)
from code_intelligence_agent.core.models import RepoParseResult, TestExecutionSummary
from code_intelligence_agent.core.program_graph import ProgramGraph, build_program_graph
from code_intelligence_agent.core.repo_parser import RepoParser


def build_repository_test_fault_localization(
    dynamic_evidence: dict[str, Any] | None,
    *,
    repository_root: str | Path | None,
    top_k: int = 10,
    analysis_paths: list[str | Path] | None = None,
    parser: RepoParser | None = None,
    detector: RuleBasedBugDetector | None = None,
    localizer: FaultLocalizer | None = None,
    history_analyzer: GitChangeHistoryAnalyzer | None = None,
) -> dict[str, Any]:
    evidence = _dict(dynamic_evidence)
    if not evidence:
        return _skipped(
            reason="dynamic_evidence_missing",
            message="Repository test dynamic evidence is not available.",
        )
    if not bool(evidence.get("usable_for_localization", False)):
        return _skipped(
            reason="dynamic_evidence_not_usable",
            message=(
                "Repository test dynamic evidence did not contain failing tests usable "
                "for fault localization."
            ),
            dynamic_evidence=evidence,
        )
    if repository_root is None:
        return _skipped(
            reason="repository_root_missing",
            message="Fault localization requires a local repository checkout.",
            dynamic_evidence=evidence,
        )
    root = Path(repository_root)
    if not root.exists() or not root.is_dir():
        return _skipped(
            reason="repository_root_missing",
            message="Repository test root does not exist or is not a directory.",
            dynamic_evidence=evidence,
            repository_root=str(root),
        )

    parsed, analysis_scope = _parse_repository_scope(
        root=root,
        parser=parser or RepoParser(),
        analysis_paths=analysis_paths,
    )
    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    findings = (detector or RuleBasedBugDetector()).detect(parsed.functions)
    summary, summary_metadata = dynamic_evidence_to_test_summary(
        program_graph,
        evidence,
    )
    selected_localizer = localizer or FaultLocalizer(
        evidence_v2_localization_config()
    )
    history = (history_analyzer or GitChangeHistoryAnalyzer()).analyze(
        root,
        parsed.functions,
    )
    ranked = selected_localizer.rank(
        program_graph,
        findings,
        summary,
        top_k=top_k,
        change_history_scores=history.scores,
    )
    active_weights = (
        selected_localizer.config.coverage_weights
        if summary.has_coverage()
        else selected_localizer.config.static_only_weights
    )
    status = "pass" if ranked else "warning"
    reason = (
        "localized_from_dynamic_evidence"
        if (
            summary_metadata["matched_failed_test_count"] > 0
            or summary_metadata["matched_traceback_frame_count"] > 0
        )
        else "localized_with_unmatched_dynamic_tests"
    )
    return {
        "status": status,
        "reason": reason,
        "message": (
            "Repository test dynamic evidence was converted into a Phase 2 "
            "fault-localization ranking."
        ),
        "repository_root": str(root),
        "analysis_scope": analysis_scope,
        "top_k": top_k,
        "dynamic_evidence_level": str(evidence.get("evidence_level") or ""),
        "recommended_validation_command": str(
            evidence.get("recommended_validation_command") or ""
        ),
        "parsed_function_count": len(parsed.functions),
        "parsed_test_count": len(parsed.tests),
        "static_finding_count": len(findings),
        "scoring_profile": selected_localizer.config.fusion_profile,
        "score_formula": (
            "clamp(sum(weight_i * normalized_signal_i) - "
            "weight_risk * patch_risk, 0, 1)"
        ),
        "score_weights": active_weights.to_dict(),
        "score_components": [
            "static",
            "graph",
            "test_failure",
            "traceback",
            "sbfl",
            "semantic",
            "llm",
            "complexity",
            "change_history",
            "risk",
        ],
        "git_change_history": _history_payload(history, ranked),
        **summary_metadata,
        "ranking_count": len(ranked),
        "top_function": ranked[0].function_name if ranked else "",
        "top_function_id": ranked[0].function_id if ranked else "",
        "top_score": ranked[0].score if ranked else 0.0,
        "public_api_evidence": _dict(evidence.get("public_api_evidence")),
        "overlay_case_context": _dict(evidence.get("overlay_case_context")),
        "rankings": [item.to_dict() for item in ranked],
        "next_actions": _next_actions(
            matched_failed_test_count=summary_metadata["matched_failed_test_count"],
            matched_traceback_frame_count=summary_metadata[
                "matched_traceback_frame_count"
            ],
            ranking_count=len(ranked),
            command=str(evidence.get("recommended_validation_command") or ""),
        ),
    }


def dynamic_evidence_to_test_summary(
    program_graph: ProgramGraph,
    dynamic_evidence: dict[str, Any],
) -> tuple[TestExecutionSummary, dict[str, Any]]:
    failed_ids: set[str] = set()
    coverage: dict[str, set[str]] = {}
    test_names: dict[str, str] = {}
    failure_messages: dict[str, str] = {}
    dynamic_test_ids: set[str] = set()
    dynamic_nodeids: dict[str, str] = {}
    unmatched_nodeids: set[str] = set()
    matched_tests: list[dict[str, str]] = []
    unmatched_tests: list[dict[str, str]] = []
    traceback_function_ids: set[str] = set()
    matched_traceback_frames: list[dict[str, Any]] = []
    unmatched_traceback_frames: list[dict[str, Any]] = []
    diagnostic = str(dynamic_evidence.get("diagnostic_summary") or "")
    failure_signal = str(dynamic_evidence.get("failure_signal") or "")

    for row in _list(dynamic_evidence.get("failing_tests")):
        item = _dict(row)
        nodeid = str(item.get("nodeid") or "").strip()
        if not nodeid:
            continue
        match = _match_test_function(program_graph, item)
        test_id = match.get("function_id") or f"dynamic_test::{nodeid}"
        failed_ids.add(test_id)
        dynamic_test_ids.add(test_id)
        dynamic_nodeids[test_id] = nodeid
        test_names[test_id] = str(item.get("test_name") or nodeid)
        failure_messages[test_id] = " ".join(
            part
            for part in (
                str(item.get("source_line") or ""),
                failure_signal,
                diagnostic,
            )
            if part
        )
        if match:
            direct_targets = _direct_test_targets(program_graph, test_id)
            if direct_targets:
                coverage[test_id] = direct_targets
            matched_tests.append({**match, "nodeid": nodeid})
        else:
            unmatched_nodeids.add(nodeid)
            unmatched_tests.append(
                {
                    "nodeid": nodeid,
                    "path": str(item.get("path") or ""),
                    "test_name": str(item.get("test_name") or ""),
                }
            )

    for row in _list(dynamic_evidence.get("traceback_frames")):
        frame = _dict(row)
        match = _match_traceback_frame(program_graph, frame)
        frame_summary = {
            "path": str(frame.get("path") or ""),
            "line": _int(frame.get("line", 0)),
            "function_name": str(frame.get("function_name") or ""),
            "source_line": str(frame.get("source_line") or ""),
        }
        if match:
            traceback_function_ids.add(str(match["function_id"]))
            matched_traceback_frames.append({**frame_summary, **match})
        else:
            unmatched_traceback_frames.append(frame_summary)

    if traceback_function_ids:
        if failed_ids:
            for test_id in list(failed_ids):
                coverage.setdefault(test_id, set()).update(traceback_function_ids)
        else:
            synthetic_test_id = _synthetic_traceback_test_id(
                matched_traceback_frames
            )
            failed_ids.add(synthetic_test_id)
            dynamic_test_ids.add(synthetic_test_id)
            coverage[synthetic_test_id] = set(traceback_function_ids)
            test_names[synthetic_test_id] = "traceback_failure"
            failure_messages[synthetic_test_id] = " ".join(
                part
                for part in (
                    failure_signal,
                    diagnostic,
                    _traceback_frame_message(matched_traceback_frames),
                )
                if part
            )

    summary = TestExecutionSummary(
        failed_tests=failed_ids,
        coverage=coverage,
        traceback_function_ids=traceback_function_ids,
        dynamic_traceback_function_ids=traceback_function_ids,
        test_names=test_names,
        failure_messages=failure_messages,
        dynamic_evidence_test_ids=dynamic_test_ids,
        dynamic_evidence_nodeids=dynamic_nodeids,
        dynamic_evidence_unmatched_nodeids=unmatched_nodeids,
    )
    metadata = {
        "failed_test_count": len(failed_ids),
        "matched_failed_test_count": len(matched_tests),
        "unmatched_failed_test_count": len(unmatched_tests),
        "coverage_inferred_from_test_graph_count": sum(
            1 for targets in coverage.values() if targets
        ),
        "matched_failing_tests": matched_tests,
        "unmatched_failing_tests": unmatched_tests,
        "traceback_frame_count": len(_list(dynamic_evidence.get("traceback_frames"))),
        "matched_traceback_frame_count": len(matched_traceback_frames),
        "unmatched_traceback_frame_count": len(unmatched_traceback_frames),
        "matched_traceback_frames": matched_traceback_frames,
        "unmatched_traceback_frames": unmatched_traceback_frames,
        "dynamic_test_ids": sorted(dynamic_test_ids),
        "dynamic_evidence_nodeids": dict(sorted(dynamic_nodeids.items())),
    }
    return summary, metadata


def render_repository_test_fault_localization_markdown(
    payload: dict[str, Any],
) -> str:
    analysis_scope = _dict(payload.get("analysis_scope"))
    lines = [
        "# Repository Test Fault Localization",
        "",
        f"- Status: `{_markdown_cell(payload.get('status', ''))}`",
        f"- Reason: `{_markdown_cell(payload.get('reason', ''))}`",
        f"- Repository Root: `{_markdown_cell(payload.get('repository_root') or 'none')}`",
        f"- Scoped Analysis: {str(bool(analysis_scope.get('enabled', False))).lower()}",
        (
            "- Analysis Files: "
            f"`{_markdown_cell(_format_list(_list(analysis_scope.get('existing_files'))))}`"
        ),
        f"- Dynamic Evidence Level: `{_markdown_cell(payload.get('dynamic_evidence_level') or 'none')}`",
        f"- Scoring Profile: `{_markdown_cell(payload.get('scoring_profile') or 'none')}`",
        f"- Score Formula: `{_markdown_cell(payload.get('score_formula') or 'none')}`",
        (
            "- Recommended Validation Command: "
            f"`{_markdown_cell(payload.get('recommended_validation_command') or 'none')}`"
        ),
        f"- Parsed Functions: {_int(payload.get('parsed_function_count', 0))}",
        f"- Parsed Tests: {_int(payload.get('parsed_test_count', 0))}",
        f"- Static Findings: {_int(payload.get('static_finding_count', 0))}",
        f"- Failed Tests: {_int(payload.get('failed_test_count', 0))}",
        f"- Matched Failed Tests: {_int(payload.get('matched_failed_test_count', 0))}",
        f"- Unmatched Failed Tests: {_int(payload.get('unmatched_failed_test_count', 0))}",
        f"- Traceback Frames: {_int(payload.get('traceback_frame_count', 0))}",
        f"- Matched Traceback Frames: {_int(payload.get('matched_traceback_frame_count', 0))}",
        f"- Unmatched Traceback Frames: {_int(payload.get('unmatched_traceback_frame_count', 0))}",
        f"- Ranking Count: {_int(payload.get('ranking_count', 0))}",
        f"- Top Function: `{_markdown_cell(payload.get('top_function') or 'none')}`",
        (
            "- Public API Evidence: "
            f"`{_markdown_cell(_format_public_api_evidence(_dict(payload.get('public_api_evidence'))))}`"
        ),
        "",
        "## Rankings",
        "",
        "| Rank | Function | File | Score | Test Failure | Traceback | Static | Graph | SBFL | Complexity | History |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in _list(payload.get("rankings")):
        row = _dict(item)
        signals = _dict(row.get("signals"))
        lines.append(
            "| "
            f"{_int(row.get('rank', 0))} | "
            f"`{_markdown_cell(row.get('function_name', ''))}` | "
            f"`{_markdown_cell(row.get('file_path', ''))}` | "
            f"{_float(row.get('score', 0.0)):.4f} | "
            f"{_float(signals.get('test_failure', signals.get('dynamic_test_evidence', 0.0))):.4f} | "
            f"{_float(signals.get('traceback', 0.0)):.4f} | "
            f"{_float(signals.get('static', 0.0)):.4f} | "
            f"{_float(signals.get('graph', 0.0)):.4f} | "
            f"{_float(signals.get('sbfl', 0.0)):.4f} | "
            f"{_float(signals.get('complexity', 0.0)):.4f} | "
            f"{_float(signals.get('change_history', 0.0)):.4f} |"
        )
    if not _list(payload.get("rankings")):
        lines.append(
            "| 0 | none | none | 0.0000 | 0.0000 | 0.0000 | 0.0000 | "
            "0.0000 | 0.0000 | 0.0000 | 0.0000 |"
        )
    lines.extend(
        [
            "",
            "## FinalScore Contribution Decomposition",
            "",
            "| Rank | Function | Static | Graph | Test Failure | Traceback | SBFL | Semantic | LLM | Complexity | History | Risk | Clamp |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in _list(payload.get("rankings")):
        row = _dict(item)
        signals = _dict(row.get("signals"))
        lines.append(
            "| "
            f"{_int(row.get('rank', 0))} | "
            f"`{_markdown_cell(row.get('function_name', ''))}` | "
            f"{_float(signals.get('contribution_static', 0.0)):.4f} | "
            f"{_float(signals.get('contribution_graph', 0.0)):.4f} | "
            f"{_float(signals.get('contribution_test_failure', 0.0)):.4f} | "
            f"{_float(signals.get('contribution_traceback', 0.0)):.4f} | "
            f"{_float(signals.get('contribution_sbfl', 0.0)):.4f} | "
            f"{_float(signals.get('contribution_semantic', 0.0)):.4f} | "
            f"{_float(signals.get('contribution_llm', 0.0)):.4f} | "
            f"{_float(signals.get('contribution_complexity', 0.0)):.4f} | "
            f"{_float(signals.get('contribution_change_history', 0.0)):.4f} | "
            f"{_float(signals.get('contribution_risk', 0.0)):.4f} | "
            f"{_float(signals.get('contribution_clamp_adjustment', 0.0)):.4f} |"
        )
    if not _list(payload.get("rankings")):
        lines.append(
            "| 0 | none | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | "
            "0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |"
        )
    history = _dict(payload.get("git_change_history"))
    lines.extend(
        [
            "",
            "## Git Change History Evidence",
            "",
            f"- Status: `{_markdown_cell(history.get('status') or 'none')}`",
            f"- Reason: `{_markdown_cell(history.get('reason') or 'none')}`",
            f"- Commit: `{_markdown_cell(history.get('commit') or 'none')}`",
            f"- Scored Functions: {_int(history.get('scored_function_count', 0))}",
        ]
    )
    lines.extend(["", "## Matched Failing Tests", ""])
    for item in _list(payload.get("matched_failing_tests")):
        row = _dict(item)
        lines.append(
            f"- `{_markdown_cell(row.get('nodeid', ''))}` -> "
            f"`{_markdown_cell(row.get('qualified_name') or row.get('name') or '')}`"
        )
    if not _list(payload.get("matched_failing_tests")):
        lines.append("- none")
    lines.extend(["", "## Unmatched Failing Tests", ""])
    for item in _list(payload.get("unmatched_failing_tests")):
        row = _dict(item)
        lines.append(f"- `{_markdown_cell(row.get('nodeid', ''))}`")
    if not _list(payload.get("unmatched_failing_tests")):
        lines.append("- none")
    lines.extend(["", "## Matched Traceback Frames", ""])
    for item in _list(payload.get("matched_traceback_frames")):
        row = _dict(item)
        lines.append(
            f"- `{_markdown_cell(row.get('path') or row.get('file_path') or '')}`:"
            f"{_int(row.get('line', 0))} "
            f"`{_markdown_cell(row.get('function_name') or '')}` -> "
            f"`{_markdown_cell(row.get('qualified_name') or row.get('matched_function_name') or '')}`"
        )
    if not _list(payload.get("matched_traceback_frames")):
        lines.append("- none")
    lines.extend(["", "## Unmatched Traceback Frames", ""])
    for item in _list(payload.get("unmatched_traceback_frames")):
        row = _dict(item)
        lines.append(
            f"- `{_markdown_cell(row.get('path') or '')}`:"
            f"{_int(row.get('line', 0))} "
            f"`{_markdown_cell(row.get('function_name') or '')}`"
        )
    if not _list(payload.get("unmatched_traceback_frames")):
        lines.append("- none")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    return "\n".join(lines)


def write_repository_test_fault_localization_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_fault_localization.json"
    markdown_path = root / "repository_test_fault_localization.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_fault_localization_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_fault_localization_json": str(json_path),
        "repository_test_fault_localization_markdown": str(markdown_path),
    }


def _history_payload(
    history: GitChangeHistoryResult,
    ranked: list[Any],
) -> dict[str, Any]:
    payload = history.to_dict()
    payload.pop("scores", None)
    all_evidence = _dict(payload.pop("function_evidence", {}))
    payload["scored_function_count"] = len(history.scores)
    payload["ranked_function_evidence"] = {
        item.function_id: all_evidence[item.function_id]
        for item in ranked
        if item.function_id in all_evidence
    }
    return payload


def _match_test_function(
    program_graph: ProgramGraph,
    failing_test: dict[str, Any],
) -> dict[str, str]:
    path = _normalize_path(str(failing_test.get("path") or ""))
    raw_test_name = _normalize_pytest_test_name(
        str(failing_test.get("test_name") or "")
    )
    dotted_test_name = raw_test_name.replace("::", ".")
    leaf_name = raw_test_name.split("::")[-1] if raw_test_name else ""
    candidates = []
    for function in program_graph.functions.values():
        if not function.metadata.get("is_test"):
            continue
        function_path = _normalize_path(function.file_path)
        if path and not (function_path == path or function_path.endswith(f"/{path}")):
            continue
        qualified_name = str(function.metadata.get("qualified_name") or function.name)
        score = 0
        if dotted_test_name and qualified_name == dotted_test_name:
            score += 4
        if dotted_test_name and qualified_name.endswith(f".{dotted_test_name}"):
            score += 3
        if leaf_name and function.name == leaf_name:
            score += 2
        if path:
            score += 1
        if score:
            candidates.append(
                (
                    score,
                    {
                        "function_id": function.id,
                        "name": function.name,
                        "qualified_name": qualified_name,
                        "file_path": function.file_path,
                    },
                )
            )
    if not candidates:
        return {}
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _match_traceback_frame(
    program_graph: ProgramGraph,
    frame: dict[str, Any],
) -> dict[str, Any]:
    path = _normalize_path(str(frame.get("path") or ""))
    line = _int(frame.get("line", 0))
    function_name = str(frame.get("function_name") or "")
    candidates: list[tuple[int, dict[str, Any]]] = []
    for function in program_graph.functions.values():
        if function.metadata.get("is_test") or function.metadata.get("is_test_file"):
            continue
        function_path = _normalize_path(function.file_path)
        if path and not _paths_match(function_path, path):
            continue
        score = 0
        if line and function.start_line <= line <= function.end_line:
            score += 8
        if function_name and function.name == function_name:
            score += 4
        qualified_name = str(function.metadata.get("qualified_name") or function.name)
        if function_name and qualified_name.endswith(f".{function_name}"):
            score += 2
        if path:
            score += 1
        if score > 1:
            candidates.append(
                (
                    score,
                    {
                        "function_id": function.id,
                        "matched_function_name": function.name,
                        "qualified_name": qualified_name,
                        "file_path": function.file_path,
                        "start_line": function.start_line,
                        "end_line": function.end_line,
                    },
                )
            )
    if not candidates:
        return {}
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _paths_match(function_path: str, frame_path: str) -> bool:
    if function_path == frame_path:
        return True
    return frame_path.endswith(f"/{function_path}") or function_path.endswith(
        f"/{frame_path}"
    )


def _synthetic_traceback_test_id(frames: list[dict[str, Any]]) -> str:
    first = frames[0] if frames else {}
    path = str(first.get("file_path") or first.get("path") or "unknown")
    line = _int(first.get("line", 0))
    name = str(first.get("matched_function_name") or first.get("function_name") or "")
    return f"dynamic_traceback::{path}:{line}:{name}"


def _traceback_frame_message(frames: list[dict[str, Any]]) -> str:
    rows = []
    for frame in frames[:5]:
        path = str(frame.get("file_path") or frame.get("path") or "")
        line = _int(frame.get("line", 0))
        name = str(
            frame.get("qualified_name")
            or frame.get("matched_function_name")
            or frame.get("function_name")
            or ""
        )
        if path or name:
            rows.append(f"{path}:{line}:{name}")
    return "traceback frames: " + ", ".join(rows) if rows else ""


def _normalize_pytest_test_name(value: str) -> str:
    parts = []
    for part in _split_pytest_test_name_parts(str(value or "")):
        text = part.strip()
        if "[" in text and text.endswith("]"):
            text = text.split("[", 1)[0]
        if text:
            parts.append(text)
    return "::".join(parts)


def _split_pytest_test_name_parts(value: str) -> list[str]:
    text = str(value or "")
    parts: list[str] = []
    start = 0
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]" and depth > 0:
            depth -= 1
        if depth == 0 and text.startswith("::", index):
            parts.append(text[start:index])
            index += 2
            start = index
            continue
        index += 1
    parts.append(text[start:])
    return parts


def _direct_test_targets(program_graph: ProgramGraph, test_id: str) -> set[str]:
    return {
        str(edge["target"])
        for edge in program_graph.edges
        if edge["type"] == "tested_by" and edge["source"] == test_id
    }


def _parse_repository_scope(
    *,
    root: Path,
    parser: RepoParser,
    analysis_paths: list[str | Path] | None,
) -> tuple[RepoParseResult, dict[str, Any]]:
    requested = [
        str(path).strip()
        for path in list(analysis_paths or [])
        if str(path).strip()
    ]
    if not requested:
        return parser.parse(root), {
            "enabled": False,
            "requested_paths": [],
            "existing_files": [],
            "missing_paths": [],
            "parse_error_count": 0,
            "parse_error_paths": [],
        }

    existing_files: list[Path] = []
    missing_paths: list[str] = []
    seen: set[str] = set()
    for requested_path in requested:
        resolved = _resolve_analysis_file(root, requested_path)
        if resolved is None:
            missing_paths.append(requested_path)
            continue
        key = resolved.as_posix()
        if key in seen:
            continue
        seen.add(key)
        existing_files.append(resolved)

    files = []
    parse_error_paths: list[str] = []
    for file_path in existing_files:
        try:
            files.extend(parser.parse(file_path).files)
        except (OSError, SyntaxError, UnicodeDecodeError):
            parse_error_paths.append(_relative_posix(file_path, root))
    analysis_scope = {
        "enabled": True,
        "requested_paths": requested,
        "existing_files": [
            _relative_posix(path, root)
            for path in existing_files
            if _relative_posix(path, root) not in parse_error_paths
        ],
        "missing_paths": missing_paths,
        "parse_error_count": len(parse_error_paths),
        "parse_error_paths": parse_error_paths,
    }
    return RepoParseResult(root_path=str(root), files=files), analysis_scope


def _resolve_analysis_file(root: Path, requested_path: str) -> Path | None:
    path = Path(requested_path)
    candidate = path if path.is_absolute() else root / requested_path.replace("\\", "/")
    try:
        resolved = candidate.resolve()
        root_resolved = root.resolve()
    except (OSError, RuntimeError):
        return None
    if not (resolved == root_resolved or root_resolved in resolved.parents):
        return None
    if not resolved.exists() or not resolved.is_file() or resolved.suffix != ".py":
        return None
    return resolved


def _relative_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _skipped(
    *,
    reason: str,
    message: str,
    dynamic_evidence: dict[str, Any] | None = None,
    repository_root: str = "",
) -> dict[str, Any]:
    evidence = _dict(dynamic_evidence)
    return {
        "status": "skipped",
        "reason": reason,
        "message": message,
        "repository_root": repository_root,
        "top_k": 0,
        "dynamic_evidence_level": str(evidence.get("evidence_level") or ""),
        "recommended_validation_command": str(
            evidence.get("recommended_validation_command") or ""
        ),
        "parsed_function_count": 0,
        "parsed_test_count": 0,
        "static_finding_count": 0,
        "failed_test_count": _int(evidence.get("failing_test_count", 0)),
        "matched_failed_test_count": 0,
        "unmatched_failed_test_count": _int(evidence.get("failing_test_count", 0)),
        "coverage_inferred_from_test_graph_count": 0,
        "matched_failing_tests": [],
        "unmatched_failing_tests": list(_list(evidence.get("failing_tests"))),
        "traceback_frame_count": _int(evidence.get("traceback_frame_count", 0)),
        "matched_traceback_frame_count": 0,
        "unmatched_traceback_frame_count": _int(
            evidence.get("traceback_frame_count", 0)
        ),
        "matched_traceback_frames": [],
        "unmatched_traceback_frames": list(_list(evidence.get("traceback_frames"))),
        "dynamic_test_ids": [],
        "dynamic_evidence_nodeids": {},
        "ranking_count": 0,
        "top_function": "",
        "top_function_id": "",
        "top_score": 0.0,
        "public_api_evidence": _dict(evidence.get("public_api_evidence")),
        "overlay_case_context": _dict(evidence.get("overlay_case_context")),
        "rankings": [],
        "next_actions": _skipped_next_actions(reason),
    }


def _next_actions(
    *,
    matched_failed_test_count: int,
    matched_traceback_frame_count: int,
    ranking_count: int,
    command: str,
) -> list[str]:
    actions = []
    if matched_failed_test_count == 0 and matched_traceback_frame_count == 0:
        actions.append(
            "Check whether failing pytest nodeids or traceback frames match parsed repository source paths."
        )
    if ranking_count:
        actions.append(
            "Review top-ranked functions and inspect dynamic_test_evidence, graph, static, and semantic signals."
        )
    if command:
        actions.append(f"Use the validation command after patch generation: {command}")
    return actions


def _skipped_next_actions(reason: str) -> list[str]:
    if reason == "dynamic_evidence_not_usable":
        return ["Run repository tests until at least one assertion-failing test is available."]
    if reason == "repository_root_missing":
        return ["Provide --repository-test-root or enable --checkout-repository-tests."]
    if reason == "dynamic_evidence_missing":
        return ["Run repository test execution before localization."]
    return []


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").strip().lstrip("./")


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
    return str(value).replace("\n", " ").replace("|", "\\|")
