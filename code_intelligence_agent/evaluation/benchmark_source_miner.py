from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.benchmark_recipe_generator import (
    SUPPORTED_RECIPES,
    generate_benchmark_recipes,
)
from code_intelligence_agent.evaluation.benchmark_provenance import (
    benchmark_provenance_summary,
)


@dataclass(frozen=True)
class SourceMiningRow:
    source: dict[str, Any]
    status: str
    generated_count: int
    rule_ids: list[str]
    recipe_results: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceMiningReport:
    source_path: str
    source_count: int
    recipe_count: int
    recipes: list[str]
    generated_source_count: int
    generated_count: int
    rule_counts: dict[str, int]
    bug_type_counts: dict[str, int]
    quality_summary: dict[str, Any]
    sources: list[SourceMiningRow]
    candidates: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        template_cases = [candidate["template_case"] for candidate in self.candidates]
        source_candidates = [
            row.source for row in self.sources if row.generated_count > 0
        ]
        return {
            "source_path": self.source_path,
            "source_count": self.source_count,
            "recipe_count": self.recipe_count,
            "recipes": self.recipes,
            "generated_source_count": self.generated_source_count,
            "generated_count": self.generated_count,
            "rule_counts": self.rule_counts,
            "bug_type_counts": self.bug_type_counts,
            "quality_summary": self.quality_summary,
            "sources": [row.to_dict() for row in self.sources],
            "source_candidates": source_candidates,
            "candidates": self.candidates,
            "catalog": {"candidates": self.candidates},
            "template": {"cases": template_cases},
            "sources_payload": {"sources": source_candidates},
        }


def mine_recipe_sources(
    payload: dict[str, Any],
    recipes: list[str] | None = None,
    source_path: str = "",
    source_cache_dir: str | Path | None = None,
) -> SourceMiningReport:
    selected_recipes = sorted(recipes or SUPPORTED_RECIPES)
    unsupported = sorted(set(selected_recipes) - SUPPORTED_RECIPES)
    if unsupported:
        raise ValueError(f"Unsupported recipes: {', '.join(unsupported)}")
    source_rows = [_initial_row(source) for source in _extract_source_dicts(payload)]
    candidates: list[dict[str, Any]] = []
    rule_counts: Counter[str] = Counter()
    bug_type_counts: Counter[str] = Counter()

    for recipe in selected_recipes:
        report = generate_benchmark_recipes(
            payload,
            recipe=recipe,
            source_path=source_path,
            source_cache_dir=source_cache_dir,
        )
        for index, result in enumerate(report.results):
            if index >= len(source_rows):
                continue
            row = source_rows[index]
            candidate_ids = [candidate.get("id", "") for candidate in result.candidates]
            recipe_rule_ids = sorted(
                {
                    rule_id
                    for candidate in result.candidates
                    for rule_id in candidate.get("rule_ids", [])
                }
            )
            row["recipe_results"].append(
                {
                    "recipe": recipe,
                    "status": result.status,
                    "generated_count": result.generated_count,
                    "candidate_ids": candidate_ids,
                    "rule_ids": recipe_rule_ids,
                    "reasons": list(result.reasons),
                }
            )
            row["generated_count"] += result.generated_count
            row["rule_ids"].update(recipe_rule_ids)
            for candidate in result.candidates:
                candidates.append(candidate)
                for rule_id in candidate.get("rule_ids", []):
                    rule_counts[str(rule_id)] += 1
                bug_type = candidate.get("bug_type")
                if bug_type:
                    bug_type_counts[str(bug_type)] += 1

    rows = [
        SourceMiningRow(
            source=row["source"],
            status="generated" if row["generated_count"] else _row_status(row),
            generated_count=row["generated_count"],
            rule_ids=sorted(row["rule_ids"]),
            recipe_results=row["recipe_results"],
        )
        for row in source_rows
    ]
    quality_summary = _source_mining_quality_summary(
        candidates=candidates,
        rows=rows,
        source_count=len(rows),
        recipe_count=len(selected_recipes),
        rule_counts=rule_counts,
        bug_type_counts=bug_type_counts,
    )
    return SourceMiningReport(
        source_path=source_path,
        source_count=len(rows),
        recipe_count=len(selected_recipes),
        recipes=selected_recipes,
        generated_source_count=sum(1 for row in rows if row.generated_count > 0),
        generated_count=len(candidates),
        rule_counts=dict(sorted(rule_counts.items())),
        bug_type_counts=dict(sorted(bug_type_counts.items())),
        quality_summary=quality_summary,
        sources=rows,
        candidates=candidates,
    )


def render_source_mining_markdown(report: SourceMiningReport) -> str:
    lines = [
        "# Benchmark Source Mining",
        "",
        f"- Source: `{report.source_path or '<memory>'}`",
        f"- Input Sources: {report.source_count}",
        f"- Recipes: {', '.join(report.recipes)}",
        f"- Candidate Sources: {report.generated_source_count}",
        f"- Generated Candidates: {report.generated_count}",
        f"- Rules: {_format_counts(report.rule_counts)}",
        f"- Bug Types: {_format_counts(report.bug_type_counts)}",
        f"- Quality Score: {float(report.quality_summary.get('quality_score', 0.0)):.3f}",
        (
            "- Provenance: "
            f"case={float(report.quality_summary.get('case_provenance_coverage', 0.0)):.3f}, "
            f"sha256={float(report.quality_summary.get('source_sha256_coverage', 0.0)):.3f}, "
            f"stable_ref={float(report.quality_summary.get('stable_ref_coverage', 0.0)):.3f}, "
            f"license={float(report.quality_summary.get('license_coverage', 0.0)):.3f}, "
            f"leakage={float(report.quality_summary.get('leakage_risk_score', 0.0)):.3f}"
        ),
        "",
        "| Target | Status | Generated | Rules | Recipes | Reasons |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    for row in report.sources:
        generated_recipes = [
            item["recipe"]
            for item in row.recipe_results
            if item.get("generated_count", 0) > 0
        ]
        reasons = [
            f"{item['recipe']}:{','.join(item.get('reasons', []))}"
            for item in row.recipe_results
            if item.get("reasons")
        ]
        lines.append(
            "| "
            f"{_markdown_cell(row.source.get('target_path', ''))} | "
            f"{_markdown_cell(row.status)} | "
            f"{row.generated_count} | "
            f"{_markdown_cell(', '.join(row.rule_ids))} | "
            f"{_markdown_cell(', '.join(generated_recipes))} | "
            f"{_markdown_cell('; '.join(reasons))} |"
        )
    return "\n".join(lines)


def _initial_row(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": _source_to_dict(source),
        "generated_count": 0,
        "rule_ids": set(),
        "recipe_results": [],
    }


def _source_mining_quality_summary(
    *,
    candidates: list[dict[str, Any]],
    rows: list[SourceMiningRow],
    source_count: int,
    recipe_count: int,
    rule_counts: Counter[str],
    bug_type_counts: Counter[str],
) -> dict[str, Any]:
    template_cases = [
        candidate["template_case"]
        for candidate in candidates
        if isinstance(candidate.get("template_case"), dict)
    ]
    provenance = (
        benchmark_provenance_summary(template_cases)
        if template_cases
        else {
            "case_count": 0,
            "source_ref_count": 0,
            "source_sha256_present_count": 0,
            "case_provenance_coverage": 0.0,
            "source_sha256_coverage": 0.0,
            "stable_ref_coverage": 0.0,
            "license_coverage": 0.0,
            "leakage_risk_score": 0.0,
            "risk_level": "low",
        }
    )
    generated_source_count = sum(1 for row in rows if row.generated_count > 0)
    source_hit_rate = _ratio(generated_source_count, source_count)
    candidate_density = _ratio(len(candidates), source_count * recipe_count)
    rule_diversity = len(rule_counts)
    bug_type_diversity = len(bug_type_counts)
    source_group_count = int(provenance.get("source_group_count", 0))
    source_ref_count = int(provenance.get("source_ref_count", 0))
    source_sha_present = int(provenance.get("source_sha256_present_count", 0))
    quality_terms = [
        source_hit_rate,
        min(1.0, candidate_density),
        min(1.0, rule_diversity / 3.0),
        min(1.0, bug_type_diversity / 3.0),
        min(1.0, source_group_count / 2.0) if source_group_count else 0.0,
        float(provenance.get("case_provenance_coverage", 0.0)),
        (
            float(provenance.get("stable_ref_coverage", 0.0))
            if source_ref_count
            else 1.0
        ),
        (
            float(provenance.get("source_sha256_coverage", 0.0))
            if source_sha_present
            else 1.0
        ),
        float(provenance.get("license_coverage", 0.0)),
        1.0 - float(provenance.get("leakage_risk_score", 0.0)),
    ]
    quality_score = round(sum(quality_terms) / len(quality_terms), 4)
    summary = {
        "candidate_count": len(candidates),
        "template_case_count": len(template_cases),
        "generated_source_count": generated_source_count,
        "source_hit_rate": source_hit_rate,
        "candidate_density": candidate_density,
        "rule_diversity_count": rule_diversity,
        "bug_type_diversity_count": bug_type_diversity,
        "source_group_count": source_group_count,
        "quality_score": quality_score,
        "ready_for_benchmark": bool(
            template_cases
            and float(provenance.get("case_provenance_coverage", 0.0)) >= 0.95
            and float(provenance.get("leakage_risk_score", 1.0)) <= 0.30
        ),
    }
    for key in (
        "case_provenance_coverage",
        "source_sha256_coverage",
        "stable_ref_coverage",
        "license_coverage",
        "materialized_mutation_coverage",
        "duplicate_signature_count",
        "max_source_file_case_share",
        "leakage_risk_score",
        "risk_level",
    ):
        summary[key] = provenance.get(key)
    return summary


def _extract_source_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sources = payload.get("sources", [])
    if not isinstance(sources, list):
        return []
    return [source for source in sources if isinstance(source, dict)]


def _source_to_dict(source: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "target_path",
        "raw_url",
        "owner",
        "repo",
        "ref",
        "source_path",
        "sha256",
        "license",
    ]
    return {key: source[key] for key in keys if source.get(key)}


def _row_status(row: dict[str, Any]) -> str:
    statuses = {item.get("status") for item in row["recipe_results"]}
    if statuses == {"failed"}:
        return "failed"
    return "skipped"


def _format_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{name}={count}" for name, count in counts.items())


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Mine GitHub/raw source candidates by running benchmark recipes "
            "against each source."
        )
    )
    parser.add_argument("sources", help="JSON file containing a sources array")
    parser.add_argument(
        "--recipe",
        action="append",
        choices=sorted(SUPPORTED_RECIPES),
        help=(
            "Recipe family to evaluate. May be repeated. Defaults to all "
            "supported recipes."
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
    parser.add_argument("--output-catalog", help="Optional catalog JSON path.")
    parser.add_argument("--output-template", help="Optional template JSON path.")
    parser.add_argument(
        "--output-sources",
        help="Optional sources JSON containing only generated candidate sources.",
    )
    parser.add_argument(
        "--source-cache-dir",
        help="Optional shared raw-source cache directory used before network fetch.",
    )
    args = parser.parse_args()

    report = mine_recipe_sources(
        load_json(args.sources),
        recipes=args.recipe,
        source_path=str(args.sources),
        source_cache_dir=Path(args.source_cache_dir)
        if args.source_cache_dir
        else None,
    )
    payload = report.to_dict()
    json_report = json.dumps(payload, indent=2, ensure_ascii=False)
    markdown_report = render_source_mining_markdown(report)
    if args.output_json:
        Path(args.output_json).write_text(json_report, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown_report, encoding="utf-8")
    if args.output_catalog:
        Path(args.output_catalog).write_text(
            json.dumps(payload["catalog"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if args.output_template:
        Path(args.output_template).write_text(
            json.dumps(payload["template"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
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
