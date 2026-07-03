from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OnboardingRunComparison:
    name: str
    baseline_present: bool
    recommended_present: bool
    baseline_passed: bool
    recommended_passed: bool
    baseline_generated_candidates: int
    recommended_generated_candidates: int
    candidate_delta: int
    baseline_benchmark_cases: int
    recommended_benchmark_cases: int
    benchmark_case_delta: int
    baseline_top1: float
    recommended_top1: float
    top1_delta: float
    baseline_map: float
    recommended_map: float
    map_delta: float
    baseline_patch_success_rate: float
    recommended_patch_success_rate: float
    patch_success_rate_delta: float
    baseline_outcome: str
    recommended_outcome: str
    baseline_fallback_reason: str
    recommended_fallback_reason: str
    change: str
    improvement_reasons: list[str]
    regression_reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OnboardingRecommendationComparison:
    baseline_report_path: str
    recommended_report_path: str
    suite_name: str
    passed: bool
    summary: dict[str, Any]
    regressions: list[str]
    run_comparisons: list[OnboardingRunComparison]

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_report_path": self.baseline_report_path,
            "recommended_report_path": self.recommended_report_path,
            "suite_name": self.suite_name,
            "passed": self.passed,
            "summary": self.summary,
            "regressions": self.regressions,
            "run_comparisons": [
                comparison.to_dict() for comparison in self.run_comparisons
            ],
        }


def compare_onboarding_recommendation_reports(
    baseline_report_path: str | Path,
    recommended_report_path: str | Path,
) -> OnboardingRecommendationComparison:
    baseline_path = Path(baseline_report_path)
    recommended_path = Path(recommended_report_path)
    baseline = _read_json(baseline_path)
    recommended = _read_json(recommended_path)
    baseline_summary = _dict(baseline.get("summary"))
    recommended_summary = _dict(recommended.get("summary"))
    baseline_runs = _extract_run_metrics(baseline)
    recommended_runs = _extract_run_metrics(recommended)
    run_names = sorted(set(baseline_runs) | set(recommended_runs))
    run_comparisons = [
        _compare_run(
            name,
            baseline_runs.get(name),
            recommended_runs.get(name),
        )
        for name in run_names
    ]
    summary_regressions = _summary_regressions(
        baseline_summary,
        recommended_summary,
    )
    missing_run_count = sum(
        1 for comparison in run_comparisons if not comparison.recommended_present
    )
    added_run_count = sum(
        1 for comparison in run_comparisons if not comparison.baseline_present
    )
    run_regression_count = sum(
        1
        for comparison in run_comparisons
        if comparison.baseline_present
        and comparison.recommended_present
        and comparison.regression_reasons
    )
    improved_run_count = sum(
        1
        for comparison in run_comparisons
        if comparison.baseline_present
        and comparison.recommended_present
        and comparison.improvement_reasons
        and not comparison.regression_reasons
    )
    summary = {
        "baseline_suite_name": baseline.get("suite_name", ""),
        "recommended_suite_name": recommended.get("suite_name", ""),
        "baseline_passed": bool(baseline.get("passed", False)),
        "recommended_passed": bool(recommended.get("passed", False)),
        "baseline_run_count": _int(baseline_summary.get("run_count", 0)),
        "recommended_run_count": _int(recommended_summary.get("run_count", 0)),
        "run_count_delta": _int(recommended_summary.get("run_count", 0))
        - _int(baseline_summary.get("run_count", 0)),
        "baseline_completed_count": _int(baseline_summary.get("completed_count", 0)),
        "recommended_completed_count": _int(
            recommended_summary.get("completed_count", 0)
        ),
        "completed_count_delta": _int(recommended_summary.get("completed_count", 0))
        - _int(baseline_summary.get("completed_count", 0)),
        "baseline_validation_pass_rate": _float(
            baseline_summary.get("validation_pass_rate", 0.0)
        ),
        "recommended_validation_pass_rate": _float(
            recommended_summary.get("validation_pass_rate", 0.0)
        ),
        "validation_pass_rate_delta": _round_delta(
            _float(recommended_summary.get("validation_pass_rate", 0.0))
            - _float(baseline_summary.get("validation_pass_rate", 0.0))
        ),
        "baseline_generated_candidates": _int(
            baseline_summary.get("generated_candidates", 0)
        ),
        "recommended_generated_candidates": _int(
            recommended_summary.get("generated_candidates", 0)
        ),
        "candidate_delta": _int(recommended_summary.get("generated_candidates", 0))
        - _int(baseline_summary.get("generated_candidates", 0)),
        "baseline_benchmark_cases": _int(baseline_summary.get("benchmark_cases", 0)),
        "recommended_benchmark_cases": _int(
            recommended_summary.get("benchmark_cases", 0)
        ),
        "benchmark_case_delta": _int(recommended_summary.get("benchmark_cases", 0))
        - _int(baseline_summary.get("benchmark_cases", 0)),
        "baseline_manifest_recommendations": _int(
            baseline_summary.get("manifest_recommendation_count", 0)
        ),
        "recommended_manifest_recommendations": _int(
            recommended_summary.get("manifest_recommendation_count", 0)
        ),
        "baseline_fallback_recovered_count": _int(
            baseline_summary.get("fallback_recovered_count", 0)
        ),
        "recommended_fallback_recovered_count": _int(
            recommended_summary.get("fallback_recovered_count", 0)
        ),
        "improved_run_count": improved_run_count,
        "regressed_run_count": run_regression_count,
        "missing_run_count": missing_run_count,
        "added_run_count": added_run_count,
        "unchanged_run_count": len(run_comparisons)
        - improved_run_count
        - run_regression_count
        - missing_run_count
        - added_run_count,
    }
    regressions = [
        *summary_regressions,
        *[
            f"{comparison.name}:{reason}"
            for comparison in run_comparisons
            for reason in comparison.regression_reasons
        ],
    ]
    return OnboardingRecommendationComparison(
        baseline_report_path=str(baseline_path),
        recommended_report_path=str(recommended_path),
        suite_name=str(
            recommended.get("suite_name")
            or baseline.get("suite_name")
            or recommended_path.stem
        ),
        passed=not regressions,
        summary=summary,
        regressions=regressions,
        run_comparisons=run_comparisons,
    )


def render_onboarding_recommendation_comparison_markdown(
    comparison: OnboardingRecommendationComparison,
) -> str:
    summary = comparison.summary
    lines = [
        "# Onboarding Recommendation Comparison",
        "",
        f"- Baseline Report: `{comparison.baseline_report_path}`",
        f"- Recommended Report: `{comparison.recommended_report_path}`",
        f"- Suite: `{comparison.suite_name}`",
        f"- Result: {'PASS' if comparison.passed else 'FAIL'}",
        (
            "- Generated Candidates: "
            f"{_int(summary.get('baseline_generated_candidates', 0))} -> "
            f"{_int(summary.get('recommended_generated_candidates', 0))} "
            f"({ _signed_int(summary.get('candidate_delta', 0)) })"
        ),
        (
            "- Benchmark Cases: "
            f"{_int(summary.get('baseline_benchmark_cases', 0))} -> "
            f"{_int(summary.get('recommended_benchmark_cases', 0))} "
            f"({ _signed_int(summary.get('benchmark_case_delta', 0)) })"
        ),
        (
            "- Validation Pass Rate: "
            f"{_float(summary.get('baseline_validation_pass_rate', 0.0)):.4f} -> "
            f"{_float(summary.get('recommended_validation_pass_rate', 0.0)):.4f} "
            f"({_signed_float(summary.get('validation_pass_rate_delta', 0.0))})"
        ),
        f"- Improved Runs: {_int(summary.get('improved_run_count', 0))}",
        f"- Regressed Runs: {_int(summary.get('regressed_run_count', 0))}",
        f"- Missing Runs: {_int(summary.get('missing_run_count', 0))}",
        f"- Added Runs: {_int(summary.get('added_run_count', 0))}",
        "",
        "## Regressions",
        "",
    ]
    if comparison.regressions:
        lines.extend(f"- `{_markdown_cell(reason)}`" for reason in comparison.regressions)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Runs",
            "",
            (
                "| Name | Baseline | Recommended | Candidates | Cases | Patch Success | "
                "Change | Regressions |"
            ),
            "| --- | --- | --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for run in comparison.run_comparisons:
        lines.append(
            "| "
            f"{_markdown_cell(run.name)} | "
            f"{_result_cell(run.baseline_present, run.baseline_passed)} | "
            f"{_result_cell(run.recommended_present, run.recommended_passed)} | "
            f"{run.baseline_generated_candidates} -> "
            f"{run.recommended_generated_candidates} "
            f"({_signed_int(run.candidate_delta)}) | "
            f"{run.baseline_benchmark_cases} -> "
            f"{run.recommended_benchmark_cases} "
            f"({_signed_int(run.benchmark_case_delta)}) | "
            f"{run.baseline_patch_success_rate:.4f} -> "
            f"{run.recommended_patch_success_rate:.4f} "
            f"({_signed_float(run.patch_success_rate_delta)}) | "
            f"{_markdown_cell(run.change)} | "
            f"{_markdown_cell(', '.join(run.regression_reasons))} |"
        )
    if not comparison.run_comparisons:
        lines.append("| none | missing | missing | 0 -> 0 (+0) | 0 -> 0 (+0) | 0.0000 -> 0.0000 (+0.0000) | empty |  |")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare a baseline onboarding smoke runner report with the report "
            "from its recommended manifest rerun."
        )
    )
    parser.add_argument("baseline_report", help="Path to the baseline runner JSON.")
    parser.add_argument(
        "recommended_report",
        help="Path to the runner JSON generated from the recommended manifest.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
    )
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    comparison = compare_onboarding_recommendation_reports(
        args.baseline_report,
        args.recommended_report,
    )
    json_payload = json.dumps(comparison.to_dict(), indent=2, ensure_ascii=False)
    markdown = render_onboarding_recommendation_comparison_markdown(comparison)
    if args.output_json:
        Path(args.output_json).write_text(json_payload, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown, encoding="utf-8")
    if args.format == "json":
        print(json_payload)
    else:
        print(markdown)
    raise SystemExit(0 if comparison.passed else 1)


def _compare_run(
    name: str,
    baseline: dict[str, Any] | None,
    recommended: dict[str, Any] | None,
) -> OnboardingRunComparison:
    baseline_present = baseline is not None
    recommended_present = recommended is not None
    baseline = baseline or {}
    recommended = recommended or {}
    baseline_passed = bool(baseline.get("passed", False))
    recommended_passed = bool(recommended.get("passed", False))
    baseline_candidates = _int(baseline.get("generated_candidates", 0))
    recommended_candidates = _int(recommended.get("generated_candidates", 0))
    baseline_cases = _int(baseline.get("benchmark_cases", 0))
    recommended_cases = _int(recommended.get("benchmark_cases", 0))
    baseline_top1 = _float(baseline.get("top1", 0.0))
    recommended_top1 = _float(recommended.get("top1", 0.0))
    baseline_map = _float(baseline.get("map", 0.0))
    recommended_map = _float(recommended.get("map", 0.0))
    baseline_patch_success = _float(baseline.get("patch_success_rate", 0.0))
    recommended_patch_success = _float(
        recommended.get("patch_success_rate", 0.0)
    )
    improvement_reasons: list[str] = []
    regression_reasons: list[str] = []
    if not recommended_present:
        regression_reasons.append("missing_in_recommended_report")
    elif not baseline_present:
        improvement_reasons.append("added_in_recommended_report")
    else:
        _classify_int_delta(
            "generated_candidates",
            recommended_candidates - baseline_candidates,
            improvement_reasons=improvement_reasons,
            regression_reasons=regression_reasons,
        )
        _classify_int_delta(
            "benchmark_cases",
            recommended_cases - baseline_cases,
            improvement_reasons=improvement_reasons,
            regression_reasons=regression_reasons,
        )
        _classify_float_delta(
            "top1",
            recommended_top1 - baseline_top1,
            improvement_reasons=improvement_reasons,
            regression_reasons=regression_reasons,
        )
        _classify_float_delta(
            "map",
            recommended_map - baseline_map,
            improvement_reasons=improvement_reasons,
            regression_reasons=regression_reasons,
        )
        _classify_float_delta(
            "patch_success_rate",
            recommended_patch_success - baseline_patch_success,
            improvement_reasons=improvement_reasons,
            regression_reasons=regression_reasons,
        )
        if baseline_passed and not recommended_passed:
            regression_reasons.append("passed_report_became_failed")
        elif not baseline_passed and recommended_passed:
            improvement_reasons.append("failed_report_became_passed")
    change = _change_label(
        baseline_present=baseline_present,
        recommended_present=recommended_present,
        improvement_reasons=improvement_reasons,
        regression_reasons=regression_reasons,
    )
    return OnboardingRunComparison(
        name=name,
        baseline_present=baseline_present,
        recommended_present=recommended_present,
        baseline_passed=baseline_passed,
        recommended_passed=recommended_passed,
        baseline_generated_candidates=baseline_candidates,
        recommended_generated_candidates=recommended_candidates,
        candidate_delta=recommended_candidates - baseline_candidates,
        baseline_benchmark_cases=baseline_cases,
        recommended_benchmark_cases=recommended_cases,
        benchmark_case_delta=recommended_cases - baseline_cases,
        baseline_top1=baseline_top1,
        recommended_top1=recommended_top1,
        top1_delta=_round_delta(recommended_top1 - baseline_top1),
        baseline_map=baseline_map,
        recommended_map=recommended_map,
        map_delta=_round_delta(recommended_map - baseline_map),
        baseline_patch_success_rate=baseline_patch_success,
        recommended_patch_success_rate=recommended_patch_success,
        patch_success_rate_delta=_round_delta(
            recommended_patch_success - baseline_patch_success
        ),
        baseline_outcome=str(baseline.get("outcome", "")),
        recommended_outcome=str(recommended.get("outcome", "")),
        baseline_fallback_reason=str(baseline.get("fallback_reason") or ""),
        recommended_fallback_reason=str(recommended.get("fallback_reason") or ""),
        change=change,
        improvement_reasons=improvement_reasons,
        regression_reasons=regression_reasons,
    )


def _extract_run_metrics(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    suite_validation = _dict(report.get("suite_validation"))
    for report_value in _list(suite_validation.get("reports")):
        item = _dict(report_value)
        name = str(item.get("name", ""))
        if not name:
            continue
        summary = _dict(item.get("summary"))
        metrics[name] = {
            "name": name,
            "passed": bool(item.get("passed", False)),
            "generated_candidates": _int(summary.get("generated_candidates", 0)),
            "benchmark_cases": _int(summary.get("benchmark_cases", 0)),
            "top1": _float(summary.get("top1", 0.0)),
            "map": _float(summary.get("map", 0.0)),
            "patch_success_rate": _float(
                summary.get("patch_success_rate", 0.0)
            ),
            "diagnostics_status": str(summary.get("diagnostics_status", "")),
            "source": str(summary.get("source", "")),
        }
    for outcome_value in _list(_dict(report.get("gap_summary")).get("run_outcomes")):
        outcome = _dict(outcome_value)
        name = str(outcome.get("name", ""))
        if not name:
            continue
        item = metrics.setdefault(name, {"name": name})
        item.setdefault("passed", str(outcome.get("outcome", "")) in {"pass", "warning"})
        item["outcome"] = str(outcome.get("outcome", ""))
        item["generated_candidates"] = _int(
            outcome.get(
                "generated_candidates",
                item.get("generated_candidates", 0),
            )
        )
        item["benchmark_cases"] = _int(
            outcome.get("benchmark_cases", item.get("benchmark_cases", 0))
        )
        item["fallback_reason"] = str(outcome.get("fallback_reason") or "")
        item["fallback_used"] = bool(outcome.get("fallback_used", False))
    for run_value in _list(report.get("runs")):
        run = _dict(run_value)
        name = str(run.get("name", ""))
        if not name:
            continue
        item = metrics.setdefault(name, {"name": name})
        item["command_passed"] = bool(run.get("passed", False))
        item.setdefault("passed", bool(run.get("passed", False)))
        item.setdefault("fallback_reason", str(run.get("fallback_reason") or ""))
    return metrics


def _summary_regressions(
    baseline_summary: dict[str, Any],
    recommended_summary: dict[str, Any],
) -> list[str]:
    regressions: list[str] = []
    checks = [
        ("run_count", "run_count_decreased"),
        ("completed_count", "completed_count_decreased"),
        ("generated_candidates", "generated_candidates_decreased"),
        ("benchmark_cases", "benchmark_cases_decreased"),
    ]
    for field, reason in checks:
        if _int(recommended_summary.get(field, 0)) < _int(
            baseline_summary.get(field, 0)
        ):
            regressions.append(reason)
    if _float(recommended_summary.get("validation_pass_rate", 0.0)) + 1e-9 < _float(
        baseline_summary.get("validation_pass_rate", 0.0)
    ):
        regressions.append("validation_pass_rate_decreased")
    return regressions


def _classify_int_delta(
    name: str,
    delta: int,
    *,
    improvement_reasons: list[str],
    regression_reasons: list[str],
) -> None:
    if delta > 0:
        improvement_reasons.append(f"{name}_increased")
    elif delta < 0:
        regression_reasons.append(f"{name}_decreased")


def _classify_float_delta(
    name: str,
    delta: float,
    *,
    improvement_reasons: list[str],
    regression_reasons: list[str],
) -> None:
    if delta > 1e-9:
        improvement_reasons.append(f"{name}_increased")
    elif delta < -1e-9:
        regression_reasons.append(f"{name}_decreased")


def _change_label(
    *,
    baseline_present: bool,
    recommended_present: bool,
    improvement_reasons: list[str],
    regression_reasons: list[str],
) -> str:
    if not baseline_present:
        return "added"
    if not recommended_present:
        return "missing"
    if regression_reasons and improvement_reasons:
        return "mixed"
    if regression_reasons:
        return "regressed"
    if improvement_reasons:
        return "improved"
    return "unchanged"


def _read_json(path: Path) -> dict[str, Any]:
    return _dict(json.loads(path.read_text(encoding="utf-8")))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _round_delta(value: float) -> float:
    return round(value, 6)


def _signed_int(value: Any) -> str:
    number = _int(value)
    return f"+{number}" if number >= 0 else str(number)


def _signed_float(value: Any) -> str:
    number = _float(value)
    sign = "+" if number >= 0 else ""
    return f"{sign}{number:.4f}"


def _result_cell(present: bool, passed: bool) -> str:
    if not present:
        return "missing"
    return "PASS" if passed else "FAIL"


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


if __name__ == "__main__":
    main()
