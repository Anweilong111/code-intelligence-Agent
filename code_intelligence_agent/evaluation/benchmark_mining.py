from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.judge_cluster_mining import (
    BenchmarkMiningSuggestion,
    PatchJudgeAuditRow,
    benchmark_mining_suggestions,
    patch_judge_failure_clusters,
)


@dataclass(frozen=True)
class BenchmarkTemplateSeed:
    seed_name: str
    priority: str
    benchmark_focus: str
    failure_type: str
    pattern: str
    upstream_search_queries: list[str]
    mutation_strategy: str
    test_strategy: str
    rationale: str
    evidence_count: int
    evidence_examples: list[str]
    template_case: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkMiningReport:
    source_path: str
    judged_candidate_count: int
    cluster_count: int
    suggestion_count: int
    suggestions: list[BenchmarkMiningSuggestion]
    template_seeds: list[BenchmarkTemplateSeed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "judged_candidate_count": self.judged_candidate_count,
            "cluster_count": self.cluster_count,
            "suggestion_count": self.suggestion_count,
            "suggestions": [item.to_dict() for item in self.suggestions],
            "template_seeds": [item.to_dict() for item in self.template_seeds],
            "template_seed_preview": {
                "cases": [item.template_case for item in self.template_seeds]
            },
        }


def mine_benchmark_template_seeds(
    payload: dict[str, Any],
    source_path: str = "",
    top_n: int | None = None,
) -> BenchmarkMiningReport:
    rows = _patch_judge_rows_from_payload(payload)
    clusters = patch_judge_failure_clusters(rows)
    suggestions = benchmark_mining_suggestions(clusters)
    if top_n is not None:
        suggestions = suggestions[: max(0, top_n)]
    seeds = [_template_seed_from_suggestion(item) for item in suggestions]
    return BenchmarkMiningReport(
        source_path=source_path,
        judged_candidate_count=len(rows),
        cluster_count=len(clusters),
        suggestion_count=len(suggestions),
        suggestions=suggestions,
        template_seeds=seeds,
    )


def render_benchmark_mining_markdown(report: BenchmarkMiningReport) -> str:
    lines = [
        "# Benchmark Mining",
        "",
        f"- Source: `{report.source_path or '<memory>'}`",
        f"- Judged Candidates: {report.judged_candidate_count}",
        f"- Failure Clusters: {report.cluster_count}",
        f"- Template Seeds: {len(report.template_seeds)}",
        "",
        "| Priority | Focus | Failure Type | Pattern | Mutation Strategy | Test Strategy | Evidence | Examples |",
        "| --- | --- | --- | --- | --- | --- | ---: | --- |",
    ]
    for seed in report.template_seeds:
        lines.append(
            "| "
            f"{_markdown_cell(seed.priority)} | "
            f"{_markdown_cell(seed.benchmark_focus)} | "
            f"{_markdown_cell(seed.failure_type)} | "
            f"{_markdown_cell(seed.pattern)} | "
            f"{_markdown_cell(seed.mutation_strategy)} | "
            f"{_markdown_cell(seed.test_strategy)} | "
            f"{seed.evidence_count} | "
            f"{_markdown_cell(', '.join(seed.evidence_examples))} |"
        )
    return "\n".join(lines)


def load_mining_payload(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _patch_judge_rows_from_payload(payload: dict[str, Any]) -> list[PatchJudgeAuditRow]:
    report = _extract_benchmark_report(payload)
    rows: list[PatchJudgeAuditRow] = []
    for case in report.get("cases", []):
        if not isinstance(case, dict):
            continue
        case_name = str(case.get("case_name", case.get("name", "")))
        for result in case.get("beam_search_results", []):
            if not isinstance(result, dict):
                continue
            judgment = result.get("patch_judgment", {})
            if not isinstance(judgment, dict) or "score" not in judgment:
                continue
            raw_score = float(judgment.get("score", 0.0))
            calibrated_score = float(
                judgment.get("calibrated_score", raw_score) or 0.0
            )
            reasons = judgment.get("calibration_reasons", [])
            if not isinstance(reasons, list):
                reasons = []
            rows.append(
                PatchJudgeAuditRow(
                    case=case_name,
                    rank=int(result.get("rank", 0)),
                    candidate_id=str(result.get("candidate_id", "")),
                    success=bool(result.get("success", False)),
                    failure_type=str(result.get("failure_type", "")),
                    bucket=str(result.get("retention_bucket", "")),
                    raw_score=raw_score,
                    calibrated_score=calibrated_score,
                    delta=calibrated_score - raw_score,
                    agreement=str(judgment.get("agreement", "")),
                    verdict=str(judgment.get("verdict", "")),
                    reason_list=[str(reason) for reason in reasons],
                    reasons=", ".join(str(reason) for reason in reasons),
                )
            )
    return rows


def _extract_benchmark_report(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("benchmark_report"), dict):
        return payload["benchmark_report"]
    if isinstance(payload.get("summary"), dict) and isinstance(payload.get("cases"), list):
        return payload
    raise ValueError("Artifact must contain benchmark_report or summary/cases.")


def _template_seed_from_suggestion(
    suggestion: BenchmarkMiningSuggestion,
) -> BenchmarkTemplateSeed:
    seed_name = f"judge_mining_{_slug(suggestion.failure_type)}_{_slug(suggestion.pattern)}"
    target_path = f"{seed_name}.py"
    test_name = f"test_{seed_name}"
    mutation_strategy = _mutation_strategy(suggestion)
    test_strategy = _test_strategy(suggestion)
    template_case = {
        "name": seed_name,
        "repo_path": f"{seed_name}_repo",
        "sources": [
            {
                "owner": "TODO_owner",
                "repo": "TODO_repo",
                "ref": "TODO_commit_or_tag",
                "source_path": f"TODO/{target_path}",
                "target_path": target_path,
            }
        ],
        "mutations": [
            {
                "target_path": target_path,
                "find": "TODO_original_safe_code",
                "replace": "TODO_buggy_mutation",
                "count": 1,
                "description": mutation_strategy,
            }
        ],
        "files": [
            {
                "target_path": f"test_{seed_name}.py",
                "content": (
                    f"# TODO: implement {test_name} for {suggestion.benchmark_focus}.\n"
                    "# The test should fail before repair and pass after a minimal patch.\n"
                ),
            }
        ],
        "benchmark": {
            "buggy_functions": ["TODO_function"],
            "expected_rule_ids": [_expected_rule_id(suggestion.failure_type)],
            "failing_tests": [test_name],
            "passed_tests": [],
            "test_args": [],
            "metadata": {
                "source": "github_raw_judge_cluster_seed",
                "seed_status": "needs_human_source_selection",
                "bug_type": _bug_type(suggestion.failure_type),
                "mining_priority": suggestion.priority,
                "mining_focus": suggestion.benchmark_focus,
                "mining_pattern": suggestion.pattern,
                "mining_failure_type": suggestion.failure_type,
                "evidence_examples": suggestion.examples,
            },
        },
    }
    return BenchmarkTemplateSeed(
        seed_name=seed_name,
        priority=suggestion.priority,
        benchmark_focus=suggestion.benchmark_focus,
        failure_type=suggestion.failure_type,
        pattern=suggestion.pattern,
        upstream_search_queries=_search_queries(suggestion),
        mutation_strategy=mutation_strategy,
        test_strategy=test_strategy,
        rationale=suggestion.rationale,
        evidence_count=suggestion.evidence_count,
        evidence_examples=suggestion.examples,
        template_case=template_case,
    )


def _search_queries(suggestion: BenchmarkMiningSuggestion) -> list[str]:
    base = [
        f"github python {suggestion.failure_type} regression test",
        f"github python {suggestion.benchmark_focus} bug fix",
    ]
    if suggestion.failure_type in {"syntax_error", "patch_apply_error"}:
        base.append("github python small function boundary guard regression")
    elif suggestion.failure_type == "timeout":
        base.append("github python infinite loop timeout regression test")
    elif suggestion.failure_type == "test_failure":
        base.append("github python semantic regression near miss unit test")
    else:
        base.append(f"github python {suggestion.pattern} repair benchmark")
    return base


def _mutation_strategy(suggestion: BenchmarkMiningSuggestion) -> str:
    failure_type = suggestion.failure_type
    if failure_type in {"syntax_error", "patch_apply_error", "import_error"}:
        return (
            "Select a compact real function whose obvious-looking repair can be "
            "made syntactically invalid; keep sandbox evidence as the final arbiter."
        )
    if failure_type == "timeout":
        return (
            "Mutate loop progress or recursion termination so static shape looks "
            "reasonable but sandbox execution times out."
        )
    if failure_type == "test_failure":
        return (
            "Create a near-miss semantic mutation where a plausible patch satisfies "
            "one assertion but violates the documented contract."
        )
    if failure_type in {"type_error", "attribute_error", "runtime_error"}:
        return (
            "Mutate a value, attribute, or API contract so a low-diff repair remains "
            "plausible but runtime traceback distinguishes the correct fix."
        )
    return (
        "Create a GitHub raw mutation matching the judge/evidence disagreement "
        "pattern and preserve execution evidence in the benchmark."
    )


def _test_strategy(suggestion: BenchmarkMiningSuggestion) -> str:
    if suggestion.failure_type == "timeout":
        return "Use a focused pytest that exercises the non-terminating path with a sandbox timeout."
    if suggestion.failure_type == "test_failure":
        return "Pair a passing smoke assertion with a failing contract assertion to expose near-miss patches."
    if suggestion.failure_type in {"syntax_error", "patch_apply_error", "import_error"}:
        return "Use a minimal failing test plus import/collection coverage so non-executable patches are penalized."
    return "Use a failing pytest that exposes the traceback and at least one passed test for calibration."


def _expected_rule_id(failure_type: str) -> str:
    if failure_type == "timeout":
        return "missing_loop_progress_guard"
    if failure_type == "test_failure":
        return "semantic_contract_regression"
    if failure_type in {"type_error", "attribute_error", "runtime_error"}:
        return "runtime_contract_violation"
    if failure_type in {"syntax_error", "patch_apply_error", "import_error"}:
        return "minimal_executable_patch_required"
    return "judge_evidence_disagreement"


def _bug_type(failure_type: str) -> str:
    return {
        "syntax_error": "non-executable repair decoy",
        "patch_apply_error": "non-applicable repair decoy",
        "import_error": "import contract error",
        "timeout": "termination error",
        "test_failure": "semantic contract error",
        "type_error": "type error",
        "attribute_error": "attribute error",
        "runtime_error": "runtime error",
    }.get(failure_type, "judge calibration error")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "unknown"


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Mine patch-judge failure clusters into benchmark template seeds."
        )
    )
    parser.add_argument("artifact", help="suite.json or benchmark_report.json")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=None,
        help="Optional maximum number of mining suggestions to emit.",
    )
    parser.add_argument("--output-json", help="Optional path for JSON output.")
    parser.add_argument("--output-markdown", help="Optional path for Markdown output.")
    parser.add_argument(
        "--output-template-seeds",
        help="Optional path for a template-like JSON containing only seed cases.",
    )
    args = parser.parse_args()

    payload = load_mining_payload(args.artifact)
    report = mine_benchmark_template_seeds(
        payload,
        source_path=str(args.artifact),
        top_n=args.top_n,
    )
    json_report = json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
    markdown_report = render_benchmark_mining_markdown(report)
    if args.output_json:
        Path(args.output_json).write_text(json_report, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown_report, encoding="utf-8")
    if args.output_template_seeds:
        Path(args.output_template_seeds).write_text(
            json.dumps(
                {"cases": [item.template_case for item in report.template_seeds]},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    if args.format == "markdown":
        print(markdown_report)
    else:
        print(json_report)


if __name__ == "__main__":
    main()
