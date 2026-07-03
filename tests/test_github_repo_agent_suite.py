import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from code_intelligence_agent.evaluation.github_repo_agent_suite import (
    GitHubRepoAgentSuiteRunResult,
    _suite_summary,
    main as repo_agent_suite_main,
    render_github_repo_agent_suite_markdown,
    run_github_repo_agent_suite,
)


def test_repo_agent_suite_example_manifest_exposes_repo_test_controls():
    manifest = json.loads(
        Path("datasets/github_cases/repo_agent_suite.example.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["defaults"]["source_cache_dir"] == (
        "outputs_smoke/github_raw_source_cache"
    )
    assert manifest["defaults"]["dependency_max_depth"] == 4
    assert manifest["defaults"][
        "repository_test_failure_overlay_candidate_limit"
    ] == 5
    assert manifest["defaults"]["auto_fallback"] is True
    assert manifest["defaults"]["fallback_max_sources"] == 50
    assert manifest["defaults"]["fallback_max_candidates"] == 20
    assert manifest["suite_thresholds"]["max_repository_test_blocked_count"] == 3


def test_repo_agent_suite_summarizes_framework_configuration_readiness():
    summary = _suite_summary(
        [
            GitHubRepoAgentSuiteRunResult(
                name="django_app",
                repo="example/django-app",
                output_dir="out/django_app",
                report_path="out/django_app/github_repo_agent.json",
                status="warning",
                passed=True,
                expected_status="",
                expectation_passed=True,
                diagnostic_issue_codes=[],
                metrics={
                    "framework_signals": ["django"],
                    "repository_test_frameworks": ["django"],
                    "repository_test_framework_configuration_status": "pass",
                    "repository_test_framework_configuration_reason": (
                        "django_settings_module_detected"
                    ),
                    "repository_config_snapshot_status": "pass",
                    "repository_config_snapshot_reason": "config_snapshot_built",
                    "repository_config_snapshot_file_count": 2,
                    "repository_test_pytest_config_source_count": 1,
                    "repository_test_pytest_addopts": ["--tb=short"],
                    "repository_test_pytest_testpaths": ["tests/unit"],
                    "repository_test_ci_config_source_count": 2,
                    "repository_test_ci_python_versions": ["3.10", "3.11"],
                    "repository_test_ci_install_command_candidates": [
                        "python -m pip install -r requirements-test.txt",
                    ],
                    "repository_test_ci_test_command_candidates": [
                        "python -m pytest --tb=short tests/unit",
                    ],
                    "repository_test_tox_envlist": ["py310", "py311"],
                    "planned_repository_test_runner": "pytest",
                    "planned_repository_test_source": "ci_config",
                    "planned_repository_test_ci_candidate_count": 1,
                    "planned_repository_test_ci_command_candidates": [
                        "python -m pytest --tb=short tests/unit",
                    ],
                    "planned_repository_test_environment_variable_names": [
                        "DJANGO_SETTINGS_MODULE",
                    ],
                    "repository_test_app_bootstrap_candidate_count": 1,
                    "repository_test_bootstrap_signal_count": 2,
                    "repository_test_final_status": "blocked",
                    "repository_test_final_reason": (
                        "framework_configuration_injected_but_failed:"
                        "django_settings_module_detected"
                    ),
                },
                metric_checks=[],
                command_args=[],
            )
        ],
        suite_thresholds={},
    )
    markdown = render_github_repo_agent_suite_markdown(
        type(
            "Report",
            (),
            {
                "suite_name": "framework_suite",
                "manifest_path": "manifest.json",
                "output_dir": "out",
                "passed": True,
                "summary": summary,
                "runs": [],
            },
        )()
    )

    assert summary["framework_signal_counts"] == {"django": 1}
    assert summary["repository_test_framework_configuration_status_counts"] == {
        "pass": 1
    }
    assert summary["repository_test_framework_configuration_reason_counts"] == {
        "django_settings_module_detected": 1
    }
    assert summary["repository_config_snapshot_count"] == 1
    assert summary["repository_config_snapshot_file_count"] == 2
    assert summary["repository_config_snapshot_status_counts"] == {"pass": 1}
    assert summary["repository_test_pytest_configured_count"] == 1
    assert summary["repository_test_pytest_testpath_run_count"] == 1
    assert summary["repository_test_pytest_addopts_run_count"] == 1
    assert summary["repository_test_ci_configured_count"] == 1
    assert summary["repository_test_ci_python_version_counts"] == {
        "3.10": 1,
        "3.11": 1,
    }
    assert summary["repository_test_ci_install_candidate_count"] == 1
    assert summary["repository_test_ci_test_command_candidate_count"] == 1
    assert summary["repository_test_tox_envlist_run_count"] == 1
    assert summary["planned_repository_test_runner_counts"] == {"pytest": 1}
    assert summary["planned_repository_test_runner_kind_count"] == 1
    assert summary["planned_repository_test_source_counts"] == {"ci_config": 1}
    assert summary["planned_repository_test_ci_candidate_count"] == 1
    assert summary["planned_repository_test_ci_candidate_runs"] == ["django_app"]
    assert summary["planned_repository_test_environment_variable_counts"] == {
        "DJANGO_SETTINGS_MODULE": 1
    }
    assert summary["repository_test_app_bootstrap_candidate_count"] == 1
    assert summary["repository_test_app_bootstrap_runs"] == ["django_app"]
    assert summary["repository_test_bootstrap_signal_count"] == 2
    assert summary["repository_test_bootstrap_signal_runs"] == ["django_app"]
    assert summary["repository_test_blocked_reason_counts"] == {
        "framework_configuration_injected_but_failed:django_settings_module_detected": 1
    }
    assert "Framework Test Configuration Statuses" in markdown
    assert "Planned Repository Test Runner Kinds" in markdown
    assert "Planned Repository Test Sources" in markdown
    assert "Repository Test Pytest Configured Runs" in markdown
    assert "Repository Test CI Configured Runs" in markdown
    assert "Repository Test App Bootstrap Candidates" in markdown


def test_repo_agent_suite_summarizes_runner_and_dynamic_evidence_diversity():
    summary = _suite_summary(
        [
            GitHubRepoAgentSuiteRunResult(
                name="pytest_repo",
                repo="example/pytest-repo",
                output_dir="out/pytest_repo",
                report_path="out/pytest_repo/github_repo_agent.json",
                status="pass",
                passed=True,
                expected_status="pass",
                expectation_passed=True,
                diagnostic_issue_codes=[],
                metrics={
                    "planned_repository_test_runner": "pytest",
                    "repository_test_dynamic_evidence_level": "passing_tests",
                    "repository_test_dynamic_usable_for_regression_validation": True,
                },
                metric_checks=[],
                command_args=[],
            ),
            GitHubRepoAgentSuiteRunResult(
                name="unittest_repo",
                repo="example/unittest-repo",
                output_dir="out/unittest_repo",
                report_path="out/unittest_repo/github_repo_agent.json",
                status="pass",
                passed=True,
                expected_status="pass",
                expectation_passed=True,
                diagnostic_issue_codes=[],
                metrics={
                    "planned_repository_test_runner": "unittest",
                    "repository_test_dynamic_evidence_level": "failing_tests",
                    "repository_test_dynamic_usable_for_localization": True,
                    "repository_test_dynamic_usable_for_patch_validation": True,
                },
                metric_checks=[],
                command_args=[],
            ),
        ],
        suite_thresholds={
            "min_planned_repository_test_runner_kind_count": 2,
            "min_repository_test_dynamic_evidence_level_kind_count": 2,
        },
    )
    markdown = render_github_repo_agent_suite_markdown(
        type(
            "Report",
            (),
            {
                "suite_name": "runner_matrix",
                "manifest_path": "manifest.json",
                "output_dir": "out",
                "passed": True,
                "summary": summary,
                "runs": [],
            },
        )()
    )

    assert summary["planned_repository_test_runner_counts"] == {
        "pytest": 1,
        "unittest": 1,
    }
    assert summary["planned_repository_test_runner_kind_count"] == 2
    assert summary["repository_test_dynamic_evidence_level_counts"] == {
        "failing_tests": 1,
        "passing_tests": 1,
    }
    assert summary["repository_test_dynamic_evidence_level_kind_count"] == 2
    assert summary["suite_threshold_failed_count"] == 0
    assert {
        check["name"]: check["passed"]
        for check in summary["suite_threshold_checks"]
    } == {
        "min_planned_repository_test_runner_kind_count": True,
        "min_repository_test_dynamic_evidence_level_kind_count": True,
    }
    assert "Planned Repository Test Runner Kinds: 2" in markdown
    assert "Repository Test Dynamic Evidence Level Kinds: 2" in markdown


def test_repo_agent_suite_summarizes_static_intelligence_readiness():
    summary = _suite_summary(
        [
            GitHubRepoAgentSuiteRunResult(
                name="static_ready",
                repo="example/static-ready",
                output_dir="out/static_ready",
                report_path="out/static_ready/github_repo_agent.json",
                status="pass",
                passed=True,
                expected_status="pass",
                expectation_passed=True,
                diagnostic_issue_codes=[],
                metrics={
                    "generated_candidates": 3,
                    "static_intelligence_status": "analysis_ready",
                    "static_intelligence_level": "static_signals",
                    "static_intelligence_selected_signal_count": 3,
                    "static_intelligence_total_signal_count": 5,
                    "static_intelligence_candidate_limit_applied": True,
                    "static_intelligence_rule_counts": {
                        "broad_exception_pass": 2,
                        "missing_len_zero_guard": 1,
                    },
                    "static_intelligence_bug_type_counts": {
                        "exception handling error": 2,
                        "boundary condition": 1,
                    },
                    "static_intelligence_quality_score": 0.6,
                    "static_intelligence_dynamic_validation_level": "not_executed",
                    "static_intelligence_primary_artifact": (
                        "out/static_ready/source_mining.md"
                    ),
                },
                metric_checks=[],
                command_args=[],
            ),
            GitHubRepoAgentSuiteRunResult(
                name="source_only",
                repo="example/source-only",
                output_dir="out/source_only",
                report_path="out/source_only/github_repo_agent.json",
                status="warning",
                passed=True,
                expected_status="warning",
                expectation_passed=True,
                diagnostic_issue_codes=["no_generated_candidates"],
                metrics={
                    "generated_candidates": 0,
                    "static_intelligence_status": "source_inventory_ready",
                    "static_intelligence_level": "source_only",
                    "static_intelligence_selected_signal_count": 0,
                    "static_intelligence_total_signal_count": 0,
                    "static_intelligence_quality_score": 0.4,
                    "static_intelligence_dynamic_validation_level": "none",
                },
                metric_checks=[],
                command_args=[],
            ),
        ],
        suite_thresholds={"min_static_intelligence_analysis_ready_count": 1},
    )
    markdown = render_github_repo_agent_suite_markdown(
        type(
            "Report",
            (),
            {
                "suite_name": "static_suite",
                "manifest_path": "manifest.json",
                "output_dir": "out",
                "passed": True,
                "summary": summary,
                "runs": [],
            },
        )()
    )

    assert summary["static_intelligence_run_count"] == 2
    assert summary["static_intelligence_analysis_ready_count"] == 1
    assert summary["static_intelligence_source_inventory_ready_count"] == 1
    assert summary["static_intelligence_selected_signal_count"] == 3
    assert summary["static_intelligence_total_signal_count"] == 5
    assert summary["static_intelligence_candidate_limit_applied_count"] == 1
    assert summary["static_intelligence_average_quality_score"] == 0.5
    assert summary["static_intelligence_status_counts"] == {
        "analysis_ready": 1,
        "source_inventory_ready": 1,
    }
    assert summary["static_intelligence_level_counts"] == {
        "source_only": 1,
        "static_signals": 1,
    }
    assert summary["static_intelligence_rule_counts"] == {
        "broad_exception_pass": 2,
        "missing_len_zero_guard": 1,
    }
    assert summary["static_intelligence_bug_type_counts"] == {
        "boundary condition": 1,
        "exception handling error": 2,
    }
    assert summary["static_intelligence_primary_artifact_runs"] == {
        "static_ready": "out/static_ready/source_mining.md"
    }
    assert summary["suite_threshold_failed_count"] == 0
    assert "Static Intelligence Runs: 2" in markdown
    assert "Static Intelligence Statuses: analysis_ready:1" in markdown
    assert "broad_exception_pass:2" in markdown


def test_repo_agent_suite_summarizes_environment_setup_install_diagnostics():
    summary = _suite_summary(
        [
            GitHubRepoAgentSuiteRunResult(
                name="editable_fallback",
                repo="example/editable",
                output_dir="out/editable",
                report_path="out/editable/github_repo_agent.json",
                status="warning",
                passed=True,
                expected_status="",
                expectation_passed=True,
                diagnostic_issue_codes=[],
                metrics={
                    "repository_test_environment_setup_status": "pass",
                    "repository_test_environment_setup_supported": True,
                    "repository_test_environment_setup_result_status": "pass",
                    "repository_test_environment_setup_result_executed": True,
                    "repository_test_environment_setup_install_failure_category": (
                        "editable_backend_unsupported"
                    ),
                    "repository_test_environment_setup_install_failure_signal": (
                        "backend does not support editable installs"
                    ),
                    "repository_test_environment_setup_install_fallback_executed": (
                        True
                    ),
                    "repository_test_environment_setup_install_fallback_returncode": 0,
                },
                metric_checks=[],
                command_args=[],
            ),
            GitHubRepoAgentSuiteRunResult(
                name="missing_requirements",
                repo="example/missing-requirements",
                output_dir="out/missing",
                report_path="out/missing/github_repo_agent.json",
                status="warning",
                passed=True,
                expected_status="",
                expectation_passed=True,
                diagnostic_issue_codes=[],
                metrics={
                    "repository_test_environment_setup_status": "pass",
                    "repository_test_environment_setup_supported": True,
                    "repository_test_environment_setup_result_status": "fail",
                    "repository_test_environment_setup_result_executed": True,
                    "repository_test_environment_setup_install_failure_category": (
                        "missing_requirement_file"
                    ),
                    "repository_test_environment_setup_install_failure_signal": (
                        "Could not open requirements file"
                    ),
                    "repository_test_environment_setup_install_fallback_executed": (
                        False
                    ),
                    "repository_test_environment_setup_install_fallback_returncode": None,
                },
                metric_checks=[],
                command_args=[],
            ),
        ],
        suite_thresholds={},
    )
    markdown = render_github_repo_agent_suite_markdown(
        type(
            "Report",
            (),
            {
                "suite_name": "setup_diagnostics_suite",
                "manifest_path": "manifest.json",
                "output_dir": "out",
                "passed": True,
                "summary": summary,
                "runs": [],
            },
        )()
    )

    assert summary["repository_test_environment_setup_executed_count"] == 2
    assert summary["repository_test_environment_setup_passed_count"] == 1
    assert summary["repository_test_environment_setup_install_fallback_count"] == 1
    assert (
        summary["repository_test_environment_setup_install_fallback_success_count"]
        == 1
    )
    assert summary["repository_test_environment_setup_install_failure_counts"] == {
        "editable_backend_unsupported": 1,
        "missing_requirement_file": 1,
    }
    assert summary["repository_test_environment_setup_install_failure_runs"] == {
        "editable_backend_unsupported": ["editable_fallback"],
        "missing_requirement_file": ["missing_requirements"],
    }
    assert "Repository Test Environment Install Fallbacks" in markdown
    assert "missing_requirement_file" in markdown


def test_repo_agent_suite_summarizes_auto_fallback_recovery():
    summary = _suite_summary(
        [
            GitHubRepoAgentSuiteRunResult(
                name="fallback_recovered",
                repo="example/project",
                output_dir="out/project",
                report_path="out/project/github_repo_agent.json",
                status="pass",
                passed=True,
                expected_status="pass",
                expectation_passed=True,
                diagnostic_issue_codes=[],
                metrics={
                    "generated_candidates": 2,
                    "benchmark_cases": 0,
                    "fallback_attempted": True,
                    "fallback_used": True,
                    "fallback_improved": True,
                    "fallback_recovered": True,
                    "fallback_reason": "no_generated_candidates",
                    "primary_generated_candidates": 0,
                    "fallback_generated_candidates": 2,
                    "primary_benchmark_cases": 0,
                    "fallback_benchmark_cases": 0,
                    "benchmarkization_status": "ready",
                    "benchmarkization_stage": "validated",
                    "benchmarkization_primary_action_id": "run_template_benchmark",
                    "benchmarkization_remediation_plan_markdown": (
                        "out/project/fallback/benchmarkization_remediation_plan.md"
                    ),
                    "primary_benchmarkization_status": "blocked",
                    "fallback_benchmarkization_status": "ready",
                    "primary_benchmarkization_primary_action_id": "collect_tests",
                    "fallback_benchmarkization_primary_action_id": (
                        "run_template_benchmark"
                    ),
                    "primary_benchmarkization_remediation_plan_markdown": (
                        "out/project/primary/benchmarkization_remediation_plan.md"
                    ),
                    "fallback_benchmarkization_remediation_plan_markdown": (
                        "out/project/fallback/benchmarkization_remediation_plan.md"
                    ),
                },
                metric_checks=[],
                command_args=[],
            ),
            GitHubRepoAgentSuiteRunResult(
                name="no_fallback",
                repo="example/other",
                output_dir="out/other",
                report_path="out/other/github_repo_agent.json",
                status="pass",
                passed=True,
                expected_status="pass",
                expectation_passed=True,
                diagnostic_issue_codes=[],
                metrics={
                    "generated_candidates": 3,
                    "benchmark_cases": 3,
                    "top1": 1.0,
                    "patch_success_rate": 1.0,
                },
                metric_checks=[],
                command_args=[],
            ),
        ],
        suite_thresholds={"min_fallback_recovered_count": 1},
    )
    markdown = render_github_repo_agent_suite_markdown(
        type(
            "Report",
            (),
            {
                "suite_name": "fallback_suite",
                "manifest_path": "manifest.json",
                "output_dir": "out",
                "passed": True,
                "summary": summary,
                "runs": [],
            },
        )()
    )

    assert summary["fallback_attempted_count"] == 1
    assert summary["fallback_used_count"] == 1
    assert summary["fallback_improved_count"] == 1
    assert summary["fallback_recovered_count"] == 1
    assert summary["fallback_candidate_delta"] == 2
    assert summary["fallback_benchmark_case_delta"] == 0
    assert summary["fallback_reason_counts"] == {"no_generated_candidates": 1}
    assert summary["fallback_reason_runs"] == {
        "no_generated_candidates": ["fallback_recovered"]
    }
    assert summary["benchmarkization_status_counts"] == {"ready": 1}
    assert summary["benchmarkization_stage_counts"] == {"validated": 1}
    assert summary["benchmarkization_primary_action_counts"] == {
        "run_template_benchmark": 1
    }
    assert summary["benchmarkization_remediation_plan_count"] == 1
    assert summary["primary_benchmarkization_status_counts"] == {"blocked": 1}
    assert summary["fallback_benchmarkization_status_counts"] == {"ready": 1}
    assert summary["benchmarkization_fallback_transition_counts"] == {
        "blocked->ready": 1
    }
    assert summary["primary_benchmarkization_action_counts"] == {
        "collect_tests": 1
    }
    assert summary["fallback_benchmarkization_action_counts"] == {
        "run_template_benchmark": 1
    }
    assert summary["primary_benchmarkization_remediation_plan_count"] == 1
    assert summary["fallback_benchmarkization_remediation_plan_count"] == 1
    assert summary["benchmarkization_fallback_audit_runs"] == [
        {
            "run": "fallback_recovered",
            "primary_status": "blocked",
            "fallback_status": "ready",
            "transition": "blocked->ready",
            "primary_action": "collect_tests",
            "fallback_action": "run_template_benchmark",
            "primary_remediation_plan_markdown": (
                "out/project/primary/benchmarkization_remediation_plan.md"
            ),
            "fallback_remediation_plan_markdown": (
                "out/project/fallback/benchmarkization_remediation_plan.md"
            ),
        }
    ]
    assert summary["suite_threshold_failed_count"] == 0
    assert "Auto Fallback Recovered Runs: 1" in markdown
    assert "Auto Fallback Candidate Delta: 2" in markdown
    assert "Benchmarkization Fallback Audit" in markdown
    assert "blocked->ready:1" in markdown
    assert "collect_tests:1" in markdown
    assert "no_generated_candidates:1" in markdown


def test_repo_agent_suite_summarizes_auto_remediation_recovery():
    summary = _suite_summary(
        [
            GitHubRepoAgentSuiteRunResult(
                name="benchmark_remediated",
                repo="example/project",
                output_dir="out/project",
                report_path="out/project/github_repo_agent.json",
                status="pass",
                passed=True,
                expected_status="pass",
                expectation_passed=True,
                diagnostic_issue_codes=[],
                metrics={
                    "generated_candidates": 1,
                    "benchmark_cases": 1,
                    "auto_remediation_attempted": True,
                    "auto_remediation_used": True,
                    "auto_remediation_improved": True,
                    "auto_remediation_action_id": "run_template_benchmark",
                    "primary_benchmark_cases": 0,
                    "remediated_benchmark_cases": 1,
                    "benchmarkization_status": "ready",
                    "benchmarkization_stage": "validated",
                    "benchmarkization_primary_action_id": "run_template_benchmark",
                    "benchmarkization_remediation_plan_markdown": (
                        "out/project/benchmarkization_remediation_plan.md"
                    ),
                    "primary_benchmarkization_status": "blocked",
                    "remediated_benchmarkization_status": "ready",
                },
                metric_checks=[],
                command_args=[],
            )
        ],
        suite_thresholds={},
    )
    markdown = render_github_repo_agent_suite_markdown(
        type(
            "Report",
            (),
            {
                "suite_name": "remediation_suite",
                "manifest_path": "manifest.json",
                "output_dir": "out",
                "passed": True,
                "summary": summary,
                "runs": [],
            },
        )()
    )

    assert summary["auto_remediation_attempted_count"] == 1
    assert summary["auto_remediation_used_count"] == 1
    assert summary["auto_remediation_improved_count"] == 1
    assert summary["auto_remediation_benchmark_case_delta"] == 1
    assert summary["auto_remediation_action_counts"] == {
        "run_template_benchmark": 1
    }
    assert summary["auto_remediation_action_runs"] == {
        "run_template_benchmark": ["benchmark_remediated"]
    }
    assert summary["benchmarkization_status_counts"] == {"ready": 1}
    assert summary["benchmarkization_stage_counts"] == {"validated": 1}
    assert summary["benchmarkization_primary_action_counts"] == {
        "run_template_benchmark": 1
    }
    assert summary["benchmarkization_remediation_plan_count"] == 1
    assert summary["primary_benchmarkization_status_counts"] == {"blocked": 1}
    assert summary["remediated_benchmarkization_status_counts"] == {"ready": 1}
    assert summary["benchmarkization_remediation_transition_counts"] == {
        "blocked->ready": 1
    }
    assert summary["benchmarkization_fallback_audit_runs"] == []
    assert "Auto Remediation Attempts: 1" in markdown
    assert "Auto Remediation Benchmark Case Delta: 1" in markdown
    assert "Benchmarkization Statuses: ready:1" in markdown
    assert "Benchmarkization Remediation Transitions: blocked->ready:1" in markdown
    assert "Benchmarkization Remediation Plans" in markdown
    assert "run_template_benchmark:1" in markdown


def test_repo_agent_suite_summarizes_failure_overlay_rates():
    summary = _suite_summary(
        [
            GitHubRepoAgentSuiteRunResult(
                name="overlay_success",
                repo="example/project",
                output_dir="out/project",
                report_path="out/project/github_repo_agent.json",
                status="pass",
                passed=True,
                expected_status="",
                expectation_passed=True,
                diagnostic_issue_codes=[],
                metrics={
                    "repository_test_failure_overlay_status": "pass",
                    "repository_test_failure_overlay_reason": (
                        "overlay_dynamic_evidence_generated"
                    ),
                    "repository_test_failure_overlay_attempted_cases": 2,
                    "repository_test_failure_overlay_supported_candidates": 4,
                    "repository_test_failure_overlay_selected_rule": (
                        "possible_index_overrun"
                    ),
                    "repository_test_failure_overlay_selected_score": 0.95,
                    "repository_test_failure_overlay_candidate_rule_counts": {
                        "possible_index_overrun": 2,
                        "dict_missing_key_guard": 2,
                    },
                    "repository_test_failure_overlay_attempted_rule_counts": {
                        "possible_index_overrun": 1,
                        "dict_missing_key_guard": 1,
                    },
                    "repository_test_failure_overlay_triggered_rule_counts": {
                        "possible_index_overrun": 1,
                    },
                    "repository_test_failure_overlay_candidate_rejection_count": 1,
                    "repository_test_failure_overlay_candidate_rejection_counts": {
                        "callable_context_unsupported": 1,
                    },
                    "repository_test_failure_overlay_candidate_rejection_rule_counts": {
                        "possible_index_overrun": 1,
                    },
                    "repository_test_failure_overlay_dominant_rejection_reason": (
                        "callable_context_unsupported"
                    ),
                    "repository_test_failure_overlay_dominant_rejection_count": 1,
                    "repository_test_failure_overlay_next_extension": {
                        "reason": "callable_context_unsupported",
                        "count": 1,
                        "recommended_extension": "inspect callable context",
                    },
                    "repository_test_failure_overlay_next_actionable_extension": {
                        "reason": "callable_context_unsupported",
                        "count": 1,
                        "recommended_extension": "inspect callable context",
                        "actionable": True,
                    },
                },
                metric_checks=[],
                command_args=[],
            ),
            GitHubRepoAgentSuiteRunResult(
                name="overlay_warning",
                repo="example/other",
                output_dir="out/other",
                report_path="out/other/github_repo_agent.json",
                status="warning",
                passed=True,
                expected_status="",
                expectation_passed=True,
                diagnostic_issue_codes=[],
                metrics={
                    "repository_test_failure_overlay_status": "warning",
                    "repository_test_failure_overlay_reason": (
                        "overlay_tests_did_not_trigger_expected_failure"
                    ),
                    "repository_test_failure_overlay_attempted_cases": 1,
                    "repository_test_failure_overlay_supported_candidates": 2,
                    "repository_test_failure_overlay_selected_score": 0.85,
                    "repository_test_failure_overlay_candidate_rule_counts": {
                        "mutable_default_arg": 2,
                    },
                    "repository_test_failure_overlay_attempted_rule_counts": {
                        "mutable_default_arg": 1,
                    },
                    "repository_test_failure_overlay_triggered_rule_counts": {},
                    "repository_test_failure_overlay_candidate_rejection_count": 2,
                    "repository_test_failure_overlay_candidate_rejection_counts": {
                        "mutable_default_shape_unsupported": 2,
                    },
                    "repository_test_failure_overlay_candidate_rejection_rule_counts": {
                        "mutable_default_arg": 2,
                    },
                    "repository_test_failure_overlay_dominant_rejection_reason": (
                        "mutable_default_shape_unsupported"
                    ),
                    "repository_test_failure_overlay_dominant_rejection_count": 2,
                    "repository_test_failure_overlay_next_extension": {
                        "reason": "mutable_default_shape_unsupported",
                        "count": 2,
                        "recommended_extension": "extend mutable default oracle",
                    },
                    "repository_test_failure_overlay_next_actionable_extension": {
                        "reason": "mutable_default_shape_unsupported",
                        "count": 2,
                        "recommended_extension": "extend mutable default oracle",
                        "actionable": True,
                    },
                },
                metric_checks=[],
                command_args=[],
            ),
        ],
        suite_thresholds={},
    )
    markdown = render_github_repo_agent_suite_markdown(
        type(
            "Report",
            (),
            {
                "suite_name": "overlay_suite",
                "manifest_path": "manifest.json",
                "output_dir": "out",
                "passed": True,
                "summary": summary,
                "runs": [],
            },
        )()
    )

    assert summary["repository_test_failure_overlay_count"] == 2
    assert summary["repository_test_failure_overlay_success_count"] == 1
    assert summary["repository_test_failure_overlay_success_rate"] == 0.5
    assert summary["repository_test_failure_overlay_attempted_case_count"] == 3
    assert summary["repository_test_failure_overlay_supported_candidate_count"] == 6
    assert summary["repository_test_failure_overlay_candidate_attempt_rate"] == 0.5
    assert summary[
        "repository_test_failure_overlay_attempt_trigger_rate"
    ] == pytest.approx(1 / 3, abs=1e-4)
    assert summary[
        "repository_test_failure_overlay_average_selected_score"
    ] == pytest.approx(0.9, abs=1e-4)
    assert summary["repository_test_failure_overlay_candidate_rule_count"] == 3
    assert summary["repository_test_failure_overlay_attempted_rule_count"] == 3
    assert summary["repository_test_failure_overlay_triggered_rule_count"] == 1
    assert (
        summary["repository_test_failure_overlay_attempted_rule_coverage_rate"]
        == 1.0
    )
    assert summary[
        "repository_test_failure_overlay_triggered_rule_coverage_rate"
    ] == pytest.approx(1 / 3, abs=1e-4)
    assert summary["repository_test_failure_overlay_attempted_rule_counts"] == {
        "dict_missing_key_guard": 1,
        "mutable_default_arg": 1,
        "possible_index_overrun": 1,
    }
    assert summary["repository_test_failure_overlay_triggered_rule_counts"] == {
        "possible_index_overrun": 1,
    }
    assert summary[
        "repository_test_failure_overlay_candidate_rejection_counts"
    ] == {
        "callable_context_unsupported": 1,
        "mutable_default_shape_unsupported": 2,
    }
    assert summary[
        "repository_test_failure_overlay_candidate_rejection_rule_counts"
    ] == {
        "mutable_default_arg": 2,
        "possible_index_overrun": 1,
    }
    assert summary[
        "repository_test_failure_overlay_dominant_rejection_counts"
    ] == {
        "callable_context_unsupported": 1,
        "mutable_default_shape_unsupported": 2,
    }
    assert summary[
        "repository_test_failure_overlay_dominant_rejection_runs"
    ] == {
        "callable_context_unsupported": ["overlay_success"],
        "mutable_default_shape_unsupported": ["overlay_warning"],
    }
    assert summary[
        "repository_test_failure_overlay_actionable_extension_counts"
    ] == {
        "callable_context_unsupported": 1,
        "mutable_default_shape_unsupported": 2,
    }
    assert summary[
        "repository_test_failure_overlay_actionable_extension_runs"
    ] == {
        "callable_context_unsupported": ["overlay_success"],
        "mutable_default_shape_unsupported": ["overlay_warning"],
    }
    assert "Repository Test Failure Overlay Success Rate: 0.5000" in markdown
    assert "Repository Test Failure Overlay Average Selected Score: 0.9000" in markdown
    assert "Repository Test Failure Overlay Candidate Rejection Counts" in markdown
    assert "Repository Test Failure Overlay Dominant Rejection Counts" in markdown
    assert "mutable_default_shape_unsupported:2" in markdown
    assert "Repository Test Failure Overlay Triggered Rule Coverage Rate: 0.3333" in markdown


def test_repo_agent_suite_summarizes_repair_validation_scopes():
    summary = _suite_summary(
        [
            GitHubRepoAgentSuiteRunResult(
                name="regression_pass",
                repo="example/pass",
                output_dir="out/pass",
                report_path="out/pass/github_repo_agent.json",
                status="pass",
                passed=True,
                expected_status="",
                expectation_passed=True,
                diagnostic_issue_codes=[],
                metrics={
                    "repository_test_patch_validation_status": "pass",
                    "repository_test_patch_validation_reason": (
                        "patch_validation_success"
                    ),
                    "repository_test_patch_validation_success_count": 1,
                    "repository_test_repair_ready": True,
                    "repository_test_repair_validation_scope": (
                        "narrow_and_regression"
                    ),
                    "repository_test_regression_ready": True,
                    "repository_test_regression_validation_status": "pass",
                },
                metric_checks=[],
                command_args=[],
            ),
            GitHubRepoAgentSuiteRunResult(
                name="regression_fail",
                repo="example/fail",
                output_dir="out/fail",
                report_path="out/fail/github_repo_agent.json",
                status="pass",
                passed=True,
                expected_status="",
                expectation_passed=True,
                diagnostic_issue_codes=[],
                metrics={
                    "repository_test_patch_validation_status": "pass",
                    "repository_test_patch_validation_reason": (
                        "patch_validation_success"
                    ),
                    "repository_test_patch_validation_success_count": 1,
                    "repository_test_repair_ready": False,
                    "repository_test_repair_validation_scope": "regression_failed",
                    "repository_test_regression_ready": False,
                    "repository_test_regression_validation_status": "fail",
                },
                metric_checks=[],
                command_args=[],
            ),
        ],
        suite_thresholds={},
    )
    markdown = render_github_repo_agent_suite_markdown(
        type(
            "Report",
            (),
            {
                "suite_name": "repair_scope_suite",
                "manifest_path": "manifest.json",
                "output_dir": "out",
                "passed": True,
                "summary": summary,
                "runs": [],
            },
        )()
    )

    assert summary["repository_test_patch_validation_success_count"] == 2
    assert summary["repository_test_repair_ready_count"] == 1
    assert summary["repository_test_regression_ready_count"] == 1
    assert summary["repository_test_repair_validation_scope_counts"] == {
        "narrow_and_regression": 1,
        "regression_failed": 1,
    }
    assert summary["repository_test_regression_validation_status_counts"] == {
        "fail": 1,
        "pass": 1,
    }
    assert "Repository Test Repair-Ready Runs: 1" in markdown
    assert "narrow_and_regression:1" in markdown
    assert "regression_failed:1" in markdown


def test_repo_agent_suite_reports_mixed_expected_results():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        average_source = _write_average_mean(root)
        manifest = root / "repo_agent_suite.json"
        output_dir = root / "suite_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "mixed_repo_agent_suite",
                    "suite_thresholds": {
                        "min_expectation_pass_rate": 1.0,
                        "min_generated_candidates": 3,
                        "min_benchmark_cases": 3,
                        "min_average_top1": 1.0,
                        "min_average_patch_success_rate": 1.0,
                        "min_weighted_patch_success_rate": 1.0,
                        "max_command_failed_count": 0,
                        "max_expectation_failed_count": 0,
                        "max_metric_check_failed_count": 0,
                        "max_repository_test_blocked_count": 2,
                    },
                    "defaults": {"preset": "smoke"},
                    "runs": [
                        {
                            "name": "average_pass",
                            "repo": "example/project",
                            "repository_test_root": str(root),
                            "repository_test_timeout": 7,
                            "repository_test_failure_overlay_candidate_limit": 3,
                            "dependency_max_depth": 2,
                            "run_repository_test_environment_setup": True,
                            "repository_test_environment_setup_timeout": 11,
                            "checkout_repository_tests": True,
                            "repository_checkout_timeout": 9,
                            "repository_checkout_depth": 2,
                            "thresholds": {
                                "generated_candidates": 3,
                                "benchmark_cases": 3,
                                "top1": 1.0,
                                "patch_success_rate": 1.0,
                            },
                        },
                        {
                            "name": "docs_expected_fail",
                            "repo": "example/docs",
                            "preset": "mining",
                            "expected_status": "fail",
                            "expected_diagnostic_codes": ["no_imported_sources"],
                            "thresholds": {"generated_candidates": 0},
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        opener = _FakeOpener(
            [
                *_repo_payloads_for_source(
                    average_source,
                    owner="example",
                    repo="project",
                    path="maths/average_mean.py",
                ),
                *_repo_payloads_no_python(owner="example", repo="docs"),
            ]
        )

        report = run_github_repo_agent_suite(manifest, output_dir, opener=opener)
        markdown = render_github_repo_agent_suite_markdown(report)
        saved = json.loads(
            (output_dir / "github_repo_agent_suite.json").read_text(
                encoding="utf-8"
            )
        )

        assert report.passed is True
        assert report.summary["run_count"] == 2
        assert report.summary["completed_count"] == 2
        assert report.summary["command_failed_count"] == 0
        assert report.summary["agent_passed_count"] == 1
        assert report.summary["agent_failed_count"] == 1
        assert report.summary["expectation_passed_count"] == 2
        assert report.summary["expectation_failed_count"] == 0
        assert report.summary["expectation_pass_rate"] == 1.0
        assert report.summary["metric_check_count"] == 5
        assert report.summary["metric_check_failed_count"] == 0
        assert report.summary["suite_threshold_check_count"] == 10
        assert report.summary["suite_threshold_failed_count"] == 0
        assert report.summary["generated_candidates"] == 3
        assert report.summary["benchmark_cases"] == 3
        assert report.summary["benchmark_run_count"] == 1
        assert report.summary["repository_test_plan_count"] == 2
        assert report.summary["repository_test_environment_setup_executed_count"] == 0
        assert report.summary["repository_test_environment_setup_passed_count"] == 0
        assert report.summary["repository_test_plan_executable_count"] == 0
        assert report.summary["repository_test_plan_narrow_count"] == 0
        assert report.summary["planned_repository_test_executed_count"] == 0
        assert report.summary["planned_repository_test_passed_count"] == 0
        assert report.summary["planned_repository_test_venv_python_count"] == 0
        assert report.summary["planned_repository_test_failure_category_counts"] == {}
        assert report.summary["repository_test_retry_recommended_count"] == 0
        assert report.summary["repository_test_retry_strategy_counts"] == {}
        assert report.summary["repository_test_retry_executed_count"] == 0
        assert report.summary["repository_test_retry_passed_count"] == 0
        assert report.summary["repository_test_dynamic_evidence_count"] == 0
        assert report.summary["repository_test_phase2_ready_count"] == 0
        assert report.summary["repository_test_phase2_ready_runs"] == []
        assert report.summary["repository_test_phase3_validation_ready_count"] == 0
        assert report.summary["repository_test_phase3_validation_ready_runs"] == []
        assert report.summary["repository_test_final_status_counts"] == {
            "blocked": 2
        }
        assert report.summary["repository_test_repaired_count"] == 0
        assert report.summary["repository_test_blocked_count"] == 2
        assert report.summary["repository_test_final_status_runs"] == {
            "blocked": ["average_pass", "docs_expected_fail"]
        }
        assert report.summary["repository_test_blocked_reason_counts"] == {
            "repository_test_not_executed": 2
        }
        assert report.summary["repository_test_blocked_reason_runs"] == {
            "repository_test_not_executed": [
                "average_pass",
                "docs_expected_fail",
            ]
        }
        assert (
            report.summary["repository_test_dynamic_localization_ready_count"]
            == 0
        )
        assert (
            report.summary[
                "repository_test_dynamic_patch_validation_ready_count"
            ]
            == 0
        )
        assert report.summary["repository_test_dynamic_evidence_level_counts"] == {}
        assert report.summary["repository_test_fault_localization_count"] == 2
        assert report.summary["repository_test_fault_localization_passed_count"] == 0
        assert report.summary["repository_test_fault_localization_reason_counts"] == {
            "dynamic_evidence_not_usable": 2
        }
        assert report.summary["repository_test_patch_candidate_run_count"] == 2
        assert report.summary["repository_test_patch_candidate_count"] == 0
        assert report.summary["repository_test_patch_candidate_passed_run_count"] == 0
        assert report.summary["repository_test_patch_candidate_reason_counts"] == {
            "fault_localization_not_ready": 2
        }
        assert report.summary["repository_test_patch_validation_run_count"] == 2
        assert report.summary["repository_test_patch_validation_success_run_count"] == 0
        assert report.summary["repository_test_patch_validation_success_count"] == 0
        assert (
            report.summary[
                "repository_test_patch_validation_reflection_candidate_count"
            ]
            == 0
        )
        assert (
            report.summary[
                "repository_test_patch_validation_successful_reflection_count"
            ]
            == 0
        )
        assert report.summary["repository_test_patch_validation_max_depth"] == 0
        assert report.summary["repository_test_patch_validation_reason_counts"] == {
            "patch_candidates_not_ready": 2
        }
        assert report.summary["average_top1"] == 1.0
        assert report.summary["average_map"] == 1.0
        assert report.summary["average_patch_success_rate"] == 1.0
        assert report.summary["weighted_top1"] == 1.0
        assert report.summary["weighted_patch_success_rate"] == 1.0
        assert report.summary["diagnostic_issue_code_counts"][
            "no_imported_sources"
        ] == 1
        assert report.summary["diagnostic_issue_code_runs"][
            "no_imported_sources"
        ] == ["docs_expected_fail"]
        assert any(
            "no_imported_sources" in action
            for action in report.summary["next_actions"]
        )
        assert [run.name for run in report.runs] == [
            "average_pass",
            "docs_expected_fail",
        ]
        assert report.runs[0].status == "pass"
        assert report.runs[0].metrics["generated_candidates"] == 3
        assert report.runs[0].metrics["benchmark_cases"] == 3
        assert report.runs[0].metrics["top1"] == 1.0
        assert report.runs[0].metrics["patch_success_rate"] == 1.0
        assert report.runs[0].metrics["repository_test_execution_plan_status"] == (
            "skipped"
        )
        assert report.runs[0].metrics["planned_repository_test_level"] == ""
        assert report.runs[0].metrics[
            "planned_repository_test_executable_now"
        ] is False
        assert report.runs[0].metrics[
            "planned_repository_test_result_status"
        ] == "skipped"
        assert report.runs[0].metrics[
            "planned_repository_test_result_executed"
        ] is False
        assert (
            report.runs[0].metrics["planned_repository_test_python_source"]
            == "current_interpreter"
        )
        assert (
            report.runs[0].metrics["planned_repository_test_failure_category"]
            == "not_executed"
        )
        assert report.runs[0].metrics["repository_test_retry_recommended"] is False
        assert (
            report.runs[0].metrics["repository_test_retry_strategy"]
            == "satisfy_execution_prerequisites"
        )
        assert (
            report.runs[0].metrics["repository_test_retry_execution_status"]
            == "skipped"
        )
        assert report.runs[0].metrics["repository_test_retry_executed"] is False
        assert (
            report.runs[0].metrics["repository_test_dynamic_evidence_level"]
            == "not_executed"
        )
        assert report.runs[0].metrics[
            "repository_test_dynamic_usable_for_localization"
        ] is False
        assert report.runs[0].metrics[
            "repository_test_dynamic_usable_for_patch_validation"
        ] is False
        assert report.runs[0].metrics[
            "repository_test_dynamic_usable_for_regression_validation"
        ] is False
        assert (
            report.summary[
                "repository_test_dynamic_regression_validation_ready_count"
            ]
            == 0
        )
        assert (
            report.runs[0].metrics["repository_test_fault_localization_status"]
            == "skipped"
        )
        assert (
            report.runs[0].metrics["repository_test_fault_localization_reason"]
            == "dynamic_evidence_not_usable"
        )
        assert (
            report.runs[0].metrics["repository_test_patch_candidates_status"]
            == "skipped"
        )
        assert (
            report.runs[0].metrics["repository_test_patch_candidates_reason"]
            == "fault_localization_not_ready"
        )
        assert report.runs[0].metrics["repository_test_patch_candidate_count"] == 0
        assert (
            report.runs[0].metrics[
                "repository_test_failure_overlay_candidate_limit"
            ]
            == 3
        )
        assert (
            report.runs[0].metrics["repository_test_patch_validation_status"]
            == "skipped"
        )
        assert (
            report.runs[0].metrics["repository_test_patch_validation_reason"]
            == "patch_candidates_not_ready"
        )
        assert (
            report.runs[0].metrics["repository_test_patch_validation_success_count"]
            == 0
        )
        assert report.runs[0].metrics["repository_test_final_status"] == "blocked"
        assert report.runs[0].metrics["repository_test_final_reason"] == (
            "repository_test_not_executed"
        )
        assert (
            report.runs[0].metrics[
                "repository_test_patch_validation_successful_reflection_count"
            ]
            == 0
        )
        assert report.runs[0].metrics[
            "repository_test_environment_setup_result_status"
        ] == "skipped"
        assert report.runs[0].metrics[
            "repository_test_environment_setup_result_executed"
        ] is False
        assert all(check["passed"] for check in report.runs[0].metric_checks)
        assert (
            "--run-repository-test-environment-setup"
            in report.runs[0].command_args
        )
        assert (
            "--repository-test-environment-setup-timeout"
            in report.runs[0].command_args
        )
        assert "11" in report.runs[0].command_args
        assert "--checkout-repository-tests" in report.runs[0].command_args
        assert "--repository-test-root" in report.runs[0].command_args
        assert str(root) in report.runs[0].command_args
        assert "--repository-test-timeout" in report.runs[0].command_args
        assert "7" in report.runs[0].command_args
        assert "--dependency-max-depth" in report.runs[0].command_args
        assert "2" in report.runs[0].command_args
        assert (
            "--repository-test-failure-overlay-candidate-limit"
            in report.runs[0].command_args
        )
        assert "3" in report.runs[0].command_args
        assert "--repository-checkout-timeout" in report.runs[0].command_args
        assert "9" in report.runs[0].command_args
        assert "--repository-checkout-depth" in report.runs[0].command_args
        assert "2" in report.runs[0].command_args
        assert report.runs[1].status == "fail"
        assert report.runs[1].expectation_passed is True
        assert report.runs[1].metrics["generated_candidates"] == 0
        assert "no_imported_sources" in report.runs[1].diagnostic_issue_codes
        assert "mixed_repo_agent_suite" in markdown
        assert "docs_expected_fail" in markdown
        assert "Average Top-1" in markdown
        assert "Patch Success" in markdown
        assert "Repository Test Plans" in markdown
        assert "Executed Repository Test Environment Setups" in markdown
        assert "Executed Planned Repository Tests" in markdown
        assert "Venv Planned Repository Tests" in markdown
        assert "Planned Repository Test Failure Categories" in markdown
        assert "Repository Test Retry Recommendations" in markdown
        assert "Repository Test Retry Strategies" in markdown
        assert "Executed Repository Test Retries" in markdown
        assert "Passed Repository Test Retries" in markdown
        assert "Repository Test Dynamic Evidence Runs" in markdown
        assert "Repository Test Localization-Ready Runs" in markdown
        assert "Repository Test Patch-Validation-Ready Runs" in markdown
        assert "Repository Test Phase 2 Ready Runs" in markdown
        assert "Repository Test Phase 3 Validation-Ready Runs" in markdown
        assert "Repository Test Final Statuses" in markdown
        assert "Repository Test Repaired Runs" in markdown
        assert "Repository Test Blocked Runs" in markdown
        assert "Repository Test Blocked Reasons" in markdown
        assert "Repository Test Final Status Runs" in markdown
        assert "Repository Test Blocked Reason Runs" in markdown
        assert "Repository Test Fault Localization Runs" in markdown
        assert "Repository Test Fault Localization Reasons" in markdown
        assert "Repository Test Patch Candidate Runs" in markdown
        assert "Repository Test Patch Candidate Reasons" in markdown
        assert "Repository Test Patch Validation Runs" in markdown
        assert "Repository Test Patch Validation Reflection Successes" in markdown
        assert "Repository Test Patch Validation Reasons" in markdown
        assert "Diagnostic Code Runs" in markdown
        assert "Next Actions" in markdown
        assert saved["passed"] is True
        assert saved["summary"]["average_top1"] == 1.0
        assert saved["summary"]["repository_test_plan_count"] == 2
        assert saved["summary"][
            "repository_test_environment_setup_executed_count"
        ] == 0
        assert saved["summary"]["planned_repository_test_executed_count"] == 0
        assert saved["summary"]["repository_test_dynamic_evidence_count"] == 0
        assert saved["summary"]["repository_test_phase2_ready_count"] == 0
        assert (
            saved["summary"]["repository_test_phase3_validation_ready_count"]
            == 0
        )
        assert saved["summary"]["repository_test_final_status_counts"] == {
            "blocked": 2
        }
        assert saved["summary"]["repository_test_blocked_reason_counts"] == {
            "repository_test_not_executed": 2
        }
        assert saved["summary"]["repository_test_fault_localization_count"] == 2
        assert saved["summary"]["repository_test_patch_candidate_run_count"] == 2
        assert saved["summary"]["repository_test_patch_validation_run_count"] == 2
        assert (
            saved["summary"][
                "repository_test_patch_validation_successful_reflection_count"
            ]
            == 0
        )
        assert saved["summary"]["diagnostic_issue_code_runs"][
            "no_imported_sources"
        ] == ["docs_expected_fail"]
        assert saved["summary"]["suite_threshold_checks"][0]["passed"] is True
        assert saved["runs"][0]["metrics"]["top1"] == 1.0
        assert saved["runs"][0]["metrics"]["repository_test_final_status"] == (
            "blocked"
        )
        assert saved["runs"][0]["metric_checks"][0]["passed"] is True
        assert (output_dir / "github_repo_agent_suite.md").exists()
        assert Path(report.runs[0].report_path).exists()
        assert Path(report.runs[1].report_path).exists()


def test_repo_agent_suite_runs_single_repo_auto_fallback():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        inplace_source = _write_inplace_sort(root)
        manifest = root / "repo_agent_suite.json"
        output_dir = root / "suite_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "fallback_repo_agent_suite",
                    "suite_thresholds": {
                        "min_fallback_recovered_count": 1,
                        "min_fallback_candidate_delta": 1,
                    },
                    "runs": [
                        {
                            "name": "fallback_project",
                            "repo": "example/project",
                            "preset": "mining",
                            "recipe": ["missing_len_zero_guard"],
                            "fallback_max_sources": 25,
                            "fallback_max_candidates": 15,
                            "thresholds": {"generated_candidates": 1},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        payloads = _repo_payloads_for_source(
            inplace_source,
            owner="example",
            repo="project",
            path="maths/sort_helpers.py",
        )
        opener = _FakeOpener([*payloads, *payloads])

        report = run_github_repo_agent_suite(manifest, output_dir, opener=opener)
        markdown = render_github_repo_agent_suite_markdown(report)
        saved = json.loads(
            (output_dir / "github_repo_agent_suite.json").read_text(
                encoding="utf-8"
            )
        )

        assert report.passed is True
        assert report.summary["fallback_attempted_count"] == 1
        assert report.summary["fallback_used_count"] == 1
        assert report.summary["fallback_improved_count"] == 1
        assert report.summary["fallback_recovered_count"] == 1
        assert report.summary["fallback_candidate_delta"] >= 1
        assert report.summary["fallback_reason_counts"] == {
            "no_generated_candidates": 1
        }
        assert report.summary["suite_threshold_failed_count"] == 0
        assert report.runs[0].metrics["fallback_recovered"] is True
        assert report.runs[0].metrics["primary_generated_candidates"] == 0
        assert report.runs[0].metrics["fallback_generated_candidates"] >= 1
        assert "--fallback-max-sources" in report.runs[0].command_args
        assert "25" in report.runs[0].command_args
        assert "--fallback-max-candidates" in report.runs[0].command_args
        assert "15" in report.runs[0].command_args
        assert "recovered:no_generated_candidates" in markdown
        assert "Auto Fallback Recovered Runs: 1" in markdown
        assert saved["summary"]["fallback_recovered_count"] == 1
        assert Path(saved["runs"][0]["metrics"]["fallback_output_dir"]).name == (
            "fallback"
        )


def test_repo_agent_suite_runs_single_repo_auto_remediation():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        average_source = _write_average_mean(root)
        manifest = root / "repo_agent_suite.json"
        output_dir = root / "suite_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "remediation_repo_agent_suite",
                    "runs": [
                        {
                            "name": "remediate_project",
                            "repo": "example/project",
                            "preset": "mining",
                            "recipe": ["missing_len_zero_guard"],
                            "auto_remediate_benchmark": True,
                            "auto_fallback": False,
                            "thresholds": {"benchmark_cases": 1},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        payloads = _repo_payloads_for_source(
            average_source,
            owner="example",
            repo="project",
            path="maths/average_mean.py",
        )
        opener = _FakeOpener([*payloads, *payloads])

        report = run_github_repo_agent_suite(manifest, output_dir, opener=opener)
        markdown = render_github_repo_agent_suite_markdown(report)
        saved = json.loads(
            (output_dir / "github_repo_agent_suite.json").read_text(
                encoding="utf-8"
            )
        )

        assert report.passed is True
        assert report.summary["auto_remediation_attempted_count"] == 1
        assert report.summary["auto_remediation_used_count"] == 1
        assert report.summary["auto_remediation_improved_count"] == 1
        assert report.summary["auto_remediation_benchmark_case_delta"] == 1
        assert report.summary["auto_remediation_action_counts"] == {
            "run_template_benchmark": 1
        }
        assert report.runs[0].metrics["benchmark_cases"] == 1
        assert report.runs[0].metrics["auto_remediation_used"] is True
        assert report.runs[0].metrics["auto_remediation_action_id"] == (
            "run_template_benchmark"
        )
        assert "--auto-remediate-benchmark" in report.runs[0].command_args
        assert "used:run_template_benchmark" in markdown
        assert "Auto Remediation Attempts: 1" in markdown
        assert saved["summary"]["auto_remediation_used_count"] == 1
        assert saved["runs"][0]["metrics"]["remediated_benchmark_cases"] == 1


def test_repo_agent_suite_fails_when_metric_threshold_is_not_met():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        average_source = _write_average_mean(root)
        manifest = root / "repo_agent_suite.json"
        output_dir = root / "suite_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "strict_metric_suite",
                    "runs": [
                        {
                            "name": "average_too_strict",
                            "repo": "example/project",
                            "min_generated_candidates": 4,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        opener = _FakeOpener(
            _repo_payloads_for_source(
                average_source,
                owner="example",
                repo="project",
                path="maths/average_mean.py",
            )
        )

        report = run_github_repo_agent_suite(manifest, output_dir, opener=opener)
        markdown = render_github_repo_agent_suite_markdown(report)

        assert report.passed is False
        assert report.summary["expectation_failed_count"] == 1
        assert report.summary["metric_check_count"] == 1
        assert report.summary["metric_check_failed_count"] == 1
        assert report.summary["metric_check_failed_runs"] == ["average_too_strict"]
        assert report.runs[0].metric_checks == [
            {
                "metric": "generated_candidates",
                "expected": ">= 4.0000",
                "actual": "3.0000",
                "passed": False,
            }
        ]
        assert "generated_candidates expected >= 4.0000 actual 3.0000" in markdown


def test_repo_agent_suite_fails_when_suite_threshold_is_not_met():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        average_source = _write_average_mean(root)
        manifest = root / "repo_agent_suite.json"
        output_dir = root / "suite_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "strict_suite_threshold",
                    "suite_thresholds": {
                        "min_generated_candidates": 4,
                        "max_command_failed_count": 0,
                        "max_repository_test_blocked_count": 0,
                    },
                    "runs": [
                        {
                            "name": "average_pass",
                            "repo": "example/project",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        opener = _FakeOpener(
            _repo_payloads_for_source(
                average_source,
                owner="example",
                repo="project",
                path="maths/average_mean.py",
            )
        )

        report = run_github_repo_agent_suite(manifest, output_dir, opener=opener)
        markdown = render_github_repo_agent_suite_markdown(report)

        assert report.passed is False
        assert report.summary["expectation_failed_count"] == 0
        assert report.summary["suite_threshold_check_count"] == 3
        assert report.summary["suite_threshold_failed_count"] == 2
        failed = [
            check
            for check in report.summary["suite_threshold_checks"]
            if not check["passed"]
        ]
        assert failed == [
            {
                "name": "max_repository_test_blocked_count",
                "metric": "repository_test_blocked_count",
                "expected": "<= 0.0000",
                "actual": "1.0000",
                "passed": False,
            },
            {
                "name": "min_generated_candidates",
                "metric": "generated_candidates",
                "expected": ">= 4.0000",
                "actual": "3.0000",
                "passed": False,
            }
        ]
        assert "Failed Suite Thresholds" in markdown
        assert "aggregate suite output" in "\n".join(report.summary["next_actions"])


def test_repo_agent_suite_cli_writes_outputs():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        average_source = _write_average_mean(root)
        manifest = root / "repo_agent_suite.json"
        output_dir = root / "suite_output"
        output_json = root / "suite.json"
        output_markdown = root / "suite.md"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "cli_repo_agent_suite",
                    "runs": [
                        {
                            "name": "average_pass",
                            "repo": "example/project",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        opener = _FakeOpener(
            _repo_payloads_for_source(
                average_source,
                owner="example",
                repo="project",
                path="maths/average_mean.py",
            )
        )

        with pytest.raises(SystemExit) as exc_info:
            repo_agent_suite_main(
                [
                    str(manifest),
                    str(output_dir),
                    "--output-json",
                    str(output_json),
                    "--output-markdown",
                    str(output_markdown),
                    "--format",
                    "json",
                    "--require-success",
                ],
                opener=opener,
            )
        saved = json.loads(output_json.read_text(encoding="utf-8"))

        assert exc_info.value.code == 0
        assert saved["passed"] is True
        assert saved["summary"]["run_count"] == 1
        assert saved["summary"]["agent_passed_count"] == 1
        assert saved["summary"]["generated_candidates"] == 3
        assert saved["summary"]["average_top1"] == 1.0
        assert saved["runs"][0]["metrics"]["patch_success_rate"] == 1.0
        assert output_markdown.exists()
        assert (output_dir / "github_repo_agent_suite.json").exists()


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


def _repo_payloads_for_source(
    raw_source: Path,
    *,
    owner: str,
    repo: str,
    path: str,
) -> list[dict]:
    return [
        {"default_branch": "main"},
        {
            "sha": "abc123",
            "tree": [
                {
                    "path": path,
                    "type": "blob",
                    "raw_url": str(raw_source),
                    "sha256": hashlib.sha256(raw_source.read_bytes()).hexdigest(),
                    "license": "MIT",
                }
            ],
            "owner": owner,
            "repo": repo,
            "ref": "main",
        },
    ]


def _repo_payloads_no_python(*, owner: str, repo: str) -> list[dict]:
    return [
        {"default_branch": "main"},
        {
            "sha": "abc123",
            "tree": [
                {
                    "path": "README.md",
                    "type": "blob",
                    "raw_url": f"https://raw.githubusercontent.com/{owner}/{repo}/main/README.md",
                }
            ],
            "owner": owner,
            "repo": repo,
            "ref": "main",
        },
    ]


def _write_average_mean(root: Path) -> Path:
    raw_source = root / "average_mean.py"
    raw_source.write_text(
        "def mean(nums):\n"
        "    if not nums:\n"
        "        raise ValueError(\"List is empty\")\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
    return raw_source


def _write_inplace_sort(root: Path) -> Path:
    raw_source = root / "sort_helpers.py"
    raw_source.write_text(
        "def sorted_values(values):\n"
        "    values.sort()\n"
        "    return values\n",
        encoding="utf-8",
    )
    return raw_source
