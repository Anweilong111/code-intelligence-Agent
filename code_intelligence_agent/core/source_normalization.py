from __future__ import annotations

import textwrap


def normalize_function_source(source: str) -> str:
    """Normalize a function or method source slice for standalone AST parsing."""

    dedented = textwrap.dedent(source)
    if _first_nonblank_indent(dedented) == 0:
        return dedented
    prefix = _first_nonblank_prefix(source)
    if not prefix:
        return dedented
    return _strip_line_prefix(source, prefix)


def _first_nonblank_indent(source: str) -> int:
    for line in source.splitlines():
        if not line.strip():
            continue
        return len(line) - len(line.lstrip(" \t"))
    return 0


def _first_nonblank_prefix(source: str) -> str:
    for line in source.splitlines():
        if not line.strip():
            continue
        stripped = line.lstrip(" \t")
        return line[: len(line) - len(stripped)]
    return ""


def _strip_line_prefix(source: str, prefix: str) -> str:
    lines = source.splitlines(keepends=True)
    normalized = []
    for line in lines:
        content = line[:-1] if line.endswith("\n") else line
        newline = "\n" if line.endswith("\n") else ""
        if content.startswith(prefix):
            normalized.append(content[len(prefix) :] + newline)
        else:
            normalized.append(line)
    return "".join(normalized)
