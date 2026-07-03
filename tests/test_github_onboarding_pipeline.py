import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from code_intelligence_agent.evaluation.github_onboarding_pipeline import (
    GitHubOnboardingPipelineReport,
    _build_repository_repair_manifest,
    _pipeline_cli_exit_code,
    _pipeline_repository_processing_acceptance_report,
    _pipeline_repository_processing_expectation_report,
    _pipeline_repository_processing_expectations,
    _pipeline_repository_processing_matrix,
    _pipeline_repository_processing_diagnosis,
    _pipeline_repository_repair_stage,
    _pipeline_summary,
    _repository_test_details_from_onboarding_report,
    _repository_test_final_diagnosis as pipeline_repository_test_final_diagnosis,
    _should_write_repository_repair_manifest,
    build_github_onboarding_pipeline_showcase,
    build_single_repo_pipeline_manifest,
    main as pipeline_main,
    render_github_onboarding_pipeline_showcase_markdown,
    render_github_onboarding_pipeline_markdown,
    render_repository_repair_manifest_markdown,
    run_github_onboarding_pipeline,
)
from code_intelligence_agent.evaluation.github_onboarding_preflight_batch import (
    GitHubOnboardingPreflightBatchReport,
)
from code_intelligence_agent.evaluation.github_onboarding_smoke_runner import (
    OnboardingSmokeRunnerReport,
    OnboardingSmokeRunResult,
)
from code_intelligence_agent.evaluation.onboarding_smoke_validator import (
    OnboardingSmokeSuiteReport,
)


def test_pipeline_runs_preflight_batch_then_smoke_runner():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        ready_discovery = _write_average_discovery(root)
        docs_discovery = _write_docs_discovery(root)
        manifest = root / "pipeline_manifest.json"
        output_dir = root / "pipeline_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "github_pipeline_smoke",
                    "defaults": {
                        "mode": "from-discovery",
                        "sample_sources": 5,
                    },
                    "runs": [
                        {
                            "name": "average_ready",
                            "discovery": ready_discovery.name,
                        },
                        {
                            "name": "docs_only",
                            "discovery": docs_discovery.name,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = run_github_onboarding_pipeline(manifest, output_dir)
        markdown = render_github_onboarding_pipeline_markdown(report)

        assert report.passed is True
        assert report.summary["preflight_run_count"] == 2
        assert report.summary["preflight_ready_count"] == 1
        assert report.summary["preflight_skipped_count"] == 1
        assert report.summary["preflight_readiness_rate"] == 0.5
        assert report.summary["preflight_ready_run_names"] == ["average_ready"]
        assert report.summary["preflight_skipped_run_names"] == ["docs_only"]
        assert report.summary["preflight_error_run_names"] == []
        assert report.summary["preflight_top_issue_code"] == "no_python_sources"
        assert report.summary["preflight_profile_doctor_status_counts"] == {
            "fail": 1,
            "warn": 1,
        }
        assert report.summary["preflight_profile_doctor_blocker_counts"] == {
            "python_sources": 1,
            "test_or_config_signal": 1,
        }
        assert report.summary["preflight_top_profile_doctor_blocker"] == (
            "python_sources"
        )
        assert report.summary["smoke_present"] is True
        assert report.summary["smoke_run_count"] == 1
        assert report.summary["smoke_generated_candidates"] >= 1
        assert report.summary["smoke_benchmark_report_count"] == 1
        assert report.summary["smoke_benchmark_patch_success_run_count"] == 1
        assert report.summary["smoke_benchmark_patch_success_rate"] == 1.0
        assert report.summary["smoke_benchmark_top1"] == 1.0
        assert report.summary["smoke_benchmark_map"] == 1.0
        assert report.summary["smoke_auto_remediation_attempted_count"] == 0
        assert report.summary["smoke_auto_remediation_used_count"] == 0
        assert report.summary["smoke_auto_remediation_improved_count"] == 0
        assert report.summary["smoke_auto_remediation_benchmark_case_delta"] == 0
        assert report.summary["smoke_auto_remediation_action_counts"] == {}
        assert report.summary["repository_test_report_count"] == 1
        assert report.summary["repository_test_phase2_ready_count"] == 0
        assert report.summary["repository_test_phase3_validation_ready_count"] == 0
        assert report.summary["promotion_status"] == "review"
        assert report.summary["promotion_promotable"] is False
        assert report.summary["promotion_blocking_reasons"] == []
        assert report.summary["promotion_warning_reasons"] == [
            "Warning stages: preflight."
        ]
        assert report.summary["pipeline_stage_statuses"]["repository_repair"] == (
            "skip"
        )
        assert report.summary["repository_repair_stage_status"] == "skip"
        assert "top_blocked_reason=dynamic_evidence_not_usable:not_executed" in (
            report.summary["repository_repair_stage_evidence"]
        )
        assert report.summary["repository_repair_stage_next_action"] == (
            "Enable --checkout-repository-tests or provide --repository-test-root, "
            "then rerun the pipeline."
        )
        criteria = report.summary["promotion_criteria"]
        assert criteria["pipeline_passed"] is True
        assert criteria["smoke_has_cases"] is True
        assert criteria["smoke_benchmark_cases"] >= 1
        assert criteria["min_smoke_benchmark_cases"] == 1
        assert criteria["smoke_case_threshold_met"] is True
        assert criteria["fallback_recovered_count"] == 0
        assert criteria["min_fallback_recovered_count"] == 0
        assert criteria["fallback_recovered_threshold_met"] is True
        assert criteria["preflight_readiness_rate"] == 0.5
        assert criteria["min_readiness_rate"] == 0.0
        assert criteria["readiness_threshold_met"] is True
        assert criteria["has_stage_blockers"] is False
        assert criteria["warning_stage_count"] == 1
        assert criteria["allow_warning_stages"] is False
        assert criteria["recommendation_regression_count"] == 0
        assert criteria["fail_on_recommendation_regression"] is True
        assert report.summary["recommended_pipeline_manifest_present"] is False
        assert report.summary["recommended_pipeline_manifest_path"] == ""
        assert report.summary["repository_repair_manifest_present"] is False
        assert report.summary["repository_repair_manifest_path"] == ""
        assert report.summary["repository_repair_manifest_markdown_path"] == ""
        assert report.summary["repository_repair_manifest_command"] == ""
        assert report.summary["promotion_config"] == {
            "min_readiness_rate": 0.0,
            "min_smoke_benchmark_cases": 1,
            "min_fallback_recovered_count": 0,
            "min_repository_test_phase2_ready_count": 0,
            "min_repository_test_phase3_validation_ready_count": 0,
            "min_repository_test_repaired_count": 0,
            "allow_warning_stages": False,
            "fail_on_recommendation_regression": True,
        }
        assert report.smoke_runner is not None
        assert report.pipeline_showcase is not None
        assert report.pipeline_showcase["artifact_kind"] == (
            "github_onboarding_pipeline_showcase"
        )
        assert report.pipeline_showcase["headline"]["preflight_readiness_rate"] == 0.5
        assert report.pipeline_showcase["readiness"]["ready_run_names"] == [
            "average_ready"
        ]
        assert report.pipeline_showcase["readiness"]["skipped_run_names"] == [
            "docs_only"
        ]
        assert report.pipeline_showcase["smoke_evidence"][
            "generated_candidates"
        ] >= 1
        assert report.pipeline_showcase["headline"][
            "smoke_benchmark_patch_success_rate"
        ] == 1.0
        assert report.pipeline_showcase["smoke_evidence"][
            "benchmark_patch_success_rate"
        ] == 1.0
        assert report.pipeline_showcase["headline"][
            "smoke_auto_remediation_used_count"
        ] == 0
        assert report.pipeline_showcase["smoke_evidence"][
            "auto_remediation_used_count"
        ] == 0
        assert report.pipeline_showcase["recommendation"][
            "pipeline_manifest_present"
        ] is False
        assert report.pipeline_showcase["recommendation"][
            "pipeline_manifest_path"
        ] == ""
        stage_status = {
            row["stage"]: row["status"]
            for row in report.pipeline_showcase["stage_audit"]
        }
        assert stage_status == {
            "preflight": "warn",
            "smoke_benchmark": "pass",
            "repository_repair": "skip",
            "recommendation_rerun": "skip",
        }
        repository_repair_stage = {
            row["stage"]: row for row in report.pipeline_showcase["stage_audit"]
        }["repository_repair"]
        assert "reports=1" in repository_repair_stage["evidence"]
        assert "top_blocked_reason=dynamic_evidence_not_usable:not_executed" in (
            repository_repair_stage["evidence"]
        )
        assert repository_repair_stage["next_action"] == (
            "Enable --checkout-repository-tests or provide --repository-test-root, "
            "then rerun the pipeline."
        )
        diagnosis = report.pipeline_showcase["repository_processing_diagnosis"]
        assert diagnosis["status"] == "blocked"
        assert diagnosis["primary_layer"] == "repository_test_execution"
        assert diagnosis["primary_blocker"] == (
            "dynamic_evidence_not_usable:not_executed"
        )
        assert diagnosis["next_action"] == (
            "Enable --checkout-repository-tests or provide --repository-test-root, "
            "then rerun the pipeline."
        )
        assert report.summary["repository_processing_status"] == "blocked"
        assert report.summary["repository_processing_primary_layer"] == (
            "repository_test_execution"
        )
        assert report.pipeline_showcase["stage_summary"] == {
            "stage_count": 4,
            "status_counts": {"pass": 1, "skip": 2, "warn": 1},
            "pass_stage_names": ["smoke_benchmark"],
            "warn_stage_names": ["preflight"],
            "fail_stage_names": [],
            "skip_stage_names": ["repository_repair", "recommendation_rerun"],
            "blocking_stage_names": [],
            "warning_stage_names": ["preflight"],
            "has_blockers": False,
            "needs_attention": True,
        }
        assert report.pipeline_showcase["promotion_gate"]["status"] == "review"
        assert report.pipeline_showcase["promotion_gate"]["promotable"] is False
        assert report.pipeline_showcase["promotion_gate"]["blocking_reasons"] == []
        assert report.pipeline_showcase["promotion_gate"]["warning_reasons"] == [
            "Warning stages: preflight."
        ]
        assert report.pipeline_showcase["promotion_gate"]["criteria"] == criteria
        assert report.pipeline_showcase["promotion_config"] == report.summary[
            "promotion_config"
        ]
        assert report.pipeline_showcase["next_actions"] == [
            "Inspect skipped runs and tune include/exclude or recipe selection."
        ]
        assert report.pipeline_showcase["resume_bullets"]
        assert report.output_paths["input_manifest"] == str(manifest)
        assert Path(report.output_paths["pipeline_json"]).exists()
        assert Path(report.output_paths["pipeline_markdown"]).exists()
        assert Path(report.output_paths["pipeline_showcase_json"]).exists()
        assert Path(report.output_paths["pipeline_showcase_markdown"]).exists()
        assert Path(report.output_paths["preflight_smoke_manifest"]).exists()
        assert Path(report.output_paths["preflight_offline_manifest"]).exists()
        assert Path(report.output_paths["smoke_runner_json"]).exists()
        assert not Path(report.output_paths["recommended_pipeline_manifest"]).exists()
        assert not Path(report.output_paths["repository_repair_manifest"]).exists()
        assert "GitHub Onboarding Pipeline Showcase" in Path(
            report.output_paths["pipeline_showcase_markdown"]
        ).read_text(encoding="utf-8")
        showcase_markdown = Path(
            report.output_paths["pipeline_showcase_markdown"]
        ).read_text(encoding="utf-8")
        assert "Promotion Gate" in showcase_markdown
        assert "Repository Processing Diagnosis" in showcase_markdown
        assert "Repository Processing Matrix" in showcase_markdown
        assert "| Primary Layer | repository_test_execution |" in showcase_markdown
        assert "| Status | review |" in showcase_markdown
        assert "Stage Summary" in showcase_markdown
        assert "Stage Audit" in showcase_markdown
        assert "| preflight | warn |" in showcase_markdown
        assert "GitHub Onboarding Pipeline" in markdown
        assert "Preflight Repository Doctor Statuses: fail=1, warn=1" in markdown
        assert "Preflight Repository Doctor Top Blocker: `python_sources`" in markdown
        assert "Pipeline Showcase" in markdown
        assert "Repository Processing Diagnosis: status=blocked" in markdown
        assert "Repository Processing Next Action:" in markdown
        assert "Pipeline Rerun Command" in markdown
        assert (
            "python -m code_intelligence_agent.evaluation.github_onboarding_pipeline "
            f"{manifest} {output_dir.with_name(output_dir.name + '_rerun')}"
        ) in markdown
        assert "Repository Test Readiness" in markdown
        assert "Smoke Auto Remediation Used Runs: 0" in markdown
        assert "Repository Test Phase 2 Ready Runs" in markdown
        assert "Repository Repair Stage: skip;" in markdown
        assert "Repository Repair Stage | status=skip" in markdown
        assert "Promotion Status: `review`" in markdown
        assert "Recommended Pipeline Manifest Present: false" in markdown
        assert "| Promotion Gate | status=review, promotable=false" in markdown
        assert "| Ready Runs | average_ready |" in markdown
        assert "| Skipped Runs | docs_only |" in markdown
        assert "github_onboarding_smoke_runner" in markdown


def test_pipeline_final_diagnosis_explains_framework_configuration():
    diagnosis = pipeline_repository_test_final_diagnosis(
        {"analysis_source": "none"},
        {
            "execution_status": "fail",
            "failure_category": "framework_configuration_error",
            "framework_configuration_reason": "django_settings_module_detected",
            "framework_environment_variable_count": 1,
        },
    )

    assert diagnosis == {
        "final_status": "blocked",
        "final_reason": (
            "framework_configuration_injected_but_failed:"
            "django_settings_module_detected"
        ),
    }


def test_pipeline_final_diagnosis_prefers_dynamic_failure_category():
    diagnosis = pipeline_repository_test_final_diagnosis(
        {"analysis_source": "none"},
        {
            "execution_status": "fail",
            "failure_category": "tox_missing_python_interpreter",
            "dynamic_failure_category": "timeout",
        },
    )

    assert diagnosis == {
        "final_status": "blocked",
        "final_reason": "dynamic_evidence_not_usable:timeout",
    }


def test_pipeline_details_prefer_effective_execution_result():
    details = _repository_test_details_from_onboarding_report(
        {
            "run_config": {
                "repository_test_execution_result": {
                    "status": "fail",
                    "command": "python -m tox",
                    "failure_category": "tox_missing_python_interpreter",
                },
                "repository_test_effective_execution_result": {
                    "present": True,
                    "status": "pass",
                    "command": "python -m pytest -q tests/test_help.py",
                    "failure_category": "none",
                },
                "repository_test_dynamic_evidence": {
                    "status": "pass",
                    "evidence_level": "passing_tests",
                    "failure_category": "none",
                },
                "repository_test_repair_summary": {
                    "status": "pass",
                    "reason": "repair_ready",
                    "conclusion": "ready_for_review",
                },
            },
            "output_paths": {
                "repository_test_repair_summary_markdown": (
                    "out/repository_test_repair_summary.md"
                )
            }
        }
    )
    diagnosis = pipeline_repository_test_final_diagnosis(
        {"analysis_source": "none"},
        details,
    )

    assert details["execution_status"] == "pass"
    assert details["execution_command"] == "python -m pytest -q tests/test_help.py"
    assert details["failure_category"] == "none"
    assert details["repair_summary_status"] == "pass"
    assert details["repair_summary_reason"] == "repair_ready"
    assert details["repair_summary_conclusion"] == "ready_for_review"
    assert details["repair_summary_path"] == "out/repository_test_repair_summary.md"
    assert diagnosis == {
        "final_status": "blocked",
        "final_reason": "repository_tests_passing",
    }


def test_pipeline_repository_repair_stage_surfaces_setup_install_failures():
    stage = _pipeline_repository_repair_stage(
        {
            "report_count": 2,
            "final_status_counts": {"blocked": 2},
            "blocked_reason_counts": {
                "dynamic_evidence_not_usable:not_executed": 1,
                "dynamic_evidence_not_usable:missing_dependency": 1,
            },
            "execution_status_counts": {"skipped": 1, "fail": 1},
            "setup_install_failure_counts": {
                "missing_requirement_file": 1,
                "editable_backend_unsupported": 1,
            },
            "repository_test_repair_summary_status_counts": {"skipped": 1},
            "repository_test_repair_summary_conclusion_counts": {
                "not_ready": 1
            },
            "phase2_ready_count": 0,
            "phase3_validation_ready_count": 0,
            "patch_validation_success_run_count": 0,
        }
    )

    assert stage["status"] == "warn"
    assert "top_setup_install_failure=editable_backend_unsupported" in stage[
        "evidence"
    ]
    assert "top_repair_summary=skipped/not_ready" in stage["evidence"]
    assert stage["next_action"] == (
        "Inspect repository_test_environment_setup_result; editable install "
        "failed, verify whether the non-editable install fallback succeeded."
    )
    diagnosis = _pipeline_repository_processing_diagnosis(
        summary={
            "repository_test_environment_setup_install_failure_counts": {
                "missing_requirement_file": 1,
                "editable_backend_unsupported": 1,
            },
            "repository_test_environment_setup_install_fallback_count": 1,
            "repository_test_environment_setup_install_fallback_success_count": 0,
            "repository_test_execution_status_counts": {"skipped": 1, "fail": 1},
            "repository_test_failure_category_counts": {"missing_dependency": 1},
            "repository_test_blocked_reason_counts": {
                "dynamic_evidence_not_usable:missing_dependency": 1,
            },
            "repository_test_final_status_counts": {"blocked": 2},
            "repository_repair_stage_next_action": stage["next_action"],
            "repository_repair_manifest_command": (
                "python -m code_intelligence_agent.evaluation.github_onboarding_pipeline "
                "repair_manifest.json repair_output"
            ),
        },
        stage_audit=[stage],
    )

    assert diagnosis["status"] == "blocked"
    assert diagnosis["primary_layer"] == "repository_test_setup"
    assert diagnosis["primary_blocker"] == (
        "setup_install_failure:editable_backend_unsupported"
    )
    assert diagnosis["command"] == (
        "python -m code_intelligence_agent.evaluation.github_onboarding_pipeline "
        "repair_manifest.json repair_output"
    )
    assert diagnosis["layers"][0]["layer"] == "repository_test_setup"
    assert diagnosis["layers"][0]["status"] == "warn"


def test_pipeline_repository_repair_stage_uses_setup_doctor_blockers():
    stage = _pipeline_repository_repair_stage(
        {
            "report_count": 1,
            "final_status_counts": {"blocked": 1},
            "blocked_reason_counts": {
                "dynamic_evidence_not_usable:not_executed": 1,
            },
            "execution_status_counts": {"skipped": 1},
            "repository_test_setup_doctor_blocker_counts": {
                "checkout:full_repo_not_materialized": 1,
            },
            "phase2_ready_count": 0,
            "phase3_validation_ready_count": 0,
            "patch_validation_success_run_count": 0,
        }
    )
    diagnosis = _pipeline_repository_processing_diagnosis(
        summary={
            "repository_test_setup_doctor_status_counts": {"blocked": 1},
            "repository_test_setup_doctor_blocker_counts": {
                "checkout:full_repo_not_materialized": 1,
            },
            "repository_test_execution_status_counts": {"skipped": 1},
            "repository_test_blocked_reason_counts": {
                "dynamic_evidence_not_usable:not_executed": 1,
            },
            "repository_test_final_status_counts": {"blocked": 1},
            "repository_repair_stage_next_action": stage["next_action"],
            "repository_repair_manifest_command": (
                "python -m code_intelligence_agent.evaluation.github_onboarding_pipeline "
                "repair_manifest.json repair_output"
            ),
        },
        stage_audit=[stage],
    )

    assert stage["status"] == "skip"
    assert "top_setup_doctor_blocker=checkout:full_repo_not_materialized" in (
        stage["evidence"]
    )
    assert stage["next_action"] == (
        "Enable --checkout-repository-tests or provide --repository-test-root, "
        "then rerun the pipeline."
    )
    assert diagnosis["status"] == "blocked"
    assert diagnosis["primary_layer"] == "repository_test_setup"
    assert diagnosis["primary_blocker"] == "checkout:full_repo_not_materialized"
    assert diagnosis["command"] == (
        "python -m code_intelligence_agent.evaluation.github_onboarding_pipeline "
        "repair_manifest.json repair_output"
    )


def test_pipeline_repository_processing_matrix_classifies_run_blockers():
    matrix = _pipeline_repository_processing_matrix(
        {
            "repository_test_run_summaries": [
                {
                    "name": "repaired_repo",
                    "final_status": "repaired",
                    "final_reason": "patch_validation_success",
                    "execution_status": "fail",
                    "execution_command": "python -m pytest tests/test_math.py",
                    "failure_category": "test_assertion_failure",
                    "setup_install_failure_category": (
                        "editable_backend_unsupported"
                    ),
                    "patch_validation_status": "pass",
                    "patch_validation_success_count": 1,
                    "repair_summary_status": "pass",
                    "repair_summary_reason": "repair_ready",
                    "repair_summary_conclusion": "ready_for_review",
                    "repair_summary_path": (
                        "out/repaired_repo/repository_test_repair_summary.md"
                    ),
                    "phase2_ready": True,
                    "phase3_validation_ready": True,
                },
                {
                    "name": "setup_blocked_repo",
                    "final_status": "blocked",
                    "final_reason": (
                        "dynamic_evidence_not_usable:missing_dependency"
                    ),
                    "execution_status": "fail",
                    "failure_category": "missing_dependency",
                    "setup_install_failure_category": "missing_requirement_file",
                    "patch_validation_success_count": 0,
                    "repair_summary_status": "skipped",
                    "repair_summary_reason": "patch_candidates_not_ready",
                    "repair_summary_conclusion": "not_ready",
                },
                {
                    "name": "not_executed_repo",
                    "final_status": "blocked",
                    "final_reason": (
                        "dynamic_evidence_not_usable:not_executed"
                    ),
                    "execution_status": "skipped",
                    "failure_category": "",
                    "setup_install_failure_category": "",
                    "patch_validation_success_count": 0,
                    "repair_summary_status": "skipped",
                    "repair_summary_reason": "patch_validation_missing",
                    "repair_summary_conclusion": "not_ready",
                },
                {
                    "name": "retry_blocked_repo",
                    "final_status": "blocked",
                    "final_reason": (
                        "dynamic_evidence_not_usable:missing_test_runner"
                    ),
                    "execution_status": "fail",
                    "execution_command": "python -m pytest tests",
                    "failure_category": "missing_test_runner",
                    "setup_install_failure_category": "",
                    "patch_validation_success_count": 0,
                    "retry_recommended": True,
                    "retry_strategy": "switch_to_installed_test_runner",
                    "retry_command": "python -m tox",
                    "repair_summary_status": "skipped",
                    "repair_summary_reason": "patch_validation_missing",
                    "repair_summary_conclusion": "not_ready",
                },
                {
                    "name": "localized_repo",
                    "final_status": "phase2_ready",
                    "final_reason": "fault_localization_ready",
                    "execution_status": "fail",
                    "failure_category": "test_assertion_failure",
                    "setup_install_failure_category": "",
                    "phase2_ready": True,
                    "phase3_validation_ready": False,
                    "patch_validation_success_count": 0,
                    "repair_summary_status": "warning",
                    "repair_summary_reason": "repair_not_ready",
                    "repair_summary_conclusion": "not_ready",
                },
            ],
        }
    )

    assert matrix["run_count"] == 5
    assert matrix["status_counts"] == {
        "blocked": 3,
        "phase2_ready": 1,
        "repaired": 1,
    }
    assert matrix["primary_layer_counts"] == {
        "fault_localization": 1,
        "repository_repair": 1,
        "repository_test_execution": 2,
        "repository_test_setup": 1,
    }
    assert matrix["primary_blocker_counts"] == {
        "dynamic_evidence_not_usable:missing_test_runner": 1,
        "dynamic_evidence_not_usable:not_executed": 1,
        "patch_generation_not_executed": 1,
        "setup_install_failure:missing_requirement_file": 1,
    }
    assert matrix["primary_blocker_runs"] == {
        "dynamic_evidence_not_usable:missing_test_runner": ["retry_blocked_repo"],
        "dynamic_evidence_not_usable:not_executed": ["not_executed_repo"],
        "patch_generation_not_executed": ["localized_repo"],
        "setup_install_failure:missing_requirement_file": ["setup_blocked_repo"],
    }
    assert matrix["repair_summary_status_counts"] == {
        "pass": 1,
        "skipped": 3,
        "warning": 1,
    }
    assert matrix["repair_summary_conclusion_counts"] == {
        "not_ready": 4,
        "ready_for_review": 1,
    }
    diagnoses = {item["name"]: item for item in matrix["run_diagnoses"]}
    assert diagnoses["repaired_repo"]["primary_blocker"] == "none"
    assert diagnoses["repaired_repo"]["repair_summary_status"] == "pass"
    assert diagnoses["repaired_repo"]["repair_summary_conclusion"] == (
        "ready_for_review"
    )
    assert "path=out/repaired_repo/repository_test_repair_summary.md" in (
        diagnoses["repaired_repo"]["repair_summary"]
    )
    assert diagnoses["setup_blocked_repo"]["primary_layer"] == (
        "repository_test_setup"
    )
    assert diagnoses["not_executed_repo"]["next_action"] == (
        "Enable --checkout-repository-tests or provide --repository-test-root, "
        "then rerun the pipeline."
    )
    assert diagnoses["retry_blocked_repo"]["next_action"] == (
        "Run repository test retry `python -m tox` using strategy "
        "`switch_to_installed_test_runner` to collect dynamic evidence."
    )
    markdown = render_github_onboarding_pipeline_showcase_markdown(
        {
            "headline": {"suite_name": "matrix_unit", "passed": False},
            "repository_processing_matrix": matrix,
            "repository_processing_acceptance": {
                "passed": False,
                "mode": "unit",
                "reason": "unit",
            },
        }
    )
    assert "Repair Summary Status Counts" in markdown
    assert "Repair Summary Conclusion Counts" in markdown
    assert "ready_for_review" in markdown
    assert "out/repaired_repo/repository_test_repair_summary.md" in markdown


def test_pipeline_repository_processing_expectations_validate_matrix():
    matrix = {
        "run_count": 2,
        "status_counts": {"blocked": 1, "repaired": 1},
        "primary_layer_counts": {
            "repository_repair": 1,
            "repository_test_setup": 1,
        },
        "primary_blocker_counts": {
            "setup_install_failure:missing_requirement_file": 1,
        },
        "repair_summary_status_counts": {"pass": 1, "skipped": 1},
        "repair_summary_conclusion_counts": {
            "not_ready": 1,
            "ready_for_review": 1,
        },
        "repair_manifest_run_context_count": 2,
        "repair_manifest_setup_repair_run_names": ["setup_repo"],
        "repair_manifest_checkout_only_run_names": ["fixed_repo"],
        "run_diagnoses": [
            {
                "name": "fixed_repo",
                "status": "repaired",
                "primary_layer": "repository_repair",
                "primary_blocker": "none",
                "repair_summary_status": "pass",
                "repair_summary_conclusion": "ready_for_review",
                "repair_summary_path": (
                    "out/fixed_repo/repository_test_repair_summary.md"
                ),
                "repair_manifest_blocker": "checkout:full_repo_not_materialized",
                "repair_manifest_setup_repair": False,
                "repair_manifest_next_action": (
                    "Enable --checkout-repository-tests before rerun."
                ),
            },
            {
                "name": "setup_repo",
                "status": "blocked",
                "primary_layer": "repository_test_setup",
                "primary_blocker": "setup_install_failure:missing_requirement_file",
                "repair_summary_status": "skipped",
                "repair_summary_conclusion": "not_ready",
                "repair_manifest_blocker": (
                    "setup_install_failure:missing_requirement_file"
                ),
                "repair_manifest_setup_repair": True,
                "repair_manifest_next_action": "Fix requirements path before rerun.",
            },
        ],
    }

    report = _pipeline_repository_processing_expectation_report(
        matrix,
        {
            "min_run_count": 2,
            "min_status_counts": {"repaired": 1},
            "min_primary_layer_counts": {"repository_repair": 1},
            "min_primary_blocker_counts": {
                "setup_install_failure:missing_requirement_file": 1,
            },
            "min_repair_summary_status_counts": {"pass": 1},
            "min_repair_summary_conclusion_counts": {
                "ready_for_review": 1
            },
            "min_repair_manifest_run_context_count": 2,
            "min_repair_manifest_setup_repair_run_count": 1,
            "min_repair_manifest_checkout_only_run_count": 1,
            "run_expectations": {
                "fixed_repo": {
                    "status": "repaired",
                    "primary_layer": "repository_repair",
                    "primary_blocker": "none",
                    "repair_summary_status": "pass",
                    "repair_summary_conclusion": "ready_for_review",
                    "repair_summary_path_contains": (
                        "repository_test_repair_summary.md"
                    ),
                    "repair_manifest_blocker": (
                        "checkout:full_repo_not_materialized"
                    ),
                    "repair_manifest_setup_repair": False,
                    "repair_manifest_next_action_contains": "checkout",
                },
                "setup_repo": {
                    "allowed_statuses": ["blocked", "incomplete"],
                    "allowed_primary_layers": ["repository_test_setup"],
                    "allowed_primary_blocker_prefixes": [
                        "setup_install_failure:"
                    ],
                    "allowed_repair_summary_statuses": ["skipped", "warning"],
                    "allowed_repair_summary_conclusions": ["not_ready"],
                    "allowed_repair_manifest_blocker_prefixes": [
                        "setup_install_failure:"
                    ],
                    "repair_manifest_setup_repair": True,
                    "repair_manifest_next_action_contains": "requirements",
                },
            },
        },
    )

    assert report["present"] is True
    assert report["passed"] is True
    assert report["failed_count"] == 0
    assert report["check_count"] == 26

    failing = _pipeline_repository_processing_expectation_report(
        matrix,
        {
            "min_run_count": 3,
            "run_expectations": {
                "missing_repo": {"status": "repaired"},
                "setup_repo": {"primary_blocker": "none"},
                "fixed_repo": {
                    "repair_summary_conclusion": "not_ready",
                    "repair_summary_path_contains": "missing.md",
                },
            },
        },
    )

    assert failing["passed"] is False
    assert failing["failed_count"] == 5


def test_pipeline_markdown_shows_repository_processing_failed_expectations():
    report = GitHubOnboardingPipelineReport(
        manifest_path="manifest.json",
        output_dir="out",
        suite_name="suite",
        passed=False,
        summary={
            "repository_processing_acceptance_passed": False,
            "repository_processing_acceptance_mode": (
                "repository_processing_expectations"
            ),
            "repository_processing_acceptance_reason": (
                "repository_processing_expectations_failed"
            ),
            "repository_processing_expectation_passed": False,
            "repository_processing_expectation_check_count": 2,
            "repository_processing_expectation_failed_count": 2,
            "repository_processing_expectation_report": {
                "present": True,
                "passed": False,
                "check_count": 2,
                "failed_count": 2,
                "checks": [
                    {
                        "name": "run:demo_repo:status",
                        "passed": False,
                        "expected": "repaired",
                        "actual": "blocked",
                    },
                    {
                        "name": "min_run_count",
                        "passed": False,
                        "expected": ">=2",
                        "actual": "1",
                    },
                ],
            },
        },
        output_paths={
            "preflight_smoke_manifest": "out/preflight_smoke_manifest.json",
            "smoke_runner_json": "out/smoke_runner.json",
            "smoke_runner_markdown": "out/smoke_runner.md",
        },
        preflight_batch={},
    )

    markdown = render_github_onboarding_pipeline_markdown(report)

    assert (
        "Repository Processing First Failed Expectation: "
        "run:demo_repo:status expected=repaired actual=blocked"
    ) in markdown
    assert (
        "Repository Processing Failed Expectation Preview: "
        "run:demo_repo:status expected=repaired actual=blocked; "
        "min_run_count expected=>=2 actual=1"
    ) in markdown


def test_pipeline_repository_processing_expectations_compile_run_level_targets():
    matrix = {
        "run_count": 2,
        "status_counts": {"blocked": 1, "repaired": 1},
        "primary_layer_counts": {
            "repository_repair": 1,
            "repository_test_execution": 1,
        },
        "primary_blocker_counts": {
            "dynamic_evidence_not_usable:not_executed": 1,
        },
        "repair_summary_status_counts": {"pass": 1, "skipped": 1},
        "repair_summary_conclusion_counts": {
            "not_ready": 1,
            "ready_for_review": 1,
        },
        "run_diagnoses": [
            {
                "name": "fixed_repo",
                "status": "repaired",
                "primary_layer": "repository_repair",
                "primary_blocker": "none",
                "repair_summary_status": "pass",
                "repair_summary_conclusion": "ready_for_review",
                "repair_summary_path": (
                    "out/fixed_repo/repository_test_repair_summary.md"
                ),
                "repair_manifest_blocker": "checkout:full_repo_not_materialized",
                "repair_manifest_setup_repair": False,
                "repair_manifest_next_action": (
                    "Enable checkout before rerunning repository repair."
                ),
            },
            {
                "name": "passing_repo",
                "status": "blocked",
                "primary_layer": "repository_test_execution",
                "primary_blocker": "dynamic_evidence_not_usable:not_executed",
                "repair_summary_status": "skipped",
                "repair_summary_conclusion": "not_ready",
            },
        ],
    }
    expectations = _pipeline_repository_processing_expectations(
        {
            "runs": [
                {
                    "name": "fixed_repo",
                    "expected_repository_processing": {
                        "target_status": "repaired",
                        "target_primary_layer": "repository_repair",
                        "target_repair_summary_status": "pass",
                        "target_repair_summary_conclusion": (
                            "ready_for_review"
                        ),
                        "target_repair_summary_path_contains": (
                            "repository_test_repair_summary.md"
                        ),
                        "target_repair_manifest_blocker": (
                            "checkout:full_repo_not_materialized"
                        ),
                        "target_repair_manifest_setup_repair": False,
                        "target_repair_manifest_next_action_contains": (
                            "checkout"
                        ),
                    },
                },
                {
                    "name": "passing_repo",
                    "expected_repository_processing": {
                        "target_status": (
                            "repaired_or_blocked_with_dynamic_evidence_diagnosis"
                        ),
                        "target_primary_layers": [
                            "repository_repair",
                            "repository_test_execution",
                        ],
                        "allowed_repair_summary_statuses": [
                            "pass",
                            "skipped",
                        ],
                        "allowed_repair_summary_conclusions": [
                            "ready_for_review",
                            "not_ready",
                        ],
                    },
                },
            ],
        }
    )

    report = _pipeline_repository_processing_expectation_report(
        matrix,
        expectations,
    )

    assert set(expectations["run_expectations"]) == {
        "fixed_repo",
        "passing_repo",
    }
    assert (
        expectations["run_expectations"]["fixed_repo"]["repair_summary_status"]
        == "pass"
    )
    assert (
        expectations["run_expectations"]["fixed_repo"][
            "repair_manifest_setup_repair"
        ]
        is False
    )
    assert report["present"] is True
    assert report["passed"] is True
    assert report["failed_count"] == 0
    assert report["check_count"] == 13


def test_pipeline_repository_processing_acceptance_prefers_explicit_expectations():
    acceptance = _pipeline_repository_processing_acceptance_report(
        {
            "promotion_status": "blocked",
            "promotion_promotable": False,
            "repository_processing_expectation_report": {
                "present": True,
                "passed": True,
                "check_count": 11,
                "failed_count": 0,
            },
        }
    )

    assert acceptance == {
        "passed": True,
        "mode": "repository_processing_expectations",
        "reason": "repository_processing_expectations_passed",
        "expectation_present": True,
        "expectation_passed": True,
        "expectation_check_count": 11,
        "expectation_failed_count": 0,
        "promotion_status": "blocked",
        "promotion_promotable": False,
    }

    failing_acceptance = _pipeline_repository_processing_acceptance_report(
        {
            "promotion_status": "promote",
            "promotion_promotable": True,
            "repository_processing_expectation_report": {
                "present": True,
                "passed": False,
                "check_count": 3,
                "failed_count": 1,
            },
        }
    )

    assert failing_acceptance["passed"] is False
    assert failing_acceptance["reason"] == (
        "repository_processing_expectations_failed"
    )


def test_pipeline_repository_processing_acceptance_falls_back_to_promotion_gate():
    acceptance = _pipeline_repository_processing_acceptance_report(
        {
            "promotion_status": "promote",
            "promotion_promotable": True,
            "repository_processing_expectation_report": {
                "present": False,
                "passed": True,
            },
        }
    )

    assert acceptance["passed"] is True
    assert acceptance["mode"] == "promotion_gate"
    assert acceptance["reason"] == "promotion_gate_promotable"


def test_pipeline_cli_exit_code_can_require_processing_expectations():
    report = GitHubOnboardingPipelineReport(
        manifest_path="manifest.json",
        output_dir="out",
        suite_name="exit_suite",
        passed=False,
        summary={
            "repository_processing_expectation_passed": True,
        },
        output_paths={},
        preflight_batch={},
        pipeline_showcase={
            "promotion_gate": {
                "promotable": True,
            },
        },
    )

    assert _pipeline_cli_exit_code(report) == 1
    assert (
        _pipeline_cli_exit_code(
            report,
            require_promotion=True,
            require_processing_expectations=True,
        )
        == 0
    )

    failed_expectation_report = GitHubOnboardingPipelineReport(
        manifest_path="manifest.json",
        output_dir="out",
        suite_name="exit_suite",
        passed=True,
        summary={
            "repository_processing_expectation_passed": False,
        },
        output_paths={},
        preflight_batch={},
        pipeline_showcase={
            "promotion_gate": {
                "promotable": True,
            },
        },
    )

    assert (
        _pipeline_cli_exit_code(
            failed_expectation_report,
            require_processing_expectations=True,
        )
        == 1
    )


def test_pipeline_summary_lifts_repository_test_phase_readiness():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        report_path = root / "onboarding_report.json"
        report_path.write_text(
            json.dumps(
                {
                    "run_config": {
                        "repository_test_analysis_route": {
                            "analysis_source": "failure_overlay_dynamic_evidence",
                            "overlay_trigger_reason": "natural_tests_passing",
                            "phase2_ready": True,
                            "phase3_validation_ready": True,
                        },
                        "repository_test_execution_result": {
                            "status": "fail",
                            "command": "python -m pytest tests/test_service.py",
                            "failure_category": "test_assertion_failure",
                        },
                        "repository_test_fault_localization": {
                            "status": "pass",
                            "top_function": "shift_left",
                        },
                        "repository_test_patch_validation": {
                            "status": "pass",
                            "success_count": 2,
                        },
                        "repository_test_environment_setup_result": {
                            "status": "pass",
                            "reason": (
                                "environment_setup_executed_with_install_fallback"
                            ),
                            "executed": True,
                            "install_failure_category": (
                                "editable_backend_unsupported"
                            ),
                            "install_failure_signal": (
                                "backend does not support editable installs"
                            ),
                            "install_fallback_executed": True,
                            "install_fallback_returncode": 0,
                        },
                        "repository_test_setup_doctor": {
                            "status": "pass",
                            "blocker": "none",
                            "next_action": "Use repository dynamic evidence.",
                            "score": 0.875,
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        preflight = GitHubOnboardingPreflightBatchReport(
            manifest_path=str(root / "manifest.json"),
            output_dir=str(root / "preflight"),
            suite_name="readiness_pipeline",
            passed=True,
            summary={
                "run_count": 1,
                "ready_count": 1,
                "skipped_count": 0,
                "error_count": 0,
                "readiness_rate": 1.0,
                "ready_run_names": ["average_ready"],
                "skipped_run_names": [],
                "error_run_names": [],
                "top_issue_code": "",
                "status_counts": {"pass": 1},
                "issue_counts": {},
            },
            runs=[],
            smoke_manifest={},
            output_paths={},
        )
        smoke = OnboardingSmokeRunnerReport(
            manifest_path=str(root / "smoke_manifest.json"),
            output_dir=str(root / "smoke"),
            suite_name="readiness_pipeline_smoke",
            passed=True,
            generated_manifest_path=str(root / "generated.json"),
            validation_json_path=str(root / "validation.json"),
            validation_markdown_path=str(root / "validation.md"),
            gap_summary_json_path=str(root / "gaps.json"),
            gap_summary_markdown_path=str(root / "gaps.md"),
            recommended_manifest_path=str(root / "recommended.json"),
            summary={
                "run_count": 1,
                "generated_candidates": 3,
                "benchmark_cases": 3,
                "manifest_recommendation_count": 0,
            },
            gap_summary={},
            runs=[
                OnboardingSmokeRunResult(
                    name="average_ready",
                    mode="repo",
                    output_dir=str(root / "run"),
                    report_path=str(report_path),
                    passed=True,
                    command_args=[],
                )
            ],
            suite_validation=OnboardingSmokeSuiteReport(
                manifest_path=str(root / "validation_manifest.json"),
                suite_name="readiness_pipeline_smoke",
                passed=True,
                summary={},
                reports=[],
            ),
        )

        summary = _pipeline_summary(
            preflight=preflight,
            smoke=smoke,
            recommended_smoke=None,
            comparison=None,
            recommendation_rerun_enabled=False,
        )
        showcase = build_github_onboarding_pipeline_showcase(
            manifest_path=str(root / "manifest.json"),
            output_dir=str(root),
            suite_name="readiness_pipeline",
            passed=True,
            summary=summary,
            output_paths={},
            preflight=preflight,
            smoke=smoke,
            comparison=None,
            promotion_config={
                "min_repository_test_phase2_ready_count": 1,
                "min_repository_test_phase3_validation_ready_count": 1,
            },
        )
        blocked_showcase = build_github_onboarding_pipeline_showcase(
            manifest_path=str(root / "manifest.json"),
            output_dir=str(root),
            suite_name="readiness_pipeline",
            passed=True,
            summary=summary,
            output_paths={},
            preflight=preflight,
            smoke=smoke,
            comparison=None,
            promotion_config={
                "min_repository_test_phase3_validation_ready_count": 2,
            },
        )
        repaired_blocked_showcase = build_github_onboarding_pipeline_showcase(
            manifest_path=str(root / "manifest.json"),
            output_dir=str(root),
            suite_name="readiness_pipeline",
            passed=True,
            summary=summary,
            output_paths={},
            preflight=preflight,
            smoke=smoke,
            comparison=None,
            promotion_config={
                "min_repository_test_repaired_count": 2,
            },
        )
        showcase_markdown = render_github_onboarding_pipeline_showcase_markdown(
            showcase
        )

        assert summary["repository_test_report_count"] == 1
        assert summary["repository_test_analysis_source_counts"] == {
            "failure_overlay_dynamic_evidence": 1
        }
        assert summary["repository_test_overlay_trigger_reason_counts"] == {
            "natural_tests_passing": 1
        }
        assert summary["repository_test_phase2_ready_count"] == 1
        assert summary["repository_test_phase2_ready_runs"] == ["average_ready"]
        assert summary["repository_test_phase3_validation_ready_count"] == 1
        assert summary["repository_test_phase3_validation_ready_runs"] == [
            "average_ready"
        ]
        assert summary["repository_test_execution_status_counts"] == {"fail": 1}
        assert summary["repository_test_execution_command_runs"] == {
            "python -m pytest tests/test_service.py": ["average_ready"]
        }
        assert summary["repository_test_failure_category_counts"] == {
            "test_assertion_failure": 1
        }
        assert summary[
            "repository_test_environment_setup_install_failure_counts"
        ] == {"editable_backend_unsupported": 1}
        assert summary[
            "repository_test_environment_setup_install_failure_runs"
        ] == {"editable_backend_unsupported": ["average_ready"]}
        assert (
            summary["repository_test_environment_setup_install_fallback_count"]
            == 1
        )
        assert (
            summary[
                "repository_test_environment_setup_install_fallback_success_count"
            ]
            == 1
        )
        assert summary["repository_test_setup_doctor_status_counts"] == {
            "pass": 1
        }
        assert summary["repository_test_setup_doctor_blocker_counts"] == {}
        assert summary["repository_test_fault_localization_status_counts"] == {
            "pass": 1
        }
        assert summary["repository_test_fault_localization_top_function_counts"] == {
            "shift_left": 1
        }
        assert summary["repository_test_patch_validation_status_counts"] == {
            "pass": 1
        }
        assert summary["repository_test_patch_validation_success_run_count"] == 1
        assert summary["repository_test_patch_validation_success_count"] == 2
        assert summary["repository_test_patch_validation_success_runs"] == [
            "average_ready"
        ]
        assert summary["repository_test_final_status_counts"] == {"repaired": 1}
        assert summary["repository_test_final_status_runs"] == {
            "repaired": ["average_ready"]
        }
        assert summary["repository_test_blocked_reason_counts"] == {}
        assert summary["repository_test_repair_summary_status_counts"] == {}
        assert summary["repository_test_repair_summary_conclusion_counts"] == {}
        assert summary["repository_test_run_summaries"] == [
            {
                "name": "average_ready",
                "final_status": "repaired",
                "final_reason": "patch_validation_success",
                "analysis_source": "failure_overlay_dynamic_evidence",
                "execution_status": "fail",
                "execution_command": "python -m pytest tests/test_service.py",
                "failure_category": "test_assertion_failure",
                "dynamic_failure_category": "",
                "setup_install_failure_category": "editable_backend_unsupported",
                "setup_install_failure_signal": (
                    "backend does not support editable installs"
                ),
                "setup_install_fallback_executed": True,
                "setup_doctor_status": "pass",
                "setup_doctor_blocker": "none",
                "setup_doctor_next_action": "Use repository dynamic evidence.",
                "phase2_ready": True,
                "phase3_validation_ready": True,
                "top_function": "shift_left",
                "patch_validation_status": "pass",
                "patch_validation_success_count": 2,
                "repair_summary_status": "",
                "repair_summary_reason": "",
                "repair_summary_conclusion": "",
                "repair_summary_path": "",
                "retry_recommended": False,
                "retry_strategy": "",
                "retry_command": "",
            }
        ]
        assert showcase["repository_test_readiness"]["phase2_ready_runs"] == [
            "average_ready"
        ]
        assert showcase["repository_test_readiness"]["final_status_counts"] == {
            "repaired": 1
        }
        assert showcase["repository_test_readiness"]["run_summaries"][0][
            "final_reason"
        ] == "patch_validation_success"
        assert showcase["repository_processing_matrix"]["run_count"] == 1
        assert showcase["repository_processing_matrix"]["status_counts"] == {
            "repaired": 1
        }
        assert showcase["repository_processing_matrix"]["run_diagnoses"][0][
            "primary_layer"
        ] == "repository_repair"
        repository_repair_stage = {
            row["stage"]: row for row in showcase["stage_audit"]
        }["repository_repair"]
        assert repository_repair_stage["status"] == "pass"
        assert "repaired=1" in repository_repair_stage["evidence"]
        assert showcase["promotion_gate"]["status"] == "promote"
        assert showcase["promotion_gate"]["criteria"][
            "repository_test_phase2_ready_threshold_met"
        ] is True
        assert showcase["promotion_gate"]["criteria"][
            "repository_test_phase3_validation_ready_threshold_met"
        ] is True
        assert showcase["promotion_gate"]["criteria"][
            "repository_test_repaired_count"
        ] == 1
        assert showcase["promotion_gate"]["criteria"][
            "repository_test_repaired_threshold_met"
        ] is True
        assert blocked_showcase["promotion_gate"]["status"] == "blocked"
        assert blocked_showcase["promotion_gate"]["blocking_reasons"] == [
            "Repository test Phase 3 validation-ready runs 1 below required 2."
        ]
        assert repaired_blocked_showcase["promotion_gate"]["status"] == "blocked"
        assert repaired_blocked_showcase["promotion_gate"]["blocking_reasons"] == [
            "Repository test repaired runs 1 below required 2."
        ]
        assert "Repository Test Phase 2 Ready Runs" in showcase_markdown
        assert "failure_overlay_dynamic_evidence=1" in showcase_markdown
        assert "test_assertion_failure=1" in showcase_markdown
        assert "shift_left=1" in showcase_markdown
        assert "Repository Test Final Statuses" in showcase_markdown
        assert "repaired=1" in showcase_markdown
        assert "Repository Test Run Outcomes" in showcase_markdown
        assert "patch_validation_success" in showcase_markdown


def test_pipeline_summary_surfaces_smoke_auto_remediation_metrics():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        preflight = GitHubOnboardingPreflightBatchReport(
            manifest_path=str(root / "manifest.json"),
            output_dir=str(root / "preflight"),
            suite_name="remediation_pipeline",
            passed=True,
            summary={
                "run_count": 1,
                "ready_count": 1,
                "skipped_count": 0,
                "error_count": 0,
                "readiness_rate": 1.0,
                "ready_run_names": ["needs_benchmark"],
                "skipped_run_names": [],
                "error_run_names": [],
                "top_issue_code": "",
                "status_counts": {"pass": 1},
                "issue_counts": {},
            },
            runs=[],
            smoke_manifest={},
            output_paths={},
        )
        smoke = OnboardingSmokeRunnerReport(
            manifest_path=str(root / "smoke_manifest.json"),
            output_dir=str(root / "smoke"),
            suite_name="remediation_pipeline_smoke",
            passed=True,
            generated_manifest_path=str(root / "generated.json"),
            validation_json_path=str(root / "validation.json"),
            validation_markdown_path=str(root / "validation.md"),
            gap_summary_json_path=str(root / "gaps.json"),
            gap_summary_markdown_path=str(root / "gaps.md"),
            recommended_manifest_path=str(root / "recommended.json"),
            summary={
                "run_count": 1,
                "generated_candidates": 2,
                "benchmark_cases": 1,
                "static_intelligence_run_count": 1,
                "static_intelligence_analysis_ready_count": 1,
                "static_intelligence_selected_signal_count": 2,
                "static_intelligence_total_signal_count": 3,
                "static_intelligence_status_counts": {"analysis_ready": 1},
                "static_intelligence_rule_counts": {
                    "missing_len_zero_guard": 2
                },
                "benchmarkization_ready_count": 1,
                "benchmarkization_ready_runs": ["needs_benchmark"],
                "benchmarkization_status_counts": {"benchmark_ready": 1},
                "benchmarkization_status_runs": {
                    "benchmark_ready": ["needs_benchmark"]
                },
                "benchmarkization_stage_counts": {"complete": 1},
                "benchmarkization_stage_runs": {"complete": ["needs_benchmark"]},
                "benchmarkization_primary_action_counts": {
                    "publish_benchmark_evidence_bundle": 1
                },
                "benchmarkization_primary_action_runs": {
                    "publish_benchmark_evidence_bundle": ["needs_benchmark"]
                },
                "benchmarkization_auto_runnable_action_count": 0,
                "benchmarkization_manual_action_count": 2,
                "benchmarkization_remediation_plan_count": 1,
                "benchmarkization_remediation_plan_runs": {
                    "needs_benchmark": str(
                        root / "smoke" / "needs_benchmark" / "benchmarkization_remediation_plan.md"
                    )
                },
                "gap_status": "pass",
                "manifest_recommendation_count": 0,
                "auto_remediation_attempted_count": 1,
                "auto_remediation_used_count": 1,
                "auto_remediation_improved_count": 1,
                "auto_remediation_attempted_runs": ["needs_benchmark"],
                "auto_remediation_used_runs": ["needs_benchmark"],
                "auto_remediation_improved_runs": ["needs_benchmark"],
                "auto_remediation_action_counts": {"run_template_benchmark": 1},
                "auto_remediation_action_runs": {
                    "run_template_benchmark": ["needs_benchmark"]
                },
                "auto_remediation_benchmark_case_delta": 1,
            },
            gap_summary={},
            runs=[],
            suite_validation=OnboardingSmokeSuiteReport(
                manifest_path=str(root / "validation_manifest.json"),
                suite_name="remediation_pipeline_smoke",
                passed=True,
                summary={},
                reports=[],
            ),
        )
        recommended_smoke = OnboardingSmokeRunnerReport(
            manifest_path=str(root / "recommended_smoke_manifest.json"),
            output_dir=str(root / "recommended_smoke"),
            suite_name="remediation_pipeline_recommended_smoke",
            passed=True,
            generated_manifest_path=str(root / "recommended_generated.json"),
            validation_json_path=str(root / "recommended_validation.json"),
            validation_markdown_path=str(root / "recommended_validation.md"),
            gap_summary_json_path=str(root / "recommended_gaps.json"),
            gap_summary_markdown_path=str(root / "recommended_gaps.md"),
            recommended_manifest_path=str(root / "recommended_again.json"),
            summary={
                "run_count": 1,
                "generated_candidates": 2,
                "benchmark_cases": 1,
                "static_intelligence_run_count": 1,
                "static_intelligence_analysis_ready_count": 1,
                "static_intelligence_selected_signal_count": 2,
                "static_intelligence_total_signal_count": 2,
                "static_intelligence_status_counts": {"analysis_ready": 1},
                "static_intelligence_rule_counts": {
                    "missing_len_zero_guard": 2
                },
                "benchmarkization_ready_count": 1,
                "benchmarkization_status_counts": {"benchmark_ready": 1},
                "benchmarkization_primary_action_counts": {
                    "publish_benchmark_evidence_bundle": 1
                },
                "benchmarkization_remediation_plan_count": 1,
                "gap_status": "pass",
                "manifest_recommendation_count": 0,
                "auto_remediation_attempted_count": 1,
                "auto_remediation_used_count": 0,
                "auto_remediation_improved_count": 0,
                "auto_remediation_action_counts": {"run_template_benchmark": 1},
                "auto_remediation_benchmark_case_delta": 0,
            },
            gap_summary={},
            runs=[],
            suite_validation=OnboardingSmokeSuiteReport(
                manifest_path=str(root / "recommended_validation_manifest.json"),
                suite_name="remediation_pipeline_recommended_smoke",
                passed=True,
                summary={},
                reports=[],
            ),
        )

        summary = _pipeline_summary(
            preflight=preflight,
            smoke=smoke,
            recommended_smoke=recommended_smoke,
            comparison=None,
            recommendation_rerun_enabled=True,
        )
        showcase = build_github_onboarding_pipeline_showcase(
            manifest_path=str(root / "manifest.json"),
            output_dir=str(root),
            suite_name="remediation_pipeline",
            passed=True,
            summary=summary,
            output_paths={},
            preflight=preflight,
            smoke=smoke,
            comparison=None,
        )
        report = GitHubOnboardingPipelineReport(
            manifest_path=str(root / "manifest.json"),
            output_dir=str(root),
            suite_name="remediation_pipeline",
            passed=True,
            summary=summary,
            output_paths={
                "preflight_smoke_manifest": str(root / "preflight_smoke.json"),
                "smoke_runner_json": str(root / "smoke_runner.json"),
                "smoke_runner_markdown": str(root / "smoke_runner.md"),
                "recommended_smoke_runner_json": str(
                    root / "recommended_smoke_runner.json"
                ),
                "recommended_smoke_runner_markdown": str(
                    root / "recommended_smoke_runner.md"
                ),
                "recommendation_comparison_json": str(
                    root / "recommendation_comparison.json"
                ),
                "recommendation_comparison_markdown": str(
                    root / "recommendation_comparison.md"
                ),
            },
            preflight_batch=preflight.to_dict(),
            smoke_runner=smoke.to_dict(),
            recommended_smoke_runner=recommended_smoke.to_dict(),
            pipeline_showcase=showcase,
        )
        markdown = render_github_onboarding_pipeline_markdown(report)
        showcase_markdown = render_github_onboarding_pipeline_showcase_markdown(
            showcase
        )

        assert summary["smoke_auto_remediation_attempted_count"] == 1
        assert summary["smoke_auto_remediation_used_count"] == 1
        assert summary["smoke_auto_remediation_improved_count"] == 1
        assert summary["smoke_auto_remediation_attempted_runs"] == [
            "needs_benchmark"
        ]
        assert summary["smoke_auto_remediation_action_counts"] == {
            "run_template_benchmark": 1
        }
        assert summary["smoke_auto_remediation_action_runs"] == {
            "run_template_benchmark": ["needs_benchmark"]
        }
        assert summary["smoke_auto_remediation_benchmark_case_delta"] == 1
        assert summary["smoke_benchmarkization_ready_count"] == 1
        assert summary["smoke_benchmarkization_status_counts"] == {
            "benchmark_ready": 1
        }
        assert summary["smoke_benchmarkization_stage_counts"] == {"complete": 1}
        assert summary["smoke_benchmarkization_primary_action_counts"] == {
            "publish_benchmark_evidence_bundle": 1
        }
        assert summary["smoke_static_intelligence_analysis_ready_count"] == 1
        assert summary["smoke_static_intelligence_selected_signal_count"] == 2
        assert summary["smoke_static_intelligence_total_signal_count"] == 3
        assert summary["smoke_static_intelligence_status_counts"] == {
            "analysis_ready": 1
        }
        assert summary["smoke_static_intelligence_rule_counts"] == {
            "missing_len_zero_guard": 2
        }
        assert summary["smoke_benchmarkization_manual_action_count"] == 2
        assert summary["smoke_benchmarkization_remediation_plan_count"] == 1
        assert summary["smoke_benchmarkization_remediation_plan_runs"] == {
            "needs_benchmark": str(
                root / "smoke" / "needs_benchmark" / "benchmarkization_remediation_plan.md"
            )
        }
        assert summary["recommended_smoke_benchmarkization_ready_count"] == 1
        assert summary["recommended_smoke_benchmarkization_status_counts"] == {
            "benchmark_ready": 1
        }
        assert (
            summary["recommended_smoke_static_intelligence_analysis_ready_count"]
            == 1
        )
        assert summary["recommended_smoke_auto_remediation_attempted_count"] == 1
        assert summary["recommended_smoke_auto_remediation_used_count"] == 0
        assert showcase["headline"]["smoke_auto_remediation_used_count"] == 1
        assert showcase["headline"][
            "smoke_auto_remediation_benchmark_case_delta"
        ] == 1
        assert showcase["headline"]["smoke_benchmarkization_ready_count"] == 1
        assert showcase["headline"]["smoke_benchmarkization_status_counts"] == {
            "benchmark_ready": 1
        }
        assert (
            showcase["headline"]["smoke_static_intelligence_analysis_ready_count"]
            == 1
        )
        assert showcase["headline"]["smoke_static_intelligence_rule_counts"] == {
            "missing_len_zero_guard": 2
        }
        assert (
            showcase["smoke_evidence"][
                "static_intelligence_analysis_ready_count"
            ]
            == 1
        )
        assert showcase["smoke_evidence"]["static_intelligence_rule_counts"] == {
            "missing_len_zero_guard": 2
        }
        assert showcase["smoke_evidence"]["benchmarkization_status_counts"] == {
            "benchmark_ready": 1
        }
        assert showcase["smoke_evidence"][
            "benchmarkization_remediation_plan_runs"
        ] == {
            "needs_benchmark": str(
                root / "smoke" / "needs_benchmark" / "benchmarkization_remediation_plan.md"
            )
        }
        assert showcase["smoke_evidence"]["auto_remediation_action_counts"] == {
            "run_template_benchmark": 1
        }
        smoke_stage = {
            row["stage"]: row for row in showcase["stage_audit"]
        }["smoke_benchmark"]
        assert "auto_remediation_used=1" in smoke_stage["evidence"]
        assert "static=1/1" in smoke_stage["evidence"]
        assert "static_rules=missing_len_zero_guard=2" in smoke_stage["evidence"]
        assert "benchmarkization=benchmark_ready=1" in smoke_stage["evidence"]
        assert any(
            "low-risk benchmark auto-remediation" in bullet
            for bullet in showcase["resume_bullets"]
        )
        assert any(
            "benchmarkization readiness" in bullet
            for bullet in showcase["resume_bullets"]
        )
        assert any(
            "static intelligence reporting" in bullet
            for bullet in showcase["resume_bullets"]
        )
        assert "Smoke Auto Remediation Used Runs: 1" in markdown
        assert "Smoke Static Intelligence Analysis Ready Runs: 1" in markdown
        assert "Smoke Benchmarkization Ready Runs: 1" in markdown
        assert "Smoke Benchmarkization Remediation Plans" in markdown
        assert "run_template_benchmark=1" in markdown
        assert (
            "Benchmark Auto Remediation: 1 used / 1 improved / 1 attempted; cases +1"
            in showcase_markdown
        )
        assert "Benchmarkization: 1 ready; statuses benchmark_ready=1" in (
            showcase_markdown
        )
        assert "Static Intelligence: 1/1 analysis-ready; signals 2/3" in (
            showcase_markdown
        )


def test_pipeline_manifest_can_configure_promotion_gate_thresholds():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        ready_discovery = _write_average_discovery(root)
        manifest = root / "pipeline_manifest.json"
        output_dir = root / "pipeline_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "github_pipeline_strict_promotion",
                    "promotion_gate": {
                        "min_readiness_rate": 1.0,
                        "min_smoke_benchmark_cases": 99,
                    },
                    "runs": [
                        {
                            "name": "average_ready",
                            "mode": "from-discovery",
                            "discovery": ready_discovery.name,
                            "sample_sources": 5,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = run_github_onboarding_pipeline(manifest, output_dir)
        promotion_gate = report.pipeline_showcase["promotion_gate"]

        assert report.passed is True
        assert report.summary["promotion_status"] == "blocked"
        assert report.summary["promotion_promotable"] is False
        assert report.summary["promotion_config"]["min_readiness_rate"] == 1.0
        assert report.summary["promotion_config"]["min_smoke_benchmark_cases"] == 99
        assert promotion_gate["status"] == "blocked"
        assert promotion_gate["criteria"]["readiness_threshold_met"] is True
        assert promotion_gate["criteria"]["smoke_case_threshold_met"] is False
        assert promotion_gate["criteria"]["min_smoke_benchmark_cases"] == 99
        assert promotion_gate["blocking_reasons"] == [
            (
                "Smoke benchmark cases "
                f"{promotion_gate['criteria']['smoke_benchmark_cases']} "
                "below required 99."
            )
        ]


def test_pipeline_promotion_allows_repository_test_only_thresholds():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        preflight = GitHubOnboardingPreflightBatchReport(
            manifest_path=str(root / "manifest.json"),
            output_dir=str(root / "preflight"),
            suite_name="repository_only_pipeline",
            passed=True,
            summary={
                "status_counts": {"warning": 1},
                "issue_counts": {"repository_test_smoke_fallback": 1},
            },
            runs=[],
            smoke_manifest={},
            output_paths={},
        )
        smoke = OnboardingSmokeRunnerReport(
            manifest_path=str(root / "smoke_manifest.json"),
            output_dir=str(root / "smoke"),
            suite_name="repository_only_pipeline_smoke",
            passed=False,
            generated_manifest_path=str(root / "generated.json"),
            validation_json_path=str(root / "validation.json"),
            validation_markdown_path=str(root / "validation.md"),
            gap_summary_json_path=str(root / "gaps.json"),
            gap_summary_markdown_path=str(root / "gaps.md"),
            recommended_manifest_path=str(root / "recommended.json"),
            summary={
                "run_count": 1,
                "generated_candidates": 0,
                "benchmark_cases": 0,
                "gap_status": "fail",
                "manifest_recommendation_count": 0,
            },
            gap_summary={},
            runs=[],
            suite_validation=OnboardingSmokeSuiteReport(
                manifest_path=str(root / "validation_manifest.json"),
                suite_name="repository_only_pipeline_smoke",
                passed=False,
                summary={},
                reports=[],
            ),
        )
        summary = {
            "preflight_readiness_rate": 1.0,
            "preflight_ready_count": 1,
            "preflight_skipped_count": 0,
            "preflight_ready_run_names": ["repo_overlay_ready"],
            "preflight_skipped_run_names": [],
            "preflight_error_run_names": [],
            "preflight_top_issue_code": "low_python_source_ratio",
            "smoke_generated_candidates": 0,
            "smoke_benchmark_cases": 0,
            "smoke_fallback_attempted_count": 0,
            "smoke_fallback_improved_count": 0,
            "smoke_fallback_recovered_count": 0,
            "recommendation_status": "not_run",
            "repository_test_report_count": 1,
            "repository_test_analysis_source_counts": {
                "failure_overlay_dynamic_evidence": 1
            },
            "repository_test_phase2_ready_count": 1,
            "repository_test_phase2_ready_runs": ["repo_overlay_ready"],
            "repository_test_phase3_validation_ready_count": 1,
            "repository_test_phase3_validation_ready_runs": ["repo_overlay_ready"],
            "repository_test_final_status_counts": {"phase3_ready": 1},
        }

        showcase = build_github_onboarding_pipeline_showcase(
            manifest_path=str(root / "manifest.json"),
            output_dir=str(root),
            suite_name="repository_only_pipeline",
            passed=False,
            summary=summary,
            output_paths={},
            preflight=preflight,
            smoke=smoke,
            comparison=None,
            promotion_config={
                "min_smoke_benchmark_cases": 0,
                "min_repository_test_phase2_ready_count": 1,
                "min_repository_test_phase3_validation_ready_count": 1,
                "allow_warning_stages": True,
            },
        )

        promotion_gate = showcase["promotion_gate"]
        assert promotion_gate["status"] == "promote"
        assert promotion_gate["promotable"] is True
        assert promotion_gate["blocking_reasons"] == []
        assert promotion_gate["criteria"]["pipeline_passed"] is False
        assert promotion_gate["criteria"]["pipeline_acceptance_met"] is True
        assert promotion_gate["criteria"]["repository_test_only_promotion"] is True
        assert promotion_gate["criteria"]["smoke_case_threshold_met"] is True


def test_pipeline_showcase_uses_smoke_gap_actions_when_no_recommendations():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        preflight = GitHubOnboardingPreflightBatchReport(
            manifest_path=str(root / "manifest.json"),
            output_dir=str(root / "preflight"),
            suite_name="gap_action_pipeline",
            passed=True,
            summary={
                "run_count": 1,
                "ready_count": 1,
                "skipped_count": 0,
                "error_count": 0,
                "readiness_rate": 1.0,
                "ready_run_names": ["repo_ready"],
                "skipped_run_names": [],
                "error_run_names": [],
                "top_issue_code": "",
                "status_counts": {"pass": 1},
                "issue_counts": {},
            },
            runs=[],
            smoke_manifest={},
            output_paths={},
        )
        smoke = OnboardingSmokeRunnerReport(
            manifest_path=str(root / "smoke_manifest.json"),
            output_dir=str(root / "smoke"),
            suite_name="gap_action_pipeline_smoke",
            passed=True,
            generated_manifest_path=str(root / "generated.json"),
            validation_json_path=str(root / "validation.json"),
            validation_markdown_path=str(root / "validation.md"),
            gap_summary_json_path=str(root / "gaps.json"),
            gap_summary_markdown_path=str(root / "gaps.md"),
            recommended_manifest_path=str(root / "recommended.json"),
            summary={
                "run_count": 1,
                "generated_candidates": 1,
                "benchmark_cases": 1,
                "gap_status": "warning",
                "manifest_recommendation_count": 0,
            },
            gap_summary={
                "headline": {
                    "status": "warning",
                    "top_diagnostic_issue": "skipped_sources",
                },
                "next_actions": [
                    "Inspect source_import.md to decide whether skipped files are expected.",
                    (
                        "Use --include/--exclude or --preserve-paths if the "
                        "selected source surface is too narrow."
                    ),
                ],
            },
            runs=[],
            suite_validation=OnboardingSmokeSuiteReport(
                manifest_path=str(root / "validation_manifest.json"),
                suite_name="gap_action_pipeline_smoke",
                passed=True,
                summary={},
                reports=[],
            ),
        )
        summary = {
            "preflight_readiness_rate": 1.0,
            "preflight_ready_count": 1,
            "preflight_skipped_count": 0,
            "preflight_ready_run_names": ["repo_ready"],
            "preflight_skipped_run_names": [],
            "preflight_error_run_names": [],
            "preflight_top_issue_code": "",
            "smoke_generated_candidates": 1,
            "smoke_benchmark_cases": 1,
            "smoke_benchmark_patch_success_rate": 1.0,
            "smoke_benchmark_top1": 1.0,
            "smoke_benchmark_map": 1.0,
            "smoke_fallback_attempted_count": 0,
            "smoke_fallback_improved_count": 0,
            "smoke_fallback_recovered_count": 0,
            "recommendation_status": "not_run",
        }

        showcase = build_github_onboarding_pipeline_showcase(
            manifest_path=str(root / "manifest.json"),
            output_dir=str(root),
            suite_name="gap_action_pipeline",
            passed=True,
            summary=summary,
            output_paths={},
            preflight=preflight,
            smoke=smoke,
            comparison=None,
            promotion_config={"allow_warning_stages": True},
        )

        smoke_stage = {
            row["stage"]: row for row in showcase["stage_audit"]
        }["smoke_benchmark"]
        assert showcase["smoke_evidence"]["gap_top_diagnostic_issue"] == (
            "skipped_sources"
        )
        assert showcase["smoke_evidence"]["gap_next_actions"] == [
            "Inspect source_import.md to decide whether skipped files are expected.",
            (
                "Use --include/--exclude or --preserve-paths if the selected "
                "source surface is too narrow."
            ),
        ]
        assert smoke_stage["status"] == "warn"
        assert smoke_stage["next_action"] == (
            "Inspect source_import.md to decide whether skipped files are expected."
        )
        assert showcase["next_actions"] == [
            "Inspect source_import.md to decide whether skipped files are expected."
        ]


def test_pipeline_reruns_recommended_manifest_and_compares_results():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        discovery = _write_mixed_recipe_discovery(root)
        manifest = root / "pipeline_manifest.json"
        output_dir = root / "pipeline_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "github_pipeline_recommendation_loop",
                    "defaults": {
                        "mode": "from-discovery",
                        "preset": "smoke",
                        "sample_sources": 5,
                        "recipe": ["missing_len_zero_guard"],
                    },
                    "runs": [
                        {
                            "name": "mixed_recipes",
                            "discovery": discovery.name,
                            "thresholds": {"min_generated_candidates": 2},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = run_github_onboarding_pipeline(manifest, output_dir)
        markdown = render_github_onboarding_pipeline_markdown(report)

        assert report.passed is True
        assert report.summary["smoke_manifest_recommendations"] == 1
        assert report.summary["smoke_fallback_attempted_count"] == 1
        assert report.summary["smoke_fallback_improved_count"] == 1
        assert report.summary["smoke_fallback_recovered_count"] == 1
        assert report.summary["recommendation_applied_count"] == 1
        assert report.summary["recommendation_rerun_present"] is True
        assert report.summary["recommendation_rerun_passed"] is True
        assert report.summary["recommendation_comparison_present"] is True
        assert report.summary["recommendation_comparison_passed"] is True
        assert report.summary["recommended_pipeline_manifest_present"] is True
        assert (
            report.summary["recommended_pipeline_manifest_path"]
            == report.output_paths["recommended_pipeline_manifest"]
        )
        assert report.summary["recommendation_status"] == "unchanged"
        assert report.summary["recommendation_candidate_delta"] == 0
        assert report.summary["recommendation_benchmark_case_delta"] == 0
        assert report.summary["recommendation_fallback_recovered_delta"] == -1
        assert report.summary[
            "recommendation_fallback_recovery_resolved_count"
        ] == 1
        assert report.pipeline_showcase is not None
        assert report.pipeline_showcase["headline"][
            "smoke_fallback_recovered_count"
        ] == 1
        assert report.pipeline_showcase["smoke_evidence"][
            "fallback_recovered_count"
        ] == 1
        assert report.pipeline_showcase["recommendation"]["status"] == "unchanged"
        assert report.pipeline_showcase["recommendation"][
            "fallback_recovered_delta"
        ] == -1
        assert report.pipeline_showcase["recommendation"][
            "fallback_recovery_resolved_count"
        ] == 1
        assert report.pipeline_showcase["recommendation"][
            "pipeline_manifest_present"
        ] is True
        assert (
            report.pipeline_showcase["recommendation"]["pipeline_manifest_path"]
            == report.output_paths["recommended_pipeline_manifest"]
        )
        assert report.pipeline_showcase["recommendation"]["regressions"] == []
        recommendation_stage = {
            row["stage"]: row
            for row in report.pipeline_showcase["stage_audit"]
        }["recommendation_rerun"]
        assert "recommendation_rerun" in report.pipeline_showcase[
            "stage_summary"
        ]["pass_stage_names"]
        assert recommendation_stage["status"] == "pass"
        assert recommendation_stage["evidence"] == (
            "applied=1, status=unchanged, regressions=0"
        )
        assert report.recommended_smoke_runner is not None
        assert report.recommendation_comparison is not None
        assert Path(report.output_paths["recommended_smoke_runner_json"]).exists()
        assert Path(report.output_paths["recommended_smoke_runner_markdown"]).exists()
        assert Path(report.output_paths["recommended_pipeline_manifest"]).exists()
        assert Path(report.output_paths["recommendation_comparison_json"]).exists()
        assert Path(report.output_paths["recommendation_comparison_markdown"]).exists()
        recommended_pipeline_manifest = json.loads(
            Path(report.output_paths["recommended_pipeline_manifest"]).read_text(
                encoding="utf-8"
            )
        )
        recommended_run = recommended_pipeline_manifest["runs"][0]
        assert recommended_pipeline_manifest["recommendation_metadata"][
            "pipeline_manifest_kind"
        ] == "recommended_pipeline_manifest"
        assert recommended_run["name"] == "mixed_recipes"
        assert recommended_run["mode"] == "from-discovery"
        assert recommended_run["max_sources"] == 50
        assert recommended_run["sample_sources"] == 50
        assert recommended_run["max_candidates"] == 20
        assert "recipe" not in recommended_run
        resolved_recommended_discovery = (
            Path(report.output_paths["recommended_pipeline_manifest"]).parent
            / recommended_run["discovery"]
        ).resolve()
        assert resolved_recommended_discovery == (
            output_dir
            / "preflight"
            / "preflight_runs"
            / "mixed_recipes"
            / "preflight_discovery.json"
        ).resolve()
        assert resolved_recommended_discovery.exists()
        assert "Recommendation Rerun Command" in markdown
        assert "Recommended Pipeline Manifest Command" in markdown
        assert "Recommended Pipeline Manifest Present: true" in markdown
        assert "Recommendation Impact" in markdown
        assert "onboarding_recommendation_comparator" in markdown


def test_pipeline_writes_recommended_pipeline_manifest_when_rerun_is_skipped():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        discovery = _write_mixed_recipe_discovery(root)
        manifest = root / "pipeline_manifest.json"
        output_dir = root / "pipeline_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "github_pipeline_recommendation_manifest_only",
                    "defaults": {
                        "mode": "from-discovery",
                        "preset": "smoke",
                        "sample_sources": 5,
                        "recipe": ["missing_len_zero_guard"],
                    },
                    "runs": [
                        {
                            "name": "mixed_recipes",
                            "discovery": discovery.name,
                            "thresholds": {"min_generated_candidates": 2},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = run_github_onboarding_pipeline(
            manifest,
            output_dir,
            run_recommendation_rerun=False,
        )
        markdown = render_github_onboarding_pipeline_markdown(report)
        recommended_pipeline_manifest = json.loads(
            Path(report.output_paths["recommended_pipeline_manifest"]).read_text(
                encoding="utf-8"
            )
        )

        assert report.summary["smoke_manifest_recommendations"] == 1
        assert report.summary["recommendation_applied_count"] == 1
        assert report.summary["recommendation_rerun_enabled"] is False
        assert report.summary["recommendation_rerun_present"] is False
        assert report.summary["recommended_pipeline_manifest_present"] is True
        assert (
            report.summary["recommended_pipeline_manifest_path"]
            == report.output_paths["recommended_pipeline_manifest"]
        )
        assert report.pipeline_showcase is not None
        assert report.pipeline_showcase["recommendation"][
            "pipeline_manifest_present"
        ] is True
        assert (
            report.pipeline_showcase["recommendation"]["pipeline_manifest_path"]
            == report.output_paths["recommended_pipeline_manifest"]
        )
        assert report.recommended_smoke_runner is None
        assert report.recommendation_comparison is None
        assert Path(report.output_paths["recommended_pipeline_manifest"]).exists()
        assert recommended_pipeline_manifest["runs"][0]["sample_sources"] == 50
        assert "recipe" not in recommended_pipeline_manifest["runs"][0]
        assert "Recommended Pipeline Manifest Command" in markdown
        assert "Recommended Pipeline Manifest Present: true" in markdown
        assert "--skip-recommendation-rerun" in markdown
        assert "Recommendation Rerun Command" not in markdown


def test_pipeline_cli_writes_report_artifacts():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        ready_discovery = _write_average_discovery(root)
        manifest = root / "pipeline_manifest.json"
        output_dir = root / "pipeline_output"
        output_json = root / "pipeline.json"
        output_markdown = root / "pipeline.md"
        output_showcase_json = root / "pipeline_showcase.json"
        output_showcase_markdown = root / "pipeline_showcase.md"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "github_pipeline_cli",
                    "runs": [
                        {
                            "name": "average_ready",
                            "mode": "from-discovery",
                            "discovery": ready_discovery.name,
                            "sample_sources": 5,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.github_onboarding_pipeline",
                str(manifest),
                str(output_dir),
                "--format",
                "json",
                "--output-json",
                str(output_json),
                "--output-markdown",
                str(output_markdown),
                "--output-showcase-json",
                str(output_showcase_json),
                "--output-showcase-markdown",
                str(output_showcase_markdown),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        saved = json.loads(output_json.read_text(encoding="utf-8"))
        saved_showcase = json.loads(output_showcase_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert saved["passed"] is True
        assert saved["summary"]["smoke_run_count"] == 1
        assert saved["smoke_runner"]["summary"]["generated_candidates"] >= 1
        assert saved["pipeline_showcase"]["headline"]["smoke_benchmark_cases"] >= 1
        assert saved["summary"]["promotion_status"] == "promote"
        assert saved["summary"]["promotion_promotable"] is True
        assert saved["summary"]["promotion_blocking_reasons"] == []
        assert saved["summary"]["promotion_warning_reasons"] == []
        assert saved["summary"]["pipeline_stage_statuses"]["repository_repair"] == (
            "skip"
        )
        assert saved["summary"]["repository_repair_stage_status"] == "skip"
        assert Path(
            saved["output_paths"]["pipeline_showcase_markdown"]
        ).exists()
        assert Path(
            saved["output_paths"]["preflight_offline_manifest"]
        ).exists()
        assert saved_showcase["artifacts"]["preflight_offline_manifest"] == (
            saved["output_paths"]["preflight_offline_manifest"]
        )
        assert saved_showcase["artifact_kind"] == (
            "github_onboarding_pipeline_showcase"
        )
        assert saved_showcase["headline"] == saved["pipeline_showcase"]["headline"]
        assert saved_showcase["stage_summary"]["stage_count"] == 4
        assert saved_showcase["stage_summary"]["has_blockers"] is False
        assert saved_showcase["promotion_gate"]["status"] == "promote"
        assert saved_showcase["promotion_gate"]["promotable"] is True
        assert saved_showcase["promotion_gate"]["blocking_reasons"] == []
        assert saved_showcase["promotion_gate"]["warning_reasons"] == []
        assert saved_showcase["stage_audit"][0]["stage"] == "preflight"
        assert {
            row["stage"]: row["status"] for row in saved_showcase["stage_audit"]
        }["repository_repair"] == "skip"
        assert saved_showcase["next_actions"]
        assert "GitHub Onboarding Pipeline Showcase" in (
            output_showcase_markdown.read_text(encoding="utf-8")
        )
        assert "github_onboarding_pipeline.json" in completed.stdout
        assert output_markdown.exists()


def test_pipeline_cli_can_require_promotion_gate():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        ready_discovery = _write_average_discovery(root)
        docs_discovery = _write_docs_discovery(root)
        manifest = root / "pipeline_manifest.json"
        output_dir = root / "pipeline_output"
        output_json = root / "pipeline.json"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "github_pipeline_require_promotion",
                    "defaults": {
                        "mode": "from-discovery",
                        "sample_sources": 5,
                    },
                    "runs": [
                        {
                            "name": "average_ready",
                            "discovery": ready_discovery.name,
                        },
                        {
                            "name": "docs_only",
                            "discovery": docs_discovery.name,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.github_onboarding_pipeline",
                str(manifest),
                str(output_dir),
                "--format",
                "json",
                "--output-json",
                str(output_json),
                "--require-promotion",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        saved = json.loads(output_json.read_text(encoding="utf-8"))
        stdout_payload = json.loads(completed.stdout)

        assert completed.returncode == 1
        assert saved["passed"] is True
        assert stdout_payload["passed"] is True
        assert saved["summary"]["promotion_status"] == "review"
        assert stdout_payload["summary"]["promotion_status"] == "review"
        assert saved["summary"]["promotion_promotable"] is False
        assert saved["pipeline_showcase"]["promotion_gate"]["status"] == "review"
        assert (
            saved["pipeline_showcase"]["promotion_gate"]["promotable"] is False
        )
        assert saved["pipeline_showcase"]["promotion_gate"]["warning_reasons"] == [
            "Warning stages: preflight."
        ]


def test_pipeline_cli_can_override_promotion_gate_thresholds():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        ready_discovery = _write_average_discovery(root)
        docs_discovery = _write_docs_discovery(root)
        manifest = root / "pipeline_manifest.json"
        output_dir = root / "pipeline_output"
        output_json = root / "pipeline.json"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "github_pipeline_cli_promotion_override",
                    "defaults": {
                        "mode": "from-discovery",
                        "sample_sources": 5,
                    },
                    "runs": [
                        {
                            "name": "average_ready",
                            "discovery": ready_discovery.name,
                        },
                        {
                            "name": "docs_only",
                            "discovery": docs_discovery.name,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.github_onboarding_pipeline",
                str(manifest),
                str(output_dir),
                "--format",
                "json",
                "--output-json",
                str(output_json),
                "--require-promotion",
                "--promotion-min-readiness-rate",
                "0.5",
                "--promotion-min-smoke-benchmark-cases",
                "1",
                "--promotion-min-repository-test-repaired-count",
                "0",
                "--promotion-allow-warning-stages",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        saved = json.loads(output_json.read_text(encoding="utf-8"))
        stdout_payload = json.loads(completed.stdout)

        assert completed.returncode == 0
        assert saved["summary"]["promotion_status"] == "promote"
        assert saved["summary"]["promotion_promotable"] is True
        assert stdout_payload["summary"]["promotion_promotable"] is True
        assert saved["summary"]["promotion_config"] == {
            "min_readiness_rate": 0.5,
            "min_smoke_benchmark_cases": 1,
            "min_fallback_recovered_count": 0,
            "min_repository_test_phase2_ready_count": 0,
            "min_repository_test_phase3_validation_ready_count": 0,
            "min_repository_test_repaired_count": 0,
            "allow_warning_stages": True,
            "fail_on_recommendation_regression": True,
        }
        criteria = saved["summary"]["promotion_criteria"]
        assert criteria["allow_warning_stages"] is True
        assert criteria["readiness_threshold_met"] is True
        assert criteria["smoke_case_threshold_met"] is True
        assert saved["summary"]["promotion_warning_reasons"] == [
            "Warning stages: preflight."
        ]


def test_single_repo_pipeline_manifest_builder_preserves_repo_options():
    manifest = build_single_repo_pipeline_manifest(
        "https://github.com/example/project",
        ref="main",
        include=["maths/average_mean.py"],
        exclude=["docs/"],
        preset="mining",
        sample_sources=7,
        max_candidates=4,
        auto_fallback=False,
        fallback_min_generated_candidates=3,
        fallback_max_sources=31,
        fallback_max_candidates=11,
        fallback_preset="smoke",
        fallback_recipe=["missing_len_zero_guard", "dict_missing_key_guard"],
        auto_remediate_benchmark=True,
        source_cache_dir="cache/raw",
        auto_scoped_include=True,
        repository_test_root="repo_checkout",
        repository_test_timeout=13,
        repository_test_failure_overlay_candidate_limit=6,
        repository_test_reflection_mode="none",
        repository_test_reflection_rounds=2,
        repository_test_reflection_width=3,
        run_repository_test_environment_setup=True,
        run_repository_test_retry=True,
        run_repository_test_retry_prerequisites=True,
        auto_repository_test_retry=True,
        auto_repository_test_retry_max_risk="medium",
        auto_repository_test_retry_allowed_runners=["pytest", "unittest"],
        repository_test_environment_setup_timeout=31,
        checkout_repository_tests=True,
        repository_checkout_timeout=41,
        repository_checkout_depth=2,
        no_repository_test_command=True,
        expected_repository_processing={
            "target_status": "repaired",
            "target_primary_layer": "repository_repair",
            "target_repair_summary_status": "pass",
            "target_repair_summary_conclusion": "ready_for_review",
            "target_repair_summary_path_contains": (
                "repository_test_repair_summary.md"
            ),
        },
        promotion_gate={
            "min_smoke_benchmark_cases": 0,
            "min_repository_test_repaired_count": 1,
            "allow_warning_stages": True,
        },
    )

    assert manifest["suite_name"] == "example_project_pipeline"
    assert manifest["single_repo_metadata"] == {
        "kind": "single_repo_pipeline",
        "repo": "https://github.com/example/project",
        "ref": "main",
        "run_name": "example_project",
        "suite_name": "example_project_pipeline",
        "preset": "mining",
        "include": ["maths/average_mean.py"],
        "exclude": ["docs/"],
        "expected_repository_processing_present": True,
        "promotion_gate_present": True,
    }
    assert manifest["runs"] == [
        {
            "name": "example_project",
            "mode": "repo",
            "repo": "https://github.com/example/project",
            "preset": "mining",
            "ref": "main",
            "include": ["maths/average_mean.py"],
            "exclude": ["docs/"],
            "sample_sources": 7,
            "max_candidates": 4,
            "auto_fallback": False,
            "auto_remediate_benchmark": True,
            "thresholds": {"min_generated_candidates": 3},
            "fallback": {
                "max_sources": 31,
                "max_candidates": 11,
                "preset": "smoke",
                "recipe": [
                    "missing_len_zero_guard",
                    "dict_missing_key_guard",
                ],
                "enabled": True,
            },
            "source_cache_dir": "cache/raw",
            "auto_scoped_include": True,
            "repository_test_root": "repo_checkout",
            "repository_test_timeout": 13,
            "repository_test_failure_overlay_candidate_limit": 6,
            "repository_test_reflection_mode": "none",
            "repository_test_reflection_rounds": 2,
            "repository_test_reflection_width": 3,
            "run_repository_test_environment_setup": True,
            "run_repository_test_retry": True,
            "run_repository_test_retry_prerequisites": True,
            "auto_repository_test_retry": True,
            "auto_repository_test_retry_max_risk": "medium",
            "auto_repository_test_retry_allowed_runners": ["pytest", "unittest"],
            "repository_test_environment_setup_timeout": 31,
            "checkout_repository_tests": True,
            "repository_checkout_timeout": 41,
            "repository_checkout_depth": 2,
            "no_repository_test_command": True,
            "expected_repository_processing": {
                "target_status": "repaired",
                "target_primary_layer": "repository_repair",
                "target_repair_summary_status": "pass",
                "target_repair_summary_conclusion": "ready_for_review",
                "target_repair_summary_path_contains": (
                    "repository_test_repair_summary.md"
                ),
            },
        }
    ]
    assert manifest["repository_processing_matrix_expectations"] == {
        "min_run_count": 1,
        "run_expectations": {
            "example_project": {
                "target_status": "repaired",
                "target_primary_layer": "repository_repair",
                "target_repair_summary_status": "pass",
                "target_repair_summary_conclusion": "ready_for_review",
                "target_repair_summary_path_contains": (
                    "repository_test_repair_summary.md"
                ),
            }
        },
        "min_status_counts": {"repaired": 1},
        "min_primary_layer_counts": {"repository_repair": 1},
        "min_repair_summary_status_counts": {"pass": 1},
        "min_repair_summary_conclusion_counts": {"ready_for_review": 1},
    }
    assert manifest["promotion_gate"] == {
        "min_smoke_benchmark_cases": 0,
        "min_repository_test_repaired_count": 1,
        "allow_warning_stages": True,
    }


def test_single_repo_pipeline_example_manifest_documents_pinned_repo_target():
    manifest_path = Path("datasets/github_cases/single_repo_pipeline.example.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["suite_name"] == "github_single_repo_pipeline_example"
    assert manifest["defaults"]["mode"] == "repo"
    assert manifest["defaults"]["preset"] == "smoke"
    assert manifest["defaults"]["fallback"] == {
        "enabled": True,
        "max_sources": 50,
        "max_candidates": 20,
    }
    assert manifest["defaults"]["no_repository_test_command"] is True
    assert manifest["promotion_gate"] == {
        "min_smoke_benchmark_cases": 1,
        "allow_warning_stages": True,
    }
    assert manifest["runs"] == [
        {
            "name": "thealgorithms_average_mean_pipeline",
            "repo": "https://github.com/TheAlgorithms/Python",
            "ref": "6c0462028f547fc905a4d9a8cc956daed8a00cd8",
            "include": ["maths/average_mean.py"],
        }
    ]


def test_checkout_pipeline_pluggy_example_manifest_documents_real_test_execution():
    manifest_path = Path("datasets/github_cases/checkout_pipeline_pluggy.example.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["suite_name"] == "github_checkout_pipeline_pluggy_example"
    assert manifest["defaults"]["mode"] == "repo"
    assert manifest["defaults"]["checkout_repository_tests"] is True
    assert manifest["defaults"]["repository_test_timeout"] == 20
    assert (
        manifest["defaults"]["repository_test_failure_overlay_candidate_limit"] == 8
    )
    assert manifest["defaults"]["fallback"] == {
        "enabled": True,
        "max_sources": 50,
        "max_candidates": 20,
    }
    assert manifest["promotion_gate"] == {
        "min_smoke_benchmark_cases": 1,
        "allow_warning_stages": True,
    }
    assert manifest["runs"] == [
        {
            "name": "pluggy_checkout_pipeline",
            "repo": "https://github.com/pytest-dev/pluggy",
            "ref": "7fce99cb955846901b22b051909aa4f30dc16128",
            "include": ["src/pluggy/_tracing.py"],
        }
    ]


def test_checkout_pipeline_thealgorithms_gronsfeld_manifest_documents_overlay_repair():
    manifest_path = Path(
        "datasets/github_cases/checkout_pipeline_thealgorithms_gronsfeld.example.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert (
        manifest["suite_name"]
        == "github_checkout_pipeline_thealgorithms_gronsfeld_example"
    )
    assert manifest["defaults"]["mode"] == "repo"
    assert manifest["defaults"]["checkout_repository_tests"] is True
    assert manifest["defaults"]["repository_test_timeout"] == 3
    assert (
        manifest["defaults"]["repository_test_failure_overlay_candidate_limit"]
        == 5
    )
    assert manifest["promotion_gate"] == {
        "min_smoke_benchmark_cases": 0,
        "min_repository_test_phase2_ready_count": 1,
        "min_repository_test_phase3_validation_ready_count": 1,
        "min_repository_test_repaired_count": 1,
        "allow_warning_stages": True,
    }
    assert manifest["runs"] == [
        {
            "name": "thealgorithms_gronsfeld_overlay_pipeline",
            "repo": "https://github.com/TheAlgorithms/Python",
            "ref": "6c0462028f547fc905a4d9a8cc956daed8a00cd8",
            "include": ["ciphers/gronsfeld_cipher.py"],
        }
    ]


def test_repository_processing_matrix_example_manifest_documents_real_repo_set():
    manifest_path = Path(
        "datasets/github_cases/repository_processing_matrix.example.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["suite_name"] == "github_repository_processing_matrix_example"
    assert manifest["defaults"]["mode"] == "repo"
    assert manifest["defaults"]["checkout_repository_tests"] is True
    assert manifest["defaults"]["repository_checkout_depth"] == 1
    assert manifest["defaults"]["repository_test_failure_overlay_candidate_limit"] == 8
    assert manifest["defaults"]["fallback"] == {
        "enabled": True,
        "max_sources": 50,
        "max_candidates": 20,
    }
    expectations = manifest["repository_processing_matrix_expectations"]
    assert expectations["min_run_count"] == 3
    assert expectations["min_primary_layer_counts"] == {"repository_repair": 1}
    assert expectations["min_repair_summary_status_counts"] == {"pass": 1}
    assert expectations["min_repair_summary_conclusion_counts"] == {
        "ready_for_review": 1
    }
    assert set(expectations["run_expectations"]) == {
        "thealgorithms_gronsfeld_matrix",
        "pluggy_tracing_matrix",
        "requests_models_matrix",
    }
    compiled_expectations = _pipeline_repository_processing_expectations(manifest)
    assert compiled_expectations["run_expectations"][
        "thealgorithms_gronsfeld_matrix"
    ]["allowed_repair_summary_statuses"] == ["pass", "warning"]
    assert compiled_expectations["run_expectations"]["pluggy_tracing_matrix"][
        "allowed_primary_layers"
    ] == ["repository_repair", "fault_localization", "repository_test_execution"]
    assert manifest["promotion_gate"] == {
        "min_smoke_benchmark_cases": 0,
        "min_repository_test_phase2_ready_count": 1,
        "min_repository_test_phase3_validation_ready_count": 1,
        "min_repository_test_repaired_count": 1,
        "allow_warning_stages": True,
    }
    runs = {run["name"]: run for run in manifest["runs"]}
    assert runs["thealgorithms_gronsfeld_matrix"] == {
        "name": "thealgorithms_gronsfeld_matrix",
        "repo": "https://github.com/TheAlgorithms/Python",
        "ref": "6c0462028f547fc905a4d9a8cc956daed8a00cd8",
        "include": ["ciphers/gronsfeld_cipher.py"],
        "sample_sources": 5,
        "repository_test_timeout": 3,
        "repository_test_failure_overlay_candidate_limit": 5,
        "expected_repository_processing": {
            "target_status": "repaired",
            "target_primary_layer": "repository_repair",
        },
    }
    assert runs["pluggy_tracing_matrix"]["repo"] == (
        "https://github.com/pytest-dev/pluggy"
    )
    assert runs["pluggy_tracing_matrix"]["ref"] == (
        "7fce99cb955846901b22b051909aa4f30dc16128"
    )
    assert runs["pluggy_tracing_matrix"]["include"] == ["src/pluggy/_tracing.py"]
    assert runs["requests_models_matrix"]["repo"] == "https://github.com/psf/requests"
    assert runs["requests_models_matrix"]["ref"] == "v2.31.0"
    assert runs["requests_models_matrix"]["target_prefix"] == "requests"
    assert runs["requests_models_matrix"]["run_repository_test_environment_setup"] is True
    assert (
        runs["requests_models_matrix"]["run_repository_test_retry_prerequisites"]
        is True
    )


def test_pipeline_cli_repo_mode_generates_manifest_and_runs_pipeline():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_source(root)
        output_dir = root / "repo_pipeline_output"
        output_json = root / "repo_pipeline.json"
        opener = _FakeOpener(
            [
                _tree_payload(raw_source),
            ]
        )

        with pytest.raises(SystemExit) as exc_info:
            pipeline_main(
                [
                    "https://github.com/example/project",
                    str(output_dir),
                    "--repo-mode",
                    "--ref",
                    "main",
                    "--include",
                    "maths/average_mean.py",
                    "--sample-sources",
                    "5",
                    "--max-candidates",
                    "5",
                    "--auto-fallback",
                    "--fallback-max-sources",
                    "17",
                    "--fallback-max-candidates",
                    "9",
                    "--fallback-preset",
                    "smoke",
                    "--fallback-recipe",
                    "missing_len_zero_guard",
                    "--auto-remediate-benchmark",
                    "--no-repository-test-command",
                    "--repository-test-timeout",
                    "9",
                    "--repository-test-failure-overlay-candidate-limit",
                    "6",
                    "--repository-test-reflection-mode",
                    "none",
                    "--checkout-repository-tests",
                    "--repository-checkout-timeout",
                    "13",
                    "--run-repository-test-retry-prerequisites",
                    "--skip-recommendation-rerun",
                    "--format",
                    "json",
                    "--output-json",
                    str(output_json),
                ],
                opener=opener,
            )

        saved = json.loads(output_json.read_text(encoding="utf-8"))
        generated_manifest = output_dir / "single_repo_pipeline_manifest.json"
        manifest_payload = json.loads(generated_manifest.read_text(encoding="utf-8"))
        smoke_manifest_payload = json.loads(
            Path(saved["output_paths"]["preflight_smoke_manifest"]).read_text(
                encoding="utf-8"
            )
        )

        assert exc_info.value.code == 1
        assert opener.urls == [
            "https://api.github.com/repos/example/project/git/trees/main?recursive=1",
        ]
        assert saved["passed"] is False
        assert saved["manifest_path"] == str(generated_manifest)
        assert saved["output_paths"]["input_manifest"] == str(generated_manifest)
        assert saved["summary"]["single_repo_present"] is True
        assert saved["summary"]["single_repo_repo"] == (
            "https://github.com/example/project"
        )
        assert saved["summary"]["single_repo_ref"] == "main"
        assert saved["summary"]["single_repo_run_name"] == "example_project"
        assert saved["summary"]["single_repo_preset"] == "smoke"
        assert saved["summary"]["single_repo_include"] == [
            "maths/average_mean.py"
        ]
        assert saved["summary"]["single_repo_metadata"]["source"] == (
            "single_repo_metadata"
        )
        assert saved["summary"]["preflight_ready_count"] == 1
        assert saved["summary"]["smoke_present"] is True
        assert saved["summary"]["smoke_passed"] is False
        assert saved["summary"]["smoke_run_count"] == 1
        assert saved["summary"]["smoke_benchmark_cases"] >= 1
        assert saved["summary"]["promotion_status"] == "blocked"
        assert "Blocking stages: smoke_benchmark." in saved["summary"][
            "promotion_blocking_reasons"
        ]
        assert manifest_payload["runs"][0]["mode"] == "repo"
        assert manifest_payload["single_repo_metadata"]["repo"] == (
            "https://github.com/example/project"
        )
        assert manifest_payload["single_repo_metadata"]["run_name"] == (
            "example_project"
        )
        assert manifest_payload["runs"][0]["repo"] == (
            "https://github.com/example/project"
        )
        assert manifest_payload["runs"][0]["include"] == [
            "maths/average_mean.py"
        ]
        assert manifest_payload["runs"][0]["auto_fallback"] is True
        assert manifest_payload["runs"][0]["auto_remediate_benchmark"] is True
        assert manifest_payload["runs"][0]["fallback"] == {
            "max_sources": 17,
            "max_candidates": 9,
            "preset": "smoke",
            "recipe": ["missing_len_zero_guard"],
            "enabled": True,
        }
        assert smoke_manifest_payload["runs"][0]["auto_fallback"] is True
        assert smoke_manifest_payload["runs"][0]["auto_remediate_benchmark"] is True
        assert smoke_manifest_payload["runs"][0]["fallback"] == {
            "max_sources": 17,
            "max_candidates": 9,
            "preset": "smoke",
            "recipe": ["missing_len_zero_guard"],
            "enabled": True,
        }
        smoke_run = smoke_manifest_payload["runs"][0]
        assert smoke_run["mode"] == "from-discovery"
        assert smoke_run["discovery"].replace("\\", "/").endswith(
            "preflight_runs/example_project/preflight_discovery.json"
        )
        assert smoke_run["owner"] == "example"
        assert smoke_run["repo"] == "project"
        assert smoke_run["ref"] == "main"
        assert manifest_payload["runs"][0]["no_repository_test_command"] is True
        assert manifest_payload["runs"][0]["repository_test_timeout"] == 9
        assert (
            manifest_payload["runs"][0][
                "repository_test_failure_overlay_candidate_limit"
            ]
            == 6
        )
        assert (
            manifest_payload["runs"][0]["repository_test_reflection_mode"]
            == "none"
        )
        assert manifest_payload["runs"][0]["checkout_repository_tests"] is True
        assert manifest_payload["runs"][0]["repository_checkout_timeout"] == 13
        assert (
            manifest_payload["runs"][0]["run_repository_test_retry_prerequisites"]
            is True
        )
        assert smoke_manifest_payload["runs"][0]["no_repository_test_command"] is True
        assert smoke_manifest_payload["runs"][0]["repository_test_timeout"] == 9
        assert (
            smoke_manifest_payload["runs"][0][
                "repository_test_failure_overlay_candidate_limit"
            ]
            == 6
        )
        assert (
            smoke_manifest_payload["runs"][0]["repository_test_reflection_mode"]
            == "none"
        )
        assert smoke_manifest_payload["runs"][0]["checkout_repository_tests"] is True
        assert smoke_manifest_payload["runs"][0]["repository_checkout_timeout"] == 13
        assert (
            smoke_manifest_payload["runs"][0][
                "run_repository_test_retry_prerequisites"
            ]
            is True
        )
        assert Path(saved["output_paths"]["pipeline_json"]).exists()


def test_pipeline_cli_repo_mode_writes_repository_repair_manifest_when_tests_not_executed():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_source(root)
        output_dir = root / "repo_pipeline_output"
        output_json = root / "repo_pipeline.json"
        output_markdown = root / "repo_pipeline.md"
        opener = _FakeOpener([_tree_payload(raw_source)])

        with pytest.raises(SystemExit) as exc_info:
            pipeline_main(
                [
                    "https://github.com/example/project",
                    str(output_dir),
                    "--repo-mode",
                    "--ref",
                    "main",
                    "--include",
                    "maths/average_mean.py",
                    "--sample-sources",
                    "5",
                    "--max-candidates",
                    "5",
                    "--format",
                    "json",
                    "--output-json",
                    str(output_json),
                    "--output-markdown",
                    str(output_markdown),
                ],
                opener=opener,
            )

        saved = json.loads(output_json.read_text(encoding="utf-8"))
        markdown = output_markdown.read_text(encoding="utf-8")
        repair_manifest_path = Path(
            saved["output_paths"]["repository_repair_manifest"]
        )
        repair_manifest_markdown_path = Path(
            saved["output_paths"]["repository_repair_manifest_markdown"]
        )
        repair_manifest = json.loads(repair_manifest_path.read_text(encoding="utf-8"))
        repair_manifest_markdown = repair_manifest_markdown_path.read_text(
            encoding="utf-8"
        )
        repair_run = repair_manifest["runs"][0]
        metadata = repair_manifest["repository_repair_recommendation_metadata"]
        expected_changed_fields = [
            "checkout_repository_tests",
            "auto_scoped_include",
            "auto_fallback",
            "thresholds.min_generated_candidates",
            "fallback.enabled",
            "fallback.max_sources",
            "fallback.max_candidates",
            "repository_test_timeout",
            "repository_test_failure_overlay_candidate_limit",
            "repository_test_reflection_mode",
            "repository_test_reflection_rounds",
            "repository_test_reflection_width",
            "repository_checkout_depth",
        ]

        assert "Single Repo Mode: true" in markdown
        assert (
            "Single Repo Target: repo=https://github.com/example/project; "
            "ref=main; run=example_project; preset=smoke"
        ) in markdown
        assert (
            "Single Repo Filters: include=maths/average_mean.py; exclude=none"
        ) in markdown
        assert exc_info.value.code == 1
        assert saved["summary"]["repository_repair_stage_status"] == "skip"
        assert saved["summary"]["repository_repair_manifest_present"] is True
        assert saved["summary"]["repository_repair_manifest_path"] == str(
            repair_manifest_path
        )
        assert saved["summary"]["repository_repair_manifest_markdown_path"] == str(
            repair_manifest_markdown_path
        )
        assert saved["summary"]["repository_repair_manifest_applied_run_count"] == 1
        assert saved["summary"]["repository_repair_manifest_changed_run_count"] == 1
        assert saved["summary"]["repository_repair_manifest_applied_defaults"] == sorted(
            expected_changed_fields
        )
        assert saved["summary"][
            "repository_repair_manifest_promotion_gate_defaults"
        ] == [
            "min_smoke_benchmark_cases",
            "min_repository_test_phase2_ready_count",
            "min_repository_test_phase3_validation_ready_count",
            "min_repository_test_repaired_count",
            "allow_warning_stages",
        ]
        expected_repair_command = (
            "python -m code_intelligence_agent.evaluation.github_onboarding_pipeline "
            f"{repair_manifest_path} {output_dir / 'repository_repair'}"
        )
        assert saved["summary"]["repository_repair_manifest_command"] == (
            expected_repair_command
        )
        assert (
            saved["summary"][
                "repository_repair_manifest_setup_repair_defaults_applied"
            ]
            is False
        )
        assert saved["summary"][
            "repository_repair_manifest_setup_install_failure_counts"
        ] == {}
        assert (
            saved["summary"]["repository_repair_manifest_top_setup_install_failure"]
            == ""
        )
        assert (
            saved["summary"]["repository_repair_manifest_setup_repair_next_action"]
            == ""
        )
        assert saved["summary"]["repository_repair_manifest_run_context_count"] == 1
        assert saved["summary"][
            "repository_repair_manifest_setup_repair_run_names"
        ] == []
        assert saved["summary"][
            "repository_repair_manifest_checkout_only_run_names"
        ] == ["example_project"]
        assert repair_manifest_path.exists()
        assert repair_manifest_markdown_path.exists()
        assert metadata["pipeline_manifest_kind"] == "repository_repair_manifest"
        assert metadata["source_manifest"] == str(
            output_dir / "single_repo_pipeline_manifest.json"
        )
        assert metadata["reason"] == "repository_repair_stage_not_executed"
        assert metadata["applied_run_count"] == 1
        assert metadata["changed_run_count"] == 1
        assert metadata["applied_run_names"] == ["example_project"]
        assert metadata["applied_default_fields"] == sorted(expected_changed_fields)
        assert metadata["changed_fields_by_run"] == {
            "example_project": expected_changed_fields
        }
        assert metadata["run_repair_context_count"] == 1
        assert metadata["setup_repair_run_names"] == []
        assert metadata["checkout_only_run_names"] == ["example_project"]
        assert metadata["promotion_gate_defaults"] == [
            "min_smoke_benchmark_cases",
            "min_repository_test_phase2_ready_count",
            "min_repository_test_phase3_validation_ready_count",
            "min_repository_test_repaired_count",
            "allow_warning_stages",
        ]
        assert repair_manifest["promotion_gate"] == {
            "min_smoke_benchmark_cases": 0,
            "min_repository_test_phase2_ready_count": 1,
            "min_repository_test_phase3_validation_ready_count": 1,
            "min_repository_test_repaired_count": 1,
            "allow_warning_stages": True,
        }
        assert repair_run["repo"] == "https://github.com/example/project"
        assert repair_run["checkout_repository_tests"] is True
        assert repair_run["auto_scoped_include"] is True
        assert repair_run["auto_fallback"] is True
        assert repair_run["thresholds"] == {"min_generated_candidates": 1}
        assert repair_run["fallback"] == {
            "enabled": True,
            "max_sources": 50,
            "max_candidates": 20,
        }
        assert repair_run["repository_test_timeout"] == 20
        assert repair_run["repository_test_failure_overlay_candidate_limit"] == 8
        assert repair_run["repository_test_reflection_mode"] == "rule"
        assert repair_run["repository_test_reflection_rounds"] == 1
        assert repair_run["repository_test_reflection_width"] == 1
        assert repair_run["repository_checkout_depth"] == 1
        assert "Repository Repair Manifest Present: true" in markdown
        assert "Repository Repair Manifest Applied Runs: 1" in markdown
        assert "Repository Repair Manifest Defaults:" in markdown
        assert "Repository Repair Manifest Run Contexts: 1" in markdown
        assert "Repository Repair Manifest Checkout-Only Runs: example_project" in (
            markdown
        )
        assert "Repository Repair Manifest Markdown Path:" in markdown
        assert "Repository Repair Manifest Command" in markdown
        assert str(repair_manifest_path) in markdown
        assert str(repair_manifest_markdown_path) in markdown
        assert expected_repair_command in markdown
        assert "# GitHub Onboarding Repository Repair Manifest" in (
            repair_manifest_markdown
        )
        assert "Suite: `example_project_pipeline_repository_repair`" in (
            repair_manifest_markdown
        )
        assert "Checkout-Only Runs: example_project" in repair_manifest_markdown
        assert "| example_project | `https://github.com/example/project` |" in (
            repair_manifest_markdown
        )
        assert "| repository_test_timeout | 20 |" in repair_manifest_markdown


def test_repository_repair_manifest_supports_batch_repo_defaults():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        source_manifest_path = root / "batch_repo_manifest.json"
        original_manifest = {
            "suite_name": "batch_repo_defaults",
            "defaults": {
                "mode": "repo",
                "preset": "smoke",
                "sample_sources": 5,
            },
            "runs": [
                {
                    "name": "repo_a",
                    "repo": "https://github.com/example/a",
                },
                {
                    "name": "repo_b",
                    "repo": "https://github.com/example/b",
                    "checkout_repository_tests": True,
                    "repository_test_timeout": 7,
                },
                {
                    "name": "malformed_repo",
                },
                {
                    "name": "offline_from_discovery",
                    "mode": "from-discovery",
                    "repo": "offline_only",
                },
            ],
        }
        original_snapshot = json.loads(json.dumps(original_manifest))

        repair_manifest = _build_repository_repair_manifest(
            original_manifest,
            source_manifest_path=source_manifest_path,
        )

        assert original_manifest == original_snapshot
        assert repair_manifest["suite_name"] == "batch_repo_defaults_repository_repair"
        assert repair_manifest["runs"][0]["checkout_repository_tests"] is True
        assert repair_manifest["runs"][0]["repository_test_timeout"] == 20
        assert repair_manifest["runs"][0]["fallback"] == {
            "enabled": True,
            "max_sources": 50,
            "max_candidates": 20,
        }
        assert repair_manifest["runs"][1]["checkout_repository_tests"] is True
        assert repair_manifest["runs"][1]["repository_test_timeout"] == 7
        assert "checkout_repository_tests" not in repair_manifest[
            "repository_repair_recommendation_metadata"
        ]["changed_fields_by_run"]["repo_b"]
        assert "repository_test_timeout" not in repair_manifest[
            "repository_repair_recommendation_metadata"
        ]["changed_fields_by_run"]["repo_b"]
        assert repair_manifest["runs"][2] == {"name": "malformed_repo"}
        assert repair_manifest["runs"][3] == {
            "name": "offline_from_discovery",
            "mode": "from-discovery",
            "repo": "offline_only",
        }

        metadata = repair_manifest["repository_repair_recommendation_metadata"]
        assert metadata["source_manifest"] == str(source_manifest_path)
        assert metadata["applied_run_count"] == 2
        assert metadata["changed_run_count"] == 2
        assert metadata["applied_run_names"] == ["repo_a", "repo_b"]
        assert set(metadata["changed_fields_by_run"]) == {"repo_a", "repo_b"}
        assert "repository_test_timeout" in metadata["changed_fields_by_run"]["repo_a"]
        assert "repository_test_timeout" not in metadata["changed_fields_by_run"][
            "repo_b"
        ]


def test_repository_repair_manifest_applies_setup_repair_context():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        source_manifest_path = root / "setup_failure_manifest.json"
        original_manifest = {
            "suite_name": "setup_failure_repo",
            "runs": [
                {
                    "name": "repo_setup_failure",
                    "mode": "repo",
                    "repo": "https://github.com/example/setup-failure",
                }
            ],
        }

        assert _should_write_repository_repair_manifest(
            {
                "repository_repair_stage_status": "warn",
                "repository_repair_stage_evidence": (
                    "reports=1, blocked=1, "
                    "top_setup_install_failure=editable_backend_unsupported"
                ),
                "repository_test_environment_setup_install_failure_counts": {
                    "editable_backend_unsupported": 1,
                },
            }
        )

        repair_manifest = _build_repository_repair_manifest(
            original_manifest,
            source_manifest_path=source_manifest_path,
            repair_context={
                "setup_install_failure_counts": {
                    "editable_backend_unsupported": 1,
                },
                "top_setup_install_failure": "editable_backend_unsupported",
                "setup_repair_next_action": (
                    "Inspect repository_test_environment_setup_result; "
                    "editable install failed."
                ),
            },
        )

        repair_run = repair_manifest["runs"][0]
        metadata = repair_manifest["repository_repair_recommendation_metadata"]

        assert repair_run["run_repository_test_environment_setup"] is True
        assert repair_run["run_repository_test_retry_prerequisites"] is True
        assert metadata["setup_install_failure_counts"] == {
            "editable_backend_unsupported": 1,
        }
        assert metadata["top_setup_install_failure"] == (
            "editable_backend_unsupported"
        )
        assert metadata["setup_repair_defaults_applied"] is True
        assert metadata["setup_repair_next_action"] == (
            "Inspect repository_test_environment_setup_result; "
            "editable install failed."
        )
        assert metadata["changed_fields_by_run"]["repo_setup_failure"][:3] == [
            "checkout_repository_tests",
            "run_repository_test_environment_setup",
            "run_repository_test_retry_prerequisites",
        ]
        assert "run_repository_test_environment_setup" in metadata[
            "applied_default_fields"
        ]


def test_repository_repair_manifest_uses_setup_doctor_context():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        source_manifest_path = root / "doctor_blocker_manifest.json"
        original_manifest = {
            "suite_name": "doctor_blocker_repo",
            "runs": [
                {
                    "name": "checkout_blocked",
                    "mode": "repo",
                    "repo": "https://github.com/example/checkout-blocked",
                },
                {
                    "name": "dependency_blocked",
                    "mode": "repo",
                    "repo": "https://github.com/example/dependency-blocked",
                },
            ],
        }

        assert _should_write_repository_repair_manifest(
            {
                "repository_repair_stage_status": "skip",
                "repository_repair_stage_evidence": "reports=1",
                "repository_test_setup_doctor_blocker_counts": {
                    "checkout:full_repo_not_materialized": 1,
                },
            }
        )
        checkout_manifest = _build_repository_repair_manifest(
            {"suite_name": "checkout_only", "runs": [original_manifest["runs"][0]]},
            source_manifest_path=source_manifest_path,
            repair_context={
                "setup_doctor_blocker_counts": {
                    "checkout:full_repo_not_materialized": 1,
                },
                "top_setup_doctor_blocker": "checkout:full_repo_not_materialized",
                "setup_doctor_next_action": (
                    "Enable --checkout-repository-tests or provide "
                    "--repository-test-root, then rerun the pipeline."
                ),
            },
        )
        checkout_run = checkout_manifest["runs"][0]
        checkout_metadata = checkout_manifest[
            "repository_repair_recommendation_metadata"
        ]

        assert checkout_run["checkout_repository_tests"] is True
        assert "run_repository_test_environment_setup" not in checkout_run
        assert checkout_metadata["setup_repair_defaults_applied"] is False
        assert checkout_metadata["setup_doctor_blocker_counts"] == {
            "checkout:full_repo_not_materialized": 1,
        }
        assert checkout_metadata["top_setup_doctor_blocker"] == (
            "checkout:full_repo_not_materialized"
        )

        setup_manifest = _build_repository_repair_manifest(
            {"suite_name": "setup_doctor", "runs": [original_manifest["runs"][1]]},
            source_manifest_path=source_manifest_path,
            repair_context={
                "setup_doctor_blocker_counts": {
                    "execution_failure:missing_dependency": 1,
                },
                "top_setup_doctor_blocker": (
                    "execution_failure:missing_dependency"
                ),
                "setup_doctor_next_action": (
                    "Install missing repository test dependencies and rerun "
                    "repository tests."
                ),
            },
        )
        setup_run = setup_manifest["runs"][0]
        setup_metadata = setup_manifest[
            "repository_repair_recommendation_metadata"
        ]

        assert setup_run["checkout_repository_tests"] is True
        assert setup_run["run_repository_test_environment_setup"] is True
        assert setup_run["run_repository_test_retry_prerequisites"] is True
        assert setup_metadata["setup_repair_defaults_applied"] is True
        assert setup_metadata["setup_doctor_blocker_counts"] == {
            "execution_failure:missing_dependency": 1,
        }
        assert setup_metadata["top_setup_doctor_blocker"] == (
            "execution_failure:missing_dependency"
        )
        assert setup_metadata["setup_repair_next_action"] == (
            "Install missing repository test dependencies and rerun "
            "repository tests."
        )

        mixed_manifest = _build_repository_repair_manifest(
            original_manifest,
            source_manifest_path=source_manifest_path,
            repair_context={
                "setup_doctor_blocker_counts": {
                    "checkout:full_repo_not_materialized": 1,
                    "execution_failure:missing_dependency": 1,
                },
                "top_setup_doctor_blocker": (
                    "checkout:full_repo_not_materialized"
                ),
                "run_repair_contexts": {
                    "checkout_blocked": {
                        "blocker": "checkout:full_repo_not_materialized",
                        "setup_repair": False,
                        "next_action": (
                            "Enable --checkout-repository-tests or provide "
                            "--repository-test-root, then rerun the pipeline."
                        ),
                    },
                    "dependency_blocked": {
                        "blocker": "execution_failure:missing_dependency",
                        "setup_repair": True,
                        "next_action": (
                            "Install missing repository test dependencies and "
                            "rerun repository tests."
                        ),
                    },
                },
            },
        )
        mixed_checkout_run = mixed_manifest["runs"][0]
        mixed_dependency_run = mixed_manifest["runs"][1]
        mixed_metadata = mixed_manifest[
            "repository_repair_recommendation_metadata"
        ]

        assert mixed_checkout_run["checkout_repository_tests"] is True
        assert "run_repository_test_environment_setup" not in mixed_checkout_run
        assert mixed_dependency_run["checkout_repository_tests"] is True
        assert mixed_dependency_run["run_repository_test_environment_setup"] is True
        assert (
            mixed_dependency_run["run_repository_test_retry_prerequisites"]
            is True
        )
        assert mixed_metadata["setup_repair_run_names"] == [
            "dependency_blocked"
        ]
        assert mixed_metadata["checkout_only_run_names"] == [
            "checkout_blocked"
        ]
        assert mixed_metadata["run_repair_context_count"] == 2
        assert mixed_metadata["run_repair_contexts"]["checkout_blocked"][
            "setup_repair"
        ] is False
        assert mixed_metadata["run_repair_contexts"]["dependency_blocked"][
            "setup_repair"
        ] is True
        repair_markdown = render_repository_repair_manifest_markdown(mixed_manifest)

        assert "# GitHub Onboarding Repository Repair Manifest" in repair_markdown
        assert "Setup Repair Runs: dependency_blocked" in repair_markdown
        assert "Checkout-Only Runs: checkout_blocked" in repair_markdown
        assert (
            "| checkout_blocked | `https://github.com/example/checkout-blocked` |"
        ) in repair_markdown
        assert (
            "| dependency_blocked | `https://github.com/example/dependency-blocked` |"
        ) in repair_markdown
        assert "execution_failure:missing_dependency" in repair_markdown


def test_pipeline_cli_repo_repair_profile_writes_reproducible_manifest_defaults():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_source(root)
        output_dir = root / "repo_repair_profile_output"
        output_json = root / "repo_repair_profile.json"
        output_markdown = root / "repo_repair_profile.md"
        opener = _FakeOpener(
            [
                _tree_payload(raw_source),
                _tree_payload(raw_source),
            ]
        )

        with pytest.raises(SystemExit) as exc_info:
            pipeline_main(
                [
                    "https://github.com/example/project",
                    str(output_dir),
                    "--repo-mode",
                    "--repo-repair-profile",
                    "--ref",
                    "main",
                    "--no-repository-test-command",
                    "--format",
                    "json",
                    "--output-json",
                    str(output_json),
                    "--output-markdown",
                    str(output_markdown),
                ],
                opener=opener,
            )

        saved = json.loads(output_json.read_text(encoding="utf-8"))
        markdown = output_markdown.read_text(encoding="utf-8")
        generated_manifest = output_dir / "single_repo_pipeline_manifest.json"
        manifest_payload = json.loads(generated_manifest.read_text(encoding="utf-8"))
        smoke_manifest_payload = json.loads(
            Path(saved["output_paths"]["preflight_smoke_manifest"]).read_text(
                encoding="utf-8"
            )
        )
        run = manifest_payload["runs"][0]
        smoke_run = smoke_manifest_payload["runs"][0]

        assert exc_info.value.code == 1
        assert run["checkout_repository_tests"] is True
        assert run["auto_scoped_include"] is True
        assert "include" not in run
        assert smoke_run["auto_scoped_include"] is True
        assert smoke_run["auto_scoped_include_count"] == 1
        assert smoke_run["auto_scoped_include_source"] == (
            "preflight_sampled_sources"
        )
        assert smoke_run["include"] == ["maths/average_mean.py"]
        assert run["auto_fallback"] is True
        assert run["thresholds"] == {"min_generated_candidates": 1}
        assert run["fallback"] == {
            "max_sources": 50,
            "max_candidates": 20,
            "enabled": True,
        }
        assert run["repository_test_timeout"] == 20
        assert run["repository_test_failure_overlay_candidate_limit"] == 8
        assert run["repository_test_reflection_mode"] == "rule"
        assert run["repository_test_reflection_rounds"] == 1
        assert run["repository_test_reflection_width"] == 1
        assert run["repository_checkout_depth"] == 1
        assert run["auto_repository_test_retry"] is True
        assert run["auto_repository_test_retry_max_risk"] == "low"
        assert run["auto_repository_test_retry_allowed_runners"] == [
            "pytest",
            "unittest",
        ]
        assert run["expected_repository_processing"] == {
            "target_status": "repaired",
            "target_primary_layer": "repository_repair",
            "target_repair_summary_status": "pass",
            "target_repair_summary_conclusion": "ready_for_review",
            "target_repair_summary_path_contains": (
                "repository_test_repair_summary.md"
            ),
        }
        assert smoke_run["auto_repository_test_retry"] is True
        assert smoke_run["auto_repository_test_retry_max_risk"] == "low"
        assert smoke_run["auto_repository_test_retry_allowed_runners"] == [
            "pytest",
            "unittest",
        ]
        assert manifest_payload["repository_processing_matrix_expectations"] == {
            "min_run_count": 1,
            "run_expectations": {
                "example_project": {
                    "target_status": "repaired",
                    "target_primary_layer": "repository_repair",
                    "target_repair_summary_status": "pass",
                    "target_repair_summary_conclusion": "ready_for_review",
                    "target_repair_summary_path_contains": (
                        "repository_test_repair_summary.md"
                    ),
                }
            },
            "min_status_counts": {"repaired": 1},
            "min_primary_layer_counts": {"repository_repair": 1},
            "min_repair_summary_status_counts": {"pass": 1},
            "min_repair_summary_conclusion_counts": {"ready_for_review": 1},
        }
        assert manifest_payload["promotion_gate"] == {
            "min_smoke_benchmark_cases": 0,
            "min_repository_test_phase2_ready_count": 1,
            "min_repository_test_phase3_validation_ready_count": 1,
            "min_repository_test_repaired_count": 1,
            "allow_warning_stages": True,
        }
        assert saved["summary"]["promotion_criteria"][
            "min_repository_test_repaired_count"
        ] == 1
        assert saved["summary"]["promotion_criteria"][
            "min_smoke_benchmark_cases"
        ] == 0
        assert saved["summary"]["promotion_criteria"][
            "allow_warning_stages"
        ] is True
        assert saved["summary"]["repository_processing_acceptance_mode"] == (
            "repository_processing_expectations"
        )
        assert saved["summary"]["repository_processing_expectation_passed"] is False
        assert saved["summary"]["repository_processing_expectation_check_count"] > 0
        failed_preview = saved["summary"][
            "repository_processing_expectation_failed_check_preview"
        ]
        assert failed_preview
        assert (
            saved["summary"]["repository_processing_expectation_first_failed_check"]
            == failed_preview[0]
        )
        assert saved["manifest_path"] == str(generated_manifest)
        assert saved["output_paths"]["input_manifest"] == str(generated_manifest)
        assert "Pipeline Rerun Command" in markdown
        assert "Repository Processing First Failed Expectation" in markdown
        assert failed_preview[0]["name"] in markdown
        assert (
            "python -m code_intelligence_agent.evaluation.github_onboarding_pipeline "
            f"{generated_manifest} {output_dir.with_name(output_dir.name + '_rerun')}"
        ) in markdown


def _write_average_discovery(root: Path) -> Path:
    raw_source = root / "average_mean.py"
    raw_source.write_text(
        "def mean(nums):\n"
        "    if not nums:\n"
        "        raise ValueError(\"List is empty\")\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
    discovery = root / "average.discovery.json"
    discovery.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "path": "maths/average_mean.py",
                        "raw_url": str(raw_source),
                        "target_path": "average_mean.py",
                        "owner": "example",
                        "repo": "algorithms",
                        "ref": "v1.0.0",
                        "sha256": hashlib.sha256(
                            raw_source.read_bytes()
                        ).hexdigest(),
                        "license": "MIT",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return discovery


def _write_average_source(root: Path) -> Path:
    raw_source = root / "average_mean.py"
    raw_source.write_text(
        "def mean(nums):\n"
        "    if not nums:\n"
        "        raise ValueError(\"List is empty\")\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
    return raw_source


def _tree_payload(raw_source: Path) -> dict:
    return {
        "sha": "abc123",
        "tree": [
            {
                "path": "maths/average_mean.py",
                "type": "blob",
                "raw_url": str(raw_source),
                "sha256": hashlib.sha256(raw_source.read_bytes()).hexdigest(),
                "license": "MIT",
            }
        ],
    }


def _write_docs_discovery(root: Path) -> Path:
    readme = root / "README.md"
    readme.write_text("# docs\n", encoding="utf-8")
    discovery = root / "docs.discovery.json"
    discovery.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "path": "README.md",
                        "raw_url": str(readme),
                        "target_path": "README.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return discovery


def _write_mixed_recipe_discovery(root: Path) -> Path:
    average_source = root / "average_mean.py"
    average_source.write_text(
        "def mean(nums):\n"
        "    if not nums:\n"
        "        raise ValueError(\"List is empty\")\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
    score_source = root / "score_lookup.py"
    score_source.write_text(
        "def score_for(scores, name):\n"
        "    return scores.get(name, 0)\n",
        encoding="utf-8",
    )
    discovery = root / "mixed_recipes.discovery.json"
    discovery.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "path": "maths/average_mean.py",
                        "raw_url": str(average_source),
                        "target_path": "average_mean.py",
                        "owner": "example",
                        "repo": "mixed",
                        "ref": "v1.0.0",
                        "sha256": hashlib.sha256(
                            average_source.read_bytes()
                        ).hexdigest(),
                        "license": "MIT",
                    },
                    {
                        "path": "metrics/score_lookup.py",
                        "raw_url": str(score_source),
                        "target_path": "score_lookup.py",
                        "owner": "example",
                        "repo": "mixed",
                        "ref": "v1.0.0",
                        "sha256": hashlib.sha256(
                            score_source.read_bytes()
                        ).hexdigest(),
                        "license": "MIT",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return discovery


class _FakeResponse:
    def __init__(self, payload):
        if isinstance(payload, bytes):
            self.payload = payload
        elif isinstance(payload, str):
            self.payload = payload.encode("utf-8")
        else:
            self.payload = json.dumps(payload).encode("utf-8")
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self.payload) - self.offset
        start = self.offset
        end = min(len(self.payload), start + size)
        self.offset = end
        return self.payload[start:end]


class _FakeOpener:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.urls = []

    def __call__(self, request, timeout):
        del timeout
        self.urls.append(request.full_url)
        return _FakeResponse(self.payloads.pop(0))
