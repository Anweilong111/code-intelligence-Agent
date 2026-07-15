from __future__ import annotations

import ast
import difflib
import hashlib
import json
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


@dataclass(frozen=True)
class FunctionSpan:
    path: str
    qualified_name: str
    start_line: int
    end_line: int
    definition_line: int

    @property
    def key(self) -> str:
        return f"{self.path}::{self.qualified_name}"


def resolve_v3_localization_ground_truth(
    *,
    case_id: str,
    bug_repository: str | Path,
    fix_repository: str | Path,
    source_files: Iterable[str],
    ranking_snapshot_sha256: str,
) -> dict[str, Any]:
    """Resolve exact changed functions without exposing the fix to ranking.

    ``ranking_snapshot_sha256`` is mandatory evidence that candidate selection and
    raw signal extraction were frozen before this oracle-only operation started.
    """
    if not ranking_snapshot_sha256:
        raise ValueError("ranking_snapshot_sha256 is required before oracle resolution")
    bug_root = Path(bug_repository).resolve()
    fix_root = Path(fix_repository).resolve()
    normalized_files = sorted(
        {
            normalized
            for value in source_files
            if (normalized := _normalize_relative_path(value))
        }
    )
    file_rows: list[dict[str, Any]] = []
    exact_keys: set[str] = set()
    projected_keys: set[str] = set()
    unmapped_files: list[dict[str, Any]] = []

    for relative_path in normalized_files:
        row = _resolve_file_ground_truth(
            bug_root=bug_root,
            fix_root=fix_root,
            relative_path=relative_path,
        )
        file_rows.append(row)
        exact_keys.update(str(value) for value in row["bug_function_keys"])
        projected_keys.update(str(value) for value in row["projected_fix_function_keys"])
        if row["unmapped_changed_line_count"] or row["status"] != "resolved":
            unmapped_files.append(
                {
                    "path": relative_path,
                    "status": row["status"],
                    "reason": row["reason"],
                    "unmapped_changed_line_count": row[
                        "unmapped_changed_line_count"
                    ],
                }
            )

    function_keys = sorted(exact_keys | projected_keys)
    rankable = bool(function_keys)
    resolved_count = sum(row["status"] == "resolved" for row in file_rows)
    payload: dict[str, Any] = {
        "schema_version": "v3_localization_ground_truth_v1",
        "case_id": str(case_id),
        "status": (
            "resolved"
            if normalized_files and resolved_count == len(normalized_files)
            else "partial"
            if resolved_count
            else "unresolved"
        ),
        "resolution_method": "diff_lines_to_innermost_ast_function_v1",
        "resolved_after_ranking_frozen": True,
        "ground_truth_used_for_ranking": False,
        "ranking_snapshot_sha256": ranking_snapshot_sha256,
        "source_files": normalized_files,
        "file_keys": normalized_files,
        "function_keys": function_keys,
        "bug_function_keys": sorted(exact_keys),
        "projected_fix_function_keys": sorted(projected_keys),
        "function_rankable": rankable,
        "function_rankability_reason": (
            "at_least_one_changed_function_exists_in_bug_revision"
            if rankable
            else "no_changed_function_exists_in_bug_revision"
        ),
        "function_ground_truth_count": len(function_keys),
        "file_ground_truth_count": len(normalized_files),
        "unmapped_files": unmapped_files,
        "files": file_rows,
    }
    payload["ground_truth_sha256"] = _sha256_json(payload)
    return payload


def _resolve_file_ground_truth(
    *,
    bug_root: Path,
    fix_root: Path,
    relative_path: str,
) -> dict[str, Any]:
    bug_path = _safe_file(bug_root, relative_path)
    fix_path = _safe_file(fix_root, relative_path)
    bug_exists = bool(bug_path and bug_path.is_file())
    fix_exists = bool(fix_path and fix_path.is_file())
    base = {
        "path": relative_path,
        "bug_exists": bug_exists,
        "fix_exists": fix_exists,
        "bug_changed_lines": [],
        "fix_changed_lines": [],
        "bug_function_keys": [],
        "fix_function_keys": [],
        "projected_fix_function_keys": [],
        "unmapped_bug_changed_lines": [],
        "unmapped_fix_changed_lines": [],
        "unmapped_changed_line_count": 0,
    }
    if not relative_path.endswith(".py"):
        return {**base, "status": "unresolved", "reason": "non_python_source_file"}
    if not bug_exists and not fix_exists:
        return {
            **base,
            "status": "unresolved",
            "reason": "source_file_missing_in_both_revisions",
        }

    bug_source = _read_source(bug_path) if bug_exists else ""
    fix_source = _read_source(fix_path) if fix_exists else ""
    base["bug_source_sha256"] = _sha256_text(bug_source) if bug_exists else ""
    base["fix_source_sha256"] = _sha256_text(fix_source) if fix_exists else ""
    bug_lines, fix_lines = _changed_lines(bug_source, fix_source)
    bug_spans, bug_parse_error = _function_spans(relative_path, bug_source)
    fix_spans, fix_parse_error = _function_spans(relative_path, fix_source)
    if bug_parse_error or fix_parse_error:
        return {
            **base,
            "status": "unresolved",
            "reason": "python_ast_parse_failed",
            "bug_parse_error": bug_parse_error,
            "fix_parse_error": fix_parse_error,
            "bug_changed_lines": sorted(bug_lines),
            "fix_changed_lines": sorted(fix_lines),
            "unmapped_changed_line_count": len(bug_lines | fix_lines),
        }

    bug_matches, unmapped_bug = _map_lines_to_spans(bug_lines, bug_spans)
    fix_matches, unmapped_fix = _map_lines_to_spans(fix_lines, fix_spans)
    bug_by_qualified_name = {span.qualified_name: span for span in bug_spans}
    projected = {
        bug_by_qualified_name[span.qualified_name].key
        for span in fix_matches
        if span.qualified_name in bug_by_qualified_name
    }
    mapped_bug_lines = _mapped_line_count(bug_lines, bug_spans)
    mapped_fix_lines = _mapped_line_count(fix_lines, fix_spans)
    return {
        **base,
        "status": "resolved",
        "reason": "diff_lines_mapped_to_ast_functions",
        "bug_changed_lines": sorted(bug_lines),
        "fix_changed_lines": sorted(fix_lines),
        "bug_function_keys": sorted(span.key for span in bug_matches),
        "fix_function_keys": sorted(span.key for span in fix_matches),
        "projected_fix_function_keys": sorted(projected),
        "bug_function_spans": [asdict(span) for span in sorted(bug_matches, key=_span_key)],
        "fix_function_spans": [asdict(span) for span in sorted(fix_matches, key=_span_key)],
        "mapped_bug_changed_line_count": mapped_bug_lines,
        "mapped_fix_changed_line_count": mapped_fix_lines,
        "unmapped_bug_changed_lines": sorted(unmapped_bug),
        "unmapped_fix_changed_lines": sorted(unmapped_fix),
        "unmapped_changed_line_count": len(unmapped_bug) + len(unmapped_fix),
    }


def _function_spans(path: str, source: str) -> tuple[list[FunctionSpan], str]:
    if not source:
        return [], ""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        return [], f"{type(exc).__name__}:{exc}"

    spans: list[FunctionSpan] = []

    def visit(node: ast.AST, parents: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, (*parents, child.name))
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorators = [
                    int(decorator.lineno)
                    for decorator in child.decorator_list
                    if getattr(decorator, "lineno", None)
                ]
                spans.append(
                    FunctionSpan(
                        path=path,
                        qualified_name=".".join((*parents, child.name)),
                        start_line=min([int(child.lineno), *decorators]),
                        end_line=int(getattr(child, "end_lineno", child.lineno)),
                        definition_line=int(child.lineno),
                    )
                )
                visit(child, (*parents, child.name))
                continue
            visit(child, parents)

    visit(tree, ())
    return spans, ""


def _changed_lines(bug_source: str, fix_source: str) -> tuple[set[int], set[int]]:
    bug_lines = bug_source.splitlines()
    fix_lines = fix_source.splitlines()
    matcher = difflib.SequenceMatcher(a=bug_lines, b=fix_lines, autojunk=False)
    changed_bug: set[int] = set()
    changed_fix: set[int] = set()
    for tag, bug_start, bug_end, fix_start, fix_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        changed_bug.update(range(bug_start + 1, bug_end + 1))
        changed_fix.update(range(fix_start + 1, fix_end + 1))
    return changed_bug, changed_fix


def _map_lines_to_spans(
    lines: set[int],
    spans: list[FunctionSpan],
) -> tuple[set[FunctionSpan], set[int]]:
    matches: set[FunctionSpan] = set()
    unmapped: set[int] = set()
    for line in lines:
        candidates = [span for span in spans if span.start_line <= line <= span.end_line]
        if not candidates:
            unmapped.add(line)
            continue
        matches.add(min(candidates, key=_innermost_span_key))
    return matches, unmapped


def _mapped_line_count(lines: set[int], spans: list[FunctionSpan]) -> int:
    return sum(
        1 for line in lines if any(span.start_line <= line <= span.end_line for span in spans)
    )


def _innermost_span_key(span: FunctionSpan) -> tuple[int, int, int]:
    return (
        span.end_line - span.start_line,
        -span.qualified_name.count("."),
        -span.start_line,
    )


def _span_key(span: FunctionSpan) -> tuple[str, int, int]:
    return span.qualified_name, span.start_line, span.end_line


def _safe_file(root: Path, relative_path: str) -> Path | None:
    candidate = (root / Path(relative_path)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _read_source(path: Path | None) -> str:
    if path is None:
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _normalize_relative_path(value: str) -> str:
    normalized = PurePosixPath(str(value).replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        return ""
    text = normalized.as_posix()
    return text[2:] if text.startswith("./") else text


def _sha256_json(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(serialized)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
