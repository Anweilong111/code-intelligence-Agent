from __future__ import annotations

import difflib
from pathlib import Path

from code_intelligence_agent.core.models import PatchCandidate


def render_unified_diff(
    old_source: str,
    new_source: str,
    relative_file_path: str,
) -> str:
    return "".join(
        difflib.unified_diff(
            old_source.splitlines(keepends=True),
            new_source.splitlines(keepends=True),
            fromfile=f"a/{relative_file_path}",
            tofile=f"b/{relative_file_path}",
        )
    )


def apply_patch_candidate(repo_path: str | Path, candidate: PatchCandidate) -> Path:
    target = Path(repo_path) / candidate.relative_file_path
    if not target.exists():
        raise FileNotFoundError(f"Patch target does not exist: {target}")
    source = target.read_text(encoding="utf-8")
    if candidate.old_source not in source:
        raise ValueError(
            f"Original source block not found in target: {candidate.relative_file_path}"
        )
    target.write_text(
        source.replace(candidate.old_source, candidate.new_source, 1),
        encoding="utf-8",
    )
    return target


def apply_patch_candidates(
    repo_path: str | Path,
    candidates: list[PatchCandidate],
) -> list[Path]:
    touched: list[Path] = []
    for candidate in candidates:
        touched.append(apply_patch_candidate(repo_path, candidate))
    return touched
