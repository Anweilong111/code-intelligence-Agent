import hashlib
import io
import json
import sys
import tempfile
import urllib.error
from pathlib import Path

import pytest

from code_intelligence_agent.evaluation.github_repo_agent import (
    GitHubRepoAgentReport,
    _agent_summary,
    _build_fetch_error_report,
    _repository_test_final_diagnosis,
    _run_onboarding_tree,
    main as repo_agent_main,
    render_github_repo_agent_markdown,
    run_github_repo_agent,
)
from code_intelligence_agent.evaluation.benchmark_source_miner import (
    SourceMiningReport,
)
from code_intelligence_agent.evaluation.github_benchmark_onboarding import (
    GitHubBenchmarkOnboardingReport,
)
from code_intelligence_agent.evaluation.github_discovery_fetcher import GitHubAPIError
from code_intelligence_agent.evaluation.github_source_importer import (
    GitHubSourceImportReport,
)


def test_github_repo_agent_runs_smoke_preset_for_repo_url():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_agent"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_agent(
            "https://github.com/example/project.git",
            output_dir,
            opener=opener,
        )
        saved = json.loads(
            (output_dir / "github_repo_agent.json").read_text(encoding="utf-8")
        )

        assert report.status == "pass"
        assert report.passed is True
        assert report.owner == "example"
        assert report.repo == "project"
        assert report.summary["imported_sources"] == 1
        assert report.summary["selected_sources"] == 1
        assert report.summary["generated_candidates"] == 3
        assert report.summary["static_intelligence_status"] == "analysis_ready"
        assert report.summary["static_intelligence_level"] == "static_signals"
        assert report.summary["static_intelligence_reason"] == (
            "mined_static_candidates"
        )
        assert report.summary["static_intelligence_selected_signal_count"] == 3
        assert report.summary["static_intelligence_total_signal_count"] == 3
        assert report.summary["static_intelligence_rule_counts"][
            "missing_len_zero_guard"
        ] >= 1
        assert report.summary["static_intelligence_dynamic_validation_level"] == (
            "not_executed"
        )
        assert report.summary["static_intelligence_primary_artifact"].endswith(
            "source_mining.md"
        )
        assert "--checkout-repository-tests" in report.summary[
            "static_intelligence_next_action"
        ]
        assert report.summary["recipe_selection_mode"] == "auto_topk"
        assert report.summary["selected_recipes"] == [
            "missing_len_zero_guard",
            "always_true_len_check",
            "inverted_empty_guard",
        ]
        assert report.summary["benchmark_cases"] == 3
        assert report.summary["top1"] == 1.0
        assert report.summary["patch_success_rate"] == 1.0
        assert report.summary["project_config_count"] == 1
        assert report.summary["recommended_test_command"] == "python -m pytest"
        assert report.summary["recommended_target_prefix"] == ""
        assert report.summary["repository_test_command_status"] == "skipped"
        assert report.summary["repository_test_command_reason"] == (
            "full_repo_not_materialized"
        )
        assert report.summary["repository_test_command_repository_root"] == ""
        assert report.summary["repository_test_command_working_dir"] == ""
        assert report.summary["repository_test_command_cwd"] == ""
        assert report.summary["repository_test_setup_doctor_status"] == "blocked"
        assert report.summary["repository_test_setup_doctor_blocker"] == (
            "checkout:full_repo_not_materialized"
        )
        assert "--checkout-repository-tests" in report.summary[
            "repository_test_setup_doctor_next_action"
        ]
        assert report.summary["repository_test_setup_doctor_check_count"] == 8
        assert report.summary[
            "repository_test_setup_doctor_passed_check_count"
        ] == 3
        assert report.summary[
            "repository_test_setup_doctor_warning_check_count"
        ] == 3
        assert report.summary[
            "repository_test_setup_doctor_blocked_check_count"
        ] == 2
        assert report.summary[
            "repository_test_setup_doctor_skipped_check_count"
        ] == 0
        assert report.summary[
            "repository_test_setup_doctor_check_status_counts"
        ] == {
            "blocked": 2,
            "pass": 3,
            "warning": 3,
        }
        assert report.summary[
            "repository_test_setup_doctor_blocked_check_names"
        ] == [
            "full_repository_checkout",
            "execution_plan",
        ]
        assert report.summary[
            "repository_test_setup_doctor_warning_check_names"
        ] == [
            "environment_setup",
            "execution_result",
            "dynamic_evidence",
        ]
        assert saved["summary"]["repository_test_setup_doctor_check_count"] == 8
        assert "checks=3/8" in (
            output_dir / "github_repo_agent.md"
        ).read_text(encoding="utf-8")
        assert report.summary["repository_test_environment_setup_status"] == (
            "warning"
        )
        assert report.summary["repository_test_environment_setup_reason"] == (
            "repository_root_missing_for_install"
        )
        assert report.summary["repository_test_environment_setup_supported"] is True
        assert report.summary["repository_test_environment_setup_result_status"] == (
            "skipped"
        )
        assert report.summary["repository_test_environment_setup_result_reason"] == (
            "execution_disabled"
        )
        assert (
            report.summary["repository_test_environment_setup_result_executed"]
            is False
        )
        assert (
            report.summary[
                "repository_test_environment_setup_install_failure_category"
            ]
            == "none"
        )
        assert (
            report.summary["repository_test_environment_setup_install_failure_signal"]
            == ""
        )
        assert (
            report.summary[
                "repository_test_environment_setup_install_fallback_executed"
            ]
            is False
        )
        assert report.summary["repository_test_execution_plan_status"] == "warning"
        assert report.summary["repository_test_execution_plan_reason"] == (
            "full_repo_not_materialized"
        )
        assert report.summary["planned_repository_test_command"] == (
            "python -m pytest -q"
        )
        assert report.summary["planned_repository_test_level"] == "smoke"
        assert report.summary["planned_repository_test_preferred_runner"] == (
            "pytest"
        )
        assert (
            report.summary["planned_repository_test_runner_fallback_used"]
            is False
        )
        assert report.summary["planned_repository_test_runner_fallback_reason"] == ""
        assert report.summary["planned_repository_test_executable_now"] is False
        assert report.summary["planned_repository_test_result_status"] == "skipped"
        assert report.summary["planned_repository_test_result_reason"] == (
            "plan_not_executable"
        )
        assert report.summary["planned_repository_test_result_executed"] is False
        assert (
            report.summary["planned_repository_test_python_executable"]
            == sys.executable
        )
        assert (
            report.summary["planned_repository_test_python_source"]
            == "current_interpreter"
        )
        assert (
            report.summary["planned_repository_test_failure_category"]
            == "not_executed"
        )
        assert report.summary["repository_test_retry_status"] == "warning"
        assert report.summary["repository_test_retry_recommended"] is False
        assert (
            report.summary["repository_test_retry_strategy"]
            == "materialize_repository_checkout"
        )
        assert report.summary["repository_test_retry_execution_status"] == "skipped"
        assert report.summary["repository_test_retry_executed"] is False
        assert report.summary["repository_test_dynamic_evidence_level"] == (
            "not_executed"
        )
        assert report.summary["repository_test_dynamic_failing_tests"] == 0
        assert (
            report.summary["repository_test_dynamic_usable_for_localization"]
            is False
        )
        assert (
            report.summary[
                "repository_test_dynamic_usable_for_patch_validation"
            ]
            is False
        )
        assert (
            report.summary[
                "repository_test_dynamic_usable_for_regression_validation"
            ]
            is False
        )
        assert report.summary["repository_test_fault_localization_status"] == (
            "skipped"
        )
        assert report.summary["repository_test_fault_localization_reason"] == (
            "dynamic_evidence_not_usable"
        )
        assert report.summary["repository_test_fault_localization_ranking_count"] == 0
        assert report.summary["repository_test_patch_candidates_status"] == "skipped"
        assert report.summary["repository_test_patch_candidates_reason"] == (
            "fault_localization_not_ready"
        )
        assert report.summary["repository_test_patch_candidate_count"] == 0
        assert report.summary["repository_test_patch_validation_status"] == (
            "skipped"
        )
        assert report.summary["repository_test_patch_validation_reason"] == (
            "patch_candidates_not_ready"
        )
        assert report.summary["repository_test_patch_validation_success_count"] == 0
        assert report.summary["repository_test_final_status"] == "blocked"
        assert report.summary["repository_test_final_reason"] == (
            "repository_test_not_executed"
        )
        assert (
            report.summary[
                "repository_test_patch_validation_successful_reflection_count"
            ]
            == 0
        )
        assert report.summary["repository_test_patch_validation_max_depth"] == 0
        assert report.summary["repository_test_best_patch_candidate_success"] is False
        assert report.summary["repository_test_repair_ready"] is False
        assert report.summary["repository_test_repair_validation_scope"] == "none"
        assert report.summary["repository_test_regression_ready"] is False
        assert (
            report.summary["repository_test_regression_validation_status"]
            == "skipped"
        )
        assert report.summary["repository_test_best_patch_has_diff"] is False
        assert report.summary["repository_test_repair_patch_path"] == ""
        assert report.summary["repository_test_repair_summary_status"] == (
            "skipped"
        )
        assert report.summary["repository_test_repair_summary_reason"] == (
            "patch_candidates_not_ready"
        )
        assert report.summary["repository_test_repair_summary_conclusion"] == (
            "not_ready"
        )
        assert report.summary["repository_test_repair_summary_path"].endswith(
            "repository_test_repair_summary.md"
        )
        assert report.summary["quality_gate_passed"] is True
        assert report.summary["smoke_validation_passed"] is True
        assert report.summary["diagnostic_error_count"] == 0
        assert report.onboarding_report["quality_gate"]["thresholds"][
            "min_quality_score"
        ] == 0.0
        assert report.onboarding_report["quality_gate"]["thresholds"][
            "min_source_hit_rate"
        ] == 0.0
        assert saved["passed"] is True
        assert saved["summary"]["recipe_selection_mode"] == "auto_topk"
        assert saved["summary"]["benchmark_cases"] == 3
        assert saved["summary"]["repository_profile"]["project_config_files"] == [
            "pyproject.toml"
        ]
        assert saved["summary"]["repository_test_command_status"] == "skipped"
        assert saved["summary"]["repository_test_environment_setup_status"] == (
            "warning"
        )
        assert saved["summary"][
            "repository_test_environment_setup_result_status"
        ] == "skipped"
        assert saved["summary"]["repository_test_execution_plan_status"] == "warning"
        assert saved["summary"]["planned_repository_test_result_status"] == "skipped"
        assert (
            saved["summary"]["planned_repository_test_python_source"]
            == "current_interpreter"
        )
        assert (
            saved["summary"]["planned_repository_test_failure_category"]
            == "not_executed"
        )
        assert saved["summary"]["repository_test_retry_status"] == "warning"
        assert saved["summary"]["repository_test_retry_recommended"] is False
        assert (
            saved["summary"]["repository_test_retry_execution_status"]
            == "skipped"
        )
        assert saved["summary"]["repository_test_dynamic_evidence_level"] == (
            "not_executed"
        )
        assert saved["summary"]["repository_test_fault_localization_status"] == (
            "skipped"
        )
        assert saved["summary"]["repository_test_patch_candidates_status"] == (
            "skipped"
        )
        assert saved["summary"]["repository_test_patch_validation_status"] == (
            "skipped"
        )
        assert saved["summary"]["repository_test_final_status"] == "blocked"
        assert saved["summary"]["repository_test_final_reason"] == (
            "repository_test_not_executed"
        )
        plan = report.summary["agent_execution_plan"]
        assert [row["stage"] for row in plan] == [
            "source_discovery",
            "benchmarkization",
            "repository_test_setup",
            "repository_test_execution",
            "repository_repair",
        ]
        assert plan[0]["status"] == "pass"
        assert plan[1]["status"] == "pass"
        assert plan[2]["status"] == "blocked"
        assert plan[2]["blocker"] == "checkout:full_repo_not_materialized"
        assert report.summary["agent_execution_plan_primary_blocker"] == (
            "checkout:full_repo_not_materialized"
        )
        assert "--checkout-repository-tests" in report.summary[
            "agent_execution_plan_next_action"
        ]
        assert saved["summary"]["agent_execution_plan_status_counts"]["blocked"] >= 1
        assert (output_dir / "github_repo_agent.md").exists()
        assert (output_dir / "github_repo_agent_execution_plan.json").exists()
        assert (output_dir / "github_repo_agent_execution_plan.md").exists()
        plan_payload = json.loads(
            (output_dir / "github_repo_agent_execution_plan.json").read_text(
                encoding="utf-8"
            )
        )
        assert plan_payload["stage_count"] == 5
        assert plan_payload["primary_blocker"] == (
            "checkout:full_repo_not_materialized"
        )
        assert plan_payload["stages"][2]["stage"] == "repository_test_setup"
        assert (output_dir / "repository_profile.json").exists()
        assert (output_dir / "repository_profile.md").exists()
        assert (output_dir / "repository_test_environment_setup.json").exists()
        assert (output_dir / "repository_test_environment_setup.md").exists()
        assert (
            output_dir / "repository_test_environment_setup_result.json"
        ).exists()
        assert (output_dir / "repository_test_environment_setup_result.md").exists()
        assert (output_dir / "repository_test_execution_plan.json").exists()
        assert (output_dir / "repository_test_execution_plan.md").exists()
        assert (output_dir / "repository_test_execution_result.json").exists()
        assert (output_dir / "repository_test_execution_result.md").exists()
        assert (output_dir / "repository_test_retry_plan.json").exists()
        assert (output_dir / "repository_test_retry_plan.md").exists()
        assert (output_dir / "repository_test_retry_execution_result.json").exists()
        assert (output_dir / "repository_test_retry_execution_result.md").exists()
        assert (output_dir / "repository_test_dynamic_evidence.json").exists()
        assert (output_dir / "repository_test_dynamic_evidence.md").exists()
        assert (output_dir / "repository_test_fault_localization.json").exists()
        assert (output_dir / "repository_test_fault_localization.md").exists()
        assert (output_dir / "repository_test_patch_candidates.json").exists()
        assert (output_dir / "repository_test_patch_candidates.md").exists()
        assert (output_dir / "repository_test_patch_validation.json").exists()
        assert (output_dir / "repository_test_patch_validation.md").exists()
        assert (output_dir / "repository_test_repair_summary.json").exists()
        assert (output_dir / "repository_test_repair_summary.md").exists()
        assert (output_dir / "repository_test_command.json").exists()
        assert (output_dir / "repository_test_command.md").exists()
        assert (output_dir / "onboarding_report.json").exists()
        assert (output_dir / "onboarding_showcase_lite.json").exists()
        assert opener.urls == [
            "https://api.github.com/repos/example/project",
            "https://api.github.com/repos/example/project/git/trees/main?recursive=1",
        ]
        markdown = (output_dir / "github_repo_agent.md").read_text(encoding="utf-8")
        assert "## Static Intelligence" in markdown
        assert "Static Intelligence" in markdown
        assert "mined_static_candidates" in markdown
        assert "Recommended Test Command" in markdown
        assert "Repository Test Command Working Dir" in markdown
        assert "Repository Test Command CWD" in markdown
        assert "Repository Test Command Root" in markdown
        assert "Repository Test Environment Setup Status" in markdown
        assert "Repository Test Environment Setup Result Status" in markdown
        assert "Planned Repository Test Command" in markdown
        assert "Planned Repository Test Automatic Env Vars" in markdown
        assert "Repository Test Patch Validation Status" in markdown
        assert "Repository Test Repair Ready" in markdown
        assert "Repository Test Repair Validation Scope" in markdown
        assert "Repository Test Regression Validation" in markdown
        assert "Repository Test Patch Validation Reflection Successes" in markdown
        assert "Repository Test Best Patch File" in markdown
        assert "Repository Test Repair Patch" in markdown
        assert "Repository Test Repair Summary" in markdown
        assert "Repository Test Repair Summary Path" in markdown
        assert "Repository Test Final Status" in markdown
        assert "Repository Test Final Reason" in markdown
        assert "Planned Repository Test Result Status" in markdown
        assert "Planned Repository Test Python Source" in markdown
        assert "Planned Repository Test Failure Category" in markdown
        assert "Repository Test Retry Strategy" in markdown
        assert "Repository Test Retry Execution Status" in markdown
        assert "Repository Test Dynamic Evidence Level" in markdown
        assert "Repository Test Evidence Usable For Localization" in markdown
        assert "Repository Test Failure Overlay Selected Score" in markdown
        assert "Repository Test Failure Overlay Candidate Score Preview" in markdown
        assert "Repository Test Failure Overlay Candidate Rejection Counts" in markdown
        assert "Repository Test Fault Localization Status" in markdown
        assert "Repository Test Patch Candidates Status" in markdown
        assert "Agent Plan Primary Blocker" in markdown
        assert "## Agent Execution Plan" in markdown
        assert "| repository_test_setup | blocked | checkout:full_repo_not_materialized" in (
            markdown
        )
        plan_markdown = (
            output_dir / "github_repo_agent_execution_plan.md"
        ).read_text(encoding="utf-8")
        assert "# GitHub Repo Agent Execution Plan" in plan_markdown
        assert "| repository_test_execution | blocked | repository_test_not_executed" in (
            plan_markdown
        )


def test_github_repo_agent_summary_preserves_repository_test_command_location(tmp_path):
    checkout = tmp_path / "checkout"
    api_root = checkout / "services" / "api"
    onboarding = _minimal_onboarding_report(tmp_path).to_dict()
    onboarding["repository_test_command"] = {
        "status": "pass",
        "executed": True,
        "reason": "command_returncode",
        "repository_root": str(checkout),
        "working_dir": "services/api",
        "cwd": str(api_root),
    }
    summary = _agent_summary(onboarding)
    report = GitHubRepoAgentReport(
        repo_spec="example/project",
        owner="example",
        repo="project",
        output_dir=str(tmp_path),
        preset="mining",
        status="pass",
        summary=summary,
        output_paths={},
        onboarding_report=onboarding,
    )
    markdown = render_github_repo_agent_markdown(report)

    assert summary["repository_test_command_status"] == "pass"
    assert summary["repository_test_command_executed"] is True
    assert summary["repository_test_command_repository_root"] == str(checkout)
    assert summary["repository_test_command_working_dir"] == "services/api"
    assert summary["repository_test_command_cwd"] == str(api_root)
    assert "Repository Test Command Working Dir: `services/api`" in markdown
    assert f"Repository Test Command CWD: `{api_root}`" in markdown
    assert f"Repository Test Command Root: `{checkout}`" in markdown


def test_github_repo_agent_uses_ref_inferred_from_tree_url():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_agent"
        opener = _FakeOpener(
            [
                {
                    "sha": "dev123",
                    "tree": [
                        {"path": "pyproject.toml", "type": "blob"},
                        {
                            "path": "maths/average_mean.py",
                            "type": "blob",
                            "raw_url": str(raw_source),
                            "sha256": hashlib.sha256(
                                raw_source.read_bytes()
                            ).hexdigest(),
                        },
                    ],
                }
            ]
        )

        report = run_github_repo_agent(
            "https://github.com/example/project/tree/develop",
            output_dir,
            opener=opener,
        )

        assert report.status == "pass"
        assert opener.urls == [
            "https://api.github.com/repos/example/project/git/trees/develop?recursive=1"
        ]
        assert report.summary["imported_sources"] == 1
        assert report.summary["repository_ref"] == "develop"
        assert report.summary["requested_ref"] == "develop"
        assert report.summary["ref_source"] == "explicit"
        assert report.summary["repo_input"] == {
            "raw": "https://github.com/example/project/tree/develop",
            "kind": "github_url",
            "normalized_repo": "example/project",
            "owner": "example",
            "repo": "project",
            "explicit_ref": "",
            "url_inferred_ref": "develop",
            "requested_ref": "develop",
            "resolved_ref": "develop",
            "ref_source": "explicit",
            "ref_selection_source": "url_path_ref",
            "ref_fallback_used": False,
            "ref_fallback_attempt_count": 0,
            "ref_fallback_attempts": [],
        }
        assert report.summary["source_cache_dir"]
        assert report.onboarding_report["discovery_metadata"]["ref"] == "develop"
        assert report.onboarding_report["discovery_metadata"]["requested_ref"] == (
            "develop"
        )
        assert report.onboarding_report["discovery_metadata"]["ref_source"] == (
            "explicit"
        )
        markdown = render_github_repo_agent_markdown(report)
        assert "Input Kind: `github_url`" in markdown
        assert "Ref Selection Source: `url_path_ref`" in markdown
        assert "URL Inferred Ref: `develop`" in markdown
        assert "Ref Fallback Used: false" in markdown
        assert "Ref Fallback Attempts: 0" in markdown
        assert "Repository Ref: `develop`" in markdown
        assert "Requested Ref: `develop`" in markdown
        assert "Ref Source: `explicit`" in markdown


def test_github_repo_agent_retries_slash_ref_candidates_from_tree_url():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_agent"
        success_payload = {
            "sha": "feature-slash-123",
            "tree": [
                {"path": "pyproject.toml", "type": "blob"},
                {
                    "path": "maths/average_mean.py",
                    "type": "blob",
                    "raw_url": str(raw_source),
                    "sha256": hashlib.sha256(raw_source.read_bytes()).hexdigest(),
                },
            ],
        }
        opener = _RefFallbackOpener(success_payload)

        report = run_github_repo_agent(
            "https://github.com/example/project/tree/feature/slash",
            output_dir,
            opener=opener,
        )

        assert report.status == "pass"
        assert opener.urls == [
            "https://api.github.com/repos/example/project/git/trees/feature?recursive=1",
            "https://api.github.com/repos/example/project/git/trees/feature%2Fslash?recursive=1",
        ]
        repo_input = report.summary["repo_input"]
        assert report.summary["repository_ref"] == "feature/slash"
        assert report.summary["requested_ref"] == "feature/slash"
        assert report.summary["ref_source"] == "explicit"
        assert repo_input["url_inferred_ref"] == "feature/slash"
        assert repo_input["ref_selection_source"] == "url_path_ref"
        assert repo_input["ref_fallback_used"] is True
        assert repo_input["ref_fallback_attempt_count"] == 2
        assert repo_input["ref_fallback_attempts"][0]["ref"] == "feature"
        assert repo_input["ref_fallback_attempts"][0]["status"] == "retry"
        assert repo_input["ref_fallback_attempts"][0]["status_code"] == 404
        assert repo_input["ref_fallback_attempts"][1] == {
            "ref": "feature/slash",
            "status": "pass",
            "reason": "url_ref_candidate_resolved",
        }
        assert report.onboarding_report["discovery_metadata"]["ref"] == (
            "feature/slash"
        )
        markdown = render_github_repo_agent_markdown(report)
        assert "URL Inferred Ref: `feature/slash`" in markdown
        assert "Ref Fallback Used: true" in markdown
        assert "Ref Fallback Attempts: 2" in markdown
        assert "Repository Ref: `feature/slash`" in markdown


def test_github_repo_agent_fetch_error_report_has_repair_summary_defaults():
    with tempfile.TemporaryDirectory() as tmp_dir:
        report = _build_fetch_error_report(
            repo_spec="example/project",
            output_dir=Path(tmp_dir),
            preset="smoke",
            error=GitHubAPIError(
                "rate limit",
                status_code=403,
                url="https://api.github.com/repos/example/project",
            ),
        )

    assert report.status == "fail"
    assert report.summary["first_failing_stage"] == "github_fetch"
    assert report.summary["repository_test_repair_summary_status"] == ""
    assert report.summary["repository_test_repair_summary_reason"] == ""
    assert report.summary["repository_test_repair_summary_conclusion"] == ""
    assert report.summary["repository_test_repair_summary_path"] == ""
    markdown = render_github_repo_agent_markdown(report)
    assert "Repository Test Repair Summary" in markdown
    assert "Repository Test Repair Summary Path" in markdown


def test_github_repo_agent_summary_lifts_repository_repair_conclusion():
    summary = _agent_summary(
        {
            "repository_test_patch_validation": {
                "status": "pass",
                "reason": "patch_validation_success",
                "executed_count": 2,
                "success_count": 1,
                "repair_ready": True,
                "repair_validation_scope": "narrow_and_regression",
                "regression_ready": True,
                "regression_validation": {
                    "status": "pass",
                    "reason": "regression_tests_passed",
                    "validation_command": "python -m pytest -q tests",
                    "passed": 3,
                    "failed": 0,
                },
                "best_candidate_id": "candidate-1",
                "best_candidate_rule_id": "possible_index_overrun",
                "best_candidate_variant": "shrink_range_upper_bound",
                "best_candidate_success": True,
                "best_patch": {
                    "candidate_id": "candidate-1",
                    "relative_file_path": "sample.py",
                    "rule_id": "possible_index_overrun",
                    "variant": "shrink_range_upper_bound",
                    "depth": 0,
                    "diff": "--- a/sample.py\n+++ b/sample.py\n",
                },
            },
            "repository_test_patch_candidates": {
                "recommended_validation_command": (
                    "python -m pytest -q tests/test_sample.py::test_bug"
                )
            },
            "repository_test_analysis_route": {"phase3_validation_ready": True},
            "repository_test_execution_result": {},
            "repository_test_fault_localization": {},
            "repository_test_repair_summary": {
                "status": "pass",
                "reason": "repair_ready",
                "conclusion": "ready_for_review",
                "repair_ready": True,
                "repair_validation_scope": "narrow_and_regression",
                "patch_path": "out/repository_test_repair.patch",
                "patch_path_present": True,
            },
            "output_paths": {
                "repository_test_repair_patch": "out/repository_test_repair.patch",
                "repository_test_repair_summary_markdown": (
                    "out/repository_test_repair_summary.md"
                ),
                "repository_test_reflection_trace_json": (
                    "out/repository_test_reflection_trace.json"
                ),
                "repository_test_reflection_trace_markdown": (
                    "out/repository_test_reflection_trace.md"
                ),
                "reflection_trace_json": "out/reflection_trace.json",
                "reflection_trace_markdown": "out/reflection_trace.md",
            },
        }
    )

    assert summary["repository_test_final_status"] == "repaired"
    assert summary["repository_test_final_reason"] == (
        "patch_validation_success_with_regression"
    )
    assert summary["repository_test_repair_ready"] is True
    assert summary["repository_test_repair_validation_scope"] == (
        "narrow_and_regression"
    )
    assert summary["repository_test_regression_ready"] is True
    assert summary["repository_test_regression_validation_status"] == "pass"
    assert summary["repository_test_regression_validation_command"] == (
        "python -m pytest -q tests"
    )
    assert summary["repository_test_regression_validation_passed"] == 3
    assert summary["repository_test_best_patch_relative_file_path"] == "sample.py"
    assert summary["repository_test_best_patch_has_diff"] is True
    assert summary["repository_test_repair_patch_path"] == (
        "out/repository_test_repair.patch"
    )
    assert summary["repository_test_reflection_trace_status"] == "pass"
    assert summary["repository_test_reflection_trace_reason"] == (
        "depth0_success_no_reflection_needed"
    )
    assert summary["repository_test_reflection_trace_initial_failure_count"] == 0
    assert summary["repository_test_reflection_trace_step_count"] == 0
    assert summary["repository_test_reflection_trace_successful_step_count"] == 0
    assert summary["repository_test_reflection_trace_path"] == (
        "out/repository_test_reflection_trace.json"
    )
    assert summary["reflection_trace_path"] == "out/reflection_trace.json"
    assert summary["reflection_trace_markdown"] == "out/reflection_trace.md"
    assert summary["repository_test_repair_summary_status"] == "pass"
    assert summary["repository_test_repair_summary_reason"] == "repair_ready"
    assert (
        summary["repository_test_repair_summary_conclusion"]
        == "ready_for_review"
    )
    assert summary["repository_test_repair_summary_path"] == (
        "out/repository_test_repair_summary.md"
    )


def test_github_repo_agent_summary_lifts_llm_patch_telemetry(tmp_path):
    summary = _agent_summary(
        {
            "repository_test_patch_candidates": {
                "status": "warning",
                "reason": "no_patch_candidates_generated",
                "patch_generation_mode": "llm",
                "llm_generation_status": "error",
                "llm_generation_reason": "http_error",
                "llm_generation_telemetry": {
                    "request_count": 1,
                    "success_count": 0,
                    "failure_count": 1,
                    "total_tokens": 18,
                    "estimated_total_tokens": 24,
                    "latency_ms_total": 130,
                    "latency_ms_average": 130.0,
                    "cost_estimate": {
                        "available": True,
                        "estimated_cost_usd": 0.00024,
                    },
                    "error_reason_counts": {"http_401": 1},
                },
            }
        }
    )
    report = GitHubRepoAgentReport(
        repo_spec="example/project",
        owner="example",
        repo="project",
        output_dir=str(tmp_path),
        preset="mining",
        status="warning",
        summary=summary,
        output_paths={},
        onboarding_report={},
    )
    markdown = render_github_repo_agent_markdown(report)

    assert summary["repository_llm_patch_generation_status"] == "error"
    assert summary["repository_llm_patch_generation_reason"] == "http_error"
    assert summary["repository_llm_patch_request_count"] == 1
    assert summary["repository_llm_patch_success_count"] == 0
    assert summary["repository_llm_patch_failure_count"] == 1
    assert summary["repository_llm_patch_total_tokens"] == 18
    assert summary["repository_llm_patch_estimated_total_tokens"] == 24
    assert summary["repository_llm_patch_latency_ms_total"] == 130
    assert summary["repository_llm_patch_latency_ms_average"] == 130.0
    assert summary["repository_llm_patch_estimated_cost_usd"] == 0.00024
    assert summary["repository_llm_patch_error_reason_counts"] == {"http_401": 1}
    assert "Repository LLM Patch Telemetry: requests=1" in markdown
    assert "failures=1" in markdown


def test_github_repo_agent_final_diagnosis_explains_framework_configuration():
    pending = _repository_test_final_diagnosis(
        route={"analysis_source": "none"},
        execution_result={
            "status": "fail",
            "failure_category": "framework_configuration_error",
        },
        fault_localization={},
        patch_validation={},
        framework_config={
            "status": "warning",
            "reason": "django_settings_module_not_inferred",
            "environment_variables": {},
        },
    )
    injected = _repository_test_final_diagnosis(
        route={"analysis_source": "none"},
        execution_result={
            "status": "fail",
            "failure_category": "framework_configuration_error",
        },
        fault_localization={},
        patch_validation={},
        framework_config={
            "status": "pass",
            "reason": "django_settings_module_detected",
            "environment_variables": {
                "DJANGO_SETTINGS_MODULE": "mysite.settings",
            },
        },
    )
    regression_failed = _repository_test_final_diagnosis(
        route={"phase3_validation_ready": True},
        execution_result={},
        fault_localization={},
        patch_validation={
            "success_count": 1,
            "repair_ready": False,
            "repair_validation_scope": "regression_failed",
        },
    )

    assert pending == {
        "final_status": "blocked",
        "final_reason": (
            "framework_configuration_pending:django_settings_module_not_inferred"
        ),
    }
    assert injected == {
        "final_status": "blocked",
        "final_reason": (
            "framework_configuration_injected_but_failed:"
            "django_settings_module_detected"
        ),
    }
    assert regression_failed == {
        "final_status": "phase3_ready",
        "final_reason": "regression_validation_failed",
    }


def test_github_repo_agent_cli_runs_mining_preset_without_benchmark():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_agent"
        output_json = root / "agent.json"
        opener = _FakeOpener(_repo_payloads(raw_source))

        with pytest.raises(SystemExit) as exc_info:
            repo_agent_main(
                [
                    "example/project",
                    str(output_dir),
                    "--preset",
                    "mining",
                    "--recipe",
                    "missing_len_zero_guard",
                    "--output-json",
                    str(output_json),
                    "--format",
                    "json",
                    "--require-success",
                ],
                opener=opener,
            )
        saved = json.loads(output_json.read_text(encoding="utf-8"))

        assert exc_info.value.code == 0
        assert saved["status"] == "pass"
        assert saved["summary"]["generated_candidates"] == 1
        assert saved["summary"]["recipe_selection_mode"] == "explicit"
        assert saved["summary"]["selected_recipes"] == ["missing_len_zero_guard"]
        assert saved["summary"]["benchmark_cases"] == 0
        assert saved["summary"]["benchmarkization_status"] == (
            "ready_to_run_benchmark"
        )
        assert saved["summary"]["benchmarkization_primary_action_id"] == (
            "run_template_benchmark"
        )
        assert saved["summary"][
            "benchmarkization_primary_action_auto_runnable"
        ] is True
        assert saved["summary"]["benchmarkization_primary_action_stage"] == "benchmark"
        assert saved["summary"]["benchmarkization_primary_action_risk"] == "low"
        assert saved["summary"]["benchmarkization_primary_action_requires"] == [
            "source_mining_template",
            "source_cache_dir",
        ]
        assert saved["summary"][
            "benchmarkization_primary_action_expected_outcome"
        ] == (
            "Benchmark report is written and benchmarkization advances to "
            "benchmark_ready."
        )
        assert Path(
            saved["summary"]["benchmarkization_remediation_plan_json"]
        ).exists()
        assert Path(
            saved["summary"]["benchmarkization_remediation_plan_markdown"]
        ).exists()
        assert saved["summary"]["benchmarkization_remediation_plan_markdown"] == (
            saved["output_paths"]["benchmarkization_remediation_plan_markdown"]
        )
        assert saved["summary"]["agent_execution_plan"][1]["stage"] == (
            "benchmarkization"
        )
        assert saved["summary"]["agent_execution_plan"][1]["status"] == "ready"
        assert saved["summary"]["agent_execution_plan"][1]["artifact"] == (
            saved["output_paths"]["benchmarkization_remediation_plan_markdown"]
        )
        assert "run_template_benchmark" in saved["summary"][
            "agent_execution_plan_next_command"
        ]
        plan_payload = json.loads(
            Path(saved["output_paths"]["agent_execution_plan_json"]).read_text(
                encoding="utf-8"
            )
        )
        assert plan_payload["stages"][1]["status"] == "ready"
        assert plan_payload["stages"][1]["artifact"] == (
            saved["output_paths"]["benchmarkization_remediation_plan_markdown"]
        )
        assert "run_template_benchmark" in plan_payload["next_command"]
        plan_markdown = Path(
            saved["output_paths"]["agent_execution_plan_markdown"]
        ).read_text(encoding="utf-8")
        assert "benchmarkization_remediation_plan.md" in plan_markdown
        assert "auto_remediation_attempted" not in saved["summary"]
        assert saved["summary"]["quality_gate_passed"] is True
        assert saved["onboarding_report"]["quality_gate"]["thresholds"][
            "min_quality_score"
        ] == 0.0
        assert saved["onboarding_report"]["quality_gate"]["thresholds"][
            "min_source_hit_rate"
        ] == 0.0
        assert saved["onboarding_report"]["preset"] == "mining"
        assert saved["onboarding_report"]["benchmark_run"] is None
        assert (output_dir / "github_repo_agent.json").exists()
        assert (output_dir / "onboarding_run_config.json").exists()


def test_github_repo_agent_auto_remediates_mining_benchmark_action():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_agent"
        payloads = _repo_payloads(raw_source)
        opener = _FakeOpener([*payloads, *payloads])

        report = run_github_repo_agent(
            "example/project",
            output_dir,
            preset="mining",
            recipes=["missing_len_zero_guard"],
            auto_remediate_benchmark=True,
            opener=opener,
        )
        saved = json.loads(
            (output_dir / "github_repo_agent.json").read_text(encoding="utf-8")
        )
        pre_remediation = json.loads(
            (output_dir / "github_repo_agent_pre_remediation.json").read_text(
                encoding="utf-8"
            )
        )
        markdown = (output_dir / "github_repo_agent.md").read_text(encoding="utf-8")

        assert report.status == "pass"
        assert report.summary["benchmark_cases"] == 1
        assert report.summary["benchmarkization_status"] == "benchmark_ready"
        assert report.summary["auto_remediation_attempted"] is True
        assert report.summary["auto_remediation_used"] is True
        assert report.summary["auto_remediation_improved"] is True
        assert report.summary["auto_remediation_action_id"] == (
            "run_template_benchmark"
        )
        assert report.summary["primary_benchmarkization_status"] == (
            "ready_to_run_benchmark"
        )
        assert report.summary["remediated_benchmarkization_status"] == (
            "benchmark_ready"
        )
        assert report.summary["primary_benchmark_cases"] == 0
        assert report.summary["remediated_benchmark_cases"] == 1
        assert saved["summary"]["auto_remediation_action_id"] == (
            "run_template_benchmark"
        )
        assert saved["onboarding_report"]["benchmark_run"]["summary"][
            "case_count"
        ] == 1
        assert Path(saved["output_paths"]["pre_remediation_agent_json"]).exists()
        assert Path(
            saved["output_paths"]["agent_execution_plan_json"]
        ).exists()
        assert Path(
            saved["output_paths"]["agent_execution_plan_markdown"]
        ).exists()
        assert Path(
            saved["output_paths"]["pre_remediation_agent_execution_plan_json"]
        ).exists()
        assert Path(
            saved["summary"]["benchmarkization_remediation_plan_markdown"]
        ).exists()
        assert pre_remediation["summary"]["benchmark_cases"] == 0
        assert pre_remediation["summary"]["benchmarkization_status"] == (
            "ready_to_run_benchmark"
        )
        assert "## Auto Remediation" in markdown
        assert "run_template_benchmark" in markdown
        assert opener.urls == [
            "https://api.github.com/repos/example/project",
            "https://api.github.com/repos/example/project/git/trees/main?recursive=1",
            "https://api.github.com/repos/example/project",
            "https://api.github.com/repos/example/project/git/trees/main?recursive=1",
        ]


def test_github_repo_agent_reports_no_python_sources_at_top_level():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        output_dir = root / "repo_agent"
        opener = _FakeOpener(_repo_payloads_no_python())

        report = run_github_repo_agent(
            "example/docs",
            output_dir,
            preset="mining",
            opener=opener,
        )
        saved = json.loads(
            (output_dir / "github_repo_agent.json").read_text(encoding="utf-8")
        )
        markdown = (output_dir / "github_repo_agent.md").read_text(encoding="utf-8")

        assert report.status == "fail"
        assert report.passed is False
        assert report.summary["discovery_items"] == 2
        assert report.summary["imported_sources"] == 0
        assert report.summary["generated_candidates"] == 0
        assert report.summary["diagnostics_status"] == "fail"
        assert report.summary["first_failing_stage"] == "source_import"
        assert report.summary["diagnostic_error_count"] >= 1
        assert "no_imported_sources" in report.summary["diagnostic_issue_codes"]
        assert saved["summary"]["diagnostic_issue_codes"] == report.summary[
            "diagnostic_issue_codes"
        ]
        assert "no_imported_sources" in markdown
        assert opener.urls == [
            "https://api.github.com/repos/example/docs",
            "https://api.github.com/repos/example/docs/git/trees/main?recursive=1",
        ]


def test_github_repo_agent_reports_no_generated_candidates_with_recipe_suggestions():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_plain_add(root)
        output_dir = root / "repo_agent"
        opener = _FakeOpener(
            _repo_payloads_for_source(
                raw_source,
                path="maths/plain_add.py",
            )
        )

        report = run_github_repo_agent(
            "example/project",
            output_dir,
            preset="mining",
            recipes=["missing_len_zero_guard"],
            auto_fallback=False,
            opener=opener,
        )
        saved = json.loads(
            (output_dir / "github_repo_agent.json").read_text(encoding="utf-8")
        )
        markdown = (output_dir / "github_repo_agent.md").read_text(encoding="utf-8")

        assert report.status == "fail"
        assert report.passed is False
        assert report.summary["imported_sources"] == 1
        assert report.summary["selected_sources"] == 1
        assert report.summary["generated_candidates"] == 0
        assert report.summary["diagnostics_status"] == "fail"
        assert report.summary["first_failing_stage"] == "source_mining"
        assert "no_generated_candidates" in report.summary["diagnostic_issue_codes"]
        assert report.summary["recipe_miss_count"] >= 1
        assert report.summary["recipe_suggestion_count"] >= 1
        assert report.summary["recipe_suggestion_preview"][0]["recipe"] == (
            "missing_len_zero_guard"
        )
        assert report.summary["recipe_suggestion_preview"][0]["top_reasons"][0][
            "reason"
        ] == "no_empty_guard_len_denominator_function"
        assert any(
            "empty-input guards" in action
            for action in report.summary["recipe_suggestion_preview"][0][
                "suggested_actions"
            ]
        )
        assert saved["summary"]["recipe_suggestion_count"] == (
            report.summary["recipe_suggestion_count"]
        )
        assert "Recipe Suggestions" in markdown
        assert "no_generated_candidates" in markdown
        assert "missing_len_zero_guard" in markdown


def test_github_repo_agent_auto_fallback_recovers_no_candidate_run():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_inplace_sort(root)
        output_dir = root / "repo_agent"
        payloads = _repo_payloads_for_source(
            raw_source,
            path="maths/sort_helpers.py",
        )
        opener = _FakeOpener([*payloads, *payloads])

        report = run_github_repo_agent(
            "example/project",
            output_dir,
            preset="mining",
            recipes=["missing_len_zero_guard"],
            opener=opener,
        )
        saved = json.loads(
            (output_dir / "github_repo_agent.json").read_text(encoding="utf-8")
        )
        markdown = (output_dir / "github_repo_agent.md").read_text(encoding="utf-8")

        assert report.status == "pass"
        assert report.passed is True
        assert report.summary["fallback_attempted"] is True
        assert report.summary["fallback_used"] is True
        assert report.summary["fallback_improved"] is True
        assert report.summary["fallback_recovered"] is True
        assert report.summary["fallback_reason"] == "no_generated_candidates"
        assert report.summary["primary_generated_candidates"] == 0
        assert report.summary["fallback_generated_candidates"] >= 1
        assert report.summary["primary_benchmarkization_status"] == (
            "blocked_at_candidate_generation"
        )
        assert report.summary["fallback_benchmarkization_status"] == (
            "ready_to_run_benchmark"
        )
        assert report.summary["primary_benchmarkization_primary_action_id"] == (
            "inspect_recipe_misses"
        )
        assert report.summary["fallback_benchmarkization_primary_action_id"] == (
            "run_template_benchmark"
        )
        assert Path(
            report.summary["primary_benchmarkization_remediation_plan_markdown"]
        ).exists()
        assert Path(
            report.summary["fallback_benchmarkization_remediation_plan_markdown"]
        ).exists()
        assert report.summary["generated_candidates"] >= 1
        assert report.summary["recipe_selection_mode"] == "auto_topk"
        assert "inplace_api_return_value" in report.summary["selected_recipes"]
        assert saved["summary"]["fallback_reason"] == "no_generated_candidates"
        assert Path(saved["output_paths"]["primary_agent_json"]).exists()
        assert Path(saved["output_paths"]["fallback_agent_json"]).exists()
        assert Path(
            saved["output_paths"]["primary_benchmarkization_remediation_plan_json"]
        ).exists()
        assert Path(
            saved["output_paths"]["fallback_benchmarkization_remediation_plan_json"]
        ).exists()
        assert Path(saved["output_paths"]["source_mining_json"]).is_relative_to(
            output_dir / "fallback"
        )
        assert "## Auto Fallback" in markdown
        assert "Fallback Generated Candidates" in markdown
        assert "Primary Remediation Plan" in markdown
        assert "Fallback Remediation Plan" in markdown
        assert "benchmarkization_remediation_plan.md" in markdown
        assert opener.urls == [
            "https://api.github.com/repos/example/project",
            "https://api.github.com/repos/example/project/git/trees/main?recursive=1",
            "https://api.github.com/repos/example/project",
            "https://api.github.com/repos/example/project/git/trees/main?recursive=1",
        ]


def test_github_repo_agent_cli_writes_report_for_github_api_error():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        output_dir = root / "repo_agent"
        output_json = root / "agent_error.json"
        output_markdown = root / "agent_error.md"
        opener = _FailingHTTPErrorOpener(
            status=403,
            reason="rate limit exceeded",
            body={
                "message": "API rate limit exceeded",
                "documentation_url": "https://docs.github.com/rest",
            },
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "1729",
            },
        )

        with pytest.raises(SystemExit) as exc_info:
            repo_agent_main(
                [
                    "example/project",
                    str(output_dir),
                    "--output-json",
                    str(output_json),
                    "--output-markdown",
                    str(output_markdown),
                    "--require-success",
                ],
                opener=opener,
            )
        saved = json.loads(output_json.read_text(encoding="utf-8"))
        agent_saved = json.loads(
            (output_dir / "github_repo_agent.json").read_text(encoding="utf-8")
        )
        markdown = output_markdown.read_text(encoding="utf-8")

        assert exc_info.value.code == 1
        assert saved["status"] == "fail"
        assert saved["passed"] is False
        assert saved["onboarding_report"] is None
        assert saved["summary"]["first_failing_stage"] == "github_fetch"
        assert saved["summary"]["diagnostic_issue_codes"] == ["github_api_error"]
        assert saved["summary"]["diagnostic_error_count"] == 1
        assert saved["summary"]["github_error"]["status_code"] == 403
        assert saved["summary"]["github_error"]["rate_limit_remaining"] == "0"
        assert "GITHUB_TOKEN" in "\n".join(saved["summary"]["next_actions"])
        assert agent_saved["summary"]["github_error"]["rate_limit_reset"] == "1729"
        assert (output_dir / "github_repo_agent.md").exists()
        assert "## GitHub Error" in markdown
        assert opener.urls == [
            "https://api.github.com/repos/example/project",
            "https://codeload.github.com/example/project/zip/HEAD",
        ]


def test_github_repo_agent_forwards_phase3_repository_test_options(monkeypatch):
    captured = {}

    def fake_onboarding(owner, repo, ref, output_root, **kwargs):
        captured.update(
            {
                "owner": owner,
                "repo": repo,
                "ref": ref,
                **kwargs,
            }
        )
        return _minimal_onboarding_report(output_root, preset=kwargs["preset"])

    monkeypatch.setattr(
        "code_intelligence_agent.evaluation.github_repo_agent._run_onboarding_tree",
        fake_onboarding,
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        output_dir = root / "repo_agent"
        repository_root = root / "checkout"

        report = run_github_repo_agent(
            "example/project",
            output_dir,
            preset="mining",
            repository_test_root=repository_root,
            repository_test_timeout=7,
            repository_test_failure_overlay_candidate_limit=2,
            repository_test_reflection_mode="none",
            repository_test_reflection_rounds=3,
            repository_test_reflection_width=4,
            patch_judge_mode="llm",
            run_repository_test_command=False,
            run_repository_test_environment_setup=True,
            repository_test_environment_setup_mode="runner_probe",
            run_repository_test_retry=True,
            run_repository_test_retry_prerequisites=True,
            auto_repository_test_retry=True,
            auto_repository_test_retry_max_risk="medium",
            auto_repository_test_retry_allowed_runners=["pytest", "unittest"],
            repository_test_environment_setup_timeout=9,
            checkout_repository_tests=True,
            repository_checkout_timeout=11,
            repository_checkout_depth=2,
            auto_fallback=False,
        )

        assert report.output_dir == str(output_dir)
        assert captured["owner"] == "example"
        assert captured["repo"] == "project"
        assert captured["repository_test_root"] == repository_root
        assert captured["repository_test_timeout"] == 7
        assert captured["repository_test_failure_overlay_candidate_limit"] == 2
        assert captured["repository_test_reflection_mode"] == "none"
        assert captured["repository_test_reflection_rounds"] == 3
        assert captured["repository_test_reflection_width"] == 4
        assert captured["patch_judge_mode"] == "llm"
        assert captured["run_repository_test_command"] is False
        assert captured["run_repository_test_environment_setup"] is True
        assert captured["repository_test_environment_setup_mode"] == (
            "runner_probe"
        )
        assert captured["run_repository_test_retry"] is True
        assert captured["run_repository_test_retry_prerequisites"] is True
        assert captured["auto_repository_test_retry"] is True
        assert captured["auto_repository_test_retry_max_risk"] == "medium"
        assert captured["auto_repository_test_retry_allowed_runners"] == [
            "pytest",
            "unittest",
        ]
        assert captured["repository_test_environment_setup_timeout"] == 9
        assert captured["checkout_repository_tests"] is True
        assert captured["repository_checkout_timeout"] == 11
        assert captured["repository_checkout_depth"] == 2
        assert (output_dir / "github_repo_agent.json").exists()


def test_run_onboarding_tree_reuses_cached_discovery_after_rate_limit(
    tmp_path,
    monkeypatch,
):
    output_dir = tmp_path / "repo_agent"
    output_dir.mkdir()
    (output_dir / "discovery.json").write_text(
        json.dumps(
            {
                "owner": "example",
                "repo": "project",
                "ref": "main",
                "tree": [
                    {
                        "path": "sample.py",
                        "type": "blob",
                    }
                ],
                "discovery": {
                    "mode": "tree",
                    "owner": "example",
                    "repo": "project",
                    "ref": "main",
                },
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    def fake_onboard_tree(*args, **kwargs):
        raise GitHubAPIError(
            "GitHub API request failed with HTTP 403: rate limit exceeded",
            status_code=403,
            rate_limit_remaining="0",
            response_body="API rate limit exceeded",
        )

    def fake_onboard_from_discovery(discovery_payload, output_root, **kwargs):
        captured["discovery_payload"] = discovery_payload
        captured["output_root"] = output_root
        captured["kwargs"] = kwargs
        return _minimal_onboarding_report(
            output_root,
            preset=kwargs["preset"],
            source=kwargs["source"],
            discovery_metadata=discovery_payload["discovery"],
        )

    monkeypatch.setattr(
        "code_intelligence_agent.evaluation.github_repo_agent.onboard_tree",
        fake_onboard_tree,
    )
    monkeypatch.setattr(
        "code_intelligence_agent.evaluation.github_repo_agent.onboard_from_discovery",
        fake_onboard_from_discovery,
    )

    report = _run_onboarding_tree(
        "example",
        "project",
        None,
        output_dir,
        token="token",
        recursive=True,
        api_base_url="https://api.github.com",
        timeout=20,
        opener=object(),
        preset="mining",
        include=["sample.py"],
        source_cache_dir=tmp_path / "cache",
    )

    assert report.preset == "mining"
    assert captured["output_root"] == output_dir
    assert captured["discovery_payload"]["discovery"]["cache_fallback"] is True
    assert captured["kwargs"]["source"] == "cached-discovery:example/project@main"
    assert captured["kwargs"]["owner"] == "example"
    assert captured["kwargs"]["repo"] == "project"
    assert captured["kwargs"]["ref"] == "main"
    assert captured["kwargs"]["include"] == ["sample.py"]
    assert captured["kwargs"]["source_cache_dir"] == tmp_path / "cache"
    assert "token" not in captured["kwargs"]
    assert "api_base_url" not in captured["kwargs"]
    assert "opener" not in captured["kwargs"]

    summary = _agent_summary(report.to_dict())
    assert summary["discovery_source"] == "cached-discovery:example/project@main"
    assert summary["discovery_cache_fallback"] is True
    assert summary["discovery_cache_fallback_source"] == str(
        output_dir / "discovery.json"
    )


def test_run_onboarding_tree_prefers_cached_discovery_without_network(
    tmp_path,
    monkeypatch,
):
    output_dir = tmp_path / "repo_agent"
    output_dir.mkdir()
    (output_dir / "discovery.json").write_text(
        json.dumps(
            {
                "owner": "example",
                "repo": "project",
                "ref": "main",
                "tree": [
                    {
                        "path": "sample.py",
                        "type": "blob",
                    }
                ],
                "discovery": {
                    "mode": "tree",
                    "owner": "example",
                    "repo": "project",
                    "ref": "main",
                },
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    def fake_onboard_tree(*args, **kwargs):
        raise AssertionError("onboard_tree should not be called")

    def fake_onboard_from_discovery(discovery_payload, output_root, **kwargs):
        captured["discovery_payload"] = discovery_payload
        captured["output_root"] = output_root
        captured["kwargs"] = kwargs
        return _minimal_onboarding_report(
            output_root,
            preset=kwargs["preset"],
            source=kwargs["source"],
            discovery_metadata=discovery_payload["discovery"],
        )

    monkeypatch.setattr(
        "code_intelligence_agent.evaluation.github_repo_agent.onboard_tree",
        fake_onboard_tree,
    )
    monkeypatch.setattr(
        "code_intelligence_agent.evaluation.github_repo_agent.onboard_from_discovery",
        fake_onboard_from_discovery,
    )

    report = _run_onboarding_tree(
        "example",
        "project",
        None,
        output_dir,
        prefer_cached_discovery=True,
        token="token",
        recursive=True,
        api_base_url="https://api.github.com",
        timeout=20,
        opener=object(),
        preset="mining",
        include=["sample.py"],
        source_cache_dir=tmp_path / "cache",
    )

    discovery = captured["discovery_payload"]["discovery"]
    summary = _agent_summary(report.to_dict())
    assert captured["output_root"] == output_dir
    assert captured["kwargs"]["source"] == (
        "cached-discovery-preferred:example/project@main"
    )
    assert discovery["cache_reuse"] is True
    assert discovery["cache_preferred"] is True
    assert discovery["cache_reuse_reason"] == "prefer_cached_discovery"
    assert "cache_fallback" not in discovery
    assert summary["discovery_cache_reuse"] is True
    assert summary["discovery_cache_preferred"] is True
    assert summary["discovery_cache_fallback"] is False
    assert summary["discovery_cache_reuse_reason"] == "prefer_cached_discovery"
    assert summary["discovery_cache_reuse_source"] == str(
        output_dir / "discovery.json"
    )


def test_run_onboarding_tree_uses_checkout_seed_after_rate_limit_when_requested(
    tmp_path,
    monkeypatch,
):
    output_dir = tmp_path / "repo_agent"
    output_dir.mkdir()
    captured = {}

    def fake_onboard_tree(*args, **kwargs):
        raise GitHubAPIError(
            "GitHub API request failed with HTTP 403: rate limit exceeded",
            status_code=403,
            rate_limit_remaining="0",
            rate_limit_reset="1729",
            response_body="API rate limit exceeded",
        )

    def fake_onboard_from_discovery(discovery_payload, output_root, **kwargs):
        captured["discovery_payload"] = discovery_payload
        captured["output_root"] = output_root
        captured["kwargs"] = kwargs
        return _minimal_onboarding_report(
            output_root,
            preset=kwargs["preset"],
            source=kwargs["source"],
            discovery_metadata=discovery_payload["discovery"],
        )

    monkeypatch.setattr(
        "code_intelligence_agent.evaluation.github_repo_agent.onboard_tree",
        fake_onboard_tree,
    )
    monkeypatch.setattr(
        "code_intelligence_agent.evaluation.github_repo_agent.onboard_from_discovery",
        fake_onboard_from_discovery,
    )

    report = _run_onboarding_tree(
        "example",
        "project",
        "develop",
        output_dir,
        token="token",
        recursive=True,
        api_base_url="https://api.github.com",
        timeout=20,
        opener=object(),
        preset="mining",
        include=["src/**/*.py"],
        source_cache_dir=tmp_path / "cache",
        checkout_repository_tests=True,
        repository_checkout_timeout=10,
    )

    discovery = captured["discovery_payload"]["discovery"]
    assert captured["output_root"] == output_dir
    assert captured["discovery_payload"]["files"] == []
    assert discovery["mode"] == "rate_limit_checkout_seed"
    assert discovery["api_rate_limit_checkout_fallback"] is True
    assert discovery["api_rate_limit_status_code"] == 403
    assert discovery["api_rate_limit_remaining"] == "0"
    assert discovery["api_rate_limit_reset"] == "1729"
    assert discovery["api_rate_limit_checkout_mode"] == "test_execution"
    assert discovery["api_rate_limit_original_checkout_requested"] is True
    assert captured["kwargs"]["source"] == (
        "github-api-rate-limit-checkout:example/project@develop"
    )
    assert captured["kwargs"]["checkout_repository_tests"] is True
    assert captured["kwargs"]["include"] == ["src/**/*.py"]
    assert captured["kwargs"]["source_cache_dir"] == tmp_path / "cache"
    assert "token" not in captured["kwargs"]
    assert "api_base_url" not in captured["kwargs"]
    assert "opener" not in captured["kwargs"]

    summary = _agent_summary(report.to_dict())
    assert summary["discovery_source"] == (
        "github-api-rate-limit-checkout:example/project@develop"
    )
    assert summary["discovery_api_rate_limit_checkout_fallback"] is True
    assert summary["discovery_api_rate_limit_status_code"] == 403
    assert summary["discovery_api_rate_limit_remaining"] == "0"


def test_run_onboarding_tree_uses_source_only_checkout_after_rate_limit(
    tmp_path,
    monkeypatch,
):
    output_dir = tmp_path / "repo_agent"
    output_dir.mkdir()
    captured = {}

    def fake_onboard_tree(*args, **kwargs):
        raise GitHubAPIError(
            "GitHub API request failed with HTTP 403: rate limit exceeded",
            status_code=403,
            rate_limit_remaining="0",
            rate_limit_reset="1729",
            response_body="API rate limit exceeded",
        )

    def fake_onboard_from_discovery(discovery_payload, output_root, **kwargs):
        captured["discovery_payload"] = discovery_payload
        captured["output_root"] = output_root
        captured["kwargs"] = kwargs
        return _minimal_onboarding_report(
            output_root,
            preset=kwargs["preset"],
            source=kwargs["source"],
            discovery_metadata=discovery_payload["discovery"],
        )

    monkeypatch.setattr(
        "code_intelligence_agent.evaluation.github_repo_agent.onboard_tree",
        fake_onboard_tree,
    )
    monkeypatch.setattr(
        "code_intelligence_agent.evaluation.github_repo_agent.onboard_from_discovery",
        fake_onboard_from_discovery,
    )

    report = _run_onboarding_tree(
        "example",
        "project",
        "develop",
        output_dir,
        token=None,
        recursive=True,
        api_base_url="https://api.github.com",
        timeout=20,
        opener=object(),
        preset="mining",
        source_cache_dir=tmp_path / "cache",
        checkout_repository_tests=False,
        run_repository_test_command=True,
        run_repository_test_environment_setup=True,
        run_repository_test_retry=True,
        run_repository_test_retry_prerequisites=True,
        auto_repository_test_retry=True,
    )

    discovery = captured["discovery_payload"]["discovery"]
    kwargs = captured["kwargs"]
    assert captured["output_root"] == output_dir
    assert discovery["api_rate_limit_checkout_mode"] == "source_only"
    assert discovery["api_rate_limit_original_checkout_requested"] is False
    assert kwargs["source"] == (
        "github-api-rate-limit-source-checkout:example/project@develop"
    )
    assert kwargs["checkout_repository_tests"] is True
    assert kwargs["run_repository_test_command"] is False
    assert kwargs["run_repository_test_environment_setup"] is False
    assert kwargs["run_repository_test_retry"] is False
    assert kwargs["run_repository_test_retry_prerequisites"] is False
    assert kwargs["auto_repository_test_retry"] is False

    summary = _agent_summary(report.to_dict())
    assert summary["discovery_api_rate_limit_checkout_fallback"] is True
    assert summary["discovery_api_rate_limit_checkout_mode"] == "source_only"
    assert (
        summary["discovery_api_rate_limit_original_checkout_requested"] is False
    )


def _minimal_onboarding_report(
    output_dir: str | Path,
    *,
    preset: str = "mining",
    source: str = "github_tree",
    discovery_metadata: dict | None = None,
) -> GitHubBenchmarkOnboardingReport:
    root = Path(output_dir)
    return GitHubBenchmarkOnboardingReport(
        mode="repo",
        preset=preset,
        source=source,
        output_dir=str(root),
        discovery_item_count=0,
        imported_source_count=0,
        selected_source_count=0,
        skipped_source_count=0,
        generated_candidate_count=0,
        ready_for_benchmark=False,
        source_limit=0,
        candidate_limit=0,
        requested_urls=[],
        discovery_metadata=discovery_metadata or {},
        repository_profile={},
        quality_summary={},
        output_paths={
            "source_import_json": str(root / "source_import.json"),
            "source_mining_json": str(root / "source_mining.json"),
            "source_mining_markdown": str(root / "source_mining.md"),
        },
        import_report=GitHubSourceImportReport(
            source_path="",
            input_count=0,
            source_count=0,
            skipped_count=0,
            rows=[],
            source_entries=[],
        ),
        mining_report=SourceMiningReport(
            source_path="",
            source_count=0,
            recipe_count=0,
            recipes=[],
            generated_source_count=0,
            generated_count=0,
            rule_counts={},
            bug_type_counts={},
            quality_summary={},
            sources=[],
            candidates=[],
        ),
        benchmarkization_readiness={
            "status": "blocked",
            "next_actions": [],
            "remediation_plan": {"actions": []},
        },
        diagnostics={"headline": {}, "issues": [], "next_actions": []},
    )


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


class _RefFallbackOpener:
    def __init__(self, success_payload):
        self.success_payload = success_payload
        self.urls = []

    def __call__(self, request, timeout):
        del timeout
        self.urls.append(request.full_url)
        if request.full_url.endswith("/git/trees/feature?recursive=1"):
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                "Not Found",
                {},
                io.BytesIO(json.dumps({"message": "Not Found"}).encode("utf-8")),
            )
        return _FakeResponse(self.success_payload)


class _FailingHTTPErrorOpener:
    def __init__(self, *, status, reason, body, headers=None):
        self.status = status
        self.reason = reason
        self.body = body
        self.headers = headers or {}
        self.urls = []

    def __call__(self, request, timeout):
        del timeout
        self.urls.append(request.full_url)
        body_bytes = json.dumps(self.body).encode("utf-8")
        raise urllib.error.HTTPError(
            request.full_url,
            self.status,
            self.reason,
            self.headers,
            io.BytesIO(body_bytes),
        )


def _repo_payloads(raw_source: Path) -> list[dict]:
    return _repo_payloads_for_source(raw_source, path="maths/average_mean.py")


def _repo_payloads_for_source(raw_source: Path, *, path: str) -> list[dict]:
    return [
        {"default_branch": "main"},
        {
            "sha": "abc123",
            "tree": [
                {
                    "path": "pyproject.toml",
                    "type": "blob",
                },
                {
                    "path": path,
                    "type": "blob",
                    "raw_url": str(raw_source),
                    "sha256": hashlib.sha256(raw_source.read_bytes()).hexdigest(),
                    "license": "MIT",
                }
            ],
        },
    ]


def _write_plain_add(root: Path) -> Path:
    raw_source = root / "plain_add.py"
    raw_source.write_text(
        "def add(left, right):\n"
        "    return left + right\n",
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


def _repo_payloads_no_python() -> list[dict]:
    return [
        {"default_branch": "main"},
        {
            "sha": "abc123",
            "tree": [
                {
                    "path": "README.md",
                    "type": "blob",
                    "raw_url": "https://raw.githubusercontent.com/example/docs/main/README.md",
                },
                {
                    "path": "docs/usage.rst",
                    "type": "blob",
                    "raw_url": "https://raw.githubusercontent.com/example/docs/main/docs/usage.rst",
                },
            ],
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
