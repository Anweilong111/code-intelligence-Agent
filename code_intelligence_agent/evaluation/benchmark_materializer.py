from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.github_fetcher import (
    FetchSource,
    GitHubBenchmarkFetcher,
    source_from_dict,
)


@dataclass(frozen=True)
class OverlayFile:
    target_path: str
    content: str


@dataclass(frozen=True)
class TextMutation:
    target_path: str
    find: str
    replace: str
    count: int = 1
    description: str = ""


@dataclass(frozen=True)
class BenchmarkTemplateCase:
    name: str
    repo_path: str
    sources: list[FetchSource] = field(default_factory=list)
    files: list[OverlayFile] = field(default_factory=list)
    mutations: list[TextMutation] = field(default_factory=list)
    benchmark: dict[str, Any] = field(default_factory=dict)


class BenchmarkMaterializer:
    def __init__(
        self,
        fetcher: GitHubBenchmarkFetcher | None = None,
        source_cache_dir: str | Path | None = None,
    ) -> None:
        self.fetcher = fetcher or GitHubBenchmarkFetcher()
        self.source_cache_dir = (
            Path(source_cache_dir) if source_cache_dir is not None else None
        )

    def materialize_template(
        self,
        template_path: str | Path,
        output_dir: str | Path,
        source_cache_dir: str | Path | None = None,
    ) -> Path:
        template_file = Path(template_path)
        data = json.loads(template_file.read_text(encoding="utf-8"))
        cases = [_template_case_from_dict(item) for item in data.get("cases", [])]
        return self.materialize_cases(
            cases,
            output_dir,
            source_cache_dir=source_cache_dir,
        )

    def materialize_cases(
        self,
        cases: list[BenchmarkTemplateCase],
        output_dir: str | Path,
        source_cache_dir: str | Path | None = None,
    ) -> Path:
        output_root = Path(output_dir)
        output_root.mkdir(parents=True, exist_ok=True)
        cache_root = (
            Path(source_cache_dir)
            if source_cache_dir is not None
            else self.source_cache_dir or output_root / ".source_cache"
        )
        manifest_cases = []
        for case in cases:
            repo_root = _safe_join(output_root, case.repo_path)
            repo_root.mkdir(parents=True, exist_ok=True)
            if case.sources:
                self.fetcher.fetch_sources(
                    _safe_sources(case.sources),
                    repo_root,
                    cache_dir=cache_root,
                )
            for mutation in case.mutations:
                _apply_text_mutation(repo_root, mutation)
            for file in case.files:
                target = _safe_join(repo_root, file.target_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(file.content, encoding="utf-8")
            manifest_case = {
                "name": case.name,
                "repo_path": case.repo_path,
                **case.benchmark,
            }
            if case.sources or case.mutations:
                metadata = dict(manifest_case.get("metadata", {}))
                if case.sources:
                    metadata["source_files"] = [
                        asdict(source) for source in case.sources
                    ]
                metadata["materialized_mutations"] = [
                    asdict(mutation) for mutation in case.mutations
                ]
                manifest_case["metadata"] = metadata
            manifest_cases.append(manifest_case)

        manifest_path = output_root / "manifest.json"
        manifest_path.write_text(
            json.dumps({"cases": manifest_cases}, indent=2),
            encoding="utf-8",
        )
        return manifest_path


def _template_case_from_dict(item: dict[str, Any]) -> BenchmarkTemplateCase:
    return BenchmarkTemplateCase(
        name=item["name"],
        repo_path=item["repo_path"],
        sources=[source_from_dict(source) for source in item.get("sources", [])],
        files=[
            OverlayFile(
                target_path=file["target_path"],
                content=file["content"],
            )
            for file in item.get("files", [])
        ],
        mutations=[
            TextMutation(
                target_path=mutation["target_path"],
                find=mutation["find"],
                replace=mutation["replace"],
                count=int(mutation.get("count", 1)),
                description=mutation.get("description", ""),
            )
            for mutation in item.get("mutations", [])
        ],
        benchmark=dict(item.get("benchmark", {})),
    )


def _safe_join(root: Path, relative_path: str) -> Path:
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe relative path: {relative_path}")
    resolved_root = root.resolve()
    resolved_target = (resolved_root / path).resolve()
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"Path escapes output directory: {relative_path}") from exc
    return resolved_target


def _safe_sources(sources: list[FetchSource]) -> list[FetchSource]:
    safe = []
    for source in sources:
        target_path = Path(source.target_path)
        if target_path.is_absolute() or ".." in target_path.parts:
            raise ValueError(f"Unsafe relative path: {source.target_path}")
        safe.append(source)
    return safe


def _apply_text_mutation(repo_root: Path, mutation: TextMutation) -> None:
    if mutation.count < 1:
        raise ValueError(f"Mutation count must be positive: {mutation.count}")
    target = _safe_join(repo_root, mutation.target_path)
    if not target.exists():
        raise FileNotFoundError(f"Mutation target does not exist: {mutation.target_path}")
    text = target.read_text(encoding="utf-8")
    occurrences = text.count(mutation.find)
    if occurrences < mutation.count:
        raise ValueError(
            f"Mutation pattern not found enough times in {mutation.target_path}: "
            f"expected {mutation.count}, found {occurrences}"
        )
    target.write_text(
        text.replace(mutation.find, mutation.replace, mutation.count),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Materialize fetched sources and tests into a benchmark manifest."
    )
    parser.add_argument("template", help="Benchmark template JSON")
    parser.add_argument("output_dir", help="Output directory for generated benchmark")
    parser.add_argument(
        "--source-cache-dir",
        help=(
            "Optional shared raw-source cache directory. Defaults to "
            "<output_dir>/.source_cache."
        ),
    )
    args = parser.parse_args()

    manifest_path = BenchmarkMaterializer().materialize_template(
        args.template,
        args.output_dir,
        source_cache_dir=args.source_cache_dir,
    )
    print(str(manifest_path))


if __name__ == "__main__":
    main()
