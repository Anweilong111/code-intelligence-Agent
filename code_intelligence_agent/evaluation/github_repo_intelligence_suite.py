from __future__ import annotations

import argparse
import io
from contextlib import contextmanager, redirect_stdout
import json
import os
import shutil
import time
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from code_intelligence_agent.agents.llm_client import llm_config_audits_for_modes
from code_intelligence_agent.evaluation.github_discovery_fetcher import GitHubAPIError
from code_intelligence_agent.evaluation.github_repo_agent import GitHubRepoAgentReport
from code_intelligence_agent.evaluation.github_repo_intelligence import (
    DEFAULT_REPOSITORY_TEST_TIMEOUT,
    _default_output_dir_for_repo,
    github_repo_intelligence_summary,
    main as github_repo_intelligence_main,
    refresh_github_repo_intelligence_summary_status,
    run_github_repo_intelligence,
    write_github_repo_intelligence_artifacts,
)
from code_intelligence_agent.evaluation.llm_config_audit import (
    render_llm_config_audit_markdown,
)
from code_intelligence_agent.evaluation.github_onboarding_matrix import (
    backfill_github_onboarding_artifacts,
    build_github_onboarding_matrix,
    write_github_onboarding_matrix_artifacts,
)
from code_intelligence_agent.evaluation.llm_repair_showcase_matrix import (
    build_llm_repair_showcase_matrix,
    write_llm_repair_showcase_matrix_artifacts,
)
from code_intelligence_agent.evaluation.llm_repair_case_catalog import (
    build_llm_repair_case_catalog_audit,
    write_llm_repair_case_catalog_audit_artifacts,
)
from code_intelligence_agent.evaluation.p6_readiness_audit import (
    build_p6_readiness_audit,
    write_p6_readiness_audit_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_patch_candidates import (
    write_repository_test_patch_candidates_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_patch_validation import (
    build_repository_test_patch_validation,
    write_repository_test_patch_validation_artifacts,
)
from code_intelligence_agent.tools.diff_utils import render_unified_diff
from code_intelligence_agent.tools.patch_validation import validate_function_patch


EXPECTED_AGENT_CONTROLLER_LOOP = [
    "observe",
    "plan",
    "act",
    "verify",
    "reflect",
    "replan",
]


@dataclass(frozen=True)
class GitHubRepoIntelligenceSuiteRunResult:
    name: str
    repo: str
    output_dir: str
    report_path: str
    status: str
    passed: bool
    expected_status: str
    expectation_passed: bool
    metrics: dict[str, Any]
    metric_checks: list[dict[str, Any]]
    expectation_checks: list[dict[str, Any]]
    command_args: list[str]
    error: str | None = None
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GitHubRepoIntelligenceSuiteReport:
    manifest_path: str
    output_dir: str
    suite_name: str
    passed: bool
    summary: dict[str, Any]
    runs: list[GitHubRepoIntelligenceSuiteRunResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_path": self.manifest_path,
            "output_dir": self.output_dir,
            "suite_name": self.suite_name,
            "passed": self.passed,
            "summary": self.summary,
            "runs": [run.to_dict() for run in self.runs],
        }


def run_github_repo_intelligence_suite(
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    opener=None,
    reuse_existing_reports: bool = False,
    start_index: int = 0,
    limit_runs: int | None = None,
) -> GitHubRepoIntelligenceSuiteReport:
    manifest = Path(manifest_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    suite_name = str(payload.get("suite_name") or payload.get("name") or manifest.stem)
    defaults = _dict(payload.get("defaults"))
    manifest_runs = [_dict(run) for run in _list(payload.get("runs"))]
    selected_runs = _slice_runs(
        manifest_runs,
        start_index=start_index,
        limit_runs=limit_runs,
    )
    run_results: list[GitHubRepoIntelligenceSuiteRunResult] = []

    for index, entry in selected_runs:
        run_started_at = time.perf_counter()
        raw_options = {**defaults, **entry}
        options = raw_options
        name = _run_name(raw_options, index)
        repo = str(raw_options.get("repo") or raw_options.get("repo_spec") or "")
        expected_status = str(raw_options.get("expected_status") or "pass")
        run_output = Path(str(raw_options.get("output_dir") or output_root / name))
        command_args = _command_args(repo, run_output, raw_options)
        if not repo:
            _append_run_result(
                run_results,
                _failed_run_result(
                    name=name,
                    repo="",
                    output_dir=str(run_output),
                    expected_status=expected_status,
                    error="manifest run is missing repo",
                    command_args=command_args,
                ),
                run_started_at,
            )
            continue

        try:
            options = _apply_execution_profile_options(raw_options)
            if _bool_option(options, "use_cli_default_output_dir", False):
                run_output = output_root / _default_output_dir_for_repo(
                    repo,
                    execution_profile=str(options.get("execution_profile") or "static"),
                    agent_shortcut=_bool_option(options, "agent", False),
                )
            command_args = _command_args(repo, run_output, options)
            controlled_result = _controlled_repair_case_result(
                name=name,
                repo=repo,
                output_dir=run_output,
                expected_status=expected_status,
                options=options,
                metric_thresholds=_metric_thresholds(options),
                command_args=command_args,
            )
            if controlled_result is not None:
                _append_run_result(run_results, controlled_result, run_started_at)
                continue
            if _reuse_existing_report_requested(
                options,
                suite_reuse_existing_reports=reuse_existing_reports,
            ):
                reused_result = _reused_run_result_from_existing_report(
                    name=name,
                    repo=repo,
                    output_dir=run_output,
                    expected_status=expected_status,
                    options=options,
                    metric_thresholds=_metric_thresholds(options),
                    command_args=command_args,
                    reuse_reason="explicit_existing_report_reuse",
                )
                if reused_result is None:
                    _append_run_result(
                        run_results,
                        _failed_run_result(
                            name=name,
                            repo=repo,
                            output_dir=str(run_output),
                            expected_status=expected_status,
                            error=(
                                "existing report reuse requested but "
                                "github_repo_intelligence.json was not found "
                                "or did not match the requested repository"
                            ),
                            command_args=command_args,
                        ),
                        run_started_at,
                    )
                else:
                    _append_run_result(run_results, reused_result, run_started_at)
                continue
            _seed_discovery_cache(options, run_output)
            with _temporary_cleared_env(
                _llm_api_key_env_names()
                if _bool_option(options, "clear_llm_api_keys", False)
                else []
            ):
                preflight_result = _llm_configuration_preflight_result(
                    name=name,
                    repo=repo,
                    output_dir=run_output,
                    expected_status=expected_status,
                    options=options,
                    metric_thresholds=_metric_thresholds(options),
                    command_args=command_args,
                )
                if preflight_result is not None:
                    _append_run_result(run_results, preflight_result, run_started_at)
                    continue
                if _bool_option(options, "use_cli_default_output_dir", False):
                    cli_result = _run_cli_default_output_dir_result(
                        name=name,
                        repo=repo,
                        output_root=output_root,
                        run_output=run_output,
                        expected_status=expected_status,
                        options=options,
                        command_args=command_args,
                        opener=opener,
                    )
                    _append_run_result(run_results, cli_result, run_started_at)
                    continue
                report = run_github_repo_intelligence(
                    repo,
                    run_output,
                    ref=_optional_str(options.get("ref")),
                    token=_token_from_env(str(options.get("token_env") or "GITHUB_TOKEN")),
                    include=_list_str(options.get("include")),
                    exclude=_list_str(options.get("exclude")),
                    target_prefix=str(options.get("target_prefix") or ""),
                    recipes=_list_str(options.get("recipes", options.get("recipe"))),
                    source_cache_dir=options.get("source_cache_dir"),
                    max_sources=_int(options.get("max_sources", 50)),
                    max_candidates=_int(options.get("max_candidates", 20)),
                    preset=str(options.get("preset") or "mining"),
                    auto_fallback=_bool_option(options, "auto_fallback", True),
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
                    repository_test_patch_validation_limit=_int(
                        options.get("repository_test_patch_validation_limit", 5)
                    ),
                    repository_patch_generation_mode=str(
                        options.get("repository_patch_generation_mode") or "rule"
                    ),
                    repository_llm_patch_candidate_limit=(
                        _int(options.get("repository_llm_patch_candidate_limit"))
                        if options.get("repository_llm_patch_candidate_limit") is not None
                        else None
                    ),
                    repository_patch_candidate_variant_allowlist=_list_str(
                        options.get("repository_patch_candidate_variant_allowlist")
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
                    patch_judge_mode=str(options.get("patch_judge_mode") or "none"),
                    run_repository_test_command=_bool_option(
                        options,
                        "run_repository_test_command",
                        True,
                    ),
                    run_repository_test_environment_setup=_bool_option(
                        options,
                        "run_repository_test_environment_setup",
                        False,
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
                    auto_repository_test_retry=_bool_option(
                        options,
                        "auto_repository_test_retry",
                        False,
                    ),
                    auto_repository_test_retry_max_risk=str(
                        options.get("auto_repository_test_retry_max_risk") or "low"
                    ),
                    auto_repository_test_retry_allowed_runners=(
                        _list_str(options.get("auto_repository_test_retry_allowed_runners"))
                        or None
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
                    prefer_cached_discovery=_bool_option(
                        options,
                        "prefer_cached_discovery",
                        False,
                    ),
                    auto_controller_actions=_bool_option(
                        options,
                        "auto_controller_actions",
                        False,
                    ),
                    auto_controller_max_actions=_int(
                        options.get("auto_controller_max_actions", 2)
                    ),
                    agent_shortcut=_bool_option(options, "agent", False),
                    api_base_url=str(options.get("api_base_url") or "https://api.github.com"),
                    timeout=_int(options.get("timeout", 20)),
                    opener=opener,
                )
            _append_run_result(
                run_results,
                _run_result_from_report(
                    name=name,
                    repo=repo,
                    report=report,
                    expected_status=expected_status,
                    options=options,
                    metric_thresholds=_metric_thresholds(options),
                    command_args=command_args,
                ),
                run_started_at,
            )
        except (GitHubAPIError, ValueError, OSError) as exc:
            if isinstance(exc, GitHubAPIError) and _is_rate_limit_error(exc):
                cached_result = _cached_run_result_from_existing_report(
                    name=name,
                    repo=repo,
                    output_dir=run_output,
                    expected_status=expected_status,
                    options=options,
                    metric_thresholds=_metric_thresholds(options),
                    command_args=command_args,
                    error=exc,
                )
                if cached_result is not None:
                    _append_run_result(run_results, cached_result, run_started_at)
                    continue
            _append_run_result(
                run_results,
                _failed_run_result(
                    name=name,
                    repo=repo,
                    output_dir=str(run_output),
                    expected_status=expected_status,
                    error=str(exc),
                    command_args=command_args,
                ),
                run_started_at,
            )

    suite_thresholds = _suite_thresholds(payload)
    summary = _suite_summary(
        run_results,
        suite_thresholds=suite_thresholds,
        manifest_run_count=len(manifest_runs),
        slice_start_index=_normalized_start_index(start_index),
        slice_limit=limit_runs,
    )
    passed = _suite_passed(summary)
    report = GitHubRepoIntelligenceSuiteReport(
        manifest_path=str(manifest),
        output_dir=str(output_root),
        suite_name=suite_name,
        passed=passed,
        summary=summary,
        runs=run_results,
    )
    if _bool_option(
        payload,
        "run_github_onboarding_matrix",
        _bool_option(defaults, "run_github_onboarding_matrix", False),
    ):
        _attach_github_onboarding_matrix(
            report,
            output_root,
            required_case_count=_int(
                payload.get(
                    "github_onboarding_matrix_required_case_count",
                    defaults.get("github_onboarding_matrix_required_case_count", 10),
                ),
                10,
            ),
        )
    if _bool_option(
        payload,
        "run_llm_repair_showcase_matrix",
        _bool_option(defaults, "run_llm_repair_showcase_matrix", False),
    ):
        _attach_llm_repair_showcase_matrix(
            report,
            output_root,
            source_report_paths=_llm_repair_source_report_paths(
                payload,
                defaults=defaults,
                manifest=manifest,
            ),
        )
    if _bool_option(
        payload,
        "run_llm_repair_case_catalog_audit",
        _bool_option(defaults, "run_llm_repair_case_catalog_audit", False),
    ):
        _attach_llm_repair_case_catalog_audit(
            report,
            output_root,
            catalog_path=_llm_repair_case_catalog_path(
                payload,
                defaults=defaults,
                manifest=manifest,
            ),
        )
    if _bool_option(
        payload,
        "run_p6_readiness_audit",
        _bool_option(defaults, "run_p6_readiness_audit", False),
    ):
        _attach_p6_readiness_audit(report, output_root)
    _refresh_suite_threshold_checks(summary, suite_thresholds)
    passed = _suite_passed(summary)
    report = GitHubRepoIntelligenceSuiteReport(
        manifest_path=str(manifest),
        output_dir=str(output_root),
        suite_name=suite_name,
        passed=passed,
        summary=summary,
        runs=run_results,
    )
    _write_suite_outputs(report, output_root)
    return report


def render_github_repo_intelligence_suite_markdown(
    report: GitHubRepoIntelligenceSuiteReport,
) -> str:
    summary = report.summary
    lines = [
        "# GitHub Repo Intelligence Suite",
        "",
        f"- Suite: `{report.suite_name}`",
        f"- Manifest: `{report.manifest_path}`",
        f"- Output Dir: `{report.output_dir}`",
        f"- Passed: {str(report.passed).lower()}",
        f"- Runs: {_int(summary.get('run_count', 0))}",
        f"- Manifest Runs: {_int(summary.get('manifest_run_count', summary.get('run_count', 0)))}",
        f"- Suite Slice Applied: {str(bool(summary.get('suite_slice_applied'))).lower()}",
        f"- Suite Slice: start={_int(summary.get('suite_slice_start_index', 0))}, limit={_markdown_cell(summary.get('suite_slice_limit'))}, runs={_int(summary.get('suite_slice_run_count', summary.get('run_count', 0)))}",
        f"- Agent Passed Runs: {_int(summary.get('agent_passed_count', 0))}",
        f"- Agent Failed Runs: {_int(summary.get('agent_failed_count', 0))}",
        f"- Expectation Passed Runs: {_int(summary.get('expectation_passed_count', 0))}",
        f"- Expectation Failed Runs: {_int(summary.get('expectation_failed_count', 0))}",
        f"- Command Failed Runs: {_int(summary.get('command_failed_count', 0))}",
        f"- Suite Run Elapsed Total ms: {_int(summary.get('suite_run_elapsed_ms_total', 0))}",
        f"- Suite Run Elapsed Average ms: {_float(summary.get('suite_run_elapsed_ms_average', 0.0))}",
        f"- Suite Run Elapsed Max ms: {_int(summary.get('suite_run_elapsed_ms_max', 0))}",
        f"- Existing Report Reuse Runs: {_int(summary.get('existing_report_reuse_count', 0))}",
        f"- Cached Report Fallback Runs: {_int(summary.get('cached_report_fallback_count', 0))}",
        f"- Discovery Cache Reuse Runs: {_int(summary.get('discovery_cache_reuse_count', 0))}",
        f"- Discovery Cache Fallback Runs: {_int(summary.get('discovery_cache_fallback_count', 0))}",
        f"- API Rate Limit Checkout Fallback Runs: {_int(summary.get('api_rate_limit_checkout_fallback_count', 0))}",
        f"- Metric Checks: {_int(summary.get('metric_check_count', 0))}",
        f"- Failed Metric Checks: {_int(summary.get('metric_check_failed_count', 0))}",
        f"- Suite Threshold Checks: {_int(summary.get('suite_threshold_check_count', 0))}",
        f"- Failed Suite Threshold Checks: {_int(summary.get('suite_threshold_failed_count', 0))}",
        f"- Artifact Core Ready Runs: {_int(summary.get('artifact_core_ready_count', 0))}",
        f"- Artifact Required Ready Runs: {_int(summary.get('artifact_required_ready_count', 0))}",
        f"- Artifact File Checked Runs: {_int(summary.get('artifact_file_checked_count', 0))}",
        f"- Acceptance Gate Pass Runs: {_int(summary.get('acceptance_gate_pass_count', 0))}",
        f"- Acceptance Repair Decision Audit Runs: {_int(summary.get('acceptance_gate_repair_decision_audit_pass_count', 0))}",
        f"- Agent Goal Readiness Pass Runs: {_int(summary.get('agent_goal_readiness_pass_count', 0))}",
        f"- Agent Goal Repair Decision Audit Runs: {_int(summary.get('agent_goal_repair_decision_audit_pass_count', 0))}",
        f"- Objective Compliance Pass Runs: {_int(summary.get('objective_compliance_pass_count', 0))}",
        f"- Agent Controller Loop Complete Runs: {_int(summary.get('agent_controller_loop_complete_count', 0))}",
        f"- Agent Decision Timeline Ready Runs: {_int(summary.get('agent_decision_timeline_ready_count', 0))}",
        f"- Agent Decision Timeline Complete Runs: {_int(summary.get('agent_decision_timeline_complete_count', 0))}",
        f"- Agent Decision Timeline Complete Steps: {_int(summary.get('agent_decision_timeline_complete_step_count', 0))}/{_int(summary.get('agent_decision_timeline_step_count', 0))}",
        f"- Agent Answer Complete Runs: {_int(summary.get('agent_answer_coverage_complete_count', 0))}",
        f"- Blocked Agent Answer Complete Runs: {_int(summary.get('blocked_agent_answer_complete_count', 0))}",
        f"- Blocked Agent Answer Incomplete Runs: {_int(summary.get('blocked_agent_answer_incomplete_count', 0))}",
        f"- Agent Answered Questions: {_format_counts(_dict(summary.get('agent_answer_question_answered_counts')))}",
        f"- GitHub Onboarding Matrix Status: `{_markdown_cell(summary.get('github_onboarding_matrix_status') or 'not_run')}`",
        f"- GitHub Onboarding Matrix Cases: {_int(summary.get('github_onboarding_matrix_case_count', 0))}",
        f"- GitHub Onboarding Matrix JSON: `{_markdown_cell(summary.get('github_onboarding_matrix_json') or 'none')}`",
        f"- GitHub Onboarding Matrix Markdown: `{_markdown_cell(summary.get('github_onboarding_matrix_markdown') or 'none')}`",
        f"- LLM Repair Showcase Matrix Status: `{_markdown_cell(summary.get('llm_repair_showcase_matrix_status') or 'not_run')}`",
        f"- LLM Repair Showcase Matrix Classes: {_format_counts(_dict(summary.get('llm_repair_showcase_matrix_class_counts')))}",
        f"- LLM Repair Showcase Matrix JSON: `{_markdown_cell(summary.get('llm_repair_showcase_matrix_json') or 'none')}`",
        f"- LLM Repair Showcase Matrix Markdown: `{_markdown_cell(summary.get('llm_repair_showcase_matrix_markdown') or 'none')}`",
        f"- LLM Repair Source Reports: {_int(summary.get('llm_repair_source_report_count', 0))}",
        f"- Missing LLM Repair Source Reports: {_int(summary.get('llm_repair_source_report_missing_count', 0))}",
        f"- Missing LLM Repair Source Report Paths: `{_markdown_cell(_format_list(_list(summary.get('llm_repair_source_report_missing_paths'))))}`",
        f"- LLM Repair Evaluation Matrix Status: `{_markdown_cell(summary.get('llm_repair_evaluation_matrix_status') or 'not_run')}`",
        f"- LLM Repair Evaluation Matrix JSON: `{_markdown_cell(summary.get('llm_repair_evaluation_matrix_json') or 'none')}`",
        f"- LLM Repair Metrics Report JSON: `{_markdown_cell(summary.get('llm_repair_metrics_report_json') or 'none')}`",
        f"- LLM Repair Patch Success@1/@3/@5: `{_markdown_cell(summary.get('llm_repair_metrics_patch_success_at') or 'none')}`",
        f"- LLM Repair Case Catalog Audit Status: `{_markdown_cell(summary.get('llm_repair_case_catalog_audit_status') or 'not_run')}`",
        f"- LLM Repair Case Catalog Matched Cases: {_int(summary.get('llm_repair_case_catalog_matched_case_count', 0))}/{_int(summary.get('llm_repair_case_catalog_declared_case_count', 0))}",
        f"- LLM Repair Case Catalog Missing Sources: {_int(summary.get('llm_repair_case_catalog_missing_source_report_count', 0))}",
        f"- LLM Repair Case Catalog Audit JSON: `{_markdown_cell(summary.get('llm_repair_case_catalog_audit_json') or 'none')}`",
        f"- LLM Repair Case Catalog Missing Checks: `{_markdown_cell(_format_list(_list(summary.get('llm_repair_case_catalog_audit_missing'))))}`",
        f"- P6 Readiness Audit Status: `{_markdown_cell(summary.get('p6_readiness_audit_status') or 'not_run')}`",
        f"- P6 Readiness Audit JSON: `{_markdown_cell(summary.get('p6_readiness_audit_json') or 'none')}`",
        f"- P6 Readiness Missing Checks: `{_markdown_cell(_format_list(_list(summary.get('p6_readiness_audit_missing'))))}`",
        f"- Repo Input Kinds: {_int(summary.get('repo_input_kind_count', 0))}",
        f"- Scenario Tag Kinds: {_int(summary.get('scenario_tag_kind_count', 0))}",
        f"- Scenario Coverage Blocked Runs: {_int(summary.get('scenario_coverage_blocked_count', 0))}",
        f"- Repository Structure Modeled Runs: {_int(summary.get('repository_structure_modeled_count', 0))}",
        f"- Repo Graph Ready Runs: {_int(summary.get('repo_graph_ready_count', 0))}",
        f"- Program Graph Available Runs: {_int(summary.get('program_graph_available_count', 0))}",
        f"- Src Layout Detected Runs: {_int(summary.get('repository_structure_src_layout_detected_count', 0))}",
        f"- Exclude Filter Effective Runs: {_int(summary.get('exclude_filter_effective_count', 0))}",
        f"- Source-Only Static Blocker Runs: {_int(summary.get('source_only_static_blocker_count', 0))}",
        f"- Planned Repository Test Command Runs: {_int(summary.get('planned_repository_test_command_count', 0))}",
        f"- Repository Test Execution Result Runs: {_int(summary.get('repository_test_execution_result_count', 0))}",
        f"- Repository Test Counted Runs: {_int(summary.get('repository_test_counted_run_count', 0))}",
        (
            "- Repository Test Counts: "
            f"total={_int(summary.get('repository_test_count', 0))}, "
            f"passed={_int(summary.get('repository_test_passed_count', 0))}, "
            f"failed={_int(summary.get('repository_test_failed_count', 0))}, "
            f"errors={_int(summary.get('repository_test_error_count', 0))}, "
            f"skipped={_int(summary.get('repository_test_skipped_count', 0))}"
        ),
        f"- Planned Repository Test Failure Context Lines: {_int(summary.get('planned_repository_test_failure_context_line_count', 0))}",
        f"- Repository Test Framework Kinds: {_int(summary.get('repository_test_framework_kind_count', 0))}",
        f"- Repository Test Command Candidate Runner Kinds: {_int(summary.get('repository_test_command_candidate_runner_kind_count', 0))}",
        f"- Repository Test Environment Diagnosed Runs: {_int(summary.get('repository_test_environment_diagnosed_count', 0))}",
        f"- Repository Test Setup Doctor Diagnosed Runs: {_int(summary.get('repository_test_setup_doctor_diagnosed_count', 0))}",
        (
            "- Repository Test Setup Doctor Checks: "
            f"pass={_int(summary.get('repository_test_setup_doctor_passed_check_count', 0))}/"
            f"{_int(summary.get('repository_test_setup_doctor_check_count', 0))}, "
            f"warning={_int(summary.get('repository_test_setup_doctor_warning_check_count', 0))}, "
            f"blocked={_int(summary.get('repository_test_setup_doctor_blocked_check_count', 0))}"
        ),
        f"- Recommended Install Command Runs: {_int(summary.get('repository_test_recommended_install_command_count', 0))}",
        f"- Planned Repository Test Runner Kinds: {_int(summary.get('planned_repository_test_runner_kind_count', 0))}",
        f"- Planned Repository Test Runner Fallback Runs: {_int(summary.get('planned_repository_test_runner_fallback_count', 0))}",
        f"- Repository Test Dynamic Evidence Level Kinds: {_int(summary.get('repository_test_dynamic_evidence_level_kind_count', 0))}",
        f"- Repository Test Dynamic Traceback Frames: {_int(summary.get('repository_test_dynamic_traceback_frame_count', 0))}",
        (
            "- Fault Localization Dynamic Matches: "
            f"failed_tests={_int(summary.get('fault_localization_matched_failed_test_count', 0))}, "
            f"traceback_frames={_int(summary.get('fault_localization_matched_traceback_frame_count', 0))}, "
            f"unmatched_traceback_frames={_int(summary.get('fault_localization_unmatched_traceback_frame_count', 0))}"
        ),
        f"- Fault Localization Application Candidate Runs: {_int(summary.get('fault_localization_application_candidate_run_count', 0))}",
        f"- Fault Localization No Application Candidate Runs: {_int(summary.get('fault_localization_no_application_candidate_run_count', 0))}",
        f"- Fault Localization Non-Application Top Runs: {_int(summary.get('fault_localization_non_application_top_ranked_count', 0))}",
        f"- Fault Localization Non-Application-Only Top-k Runs: {_int(summary.get('fault_localization_non_application_topk_only_count', 0))}",
        f"- Agent Auto Loop Progressed Actions: {_int(summary.get('agent_auto_loop_progress_count', 0))}",
        f"- Agent Auto Complete Loop Runs: {_int(summary.get('agent_auto_complete_loop_count', 0))}",
        f"- Agent Auto Action Loop Complete Runs: {_int(summary.get('agent_auto_action_loop_complete_run_count', 0))}",
        f"- Agent Auto Action Loop Complete Actions: {_int(summary.get('agent_auto_action_loop_complete_count', 0))}/{_int(summary.get('agent_auto_action_loop_required_count', 0))}",
        f"- Agent Auto Patch Validation Reached Actions: {_int(summary.get('agent_auto_patch_validation_reached_action_count', 0))}",
        f"- Agent Auto Repair Ready Actions: {_int(summary.get('agent_auto_repair_ready_action_count', 0))}",
        f"- Agent Auto Repair Goal Reached Runs: {_int(summary.get('agent_auto_repair_goal_reached_count', 0))}",
        f"- Agent Auto Reflection Actions: {_int(summary.get('agent_auto_reflection_action_count', 0))}",
        f"- Agent Auto Reflection Candidate Actions: {_int(summary.get('agent_auto_reflection_candidate_action_count', 0))}",
        f"- Agent Auto Successful Reflection Actions: {_int(summary.get('agent_auto_successful_reflection_action_count', 0))}",
        f"- Agent Auto Reflection Goal Reached Runs: {_int(summary.get('agent_auto_reflection_goal_reached_count', 0))}",
        f"- Repository Test Repair Ready Runs: {_int(summary.get('repository_test_repair_ready_count', 0))}",
        f"- Phase 4 Ready Runs: {_int(summary.get('phase4_ready_count', 0))}",
        f"- Phase 4 Executed Runs: {_int(summary.get('phase4_executed_count', 0))}",
        f"- Phase 4 Baseline Caveat Runs: {_int(summary.get('phase4_baseline_regression_caveat_count', 0))}",
        f"- Repository Test Patch Validation Successes: {_int(summary.get('repository_test_patch_validation_success_count', 0))}",
        (
            "- Repository Test Patch Validation Candidates: "
            f"input={_int(summary.get('repository_test_patch_validation_input_candidate_count', 0))}, "
            f"validated={_int(summary.get('repository_test_patch_validation_candidate_count', 0))}, "
            f"safety_blocked={_int(summary.get('repository_test_patch_validation_safety_blocked_candidate_count', 0))}"
        ),
        f"- Repository Test Reflection Candidates: {_int(summary.get('repository_test_patch_validation_reflection_candidate_count', 0))}",
        f"- Repository Test Reflection Successes: {_int(summary.get('repository_test_patch_validation_successful_reflection_count', 0))}",
        f"- Repository Test Regression Reflection Successes: {_int(summary.get('repository_test_patch_validation_successful_regression_reflection_count', 0))}",
        f"- Reflection Initial Failure Types: {_format_counts(_dict(summary.get('reflection_initial_failure_type_counts')))}",
        f"- Reflection Failure Types: {_format_counts(_dict(summary.get('reflection_failure_type_counts')))}",
        f"- Successful Reflection Parent Failure Types: {_format_counts(_dict(summary.get('successful_reflection_parent_failure_type_counts')))}",
        "",
        "## Status Counts",
        "",
        f"- Agent Statuses: {_format_counts(_dict(summary.get('agent_status_counts')))}",
        f"- Static Intelligence Statuses: {_format_counts(_dict(summary.get('static_intelligence_status_counts')))}",
        f"- Analysis Stages: {_format_counts(_dict(summary.get('analysis_stage_counts')))}",
        f"- Blockers: {_format_counts(_dict(summary.get('blocker_counts')))}",
        f"- Controller Actions: {_format_counts(_dict(summary.get('controller_action_counts')))}",
        f"- Execution Profiles: {_format_counts(_dict(summary.get('execution_profile_counts')))}",
        f"- Agent Shortcut Runs: {_int(summary.get('agent_shortcut_count', 0))}",
        f"- Default Output Dir Runs: {_int(summary.get('output_dir_defaulted_count', 0))}",
        f"- Agent Default Output Dir Runs: {_int(summary.get('agent_default_output_dir_count', 0))}",
        f"- Repo Input Kinds: {_format_counts(_dict(summary.get('repo_input_kind_counts')))}",
        f"- Scenario Tags: {_format_counts(_dict(summary.get('scenario_tag_counts')))}",
        f"- Scenario Coverage Blockers: {_format_counts(_dict(summary.get('scenario_coverage_blocker_counts')))}",
        f"- Artifact Inventory Statuses: {_format_counts(_dict(summary.get('artifact_inventory_status_counts')))}",
        f"- Acceptance Gate Statuses: {_format_counts(_dict(summary.get('acceptance_gate_status_counts')))}",
        f"- Agent Goal Readiness Statuses: {_format_counts(_dict(summary.get('agent_goal_readiness_status_counts')))}",
        f"- Objective Compliance Statuses: {_format_counts(_dict(summary.get('objective_compliance_status_counts')))}",
        f"- Objective Compliance Section Passes: {_format_counts(_dict(summary.get('objective_compliance_section_pass_counts')))}",
        f"- Objective Compliance Section Warnings: {_format_counts(_dict(summary.get('objective_compliance_section_warning_counts')))}",
        f"- Objective Compliance Failed Sections: {_format_counts(_dict(summary.get('objective_compliance_failed_section_counts')))}",
        f"- Phase 4 Evaluation Statuses: {_format_counts(_dict(summary.get('phase4_search_evaluation_status_counts')))}",
        f"- Phase 4 Execution Statuses: {_format_counts(_dict(summary.get('phase4_search_evaluation_execution_status_counts')))}",
        f"- Fault Localization Modes: {_format_counts(_dict(summary.get('fault_localization_mode_counts')))}",
        f"- Fault Localization Top Source Roles: {_format_counts(_dict(summary.get('fault_localization_top_source_role_counts')))}",
        f"- Fault Localization Source Roles: {_format_counts(_dict(summary.get('fault_localization_source_role_counts')))}",
        f"- Failure Overlay Statuses: {_format_counts(_dict(summary.get('repository_test_failure_overlay_status_counts')))}",
        f"- Patch Candidate Statuses: {_format_counts(_dict(summary.get('repository_test_patch_candidates_status_counts')))}",
        f"- Patch Generation Modes: {_format_counts(_dict(summary.get('repository_patch_generation_mode_counts')))}",
        f"- LLM Patch Generation Statuses: {_format_counts(_dict(summary.get('repository_llm_patch_generation_status_counts')))}",
        (
            "- LLM Patch Telemetry: "
            f"requests={_int(summary.get('repository_llm_patch_request_count', 0))}, "
            f"successes={_int(summary.get('repository_llm_patch_success_count', 0))}, "
            f"failures={_int(summary.get('repository_llm_patch_failure_count', 0))}, "
            f"tokens={_int(summary.get('repository_llm_patch_total_tokens', 0))}, "
            f"estimated_tokens={_int(summary.get('repository_llm_patch_estimated_total_tokens', 0))}, "
            f"latency_ms_total={_int(summary.get('repository_llm_patch_latency_ms_total', 0))}, "
            f"latency_ms_average={_float(summary.get('repository_llm_patch_latency_ms_average', 0.0))}, "
            f"estimated_cost_usd={_float(summary.get('repository_llm_patch_estimated_cost_usd_total', 0.0))}"
        ),
        f"- LLM Patch Error Reasons: {_format_counts(_dict(summary.get('repository_llm_patch_error_reason_counts')))}",
        f"- LLM Patch Provider Failure Classes: {_format_counts(_dict(summary.get('repository_llm_patch_provider_failure_class_counts')))}",
        f"- LLM Reflection Statuses: {_format_counts(_dict(summary.get('repository_llm_reflection_status_counts')))}",
        f"- LLM Reflection Blockers: {_format_counts(_dict(summary.get('repository_llm_reflection_blocker_counts')))}",
        f"- Patch Safety Gate Statuses: {_format_counts(_dict(summary.get('repository_patch_safety_gate_status_counts')))}",
        f"- Patch Validation Statuses: {_format_counts(_dict(summary.get('repository_test_patch_validation_status_counts')))}",
        f"- Patch Validation Failure Types: {_format_counts(_dict(summary.get('repository_test_patch_validation_failure_type_counts')))}",
        f"- Reflection Failure Types: {_format_counts(_dict(summary.get('reflection_failure_type_counts')))}",
        f"- Reflection Parent Failure Types: {_format_counts(_dict(summary.get('reflection_parent_failure_type_counts')))}",
        f"- Repair Validation Scopes: {_format_counts(_dict(summary.get('repository_test_repair_validation_scope_counts')))}",
        f"- Planned Repository Test Runners: {_format_counts(_dict(summary.get('planned_repository_test_runner_counts')))}",
        f"- Planned Repository Test Runner Fallback Reasons: {_format_counts(_dict(summary.get('planned_repository_test_runner_fallback_reason_counts')))}",
        f"- Planned Repository Test Result Statuses: {_format_counts(_dict(summary.get('planned_repository_test_result_status_counts')))}",
        f"- Repository Test Count Sources: {_format_counts(_dict(summary.get('repository_test_count_source_counts')))}",
        f"- Repository Test Frameworks: {_format_counts(_dict(summary.get('repository_test_framework_counts')))}",
        f"- Repository Test Command Candidate Runners: {_format_counts(_dict(summary.get('repository_test_command_candidate_runner_counts')))}",
        f"- Planned Repository Test Levels: {_format_counts(_dict(summary.get('planned_repository_test_level_counts')))}",
        f"- Repository Test Environment Statuses: {_format_counts(_dict(summary.get('repository_test_environment_status_counts')))}",
        f"- Repository Test Setup Doctor Statuses: {_format_counts(_dict(summary.get('repository_test_setup_doctor_status_counts')))}",
        f"- Repository Test Setup Doctor Blockers: {_format_counts(_dict(summary.get('repository_test_setup_doctor_blocker_counts')))}",
        f"- Repository Test Setup Doctor Check Statuses: {_format_counts(_dict(summary.get('repository_test_setup_doctor_check_status_counts')))}",
        f"- Repository Test Setup Doctor Blocked Checks: {_format_counts(_dict(summary.get('repository_test_setup_doctor_blocked_check_name_counts')))}",
        f"- Repository Test Setup Doctor Warning Checks: {_format_counts(_dict(summary.get('repository_test_setup_doctor_warning_check_name_counts')))}",
        f"- Repository Test Timeout Narrowing Statuses: {_format_counts(_dict(summary.get('repository_test_timeout_narrowing_status_counts')))}",
        f"- Repository Test Timeout Narrowing Reasons: {_format_counts(_dict(summary.get('repository_test_timeout_narrowing_reason_counts')))}",
        f"- Repository Test Timeout Narrowing Executed Runs: {_int(summary.get('repository_test_timeout_narrowing_executed_count', 0))}",
        f"- Repository Test Timeout Narrowing Attempts: {_int(summary.get('repository_test_timeout_narrowing_attempt_count', 0))}",
        f"- Repository Test Environment Repair Plan Statuses: {_format_counts(_dict(summary.get('repository_test_environment_repair_plan_status_counts')))}",
        f"- Repository Test Environment Repair Plan Blockers: {_format_counts(_dict(summary.get('repository_test_environment_repair_plan_blocker_counts')))}",
        f"- Repository Test Environment Repair Plan Ready Runs: {_int(summary.get('repository_test_environment_repair_plan_ready_count', 0))}",
        f"- Repository Test Environment Repair Plan Install Command Runs: {_int(summary.get('repository_test_environment_repair_plan_install_command_count', 0))}",
        f"- Dynamic Evidence Levels: {_format_counts(_dict(summary.get('repository_test_dynamic_evidence_level_counts')))}",
        f"- Agent Auto Verify Outcomes: {_format_counts(_dict(summary.get('agent_auto_verify_outcome_counts')))}",
        f"- Agent Auto Reflect Statuses: {_format_counts(_dict(summary.get('agent_auto_reflect_status_counts')))}",
        f"- Agent Auto Replan Policies: {_format_counts(_dict(summary.get('agent_auto_replan_policy_counts')))}",
        f"- Agent Auto Goal Readiness Statuses: {_format_counts(_dict(summary.get('agent_auto_goal_readiness_status_counts')))}",
        f"- Agent Auto Goal Readiness Passed Actions: {_int(summary.get('agent_auto_goal_readiness_passed_action_count', 0))}",
        f"- Agent Auto Actions: {_format_counts(_dict(summary.get('agent_auto_action_id_counts')))}",
        f"- Agent Auto Stop Categories: {_format_counts(_dict(summary.get('agent_auto_stop_category_counts')))}",
        f"- Agent Auto Stop Reasons: {_format_counts(_dict(summary.get('agent_auto_stop_reason_counts')))}",
        f"- Agent Auto Stop Recovery Policies: {_format_counts(_dict(summary.get('agent_auto_stop_recovery_policy_counts')))}",
        f"- Agent Auto Stop External Inputs: {_format_counts(_dict(summary.get('agent_auto_stop_external_input_kind_counts')))}",
        f"- Agent Auto Stops Requiring User Action: {_int(summary.get('agent_auto_stop_requires_user_action_count', 0))}",
        f"- Agent Auto Stops Requiring Environment Change: {_int(summary.get('agent_auto_stop_requires_environment_change_count', 0))}",
        f"- Agent Answer Testability Statuses: {_format_counts(_dict(summary.get('agent_answer_testability_status_counts')))}",
        f"- Agent Answer Repairability Statuses: {_format_counts(_dict(summary.get('agent_answer_repairability_status_counts')))}",
        f"- Agent Missing Questions: {_format_counts(_dict(summary.get('agent_answer_question_missing_counts')))}",
        f"- Missing Agent Answer Questions: {_format_counts(_dict(summary.get('agent_answer_missing_question_counts')))}",
        "",
        "## Runs",
        "",
        "| Name | Repo | Profile | Tags | Status | Expected | Expectation | Stage | Patch Mode | Phase 4 | Answers | Tests | Reflect | Blocker | Action | Artifacts | Report |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for run in report.runs:
        metrics = run.metrics
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(run.name),
                    _markdown_cell(run.repo),
                    _markdown_cell(str(metrics.get("execution_profile") or "")),
                    _markdown_cell(", ".join(str(item) for item in _list(metrics.get("scenario_tags")))),
                    _markdown_cell(run.status),
                    _markdown_cell(run.expected_status),
                    _markdown_cell(str(run.expectation_passed).lower()),
                    _markdown_cell(str(metrics.get("analysis_stage") or "")),
                    _markdown_cell(
                        str(metrics.get("repository_patch_generation_mode") or "")
                    ),
                    _markdown_cell(
                        str(metrics.get("phase4_search_evaluation_status") or "")
                    ),
                    _markdown_cell(
                        (
                            f"{_int(metrics.get('agent_answer_coverage_answered_count', 0))}/"
                            f"{_int(metrics.get('agent_answer_coverage_required_count', 0))}"
                        )
                    ),
                    _markdown_cell(
                        (
                            f"{str(metrics.get('planned_repository_test_result_status') or 'none')}:"
                            f"{_int(metrics.get('planned_repository_test_result_passed', 0))}/"
                            f"{_int(metrics.get('planned_repository_test_result_test_count', 0))}"
                        )
                    ),
                    _markdown_cell(
                        (
                            f"{_int(metrics.get('repository_test_patch_validation_successful_reflection_count', 0))}/"
                            f"{_int(metrics.get('repository_test_patch_validation_reflection_candidate_count', 0))}"
                        )
                    ),
                    _markdown_cell(str(metrics.get("blocker") or "")),
                    _markdown_cell(str(metrics.get("controller_action_id") or "")),
                    _markdown_cell(str(metrics.get("artifact_inventory_status") or "")),
                    _markdown_cell(run.report_path or "none"),
                ]
            )
            + " |"
        )
    if not report.runs:
        lines.append("| none | none | none | none | none | none | none | none | none | none | none | none | none | none | none | none | none |")

    threshold_checks = _list(summary.get("suite_threshold_checks"))
    if threshold_checks:
        lines.extend(
            [
                "",
                "## Suite Threshold Checks",
                "",
                "| Name | Metric | Expected | Actual | Passed |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for check in threshold_checks:
            item = _dict(check)
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_cell(item.get("name")),
                        _markdown_cell(item.get("metric")),
                        _markdown_cell(item.get("expected")),
                        _markdown_cell(item.get("actual")),
                        _markdown_cell(str(item.get("passed")).lower()),
                    ]
                )
                + " |"
            )

    failed_runs = [run for run in report.runs if run.error]
    if failed_runs:
        lines.extend(["", "## Command Errors", ""])
        for run in failed_runs:
            lines.append(f"- `{_markdown_cell(run.name)}`: {_markdown_cell(run.error)}")
    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a batch smoke suite for GitHub repo intelligence reports."
    )
    parser.add_argument("manifest")
    parser.add_argument("output_dir")
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument(
        "--require-success",
        action="store_true",
        help="Exit non-zero unless all suite expectations and thresholds pass.",
    )
    parser.add_argument(
        "--reuse-existing-reports",
        action="store_true",
        help=(
            "Recompute suite expectations from existing per-run "
            "github_repo_intelligence.json files without rerunning repositories."
        ),
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=0,
        help="Run only manifest entries starting from this zero-based index.",
    )
    parser.add_argument(
        "--limit-runs",
        type=int,
        default=None,
        help="Run at most this many manifest entries.",
    )
    return parser


def main(argv: list[str] | None = None, opener=None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    report = run_github_repo_intelligence_suite(
        args.manifest,
        args.output_dir,
        opener=opener,
        reuse_existing_reports=args.reuse_existing_reports,
        start_index=args.start_index,
        limit_runs=args.limit_runs,
    )
    rendered_json = json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
    rendered_markdown = render_github_repo_intelligence_suite_markdown(report)
    if args.output_json:
        Path(args.output_json).write_text(rendered_json, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(rendered_markdown, encoding="utf-8")
    print(rendered_json if args.format == "json" else rendered_markdown)
    raise SystemExit(0 if report.passed or not args.require_success else 1)


def _run_result_from_report(
    *,
    name: str,
    repo: str,
    report: GitHubRepoAgentReport,
    expected_status: str,
    options: dict[str, Any],
    metric_thresholds: dict[str, float],
    command_args: list[str],
) -> GitHubRepoIntelligenceSuiteRunResult:
    summary = github_repo_intelligence_summary(report)
    write_github_repo_intelligence_artifacts(report, summary)
    metrics = _suite_metric_snapshot(summary)
    metrics["execution_profile"] = str(options.get("execution_profile") or "static")
    metrics["agent_shortcut_expected"] = _bool_option(options, "agent", False)
    metrics["repo_input_kind"] = _repo_input_kind(repo)
    metrics["scenario_tags"] = _scenario_tags(options)
    metric_checks = _evaluate_metric_thresholds(metrics, metric_thresholds)
    expectation_checks = _evaluate_expectations(
        metrics,
        expected_status=expected_status,
        options=options,
    )
    expectation_passed = all(bool(check.get("passed")) for check in expectation_checks)
    if metric_checks:
        expectation_passed = expectation_passed and all(
            bool(check.get("passed")) for check in metric_checks
        )
    return GitHubRepoIntelligenceSuiteRunResult(
        name=name,
        repo=repo,
        output_dir=report.output_dir,
        report_path=str(summary.get("intelligence_json") or ""),
        status=str(summary.get("status") or report.status),
        passed=bool(summary.get("passed", report.passed)),
        expected_status=expected_status,
        expectation_passed=expectation_passed,
        metrics=metrics,
        metric_checks=metric_checks,
        expectation_checks=expectation_checks,
        command_args=command_args,
    )


def _cached_run_result_from_existing_report(
    *,
    name: str,
    repo: str,
    output_dir: Path,
    expected_status: str,
    options: dict[str, Any],
    metric_thresholds: dict[str, float],
    command_args: list[str],
    error: GitHubAPIError,
) -> GitHubRepoIntelligenceSuiteRunResult | None:
    return _existing_report_run_result(
        name=name,
        repo=repo,
        output_dir=output_dir,
        expected_status=expected_status,
        options=options,
        metric_thresholds=metric_thresholds,
        command_args=command_args,
        cached_report_fallback=True,
        cached_report_fallback_reason=str(error),
        existing_report_reuse=False,
        existing_report_reuse_reason="",
    )


def _reused_run_result_from_existing_report(
    *,
    name: str,
    repo: str,
    output_dir: Path,
    expected_status: str,
    options: dict[str, Any],
    metric_thresholds: dict[str, float],
    command_args: list[str],
    reuse_reason: str,
) -> GitHubRepoIntelligenceSuiteRunResult | None:
    return _existing_report_run_result(
        name=name,
        repo=repo,
        output_dir=output_dir,
        expected_status=expected_status,
        options=options,
        metric_thresholds=metric_thresholds,
        command_args=command_args,
        cached_report_fallback=False,
        cached_report_fallback_reason="",
        existing_report_reuse=True,
        existing_report_reuse_reason=reuse_reason,
    )


def _run_cli_default_output_dir_result(
    *,
    name: str,
    repo: str,
    output_root: Path,
    run_output: Path,
    expected_status: str,
    options: dict[str, Any],
    command_args: list[str],
    opener=None,
) -> GitHubRepoIntelligenceSuiteRunResult:
    captured_stdout = io.StringIO()
    exit_code = 0
    with _temporary_working_directory(output_root), redirect_stdout(captured_stdout):
        try:
            github_repo_intelligence_main(command_args[3:], opener=opener)
        except SystemExit as exc:
            exit_code = _int(exc.code)
    if exit_code != 0:
        raise OSError(
            "github_repo_intelligence CLI default-output run exited "
            f"with code {exit_code}"
        )
    result = _existing_report_run_result(
        name=name,
        repo=repo,
        output_dir=run_output,
        expected_status=expected_status,
        options=options,
        metric_thresholds=_metric_thresholds(options),
        command_args=command_args,
        cached_report_fallback=False,
        cached_report_fallback_reason="",
        existing_report_reuse=False,
        existing_report_reuse_reason="",
    )
    if result is None:
        raise OSError(
            "github_repo_intelligence CLI default-output run did not write "
            f"{run_output / 'github_repo_intelligence.json'}"
        )
    return result


def _existing_report_run_result(
    *,
    name: str,
    repo: str,
    output_dir: Path,
    expected_status: str,
    options: dict[str, Any],
    metric_thresholds: dict[str, float],
    command_args: list[str],
    cached_report_fallback: bool,
    cached_report_fallback_reason: str,
    existing_report_reuse: bool,
    existing_report_reuse_reason: str,
) -> GitHubRepoIntelligenceSuiteRunResult | None:
    report_path = output_dir / "github_repo_intelligence.json"
    if not report_path.is_file():
        return None
    try:
        summary = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    summary = refresh_github_repo_intelligence_summary_status(summary)
    if not _summary_matches_requested_repo(summary, repo):
        return None
    metrics = _suite_metric_snapshot(summary)
    metrics["execution_profile"] = str(options.get("execution_profile") or "static")
    metrics["agent_shortcut_expected"] = _bool_option(options, "agent", False)
    metrics["repo_input_kind"] = _repo_input_kind(repo)
    metrics["scenario_tags"] = _scenario_tags(options)
    metrics["cached_report_fallback"] = cached_report_fallback
    metrics["cached_report_fallback_reason"] = cached_report_fallback_reason
    metrics["existing_report_reuse"] = existing_report_reuse
    metrics["existing_report_reuse_reason"] = existing_report_reuse_reason
    metric_checks = _evaluate_metric_thresholds(metrics, metric_thresholds)
    expectation_checks = _evaluate_expectations(
        metrics,
        expected_status=expected_status,
        options=options,
    )
    expectation_passed = all(bool(check.get("passed")) for check in expectation_checks)
    if metric_checks:
        expectation_passed = expectation_passed and all(
            bool(check.get("passed")) for check in metric_checks
        )
    return GitHubRepoIntelligenceSuiteRunResult(
        name=name,
        repo=repo,
        output_dir=str(output_dir),
        report_path=str(report_path),
        status=str(summary.get("status") or ""),
        passed=bool(summary.get("passed", False)),
        expected_status=expected_status,
        expectation_passed=expectation_passed,
        metrics=metrics,
        metric_checks=metric_checks,
        expectation_checks=expectation_checks,
        command_args=command_args,
    )


def _summary_matches_requested_repo(summary: dict[str, Any], repo: str) -> bool:
    requested = str(repo or "").strip()
    saved_values = [
        str(summary.get("repo_spec") or "").strip(),
        str(summary.get("repo") or "").strip(),
    ]
    saved_values = [value for value in saved_values if value]
    if not requested or not saved_values:
        return False
    requested_identity = _repo_identity(requested)
    for saved in saved_values:
        if saved == requested:
            return True
        saved_identity = _repo_identity(saved)
        if requested_identity and saved_identity and requested_identity == saved_identity:
            return True
        if "/" in saved and saved in requested:
            return True
    return False


def _repo_identity(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    lowered = text.lower()
    for prefix in ("https://github.com/", "http://github.com/"):
        if lowered.startswith(prefix):
            text = text[len(prefix) :]
            break
    if text.endswith(".git"):
        text = text[:-4]
    parts = [part for part in text.split("/") if part]
    if len(parts) < 2:
        return ""
    return "/".join(parts[:2]).lower()


def _reuse_existing_report_requested(
    options: dict[str, Any],
    *,
    suite_reuse_existing_reports: bool,
) -> bool:
    return bool(
        suite_reuse_existing_reports
        or _bool_option(options, "reuse_existing_report", False)
        or _bool_option(options, "reuse_existing_reports", False)
    )


def _seed_discovery_cache(options: dict[str, Any], output_dir: Path) -> None:
    seed_path_text = str(options.get("seed_discovery_path") or "").strip()
    if not seed_path_text:
        return
    seed_path = Path(seed_path_text)
    if not seed_path.is_file():
        raise FileNotFoundError(f"seed discovery file not found: {seed_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    target_path = output_dir / "discovery.json"
    try:
        if seed_path.resolve() == target_path.resolve():
            return
    except OSError:
        pass
    shutil.copyfile(seed_path, target_path)


@contextmanager
def _temporary_working_directory(path: Path):
    previous = Path.cwd()
    path.mkdir(parents=True, exist_ok=True)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _is_rate_limit_error(error: GitHubAPIError) -> bool:
    message = str(error).lower()
    return bool(
        error.status_code in {403, 429}
        and (
            str(error.rate_limit_remaining or "") == "0"
            or "rate limit" in message
            or "rate limit exceeded" in error.response_body.lower()
        )
    )


def _slice_runs(
    runs: list[dict[str, Any]],
    *,
    start_index: int,
    limit_runs: int | None,
) -> list[tuple[int, dict[str, Any]]]:
    start = _normalized_start_index(start_index)
    indexed = list(enumerate(runs))
    if start:
        indexed = indexed[start:]
    if limit_runs is not None:
        limit = max(0, _int(limit_runs))
        indexed = indexed[:limit]
    return indexed


def _normalized_start_index(value: Any) -> int:
    return max(0, _int(value))


def _append_run_result(
    run_results: list[GitHubRepoIntelligenceSuiteRunResult],
    result: GitHubRepoIntelligenceSuiteRunResult,
    started_at: float,
) -> None:
    elapsed_ms = max(0, int(round((time.perf_counter() - started_at) * 1000)))
    run_results.append(replace(result, elapsed_ms=elapsed_ms))


def _failed_run_result(
    *,
    name: str,
    repo: str,
    output_dir: str,
    expected_status: str,
    error: str,
    command_args: list[str] | None = None,
) -> GitHubRepoIntelligenceSuiteRunResult:
    return GitHubRepoIntelligenceSuiteRunResult(
        name=name,
        repo=repo,
        output_dir=output_dir,
        report_path="",
        status="command_error",
        passed=False,
        expected_status=expected_status,
        expectation_passed=False,
        metrics={},
        metric_checks=[],
        expectation_checks=[
            {
                "name": "status",
                "expected": expected_status,
                "actual": "command_error",
                "passed": False,
            }
        ],
        command_args=command_args or [],
        error=error,
    )


def _controlled_repair_case_result(
    *,
    name: str,
    repo: str,
    output_dir: Path,
    expected_status: str,
    options: dict[str, Any],
    metric_thresholds: dict[str, float],
    command_args: list[str],
) -> GitHubRepoIntelligenceSuiteRunResult | None:
    case_kind = str(options.get("controlled_repair_case") or "").strip().lower()
    if not case_kind:
        return None
    if case_kind != "safety_gate_blocker":
        raise ValueError(f"unsupported controlled_repair_case: {case_kind}")

    output_dir.mkdir(parents=True, exist_ok=True)
    repo_root = output_dir / "controlled_repo"
    tests_root = repo_root / "tests"
    tests_root.mkdir(parents=True, exist_ok=True)
    old_source = "def pick(values):\n    return values[0]\n"
    unsafe_source = (
        "def pick(values):\n"
        "    if not values:\n"
        "        return None\n"
        "    return values[0]\n"
        "\n"
        "class EscapedPatchScope:\n"
        "    pass\n"
    )
    (repo_root / "sample.py").write_text(old_source, encoding="utf-8")
    (tests_root / "test_sample.py").write_text(
        "from sample import pick\n\n\n"
        "def test_pick_empty():\n"
        "    assert pick([]) is None\n",
        encoding="utf-8",
    )

    validation = validate_function_patch(old_source, unsafe_source)
    safety_gate = {
        "status": "pass" if validation.valid else "blocked",
        "ast_valid": validation.ast_valid,
        "scope_limited": validation.scope_limited,
        "minimal_diff": not (
            "patch_too_large" in validation.reasons
            or "patch_change_ratio_too_large" in validation.reasons
        ),
        "signature_change_allowed": validation.signature_change_allowed,
        "reasons": validation.reasons,
    }
    patch_candidates = {
        "status": "pass",
        "reason": "controlled_safety_gate_candidate",
        "message": (
            "Controlled repair case generated a candidate that must be "
            "blocked before sandbox execution."
        ),
        "repository_root": str(repo_root),
        "candidate_count": 1,
        "patch_generation_mode": str(
            options.get("repository_patch_generation_mode") or "hybrid"
        ),
        "generator_counts": {"controlled": 1},
        "safety_gate": {
            "status": "blocked",
            "candidate_count": 1,
            "passed_count": 0,
            "blocked_count": 1,
            "all_candidates_safe": False,
            "reason_counts": {
                str(reason): validation.reasons.count(reason)
                for reason in sorted(set(validation.reasons))
            },
            "required_checks": [
                "ast_valid",
                "scope_limited",
                "signature_guard",
                "minimal_diff",
            ],
        },
        "recommended_pytest_args": ["tests/test_sample.py::test_pick_empty"],
        "recommended_pytest_args_source": "controlled_dynamic_oracle",
        "recommended_validation_command": (
            "python -m pytest -q tests/test_sample.py::test_pick_empty"
        ),
        "targets": [{"function_id": "sample.py::pick", "score": 1.0}],
        "candidates": [
            {
                "id": f"{name}_unsafe_patch",
                "target_file": str(repo_root / "sample.py"),
                "relative_file_path": "sample.py",
                "target_function_id": "sample.py::pick",
                "target_function_name": "pick",
                "rule_id": "controlled_safety_gate_probe",
                "description": "Controlled unsafe patch for safety gate evidence.",
                "old_source": old_source,
                "new_source": unsafe_source,
                "diff": render_unified_diff(old_source, unsafe_source, "sample.py"),
                "metadata": {
                    "generator": "controlled_safety_gate",
                    "variant": "scope_escape_probe",
                    "safety_gate": safety_gate,
                    "validation": validation.to_dict(),
                },
            }
        ],
        "next_actions": [
            "Inspect safety-gate reasons before retrying patch generation.",
            "Generate a smaller AST-valid patch limited to the target function.",
        ],
    }
    candidate_paths = write_repository_test_patch_candidates_artifacts(
        patch_candidates,
        output_dir,
    )
    patch_validation = build_repository_test_patch_validation(
        patch_candidates,
        repository_root=repo_root,
        validation_limit=1,
        reflection_mode="none",
        reflection_rounds=0,
        reflection_width=0,
        timeout=_int(options.get("repository_test_timeout", 10)),
    )
    validation_paths = write_repository_test_patch_validation_artifacts(
        patch_validation,
        output_dir,
    )
    summary = _controlled_safety_gate_summary(
        name=name,
        repo=repo,
        output_dir=output_dir,
        repo_root=repo_root,
        patch_candidates=patch_candidates,
        patch_validation=patch_validation,
        candidate_paths=candidate_paths,
        validation_paths=validation_paths,
        options=options,
    )
    report_path = output_dir / "github_repo_intelligence.json"
    report_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_path = output_dir / "github_repo_intelligence.md"
    markdown_path.write_text(
        _controlled_safety_gate_markdown(summary),
        encoding="utf-8",
    )
    summary["intelligence_json"] = str(report_path)
    summary["intelligence_markdown"] = str(markdown_path)
    report_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    metrics = _suite_metric_snapshot(summary)
    metrics["execution_profile"] = str(options.get("execution_profile") or "controlled")
    metrics["agent_shortcut_expected"] = False
    metrics["repo_input_kind"] = "controlled"
    metrics["scenario_tags"] = _scenario_tags(options)
    metric_checks = _evaluate_metric_thresholds(metrics, metric_thresholds)
    expectation_checks = _evaluate_expectations(
        metrics,
        expected_status=expected_status,
        options=options,
    )
    expectation_passed = all(bool(check.get("passed")) for check in expectation_checks)
    if metric_checks:
        expectation_passed = expectation_passed and all(
            bool(check.get("passed")) for check in metric_checks
        )
    return GitHubRepoIntelligenceSuiteRunResult(
        name=name,
        repo=repo,
        output_dir=str(output_dir),
        report_path=str(report_path),
        status=str(summary.get("status") or ""),
        passed=bool(summary.get("passed", False)),
        expected_status=expected_status,
        expectation_passed=expectation_passed,
        metrics=metrics,
        metric_checks=metric_checks,
        expectation_checks=expectation_checks,
        command_args=command_args,
    )


def _controlled_safety_gate_summary(
    *,
    name: str,
    repo: str,
    output_dir: Path,
    repo_root: Path,
    patch_candidates: dict[str, Any],
    patch_validation: dict[str, Any],
    candidate_paths: dict[str, str],
    validation_paths: dict[str, str],
    options: dict[str, Any],
) -> dict[str, Any]:
    blocker = "patch_candidates_blocked_by_safety_gate"
    next_action = (
        "Generate a smaller AST-valid, scope-limited patch before rerunning "
        "sandbox validation."
    )
    selected_action = {
        "id": "regenerate_safe_patch_candidates",
        "phase": "phase3",
        "tool": "repository_test_patch_candidates",
        "reason": "All controlled candidates were blocked by the pre-sandbox safety gate.",
        "command": "rerun patch generation with a narrower scope",
    }
    decision_trace = [
        {"phase": phase, "action_id": selected_action["id"]}
        for phase in EXPECTED_AGENT_CONTROLLER_LOOP
    ]
    validation_status = str(patch_validation.get("status") or "")
    validation_reason = str(patch_validation.get("reason") or "")
    safety_blocked_count = _int(
        patch_validation.get("safety_blocked_candidate_count", 0)
    )
    return {
        "repo": repo,
        "repo_spec": repo,
        "output_dir": str(output_dir),
        "status": "pass",
        "passed": True,
        "repository_root": str(repo_root),
        "controlled_repair_case": "safety_gate_blocker",
        "analysis_readiness": {
            "current_stage": "phase3_patch_validation",
            "next_stage": "phase3_patch_reflection_or_expansion",
            "blocker": blocker,
            "patch_validation_status": validation_status,
            "patch_validation_reason": validation_reason,
            "patch_validation_input_candidate_count": _int(
                patch_validation.get("input_candidate_count", 0)
            ),
            "patch_validation_candidate_count": _int(
                patch_validation.get("candidate_count", 0)
            ),
            "patch_validation_safety_blocked_candidate_count": safety_blocked_count,
            "repair_ready": False,
            "repair_validation_scope": "none",
        },
        "repository_structure": {
            "analyzed_file_count": 1,
            "function_count": 1,
            "class_count": 0,
            "total_loc": 2,
            "test_structure": {
                "test_directory_count": 1,
                "test_command_candidate_count": 1,
                "test_command_runner_counts": {"pytest": 1},
                "test_command_runner_kind_count": 1,
                "test_framework_signals": ["pytest"],
            },
            "package_structure": {},
        },
        "fault_localization": {
            "mode": "controlled",
            "status": "pass",
            "reason": "controlled_safety_gate_probe",
            "top_function": "pick",
            "rankings": [
                {
                    "rank": 1,
                    "function_id": "sample.py::pick",
                    "function_name": "pick",
                    "file_path": "sample.py",
                    "final_score": 1.0,
                    "dynamic_test_evidence_score": 1.0,
                }
            ],
        },
        "agent_controller": {
            "control_loop": EXPECTED_AGENT_CONTROLLER_LOOP,
            "decision_trace": decision_trace,
            "selected_action": selected_action,
            "primary_blocker": blocker,
            "observations": [{"signal": "patch_validation_reason", "value": validation_reason}],
            "plan": [{"step": 1, "action": selected_action["id"]}],
        },
        "agent_decision_timeline": {
            "status": "complete",
            "complete": True,
            "step_count": len(EXPECTED_AGENT_CONTROLLER_LOOP),
            "complete_step_count": len(EXPECTED_AGENT_CONTROLLER_LOOP),
            "executed_step_count": 1,
            "blocked_step_count": 1,
        },
        "agent_answers": {
            "blocker": blocker,
            "next_action": next_action,
            "repairability": {
                "status": blocker,
                "can_repair": True,
                "answer": (
                    "The candidate was blocked by AST/scope safety checks before "
                    "pytest; regenerate a smaller function-scoped patch."
                ),
            },
            "testability": {
                "status": "controlled_oracle_available",
                "answer": "The controlled pytest nodeid is available but was not executed after the safety gate blocked the patch.",
            },
            "answer_coverage": {
                "complete": True,
                "answered_question_count": 3,
                "required_question_count": 3,
                "missing_questions": [],
                "questions": [
                    {"id": "blocker", "answered": True},
                    {"id": "next_action", "answered": True},
                    {"id": "repairability", "answered": True},
                ],
            },
        },
        "repository_test_patch_candidates_status": str(patch_candidates.get("status") or ""),
        "repository_test_patch_candidates_reason": str(patch_candidates.get("reason") or ""),
        "repository_test_patch_candidate_count": _int(patch_candidates.get("candidate_count", 0)),
        "repository_patch_generation_mode": str(
            options.get("repository_patch_generation_mode") or "hybrid"
        ),
        "repository_patch_generator_counts": _dict(patch_candidates.get("generator_counts")),
        "repository_patch_safety_gate_status": "blocked",
        "repository_patch_safety_gate_blocked_count": _int(
            _dict(patch_candidates.get("safety_gate")).get("blocked_count", 0)
        ),
        "repository_patch_candidate_variant_filter": {},
        "repository_llm_patch_generation_status": "not_run",
        "repository_llm_patch_generation_reason": "controlled_safety_gate_case",
        "repository_test_patch_validation_status": validation_status,
        "repository_test_patch_validation_reason": validation_reason,
        "repository_test_patch_validation_input_candidate_count": _int(
            patch_validation.get("input_candidate_count", 0)
        ),
        "repository_test_patch_validation_candidate_count": _int(
            patch_validation.get("candidate_count", 0)
        ),
        "repository_test_patch_validation_safety_blocked_candidate_count": safety_blocked_count,
        "repository_test_patch_validation_executed_count": _int(
            patch_validation.get("executed_count", 0)
        ),
        "repository_test_patch_validation_success_count": _int(
            patch_validation.get("success_count", 0)
        ),
        "repository_test_patch_validation_failure_type_counts": {},
        "repository_test_patch_validation_reflection_candidate_count": 0,
        "repository_test_patch_validation_successful_reflection_count": 0,
        "repository_test_patch_validation_json": str(
            validation_paths.get("repository_test_patch_validation_json") or ""
        ),
        "repository_test_patch_validation_markdown": str(
            validation_paths.get("repository_test_patch_validation_markdown") or ""
        ),
        "repository_test_patch_candidates_json": str(
            candidate_paths.get("repository_test_patch_candidates_json") or ""
        ),
        "repository_test_patch_candidates_markdown": str(
            candidate_paths.get("repository_test_patch_candidates_markdown") or ""
        ),
        "repository_test_patch_judge_mode": "none",
        "repository_test_patch_judge_status": "disabled",
        "repository_test_patch_judge_authority": "sandbox_pytest_decides_success",
        "repository_test_patch_judge_candidate_count": 0,
        "repository_test_repair_ready": False,
        "repository_test_repair_validation_scope": "none",
        "final_report": {
            "objective_compliance": {
                "status": "pass",
                "passed": True,
                "section_count": 1,
                "passed_section_count": 1,
                "failed_section_count": 0,
                "failed_sections": [],
                "sections": [{"id": "safety_gate_blocker", "passed": True}],
            }
        },
    }


def _controlled_safety_gate_markdown(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Controlled Safety Gate Repair Case",
            "",
            f"- Repo: `{summary.get('repo_spec')}`",
            f"- Status: `{summary.get('status')}`",
            f"- Blocker: `{_dict(summary.get('analysis_readiness')).get('blocker')}`",
            f"- Patch Validation: `{summary.get('repository_test_patch_validation_status')}`",
            f"- Patch Validation Reason: `{summary.get('repository_test_patch_validation_reason')}`",
            f"- Safety Blocked Candidates: {summary.get('repository_test_patch_validation_safety_blocked_candidate_count')}",
            "- Sandbox Authority: `sandbox_pytest_decides_success`",
            "",
        ]
    )


def _llm_configuration_preflight_result(
    *,
    name: str,
    repo: str,
    output_dir: Path,
    expected_status: str,
    options: dict[str, Any],
    metric_thresholds: dict[str, float],
    command_args: list[str],
) -> GitHubRepoIntelligenceSuiteRunResult | None:
    if not _bool_option(options, "require_llm_configuration", False):
        return None
    audit_patch_mode = _llm_preflight_patch_mode(options)
    patch_judge_mode = str(options.get("patch_judge_mode") or "none")
    audit = llm_config_audits_for_modes(
        patch_mode=audit_patch_mode,
        judge_mode=str(options.get("judge_mode") or "none"),
        patch_judge_mode=patch_judge_mode,
        llm_score_mode=str(options.get("llm_score_mode") or "none"),
    )
    missing_roles = [
        str(item) for item in _list(audit.get("missing_enabled_api_key_roles"))
    ]
    invalid_key_findings = (
        _llm_invalid_api_key_findings(
            audit,
            min_length=_int(options.get("llm_api_key_min_length", 20)),
        )
        if _bool_option(options, "reject_placeholder_llm_api_keys", False)
        else []
    )
    invalid_roles = [
        str(item.get("role") or "")
        for item in invalid_key_findings
        if str(item.get("role") or "")
    ]
    blocked_roles = _dedupe_strings([*missing_roles, *invalid_roles])
    if not blocked_roles:
        _write_llm_config_preflight_artifacts(
            output_dir,
            audit=audit,
            status="pass",
            reason="llm_configuration_ready",
            missing_roles=[],
            invalid_key_findings=[],
        )
        return None

    block_reason = (
        "missing_enabled_llm_api_key"
        if missing_roles
        else "invalid_enabled_llm_api_key"
    )
    preflight_path = _write_llm_config_preflight_artifacts(
        output_dir,
        audit=audit,
        status="blocked",
        reason=block_reason,
        missing_roles=missing_roles,
        invalid_key_findings=invalid_key_findings,
    )
    metrics = _llm_preflight_blocked_metrics(
        options=options,
        audit=audit,
        missing_roles=missing_roles,
        invalid_key_findings=invalid_key_findings,
    )
    metric_checks = _evaluate_metric_thresholds(metrics, metric_thresholds)
    expectation_checks = _evaluate_expectations(
        metrics,
        expected_status=expected_status,
        options=options,
    )
    expectation_passed = all(bool(check.get("passed")) for check in expectation_checks)
    expected_blocker = expectation_passed and expected_status == "llm_config_blocked"
    return GitHubRepoIntelligenceSuiteRunResult(
        name=name,
        repo=repo,
        output_dir=str(output_dir),
        report_path=str(preflight_path),
        status="llm_config_blocked",
        passed=expected_blocker,
        expected_status=expected_status,
        expectation_passed=expectation_passed,
        metrics=metrics,
        metric_checks=metric_checks,
        expectation_checks=expectation_checks,
        command_args=command_args,
        error=(
            None
            if expected_blocker
            else (
                "missing_enabled_llm_api_key_roles:"
                + ",".join(missing_roles)
                if missing_roles
                else "invalid_enabled_llm_api_key_roles:"
                + ",".join(invalid_roles)
            )
        ),
    )


def _llm_preflight_patch_mode(options: dict[str, Any]) -> str:
    patch_mode = str(options.get("repository_patch_generation_mode") or "rule")
    reflection_mode = str(options.get("repository_test_reflection_mode") or "none")
    if reflection_mode.lower() == "llm" and patch_mode.lower() not in {
        "llm",
        "hybrid",
    }:
        return "llm"
    return patch_mode


def _write_llm_config_preflight_artifacts(
    output_dir: Path,
    *,
    audit: dict[str, Any],
    status: str,
    reason: str,
    missing_roles: list[str],
    invalid_key_findings: list[dict[str, Any]] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    invalid_findings = [_dict(item) for item in _list(invalid_key_findings)]
    invalid_roles = _dedupe_strings(
        str(item.get("role") or "") for item in invalid_findings
    )
    blocked_roles = _dedupe_strings([*missing_roles, *invalid_roles])
    required_environment = _llm_preflight_required_environment(
        audit,
        missing_roles=blocked_roles,
    )
    next_actions = _llm_preflight_next_actions(
        required_environment,
        invalid_key_findings=invalid_findings,
    )
    payload = {
        "status": status,
        "reason": reason,
        "missing_enabled_api_key_roles": missing_roles,
        "invalid_enabled_api_key_roles": invalid_roles,
        "invalid_api_key_findings": invalid_findings,
        "required_environment": required_environment,
        "next_actions": next_actions,
        "llm_config_audit": audit,
    }
    json_path = output_dir / "llm_config_preflight.json"
    markdown_path = output_dir / "llm_config_preflight.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown = [
        "# LLM Configuration Preflight",
        "",
        f"- Status: `{status}`",
        f"- Reason: `{reason}`",
        (
            "- Missing Enabled API Key Roles: "
            + (", ".join(missing_roles) if missing_roles else "none")
        ),
        (
            "- Invalid Enabled API Key Roles: "
            + (", ".join(invalid_roles) if invalid_roles else "none")
        ),
        "",
        "## Required Environment",
        "",
        *_render_llm_preflight_required_environment_markdown(
            required_environment
        ),
        "## Next Actions",
        "",
        *(
            [f"- {action}" for action in next_actions]
            if next_actions
            else ["- LLM configuration is ready; continue the suite."]
        ),
        "",
        render_llm_config_audit_markdown(audit),
        "",
    ]
    markdown_path.write_text("\n".join(markdown), encoding="utf-8")
    return json_path


def _render_llm_preflight_required_environment_markdown(
    required_environment: list[dict[str, Any]],
) -> list[str]:
    if not required_environment:
        return ["No missing enabled LLM roles.", ""]
    lines: list[str] = []
    for item in required_environment:
        accepted_envs = ", ".join(_list_str(item.get("accepted_api_key_envs")))
        lines.extend(
            [
                f"### {_markdown_cell(item.get('role'))}",
                "",
                "- Accepted API Key Envs: "
                + (accepted_envs if accepted_envs else "none"),
                f"- Provider Env: `{_markdown_cell(item.get('provider_env'))}`",
                f"- Model Env: `{_markdown_cell(item.get('model_env'))}`",
                f"- Base URL Env: `{_markdown_cell(item.get('base_url_env'))}`",
                f"- Resolved Provider: `{_markdown_cell(item.get('provider'))}`",
                f"- Resolved Model: `{_markdown_cell(item.get('model'))}`",
                f"- Resolved Base URL: `{_markdown_cell(item.get('base_url'))}`",
                "",
            ]
        )
    return lines


def _llm_preflight_required_environment(
    audit: dict[str, Any],
    *,
    missing_roles: list[str],
) -> list[dict[str, Any]]:
    roles = {
        str(_dict(item).get("role") or ""): _dict(item)
        for item in _list(audit.get("roles"))
    }
    requirements: list[dict[str, Any]] = []
    for role_name in missing_roles:
        role = roles.get(role_name, {})
        requirements.append(
            {
                "role": role_name,
                "accepted_api_key_envs": _list_str(
                    role.get("checked_api_key_envs")
                ),
                "provider_env": _llm_role_env_name(role_name, "provider"),
                "model_env": _llm_role_env_name(role_name, "model"),
                "base_url_env": _llm_role_env_name(role_name, "base_url"),
                "provider": str(role.get("provider") or ""),
                "model": str(role.get("model") or ""),
                "base_url": str(role.get("base_url") or ""),
            }
        )
    return requirements


def _llm_preflight_next_actions(
    required_environment: list[dict[str, Any]],
    *,
    invalid_key_findings: list[dict[str, Any]] | None = None,
) -> list[str]:
    actions = []
    invalid_findings = [_dict(item) for item in _list(invalid_key_findings)]
    invalid_roles = _dedupe_strings(
        str(item.get("role") or "") for item in invalid_findings
    )
    for item in required_environment:
        role = str(item.get("role") or "llm")
        envs = _list_str(item.get("accepted_api_key_envs"))
        if role in invalid_roles:
            actions.append(
                "Replace the placeholder or test API key for "
                f"`{role}` with a real provider key in one of: "
                + (", ".join(envs) if envs else "the required key env")
                + "."
            )
        if envs:
            actions.append(
                "Set one accepted API key environment variable for "
                f"`{role}`: {', '.join(envs)}."
            )
        else:
            actions.append(
                f"Set the required API key environment variable for `{role}`."
            )
    if required_environment:
        actions.append(
            "Do not write API keys into manifests, README files, tests, reports, "
            "or committed project files."
        )
        actions.append(
            "Re-run the LLM repair smoke suite after the environment variables "
            "are visible to the current shell."
        )
    return actions


def _llm_invalid_api_key_findings(
    audit: dict[str, Any],
    *,
    min_length: int,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in _list(audit.get("roles")):
        role = _dict(item)
        if not bool(role.get("enabled", False)):
            continue
        if not bool(role.get("api_key_present", False)):
            continue
        source_env = str(role.get("api_key_source") or role.get("api_key_env") or "")
        key = os.environ.get(source_env, "") if source_env else ""
        reason = _placeholder_api_key_reason(key, min_length=min_length)
        if not reason:
            continue
        findings.append(
            {
                "role": str(role.get("role") or ""),
                "reason": reason,
                "api_key_env": source_env,
                "checked_api_key_envs": _list_str(
                    role.get("checked_api_key_envs")
                ),
                "api_key_fingerprint": str(
                    role.get("api_key_fingerprint") or ""
                ),
                "api_key_length": _int(role.get("api_key_length", 0)),
                "provider": str(role.get("provider") or ""),
                "model": str(role.get("model") or ""),
            }
        )
    return findings


def _placeholder_api_key_reason(api_key: str, *, min_length: int) -> str:
    stripped = str(api_key or "").strip()
    if not stripped:
        return ""
    lowered = stripped.lower()
    exact_placeholders = {
        "fake-key",
        "fake-secret-value",
        "fake-deepseek-key",
        "placeholder",
        "dummy",
        "test-key",
        "your_api_key",
        "your-api-key",
        "your_deepseek_api_key",
        "your-deepseek-api-key",
    }
    if lowered in exact_placeholders:
        return "placeholder_api_key_value"
    if lowered.startswith(("fake-", "dummy-", "temporary-", "test-")):
        return "placeholder_api_key_value"
    if lowered.endswith("-for-audit-only"):
        return "placeholder_api_key_value"
    if len(stripped) < max(1, min_length):
        return "api_key_too_short"
    return ""


def _llm_role_env_name(role: str, kind: str) -> str:
    names = {
        "patch_generation": {
            "provider": "CIA_LLM_PROVIDER",
            "model": "CIA_LLM_MODEL",
            "base_url": "CIA_LLM_BASE_URL",
        },
        "judge": {
            "provider": "CIA_JUDGE_PROVIDER",
            "model": "CIA_JUDGE_MODEL",
            "base_url": "CIA_JUDGE_BASE_URL",
        },
        "localization": {
            "provider": "CIA_LOCALIZATION_LLM_PROVIDER",
            "model": "CIA_LOCALIZATION_LLM_MODEL",
            "base_url": "CIA_LOCALIZATION_LLM_BASE_URL",
        },
    }
    return names.get(role, {}).get(kind, "")


def _llm_preflight_blocked_metrics(
    *,
    options: dict[str, Any],
    audit: dict[str, Any],
    missing_roles: list[str],
    invalid_key_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    roles = {
        str(_dict(item).get("role") or ""): _dict(item)
        for item in _list(audit.get("roles"))
    }
    patch_audit = roles.get("patch_generation", {})
    judge_audit = roles.get("judge", {})
    invalid_findings = [_dict(item) for item in _list(invalid_key_findings)]
    invalid_roles = _dedupe_strings(
        str(item.get("role") or "") for item in invalid_findings
    )
    invalid_env_by_role = {
        str(item.get("role") or ""): str(item.get("api_key_env") or "")
        for item in invalid_findings
    }
    patch_missing = "patch_generation" in missing_roles
    judge_missing = "judge" in missing_roles
    patch_invalid = "patch_generation" in invalid_roles
    judge_invalid = "judge" in invalid_roles
    patch_blocked = patch_missing or patch_invalid
    judge_blocked = judge_missing or judge_invalid
    patch_mode = str(options.get("repository_patch_generation_mode") or "")
    patch_judge_mode = str(options.get("patch_judge_mode") or "")
    blocker = (
        "llm_config_missing_api_key"
        if missing_roles
        else "llm_config_invalid_api_key"
    )
    preflight_reason = (
        "missing_enabled_llm_api_key"
        if missing_roles
        else "invalid_enabled_llm_api_key"
    )
    patch_reason = (
        "missing_llm_api_key"
        if patch_missing
        else "invalid_llm_api_key"
        if patch_invalid
        else "llm_configuration_ready"
    )
    judge_reason = (
        f"missing_api_key:{judge_audit.get('api_key_env') or 'CIA_JUDGE_API_KEY'}"
        if judge_missing and patch_judge_mode == "llm"
        else f"invalid_api_key:{invalid_env_by_role.get('judge') or judge_audit.get('api_key_env') or 'CIA_JUDGE_API_KEY'}"
        if judge_invalid and patch_judge_mode == "llm"
        else ""
    )
    required_environment = _llm_preflight_required_environment(
        audit,
        missing_roles=_dedupe_strings([*missing_roles, *invalid_roles]),
    )
    next_actions = _llm_preflight_next_actions(
        required_environment,
        invalid_key_findings=invalid_findings,
    )
    next_action = next_actions[-1] if next_actions else ""
    return {
        "status": "llm_config_blocked",
        "blocker": blocker,
        "next_action": next_action,
        "analysis_next_action": next_action,
        "agent_answers_blocker": blocker,
        "agent_answers_next_action": next_action,
        "agent_answer_coverage_complete": True,
        "agent_answer_coverage_answered_count": 1,
        "agent_answer_coverage_required_count": 1,
        "agent_answer_coverage_missing_questions": [],
        "agent_answer_question_statuses": {
            "llm_configuration": "answered",
        },
        "repository_patch_generation_mode": patch_mode,
        "repository_llm_patch_generation_status": (
            "blocked" if patch_blocked else "ready"
        ),
        "repository_llm_patch_generation_reason": patch_reason,
        "repository_llm_patch_provider": str(patch_audit.get("provider") or ""),
        "repository_llm_patch_model": str(patch_audit.get("model") or ""),
        "repository_llm_patch_api_key_present": bool(
            patch_audit.get("api_key_present", False)
        ),
        "repository_llm_reflection_status": (
            "blocked" if patch_blocked else "ready"
        ),
        "repository_llm_reflection_reason": (
            f"missing_api_key:{patch_audit.get('api_key_env') or 'CIA_LLM_API_KEY'}"
            if patch_missing
            else f"invalid_api_key:{invalid_env_by_role.get('patch_generation') or patch_audit.get('api_key_env') or 'CIA_LLM_API_KEY'}"
            if patch_invalid
            else "llm_configuration_ready"
        ),
        "repository_llm_reflection_blocker": (
            f"missing_api_key:{patch_audit.get('api_key_env') or 'CIA_LLM_API_KEY'}"
            if patch_missing
            else f"invalid_api_key:{invalid_env_by_role.get('patch_generation') or patch_audit.get('api_key_env') or 'CIA_LLM_API_KEY'}"
            if patch_invalid
            else ""
        ),
        "repository_llm_reflection_provider": str(patch_audit.get("provider") or ""),
        "repository_llm_reflection_model": str(patch_audit.get("model") or ""),
        "repository_test_patch_judge_mode": patch_judge_mode,
        "repository_test_patch_judge_status": (
            "unavailable" if judge_blocked and patch_judge_mode == "llm" else ""
        ),
        "repository_test_patch_judge_reason": (
            judge_reason
            if judge_blocked and patch_judge_mode == "llm"
            else ""
        ),
        "repository_test_patch_judge_enabled": False,
        "repository_test_patch_judge_candidate_count": 0,
        "llm_config_preflight_status": "blocked",
        "llm_config_preflight_reason": preflight_reason,
        "llm_config_missing_enabled_api_key_role_count": len(missing_roles),
        "llm_config_missing_enabled_api_key_roles": missing_roles,
        "llm_config_invalid_enabled_api_key_role_count": len(invalid_roles),
        "llm_config_invalid_enabled_api_key_roles": invalid_roles,
        "llm_config_invalid_api_key_findings": invalid_findings,
        "llm_config_enabled_roles": [
            str(item) for item in _list(audit.get("enabled_roles"))
        ],
        "llm_config_configuration_complete": bool(
            audit.get("configuration_complete", False)
        ),
        "llm_config_required_environment": required_environment,
        "llm_config_next_actions": next_actions,
    }


def _suite_summary(
    runs: list[GitHubRepoIntelligenceSuiteRunResult],
    *,
    suite_thresholds: dict[str, float],
    manifest_run_count: int | None = None,
    slice_start_index: int = 0,
    slice_limit: int | None = None,
) -> dict[str, Any]:
    agent_status_counts: Counter[str] = Counter()
    static_status_counts: Counter[str] = Counter()
    stage_counts: Counter[str] = Counter()
    blocker_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    execution_profile_counts: Counter[str] = Counter()
    agent_shortcut_count = 0
    output_dir_defaulted_runs: list[str] = []
    agent_default_output_dir_runs: list[str] = []
    repo_input_kind_counts: Counter[str] = Counter()
    scenario_tag_counts: Counter[str] = Counter()
    scenario_coverage_blocker_counts: Counter[str] = Counter()
    artifact_status_counts: Counter[str] = Counter()
    acceptance_gate_status_counts: Counter[str] = Counter()
    agent_goal_readiness_status_counts: Counter[str] = Counter()
    objective_compliance_status_counts: Counter[str] = Counter()
    objective_compliance_section_pass_counts: Counter[str] = Counter()
    objective_compliance_section_warning_counts: Counter[str] = Counter()
    objective_compliance_failed_section_counts: Counter[str] = Counter()
    phase4_status_counts: Counter[str] = Counter()
    phase4_execution_status_counts: Counter[str] = Counter()
    fault_mode_counts: Counter[str] = Counter()
    fault_status_counts: Counter[str] = Counter()
    fault_top_source_role_counts: Counter[str] = Counter()
    fault_source_role_counts: Counter[str] = Counter()
    failure_overlay_status_counts: Counter[str] = Counter()
    patch_candidates_status_counts: Counter[str] = Counter()
    patch_generation_mode_counts: Counter[str] = Counter()
    llm_patch_generation_status_counts: Counter[str] = Counter()
    llm_patch_error_reason_counts: Counter[str] = Counter()
    llm_patch_failure_class_counts: Counter[str] = Counter()
    llm_reflection_status_counts: Counter[str] = Counter()
    llm_reflection_blocker_counts: Counter[str] = Counter()
    patch_safety_gate_status_counts: Counter[str] = Counter()
    patch_validation_status_counts: Counter[str] = Counter()
    patch_judge_mode_counts: Counter[str] = Counter()
    patch_judge_status_counts: Counter[str] = Counter()
    patch_judge_outcome_counts: Counter[str] = Counter()
    patch_validation_failure_type_counts: Counter[str] = Counter()
    reflection_initial_failure_type_counts: Counter[str] = Counter()
    reflection_failure_type_counts: Counter[str] = Counter()
    reflection_parent_failure_type_counts: Counter[str] = Counter()
    successful_reflection_parent_failure_type_counts: Counter[str] = Counter()
    repair_validation_scope_counts: Counter[str] = Counter()
    runner_counts: Counter[str] = Counter()
    test_framework_counts: Counter[str] = Counter()
    test_command_candidate_runner_counts: Counter[str] = Counter()
    runner_fallback_reason_counts: Counter[str] = Counter()
    test_result_status_counts: Counter[str] = Counter()
    test_count_source_counts: Counter[str] = Counter()
    runner_source_counts: Counter[str] = Counter()
    runner_level_counts: Counter[str] = Counter()
    environment_status_counts: Counter[str] = Counter()
    setup_doctor_status_counts: Counter[str] = Counter()
    setup_doctor_blocker_counts: Counter[str] = Counter()
    setup_doctor_check_status_counts: Counter[str] = Counter()
    setup_doctor_blocked_check_name_counts: Counter[str] = Counter()
    setup_doctor_warning_check_name_counts: Counter[str] = Counter()
    timeout_narrowing_status_counts: Counter[str] = Counter()
    timeout_narrowing_reason_counts: Counter[str] = Counter()
    timeout_narrowing_failure_category_counts: Counter[str] = Counter()
    environment_repair_plan_status_counts: Counter[str] = Counter()
    environment_repair_plan_blocker_counts: Counter[str] = Counter()
    dynamic_level_counts: Counter[str] = Counter()
    auto_verify_outcome_counts: Counter[str] = Counter()
    auto_reflect_status_counts: Counter[str] = Counter()
    auto_replan_policy_counts: Counter[str] = Counter()
    auto_goal_readiness_status_counts: Counter[str] = Counter()
    auto_action_id_counts: Counter[str] = Counter()
    auto_stop_reason_counts: Counter[str] = Counter()
    auto_stop_category_counts: Counter[str] = Counter()
    auto_stop_recovery_policy_counts: Counter[str] = Counter()
    auto_stop_external_input_kind_counts: Counter[str] = Counter()
    auto_stop_requires_user_action_count = 0
    auto_stop_requires_environment_change_count = 0
    answer_testability_status_counts: Counter[str] = Counter()
    answer_repairability_status_counts: Counter[str] = Counter()
    artifact_core_ready_runs: list[str] = []
    artifact_required_ready_runs: list[str] = []
    artifact_file_checked_runs: list[str] = []
    acceptance_gate_pass_runs: list[str] = []
    acceptance_gate_repair_decision_audit_pass_runs: list[str] = []
    agent_goal_readiness_pass_runs: list[str] = []
    agent_goal_repair_decision_audit_pass_runs: list[str] = []
    objective_compliance_pass_runs: list[str] = []
    controller_loop_complete_runs: list[str] = []
    decision_timeline_ready_runs: list[str] = []
    decision_timeline_complete_runs: list[str] = []
    decision_timeline_step_total = 0
    decision_timeline_complete_step_total = 0
    existing_report_reuse_runs: list[str] = []
    cached_report_fallback_runs: list[str] = []
    discovery_cache_reuse_runs: list[str] = []
    discovery_cache_fallback_runs: list[str] = []
    api_rate_limit_checkout_fallback_runs: list[str] = []
    phase4_ready_runs: list[str] = []
    phase4_baseline_caveat_runs: list[str] = []
    phase4_full_suite_green_runs: list[str] = []
    phase4_executed_runs: list[str] = []
    static_analysis_ready_runs: list[str] = []
    source_import_blocked_runs: list[str] = []
    source_only_static_blocker_runs: list[str] = []
    phase2_ready_runs: list[str] = []
    structure_modeled_runs: list[str] = []
    repo_graph_ready_runs: list[str] = []
    program_graph_available_runs: list[str] = []
    src_layout_detected_runs: list[str] = []
    exclude_filter_requested_runs: list[str] = []
    exclude_filter_effective_runs: list[str] = []
    timeout_narrowing_runs: list[str] = []
    timeout_narrowing_executed_runs: list[str] = []
    fault_localization_application_candidate_runs: list[str] = []
    fault_localization_no_application_candidate_runs: list[str] = []
    fault_localization_non_application_top_ranked_runs: list[str] = []
    fault_localization_non_application_topk_only_runs: list[str] = []
    fault_localization_application_candidate_total = 0
    planned_test_command_runs: list[str] = []
    planned_test_runner_fallback_runs: list[str] = []
    test_execution_result_runs: list[str] = []
    test_counted_runs: list[str] = []
    planned_test_failure_context_line_total = 0
    repository_test_count_total = 0
    repository_test_passed_total = 0
    repository_test_failed_total = 0
    repository_test_error_total = 0
    repository_test_skipped_total = 0
    dynamic_traceback_frame_total = 0
    fault_localization_matched_failed_test_total = 0
    fault_localization_unmatched_failed_test_total = 0
    fault_localization_traceback_frame_total = 0
    fault_localization_matched_traceback_frame_total = 0
    fault_localization_unmatched_traceback_frame_total = 0
    environment_diagnosed_runs: list[str] = []
    setup_doctor_diagnosed_runs: list[str] = []
    environment_repair_plan_ready_runs: list[str] = []
    environment_repair_plan_install_command_runs: list[str] = []
    setup_doctor_check_total = 0
    setup_doctor_passed_check_total = 0
    setup_doctor_warning_check_total = 0
    setup_doctor_blocked_check_total = 0
    setup_doctor_skipped_check_total = 0
    recommended_install_command_runs: list[str] = []
    test_executable_runs: list[str] = []
    patch_repair_attemptable_runs: list[str] = []
    failure_overlay_ready_runs: list[str] = []
    patch_candidates_ready_runs: list[str] = []
    patch_validation_ready_runs: list[str] = []
    patch_validation_success_total = 0
    rule_patch_candidate_total = 0
    llm_patch_candidate_total = 0
    llm_patch_request_total = 0
    llm_patch_success_total = 0
    llm_patch_failure_total = 0
    llm_patch_total_tokens = 0
    llm_patch_estimated_total_tokens = 0
    llm_patch_latency_ms_total = 0
    llm_patch_estimated_cost_usd_total = 0.0
    llm_patch_cost_available_runs: list[str] = []
    patch_safety_gate_blocked_total = 0
    patch_validation_input_candidate_total = 0
    patch_validation_candidate_total = 0
    patch_validation_safety_blocked_total = 0
    patch_judge_candidate_total = 0
    reflection_candidate_total = 0
    successful_reflection_total = 0
    regression_reflection_candidate_total = 0
    successful_regression_reflection_total = 0
    repository_repair_ready_runs: list[str] = []
    auto_loop_complete_runs: list[str] = []
    auto_action_loop_complete_runs: list[str] = []
    auto_loop_progress_total = 0
    auto_loop_no_progress_total = 0
    auto_action_loop_required_total = 0
    auto_action_loop_complete_total = 0
    auto_action_loop_incomplete_total = 0
    auto_patch_validation_reached_total = 0
    auto_repair_ready_action_total = 0
    auto_repair_goal_reached_runs: list[str] = []
    auto_reflection_action_total = 0
    auto_reflection_candidate_action_total = 0
    auto_successful_reflection_action_total = 0
    auto_reflection_goal_reached_runs: list[str] = []
    answer_coverage_complete_runs: list[str] = []
    answer_coverage_incomplete_runs: list[str] = []
    blocked_agent_answer_complete_runs: list[str] = []
    blocked_agent_answer_incomplete_runs: list[str] = []
    scenario_coverage_blocked_runs: list[str] = []
    answer_missing_question_counts: Counter[str] = Counter()
    answer_question_answered_counts: Counter[str] = Counter()
    answer_question_missing_counts: Counter[str] = Counter()
    answer_coverage_answered_total = 0
    answer_coverage_required_total = 0

    for run in runs:
        metrics = run.metrics
        if not metrics:
            continue
        _count(agent_status_counts, metrics.get("status"))
        _count(static_status_counts, metrics.get("static_intelligence_status"))
        _count(stage_counts, metrics.get("analysis_stage"))
        _count(blocker_counts, metrics.get("blocker"))
        _count(action_counts, metrics.get("controller_action_id"))
        _count(execution_profile_counts, metrics.get("execution_profile"))
        if bool(metrics.get("agent_shortcut", False)):
            agent_shortcut_count += 1
        if bool(metrics.get("output_dir_defaulted", False)):
            output_dir_defaulted_runs.append(run.name)
            if bool(metrics.get("agent_mode", False)):
                agent_default_output_dir_runs.append(run.name)
        _count(repo_input_kind_counts, metrics.get("repo_input_kind"))
        scenario_tag_counts.update(
            str(item) for item in _list(metrics.get("scenario_tags")) if str(item)
        )
        scenario_coverage_blocker = _scenario_coverage_blocker(run, metrics)
        if scenario_coverage_blocker:
            scenario_coverage_blocked_runs.append(run.name)
            _count(scenario_coverage_blocker_counts, scenario_coverage_blocker)
        _count(artifact_status_counts, metrics.get("artifact_inventory_status"))
        _count(acceptance_gate_status_counts, metrics.get("acceptance_gate_status"))
        _count(
            agent_goal_readiness_status_counts,
            metrics.get("agent_goal_readiness_status"),
        )
        _count(
            objective_compliance_status_counts,
            metrics.get("objective_compliance_status"),
        )
        for section_id, status in _dict(
            metrics.get("objective_compliance_section_statuses")
        ).items():
            if not str(section_id):
                continue
            if str(status) == "pass":
                objective_compliance_section_pass_counts.update([str(section_id)])
            else:
                objective_compliance_section_warning_counts.update([str(section_id)])
        objective_compliance_failed_section_counts.update(
            str(item)
            for item in _list(metrics.get("objective_compliance_failed_sections"))
            if str(item)
        )
        _count(phase4_status_counts, metrics.get("phase4_search_evaluation_status"))
        _count(
            phase4_execution_status_counts,
            metrics.get("phase4_search_evaluation_execution_status"),
        )
        _count(fault_mode_counts, metrics.get("fault_localization_mode"))
        _count(fault_status_counts, metrics.get("fault_localization_status"))
        _count(
            fault_top_source_role_counts,
            metrics.get("fault_localization_top_source_role"),
        )
        fault_source_role_counts.update(
            {
                str(key): _int(value)
                for key, value in _dict(
                    metrics.get("fault_localization_source_role_counts")
                ).items()
                if str(key)
            }
        )
        _count(
            failure_overlay_status_counts,
            metrics.get("repository_test_failure_overlay_status"),
        )
        _count(
            patch_candidates_status_counts,
            metrics.get("repository_test_patch_candidates_status"),
        )
        _count(
            patch_generation_mode_counts,
            metrics.get("repository_patch_generation_mode"),
        )
        _count(
            llm_patch_generation_status_counts,
            metrics.get("repository_llm_patch_generation_status"),
        )
        llm_patch_request_total += _int(
            metrics.get("repository_llm_patch_request_count", 0)
        )
        llm_patch_success_total += _int(
            metrics.get("repository_llm_patch_success_count", 0)
        )
        llm_patch_failure_total += _int(
            metrics.get("repository_llm_patch_failure_count", 0)
        )
        llm_patch_total_tokens += _int(
            metrics.get("repository_llm_patch_total_tokens", 0)
        )
        llm_patch_estimated_total_tokens += _int(
            metrics.get("repository_llm_patch_estimated_total_tokens", 0)
        )
        llm_patch_latency_ms_total += _int(
            metrics.get("repository_llm_patch_latency_ms_total", 0)
        )
        if bool(metrics.get("repository_llm_patch_cost_available", False)):
            llm_patch_cost_available_runs.append(run.name)
            llm_patch_estimated_cost_usd_total += _float(
                metrics.get("repository_llm_patch_estimated_cost_usd", 0.0)
            )
        llm_patch_error_reason_counts.update(
            {
                str(key): _int(value)
                for key, value in _dict(
                    metrics.get("repository_llm_patch_error_reason_counts")
                ).items()
                if str(key)
            }
        )
        _count(
            llm_patch_failure_class_counts,
            metrics.get("repository_llm_patch_provider_failure_class"),
        )
        _count(
            llm_reflection_status_counts,
            metrics.get("repository_llm_reflection_status"),
        )
        _count(
            llm_reflection_blocker_counts,
            metrics.get("repository_llm_reflection_blocker"),
        )
        _count(
            patch_safety_gate_status_counts,
            metrics.get("repository_patch_safety_gate_status"),
        )
        _count(
            patch_validation_status_counts,
            metrics.get("repository_test_patch_validation_status"),
        )
        _count(
            patch_judge_mode_counts,
            metrics.get("repository_test_patch_judge_mode"),
        )
        _count(
            patch_judge_status_counts,
            metrics.get("repository_test_patch_judge_status"),
        )
        patch_validation_failure_type_counts.update(
            {
                str(key): _int(value)
                for key, value in _dict(
                    metrics.get("repository_test_patch_validation_failure_type_counts")
                ).items()
                if str(key)
            }
        )
        reflection_initial_failure_type_counts.update(
            {
                str(key): _int(value)
                for key, value in _dict(
                    metrics.get("reflection_initial_failure_type_counts")
                ).items()
                if str(key)
            }
        )
        reflection_failure_type_counts.update(
            {
                str(key): _int(value)
                for key, value in _dict(
                    metrics.get("reflection_failure_type_counts")
                ).items()
                if str(key)
            }
        )
        reflection_parent_failure_type_counts.update(
            {
                str(key): _int(value)
                for key, value in _dict(
                    metrics.get("reflection_parent_failure_type_counts")
                ).items()
                if str(key)
            }
        )
        successful_reflection_parent_failure_type_counts.update(
            {
                str(key): _int(value)
                for key, value in _dict(
                    metrics.get("successful_reflection_parent_failure_type_counts")
                ).items()
                if str(key)
            }
        )
        _count(
            repair_validation_scope_counts,
            metrics.get("repository_test_repair_validation_scope"),
        )
        _count(runner_counts, metrics.get("planned_repository_test_runner"))
        planned_test_failure_context_line_total += _int(
            metrics.get("planned_repository_test_failure_context_line_count", 0)
        )
        dynamic_traceback_frame_total += _int(
            metrics.get("repository_test_dynamic_traceback_frames", 0)
        )
        fault_localization_matched_failed_test_total += _int(
            metrics.get("fault_localization_matched_failed_test_count", 0)
        )
        fault_localization_unmatched_failed_test_total += _int(
            metrics.get("fault_localization_unmatched_failed_test_count", 0)
        )
        fault_localization_traceback_frame_total += _int(
            metrics.get("fault_localization_traceback_frame_count", 0)
        )
        fault_localization_matched_traceback_frame_total += _int(
            metrics.get("fault_localization_matched_traceback_frame_count", 0)
        )
        fault_localization_unmatched_traceback_frame_total += _int(
            metrics.get("fault_localization_unmatched_traceback_frame_count", 0)
        )
        if bool(metrics.get("planned_repository_test_runner_fallback_used", False)):
            planned_test_runner_fallback_runs.append(run.name)
            _count(
                runner_fallback_reason_counts,
                metrics.get("planned_repository_test_runner_fallback_reason"),
            )
        _count(
            test_result_status_counts,
            metrics.get("planned_repository_test_result_status"),
        )
        _count(
            test_count_source_counts,
            metrics.get("planned_repository_test_result_test_count_source"),
        )
        if str(metrics.get("planned_repository_test_result_status") or ""):
            test_execution_result_runs.append(run.name)
        if _int(metrics.get("planned_repository_test_result_test_count", 0)) > 0:
            test_counted_runs.append(run.name)
        repository_test_count_total += _int(
            metrics.get("planned_repository_test_result_test_count", 0)
        )
        repository_test_passed_total += _int(
            metrics.get("planned_repository_test_result_passed", 0)
        )
        repository_test_failed_total += _int(
            metrics.get("planned_repository_test_result_failed", 0)
        )
        repository_test_error_total += _int(
            metrics.get("planned_repository_test_result_errors", 0)
        )
        repository_test_skipped_total += _int(
            metrics.get("planned_repository_test_result_skipped", 0)
        )
        test_framework_counts.update(
            str(item)
            for item in _list(metrics.get("repository_test_framework_signals"))
            if str(item)
        )
        test_command_candidate_runner_counts.update(
            {
                str(key): _int(value)
                for key, value in _dict(
                    metrics.get("repository_test_command_candidate_runner_counts")
                ).items()
                if str(key)
            }
        )
        _count(runner_source_counts, metrics.get("planned_repository_test_source"))
        _count(runner_level_counts, metrics.get("planned_repository_test_level"))
        _count(
            environment_status_counts,
            metrics.get("repository_test_environment_status"),
        )
        _count(
            setup_doctor_status_counts,
            metrics.get("repository_test_setup_doctor_status"),
        )
        _count(
            setup_doctor_blocker_counts,
            metrics.get("repository_test_setup_doctor_blocker"),
        )
        setup_doctor_check_status_counts.update(
            {
                str(key): _int(value)
                for key, value in _dict(
                    metrics.get("repository_test_setup_doctor_check_status_counts")
                ).items()
                if str(key)
            }
        )
        setup_doctor_blocked_check_name_counts.update(
            str(item)
            for item in _list(
                metrics.get("repository_test_setup_doctor_blocked_check_names")
            )
            if str(item)
        )
        setup_doctor_warning_check_name_counts.update(
            str(item)
            for item in _list(
                metrics.get("repository_test_setup_doctor_warning_check_names")
            )
            if str(item)
        )
        _count(
            timeout_narrowing_status_counts,
            metrics.get("repository_test_timeout_narrowing_status"),
        )
        _count(
            timeout_narrowing_reason_counts,
            metrics.get("repository_test_timeout_narrowing_reason"),
        )
        _count(
            timeout_narrowing_failure_category_counts,
            metrics.get(
                "repository_test_timeout_narrowing_selected_failure_category"
            ),
        )
        if str(metrics.get("repository_test_timeout_narrowing_status") or ""):
            timeout_narrowing_runs.append(run.name)
        if bool(metrics.get("repository_test_timeout_narrowing_executed", False)):
            timeout_narrowing_executed_runs.append(run.name)
        _count(
            environment_repair_plan_status_counts,
            metrics.get("repository_test_environment_repair_plan_status"),
        )
        _count(
            environment_repair_plan_blocker_counts,
            metrics.get("repository_test_environment_repair_plan_blocker"),
        )
        if bool(metrics.get("repository_test_environment_repair_plan_ready")):
            environment_repair_plan_ready_runs.append(run.name)
        if bool(
            metrics.get(
                "repository_test_environment_repair_plan_has_install_command"
            )
        ):
            environment_repair_plan_install_command_runs.append(run.name)
        setup_doctor_check_total += _int(
            metrics.get("repository_test_setup_doctor_check_count", 0)
        )
        setup_doctor_passed_check_total += _int(
            metrics.get("repository_test_setup_doctor_passed_check_count", 0)
        )
        setup_doctor_warning_check_total += _int(
            metrics.get("repository_test_setup_doctor_warning_check_count", 0)
        )
        setup_doctor_blocked_check_total += _int(
            metrics.get("repository_test_setup_doctor_blocked_check_count", 0)
        )
        setup_doctor_skipped_check_total += _int(
            metrics.get("repository_test_setup_doctor_skipped_check_count", 0)
        )
        _count(dynamic_level_counts, metrics.get("repository_test_dynamic_evidence_level"))
        _count(
            answer_testability_status_counts,
            metrics.get("agent_answer_testability_status"),
        )
        _count(
            answer_repairability_status_counts,
            metrics.get("agent_answer_repairability_status"),
        )
        auto_verify_outcome_counts.update(
            {
                str(key): _int(value)
                for key, value in _dict(
                    metrics.get("agent_auto_verify_outcome_counts")
                ).items()
            }
        )
        auto_reflect_status_counts.update(
            {
                str(key): _int(value)
                for key, value in _dict(
                    metrics.get("agent_auto_reflect_status_counts")
                ).items()
            }
        )
        auto_replan_policy_counts.update(
            {
                str(key): _int(value)
                for key, value in _dict(
                    metrics.get("agent_auto_replan_policy_counts")
                ).items()
            }
        )
        auto_goal_readiness_status_counts.update(
            {
                str(key): _int(value)
                for key, value in _dict(
                    metrics.get("agent_auto_goal_readiness_status_counts")
                ).items()
            }
        )
        auto_action_id_counts.update(
            {
                str(key): _int(value)
                for key, value in _dict(
                    metrics.get("agent_auto_action_id_counts")
                ).items()
            }
        )
        _count(auto_stop_reason_counts, metrics.get("agent_auto_stop_reason"))
        _count(auto_stop_category_counts, metrics.get("agent_auto_stop_category"))
        _count(
            auto_stop_recovery_policy_counts,
            metrics.get("agent_auto_stop_recovery_policy"),
        )
        _count(
            auto_stop_external_input_kind_counts,
            metrics.get("agent_auto_stop_external_input_kind"),
        )
        if bool(metrics.get("agent_auto_stop_requires_user_action", False)):
            auto_stop_requires_user_action_count += 1
        if bool(metrics.get("agent_auto_stop_requires_environment_change", False)):
            auto_stop_requires_environment_change_count += 1
        auto_loop_progress_total += _int(
            metrics.get("agent_auto_loop_progress_count", 0)
        )
        auto_loop_no_progress_total += _int(
            metrics.get("agent_auto_loop_no_progress_count", 0)
        )
        auto_action_loop_required_total += _int(
            metrics.get("agent_auto_action_loop_required_count", 0)
        )
        auto_action_loop_complete_total += _int(
            metrics.get("agent_auto_action_loop_complete_count", 0)
        )
        auto_action_loop_incomplete_total += _int(
            metrics.get("agent_auto_action_loop_incomplete_count", 0)
        )
        auto_patch_validation_reached_total += _int(
            metrics.get("agent_auto_patch_validation_reached_action_count", 0)
        )
        auto_repair_ready_action_total += _int(
            metrics.get("agent_auto_repair_ready_action_count", 0)
        )
        auto_reflection_action_total += _int(
            metrics.get("agent_auto_reflection_action_count", 0)
        )
        auto_reflection_candidate_action_total += _int(
            metrics.get("agent_auto_reflection_candidate_action_count", 0)
        )
        auto_successful_reflection_action_total += _int(
            metrics.get("agent_auto_successful_reflection_action_count", 0)
        )
        if bool(metrics.get("artifact_inventory_core_ready")):
            artifact_core_ready_runs.append(run.name)
        if bool(metrics.get("artifact_inventory_required_ready")):
            artifact_required_ready_runs.append(run.name)
        if bool(metrics.get("artifact_inventory_file_check_enabled")):
            artifact_file_checked_runs.append(run.name)
        if bool(metrics.get("acceptance_gate_passed")):
            acceptance_gate_pass_runs.append(run.name)
        if bool(metrics.get("acceptance_gate_repair_decision_audit_passed")):
            acceptance_gate_repair_decision_audit_pass_runs.append(run.name)
        if bool(metrics.get("agent_goal_readiness_passed")):
            agent_goal_readiness_pass_runs.append(run.name)
        if bool(metrics.get("agent_goal_repair_decision_audit_passed")):
            agent_goal_repair_decision_audit_pass_runs.append(run.name)
        if bool(metrics.get("objective_compliance_passed")):
            objective_compliance_pass_runs.append(run.name)
        if bool(metrics.get("agent_controller_loop_complete")):
            controller_loop_complete_runs.append(run.name)
        if str(metrics.get("agent_decision_timeline_status") or "") == "pass":
            decision_timeline_ready_runs.append(run.name)
        if bool(metrics.get("agent_decision_timeline_complete")):
            decision_timeline_complete_runs.append(run.name)
        decision_timeline_step_total += _int(
            metrics.get("agent_decision_timeline_step_count", 0)
        )
        decision_timeline_complete_step_total += _int(
            metrics.get("agent_decision_timeline_complete_step_count", 0)
        )
        if bool(metrics.get("existing_report_reuse")):
            existing_report_reuse_runs.append(run.name)
        if bool(metrics.get("cached_report_fallback")):
            cached_report_fallback_runs.append(run.name)
        if bool(metrics.get("discovery_cache_reuse")):
            discovery_cache_reuse_runs.append(run.name)
        if bool(metrics.get("discovery_cache_fallback")):
            discovery_cache_fallback_runs.append(run.name)
        if bool(metrics.get("discovery_api_rate_limit_checkout_fallback")):
            api_rate_limit_checkout_fallback_runs.append(run.name)
        if bool(metrics.get("phase4_ready_for_evaluation")):
            phase4_ready_runs.append(run.name)
        if bool(metrics.get("phase4_baseline_regression_caveat")):
            phase4_baseline_caveat_runs.append(run.name)
        if bool(metrics.get("phase4_full_suite_green_claim_allowed")):
            phase4_full_suite_green_runs.append(run.name)
        if bool(metrics.get("phase4_search_evaluation_executed")):
            phase4_executed_runs.append(run.name)
        if metrics.get("static_intelligence_status") == "analysis_ready":
            static_analysis_ready_runs.append(run.name)
        if metrics.get("analysis_stage") == "source_import_blocked":
            source_import_blocked_runs.append(run.name)
        if (
            metrics.get("static_intelligence_level") == "source_only"
            and metrics.get("blocker") == "no_static_candidates"
            and bool(metrics.get("repository_structure_modeled"))
            and bool(metrics.get("repo_graph_ready"))
        ):
            source_only_static_blocker_runs.append(run.name)
        if metrics.get("analysis_stage") == "phase2_static_graph_fault_localization":
            phase2_ready_runs.append(run.name)
        if bool(metrics.get("repository_structure_modeled")):
            structure_modeled_runs.append(run.name)
        if bool(metrics.get("repo_graph_ready")):
            repo_graph_ready_runs.append(run.name)
        if bool(metrics.get("repo_graph_program_graph_available")):
            program_graph_available_runs.append(run.name)
        if _int(metrics.get("repository_structure_src_layout_package_count", 0)) > 0:
            src_layout_detected_runs.append(run.name)
        if _int(metrics.get("agent_invocation_exclude_count", 0)) > 0:
            exclude_filter_requested_runs.append(run.name)
        if bool(metrics.get("agent_invocation_exclude_reduced_selected_sources")):
            exclude_filter_effective_runs.append(run.name)
        fault_ranking_count = _int(metrics.get("fault_localization_ranking_count", 0))
        fault_application_count = _int(
            metrics.get("fault_localization_application_candidate_count", 0)
        )
        fault_localization_application_candidate_total += fault_application_count
        if fault_ranking_count > 0 and fault_application_count > 0:
            fault_localization_application_candidate_runs.append(run.name)
        if fault_ranking_count > 0 and fault_application_count == 0:
            fault_localization_no_application_candidate_runs.append(run.name)
        if bool(metrics.get("fault_localization_non_application_top_ranked", False)):
            fault_localization_non_application_top_ranked_runs.append(run.name)
        if bool(metrics.get("fault_localization_non_application_topk_only", False)):
            fault_localization_non_application_topk_only_runs.append(run.name)
        if str(metrics.get("planned_repository_test_command") or ""):
            planned_test_command_runs.append(run.name)
        if bool(metrics.get("repository_test_environment_diagnosed")):
            environment_diagnosed_runs.append(run.name)
        if bool(metrics.get("repository_test_setup_doctor_diagnosed")):
            setup_doctor_diagnosed_runs.append(run.name)
        if str(metrics.get("recommended_install_command") or ""):
            recommended_install_command_runs.append(run.name)
        if bool(metrics.get("can_attempt_dynamic_tests")):
            test_executable_runs.append(run.name)
        if bool(metrics.get("can_attempt_patch_repair")):
            patch_repair_attemptable_runs.append(run.name)
        if bool(metrics.get("repository_test_repair_ready")):
            repository_repair_ready_runs.append(run.name)
        if bool(metrics.get("agent_auto_complete_loop_recorded")):
            auto_loop_complete_runs.append(run.name)
        if bool(metrics.get("agent_auto_action_loop_complete")):
            auto_action_loop_complete_runs.append(run.name)
        if bool(metrics.get("agent_auto_repair_goal_reached")):
            auto_repair_goal_reached_runs.append(run.name)
        if bool(metrics.get("agent_auto_reflection_goal_reached")):
            auto_reflection_goal_reached_runs.append(run.name)
        if bool(metrics.get("agent_answer_coverage_complete")):
            answer_coverage_complete_runs.append(run.name)
        else:
            answer_coverage_incomplete_runs.append(run.name)
        blocker = str(metrics.get("blocker") or "").strip()
        answer_blocker = str(metrics.get("agent_answers_blocker") or "").strip()
        answer_next_action = str(
            metrics.get("agent_answers_next_action") or ""
        ).strip()
        if blocker:
            if (
                bool(metrics.get("agent_answer_coverage_complete"))
                and answer_blocker
                and answer_blocker.lower() != "none"
                and answer_blocker == blocker
                and answer_next_action
            ):
                blocked_agent_answer_complete_runs.append(run.name)
            else:
                blocked_agent_answer_incomplete_runs.append(run.name)
        answer_coverage_answered_total += _int(
            metrics.get("agent_answer_coverage_answered_count", 0)
        )
        answer_coverage_required_total += _int(
            metrics.get("agent_answer_coverage_required_count", 0)
        )
        answer_missing_question_counts.update(
            str(item)
            for item in _list(metrics.get("agent_answer_coverage_missing_questions"))
            if str(item)
        )
        for question_id, status in _dict(
            metrics.get("agent_answer_question_statuses")
        ).items():
            question = str(question_id)
            if not question:
                continue
            if str(status) == "answered":
                answer_question_answered_counts.update([question])
            else:
                answer_question_missing_counts.update([question])
        if str(metrics.get("repository_test_failure_overlay_status") or "") == "pass":
            failure_overlay_ready_runs.append(run.name)
        if str(metrics.get("repository_test_patch_candidates_status") or "") == "pass":
            patch_candidates_ready_runs.append(run.name)
        if str(metrics.get("repository_test_patch_validation_status") or "") == "pass":
            patch_validation_ready_runs.append(run.name)
        patch_validation_success_total += _int(
            metrics.get("repository_test_patch_validation_success_count", 0)
        )
        rule_patch_candidate_total += _int(
            metrics.get("repository_patch_generator_rule_count", 0)
        )
        llm_patch_candidate_total += _first_int(
            metrics.get("repository_patch_generator_llm_candidate_count"),
            metrics.get("repository_patch_generator_llm_count"),
        )
        patch_safety_gate_blocked_total += _int(
            metrics.get("repository_patch_safety_gate_blocked_count", 0)
        )
        patch_validation_input_candidate_total += _int(
            metrics.get(
                "repository_test_patch_validation_input_candidate_count",
                0,
            )
        )
        patch_validation_candidate_total += _int(
            metrics.get("repository_test_patch_validation_candidate_count", 0)
        )
        patch_validation_safety_blocked_total += _int(
            metrics.get(
                "repository_test_patch_validation_safety_blocked_candidate_count",
                0,
            )
        )
        patch_judge_candidate_total += _int(
            metrics.get("repository_test_patch_judge_candidate_count", 0)
        )
        patch_judge_outcome_counts.update(
            {
                str(key): _int(value)
                for key, value in _dict(
                    metrics.get("repository_test_patch_judge_outcome_counts")
                ).items()
            }
        )
        reflection_candidate_total += _int(
            metrics.get("repository_test_patch_validation_reflection_candidate_count", 0)
        )
        successful_reflection_total += _int(
            metrics.get(
                "repository_test_patch_validation_successful_reflection_count",
                0,
            )
        )
        regression_reflection_candidate_total += _int(
            metrics.get(
                "repository_test_patch_validation_regression_reflection_candidate_count",
                0,
            )
        )
        successful_regression_reflection_total += _int(
            metrics.get(
                "repository_test_patch_validation_successful_regression_reflection_count",
                0,
            )
        )

    run_elapsed_values = [_int(run.elapsed_ms) for run in runs]
    run_elapsed_ms_total = sum(run_elapsed_values)
    run_elapsed_ms_average = (
        round(run_elapsed_ms_total / len(run_elapsed_values), 2)
        if run_elapsed_values
        else 0.0
    )
    run_elapsed_ms_max = max(run_elapsed_values, default=0)
    metric_check_count = sum(len(run.metric_checks) for run in runs)
    metric_check_failed_count = sum(
        1
        for run in runs
        for check in run.metric_checks
        if not bool(check.get("passed"))
    )
    expectation_check_count = sum(len(run.expectation_checks) for run in runs)
    expectation_check_failed_count = sum(
        1
        for run in runs
        for check in run.expectation_checks
        if not bool(check.get("passed"))
    )
    summary = {
        "run_count": len(runs),
        "manifest_run_count": (
            _int(manifest_run_count)
            if manifest_run_count is not None
            else len(runs)
        ),
        "suite_slice_applied": bool(
            _int(slice_start_index) > 0 or slice_limit is not None
        ),
        "suite_slice_start_index": _normalized_start_index(slice_start_index),
        "suite_slice_limit": (
            _int(slice_limit) if slice_limit is not None else None
        ),
        "suite_slice_run_count": len(runs),
        "agent_passed_count": sum(1 for run in runs if run.passed),
        "agent_failed_count": sum(1 for run in runs if not run.passed),
        "command_failed_count": sum(1 for run in runs if run.error),
        "suite_run_elapsed_ms_total": run_elapsed_ms_total,
        "suite_run_elapsed_ms_average": run_elapsed_ms_average,
        "suite_run_elapsed_ms_max": run_elapsed_ms_max,
        "existing_report_reuse_count": len(existing_report_reuse_runs),
        "existing_report_reuse_runs": existing_report_reuse_runs,
        "cached_report_fallback_count": len(cached_report_fallback_runs),
        "cached_report_fallback_runs": cached_report_fallback_runs,
        "discovery_cache_reuse_count": len(discovery_cache_reuse_runs),
        "discovery_cache_reuse_runs": discovery_cache_reuse_runs,
        "discovery_cache_fallback_count": len(discovery_cache_fallback_runs),
        "discovery_cache_fallback_runs": discovery_cache_fallback_runs,
        "api_rate_limit_checkout_fallback_count": len(
            api_rate_limit_checkout_fallback_runs
        ),
        "api_rate_limit_checkout_fallback_runs": (
            api_rate_limit_checkout_fallback_runs
        ),
        "expectation_passed_count": sum(1 for run in runs if run.expectation_passed),
        "expectation_failed_count": sum(
            1 for run in runs if not run.expectation_passed
        ),
        "expectation_check_count": expectation_check_count,
        "expectation_check_failed_count": expectation_check_failed_count,
        "metric_check_count": metric_check_count,
        "metric_check_failed_count": metric_check_failed_count,
        "agent_status_counts": dict(sorted(agent_status_counts.items())),
        "static_intelligence_status_counts": dict(
            sorted(static_status_counts.items())
        ),
        "analysis_stage_counts": dict(sorted(stage_counts.items())),
        "blocker_counts": dict(sorted(blocker_counts.items())),
        "controller_action_counts": dict(sorted(action_counts.items())),
        "execution_profile_counts": dict(sorted(execution_profile_counts.items())),
        "agent_shortcut_count": agent_shortcut_count,
        "output_dir_defaulted_count": len(output_dir_defaulted_runs),
        "output_dir_defaulted_runs": output_dir_defaulted_runs,
        "agent_default_output_dir_count": len(agent_default_output_dir_runs),
        "agent_default_output_dir_runs": agent_default_output_dir_runs,
        "repo_input_kind_counts": dict(sorted(repo_input_kind_counts.items())),
        "repo_input_kind_count": len(repo_input_kind_counts),
        "scenario_tag_counts": dict(sorted(scenario_tag_counts.items())),
        "scenario_tag_kind_count": len(scenario_tag_counts),
        "scenario_coverage_blocked_count": len(scenario_coverage_blocked_runs),
        "scenario_coverage_blocked_runs": scenario_coverage_blocked_runs,
        "scenario_coverage_blocker_counts": dict(
            sorted(scenario_coverage_blocker_counts.items())
        ),
        "scenario_coverage_blocker_kind_count": len(
            scenario_coverage_blocker_counts
        ),
        "artifact_inventory_status_counts": dict(
            sorted(artifact_status_counts.items())
        ),
        "acceptance_gate_status_counts": dict(
            sorted(acceptance_gate_status_counts.items())
        ),
        "acceptance_gate_pass_count": len(acceptance_gate_pass_runs),
        "acceptance_gate_pass_runs": acceptance_gate_pass_runs,
        "acceptance_gate_repair_decision_audit_pass_count": len(
            acceptance_gate_repair_decision_audit_pass_runs
        ),
        "acceptance_gate_repair_decision_audit_pass_runs": (
            acceptance_gate_repair_decision_audit_pass_runs
        ),
        "agent_goal_readiness_status_counts": dict(
            sorted(agent_goal_readiness_status_counts.items())
        ),
        "agent_goal_readiness_pass_count": len(agent_goal_readiness_pass_runs),
        "agent_goal_readiness_pass_runs": agent_goal_readiness_pass_runs,
        "agent_goal_repair_decision_audit_pass_count": len(
            agent_goal_repair_decision_audit_pass_runs
        ),
        "agent_goal_repair_decision_audit_pass_runs": (
            agent_goal_repair_decision_audit_pass_runs
        ),
        "objective_compliance_status_counts": dict(
            sorted(objective_compliance_status_counts.items())
        ),
        "objective_compliance_pass_count": len(objective_compliance_pass_runs),
        "objective_compliance_pass_runs": objective_compliance_pass_runs,
        "objective_compliance_section_pass_counts": dict(
            sorted(objective_compliance_section_pass_counts.items())
        ),
        "objective_compliance_section_warning_counts": dict(
            sorted(objective_compliance_section_warning_counts.items())
        ),
        "objective_compliance_failed_section_counts": dict(
            sorted(objective_compliance_failed_section_counts.items())
        ),
        "objective_compliance_failed_section_kind_count": len(
            objective_compliance_failed_section_counts
        ),
        "phase4_search_evaluation_status_counts": dict(
            sorted(phase4_status_counts.items())
        ),
        "phase4_search_evaluation_execution_status_counts": dict(
            sorted(phase4_execution_status_counts.items())
        ),
        "phase4_ready_count": len(phase4_ready_runs),
        "phase4_ready_runs": phase4_ready_runs,
        "phase4_executed_count": len(phase4_executed_runs),
        "phase4_executed_runs": phase4_executed_runs,
        "phase4_baseline_regression_caveat_count": len(
            phase4_baseline_caveat_runs
        ),
        "phase4_baseline_regression_caveat_runs": phase4_baseline_caveat_runs,
        "phase4_full_suite_green_claim_allowed_count": len(
            phase4_full_suite_green_runs
        ),
        "phase4_full_suite_green_claim_allowed_runs": phase4_full_suite_green_runs,
        "artifact_core_ready_count": len(artifact_core_ready_runs),
        "artifact_core_ready_runs": artifact_core_ready_runs,
        "artifact_required_ready_count": len(artifact_required_ready_runs),
        "artifact_required_ready_runs": artifact_required_ready_runs,
        "artifact_file_checked_count": len(artifact_file_checked_runs),
        "artifact_file_checked_runs": artifact_file_checked_runs,
        "agent_controller_loop_complete_count": len(controller_loop_complete_runs),
        "agent_controller_loop_complete_runs": controller_loop_complete_runs,
        "agent_decision_timeline_ready_count": len(decision_timeline_ready_runs),
        "agent_decision_timeline_ready_runs": decision_timeline_ready_runs,
        "agent_decision_timeline_complete_count": len(
            decision_timeline_complete_runs
        ),
        "agent_decision_timeline_complete_runs": decision_timeline_complete_runs,
        "agent_decision_timeline_step_count": decision_timeline_step_total,
        "agent_decision_timeline_complete_step_count": (
            decision_timeline_complete_step_total
        ),
        "agent_answer_coverage_complete_count": len(
            answer_coverage_complete_runs
        ),
        "agent_answer_coverage_complete_runs": answer_coverage_complete_runs,
        "agent_answer_coverage_incomplete_count": len(
            answer_coverage_incomplete_runs
        ),
        "agent_answer_coverage_incomplete_runs": answer_coverage_incomplete_runs,
        "blocked_agent_answer_complete_count": len(
            blocked_agent_answer_complete_runs
        ),
        "blocked_agent_answer_complete_runs": blocked_agent_answer_complete_runs,
        "blocked_agent_answer_incomplete_count": len(
            blocked_agent_answer_incomplete_runs
        ),
        "blocked_agent_answer_incomplete_runs": (
            blocked_agent_answer_incomplete_runs
        ),
        "agent_answer_coverage_answered_total": answer_coverage_answered_total,
        "agent_answer_coverage_required_total": answer_coverage_required_total,
        "agent_answer_missing_question_counts": dict(
            sorted(answer_missing_question_counts.items())
        ),
        "agent_answer_question_answered_counts": dict(
            sorted(answer_question_answered_counts.items())
        ),
        "agent_answer_question_answered_kind_count": len(
            answer_question_answered_counts
        ),
        "agent_answer_question_missing_counts": dict(
            sorted(answer_question_missing_counts.items())
        ),
        "agent_answer_question_missing_kind_count": len(
            answer_question_missing_counts
        ),
        "agent_answer_testability_status_counts": dict(
            sorted(answer_testability_status_counts.items())
        ),
        "agent_answer_testability_status_kind_count": len(
            answer_testability_status_counts
        ),
        "agent_answer_repairability_status_counts": dict(
            sorted(answer_repairability_status_counts.items())
        ),
        "agent_answer_repairability_status_kind_count": len(
            answer_repairability_status_counts
        ),
        "static_analysis_ready_count": len(static_analysis_ready_runs),
        "static_analysis_ready_runs": static_analysis_ready_runs,
        "source_import_blocked_count": len(source_import_blocked_runs),
        "source_import_blocked_runs": source_import_blocked_runs,
        "source_only_static_blocker_count": len(source_only_static_blocker_runs),
        "source_only_static_blocker_runs": source_only_static_blocker_runs,
        "phase2_static_graph_fault_localization_count": len(phase2_ready_runs),
        "phase2_static_graph_fault_localization_runs": phase2_ready_runs,
        "repository_structure_modeled_count": len(structure_modeled_runs),
        "repository_structure_modeled_runs": structure_modeled_runs,
        "repo_graph_ready_count": len(repo_graph_ready_runs),
        "repo_graph_ready_runs": repo_graph_ready_runs,
        "program_graph_available_count": len(program_graph_available_runs),
        "program_graph_available_runs": program_graph_available_runs,
        "repository_structure_src_layout_detected_count": len(
            src_layout_detected_runs
        ),
        "repository_structure_src_layout_detected_runs": src_layout_detected_runs,
        "exclude_filter_requested_count": len(exclude_filter_requested_runs),
        "exclude_filter_requested_runs": exclude_filter_requested_runs,
        "exclude_filter_effective_count": len(exclude_filter_effective_runs),
        "exclude_filter_effective_runs": exclude_filter_effective_runs,
        "fault_localization_mode_counts": dict(sorted(fault_mode_counts.items())),
        "fault_localization_status_counts": dict(sorted(fault_status_counts.items())),
        "fault_localization_top_source_role_counts": dict(
            sorted(fault_top_source_role_counts.items())
        ),
        "fault_localization_source_role_counts": dict(
            sorted(fault_source_role_counts.items())
        ),
        "fault_localization_application_candidate_count": (
            fault_localization_application_candidate_total
        ),
        "fault_localization_application_candidate_run_count": len(
            fault_localization_application_candidate_runs
        ),
        "fault_localization_application_candidate_runs": (
            fault_localization_application_candidate_runs
        ),
        "fault_localization_no_application_candidate_run_count": len(
            fault_localization_no_application_candidate_runs
        ),
        "fault_localization_no_application_candidate_runs": (
            fault_localization_no_application_candidate_runs
        ),
        "fault_localization_non_application_top_ranked_count": len(
            fault_localization_non_application_top_ranked_runs
        ),
        "fault_localization_non_application_top_ranked_runs": (
            fault_localization_non_application_top_ranked_runs
        ),
        "fault_localization_non_application_topk_only_count": len(
            fault_localization_non_application_topk_only_runs
        ),
        "fault_localization_non_application_topk_only_runs": (
            fault_localization_non_application_topk_only_runs
        ),
        "repository_test_failure_overlay_status_counts": dict(
            sorted(failure_overlay_status_counts.items())
        ),
        "repository_test_failure_overlay_ready_count": len(
            failure_overlay_ready_runs
        ),
        "repository_test_failure_overlay_ready_runs": failure_overlay_ready_runs,
        "repository_test_patch_candidates_status_counts": dict(
            sorted(patch_candidates_status_counts.items())
        ),
        "repository_test_patch_candidates_ready_count": len(
            patch_candidates_ready_runs
        ),
        "repository_test_patch_candidates_ready_runs": patch_candidates_ready_runs,
        "repository_patch_generation_mode_counts": dict(
            sorted(patch_generation_mode_counts.items())
        ),
        "repository_llm_patch_generation_status_counts": dict(
            sorted(llm_patch_generation_status_counts.items())
        ),
        "repository_llm_patch_request_count": llm_patch_request_total,
        "repository_llm_patch_success_count": llm_patch_success_total,
        "repository_llm_patch_failure_count": llm_patch_failure_total,
        "repository_llm_patch_total_tokens": llm_patch_total_tokens,
        "repository_llm_patch_estimated_total_tokens": (
            llm_patch_estimated_total_tokens
        ),
        "repository_llm_patch_latency_ms_total": llm_patch_latency_ms_total,
        "repository_llm_patch_latency_ms_average": (
            round(llm_patch_latency_ms_total / llm_patch_request_total, 2)
            if llm_patch_request_total
            else 0.0
        ),
        "repository_llm_patch_estimated_cost_usd_total": (
            round(llm_patch_estimated_cost_usd_total, 8)
        ),
        "repository_llm_patch_cost_available_count": len(
            llm_patch_cost_available_runs
        ),
        "repository_llm_patch_cost_available_runs": llm_patch_cost_available_runs,
        "repository_llm_patch_error_reason_counts": dict(
            sorted(llm_patch_error_reason_counts.items())
        ),
        "repository_llm_patch_error_reason_kind_count": len(
            llm_patch_error_reason_counts
        ),
        "repository_llm_patch_provider_failure_class_counts": dict(
            sorted(llm_patch_failure_class_counts.items())
        ),
        "repository_llm_patch_provider_failure_class_kind_count": len(
            llm_patch_failure_class_counts
        ),
        "repository_llm_reflection_status_counts": dict(
            sorted(llm_reflection_status_counts.items())
        ),
        "repository_llm_reflection_blocker_counts": dict(
            sorted(llm_reflection_blocker_counts.items())
        ),
        "repository_patch_safety_gate_status_counts": dict(
            sorted(patch_safety_gate_status_counts.items())
        ),
        "repository_patch_generator_rule_candidate_count": (
            rule_patch_candidate_total
        ),
        "repository_patch_generator_llm_candidate_count": llm_patch_candidate_total,
        "repository_patch_safety_gate_blocked_count": (
            patch_safety_gate_blocked_total
        ),
        "repository_test_patch_validation_status_counts": dict(
            sorted(patch_validation_status_counts.items())
        ),
        "repository_test_patch_judge_mode_counts": dict(
            sorted(patch_judge_mode_counts.items())
        ),
        "repository_test_patch_judge_status_counts": dict(
            sorted(patch_judge_status_counts.items())
        ),
        "repository_test_patch_judge_candidate_count": patch_judge_candidate_total,
        "repository_test_patch_judge_outcome_counts": dict(
            sorted(patch_judge_outcome_counts.items())
        ),
        "repository_test_patch_judge_accept_success_count": _int(
            patch_judge_outcome_counts.get("accept_success", 0)
        ),
        "repository_test_patch_judge_reject_failure_count": _int(
            patch_judge_outcome_counts.get("reject_failure", 0)
        ),
        "repository_test_patch_judge_accept_failure_count": _int(
            patch_judge_outcome_counts.get("accept_failure", 0)
        ),
        "repository_test_patch_judge_reject_success_count": _int(
            patch_judge_outcome_counts.get("reject_success", 0)
        ),
        "repository_test_patch_validation_failure_type_counts": dict(
            sorted(patch_validation_failure_type_counts.items())
        ),
        "repository_test_patch_validation_failure_type_kind_count": len(
            patch_validation_failure_type_counts
        ),
        "repository_test_patch_validation_ready_count": len(
            patch_validation_ready_runs
        ),
        "repository_test_patch_validation_ready_runs": patch_validation_ready_runs,
        "repository_test_patch_validation_success_count": (
            patch_validation_success_total
        ),
        "repository_test_patch_validation_input_candidate_count": (
            patch_validation_input_candidate_total
        ),
        "repository_test_patch_validation_candidate_count": (
            patch_validation_candidate_total
        ),
        "repository_test_patch_validation_safety_blocked_candidate_count": (
            patch_validation_safety_blocked_total
        ),
        "repository_test_repair_ready_count": len(repository_repair_ready_runs),
        "repository_test_repair_ready_runs": repository_repair_ready_runs,
        "repository_test_repair_validation_scope_counts": dict(
            sorted(repair_validation_scope_counts.items())
        ),
        "repository_test_patch_validation_reflection_candidate_count": (
            reflection_candidate_total
        ),
        "repository_test_patch_validation_successful_reflection_count": (
            successful_reflection_total
        ),
        "repository_test_patch_validation_regression_reflection_candidate_count": (
            regression_reflection_candidate_total
        ),
        "repository_test_patch_validation_successful_regression_reflection_count": (
            successful_regression_reflection_total
        ),
        "reflection_initial_failure_type_counts": dict(
            sorted(reflection_initial_failure_type_counts.items())
        ),
        "reflection_initial_failure_type_kind_count": len(
            reflection_initial_failure_type_counts
        ),
        "reflection_failure_type_counts": dict(
            sorted(reflection_failure_type_counts.items())
        ),
        "reflection_failure_type_kind_count": len(reflection_failure_type_counts),
        "reflection_parent_failure_type_counts": dict(
            sorted(reflection_parent_failure_type_counts.items())
        ),
        "reflection_parent_failure_type_kind_count": len(
            reflection_parent_failure_type_counts
        ),
        "successful_reflection_parent_failure_type_counts": dict(
            sorted(successful_reflection_parent_failure_type_counts.items())
        ),
        "successful_reflection_parent_failure_type_kind_count": len(
            successful_reflection_parent_failure_type_counts
        ),
        "planned_repository_test_runner_counts": dict(sorted(runner_counts.items())),
        "planned_repository_test_runner_kind_count": len(runner_counts),
        "planned_repository_test_runner_fallback_count": len(
            planned_test_runner_fallback_runs
        ),
        "planned_repository_test_runner_fallback_runs": (
            planned_test_runner_fallback_runs
        ),
        "planned_repository_test_runner_fallback_reason_counts": dict(
            sorted(runner_fallback_reason_counts.items())
        ),
        "planned_repository_test_runner_fallback_reason_kind_count": len(
            runner_fallback_reason_counts
        ),
        "planned_repository_test_result_status_counts": dict(
            sorted(test_result_status_counts.items())
        ),
        "planned_repository_test_result_status_kind_count": len(
            test_result_status_counts
        ),
        "repository_test_count_source_counts": dict(
            sorted(test_count_source_counts.items())
        ),
        "repository_test_count_source_kind_count": len(test_count_source_counts),
        "repository_test_framework_counts": dict(
            sorted(test_framework_counts.items())
        ),
        "repository_test_framework_kind_count": len(test_framework_counts),
        "repository_test_command_candidate_runner_counts": dict(
            sorted(test_command_candidate_runner_counts.items())
        ),
        "repository_test_command_candidate_runner_kind_count": len(
            test_command_candidate_runner_counts
        ),
        "planned_repository_test_command_count": len(planned_test_command_runs),
        "planned_repository_test_command_runs": planned_test_command_runs,
        "repository_test_execution_result_count": len(test_execution_result_runs),
        "repository_test_execution_result_runs": test_execution_result_runs,
        "repository_test_counted_run_count": len(test_counted_runs),
        "repository_test_counted_runs": test_counted_runs,
        "repository_test_count": repository_test_count_total,
        "repository_test_passed_count": repository_test_passed_total,
        "repository_test_failed_count": repository_test_failed_total,
        "repository_test_error_count": repository_test_error_total,
        "repository_test_skipped_count": repository_test_skipped_total,
        "planned_repository_test_failure_context_line_count": (
            planned_test_failure_context_line_total
        ),
        "repository_test_dynamic_traceback_frame_count": (
            dynamic_traceback_frame_total
        ),
        "fault_localization_matched_failed_test_count": (
            fault_localization_matched_failed_test_total
        ),
        "fault_localization_unmatched_failed_test_count": (
            fault_localization_unmatched_failed_test_total
        ),
        "fault_localization_traceback_frame_count": (
            fault_localization_traceback_frame_total
        ),
        "fault_localization_matched_traceback_frame_count": (
            fault_localization_matched_traceback_frame_total
        ),
        "fault_localization_unmatched_traceback_frame_count": (
            fault_localization_unmatched_traceback_frame_total
        ),
        "planned_repository_test_source_counts": dict(
            sorted(runner_source_counts.items())
        ),
        "planned_repository_test_level_counts": dict(sorted(runner_level_counts.items())),
        "repository_test_environment_status_counts": dict(
            sorted(environment_status_counts.items())
        ),
        "repository_test_environment_diagnosed_count": len(
            environment_diagnosed_runs
        ),
        "repository_test_environment_diagnosed_runs": environment_diagnosed_runs,
        "repository_test_setup_doctor_status_counts": dict(
            sorted(setup_doctor_status_counts.items())
        ),
        "repository_test_setup_doctor_blocker_counts": dict(
            sorted(setup_doctor_blocker_counts.items())
        ),
        "repository_test_setup_doctor_check_status_counts": dict(
            sorted(setup_doctor_check_status_counts.items())
        ),
        "repository_test_setup_doctor_check_status_kind_count": len(
            setup_doctor_check_status_counts
        ),
        "repository_test_setup_doctor_blocked_check_name_counts": dict(
            sorted(setup_doctor_blocked_check_name_counts.items())
        ),
        "repository_test_setup_doctor_warning_check_name_counts": dict(
            sorted(setup_doctor_warning_check_name_counts.items())
        ),
        "repository_test_timeout_narrowing_status_counts": dict(
            sorted(timeout_narrowing_status_counts.items())
        ),
        "repository_test_timeout_narrowing_status_kind_count": len(
            timeout_narrowing_status_counts
        ),
        "repository_test_timeout_narrowing_reason_counts": dict(
            sorted(timeout_narrowing_reason_counts.items())
        ),
        "repository_test_timeout_narrowing_reason_kind_count": len(
            timeout_narrowing_reason_counts
        ),
        "repository_test_timeout_narrowing_selected_failure_category_counts": (
            dict(sorted(timeout_narrowing_failure_category_counts.items()))
        ),
        "repository_test_timeout_narrowing_selected_failure_category_kind_count": len(
            timeout_narrowing_failure_category_counts
        ),
        "repository_test_timeout_narrowing_count": len(timeout_narrowing_runs),
        "repository_test_timeout_narrowing_runs": timeout_narrowing_runs,
        "repository_test_timeout_narrowing_executed_count": len(
            timeout_narrowing_executed_runs
        ),
        "repository_test_timeout_narrowing_executed_runs": (
            timeout_narrowing_executed_runs
        ),
        "repository_test_timeout_narrowing_attempt_count": sum(
            _int(run.metrics.get("repository_test_timeout_narrowing_attempt_count", 0))
            for run in runs
        ),
        "repository_test_environment_repair_plan_status_counts": dict(
            sorted(environment_repair_plan_status_counts.items())
        ),
        "repository_test_environment_repair_plan_blocker_counts": dict(
            sorted(environment_repair_plan_blocker_counts.items())
        ),
        "repository_test_environment_repair_plan_ready_count": len(
            environment_repair_plan_ready_runs
        ),
        "repository_test_environment_repair_plan_ready_runs": (
            environment_repair_plan_ready_runs
        ),
        "repository_test_environment_repair_plan_install_command_count": len(
            environment_repair_plan_install_command_runs
        ),
        "repository_test_environment_repair_plan_install_command_runs": (
            environment_repair_plan_install_command_runs
        ),
        "repository_test_setup_doctor_check_count": setup_doctor_check_total,
        "repository_test_setup_doctor_passed_check_count": (
            setup_doctor_passed_check_total
        ),
        "repository_test_setup_doctor_warning_check_count": (
            setup_doctor_warning_check_total
        ),
        "repository_test_setup_doctor_blocked_check_count": (
            setup_doctor_blocked_check_total
        ),
        "repository_test_setup_doctor_skipped_check_count": (
            setup_doctor_skipped_check_total
        ),
        "repository_test_setup_doctor_diagnosed_count": len(
            setup_doctor_diagnosed_runs
        ),
        "repository_test_setup_doctor_diagnosed_runs": setup_doctor_diagnosed_runs,
        "repository_test_recommended_install_command_count": len(
            recommended_install_command_runs
        ),
        "repository_test_recommended_install_command_runs": (
            recommended_install_command_runs
        ),
        "repository_test_dynamic_evidence_level_counts": dict(
            sorted(dynamic_level_counts.items())
        ),
        "repository_test_dynamic_evidence_level_kind_count": len(
            dynamic_level_counts
        ),
        "agent_auto_loop_progress_count": auto_loop_progress_total,
        "agent_auto_loop_no_progress_count": auto_loop_no_progress_total,
        "agent_auto_complete_loop_count": len(auto_loop_complete_runs),
        "agent_auto_complete_loop_runs": auto_loop_complete_runs,
        "agent_auto_action_loop_required_count": auto_action_loop_required_total,
        "agent_auto_action_loop_complete_count": auto_action_loop_complete_total,
        "agent_auto_action_loop_incomplete_count": auto_action_loop_incomplete_total,
        "agent_auto_action_loop_complete_run_count": len(
            auto_action_loop_complete_runs
        ),
        "agent_auto_action_loop_complete_runs": auto_action_loop_complete_runs,
        "agent_auto_verify_outcome_counts": dict(
            sorted(auto_verify_outcome_counts.items())
        ),
        "agent_auto_reflect_status_counts": dict(
            sorted(auto_reflect_status_counts.items())
        ),
        "agent_auto_replan_policy_counts": dict(
            sorted(auto_replan_policy_counts.items())
        ),
        "agent_auto_goal_readiness_status_counts": dict(
            sorted(auto_goal_readiness_status_counts.items())
        ),
        "agent_auto_goal_readiness_passed_action_count": sum(
            _int(run.metrics.get("agent_auto_goal_readiness_passed_action_count", 0))
            for run in runs
        ),
        "agent_auto_action_id_counts": dict(sorted(auto_action_id_counts.items())),
        "agent_auto_stop_reason_counts": dict(sorted(auto_stop_reason_counts.items())),
        "agent_auto_stop_category_counts": dict(
            sorted(auto_stop_category_counts.items())
        ),
        "agent_auto_stop_recovery_policy_counts": dict(
            sorted(auto_stop_recovery_policy_counts.items())
        ),
        "agent_auto_stop_external_input_kind_counts": dict(
            sorted(auto_stop_external_input_kind_counts.items())
        ),
        "agent_auto_stop_requires_user_action_count": (
            auto_stop_requires_user_action_count
        ),
        "agent_auto_stop_requires_environment_change_count": (
            auto_stop_requires_environment_change_count
        ),
        "agent_auto_patch_validation_reached_action_count": (
            auto_patch_validation_reached_total
        ),
        "agent_auto_repair_ready_action_count": auto_repair_ready_action_total,
        "agent_auto_repair_goal_reached_count": len(auto_repair_goal_reached_runs),
        "agent_auto_repair_goal_reached_runs": auto_repair_goal_reached_runs,
        "agent_auto_reflection_action_count": auto_reflection_action_total,
        "agent_auto_reflection_candidate_action_count": (
            auto_reflection_candidate_action_total
        ),
        "agent_auto_successful_reflection_action_count": (
            auto_successful_reflection_action_total
        ),
        "agent_auto_reflection_goal_reached_count": len(
            auto_reflection_goal_reached_runs
        ),
        "agent_auto_reflection_goal_reached_runs": auto_reflection_goal_reached_runs,
        "test_executable_run_count": len(test_executable_runs),
        "test_executable_runs": test_executable_runs,
        "patch_repair_attemptable_count": len(patch_repair_attemptable_runs),
        "patch_repair_attemptable_runs": patch_repair_attemptable_runs,
        "patch_repair_ready_count": len(repository_repair_ready_runs),
        "patch_repair_ready_runs": repository_repair_ready_runs,
    }
    summary.update(_counter_metric_fields("repo_input_kind", repo_input_kind_counts))
    summary.update(_counter_metric_fields("scenario_tag", scenario_tag_counts))
    summary.update(
        _counter_metric_fields(
            "agent_answer_question_answered",
            answer_question_answered_counts,
        )
    )
    summary.update(
        _counter_metric_fields(
            "agent_answer_question_missing",
            answer_question_missing_counts,
        )
    )
    summary.update(
        _counter_metric_fields("agent_auto_action_id", auto_action_id_counts)
    )
    summary.update(
        _counter_metric_fields(
            "repository_test_patch_validation_failure_type",
            patch_validation_failure_type_counts,
        )
    )
    summary.update(
        _counter_metric_fields(
            "reflection_initial_failure_type",
            reflection_initial_failure_type_counts,
        )
    )
    summary.update(
        _counter_metric_fields(
            "reflection_failure_type",
            reflection_failure_type_counts,
        )
    )
    summary.update(
        _counter_metric_fields(
            "reflection_parent_failure_type",
            reflection_parent_failure_type_counts,
        )
    )
    summary.update(
        _counter_metric_fields(
            "successful_reflection_parent_failure_type",
            successful_reflection_parent_failure_type_counts,
        )
    )
    summary.update(
        _counter_metric_fields(
            "agent_auto_stop_recovery_policy",
            auto_stop_recovery_policy_counts,
        )
    )
    summary.update(
        _counter_metric_fields(
            "agent_auto_stop_external_input_kind",
            auto_stop_external_input_kind_counts,
        )
    )
    summary.update(
        _counter_metric_fields(
            "repository_test_environment_repair_plan_status",
            environment_repair_plan_status_counts,
        )
    )
    summary.update(
        _counter_metric_fields(
            "repository_test_environment_repair_plan_blocker",
            environment_repair_plan_blocker_counts,
        )
    )
    summary.update(
        _counter_metric_fields(
            "repository_test_setup_doctor_blocker",
            setup_doctor_blocker_counts,
        )
    )
    summary.update(
        _counter_metric_fields(
            "repository_test_timeout_narrowing_status",
            timeout_narrowing_status_counts,
        )
    )
    summary.update(
        _counter_metric_fields(
            "repository_test_timeout_narrowing_reason",
            timeout_narrowing_reason_counts,
        )
    )
    summary.update(
        _counter_metric_fields(
            "repository_test_timeout_narrowing_selected_failure_category",
            timeout_narrowing_failure_category_counts,
        )
    )
    summary.update(
        _counter_metric_fields(
            "scenario_coverage_blocker",
            scenario_coverage_blocker_counts,
        )
    )
    _refresh_suite_threshold_checks(summary, suite_thresholds)
    return summary


def _json_artifact_payload(path_value: Any) -> dict[str, Any]:
    path = Path(str(path_value or ""))
    if not str(path):
        return {}
    try:
        if not path.is_file():
            return {}
        return _dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}


def _scenario_coverage_blocker(
    run: GitHubRepoIntelligenceSuiteRunResult,
    metrics: dict[str, Any],
) -> str:
    if not _list(metrics.get("scenario_tags")):
        return ""
    if run.error:
        return "command_error"
    blocker = str(metrics.get("blocker") or "")
    if blocker.startswith("github_fetch:"):
        return blocker
    return ""


def _agent_auto_action_loop_complete(action: dict[str, Any]) -> bool:
    required_fields = [
        "loop_observe_stage",
        "loop_plan_action",
        "action_id",
        "loop_verify_outcome",
        "loop_reflect_status",
        "loop_replan_policy",
        "loop_replan_next_action",
    ]
    return all(str(action.get(field) or "") for field in required_fields)


def _agent_auto_action_reached_patch_validation(action: dict[str, Any]) -> bool:
    patch_status = str(action.get("after_patch_validation_status") or "")
    return bool(
        str(action.get("after_stage") or "") == "phase3_patch_validation"
        or (patch_status and patch_status != "skipped")
    )


def _agent_auto_action_generated_reflection_candidate(action: dict[str, Any]) -> bool:
    return _int(action.get("after_reflection_candidate_count", 0)) > 0


def _agent_auto_action_successful_reflection(action: dict[str, Any]) -> bool:
    return _int(action.get("after_successful_reflection_count", 0)) > 0


def _suite_metric_snapshot(summary: dict[str, Any]) -> dict[str, Any]:
    readiness = _dict(summary.get("analysis_readiness"))
    invocation = _dict(summary.get("agent_invocation"))
    controller = _dict(summary.get("agent_controller"))
    selected_action = _dict(controller.get("selected_action"))
    controller_loop = [str(item) for item in _list(controller.get("control_loop"))]
    decision_trace_phases = [
        str(_dict(item).get("phase") or "")
        for item in _list(controller.get("decision_trace"))
    ]
    structure = _dict(summary.get("repository_structure"))
    test_structure = _dict(structure.get("test_structure"))
    package_structure = _dict(structure.get("package_structure"))
    repo_graph = _dict(summary.get("repo_graph"))
    program_graph = _dict(repo_graph.get("program_graph"))
    artifact_inventory = _dict(summary.get("artifact_inventory"))
    acceptance_gate = _dict(summary.get("acceptance_gate"))
    acceptance_checks = {
        str(_dict(item).get("name") or ""): _dict(item)
        for item in _list(acceptance_gate.get("checks"))
    }
    agent_goal_readiness = _dict(summary.get("agent_goal_readiness"))
    goal_criteria = {
        str(_dict(item).get("name") or ""): _dict(item)
        for item in _list(agent_goal_readiness.get("criteria"))
    }
    phase4 = _dict(summary.get("phase4_search_evaluation"))
    phase4_execution = _dict(phase4.get("execution"))
    phase4_search_budget = _dict(phase4.get("search_budget"))
    artifact_groups = _dict(artifact_inventory.get("groups"))
    all_artifact_items = [
        _dict(item)
        for rows in artifact_groups.values()
        for item in _list(rows)
    ]
    core_items = [
        item
        for item in all_artifact_items
        if str(item.get("required_when") or "") == "always"
    ]
    required_items = [
        item for item in all_artifact_items if bool(item.get("required_now"))
    ]
    fault = _dict(summary.get("fault_localization"))
    fault_rankings = [_dict(item) for item in _list(fault.get("rankings"))]
    fault_top_ranking = fault_rankings[0] if fault_rankings else {}
    answers = _dict(summary.get("agent_answers"))
    answer_coverage = _dict(answers.get("answer_coverage"))
    answer_question_statuses = {
        str(item.get("id") or ""): (
            "answered" if bool(item.get("answered", False)) else "missing"
        )
        for item in [_dict(row) for row in _list(answer_coverage.get("questions"))]
        if str(item.get("id") or "")
    }
    answer_testability = _dict(answers.get("testability"))
    answer_repairability = _dict(answers.get("repairability"))
    environment_repair_plan = _dict(
        summary.get("repository_test_environment_repair_plan")
    )
    decision_timeline = _dict(summary.get("agent_decision_timeline"))
    final_report = _dict(summary.get("final_report"))
    objective_compliance = _dict(final_report.get("objective_compliance"))
    objective_sections = [
        _dict(item) for item in _list(objective_compliance.get("sections"))
    ]
    objective_section_statuses = {
        str(section.get("id") or ""): (
            "pass" if bool(section.get("passed", False)) else "warning"
        )
        for section in objective_sections
        if str(section.get("id") or "")
    }
    reflection = _dict(summary.get("reflection_summary"))
    auto_loop = _dict(summary.get("agent_auto_loop_audit"))
    auto_stop_state = _dict(summary.get("agent_auto_stop_state"))
    auto_actions = [_dict(item) for item in _list(summary.get("agent_auto_actions"))]
    auto_action_loop_complete_count = sum(
        1 for action in auto_actions if _agent_auto_action_loop_complete(action)
    )
    auto_action_loop_required_count = len(auto_actions)
    auto_action_id_counts = Counter(
        str(action.get("action_id") or "") for action in auto_actions
    )
    auto_patch_validation_reached_count = sum(
        1 for action in auto_actions if _agent_auto_action_reached_patch_validation(action)
    )
    auto_repair_ready_action_count = sum(
        1 for action in auto_actions if bool(action.get("after_repair_ready", False))
    )
    auto_reflection_action_count = sum(
        1
        for action in auto_actions
        if str(action.get("action_id") or "") == "run_patch_reflection_loop"
    )
    auto_reflection_candidate_action_count = sum(
        1
        for action in auto_actions
        if _agent_auto_action_generated_reflection_candidate(action)
    )
    auto_successful_reflection_action_count = sum(
        1 for action in auto_actions if _agent_auto_action_successful_reflection(action)
    )
    auto_stop_reason = str(summary.get("agent_auto_stop_reason") or "")
    patch_generator_counts = _dict(summary.get("repository_patch_generator_counts"))
    llm_patch_audit = _dict(
        summary.get("repository_llm_patch_config_audit")
    ) or _dict(summary.get("repository_llm_patch_generation_audit"))
    llm_patch_generation_audit = _dict(
        summary.get("repository_llm_patch_generation_audit")
    )
    llm_patch_telemetry = _dict(
        summary.get("repository_llm_patch_generation_telemetry")
    ) or _dict(llm_patch_generation_audit.get("telemetry"))
    llm_patch_cost_estimate = _dict(llm_patch_telemetry.get("cost_estimate"))
    llm_reflection_audit = _dict(
        summary.get("repository_llm_reflection_config_audit")
    ) or _dict(summary.get("repository_llm_reflection_audit"))
    patch_variant_filter = _dict(
        summary.get("repository_patch_candidate_variant_filter")
    )
    test_environment = _json_artifact_payload(
        summary.get("repository_test_environment_json")
    )
    test_execution_result = _json_artifact_payload(
        summary.get("repository_test_execution_result_json")
    )
    pytest_configuration = _dict(test_environment.get("pytest_configuration"))
    ci_install_command_candidates = _list(
        summary.get("repository_test_ci_install_command_candidates")
    )
    if not ci_install_command_candidates:
        ci_install_command_candidates = _list(
            test_environment.get("ci_install_command_candidates")
        )
    ci_test_command_candidates = _list(
        summary.get("repository_test_ci_test_command_candidates")
    )
    if not ci_test_command_candidates:
        ci_test_command_candidates = _list(
            test_environment.get("ci_test_command_candidates")
        )
    pytest_config_source_count = _int(
        summary.get("repository_test_pytest_config_source_count", 0)
    )
    if pytest_config_source_count <= 0:
        pytest_config_source_count = _int(
            test_environment.get("pytest_config_source_count", 0)
        )
    if pytest_config_source_count <= 0:
        pytest_config_source_count = _int(pytest_configuration.get("source_count", 0))
    environment_variable_names = _list(
        summary.get("planned_repository_test_environment_variable_names")
    )
    if not environment_variable_names:
        environment_variable_names = _list(
            pytest_configuration.get("environment_variable_names")
        )
    planned_test_result_status = str(
        summary.get("planned_repository_test_result_status")
        or readiness.get("planned_repository_test_result_status")
        or test_execution_result.get("status")
        or ""
    )
    planned_test_result_passed = _first_int(
        summary.get("planned_repository_test_result_passed"),
        readiness.get("planned_repository_test_result_passed"),
        test_execution_result.get("passed"),
    )
    planned_test_result_failed = _first_int(
        summary.get("planned_repository_test_result_failed"),
        readiness.get("planned_repository_test_result_failed"),
        test_execution_result.get("failed"),
    )
    planned_test_result_errors = _first_int(
        summary.get("planned_repository_test_result_errors"),
        readiness.get("planned_repository_test_result_errors"),
        test_execution_result.get("errors"),
    )
    planned_test_result_skipped = _first_int(
        summary.get("planned_repository_test_result_skipped"),
        readiness.get("planned_repository_test_result_skipped"),
        test_execution_result.get("skipped"),
    )
    planned_test_result_test_count = _first_int(
        summary.get("planned_repository_test_result_test_count"),
        readiness.get("planned_repository_test_result_test_count"),
        test_execution_result.get("test_count"),
        answer_testability.get("test_count"),
    )
    planned_test_result_test_count_source = str(
        summary.get("planned_repository_test_result_test_count_source")
        or readiness.get("planned_repository_test_result_test_count_source")
        or test_execution_result.get("test_count_source")
        or answer_testability.get("test_count_source")
        or ""
    )
    selected_source_count = _int(summary.get("selected_source_count", 0))
    invocation_include_count = len(_list(invocation.get("include")))
    invocation_exclude_count = len(_list(invocation.get("exclude")))
    return {
        "status": str(summary.get("status") or ""),
        "passed": bool(summary.get("passed", False)),
        "agent_shortcut": bool(invocation.get("agent_shortcut", False)),
        "agent_mode": bool(invocation.get("agent_mode", False)),
        "output_dir_defaulted": bool(invocation.get("output_dir_defaulted", False)),
        "default_output_dir": str(invocation.get("default_output_dir") or ""),
        "existing_report_reuse": False,
        "existing_report_reuse_reason": "",
        "cached_report_fallback": False,
        "cached_report_fallback_reason": "",
        "discovery_source": str(
            summary.get("discovery_source") or summary.get("source") or ""
        ),
        "discovery_cache_reuse": bool(
            summary.get("discovery_cache_reuse", False)
        ),
        "discovery_cache_reuse_reason": str(
            summary.get("discovery_cache_reuse_reason") or ""
        ),
        "discovery_cache_reuse_source": str(
            summary.get("discovery_cache_reuse_source") or ""
        ),
        "discovery_cache_preferred": bool(
            summary.get("discovery_cache_preferred", False)
        ),
        "discovery_cache_preferred_source": str(
            summary.get("discovery_cache_preferred_source") or ""
        ),
        "discovery_cache_fallback": bool(
            summary.get("discovery_cache_fallback", False)
        ),
        "discovery_cache_fallback_source": str(
            summary.get("discovery_cache_fallback_source") or ""
        ),
        "discovery_api_rate_limit_checkout_fallback": bool(
            summary.get("discovery_api_rate_limit_checkout_fallback", False)
        ),
        "discovery_api_rate_limit_status_code": summary.get(
            "discovery_api_rate_limit_status_code"
        ),
        "discovery_api_rate_limit_remaining": str(
            summary.get("discovery_api_rate_limit_remaining") or ""
        ),
        "discovery_api_rate_limit_checkout_mode": str(
            summary.get("discovery_api_rate_limit_checkout_mode") or ""
        ),
        "discovery_api_rate_limit_original_checkout_requested": bool(
            summary.get(
                "discovery_api_rate_limit_original_checkout_requested",
                False,
            )
        ),
        "static_intelligence_status": str(
            summary.get("static_intelligence_status") or ""
        ),
        "static_intelligence_level": str(
            summary.get("static_intelligence_level") or ""
        ),
        "static_intelligence_reason": str(
            summary.get("static_intelligence_reason") or ""
        ),
        "selected_source_count": selected_source_count,
        "imported_source_count": _int(summary.get("imported_source_count", 0)),
        "repository_scope_status": str(
            summary.get("repository_scope_status") or "unknown"
        ),
        "repository_discovered_python_source_count": _int(
            summary.get("repository_discovered_python_source_count", 0)
        ),
        "repository_source_root_count": len(
            _list(summary.get("repository_source_roots"))
        ),
        "repository_test_root_count": len(
            _list(summary.get("repository_test_roots"))
        ),
        "repository_compatibility_status": str(
            summary.get("repository_compatibility_status") or "unknown"
        ),
        "repository_compatibility_termination_reason": str(
            summary.get("repository_compatibility_termination_reason") or ""
        ),
        "repository_compatibility_primary_blocker": str(
            summary.get("repository_compatibility_primary_blocker") or ""
        ),
        "repository_python_compatibility_status": str(
            summary.get("repository_python_compatibility_status") or "unknown"
        ),
        "repository_dependency_access_blocker_count": len(
            _list(summary.get("repository_dependency_access_blockers"))
        ),
        "repository_install_risk": str(
            summary.get("repository_install_risk") or "unknown"
        ),
        "repository_install_auto_execution_allowed": bool(
            summary.get("repository_install_auto_execution_allowed", False)
        ),
        "repository_compatibility_artifact_present": bool(
            _json_artifact_payload(summary.get("repository_compatibility_json"))
        ),
        "selected_signal_count": _int(summary.get("selected_signal_count", 0)),
        "total_signal_count": _int(summary.get("total_signal_count", 0)),
        "agent_invocation_include_count": invocation_include_count,
        "agent_invocation_exclude_count": invocation_exclude_count,
        "agent_invocation_exclude_requested": invocation_exclude_count > 0,
        "agent_invocation_exclude_reduced_selected_sources": (
            invocation_exclude_count > 0
            and invocation_include_count > 0
            and selected_source_count < invocation_include_count
        ),
        "repository_structure_modeled": (
            _int(structure.get("analyzed_file_count", 0)) > 0
            and _int(structure.get("function_count", 0)) > 0
        ),
        "repository_structure_analyzed_file_count": _int(
            structure.get("analyzed_file_count", 0)
        ),
        "repository_structure_function_count": _int(
            structure.get("function_count", 0)
        ),
        "repository_structure_class_count": _int(structure.get("class_count", 0)),
        "repository_structure_total_loc": _int(structure.get("total_loc", 0)),
        "repository_structure_import_count": _int(structure.get("import_count", 0)),
        "repository_structure_call_site_count": _int(
            structure.get("call_site_count", 0)
        ),
        "repository_structure_parse_error_count": _int(
            structure.get("parse_error_count", 0)
        ),
        "repository_structure_package_root_count": len(
            _list(package_structure.get("package_roots"))
        ),
        "repository_structure_src_layout_package_count": len(
            _list(package_structure.get("src_layout_packages"))
        ),
        "repository_structure_recommended_target_prefix_present": bool(
            str(package_structure.get("recommended_target_prefix") or "")
        ),
        "repo_graph_ready": (
            _int(repo_graph.get("file_node_count", 0)) > 0
            and _int(repo_graph.get("function_node_count", 0)) > 0
        ),
        "repo_graph_file_node_count": _int(repo_graph.get("file_node_count", 0)),
        "repo_graph_function_node_count": _int(
            repo_graph.get("function_node_count", 0)
        ),
        "repo_graph_file_dependency_edge_count": _int(
            repo_graph.get("file_dependency_edge_count", 0)
        ),
        "repo_graph_function_call_edge_count": _int(
            repo_graph.get("function_call_edge_count", 0)
        ),
        "repo_graph_unresolved_call_site_count": _int(
            repo_graph.get("unresolved_call_site_count", 0)
        ),
        "repo_graph_program_graph_available": bool(
            program_graph.get("available", False)
        ),
        "repo_graph_program_graph_node_count": _int(
            program_graph.get("node_count", 0)
        ),
        "repo_graph_program_graph_edge_count": _int(
            program_graph.get("edge_count", 0)
        ),
        "repo_graph_program_graph_cfg_edge_count": _int(
            program_graph.get("cfg_edge_count", 0)
        ),
        "repo_graph_program_graph_module_dependency_edge_count": _int(
            program_graph.get("module_dependency_edge_count", 0)
        ),
        "repo_graph_program_graph_cross_function_data_flow_edge_count": _int(
            program_graph.get("cross_function_data_flow_edge_count", 0)
        ),
        "analysis_stage": str(readiness.get("current_stage") or ""),
        "stage_number": _int(readiness.get("stage_number", 0)),
        "next_stage": str(readiness.get("next_stage") or ""),
        "blocker": str(readiness.get("blocker") or ""),
        "analysis_next_action": str(readiness.get("next_action") or ""),
        "can_generate_static_report": bool(
            readiness.get("can_generate_static_report", False)
        ),
        "can_attempt_dynamic_tests": bool(
            readiness.get("can_attempt_dynamic_tests", False)
        ),
        "can_attempt_patch_repair": bool(
            readiness.get("can_attempt_patch_repair", False)
        ),
        "repository_test_dynamic_evidence_level": str(
            readiness.get("dynamic_evidence_level") or ""
        ),
        "repository_test_dynamic_usable_for_localization": bool(
            readiness.get("dynamic_evidence_usable_for_localization", False)
        ),
        "repository_test_dynamic_traceback_frames": _int(
            summary.get("repository_test_dynamic_traceback_frames", 0)
        ),
        "planned_repository_test_command": str(
            summary.get("planned_repository_test_command")
            or readiness.get("planned_repository_test_command")
            or ""
        ),
        "planned_repository_test_runner": str(
            summary.get("planned_repository_test_runner") or ""
        ),
        "planned_repository_test_level": str(
            summary.get("planned_repository_test_level") or ""
        ),
        "planned_repository_test_source": str(
            summary.get("planned_repository_test_source") or ""
        ),
        "planned_repository_test_failure_context_line_count": _int(
            summary.get("planned_repository_test_failure_context_line_count", 0)
        ),
        "planned_repository_test_preferred_runner": str(
            summary.get("planned_repository_test_preferred_runner") or ""
        ),
        "planned_repository_test_runner_fallback_used": bool(
            summary.get("planned_repository_test_runner_fallback_used", False)
        ),
        "planned_repository_test_runner_fallback_reason": str(
            summary.get("planned_repository_test_runner_fallback_reason") or ""
        ),
        "planned_repository_test_runner_fallback_from": str(
            summary.get("planned_repository_test_runner_fallback_from") or ""
        ),
        "planned_repository_test_runner_fallback_to": str(
            summary.get("planned_repository_test_runner_fallback_to") or ""
        ),
        "planned_repository_test_executable_now": bool(
            readiness.get("planned_repository_test_executable_now", False)
        ),
        "planned_repository_test_result_status": str(
            planned_test_result_status
        ),
        "planned_repository_test_failure_category": str(
            summary.get("planned_repository_test_failure_category")
            or test_execution_result.get("failure_category")
            or ""
        ),
        "planned_repository_test_result_executed": bool(
            summary.get("planned_repository_test_result_executed", False)
            or test_execution_result.get("executed", False)
        ),
        "planned_repository_test_result_passed": planned_test_result_passed,
        "planned_repository_test_result_failed": planned_test_result_failed,
        "planned_repository_test_result_errors": planned_test_result_errors,
        "planned_repository_test_result_skipped": planned_test_result_skipped,
        "planned_repository_test_result_test_count": (
            planned_test_result_test_count
        ),
        "planned_repository_test_result_test_count_source": (
            planned_test_result_test_count_source
        ),
        "repository_test_environment_status": str(
            summary.get("repository_test_environment_status") or ""
        ),
        "repository_test_environment_reason": str(
            summary.get("repository_test_environment_reason") or ""
        ),
        "repository_test_environment_diagnosed": bool(
            str(summary.get("repository_test_environment_status") or "")
        ),
        "repository_test_setup_doctor_status": str(
            summary.get("repository_test_setup_doctor_status") or ""
        ),
        "repository_test_setup_doctor_blocker": str(
            summary.get("repository_test_setup_doctor_blocker") or ""
        ),
        "repository_test_setup_doctor_next_action": str(
            summary.get("repository_test_setup_doctor_next_action") or ""
        ),
        "repository_test_setup_doctor_check_count": _int(
            summary.get("repository_test_setup_doctor_check_count", 0)
        ),
        "repository_test_setup_doctor_passed_check_count": _int(
            summary.get("repository_test_setup_doctor_passed_check_count", 0)
        ),
        "repository_test_setup_doctor_warning_check_count": _int(
            summary.get("repository_test_setup_doctor_warning_check_count", 0)
        ),
        "repository_test_setup_doctor_blocked_check_count": _int(
            summary.get("repository_test_setup_doctor_blocked_check_count", 0)
        ),
        "repository_test_setup_doctor_skipped_check_count": _int(
            summary.get("repository_test_setup_doctor_skipped_check_count", 0)
        ),
        "repository_test_setup_doctor_check_status_counts": _dict(
            summary.get("repository_test_setup_doctor_check_status_counts")
        ),
        "repository_test_setup_doctor_blocked_check_names": _list(
            summary.get("repository_test_setup_doctor_blocked_check_names")
        ),
        "repository_test_setup_doctor_warning_check_names": _list(
            summary.get("repository_test_setup_doctor_warning_check_names")
        ),
        "repository_test_setup_doctor_diagnosed": bool(
            str(summary.get("repository_test_setup_doctor_status") or "")
        ),
        "repository_test_timeout_narrowing_status": str(
            summary.get("repository_test_timeout_narrowing_status") or ""
        ),
        "repository_test_timeout_narrowing_reason": str(
            summary.get("repository_test_timeout_narrowing_reason") or ""
        ),
        "repository_test_timeout_narrowing_executed": bool(
            summary.get("repository_test_timeout_narrowing_executed", False)
        ),
        "repository_test_timeout_narrowing_attempt_count": _int(
            summary.get("repository_test_timeout_narrowing_attempt_count", 0)
        ),
        "repository_test_timeout_narrowing_selected_command": str(
            summary.get("repository_test_timeout_narrowing_selected_command") or ""
        ),
        "repository_test_timeout_narrowing_selected_failure_category": str(
            summary.get(
                "repository_test_timeout_narrowing_selected_failure_category"
            )
            or ""
        ),
        "repository_test_environment_repair_plan_status": str(
            environment_repair_plan.get("status")
            or summary.get("repository_test_environment_repair_plan_status")
            or ""
        ),
        "repository_test_environment_repair_plan_blocker": str(
            environment_repair_plan.get("blocker")
            or summary.get("repository_test_environment_repair_plan_blocker")
            or ""
        ),
        "repository_test_environment_repair_plan_recommended_install_command": str(
            environment_repair_plan.get("recommended_install_command")
            or summary.get(
                "repository_test_environment_repair_plan_recommended_install_command"
            )
            or ""
        ),
        "repository_test_environment_repair_plan_missing_dependency_module_count": len(
            _list(environment_repair_plan.get("missing_dependency_modules"))
            or _list(
                summary.get(
                    "repository_test_environment_repair_plan_missing_dependency_modules"
                )
            )
        ),
        "repository_test_environment_repair_plan_ready": bool(
            str(
                environment_repair_plan.get("status")
                or summary.get("repository_test_environment_repair_plan_status")
                or ""
            )
            == "pass"
        ),
        "repository_test_environment_repair_plan_has_install_command": bool(
            environment_repair_plan.get("recommended_install_command")
            or summary.get(
                "repository_test_environment_repair_plan_recommended_install_command"
            )
        ),
        "repository_test_pytest_config_source_count": pytest_config_source_count,
        "repository_test_ci_install_command_candidate_count": len(
            ci_install_command_candidates
        ),
        "repository_test_ci_test_command_candidate_count": len(
            ci_test_command_candidates
        ),
        "recommended_install_command": str(
            summary.get("recommended_install_command") or ""
        ),
        "recommended_install_command_present": bool(
            str(summary.get("recommended_install_command") or "")
        ),
        "planned_repository_test_environment_variable_count": len(
            environment_variable_names
        ),
        "repository_test_framework_signals": [
            str(item) for item in _list(test_structure.get("test_framework_signals"))
        ],
        "repository_test_framework_signal_count": len(
            _list(test_structure.get("test_framework_signals"))
        ),
        "repository_test_command_candidate_count": _int(
            test_structure.get("test_command_candidate_count", 0)
        ),
        "repository_test_command_candidate_runner_counts": _dict(
            test_structure.get("test_command_runner_counts")
        ),
        "repository_test_command_candidate_runner_kind_count": _int(
            test_structure.get("test_command_runner_kind_count", 0)
        ),
        "controller_action_id": str(selected_action.get("id") or ""),
        "controller_action_phase": str(selected_action.get("phase") or ""),
        "controller_action_reason": str(selected_action.get("reason") or ""),
        "controller_primary_blocker": str(controller.get("primary_blocker") or ""),
        "agent_controller_loop_complete": (
            controller_loop == EXPECTED_AGENT_CONTROLLER_LOOP
            and decision_trace_phases == EXPECTED_AGENT_CONTROLLER_LOOP
        ),
        "agent_controller_loop_phase_count": len(controller_loop),
        "agent_controller_decision_trace_phase_count": len(decision_trace_phases),
        "agent_controller_observation_count": len(
            _list(controller.get("observations"))
        ),
        "agent_controller_plan_step_count": len(_list(controller.get("plan"))),
        "agent_decision_timeline_status": str(
            decision_timeline.get("status") or ""
        ),
        "agent_decision_timeline_complete": bool(
            decision_timeline.get("complete", False)
        ),
        "agent_decision_timeline_step_count": _int(
            decision_timeline.get("step_count", 0)
        ),
        "agent_decision_timeline_complete_step_count": _int(
            decision_timeline.get("complete_step_count", 0)
        ),
        "agent_decision_timeline_executed_step_count": _int(
            decision_timeline.get("executed_step_count", 0)
        ),
        "agent_decision_timeline_blocked_step_count": _int(
            decision_timeline.get("blocked_step_count", 0)
        ),
        "agent_answers_blocker": str(answers.get("blocker") or ""),
        "agent_answers_next_action": str(answers.get("next_action") or ""),
        "agent_answer_testability_status": str(
            answer_testability.get("status") or ""
        ),
        "agent_answer_testability_answer": str(
            answer_testability.get("answer") or ""
        ),
        "agent_answer_repairability_status": str(
            answer_repairability.get("status") or ""
        ),
        "agent_answer_repairability_answer": str(
            answer_repairability.get("answer") or ""
        ),
        "agent_answer_coverage_complete": bool(
            answer_coverage.get("complete", False)
        ),
        "agent_answer_coverage_answered_count": _int(
            answer_coverage.get("answered_question_count", 0)
        ),
        "agent_answer_coverage_required_count": _int(
            answer_coverage.get("required_question_count", 0)
        ),
        "agent_answer_coverage_missing_questions": _list(
            answer_coverage.get("missing_questions")
        ),
        "agent_answer_question_statuses": answer_question_statuses,
        "agent_auto_action_count": _int(summary.get("agent_auto_action_count", 0)),
        "agent_auto_loop_progress_count": _int(
            auto_loop.get("progress_count", 0)
        ),
        "agent_auto_loop_no_progress_count": _int(
            auto_loop.get("no_progress_count", 0)
        ),
        "agent_auto_complete_loop_recorded": bool(
            auto_loop.get("complete_loop_recorded", False)
        ),
        "agent_auto_action_loop_required_count": auto_action_loop_required_count,
        "agent_auto_action_loop_complete_count": auto_action_loop_complete_count,
        "agent_auto_action_loop_incomplete_count": (
            auto_action_loop_required_count - auto_action_loop_complete_count
        ),
        "agent_auto_action_loop_complete": bool(
            auto_action_loop_required_count > 0
            and auto_action_loop_complete_count == auto_action_loop_required_count
        ),
        "agent_auto_action_id_counts": dict(sorted(auto_action_id_counts.items())),
        "agent_auto_stop_reason": auto_stop_reason,
        "agent_auto_stop_category": str(auto_stop_state.get("category") or ""),
        "agent_auto_stop_action_id": str(auto_stop_state.get("action_id") or ""),
        "agent_auto_stop_recovery_policy": str(
            auto_stop_state.get("recovery_policy") or ""
        ),
        "agent_auto_stop_external_input_kind": str(
            auto_stop_state.get("external_input_kind") or ""
        ),
        "agent_auto_stop_requires_user_action": bool(
            auto_stop_state.get("requires_user_action", False)
        ),
        "agent_auto_stop_requires_environment_change": bool(
            auto_stop_state.get("requires_environment_change", False)
        ),
        "agent_auto_stop_recommended_next_action": str(
            auto_stop_state.get("recommended_next_action") or ""
        ),
        "agent_auto_patch_validation_reached_action_count": (
            auto_patch_validation_reached_count
        ),
        "agent_auto_repair_ready_action_count": auto_repair_ready_action_count,
        "agent_auto_repair_goal_reached": bool(
            auto_stop_reason == "phase_goal_reached:patch_validation_ready"
            or auto_repair_ready_action_count > 0
        ),
        "agent_auto_reflection_action_count": auto_reflection_action_count,
        "agent_auto_reflection_candidate_action_count": (
            auto_reflection_candidate_action_count
        ),
        "agent_auto_successful_reflection_action_count": (
            auto_successful_reflection_action_count
        ),
        "agent_auto_reflection_goal_reached": bool(
            auto_successful_reflection_action_count > 0
        ),
        "agent_auto_verify_outcome_counts": _dict(
            auto_loop.get("verify_outcome_counts")
        ),
        "agent_auto_reflect_status_counts": _dict(
            auto_loop.get("reflect_status_counts")
        ),
        "agent_auto_replan_policy_counts": _dict(
            auto_loop.get("replan_policy_counts")
        ),
        "agent_auto_goal_readiness_status_counts": _dict(
            auto_loop.get("goal_readiness_status_counts")
        ),
        "agent_auto_goal_readiness_passed_action_count": _int(
            auto_loop.get("goal_readiness_passed_action_count", 0)
        ),
        "agent_auto_final_goal_readiness_status": str(
            auto_loop.get("final_goal_readiness_status") or ""
        ),
        "artifact_inventory_status": str(artifact_inventory.get("status") or ""),
        "artifact_inventory_reason": str(artifact_inventory.get("reason") or ""),
        "artifact_inventory_file_check_enabled": bool(
            artifact_inventory.get("file_check_enabled", False)
        ),
        "artifact_inventory_available_count": _int(
            artifact_inventory.get("available_count", 0)
        ),
        "artifact_inventory_artifact_count": _int(
            artifact_inventory.get("artifact_count", 0)
        ),
        "artifact_inventory_required_status": str(
            artifact_inventory.get("required_status") or ""
        ),
        "artifact_inventory_required_available_count": _int(
            artifact_inventory.get("required_available_count", 0)
        ),
        "artifact_inventory_required_count": _int(
            artifact_inventory.get("required_count", 0)
        ),
        "artifact_inventory_missing_required_count": len(
            _list(artifact_inventory.get("missing_required_artifacts"))
        ),
        "artifact_inventory_missing_core_count": len(
            _list(artifact_inventory.get("missing_core_artifacts"))
        ),
        "artifact_inventory_core_ready": bool(artifact_inventory)
        and len(_list(artifact_inventory.get("missing_core_artifacts"))) == 0,
        "artifact_inventory_required_ready": bool(artifact_inventory)
        and len(_list(artifact_inventory.get("missing_required_artifacts"))) == 0,
        "artifact_inventory_core_file_checked_count": sum(
            1 for item in core_items if bool(_dict(item).get("file_checked"))
        ),
        "artifact_inventory_core_file_nonempty_count": sum(
            1 for item in core_items if bool(_dict(item).get("file_nonempty"))
        ),
        "artifact_inventory_required_file_checked_count": sum(
            1 for item in required_items if bool(_dict(item).get("file_checked"))
        ),
        "artifact_inventory_required_file_nonempty_count": sum(
            1 for item in required_items if bool(_dict(item).get("file_nonempty"))
        ),
        "acceptance_gate_status": str(acceptance_gate.get("status") or ""),
        "acceptance_gate_passed": bool(acceptance_gate.get("passed", False)),
        "acceptance_gate_check_count": _int(
            acceptance_gate.get("check_count", 0)
        ),
        "acceptance_gate_passed_check_count": _int(
            acceptance_gate.get("passed_check_count", 0)
        ),
        "acceptance_gate_failed_check_count": _int(
            acceptance_gate.get("failed_check_count", 0)
        ),
        "acceptance_gate_failed_checks": [
            str(item) for item in _list(acceptance_gate.get("failed_checks"))
        ],
        "acceptance_gate_repair_decision_audit_passed": bool(
            _dict(acceptance_checks.get("repair_decision_audit")).get(
                "passed",
                False,
            )
        ),
        "acceptance_gate_repair_decision_audit_evidence": str(
            _dict(acceptance_checks.get("repair_decision_audit")).get("evidence")
            or ""
        ),
        "agent_goal_readiness_status": str(
            agent_goal_readiness.get("status") or ""
        ),
        "agent_goal_readiness_passed": bool(
            agent_goal_readiness.get("passed", False)
        ),
        "agent_goal_readiness_criteria_count": _int(
            agent_goal_readiness.get("criteria_count", 0)
        ),
        "agent_goal_readiness_passed_criteria_count": _int(
            agent_goal_readiness.get("passed_criteria_count", 0)
        ),
        "agent_goal_readiness_failed_criteria_count": _int(
            agent_goal_readiness.get("failed_criteria_count", 0)
        ),
        "agent_goal_readiness_failed_criteria": [
            str(item) for item in _list(agent_goal_readiness.get("failed_criteria"))
        ],
        "agent_goal_repair_decision_audit_passed": bool(
            _dict(goal_criteria.get("repair_decision_audit")).get(
                "passed",
                False,
            )
        ),
        "agent_goal_repair_decision_audit_evidence": str(
            _dict(goal_criteria.get("repair_decision_audit")).get("evidence")
            or ""
        ),
        "objective_compliance_status": str(
            objective_compliance.get("status") or ""
        ),
        "objective_compliance_passed": bool(
            objective_compliance.get("passed", False)
        ),
        "objective_compliance_section_count": _int(
            objective_compliance.get("section_count", 0)
        ),
        "objective_compliance_passed_section_count": _int(
            objective_compliance.get("passed_section_count", 0)
        ),
        "objective_compliance_failed_section_count": _int(
            objective_compliance.get("failed_section_count", 0)
        ),
        "objective_compliance_failed_sections": [
            str(item) for item in _list(objective_compliance.get("failed_sections"))
        ],
        "objective_compliance_section_statuses": objective_section_statuses,
        "phase4_search_evaluation_status": str(phase4.get("status") or ""),
        "phase4_search_evaluation_reason": str(phase4.get("reason") or ""),
        "phase4_search_evaluation_executed": bool(
            phase4_execution.get("executed", False)
        ),
        "phase4_search_evaluation_execution_status": str(
            phase4_execution.get("status") or ""
        ),
        "phase4_search_evaluation_execution_reason": str(
            phase4_execution.get("reason") or ""
        ),
        "phase4_ready_for_evaluation": bool(
            phase4.get("ready_for_phase4", False)
        ),
        "phase4_baseline_regression_caveat": bool(
            phase4.get("baseline_regression_caveat", False)
        ),
        "phase4_full_suite_green_claim_allowed": bool(
            phase4.get("full_suite_green_claim_allowed", False)
        ),
        "phase4_search_candidate_count": _int(
            phase4_search_budget.get("candidate_count", 0)
        ),
        "phase4_search_executed_count": _int(
            phase4_search_budget.get("executed_count", 0)
        ),
        "phase4_search_success_count": _int(
            phase4_search_budget.get("success_count", 0)
        ),
        "phase4_search_success_rate": _float(
            phase4_search_budget.get("success_rate", 0.0)
        ),
        "phase4_search_evaluation_path": str(
            summary.get("phase4_search_evaluation_json") or ""
        ),
        "fault_localization_mode": str(fault.get("mode") or ""),
        "fault_localization_status": str(fault.get("status") or ""),
        "fault_localization_reason": str(fault.get("reason") or ""),
        "fault_localization_top_function": str(fault.get("top_function") or ""),
        "fault_localization_top_source_role": str(fault.get("top_source_role") or ""),
        "fault_localization_ranking_count": len(fault_rankings),
        "fault_localization_application_candidate_count": _int(
            fault.get("application_candidate_count", 0)
        ),
        "fault_localization_non_application_top_ranked": bool(
            fault.get("non_application_top_ranked", False)
        ),
        "fault_localization_non_application_topk_only": bool(
            fault.get("non_application_topk_only", False)
        ),
        "fault_localization_source_role_counts": _dict(
            fault.get("source_role_counts")
        ),
        "fault_localization_matched_failed_test_count": _int(
            fault.get("matched_failed_test_count", 0)
        ),
        "fault_localization_unmatched_failed_test_count": _int(
            fault.get("unmatched_failed_test_count", 0)
        ),
        "fault_localization_traceback_frame_count": _int(
            fault.get("traceback_frame_count", 0)
        ),
        "fault_localization_matched_traceback_frame_count": _int(
            fault.get("matched_traceback_frame_count", 0)
        ),
        "fault_localization_unmatched_traceback_frame_count": _int(
            fault.get("unmatched_traceback_frame_count", 0)
        ),
        "fault_localization_top_static_rule_score": _float(
            fault_top_ranking.get("static_rule_score", 0.0)
        ),
        "fault_localization_top_graph_score": _float(
            fault_top_ranking.get("graph_score", 0.0)
        ),
        "fault_localization_top_source_role_score": _float(
            fault_top_ranking.get("source_role_score", 0.0)
        ),
        "fault_localization_top_sbfl_score": _float(
            fault_top_ranking.get("sbfl_score", 0.0)
        ),
        "fault_localization_top_dynamic_evidence_score": _float(
            fault_top_ranking.get("dynamic_test_evidence_score", 0.0)
        ),
        "fault_localization_top_final_score": _float(
            fault_top_ranking.get("final_score", 0.0)
        ),
        "fault_localization_rankings_with_static_rule_score_count": sum(
            1 for item in fault_rankings if "static_rule_score" in item
        ),
        "fault_localization_rankings_with_graph_score_count": sum(
            1 for item in fault_rankings if "graph_score" in item
        ),
        "fault_localization_rankings_with_source_role_score_count": sum(
            1 for item in fault_rankings if "source_role_score" in item
        ),
        "fault_localization_rankings_with_sbfl_score_count": sum(
            1 for item in fault_rankings if "sbfl_score" in item
        ),
        "fault_localization_rankings_with_dynamic_evidence_score_count": sum(
            1 for item in fault_rankings if "dynamic_test_evidence_score" in item
        ),
        "fault_localization_rankings_with_final_score_count": sum(
            1 for item in fault_rankings if "final_score" in item
        ),
        "repository_test_failure_overlay_status": str(
            summary.get("repository_test_failure_overlay_status") or ""
        ),
        "repository_test_failure_overlay_reason": str(
            summary.get("repository_test_failure_overlay_reason") or ""
        ),
        "repository_test_failure_overlay_selected_rule": str(
            summary.get("repository_test_failure_overlay_selected_rule") or ""
        ),
        "repository_test_failure_overlay_selected_function": str(
            summary.get("repository_test_failure_overlay_selected_function") or ""
        ),
        "repository_test_failure_overlay_dynamic_evidence_level": str(
            summary.get("repository_test_failure_overlay_dynamic_evidence_level") or ""
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
        "repository_patch_generation_mode": str(
            summary.get("repository_patch_generation_mode") or ""
        ),
        "repository_patch_candidate_variant_filter_enabled": bool(
            patch_variant_filter.get("enabled", False)
        ),
        "repository_patch_candidate_variant_filter_input_count": _int(
            patch_variant_filter.get("input_count", 0)
        ),
        "repository_patch_candidate_variant_filter_kept_count": _int(
            patch_variant_filter.get("kept_count", 0)
        ),
        "repository_patch_candidate_variant_filter_dropped_count": _int(
            patch_variant_filter.get("dropped_count", 0)
        ),
        "repository_patch_generator_rule_count": _int(
            patch_generator_counts.get("rule", 0)
        ),
        "repository_patch_generator_llm_count": _int(
            patch_generator_counts.get("llm", 0)
        ),
        "repository_patch_generator_llm_candidate_count": _first_int(
            summary.get("repository_patch_generator_llm_candidate_count"),
            patch_generator_counts.get("llm", 0),
        ),
        "repository_llm_patch_generation_status": str(
            summary.get("repository_llm_patch_generation_status") or ""
        ),
        "repository_llm_patch_generation_reason": str(
            summary.get("repository_llm_patch_generation_reason") or ""
        ),
        "repository_llm_patch_provider": str(
            summary.get("repository_llm_patch_provider")
            or llm_patch_audit.get("provider")
            or ""
        ),
        "repository_llm_patch_model": str(
            summary.get("repository_llm_patch_model")
            or llm_patch_audit.get("model")
            or ""
        ),
        "repository_llm_patch_api_key_present": bool(
            summary.get("repository_llm_patch_api_key_present")
            or llm_patch_audit.get("api_key_present", False)
        ),
        "repository_llm_patch_api_key_source": str(
            summary.get("repository_llm_patch_api_key_source")
            or llm_patch_audit.get("api_key_source")
            or ""
        ),
        "repository_llm_patch_api_key_fingerprint": str(
            summary.get("repository_llm_patch_api_key_fingerprint")
            or llm_patch_audit.get("api_key_fingerprint")
            or ""
        ),
        "repository_llm_patch_request_count": _int(
            summary.get("repository_llm_patch_request_count")
            or llm_patch_generation_audit.get("request_count")
            or llm_patch_telemetry.get("request_count")
            or 0
        ),
        "repository_llm_patch_success_count": _int(
            summary.get("repository_llm_patch_success_count")
            or llm_patch_generation_audit.get("success_count")
            or llm_patch_telemetry.get("success_count")
            or 0
        ),
        "repository_llm_patch_failure_count": _int(
            summary.get("repository_llm_patch_failure_count")
            or llm_patch_generation_audit.get("failure_count")
            or llm_patch_telemetry.get("failure_count")
            or 0
        ),
        "repository_llm_patch_total_tokens": _int(
            summary.get("repository_llm_patch_total_tokens")
            or llm_patch_generation_audit.get("total_tokens")
            or llm_patch_telemetry.get("total_tokens")
            or 0
        ),
        "repository_llm_patch_estimated_total_tokens": _int(
            summary.get("repository_llm_patch_estimated_total_tokens")
            or llm_patch_generation_audit.get("estimated_total_tokens")
            or llm_patch_telemetry.get("estimated_total_tokens")
            or 0
        ),
        "repository_llm_patch_latency_ms_total": _int(
            summary.get("repository_llm_patch_latency_ms_total")
            or llm_patch_generation_audit.get("latency_ms_total")
            or llm_patch_telemetry.get("latency_ms_total")
            or 0
        ),
        "repository_llm_patch_latency_ms_average": _float(
            summary.get("repository_llm_patch_latency_ms_average")
            or llm_patch_generation_audit.get("latency_ms_average")
            or llm_patch_telemetry.get("latency_ms_average")
            or 0.0
        ),
        "repository_llm_patch_estimated_cost_usd": (
            summary.get("repository_llm_patch_estimated_cost_usd")
            if summary.get("repository_llm_patch_estimated_cost_usd") is not None
            else llm_patch_generation_audit.get("estimated_cost_usd")
            if llm_patch_generation_audit.get("estimated_cost_usd") is not None
            else llm_patch_cost_estimate.get("estimated_cost_usd")
        ),
        "repository_llm_patch_cost_available": bool(
            llm_patch_cost_estimate.get("available", False)
            or summary.get("repository_llm_patch_estimated_cost_usd") is not None
            or llm_patch_generation_audit.get("estimated_cost_usd") is not None
        ),
        "repository_llm_patch_error_reason_counts": _dict(
            summary.get("repository_llm_patch_error_reason_counts")
        )
        or _dict(llm_patch_generation_audit.get("error_reason_counts"))
        or _dict(llm_patch_telemetry.get("error_reason_counts")),
        "repository_llm_patch_provider_failure_class": str(
            summary.get("repository_llm_patch_provider_failure_class")
            or _dict(summary.get("llm_repair_action_audit")).get(
                "llm_provider_failure_class"
            )
            or ""
        ),
        "repository_llm_reflection_status": str(
            summary.get("repository_llm_reflection_status") or ""
        ),
        "repository_llm_reflection_reason": str(
            summary.get("repository_llm_reflection_reason") or ""
        ),
        "repository_llm_reflection_blocker": str(
            summary.get("repository_llm_reflection_blocker") or ""
        ),
        "repository_llm_reflection_blocked": bool(
            summary.get("repository_llm_reflection_blocked", False)
        ),
        "repository_llm_reflection_provider": str(
            summary.get("repository_llm_reflection_provider")
            or llm_reflection_audit.get("provider")
            or ""
        ),
        "repository_llm_reflection_model": str(
            summary.get("repository_llm_reflection_model")
            or llm_reflection_audit.get("model")
            or ""
        ),
        "repository_llm_reflection_api_key_present": bool(
            summary.get("repository_llm_reflection_api_key_present")
            or llm_reflection_audit.get("api_key_present", False)
        ),
        "repository_llm_reflection_api_key_source": str(
            summary.get("repository_llm_reflection_api_key_source")
            or llm_reflection_audit.get("api_key_source")
            or ""
        ),
        "repository_llm_reflection_api_key_fingerprint": str(
            summary.get("repository_llm_reflection_api_key_fingerprint")
            or llm_reflection_audit.get("api_key_fingerprint")
            or ""
        ),
        "repository_test_patch_validation_llm_reflection_attempt_count": _int(
            summary.get(
                "repository_test_patch_validation_llm_reflection_attempt_count",
                0,
            )
        ),
        "repository_test_patch_validation_llm_reflection_audit": _list(
            summary.get("repository_test_patch_validation_llm_reflection_audit")
        ),
        "repository_patch_safety_gate_status": str(
            summary.get("repository_patch_safety_gate_status") or ""
        ),
        "repository_patch_safety_gate_blocked_count": _int(
            summary.get("repository_patch_safety_gate_blocked_count", 0)
        ),
        "repository_test_patch_target_function_count": _int(
            summary.get("repository_test_patch_target_function_count", 0)
        ),
        "repository_test_patch_validation_status": str(
            summary.get("repository_test_patch_validation_status") or ""
        ),
        "repository_test_patch_validation_reason": str(
            summary.get("repository_test_patch_validation_reason") or ""
        ),
        "repository_test_patch_validation_input_candidate_count": _int(
            summary.get(
                "repository_test_patch_validation_input_candidate_count",
                0,
            )
        ),
        "repository_test_patch_validation_candidate_count": _int(
            summary.get("repository_test_patch_validation_candidate_count", 0)
        ),
        "repository_test_patch_validation_safety_blocked_candidate_count": _int(
            summary.get(
                "repository_test_patch_validation_safety_blocked_candidate_count",
                0,
            )
        ),
        "repository_test_patch_validation_executed_count": _int(
            summary.get("repository_test_patch_validation_executed_count", 0)
        ),
        "repository_test_patch_validation_success_count": _int(
            summary.get("repository_test_patch_validation_success_count", 0)
        ),
        "repository_test_patch_validation_failure_type_counts": _dict(
            summary.get("repository_test_patch_validation_failure_type_counts")
        ),
        "repository_test_patch_validation_failure_type_kind_count": len(
            _dict(summary.get("repository_test_patch_validation_failure_type_counts"))
        ),
        "repository_test_patch_judge_mode": str(
            summary.get("repository_test_patch_judge_mode") or ""
        ),
        "repository_test_patch_judge_status": str(
            summary.get("repository_test_patch_judge_status") or ""
        ),
        "repository_test_patch_judge_reason": str(
            summary.get("repository_test_patch_judge_reason") or ""
        ),
        "repository_test_patch_judge_enabled": bool(
            summary.get("repository_test_patch_judge_enabled", False)
        ),
        "repository_test_patch_judge_candidate_count": _int(
            summary.get("repository_test_patch_judge_candidate_count", 0)
        ),
        "repository_test_patch_judge_authority": str(
            summary.get("repository_test_patch_judge_authority") or ""
        ),
        "repository_test_patch_judge_verdict_counts": _dict(
            summary.get("repository_test_patch_judge_verdict_counts")
        ),
        "repository_test_patch_judge_agreement_counts": _dict(
            summary.get("repository_test_patch_judge_agreement_counts")
        ),
        "repository_test_patch_judge_outcome_counts": _dict(
            summary.get("repository_test_patch_judge_outcome_counts")
        ),
        "repository_test_patch_judge_accept_success_count": _int(
            summary.get("repository_test_patch_judge_accept_success_count", 0)
        ),
        "repository_test_patch_judge_reject_failure_count": _int(
            summary.get("repository_test_patch_judge_reject_failure_count", 0)
        ),
        "repository_test_patch_judge_accept_failure_count": _int(
            summary.get("repository_test_patch_judge_accept_failure_count", 0)
        ),
        "repository_test_patch_judge_reject_success_count": _int(
            summary.get("repository_test_patch_judge_reject_success_count", 0)
        ),
        "repository_test_repair_ready": bool(
            summary.get("repository_test_repair_ready", False)
        ),
        "repository_test_repair_validation_scope": str(
            summary.get("repository_test_repair_validation_scope") or ""
        ),
        "repository_test_patch_validation_reflection_candidate_count": _int(
            summary.get("repository_test_patch_validation_reflection_candidate_count", 0)
        ),
        "repository_test_patch_validation_successful_reflection_count": _int(
            summary.get(
                "repository_test_patch_validation_successful_reflection_count",
                0,
            )
        ),
        "repository_test_patch_validation_regression_reflection_candidate_count": _int(
            summary.get(
                "repository_test_patch_validation_regression_reflection_candidate_count",
                0,
            )
        ),
        "repository_test_patch_validation_successful_regression_reflection_count": _int(
            summary.get(
                "repository_test_patch_validation_successful_regression_reflection_count",
                0,
            )
        ),
        "reflection_initial_failure_type_counts": _dict(
            reflection.get("initial_failure_type_counts")
        ),
        "reflection_initial_failure_type_kind_count": len(
            _dict(reflection.get("initial_failure_type_counts"))
        ),
        "reflection_parent_failure_type_counts": _dict(
            reflection.get("reflection_parent_failure_type_counts")
        ),
        "reflection_parent_failure_type_kind_count": len(
            _dict(reflection.get("reflection_parent_failure_type_counts"))
        ),
        "successful_reflection_parent_failure_type_counts": _dict(
            reflection.get("successful_reflection_parent_failure_type_counts")
        ),
        "successful_reflection_parent_failure_type_kind_count": len(
            _dict(reflection.get("successful_reflection_parent_failure_type_counts"))
        ),
        "reflection_failure_type_counts": _dict(
            reflection.get("reflection_failure_type_counts")
        ),
        "reflection_failure_type_kind_count": len(
            _dict(reflection.get("reflection_failure_type_counts"))
        ),
        "report_path": str(summary.get("intelligence_json") or ""),
        "controller_report_path": str(summary.get("agent_controller_json") or ""),
        "artifact_inventory_path": str(summary.get("artifact_inventory_json") or ""),
    }


def _evaluate_expectations(
    metrics: dict[str, Any],
    *,
    expected_status: str,
    options: dict[str, Any],
) -> list[dict[str, Any]]:
    checks = [
        _expectation_check(
            name="status",
            actual=str(metrics.get("status") or ""),
            expected=expected_status,
        )
    ]
    for option_name, metric_name in (
        ("expected_analysis_stage", "analysis_stage"),
        ("expected_blocker", "blocker"),
        ("expected_controller_action", "controller_action_id"),
        ("expected_agent_shortcut", "agent_shortcut"),
        ("expected_execution_profile", "execution_profile"),
        ("expected_artifact_inventory_status", "artifact_inventory_status"),
        ("expected_fault_localization_mode", "fault_localization_mode"),
        ("expected_failure_overlay_status", "repository_test_failure_overlay_status"),
        ("expected_patch_candidates_status", "repository_test_patch_candidates_status"),
        ("expected_patch_generation_mode", "repository_patch_generation_mode"),
        (
            "expected_llm_patch_generation_status",
            "repository_llm_patch_generation_status",
        ),
        (
            "expected_patch_safety_gate_status",
            "repository_patch_safety_gate_status",
        ),
        ("expected_patch_validation_status", "repository_test_patch_validation_status"),
        ("expected_patch_judge_mode", "repository_test_patch_judge_mode"),
        ("expected_patch_judge_status", "repository_test_patch_judge_status"),
        ("expected_repair_validation_scope", "repository_test_repair_validation_scope"),
        ("expected_planned_repository_test_runner", "planned_repository_test_runner"),
        (
            "expected_planned_repository_test_result_status",
            "planned_repository_test_result_status",
        ),
        (
            "expected_planned_repository_test_runner_fallback_reason",
            "planned_repository_test_runner_fallback_reason",
        ),
        ("expected_dynamic_evidence_level", "repository_test_dynamic_evidence_level"),
        ("expected_repository_test_environment_status", "repository_test_environment_status"),
        ("expected_repository_test_setup_doctor_status", "repository_test_setup_doctor_status"),
        ("expected_repository_test_setup_doctor_blocker", "repository_test_setup_doctor_blocker"),
        (
            "expected_repository_test_timeout_narrowing_status",
            "repository_test_timeout_narrowing_status",
        ),
        (
            "expected_repository_test_timeout_narrowing_reason",
            "repository_test_timeout_narrowing_reason",
        ),
        (
            "expected_repository_test_timeout_narrowing_selected_failure_category",
            "repository_test_timeout_narrowing_selected_failure_category",
        ),
        (
            "expected_agent_answer_testability_status",
            "agent_answer_testability_status",
        ),
        (
            "expected_agent_answer_repairability_status",
            "agent_answer_repairability_status",
        ),
    ):
        if options.get(option_name) is not None:
            checks.append(
                _expectation_check(
                    name=option_name.removeprefix("expected_"),
                    actual=str(metrics.get(metric_name) or ""),
                    expected=str(options.get(option_name) or ""),
                )
            )
    return checks


def _expectation_check(*, name: str, actual: str, expected: str) -> dict[str, Any]:
    return {
        "name": name,
        "expected": expected,
        "actual": actual,
        "passed": actual == expected,
    }


def _metric_thresholds(options: dict[str, Any]) -> dict[str, float]:
    raw = _dict(options.get("metric_thresholds"))
    return {
        str(metric): _float(expected)
        for metric, expected in raw.items()
        if expected is not None
    }


def _suite_thresholds(payload: dict[str, Any]) -> dict[str, float]:
    raw = _dict(payload.get("suite_thresholds"))
    return {
        str(metric): _float(expected)
        for metric, expected in raw.items()
        if expected is not None
    }


def _evaluate_metric_thresholds(
    metrics: dict[str, Any],
    thresholds: dict[str, float],
) -> list[dict[str, Any]]:
    checks = []
    for metric, expected in sorted(thresholds.items()):
        actual = _float(metrics.get(metric, 0.0))
        checks.append(
            {
                "metric": metric,
                "expected": f">= {expected:.4f}",
                "actual": f"{actual:.4f}",
                "passed": actual + 1e-12 >= expected,
            }
        )
    return checks


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


def _refresh_suite_threshold_checks(
    summary: dict[str, Any],
    thresholds: dict[str, float],
) -> None:
    suite_checks = _evaluate_suite_thresholds(summary, thresholds)
    summary["suite_threshold_checks"] = suite_checks
    summary["suite_threshold_check_count"] = len(suite_checks)
    summary["suite_threshold_failed_count"] = sum(
        1 for check in suite_checks if not bool(check.get("passed"))
    )


def _suite_passed(summary: dict[str, Any]) -> bool:
    return (
        _int(summary.get("command_failed_count", 0)) == 0
        and _int(summary.get("expectation_failed_count", 0)) == 0
        and _int(summary.get("metric_check_failed_count", 0)) == 0
        and _int(summary.get("suite_threshold_failed_count", 0)) == 0
    )


def _suite_threshold_metric_and_comparator(key: str) -> tuple[str, str]:
    if key.startswith("max_"):
        return key.removeprefix("max_"), "<="
    if key.startswith("min_"):
        return key.removeprefix("min_"), ">="
    return key, ">="


def _write_suite_outputs(
    report: GitHubRepoIntelligenceSuiteReport,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "github_repo_intelligence_suite.json").write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "github_repo_intelligence_suite.md").write_text(
        render_github_repo_intelligence_suite_markdown(report),
        encoding="utf-8",
    )


def _attach_github_onboarding_matrix(
    report: GitHubRepoIntelligenceSuiteReport,
    output_dir: Path,
    *,
    required_case_count: int,
) -> None:
    report_paths = [
        run.report_path
        for run in report.runs
        if str(run.report_path or "").strip()
    ]
    backfill = backfill_github_onboarding_artifacts(report_paths)
    matrix = build_github_onboarding_matrix(
        report_paths,
        required_case_count=required_case_count,
    )
    paths = write_github_onboarding_matrix_artifacts(matrix, output_dir)
    scenario_coverage = _dict(matrix.get("scenario_coverage"))
    covered_scenario_count = sum(
        1
        for row in scenario_coverage.values()
        if _int(_dict(row).get("count", 0)) > 0
    )
    artifact_coverage = _dict(matrix.get("artifact_coverage"))
    complete_artifact_group_count = sum(
        1
        for row in artifact_coverage.values()
        if _int(_dict(row).get("missing", 0)) == 0
        and _int(_dict(row).get("present", 0)) > 0
    )
    report.summary.update(
        {
            "github_onboarding_matrix_status": str(matrix.get("status") or ""),
            "github_onboarding_matrix_reason": str(matrix.get("reason") or ""),
            "github_onboarding_matrix_case_count": _int(
                matrix.get("case_count", 0)
            ),
            "github_onboarding_matrix_required_case_count": _int(
                matrix.get("required_case_count", required_case_count)
            ),
            "github_onboarding_matrix_passed_check_count": _int(
                matrix.get("passed_check_count", 0)
            ),
            "github_onboarding_matrix_check_count": _int(
                matrix.get("check_count", 0)
            ),
            "github_onboarding_matrix_covered_scenario_count": (
                covered_scenario_count
            ),
            "github_onboarding_matrix_complete_artifact_group_count": (
                complete_artifact_group_count
            ),
            "github_onboarding_backfill_status": str(backfill.get("status") or ""),
            "github_onboarding_backfill_report_count": _int(
                backfill.get("report_count", 0)
            ),
            "github_onboarding_backfill_status_counts": _dict(
                backfill.get("status_counts")
            ),
            "github_onboarding_matrix_json": str(
                paths.get("github_onboarding_matrix_json") or ""
            ),
            "github_onboarding_matrix_markdown": str(
                paths.get("github_onboarding_matrix_markdown") or ""
            ),
        }
    )


def _attach_llm_repair_showcase_matrix(
    report: GitHubRepoIntelligenceSuiteReport,
    output_dir: Path,
    *,
    source_report_paths: list[Path] | None = None,
) -> None:
    suite_payload = report.to_dict()
    suite_payload["suite_report_path"] = str(
        output_dir / "github_repo_intelligence_suite.json"
    )
    external_payloads, missing_sources = _load_llm_repair_source_reports(
        source_report_paths or []
    )
    matrix = build_llm_repair_showcase_matrix([suite_payload, *external_payloads])
    paths = write_llm_repair_showcase_matrix_artifacts(matrix, output_dir)
    class_counts = _dict(matrix.get("class_counts"))
    requirement_status = _dict(matrix.get("requirement_status"))
    evaluation_path = Path(str(paths.get("llm_repair_evaluation_matrix_json") or ""))
    metrics_path = Path(str(paths.get("llm_repair_metrics_report_json") or ""))
    evaluation = _json_artifact_payload(evaluation_path)
    metrics_report = _dict(evaluation.get("metrics_report")) or _json_artifact_payload(
        metrics_path
    )
    patch_success_at = _dict(metrics_report.get("patch_success_at"))
    report.summary.update(
        {
            "llm_repair_showcase_matrix_status": str(
                matrix.get("status") or ""
            ),
            "llm_repair_showcase_matrix_reason": str(
                matrix.get("reason") or ""
            ),
            "llm_repair_showcase_matrix_case_count": _int(
                matrix.get("case_count", 0)
            ),
            "llm_repair_showcase_matrix_json": str(
                paths.get("llm_repair_showcase_matrix_json") or ""
            ),
            "llm_repair_showcase_matrix_markdown": str(
                paths.get("llm_repair_showcase_matrix_markdown") or ""
            ),
            "llm_repair_evaluation_matrix_status": str(
                evaluation.get("status") or ""
            ),
            "llm_repair_evaluation_matrix_reason": str(
                evaluation.get("reason") or ""
            ),
            "llm_repair_evaluation_matrix_json": str(
                paths.get("llm_repair_evaluation_matrix_json") or ""
            ),
            "llm_repair_evaluation_matrix_markdown": str(
                paths.get("llm_repair_evaluation_matrix_markdown") or ""
            ),
            "llm_repair_metrics_report_status": str(
                metrics_report.get("status") or ""
            ),
            "llm_repair_metrics_report_json": str(
                paths.get("llm_repair_metrics_report_json") or ""
            ),
            "llm_repair_metrics_report_markdown": str(
                paths.get("llm_repair_metrics_report_markdown") or ""
            ),
            "llm_repair_metrics_patch_success_at": (
                f"1={_float(patch_success_at.get('1', 0.0)):.4f}, "
                f"3={_float(patch_success_at.get('3', 0.0)):.4f}, "
                f"5={_float(patch_success_at.get('5', 0.0)):.4f}"
            ),
            "llm_repair_metrics_sandbox_pass_rate": _float(
                metrics_report.get("sandbox_pass_rate", 0.0)
            ),
            "llm_repair_metrics_judge_sandbox_agreement_rate": _float(
                metrics_report.get("judge_sandbox_agreement_rate", 0.0)
            ),
            "llm_repair_metrics_patch_judge_accept_success_count": _int(
                metrics_report.get("patch_judge_accept_success_count", 0)
            ),
            "llm_repair_metrics_patch_judge_reject_failure_count": _int(
                metrics_report.get("patch_judge_reject_failure_count", 0)
            ),
            "llm_repair_metrics_agent_loop_trace_complete_count": _int(
                metrics_report.get("agent_loop_trace_complete_count", 0)
            ),
            "llm_repair_showcase_matrix_class_counts": class_counts,
            "llm_repair_showcase_matrix_requirement_status": requirement_status,
            "llm_repair_showcase_matrix_direct_success_count": _int(
                class_counts.get("llm_direct_success", 0)
            ),
            "llm_repair_showcase_matrix_reflection_success_count": _int(
                class_counts.get("llm_reflection_success", 0)
            ),
            "llm_repair_showcase_matrix_blocker_count": _int(
                class_counts.get("llm_blocker", 0)
            ),
            "llm_repair_showcase_matrix_direct_success_present": bool(
                requirement_status.get("llm_direct_success", False)
            ),
            "llm_repair_showcase_matrix_reflection_success_present": bool(
                requirement_status.get("llm_reflection_success", False)
            ),
            "llm_repair_showcase_matrix_blocker_present": bool(
                requirement_status.get("llm_blocker", False)
            ),
            "llm_repair_source_report_count": len(external_payloads),
            "llm_repair_source_report_missing_count": len(missing_sources),
            "llm_repair_source_report_paths": [
                str(path) for path in (source_report_paths or [])
            ],
            "llm_repair_source_report_missing_paths": missing_sources,
        }
    )


def _llm_repair_source_report_paths(
    payload: dict[str, Any],
    *,
    defaults: dict[str, Any],
    manifest: Path,
) -> list[Path]:
    values: list[str] = []
    for key in (
        "llm_repair_source_reports",
        "llm_repair_source_report_paths",
        "llm_repair_evaluation_source_reports",
    ):
        values.extend(_list_str(defaults.get(key)))
        values.extend(_list_str(payload.get(key)))
    paths: list[Path] = []
    for value in _dedupe_strings(values):
        path = Path(value)
        if path.is_absolute() or path.exists():
            paths.append(path)
            continue
        paths.append(manifest.parent / path)
    return paths


def _load_llm_repair_source_reports(
    source_report_paths: list[Path],
) -> tuple[list[dict[str, Any]], list[str]]:
    payloads: list[dict[str, Any]] = []
    missing: list[str] = []
    for path in source_report_paths:
        resolved = path / "github_repo_intelligence_suite.json" if path.is_dir() else path
        payload = _json_artifact_payload(resolved)
        if not payload:
            missing.append(str(resolved))
            continue
        payload.setdefault("suite_report_path", str(resolved))
        payloads.append(payload)
    return payloads, missing


def _attach_llm_repair_case_catalog_audit(
    report: GitHubRepoIntelligenceSuiteReport,
    output_dir: Path,
    *,
    catalog_path: Path | None,
) -> None:
    _write_suite_outputs(report, output_dir)
    repair_path_text = str(report.summary.get("llm_repair_evaluation_matrix_json") or "")
    repair_path = Path(repair_path_text) if repair_path_text else None
    repair_matrix = _json_artifact_payload(repair_path)
    catalog = _json_artifact_payload(catalog_path) if catalog_path else {}
    if not catalog:
        report.summary.update(
            {
                "llm_repair_case_catalog_audit_status": "not_run",
                "llm_repair_case_catalog_audit_reason": "catalog_missing",
                "llm_repair_case_catalog_path": str(catalog_path or ""),
                "llm_repair_case_catalog_matrix_path": repair_path_text,
                "llm_repair_case_catalog_audit_json": "",
                "llm_repair_case_catalog_audit_markdown": "",
                "llm_repair_case_catalog_declared_case_count": 0,
                "llm_repair_case_catalog_matched_case_count": 0,
                "llm_repair_case_catalog_missing_case_count": 0,
                "llm_repair_case_catalog_missing_source_report_count": 0,
                "llm_repair_case_catalog_failed_check_count": 0,
                "llm_repair_case_catalog_passed_check_count": 0,
                "llm_repair_case_catalog_check_count": 0,
                "llm_repair_case_catalog_audit_missing": [
                    "llm_repair_case_catalog"
                ],
            }
        )
        return
    audit = build_llm_repair_case_catalog_audit(
        catalog,
        repair_matrix,
        catalog_path=str(catalog_path or ""),
        matrix_path=repair_path_text,
    )
    paths = write_llm_repair_case_catalog_audit_artifacts(audit, output_dir)
    summary = _dict(audit.get("summary"))
    counts = _dict(audit.get("counts"))
    report.summary.update(
        {
            "llm_repair_case_catalog_audit_status": str(audit.get("status") or ""),
            "llm_repair_case_catalog_audit_reason": str(audit.get("reason") or ""),
            "llm_repair_case_catalog_path": str(catalog_path or ""),
            "llm_repair_case_catalog_matrix_path": repair_path_text,
            "llm_repair_case_catalog_audit_json": str(
                paths.get("llm_repair_case_catalog_audit_json") or ""
            ),
            "llm_repair_case_catalog_audit_markdown": str(
                paths.get("llm_repair_case_catalog_audit_markdown") or ""
            ),
            "llm_repair_case_catalog_declared_case_count": _int(
                summary.get("declared_case_count", 0)
            ),
            "llm_repair_case_catalog_matched_case_count": _int(
                summary.get("matched_case_count", 0)
            ),
            "llm_repair_case_catalog_missing_case_count": _int(
                summary.get("missing_case_count", 0)
            ),
            "llm_repair_case_catalog_matrix_case_count": _int(
                summary.get("matrix_case_count", 0)
            ),
            "llm_repair_case_catalog_matrix_exists": bool(
                summary.get("matrix_exists", False)
            ),
            "llm_repair_case_catalog_source_report_count": _int(
                summary.get("source_report_count", 0)
            ),
            "llm_repair_case_catalog_missing_source_report_count": _int(
                summary.get("missing_source_report_count", 0)
            ),
            "llm_repair_case_catalog_direct_success_count": _int(
                counts.get("llm_direct_success_count", 0)
            ),
            "llm_repair_case_catalog_reflection_success_count": _int(
                counts.get("llm_reflection_success_count", 0)
            ),
            "llm_repair_case_catalog_blocker_count": _int(
                counts.get("llm_blocker_count", 0)
            ),
            "llm_repair_case_catalog_agent_loop_trace_complete_count": _int(
                counts.get("agent_loop_trace_complete_count", 0)
            ),
            "llm_repair_case_catalog_failed_check_count": _int(
                summary.get("failed_target_check_count", 0)
            ),
            "llm_repair_case_catalog_passed_check_count": _int(
                summary.get("passed_target_check_count", 0)
            ),
            "llm_repair_case_catalog_check_count": _int(
                summary.get("target_check_count", 0)
            ),
            "llm_repair_case_catalog_audit_missing": _list(audit.get("missing")),
            "llm_repair_case_catalog_audit_next_actions": _list(
                audit.get("next_actions")
            ),
            "llm_repair_case_catalog_sandbox_authority": str(
                audit.get("sandbox_authority") or ""
            ),
        }
    )


def _llm_repair_case_catalog_path(
    payload: dict[str, Any],
    *,
    defaults: dict[str, Any],
    manifest: Path,
) -> Path | None:
    for key in (
        "llm_repair_case_catalog",
        "llm_repair_case_catalog_path",
        "llm_repair_case_catalog_manifest",
    ):
        value = str(payload.get(key) or defaults.get(key) or "")
        if value:
            path = Path(value)
            if path.is_absolute() or path.exists():
                return path
            return manifest.parent / path
    return None


def _attach_p6_readiness_audit(
    report: GitHubRepoIntelligenceSuiteReport,
    output_dir: Path,
) -> None:
    onboarding_path = Path(str(report.summary.get("github_onboarding_matrix_json") or ""))
    repair_path = Path(str(report.summary.get("llm_repair_evaluation_matrix_json") or ""))
    onboarding_matrix = _json_artifact_payload(onboarding_path)
    repair_matrix = _json_artifact_payload(repair_path)
    if not onboarding_matrix or not repair_matrix:
        report.summary.update(
            {
                "p6_readiness_audit_status": "not_run",
                "p6_readiness_audit_reason": "required_matrices_missing",
                "p6_readiness_audit_json": "",
                "p6_readiness_audit_markdown": "",
                "p6_readiness_audit_failed_check_count": 0,
                "p6_readiness_audit_passed_check_count": 0,
                "p6_readiness_audit_check_count": 0,
                "p6_readiness_audit_missing": [
                    name
                    for name, payload in (
                        ("github_onboarding_matrix", onboarding_matrix),
                        ("llm_repair_evaluation_matrix", repair_matrix),
                    )
                    if not payload
                ],
            }
        )
        return
    audit = build_p6_readiness_audit(
        onboarding_matrix,
        repair_matrix,
        onboarding_matrix_path=str(onboarding_path),
        repair_matrix_path=str(repair_path),
    )
    paths = write_p6_readiness_audit_artifacts(audit, output_dir)
    summary = _dict(audit.get("summary"))
    report.summary.update(
        {
            "p6_readiness_audit_status": str(audit.get("status") or ""),
            "p6_readiness_audit_reason": str(audit.get("reason") or ""),
            "p6_readiness_audit_json": str(
                paths.get("p6_readiness_audit_json") or ""
            ),
            "p6_readiness_audit_markdown": str(
                paths.get("p6_readiness_audit_markdown") or ""
            ),
            "p6_readiness_audit_failed_check_count": _int(
                summary.get("failed_check_count", 0)
            ),
            "p6_readiness_audit_passed_check_count": _int(
                summary.get("passed_check_count", 0)
            ),
            "p6_readiness_audit_check_count": _int(
                summary.get("check_count", 0)
            ),
            "p6_readiness_audit_missing": _list(audit.get("missing")),
            "p6_readiness_audit_next_actions": _list(audit.get("next_actions")),
            "p6_readiness_audit_sandbox_authority": str(
                audit.get("sandbox_authority") or ""
            ),
        }
    )


def _apply_execution_profile_options(options: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(options)
    profile = str(normalized.get("execution_profile") or "static")
    if _bool_option(normalized, "agent", False):
        profile = "agent-auto"
        normalized["execution_profile"] = "agent-auto"
        if _int(normalized.get("auto_controller_max_actions", 0)) < 4:
            normalized["auto_controller_max_actions"] = 4
    normalized["execution_profile"] = profile
    repository_test_timeout_explicit = (
        "repository_test_timeout" in normalized
        and normalized.get("repository_test_timeout") is not None
    )
    if profile == "static":
        return normalized
    if profile == "controlled-repair":
        return normalized
    if profile == "checkout":
        normalized["checkout_repository_tests"] = True
        if not repository_test_timeout_explicit and _int(
            normalized.get(
                "repository_test_timeout",
                DEFAULT_REPOSITORY_TEST_TIMEOUT,
            )
        ) == DEFAULT_REPOSITORY_TEST_TIMEOUT:
            normalized["repository_test_timeout"] = 30
        return normalized
    if profile == "phase3-fast":
        normalized["checkout_repository_tests"] = True
        normalized["run_repository_test_retry_prerequisites"] = True
        normalized["auto_repository_test_retry"] = True
        if not repository_test_timeout_explicit and _int(
            normalized.get(
                "repository_test_timeout",
                DEFAULT_REPOSITORY_TEST_TIMEOUT,
            )
        ) == DEFAULT_REPOSITORY_TEST_TIMEOUT:
            normalized["repository_test_timeout"] = 30
        if str(normalized.get("auto_repository_test_retry_max_risk") or "low") == "low":
            normalized["auto_repository_test_retry_max_risk"] = "medium"
        allowed_runners = _list_str(
            normalized.get(
                "auto_repository_test_retry_allowed_runners",
                normalized.get("auto_repository_test_retry_runner"),
            )
        )
        if not allowed_runners:
            allowed_runners = ["pytest", "unittest"]
        normalized["auto_repository_test_retry_allowed_runners"] = allowed_runners
        return normalized
    if profile == "agent-auto":
        normalized["auto_controller_actions"] = True
        if not repository_test_timeout_explicit and _int(
            normalized.get(
                "repository_test_timeout",
                DEFAULT_REPOSITORY_TEST_TIMEOUT,
            )
        ) == DEFAULT_REPOSITORY_TEST_TIMEOUT:
            normalized["repository_test_timeout"] = 30
        return normalized
    raise ValueError(f"unsupported execution profile: {profile}")


def _command_args(repo: str, output_dir: Path, options: dict[str, Any]) -> list[str]:
    args = [
        "python",
        "-m",
        "code_intelligence_agent.evaluation.github_repo_intelligence",
        repo,
    ]
    if not _bool_option(options, "use_cli_default_output_dir", False):
        args.append(str(output_dir))
    profile = str(options.get("execution_profile") or "")
    agent_shortcut = _bool_option(options, "agent", False)
    if agent_shortcut:
        args.append("--agent")
    elif profile and profile != "static":
        args.extend(["--execution-profile", profile])
    for key, flag in (
        ("ref", "--ref"),
        ("target_prefix", "--target-prefix"),
        ("preset", "--preset"),
        ("source_cache_dir", "--source-cache-dir"),
        ("api_base_url", "--api-base-url"),
    ):
        value = options.get(key)
        if value:
            args.extend([flag, str(value)])
    for value in _list_str(options.get("include")):
        args.extend(["--include", value])
    for value in _list_str(options.get("exclude")):
        args.extend(["--exclude", value])
    for value in _list_str(options.get("recipes", options.get("recipe"))):
        args.extend(["--recipe", value])
    if options.get("max_sources") is not None:
        args.extend(["--max-sources", str(options.get("max_sources"))])
    if options.get("max_candidates") is not None:
        args.extend(["--max-candidates", str(options.get("max_candidates"))])
    if options.get("repository_test_timeout") is not None:
        args.extend(
            [
                "--repository-test-timeout",
                str(options.get("repository_test_timeout")),
            ]
        )
    if options.get("auto_controller_max_actions") is not None:
        args.extend(
            [
                "--auto-controller-max-actions",
                str(options.get("auto_controller_max_actions")),
            ]
        )
    if options.get("repository_test_failure_overlay_candidate_limit") is not None:
        args.extend(
            [
                "--repository-test-failure-overlay-candidate-limit",
                str(options.get("repository_test_failure_overlay_candidate_limit")),
            ]
        )
    if options.get("repository_test_patch_validation_limit") is not None:
        args.extend(
            [
                "--repository-test-patch-validation-limit",
                str(options.get("repository_test_patch_validation_limit")),
            ]
        )
    if options.get("repository_patch_generation_mode") is not None:
        args.extend(
            [
                "--repository-patch-generation-mode",
                str(options.get("repository_patch_generation_mode")),
            ]
        )
    if options.get("repository_llm_patch_candidate_limit") is not None:
        args.extend(
            [
                "--repository-llm-patch-candidate-limit",
                str(options.get("repository_llm_patch_candidate_limit")),
            ]
        )
    for value in _list_str(options.get("repository_patch_candidate_variant_allowlist")):
        args.extend(["--repository-patch-candidate-variant", value])
    if options.get("repository_test_reflection_mode") is not None:
        args.extend(
            [
                "--repository-test-reflection-mode",
                str(options.get("repository_test_reflection_mode")),
            ]
        )
    if options.get("repository_test_reflection_rounds") is not None:
        args.extend(
            [
                "--repository-test-reflection-rounds",
                str(options.get("repository_test_reflection_rounds")),
            ]
        )
    if options.get("repository_test_reflection_width") is not None:
        args.extend(
            [
                "--repository-test-reflection-width",
                str(options.get("repository_test_reflection_width")),
            ]
        )
    if options.get("patch_judge_mode") is not None:
        args.extend(["--patch-judge-mode", str(options.get("patch_judge_mode"))])
    if options.get("repository_checkout_depth") is not None:
        args.extend(
            [
                "--repository-checkout-depth",
                str(options.get("repository_checkout_depth")),
            ]
        )
    if options.get("auto_fallback") is False:
        args.append("--no-auto-fallback")
    if options.get("run_repository_test_command") is False:
        args.append("--no-repository-test-command")
    if _bool_option(options, "checkout_repository_tests", False):
        args.append("--checkout-repository-tests")
    if _bool_option(options, "prefer_cached_discovery", False):
        args.append("--prefer-cached-discovery")
    if _bool_option(options, "run_repository_test_retry_prerequisites", False):
        args.append("--run-repository-test-retry-prerequisites")
    if _bool_option(options, "auto_repository_test_retry", False):
        args.append("--auto-repository-test-retry")
    if options.get("auto_repository_test_retry_max_risk") is not None:
        args.extend(
            [
                "--auto-repository-test-retry-max-risk",
                str(options.get("auto_repository_test_retry_max_risk")),
            ]
        )
    for runner in _list_str(options.get("auto_repository_test_retry_allowed_runners")):
        args.extend(["--auto-repository-test-retry-runner", runner])
    if _bool_option(options, "auto_controller_actions", False) and not agent_shortcut:
        args.append("--auto-controller-actions")
    return args


def _run_name(options: dict[str, Any], index: int) -> str:
    name = str(options.get("name") or "").strip()
    if name:
        return _safe_name(name)
    repo = str(options.get("repo") or options.get("repo_spec") or f"run_{index + 1}")
    return _safe_name(repo.replace("/", "_"))


def _safe_name(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in {"_", "-"} else "_"
        for char in value
    )


def _token_from_env(name: str) -> str | None:
    if not name:
        return None
    return os.environ.get(name) or None


def _scenario_tags(options: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    tags: list[str] = []
    for item in _list(options.get("scenario_tags", options.get("tags"))):
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        tags.append(text)
    return tags


def _repo_input_kind(repo: str) -> str:
    text = str(repo or "").strip()
    lowered = text.lower()
    if lowered.startswith("https://github.com/") or lowered.startswith(
        "http://github.com/"
    ):
        return "github_url"
    if (
        "/" in text
        and "://" not in text
        and len([part for part in text.split("/") if part]) == 2
    ):
        return "owner_repo"
    return "other"


def _llm_api_key_env_names() -> list[str]:
    return [
        "CIA_LLM_API_KEY",
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
        "ALIBABA_API_KEY",
        "CIA_JUDGE_API_KEY",
        "CIA_LOCALIZATION_LLM_API_KEY",
    ]


@contextmanager
def _temporary_cleared_env(names: list[str]):
    saved = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ.pop(name, None)
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _bool_option(
    options: dict[str, Any],
    key: str,
    default: bool,
) -> bool:
    if key not in options:
        return default
    value = options.get(key)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _list_str(value: Any) -> list[str]:
    return [str(item) for item in _list(value) if str(item)]


def _dedupe_strings(values: Any) -> list[str]:
    deduped: list[str] = []
    if isinstance(values, (str, bytes, dict)) or values is None:
        iterable = _list(values)
    else:
        try:
            iterable = list(values)
        except TypeError:
            iterable = _list(values)
    for value in iterable:
        text = str(value or "")
        if text and text not in deduped:
            deduped.append(text)
    return deduped


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _first_int(*values: Any, default: int = 0) -> int:
    for value in values:
        if value is None:
            continue
        text = str(value)
        if not text:
            continue
        return _int(value, default)
    return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _count(counter: Counter[str], value: Any) -> None:
    text = str(value or "")
    if text:
        counter.update([text])


def _counter_metric_fields(prefix: str, counts: Counter[str]) -> dict[str, int]:
    fields: dict[str, int] = {}
    for key, value in counts.items():
        fragment = _metric_key_fragment(key)
        if fragment:
            fields[f"{prefix}_{fragment}_count"] = _int(value)
    return fields


def _metric_key_fragment(value: Any) -> str:
    text = str(value or "").strip().lower()
    chars = [char if char.isalnum() else "_" for char in text]
    compact = "_".join(part for part in "".join(chars).split("_") if part)
    return compact


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(
        f"{key}={_int(value)}" for key, value in sorted(counts.items())
    )


def _format_list(values: list[Any]) -> str:
    if not values:
        return "none"
    return ", ".join(str(value) for value in values)


def _markdown_cell(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    main()
