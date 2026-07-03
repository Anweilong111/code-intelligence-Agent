from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CatalogBuildReport:
    source_path: str
    case_count: int
    candidate_count: int
    rule_counts: dict[str, int]
    bug_type_counts: dict[str, int]
    candidates: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "case_count": self.case_count,
            "candidate_count": self.candidate_count,
            "rule_counts": self.rule_counts,
            "bug_type_counts": self.bug_type_counts,
            "candidates": [asdict_like(candidate) for candidate in self.candidates],
            "catalog": {
                "candidates": [asdict_like(candidate) for candidate in self.candidates]
            },
        }


def build_seed_realization_catalog(
    template_payload: dict[str, Any],
    source_path: str = "",
) -> CatalogBuildReport:
    cases = _extract_template_cases(template_payload)
    candidates = [_candidate_from_case(case) for case in cases]
    rule_counts: Counter[str] = Counter()
    bug_type_counts: Counter[str] = Counter()
    for case in cases:
        benchmark = case.get("benchmark", {})
        if not isinstance(benchmark, dict):
            continue
        for rule_id in _string_list(benchmark.get("expected_rule_ids")):
            rule_counts[rule_id] += 1
        bug_type = _metadata(case).get("bug_type")
        if bug_type:
            bug_type_counts[str(bug_type)] += 1
    return CatalogBuildReport(
        source_path=source_path,
        case_count=len(cases),
        candidate_count=len(candidates),
        rule_counts=dict(sorted(rule_counts.items())),
        bug_type_counts=dict(sorted(bug_type_counts.items())),
        candidates=candidates,
    )


def render_catalog_markdown(report: CatalogBuildReport) -> str:
    lines = [
        "# Seed Realization Catalog",
        "",
        f"- Source: `{report.source_path or '<memory>'}`",
        f"- Cases: {report.case_count}",
        f"- Candidates: {report.candidate_count}",
        f"- Rules: {_format_counts(report.rule_counts)}",
        f"- Bug Types: {_format_counts(report.bug_type_counts)}",
        "",
        "| Candidate | Rules | Bug Type | Failure Tags | Focus Tags | Pattern Tags |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for candidate in report.candidates:
        case = candidate.get("template_case", {})
        benchmark = case.get("benchmark", {}) if isinstance(case, dict) else {}
        metadata = benchmark.get("metadata", {}) if isinstance(benchmark, dict) else {}
        lines.append(
            "| "
            f"{_markdown_cell(candidate.get('id', ''))} | "
            f"{_markdown_cell(', '.join(candidate.get('rule_ids', [])))} | "
            f"{_markdown_cell(metadata.get('bug_type', ''))} | "
            f"{_markdown_cell(', '.join(candidate.get('failure_types', [])))} | "
            f"{_markdown_cell(', '.join(candidate.get('benchmark_focuses', [])))} | "
            f"{_markdown_cell(', '.join(candidate.get('patterns', [])))} |"
        )
    return "\n".join(lines)


def _candidate_from_case(case: dict[str, Any]) -> dict[str, Any]:
    rule_ids = _expected_rules(case)
    bug_type = str(_metadata(case).get("bug_type", ""))
    failure_types = _infer_failure_types(rule_ids, bug_type)
    focuses = _infer_benchmark_focuses(rule_ids, bug_type, failure_types)
    patterns = _infer_patterns(failure_types, rule_ids)
    candidate = {
        "id": str(case.get("name", "")),
        "rule_ids": rule_ids,
        "bug_type": bug_type,
        "failure_types": failure_types,
        "benchmark_focuses": focuses,
        "patterns": patterns,
        "source_summary": _source_summary(case),
        "mutation_summary": _mutation_summary(case),
        "template_case": asdict_like(case),
    }
    return candidate


def _infer_failure_types(rule_ids: list[str], bug_type: str) -> list[str]:
    inferred = {"syntax_error", "patch_apply_error", "test_failure"}
    rule_set = set(rule_ids)
    if "stringified_numeric_value" in rule_set:
        inferred.update({"type_error", "runtime_error"})
    if "inplace_api_return_value" in rule_set:
        inferred.update({"type_error", "attribute_error", "runtime_error"})
    if "missing_len_zero_guard" in rule_set:
        inferred.update({"runtime_error", "zero_division_error"})
    if "possible_index_overrun" in rule_set:
        inferred.update({"runtime_error", "index_error"})
    if "dict_missing_key_guard" in rule_set:
        inferred.update({"runtime_error", "key_error"})
    if "broad_exception_pass" in rule_set:
        inferred.update({"runtime_error", "execution_error"})
    if "mutable_default_arg" in rule_set:
        inferred.add("state_leakage")
    if "always_true_len_check" in rule_set:
        inferred.update({"runtime_error", "condition_error"})
    if "inverted_empty_guard" in rule_set:
        inferred.update({"runtime_error", "condition_error"})
    if "enumerate_start_zero_counter" in rule_set:
        inferred.update({"runtime_error", "off_by_one_error"})
    normalized_bug_type = bug_type.lower()
    if "type" in normalized_bug_type:
        inferred.add("type_error")
    if "api" in normalized_bug_type:
        inferred.update({"type_error", "attribute_error"})
    if "boundary" in normalized_bug_type:
        inferred.update({"runtime_error", "index_error"})
    if "zero division" in normalized_bug_type:
        inferred.update({"runtime_error", "zero_division_error"})
    if "key" in normalized_bug_type:
        inferred.update({"runtime_error", "key_error"})
    if "exception" in normalized_bug_type:
        inferred.update({"runtime_error", "execution_error"})
    return sorted(inferred)


def _infer_benchmark_focuses(
    rule_ids: list[str],
    bug_type: str,
    failure_types: list[str],
) -> list[str]:
    rule_set = set(rule_ids)
    focuses = {
        "execution-evidence calibration",
        "judge false-positive hardening",
    }
    if "test_failure" in failure_types:
        focuses.add("near-miss semantic repair")
    if any(
        failure_type in {"type_error", "attribute_error", "runtime_error"}
        for failure_type in failure_types
    ):
        focuses.add("runtime traceback calibration")
    normalized_bug_type = bug_type.lower()
    if "mutable_default_arg" in rule_set or "state" in normalized_bug_type:
        focuses.add("stateful regression repair")
    if "inverted_empty_guard" in rule_set or "condition" in normalized_bug_type:
        focuses.add("condition-guard calibration")
    if "dict_missing_key_guard" in rule_set or "key" in normalized_bug_type:
        focuses.update(
            {
                "data-access guard calibration",
                "mapping default semantics",
            }
        )
    return sorted(focuses)


def _infer_patterns(
    failure_types: list[str],
    rule_ids: list[str] | None = None,
) -> list[str]:
    patterns = {"capped_by_execution_evidence"}
    patterns.update(f"failure_type={failure_type}" for failure_type in failure_types)
    if "dict_missing_key_guard" in set(rule_ids or []):
        patterns.add("missing_mapping_key_default")
    return sorted(patterns)


def _source_summary(case: dict[str, Any]) -> dict[str, Any]:
    sources = case.get("sources", [])
    if not isinstance(sources, list):
        sources = []
    return {
        "source_count": len(sources),
        "targets": [
            str(source.get("target_path", ""))
            for source in sources
            if isinstance(source, dict)
        ],
        "upstreams": [
            "/".join(
                part
                for part in [
                    str(source.get("owner", "")),
                    str(source.get("repo", "")),
                ]
                if part
            )
            for source in sources
            if isinstance(source, dict)
        ],
    }


def _mutation_summary(case: dict[str, Any]) -> dict[str, Any]:
    mutations = case.get("mutations", [])
    if not isinstance(mutations, list):
        mutations = []
    return {
        "mutation_count": len(mutations),
        "descriptions": [
            str(mutation.get("description", ""))
            for mutation in mutations
            if isinstance(mutation, dict) and mutation.get("description")
        ],
    }


def _extract_template_cases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    cases = payload.get("cases", [])
    if not isinstance(cases, list):
        return []
    return [case for case in cases if isinstance(case, dict)]


def _expected_rules(case: dict[str, Any]) -> list[str]:
    benchmark = case.get("benchmark", {})
    if not isinstance(benchmark, dict):
        return []
    return sorted(_string_list(benchmark.get("expected_rule_ids")))


def _metadata(case: dict[str, Any]) -> dict[str, Any]:
    benchmark = case.get("benchmark", {})
    if not isinstance(benchmark, dict):
        return {}
    metadata = benchmark.get("metadata", {})
    return metadata if isinstance(metadata, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def asdict_like(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _format_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{name}={count}" for name, count in counts.items())


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a seed realization catalog from existing benchmark templates."
        )
    )
    parser.add_argument("template", help="Benchmark template JSON")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format.",
    )
    parser.add_argument("--output-json", help="Optional full report JSON path.")
    parser.add_argument("--output-markdown", help="Optional markdown report path.")
    parser.add_argument(
        "--output-catalog",
        help="Optional catalog JSON path containing only candidates.",
    )
    args = parser.parse_args()

    template_payload = load_json(args.template)
    report = build_seed_realization_catalog(
        template_payload,
        source_path=str(args.template),
    )
    payload = report.to_dict()
    json_report = json.dumps(payload, indent=2, ensure_ascii=False)
    markdown_report = render_catalog_markdown(report)
    if args.output_json:
        Path(args.output_json).write_text(json_report, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown_report, encoding="utf-8")
    if args.output_catalog:
        Path(args.output_catalog).write_text(
            json.dumps(payload["catalog"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if args.format == "markdown":
        print(markdown_report)
    else:
        print(json_report)


if __name__ == "__main__":
    main()
