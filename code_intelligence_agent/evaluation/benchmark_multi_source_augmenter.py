from __future__ import annotations

import argparse
import ast
import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from code_intelligence_agent.evaluation.benchmark_validator import BenchmarkValidator
from code_intelligence_agent.evaluation.github_fetcher import (
    GitHubBenchmarkFetcher,
    source_from_dict,
)


@dataclass(frozen=True)
class MultiSourceAugmentationRow:
    case_name: str
    status: str
    source_count_before: int
    source_count_after: int
    added_sources: list[str]
    matched_imports: list[str]
    unresolved_imports: list[str]
    errors: list[str]
    template_case: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MultiSourceAugmentationReport:
    template_path: str
    sources_path: str
    case_count: int
    available_source_count: int
    augmented_count: int
    unchanged_count: int
    failed_count: int
    rows: list[MultiSourceAugmentationRow]

    def to_dict(self) -> dict[str, Any]:
        cases = [
            row.template_case
            for row in self.rows
            if row.template_case is not None and row.status != "failed"
        ]
        return {
            "template_path": self.template_path,
            "sources_path": self.sources_path,
            "case_count": self.case_count,
            "available_source_count": self.available_source_count,
            "augmented_count": self.augmented_count,
            "unchanged_count": self.unchanged_count,
            "failed_count": self.failed_count,
            "rows": [row.to_dict() for row in self.rows],
            "template": {"cases": cases},
        }


def augment_template_with_dependency_sources(
    template_payload: dict[str, Any],
    available_sources_payload: dict[str, Any],
    template_path: str = "",
    sources_path: str = "",
    source_cache_dir: str | Path | None = None,
    max_depth: int = 1,
) -> MultiSourceAugmentationReport:
    cases = _extract_template_cases(template_payload)
    available_sources = _extract_sources(available_sources_payload)
    source_index = _build_source_index(available_sources)
    rows = [
        _augment_case(
            case,
            source_index=source_index,
            source_cache_dir=source_cache_dir,
            max_depth=max_depth,
        )
        for case in cases
    ]
    return MultiSourceAugmentationReport(
        template_path=template_path,
        sources_path=sources_path,
        case_count=len(cases),
        available_source_count=len(available_sources),
        augmented_count=sum(1 for row in rows if row.status == "augmented"),
        unchanged_count=sum(1 for row in rows if row.status == "unchanged"),
        failed_count=sum(1 for row in rows if row.status == "failed"),
        rows=rows,
    )


def render_multi_source_augmentation_markdown(
    report: MultiSourceAugmentationReport,
) -> str:
    lines = [
        "# Multi-Source Benchmark Augmentation",
        "",
        f"- Template: `{report.template_path or '<memory>'}`",
        f"- Sources: `{report.sources_path or '<memory>'}`",
        f"- Cases: {report.case_count}",
        f"- Available Sources: {report.available_source_count}",
        f"- Augmented: {report.augmented_count}",
        f"- Unchanged: {report.unchanged_count}",
        f"- Failed: {report.failed_count}",
        "",
        "| Case | Status | Sources | Added | Matched Imports | Unresolved Imports | Errors |",
        "| --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for row in report.rows:
        lines.append(
            "| "
            f"{_markdown_cell(row.case_name)} | "
            f"{_markdown_cell(row.status)} | "
            f"{row.source_count_before}->{row.source_count_after} | "
            f"{_markdown_cell(', '.join(row.added_sources))} | "
            f"{_markdown_cell(', '.join(row.matched_imports))} | "
            f"{_markdown_cell(', '.join(row.unresolved_imports))} | "
            f"{_markdown_cell(', '.join(row.errors))} |"
        )
    return "\n".join(lines)


def _augment_case(
    case: dict[str, Any],
    source_index: dict[str, dict[str, Any]],
    source_cache_dir: str | Path | None,
    max_depth: int,
) -> MultiSourceAugmentationRow:
    case_copy = _deepcopy_json(case)
    case_name = str(case_copy.get("name", ""))
    sources = case_copy.get("sources", [])
    if not isinstance(sources, list):
        return MultiSourceAugmentationRow(
            case_name=case_name,
            status="failed",
            source_count_before=0,
            source_count_after=0,
            added_sources=[],
            matched_imports=[],
            unresolved_imports=[],
            errors=["case_sources_not_list"],
            template_case=None,
        )
    existing_targets = {
        str(source.get("target_path", ""))
        for source in sources
        if isinstance(source, dict)
    }
    queue: list[tuple[dict[str, Any], int]] = [
        (source, 0) for source in sources if isinstance(source, dict)
    ]
    added_sources: list[str] = []
    matched_imports: list[str] = []
    unresolved_imports: set[str] = set()
    errors: list[str] = []
    seen_targets = set(existing_targets)

    while queue:
        source, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        try:
            text = _read_source_text(source, source_cache_dir=source_cache_dir)
        except Exception as exc:
            errors.append(
                f"source_read_error:{source.get('target_path', '')}:{type(exc).__name__}"
            )
            continue
        imports = _imported_modules(text, str(source.get("target_path", "")))
        for imported_module in imports:
            dependency = source_index.get(imported_module)
            if dependency is None:
                if _looks_local_import(imported_module):
                    unresolved_imports.add(imported_module)
                continue
            dependency_target = str(dependency.get("target_path", ""))
            if not dependency_target or dependency_target in seen_targets:
                continue
            dependency_copy = _deepcopy_json(dependency)
            sources.append(dependency_copy)
            seen_targets.add(dependency_target)
            added_sources.append(dependency_target)
            matched_imports.append(imported_module)
            queue.append((dependency_copy, depth + 1))

    case_copy["sources"] = sources
    validation_errors = _template_validation_errors(case_copy)
    if validation_errors:
        return MultiSourceAugmentationRow(
            case_name=case_name,
            status="failed",
            source_count_before=len(existing_targets),
            source_count_after=len(sources),
            added_sources=added_sources,
            matched_imports=sorted(set(matched_imports)),
            unresolved_imports=sorted(unresolved_imports),
            errors=errors + [f"validator_error:{item}" for item in validation_errors],
            template_case=None,
        )
    benchmark = dict(case_copy.get("benchmark", {}))
    metadata = dict(benchmark.get("metadata", {}))
    if added_sources:
        metadata.update(
            {
                "multi_source_raw": True,
                "dependency_source_targets": added_sources,
                "dependency_imports": sorted(set(matched_imports)),
                "dependency_max_depth": max_depth,
            }
        )
        benchmark["metadata"] = metadata
        case_copy["benchmark"] = benchmark
    return MultiSourceAugmentationRow(
        case_name=case_name,
        status="augmented" if added_sources else "unchanged",
        source_count_before=len(existing_targets),
        source_count_after=len(sources),
        added_sources=added_sources,
        matched_imports=sorted(set(matched_imports)),
        unresolved_imports=sorted(unresolved_imports),
        errors=errors,
        template_case=case_copy,
    )


def _read_source_text(
    source: dict[str, Any],
    source_cache_dir: str | Path | None = None,
) -> str:
    fetch_source = source_from_dict(source)
    with tempfile.TemporaryDirectory() as tmp_dir:
        written = GitHubBenchmarkFetcher().fetch_sources(
            [fetch_source],
            tmp_dir,
            cache_dir=source_cache_dir,
        )
        if not written:
            raise FileNotFoundError(str(source.get("target_path", "")))
        return written[0].read_text(encoding="utf-8")


def _imported_modules(source_text: str, target_path: str) -> list[str]:
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    modules: set[str] = set()
    current_module = _module_name_from_path(target_path)
    package_parts = current_module.split(".")[:-1] if current_module else []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = _import_from_module(node, package_parts)
            if module:
                modules.add(module)
            if node.level > 0 and not node.module:
                base_parts = _relative_import_base_parts(node, package_parts)
                for alias in node.names:
                    if alias.name and alias.name != "*":
                        modules.add(".".join(base_parts + [alias.name]))
    return sorted(modules)


def _import_from_module(node: ast.ImportFrom, package_parts: list[str]) -> str:
    if node.level <= 0:
        return node.module or ""
    base_parts = _relative_import_base_parts(node, package_parts)
    module_parts = [part for part in (node.module or "").split(".") if part]
    return ".".join(base_parts + module_parts)


def _relative_import_base_parts(
    node: ast.ImportFrom,
    package_parts: list[str],
) -> list[str]:
    base_count = max(0, len(package_parts) - node.level + 1)
    return package_parts[:base_count]


def _build_source_index(sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for source in sources:
        for module in _source_modules(source):
            index.setdefault(module, source)
    return index


def _source_modules(source: dict[str, Any]) -> set[str]:
    modules = set()
    for key in ["target_path", "source_path"]:
        value = source.get(key)
        if not value:
            continue
        module = _module_name_from_path(str(value))
        if module:
            modules.add(module)
            modules.add(module.split(".")[-1])
            if module.endswith(".__init__"):
                package_module = module[: -len(".__init__")]
                modules.add(package_module)
                modules.add(package_module.split(".")[-1])
    return modules


def _module_name_from_path(path: str) -> str:
    pure = PurePosixPath(path.replace("\\", "/"))
    if pure.suffix != ".py":
        return ""
    parts = list(pure.with_suffix("").parts)
    if not parts or not all(_is_identifier(part) for part in parts):
        return ""
    return ".".join(parts)


def _looks_local_import(module: str) -> bool:
    if not module:
        return False
    root = module.split(".")[0]
    return root not in {
        "__future__",
        "abc",
        "ast",
        "collections",
        "dataclasses",
        "doctest",
        "functools",
        "itertools",
        "json",
        "math",
        "os",
        "pathlib",
        "random",
        "re",
        "statistics",
        "sys",
        "timeit",
        "typing",
    }


def _extract_template_cases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    cases = payload.get("cases", [])
    if not isinstance(cases, list):
        return []
    return [case for case in cases if isinstance(case, dict)]


def _extract_sources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sources = payload.get("sources", [])
    if not isinstance(sources, list):
        return []
    return [source for source in sources if isinstance(source, dict)]


def _template_validation_errors(case: dict[str, Any]) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "template.json"
        path.write_text(
            json.dumps({"cases": [case]}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        report = BenchmarkValidator().validate_template(path)
    return [f"{issue.location}:{issue.message}" for issue in report.errors]


def _is_identifier(value: str) -> bool:
    return bool(value) and value.isidentifier()


def _deepcopy_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Augment benchmark templates with dependency sources selected from "
            "an available sources manifest."
        )
    )
    parser.add_argument("template", help="Benchmark template JSON")
    parser.add_argument("sources", help="Available sources manifest JSON")
    parser.add_argument(
        "--max-depth",
        type=int,
        default=1,
        help="Maximum dependency traversal depth.",
    )
    parser.add_argument(
        "--source-cache-dir",
        help="Optional shared raw-source cache directory.",
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
        "--output-template",
        help="Optional augmented benchmark template JSON path.",
    )
    args = parser.parse_args()

    report = augment_template_with_dependency_sources(
        load_json(args.template),
        load_json(args.sources),
        template_path=str(args.template),
        sources_path=str(args.sources),
        source_cache_dir=args.source_cache_dir,
        max_depth=args.max_depth,
    )
    payload = report.to_dict()
    json_report = json.dumps(payload, indent=2, ensure_ascii=False)
    markdown_report = render_multi_source_augmentation_markdown(report)
    if args.output_json:
        Path(args.output_json).write_text(json_report, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown_report, encoding="utf-8")
    if args.output_template:
        Path(args.output_template).write_text(
            json.dumps(payload["template"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if args.format == "markdown":
        print(markdown_report)
    else:
        print(json_report)


if __name__ == "__main__":
    main()
