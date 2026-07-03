from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_REQUIRED_ARTIFACTS = (
    "sources",
    "source_mining_json",
    "catalog",
    "template",
    "diagnostics_json",
    "diagnostics_markdown",
    "quality_gate_json",
    "quality_gate_markdown",
    "showcase_lite_json",
    "showcase_lite_markdown",
    "run_config_json",
    "run_config_markdown",
    "benchmark_manifest",
    "benchmark_report_json",
    "benchmark_report_markdown",
)


@dataclass(frozen=True)
class OnboardingSmokeThresholds:
    min_generated_candidates: int = 1
    require_quality_gate: bool = True
    require_benchmark_run: bool = True
    min_benchmark_cases: int = 1
    min_top1: float = 0.50
    min_map: float = 0.50
    min_patch_success_rate: float = 0.50
    allowed_diagnostics_statuses: tuple[str, ...] = ("pass", "warning")
    required_artifacts: tuple[str, ...] = DEFAULT_REQUIRED_ARTIFACTS

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["allowed_diagnostics_statuses"] = list(
            self.allowed_diagnostics_statuses
        )
        payload["required_artifacts"] = list(self.required_artifacts)
        return payload


@dataclass(frozen=True)
class OnboardingSmokeCheck:
    name: str
    passed: bool
    expected: str
    actual: str
    details: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OnboardingSmokeValidationReport:
    report_path: str
    passed: bool
    thresholds: OnboardingSmokeThresholds
    checks: list[OnboardingSmokeCheck]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_path": self.report_path,
            "passed": self.passed,
            "thresholds": self.thresholds.to_dict(),
            "checks": [check.to_dict() for check in self.checks],
            "summary": self.summary,
        }


@dataclass(frozen=True)
class OnboardingSmokeSuiteReport:
    manifest_path: str
    suite_name: str
    passed: bool
    summary: dict[str, Any]
    reports: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_path": self.manifest_path,
            "suite_name": self.suite_name,
            "passed": self.passed,
            "summary": self.summary,
            "reports": self.reports,
        }


def validate_onboarding_smoke_report(
    report_path: str | Path,
    *,
    thresholds: OnboardingSmokeThresholds | None = None,
) -> OnboardingSmokeValidationReport:
    path = Path(report_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    thresholds = thresholds or OnboardingSmokeThresholds()
    summary = _summary(payload)
    benchmark_run = _dict(payload.get("benchmark_run"))
    benchmark_summary = _dict(benchmark_run.get("summary"))
    quality_gate = _dict(payload.get("quality_gate"))
    diagnostics = _dict(payload.get("diagnostics"))
    diagnostics_headline = _dict(diagnostics.get("headline"))
    checks = [
        _int_check(
            "generated_candidates",
            _int(payload.get("generated_candidate_count", 0)),
            thresholds.min_generated_candidates,
        ),
        _bool_check(
            "quality_gate_present",
            not thresholds.require_quality_gate or bool(quality_gate),
            "present" if thresholds.require_quality_gate else "optional",
            "present" if quality_gate else "missing",
        ),
        _bool_check(
            "quality_gate_passed",
            not thresholds.require_quality_gate
            or bool(quality_gate.get("passed", False)),
            "true" if thresholds.require_quality_gate else "optional",
            str(bool(quality_gate.get("passed", False))).lower()
            if quality_gate
            else "missing",
        ),
        _bool_check(
            "benchmark_run_present",
            not thresholds.require_benchmark_run or bool(benchmark_run),
            "present" if thresholds.require_benchmark_run else "optional",
            "present" if benchmark_run else "missing",
        ),
    ]
    if thresholds.require_benchmark_run:
        checks.extend(
            [
                _int_check(
                    "benchmark_cases",
                    _int(benchmark_summary.get("case_count", 0)),
                    thresholds.min_benchmark_cases,
                ),
                _float_check(
                    "benchmark_top1",
                    _float(benchmark_summary.get("top1", 0.0)),
                    thresholds.min_top1,
                ),
                _float_check(
                    "benchmark_map",
                    _float(benchmark_summary.get("map", 0.0)),
                    thresholds.min_map,
                ),
                _float_check(
                    "benchmark_patch_success_rate",
                    _float(benchmark_summary.get("patch_success_rate", 0.0)),
                    thresholds.min_patch_success_rate,
                ),
            ]
        )
    diagnostics_status = str(diagnostics_headline.get("status", "missing"))
    checks.append(
        OnboardingSmokeCheck(
            name="diagnostics_status",
            passed=diagnostics_status in thresholds.allowed_diagnostics_statuses,
            expected=", ".join(thresholds.allowed_diagnostics_statuses),
            actual=diagnostics_status,
        )
    )
    missing_artifacts = _missing_artifacts(
        _dict(payload.get("output_paths")),
        report_path=path,
        artifact_names=thresholds.required_artifacts,
    )
    checks.append(
        OnboardingSmokeCheck(
            name="required_artifacts",
            passed=not missing_artifacts,
            expected="all required artifacts exist",
            actual=f"missing={len(missing_artifacts)}",
            details=missing_artifacts or None,
        )
    )
    return OnboardingSmokeValidationReport(
        report_path=str(path),
        passed=all(check.passed for check in checks),
        thresholds=thresholds,
        checks=checks,
        summary=summary,
    )


def validate_onboarding_smoke_manifest(
    manifest_path: str | Path,
) -> OnboardingSmokeSuiteReport:
    path = Path(manifest_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = _manifest_entries(payload)
    default_thresholds = _thresholds_from_dict(_dict(payload.get("thresholds")))
    report_results = []
    for index, entry_value in enumerate(entries):
        entry = _dict(entry_value)
        name = str(entry.get("name") or f"report_{index}")
        raw_report_path = str(entry.get("report_path") or entry.get("path") or "")
        resolved_report_path = _resolve_manifest_path(raw_report_path, base_dir=path.parent)
        thresholds = _thresholds_from_dict(
            _dict(entry.get("thresholds")),
            base=default_thresholds,
        )
        validation = validate_onboarding_smoke_report(
            resolved_report_path,
            thresholds=thresholds,
        )
        report_results.append(
            {
                "name": name,
                "report_path": str(resolved_report_path),
                "passed": validation.passed,
                "summary": validation.summary,
                "failed_checks": [
                    check.to_dict() for check in validation.checks if not check.passed
                ],
                "validation": validation.to_dict(),
            }
        )
    summary = _suite_summary(report_results)
    return OnboardingSmokeSuiteReport(
        manifest_path=str(path),
        suite_name=str(payload.get("suite_name") or payload.get("name") or path.stem),
        passed=all(report.get("passed", False) for report in report_results)
        and bool(report_results),
        summary=summary,
        reports=report_results,
    )


def render_onboarding_smoke_validation_markdown(
    report: OnboardingSmokeValidationReport,
) -> str:
    summary = report.summary
    lines = [
        "# Onboarding Smoke Validation",
        "",
        f"- Report: `{report.report_path}`",
        f"- Result: {'PASS' if report.passed else 'FAIL'}",
        f"- Source: `{summary.get('source', '')}`",
        f"- Generated Candidates: {_int(summary.get('generated_candidates', 0))}",
        f"- Quality Gate: {_markdown_cell(summary.get('quality_gate_passed'))}",
        f"- Diagnostics: `{summary.get('diagnostics_status', '')}`",
        f"- Benchmarkization: `{summary.get('benchmarkization_status', '')}`",
        f"- Benchmarkization Stage: `{summary.get('benchmarkization_stage', '')}`",
        f"- Benchmarkization Primary Action: `{summary.get('benchmarkization_primary_action_id', '')}`",
        f"- Benchmark Cases: {_int(summary.get('benchmark_cases', 0))}",
        f"- Top-1: {_float(summary.get('top1', 0.0)):.4f}",
        f"- MAP: {_float(summary.get('map', 0.0)):.4f}",
        (
            "- Patch Success: "
            f"{_float(summary.get('patch_success_rate', 0.0)):.4f}"
        ),
        "",
        "| Check | Expected | Actual | Result | Details |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in report.checks:
        lines.append(
            "| "
            f"{_markdown_cell(check.name)} | "
            f"{_markdown_cell(check.expected)} | "
            f"{_markdown_cell(check.actual)} | "
            f"{'PASS' if check.passed else 'FAIL'} | "
            f"{_markdown_cell('; '.join(check.details or []))} |"
        )
    return "\n".join(lines)


def render_onboarding_smoke_suite_markdown(
    suite: OnboardingSmokeSuiteReport,
) -> str:
    summary = suite.summary
    lines = [
        "# Onboarding Smoke Suite Validation",
        "",
        f"- Manifest: `{suite.manifest_path}`",
        f"- Suite: `{suite.suite_name}`",
        f"- Result: {'PASS' if suite.passed else 'FAIL'}",
        f"- Reports: {_int(summary.get('report_count', 0))}",
        f"- Passed Reports: {_int(summary.get('passed_count', 0))}",
        f"- Pass Rate: {_float(summary.get('pass_rate', 0.0)):.4f}",
        f"- Generated Candidates: {_int(summary.get('generated_candidates', 0))}",
        f"- Benchmark Cases: {_int(summary.get('benchmark_cases', 0))}",
        f"- Benchmark Reports: {_int(summary.get('benchmark_report_count', 0))}",
        f"- Benchmarkization Ready Reports: {_int(summary.get('benchmarkization_ready_count', 0))}",
        (
            "- Benchmarkization Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('benchmarkization_status_counts'))))}"
        ),
        (
            "- Benchmarkization Stages: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('benchmarkization_stage_counts'))))}"
        ),
        (
            "- Benchmarkization Actions: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('benchmarkization_primary_action_counts'))))}"
        ),
        f"- Benchmarkization Remediation Plans: {_int(summary.get('benchmarkization_remediation_plan_count', 0))}",
        f"- Average Top-1: {_float(summary.get('average_top1', 0.0)):.4f}",
        f"- Average MAP: {_float(summary.get('average_map', 0.0)):.4f}",
        (
            "- Average Patch Success: "
            f"{_float(summary.get('average_patch_success_rate', 0.0)):.4f}"
        ),
        "",
        "## Reports",
        "",
        "| Name | Result | Source | Candidates | Diagnostics | Benchmarkization | Action | Top-1 | MAP | Patch Success | Failed Checks |",
        "| --- | --- | --- | ---: | --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for report_value in suite.reports:
        report = _dict(report_value)
        item_summary = _dict(report.get("summary"))
        failed_checks = [
            str(check.get("name", ""))
            for check in _list(report.get("failed_checks"))
            if isinstance(check, dict)
        ]
        lines.append(
            "| "
            f"{_markdown_cell(report.get('name', ''))} | "
            f"{'PASS' if report.get('passed') else 'FAIL'} | "
            f"{_markdown_cell(item_summary.get('source', ''))} | "
            f"{_int(item_summary.get('generated_candidates', 0))} | "
            f"{_markdown_cell(item_summary.get('diagnostics_status', ''))} | "
            f"{_markdown_cell(item_summary.get('benchmarkization_status', ''))} | "
            f"{_markdown_cell(item_summary.get('benchmarkization_primary_action_id', ''))} | "
            f"{_float(item_summary.get('top1', 0.0)):.4f} | "
            f"{_float(item_summary.get('map', 0.0)):.4f} | "
            f"{_float(item_summary.get('patch_success_rate', 0.0)):.4f} | "
            f"{_markdown_cell(', '.join(failed_checks))} |"
        )
    if not suite.reports:
        lines.append(
            "| none | FAIL |  | 0 |  |  |  | 0.0000 | 0.0000 | 0.0000 | no reports |"
        )
    remediation_plan_runs = _dict(summary.get("benchmarkization_remediation_plan_runs"))
    if remediation_plan_runs:
        lines.extend(
            [
                "",
                "## Benchmarkization Remediation Plans",
                "",
                "| Name | Plan |",
                "| --- | --- |",
            ]
        )
        for name, path in sorted(remediation_plan_runs.items()):
            lines.append(
                "| "
                f"{_markdown_cell(name)} | "
                f"{_markdown_cell(path)} |"
            )
    return "\n".join(lines)


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    benchmark_run = _dict(payload.get("benchmark_run"))
    benchmark_summary = _dict(benchmark_run.get("summary"))
    quality_gate = _dict(payload.get("quality_gate"))
    diagnostics = _dict(payload.get("diagnostics"))
    diagnostics_summary = _dict(diagnostics.get("summary"))
    diagnostics_headline = _dict(diagnostics.get("headline"))
    benchmarkization = _dict(payload.get("benchmarkization_readiness"))
    remediation_plan = _dict(benchmarkization.get("remediation_plan"))
    output_paths = _dict(payload.get("output_paths"))
    benchmarkization_status = str(
        benchmarkization.get("status")
        or diagnostics_summary.get("benchmarkization_status")
        or ""
    )
    benchmarkization_ready = (
        bool(benchmarkization.get("ready"))
        if "ready" in benchmarkization
        else bool(diagnostics_summary.get("benchmarkization_ready", False))
    )
    return {
        "source": payload.get("source", ""),
        "mode": payload.get("mode", ""),
        "preset": payload.get("preset", ""),
        "generated_candidates": _int(payload.get("generated_candidate_count", 0)),
        "quality_gate_passed": quality_gate.get("passed")
        if quality_gate
        else None,
        "diagnostics_status": diagnostics_headline.get("status", "missing"),
        "benchmarkization_status": benchmarkization_status,
        "benchmarkization_stage": str(benchmarkization.get("stage") or ""),
        "benchmarkization_ready": benchmarkization_ready,
        "benchmarkization_primary_action_id": str(
            remediation_plan.get("primary_action_id") or ""
        ),
        "benchmarkization_auto_runnable_action_count": _int(
            remediation_plan.get("auto_runnable_action_count", 0)
        ),
        "benchmarkization_manual_action_count": _int(
            remediation_plan.get("manual_action_count", 0)
        ),
        "benchmarkization_remediation_plan_json": str(
            output_paths.get("benchmarkization_remediation_plan_json") or ""
        ),
        "benchmarkization_remediation_plan_markdown": str(
            output_paths.get("benchmarkization_remediation_plan_markdown") or ""
        ),
        "benchmark_cases": _int(benchmark_summary.get("case_count", 0)),
        "top1": _float(benchmark_summary.get("top1", 0.0)),
        "map": _float(benchmark_summary.get("map", 0.0)),
        "patch_success_rate": _float(
            benchmark_summary.get("patch_success_rate", 0.0)
        ),
    }


def _suite_summary(reports: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = [_dict(report.get("summary")) for report in reports]
    benchmark_summaries = [
        summary for summary in summaries if _int(summary.get("benchmark_cases", 0)) > 0
    ]
    report_count = len(reports)
    passed_count = sum(1 for report in reports if report.get("passed"))
    diagnostics_status_counts: dict[str, int] = {}
    benchmarkization_status_counts: dict[str, int] = {}
    benchmarkization_status_runs: dict[str, list[str]] = {}
    benchmarkization_stage_counts: dict[str, int] = {}
    benchmarkization_stage_runs: dict[str, list[str]] = {}
    benchmarkization_primary_action_counts: dict[str, int] = {}
    benchmarkization_primary_action_runs: dict[str, list[str]] = {}
    benchmarkization_remediation_plan_runs: dict[str, str] = {}
    benchmarkization_ready_runs: list[str] = []
    for report in reports:
        summary = _dict(report.get("summary"))
        run_name = str(report.get("name") or summary.get("source") or "")
        status = str(summary.get("diagnostics_status", "missing"))
        diagnostics_status_counts[status] = diagnostics_status_counts.get(status, 0) + 1
        benchmarkization_status = str(
            summary.get("benchmarkization_status") or ""
        ).strip()
        if benchmarkization_status:
            _add_counted_run(
                benchmarkization_status_counts,
                benchmarkization_status_runs,
                benchmarkization_status,
                run_name,
            )
        benchmarkization_stage = str(
            summary.get("benchmarkization_stage") or ""
        ).strip()
        if benchmarkization_stage:
            _add_counted_run(
                benchmarkization_stage_counts,
                benchmarkization_stage_runs,
                benchmarkization_stage,
                run_name,
            )
        primary_action = str(
            summary.get("benchmarkization_primary_action_id") or ""
        ).strip()
        if primary_action:
            _add_counted_run(
                benchmarkization_primary_action_counts,
                benchmarkization_primary_action_runs,
                primary_action,
                run_name,
            )
        remediation_plan_path = str(
            summary.get("benchmarkization_remediation_plan_markdown") or ""
        ).strip()
        if remediation_plan_path:
            benchmarkization_remediation_plan_runs[run_name] = remediation_plan_path
        if bool(summary.get("benchmarkization_ready", False)):
            benchmarkization_ready_runs.append(run_name)
    return {
        "report_count": report_count,
        "passed_count": passed_count,
        "failed_count": report_count - passed_count,
        "pass_rate": _ratio(passed_count, report_count),
        "generated_candidates": sum(
            _int(summary.get("generated_candidates", 0)) for summary in summaries
        ),
        "benchmark_cases": sum(
            _int(summary.get("benchmark_cases", 0)) for summary in summaries
        ),
        "benchmark_report_count": len(benchmark_summaries),
        "average_top1": _average(
            [_float(summary.get("top1", 0.0)) for summary in benchmark_summaries]
        ),
        "average_map": _average(
            [_float(summary.get("map", 0.0)) for summary in benchmark_summaries]
        ),
        "average_patch_success_rate": _average(
            [
                _float(summary.get("patch_success_rate", 0.0))
                for summary in benchmark_summaries
            ]
        ),
        "diagnostics_status_counts": dict(sorted(diagnostics_status_counts.items())),
        "benchmarkization_ready_count": len(benchmarkization_ready_runs),
        "benchmarkization_ready_runs": sorted(benchmarkization_ready_runs),
        "benchmarkization_status_counts": dict(
            sorted(benchmarkization_status_counts.items())
        ),
        "benchmarkization_status_runs": {
            status: sorted(names)
            for status, names in sorted(benchmarkization_status_runs.items())
        },
        "benchmarkization_stage_counts": dict(
            sorted(benchmarkization_stage_counts.items())
        ),
        "benchmarkization_stage_runs": {
            stage: sorted(names)
            for stage, names in sorted(benchmarkization_stage_runs.items())
        },
        "benchmarkization_primary_action_counts": dict(
            sorted(benchmarkization_primary_action_counts.items())
        ),
        "benchmarkization_primary_action_runs": {
            action: sorted(names)
            for action, names in sorted(benchmarkization_primary_action_runs.items())
        },
        "benchmarkization_auto_runnable_action_count": sum(
            _int(summary.get("benchmarkization_auto_runnable_action_count", 0))
            for summary in summaries
        ),
        "benchmarkization_manual_action_count": sum(
            _int(summary.get("benchmarkization_manual_action_count", 0))
            for summary in summaries
        ),
        "benchmarkization_remediation_plan_count": len(
            benchmarkization_remediation_plan_runs
        ),
        "benchmarkization_remediation_plan_runs": dict(
            sorted(benchmarkization_remediation_plan_runs.items())
        ),
    }


def _add_counted_run(
    counts: dict[str, int],
    runs: dict[str, list[str]],
    key: str,
    run_name: str,
) -> None:
    counts[key] = counts.get(key, 0) + 1
    runs.setdefault(key, []).append(run_name)


def _manifest_entries(payload: dict[str, Any]) -> list[Any]:
    for key in ("reports", "runs", "items"):
        values = payload.get(key)
        if isinstance(values, list):
            return values
    return []


def _thresholds_from_dict(
    payload: dict[str, Any],
    *,
    base: OnboardingSmokeThresholds | None = None,
) -> OnboardingSmokeThresholds:
    base = base or OnboardingSmokeThresholds()
    values = base.to_dict()
    for key, value in payload.items():
        if key in values:
            values[key] = value
    return OnboardingSmokeThresholds(
        min_generated_candidates=_int(values.get("min_generated_candidates", 1)),
        require_quality_gate=bool(values.get("require_quality_gate", True)),
        require_benchmark_run=bool(values.get("require_benchmark_run", True)),
        min_benchmark_cases=_int(values.get("min_benchmark_cases", 1)),
        min_top1=_float(values.get("min_top1", 0.50)),
        min_map=_float(values.get("min_map", 0.50)),
        min_patch_success_rate=_float(values.get("min_patch_success_rate", 0.50)),
        allowed_diagnostics_statuses=tuple(
            str(status)
            for status in values.get("allowed_diagnostics_statuses", ("pass", "warning"))
        ),
        required_artifacts=tuple(
            str(artifact)
            for artifact in values.get("required_artifacts", DEFAULT_REQUIRED_ARTIFACTS)
        ),
    )


def _resolve_manifest_path(path_text: str, *, base_dir: Path) -> Path:
    path = Path(path_text)
    if path.exists() or path.is_absolute():
        return path
    return base_dir / path


def _missing_artifacts(
    output_paths: dict[str, Any],
    *,
    report_path: Path,
    artifact_names: tuple[str, ...],
) -> list[str]:
    missing = []
    for name in artifact_names:
        raw_path = output_paths.get(name)
        if not raw_path:
            missing.append(f"{name}:missing_path")
            continue
        if not _artifact_exists(str(raw_path), report_path=report_path):
            missing.append(f"{name}:{raw_path}")
    return missing


def _artifact_exists(path_text: str, *, report_path: Path) -> bool:
    candidate = Path(path_text)
    if candidate.exists():
        return True
    if not candidate.is_absolute() and (report_path.parent / candidate).exists():
        return True
    return False


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 6)


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 6)


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={_int(value)}" for key, value in sorted(counts.items()))


def _int_check(name: str, actual: int, minimum: int) -> OnboardingSmokeCheck:
    return OnboardingSmokeCheck(
        name=name,
        passed=actual >= minimum,
        expected=f">= {minimum}",
        actual=str(actual),
    )


def _float_check(
    name: str,
    actual: float,
    minimum: float,
) -> OnboardingSmokeCheck:
    return OnboardingSmokeCheck(
        name=name,
        passed=actual >= minimum,
        expected=f">= {minimum:.4f}",
        actual=f"{actual:.4f}",
    )


def _bool_check(
    name: str,
    passed: bool,
    expected: str,
    actual: str,
) -> OnboardingSmokeCheck:
    return OnboardingSmokeCheck(
        name=name,
        passed=passed,
        expected=expected,
        actual=actual,
    )


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


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a GitHub onboarding smoke onboarding_report.json."
    )
    parser.add_argument("report", help="Path to onboarding_report.json.")
    parser.add_argument(
        "--min-generated-candidates",
        type=int,
        default=1,
    )
    parser.add_argument("--no-require-quality-gate", action="store_true")
    parser.add_argument("--no-require-benchmark-run", action="store_true")
    parser.add_argument("--min-benchmark-cases", type=int, default=1)
    parser.add_argument("--min-top1", type=float, default=0.50)
    parser.add_argument("--min-map", type=float, default=0.50)
    parser.add_argument("--min-patch-success-rate", type=float, default=0.50)
    parser.add_argument(
        "--allow-diagnostics-status",
        action="append",
        choices=["pass", "warning", "fail"],
        help=(
            "Allowed onboarding_diagnostics status. May be repeated. Defaults "
            "to pass and warning."
        ),
    )
    parser.add_argument(
        "--require-artifact",
        action="append",
        help=(
            "Additional output_paths artifact key that must exist. Defaults "
            "already require core smoke artifacts."
        ),
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
    )
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    return parser


def build_manifest_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate multiple GitHub onboarding smoke reports from a manifest."
    )
    parser.add_argument("manifest", help="Path to onboarding smoke manifest JSON.")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
    )
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    return parser


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "manifest":
        _main_manifest(argv[1:])
        return
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    allowed_statuses = tuple(args.allow_diagnostics_status or ["pass", "warning"])
    required_artifacts = tuple(
        list(DEFAULT_REQUIRED_ARTIFACTS) + list(args.require_artifact or [])
    )
    thresholds = OnboardingSmokeThresholds(
        min_generated_candidates=args.min_generated_candidates,
        require_quality_gate=not args.no_require_quality_gate,
        require_benchmark_run=not args.no_require_benchmark_run,
        min_benchmark_cases=args.min_benchmark_cases,
        min_top1=args.min_top1,
        min_map=args.min_map,
        min_patch_success_rate=args.min_patch_success_rate,
        allowed_diagnostics_statuses=allowed_statuses,
        required_artifacts=required_artifacts,
    )
    report = validate_onboarding_smoke_report(args.report, thresholds=thresholds)
    json_payload = json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
    markdown = render_onboarding_smoke_validation_markdown(report)
    if args.output_json:
        Path(args.output_json).write_text(json_payload, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown, encoding="utf-8")
    if args.format == "json":
        print(json_payload)
    else:
        print(markdown)
    raise SystemExit(0 if report.passed else 1)


def _main_manifest(argv: list[str]) -> None:
    parser = build_manifest_arg_parser()
    args = parser.parse_args(argv)
    suite = validate_onboarding_smoke_manifest(args.manifest)
    json_payload = json.dumps(suite.to_dict(), indent=2, ensure_ascii=False)
    markdown = render_onboarding_smoke_suite_markdown(suite)
    if args.output_json:
        Path(args.output_json).write_text(json_payload, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown, encoding="utf-8")
    if args.format == "json":
        print(json_payload)
    else:
        print(markdown)
    raise SystemExit(0 if suite.passed else 1)


if __name__ == "__main__":
    main()
