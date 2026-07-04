from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.github_benchmark_onboarding import (
    DEFAULT_DEPENDENCY_MAX_DEPTH,
    GitHubBenchmarkOnboardingReport,
    OnboardingQualityGateThresholds,
    github_ref_candidates_from_repo_spec,
    onboard_from_discovery,
    onboard_tree,
    parse_github_repo_spec,
    parse_github_repo_spec_with_ref,
)
from code_intelligence_agent.evaluation.github_discovery_fetcher import GitHubAPIError


@dataclass(frozen=True)
class GitHubRepoAgentReport:
    repo_spec: str
    owner: str
    repo: str
    output_dir: str
    preset: str
    status: str
    summary: dict[str, Any]
    output_paths: dict[str, str]
    onboarding_report: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["passed"] = self.passed
        return payload


def run_github_repo_agent(
    repo_spec: str,
    output_dir: str | Path,
    *,
    ref: str | None = None,
    token: str | None = None,
    recursive: bool = True,
    api_base_url: str = "https://api.github.com",
    timeout: int = 20,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    target_prefix: str = "",
    recipes: list[str] | None = None,
    source_cache_dir: str | Path | None = None,
    max_sources: int | None = None,
    max_candidates: int | None = None,
    auto_dependency_sources: bool = True,
    dependency_max_depth: int = DEFAULT_DEPENDENCY_MAX_DEPTH,
    preset: str = "smoke",
    run_smoke_validation: bool | None = None,
    repository_test_root: str | Path | None = None,
    repository_test_timeout: int = 20,
    repository_test_failure_overlay_candidate_limit: int = 5,
    repository_test_patch_validation_limit: int = 5,
    repository_patch_generation_mode: str = "rule",
    repository_llm_patch_candidate_limit: int | None = None,
    repository_patch_candidate_variant_allowlist: list[str] | None = None,
    repository_test_reflection_mode: str = "rule",
    repository_test_reflection_rounds: int = 1,
    repository_test_reflection_width: int = 1,
    patch_judge_mode: str = "none",
    run_repository_test_command: bool = True,
    run_repository_test_environment_setup: bool = False,
    run_repository_test_retry: bool = False,
    run_repository_test_retry_prerequisites: bool = False,
    auto_repository_test_retry: bool = False,
    auto_repository_test_retry_max_risk: str = "low",
    auto_repository_test_retry_allowed_runners: list[str] | None = None,
    repository_test_environment_setup_timeout: int = 120,
    checkout_repository_tests: bool = False,
    repository_checkout_timeout: int = 120,
    repository_checkout_depth: int = 1,
    prefer_cached_discovery: bool = False,
    auto_fallback: bool = True,
    fallback_min_generated_candidates: int = 1,
    fallback_max_sources: int | None = None,
    fallback_max_candidates: int | None = None,
    fallback_preset: str | None = None,
    fallback_recipes: list[str] | None = None,
    auto_remediate_benchmark: bool = False,
    opener=None,
) -> GitHubRepoAgentReport:
    owner, repo, inferred_ref = parse_github_repo_spec_with_ref(repo_spec)
    ref_candidates = github_ref_candidates_from_repo_spec(repo_spec)
    resolved_ref = ref or inferred_ref
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    options = _resolved_onboarding_options(
        preset=preset,
        max_sources=max_sources,
        max_candidates=max_candidates,
        run_smoke_validation=run_smoke_validation,
    )
    onboarding, resolved_ref, effective_inferred_ref, ref_fallback_attempts = (
        _run_onboarding_tree_with_ref_fallback(
            owner,
            repo,
            resolved_ref,
            output_root,
            explicit_ref=ref,
            inferred_ref=inferred_ref,
            ref_candidates=ref_candidates,
            token=token,
            recursive=recursive,
            api_base_url=api_base_url,
            timeout=timeout,
            include=include,
            exclude=exclude,
            target_prefix=target_prefix,
            recipes=recipes,
            source_cache_dir=source_cache_dir,
            max_sources=options["max_sources"],
            max_candidates=options["max_candidates"],
            auto_dependency_sources=auto_dependency_sources,
            dependency_max_depth=dependency_max_depth,
            preset=preset,
            materialize_template=options["materialize_template"],
            run_benchmark=options["run_benchmark"],
            use_dynamic_coverage=options["use_dynamic_coverage"],
            run_quality_gate=options["run_quality_gate"],
            quality_gate_thresholds=options["quality_gate_thresholds"],
            run_showcase_lite=options["run_showcase_lite"],
            run_smoke_validation=options["run_smoke_validation"],
            repository_test_root=repository_test_root,
            repository_test_timeout=repository_test_timeout,
            repository_test_failure_overlay_candidate_limit=repository_test_failure_overlay_candidate_limit,
            repository_test_patch_validation_limit=repository_test_patch_validation_limit,
            repository_patch_generation_mode=repository_patch_generation_mode,
            repository_llm_patch_candidate_limit=repository_llm_patch_candidate_limit,
            repository_patch_candidate_variant_allowlist=repository_patch_candidate_variant_allowlist,
            repository_test_reflection_mode=repository_test_reflection_mode,
            repository_test_reflection_rounds=repository_test_reflection_rounds,
            repository_test_reflection_width=repository_test_reflection_width,
            patch_judge_mode=patch_judge_mode,
            run_repository_test_command=run_repository_test_command,
            run_repository_test_environment_setup=run_repository_test_environment_setup,
            run_repository_test_retry=run_repository_test_retry,
            run_repository_test_retry_prerequisites=run_repository_test_retry_prerequisites,
            auto_repository_test_retry=auto_repository_test_retry,
            auto_repository_test_retry_max_risk=auto_repository_test_retry_max_risk,
            auto_repository_test_retry_allowed_runners=(
                auto_repository_test_retry_allowed_runners
            ),
            repository_test_environment_setup_timeout=repository_test_environment_setup_timeout,
            checkout_repository_tests=checkout_repository_tests,
            repository_checkout_timeout=repository_checkout_timeout,
            repository_checkout_depth=repository_checkout_depth,
            prefer_cached_discovery=prefer_cached_discovery,
            opener=opener,
        )
    )
    report = _build_agent_report(
        repo_spec=repo_spec,
        owner=owner,
        repo=repo,
        explicit_ref=ref,
        inferred_ref=effective_inferred_ref,
        ref_fallback_attempts=ref_fallback_attempts,
        output_dir=output_root,
        preset=preset,
        onboarding=onboarding,
    )
    remediation_action = _agent_auto_benchmark_remediation_action(
        report.summary,
        enabled=auto_remediate_benchmark,
    )
    if remediation_action:
        _write_agent_snapshot(
            report,
            output_root / "github_repo_agent_pre_remediation.json",
            output_root / "github_repo_agent_pre_remediation.md",
        )
        remediated_onboarding = _run_onboarding_tree(
            owner,
            repo,
            resolved_ref,
            output_root,
            token=token,
            recursive=recursive,
            api_base_url=api_base_url,
            timeout=timeout,
            include=include,
            exclude=exclude,
            target_prefix=target_prefix,
            recipes=recipes,
            source_cache_dir=source_cache_dir,
            max_sources=options["max_sources"],
            max_candidates=options["max_candidates"],
            auto_dependency_sources=auto_dependency_sources,
            dependency_max_depth=dependency_max_depth,
            preset=preset,
            materialize_template=True,
            run_benchmark=True,
            use_dynamic_coverage=options["use_dynamic_coverage"],
            run_quality_gate=options["run_quality_gate"],
            quality_gate_thresholds=options["quality_gate_thresholds"],
            run_showcase_lite=options["run_showcase_lite"],
            run_smoke_validation=options["run_smoke_validation"],
            repository_test_root=repository_test_root,
            repository_test_timeout=repository_test_timeout,
            repository_test_failure_overlay_candidate_limit=repository_test_failure_overlay_candidate_limit,
            repository_test_patch_validation_limit=repository_test_patch_validation_limit,
            repository_patch_generation_mode=repository_patch_generation_mode,
            repository_llm_patch_candidate_limit=repository_llm_patch_candidate_limit,
            repository_patch_candidate_variant_allowlist=repository_patch_candidate_variant_allowlist,
            repository_test_reflection_mode=repository_test_reflection_mode,
            repository_test_reflection_rounds=repository_test_reflection_rounds,
            repository_test_reflection_width=repository_test_reflection_width,
            patch_judge_mode=patch_judge_mode,
            run_repository_test_command=run_repository_test_command,
            run_repository_test_environment_setup=run_repository_test_environment_setup,
            run_repository_test_retry=run_repository_test_retry,
            run_repository_test_retry_prerequisites=run_repository_test_retry_prerequisites,
            auto_repository_test_retry=auto_repository_test_retry,
            auto_repository_test_retry_max_risk=auto_repository_test_retry_max_risk,
            auto_repository_test_retry_allowed_runners=(
                auto_repository_test_retry_allowed_runners
            ),
            repository_test_environment_setup_timeout=repository_test_environment_setup_timeout,
            checkout_repository_tests=checkout_repository_tests,
            repository_checkout_timeout=repository_checkout_timeout,
            repository_checkout_depth=repository_checkout_depth,
            prefer_cached_discovery=prefer_cached_discovery,
            opener=opener,
        )
        remediated_report = _build_agent_report(
            repo_spec=repo_spec,
            owner=owner,
            repo=repo,
            explicit_ref=ref,
            inferred_ref=effective_inferred_ref,
            ref_fallback_attempts=ref_fallback_attempts,
            output_dir=output_root,
            preset=preset,
            onboarding=remediated_onboarding,
        )
        report = _merge_auto_remediation_report(
            primary=report,
            remediated=remediated_report,
            output_dir=output_root,
            action=remediation_action,
        )
    fallback_reason = _agent_fallback_reason(
        report.summary,
        enabled=auto_fallback,
        min_generated_candidates=fallback_min_generated_candidates,
    )
    if fallback_reason:
        _write_agent_snapshot(
            report,
            output_root / "github_repo_agent_primary.json",
            output_root / "github_repo_agent_primary.md",
        )
        fallback_output_root = output_root / "fallback"
        fallback_preset_value = fallback_preset or preset
        fallback_options = _resolved_onboarding_options(
            preset=fallback_preset_value,
            max_sources=_fallback_limit(
                fallback_max_sources,
                options["max_sources"],
                default=50,
            ),
            max_candidates=_fallback_limit(
                fallback_max_candidates,
                options["max_candidates"],
                default=20,
            ),
            run_smoke_validation=run_smoke_validation,
        )
        fallback_onboarding = _run_onboarding_tree(
            owner,
            repo,
            resolved_ref,
            fallback_output_root,
            token=token,
            recursive=recursive,
            api_base_url=api_base_url,
            timeout=timeout,
            include=include,
            exclude=exclude,
            target_prefix=target_prefix,
            recipes=fallback_recipes,
            source_cache_dir=source_cache_dir,
            max_sources=fallback_options["max_sources"],
            max_candidates=fallback_options["max_candidates"],
            auto_dependency_sources=auto_dependency_sources,
            dependency_max_depth=dependency_max_depth,
            preset=fallback_preset_value,
            materialize_template=fallback_options["materialize_template"],
            run_benchmark=fallback_options["run_benchmark"],
            use_dynamic_coverage=fallback_options["use_dynamic_coverage"],
            run_quality_gate=fallback_options["run_quality_gate"],
            quality_gate_thresholds=fallback_options["quality_gate_thresholds"],
            run_showcase_lite=fallback_options["run_showcase_lite"],
            run_smoke_validation=fallback_options["run_smoke_validation"],
            repository_test_root=repository_test_root,
            repository_test_timeout=repository_test_timeout,
            repository_test_failure_overlay_candidate_limit=repository_test_failure_overlay_candidate_limit,
            repository_test_patch_validation_limit=repository_test_patch_validation_limit,
            repository_patch_generation_mode=repository_patch_generation_mode,
            repository_llm_patch_candidate_limit=repository_llm_patch_candidate_limit,
            repository_patch_candidate_variant_allowlist=repository_patch_candidate_variant_allowlist,
            repository_test_reflection_mode=repository_test_reflection_mode,
            repository_test_reflection_rounds=repository_test_reflection_rounds,
            repository_test_reflection_width=repository_test_reflection_width,
            patch_judge_mode=patch_judge_mode,
            run_repository_test_command=run_repository_test_command,
            run_repository_test_environment_setup=run_repository_test_environment_setup,
            run_repository_test_retry=run_repository_test_retry,
            run_repository_test_retry_prerequisites=run_repository_test_retry_prerequisites,
            auto_repository_test_retry=auto_repository_test_retry,
            auto_repository_test_retry_max_risk=auto_repository_test_retry_max_risk,
            auto_repository_test_retry_allowed_runners=(
                auto_repository_test_retry_allowed_runners
            ),
            repository_test_environment_setup_timeout=repository_test_environment_setup_timeout,
            checkout_repository_tests=checkout_repository_tests,
            repository_checkout_timeout=repository_checkout_timeout,
            repository_checkout_depth=repository_checkout_depth,
            prefer_cached_discovery=prefer_cached_discovery,
            opener=opener,
        )
        fallback_report = _build_agent_report(
            repo_spec=repo_spec,
            owner=owner,
            repo=repo,
            explicit_ref=ref,
            inferred_ref=effective_inferred_ref,
            ref_fallback_attempts=ref_fallback_attempts,
            output_dir=fallback_output_root,
            preset=fallback_preset_value,
            onboarding=fallback_onboarding,
        )
        _write_agent_report(fallback_report)
        report = _merge_fallback_report(
            primary=report,
            fallback=fallback_report,
            output_dir=output_root,
            reason=fallback_reason,
            min_generated_candidates=fallback_min_generated_candidates,
        )
    _write_agent_report(report)
    return report


def render_github_repo_agent_markdown(report: GitHubRepoAgentReport) -> str:
    summary = report.summary
    github_error = _dict(summary.get("github_error"))
    repo_input = _dict(summary.get("repo_input"))
    lines = [
        "# GitHub Repo Agent Report",
        "",
        f"- Repo: `{report.owner}/{report.repo}`",
        f"- Input: `{report.repo_spec}`",
        f"- Input Kind: `{_markdown_cell(repo_input.get('kind') or 'unknown')}`",
        (
            "- Normalized Repo: "
            f"`{_markdown_cell(repo_input.get('normalized_repo') or f'{report.owner}/{report.repo}')}`"
        ),
        (
            "- Ref Selection Source: "
            f"`{_markdown_cell(repo_input.get('ref_selection_source') or 'none')}`"
        ),
        (
            "- URL Inferred Ref: "
            f"`{_markdown_cell(repo_input.get('url_inferred_ref') or 'none')}`"
        ),
        (
            "- Ref Fallback Used: "
            f"{str(bool(repo_input.get('ref_fallback_used', False))).lower()}"
        ),
        (
            "- Ref Fallback Attempts: "
            f"{_int(repo_input.get('ref_fallback_attempt_count', 0))}"
        ),
        f"- Repository Ref: `{_markdown_cell(summary.get('repository_ref') or 'default')}`",
        (
            "- Requested Ref: "
            f"`{_markdown_cell(summary.get('requested_ref') or 'default')}`"
        ),
        f"- Ref Source: `{_markdown_cell(summary.get('ref_source') or 'default_branch_discovery')}`",
        f"- Source Cache Dir: `{_markdown_cell(summary.get('source_cache_dir') or 'none')}`",
        f"- Discovery Source: `{_markdown_cell(summary.get('discovery_source') or summary.get('source') or 'github_tree')}`",
        (
            "- Discovery Cache Fallback: "
            f"{str(bool(summary.get('discovery_cache_fallback', False))).lower()}"
        ),
        (
            "- Discovery Cache Reuse: "
            f"{str(bool(summary.get('discovery_cache_reuse', False))).lower()}"
        ),
        (
            "- Discovery Cache Reuse Reason: "
            f"`{_markdown_cell(summary.get('discovery_cache_reuse_reason') or 'none')}`"
        ),
        (
            "- API Rate Limit Checkout Fallback: "
            f"{str(bool(summary.get('discovery_api_rate_limit_checkout_fallback', False))).lower()}"
        ),
        f"- Output Dir: `{report.output_dir}`",
        f"- Preset: `{report.preset}`",
        f"- Status: `{report.status}`",
        f"- Discovery Items: {_int(summary.get('discovery_items', 0))}",
        f"- Imported Sources: {_int(summary.get('imported_sources', 0))}",
        f"- Selected Sources: {_int(summary.get('selected_sources', 0))}",
        f"- Generated Candidates: {_int(summary.get('generated_candidates', 0))}",
        (
            "- Static Intelligence: "
            f"`{_markdown_cell(summary.get('static_intelligence_status') or 'none')}` "
            f"({_int(summary.get('static_intelligence_selected_signal_count', 0))}/"
            f"{_int(summary.get('static_intelligence_total_signal_count', 0))} signals)"
        ),
        f"- Recipe Selection: `{summary.get('recipe_selection_mode', '')}`",
        (
            "- Selected Recipes: "
            f"{', '.join(str(item) for item in _list(summary.get('selected_recipes')))}"
        ),
        f"- Test Sources: {_int(summary.get('test_source_count', 0))}",
        (
            "- Test Framework Signals: "
            f"{', '.join(str(item) for item in _list(summary.get('test_framework_signals'))) or 'none'}"
        ),
        (
            "- Framework Signals: "
            f"{', '.join(str(item) for item in _list(summary.get('framework_signals'))) or 'none'}"
        ),
        (
            "- Recommended Test Command: "
            f"`{_markdown_cell(summary.get('recommended_test_command') or 'none')}`"
        ),
        (
            "- Test Command Candidates: "
            f"{_int(summary.get('test_command_candidate_count', 0))}"
        ),
        (
            "- Top Test Command Runner: "
            f"`{_markdown_cell(summary.get('top_test_command_runner') or 'none')}`"
        ),
        (
            "- Top Test Command Reason: "
            f"`{_markdown_cell(summary.get('top_test_command_reason') or 'none')}`"
        ),
        (
            "- Recommended Target Prefix: "
            f"`{_markdown_cell(summary.get('recommended_target_prefix') or 'none')}`"
        ),
        (
            "- Repository Test Command Status: "
            f"`{_markdown_cell(summary.get('repository_test_command_status') or 'none')}`"
        ),
        (
            "- Repository Test Setup Doctor: "
            f"`{_markdown_cell(summary.get('repository_test_setup_doctor_status') or 'none')}`/"
            f"`{_markdown_cell(summary.get('repository_test_setup_doctor_blocker') or 'none')}`, "
            f"score={_float(summary.get('repository_test_setup_doctor_score', 0.0)):.4f}, "
            f"checks={_int(summary.get('repository_test_setup_doctor_passed_check_count', 0))}/"
            f"{_int(summary.get('repository_test_setup_doctor_check_count', 0))}, "
            f"warning={_int(summary.get('repository_test_setup_doctor_warning_check_count', 0))}, "
            f"blocked={_int(summary.get('repository_test_setup_doctor_blocked_check_count', 0))}"
        ),
        (
            "- Repository Test Setup Doctor Next Action: "
            f"{_markdown_cell(summary.get('repository_test_setup_doctor_next_action') or 'none')}"
        ),
        (
            "- Repository Test Environment Status: "
            f"`{_markdown_cell(summary.get('repository_test_environment_status') or 'none')}`"
        ),
        (
            "- Repository Config Snapshot: "
            f"`{_markdown_cell(summary.get('repository_config_snapshot_status') or 'none')}`/"
            f"`{_markdown_cell(summary.get('repository_config_snapshot_reason') or 'none')}`, "
            f"files={_int(summary.get('repository_config_snapshot_file_count', 0))}"
        ),
        (
            "- Repository Test Pytest Config: "
            f"sources={_int(summary.get('repository_test_pytest_config_source_count', 0))}, "
            f"testpaths={_markdown_cell(', '.join(str(item) for item in _list(summary.get('repository_test_pytest_testpaths'))) or 'none')}, "
            f"addopts={_markdown_cell(' '.join(str(item) for item in _list(summary.get('repository_test_pytest_addopts'))) or 'none')}"
        ),
        (
            "- Repository Test CI Config: "
            f"sources={_int(summary.get('repository_test_ci_config_source_count', 0))}, "
            f"python={_markdown_cell(', '.join(str(item) for item in _list(summary.get('repository_test_ci_python_versions'))) or 'none')}, "
            f"installs={_markdown_cell('; '.join(str(item) for item in _list(summary.get('repository_test_ci_install_command_candidates'))) or 'none')}, "
            f"tests={_markdown_cell('; '.join(str(item) for item in _list(summary.get('repository_test_ci_test_command_candidates'))) or 'none')}"
        ),
        (
            "- Repository Test Framework Configuration: "
            f"`{_markdown_cell(summary.get('repository_test_framework_configuration_status') or 'none')}`/"
            f"`{_markdown_cell(summary.get('repository_test_framework_configuration_reason') or 'none')}`"
        ),
        (
            "- Repository Test App Bootstrap Candidates: "
            f"{', '.join(str(item) for item in _list(summary.get('repository_test_app_bootstrap_candidates'))) or 'none'}"
        ),
        (
            "- Repository Test Environment Setup Status: "
            f"`{_markdown_cell(summary.get('repository_test_environment_setup_status') or 'none')}`"
        ),
        (
            "- Repository Test Environment Setup Result Status: "
            f"`{_markdown_cell(summary.get('repository_test_environment_setup_result_status') or 'none')}`"
        ),
        (
            "- Repository Test Environment Setup Result Executed: "
            f"{str(bool(summary.get('repository_test_environment_setup_result_executed', False))).lower()}"
        ),
        (
            "- Repository Test Environment Setup Install Failure: "
            f"`{_markdown_cell(summary.get('repository_test_environment_setup_install_failure_category') or 'none')}`"
        ),
        (
            "- Repository Test Environment Setup Install Fallback Executed: "
            f"{str(bool(summary.get('repository_test_environment_setup_install_fallback_executed', False))).lower()}"
        ),
        (
            "- Repository Test Execution Plan Status: "
            f"`{_markdown_cell(summary.get('repository_test_execution_plan_status') or 'none')}`"
        ),
        (
            "- Planned Repository Test Command: "
            f"`{_markdown_cell(summary.get('planned_repository_test_command') or 'none')}`"
        ),
        (
            "- Planned Repository Test Runner: "
            f"`{_markdown_cell(summary.get('planned_repository_test_runner') or 'none')}`"
        ),
        (
            "- Planned Repository Test Source: "
            f"`{_markdown_cell(summary.get('planned_repository_test_source') or 'none')}`"
        ),
        (
            "- Planned Repository Test Runner Fallback: "
            f"{str(bool(summary.get('planned_repository_test_runner_fallback_used', False))).lower()} "
            f"`{_markdown_cell(summary.get('planned_repository_test_runner_fallback_from') or 'none')}`"
            " -> "
            f"`{_markdown_cell(summary.get('planned_repository_test_runner_fallback_to') or 'none')}` "
            f"reason=`{_markdown_cell(summary.get('planned_repository_test_runner_fallback_reason') or 'none')}`"
        ),
        (
            "- Planned Repository Test CI Candidates: "
            f"{_int(summary.get('planned_repository_test_ci_candidate_count', 0))}"
        ),
        (
            "- Planned Repository Test Env Vars: "
            f"{', '.join(str(item) for item in _list(summary.get('planned_repository_test_environment_variable_names'))) or 'none'}"
        ),
        (
            "- Planned Repository Test Automatic Env Vars: "
            f"{', '.join(str(item) for item in _list(summary.get('planned_repository_test_automatic_env_var_names'))) or 'none'}"
        ),
        (
            "- Planned Repository Test Executable Now: "
            f"{str(bool(summary.get('planned_repository_test_executable_now', False))).lower()}"
        ),
        (
            "- Planned Repository Test Result Status: "
            f"`{_markdown_cell(summary.get('planned_repository_test_result_status') or 'none')}`"
        ),
        (
            "- Planned Repository Test Result Executed: "
            f"{str(bool(summary.get('planned_repository_test_result_executed', False))).lower()}"
        ),
        (
            "- Planned Repository Test Python: "
            f"`{_markdown_cell(summary.get('planned_repository_test_python_executable') or 'none')}`"
        ),
        (
            "- Planned Repository Test Python Source: "
            f"`{_markdown_cell(summary.get('planned_repository_test_python_source') or 'none')}`"
        ),
        (
            "- Planned Repository Test Failure Category: "
            f"`{_markdown_cell(summary.get('planned_repository_test_failure_category') or 'none')}`"
        ),
        (
            "- Planned Repository Test Failure Context Lines: "
            f"{_int(summary.get('planned_repository_test_failure_context_line_count', 0))}"
        ),
        (
            "- Repository Test Retry Recommended: "
            f"{str(bool(summary.get('repository_test_retry_recommended', False))).lower()}"
        ),
        (
            "- Repository Test Retry Strategy: "
            f"`{_markdown_cell(summary.get('repository_test_retry_strategy') or 'none')}`"
        ),
        (
            "- Repository Test Retry Command: "
            f"`{_markdown_cell(summary.get('repository_test_retry_command') or 'none')}`"
        ),
        (
            "- Repository Test Retry Execution Status: "
            f"`{_markdown_cell(summary.get('repository_test_retry_execution_status') or 'none')}`"
        ),
        (
            "- Repository Test Retry Executed: "
            f"{str(bool(summary.get('repository_test_retry_executed', False))).lower()}"
        ),
        (
            "- Repository Test Retry Setup Prerequisite Required: "
            f"{str(bool(summary.get('repository_test_retry_setup_prerequisite_required', False))).lower()}"
        ),
        (
            "- Repository Test Retry Setup Prerequisite Satisfied: "
            f"{str(bool(summary.get('repository_test_retry_setup_prerequisite_satisfied', False))).lower()}"
        ),
        (
            "- Repository Test Retry Setup Prerequisite Status: "
            f"`{_markdown_cell(summary.get('repository_test_retry_setup_prerequisite_status') or 'none')}`"
        ),
        (
            "- Repository Test Retry Setup Prerequisite Auto Executed: "
            f"{str(bool(summary.get('repository_test_retry_setup_prerequisite_auto_executed', False))).lower()}"
        ),
        (
            "- Repository Test Timeout Narrowing Status: "
            f"`{_markdown_cell(summary.get('repository_test_timeout_narrowing_status') or 'none')}`"
        ),
        (
            "- Repository Test Timeout Narrowing Executed: "
            f"{str(bool(summary.get('repository_test_timeout_narrowing_executed', False))).lower()}"
        ),
        (
            "- Repository Test Timeout Narrowing Attempts: "
            f"{_int(summary.get('repository_test_timeout_narrowing_attempt_count', 0))}"
        ),
        (
            "- Repository Test Timeout Narrowing Selected Command: "
            f"`{_markdown_cell(summary.get('repository_test_timeout_narrowing_selected_command') or 'none')}`"
        ),
        (
            "- Repository Test Dynamic Evidence Level: "
            f"`{_markdown_cell(summary.get('repository_test_dynamic_evidence_level') or 'none')}`"
        ),
        (
            "- Repository Test Dynamic Failing Tests: "
            f"{_int(summary.get('repository_test_dynamic_failing_tests', 0))}"
        ),
        (
            "- Repository Test Dynamic Traceback Frames: "
            f"{_int(summary.get('repository_test_dynamic_traceback_frames', 0))}"
        ),
        (
            "- Repository Test Evidence Usable For Localization: "
            f"{str(bool(summary.get('repository_test_dynamic_usable_for_localization', False))).lower()}"
        ),
        (
            "- Repository Test Evidence Usable For Patch Validation: "
            f"{str(bool(summary.get('repository_test_dynamic_usable_for_patch_validation', False))).lower()}"
        ),
        (
            "- Repository Test Evidence Usable For Regression Validation: "
            f"{str(bool(summary.get('repository_test_dynamic_usable_for_regression_validation', False))).lower()}"
        ),
        (
            "- Repository Test Dynamic Validation Command: "
            f"`{_markdown_cell(summary.get('repository_test_dynamic_validation_command') or 'none')}`"
        ),
        (
            "- Repository Test Analysis Source: "
            f"`{_markdown_cell(summary.get('repository_test_analysis_source') or 'none')}`"
        ),
        (
            "- Repository Test Overlay Trigger Reason: "
            f"`{_markdown_cell(summary.get('repository_test_overlay_trigger_reason') or 'none')}`"
        ),
        (
            "- Repository Test Phase 2 Ready: "
            f"{str(bool(summary.get('repository_test_phase2_ready', False))).lower()}"
        ),
        (
            "- Repository Test Phase 3 Validation Ready: "
            f"{str(bool(summary.get('repository_test_phase3_validation_ready', False))).lower()}"
        ),
        (
            "- Repository Test Final Status: "
            f"`{_markdown_cell(summary.get('repository_test_final_status') or 'none')}`"
        ),
        (
            "- Repository Test Final Reason: "
            f"`{_markdown_cell(summary.get('repository_test_final_reason') or 'none')}`"
        ),
        (
            "- Repository Test Failure Overlay Status: "
            f"`{_markdown_cell(summary.get('repository_test_failure_overlay_status') or 'none')}`"
        ),
        (
            "- Repository Test Failure Overlay Rule: "
            f"`{_markdown_cell(summary.get('repository_test_failure_overlay_selected_rule') or 'none')}`"
        ),
        (
            "- Repository Test Failure Overlay Function: "
            f"`{_markdown_cell(summary.get('repository_test_failure_overlay_selected_function') or 'none')}`"
        ),
        (
            "- Repository Test Failure Overlay Attempts: "
            f"{_int(summary.get('repository_test_failure_overlay_attempted_cases', 0))}"
        ),
        (
            "- Repository Test Failure Overlay Candidate Limit: "
            f"{_int(summary.get('repository_test_failure_overlay_candidate_limit', 0))}"
        ),
        (
            "- Repository Test Failure Overlay Candidate Rules: "
            f"`{_markdown_cell(_format_counts(_dict(summary.get('repository_test_failure_overlay_candidate_rule_counts'))))}`"
        ),
        (
            "- Repository Test Failure Overlay Triggered Rules: "
            f"`{_markdown_cell(_format_counts(_dict(summary.get('repository_test_failure_overlay_triggered_rule_counts'))))}`"
        ),
        (
            "- Repository Test Failure Overlay Candidate Rejections: "
            f"{_int(summary.get('repository_test_failure_overlay_candidate_rejection_count', 0))}"
        ),
        (
            "- Repository Test Failure Overlay Candidate Rejection Counts: "
            f"`{_markdown_cell(_format_counts(_dict(summary.get('repository_test_failure_overlay_candidate_rejection_counts'))))}`"
        ),
        (
            "- Repository Test Failure Overlay Candidate Rejection Examples: "
            f"`{_markdown_cell(_format_rejection_examples(_list(summary.get('repository_test_failure_overlay_candidate_rejection_examples'))))}`"
        ),
        (
            "- Repository Test Failure Overlay Dominant Rejection: "
            f"`{_markdown_cell(_format_overlay_dominant_rejection(summary))}`"
        ),
        (
            "- Repository Test Failure Overlay Next Extension: "
            f"`{_markdown_cell(_format_overlay_next_extension(summary))}`"
        ),
        (
            "- Repository Test Failure Overlay Next Actionable Extension: "
            f"`{_markdown_cell(_format_overlay_next_actionable_extension(summary))}`"
        ),
        (
            "- Repository Test Failure Overlay Selected Rank: "
            f"{_int(summary.get('repository_test_failure_overlay_selected_candidate_rank', 0))}"
        ),
        (
            "- Repository Test Failure Overlay Selected Score: "
            f"{_float(summary.get('repository_test_failure_overlay_selected_score', 0.0)):.4f}"
        ),
        (
            "- Repository Test Failure Overlay Average Candidate Score: "
            f"{_float(summary.get('repository_test_failure_overlay_average_candidate_score', 0.0)):.4f}"
        ),
        (
            "- Repository Test Failure Overlay Selected Score Breakdown: "
            f"`{_markdown_cell(_format_score_breakdown(_dict(summary.get('repository_test_failure_overlay_selected_score_breakdown'))))}`"
        ),
        (
            "- Repository Test Failure Overlay Candidate Score Preview: "
            f"`{_markdown_cell(_format_score_preview(_list(summary.get('repository_test_failure_overlay_candidate_score_preview'))))}`"
        ),
        (
            "- Repository Test Failure Overlay Evidence Level: "
            f"`{_markdown_cell(summary.get('repository_test_failure_overlay_dynamic_evidence_level') or 'none')}`"
        ),
        (
            "- Repository Test Failure Overlay Validation Command: "
            f"`{_markdown_cell(summary.get('repository_test_failure_overlay_validation_command') or 'none')}`"
        ),
        (
            "- Repository Test Fault Localization Status: "
            f"`{_markdown_cell(summary.get('repository_test_fault_localization_status') or 'none')}`"
        ),
        (
            "- Repository Test Fault Localization Rankings: "
            f"{_int(summary.get('repository_test_fault_localization_ranking_count', 0))}"
        ),
        (
            "- Repository Test Fault Localization Top Function: "
            f"`{_markdown_cell(summary.get('repository_test_fault_localization_top_function') or 'none')}`"
        ),
        (
            "- Repository Test Patch Candidates Status: "
            f"`{_markdown_cell(summary.get('repository_test_patch_candidates_status') or 'none')}`"
        ),
        (
            "- Repository Test Patch Candidate Count: "
            f"{_int(summary.get('repository_test_patch_candidate_count', 0))}"
        ),
        (
            "- Repository Test Patch Validation Status: "
            f"`{_markdown_cell(summary.get('repository_test_patch_validation_status') or 'none')}`"
        ),
        (
            "- Repository Test Patch Validation Candidates: "
            f"input={_int(summary.get('repository_test_patch_validation_input_candidate_count', 0))}, "
            f"validated={_int(summary.get('repository_test_patch_validation_candidate_count', 0))}, "
            f"safety_blocked={_int(summary.get('repository_test_patch_validation_safety_blocked_candidate_count', 0))}"
        ),
        (
            "- Repository Test Patch Validation Successes: "
            f"{_int(summary.get('repository_test_patch_validation_success_count', 0))}"
        ),
        (
            "- Repository Test Repair Ready: "
            f"{str(bool(summary.get('repository_test_repair_ready', False))).lower()}"
        ),
        (
            "- Repository Test Repair Validation Scope: "
            f"`{_markdown_cell(summary.get('repository_test_repair_validation_scope') or 'none')}`"
        ),
        (
            "- Repository Test Regression Validation: "
            f"`{_markdown_cell(summary.get('repository_test_regression_validation_status') or 'none')}`/"
            f"`{_markdown_cell(summary.get('repository_test_regression_validation_reason') or 'none')}`"
        ),
        (
            "- Repository Test Regression Command: "
            f"`{_markdown_cell(summary.get('repository_test_regression_validation_command') or 'none')}`"
        ),
                (
                    "- Repository Test Patch Validation Reflection Successes: "
                    f"{_int(summary.get('repository_test_patch_validation_successful_reflection_count', 0))}"
                ),
                (
                    "- Repository Test Patch Validation Regression Reflection Successes: "
                    f"{_int(summary.get('repository_test_patch_validation_successful_regression_reflection_count', 0))}"
                ),
                (
                    "- Repository Test Patch Validation Reflection Mode: "
                    f"`{_markdown_cell(summary.get('repository_test_patch_validation_reflection_mode') or 'none')}`"
                ),
        (
            "- Repository Test Patch Validation Refiner Status: "
            f"`{_markdown_cell(summary.get('repository_test_patch_validation_refiner_status') or 'none')}`"
        ),
        (
            "- Repository Test Patch Validation Max Depth: "
            f"{_int(summary.get('repository_test_patch_validation_max_depth', 0))}"
        ),
        (
            "- Repository Test Patch Judge: "
            f"`{_markdown_cell(summary.get('repository_test_patch_judge_mode') or 'none')}`/"
            f"`{_markdown_cell(summary.get('repository_test_patch_judge_status') or 'disabled')}` "
            f"judged={_int(summary.get('repository_test_patch_judge_candidate_count', 0))}, "
            f"authority=`{_markdown_cell(summary.get('repository_test_patch_judge_authority') or 'sandbox_pytest_decides_success')}`"
        ),
        (
            "- Repository Test Reflection Trace: "
            f"`{_markdown_cell(summary.get('repository_test_reflection_trace_status') or 'none')}`/"
            f"`{_markdown_cell(summary.get('repository_test_reflection_trace_reason') or 'none')}`"
        ),
        (
            "- Repository Test Reflection Steps: "
            f"initial_failures={_int(summary.get('repository_test_reflection_trace_initial_failure_count', 0))}, "
            f"steps={_int(summary.get('repository_test_reflection_trace_step_count', 0))}, "
            f"successful={_int(summary.get('repository_test_reflection_trace_successful_step_count', 0))}"
        ),
        (
            "- Repository Test Reflection Trace Path: "
            f"`{_markdown_cell(summary.get('reflection_trace_markdown') or 'none')}`"
        ),
        (
            "- Repository Test Best Patch Candidate: "
            f"`{_markdown_cell(summary.get('repository_test_best_patch_candidate_id') or 'none')}`"
        ),
        (
            "- Repository Test Best Patch File: "
            f"`{_markdown_cell(summary.get('repository_test_best_patch_relative_file_path') or 'none')}`"
        ),
        (
            "- Repository Test Repair Patch: "
            f"`{_markdown_cell(summary.get('repository_test_repair_patch_path') or 'none')}`"
        ),
        (
            "- Repository Test Repair Summary: "
            f"`{_markdown_cell(summary.get('repository_test_repair_summary_status') or 'none')}`/"
            f"`{_markdown_cell(summary.get('repository_test_repair_summary_reason') or 'none')}`"
        ),
        (
            "- Repository Test Repair Summary Conclusion: "
            f"`{_markdown_cell(summary.get('repository_test_repair_summary_conclusion') or 'none')}`"
        ),
        (
            "- Repository Test Repair Summary Path: "
            f"`{_markdown_cell(summary.get('repository_test_repair_summary_path') or 'none')}`"
        ),
        (
            "- Recommended Install Command: "
            f"`{_markdown_cell(summary.get('recommended_install_command') or 'none')}`"
        ),
        (
            "- Repository Checkout Status: "
            f"`{_markdown_cell(summary.get('repository_checkout_status') or 'none')}`"
        ),
        (
            "- Repository Checkout Method: "
            f"`{_markdown_cell(summary.get('repository_checkout_method') or 'none')}`"
        ),
        (
            "- Repository Checkout Source Files: "
            f"{_int(summary.get('repository_checkout_source_count', 0))}"
        ),
        (
            "- Repository Test Command Executed: "
            f"{str(bool(summary.get('repository_test_command_executed', False))).lower()}"
        ),
        f"- Ready For Benchmark: {str(bool(summary.get('ready_for_benchmark', False))).lower()}",
        (
            "- Benchmarkization: "
            f"`{_markdown_cell(summary.get('benchmarkization_status') or 'none')}` "
            f"(ready={str(bool(summary.get('benchmarkization_ready', False))).lower()})"
        ),
        (
            "- Benchmarkization Remediation Plan: "
            f"`{_markdown_cell(summary.get('benchmarkization_remediation_plan_markdown') or 'none')}`"
        ),
        f"- Benchmark Cases: {_int(summary.get('benchmark_cases', 0))}",
        f"- Top-1: {_float(summary.get('top1', 0.0)):.4f}",
        f"- MAP: {_float(summary.get('map', 0.0)):.4f}",
        f"- Patch Success Rate: {_float(summary.get('patch_success_rate', 0.0)):.4f}",
        f"- Quality Gate: {_status_word(summary.get('quality_gate_passed'))}",
        f"- Smoke Validation: {_status_word(summary.get('smoke_validation_passed'))}",
        f"- Diagnostics: `{summary.get('diagnostics_status', '')}`",
        f"- First Failing Stage: `{summary.get('first_failing_stage', '')}`",
        (
            "- Diagnostic Issues: "
            f"errors={_int(summary.get('diagnostic_error_count', 0))}, "
            f"warnings={_int(summary.get('diagnostic_warning_count', 0))}, "
            f"info={_int(summary.get('diagnostic_info_count', 0))}"
        ),
        (
            "- Diagnostic Codes: "
            f"{', '.join(str(item) for item in _list(summary.get('diagnostic_issue_codes'))) or 'none'}"
        ),
        (
            "- Agent Plan Statuses: "
            f"`{_markdown_cell(_format_counts(_dict(summary.get('agent_execution_plan_status_counts'))))}`"
        ),
        (
            "- Agent Plan Primary Blocker: "
            f"`{_markdown_cell(summary.get('agent_execution_plan_primary_blocker') or 'none')}`"
        ),
        (
            "- Agent Plan Next Action: "
            f"{_markdown_cell(summary.get('agent_execution_plan_next_action') or 'none')}"
        ),
        (
            "- Agent Plan Next Command: "
            f"`{_markdown_cell(summary.get('agent_execution_plan_next_command') or 'none')}`"
        ),
        "",
    ]
    if summary.get("static_intelligence_status"):
        lines.extend(
            [
                "## Static Intelligence",
                "",
                (
                    "- Status: "
                    f"`{_markdown_cell(summary.get('static_intelligence_status') or 'none')}`/"
                    f"`{_markdown_cell(summary.get('static_intelligence_level') or 'none')}`"
                ),
                (
                    "- Reason: "
                    f"`{_markdown_cell(summary.get('static_intelligence_reason') or 'none')}`"
                ),
                (
                    "- Source Scope: "
                    f"imported={_int(summary.get('static_intelligence_imported_source_count', 0))}, "
                    f"selected={_int(summary.get('static_intelligence_selected_source_count', 0))}, "
                    f"hit_rate={_float(summary.get('static_intelligence_source_hit_rate', 0.0)):.4f}"
                ),
                (
                    "- Static Signals: "
                    f"selected={_int(summary.get('static_intelligence_selected_signal_count', 0))}, "
                    f"total={_int(summary.get('static_intelligence_total_signal_count', 0))}, "
                    f"density={_float(summary.get('static_intelligence_candidate_density', 0.0)):.4f}"
                ),
                (
                    "- Rule Counts: "
                    f"`{_markdown_cell(_format_counts(_dict(summary.get('static_intelligence_rule_counts'))))}`"
                ),
                (
                    "- Bug Type Counts: "
                    f"`{_markdown_cell(_format_counts(_dict(summary.get('static_intelligence_bug_type_counts'))))}`"
                ),
                (
                    "- Quality: "
                    f"score={_float(summary.get('static_intelligence_quality_score', 0.0)):.4f}, "
                    f"rule_diversity={_int(summary.get('static_intelligence_rule_diversity', 0))}, "
                    f"bug_type_diversity={_int(summary.get('static_intelligence_bug_type_diversity', 0))}"
                ),
                (
                    "- Dynamic Validation: "
                    f"`{_markdown_cell(summary.get('static_intelligence_dynamic_validation_level') or 'none')}`"
                ),
                (
                    "- Primary Artifact: "
                    f"`{_markdown_cell(summary.get('static_intelligence_primary_artifact') or 'none')}`"
                ),
                (
                    "- Next Action: "
                    f"{_markdown_cell(summary.get('static_intelligence_next_action') or 'none')}"
                ),
                "",
            ]
        )
    plan_rows = _list(summary.get("agent_execution_plan"))
    if plan_rows:
        lines.extend(
            [
                "## Agent Execution Plan",
                "",
                "| Stage | Status | Blocker | Evidence | Next Action | Command | Artifact |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row_value in plan_rows:
            row = _dict(row_value)
            lines.append(
                "| "
                f"{_markdown_cell(row.get('stage', ''))} | "
                f"{_markdown_cell(row.get('status', ''))} | "
                f"{_markdown_cell(row.get('blocker', 'none'))} | "
                f"{_markdown_cell(row.get('evidence', ''))} | "
                f"{_markdown_cell(row.get('next_action', '') or 'none')} | "
                f"`{_markdown_cell(row.get('command', '') or 'none')}` | "
                f"`{_markdown_cell(row.get('artifact', '') or 'none')}` |"
            )
        lines.append("")
    if summary.get("fallback_attempted"):
        lines.extend(
            [
                "## Auto Fallback",
                "",
                f"- Reason: `{_markdown_cell(summary.get('fallback_reason') or 'none')}`",
                (
                    "- Used As Final Report: "
                    f"{str(bool(summary.get('fallback_used', False))).lower()}"
                ),
                (
                    "- Improved: "
                    f"{str(bool(summary.get('fallback_improved', False))).lower()}"
                ),
                (
                    "- Recovered: "
                    f"{str(bool(summary.get('fallback_recovered', False))).lower()}"
                ),
                (
                    "- Min Generated Candidates: "
                    f"{_int(summary.get('fallback_min_generated_candidates', 0))}"
                ),
                (
                    "- Primary Generated Candidates: "
                    f"{_int(summary.get('primary_generated_candidates', 0))}"
                ),
                (
                    "- Fallback Generated Candidates: "
                    f"{_int(summary.get('fallback_generated_candidates', 0))}"
                ),
                (
                    "- Primary Benchmarkization: "
                    f"`{_markdown_cell(summary.get('primary_benchmarkization_status') or 'none')}`"
                ),
                (
                    "- Fallback Benchmarkization: "
                    f"`{_markdown_cell(summary.get('fallback_benchmarkization_status') or 'none')}`"
                ),
                (
                    "- Primary Benchmarkization Action: "
                    f"`{_markdown_cell(summary.get('primary_benchmarkization_primary_action_id') or 'none')}`"
                ),
                (
                    "- Fallback Benchmarkization Action: "
                    f"`{_markdown_cell(summary.get('fallback_benchmarkization_primary_action_id') or 'none')}`"
                ),
                (
                    "- Primary Remediation Plan: "
                    f"`{_markdown_cell(summary.get('primary_benchmarkization_remediation_plan_markdown') or 'none')}`"
                ),
                (
                    "- Fallback Remediation Plan: "
                    f"`{_markdown_cell(summary.get('fallback_benchmarkization_remediation_plan_markdown') or 'none')}`"
                ),
                (
                    "- Primary Output Dir: "
                    f"`{_markdown_cell(summary.get('primary_output_dir') or 'none')}`"
                ),
                (
                    "- Fallback Output Dir: "
                    f"`{_markdown_cell(summary.get('fallback_output_dir') or 'none')}`"
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## Benchmarkization",
            "",
            f"- Status: `{_markdown_cell(summary.get('benchmarkization_status') or 'none')}`",
            f"- Stage: `{_markdown_cell(summary.get('benchmarkization_stage') or 'none')}`",
            f"- Ready: {str(bool(summary.get('benchmarkization_ready', False))).lower()}",
            (
                "- Blocking Reasons: "
                f"`{_markdown_cell(', '.join(str(item) for item in _list(summary.get('benchmarkization_blocking_reasons'))) or 'none')}`"
            ),
            (
                "- Primary Remediation Action: "
                f"`{_markdown_cell(summary.get('benchmarkization_primary_action_id') or 'none')}`"
            ),
            (
                "- Primary Remediation Command: "
                f"`{_markdown_cell(summary.get('benchmarkization_primary_action_command') or 'none')}`"
            ),
            (
                "- Primary Remediation Risk: "
                f"`{_markdown_cell(summary.get('benchmarkization_primary_action_risk') or 'none')}`"
            ),
            (
                "- Primary Remediation Requires: "
                f"`{_markdown_cell(', '.join(str(item) for item in _list(summary.get('benchmarkization_primary_action_requires'))) or 'none')}`"
            ),
            (
                "- Primary Remediation Expected Outcome: "
                f"{_markdown_cell(summary.get('benchmarkization_primary_action_expected_outcome') or 'none')}"
            ),
            (
                "- Remediation Plan JSON: "
                f"`{_markdown_cell(summary.get('benchmarkization_remediation_plan_json') or 'none')}`"
            ),
            (
                "- Remediation Plan Markdown: "
                f"`{_markdown_cell(summary.get('benchmarkization_remediation_plan_markdown') or 'none')}`"
            ),
            "",
        ]
    )
    if summary.get("auto_remediation_attempted"):
        lines.extend(
            [
                "## Auto Remediation",
                "",
                f"- Attempted: {str(bool(summary.get('auto_remediation_attempted'))).lower()}",
                f"- Used As Final Report: {str(bool(summary.get('auto_remediation_used'))).lower()}",
                f"- Improved: {str(bool(summary.get('auto_remediation_improved'))).lower()}",
                (
                    "- Action: "
                    f"`{_markdown_cell(summary.get('auto_remediation_action_id') or 'none')}`"
                ),
                (
                    "- Command: "
                    f"`{_markdown_cell(summary.get('auto_remediation_command') or 'none')}`"
                ),
                (
                    "- Primary Benchmarkization: "
                    f"`{_markdown_cell(summary.get('primary_benchmarkization_status') or 'none')}`"
                ),
                (
                    "- Remediated Benchmarkization: "
                    f"`{_markdown_cell(summary.get('remediated_benchmarkization_status') or 'none')}`"
                ),
                (
                    "- Remediated Benchmark Cases: "
                    f"{_int(summary.get('remediated_benchmark_cases', 0))}"
                ),
                "",
            ]
        )
    if github_error:
        lines.extend(
            [
                "## GitHub Error",
                "",
                f"- Status Code: `{_markdown_cell(github_error.get('status_code'))}`",
                f"- URL: `{_markdown_cell(github_error.get('url', ''))}`",
                (
                    "- Rate Limit Remaining: "
                    f"`{_markdown_cell(github_error.get('rate_limit_remaining'))}`"
                ),
                (
                    "- Rate Limit Reset: "
                    f"`{_markdown_cell(github_error.get('rate_limit_reset'))}`"
                ),
                f"- Message: {_markdown_cell(github_error.get('message', ''))}",
                "",
            ]
        )
    recipe_suggestions = _list(summary.get("recipe_suggestion_preview"))
    if recipe_suggestions:
        lines.extend(
            [
                "## Recipe Suggestions",
                "",
                "| Recipe | Misses | Top Reasons | Suggested Actions |",
                "| --- | ---: | --- | --- |",
            ]
        )
        for suggestion_value in recipe_suggestions:
            suggestion = _dict(suggestion_value)
            reasons = ", ".join(
                str(reason.get("reason", ""))
                for reason in _list(suggestion.get("top_reasons"))
            )
            actions = "; ".join(
                str(action) for action in _list(suggestion.get("suggested_actions"))
            )
            lines.append(
                "| "
                f"{_markdown_cell(suggestion.get('recipe', ''))} | "
                f"{_int(suggestion.get('miss_count', 0))} | "
                f"{_markdown_cell(reasons)} | "
                f"{_markdown_cell(actions)} |"
            )
        lines.append("")
    lines.extend(["## Next Actions", ""])
    for action in _list(summary.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(summary.get("next_actions")):
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            "| Artifact | Path |",
            "| --- | --- |",
        ]
    )
    for name, path in report.output_paths.items():
        lines.append(f"| {_markdown_cell(name)} | `{_markdown_cell(path)}` |")
    return "\n".join(lines)


def render_github_repo_agent_execution_plan_markdown(
    report: GitHubRepoAgentReport,
) -> str:
    summary = report.summary
    lines = [
        "# GitHub Repo Agent Execution Plan",
        "",
        f"- Repo: `{report.owner}/{report.repo}`",
        f"- Input: `{report.repo_spec}`",
        f"- Output Dir: `{report.output_dir}`",
        f"- Preset: `{report.preset}`",
        (
            "- Status Counts: "
            f"`{_markdown_cell(_format_counts(_dict(summary.get('agent_execution_plan_status_counts'))))}`"
        ),
        (
            "- Primary Blocker: "
            f"`{_markdown_cell(summary.get('agent_execution_plan_primary_blocker') or 'none')}`"
        ),
        (
            "- Next Action: "
            f"{_markdown_cell(summary.get('agent_execution_plan_next_action') or 'none')}"
        ),
        (
            "- Next Command: "
            f"`{_markdown_cell(summary.get('agent_execution_plan_next_command') or 'none')}`"
        ),
        "",
        "## Stages",
        "",
        "| Stage | Status | Blocker | Evidence | Next Action | Command | Artifact |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row_value in _list(summary.get("agent_execution_plan")):
        row = _dict(row_value)
        lines.append(
            "| "
            f"{_markdown_cell(row.get('stage', ''))} | "
            f"{_markdown_cell(row.get('status', ''))} | "
            f"{_markdown_cell(row.get('blocker', 'none'))} | "
            f"{_markdown_cell(row.get('evidence', ''))} | "
            f"{_markdown_cell(row.get('next_action', '') or 'none')} | "
            f"`{_markdown_cell(row.get('command', '') or 'none')}` | "
            f"`{_markdown_cell(row.get('artifact', '') or 'none')}` |"
        )
    if not _list(summary.get("agent_execution_plan")):
        lines.append("| none | unknown | none | none | none | `none` | `none` |")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one-command GitHub repository code-intelligence onboarding: "
            "repo discovery, source mining, benchmark smoke run, quality gate "
            "and agent summary."
        )
    )
    parser.add_argument("repo", help="GitHub owner/repo or github.com URL.")
    parser.add_argument("output_dir", help="Directory for agent artifacts.")
    parser.add_argument("--ref", help="Commit, tag, or branch. Defaults to default_branch.")
    parser.add_argument("--preset", choices=["smoke", "mining"], default="smoke")
    parser.add_argument("--include", action="append")
    parser.add_argument("--exclude", action="append")
    parser.add_argument("--target-prefix", default="")
    parser.add_argument("--recipe", action="append")
    parser.add_argument("--source-cache-dir")
    parser.add_argument("--max-sources", type=int)
    parser.add_argument("--max-candidates", type=int)
    parser.add_argument(
        "--no-auto-fallback",
        action="store_true",
        help=(
            "Disable automatic fallback rerun when the primary repo-agent run "
            "does not generate enough benchmark candidates."
        ),
    )
    parser.add_argument(
        "--fallback-min-generated-candidates",
        type=int,
        default=1,
        help=(
            "Minimum generated candidates required to avoid fallback. The "
            "default only retries zero-candidate runs."
        ),
    )
    parser.add_argument("--fallback-max-sources", type=int)
    parser.add_argument("--fallback-max-candidates", type=int)
    parser.add_argument("--fallback-preset", choices=["smoke", "mining"])
    parser.add_argument(
        "--auto-remediate-benchmark",
        action="store_true",
        help=(
            "When benchmarkization readiness exposes a low-risk auto-runnable "
            "benchmark action, execute it and refresh the agent report."
        ),
    )
    parser.add_argument(
        "--fallback-recipe",
        action="append",
        help=(
            "Recipe constraint for fallback. Omit to remove primary --recipe "
            "constraints and let auto recipe selection choose."
        ),
    )
    parser.add_argument("--no-recursive", action="store_true")
    parser.add_argument("--no-auto-dependency-sources", action="store_true")
    parser.add_argument(
        "--dependency-max-depth",
        type=int,
        default=DEFAULT_DEPENDENCY_MAX_DEPTH,
    )
    parser.add_argument(
        "--no-smoke-validation",
        action="store_true",
        help="Disable smoke validation for smoke preset.",
    )
    parser.add_argument(
        "--checkout-repository-tests",
        action="store_true",
        help="Shallow-clone the full repository before validating the recommended test command.",
    )
    parser.add_argument("--repository-test-root")
    parser.add_argument("--repository-test-timeout", type=int, default=20)
    parser.add_argument(
        "--repository-test-failure-overlay-candidate-limit",
        type=int,
        default=5,
        help=(
            "Maximum generated failure-overlay candidates to attempt when "
            "repository tests do not provide localization-ready evidence."
        ),
    )
    parser.add_argument(
        "--repository-test-patch-validation-limit",
        type=int,
        default=5,
        help="Maximum repository-test patch candidates to validate before reflection.",
    )
    parser.add_argument(
        "--repository-patch-generation-mode",
        choices=["rule", "llm", "hybrid"],
        default="rule",
        help=(
            "Patch candidate source for repository repair: rule-based, LLM, "
            "or hybrid rule+LLM. LLM mode reads CIA_LLM_* env vars."
        ),
    )
    parser.add_argument("--repository-llm-patch-candidate-limit", type=int)
    parser.add_argument(
        "--repository-patch-candidate-variant",
        action="append",
        default=[],
        help=(
            "Restrict repository patch candidates to a rule/LLM variant name; "
            "repeat to allow multiple variants. This is mainly for auditable "
            "reflection hard-case probes."
        ),
    )
    parser.add_argument(
        "--repository-test-reflection-mode",
        choices=["rule", "llm", "none"],
        default="rule",
    )
    parser.add_argument("--repository-test-reflection-rounds", type=int, default=1)
    parser.add_argument("--repository-test-reflection-width", type=int, default=1)
    parser.add_argument(
        "--patch-judge-mode",
        choices=["none", "llm"],
        default="none",
        help=(
            "Optional patch-level LLM judge for repository repair validation. "
            "Judge scores are audit/ranking signals; sandbox pytest remains authoritative."
        ),
    )
    parser.add_argument(
        "--no-repository-test-command",
        action="store_true",
        help="Do not write repository_test_command validation artifacts.",
    )
    parser.add_argument(
        "--run-repository-test-environment-setup",
        action="store_true",
        help=(
            "Create the isolated repository test venv and run supported pip "
            "install commands before planned test execution."
        ),
    )
    parser.add_argument(
        "--run-repository-test-retry",
        action="store_true",
        help="Execute the safe retry command recommended by repository_test_retry_plan.",
    )
    parser.add_argument(
        "--run-repository-test-retry-prerequisites",
        action="store_true",
        help=(
            "When retry requires repository test environment setup, execute the "
            "supported setup prerequisite before the retry command."
        ),
    )
    parser.add_argument(
        "--auto-repository-test-retry",
        action="store_true",
        help=(
            "Automatically execute a recommended repository test retry when "
            "its risk is within --auto-repository-test-retry-max-risk."
        ),
    )
    parser.add_argument(
        "--auto-repository-test-retry-max-risk",
        choices=["low", "medium", "high"],
        default="low",
        help="Maximum retry risk allowed for --auto-repository-test-retry.",
    )
    parser.add_argument(
        "--auto-repository-test-retry-runner",
        action="append",
        default=[],
        help=(
            "Restrict --auto-repository-test-retry to a python -m runner; "
            "repeat for multiple allowed runners such as pytest or unittest."
        ),
    )
    parser.add_argument(
        "--repository-test-environment-setup-timeout",
        type=int,
        default=120,
    )
    parser.add_argument("--repository-checkout-timeout", type=int, default=120)
    parser.add_argument("--repository-checkout-depth", type=int, default=1)
    parser.add_argument(
        "--prefer-cached-discovery",
        action="store_true",
        help=(
            "Reuse an existing output_dir/discovery.json before calling the "
            "GitHub tree API when it matches the requested repository/ref."
        ),
    )
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--api-base-url", default="https://api.github.com")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument(
        "--require-success",
        action="store_true",
        help="Exit non-zero unless the agent status is pass.",
    )
    return parser


def main(argv: list[str] | None = None, opener=None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        report = run_github_repo_agent(
            args.repo,
            args.output_dir,
            ref=args.ref,
            token=_token_from_env(args.token_env),
            recursive=not args.no_recursive,
            api_base_url=args.api_base_url,
            timeout=args.timeout,
            include=args.include,
            exclude=args.exclude,
            target_prefix=args.target_prefix,
            recipes=args.recipe,
            source_cache_dir=args.source_cache_dir,
            max_sources=args.max_sources,
            max_candidates=args.max_candidates,
            auto_dependency_sources=not args.no_auto_dependency_sources,
            dependency_max_depth=args.dependency_max_depth,
            preset=args.preset,
            run_smoke_validation=(False if args.no_smoke_validation else None),
            repository_test_root=args.repository_test_root,
            repository_test_timeout=args.repository_test_timeout,
            repository_test_failure_overlay_candidate_limit=args.repository_test_failure_overlay_candidate_limit,
            repository_test_patch_validation_limit=args.repository_test_patch_validation_limit,
            repository_patch_generation_mode=args.repository_patch_generation_mode,
            repository_llm_patch_candidate_limit=args.repository_llm_patch_candidate_limit,
            repository_patch_candidate_variant_allowlist=(
                args.repository_patch_candidate_variant
            ),
            repository_test_reflection_mode=args.repository_test_reflection_mode,
            repository_test_reflection_rounds=args.repository_test_reflection_rounds,
            repository_test_reflection_width=args.repository_test_reflection_width,
            patch_judge_mode=args.patch_judge_mode,
            run_repository_test_command=not args.no_repository_test_command,
            run_repository_test_environment_setup=args.run_repository_test_environment_setup,
            run_repository_test_retry=args.run_repository_test_retry,
            run_repository_test_retry_prerequisites=(
                args.run_repository_test_retry_prerequisites
            ),
            auto_repository_test_retry=args.auto_repository_test_retry,
            auto_repository_test_retry_max_risk=args.auto_repository_test_retry_max_risk,
            auto_repository_test_retry_allowed_runners=(
                args.auto_repository_test_retry_runner
            ),
            repository_test_environment_setup_timeout=args.repository_test_environment_setup_timeout,
            checkout_repository_tests=args.checkout_repository_tests,
            repository_checkout_timeout=args.repository_checkout_timeout,
            repository_checkout_depth=args.repository_checkout_depth,
            prefer_cached_discovery=args.prefer_cached_discovery,
            auto_fallback=not args.no_auto_fallback,
            fallback_min_generated_candidates=args.fallback_min_generated_candidates,
            fallback_max_sources=args.fallback_max_sources,
            fallback_max_candidates=args.fallback_max_candidates,
            fallback_preset=args.fallback_preset,
            fallback_recipes=args.fallback_recipe,
            auto_remediate_benchmark=args.auto_remediate_benchmark,
            opener=opener,
        )
    except ValueError as exc:
        parser.error(str(exc))
    except GitHubAPIError as exc:
        report = _build_fetch_error_report(
            repo_spec=args.repo,
            output_dir=Path(args.output_dir),
            preset=args.preset,
            error=exc,
        )
        _write_agent_report(report)
        _write_requested_outputs(
            report,
            output_json=args.output_json,
            output_markdown=args.output_markdown,
        )
        parser.exit(1, f"error: {exc}\n")

    _write_requested_outputs(
        report,
        output_json=args.output_json,
        output_markdown=args.output_markdown,
    )
    payload = (
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
        if args.format == "json"
        else render_github_repo_agent_markdown(report)
    )
    print(payload)
    raise SystemExit(0 if report.passed or not args.require_success else 1)


def _run_onboarding_tree(
    owner: str,
    repo: str,
    ref: str | None,
    output_root: Path,
    *,
    prefer_cached_discovery: bool = False,
    **kwargs,
) -> GitHubBenchmarkOnboardingReport:
    if prefer_cached_discovery:
        cached = _cached_discovery_payload_for_repo(
            output_root,
            owner,
            repo,
            ref,
            reuse_reason="prefer_cached_discovery",
            fallback=False,
        )
        if cached is not None:
            discovery_ref = str(cached.get("ref") or ref or "")
            return onboard_from_discovery(
                cached,
                output_root,
                source=f"cached-discovery-preferred:{owner}/{repo}@{discovery_ref}",
                owner=owner,
                repo=repo,
                ref=discovery_ref or ref,
                requested_urls=[],
                **_onboard_from_discovery_kwargs(kwargs),
            )
    try:
        return onboard_tree(
            owner,
            repo,
            ref,
            output_root,
            **kwargs,
        )
    except GitHubAPIError as exc:
        if not _is_rate_limit_error(exc):
            raise
        cached = _cached_discovery_payload_for_repo(
            output_root,
            owner,
            repo,
            ref,
            reuse_reason="rate_limit_fallback",
            fallback=True,
        )
        if cached is not None:
            discovery_ref = str(cached.get("ref") or ref or "")
            return onboard_from_discovery(
                cached,
                output_root,
                source=f"cached-discovery:{owner}/{repo}@{discovery_ref}",
                owner=owner,
                repo=repo,
                ref=discovery_ref or ref,
                requested_urls=[],
                **_onboard_from_discovery_kwargs(kwargs),
            )
        if not _should_attempt_rate_limit_checkout_fallback(kwargs):
            raise
        fallback = _checkout_seed_discovery_payload_for_repo(owner, repo, ref, exc)
        fallback_ref = str(fallback.get("ref") or ref or "")
        fallback_kwargs = _onboard_from_discovery_kwargs(kwargs)
        fallback_kwargs["checkout_repository_tests"] = True
        return onboard_from_discovery(
            fallback,
            output_root,
            source=f"github-api-rate-limit-checkout:{owner}/{repo}@{fallback_ref or 'default'}",
            owner=owner,
            repo=repo,
            ref=fallback_ref or ref,
            requested_urls=[],
            **fallback_kwargs,
        )


def _run_onboarding_tree_with_ref_fallback(
    owner: str,
    repo: str,
    ref: str | None,
    output_root: Path,
    *,
    explicit_ref: str | None,
    inferred_ref: str | None,
    ref_candidates: list[str],
    **kwargs,
) -> tuple[GitHubBenchmarkOnboardingReport, str | None, str | None, list[dict[str, Any]]]:
    candidates = _ordered_url_ref_candidates(
        initial_ref=ref,
        explicit_ref=explicit_ref,
        inferred_ref=inferred_ref,
        ref_candidates=ref_candidates,
    )
    attempts: list[dict[str, Any]] = []
    last_error: GitHubAPIError | None = None
    for index, candidate in enumerate(candidates):
        try:
            report = _run_onboarding_tree(
                owner,
                repo,
                candidate,
                output_root,
                **kwargs,
            )
            effective_inferred_ref = (
                str(candidate or "") if inferred_ref and not explicit_ref else inferred_ref
            )
            if attempts:
                attempts.append(
                    {
                        "ref": str(candidate or ""),
                        "status": "pass",
                        "reason": "url_ref_candidate_resolved",
                    }
                )
            return report, candidate, effective_inferred_ref, attempts
        except GitHubAPIError as exc:
            last_error = exc
            should_try_next = (
                index + 1 < len(candidates)
                and not explicit_ref
                and bool(inferred_ref)
                and _is_ref_not_found_error(exc)
            )
            attempts.append(
                {
                    "ref": str(candidate or ""),
                    "status": "retry" if should_try_next else "fail",
                    "reason": "github_tree_ref_not_found"
                    if _is_ref_not_found_error(exc)
                    else "github_tree_fetch_failed",
                    "status_code": exc.status_code,
                    "url": exc.url,
                }
            )
            if not should_try_next:
                raise
    if last_error is not None:
        raise last_error
    return _run_onboarding_tree(owner, repo, ref, output_root, **kwargs), ref, inferred_ref, []


def _ordered_url_ref_candidates(
    *,
    initial_ref: str | None,
    explicit_ref: str | None,
    inferred_ref: str | None,
    ref_candidates: list[str],
) -> list[str | None]:
    if explicit_ref or not inferred_ref:
        return [initial_ref]
    ordered: list[str | None] = []
    if initial_ref:
        ordered.append(str(initial_ref))
    for candidate in ref_candidates:
        normalized = str(candidate or "").strip()
        if normalized and normalized not in ordered:
            ordered.append(normalized)
    return ordered or [initial_ref]


def _is_ref_not_found_error(error: GitHubAPIError) -> bool:
    return error.status_code == 404


def _should_attempt_rate_limit_checkout_fallback(kwargs: dict[str, Any]) -> bool:
    return bool(
        kwargs.get("checkout_repository_tests")
        or kwargs.get("repository_test_root") is not None
    )


def _checkout_seed_discovery_payload_for_repo(
    owner: str,
    repo: str,
    ref: str | None,
    error: GitHubAPIError,
) -> dict[str, Any]:
    discovery: dict[str, Any] = {
        "mode": "rate_limit_checkout_seed",
        "owner": owner,
        "repo": repo,
        "api_rate_limit_checkout_fallback": True,
        "api_rate_limit_status_code": error.status_code,
        "api_rate_limit_remaining": error.rate_limit_remaining,
        "api_rate_limit_reset": error.rate_limit_reset,
    }
    if ref:
        discovery.update(
            {
                "ref": ref,
                "requested_ref": ref,
                "ref_source": "explicit",
            }
        )
    else:
        discovery["ref_source"] = "checkout_default_branch"
    return {
        "owner": owner,
        "repo": repo,
        "ref": ref or "",
        "files": [],
        "discovery": discovery,
    }


def _cached_discovery_payload_for_repo(
    output_root: Path,
    owner: str,
    repo: str,
    ref: str | None,
    *,
    reuse_reason: str = "rate_limit_fallback",
    fallback: bool = True,
) -> dict[str, Any] | None:
    path = output_root / "discovery.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if str(payload.get("owner") or "") != owner:
        return None
    if str(payload.get("repo") or "") != repo:
        return None
    cached_ref = str(payload.get("ref") or "")
    if ref and cached_ref and cached_ref != ref:
        return None
    if not isinstance(payload.get("tree"), list) and not isinstance(
        payload.get("files"),
        list,
    ):
        return None
    payload = dict(payload)
    discovery = dict(payload.get("discovery") or {})
    discovery["cache_reuse"] = True
    discovery["cache_reuse_reason"] = reuse_reason
    discovery["cache_reuse_source"] = str(path)
    if fallback:
        discovery["cache_fallback"] = True
        discovery["cache_fallback_source"] = str(path)
    else:
        discovery["cache_preferred"] = True
        discovery["cache_preferred_source"] = str(path)
    payload["discovery"] = discovery
    return payload


def _onboard_from_discovery_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    excluded = {"token", "recursive", "api_base_url", "timeout", "opener"}
    return {key: value for key, value in kwargs.items() if key not in excluded}


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


def _resolved_onboarding_options(
    *,
    preset: str,
    max_sources: int | None,
    max_candidates: int | None,
    run_smoke_validation: bool | None,
) -> dict[str, Any]:
    if preset == "smoke":
        return {
            "max_sources": max_sources if max_sources is not None else 20,
            "max_candidates": max_candidates if max_candidates is not None else 10,
            "materialize_template": True,
            "run_benchmark": True,
            "use_dynamic_coverage": False,
            "run_quality_gate": True,
            "quality_gate_thresholds": OnboardingQualityGateThresholds(
                min_quality_score=0.0,
                min_source_hit_rate=0.0,
                require_ready_for_benchmark=False
            ),
            "run_showcase_lite": True,
            "run_smoke_validation": True
            if run_smoke_validation is None
            else run_smoke_validation,
        }
    if preset == "mining":
        return {
            "max_sources": max_sources if max_sources is not None else 50,
            "max_candidates": max_candidates if max_candidates is not None else 20,
            "materialize_template": False,
            "run_benchmark": False,
            "use_dynamic_coverage": False,
            "run_quality_gate": True,
            "quality_gate_thresholds": OnboardingQualityGateThresholds(
                min_quality_score=0.0,
                min_source_hit_rate=0.0,
                require_ready_for_benchmark=False,
                require_benchmark_run=False,
            ),
            "run_showcase_lite": True,
            "run_smoke_validation": False
            if run_smoke_validation is None
            else run_smoke_validation,
        }
    raise ValueError(f"unsupported preset: {preset}")


def _build_agent_report(
    *,
    repo_spec: str,
    owner: str,
    repo: str,
    explicit_ref: str | None,
    inferred_ref: str | None,
    ref_fallback_attempts: list[dict[str, Any]],
    output_dir: Path,
    preset: str,
    onboarding: GitHubBenchmarkOnboardingReport,
) -> GitHubRepoAgentReport:
    onboarding_payload = onboarding.to_dict()
    summary = _agent_summary(onboarding_payload)
    summary["repo_input"] = _repo_input_summary(
        repo_spec=repo_spec,
        owner=owner,
        repo=repo,
        explicit_ref=explicit_ref,
        inferred_ref=inferred_ref,
        resolved_ref=str(summary.get("repository_ref") or ""),
        requested_ref=str(summary.get("requested_ref") or ""),
        ref_source=str(summary.get("ref_source") or ""),
        ref_fallback_attempts=ref_fallback_attempts,
    )
    status = _agent_status(summary)
    paths = {
        "agent_json": str(output_dir / "github_repo_agent.json"),
        "agent_markdown": str(output_dir / "github_repo_agent.md"),
        "agent_execution_plan_json": str(
            output_dir / "github_repo_agent_execution_plan.json"
        ),
        "agent_execution_plan_markdown": str(
            output_dir / "github_repo_agent_execution_plan.md"
        ),
        **dict(onboarding.output_paths),
    }
    return GitHubRepoAgentReport(
        repo_spec=repo_spec,
        owner=owner,
        repo=repo,
        output_dir=str(output_dir),
        preset=preset,
        status=status,
        summary=summary,
        output_paths=paths,
        onboarding_report=onboarding_payload,
    )


def _repo_input_summary(
    *,
    repo_spec: str,
    owner: str,
    repo: str,
    explicit_ref: str | None,
    inferred_ref: str | None,
    resolved_ref: str,
    requested_ref: str,
    ref_source: str,
    ref_fallback_attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    raw = str(repo_spec or "").strip()
    kind = _repo_input_kind(raw)
    ref_selection_source = _repo_ref_selection_source(
        explicit_ref=explicit_ref,
        inferred_ref=inferred_ref,
        requested_ref=requested_ref,
        ref_source=ref_source,
    )
    return {
        "raw": raw,
        "kind": kind,
        "normalized_repo": f"{owner}/{repo}",
        "owner": owner,
        "repo": repo,
        "explicit_ref": str(explicit_ref or ""),
        "url_inferred_ref": str(inferred_ref or ""),
        "requested_ref": str(requested_ref or ""),
        "resolved_ref": str(resolved_ref or ""),
        "ref_source": str(ref_source or ""),
        "ref_selection_source": ref_selection_source,
        "ref_fallback_used": any(
            str(item.get("status") or "") == "pass"
            for item in ref_fallback_attempts
        ),
        "ref_fallback_attempt_count": len(ref_fallback_attempts),
        "ref_fallback_attempts": ref_fallback_attempts,
    }


def _repo_input_kind(raw: str) -> str:
    value = raw.strip()
    lowered = value.lower()
    if value.startswith("git@github.com:"):
        return "github_ssh_url"
    if lowered.startswith(("https://github.com/", "http://github.com/")):
        return "github_url"
    if lowered.startswith(("https://www.github.com/", "http://www.github.com/")):
        return "github_url"
    if lowered.startswith(("github.com/", "www.github.com/")):
        return "github_url_without_scheme"
    return "owner_repo"


def _repo_ref_selection_source(
    *,
    explicit_ref: str | None,
    inferred_ref: str | None,
    requested_ref: str,
    ref_source: str,
) -> str:
    explicit = str(explicit_ref or "").strip()
    inferred = str(inferred_ref or "").strip()
    requested = str(requested_ref or "").strip()
    if explicit:
        return "cli_ref"
    if inferred and requested == inferred:
        return "url_path_ref"
    if ref_source == "default_branch":
        return "default_branch"
    if ref_source:
        return ref_source
    return "none"


def _agent_fallback_reason(
    summary: dict[str, Any],
    *,
    enabled: bool,
    min_generated_candidates: int,
) -> str | None:
    if not enabled:
        return None
    if _int(summary.get("imported_sources", 0)) <= 0:
        return None
    generated = _int(summary.get("generated_candidates", 0))
    threshold = max(1, _int(min_generated_candidates))
    if generated <= 0:
        return "no_generated_candidates"
    if generated < threshold:
        return "low_generated_candidates"
    return None


def _agent_auto_benchmark_remediation_action(
    summary: dict[str, Any],
    *,
    enabled: bool,
) -> dict[str, Any]:
    if not enabled:
        return {}
    if str(summary.get("benchmarkization_status") or "") != "ready_to_run_benchmark":
        return {}
    action = _dict(summary.get("benchmarkization_primary_action"))
    if str(action.get("action_id") or "") != "run_template_benchmark":
        return {}
    if not bool(action.get("auto_runnable", False)):
        return {}
    if str(action.get("risk") or "low") not in {"low", "none"}:
        return {}
    return action


def _fallback_limit(explicit: int | None, current: Any, *, default: int) -> int:
    if explicit is not None:
        return max(1, _int(explicit))
    current_value = _int(current)
    return max(current_value, default) if current_value > 0 else default


def _merge_auto_remediation_report(
    *,
    primary: GitHubRepoAgentReport,
    remediated: GitHubRepoAgentReport,
    output_dir: Path,
    action: dict[str, Any],
) -> GitHubRepoAgentReport:
    primary_benchmarkization = str(
        primary.summary.get("benchmarkization_status") or ""
    )
    remediated_benchmarkization = str(
        remediated.summary.get("benchmarkization_status") or ""
    )
    primary_cases = _int(primary.summary.get("benchmark_cases", 0))
    remediated_cases = _int(remediated.summary.get("benchmark_cases", 0))
    improved = (
        remediated_cases > primary_cases
        or remediated_benchmarkization == "benchmark_ready"
        or (primary.status != "pass" and remediated.status == "pass")
    )
    selected = remediated if improved else primary
    summary = dict(selected.summary)
    summary.update(
        {
            "auto_remediation_enabled": True,
            "auto_remediation_attempted": True,
            "auto_remediation_used": selected is remediated,
            "auto_remediation_improved": improved,
            "auto_remediation_action_id": str(action.get("action_id") or ""),
            "auto_remediation_command": str(action.get("command") or ""),
            "auto_remediation_stage": str(action.get("stage") or ""),
            "auto_remediation_risk": str(action.get("risk") or ""),
            "primary_benchmarkization_status": primary_benchmarkization,
            "remediated_benchmarkization_status": remediated_benchmarkization,
            "primary_benchmark_cases": primary_cases,
            "remediated_benchmark_cases": remediated_cases,
            "pre_remediation_output_dir": primary.output_dir,
            "remediated_output_dir": remediated.output_dir,
        }
    )
    paths = dict(selected.output_paths)
    paths["agent_json"] = str(output_dir / "github_repo_agent.json")
    paths["agent_markdown"] = str(output_dir / "github_repo_agent.md")
    paths["agent_execution_plan_json"] = str(
        output_dir / "github_repo_agent_execution_plan.json"
    )
    paths["agent_execution_plan_markdown"] = str(
        output_dir / "github_repo_agent_execution_plan.md"
    )
    paths["pre_remediation_agent_json"] = str(
        output_dir / "github_repo_agent_pre_remediation.json"
    )
    paths["pre_remediation_agent_markdown"] = str(
        output_dir / "github_repo_agent_pre_remediation.md"
    )
    paths["pre_remediation_agent_execution_plan_json"] = str(
        output_dir / "github_repo_agent_pre_remediation_execution_plan.json"
    )
    paths["pre_remediation_agent_execution_plan_markdown"] = str(
        output_dir / "github_repo_agent_pre_remediation_execution_plan.md"
    )
    return GitHubRepoAgentReport(
        repo_spec=selected.repo_spec,
        owner=selected.owner,
        repo=selected.repo,
        output_dir=str(output_dir),
        preset=selected.preset,
        status=selected.status,
        summary=summary,
        output_paths=paths,
        onboarding_report=selected.onboarding_report,
    )


def _merge_fallback_report(
    *,
    primary: GitHubRepoAgentReport,
    fallback: GitHubRepoAgentReport,
    output_dir: Path,
    reason: str,
    min_generated_candidates: int,
) -> GitHubRepoAgentReport:
    primary_candidates = _int(primary.summary.get("generated_candidates", 0))
    fallback_candidates = _int(fallback.summary.get("generated_candidates", 0))
    primary_cases = _int(primary.summary.get("benchmark_cases", 0))
    fallback_cases = _int(fallback.summary.get("benchmark_cases", 0))
    improved = (
        fallback_candidates > primary_candidates
        or fallback_cases > primary_cases
        or (primary.status != "pass" and fallback.status == "pass")
    )
    threshold = max(1, _int(min_generated_candidates))
    recovered = primary_candidates < threshold <= fallback_candidates
    selected = fallback if improved else primary
    summary = dict(selected.summary)
    summary.update(
        {
            "auto_fallback_enabled": True,
            "fallback_attempted": True,
            "fallback_used": selected is fallback,
            "fallback_improved": improved,
            "fallback_recovered": recovered,
            "fallback_reason": reason,
            "fallback_min_generated_candidates": threshold,
            "primary_output_dir": primary.output_dir,
            "fallback_output_dir": fallback.output_dir,
            "primary_status": primary.status,
            "fallback_status": fallback.status,
            "primary_generated_candidates": primary_candidates,
            "fallback_generated_candidates": fallback_candidates,
            "primary_benchmark_cases": primary_cases,
            "fallback_benchmark_cases": fallback_cases,
            "primary_benchmarkization_status": str(
                primary.summary.get("benchmarkization_status") or ""
            ),
            "fallback_benchmarkization_status": str(
                fallback.summary.get("benchmarkization_status") or ""
            ),
            "primary_benchmarkization_primary_action_id": str(
                primary.summary.get("benchmarkization_primary_action_id") or ""
            ),
            "fallback_benchmarkization_primary_action_id": str(
                fallback.summary.get("benchmarkization_primary_action_id") or ""
            ),
            "primary_benchmarkization_remediation_plan_markdown": str(
                primary.summary.get("benchmarkization_remediation_plan_markdown")
                or ""
            ),
            "fallback_benchmarkization_remediation_plan_markdown": str(
                fallback.summary.get("benchmarkization_remediation_plan_markdown")
                or ""
            ),
        }
    )
    paths = dict(selected.output_paths)
    paths["agent_json"] = str(output_dir / "github_repo_agent.json")
    paths["agent_markdown"] = str(output_dir / "github_repo_agent.md")
    paths["agent_execution_plan_json"] = str(
        output_dir / "github_repo_agent_execution_plan.json"
    )
    paths["agent_execution_plan_markdown"] = str(
        output_dir / "github_repo_agent_execution_plan.md"
    )
    paths["primary_agent_json"] = str(output_dir / "github_repo_agent_primary.json")
    paths["primary_agent_markdown"] = str(
        output_dir / "github_repo_agent_primary.md"
    )
    paths["primary_agent_execution_plan_json"] = str(
        output_dir / "github_repo_agent_primary_execution_plan.json"
    )
    paths["primary_agent_execution_plan_markdown"] = str(
        output_dir / "github_repo_agent_primary_execution_plan.md"
    )
    paths["primary_benchmarkization_remediation_plan_json"] = primary.output_paths.get(
        "benchmarkization_remediation_plan_json",
        "",
    )
    paths["primary_benchmarkization_remediation_plan_markdown"] = (
        primary.output_paths.get("benchmarkization_remediation_plan_markdown", "")
    )
    paths["fallback_agent_json"] = fallback.output_paths["agent_json"]
    paths["fallback_agent_markdown"] = fallback.output_paths["agent_markdown"]
    paths["fallback_agent_execution_plan_json"] = fallback.output_paths[
        "agent_execution_plan_json"
    ]
    paths["fallback_agent_execution_plan_markdown"] = fallback.output_paths[
        "agent_execution_plan_markdown"
    ]
    paths["fallback_benchmarkization_remediation_plan_json"] = (
        fallback.output_paths.get("benchmarkization_remediation_plan_json", "")
    )
    paths["fallback_benchmarkization_remediation_plan_markdown"] = (
        fallback.output_paths.get("benchmarkization_remediation_plan_markdown", "")
    )
    return GitHubRepoAgentReport(
        repo_spec=selected.repo_spec,
        owner=selected.owner,
        repo=selected.repo,
        output_dir=str(output_dir),
        preset=selected.preset,
        status=selected.status,
        summary=summary,
        output_paths=paths,
        onboarding_report=selected.onboarding_report,
    )


def _build_fetch_error_report(
    *,
    repo_spec: str,
    output_dir: Path,
    preset: str,
    error: GitHubAPIError,
) -> GitHubRepoAgentReport:
    output_dir.mkdir(parents=True, exist_ok=True)
    owner, repo = parse_github_repo_spec(repo_spec)
    summary = {
        "mode": "tree",
        "source": f"github-tree:{owner}/{repo}",
        "discovery_source": f"github-tree:{owner}/{repo}",
        "discovery_cache_reuse": False,
        "discovery_cache_reuse_reason": "",
        "discovery_cache_reuse_source": "",
        "discovery_cache_preferred": False,
        "discovery_cache_preferred_source": "",
        "discovery_cache_fallback": False,
        "discovery_cache_fallback_source": "",
        "discovery_api_rate_limit_checkout_fallback": False,
        "discovery_api_rate_limit_status_code": None,
        "discovery_api_rate_limit_remaining": "",
        "repository_ref": "",
        "requested_ref": "",
        "ref_source": "",
        "source_cache_dir": "",
        "discovery_items": 0,
        "imported_sources": 0,
        "selected_sources": 0,
        "skipped_sources": 0,
        "generated_candidates": 0,
        "recipe_selection_mode": "",
        "selected_recipes": [],
        "ready_for_benchmark": False,
        "quality_score": 0.0,
        "benchmark_cases": 0,
        "top1": 0.0,
        "map": 0.0,
        "patch_success_rate": 0.0,
        "repository_profile": {},
        "test_source_count": 0,
        "test_framework_signals": [],
        "framework_signals": [],
        "recommended_test_command": "",
        "recommended_target_prefix": "",
        "project_config_count": 0,
        "repository_test_command_status": "",
        "repository_test_command_executed": False,
        "repository_test_command_reason": "",
        "repository_test_setup_doctor_status": "",
        "repository_test_setup_doctor_blocker": "",
        "repository_test_setup_doctor_score": 0.0,
        "repository_test_setup_doctor_next_action": "",
        "repository_test_setup_doctor_check_count": 0,
        "repository_test_setup_doctor_passed_check_count": 0,
        "repository_test_setup_doctor_warning_check_count": 0,
        "repository_test_setup_doctor_blocked_check_count": 0,
        "repository_test_setup_doctor_skipped_check_count": 0,
        "repository_test_setup_doctor_check_status_counts": {},
        "repository_test_setup_doctor_blocked_check_names": [],
        "repository_test_setup_doctor_warning_check_names": [],
        "repository_test_environment_status": "",
        "repository_test_environment_reason": "",
        "repository_config_snapshot_status": "",
        "repository_config_snapshot_reason": "",
        "repository_config_snapshot_file_count": 0,
        "repository_test_pytest_config_source_count": 0,
        "repository_test_pytest_addopts": [],
        "repository_test_pytest_testpaths": [],
        "repository_test_ci_config_source_count": 0,
        "repository_test_ci_python_versions": [],
        "repository_test_ci_install_command_candidates": [],
        "repository_test_ci_test_command_candidates": [],
        "repository_test_tox_envlist": [],
        "repository_test_framework_configuration_status": "",
        "repository_test_framework_configuration_reason": "",
        "repository_test_frameworks": [],
        "repository_test_app_bootstrap_candidate_count": 0,
        "repository_test_app_bootstrap_candidates": [],
        "repository_test_bootstrap_signal_count": 0,
        "repository_test_environment_setup_status": "",
        "repository_test_environment_setup_reason": "",
        "repository_test_environment_setup_supported": False,
        "repository_test_environment_setup_venv_path": "",
        "repository_test_environment_setup_result_status": "",
        "repository_test_environment_setup_result_reason": "",
        "repository_test_environment_setup_result_executed": False,
        "repository_test_environment_setup_install_failure_category": "",
        "repository_test_environment_setup_install_failure_signal": "",
        "repository_test_environment_setup_install_fallback_executed": False,
        "repository_test_environment_setup_install_fallback_returncode": None,
        "repository_test_execution_plan_status": "",
        "repository_test_execution_plan_reason": "",
        "planned_repository_test_command": "",
        "planned_repository_test_level": "",
        "planned_repository_test_risk": "",
        "planned_repository_test_runner": "",
        "planned_repository_test_source": "",
        "planned_repository_test_preferred_runner": "",
        "planned_repository_test_runner_fallback_used": False,
        "planned_repository_test_runner_fallback_reason": "",
        "planned_repository_test_runner_fallback_from": "",
        "planned_repository_test_runner_fallback_to": "",
        "planned_repository_test_ci_candidate_count": 0,
        "planned_repository_test_ci_command_candidates": [],
        "planned_repository_test_executable_now": False,
        "planned_repository_test_environment_variable_names": [],
        "planned_repository_test_automatic_env_var_names": [],
        "planned_repository_test_result_status": "",
        "planned_repository_test_result_reason": "",
        "planned_repository_test_result_executed": False,
        "planned_repository_test_result_passed": 0,
        "planned_repository_test_result_failed": 0,
        "planned_repository_test_result_errors": 0,
        "planned_repository_test_result_skipped": 0,
        "planned_repository_test_result_test_count": 0,
        "planned_repository_test_result_test_count_source": "",
        "planned_repository_test_python_executable": "",
        "planned_repository_test_python_source": "",
        "planned_repository_test_failure_category": "",
        "planned_repository_test_failure_signal": "",
        "planned_repository_test_failure_context_line_count": 0,
        "repository_test_retry_status": "",
        "repository_test_retry_reason": "",
        "repository_test_retry_recommended": False,
        "repository_test_retry_strategy": "",
        "repository_test_retry_command": "",
        "repository_test_retry_execution_status": "",
        "repository_test_retry_execution_reason": "",
        "repository_test_retry_executed": False,
        "repository_test_retry_setup_prerequisite_required": False,
        "repository_test_retry_setup_prerequisite_satisfied": False,
        "repository_test_retry_setup_prerequisite_status": "",
        "repository_test_retry_setup_prerequisite_auto_executed": False,
        "repository_test_retry_execution_passed": 0,
        "repository_test_retry_execution_failed": 0,
        "repository_test_timeout_narrowing_status": "",
        "repository_test_timeout_narrowing_reason": "",
        "repository_test_timeout_narrowing_executed": False,
        "repository_test_timeout_narrowing_attempt_count": 0,
        "repository_test_timeout_narrowing_selected_command": "",
        "repository_test_timeout_narrowing_selected_failure_category": "",
        "repository_test_dynamic_evidence_level": "",
        "repository_test_dynamic_evidence_reason": "",
        "repository_test_dynamic_failing_tests": 0,
        "repository_test_dynamic_traceback_frames": 0,
        "repository_test_dynamic_failed_tests": 0,
        "repository_test_dynamic_passed_tests": 0,
        "repository_test_dynamic_usable_for_localization": False,
        "repository_test_dynamic_usable_for_regression_validation": False,
        "repository_test_dynamic_usable_for_patch_validation": False,
        "repository_test_dynamic_validation_command": "",
        "repository_test_analysis_source": "",
        "repository_test_overlay_trigger_reason": "",
        "repository_test_phase2_ready": False,
        "repository_test_phase3_validation_ready": False,
        "repository_test_final_status": "blocked",
        "repository_test_final_reason": "github_fetch_failed",
        "repository_test_failure_overlay_status": "",
        "repository_test_failure_overlay_reason": "",
        "repository_test_failure_overlay_attempted_cases": 0,
        "repository_test_failure_overlay_supported_candidates": 0,
        "repository_test_failure_overlay_selected_rule": "",
        "repository_test_failure_overlay_selected_function": "",
        "repository_test_failure_overlay_candidate_rejection_count": 0,
        "repository_test_failure_overlay_candidate_rejection_counts": {},
        "repository_test_failure_overlay_candidate_rejection_rule_counts": {},
        "repository_test_failure_overlay_candidate_rejection_examples": [],
        "repository_test_failure_overlay_dominant_rejection_reason": "",
        "repository_test_failure_overlay_dominant_rejection_count": 0,
        "repository_test_failure_overlay_rejection_recommendations": [],
        "repository_test_failure_overlay_next_extension": {},
        "repository_test_failure_overlay_next_actionable_extension": {},
        "repository_test_failure_overlay_selected_score": 0.0,
        "repository_test_failure_overlay_average_candidate_score": 0.0,
        "repository_test_failure_overlay_selected_score_breakdown": {},
        "repository_test_failure_overlay_candidate_score_preview": [],
        "repository_test_failure_overlay_dynamic_evidence_level": "",
        "repository_test_failure_overlay_validation_command": "",
        "repository_test_fault_localization_status": "",
        "repository_test_fault_localization_reason": "",
        "repository_test_fault_localization_ranking_count": 0,
        "repository_test_fault_localization_top_function": "",
        "repository_test_fault_localization_top_score": 0.0,
        "repository_test_fault_localization_matched_failed_tests": 0,
        "repository_test_fault_localization_unmatched_failed_tests": 0,
        "repository_test_fault_localization_traceback_frames": 0,
        "repository_test_fault_localization_matched_traceback_frames": 0,
        "repository_test_fault_localization_unmatched_traceback_frames": 0,
        "repository_test_patch_candidates_status": "",
        "repository_test_patch_candidates_reason": "",
        "repository_test_patch_candidate_count": 0,
        "repository_patch_generation_mode": "",
        "repository_patch_generator_counts": {},
        "repository_llm_patch_generation_status": "",
        "repository_llm_patch_generation_reason": "",
        "repository_patch_safety_gate_status": "",
        "repository_patch_safety_gate_blocked_count": 0,
        "repository_test_patch_target_function_count": 0,
        "repository_test_patch_validation_command": "",
        "repository_test_patch_validation_status": "",
        "repository_test_patch_validation_reason": "",
        "repository_test_patch_validation_input_candidate_count": 0,
        "repository_test_patch_validation_candidate_count": 0,
        "repository_test_patch_validation_safety_blocked_candidate_count": 0,
        "repository_test_patch_validation_executed_count": 0,
        "repository_test_patch_validation_success_count": 0,
        "repository_test_repair_ready": False,
        "repository_test_repair_validation_scope": "",
        "repository_test_regression_ready": False,
        "repository_test_regression_validation_status": "",
        "repository_test_regression_validation_reason": "",
        "repository_test_regression_validation_command": "",
        "repository_test_regression_validation_passed": 0,
        "repository_test_regression_validation_failed": 0,
        "repository_test_patch_validation_reflection_mode": "",
        "repository_test_patch_validation_refiner_status": "",
        "repository_test_patch_validation_refiner_reason": "",
        "repository_test_patch_validation_reflection_candidate_count": 0,
        "repository_test_patch_validation_successful_reflection_count": 0,
        "repository_test_patch_validation_regression_reflection_candidate_count": 0,
        "repository_test_patch_validation_successful_regression_reflection_count": 0,
        "repository_test_patch_validation_max_depth": 0,
        "repository_test_patch_validation_failure_type_counts": {},
        "repository_test_patch_judge_mode": "",
        "repository_test_patch_judge_status": "",
        "repository_test_patch_judge_reason": "",
        "repository_test_patch_judge_enabled": False,
        "repository_test_patch_judge_candidate_count": 0,
        "repository_test_patch_judge_verdict_counts": {},
        "repository_test_patch_judge_agreement_counts": {},
        "repository_test_patch_judge_authority": "",
        "repository_test_patch_judge_config_audit": {},
        "repository_test_reflection_trace_status": "",
        "repository_test_reflection_trace_reason": "",
        "repository_test_reflection_trace_initial_failure_count": 0,
        "repository_test_reflection_trace_step_count": 0,
        "repository_test_reflection_trace_successful_step_count": 0,
        "repository_test_reflection_trace_path": "",
        "repository_test_reflection_trace_markdown": "",
        "reflection_trace_path": "",
        "reflection_trace_markdown": "",
        "repository_test_best_patch_candidate_id": "",
        "repository_test_best_patch_candidate_rule_id": "",
        "repository_test_best_patch_candidate_variant": "",
        "repository_test_best_patch_candidate_success": False,
        "repository_test_best_patch_relative_file_path": "",
        "repository_test_best_patch_rule_id": "",
        "repository_test_best_patch_variant": "",
        "repository_test_best_patch_depth": 0,
        "repository_test_best_patch_parent_candidate_id": "",
        "repository_test_best_patch_has_diff": False,
        "repository_test_repair_patch_path": "",
        "repository_test_repair_summary_status": "",
        "repository_test_repair_summary_reason": "",
        "repository_test_repair_summary_conclusion": "",
        "repository_test_repair_summary_path": "",
        "recommended_install_command": "",
        "repository_test_module": "",
        "repository_test_tool_available": None,
        "repository_checkout_status": "",
        "repository_checkout_reason": "",
        "repository_checkout_method": "",
        "repository_checkout_path": "",
        "repository_checkout_source_count": 0,
        "repository_checkout_source_discovery_present": False,
        "quality_gate_present": False,
        "quality_gate_passed": None,
        "smoke_validation_present": False,
        "smoke_validation_passed": None,
        "diagnostics_status": "fail",
        "first_failing_stage": "github_fetch",
        "diagnostic_issue_count": 1,
        "diagnostic_error_count": 1,
        "diagnostic_warning_count": 0,
        "diagnostic_info_count": 0,
        "diagnostic_issue_codes": ["github_api_error"],
        "diagnostic_issue_preview": [
            {
                "stage": "github_fetch",
                "severity": "error",
                "code": "github_api_error",
                "message": str(error),
            }
        ],
        "github_error": {
            "type": type(error).__name__,
            "message": str(error),
            "status_code": error.status_code,
            "url": error.url,
            "rate_limit_remaining": error.rate_limit_remaining,
            "rate_limit_reset": error.rate_limit_reset,
            "response_body": error.response_body,
        },
        "next_actions": _github_error_next_actions(error),
    }
    agent_execution_plan = _agent_execution_plan(summary)
    summary["agent_execution_plan"] = agent_execution_plan
    summary["agent_execution_plan_status_counts"] = _agent_plan_status_counts(
        agent_execution_plan
    )
    summary["agent_execution_plan_primary_blocker"] = (
        _agent_plan_primary_blocker(agent_execution_plan)
    )
    summary["agent_execution_plan_next_action"] = _agent_plan_next_action(
        agent_execution_plan
    )
    summary["agent_execution_plan_next_command"] = _agent_plan_next_command(
        agent_execution_plan
    )
    paths = {
        "agent_json": str(output_dir / "github_repo_agent.json"),
        "agent_markdown": str(output_dir / "github_repo_agent.md"),
        "agent_execution_plan_json": str(
            output_dir / "github_repo_agent_execution_plan.json"
        ),
        "agent_execution_plan_markdown": str(
            output_dir / "github_repo_agent_execution_plan.md"
        ),
    }
    return GitHubRepoAgentReport(
        repo_spec=repo_spec,
        owner=owner,
        repo=repo,
        output_dir=str(output_dir),
        preset=preset,
        status="fail",
        summary=summary,
        output_paths=paths,
        onboarding_report=None,
    )


def _agent_summary(onboarding: dict[str, Any]) -> dict[str, Any]:
    output_paths = _dict(onboarding.get("output_paths"))
    discovery_metadata = _dict(onboarding.get("discovery_metadata"))
    benchmark_summary = _dict(_dict(onboarding.get("benchmark_run")).get("summary"))
    mining_report = _dict(onboarding.get("mining_report"))
    diagnostics_headline = _dict(_dict(onboarding.get("diagnostics")).get("headline"))
    quality_summary = _dict(onboarding.get("quality_summary"))
    quality_gate = _dict(onboarding.get("quality_gate"))
    smoke_validation = _dict(onboarding.get("smoke_validation"))
    diagnostics = _dict(onboarding.get("diagnostics"))
    run_config = _dict(onboarding.get("run_config"))
    benchmarkization = (
        _dict(onboarding.get("benchmarkization_readiness"))
        or _dict(diagnostics.get("benchmarkization_readiness"))
        or _dict(run_config.get("benchmarkization_readiness"))
    )
    remediation_plan = _dict(benchmarkization.get("remediation_plan"))
    remediation_actions = [
        _dict(action) for action in _list(remediation_plan.get("actions"))
    ]
    primary_remediation_action = (
        remediation_actions[0] if remediation_actions else {}
    )
    repository_test_config = _dict(run_config.get("repository_test_command"))
    repository_analysis_route = _dict(run_config.get("repository_test_analysis_route"))
    repository_setup_doctor = _dict(
        onboarding.get("repository_test_setup_doctor")
    ) or _dict(run_config.get("repository_test_setup_doctor"))
    repository_profile = _dict(onboarding.get("repository_profile"))
    test_command_candidates = _list(repository_profile.get("test_command_candidates"))
    top_test_command_candidate = _dict(
        test_command_candidates[0] if test_command_candidates else {}
    )
    repository_test = _dict(onboarding.get("repository_test_command"))
    repository_environment = _dict(onboarding.get("repository_test_environment"))
    repository_config_snapshot = _dict(onboarding.get("repository_config_snapshot"))
    repository_environment_setup = _dict(
        onboarding.get("repository_test_environment_setup")
    )
    repository_environment_setup_result = _dict(
        onboarding.get("repository_test_environment_setup_result")
    )
    repository_execution_plan = _dict(
        onboarding.get("repository_test_execution_plan")
    )
    repository_execution_candidates = [
        _dict(item) for item in _list(repository_execution_plan.get("candidate_commands"))
    ]
    repository_execution_recommended = (
        repository_execution_candidates[0] if repository_execution_candidates else {}
    )
    repository_execution_result = _dict(
        onboarding.get("repository_test_execution_result")
    )
    repository_retry_plan = _dict(onboarding.get("repository_test_retry_plan"))
    repository_retry_execution_result = _dict(
        onboarding.get("repository_test_retry_execution_result")
    )
    repository_timeout_narrowing = _dict(
        onboarding.get("repository_test_timeout_narrowing")
    )
    repository_dynamic_evidence = _dict(
        onboarding.get("repository_test_dynamic_evidence")
    )
    repository_failure_overlay = _dict(
        onboarding.get("repository_test_failure_overlay")
    )
    repository_failure_overlay_case = _dict(
        repository_failure_overlay.get("selected_case")
    )
    repository_failure_overlay_dynamic = _dict(
        repository_failure_overlay.get("dynamic_evidence")
    )
    repository_failure_overlay_strategy = _dict(
        repository_failure_overlay.get("strategy_summary")
    )
    repository_fault_localization = _dict(
        onboarding.get("repository_test_fault_localization")
    )
    repository_patch_candidates = _dict(
        onboarding.get("repository_test_patch_candidates")
    )
    repository_patch_validation = _dict(
        onboarding.get("repository_test_patch_validation")
    )
    repository_reflection_initial_failures = [
        _dict(item)
        for item in _list(repository_patch_validation.get("results"))
        if _int(_dict(item).get("depth", 0)) == 0
        and not bool(_dict(item).get("success", False))
    ]
    repository_reflection_steps = [
        _dict(item)
        for item in _list(repository_patch_validation.get("results"))
        if _int(_dict(item).get("depth", 0)) > 0
    ]
    repository_successful_reflection_steps = [
        item for item in repository_reflection_steps if bool(item.get("success", False))
    ]
    repository_reflection_trace_reason = _repository_reflection_trace_reason(
        repository_patch_validation,
        initial_failure_count=len(repository_reflection_initial_failures),
        reflection_step_count=len(repository_reflection_steps),
        successful_reflection_step_count=len(repository_successful_reflection_steps),
    )
    repository_repair_summary = _dict(
        onboarding.get("repository_test_repair_summary")
    )
    repository_best_patch = _dict(repository_patch_validation.get("best_patch"))
    repository_regression_validation = _dict(
        repository_patch_validation.get("regression_validation")
    )
    repository_framework_config = _dict(
        repository_environment.get("framework_test_configuration")
    )
    repository_ci_config = _dict(repository_environment.get("ci_configuration"))
    repository_app_bootstrap_candidates = [
        _dict(item)
        for item in _list(repository_framework_config.get("app_bootstrap_candidates"))
    ]
    repository_final_diagnosis = _repository_test_final_diagnosis(
        route=repository_analysis_route,
        execution_result=repository_execution_result,
        fault_localization=repository_fault_localization,
        patch_validation=repository_patch_validation,
        framework_config=repository_framework_config,
    )
    repository_checkout = _dict(onboarding.get("repository_checkout"))
    repository_checkout_sources = _dict(onboarding.get("repository_checkout_sources"))
    repository_checkout_source_metadata = _dict(
        repository_checkout_sources.get("discovery")
    )
    diagnostic_issues = _list(diagnostics.get("issues"))
    recipe_suggestions = _list(diagnostics.get("recipe_suggestions"))
    summary = {
        "mode": str(onboarding.get("mode") or ""),
        "source": str(onboarding.get("source") or ""),
        "discovery_source": str(onboarding.get("source") or ""),
        "discovery_cache_reuse": bool(
            discovery_metadata.get("cache_reuse", False)
            or discovery_metadata.get("cache_fallback", False)
            or repository_checkout_source_metadata.get("cache_reuse", False)
            or repository_checkout_source_metadata.get("cache_fallback", False)
        ),
        "discovery_cache_reuse_reason": str(
            discovery_metadata.get("cache_reuse_reason")
            or repository_checkout_source_metadata.get("cache_reuse_reason")
            or (
                "rate_limit_fallback"
                if (
                    discovery_metadata.get("cache_fallback", False)
                    or repository_checkout_source_metadata.get("cache_fallback", False)
                )
                else ""
            )
        ),
        "discovery_cache_reuse_source": str(
            discovery_metadata.get("cache_reuse_source")
            or repository_checkout_source_metadata.get("cache_reuse_source")
            or discovery_metadata.get("cache_fallback_source")
            or repository_checkout_source_metadata.get("cache_fallback_source")
            or ""
        ),
        "discovery_cache_preferred": bool(
            discovery_metadata.get("cache_preferred", False)
            or repository_checkout_source_metadata.get("cache_preferred", False)
        ),
        "discovery_cache_preferred_source": str(
            discovery_metadata.get("cache_preferred_source")
            or repository_checkout_source_metadata.get("cache_preferred_source")
            or ""
        ),
        "discovery_cache_fallback": bool(
            discovery_metadata.get("cache_fallback", False)
            or repository_checkout_source_metadata.get("cache_fallback", False)
        ),
        "discovery_cache_fallback_source": str(
            discovery_metadata.get("cache_fallback_source")
            or repository_checkout_source_metadata.get("cache_fallback_source")
            or ""
        ),
        "discovery_api_rate_limit_checkout_fallback": bool(
            discovery_metadata.get("api_rate_limit_checkout_fallback", False)
        ),
        "discovery_api_rate_limit_status_code": discovery_metadata.get(
            "api_rate_limit_status_code"
        ),
        "discovery_api_rate_limit_remaining": str(
            discovery_metadata.get("api_rate_limit_remaining") or ""
        ),
        "repository_ref": str(discovery_metadata.get("ref") or ""),
        "requested_ref": str(discovery_metadata.get("requested_ref") or ""),
        "ref_source": str(discovery_metadata.get("ref_source") or ""),
        "source_cache_dir": str(output_paths.get("source_cache_dir") or ""),
        "discovery_items": _int(onboarding.get("discovery_item_count", 0)),
        "imported_sources": _int(onboarding.get("imported_source_count", 0)),
        "selected_sources": _int(onboarding.get("selected_source_count", 0)),
        "skipped_sources": _int(onboarding.get("skipped_source_count", 0)),
        "generated_candidates": _int(onboarding.get("generated_candidate_count", 0)),
        "source_mining_markdown_path": str(
            output_paths.get("source_mining_markdown") or ""
        ),
        "recipe_selection_mode": str(
            quality_summary.get("recipe_selection_mode") or ""
        ),
        "selected_recipes": [
            str(recipe) for recipe in _list(quality_summary.get("selected_recipes"))
        ],
        "ready_for_benchmark": bool(onboarding.get("ready_for_benchmark", False)),
        "quality_score": _float(quality_summary.get("quality_score", 0.0)),
        "benchmark_cases": _int(benchmark_summary.get("case_count", 0)),
        "top1": _float(benchmark_summary.get("top1", 0.0)),
        "map": _float(benchmark_summary.get("map", 0.0)),
        "patch_success_rate": _float(
            benchmark_summary.get("patch_success_rate", 0.0)
        ),
        "benchmarkization_status": str(benchmarkization.get("status") or ""),
        "benchmarkization_stage": str(benchmarkization.get("stage") or ""),
        "benchmarkization_ready": bool(benchmarkization.get("ready", False)),
        "benchmarkization_blocking_reasons": [
            str(item) for item in _list(benchmarkization.get("blocking_reasons"))
        ],
        "benchmarkization_next_actions": [
            str(item) for item in _list(benchmarkization.get("next_actions"))
        ],
        "benchmarkization_remediation_plan": remediation_plan,
        "benchmarkization_primary_action": primary_remediation_action,
        "benchmarkization_primary_action_id": str(
            primary_remediation_action.get("action_id") or ""
        ),
        "benchmarkization_primary_action_auto_runnable": bool(
            primary_remediation_action.get("auto_runnable", False)
        ),
        "benchmarkization_primary_action_command": str(
            primary_remediation_action.get("command") or ""
        ),
        "benchmarkization_primary_action_stage": str(
            primary_remediation_action.get("stage") or ""
        ),
        "benchmarkization_primary_action_risk": str(
            primary_remediation_action.get("risk") or ""
        ),
        "benchmarkization_primary_action_requires": [
            str(item)
            for item in _list(primary_remediation_action.get("requires"))
        ],
        "benchmarkization_primary_action_expected_outcome": str(
            primary_remediation_action.get("expected_outcome") or ""
        ),
        "benchmarkization_remediation_plan_json": str(
            output_paths.get("benchmarkization_remediation_plan_json") or ""
        ),
        "benchmarkization_remediation_plan_markdown": str(
            output_paths.get("benchmarkization_remediation_plan_markdown") or ""
        ),
        "benchmarkization_auto_runnable_action_count": _int(
            remediation_plan.get("auto_runnable_action_count", 0)
        ),
        "benchmarkization_manual_action_count": _int(
            remediation_plan.get("manual_action_count", 0)
        ),
        "repository_profile": repository_profile,
        "test_source_count": _int(repository_profile.get("test_source_count", 0)),
        "test_framework_signals": [
            str(item) for item in _list(repository_profile.get("test_framework_signals"))
        ],
        "framework_signals": [
            str(item) for item in _list(repository_profile.get("framework_signals"))
        ],
        "recommended_test_command": str(
            repository_profile.get("recommended_test_command") or ""
        ),
        "test_command_candidate_count": _int(
            repository_profile.get("test_command_candidate_count", 0)
        ),
        "top_test_command_runner": str(
            top_test_command_candidate.get("runner") or ""
        ),
        "top_test_command_reason": str(
            top_test_command_candidate.get("reason") or ""
        ),
        "recommended_target_prefix": str(
            repository_profile.get("recommended_target_prefix") or ""
        ),
        "project_config_count": _int(
            repository_profile.get("project_config_count", 0)
        ),
        "repository_test_command_status": str(
            repository_test.get("status") or ""
        ),
        "repository_test_command_executed": bool(
            repository_test.get("executed", False)
        ),
        "repository_test_command_reason": str(
            repository_test.get("reason") or ""
        ),
        "repository_test_setup_doctor_status": str(
            repository_setup_doctor.get("status") or ""
        ),
        "repository_test_setup_doctor_blocker": str(
            repository_setup_doctor.get("blocker") or ""
        ),
        "repository_test_setup_doctor_score": _float(
            repository_setup_doctor.get("score", 0.0)
        ),
        "repository_test_setup_doctor_next_action": str(
            repository_setup_doctor.get("next_action") or ""
        ),
        "repository_test_setup_doctor_check_count": _setup_doctor_check_count(
            repository_setup_doctor,
        ),
        "repository_test_setup_doctor_passed_check_count": _setup_doctor_status_count(
            repository_setup_doctor,
            "pass",
        ),
        "repository_test_setup_doctor_warning_check_count": _setup_doctor_status_count(
            repository_setup_doctor,
            "warning",
        ),
        "repository_test_setup_doctor_blocked_check_count": _setup_doctor_status_count(
            repository_setup_doctor,
            "blocked",
        ),
        "repository_test_setup_doctor_skipped_check_count": _setup_doctor_status_count(
            repository_setup_doctor,
            "skipped",
        ),
        "repository_test_setup_doctor_check_status_counts": (
            _setup_doctor_check_status_counts(repository_setup_doctor)
        ),
        "repository_test_setup_doctor_blocked_check_names": (
            _setup_doctor_names_by_status(repository_setup_doctor, "blocked")
        ),
        "repository_test_setup_doctor_warning_check_names": (
            _setup_doctor_names_by_status(repository_setup_doctor, "warning")
        ),
        "repository_test_environment_status": str(
            repository_environment.get("status") or ""
        ),
        "repository_test_environment_reason": str(
            repository_environment.get("reason") or ""
        ),
        "repository_config_snapshot_status": str(
            repository_config_snapshot.get("status") or ""
        ),
        "repository_config_snapshot_reason": str(
            repository_config_snapshot.get("reason") or ""
        ),
        "repository_config_snapshot_file_count": _int(
            repository_config_snapshot.get("file_count", 0)
        ),
        "repository_test_pytest_config_source_count": _int(
            repository_environment.get("pytest_config_source_count", 0)
        ),
        "repository_test_pytest_addopts": [
            str(item) for item in _list(repository_environment.get("pytest_config_addopts"))
        ],
        "repository_test_pytest_testpaths": [
            str(item) for item in _list(repository_environment.get("pytest_config_testpaths"))
        ],
        "repository_test_ci_config_source_count": _int(
            repository_environment.get("ci_config_source_count", 0)
        ),
        "repository_test_ci_python_versions": [
            str(item) for item in _list(repository_ci_config.get("python_versions"))
        ],
        "repository_test_ci_install_command_candidates": [
            str(_dict(item).get("command") or "")
            for item in _list(repository_ci_config.get("install_commands"))
            if str(_dict(item).get("command") or "")
        ],
        "repository_test_ci_test_command_candidates": [
            str(_dict(item).get("command") or "")
            for item in _list(repository_ci_config.get("test_commands"))
            if str(_dict(item).get("command") or "")
        ],
        "repository_test_tox_envlist": [
            str(item) for item in _list(repository_ci_config.get("tox_envlist"))
        ],
        "repository_test_framework_configuration_status": str(
            repository_framework_config.get("status") or ""
        ),
        "repository_test_framework_configuration_reason": str(
            repository_framework_config.get("reason") or ""
        ),
        "repository_test_frameworks": [
            str(item)
            for item in _list(repository_framework_config.get("frameworks"))
        ],
        "repository_test_app_bootstrap_candidate_count": _int(
            repository_framework_config.get("app_bootstrap_candidate_count", 0)
        ),
        "repository_test_app_bootstrap_candidates": [
            str(item.get("app_import") or "")
            for item in repository_app_bootstrap_candidates[:5]
            if str(item.get("app_import") or "")
        ],
        "repository_test_bootstrap_signal_count": _int(
            repository_framework_config.get("test_bootstrap_signal_count", 0)
        ),
        "repository_test_environment_setup_status": str(
            repository_environment_setup.get("status") or ""
        ),
        "repository_test_environment_setup_reason": str(
            repository_environment_setup.get("reason") or ""
        ),
        "repository_test_environment_setup_supported": bool(
            repository_environment_setup.get("install_command_supported", False)
        ),
        "repository_test_environment_setup_venv_path": str(
            repository_environment_setup.get("venv_path") or ""
        ),
        "repository_test_environment_setup_result_status": str(
            repository_environment_setup_result.get("status") or ""
        ),
        "repository_test_environment_setup_result_reason": str(
            repository_environment_setup_result.get("reason") or ""
        ),
        "repository_test_environment_setup_result_executed": bool(
            repository_environment_setup_result.get("executed", False)
        ),
        "repository_test_environment_setup_install_failure_category": str(
            repository_environment_setup_result.get("install_failure_category")
            or ""
        ),
        "repository_test_environment_setup_install_failure_signal": str(
            repository_environment_setup_result.get("install_failure_signal")
            or ""
        ),
        "repository_test_environment_setup_install_fallback_executed": bool(
            repository_environment_setup_result.get(
                "install_fallback_executed",
                False,
            )
        ),
        "repository_test_environment_setup_install_fallback_returncode": (
            repository_environment_setup_result.get("install_fallback_returncode")
        ),
        "repository_test_execution_plan_status": str(
            repository_execution_plan.get("status") or ""
        ),
        "repository_test_execution_plan_reason": str(
            repository_execution_plan.get("reason") or ""
        ),
        "planned_repository_test_command": str(
            repository_execution_plan.get("recommended_execution_command") or ""
        ),
        "planned_repository_test_level": str(
            repository_execution_plan.get("recommended_execution_level") or ""
        ),
        "planned_repository_test_risk": str(
            repository_execution_plan.get("recommended_execution_risk") or ""
        ),
        "planned_repository_test_runner": str(
            repository_execution_plan.get("recommended_execution_runner") or ""
        ),
        "planned_repository_test_source": str(
            repository_execution_recommended.get("source") or ""
        ),
        "planned_repository_test_preferred_runner": str(
            repository_execution_plan.get("preferred_test_runner") or ""
        ),
        "planned_repository_test_runner_fallback_used": bool(
            repository_execution_plan.get("runner_fallback_used", False)
        ),
        "planned_repository_test_runner_fallback_reason": str(
            repository_execution_plan.get("runner_fallback_reason") or ""
        ),
        "planned_repository_test_runner_fallback_from": str(
            repository_execution_plan.get("runner_fallback_from") or ""
        ),
        "planned_repository_test_runner_fallback_to": str(
            repository_execution_plan.get("runner_fallback_to") or ""
        ),
        "planned_repository_test_ci_candidate_count": _int(
            repository_execution_plan.get("ci_test_command_candidate_count", 0)
        ),
        "planned_repository_test_ci_command_candidates": [
            str(item)
            for item in _list(
                repository_execution_plan.get("ci_test_command_candidates")
            )
        ],
        "planned_repository_test_executable_now": bool(
            repository_execution_plan.get("executable_now", False)
        ),
        "planned_repository_test_environment_variable_names": [
            str(item)
            for item in _list(
                repository_execution_plan.get("planned_environment_variable_names")
            )
        ],
        "planned_repository_test_automatic_env_var_names": [
            str(item)
            for item in _list(
                repository_execution_result.get("automatic_environment_variable_names")
            )
        ],
        "planned_repository_test_result_status": str(
            repository_execution_result.get("status") or ""
        ),
        "planned_repository_test_result_reason": str(
            repository_execution_result.get("reason") or ""
        ),
        "planned_repository_test_result_executed": bool(
            repository_execution_result.get("executed", False)
        ),
        "planned_repository_test_result_passed": _int(
            repository_execution_result.get("passed", 0)
        ),
        "planned_repository_test_result_failed": _int(
            repository_execution_result.get("failed", 0)
        ),
        "planned_repository_test_result_errors": _int(
            repository_execution_result.get("errors", 0)
        ),
        "planned_repository_test_result_skipped": _int(
            repository_execution_result.get("skipped", 0)
        ),
        "planned_repository_test_result_test_count": _int(
            repository_execution_result.get("test_count", 0)
        ),
        "planned_repository_test_result_test_count_source": str(
            repository_execution_result.get("test_count_source") or ""
        ),
        "planned_repository_test_python_executable": str(
            repository_execution_result.get("python_executable") or ""
        ),
        "planned_repository_test_python_source": str(
            repository_execution_result.get("python_executable_source") or ""
        ),
        "planned_repository_test_failure_category": str(
            repository_execution_result.get("failure_category") or ""
        ),
        "planned_repository_test_failure_signal": str(
            repository_execution_result.get("failure_signal") or ""
        ),
        "planned_repository_test_failure_context_line_count": _int(
            repository_execution_result.get("failure_context_line_count", 0)
        ),
        "repository_test_retry_status": str(
            repository_retry_plan.get("status") or ""
        ),
        "repository_test_retry_reason": str(
            repository_retry_plan.get("reason") or ""
        ),
        "repository_test_retry_recommended": bool(
            repository_retry_plan.get("retry_recommended", False)
        ),
        "repository_test_retry_strategy": str(
            repository_retry_plan.get("retry_strategy") or ""
        ),
        "repository_test_retry_command": str(
            repository_retry_plan.get("retry_command") or ""
        ),
        "repository_test_retry_execution_status": str(
            repository_retry_execution_result.get("status") or ""
        ),
        "repository_test_retry_execution_reason": str(
            repository_retry_execution_result.get("reason") or ""
        ),
        "repository_test_retry_executed": bool(
            repository_retry_execution_result.get("executed", False)
        ),
        "repository_test_retry_setup_prerequisite_required": bool(
            repository_retry_execution_result.get(
                "retry_setup_prerequisite_required",
                False,
            )
        ),
        "repository_test_retry_setup_prerequisite_satisfied": bool(
            repository_retry_execution_result.get(
                "retry_setup_prerequisite_satisfied",
                False,
            )
        ),
        "repository_test_retry_setup_prerequisite_status": str(
            repository_retry_execution_result.get(
                "retry_setup_prerequisite_status"
            )
            or ""
        ),
        "repository_test_retry_setup_prerequisite_auto_executed": bool(
            repository_retry_execution_result.get(
                "retry_setup_prerequisite_auto_executed",
                False,
            )
        ),
        "repository_test_retry_execution_passed": _int(
            repository_retry_execution_result.get("passed", 0)
        ),
        "repository_test_retry_execution_failed": _int(
            repository_retry_execution_result.get("failed", 0)
        ),
        "repository_test_timeout_narrowing_status": str(
            repository_timeout_narrowing.get("status") or ""
        ),
        "repository_test_timeout_narrowing_reason": str(
            repository_timeout_narrowing.get("reason") or ""
        ),
        "repository_test_timeout_narrowing_executed": bool(
            repository_timeout_narrowing.get("executed", False)
        ),
        "repository_test_timeout_narrowing_attempt_count": _int(
            repository_timeout_narrowing.get("attempt_count", 0)
        ),
        "repository_test_timeout_narrowing_selected_command": str(
            repository_timeout_narrowing.get("selected_command") or ""
        ),
        "repository_test_timeout_narrowing_selected_failure_category": str(
            repository_timeout_narrowing.get("selected_failure_category") or ""
        ),
        "repository_test_dynamic_evidence_level": str(
            repository_dynamic_evidence.get("evidence_level") or ""
        ),
        "repository_test_dynamic_evidence_reason": str(
            repository_dynamic_evidence.get("reason") or ""
        ),
        "repository_test_dynamic_failing_tests": _int(
            repository_dynamic_evidence.get("failing_test_count", 0)
        ),
        "repository_test_dynamic_traceback_frames": _int(
            repository_dynamic_evidence.get("traceback_frame_count", 0)
        ),
        "repository_test_dynamic_failed_tests": _int(
            repository_dynamic_evidence.get("failed_test_count", 0)
        ),
        "repository_test_dynamic_passed_tests": _int(
            repository_dynamic_evidence.get("passed_test_count", 0)
        ),
        "repository_test_dynamic_usable_for_localization": bool(
            repository_dynamic_evidence.get("usable_for_localization", False)
        ),
        "repository_test_dynamic_usable_for_regression_validation": bool(
            repository_dynamic_evidence.get(
                "usable_for_regression_validation",
                False,
            )
        ),
        "repository_test_dynamic_usable_for_patch_validation": bool(
            repository_dynamic_evidence.get("usable_for_patch_validation", False)
        ),
        "repository_test_dynamic_validation_command": str(
            repository_dynamic_evidence.get("recommended_validation_command") or ""
        ),
        "repository_test_analysis_source": str(
            repository_analysis_route.get("analysis_source") or ""
        ),
        "repository_test_overlay_trigger_reason": str(
            repository_analysis_route.get("overlay_trigger_reason") or ""
        ),
        "repository_test_phase2_ready": bool(
            repository_analysis_route.get("phase2_ready", False)
        ),
        "repository_test_phase3_validation_ready": bool(
            repository_analysis_route.get("phase3_validation_ready", False)
        ),
        "repository_test_final_status": str(
            repository_final_diagnosis.get("final_status") or ""
        ),
        "repository_test_final_reason": str(
            repository_final_diagnosis.get("final_reason") or ""
        ),
        "repository_test_failure_overlay_status": str(
            repository_failure_overlay.get("status") or ""
        ),
        "repository_test_failure_overlay_reason": str(
            repository_failure_overlay.get("reason") or ""
        ),
        "repository_test_failure_overlay_attempted_cases": _int(
            repository_failure_overlay.get("attempted_case_count", 0)
        ),
        "repository_test_failure_overlay_supported_candidates": _int(
            repository_failure_overlay.get("supported_candidate_count", 0)
        ),
        "repository_test_failure_overlay_candidate_limit": _int(
            repository_test_config.get("failure_overlay_candidate_limit", 0)
        ),
        "repository_test_patch_validation_limit": _int(
            repository_test_config.get("patch_validation_limit", 0)
        ),
        "repository_test_failure_overlay_strategy_policy": str(
            repository_failure_overlay_strategy.get("policy") or ""
        ),
        "repository_test_failure_overlay_candidate_rule_counts": dict(
            _dict(repository_failure_overlay_strategy.get("candidate_rule_counts"))
        ),
        "repository_test_failure_overlay_attempted_rule_counts": dict(
            _dict(repository_failure_overlay_strategy.get("attempted_rule_counts"))
        ),
        "repository_test_failure_overlay_triggered_rule_counts": dict(
            _dict(repository_failure_overlay_strategy.get("triggered_rule_counts"))
        ),
        "repository_test_failure_overlay_candidate_rejection_count": _int(
            repository_failure_overlay_strategy.get("candidate_rejection_count", 0)
        ),
        "repository_test_failure_overlay_candidate_rejection_counts": dict(
            _dict(repository_failure_overlay_strategy.get("candidate_rejection_counts"))
        ),
        "repository_test_failure_overlay_candidate_rejection_rule_counts": dict(
            _dict(
                repository_failure_overlay_strategy.get(
                    "candidate_rejection_rule_counts"
                )
            )
        ),
        "repository_test_failure_overlay_candidate_rejection_examples": _list(
            repository_failure_overlay_strategy.get("candidate_rejection_examples")
        ),
        "repository_test_failure_overlay_dominant_rejection_reason": str(
            repository_failure_overlay_strategy.get(
                "dominant_candidate_rejection_reason"
            )
            or ""
        ),
        "repository_test_failure_overlay_dominant_rejection_count": _int(
            repository_failure_overlay_strategy.get(
                "dominant_candidate_rejection_count",
                0,
            )
        ),
        "repository_test_failure_overlay_rejection_recommendations": _list(
            repository_failure_overlay_strategy.get(
                "candidate_rejection_recommendations"
            )
        ),
        "repository_test_failure_overlay_next_extension": dict(
            _dict(repository_failure_overlay_strategy.get("next_overlay_extension"))
        ),
        "repository_test_failure_overlay_next_actionable_extension": dict(
            _dict(
                repository_failure_overlay_strategy.get(
                    "next_actionable_overlay_extension"
                )
            )
        ),
        "repository_test_failure_overlay_selected_candidate_rank": _int(
            repository_failure_overlay_strategy.get("selected_candidate_rank", 0)
        ),
        "repository_test_failure_overlay_selected_rule": str(
            repository_failure_overlay_case.get("rule_id") or ""
        ),
        "repository_test_failure_overlay_selected_function": str(
            repository_failure_overlay_case.get("function_name") or ""
        ),
        "repository_test_failure_overlay_selected_score": _float(
            repository_failure_overlay_strategy.get("selected_score", 0.0)
        ),
        "repository_test_failure_overlay_average_candidate_score": _float(
            repository_failure_overlay_strategy.get("average_candidate_score", 0.0)
        ),
        "repository_test_failure_overlay_selected_score_breakdown": dict(
            _dict(repository_failure_overlay_strategy.get("selected_score_breakdown"))
        ),
        "repository_test_failure_overlay_candidate_score_preview": _list(
            repository_failure_overlay_strategy.get("candidate_score_preview")
        ),
        "repository_test_failure_overlay_dynamic_evidence_level": str(
            repository_failure_overlay_dynamic.get("evidence_level") or ""
        ),
        "repository_test_failure_overlay_validation_command": str(
            repository_failure_overlay.get("recommended_validation_command") or ""
        ),
        "repository_test_fault_localization_status": str(
            repository_fault_localization.get("status") or ""
        ),
        "repository_test_fault_localization_reason": str(
            repository_fault_localization.get("reason") or ""
        ),
        "repository_test_fault_localization_ranking_count": _int(
            repository_fault_localization.get("ranking_count", 0)
        ),
        "repository_test_fault_localization_top_function": str(
            repository_fault_localization.get("top_function") or ""
        ),
        "repository_test_fault_localization_top_score": _float(
            repository_fault_localization.get("top_score", 0.0)
        ),
        "repository_test_fault_localization_matched_failed_tests": _int(
            repository_fault_localization.get("matched_failed_test_count", 0)
        ),
        "repository_test_fault_localization_unmatched_failed_tests": _int(
            repository_fault_localization.get("unmatched_failed_test_count", 0)
        ),
        "repository_test_fault_localization_traceback_frames": _int(
            repository_fault_localization.get("traceback_frame_count", 0)
        ),
        "repository_test_fault_localization_matched_traceback_frames": _int(
            repository_fault_localization.get("matched_traceback_frame_count", 0)
        ),
        "repository_test_fault_localization_unmatched_traceback_frames": _int(
            repository_fault_localization.get("unmatched_traceback_frame_count", 0)
        ),
        "repository_test_patch_candidates_status": str(
            repository_patch_candidates.get("status") or ""
        ),
        "repository_test_patch_candidates_reason": str(
            repository_patch_candidates.get("reason") or ""
        ),
        "repository_test_patch_candidate_count": _int(
            repository_patch_candidates.get("candidate_count", 0)
        ),
        "repository_patch_generation_mode": str(
            repository_patch_candidates.get("patch_generation_mode") or ""
        ),
        "repository_patch_generator_counts": _dict(
            repository_patch_candidates.get("generator_counts")
        ),
        "repository_patch_candidate_variant_filter": _dict(
            repository_patch_candidates.get("candidate_variant_filter")
        ),
        "repository_llm_patch_generation_status": str(
            repository_patch_candidates.get("llm_generation_status") or ""
        ),
        "repository_llm_patch_generation_reason": str(
            repository_patch_candidates.get("llm_generation_reason") or ""
        ),
        "repository_patch_safety_gate_status": str(
            _dict(repository_patch_candidates.get("safety_gate")).get("status") or ""
        ),
        "repository_patch_safety_gate_blocked_count": _int(
            _dict(repository_patch_candidates.get("safety_gate")).get(
                "blocked_count",
                0,
            )
        ),
        "repository_test_patch_target_function_count": _int(
            repository_patch_candidates.get("target_function_count", 0)
        ),
        "repository_test_patch_validation_command": str(
            repository_patch_candidates.get("recommended_validation_command") or ""
        ),
        "repository_test_patch_validation_status": str(
            repository_patch_validation.get("status") or ""
        ),
        "repository_test_patch_validation_reason": str(
            repository_patch_validation.get("reason") or ""
        ),
        "repository_test_patch_validation_input_candidate_count": _int(
            repository_patch_validation.get("input_candidate_count", 0)
        ),
        "repository_test_patch_validation_candidate_count": _int(
            repository_patch_validation.get("candidate_count", 0)
        ),
        "repository_test_patch_validation_safety_blocked_candidate_count": _int(
            repository_patch_validation.get("safety_blocked_candidate_count", 0)
        ),
        "repository_test_patch_validation_executed_count": _int(
            repository_patch_validation.get("executed_count", 0)
        ),
        "repository_test_patch_validation_success_count": _int(
            repository_patch_validation.get("success_count", 0)
        ),
        "repository_test_repair_ready": bool(
            repository_patch_validation.get("repair_ready", False)
        ),
        "repository_test_repair_validation_scope": str(
            repository_patch_validation.get("repair_validation_scope") or ""
        ),
        "repository_test_regression_ready": bool(
            repository_patch_validation.get("regression_ready", False)
        ),
        "repository_test_regression_validation_status": str(
            repository_regression_validation.get("status") or ""
        ),
        "repository_test_regression_validation_reason": str(
            repository_regression_validation.get("reason") or ""
        ),
        "repository_test_regression_validation_command": str(
            repository_regression_validation.get("validation_command") or ""
        ),
        "repository_test_regression_validation_passed": _int(
            repository_regression_validation.get("passed", 0)
        ),
        "repository_test_regression_validation_failed": _int(
            repository_regression_validation.get("failed", 0)
        ),
        "repository_test_patch_validation_reflection_mode": str(
            repository_patch_validation.get("reflection_mode") or ""
        ),
        "repository_test_patch_validation_refiner_status": str(
            repository_patch_validation.get("reflection_refiner_status") or ""
        ),
        "repository_test_patch_validation_refiner_reason": str(
            repository_patch_validation.get("reflection_refiner_reason") or ""
        ),
        "repository_test_patch_validation_reflection_candidate_count": _int(
            repository_patch_validation.get("reflection_candidate_count", 0)
        ),
        "repository_test_patch_validation_successful_reflection_count": _int(
            repository_patch_validation.get(
                "successful_reflection_candidate_count",
                0,
            )
        ),
        "repository_test_patch_validation_regression_reflection_candidate_count": _int(
            repository_patch_validation.get("regression_reflection_candidate_count", 0)
        ),
        "repository_test_patch_validation_successful_regression_reflection_count": _int(
            repository_patch_validation.get(
                "successful_regression_reflection_candidate_count",
                0,
            )
        ),
        "repository_test_patch_validation_max_depth": _int(
            repository_patch_validation.get("max_depth_executed", 0)
        ),
        "repository_test_patch_validation_failure_type_counts": dict(
            _dict(repository_patch_validation.get("failure_type_counts"))
        ),
        "repository_test_patch_judge_mode": str(
            repository_patch_validation.get("patch_judge_mode") or ""
        ),
        "repository_test_patch_judge_status": str(
            repository_patch_validation.get("patch_judge_status") or ""
        ),
        "repository_test_patch_judge_reason": str(
            repository_patch_validation.get("patch_judge_reason") or ""
        ),
        "repository_test_patch_judge_enabled": bool(
            repository_patch_validation.get("patch_judge_enabled", False)
        ),
        "repository_test_patch_judge_candidate_count": _int(
            repository_patch_validation.get("patch_judge_candidate_count", 0)
        ),
        "repository_test_patch_judge_verdict_counts": dict(
            _dict(repository_patch_validation.get("patch_judge_verdict_counts"))
        ),
        "repository_test_patch_judge_agreement_counts": dict(
            _dict(repository_patch_validation.get("patch_judge_agreement_counts"))
        ),
        "repository_test_patch_judge_authority": str(
            repository_patch_validation.get("patch_judge_authority") or ""
        ),
        "repository_test_patch_judge_config_audit": _dict(
            repository_patch_validation.get("patch_judge_config_audit")
        ),
        "repository_test_reflection_trace_status": (
            "pass"
            if str(repository_patch_validation.get("status") or "") == "pass"
            else (
                "review"
                if repository_patch_validation
                and str(repository_patch_validation.get("status") or "") != "skipped"
                else (
                    "skipped"
                    if str(repository_patch_validation.get("status") or "")
                    else ""
                )
            )
        ),
        "repository_test_reflection_trace_reason": repository_reflection_trace_reason,
        "repository_test_reflection_trace_initial_failure_count": len(
            repository_reflection_initial_failures
        ),
        "repository_test_reflection_trace_step_count": len(
            repository_reflection_steps
        ),
        "repository_test_reflection_trace_successful_step_count": len(
            repository_successful_reflection_steps
        ),
        "repository_test_reflection_trace_path": str(
            _dict(onboarding.get("output_paths")).get(
                "repository_test_reflection_trace_json"
            )
            or ""
        ),
        "repository_test_reflection_trace_markdown": str(
            _dict(onboarding.get("output_paths")).get(
                "repository_test_reflection_trace_markdown"
            )
            or ""
        ),
        "reflection_trace_path": str(
            _dict(onboarding.get("output_paths")).get("reflection_trace_json")
            or ""
        ),
        "reflection_trace_markdown": str(
            _dict(onboarding.get("output_paths")).get("reflection_trace_markdown")
            or ""
        ),
        "repository_test_best_patch_candidate_id": str(
            repository_patch_validation.get("best_candidate_id") or ""
        ),
        "repository_test_best_patch_candidate_rule_id": str(
            repository_patch_validation.get("best_candidate_rule_id") or ""
        ),
        "repository_test_best_patch_candidate_variant": str(
            repository_patch_validation.get("best_candidate_variant") or ""
        ),
        "repository_test_best_patch_candidate_success": bool(
            repository_patch_validation.get("best_candidate_success", False)
        ),
        "repository_test_best_patch_relative_file_path": str(
            repository_best_patch.get("relative_file_path") or ""
        ),
        "repository_test_best_patch_rule_id": str(
            repository_best_patch.get("rule_id") or ""
        ),
        "repository_test_best_patch_variant": str(
            repository_best_patch.get("variant") or ""
        ),
        "repository_test_best_patch_depth": _int(
            repository_best_patch.get("depth", 0)
        ),
        "repository_test_best_patch_parent_candidate_id": str(
            repository_best_patch.get("parent_candidate_id") or ""
        ),
        "repository_test_best_patch_has_diff": bool(
            str(repository_best_patch.get("diff") or "")
        ),
        "repository_test_repair_patch_path": str(
            _dict(onboarding.get("output_paths")).get("repository_test_repair_patch")
            or ""
        ),
        "repository_test_repair_summary_status": str(
            repository_repair_summary.get("status") or ""
        ),
        "repository_test_repair_summary_reason": str(
            repository_repair_summary.get("reason") or ""
        ),
        "repository_test_repair_summary_conclusion": str(
            repository_repair_summary.get("conclusion") or ""
        ),
        "repository_test_repair_summary_path": str(
            _dict(onboarding.get("output_paths")).get(
                "repository_test_repair_summary_markdown"
            )
            or ""
        ),
        "recommended_install_command": str(
            repository_environment.get("recommended_install_command") or ""
        ),
        "repository_test_module": str(repository_environment.get("test_module") or ""),
        "repository_test_tool_available": repository_environment.get(
            "test_tool_available"
        ),
        "repository_checkout_status": str(repository_checkout.get("status") or ""),
        "repository_checkout_reason": str(repository_checkout.get("reason") or ""),
        "repository_checkout_method": str(
            repository_checkout.get("checkout_method") or ""
        ),
        "repository_checkout_path": str(
            repository_checkout.get("checkout_path") or ""
        ),
        "repository_checkout_source_discovery_present": bool(
            repository_checkout_sources
        ),
        "repository_checkout_source_count": _int(
            repository_checkout_source_metadata.get("included_file_count", 0)
        ),
        "repository_checkout_source_reason": str(
            repository_checkout_source_metadata.get("reason") or ""
        ),
        "quality_gate_present": bool(quality_gate),
        "quality_gate_passed": quality_gate.get("passed") if quality_gate else None,
        "smoke_validation_present": bool(smoke_validation),
        "smoke_validation_passed": (
            smoke_validation.get("passed") if smoke_validation else None
        ),
        "diagnostics_status": str(diagnostics_headline.get("status") or ""),
        "first_failing_stage": str(
            diagnostics_headline.get("first_failing_stage") or ""
        ),
        "diagnostic_issue_count": _int(diagnostics_headline.get("issue_count", 0)),
        "diagnostic_error_count": _int(diagnostics_headline.get("error_count", 0)),
        "diagnostic_warning_count": _int(
            diagnostics_headline.get("warning_count", 0)
        ),
        "diagnostic_info_count": _int(diagnostics_headline.get("info_count", 0)),
        "diagnostic_issue_codes": [
            str(_dict(issue).get("code") or "")
            for issue in diagnostic_issues
            if _dict(issue).get("code")
        ],
        "diagnostic_issue_preview": [
            {
                "stage": str(_dict(issue).get("stage") or ""),
                "severity": str(_dict(issue).get("severity") or ""),
                "code": str(_dict(issue).get("code") or ""),
                "message": str(_dict(issue).get("message") or ""),
            }
            for issue in diagnostic_issues[:5]
        ],
        "recipe_miss_count": len(_list(diagnostics.get("recipe_misses"))),
        "recipe_suggestion_count": len(recipe_suggestions),
        "recipe_suggestion_preview": [
            _recipe_suggestion_preview(suggestion)
            for suggestion in recipe_suggestions[:5]
        ],
        "next_actions": _list(diagnostics.get("next_actions")),
    }
    summary.update(_static_intelligence_summary(summary, mining_report))
    agent_execution_plan = _agent_execution_plan(summary)
    summary["agent_execution_plan"] = agent_execution_plan
    summary["agent_execution_plan_status_counts"] = _agent_plan_status_counts(
        agent_execution_plan
    )
    summary["agent_execution_plan_primary_blocker"] = (
        _agent_plan_primary_blocker(agent_execution_plan)
    )
    summary["agent_execution_plan_next_action"] = _agent_plan_next_action(
        agent_execution_plan
    )
    summary["agent_execution_plan_next_command"] = _agent_plan_next_command(
        agent_execution_plan
    )
    return summary


def _static_intelligence_summary(
    summary: dict[str, Any],
    mining_report: dict[str, Any],
) -> dict[str, Any]:
    mining_quality = _dict(mining_report.get("quality_summary"))
    imported_sources = _int(summary.get("imported_sources", 0))
    selected_sources = _int(summary.get("selected_sources", 0))
    selected_signals = _int(summary.get("generated_candidates", 0))
    total_signals = _int(mining_report.get("generated_count", 0)) or selected_signals
    rule_counts = dict(_dict(mining_report.get("rule_counts"))) or dict(
        _dict(summary.get("repository_test_failure_overlay_candidate_rule_counts"))
    )
    bug_type_counts = dict(_dict(mining_report.get("bug_type_counts")))
    source_hit_rate = _float(
        summary.get("source_hit_rate", mining_quality.get("source_hit_rate", 0.0))
    ) or _float(mining_quality.get("source_hit_rate", 0.0))
    candidate_density = _float(
        summary.get(
            "candidate_density",
            mining_quality.get("candidate_density", 0.0),
        )
    ) or _float(mining_quality.get("candidate_density", 0.0))
    quality_score = _float(summary.get("quality_score", 0.0)) or _float(
        mining_quality.get("quality_score", 0.0)
    )
    if imported_sources <= 0:
        status = "blocked"
        level = "no_python_sources"
        reason = "no_imported_sources"
    elif selected_sources <= 0:
        status = "blocked"
        level = "no_selected_sources"
        reason = "source_selection_empty"
    elif total_signals > 0:
        status = "analysis_ready"
        level = (
            "static_signals_with_dynamic_evidence"
            if str(summary.get("repository_test_dynamic_evidence_level") or "")
            not in {"", "not_executed", "none"}
            else "static_signals"
        )
        reason = "mined_static_candidates"
    else:
        status = "source_inventory_ready"
        level = "source_only"
        reason = "no_static_candidates"
    return {
        "static_intelligence_status": status,
        "static_intelligence_level": level,
        "static_intelligence_reason": reason,
        "static_intelligence_imported_source_count": imported_sources,
        "static_intelligence_selected_source_count": selected_sources,
        "static_intelligence_selected_signal_count": selected_signals,
        "static_intelligence_total_signal_count": total_signals,
        "static_intelligence_candidate_limit_applied": bool(
            summary.get("candidate_limit_applied", False)
        )
        or selected_signals < total_signals,
        "static_intelligence_rule_counts": rule_counts,
        "static_intelligence_bug_type_counts": bug_type_counts,
        "static_intelligence_source_hit_rate": source_hit_rate,
        "static_intelligence_candidate_density": candidate_density,
        "static_intelligence_rule_diversity": _int(
            summary.get(
                "selected_rule_count",
                mining_quality.get("rule_diversity_count", len(rule_counts)),
            )
        ),
        "static_intelligence_bug_type_diversity": _int(
            summary.get(
                "selected_bug_type_count",
                mining_quality.get("bug_type_diversity_count", len(bug_type_counts)),
            )
        ),
        "static_intelligence_quality_score": quality_score,
        "static_intelligence_dynamic_validation_level": str(
            summary.get("repository_test_dynamic_evidence_level") or "none"
        ),
        "static_intelligence_primary_artifact": str(
            summary.get("source_mining_markdown_path") or ""
        ),
        "static_intelligence_next_action": _static_intelligence_next_action(
            status=status,
            selected_signals=selected_signals,
            total_signals=total_signals,
            summary=summary,
        ),
    }


def _static_intelligence_next_action(
    *,
    status: str,
    selected_signals: int,
    total_signals: int,
    summary: dict[str, Any],
) -> str:
    if status == "blocked":
        return "Adjust include/exclude filters or target prefix so Python sources can be imported."
    if total_signals <= 0:
        return "Inspect source_mining.md recipe misses, then broaden recipes or target prefix."
    if selected_signals <= 0:
        return "Raise max candidates or inspect omitted candidate counts before benchmark generation."
    if str(summary.get("repository_test_final_status") or "") == "blocked":
        setup_action = str(summary.get("repository_test_setup_doctor_next_action") or "")
        if setup_action:
            return setup_action
        return "Run repository tests or benchmark remediation to attach dynamic evidence."
    if str(summary.get("benchmarkization_status") or "") == "ready_to_run_benchmark":
        command = str(summary.get("benchmarkization_primary_action_command") or "")
        return command or "Run template benchmark to validate mined static candidates."
    return "Use the static signal report to choose repository tests, fault localization, or benchmark validation."


def _agent_execution_plan(summary: dict[str, Any]) -> list[dict[str, Any]]:
    imported_sources = _int(summary.get("imported_sources", 0))
    selected_sources = _int(summary.get("selected_sources", 0))
    discovery_items = _int(summary.get("discovery_items", 0))
    benchmark_status = str(summary.get("benchmarkization_status") or "")
    benchmark_action_command = str(
        summary.get("benchmarkization_primary_action_command") or ""
    )
    setup_doctor_blocker = str(
        summary.get("repository_test_setup_doctor_blocker") or ""
    )
    setup_doctor_status = str(
        summary.get("repository_test_setup_doctor_status") or ""
    )
    planned_command = str(summary.get("planned_repository_test_command") or "")
    retry_command = str(summary.get("repository_test_retry_command") or "")
    final_status = str(summary.get("repository_test_final_status") or "")
    final_reason = str(summary.get("repository_test_final_reason") or "")
    repair_status = str(summary.get("repository_test_repair_summary_status") or "")
    repair_reason = str(summary.get("repository_test_repair_summary_reason") or "")
    repair_conclusion = str(
        summary.get("repository_test_repair_summary_conclusion") or ""
    )
    repair_patch_path = str(summary.get("repository_test_repair_patch_path") or "")
    return [
        _agent_plan_row(
            "source_discovery",
            status="pass" if imported_sources > 0 else "blocked",
            evidence=(
                f"discovery={discovery_items}, imported={imported_sources}, "
                f"selected={selected_sources}"
            ),
            blocker="none" if imported_sources > 0 else "source:no_python_sources",
            next_action=(
                "Proceed to benchmarkization."
                if imported_sources > 0
                else "Adjust repository include/exclude filters or select a Python repository."
            ),
        ),
        _agent_plan_row(
            "benchmarkization",
            status=_agent_plan_benchmarkization_status(summary),
            evidence=(
                f"status={benchmark_status or 'none'}, "
                f"candidates={_int(summary.get('generated_candidates', 0))}, "
                f"cases={_int(summary.get('benchmark_cases', 0))}"
            ),
            blocker=(
                "none"
                if bool(summary.get("benchmarkization_ready", False))
                else (
                    str(
                        _list(summary.get("benchmarkization_blocking_reasons"))[0]
                    )
                    if _list(summary.get("benchmarkization_blocking_reasons"))
                    else benchmark_status or "benchmarkization:not_ready"
                )
            ),
            next_action=_agent_plan_benchmarkization_next_action(summary),
            command=benchmark_action_command,
            artifact=str(
                summary.get("benchmarkization_remediation_plan_markdown") or ""
            ),
        ),
        _agent_plan_row(
            "repository_test_setup",
            status=_agent_plan_setup_status(setup_doctor_status),
            evidence=(
                f"doctor={setup_doctor_status or 'none'}, "
                f"blocker={setup_doctor_blocker or 'none'}, "
                f"score={_float(summary.get('repository_test_setup_doctor_score', 0.0)):.4f}"
            ),
            blocker=setup_doctor_blocker or "none",
            next_action=str(
                summary.get("repository_test_setup_doctor_next_action") or ""
            ),
            command=str(summary.get("recommended_install_command") or ""),
        ),
        _agent_plan_row(
            "repository_test_execution",
            status=_agent_plan_repository_execution_status(summary),
            evidence=(
                f"execution={summary.get('planned_repository_test_result_status') or 'none'}, "
                f"failure={summary.get('planned_repository_test_failure_category') or 'none'}, "
                f"final={final_status or 'none'}:{final_reason or 'none'}"
            ),
            blocker=final_reason or "none",
            next_action=_agent_plan_repository_execution_next_action(summary),
            command=retry_command or planned_command,
        ),
        _agent_plan_row(
            "repository_repair",
            status=_agent_plan_repository_repair_status(summary),
            evidence=(
                f"repair={repair_status or 'none'}:{repair_reason or 'none'}, "
                f"conclusion={repair_conclusion or 'none'}"
            ),
            blocker=repair_reason or final_reason or "none",
            next_action=_agent_plan_repository_repair_next_action(summary),
            command=repair_patch_path,
        ),
    ]


def _setup_doctor_check_count(payload: dict[str, Any]) -> int:
    explicit = _int(payload.get("check_count", 0))
    if explicit:
        return explicit
    return len(_list(payload.get("checks")))


def _setup_doctor_status_count(payload: dict[str, Any], status: str) -> int:
    key = f"{status}_check_count"
    explicit = _int(payload.get(key, 0))
    if explicit:
        return explicit
    return sum(
        1
        for check in _list(payload.get("checks"))
        if str(_dict(check).get("status") or "") == status
    )


def _setup_doctor_check_status_counts(payload: dict[str, Any]) -> dict[str, int]:
    explicit = _dict(payload.get("check_status_counts"))
    if explicit:
        return {
            str(key): _int(value)
            for key, value in sorted(explicit.items())
            if str(key)
        }
    counts: dict[str, int] = {}
    for check in _list(payload.get("checks")):
        status = str(_dict(check).get("status") or "")
        if not status:
            continue
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _setup_doctor_names_by_status(
    payload: dict[str, Any],
    status: str,
) -> list[str]:
    key = f"{status}_check_names"
    explicit = [str(item) for item in _list(payload.get(key)) if str(item)]
    if explicit:
        return explicit
    return [
        str(_dict(check).get("name") or "")
        for check in _list(payload.get("checks"))
        if str(_dict(check).get("status") or "") == status
        and str(_dict(check).get("name") or "")
    ]


def _agent_plan_row(
    stage: str,
    *,
    status: str,
    evidence: str,
    blocker: str = "none",
    next_action: str = "",
    command: str = "",
    artifact: str = "",
) -> dict[str, Any]:
    return {
        "stage": stage,
            "status": status or "unknown",
            "evidence": evidence,
            "blocker": blocker or "none",
            "next_action": next_action or "",
            "command": command or "",
            "artifact": artifact or "",
        }


def _agent_plan_benchmarkization_status(summary: dict[str, Any]) -> str:
    status = str(summary.get("benchmarkization_status") or "")
    if status == "ready_to_run_benchmark":
        return "ready"
    if (
        bool(summary.get("benchmarkization_ready", False))
        or status == "benchmark_ready"
        or _int(summary.get("benchmark_cases", 0)) > 0
    ):
        return "pass"
    if status in {"blocked", "not_ready", "failed"}:
        return "blocked"
    return "warn" if status else "unknown"


def _agent_plan_benchmarkization_next_action(summary: dict[str, Any]) -> str:
    actions = _list(summary.get("benchmarkization_next_actions"))
    if actions:
        return str(actions[0])
    command = str(summary.get("benchmarkization_primary_action_command") or "")
    if command:
        return "Run the primary benchmarkization remediation command."
    if bool(summary.get("benchmarkization_ready", False)):
        return "Use benchmark artifacts as repair/localization evidence."
    return "Inspect benchmarkization readiness diagnostics."


def _agent_plan_setup_status(status: str) -> str:
    if status in {"pass", "ready"}:
        return "pass"
    if status in {"blocked", "fail", "failed"}:
        return "blocked"
    if status in {"warn", "warning"}:
        return "warn"
    return "unknown" if not status else status


def _agent_plan_repository_execution_status(summary: dict[str, Any]) -> str:
    final_status = str(summary.get("repository_test_final_status") or "")
    if final_status == "repaired":
        return "pass"
    if final_status in {"phase2_ready", "phase3_ready"}:
        return "ready"
    if final_status == "blocked":
        return "blocked"
    result_status = str(summary.get("planned_repository_test_result_status") or "")
    if result_status in {"pass", "passed"}:
        return "pass"
    if result_status in {"fail", "failed"}:
        return "warn"
    if result_status == "skipped":
        return "blocked"
    return "unknown"


def _agent_plan_repository_execution_next_action(summary: dict[str, Any]) -> str:
    retry_command = str(summary.get("repository_test_retry_command") or "")
    if retry_command:
        return "Run the repository-test retry command."
    if bool(summary.get("planned_repository_test_executable_now", False)):
        return "Run the planned repository test command."
    setup_action = str(summary.get("repository_test_setup_doctor_next_action") or "")
    if setup_action:
        return setup_action
    return "Materialize repository tests and rerun repository-test execution."


def _agent_plan_repository_repair_status(summary: dict[str, Any]) -> str:
    if bool(summary.get("repository_test_repair_ready", False)):
        return "pass"
    final_status = str(summary.get("repository_test_final_status") or "")
    if final_status in {"phase2_ready", "phase3_ready"}:
        return "ready"
    repair_status = str(summary.get("repository_test_repair_summary_status") or "")
    if repair_status in {"pass", "ready"}:
        return "pass"
    if repair_status in {"skipped", "blocked", "fail", "failed"}:
        return "blocked"
    return "unknown" if not repair_status else repair_status


def _agent_plan_repository_repair_next_action(summary: dict[str, Any]) -> str:
    if str(summary.get("repository_test_repair_patch_path") or ""):
        return "Review the generated repair patch and validation summary."
    actionable_extension = _dict(
        summary.get("repository_test_failure_overlay_next_actionable_extension")
    )
    recommendation = str(actionable_extension.get("recommended_extension") or "")
    if recommendation:
        return f"Extend failure-overlay generation with {recommendation}."
    if str(summary.get("repository_test_retry_command") or ""):
        return "Run repository-test retry before repair."
    setup_action = str(summary.get("repository_test_setup_doctor_next_action") or "")
    if setup_action:
        return setup_action
    return "Collect usable dynamic evidence before patch generation."


def _agent_plan_status_counts(plan: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in plan:
        status = str(_dict(row).get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _agent_plan_primary_blocker(plan: list[dict[str, Any]]) -> str:
    for row in plan:
        item = _dict(row)
        status = str(item.get("status") or "")
        blocker = str(item.get("blocker") or "")
        if status not in {"pass", "ready", "skip"} and blocker != "none":
            return blocker
    return ""


def _agent_plan_next_action(plan: list[dict[str, Any]]) -> str:
    for row in plan:
        item = _dict(row)
        status = str(item.get("status") or "")
        action = str(item.get("next_action") or "")
        if status != "pass" and action:
            return action
    return ""


def _agent_plan_next_command(plan: list[dict[str, Any]]) -> str:
    for row in plan:
        item = _dict(row)
        status = str(item.get("status") or "")
        command = str(item.get("command") or "")
        if status != "pass" and command:
            return command
    return ""


def _repository_test_final_diagnosis(
    *,
    route: dict[str, Any],
    execution_result: dict[str, Any],
    fault_localization: dict[str, Any],
    patch_validation: dict[str, Any],
    framework_config: dict[str, Any] | None = None,
) -> dict[str, str]:
    if bool(patch_validation.get("repair_ready", False)):
        return {
            "final_status": "repaired",
            "final_reason": (
                "patch_validation_success_with_regression"
                if str(patch_validation.get("repair_validation_scope") or "")
                == "narrow_and_regression"
                else "patch_validation_success"
            ),
        }
    if (
        _int(patch_validation.get("success_count", 0)) > 0
        and str(patch_validation.get("repair_validation_scope") or "")
        == "regression_failed"
    ):
        return {
            "final_status": "phase3_ready",
            "final_reason": "regression_validation_failed",
        }

    if bool(route.get("phase3_validation_ready", False)):
        patch_status = str(patch_validation.get("status") or "").strip()
        return {
            "final_status": "phase3_ready",
            "final_reason": (
                "patch_validation_executed_without_success"
                if patch_status
                else "ready_for_patch_validation"
            ),
        }

    if bool(route.get("phase2_ready", False)):
        localization_status = str(fault_localization.get("status") or "").strip()
        return {
            "final_status": "phase2_ready",
            "final_reason": (
                "fault_localization_available"
                if localization_status == "pass"
                else "ready_for_fault_localization"
            ),
        }

    execution_status = str(execution_result.get("status") or "").strip()
    failure_category = str(execution_result.get("failure_category") or "").strip()
    analysis_source = str(route.get("analysis_source") or "").strip()
    if execution_status == "pass":
        reason = "repository_tests_passing"
    elif execution_status in {"error", "timeout"}:
        reason = f"repository_test_execution_{execution_status}"
    elif execution_status == "skipped":
        reason = "repository_test_not_executed"
    elif failure_category == "framework_configuration_error":
        reason = _framework_configuration_blocked_reason(framework_config or {})
    elif failure_category and failure_category not in {"none", "not_executed"}:
        reason = f"dynamic_evidence_not_usable:{failure_category}"
    elif analysis_source in {"", "none"}:
        reason = "dynamic_evidence_not_usable"
    else:
        reason = "repository_test_not_ready"
    return {
        "final_status": "blocked",
        "final_reason": reason,
    }


def _repository_reflection_trace_reason(
    patch_validation: dict[str, Any],
    *,
    initial_failure_count: int,
    reflection_step_count: int,
    successful_reflection_step_count: int,
) -> str:
    if not patch_validation:
        return ""
    status = str(patch_validation.get("status") or "")
    if status == "skipped":
        return "patch_validation_not_executed"
    if successful_reflection_step_count > 0:
        return "reflection_repaired_candidate"
    if reflection_step_count > 0:
        return "reflection_attempted_no_success"
    if bool(patch_validation.get("repair_ready", False)):
        return "depth0_success_no_reflection_needed"
    if initial_failure_count > 0 and bool(
        patch_validation.get("reflection_enabled", False)
    ):
        return "reflection_enabled_but_no_child_candidate"
    if initial_failure_count > 0:
        return "depth0_failures_without_reflection"
    return "depth0_success_no_reflection_needed"


def _framework_configuration_blocked_reason(framework_config: dict[str, Any]) -> str:
    reason = str(framework_config.get("reason") or "").strip()
    env_vars = _dict(framework_config.get("environment_variables"))
    if env_vars:
        suffix = reason or "planned_environment_variables_applied"
        return f"framework_configuration_injected_but_failed:{suffix}"
    if reason:
        return f"framework_configuration_pending:{reason}"
    return "framework_configuration_pending:missing_framework_test_settings"


def _agent_status(summary: dict[str, Any]) -> str:
    if summary.get("smoke_validation_present"):
        return "pass" if summary.get("smoke_validation_passed") else "fail"
    if summary.get("quality_gate_present"):
        return "pass" if summary.get("quality_gate_passed") else "fail"
    if _int(summary.get("generated_candidates", 0)) > 0:
        return "warning"
    return "fail"


def _write_agent_report(report: GitHubRepoAgentReport) -> None:
    Path(report.output_paths["agent_json"]).write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(report.output_paths["agent_markdown"]).write_text(
        render_github_repo_agent_markdown(report),
        encoding="utf-8",
    )
    _write_agent_execution_plan_artifacts(
        report,
        json_path=Path(report.output_paths["agent_execution_plan_json"]),
        markdown_path=Path(report.output_paths["agent_execution_plan_markdown"]),
    )


def _write_agent_snapshot(
    report: GitHubRepoAgentReport,
    json_path: Path,
    markdown_path: Path,
) -> None:
    json_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_github_repo_agent_markdown(report),
        encoding="utf-8",
    )
    _write_agent_execution_plan_artifacts(
        report,
        json_path=json_path.with_name(f"{json_path.stem}_execution_plan.json"),
        markdown_path=markdown_path.with_name(
            f"{markdown_path.stem}_execution_plan.md"
        ),
    )


def _write_agent_execution_plan_artifacts(
    report: GitHubRepoAgentReport,
    *,
    json_path: Path,
    markdown_path: Path,
) -> None:
    json_path.write_text(
        json.dumps(_agent_execution_plan_payload(report), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_github_repo_agent_execution_plan_markdown(report),
        encoding="utf-8",
    )


def _agent_execution_plan_payload(
    report: GitHubRepoAgentReport,
) -> dict[str, Any]:
    summary = report.summary
    return {
        "repo_spec": report.repo_spec,
        "owner": report.owner,
        "repo": report.repo,
        "output_dir": report.output_dir,
        "preset": report.preset,
        "status": report.status,
        "stage_count": len(_list(summary.get("agent_execution_plan"))),
        "status_counts": _dict(summary.get("agent_execution_plan_status_counts")),
        "primary_blocker": str(
            summary.get("agent_execution_plan_primary_blocker") or ""
        ),
        "next_action": str(summary.get("agent_execution_plan_next_action") or ""),
        "next_command": str(summary.get("agent_execution_plan_next_command") or ""),
        "stages": _list(summary.get("agent_execution_plan")),
    }


def _write_requested_outputs(
    report: GitHubRepoAgentReport,
    *,
    output_json: str | None,
    output_markdown: str | None,
) -> None:
    if output_json:
        Path(output_json).write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if output_markdown:
        Path(output_markdown).write_text(
            render_github_repo_agent_markdown(report),
            encoding="utf-8",
        )


def _github_error_next_actions(error: GitHubAPIError) -> list[str]:
    actions = [
        "Retry after confirming the repository exists and is reachable from this environment.",
        "Use --ref with a commit, tag, or branch when default-branch discovery is not the desired target.",
    ]
    if error.status_code in {403, 429} or error.rate_limit_remaining == "0":
        actions.insert(
            0,
            "Set GITHUB_TOKEN or pass --token-env with a token environment variable to avoid unauthenticated GitHub API limits.",
        )
    if error.status_code in {401, 404}:
        actions.insert(
            0,
            "Check repository visibility and token permissions for private or restricted repositories.",
        )
    return actions


def _recipe_suggestion_preview(value: Any) -> dict[str, Any]:
    suggestion = _dict(value)
    return {
        "recipe": str(suggestion.get("recipe") or ""),
        "miss_count": _int(suggestion.get("miss_count", 0)),
        "source_count": _int(suggestion.get("source_count", 0)),
        "top_reasons": [
            {
                "reason": str(_dict(reason).get("reason") or ""),
                "count": _int(_dict(reason).get("count", 0)),
            }
            for reason in _list(suggestion.get("top_reasons"))[:3]
        ],
        "suggested_actions": [
            str(action) for action in _list(suggestion.get("suggested_actions"))[:3]
        ],
    }


def _token_from_env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value else None


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


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={_int(value)}" for key, value in sorted(counts.items()))


def _format_overlay_dominant_rejection(summary: dict[str, Any]) -> str:
    reason = str(
        summary.get("repository_test_failure_overlay_dominant_rejection_reason") or ""
    )
    if not reason:
        return "none"
    count = _int(
        summary.get("repository_test_failure_overlay_dominant_rejection_count", 0)
    )
    return f"{reason}:{count}"


def _format_overlay_next_extension(summary: dict[str, Any]) -> str:
    extension = _dict(summary.get("repository_test_failure_overlay_next_extension"))
    reason = str(extension.get("reason") or "")
    recommendation = str(extension.get("recommended_extension") or "")
    if not reason and not recommendation:
        return "none"
    if not recommendation:
        return reason
    return f"{reason} -> {recommendation}"


def _format_overlay_next_actionable_extension(summary: dict[str, Any]) -> str:
    extension = _dict(
        summary.get("repository_test_failure_overlay_next_actionable_extension")
    )
    reason = str(extension.get("reason") or "")
    recommendation = str(extension.get("recommended_extension") or "")
    if not reason and not recommendation:
        return "none"
    if not recommendation:
        return reason
    return f"{reason} -> {recommendation}"


def _format_score_breakdown(breakdown: dict[str, Any]) -> str:
    if not breakdown:
        return "none"
    fields = [
        "score",
        "static_confidence",
        "rule_trigger_prior",
        "callable_kind_weight",
        "oracle_specificity",
        "assertion_oracle_bonus",
    ]
    parts = [
        f"{field}={_float(breakdown.get(field, 0.0)):.4f}"
        for field in fields
        if field in breakdown
    ]
    return ", ".join(parts) if parts else "none"


def _format_score_preview(items: list[Any]) -> str:
    rows: list[str] = []
    for item in items[:5]:
        row = _dict(item)
        rank = _int(row.get("rank", 0))
        rule = str(row.get("rule_id") or "unknown")
        function_name = str(row.get("function_name") or "unknown")
        score = _float(row.get("overlay_score", 0.0))
        rows.append(f"{rank}:{rule}@{function_name}={score:.4f}")
    return "; ".join(rows) if rows else "none"


def _format_rejection_examples(items: list[Any]) -> str:
    rows: list[str] = []
    for item in items[:5]:
        row = _dict(item)
        rule = str(row.get("rule_id") or "unknown")
        function_name = str(
            row.get("qualified_name")
            or row.get("function_name")
            or "unknown"
        )
        reason = str(row.get("reason") or "unknown")
        rows.append(f"{rule}@{function_name}:{reason}")
    return "; ".join(rows) if rows else "none"


def _status_word(value: Any) -> str:
    if value is None:
        return "not_run"
    return "PASS" if bool(value) else "FAIL"


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":  # pragma: no cover
    main()
