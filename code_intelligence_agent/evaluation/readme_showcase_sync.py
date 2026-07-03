from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


METRIC_LABELS = (
    "Benchmark Cases",
    "Top-1 Localization",
    "Top-3 Localization",
    "MAP",
    "Patch Success Rate",
    "Beam Success Rate",
    "Cross-function Data-flow Cases",
    "Program Slice Cases",
    "Slice-grounded Cases",
    "Average Top-1 Slice Support",
    "Generated Hard Cases",
    "Generated Score Inversions",
    "Generated Diversity-Assisted Successes",
    "Generated Diversity Success Lift",
    "Generated Diversity Success Bonus",
    "Ablation-linked Generated Cases",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Synchronize the README project overview metrics from a "
            "showcase_report.json artifact."
        )
    )
    parser.add_argument("readme", help="Path to README.MD")
    parser.add_argument("showcase_report", help="Path to showcase_report.json")
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite README with synchronized overview metrics.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit with status 1 when README metrics are stale.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
        help="Output format.",
    )
    args = parser.parse_args()

    readme_path = Path(args.readme)
    showcase = json.loads(Path(args.showcase_report).read_text(encoding="utf-8"))
    original = readme_path.read_text(encoding="utf-8")
    expected = showcase_overview_metrics(showcase)
    mismatches = readme_showcase_mismatches(original, expected)
    updated = sync_readme_showcase_text(original, expected)
    changed = updated != original
    if args.in_place and changed:
        readme_path.write_text(updated, encoding="utf-8")
    result = {
        "changed": changed,
        "checked": bool(args.check),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(render_sync_markdown(result))
    if args.check and mismatches:
        raise SystemExit(1)


def showcase_overview_metrics(showcase: dict[str, Any]) -> dict[str, str]:
    headline = _dict(showcase.get("headline"))
    graph = _dict(
        _dict(showcase.get("algorithm_evidence")).get("static_graph_reasoning")
    )
    generated = _dict(showcase.get("generated_hard_case_evidence_summary"))
    ablation_links = _dict(showcase.get("generated_hard_case_ablation_link_summary"))
    return {
        "Benchmark Cases": str(_int(headline.get("case_count", 0))),
        "Top-1 Localization": _fmt(headline.get("top1", 0.0)),
        "Top-3 Localization": _fmt(headline.get("top3", 0.0)),
        "MAP": _fmt(headline.get("map", 0.0)),
        "Patch Success Rate": _fmt(headline.get("patch_success_rate", 0.0)),
        "Beam Success Rate": _fmt(headline.get("beam_success_rate", 0.0)),
        "Cross-function Data-flow Cases": str(
            _int(graph.get("cross_function_data_flow_cases", 0))
        ),
        "Program Slice Cases": str(_int(graph.get("program_slice_cases", 0))),
        "Slice-grounded Cases": str(_int(graph.get("slice_grounded_cases", 0))),
        "Average Top-1 Slice Support": _fmt(
            graph.get("average_top1_slice_support", 0.0)
        ),
        "Generated Hard Cases": str(_int(generated.get("generated_cases", 0))),
        "Generated Score Inversions": str(
            _int(generated.get("score_inversions", 0))
        ),
        "Generated Diversity-Assisted Successes": str(
            _int(generated.get("diversity_assisted_successes", 0))
        ),
        "Generated Diversity Success Lift": _fmt(
            generated.get("average_success_diversity_lift", 0.0)
        ),
        "Generated Diversity Success Bonus": _fmt(
            generated.get("average_success_diversity_bonus", 0.0)
        ),
        "Ablation-linked Generated Cases": str(
            _int(ablation_links.get("linked_cases", 0))
        ),
    }


def sync_readme_showcase_text(
    readme: str,
    expected_metrics: dict[str, str],
) -> str:
    updated = readme
    missing = []
    for label in METRIC_LABELS:
        value = expected_metrics[label]
        pattern = re.compile(
            rf"^\|\s*{re.escape(label)}\s*\|\s*[^|]+\s*\|$",
            re.MULTILINE,
        )
        updated, count = pattern.subn(f"| {label} | {value} |", updated, count=1)
        if count != 1:
            missing.append(label)
    if missing:
        raise ValueError(
            "README overview is missing metric rows: " + ", ".join(missing)
        )
    return updated


def readme_showcase_mismatches(
    readme: str,
    expected_metrics: dict[str, str],
) -> list[dict[str, str]]:
    actual = extract_readme_showcase_metrics(readme)
    mismatches = []
    for label in METRIC_LABELS:
        expected = expected_metrics[label]
        actual_value = actual.get(label, "")
        if actual_value != expected:
            mismatches.append(
                {
                    "metric": label,
                    "readme": actual_value,
                    "expected": expected,
                }
            )
    return mismatches


def extract_readme_showcase_metrics(readme: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in readme.splitlines():
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 2:
            continue
        label, value = cells
        if label in METRIC_LABELS:
            rows[label] = value
    return rows


def render_sync_markdown(result: dict[str, Any]) -> str:
    mismatches = [
        row for row in result.get("mismatches", []) if isinstance(row, dict)
    ]
    lines = [
        "# README Showcase Sync",
        "",
        f"- Changed: {str(result.get('changed', False)).lower()}",
        f"- Mismatch Count: {_int(result.get('mismatch_count', 0))}",
    ]
    if mismatches:
        lines.extend(
            [
                "",
                "| Metric | README | Expected |",
                "| --- | ---: | ---: |",
            ]
        )
        for row in mismatches:
            lines.append(
                "| "
                f"{row.get('metric', '')} | "
                f"{row.get('readme', '')} | "
                f"{row.get('expected', '')} |"
            )
    else:
        lines.extend(["", "README showcase metrics are in sync."])
    return "\n".join(lines)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _fmt(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "0.0000"


if __name__ == "__main__":
    main()
