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
class MultiBugCompositionRow:
    candidate_ids: list[str]
    status: str
    reasons: list[str]
    functions: list[str]
    rules: list[str]
    wrapper_depth: int = 0
    wrapper_targets: list[str] | None = None
    template_case: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MultiBugCompositionReport:
    catalog_path: str
    candidate_count: int
    composed_count: int
    skipped_count: int
    rows: list[MultiBugCompositionRow]

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


def compose_multi_bug_benchmarks(
    catalog_payload: dict[str, Any],
    catalog_path: str = "",
    include_rules: list[str] | None = None,
    max_cases: int | None = None,
    bugs_per_case: int = 2,
    wrapper_depth: int = 0,
) -> MultiBugCompositionReport:
    candidates = _eligible_candidates(
        _extract_candidates(catalog_payload),
        include_rules=include_rules,
    )
    rows: list[MultiBugCompositionRow] = []
    composed = 0
    effective_bugs_per_case = max(2, bugs_per_case)
    effective_wrapper_depth = max(0, wrapper_depth)
    used_ids: set[str] = set()

    for group in _candidate_groups(candidates, effective_bugs_per_case):
        group_ids = [str(candidate.get("id", "")) for candidate in group]
        if max_cases is not None and composed >= max_cases:
            rows.append(_skipped(group, "max_cases_reached"))
            continue
        if any(candidate_id in used_ids for candidate_id in group_ids):
            rows.append(_skipped(group, "candidate_already_used"))
            continue
        row = _compose_group(group, wrapper_depth=effective_wrapper_depth)
        if row.status == "composed":
            composed += 1
            used_ids.update(group_ids)
        rows.append(row)

    return MultiBugCompositionReport(
        catalog_path=catalog_path,
        candidate_count=len(candidates),
        composed_count=sum(1 for row in rows if row.status == "composed"),
        skipped_count=sum(1 for row in rows if row.status != "composed"),
        rows=rows,
    )


def render_multi_bug_composition_markdown(
    report: MultiBugCompositionReport,
) -> str:
    lines = [
        "# Multi-Bug Benchmark Composition",
        "",
        f"- Source: `{report.catalog_path or '<memory>'}`",
        f"- Candidates: {report.candidate_count}",
        f"- Composed: {report.composed_count}",
        f"- Skipped: {report.skipped_count}",
        "",
        "| Candidates | Status | Functions | Rules | Reasons |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report.rows:
        lines.append(
            "| "
            f"{_markdown_cell(', '.join(row.candidate_ids))} | "
            f"{_markdown_cell(row.status)} | "
            f"{_markdown_cell(', '.join(row.functions))} | "
            f"{_markdown_cell(', '.join(row.rules))} | "
            f"{_markdown_cell(', '.join(row.reasons))} |"
        )
    return "\n".join(lines)


def _compose_group(
    candidates: list[dict[str, Any]],
    wrapper_depth: int = 0,
) -> MultiBugCompositionRow:
    cases = [_candidate_template_case(candidate) for candidate in candidates]
    if any(not case for case in cases):
        return _skipped(candidates, "missing_template_case")
    functions = [_simple_buggy_function(case) for case in cases]
    if any(not function for function in functions):
        return _skipped(candidates, "requires_simple_buggy_function")
    if len(set(functions)) != len(functions):
        return _skipped(candidates, "duplicate_buggy_function")

    sources = _dedupe_by_key(
        source
        for case in cases
        for source in case.get("sources", [])
        if isinstance(source, dict)
    )
    if not sources:
        return _skipped(candidates, "missing_sources")

    mutations = [
        _deepcopy_json(mutation)
        for case in cases
        for mutation in case.get("mutations", [])
        if isinstance(mutation, dict)
    ]
    if len(mutations) < len(cases):
        return _skipped(candidates, "missing_mutations")

    files: list[dict[str, Any]] = []
    wrapper_targets: list[str] = []
    wrapper_specs: list[dict[str, Any]] = []
    failing_tests: list[str] = []
    passed_tests: list[str] = []
    expected_rules: list[str] = []
    source_targets = sorted(
        {
            str(source.get("target_path", ""))
            for source in sources
            if str(source.get("target_path", ""))
        }
    )
    candidate_ids = [str(candidate.get("id", "")) for candidate in candidates]

    for index, case in enumerate(cases, start=1):
        benchmark = case.get("benchmark", {})
        if not isinstance(benchmark, dict):
            return _skipped(candidates, "benchmark_not_object")
        failing_tests.extend(
            str(test) for test in benchmark.get("failing_tests", []) if str(test)
        )
        passed_tests.extend(
            str(test) for test in benchmark.get("passed_tests", []) if str(test)
        )
        expected_rules.extend(
            str(rule) for rule in benchmark.get("expected_rule_ids", []) if str(rule)
        )
        wrapper_files: list[dict[str, str]] = []
        entry_wrapper: dict[str, str] | None = None
        function_name = functions[index - 1]
        if wrapper_depth > 0:
            source_target = _primary_source_target(case)
            source_module = _module_name_from_target(source_target)
            if not source_module:
                return _skipped(candidates, "unsupported_source_target")
            service_module = _safe_identifier(
                f"{_slug(candidate_ids[index - 1])}_multi_bug_service"
            )
            wrapper_files = _wrapper_files(
                source_module=source_module,
                function_name=function_name,
                service_module=service_module,
                wrapper_function=_safe_identifier(f"call_{function_name}"),
                wrapper_depth=wrapper_depth,
            )
            entry_wrapper = wrapper_files[0]
            wrapper_targets.extend(item["target_path"] for item in wrapper_files)
            wrapper_specs.append(
                {
                    "candidate_id": candidate_ids[index - 1],
                    "wrapped_module": source_module,
                    "wrapped_function": function_name,
                    "wrapper_module": entry_wrapper["module_name"],
                    "wrapper_function": entry_wrapper["function_name"],
                    "wrapper_modules": [item["module_name"] for item in wrapper_files],
                    "wrapper_functions": [
                        item["function_name"] for item in wrapper_files
                    ],
                    "wrapper_targets": [item["target_path"] for item in wrapper_files],
                }
            )
            files.extend(
                {
                    "target_path": wrapper_file["target_path"],
                    "content": wrapper_file["content"],
                }
                for wrapper_file in wrapper_files
            )
        for file in case.get("files", []):
            if not isinstance(file, dict):
                continue
            renamed = _renamed_test_file(file, index=index)
            if entry_wrapper is not None:
                source_module = wrapper_specs[-1]["wrapped_module"]
                transformed_content = _transform_test_import(
                    str(renamed.get("content", "")),
                    source_module=source_module,
                    function_name=function_name,
                    service_module=entry_wrapper["module_name"],
                    wrapper_function=entry_wrapper["function_name"],
                )
                if transformed_content is None:
                    return _skipped(candidates, "test_import_not_rewritable")
                renamed["content"] = transformed_content
            files.append(renamed)

    name = _multi_bug_name(candidate_ids)
    template_case = {
        "name": name,
        "repo_path": f"{name}_repo",
        "sources": sources,
        "mutations": mutations,
        "files": files,
        "benchmark": {
            "buggy_functions": _stable_unique(functions),
            "expected_rule_ids": _stable_unique(expected_rules),
            "failing_tests": _stable_unique(failing_tests),
            "passed_tests": _stable_unique(passed_tests),
            "test_args": [],
            "metadata": {
                "source": "multi_bug_recipe_composition",
                "bug_type": "multi bug",
                "bugs_per_case": len(cases),
                "composed_candidate_ids": candidate_ids,
                "composed_rules": _stable_unique(expected_rules),
                "composed_functions": _stable_unique(functions),
                "source_targets": source_targets,
                "cross_file_trace": wrapper_depth > 0,
                "wrapper_depth": wrapper_depth,
                "wrapper_targets": wrapper_targets,
                "wrapper_specs": wrapper_specs,
            },
        },
    }
    validation_errors = _template_validation_errors(template_case)
    if validation_errors:
        return MultiBugCompositionRow(
            candidate_ids=candidate_ids,
            status="skipped",
            reasons=[f"validator_error={item}" for item in validation_errors],
            functions=functions,
            rules=_stable_unique(expected_rules),
            wrapper_depth=wrapper_depth,
            wrapper_targets=wrapper_targets,
            template_case=None,
        )
    reasons = ["merged_recipe_cases"]
    if wrapper_depth > 0:
        reasons.append("wrapped_test_imports")
        reasons.append(f"wrapper_depth={wrapper_depth}")
    return MultiBugCompositionRow(
        candidate_ids=candidate_ids,
        status="composed",
        reasons=reasons,
        functions=functions,
        rules=_stable_unique(expected_rules),
        wrapper_depth=wrapper_depth,
        wrapper_targets=wrapper_targets,
        template_case=template_case,
    )


def _eligible_candidates(
    candidates: list[dict[str, Any]],
    include_rules: list[str] | None,
) -> list[dict[str, Any]]:
    include_rule_set = set(include_rules or [])
    eligible = []
    for candidate in candidates:
        rules = set(_candidate_rule_ids(candidate))
        if include_rule_set and not (include_rule_set & rules):
            continue
        if _candidate_template_case(candidate):
            eligible.append(candidate)
    return eligible


def _candidate_groups(
    candidates: list[dict[str, Any]],
    bugs_per_case: int,
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_functions: set[str] = set()
    for candidate in candidates:
        function = _simple_buggy_function(_candidate_template_case(candidate))
        if not function or function in current_functions:
            continue
        current.append(candidate)
        current_functions.add(function)
        if len(current) == bugs_per_case:
            groups.append(current)
            current = []
            current_functions = set()
    return groups


def _renamed_test_file(file: dict[str, Any], index: int) -> dict[str, Any]:
    target_path = str(file.get("target_path", ""))
    path = Path(target_path)
    if path.suffix == ".py":
        renamed = f"{path.stem}_bug{index}{path.suffix}"
        if str(path.parent) not in {"", "."}:
            renamed = (path.parent / renamed).as_posix()
    else:
        renamed = f"bug{index}_{_slug(target_path or 'test_file')}.py"
    return {
        "target_path": renamed,
        "content": str(file.get("content", "")),
    }


def _primary_source_target(case: dict[str, Any]) -> str:
    sources = case.get("sources", [])
    if isinstance(sources, list) and sources:
        first = sources[0]
        if isinstance(first, dict):
            return str(first.get("target_path", ""))
    benchmark = case.get("benchmark", {})
    if isinstance(benchmark, dict):
        metadata = benchmark.get("metadata", {})
        if isinstance(metadata, dict):
            return str(metadata.get("target_path", ""))
    return ""


def _module_name_from_target(target_path: str) -> str:
    path = PurePosixPath(target_path.replace("\\", "/"))
    if path.suffix != ".py":
        return ""
    module_parts = list(path.with_suffix("").parts)
    if not module_parts or not all(_is_identifier(part) for part in module_parts):
        return ""
    return ".".join(module_parts)


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
    content: str,
    source_module: str,
    function_name: str,
    service_module: str,
    wrapper_function: str,
) -> str | None:
    wrapper_import = f"from {service_module} import {wrapper_function} as {function_name}"
    original = f"from {source_module} import {function_name}"
    if original in content:
        return content.replace(original, wrapper_import, 1)

    lines = content.split("\n")
    pattern = re.compile(
        rf"^(\s*)from\s+{re.escape(source_module)}\s+import\s+(.+)$"
    )
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match is None:
            continue
        indent, imported_text = match.groups()
        imports = [item.strip() for item in imported_text.split(",")]
        retained = [
            item
            for item in imports
            if item
            and item != function_name
            and not item.startswith(f"{function_name} as ")
        ]
        if len(retained) == len(imports):
            continue
        replacement_lines = []
        if retained:
            replacement_lines.append(
                f"{indent}from {source_module} import {', '.join(retained)}"
            )
        replacement_lines.append(f"{indent}{wrapper_import}")
        lines[index] = "\n".join(replacement_lines)
        return "\n".join(lines)
    return None


def _dedupe_by_key(items: Any) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str, str]] = set()
    output: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("target_path", "")),
            str(item.get("raw_url", "")),
            str(item.get("owner", "")),
            str(item.get("repo", "")),
            str(item.get("source_path", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(_deepcopy_json(item))
    return output


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
    if not isinstance(functions, list) or len(functions) != 1:
        return ""
    name = str(functions[0])
    return name if _is_identifier(name) else ""


def _multi_bug_name(candidate_ids: list[str]) -> str:
    suffix = "_".join(_slug(candidate_id) for candidate_id in candidate_ids[:3])
    if len(suffix) > 90:
        suffix = suffix[:90].rstrip("_")
    return f"multi_bug_{suffix or 'case'}"


def _stable_unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _is_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value))


def _safe_identifier(value: str) -> str:
    candidate = re.sub(r"[^0-9A-Za-z_]+", "_", value).strip("_")
    if not candidate or candidate[0].isdigit():
        candidate = f"generated_{candidate}"
    return candidate


def _slug(value: Any) -> str:
    candidate = re.sub(r"[^0-9A-Za-z_]+", "_", str(value).lower()).strip("_")
    if not candidate or candidate[0].isdigit():
        candidate = f"generated_{candidate}"
    return candidate


def _skipped(candidates: list[dict[str, Any]], reason: str) -> MultiBugCompositionRow:
    cases = [_candidate_template_case(candidate) for candidate in candidates]
    functions = [
        function
        for function in (_simple_buggy_function(case) for case in cases)
        if function
    ]
    rules = [
        rule
        for candidate in candidates
        for rule in _candidate_rule_ids(candidate)
        if rule
    ]
    return MultiBugCompositionRow(
        candidate_ids=[str(candidate.get("id", "")) for candidate in candidates],
        status="skipped",
        reasons=[reason],
        functions=functions,
        rules=_stable_unique(rules),
        template_case=None,
    )


def _deepcopy_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compose recipe catalog candidates into multi-bug benchmarks."
    )
    parser.add_argument("catalog", help="Catalog JSON containing candidates.")
    parser.add_argument(
        "--include-rule",
        action="append",
        help="Only compose candidates containing this rule id. May be repeated.",
    )
    parser.add_argument(
        "--bugs-per-case",
        type=int,
        default=2,
        help="Number of independent buggy functions to merge per composed case.",
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
        default=0,
        help=(
            "Optional number of cross-file wrapper hops per buggy function. "
            "Defaults to 0 for direct-import multi-bug cases."
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

    report = compose_multi_bug_benchmarks(
        load_json(args.catalog),
        catalog_path=str(args.catalog),
        include_rules=args.include_rule,
        max_cases=args.max_cases,
        bugs_per_case=args.bugs_per_case,
        wrapper_depth=args.wrapper_depth,
    )
    payload = report.to_dict()
    json_report = json.dumps(payload, indent=2, ensure_ascii=False)
    markdown_report = render_multi_bug_composition_markdown(report)
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
