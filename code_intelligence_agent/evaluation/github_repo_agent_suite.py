from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.github_discovery_fetcher import GitHubAPIError
from code_intelligence_agent.evaluation.github_repo_agent import (
    GitHubRepoAgentReport,
    render_github_repo_agent_markdown,
    run_github_repo_agent,
)


@dataclass(frozen=True)
class GitHubRepoAgentSuiteRunResult:
    name: str
    repo: str
    output_dir: str
    report_path: str
    status: str
    passed: bool
    expected_status: str
    expectation_passed: bool
    diagnostic_issue_codes: list[str]
    metrics: dict[str, Any]
    metric_checks: list[dict[str, Any]]
    command_args: list[str]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GitHubRepoAgentSuiteReport:
    manifest_path: str
    output_dir: str
    suite_name: str
    passed: bool
    summary: dict[str, Any]
    runs: list[GitHubRepoAgentSuiteRunResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_path": self.manifest_path,
            "output_dir": self.output_dir,
            "suite_name": self.suite_name,
            "passed": self.passed,
            "summary": self.summary,
            "runs": [run.to_dict() for run in self.runs],
        }


def run_github_repo_agent_suite(
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    opener=None,
) -> GitHubRepoAgentSuiteReport:
    manifest = Path(manifest_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    suite_name = str(payload.get("suite_name") or payload.get("name") or manifest.stem)
    defaults = _dict(payload.get("defaults"))
    runs = [_dict(run) for run in _list(payload.get("runs"))]
    run_results: list[GitHubRepoAgentSuiteRunResult] = []

    for index, entry in enumerate(runs):
        options = {**defaults, **entry}
        name = _run_name(options, index)
        repo = str(options.get("repo") or options.get("repo_spec") or "")
        if not repo:
            run_results.append(
                _failed_run_result(
                    name=name,
                    repo="",
                    output_dir=str(output_root / name),
                    expected_status=str(options.get("expected_status") or "pass"),
                    error="manifest run is missing repo",
                )
            )
            continue
        run_output = Path(str(options.get("output_dir") or output_root / name))
        expected_status = str(options.get("expected_status") or "pass")
        command_args = _command_args(repo, run_output, options)
        try:
            report = run_github_repo_agent(
                repo,
                run_output,
                ref=_optional_str(options.get("ref")),
                token=_token_from_env(str(options.get("token_env") or "GITHUB_TOKEN")),
                recursive=_bool_option(options, "recursive", True),
                api_base_url=str(options.get("api_base_url") or "https://api.github.com"),
                timeout=_int(options.get("timeout", 20)),
                include=_list_str(options.get("include")),
                exclude=_list_str(options.get("exclude")),
                target_prefix=str(options.get("target_prefix") or ""),
                recipes=_list_str(options.get("recipes", options.get("recipe"))),
                source_cache_dir=options.get("source_cache_dir"),
                max_sources=_optional_int(options.get("max_sources")),
                max_candidates=_optional_int(options.get("max_candidates")),
                auto_dependency_sources=_bool_option(
                    options,
                    "auto_dependency_sources",
                    True,
                ),
                dependency_max_depth=_int(options.get("dependency_max_depth", 4)),
                preset=str(options.get("preset") or "smoke"),
                run_smoke_validation=_optional_bool(options.get("run_smoke_validation")),
                repository_test_root=options.get("repository_test_root"),
                repository_test_timeout=_int(
                    options.get("repository_test_timeout", 20)
                ),
                repository_test_failure_overlay_candidate_limit=_int(
                    options.get(
                        "repository_test_failure_overlay_candidate_limit",
                        5,
                    )
                ),
                repository_test_reflection_mode=str(
                    options.get("repository_test_reflection_mode") or "rule"
                ),
                repository_test_reflection_rounds=_int(
                    options.get("repository_test_reflection_rounds", 1)
                ),
                repository_test_reflection_width=_int(
                    options.get("repository_test_reflection_width", 1)
                ),
                run_repository_test_environment_setup=_bool_option(
                    options,
                    "run_repository_test_environment_setup",
                    False,
                ),
                repository_test_environment_setup_mode=str(
                    options.get("repository_test_environment_setup_mode") or "project"
                ),
                run_repository_test_retry=_bool_option(
                    options,
                    "run_repository_test_retry",
                    False,
                ),
                run_repository_test_retry_prerequisites=_bool_option(
                    options,
                    "run_repository_test_retry_prerequisites",
                    False,
                ),
                repository_test_environment_setup_timeout=_int(
                    options.get("repository_test_environment_setup_timeout", 120)
                ),
                checkout_repository_tests=_bool_option(
                    options,
                    "checkout_repository_tests",
                    False,
                ),
                repository_checkout_timeout=_int(
                    options.get("repository_checkout_timeout", 120)
                ),
                repository_checkout_depth=_int(
                    options.get("repository_checkout_depth", 1)
                ),
                auto_fallback=_bool_option(options, "auto_fallback", True),
                fallback_min_generated_candidates=_int(
                    options.get("fallback_min_generated_candidates", 1)
                ),
                fallback_max_sources=_optional_int(
                    options.get("fallback_max_sources")
                ),
                fallback_max_candidates=_optional_int(
                    options.get("fallback_max_candidates")
                ),
                fallback_preset=_optional_str(options.get("fallback_preset")),
                fallback_recipes=(
                    _list_str(
                        options.get(
                            "fallback_recipes",
                            options.get("fallback_recipe"),
                        )
                    )
                    or None
                ),
                auto_remediate_benchmark=_bool_option(
                    options,
                    "auto_remediate_benchmark",
                    False,
                ),
                opener=opener,
            )
            run_results.append(
                _run_result_from_report(
                    name=name,
                    repo=repo,
                    report=report,
                    expected_status=expected_status,
                    expected_diagnostic_codes=_list_str(
                        options.get("expected_diagnostic_codes")
                    ),
                    metric_thresholds=_metric_thresholds(options),
                    command_args=command_args,
                )
            )
        except (GitHubAPIError, ValueError, OSError) as exc:
            run_results.append(
                _failed_run_result(
                    name=name,
                    repo=repo,
                    output_dir=str(run_output),
                    expected_status=expected_status,
                    command_args=command_args,
                    error=str(exc),
                )
            )

    summary = _suite_summary(
        run_results,
        suite_thresholds=_suite_thresholds(payload),
    )
    passed = (
        summary["command_failed_count"] == 0
        and summary["expectation_failed_count"] == 0
        and summary["suite_threshold_failed_count"] == 0
    )
    report = GitHubRepoAgentSuiteReport(
        manifest_path=str(manifest),
        output_dir=str(output_root),
        suite_name=suite_name,
        passed=passed,
        summary=summary,
        runs=run_results,
    )
    _write_suite_outputs(report, output_root)
    return report


def render_github_repo_agent_suite_markdown(
    report: GitHubRepoAgentSuiteReport,
) -> str:
    summary = report.summary
    lines = [
        "# GitHub Repo Agent Suite",
        "",
        f"- Suite: `{report.suite_name}`",
        f"- Manifest: `{report.manifest_path}`",
        f"- Output Dir: `{report.output_dir}`",
        f"- Passed: {str(report.passed).lower()}",
        f"- Runs: {_int(summary.get('run_count', 0))}",
        f"- Agent Passed Runs: {_int(summary.get('agent_passed_count', 0))}",
        f"- Agent Failed Runs: {_int(summary.get('agent_failed_count', 0))}",
        f"- Expectation Passed Runs: {_int(summary.get('expectation_passed_count', 0))}",
        f"- Expectation Failed Runs: {_int(summary.get('expectation_failed_count', 0))}",
        f"- Command Failed Runs: {_int(summary.get('command_failed_count', 0))}",
        f"- Metric Checks: {_int(summary.get('metric_check_count', 0))}",
        f"- Failed Metric Checks: {_int(summary.get('metric_check_failed_count', 0))}",
        f"- Suite Threshold Checks: {_int(summary.get('suite_threshold_check_count', 0))}",
        f"- Failed Suite Threshold Checks: {_int(summary.get('suite_threshold_failed_count', 0))}",
        f"- Generated Candidates: {_int(summary.get('generated_candidates', 0))}",
        f"- Static Intelligence Runs: {_int(summary.get('static_intelligence_run_count', 0))}",
        f"- Static Intelligence Analysis Ready Runs: {_int(summary.get('static_intelligence_analysis_ready_count', 0))}",
        (
            "- Static Intelligence Signals: "
            f"selected={_int(summary.get('static_intelligence_selected_signal_count', 0))}, "
            f"total={_int(summary.get('static_intelligence_total_signal_count', 0))}"
        ),
        (
            "- Static Intelligence Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('static_intelligence_status_counts'))))}"
        ),
        (
            "- Static Intelligence Levels: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('static_intelligence_level_counts'))))}"
        ),
        (
            "- Static Intelligence Rules: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('static_intelligence_rule_counts'))))}"
        ),
        (
            "- Static Intelligence Bug Types: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('static_intelligence_bug_type_counts'))))}"
        ),
        (
            "- Static Intelligence Dynamic Levels: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('static_intelligence_dynamic_validation_level_counts'))))}"
        ),
        f"- Static Intelligence Candidate-Limited Runs: {_int(summary.get('static_intelligence_candidate_limit_applied_count', 0))}",
        f"- Benchmark Cases: {_int(summary.get('benchmark_cases', 0))}",
        f"- Auto Fallback Attempts: {_int(summary.get('fallback_attempted_count', 0))}",
        f"- Auto Fallback Used Runs: {_int(summary.get('fallback_used_count', 0))}",
        f"- Auto Fallback Improved Runs: {_int(summary.get('fallback_improved_count', 0))}",
        f"- Auto Fallback Recovered Runs: {_int(summary.get('fallback_recovered_count', 0))}",
        f"- Auto Fallback Candidate Delta: {_int(summary.get('fallback_candidate_delta', 0))}",
        f"- Auto Fallback Benchmark Case Delta: {_int(summary.get('fallback_benchmark_case_delta', 0))}",
        (
            "- Auto Fallback Reasons: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('fallback_reason_counts'))))}"
        ),
        f"- Auto Remediation Attempts: {_int(summary.get('auto_remediation_attempted_count', 0))}",
        f"- Auto Remediation Used Runs: {_int(summary.get('auto_remediation_used_count', 0))}",
        f"- Auto Remediation Improved Runs: {_int(summary.get('auto_remediation_improved_count', 0))}",
        f"- Auto Remediation Benchmark Case Delta: {_int(summary.get('auto_remediation_benchmark_case_delta', 0))}",
        (
            "- Auto Remediation Actions: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('auto_remediation_action_counts'))))}"
        ),
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
        (
            "- Benchmarkization Remediation Plans: "
            f"{_int(summary.get('benchmarkization_remediation_plan_count', 0))}"
        ),
        (
            "- Primary Benchmarkization Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('primary_benchmarkization_status_counts'))))}"
        ),
        (
            "- Fallback Benchmarkization Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('fallback_benchmarkization_status_counts'))))}"
        ),
        (
            "- Remediated Benchmarkization Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('remediated_benchmarkization_status_counts'))))}"
        ),
        (
            "- Benchmarkization Fallback Transitions: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('benchmarkization_fallback_transition_counts'))))}"
        ),
        (
            "- Benchmarkization Remediation Transitions: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('benchmarkization_remediation_transition_counts'))))}"
        ),
        (
            "- Primary Benchmarkization Actions: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('primary_benchmarkization_action_counts'))))}"
        ),
        (
            "- Fallback Benchmarkization Actions: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('fallback_benchmarkization_action_counts'))))}"
        ),
        (
            "- Primary Benchmarkization Remediation Plans: "
            f"{_int(summary.get('primary_benchmarkization_remediation_plan_count', 0))}"
        ),
        (
            "- Fallback Benchmarkization Remediation Plans: "
            f"{_int(summary.get('fallback_benchmarkization_remediation_plan_count', 0))}"
        ),
        f"- Benchmark Runs: {_int(summary.get('benchmark_run_count', 0))}",
        f"- Repository Test Environment Setup Plans: {_int(summary.get('repository_test_environment_setup_count', 0))}",
        f"- Supported Repository Test Environment Setups: {_int(summary.get('repository_test_environment_setup_supported_count', 0))}",
        f"- Executed Repository Test Environment Setups: {_int(summary.get('repository_test_environment_setup_executed_count', 0))}",
        f"- Passed Repository Test Environment Setups: {_int(summary.get('repository_test_environment_setup_passed_count', 0))}",
        f"- Repository Test Environment Install Fallbacks: {_int(summary.get('repository_test_environment_setup_install_fallback_count', 0))}",
        f"- Repository Test Environment Install Fallback Successes: {_int(summary.get('repository_test_environment_setup_install_fallback_success_count', 0))}",
        (
            "- Repository Test Environment Install Failure Categories: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_environment_setup_install_failure_counts'))))}"
        ),
        f"- Repository Config Snapshot Runs: {_int(summary.get('repository_config_snapshot_count', 0))}",
        f"- Repository Config Snapshot Files: {_int(summary.get('repository_config_snapshot_file_count', 0))}",
        f"- Repository Test Plans: {_int(summary.get('repository_test_plan_count', 0))}",
        f"- Executable Repository Test Plans: {_int(summary.get('repository_test_plan_executable_count', 0))}",
        f"- Narrow Repository Test Plans: {_int(summary.get('repository_test_plan_narrow_count', 0))}",
        f"- Executed Planned Repository Tests: {_int(summary.get('planned_repository_test_executed_count', 0))}",
        f"- Passed Planned Repository Tests: {_int(summary.get('planned_repository_test_passed_count', 0))}",
        f"- Venv Planned Repository Tests: {_int(summary.get('planned_repository_test_venv_python_count', 0))}",
        (
            "- Planned Repository Test Runners: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('planned_repository_test_runner_counts'))))}"
        ),
        (
            "- Planned Repository Test Runner Kinds: "
            f"{_int(summary.get('planned_repository_test_runner_kind_count', 0))}"
        ),
        (
            "- Planned Repository Test Sources: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('planned_repository_test_source_counts'))))}"
        ),
        f"- Planned Repository Test CI Candidates: {_int(summary.get('planned_repository_test_ci_candidate_count', 0))}",
        (
            "- Framework Signals: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('framework_signal_counts'))))}"
        ),
        (
            "- Framework Test Configuration Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_framework_configuration_status_counts'))))}"
        ),
        (
            "- Framework Test Configuration Reasons: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_framework_configuration_reason_counts'))))}"
        ),
        f"- Repository Test Pytest Configured Runs: {_int(summary.get('repository_test_pytest_configured_count', 0))}",
        f"- Repository Test Pytest Testpath Runs: {_int(summary.get('repository_test_pytest_testpath_run_count', 0))}",
        f"- Repository Test Pytest Addopts Runs: {_int(summary.get('repository_test_pytest_addopts_run_count', 0))}",
        f"- Repository Test CI Configured Runs: {_int(summary.get('repository_test_ci_configured_count', 0))}",
        (
            "- Repository Test CI Python Versions: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_ci_python_version_counts'))))}"
        ),
        f"- Repository Test CI Install Candidates: {_int(summary.get('repository_test_ci_install_candidate_count', 0))}",
        f"- Repository Test CI Test Candidates: {_int(summary.get('repository_test_ci_test_command_candidate_count', 0))}",
        f"- Repository Test Tox Envlist Runs: {_int(summary.get('repository_test_tox_envlist_run_count', 0))}",
        (
            "- Planned Repository Test Env Vars: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('planned_repository_test_environment_variable_counts'))))}"
        ),
        f"- Repository Test App Bootstrap Candidates: {_int(summary.get('repository_test_app_bootstrap_candidate_count', 0))}",
        f"- Repository Test Bootstrap Signals: {_int(summary.get('repository_test_bootstrap_signal_count', 0))}",
        (
            "- Planned Repository Test Failure Categories: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('planned_repository_test_failure_category_counts'))))}"
        ),
        f"- Repository Test Retry Recommendations: {_int(summary.get('repository_test_retry_recommended_count', 0))}",
        f"- Executed Repository Test Retries: {_int(summary.get('repository_test_retry_executed_count', 0))}",
        f"- Passed Repository Test Retries: {_int(summary.get('repository_test_retry_passed_count', 0))}",
        f"- Repository Test Dynamic Evidence Runs: {_int(summary.get('repository_test_dynamic_evidence_count', 0))}",
        f"- Repository Test Localization-Ready Runs: {_int(summary.get('repository_test_dynamic_localization_ready_count', 0))}",
        f"- Repository Test Regression-Validation-Ready Runs: {_int(summary.get('repository_test_dynamic_regression_validation_ready_count', 0))}",
        f"- Repository Test Patch-Validation-Ready Runs: {_int(summary.get('repository_test_dynamic_patch_validation_ready_count', 0))}",
        f"- Repository Test Phase 2 Ready Runs: {_int(summary.get('repository_test_phase2_ready_count', 0))}",
        f"- Repository Test Phase 3 Validation-Ready Runs: {_int(summary.get('repository_test_phase3_validation_ready_count', 0))}",
        (
            "- Repository Test Final Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_final_status_counts'))))}"
        ),
        f"- Repository Test Repaired Runs: {_int(summary.get('repository_test_repaired_count', 0))}",
        f"- Repository Test Blocked Runs: {_int(summary.get('repository_test_blocked_count', 0))}",
        (
            "- Repository Test Blocked Reasons: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_blocked_reason_counts'))))}"
        ),
        f"- Repository Test Failure Overlay Runs: {_int(summary.get('repository_test_failure_overlay_count', 0))}",
        f"- Repository Test Failure Overlay Successes: {_int(summary.get('repository_test_failure_overlay_success_count', 0))}",
        f"- Repository Test Failure Overlay Success Rate: {_float(summary.get('repository_test_failure_overlay_success_rate', 0.0)):.4f}",
        f"- Repository Test Failure Overlay Attempts: {_int(summary.get('repository_test_failure_overlay_attempted_case_count', 0))}",
        f"- Repository Test Failure Overlay Candidate Attempt Rate: {_float(summary.get('repository_test_failure_overlay_candidate_attempt_rate', 0.0)):.4f}",
        f"- Repository Test Failure Overlay Attempt Trigger Rate: {_float(summary.get('repository_test_failure_overlay_attempt_trigger_rate', 0.0)):.4f}",
        f"- Repository Test Failure Overlay Average Selected Score: {_float(summary.get('repository_test_failure_overlay_average_selected_score', 0.0)):.4f}",
        f"- Repository Test Failure Overlay Candidate Rules: {_int(summary.get('repository_test_failure_overlay_candidate_rule_count', 0))}",
        f"- Repository Test Failure Overlay Attempted Rules: {_int(summary.get('repository_test_failure_overlay_attempted_rule_count', 0))}",
        f"- Repository Test Failure Overlay Triggered Rules: {_int(summary.get('repository_test_failure_overlay_triggered_rule_count', 0))}",
        (
            "- Repository Test Failure Overlay Candidate Rejection Counts: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_failure_overlay_candidate_rejection_counts'))))}"
        ),
        (
            "- Repository Test Failure Overlay Dominant Rejection Counts: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_failure_overlay_dominant_rejection_counts'))))}"
        ),
        (
            "- Repository Test Failure Overlay Actionable Extension Counts: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_failure_overlay_actionable_extension_counts'))))}"
        ),
        f"- Repository Test Failure Overlay Attempted Rule Coverage Rate: {_float(summary.get('repository_test_failure_overlay_attempted_rule_coverage_rate', 0.0)):.4f}",
        f"- Repository Test Failure Overlay Triggered Rule Coverage Rate: {_float(summary.get('repository_test_failure_overlay_triggered_rule_coverage_rate', 0.0)):.4f}",
        f"- Repository Test Fault Localization Runs: {_int(summary.get('repository_test_fault_localization_count', 0))}",
        f"- Repository Test Fault Localization Passed Runs: {_int(summary.get('repository_test_fault_localization_passed_count', 0))}",
        f"- Repository Test Patch Candidate Runs: {_int(summary.get('repository_test_patch_candidate_run_count', 0))}",
        f"- Repository Test Patch Candidates: {_int(summary.get('repository_test_patch_candidate_count', 0))}",
        f"- Repository Test Patch Validation Runs: {_int(summary.get('repository_test_patch_validation_run_count', 0))}",
        f"- Repository Test Patch Validation Success Runs: {_int(summary.get('repository_test_patch_validation_success_run_count', 0))}",
        f"- Repository Test Patch Validation Successes: {_int(summary.get('repository_test_patch_validation_success_count', 0))}",
        f"- Repository Test Repair-Ready Runs: {_int(summary.get('repository_test_repair_ready_count', 0))}",
        f"- Repository Test Regression-Ready Runs: {_int(summary.get('repository_test_regression_ready_count', 0))}",
        (
            "- Repository Test Repair Validation Scopes: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_repair_validation_scope_counts'))))}"
        ),
        (
            "- Repository Test Regression Validation Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_regression_validation_status_counts'))))}"
        ),
        f"- Repository Test Patch Validation Reflection Candidates: {_int(summary.get('repository_test_patch_validation_reflection_candidate_count', 0))}",
        f"- Repository Test Patch Validation Reflection Successes: {_int(summary.get('repository_test_patch_validation_successful_reflection_count', 0))}",
        f"- Repository Test Patch Validation Max Depth: {_int(summary.get('repository_test_patch_validation_max_depth', 0))}",
        (
            "- Repository Test Retry Strategies: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_retry_strategy_counts'))))}"
        ),
        (
            "- Repository Test Dynamic Evidence Levels: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_dynamic_evidence_level_counts'))))}"
        ),
        (
            "- Repository Test Dynamic Evidence Level Kinds: "
            f"{_int(summary.get('repository_test_dynamic_evidence_level_kind_count', 0))}"
        ),
        (
            "- Repository Test Analysis Sources: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_analysis_source_counts'))))}"
        ),
        (
            "- Repository Test Overlay Trigger Reasons: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_overlay_trigger_reason_counts'))))}"
        ),
        (
            "- Repository Test Failure Overlay Reasons: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_failure_overlay_reason_counts'))))}"
        ),
        (
            "- Repository Test Failure Overlay Rules: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_failure_overlay_rule_counts'))))}"
        ),
        (
            "- Repository Test Failure Overlay Candidate Rules: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_failure_overlay_candidate_rule_counts'))))}"
        ),
        (
            "- Repository Test Failure Overlay Triggered Rules: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_failure_overlay_triggered_rule_counts'))))}"
        ),
        f"- Average Top-1: {_float(summary.get('average_top1', 0.0)):.4f}",
        f"- Average MAP: {_float(summary.get('average_map', 0.0)):.4f}",
        f"- Average Patch Success Rate: {_float(summary.get('average_patch_success_rate', 0.0)):.4f}",
        f"- Weighted Top-1: {_float(summary.get('weighted_top1', 0.0)):.4f}",
        f"- Weighted MAP: {_float(summary.get('weighted_map', 0.0)):.4f}",
        f"- Weighted Patch Success Rate: {_float(summary.get('weighted_patch_success_rate', 0.0)):.4f}",
        "",
        "## Runs",
        "",
        "| Name | Repo | Status | Expected | Match | Fallback | Remediation | Candidates | Benchmark Cases | Top-1 | MAP | Patch Success | Diagnostic Codes |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    by_name = {run.name: run for run in report.runs}
    for run in report.runs:
        lines.append(
            "| "
            f"{_markdown_cell(run.name)} | "
            f"{_markdown_cell(run.repo)} | "
            f"{_markdown_cell(run.status)} | "
            f"{_markdown_cell(run.expected_status)} | "
            f"{str(run.expectation_passed).lower()} | "
            f"{_markdown_cell(_fallback_cell(run.metrics))} | "
            f"{_markdown_cell(_auto_remediation_cell(run.metrics))} | "
            f"{_int(_run_metric(run, 'generated_candidates'))} | "
            f"{_int(_run_metric(run, 'benchmark_cases'))} | "
            f"{_float(_run_metric(run, 'top1')):.4f} | "
            f"{_float(_run_metric(run, 'map')):.4f} | "
            f"{_float(_run_metric(run, 'patch_success_rate')):.4f} | "
            f"{_markdown_cell(', '.join(run.diagnostic_issue_codes))} |"
        )
    benchmarkization_audit_rows = _list(
        summary.get("benchmarkization_fallback_audit_runs")
    )
    if benchmarkization_audit_rows:
        lines.extend(
            [
                "",
                "## Benchmarkization Fallback Audit",
                "",
                (
                    "| Run | Primary Status | Fallback Status | Transition | "
                    "Primary Action | Fallback Action | Primary Plan | Fallback Plan |"
                ),
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row_value in benchmarkization_audit_rows:
            row = _dict(row_value)
            lines.append(
                "| "
                f"{_markdown_cell(row.get('run') or '')} | "
                f"{_markdown_cell(row.get('primary_status') or 'none')} | "
                f"{_markdown_cell(row.get('fallback_status') or 'none')} | "
                f"{_markdown_cell(row.get('transition') or 'none')} | "
                f"{_markdown_cell(row.get('primary_action') or 'none')} | "
                f"{_markdown_cell(row.get('fallback_action') or 'none')} | "
                f"{_markdown_cell(row.get('primary_remediation_plan_markdown') or 'none')} | "
                f"{_markdown_cell(row.get('fallback_remediation_plan_markdown') or 'none')} |"
            )
    remediation_plan_runs = _dict(
        summary.get("benchmarkization_remediation_plan_runs")
    )
    if remediation_plan_runs:
        lines.extend(
            [
                "",
                "## Benchmarkization Remediation Plans",
                "",
                "| Run | Plan |",
                "| --- | --- |",
            ]
        )
        for run_name, plan_path in sorted(remediation_plan_runs.items()):
            lines.append(
                "| "
                f"{_markdown_cell(run_name)} | "
                f"{_markdown_cell(plan_path)} |"
            )
    code_counts = _dict(summary.get("diagnostic_issue_code_counts"))
    if code_counts:
        lines.extend(["", "## Diagnostic Code Counts", ""])
        for code, count in sorted(code_counts.items()):
            lines.append(f"- `{_markdown_cell(code)}`: {_int(count)}")
    code_runs = _dict(summary.get("diagnostic_issue_code_runs"))
    if code_runs:
        lines.extend(
            [
                "",
                "## Diagnostic Code Runs",
                "",
                "| Code | Runs |",
                "| --- | --- |",
            ]
        )
        for code, runs in sorted(code_runs.items()):
            lines.append(
                "| "
                f"`{_markdown_cell(code)}` | "
                f"{_markdown_cell(', '.join(str(run) for run in _list(runs)))} |"
            )
    failure_counts = _dict(summary.get("planned_repository_test_failure_category_counts"))
    if failure_counts:
        lines.extend(["", "## Planned Repository Test Failure Categories", ""])
        for category, count in sorted(failure_counts.items()):
            lines.append(f"- `{_markdown_cell(category)}`: {_int(count)}")
    runner_counts = _dict(summary.get("planned_repository_test_runner_counts"))
    if runner_counts:
        lines.extend(["", "## Planned Repository Test Runners", ""])
        for runner, count in sorted(runner_counts.items()):
            lines.append(f"- `{_markdown_cell(runner)}`: {_int(count)}")
    runner_runs = _dict(summary.get("planned_repository_test_runner_runs"))
    if runner_runs:
        lines.extend(
            [
                "",
                "## Planned Repository Test Runner Runs",
                "",
                "| Runner | Runs |",
                "| --- | --- |",
            ]
        )
        for runner, runs in sorted(runner_runs.items()):
            lines.append(
                "| "
                f"`{_markdown_cell(runner)}` | "
                f"{_markdown_cell(', '.join(str(run) for run in _list(runs)))} |"
            )
    failure_runs = _dict(summary.get("planned_repository_test_failure_category_runs"))
    if failure_runs:
        lines.extend(
            [
                "",
                "## Planned Repository Test Failure Category Runs",
                "",
                "| Category | Runs |",
                "| --- | --- |",
            ]
        )
        for category, runs in sorted(failure_runs.items()):
            lines.append(
                "| "
                f"`{_markdown_cell(category)}` | "
                f"{_markdown_cell(', '.join(str(run) for run in _list(runs)))} |"
            )
    retry_counts = _dict(summary.get("repository_test_retry_strategy_counts"))
    if retry_counts:
        lines.extend(["", "## Repository Test Retry Strategies", ""])
        for strategy, count in sorted(retry_counts.items()):
            lines.append(f"- `{_markdown_cell(strategy)}`: {_int(count)}")
    retry_runs = _dict(summary.get("repository_test_retry_strategy_runs"))
    if retry_runs:
        lines.extend(
            [
                "",
                "## Repository Test Retry Strategy Runs",
                "",
                "| Strategy | Runs |",
                "| --- | --- |",
            ]
        )
        for strategy, runs in sorted(retry_runs.items()):
            lines.append(
                "| "
                f"`{_markdown_cell(strategy)}` | "
                f"{_markdown_cell(', '.join(str(run) for run in _list(runs)))} |"
            )
    dynamic_counts = _dict(summary.get("repository_test_dynamic_evidence_level_counts"))
    if dynamic_counts:
        lines.extend(["", "## Repository Test Dynamic Evidence Levels", ""])
        for level, count in sorted(dynamic_counts.items()):
            lines.append(f"- `{_markdown_cell(level)}`: {_int(count)}")
    dynamic_runs = _dict(summary.get("repository_test_dynamic_evidence_level_runs"))
    if dynamic_runs:
        lines.extend(
            [
                "",
                "## Repository Test Dynamic Evidence Runs",
                "",
                "| Evidence Level | Runs |",
                "| --- | --- |",
            ]
        )
        for level, runs in sorted(dynamic_runs.items()):
            lines.append(
                "| "
                f"`{_markdown_cell(level)}` | "
                f"{_markdown_cell(', '.join(str(run) for run in _list(runs)))} |"
            )
    phase2_runs = _list(summary.get("repository_test_phase2_ready_runs"))
    phase3_runs = _list(
        summary.get("repository_test_phase3_validation_ready_runs")
    )
    if phase2_runs or phase3_runs:
        lines.extend(
            [
                "",
                "## Repository Test Phase Readiness",
                "",
                "| Phase | Runs |",
                "| --- | --- |",
            ]
        )
        if phase2_runs:
            lines.append(
                "| Phase 2 Fault Localization | "
                f"{_markdown_cell(', '.join(str(run) for run in phase2_runs))} |"
            )
        if phase3_runs:
            lines.append(
                "| Phase 3 Patch Validation | "
                f"{_markdown_cell(', '.join(str(run) for run in phase3_runs))} |"
            )
    final_status_runs = _dict(summary.get("repository_test_final_status_runs"))
    if final_status_runs:
        lines.extend(
            [
                "",
                "## Repository Test Final Status Runs",
                "",
                "| Final Status | Runs |",
                "| --- | --- |",
            ]
        )
        for status, runs in sorted(final_status_runs.items()):
            lines.append(
                "| "
                f"`{_markdown_cell(status)}` | "
                f"{_markdown_cell(', '.join(str(run) for run in _list(runs)))} |"
            )
    blocked_reason_runs = _dict(summary.get("repository_test_blocked_reason_runs"))
    if blocked_reason_runs:
        lines.extend(
            [
                "",
                "## Repository Test Blocked Reason Runs",
                "",
                "| Reason | Runs |",
                "| --- | --- |",
            ]
        )
        for reason, runs in sorted(blocked_reason_runs.items()):
            lines.append(
                "| "
                f"`{_markdown_cell(reason)}` | "
                f"{_markdown_cell(', '.join(str(run) for run in _list(runs)))} |"
            )
    overlay_reason_counts = _dict(
        summary.get("repository_test_failure_overlay_reason_counts")
    )
    if overlay_reason_counts:
        lines.extend(["", "## Repository Test Failure Overlay Reasons", ""])
        for reason, count in sorted(overlay_reason_counts.items()):
            lines.append(f"- `{_markdown_cell(reason)}`: {_int(count)}")
    overlay_reason_runs = _dict(
        summary.get("repository_test_failure_overlay_reason_runs")
    )
    if overlay_reason_runs:
        lines.extend(
            [
                "",
                "## Repository Test Failure Overlay Reason Runs",
                "",
                "| Reason | Runs |",
                "| --- | --- |",
            ]
        )
        for reason, runs in sorted(overlay_reason_runs.items()):
            lines.append(
                "| "
                f"`{_markdown_cell(reason)}` | "
                f"{_markdown_cell(', '.join(str(run) for run in _list(runs)))} |"
            )
    overlay_rule_counts = _dict(
        summary.get("repository_test_failure_overlay_rule_counts")
    )
    if overlay_rule_counts:
        lines.extend(["", "## Repository Test Failure Overlay Rules", ""])
        for rule, count in sorted(overlay_rule_counts.items()):
            lines.append(f"- `{_markdown_cell(rule)}`: {_int(count)}")
    overlay_rule_runs = _dict(summary.get("repository_test_failure_overlay_rule_runs"))
    if overlay_rule_runs:
        lines.extend(
            [
                "",
                "## Repository Test Failure Overlay Rule Runs",
                "",
                "| Rule | Runs |",
                "| --- | --- |",
            ]
        )
        for rule, runs in sorted(overlay_rule_runs.items()):
            lines.append(
                "| "
                f"`{_markdown_cell(rule)}` | "
                f"{_markdown_cell(', '.join(str(run) for run in _list(runs)))} |"
            )
    fault_counts = _dict(summary.get("repository_test_fault_localization_reason_counts"))
    if fault_counts:
        lines.extend(["", "## Repository Test Fault Localization Reasons", ""])
        for reason, count in sorted(fault_counts.items()):
            lines.append(f"- `{_markdown_cell(reason)}`: {_int(count)}")
    fault_runs = _dict(summary.get("repository_test_fault_localization_reason_runs"))
    if fault_runs:
        lines.extend(
            [
                "",
                "## Repository Test Fault Localization Runs",
                "",
                "| Reason | Runs |",
                "| --- | --- |",
            ]
        )
        for reason, runs in sorted(fault_runs.items()):
            lines.append(
                "| "
                f"`{_markdown_cell(reason)}` | "
                f"{_markdown_cell(', '.join(str(run) for run in _list(runs)))} |"
            )
    patch_counts = _dict(summary.get("repository_test_patch_candidate_reason_counts"))
    if patch_counts:
        lines.extend(["", "## Repository Test Patch Candidate Reasons", ""])
        for reason, count in sorted(patch_counts.items()):
            lines.append(f"- `{_markdown_cell(reason)}`: {_int(count)}")
    patch_runs = _dict(summary.get("repository_test_patch_candidate_reason_runs"))
    if patch_runs:
        lines.extend(
            [
                "",
                "## Repository Test Patch Candidate Runs",
                "",
                "| Reason | Runs |",
                "| --- | --- |",
            ]
        )
        for reason, runs in sorted(patch_runs.items()):
            lines.append(
                "| "
                f"`{_markdown_cell(reason)}` | "
                f"{_markdown_cell(', '.join(str(run) for run in _list(runs)))} |"
            )
    validation_counts = _dict(
        summary.get("repository_test_patch_validation_reason_counts")
    )
    if validation_counts:
        lines.extend(["", "## Repository Test Patch Validation Reasons", ""])
        for reason, count in validation_counts.items():
            lines.append(f"- `{_markdown_cell(reason)}`: {_int(count)}")
    validation_runs = _dict(summary.get("repository_test_patch_validation_reason_runs"))
    if validation_runs:
        lines.extend(
            [
                "",
                "## Repository Test Patch Validation Runs",
                "",
                "| Reason | Runs |",
                "| --- | --- |",
            ]
        )
        for reason, runs in validation_runs.items():
            lines.append(
                "| "
                f"`{_markdown_cell(reason)}` | "
                f"{_markdown_cell(', '.join(str(run) for run in _list(runs)))} |"
            )
    validation_failure_counts = _dict(
        summary.get("repository_test_patch_validation_failure_type_counts")
    )
    if validation_failure_counts:
        lines.extend(["", "## Repository Test Patch Validation Failure Types", ""])
        for failure_type, count in validation_failure_counts.items():
            lines.append(f"- `{_markdown_cell(failure_type)}`: {_int(count)}")
    next_actions = _list(summary.get("next_actions"))
    if next_actions:
        lines.extend(["", "## Next Actions", ""])
        for action in next_actions:
            lines.append(f"- {_markdown_cell(action)}")
    failed = [
        run for run in by_name.values()
        if not run.expectation_passed or run.error
    ]
    if failed:
        lines.extend(["", "## Failed Expectations", ""])
        for run in failed:
            failed_metrics = [
                check for check in run.metric_checks if not check.get("passed")
            ]
            if failed_metrics:
                metric_detail = "; ".join(
                    f"{check.get('metric')} expected {check.get('expected')} actual {check.get('actual')}"
                    for check in failed_metrics
                )
                detail = metric_detail
            else:
                detail = run.error or "status, diagnostics, or thresholds did not match expectations"
            lines.append(f"- `{_markdown_cell(run.name)}`: {_markdown_cell(detail)}")
    failed_suite_checks = [
        check
        for check in _list(summary.get("suite_threshold_checks"))
        if not check.get("passed")
    ]
    if failed_suite_checks:
        lines.extend(["", "## Failed Suite Thresholds", ""])
        for check in failed_suite_checks:
            lines.append(
                "- "
                f"`{_markdown_cell(check.get('metric', ''))}` "
                f"expected {check.get('expected')} actual {check.get('actual')}"
            )
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a multi-repository GitHub repo-agent smoke suite."
    )
    parser.add_argument("manifest", help="Path to repo-agent suite manifest JSON.")
    parser.add_argument("output_dir", help="Directory for suite artifacts.")
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument(
        "--require-success",
        action="store_true",
        help="Exit non-zero unless all suite expectations pass.",
    )
    return parser


def main(argv: list[str] | None = None, opener=None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    report = run_github_repo_agent_suite(args.manifest, args.output_dir, opener=opener)
    if args.output_json:
        Path(args.output_json).write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if args.output_markdown:
        Path(args.output_markdown).write_text(
            render_github_repo_agent_suite_markdown(report),
            encoding="utf-8",
        )
    payload = (
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
        if args.format == "json"
        else render_github_repo_agent_suite_markdown(report)
    )
    print(payload)
    raise SystemExit(0 if report.passed or not args.require_success else 1)


def _run_result_from_report(
    *,
    name: str,
    repo: str,
    report: GitHubRepoAgentReport,
    expected_status: str,
    expected_diagnostic_codes: list[str],
    metric_thresholds: dict[str, float],
    command_args: list[str],
) -> GitHubRepoAgentSuiteRunResult:
    issue_codes = [
        str(code) for code in _list(report.summary.get("diagnostic_issue_codes"))
    ]
    metrics = _suite_metric_snapshot(report.summary)
    metric_checks = _evaluate_metric_thresholds(report.summary, metric_thresholds)
    expectation_passed = report.status == expected_status
    if expected_diagnostic_codes:
        expectation_passed = expectation_passed and all(
            code in issue_codes for code in expected_diagnostic_codes
        )
    if metric_checks:
        expectation_passed = expectation_passed and all(
            bool(check.get("passed")) for check in metric_checks
        )
    return GitHubRepoAgentSuiteRunResult(
        name=name,
        repo=repo,
        output_dir=report.output_dir,
        report_path=report.output_paths.get("agent_json", ""),
        status=report.status,
        passed=report.passed,
        expected_status=expected_status,
        expectation_passed=expectation_passed,
        diagnostic_issue_codes=issue_codes,
        metrics=metrics,
        metric_checks=metric_checks,
        command_args=command_args,
    )


def _failed_run_result(
    *,
    name: str,
    repo: str,
    output_dir: str,
    expected_status: str,
    error: str,
    command_args: list[str] | None = None,
) -> GitHubRepoAgentSuiteRunResult:
    return GitHubRepoAgentSuiteRunResult(
        name=name,
        repo=repo,
        output_dir=output_dir,
        report_path="",
        status="command_error",
        passed=False,
        expected_status=expected_status,
        expectation_passed=False,
        diagnostic_issue_codes=[],
        metrics={},
        metric_checks=[],
        command_args=command_args or [],
        error=error,
    )


def _suite_summary(
    runs: list[GitHubRepoAgentSuiteRunResult],
    *,
    suite_thresholds: dict[str, float],
) -> dict[str, Any]:
    summaries = [run.metrics for run in runs if run.metrics]
    benchmark_runs = [
        run for run in runs
        if _int(run.metrics.get("benchmark_cases", 0)) > 0
    ]
    quality_runs = [
        run for run in runs
        if _int(run.metrics.get("generated_candidates", 0)) > 0
    ]
    code_counts: Counter[str] = Counter()
    code_runs: dict[str, list[str]] = {}
    static_intelligence_status_counts: Counter[str] = Counter()
    static_intelligence_status_runs: dict[str, list[str]] = {}
    static_intelligence_level_counts: Counter[str] = Counter()
    static_intelligence_level_runs: dict[str, list[str]] = {}
    static_intelligence_rule_counts: Counter[str] = Counter()
    static_intelligence_bug_type_counts: Counter[str] = Counter()
    static_intelligence_dynamic_level_counts: Counter[str] = Counter()
    static_intelligence_dynamic_level_runs: dict[str, list[str]] = {}
    static_intelligence_artifact_runs: dict[str, str] = {}
    static_intelligence_quality_scores: list[float] = []
    fallback_reason_counts: Counter[str] = Counter()
    fallback_reason_runs: dict[str, list[str]] = {}
    auto_remediation_action_counts: Counter[str] = Counter()
    auto_remediation_action_runs: dict[str, list[str]] = {}
    benchmarkization_status_counts: Counter[str] = Counter()
    benchmarkization_status_runs: dict[str, list[str]] = {}
    benchmarkization_stage_counts: Counter[str] = Counter()
    benchmarkization_stage_runs: dict[str, list[str]] = {}
    benchmarkization_primary_action_counts: Counter[str] = Counter()
    benchmarkization_primary_action_runs: dict[str, list[str]] = {}
    benchmarkization_remediation_plan_runs: dict[str, str] = {}
    primary_benchmarkization_status_counts: Counter[str] = Counter()
    primary_benchmarkization_status_runs: dict[str, list[str]] = {}
    fallback_benchmarkization_status_counts: Counter[str] = Counter()
    fallback_benchmarkization_status_runs: dict[str, list[str]] = {}
    remediated_benchmarkization_status_counts: Counter[str] = Counter()
    remediated_benchmarkization_status_runs: dict[str, list[str]] = {}
    benchmarkization_fallback_transition_counts: Counter[str] = Counter()
    benchmarkization_fallback_transition_runs: dict[str, list[str]] = {}
    benchmarkization_remediation_transition_counts: Counter[str] = Counter()
    benchmarkization_remediation_transition_runs: dict[str, list[str]] = {}
    primary_benchmarkization_action_counts: Counter[str] = Counter()
    primary_benchmarkization_action_runs: dict[str, list[str]] = {}
    fallback_benchmarkization_action_counts: Counter[str] = Counter()
    fallback_benchmarkization_action_runs: dict[str, list[str]] = {}
    benchmarkization_fallback_audit_runs: list[dict[str, str]] = []
    primary_benchmarkization_remediation_plan_runs: dict[str, str] = {}
    fallback_benchmarkization_remediation_plan_runs: dict[str, str] = {}
    planned_runner_counts: Counter[str] = Counter()
    planned_runner_runs: dict[str, list[str]] = {}
    planned_source_counts: Counter[str] = Counter()
    planned_source_runs: dict[str, list[str]] = {}
    planned_failure_counts: Counter[str] = Counter()
    planned_failure_runs: dict[str, list[str]] = {}
    config_snapshot_status_counts: Counter[str] = Counter()
    config_snapshot_status_runs: dict[str, list[str]] = {}
    framework_signal_counts: Counter[str] = Counter()
    framework_signal_runs: dict[str, list[str]] = {}
    framework_config_status_counts: Counter[str] = Counter()
    framework_config_status_runs: dict[str, list[str]] = {}
    framework_config_reason_counts: Counter[str] = Counter()
    framework_config_reason_runs: dict[str, list[str]] = {}
    planned_env_var_counts: Counter[str] = Counter()
    planned_env_var_runs: dict[str, list[str]] = {}
    ci_python_version_counts: Counter[str] = Counter()
    ci_python_version_runs: dict[str, list[str]] = {}
    ci_config_runs: list[str] = []
    ci_install_candidate_runs: list[str] = []
    ci_test_command_candidate_runs: list[str] = []
    tox_envlist_runs: list[str] = []
    app_bootstrap_runs: list[str] = []
    test_bootstrap_signal_runs: list[str] = []
    retry_strategy_counts: Counter[str] = Counter()
    retry_strategy_runs: dict[str, list[str]] = {}
    setup_install_failure_counts: Counter[str] = Counter()
    setup_install_failure_runs: dict[str, list[str]] = {}
    dynamic_evidence_level_counts: Counter[str] = Counter()
    dynamic_evidence_level_runs: dict[str, list[str]] = {}
    analysis_source_counts: Counter[str] = Counter()
    analysis_source_runs: dict[str, list[str]] = {}
    overlay_trigger_reason_counts: Counter[str] = Counter()
    overlay_trigger_reason_runs: dict[str, list[str]] = {}
    phase2_ready_runs: list[str] = []
    phase3_validation_ready_runs: list[str] = []
    final_status_counts: Counter[str] = Counter()
    final_status_runs: dict[str, list[str]] = {}
    blocked_reason_counts: Counter[str] = Counter()
    blocked_reason_runs: dict[str, list[str]] = {}
    failure_overlay_reason_counts: Counter[str] = Counter()
    failure_overlay_reason_runs: dict[str, list[str]] = {}
    failure_overlay_rule_counts: Counter[str] = Counter()
    failure_overlay_rule_runs: dict[str, list[str]] = {}
    failure_overlay_candidate_rule_counts: Counter[str] = Counter()
    failure_overlay_attempted_rule_counts: Counter[str] = Counter()
    failure_overlay_triggered_rule_counts: Counter[str] = Counter()
    failure_overlay_candidate_rejection_counts: Counter[str] = Counter()
    failure_overlay_candidate_rejection_rule_counts: Counter[str] = Counter()
    failure_overlay_dominant_rejection_counts: Counter[str] = Counter()
    failure_overlay_dominant_rejection_runs: dict[str, list[str]] = {}
    failure_overlay_actionable_extension_counts: Counter[str] = Counter()
    failure_overlay_actionable_extension_runs: dict[str, list[str]] = {}
    failure_overlay_selected_scores: list[float] = []
    fault_localization_reason_counts: Counter[str] = Counter()
    fault_localization_reason_runs: dict[str, list[str]] = {}
    patch_candidate_reason_counts: Counter[str] = Counter()
    patch_candidate_reason_runs: dict[str, list[str]] = {}
    patch_validation_reason_counts: Counter[str] = Counter()
    patch_validation_reason_runs: dict[str, list[str]] = {}
    patch_validation_failure_type_counts: Counter[str] = Counter()
    repair_validation_scope_counts: Counter[str] = Counter()
    repair_validation_scope_runs: dict[str, list[str]] = {}
    regression_validation_status_counts: Counter[str] = Counter()
    regression_validation_status_runs: dict[str, list[str]] = {}
    for run in runs:
        code_counts.update(run.diagnostic_issue_codes)
        for code in run.diagnostic_issue_codes:
            code_runs.setdefault(code, []).append(run.name)
        static_status = str(run.metrics.get("static_intelligence_status") or "")
        if static_status:
            static_intelligence_status_counts.update([static_status])
            static_intelligence_status_runs.setdefault(static_status, []).append(
                run.name
            )
        static_level = str(run.metrics.get("static_intelligence_level") or "")
        if static_level:
            static_intelligence_level_counts.update([static_level])
            static_intelligence_level_runs.setdefault(static_level, []).append(
                run.name
            )
        static_dynamic_level = str(
            run.metrics.get("static_intelligence_dynamic_validation_level") or ""
        )
        if static_dynamic_level:
            static_intelligence_dynamic_level_counts.update([static_dynamic_level])
            static_intelligence_dynamic_level_runs.setdefault(
                static_dynamic_level,
                [],
            ).append(run.name)
        static_artifact = str(
            run.metrics.get("static_intelligence_primary_artifact") or ""
        )
        if static_artifact:
            static_intelligence_artifact_runs[run.name] = static_artifact
        static_quality_score = _float(
            run.metrics.get("static_intelligence_quality_score", 0.0)
        )
        if static_quality_score > 0.0:
            static_intelligence_quality_scores.append(static_quality_score)
        static_intelligence_rule_counts.update(
            {
                str(rule): _int(count)
                for rule, count in _dict(
                    run.metrics.get("static_intelligence_rule_counts")
                ).items()
            }
        )
        static_intelligence_bug_type_counts.update(
            {
                str(bug_type): _int(count)
                for bug_type, count in _dict(
                    run.metrics.get("static_intelligence_bug_type_counts")
                ).items()
            }
        )
        fallback_reason = str(run.metrics.get("fallback_reason") or "")
        if fallback_reason:
            fallback_reason_counts.update([fallback_reason])
            fallback_reason_runs.setdefault(fallback_reason, []).append(run.name)
        auto_remediation_action = str(
            run.metrics.get("auto_remediation_action_id") or ""
        )
        if auto_remediation_action:
            auto_remediation_action_counts.update([auto_remediation_action])
            auto_remediation_action_runs.setdefault(
                auto_remediation_action,
                [],
            ).append(run.name)
        benchmarkization_status = str(
            run.metrics.get("benchmarkization_status") or ""
        )
        if benchmarkization_status:
            benchmarkization_status_counts.update([benchmarkization_status])
            benchmarkization_status_runs.setdefault(
                benchmarkization_status,
                [],
            ).append(run.name)
        benchmarkization_stage = str(run.metrics.get("benchmarkization_stage") or "")
        if benchmarkization_stage:
            benchmarkization_stage_counts.update([benchmarkization_stage])
            benchmarkization_stage_runs.setdefault(
                benchmarkization_stage,
                [],
            ).append(run.name)
        benchmarkization_primary_action = str(
            run.metrics.get("benchmarkization_primary_action_id") or ""
        )
        if benchmarkization_primary_action:
            benchmarkization_primary_action_counts.update(
                [benchmarkization_primary_action]
            )
            benchmarkization_primary_action_runs.setdefault(
                benchmarkization_primary_action,
                [],
            ).append(run.name)
        benchmarkization_plan = str(
            run.metrics.get("benchmarkization_remediation_plan_markdown") or ""
        )
        if benchmarkization_plan:
            benchmarkization_remediation_plan_runs[run.name] = benchmarkization_plan
        primary_benchmarkization_status = str(
            run.metrics.get("primary_benchmarkization_status") or ""
        )
        if primary_benchmarkization_status:
            primary_benchmarkization_status_counts.update(
                [primary_benchmarkization_status]
            )
            primary_benchmarkization_status_runs.setdefault(
                primary_benchmarkization_status,
                [],
            ).append(run.name)
        fallback_benchmarkization_status = str(
            run.metrics.get("fallback_benchmarkization_status") or ""
        )
        if fallback_benchmarkization_status:
            fallback_benchmarkization_status_counts.update(
                [fallback_benchmarkization_status]
            )
            fallback_benchmarkization_status_runs.setdefault(
                fallback_benchmarkization_status,
                [],
            ).append(run.name)
        remediated_benchmarkization_status = str(
            run.metrics.get("remediated_benchmarkization_status") or ""
        )
        if remediated_benchmarkization_status:
            remediated_benchmarkization_status_counts.update(
                [remediated_benchmarkization_status]
            )
            remediated_benchmarkization_status_runs.setdefault(
                remediated_benchmarkization_status,
                [],
            ).append(run.name)
        if primary_benchmarkization_status and fallback_benchmarkization_status:
            transition = (
                f"{primary_benchmarkization_status}->{fallback_benchmarkization_status}"
            )
            benchmarkization_fallback_transition_counts.update([transition])
            benchmarkization_fallback_transition_runs.setdefault(
                transition,
                [],
            ).append(run.name)
        if primary_benchmarkization_status and remediated_benchmarkization_status:
            transition = (
                f"{primary_benchmarkization_status}->{remediated_benchmarkization_status}"
            )
            benchmarkization_remediation_transition_counts.update([transition])
            benchmarkization_remediation_transition_runs.setdefault(
                transition,
                [],
            ).append(run.name)
        primary_benchmarkization_action = str(
            run.metrics.get("primary_benchmarkization_primary_action_id") or ""
        )
        if primary_benchmarkization_action:
            primary_benchmarkization_action_counts.update(
                [primary_benchmarkization_action]
            )
            primary_benchmarkization_action_runs.setdefault(
                primary_benchmarkization_action,
                [],
            ).append(run.name)
        fallback_benchmarkization_action = str(
            run.metrics.get("fallback_benchmarkization_primary_action_id") or ""
        )
        if fallback_benchmarkization_action:
            fallback_benchmarkization_action_counts.update(
                [fallback_benchmarkization_action]
            )
            fallback_benchmarkization_action_runs.setdefault(
                fallback_benchmarkization_action,
                [],
            ).append(run.name)
        primary_plan = str(
            run.metrics.get("primary_benchmarkization_remediation_plan_markdown")
            or ""
        )
        if primary_plan:
            primary_benchmarkization_remediation_plan_runs[run.name] = primary_plan
        fallback_plan = str(
            run.metrics.get("fallback_benchmarkization_remediation_plan_markdown")
            or ""
        )
        if fallback_plan:
            fallback_benchmarkization_remediation_plan_runs[run.name] = fallback_plan
        if (
            fallback_benchmarkization_status
            or primary_benchmarkization_action
            or fallback_benchmarkization_action
            or primary_plan
            or fallback_plan
        ):
            benchmarkization_fallback_audit_runs.append(
                {
                    "run": run.name,
                    "primary_status": primary_benchmarkization_status,
                    "fallback_status": fallback_benchmarkization_status,
                    "transition": (
                        f"{primary_benchmarkization_status}->{fallback_benchmarkization_status}"
                        if primary_benchmarkization_status
                        and fallback_benchmarkization_status
                        else ""
                    ),
                    "primary_action": primary_benchmarkization_action,
                    "fallback_action": fallback_benchmarkization_action,
                    "primary_remediation_plan_markdown": primary_plan,
                    "fallback_remediation_plan_markdown": fallback_plan,
                }
            )
        failure_category = str(
            run.metrics.get("planned_repository_test_failure_category") or ""
        )
        if failure_category and failure_category not in {"none", "not_executed"}:
            planned_failure_counts.update([failure_category])
            planned_failure_runs.setdefault(failure_category, []).append(run.name)
        planned_runner = str(run.metrics.get("planned_repository_test_runner") or "")
        if planned_runner:
            planned_runner_counts.update([planned_runner])
            planned_runner_runs.setdefault(planned_runner, []).append(run.name)
        config_snapshot_status = str(
            run.metrics.get("repository_config_snapshot_status") or ""
        )
        if config_snapshot_status:
            config_snapshot_status_counts.update([config_snapshot_status])
            config_snapshot_status_runs.setdefault(config_snapshot_status, []).append(
                run.name
            )
        planned_source = str(run.metrics.get("planned_repository_test_source") or "")
        if planned_source:
            planned_source_counts.update([planned_source])
            planned_source_runs.setdefault(planned_source, []).append(run.name)
        run_frameworks = sorted(
            {
                str(framework)
                for framework in _list(run.metrics.get("framework_signals"))
                + _list(run.metrics.get("repository_test_frameworks"))
                if str(framework)
            }
        )
        for framework in run_frameworks:
            framework_name = str(framework)
            framework_signal_counts.update([framework_name])
            framework_signal_runs.setdefault(framework_name, []).append(run.name)
        framework_status = str(
            run.metrics.get("repository_test_framework_configuration_status") or ""
        )
        if framework_status:
            framework_config_status_counts.update([framework_status])
            framework_config_status_runs.setdefault(framework_status, []).append(
                run.name
            )
        framework_reason = str(
            run.metrics.get("repository_test_framework_configuration_reason") or ""
        )
        if framework_reason:
            framework_config_reason_counts.update([framework_reason])
            framework_config_reason_runs.setdefault(framework_reason, []).append(
                run.name
            )
        for env_name in _list(
            run.metrics.get("planned_repository_test_environment_variable_names")
        ):
            env_var_name = str(env_name)
            if env_var_name:
                planned_env_var_counts.update([env_var_name])
                planned_env_var_runs.setdefault(env_var_name, []).append(run.name)
        if _int(run.metrics.get("repository_test_ci_config_source_count", 0)) > 0:
            ci_config_runs.append(run.name)
        for version in _list(run.metrics.get("repository_test_ci_python_versions")):
            version_name = str(version)
            if version_name:
                ci_python_version_counts.update([version_name])
                ci_python_version_runs.setdefault(version_name, []).append(run.name)
        if _list(run.metrics.get("repository_test_ci_install_command_candidates")):
            ci_install_candidate_runs.append(run.name)
        if _list(run.metrics.get("repository_test_ci_test_command_candidates")):
            ci_test_command_candidate_runs.append(run.name)
        if _list(run.metrics.get("repository_test_tox_envlist")):
            tox_envlist_runs.append(run.name)
        if _int(run.metrics.get("repository_test_app_bootstrap_candidate_count", 0)) > 0:
            app_bootstrap_runs.append(run.name)
        if _int(run.metrics.get("repository_test_bootstrap_signal_count", 0)) > 0:
            test_bootstrap_signal_runs.append(run.name)
        if bool(run.metrics.get("repository_test_retry_recommended", False)):
            retry_strategy = str(
                run.metrics.get("repository_test_retry_strategy") or "unknown"
            )
            retry_strategy_counts.update([retry_strategy])
            retry_strategy_runs.setdefault(retry_strategy, []).append(run.name)
        setup_install_failure = str(
            run.metrics.get(
                "repository_test_environment_setup_install_failure_category"
            )
            or ""
        )
        if setup_install_failure and setup_install_failure != "none":
            setup_install_failure_counts.update([setup_install_failure])
            setup_install_failure_runs.setdefault(
                setup_install_failure,
                [],
            ).append(run.name)
        dynamic_level = str(
            run.metrics.get("repository_test_dynamic_evidence_level") or ""
        )
        if dynamic_level and dynamic_level not in {"none", "not_executed"}:
            dynamic_evidence_level_counts.update([dynamic_level])
            dynamic_evidence_level_runs.setdefault(dynamic_level, []).append(run.name)
        analysis_source = str(run.metrics.get("repository_test_analysis_source") or "")
        if analysis_source:
            analysis_source_counts.update([analysis_source])
            analysis_source_runs.setdefault(analysis_source, []).append(run.name)
        overlay_trigger_reason = str(
            run.metrics.get("repository_test_overlay_trigger_reason") or ""
        )
        if overlay_trigger_reason:
            overlay_trigger_reason_counts.update([overlay_trigger_reason])
            overlay_trigger_reason_runs.setdefault(overlay_trigger_reason, []).append(
                run.name
            )
        if bool(run.metrics.get("repository_test_phase2_ready", False)):
            phase2_ready_runs.append(run.name)
        if bool(run.metrics.get("repository_test_phase3_validation_ready", False)):
            phase3_validation_ready_runs.append(run.name)
        final_status = str(run.metrics.get("repository_test_final_status") or "")
        if final_status:
            final_status_counts.update([final_status])
            final_status_runs.setdefault(final_status, []).append(run.name)
            if final_status == "blocked":
                final_reason = str(
                    run.metrics.get("repository_test_final_reason") or "unknown"
                )
                blocked_reason_counts.update([final_reason])
                blocked_reason_runs.setdefault(final_reason, []).append(run.name)
        overlay_status = str(
            run.metrics.get("repository_test_failure_overlay_status") or ""
        )
        if overlay_status:
            overlay_reason = str(
                run.metrics.get("repository_test_failure_overlay_reason")
                or "unknown"
            )
            failure_overlay_reason_counts.update([overlay_reason])
            failure_overlay_reason_runs.setdefault(overlay_reason, []).append(run.name)
            overlay_rule = str(
                run.metrics.get("repository_test_failure_overlay_selected_rule") or ""
            )
            if overlay_rule:
                failure_overlay_rule_counts.update([overlay_rule])
                failure_overlay_rule_runs.setdefault(overlay_rule, []).append(run.name)
            selected_score = _float(
                run.metrics.get("repository_test_failure_overlay_selected_score", 0.0)
            )
            if selected_score > 0.0:
                failure_overlay_selected_scores.append(selected_score)
            failure_overlay_candidate_rule_counts.update(
                {
                    str(rule): _int(count)
                    for rule, count in _dict(
                        run.metrics.get(
                            "repository_test_failure_overlay_candidate_rule_counts"
                        )
                    ).items()
                }
            )
            failure_overlay_attempted_rule_counts.update(
                {
                    str(rule): _int(count)
                    for rule, count in _dict(
                        run.metrics.get(
                            "repository_test_failure_overlay_attempted_rule_counts"
                        )
                    ).items()
                }
            )
            failure_overlay_triggered_rule_counts.update(
                {
                    str(rule): _int(count)
                    for rule, count in _dict(
                        run.metrics.get(
                            "repository_test_failure_overlay_triggered_rule_counts"
                        )
                    ).items()
                }
            )
            failure_overlay_candidate_rejection_counts.update(
                {
                    str(reason): _int(count)
                    for reason, count in _dict(
                        run.metrics.get(
                            "repository_test_failure_overlay_candidate_rejection_counts"
                        )
                    ).items()
                }
            )
            failure_overlay_candidate_rejection_rule_counts.update(
                {
                    str(rule): _int(count)
                    for rule, count in _dict(
                        run.metrics.get(
                            "repository_test_failure_overlay_candidate_rejection_rule_counts"
                        )
                    ).items()
                }
            )
            dominant_reason = str(
                run.metrics.get(
                    "repository_test_failure_overlay_dominant_rejection_reason"
                )
                or ""
            )
            if dominant_reason:
                dominant_count = max(
                    1,
                    _int(
                        run.metrics.get(
                            "repository_test_failure_overlay_dominant_rejection_count",
                            0,
                        )
                    ),
                )
                failure_overlay_dominant_rejection_counts.update(
                    {dominant_reason: dominant_count}
                )
                failure_overlay_dominant_rejection_runs.setdefault(
                    dominant_reason,
                    [],
                ).append(run.name)
            actionable_extension = _dict(
                run.metrics.get(
                    "repository_test_failure_overlay_next_actionable_extension"
                )
            )
            actionable_reason = str(actionable_extension.get("reason") or "")
            if actionable_reason:
                actionable_count = max(1, _int(actionable_extension.get("count", 0)))
                failure_overlay_actionable_extension_counts.update(
                    {actionable_reason: actionable_count}
                )
                failure_overlay_actionable_extension_runs.setdefault(
                    actionable_reason,
                    [],
                ).append(run.name)
        fault_localization_status = str(
            run.metrics.get("repository_test_fault_localization_status") or ""
        )
        if fault_localization_status:
            fault_reason = str(
                run.metrics.get("repository_test_fault_localization_reason")
                or "unknown"
            )
            fault_localization_reason_counts.update([fault_reason])
            fault_localization_reason_runs.setdefault(fault_reason, []).append(run.name)
        patch_candidate_status = str(
            run.metrics.get("repository_test_patch_candidates_status") or ""
        )
        if patch_candidate_status:
            patch_reason = str(
                run.metrics.get("repository_test_patch_candidates_reason")
                or "unknown"
            )
            patch_candidate_reason_counts.update([patch_reason])
            patch_candidate_reason_runs.setdefault(patch_reason, []).append(run.name)
        patch_validation_status = str(
            run.metrics.get("repository_test_patch_validation_status") or ""
        )
        if patch_validation_status:
            validation_reason = str(
                run.metrics.get("repository_test_patch_validation_reason")
                or "unknown"
            )
            patch_validation_reason_counts.update([validation_reason])
            patch_validation_reason_runs.setdefault(validation_reason, []).append(
                run.name
            )
            patch_validation_failure_type_counts.update(
                {
                    str(failure_type): _int(count)
                    for failure_type, count in _dict(
                        run.metrics.get(
                            "repository_test_patch_validation_failure_type_counts"
                        )
                    ).items()
                }
            )
        repair_scope = str(
            run.metrics.get("repository_test_repair_validation_scope") or ""
        )
        if repair_scope:
            repair_validation_scope_counts.update([repair_scope])
            repair_validation_scope_runs.setdefault(repair_scope, []).append(run.name)
        regression_status = str(
            run.metrics.get("repository_test_regression_validation_status") or ""
        )
        if regression_status:
            regression_validation_status_counts.update([regression_status])
            regression_validation_status_runs.setdefault(
                regression_status,
                [],
            ).append(run.name)
    run_count = len(runs)
    command_failed_count = sum(1 for run in runs if run.status == "command_error")
    expectation_passed_count = sum(1 for run in runs if run.expectation_passed)
    expectation_failed_count = sum(1 for run in runs if not run.expectation_passed)
    metric_check_count = sum(len(run.metric_checks) for run in runs)
    metric_check_failed_count = sum(
        1
        for run in runs
        for check in run.metric_checks
        if not check.get("passed")
    )
    failure_overlay_count = sum(
        1
        for summary in summaries
        if str(summary.get("repository_test_failure_overlay_status") or "")
    )
    failure_overlay_success_count = sum(
        1
        for summary in summaries
        if str(summary.get("repository_test_failure_overlay_status") or "")
        == "pass"
    )
    failure_overlay_attempted_case_count = sum(
        _int(summary.get("repository_test_failure_overlay_attempted_cases", 0))
        for summary in summaries
    )
    failure_overlay_supported_candidate_count = sum(
        _int(
            summary.get(
                "repository_test_failure_overlay_supported_candidates",
                0,
            )
        )
        for summary in summaries
    )
    failure_overlay_candidate_rule_total = sum(
        _int(count) for count in failure_overlay_candidate_rule_counts.values()
    )
    failure_overlay_attempted_rule_total = sum(
        _int(count) for count in failure_overlay_attempted_rule_counts.values()
    )
    failure_overlay_triggered_rule_total = sum(
        _int(count) for count in failure_overlay_triggered_rule_counts.values()
    )
    failure_overlay_candidate_rule_count = len(
        [rule for rule, count in failure_overlay_candidate_rule_counts.items() if count]
    )
    failure_overlay_attempted_rule_count = len(
        [rule for rule, count in failure_overlay_attempted_rule_counts.items() if count]
    )
    failure_overlay_triggered_rule_count = len(
        [rule for rule, count in failure_overlay_triggered_rule_counts.items() if count]
    )
    summary = {
        "run_count": run_count,
        "completed_count": sum(1 for run in runs if run.status != "command_error"),
        "command_failed_count": command_failed_count,
        "command_error_runs": [
            run.name for run in runs if run.status == "command_error"
        ],
        "agent_passed_count": sum(1 for run in runs if run.passed),
        "agent_failed_count": sum(
            1 for run in runs
            if run.status != "command_error" and not run.passed
        ),
        "agent_failed_runs": [
            run.name for run in runs
            if run.status != "command_error" and not run.passed
        ],
        "expectation_passed_count": expectation_passed_count,
        "expectation_failed_count": expectation_failed_count,
        "expectation_pass_rate": _ratio(expectation_passed_count, run_count),
        "expectation_failed_runs": [
            run.name for run in runs if not run.expectation_passed
        ],
        "metric_check_count": metric_check_count,
        "metric_check_failed_count": metric_check_failed_count,
        "metric_check_pass_rate": _ratio(
            metric_check_count - metric_check_failed_count,
            metric_check_count,
        ),
        "metric_check_failed_runs": [
            run.name
            for run in runs
            if any(not check.get("passed") for check in run.metric_checks)
        ],
        "generated_candidates": sum(
            _int(summary.get("generated_candidates", 0)) for summary in summaries
        ),
        "static_intelligence_run_count": _int(
            sum(static_intelligence_status_counts.values())
        ),
        "static_intelligence_analysis_ready_count": _int(
            static_intelligence_status_counts.get("analysis_ready", 0)
        ),
        "static_intelligence_source_inventory_ready_count": _int(
            static_intelligence_status_counts.get("source_inventory_ready", 0)
        ),
        "static_intelligence_blocked_count": _int(
            static_intelligence_status_counts.get("blocked", 0)
        ),
        "static_intelligence_selected_signal_count": sum(
            _int(summary.get("static_intelligence_selected_signal_count", 0))
            for summary in summaries
        ),
        "static_intelligence_total_signal_count": sum(
            _int(summary.get("static_intelligence_total_signal_count", 0))
            for summary in summaries
        ),
        "static_intelligence_candidate_limit_applied_count": sum(
            1
            for summary in summaries
            if bool(summary.get("static_intelligence_candidate_limit_applied", False))
        ),
        "static_intelligence_average_quality_score": (
            round(
                sum(static_intelligence_quality_scores)
                / len(static_intelligence_quality_scores),
                6,
            )
            if static_intelligence_quality_scores
            else 0.0
        ),
        "static_intelligence_status_counts": dict(
            sorted(static_intelligence_status_counts.items())
        ),
        "static_intelligence_status_runs": {
            status: sorted(names)
            for status, names in sorted(static_intelligence_status_runs.items())
        },
        "static_intelligence_level_counts": dict(
            sorted(static_intelligence_level_counts.items())
        ),
        "static_intelligence_level_runs": {
            level: sorted(names)
            for level, names in sorted(static_intelligence_level_runs.items())
        },
        "static_intelligence_rule_counts": dict(
            sorted(static_intelligence_rule_counts.items())
        ),
        "static_intelligence_bug_type_counts": dict(
            sorted(static_intelligence_bug_type_counts.items())
        ),
        "static_intelligence_dynamic_validation_level_counts": dict(
            sorted(static_intelligence_dynamic_level_counts.items())
        ),
        "static_intelligence_dynamic_validation_level_runs": {
            level: sorted(names)
            for level, names in sorted(
                static_intelligence_dynamic_level_runs.items()
            )
        },
        "static_intelligence_primary_artifact_runs": dict(
            sorted(static_intelligence_artifact_runs.items())
        ),
        "benchmark_cases": sum(
            _int(summary.get("benchmark_cases", 0)) for summary in summaries
        ),
        "fallback_attempted_count": sum(
            1 for summary in summaries if bool(summary.get("fallback_attempted", False))
        ),
        "fallback_used_count": sum(
            1 for summary in summaries if bool(summary.get("fallback_used", False))
        ),
        "fallback_improved_count": sum(
            1 for summary in summaries if bool(summary.get("fallback_improved", False))
        ),
        "fallback_recovered_count": sum(
            1 for summary in summaries if bool(summary.get("fallback_recovered", False))
        ),
        "fallback_attempted_runs": sorted(
            run.name
            for run in runs
            if bool(run.metrics.get("fallback_attempted", False))
        ),
        "fallback_used_runs": sorted(
            run.name for run in runs if bool(run.metrics.get("fallback_used", False))
        ),
        "fallback_improved_runs": sorted(
            run.name
            for run in runs
            if bool(run.metrics.get("fallback_improved", False))
        ),
        "fallback_recovered_runs": sorted(
            run.name
            for run in runs
            if bool(run.metrics.get("fallback_recovered", False))
        ),
        "fallback_reason_counts": dict(sorted(fallback_reason_counts.items())),
        "fallback_reason_runs": {
            reason: sorted(names)
            for reason, names in sorted(fallback_reason_runs.items())
        },
        "fallback_candidate_delta": sum(
            max(
                0,
                _int(summary.get("fallback_generated_candidates", 0))
                - _int(summary.get("primary_generated_candidates", 0)),
            )
            for summary in summaries
        ),
        "fallback_benchmark_case_delta": sum(
            max(
                0,
                _int(summary.get("fallback_benchmark_cases", 0))
                - _int(summary.get("primary_benchmark_cases", 0)),
            )
            for summary in summaries
        ),
        "auto_remediation_attempted_count": sum(
            1
            for summary in summaries
            if bool(summary.get("auto_remediation_attempted", False))
        ),
        "auto_remediation_used_count": sum(
            1
            for summary in summaries
            if bool(summary.get("auto_remediation_used", False))
        ),
        "auto_remediation_improved_count": sum(
            1
            for summary in summaries
            if bool(summary.get("auto_remediation_improved", False))
        ),
        "auto_remediation_attempted_runs": sorted(
            run.name
            for run in runs
            if bool(run.metrics.get("auto_remediation_attempted", False))
        ),
        "auto_remediation_used_runs": sorted(
            run.name
            for run in runs
            if bool(run.metrics.get("auto_remediation_used", False))
        ),
        "auto_remediation_improved_runs": sorted(
            run.name
            for run in runs
            if bool(run.metrics.get("auto_remediation_improved", False))
        ),
        "auto_remediation_action_counts": dict(
            sorted(auto_remediation_action_counts.items())
        ),
        "auto_remediation_action_runs": {
            action: sorted(names)
            for action, names in sorted(auto_remediation_action_runs.items())
        },
        "auto_remediation_benchmark_case_delta": sum(
            max(
                0,
                _int(summary.get("remediated_benchmark_cases", 0))
                - _int(summary.get("primary_benchmark_cases", 0)),
            )
            for summary in summaries
        ),
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
        "benchmarkization_remediation_plan_count": len(
            benchmarkization_remediation_plan_runs
        ),
        "benchmarkization_remediation_plan_runs": dict(
            sorted(benchmarkization_remediation_plan_runs.items())
        ),
        "primary_benchmarkization_status_counts": dict(
            sorted(primary_benchmarkization_status_counts.items())
        ),
        "primary_benchmarkization_status_runs": {
            status: sorted(names)
            for status, names in sorted(primary_benchmarkization_status_runs.items())
        },
        "fallback_benchmarkization_status_counts": dict(
            sorted(fallback_benchmarkization_status_counts.items())
        ),
        "fallback_benchmarkization_status_runs": {
            status: sorted(names)
            for status, names in sorted(fallback_benchmarkization_status_runs.items())
        },
        "remediated_benchmarkization_status_counts": dict(
            sorted(remediated_benchmarkization_status_counts.items())
        ),
        "remediated_benchmarkization_status_runs": {
            status: sorted(names)
            for status, names in sorted(
                remediated_benchmarkization_status_runs.items()
            )
        },
        "benchmarkization_fallback_transition_counts": dict(
            sorted(benchmarkization_fallback_transition_counts.items())
        ),
        "benchmarkization_fallback_transition_runs": {
            transition: sorted(names)
            for transition, names in sorted(
                benchmarkization_fallback_transition_runs.items()
            )
        },
        "benchmarkization_remediation_transition_counts": dict(
            sorted(benchmarkization_remediation_transition_counts.items())
        ),
        "benchmarkization_remediation_transition_runs": {
            transition: sorted(names)
            for transition, names in sorted(
                benchmarkization_remediation_transition_runs.items()
            )
        },
        "primary_benchmarkization_action_counts": dict(
            sorted(primary_benchmarkization_action_counts.items())
        ),
        "primary_benchmarkization_action_runs": {
            action: sorted(names)
            for action, names in sorted(primary_benchmarkization_action_runs.items())
        },
        "fallback_benchmarkization_action_counts": dict(
            sorted(fallback_benchmarkization_action_counts.items())
        ),
        "fallback_benchmarkization_action_runs": {
            action: sorted(names)
            for action, names in sorted(fallback_benchmarkization_action_runs.items())
        },
        "benchmarkization_fallback_audit_count": len(
            benchmarkization_fallback_audit_runs
        ),
        "benchmarkization_fallback_audit_runs": sorted(
            benchmarkization_fallback_audit_runs,
            key=lambda item: item.get("run", ""),
        ),
        "primary_benchmarkization_remediation_plan_count": len(
            primary_benchmarkization_remediation_plan_runs
        ),
        "primary_benchmarkization_remediation_plan_runs": dict(
            sorted(primary_benchmarkization_remediation_plan_runs.items())
        ),
        "fallback_benchmarkization_remediation_plan_count": len(
            fallback_benchmarkization_remediation_plan_runs
        ),
        "fallback_benchmarkization_remediation_plan_runs": dict(
            sorted(fallback_benchmarkization_remediation_plan_runs.items())
        ),
        "benchmark_run_count": len(benchmark_runs),
        "quality_run_count": len(quality_runs),
        "repository_test_environment_setup_count": sum(
            1
            for summary in summaries
            if str(summary.get("repository_test_environment_setup_status") or "")
        ),
        "repository_test_environment_setup_supported_count": sum(
            1
            for summary in summaries
            if bool(summary.get("repository_test_environment_setup_supported", False))
        ),
        "repository_test_environment_setup_executed_count": sum(
            1
            for summary in summaries
            if bool(
                summary.get(
                    "repository_test_environment_setup_result_executed",
                    False,
                )
            )
        ),
        "repository_test_environment_setup_passed_count": sum(
            1
            for summary in summaries
            if str(summary.get("repository_test_environment_setup_result_status") or "")
            == "pass"
        ),
        "repository_test_environment_setup_install_fallback_count": sum(
            1
            for summary in summaries
            if bool(
                summary.get(
                    "repository_test_environment_setup_install_fallback_executed",
                    False,
                )
            )
        ),
        "repository_test_environment_setup_install_fallback_success_count": sum(
            1
            for summary in summaries
            if bool(
                summary.get(
                    "repository_test_environment_setup_install_fallback_executed",
                    False,
                )
            )
            and _int(
                summary.get(
                    "repository_test_environment_setup_install_fallback_returncode",
                    -1,
                )
            )
            == 0
        ),
        "repository_test_environment_setup_install_failure_counts": dict(
            sorted(setup_install_failure_counts.items())
        ),
        "repository_test_environment_setup_install_failure_runs": {
            category: sorted(names)
            for category, names in sorted(setup_install_failure_runs.items())
        },
        "repository_test_plan_count": sum(
            1
            for summary in summaries
            if str(summary.get("repository_test_execution_plan_status") or "")
        ),
        "repository_test_plan_executable_count": sum(
            1
            for summary in summaries
            if bool(summary.get("planned_repository_test_executable_now", False))
        ),
        "repository_test_plan_narrow_count": sum(
            1
            for summary in summaries
            if str(summary.get("planned_repository_test_level") or "") == "narrow"
        ),
        "planned_repository_test_executed_count": sum(
            1
            for summary in summaries
            if bool(summary.get("planned_repository_test_result_executed", False))
        ),
        "planned_repository_test_passed_count": sum(
            1
            for summary in summaries
            if str(summary.get("planned_repository_test_result_status") or "")
            == "pass"
        ),
        "planned_repository_test_venv_python_count": sum(
            1
            for summary in summaries
            if str(summary.get("planned_repository_test_python_source") or "")
            == "repository_test_environment_setup"
        ),
        "planned_repository_test_runner_counts": dict(
            sorted(planned_runner_counts.items())
        ),
        "planned_repository_test_runner_kind_count": len(planned_runner_counts),
        "planned_repository_test_runner_runs": {
            runner: sorted(names)
            for runner, names in sorted(planned_runner_runs.items())
        },
        "planned_repository_test_source_counts": dict(
            sorted(planned_source_counts.items())
        ),
        "planned_repository_test_source_runs": {
            source: sorted(names)
            for source, names in sorted(planned_source_runs.items())
        },
        "planned_repository_test_ci_candidate_count": sum(
            _int(summary.get("planned_repository_test_ci_candidate_count", 0))
            for summary in summaries
        ),
        "planned_repository_test_ci_candidate_runs": sorted(
            {
                run.name
                for run in runs
                if _int(
                    run.metrics.get("planned_repository_test_ci_candidate_count", 0)
                )
                > 0
            }
        ),
        "repository_config_snapshot_count": sum(
            1
            for summary in summaries
            if str(summary.get("repository_config_snapshot_status") or "")
        ),
        "repository_config_snapshot_file_count": sum(
            _int(summary.get("repository_config_snapshot_file_count", 0))
            for summary in summaries
        ),
        "repository_config_snapshot_status_counts": dict(
            sorted(config_snapshot_status_counts.items())
        ),
        "repository_config_snapshot_status_runs": {
            status: sorted(names)
            for status, names in sorted(config_snapshot_status_runs.items())
        },
        "framework_signal_counts": dict(sorted(framework_signal_counts.items())),
        "framework_signal_runs": {
            framework: sorted(names)
            for framework, names in sorted(framework_signal_runs.items())
        },
        "repository_test_framework_configuration_status_counts": dict(
            sorted(framework_config_status_counts.items())
        ),
        "repository_test_framework_configuration_status_runs": {
            status: sorted(names)
            for status, names in sorted(framework_config_status_runs.items())
        },
        "repository_test_framework_configuration_reason_counts": dict(
            sorted(framework_config_reason_counts.items())
        ),
        "repository_test_framework_configuration_reason_runs": {
            reason: sorted(names)
            for reason, names in sorted(framework_config_reason_runs.items())
        },
        "repository_test_pytest_configured_count": sum(
            1
            for summary in summaries
            if _int(summary.get("repository_test_pytest_config_source_count", 0)) > 0
        ),
        "repository_test_pytest_testpath_run_count": sum(
            1
            for summary in summaries
            if _list(summary.get("repository_test_pytest_testpaths"))
        ),
        "repository_test_pytest_addopts_run_count": sum(
            1
            for summary in summaries
            if _list(summary.get("repository_test_pytest_addopts"))
        ),
        "repository_test_ci_configured_count": len(sorted(set(ci_config_runs))),
        "repository_test_ci_configured_runs": sorted(set(ci_config_runs)),
        "repository_test_ci_python_version_counts": dict(
            sorted(ci_python_version_counts.items())
        ),
        "repository_test_ci_python_version_runs": {
            version: sorted(names)
            for version, names in sorted(ci_python_version_runs.items())
        },
        "repository_test_ci_install_candidate_count": sum(
            len(_list(summary.get("repository_test_ci_install_command_candidates")))
            for summary in summaries
        ),
        "repository_test_ci_install_candidate_runs": sorted(
            set(ci_install_candidate_runs)
        ),
        "repository_test_ci_test_command_candidate_count": sum(
            len(_list(summary.get("repository_test_ci_test_command_candidates")))
            for summary in summaries
        ),
        "repository_test_ci_test_command_candidate_runs": sorted(
            set(ci_test_command_candidate_runs)
        ),
        "repository_test_tox_envlist_run_count": len(sorted(set(tox_envlist_runs))),
        "repository_test_tox_envlist_runs": sorted(set(tox_envlist_runs)),
        "planned_repository_test_environment_variable_counts": dict(
            sorted(planned_env_var_counts.items())
        ),
        "planned_repository_test_environment_variable_runs": {
            name: sorted(names)
            for name, names in sorted(planned_env_var_runs.items())
        },
        "repository_test_app_bootstrap_candidate_count": sum(
            _int(summary.get("repository_test_app_bootstrap_candidate_count", 0))
            for summary in summaries
        ),
        "repository_test_app_bootstrap_runs": sorted(app_bootstrap_runs),
        "repository_test_bootstrap_signal_count": sum(
            _int(summary.get("repository_test_bootstrap_signal_count", 0))
            for summary in summaries
        ),
        "repository_test_bootstrap_signal_runs": sorted(test_bootstrap_signal_runs),
        "planned_repository_test_failure_category_counts": dict(
            sorted(planned_failure_counts.items())
        ),
        "planned_repository_test_failure_category_runs": {
            category: sorted(names)
            for category, names in sorted(planned_failure_runs.items())
        },
        "repository_test_retry_recommended_count": sum(
            1
            for summary in summaries
            if bool(summary.get("repository_test_retry_recommended", False))
        ),
        "repository_test_retry_strategy_counts": dict(
            sorted(retry_strategy_counts.items())
        ),
        "repository_test_retry_strategy_runs": {
            strategy: sorted(names)
            for strategy, names in sorted(retry_strategy_runs.items())
        },
        "repository_test_retry_executed_count": sum(
            1
            for summary in summaries
            if bool(summary.get("repository_test_retry_executed", False))
        ),
        "repository_test_retry_passed_count": sum(
            1
            for summary in summaries
            if str(summary.get("repository_test_retry_execution_status") or "")
            == "pass"
        ),
        "repository_test_dynamic_evidence_count": sum(
            1
            for summary in summaries
            if str(summary.get("repository_test_dynamic_evidence_level") or "")
            not in {"", "none", "not_executed"}
        ),
        "repository_test_dynamic_localization_ready_count": sum(
            1
            for summary in summaries
            if bool(
                summary.get(
                    "repository_test_dynamic_usable_for_localization",
                    False,
                )
            )
        ),
        "repository_test_dynamic_regression_validation_ready_count": sum(
            1
            for summary in summaries
            if bool(
                summary.get(
                    "repository_test_dynamic_usable_for_regression_validation",
                    False,
                )
            )
        ),
        "repository_test_dynamic_patch_validation_ready_count": sum(
            1
            for summary in summaries
            if bool(
                summary.get(
                    "repository_test_dynamic_usable_for_patch_validation",
                    False,
                )
            )
        ),
        "repository_test_dynamic_evidence_level_counts": dict(
            sorted(dynamic_evidence_level_counts.items())
        ),
        "repository_test_dynamic_evidence_level_kind_count": len(
            dynamic_evidence_level_counts
        ),
        "repository_test_dynamic_evidence_level_runs": {
            level: sorted(names)
            for level, names in sorted(dynamic_evidence_level_runs.items())
        },
        "repository_test_analysis_source_counts": dict(
            sorted(analysis_source_counts.items())
        ),
        "repository_test_analysis_source_runs": {
            source: sorted(names)
            for source, names in sorted(analysis_source_runs.items())
        },
        "repository_test_overlay_trigger_reason_counts": dict(
            sorted(overlay_trigger_reason_counts.items())
        ),
        "repository_test_overlay_trigger_reason_runs": {
            reason: sorted(names)
            for reason, names in sorted(overlay_trigger_reason_runs.items())
        },
        "repository_test_phase2_ready_count": len(phase2_ready_runs),
        "repository_test_phase2_ready_runs": sorted(phase2_ready_runs),
        "repository_test_phase3_validation_ready_count": len(
            phase3_validation_ready_runs
        ),
        "repository_test_phase3_validation_ready_runs": sorted(
            phase3_validation_ready_runs
        ),
        "repository_test_final_status_counts": dict(
            sorted(final_status_counts.items())
        ),
        "repository_test_repaired_count": _int(final_status_counts.get("repaired", 0)),
        "repository_test_blocked_count": _int(final_status_counts.get("blocked", 0)),
        "repository_test_final_status_runs": {
            status: sorted(names)
            for status, names in sorted(final_status_runs.items())
        },
        "repository_test_blocked_reason_counts": dict(
            sorted(blocked_reason_counts.items())
        ),
        "repository_test_blocked_reason_runs": {
            reason: sorted(names)
            for reason, names in sorted(blocked_reason_runs.items())
        },
        "repository_test_failure_overlay_count": failure_overlay_count,
        "repository_test_failure_overlay_success_count": (
            failure_overlay_success_count
        ),
        "repository_test_failure_overlay_success_rate": _ratio(
            failure_overlay_success_count,
            failure_overlay_count,
        ),
        "repository_test_failure_overlay_attempted_case_count": (
            failure_overlay_attempted_case_count
        ),
        "repository_test_failure_overlay_supported_candidate_count": (
            failure_overlay_supported_candidate_count
        ),
        "repository_test_failure_overlay_candidate_attempt_rate": _ratio(
            failure_overlay_attempted_case_count,
            failure_overlay_supported_candidate_count,
        ),
        "repository_test_failure_overlay_attempt_trigger_rate": _ratio(
            failure_overlay_triggered_rule_total,
            failure_overlay_attempted_rule_total,
        ),
        "repository_test_failure_overlay_average_selected_score": (
            round(
                sum(failure_overlay_selected_scores)
                / len(failure_overlay_selected_scores),
                6,
            )
            if failure_overlay_selected_scores
            else 0.0
        ),
        "repository_test_failure_overlay_candidate_rule_count": (
            failure_overlay_candidate_rule_count
        ),
        "repository_test_failure_overlay_attempted_rule_count": (
            failure_overlay_attempted_rule_count
        ),
        "repository_test_failure_overlay_triggered_rule_count": (
            failure_overlay_triggered_rule_count
        ),
        "repository_test_failure_overlay_attempted_rule_coverage_rate": _ratio(
            failure_overlay_attempted_rule_count,
            failure_overlay_candidate_rule_count,
        ),
        "repository_test_failure_overlay_triggered_rule_coverage_rate": _ratio(
            failure_overlay_triggered_rule_count,
            failure_overlay_candidate_rule_count,
        ),
        "repository_test_failure_overlay_reason_counts": dict(
            sorted(failure_overlay_reason_counts.items())
        ),
        "repository_test_failure_overlay_reason_runs": {
            reason: sorted(names)
            for reason, names in sorted(failure_overlay_reason_runs.items())
        },
        "repository_test_failure_overlay_rule_counts": dict(
            sorted(failure_overlay_rule_counts.items())
        ),
        "repository_test_failure_overlay_candidate_rule_counts": dict(
            sorted(failure_overlay_candidate_rule_counts.items())
        ),
        "repository_test_failure_overlay_attempted_rule_counts": dict(
            sorted(failure_overlay_attempted_rule_counts.items())
        ),
        "repository_test_failure_overlay_triggered_rule_counts": dict(
            sorted(failure_overlay_triggered_rule_counts.items())
        ),
        "repository_test_failure_overlay_candidate_rejection_counts": dict(
            sorted(failure_overlay_candidate_rejection_counts.items())
        ),
        "repository_test_failure_overlay_candidate_rejection_rule_counts": dict(
            sorted(failure_overlay_candidate_rejection_rule_counts.items())
        ),
        "repository_test_failure_overlay_dominant_rejection_counts": dict(
            sorted(failure_overlay_dominant_rejection_counts.items())
        ),
        "repository_test_failure_overlay_dominant_rejection_runs": {
            reason: sorted(names)
            for reason, names in sorted(
                failure_overlay_dominant_rejection_runs.items()
            )
        },
        "repository_test_failure_overlay_actionable_extension_counts": dict(
            sorted(failure_overlay_actionable_extension_counts.items())
        ),
        "repository_test_failure_overlay_actionable_extension_runs": {
            reason: sorted(names)
            for reason, names in sorted(
                failure_overlay_actionable_extension_runs.items()
            )
        },
        "repository_test_failure_overlay_rule_runs": {
            rule: sorted(names)
            for rule, names in sorted(failure_overlay_rule_runs.items())
        },
        "repository_test_fault_localization_count": sum(
            1
            for summary in summaries
            if str(summary.get("repository_test_fault_localization_status") or "")
        ),
        "repository_test_fault_localization_passed_count": sum(
            1
            for summary in summaries
            if str(summary.get("repository_test_fault_localization_status") or "")
            == "pass"
        ),
        "repository_test_fault_localization_reason_counts": dict(
            sorted(fault_localization_reason_counts.items())
        ),
        "repository_test_fault_localization_reason_runs": {
            reason: sorted(names)
            for reason, names in sorted(fault_localization_reason_runs.items())
        },
        "repository_test_patch_candidate_run_count": sum(
            1
            for summary in summaries
            if str(summary.get("repository_test_patch_candidates_status") or "")
        ),
        "repository_test_patch_candidate_count": sum(
            _int(summary.get("repository_test_patch_candidate_count", 0))
            for summary in summaries
        ),
        "repository_test_patch_candidate_passed_run_count": sum(
            1
            for summary in summaries
            if str(summary.get("repository_test_patch_candidates_status") or "")
            == "pass"
        ),
        "repository_test_patch_candidate_reason_counts": dict(
            sorted(patch_candidate_reason_counts.items())
        ),
        "repository_test_patch_candidate_reason_runs": {
            reason: sorted(names)
            for reason, names in sorted(patch_candidate_reason_runs.items())
        },
        "repository_test_patch_validation_run_count": sum(
            1
            for summary in summaries
            if str(summary.get("repository_test_patch_validation_status") or "")
        ),
        "repository_test_patch_validation_success_run_count": sum(
            1
            for summary in summaries
            if str(summary.get("repository_test_patch_validation_status") or "")
            == "pass"
        ),
        "repository_test_patch_validation_executed_count": sum(
            _int(summary.get("repository_test_patch_validation_executed_count", 0))
            for summary in summaries
        ),
        "repository_test_patch_validation_success_count": sum(
            _int(summary.get("repository_test_patch_validation_success_count", 0))
            for summary in summaries
        ),
        "repository_test_repair_ready_count": sum(
            1
            for summary in summaries
            if bool(summary.get("repository_test_repair_ready", False))
        ),
        "repository_test_regression_ready_count": sum(
            1
            for summary in summaries
            if bool(summary.get("repository_test_regression_ready", False))
        ),
        "repository_test_repair_validation_scope_counts": dict(
            sorted(repair_validation_scope_counts.items())
        ),
        "repository_test_repair_validation_scope_runs": {
            scope: sorted(names)
            for scope, names in sorted(repair_validation_scope_runs.items())
        },
        "repository_test_regression_validation_status_counts": dict(
            sorted(regression_validation_status_counts.items())
        ),
        "repository_test_regression_validation_status_runs": {
            status: sorted(names)
            for status, names in sorted(regression_validation_status_runs.items())
        },
        "repository_test_patch_validation_reflection_candidate_count": sum(
            _int(
                summary.get(
                    "repository_test_patch_validation_reflection_candidate_count",
                    0,
                )
            )
            for summary in summaries
        ),
        "repository_test_patch_validation_successful_reflection_count": sum(
            _int(
                summary.get(
                    "repository_test_patch_validation_successful_reflection_count",
                    0,
                )
            )
            for summary in summaries
        ),
        "repository_test_patch_validation_max_depth": max(
            [
                _int(summary.get("repository_test_patch_validation_max_depth", 0))
                for summary in summaries
            ]
            or [0]
        ),
        "repository_test_patch_validation_reason_counts": dict(
            sorted(patch_validation_reason_counts.items())
        ),
        "repository_test_patch_validation_reason_runs": {
            reason: sorted(names)
            for reason, names in sorted(patch_validation_reason_runs.items())
        },
        "repository_test_patch_validation_failure_type_counts": dict(
            sorted(patch_validation_failure_type_counts.items())
        ),
        "average_top1": _average_run_metric(benchmark_runs, "top1"),
        "average_map": _average_run_metric(benchmark_runs, "map"),
        "average_patch_success_rate": _average_run_metric(
            benchmark_runs,
            "patch_success_rate",
        ),
        "average_quality_score": _average_run_metric(quality_runs, "quality_score"),
        "weighted_top1": _weighted_run_metric(benchmark_runs, "top1"),
        "weighted_map": _weighted_run_metric(benchmark_runs, "map"),
        "weighted_patch_success_rate": _weighted_run_metric(
            benchmark_runs,
            "patch_success_rate",
        ),
        "diagnostic_issue_code_counts": dict(sorted(code_counts.items())),
        "diagnostic_issue_code_runs": {
            code: sorted(names) for code, names in sorted(code_runs.items())
        },
    }
    suite_checks = _evaluate_suite_thresholds(summary, suite_thresholds)
    summary["suite_threshold_checks"] = suite_checks
    summary["suite_threshold_check_count"] = len(suite_checks)
    summary["suite_threshold_failed_count"] = sum(
        1 for check in suite_checks if not check.get("passed")
    )
    summary["next_actions"] = _suite_next_actions(summary)
    return summary


def _suite_next_actions(summary: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    code_runs = _dict(summary.get("diagnostic_issue_code_runs"))
    if _list(summary.get("command_error_runs")):
        actions.append(
            "Fix command_error runs first: check manifest repo values, GitHub API access, proxy, and token configuration."
        )
    if _list(summary.get("expectation_failed_runs")):
        actions.append(
            "Review expectation_failed_runs: either adjust expected_status/expected_diagnostic_codes or inspect the per-run agent report for a real regression."
        )
    if _list(summary.get("metric_check_failed_runs")):
        actions.append(
            "Review metric_check_failed_runs: compare per-run metric_checks with suite thresholds and inspect the run-level github_repo_agent.json."
        )
    if _int(summary.get("suite_threshold_failed_count", 0)) > 0:
        actions.append(
            "Review failed suite_threshold_checks: aggregate suite output did not meet the manifest-level quality gate."
        )
    if "github_api_error" in code_runs:
        actions.append(
            "For github_api_error runs, set GITHUB_TOKEN or verify proxy/network access before rerunning the suite."
        )
    if "no_imported_sources" in code_runs:
        actions.append(
            "For no_imported_sources runs, confirm the target repo contains Python files or relax include/exclude filters."
        )
    if "no_generated_candidates" in code_runs:
        actions.append(
            "For no_generated_candidates runs, inspect recipe_suggestion_preview, broaden recipes, or increase max_sources."
        )
    if "quality_gate_failed" in code_runs:
        actions.append(
            "For quality_gate_failed runs, inspect the per-run onboarding_quality_gate.md and decide whether it is an expected exploratory warning."
        )
    if not actions:
        actions.append(
            "Keep this suite as a repeatable repo-agent regression gate and add more repositories to measure generalization."
        )
    return actions


def _write_suite_outputs(report: GitHubRepoAgentSuiteReport, output_dir: Path) -> None:
    (output_dir / "github_repo_agent_suite.json").write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "github_repo_agent_suite.md").write_text(
        render_github_repo_agent_suite_markdown(report),
        encoding="utf-8",
    )


def _command_args(repo: str, output_dir: Path, options: dict[str, Any]) -> list[str]:
    args = [repo, str(output_dir)]
    for key, flag in (
        ("ref", "--ref"),
        ("preset", "--preset"),
        ("target_prefix", "--target-prefix"),
        ("source_cache_dir", "--source-cache-dir"),
        ("max_sources", "--max-sources"),
        ("max_candidates", "--max-candidates"),
        ("dependency_max_depth", "--dependency-max-depth"),
        ("token_env", "--token-env"),
        ("api_base_url", "--api-base-url"),
        ("timeout", "--timeout"),
    ):
        value = options.get(key)
        if value is not None:
            args.extend([flag, str(value)])
    if _bool_option(options, "recursive", True) is False:
        args.append("--no-recursive")
    if _bool_option(options, "auto_dependency_sources", True) is False:
        args.append("--no-auto-dependency-sources")
    if _optional_bool(options.get("run_smoke_validation")) is False:
        args.append("--no-smoke-validation")
    for item in _list_str(options.get("include")):
        args.extend(["--include", item])
    for item in _list_str(options.get("exclude")):
        args.extend(["--exclude", item])
    for item in _list_str(options.get("recipes", options.get("recipe"))):
        args.extend(["--recipe", item])
    if _bool_option(options, "auto_fallback", True) is False:
        args.append("--no-auto-fallback")
    if options.get("fallback_min_generated_candidates") is not None:
        args.extend(
            [
                "--fallback-min-generated-candidates",
                str(options["fallback_min_generated_candidates"]),
            ]
        )
    if options.get("fallback_max_sources") is not None:
        args.extend(["--fallback-max-sources", str(options["fallback_max_sources"])])
    if options.get("fallback_max_candidates") is not None:
        args.extend(
            ["--fallback-max-candidates", str(options["fallback_max_candidates"])]
        )
    if options.get("fallback_preset") is not None:
        args.extend(["--fallback-preset", str(options["fallback_preset"])])
    for item in _list_str(
        options.get("fallback_recipes", options.get("fallback_recipe"))
    ):
        args.extend(["--fallback-recipe", item])
    if _bool_option(options, "auto_remediate_benchmark", False):
        args.append("--auto-remediate-benchmark")
    if _bool_option(options, "checkout_repository_tests", False):
        args.append("--checkout-repository-tests")
        if options.get("repository_checkout_timeout") is not None:
            args.extend(
                ["--repository-checkout-timeout", str(options["repository_checkout_timeout"])]
            )
        if options.get("repository_checkout_depth") is not None:
            args.extend(
                ["--repository-checkout-depth", str(options["repository_checkout_depth"])]
            )
    if options.get("repository_test_root") is not None:
        args.extend(["--repository-test-root", str(options["repository_test_root"])])
    if options.get("repository_test_timeout") is not None:
        args.extend(
            ["--repository-test-timeout", str(options["repository_test_timeout"])]
        )
    if options.get("repository_test_failure_overlay_candidate_limit") is not None:
        args.extend(
            [
                "--repository-test-failure-overlay-candidate-limit",
                str(options["repository_test_failure_overlay_candidate_limit"]),
            ]
        )
    if options.get("repository_test_reflection_mode") is not None:
        args.extend(
            [
                "--repository-test-reflection-mode",
                str(options["repository_test_reflection_mode"]),
            ]
        )
    if options.get("repository_test_reflection_rounds") is not None:
        args.extend(
            [
                "--repository-test-reflection-rounds",
                str(options["repository_test_reflection_rounds"]),
            ]
        )
    if options.get("repository_test_reflection_width") is not None:
        args.extend(
            [
                "--repository-test-reflection-width",
                str(options["repository_test_reflection_width"]),
            ]
        )
    if _bool_option(options, "run_repository_test_environment_setup", False):
        args.append("--run-repository-test-environment-setup")
        if options.get("repository_test_environment_setup_timeout") is not None:
            args.extend(
                [
                    "--repository-test-environment-setup-timeout",
                    str(options["repository_test_environment_setup_timeout"]),
                ]
            )
    if options.get("repository_test_environment_setup_mode") is not None:
        args.extend(
            [
                "--repository-test-environment-setup-mode",
                str(options["repository_test_environment_setup_mode"]),
            ]
        )
    if _bool_option(options, "run_repository_test_retry", False):
        args.append("--run-repository-test-retry")
    if _bool_option(options, "run_repository_test_retry_prerequisites", False):
        args.append("--run-repository-test-retry-prerequisites")
    return args


def _metric_thresholds(options: dict[str, Any]) -> dict[str, float]:
    thresholds = dict(_dict(options.get("thresholds")))
    for key, metric in (
        ("min_generated_candidates", "generated_candidates"),
        ("min_benchmark_cases", "benchmark_cases"),
        ("min_top1", "top1"),
        ("min_map", "map"),
        ("min_patch_success_rate", "patch_success_rate"),
        ("min_quality_score", "quality_score"),
    ):
        if key in options and metric not in thresholds:
            thresholds[metric] = options[key]
    return {
        str(metric): _float(expected)
        for metric, expected in thresholds.items()
        if expected is not None
    }


def _suite_thresholds(payload: dict[str, Any]) -> dict[str, float]:
    raw = _dict(payload.get("suite_thresholds"))
    return {
        str(metric): _float(expected)
        for metric, expected in raw.items()
        if expected is not None
    }


def _evaluate_suite_thresholds(
    summary: dict[str, Any],
    thresholds: dict[str, float],
) -> list[dict[str, Any]]:
    checks = []
    for key, expected in sorted(thresholds.items()):
        metric, comparator = _suite_threshold_metric_and_comparator(key)
        actual = _float(summary.get(metric, 0.0))
        if comparator == "<=":
            passed = actual <= expected + 1e-12
            expected_text = f"<= {expected:.4f}"
        else:
            passed = actual + 1e-12 >= expected
            expected_text = f">= {expected:.4f}"
        checks.append(
            {
                "name": key,
                "metric": metric,
                "expected": expected_text,
                "actual": f"{actual:.4f}",
                "passed": passed,
            }
        )
    return checks


def _suite_threshold_metric_and_comparator(key: str) -> tuple[str, str]:
    if key.startswith("max_"):
        return key.removeprefix("max_"), "<="
    if key.startswith("min_"):
        return key.removeprefix("min_"), ">="
    return key, ">="


def _evaluate_metric_thresholds(
    summary: dict[str, Any],
    thresholds: dict[str, float],
) -> list[dict[str, Any]]:
    checks = []
    for metric, expected in sorted(thresholds.items()):
        actual = _float(summary.get(metric, 0.0))
        checks.append(
            {
                "metric": metric,
                "expected": f">= {expected:.4f}",
                "actual": f"{actual:.4f}",
                "passed": actual + 1e-12 >= expected,
            }
        )
    return checks


def _run_name(options: dict[str, Any], index: int) -> str:
    name = str(options.get("name") or "").strip()
    if name:
        return _safe_name(name)
    repo = str(options.get("repo") or options.get("repo_spec") or f"run_{index + 1}")
    return _safe_name(repo.replace("/", "_"))


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in value)


def _suite_metric_snapshot(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_candidates": _int(summary.get("generated_candidates", 0)),
        "benchmark_cases": _int(summary.get("benchmark_cases", 0)),
        "top1": _float(summary.get("top1", 0.0)),
        "map": _float(summary.get("map", 0.0)),
        "patch_success_rate": _float(summary.get("patch_success_rate", 0.0)),
        "quality_score": _float(summary.get("quality_score", 0.0)),
        "static_intelligence_status": str(
            summary.get("static_intelligence_status") or ""
        ),
        "static_intelligence_level": str(
            summary.get("static_intelligence_level") or ""
        ),
        "static_intelligence_reason": str(
            summary.get("static_intelligence_reason") or ""
        ),
        "static_intelligence_imported_source_count": _int(
            summary.get("static_intelligence_imported_source_count", 0)
        ),
        "static_intelligence_selected_source_count": _int(
            summary.get("static_intelligence_selected_source_count", 0)
        ),
        "static_intelligence_selected_signal_count": _int(
            summary.get("static_intelligence_selected_signal_count", 0)
        ),
        "static_intelligence_total_signal_count": _int(
            summary.get("static_intelligence_total_signal_count", 0)
        ),
        "static_intelligence_candidate_limit_applied": bool(
            summary.get("static_intelligence_candidate_limit_applied", False)
        ),
        "static_intelligence_rule_counts": dict(
            _dict(summary.get("static_intelligence_rule_counts"))
        ),
        "static_intelligence_bug_type_counts": dict(
            _dict(summary.get("static_intelligence_bug_type_counts"))
        ),
        "static_intelligence_source_hit_rate": _float(
            summary.get("static_intelligence_source_hit_rate", 0.0)
        ),
        "static_intelligence_candidate_density": _float(
            summary.get("static_intelligence_candidate_density", 0.0)
        ),
        "static_intelligence_rule_diversity": _int(
            summary.get("static_intelligence_rule_diversity", 0)
        ),
        "static_intelligence_bug_type_diversity": _int(
            summary.get("static_intelligence_bug_type_diversity", 0)
        ),
        "static_intelligence_quality_score": _float(
            summary.get("static_intelligence_quality_score", 0.0)
        ),
        "static_intelligence_dynamic_validation_level": str(
            summary.get("static_intelligence_dynamic_validation_level") or ""
        ),
        "static_intelligence_primary_artifact": str(
            summary.get("static_intelligence_primary_artifact") or ""
        ),
        "static_intelligence_next_action": str(
            summary.get("static_intelligence_next_action") or ""
        ),
        "fallback_attempted": bool(summary.get("fallback_attempted", False)),
        "fallback_used": bool(summary.get("fallback_used", False)),
        "fallback_improved": bool(summary.get("fallback_improved", False)),
        "fallback_recovered": bool(summary.get("fallback_recovered", False)),
        "fallback_reason": str(summary.get("fallback_reason") or ""),
        "fallback_min_generated_candidates": _int(
            summary.get("fallback_min_generated_candidates", 0)
        ),
        "primary_generated_candidates": _int(
            summary.get("primary_generated_candidates", 0)
        ),
        "fallback_generated_candidates": _int(
            summary.get("fallback_generated_candidates", 0)
        ),
        "primary_benchmark_cases": _int(summary.get("primary_benchmark_cases", 0)),
        "fallback_benchmark_cases": _int(
            summary.get("fallback_benchmark_cases", 0)
        ),
        "primary_output_dir": str(summary.get("primary_output_dir") or ""),
        "fallback_output_dir": str(summary.get("fallback_output_dir") or ""),
        "auto_remediation_attempted": bool(
            summary.get("auto_remediation_attempted", False)
        ),
        "auto_remediation_used": bool(
            summary.get("auto_remediation_used", False)
        ),
        "auto_remediation_improved": bool(
            summary.get("auto_remediation_improved", False)
        ),
        "auto_remediation_action_id": str(
            summary.get("auto_remediation_action_id") or ""
        ),
        "auto_remediation_command": str(
            summary.get("auto_remediation_command") or ""
        ),
        "primary_benchmarkization_status": str(
            summary.get("primary_benchmarkization_status") or ""
        ),
        "fallback_benchmarkization_status": str(
            summary.get("fallback_benchmarkization_status") or ""
        ),
        "remediated_benchmarkization_status": str(
            summary.get("remediated_benchmarkization_status") or ""
        ),
        "remediated_benchmark_cases": _int(
            summary.get("remediated_benchmark_cases", 0)
        ),
        "pre_remediation_output_dir": str(
            summary.get("pre_remediation_output_dir") or ""
        ),
        "remediated_output_dir": str(summary.get("remediated_output_dir") or ""),
        "benchmarkization_status": str(summary.get("benchmarkization_status") or ""),
        "benchmarkization_stage": str(summary.get("benchmarkization_stage") or ""),
        "benchmarkization_ready": bool(
            summary.get("benchmarkization_ready", False)
        ),
        "benchmarkization_primary_action_id": str(
            summary.get("benchmarkization_primary_action_id") or ""
        ),
        "benchmarkization_primary_action_auto_runnable": bool(
            summary.get("benchmarkization_primary_action_auto_runnable", False)
        ),
        "benchmarkization_remediation_plan_json": str(
            summary.get("benchmarkization_remediation_plan_json") or ""
        ),
        "benchmarkization_remediation_plan_markdown": str(
            summary.get("benchmarkization_remediation_plan_markdown") or ""
        ),
        "primary_benchmarkization_primary_action_id": str(
            summary.get("primary_benchmarkization_primary_action_id") or ""
        ),
        "fallback_benchmarkization_primary_action_id": str(
            summary.get("fallback_benchmarkization_primary_action_id") or ""
        ),
        "primary_benchmarkization_remediation_plan_markdown": str(
            summary.get("primary_benchmarkization_remediation_plan_markdown") or ""
        ),
        "fallback_benchmarkization_remediation_plan_markdown": str(
            summary.get("fallback_benchmarkization_remediation_plan_markdown") or ""
        ),
        "repository_test_environment_setup_status": str(
            summary.get("repository_test_environment_setup_status") or ""
        ),
        "repository_test_environment_setup_supported": bool(
            summary.get("repository_test_environment_setup_supported", False)
        ),
        "repository_test_environment_setup_result_status": str(
            summary.get("repository_test_environment_setup_result_status") or ""
        ),
        "repository_test_environment_setup_result_executed": bool(
            summary.get("repository_test_environment_setup_result_executed", False)
        ),
        "repository_test_environment_setup_install_failure_category": str(
            summary.get(
                "repository_test_environment_setup_install_failure_category"
            )
            or ""
        ),
        "repository_test_environment_setup_install_failure_signal": str(
            summary.get("repository_test_environment_setup_install_failure_signal")
            or ""
        ),
        "repository_test_environment_setup_install_fallback_executed": bool(
            summary.get(
                "repository_test_environment_setup_install_fallback_executed",
                False,
            )
        ),
        "repository_test_environment_setup_install_fallback_returncode": (
            summary.get(
                "repository_test_environment_setup_install_fallback_returncode"
            )
        ),
        "repository_config_snapshot_status": str(
            summary.get("repository_config_snapshot_status") or ""
        ),
        "repository_config_snapshot_reason": str(
            summary.get("repository_config_snapshot_reason") or ""
        ),
        "repository_config_snapshot_file_count": _int(
            summary.get("repository_config_snapshot_file_count", 0)
        ),
        "repository_test_pytest_config_source_count": _int(
            summary.get("repository_test_pytest_config_source_count", 0)
        ),
        "repository_test_pytest_addopts": [
            str(item) for item in _list(summary.get("repository_test_pytest_addopts"))
        ],
        "repository_test_pytest_testpaths": [
            str(item) for item in _list(summary.get("repository_test_pytest_testpaths"))
        ],
        "repository_test_ci_config_source_count": _int(
            summary.get("repository_test_ci_config_source_count", 0)
        ),
        "repository_test_ci_python_versions": [
            str(item)
            for item in _list(summary.get("repository_test_ci_python_versions"))
        ],
        "repository_test_ci_install_command_candidates": [
            str(item)
            for item in _list(
                summary.get("repository_test_ci_install_command_candidates")
            )
        ],
        "repository_test_ci_test_command_candidates": [
            str(item)
            for item in _list(summary.get("repository_test_ci_test_command_candidates"))
        ],
        "repository_test_tox_envlist": [
            str(item) for item in _list(summary.get("repository_test_tox_envlist"))
        ],
        "repository_test_execution_plan_status": str(
            summary.get("repository_test_execution_plan_status") or ""
        ),
        "planned_repository_test_level": str(
            summary.get("planned_repository_test_level") or ""
        ),
        "planned_repository_test_risk": str(
            summary.get("planned_repository_test_risk") or ""
        ),
        "planned_repository_test_runner": str(
            summary.get("planned_repository_test_runner") or ""
        ),
        "planned_repository_test_source": str(
            summary.get("planned_repository_test_source") or ""
        ),
        "planned_repository_test_ci_candidate_count": _int(
            summary.get("planned_repository_test_ci_candidate_count", 0)
        ),
        "planned_repository_test_ci_command_candidates": [
            str(item)
            for item in _list(
                summary.get("planned_repository_test_ci_command_candidates")
            )
        ],
        "framework_signals": [
            str(item) for item in _list(summary.get("framework_signals"))
        ],
        "repository_test_framework_configuration_status": str(
            summary.get("repository_test_framework_configuration_status") or ""
        ),
        "repository_test_framework_configuration_reason": str(
            summary.get("repository_test_framework_configuration_reason") or ""
        ),
        "repository_test_frameworks": [
            str(item) for item in _list(summary.get("repository_test_frameworks"))
        ],
        "repository_test_app_bootstrap_candidate_count": _int(
            summary.get("repository_test_app_bootstrap_candidate_count", 0)
        ),
        "repository_test_bootstrap_signal_count": _int(
            summary.get("repository_test_bootstrap_signal_count", 0)
        ),
        "planned_repository_test_executable_now": bool(
            summary.get("planned_repository_test_executable_now", False)
        ),
        "planned_repository_test_environment_variable_names": [
            str(item)
            for item in _list(
                summary.get("planned_repository_test_environment_variable_names")
            )
        ],
        "planned_repository_test_result_status": str(
            summary.get("planned_repository_test_result_status") or ""
        ),
        "planned_repository_test_result_executed": bool(
            summary.get("planned_repository_test_result_executed", False)
        ),
        "planned_repository_test_python_source": str(
            summary.get("planned_repository_test_python_source") or ""
        ),
        "planned_repository_test_failure_category": str(
            summary.get("planned_repository_test_failure_category") or ""
        ),
        "repository_test_retry_recommended": bool(
            summary.get("repository_test_retry_recommended", False)
        ),
        "repository_test_retry_strategy": str(
            summary.get("repository_test_retry_strategy") or ""
        ),
        "repository_test_retry_execution_status": str(
            summary.get("repository_test_retry_execution_status") or ""
        ),
        "repository_test_retry_executed": bool(
            summary.get("repository_test_retry_executed", False)
        ),
        "repository_test_dynamic_evidence_level": str(
            summary.get("repository_test_dynamic_evidence_level") or ""
        ),
        "repository_test_dynamic_failing_tests": _int(
            summary.get("repository_test_dynamic_failing_tests", 0)
        ),
        "repository_test_dynamic_usable_for_localization": bool(
            summary.get("repository_test_dynamic_usable_for_localization", False)
        ),
        "repository_test_dynamic_usable_for_regression_validation": bool(
            summary.get(
                "repository_test_dynamic_usable_for_regression_validation",
                False,
            )
        ),
        "repository_test_dynamic_usable_for_patch_validation": bool(
            summary.get(
                "repository_test_dynamic_usable_for_patch_validation",
                False,
            )
        ),
        "repository_test_analysis_source": str(
            summary.get("repository_test_analysis_source") or ""
        ),
        "repository_test_overlay_trigger_reason": str(
            summary.get("repository_test_overlay_trigger_reason") or ""
        ),
        "repository_test_phase2_ready": bool(
            summary.get("repository_test_phase2_ready", False)
        ),
        "repository_test_phase3_validation_ready": bool(
            summary.get("repository_test_phase3_validation_ready", False)
        ),
        "repository_test_final_status": str(
            summary.get("repository_test_final_status") or ""
        ),
        "repository_test_final_reason": str(
            summary.get("repository_test_final_reason") or ""
        ),
        "repository_test_failure_overlay_status": str(
            summary.get("repository_test_failure_overlay_status") or ""
        ),
        "repository_test_failure_overlay_reason": str(
            summary.get("repository_test_failure_overlay_reason") or ""
        ),
        "repository_test_failure_overlay_attempted_cases": _int(
            summary.get("repository_test_failure_overlay_attempted_cases", 0)
        ),
        "repository_test_failure_overlay_supported_candidates": _int(
            summary.get("repository_test_failure_overlay_supported_candidates", 0)
        ),
        "repository_test_failure_overlay_candidate_limit": _int(
            summary.get("repository_test_failure_overlay_candidate_limit", 0)
        ),
        "repository_test_failure_overlay_strategy_policy": str(
            summary.get("repository_test_failure_overlay_strategy_policy") or ""
        ),
        "repository_test_failure_overlay_candidate_rule_counts": dict(
            _dict(summary.get("repository_test_failure_overlay_candidate_rule_counts"))
        ),
        "repository_test_failure_overlay_attempted_rule_counts": dict(
            _dict(summary.get("repository_test_failure_overlay_attempted_rule_counts"))
        ),
        "repository_test_failure_overlay_triggered_rule_counts": dict(
            _dict(summary.get("repository_test_failure_overlay_triggered_rule_counts"))
        ),
        "repository_test_failure_overlay_candidate_rejection_count": _int(
            summary.get("repository_test_failure_overlay_candidate_rejection_count", 0)
        ),
        "repository_test_failure_overlay_candidate_rejection_counts": dict(
            _dict(
                summary.get("repository_test_failure_overlay_candidate_rejection_counts")
            )
        ),
        "repository_test_failure_overlay_candidate_rejection_rule_counts": dict(
            _dict(
                summary.get(
                    "repository_test_failure_overlay_candidate_rejection_rule_counts"
                )
            )
        ),
        "repository_test_failure_overlay_candidate_rejection_examples": _list(
            summary.get("repository_test_failure_overlay_candidate_rejection_examples")
        ),
        "repository_test_failure_overlay_dominant_rejection_reason": str(
            summary.get("repository_test_failure_overlay_dominant_rejection_reason")
            or ""
        ),
        "repository_test_failure_overlay_dominant_rejection_count": _int(
            summary.get("repository_test_failure_overlay_dominant_rejection_count", 0)
        ),
        "repository_test_failure_overlay_rejection_recommendations": _list(
            summary.get("repository_test_failure_overlay_rejection_recommendations")
        ),
        "repository_test_failure_overlay_next_extension": dict(
            _dict(summary.get("repository_test_failure_overlay_next_extension"))
        ),
        "repository_test_failure_overlay_next_actionable_extension": dict(
            _dict(
                summary.get(
                    "repository_test_failure_overlay_next_actionable_extension"
                )
            )
        ),
        "repository_test_failure_overlay_selected_candidate_rank": _int(
            summary.get("repository_test_failure_overlay_selected_candidate_rank", 0)
        ),
        "repository_test_failure_overlay_selected_rule": str(
            summary.get("repository_test_failure_overlay_selected_rule") or ""
        ),
        "repository_test_failure_overlay_selected_function": str(
            summary.get("repository_test_failure_overlay_selected_function") or ""
        ),
        "repository_test_failure_overlay_selected_score": _float(
            summary.get("repository_test_failure_overlay_selected_score", 0.0)
        ),
        "repository_test_failure_overlay_average_candidate_score": _float(
            summary.get(
                "repository_test_failure_overlay_average_candidate_score",
                0.0,
            )
        ),
        "repository_test_failure_overlay_selected_score_breakdown": dict(
            _dict(summary.get("repository_test_failure_overlay_selected_score_breakdown"))
        ),
        "repository_test_failure_overlay_candidate_score_preview": _list(
            summary.get("repository_test_failure_overlay_candidate_score_preview")
        ),
        "repository_test_failure_overlay_dynamic_evidence_level": str(
            summary.get("repository_test_failure_overlay_dynamic_evidence_level") or ""
        ),
        "repository_test_fault_localization_status": str(
            summary.get("repository_test_fault_localization_status") or ""
        ),
        "repository_test_fault_localization_reason": str(
            summary.get("repository_test_fault_localization_reason") or ""
        ),
        "repository_test_fault_localization_ranking_count": _int(
            summary.get("repository_test_fault_localization_ranking_count", 0)
        ),
        "repository_test_fault_localization_top_score": _float(
            summary.get("repository_test_fault_localization_top_score", 0.0)
        ),
        "repository_test_patch_candidates_status": str(
            summary.get("repository_test_patch_candidates_status") or ""
        ),
        "repository_test_patch_candidates_reason": str(
            summary.get("repository_test_patch_candidates_reason") or ""
        ),
        "repository_test_patch_candidate_count": _int(
            summary.get("repository_test_patch_candidate_count", 0)
        ),
        "repository_test_patch_validation_status": str(
            summary.get("repository_test_patch_validation_status") or ""
        ),
        "repository_test_patch_validation_reason": str(
            summary.get("repository_test_patch_validation_reason") or ""
        ),
        "repository_test_patch_validation_executed_count": _int(
            summary.get("repository_test_patch_validation_executed_count", 0)
        ),
        "repository_test_patch_validation_success_count": _int(
            summary.get("repository_test_patch_validation_success_count", 0)
        ),
        "repository_test_repair_ready": bool(
            summary.get("repository_test_repair_ready", False)
        ),
        "repository_test_repair_validation_scope": str(
            summary.get("repository_test_repair_validation_scope") or ""
        ),
        "repository_test_regression_ready": bool(
            summary.get("repository_test_regression_ready", False)
        ),
        "repository_test_regression_validation_status": str(
            summary.get("repository_test_regression_validation_status") or ""
        ),
        "repository_test_regression_validation_reason": str(
            summary.get("repository_test_regression_validation_reason") or ""
        ),
        "repository_test_patch_validation_reflection_candidate_count": _int(
            summary.get(
                "repository_test_patch_validation_reflection_candidate_count",
                0,
            )
        ),
        "repository_test_patch_validation_successful_reflection_count": _int(
            summary.get(
                "repository_test_patch_validation_successful_reflection_count",
                0,
            )
        ),
        "repository_test_patch_validation_max_depth": _int(
            summary.get("repository_test_patch_validation_max_depth", 0)
        ),
        "repository_test_best_patch_candidate_success": bool(
            summary.get("repository_test_best_patch_candidate_success", False)
        ),
        "diagnostics_status": str(summary.get("diagnostics_status", "")),
        "first_failing_stage": str(summary.get("first_failing_stage", "")),
    }


def _average_run_metric(
    runs: list[GitHubRepoAgentSuiteRunResult],
    metric: str,
) -> float:
    if not runs:
        return 0.0
    return round(sum(_float(run.metrics.get(metric, 0.0)) for run in runs) / len(runs), 4)


def _weighted_run_metric(
    runs: list[GitHubRepoAgentSuiteRunResult],
    metric: str,
) -> float:
    total_cases = sum(_int(run.metrics.get("benchmark_cases", 0)) for run in runs)
    if total_cases <= 0:
        return 0.0
    weighted = sum(
        _float(run.metrics.get(metric, 0.0))
        * _int(run.metrics.get("benchmark_cases", 0))
        for run in runs
    )
    return round(weighted / total_cases, 4)


def _run_metric(run: GitHubRepoAgentSuiteRunResult, name: str) -> Any:
    if name in run.metrics:
        return run.metrics.get(name, 0)
    payload = _load_report(run.report_path)
    return _dict(payload.get("summary")).get(name, 0)


def _load_report(path: str) -> dict[str, Any]:
    if not path:
        return {}
    report_path = Path(path)
    if not report_path.exists():
        return {}
    return json.loads(report_path.read_text(encoding="utf-8"))


def _token_from_env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _list_str(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return _int(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return _bool(value)


def _bool_option(payload: dict[str, Any], key: str, default: bool) -> bool:
    if key not in payload:
        return default
    return _bool(payload[key])


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
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


def _ratio(numerator: int | float, denominator: int | float) -> float:
    denominator_value = _float(denominator)
    if denominator_value <= 0:
        return 0.0
    return round(_float(numerator) / denominator_value, 4)


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(
        f"{key}:{_int(value)}" for key, value in sorted(counts.items())
    )


def _fallback_cell(metrics: dict[str, Any]) -> str:
    if not bool(metrics.get("fallback_attempted", False)):
        return "none"
    reason = str(metrics.get("fallback_reason") or "unknown")
    if bool(metrics.get("fallback_recovered", False)):
        return f"recovered:{reason}"
    if bool(metrics.get("fallback_used", False)):
        return f"used:{reason}"
    if bool(metrics.get("fallback_improved", False)):
        return f"improved:{reason}"
    return f"attempted:{reason}"


def _auto_remediation_cell(metrics: dict[str, Any]) -> str:
    if not bool(metrics.get("auto_remediation_attempted", False)):
        return "none"
    action = str(metrics.get("auto_remediation_action_id") or "unknown")
    if bool(metrics.get("auto_remediation_used", False)):
        return f"used:{action}"
    if bool(metrics.get("auto_remediation_improved", False)):
        return f"improved:{action}"
    return f"attempted:{action}"


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":  # pragma: no cover
    main()
