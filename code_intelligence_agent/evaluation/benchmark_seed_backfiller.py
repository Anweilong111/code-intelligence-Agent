from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.benchmark_seed_realizer import (
    SeedRealization,
    realize_benchmark_template_seeds,
)
from code_intelligence_agent.evaluation.benchmark_validator import (
    BenchmarkValidator,
)


@dataclass(frozen=True)
class SeedBackfillRow:
    seed_name: str
    status: str
    candidate_id: str
    realization_score: float
    reasons: list[str]
    audit_errors: list[str]
    audit_warnings: list[str]
    template_case: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SeedBackfillReport:
    seed_path: str
    catalog_path: str
    seed_count: int
    candidate_count: int
    realized_count: int
    completed_count: int
    incomplete_count: int
    unmatched_count: int
    rows: list[SeedBackfillRow]

    def to_dict(self) -> dict[str, Any]:
        realized_cases = [
            row.template_case
            for row in self.rows
            if row.template_case is not None
        ]
        completed_cases = [
            row.template_case
            for row in self.rows
            if row.status == "completed" and row.template_case is not None
        ]
        return {
            "seed_path": self.seed_path,
            "catalog_path": self.catalog_path,
            "seed_count": self.seed_count,
            "candidate_count": self.candidate_count,
            "realized_count": self.realized_count,
            "completed_count": self.completed_count,
            "incomplete_count": self.incomplete_count,
            "unmatched_count": self.unmatched_count,
            "rows": [row.to_dict() for row in self.rows],
            "realized_template": {"cases": realized_cases},
            "completed_template": {"cases": completed_cases},
        }


def backfill_benchmark_template_seeds(
    seed_payload: dict[str, Any],
    catalog_payload: dict[str, Any],
    seed_path: str = "",
    catalog_path: str = "",
) -> SeedBackfillReport:
    realization_report = realize_benchmark_template_seeds(
        seed_payload,
        catalog_payload,
        seed_path=seed_path,
        catalog_path=catalog_path,
    )
    rows = [_row_from_realization(item) for item in realization_report.realizations]
    completed_count = sum(1 for row in rows if row.status == "completed")
    incomplete_count = sum(1 for row in rows if row.status == "incomplete")
    unmatched_count = sum(1 for row in rows if row.status == "unmatched")
    return SeedBackfillReport(
        seed_path=seed_path,
        catalog_path=catalog_path,
        seed_count=realization_report.seed_count,
        candidate_count=realization_report.candidate_count,
        realized_count=realization_report.realized_count,
        completed_count=completed_count,
        incomplete_count=incomplete_count,
        unmatched_count=unmatched_count,
        rows=rows,
    )


def render_seed_backfill_markdown(report: SeedBackfillReport) -> str:
    lines = [
        "# Benchmark Seed Backfill",
        "",
        f"- Seeds: {report.seed_count}",
        f"- Candidates: {report.candidate_count}",
        f"- Realized: {report.realized_count}",
        f"- Completed: {report.completed_count}",
        f"- Incomplete: {report.incomplete_count}",
        f"- Unmatched: {report.unmatched_count}",
        "",
        "| Seed | Status | Candidate | Score | Reasons | Errors | Warnings |",
        "| --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for row in report.rows:
        lines.append(
            "| "
            f"{_markdown_cell(row.seed_name)} | "
            f"{_markdown_cell(row.status)} | "
            f"{_markdown_cell(row.candidate_id)} | "
            f"{row.realization_score:.2f} | "
            f"{_markdown_cell(', '.join(row.reasons))} | "
            f"{_markdown_cell(', '.join(row.audit_errors))} | "
            f"{_markdown_cell(', '.join(row.audit_warnings))} |"
        )
    return "\n".join(lines)


def _row_from_realization(realization: SeedRealization) -> SeedBackfillRow:
    if realization.template_case is None:
        return SeedBackfillRow(
            seed_name=realization.seed_name,
            status="unmatched",
            candidate_id=realization.candidate_id,
            realization_score=realization.score,
            reasons=[realization.unmatched_reason] if realization.unmatched_reason else [],
            audit_errors=["unmatched_seed"],
            audit_warnings=[],
            template_case=None,
        )
    audit_errors, audit_warnings = _audit_completed_case(realization.template_case)
    status = "completed" if not audit_errors else "incomplete"
    return SeedBackfillRow(
        seed_name=realization.seed_name,
        status=status,
        candidate_id=realization.candidate_id,
        realization_score=realization.score,
        reasons=realization.reasons,
        audit_errors=audit_errors,
        audit_warnings=audit_warnings,
        template_case=realization.template_case,
    )


def _audit_completed_case(case: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if _contains_todo_placeholder(case):
        errors.append("unresolved_todo_placeholder")
    if not _non_empty_list(case.get("sources")):
        errors.append("missing_sources")
    if not _non_empty_list(case.get("mutations")):
        errors.append("missing_mutations")
    if not _non_empty_list(case.get("files")):
        errors.append("missing_test_files")
    if not _has_pytest_oracle(case):
        errors.append("missing_pytest_oracle")
    if not _has_ground_truth(case):
        errors.append("missing_ground_truth")
    if not _has_described_mutation(case):
        warnings.append("missing_mutation_description")
    if not _has_passed_tests(case):
        warnings.append("no_passed_tests_declared")
    validation_errors, validation_warnings = _validate_case_as_template(case)
    errors.extend(validation_errors)
    warnings.extend(validation_warnings)
    return sorted(set(errors)), sorted(set(warnings))


def _validate_case_as_template(case: dict[str, Any]) -> tuple[list[str], list[str]]:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "template.json"
        path.write_text(
            json.dumps({"cases": [case]}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        report = BenchmarkValidator().validate_template(path)
    errors = [
        f"validator_error:{issue.location}:{issue.message}"
        for issue in report.errors
    ]
    warnings = [
        f"validator_warning:{issue.location}:{issue.message}"
        for issue in report.warnings
    ]
    return errors, warnings


def _contains_todo_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return "TODO" in value
    if isinstance(value, dict):
        return any(_contains_todo_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_todo_placeholder(item) for item in value)
    return False


def _has_pytest_oracle(case: dict[str, Any]) -> bool:
    files = case.get("files", [])
    if not isinstance(files, list):
        return False
    for file in files:
        if not isinstance(file, dict):
            continue
        target = str(file.get("target_path", ""))
        content = str(file.get("content", ""))
        if target.endswith(".py") and "def test_" in content:
            return True
    return False


def _has_ground_truth(case: dict[str, Any]) -> bool:
    benchmark = case.get("benchmark", {})
    if not isinstance(benchmark, dict):
        return False
    return bool(
        _non_empty_list(benchmark.get("buggy_functions"))
        and _non_empty_list(benchmark.get("expected_rule_ids"))
        and _non_empty_list(benchmark.get("failing_tests"))
    )


def _has_described_mutation(case: dict[str, Any]) -> bool:
    mutations = case.get("mutations", [])
    if not isinstance(mutations, list):
        return False
    return any(
        isinstance(mutation, dict) and bool(mutation.get("description"))
        for mutation in mutations
    )


def _has_passed_tests(case: dict[str, Any]) -> bool:
    benchmark = case.get("benchmark", {})
    if not isinstance(benchmark, dict):
        return False
    return _non_empty_list(benchmark.get("passed_tests"))


def _non_empty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill benchmark template seeds with concrete catalog candidates "
            "and emit only completion-audited templates."
        )
    )
    parser.add_argument("seeds", help="template_seeds.json or benchmark_mining.json")
    parser.add_argument("catalog", help="Seed realization candidate catalog JSON")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format.",
    )
    parser.add_argument("--output-json", help="Optional backfill report JSON path.")
    parser.add_argument("--output-markdown", help="Optional markdown report path.")
    parser.add_argument(
        "--output-template",
        help="Optional completed benchmark template JSON path.",
    )
    parser.add_argument(
        "--output-realized-template",
        help="Optional realized template JSON path including incomplete rows.",
    )
    parser.add_argument(
        "--fail-on-incomplete",
        action="store_true",
        help="Exit with status 1 if any seed is unmatched or incomplete.",
    )
    args = parser.parse_args()

    report = backfill_benchmark_template_seeds(
        load_json(args.seeds),
        load_json(args.catalog),
        seed_path=str(args.seeds),
        catalog_path=str(args.catalog),
    )
    payload = report.to_dict()
    json_report = json.dumps(payload, indent=2, ensure_ascii=False)
    markdown_report = render_seed_backfill_markdown(report)
    if args.output_json:
        Path(args.output_json).write_text(json_report, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown_report, encoding="utf-8")
    if args.output_template:
        Path(args.output_template).write_text(
            json.dumps(payload["completed_template"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if args.output_realized_template:
        Path(args.output_realized_template).write_text(
            json.dumps(payload["realized_template"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if args.format == "markdown":
        print(markdown_report)
    else:
        print(json_report)
    if args.fail_on_incomplete and (
        report.incomplete_count or report.unmatched_count
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
