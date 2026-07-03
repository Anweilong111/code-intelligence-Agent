from __future__ import annotations

import argparse
import copy
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.github_onboarding_preflight_batch import (
    GitHubOnboardingPreflightBatchReport,
    run_github_onboarding_preflight_batch,
)
from code_intelligence_agent.evaluation.github_onboarding_smoke_runner import (
    OnboardingSmokeRunnerReport,
    render_onboarding_smoke_runner_markdown,
    run_onboarding_smoke_suite,
)
from code_intelligence_agent.evaluation.onboarding_recommendation_comparator import (
    OnboardingRecommendationComparison,
    compare_onboarding_recommendation_reports,
    render_onboarding_recommendation_comparison_markdown,
)


REPO_REPAIR_PROFILE_PROMOTION_GATE = {
    "min_smoke_benchmark_cases": 0,
    "min_repository_test_phase2_ready_count": 1,
    "min_repository_test_phase3_validation_ready_count": 1,
    "min_repository_test_repaired_count": 1,
    "allow_warning_stages": True,
}
REPO_REPAIR_PROFILE_FALLBACK_MAX_SOURCES = 50
REPO_REPAIR_PROFILE_FALLBACK_MAX_CANDIDATES = 20
REPO_REPAIR_PROFILE_OVERLAY_CANDIDATE_LIMIT = 8


@dataclass(frozen=True)
class GitHubOnboardingPipelineReport:
    manifest_path: str
    output_dir: str
    suite_name: str
    passed: bool
    summary: dict[str, Any]
    output_paths: dict[str, str]
    preflight_batch: dict[str, Any]
    smoke_runner: dict[str, Any] | None = None
    recommended_smoke_runner: dict[str, Any] | None = None
    recommendation_comparison: dict[str, Any] | None = None
    pipeline_showcase: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_github_onboarding_pipeline(
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    opener=None,
    run_recommendation_rerun: bool = True,
    promotion_config_overrides: dict[str, Any] | None = None,
) -> GitHubOnboardingPipelineReport:
    manifest = Path(manifest_path)
    manifest_payload = _read_json(manifest)
    promotion_config = _merge_promotion_config(
        _pipeline_promotion_config(manifest_payload),
        _dict(promotion_config_overrides),
    )
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    preflight_dir = output_root / "preflight"
    smoke_dir = output_root / "smoke"
    output_paths = _pipeline_output_paths(output_root, manifest)

    preflight = run_github_onboarding_preflight_batch(
        manifest,
        preflight_dir,
        opener=opener,
    )
    smoke: OnboardingSmokeRunnerReport | None = None
    recommended_smoke: OnboardingSmokeRunnerReport | None = None
    comparison: OnboardingRecommendationComparison | None = None
    recommended_pipeline_manifest_written = False
    if _int(preflight.summary.get("ready_count", 0)) > 0:
        smoke = run_onboarding_smoke_suite(
            preflight.output_paths["smoke_manifest"],
            smoke_dir,
            opener=opener,
        )
        _write_smoke_runner_artifacts(
            smoke,
            output_paths["smoke_runner_json"],
            output_paths["smoke_runner_markdown"],
        )
        recommendation_count = _recommended_manifest_applied_count(
            smoke.recommended_manifest_path
        )
        if recommendation_count > 0:
            _write_recommended_pipeline_manifest(
                source_manifest_path=smoke.recommended_manifest_path,
                original_manifest_payload=manifest_payload,
                target_path=output_paths["recommended_pipeline_manifest"],
            )
            recommended_pipeline_manifest_written = True
        if run_recommendation_rerun and recommendation_count > 0:
            recommended_dir = output_root / "recommended_smoke"
            recommended_smoke = run_onboarding_smoke_suite(
                smoke.recommended_manifest_path,
                recommended_dir,
                opener=opener,
            )
            _write_smoke_runner_artifacts(
                recommended_smoke,
                output_paths["recommended_smoke_runner_json"],
                output_paths["recommended_smoke_runner_markdown"],
            )
            comparison = compare_onboarding_recommendation_reports(
                output_paths["smoke_runner_json"],
                output_paths["recommended_smoke_runner_json"],
            )
            _write_recommendation_comparison_artifacts(
                comparison,
                output_paths["recommendation_comparison_json"],
                output_paths["recommendation_comparison_markdown"],
            )

    summary = _pipeline_summary(
        preflight=preflight,
        smoke=smoke,
        recommended_smoke=recommended_smoke,
        comparison=comparison,
        recommendation_rerun_enabled=run_recommendation_rerun,
    )
    single_repo_metadata = _pipeline_single_repo_metadata(manifest_payload)
    summary["single_repo_metadata"] = single_repo_metadata
    summary["single_repo_present"] = bool(
        single_repo_metadata.get("present", False)
    )
    summary["single_repo_repo"] = str(single_repo_metadata.get("repo") or "")
    summary["single_repo_ref"] = str(single_repo_metadata.get("ref") or "")
    summary["single_repo_run_name"] = str(
        single_repo_metadata.get("run_name") or ""
    )
    summary["single_repo_preset"] = str(
        single_repo_metadata.get("preset") or ""
    )
    summary["single_repo_include"] = _list(single_repo_metadata.get("include"))
    summary["single_repo_exclude"] = _list(single_repo_metadata.get("exclude"))
    summary["recommended_pipeline_manifest_present"] = (
        recommended_pipeline_manifest_written
    )
    summary["recommended_pipeline_manifest_path"] = (
        output_paths["recommended_pipeline_manifest"]
        if recommended_pipeline_manifest_written
        else ""
    )
    passed = _pipeline_passed(
        preflight=preflight,
        smoke=smoke,
        recommended_smoke=recommended_smoke,
        comparison=comparison,
    )
    pipeline_showcase = build_github_onboarding_pipeline_showcase(
        manifest_path=str(manifest),
        output_dir=str(output_root),
        suite_name=preflight.suite_name,
        passed=passed,
        summary=summary,
        output_paths=output_paths,
        preflight=preflight,
        smoke=smoke,
        comparison=comparison,
        promotion_config=promotion_config,
    )
    summary = _pipeline_summary_with_promotion_gate(summary, pipeline_showcase)
    repository_repair_manifest_written = False
    if _should_write_repository_repair_manifest(summary):
        repository_repair_manifest_written = _write_repository_repair_manifest(
            original_manifest_payload=manifest_payload,
            source_manifest_path=manifest,
            target_path=output_paths["repository_repair_manifest"],
            markdown_path=output_paths["repository_repair_manifest_markdown"],
            summary=summary,
        )
    if not repository_repair_manifest_written:
        Path(output_paths["repository_repair_manifest"]).unlink(missing_ok=True)
        Path(output_paths["repository_repair_manifest_markdown"]).unlink(
            missing_ok=True
        )
    repository_repair_manifest_metadata = (
        _repository_repair_manifest_metadata(
            output_paths["repository_repair_manifest"]
        )
        if repository_repair_manifest_written
        else {}
    )
    summary["repository_repair_manifest_present"] = (
        repository_repair_manifest_written
    )
    summary["repository_repair_manifest_path"] = (
        output_paths["repository_repair_manifest"]
        if repository_repair_manifest_written
        else ""
    )
    summary["repository_repair_manifest_markdown_path"] = (
        output_paths["repository_repair_manifest_markdown"]
        if repository_repair_manifest_written
        else ""
    )
    summary["repository_repair_manifest_applied_run_count"] = _int(
        repository_repair_manifest_metadata.get("applied_run_count", 0)
    )
    summary["repository_repair_manifest_changed_run_count"] = _int(
        repository_repair_manifest_metadata.get("changed_run_count", 0)
    )
    summary["repository_repair_manifest_applied_defaults"] = _list(
        repository_repair_manifest_metadata.get("applied_default_fields")
    )
    summary["repository_repair_manifest_promotion_gate_defaults"] = _list(
        repository_repair_manifest_metadata.get("promotion_gate_defaults")
    )
    summary["repository_repair_manifest_setup_repair_defaults_applied"] = bool(
        repository_repair_manifest_metadata.get("setup_repair_defaults_applied", False)
    )
    summary["repository_repair_manifest_setup_install_failure_counts"] = _dict(
        repository_repair_manifest_metadata.get("setup_install_failure_counts")
    )
    summary["repository_repair_manifest_top_setup_install_failure"] = str(
        repository_repair_manifest_metadata.get("top_setup_install_failure") or ""
    )
    summary["repository_repair_manifest_setup_doctor_blocker_counts"] = _dict(
        repository_repair_manifest_metadata.get("setup_doctor_blocker_counts")
    )
    summary["repository_repair_manifest_top_setup_doctor_blocker"] = str(
        repository_repair_manifest_metadata.get("top_setup_doctor_blocker") or ""
    )
    summary["repository_repair_manifest_setup_doctor_next_action"] = str(
        repository_repair_manifest_metadata.get("setup_doctor_next_action") or ""
    )
    run_repair_contexts = _dict(
        repository_repair_manifest_metadata.get("run_repair_contexts")
    )
    summary["repository_repair_manifest_run_repair_contexts"] = run_repair_contexts
    summary["repository_repair_manifest_run_context_count"] = len(
        run_repair_contexts
    )
    summary["repository_repair_manifest_setup_repair_run_names"] = [
        str(name)
        for name in _list(
            repository_repair_manifest_metadata.get("setup_repair_run_names")
        )
    ]
    summary["repository_repair_manifest_checkout_only_run_names"] = [
        str(name)
        for name in _list(
            repository_repair_manifest_metadata.get("checkout_only_run_names")
        )
    ]
    summary["repository_repair_manifest_setup_repair_next_action"] = str(
        repository_repair_manifest_metadata.get("setup_repair_next_action") or ""
    )
    summary["repository_repair_manifest_command"] = (
        _repository_repair_manifest_command(
            output_paths["repository_repair_manifest"],
            output_root,
        )
        if repository_repair_manifest_written
        else ""
    )
    repository_processing_diagnosis = _pipeline_repository_processing_diagnosis(
        summary=summary,
        stage_audit=_list(pipeline_showcase.get("stage_audit")),
    )
    summary["repository_processing_diagnosis"] = repository_processing_diagnosis
    summary["repository_processing_status"] = str(
        repository_processing_diagnosis.get("status") or ""
    )
    summary["repository_processing_primary_layer"] = str(
        repository_processing_diagnosis.get("primary_layer") or ""
    )
    summary["repository_processing_primary_blocker"] = str(
        repository_processing_diagnosis.get("primary_blocker") or ""
    )
    summary["repository_processing_next_action"] = str(
        repository_processing_diagnosis.get("next_action") or ""
    )
    summary["repository_processing_command"] = str(
        repository_processing_diagnosis.get("command") or ""
    )
    repository_processing_matrix = _pipeline_repository_processing_matrix(summary)
    summary["repository_processing_matrix"] = repository_processing_matrix
    summary["repository_processing_run_count"] = _int(
        repository_processing_matrix.get("run_count", 0)
    )
    summary["repository_processing_status_counts"] = _dict(
        repository_processing_matrix.get("status_counts")
    )
    summary["repository_processing_primary_layer_counts"] = _dict(
        repository_processing_matrix.get("primary_layer_counts")
    )
    summary["repository_processing_primary_blocker_counts"] = _dict(
        repository_processing_matrix.get("primary_blocker_counts")
    )
    summary["repository_processing_primary_blocker_runs"] = _dict(
        repository_processing_matrix.get("primary_blocker_runs")
    )
    summary["repository_processing_repair_summary_status_counts"] = _dict(
        repository_processing_matrix.get("repair_summary_status_counts")
    )
    summary["repository_processing_repair_summary_conclusion_counts"] = _dict(
        repository_processing_matrix.get("repair_summary_conclusion_counts")
    )
    repository_processing_expectation_report = (
        _pipeline_repository_processing_expectation_report(
            repository_processing_matrix,
            _pipeline_repository_processing_expectations(manifest_payload),
        )
    )
    summary["repository_processing_expectation_report"] = (
        repository_processing_expectation_report
    )
    summary["repository_processing_expectation_passed"] = bool(
        repository_processing_expectation_report.get("passed", True)
    )
    summary["repository_processing_expectation_check_count"] = _int(
        repository_processing_expectation_report.get("check_count", 0)
    )
    summary["repository_processing_expectation_failed_count"] = _int(
        repository_processing_expectation_report.get("failed_count", 0)
    )
    failed_expectation_checks = _repository_processing_failed_expectation_checks(
        repository_processing_expectation_report
    )
    summary["repository_processing_expectation_failed_checks"] = (
        failed_expectation_checks
    )
    summary["repository_processing_expectation_failed_check_preview"] = (
        failed_expectation_checks[:5]
    )
    summary["repository_processing_expectation_first_failed_check"] = (
        failed_expectation_checks[0] if failed_expectation_checks else {}
    )
    repository_processing_acceptance = (
        _pipeline_repository_processing_acceptance_report(summary)
    )
    summary["repository_processing_acceptance"] = repository_processing_acceptance
    summary["repository_processing_acceptance_passed"] = bool(
        repository_processing_acceptance.get("passed", False)
    )
    summary["repository_processing_acceptance_mode"] = str(
        repository_processing_acceptance.get("mode") or ""
    )
    summary["repository_processing_acceptance_reason"] = str(
        repository_processing_acceptance.get("reason") or ""
    )
    pipeline_showcase["repository_processing_diagnosis"] = (
        repository_processing_diagnosis
    )
    pipeline_showcase["repository_processing_matrix"] = repository_processing_matrix
    pipeline_showcase["repository_processing_expectation_report"] = (
        repository_processing_expectation_report
    )
    pipeline_showcase["repository_processing_acceptance"] = (
        repository_processing_acceptance
    )
    report = GitHubOnboardingPipelineReport(
        manifest_path=str(manifest),
        output_dir=str(output_root),
        suite_name=preflight.suite_name,
        passed=passed,
        summary=summary,
        output_paths=output_paths,
        preflight_batch=preflight.to_dict(),
        smoke_runner=smoke.to_dict() if smoke is not None else None,
        recommended_smoke_runner=(
            recommended_smoke.to_dict() if recommended_smoke is not None else None
        ),
        recommendation_comparison=(
            comparison.to_dict() if comparison is not None else None
        ),
        pipeline_showcase=pipeline_showcase,
    )
    _write_pipeline_artifacts(report)
    return report


def run_github_onboarding_pipeline_for_repo(
    repo_spec: str,
    output_dir: str | Path,
    *,
    ref: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    preset: str = "smoke",
    sample_sources: int | None = None,
    max_candidates: int | None = None,
    auto_fallback: bool | None = None,
    fallback_min_generated_candidates: int | None = None,
    fallback_max_sources: int | None = None,
    fallback_max_candidates: int | None = None,
    fallback_preset: str | None = None,
    fallback_recipe: list[str] | None = None,
    auto_remediate_benchmark: bool = False,
    source_cache_dir: str | Path | None = None,
    auto_scoped_include: bool | None = None,
    repository_test_root: str | Path | None = None,
    repository_test_timeout: int | None = None,
    repository_test_failure_overlay_candidate_limit: int | None = None,
    repository_test_reflection_mode: str | None = None,
    repository_test_reflection_rounds: int | None = None,
    repository_test_reflection_width: int | None = None,
    run_repository_test_environment_setup: bool = False,
    run_repository_test_retry: bool = False,
    run_repository_test_retry_prerequisites: bool = False,
    auto_repository_test_retry: bool = False,
    auto_repository_test_retry_max_risk: str | None = None,
    auto_repository_test_retry_allowed_runners: list[str] | None = None,
    repository_test_environment_setup_timeout: int | None = None,
    checkout_repository_tests: bool = False,
    repository_checkout_timeout: int | None = None,
    repository_checkout_depth: int | None = None,
    no_repository_test_command: bool = False,
    suite_name: str | None = None,
    run_name: str | None = None,
    expected_repository_processing: dict[str, Any] | None = None,
    opener=None,
    run_recommendation_rerun: bool = True,
    promotion_config_overrides: dict[str, Any] | None = None,
    promotion_gate: dict[str, Any] | None = None,
) -> GitHubOnboardingPipelineReport:
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "single_repo_pipeline_manifest.json"
    manifest = build_single_repo_pipeline_manifest(
        repo_spec,
        ref=ref,
        include=include,
        exclude=exclude,
        preset=preset,
        sample_sources=sample_sources,
        max_candidates=max_candidates,
        auto_fallback=auto_fallback,
        fallback_min_generated_candidates=fallback_min_generated_candidates,
        fallback_max_sources=fallback_max_sources,
        fallback_max_candidates=fallback_max_candidates,
        fallback_preset=fallback_preset,
        fallback_recipe=fallback_recipe,
        auto_remediate_benchmark=auto_remediate_benchmark,
        source_cache_dir=source_cache_dir,
        auto_scoped_include=auto_scoped_include,
        repository_test_root=repository_test_root,
        repository_test_timeout=repository_test_timeout,
        repository_test_failure_overlay_candidate_limit=(
            repository_test_failure_overlay_candidate_limit
        ),
        repository_test_reflection_mode=repository_test_reflection_mode,
        repository_test_reflection_rounds=repository_test_reflection_rounds,
        repository_test_reflection_width=repository_test_reflection_width,
        run_repository_test_environment_setup=run_repository_test_environment_setup,
        run_repository_test_retry=run_repository_test_retry,
        run_repository_test_retry_prerequisites=(
            run_repository_test_retry_prerequisites
        ),
        auto_repository_test_retry=auto_repository_test_retry,
        auto_repository_test_retry_max_risk=auto_repository_test_retry_max_risk,
        auto_repository_test_retry_allowed_runners=(
            auto_repository_test_retry_allowed_runners
        ),
        repository_test_environment_setup_timeout=(
            repository_test_environment_setup_timeout
        ),
        checkout_repository_tests=checkout_repository_tests,
        repository_checkout_timeout=repository_checkout_timeout,
        repository_checkout_depth=repository_checkout_depth,
        no_repository_test_command=no_repository_test_command,
        suite_name=suite_name,
        run_name=run_name,
        expected_repository_processing=expected_repository_processing,
        promotion_gate=promotion_gate,
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return run_github_onboarding_pipeline(
        manifest_path,
        output_root,
        opener=opener,
        run_recommendation_rerun=run_recommendation_rerun,
        promotion_config_overrides=promotion_config_overrides,
    )


def build_single_repo_pipeline_manifest(
    repo_spec: str,
    *,
    ref: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    preset: str = "smoke",
    sample_sources: int | None = None,
    max_candidates: int | None = None,
    auto_fallback: bool | None = None,
    fallback_min_generated_candidates: int | None = None,
    fallback_max_sources: int | None = None,
    fallback_max_candidates: int | None = None,
    fallback_preset: str | None = None,
    fallback_recipe: list[str] | None = None,
    auto_remediate_benchmark: bool = False,
    source_cache_dir: str | Path | None = None,
    auto_scoped_include: bool | None = None,
    repository_test_root: str | Path | None = None,
    repository_test_timeout: int | None = None,
    repository_test_failure_overlay_candidate_limit: int | None = None,
    repository_test_reflection_mode: str | None = None,
    repository_test_reflection_rounds: int | None = None,
    repository_test_reflection_width: int | None = None,
    run_repository_test_environment_setup: bool = False,
    run_repository_test_retry: bool = False,
    run_repository_test_retry_prerequisites: bool = False,
    auto_repository_test_retry: bool = False,
    auto_repository_test_retry_max_risk: str | None = None,
    auto_repository_test_retry_allowed_runners: list[str] | None = None,
    repository_test_environment_setup_timeout: int | None = None,
    checkout_repository_tests: bool = False,
    repository_checkout_timeout: int | None = None,
    repository_checkout_depth: int | None = None,
    no_repository_test_command: bool = False,
    suite_name: str | None = None,
    run_name: str | None = None,
    expected_repository_processing: dict[str, Any] | None = None,
    promotion_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    name = run_name or _repo_run_name(repo_spec)
    run: dict[str, Any] = {
        "name": name,
        "mode": "repo",
        "repo": repo_spec,
        "preset": preset,
    }
    if ref:
        run["ref"] = ref
    if include:
        run["include"] = list(include)
    if exclude:
        run["exclude"] = list(exclude)
    if sample_sources is not None:
        run["sample_sources"] = sample_sources
    if max_candidates is not None:
        run["max_candidates"] = max_candidates
    if auto_fallback is not None:
        run["auto_fallback"] = auto_fallback
    if auto_remediate_benchmark:
        run["auto_remediate_benchmark"] = True
    if fallback_min_generated_candidates is not None:
        run["thresholds"] = {
            "min_generated_candidates": fallback_min_generated_candidates
        }
    fallback: dict[str, Any] = {}
    if fallback_max_sources is not None:
        fallback["max_sources"] = fallback_max_sources
    if fallback_max_candidates is not None:
        fallback["max_candidates"] = fallback_max_candidates
    if fallback_preset is not None:
        fallback["preset"] = fallback_preset
    if fallback_recipe:
        fallback["recipe"] = list(fallback_recipe)
    if fallback:
        fallback["enabled"] = True
        run["fallback"] = fallback
    if source_cache_dir is not None:
        run["source_cache_dir"] = str(source_cache_dir)
    if auto_scoped_include is not None:
        run["auto_scoped_include"] = auto_scoped_include
    if repository_test_root is not None:
        run["repository_test_root"] = str(repository_test_root)
    if repository_test_timeout is not None:
        run["repository_test_timeout"] = repository_test_timeout
    if repository_test_failure_overlay_candidate_limit is not None:
        run["repository_test_failure_overlay_candidate_limit"] = (
            repository_test_failure_overlay_candidate_limit
        )
    if repository_test_reflection_mode is not None:
        run["repository_test_reflection_mode"] = repository_test_reflection_mode
    if repository_test_reflection_rounds is not None:
        run["repository_test_reflection_rounds"] = repository_test_reflection_rounds
    if repository_test_reflection_width is not None:
        run["repository_test_reflection_width"] = repository_test_reflection_width
    if run_repository_test_environment_setup:
        run["run_repository_test_environment_setup"] = True
    if run_repository_test_retry:
        run["run_repository_test_retry"] = True
    if run_repository_test_retry_prerequisites:
        run["run_repository_test_retry_prerequisites"] = True
    if auto_repository_test_retry:
        run["auto_repository_test_retry"] = True
    if auto_repository_test_retry_max_risk is not None:
        run["auto_repository_test_retry_max_risk"] = auto_repository_test_retry_max_risk
    if auto_repository_test_retry_allowed_runners:
        run["auto_repository_test_retry_allowed_runners"] = [
            str(item) for item in auto_repository_test_retry_allowed_runners
        ]
    if repository_test_environment_setup_timeout is not None:
        run["repository_test_environment_setup_timeout"] = (
            repository_test_environment_setup_timeout
        )
    if checkout_repository_tests:
        run["checkout_repository_tests"] = True
    if repository_checkout_timeout is not None:
        run["repository_checkout_timeout"] = repository_checkout_timeout
    if repository_checkout_depth is not None:
        run["repository_checkout_depth"] = repository_checkout_depth
    if no_repository_test_command:
        run["no_repository_test_command"] = True
    expected_processing = _dict(expected_repository_processing)
    if expected_processing:
        run["expected_repository_processing"] = copy.deepcopy(expected_processing)
    suite = suite_name or f"{name}_pipeline"
    provided_promotion_gate = _provided_config(promotion_gate)
    manifest = {
        "suite_name": suite,
        "description": "Generated by github_onboarding_pipeline single repo mode.",
        "single_repo_metadata": {
            "kind": "single_repo_pipeline",
            "repo": repo_spec,
            "ref": ref or "",
            "run_name": name,
            "suite_name": suite,
            "preset": preset,
            "include": list(include or []),
            "exclude": list(exclude or []),
            "expected_repository_processing_present": bool(expected_processing),
            "promotion_gate_present": bool(provided_promotion_gate),
        },
        "runs": [run],
    }
    processing_expectations = _single_repo_processing_expectations(
        run_name=name,
        expected_repository_processing=expected_processing,
    )
    if processing_expectations:
        manifest["repository_processing_matrix_expectations"] = (
            processing_expectations
        )
    if provided_promotion_gate:
        manifest["promotion_gate"] = provided_promotion_gate
    return manifest


def _single_repo_processing_expectations(
    *,
    run_name: str,
    expected_repository_processing: dict[str, Any],
) -> dict[str, Any]:
    if not expected_repository_processing:
        return {}
    expectations: dict[str, Any] = {
        "min_run_count": 1,
        "run_expectations": {
            run_name: copy.deepcopy(expected_repository_processing),
        },
    }
    target_status = str(
        expected_repository_processing.get("target_status")
        or expected_repository_processing.get("status")
        or ""
    )
    target_layer = str(
        expected_repository_processing.get("target_primary_layer")
        or expected_repository_processing.get("primary_layer")
        or ""
    )
    target_repair_status = str(
        expected_repository_processing.get("target_repair_summary_status")
        or expected_repository_processing.get("repair_summary_status")
        or ""
    )
    target_repair_conclusion = str(
        expected_repository_processing.get("target_repair_summary_conclusion")
        or expected_repository_processing.get("repair_summary_conclusion")
        or ""
    )
    if target_status:
        expectations["min_status_counts"] = {target_status: 1}
    if target_layer:
        expectations["min_primary_layer_counts"] = {target_layer: 1}
    if target_repair_status:
        expectations["min_repair_summary_status_counts"] = {
            target_repair_status: 1
        }
    if target_repair_conclusion:
        expectations["min_repair_summary_conclusion_counts"] = {
            target_repair_conclusion: 1
        }
    return expectations


def _pipeline_single_repo_metadata(
    manifest_payload: dict[str, Any],
) -> dict[str, Any]:
    explicit = _dict(manifest_payload.get("single_repo_metadata"))
    if explicit:
        return _normalized_single_repo_metadata(
            explicit,
            manifest_payload=manifest_payload,
            source="single_repo_metadata",
        )
    defaults = _dict(manifest_payload.get("defaults"))
    runs = [_dict(run) for run in _list(manifest_payload.get("runs"))]
    if len(runs) != 1:
        return {"present": False}
    run = {**defaults, **runs[0]}
    mode = str(run.get("mode") or defaults.get("mode") or "")
    repo = str(run.get("repo") or run.get("repo_spec") or "")
    if mode != "repo" and not repo:
        return {"present": False}
    if not repo:
        return {"present": False}
    metadata = {
        "repo": repo,
        "ref": str(run.get("ref") or defaults.get("ref") or ""),
        "run_name": str(run.get("name") or _repo_run_name(repo)),
        "suite_name": str(manifest_payload.get("suite_name") or ""),
        "preset": str(run.get("preset") or defaults.get("preset") or ""),
        "include": _list(run.get("include")),
        "exclude": _list(run.get("exclude")),
        "expected_repository_processing_present": bool(
            _dict(run.get("expected_repository_processing"))
        ),
        "promotion_gate_present": bool(_dict(manifest_payload.get("promotion_gate"))),
    }
    return _normalized_single_repo_metadata(
        metadata,
        manifest_payload=manifest_payload,
        source="derived_single_repo_run",
    )


def _normalized_single_repo_metadata(
    metadata: dict[str, Any],
    *,
    manifest_payload: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    return {
        "present": True,
        "source": source,
        "kind": str(metadata.get("kind") or "single_repo_pipeline"),
        "repo": str(metadata.get("repo") or metadata.get("repo_spec") or ""),
        "ref": str(metadata.get("ref") or ""),
        "run_name": str(metadata.get("run_name") or ""),
        "suite_name": str(
            metadata.get("suite_name") or manifest_payload.get("suite_name") or ""
        ),
        "preset": str(metadata.get("preset") or ""),
        "include": [str(item) for item in _list(metadata.get("include"))],
        "exclude": [str(item) for item in _list(metadata.get("exclude"))],
        "expected_repository_processing_present": bool(
            metadata.get("expected_repository_processing_present", False)
        ),
        "processing_expectations_present": bool(
            _dict(manifest_payload.get("repository_processing_matrix_expectations"))
        ),
        "promotion_gate_present": bool(
            metadata.get("promotion_gate_present", False)
            or _dict(manifest_payload.get("promotion_gate"))
        ),
    }


def build_github_onboarding_pipeline_showcase(
    *,
    manifest_path: str,
    output_dir: str,
    suite_name: str,
    passed: bool,
    summary: dict[str, Any],
    output_paths: dict[str, str],
    preflight: GitHubOnboardingPreflightBatchReport,
    smoke: OnboardingSmokeRunnerReport | None,
    comparison: OnboardingRecommendationComparison | None,
    promotion_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_promotion_config = _normalize_promotion_config(
        _dict(promotion_config)
    )
    headline = {
        "suite_name": suite_name,
        "passed": passed,
        "preflight_readiness_rate": _float(
            summary.get("preflight_readiness_rate", 0.0)
        ),
        "preflight_ready_count": _int(summary.get("preflight_ready_count", 0)),
        "preflight_skipped_count": _int(summary.get("preflight_skipped_count", 0)),
        "preflight_top_issue_code": str(
            summary.get("preflight_top_issue_code") or ""
        ),
        "preflight_profile_doctor_status_counts": _dict(
            summary.get("preflight_profile_doctor_status_counts")
        ),
        "preflight_top_profile_doctor_blocker": str(
            summary.get("preflight_top_profile_doctor_blocker") or ""
        ),
        "smoke_generated_candidates": _int(
            summary.get("smoke_generated_candidates", 0)
        ),
        "smoke_benchmark_cases": _int(summary.get("smoke_benchmark_cases", 0)),
        "smoke_static_intelligence_run_count": _int(
            summary.get("smoke_static_intelligence_run_count", 0)
        ),
        "smoke_static_intelligence_analysis_ready_count": _int(
            summary.get("smoke_static_intelligence_analysis_ready_count", 0)
        ),
        "smoke_static_intelligence_selected_signal_count": _int(
            summary.get("smoke_static_intelligence_selected_signal_count", 0)
        ),
        "smoke_static_intelligence_total_signal_count": _int(
            summary.get("smoke_static_intelligence_total_signal_count", 0)
        ),
        "smoke_static_intelligence_status_counts": _dict(
            summary.get("smoke_static_intelligence_status_counts")
        ),
        "smoke_static_intelligence_rule_counts": _dict(
            summary.get("smoke_static_intelligence_rule_counts")
        ),
        "smoke_benchmark_patch_success_rate": _float(
            summary.get("smoke_benchmark_patch_success_rate", 0.0)
        ),
        "smoke_benchmark_top1": _float(summary.get("smoke_benchmark_top1", 0.0)),
        "smoke_benchmark_map": _float(summary.get("smoke_benchmark_map", 0.0)),
        "smoke_benchmarkization_ready_count": _int(
            summary.get("smoke_benchmarkization_ready_count", 0)
        ),
        "smoke_benchmarkization_status_counts": _dict(
            summary.get("smoke_benchmarkization_status_counts")
        ),
        "smoke_benchmarkization_primary_action_counts": _dict(
            summary.get("smoke_benchmarkization_primary_action_counts")
        ),
        "smoke_benchmarkization_remediation_plan_count": _int(
            summary.get("smoke_benchmarkization_remediation_plan_count", 0)
        ),
        "smoke_fallback_attempted_count": _int(
            summary.get("smoke_fallback_attempted_count", 0)
        ),
        "smoke_fallback_improved_count": _int(
            summary.get("smoke_fallback_improved_count", 0)
        ),
        "smoke_fallback_recovered_count": _int(
            summary.get("smoke_fallback_recovered_count", 0)
        ),
        "smoke_auto_remediation_attempted_count": _int(
            summary.get("smoke_auto_remediation_attempted_count", 0)
        ),
        "smoke_auto_remediation_used_count": _int(
            summary.get("smoke_auto_remediation_used_count", 0)
        ),
        "smoke_auto_remediation_improved_count": _int(
            summary.get("smoke_auto_remediation_improved_count", 0)
        ),
        "smoke_auto_remediation_benchmark_case_delta": _int(
            summary.get("smoke_auto_remediation_benchmark_case_delta", 0)
        ),
        "recommendation_status": str(summary.get("recommendation_status") or ""),
        "recommendation_candidate_delta": _int(
            summary.get("recommendation_candidate_delta", 0)
        ),
        "recommendation_benchmark_case_delta": _int(
            summary.get("recommendation_benchmark_case_delta", 0)
        ),
        "recommendation_validation_pass_rate_delta": _float(
            summary.get("recommendation_validation_pass_rate_delta", 0.0)
        ),
    }
    readiness = {
        "ready_run_names": _list(summary.get("preflight_ready_run_names")),
        "skipped_run_names": _list(summary.get("preflight_skipped_run_names")),
        "error_run_names": _list(summary.get("preflight_error_run_names")),
        "status_counts": _dict(preflight.summary.get("status_counts")),
        "issue_counts": _dict(preflight.summary.get("issue_counts")),
        "profile_doctor_status_counts": _dict(
            preflight.summary.get("profile_doctor_status_counts")
        ),
        "profile_doctor_blocker_counts": _dict(
            preflight.summary.get("profile_doctor_blocker_counts")
        ),
        "top_profile_doctor_blocker": str(
            preflight.summary.get("top_profile_doctor_blocker") or ""
        ),
    }
    smoke_summary = _dict(smoke.summary if smoke is not None else {})
    smoke_gap_summary = _dict(smoke.gap_summary if smoke is not None else {})
    smoke_gap_headline = _dict(smoke_gap_summary.get("headline"))
    smoke_evidence = {
        "present": smoke is not None,
        "passed": bool(smoke.passed) if smoke is not None else False,
        "run_count": _int(smoke_summary.get("run_count", 0)),
        "generated_candidates": _int(smoke_summary.get("generated_candidates", 0)),
        "benchmark_cases": _int(smoke_summary.get("benchmark_cases", 0)),
        "static_intelligence_run_count": _int(
            smoke_summary.get("static_intelligence_run_count", 0)
        ),
        "static_intelligence_analysis_ready_count": _int(
            smoke_summary.get("static_intelligence_analysis_ready_count", 0)
        ),
        "static_intelligence_source_inventory_ready_count": _int(
            smoke_summary.get(
                "static_intelligence_source_inventory_ready_count",
                0,
            )
        ),
        "static_intelligence_blocked_count": _int(
            smoke_summary.get("static_intelligence_blocked_count", 0)
        ),
        "static_intelligence_selected_signal_count": _int(
            smoke_summary.get("static_intelligence_selected_signal_count", 0)
        ),
        "static_intelligence_total_signal_count": _int(
            smoke_summary.get("static_intelligence_total_signal_count", 0)
        ),
        "static_intelligence_candidate_limit_applied_count": _int(
            smoke_summary.get(
                "static_intelligence_candidate_limit_applied_count",
                0,
            )
        ),
        "static_intelligence_average_quality_score": _float(
            smoke_summary.get("static_intelligence_average_quality_score", 0.0)
        ),
        "static_intelligence_status_counts": _dict(
            smoke_summary.get("static_intelligence_status_counts")
        ),
        "static_intelligence_level_counts": _dict(
            smoke_summary.get("static_intelligence_level_counts")
        ),
        "static_intelligence_reason_counts": _dict(
            smoke_summary.get("static_intelligence_reason_counts")
        ),
        "static_intelligence_rule_counts": _dict(
            smoke_summary.get("static_intelligence_rule_counts")
        ),
        "static_intelligence_bug_type_counts": _dict(
            smoke_summary.get("static_intelligence_bug_type_counts")
        ),
        "static_intelligence_dynamic_validation_level_counts": _dict(
            smoke_summary.get("static_intelligence_dynamic_validation_level_counts")
        ),
        "static_intelligence_primary_artifact_runs": _dict(
            smoke_summary.get("static_intelligence_primary_artifact_runs")
        ),
        "benchmark_patch_success_rate": _float(
            summary.get("smoke_benchmark_patch_success_rate", 0.0)
        ),
        "benchmark_top1": _float(summary.get("smoke_benchmark_top1", 0.0)),
        "benchmark_map": _float(summary.get("smoke_benchmark_map", 0.0)),
        "benchmarkization_ready_count": _int(
            smoke_summary.get("benchmarkization_ready_count", 0)
        ),
        "benchmarkization_status_counts": _dict(
            smoke_summary.get("benchmarkization_status_counts")
        ),
        "benchmarkization_stage_counts": _dict(
            smoke_summary.get("benchmarkization_stage_counts")
        ),
        "benchmarkization_primary_action_counts": _dict(
            smoke_summary.get("benchmarkization_primary_action_counts")
        ),
        "benchmarkization_remediation_plan_count": _int(
            smoke_summary.get("benchmarkization_remediation_plan_count", 0)
        ),
        "benchmarkization_remediation_plan_runs": _dict(
            smoke_summary.get("benchmarkization_remediation_plan_runs")
        ),
        "gap_status": str(smoke_summary.get("gap_status") or ""),
        "fallback_attempted_count": _int(
            smoke_summary.get("fallback_attempted_count", 0)
        ),
        "fallback_improved_count": _int(
            smoke_summary.get("fallback_improved_count", 0)
        ),
        "fallback_recovered_count": _int(
            smoke_summary.get("fallback_recovered_count", 0)
        ),
        "auto_remediation_attempted_count": _int(
            smoke_summary.get("auto_remediation_attempted_count", 0)
        ),
        "auto_remediation_used_count": _int(
            smoke_summary.get("auto_remediation_used_count", 0)
        ),
        "auto_remediation_improved_count": _int(
            smoke_summary.get("auto_remediation_improved_count", 0)
        ),
        "auto_remediation_benchmark_case_delta": _int(
            smoke_summary.get("auto_remediation_benchmark_case_delta", 0)
        ),
        "auto_remediation_action_counts": _dict(
            smoke_summary.get("auto_remediation_action_counts")
        ),
        "auto_remediation_action_runs": _dict(
            smoke_summary.get("auto_remediation_action_runs")
        ),
        "manifest_recommendation_count": _int(
            smoke_summary.get("manifest_recommendation_count", 0)
        ),
        "gap_top_diagnostic_issue": str(
            smoke_gap_headline.get("top_diagnostic_issue") or ""
        ),
        "gap_top_validation_failed_check": str(
            smoke_gap_headline.get("top_validation_failed_check") or ""
        ),
        "gap_next_actions": [
            str(action)
            for action in _list(smoke_gap_summary.get("next_actions"))
            if str(action).strip()
        ],
    }
    repository_test_readiness = {
        "report_count": _int(summary.get("repository_test_report_count", 0)),
        "analysis_source_counts": _dict(
            summary.get("repository_test_analysis_source_counts")
        ),
        "analysis_source_runs": _dict(
            summary.get("repository_test_analysis_source_runs")
        ),
        "overlay_trigger_reason_counts": _dict(
            summary.get("repository_test_overlay_trigger_reason_counts")
        ),
        "overlay_trigger_reason_runs": _dict(
            summary.get("repository_test_overlay_trigger_reason_runs")
        ),
        "phase2_ready_count": _int(
            summary.get("repository_test_phase2_ready_count", 0)
        ),
        "phase2_ready_runs": _list(
            summary.get("repository_test_phase2_ready_runs")
        ),
        "phase3_validation_ready_count": _int(
            summary.get("repository_test_phase3_validation_ready_count", 0)
        ),
        "phase3_validation_ready_runs": _list(
            summary.get("repository_test_phase3_validation_ready_runs")
        ),
        "execution_status_counts": _dict(
            summary.get("repository_test_execution_status_counts")
        ),
        "execution_status_runs": _dict(
            summary.get("repository_test_execution_status_runs")
        ),
        "execution_command_runs": _dict(
            summary.get("repository_test_execution_command_runs")
        ),
        "failure_category_counts": _dict(
            summary.get("repository_test_failure_category_counts")
        ),
        "failure_category_runs": _dict(
            summary.get("repository_test_failure_category_runs")
        ),
        "setup_install_failure_counts": _dict(
            summary.get("repository_test_environment_setup_install_failure_counts")
        ),
        "setup_install_failure_runs": _dict(
            summary.get("repository_test_environment_setup_install_failure_runs")
        ),
        "setup_install_fallback_count": _int(
            summary.get(
                "repository_test_environment_setup_install_fallback_count",
                0,
            )
        ),
        "setup_install_fallback_success_count": _int(
            summary.get(
                "repository_test_environment_setup_install_fallback_success_count",
                0,
            )
        ),
        "fault_localization_status_counts": _dict(
            summary.get("repository_test_fault_localization_status_counts")
        ),
        "fault_localization_status_runs": _dict(
            summary.get("repository_test_fault_localization_status_runs")
        ),
        "fault_localization_top_function_counts": _dict(
            summary.get("repository_test_fault_localization_top_function_counts")
        ),
        "fault_localization_top_function_runs": _dict(
            summary.get("repository_test_fault_localization_top_function_runs")
        ),
        "patch_validation_status_counts": _dict(
            summary.get("repository_test_patch_validation_status_counts")
        ),
        "patch_validation_status_runs": _dict(
            summary.get("repository_test_patch_validation_status_runs")
        ),
        "patch_validation_success_run_count": _int(
            summary.get("repository_test_patch_validation_success_run_count", 0)
        ),
        "patch_validation_success_count": _int(
            summary.get("repository_test_patch_validation_success_count", 0)
        ),
        "patch_validation_success_runs": _list(
            summary.get("repository_test_patch_validation_success_runs")
        ),
        "final_status_counts": _dict(
            summary.get("repository_test_final_status_counts")
        ),
        "final_status_runs": _dict(
            summary.get("repository_test_final_status_runs")
        ),
        "blocked_reason_counts": _dict(
            summary.get("repository_test_blocked_reason_counts")
        ),
        "blocked_reason_runs": _dict(
            summary.get("repository_test_blocked_reason_runs")
        ),
        "run_summaries": _list(summary.get("repository_test_run_summaries")),
    }
    comparison_summary = _dict(comparison.summary if comparison is not None else {})
    recommendation = {
        "enabled": bool(summary.get("recommendation_rerun_enabled", False)),
        "applied_count": _int(summary.get("recommendation_applied_count", 0)),
        "pipeline_manifest_present": bool(
            summary.get("recommended_pipeline_manifest_present", False)
        ),
        "pipeline_manifest_path": str(
            summary.get("recommended_pipeline_manifest_path") or ""
        ),
        "status": str(summary.get("recommendation_status") or ""),
        "rerun_present": bool(summary.get("recommendation_rerun_present", False)),
        "rerun_passed": bool(summary.get("recommendation_rerun_passed", False)),
        "comparison_passed": bool(
            summary.get("recommendation_comparison_passed", False)
        ),
        "candidate_delta": _int(comparison_summary.get("candidate_delta", 0)),
        "benchmark_case_delta": _int(
            comparison_summary.get("benchmark_case_delta", 0)
        ),
        "fallback_recovered_delta": _int(
            comparison_summary.get("recommended_fallback_recovered_count", 0)
        )
        - _int(comparison_summary.get("baseline_fallback_recovered_count", 0)),
        "fallback_recovery_resolved_count": max(
            0,
            _int(comparison_summary.get("baseline_fallback_recovered_count", 0))
            - _int(comparison_summary.get("recommended_fallback_recovered_count", 0)),
        ),
        "validation_pass_rate_delta": _float(
            comparison_summary.get("validation_pass_rate_delta", 0.0)
        ),
        "regressions": list(comparison.regressions) if comparison is not None else [],
    }
    stage_audit = _pipeline_stage_audit(
        headline=headline,
        readiness=readiness,
        smoke_evidence=smoke_evidence,
        repository_test_readiness=repository_test_readiness,
        recommendation=recommendation,
    )
    stage_summary = _pipeline_stage_summary(stage_audit)
    repository_processing_matrix = _pipeline_repository_processing_matrix(summary)
    promotion_gate = _pipeline_promotion_gate(
        headline=headline,
        smoke_evidence=smoke_evidence,
        repository_test_readiness=repository_test_readiness,
        recommendation=recommendation,
        stage_audit=stage_audit,
        stage_summary=stage_summary,
        promotion_config=normalized_promotion_config,
    )
    return {
        "artifact_kind": "github_onboarding_pipeline_showcase",
        "manifest_path": manifest_path,
        "output_dir": output_dir,
        "headline": headline,
        "readiness": readiness,
        "smoke_evidence": smoke_evidence,
        "repository_test_readiness": repository_test_readiness,
        "recommendation": recommendation,
        "stage_audit": stage_audit,
        "stage_summary": stage_summary,
        "promotion_gate": promotion_gate,
        "promotion_config": normalized_promotion_config,
        "repository_processing_matrix": repository_processing_matrix,
        "next_actions": _pipeline_next_actions(stage_audit),
        "resume_bullets": _pipeline_resume_bullets(headline, readiness),
        "artifacts": dict(output_paths),
    }


def render_github_onboarding_pipeline_showcase_markdown(
    showcase: dict[str, Any],
) -> str:
    headline = _dict(showcase.get("headline"))
    readiness = _dict(showcase.get("readiness"))
    smoke = _dict(showcase.get("smoke_evidence"))
    repository_test = _dict(showcase.get("repository_test_readiness"))
    recommendation = _dict(showcase.get("recommendation"))
    stage_summary = _dict(showcase.get("stage_summary"))
    promotion_gate = _dict(showcase.get("promotion_gate"))
    repository_processing = _dict(showcase.get("repository_processing_diagnosis"))
    repository_processing_matrix = _dict(
        showcase.get("repository_processing_matrix")
    )
    repository_processing_expectations = _dict(
        showcase.get("repository_processing_expectation_report")
    )
    repository_processing_acceptance = _dict(
        showcase.get("repository_processing_acceptance")
    )
    lines = [
        "# GitHub Onboarding Pipeline Showcase",
        "",
        "## Summary",
        "",
        f"- Suite: `{_markdown_cell(headline.get('suite_name', ''))}`",
        f"- Result: {'PASS' if headline.get('passed') else 'FAIL'}",
        (
            "- Repository Processing Acceptance: "
            f"{'PASS' if repository_processing_acceptance.get('passed') else 'FAIL'} "
            f"({_markdown_cell(repository_processing_acceptance.get('mode', ''))}:"
            f"{_markdown_cell(repository_processing_acceptance.get('reason', ''))})"
        ),
        (
            "- Preflight Readiness: "
            f"{_float(headline.get('preflight_readiness_rate', 0.0)):.4f} "
            f"({_int(headline.get('preflight_ready_count', 0))} ready / "
            f"{_int(headline.get('preflight_skipped_count', 0))} skipped)"
        ),
        f"- Top Issue: `{_markdown_cell(headline.get('preflight_top_issue_code', ''))}`",
        (
            "- Smoke Evidence: "
            f"{_int(headline.get('smoke_generated_candidates', 0))} candidates / "
            f"{_int(headline.get('smoke_benchmark_cases', 0))} benchmark cases"
        ),
        (
            "- Static Intelligence: "
            f"{_int(headline.get('smoke_static_intelligence_analysis_ready_count', 0))}/"
            f"{_int(headline.get('smoke_static_intelligence_run_count', 0))} analysis-ready; "
            f"signals {_int(headline.get('smoke_static_intelligence_selected_signal_count', 0))}/"
            f"{_int(headline.get('smoke_static_intelligence_total_signal_count', 0))}; "
            f"statuses {_markdown_cell(_format_counts(_dict(headline.get('smoke_static_intelligence_status_counts'))))}; "
            f"rules {_markdown_cell(_format_counts(_dict(headline.get('smoke_static_intelligence_rule_counts'))))}"
        ),
        (
            "- Fallback Recovery: "
            f"{_int(headline.get('smoke_fallback_recovered_count', 0))} recovered / "
            f"{_int(headline.get('smoke_fallback_improved_count', 0))} improved / "
            f"{_int(headline.get('smoke_fallback_attempted_count', 0))} attempted"
        ),
        (
            "- Benchmark Auto Remediation: "
            f"{_int(headline.get('smoke_auto_remediation_used_count', 0))} used / "
            f"{_int(headline.get('smoke_auto_remediation_improved_count', 0))} improved / "
            f"{_int(headline.get('smoke_auto_remediation_attempted_count', 0))} attempted; "
            f"cases {_signed_int(headline.get('smoke_auto_remediation_benchmark_case_delta', 0))}"
        ),
        (
            "- Benchmarkization: "
            f"{_int(headline.get('smoke_benchmarkization_ready_count', 0))} ready; "
            f"statuses {_markdown_cell(_format_counts(_dict(headline.get('smoke_benchmarkization_status_counts'))))}; "
            f"actions {_markdown_cell(_format_counts(_dict(headline.get('smoke_benchmarkization_primary_action_counts'))))}; "
            f"plans {_int(headline.get('smoke_benchmarkization_remediation_plan_count', 0))}"
        ),
        (
            "- Recommendation: "
            f"{_markdown_cell(headline.get('recommendation_status', ''))}; "
            f"candidates {_signed_int(headline.get('recommendation_candidate_delta', 0))}, "
            f"cases {_signed_int(headline.get('recommendation_benchmark_case_delta', 0))}, "
            f"fallback resolved {_int(recommendation.get('fallback_recovery_resolved_count', 0))}, "
            f"validation {_signed_float(headline.get('recommendation_validation_pass_rate_delta', 0.0))}"
        ),
        "",
        "## Resume Bullets",
        "",
    ]
    for bullet in _list(showcase.get("resume_bullets")):
        lines.append(f"- {_markdown_cell(bullet)}")
    if not _list(showcase.get("resume_bullets")):
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Repository Processing Diagnosis",
            "",
            "| Signal | Value |",
            "| --- | --- |",
            f"| Status | {_markdown_cell(repository_processing.get('status', ''))} |",
            f"| Primary Layer | {_markdown_cell(repository_processing.get('primary_layer', ''))} |",
            f"| Primary Blocker | {_markdown_cell(repository_processing.get('primary_blocker', ''))} |",
            f"| Evidence | {_markdown_cell(repository_processing.get('evidence', ''))} |",
            f"| Next Action | {_markdown_cell(repository_processing.get('next_action', ''))} |",
            f"| Command | `{_markdown_cell(repository_processing.get('command', ''))}` |",
            "",
            "| Layer | Status | Blocker | Evidence | Next Action |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for layer_value in _list(repository_processing.get("layers")):
        layer = _dict(layer_value)
        lines.append(
            "| "
            f"{_markdown_cell(layer.get('layer', ''))} | "
            f"{_markdown_cell(layer.get('status', ''))} | "
            f"{_markdown_cell(layer.get('blocker', ''))} | "
            f"{_markdown_cell(layer.get('evidence', ''))} | "
            f"{_markdown_cell(layer.get('next_action', ''))} |"
        )
    if not _list(repository_processing.get("layers")):
        lines.append("| none | skip | none |  |  |")
    lines.extend(
        [
            "",
            "## Repository Processing Matrix",
            "",
            "| Signal | Value |",
            "| --- | --- |",
            f"| Run Count | {_int(repository_processing_matrix.get('run_count', 0))} |",
            f"| Status Counts | {_markdown_cell(_format_counts(_dict(repository_processing_matrix.get('status_counts'))))} |",
            f"| Primary Layer Counts | {_markdown_cell(_format_counts(_dict(repository_processing_matrix.get('primary_layer_counts'))))} |",
            f"| Primary Blocker Counts | {_markdown_cell(_format_counts(_dict(repository_processing_matrix.get('primary_blocker_counts'))))} |",
            f"| Repair Summary Status Counts | {_markdown_cell(_format_counts(_dict(repository_processing_matrix.get('repair_summary_status_counts'))))} |",
            f"| Repair Summary Conclusion Counts | {_markdown_cell(_format_counts(_dict(repository_processing_matrix.get('repair_summary_conclusion_counts'))))} |",
            "",
            "| Run | Status | Primary Layer | Primary Blocker | Repair Summary | Evidence | Next Action |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item_value in _list(repository_processing_matrix.get("run_diagnoses")):
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('name', ''))} | "
            f"{_markdown_cell(item.get('status', ''))} | "
            f"{_markdown_cell(item.get('primary_layer', ''))} | "
            f"{_markdown_cell(item.get('primary_blocker', ''))} | "
            f"{_markdown_cell(item.get('repair_summary', 'none'))} | "
            f"{_markdown_cell(item.get('evidence', ''))} | "
            f"{_markdown_cell(item.get('next_action', ''))} |"
        )
    if not _list(repository_processing_matrix.get("run_diagnoses")):
        lines.append("| none | skip | none | none | none |  |  |")
    lines.extend(
        [
            "",
            "## Repository Processing Expectations",
            "",
            "| Signal | Value |",
            "| --- | --- |",
            f"| Acceptance | {str(bool(repository_processing_acceptance.get('passed', False))).lower()} |",
            f"| Acceptance Mode | {_markdown_cell(repository_processing_acceptance.get('mode', ''))} |",
            f"| Acceptance Reason | {_markdown_cell(repository_processing_acceptance.get('reason', ''))} |",
            f"| Present | {str(bool(repository_processing_expectations.get('present', False))).lower()} |",
            f"| Passed | {str(bool(repository_processing_expectations.get('passed', True))).lower()} |",
            f"| Checks | {_int(repository_processing_expectations.get('check_count', 0))} |",
            f"| Failed Checks | {_int(repository_processing_expectations.get('failed_count', 0))} |",
            "",
            "| Check | Passed | Expected | Actual |",
            "| --- | --- | --- | --- |",
        ]
    )
    for check_value in _list(repository_processing_expectations.get("checks")):
        check = _dict(check_value)
        lines.append(
            "| "
            f"{_markdown_cell(check.get('name', ''))} | "
            f"{str(bool(check.get('passed', False))).lower()} | "
            f"{_markdown_cell(check.get('expected', ''))} | "
            f"{_markdown_cell(check.get('actual', ''))} |"
        )
    if not _list(repository_processing_expectations.get("checks")):
        lines.append("| none | true | none | none |")
    lines.extend(
        [
            "",
            "## Promotion Gate",
            "",
            "| Signal | Value |",
            "| --- | --- |",
            f"| Status | {_markdown_cell(promotion_gate.get('status', ''))} |",
            f"| Promotable | {str(bool(promotion_gate.get('promotable', False))).lower()} |",
            f"| Blocking Reasons | {_markdown_cell(_format_name_list(promotion_gate.get('blocking_reasons')))} |",
            f"| Warning Reasons | {_markdown_cell(_format_name_list(promotion_gate.get('warning_reasons')))} |",
            f"| Criteria | {_markdown_cell(_format_key_values(_dict(promotion_gate.get('criteria'))))} |",
            "",
            "## Stage Summary",
            "",
            "| Signal | Value |",
            "| --- | --- |",
            f"| Stage Count | {_int(stage_summary.get('stage_count', 0))} |",
            f"| Status Counts | {_markdown_cell(_format_counts(_dict(stage_summary.get('status_counts'))))} |",
            f"| Blocking Stages | {_markdown_cell(_format_name_list(stage_summary.get('blocking_stage_names')))} |",
            f"| Warning Stages | {_markdown_cell(_format_name_list(stage_summary.get('warning_stage_names')))} |",
            f"| Has Blockers | {str(bool(stage_summary.get('has_blockers', False))).lower()} |",
            f"| Needs Attention | {str(bool(stage_summary.get('needs_attention', False))).lower()} |",
            "",
            "## Stage Audit",
            "",
            "| Stage | Status | Evidence | Next Action |",
            "| --- | --- | --- | --- |",
        ]
    )
    for stage_value in _list(showcase.get("stage_audit")):
        stage = _dict(stage_value)
        lines.append(
            "| "
            f"{_markdown_cell(stage.get('stage', ''))} | "
            f"{_markdown_cell(stage.get('status', ''))} | "
            f"{_markdown_cell(stage.get('evidence', ''))} | "
            f"{_markdown_cell(stage.get('next_action', ''))} |"
        )
    if not _list(showcase.get("stage_audit")):
        lines.append("| none | skip |  |  |")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(showcase.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(showcase.get("next_actions")):
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Readiness",
            "",
            "| Signal | Value |",
            "| --- | --- |",
            f"| Ready Runs | {_markdown_cell(_format_name_list(readiness.get('ready_run_names')))} |",
            f"| Skipped Runs | {_markdown_cell(_format_name_list(readiness.get('skipped_run_names')))} |",
            f"| Error Runs | {_markdown_cell(_format_name_list(readiness.get('error_run_names')))} |",
            f"| Status Counts | {_markdown_cell(_format_counts(_dict(readiness.get('status_counts'))))} |",
            f"| Issue Counts | {_markdown_cell(_format_counts(_dict(readiness.get('issue_counts'))))} |",
            "",
            "## Smoke And Recommendation",
            "",
            "| Signal | Value |",
            "| --- | --- |",
            f"| Smoke Passed | {str(bool(smoke.get('passed', False))).lower()} |",
            f"| Smoke Gap Status | {_markdown_cell(smoke.get('gap_status', ''))} |",
            f"| Fallback Attempted Runs | {_int(smoke.get('fallback_attempted_count', 0))} |",
            f"| Fallback Improved Runs | {_int(smoke.get('fallback_improved_count', 0))} |",
            f"| Fallback Recovered Runs | {_int(smoke.get('fallback_recovered_count', 0))} |",
            f"| Auto Remediation Attempted Runs | {_int(smoke.get('auto_remediation_attempted_count', 0))} |",
            f"| Auto Remediation Used Runs | {_int(smoke.get('auto_remediation_used_count', 0))} |",
            f"| Auto Remediation Improved Runs | {_int(smoke.get('auto_remediation_improved_count', 0))} |",
            f"| Auto Remediation Benchmark Case Delta | {_int(smoke.get('auto_remediation_benchmark_case_delta', 0))} |",
            f"| Auto Remediation Actions | {_markdown_cell(_format_counts(_dict(smoke.get('auto_remediation_action_counts'))))} |",
            f"| Manifest Recommendations | {_int(smoke.get('manifest_recommendation_count', 0))} |",
            f"| Repository Test Reports | {_int(repository_test.get('report_count', 0))} |",
            f"| Repository Test Analysis Sources | {_markdown_cell(_format_counts(_dict(repository_test.get('analysis_source_counts'))))} |",
            f"| Repository Test Overlay Trigger Reasons | {_markdown_cell(_format_counts(_dict(repository_test.get('overlay_trigger_reason_counts'))))} |",
            f"| Repository Test Phase 2 Ready Runs | {_int(repository_test.get('phase2_ready_count', 0))}: {_markdown_cell(_format_name_list(repository_test.get('phase2_ready_runs')))} |",
            f"| Repository Test Phase 3 Validation-Ready Runs | {_int(repository_test.get('phase3_validation_ready_count', 0))}: {_markdown_cell(_format_name_list(repository_test.get('phase3_validation_ready_runs')))} |",
            f"| Repository Test Execution Statuses | {_markdown_cell(_format_counts(_dict(repository_test.get('execution_status_counts'))))} |",
            f"| Repository Test Failure Categories | {_markdown_cell(_format_counts(_dict(repository_test.get('failure_category_counts'))))} |",
            f"| Repository Test Setup Install Failures | {_markdown_cell(_format_counts(_dict(repository_test.get('setup_install_failure_counts'))))} |",
            f"| Repository Test Setup Install Fallbacks | {_int(repository_test.get('setup_install_fallback_count', 0))} / {_int(repository_test.get('setup_install_fallback_success_count', 0))} succeeded |",
            f"| Repository Test Top Localized Functions | {_markdown_cell(_format_counts(_dict(repository_test.get('fault_localization_top_function_counts'))))} |",
            f"| Repository Test Patch Validation Statuses | {_markdown_cell(_format_counts(_dict(repository_test.get('patch_validation_status_counts'))))} |",
            f"| Repository Test Patch Validation Success Runs | {_int(repository_test.get('patch_validation_success_run_count', 0))}: {_markdown_cell(_format_name_list(repository_test.get('patch_validation_success_runs')))} |",
            f"| Repository Test Patch Validation Successes | {_int(repository_test.get('patch_validation_success_count', 0))} |",
            f"| Repository Test Final Statuses | {_markdown_cell(_format_counts(_dict(repository_test.get('final_status_counts'))))} |",
            f"| Repository Test Blocked Reasons | {_markdown_cell(_format_counts(_dict(repository_test.get('blocked_reason_counts'))))} |",
            f"| Recommendation Rerun Passed | {str(bool(recommendation.get('rerun_passed', False))).lower()} |",
            f"| Recommendation Fallback Recovery Resolved | {_int(recommendation.get('fallback_recovery_resolved_count', 0))} |",
            f"| Recommendation Regressions | {_markdown_cell(', '.join(str(item) for item in _list(recommendation.get('regressions'))) or 'none')} |",
        ]
    )
    run_summaries = _list(repository_test.get("run_summaries"))
    if run_summaries:
        lines.extend(
            [
                "",
                "## Repository Test Run Outcomes",
                "",
                "| Run | Final Status | Reason | Command | Failure Category | Top Function | Patch Successes |",
                "| --- | --- | --- | --- | --- | --- | ---: |",
            ]
        )
        for item_value in run_summaries:
            item = _dict(item_value)
            lines.append(
                "| "
                f"{_markdown_cell(item.get('name', ''))} | "
                f"{_markdown_cell(item.get('final_status', ''))} | "
                f"{_markdown_cell(item.get('final_reason', ''))} | "
                f"`{_markdown_cell(item.get('execution_command') or 'none')}` | "
                f"{_markdown_cell(item.get('failure_category') or 'none')} | "
                f"{_markdown_cell(item.get('top_function') or 'none')} | "
                f"{_int(item.get('patch_validation_success_count', 0))} |"
            )
    lines.extend(
        [
            "",
            "## Key Artifacts",
            "",
            "| Artifact | Path |",
            "| --- | --- |",
        ]
    )
    for name, path in _dict(showcase.get("artifacts")).items():
        lines.append(f"| {_markdown_cell(name)} | `{_markdown_cell(path)}` |")
    return "\n".join(lines)


def render_github_onboarding_pipeline_markdown(
    report: GitHubOnboardingPipelineReport,
) -> str:
    summary = report.summary
    failed_expectation_preview = _list(
        summary.get("repository_processing_expectation_failed_check_preview")
    )
    if not failed_expectation_preview:
        failed_expectation_preview = _repository_processing_failed_expectation_checks(
            _dict(summary.get("repository_processing_expectation_report")),
            limit=5,
        )
    first_failed_expectation = _dict(
        summary.get("repository_processing_expectation_first_failed_check")
    )
    if not first_failed_expectation and failed_expectation_preview:
        first_failed_expectation = _dict(failed_expectation_preview[0])
    lines = [
        "# GitHub Onboarding Pipeline",
        "",
        f"- Manifest: `{report.manifest_path}`",
        f"- Output Dir: `{report.output_dir}`",
        f"- Suite: `{report.suite_name}`",
        f"- Result: {'PASS' if report.passed else 'FAIL'}",
        f"- Single Repo Mode: {str(bool(summary.get('single_repo_present', False))).lower()}",
        (
            "- Single Repo Target: "
            f"repo={_markdown_cell(summary.get('single_repo_repo', ''))}; "
            f"ref={_markdown_cell(summary.get('single_repo_ref', ''))}; "
            f"run={_markdown_cell(summary.get('single_repo_run_name', ''))}; "
            f"preset={_markdown_cell(summary.get('single_repo_preset', ''))}"
        ),
        (
            "- Single Repo Filters: "
            f"include={_markdown_cell(_format_name_list(summary.get('single_repo_include')))}; "
            f"exclude={_markdown_cell(_format_name_list(summary.get('single_repo_exclude')))}"
        ),
        f"- Repository Processing Acceptance: {'PASS' if summary.get('repository_processing_acceptance_passed') else 'FAIL'}",
        (
            "- Repository Processing Acceptance Reason: "
            f"{_markdown_cell(summary.get('repository_processing_acceptance_mode', ''))}:"
            f"{_markdown_cell(summary.get('repository_processing_acceptance_reason', ''))}"
        ),
        f"- Preflight Ready Runs: {_int(summary.get('preflight_ready_count', 0))}",
        f"- Preflight Skipped Runs: {_int(summary.get('preflight_skipped_count', 0))}",
        f"- Preflight Readiness Rate: {_float(summary.get('preflight_readiness_rate', 0.0)):.4f}",
        f"- Preflight Top Issue: `{_markdown_cell(summary.get('preflight_top_issue_code', ''))}`",
        (
            "- Preflight Repository Doctor Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('preflight_profile_doctor_status_counts'))))}"
        ),
        (
            "- Preflight Repository Doctor Top Blocker: "
            f"`{_markdown_cell(summary.get('preflight_top_profile_doctor_blocker', ''))}`"
        ),
        f"- Smoke Present: {str(bool(summary.get('smoke_present', False))).lower()}",
        f"- Smoke Passed: {str(bool(summary.get('smoke_passed', False))).lower()}",
        f"- Smoke Run Count: {_int(summary.get('smoke_run_count', 0))}",
        f"- Smoke Generated Candidates: {_int(summary.get('smoke_generated_candidates', 0))}",
        f"- Smoke Benchmark Cases: {_int(summary.get('smoke_benchmark_cases', 0))}",
        f"- Smoke Static Intelligence Runs: {_int(summary.get('smoke_static_intelligence_run_count', 0))}",
        f"- Smoke Static Intelligence Analysis Ready Runs: {_int(summary.get('smoke_static_intelligence_analysis_ready_count', 0))}",
        (
            "- Smoke Static Intelligence Signals: "
            f"{_int(summary.get('smoke_static_intelligence_selected_signal_count', 0))} selected / "
            f"{_int(summary.get('smoke_static_intelligence_total_signal_count', 0))} total"
        ),
        (
            "- Smoke Static Intelligence Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('smoke_static_intelligence_status_counts'))))}"
        ),
        (
            "- Smoke Static Intelligence Rules: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('smoke_static_intelligence_rule_counts'))))}"
        ),
        f"- Smoke Fallback Attempted Runs: {_int(summary.get('smoke_fallback_attempted_count', 0))}",
        f"- Smoke Fallback Improved Runs: {_int(summary.get('smoke_fallback_improved_count', 0))}",
        f"- Smoke Fallback Recovered Runs: {_int(summary.get('smoke_fallback_recovered_count', 0))}",
        f"- Smoke Auto Remediation Attempted Runs: {_int(summary.get('smoke_auto_remediation_attempted_count', 0))}",
        f"- Smoke Auto Remediation Used Runs: {_int(summary.get('smoke_auto_remediation_used_count', 0))}",
        f"- Smoke Auto Remediation Improved Runs: {_int(summary.get('smoke_auto_remediation_improved_count', 0))}",
        f"- Smoke Auto Remediation Benchmark Case Delta: {_int(summary.get('smoke_auto_remediation_benchmark_case_delta', 0))}",
        (
            "- Smoke Auto Remediation Actions: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('smoke_auto_remediation_action_counts'))))}"
        ),
        f"- Smoke Benchmarkization Ready Runs: {_int(summary.get('smoke_benchmarkization_ready_count', 0))}",
        (
            "- Smoke Benchmarkization Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('smoke_benchmarkization_status_counts'))))}"
        ),
        (
            "- Smoke Benchmarkization Stages: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('smoke_benchmarkization_stage_counts'))))}"
        ),
        (
            "- Smoke Benchmarkization Actions: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('smoke_benchmarkization_primary_action_counts'))))}"
        ),
        f"- Smoke Benchmarkization Remediation Plans: {_int(summary.get('smoke_benchmarkization_remediation_plan_count', 0))}",
        (
            "- Repository Test Analysis Sources: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_analysis_source_counts'))))}"
        ),
        (
            "- Repository Test Execution Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_execution_status_counts'))))}"
        ),
        (
            "- Repository Test Failure Categories: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_failure_category_counts'))))}"
        ),
        (
            "- Repository Test Setup Install Failures: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_environment_setup_install_failure_counts'))))}"
        ),
        (
            "- Repository Test Setup Doctor Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_setup_doctor_status_counts'))))}"
        ),
        (
            "- Repository Test Setup Doctor Blockers: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_setup_doctor_blocker_counts'))))}"
        ),
        f"- Repository Test Setup Install Fallbacks: {_int(summary.get('repository_test_environment_setup_install_fallback_count', 0))}",
        f"- Repository Test Setup Install Fallback Successes: {_int(summary.get('repository_test_environment_setup_install_fallback_success_count', 0))}",
        f"- Repository Test Phase 2 Ready Runs: {_int(summary.get('repository_test_phase2_ready_count', 0))}",
        f"- Repository Test Phase 3 Validation-Ready Runs: {_int(summary.get('repository_test_phase3_validation_ready_count', 0))}",
        (
            "- Repository Test Top Localized Functions: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_fault_localization_top_function_counts'))))}"
        ),
        f"- Repository Test Patch Validation Success Runs: {_int(summary.get('repository_test_patch_validation_success_run_count', 0))}",
        f"- Repository Test Patch Validation Successes: {_int(summary.get('repository_test_patch_validation_success_count', 0))}",
        (
            "- Repository Test Final Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_final_status_counts'))))}"
        ),
        (
            "- Repository Test Blocked Reasons: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_test_blocked_reason_counts'))))}"
        ),
        (
            "- Pipeline Stage Statuses: "
            f"{_markdown_cell(_format_key_values(_dict(summary.get('pipeline_stage_statuses'))))}"
        ),
        (
            "- Repository Processing Diagnosis: "
            f"status={_markdown_cell(summary.get('repository_processing_status', ''))}; "
            f"layer={_markdown_cell(summary.get('repository_processing_primary_layer', ''))}; "
            f"blocker={_markdown_cell(summary.get('repository_processing_primary_blocker', ''))}"
        ),
        (
            "- Repository Processing Next Action: "
            f"{_markdown_cell(summary.get('repository_processing_next_action', ''))}"
        ),
        f"- Repository Processing Command: `{_markdown_cell(summary.get('repository_processing_command', ''))}`",
        f"- Repository Processing Runs: {_int(summary.get('repository_processing_run_count', 0))}",
        (
            "- Repository Processing Status Counts: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_processing_status_counts'))))}"
        ),
        (
            "- Repository Processing Primary Layers: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_processing_primary_layer_counts'))))}"
        ),
        (
            "- Repository Processing Primary Blockers: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_processing_primary_blocker_counts'))))}"
        ),
        (
            "- Repository Processing Repair Summary Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_processing_repair_summary_status_counts'))))}"
        ),
        (
            "- Repository Processing Repair Summary Conclusions: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_processing_repair_summary_conclusion_counts'))))}"
        ),
        f"- Repository Processing Expectations Passed: {str(bool(summary.get('repository_processing_expectation_passed', True))).lower()}",
        f"- Repository Processing Expectation Checks: {_int(summary.get('repository_processing_expectation_check_count', 0))}",
        f"- Repository Processing Expectation Failures: {_int(summary.get('repository_processing_expectation_failed_count', 0))}",
        (
            "- Repository Processing First Failed Expectation: "
            f"{_markdown_cell(_format_expectation_check(first_failed_expectation))}"
        ),
        (
            "- Repository Processing Failed Expectation Preview: "
            f"{_markdown_cell(_format_expectation_checks(failed_expectation_preview))}"
        ),
        (
            "- Repository Repair Stage: "
            f"{_markdown_cell(summary.get('repository_repair_stage_status', ''))}; "
            f"{_markdown_cell(summary.get('repository_repair_stage_evidence', ''))}"
        ),
        (
            "- Repository Repair Next Action: "
            f"{_markdown_cell(summary.get('repository_repair_stage_next_action', ''))}"
        ),
        f"- Repository Repair Manifest Present: {str(bool(summary.get('repository_repair_manifest_present', False))).lower()}",
        f"- Repository Repair Manifest Path: `{_markdown_cell(summary.get('repository_repair_manifest_path', ''))}`",
        f"- Repository Repair Manifest Markdown Path: `{_markdown_cell(summary.get('repository_repair_manifest_markdown_path', ''))}`",
        f"- Repository Repair Manifest Applied Runs: {_int(summary.get('repository_repair_manifest_applied_run_count', 0))}",
        (
            "- Repository Repair Manifest Defaults: "
            f"{_markdown_cell(_format_name_list(summary.get('repository_repair_manifest_applied_defaults')))}"
        ),
        f"- Repository Repair Manifest Setup Repair Defaults Applied: {str(bool(summary.get('repository_repair_manifest_setup_repair_defaults_applied', False))).lower()}",
        (
            "- Repository Repair Manifest Setup Failures: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_repair_manifest_setup_install_failure_counts'))))}"
        ),
        (
            "- Repository Repair Manifest Top Setup Failure: "
            f"{_markdown_cell(summary.get('repository_repair_manifest_top_setup_install_failure', ''))}"
        ),
        (
            "- Repository Repair Manifest Setup Doctor Blockers: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('repository_repair_manifest_setup_doctor_blocker_counts'))))}"
        ),
        (
            "- Repository Repair Manifest Top Setup Doctor Blocker: "
            f"{_markdown_cell(summary.get('repository_repair_manifest_top_setup_doctor_blocker', ''))}"
        ),
        (
            "- Repository Repair Manifest Setup Doctor Next Action: "
            f"{_markdown_cell(summary.get('repository_repair_manifest_setup_doctor_next_action', ''))}"
        ),
        f"- Repository Repair Manifest Run Contexts: {_int(summary.get('repository_repair_manifest_run_context_count', 0))}",
        (
            "- Repository Repair Manifest Setup Repair Runs: "
            f"{_markdown_cell(_format_name_list(summary.get('repository_repair_manifest_setup_repair_run_names')))}"
        ),
        (
            "- Repository Repair Manifest Checkout-Only Runs: "
            f"{_markdown_cell(_format_name_list(summary.get('repository_repair_manifest_checkout_only_run_names')))}"
        ),
        (
            "- Repository Repair Manifest Setup Next Action: "
            f"{_markdown_cell(summary.get('repository_repair_manifest_setup_repair_next_action', ''))}"
        ),
        f"- Recommendation Rerun Enabled: {str(bool(summary.get('recommendation_rerun_enabled', False))).lower()}",
        f"- Recommendation Applied Count: {_int(summary.get('recommendation_applied_count', 0))}",
        f"- Recommended Pipeline Manifest Present: {str(bool(summary.get('recommended_pipeline_manifest_present', False))).lower()}",
        f"- Recommended Pipeline Manifest Path: `{_markdown_cell(summary.get('recommended_pipeline_manifest_path', ''))}`",
        f"- Recommendation Rerun Present: {str(bool(summary.get('recommendation_rerun_present', False))).lower()}",
        f"- Recommendation Rerun Passed: {str(bool(summary.get('recommendation_rerun_passed', False))).lower()}",
        f"- Recommendation Comparison Passed: {str(bool(summary.get('recommendation_comparison_passed', False))).lower()}",
        f"- Recommendation Regressions: {_int(summary.get('recommendation_regression_count', 0))}",
        f"- Recommendation Status: `{_markdown_cell(summary.get('recommendation_status', ''))}`",
        f"- Promotion Status: `{_markdown_cell(summary.get('promotion_status', ''))}`",
        f"- Promotion Promotable: {str(bool(summary.get('promotion_promotable', False))).lower()}",
        "",
        "## Pipeline Showcase",
        "",
        "| Signal | Value |",
        "| --- | --- |",
        (
            "| Ready Runs | "
            f"{_markdown_cell(_format_name_list(summary.get('preflight_ready_run_names')))} |"
        ),
        (
            "| Skipped Runs | "
            f"{_markdown_cell(_format_name_list(summary.get('preflight_skipped_run_names')))} |"
        ),
        (
            "| Error Runs | "
            f"{_markdown_cell(_format_name_list(summary.get('preflight_error_run_names')))} |"
        ),
        (
            "| Smoke Evidence | "
            f"{_int(summary.get('smoke_generated_candidates', 0))} candidates / "
            f"{_int(summary.get('smoke_benchmark_cases', 0))} benchmark cases; "
            f"fallback recovered={_int(summary.get('smoke_fallback_recovered_count', 0))}; "
            f"auto remediation used={_int(summary.get('smoke_auto_remediation_used_count', 0))}; "
            f"benchmarkization={_markdown_cell(_format_counts(_dict(summary.get('smoke_benchmarkization_status_counts'))))} |"
        ),
        (
            "| Repository Test Readiness | "
            f"phase2={_int(summary.get('repository_test_phase2_ready_count', 0))}, "
            f"phase3={_int(summary.get('repository_test_phase3_validation_ready_count', 0))}, "
            f"patch_success_runs={_int(summary.get('repository_test_patch_validation_success_run_count', 0))}, "
            f"final={_markdown_cell(_format_counts(_dict(summary.get('repository_test_final_status_counts'))))}, "
            f"sources={_markdown_cell(_format_counts(_dict(summary.get('repository_test_analysis_source_counts'))))} |"
        ),
        (
            "| Repository Repair Stage | "
            f"status={_markdown_cell(summary.get('repository_repair_stage_status', ''))}, "
            f"evidence={_markdown_cell(summary.get('repository_repair_stage_evidence', ''))}, "
            f"next={_markdown_cell(summary.get('repository_repair_stage_next_action', ''))} |"
        ),
        (
            "| Repository Processing Acceptance | "
            f"passed={str(bool(summary.get('repository_processing_acceptance_passed', False))).lower()}, "
            f"mode={_markdown_cell(summary.get('repository_processing_acceptance_mode', ''))}, "
            f"reason={_markdown_cell(summary.get('repository_processing_acceptance_reason', ''))} |"
        ),
        (
            "| Recommendation Impact | "
            f"candidates {_signed_int(summary.get('recommendation_candidate_delta', 0))}, "
            f"benchmark cases {_signed_int(summary.get('recommendation_benchmark_case_delta', 0))}, "
            f"fallback resolved {_int(summary.get('recommendation_fallback_recovery_resolved_count', 0))}, "
            f"validation {_signed_float(summary.get('recommendation_validation_pass_rate_delta', 0.0))} |"
        ),
        (
            "| Promotion Gate | "
            f"status={_markdown_cell(summary.get('promotion_status', ''))}, "
            f"promotable={str(bool(summary.get('promotion_promotable', False))).lower()}, "
            f"blockers={_markdown_cell(_format_name_list(summary.get('promotion_blocking_reasons')))}, "
            f"warnings={_markdown_cell(_format_name_list(summary.get('promotion_warning_reasons')))} |"
        ),
        "",
        "## Artifacts",
        "",
        "| Artifact | Path |",
        "| --- | --- |",
    ]
    for key, value in report.output_paths.items():
        lines.append(f"| {_markdown_cell(key)} | `{_markdown_cell(value)}` |")
    plan_runs = _dict(summary.get("smoke_benchmarkization_remediation_plan_runs"))
    if plan_runs:
        lines.extend(
            [
                "",
                "## Smoke Benchmarkization Remediation Plans",
                "",
                "| Run | Plan |",
                "| --- | --- |",
            ]
        )
        for run_name, plan_path in sorted(plan_runs.items()):
            lines.append(
                "| "
                f"{_markdown_cell(run_name)} | "
                f"`{_markdown_cell(plan_path)}` |"
            )
    lines.extend(
        [
            "",
            "## Pipeline Rerun Command",
            "",
            "```bash",
            _pipeline_rerun_command(report),
            "```",
            "",
            "## Smoke Command",
            "",
            "```bash",
            (
                "python -m code_intelligence_agent.evaluation.github_onboarding_smoke_runner "
                f"{report.output_paths['preflight_smoke_manifest']} "
                f"{Path(report.output_dir) / 'smoke'} "
                f"--output-json {report.output_paths['smoke_runner_json']} "
                f"--output-markdown {report.output_paths['smoke_runner_markdown']}"
            ),
            "```",
        ]
    )
    if _int(summary.get("recommendation_applied_count", 0)) > 0:
        lines.extend(
            [
                "",
                "## Recommended Pipeline Manifest Command",
                "",
                "```bash",
                (
                    "python -m code_intelligence_agent.evaluation.github_onboarding_pipeline "
                    f"{report.output_paths['recommended_pipeline_manifest']} "
                    f"{Path(report.output_dir) / 'recommended_pipeline'}"
                ),
                "```",
            ]
        )
    if bool(summary.get("repository_repair_manifest_present", False)):
        lines.extend(
            [
                "",
                "## Repository Repair Manifest Command",
                "",
                "```bash",
                str(
                    summary.get("repository_repair_manifest_command")
                    or _repository_repair_manifest_command(
                        summary.get("repository_repair_manifest_path", ""),
                        report.output_dir,
                    )
                ),
                "```",
            ]
        )
    if summary.get("recommendation_rerun_present"):
        lines.extend(
            [
                "",
                "## Recommendation Rerun Command",
                "",
                "```bash",
                (
                    "python -m code_intelligence_agent.evaluation.github_onboarding_smoke_runner "
                    f"{Path(report.output_dir) / 'smoke' / 'onboarding_smoke_recommended_manifest.json'} "
                    f"{Path(report.output_dir) / 'recommended_smoke'} "
                    f"--output-json {report.output_paths['recommended_smoke_runner_json']} "
                    f"--output-markdown {report.output_paths['recommended_smoke_runner_markdown']}"
                ),
                "```",
                "",
                "## Recommendation Comparison Command",
                "",
                "```bash",
                (
                    "python -m code_intelligence_agent.evaluation.onboarding_recommendation_comparator "
                    f"{report.output_paths['smoke_runner_json']} "
                    f"{report.output_paths['recommended_smoke_runner_json']} "
                    f"--output-json {report.output_paths['recommendation_comparison_json']} "
                    f"--output-markdown {report.output_paths['recommendation_comparison_markdown']}"
                ),
                "```",
            ]
        )
    return "\n".join(lines)


def _pipeline_rerun_command(report: GitHubOnboardingPipelineReport) -> str:
    input_manifest = report.output_paths.get("input_manifest") or report.manifest_path
    command = (
        "python -m code_intelligence_agent.evaluation.github_onboarding_pipeline "
        f"{input_manifest} {_pipeline_rerun_output_dir(report.output_dir)}"
    )
    if not bool(_dict(report.summary).get("recommendation_rerun_enabled", False)):
        command = f"{command} --skip-recommendation-rerun"
    return command


def _pipeline_rerun_output_dir(output_dir: str) -> Path:
    output_path = Path(output_dir)
    if output_path.name:
        return output_path.with_name(f"{output_path.name}_rerun")
    return output_path / "rerun"


def _repository_repair_manifest_command(
    manifest_path: str | Path,
    output_dir: str | Path,
) -> str:
    if not str(manifest_path):
        return ""
    return (
        "python -m code_intelligence_agent.evaluation.github_onboarding_pipeline "
        f"{manifest_path} {Path(output_dir) / 'repository_repair'}"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run GitHub onboarding end-to-end: batch preflight followed by "
            "the smoke runner for ready repositories."
        )
    )
    parser.add_argument(
        "manifest",
        help=(
            "Path to preflight batch manifest. With --repo-mode, this is a "
            "GitHub repo spec such as owner/repo or https://github.com/owner/repo."
        ),
    )
    parser.add_argument("output_dir", help="Directory for pipeline artifacts.")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    parser.add_argument("--output-showcase-json")
    parser.add_argument("--output-showcase-markdown")
    parser.add_argument(
        "--repo-mode",
        action="store_true",
        help=(
            "Treat the first positional argument as a single GitHub repository "
            "and generate a reproducible one-run pipeline manifest."
        ),
    )
    parser.add_argument(
        "--repo-repair-profile",
        action="store_true",
        help=(
            "With --repo-mode, apply one-click repository repair defaults: "
            "checkout repository tests, failure-overlay candidates, reflection, "
            "fallback expansion, and repaired-run promotion thresholds."
        ),
    )
    parser.add_argument("--ref", help="Repository ref used by --repo-mode.")
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Repository path include filter for --repo-mode; may be repeated.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Repository path exclude filter for --repo-mode; may be repeated.",
    )
    parser.add_argument(
        "--preset",
        default="smoke",
        help="Onboarding preset used by --repo-mode.",
    )
    parser.add_argument(
        "--sample-sources",
        type=int,
        help="Preflight sample source limit used by --repo-mode.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        help="Benchmark candidate limit used by --repo-mode.",
    )
    parser.add_argument(
        "--auto-fallback",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable automatic fallback in the --repo-mode manifest.",
    )
    parser.add_argument(
        "--fallback-min-generated-candidates",
        type=int,
        help=(
            "Write thresholds.min_generated_candidates into the --repo-mode "
            "manifest so low-yield runs trigger fallback recovery."
        ),
    )
    parser.add_argument(
        "--fallback-max-sources",
        type=int,
        help="Fallback source limit written into the --repo-mode manifest.",
    )
    parser.add_argument(
        "--fallback-max-candidates",
        type=int,
        help="Fallback candidate limit written into the --repo-mode manifest.",
    )
    parser.add_argument(
        "--fallback-preset",
        help="Fallback preset written into the --repo-mode manifest.",
    )
    parser.add_argument(
        "--fallback-recipe",
        action="append",
        help="Fallback recipe written into the --repo-mode manifest; repeatable.",
    )
    parser.add_argument(
        "--auto-remediate-benchmark",
        action="store_true",
        help=(
            "Write auto_remediate_benchmark into the --repo-mode manifest so "
            "repo-agent can execute low-risk benchmark remediation actions."
        ),
    )
    parser.add_argument(
        "--source-cache-dir",
        help="Source cache directory used by --repo-mode.",
    )
    parser.add_argument(
        "--auto-scoped-include",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "With --repo-mode, let preflight write sampled Python source paths "
            "as include filters for the downstream smoke/repair run."
        ),
    )
    parser.add_argument(
        "--repository-test-root",
        help="Local repository test root copied into the --repo-mode manifest.",
    )
    parser.add_argument(
        "--repository-test-timeout",
        type=int,
        help="Repository test execution timeout copied into the --repo-mode manifest.",
    )
    parser.add_argument(
        "--repository-test-failure-overlay-candidate-limit",
        type=int,
        help="Failure-overlay candidate limit copied into the --repo-mode manifest.",
    )
    parser.add_argument(
        "--repository-test-reflection-mode",
        choices=["rule", "llm", "none"],
        help="Patch-validation reflection mode copied into the --repo-mode manifest.",
    )
    parser.add_argument(
        "--repository-test-reflection-rounds",
        type=int,
        help="Patch-validation reflection depth copied into the --repo-mode manifest.",
    )
    parser.add_argument(
        "--repository-test-reflection-width",
        type=int,
        help="Patch-validation reflection width copied into the --repo-mode manifest.",
    )
    parser.add_argument(
        "--run-repository-test-environment-setup",
        action="store_true",
        help="Enable repository test environment setup in the --repo-mode manifest.",
    )
    parser.add_argument(
        "--run-repository-test-retry",
        action="store_true",
        help="Enable safe repository test retry execution in the --repo-mode manifest.",
    )
    parser.add_argument(
        "--run-repository-test-retry-prerequisites",
        action="store_true",
        help=(
            "Allow safe prerequisite execution before repository test retry "
            "in the --repo-mode manifest."
        ),
    )
    parser.add_argument(
        "--auto-repository-test-retry",
        action="store_true",
        help=(
            "With --repo-mode, automatically execute recommended repository "
            "test retries up to --auto-repository-test-retry-max-risk."
        ),
    )
    parser.add_argument(
        "--auto-repository-test-retry-max-risk",
        choices=["low", "medium", "high"],
        help="Maximum auto retry risk copied into the --repo-mode manifest.",
    )
    parser.add_argument(
        "--auto-repository-test-retry-runner",
        action="append",
        default=[],
        help=(
            "Allowed auto retry python -m runner copied into the --repo-mode "
            "manifest; repeat for multiple runners."
        ),
    )
    parser.add_argument(
        "--repository-test-environment-setup-timeout",
        type=int,
        help="Environment setup timeout copied into the --repo-mode manifest.",
    )
    parser.add_argument(
        "--checkout-repository-tests",
        action="store_true",
        help="Enable automatic repository checkout in the --repo-mode manifest.",
    )
    parser.add_argument(
        "--repository-checkout-timeout",
        type=int,
        help="Repository checkout timeout copied into the --repo-mode manifest.",
    )
    parser.add_argument(
        "--repository-checkout-depth",
        type=int,
        help="Repository checkout depth copied into the --repo-mode manifest.",
    )
    parser.add_argument(
        "--no-repository-test-command",
        action="store_true",
        help="Disable repository test command artifacts in the --repo-mode manifest.",
    )
    parser.add_argument(
        "--suite-name",
        help="Generated suite_name used by --repo-mode.",
    )
    parser.add_argument(
        "--run-name",
        help="Generated run name used by --repo-mode.",
    )
    parser.add_argument(
        "--require-promotion",
        action="store_true",
        help=(
            "Return a failing exit code unless the pipeline showcase promotion "
            "gate reaches promotable=true."
        ),
    )
    parser.add_argument(
        "--require-processing-expectations",
        action="store_true",
        help=(
            "Return a failing exit code unless "
            "repository_processing_matrix_expectations pass when present."
        ),
    )
    parser.add_argument(
        "--promotion-min-readiness-rate",
        type=float,
        help="Override promotion_gate.min_readiness_rate for this run.",
    )
    parser.add_argument(
        "--promotion-min-smoke-benchmark-cases",
        type=int,
        help="Override promotion_gate.min_smoke_benchmark_cases for this run.",
    )
    parser.add_argument(
        "--promotion-min-fallback-recovered-count",
        type=int,
        help="Override promotion_gate.min_fallback_recovered_count for this run.",
    )
    parser.add_argument(
        "--promotion-min-repository-test-phase2-ready-count",
        type=int,
        help=(
            "Override promotion_gate.min_repository_test_phase2_ready_count "
            "for this run."
        ),
    )
    parser.add_argument(
        "--promotion-min-repository-test-phase3-validation-ready-count",
        type=int,
        help=(
            "Override promotion_gate.min_repository_test_phase3_validation_ready_count "
            "for this run."
        ),
    )
    parser.add_argument(
        "--promotion-min-repository-test-repaired-count",
        type=int,
        help=(
            "Override promotion_gate.min_repository_test_repaired_count "
            "for this run."
        ),
    )
    parser.add_argument(
        "--promotion-allow-warning-stages",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override whether warning stages can still be promoted.",
    )
    parser.add_argument(
        "--promotion-fail-on-recommendation-regression",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override whether recommendation regressions block promotion.",
    )
    parser.add_argument(
        "--skip-recommendation-rerun",
        action="store_true",
        help=(
            "Only run the baseline preflight and smoke suite; do not rerun the "
            "generated recommended manifest."
        ),
    )
    return parser


def main(argv: list[str] | None = None, opener=None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.repo_repair_profile and not args.repo_mode:
        parser.error("--repo-repair-profile requires --repo-mode")
    if args.repo_repair_profile:
        _apply_repo_repair_profile_defaults(args)
    promotion_overrides = _promotion_config_overrides_from_args(args)
    repo_mode_promotion_gate = _provided_config(promotion_overrides)
    repo_mode_expected_processing = (
        _repo_repair_profile_expected_processing()
        if args.repo_repair_profile
        else None
    )
    if args.repo_mode:
        report = run_github_onboarding_pipeline_for_repo(
            args.manifest,
            args.output_dir,
            ref=args.ref,
            include=args.include,
            exclude=args.exclude,
            preset=args.preset,
            sample_sources=args.sample_sources,
            max_candidates=args.max_candidates,
            auto_fallback=args.auto_fallback,
            fallback_min_generated_candidates=args.fallback_min_generated_candidates,
            fallback_max_sources=args.fallback_max_sources,
            fallback_max_candidates=args.fallback_max_candidates,
            fallback_preset=args.fallback_preset,
            fallback_recipe=args.fallback_recipe,
            auto_remediate_benchmark=args.auto_remediate_benchmark,
            source_cache_dir=args.source_cache_dir,
            auto_scoped_include=args.auto_scoped_include,
            repository_test_root=args.repository_test_root,
            repository_test_timeout=args.repository_test_timeout,
            repository_test_failure_overlay_candidate_limit=(
                args.repository_test_failure_overlay_candidate_limit
            ),
            repository_test_reflection_mode=args.repository_test_reflection_mode,
            repository_test_reflection_rounds=args.repository_test_reflection_rounds,
            repository_test_reflection_width=args.repository_test_reflection_width,
            run_repository_test_environment_setup=(
                args.run_repository_test_environment_setup
            ),
            run_repository_test_retry=args.run_repository_test_retry,
            run_repository_test_retry_prerequisites=(
                args.run_repository_test_retry_prerequisites
            ),
            auto_repository_test_retry=args.auto_repository_test_retry,
            auto_repository_test_retry_max_risk=(
                args.auto_repository_test_retry_max_risk
            ),
            auto_repository_test_retry_allowed_runners=(
                args.auto_repository_test_retry_runner
            ),
            repository_test_environment_setup_timeout=(
                args.repository_test_environment_setup_timeout
            ),
            checkout_repository_tests=args.checkout_repository_tests,
            repository_checkout_timeout=args.repository_checkout_timeout,
            repository_checkout_depth=args.repository_checkout_depth,
            no_repository_test_command=args.no_repository_test_command,
            suite_name=args.suite_name,
            run_name=args.run_name,
            opener=opener,
            run_recommendation_rerun=not args.skip_recommendation_rerun,
            promotion_config_overrides=promotion_overrides,
            expected_repository_processing=repo_mode_expected_processing,
            promotion_gate=repo_mode_promotion_gate,
        )
    else:
        report = run_github_onboarding_pipeline(
            args.manifest,
            args.output_dir,
            opener=opener,
            run_recommendation_rerun=not args.skip_recommendation_rerun,
            promotion_config_overrides=promotion_overrides,
        )
    json_payload = json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
    markdown = render_github_onboarding_pipeline_markdown(report)
    if args.output_json:
        Path(args.output_json).write_text(json_payload, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown, encoding="utf-8")
    if args.output_showcase_json and report.pipeline_showcase is not None:
        Path(args.output_showcase_json).write_text(
            json.dumps(report.pipeline_showcase, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if args.output_showcase_markdown and report.pipeline_showcase is not None:
        Path(args.output_showcase_markdown).write_text(
            render_github_onboarding_pipeline_showcase_markdown(
                report.pipeline_showcase
            ),
            encoding="utf-8",
        )
    if args.format == "json":
        print(json_payload)
    else:
        print(markdown)
    raise SystemExit(
        _pipeline_cli_exit_code(
            report,
            require_promotion=bool(args.require_promotion),
            require_processing_expectations=bool(
                args.require_processing_expectations
            ),
        )
    )


def _pipeline_cli_exit_code(
    report: GitHubOnboardingPipelineReport,
    *,
    require_promotion: bool = False,
    require_processing_expectations: bool = False,
) -> int:
    if require_promotion or require_processing_expectations:
        passed = True
        if require_promotion:
            promotion_gate = _dict(
                _dict(report.pipeline_showcase).get("promotion_gate")
            )
            passed = passed and bool(promotion_gate.get("promotable"))
        if require_processing_expectations:
            passed = passed and bool(
                _dict(report.summary).get(
                    "repository_processing_expectation_passed",
                    True,
                )
            )
        return 0 if passed else 1
    return 0 if report.passed else 1


def _pipeline_output_paths(output_root: Path, manifest_path: Path) -> dict[str, str]:
    return {
        "input_manifest": str(manifest_path),
        "pipeline_json": str(output_root / "github_onboarding_pipeline.json"),
        "pipeline_markdown": str(output_root / "github_onboarding_pipeline.md"),
        "pipeline_showcase_json": str(
            output_root / "github_onboarding_pipeline_showcase.json"
        ),
        "pipeline_showcase_markdown": str(
            output_root / "github_onboarding_pipeline_showcase.md"
        ),
        "preflight_batch_json": str(
            output_root / "preflight" / "preflight_batch_report.json"
        ),
        "preflight_batch_markdown": str(
            output_root / "preflight" / "preflight_batch_report.md"
        ),
        "preflight_smoke_manifest": str(
            output_root / "preflight" / "preflight_batch_smoke_manifest.json"
        ),
        "preflight_offline_manifest": str(
            output_root / "preflight" / "preflight_batch_offline_manifest.json"
        ),
        "smoke_runner_json": str(output_root / "smoke" / "runner.json"),
        "smoke_runner_markdown": str(output_root / "smoke" / "runner.md"),
        "recommended_smoke_runner_json": str(
            output_root / "recommended_smoke" / "runner.json"
        ),
        "recommended_smoke_runner_markdown": str(
            output_root / "recommended_smoke" / "runner.md"
        ),
        "repository_repair_manifest": str(
            output_root / "github_onboarding_pipeline_repository_repair_manifest.json"
        ),
        "repository_repair_manifest_markdown": str(
            output_root / "github_onboarding_pipeline_repository_repair_manifest.md"
        ),
        "recommended_pipeline_manifest": str(
            output_root / "github_onboarding_pipeline_recommended_manifest.json"
        ),
        "recommendation_comparison_json": str(
            output_root / "recommendation_comparison.json"
        ),
        "recommendation_comparison_markdown": str(
            output_root / "recommendation_comparison.md"
        ),
    }


def _write_smoke_runner_artifacts(
    report: OnboardingSmokeRunnerReport,
    json_path: str,
    markdown_path: str,
) -> None:
    path = Path(json_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(markdown_path).write_text(
        render_onboarding_smoke_runner_markdown(report),
        encoding="utf-8",
    )


def _write_pipeline_artifacts(report: GitHubOnboardingPipelineReport) -> None:
    Path(report.output_paths["pipeline_json"]).write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(report.output_paths["pipeline_markdown"]).write_text(
        render_github_onboarding_pipeline_markdown(report),
        encoding="utf-8",
    )
    if report.pipeline_showcase is not None:
        Path(report.output_paths["pipeline_showcase_json"]).write_text(
            json.dumps(report.pipeline_showcase, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        Path(report.output_paths["pipeline_showcase_markdown"]).write_text(
            render_github_onboarding_pipeline_showcase_markdown(
                report.pipeline_showcase
            ),
            encoding="utf-8",
        )


def _write_recommended_pipeline_manifest(
    *,
    source_manifest_path: str | Path,
    original_manifest_payload: dict[str, Any],
    target_path: str | Path,
) -> None:
    source_path = Path(source_manifest_path)
    target = Path(target_path)
    recommended_smoke_manifest = _read_json(source_path)
    pipeline_manifest = _build_recommended_pipeline_manifest(
        recommended_smoke_manifest,
        original_manifest_payload=original_manifest_payload,
        source_manifest_dir=source_path.parent,
        target_manifest_dir=target.parent,
    )
    target.write_text(
        json.dumps(pipeline_manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _should_write_repository_repair_manifest(summary: dict[str, Any]) -> bool:
    setup_failure_counts = _dict(
        summary.get("repository_test_environment_setup_install_failure_counts")
    )
    top_setup_doctor_blocker = _top_count_key(
        _repository_test_setup_doctor_blocker_counts(summary)
    )
    return (
        str(summary.get("repository_repair_stage_status") or "") in {"skip", "warn"}
        and (
            "dynamic_evidence_not_usable:not_executed"
            in str(summary.get("repository_repair_stage_evidence") or "")
            or bool(setup_failure_counts)
            or _repository_test_setup_doctor_manifest_recommended(
                top_setup_doctor_blocker
            )
        )
    )


def _write_repository_repair_manifest(
    *,
    original_manifest_payload: dict[str, Any],
    source_manifest_path: str | Path,
    target_path: str | Path,
    markdown_path: str | Path | None = None,
    summary: dict[str, Any] | None = None,
) -> bool:
    target = Path(target_path)
    manifest = _build_repository_repair_manifest(
        original_manifest_payload,
        source_manifest_path=source_manifest_path,
        repair_context=_repository_repair_manifest_context(_dict(summary)),
    )
    if not manifest:
        return False
    target.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    if markdown_path is not None:
        Path(markdown_path).write_text(
            render_repository_repair_manifest_markdown(manifest),
            encoding="utf-8",
        )
    return True


def render_repository_repair_manifest_markdown(
    manifest: dict[str, Any],
) -> str:
    metadata = _dict(manifest.get("repository_repair_recommendation_metadata"))
    run_contexts = _dict(metadata.get("run_repair_contexts"))
    changed_fields_by_run = _dict(metadata.get("changed_fields_by_run"))
    lines = [
        "# GitHub Onboarding Repository Repair Manifest",
        "",
        f"- Suite: `{_markdown_cell(manifest.get('suite_name', ''))}`",
        f"- Source Manifest: `{_markdown_cell(metadata.get('source_manifest', ''))}`",
        f"- Applied Runs: {_int(metadata.get('applied_run_count', 0))}",
        f"- Changed Runs: {_int(metadata.get('changed_run_count', 0))}",
        (
            "- Applied Defaults: "
            f"{_markdown_cell(_format_name_list(metadata.get('applied_default_fields')))}"
        ),
        (
            "- Promotion Gate Defaults: "
            f"{_markdown_cell(_format_name_list(metadata.get('promotion_gate_defaults')))}"
        ),
        (
            "- Setup Repair Defaults Applied: "
            f"{str(bool(metadata.get('setup_repair_defaults_applied', False))).lower()}"
        ),
        (
            "- Setup Install Failures: "
            f"{_markdown_cell(_format_counts(_dict(metadata.get('setup_install_failure_counts'))))}"
        ),
        (
            "- Top Setup Install Failure: "
            f"{_markdown_cell(metadata.get('top_setup_install_failure', ''))}"
        ),
        (
            "- Setup Doctor Blockers: "
            f"{_markdown_cell(_format_counts(_dict(metadata.get('setup_doctor_blocker_counts'))))}"
        ),
        (
            "- Top Setup Doctor Blocker: "
            f"{_markdown_cell(metadata.get('top_setup_doctor_blocker', ''))}"
        ),
        (
            "- Setup Doctor Next Action: "
            f"{_markdown_cell(metadata.get('setup_doctor_next_action', ''))}"
        ),
        f"- Run Contexts: {_int(metadata.get('run_repair_context_count', 0))}",
        (
            "- Setup Repair Runs: "
            f"{_markdown_cell(_format_name_list(metadata.get('setup_repair_run_names')))}"
        ),
        (
            "- Checkout-Only Runs: "
            f"{_markdown_cell(_format_name_list(metadata.get('checkout_only_run_names')))}"
        ),
        (
            "- Setup Repair Next Action: "
            f"{_markdown_cell(metadata.get('setup_repair_next_action', ''))}"
        ),
        "",
        "## Run Repair Plan",
        "",
        "| Run | Repo | Changed Fields | Setup Repair | Blocker | Next Action |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    for index, value in enumerate(_list(manifest.get("runs"))):
        run = _dict(value)
        run_name = str(run.get("name") or f"run_{index + 1}")
        context = _dict(run_contexts.get(run_name))
        changed_fields = _list(changed_fields_by_run.get(run_name))
        setup_repair = bool(
            context.get("setup_repair", False)
            or run.get("run_repository_test_environment_setup") is True
            or run.get("run_repository_test_retry_prerequisites") is True
        )
        blocker = str(context.get("blocker") or "none")
        next_action = str(context.get("next_action") or "")
        lines.append(
            "| "
            f"{_markdown_cell(run_name)} | "
            f"`{_markdown_cell(_repository_repair_manifest_run_repo_label(run))}` | "
            f"{len(changed_fields)} | "
            f"{str(setup_repair).lower()} | "
            f"{_markdown_cell(blocker)} | "
            f"{_markdown_cell(next_action or 'none')} |"
        )
    if not _list(manifest.get("runs")):
        lines.append("| none | `none` | 0 | false | none | none |")
    lines.extend(
        [
            "",
            "## Key Repair Settings By Run",
            "",
            "| Run | Field | Value |",
            "| --- | --- | --- |",
        ]
    )
    settings_written = False
    for index, value in enumerate(_list(manifest.get("runs"))):
        run = _dict(value)
        run_name = str(run.get("name") or f"run_{index + 1}")
        for field, setting_value in _repository_repair_manifest_key_settings(run):
            settings_written = True
            lines.append(
                f"| {_markdown_cell(run_name)} | {_markdown_cell(field)} | "
                f"{_markdown_cell(_repository_repair_manifest_setting_value(setting_value))} |"
            )
    if not settings_written:
        lines.append("| none | none | none |")
    lines.extend(
        [
            "",
            "## Changed Fields By Run",
            "",
            "| Run | Fields |",
            "| --- | --- |",
        ]
    )
    if changed_fields_by_run:
        for run_name, fields in sorted(changed_fields_by_run.items()):
            lines.append(
                f"| {_markdown_cell(run_name)} | "
                f"{_markdown_cell(_format_name_list(fields))} |"
            )
    else:
        lines.append("| none | none |")
    lines.extend(
        [
            "",
            "## Promotion Gate",
            "",
            "| Field | Value |",
            "| --- | --- |",
        ]
    )
    promotion_gate = _dict(manifest.get("promotion_gate"))
    if promotion_gate:
        for key, value in sorted(promotion_gate.items()):
            lines.append(f"| {_markdown_cell(key)} | {_markdown_cell(value)} |")
    else:
        lines.append("| none | none |")
    return "\n".join(lines)


def _repository_repair_manifest_run_repo_label(run: dict[str, Any]) -> str:
    repo = str(run.get("repo") or "").strip()
    if repo:
        return repo
    repo_spec = _dict(run.get("repo_spec"))
    for key in ("repo", "url", "html_url", "clone_url"):
        value = str(repo_spec.get(key) or "").strip()
        if value:
            return value
    return "none"


def _repository_repair_manifest_key_settings(run: dict[str, Any]) -> list[tuple[str, Any]]:
    thresholds = _dict(run.get("thresholds"))
    fallback = _dict(run.get("fallback"))
    fields = [
        ("checkout_repository_tests", run.get("checkout_repository_tests")),
        (
            "run_repository_test_environment_setup",
            run.get("run_repository_test_environment_setup"),
        ),
        (
            "run_repository_test_retry_prerequisites",
            run.get("run_repository_test_retry_prerequisites"),
        ),
        ("auto_scoped_include", run.get("auto_scoped_include")),
        ("auto_fallback", run.get("auto_fallback")),
        ("thresholds.min_generated_candidates", thresholds.get("min_generated_candidates")),
        ("fallback.enabled", fallback.get("enabled")),
        ("fallback.max_sources", fallback.get("max_sources")),
        ("fallback.max_candidates", fallback.get("max_candidates")),
        ("repository_test_timeout", run.get("repository_test_timeout")),
        (
            "repository_test_failure_overlay_candidate_limit",
            run.get("repository_test_failure_overlay_candidate_limit"),
        ),
        ("repository_test_reflection_mode", run.get("repository_test_reflection_mode")),
        (
            "repository_test_reflection_rounds",
            run.get("repository_test_reflection_rounds"),
        ),
        (
            "repository_test_reflection_width",
            run.get("repository_test_reflection_width"),
        ),
        ("repository_checkout_depth", run.get("repository_checkout_depth")),
    ]
    return [(field, value) for field, value in fields if value is not None]


def _repository_repair_manifest_setting_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _build_repository_repair_manifest(
    original_manifest_payload: dict[str, Any],
    *,
    source_manifest_path: str | Path,
    repair_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = copy.deepcopy(original_manifest_payload)
    context = _dict(repair_context)
    setup_failure_counts = _dict(context.get("setup_install_failure_counts"))
    setup_doctor_blocker_counts = _dict(context.get("setup_doctor_blocker_counts"))
    top_setup_doctor_blocker = str(context.get("top_setup_doctor_blocker") or "")
    global_setup_repair = bool(
        setup_failure_counts
        or _repository_test_setup_doctor_requires_setup_repair(
            top_setup_doctor_blocker
        )
    )
    run_repair_contexts = {
        str(name): _dict(value)
        for name, value in _dict(context.get("run_repair_contexts")).items()
        if str(name)
    }
    source_suite_name = str(payload.get("suite_name") or "github_onboarding")
    defaults = _dict(payload.get("defaults"))
    runs: list[dict[str, Any]] = []
    applied_run_names: list[str] = []
    changed_fields_by_run: dict[str, list[str]] = {}
    applied_default_fields: set[str] = set()
    changed_run_count = 0
    for index, raw_run in enumerate(_list(payload.get("runs"))):
        run = copy.deepcopy(_dict(raw_run))
        effective_run = {**defaults, **run}
        if _repository_repair_manifest_run_eligible(effective_run):
            run_name = str(run.get("name") or f"run_{index + 1}")
            run_context = _dict(run_repair_contexts.get(run_name))
            setup_repair = bool(
                run_context.get("setup_repair", False)
                if run_repair_contexts
                else global_setup_repair
            )
            changed_fields = _apply_repository_repair_run_defaults(
                run,
                setup_repair=setup_repair,
            )
            if changed_fields:
                changed_run_count += 1
                changed_fields_by_run[run_name] = changed_fields
                applied_default_fields.update(changed_fields)
            applied_run_names.append(run_name)
        runs.append(run)
    if not applied_run_names or changed_run_count <= 0:
        return {}
    payload["suite_name"] = f"{source_suite_name}_repository_repair"
    payload["description"] = (
        "Generated by github_onboarding_pipeline to rerun repository-test "
        "repair with checkout, failure-overlay and reflection defaults."
    )
    payload["runs"] = runs
    promotion_gate = dict(_dict(payload.get("promotion_gate")))
    promotion_gate_defaults: list[str] = []
    for key, value in REPO_REPAIR_PROFILE_PROMOTION_GATE.items():
        if promotion_gate.get(key) is None:
            promotion_gate[key] = value
            promotion_gate_defaults.append(key)
    payload["promotion_gate"] = promotion_gate
    payload["repository_repair_recommendation_metadata"] = {
        "pipeline_manifest_kind": "repository_repair_manifest",
        "source_manifest": str(source_manifest_path),
        "reason": "repository_repair_stage_not_executed",
        "applied_run_count": len(applied_run_names),
        "changed_run_count": changed_run_count,
        "applied_run_names": applied_run_names,
        "applied_default_fields": sorted(applied_default_fields),
        "changed_fields_by_run": changed_fields_by_run,
        "promotion_gate_defaults": promotion_gate_defaults,
        "setup_install_failure_counts": dict(sorted(setup_failure_counts.items())),
        "top_setup_install_failure": str(
            context.get("top_setup_install_failure") or ""
        ),
        "setup_doctor_blocker_counts": dict(
            sorted(setup_doctor_blocker_counts.items())
        ),
        "top_setup_doctor_blocker": top_setup_doctor_blocker,
        "setup_doctor_next_action": str(
            context.get("setup_doctor_next_action") or ""
        ),
        "run_repair_contexts": run_repair_contexts,
        "run_repair_context_count": len(run_repair_contexts),
        "setup_repair_run_names": sorted(
            name
            for name, value in run_repair_contexts.items()
            if bool(_dict(value).get("setup_repair", False))
        ),
        "checkout_only_run_names": sorted(
            name
            for name, value in run_repair_contexts.items()
            if value and not bool(_dict(value).get("setup_repair", False))
        ),
        "setup_repair_defaults_applied": bool(
            global_setup_repair
            or any(
                bool(_dict(value).get("setup_repair", False))
                for value in run_repair_contexts.values()
            )
        ),
        "setup_repair_next_action": str(
            context.get("setup_repair_next_action")
            or (
                context.get("setup_doctor_next_action")
                if global_setup_repair
                else ""
            )
            or ""
        ),
    }
    return payload


def _repository_repair_manifest_context(summary: dict[str, Any]) -> dict[str, Any]:
    setup_failure_counts = _dict(
        summary.get("repository_test_environment_setup_install_failure_counts")
    )
    setup_doctor_blocker_counts = _repository_test_setup_doctor_blocker_counts(
        summary
    )
    top_setup_failure = _top_count_key(setup_failure_counts)
    top_setup_doctor_blocker = _top_count_key(setup_doctor_blocker_counts)
    actionable_setup_doctor_blocker = (
        top_setup_doctor_blocker
        if _repository_test_setup_doctor_manifest_recommended(
            top_setup_doctor_blocker
        )
        else ""
    )
    setup_doctor_next_action = _repository_test_setup_doctor_blocker_action(
        actionable_setup_doctor_blocker
    )
    run_repair_contexts = _repository_repair_manifest_run_contexts(summary)
    return {
        "setup_install_failure_counts": setup_failure_counts,
        "top_setup_install_failure": top_setup_failure,
        "setup_doctor_blocker_counts": setup_doctor_blocker_counts,
        "top_setup_doctor_blocker": top_setup_doctor_blocker,
        "setup_doctor_next_action": setup_doctor_next_action,
        "run_repair_contexts": run_repair_contexts,
        "setup_repair_next_action": (
            _repository_repair_setup_failure_action(top_setup_failure)
            if top_setup_failure
            else (
                setup_doctor_next_action
                if _repository_test_setup_doctor_requires_setup_repair(
                    actionable_setup_doctor_blocker
                )
                else ""
            )
        ),
    }


def _repository_repair_manifest_run_contexts(
    summary: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    for run_value in _list(summary.get("repository_test_run_summaries")):
        run_summary = _dict(run_value)
        name = str(run_summary.get("name") or "").strip()
        if not name:
            continue
        context = _repository_repair_manifest_run_context(run_summary)
        if context:
            contexts[name] = context
    return contexts


def _repository_repair_manifest_run_context(
    run_summary: dict[str, Any],
) -> dict[str, Any]:
    setup_failure = str(run_summary.get("setup_install_failure_category") or "")
    if setup_failure:
        blocker = f"setup_install_failure:{setup_failure}"
        return {
            "blocker": blocker,
            "setup_repair": True,
            "next_action": _repository_repair_setup_failure_action(setup_failure),
        }
    setup_doctor_blocker = str(run_summary.get("setup_doctor_blocker") or "")
    if _repository_test_setup_doctor_manifest_recommended(setup_doctor_blocker):
        return {
            "blocker": setup_doctor_blocker,
            "setup_repair": _repository_test_setup_doctor_requires_setup_repair(
                setup_doctor_blocker
            ),
            "next_action": (
                str(run_summary.get("setup_doctor_next_action") or "")
                or _repository_test_setup_doctor_blocker_action(
                    setup_doctor_blocker
                )
            ),
        }
    final_reason = str(run_summary.get("final_reason") or "")
    if final_reason.startswith("dynamic_evidence_not_usable:not_executed"):
        return {
            "blocker": final_reason,
            "setup_repair": False,
            "next_action": (
                "Enable --checkout-repository-tests or provide "
                "--repository-test-root, then rerun the pipeline."
            ),
        }
    return {}


def _repository_test_setup_doctor_blocker_counts(
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _dict(payload.get("repository_test_setup_doctor_blocker_counts")) or _dict(
        payload.get("setup_doctor_blocker_counts")
    )


def _repository_test_setup_doctor_status_counts(
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _dict(payload.get("repository_test_setup_doctor_status_counts")) or _dict(
        payload.get("setup_doctor_status_counts")
    )


def _repository_test_setup_doctor_manifest_recommended(blocker: str) -> bool:
    if not blocker or blocker == "none":
        return False
    if blocker.startswith("checkout:"):
        return True
    return _repository_test_setup_doctor_requires_setup_repair(blocker)


def _repository_test_setup_doctor_requires_setup_repair(blocker: str) -> bool:
    if not blocker or blocker == "none":
        return False
    if blocker.startswith("setup_install_failure:"):
        return True
    if blocker in {
        "environment:test_tool_missing",
        "execution_plan:planned_runner_not_prepared",
        "execution_plan:test_environment_warning",
        "execution_failure:missing_test_runner",
        "execution_failure:missing_dependency",
        "execution_failure:tox_missing_python_interpreter",
    }:
        return True
    return False


def _repository_test_setup_doctor_blocker_action(blocker: str) -> str:
    if not blocker or blocker == "none":
        return ""
    if blocker.startswith("checkout:"):
        return (
            "Enable --checkout-repository-tests or provide --repository-test-root, "
            "then rerun the pipeline."
        )
    if blocker.startswith("setup_install_failure:"):
        return _repository_repair_setup_failure_action(
            blocker.split(":", 1)[1]
        )
    if blocker == "test_command:no_recommended_test_command":
        return (
            "Add or infer a repository test command from project config or CI, "
            "then rerun repo onboarding."
        )
    if blocker == "test_command:unsupported_command":
        return "Use a safe python -m module style repository test command."
    if blocker == "environment:test_tool_missing":
        return (
            "Run repository-test environment setup or install the missing test "
            "runner, then rerun the planned command."
        )
    if blocker == "execution_plan:planned_runner_not_prepared":
        return (
            "Prepare the planned test runner through repository-test environment "
            "setup, then rerun the pipeline."
        )
    if blocker == "execution_plan:test_environment_warning":
        return "Fix repository-test environment warnings or run setup before execution."
    if blocker == "execution_failure:missing_test_runner":
        return "Install the selected test runner and rerun repository tests."
    if blocker == "execution_failure:missing_dependency":
        return "Install missing repository test dependencies and rerun repository tests."
    if blocker == "execution_failure:tox_missing_python_interpreter":
        return (
            "Install the tox target Python interpreter or use the pytest fallback "
            "command from the retry plan."
        )
    if blocker.startswith("dynamic_evidence:"):
        return "Inspect repository_test_dynamic_evidence and retry or overlay outputs."
    return f"Inspect repository_test_setup_doctor blocker `{blocker}`."


def _repository_test_setup_doctor_blocker_layer(blocker: str) -> str:
    if blocker.startswith(("execution_failure:", "execution_result:")):
        return "repository_test_execution"
    if blocker.startswith("dynamic_evidence:"):
        return "repository_test_execution"
    return "repository_test_setup"


def _repository_repair_manifest_run_eligible(run: dict[str, Any]) -> bool:
    if run.get("repo_spec"):
        return True
    return str(run.get("mode") or "") == "repo" and bool(run.get("repo"))


def _apply_repository_repair_run_defaults(
    run: dict[str, Any],
    *,
    setup_repair: bool = False,
) -> list[str]:
    changed_fields: list[str] = []
    if run.get("checkout_repository_tests") is not True:
        run["checkout_repository_tests"] = True
        changed_fields.append("checkout_repository_tests")
    if run.pop("no_repository_test_command", None) is not None:
        changed_fields.append("no_repository_test_command")
    if setup_repair:
        _append_if_changed(
            changed_fields,
            "run_repository_test_environment_setup",
            _set_missing(run, "run_repository_test_environment_setup", True),
        )
        _append_if_changed(
            changed_fields,
            "run_repository_test_retry_prerequisites",
            _set_missing(run, "run_repository_test_retry_prerequisites", True),
        )
    _append_if_changed(
        changed_fields,
        "auto_scoped_include",
        _set_missing(run, "auto_scoped_include", True),
    )
    _append_if_changed(
        changed_fields,
        "auto_fallback",
        _set_missing(run, "auto_fallback", True),
    )
    thresholds = dict(_dict(run.get("thresholds")))
    if _int(thresholds.get("min_generated_candidates", 0)) < 1:
        thresholds["min_generated_candidates"] = 1
        run["thresholds"] = thresholds
        changed_fields.append("thresholds.min_generated_candidates")
    fallback = dict(_dict(run.get("fallback")))
    _append_if_changed(
        changed_fields,
        "fallback.enabled",
        _set_missing(fallback, "enabled", True),
    )
    _append_if_changed(
        changed_fields,
        "fallback.max_sources",
        _set_missing(
            fallback,
            "max_sources",
            REPO_REPAIR_PROFILE_FALLBACK_MAX_SOURCES,
        ),
    )
    _append_if_changed(
        changed_fields,
        "fallback.max_candidates",
        _set_missing(
            fallback,
            "max_candidates",
            REPO_REPAIR_PROFILE_FALLBACK_MAX_CANDIDATES,
        ),
    )
    run["fallback"] = fallback
    _append_if_changed(
        changed_fields,
        "repository_test_timeout",
        _set_missing(run, "repository_test_timeout", 20),
    )
    _append_if_changed(
        changed_fields,
        "repository_test_failure_overlay_candidate_limit",
        _set_missing(
            run,
            "repository_test_failure_overlay_candidate_limit",
            REPO_REPAIR_PROFILE_OVERLAY_CANDIDATE_LIMIT,
        ),
    )
    _append_if_changed(
        changed_fields,
        "repository_test_reflection_mode",
        _set_missing(run, "repository_test_reflection_mode", "rule"),
    )
    _append_if_changed(
        changed_fields,
        "repository_test_reflection_rounds",
        _set_missing(run, "repository_test_reflection_rounds", 1),
    )
    _append_if_changed(
        changed_fields,
        "repository_test_reflection_width",
        _set_missing(run, "repository_test_reflection_width", 1),
    )
    _append_if_changed(
        changed_fields,
        "repository_checkout_depth",
        _set_missing(run, "repository_checkout_depth", 1),
    )
    return changed_fields


def _append_if_changed(
    changed_fields: list[str],
    field: str,
    changed: bool,
) -> None:
    if changed:
        changed_fields.append(field)


def _repository_repair_manifest_metadata(path: str | Path) -> dict[str, Any]:
    payload = _read_json(path)
    return _dict(payload.get("repository_repair_recommendation_metadata"))


def _set_missing(payload: dict[str, Any], key: str, value: Any) -> bool:
    if payload.get(key) is None:
        payload[key] = value
        return True
    return False


def _build_recommended_pipeline_manifest(
    recommended_smoke_manifest: dict[str, Any],
    *,
    original_manifest_payload: dict[str, Any],
    source_manifest_dir: str | Path,
    target_manifest_dir: str | Path,
) -> dict[str, Any]:
    source_suite_name = str(
        recommended_smoke_manifest.get("suite_name")
        or original_manifest_payload.get("suite_name")
        or "github_onboarding"
    )
    runs = [
        _pipeline_run_from_smoke_run(
            _dict(run),
            source_manifest_dir=source_manifest_dir,
            target_manifest_dir=target_manifest_dir,
        )
        for run in _list(recommended_smoke_manifest.get("runs"))
    ]
    payload: dict[str, Any] = {
        "suite_name": f"{source_suite_name}_recommended_pipeline",
        "description": (
            "Generated by github_onboarding_pipeline from the smoke runner "
            "recommended manifest. This can be passed back to "
            "github_onboarding_pipeline for a full preflight + smoke rerun."
        ),
        "runs": runs,
    }
    for key in ("thresholds", "smoke_defaults"):
        value = recommended_smoke_manifest.get(key)
        if isinstance(value, dict) and value:
            payload[key] = value
    promotion_gate = _dict(original_manifest_payload.get("promotion_gate"))
    if promotion_gate:
        payload["promotion_gate"] = promotion_gate
    metadata = _dict(recommended_smoke_manifest.get("recommendation_metadata"))
    payload["recommendation_metadata"] = {
        **metadata,
        "source": "github_onboarding_pipeline",
        "source_smoke_manifest": str(Path(source_manifest_dir)),
        "pipeline_manifest_kind": "recommended_pipeline_manifest",
    }
    return payload


def _pipeline_run_from_smoke_run(
    run: dict[str, Any],
    *,
    source_manifest_dir: str | Path,
    target_manifest_dir: str | Path,
) -> dict[str, Any]:
    converted = dict(run)
    if "sample_sources" not in converted:
        max_sources = _int(converted.get("max_sources", 0))
        if max_sources > 0:
            converted["sample_sources"] = max_sources
    return _rebase_discovery_paths(
        converted,
        source_manifest_dir=source_manifest_dir,
        target_manifest_dir=target_manifest_dir,
    )


def _rebase_discovery_paths(
    run: dict[str, Any],
    *,
    source_manifest_dir: str | Path,
    target_manifest_dir: str | Path,
) -> dict[str, Any]:
    source_dir = Path(source_manifest_dir)
    target_dir = Path(target_manifest_dir)
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


def _write_recommendation_comparison_artifacts(
    comparison: OnboardingRecommendationComparison,
    json_path: str,
    markdown_path: str,
) -> None:
    Path(json_path).write_text(
        json.dumps(comparison.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(markdown_path).write_text(
        render_onboarding_recommendation_comparison_markdown(comparison),
        encoding="utf-8",
    )


def _pipeline_summary(
    *,
    preflight: GitHubOnboardingPreflightBatchReport,
    smoke: OnboardingSmokeRunnerReport | None,
    recommended_smoke: OnboardingSmokeRunnerReport | None,
    comparison: OnboardingRecommendationComparison | None,
    recommendation_rerun_enabled: bool,
) -> dict[str, Any]:
    smoke_summary = _dict(smoke.summary if smoke is not None else {})
    recommended_summary = _dict(
        recommended_smoke.summary if recommended_smoke is not None else {}
    )
    comparison_summary = _dict(comparison.summary if comparison is not None else {})
    repository_test_readiness = _pipeline_repository_test_readiness(smoke)
    benchmark_repair = _pipeline_benchmark_repair_summary(smoke)
    return {
        "preflight_passed": preflight.passed,
        "preflight_run_count": _int(preflight.summary.get("run_count", 0)),
        "preflight_ready_count": _int(preflight.summary.get("ready_count", 0)),
        "preflight_skipped_count": _int(preflight.summary.get("skipped_count", 0)),
        "preflight_error_count": _int(preflight.summary.get("error_count", 0)),
        "preflight_readiness_rate": _float(
            preflight.summary.get("readiness_rate", 0.0)
        ),
        "preflight_ready_run_names": _list(
            preflight.summary.get("ready_run_names")
        ),
        "preflight_skipped_run_names": _list(
            preflight.summary.get("skipped_run_names")
        ),
        "preflight_error_run_names": _list(
            preflight.summary.get("error_run_names")
        ),
        "preflight_top_issue_code": str(
            preflight.summary.get("top_issue_code") or ""
        ),
        "preflight_profile_doctor_status_counts": _dict(
            preflight.summary.get("profile_doctor_status_counts")
        ),
        "preflight_profile_doctor_blocker_counts": _dict(
            preflight.summary.get("profile_doctor_blocker_counts")
        ),
        "preflight_top_profile_doctor_blocker": str(
            preflight.summary.get("top_profile_doctor_blocker") or ""
        ),
        "smoke_present": smoke is not None,
        "smoke_passed": bool(smoke.passed) if smoke is not None else False,
        "smoke_run_count": _int(smoke_summary.get("run_count", 0)),
        "smoke_generated_candidates": _int(
            smoke_summary.get("generated_candidates", 0)
        ),
        "smoke_benchmark_cases": _int(smoke_summary.get("benchmark_cases", 0)),
        **_pipeline_static_intelligence_summary(smoke_summary, prefix="smoke"),
        **_pipeline_benchmarkization_summary(smoke_summary, prefix="smoke"),
        **benchmark_repair,
        **_pipeline_auto_remediation_summary(
            smoke_summary,
            prefix="smoke",
        ),
        "smoke_fallback_attempted_count": _int(
            smoke_summary.get("fallback_attempted_count", 0)
        ),
        "smoke_fallback_improved_count": _int(
            smoke_summary.get("fallback_improved_count", 0)
        ),
        "smoke_fallback_recovered_count": _int(
            smoke_summary.get("fallback_recovered_count", 0)
        ),
        "smoke_manifest_recommendations": _int(
            smoke_summary.get("manifest_recommendation_count", 0)
        ),
        "recommendation_rerun_enabled": recommendation_rerun_enabled,
        "recommendation_applied_count": (
            _recommended_manifest_applied_count(smoke.recommended_manifest_path)
            if smoke is not None
            else 0
        ),
        "recommendation_rerun_present": recommended_smoke is not None,
        "recommendation_rerun_passed": (
            bool(recommended_smoke.passed) if recommended_smoke is not None else False
        ),
        "recommended_smoke_run_count": _int(recommended_summary.get("run_count", 0)),
        "recommended_smoke_generated_candidates": _int(
            recommended_summary.get("generated_candidates", 0)
        ),
        "recommended_smoke_benchmark_cases": _int(
            recommended_summary.get("benchmark_cases", 0)
        ),
        **_pipeline_static_intelligence_summary(
            recommended_summary,
            prefix="recommended_smoke",
        ),
        **_pipeline_benchmarkization_summary(
            recommended_summary,
            prefix="recommended_smoke",
        ),
        "recommended_smoke_fallback_attempted_count": _int(
            recommended_summary.get("fallback_attempted_count", 0)
        ),
        "recommended_smoke_fallback_improved_count": _int(
            recommended_summary.get("fallback_improved_count", 0)
        ),
        "recommended_smoke_fallback_recovered_count": _int(
            recommended_summary.get("fallback_recovered_count", 0)
        ),
        **_pipeline_auto_remediation_summary(
            recommended_summary,
            prefix="recommended_smoke",
        ),
        "recommendation_comparison_present": comparison is not None,
        "recommendation_comparison_passed": (
            bool(comparison.passed) if comparison is not None else False
        ),
        "recommendation_regression_count": (
            len(comparison.regressions) if comparison is not None else 0
        ),
        "recommendation_improved_run_count": _int(
            comparison_summary.get("improved_run_count", 0)
        ),
        "recommendation_regressed_run_count": _int(
            comparison_summary.get("regressed_run_count", 0)
        ),
        "recommendation_candidate_delta": _int(
            comparison_summary.get("candidate_delta", 0)
        ),
        "recommendation_benchmark_case_delta": _int(
            comparison_summary.get("benchmark_case_delta", 0)
        ),
        "recommendation_fallback_recovered_delta": (
            _int(comparison_summary.get("recommended_fallback_recovered_count", 0))
            - _int(comparison_summary.get("baseline_fallback_recovered_count", 0))
        ),
        "recommendation_fallback_recovery_resolved_count": max(
            0,
            _int(comparison_summary.get("baseline_fallback_recovered_count", 0))
            - _int(comparison_summary.get("recommended_fallback_recovered_count", 0)),
        ),
        "recommendation_validation_pass_rate_delta": _float(
            comparison_summary.get("validation_pass_rate_delta", 0.0)
        ),
        "recommendation_status": _recommendation_status(
            comparison=comparison,
            comparison_summary=comparison_summary,
        ),
        **repository_test_readiness,
    }


def _pipeline_summary_with_promotion_gate(
    summary: dict[str, Any],
    pipeline_showcase: dict[str, Any],
) -> dict[str, Any]:
    promotion_gate = _dict(pipeline_showcase.get("promotion_gate"))
    stage_audit = [_dict(stage) for stage in _list(pipeline_showcase.get("stage_audit"))]
    stage_statuses = {
        str(stage.get("stage")): str(stage.get("status") or "")
        for stage in stage_audit
        if str(stage.get("stage") or "").strip()
    }
    stage_next_actions = {
        str(stage.get("stage")): str(stage.get("next_action") or "")
        for stage in stage_audit
        if str(stage.get("stage") or "").strip()
    }
    stage_evidence = {
        str(stage.get("stage")): str(stage.get("evidence") or "")
        for stage in stage_audit
        if str(stage.get("stage") or "").strip()
    }
    repository_repair_stage = _dict(
        next(
            (
                stage
                for stage in stage_audit
                if str(stage.get("stage") or "") == "repository_repair"
            ),
            {},
        )
    )
    promoted = dict(summary)
    promoted.update(
        {
            "promotion_status": str(promotion_gate.get("status") or ""),
            "promotion_promotable": bool(
                promotion_gate.get("promotable", False)
            ),
            "promotion_blocking_reasons": _list(
                promotion_gate.get("blocking_reasons")
            ),
            "promotion_warning_reasons": _list(
                promotion_gate.get("warning_reasons")
            ),
            "promotion_criteria": _dict(promotion_gate.get("criteria")),
            "promotion_config": _dict(promotion_gate.get("config")),
            "pipeline_stage_statuses": stage_statuses,
            "pipeline_stage_next_actions": stage_next_actions,
            "pipeline_stage_evidence": stage_evidence,
            "repository_repair_stage_status": str(
                repository_repair_stage.get("status") or ""
            ),
            "repository_repair_stage_evidence": str(
                repository_repair_stage.get("evidence") or ""
            ),
            "repository_repair_stage_next_action": str(
                repository_repair_stage.get("next_action") or ""
            ),
        }
    )
    return promoted


def _pipeline_benchmark_repair_summary(
    smoke: OnboardingSmokeRunnerReport | None,
) -> dict[str, Any]:
    if smoke is None:
        return {
            "smoke_benchmark_report_count": 0,
            "smoke_benchmark_patch_success_run_count": 0,
            "smoke_benchmark_patch_success_case_count": 0.0,
            "smoke_benchmark_patch_success_rate": 0.0,
            "smoke_benchmark_top1": 0.0,
            "smoke_benchmark_map": 0.0,
        }
    report_count = 0
    total_cases = 0
    patch_success_cases = 0.0
    weighted_top1 = 0.0
    weighted_map = 0.0
    patch_success_runs = 0
    for run in smoke.runs:
        payload = _read_json(run.report_path)
        benchmark_summary = _dict(_dict(payload.get("benchmark_run")).get("summary"))
        case_count = _int(benchmark_summary.get("case_count", 0))
        if case_count <= 0:
            continue
        report_count += 1
        total_cases += case_count
        patch_success_rate = _float(
            benchmark_summary.get("patch_success_rate", 0.0)
        )
        patch_success_cases += patch_success_rate * case_count
        weighted_top1 += _float(benchmark_summary.get("top1", 0.0)) * case_count
        weighted_map += _float(benchmark_summary.get("map", 0.0)) * case_count
        if patch_success_rate > 0.0:
            patch_success_runs += 1
    return {
        "smoke_benchmark_report_count": report_count,
        "smoke_benchmark_patch_success_run_count": patch_success_runs,
        "smoke_benchmark_patch_success_case_count": round(patch_success_cases, 4),
        "smoke_benchmark_patch_success_rate": _ratio(
            patch_success_cases,
            total_cases,
        ),
        "smoke_benchmark_top1": _ratio(weighted_top1, total_cases),
        "smoke_benchmark_map": _ratio(weighted_map, total_cases),
    }


def _pipeline_auto_remediation_summary(
    smoke_summary: dict[str, Any],
    *,
    prefix: str,
) -> dict[str, Any]:
    action_runs = {
        str(action): _list(names)
        for action, names in _dict(
            smoke_summary.get("auto_remediation_action_runs")
        ).items()
    }
    return {
        f"{prefix}_auto_remediation_attempted_count": _int(
            smoke_summary.get("auto_remediation_attempted_count", 0)
        ),
        f"{prefix}_auto_remediation_used_count": _int(
            smoke_summary.get("auto_remediation_used_count", 0)
        ),
        f"{prefix}_auto_remediation_improved_count": _int(
            smoke_summary.get("auto_remediation_improved_count", 0)
        ),
        f"{prefix}_auto_remediation_attempted_runs": _list(
            smoke_summary.get("auto_remediation_attempted_runs")
        ),
        f"{prefix}_auto_remediation_used_runs": _list(
            smoke_summary.get("auto_remediation_used_runs")
        ),
        f"{prefix}_auto_remediation_improved_runs": _list(
            smoke_summary.get("auto_remediation_improved_runs")
        ),
        f"{prefix}_auto_remediation_action_counts": _dict(
            smoke_summary.get("auto_remediation_action_counts")
        ),
        f"{prefix}_auto_remediation_action_runs": action_runs,
        f"{prefix}_auto_remediation_benchmark_case_delta": _int(
            smoke_summary.get("auto_remediation_benchmark_case_delta", 0)
        ),
    }


def _pipeline_benchmarkization_summary(
    smoke_summary: dict[str, Any],
    *,
    prefix: str,
) -> dict[str, Any]:
    return {
        f"{prefix}_benchmarkization_ready_count": _int(
            smoke_summary.get("benchmarkization_ready_count", 0)
        ),
        f"{prefix}_benchmarkization_ready_runs": _list(
            smoke_summary.get("benchmarkization_ready_runs")
        ),
        f"{prefix}_benchmarkization_status_counts": _dict(
            smoke_summary.get("benchmarkization_status_counts")
        ),
        f"{prefix}_benchmarkization_status_runs": _dict(
            smoke_summary.get("benchmarkization_status_runs")
        ),
        f"{prefix}_benchmarkization_stage_counts": _dict(
            smoke_summary.get("benchmarkization_stage_counts")
        ),
        f"{prefix}_benchmarkization_stage_runs": _dict(
            smoke_summary.get("benchmarkization_stage_runs")
        ),
        f"{prefix}_benchmarkization_primary_action_counts": _dict(
            smoke_summary.get("benchmarkization_primary_action_counts")
        ),
        f"{prefix}_benchmarkization_primary_action_runs": _dict(
            smoke_summary.get("benchmarkization_primary_action_runs")
        ),
        f"{prefix}_benchmarkization_auto_runnable_action_count": _int(
            smoke_summary.get("benchmarkization_auto_runnable_action_count", 0)
        ),
        f"{prefix}_benchmarkization_manual_action_count": _int(
            smoke_summary.get("benchmarkization_manual_action_count", 0)
        ),
        f"{prefix}_benchmarkization_remediation_plan_count": _int(
            smoke_summary.get("benchmarkization_remediation_plan_count", 0)
        ),
        f"{prefix}_benchmarkization_remediation_plan_runs": _dict(
            smoke_summary.get("benchmarkization_remediation_plan_runs")
        ),
    }


def _pipeline_static_intelligence_summary(
    smoke_summary: dict[str, Any],
    *,
    prefix: str,
) -> dict[str, Any]:
    return {
        f"{prefix}_static_intelligence_run_count": _int(
            smoke_summary.get("static_intelligence_run_count", 0)
        ),
        f"{prefix}_static_intelligence_analysis_ready_count": _int(
            smoke_summary.get("static_intelligence_analysis_ready_count", 0)
        ),
        f"{prefix}_static_intelligence_source_inventory_ready_count": _int(
            smoke_summary.get(
                "static_intelligence_source_inventory_ready_count",
                0,
            )
        ),
        f"{prefix}_static_intelligence_blocked_count": _int(
            smoke_summary.get("static_intelligence_blocked_count", 0)
        ),
        f"{prefix}_static_intelligence_selected_signal_count": _int(
            smoke_summary.get("static_intelligence_selected_signal_count", 0)
        ),
        f"{prefix}_static_intelligence_total_signal_count": _int(
            smoke_summary.get("static_intelligence_total_signal_count", 0)
        ),
        f"{prefix}_static_intelligence_candidate_limit_applied_count": _int(
            smoke_summary.get(
                "static_intelligence_candidate_limit_applied_count",
                0,
            )
        ),
        f"{prefix}_static_intelligence_average_quality_score": _float(
            smoke_summary.get("static_intelligence_average_quality_score", 0.0)
        ),
        f"{prefix}_static_intelligence_status_counts": _dict(
            smoke_summary.get("static_intelligence_status_counts")
        ),
        f"{prefix}_static_intelligence_status_runs": _dict(
            smoke_summary.get("static_intelligence_status_runs")
        ),
        f"{prefix}_static_intelligence_level_counts": _dict(
            smoke_summary.get("static_intelligence_level_counts")
        ),
        f"{prefix}_static_intelligence_level_runs": _dict(
            smoke_summary.get("static_intelligence_level_runs")
        ),
        f"{prefix}_static_intelligence_reason_counts": _dict(
            smoke_summary.get("static_intelligence_reason_counts")
        ),
        f"{prefix}_static_intelligence_reason_runs": _dict(
            smoke_summary.get("static_intelligence_reason_runs")
        ),
        f"{prefix}_static_intelligence_rule_counts": _dict(
            smoke_summary.get("static_intelligence_rule_counts")
        ),
        f"{prefix}_static_intelligence_bug_type_counts": _dict(
            smoke_summary.get("static_intelligence_bug_type_counts")
        ),
        f"{prefix}_static_intelligence_dynamic_validation_level_counts": _dict(
            smoke_summary.get("static_intelligence_dynamic_validation_level_counts")
        ),
        f"{prefix}_static_intelligence_dynamic_validation_level_runs": _dict(
            smoke_summary.get("static_intelligence_dynamic_validation_level_runs")
        ),
        f"{prefix}_static_intelligence_primary_artifact_runs": _dict(
            smoke_summary.get("static_intelligence_primary_artifact_runs")
        ),
        f"{prefix}_static_intelligence_run_summaries": _list(
            smoke_summary.get("static_intelligence_run_summaries")
        ),
    }


def _pipeline_repository_test_readiness(
    smoke: OnboardingSmokeRunnerReport | None,
) -> dict[str, Any]:
    analysis_source_counts: dict[str, int] = {}
    analysis_source_runs: dict[str, list[str]] = {}
    overlay_reason_counts: dict[str, int] = {}
    overlay_reason_runs: dict[str, list[str]] = {}
    execution_status_counts: dict[str, int] = {}
    execution_status_runs: dict[str, list[str]] = {}
    execution_command_runs: dict[str, list[str]] = {}
    failure_category_counts: dict[str, int] = {}
    failure_category_runs: dict[str, list[str]] = {}
    setup_install_failure_counts: dict[str, int] = {}
    setup_install_failure_runs: dict[str, list[str]] = {}
    setup_install_fallback_count = 0
    setup_install_fallback_success_count = 0
    setup_doctor_status_counts: dict[str, int] = {}
    setup_doctor_status_runs: dict[str, list[str]] = {}
    setup_doctor_blocker_counts: dict[str, int] = {}
    setup_doctor_blocker_runs: dict[str, list[str]] = {}
    fault_status_counts: dict[str, int] = {}
    fault_status_runs: dict[str, list[str]] = {}
    top_function_counts: dict[str, int] = {}
    top_function_runs: dict[str, list[str]] = {}
    patch_status_counts: dict[str, int] = {}
    patch_status_runs: dict[str, list[str]] = {}
    repair_summary_status_counts: dict[str, int] = {}
    repair_summary_status_runs: dict[str, list[str]] = {}
    repair_summary_reason_counts: dict[str, int] = {}
    repair_summary_reason_runs: dict[str, list[str]] = {}
    repair_summary_conclusion_counts: dict[str, int] = {}
    repair_summary_conclusion_runs: dict[str, list[str]] = {}
    final_status_counts: dict[str, int] = {}
    final_status_runs: dict[str, list[str]] = {}
    blocked_reason_counts: dict[str, int] = {}
    blocked_reason_runs: dict[str, list[str]] = {}
    run_summaries: list[dict[str, Any]] = []
    phase2_ready_runs: list[str] = []
    phase3_ready_runs: list[str] = []
    patch_success_runs: list[str] = []
    patch_success_count = 0
    report_count = 0
    if smoke is None:
        return _repository_test_readiness_payload(
            report_count=0,
            analysis_source_counts=analysis_source_counts,
            analysis_source_runs=analysis_source_runs,
            overlay_reason_counts=overlay_reason_counts,
            overlay_reason_runs=overlay_reason_runs,
            execution_status_counts=execution_status_counts,
            execution_status_runs=execution_status_runs,
            execution_command_runs=execution_command_runs,
            failure_category_counts=failure_category_counts,
            failure_category_runs=failure_category_runs,
            setup_install_failure_counts=setup_install_failure_counts,
            setup_install_failure_runs=setup_install_failure_runs,
            setup_install_fallback_count=setup_install_fallback_count,
            setup_install_fallback_success_count=(
                setup_install_fallback_success_count
            ),
            setup_doctor_status_counts=setup_doctor_status_counts,
            setup_doctor_status_runs=setup_doctor_status_runs,
            setup_doctor_blocker_counts=setup_doctor_blocker_counts,
            setup_doctor_blocker_runs=setup_doctor_blocker_runs,
            fault_status_counts=fault_status_counts,
            fault_status_runs=fault_status_runs,
            top_function_counts=top_function_counts,
            top_function_runs=top_function_runs,
            patch_status_counts=patch_status_counts,
            patch_status_runs=patch_status_runs,
            repair_summary_status_counts=repair_summary_status_counts,
            repair_summary_status_runs=repair_summary_status_runs,
            repair_summary_reason_counts=repair_summary_reason_counts,
            repair_summary_reason_runs=repair_summary_reason_runs,
            repair_summary_conclusion_counts=repair_summary_conclusion_counts,
            repair_summary_conclusion_runs=repair_summary_conclusion_runs,
            final_status_counts=final_status_counts,
            final_status_runs=final_status_runs,
            blocked_reason_counts=blocked_reason_counts,
            blocked_reason_runs=blocked_reason_runs,
            run_summaries=run_summaries,
            phase2_ready_runs=phase2_ready_runs,
            phase3_ready_runs=phase3_ready_runs,
            patch_success_runs=patch_success_runs,
            patch_success_count=patch_success_count,
        )

    for run in smoke.runs:
        payload = _read_json(run.report_path)
        if not payload:
            continue
        route = _repository_test_route_from_onboarding_report(payload)
        if not route:
            continue
        report_count += 1
        analysis_source = str(route.get("analysis_source") or "none")
        _count_name(analysis_source_counts, analysis_source)
        analysis_source_runs.setdefault(analysis_source, []).append(run.name)
        overlay_reason = str(route.get("overlay_trigger_reason") or "")
        if overlay_reason:
            _count_name(overlay_reason_counts, overlay_reason)
            overlay_reason_runs.setdefault(overlay_reason, []).append(run.name)
        if bool(route.get("phase2_ready", False)):
            phase2_ready_runs.append(run.name)
        if bool(route.get("phase3_validation_ready", False)):
            phase3_ready_runs.append(run.name)
        test_details = _repository_test_details_from_onboarding_report(payload)
        _add_counted_run(
            execution_status_counts,
            execution_status_runs,
            test_details["execution_status"],
            run.name,
            skip_values={"", "none"},
        )
        _add_mapped_run(
            execution_command_runs,
            test_details["execution_command"],
            run.name,
            skip_values={"", "none"},
        )
        _add_counted_run(
            failure_category_counts,
            failure_category_runs,
            test_details["failure_category"],
            run.name,
            skip_values={"", "none"},
        )
        _add_counted_run(
            setup_install_failure_counts,
            setup_install_failure_runs,
            test_details["setup_install_failure_category"],
            run.name,
            skip_values={"", "none"},
        )
        if bool(test_details.get("setup_install_fallback_executed", False)):
            setup_install_fallback_count += 1
            if _int(test_details.get("setup_install_fallback_returncode", -1)) == 0:
                setup_install_fallback_success_count += 1
        _add_counted_run(
            setup_doctor_status_counts,
            setup_doctor_status_runs,
            test_details["setup_doctor_status"],
            run.name,
            skip_values={"", "none"},
        )
        _add_counted_run(
            setup_doctor_blocker_counts,
            setup_doctor_blocker_runs,
            test_details["setup_doctor_blocker"],
            run.name,
            skip_values={"", "none"},
        )
        _add_counted_run(
            fault_status_counts,
            fault_status_runs,
            test_details["fault_localization_status"],
            run.name,
            skip_values={"", "none"},
        )
        _add_counted_run(
            top_function_counts,
            top_function_runs,
            test_details["top_function"],
            run.name,
            skip_values={"", "none"},
        )
        _add_counted_run(
            patch_status_counts,
            patch_status_runs,
            test_details["patch_validation_status"],
            run.name,
            skip_values={"", "none"},
        )
        _add_counted_run(
            repair_summary_status_counts,
            repair_summary_status_runs,
            test_details["repair_summary_status"],
            run.name,
            skip_values={"", "none"},
        )
        _add_counted_run(
            repair_summary_reason_counts,
            repair_summary_reason_runs,
            test_details["repair_summary_reason"],
            run.name,
            skip_values={"", "none"},
        )
        _add_counted_run(
            repair_summary_conclusion_counts,
            repair_summary_conclusion_runs,
            test_details["repair_summary_conclusion"],
            run.name,
            skip_values={"", "none"},
        )
        success_count = _int(test_details["patch_validation_success_count"])
        patch_success_count += success_count
        if success_count > 0:
            patch_success_runs.append(run.name)
        final_diagnosis = _repository_test_final_diagnosis(route, test_details)
        final_status = str(final_diagnosis["final_status"])
        final_reason = str(final_diagnosis["final_reason"])
        _add_counted_run(
            final_status_counts,
            final_status_runs,
            final_status,
            run.name,
        )
        if final_status == "blocked":
            _add_counted_run(
                blocked_reason_counts,
                blocked_reason_runs,
                final_reason,
                run.name,
            )
        run_summaries.append(
            {
                "name": run.name,
                "final_status": final_status,
                "final_reason": final_reason,
                "analysis_source": analysis_source,
                "execution_status": test_details["execution_status"],
                "execution_command": test_details["execution_command"],
                "failure_category": test_details["failure_category"],
                "dynamic_failure_category": test_details[
                    "dynamic_failure_category"
                ],
                "setup_install_failure_category": test_details[
                    "setup_install_failure_category"
                ],
                "setup_install_failure_signal": test_details[
                    "setup_install_failure_signal"
                ],
                "setup_install_fallback_executed": bool(
                    test_details.get("setup_install_fallback_executed", False)
                ),
                "setup_doctor_status": test_details["setup_doctor_status"],
                "setup_doctor_blocker": test_details["setup_doctor_blocker"],
                "setup_doctor_next_action": test_details[
                    "setup_doctor_next_action"
                ],
                "phase2_ready": bool(route.get("phase2_ready", False)),
                "phase3_validation_ready": bool(
                    route.get("phase3_validation_ready", False)
                ),
                "top_function": test_details["top_function"],
                "patch_validation_status": test_details[
                    "patch_validation_status"
                ],
                "patch_validation_success_count": success_count,
                "repair_summary_status": test_details["repair_summary_status"],
                "repair_summary_reason": test_details["repair_summary_reason"],
                "repair_summary_conclusion": test_details[
                    "repair_summary_conclusion"
                ],
                "repair_summary_path": test_details["repair_summary_path"],
                "retry_recommended": bool(
                    test_details.get("retry_recommended", False)
                ),
                "retry_strategy": test_details["retry_strategy"],
                "retry_command": test_details["retry_command"],
            }
        )

    return _repository_test_readiness_payload(
        report_count=report_count,
        analysis_source_counts=analysis_source_counts,
        analysis_source_runs=analysis_source_runs,
        overlay_reason_counts=overlay_reason_counts,
        overlay_reason_runs=overlay_reason_runs,
        execution_status_counts=execution_status_counts,
        execution_status_runs=execution_status_runs,
        execution_command_runs=execution_command_runs,
        failure_category_counts=failure_category_counts,
        failure_category_runs=failure_category_runs,
        setup_install_failure_counts=setup_install_failure_counts,
        setup_install_failure_runs=setup_install_failure_runs,
        setup_install_fallback_count=setup_install_fallback_count,
        setup_install_fallback_success_count=setup_install_fallback_success_count,
        setup_doctor_status_counts=setup_doctor_status_counts,
        setup_doctor_status_runs=setup_doctor_status_runs,
        setup_doctor_blocker_counts=setup_doctor_blocker_counts,
        setup_doctor_blocker_runs=setup_doctor_blocker_runs,
        fault_status_counts=fault_status_counts,
        fault_status_runs=fault_status_runs,
        top_function_counts=top_function_counts,
        top_function_runs=top_function_runs,
        patch_status_counts=patch_status_counts,
        patch_status_runs=patch_status_runs,
        repair_summary_status_counts=repair_summary_status_counts,
        repair_summary_status_runs=repair_summary_status_runs,
        repair_summary_reason_counts=repair_summary_reason_counts,
        repair_summary_reason_runs=repair_summary_reason_runs,
        repair_summary_conclusion_counts=repair_summary_conclusion_counts,
        repair_summary_conclusion_runs=repair_summary_conclusion_runs,
        final_status_counts=final_status_counts,
        final_status_runs=final_status_runs,
        blocked_reason_counts=blocked_reason_counts,
        blocked_reason_runs=blocked_reason_runs,
        run_summaries=run_summaries,
        phase2_ready_runs=phase2_ready_runs,
        phase3_ready_runs=phase3_ready_runs,
        patch_success_runs=patch_success_runs,
        patch_success_count=patch_success_count,
    )


def _repository_test_readiness_payload(
    *,
    report_count: int,
    analysis_source_counts: dict[str, int],
    analysis_source_runs: dict[str, list[str]],
    overlay_reason_counts: dict[str, int],
    overlay_reason_runs: dict[str, list[str]],
    execution_status_counts: dict[str, int],
    execution_status_runs: dict[str, list[str]],
    execution_command_runs: dict[str, list[str]],
    failure_category_counts: dict[str, int],
    failure_category_runs: dict[str, list[str]],
    setup_install_failure_counts: dict[str, int],
    setup_install_failure_runs: dict[str, list[str]],
    setup_install_fallback_count: int,
    setup_install_fallback_success_count: int,
    setup_doctor_status_counts: dict[str, int],
    setup_doctor_status_runs: dict[str, list[str]],
    setup_doctor_blocker_counts: dict[str, int],
    setup_doctor_blocker_runs: dict[str, list[str]],
    fault_status_counts: dict[str, int],
    fault_status_runs: dict[str, list[str]],
    top_function_counts: dict[str, int],
    top_function_runs: dict[str, list[str]],
    patch_status_counts: dict[str, int],
    patch_status_runs: dict[str, list[str]],
    repair_summary_status_counts: dict[str, int],
    repair_summary_status_runs: dict[str, list[str]],
    repair_summary_reason_counts: dict[str, int],
    repair_summary_reason_runs: dict[str, list[str]],
    repair_summary_conclusion_counts: dict[str, int],
    repair_summary_conclusion_runs: dict[str, list[str]],
    final_status_counts: dict[str, int],
    final_status_runs: dict[str, list[str]],
    blocked_reason_counts: dict[str, int],
    blocked_reason_runs: dict[str, list[str]],
    run_summaries: list[dict[str, Any]],
    phase2_ready_runs: list[str],
    phase3_ready_runs: list[str],
    patch_success_runs: list[str],
    patch_success_count: int,
) -> dict[str, Any]:
    return {
        "repository_test_report_count": report_count,
        "repository_test_analysis_source_counts": dict(
            sorted(analysis_source_counts.items())
        ),
        "repository_test_analysis_source_runs": {
            source: sorted(names)
            for source, names in sorted(analysis_source_runs.items())
        },
        "repository_test_overlay_trigger_reason_counts": dict(
            sorted(overlay_reason_counts.items())
        ),
        "repository_test_overlay_trigger_reason_runs": {
            reason: sorted(names)
            for reason, names in sorted(overlay_reason_runs.items())
        },
        "repository_test_execution_status_counts": dict(
            sorted(execution_status_counts.items())
        ),
        "repository_test_execution_status_runs": {
            status: sorted(names)
            for status, names in sorted(execution_status_runs.items())
        },
        "repository_test_execution_command_runs": {
            command: sorted(names)
            for command, names in sorted(execution_command_runs.items())
        },
        "repository_test_failure_category_counts": dict(
            sorted(failure_category_counts.items())
        ),
        "repository_test_failure_category_runs": {
            category: sorted(names)
            for category, names in sorted(failure_category_runs.items())
        },
        "repository_test_environment_setup_install_failure_counts": dict(
            sorted(setup_install_failure_counts.items())
        ),
        "repository_test_environment_setup_install_failure_runs": {
            category: sorted(names)
            for category, names in sorted(setup_install_failure_runs.items())
        },
        "repository_test_environment_setup_install_fallback_count": (
            setup_install_fallback_count
        ),
        "repository_test_environment_setup_install_fallback_success_count": (
            setup_install_fallback_success_count
        ),
        "repository_test_setup_doctor_status_counts": dict(
            sorted(setup_doctor_status_counts.items())
        ),
        "repository_test_setup_doctor_status_runs": {
            status: sorted(names)
            for status, names in sorted(setup_doctor_status_runs.items())
        },
        "repository_test_setup_doctor_blocker_counts": dict(
            sorted(setup_doctor_blocker_counts.items())
        ),
        "repository_test_setup_doctor_blocker_runs": {
            blocker: sorted(names)
            for blocker, names in sorted(setup_doctor_blocker_runs.items())
        },
        "repository_test_fault_localization_status_counts": dict(
            sorted(fault_status_counts.items())
        ),
        "repository_test_fault_localization_status_runs": {
            status: sorted(names)
            for status, names in sorted(fault_status_runs.items())
        },
        "repository_test_fault_localization_top_function_counts": dict(
            sorted(top_function_counts.items())
        ),
        "repository_test_fault_localization_top_function_runs": {
            function: sorted(names)
            for function, names in sorted(top_function_runs.items())
        },
        "repository_test_patch_validation_status_counts": dict(
            sorted(patch_status_counts.items())
        ),
        "repository_test_patch_validation_status_runs": {
            status: sorted(names)
            for status, names in sorted(patch_status_runs.items())
        },
        "repository_test_repair_summary_status_counts": dict(
            sorted(repair_summary_status_counts.items())
        ),
        "repository_test_repair_summary_status_runs": {
            status: sorted(names)
            for status, names in sorted(repair_summary_status_runs.items())
        },
        "repository_test_repair_summary_reason_counts": dict(
            sorted(repair_summary_reason_counts.items())
        ),
        "repository_test_repair_summary_reason_runs": {
            reason: sorted(names)
            for reason, names in sorted(repair_summary_reason_runs.items())
        },
        "repository_test_repair_summary_conclusion_counts": dict(
            sorted(repair_summary_conclusion_counts.items())
        ),
        "repository_test_repair_summary_conclusion_runs": {
            conclusion: sorted(names)
            for conclusion, names in sorted(
                repair_summary_conclusion_runs.items()
            )
        },
        "repository_test_final_status_counts": dict(
            sorted(final_status_counts.items())
        ),
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
        "repository_test_run_summaries": sorted(
            run_summaries,
            key=lambda item: str(item.get("name") or ""),
        ),
        "repository_test_phase2_ready_count": len(phase2_ready_runs),
        "repository_test_phase2_ready_runs": sorted(phase2_ready_runs),
        "repository_test_phase3_validation_ready_count": len(phase3_ready_runs),
        "repository_test_phase3_validation_ready_runs": sorted(phase3_ready_runs),
        "repository_test_patch_validation_success_run_count": len(
            patch_success_runs
        ),
        "repository_test_patch_validation_success_count": patch_success_count,
        "repository_test_patch_validation_success_runs": sorted(patch_success_runs),
    }


def _repository_test_route_from_onboarding_report(
    payload: dict[str, Any],
) -> dict[str, Any]:
    run_config_route = _dict(
        _dict(payload.get("run_config")).get("repository_test_analysis_route")
    )
    if run_config_route:
        return run_config_route

    dynamic = _dict(payload.get("repository_test_dynamic_evidence"))
    overlay = _dict(payload.get("repository_test_failure_overlay"))
    if not dynamic and not overlay:
        return {}
    natural_localization_ready = bool(
        dynamic.get("usable_for_localization", False)
    )
    overlay_localization_ready = bool(
        overlay.get("usable_for_localization", False)
    ) or str(overlay.get("status") or "") == "pass"
    source = "none"
    if natural_localization_ready:
        source = "natural_dynamic_evidence"
    elif overlay_localization_ready:
        source = "failure_overlay_dynamic_evidence"
    return {
        "analysis_source": source,
        "overlay_trigger_reason": str(overlay.get("reason") or ""),
        "phase2_ready": source
        in {"natural_dynamic_evidence", "failure_overlay_dynamic_evidence"},
        "phase3_validation_ready": bool(
            dynamic.get("usable_for_patch_validation", False)
        )
        or bool(overlay.get("usable_for_patch_validation", False)),
    }


def _repository_test_details_from_onboarding_report(
    payload: dict[str, Any],
) -> dict[str, Any]:
    run_config = _dict(payload.get("run_config"))
    execution_result = _dict(
        run_config.get("repository_test_execution_result")
    ) or _dict(payload.get("repository_test_execution_result"))
    execution_plan = _dict(
        run_config.get("repository_test_execution_plan")
    ) or _dict(payload.get("repository_test_execution_plan"))
    environment = _dict(
        run_config.get("repository_test_environment")
    ) or _dict(payload.get("repository_test_environment"))
    setup_result = _dict(
        run_config.get("repository_test_environment_setup_result")
    ) or _dict(payload.get("repository_test_environment_setup_result"))
    setup_doctor = _dict(run_config.get("repository_test_setup_doctor")) or _dict(
        payload.get("repository_test_setup_doctor")
    )
    framework_config = _dict(environment.get("framework_test_configuration"))
    localization = _dict(
        run_config.get("repository_test_fault_localization")
    ) or _dict(payload.get("repository_test_fault_localization"))
    patch_validation = _dict(
        run_config.get("repository_test_patch_validation")
    ) or _dict(payload.get("repository_test_patch_validation"))
    repair_summary = _dict(
        run_config.get("repository_test_repair_summary")
    ) or _dict(payload.get("repository_test_repair_summary"))
    retry_plan = _dict(run_config.get("repository_test_retry_plan")) or _dict(
        payload.get("repository_test_retry_plan")
    )
    dynamic_evidence = _dict(
        run_config.get("repository_test_dynamic_evidence")
    ) or _dict(payload.get("repository_test_dynamic_evidence"))
    effective_execution = _dict(
        run_config.get("repository_test_effective_execution_result")
    )
    if not bool(effective_execution.get("present", False)):
        effective_execution = {}
    return {
        "execution_status": str(
            effective_execution.get("status")
            or execution_result.get("status")
            or execution_plan.get("status")
            or ""
        ),
        "execution_command": str(
            effective_execution.get("command")
            or execution_result.get("command")
            or execution_plan.get("recommended_execution_command")
            or ""
        ),
        "failure_category": str(
            effective_execution.get("failure_category")
            or execution_result.get("failure_category")
            or ""
        ),
        "dynamic_failure_category": str(
            dynamic_evidence.get("failure_category") or ""
        ),
        "setup_install_failure_category": str(
            setup_result.get("install_failure_category") or ""
        ),
        "setup_install_failure_signal": str(
            setup_result.get("install_failure_signal") or ""
        ),
        "setup_install_fallback_executed": bool(
            setup_result.get("install_fallback_executed", False)
        ),
        "setup_install_fallback_returncode": (
            setup_result.get("install_fallback_returncode")
        ),
        "setup_doctor_status": str(setup_doctor.get("status") or ""),
        "setup_doctor_blocker": str(setup_doctor.get("blocker") or ""),
        "setup_doctor_next_action": str(setup_doctor.get("next_action") or ""),
        "framework_configuration_reason": str(
            framework_config.get("reason") or ""
        ),
        "framework_environment_variable_count": len(
            _dict(framework_config.get("environment_variables"))
        ),
        "fault_localization_status": str(localization.get("status") or ""),
        "top_function": str(localization.get("top_function") or ""),
        "patch_validation_status": str(patch_validation.get("status") or ""),
        "patch_validation_success_count": _int(
            patch_validation.get("success_count", 0)
        ),
        "repair_summary_status": str(repair_summary.get("status") or ""),
        "repair_summary_reason": str(repair_summary.get("reason") or ""),
        "repair_summary_conclusion": str(
            repair_summary.get("conclusion") or ""
        ),
        "repair_summary_path": str(
            _dict(payload.get("output_paths")).get(
                "repository_test_repair_summary_markdown"
            )
            or repair_summary.get("summary_path")
            or repair_summary.get("path")
            or ""
        ),
        "retry_recommended": bool(retry_plan.get("retry_recommended", False)),
        "retry_strategy": str(retry_plan.get("retry_strategy") or ""),
        "retry_command": str(retry_plan.get("retry_command") or ""),
    }


def _repository_test_final_diagnosis(
    route: dict[str, Any],
    test_details: dict[str, Any],
) -> dict[str, str]:
    success_count = _int(test_details.get("patch_validation_success_count", 0))
    if success_count > 0:
        return {
            "final_status": "repaired",
            "final_reason": "patch_validation_success",
        }

    if bool(route.get("phase3_validation_ready", False)):
        patch_status = str(
            test_details.get("patch_validation_status") or ""
        ).strip()
        return {
            "final_status": "phase3_ready",
            "final_reason": (
                "patch_validation_executed_without_success"
                if patch_status
                else "ready_for_patch_validation"
            ),
        }

    if bool(route.get("phase2_ready", False)):
        localization_status = str(
            test_details.get("fault_localization_status") or ""
        ).strip()
        return {
            "final_status": "phase2_ready",
            "final_reason": (
                "fault_localization_available"
                if localization_status == "pass"
                else "ready_for_fault_localization"
            ),
        }

    execution_status = str(
        test_details.get("execution_status") or ""
    ).strip()
    failure_category = str(
        test_details.get("dynamic_failure_category")
        or test_details.get("failure_category")
        or ""
    ).strip()
    analysis_source = str(route.get("analysis_source") or "").strip()
    if execution_status == "pass":
        reason = "repository_tests_passing"
    elif execution_status in {"error", "timeout"}:
        reason = f"repository_test_execution_{execution_status}"
    elif failure_category == "framework_configuration_error":
        reason = _framework_configuration_blocked_reason(test_details)
    elif failure_category:
        reason = f"dynamic_evidence_not_usable:{failure_category}"
    elif analysis_source in {"", "none"}:
        reason = "dynamic_evidence_not_usable"
    else:
        reason = "repository_test_not_ready"
    return {
        "final_status": "blocked",
        "final_reason": reason,
    }


def _framework_configuration_blocked_reason(test_details: dict[str, Any]) -> str:
    reason = str(test_details.get("framework_configuration_reason") or "").strip()
    if _int(test_details.get("framework_environment_variable_count", 0)) > 0:
        suffix = reason or "planned_environment_variables_applied"
        return f"framework_configuration_injected_but_failed:{suffix}"
    if reason:
        return f"framework_configuration_pending:{reason}"
    return "framework_configuration_pending:missing_framework_test_settings"


def _count_name(counts: dict[str, int], name: str) -> None:
    counts[name] = counts.get(name, 0) + 1


def _add_counted_run(
    counts: dict[str, int],
    runs: dict[str, list[str]],
    value: Any,
    run_name: str,
    *,
    skip_values: set[str] | None = None,
) -> None:
    key = str(value or "").strip()
    if not key or key in (skip_values or set()):
        return
    _count_name(counts, key)
    runs.setdefault(key, []).append(run_name)


def _add_mapped_run(
    runs: dict[str, list[str]],
    value: Any,
    run_name: str,
    *,
    skip_values: set[str] | None = None,
) -> None:
    key = str(value or "").strip()
    if not key or key in (skip_values or set()):
        return
    runs.setdefault(key, []).append(run_name)


def _pipeline_passed(
    *,
    preflight: GitHubOnboardingPreflightBatchReport,
    smoke: OnboardingSmokeRunnerReport | None,
    recommended_smoke: OnboardingSmokeRunnerReport | None,
    comparison: OnboardingRecommendationComparison | None,
) -> bool:
    if not preflight.passed or smoke is None or not smoke.passed:
        return False
    if recommended_smoke is None and comparison is None:
        return True
    return (
        recommended_smoke is not None
        and recommended_smoke.passed
        and comparison is not None
        and comparison.passed
    )


def _recommended_manifest_applied_count(path: str | Path) -> int:
    payload = _read_json(path)
    metadata = _dict(payload.get("recommendation_metadata"))
    return _int(metadata.get("applied_recommendation_count", 0))


def _read_json(path: str | Path) -> dict[str, Any]:
    try:
        return _dict(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _provided_config(value: Any) -> dict[str, Any]:
    return {key: item for key, item in _dict(value).items() if item is not None}


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


def _ratio(numerator: float, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def _pipeline_promotion_config(payload: dict[str, Any]) -> dict[str, Any]:
    return _normalize_promotion_config(_dict(payload.get("promotion_gate")))


def _merge_promotion_config(
    base_config: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(base_config)
    for key, value in overrides.items():
        if value is not None:
            merged[key] = value
    return _normalize_promotion_config(merged)


def _promotion_config_overrides_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "min_readiness_rate": args.promotion_min_readiness_rate,
        "min_smoke_benchmark_cases": args.promotion_min_smoke_benchmark_cases,
        "min_fallback_recovered_count": args.promotion_min_fallback_recovered_count,
        "min_repository_test_phase2_ready_count": (
            args.promotion_min_repository_test_phase2_ready_count
        ),
        "min_repository_test_phase3_validation_ready_count": (
            args.promotion_min_repository_test_phase3_validation_ready_count
        ),
        "min_repository_test_repaired_count": (
            args.promotion_min_repository_test_repaired_count
        ),
        "allow_warning_stages": args.promotion_allow_warning_stages,
        "fail_on_recommendation_regression": (
            args.promotion_fail_on_recommendation_regression
        ),
    }


def _apply_repo_repair_profile_defaults(args: argparse.Namespace) -> None:
    args.checkout_repository_tests = True
    if args.auto_scoped_include is None:
        args.auto_scoped_include = True
    if args.auto_fallback is None:
        args.auto_fallback = True
    _set_default(args, "fallback_min_generated_candidates", 1)
    _set_default(
        args,
        "fallback_max_sources",
        REPO_REPAIR_PROFILE_FALLBACK_MAX_SOURCES,
    )
    _set_default(
        args,
        "fallback_max_candidates",
        REPO_REPAIR_PROFILE_FALLBACK_MAX_CANDIDATES,
    )
    _set_default(args, "repository_test_timeout", 20)
    _set_default(
        args,
        "repository_test_failure_overlay_candidate_limit",
        REPO_REPAIR_PROFILE_OVERLAY_CANDIDATE_LIMIT,
    )
    _set_default(args, "repository_test_reflection_mode", "rule")
    _set_default(args, "repository_test_reflection_rounds", 1)
    _set_default(args, "repository_test_reflection_width", 1)
    _set_default(args, "repository_checkout_depth", 1)
    args.auto_repository_test_retry = True
    _set_default(args, "auto_repository_test_retry_max_risk", "low")
    if not getattr(args, "auto_repository_test_retry_runner", None):
        args.auto_repository_test_retry_runner = ["pytest", "unittest"]
    for key, value in REPO_REPAIR_PROFILE_PROMOTION_GATE.items():
        _set_default(args, _promotion_arg_name(key), value)


def _repo_repair_profile_expected_processing() -> dict[str, Any]:
    return {
        "target_status": "repaired",
        "target_primary_layer": "repository_repair",
        "target_repair_summary_status": "pass",
        "target_repair_summary_conclusion": "ready_for_review",
        "target_repair_summary_path_contains": (
            "repository_test_repair_summary.md"
        ),
    }


def _set_default(args: argparse.Namespace, name: str, value: Any) -> None:
    if getattr(args, name, None) is None:
        setattr(args, name, value)


def _promotion_arg_name(config_key: str) -> str:
    return f"promotion_{config_key}"


def _normalize_promotion_config(raw_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "min_readiness_rate": max(
            0.0,
            _float(raw_config.get("min_readiness_rate", 0.0)),
        ),
        "min_smoke_benchmark_cases": max(
            0,
            _int(raw_config.get("min_smoke_benchmark_cases", 1)),
        ),
        "min_fallback_recovered_count": max(
            0,
            _int(raw_config.get("min_fallback_recovered_count", 0)),
        ),
        "min_repository_test_phase2_ready_count": max(
            0,
            _int(raw_config.get("min_repository_test_phase2_ready_count", 0)),
        ),
        "min_repository_test_phase3_validation_ready_count": max(
            0,
            _int(
                raw_config.get(
                    "min_repository_test_phase3_validation_ready_count",
                    0,
                )
            ),
        ),
        "min_repository_test_repaired_count": max(
            0,
            _int(raw_config.get("min_repository_test_repaired_count", 0)),
        ),
        "allow_warning_stages": _bool(
            raw_config.get("allow_warning_stages", False)
        ),
        "fail_on_recommendation_regression": _bool(
            raw_config.get("fail_on_recommendation_regression", True),
            default=True,
        ),
    }


def _recommendation_status(
    *,
    comparison: OnboardingRecommendationComparison | None,
    comparison_summary: dict[str, Any],
) -> str:
    if comparison is None:
        return "not_run"
    if comparison.regressions:
        return "regressed"
    if (
        _int(comparison_summary.get("candidate_delta", 0)) > 0
        or _int(comparison_summary.get("benchmark_case_delta", 0)) > 0
        or _int(comparison_summary.get("improved_run_count", 0)) > 0
        or _float(comparison_summary.get("validation_pass_rate_delta", 0.0)) > 0.0
    ):
        return "improved"
    return "unchanged"


def _pipeline_resume_bullets(
    headline: dict[str, Any],
    readiness: dict[str, Any],
) -> list[str]:
    bullets = [
        (
            "Built a multi-repository GitHub onboarding pipeline with "
            f"{_float(headline.get('preflight_readiness_rate', 0.0)):.4f} "
            "preflight readiness, filtering candidate repositories before "
            "sandbox-heavy benchmark materialization."
        ),
        (
            "Generated "
            f"{_int(headline.get('smoke_generated_candidates', 0))} benchmark "
            f"candidates and {_int(headline.get('smoke_benchmark_cases', 0))} "
            "executable smoke benchmark cases from ready GitHub sources."
        ),
    ]
    static_ready = _int(
        headline.get("smoke_static_intelligence_analysis_ready_count", 0)
    )
    if static_ready > 0:
        bullets.append(
            "Built arbitrary-repository static intelligence reporting with "
            f"{static_ready}/"
            f"{_int(headline.get('smoke_static_intelligence_run_count', 0))} "
            "runs reaching analysis-ready status and "
            f"{_int(headline.get('smoke_static_intelligence_selected_signal_count', 0))}/"
            f"{_int(headline.get('smoke_static_intelligence_total_signal_count', 0))} "
            "static bug signals retained for downstream validation."
        )
    status = str(headline.get("recommendation_status") or "not_run")
    if status != "not_run":
        bullets.append(
            "Added recommendation rerun validation with "
            f"{status} status, candidate delta "
            f"{_signed_int(headline.get('recommendation_candidate_delta', 0))} "
            "and benchmark-case delta "
            f"{_signed_int(headline.get('recommendation_benchmark_case_delta', 0))}."
        )
    fallback_recovered = _int(headline.get("smoke_fallback_recovered_count", 0))
    if fallback_recovered > 0:
        bullets.append(
            "Implemented automatic fallback recovery for low-yield repository "
            f"mining runs, recovering {fallback_recovered} benchmark-ready runs "
            "without hand-editing the source manifest."
        )
    auto_remediation_used = _int(
        headline.get("smoke_auto_remediation_used_count", 0)
    )
    if auto_remediation_used > 0:
        bullets.append(
            "Added low-risk benchmark auto-remediation to execute runnable "
            f"repair actions across {auto_remediation_used} repository runs, "
            "turning benchmarkization recommendations into measured cases."
        )
    benchmarkization_ready = _int(
        headline.get("smoke_benchmarkization_ready_count", 0)
    )
    if benchmarkization_ready > 0:
        bullets.append(
            "Audited repository benchmarkization readiness across smoke runs, "
            f"with {benchmarkization_ready} reports reaching benchmark-ready status "
            "and remediation-plan artifacts retained for blocked runs."
        )
    top_issue = str(headline.get("preflight_top_issue_code") or "")
    if top_issue:
        bullets.append(
            f"Aggregated onboarding failure taxonomy with top issue `{top_issue}` "
            f"across skipped runs: {_format_name_list(readiness.get('skipped_run_names'))}."
        )
    return bullets


def _pipeline_stage_audit(
    *,
    headline: dict[str, Any],
    readiness: dict[str, Any],
    smoke_evidence: dict[str, Any],
    repository_test_readiness: dict[str, Any],
    recommendation: dict[str, Any],
) -> list[dict[str, str]]:
    ready_count = _int(headline.get("preflight_ready_count", 0))
    skipped_count = _int(headline.get("preflight_skipped_count", 0))
    top_issue = str(headline.get("preflight_top_issue_code") or "")
    if ready_count <= 0:
        preflight_status = "fail"
        preflight_action = "Fix discovery/import filters before smoke benchmarking."
    elif skipped_count > 0 or top_issue:
        preflight_status = "warn"
        preflight_action = "Inspect skipped runs and tune include/exclude or recipe selection."
    else:
        preflight_status = "pass"
        preflight_action = "Proceed to smoke benchmark execution."

    smoke_present = bool(smoke_evidence.get("present", False))
    smoke_passed = bool(smoke_evidence.get("passed", False))
    smoke_cases = _int(smoke_evidence.get("benchmark_cases", 0))
    benchmarkization_statuses = _format_counts(
        _dict(smoke_evidence.get("benchmarkization_status_counts"))
    )
    static_ready = _int(
        smoke_evidence.get("static_intelligence_analysis_ready_count", 0)
    )
    static_runs = _int(smoke_evidence.get("static_intelligence_run_count", 0))
    static_statuses = _format_counts(
        _dict(smoke_evidence.get("static_intelligence_status_counts"))
    )
    static_rules = _format_counts(
        _dict(smoke_evidence.get("static_intelligence_rule_counts"))
    )
    fallback_recovered = _int(smoke_evidence.get("fallback_recovered_count", 0))
    auto_remediation_used = _int(
        smoke_evidence.get("auto_remediation_used_count", 0)
    )
    smoke_gap_status = str(smoke_evidence.get("gap_status") or "")
    recommendation_count = _int(
        smoke_evidence.get("manifest_recommendation_count", 0)
    )
    if not smoke_present:
        smoke_status = "skip"
        smoke_action = "Run smoke suite after at least one preflight target is ready."
    elif not smoke_passed or smoke_cases <= 0:
        smoke_status = "fail"
        smoke_action = "Inspect smoke runner gaps and benchmark reports."
    elif recommendation_count > 0:
        smoke_status = "warn"
        smoke_action = "Review fallback recommendations before scaling the repo list."
    elif smoke_gap_status == "warning":
        smoke_status = "warn"
        gap_next_actions = [
            str(action).strip()
            for action in _list(smoke_evidence.get("gap_next_actions"))
            if str(action).strip()
        ]
        smoke_action = (
            gap_next_actions[0]
            if gap_next_actions
            else "Inspect smoke runner gap diagnostics before scaling the repo list."
        )
    else:
        smoke_status = "pass"
        smoke_action = "Keep generated benchmark artifacts as reproducible evidence."

    applied_count = _int(recommendation.get("applied_count", 0))
    recommendation_status_value = str(recommendation.get("status") or "not_run")
    if applied_count <= 0:
        recommendation_status = "skip"
        recommendation_action = "No recovered fallback settings need rerun validation."
    elif recommendation_status_value == "regressed" or not bool(
        recommendation.get("comparison_passed", False)
    ):
        recommendation_status = "fail"
        recommendation_action = "Inspect recommendation comparison regressions."
    else:
        recommendation_status = "pass"
        recommendation_action = "Promote the recommended manifest when useful."

    return [
        {
            "stage": "preflight",
            "status": preflight_status,
            "evidence": (
                f"{ready_count} ready, {skipped_count} skipped, "
                f"top_issue={top_issue or 'none'}"
            ),
            "next_action": preflight_action,
        },
        {
            "stage": "smoke_benchmark",
            "status": smoke_status,
            "evidence": (
                f"{_int(smoke_evidence.get('generated_candidates', 0))} candidates, "
                f"{smoke_cases} cases, fallback_recovered={fallback_recovered}, "
                f"auto_remediation_used={auto_remediation_used}, "
                f"static={static_ready}/{static_runs}, "
                f"static_statuses={static_statuses}, "
                f"static_rules={static_rules}, "
                f"benchmarkization={benchmarkization_statuses}, "
                f"gap={smoke_gap_status or 'none'}"
            ),
            "next_action": smoke_action,
        },
        _pipeline_repository_repair_stage(repository_test_readiness),
        {
            "stage": "recommendation_rerun",
            "status": recommendation_status,
            "evidence": (
                f"applied={applied_count}, status={recommendation_status_value}, "
                f"regressions={len(_list(recommendation.get('regressions')))}"
            ),
            "next_action": recommendation_action,
        },
    ]


def _pipeline_repository_repair_stage(
    repository_test_readiness: dict[str, Any],
) -> dict[str, str]:
    report_count = _int(repository_test_readiness.get("report_count", 0))
    final_counts = _dict(repository_test_readiness.get("final_status_counts"))
    blocked_counts = _dict(repository_test_readiness.get("blocked_reason_counts"))
    execution_counts = _dict(repository_test_readiness.get("execution_status_counts"))
    setup_failure_counts = _dict(
        repository_test_readiness.get("setup_install_failure_counts")
    )
    setup_doctor_blocker_counts = _repository_test_setup_doctor_blocker_counts(
        repository_test_readiness
    )
    repair_summary_status_counts = _dict(
        repository_test_readiness.get("repair_summary_status_counts")
        or repository_test_readiness.get("repository_test_repair_summary_status_counts")
    )
    repair_summary_conclusion_counts = _dict(
        repository_test_readiness.get("repair_summary_conclusion_counts")
        or repository_test_readiness.get(
            "repository_test_repair_summary_conclusion_counts"
        )
    )
    phase2_ready = _int(repository_test_readiness.get("phase2_ready_count", 0))
    phase3_ready = _int(
        repository_test_readiness.get("phase3_validation_ready_count", 0)
    )
    patch_success_runs = _int(
        repository_test_readiness.get("patch_validation_success_run_count", 0)
    )
    repaired_count = _int(final_counts.get("repaired", 0))
    blocked_count = _int(final_counts.get("blocked", 0))
    top_blocked_reason = _top_count_key(blocked_counts)
    top_setup_failure = _top_count_key(setup_failure_counts)
    top_setup_doctor_blocker = _top_count_key(setup_doctor_blocker_counts)
    top_repair_summary_status = _top_count_key(repair_summary_status_counts)
    top_repair_summary_conclusion = _top_count_key(
        repair_summary_conclusion_counts
    )

    if report_count <= 0:
        status = "skip"
        action = (
            "Run repo-mode with --checkout-repository-tests to collect repository "
            "test repair evidence."
        )
    elif repaired_count > 0 or patch_success_runs > 0:
        status = "pass"
        action = "Keep repository repair artifacts as Phase 2/3 evidence."
    elif phase3_ready > 0:
        status = "warn"
        action = "Inspect repository_test_patch_validation artifacts for failed patches."
    elif phase2_ready > 0:
        status = "warn"
        action = "Run patch candidate generation and sandbox validation for localized functions."
    elif blocked_count > 0:
        status = (
            "skip"
            if _repository_repair_not_executed(execution_counts, blocked_counts)
            else "warn"
        )
        action = _repository_repair_blocked_action(
            top_blocked_reason,
            top_setup_failure=top_setup_failure,
            top_setup_doctor_blocker=top_setup_doctor_blocker,
        )
    else:
        status = "skip"
        action = "Enable repository-test execution before assessing repair readiness."

    return {
        "stage": "repository_repair",
        "status": status,
        "evidence": (
            f"reports={report_count}, phase2={phase2_ready}, "
            f"phase3={phase3_ready}, repaired={repaired_count}, "
            f"blocked={blocked_count}, top_blocked_reason={top_blocked_reason or 'none'}, "
            f"top_setup_install_failure={top_setup_failure or 'none'}, "
            f"top_setup_doctor_blocker={top_setup_doctor_blocker or 'none'}, "
            f"top_repair_summary={top_repair_summary_status or 'none'}/"
            f"{top_repair_summary_conclusion or 'none'}"
        ),
        "next_action": action,
    }


def _repository_repair_not_executed(
    execution_counts: dict[str, Any],
    blocked_counts: dict[str, Any],
) -> bool:
    execution_statuses = {
        str(status)
        for status, count in execution_counts.items()
        if _int(count) > 0
    }
    blocked_reasons = {
        str(reason)
        for reason, count in blocked_counts.items()
        if _int(count) > 0
    }
    return (
        execution_statuses <= {"skipped"}
        and bool(blocked_reasons)
        and all(
            reason.startswith("dynamic_evidence_not_usable:not_executed")
            for reason in blocked_reasons
        )
    )


def _repository_repair_blocked_action(
    top_blocked_reason: str,
    *,
    top_setup_failure: str = "",
    top_setup_doctor_blocker: str = "",
) -> str:
    if top_setup_failure:
        return _repository_repair_setup_failure_action(top_setup_failure)
    if top_setup_doctor_blocker:
        return _repository_test_setup_doctor_blocker_action(
            top_setup_doctor_blocker
        )
    if top_blocked_reason.startswith("dynamic_evidence_not_usable:not_executed"):
        return (
            "Enable --checkout-repository-tests or provide --repository-test-root, "
            "then rerun the pipeline."
        )
    if top_blocked_reason.startswith("dynamic_evidence_not_usable"):
        return "Inspect repository_test_execution_result and retry plan for usable failing tests."
    if top_blocked_reason == "repository_tests_passing":
        return "Review failure-overlay candidates when natural tests pass without dynamic failures."
    if top_blocked_reason:
        return f"Inspect repository repair blocked reason `{top_blocked_reason}`."
    return "Inspect repository_test_run_summaries for the first blocked repair step."


def _repository_repair_setup_failure_action(top_setup_failure: str) -> str:
    if top_setup_failure == "editable_backend_unsupported":
        return (
            "Inspect repository_test_environment_setup_result; editable install "
            "failed, verify whether the non-editable install fallback succeeded."
        )
    if top_setup_failure == "missing_requirement_file":
        return (
            "Verify checkout ref/depth and dependency file paths before rerunning "
            "repository test setup."
        )
    if top_setup_failure == "python_version_incompatible":
        return (
            "Rerun repository tests with a Python version compatible with the "
            "repository metadata."
        )
    if top_setup_failure in {"dependency_conflict", "package_resolution_failed"}:
        return (
            "Inspect dependency pins, extras and package index availability before "
            "rerunning repository test setup."
        )
    if top_setup_failure == "network_or_index_error":
        return "Retry dependency setup after network/package-index access is stable."
    if top_setup_failure == "build_backend_missing":
        return "Inspect pyproject.toml build-system requirements and backend configuration."
    return (
        f"Inspect repository_test_environment_setup_result for install failure "
        f"`{top_setup_failure}`."
    )


def _top_count_key(counts: dict[str, Any]) -> str:
    if not counts:
        return ""
    return max(
        sorted((str(key), _int(value)) for key, value in counts.items()),
        key=lambda item: item[1],
    )[0]


def _pipeline_stage_summary(stage_audit: list[dict[str, str]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    stages_by_status: dict[str, list[str]] = {
        "pass": [],
        "warn": [],
        "fail": [],
        "skip": [],
    }
    for stage in stage_audit:
        stage_name = str(stage.get("stage", "")).strip()
        status = str(stage.get("status", "")).strip() or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        stages_by_status.setdefault(status, [])
        if stage_name:
            stages_by_status[status].append(stage_name)

    blocking_stage_names = list(stages_by_status.get("fail", []))
    warning_stage_names = list(stages_by_status.get("warn", []))
    return {
        "stage_count": len(stage_audit),
        "status_counts": dict(sorted(status_counts.items())),
        "pass_stage_names": list(stages_by_status.get("pass", [])),
        "warn_stage_names": warning_stage_names,
        "fail_stage_names": blocking_stage_names,
        "skip_stage_names": list(stages_by_status.get("skip", [])),
        "blocking_stage_names": blocking_stage_names,
        "warning_stage_names": warning_stage_names,
        "has_blockers": bool(blocking_stage_names),
        "needs_attention": bool(blocking_stage_names or warning_stage_names),
    }


def _pipeline_promotion_gate(
    *,
    headline: dict[str, Any],
    smoke_evidence: dict[str, Any],
    repository_test_readiness: dict[str, Any],
    recommendation: dict[str, Any],
    stage_audit: list[dict[str, str]],
    stage_summary: dict[str, Any],
    promotion_config: dict[str, Any],
) -> dict[str, Any]:
    warning_stage_names = [
        str(name) for name in _list(stage_summary.get("warning_stage_names"))
    ]
    blocking_stage_names = [
        str(name) for name in _list(stage_summary.get("blocking_stage_names"))
    ]
    recommendation_regressions = _list(recommendation.get("regressions"))
    smoke_cases = _int(smoke_evidence.get("benchmark_cases", 0))
    min_smoke_cases = _int(promotion_config.get("min_smoke_benchmark_cases", 1))
    fallback_recovered_count = _int(
        smoke_evidence.get("fallback_recovered_count", 0)
    )
    min_fallback_recovered_count = _int(
        promotion_config.get("min_fallback_recovered_count", 0)
    )
    phase2_ready_count = _int(
        repository_test_readiness.get("phase2_ready_count", 0)
    )
    phase3_ready_count = _int(
        repository_test_readiness.get("phase3_validation_ready_count", 0)
    )
    repaired_count = _int(
        _dict(repository_test_readiness.get("final_status_counts")).get(
            "repaired",
            0,
        )
    )
    min_phase2_ready_count = _int(
        promotion_config.get("min_repository_test_phase2_ready_count", 0)
    )
    min_phase3_ready_count = _int(
        promotion_config.get(
            "min_repository_test_phase3_validation_ready_count",
            0,
        )
    )
    min_repaired_count = _int(
        promotion_config.get("min_repository_test_repaired_count", 0)
    )
    readiness_rate = _float(headline.get("preflight_readiness_rate", 0.0))
    min_readiness_rate = _float(promotion_config.get("min_readiness_rate", 0.0))
    allow_warning_stages = _bool(
        promotion_config.get("allow_warning_stages", False)
    )
    fail_on_recommendation_regression = _bool(
        promotion_config.get("fail_on_recommendation_regression", True),
        default=True,
    )
    smoke_has_cases = smoke_cases > 0
    smoke_case_threshold_met = smoke_cases >= min_smoke_cases
    fallback_recovered_threshold_met = (
        fallback_recovered_count >= min_fallback_recovered_count
    )
    phase2_ready_threshold_met = phase2_ready_count >= min_phase2_ready_count
    phase3_ready_threshold_met = phase3_ready_count >= min_phase3_ready_count
    repaired_threshold_met = repaired_count >= min_repaired_count
    readiness_threshold_met = readiness_rate >= min_readiness_rate
    repository_test_threshold_requested = any(
        value > 0
        for value in (
            min_phase2_ready_count,
            min_phase3_ready_count,
            min_repaired_count,
        )
    )
    repository_test_thresholds_met = (
        phase2_ready_threshold_met
        and phase3_ready_threshold_met
        and repaired_threshold_met
    )
    repository_test_only_promotion = (
        min_smoke_cases <= 0
        and repository_test_threshold_requested
        and repository_test_thresholds_met
    )
    effective_blocking_stage_names = [
        name
        for name in blocking_stage_names
        if not (repository_test_only_promotion and name == "smoke_benchmark")
    ]
    pipeline_acceptance_met = bool(headline.get("passed", False)) or (
        repository_test_only_promotion and readiness_threshold_met
    )
    criteria = {
        "pipeline_passed": bool(headline.get("passed", False)),
        "pipeline_acceptance_met": pipeline_acceptance_met,
        "repository_test_only_promotion": repository_test_only_promotion,
        "smoke_has_cases": smoke_has_cases,
        "smoke_benchmark_cases": smoke_cases,
        "min_smoke_benchmark_cases": min_smoke_cases,
        "smoke_case_threshold_met": smoke_case_threshold_met,
        "fallback_recovered_count": fallback_recovered_count,
        "min_fallback_recovered_count": min_fallback_recovered_count,
        "fallback_recovered_threshold_met": fallback_recovered_threshold_met,
        "repository_test_phase2_ready_count": phase2_ready_count,
        "min_repository_test_phase2_ready_count": min_phase2_ready_count,
        "repository_test_phase2_ready_threshold_met": (
            phase2_ready_threshold_met
        ),
        "repository_test_phase3_validation_ready_count": phase3_ready_count,
        "min_repository_test_phase3_validation_ready_count": (
            min_phase3_ready_count
        ),
        "repository_test_phase3_validation_ready_threshold_met": (
            phase3_ready_threshold_met
        ),
        "repository_test_repaired_count": repaired_count,
        "min_repository_test_repaired_count": min_repaired_count,
        "repository_test_repaired_threshold_met": repaired_threshold_met,
        "preflight_readiness_rate": readiness_rate,
        "min_readiness_rate": min_readiness_rate,
        "readiness_threshold_met": readiness_threshold_met,
        "has_stage_blockers": bool(effective_blocking_stage_names),
        "warning_stage_count": len(warning_stage_names),
        "allow_warning_stages": allow_warning_stages,
        "recommendation_regression_count": len(recommendation_regressions),
        "fail_on_recommendation_regression": fail_on_recommendation_regression,
    }

    blocking_reasons: list[str] = []
    if not pipeline_acceptance_met:
        blocking_reasons.append("Pipeline did not pass end-to-end.")
    if min_smoke_cases > 0 and not smoke_has_cases:
        blocking_reasons.append("No executable smoke benchmark cases were produced.")
    elif not smoke_case_threshold_met:
        blocking_reasons.append(
            f"Smoke benchmark cases {smoke_cases} below required {min_smoke_cases}."
        )
    if not readiness_threshold_met:
        blocking_reasons.append(
            "Preflight readiness rate "
            f"{readiness_rate:.4f} below required {min_readiness_rate:.4f}."
        )
    if not fallback_recovered_threshold_met:
        blocking_reasons.append(
            "Fallback recovered runs "
            f"{fallback_recovered_count} below required {min_fallback_recovered_count}."
        )
    if not phase2_ready_threshold_met:
        blocking_reasons.append(
            "Repository test Phase 2 ready runs "
            f"{phase2_ready_count} below required {min_phase2_ready_count}."
        )
    if not phase3_ready_threshold_met:
        blocking_reasons.append(
            "Repository test Phase 3 validation-ready runs "
            f"{phase3_ready_count} below required {min_phase3_ready_count}."
        )
    if not repaired_threshold_met:
        blocking_reasons.append(
            "Repository test repaired runs "
            f"{repaired_count} below required {min_repaired_count}."
        )
    if effective_blocking_stage_names:
        blocking_reasons.append(
            f"Blocking stages: {_format_name_list(effective_blocking_stage_names)}."
        )
    if recommendation_regressions and fail_on_recommendation_regression:
        blocking_reasons.append(
            f"Recommendation regressions: {len(recommendation_regressions)}."
        )

    warning_reasons: list[str] = []
    if warning_stage_names:
        warning_reasons.append(
            f"Warning stages: {_format_name_list(warning_stage_names)}."
        )

    if blocking_reasons:
        status = "blocked"
    elif warning_reasons and not allow_warning_stages:
        status = "review"
    else:
        status = "promote"

    return {
        "status": status,
        "promotable": status == "promote",
        "blocking_reasons": blocking_reasons,
        "warning_reasons": warning_reasons,
        "criteria": criteria,
        "config": dict(promotion_config),
        "stage_actions": {
            str(stage.get("stage", "")): str(stage.get("next_action", ""))
            for stage in stage_audit
            if str(stage.get("stage", "")).strip()
        },
    }


def _pipeline_next_actions(stage_audit: list[dict[str, str]]) -> list[str]:
    actions: list[str] = []
    for stage in stage_audit:
        status = str(stage.get("status", ""))
        if status in {"fail", "warn"}:
            action = str(stage.get("next_action", "")).strip()
            if action and action not in actions:
                actions.append(action)
    if not actions:
        actions.append(
            "Keep this pipeline showcase as a reproducible onboarding evidence artifact."
        )
    return actions


def _pipeline_repository_processing_matrix(summary: dict[str, Any]) -> dict[str, Any]:
    run_diagnoses = [
        _pipeline_repository_processing_run_diagnosis(_dict(item))
        for item in _list(summary.get("repository_test_run_summaries"))
    ]
    repair_manifest_contexts = _dict(
        summary.get("repository_repair_manifest_run_repair_contexts")
    )
    _annotate_run_diagnoses_with_repair_manifest_contexts(
        run_diagnoses,
        repair_manifest_contexts,
    )
    status_counts: dict[str, int] = {}
    primary_layer_counts: dict[str, int] = {}
    primary_blocker_counts: dict[str, int] = {}
    primary_blocker_runs: dict[str, list[str]] = {}
    repair_summary_status_counts: dict[str, int] = {}
    repair_summary_status_runs: dict[str, list[str]] = {}
    repair_summary_conclusion_counts: dict[str, int] = {}
    repair_summary_conclusion_runs: dict[str, list[str]] = {}
    for item in run_diagnoses:
        run_name = str(item.get("name") or "")
        _add_counted_run(status_counts, {}, item.get("status"), run_name)
        _add_counted_run(primary_layer_counts, {}, item.get("primary_layer"), run_name)
        _add_counted_run(
            primary_blocker_counts,
            primary_blocker_runs,
            item.get("primary_blocker"),
            run_name,
            skip_values={"", "none"},
        )
        _add_counted_run(
            repair_summary_status_counts,
            repair_summary_status_runs,
            item.get("repair_summary_status"),
            run_name,
            skip_values={"", "none"},
        )
        _add_counted_run(
            repair_summary_conclusion_counts,
            repair_summary_conclusion_runs,
            item.get("repair_summary_conclusion"),
            run_name,
            skip_values={"", "none"},
        )
    return {
        "run_count": len(run_diagnoses),
        "status_counts": dict(sorted(status_counts.items())),
        "primary_layer_counts": dict(sorted(primary_layer_counts.items())),
        "primary_blocker_counts": dict(sorted(primary_blocker_counts.items())),
        "primary_blocker_runs": {
            key: sorted(values)
            for key, values in sorted(primary_blocker_runs.items())
        },
        "repair_summary_status_counts": dict(
            sorted(repair_summary_status_counts.items())
        ),
        "repair_summary_status_runs": {
            key: sorted(values)
            for key, values in sorted(repair_summary_status_runs.items())
        },
        "repair_summary_conclusion_counts": dict(
            sorted(repair_summary_conclusion_counts.items())
        ),
        "repair_summary_conclusion_runs": {
            key: sorted(values)
            for key, values in sorted(repair_summary_conclusion_runs.items())
        },
        "repair_manifest_run_context_count": _int(
            summary.get("repository_repair_manifest_run_context_count", 0)
        ),
        "repair_manifest_setup_repair_run_names": [
            str(name)
            for name in _list(
                summary.get("repository_repair_manifest_setup_repair_run_names")
            )
        ],
        "repair_manifest_checkout_only_run_names": [
            str(name)
            for name in _list(
                summary.get("repository_repair_manifest_checkout_only_run_names")
            )
        ],
        "repair_manifest_run_contexts": copy.deepcopy(repair_manifest_contexts),
        "run_diagnoses": sorted(
            run_diagnoses,
            key=lambda item: str(item.get("name") or ""),
        ),
    }


def _annotate_run_diagnoses_with_repair_manifest_contexts(
    run_diagnoses: list[dict[str, Any]],
    repair_manifest_contexts: dict[str, Any],
) -> None:
    for diagnosis in run_diagnoses:
        name = str(diagnosis.get("name") or "")
        context = _dict(repair_manifest_contexts.get(name))
        diagnosis["repair_manifest_blocker"] = str(context.get("blocker") or "")
        diagnosis["repair_manifest_setup_repair"] = bool(
            context.get("setup_repair", False)
        )
        diagnosis["repair_manifest_next_action"] = str(
            context.get("next_action") or ""
        )


def _pipeline_repository_processing_expectations(
    manifest_payload: dict[str, Any],
) -> dict[str, Any]:
    root_expectations = copy.deepcopy(
        _dict(manifest_payload.get("repository_processing_matrix_expectations"))
    )
    run_expectations: dict[str, Any] = {}
    for run_value in _list(manifest_payload.get("runs")):
        run = _dict(run_value)
        run_name = str(run.get("name") or "")
        if not run_name:
            continue
        compiled = _compile_repository_processing_run_expectation(
            _dict(run.get("expected_repository_processing"))
        )
        if compiled:
            run_expectations[run_name] = compiled

    for run_name, expectation_value in sorted(
        _dict(root_expectations.get("run_expectations")).items()
    ):
        existing = _dict(run_expectations.get(str(run_name)))
        merged = dict(existing)
        merged.update(copy.deepcopy(_dict(expectation_value)))
        run_expectations[str(run_name)] = merged

    if run_expectations:
        root_expectations["run_expectations"] = run_expectations
    return root_expectations


def _compile_repository_processing_run_expectation(
    raw_expectation: dict[str, Any],
) -> dict[str, Any]:
    if not raw_expectation:
        return {}
    result: dict[str, Any] = {}
    direct_fields = (
        "status",
        "primary_layer",
        "primary_blocker",
        "repair_summary_status",
        "repair_summary_conclusion",
        "repair_summary_path_contains",
        "repair_manifest_blocker",
        "repair_manifest_setup_repair",
        "repair_manifest_next_action_contains",
        "purpose",
    )
    for field in direct_fields:
        if raw_expectation.get(field) not in (None, ""):
            result[field] = copy.deepcopy(raw_expectation.get(field))
    list_fields = (
        "allowed_statuses",
        "allowed_primary_layers",
        "allowed_primary_blockers",
        "allowed_primary_blocker_prefixes",
        "allowed_repair_summary_statuses",
        "allowed_repair_summary_conclusions",
        "allowed_repair_manifest_blockers",
        "allowed_repair_manifest_blocker_prefixes",
    )
    for field in list_fields:
        values = [str(item) for item in _list(raw_expectation.get(field)) if str(item)]
        if values:
            result[field] = values

    _apply_target_status_expectation(result, str(raw_expectation.get("target_status") or ""))
    target_statuses = [
        str(item) for item in _list(raw_expectation.get("target_statuses")) if str(item)
    ]
    if target_statuses and "allowed_statuses" not in result and "status" not in result:
        result["allowed_statuses"] = target_statuses
    if raw_expectation.get("target_primary_layer"):
        result.setdefault(
            "primary_layer", str(raw_expectation.get("target_primary_layer") or "")
        )
    target_primary_layers = [
        str(item)
        for item in _list(raw_expectation.get("target_primary_layers"))
        if str(item)
    ]
    if target_primary_layers and "allowed_primary_layers" not in result:
        result["allowed_primary_layers"] = target_primary_layers
    if raw_expectation.get("target_primary_blocker"):
        result.setdefault(
            "primary_blocker",
            str(raw_expectation.get("target_primary_blocker") or ""),
        )
    target_primary_blockers = [
        str(item)
        for item in _list(raw_expectation.get("target_primary_blockers"))
        if str(item)
    ]
    if target_primary_blockers and "allowed_primary_blockers" not in result:
        result["allowed_primary_blockers"] = target_primary_blockers
    target_primary_blocker_prefixes = [
        str(item)
        for item in _list(raw_expectation.get("target_primary_blocker_prefixes"))
        if str(item)
    ]
    if (
        target_primary_blocker_prefixes
        and "allowed_primary_blocker_prefixes" not in result
    ):
        result["allowed_primary_blocker_prefixes"] = target_primary_blocker_prefixes
    if raw_expectation.get("target_repair_summary_status"):
        result.setdefault(
            "repair_summary_status",
            str(raw_expectation.get("target_repair_summary_status") or ""),
        )
    if raw_expectation.get("target_repair_summary_conclusion"):
        result.setdefault(
            "repair_summary_conclusion",
            str(raw_expectation.get("target_repair_summary_conclusion") or ""),
        )
    if raw_expectation.get("target_repair_summary_path_contains"):
        result.setdefault(
            "repair_summary_path_contains",
            str(raw_expectation.get("target_repair_summary_path_contains") or ""),
        )
    if raw_expectation.get("target_repair_manifest_blocker"):
        result.setdefault(
            "repair_manifest_blocker",
            str(raw_expectation.get("target_repair_manifest_blocker") or ""),
        )
    target_repair_manifest_blockers = [
        str(item)
        for item in _list(raw_expectation.get("target_repair_manifest_blockers"))
        if str(item)
    ]
    if (
        target_repair_manifest_blockers
        and "allowed_repair_manifest_blockers" not in result
    ):
        result["allowed_repair_manifest_blockers"] = (
            target_repair_manifest_blockers
        )
    target_repair_manifest_blocker_prefixes = [
        str(item)
        for item in _list(
            raw_expectation.get("target_repair_manifest_blocker_prefixes")
        )
        if str(item)
    ]
    if (
        target_repair_manifest_blocker_prefixes
        and "allowed_repair_manifest_blocker_prefixes" not in result
    ):
        result["allowed_repair_manifest_blocker_prefixes"] = (
            target_repair_manifest_blocker_prefixes
        )
    if raw_expectation.get("target_repair_manifest_setup_repair") is not None:
        result.setdefault(
            "repair_manifest_setup_repair",
            _bool(raw_expectation.get("target_repair_manifest_setup_repair")),
        )
    if raw_expectation.get("target_repair_manifest_next_action_contains"):
        result.setdefault(
            "repair_manifest_next_action_contains",
            str(
                raw_expectation.get(
                    "target_repair_manifest_next_action_contains"
                )
                or ""
            ),
        )
    return {key: value for key, value in result.items() if value not in ("", [], {})}


def _apply_target_status_expectation(
    result: dict[str, Any],
    target_status: str,
) -> None:
    if not target_status or "status" in result or "allowed_statuses" in result:
        return
    exact_statuses = {"repaired", "phase3_ready", "phase2_ready", "blocked", "incomplete"}
    if target_status in exact_statuses:
        result["status"] = target_status
        return
    if target_status == "repaired_or_blocked_with_dynamic_evidence_diagnosis":
        result["allowed_statuses"] = [
            "repaired",
            "phase3_ready",
            "phase2_ready",
            "blocked",
        ]
        result.setdefault(
            "allowed_primary_blockers",
            ["none", "repository_tests_passing", "patch_generation_not_executed"],
        )
        result.setdefault(
            "allowed_primary_blocker_prefixes",
            ["dynamic_evidence_not_usable:"],
        )
        return
    if target_status == "setup_or_execution_diagnosed":
        result["allowed_statuses"] = [
            "repaired",
            "phase3_ready",
            "phase2_ready",
            "blocked",
            "incomplete",
        ]
        result.setdefault(
            "allowed_primary_blockers",
            ["none", "patch_generation_not_executed", "repository_test_not_executed"],
        )
        result.setdefault(
            "allowed_primary_blocker_prefixes",
            ["setup_install_failure:", "dynamic_evidence_not_usable:", "execution_status:"],
        )
        return
    result["status"] = target_status


def _pipeline_repository_processing_expectation_report(
    matrix: dict[str, Any],
    expectations: dict[str, Any],
) -> dict[str, Any]:
    if not expectations:
        return {
            "present": False,
            "passed": True,
            "check_count": 0,
            "failed_count": 0,
            "checks": [],
        }
    checks: list[dict[str, Any]] = []
    if expectations.get("min_run_count") is not None:
        _append_min_check(
            checks,
            name="min_run_count",
            actual=_int(matrix.get("run_count", 0)),
            expected=_int(expectations.get("min_run_count", 0)),
        )
    for expectation_name, matrix_field in (
        (
            "min_repair_manifest_run_context_count",
            "repair_manifest_run_context_count",
        ),
        (
            "min_repair_manifest_setup_repair_run_count",
            "repair_manifest_setup_repair_run_names",
        ),
        (
            "min_repair_manifest_checkout_only_run_count",
            "repair_manifest_checkout_only_run_names",
        ),
    ):
        if expectations.get(expectation_name) is None:
            continue
        actual_value = matrix.get(matrix_field)
        actual = (
            len(_list(actual_value))
            if isinstance(actual_value, list)
            else _int(actual_value)
        )
        _append_min_check(
            checks,
            name=expectation_name,
            actual=actual,
            expected=_int(expectations.get(expectation_name, 0)),
        )
    for field, check_name in (
        ("status_counts", "min_status_counts"),
        ("primary_layer_counts", "min_primary_layer_counts"),
        ("primary_blocker_counts", "min_primary_blocker_counts"),
        (
            "repair_summary_status_counts",
            "min_repair_summary_status_counts",
        ),
        (
            "repair_summary_conclusion_counts",
            "min_repair_summary_conclusion_counts",
        ),
    ):
        _append_min_count_checks(
            checks,
            name=check_name,
            actual_counts=_dict(matrix.get(field)),
            expected_counts=_dict(expectations.get(check_name)),
        )
    diagnoses_by_name = {
        str(item.get("name") or ""): _dict(item)
        for item in _list(matrix.get("run_diagnoses"))
        if str(item.get("name") or "")
    }
    for run_name, raw_expectation in sorted(
        _dict(expectations.get("run_expectations")).items()
    ):
        expectation = _dict(raw_expectation)
        diagnosis = _dict(diagnoses_by_name.get(str(run_name)))
        if not diagnosis:
            checks.append(
                {
                    "name": f"run:{run_name}:present",
                    "passed": False,
                    "expected": "present",
                    "actual": "missing",
                }
            )
            continue
        _append_allowed_or_exact_check(
            checks,
            name=f"run:{run_name}:status",
            actual=str(diagnosis.get("status") or ""),
            exact=str(expectation.get("status") or ""),
            allowed=[str(item) for item in _list(expectation.get("allowed_statuses"))],
        )
        _append_allowed_or_exact_check(
            checks,
            name=f"run:{run_name}:primary_layer",
            actual=str(diagnosis.get("primary_layer") or ""),
            exact=str(expectation.get("primary_layer") or ""),
            allowed=[
                str(item) for item in _list(expectation.get("allowed_primary_layers"))
            ],
        )
        _append_allowed_or_exact_check(
            checks,
            name=f"run:{run_name}:primary_blocker",
            actual=str(diagnosis.get("primary_blocker") or ""),
            exact=str(expectation.get("primary_blocker") or ""),
            allowed=[
                str(item) for item in _list(expectation.get("allowed_primary_blockers"))
            ],
            allowed_prefixes=[
                str(item)
                for item in _list(expectation.get("allowed_primary_blocker_prefixes"))
            ],
        )
        _append_allowed_or_exact_check(
            checks,
            name=f"run:{run_name}:repair_summary_status",
            actual=str(diagnosis.get("repair_summary_status") or ""),
            exact=str(expectation.get("repair_summary_status") or ""),
            allowed=[
                str(item)
                for item in _list(expectation.get("allowed_repair_summary_statuses"))
            ],
        )
        _append_allowed_or_exact_check(
            checks,
            name=f"run:{run_name}:repair_summary_conclusion",
            actual=str(diagnosis.get("repair_summary_conclusion") or ""),
            exact=str(expectation.get("repair_summary_conclusion") or ""),
            allowed=[
                str(item)
                for item in _list(
                    expectation.get("allowed_repair_summary_conclusions")
                )
            ],
        )
        _append_contains_check(
            checks,
            name=f"run:{run_name}:repair_summary_path",
            actual=str(diagnosis.get("repair_summary_path") or ""),
            expected_substring=str(
                expectation.get("repair_summary_path_contains") or ""
            ),
        )
        _append_allowed_or_exact_check(
            checks,
            name=f"run:{run_name}:repair_manifest_blocker",
            actual=str(diagnosis.get("repair_manifest_blocker") or ""),
            exact=str(expectation.get("repair_manifest_blocker") or ""),
            allowed=[
                str(item)
                for item in _list(
                    expectation.get("allowed_repair_manifest_blockers")
                )
            ],
            allowed_prefixes=[
                str(item)
                for item in _list(
                    expectation.get(
                        "allowed_repair_manifest_blocker_prefixes"
                    )
                )
            ],
        )
        _append_bool_check(
            checks,
            name=f"run:{run_name}:repair_manifest_setup_repair",
            actual=bool(diagnosis.get("repair_manifest_setup_repair", False)),
            expected=expectation.get("repair_manifest_setup_repair"),
        )
        _append_contains_check(
            checks,
            name=f"run:{run_name}:repair_manifest_next_action",
            actual=str(diagnosis.get("repair_manifest_next_action") or ""),
            expected_substring=str(
                expectation.get("repair_manifest_next_action_contains") or ""
            ),
        )
    failed_count = sum(1 for check in checks if not bool(check.get("passed", False)))
    return {
        "present": True,
        "passed": failed_count == 0,
        "check_count": len(checks),
        "failed_count": failed_count,
        "checks": checks,
    }


def _repository_processing_failed_expectation_checks(
    report: dict[str, Any],
    *,
    limit: int | None = None,
) -> list[dict[str, str]]:
    failed: list[dict[str, str]] = []
    for raw_check in _list(report.get("checks")):
        check = _dict(raw_check)
        if bool(check.get("passed", False)):
            continue
        failed.append(
            {
                "name": str(check.get("name") or ""),
                "expected": str(check.get("expected") or ""),
                "actual": str(check.get("actual") or ""),
            }
        )
        if limit is not None and len(failed) >= limit:
            break
    return failed


def _pipeline_repository_processing_acceptance_report(
    summary: dict[str, Any],
) -> dict[str, Any]:
    expectation_report = _dict(
        summary.get("repository_processing_expectation_report")
    )
    expectation_present = bool(expectation_report.get("present", False))
    expectation_passed = bool(expectation_report.get("passed", True))
    promotion_status = str(summary.get("promotion_status") or "")
    promotion_promotable = bool(summary.get("promotion_promotable", False))
    if expectation_present:
        passed = expectation_passed
        reason = (
            "repository_processing_expectations_passed"
            if passed
            else "repository_processing_expectations_failed"
        )
        mode = "repository_processing_expectations"
    elif promotion_status:
        passed = promotion_promotable
        reason = (
            "promotion_gate_promotable"
            if passed
            else f"promotion_gate_{promotion_status}"
        )
        mode = "promotion_gate"
    else:
        passed = False
        reason = "repository_processing_acceptance_not_configured"
        mode = "not_configured"
    return {
        "passed": passed,
        "mode": mode,
        "reason": reason,
        "expectation_present": expectation_present,
        "expectation_passed": expectation_passed,
        "expectation_check_count": _int(
            expectation_report.get("check_count", 0)
        ),
        "expectation_failed_count": _int(
            expectation_report.get("failed_count", 0)
        ),
        "promotion_status": promotion_status,
        "promotion_promotable": promotion_promotable,
    }


def _append_min_check(
    checks: list[dict[str, Any]],
    *,
    name: str,
    actual: int,
    expected: int,
) -> None:
    checks.append(
        {
            "name": name,
            "passed": actual >= expected,
            "expected": f">={expected}",
            "actual": str(actual),
        }
    )


def _append_min_count_checks(
    checks: list[dict[str, Any]],
    *,
    name: str,
    actual_counts: dict[str, Any],
    expected_counts: dict[str, Any],
) -> None:
    for key, expected_value in sorted(expected_counts.items()):
        actual = _int(actual_counts.get(str(key), 0))
        expected = _int(expected_value)
        _append_min_check(
            checks,
            name=f"{name}:{key}",
            actual=actual,
            expected=expected,
        )


def _append_allowed_or_exact_check(
    checks: list[dict[str, Any]],
    *,
    name: str,
    actual: str,
    exact: str = "",
    allowed: list[str] | None = None,
    allowed_prefixes: list[str] | None = None,
) -> None:
    allowed_values = [value for value in (allowed or []) if value]
    prefix_values = [value for value in (allowed_prefixes or []) if value]
    if not exact and not allowed_values and not prefix_values:
        return
    if exact:
        passed = actual == exact
        expected = exact
    else:
        passed = actual in allowed_values or any(
            actual.startswith(prefix) for prefix in prefix_values
        )
        expected_values = list(allowed_values)
        expected_values.extend(f"{prefix}*" for prefix in prefix_values)
        expected = ", ".join(expected_values)
    checks.append(
        {
            "name": name,
            "passed": passed,
            "expected": expected,
            "actual": actual,
        }
    )


def _append_bool_check(
    checks: list[dict[str, Any]],
    *,
    name: str,
    actual: bool,
    expected: Any,
) -> None:
    if expected is None:
        return
    expected_bool = _bool(expected)
    checks.append(
        {
            "name": name,
            "passed": actual is expected_bool,
            "expected": str(expected_bool).lower(),
            "actual": str(actual).lower(),
        }
    )


def _append_contains_check(
    checks: list[dict[str, Any]],
    *,
    name: str,
    actual: str,
    expected_substring: str,
) -> None:
    if not expected_substring:
        return
    checks.append(
        {
            "name": name,
            "passed": expected_substring in actual,
            "expected": f"contains:{expected_substring}",
            "actual": actual,
        }
    )


def _pipeline_repository_processing_run_diagnosis(
    run_summary: dict[str, Any],
) -> dict[str, str]:
    name = str(run_summary.get("name") or "")
    final_status = str(run_summary.get("final_status") or "")
    final_reason = str(run_summary.get("final_reason") or "")
    execution_status = str(run_summary.get("execution_status") or "")
    execution_command = str(run_summary.get("execution_command") or "")
    failure_category = str(run_summary.get("failure_category") or "")
    setup_failure = str(run_summary.get("setup_install_failure_category") or "")
    setup_doctor_status = str(run_summary.get("setup_doctor_status") or "")
    setup_doctor_blocker = str(run_summary.get("setup_doctor_blocker") or "")
    setup_doctor_next_action = str(
        run_summary.get("setup_doctor_next_action") or ""
    )
    patch_status = str(run_summary.get("patch_validation_status") or "")
    patch_success_count = _int(run_summary.get("patch_validation_success_count", 0))
    repair_summary_status = str(run_summary.get("repair_summary_status") or "")
    repair_summary_reason = str(run_summary.get("repair_summary_reason") or "")
    repair_summary_conclusion = str(
        run_summary.get("repair_summary_conclusion") or ""
    )
    repair_summary_path = str(run_summary.get("repair_summary_path") or "")
    repair_summary_text = _repository_processing_repair_summary_text(
        status=repair_summary_status,
        reason=repair_summary_reason,
        conclusion=repair_summary_conclusion,
        path=repair_summary_path,
    )
    repair_summary_fields = {
        "repair_summary": repair_summary_text,
        "repair_summary_status": repair_summary_status,
        "repair_summary_reason": repair_summary_reason,
        "repair_summary_conclusion": repair_summary_conclusion,
        "repair_summary_path": repair_summary_path,
    }
    phase2_ready = bool(run_summary.get("phase2_ready", False))
    phase3_ready = bool(run_summary.get("phase3_validation_ready", False))
    retry_next_action = _repository_processing_retry_next_action(run_summary)
    evidence = (
        f"final={final_status or 'none'}, execution={execution_status or 'none'}, "
        f"failure={failure_category or 'none'}, setup={setup_failure or 'none'}, "
        f"setup_doctor={setup_doctor_status or 'none'}:"
        f"{setup_doctor_blocker or 'none'}, "
        f"patch={patch_status or 'none'}, patch_successes={patch_success_count}"
    )

    if final_status == "repaired" or patch_success_count > 0:
        return {
            "name": name,
            "status": "repaired",
            "primary_layer": "repository_repair",
            "primary_blocker": "none",
            **repair_summary_fields,
            "evidence": evidence,
            "next_action": "Keep repository repair artifacts as verified Phase 3 evidence.",
            "command": execution_command,
        }
    if setup_failure:
        return {
            "name": name,
            "status": "blocked",
            "primary_layer": "repository_test_setup",
            "primary_blocker": f"setup_install_failure:{setup_failure}",
            **repair_summary_fields,
            "evidence": evidence,
            "next_action": _repository_repair_setup_failure_action(setup_failure),
            "command": execution_command,
        }
    if (
        setup_doctor_blocker
        and setup_doctor_blocker != "none"
        and _repository_test_setup_doctor_manifest_recommended(
            setup_doctor_blocker
        )
    ):
        return {
            "name": name,
            "status": "blocked",
            "primary_layer": _repository_test_setup_doctor_blocker_layer(
                setup_doctor_blocker
            ),
            "primary_blocker": setup_doctor_blocker,
            **repair_summary_fields,
            "evidence": evidence,
            "next_action": (
                setup_doctor_next_action
                or _repository_test_setup_doctor_blocker_action(
                    setup_doctor_blocker
                )
            ),
            "command": execution_command,
        }
    if final_status == "phase3_ready" or phase3_ready:
        blocker = (
            f"patch_validation:{patch_status}"
            if patch_status
            else "patch_validation:not_executed"
        )
        return {
            "name": name,
            "status": "phase3_ready",
            "primary_layer": "repository_repair",
            "primary_blocker": blocker,
            **repair_summary_fields,
            "evidence": evidence,
            "next_action": "Run or inspect repository_test_patch_validation artifacts.",
            "command": execution_command,
        }
    if final_status == "phase2_ready" or phase2_ready:
        return {
            "name": name,
            "status": "phase2_ready",
            "primary_layer": "fault_localization",
            "primary_blocker": "patch_generation_not_executed",
            **repair_summary_fields,
            "evidence": evidence,
            "next_action": "Run patch candidate generation and sandbox validation for localized functions.",
            "command": execution_command,
        }
    if final_status == "blocked":
        layer, next_action = _pipeline_repository_processing_blocked_layer(
            final_reason,
            failure_category=failure_category,
        )
        return {
            "name": name,
            "status": "blocked",
            "primary_layer": layer,
            "primary_blocker": final_reason or "blocked",
            **repair_summary_fields,
            "evidence": evidence,
            "next_action": retry_next_action or next_action,
            "command": execution_command,
        }
    if execution_status:
        return {
            "name": name,
            "status": "incomplete",
            "primary_layer": "repository_test_execution",
            "primary_blocker": f"execution_status:{execution_status}",
            **repair_summary_fields,
            "evidence": evidence,
            "next_action": "Inspect repository_test_execution_result and retry plan.",
            "command": execution_command,
        }
    return {
        "name": name,
        "status": "incomplete",
        "primary_layer": "repository_test_setup",
        "primary_blocker": "repository_test_not_executed",
        **repair_summary_fields,
        "evidence": evidence,
        "next_action": "Run checkout-enabled repository tests to collect dynamic evidence.",
        "command": execution_command,
    }


def _repository_processing_repair_summary_text(
    *,
    status: str,
    reason: str,
    conclusion: str,
    path: str,
) -> str:
    if not any((status, reason, conclusion, path)):
        return "none"
    text = f"{status or 'none'}/{reason or 'none'}"
    if conclusion:
        text = f"{text}:{conclusion}"
    if path:
        text = f"{text}; path={path}"
    return text


def _pipeline_repository_processing_blocked_layer(
    final_reason: str,
    *,
    failure_category: str = "",
) -> tuple[str, str]:
    if final_reason.startswith("dynamic_evidence_not_usable:not_executed"):
        return (
            "repository_test_execution",
            "Enable --checkout-repository-tests or provide --repository-test-root, then rerun the pipeline.",
        )
    if final_reason.startswith("dynamic_evidence_not_usable"):
        return (
            "repository_test_execution",
            "Inspect repository_test_execution_result and retry plan for usable failing tests.",
        )
    if final_reason == "repository_tests_passing":
        return (
            "repository_test_execution",
            "Review failure-overlay candidates when natural tests pass without dynamic failures.",
        )
    if final_reason.startswith("framework_configuration"):
        return (
            "repository_test_execution",
            "Inspect framework environment variables and repository test configuration.",
        )
    if failure_category:
        return (
            "repository_test_execution",
            "Inspect repository_test_execution_result and retry plan for the classified failure.",
        )
    return (
        "repository_repair",
        "Inspect repository_test_run_summaries for the first blocked repair step.",
    )


def _repository_processing_retry_next_action(run_summary: dict[str, Any]) -> str:
    if not bool(run_summary.get("retry_recommended", False)):
        return ""
    strategy = str(run_summary.get("retry_strategy") or "").strip()
    command = str(run_summary.get("retry_command") or "").strip()
    if command and strategy:
        return (
            f"Run repository test retry `{command}` using strategy `{strategy}` "
            "to collect dynamic evidence."
        )
    if command:
        return f"Run repository test retry `{command}` to collect dynamic evidence."
    if strategy:
        return (
            f"Apply repository test retry strategy `{strategy}` to collect "
            "dynamic evidence."
        )
    return "Inspect repository_test_retry_plan for a recommended retry action."


def _pipeline_repository_processing_diagnosis(
    *,
    summary: dict[str, Any],
    stage_audit: list[Any],
) -> dict[str, Any]:
    stages = [_dict(stage) for stage in stage_audit]
    stage_by_name = {
        str(stage.get("stage") or ""): stage
        for stage in stages
        if str(stage.get("stage") or "")
    }
    setup_failure_counts = _dict(
        summary.get("repository_test_environment_setup_install_failure_counts")
    )
    setup_doctor_status_counts = _repository_test_setup_doctor_status_counts(
        summary
    )
    setup_doctor_blocker_counts = _repository_test_setup_doctor_blocker_counts(
        summary
    )
    failure_category_counts = _dict(summary.get("repository_test_failure_category_counts"))
    blocked_reason_counts = _dict(summary.get("repository_test_blocked_reason_counts"))
    execution_status_counts = _dict(
        summary.get("repository_test_execution_status_counts")
    )
    final_status_counts = _dict(summary.get("repository_test_final_status_counts"))
    top_setup_failure = _top_count_key(setup_failure_counts)
    top_setup_doctor_blocker = _top_count_key(setup_doctor_blocker_counts)
    actionable_setup_doctor_blocker = (
        top_setup_doctor_blocker
        if _repository_test_setup_doctor_manifest_recommended(
            top_setup_doctor_blocker
        )
        else ""
    )
    top_setup_doctor_layer = _repository_test_setup_doctor_blocker_layer(
        actionable_setup_doctor_blocker
    )
    top_failure_category = _top_count_key(failure_category_counts)
    top_blocked_reason = _top_count_key(blocked_reason_counts)
    repair_command = str(summary.get("repository_repair_manifest_command") or "")
    repaired_count = _int(final_status_counts.get("repaired", 0))
    patch_success_runs = _int(
        summary.get("repository_test_patch_validation_success_run_count", 0)
    )

    layers: list[dict[str, str]] = []
    for stage_name in ("preflight", "smoke_benchmark"):
        stage = _dict(stage_by_name.get(stage_name))
        if stage:
            layers.append(
                {
                    "layer": stage_name,
                    "status": str(stage.get("status") or "skip"),
                    "blocker": _stage_blocker(stage),
                    "evidence": str(stage.get("evidence") or ""),
                    "next_action": str(stage.get("next_action") or ""),
                    "command": "",
                }
            )

    setup_status = "pass"
    setup_blocker = "none"
    setup_evidence = (
        f"install_failures={_format_counts(setup_failure_counts)}, "
        f"doctor_statuses={_format_counts(setup_doctor_status_counts)}, "
        f"doctor_blockers={_format_counts(setup_doctor_blocker_counts)}, "
        "fallbacks="
        f"{_int(summary.get('repository_test_environment_setup_install_fallback_count', 0))}/"
        f"{_int(summary.get('repository_test_environment_setup_install_fallback_success_count', 0))} succeeded"
    )
    setup_next_action = "Keep setup diagnostics as repository-test environment evidence."
    setup_command = ""
    if top_setup_failure:
        setup_status = "warn"
        setup_blocker = f"setup_install_failure:{top_setup_failure}"
        setup_next_action = (
            str(summary.get("repository_repair_manifest_setup_repair_next_action") or "")
            or _repository_repair_setup_failure_action(top_setup_failure)
        )
        setup_command = repair_command
    elif (
        actionable_setup_doctor_blocker
        and top_setup_doctor_layer == "repository_test_setup"
    ):
        setup_status = "warn"
        setup_blocker = actionable_setup_doctor_blocker
        setup_next_action = (
            str(summary.get("repository_repair_manifest_setup_doctor_next_action") or "")
            or _repository_test_setup_doctor_blocker_action(
                actionable_setup_doctor_blocker
            )
        )
        setup_command = repair_command
    elif not setup_failure_counts and not execution_status_counts:
        setup_status = "skip"
        setup_next_action = "Run repository-test setup/execution to collect dynamic evidence."
    layers.append(
        {
            "layer": "repository_test_setup",
            "status": setup_status,
            "blocker": setup_blocker,
            "evidence": setup_evidence,
            "next_action": setup_next_action,
            "command": setup_command,
        }
    )

    execution_status = "pass" if execution_status_counts else "skip"
    execution_blocker = "none"
    execution_next_action = "Keep repository-test execution results as dynamic evidence."
    execution_command = ""
    if top_blocked_reason.startswith("dynamic_evidence_not_usable"):
        execution_status = "warn"
        execution_blocker = top_blocked_reason
        execution_next_action = str(
            summary.get("repository_repair_stage_next_action") or ""
        )
        execution_command = repair_command
    elif (
        actionable_setup_doctor_blocker
        and top_setup_doctor_layer == "repository_test_execution"
    ):
        execution_status = "warn"
        execution_blocker = actionable_setup_doctor_blocker
        execution_next_action = (
            str(summary.get("repository_repair_manifest_setup_doctor_next_action") or "")
            or _repository_test_setup_doctor_blocker_action(
                actionable_setup_doctor_blocker
            )
        )
        execution_command = repair_command
    elif top_failure_category:
        execution_status = "warn"
        execution_blocker = f"test_failure_category:{top_failure_category}"
        execution_next_action = (
            "Inspect repository_test_execution_result and retry plan for usable "
            "failing tests."
        )
    elif not execution_status_counts:
        execution_next_action = "Run checkout-enabled repository tests to collect execution evidence."
        execution_command = repair_command
    layers.append(
        {
            "layer": "repository_test_execution",
            "status": execution_status,
            "blocker": execution_blocker,
            "evidence": (
                f"execution={_format_counts(execution_status_counts)}, "
                f"failure_categories={_format_counts(failure_category_counts)}"
            ),
            "next_action": execution_next_action,
            "command": execution_command,
        }
    )

    repair_stage = _dict(stage_by_name.get("repository_repair"))
    if repair_stage:
        repair_blocker = top_blocked_reason or _stage_blocker(repair_stage)
        layers.append(
            {
                "layer": "repository_repair",
                "status": str(repair_stage.get("status") or "skip"),
                "blocker": repair_blocker or "none",
                "evidence": str(repair_stage.get("evidence") or ""),
                "next_action": str(repair_stage.get("next_action") or ""),
                "command": repair_command,
            }
        )

    recommendation_stage = _dict(stage_by_name.get("recommendation_rerun"))
    if recommendation_stage:
        layers.append(
            {
                "layer": "recommendation_rerun",
                "status": str(recommendation_stage.get("status") or "skip"),
                "blocker": _stage_blocker(recommendation_stage),
                "evidence": str(recommendation_stage.get("evidence") or ""),
                "next_action": str(recommendation_stage.get("next_action") or ""),
                "command": "",
            }
        )

    primary = _pipeline_repository_processing_primary_layer(
        layers,
        repaired=bool(repaired_count > 0 or patch_success_runs > 0),
    )
    status = _repository_processing_status(primary, repaired_count, patch_success_runs)
    return {
        "status": status,
        "primary_layer": str(primary.get("layer") or "none"),
        "primary_blocker": str(primary.get("blocker") or "none"),
        "evidence": str(primary.get("evidence") or ""),
        "next_action": str(primary.get("next_action") or ""),
        "command": str(primary.get("command") or ""),
        "layers": layers,
    }


def _pipeline_repository_processing_primary_layer(
    layers: list[dict[str, str]],
    *,
    repaired: bool,
) -> dict[str, str]:
    if repaired:
        for layer in layers:
            if layer.get("layer") == "repository_repair":
                return layer
    for preferred_layer in (
        "repository_test_setup",
        "repository_test_execution",
        "repository_repair",
        "smoke_benchmark",
        "preflight",
        "recommendation_rerun",
    ):
        for layer in layers:
            if (
                layer.get("layer") == preferred_layer
                and layer.get("status") in {"fail", "warn"}
                and str(layer.get("blocker") or "none") != "none"
            ):
                return layer
    for layer in layers:
        if layer.get("status") in {"fail", "warn"}:
            return layer
    for layer in layers:
        if layer.get("status") == "skip":
            return layer
    return layers[0] if layers else {"layer": "none", "status": "skip"}


def _repository_processing_status(
    primary: dict[str, str],
    repaired_count: int,
    patch_success_runs: int,
) -> str:
    if repaired_count > 0 or patch_success_runs > 0:
        return "repaired"
    primary_status = str(primary.get("status") or "")
    primary_blocker = str(primary.get("blocker") or "none")
    if primary_status == "fail":
        return "blocked"
    if primary_status == "warn" and primary_blocker != "none":
        return "blocked"
    if primary_status == "warn":
        return "review"
    if primary_status == "skip":
        return "incomplete"
    return "ready"


def _stage_blocker(stage: dict[str, Any]) -> str:
    status = str(stage.get("status") or "")
    evidence = str(stage.get("evidence") or "")
    if status not in {"fail", "warn"}:
        return "none"
    for key in (
        "top_blocked_reason",
        "top_setup_install_failure",
        "top_issue",
        "gap",
        "status",
    ):
        marker = f"{key}="
        if marker not in evidence:
            continue
        value = evidence.split(marker, 1)[1].split(",", 1)[0].strip()
        if value and value != "none":
            return f"{key}:{value}"
    return status


def _repo_run_name(repo_spec: str) -> str:
    value = repo_spec.strip().removesuffix(".git")
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    value = value.strip("/")
    if not value:
        return "single_repo"
    return "".join(char if char.isalnum() else "_" for char in value).strip("_")


def _format_name_list(value: Any) -> str:
    names = [str(item) for item in _list(value)]
    return ", ".join(names) if names else "none"


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(
        f"{key}={_int(value)}" for key, value in sorted(counts.items())
    )


def _format_key_values(values: dict[str, Any]) -> str:
    if not values:
        return "none"
    formatted: list[str] = []
    for key, value in sorted(values.items()):
        if isinstance(value, bool):
            rendered = str(value).lower()
        elif isinstance(value, float):
            rendered = f"{value:.4f}"
        else:
            rendered = str(value)
        formatted.append(f"{key}={rendered}")
    return ", ".join(formatted)


def _format_expectation_check(value: Any) -> str:
    check = _dict(value)
    name = str(check.get("name") or "")
    if not name:
        return "none"
    expected = str(check.get("expected") or "")
    actual = str(check.get("actual") or "")
    return f"{name} expected={expected} actual={actual}"


def _format_expectation_checks(value: Any) -> str:
    checks = [
        _format_expectation_check(check)
        for check in _list(value)
        if _dict(check)
    ]
    return "; ".join(checks) if checks else "none"


def _signed_int(value: Any) -> str:
    number = _int(value)
    return f"+{number}" if number >= 0 else str(number)


def _signed_float(value: Any) -> str:
    number = _float(value)
    sign = "+" if number >= 0 else ""
    return f"{sign}{number:.4f}"


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


if __name__ == "__main__":
    main()
