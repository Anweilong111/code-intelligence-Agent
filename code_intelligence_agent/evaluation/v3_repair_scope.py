from __future__ import annotations

import ast
import re
import warnings
from pathlib import Path, PurePosixPath
from typing import Any

from code_intelligence_agent.core.repo_parser import is_default_excluded_repo_path


DEFAULT_FULL_REPOSITORY_FILE_THRESHOLD = 300
DEFAULT_SCOPED_FILE_LIMIT = 40
DEFAULT_REVERSE_IMPORT_EXPANSION_LIMIT = 8
DEFAULT_LEXICAL_EXPANSION_LIMIT = 12


def select_v3_analysis_scope(
    repository_root: str | Path,
    *,
    case: dict[str, Any],
    dynamic_evidence: dict[str, Any],
    full_repository_file_threshold: int = DEFAULT_FULL_REPOSITORY_FILE_THRESHOLD,
    scoped_file_limit: int = DEFAULT_SCOPED_FILE_LIMIT,
    reverse_import_expansion_limit: int = DEFAULT_REVERSE_IMPORT_EXPANSION_LIMIT,
    lexical_expansion_limit: int = DEFAULT_LEXICAL_EXPANSION_LIMIT,
    import_depth: int = 3,
) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    python_files = _python_files(root)
    if len(python_files) <= max(0, full_repository_file_threshold):
        return {
            "status": "pass",
            "mode": "full_repository",
            "reason": "repository_below_python_file_threshold",
            "python_file_count": len(python_files),
            "full_repository_file_threshold": full_repository_file_threshold,
            "scoped_file_limit": scoped_file_limit,
            "reverse_import_expansion_limit": reverse_import_expansion_limit,
            "lexical_expansion_limit": lexical_expansion_limit,
            "analysis_paths": None,
            "selected_file_count": len(python_files),
            "seed_paths": [],
            "import_expansion_paths": [],
            "reverse_import_expansion_paths": [],
            "lexical_expansion_paths": [],
            "ground_truth_used": False,
        }

    known_paths = {path.relative_to(root).as_posix(): path for path in python_files}
    selected: list[str] = []
    seed_paths: list[str] = []
    import_paths: list[str] = []
    reverse_import_paths: list[str] = []
    lexical_paths: list[str] = []

    def add(relative_path: str, source: str) -> None:
        normalized = _normalized_path(relative_path)
        if normalized not in known_paths or normalized in selected:
            return
        if len(selected) >= max(1, scoped_file_limit):
            return
        selected.append(normalized)
        if source == "seed":
            seed_paths.append(normalized)
        elif source == "import":
            import_paths.append(normalized)
        elif source == "reverse_import":
            reverse_import_paths.append(normalized)
        elif source == "lexical":
            lexical_paths.append(normalized)

    for value in _list(case.get("test_overlay_paths")):
        add(str(value), "seed")
    for group_name in ("failing_tests", "traceback_frames"):
        for value in _list(dynamic_evidence.get(group_name)):
            row = _dict(value)
            relative = _relative_repository_path(root, str(row.get("path") or ""))
            if relative:
                add(relative, "seed")

    frontier = list(seed_paths)
    visited_import_sources: set[str] = set()
    for _ in range(max(0, import_depth)):
        next_frontier: list[str] = []
        for relative in frontier:
            if relative in visited_import_sources:
                continue
            visited_import_sources.add(relative)
            path = known_paths.get(relative)
            if path is None:
                continue
            for imported in _local_import_paths(root, path, known_paths):
                before = len(selected)
                add(imported, "import")
                if len(selected) > before:
                    next_frontier.append(imported)
        frontier = next_frontier
        if not frontier or len(selected) >= scoped_file_limit:
            break

    reverse_candidates = _reverse_import_candidates(
        root,
        known_paths=known_paths,
        selected_paths=selected,
    )
    for relative in reverse_candidates:
        if len(reverse_import_paths) >= max(0, reverse_import_expansion_limit):
            break
        add(relative, "reverse_import")
        if len(selected) >= scoped_file_limit:
            break

    query_tokens = _scope_query_tokens(case, dynamic_evidence)
    lexical_candidates = []
    for relative, path in known_paths.items():
        if relative in selected or _looks_like_test_path(relative):
            continue
        score = _path_token_score(relative, query_tokens)
        if score <= 0:
            continue
        lexical_candidates.append((score, len(relative), relative, path))
    lexical_candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    for _, _, relative, _ in lexical_candidates:
        if len(lexical_paths) >= max(0, lexical_expansion_limit):
            break
        add(relative, "lexical")
        if len(selected) >= scoped_file_limit:
            break

    if not selected:
        for relative in sorted(known_paths)[: max(1, scoped_file_limit)]:
            add(relative, "lexical")
    return {
        "status": "pass" if selected else "fail",
        "mode": "bounded_dynamic_import_scope",
        "reason": (
            "large_repository_scoped_from_failure_and_import_evidence"
            if selected
            else "large_repository_scope_empty"
        ),
        "python_file_count": len(python_files),
        "full_repository_file_threshold": full_repository_file_threshold,
        "scoped_file_limit": scoped_file_limit,
        "reverse_import_expansion_limit": reverse_import_expansion_limit,
        "lexical_expansion_limit": lexical_expansion_limit,
        "analysis_paths": selected,
        "selected_file_count": len(selected),
        "seed_paths": seed_paths,
        "import_expansion_paths": import_paths,
        "reverse_import_expansion_paths": reverse_import_paths,
        "lexical_expansion_paths": lexical_paths,
        "query_tokens": sorted(query_tokens),
        "import_depth": max(0, import_depth),
        "ground_truth_used": False,
        "scope_risk": (
            "Functions outside the bounded scope are not ranked in this preparation pass."
        ),
    }


def _python_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*.py"):
        try:
            relative_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if (
            path.is_file()
            and not path.is_symlink()
            and not is_default_excluded_repo_path(relative_parts)
        ):
            files.append(path)
    return sorted(files)


def _local_import_paths(
    root: Path,
    source_path: Path,
    known_paths: dict[str, Path],
) -> list[str]:
    try:
        source = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source, filename=str(source_path))
    except SyntaxError:
        return []
    relative = source_path.relative_to(root)
    current_module_parts = list(relative.with_suffix("").parts)
    if current_module_parts and current_module_parts[-1] == "__init__":
        current_module_parts.pop()
    else:
        current_module_parts = current_module_parts[:-1]
    imported_paths: list[str] = []
    for node in ast.walk(tree):
        module_names: list[str] = []
        if isinstance(node, ast.Import):
            module_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base_parts = list(current_module_parts)
            if node.level:
                trim = max(0, node.level - 1)
                base_parts = base_parts[: max(0, len(base_parts) - trim)]
            else:
                base_parts = []
            module_parts = [part for part in str(node.module or "").split(".") if part]
            base_module = ".".join([*base_parts, *module_parts])
            if base_module:
                module_names.append(base_module)
            for alias in node.names:
                if alias.name == "*":
                    continue
                child = ".".join(
                    [part for part in (base_module, alias.name) if part]
                )
                if child:
                    module_names.append(child)
        for module_name in module_names:
            for candidate in _module_path_candidates(module_name):
                if candidate in known_paths and candidate not in imported_paths:
                    imported_paths.append(candidate)
    return imported_paths


def _module_path_candidates(module_name: str) -> list[str]:
    parts = [part for part in module_name.split(".") if part]
    if not parts:
        return []
    path = PurePosixPath(*parts)
    return [f"{path.as_posix()}.py", (path / "__init__.py").as_posix()]


def _reverse_import_candidates(
    root: Path,
    *,
    known_paths: dict[str, Path],
    selected_paths: list[str],
) -> list[str]:
    selected_production = {
        path
        for path in selected_paths
        if not _looks_like_auxiliary_path(path)
    }
    if not selected_production:
        return []
    module_hints = {
        part
        for path in selected_production
        for part in _module_hints(path)
    }
    candidates: list[tuple[int, int, int, str]] = []
    for relative, path in known_paths.items():
        if relative in selected_paths or _looks_like_auxiliary_path(relative):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if module_hints and not any(hint in source for hint in module_hints):
            continue
        imported = set(_local_import_paths(root, path, known_paths))
        match_count = len(imported.intersection(selected_production))
        if match_count:
            candidates.append(
                (-match_count, len(PurePosixPath(relative).parts), len(relative), relative)
            )
    candidates.sort()
    return [relative for _, _, _, relative in candidates]


def _module_hints(relative_path: str) -> set[str]:
    path = PurePosixPath(relative_path)
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    if not parts:
        return set()
    dotted = ".".join(parts)
    return {dotted, parts[-1]}


def _scope_query_tokens(
    case: dict[str, Any],
    dynamic_evidence: dict[str, Any],
) -> set[str]:
    values = [
        str(dynamic_evidence.get("failure_signal") or ""),
        str(dynamic_evidence.get("diagnostic_summary") or ""),
    ]
    for group_name in ("failing_tests", "traceback_frames"):
        for value in _list(dynamic_evidence.get(group_name)):
            row = _dict(value)
            values.extend(
                str(row.get(key) or "")
                for key in ("nodeid", "test_name", "function_name", "source_line")
            )
    for command in _list(case.get("targeted_test_commands")):
        values.extend(str(part) for part in _list(command))
    tokens: set[str] = set()
    for value in values:
        expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expanded):
            normalized = token.lower()
            tokens.add(normalized)
            tokens.update(part for part in normalized.split("_") if len(part) > 2)
    return {
        token
        for token in tokens
        if len(token) > 2
        and token
        not in {
            "test",
            "tests",
            "python",
            "pytest",
            "unittest",
            "fail",
            "failed",
            "error",
            "assert",
            "assertion",
            "repository",
        }
    }


def _path_token_score(relative_path: str, query_tokens: set[str]) -> int:
    normalized = relative_path.lower().replace("-", "_")
    path_tokens = set(
        token
        for token in re.findall(r"[a-z0-9_]+", normalized)
        for token in [token, *token.split("_")]
        if len(token) > 2
    )
    overlap = query_tokens.intersection(path_tokens)
    score = len(overlap) * 3
    for token in query_tokens:
        if len(token) >= 6 and token in normalized:
            score += 1
    return score


def _relative_repository_path(root: Path, value: str) -> str:
    if not value:
        return ""
    normalized = value.replace("\\", "/")
    root_text = root.as_posix()
    if normalized.lower().startswith(root_text.lower() + "/"):
        return normalized[len(root_text) + 1 :]
    path = Path(value)
    try:
        return path.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        pure = PurePosixPath(normalized)
        return pure.as_posix() if not pure.is_absolute() else ""


def _normalized_path(value: str) -> str:
    pure = PurePosixPath(str(value or "").replace("\\", "/"))
    if not value or pure.is_absolute() or ".." in pure.parts:
        return ""
    return pure.as_posix()


def _looks_like_test_path(value: str) -> bool:
    path = PurePosixPath(value)
    parts = {part.lower() for part in path.parts[:-1]}
    name = path.name.lower()
    return bool(
        {"test", "tests"}.intersection(parts)
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _looks_like_auxiliary_path(value: str) -> bool:
    path = PurePosixPath(value)
    parts = {part.lower() for part in path.parts[:-1]}
    return bool(
        _looks_like_test_path(value)
        or parts.intersection(
            {
                "bench",
                "benchmark",
                "benchmarks",
                "demo",
                "demos",
                "doc",
                "docs",
                "docs_src",
                "example",
                "examples",
            }
        )
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
