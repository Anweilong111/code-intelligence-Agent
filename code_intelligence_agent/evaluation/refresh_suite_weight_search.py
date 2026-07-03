from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.quality_gate import (
    evaluate_quality_gate,
    quality_gate_thresholds_from_dict,
    render_quality_gate_markdown,
)
from code_intelligence_agent.evaluation.benchmark_provenance import (
    benchmark_provenance_summary,
)
from code_intelligence_agent.evaluation.report import render_weight_search_markdown
from code_intelligence_agent.evaluation.showcase_report import (
    build_showcase_report,
    render_resume_showcase_markdown,
    render_showcase_markdown,
)
from code_intelligence_agent.evaluation.weight_search import WeightSearchRunner


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh FinalScore weight-search results in an existing "
            "experiment-suite artifact without rerunning the full benchmark."
        )
    )
    parser.add_argument("suite_json", help="Path to an existing suite.json")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
        help="Stdout format for the refreshed suite artifact.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=None,
        help="Number of FinalScore weight profiles to retain.",
    )
    parser.add_argument(
        "--no-dynamic-coverage",
        action="store_true",
        help="Force manifest fallback coverage for the refreshed weight search.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite suite.json and known derived markdown/showcase files.",
    )
    parser.add_argument("--output-json", help="Optional refreshed suite JSON path")
    parser.add_argument("--output-markdown", help="Optional refreshed suite markdown path")
    args = parser.parse_args()

    refreshed = refresh_suite_weight_search(
        args.suite_json,
        top_n=args.top_n,
        force_no_dynamic_coverage=args.no_dynamic_coverage,
        in_place=args.in_place,
        output_json=args.output_json,
        output_markdown=args.output_markdown,
    )
    if args.format == "json":
        print(json.dumps(refreshed, indent=2, ensure_ascii=False))
    else:
        print(refreshed.get("markdown", ""))


def refresh_suite_weight_search(
    suite_json: str | Path,
    *,
    top_n: int | None = None,
    force_no_dynamic_coverage: bool = False,
    in_place: bool = False,
    output_json: str | Path | None = None,
    output_markdown: str | Path | None = None,
) -> dict[str, Any]:
    suite_path = Path(suite_json)
    payload = json.loads(suite_path.read_text(encoding="utf-8"))
    settings = _settings(payload)
    manifest_path = Path(str(payload.get("manifest_path", "")))
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest does not exist: {manifest_path}")

    retained_count = max(1, top_n or int(settings.get("weight_search_top_n", 10) or 10))
    use_dynamic_coverage = bool(settings.get("use_dynamic_coverage", True))
    if force_no_dynamic_coverage:
        use_dynamic_coverage = False
    weight_results = WeightSearchRunner(
        use_dynamic_coverage=use_dynamic_coverage,
    ).search_manifest(manifest_path)[:retained_count]

    payload["weight_search_results"] = [item.to_dict() for item in weight_results]
    settings["run_weight_search"] = True
    settings["weight_search_top_n"] = retained_count
    settings["use_dynamic_coverage"] = use_dynamic_coverage
    payload["settings"] = settings

    _refresh_benchmark_provenance_if_present(payload)
    quality_gate = _refresh_quality_gate_if_needed(payload)
    showcase_report = _refresh_showcase_if_needed(payload)
    payload["markdown"] = _refresh_markdown_sections(
        markdown=str(payload.get("markdown", "")),
        weight_markdown=render_weight_search_markdown(
            weight_results,
            top_n=retained_count,
        ),
        quality_gate_markdown=(
            render_quality_gate_markdown(quality_gate) if quality_gate else ""
        ),
        showcase_markdown=(
            render_showcase_markdown(showcase_report) if showcase_report else ""
        ),
    )

    _write_outputs(
        payload=payload,
        suite_path=suite_path,
        in_place=in_place,
        output_json=Path(output_json) if output_json else None,
        output_markdown=Path(output_markdown) if output_markdown else None,
    )
    return payload


def _refresh_benchmark_provenance_if_present(payload: dict[str, Any]) -> None:
    benchmark = payload.get("benchmark_report")
    if not isinstance(benchmark, dict):
        return
    summary = benchmark.get("summary")
    cases = benchmark.get("cases")
    if not isinstance(summary, dict) or not isinstance(cases, list):
        return
    if "benchmark_provenance_audit" not in summary:
        return
    summary["benchmark_provenance_audit"] = benchmark_provenance_summary(cases)


def _settings(payload: dict[str, Any]) -> dict[str, Any]:
    settings = payload.get("settings", {})
    return dict(settings) if isinstance(settings, dict) else {}


def _refresh_quality_gate_if_needed(payload: dict[str, Any]):
    settings = _settings(payload)
    should_refresh = (
        payload.get("quality_gate") is not None
        or settings.get("run_quality_gate") is True
    )
    if not should_refresh:
        return None
    existing = payload.get("quality_gate", {})
    thresholds_payload = (
        existing.get("thresholds", {}) if isinstance(existing, dict) else {}
    )
    thresholds = quality_gate_thresholds_from_dict(
        thresholds_payload if isinstance(thresholds_payload, dict) else {}
    )
    result = evaluate_quality_gate(payload, thresholds=thresholds)
    payload["quality_gate"] = result.to_dict()
    return result


def _refresh_showcase_if_needed(payload: dict[str, Any]) -> dict[str, Any] | None:
    should_refresh = (
        payload.get("showcase_report") is not None
        or bool(payload.get("showcase_report_json_path"))
        or bool(payload.get("showcase_report_markdown_path"))
    )
    if not should_refresh:
        return None
    report = build_showcase_report(payload)
    payload["showcase_report"] = report
    return report


def _refresh_markdown_sections(
    *,
    markdown: str,
    weight_markdown: str,
    quality_gate_markdown: str,
    showcase_markdown: str,
) -> str:
    sections = [
        ("## FinalScore Weight Search", weight_markdown),
    ]
    if quality_gate_markdown:
        sections.append(("# Quality Gate", quality_gate_markdown))
    if showcase_markdown:
        sections.append(("# Algorithm Showcase Report", showcase_markdown))

    refreshed = markdown
    for heading, body in sections:
        section = body if body.startswith(heading) else f"{heading}\n\n{body}"
        refreshed = _replace_or_append_section(refreshed, heading, section)
    return refreshed


def _replace_or_append_section(markdown: str, heading: str, section: str) -> str:
    if not markdown:
        return section
    lines = markdown.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == heading),
        None,
    )
    if start is None:
        return markdown.rstrip() + "\n\n" + section

    heading_level = _heading_level(heading)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line_level = _heading_level(lines[index])
        if line_level and line_level <= heading_level:
            end = index
            break
    replacement = section.splitlines()
    return "\n".join(lines[:start] + replacement + lines[end:])


def _heading_level(line: str) -> int:
    stripped = line.lstrip()
    hashes = len(stripped) - len(stripped.lstrip("#"))
    if hashes == 0:
        return 0
    if len(stripped) <= hashes or stripped[hashes] != " ":
        return 0
    return hashes


def _write_outputs(
    *,
    payload: dict[str, Any],
    suite_path: Path,
    in_place: bool,
    output_json: Path | None,
    output_markdown: Path | None,
) -> None:
    json_text = json.dumps(payload, indent=2, ensure_ascii=False)
    markdown = str(payload.get("markdown", ""))
    if in_place:
        suite_path.write_text(json_text, encoding="utf-8")
        suite_markdown_path = payload.get("suite_markdown_path")
        if suite_markdown_path:
            Path(str(suite_markdown_path)).write_text(markdown, encoding="utf-8")
        _write_showcase_outputs(payload)
    if output_json:
        output_json.write_text(json_text, encoding="utf-8")
    if output_markdown:
        output_markdown.write_text(markdown, encoding="utf-8")


def _write_showcase_outputs(payload: dict[str, Any]) -> None:
    report = payload.get("showcase_report")
    if not isinstance(report, dict):
        return
    json_path = payload.get("showcase_report_json_path")
    markdown_path = payload.get("showcase_report_markdown_path")
    resume_markdown_path = payload.get("resume_showcase_markdown_path")
    if json_path:
        Path(str(json_path)).write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if markdown_path:
        Path(str(markdown_path)).write_text(
            render_showcase_markdown(report),
            encoding="utf-8",
        )
    if resume_markdown_path:
        Path(str(resume_markdown_path)).write_text(
            render_resume_showcase_markdown(report),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
