from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.github_benchmark_onboarding import (
    main as onboarding_main,
)
from code_intelligence_agent.evaluation.onboarding_smoke_validator import (
    OnboardingSmokeSuiteReport,
    render_onboarding_smoke_suite_markdown,
    validate_onboarding_smoke_manifest,
)


@dataclass(frozen=True)
class OnboardingSmokeRunResult:
    name: str
    mode: str
    output_dir: str
    report_path: str
    passed: bool
    command_args: list[str]
    min_generated_candidates: int = 1
    error: str | None = None
    stdout_preview: str = ""
    fallback_attempted: bool = False
    fallback_used: bool = False
    fallback_reason: str | None = None
    fallback_min_generated_candidates: int | None = None
    primary_output_dir: str | None = None
    primary_report_path: str | None = None
    primary_command_args: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OnboardingSmokeRunnerReport:
    manifest_path: str
    output_dir: str
    suite_name: str
    passed: bool
    generated_manifest_path: str
    validation_json_path: str
    validation_markdown_path: str
    gap_summary_json_path: str
    gap_summary_markdown_path: str
    recommended_manifest_path: str
    summary: dict[str, Any]
    gap_summary: dict[str, Any]
    runs: list[OnboardingSmokeRunResult]
    suite_validation: OnboardingSmokeSuiteReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_path": self.manifest_path,
            "output_dir": self.output_dir,
            "suite_name": self.suite_name,
            "passed": self.passed,
            "generated_manifest_path": self.generated_manifest_path,
            "validation_json_path": self.validation_json_path,
            "validation_markdown_path": self.validation_markdown_path,
            "gap_summary_json_path": self.gap_summary_json_path,
            "gap_summary_markdown_path": self.gap_summary_markdown_path,
            "recommended_manifest_path": self.recommended_manifest_path,
            "summary": self.summary,
            "gap_summary": self.gap_summary,
            "runs": [run.to_dict() for run in self.runs],
            "suite_validation": self.suite_validation.to_dict(),
        }


def run_onboarding_smoke_suite(
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    opener=None,
) -> OnboardingSmokeRunnerReport:
    manifest = Path(manifest_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    suite_name = str(payload.get("suite_name") or payload.get("name") or manifest.stem)
    defaults = _dict(payload.get("defaults"))
    suite_thresholds = _dict(payload.get("thresholds"))
    runs = _manifest_runs(payload)
    run_results: list[OnboardingSmokeRunResult] = []
    validation_entries: list[dict[str, Any]] = []
    shared_cache = output_root / "source_cache"

    for index, entry_value in enumerate(runs):
        entry = _dict(entry_value)
        options = {**defaults, **entry}
        name = _run_name(options, index)
        mode = str(options.get("mode") or "repo")
        thresholds = {**suite_thresholds, **_dict(entry.get("thresholds"))}
        min_generated_candidates = _min_generated_candidates(thresholds)
        run_output = _run_output_dir(options, output_root=output_root, name=name)
        options.setdefault("source_cache_dir", str(shared_cache))
        options.setdefault("format", "json")
        result = _execute_onboarding_run(
            name=name,
            mode=mode,
            options=options,
            output_dir=run_output,
            manifest_dir=manifest.parent,
            opener=opener,
        )
        result = replace(
            result,
            min_generated_candidates=min_generated_candidates,
        )
        fallback_reason = _fallback_reason(
            result,
            options,
            min_generated_candidates=min_generated_candidates,
        )
        if fallback_reason:
            fallback_options = _fallback_options(options)
            fallback_output = _fallback_output_dir(
                options,
                primary_output_dir=run_output,
            )
            fallback_result = _execute_onboarding_run(
                name=name,
                mode=mode,
                options=fallback_options,
                output_dir=fallback_output,
                manifest_dir=manifest.parent,
                opener=opener,
            )
            result = replace(
                fallback_result,
                fallback_attempted=True,
                fallback_used=True,
                fallback_reason=fallback_reason,
                fallback_min_generated_candidates=min_generated_candidates,
                min_generated_candidates=min_generated_candidates,
                primary_output_dir=str(run_output),
                primary_report_path=result.report_path,
                primary_command_args=result.command_args,
            )

        report_path = Path(result.report_path)
        if report_path.exists():
            validation_entry: dict[str, Any] = {
                "name": name,
                "mode": mode,
                "report_path": result.report_path,
            }
            if result.fallback_attempted:
                validation_entry["fallback"] = {
                    "attempted": result.fallback_attempted,
                    "used": result.fallback_used,
                    "reason": result.fallback_reason,
                    "primary_report_path": result.primary_report_path,
                }
            if "thresholds" in entry:
                validation_entry["thresholds"] = entry["thresholds"]
            validation_entries.append(validation_entry)
        run_results.append(result)

    generated_manifest = {
        "suite_name": suite_name,
        "description": payload.get("description", ""),
        "thresholds": _dict(payload.get("thresholds")),
        "reports": validation_entries,
    }
    generated_manifest_path = output_root / "onboarding_smoke_manifest.json"
    generated_manifest_path.write_text(
        json.dumps(generated_manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    suite_validation = validate_onboarding_smoke_manifest(generated_manifest_path)
    validation_json_path = output_root / "onboarding_smoke_suite.json"
    validation_markdown_path = output_root / "onboarding_smoke_suite.md"
    validation_json_path.write_text(
        json.dumps(suite_validation.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    validation_markdown_path.write_text(
        render_onboarding_smoke_suite_markdown(suite_validation),
        encoding="utf-8",
    )
    failed_runs = [run for run in run_results if not run.passed]
    gap_summary = build_onboarding_smoke_gap_summary(
        run_results,
        suite_validation,
    )
    gap_summary_json_path = output_root / "onboarding_smoke_gaps.json"
    gap_summary_markdown_path = output_root / "onboarding_smoke_gaps.md"
    gap_summary_json_path.write_text(
        json.dumps(gap_summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    gap_summary_markdown_path.write_text(
        render_onboarding_smoke_gap_markdown(gap_summary),
        encoding="utf-8",
    )
    recommended_manifest = build_onboarding_recommended_manifest(
        payload,
        gap_summary,
        source_manifest_dir=manifest.parent,
        recommended_manifest_dir=output_root,
    )
    recommended_manifest_path = output_root / "onboarding_smoke_recommended_manifest.json"
    recommended_manifest_path.write_text(
        json.dumps(recommended_manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    summary = {
        "run_count": len(run_results),
        "completed_count": len(run_results) - len(failed_runs),
        "failed_count": len(failed_runs),
        "fallback_attempted_count": gap_summary["headline"][
            "fallback_attempted_runs"
        ],
        "fallback_improved_count": gap_summary["headline"][
            "fallback_improved_runs"
        ],
        "fallback_recovered_count": gap_summary["headline"][
            "fallback_recovered_runs"
        ],
        "validation_passed": suite_validation.passed,
        "validation_report_count": suite_validation.summary.get("report_count", 0),
        "validation_pass_rate": suite_validation.summary.get("pass_rate", 0.0),
        "generated_candidates": suite_validation.summary.get(
            "generated_candidates", 0
        ),
        "benchmark_cases": suite_validation.summary.get("benchmark_cases", 0),
        **_smoke_static_intelligence_summary(run_results),
        **_smoke_benchmarkization_summary(suite_validation.summary),
        "gap_status": gap_summary["headline"]["status"],
        "gap_action_count": len(gap_summary["next_actions"]),
        "manifest_recommendation_count": len(
            gap_summary.get("manifest_recommendations", [])
        ),
    }
    passed = not failed_runs and suite_validation.passed
    return OnboardingSmokeRunnerReport(
        manifest_path=str(manifest),
        output_dir=str(output_root),
        suite_name=suite_name,
        passed=passed,
        generated_manifest_path=str(generated_manifest_path),
        validation_json_path=str(validation_json_path),
        validation_markdown_path=str(validation_markdown_path),
        gap_summary_json_path=str(gap_summary_json_path),
        gap_summary_markdown_path=str(gap_summary_markdown_path),
        recommended_manifest_path=str(recommended_manifest_path),
        summary=summary,
        gap_summary=gap_summary,
        runs=run_results,
        suite_validation=suite_validation,
    )


def _execute_onboarding_run(
    *,
    name: str,
    mode: str,
    options: dict[str, Any],
    output_dir: Path,
    manifest_dir: Path,
    opener: Any,
) -> OnboardingSmokeRunResult:
    command_args: list[str] = []
    stdout_buffer = io.StringIO()
    error: str | None = None
    try:
        command_args = _onboarding_args(
            options,
            mode=mode,
            output_dir=output_dir,
            manifest_dir=manifest_dir,
        )
        with contextlib.redirect_stdout(stdout_buffer):
            onboarding_main(command_args, opener=opener)
        passed = True
    except SystemExit as exc:
        passed = False
        error = f"SystemExit({exc.code})"
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        passed = False
        error = f"{type(exc).__name__}: {exc}"

    report_path = output_dir / "onboarding_report.json"
    if passed and not report_path.exists():
        passed = False
        error = "onboarding_report.json was not written"
    return OnboardingSmokeRunResult(
        name=name,
        mode=mode,
        output_dir=str(output_dir),
        report_path=str(report_path),
        passed=passed,
        command_args=command_args,
        error=error,
        stdout_preview=_preview(stdout_buffer.getvalue()),
    )


def _smoke_benchmarkization_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "benchmarkization_ready_count": _int(
            summary.get("benchmarkization_ready_count", 0)
        ),
        "benchmarkization_ready_runs": _list(
            summary.get("benchmarkization_ready_runs")
        ),
        "benchmarkization_status_counts": _dict(
            summary.get("benchmarkization_status_counts")
        ),
        "benchmarkization_status_runs": _dict(
            summary.get("benchmarkization_status_runs")
        ),
        "benchmarkization_stage_counts": _dict(
            summary.get("benchmarkization_stage_counts")
        ),
        "benchmarkization_stage_runs": _dict(
            summary.get("benchmarkization_stage_runs")
        ),
        "benchmarkization_primary_action_counts": _dict(
            summary.get("benchmarkization_primary_action_counts")
        ),
        "benchmarkization_primary_action_runs": _dict(
            summary.get("benchmarkization_primary_action_runs")
        ),
        "benchmarkization_auto_runnable_action_count": _int(
            summary.get("benchmarkization_auto_runnable_action_count", 0)
        ),
        "benchmarkization_manual_action_count": _int(
            summary.get("benchmarkization_manual_action_count", 0)
        ),
        "benchmarkization_remediation_plan_count": _int(
            summary.get("benchmarkization_remediation_plan_count", 0)
        ),
        "benchmarkization_remediation_plan_runs": _dict(
            summary.get("benchmarkization_remediation_plan_runs")
        ),
    }


def _smoke_static_intelligence_summary(
    runs: list[OnboardingSmokeRunResult],
) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    status_runs: dict[str, list[str]] = {}
    level_counts: dict[str, int] = {}
    level_runs: dict[str, list[str]] = {}
    reason_counts: dict[str, int] = {}
    reason_runs: dict[str, list[str]] = {}
    dynamic_level_counts: dict[str, int] = {}
    dynamic_level_runs: dict[str, list[str]] = {}
    rule_counts: dict[str, int] = {}
    bug_type_counts: dict[str, int] = {}
    artifact_runs: dict[str, str] = {}
    run_summaries: list[dict[str, Any]] = []
    quality_scores: list[float] = []
    report_count = 0
    analysis_ready_count = 0
    source_inventory_ready_count = 0
    blocked_count = 0
    selected_signal_count = 0
    total_signal_count = 0
    candidate_limit_applied_count = 0

    for run in runs:
        payload = _read_json(run.report_path)
        if not payload:
            continue
        report_count += 1
        mining_report = _dict(payload.get("mining_report"))
        mining_quality = _dict(mining_report.get("quality_summary"))
        quality_summary = _dict(payload.get("quality_summary")) or mining_quality
        imported_sources = _int(payload.get("imported_source_count", 0))
        selected_sources = _int(payload.get("selected_source_count", 0))
        selected_signals = _int(payload.get("generated_candidate_count", 0))
        total_signals = _int(mining_report.get("generated_count", 0)) or selected_signals
        dynamic_evidence = _dict(payload.get("repository_test_dynamic_evidence"))
        dynamic_level = str(
            dynamic_evidence.get("evidence_level")
            or payload.get("repository_test_dynamic_evidence_level")
            or "none"
        )
        if not dynamic_level:
            dynamic_level = "none"
        run_rule_counts = _dict(mining_report.get("rule_counts"))
        run_bug_type_counts = _dict(mining_report.get("bug_type_counts"))
        quality_score = _float(
            quality_summary.get(
                "quality_score",
                mining_quality.get("quality_score", 0.0),
            )
        )
        if imported_sources <= 0:
            status = "blocked"
            level = "no_python_sources"
            reason = "no_imported_sources"
            blocked_count += 1
        elif selected_sources <= 0:
            status = "blocked"
            level = "no_selected_sources"
            reason = "source_selection_empty"
            blocked_count += 1
        elif total_signals > 0:
            status = "analysis_ready"
            level = (
                "static_signals_with_dynamic_evidence"
                if dynamic_level not in {"", "not_executed", "none"}
                else "static_signals"
            )
            reason = "mined_static_candidates"
            analysis_ready_count += 1
        else:
            status = "source_inventory_ready"
            level = "source_only"
            reason = "no_static_candidates"
            source_inventory_ready_count += 1

        candidate_limit_applied = bool(
            quality_summary.get("candidate_limit_applied", False)
        ) or selected_signals < total_signals
        if candidate_limit_applied:
            candidate_limit_applied_count += 1
        output_paths = _dict(payload.get("output_paths"))
        primary_artifact = str(output_paths.get("source_mining_markdown") or "")
        if primary_artifact:
            artifact_runs[run.name] = primary_artifact
        selected_signal_count += selected_signals
        total_signal_count += total_signals
        if quality_score > 0.0:
            quality_scores.append(quality_score)
        _increment(status_counts, status)
        status_runs.setdefault(status, []).append(run.name)
        _increment(level_counts, level)
        level_runs.setdefault(level, []).append(run.name)
        _increment(reason_counts, reason)
        reason_runs.setdefault(reason, []).append(run.name)
        _increment(dynamic_level_counts, dynamic_level)
        dynamic_level_runs.setdefault(dynamic_level, []).append(run.name)
        for rule, count in run_rule_counts.items():
            key = str(rule)
            rule_counts[key] = rule_counts.get(key, 0) + _int(count)
        for bug_type, count in run_bug_type_counts.items():
            key = str(bug_type)
            bug_type_counts[key] = bug_type_counts.get(key, 0) + _int(count)
        run_summaries.append(
            {
                "name": run.name,
                "status": status,
                "level": level,
                "reason": reason,
                "imported_source_count": imported_sources,
                "selected_source_count": selected_sources,
                "selected_signal_count": selected_signals,
                "total_signal_count": total_signals,
                "candidate_limit_applied": candidate_limit_applied,
                "quality_score": quality_score,
                "dynamic_validation_level": dynamic_level,
                "primary_artifact": primary_artifact,
            }
        )

    return {
        "static_intelligence_run_count": report_count,
        "static_intelligence_analysis_ready_count": analysis_ready_count,
        "static_intelligence_source_inventory_ready_count": (
            source_inventory_ready_count
        ),
        "static_intelligence_blocked_count": blocked_count,
        "static_intelligence_selected_signal_count": selected_signal_count,
        "static_intelligence_total_signal_count": total_signal_count,
        "static_intelligence_candidate_limit_applied_count": (
            candidate_limit_applied_count
        ),
        "static_intelligence_average_quality_score": (
            sum(quality_scores) / len(quality_scores) if quality_scores else 0.0
        ),
        "static_intelligence_status_counts": status_counts,
        "static_intelligence_status_runs": status_runs,
        "static_intelligence_level_counts": level_counts,
        "static_intelligence_level_runs": level_runs,
        "static_intelligence_reason_counts": reason_counts,
        "static_intelligence_reason_runs": reason_runs,
        "static_intelligence_rule_counts": rule_counts,
        "static_intelligence_bug_type_counts": bug_type_counts,
        "static_intelligence_dynamic_validation_level_counts": (
            dynamic_level_counts
        ),
        "static_intelligence_dynamic_validation_level_runs": dynamic_level_runs,
        "static_intelligence_primary_artifact_runs": artifact_runs,
        "static_intelligence_run_summaries": run_summaries,
    }


def _fallback_reason(
    result: OnboardingSmokeRunResult,
    options: dict[str, Any],
    *,
    min_generated_candidates: int,
) -> str | None:
    if not _fallback_enabled(options):
        return None
    if not result.passed:
        return None
    report_payload = _read_json(result.report_path)
    if not report_payload:
        return None
    generated_candidates = _int(report_payload.get("generated_candidate_count", 0))
    if generated_candidates <= 0:
        return "no_generated_candidates"
    if generated_candidates < min_generated_candidates:
        return "low_generated_candidates"
    return None


def _min_generated_candidates(thresholds: dict[str, Any]) -> int:
    if "min_generated_candidates" in thresholds:
        return max(1, _int(thresholds.get("min_generated_candidates")))
    return 1


def _fallback_enabled(options: dict[str, Any]) -> bool:
    fallback = options.get("fallback")
    if isinstance(fallback, dict) and "enabled" in fallback:
        return _bool(fallback.get("enabled"))
    if fallback is not None and not isinstance(fallback, dict):
        return _bool(fallback)
    if "auto_fallback" in options:
        return _bool(options.get("auto_fallback"))
    return True


def _fallback_options(options: dict[str, Any]) -> dict[str, Any]:
    fallback_config = _dict(options.get("fallback"))
    fallback = dict(options)
    fallback.pop("fallback", None)
    fallback["preset"] = str(fallback_config.get("preset") or "smoke")
    fallback["max_sources"] = _fallback_limit(
        fallback_config.get("max_sources"),
        options.get("max_sources"),
        default=50,
    )
    fallback["max_candidates"] = _fallback_limit(
        fallback_config.get("max_candidates"),
        options.get("max_candidates"),
        default=20,
    )
    if "recipe" in fallback_config or "recipes" in fallback_config:
        fallback.pop("recipe", None)
        fallback.pop("recipes", None)
        recipe_value = fallback_config.get("recipe", fallback_config.get("recipes"))
        if recipe_value is not None:
            fallback["recipe"] = recipe_value
    else:
        fallback.pop("recipe", None)
        fallback.pop("recipes", None)
    for key, value in fallback_config.items():
        if key in {"enabled", "output_dir", "recipe", "recipes"}:
            continue
        if value is None:
            fallback.pop(key, None)
        else:
            fallback[key] = value
    return fallback


def _fallback_limit(explicit: Any, current: Any, *, default: int) -> int:
    if explicit is not None:
        return _int(explicit)
    current_value = _int(current)
    return max(current_value, default) if current_value > 0 else default


def _fallback_output_dir(
    options: dict[str, Any],
    *,
    primary_output_dir: Path,
) -> Path:
    fallback_config = _dict(options.get("fallback"))
    raw_output = fallback_config.get("output_dir")
    if raw_output:
        path = Path(str(raw_output))
        if path.is_absolute():
            return path
        return primary_output_dir / path
    return primary_output_dir / "fallback"


def build_onboarding_smoke_gap_summary(
    runs: list[OnboardingSmokeRunResult],
    suite_validation: OnboardingSmokeSuiteReport,
) -> dict[str, Any]:
    validation_by_name = {
        str(report.get("name", "")): _dict(report)
        for report in suite_validation.reports
        if isinstance(report, dict)
    }
    run_outcomes: list[dict[str, Any]] = []
    command_error_counts: dict[str, int] = {}
    validation_failed_check_counts: dict[str, int] = {}
    diagnostic_status_counts: dict[str, int] = {}
    diagnostic_issue_counts: dict[str, int] = {}
    first_failing_stage_counts: dict[str, int] = {}
    recipe_reason_counts: dict[str, int] = {}
    suggested_actions: list[str] = []
    fallback_attempted_runs = 0
    fallback_recovered_runs = 0
    fallback_improved_runs = 0
    manifest_recommendations: list[dict[str, Any]] = []

    for report in suite_validation.reports:
        item = _dict(report)
        for check in _list(item.get("failed_checks")):
            check_name = str(_dict(check).get("name", "unknown"))
            _increment(validation_failed_check_counts, check_name)

    for run in runs:
        validation = validation_by_name.get(run.name, {})
        report_payload = _read_json(run.report_path)
        primary_payload = _read_json(run.primary_report_path or run.report_path)
        diagnostics = _dict(report_payload.get("diagnostics"))
        diagnostics_headline = _dict(diagnostics.get("headline"))
        status = str(diagnostics_headline.get("status", "missing"))
        _increment(diagnostic_status_counts, status)
        first_stage = str(diagnostics_headline.get("first_failing_stage") or "")
        if first_stage:
            _increment(first_failing_stage_counts, first_stage)
        for issue in _list(diagnostics.get("issues")):
            item = _dict(issue)
            code = str(item.get("code", "unknown"))
            _increment(diagnostic_issue_counts, code)
            for action in _list(item.get("next_steps")):
                _append_unique(suggested_actions, str(action))
        for suggestion in _list(diagnostics.get("recipe_suggestions")):
            item = _dict(suggestion)
            for reason in _list(item.get("top_reasons")):
                reason_name = str(_dict(reason).get("reason", "unknown"))
                _increment(recipe_reason_counts, reason_name)
            for action in _list(item.get("suggested_actions")):
                _append_unique(suggested_actions, str(action))

        validation_failed_checks = [
            str(_dict(check).get("name", "unknown"))
            for check in _list(validation.get("failed_checks"))
        ]
        final_generated_candidates = _int(
            report_payload.get("generated_candidate_count", 0)
        )
        primary_generated_candidates = _int(
            primary_payload.get("generated_candidate_count", 0)
        )
        min_generated_candidates = max(1, _int(run.min_generated_candidates))
        if run.fallback_attempted:
            fallback_attempted_runs += 1
        fallback_improved = (
            run.fallback_used
            and final_generated_candidates > primary_generated_candidates
        )
        if fallback_improved:
            fallback_improved_runs += 1
        fallback_recovered = (
            run.fallback_used
            and primary_generated_candidates < min_generated_candidates
            and final_generated_candidates >= min_generated_candidates
        )
        if fallback_recovered:
            fallback_recovered_runs += 1
        if not run.passed:
            outcome = "command_failed"
            _increment(command_error_counts, _error_kind(run.error))
        elif validation_failed_checks:
            outcome = "validation_failed"
        elif fallback_recovered:
            outcome = "fallback_recovered"
        elif status == "fail":
            outcome = "diagnostics_failed"
        elif status == "warning":
            outcome = "warning"
        else:
            outcome = "pass"
        run_outcomes.append(
            {
                "name": run.name,
                "mode": run.mode,
                "outcome": outcome,
                "command_error": run.error,
                "diagnostics_status": status,
                "first_failing_stage": first_stage,
                "generated_candidates": final_generated_candidates,
                "primary_generated_candidates": primary_generated_candidates,
                "min_generated_candidates": min_generated_candidates,
                "fallback_attempted": run.fallback_attempted,
                "fallback_used": run.fallback_used,
                "fallback_improved": fallback_improved,
                "fallback_recovered": fallback_recovered,
                "fallback_reason": run.fallback_reason,
                "fallback_min_generated_candidates": (
                    run.fallback_min_generated_candidates
                ),
                "primary_report_path": run.primary_report_path,
                "benchmark_cases": _int(
                    _dict(_dict(report_payload.get("benchmark_run")).get("summary")).get(
                        "case_count",
                        0,
                    )
                ),
                "failed_checks": validation_failed_checks,
                "report_path": run.report_path,
            }
        )
        if fallback_recovered:
            manifest_recommendations.append(
                _manifest_recommendation(
                    run,
                    primary_generated_candidates=primary_generated_candidates,
                    final_generated_candidates=final_generated_candidates,
                    min_generated_candidates=min_generated_candidates,
                )
            )

    command_failed_runs = sum(
        1 for outcome in run_outcomes if outcome["outcome"] == "command_failed"
    )
    validation_failed_reports = sum(
        1 for outcome in run_outcomes if outcome["outcome"] == "validation_failed"
    )
    diagnostics_fail_reports = sum(
        1 for outcome in run_outcomes if outcome["diagnostics_status"] == "fail"
    )
    no_candidate_reports = sum(
        1 for outcome in run_outcomes if _int(outcome["generated_candidates"]) == 0
    )
    status = "pass"
    if command_failed_runs or validation_failed_reports or diagnostics_fail_reports:
        status = "fail"
    elif fallback_recovered_runs:
        status = "warning"
    elif any(outcome["outcome"] == "warning" for outcome in run_outcomes):
        status = "warning"
    next_actions = _gap_next_actions(
        command_failed_runs=command_failed_runs,
        validation_failed_check_counts=validation_failed_check_counts,
        diagnostic_issue_counts=diagnostic_issue_counts,
        no_candidate_reports=no_candidate_reports,
        fallback_recovered_runs=fallback_recovered_runs,
        suggested_actions=suggested_actions,
    )
    return {
        "headline": {
            "status": status,
            "run_count": len(runs),
            "command_failed_runs": command_failed_runs,
            "validation_failed_reports": validation_failed_reports,
            "diagnostics_fail_reports": diagnostics_fail_reports,
            "no_candidate_reports": no_candidate_reports,
            "fallback_attempted_runs": fallback_attempted_runs,
            "fallback_improved_runs": fallback_improved_runs,
            "fallback_recovered_runs": fallback_recovered_runs,
            "top_validation_failed_check": _top_key(validation_failed_check_counts),
            "top_diagnostic_issue": _top_key(diagnostic_issue_counts),
            "top_recipe_miss_reason": _top_key(recipe_reason_counts),
        },
        "run_outcomes": run_outcomes,
        "command_error_counts": dict(sorted(command_error_counts.items())),
        "validation_failed_check_counts": dict(
            sorted(validation_failed_check_counts.items())
        ),
        "diagnostic_status_counts": dict(sorted(diagnostic_status_counts.items())),
        "diagnostic_issue_counts": dict(sorted(diagnostic_issue_counts.items())),
        "first_failing_stage_counts": dict(sorted(first_failing_stage_counts.items())),
        "recipe_miss_reason_counts": dict(sorted(recipe_reason_counts.items())),
        "manifest_recommendations": manifest_recommendations,
        "suggested_actions": suggested_actions,
        "next_actions": next_actions,
    }


def build_onboarding_recommended_manifest(
    manifest_payload: dict[str, Any],
    gap_summary: dict[str, Any],
    *,
    source_manifest_dir: str | Path | None = None,
    recommended_manifest_dir: str | Path | None = None,
) -> dict[str, Any]:
    recommended = _deepcopy_json(manifest_payload)
    recommendations = _list(gap_summary.get("manifest_recommendations"))
    run_key = _manifest_run_key(recommended)
    runs = _list(recommended.get(run_key)) if run_key else []
    defaults = _dict(recommended.get("defaults"))
    applied: list[dict[str, Any]] = []
    for recommendation_value in recommendations:
        recommendation = _dict(recommendation_value)
        target_name = str(recommendation.get("name", ""))
        target_index = _find_manifest_run_index(
            runs,
            target_name=target_name,
            defaults=defaults,
        )
        if target_index is None:
            continue
        run = _dict(runs[target_index])
        fallback = _dict(recommendation.get("recommended_fallback"))
        for key, value in fallback.items():
            if key == "enabled":
                continue
            run[key] = value
        for field in _string_list(recommendation.get("remove_primary_fields")):
            run.pop(field, None)
            if field == "recipe":
                run.pop("recipes", None)
        run["fallback"] = fallback
        runs[target_index] = run
        applied.append(
            {
                "name": target_name,
                "run_index": target_index,
                "fallback_reason": recommendation.get("fallback_reason"),
                "candidate_delta": recommendation.get("candidate_delta"),
                "remove_primary_fields": _string_list(
                    recommendation.get("remove_primary_fields")
                ),
            }
        )
    if run_key:
        runs = [
            _rebase_discovery_paths(
                _dict(run),
                source_manifest_dir=source_manifest_dir,
                recommended_manifest_dir=recommended_manifest_dir,
            )
            for run in runs
        ]
        recommended[run_key] = runs
    recommended["recommendation_metadata"] = {
        "source": "github_onboarding_smoke_runner",
        "applied_recommendation_count": len(applied),
        "applied_recommendations": applied,
        "note": (
            "Generated from fallback-recovered onboarding smoke runs. Review "
            "before replacing the source manifest."
        ),
    }
    return recommended


def _rebase_discovery_paths(
    run: dict[str, Any],
    *,
    source_manifest_dir: str | Path | None,
    recommended_manifest_dir: str | Path | None,
) -> dict[str, Any]:
    if source_manifest_dir is None or recommended_manifest_dir is None:
        return run
    source_dir = Path(source_manifest_dir)
    target_dir = Path(recommended_manifest_dir)
    for key in ("discovery", "discovery_path"):
        value = run.get(key)
        if not value:
            continue
        path = Path(str(value))
        if path.is_absolute():
            continue
        resolved = (source_dir / path).resolve()
        try:
            run[key] = os.path.relpath(resolved, target_dir.resolve())
        except ValueError:
            run[key] = str(resolved)
    return run


def render_onboarding_smoke_runner_markdown(
    report: OnboardingSmokeRunnerReport,
) -> str:
    summary = report.summary
    gap_headline = _dict(report.gap_summary.get("headline"))
    lines = [
        "# GitHub Onboarding Smoke Runner",
        "",
        f"- Manifest: `{report.manifest_path}`",
        f"- Output Dir: `{report.output_dir}`",
        f"- Suite: `{report.suite_name}`",
        f"- Result: {'PASS' if report.passed else 'FAIL'}",
        f"- Runs: {_int(summary.get('run_count', 0))}",
        f"- Completed Runs: {_int(summary.get('completed_count', 0))}",
        f"- Failed Runs: {_int(summary.get('failed_count', 0))}",
        f"- Fallback Attempted: {_int(summary.get('fallback_attempted_count', 0))}",
        f"- Fallback Improved: {_int(summary.get('fallback_improved_count', 0))}",
        f"- Fallback Recovered: {_int(summary.get('fallback_recovered_count', 0))}",
        f"- Suite Validation: {'PASS' if report.suite_validation.passed else 'FAIL'}",
        f"- Validation Pass Rate: {_float(summary.get('validation_pass_rate', 0.0)):.4f}",
        f"- Generated Candidates: {_int(summary.get('generated_candidates', 0))}",
        f"- Static Intelligence Runs: {_int(summary.get('static_intelligence_run_count', 0))}",
        f"- Static Intelligence Analysis Ready Runs: {_int(summary.get('static_intelligence_analysis_ready_count', 0))}",
        (
            "- Static Intelligence Signals: "
            f"{_int(summary.get('static_intelligence_selected_signal_count', 0))} selected / "
            f"{_int(summary.get('static_intelligence_total_signal_count', 0))} total"
        ),
        (
            "- Static Intelligence Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('static_intelligence_status_counts'))))}"
        ),
        (
            "- Static Intelligence Rules: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('static_intelligence_rule_counts'))))}"
        ),
        f"- Benchmark Cases: {_int(summary.get('benchmark_cases', 0))}",
        f"- Benchmarkization Ready Reports: {_int(summary.get('benchmarkization_ready_count', 0))}",
        (
            "- Benchmarkization Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('benchmarkization_status_counts'))))}"
        ),
        (
            "- Benchmarkization Actions: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('benchmarkization_primary_action_counts'))))}"
        ),
        f"- Benchmarkization Remediation Plans: {_int(summary.get('benchmarkization_remediation_plan_count', 0))}",
        f"- Gap Status: `{gap_headline.get('status', 'unknown')}`",
        f"- Gap Actions: {_int(summary.get('gap_action_count', 0))}",
        "",
        "## Runs",
        "",
        "| Name | Mode | Result | Fallback | Report | Error |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for run in report.runs:
        fallback_status = "recovered" if run.fallback_used else ""
        lines.append(
            "| "
            f"{_markdown_cell(run.name)} | "
            f"{_markdown_cell(run.mode)} | "
            f"{'PASS' if run.passed else 'FAIL'} | "
            f"{_markdown_cell(fallback_status)} | "
            f"{_markdown_cell(run.report_path)} | "
            f"{_markdown_cell(run.error or '')} |"
        )
    if not report.runs:
        lines.append("| none |  | FAIL |  |  | no runs |")
    lines.extend(
        [
            "",
            "## Generated Artifacts",
            "",
            f"- Suite Manifest: `{report.generated_manifest_path}`",
            f"- Suite JSON: `{report.validation_json_path}`",
            f"- Suite Markdown: `{report.validation_markdown_path}`",
            f"- Gap Summary JSON: `{report.gap_summary_json_path}`",
            f"- Gap Summary Markdown: `{report.gap_summary_markdown_path}`",
            f"- Recommended Manifest: `{report.recommended_manifest_path}`",
        ]
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


def render_onboarding_smoke_gap_markdown(gap_summary: dict[str, Any]) -> str:
    headline = _dict(gap_summary.get("headline"))
    lines = [
        "# GitHub Onboarding Smoke Gaps",
        "",
        "## Summary",
        "",
        f"- Status: `{headline.get('status', 'unknown')}`",
        f"- Runs: {_int(headline.get('run_count', 0))}",
        f"- Command Failed Runs: {_int(headline.get('command_failed_runs', 0))}",
        f"- Validation Failed Reports: {_int(headline.get('validation_failed_reports', 0))}",
        f"- Diagnostics Fail Reports: {_int(headline.get('diagnostics_fail_reports', 0))}",
        f"- No-Candidate Reports: {_int(headline.get('no_candidate_reports', 0))}",
        f"- Fallback Attempted Runs: {_int(headline.get('fallback_attempted_runs', 0))}",
        f"- Fallback Improved Runs: {_int(headline.get('fallback_improved_runs', 0))}",
        f"- Fallback Recovered Runs: {_int(headline.get('fallback_recovered_runs', 0))}",
        f"- Top Failed Check: `{headline.get('top_validation_failed_check', '')}`",
        f"- Top Diagnostic Issue: `{headline.get('top_diagnostic_issue', '')}`",
        f"- Top Recipe Miss Reason: `{headline.get('top_recipe_miss_reason', '')}`",
        "",
        "## Run Outcomes",
        "",
        "| Name | Outcome | Diagnostics | Required Candidates | Primary Candidates | Final Candidates | Benchmark Cases | Fallback | Failed Checks |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for outcome in _list(gap_summary.get("run_outcomes")):
        item = _dict(outcome)
        fallback_status = (
            "recovered"
            if item.get("fallback_recovered")
            else "attempted"
            if item.get("fallback_attempted")
            else ""
        )
        lines.append(
            "| "
            f"{_markdown_cell(item.get('name', ''))} | "
            f"{_markdown_cell(item.get('outcome', ''))} | "
            f"{_markdown_cell(item.get('diagnostics_status', ''))} | "
            f"{_int(item.get('min_generated_candidates', 1))} | "
            f"{_int(item.get('primary_generated_candidates', 0))} | "
            f"{_int(item.get('generated_candidates', 0))} | "
            f"{_int(item.get('benchmark_cases', 0))} | "
            f"{_markdown_cell(fallback_status)} | "
            f"{_markdown_cell(', '.join(_string_list(item.get('failed_checks'))))} |"
        )
    if not _list(gap_summary.get("run_outcomes")):
        lines.append("| none |  |  | 0 | 0 | 0 | 0 |  | no runs |")
    lines.extend(
        [
            "",
            "## Failure Counts",
            "",
            "| Kind | Counts |",
            "| --- | --- |",
            f"| Command Errors | {_markdown_cell(_format_counts(_dict(gap_summary.get('command_error_counts'))))} |",
            f"| Failed Checks | {_markdown_cell(_format_counts(_dict(gap_summary.get('validation_failed_check_counts'))))} |",
            f"| Diagnostic Issues | {_markdown_cell(_format_counts(_dict(gap_summary.get('diagnostic_issue_counts'))))} |",
            f"| Recipe Miss Reasons | {_markdown_cell(_format_counts(_dict(gap_summary.get('recipe_miss_reason_counts'))))} |",
        ]
    )
    next_actions = _list(gap_summary.get("next_actions"))
    if next_actions:
        lines.extend(["", "## Next Actions", ""])
        for action in next_actions:
            lines.append(f"- {_markdown_cell(action)}")
    recommendations = _list(gap_summary.get("manifest_recommendations"))
    if recommendations:
        lines.extend(
            [
                "",
                "## Manifest Recommendations",
                "",
                "| Run | Reason | Candidate Delta | Recommended Fallback | Remove Fields |",
                "| --- | --- | ---: | --- | --- |",
            ]
        )
        for recommendation_value in recommendations:
            recommendation = _dict(recommendation_value)
            lines.append(
                "| "
                f"{_markdown_cell(recommendation.get('name', ''))} | "
                f"{_markdown_cell(recommendation.get('fallback_reason', ''))} | "
                f"{_int(recommendation.get('candidate_delta', 0))} | "
                f"{_markdown_cell(json.dumps(_dict(recommendation.get('recommended_fallback')), ensure_ascii=False))} | "
                f"{_markdown_cell(', '.join(_string_list(recommendation.get('remove_primary_fields'))))} |"
            )
    return "\n".join(lines)


def _onboarding_args(
    options: dict[str, Any],
    *,
    mode: str,
    output_dir: Path,
    manifest_dir: Path,
) -> list[str]:
    args: list[str] = [mode]
    if mode == "from-discovery":
        discovery = options.get("discovery") or options.get("discovery_path")
        if not discovery:
            raise ValueError(f"{mode} run requires discovery")
        args.extend([str(_resolve_path(str(discovery), base_dir=manifest_dir))])
    elif mode == "repo":
        repo_spec = options.get("repo_spec") or options.get("repo")
        if not repo_spec:
            raise ValueError("repo run requires repo or repo_spec")
        args.append(str(repo_spec))
    elif mode == "tree":
        owner = options.get("owner")
        repo = options.get("repo")
        ref = options.get("ref")
        if not owner or not repo or not ref:
            raise ValueError("tree run requires owner, repo and ref")
        args.extend([str(owner), str(repo)])
    elif mode == "search":
        query = options.get("query")
        if not query:
            raise ValueError("search run requires query")
        args.append(str(query))
    else:
        raise ValueError(f"Unsupported onboarding smoke run mode: {mode}")

    args.append(str(output_dir))
    _append_mode_flags(args, options, mode=mode)
    _append_shared_flags(args, options, output_dir=output_dir)
    return args


def _append_mode_flags(args: list[str], options: dict[str, Any], *, mode: str) -> None:
    if mode in {"repo", "tree", "search"} and options.get("ref"):
        args.extend(["--ref", str(options["ref"])])
    if mode in {"repo", "tree"} and _bool(options.get("no_recursive")):
        args.append("--no-recursive")
    if mode in {"repo", "tree", "search"}:
        for key, flag in (
            ("token_env", "--token-env"),
            ("api_base_url", "--api-base-url"),
            ("timeout", "--timeout"),
        ):
            if key in options and options[key] is not None:
                args.extend([flag, str(options[key])])
    if mode == "search":
        for key, flag in (
            ("owner", "--owner"),
            ("repo", "--repo"),
            ("extension", "--extension"),
            ("per_page", "--per-page"),
            ("max_pages", "--max-pages"),
        ):
            if key in options and options[key] is not None:
                args.extend([flag, str(options[key])])
    if mode == "from-discovery":
        for key, flag in (
            ("owner", "--owner"),
            ("repo", "--repo"),
            ("ref", "--ref"),
        ):
            if key in options and options[key] is not None:
                args.extend([flag, str(options[key])])


def _append_shared_flags(
    args: list[str],
    options: dict[str, Any],
    *,
    output_dir: Path,
) -> None:
    value_flags = (
        ("preset", "--preset"),
        ("target_prefix", "--target-prefix"),
        ("source_cache_dir", "--source-cache-dir"),
        ("max_sources", "--max-sources"),
        ("max_candidates", "--max-candidates"),
        ("dependency_max_depth", "--dependency-max-depth"),
        ("benchmark_output_dir", "--benchmark-output-dir"),
        ("patch_mode", "--patch-mode"),
        ("judge_mode", "--judge-mode"),
        ("patch_judge_mode", "--patch-judge-mode"),
        ("llm_score_mode", "--llm-score-mode"),
        ("format", "--format"),
        ("min_imported_sources", "--min-imported-sources"),
        ("min_generated_candidates", "--min-generated-candidates"),
        ("min_quality_score", "--min-quality-score"),
        ("min_source_hit_rate", "--min-source-hit-rate"),
        ("min_selected_source_groups", "--min-selected-source-groups"),
        ("min_selected_source_directories", "--min-selected-source-directories"),
        ("min_selected_rules", "--min-selected-rules"),
        ("min_selected_bug_types", "--min-selected-bug-types"),
        ("min_source_group_coverage", "--min-source-group-coverage"),
        ("min_source_directory_coverage", "--min-source-directory-coverage"),
        ("min_candidate_rule_coverage", "--min-candidate-rule-coverage"),
        ("min_candidate_bug_type_coverage", "--min-candidate-bug-type-coverage"),
        ("min_candidate_source_coverage", "--min-candidate-source-coverage"),
        ("min_benchmark_cases", "--min-benchmark-cases"),
        ("min_top1", "--min-top1"),
        ("min_map", "--min-map"),
        ("min_patch_success_rate", "--min-patch-success-rate"),
        ("repository_test_root", "--repository-test-root"),
        ("repository_test_timeout", "--repository-test-timeout"),
        (
            "repository_test_failure_overlay_candidate_limit",
            "--repository-test-failure-overlay-candidate-limit",
        ),
        ("repository_test_reflection_mode", "--repository-test-reflection-mode"),
        ("repository_test_reflection_rounds", "--repository-test-reflection-rounds"),
        ("repository_test_reflection_width", "--repository-test-reflection-width"),
        (
            "repository_test_environment_setup_timeout",
            "--repository-test-environment-setup-timeout",
        ),
        (
            "auto_repository_test_retry_max_risk",
            "--auto-repository-test-retry-max-risk",
        ),
        ("repository_checkout_timeout", "--repository-checkout-timeout"),
        ("repository_checkout_depth", "--repository-checkout-depth"),
    )
    repeat_flags = (
        ("include", "--include"),
        ("exclude", "--exclude"),
        ("recipe", "--recipe"),
        ("recipes", "--recipe"),
        (
            "auto_repository_test_retry_allowed_runners",
            "--auto-repository-test-retry-runner",
        ),
    )
    bool_flags = (
        ("preserve_paths", "--preserve-paths"),
        ("no_auto_dependency_sources", "--no-auto-dependency-sources"),
        ("materialize_template", "--materialize-template"),
        ("run_benchmark", "--run-benchmark"),
        ("no_dynamic_coverage", "--no-dynamic-coverage"),
        ("run_quality_gate", "--run-quality-gate"),
        ("no_require_ready_for_benchmark", "--no-require-ready-for-benchmark"),
        ("no_require_benchmark_run", "--no-require-benchmark-run"),
        ("run_showcase_lite", "--run-showcase-lite"),
        ("run_smoke_validation", "--run-smoke-validation"),
        ("no_smoke_validation", "--no-smoke-validation"),
        (
            "run_repository_test_environment_setup",
            "--run-repository-test-environment-setup",
        ),
        ("run_repository_test_retry", "--run-repository-test-retry"),
        (
            "run_repository_test_retry_prerequisites",
            "--run-repository-test-retry-prerequisites",
        ),
        ("auto_repository_test_retry", "--auto-repository-test-retry"),
        ("checkout_repository_tests", "--checkout-repository-tests"),
        ("no_repository_test_command", "--no-repository-test-command"),
    )
    for key, flag in repeat_flags:
        for value in _string_list(options.get(key)):
            args.extend([flag, value])
    for key, flag in value_flags:
        if key not in options or options[key] is None:
            continue
        value = options[key]
        if key == "benchmark_output_dir":
            value = output_dir / str(value)
        args.extend([flag, str(value)])
    for key, flag in bool_flags:
        if _bool(options.get(key)):
            args.append(flag)


def _manifest_runs(payload: dict[str, Any]) -> list[Any]:
    for key in ("runs", "repos", "items"):
        values = payload.get(key)
        if isinstance(values, list):
            return values
    return []


def _manifest_run_key(payload: dict[str, Any]) -> str:
    for key in ("runs", "repos", "items"):
        if isinstance(payload.get(key), list):
            return key
    return ""


def _find_manifest_run_index(
    runs: list[Any],
    *,
    target_name: str,
    defaults: dict[str, Any],
) -> int | None:
    for index, run_value in enumerate(runs):
        run = _dict(run_value)
        options = {**defaults, **run}
        if _run_name(options, index) == target_name:
            return index
    return None


def _run_name(options: dict[str, Any], index: int) -> str:
    value = options.get("name")
    if value:
        return str(value)
    if options.get("repo"):
        return _slug(str(options["repo"]))
    return f"run_{index + 1}"


def _run_output_dir(options: dict[str, Any], *, output_root: Path, name: str) -> Path:
    raw_output = options.get("output_dir")
    if raw_output:
        path = Path(str(raw_output))
        if path.is_absolute():
            return path
        return output_root / path
    return output_root / "runs" / _slug(name)


def _resolve_path(value: str, *, base_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir / path


def _manifest_recommendation(
    run: OnboardingSmokeRunResult,
    *,
    primary_generated_candidates: int,
    final_generated_candidates: int,
    min_generated_candidates: int,
) -> dict[str, Any]:
    final_values = _command_values(run.command_args)
    primary_values = _command_values(run.primary_command_args or [])
    recommended_fallback: dict[str, Any] = {"enabled": True}
    for key in ("preset", "max_sources", "max_candidates"):
        value = final_values.get(key)
        if value is not None:
            recommended_fallback[key] = value
    final_recipes = _string_list(final_values.get("recipe"))
    if final_recipes:
        recommended_fallback["recipe"] = final_recipes
    remove_fields: list[str] = []
    if primary_values.get("recipe") and not final_recipes:
        remove_fields.append("recipe")
    return {
        "name": run.name,
        "mode": run.mode,
        "fallback_reason": run.fallback_reason,
        "min_generated_candidates": min_generated_candidates,
        "primary_generated_candidates": primary_generated_candidates,
        "final_generated_candidates": final_generated_candidates,
        "candidate_delta": final_generated_candidates - primary_generated_candidates,
        "recommended_fallback": recommended_fallback,
        "remove_primary_fields": remove_fields,
        "primary_report_path": run.primary_report_path,
        "final_report_path": run.report_path,
        "note": (
            "Promote this fallback block into the run entry, or move it into "
            "defaults if multiple repositories recover with the same settings."
        ),
    }


def _command_values(args: list[str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    repeat_values: dict[str, list[str]] = {}
    flag_to_key = {
        "--preset": "preset",
        "--max-sources": "max_sources",
        "--max-candidates": "max_candidates",
        "--recipe": "recipe",
    }
    index = 0
    while index < len(args):
        flag = args[index]
        key = flag_to_key.get(flag)
        if key and index + 1 < len(args):
            value = args[index + 1]
            if key == "recipe":
                repeat_values.setdefault(key, []).append(value)
            elif key in {"max_sources", "max_candidates"}:
                values[key] = _int(value)
            else:
                values[key] = value
            index += 2
            continue
        index += 1
    values.update(repeat_values)
    return values


def _read_json(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    if not path.exists():
        return {}
    try:
        return _dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}


def _gap_next_actions(
    *,
    command_failed_runs: int,
    validation_failed_check_counts: dict[str, int],
    diagnostic_issue_counts: dict[str, int],
    no_candidate_reports: int,
    fallback_recovered_runs: int,
    suggested_actions: list[str],
) -> list[str]:
    actions: list[str] = []
    if fallback_recovered_runs:
        actions.append(
            "Promote recovered fallback settings into the manifest defaults or run-specific configuration."
        )
    if command_failed_runs:
        actions.append(
            "Fix manifest entries, GitHub API access, or missing discovery files before judging benchmark quality."
        )
    if no_candidate_reports or "generated_candidates" in validation_failed_check_counts:
        actions.append(
            "Broaden recipe mining by removing --recipe, adding adjacent recipes, increasing --max-sources, or using --preset mining."
        )
    if "required_artifacts" in validation_failed_check_counts:
        actions.append(
            "Inspect each run output directory for missing template, benchmark, quality gate, or diagnostics artifacts."
        )
    if "benchmark_patch_success_rate" in validation_failed_check_counts:
        actions.append(
            "Inspect benchmark_report.md and patch traces to decide whether the generated mutation needs a stronger oracle or repair rule."
        )
    if "benchmark_top1" in validation_failed_check_counts or "benchmark_map" in validation_failed_check_counts:
        actions.append(
            "Inspect localization signals and consider widening Top-k, adding graph evidence, or strengthening rule filters for this repo."
        )
    if "source_read_errors" in diagnostic_issue_counts:
        actions.append(
            "Use --source-cache-dir with pre-fetched raw sources or set GITHUB_TOKEN for private/rate-limited repositories."
        )
    if "no_imported_sources" in diagnostic_issue_counts:
        actions.append(
            "Relax include/exclude filters or target Python source paths before recipe mining."
        )
    if "quality_gate_failed" in diagnostic_issue_counts:
        actions.append(
            "Inspect onboarding_quality_gate.md and tune exploratory thresholds separately from full-suite thresholds."
        )
    for action in suggested_actions:
        _append_unique(actions, action)
    if not actions:
        actions.append(
            "Keep this manifest as a repeatable onboarding smoke gate and add more repositories to measure generalization."
        )
    return actions


def _increment(counts: dict[str, int], key: str) -> None:
    normalized = key or "unknown"
    counts[normalized] = counts.get(normalized, 0) + 1


def _append_unique(values: list[str], value: str) -> None:
    normalized = value.strip()
    if normalized and normalized not in values:
        values.append(normalized)


def _top_key(counts: dict[str, int]) -> str:
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _error_kind(error: str | None) -> str:
    if not error:
        return "unknown"
    return error.split(":", 1)[0].strip() or "unknown"


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def _deepcopy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _preview(value: str, *, limit: int = 2000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


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


def _slug(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "_" for char in value]
    slug = "".join(chars).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "run"


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a multi-repository GitHub onboarding smoke manifest and "
            "validate the generated onboarding reports."
        )
    )
    parser.add_argument("manifest", help="Path to onboarding smoke runner manifest.")
    parser.add_argument("output_dir", help="Directory for generated run artifacts.")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
    )
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    return parser


def main(argv: list[str] | None = None, opener=None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    report = run_onboarding_smoke_suite(
        args.manifest,
        args.output_dir,
        opener=opener,
    )
    json_payload = json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
    markdown = render_onboarding_smoke_runner_markdown(report)
    if args.output_json:
        Path(args.output_json).write_text(json_payload, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown, encoding="utf-8")
    if args.format == "json":
        print(json_payload)
    else:
        print(markdown)
    raise SystemExit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
