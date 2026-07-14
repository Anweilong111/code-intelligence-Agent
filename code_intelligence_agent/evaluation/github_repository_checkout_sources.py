from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any

from code_intelligence_agent.core.repo_parser import (
    DEFAULT_EXCLUDED_DIRS as PARSER_EXCLUDED_DIRS,
    is_default_excluded_repo_path,
)


DEFAULT_EXCLUDED_DIRS = set(PARSER_EXCLUDED_DIRS)

DEFAULT_HASH_EXTENSIONS = {
    ".cfg",
    ".ini",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def build_repository_checkout_discovery(
    checkout_path: str | Path,
    *,
    owner: str | None = None,
    repo: str | None = None,
    ref: str | None = None,
    max_files: int = 5000,
    max_hash_bytes: int = 2_000_000,
    excluded_dirs: set[str] | None = None,
) -> dict[str, Any]:
    root = Path(checkout_path).resolve()
    using_default_exclusions = excluded_dirs is None
    ignored_dirs = set(DEFAULT_EXCLUDED_DIRS if excluded_dirs is None else excluded_dirs)
    files: list[dict[str, Any]] = []
    scanned_file_count = 0
    skipped_dir_count = 0
    truncated = False

    if max_files <= 0:
        return _payload(
            root=root,
            owner=owner,
            repo=repo,
            ref=ref,
            files=[],
            scanned_file_count=0,
            skipped_dir_count=0,
            truncated=True,
            reason="invalid_max_files",
        )
    if not root.exists() or not root.is_dir():
        return _payload(
            root=root,
            owner=owner,
            repo=repo,
            ref=ref,
            files=[],
            scanned_file_count=0,
            skipped_dir_count=0,
            truncated=False,
            reason="checkout_path_missing",
        )

    for current_root, dir_names, file_names in os.walk(root):
        original_dir_names = sorted(dir_names)
        current = Path(current_root)
        try:
            current_parts = current.resolve().relative_to(root).parts
        except ValueError:
            current_parts = ()
        dir_names[:] = [
            name
            for name in original_dir_names
            if not _is_hidden_runtime_dir(name)
            and not (
                is_default_excluded_repo_path((*current_parts, name))
                if using_default_exclusions
                else name in ignored_dirs
            )
        ]
        skipped_dir_count += len(original_dir_names) - len(dir_names)
        for file_name in sorted(file_names):
            path = current / file_name
            if path.is_symlink() or not path.is_file():
                continue
            relative = _relative_posix_path(root, path)
            if not relative:
                continue
            scanned_file_count += 1
            if len(files) >= max_files:
                truncated = True
                continue
            entry = {
                "path": relative,
                "type": "blob",
                "raw_url": str(path.resolve()),
            }
            if owner:
                entry["owner"] = owner
            if repo:
                entry["repo"] = repo
            if ref:
                entry["ref"] = ref
            digest = _optional_sha256(path, max_hash_bytes=max_hash_bytes)
            if digest:
                entry["sha256"] = digest
            files.append(entry)

    return _payload(
        root=root,
        owner=owner,
        repo=repo,
        ref=ref,
        files=files,
        scanned_file_count=scanned_file_count,
        skipped_dir_count=skipped_dir_count,
        truncated=truncated,
        reason="ok",
    )


def render_repository_checkout_discovery_markdown(payload: dict[str, Any]) -> str:
    metadata = _dict(payload.get("discovery"))
    files = _list(payload.get("files"))
    lines = [
        "# Repository Checkout Source Discovery",
        "",
        f"- Checkout Path: `{_markdown_cell(metadata.get('checkout_path', ''))}`",
        f"- Owner/Repo: `{_markdown_cell(payload.get('owner', ''))}/{_markdown_cell(payload.get('repo', ''))}`",
        f"- Ref: `{_markdown_cell(payload.get('ref', ''))}`",
        f"- Reason: `{_markdown_cell(metadata.get('reason', ''))}`",
        f"- Scanned Files: {_int(metadata.get('scanned_file_count', 0))}",
        f"- Included Files: {len(files)}",
        f"- Skipped Directories: {_int(metadata.get('skipped_dir_count', 0))}",
        f"- Truncated: {str(bool(metadata.get('truncated', False))).lower()}",
        "",
        "## File Preview",
        "",
        "| Path | SHA256 |",
        "| --- | --- |",
    ]
    for entry in files[:30]:
        lines.append(
            "| "
            f"{_markdown_cell(_dict(entry).get('path', ''))} | "
            f"{_markdown_cell(_dict(entry).get('sha256', ''))} |"
        )
    if not files:
        lines.append("| none |  |")
    if len(files) > 30:
        lines.append(f"| ... {len(files) - 30} more files |  |")
    return "\n".join(lines)


def write_repository_checkout_discovery_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_checkout_sources.json"
    markdown_path = root / "repository_checkout_sources.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_checkout_discovery_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_checkout_sources_json": str(json_path),
        "repository_checkout_sources_markdown": str(markdown_path),
    }


def _payload(
    *,
    root: Path,
    owner: str | None,
    repo: str | None,
    ref: str | None,
    files: list[dict[str, Any]],
    scanned_file_count: int,
    skipped_dir_count: int,
    truncated: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "discovery": {
            "mode": "repository_checkout",
            "checkout_path": str(root),
            "reason": reason,
            "scanned_file_count": scanned_file_count,
            "included_file_count": len(files),
            "skipped_dir_count": skipped_dir_count,
            "truncated": truncated,
        },
        "owner": owner or "",
        "repo": repo or "",
        "ref": ref or "",
        "files": files,
    }


def _relative_posix_path(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root)
    except ValueError:
        return ""
    parts = PurePosixPath(*relative.parts).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return ""
    return "/".join(parts)


def _optional_sha256(path: Path, *, max_hash_bytes: int) -> str:
    if path.suffix.lower() not in DEFAULT_HASH_EXTENSIONS and path.name not in {
        "Pipfile",
        "setup.py",
    }:
        return ""
    try:
        if path.stat().st_size > max_hash_bytes:
            return ""
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _is_hidden_runtime_dir(name: str) -> bool:
    return name.startswith(".") and name not in {".github"}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")
