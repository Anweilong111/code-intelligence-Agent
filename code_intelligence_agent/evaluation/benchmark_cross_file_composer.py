from __future__ import annotations

import argparse
import json
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from code_intelligence_agent.evaluation.benchmark_validator import BenchmarkValidator


@dataclass(frozen=True)
class CrossFileCompositionRow:
    candidate_id: str
    status: str
    reasons: list[str]
    source_target: str = ""
    wrapper_target: str = ""
    wrapper_function: str = ""
    wrapper_depth: int = 0
    wrapper_targets: list[str] | None = None
    template_case: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CrossFileCompositionReport:
    catalog_path: str
    candidate_count: int
    composed_count: int
    skipped_count: int
    rows: list[CrossFileCompositionRow]

    def to_dict(self) -> dict[str, Any]:
        cases = [
            row.template_case
            for row in self.rows
            if row.status == "composed" and row.template_case is not None
        ]
        return {
            "catalog_path": self.catalog_path,
            "candidate_count": self.candidate_count,
            "composed_count": self.composed_count,
            "skipped_count": self.skipped_count,
            "rows": [row.to_dict() for row in self.rows],
            "template": {"cases": cases},
        }


def compose_cross_file_benchmarks(
    catalog_payload: dict[str, Any],
    catalog_path: str = "",
    include_rules: list[str] | None = None,
    max_cases: int | None = None,
    service_suffix: str = "service",
    wrapper_depth: int = 1,
) -> CrossFileCompositionReport:
    include_rule_set = set(include_rules or [])
    candidates = _extract_candidates(catalog_payload)
    rows: list[CrossFileCompositionRow] = []
    composed = 0
    effective_wrapper_depth = max(1, wrapper_depth)
    for candidate in candidates:
        if max_cases is not None and composed >= max_cases:
            rows.append(_skipped(candidate, "max_cases_reached"))
            continue
        if include_rule_set and not (
            include_rule_set & set(_candidate_rule_ids(candidate))
        ):
            rows.append(_skipped(candidate, "rule_not_selected"))
            continue
        row = _compose_candidate(
            candidate,
            service_suffix=service_suffix,
            wrapper_depth=effective_wrapper_depth,
        )
        if row.status == "composed":
            composed += 1
        rows.append(row)
    return CrossFileCompositionReport(
        catalog_path=catalog_path,
        candidate_count=len(candidates),
        composed_count=sum(1 for row in rows if row.status == "composed"),
        skipped_count=sum(1 for row in rows if row.status != "composed"),
        rows=rows,
    )


def render_cross_file_composition_markdown(
    report: CrossFileCompositionReport,
) -> str:
    lines = [
        "# Cross-File Benchmark Composition",
        "",
        f"- Source: `{report.catalog_path or '<memory>'}`",
        f"- Candidates: {report.candidate_count}",
        f"- Composed: {report.composed_count}",
        f"- Skipped: {report.skipped_count}",
        "",
        "| Candidate | Status | Source | Wrapper | Function | Reasons |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in report.rows:
        lines.append(
            "| "
            f"{_markdown_cell(row.candidate_id)} | "
            f"{_markdown_cell(row.status)} | "
            f"{_markdown_cell(row.source_target)} | "
            f"{_markdown_cell(row.wrapper_target)} | "
            f"{_markdown_cell(row.wrapper_function)} | "
            f"{_markdown_cell(', '.join(row.reasons))} |"
        )
    return "\n".join(lines)


def _compose_candidate(
    candidate: dict[str, Any],
    service_suffix: str,
    wrapper_depth: int,
) -> CrossFileCompositionRow:
    candidate_id = str(candidate.get("id", ""))
    case = _deepcopy_json(_candidate_template_case(candidate))
    if not case:
        return _skipped(candidate, "missing_template_case")
    sources = case.get("sources", [])
    if not isinstance(sources, list) or len(sources) != 1:
        return _skipped(candidate, "requires_exactly_one_source")
    source_target = str(sources[0].get("target_path", ""))
    source_module = _module_name_from_target(source_target)
    if not source_module:
        return _skipped(candidate, "unsupported_source_target")
    function_name = _simple_buggy_function(case)
    if not function_name:
        return _skipped(candidate, "requires_simple_buggy_function")
    test_files = _test_files(case)
    if not test_files:
        return _skipped(candidate, "missing_test_file")
    service_module = _safe_identifier(
        f"{_slug(case.get('name') or candidate_id)}_{service_suffix}"
    )
    wrapper_function = _safe_identifier(f"call_{function_name}")
    wrapper_files = _wrapper_files(
        source_module=source_module,
        function_name=function_name,
        service_module=service_module,
        wrapper_function=wrapper_function,
        wrapper_depth=wrapper_depth,
    )
    entry_wrapper = wrapper_files[0]
    service_target = entry_wrapper["target_path"]
    transformed_files = []
    for test_file in test_files:
        transformed = _transform_test_import(
            test_file,
            source_module=source_module,
            function_name=function_name,
            service_module=entry_wrapper["module_name"],
            wrapper_function=entry_wrapper["function_name"],
        )
        if transformed is None:
            return _skipped(candidate, "test_import_not_rewritable")
        transformed_files.append(transformed)

    case["name"] = f"cross_file_{case['name']}"
    case["repo_path"] = f"{case['repo_path']}_cross_file"
    case["files"] = [
        *[
            {
                "target_path": wrapper_file["target_path"],
                "content": wrapper_file["content"],
            }
            for wrapper_file in wrapper_files
        ],
        *transformed_files,
    ]
    benchmark = dict(case.get("benchmark", {}))
    metadata = dict(benchmark.get("metadata", {}))
    metadata.update(
        {
            "source": "cross_file_recipe_composition",
            "cross_file_trace": True,
            "wrapper_module": entry_wrapper["module_name"],
            "wrapper_function": entry_wrapper["function_name"],
            "wrapper_depth": wrapper_depth,
            "wrapper_modules": [item["module_name"] for item in wrapper_files],
            "wrapper_functions": [item["function_name"] for item in wrapper_files],
            "wrapper_targets": [item["target_path"] for item in wrapper_files],
            "wrapped_module": source_module,
            "wrapped_function": function_name,
            "source_candidate_id": candidate_id,
            "original_case_name": case["name"].removeprefix("cross_file_"),
        }
    )
    benchmark["metadata"] = metadata
    case["benchmark"] = benchmark
    validation_errors = _template_validation_errors(case)
    if validation_errors:
        return CrossFileCompositionRow(
            candidate_id=candidate_id,
            status="skipped",
            reasons=[f"validator_error={item}" for item in validation_errors],
            source_target=source_target,
            wrapper_target=service_target,
            wrapper_function=entry_wrapper["function_name"],
            wrapper_depth=wrapper_depth,
            wrapper_targets=[item["target_path"] for item in wrapper_files],
            template_case=None,
        )
    reasons = ["wrapped_test_import"]
    if wrapper_depth > 1:
        reasons.append(f"wrapper_depth={wrapper_depth}")
    return CrossFileCompositionRow(
        candidate_id=candidate_id,
        status="composed",
        reasons=reasons,
        source_target=source_target,
        wrapper_target=service_target,
        wrapper_function=entry_wrapper["function_name"],
        wrapper_depth=wrapper_depth,
        wrapper_targets=[item["target_path"] for item in wrapper_files],
        template_case=case,
    )


def _wrapper_files(
    source_module: str,
    function_name: str,
    service_module: str,
    wrapper_function: str,
    wrapper_depth: int,
) -> list[dict[str, str]]:
    wrappers: list[dict[str, str]] = []
    for index in range(wrapper_depth):
        hop = index + 1
        module_name = service_module if index == 0 else f"{service_module}_hop{hop}"
        function = (
            wrapper_function
            if index == 0
            else _safe_identifier(f"{wrapper_function}_hop{hop}")
        )
        wrappers.append(
            {
                "module_name": module_name,
                "target_path": f"{module_name}.py",
                "function_name": function,
                "content": "",
            }
        )
    for index, wrapper in enumerate(wrappers):
        if index == len(wrappers) - 1:
            wrapper["content"] = _service_content(
                import_module=source_module,
                imported_function=function_name,
                imported_alias=f"_target_{function_name}",
                wrapper_function=wrapper["function_name"],
            )
        else:
            next_wrapper = wrappers[index + 1]
            next_alias = f"_next_{next_wrapper['function_name']}"
            wrapper["content"] = _service_content(
                import_module=next_wrapper["module_name"],
                imported_function=next_wrapper["function_name"],
                imported_alias=next_alias,
                wrapper_function=wrapper["function_name"],
            )
    return wrappers


def _service_content(
    import_module: str,
    imported_function: str,
    imported_alias: str,
    wrapper_function: str,
) -> str:
    return (
        f"from {import_module} import {imported_function} as {imported_alias}\n\n\n"
        f"def {wrapper_function}(*args, **kwargs):\n"
        f"    return {imported_alias}(*args, **kwargs)\n"
    )


def _transform_test_import(
    test_file: dict[str, Any],
    source_module: str,
    function_name: str,
    service_module: str,
    wrapper_function: str,
) -> dict[str, Any] | None:
    content = str(test_file.get("content", ""))
    original = f"from {source_module} import {function_name}"
    replacement = f"from {service_module} import {wrapper_function} as {function_name}"
    if original not in content:
        return None
    transformed = dict(test_file)
    transformed["content"] = content.replace(original, replacement, 1)
    return transformed


def _template_validation_errors(case: dict[str, Any]) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "template.json"
        path.write_text(
            json.dumps({"cases": [case]}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        report = BenchmarkValidator().validate_template(path)
    return [f"{issue.location}:{issue.message}" for issue in report.errors]


def _extract_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        catalog = payload.get("catalog", {})
        if isinstance(catalog, dict):
            candidates = catalog.get("candidates", [])
    if not isinstance(candidates, list):
        return []
    return [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict)
    ]


def _candidate_template_case(candidate: dict[str, Any]) -> dict[str, Any]:
    case = candidate.get("template_case", {})
    return case if isinstance(case, dict) else {}


def _candidate_rule_ids(candidate: dict[str, Any]) -> list[str]:
    rules = candidate.get("rule_ids")
    if isinstance(rules, list):
        return [str(rule) for rule in rules if str(rule)]
    case = _candidate_template_case(candidate)
    benchmark = case.get("benchmark", {}) if isinstance(case, dict) else {}
    if isinstance(benchmark, dict) and isinstance(benchmark.get("expected_rule_ids"), list):
        return [str(rule) for rule in benchmark["expected_rule_ids"] if str(rule)]
    return []


def _simple_buggy_function(case: dict[str, Any]) -> str:
    benchmark = case.get("benchmark", {})
    if not isinstance(benchmark, dict):
        return ""
    functions = benchmark.get("buggy_functions", [])
    if not isinstance(functions, list) or not functions:
        return ""
    name = str(functions[0])
    if not _is_identifier(name):
        return ""
    return name


def _test_files(case: dict[str, Any]) -> list[dict[str, Any]]:
    files = case.get("files", [])
    if not isinstance(files, list):
        return []
    return [
        file
        for file in files
        if isinstance(file, dict)
        and str(file.get("target_path", "")).endswith(".py")
        and "def test_" in str(file.get("content", ""))
    ]


def _module_name_from_target(target_path: str) -> str:
    path = PurePosixPath(target_path)
    if path.suffix != ".py":
        return ""
    module_parts = list(path.with_suffix("").parts)
    if not module_parts or not all(_is_identifier(part) for part in module_parts):
        return ""
    return ".".join(module_parts)


def _safe_identifier(value: str) -> str:
    candidate = re.sub(r"[^0-9A-Za-z_]+", "_", value).strip("_")
    if not candidate or candidate[0].isdigit():
        candidate = f"generated_{candidate}"
    return candidate


def _is_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value))


def _slug(value: Any) -> str:
    return _safe_identifier(str(value).lower())


def _skipped(candidate: dict[str, Any], reason: str) -> CrossFileCompositionRow:
    return CrossFileCompositionRow(
        candidate_id=str(candidate.get("id", "")),
        status="skipped",
        reasons=[reason],
    )


def _deepcopy_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compose single-source recipe candidates into cross-file benchmarks."
    )
    parser.add_argument("catalog", help="Catalog JSON containing candidates.")
    parser.add_argument(
        "--include-rule",
        action="append",
        help="Only compose candidates containing this rule id. May be repeated.",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Optional maximum number of composed cases.",
    )
    parser.add_argument(
        "--wrapper-depth",
        type=int,
        default=1,
        help=(
            "Number of cross-file wrapper hops to synthesize before calling the "
            "buggy function."
        ),
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
        help="Optional composed benchmark template JSON path.",
    )
    args = parser.parse_args()

    report = compose_cross_file_benchmarks(
        load_json(args.catalog),
        catalog_path=str(args.catalog),
        include_rules=args.include_rule,
        max_cases=args.max_cases,
        wrapper_depth=args.wrapper_depth,
    )
    payload = report.to_dict()
    json_report = json.dumps(payload, indent=2, ensure_ascii=False)
    markdown_report = render_cross_file_composition_markdown(report)
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
