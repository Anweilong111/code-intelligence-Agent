from __future__ import annotations

import argparse
import fnmatch
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass(frozen=True)
class GitHubDiscoveryItem:
    source_path: str | None
    owner: str | None
    repo: str | None
    ref: str | None
    raw_url: str | None = None
    target_path: str | None = None
    sha256: str | None = None
    license: str | None = None
    is_blob: bool = True
    origin: str = "unknown"


@dataclass(frozen=True)
class GitHubSourceImportRow:
    source_path: str
    status: str
    reason: str
    source: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GitHubSourceImportReport:
    source_path: str
    input_count: int
    source_count: int
    skipped_count: int
    rows: list[GitHubSourceImportRow]
    source_entries: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "input_count": self.input_count,
            "source_count": self.source_count,
            "skipped_count": self.skipped_count,
            "rows": [row.to_dict() for row in self.rows],
            "source_entries": self.source_entries,
            "sources_payload": {"sources": self.source_entries},
        }


def import_github_sources(
    payload: dict[str, Any],
    source_path: str = "",
    owner: str | None = None,
    repo: str | None = None,
    ref: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    preserve_paths: bool = False,
    preserve_raw_upstream: bool = False,
    target_prefix: str = "",
) -> GitHubSourceImportReport:
    include_patterns = include or ["*.py"]
    exclude_patterns = exclude or []
    items = list(
        _iter_discovery_items(
            payload,
            default_owner=owner,
            default_repo=repo,
            default_ref=ref,
        )
    )
    source_items: list[GitHubDiscoveryItem] = []
    skipped_rows: list[GitHubSourceImportRow] = []

    for item in items:
        clean_path = _clean_relative_path(item.source_path or "")
        if not clean_path:
            skipped_rows.append(_skipped(item, "missing_or_unsafe_source_path"))
            continue
        if not item.is_blob:
            skipped_rows.append(_skipped(item, "not_blob"))
            continue
        if not _matches_any(clean_path, include_patterns):
            skipped_rows.append(_skipped(item, "not_included"))
            continue
        if _matches_any(clean_path, exclude_patterns):
            skipped_rows.append(_skipped(item, "excluded"))
            continue
        if not item.raw_url and not (item.owner and item.repo and item.ref):
            skipped_rows.append(_skipped(item, "missing_owner_repo_ref"))
            continue
        source_items.append(
            GitHubDiscoveryItem(
                source_path=clean_path,
                owner=item.owner,
                repo=item.repo,
                ref=item.ref,
                raw_url=item.raw_url,
                target_path=item.target_path,
                sha256=item.sha256,
                license=item.license,
                is_blob=True,
                origin=item.origin,
            )
        )

    source_items = sorted(
        source_items,
        key=lambda item: (
            item.owner or "",
            item.repo or "",
            item.ref or "",
            item.source_path or "",
            item.raw_url or "",
        ),
    )
    source_entries = _build_source_entries(
        source_items,
        preserve_paths=preserve_paths,
        preserve_raw_upstream=preserve_raw_upstream,
        target_prefix=target_prefix,
    )
    imported_rows = [
        GitHubSourceImportRow(
            source_path=entry.get("source_path") or entry.get("raw_url", ""),
            status="imported",
            reason="",
            source=entry,
        )
        for entry in source_entries
    ]
    rows = imported_rows + skipped_rows
    return GitHubSourceImportReport(
        source_path=source_path,
        input_count=len(items),
        source_count=len(source_entries),
        skipped_count=len(skipped_rows),
        rows=rows,
        source_entries=source_entries,
    )


def render_github_source_import_markdown(report: GitHubSourceImportReport) -> str:
    lines = [
        "# GitHub Source Import",
        "",
        f"- Source: `{report.source_path or '<memory>'}`",
        f"- Input Items: {report.input_count}",
        f"- Imported Sources: {report.source_count}",
        f"- Skipped Items: {report.skipped_count}",
        "",
        "| Target | Status | Upstream | Ref | Source Path | Reason |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in report.rows:
        source = row.source or {}
        upstream = _format_upstream(source)
        lines.append(
            "| "
            f"{_markdown_cell(source.get('target_path', ''))} | "
            f"{_markdown_cell(row.status)} | "
            f"{_markdown_cell(upstream)} | "
            f"{_markdown_cell(source.get('ref', ''))} | "
            f"{_markdown_cell(row.source_path)} | "
            f"{_markdown_cell(row.reason)} |"
        )
    return "\n".join(lines)


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _iter_discovery_items(
    payload: dict[str, Any],
    default_owner: str | None = None,
    default_repo: str | None = None,
    default_ref: str | None = None,
) -> list[GitHubDiscoveryItem]:
    items: list[GitHubDiscoveryItem] = []
    payload_owner, payload_repo = _owner_repo_from_payload(payload)
    top_owner = payload_owner or default_owner
    top_repo = payload_repo or default_repo
    top_ref = payload.get("ref") or default_ref
    top_license = _string_or_none(payload.get("license"))

    if isinstance(payload.get("tree"), list):
        for node in payload["tree"]:
            if not isinstance(node, dict):
                items.append(
                    GitHubDiscoveryItem(
                        source_path=None,
                        owner=top_owner,
                        repo=top_repo,
                        ref=top_ref,
                        is_blob=False,
                        origin="tree",
                    )
                )
                continue
            items.append(
                GitHubDiscoveryItem(
                    source_path=_string_or_none(node.get("path")),
                    owner=_string_or_none(node.get("owner")) or top_owner,
                    repo=_string_or_none(node.get("repo")) or top_repo,
                    ref=_string_or_none(node.get("ref")) or top_ref,
                    raw_url=_string_or_none(node.get("raw_url")),
                    target_path=_string_or_none(node.get("target_path")),
                    sha256=_string_or_none(node.get("sha256")),
                    license=_string_or_none(node.get("license")),
                    is_blob=node.get("type", "blob") == "blob",
                    origin="tree",
                )
            )

    if isinstance(payload.get("items"), list):
        for item in payload["items"]:
            if not isinstance(item, dict):
                items.append(
                    GitHubDiscoveryItem(
                        source_path=None,
                        owner=top_owner,
                        repo=top_repo,
                        ref=top_ref,
                        origin="search",
                    )
                )
                continue
            repo_owner, repo_name, repo_ref = _repository_fields(
                item.get("repository")
            )
            items.append(
                GitHubDiscoveryItem(
                    source_path=_string_or_none(item.get("path")),
                    owner=_string_or_none(item.get("owner")) or repo_owner or top_owner,
                    repo=_string_or_none(item.get("repo")) or repo_name or top_repo,
                    ref=_string_or_none(item.get("ref")) or top_ref or repo_ref,
                    raw_url=_string_or_none(item.get("raw_url")),
                    target_path=_string_or_none(item.get("target_path")),
                    sha256=_string_or_none(item.get("sha256")),
                    license=_string_or_none(item.get("license")),
                    is_blob=True,
                    origin="search",
                )
            )

    if isinstance(payload.get("repositories"), list):
        for repository in payload["repositories"]:
            if isinstance(repository, dict):
                items.extend(
                    _repository_items(
                        repository,
                        default_owner=top_owner,
                        default_repo=top_repo,
                        default_ref=top_ref,
                    )
                )

    if isinstance(payload.get("files"), list):
        items.extend(
            _file_items(
                payload["files"],
                owner=top_owner,
                repo=top_repo,
                ref=top_ref,
                license=top_license,
                origin="files",
            )
        )

    return items


def _repository_items(
    repository: dict[str, Any],
    default_owner: str | None,
    default_repo: str | None,
    default_ref: str | None,
) -> list[GitHubDiscoveryItem]:
    owner, repo = _owner_repo_from_payload(repository)
    repository_owner = owner or default_owner
    repository_repo = repo or default_repo
    repository_ref = _string_or_none(repository.get("ref")) or default_ref
    repository_license = _string_or_none(repository.get("license"))
    files = repository.get("paths")
    if files is None:
        files = repository.get("files", [])
    if not isinstance(files, list):
        return [
            GitHubDiscoveryItem(
                source_path=None,
                owner=repository_owner,
                repo=repository_repo,
                ref=repository_ref,
                origin="repositories",
            )
        ]
    return _file_items(
        files,
        owner=repository_owner,
        repo=repository_repo,
        ref=repository_ref,
        license=repository_license,
        origin="repositories",
    )


def _file_items(
    files: list[Any],
    owner: str | None,
    repo: str | None,
    ref: str | None,
    origin: str,
    license: str | None = None,
) -> list[GitHubDiscoveryItem]:
    items: list[GitHubDiscoveryItem] = []
    for file_entry in files:
        if isinstance(file_entry, str):
            items.append(
                GitHubDiscoveryItem(
                    source_path=file_entry,
                    owner=owner,
                    repo=repo,
                    ref=ref,
                    license=license,
                    origin=origin,
                )
            )
            continue
        if not isinstance(file_entry, dict):
            items.append(
                GitHubDiscoveryItem(
                    source_path=None,
                    owner=owner,
                    repo=repo,
                    ref=ref,
                    origin=origin,
                )
            )
            continue
        items.append(
            GitHubDiscoveryItem(
                source_path=_string_or_none(
                    file_entry.get("path") or file_entry.get("source_path")
                ),
                owner=_string_or_none(file_entry.get("owner")) or owner,
                repo=_string_or_none(file_entry.get("repo")) or repo,
                ref=_string_or_none(file_entry.get("ref")) or ref,
                raw_url=_string_or_none(file_entry.get("raw_url")),
                target_path=_string_or_none(file_entry.get("target_path")),
                sha256=_string_or_none(file_entry.get("sha256")),
                license=_string_or_none(file_entry.get("license")) or license,
                is_blob=file_entry.get("type", "blob") == "blob",
                origin=origin,
            )
        )
    return items


def _build_source_entries(
    items: list[GitHubDiscoveryItem],
    preserve_paths: bool,
    preserve_raw_upstream: bool,
    target_prefix: str,
) -> list[dict[str, Any]]:
    basename_counts = Counter(
        PurePosixPath(item.source_path or "").name
        for item in items
        if not item.target_path
    )
    used_targets: set[str] = set()
    entries = []
    for item in items:
        target_path = _target_path_for_item(
            item,
            basename_counts=basename_counts,
            preserve_paths=preserve_paths,
            target_prefix=target_prefix,
            used_targets=used_targets,
        )
        if target_path is None:
            continue
        entry = {"target_path": target_path}
        if item.raw_url:
            entry["raw_url"] = item.raw_url
            entry["source_path"] = item.source_path
            if preserve_raw_upstream:
                entry["owner"] = item.owner
                entry["repo"] = item.repo
                entry["ref"] = item.ref
        else:
            entry["owner"] = item.owner
            entry["repo"] = item.repo
            entry["ref"] = item.ref
            entry["source_path"] = item.source_path
        if item.sha256:
            entry["sha256"] = item.sha256
        if item.license:
            entry["license"] = item.license
        entries.append({key: value for key, value in entry.items() if value})
    return entries


def _target_path_for_item(
    item: GitHubDiscoveryItem,
    basename_counts: Counter[str],
    preserve_paths: bool,
    target_prefix: str,
    used_targets: set[str],
) -> str | None:
    explicit_target = _clean_relative_path(item.target_path or "")
    join_prefix = True
    if item.target_path and not explicit_target:
        return None
    if explicit_target:
        target = explicit_target
    elif preserve_paths:
        target = item.source_path or ""
    else:
        source_path = item.source_path or ""
        package_relative = _target_relative_to_prefix(source_path, target_prefix)
        basename = PurePosixPath(source_path).name
        if package_relative:
            target = package_relative
        elif _should_preserve_unprefixed_source_path(source_path, target_prefix):
            target = source_path
            join_prefix = False
        elif basename_counts[basename] > 1:
            target = _slug_source_path(source_path)
        else:
            target = basename
    target = _join_target_prefix(target_prefix if join_prefix else "", target)
    target = _dedupe_target(target, used_targets)
    used_targets.add(target)
    return target


def _owner_repo_from_payload(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    owner = _string_or_none(payload.get("owner"))
    repo = _string_or_none(payload.get("repo") or payload.get("name"))
    full_name = _string_or_none(payload.get("full_name"))
    if full_name and not (owner and repo):
        split_owner, split_repo = _split_full_name(full_name)
        owner = owner or split_owner
        repo = repo or split_repo
    owner_obj = payload.get("owner")
    if isinstance(owner_obj, dict):
        owner = _string_or_none(owner_obj.get("login")) or owner
    return owner, repo


def _repository_fields(repository: Any) -> tuple[str | None, str | None, str | None]:
    if not isinstance(repository, dict):
        return None, None, None
    owner, repo = _owner_repo_from_payload(repository)
    ref = _string_or_none(repository.get("ref") or repository.get("default_branch"))
    return owner, repo, ref


def _split_full_name(full_name: str) -> tuple[str | None, str | None]:
    parts = full_name.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None, None
    return parts[0], parts[1]


def _clean_relative_path(value: str) -> str:
    path = str(value).replace("\\", "/").strip()
    if not path:
        return ""
    if re.match(r"^[A-Za-z]:", path):
        return ""
    path = path.lstrip("/")
    parts = [part for part in path.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _slug_source_path(source_path: str) -> str:
    path = PurePosixPath(source_path)
    suffix = path.suffix
    stem = str(path.with_suffix(""))
    slug = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_")
    return f"{slug}{suffix}" if slug else path.name


def _target_relative_to_prefix(source_path: str, target_prefix: str) -> str:
    clean_source = _clean_relative_path(source_path)
    clean_prefix = _clean_relative_path(target_prefix)
    if not clean_source or not clean_prefix:
        return ""
    source_parts = PurePosixPath(clean_source).parts
    prefix_parts = PurePosixPath(clean_prefix).parts
    prefix_length = len(prefix_parts)
    if not prefix_parts:
        return ""
    for index in range(0, len(source_parts) - prefix_length + 1):
        if tuple(source_parts[index : index + prefix_length]) != prefix_parts:
            continue
        relative_parts = source_parts[index + prefix_length :]
        if relative_parts:
            return "/".join(relative_parts)
    return ""


def _should_preserve_unprefixed_source_path(
    source_path: str,
    target_prefix: str,
) -> bool:
    clean_source = _clean_relative_path(source_path)
    clean_prefix = _clean_relative_path(target_prefix)
    if not clean_source or not clean_prefix:
        return False
    source_parts = PurePosixPath(clean_source).parts
    prefix_parts = PurePosixPath(clean_prefix).parts
    if not source_parts or not prefix_parts:
        return False
    for index in range(0, len(source_parts) - len(prefix_parts) + 1):
        if tuple(source_parts[index : index + len(prefix_parts)]) == prefix_parts:
            return False
    return source_parts[0] in {
        "benchmarks",
        "benchmark",
        "docs",
        "doc",
        "examples",
        "example",
        "samples",
        "sample",
        "scripts",
        "tests",
        "test",
        "tools",
    }


def _join_target_prefix(prefix: str, target: str) -> str:
    clean_target = _clean_relative_path(target)
    clean_prefix = _clean_relative_path(prefix)
    if not clean_target:
        return ""
    if not clean_prefix:
        return clean_target
    return f"{clean_prefix}/{clean_target}"


def _dedupe_target(target: str, used_targets: set[str]) -> str:
    if target not in used_targets:
        return target
    path = PurePosixPath(target)
    stem = str(path.with_suffix(""))
    suffix = path.suffix
    index = 2
    while True:
        candidate = f"{stem}_{index}{suffix}"
        if candidate not in used_targets:
            return candidate
        index += 1


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _skipped(item: GitHubDiscoveryItem, reason: str) -> GitHubSourceImportRow:
    return GitHubSourceImportRow(
        source_path=item.source_path or "",
        status="skipped",
        reason=reason,
        source=None,
    )


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _format_upstream(source: dict[str, Any]) -> str:
    if source.get("raw_url"):
        return source["raw_url"]
    owner = source.get("owner", "")
    repo = source.get("repo", "")
    if owner and repo:
        return f"{owner}/{repo}"
    return ""


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Convert GitHub tree/search/repository file-list payloads into "
            "benchmark source manifests."
        )
    )
    parser.add_argument("discovery", help="GitHub discovery JSON payload.")
    parser.add_argument("--owner", help="Default owner when payload omits it.")
    parser.add_argument("--repo", help="Default repo when payload omits it.")
    parser.add_argument("--ref", help="Default ref when payload omits it.")
    parser.add_argument(
        "--include",
        action="append",
        help="Glob pattern to include. Defaults to '*.py'. May be repeated.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        help="Glob pattern to exclude. May be repeated.",
    )
    parser.add_argument(
        "--preserve-paths",
        action="store_true",
        help="Use repository-relative paths as benchmark target paths.",
    )
    parser.add_argument(
        "--preserve-raw-upstream",
        action="store_true",
        help="Keep owner/repo/ref metadata even when a raw_url is present.",
    )
    parser.add_argument(
        "--target-prefix",
        default="",
        help="Optional prefix added to generated target paths.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format.",
    )
    parser.add_argument("--output-json", help="Optional full report JSON path.")
    parser.add_argument("--output-markdown", help="Optional markdown report path.")
    parser.add_argument(
        "--output-sources",
        help="Optional sources JSON path for benchmark_source_miner/fetcher.",
    )
    args = parser.parse_args()

    report = import_github_sources(
        load_json(args.discovery),
        source_path=str(args.discovery),
        owner=args.owner,
        repo=args.repo,
        ref=args.ref,
        include=args.include,
        exclude=args.exclude,
        preserve_paths=args.preserve_paths,
        preserve_raw_upstream=args.preserve_raw_upstream,
        target_prefix=args.target_prefix,
    )
    payload = report.to_dict()
    json_report = json.dumps(payload, indent=2, ensure_ascii=False)
    markdown_report = render_github_source_import_markdown(report)
    if args.output_json:
        Path(args.output_json).write_text(json_report, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown_report, encoding="utf-8")
    if args.output_sources:
        Path(args.output_sources).write_text(
            json.dumps(payload["sources_payload"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if args.format == "markdown":
        print(markdown_report)
    else:
        print(json_report)


if __name__ == "__main__":
    main()
