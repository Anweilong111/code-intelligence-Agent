import hashlib
import json
import os
from pathlib import Path

from code_intelligence_agent.agents.patch_generator import PatchGenerator
import code_intelligence_agent.evaluation.github_benchmark_onboarding as onboarding_module
import code_intelligence_agent.evaluation.github_repo_intelligence_suite as suite_module
from code_intelligence_agent.evaluation.github_repo_intelligence_suite import (
    GitHubRepoIntelligenceSuiteReport,
    GitHubRepoIntelligenceSuiteRunResult,
    _apply_execution_profile_options,
    _cached_run_result_from_existing_report,
    _command_args,
    _is_rate_limit_error,
    _repo_input_kind,
    _suite_metric_snapshot,
    _suite_summary,
    _temporary_cleared_env,
    render_github_repo_intelligence_suite_markdown,
    run_github_repo_intelligence_suite,
)
from code_intelligence_agent.evaluation.github_discovery_fetcher import GitHubAPIError


def test_intelligence_suite_runs_static_and_blocked_reports(tmp_path):
    raw_source = _write_average_mean(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    output_dir = tmp_path / "suite"
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "repo_intelligence_smoke_suite",
                "defaults": {
                    "source_cache_dir": str(tmp_path / "source_cache"),
                    "max_sources": 5,
                    "max_candidates": 5,
                    "auto_fallback": False,
                    "run_repository_test_command": True,
                    "repository_patch_generation_mode": "rule",
                },
                "suite_thresholds": {
                    "min_artifact_core_ready_count": 2,
                    "min_agent_answer_coverage_complete_count": 2,
                    "min_source_import_blocked_count": 1,
                    "min_phase2_static_graph_fault_localization_count": 1,
                    "min_repository_structure_modeled_count": 1,
                    "min_repo_graph_ready_count": 1,
                    "min_program_graph_available_count": 1,
                    "min_planned_repository_test_command_count": 1,
                    "min_repository_test_environment_diagnosed_count": 2,
                    "min_repository_test_setup_doctor_diagnosed_count": 2,
                    "min_repository_test_recommended_install_command_count": 1,
                    "min_fault_localization_application_candidate_run_count": 1,
                },
                "runs": [
                    {
                        "name": "static_avg",
                        "repo": "example/project",
                        "recipes": ["missing_len_zero_guard"],
                        "expected_status": "pass",
                        "expected_analysis_stage": (
                            "phase2_static_graph_fault_localization"
                        ),
                        "expected_controller_action": (
                            "run_repository_tests_with_checkout"
                        ),
                        "expected_artifact_inventory_status": "pass",
                        "expected_patch_generation_mode": "rule",
                        "expected_agent_answer_testability_status": (
                            "can_attempt_with_checkout_or_setup"
                        ),
                        "expected_agent_answer_repairability_status": (
                            "needs_dynamic_evidence_or_patch_context"
                        ),
                        "expected_repository_test_environment_status": "pass",
                        "expected_repository_test_setup_doctor_status": "blocked",
                        "expected_repository_test_setup_doctor_blocker": (
                            "checkout:full_repo_not_materialized"
                        ),
                        "metric_thresholds": {
                            "artifact_inventory_core_file_nonempty_count": 18,
                            "repository_structure_analyzed_file_count": 2,
                            "repository_structure_function_count": 2,
                            "repository_structure_total_loc": 1,
                            "repository_structure_call_site_count": 1,
                            "repo_graph_file_node_count": 2,
                            "repo_graph_function_node_count": 2,
                            "repo_graph_program_graph_available": 1,
                            "repo_graph_program_graph_node_count": 1,
                            "repo_graph_program_graph_edge_count": 1,
                            "fault_localization_rankings_with_static_rule_score_count": 1,
                            "fault_localization_rankings_with_graph_score_count": 1,
                            "fault_localization_rankings_with_source_role_score_count": 1,
                            "fault_localization_rankings_with_final_score_count": 1,
                            "fault_localization_top_static_rule_score": 0.1,
                            "fault_localization_top_graph_score": 0.1,
                            "fault_localization_top_source_role_score": 0.1,
                            "fault_localization_top_final_score": 0.1,
                            "fault_localization_ranking_count": 1,
                        },
                    },
                    {
                        "name": "blocked_no_python",
                        "repo": "example/docs",
                        "expected_status": "pass",
                        "expected_analysis_stage": "source_import_blocked",
                        "expected_blocker": "source_import_or_parse_missing",
                        "expected_controller_action": "adjust_source_filters",
                        "expected_artifact_inventory_status": "pass",
                        "expected_patch_generation_mode": "rule",
                        "expected_agent_answer_testability_status": "blocked",
                        "expected_agent_answer_repairability_status": "not_ready",
                        "expected_repository_test_environment_status": "skipped",
                        "expected_repository_test_setup_doctor_status": "blocked",
                        "expected_repository_test_setup_doctor_blocker": (
                            "profile:python_sources"
                        ),
                    },
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    opener = _FakeOpener(
        _repo_payloads(raw_source)
        + _repo_payloads_without_python()
    )

    report = run_github_repo_intelligence_suite(
        manifest_path,
        output_dir,
        opener=opener,
    )
    markdown = render_github_repo_intelligence_suite_markdown(report)

    assert report.passed is True
    assert report.summary["run_count"] == 2
    assert report.summary["expectation_passed_count"] == 2
    assert report.summary["artifact_core_ready_count"] == 2
    assert report.summary["artifact_required_ready_count"] == 2
    assert report.summary["artifact_file_checked_count"] == 2
    assert report.summary["agent_controller_loop_complete_count"] == 2
    assert report.summary["agent_answer_coverage_complete_count"] == 2
    assert report.summary["agent_answer_coverage_incomplete_count"] == 0
    assert report.summary["agent_answer_coverage_answered_total"] == 14
    assert report.summary["agent_answer_coverage_required_total"] == 14
    assert report.summary["agent_answer_missing_question_counts"] == {}
    expected_answered_questions = {
        "blocker": 2,
        "next_action": 2,
        "repairability": 2,
        "repository_structure": 2,
        "suspicious_functions": 2,
        "suspicious_reason": 2,
        "testability": 2,
    }
    assert report.summary["agent_answer_question_answered_counts"] == (
        expected_answered_questions
    )
    assert report.summary["agent_answer_question_answered_kind_count"] == 7
    assert report.summary["agent_answer_question_missing_counts"] == {}
    assert report.summary["agent_answer_question_missing_kind_count"] == 0
    for question_id, count in expected_answered_questions.items():
        assert report.summary[
            f"agent_answer_question_answered_{question_id}_count"
        ] == count
    assert report.summary["agent_answer_testability_status_counts"] == {
        "blocked": 1,
        "can_attempt_with_checkout_or_setup": 1,
    }
    assert report.summary["agent_answer_repairability_status_counts"] == {
        "needs_dynamic_evidence_or_patch_context": 1,
        "not_ready": 1,
    }
    assert report.summary["repository_structure_modeled_count"] == 1
    assert report.summary["repo_graph_ready_count"] == 1
    assert report.summary["program_graph_available_count"] == 1
    assert report.summary["fault_localization_application_candidate_count"] == 1
    assert report.summary["fault_localization_application_candidate_run_count"] == 1
    assert report.summary["fault_localization_application_candidate_runs"] == [
        "static_avg"
    ]
    assert report.summary["fault_localization_no_application_candidate_run_count"] == 0
    assert report.summary["fault_localization_non_application_top_ranked_count"] == 0
    assert report.summary["fault_localization_non_application_topk_only_count"] == 0
    assert report.summary["fault_localization_top_source_role_counts"] == {
        "application": 1
    }
    assert report.summary["fault_localization_source_role_counts"] == {
        "application": 1
    }
    assert report.summary["planned_repository_test_command_count"] == 1
    assert report.summary["planned_repository_test_command_runs"] == ["static_avg"]
    assert report.summary["planned_repository_test_level_counts"] == {"smoke": 1}
    assert report.summary["repository_test_environment_status_counts"] == {
        "pass": 1,
        "skipped": 1,
    }
    assert report.summary["repository_test_environment_diagnosed_count"] == 2
    assert report.summary["repository_test_environment_diagnosed_runs"] == [
        "static_avg",
        "blocked_no_python",
    ]
    assert report.summary["repository_test_setup_doctor_status_counts"] == {
        "blocked": 2
    }
    assert report.summary["repository_test_setup_doctor_blocker_counts"] == {
        "checkout:full_repo_not_materialized": 1,
        "profile:python_sources": 1,
    }
    assert report.summary["repository_test_setup_doctor_check_count"] == 16
    assert (
        report.summary["repository_test_setup_doctor_passed_check_count"] >= 3
    )
    assert (
        report.summary["repository_test_setup_doctor_blocked_check_count"] >= 3
    )
    assert "blocked" in report.summary[
        "repository_test_setup_doctor_check_status_counts"
    ]
    assert "pass" in report.summary[
        "repository_test_setup_doctor_check_status_counts"
    ]
    assert report.summary[
        "repository_test_setup_doctor_blocked_check_name_counts"
    ]["full_repository_checkout"] >= 1
    assert report.summary[
        "repository_test_setup_doctor_blocked_check_name_counts"
    ]["execution_plan"] >= 1
    assert report.summary["repository_test_setup_doctor_diagnosed_count"] == 2
    assert report.summary["repository_test_setup_doctor_diagnosed_runs"] == [
        "static_avg",
        "blocked_no_python",
    ]
    assert report.summary["repository_test_recommended_install_command_count"] == 1
    assert report.summary["repository_test_recommended_install_command_runs"] == [
        "static_avg"
    ]
    assert report.summary["artifact_inventory_status_counts"] == {"pass": 2}
    assert report.summary["acceptance_gate_status_counts"] == {"pass": 2}
    assert report.summary["acceptance_gate_pass_count"] == 2
    assert report.summary["acceptance_gate_pass_runs"] == [
        "static_avg",
        "blocked_no_python",
    ]
    assert report.summary[
        "acceptance_gate_repair_decision_audit_pass_count"
    ] == 2
    assert report.summary[
        "acceptance_gate_repair_decision_audit_pass_runs"
    ] == [
        "static_avg",
        "blocked_no_python",
    ]
    assert report.summary["agent_goal_readiness_status_counts"] == {"pass": 2}
    assert report.summary["agent_goal_readiness_pass_count"] == 2
    assert report.summary["agent_goal_readiness_pass_runs"] == [
        "static_avg",
        "blocked_no_python",
    ]
    assert report.summary[
        "agent_goal_repair_decision_audit_pass_count"
    ] == 2
    assert report.summary["agent_goal_repair_decision_audit_pass_runs"] == [
        "static_avg",
        "blocked_no_python",
    ]
    assert report.summary["objective_compliance_status_counts"] == {"pass": 2}
    assert report.summary["objective_compliance_pass_count"] == 2
    assert report.summary["objective_compliance_pass_runs"] == [
        "static_avg",
        "blocked_no_python",
    ]
    assert report.summary["objective_compliance_failed_section_counts"] == {}
    assert report.summary["objective_compliance_failed_section_kind_count"] == 0
    assert report.summary["objective_compliance_section_warning_counts"] == {}
    assert report.summary["objective_compliance_section_pass_counts"] == {
        "agent_controller_and_auditable_reports": 2,
        "github_input_checkout_and_cache": 2,
        "patch_validation_and_reflection": 2,
        "repo_understanding_and_graph_modeling": 2,
        "static_signals_and_topk_localization": 2,
        "test_diagnosis_and_dynamic_evidence": 2,
    }
    assert report.summary["analysis_stage_counts"] == {
        "phase2_static_graph_fault_localization": 1,
        "source_import_blocked": 1,
    }
    assert report.summary["controller_action_counts"] == {
        "adjust_source_filters": 1,
        "run_repository_tests_with_checkout": 1,
    }
    assert report.summary["repository_patch_generation_mode_counts"] == {"rule": 2}
    assert report.summary["repository_llm_patch_generation_status_counts"] == {
        "disabled": 2
    }
    assert report.summary["repository_patch_safety_gate_status_counts"] == {
        "skipped": 2
    }
    assert report.summary["execution_profile_counts"] == {"static": 2}
    assert report.summary["suite_threshold_failed_count"] == 0
    assert (output_dir / "github_repo_intelligence_suite.json").exists()
    assert (output_dir / "github_repo_intelligence_suite.md").exists()
    assert "GitHub Repo Intelligence Suite" in markdown
    assert "Artifact Required Ready Runs: 2" in markdown
    assert "Acceptance Gate Pass Runs: 2" in markdown
    assert "Acceptance Repair Decision Audit Runs: 2" in markdown
    assert "Agent Goal Readiness Pass Runs: 2" in markdown
    assert "Agent Goal Repair Decision Audit Runs: 2" in markdown
    assert "Objective Compliance Pass Runs: 2" in markdown
    assert "Agent Controller Loop Complete Runs: 2" in markdown
    assert "Agent Answer Complete Runs: 2" in markdown
    assert "Repository Structure Modeled Runs: 1" in markdown
    assert "Repo Graph Ready Runs: 1" in markdown
    assert "Program Graph Available Runs: 1" in markdown
    assert "Fault Localization Application Candidate Runs: 1" in markdown
    assert "Fault Localization No Application Candidate Runs: 0" in markdown
    assert "Fault Localization Non-Application Top Runs: 0" in markdown
    assert "Fault Localization Non-Application-Only Top-k Runs: 0" in markdown
    assert "Planned Repository Test Command Runs: 1" in markdown
    assert "Repository Test Environment Diagnosed Runs: 2" in markdown
    assert "Repository Test Setup Doctor Diagnosed Runs: 2" in markdown
    assert "Repository Test Setup Doctor Checks:" in markdown
    assert "Recommended Install Command Runs: 1" in markdown
    assert "Planned Repository Test Levels: smoke=1" in markdown
    assert "Repository Test Environment Statuses: pass=1, skipped=1" in markdown
    assert "Repository Test Setup Doctor Statuses: blocked=2" in markdown
    assert (
        "Repository Test Setup Doctor Blockers: "
        "checkout:full_repo_not_materialized=1, profile:python_sources=1"
    ) in markdown
    assert "Repository Test Setup Doctor Check Statuses:" in markdown
    assert "Repository Test Setup Doctor Blocked Checks:" in markdown
    assert (
        "Agent Answer Testability Statuses: "
        "blocked=1, can_attempt_with_checkout_or_setup=1"
    ) in markdown
    assert (
        "Agent Answer Repairability Statuses: "
        "needs_dynamic_evidence_or_patch_context=1, not_ready=1"
    ) in markdown
    assert "Agent Answered Questions: blocker=2, next_action=2" in markdown
    assert "Agent Missing Questions: none" in markdown
    assert "Missing Agent Answer Questions: none" in markdown
    assert "blocked_no_python" in markdown
    assert "source_import_blocked" in markdown
    assert "Patch Generation Modes: rule=2" in markdown
    assert "LLM Patch Generation Statuses: disabled=2" in markdown
    assert "Patch Safety Gate Statuses: skipped=2" in markdown
    assert "Fault Localization Top Source Roles: application=1" in markdown
    assert "Fault Localization Source Roles: application=1" in markdown
    assert "Execution Profiles: static=2" in markdown
    assert "Acceptance Gate Statuses: pass=2" in markdown
    assert "Agent Goal Readiness Statuses: pass=2" in markdown
    assert "Objective Compliance Statuses: pass=2" in markdown
    assert (
        "Objective Compliance Section Passes: "
        "agent_controller_and_auditable_reports=2"
    ) in markdown
    assert "Objective Compliance Section Warnings: none" in markdown
    assert "Objective Compliance Failed Sections: none" in markdown

    for run in report.runs:
        report_path = Path(run.report_path)
        assert report_path.exists()
        saved = json.loads(report_path.read_text(encoding="utf-8"))
        assert saved["artifact_inventory"]["reason"] == "core_artifacts_written"
        assert saved["artifact_inventory"]["required_status"] == "pass"
        assert saved["artifact_inventory"]["missing_required_artifacts"] == []
        assert saved["acceptance_gate"]["status"] == "pass"
        assert saved["acceptance_gate"]["failed_checks"] == []
        assert saved["agent_goal_readiness"]["status"] == "pass"
        assert saved["agent_goal_readiness"]["failed_criteria"] == []
        assert saved["final_report"]["objective_compliance"]["status"] == "pass"
        assert saved["final_report"]["objective_compliance"]["failed_sections"] == []
        assert run.metrics["acceptance_gate_status"] == "pass"
        assert run.metrics["acceptance_gate_passed"] is True
        assert run.metrics["acceptance_gate_failed_check_count"] == 0
        assert run.metrics["acceptance_gate_repair_decision_audit_passed"] is True
        assert run.metrics["agent_goal_readiness_status"] == "pass"
        assert run.metrics["agent_goal_readiness_passed"] is True
        assert run.metrics["agent_goal_readiness_failed_criteria_count"] == 0
        assert run.metrics["agent_goal_repair_decision_audit_passed"] is True
        assert run.metrics["objective_compliance_status"] == "pass"
        assert run.metrics["objective_compliance_passed"] is True
        assert run.metrics["objective_compliance_failed_section_count"] == 0
        assert run.metrics["objective_compliance_failed_sections"] == []
        assert run.metrics["objective_compliance_passed_section_count"] == 6
        assert run.metrics["objective_compliance_section_count"] == 6
        acceptance_checks = {
            item["name"]: item for item in saved["acceptance_gate"]["checks"]
        }
        goal_criteria = {
            item["name"]: item
            for item in saved["agent_goal_readiness"]["criteria"]
        }
        assert acceptance_checks["repair_decision_audit"]["passed"] is True
        assert goal_criteria["repair_decision_audit"]["passed"] is True
        assert saved["agent_answers"]["artifact_inventory"]["core_ready"] is True
        assert saved["agent_answers"]["artifact_inventory"]["required_ready"] is True
        assert saved["agent_answers"]["answer_coverage_complete"] is True
        assert saved["agent_answers"]["answer_coverage_answered_count"] == 7
        assert run.metrics["agent_answer_coverage_complete"] is True
        assert run.metrics["agent_answer_coverage_answered_count"] == 7
        assert run.metrics["agent_answer_coverage_required_count"] == 7
        assert run.metrics["agent_answer_coverage_missing_questions"] == []
        assert run.metrics["agent_answer_question_statuses"] == {
            "blocker": "answered",
            "next_action": "answered",
            "repairability": "answered",
            "repository_structure": "answered",
            "suspicious_functions": "answered",
            "suspicious_reason": "answered",
            "testability": "answered",
        }
        assert run.metrics["agent_answer_testability_status"] == saved[
            "agent_answers"
        ]["testability"]["status"]
        assert run.metrics["agent_answer_repairability_status"] == saved[
            "agent_answers"
        ]["repairability"]["status"]
        assert run.metrics["repository_structure_analyzed_file_count"] == saved[
            "repository_structure"
        ]["analyzed_file_count"]
        assert run.metrics["repo_graph_file_node_count"] == saved["repo_graph"][
            "file_node_count"
        ]
        assert run.metrics["repo_graph_program_graph_available"] is bool(
            saved["repo_graph"]["program_graph"]["available"]
        )
        assert run.metrics["repository_test_environment_status"] == saved[
            "repository_test_environment_status"
        ]
        assert run.metrics["repository_test_environment_reason"] == saved[
            "repository_test_environment_reason"
        ]
        assert run.metrics["repository_test_setup_doctor_status"] == saved[
            "repository_test_setup_doctor_status"
        ]
        assert run.metrics["repository_test_setup_doctor_blocker"] == saved[
            "repository_test_setup_doctor_blocker"
        ]
        assert run.metrics["repository_test_setup_doctor_check_count"] == saved[
            "repository_test_setup_doctor_check_count"
        ]
        assert run.metrics[
            "repository_test_setup_doctor_check_status_counts"
        ] == saved["repository_test_setup_doctor_check_status_counts"]
        assert run.metrics[
            "repository_test_setup_doctor_blocked_check_names"
        ] == saved["repository_test_setup_doctor_blocked_check_names"]
        assert run.metrics["recommended_install_command"] == saved[
            "recommended_install_command"
        ]
        if run.metrics["fault_localization_ranking_count"]:
            top = saved["fault_localization"]["rankings"][0]
            fault = saved["fault_localization"]
            assert run.metrics["fault_localization_top_source_role"] == fault[
                "top_source_role"
            ]
            assert run.metrics["fault_localization_application_candidate_count"] == fault[
                "application_candidate_count"
            ]
            assert run.metrics["fault_localization_source_role_counts"] == fault[
                "source_role_counts"
            ]
            assert run.metrics["fault_localization_top_static_rule_score"] == top[
                "static_rule_score"
            ]
            assert run.metrics["fault_localization_top_graph_score"] == top[
                "graph_score"
            ]
            assert run.metrics["fault_localization_top_source_role_score"] == top[
                "source_role_score"
            ]
            assert run.metrics["fault_localization_top_final_score"] == top[
                "final_score"
            ]
            assert (
                run.metrics[
                    "fault_localization_rankings_with_static_rule_score_count"
                ]
                >= 1
            )
            assert (
                run.metrics["fault_localization_rankings_with_graph_score_count"]
                >= 1
            )
            assert (
                run.metrics[
                    "fault_localization_rankings_with_source_role_score_count"
                ]
                >= 1
            )
            assert (
                run.metrics["fault_localization_rankings_with_final_score_count"]
                >= 1
            )
        assert run.metrics["agent_controller_loop_complete"] is True
        assert run.metrics["agent_controller_loop_phase_count"] == 6
        assert run.metrics["agent_controller_decision_trace_phase_count"] == 6
        assert run.metrics["agent_controller_observation_count"] > 0
        assert run.metrics["agent_controller_plan_step_count"] > 0
        assert run.metrics["repository_patch_generation_mode"] == "rule"
        assert run.metrics["repository_llm_patch_generation_status"] == "disabled"
        assert run.metrics["repository_patch_safety_gate_status"] == "skipped"
        assert run.metrics["artifact_inventory_required_file_checked_count"] == (
            run.metrics["artifact_inventory_required_count"]
        )
        assert run.metrics["artifact_inventory_required_file_nonempty_count"] == (
            run.metrics["artifact_inventory_required_count"]
        )
        assert run.metrics["artifact_inventory_required_file_nonempty_count"] >= (
            run.metrics["artifact_inventory_core_file_nonempty_count"]
        )
        assert saved["agent_controller"]["control_loop"] == [
            "observe",
            "plan",
            "act",
            "verify",
            "reflect",
            "replan",
        ]


def test_intelligence_suite_gates_reflection_repair_loop(tmp_path, monkeypatch):
    raw_source = _write_shift_left(tmp_path)
    manifest_path = tmp_path / "reflection_manifest.json"
    output_dir = tmp_path / "suite"
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "repo_intelligence_reflection_suite",
                "defaults": {
                    "execution_profile": "agent-auto",
                    "max_sources": 5,
                    "max_candidates": 5,
                    "auto_fallback": False,
                    "repository_patch_generation_mode": "rule",
                    "repository_test_timeout": 10,
                    "auto_controller_max_actions": 2,
                    "repository_test_reflection_mode": "rule",
                    "repository_test_reflection_rounds": 1,
                    "repository_test_reflection_width": 1,
                },
                "suite_thresholds": {
                    "max_command_failed_count": 0,
                    "max_expectation_failed_count": 0,
                    "max_metric_check_failed_count": 0,
                    "min_agent_answer_coverage_complete_count": 1,
                    "min_agent_auto_complete_loop_count": 1,
                    "min_repository_test_repair_ready_count": 1,
                    "min_repository_test_patch_validation_reflection_candidate_count": 1,
                    "min_repository_test_patch_validation_successful_reflection_count": 1,
                    "min_reflection_initial_failure_type_kind_count": 1,
                    "min_reflection_parent_failure_type_kind_count": 1,
                    "min_successful_reflection_parent_failure_type_kind_count": 1,
                    "min_agent_auto_reflection_candidate_action_count": 1,
                    "min_agent_auto_successful_reflection_action_count": 1,
                    "min_agent_auto_reflection_goal_reached_count": 1,
                },
                "runs": [
                    {
                        "name": "reflection_shift_left",
                        "repo": "example/project",
                        "recipes": ["possible_index_overrun"],
                        "expected_status": "pass",
                        "expected_execution_profile": "agent-auto",
                        "expected_patch_generation_mode": "rule",
                        "expected_patch_validation_status": "pass",
                        "metric_thresholds": {
                            "repository_test_patch_validation_success_count": 1,
                            "repository_test_patch_validation_reflection_candidate_count": 1,
                            "repository_test_patch_validation_successful_reflection_count": 1,
                            "reflection_initial_failure_type_kind_count": 1,
                            "reflection_parent_failure_type_kind_count": 1,
                            "successful_reflection_parent_failure_type_kind_count": 1,
                            "agent_auto_reflection_candidate_action_count": 1,
                            "agent_auto_successful_reflection_action_count": 1,
                        },
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    opener = _FakeOpener(_shift_left_repo_payloads(raw_source) * 2)

    def fake_checkout_github_repository(**kwargs):
        checkout_root = Path(kwargs["output_dir"]) / "repository_checkout"
        (checkout_root / "tests").mkdir(parents=True, exist_ok=True)
        (checkout_root / "sample.py").write_text(
            raw_source.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (checkout_root / "tests" / "test_sample.py").write_text(
            "from sample import shift_left\n\n"
            "def test_shift_left():\n"
            "    assert shift_left([1, 2, 3]) == [2, 3]\n",
            encoding="utf-8",
        )
        return {
            "status": "pass",
            "reason": "fake_checkout_created",
            "message": "Fake checkout created for reflection suite test.",
            "checkout_path": str(checkout_root),
            "checkout_method": "fake",
            "owner": kwargs["owner"],
            "repo": kwargs["repo"],
            "ref": kwargs.get("ref"),
        }

    monkeypatch.setattr(
        onboarding_module,
        "checkout_github_repository",
        fake_checkout_github_repository,
    )
    original_generate = PatchGenerator.generate

    def conservative_only_generate(self, *args, **kwargs):
        candidates = original_generate(self, *args, **kwargs)
        conservative = [
            candidate
            for candidate in candidates
            if candidate.metadata.get("variant") == "overly_conservative_range_bound"
        ]
        return conservative or candidates

    monkeypatch.setattr(PatchGenerator, "generate", conservative_only_generate)

    report = run_github_repo_intelligence_suite(
        manifest_path,
        output_dir,
        opener=opener,
    )
    markdown = render_github_repo_intelligence_suite_markdown(report)

    assert report.passed is True
    assert report.summary["agent_answer_coverage_complete_count"] == 1
    assert report.summary["agent_auto_complete_loop_count"] == 1
    assert report.summary["repository_test_repair_ready_count"] == 1
    assert report.summary[
        "repository_test_patch_validation_reflection_candidate_count"
    ] == 1
    assert report.summary[
        "repository_test_patch_validation_successful_reflection_count"
    ] == 1
    assert report.summary["repository_test_patch_validation_failure_type_counts"] == {
        "success": 1,
        "test_failure": 1,
    }
    assert report.summary["reflection_initial_failure_type_counts"] == {
        "test_failure": 1
    }
    assert report.summary["reflection_parent_failure_type_counts"] == {
        "test_failure": 1
    }
    assert report.summary["successful_reflection_parent_failure_type_counts"] == {
        "test_failure": 1
    }
    assert report.summary["agent_auto_reflection_candidate_action_count"] == 1
    assert report.summary["agent_auto_successful_reflection_action_count"] == 1
    assert report.summary["agent_auto_reflection_goal_reached_count"] == 1
    assert report.summary["suite_threshold_failed_count"] == 0
    assert "Repository Test Reflection Successes: 1" in markdown
    assert "Reflection Initial Failure Types: test_failure=1" in markdown
    assert "Successful Reflection Parent Failure Types: test_failure=1" in markdown
    assert "Patch Validation Failure Types: success=1, test_failure=1" in markdown
    assert "Reflection Parent Failure Types: test_failure=1" in markdown
    assert "Agent Auto Reflection Candidate Actions: 1" in markdown
    assert "Agent Auto Successful Reflection Actions: 1" in markdown
    assert "Agent Auto Reflection Goal Reached Runs: 1" in markdown
    assert "reflection_shift_left" in markdown
    assert "1/1" in markdown

    run = report.runs[0]
    assert run.metrics[
        "repository_test_patch_validation_successful_reflection_count"
    ] == 1
    assert run.metrics["repository_test_patch_validation_failure_type_counts"] == {
        "success": 1,
        "test_failure": 1,
    }
    assert run.metrics["reflection_initial_failure_type_counts"] == {
        "test_failure": 1
    }
    assert run.metrics["reflection_parent_failure_type_counts"] == {
        "test_failure": 1
    }
    assert run.metrics["successful_reflection_parent_failure_type_counts"] == {
        "test_failure": 1
    }
    assert run.metrics["reflection_failure_type_counts"] == {"success": 1}
    assert run.metrics["agent_auto_reflection_candidate_action_count"] == 1
    assert run.metrics["agent_auto_successful_reflection_action_count"] == 1
    assert run.metrics["agent_auto_reflection_goal_reached"] is True
    assert run.metrics["agent_answer_coverage_complete"] is True
    saved = json.loads(Path(run.report_path).read_text(encoding="utf-8"))
    assert saved["reflection_summary"]["reason"] == "reflection_repaired_candidate"
    assert saved["reflection_summary"]["best_depth"] == 1
    assert saved["reflection_summary"]["initial_failure_type_counts"] == {
        "test_failure": 1
    }
    assert saved["reflection_summary"][
        "successful_reflection_parent_failure_type_counts"
    ] == {"test_failure": 1}
    assert saved["agent_auto_actions"][0]["action_id"] == (
        "run_repository_tests_with_checkout"
    )
    assert saved["agent_auto_actions"][0]["after_reflection_trace_reason"] == (
        "reflection_repaired_candidate"
    )


def test_intelligence_suite_runs_unittest_dynamic_evidence_path(
    tmp_path,
    monkeypatch,
):
    source_path, test_path = _write_unittest_repo(tmp_path)
    manifest_path = tmp_path / "unittest_manifest.json"
    output_dir = tmp_path / "suite"
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "repo_intelligence_unittest_suite",
                "defaults": {
                    "execution_profile": "checkout",
                    "max_sources": 10,
                    "max_candidates": 5,
                    "auto_fallback": False,
                    "repository_patch_generation_mode": "rule",
                    "repository_test_timeout": 10,
                    "repository_checkout_depth": 1,
                },
                "suite_thresholds": {
                    "max_command_failed_count": 0,
                    "max_expectation_failed_count": 0,
                    "max_metric_check_failed_count": 0,
                    "min_agent_answer_coverage_complete_count": 1,
                    "min_test_executable_run_count": 1,
                    "min_planned_repository_test_runner_kind_count": 1,
                    "min_repository_test_dynamic_evidence_level_kind_count": 1,
                    "min_fault_localization_matched_failed_test_count": 1,
                    "min_fault_localization_matched_traceback_frame_count": 1,
                },
                "runs": [
                    {
                        "name": "unittest_failure_repo",
                        "repo": "example/unittest-project",
                        "expected_status": "fail",
                        "expected_execution_profile": "checkout",
                        "expected_planned_repository_test_runner": "unittest",
                        "expected_dynamic_evidence_level": "failing_tests",
                        "expected_patch_generation_mode": "rule",
                        "metric_thresholds": {
                            "artifact_inventory_core_file_nonempty_count": 18,
                            "fault_localization_ranking_count": 1,
                        },
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    opener = _FakeOpener(_unittest_repo_payloads(source_path, test_path))

    def fake_checkout_github_repository(**kwargs):
        checkout_root = Path(kwargs["output_dir"]) / "repository_checkout"
        (checkout_root / "tests").mkdir(parents=True, exist_ok=True)
        (checkout_root / "sample.py").write_text(
            source_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (checkout_root / "tests" / "test_sample.py").write_text(
            test_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        return {
            "status": "pass",
            "reason": "fake_checkout_created",
            "message": "Fake checkout created for unittest suite test.",
            "checkout_path": str(checkout_root),
            "checkout_method": "fake",
            "owner": kwargs["owner"],
            "repo": kwargs["repo"],
            "ref": kwargs.get("ref"),
        }

    monkeypatch.setattr(
        onboarding_module,
        "checkout_github_repository",
        fake_checkout_github_repository,
    )

    report = run_github_repo_intelligence_suite(
        manifest_path,
        output_dir,
        opener=opener,
    )
    markdown = render_github_repo_intelligence_suite_markdown(report)

    assert report.passed is True
    assert report.summary["planned_repository_test_runner_counts"] == {"unittest": 1}
    assert report.summary["repository_test_dynamic_evidence_level_counts"] == {
        "failing_tests": 1
    }
    assert report.summary["planned_repository_test_result_status_counts"] == {
        "fail": 1
    }
    assert report.summary["repository_test_count_source_counts"] == {
        "unittest_summary": 1
    }
    assert report.summary["repository_test_execution_result_count"] == 1
    assert report.summary["repository_test_counted_run_count"] == 1
    assert report.summary["repository_test_count"] == 1
    assert report.summary["repository_test_failed_count"] == 1
    assert report.summary["fault_localization_matched_failed_test_count"] >= 1
    assert report.summary["fault_localization_matched_traceback_frame_count"] >= 1
    assert report.summary["fault_localization_traceback_frame_count"] == (
        report.summary["fault_localization_matched_traceback_frame_count"]
        + report.summary["fault_localization_unmatched_traceback_frame_count"]
    )
    assert report.summary["test_executable_run_count"] == 1
    assert report.summary["agent_answer_coverage_complete_count"] == 1
    assert "Planned Repository Test Runners: unittest=1" in markdown
    assert "Repository Test Counted Runs: 1" in markdown
    assert "Dynamic Evidence Levels: failing_tests=1" in markdown


    assert "Fault Localization Dynamic Matches:" in markdown

    run = report.runs[0]
    assert run.metrics["planned_repository_test_runner"] == "unittest"
    assert run.metrics["planned_repository_test_result_status"] == "fail"
    assert run.metrics["planned_repository_test_result_test_count"] == 1
    assert run.metrics["planned_repository_test_result_failed"] == 1
    assert (
        run.metrics["planned_repository_test_result_test_count_source"]
        == "unittest_summary"
    )
    assert run.metrics["repository_test_dynamic_evidence_level"] == "failing_tests"
    assert run.metrics["fault_localization_mode"] == "dynamic"
    assert run.metrics["fault_localization_matched_failed_test_count"] >= 1
    assert run.metrics["fault_localization_matched_traceback_frame_count"] >= 1
    assert run.metrics["fault_localization_traceback_frame_count"] == (
        run.metrics["fault_localization_matched_traceback_frame_count"]
        + run.metrics["fault_localization_unmatched_traceback_frame_count"]
    )
    saved = json.loads(Path(run.report_path).read_text(encoding="utf-8"))
    execution_plan = json.loads(
        Path(saved["repository_test_execution_plan_json"]).read_text(encoding="utf-8")
    )
    execution_result = json.loads(
        Path(saved["repository_test_execution_result_json"]).read_text(encoding="utf-8")
    )
    dynamic_evidence = json.loads(
        Path(saved["repository_test_dynamic_evidence_json"]).read_text(encoding="utf-8")
    )
    fault_localization = json.loads(
        Path(saved["fault_localization_json"]).read_text(encoding="utf-8")
    )
    assert execution_plan["recommended_execution_runner"] == "unittest"
    assert execution_result["execution_runner"] == "unittest"
    assert execution_result["test_count"] == 1
    assert execution_result["failed"] == 1
    assert execution_result["test_count_source"] == "unittest_summary"
    assert dynamic_evidence["failing_tests"][0]["nodeid"].endswith(
        "test_sample.py::ShiftLeftTest::test_shift_left_short_values"
    )
    assert dynamic_evidence["traceback_frame_count"] >= 1
    assert fault_localization["top_function"] == "shift_left"
    assert fault_localization["matched_failed_test_count"] >= 1
    assert fault_localization["matched_traceback_frame_count"] >= 1
    assert fault_localization["traceback_frame_count"] == (
        fault_localization["matched_traceback_frame_count"]
        + fault_localization["unmatched_traceback_frame_count"]
    )
    assert saved["agent_answers"]["testability"]["status"] == "tests_failed"


def test_intelligence_suite_forwards_patch_judge_mode_to_direct_runner(
    tmp_path,
    monkeypatch,
):
    manifest_path = tmp_path / "manifest.json"
    output_dir = tmp_path / "suite"
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "patch_judge_forwarding_suite",
                "defaults": {
                    "execution_profile": "phase3-fast",
                    "repository_patch_generation_mode": "llm",
                    "repository_test_reflection_mode": "llm",
                    "patch_judge_mode": "llm",
                    "run_repository_test_command": True,
                    "auto_fallback": False,
                },
                "runs": [
                    {
                        "name": "llm_judge_forwarded",
                        "repo": "example/project",
                        "expected_status": "pass",
                        "expected_patch_judge_mode": "llm",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    captured = {}

    def fake_run_github_repo_intelligence(repo_spec, output_dir_arg, **kwargs):
        captured.update(kwargs)
        return suite_module.GitHubRepoAgentReport(
            repo_spec=repo_spec,
            owner="example",
            repo="project",
            output_dir=str(output_dir_arg),
            preset=str(kwargs.get("preset") or ""),
            status="pass",
            summary={},
            output_paths={},
            onboarding_report={},
        )

    def fake_summary(report):
        return {
            "repo": report.repo_spec,
            "repo_spec": report.repo_spec,
            "output_dir": report.output_dir,
            "status": "pass",
            "passed": True,
            "intelligence_json": str(
                Path(report.output_dir) / "github_repo_intelligence.json"
            ),
            "repository_test_patch_judge_mode": "llm",
            "repository_test_patch_judge_status": "ready",
            "repository_test_patch_judge_candidate_count": 1,
            "repository_test_patch_judge_authority": (
                "sandbox_pytest_decides_success"
            ),
        }

    monkeypatch.setattr(
        suite_module,
        "run_github_repo_intelligence",
        fake_run_github_repo_intelligence,
    )
    monkeypatch.setattr(
        suite_module,
        "github_repo_intelligence_summary",
        fake_summary,
    )
    monkeypatch.setattr(
        suite_module,
        "write_github_repo_intelligence_artifacts",
        lambda report, summary: None,
    )

    report = run_github_repo_intelligence_suite(manifest_path, output_dir)
    expectation_checks = {
        check["name"]: check for check in report.runs[0].expectation_checks
    }

    assert captured["patch_judge_mode"] == "llm"
    assert report.passed is True
    assert report.runs[0].metrics["repository_test_patch_judge_mode"] == "llm"
    assert expectation_checks["patch_judge_mode"]["passed"] is True


def test_intelligence_suite_example_manifest_exposes_agent_audit_controls():
    manifest = json.loads(
        Path("datasets/github_cases/repo_intelligence_suite.example.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["suite_name"] == "repo_intelligence_smoke_suite"
    assert manifest["defaults"]["preset"] == "mining"
    assert manifest["defaults"]["source_cache_dir"] == (
        "outputs_smoke/github_raw_source_cache"
    )
    assert manifest["defaults"]["run_repository_test_command"] is True
    assert manifest["defaults"]["repository_patch_generation_mode"] == "rule"
    assert manifest["suite_thresholds"]["max_command_failed_count"] == 0
    assert manifest["suite_thresholds"]["min_artifact_core_ready_count"] == 3
    assert manifest["suite_thresholds"][
        "min_agent_controller_loop_complete_count"
    ] == 3
    assert manifest["suite_thresholds"][
        "min_agent_decision_timeline_complete_count"
    ] == 3
    assert manifest["suite_thresholds"][
        "min_agent_answer_coverage_complete_count"
    ] == 3
    assert manifest["suite_thresholds"][
        "min_agent_answer_testability_status_kind_count"
    ] == 2
    assert manifest["suite_thresholds"][
        "min_agent_answer_repairability_status_kind_count"
    ] == 2
    assert manifest["suite_thresholds"]["min_repository_structure_modeled_count"] == 2
    assert manifest["suite_thresholds"]["min_repo_graph_ready_count"] == 2
    assert manifest["suite_thresholds"]["min_program_graph_available_count"] == 2
    assert manifest["suite_thresholds"][
        "min_planned_repository_test_command_count"
    ] == 2
    assert manifest["suite_thresholds"][
        "min_repository_test_environment_diagnosed_count"
    ] == 2
    assert manifest["suite_thresholds"][
        "min_repository_test_setup_doctor_diagnosed_count"
    ] == 2
    assert manifest["suite_thresholds"][
        "min_repository_test_recommended_install_command_count"
    ] == 2
    assert manifest["suite_thresholds"]["min_source_import_blocked_count"] == 1
    assert manifest["runs"][0]["expected_controller_action"] == (
        "run_repository_tests_with_checkout"
    )
    assert manifest["runs"][0]["expected_patch_generation_mode"] == "rule"
    assert manifest["runs"][0]["expected_agent_answer_testability_status"] == (
        "can_attempt_with_checkout_or_setup"
    )
    assert manifest["runs"][0]["expected_agent_answer_repairability_status"] == (
        "needs_dynamic_evidence_or_patch_context"
    )
    assert manifest["runs"][0]["expected_repository_test_environment_status"] == (
        "warning"
    )
    assert manifest["runs"][0]["expected_repository_test_setup_doctor_status"] == (
        "blocked"
    )
    assert manifest["runs"][0]["expected_repository_test_setup_doctor_blocker"] == (
        "checkout:full_repo_not_materialized"
    )
    assert manifest["runs"][1]["expected_repository_test_environment_status"] == (
        "warning"
    )
    assert manifest["runs"][1]["expected_repository_test_setup_doctor_status"] == (
        "blocked"
    )
    assert manifest["runs"][1]["expected_repository_test_setup_doctor_blocker"] == (
        "checkout:full_repo_not_materialized"
    )
    assert manifest["runs"][2]["expected_status"] == "pass"
    assert manifest["runs"][2]["expected_analysis_stage"] == "source_import_blocked"
    assert manifest["runs"][2]["expected_controller_action"] == (
        "adjust_source_filters"
    )
    assert manifest["runs"][2]["expected_agent_answer_testability_status"] == (
        "blocked"
    )
    assert manifest["runs"][2]["expected_agent_answer_repairability_status"] == (
        "not_ready"
    )
    assert manifest["runs"][0]["metric_thresholds"][
        "artifact_inventory_required_file_nonempty_count"
    ] == 26
    assert manifest["runs"][0]["metric_thresholds"][
        "repository_structure_analyzed_file_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "repo_graph_program_graph_available"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "repository_test_pytest_config_source_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "repository_test_ci_test_command_candidate_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "fault_localization_rankings_with_static_rule_score_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "fault_localization_rankings_with_graph_score_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "fault_localization_rankings_with_source_role_score_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "fault_localization_rankings_with_final_score_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "fault_localization_top_final_score"
    ] == 0.1
    assert manifest["runs"][1]["metric_thresholds"][
        "repository_test_ci_install_command_candidate_count"
    ] == 1
    assert manifest["runs"][1]["metric_thresholds"][
        "repository_test_ci_test_command_candidate_count"
    ] == 1
    assert manifest["runs"][2]["metric_thresholds"][
        "artifact_inventory_required_file_nonempty_count"
    ] == 26


def test_intelligence_suite_metric_snapshot_reads_environment_artifact(tmp_path):
    environment_path = tmp_path / "repository_test_environment.json"
    environment_path.write_text(
        json.dumps(
            {
                "pytest_config_source_count": 2,
                "pytest_configuration": {
                    "source_count": 2,
                    "environment_variable_names": ["PYTEST_ADDOPTS"],
                },
                "ci_install_command_candidates": [
                    "python -m pip install pytest"
                ],
                "ci_test_command_candidates": ["python -m pytest tests"],
            }
        ),
        encoding="utf-8",
    )
    execution_result_path = tmp_path / "repository_test_execution_result.json"
    execution_result_path.write_text(
        json.dumps(
            {
                "status": "pass",
                "passed": 3,
                "failed": 0,
                "errors": 0,
                "skipped": 1,
                "test_count": 4,
                "test_count_source": "pytest_summary",
            }
        ),
        encoding="utf-8",
    )

    metrics = _suite_metric_snapshot(
        {
            "repository_test_environment_json": str(environment_path),
            "repository_test_execution_result_json": str(execution_result_path),
            "repository_test_environment_status": "warning",
            "repository_test_setup_doctor_status": "blocked",
            "planned_repository_test_preferred_runner": "tox",
            "planned_repository_test_runner": "pytest",
            "planned_repository_test_failure_context_line_count": 7,
            "planned_repository_test_runner_fallback_used": True,
            "planned_repository_test_runner_fallback_reason": "missing_runner:tox",
            "planned_repository_test_runner_fallback_from": "tox",
            "planned_repository_test_runner_fallback_to": "pytest",
            "recommended_install_command": "python -m pip install tox",
            "repository_test_dynamic_traceback_frames": 2,
            "repository_structure": {
                "test_structure": {
                    "test_framework_signals": ["pytest", "tox"],
                    "test_command_candidate_count": 2,
                    "test_command_runner_counts": {"pytest": 1, "tox": 1},
                    "test_command_runner_kind_count": 2,
                }
            },
        }
    )

    assert metrics["repository_test_environment_diagnosed"] is True
    assert metrics["repository_test_setup_doctor_diagnosed"] is True
    assert metrics["repository_test_pytest_config_source_count"] == 2
    assert metrics["repository_test_ci_install_command_candidate_count"] == 1
    assert metrics["repository_test_ci_test_command_candidate_count"] == 1
    assert metrics["planned_repository_test_environment_variable_count"] == 1
    assert metrics["repository_test_framework_signals"] == ["pytest", "tox"]
    assert metrics["repository_test_framework_signal_count"] == 2
    assert metrics["repository_test_command_candidate_count"] == 2
    assert metrics["repository_test_command_candidate_runner_counts"] == {
        "pytest": 1,
        "tox": 1,
    }
    assert metrics["repository_test_command_candidate_runner_kind_count"] == 2
    assert metrics["planned_repository_test_preferred_runner"] == "tox"
    assert metrics["planned_repository_test_runner"] == "pytest"
    assert metrics["planned_repository_test_failure_context_line_count"] == 7
    assert metrics["planned_repository_test_runner_fallback_used"] is True
    assert metrics["planned_repository_test_runner_fallback_reason"] == (
        "missing_runner:tox"
    )
    assert metrics["planned_repository_test_runner_fallback_from"] == "tox"
    assert metrics["planned_repository_test_runner_fallback_to"] == "pytest"
    assert metrics["recommended_install_command"] == "python -m pip install tox"
    assert metrics["recommended_install_command_present"] is True
    assert metrics["planned_repository_test_result_status"] == "pass"
    assert metrics["planned_repository_test_result_passed"] == 3
    assert metrics["planned_repository_test_result_failed"] == 0
    assert metrics["planned_repository_test_result_errors"] == 0
    assert metrics["planned_repository_test_result_skipped"] == 1
    assert metrics["planned_repository_test_result_test_count"] == 4
    assert (
        metrics["planned_repository_test_result_test_count_source"]
        == "pytest_summary"
    )
    assert metrics["repository_test_dynamic_traceback_frames"] == 2


def test_intelligence_suite_summarizes_repository_test_counts():
    summary = _suite_summary(
        [
            GitHubRepoIntelligenceSuiteRunResult(
                name="pytest_repo",
                repo="example/pytest-repo",
                output_dir="out/pytest_repo",
                report_path="out/pytest_repo/github_repo_intelligence.json",
                status="pass",
                passed=True,
                expected_status="pass",
                expectation_passed=True,
                metrics={
                    "status": "pass",
                    "planned_repository_test_result_status": "pass",
                    "planned_repository_test_result_passed": 3,
                    "planned_repository_test_result_failed": 0,
                    "planned_repository_test_result_errors": 0,
                    "planned_repository_test_result_skipped": 1,
                    "planned_repository_test_result_test_count": 4,
                    "planned_repository_test_result_test_count_source": (
                        "pytest_summary"
                    ),
                },
                metric_checks=[],
                expectation_checks=[],
                command_args=[],
            ),
            GitHubRepoIntelligenceSuiteRunResult(
                name="unittest_repo",
                repo="example/unittest-repo",
                output_dir="out/unittest_repo",
                report_path="out/unittest_repo/github_repo_intelligence.json",
                status="fail",
                passed=False,
                expected_status="fail",
                expectation_passed=True,
                metrics={
                    "status": "fail",
                    "planned_repository_test_result_status": "fail",
                    "planned_repository_test_result_passed": 1,
                    "planned_repository_test_result_failed": 1,
                    "planned_repository_test_result_errors": 1,
                    "planned_repository_test_result_skipped": 0,
                    "planned_repository_test_result_test_count": 3,
                    "planned_repository_test_result_test_count_source": (
                        "unittest_summary"
                    ),
                },
                metric_checks=[],
                expectation_checks=[],
                command_args=[],
            ),
        ],
        suite_thresholds={
            "min_repository_test_execution_result_count": 2,
            "min_repository_test_counted_run_count": 2,
            "min_repository_test_count": 7,
            "min_repository_test_passed_count": 4,
            "min_repository_test_failed_count": 1,
            "min_repository_test_error_count": 1,
            "min_repository_test_skipped_count": 1,
            "min_repository_test_count_source_kind_count": 2,
        },
    )
    markdown = render_github_repo_intelligence_suite_markdown(
        type(
            "Report",
            (),
            {
                "suite_name": "test_count_suite",
                "manifest_path": "manifest.json",
                "output_dir": "out",
                "passed": True,
                "summary": summary,
                "runs": [],
            },
        )()
    )

    assert summary["planned_repository_test_result_status_counts"] == {
        "fail": 1,
        "pass": 1,
    }
    assert summary["repository_test_count_source_counts"] == {
        "pytest_summary": 1,
        "unittest_summary": 1,
    }
    assert summary["repository_test_execution_result_count"] == 2
    assert summary["repository_test_execution_result_runs"] == [
        "pytest_repo",
        "unittest_repo",
    ]
    assert summary["repository_test_counted_run_count"] == 2
    assert summary["repository_test_counted_runs"] == [
        "pytest_repo",
        "unittest_repo",
    ]
    assert summary["repository_test_count"] == 7
    assert summary["repository_test_passed_count"] == 4
    assert summary["repository_test_failed_count"] == 1
    assert summary["repository_test_error_count"] == 1
    assert summary["repository_test_skipped_count"] == 1
    assert summary["suite_threshold_failed_count"] == 0
    assert "Repository Test Execution Result Runs: 2" in markdown
    assert "Repository Test Counted Runs: 2" in markdown
    assert (
        "Repository Test Counts: total=7, passed=4, failed=1, errors=1, "
        "skipped=1"
    ) in markdown
    assert "Planned Repository Test Result Statuses: fail=1, pass=1" in markdown
    assert (
        "Repository Test Count Sources: pytest_summary=1, unittest_summary=1"
    ) in markdown


def test_intelligence_suite_summarizes_timeout_narrowing_metrics():
    result = GitHubRepoIntelligenceSuiteRunResult(
        name="timeout_narrowed_repo",
        repo="example/timeout-narrowed",
        output_dir="out/timeout_narrowed",
        report_path="out/timeout_narrowed/github_repo_intelligence.json",
        status="pass",
        passed=True,
        expected_status="pass",
        expectation_passed=True,
        metrics={
            "status": "pass",
            "repository_test_timeout_narrowing_status": "pass",
            "repository_test_timeout_narrowing_reason": (
                "timeout_narrowing_selected_non_timeout_result"
            ),
            "repository_test_timeout_narrowing_executed": True,
            "repository_test_timeout_narrowing_attempt_count": 2,
            "repository_test_timeout_narrowing_selected_command": (
                "python -m pytest -q --maxfail=1 tests/test_sample.py"
            ),
            "repository_test_timeout_narrowing_selected_failure_category": "none",
        },
        metric_checks=[],
        expectation_checks=[],
        command_args=[],
    )

    summary = _suite_summary(
        [result],
        suite_thresholds={
            "min_repository_test_timeout_narrowing_count": 1,
            "min_repository_test_timeout_narrowing_executed_count": 1,
            "min_repository_test_timeout_narrowing_attempt_count": 2,
            "min_repository_test_timeout_narrowing_status_pass_count": 1,
            "min_repository_test_timeout_narrowing_reason_timeout_narrowing_selected_non_timeout_result_count": 1,
            "min_repository_test_timeout_narrowing_selected_failure_category_none_count": 1,
        },
    )
    markdown = render_github_repo_intelligence_suite_markdown(
        GitHubRepoIntelligenceSuiteReport(
            suite_name="timeout_narrowing_suite",
            manifest_path="manifest.json",
            output_dir="out",
            passed=True,
            runs=[result],
            summary=summary,
        )
    )

    assert summary["repository_test_timeout_narrowing_status_counts"] == {
        "pass": 1
    }
    assert summary["repository_test_timeout_narrowing_reason_counts"] == {
        "timeout_narrowing_selected_non_timeout_result": 1
    }
    assert summary[
        "repository_test_timeout_narrowing_selected_failure_category_counts"
    ] == {"none": 1}
    assert summary["repository_test_timeout_narrowing_count"] == 1
    assert summary["repository_test_timeout_narrowing_runs"] == [
        "timeout_narrowed_repo"
    ]
    assert summary["repository_test_timeout_narrowing_executed_count"] == 1
    assert summary["repository_test_timeout_narrowing_executed_runs"] == [
        "timeout_narrowed_repo"
    ]
    assert summary["repository_test_timeout_narrowing_attempt_count"] == 2
    assert summary["repository_test_timeout_narrowing_status_pass_count"] == 1
    assert (
        summary[
            "repository_test_timeout_narrowing_reason_timeout_narrowing_selected_non_timeout_result_count"
        ]
        == 1
    )
    assert (
        summary[
            "repository_test_timeout_narrowing_selected_failure_category_none_count"
        ]
        == 1
    )
    assert summary["suite_threshold_failed_count"] == 0
    assert "Repository Test Timeout Narrowing Statuses: pass=1" in markdown
    assert (
        "Repository Test Timeout Narrowing Reasons: "
        "timeout_narrowing_selected_non_timeout_result=1"
    ) in markdown
    assert "Repository Test Timeout Narrowing Executed Runs: 1" in markdown
    assert "Repository Test Timeout Narrowing Attempts: 2" in markdown


def test_intelligence_suite_expands_reflection_failure_type_counts_for_thresholds():
    result = GitHubRepoIntelligenceSuiteRunResult(
        name="reflection_taxonomy_repo",
        repo="example/reflection-taxonomy",
        output_dir="out/reflection_taxonomy",
        report_path="out/reflection_taxonomy/github_repo_intelligence.json",
        status="pass",
        passed=True,
        expected_status="pass",
        expectation_passed=True,
        metrics={
            "status": "pass",
            "repository_test_patch_validation_failure_type_counts": {
                "success": 1,
                "test_failure": 2,
            },
            "reflection_initial_failure_type_counts": {"test_failure": 1},
            "reflection_failure_type_counts": {
                "import_error": 1,
                "success": 1,
            },
            "reflection_parent_failure_type_counts": {"test_failure": 2},
            "successful_reflection_parent_failure_type_counts": {
                "test_failure": 1
            },
        },
        metric_checks=[],
        expectation_checks=[],
        command_args=[],
    )

    summary = _suite_summary(
        [result],
        suite_thresholds={
            "min_repository_test_patch_validation_failure_type_success_count": 1,
            "min_repository_test_patch_validation_failure_type_test_failure_count": 2,
            "min_reflection_initial_failure_type_test_failure_count": 1,
            "min_reflection_failure_type_import_error_count": 1,
            "min_reflection_failure_type_success_count": 1,
            "min_reflection_parent_failure_type_test_failure_count": 2,
            "min_successful_reflection_parent_failure_type_test_failure_count": 1,
        },
    )
    markdown = render_github_repo_intelligence_suite_markdown(
        GitHubRepoIntelligenceSuiteReport(
            suite_name="reflection_taxonomy_suite",
            manifest_path="manifest.json",
            output_dir="out",
            passed=True,
            runs=[result],
            summary=summary,
        )
    )

    assert summary["repository_test_patch_validation_failure_type_counts"] == {
        "success": 1,
        "test_failure": 2,
    }
    assert summary["reflection_initial_failure_type_counts"] == {
        "test_failure": 1
    }
    assert summary["reflection_failure_type_counts"] == {
        "import_error": 1,
        "success": 1,
    }
    assert summary["reflection_parent_failure_type_counts"] == {
        "test_failure": 2
    }
    assert summary["successful_reflection_parent_failure_type_counts"] == {
        "test_failure": 1
    }
    assert (
        summary[
            "repository_test_patch_validation_failure_type_test_failure_count"
        ]
        == 2
    )
    assert summary["reflection_initial_failure_type_test_failure_count"] == 1
    assert summary["reflection_failure_type_import_error_count"] == 1
    assert summary["reflection_failure_type_success_count"] == 1
    assert summary["reflection_parent_failure_type_test_failure_count"] == 2
    assert (
        summary["successful_reflection_parent_failure_type_test_failure_count"]
        == 1
    )
    assert summary["suite_threshold_failed_count"] == 0
    assert "Patch Validation Failure Types: success=1, test_failure=2" in markdown
    assert "Reflection Initial Failure Types: test_failure=1" in markdown
    assert "Reflection Failure Types: import_error=1, success=1" in markdown
    assert "Reflection Parent Failure Types: test_failure=2" in markdown
    assert "Successful Reflection Parent Failure Types: test_failure=1" in markdown


def test_intelligence_suite_summarizes_source_only_static_blockers():
    result = GitHubRepoIntelligenceSuiteRunResult(
        name="requests_source_only",
        repo="https://github.com/psf/requests",
        output_dir="out/requests_source_only",
        report_path="out/requests_source_only/github_repo_intelligence.json",
        status="pass",
        passed=True,
        expected_status="pass",
        expectation_passed=True,
        metrics={
            "status": "pass",
            "static_intelligence_level": "source_only",
            "blocker": "no_static_candidates",
            "repository_structure_modeled": True,
            "repo_graph_ready": True,
        },
        metric_checks=[],
        expectation_checks=[],
        command_args=[],
    )

    summary = _suite_summary(
        [result],
        suite_thresholds={"min_source_only_static_blocker_count": 1},
    )
    markdown = render_github_repo_intelligence_suite_markdown(
        GitHubRepoIntelligenceSuiteReport(
            suite_name="source_only_static_blocker_suite",
            manifest_path="manifest.json",
            output_dir="out",
            passed=True,
            runs=[result],
            summary=summary,
        )
    )

    assert summary["source_only_static_blocker_count"] == 1
    assert summary["source_only_static_blocker_runs"] == ["requests_source_only"]
    assert summary["suite_threshold_failed_count"] == 0
    assert "Source-Only Static Blocker Runs: 1" in markdown


def test_intelligence_suite_summarizes_llm_reflection_audit_counts():
    summary = _suite_summary(
        [
            GitHubRepoIntelligenceSuiteRunResult(
                name="llm_missing_key",
                repo="example/missing-key",
                output_dir="out/missing-key",
                report_path="out/missing-key/github_repo_intelligence.json",
                status="pass",
                passed=True,
                expected_status="pass",
                expectation_passed=True,
                metrics={
                    "status": "pass",
                    "repository_llm_reflection_status": "unavailable",
                    "repository_llm_reflection_blocker": (
                        "missing_api_key:CIA_LLM_API_KEY"
                    ),
                    "repository_llm_reflection_blocked": True,
                    "repository_llm_reflection_provider": "deepseek",
                    "repository_llm_reflection_model": "deepseek-v4-pro",
                },
                metric_checks=[],
                expectation_checks=[],
                command_args=[],
            ),
            GitHubRepoIntelligenceSuiteRunResult(
                name="llm_ready",
                repo="example/ready",
                output_dir="out/ready",
                report_path="out/ready/github_repo_intelligence.json",
                status="pass",
                passed=True,
                expected_status="pass",
                expectation_passed=True,
                metrics={
                    "status": "pass",
                    "repository_llm_reflection_status": "ready",
                    "repository_llm_reflection_blocker": "",
                    "repository_llm_reflection_blocked": False,
                    "repository_llm_reflection_provider": "deepseek",
                    "repository_llm_reflection_model": "deepseek-v4-pro",
                },
                metric_checks=[],
                expectation_checks=[],
                command_args=[],
            ),
        ],
        suite_thresholds={},
    )
    markdown = render_github_repo_intelligence_suite_markdown(
        type(
            "Report",
            (),
            {
                "suite_name": "llm_reflection_suite",
                "manifest_path": "manifest.json",
                "output_dir": "out",
                "passed": True,
                "summary": summary,
                "runs": [],
            },
        )()
    )

    assert summary["repository_llm_reflection_status_counts"] == {
        "ready": 1,
        "unavailable": 1,
    }
    assert summary["repository_llm_reflection_blocker_counts"] == {
        "missing_api_key:CIA_LLM_API_KEY": 1
    }
    assert "LLM Reflection Statuses: ready=1, unavailable=1" in markdown
    assert (
        "LLM Reflection Blockers: missing_api_key:CIA_LLM_API_KEY=1"
        in markdown
    )


def test_intelligence_suite_summarizes_scenario_tags_and_repo_input_kinds():
    summary = _suite_summary(
        [
            GitHubRepoIntelligenceSuiteRunResult(
                name="owner_repo_case",
                repo="pypa/sampleproject",
                output_dir="out/owner_repo_case",
                report_path="out/owner_repo_case/github_repo_intelligence.json",
                status="pass",
                passed=True,
                expected_status="pass",
                expectation_passed=True,
                metrics={
                    "status": "pass",
                    "repo_input_kind": "owner_repo",
                    "agent_mode": True,
                    "output_dir_defaulted": True,
                    "scenario_tags": [
                        "owner_repo_input",
                        "default_branch_discovery",
                        "unittest_fallback",
                    ],
                },
                metric_checks=[],
                expectation_checks=[],
                command_args=[],
            ),
            GitHubRepoIntelligenceSuiteRunResult(
                name="url_case",
                repo="https://github.com/pytest-dev/pluggy",
                output_dir="out/url_case",
                report_path="out/url_case/github_repo_intelligence.json",
                status="pass",
                passed=True,
                expected_status="pass",
                expectation_passed=True,
                metrics={
                    "status": "pass",
                    "repo_input_kind": "github_url",
                    "scenario_tags": [
                        "github_url_input",
                        "src_layout",
                        "pytest_project",
                    ],
                },
                metric_checks=[],
                expectation_checks=[],
                command_args=[],
            ),
        ],
        suite_thresholds={
            "min_repo_input_kind_count": 2,
            "min_repo_input_kind_owner_repo_count": 1,
            "min_repo_input_kind_github_url_count": 1,
            "min_scenario_tag_kind_count": 6,
            "min_scenario_tag_owner_repo_input_count": 1,
            "min_scenario_tag_github_url_input_count": 1,
            "min_scenario_tag_src_layout_count": 1,
            "min_scenario_tag_unittest_fallback_count": 1,
            "min_output_dir_defaulted_count": 1,
            "min_agent_default_output_dir_count": 1,
        },
    )
    markdown = render_github_repo_intelligence_suite_markdown(
        type(
            "Report",
            (),
            {
                "suite_name": "scenario_suite",
                "manifest_path": "manifest.json",
                "output_dir": "out",
                "passed": True,
                "summary": summary,
                "runs": [],
            },
        )()
    )

    assert summary["repo_input_kind_counts"] == {
        "github_url": 1,
        "owner_repo": 1,
    }
    assert summary["scenario_tag_counts"] == {
        "default_branch_discovery": 1,
        "github_url_input": 1,
        "owner_repo_input": 1,
        "pytest_project": 1,
        "src_layout": 1,
        "unittest_fallback": 1,
    }
    assert summary["repo_input_kind_owner_repo_count"] == 1
    assert summary["repo_input_kind_github_url_count"] == 1
    assert summary["output_dir_defaulted_count"] == 1
    assert summary["output_dir_defaulted_runs"] == ["owner_repo_case"]
    assert summary["agent_default_output_dir_count"] == 1
    assert summary["agent_default_output_dir_runs"] == ["owner_repo_case"]
    assert summary["scenario_tag_src_layout_count"] == 1
    assert summary["scenario_tag_unittest_fallback_count"] == 1
    assert summary["scenario_coverage_blocked_count"] == 0
    assert summary["scenario_coverage_blocker_counts"] == {}
    assert summary["suite_threshold_failed_count"] == 0
    assert "Default Output Dir Runs: 1" in markdown
    assert "Agent Default Output Dir Runs: 1" in markdown
    assert "Repo Input Kinds: github_url=1, owner_repo=1" in markdown
    assert "Scenario Tags:" in markdown
    assert "src_layout=1" in markdown
    assert "unittest_fallback=1" in markdown


def test_intelligence_suite_can_run_repo_only_cli_default_output_dir(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    output_dir = tmp_path / "suite"
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "repo_only_cli_suite",
                "defaults": {
                    "agent": True,
                    "preset": "mining",
                    "max_sources": 5,
                    "max_candidates": 5,
                    "auto_fallback": False,
                    "run_repository_test_command": False,
                    "repository_patch_generation_mode": "rule",
                    "auto_controller_max_actions": 4,
                    "use_cli_default_output_dir": True,
                },
                "suite_thresholds": {
                    "max_command_failed_count": 0,
                    "max_expectation_failed_count": 0,
                    "max_metric_check_failed_count": 0,
                    "min_output_dir_defaulted_count": 1,
                    "min_agent_default_output_dir_count": 1,
                    "min_agent_shortcut_count": 1,
                    "min_artifact_core_ready_count": 1,
                    "min_agent_controller_loop_complete_count": 1,
                    "min_agent_answer_coverage_complete_count": 1,
                },
                "runs": [
                    {
                        "name": "repo_only_agent",
                        "repo": "example/project",
                        "expected_status": "pass",
                        "expected_agent_shortcut": True,
                        "expected_execution_profile": "agent-auto",
                        "metric_thresholds": {
                            "output_dir_defaulted": 1,
                            "agent_controller_loop_complete": 1,
                            "agent_answer_coverage_answered_count": 7,
                        },
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    opener = _FakeOpener(_repo_payloads_without_python() * 3)

    report = run_github_repo_intelligence_suite(
        manifest_path,
        output_dir,
        opener=opener,
    )
    run = report.runs[0]
    saved = json.loads(Path(run.report_path).read_text(encoding="utf-8"))
    invocation = saved["agent_invocation"]

    assert report.passed is True
    assert report.summary["output_dir_defaulted_count"] == 1
    assert report.summary["agent_default_output_dir_count"] == 1
    assert run.metrics["output_dir_defaulted"] is True
    assert run.metrics["agent_mode"] is True
    assert run.metrics["agent_shortcut"] is True
    assert invocation["output_dir_defaulted"] is True
    assert invocation["default_output_dir"].endswith(
        "repo_intelligence_agent_example_project"
    )
    assert Path(run.output_dir) == (
        output_dir / "outputs" / "repo_intelligence_agent_example_project"
    )
    assert run.command_args[3] == "example/project"
    assert run.command_args[4] == "--agent"
    assert str(run.output_dir) not in run.command_args


def test_intelligence_suite_metric_snapshot_audits_agent_auto_action_loop():
    metrics = _suite_metric_snapshot(
        {
            "agent_auto_actions": [
                {
                    "action_id": "run_repository_tests_with_checkout",
                    "loop_observe_stage": "phase2_static_graph_fault_localization",
                    "loop_plan_action": "run_repository_tests_with_checkout",
                    "loop_verify_outcome": "dynamic_evidence_collected",
                    "loop_reflect_status": "verified_progress",
                    "loop_replan_policy": "continue_observe_plan_act",
                    "loop_replan_next_action": "prepare_repository_test_environment",
                },
                {
                    "action_id": "generate_and_validate_patches",
                    "loop_observe_stage": "phase2_static_graph_fault_localization",
                    "loop_plan_action": "generate_and_validate_patches",
                    "loop_verify_outcome": "patch_validation_ready",
                    "loop_reflect_status": "verified_progress",
                    "loop_replan_policy": "stop_phase_goal_reached",
                    "loop_replan_next_action": "run_search_and_ablation_evaluation",
                    "after_stage": "phase3_patch_validation",
                    "after_patch_validation_status": "pass",
                    "after_repair_ready": True,
                },
                {
                    "action_id": "run_patch_reflection_loop",
                    "loop_observe_stage": "phase3_patch_validation",
                    "loop_plan_action": "run_patch_reflection_loop",
                    "loop_verify_outcome": "reflection_candidates_generated",
                    "loop_reflect_status": "verified_progress",
                    "loop_replan_policy": "stop_phase_goal_reached",
                    "loop_replan_next_action": "run_search_and_ablation_evaluation",
                    "after_reflection_candidate_count": 1,
                    "after_successful_reflection_count": 1,
                },
            ],
            "agent_auto_stop_reason": "phase_goal_reached:patch_validation_ready",
            "agent_auto_loop_audit": {
                "progress_count": 3,
                "no_progress_count": 0,
                "complete_loop_recorded": True,
                "verify_outcome_counts": {
                    "dynamic_evidence_collected": 1,
                    "environment_repair_plan_recorded": 1,
                    "reflection_candidates_generated": 1,
                },
                "reflect_status_counts": {"verified_progress": 3},
                "replan_policy_counts": {
                    "continue_observe_plan_act": 1,
                    "manual_or_blocked_next_action": 1,
                    "stop_phase_goal_reached": 1,
                },
                "goal_readiness_status_counts": {
                    "pass": 2,
                    "warning": 1,
                },
                "goal_readiness_passed_action_count": 2,
                "final_goal_readiness_status": "pass",
            },
        }
    )

    assert metrics["agent_auto_action_loop_required_count"] == 3
    assert metrics["agent_auto_action_loop_complete_count"] == 3
    assert metrics["agent_auto_action_loop_incomplete_count"] == 0
    assert metrics["agent_auto_action_loop_complete"] is True
    assert metrics["agent_auto_patch_validation_reached_action_count"] == 1
    assert metrics["agent_auto_repair_ready_action_count"] == 1
    assert metrics["agent_auto_repair_goal_reached"] is True
    assert metrics["agent_auto_reflection_action_count"] == 1
    assert metrics["agent_auto_reflection_candidate_action_count"] == 1
    assert metrics["agent_auto_successful_reflection_action_count"] == 1
    assert metrics["agent_auto_reflection_goal_reached"] is True
    assert metrics["agent_auto_action_id_counts"] == {
        "generate_and_validate_patches": 1,
        "run_patch_reflection_loop": 1,
        "run_repository_tests_with_checkout": 1,
    }
    assert metrics["agent_auto_reflect_status_counts"] == {"verified_progress": 3}
    assert metrics["agent_auto_goal_readiness_status_counts"] == {
        "pass": 2,
        "warning": 1,
    }
    assert metrics["agent_auto_goal_readiness_passed_action_count"] == 2
    assert metrics["agent_auto_final_goal_readiness_status"] == "pass"


def test_intelligence_suite_summarizes_agent_auto_action_loop_audit():
    summary = _suite_summary(
        [
            GitHubRepoIntelligenceSuiteRunResult(
                name="agent_auto_repo",
                repo="example/project",
                output_dir="out/agent_auto_repo",
                report_path="out/agent_auto_repo/github_repo_intelligence.json",
                status="pass",
                passed=True,
                expected_status="pass",
                expectation_passed=True,
                metrics={
                    "status": "pass",
                    "static_intelligence_status": "analysis_ready",
                    "analysis_stage": "phase2_static_graph_fault_localization",
                    "controller_action_id": "await_environment_repair",
                    "artifact_inventory_status": "pass",
                    "artifact_inventory_core_ready": True,
                    "artifact_inventory_file_check_enabled": True,
                    "agent_decision_timeline_status": "pass",
                    "agent_decision_timeline_complete": True,
                    "agent_decision_timeline_step_count": 2,
                    "agent_decision_timeline_complete_step_count": 2,
                    "agent_auto_loop_progress_count": 2,
                    "agent_auto_loop_no_progress_count": 0,
                    "agent_auto_complete_loop_recorded": True,
                    "agent_auto_action_loop_required_count": 2,
                    "agent_auto_action_loop_complete_count": 2,
                    "agent_auto_action_loop_incomplete_count": 0,
                    "agent_auto_action_loop_complete": True,
                    "agent_auto_action_id_counts": {
                        "run_repository_tests_with_checkout": 1,
                        "generate_and_validate_patches": 1,
                    },
                    "agent_auto_stop_reason": (
                        "phase_goal_reached:patch_validation_ready"
                    ),
                    "agent_auto_stop_category": "phase_goal_reached",
                    "agent_auto_stop_action_id": (
                        "run_search_and_ablation_evaluation"
                    ),
                    "agent_auto_stop_recovery_policy": (
                        "stop_phase_goal_reached"
                    ),
                    "agent_auto_stop_external_input_kind": "none",
                    "agent_auto_stop_requires_user_action": False,
                    "agent_auto_stop_requires_environment_change": False,
                    "agent_auto_patch_validation_reached_action_count": 1,
                    "agent_auto_repair_ready_action_count": 1,
                    "agent_auto_repair_goal_reached": True,
                    "agent_auto_reflection_action_count": 1,
                    "agent_auto_reflection_candidate_action_count": 1,
                    "agent_auto_successful_reflection_action_count": 1,
                    "agent_auto_reflection_goal_reached": True,
                    "agent_auto_verify_outcome_counts": {
                        "dynamic_evidence_collected": 1,
                        "environment_repair_plan_recorded": 1,
                    },
                    "agent_auto_reflect_status_counts": {"verified_progress": 2},
                    "agent_auto_replan_policy_counts": {
                        "continue_observe_plan_act": 1,
                        "manual_or_blocked_next_action": 1,
                    },
                    "agent_auto_goal_readiness_status_counts": {
                        "pass": 2,
                    },
                    "agent_auto_goal_readiness_passed_action_count": 2,
                    "agent_auto_final_goal_readiness_status": "pass",
                },
                metric_checks=[],
                expectation_checks=[],
                command_args=[],
            )
        ],
        suite_thresholds={
            "min_agent_auto_complete_loop_count": 1,
            "min_agent_decision_timeline_complete_count": 1,
            "min_agent_auto_action_loop_complete_run_count": 1,
            "min_agent_auto_action_loop_complete_count": 2,
            "max_agent_auto_action_loop_incomplete_count": 0,
            "min_agent_auto_patch_validation_reached_action_count": 1,
            "min_agent_auto_repair_ready_action_count": 1,
            "min_agent_auto_repair_goal_reached_count": 1,
            "min_agent_auto_reflection_action_count": 1,
            "min_agent_auto_reflection_candidate_action_count": 1,
            "min_agent_auto_successful_reflection_action_count": 1,
            "min_agent_auto_reflection_goal_reached_count": 1,
        },
    )
    markdown = render_github_repo_intelligence_suite_markdown(
        type(
            "Report",
            (),
            {
                "suite_name": "agent_auto_suite",
                "manifest_path": "manifest.json",
                "output_dir": "out",
                "passed": True,
                "summary": summary,
                "runs": [],
            },
        )()
    )

    assert summary["agent_auto_complete_loop_count"] == 1
    assert summary["agent_decision_timeline_ready_count"] == 1
    assert summary["agent_decision_timeline_complete_count"] == 1
    assert summary["agent_decision_timeline_step_count"] == 2
    assert summary["agent_decision_timeline_complete_step_count"] == 2
    assert summary["agent_auto_action_loop_complete_run_count"] == 1
    assert summary["agent_auto_action_loop_required_count"] == 2
    assert summary["agent_auto_action_loop_complete_count"] == 2
    assert summary["agent_auto_action_loop_incomplete_count"] == 0
    assert summary["agent_auto_patch_validation_reached_action_count"] == 1
    assert summary["agent_auto_repair_ready_action_count"] == 1
    assert summary["agent_auto_repair_goal_reached_count"] == 1
    assert summary["agent_auto_reflection_action_count"] == 1
    assert summary["agent_auto_reflection_candidate_action_count"] == 1
    assert summary["agent_auto_successful_reflection_action_count"] == 1
    assert summary["agent_auto_reflection_goal_reached_count"] == 1
    assert summary["agent_auto_reflect_status_counts"] == {"verified_progress": 2}
    assert summary["agent_auto_goal_readiness_status_counts"] == {"pass": 2}
    assert summary["agent_auto_goal_readiness_passed_action_count"] == 2
    assert summary["agent_auto_action_id_counts"] == {
        "generate_and_validate_patches": 1,
        "run_repository_tests_with_checkout": 1,
    }
    assert summary["agent_auto_stop_reason_counts"] == {
        "phase_goal_reached:patch_validation_ready": 1
    }
    assert summary["agent_auto_stop_category_counts"] == {
        "phase_goal_reached": 1
    }
    assert summary["agent_auto_stop_recovery_policy_counts"] == {
        "stop_phase_goal_reached": 1
    }
    assert summary["agent_auto_stop_external_input_kind_counts"] == {
        "none": 1
    }
    assert (
        summary["agent_auto_stop_recovery_policy_stop_phase_goal_reached_count"]
        == 1
    )
    assert summary["agent_auto_stop_external_input_kind_none_count"] == 1
    assert summary["agent_auto_stop_requires_user_action_count"] == 0
    assert summary["agent_auto_stop_requires_environment_change_count"] == 0
    assert summary["suite_threshold_failed_count"] == 0
    assert "Agent Auto Action Loop Complete Runs: 1" in markdown
    assert "Agent Decision Timeline Ready Runs: 1" in markdown
    assert "Agent Decision Timeline Complete Steps: 2/2" in markdown
    assert "Agent Auto Action Loop Complete Actions: 2/2" in markdown
    assert "Agent Auto Patch Validation Reached Actions: 1" in markdown
    assert "Agent Auto Repair Ready Actions: 1" in markdown
    assert "Agent Auto Repair Goal Reached Runs: 1" in markdown
    assert "Agent Auto Reflection Actions: 1" in markdown
    assert "Agent Auto Reflection Candidate Actions: 1" in markdown
    assert "Agent Auto Successful Reflection Actions: 1" in markdown
    assert "Agent Auto Reflection Goal Reached Runs: 1" in markdown
    assert "Agent Auto Reflect Statuses: verified_progress=2" in markdown
    assert "Agent Auto Goal Readiness Statuses: pass=2" in markdown
    assert "Agent Auto Goal Readiness Passed Actions: 2" in markdown
    assert (
        "Agent Auto Actions: generate_and_validate_patches=1, "
        "run_repository_tests_with_checkout=1"
    ) in markdown
    assert (
        "Agent Auto Stop Reasons: phase_goal_reached:patch_validation_ready=1"
    ) in markdown
    assert (
        "Agent Auto Stop Recovery Policies: stop_phase_goal_reached=1"
    ) in markdown
    assert "Agent Auto Stop External Inputs: none=1" in markdown
    assert "Agent Auto Stops Requiring User Action: 0" in markdown
    assert "Agent Auto Stop Categories: phase_goal_reached=1" in markdown


def test_intelligence_suite_expands_agent_auto_action_id_counts_for_thresholds():
    result = GitHubRepoIntelligenceSuiteRunResult(
        name="timeout_controller_repo",
        repo="example/timeout-controller",
        output_dir="out/timeout_controller",
        report_path="out/timeout_controller/github_repo_intelligence.json",
        status="pass",
        passed=True,
        expected_status="pass",
        expectation_passed=True,
        metrics={
            "status": "pass",
            "agent_auto_action_id_counts": {
                "narrow_repository_tests_after_timeout": 1,
            },
        },
        metric_checks=[],
        expectation_checks=[],
        command_args=[],
    )

    summary = _suite_summary(
        [result],
        suite_thresholds={
            "min_agent_auto_action_id_narrow_repository_tests_after_timeout_count": 1,
        },
    )
    markdown = render_github_repo_intelligence_suite_markdown(
        GitHubRepoIntelligenceSuiteReport(
            suite_name="agent_auto_action_id_suite",
            manifest_path="manifest.json",
            output_dir="out",
            passed=True,
            runs=[result],
            summary=summary,
        )
    )

    assert summary["agent_auto_action_id_counts"] == {
        "narrow_repository_tests_after_timeout": 1
    }
    assert (
        summary[
            "agent_auto_action_id_narrow_repository_tests_after_timeout_count"
        ]
        == 1
    )
    assert summary["suite_threshold_failed_count"] == 0
    assert "Agent Auto Actions: narrow_repository_tests_after_timeout=1" in markdown


def test_intelligence_suite_summarizes_environment_repair_plan_metrics():
    summary = _suite_summary(
        [
            GitHubRepoIntelligenceSuiteRunResult(
                name="env_blocked_repo",
                repo="example/project",
                output_dir="out/env_blocked_repo",
                report_path="out/env_blocked_repo/github_repo_intelligence.json",
                status="pass",
                passed=True,
                expected_status="pass",
                expectation_passed=True,
                metrics={
                    "status": "pass",
                    "repository_test_environment_repair_plan_status": "pass",
                    "repository_test_environment_repair_plan_blocker": (
                        "environment:test_tool_missing"
                    ),
                    "repository_test_environment_repair_plan_ready": True,
                    "repository_test_environment_repair_plan_has_install_command": (
                        True
                    ),
                    "agent_auto_stop_reason": "selected_action_not_executable",
                    "agent_auto_stop_category": "manual_or_blocked",
                    "agent_auto_stop_recovery_policy": (
                        "apply_environment_repair_then_rerun_agent"
                    ),
                    "agent_auto_stop_external_input_kind": "environment",
                    "agent_auto_stop_requires_user_action": True,
                    "agent_auto_stop_requires_environment_change": True,
                },
                metric_checks=[],
                expectation_checks=[],
                command_args=[],
            )
        ],
        suite_thresholds={
            "min_repository_test_environment_repair_plan_ready_count": 1,
            "min_repository_test_environment_repair_plan_install_command_count": 1,
            "min_repository_test_environment_repair_plan_status_pass_count": 1,
            "min_repository_test_environment_repair_plan_blocker_environment_test_tool_missing_count": 1,
            "min_agent_auto_stop_requires_environment_change_count": 1,
            "min_agent_auto_stop_external_input_kind_environment_count": 1,
        },
    )
    markdown = render_github_repo_intelligence_suite_markdown(
        GitHubRepoIntelligenceSuiteReport(
            manifest_path="manifest.json",
            output_dir="out",
            suite_name="environment_repair_suite",
            passed=True,
            summary=summary,
            runs=[],
        )
    )

    assert summary["repository_test_environment_repair_plan_status_counts"] == {
        "pass": 1
    }
    assert summary["repository_test_environment_repair_plan_blocker_counts"] == {
        "environment:test_tool_missing": 1
    }
    assert summary["repository_test_environment_repair_plan_ready_count"] == 1
    assert (
        summary["repository_test_environment_repair_plan_install_command_count"]
        == 1
    )
    assert summary["repository_test_environment_repair_plan_status_pass_count"] == 1
    assert (
        summary[
            "repository_test_environment_repair_plan_blocker_environment_test_tool_missing_count"
        ]
        == 1
    )
    assert summary["agent_auto_stop_requires_environment_change_count"] == 1
    assert summary["agent_auto_stop_external_input_kind_environment_count"] == 1
    assert summary["suite_threshold_failed_count"] == 0
    assert "Repository Test Environment Repair Plan Statuses: pass=1" in markdown
    assert "Repository Test Environment Repair Plan Ready Runs: 1" in markdown
    assert "Agent Auto Stop External Inputs: environment=1" in markdown


def test_intelligence_suite_setup_doctor_blocker_counts_are_thresholdable():
    summary = _suite_summary(
        [
            GitHubRepoIntelligenceSuiteRunResult(
                name="no_test_command_project",
                repo="https://github.com/example/no-tests",
                output_dir="out/no_test_command_project",
                report_path="out/no_test_command_project/github_repo_intelligence.json",
                status="pass",
                passed=True,
                expected_status="pass",
                expectation_passed=True,
                metrics={
                    "repository_test_setup_doctor_status": "blocked",
                    "repository_test_setup_doctor_blocker": (
                        "test_command:no_recommended_test_command"
                    ),
                },
                metric_checks=[],
                expectation_checks=[],
                command_args=[],
            )
        ],
        suite_thresholds={
            "min_repository_test_setup_doctor_blocker_test_command_no_recommended_test_command_count": 1,
        },
    )

    assert summary["repository_test_setup_doctor_blocker_counts"] == {
        "test_command:no_recommended_test_command": 1
    }
    assert (
        summary[
            "repository_test_setup_doctor_blocker_test_command_no_recommended_test_command_count"
        ]
        == 1
    )
    assert summary["suite_threshold_failed_count"] == 0


def test_intelligence_suite_agent_auto_example_manifest_exposes_one_command_goal():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_agent_auto_smoke.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == "repo_intelligence_agent_auto_smoke"
    assert manifest["defaults"]["agent"] is True
    assert manifest["defaults"]["auto_controller_max_actions"] == 4
    assert manifest["defaults"]["repository_patch_generation_mode"] == "rule"
    assert manifest["suite_thresholds"]["min_agent_auto_complete_loop_count"] == 1
    assert manifest["suite_thresholds"][
        "min_agent_controller_loop_complete_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_decision_timeline_complete_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_answer_coverage_complete_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_agent_shortcut_count"] == 1
    assert manifest["suite_thresholds"]["min_objective_compliance_pass_count"] == 1
    assert manifest["suite_thresholds"][
        "max_objective_compliance_failed_section_kind_count"
    ] == 0
    assert manifest["suite_thresholds"][
        "min_repository_test_dynamic_evidence_level_kind_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_execution_result_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_repository_test_counted_run_count"] == 1
    assert manifest["suite_thresholds"]["min_repository_test_count"] == 1
    assert manifest["suite_thresholds"]["min_repository_test_passed_count"] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_count_source_kind_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_auto_action_loop_complete_run_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_auto_action_loop_complete_count"
    ] == 2
    assert manifest["suite_thresholds"][
        "max_agent_auto_action_loop_incomplete_count"
    ] == 0
    assert manifest["runs"][0]["expected_agent_shortcut"] is True
    assert manifest["runs"][0]["expected_execution_profile"] == "agent-auto"
    assert manifest["runs"][0]["expected_patch_generation_mode"] == "rule"
    assert manifest["runs"][0]["expected_llm_patch_generation_status"] == "disabled"
    assert manifest["runs"][0]["expected_planned_repository_test_result_status"] == (
        "pass"
    )
    assert manifest["runs"][0]["metric_thresholds"][
        "agent_auto_loop_progress_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "agent_decision_timeline_complete"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "agent_decision_timeline_step_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "agent_auto_action_loop_complete_count"
    ] == 2
    assert manifest["runs"][0]["metric_thresholds"][
        "planned_repository_test_result_test_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "planned_repository_test_result_passed"
    ] == 1


def test_intelligence_suite_agent_matrix_manifest_documents_scenario_coverage():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_agent_matrix.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == "repo_intelligence_agent_matrix"
    assert manifest["defaults"]["source_cache_dir"] == (
        "outputs_smoke/github_raw_source_cache"
    )
    assert manifest["defaults"]["repository_patch_generation_mode"] == "rule"
    assert manifest["suite_thresholds"]["max_scenario_coverage_blocked_count"] == 0
    assert manifest["suite_thresholds"]["min_run_count"] == 6
    assert manifest["suite_thresholds"]["min_repo_input_kind_count"] == 2
    assert manifest["suite_thresholds"]["min_scenario_tag_kind_count"] == 17
    assert manifest["suite_thresholds"]["min_objective_compliance_pass_count"] == 6
    assert manifest["suite_thresholds"][
        "min_blocked_agent_answer_complete_count"
    ] == 6
    assert (
        manifest["suite_thresholds"][
            "min_agent_answer_question_answered_kind_count"
        ]
        == 7
    )
    assert (
        manifest["suite_thresholds"][
            "max_agent_answer_question_missing_kind_count"
        ]
        == 0
    )
    expected_answer_questions = [
        "repository_structure",
        "suspicious_functions",
        "suspicious_reason",
        "testability",
        "repairability",
        "blocker",
        "next_action",
    ]
    for question_id in expected_answer_questions:
        assert (
            manifest["suite_thresholds"][
                f"min_agent_answer_question_answered_{question_id}_count"
            ]
            == 6
        )
    assert manifest["suite_thresholds"][
        "max_blocked_agent_answer_incomplete_count"
    ] == 0
    assert manifest["suite_thresholds"][
        "max_objective_compliance_failed_section_kind_count"
    ] == 0
    assert manifest["suite_thresholds"][
        "min_scenario_tag_owner_repo_input_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_scenario_tag_github_url_input_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_scenario_tag_default_branch_discovery_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_scenario_tag_pinned_ref_count"] == 3
    assert manifest["suite_thresholds"][
        "min_scenario_tag_shallow_checkout_count"
    ] == 2
    assert manifest["suite_thresholds"]["min_scenario_tag_src_layout_count"] == 1
    assert manifest["suite_thresholds"][
        "min_repository_structure_src_layout_detected_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_scenario_tag_no_python_blocker_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_scenario_tag_repair_candidate_repository_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_scenario_tag_test_execution_disabled_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_execution_result_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_repository_test_count"] == 1

    runs = manifest["runs"]
    assert len(runs) == 6
    assert any(run["repo"] == "pypa/sampleproject" for run in runs)
    assert any(
        str(run["repo"]).startswith("https://github.com/") for run in runs
    )
    tag_union = {
        tag
        for run in runs
        for tag in run.get("scenario_tags", [])
    }
    assert {
        "owner_repo_input",
        "github_url_input",
        "default_branch_discovery",
        "pinned_ref",
        "include_filter",
        "source_cache",
        "shallow_checkout",
        "agent_auto",
        "static_fallback",
        "src_layout",
        "pytest_project",
        "nox_project",
        "unittest_fallback",
        "dependency_sensitive",
        "no_python_blocker",
        "repair_candidate_repository",
        "test_execution_disabled",
    }.issubset(tag_union)

    sampleproject = runs[0]
    assert sampleproject["repo"] == "pypa/sampleproject"
    assert sampleproject["execution_profile"] == "agent-auto"
    assert sampleproject["expected_planned_repository_test_result_status"] == "pass"
    assert sampleproject["metric_thresholds"][
        "planned_repository_test_result_test_count"
    ] == 1

    requests = next(run for run in runs if run["name"].startswith("requests"))
    assert requests["execution_profile"] == "checkout"
    assert requests["run_repository_test_command"] is False
    assert "shallow_checkout" in requests["scenario_tags"]
    assert "test_execution_disabled" in requests["scenario_tags"]
    assert requests["expected_status"] == "pass"
    assert requests["expected_blocker"] == "no_static_candidates"

    pluggy = next(run for run in runs if run["name"].startswith("pluggy"))
    assert pluggy["include"] == [
        "src/pluggy/__init__.py",
        "src/pluggy/_tracing.py",
    ]
    assert pluggy["metric_thresholds"]["repository_structure_analyzed_file_count"] == 2
    assert pluggy["metric_thresholds"]["repository_structure_src_layout_package_count"] == 1
    assert (
        pluggy["metric_thresholds"][
            "repository_structure_recommended_target_prefix_present"
        ]
        == 1
    )

    click = next(run for run in runs if run["name"].startswith("click"))
    assert click["name"] == "click_utils_pinned_checkout"
    assert click["ref"] == "8.1.7"
    assert click["execution_profile"] == "checkout"
    assert click["run_repository_test_command"] is False
    assert click["expected_status"] == "pass"
    assert click["expected_blocker"] == "no_static_candidates"

    repair = next(run for run in runs if run["name"].startswith("thealgorithms"))
    assert repair["name"] == "thealgorithms_gronsfeld_repair_candidate_checkout"
    assert repair["execution_profile"] == "checkout"
    assert repair["run_repository_test_command"] is False
    assert repair["expected_status"] == "pass"
    assert repair["expected_blocker"] == "no_static_candidates"
    assert repair["metric_thresholds"][
        "repo_graph_program_graph_available"
    ] == 1

    no_python = next(run for run in runs if run["name"].startswith("octocat"))
    assert no_python["execution_profile"] == "checkout"
    assert no_python["ref"] == "master"
    assert no_python["run_repository_test_command"] is False
    assert no_python["expected_analysis_stage"] == "source_import_blocked"
    assert no_python["expected_controller_action"] == "adjust_source_filters"
    assert no_python["expected_agent_answer_testability_status"] == (
        "not_available"
    )


def test_intelligence_suite_goal_live_manifest_exposes_agent_completion_gate():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_goal_live_smoke.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == "repo_intelligence_goal_live_smoke"
    assert manifest["defaults"]["source_cache_dir"] == (
        "outputs_smoke/github_raw_source_cache"
    )
    assert manifest["defaults"]["repository_patch_generation_mode"] == "rule"
    assert manifest["defaults"]["repository_test_timeout"] == 20
    assert manifest["defaults"]["repository_checkout_timeout"] == 120
    assert manifest["suite_thresholds"]["max_command_failed_count"] == 0
    assert manifest["suite_thresholds"]["min_run_count"] == 3
    assert manifest["suite_thresholds"]["min_repo_input_kind_count"] == 2
    assert manifest["suite_thresholds"][
        "min_acceptance_gate_repair_decision_audit_pass_count"
    ] == 3
    assert manifest["suite_thresholds"][
        "min_agent_goal_repair_decision_audit_pass_count"
    ] == 3
    assert manifest["suite_thresholds"]["min_objective_compliance_pass_count"] == 3
    assert (
        manifest["suite_thresholds"][
            "min_agent_answer_question_answered_kind_count"
        ]
        == 7
    )
    assert (
        manifest["suite_thresholds"][
            "max_agent_answer_question_missing_kind_count"
        ]
        == 0
    )
    expected_answer_questions = [
        "repository_structure",
        "suspicious_functions",
        "suspicious_reason",
        "testability",
        "repairability",
        "blocker",
        "next_action",
    ]
    for question_id in expected_answer_questions:
        assert (
            manifest["suite_thresholds"][
                f"min_agent_answer_question_answered_{question_id}_count"
            ]
            == 3
        )
    assert manifest["suite_thresholds"][
        "min_blocked_agent_answer_complete_count"
    ] == 2
    assert manifest["suite_thresholds"][
        "max_blocked_agent_answer_incomplete_count"
    ] == 0
    assert manifest["suite_thresholds"][
        "max_objective_compliance_failed_section_kind_count"
    ] == 0
    assert manifest["suite_thresholds"][
        "min_repository_test_patch_validation_success_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_repository_test_repair_ready_count"] == 1
    assert manifest["suite_thresholds"][
        "min_planned_repository_test_runner_fallback_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_source_only_static_blocker_count"] == 1
    assert manifest["suite_thresholds"][
        "min_agent_auto_stop_requires_user_action_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_auto_stop_recovery_policy_provide_failing_test_bug_report_or_overlay_rule_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_auto_stop_external_input_kind_failing_test_or_bug_report_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_scenario_tag_test_execution_disabled_count"
    ] == 1

    runs = manifest["runs"]
    assert len(runs) == 3
    assert {run["repo"] for run in runs} == {
        "pypa/sampleproject",
        "https://github.com/psf/requests",
        "https://github.com/TheAlgorithms/Python",
    }
    assert {tag for run in runs for tag in run.get("scenario_tags", [])}.issuperset(
        {
            "owner_repo_input",
            "github_url_input",
            "default_branch_discovery",
            "pinned_ref",
            "include_filter",
            "agent_auto",
            "checkout_tests",
            "runner_fallback",
            "shallow_checkout",
            "dependency_sensitive",
            "static_fallback",
            "test_execution_disabled",
            "phase3_fast",
            "dynamic_overlay",
            "repair_candidate_repository",
            "patch_validation",
            "sandbox_validation",
        }
    )

    sampleproject = runs[0]
    assert sampleproject["execution_profile"] == "agent-auto"
    assert sampleproject["expected_planned_repository_test_runner"] == "unittest"
    assert sampleproject[
        "expected_planned_repository_test_runner_fallback_reason"
    ] == "missing_runner:nox"
    assert sampleproject["expected_dynamic_evidence_level"] == "passing_tests"
    assert sampleproject["expected_agent_answer_repairability_status"] == (
        "needs_dynamic_evidence_or_patch_context"
    )
    assert sampleproject["metric_thresholds"][
        "acceptance_gate_repair_decision_audit_passed"
    ] == 1

    source_only = runs[1]
    assert source_only["name"] == "requests_models_static_blocker_live_goal"
    assert source_only["execution_profile"] == "checkout"
    assert source_only["run_repository_test_command"] is False
    assert source_only["include"] == ["requests/models.py"]
    assert source_only["target_prefix"] == "requests"
    assert source_only["expected_analysis_stage"] == "phase1_repo_understanding"
    assert source_only["expected_blocker"] == "no_static_candidates"
    assert source_only["expected_controller_action"] == (
        "run_repository_tests_with_checkout"
    )
    assert source_only["expected_dynamic_evidence_level"] == "none"
    assert source_only["expected_agent_answer_repairability_status"] == "not_ready"
    assert source_only["metric_thresholds"][
        "repository_test_command_candidate_runner_kind_count"
    ] == 2

    repair = runs[2]
    assert repair["execution_profile"] == "phase3-fast"
    assert repair["include"] == ["ciphers/gronsfeld_cipher.py"]
    assert repair["checkout_repository_tests"] is True
    assert repair["expected_fault_localization_mode"] == "dynamic"
    assert repair["expected_patch_validation_status"] == "pass"
    assert repair["expected_repair_validation_scope"] == (
        "narrow_and_unchanged_regression_baseline"
    )
    assert repair["expected_agent_answer_repairability_status"] == "repair_ready"
    assert repair["metric_thresholds"][
        "repository_test_patch_validation_success_count"
    ] == 1
    assert repair["metric_thresholds"]["repository_test_repair_ready"] == 1


def test_intelligence_suite_fresh_live_manifest_forbids_report_reuse():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_fresh_live_smoke.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == "repo_intelligence_fresh_live_smoke"
    assert manifest["defaults"]["source_cache_dir"] == (
        "outputs_smoke/github_raw_source_cache"
    )
    assert manifest["defaults"]["repository_patch_generation_mode"] == "rule"
    assert manifest["defaults"]["auto_controller_max_actions"] == 2
    assert manifest["suite_thresholds"]["max_existing_report_reuse_count"] == 0
    assert manifest["suite_thresholds"]["max_cached_report_fallback_count"] == 0
    assert manifest["suite_thresholds"]["max_discovery_cache_fallback_count"] == 0
    assert manifest["suite_thresholds"]["min_run_count"] == 1
    assert manifest["suite_thresholds"]["min_discovery_cache_reuse_count"] == 1
    assert manifest["suite_thresholds"]["min_repo_input_kind_owner_repo_count"] == 1
    assert manifest["suite_thresholds"]["min_scenario_tag_fresh_live_count"] == 1
    assert manifest["suite_thresholds"]["min_agent_controller_loop_complete_count"] == 1
    assert manifest["suite_thresholds"]["min_agent_decision_timeline_complete_count"] == 1
    assert manifest["suite_thresholds"]["min_blocked_agent_answer_complete_count"] == 1
    assert manifest["suite_thresholds"][
        "max_blocked_agent_answer_incomplete_count"
    ] == 0
    assert manifest["suite_thresholds"]["min_objective_compliance_pass_count"] == 1
    assert manifest["suite_thresholds"]["min_repository_test_execution_result_count"] == 1
    assert manifest["suite_thresholds"]["min_agent_auto_action_loop_complete_count"] == 2

    runs = manifest["runs"]
    assert len(runs) == 1
    run = runs[0]
    assert run["name"] == "pypa_sampleproject_fresh_agent_auto"
    assert run["repo"] == "pypa/sampleproject"
    assert run["execution_profile"] == "agent-auto"
    assert run["prefer_cached_discovery"] is True
    assert run["seed_discovery_path"] == (
        "datasets/github_cases/discovery_cache/pypa_sampleproject.discovery.json"
    )
    assert "fresh_live" in run["scenario_tags"]
    assert run["expected_planned_repository_test_runner"] == "unittest"
    assert run["expected_planned_repository_test_runner_fallback_reason"] == (
        "missing_runner:nox"
    )
    assert run["expected_dynamic_evidence_level"] == "passing_tests"
    assert run["expected_agent_answer_testability_status"] == "overlay_not_usable"
    assert run["metric_thresholds"]["discovery_cache_reuse"] == 1
    assert run["metric_thresholds"]["discovery_cache_preferred"] == 1
    assert run["metric_thresholds"]["objective_compliance_passed"] == 1


def test_intelligence_suite_fresh_environment_manifest_gates_setup_doctor():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_fresh_environment_diagnosis_smoke.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == (
        "repo_intelligence_fresh_environment_diagnosis_smoke"
    )
    assert manifest["defaults"]["source_cache_dir"] == (
        "outputs_smoke/github_raw_source_cache"
    )
    assert manifest["defaults"]["run_repository_test_command"] is True
    assert manifest["suite_thresholds"]["max_existing_report_reuse_count"] == 0
    assert manifest["suite_thresholds"]["max_cached_report_fallback_count"] == 0
    assert manifest["suite_thresholds"]["max_discovery_cache_fallback_count"] == 0
    assert manifest["suite_thresholds"]["min_discovery_cache_reuse_count"] == 1
    assert manifest["suite_thresholds"]["min_repo_input_kind_owner_repo_count"] == 1
    assert manifest["suite_thresholds"][
        "min_scenario_tag_test_environment_blocker_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_scenario_tag_environment_repair_advice_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_environment_diagnosed_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_setup_doctor_diagnosed_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_recommended_install_command_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_planned_repository_test_runner_fallback_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_repository_test_count"] == 1

    runs = manifest["runs"]
    assert len(runs) == 1
    run = runs[0]
    assert run["name"] == "pypa_sampleproject_fresh_environment_diagnosis"
    assert run["repo"] == "pypa/sampleproject"
    assert run["execution_profile"] == "agent-auto"
    assert run["prefer_cached_discovery"] is True
    assert run["seed_discovery_path"] == (
        "datasets/github_cases/discovery_cache/pypa_sampleproject.discovery.json"
    )
    assert "test_environment_blocker" in run["scenario_tags"]
    assert "environment_repair_advice" in run["scenario_tags"]
    assert run["expected_planned_repository_test_runner"] == "unittest"
    assert run["expected_planned_repository_test_runner_fallback_reason"] == (
        "missing_runner:nox"
    )
    assert run["expected_repository_test_environment_status"] == "warning"
    assert run["expected_repository_test_setup_doctor_status"] == "blocked"
    assert run["expected_repository_test_setup_doctor_blocker"] == (
        "environment:test_tool_missing"
    )
    assert run["metric_thresholds"]["repository_test_environment_diagnosed"] == 1
    assert run["metric_thresholds"]["repository_test_setup_doctor_diagnosed"] == 1
    assert (
        run["metric_thresholds"]["repository_test_setup_doctor_blocked_check_count"]
        == 1
    )
    assert run["metric_thresholds"]["recommended_install_command_present"] == 1
    assert run["metric_thresholds"]["planned_repository_test_runner_fallback_used"] == 1
    assert run["metric_thresholds"]["planned_repository_test_result_test_count"] == 1


def test_intelligence_suite_fresh_no_test_command_manifest_gates_test_blocker():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_fresh_no_test_command_smoke.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == (
        "repo_intelligence_fresh_no_test_command_smoke"
    )
    assert manifest["defaults"]["source_cache_dir"] == (
        "outputs_smoke/github_raw_source_cache"
    )
    assert manifest["defaults"]["run_repository_test_command"] is True
    assert manifest["defaults"]["repository_test_timeout"] == 8
    assert manifest["suite_thresholds"]["max_existing_report_reuse_count"] == 0
    assert manifest["suite_thresholds"]["max_cached_report_fallback_count"] == 0
    assert manifest["suite_thresholds"]["max_discovery_cache_fallback_count"] == 0
    assert manifest["suite_thresholds"]["max_planned_repository_test_command_count"] == 0
    assert manifest["suite_thresholds"]["max_repository_test_counted_run_count"] == 0
    assert manifest["suite_thresholds"]["max_repository_test_count"] == 0
    assert manifest["suite_thresholds"]["max_repository_test_framework_kind_count"] == 0
    assert (
        manifest["suite_thresholds"][
            "max_repository_test_command_candidate_runner_kind_count"
        ]
        == 0
    )
    assert manifest["suite_thresholds"]["min_discovery_cache_reuse_count"] == 1
    assert manifest["suite_thresholds"]["min_repo_input_kind_github_url_count"] == 1
    assert manifest["suite_thresholds"][
        "min_scenario_tag_default_branch_discovery_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_scenario_tag_no_test_command_count"] == 1
    assert manifest["suite_thresholds"]["min_repository_structure_modeled_count"] == 1
    assert manifest["suite_thresholds"]["min_repo_graph_ready_count"] == 1
    assert manifest["suite_thresholds"]["min_program_graph_available_count"] == 1
    assert manifest["suite_thresholds"]["min_source_only_static_blocker_count"] == 1
    assert (
        manifest["suite_thresholds"][
            "min_repository_test_setup_doctor_blocker_test_command_no_recommended_test_command_count"
        ]
        == 1
    )

    runs = manifest["runs"]
    assert len(runs) == 1
    run = runs[0]
    assert run["name"] == "nanogpt_fresh_no_test_command"
    assert run["repo"] == "https://github.com/karpathy/nanoGPT"
    assert run["execution_profile"] == "checkout"
    assert run["prefer_cached_discovery"] is True
    assert run["seed_discovery_path"] == (
        "datasets/github_cases/discovery_cache/karpathy_nanogpt_master.discovery.json"
    )
    assert run["checkout_repository_tests"] is True
    assert "default_branch_discovery" in run["scenario_tags"]
    assert "no_test_command" in run["scenario_tags"]
    assert run["expected_analysis_stage"] == "phase1_repo_understanding"
    assert run["expected_blocker"] == "no_static_candidates"
    assert run["expected_controller_action"] == "expand_static_candidate_search"
    assert run["expected_dynamic_evidence_level"] == "not_executed"
    assert run["expected_repository_test_environment_status"] == "skipped"
    assert run["expected_repository_test_setup_doctor_status"] == "blocked"
    assert run["expected_repository_test_setup_doctor_blocker"] == (
        "test_command:no_recommended_test_command"
    )
    assert run["expected_planned_repository_test_result_status"] == "skipped"
    assert run["expected_agent_answer_testability_status"] == "overlay_not_usable"
    assert run["expected_agent_answer_repairability_status"] == "not_ready"
    assert run["metric_thresholds"]["discovery_cache_reuse"] == 1
    assert run["metric_thresholds"]["discovery_cache_preferred"] == 1
    assert run["metric_thresholds"]["repository_test_environment_diagnosed"] == 1
    assert run["metric_thresholds"]["repository_test_setup_doctor_diagnosed"] == 1
    assert (
        run["metric_thresholds"]["repository_test_setup_doctor_blocked_check_count"]
        == 3
    )


def test_intelligence_suite_fresh_no_python_manifest_gates_source_blocker():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_fresh_no_python_blocker_smoke.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == "repo_intelligence_fresh_no_python_blocker_smoke"
    assert manifest["defaults"]["source_cache_dir"] == (
        "outputs_smoke/github_raw_source_cache"
    )
    assert manifest["defaults"]["run_repository_test_command"] is False
    assert manifest["defaults"]["auto_fallback"] is False
    assert manifest["suite_thresholds"]["max_existing_report_reuse_count"] == 0
    assert manifest["suite_thresholds"]["max_cached_report_fallback_count"] == 0
    assert manifest["suite_thresholds"]["max_discovery_cache_fallback_count"] == 0
    assert manifest["suite_thresholds"]["max_repository_test_execution_result_count"] == 0
    assert manifest["suite_thresholds"]["max_repository_structure_modeled_count"] == 0
    assert manifest["suite_thresholds"]["max_repo_graph_ready_count"] == 0
    assert manifest["suite_thresholds"]["max_program_graph_available_count"] == 0
    assert manifest["suite_thresholds"]["min_discovery_cache_reuse_count"] == 1
    assert manifest["suite_thresholds"]["min_repo_input_kind_github_url_count"] == 1
    assert manifest["suite_thresholds"]["min_scenario_tag_no_python_blocker_count"] == 1
    assert manifest["suite_thresholds"]["min_source_import_blocked_count"] == 1
    assert manifest["suite_thresholds"]["min_blocked_agent_answer_complete_count"] == 1

    runs = manifest["runs"]
    assert len(runs) == 1
    run = runs[0]
    assert run["name"] == "octocat_hello_world_fresh_no_python_blocker"
    assert run["repo"] == "https://github.com/octocat/Hello-World"
    assert run["ref"] == "master"
    assert run["execution_profile"] == "checkout"
    assert run["prefer_cached_discovery"] is True
    assert run["seed_discovery_path"] == (
        "datasets/github_cases/discovery_cache/octocat_hello_world_master.discovery.json"
    )
    assert run["run_repository_test_command"] is False
    assert run["auto_fallback"] is False
    assert "no_python_blocker" in run["scenario_tags"]
    assert run["expected_analysis_stage"] == "source_import_blocked"
    assert run["expected_blocker"] == "source_import_or_parse_missing"
    assert run["expected_controller_action"] == "adjust_source_filters"
    assert run["expected_agent_answer_testability_status"] == "not_available"
    assert run["expected_agent_answer_repairability_status"] == "not_ready"
    assert run["metric_thresholds"]["discovery_cache_reuse"] == 1
    assert run["metric_thresholds"]["discovery_cache_preferred"] == 1
    assert run["metric_thresholds"]["agent_answer_coverage_answered_count"] == 7
    assert run["metric_thresholds"]["objective_compliance_passed"] == 1


def test_intelligence_suite_fresh_static_blocker_manifest_gates_url_slice():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_fresh_static_blocker_smoke.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == (
        "repo_intelligence_fresh_static_blocker_smoke"
    )
    assert manifest["defaults"]["source_cache_dir"] == (
        "outputs_smoke/github_raw_source_cache"
    )
    assert manifest["defaults"]["run_repository_test_command"] is False
    assert manifest["suite_thresholds"]["max_existing_report_reuse_count"] == 0
    assert manifest["suite_thresholds"]["max_cached_report_fallback_count"] == 0
    assert manifest["suite_thresholds"]["max_discovery_cache_fallback_count"] == 0
    assert manifest["suite_thresholds"]["max_repository_test_execution_result_count"] == 0
    assert manifest["suite_thresholds"]["min_discovery_cache_reuse_count"] == 1
    assert manifest["suite_thresholds"]["min_repo_input_kind_github_url_count"] == 1
    assert manifest["suite_thresholds"]["min_scenario_tag_include_filter_count"] == 1
    assert manifest["suite_thresholds"]["min_source_only_static_blocker_count"] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_command_candidate_runner_kind_count"
    ] == 2
    assert manifest["suite_thresholds"]["min_blocked_agent_answer_complete_count"] == 1

    runs = manifest["runs"]
    assert len(runs) == 1
    run = runs[0]
    assert run["name"] == "requests_models_fresh_static_blocker"
    assert run["repo"] == "https://github.com/psf/requests"
    assert run["ref"] == "v2.31.0"
    assert run["execution_profile"] == "checkout"
    assert run["prefer_cached_discovery"] is True
    assert run["seed_discovery_path"] == (
        "datasets/github_cases/discovery_cache/psf_requests_v2_31_0.discovery.json"
    )
    assert run["run_repository_test_command"] is False
    assert run["include"] == ["requests/models.py"]
    assert run["target_prefix"] == "requests"
    assert "fresh_live" in run["scenario_tags"]
    assert run["expected_analysis_stage"] == "phase1_repo_understanding"
    assert run["expected_blocker"] == "no_static_candidates"
    assert run["expected_agent_answer_testability_status"] == (
        "can_attempt_with_checkout_or_setup"
    )
    assert run["expected_agent_answer_repairability_status"] == "not_ready"
    assert run["metric_thresholds"]["discovery_cache_reuse"] == 1
    assert run["metric_thresholds"]["discovery_cache_preferred"] == 1
    assert run["metric_thresholds"]["repository_test_command_candidate_runner_kind_count"] == 2


def test_intelligence_suite_fresh_src_layout_manifest_gates_package_layout():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_fresh_src_layout_smoke.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == "repo_intelligence_fresh_src_layout_smoke"
    assert manifest["defaults"]["source_cache_dir"] == (
        "outputs_smoke/github_raw_source_cache"
    )
    assert manifest["defaults"]["run_repository_test_command"] is False
    assert manifest["suite_thresholds"]["max_existing_report_reuse_count"] == 0
    assert manifest["suite_thresholds"]["max_cached_report_fallback_count"] == 0
    assert manifest["suite_thresholds"]["max_discovery_cache_fallback_count"] == 0
    assert manifest["suite_thresholds"]["max_repository_test_execution_result_count"] == 0
    assert manifest["suite_thresholds"]["min_discovery_cache_reuse_count"] == 1
    assert manifest["suite_thresholds"]["min_repo_input_kind_github_url_count"] == 1
    assert manifest["suite_thresholds"]["min_scenario_tag_src_layout_count"] == 1
    assert manifest["suite_thresholds"][
        "min_repository_structure_src_layout_detected_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_phase2_static_graph_fault_localization_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_blocked_agent_answer_complete_count"] == 1

    runs = manifest["runs"]
    assert len(runs) == 1
    run = runs[0]
    assert run["name"] == "pluggy_tracing_fresh_src_layout"
    assert run["repo"] == "https://github.com/pytest-dev/pluggy"
    assert run["ref"] == "7fce99cb955846901b22b051909aa4f30dc16128"
    assert run["execution_profile"] == "checkout"
    assert run["prefer_cached_discovery"] is True
    assert run["seed_discovery_path"] == (
        "datasets/github_cases/discovery_cache/pytest_dev_pluggy_7fce99c.discovery.json"
    )
    assert run["run_repository_test_command"] is False
    assert run["include"] == [
        "src/pluggy/__init__.py",
        "src/pluggy/_tracing.py",
    ]
    assert "src_layout" in run["scenario_tags"]
    assert run["expected_analysis_stage"] == "phase2_static_graph_fault_localization"
    assert run["expected_blocker"] == "dynamic_evidence_not_usable"
    assert run["expected_controller_action"] == "discover_repository_tests"
    assert run["expected_agent_answer_testability_status"] == (
        "can_attempt_with_checkout_or_setup"
    )
    assert run["expected_agent_answer_repairability_status"] == (
        "needs_dynamic_evidence_or_patch_context"
    )
    assert run["metric_thresholds"]["discovery_cache_reuse"] == 1
    assert run["metric_thresholds"]["discovery_cache_preferred"] == 1
    assert run["metric_thresholds"]["repository_structure_package_root_count"] == 1
    assert run["metric_thresholds"]["repository_structure_src_layout_package_count"] == 1
    assert (
        run["metric_thresholds"][
            "repository_structure_recommended_target_prefix_present"
        ]
        == 1
    )


def test_intelligence_suite_fresh_exclude_filter_manifest_gates_filter_effect():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_fresh_exclude_filter_smoke.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == "repo_intelligence_fresh_exclude_filter_smoke"
    assert manifest["defaults"]["source_cache_dir"] == (
        "outputs_smoke/github_raw_source_cache"
    )
    assert manifest["defaults"]["run_repository_test_command"] is False
    assert manifest["suite_thresholds"]["max_existing_report_reuse_count"] == 0
    assert manifest["suite_thresholds"]["max_cached_report_fallback_count"] == 0
    assert manifest["suite_thresholds"]["max_discovery_cache_fallback_count"] == 0
    assert manifest["suite_thresholds"]["max_repository_test_execution_result_count"] == 0
    assert manifest["suite_thresholds"]["min_discovery_cache_reuse_count"] == 1
    assert manifest["suite_thresholds"]["min_repo_input_kind_github_url_count"] == 1
    assert manifest["suite_thresholds"]["min_scenario_tag_include_filter_count"] == 1
    assert manifest["suite_thresholds"]["min_scenario_tag_exclude_filter_count"] == 1
    assert manifest["suite_thresholds"]["min_exclude_filter_requested_count"] == 1
    assert manifest["suite_thresholds"]["min_exclude_filter_effective_count"] == 1
    assert manifest["suite_thresholds"][
        "min_phase2_static_graph_fault_localization_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_blocked_agent_answer_complete_count"] == 1

    runs = manifest["runs"]
    assert len(runs) == 1
    run = runs[0]
    assert run["name"] == "pluggy_tracing_fresh_exclude_filter"
    assert run["repo"] == "https://github.com/pytest-dev/pluggy"
    assert run["ref"] == "7fce99cb955846901b22b051909aa4f30dc16128"
    assert run["execution_profile"] == "checkout"
    assert run["prefer_cached_discovery"] is True
    assert run["seed_discovery_path"] == (
        "datasets/github_cases/discovery_cache/pytest_dev_pluggy_7fce99c.discovery.json"
    )
    assert run["include"] == [
        "src/pluggy/__init__.py",
        "src/pluggy/_tracing.py",
    ]
    assert run["exclude"] == ["src/pluggy/__init__.py"]
    assert "exclude_filter" in run["scenario_tags"]
    assert run["expected_analysis_stage"] == "phase2_static_graph_fault_localization"
    assert run["expected_blocker"] == "dynamic_evidence_not_usable"
    assert run["expected_controller_action"] == "discover_repository_tests"
    assert run["metric_thresholds"]["agent_invocation_include_count"] == 2
    assert run["metric_thresholds"]["agent_invocation_exclude_count"] == 1
    assert (
        run["metric_thresholds"][
            "agent_invocation_exclude_reduced_selected_sources"
        ]
        == 1
    )
    assert run["metric_thresholds"]["selected_source_count"] == 1
    assert run["metric_thresholds"]["repository_structure_analyzed_file_count"] == 1


def test_intelligence_suite_agent_matrix_tags_can_gate_synthetic_runs():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_agent_matrix.example.json"
        ).read_text(encoding="utf-8")
    )
    tag_thresholds = {
        key: value
        for key, value in manifest["suite_thresholds"].items()
        if key.startswith("min_repo_input_kind")
        or key.startswith("min_scenario_tag")
    }
    tag_thresholds["min_run_count"] = manifest["suite_thresholds"]["min_run_count"]
    synthetic_runs = [
        GitHubRepoIntelligenceSuiteRunResult(
            name=run["name"],
            repo=run["repo"],
            output_dir=f"out/{run['name']}",
            report_path=f"out/{run['name']}/github_repo_intelligence.json",
            status="pass",
            passed=True,
            expected_status="pass",
            expectation_passed=True,
            metrics={
                "status": "pass",
                "repo_input_kind": _repo_input_kind(run["repo"]),
                "scenario_tags": run.get("scenario_tags", []),
            },
            metric_checks=[],
            expectation_checks=[],
            command_args=[],
        )
        for run in manifest["runs"]
    ]

    summary = _suite_summary(synthetic_runs, suite_thresholds=tag_thresholds)
    markdown = render_github_repo_intelligence_suite_markdown(
        GitHubRepoIntelligenceSuiteReport(
            suite_name="matrix_tags",
            manifest_path="manifest.json",
            output_dir="out",
            passed=True,
            runs=synthetic_runs,
            summary=summary,
        )
    )

    assert summary["run_count"] == 6
    assert summary["repo_input_kind_counts"] == {
        "github_url": 5,
        "owner_repo": 1,
    }
    assert summary["scenario_tag_kind_count"] >= 17
    assert summary["scenario_tag_owner_repo_input_count"] == 1
    assert summary["scenario_tag_github_url_input_count"] == 5
    assert summary["scenario_tag_repair_candidate_repository_count"] == 1
    assert summary["scenario_tag_no_python_blocker_count"] == 1
    assert summary["scenario_tag_test_execution_disabled_count"] == 4
    assert summary["suite_threshold_failed_count"] == 0
    assert "Repo Input Kinds: github_url=5, owner_repo=1" in markdown
    assert "Scenario Tags:" in markdown
    assert "repair_candidate_repository=1" in markdown
    assert "no_python_blocker=1" in markdown


def test_intelligence_suite_flags_scenario_coverage_blockers():
    result = GitHubRepoIntelligenceSuiteRunResult(
        name="github_fetch_blocked",
        repo="https://github.com/example/project",
        output_dir="out/github_fetch_blocked",
        report_path="out/github_fetch_blocked/github_repo_intelligence.json",
        status="pass",
        passed=True,
        expected_status="pass",
        expectation_passed=True,
        metrics={
            "status": "pass",
            "repo_input_kind": "github_url",
            "scenario_tags": ["github_url_input", "src_layout"],
            "blocker": "github_fetch:github_api_error",
        },
        metric_checks=[],
        expectation_checks=[],
        command_args=[],
    )

    summary = _suite_summary(
        [result],
        suite_thresholds={"max_scenario_coverage_blocked_count": 0},
    )
    markdown = render_github_repo_intelligence_suite_markdown(
        GitHubRepoIntelligenceSuiteReport(
            suite_name="coverage_blocker_suite",
            manifest_path="manifest.json",
            output_dir="out",
            passed=False,
            runs=[result],
            summary=summary,
        )
    )

    assert summary["scenario_coverage_blocked_count"] == 1
    assert summary["scenario_coverage_blocked_runs"] == ["github_fetch_blocked"]
    assert summary["scenario_coverage_blocker_counts"] == {
        "github_fetch:github_api_error": 1
    }
    assert summary[
        "scenario_coverage_blocker_github_fetch_github_api_error_count"
    ] == 1
    assert summary["suite_threshold_failed_count"] == 1
    assert "Scenario Coverage Blocked Runs: 1" in markdown
    assert (
        "Scenario Coverage Blockers: github_fetch:github_api_error=1"
        in markdown
    )


def test_intelligence_suite_repair_example_manifest_exposes_patch_validation_goal():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_repair_smoke.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == "repo_intelligence_repair_smoke"
    assert manifest["defaults"]["execution_profile"] == "phase3-fast"
    assert manifest["defaults"]["repository_test_failure_overlay_candidate_limit"] == 5
    assert manifest["defaults"]["repository_patch_generation_mode"] == "rule"
    assert manifest["suite_thresholds"]["max_existing_report_reuse_count"] == 0
    assert manifest["suite_thresholds"]["max_cached_report_fallback_count"] == 0
    assert manifest["suite_thresholds"]["max_scenario_coverage_blocked_count"] == 0
    assert manifest["suite_thresholds"]["min_artifact_required_ready_count"] == 1
    assert manifest["suite_thresholds"]["min_acceptance_gate_pass_count"] == 1
    assert manifest["suite_thresholds"]["min_agent_goal_readiness_pass_count"] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_patch_validation_success_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_answer_coverage_complete_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_controller_loop_complete_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_repair_ready_count"
    ] == 1
    assert manifest["runs"][0]["expected_status"] == "pass"
    assert manifest["runs"][0]["expected_patch_validation_status"] == "pass"
    assert manifest["runs"][0]["expected_patch_generation_mode"] == "rule"
    assert manifest["runs"][0]["expected_llm_patch_generation_status"] == "disabled"
    assert manifest["runs"][0]["expected_patch_safety_gate_status"] == "pass"
    assert manifest["runs"][0]["expected_repair_validation_scope"] == (
        "narrow_and_unchanged_regression_baseline"
    )
    assert manifest["runs"][0]["metric_thresholds"][
        "repository_test_patch_candidate_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "artifact_inventory_required_file_nonempty_count"
    ] == 34
    assert manifest["runs"][0]["metric_thresholds"][
        "repository_patch_generator_rule_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "acceptance_gate_repair_decision_audit_passed"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "agent_goal_repair_decision_audit_passed"
    ] == 1


def test_intelligence_suite_agent_auto_repair_manifest_exposes_agent_goal():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_agent_auto_repair_smoke.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == "repo_intelligence_agent_auto_repair_smoke"
    assert manifest["defaults"]["agent"] is True
    assert manifest["defaults"]["auto_fallback"] is False
    assert manifest["defaults"]["repository_test_failure_overlay_candidate_limit"] == 5
    assert manifest["defaults"]["repository_patch_generation_mode"] == "rule"
    assert manifest["defaults"]["repository_patch_candidate_variant_allowlist"] == [
        "insert_len_zero_guard"
    ]
    assert manifest["defaults"]["repository_test_reflection_mode"] == "rule"
    assert manifest["defaults"]["auto_controller_max_actions"] == 4
    assert manifest["suite_thresholds"]["max_existing_report_reuse_count"] == 0
    assert manifest["suite_thresholds"]["max_cached_report_fallback_count"] == 0
    assert manifest["suite_thresholds"]["max_scenario_coverage_blocked_count"] == 0
    assert manifest["suite_thresholds"]["min_artifact_required_ready_count"] == 1
    assert manifest["suite_thresholds"]["min_acceptance_gate_pass_count"] == 1
    assert manifest["suite_thresholds"]["min_agent_goal_readiness_pass_count"] == 1
    assert manifest["suite_thresholds"][
        "min_agent_auto_action_loop_complete_run_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_decision_timeline_complete_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_agent_shortcut_count"] == 1
    assert manifest["suite_thresholds"]["min_objective_compliance_pass_count"] == 1
    assert manifest["suite_thresholds"][
        "max_objective_compliance_failed_section_kind_count"
    ] == 0
    assert manifest["suite_thresholds"][
        "min_agent_auto_patch_validation_reached_action_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_auto_repair_ready_action_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_auto_repair_goal_reached_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_patch_validation_success_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_patch_validation_reflection_candidate_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_patch_validation_successful_reflection_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_patch_validation_failure_type_success_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_patch_validation_failure_type_test_failure_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_reflection_initial_failure_type_test_failure_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_reflection_failure_type_success_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_reflection_parent_failure_type_test_failure_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_successful_reflection_parent_failure_type_test_failure_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_auto_reflection_candidate_action_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_auto_successful_reflection_action_count"
    ] == 1
    assert manifest["runs"][0]["expected_agent_shortcut"] is True
    assert manifest["runs"][0]["expected_execution_profile"] == "agent-auto"
    assert manifest["runs"][0]["expected_patch_validation_status"] == "pass"
    assert manifest["runs"][0]["expected_patch_generation_mode"] == "rule"
    assert manifest["runs"][0]["expected_llm_patch_generation_status"] == "disabled"
    assert manifest["runs"][0]["expected_patch_safety_gate_status"] == "pass"
    assert manifest["runs"][0]["metric_thresholds"][
        "agent_auto_patch_validation_reached_action_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "agent_decision_timeline_complete"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "agent_auto_repair_ready_action_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "repository_test_patch_validation_success_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "repository_patch_candidate_variant_filter_kept_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "repository_patch_candidate_variant_filter_dropped_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "repository_test_patch_validation_reflection_candidate_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "repository_test_patch_validation_successful_reflection_count"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "acceptance_gate_repair_decision_audit_passed"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "agent_goal_repair_decision_audit_passed"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"]["objective_compliance_passed"] == 1


def test_intelligence_suite_agent_cli_default_output_repair_manifest_exposes_repair_goal():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_agent_cli_default_output_repair_smoke.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == (
        "repo_intelligence_agent_cli_default_output_repair_smoke"
    )
    assert "without an output_dir positional argument" in manifest["description"]
    assert manifest["defaults"]["agent"] is True
    assert manifest["defaults"]["use_cli_default_output_dir"] is True
    assert manifest["defaults"]["auto_fallback"] is False
    assert manifest["defaults"]["repository_test_failure_overlay_candidate_limit"] == 5
    assert manifest["defaults"]["repository_patch_generation_mode"] == "rule"
    assert manifest["defaults"]["repository_patch_candidate_variant_allowlist"] == [
        "insert_len_zero_guard"
    ]
    assert manifest["defaults"]["repository_test_reflection_mode"] == "rule"
    assert manifest["defaults"]["auto_controller_max_actions"] == 4
    assert manifest["suite_thresholds"]["min_run_count"] == 1
    assert manifest["suite_thresholds"]["min_agent_shortcut_count"] == 1
    assert manifest["suite_thresholds"]["min_output_dir_defaulted_count"] == 1
    assert manifest["suite_thresholds"]["min_agent_default_output_dir_count"] == 1
    assert manifest["suite_thresholds"]["max_existing_report_reuse_count"] == 0
    assert manifest["suite_thresholds"]["max_cached_report_fallback_count"] == 0
    assert manifest["suite_thresholds"]["max_scenario_coverage_blocked_count"] == 0
    assert manifest["suite_thresholds"]["min_artifact_required_ready_count"] == 1
    assert manifest["suite_thresholds"]["min_acceptance_gate_pass_count"] == 1
    assert manifest["suite_thresholds"]["min_agent_goal_readiness_pass_count"] == 1
    assert manifest["suite_thresholds"][
        "min_agent_controller_loop_complete_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_decision_timeline_complete_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_answer_coverage_complete_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_objective_compliance_pass_count"] == 1
    assert manifest["suite_thresholds"][
        "max_objective_compliance_failed_section_kind_count"
    ] == 0
    assert manifest["suite_thresholds"][
        "min_agent_auto_patch_validation_reached_action_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_auto_repair_ready_action_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_auto_repair_goal_reached_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_patch_validation_success_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_patch_validation_reflection_candidate_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_patch_validation_successful_reflection_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_successful_reflection_parent_failure_type_test_failure_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_auto_successful_reflection_action_count"
    ] == 1

    run = manifest["runs"][0]
    assert run["name"] == "thealgorithms_gronsfeld_repo_only_default_output_repair"
    assert run["repo"] == "https://github.com/TheAlgorithms/Python"
    assert run["expected_agent_shortcut"] is True
    assert run["expected_execution_profile"] == "agent-auto"
    assert run["expected_patch_validation_status"] == "pass"
    assert run["expected_patch_generation_mode"] == "rule"
    assert run["expected_llm_patch_generation_status"] == "disabled"
    assert run["expected_patch_safety_gate_status"] == "pass"
    assert run["expected_repair_validation_scope"] == (
        "narrow_and_unchanged_regression_baseline"
    )
    assert "cli_default_output" in run["scenario_tags"]
    assert "patch_validation" in run["scenario_tags"]
    assert "reflection_repair" in run["scenario_tags"]
    assert run["metric_thresholds"]["output_dir_defaulted"] == 1
    assert run["metric_thresholds"][
        "agent_auto_patch_validation_reached_action_count"
    ] == 1
    assert run["metric_thresholds"][
        "agent_auto_repair_ready_action_count"
    ] == 1
    assert run["metric_thresholds"][
        "repository_test_patch_validation_success_count"
    ] == 1
    assert run["metric_thresholds"][
        "repository_test_patch_validation_reflection_candidate_count"
    ] == 1
    assert run["metric_thresholds"][
        "repository_test_patch_validation_successful_reflection_count"
    ] == 1
    assert run["metric_thresholds"][
        "agent_auto_successful_reflection_action_count"
    ] == 1
    assert run["metric_thresholds"]["objective_compliance_passed"] == 1


def test_intelligence_suite_agent_cli_default_output_manifest_exposes_repo_only_gate():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_agent_cli_default_output_smoke.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == (
        "repo_intelligence_agent_cli_default_output_smoke"
    )
    assert "without an output_dir positional argument" in manifest["description"]
    assert manifest["defaults"]["agent"] is True
    assert manifest["defaults"]["use_cli_default_output_dir"] is True
    assert manifest["defaults"]["auto_controller_max_actions"] == 4
    assert manifest["defaults"]["repository_patch_generation_mode"] == "rule"
    assert manifest["suite_thresholds"]["min_run_count"] == 1
    assert manifest["suite_thresholds"]["min_agent_shortcut_count"] == 1
    assert manifest["suite_thresholds"]["min_output_dir_defaulted_count"] == 1
    assert manifest["suite_thresholds"]["min_agent_default_output_dir_count"] == 1
    assert manifest["suite_thresholds"][
        "min_agent_controller_loop_complete_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_decision_timeline_complete_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_answer_coverage_complete_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_objective_compliance_pass_count"] == 1
    assert manifest["suite_thresholds"][
        "max_objective_compliance_failed_section_kind_count"
    ] == 0
    assert manifest["suite_thresholds"]["min_agent_auto_complete_loop_count"] == 1
    assert manifest["suite_thresholds"][
        "min_agent_auto_action_loop_complete_run_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_auto_action_loop_complete_count"
    ] == 2
    assert manifest["runs"][0]["name"] == "pypa_sampleproject_repo_only_agent_cli"
    assert manifest["runs"][0]["repo"] == "pypa/sampleproject"
    assert manifest["runs"][0]["expected_status"] == "pass"
    assert manifest["runs"][0]["expected_agent_shortcut"] is True
    assert manifest["runs"][0]["expected_execution_profile"] == "agent-auto"
    assert manifest["runs"][0]["metric_thresholds"]["output_dir_defaulted"] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "agent_decision_timeline_complete"
    ] == 1
    assert manifest["runs"][0]["metric_thresholds"][
        "agent_auto_action_loop_complete_count"
    ] == 2
    assert manifest["runs"][0]["metric_thresholds"][
        "objective_compliance_passed"
    ] == 1


def test_intelligence_suite_agent_cli_default_output_matrix_exposes_arbitrary_repo_gate():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_agent_cli_default_output_matrix.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == (
        "repo_intelligence_agent_cli_default_output_matrix"
    )
    assert manifest["defaults"]["agent"] is True
    assert manifest["defaults"]["use_cli_default_output_dir"] is True
    assert manifest["defaults"]["auto_controller_max_actions"] == 4
    assert manifest["suite_thresholds"]["min_run_count"] == 3
    assert manifest["suite_thresholds"]["min_repo_input_kind_count"] == 2
    assert manifest["suite_thresholds"]["min_repo_input_kind_owner_repo_count"] == 1
    assert manifest["suite_thresholds"]["min_repo_input_kind_github_url_count"] == 2
    assert manifest["suite_thresholds"]["min_scenario_tag_cli_default_output_count"] == 3
    assert manifest["suite_thresholds"]["min_scenario_tag_src_layout_count"] == 1
    assert manifest["suite_thresholds"]["min_scenario_tag_no_python_blocker_count"] == 1
    assert manifest["suite_thresholds"][
        "min_scenario_tag_passing_tests_dynamic_evidence_count"
    ] == 2
    assert manifest["suite_thresholds"]["min_agent_shortcut_count"] == 3
    assert manifest["suite_thresholds"]["min_output_dir_defaulted_count"] == 3
    assert manifest["suite_thresholds"]["min_agent_default_output_dir_count"] == 3
    assert manifest["suite_thresholds"][
        "min_agent_controller_loop_complete_count"
    ] == 3
    assert manifest["suite_thresholds"][
        "min_agent_decision_timeline_complete_count"
    ] == 3
    assert manifest["suite_thresholds"][
        "min_agent_answer_coverage_complete_count"
    ] == 3
    assert manifest["suite_thresholds"]["min_objective_compliance_pass_count"] == 3
    assert manifest["suite_thresholds"][
        "max_objective_compliance_failed_section_kind_count"
    ] == 0
    assert manifest["suite_thresholds"]["min_repository_structure_modeled_count"] == 2
    assert manifest["suite_thresholds"]["min_repo_graph_ready_count"] == 2
    assert manifest["suite_thresholds"]["min_source_import_blocked_count"] == 1
    assert manifest["suite_thresholds"]["min_repository_test_execution_result_count"] == 2
    assert manifest["suite_thresholds"]["min_repository_test_counted_run_count"] == 2
    assert manifest["suite_thresholds"]["min_repository_test_count_source_kind_count"] == 2

    runs = manifest["runs"]
    assert len(runs) == 3
    assert {run["name"] for run in runs} == {
        "pypa_sampleproject_repo_only_default_output",
        "pluggy_src_layout_repo_only_default_output",
        "octocat_no_python_repo_only_default_output",
    }
    assert all(run["expected_agent_shortcut"] is True for run in runs)
    assert all(run["expected_execution_profile"] == "agent-auto" for run in runs)
    assert all(run["metric_thresholds"]["output_dir_defaulted"] == 1 for run in runs)

    owner_repo_run = runs[0]
    assert owner_repo_run["repo"] == "pypa/sampleproject"
    assert "owner_repo_input" in owner_repo_run["scenario_tags"]
    assert "default_branch_discovery" in owner_repo_run["scenario_tags"]
    assert "passing_tests_dynamic_evidence" in owner_repo_run["scenario_tags"]
    assert owner_repo_run["expected_planned_repository_test_result_status"] == "pass"
    assert owner_repo_run["metric_thresholds"][
        "planned_repository_test_result_passed"
    ] == 1

    src_layout_run = runs[1]
    assert src_layout_run["repo"] == "https://github.com/pytest-dev/pluggy"
    assert "src_layout" in src_layout_run["scenario_tags"]
    assert "include_filter" in src_layout_run["scenario_tags"]
    assert "passing_tests_dynamic_evidence" in src_layout_run["scenario_tags"]
    assert src_layout_run["run_repository_test_command"] is False
    assert src_layout_run["expected_blocker"] == (
        "dynamic_evidence_not_usable:passing_tests"
    )
    assert src_layout_run["expected_controller_action"] == (
        "extend_failure_overlay_or_provide_bug_report"
    )
    assert src_layout_run["expected_dynamic_evidence_level"] == "passing_tests"
    assert src_layout_run["expected_agent_answer_testability_status"] == (
        "overlay_not_usable"
    )
    assert src_layout_run["expected_planned_repository_test_result_status"] == "pass"
    assert src_layout_run["metric_thresholds"][
        "repository_structure_src_layout_package_count"
    ] == 1
    assert src_layout_run["metric_thresholds"][
        "repo_graph_program_graph_available"
    ] == 1
    assert src_layout_run["metric_thresholds"][
        "planned_repository_test_result_passed"
    ] == 1

    no_python_run = runs[2]
    assert no_python_run["repo"] == "https://github.com/octocat/Hello-World"
    assert no_python_run["auto_fallback"] is False
    assert "no_python_blocker" in no_python_run["scenario_tags"]
    assert no_python_run["expected_analysis_stage"] == "source_import_blocked"
    assert no_python_run["expected_blocker"] == "source_import_or_parse_missing"
    assert no_python_run["expected_controller_action"] == "adjust_source_filters"
    assert no_python_run["expected_agent_answer_repairability_status"] == "not_ready"


def test_intelligence_suite_agent_cli_default_output_blocker_matrix_exposes_diagnostics_gate():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_agent_cli_default_output_blocker_matrix.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == (
        "repo_intelligence_agent_cli_default_output_blocker_matrix"
    )
    assert manifest["defaults"]["agent"] is True
    assert manifest["defaults"]["use_cli_default_output_dir"] is True
    assert manifest["defaults"]["run_repository_test_command"] is True
    assert manifest["suite_thresholds"]["min_run_count"] == 2
    assert manifest["suite_thresholds"]["min_agent_shortcut_count"] == 2
    assert manifest["suite_thresholds"]["min_output_dir_defaulted_count"] == 2
    assert manifest["suite_thresholds"]["min_agent_default_output_dir_count"] == 2
    assert manifest["suite_thresholds"]["min_repo_input_kind_count"] == 2
    assert manifest["suite_thresholds"]["min_scenario_tag_cli_default_output_count"] == 2
    assert manifest["suite_thresholds"][
        "min_scenario_tag_test_environment_blocker_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_scenario_tag_environment_repair_advice_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_scenario_tag_no_test_command_count"] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_environment_diagnosed_count"
    ] == 2
    assert manifest["suite_thresholds"][
        "min_repository_test_setup_doctor_diagnosed_count"
    ] == 2
    assert manifest["suite_thresholds"][
        "min_repository_test_recommended_install_command_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_planned_repository_test_runner_fallback_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_setup_doctor_blocker_environment_test_tool_missing_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_setup_doctor_blocker_test_command_no_recommended_test_command_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_objective_compliance_pass_count"] == 2
    assert manifest["suite_thresholds"][
        "max_objective_compliance_failed_section_kind_count"
    ] == 0

    runs = manifest["runs"]
    assert len(runs) == 2
    assert all(run["expected_agent_shortcut"] is True for run in runs)
    assert all(run["expected_execution_profile"] == "agent-auto" for run in runs)
    assert all(run["metric_thresholds"]["output_dir_defaulted"] == 1 for run in runs)

    environment_run = runs[0]
    assert environment_run["name"] == "pypa_sampleproject_repo_only_environment_blocker"
    assert environment_run["repo"] == "pypa/sampleproject"
    assert "test_environment_blocker" in environment_run["scenario_tags"]
    assert "environment_repair_advice" in environment_run["scenario_tags"]
    assert environment_run["expected_planned_repository_test_runner"] == "unittest"
    assert environment_run["expected_planned_repository_test_runner_fallback_reason"] == (
        "missing_runner:nox"
    )
    assert environment_run["expected_repository_test_setup_doctor_blocker"] == (
        "environment:test_tool_missing"
    )
    assert environment_run["metric_thresholds"][
        "recommended_install_command_present"
    ] == 1
    assert environment_run["metric_thresholds"][
        "planned_repository_test_runner_fallback_used"
    ] == 1

    no_test_run = runs[1]
    assert no_test_run["name"] == "nanogpt_repo_only_no_test_command_blocker"
    assert no_test_run["repo"] == "https://github.com/karpathy/nanoGPT"
    assert "no_test_command" in no_test_run["scenario_tags"]
    assert no_test_run["checkout_repository_tests"] is True
    assert no_test_run["repository_test_timeout"] == 8
    assert no_test_run["expected_dynamic_evidence_level"] == "not_executed"
    assert no_test_run["expected_repository_test_environment_status"] == "skipped"
    assert no_test_run["expected_repository_test_setup_doctor_blocker"] == (
        "test_command:no_recommended_test_command"
    )
    assert no_test_run["expected_agent_answer_repairability_status"] == "not_ready"
    assert no_test_run["metric_thresholds"][
        "repository_test_setup_doctor_blocked_check_count"
    ] == 3
    assert no_test_run["metric_thresholds"]["repo_graph_program_graph_available"] == 1


def test_intelligence_suite_agent_cli_default_output_acceptance_manifest_covers_goal():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_agent_cli_default_output_acceptance.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == (
        "repo_intelligence_agent_cli_default_output_acceptance"
    )
    assert manifest["defaults"]["agent"] is True
    assert manifest["defaults"]["use_cli_default_output_dir"] is True
    assert manifest["suite_thresholds"]["min_run_count"] == 5
    assert manifest["suite_thresholds"]["min_agent_shortcut_count"] == 5
    assert manifest["suite_thresholds"]["min_output_dir_defaulted_count"] == 5
    assert manifest["suite_thresholds"]["min_agent_default_output_dir_count"] == 5
    assert manifest["suite_thresholds"]["min_repo_input_kind_count"] == 2
    assert manifest["suite_thresholds"]["min_repo_input_kind_owner_repo_count"] == 1
    assert manifest["suite_thresholds"]["min_repo_input_kind_github_url_count"] == 4
    assert manifest["suite_thresholds"]["max_discovery_cache_fallback_count"] == 0
    assert manifest["suite_thresholds"]["min_scenario_tag_source_cache_count"] >= 3
    assert manifest["suite_thresholds"]["min_scenario_tag_src_layout_count"] == 1
    assert manifest["suite_thresholds"]["min_scenario_tag_no_python_blocker_count"] == 1
    assert manifest["suite_thresholds"]["min_scenario_tag_no_test_command_count"] == 1
    assert manifest["suite_thresholds"][
        "min_scenario_tag_test_environment_blocker_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_scenario_tag_repair_candidate_repository_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_scenario_tag_patch_validation_count"] == 1
    assert manifest["suite_thresholds"]["min_scenario_tag_reflection_repair_count"] == 1
    assert manifest["suite_thresholds"]["min_objective_compliance_pass_count"] == 5
    assert manifest["suite_thresholds"][
        "max_objective_compliance_failed_section_kind_count"
    ] == 0
    assert manifest["suite_thresholds"]["min_repository_structure_modeled_count"] == 4
    assert manifest["suite_thresholds"]["min_repo_graph_ready_count"] == 4
    assert manifest["suite_thresholds"]["min_program_graph_available_count"] == 4
    assert manifest["suite_thresholds"]["min_source_import_blocked_count"] == 1
    assert manifest["suite_thresholds"]["min_source_only_static_blocker_count"] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_setup_doctor_blocker_environment_test_tool_missing_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_setup_doctor_blocker_test_command_no_recommended_test_command_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_patch_validation_success_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_patch_validation_successful_reflection_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_auto_successful_reflection_action_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_agent_auto_complete_loop_count"] == 3
    assert manifest["suite_thresholds"][
        "min_agent_auto_action_loop_complete_run_count"
    ] == 3

    runs = manifest["runs"]
    repos = [run["repo"] for run in runs]
    assert len(runs) == 5
    assert len(set(repos)) == 5
    assert set(repos) == {
        "pypa/sampleproject",
        "https://github.com/pytest-dev/pluggy",
        "https://github.com/octocat/Hello-World",
        "https://github.com/TheAlgorithms/Python",
        "https://github.com/karpathy/nanoGPT",
    }
    assert all(run["expected_agent_shortcut"] is True for run in runs)
    assert all(run["expected_execution_profile"] == "agent-auto" for run in runs)
    assert all(run["metric_thresholds"]["output_dir_defaulted"] == 1 for run in runs)

    tags = {tag for run in runs for tag in run.get("scenario_tags", [])}
    for tag in {
        "cli_default_output",
        "owner_repo_input",
        "github_url_input",
        "default_branch_discovery",
        "pinned_ref",
        "include_filter",
        "src_layout",
        "no_python_blocker",
        "no_test_command",
        "test_environment_blocker",
        "environment_repair_advice",
        "patch_validation",
        "reflection_repair",
    }:
        assert tag in tags

    repair_run = next(
        run
        for run in runs
        if run["name"] == "thealgorithms_gronsfeld_acceptance_repair_reflection"
    )
    assert repair_run["prefer_cached_discovery"] is True
    assert Path(repair_run["seed_discovery_path"]).is_file()
    assert "source_cache" in repair_run["scenario_tags"]
    assert repair_run["expected_patch_validation_status"] == "pass"
    assert repair_run["metric_thresholds"]["discovery_cache_reuse"] == 1
    assert repair_run["metric_thresholds"]["discovery_cache_preferred"] == 1
    assert repair_run["metric_thresholds"][
        "repository_test_patch_validation_successful_reflection_count"
    ] == 1
    assert repair_run["metric_thresholds"][
        "agent_auto_successful_reflection_action_count"
    ] == 1


def test_intelligence_suite_p3_product_manifest_covers_robustness_goal():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_p3_product_robustness.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == "repo_intelligence_p3_product_robustness"
    assert "P3 product-robustness suite" in manifest["description"]
    assert manifest["defaults"]["execution_profile"] == "agent-auto"
    assert manifest["defaults"]["source_cache_dir"] == (
        "outputs_smoke/github_raw_source_cache"
    )
    assert manifest["defaults"]["repository_patch_generation_mode"] == "rule"
    assert manifest["defaults"]["auto_controller_max_actions"] == 0
    assert manifest["defaults"]["repository_checkout_depth"] == 1
    assert manifest["suite_thresholds"]["min_run_count"] == 9
    assert manifest["suite_thresholds"]["min_agent_shortcut_count"] == 2
    assert manifest["suite_thresholds"]["min_repo_input_kind_count"] == 2
    assert manifest["suite_thresholds"]["min_repo_input_kind_owner_repo_count"] == 1
    assert manifest["suite_thresholds"]["min_repo_input_kind_github_url_count"] == 8
    assert manifest["suite_thresholds"][
        "min_scenario_tag_p3_product_robustness_count"
    ] == 9
    assert manifest["suite_thresholds"]["min_scenario_tag_complex_pyproject_count"] == 2
    assert manifest["suite_thresholds"]["min_scenario_tag_tox_project_count"] == 1
    assert manifest["suite_thresholds"]["min_scenario_tag_nox_project_count"] == 1
    assert manifest["suite_thresholds"][
        "min_scenario_tag_no_python_blocker_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_scenario_tag_no_test_command_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_scenario_tag_test_environment_blocker_count"
    ] == 1
    assert manifest["suite_thresholds"]["min_agent_controller_loop_complete_count"] == 9
    assert manifest["suite_thresholds"][
        "min_agent_decision_timeline_complete_count"
    ] == 9
    assert manifest["suite_thresholds"]["min_repository_structure_modeled_count"] == 8
    assert manifest["suite_thresholds"]["min_source_import_blocked_count"] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_setup_doctor_blocker_environment_test_tool_missing_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_setup_doctor_blocker_test_command_no_recommended_test_command_count"
    ] == 1

    runs = manifest["runs"]
    repos = [run["repo"] for run in runs]
    assert len(runs) == 9
    assert len(set(repos)) == 9
    assert set(repos) == {
        "pypa/sampleproject",
        "https://github.com/pytest-dev/pluggy",
        "https://github.com/psf/requests",
        "https://github.com/pallets/click",
        "https://github.com/Textualize/rich",
        "https://github.com/tiangolo/fastapi",
        "https://github.com/TheAlgorithms/Python",
        "https://github.com/octocat/Hello-World",
        "https://github.com/karpathy/nanoGPT",
    }
    assert all(run["expected_execution_profile"] == "agent-auto" for run in runs)
    assert all(
        "p3_product_robustness" in run.get("scenario_tags", []) for run in runs
    )
    agent_shortcut_runs = [
        run for run in runs if run.get("expected_agent_shortcut") is True
    ]
    assert {run["name"] for run in agent_shortcut_runs} == {
        "pypa_sampleproject_p3_environment_blocker",
        "thealgorithms_p3_repair_reflection",
    }
    assert all(run.get("agent") is True for run in agent_shortcut_runs)

    tags = {tag for run in runs for tag in run.get("scenario_tags", [])}
    for tag in {
        "owner_repo_input",
        "github_url_input",
        "default_branch_discovery",
        "pinned_ref",
        "source_cache",
        "include_filter",
        "source_limit",
        "shallow_checkout",
        "src_layout",
        "complex_pyproject",
        "pytest_project",
        "nox_project",
        "tox_project",
        "dependency_sensitive",
        "no_python_blocker",
        "no_test_command",
        "test_environment_blocker",
        "repair_candidate_repository",
        "patch_validation",
        "reflection_repair",
        "static_fallback",
        "test_execution_disabled",
    }:
        assert tag in tags

    cached_runs = [
        run for run in runs if run.get("prefer_cached_discovery") is True
    ]
    assert len(cached_runs) >= 5
    for run in cached_runs:
        assert Path(run["seed_discovery_path"]).is_file()

    rich = next(run for run in runs if run["name"].startswith("rich"))
    assert rich["ref"] == "v13.7.1"
    assert rich["include"] == ["rich/console.py"]
    assert "complex_pyproject" in rich["scenario_tags"]

    fastapi = next(run for run in runs if run["name"].startswith("fastapi"))
    assert fastapi["ref"] == "0.111.0"
    assert fastapi["include"] == ["fastapi/applications.py"]
    assert "complex_pyproject" in fastapi["scenario_tags"]

    repair = next(run for run in runs if run["name"].startswith("thealgorithms"))
    assert repair["run_repository_test_command"] is True
    assert repair["expected_patch_validation_status"] == "pass"
    assert repair["metric_thresholds"][
        "repository_test_patch_validation_successful_reflection_count"
    ] == 1

    no_python = next(run for run in runs if run["name"].startswith("octocat"))
    assert no_python["expected_blocker"] == "source_import_or_parse_missing"
    assert no_python["expected_controller_action"] == "adjust_source_filters"

    no_test = next(run for run in runs if run["name"].startswith("nanogpt"))
    assert no_test["expected_repository_test_setup_doctor_blocker"] == (
        "test_command:no_recommended_test_command"
    )
    assert "no_test_command" in no_test["scenario_tags"]


def test_intelligence_suite_hybrid_no_key_manifest_exposes_llm_blocker_goal():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_hybrid_no_key_smoke.example.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["suite_name"] == "repo_intelligence_hybrid_no_key_smoke"
    assert manifest["defaults"]["clear_llm_api_keys"] is True
    assert manifest["defaults"]["repository_patch_generation_mode"] == "hybrid"
    assert manifest["defaults"]["repository_llm_patch_candidate_limit"] == 1
    assert manifest["suite_thresholds"][
        "min_repository_patch_generator_rule_candidate_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_answer_coverage_complete_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_controller_loop_complete_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_agent_decision_timeline_complete_count"
    ] == 1
    assert manifest["suite_thresholds"][
        "min_repository_test_repair_ready_count"
    ] == 1
    assert manifest["runs"][0]["expected_status"] == "pass"
    assert manifest["runs"][0]["expected_patch_generation_mode"] == "hybrid"
    assert manifest["runs"][0]["expected_llm_patch_generation_status"] == "blocked"
    assert manifest["runs"][0]["expected_patch_safety_gate_status"] == "pass"
    assert manifest["runs"][0]["metric_thresholds"][
        "artifact_inventory_required_file_nonempty_count"
    ] == 34


def test_intelligence_suite_llm_repair_smoke_manifest_requires_real_llm():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_llm_repair_smoke.example.json"
        ).read_text(encoding="utf-8")
    )
    defaults = manifest["defaults"]
    thresholds = manifest["suite_thresholds"]
    run = manifest["runs"][0]

    assert manifest["suite_name"] == "repo_intelligence_llm_repair_smoke"
    assert "requires CIA_LLM_API_KEY" in manifest["description"]
    assert "CIA_LLM_API_KEY or DEEPSEEK_API_KEY for patch/reflection" in (
        manifest["description"]
    )
    assert "CIA_JUDGE_API_KEY or DEEPSEEK_API_KEY" in manifest["description"]
    assert "Do not store API keys" in manifest["description"]
    assert defaults["clear_llm_api_keys"] is False
    assert defaults["require_llm_configuration"] is True
    assert defaults["reject_placeholder_llm_api_keys"] is True
    assert defaults["llm_api_key_min_length"] == 20
    assert defaults["run_llm_repair_showcase_matrix"] is True
    assert defaults["run_repository_test_command"] is True
    assert defaults["repository_patch_generation_mode"] == "llm"
    assert defaults["repository_llm_patch_candidate_limit"] == 2
    assert defaults["repository_test_reflection_mode"] == "llm"
    assert defaults["repository_test_reflection_width"] == 2
    assert defaults["patch_judge_mode"] == "llm"
    assert thresholds["min_repository_patch_generator_llm_candidate_count"] == 1
    assert thresholds["min_repository_test_patch_validation_success_count"] == 1
    assert thresholds["min_repository_test_repair_ready_count"] == 1
    assert thresholds["min_repository_test_patch_judge_candidate_count"] == 1
    assert thresholds["min_agent_controller_loop_complete_count"] == 1
    assert run["expected_status"] == "pass"
    assert run["expected_patch_generation_mode"] == "llm"
    assert run["expected_llm_patch_generation_status"] == "pass"
    assert run["expected_patch_safety_gate_status"] == "pass"
    assert run["expected_patch_judge_mode"] == "llm"
    assert run["expected_patch_judge_status"] == "ready"
    assert run["metric_thresholds"]["repository_patch_generator_llm_count"] == 1
    assert run["metric_thresholds"]["repository_test_patch_judge_candidate_count"] == 1
    assert run["metric_thresholds"][
        "repository_test_patch_validation_success_count"
    ] == 1
    assert "real_llm_required" in run["scenario_tags"]
    assert "sandbox_patch_validation" in run["scenario_tags"]


def test_intelligence_suite_p6_llm_direct_success_manifest_defines_real_source_case():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_p6_llm_direct_success.example.json"
        ).read_text(encoding="utf-8")
    )
    defaults = manifest["defaults"]
    thresholds = manifest["suite_thresholds"]
    runs = {run["name"]: run for run in manifest["runs"]}
    run = runs["direct_guard_0"]
    self_run = runs["direct_guard_1"]
    algorithm_run = runs["direct_guard_2"]

    assert manifest["suite_name"] == "repo_intelligence_p6_llm_direct_success"
    assert manifest["run_llm_repair_showcase_matrix"] is True
    assert defaults["clear_llm_api_keys"] is False
    assert defaults["require_llm_configuration"] is True
    assert defaults["repository_patch_generation_mode"] == "llm"
    assert defaults["repository_llm_patch_candidate_limit"] == 2
    assert defaults["repository_test_reflection_mode"] == "llm"
    assert defaults["patch_judge_mode"] == "llm"
    assert thresholds["min_run_count"] == 3
    assert thresholds["min_llm_repair_showcase_matrix_direct_success_count"] == 3
    assert thresholds["min_repository_patch_generator_llm_candidate_count"] == 3
    assert thresholds["min_repository_test_patch_judge_accept_success_count"] == 3
    assert run["name"] == "direct_guard_0"
    assert run["expected_status"] == "pass"
    assert run["expected_patch_generation_mode"] == "llm"
    assert run["expected_llm_patch_generation_status"] == "pass"
    assert run["expected_patch_safety_gate_status"] == "pass"
    assert run["expected_patch_validation_status"] == "pass"
    assert run["expected_patch_judge_mode"] == "llm"
    assert run["expected_patch_judge_status"] == "ready"
    assert run["metric_thresholds"]["repository_patch_generator_llm_count"] == 1
    assert (
        run["metric_thresholds"]["repository_test_patch_judge_accept_success_count"]
        == 1
    )
    assert "real_llm_required" in run["scenario_tags"]
    assert "sandbox_patch_validation" in run["scenario_tags"]
    assert self_run["repo"] == "https://github.com/Anweilong111/code-intelligence-Agent"
    assert self_run["ref"] == "b30b814d7dcca0555b42f86d09b4f48a2e6b5a28"
    assert self_run["include"] == ["code_intelligence_agent/evaluation/report.py"]
    assert self_run["expected_repair_validation_scope"] == "narrow_and_regression"
    assert self_run["expected_patch_judge_mode"] == "llm"
    assert (
        self_run["metric_thresholds"][
            "repository_test_patch_judge_accept_success_count"
        ]
        == 1
    )
    assert "real_llm_required" in self_run["scenario_tags"]
    assert "sandbox_patch_validation" in self_run["scenario_tags"]
    assert algorithm_run["repo"] == "https://github.com/keon/algorithms"
    assert algorithm_run["ref"] == "f9896169928237b9772371656d878215a43cd57f"
    assert algorithm_run["include"] == [
        "algorithms/searching/next_greatest_letter.py"
    ]
    assert algorithm_run["expected_repair_validation_scope"] == "narrow_and_regression"
    assert algorithm_run["expected_patch_judge_mode"] == "llm"
    assert (
        algorithm_run["metric_thresholds"][
            "repository_test_patch_judge_accept_success_count"
        ]
        == 1
    )
    assert "real_llm_required" in algorithm_run["scenario_tags"]
    assert "sandbox_patch_validation" in algorithm_run["scenario_tags"]


def test_intelligence_suite_p6_llm_repair_blocker_manifest_defines_expected_blockers():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_p6_llm_repair_blockers.example.json"
        ).read_text(encoding="utf-8")
    )
    defaults = manifest["defaults"]
    thresholds = manifest["suite_thresholds"]
    runs = manifest["runs"]

    assert manifest["suite_name"] == "repo_intelligence_p6_llm_repair_blockers"
    assert manifest["run_llm_repair_showcase_matrix"] is True
    assert manifest["run_llm_repair_case_catalog_audit"] is True
    assert manifest["llm_repair_case_catalog_path"] == (
        "llm_repair_case_catalog.example.json"
    )
    assert defaults["clear_llm_api_keys"] is True
    assert defaults["require_llm_configuration"] is True
    assert defaults["repository_patch_generation_mode"] == "llm"
    assert defaults["repository_test_reflection_mode"] == "llm"
    assert defaults["patch_judge_mode"] == "llm"
    assert thresholds["max_command_failed_count"] == 0
    assert thresholds["min_llm_repair_showcase_matrix_blocker_count"] == 3
    assert thresholds["min_llm_repair_case_catalog_matched_case_count"] == 3
    assert thresholds["max_llm_repair_case_catalog_missing_source_report_count"] == 0
    assert len(runs) == 3
    assert {run["name"] for run in runs} == {
        "llm_failed_blocker_0",
        "llm_failed_blocker_1",
        "llm_failed_blocker_2",
    }
    assert all(run["expected_status"] == "llm_config_blocked" for run in runs)
    assert all(
        run["expected_llm_patch_generation_status"] == "blocked" for run in runs
    )
    assert all(run["expected_patch_judge_status"] == "unavailable" for run in runs)
    assert all("blocker_expected" in run["scenario_tags"] for run in runs)


def test_intelligence_suite_p6_onboarding_blocker_manifest_defines_source_cases():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_p6_onboarding_blockers.example.json"
        ).read_text(encoding="utf-8")
    )
    thresholds = manifest["suite_thresholds"]
    runs = {run["name"]: run for run in manifest["runs"]}

    assert manifest["suite_name"] == "repo_intelligence_p6_onboarding_blockers"
    assert manifest["run_llm_repair_showcase_matrix"] is True
    assert manifest["defaults"]["execution_profile"] == "agent-auto"
    assert manifest["defaults"]["repository_patch_generation_mode"] == "rule"
    assert thresholds["min_run_count"] == 3
    assert thresholds["min_llm_repair_showcase_matrix_blocker_count"] == 3
    assert set(runs) == {
        "environment_blocker_0",
        "no_test_oracle_blocker_0",
        "no_test_oracle_blocker_1",
    }
    assert "environment_blocker" in runs["environment_blocker_0"]["scenario_tags"]
    assert runs["environment_blocker_0"][
        "expected_repository_test_setup_doctor_blocker"
    ] == "environment:test_tool_missing"
    assert "no_test_oracle_blocker" in runs["no_test_oracle_blocker_0"][
        "scenario_tags"
    ]
    assert runs["no_test_oracle_blocker_1"][
        "expected_repository_test_setup_doctor_blocker"
    ] == "test_command:no_recommended_test_command"


def test_intelligence_suite_p6_safety_gate_blocker_manifest_defines_source_case():
    manifest = json.loads(
        Path(
            "datasets/github_cases/repo_intelligence_p6_safety_gate_blockers.example.json"
        ).read_text(encoding="utf-8")
    )
    defaults = manifest["defaults"]
    thresholds = manifest["suite_thresholds"]
    run = manifest["runs"][0]

    assert manifest["suite_name"] == "repo_intelligence_p6_safety_gate_blockers"
    assert manifest["run_llm_repair_showcase_matrix"] is True
    assert defaults["controlled_repair_case"] == "safety_gate_blocker"
    assert defaults["repository_patch_generation_mode"] == "hybrid"
    assert thresholds["min_llm_repair_showcase_matrix_blocker_count"] == 1
    assert thresholds[
        "min_repository_test_patch_validation_safety_blocked_candidate_count"
    ] == 1
    assert run["name"] == "safety_gate_blocker_0"
    assert run["expected_blocker"] == "patch_candidates_blocked_by_safety_gate"
    assert run["expected_controller_action"] == "regenerate_safe_patch_candidates"
    assert run["expected_patch_safety_gate_status"] == "blocked"
    assert run["expected_patch_validation_status"] == "skipped"
    assert "pre_sandbox_safety_gate" in run["scenario_tags"]


def test_intelligence_suite_runs_controlled_safety_gate_blocker_case(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    output_dir = tmp_path / "suite"
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "controlled_safety_gate_suite",
                "run_llm_repair_showcase_matrix": True,
                "defaults": {
                    "execution_profile": "controlled-repair",
                    "controlled_repair_case": "safety_gate_blocker",
                    "repository_patch_generation_mode": "hybrid",
                    "repository_test_reflection_mode": "none",
                },
                "suite_thresholds": {
                    "max_command_failed_count": 0,
                    "max_expectation_failed_count": 0,
                    "min_llm_repair_showcase_matrix_blocker_count": 1,
                    "min_repository_test_patch_validation_safety_blocked_candidate_count": 1,
                },
                "runs": [
                    {
                        "name": "safety_gate_blocker_0",
                        "repo": "controlled/safety_gate_blocker_0",
                        "expected_status": "pass",
                        "expected_blocker": (
                            "patch_candidates_blocked_by_safety_gate"
                        ),
                        "expected_controller_action": (
                            "regenerate_safe_patch_candidates"
                        ),
                        "expected_patch_generation_mode": "hybrid",
                        "expected_patch_safety_gate_status": "blocked",
                        "expected_patch_validation_status": "skipped",
                        "metric_thresholds": {
                            "repository_test_patch_candidate_count": 1,
                            "repository_patch_safety_gate_blocked_count": 1,
                            "repository_test_patch_validation_input_candidate_count": 1,
                            "repository_test_patch_validation_safety_blocked_candidate_count": 1,
                            "agent_controller_loop_complete": 1,
                            "agent_decision_timeline_complete": 1,
                            "agent_answer_coverage_answered_count": 3,
                        },
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    report = run_github_repo_intelligence_suite(manifest_path, output_dir)
    run = report.runs[0]
    validation = json.loads(
        (output_dir / "safety_gate_blocker_0" / "repository_test_patch_validation.json")
        .read_text(encoding="utf-8")
    )
    matrix = json.loads(
        (output_dir / "llm_repair_showcase_matrix.json").read_text(
            encoding="utf-8"
        )
    )
    row = matrix["matrix"][0]

    assert report.passed is True
    assert run.status == "pass"
    assert run.metrics["blocker"] == "patch_candidates_blocked_by_safety_gate"
    assert run.metrics["repository_test_patch_validation_status"] == "skipped"
    assert (
        run.metrics["repository_test_patch_validation_reason"]
        == "all_candidates_blocked_by_safety_gate"
    )
    assert (
        run.metrics["repository_test_patch_validation_safety_blocked_candidate_count"]
        == 1
    )
    assert validation["reason"] == "all_candidates_blocked_by_safety_gate"
    assert validation["success_count"] == 0
    assert validation["safety_blocked_candidate_count"] == 1
    assert row["class"] == "llm_blocker"
    assert row["blocker_category"] == "safety_gate_blocker"
    assert row["evidence_status"] == "complete"
    assert row["patch_validation_safety_blocked_count"] == 1
    assert row["patch_judge_authority"] == "sandbox_pytest_decides_success"


def test_intelligence_suite_llm_preflight_blocks_missing_keys_before_runner(
    tmp_path,
    monkeypatch,
):
    for env_name in (
        "CIA_LLM_API_KEY",
        "DEEPSEEK_API_KEY",
        "CIA_JUDGE_API_KEY",
        "DASHSCOPE_API_KEY",
        "ALIBABA_API_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)
    manifest_path = tmp_path / "manifest.json"
    output_dir = tmp_path / "suite"
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "llm_preflight_suite",
                "defaults": {
                    "require_llm_configuration": True,
                    "run_llm_repair_showcase_matrix": True,
                    "repository_patch_generation_mode": "llm",
                    "repository_test_reflection_mode": "llm",
                    "patch_judge_mode": "llm",
                },
                "suite_thresholds": {
                    "min_llm_repair_showcase_matrix_blocker_count": 1,
                    "min_llm_repair_showcase_matrix_direct_success_count": 1,
                },
                "runs": [
                    {
                        "name": "missing_llm_keys",
                        "repo": "example/project",
                        "expected_status": "pass",
                        "expected_patch_generation_mode": "llm",
                        "expected_llm_patch_generation_status": "pass",
                        "expected_patch_judge_mode": "llm",
                        "expected_patch_judge_status": "ready",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    def fail_if_runner_called(*args, **kwargs):
        raise AssertionError("runner should not be called when LLM preflight fails")

    monkeypatch.setattr(
        suite_module,
        "run_github_repo_intelligence",
        fail_if_runner_called,
    )

    report = run_github_repo_intelligence_suite(manifest_path, output_dir)
    run = report.runs[0]
    preflight = json.loads(Path(run.report_path).read_text(encoding="utf-8"))
    markdown = (Path(run.output_dir) / "llm_config_preflight.md").read_text(
        encoding="utf-8"
    )
    matrix_path = output_dir / "llm_repair_showcase_matrix.json"
    matrix_markdown_path = output_dir / "llm_repair_showcase_matrix.md"
    evaluation_matrix_path = output_dir / "llm_repair_evaluation_matrix.json"
    metrics_report_path = output_dir / "llm_repair_metrics_report.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    evaluation_matrix = json.loads(evaluation_matrix_path.read_text(encoding="utf-8"))
    metrics_report = json.loads(metrics_report_path.read_text(encoding="utf-8"))
    suite_markdown = (output_dir / "github_repo_intelligence_suite.md").read_text(
        encoding="utf-8"
    )

    assert report.passed is False
    assert report.summary["command_failed_count"] == 1
    assert report.summary["llm_repair_showcase_matrix_status"] == "incomplete"
    assert report.summary["llm_repair_showcase_matrix_class_counts"] == {
        "llm_blocker": 1
    }
    assert report.summary["llm_repair_showcase_matrix_blocker_present"] is True
    assert report.summary["llm_repair_showcase_matrix_direct_success_present"] is False
    assert (
        report.summary["llm_repair_showcase_matrix_reflection_success_present"]
        is False
    )
    assert report.summary["llm_repair_showcase_matrix_json"] == str(matrix_path)
    assert report.summary["llm_repair_showcase_matrix_markdown"] == str(
        matrix_markdown_path
    )
    assert report.summary["llm_repair_evaluation_matrix_status"] == "incomplete"
    assert report.summary["llm_repair_evaluation_matrix_json"] == str(
        evaluation_matrix_path
    )
    assert report.summary["llm_repair_metrics_report_status"] == "incomplete"
    assert report.summary["llm_repair_metrics_report_json"] == str(
        metrics_report_path
    )
    assert report.summary["llm_repair_metrics_patch_success_at"] == (
        "1=0.0000, 3=0.0000, 5=0.0000"
    )
    suite_thresholds = {
        check["name"]: check for check in report.summary["suite_threshold_checks"]
    }
    assert report.summary["suite_threshold_failed_count"] == 1
    assert suite_thresholds[
        "min_llm_repair_showcase_matrix_blocker_count"
    ]["passed"] is True
    assert suite_thresholds[
        "min_llm_repair_showcase_matrix_blocker_count"
    ]["actual"] == "1.0000"
    assert suite_thresholds[
        "min_llm_repair_showcase_matrix_direct_success_count"
    ]["passed"] is False
    assert suite_thresholds[
        "min_llm_repair_showcase_matrix_direct_success_count"
    ]["actual"] == "0.0000"
    assert matrix["class_counts"] == {"llm_blocker": 1}
    assert matrix["requirement_status"]["llm_blocker"] is True
    assert matrix["requirement_status"]["llm_direct_success"] is False
    assert matrix["requirement_status"]["llm_reflection_success"] is False
    assert evaluation_matrix["metrics_report"]["sandbox_authority"] == (
        "sandbox_pytest_decides_success"
    )
    assert metrics_report["llm_blocker_count"] == 1
    assert "LLM Repair Showcase Matrix Status: `incomplete`" in suite_markdown
    assert "LLM Repair Evaluation Matrix Status: `incomplete`" in suite_markdown
    assert run.status == "llm_config_blocked"
    assert run.error == (
        "missing_enabled_llm_api_key_roles:patch_generation,judge"
    )
    assert run.metrics["repository_llm_patch_generation_status"] == "blocked"
    assert run.metrics["repository_llm_patch_generation_reason"] == (
        "missing_llm_api_key"
    )
    assert run.metrics["repository_test_patch_judge_status"] == "unavailable"
    assert run.metrics["llm_config_missing_enabled_api_key_roles"] == [
        "patch_generation",
        "judge",
    ]
    assert preflight["status"] == "blocked"
    assert preflight["reason"] == "missing_enabled_llm_api_key"
    assert preflight["missing_enabled_api_key_roles"] == [
        "patch_generation",
        "judge",
    ]
    assert preflight["required_environment"] == [
        {
            "role": "patch_generation",
            "accepted_api_key_envs": [
                "CIA_LLM_API_KEY",
                "DEEPSEEK_API_KEY",
            ],
            "provider_env": "CIA_LLM_PROVIDER",
            "model_env": "CIA_LLM_MODEL",
            "base_url_env": "CIA_LLM_BASE_URL",
            "provider": "deepseek",
            "model": "deepseek-v4-pro",
            "base_url": "https://api.deepseek.com/chat/completions",
        },
        {
            "role": "judge",
            "accepted_api_key_envs": [
                "CIA_JUDGE_API_KEY",
                "DEEPSEEK_API_KEY",
            ],
            "provider_env": "CIA_JUDGE_PROVIDER",
            "model_env": "CIA_JUDGE_MODEL",
            "base_url_env": "CIA_JUDGE_BASE_URL",
            "provider": "deepseek",
            "model": "deepseek-v4-pro",
            "base_url": "https://api.deepseek.com/chat/completions",
        },
    ]
    assert preflight["next_actions"] == [
        (
            "Set one accepted API key environment variable for "
            "`patch_generation`: CIA_LLM_API_KEY, DEEPSEEK_API_KEY."
        ),
        (
            "Set one accepted API key environment variable for "
            "`judge`: CIA_JUDGE_API_KEY, DEEPSEEK_API_KEY."
        ),
        (
            "Do not write API keys into manifests, README files, tests, "
            "reports, or committed project files."
        ),
        (
            "Re-run the LLM repair smoke suite after the environment variables "
            "are visible to the current shell."
        ),
    ]
    assert run.metrics["llm_config_required_environment"] == (
        preflight["required_environment"]
    )
    assert run.metrics["llm_config_next_actions"] == preflight["next_actions"]
    assert run.metrics["agent_answer_coverage_complete"] is True
    assert run.metrics["agent_answers_blocker"] == "llm_config_missing_api_key"
    assert run.metrics["agent_answers_next_action"] == (
        "Re-run the LLM repair smoke suite after the environment variables "
        "are visible to the current shell."
    )
    assert run.metrics["agent_answer_question_statuses"] == {
        "llm_configuration": "answered",
    }
    serialized = json.dumps(preflight)
    assert "sk-" not in serialized
    assert "LLM Configuration Preflight" in markdown
    assert "Missing Enabled API Key Roles: patch_generation, judge" in markdown
    assert "Accepted API Key Envs: CIA_LLM_API_KEY, DEEPSEEK_API_KEY" in markdown
    assert "Accepted API Key Envs: CIA_JUDGE_API_KEY, DEEPSEEK_API_KEY" in markdown
    assert "Checked API Key Envs" in markdown


def test_intelligence_suite_treats_expected_llm_preflight_blockers_as_pass(
    tmp_path,
    monkeypatch,
):
    for env_name in (
        "CIA_LLM_API_KEY",
        "DEEPSEEK_API_KEY",
        "CIA_JUDGE_API_KEY",
        "DASHSCOPE_API_KEY",
        "ALIBABA_API_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)
    manifest_path = tmp_path / "manifest.json"
    output_dir = tmp_path / "suite"
    catalog_path = tmp_path / "catalog.json"
    suite_report_path = output_dir / "github_repo_intelligence_suite.json"
    case_names = [f"llm_failed_blocker_{index}" for index in range(3)]
    catalog_path.write_text(
        json.dumps(
            {
                "name": "expected_blockers",
                "targets": {
                    "case_count": 3,
                    "llm_direct_success": 0,
                    "llm_reflection_success": 0,
                    "llm_blocker": 3,
                    "llm_direct_evidence_complete": 0,
                    "llm_reflection_evidence_complete": 0,
                    "llm_blocker_evidence_complete": 3,
                    "llm_patch_judge_ready": 0,
                    "llm_patch_judge_accept_success": 0,
                    "llm_patch_judge_reject_failure": 0,
                    "llm_failed_blocker": 3,
                    "environment_blocker": 0,
                    "no_test_oracle_blocker": 0,
                    "safety_gate_blocker": 0,
                    "agent_loop_trace_complete": 3,
                },
                "source_reports": [str(suite_report_path)],
                "cases": [
                    {
                        "case_id": name,
                        "repo": f"example/{name}",
                        "expected_class": "llm_blocker",
                        "expected_blocker_category": "llm_failed_blocker",
                        "source_report_path": str(suite_report_path),
                    }
                    for name in case_names
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "expected_llm_blockers",
                "run_llm_repair_showcase_matrix": True,
                "run_llm_repair_case_catalog_audit": True,
                "llm_repair_case_catalog_path": str(catalog_path),
                "defaults": {
                    "clear_llm_api_keys": True,
                    "require_llm_configuration": True,
                    "repository_patch_generation_mode": "llm",
                    "repository_test_reflection_mode": "llm",
                    "patch_judge_mode": "llm",
                },
                "suite_thresholds": {
                    "max_command_failed_count": 0,
                    "max_expectation_failed_count": 0,
                    "min_llm_repair_showcase_matrix_blocker_count": 3,
                    "min_llm_repair_case_catalog_matched_case_count": 3,
                    "max_llm_repair_case_catalog_missing_case_count": 0,
                    "max_llm_repair_case_catalog_missing_source_report_count": 0,
                    "min_llm_repair_case_catalog_blocker_count": 3,
                },
                "runs": [
                    {
                        "name": name,
                        "repo": f"example/{name}",
                        "expected_status": "llm_config_blocked",
                        "expected_patch_generation_mode": "llm",
                        "expected_llm_patch_generation_status": "blocked",
                        "expected_patch_judge_mode": "llm",
                        "expected_patch_judge_status": "unavailable",
                    }
                    for name in case_names
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    def fail_if_runner_called(*args, **kwargs):
        raise AssertionError("runner should not be called when LLM preflight blocks")

    monkeypatch.setattr(
        suite_module,
        "run_github_repo_intelligence",
        fail_if_runner_called,
    )

    report = run_github_repo_intelligence_suite(manifest_path, output_dir)
    audit = json.loads(
        (output_dir / "llm_repair_case_catalog_audit.json").read_text(
            encoding="utf-8"
        )
    )
    checks = {
        check["name"]: check for check in report.summary["suite_threshold_checks"]
    }

    assert report.passed is True
    assert report.summary["agent_passed_count"] == 3
    assert report.summary["command_failed_count"] == 0
    assert report.summary["expectation_failed_count"] == 0
    assert report.summary["llm_repair_showcase_matrix_blocker_count"] == 3
    assert report.summary["llm_repair_case_catalog_audit_status"] == "pass"
    assert report.summary["llm_repair_case_catalog_matched_case_count"] == 3
    assert report.summary["llm_repair_case_catalog_missing_source_report_count"] == 0
    assert audit["counts"]["llm_failed_blocker_count"] == 3
    assert checks["max_command_failed_count"]["passed"] is True
    assert checks["min_llm_repair_case_catalog_matched_case_count"]["passed"] is True
    assert all(run.error is None for run in report.runs)
    assert {run.status for run in report.runs} == {"llm_config_blocked"}


def test_intelligence_suite_llm_showcase_thresholds_recompute_report_passed(
    tmp_path,
    monkeypatch,
):
    manifest_path = tmp_path / "manifest.json"
    output_dir = tmp_path / "suite"
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "llm_showcase_threshold_suite",
                "defaults": {
                    "run_llm_repair_showcase_matrix": True,
                    "repository_patch_generation_mode": "llm",
                },
                "suite_thresholds": {
                    "min_llm_repair_showcase_matrix_direct_success_count": 1,
                    "min_llm_repair_showcase_matrix_reflection_success_count": 1,
                },
                "runs": [
                    {
                        "name": "direct_only",
                        "repo": "example/project",
                        "expected_status": "pass",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    def fake_run_github_repo_intelligence(repo_spec, output_dir_arg, **kwargs):
        return suite_module.GitHubRepoAgentReport(
            repo_spec=repo_spec,
            owner="example",
            repo="project",
            output_dir=str(output_dir_arg),
            preset=str(kwargs.get("preset") or ""),
            status="pass",
            summary={},
            output_paths={},
            onboarding_report={},
        )

    def fake_summary(report):
        return {
            "repo": report.repo_spec,
            "repo_spec": report.repo_spec,
            "output_dir": report.output_dir,
            "status": "pass",
            "passed": True,
            "intelligence_json": str(
                Path(report.output_dir) / "github_repo_intelligence.json"
            ),
            "repository_patch_generation_mode": "llm",
            "repository_llm_patch_generation_status": "pass",
            "repository_llm_patch_provider": "deepseek",
            "repository_llm_patch_model": "deepseek-v4-pro",
            "repository_llm_patch_api_key_present": True,
            "repository_patch_generator_counts": {"llm": 1},
            "repository_patch_generator_llm_candidate_count": 1,
            "repository_test_patch_validation_status": "pass",
            "repository_test_patch_validation_success_count": 1,
            "repository_test_patch_validation_successful_reflection_count": 0,
        }

    monkeypatch.setattr(
        suite_module,
        "run_github_repo_intelligence",
        fake_run_github_repo_intelligence,
    )
    monkeypatch.setattr(
        suite_module,
        "github_repo_intelligence_summary",
        fake_summary,
    )
    monkeypatch.setattr(
        suite_module,
        "write_github_repo_intelligence_artifacts",
        lambda report, summary: None,
    )

    report = run_github_repo_intelligence_suite(manifest_path, output_dir)
    checks = {
        check["name"]: check for check in report.summary["suite_threshold_checks"]
    }

    assert report.summary["command_failed_count"] == 0
    assert report.summary["expectation_failed_count"] == 0
    assert report.summary["metric_check_failed_count"] == 0
    assert report.summary["llm_repair_showcase_matrix_direct_success_count"] == 1
    assert report.summary["llm_repair_showcase_matrix_reflection_success_count"] == 0
    assert report.summary["suite_threshold_failed_count"] == 1
    assert report.passed is False
    assert report.runs[0].metrics["repository_llm_patch_provider"] == "deepseek"
    assert report.runs[0].metrics["repository_llm_patch_model"] == "deepseek-v4-pro"
    assert report.runs[0].metrics["repository_llm_patch_api_key_present"] is True
    assert checks[
        "min_llm_repair_showcase_matrix_direct_success_count"
    ]["passed"] is True
    assert checks[
        "min_llm_repair_showcase_matrix_reflection_success_count"
    ]["passed"] is False


def test_intelligence_suite_aggregates_external_llm_repair_source_reports(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    output_dir = tmp_path / "suite"
    source_path = tmp_path / "external_suite.json"
    source_path.write_text(
        json.dumps(
            {
                "suite_name": "external_llm_repair_suite",
                "runs": [
                    _external_llm_direct_success_run("direct"),
                    _external_llm_reflection_success_run("reflection"),
                    _external_llm_blocker_run("missing_key"),
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "aggregate_repair_sources",
                "run_llm_repair_showcase_matrix": True,
                "llm_repair_source_reports": [str(source_path)],
                "suite_thresholds": {
                    "max_llm_repair_source_report_missing_count": 0,
                    "min_llm_repair_source_report_count": 1,
                    "min_llm_repair_showcase_matrix_direct_success_count": 1,
                    "min_llm_repair_showcase_matrix_reflection_success_count": 1,
                    "min_llm_repair_showcase_matrix_blocker_count": 1,
                },
                "runs": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    report = run_github_repo_intelligence_suite(manifest_path, output_dir)
    matrix = json.loads(
        (output_dir / "llm_repair_showcase_matrix.json").read_text(
            encoding="utf-8"
        )
    )
    evaluation = json.loads(
        (output_dir / "llm_repair_evaluation_matrix.json").read_text(
            encoding="utf-8"
        )
    )
    suite_markdown = (output_dir / "github_repo_intelligence_suite.md").read_text(
        encoding="utf-8"
    )

    assert report.passed is True
    assert report.summary["llm_repair_source_report_count"] == 1
    assert report.summary["llm_repair_source_report_missing_count"] == 0
    assert report.summary["llm_repair_showcase_matrix_status"] == "pass"
    assert matrix["class_counts"] == {
        "llm_blocker": 1,
        "llm_direct_success": 1,
        "llm_reflection_success": 1,
    }
    assert evaluation["case_count"] == 3
    assert {
        row["suite_report_path"] for row in evaluation["matrix"]
    } == {str(source_path)}
    assert "LLM Repair Source Reports: 1" in suite_markdown
    assert "Missing LLM Repair Source Reports: 0" in suite_markdown


def test_intelligence_suite_writes_llm_repair_case_catalog_audit(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    output_dir = tmp_path / "suite"
    source_path = tmp_path / "external_suite.json"
    catalog_path = tmp_path / "catalog.json"
    source_path.write_text(
        json.dumps(
            {
                "suite_name": "external_llm_repair_suite",
                "runs": [
                    _catalog_llm_direct_success_run("direct_catalog"),
                    _catalog_llm_reflection_success_run("reflection_catalog"),
                    _catalog_llm_blocker_run("missing_key_catalog"),
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    catalog_path.write_text(
        json.dumps(
            {
                "name": "small_catalog",
                "targets": {
                    "case_count": 3,
                    "llm_direct_success": 1,
                    "llm_reflection_success": 1,
                    "llm_blocker": 1,
                    "llm_direct_evidence_complete": 1,
                    "llm_reflection_evidence_complete": 1,
                    "llm_blocker_evidence_complete": 1,
                    "llm_patch_judge_ready": 2,
                    "llm_patch_judge_accept_success": 1,
                    "llm_patch_judge_reject_failure": 1,
                    "llm_failed_blocker": 1,
                    "environment_blocker": 0,
                    "no_test_oracle_blocker": 0,
                    "safety_gate_blocker": 0,
                    "agent_loop_trace_complete": 3,
                },
                "source_reports": [str(source_path)],
                "cases": [
                    {
                        "case_id": "direct_catalog",
                        "repo": "example/direct_catalog",
                        "expected_class": "llm_direct_success",
                        "source_report_path": str(source_path),
                    },
                    {
                        "case_id": "reflection_catalog",
                        "repo": "example/reflection_catalog",
                        "expected_class": "llm_reflection_success",
                        "source_report_path": str(source_path),
                    },
                    {
                        "case_id": "missing_key_catalog",
                        "repo": "example/missing_key_catalog",
                        "expected_class": "llm_blocker",
                        "expected_blocker_category": "llm_failed_blocker",
                        "source_report_path": str(source_path),
                    },
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "repair_catalog_audit_suite",
                "run_llm_repair_showcase_matrix": True,
                "run_llm_repair_case_catalog_audit": True,
                "llm_repair_source_reports": [str(source_path)],
                "llm_repair_case_catalog_path": str(catalog_path),
                "suite_thresholds": {
                    "max_llm_repair_source_report_missing_count": 0,
                    "min_llm_repair_source_report_count": 1,
                    "min_llm_repair_case_catalog_matched_case_count": 3,
                    "max_llm_repair_case_catalog_missing_case_count": 0,
                    "max_llm_repair_case_catalog_missing_source_report_count": 0,
                    "min_llm_repair_case_catalog_agent_loop_trace_complete_count": 3,
                },
                "runs": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    report = run_github_repo_intelligence_suite(manifest_path, output_dir)
    audit_path = output_dir / "llm_repair_case_catalog_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    suite_markdown = (output_dir / "github_repo_intelligence_suite.md").read_text(
        encoding="utf-8"
    )

    assert report.passed is True
    assert audit["status"] == "pass"
    assert report.summary["llm_repair_case_catalog_audit_status"] == "pass"
    assert report.summary["llm_repair_case_catalog_audit_json"] == str(audit_path)
    assert report.summary["llm_repair_case_catalog_declared_case_count"] == 3
    assert report.summary["llm_repair_case_catalog_matched_case_count"] == 3
    assert report.summary["llm_repair_case_catalog_missing_case_count"] == 0
    assert (
        report.summary["llm_repair_case_catalog_missing_source_report_count"]
        == 0
    )
    assert report.summary["llm_repair_case_catalog_direct_success_count"] == 1
    assert report.summary["llm_repair_case_catalog_reflection_success_count"] == 1
    assert report.summary["llm_repair_case_catalog_blocker_count"] == 1
    assert report.summary[
        "llm_repair_case_catalog_agent_loop_trace_complete_count"
    ] == 3
    assert "LLM Repair Case Catalog Audit Status: `pass`" in suite_markdown
    assert "LLM Repair Case Catalog Matched Cases: 3/3" in suite_markdown


def test_intelligence_suite_reports_missing_external_llm_repair_source(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    output_dir = tmp_path / "suite"
    missing_path = tmp_path / "missing_suite.json"
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "missing_repair_source",
                "run_llm_repair_showcase_matrix": True,
                "llm_repair_source_reports": [str(missing_path)],
                "suite_thresholds": {
                    "max_llm_repair_source_report_missing_count": 0,
                },
                "runs": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    report = run_github_repo_intelligence_suite(manifest_path, output_dir)
    checks = {
        check["name"]: check for check in report.summary["suite_threshold_checks"]
    }
    suite_markdown = (output_dir / "github_repo_intelligence_suite.md").read_text(
        encoding="utf-8"
    )

    assert report.passed is False
    assert report.summary["llm_repair_source_report_count"] == 0
    assert report.summary["llm_repair_source_report_missing_count"] == 1
    assert report.summary["llm_repair_source_report_missing_paths"] == [
        str(missing_path)
    ]
    assert checks["max_llm_repair_source_report_missing_count"]["passed"] is False
    assert "Missing LLM Repair Source Reports: 1" in suite_markdown


def _external_llm_direct_success_run(name: str) -> dict:
    return {
        "name": name,
        "repo": f"example/{name}",
        "report_path": f"out/{name}/github_repo_intelligence.json",
        "status": "pass",
        "passed": True,
        "metrics": {
            "repository_patch_generation_mode": "llm",
            "repository_llm_patch_generation_status": "pass",
            "repository_llm_patch_provider": "deepseek",
            "repository_llm_patch_model": "deepseek-v4-pro",
            "repository_llm_patch_api_key_present": True,
            "repository_patch_generator_llm_candidate_count": 2,
            "repository_test_patch_validation_status": "pass",
            "repository_test_patch_validation_executed_count": 1,
            "repository_test_patch_validation_candidate_count": 2,
            "repository_test_patch_validation_success_count": 1,
            "repository_test_patch_validation_successful_reflection_count": 0,
            "repository_test_patch_judge_authority": (
                "sandbox_pytest_decides_success"
            ),
        },
    }


def _external_llm_reflection_success_run(name: str) -> dict:
    payload = _external_llm_direct_success_run(name)
    payload["metrics"].update(
        {
            "repository_test_patch_validation_executed_count": 2,
            "repository_test_patch_validation_reflection_mode": "llm",
            "repository_test_patch_validation_reflection_candidate_count": 2,
            "repository_test_patch_validation_successful_reflection_count": 1,
            "repository_test_patch_validation_llm_reflection_attempt_count": 1,
            "repository_llm_reflection_status": "ready",
            "repository_llm_reflection_provider": "deepseek",
            "repository_llm_reflection_model": "deepseek-v4-pro",
        }
    )
    return payload


def _external_llm_blocker_run(name: str) -> dict:
    return {
        "name": name,
        "repo": f"example/{name}",
        "report_path": f"out/{name}/llm_config_preflight.json",
        "status": "llm_config_blocked",
        "passed": False,
        "metrics": {
            "status": "llm_config_blocked",
            "blocker": "llm_config_missing_api_key",
            "repository_patch_generation_mode": "llm",
            "repository_llm_patch_generation_status": "blocked",
            "repository_llm_patch_provider": "deepseek",
            "repository_llm_patch_model": "deepseek-v4-pro",
            "repository_llm_patch_api_key_present": False,
            "repository_patch_generator_llm_candidate_count": 0,
            "repository_test_patch_validation_success_count": 0,
            "agent_answers_next_action": (
                "Re-run the LLM repair suite after environment setup."
            ),
        },
    }


def _catalog_llm_direct_success_run(name: str) -> dict:
    return {
        "name": name,
        "repo": f"example/{name}",
        "output_dir": f"out/{name}",
        "report_path": f"out/{name}/github_repo_intelligence.json",
        "status": "pass",
        "passed": True,
        "metrics": {
            "repository_patch_generation_mode": "llm",
            "repository_llm_patch_generation_status": "pass",
            "repository_llm_patch_provider": "deepseek",
            "repository_llm_patch_model": "deepseek-v4-pro",
            "repository_llm_patch_api_key_present": True,
            "repository_patch_generator_llm_candidate_count": 2,
            "repository_test_patch_validation_status": "pass",
            "repository_test_patch_validation_json": (
                f"out/{name}/repository_test_patch_validation.json"
            ),
            "repository_test_patch_validation_markdown": (
                f"out/{name}/repository_test_patch_validation.md"
            ),
            "repository_test_patch_validation_executed_count": 1,
            "repository_test_patch_validation_candidate_count": 2,
            "repository_test_patch_validation_success_count": 1,
            "repository_test_patch_validation_first_success_rank": 1,
            "repository_test_patch_validation_successful_reflection_count": 0,
            "repository_test_patch_judge_mode": "llm",
            "repository_test_patch_judge_status": "ready",
            "repository_test_patch_judge_candidate_count": 1,
            "repository_test_patch_judge_authority": (
                "sandbox_pytest_decides_success"
            ),
            "repository_test_patch_judge_agreement_counts": {"aligned": 1},
            "repository_test_patch_judge_outcome_counts": {"accept_success": 1},
            "agent_answers_next_action": "Generate final agent report.",
        },
    }


def _catalog_llm_reflection_success_run(name: str) -> dict:
    payload = _catalog_llm_direct_success_run(name)
    payload["metrics"].update(
        {
            "repository_test_patch_validation_executed_count": 2,
            "repository_test_patch_validation_reflection_mode": "llm",
            "repository_test_patch_validation_reflection_candidate_count": 2,
            "repository_test_patch_validation_successful_reflection_count": 1,
            "repository_test_patch_validation_llm_reflection_attempt_count": 1,
            "repository_test_patch_validation_llm_reflection_audit": [
                {
                    "parent_patch_id": f"{name}_parent",
                    "round_index": 1,
                    "requested_candidate_count": 2,
                    "parsed_candidate_count": 2,
                    "accepted_candidate_count": 1,
                    "rejected_candidate_count": 1,
                    "prompt_context_audit": {
                        "status": "pass",
                        "missing_fields": [],
                    },
                    "response_parse": {"status": "pass"},
                }
            ],
            "repository_llm_reflection_status": "ready",
            "repository_llm_reflection_provider": "deepseek",
            "repository_llm_reflection_model": "deepseek-v4-pro",
            "repository_test_patch_judge_candidate_count": 2,
            "repository_test_patch_judge_agreement_counts": {
                "aligned": 1,
                "judge_more_conservative": 1,
            },
            "repository_test_patch_judge_outcome_counts": {
                "accept_success": 1,
                "reject_failure": 1,
            },
        }
    )
    return payload


def _catalog_llm_blocker_run(name: str) -> dict:
    return {
        "name": name,
        "repo": f"example/{name}",
        "output_dir": f"out/{name}",
        "report_path": f"out/{name}/llm_config_preflight.json",
        "status": "llm_config_blocked",
        "passed": False,
        "metrics": {
            "status": "llm_config_blocked",
            "blocker": "llm_config_missing_api_key",
            "repository_patch_generation_mode": "llm",
            "repository_llm_patch_generation_status": "blocked",
            "repository_llm_patch_provider": "deepseek",
            "repository_llm_patch_model": "deepseek-v4-pro",
            "repository_llm_patch_api_key_present": False,
            "repository_patch_generator_llm_candidate_count": 0,
            "repository_test_patch_validation_success_count": 0,
            "agent_answers_next_action": (
                "Configure the LLM key and rerun the repair suite."
            ),
        },
    }


def test_intelligence_suite_llm_preflight_blocks_placeholder_key_before_runner(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-deepseek-key")
    for env_name in (
        "CIA_LLM_API_KEY",
        "CIA_JUDGE_API_KEY",
        "DASHSCOPE_API_KEY",
        "ALIBABA_API_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)
    manifest_path = tmp_path / "manifest.json"
    output_dir = tmp_path / "suite"
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "llm_preflight_placeholder_suite",
                "defaults": {
                    "require_llm_configuration": True,
                    "reject_placeholder_llm_api_keys": True,
                    "repository_patch_generation_mode": "llm",
                    "repository_test_reflection_mode": "llm",
                    "patch_judge_mode": "llm",
                },
                "runs": [
                    {
                        "name": "placeholder_llm_key",
                        "repo": "example/project",
                        "expected_status": "pass",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    def fail_if_runner_called(*args, **kwargs):
        raise AssertionError(
            "runner should not be called when LLM preflight rejects placeholder keys"
        )

    monkeypatch.setattr(
        suite_module,
        "run_github_repo_intelligence",
        fail_if_runner_called,
    )

    report = run_github_repo_intelligence_suite(manifest_path, output_dir)
    run = report.runs[0]
    preflight = json.loads(Path(run.report_path).read_text(encoding="utf-8"))
    markdown = (Path(run.output_dir) / "llm_config_preflight.md").read_text(
        encoding="utf-8"
    )

    assert report.passed is False
    assert run.status == "llm_config_blocked"
    assert run.error == (
        "invalid_enabled_llm_api_key_roles:patch_generation,judge"
    )
    assert run.metrics["blocker"] == "llm_config_invalid_api_key"
    assert run.metrics["repository_llm_patch_generation_status"] == "blocked"
    assert run.metrics["repository_llm_patch_generation_reason"] == (
        "invalid_llm_api_key"
    )
    assert run.metrics["repository_llm_reflection_blocker"] == (
        "invalid_api_key:DEEPSEEK_API_KEY"
    )
    assert run.metrics["repository_test_patch_judge_reason"] == (
        "invalid_api_key:DEEPSEEK_API_KEY"
    )
    assert run.metrics["llm_config_missing_enabled_api_key_roles"] == []
    assert run.metrics["llm_config_invalid_enabled_api_key_roles"] == [
        "patch_generation",
        "judge",
    ]
    assert preflight["status"] == "blocked"
    assert preflight["reason"] == "invalid_enabled_llm_api_key"
    assert preflight["missing_enabled_api_key_roles"] == []
    assert preflight["invalid_enabled_api_key_roles"] == [
        "patch_generation",
        "judge",
    ]
    assert [item["reason"] for item in preflight["invalid_api_key_findings"]] == [
        "placeholder_api_key_value",
        "placeholder_api_key_value",
    ]
    assert {
        item["api_key_env"] for item in preflight["invalid_api_key_findings"]
    } == {"DEEPSEEK_API_KEY"}
    serialized = json.dumps(preflight)
    assert "fake-deepseek-key" not in serialized
    assert "sk-" not in serialized
    assert "Invalid Enabled API Key Roles: patch_generation, judge" in markdown
    assert "Replace the placeholder or test API key" in markdown


def test_intelligence_suite_rerun_prefers_cached_discovery(tmp_path):
    raw_source = _write_average_mean(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    output_dir = tmp_path / "suite"
    shared_run_dir = output_dir / "cached_project"
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "repo_intelligence_cache_reuse_suite",
                "defaults": {
                    "source_cache_dir": str(tmp_path / "source_cache"),
                    "max_sources": 5,
                    "max_candidates": 5,
                    "auto_fallback": False,
                    "run_repository_test_command": True,
                },
                "suite_thresholds": {
                    "min_discovery_cache_reuse_count": 1,
                    "min_artifact_core_ready_count": 2,
                    "min_agent_answer_coverage_complete_count": 2,
                },
                "runs": [
                    {
                        "name": "cached_seed",
                        "repo": "example/project",
                        "output_dir": str(shared_run_dir),
                        "recipes": ["missing_len_zero_guard"],
                        "expected_status": "pass",
                    },
                    {
                        "name": "cached_rerun",
                        "repo": "example/project",
                        "output_dir": str(shared_run_dir),
                        "recipes": ["missing_len_zero_guard"],
                        "prefer_cached_discovery": True,
                        "expected_status": "pass",
                    },
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    opener = _FakeOpener(_repo_payloads(raw_source))

    report = run_github_repo_intelligence_suite(
        manifest_path,
        output_dir,
        opener=opener,
    )
    markdown = render_github_repo_intelligence_suite_markdown(report)
    rerun = next(run for run in report.runs if run.name == "cached_rerun")
    rerun_summary = json.loads(
        (shared_run_dir / "github_repo_intelligence.json").read_text(
            encoding="utf-8"
        )
    )

    assert report.passed is True
    assert opener.urls == [
        "https://api.github.com/repos/example/project",
        "https://api.github.com/repos/example/project/git/trees/main?recursive=1",
    ]
    assert report.summary["discovery_cache_reuse_count"] == 1
    assert report.summary["discovery_cache_reuse_runs"] == ["cached_rerun"]
    assert rerun.metrics["discovery_cache_reuse"] is True
    assert rerun.metrics["discovery_cache_preferred"] is True
    assert rerun.metrics["discovery_cache_reuse_reason"] == (
        "prefer_cached_discovery"
    )
    assert "--prefer-cached-discovery" in rerun.command_args
    assert rerun_summary["discovery_source"] == (
        "cached-discovery-preferred:example/project@main"
    )
    assert rerun_summary["acceptance_gate"]["status"] == "pass"
    assert "Discovery Cache Reuse Runs: 1" in markdown


def test_intelligence_suite_seeds_preferred_cached_discovery(tmp_path):
    raw_source = _write_average_mean(tmp_path)
    seed_discovery_path = tmp_path / "seed_discovery.json"
    seed_payload = dict(_repo_payloads(raw_source)[1])
    seed_payload.update(
        {
            "owner": "example",
            "repo": "project",
            "ref": "main",
            "discovery": {},
        }
    )
    seed_discovery_path.write_text(
        json.dumps(seed_payload, indent=2),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    output_dir = tmp_path / "suite"
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "seeded_discovery_suite",
                "defaults": {
                    "source_cache_dir": str(tmp_path / "source_cache"),
                    "max_sources": 5,
                    "max_candidates": 5,
                    "auto_fallback": False,
                    "run_repository_test_command": False,
                },
                "suite_thresholds": {
                    "max_command_failed_count": 0,
                    "max_existing_report_reuse_count": 0,
                    "min_discovery_cache_reuse_count": 1,
                    "min_artifact_core_ready_count": 1,
                    "min_agent_answer_coverage_complete_count": 1,
                },
                "runs": [
                    {
                        "name": "seeded_project",
                        "repo": "example/project",
                        "seed_discovery_path": str(seed_discovery_path),
                        "prefer_cached_discovery": True,
                        "recipes": ["missing_len_zero_guard"],
                        "expected_status": "pass",
                        "metric_thresholds": {
                            "discovery_cache_reuse": 1,
                            "discovery_cache_preferred": 1,
                            "repository_structure_analyzed_file_count": 1,
                        },
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    opener = _FakeOpener([])

    report = run_github_repo_intelligence_suite(
        manifest_path,
        output_dir,
        opener=opener,
    )
    markdown = render_github_repo_intelligence_suite_markdown(report)
    run = report.runs[0]
    saved = json.loads(
        (output_dir / "seeded_project" / "github_repo_intelligence.json").read_text(
            encoding="utf-8"
        )
    )

    assert report.passed is True
    assert opener.urls == []
    assert (output_dir / "seeded_project" / "discovery.json").is_file()
    assert run.metrics["discovery_cache_reuse"] is True
    assert run.metrics["discovery_cache_preferred"] is True
    assert saved["discovery_source"] == (
        "cached-discovery-preferred:example/project@main"
    )
    assert "Discovery Cache Reuse Runs: 1" in markdown


def test_intelligence_suite_can_reuse_cached_report_after_rate_limit(tmp_path):
    output_dir = tmp_path / "cached_run"
    output_dir.mkdir()
    report_path = output_dir / "github_repo_intelligence.json"
    report_path.write_text(
        json.dumps(
            {
                "repo": "example/project",
                "repo_spec": "example/project",
                "status": "pass",
                "passed": True,
                "discovery_source": "cached-discovery:example/project@main",
                "discovery_cache_fallback": True,
                "discovery_cache_fallback_source": str(
                    output_dir / "discovery.json"
                ),
                "analysis_readiness": {
                    "current_stage": "phase2_static_graph_fault_localization",
                    "blocker": "checkout:full_repo_not_materialized",
                },
                "agent_controller": {
                    "control_loop": [
                        "observe",
                        "plan",
                        "act",
                        "verify",
                        "reflect",
                        "replan",
                    ],
                    "decision_trace": [
                        {"phase": "observe"},
                        {"phase": "plan"},
                        {"phase": "act"},
                        {"phase": "verify"},
                        {"phase": "reflect"},
                        {"phase": "replan"},
                    ],
                    "selected_action": {"id": "run_repository_tests_with_checkout"},
                    "observations": [{"name": "repository_structure"}],
                    "plan": [{"step": 1}],
                },
                "artifact_inventory": {
                    "status": "pass",
                    "required_status": "pass",
                    "file_check_enabled": True,
                    "missing_core_artifacts": [],
                    "missing_required_artifacts": [],
                    "groups": {},
                },
                "agent_answers": {
                    "answer_coverage": {
                        "complete": True,
                        "answered_question_count": 7,
                        "required_question_count": 7,
                        "missing_questions": [],
                    },
                    "blocker": "checkout:full_repo_not_materialized",
                    "next_action": "Run repository tests with checkout.",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    error = GitHubAPIError(
        "GitHub API request failed with HTTP 403: rate limit exceeded",
        status_code=403,
        rate_limit_remaining="0",
        response_body="API rate limit exceeded",
    )

    assert _is_rate_limit_error(error) is True
    result = _cached_run_result_from_existing_report(
        name="cached_project",
        repo="example/project",
        output_dir=output_dir,
        expected_status="pass",
        options={"execution_profile": "static"},
        metric_thresholds={
            "agent_controller_loop_complete": 1,
            "agent_answer_coverage_answered_count": 7,
        },
        command_args=["python", "-m", "code_intelligence_agent"],
        error=error,
    )

    assert result is not None
    assert result.status == "pass"
    assert result.passed is True
    assert result.expectation_passed is True
    assert result.error is None
    assert result.report_path == str(report_path)
    assert result.metrics["cached_report_fallback"] is True
    assert result.metrics["discovery_source"] == (
        "cached-discovery:example/project@main"
    )
    assert result.metrics["discovery_cache_fallback"] is True
    assert result.metrics["discovery_cache_fallback_source"] == str(
        output_dir / "discovery.json"
    )
    assert result.metrics["agent_controller_loop_complete"] is True
    assert all(check["passed"] for check in result.metric_checks)


def test_intelligence_suite_can_explicitly_reuse_existing_reports(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    output_dir = tmp_path / "suite"
    run_dir = output_dir / "reused_project"
    run_dir.mkdir(parents=True)
    report_path = run_dir / "github_repo_intelligence.json"
    report_path.write_text(
        json.dumps(
            {
                "repo": "example/project",
                "repo_spec": "https://github.com/example/project",
                "status": "fail",
                "passed": False,
                "analysis_readiness": {
                    "current_stage": "phase1_repo_understanding",
                    "blocker": "no_static_candidates",
                },
                "agent_controller": {
                    "control_loop": [
                        "observe",
                        "plan",
                        "act",
                        "verify",
                        "reflect",
                        "replan",
                    ],
                    "decision_trace": [
                        {"phase": "observe"},
                        {"phase": "plan"},
                        {"phase": "act"},
                        {"phase": "verify"},
                        {"phase": "reflect"},
                        {"phase": "replan"},
                    ],
                    "selected_action": {"id": "run_repository_tests_with_checkout"},
                },
                "artifact_inventory": {
                    "status": "pass",
                    "required_status": "pass",
                    "file_check_enabled": True,
                    "missing_core_artifacts": [],
                    "missing_required_artifacts": [],
                    "groups": {},
                },
                "agent_answers": {
                    "answer_coverage": {
                        "complete": True,
                        "answered_question_count": 7,
                        "required_question_count": 7,
                        "missing_questions": [],
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "reuse_existing_report_suite",
                "defaults": {
                    "max_sources": 1,
                    "max_candidates": 1,
                },
                "suite_thresholds": {
                    "min_existing_report_reuse_count": 1,
                    "min_agent_controller_loop_complete_count": 1,
                    "min_agent_answer_coverage_complete_count": 1,
                },
                "runs": [
                    {
                        "name": "reused_project",
                        "repo": "example/project",
                        "expected_status": "pass",
                        "expected_analysis_stage": (
                            "phase1_repo_understanding"
                        ),
                        "expected_blocker": "no_static_candidates",
                        "expected_controller_action": (
                            "run_repository_tests_with_checkout"
                        ),
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    opener = _FakeOpener([])

    report = run_github_repo_intelligence_suite(
        manifest_path,
        output_dir,
        opener=opener,
        reuse_existing_reports=True,
    )
    markdown = render_github_repo_intelligence_suite_markdown(report)

    assert opener.urls == []
    assert report.passed is True
    assert report.summary["existing_report_reuse_count"] == 1
    assert report.summary["existing_report_reuse_runs"] == ["reused_project"]
    assert report.summary["cached_report_fallback_count"] == 0
    assert report.runs[0].report_path == str(report_path)
    assert report.runs[0].status == "pass"
    assert report.runs[0].passed is True
    assert report.runs[0].metrics["existing_report_reuse"] is True
    assert report.runs[0].metrics["existing_report_reuse_reason"] == (
        "explicit_existing_report_reuse"
    )
    assert report.runs[0].metrics["repo_input_kind"] == "owner_repo"
    assert "Existing Report Reuse Runs: 1" in markdown


def test_intelligence_suite_counts_discovery_cache_fallback_runs():
    result = GitHubRepoIntelligenceSuiteRunResult(
        name="cached_discovery_project",
        repo="example/project",
        output_dir="out",
        command_args=[],
        status="pass",
        passed=True,
        expected_status="pass",
        expectation_checks=[],
        expectation_passed=True,
        metrics={
            "status": "pass",
            "passed": True,
            "discovery_cache_reuse": True,
            "discovery_cache_reuse_reason": "prefer_cached_discovery",
            "discovery_cache_fallback": True,
            "discovery_api_rate_limit_checkout_fallback": True,
            "agent_answer_coverage_complete": True,
        },
        metric_checks=[],
        report_path="out/github_repo_intelligence.json",
        error=None,
    )

    summary = _suite_summary(
        [result],
        suite_thresholds={
            "min_discovery_cache_reuse_count": 1,
            "min_discovery_cache_fallback_count": 1,
            "min_api_rate_limit_checkout_fallback_count": 1,
        },
    )
    markdown = render_github_repo_intelligence_suite_markdown(
        GitHubRepoIntelligenceSuiteReport(
            suite_name="fallback_suite",
            manifest_path="manifest.json",
            output_dir="out",
            passed=True,
            runs=[result],
            summary=summary,
        )
    )

    assert summary["discovery_cache_reuse_count"] == 1
    assert summary["discovery_cache_reuse_runs"] == ["cached_discovery_project"]
    assert summary["discovery_cache_fallback_count"] == 1
    assert summary["discovery_cache_fallback_runs"] == ["cached_discovery_project"]
    assert summary["api_rate_limit_checkout_fallback_count"] == 1
    assert summary["api_rate_limit_checkout_fallback_runs"] == [
        "cached_discovery_project"
    ]
    assert summary["suite_threshold_failed_count"] == 0
    assert "Discovery Cache Reuse Runs: 1" in markdown
    assert "Discovery Cache Fallback Runs: 1" in markdown
    assert "API Rate Limit Checkout Fallback Runs: 1" in markdown


def test_intelligence_suite_counts_effective_exclude_filter_runs():
    result = GitHubRepoIntelligenceSuiteRunResult(
        name="exclude_filtered_project",
        repo="example/project",
        output_dir="out",
        command_args=[],
        status="pass",
        passed=True,
        expected_status="pass",
        expectation_checks=[],
        expectation_passed=True,
        metrics={
            "status": "pass",
            "passed": True,
            "agent_invocation_include_count": 2,
            "agent_invocation_exclude_count": 1,
            "agent_invocation_exclude_reduced_selected_sources": True,
            "agent_answer_coverage_complete": True,
        },
        metric_checks=[],
        report_path="out/github_repo_intelligence.json",
        error=None,
    )

    summary = _suite_summary(
        [result],
        suite_thresholds={
            "min_exclude_filter_requested_count": 1,
            "min_exclude_filter_effective_count": 1,
        },
    )
    markdown = render_github_repo_intelligence_suite_markdown(
        GitHubRepoIntelligenceSuiteReport(
            suite_name="exclude_filter_suite",
            manifest_path="manifest.json",
            output_dir="out",
            passed=True,
            runs=[result],
            summary=summary,
        )
    )

    assert summary["exclude_filter_requested_count"] == 1
    assert summary["exclude_filter_requested_runs"] == ["exclude_filtered_project"]
    assert summary["exclude_filter_effective_count"] == 1
    assert summary["exclude_filter_effective_runs"] == ["exclude_filtered_project"]
    assert summary["suite_threshold_failed_count"] == 0
    assert "Exclude Filter Effective Runs: 1" in markdown


def test_intelligence_suite_counts_blocked_agent_answer_completeness():
    complete = GitHubRepoIntelligenceSuiteRunResult(
        name="static_blocker",
        repo="example/static-blocker",
        output_dir="out/static_blocker",
        command_args=[],
        status="pass",
        passed=True,
        expected_status="pass",
        expectation_checks=[],
        expectation_passed=True,
        metrics={
            "status": "pass",
            "blocker": "no_static_candidates",
            "agent_answer_coverage_complete": True,
            "agent_answers_blocker": "no_static_candidates",
            "agent_answers_next_action": "Collect failing tests or bug report.",
        },
        metric_checks=[],
        report_path="out/static_blocker/github_repo_intelligence.json",
        error=None,
    )
    incomplete = GitHubRepoIntelligenceSuiteRunResult(
        name="missing_next_action",
        repo="example/missing-next-action",
        output_dir="out/missing_next_action",
        command_args=[],
        status="pass",
        passed=True,
        expected_status="pass",
        expectation_checks=[],
        expectation_passed=True,
        metrics={
            "status": "pass",
            "blocker": "source_import_or_parse_missing",
            "agent_answer_coverage_complete": True,
            "agent_answers_blocker": "source_import_or_parse_missing",
            "agent_answers_next_action": "",
        },
        metric_checks=[],
        report_path="out/missing_next_action/github_repo_intelligence.json",
        error=None,
    )
    unblocked = GitHubRepoIntelligenceSuiteRunResult(
        name="repair_ready",
        repo="example/repair-ready",
        output_dir="out/repair_ready",
        command_args=[],
        status="pass",
        passed=True,
        expected_status="pass",
        expectation_checks=[],
        expectation_passed=True,
        metrics={
            "status": "pass",
            "blocker": "",
            "agent_answer_coverage_complete": True,
            "agent_answers_blocker": "none",
            "agent_answers_next_action": "Run patch validation.",
        },
        metric_checks=[],
        report_path="out/repair_ready/github_repo_intelligence.json",
        error=None,
    )

    summary = _suite_summary(
        [complete, incomplete, unblocked],
        suite_thresholds={
            "min_blocked_agent_answer_complete_count": 1,
            "max_blocked_agent_answer_incomplete_count": 1,
        },
    )
    markdown = render_github_repo_intelligence_suite_markdown(
        GitHubRepoIntelligenceSuiteReport(
            suite_name="blocked_agent_answer_suite",
            manifest_path="manifest.json",
            output_dir="out",
            passed=True,
            runs=[complete, incomplete, unblocked],
            summary=summary,
        )
    )

    assert summary["blocked_agent_answer_complete_count"] == 1
    assert summary["blocked_agent_answer_complete_runs"] == ["static_blocker"]
    assert summary["blocked_agent_answer_incomplete_count"] == 1
    assert summary["blocked_agent_answer_incomplete_runs"] == [
        "missing_next_action"
    ]
    assert all(check["passed"] for check in summary["suite_threshold_checks"])
    assert "Blocked Agent Answer Complete Runs: 1" in markdown
    assert "Blocked Agent Answer Incomplete Runs: 1" in markdown


def test_intelligence_suite_temporarily_clears_llm_keys():
    os.environ["CIA_LLM_API_KEY"] = "temporary-test-key"
    os.environ["DEEPSEEK_API_KEY"] = "temporary-deepseek-key"

    with _temporary_cleared_env(["CIA_LLM_API_KEY", "DEEPSEEK_API_KEY"]):
        assert os.environ.get("CIA_LLM_API_KEY") is None
        assert os.environ.get("DEEPSEEK_API_KEY") is None

    assert os.environ["CIA_LLM_API_KEY"] == "temporary-test-key"
    assert os.environ["DEEPSEEK_API_KEY"] == "temporary-deepseek-key"
    os.environ.pop("CIA_LLM_API_KEY", None)
    os.environ.pop("DEEPSEEK_API_KEY", None)


def test_intelligence_suite_applies_agent_auto_execution_profile_to_command():
    options = _apply_execution_profile_options(
        {
            "execution_profile": "agent-auto",
            "repository_test_timeout": 20,
            "auto_controller_max_actions": 3,
            "repository_patch_generation_mode": "hybrid",
            "repository_patch_candidate_variant_allowlist": [
                "insert_len_zero_guard"
            ],
            "prefer_cached_discovery": True,
        }
    )
    command_args = _command_args("example/project", Path("out"), options)
    default_options = _apply_execution_profile_options(
        {
            "execution_profile": "agent-auto",
        }
    )

    assert options["auto_controller_actions"] is True
    assert options["repository_test_timeout"] == 20
    assert default_options["repository_test_timeout"] == 30
    assert options["repository_patch_generation_mode"] == "hybrid"
    assert "--execution-profile" in command_args
    assert "agent-auto" in command_args
    assert "--repository-patch-generation-mode" in command_args
    assert "hybrid" in command_args
    assert "--repository-patch-candidate-variant" in command_args
    assert "insert_len_zero_guard" in command_args
    assert "--prefer-cached-discovery" in command_args
    assert "--auto-controller-actions" in command_args
    assert command_args[
        command_args.index("--auto-controller-max-actions") + 1
    ] == "3"
    assert command_args[
        command_args.index("--repository-test-timeout") + 1
    ] == "20"


def test_intelligence_suite_applies_agent_shortcut_to_command():
    options = _apply_execution_profile_options(
        {
            "agent": True,
            "auto_controller_max_actions": 2,
            "repository_patch_generation_mode": "rule",
        }
    )
    command_args = _command_args("example/project", Path("out"), options)

    assert options["execution_profile"] == "agent-auto"
    assert options["auto_controller_actions"] is True
    assert options["auto_controller_max_actions"] == 4
    assert "--agent" in command_args
    assert "--execution-profile" not in command_args
    assert "--auto-controller-actions" not in command_args
    assert command_args[
        command_args.index("--auto-controller-max-actions") + 1
    ] == "4"


def test_intelligence_suite_applies_phase3_fast_execution_profile_to_command():
    options = _apply_execution_profile_options(
        {
            "execution_profile": "phase3-fast",
            "repository_test_timeout": 20,
            "auto_repository_test_retry_max_risk": "low",
            "repository_test_failure_overlay_candidate_limit": 5,
            "repository_test_reflection_mode": "rule",
            "repository_test_reflection_rounds": 1,
            "repository_test_reflection_width": 1,
            "repository_patch_generation_mode": "hybrid",
            "repository_llm_patch_candidate_limit": 2,
        }
    )
    command_args = _command_args("example/project", Path("out"), options)
    default_options = _apply_execution_profile_options(
        {
            "execution_profile": "phase3-fast",
        }
    )

    assert options["checkout_repository_tests"] is True
    assert options["run_repository_test_retry_prerequisites"] is True
    assert options["auto_repository_test_retry"] is True
    assert options["repository_test_timeout"] == 20
    assert default_options["repository_test_timeout"] == 30
    assert options["auto_repository_test_retry_max_risk"] == "medium"
    assert options["auto_repository_test_retry_allowed_runners"] == [
        "pytest",
        "unittest",
    ]
    assert "--execution-profile" in command_args
    assert "phase3-fast" in command_args
    assert "--checkout-repository-tests" in command_args
    assert "--run-repository-test-retry-prerequisites" in command_args
    assert "--auto-repository-test-retry" in command_args
    assert command_args.count("--auto-repository-test-retry-runner") == 2
    assert command_args[
        command_args.index("--repository-test-failure-overlay-candidate-limit") + 1
    ] == "5"
    assert command_args[
        command_args.index("--repository-test-reflection-mode") + 1
    ] == "rule"
    assert command_args[
        command_args.index("--repository-test-reflection-rounds") + 1
    ] == "1"
    assert command_args[
        command_args.index("--repository-test-reflection-width") + 1
    ] == "1"
    assert command_args[
        command_args.index("--repository-patch-generation-mode") + 1
    ] == "hybrid"
    assert command_args[
        command_args.index("--repository-llm-patch-candidate-limit") + 1
    ] == "2"


def test_intelligence_suite_thresholds_fail_for_missing_runner_diversity():
    summary = _suite_summary(
        [
            GitHubRepoIntelligenceSuiteRunResult(
                name="pytest_repo",
                repo="example/pytest-repo",
                output_dir="out/pytest_repo",
                report_path="out/pytest_repo/github_repo_intelligence.json",
                status="pass",
                passed=True,
                expected_status="pass",
                expectation_passed=True,
                metrics={
                    "status": "pass",
                    "static_intelligence_status": "analysis_ready",
                    "analysis_stage": "phase2_static_graph_fault_localization",
                    "controller_action_id": "run_repository_tests_with_checkout",
                    "artifact_inventory_status": "pass",
                    "artifact_inventory_core_ready": True,
                    "artifact_inventory_file_check_enabled": True,
                    "planned_repository_test_runner": "pytest",
                    "planned_repository_test_failure_context_line_count": 7,
                    "planned_repository_test_runner_fallback_used": True,
                    "planned_repository_test_runner_fallback_reason": (
                        "missing_runner:tox"
                    ),
                    "repository_test_framework_signals": ["pytest"],
                    "repository_test_command_candidate_runner_counts": {
                        "pytest": 1
                    },
                    "repository_test_dynamic_evidence_level": "none",
                    "repository_test_dynamic_traceback_frames": 2,
                    "agent_auto_loop_progress_count": 1,
                    "agent_auto_loop_no_progress_count": 0,
                    "agent_auto_complete_loop_recorded": True,
                    "agent_auto_verify_outcome_counts": {
                        "dynamic_fault_localization_ready": 1
                    },
                    "agent_auto_replan_policy_counts": {
                        "continue_observe_plan_act": 1
                    },
                },
                metric_checks=[],
                expectation_checks=[],
                command_args=[],
            )
        ],
        suite_thresholds={
            "min_planned_repository_test_runner_kind_count": 2,
            "min_repository_test_dynamic_evidence_level_kind_count": 1,
        },
    )

    assert summary["planned_repository_test_runner_counts"] == {"pytest": 1}
    assert summary["planned_repository_test_runner_kind_count"] == 1
    assert summary["planned_repository_test_failure_context_line_count"] == 7
    assert summary["repository_test_dynamic_traceback_frame_count"] == 2
    assert summary["planned_repository_test_runner_fallback_count"] == 1
    assert summary["planned_repository_test_runner_fallback_runs"] == [
        "pytest_repo"
    ]
    assert summary["planned_repository_test_runner_fallback_reason_counts"] == {
        "missing_runner:tox": 1
    }
    assert summary["planned_repository_test_runner_fallback_reason_kind_count"] == 1
    assert summary["repository_test_framework_counts"] == {"pytest": 1}
    assert summary["repository_test_framework_kind_count"] == 1
    assert summary["repository_test_command_candidate_runner_counts"] == {"pytest": 1}
    assert summary["repository_test_command_candidate_runner_kind_count"] == 1
    assert summary["repository_test_dynamic_evidence_level_counts"] == {"none": 1}
    assert summary["agent_auto_loop_progress_count"] == 1
    assert summary["agent_auto_complete_loop_count"] == 1
    assert summary["agent_auto_verify_outcome_counts"] == {
        "dynamic_fault_localization_ready": 1
    }
    assert summary["agent_auto_replan_policy_counts"] == {
        "continue_observe_plan_act": 1
    }
    assert summary["suite_threshold_failed_count"] == 1
    assert summary["suite_threshold_checks"][0]["name"] == (
        "min_planned_repository_test_runner_kind_count"
    )
    assert summary["suite_threshold_checks"][0]["passed"] is False


def test_intelligence_suite_summarizes_patch_repair_readiness_metrics():
    summary = _suite_summary(
        [
            GitHubRepoIntelligenceSuiteRunResult(
                name="repair_repo",
                repo="example/repair-repo",
                output_dir="out/repair_repo",
                report_path="out/repair_repo/github_repo_intelligence.json",
                status="pass",
                passed=True,
                expected_status="pass",
                expectation_passed=True,
                metrics={
                    "status": "pass",
                    "static_intelligence_status": "analysis_ready",
                    "analysis_stage": "phase3_patch_validation",
                    "controller_action_id": "run_search_and_ablation_evaluation",
                    "artifact_inventory_status": "pass",
                    "artifact_inventory_core_ready": True,
                    "artifact_inventory_file_check_enabled": True,
                    "phase4_search_evaluation_status": "ready",
                    "phase4_search_evaluation_executed": True,
                    "phase4_search_evaluation_execution_status": "pass",
                    "phase4_ready_for_evaluation": True,
                    "phase4_baseline_regression_caveat": True,
                    "phase4_full_suite_green_claim_allowed": False,
                    "repository_test_failure_overlay_status": "pass",
                    "repository_test_patch_candidates_status": "pass",
                    "repository_test_patch_validation_status": "pass",
                    "repository_test_patch_validation_success_count": 1,
                    "repository_test_repair_ready": True,
                    "repository_test_repair_validation_scope": "repository_tests",
                    "repository_test_patch_validation_reflection_candidate_count": 2,
                    "repository_test_patch_validation_successful_reflection_count": 1,
                },
                metric_checks=[],
                expectation_checks=[],
                command_args=[],
            )
        ],
        suite_thresholds={
            "min_repository_test_failure_overlay_ready_count": 1,
            "min_repository_test_patch_candidates_ready_count": 1,
            "min_repository_test_patch_validation_ready_count": 1,
            "min_repository_test_patch_validation_success_count": 1,
            "min_repository_test_repair_ready_count": 1,
            "min_repository_test_patch_validation_reflection_candidate_count": 2,
            "min_repository_test_patch_validation_successful_reflection_count": 1,
        },
    )
    markdown = render_github_repo_intelligence_suite_markdown(
        type(
            "Report",
            (),
            {
                "suite_name": "repair_suite",
                "manifest_path": "manifest.json",
                "output_dir": "out",
                "passed": True,
                "summary": summary,
                "runs": [],
            },
        )()
    )

    assert summary["repository_test_failure_overlay_status_counts"] == {"pass": 1}
    assert summary["repository_test_patch_candidates_status_counts"] == {"pass": 1}
    assert summary["repository_test_patch_validation_status_counts"] == {"pass": 1}
    assert summary["repository_test_repair_ready_count"] == 1
    assert summary["phase4_search_evaluation_status_counts"] == {"ready": 1}
    assert summary["phase4_search_evaluation_execution_status_counts"] == {
        "pass": 1
    }
    assert summary["phase4_ready_count"] == 1
    assert summary["phase4_ready_runs"] == ["repair_repo"]
    assert summary["phase4_executed_count"] == 1
    assert summary["phase4_executed_runs"] == ["repair_repo"]
    assert summary["phase4_baseline_regression_caveat_count"] == 1
    assert summary["phase4_baseline_regression_caveat_runs"] == ["repair_repo"]
    assert summary["phase4_full_suite_green_claim_allowed_count"] == 0
    assert summary["repository_test_patch_validation_success_count"] == 1
    assert summary[
        "repository_test_patch_validation_reflection_candidate_count"
    ] == 2
    assert summary[
        "repository_test_patch_validation_successful_reflection_count"
    ] == 1
    assert summary["suite_threshold_failed_count"] == 0
    assert "Repository Test Repair Ready Runs: 1" in markdown
    assert "Repository Test Reflection Candidates: 2" in markdown
    assert "Repository Test Reflection Successes: 1" in markdown
    assert "Phase 4 Ready Runs: 1" in markdown
    assert "Phase 4 Executed Runs: 1" in markdown
    assert "Phase 4 Evaluation Statuses: ready=1" in markdown
    assert "Phase 4 Execution Statuses: pass=1" in markdown
    assert "Patch Validation Statuses: pass=1" in markdown


def test_intelligence_suite_distinguishes_attemptable_from_repair_ready():
    summary = _suite_summary(
        [
            GitHubRepoIntelligenceSuiteRunResult(
                name="partial_repair_repo",
                repo="example/project",
                output_dir="out/partial_repair_repo",
                report_path="out/partial_repair_repo/github_repo_intelligence.json",
                status="fail",
                passed=False,
                expected_status="fail",
                expectation_passed=True,
                metrics={
                    "status": "fail",
                    "analysis_stage": "phase3_patch_validation",
                    "controller_action_id": "run_patch_reflection_loop",
                    "can_attempt_patch_repair": True,
                    "repository_test_patch_validation_status": "pass",
                    "repository_test_patch_validation_success_count": 1,
                    "repository_test_repair_ready": False,
                    "repository_test_repair_validation_scope": "regression_failed",
                },
                metric_checks=[],
                expectation_checks=[],
                command_args=[],
            )
        ],
        suite_thresholds={},
    )

    assert summary["patch_repair_attemptable_count"] == 1
    assert summary["patch_repair_attemptable_runs"] == ["partial_repair_repo"]
    assert summary["patch_repair_ready_count"] == 0
    assert summary["patch_repair_ready_runs"] == []
    assert summary["repository_test_repair_ready_count"] == 0
    assert summary["repository_test_repair_validation_scope_counts"] == {
        "regression_failed": 1
    }


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


def _repo_payloads(raw_source: Path) -> list[dict]:
    helper_source = raw_source.parent / "helpers.py"
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
                    "path": "maths/average_mean.py",
                    "type": "blob",
                    "raw_url": str(raw_source),
                    "sha256": hashlib.sha256(raw_source.read_bytes()).hexdigest(),
                    "license": "MIT",
                },
                {
                    "path": "helpers.py",
                    "type": "blob",
                    "raw_url": str(helper_source),
                    "sha256": hashlib.sha256(
                        helper_source.read_bytes()
                    ).hexdigest(),
                    "license": "MIT",
                },
            ],
        },
    ]


def _shift_left_repo_payloads(raw_source: Path) -> list[dict]:
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
                    "path": "sample.py",
                    "type": "blob",
                    "raw_url": str(raw_source),
                    "sha256": hashlib.sha256(raw_source.read_bytes()).hexdigest(),
                    "license": "MIT",
                },
            ],
        },
    ]


def _unittest_repo_payloads(source_path: Path, test_path: Path) -> list[dict]:
    return [
        {"default_branch": "main"},
        {
            "sha": "abc123",
            "tree": [
                {
                    "path": "sample.py",
                    "type": "blob",
                    "raw_url": str(source_path),
                    "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                    "license": "MIT",
                },
                {
                    "path": "tests/test_sample.py",
                    "type": "blob",
                    "raw_url": str(test_path),
                    "sha256": hashlib.sha256(test_path.read_bytes()).hexdigest(),
                    "license": "MIT",
                },
            ],
        },
    ]


def _repo_payloads_without_python() -> list[dict]:
    return [
        {"default_branch": "main"},
        {
            "sha": "abc123",
            "tree": [
                {
                    "path": "README.md",
                    "type": "blob",
                },
                {
                    "path": "docs/guide.txt",
                    "type": "blob",
                },
            ],
        },
    ]


def _write_average_mean(root: Path) -> Path:
    raw_source = root / "average_mean.py"
    raw_source.write_text(
        "from helpers import normalize\n"
        "\n"
        "def mean(nums):\n"
        "    normalize(nums)\n"
        "    if not nums:\n"
        "        raise ValueError(\"List is empty\")\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
    (root / "helpers.py").write_text(
        "def normalize(values):\n"
        "    return list(values)\n",
        encoding="utf-8",
    )
    return raw_source


def _write_shift_left(root: Path) -> Path:
    raw_source = root / "sample.py"
    raw_source.write_text(
        "def shift_left(values):\n"
        "    shifted = []\n"
        "    for i in range(len(values)):\n"
        "        shifted.append(values[i + 1])\n"
        "    return shifted\n",
        encoding="utf-8",
    )
    return raw_source


def _write_unittest_repo(root: Path) -> tuple[Path, Path]:
    source_path = root / "sample.py"
    source_path.write_text(
        "def shift_left(values):\n"
        "    shifted = []\n"
        "    for i in range(len(values)):\n"
        "        shifted.append(values[i + 1])\n"
        "    return shifted\n",
        encoding="utf-8",
    )
    tests_dir = root / "tests"
    tests_dir.mkdir()
    test_path = tests_dir / "test_sample.py"
    test_path.write_text(
        "import unittest\n\n"
        "from sample import shift_left\n\n"
        "class ShiftLeftTest(unittest.TestCase):\n"
        "    def test_shift_left_short_values(self):\n"
        "        self.assertEqual(shift_left([1]), [])\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
        encoding="utf-8",
    )
    return source_path, test_path
