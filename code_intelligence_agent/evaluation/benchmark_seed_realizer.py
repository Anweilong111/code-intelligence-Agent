from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.benchmark_provenance import (
    benchmark_provenance_summary,
)


@dataclass(frozen=True)
class SeedRealization:
    seed_name: str
    status: str
    candidate_id: str
    score: float
    reasons: list[str]
    template_case: dict[str, Any] | None = None
    unmatched_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SeedRealizationReport:
    seed_path: str
    catalog_path: str
    seed_count: int
    candidate_count: int
    realized_count: int
    unmatched_count: int
    realizations: list[SeedRealization]

    def to_dict(self) -> dict[str, Any]:
        realized_cases = [
            item.template_case
            for item in self.realizations
            if item.template_case is not None
        ]
        return {
            "seed_path": self.seed_path,
            "catalog_path": self.catalog_path,
            "seed_count": self.seed_count,
            "candidate_count": self.candidate_count,
            "realized_count": self.realized_count,
            "unmatched_count": self.unmatched_count,
            "realizations": [item.to_dict() for item in self.realizations],
            "realized_template": {"cases": realized_cases},
        }


def realize_benchmark_template_seeds(
    seed_payload: dict[str, Any],
    catalog_payload: dict[str, Any],
    seed_path: str = "",
    catalog_path: str = "",
) -> SeedRealizationReport:
    seeds = _extract_seed_cases(seed_payload)
    candidates = _extract_candidates(catalog_payload)
    realizations = [
        _realize_seed(seed, candidates)
        for seed in seeds
    ]
    realized_count = sum(1 for item in realizations if item.status == "realized")
    return SeedRealizationReport(
        seed_path=seed_path,
        catalog_path=catalog_path,
        seed_count=len(seeds),
        candidate_count=len(candidates),
        realized_count=realized_count,
        unmatched_count=len(seeds) - realized_count,
        realizations=realizations,
    )


def render_seed_realization_markdown(report: SeedRealizationReport) -> str:
    lines = [
        "# Benchmark Seed Realization",
        "",
        f"- Seeds: {report.seed_count}",
        f"- Candidates: {report.candidate_count}",
        f"- Realized: {report.realized_count}",
        f"- Unmatched: {report.unmatched_count}",
        "",
        "| Seed | Status | Candidate | Score | Reasons |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for item in report.realizations:
        lines.append(
            "| "
            f"{_markdown_cell(item.seed_name)} | "
            f"{_markdown_cell(item.status)} | "
            f"{_markdown_cell(item.candidate_id)} | "
            f"{item.score:.2f} | "
            f"{_markdown_cell(', '.join(item.reasons) or item.unmatched_reason)} |"
        )
    return "\n".join(lines)


def _realize_seed(
    seed: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> SeedRealization:
    seed_name = str(seed.get("name", ""))
    scored = [
        _score_candidate(seed, candidate)
        for candidate in candidates
    ]
    scored = [item for item in scored if item[0] > 0.0]
    if not scored:
        return SeedRealization(
            seed_name=seed_name,
            status="unmatched",
            candidate_id="",
            score=0.0,
            reasons=[],
            unmatched_reason="No catalog candidate matched mining metadata.",
        )
    score, candidate_id, reasons, candidate = sorted(
        scored,
        key=lambda item: (-item[0], item[1]),
    )[0]
    template_case = _realized_template_case(seed, candidate, score, reasons)
    return SeedRealization(
        seed_name=seed_name,
        status="realized",
        candidate_id=candidate_id,
        score=score,
        reasons=reasons,
        template_case=template_case,
    )


def _score_candidate(
    seed: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[float, str, list[str], dict[str, Any]]:
    seed_metadata = _benchmark_metadata(seed)
    candidate_id = str(candidate.get("id", ""))
    candidate_case = _candidate_template_case(candidate)
    candidate_metadata = _benchmark_metadata(candidate_case)
    score = 0.0
    reasons = []
    seed_failure_type = str(seed_metadata.get("mining_failure_type", ""))
    seed_focus = str(seed_metadata.get("mining_focus", ""))
    seed_pattern = str(seed_metadata.get("mining_pattern", ""))
    candidate_failure_types = _string_set(
        candidate.get("failure_types")
        or candidate_metadata.get("target_judge_failure_types")
    )
    candidate_focuses = _string_set(
        candidate.get("benchmark_focuses")
        or candidate_metadata.get("target_benchmark_focuses")
    )
    candidate_patterns = _string_set(
        candidate.get("patterns")
        or candidate_metadata.get("target_calibration_patterns")
    )
    if seed_failure_type and seed_failure_type in candidate_failure_types:
        score += 4.0
        reasons.append(f"failure_type={seed_failure_type}")
    if seed_focus and seed_focus in candidate_focuses:
        score += 3.0
        reasons.append(f"focus={seed_focus}")
    if seed_pattern and seed_pattern in candidate_patterns:
        score += 2.0
        reasons.append(f"pattern={seed_pattern}")
    seed_rules = _string_set(seed.get("benchmark", {}).get("expected_rule_ids"))
    candidate_rules = _string_set(candidate_case.get("benchmark", {}).get("expected_rule_ids"))
    overlap = sorted(seed_rules & candidate_rules)
    if overlap:
        score += float(len(overlap))
        reasons.append(f"rules={','.join(overlap)}")
    if score <= 0.0:
        return score, candidate_id, reasons, candidate
    provenance_score, provenance_reasons = _candidate_provenance_bonus(
        candidate_case
    )
    if provenance_score > 0.0:
        score += provenance_score
        reasons.extend(provenance_reasons)
    return score, candidate_id, reasons, candidate


def _candidate_provenance_bonus(candidate_case: dict[str, Any]) -> tuple[float, list[str]]:
    audit = benchmark_provenance_summary([candidate_case])
    source_ref_count = int(audit.get("source_ref_count", 0))
    source_sha_present = int(audit.get("source_sha256_present_count", 0))
    case_coverage = float(audit.get("case_provenance_coverage", 0.0))
    sha_coverage = (
        float(audit.get("source_sha256_coverage", 0.0))
        if source_sha_present
        else 0.0
    )
    stable_ref_coverage = (
        float(audit.get("stable_ref_coverage", 0.0))
        if source_ref_count
        else 0.0
    )
    license_coverage = float(audit.get("license_coverage", 0.0))
    leakage_risk = float(audit.get("leakage_risk_score", 1.0))
    if max(case_coverage, sha_coverage, stable_ref_coverage, license_coverage) <= 0.0:
        return 0.0, []
    score = round(
        0.25 * case_coverage
        + 0.25 * sha_coverage
        + 0.20 * stable_ref_coverage
        + 0.20 * license_coverage
        + 0.10 * max(0.0, 1.0 - leakage_risk),
        4,
    )
    reasons = []
    if score > 0.0:
        reasons.append(f"provenance_bonus={score:.4f}")
        reasons.append(f"case_provenance={case_coverage:.4f}")
        if source_sha_present:
            reasons.append(f"source_sha256={sha_coverage:.4f}")
        if source_ref_count:
            reasons.append(f"stable_ref={stable_ref_coverage:.4f}")
        reasons.append(f"license={license_coverage:.4f}")
        reasons.append(f"leakage_risk={leakage_risk:.4f}")
    return score, reasons


def _realized_template_case(
    seed: dict[str, Any],
    candidate: dict[str, Any],
    score: float,
    reasons: list[str],
) -> dict[str, Any]:
    template_case = _deepcopy_json(_candidate_template_case(candidate))
    seed_metadata = _benchmark_metadata(seed)
    candidate_id = str(candidate.get("id", ""))
    benchmark = dict(template_case.get("benchmark", {}))
    metadata = dict(benchmark.get("metadata", {}))
    metadata.update(
        {
            "seed_status": "realized_from_catalog",
            "mining_seed_name": seed.get("name", ""),
            "realization_candidate_id": candidate_id,
            "realization_score": score,
            "realization_reasons": reasons,
            "mining_failure_type": seed_metadata.get("mining_failure_type", ""),
            "mining_focus": seed_metadata.get("mining_focus", ""),
            "mining_pattern": seed_metadata.get("mining_pattern", ""),
            "mining_priority": seed_metadata.get("mining_priority", ""),
            "evidence_examples": seed_metadata.get("evidence_examples", []),
        }
    )
    benchmark["metadata"] = metadata
    template_case["benchmark"] = benchmark
    return template_case


def _extract_seed_cases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("cases"), list):
        return [item for item in payload["cases"] if isinstance(item, dict)]
    preview = payload.get("template_seed_preview", {})
    if isinstance(preview, dict) and isinstance(preview.get("cases"), list):
        return [item for item in preview["cases"] if isinstance(item, dict)]
    seeds = payload.get("template_seeds", [])
    output = []
    if isinstance(seeds, list):
        for seed in seeds:
            if not isinstance(seed, dict):
                continue
            case = seed.get("template_case")
            if isinstance(case, dict):
                output.append(case)
            elif "name" in seed:
                output.append(seed)
    return output


def _extract_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        return []
    return [
        item
        for item in candidates
        if isinstance(item, dict) and isinstance(item.get("template_case"), dict)
    ]


def _candidate_template_case(candidate: dict[str, Any]) -> dict[str, Any]:
    case = candidate.get("template_case", {})
    return case if isinstance(case, dict) else {}


def _benchmark_metadata(case: dict[str, Any]) -> dict[str, Any]:
    benchmark = case.get("benchmark", {})
    if not isinstance(benchmark, dict):
        return {}
    metadata = benchmark.get("metadata", {})
    return metadata if isinstance(metadata, dict) else {}


def _string_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value} if value else set()
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value if str(item)}


def _deepcopy_json(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value))


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Realize benchmark template seeds with concrete GitHub/raw candidate recipes."
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
    parser.add_argument("--output-json", help="Optional realization report JSON path.")
    parser.add_argument("--output-markdown", help="Optional realization markdown path.")
    parser.add_argument(
        "--output-template",
        help="Optional realized benchmark template JSON path.",
    )
    parser.add_argument(
        "--fail-on-unmatched",
        action="store_true",
        help="Exit with status 1 if any seed cannot be realized from the catalog.",
    )
    args = parser.parse_args()

    seed_payload = load_json(args.seeds)
    catalog_payload = load_json(args.catalog)
    report = realize_benchmark_template_seeds(
        seed_payload,
        catalog_payload,
        seed_path=str(args.seeds),
        catalog_path=str(args.catalog),
    )
    report_payload = report.to_dict()
    json_report = json.dumps(report_payload, indent=2, ensure_ascii=False)
    markdown_report = render_seed_realization_markdown(report)
    if args.output_json:
        Path(args.output_json).write_text(json_report, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown_report, encoding="utf-8")
    if args.output_template:
        Path(args.output_template).write_text(
            json.dumps(report_payload["realized_template"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if args.format == "markdown":
        print(markdown_report)
    else:
        print(json_report)
    if args.fail_on_unmatched and report.unmatched_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
