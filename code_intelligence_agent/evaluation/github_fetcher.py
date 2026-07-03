from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FetchSource:
    target_path: str
    raw_url: str | None = None
    owner: str | None = None
    repo: str | None = None
    ref: str | None = None
    source_path: str | None = None
    sha256: str | None = None
    license: str | None = None

    @property
    def resolved_url(self) -> str:
        if self.raw_url:
            return self.raw_url
        missing = [
            name
            for name, value in {
                "owner": self.owner,
                "repo": self.repo,
                "ref": self.ref,
                "source_path": self.source_path,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(f"Missing GitHub source fields: {', '.join(missing)}")
        return github_raw_url(
            owner=self.owner or "",
            repo=self.repo or "",
            ref=self.ref or "",
            path=self.source_path or "",
        )


class GitHubBenchmarkFetcher:
    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None

    def fetch_manifest_sources(
        self,
        manifest_path: str | Path,
        output_dir: str | Path,
    ) -> list[Path]:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        sources = [
            FetchSource(
                target_path=item["target_path"],
                raw_url=item.get("raw_url"),
                owner=item.get("owner"),
                repo=item.get("repo"),
                ref=item.get("ref"),
                source_path=item.get("source_path"),
                sha256=item.get("sha256"),
                license=item.get("license"),
            )
            for item in manifest.get("sources", [])
        ]
        return self.fetch_sources(sources, output_dir)

    def fetch_sources(
        self,
        sources: list[FetchSource],
        output_dir: str | Path,
        cache_dir: str | Path | None = None,
    ) -> list[Path]:
        output_root = Path(output_dir)
        output_root.mkdir(parents=True, exist_ok=True)
        cache_root = (
            Path(cache_dir)
            if cache_dir is not None
            else self.cache_dir or output_root / ".source_cache"
        )
        written = []
        for source in sources:
            target = output_root / source.target_path
            cached = _read_existing_source(target, source)
            if cached is not None:
                written.append(target)
                continue

            content = _read_cached_source(cache_root, source)
            if content is None:
                content = read_source_bytes(source.resolved_url)
                _validate_source_digest(content, source)
                _write_cached_source(cache_root, source, content)
            else:
                _validate_source_digest(content, source)

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            written.append(target)
        return written


def github_raw_url(owner: str, repo: str, ref: str, path: str) -> str:
    safe_path = path.lstrip("/")
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{safe_path}"


def read_source_bytes(url_or_path: str) -> bytes:
    if url_or_path.startswith(("http://", "https://", "file://")):
        with urllib.request.urlopen(url_or_path, timeout=20) as response:
            return response.read()
    path = Path(url_or_path)
    if path.exists():
        return path.read_bytes()
    with urllib.request.urlopen(url_or_path, timeout=20) as response:
        return response.read()


def _read_existing_source(target: Path, source: FetchSource) -> bytes | None:
    if not target.exists() or not source.sha256:
        return None
    content = target.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != source.sha256:
        return None
    return content


def _read_cached_source(cache_root: Path, source: FetchSource) -> bytes | None:
    cache_path = _source_cache_path(cache_root, source)
    if not cache_path.exists():
        return None
    content = cache_path.read_bytes()
    if source.sha256 and hashlib.sha256(content).hexdigest() != source.sha256:
        return None
    return content


def _write_cached_source(
    cache_root: Path,
    source: FetchSource,
    content: bytes,
) -> None:
    cache_path = _source_cache_path(cache_root, source)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(content)


def _validate_source_digest(content: bytes, source: FetchSource) -> None:
    digest = hashlib.sha256(content).hexdigest()
    if source.sha256 and digest != source.sha256:
        raise ValueError(
            f"sha256 mismatch for {source.target_path}: expected "
            f"{source.sha256}, got {digest}"
        )


def _source_cache_path(cache_root: Path, source: FetchSource) -> Path:
    suffix = Path(source.target_path).suffix
    if source.sha256:
        return cache_root / f"{source.sha256}{suffix}"
    key = hashlib.sha256(source.resolved_url.encode("utf-8")).hexdigest()
    return cache_root / f"{key}{suffix}"


def copy_local_tree(source_dir: str | Path, output_dir: str | Path) -> Path:
    source = Path(source_dir)
    target = Path(output_dir)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch GitHub/raw benchmark sources.")
    parser.add_argument("manifest", help="Manifest JSON containing a sources array")
    parser.add_argument("output_dir", help="Directory where files will be written")
    args = parser.parse_args()

    written = GitHubBenchmarkFetcher().fetch_manifest_sources(
        args.manifest,
        args.output_dir,
    )
    print(json.dumps([str(path) for path in written], indent=2))


def source_from_dict(item: dict[str, Any]) -> FetchSource:
    return FetchSource(
        target_path=item["target_path"],
        raw_url=item.get("raw_url"),
        owner=item.get("owner"),
        repo=item.get("repo"),
        ref=item.get("ref"),
        source_path=item.get("source_path"),
        sha256=item.get("sha256"),
        license=item.get("license"),
    )


if __name__ == "__main__":
    main()
