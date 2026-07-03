import hashlib
import json
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from code_intelligence_agent.agents.patch_generator import PatchGenerator
from code_intelligence_agent.agents.controller import build_agent_controller_plan
import code_intelligence_agent.evaluation.github_benchmark_onboarding as onboarding_module
import code_intelligence_agent.evaluation.github_repo_intelligence as intelligence_module
from code_intelligence_agent.core.models import ExecutionResult
from code_intelligence_agent.evaluation.github_discovery_fetcher import GitHubAPIError
from code_intelligence_agent.evaluation.github_repo_intelligence import (
    build_arg_parser,
    github_repo_intelligence_summary,
    main as repo_intelligence_main,
    render_github_repo_intelligence_summary,
    run_github_repo_intelligence,
    write_github_repo_intelligence_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_reflection_trace import (
    build_repository_test_reflection_trace,
)


def test_github_repo_intelligence_defaults_to_static_analysis_summary():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        summary = github_repo_intelligence_summary(report)
        markdown = render_github_repo_intelligence_summary(report)
        markdown = render_github_repo_intelligence_summary(report)

        assert report.preset == "mining"
        assert summary["agent_invocation"]["effective_execution_profile"] == "static"
        assert summary["agent_invocation"]["agent_mode"] is False
        assert summary["agent_invocation"]["auto_controller_actions"] is False
        assert summary["agent_invocation"]["auto_controller_max_actions"] == 2
        assert report.summary["benchmark_cases"] == 0
        assert summary["discovery_source"] == "github-tree:example/project@main"
        assert summary["discovery_cache_fallback"] is False
        assert summary["static_intelligence_status"] == "analysis_ready"
        assert summary["static_intelligence_level"] == "static_signals"
        assert summary["selected_signal_count"] == 1
        assert summary["total_signal_count"] == 1
        assert summary["rule_counts"] == {"missing_len_zero_guard": 1}
        assert summary["repository_structure"]["analyzed_file_count"] == 2
        assert summary["repository_structure"]["function_count"] == 2
        assert summary["repository_structure"]["class_count"] == 0
        assert summary["repository_structure"]["call_site_count"] == 5
        assert summary["repository_structure"]["max_cyclomatic_complexity"] == 2
        assert summary["repository_structure"]["package_structure"][
            "package_roots"
        ] == []
        assert summary["repository_structure"]["package_structure"][
            "src_layout_packages"
        ] == []
        assert summary["repository_structure"]["test_structure"][
            "test_source_count"
        ] == 0
        assert summary["repository_structure"]["test_structure"][
            "recommended_test_command"
        ] == "python -m pytest"
        assert summary["repository_structure"]["project_config"][
            "project_config_files"
        ] == ["pyproject.toml"]
        assert summary["repository_structure"]["project_config"][
            "dependency_tool_signals"
        ] == ["pyproject"]
        assert summary["repository_structure"]["project_config"][
            "dependency_file_count"
        ] == 1
        assert summary["repository_structure"]["project_config"][
            "packaging_file_count"
        ] == 1
        assert summary["repository_structure"]["project_config"][
            "dependency_manager_profile"
        ]["reason"] == "dependency_config_detected"
        assert summary["repo_graph"]["file_node_count"] == 2
        assert summary["repo_graph"]["function_node_count"] == 2
        assert summary["repo_graph"]["file_dependency_edge_count"] == 1
        assert summary["repo_graph"]["function_call_edge_count"] == 1
        program_graph = summary["repo_graph"]["program_graph"]
        assert program_graph["available"] is True
        assert program_graph["node_type_counts"]["function"] == 2
        assert program_graph["edge_type_counts"]["calls"] == 1
        assert program_graph["module_dependency_edge_count"] == 1
        assert program_graph["cross_function_data_flow_edge_count"] == 1
        assert program_graph["cfg_edge_count"] >= 1
        assert program_graph["module_dependency_edges_preview"][0][
            "caller_file"
        ] == "average_mean.py"
        assert program_graph["module_dependency_edges_preview"][0][
            "callee_file"
        ] == "helpers.py"
        assert program_graph["cross_function_data_flow_edges_preview"][0][
            "source_variable"
        ] == "nums"
        assert program_graph["cross_function_data_flow_edges_preview"][0][
            "target_variable"
        ] == "values"
        assert summary["repo_graph"]["top_function_nodes"][0]["name"] == (
            "normalize"
        )
        assert summary["static_fault_localization"]["status"] == "pass"
        assert summary["static_fault_localization"]["reason"] == (
            "ranked_static_candidates_with_repo_graph"
        )
        assert summary["static_fault_localization"]["candidate_function_count"] == 1
        assert summary["static_fault_localization"]["top_function"] == "mean"
        assert summary["static_fault_localization"]["rankings"][0][
            "static_rule_score"
        ] == 1.0
        assert summary["static_fault_localization"]["rankings"][0][
            "graph_score"
        ] == 1.0
        assert summary["static_fault_localization"]["rankings"][0][
            "source_role"
        ] == "application"
        assert summary["static_fault_localization"]["rankings"][0][
            "source_role_score"
        ] == 1.0
        assert summary["static_fault_localization"]["rankings"][0][
            "final_score"
        ] == 1.0
        assert summary["fault_localization"]["mode"] == "static_fallback"
        assert summary["fault_localization"]["status"] == "pass"
        assert summary["fault_localization"]["reason"] == (
            "static_fallback_no_dynamic_ranking"
        )
        assert summary["fault_localization"]["top_function"] == "mean"
        assert summary["fault_localization"]["rankings"][0]["final_score"] == 1.0
        final_report = summary["final_report"]
        assert final_report["repo"] == "example/project"
        assert final_report["repository_structure"]["analyzed_files"] == 2
        assert final_report["top_suspicious_function"] == "mean"
        assert final_report["application_candidate_coverage"]["status"] == (
            "application_candidates_ranked"
        )
        assert final_report["controller"]["selected_action"] == (
            "run_repository_tests_with_checkout"
        )
        assert final_report["verification"]["answer_coverage_complete"] is True
        assert final_report["verification"]["repair_success_claim"] == "not_claimed"
        assert final_report["evidence_artifacts"]["fault_localization_json"].endswith(
            "fault_localization.json"
        )
        answers = summary["agent_answers"]
        assert answers["repository_structure"]["analyzed_files"] == 2
        assert answers["repository_structure"]["functions"] == 2
        assert answers["repository_structure"]["project_config"][
            "project_config_files"
        ] == ["pyproject.toml"]
        assert "project config files: pyproject.toml" in (
            answers["repository_structure_answer"]
        )
        assert answers["top_suspicious_functions"][0]["function"] == "mean"
        assert answers["top_suspicious_functions"][0]["mode"] == "static_fallback"
        assert answers["application_candidate_coverage"]["status"] == (
            "application_candidates_ranked"
        )
        assert answers["application_candidate_coverage"][
            "application_candidate_count"
        ] == 1
        assert answers["application_candidate_coverage"]["source_role_counts"] == {
            "application": 1,
        }
        assert "Top-k includes application-source candidates" in (
            answers["application_candidate_coverage_answer"]
        )
        assert "StaticRuleScore=1.0000" in (
            answers["top_suspicious_functions"][0]["why"]
        )
        assert "SourceRole=application" in (
            answers["top_suspicious_functions"][0]["why"]
        )
        assert answers["blocker"] == "checkout:full_repo_not_materialized"
        assert answers["selected_controller_action"] == (
            "run_repository_tests_with_checkout"
        )
        assert answers["next_action"] == (
            "Static localization is ready; collect repository-test evidence next."
        )
        assert answers["testability"]["status"] == (
            "can_attempt_with_checkout_or_setup"
        )
        assert answers["repairability"]["status"] == (
            "needs_dynamic_evidence_or_patch_context"
        )
        assert answers["artifact_inventory"]["status"] == "pass"
        assert answers["artifact_inventory"]["core_ready"] is True
        assert answers["artifact_inventory"]["missing_core_artifacts"] == []
        assert answers["testability"]["answer"]
        assert "All core intelligence artifacts" in (
            answers["artifact_inventory_answer"]
        )
        assert answers["answer_coverage_complete"] is True
        assert answers["answer_coverage_answered_count"] == 7
        assert answers["answer_coverage_required_count"] == 7
        assert answers["answer_coverage_missing_questions"] == []
        assert answers["answer_coverage"]["complete"] is True
        assert [
            item["id"] for item in answers["answer_coverage"]["questions"]
        ] == [
            "repository_structure",
            "suspicious_functions",
            "suspicious_reason",
            "testability",
            "repairability",
            "blocker",
            "next_action",
        ]
        assert "Top suspicious function: mean" in answers["executive_summary"]
        assert "Artifacts: pass" in answers["executive_summary"]
        assert "Discovery Source: `github-tree:example/project@main`" in markdown
        assert "Discovery Cache Fallback: false" in markdown
        readiness = summary["analysis_readiness"]
        assert readiness["current_stage"] == "phase2_static_graph_fault_localization"
        assert readiness["stage_number"] == 2
        assert readiness["next_stage"] == "phase3_repository_test_execution"
        assert readiness["can_generate_static_report"] is True
        assert readiness["can_attempt_patch_repair"] is False
        assert "phase1_repo_understanding" in readiness["completed_phases"]
        assert "phase2_static_bug_signal_mining" in readiness["completed_phases"]
        assert (
            "phase2_static_graph_fault_localization"
            in readiness["completed_phases"]
        )
        test_structure = summary["repository_structure"]["test_structure"]
        assert test_structure["test_framework_signals"] == []
        assert test_structure["test_command_runner_counts"] == {"pytest": 1}
        assert test_structure["test_command_candidates"][0]["runner"] == "pytest"
        assert test_structure["test_command_candidates"][0]["command"] == (
            "python -m pytest"
        )
        controller = summary["agent_controller"]
        assert controller["agent_type"] == "code_intelligence_controller"
        assert controller["control_loop"] == [
            "observe",
            "plan",
            "act",
            "verify",
            "reflect",
            "replan",
        ]
        assert [step["phase"] for step in controller["decision_trace"]] == [
            "observe",
            "plan",
            "act",
            "verify",
            "reflect",
            "replan",
        ]
        assert controller["verification"]["expected_artifact"] == (
            "repository_test_dynamic_evidence.json"
        )
        assert controller["replan"]["next_policy"] == (
            "classify_blocker_and_select_recovery_action"
        )
        assert controller["current_stage"] == (
            "phase2_static_graph_fault_localization"
        )
        assert controller["selected_action"]["id"] == (
            "run_repository_tests_with_checkout"
        )
        assert controller["selected_action"]["phase"] == "phase3"
        assert controller["reflection"]["fallback_action"] == (
            "collect_dynamic_failure_evidence"
        )
        controller_observations = {
            item["signal"]: item["value"] for item in controller["observations"]
        }
        assert controller_observations["project_config_files"] == "pyproject.toml"
        assert controller_observations["dependency_tool_signals"] == "pyproject"
        assert controller_observations["dependency_file_count"] == "1"
        assert controller_observations["packaging_file_count"] == "1"
        assert [step["mode"] for step in controller["plan"]] == [
            "observe",
            "plan",
            "act",
            "verify",
            "reflect",
            "replan",
        ]
        assert summary["repository_structure"]["top_complexity_functions"][0][
            "name"
        ] == "mean"
        assert summary["agent_json"].endswith("github_repo_agent.json")
        assert Path(summary["agent_json"]).exists()
        assert Path(summary["agent_markdown"]).exists()
        assert "GitHub Repository Intelligence Summary" in markdown
        assert "Invocation: profile=`static`, agent=false" in markdown
        assert "Static Intelligence: `analysis_ready`/`static_signals`" in markdown
        assert "Rule Counts: missing_len_zero_guard=1" in markdown
        assert "Repository Structure" in markdown
        assert "Max Cyclomatic Complexity: 2" in markdown
        assert "Package And Test Layout" in markdown
        assert "Project Config Files: pyproject.toml" in markdown
        assert "Dependency Tool Signals: pyproject" in markdown
        assert "Test Command Candidate Runners: pytest=1" in markdown
        assert "Test Command Candidates" in markdown
        assert "Recommended Test Command: `python -m pytest`" in markdown
        assert "| mean |" in markdown
        assert "Repo Graph" in markdown
        assert "File Dependency Edges: 1" in markdown
        assert "Function Call Edges: 1" in markdown
        assert "Program Graph" in markdown
        assert "Cross-function Data-flow Edges: 1" in markdown
        assert "CFG Edges:" in markdown
        assert "Analysis Readiness" in markdown
        assert "Current Stage: `phase2_static_graph_fault_localization`" in markdown
        assert "Agent Controller" in markdown
        assert "Selected Action: `run_repository_tests_with_checkout`" in markdown
        assert "observe -> plan -> act -> verify -> reflect -> replan" in markdown
        assert "Decision Trace" in markdown
        assert "Fault Localization" in markdown
        assert "Mode: `static_fallback`" in markdown
        assert "Static Fault Localization" in markdown
        assert "StaticRuleScore" in markdown
        assert "SourceRoleScore" in markdown
        assert "| 1 | mean |" in markdown
        assert "Agent Answers" in markdown
        assert "Top Suspicious Functions" in markdown
        assert "Can Test:" in markdown
        assert "Can Repair:" in markdown
        assert "Audit Artifacts:" in markdown
        assert "top function is mean" in markdown


def test_static_fault_localization_prefers_application_code_over_automation():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        source_mining_path = root / "source_mining.json"
        source_mining_path.write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "id": "app",
                            "target_path": "src/pkg/core.py",
                            "function_name": "parse_value",
                            "rule_ids": ["broad_exception_pass"],
                            "bug_type": "exception handling error",
                        },
                        {
                            "id": "nox",
                            "target_path": "noxfile.py",
                            "function_name": "tests",
                            "rule_ids": ["broad_exception_pass"],
                            "bug_type": "exception handling error",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        report = intelligence_module.GitHubRepoAgentReport(
            repo_spec="example/project",
            owner="example",
            repo="project",
            output_dir=str(root),
            preset="mining",
            status="pass",
            summary={},
            output_paths={"source_mining_json": str(source_mining_path)},
            onboarding_report=None,
        )
        repo_graph = {
            "function_nodes": [
                {
                    "id": "src/pkg/core.py::parse_value",
                    "name": "parse_value",
                    "file_path": "src/pkg/core.py",
                    "start_line": 1,
                    "end_line": 5,
                    "score": 1.0,
                },
                {
                    "id": "noxfile.py::tests",
                    "name": "tests",
                    "file_path": "noxfile.py",
                    "start_line": 1,
                    "end_line": 5,
                    "score": 1.0,
                },
            ]
        }

        payload = intelligence_module._static_fault_localization_summary(
            report,
            repo_graph,
            top_k=2,
        )

        assert payload["weights"] == {
            "static_rule": 0.55,
            "graph": 0.30,
            "source_role": 0.15,
        }
        assert payload["rankings"][0]["function_name"] == "parse_value"
        assert payload["rankings"][0]["source_role"] == "application"
        assert payload["rankings"][0]["source_role_score"] == 1.0
        assert payload["rankings"][1]["function_name"] == "tests"
        assert payload["rankings"][1]["source_role"] == "test_automation"
        assert payload["rankings"][1]["source_role_score"] == 0.55
        assert payload["rankings"][0]["final_score"] > payload["rankings"][1][
            "final_score"
        ]
        assert payload["source_role_counts"] == {
            "application": 1,
            "test_automation": 1,
        }
        assert payload["application_candidate_count"] == 1
        assert payload["top_application_function"] == "parse_value"
        assert payload["non_application_topk_only"] is False


def test_agent_answers_audit_missing_application_candidates():
    payload = {
        "repository_structure": {
            "package_structure": {
                "src_layout_packages": ["pkg"],
                "package_roots": ["src"],
                "recommended_target_prefix": "pkg",
            }
        },
        "fault_localization": {
            "mode": "static_fallback",
            "rankings": [
                {
                    "rank": 1,
                    "function_name": "tests",
                    "function_id": "noxfile.py::tests",
                    "file_path": "noxfile.py",
                    "source_role": "test_automation",
                    "source_role_score": 0.55,
                    "static_rule_score": 1.0,
                    "graph_score": 1.0,
                    "sbfl_score": 0.0,
                    "dynamic_test_evidence_score": 0.0,
                    "final_score": 0.9325,
                }
            ],
            "source_role_counts": {"test_automation": 1},
            "top_source_role": "test_automation",
            "application_candidate_count": 0,
        },
        "analysis_readiness": {
            "blocker": "dynamic_evidence_not_usable:passing_tests"
        },
        "reflection_summary": {},
        "artifact_inventory": {},
        "agent_controller": {
            "selected_action": {"reason": "Collect failing-test evidence."}
        },
    }

    answers = intelligence_module._agent_answers_summary(payload)
    coverage = answers["application_candidate_coverage"]

    assert coverage["status"] == "no_application_candidates_ranked"
    assert coverage["recommended_target_prefix"] == "pkg"
    assert coverage["application_source_hints"] == ["pkg", "src"]
    assert "Top-k currently contains no application-source candidates" in (
        coverage["answer"]
    )
    assert "SourceRole=test_automation" in answers["why_suspicious_answer"]


def test_github_repo_intelligence_forwards_prefer_cached_discovery(monkeypatch, tmp_path):
    captured = {}

    def fake_run_github_repo_agent(repo_spec, output_dir, **kwargs):
        captured["repo_spec"] = repo_spec
        captured["output_dir"] = output_dir
        captured.update(kwargs)
        return intelligence_module.GitHubRepoAgentReport(
            repo_spec=repo_spec,
            owner="example",
            repo="project",
            output_dir=str(output_dir),
            preset=str(kwargs.get("preset") or "mining"),
            status="pass",
            summary={},
            output_paths={},
            onboarding_report={},
        )

    monkeypatch.setattr(
        intelligence_module,
        "run_github_repo_agent",
        fake_run_github_repo_agent,
    )

    report = run_github_repo_intelligence(
        "example/project",
        tmp_path / "repo_intelligence",
        prefer_cached_discovery=True,
    )

    assert report.status == "pass"
    assert captured["repo_spec"] == "example/project"
    assert captured["prefer_cached_discovery"] is True


def test_github_repo_intelligence_lifts_hybrid_llm_patch_blocker_audit(
    monkeypatch,
):
    for env_name in (
        "CIA_LLM_API_KEY",
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
        "ALIBABA_API_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("CIA_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("CIA_LLM_MODEL", "deepseekv4PRO")

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        patch_candidates_path = output_dir / "repository_test_patch_candidates.json"
        patch_candidates_path.write_text(
            json.dumps(
                {
                    "status": "pass",
                    "reason": "patch_candidates_generated",
                    "candidate_count": 2,
                    "patch_generation_mode": "hybrid",
                    "generator_counts": {"rule": 2, "llm": 0},
                    "llm_generation_status": "blocked",
                    "llm_generation_reason": "missing_llm_api_key",
                    "llm_config_audit": {
                        "role": "patch_generation",
                        "enabled": True,
                        "provider": "deepseek",
                        "model": "deepseek-v4-pro",
                        "model_source": "CIA_LLM_MODEL",
                        "base_url": "https://api.deepseek.com/chat/completions",
                        "base_url_source": "default",
                        "api_key_env": "CIA_LLM_API_KEY",
                        "checked_api_key_envs": [
                            "CIA_LLM_API_KEY",
                            "DEEPSEEK_API_KEY",
                        ],
                        "api_key_present": False,
                        "api_key_source": "",
                        "api_key_fingerprint": "",
                        "api_key_length": 0,
                        "warnings": ["missing_api_key:CIA_LLM_API_KEY"],
                    },
                    "candidates": [],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        report = replace(
            report,
            summary={
                **report.summary,
                "repository_patch_generation_mode": "hybrid",
                "repository_patch_generator_counts": {"rule": 2, "llm": 0},
                "repository_llm_patch_generation_status": "blocked",
                "repository_llm_patch_generation_reason": "missing_llm_api_key",
                "repository_test_patch_candidates_status": "pass",
                "repository_test_patch_candidates_reason": (
                    "patch_candidates_generated"
                ),
                "repository_test_patch_candidate_count": 2,
            },
            output_paths={
                **report.output_paths,
                "repository_test_patch_candidates_json": str(
                    patch_candidates_path
                ),
            },
        )

        summary = github_repo_intelligence_summary(report)
        markdown = render_github_repo_intelligence_summary(report)

        assert summary["repository_patch_generation_mode"] == "hybrid"
        assert summary["repository_patch_generator_counts"] == {
            "rule": 2,
            "llm": 0,
        }
        assert summary["repository_llm_patch_generation_status"] == "blocked"
        assert summary["repository_llm_patch_generation_reason"] == (
            "missing_llm_api_key"
        )
        assert summary["repository_llm_patch_provider"] == "deepseek"
        assert summary["repository_llm_patch_model"] == "deepseek-v4-pro"
        assert summary["repository_llm_patch_api_key_present"] is False
        assert summary["repository_llm_patch_blocked"] is True
        assert summary["repository_llm_patch_generation_fallback_used"] is True
        assert summary["repository_llm_patch_generation_fallback_reason"] == (
            "hybrid_rule_fallback_after_llm_blocker"
        )
        assert summary["repository_llm_patch_config_audit"][
            "api_key_fingerprint"
        ] == ""
        observations = {
            item["signal"]: item["value"]
            for item in summary["agent_controller"]["observations"]
        }
        assert observations["repository_llm_patch_generation_status"] == (
            "blocked"
        )
        assert observations["repository_llm_patch_generation_reason"] == (
            "missing_llm_api_key"
        )
        assert observations["repository_llm_patch_generation_fallback"] == (
            "blocked=True, fallback_used=True, provider=deepseek, "
            "model=deepseek-v4-pro"
        )
        readiness_criteria = {
            item["name"]: item
            for item in summary["agent_goal_readiness"]["criteria"]
        }
        acceptance_checks = {
            item["name"]: item
            for item in summary["acceptance_gate"]["checks"]
        }
        assert readiness_criteria["repair_decision_audit"]["passed"] is True
        assert "llm_patch_required=true" in readiness_criteria[
            "repair_decision_audit"
        ]["evidence"]
        assert "llm_patch_blocked=true" in readiness_criteria[
            "repair_decision_audit"
        ]["evidence"]
        assert acceptance_checks["repair_decision_audit"]["passed"] is True
        repairability = summary["agent_answers"]["repairability"]
        assert repairability["llm_patch_blocked"] is True
        assert repairability["llm_patch_fallback_used"] is True
        assert "hybrid mode continued with rule-based candidates" in (
            repairability["answer"]
        )
        assert "LLM Patch Config: provider=`deepseek`, model=`deepseek-v4-pro`" in (
            markdown
        )
        assert "LLM Patch Fallback: blocked=true, fallback_used=true" in markdown


def test_github_repo_intelligence_lifts_llm_reflection_blocker_audit(
    monkeypatch,
):
    for env_name in (
        "CIA_LLM_API_KEY",
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
        "ALIBABA_API_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        patch_validation_payload = {
            "status": "fail",
            "reason": "no_candidate_passed_repository_tests",
            "reflection_enabled": False,
            "reflection_mode": "llm",
            "reflection_refiner_status": "unavailable",
            "reflection_refiner_reason": "missing_api_key:CIA_LLM_API_KEY",
            "reflection_rounds": 1,
            "reflection_width": 2,
            "reflection_candidate_count": 0,
            "successful_reflection_candidate_count": 0,
            "max_depth_executed": 0,
            "repair_ready": False,
            "repair_validation_scope": "none",
            "llm_reflection_config_audit": {
                "role": "patch_generation",
                "enabled": True,
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "base_url": "https://api.deepseek.com/chat/completions",
                "api_key_env": "CIA_LLM_API_KEY",
                "checked_api_key_envs": [
                    "CIA_LLM_API_KEY",
                    "DEEPSEEK_API_KEY",
                ],
                "api_key_present": False,
                "api_key_source": "",
                "api_key_fingerprint": "",
                "warnings": ["missing_api_key:CIA_LLM_API_KEY"],
            },
            "results": [
                {
                    "candidate_id": "average_mean.py::mean::rule::0",
                    "rule_id": "missing_len_zero_guard",
                    "variant": "return_default_on_empty",
                    "depth": 0,
                    "success": False,
                    "failure_type": "test_failure",
                    "failure_reason": "assertion_failed",
                    "passed": 0,
                    "failed": 1,
                }
            ],
        }
        reflection_trace = build_repository_test_reflection_trace(
            patch_validation_payload
        )
        patch_validation_path = output_dir / "repository_test_patch_validation.json"
        reflection_trace_path = output_dir / "repository_test_reflection_trace.json"
        patch_validation_path.write_text(
            json.dumps(patch_validation_payload, indent=2),
            encoding="utf-8",
        )
        reflection_trace_path.write_text(
            json.dumps(reflection_trace, indent=2),
            encoding="utf-8",
        )
        report = replace(
            report,
            summary={
                **report.summary,
                "repository_test_patch_validation_status": "fail",
                "repository_test_patch_validation_reason": (
                    "no_candidate_passed_repository_tests"
                ),
                "repository_test_patch_validation_reflection_mode": "llm",
                "repository_test_patch_validation_refiner_status": (
                    "unavailable"
                ),
                "repository_test_patch_validation_refiner_reason": (
                    "missing_api_key:CIA_LLM_API_KEY"
                ),
                "repository_test_patch_validation_reflection_candidate_count": 0,
                "repository_test_repair_ready": False,
            },
            output_paths={
                **report.output_paths,
                "repository_test_patch_validation_json": str(
                    patch_validation_path
                ),
                "repository_test_reflection_trace_json": str(
                    reflection_trace_path
                ),
                "reflection_trace_json": str(reflection_trace_path),
            },
        )

        summary = github_repo_intelligence_summary(report)
        markdown = render_github_repo_intelligence_summary(report)

        assert summary["repository_llm_reflection_provider"] == "deepseek"
        assert summary["repository_llm_reflection_model"] == "deepseek-v4-pro"
        assert summary["repository_llm_reflection_api_key_present"] is False
        assert summary["repository_llm_reflection_blocked"] is True
        assert summary["repository_llm_reflection_blocker"] == (
            "missing_api_key:CIA_LLM_API_KEY"
        )
        observations = {
            item["signal"]: item["value"]
            for item in summary["agent_controller"]["observations"]
        }
        assert observations["repository_llm_reflection_status"] == "unavailable"
        assert observations["repository_llm_reflection_reason"] == (
            "missing_api_key:CIA_LLM_API_KEY"
        )
        assert observations["repository_llm_reflection_blocker"] == (
            "blocked=True, blocker=missing_api_key:CIA_LLM_API_KEY, "
            "provider=deepseek, model=deepseek-v4-pro"
        )
        readiness_criteria = {
            item["name"]: item
            for item in summary["agent_goal_readiness"]["criteria"]
        }
        acceptance_checks = {
            item["name"]: item
            for item in summary["acceptance_gate"]["checks"]
        }
        assert readiness_criteria["repair_decision_audit"]["passed"] is True
        assert "llm_reflection_required=true" in readiness_criteria[
            "repair_decision_audit"
        ]["evidence"]
        assert "llm_reflection_blocked=true" in readiness_criteria[
            "repair_decision_audit"
        ]["evidence"]
        assert acceptance_checks["repair_decision_audit"]["passed"] is True
        repairability = summary["agent_answers"]["repairability"]
        assert repairability["llm_reflection_blocked"] is True
        assert "LLM reflection (deepseek/deepseek-v4-pro) is blocked" in (
            repairability["answer"]
        )
        assert "LLM Reflection Config: provider=`deepseek`" in markdown
        assert "blocked=true" in markdown


def test_github_repo_intelligence_lifts_patch_judge_audit(tmp_path):
    report = intelligence_module.GitHubRepoAgentReport(
        repo_spec="example/project",
        owner="example",
        repo="project",
        output_dir=str(tmp_path),
        preset="mining",
        status="pass",
        summary={
            "repository_test_patch_validation_status": "pass",
            "repository_test_patch_validation_reason": "patch_validation_success",
            "repository_test_patch_validation_success_count": 1,
            "repository_test_repair_ready": True,
            "repository_test_patch_judge_mode": "llm",
            "repository_test_patch_judge_status": "ready",
            "repository_test_patch_judge_reason": "llm_patch_judge_ready",
            "repository_test_patch_judge_enabled": True,
            "repository_test_patch_judge_candidate_count": 2,
            "repository_test_patch_judge_verdict_counts": {
                "prefer": 1,
                "reject": 1,
            },
            "repository_test_patch_judge_agreement_counts": {
                "aligned": 1,
                "judge_more_optimistic": 1,
            },
            "repository_test_patch_judge_authority": (
                "sandbox_pytest_decides_success"
            ),
            "repository_test_patch_judge_config_audit": {
                "role": "judge",
                "enabled": True,
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "api_key_env": "CIA_JUDGE_API_KEY",
                "checked_api_key_envs": [
                    "CIA_JUDGE_API_KEY",
                    "DEEPSEEK_API_KEY",
                ],
                "api_key_present": True,
                "api_key_source": "DEEPSEEK_API_KEY",
                "api_key_fingerprint": "sha256:abcd1234",
                "api_key_length": 32,
                "warnings": [],
            },
        },
        output_paths={},
        onboarding_report={},
    )

    summary = github_repo_intelligence_summary(report)
    markdown = render_github_repo_intelligence_summary(report)

    assert summary["repository_test_patch_judge_mode"] == "llm"
    assert summary["repository_test_patch_judge_status"] == "ready"
    assert summary["repository_test_patch_judge_enabled"] is True
    assert summary["repository_test_patch_judge_candidate_count"] == 2
    assert summary["repository_test_patch_judge_authority"] == (
        "sandbox_pytest_decides_success"
    )
    assert summary["repository_test_patch_judge_config_audit"]["provider"] == (
        "deepseek"
    )
    assert summary["repository_test_patch_judge_config_audit"]["model"] == (
        "deepseek-v4-pro"
    )
    observations = {
        item["signal"]: item["value"]
        for item in summary["agent_controller"]["observations"]
    }
    assert observations["repository_test_patch_judge_status"] == "ready"
    assert observations["repository_test_patch_judge_reason"] == (
        "llm_patch_judge_ready"
    )
    assert observations["repository_test_patch_judge_authority"] == (
        "mode=llm, judged=2, authority=sandbox_pytest_decides_success"
    )
    assert "Patch Judge: `llm`/`ready` judged=2" in markdown
    assert "sandbox_pytest_decides_success" in markdown


def test_agent_goal_readiness_requires_reflection_action_or_diagnosis_on_patch_failure():
    payload = _agent_goal_readiness_reflection_payload(
        controller_action="run_search_and_ablation_evaluation",
        reflection_summary={"available": True},
    )

    readiness = intelligence_module._agent_goal_readiness_summary(payload)
    criteria = {item["name"]: item for item in readiness["criteria"]}

    assert criteria["reflection_loop_when_patch_fails"]["passed"] is False
    evidence = criteria["reflection_loop_when_patch_fails"]["evidence"]
    assert "patch_failed=true" in evidence
    assert "trace=true" in evidence
    assert "action=run_search_and_ablation_evaluation" in evidence


def test_agent_goal_readiness_accepts_planned_reflection_action_on_patch_failure():
    payload = _agent_goal_readiness_reflection_payload(
        controller_action="run_patch_reflection_loop",
        reflection_summary={"available": True},
    )

    readiness = intelligence_module._agent_goal_readiness_summary(payload)
    criteria = {item["name"]: item for item in readiness["criteria"]}

    assert criteria["reflection_loop_when_patch_fails"]["passed"] is True
    assert "action=run_patch_reflection_loop" in criteria[
        "reflection_loop_when_patch_fails"
    ]["evidence"]


def test_agent_goal_readiness_accepts_reflection_failure_diagnosis_on_patch_failure():
    payload = _agent_goal_readiness_reflection_payload(
        controller_action="run_search_and_ablation_evaluation",
        reflection_summary={
            "available": True,
            "initial_failure_type_counts": {"test_failure": 1},
            "recommended_reflection_strategies": [
                {"id": "refine_logic_against_failing_assertion"}
            ],
        },
    )

    readiness = intelligence_module._agent_goal_readiness_summary(payload)
    criteria = {item["name"]: item for item in readiness["criteria"]}

    assert criteria["reflection_loop_when_patch_fails"]["passed"] is True
    evidence = criteria["reflection_loop_when_patch_fails"]["evidence"]
    assert "initial_failure_types=test_failure=1" in evidence
    assert "strategies=1" in evidence


def test_github_repo_intelligence_rerun_uses_cached_discovery_end_to_end():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        source_cache_dir = root / "source_cache"
        first_opener = _FakeOpener(_repo_payloads(raw_source))

        first_report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            source_cache_dir=source_cache_dir,
            opener=first_opener,
        )

        assert first_report.status == "pass"
        assert (output_dir / "discovery.json").is_file()

        second_opener = _FakeOpener([])
        second_report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            source_cache_dir=source_cache_dir,
            prefer_cached_discovery=True,
            opener=second_opener,
        )
        summary = github_repo_intelligence_summary(second_report)
        paths = write_github_repo_intelligence_artifacts(second_report, summary)
        saved = json.loads(
            Path(paths["github_repo_intelligence_json"]).read_text(encoding="utf-8")
        )
        markdown = Path(paths["github_repo_intelligence_markdown"]).read_text(
            encoding="utf-8"
        )

        assert second_opener.urls == []
        assert summary["discovery_source"] == (
            "cached-discovery-preferred:example/project@main"
        )
        assert summary["discovery_cache_reuse"] is True
        assert summary["discovery_cache_preferred"] is True
        assert summary["discovery_cache_fallback"] is False
        assert summary["discovery_cache_reuse_reason"] == "prefer_cached_discovery"
        assert summary["static_intelligence_status"] == "analysis_ready"
        assert summary["fault_localization"]["mode"] == "static_fallback"
        assert summary["agent_controller"]["selected_action"]["id"] == (
            "run_repository_tests_with_checkout"
        )
        assert saved["acceptance_gate"]["status"] == "pass"
        assert saved["agent_goal_readiness"]["status"] == "pass"
        assert saved["agent_goal_readiness"]["passed"] is True
        assert saved["agent_goal_readiness"]["failed_criteria"] == []
        assert saved["agent_goal_readiness"]["passed_criteria_count"] == (
            saved["agent_goal_readiness"]["criteria_count"]
        )
        saved_goal_criteria = {
            item["name"]: item
            for item in saved["agent_goal_readiness"]["criteria"]
        }
        assert saved_goal_criteria["source_cache_and_filter_controls"][
            "passed"
        ] is True
        assert "cache_reuse=true" in saved_goal_criteria[
            "source_cache_and_filter_controls"
        ]["evidence"]
        assert "prefer_cached=true" in saved_goal_criteria[
            "source_cache_and_filter_controls"
        ]["evidence"]
        assert saved_goal_criteria["fault_score_decomposition"]["passed"] is True
        score_evidence = saved_goal_criteria["fault_score_decomposition"]["evidence"]
        assert "static_rule_score" in score_evidence
        assert "graph_score" in score_evidence
        assert "sbfl_score" in score_evidence
        assert "dynamic_test_evidence_score" in score_evidence
        assert "final_score" in score_evidence
        assert saved["agent_answers"]["answer_coverage_complete"] is True
        assert "Discovery Cache Reuse: true" in markdown
        assert "Acceptance Gate: `pass`" in markdown
        assert "Agent Goal Readiness: `pass`" in markdown
        assert "## Agent Goal Readiness" in markdown


def test_onboarding_checkout_preserves_discovery_cache_metadata():
    checkout_payload = {
        "owner": "example",
        "repo": "project",
        "ref": "main",
        "discovery": {
            "mode": "repository_checkout",
            "checkout_path": "repo",
        },
    }
    original_payload = {
        "owner": "example",
        "repo": "project",
        "ref": "main",
        "discovery": {
            "cache_reuse": True,
            "cache_reuse_reason": "prefer_cached_discovery",
            "cache_reuse_source": "out/discovery.json",
            "cache_preferred": True,
            "cache_preferred_source": "out/discovery.json",
        },
    }

    onboarding_module._preserve_checkout_ref_provenance(
        checkout_payload,
        original_payload,
    )

    metadata = checkout_payload["discovery"]
    assert metadata["owner"] == "example"
    assert metadata["repo"] == "project"
    assert metadata["ref"] == "main"
    assert metadata["cache_reuse"] is True
    assert metadata["cache_reuse_reason"] == "prefer_cached_discovery"
    assert metadata["cache_reuse_source"] == "out/discovery.json"
    assert metadata["cache_preferred"] is True
    assert metadata["cache_preferred_source"] == "out/discovery.json"


def test_github_repo_intelligence_uses_ref_inferred_from_tree_url():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
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

        report = run_github_repo_intelligence(
            "https://github.com/example/project/tree/develop",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        summary = github_repo_intelligence_summary(report)
        markdown = render_github_repo_intelligence_summary(report)

        assert report.status == "pass"
        assert opener.urls == [
            "https://api.github.com/repos/example/project/git/trees/develop?recursive=1"
        ]
        assert report.onboarding_report["discovery_metadata"]["ref"] == "develop"
        assert summary["repo"] == "example/project"
        assert summary["repository_ref"] == "develop"
        assert summary["requested_ref"] == "develop"
        assert summary["ref_source"] == "explicit"
        assert summary["repo_input"]["kind"] == "github_url"
        assert summary["repo_input"]["normalized_repo"] == "example/project"
        assert summary["repo_input"]["url_inferred_ref"] == "develop"
        assert summary["repo_input"]["ref_selection_source"] == "url_path_ref"
        assert summary["repo_input"]["ref_fallback_used"] is False
        assert summary["repo_input"]["ref_fallback_attempt_count"] == 0
        assert summary["repo_input"]["ref_fallback_attempts"] == []
        assert summary["source_cache_dir"]
        assert summary["selected_source_count"] == 1
        goal_criteria = {
            item["name"]: item for item in summary["agent_goal_readiness"]["criteria"]
        }
        assert goal_criteria["github_ref_provenance"]["passed"] is True
        assert "ref_source=explicit" in goal_criteria[
            "github_ref_provenance"
        ]["evidence"]
        assert "input_kind=github_url" in goal_criteria[
            "github_ref_provenance"
        ]["evidence"]
        assert "ref_selection=url_path_ref" in goal_criteria[
            "github_ref_provenance"
        ]["evidence"]
        assert summary["agent_controller"]["control_loop"] == [
            "observe",
            "plan",
            "act",
            "verify",
            "reflect",
            "replan",
        ]
        observations = {
            item["signal"]: item["value"]
            for item in summary["agent_controller"]["observations"]
        }
        assert observations["repository_ref"] == "develop"
        assert observations["requested_ref"] == "develop"
        assert observations["ref_source"] == "explicit"
        assert observations["repo_input_kind"] == "github_url"
        assert observations["repo_input_ref_selection_source"] == "url_path_ref"
        assert observations["repo_input_url_inferred_ref"] == "develop"
        assert "Input Kind: `github_url`" in markdown
        assert "Ref Selection Source: `url_path_ref`" in markdown
        assert "URL Inferred Ref: `develop`" in markdown
        assert "Ref Fallback Used: false" in markdown
        assert "Ref Fallback Attempts: 0" in markdown


def test_github_repo_intelligence_auto_controller_runs_checkout_action(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = root / "sample.py"
        raw_source.write_text(
            "def shift_left(values):\n"
            "    shifted = []\n"
            "    for i in range(len(values)):\n"
            "        shifted.append(values[i + 1])\n"
            "    return shifted\n",
            encoding="utf-8",
        )
        output_dir = root / "repo_intelligence"
        discovery_payloads = [
            {"default_branch": "main"},
            {
                "sha": "abc123",
                "tree": [
                    {"path": "pyproject.toml", "type": "blob"},
                    {
                        "path": "sample.py",
                        "type": "blob",
                        "raw_url": str(raw_source),
                        "sha256": hashlib.sha256(
                            raw_source.read_bytes()
                        ).hexdigest(),
                        "license": "MIT",
                    },
                ],
            },
        ]
        opener = _FakeOpener(discovery_payloads + discovery_payloads)

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
                "message": "Fake checkout created for controller auto-action test.",
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
                if candidate.metadata.get("variant")
                == "overly_conservative_range_bound"
            ]
            return conservative or candidates

        monkeypatch.setattr(
            PatchGenerator,
            "generate",
            conservative_only_generate,
        )

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["possible_index_overrun"],
            max_sources=5,
            max_candidates=5,
            auto_fallback=False,
            auto_controller_actions=True,
            auto_controller_max_actions=2,
            opener=opener,
        )
        summary = github_repo_intelligence_summary(report)
        markdown = render_github_repo_intelligence_summary(report)

        assert opener.urls == [
            "https://api.github.com/repos/example/project",
            "https://api.github.com/repos/example/project/git/trees/main?recursive=1",
            "https://api.github.com/repos/example/project",
            "https://api.github.com/repos/example/project/git/trees/main?recursive=1",
        ]
        assert report.summary["agent_auto_enabled"] is True
        assert report.summary["agent_auto_action_count"] == 1
        assert report.summary["agent_auto_actions"][0]["action_id"] == (
            "run_repository_tests_with_checkout"
        )
        assert report.summary["repository_ref"] == "main"
        assert report.summary["requested_ref"] == ""
        assert report.summary["ref_source"] == "default_branch"
        assert report.onboarding_report["discovery_metadata"]["ref"] == "main"
        assert report.onboarding_report["discovery_metadata"]["requested_ref"] is None
        assert report.onboarding_report["discovery_metadata"]["ref_source"] == (
            "default_branch"
        )
        assert Path(
            report.summary["agent_auto_actions"][0]["pre_action_controller_json"]
        ).exists()
        assert report.summary["repository_checkout_status"] == "pass"
        assert report.summary["repository_checkout_method"] == "fake"
        assert report.summary["planned_repository_test_result_executed"] is True
        assert report.summary["planned_repository_test_result_status"] == "fail"
        assert report.summary["repository_test_dynamic_evidence_level"] == (
            "failing_tests"
        )
        assert report.summary["repository_test_fault_localization_status"] == "pass"
        assert report.summary["repository_test_fault_localization_top_function"] == (
            "shift_left"
        )
        assert report.summary["repository_test_patch_candidates_status"] == "pass"
        assert report.summary["repository_test_patch_validation_status"] == "pass"
        assert report.summary["repository_test_patch_validation_success_count"] >= 1
        assert (
            report.summary[
                "repository_test_patch_validation_reflection_candidate_count"
            ]
            >= 1
        )
        assert (
            report.summary[
                "repository_test_patch_validation_successful_reflection_count"
            ]
            >= 1
        )
        assert report.summary["repository_test_repair_ready"] is True
        assert summary["agent_auto_action_count"] == 1
        assert summary["agent_auto_max_actions"] == 2
        assert summary["repository_ref"] == "main"
        assert summary["requested_ref"] == ""
        assert summary["ref_source"] == "default_branch"
        goal_criteria = {
            item["name"]: item for item in summary["agent_goal_readiness"]["criteria"]
        }
        assert goal_criteria["github_ref_provenance"]["passed"] is True
        assert "ref_source=default_branch" in goal_criteria[
            "github_ref_provenance"
        ]["evidence"]
        assert goal_criteria["shallow_checkout_policy"]["passed"] is True
        assert "auto_checkout_actions=1" in goal_criteria[
            "shallow_checkout_policy"
        ]["evidence"]
        assert goal_criteria["fault_score_decomposition"]["passed"] is True
        score_evidence = goal_criteria["fault_score_decomposition"]["evidence"]
        assert "sbfl_score" in score_evidence
        assert "dynamic_test_evidence_score" in score_evidence
        assert summary["agent_auto_stop_reason"] == (
            "phase_goal_reached:patch_validation_ready"
        )
        assert summary["agent_auto_stop_state"]["category"] == "phase_goal_reached"
        assert summary["agent_auto_stop_state"]["action_id"] == (
            "run_search_and_ablation_evaluation"
        )
        assert len(summary["agent_auto_trace"]) == 2
        assert summary["agent_auto_trace"][0]["auto_executed"] is True
        assert summary["agent_auto_trace"][0]["plan_selected_action"] == (
            "run_repository_tests_with_checkout"
        )
        assert summary["agent_auto_trace"][0][
            "observe_agent_goal_readiness_status"
        ] == "pass"
        assert summary["agent_auto_trace"][0]["verify_dynamic_evidence_level"] == (
            "failing_tests"
        )
        assert summary["agent_auto_trace"][0][
            "verify_agent_goal_readiness_status"
        ] == "pass"
        assert summary["agent_auto_trace"][0][
            "verify_agent_goal_readiness_failed_criteria"
        ] == []
        assert summary["agent_auto_trace"][0]["verify_outcome"] == (
            "phase_goal_reached:patch_validation_ready"
        )
        assert summary["agent_auto_trace"][0]["verify_progress"] is True
        assert summary["agent_auto_trace"][0]["reflect_status"] == (
            "verified_progress"
        )
        assert summary["agent_auto_trace"][0]["replan_policy"] == (
            "stop_phase_goal_reached"
        )
        assert summary["agent_auto_trace"][1]["auto_executed"] is False
        assert summary["agent_auto_trace"][1]["stop_reason"] == (
            "phase_goal_reached:patch_validation_ready"
        )
        assert summary["agent_auto_trace"][1]["stop_category"] == (
            "phase_goal_reached"
        )
        assert summary["agent_auto_loop_audit"]["progress_count"] == 1
        assert summary["agent_auto_loop_audit"]["no_progress_count"] == 0
        assert summary["agent_auto_loop_audit"]["complete_loop_recorded"] is True
        assert summary["agent_auto_loop_audit"]["verify_outcome_counts"] == {
            "phase_goal_reached:patch_validation_ready": 1
        }
        assert summary["agent_auto_loop_audit"]["replan_policy_counts"] == {
            "stop_phase_goal_reached": 1
        }
        assert summary["agent_auto_loop_audit"][
            "goal_readiness_status_counts"
        ] == {"pass": 1}
        assert summary["agent_auto_loop_audit"][
            "goal_readiness_passed_action_count"
        ] == 1
        assert summary["agent_auto_loop_audit"][
            "final_goal_readiness_status"
        ] == "pass"
        timeline = summary["agent_decision_timeline"]
        assert timeline["status"] == "pass"
        assert timeline["source"] == "agent_auto_trace"
        assert timeline["step_count"] == 2
        assert timeline["complete_step_count"] == 2
        assert timeline["executed_step_count"] == 1
        assert timeline["steps"][0]["plan"]["selected_action"] == (
            "run_repository_tests_with_checkout"
        )
        assert timeline["steps"][0]["act"]["status"] == "executed"
        assert timeline["steps"][1]["act"]["status"] == "stopped"
        assert timeline["steps"][1]["act"]["stop_category"] == (
            "phase_goal_reached"
        )
        controller = summary["agent_controller"]
        assert controller["auto_controller"]["enabled"] is True
        assert controller["auto_controller"]["action_count"] == 1
        assert controller["auto_controller"]["trace_count"] == 2
        assert controller["auto_controller"]["progress_count"] == 1
        assert controller["auto_controller"]["complete_loop_recorded"] is True
        assert controller["auto_controller"]["verify_outcome_counts"] == {
            "phase_goal_reached:patch_validation_ready": 1
        }
        assert controller["auto_controller"]["goal_readiness_status_counts"] == {
            "pass": 1
        }
        assert controller["auto_controller"][
            "goal_readiness_passed_action_count"
        ] == 1
        assert controller["auto_controller"]["final_goal_readiness_status"] == "pass"
        assert controller["auto_controller"]["stop_reason"] == (
            "phase_goal_reached:patch_validation_ready"
        )
        assert controller["auto_controller"]["stop_category"] == (
            "phase_goal_reached"
        )
        assert controller["auto_controller"]["stop_action_id"] == (
            "run_search_and_ablation_evaluation"
        )
        loop_audit = controller["loop_iteration_audit"]
        assert loop_audit["status"] == "pass"
        assert loop_audit["source"] == "agent_auto_trace"
        assert loop_audit["iteration_count"] == 2
        assert loop_audit["complete_iteration_count"] == 2
        assert loop_audit["executed_iteration_count"] == 1
        assert loop_audit["stopped_iteration_count"] == 1
        assert loop_audit["iterations"][0]["action_id"] == (
            "run_repository_tests_with_checkout"
        )
        assert loop_audit["iterations"][0]["act_status"] == "executed"
        assert "outcome=phase_goal_reached:patch_validation_ready" in (
            loop_audit["iterations"][0]["verify"]
        )
        assert loop_audit["iterations"][1]["act_status"] == "stopped"
        assert "stop=phase_goal_reached" in loop_audit["iterations"][1]["act"]
        assert controller["auto_trace"][0]["plan_selected_action"] == (
            "run_repository_tests_with_checkout"
        )
        assert controller["auto_trace"][1]["auto_executed"] is False
        assert controller["auto_actions"][0]["after_reflection_trace_reason"] == (
            "reflection_repaired_candidate"
        )
        assert summary["agent_auto_actions"][0]["before_blocker"] == (
            "no_static_candidates"
        )
        auto_action = summary["agent_auto_actions"][0]
        assert auto_action["before_agent_goal_readiness_status"] == "pass"
        assert auto_action["after_agent_goal_readiness_status"] == "pass"
        assert auto_action["loop_verify_agent_goal_readiness_status"] == "pass"
        assert auto_action["loop_verify_agent_goal_readiness_failed_criteria"] == []
        assert auto_action["loop_verify_outcome"] == (
            "phase_goal_reached:patch_validation_ready"
        )
        assert auto_action["loop_verify_progress"] is True
        assert auto_action["loop_reflect_status"] == "verified_progress"
        assert auto_action["loop_replan_policy"] == "stop_phase_goal_reached"
        assert auto_action["loop_replan_next_action"] == (
            "run_search_and_ablation_evaluation"
        )
        assert auto_action["after_dynamic_evidence_level"] == "failing_tests"
        assert auto_action["after_fault_localization_mode"] == "dynamic"
        assert auto_action["after_fault_localization_status"] == "pass"
        assert auto_action["after_patch_candidates_status"] == "pass"
        assert auto_action["after_patch_validation_status"] == "pass"
        assert auto_action["after_patch_validation_success_count"] >= 1
        assert auto_action["after_repair_ready"] is True
        assert auto_action["after_reflection_candidate_count"] >= 1
        assert auto_action["after_successful_reflection_count"] >= 1
        assert auto_action["after_reflection_trace_reason"] == (
            "reflection_repaired_candidate"
        )
        assert auto_action["after_reflection_repair_ready"] is True
        reflection = summary["reflection_summary"]
        assert reflection["available"] is True
        assert reflection["reason"] == "reflection_repaired_candidate"
        assert reflection["reflection_candidate_count"] >= 1
        assert reflection["successful_reflection_candidate_count"] >= 1
        assert reflection["repair_ready"] is True
        assert reflection["best_depth"] == 1
        assert reflection["failure_type_counts"] == {
            "success": 1,
            "test_failure": 1,
        }
        assert reflection["initial_failure_type_counts"] == {"test_failure": 1}
        assert reflection["reflection_parent_failure_type_counts"] == {
            "test_failure": 1
        }
        assert reflection["successful_reflection_parent_failure_type_counts"] == {
            "test_failure": 1
        }
        assert reflection["reflection_failure_type_counts"] == {"success": 1}
        answers = summary["agent_answers"]
        assert answers["top_suspicious_functions"][0]["function"] == "shift_left"
        assert answers["top_suspicious_functions"][0]["mode"] == "dynamic"
        assert "DynamicEvidenceScore=1.0000" in (
            answers["top_suspicious_functions"][0]["why"]
        )
        assert answers["testability"]["status"] == "tests_failed"
        assert answers["testability"]["dynamic_evidence_level"] == "failing_tests"
        assert answers["repairability"]["status"] == "repair_ready"
        assert answers["repairability"]["repair_ready"] is True
        assert answers["blocker"] == "none"
        assert answers["answer_coverage_complete"] is True
        assert answers["answer_coverage_answered_count"] == 7
        assert "Repairability: repair_ready" in answers["executive_summary"]
        observations = {
            item["signal"]: item["value"]
            for item in summary["agent_controller"]["observations"]
        }
        assert observations["agent_auto_enabled"] == "True"
        assert observations["agent_auto_action_count"] == "1"
        assert "Controller Auto Actions" in markdown
        assert "Controller Auto Trace" in markdown
        assert "Agent Decision Timeline: `pass` (2/2 complete)" in markdown
        assert "Agent Loop Progress" in markdown
        assert "Agent Loop Goal Readiness" in markdown
        assert "phase_goal_reached:patch_validation_ready=1" in markdown
        assert "stop_phase_goal_reached=1" in markdown
        assert "phase_goal_reached:patch_validation_ready" in markdown
        assert "Reflection Summary" in markdown
        assert "reflection_repaired_candidate" in markdown
        assert "Initial Failure Types: test_failure=1" in markdown
        assert "Successful Reflection Parent Failure Types: test_failure=1" in markdown
        assert "failing_tests" in markdown
        assert "pass(" in markdown
        write_github_repo_intelligence_artifacts(report, summary)
        controller_json = json.loads(
            (output_dir / "github_repo_agent_controller.json").read_text(
                encoding="utf-8"
            )
        )
        controller_markdown = (
            output_dir / "github_repo_agent_controller.md"
        ).read_text(encoding="utf-8")
        assert controller_json["auto_controller"]["enabled"] is True
        assert controller_json["auto_controller"]["trace_count"] == 2
        assert controller_json["auto_controller"]["progress_count"] == 1
        assert controller_json["auto_controller"]["complete_loop_recorded"] is True
        assert controller_json["auto_trace"][0]["auto_executed"] is True
        assert controller_json["auto_trace"][0]["verify_outcome"] == (
            "phase_goal_reached:patch_validation_ready"
        )
        assert controller_json["auto_trace"][0]["replan_policy"] == (
            "stop_phase_goal_reached"
        )
        assert controller_json["auto_trace"][1]["auto_executed"] is False
        assert controller_json["loop_iteration_audit"]["iteration_count"] == 2
        assert controller_json["loop_iteration_audit"]["executed_iteration_count"] == 1
        timeline_json = json.loads(
            (output_dir / "agent_decision_timeline.json").read_text(
                encoding="utf-8"
            )
        )
        timeline_markdown = (
            output_dir / "agent_decision_timeline.md"
        ).read_text(encoding="utf-8")
        assert timeline_json["status"] == "pass"
        assert timeline_json["source"] == "agent_auto_trace"
        assert timeline_json["steps"][0]["act"]["status"] == "executed"
        assert "Agent Decision Timeline" in timeline_markdown
        assert "run_repository_tests_with_checkout" in timeline_markdown
        assert "## Auto Controller" in controller_markdown
        assert "### Auto Trace" in controller_markdown
        assert "## Loop Iteration Audit" in controller_markdown
        assert "Progressed Actions: 1" in controller_markdown
        assert "Verify Outcomes: phase_goal_reached:patch_validation_ready=1" in (
            controller_markdown
        )
        assert "Goal Readiness Statuses: pass=1" in controller_markdown
        assert "Final Goal Readiness: `pass`" in controller_markdown
        assert "run_repository_tests_with_checkout" in controller_markdown


def test_github_repo_intelligence_auto_controller_relaxes_source_filters():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source) + _repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            include=["does_not_exist.py"],
            exclude=["maths/average_mean.py"],
            target_prefix="wrongpkg",
            recipes=["missing_len_zero_guard"],
            max_sources=1,
            max_candidates=1,
            auto_fallback=False,
            auto_controller_actions=True,
            auto_controller_max_actions=1,
            opener=opener,
        )
        summary = github_repo_intelligence_summary(report)
        markdown = render_github_repo_intelligence_summary(report)

        assert report.summary["agent_auto_enabled"] is True
        assert report.summary["agent_auto_action_count"] == 1
        assert report.summary["agent_auto_actions"][0]["action_id"] == (
            "adjust_source_filters"
        )
        assert report.summary["agent_auto_actions"][0]["before_stage"] == (
            "source_import_blocked"
        )
        assert report.summary["agent_auto_actions"][0]["rerun_include"] == []
        assert report.summary["agent_auto_actions"][0]["rerun_exclude"] == []
        assert report.summary["agent_auto_actions"][0]["rerun_target_prefix"] == ""
        assert report.summary["agent_auto_actions"][0]["rerun_max_sources"] == (
            intelligence_module.DEFAULT_MAX_SOURCES
        )
        assert report.summary["agent_auto_actions"][0]["rerun_max_candidates"] == (
            intelligence_module.DEFAULT_MAX_CANDIDATES
        )
        assert report.summary["agent_auto_actions"][0]["after_stage"] == (
            "phase2_static_graph_fault_localization"
        )
        assert report.summary["agent_auto_trace"][0]["plan_selected_action"] == (
            "adjust_source_filters"
        )
        assert report.summary["agent_auto_trace"][0]["auto_executed"] is True
        assert report.summary["agent_auto_trace"][1]["auto_executed"] is False
        assert report.summary["agent_auto_stop_reason"] == "max_actions_reached"
        assert summary["static_intelligence_status"] == "analysis_ready"
        assert summary["selected_signal_count"] == 1
        assert summary["repository_structure"]["analyzed_file_count"] == 2
        assert summary["fault_localization"]["mode"] == "static_fallback"
        assert summary["agent_controller"]["selected_action"]["id"] == (
            "run_repository_tests_with_checkout"
        )
        assert "adjust_source_filters" in markdown
        assert "max_actions_reached" in markdown


def test_github_repo_intelligence_auto_controller_records_passing_tests_as_guard(
    monkeypatch,
):
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        discovery_payloads = _repo_payloads(raw_source)
        opener = _FakeOpener(discovery_payloads + discovery_payloads)

        def fake_checkout_github_repository(**kwargs):
            checkout_root = Path(kwargs["output_dir"]) / "repository_checkout"
            (checkout_root / "tests").mkdir(parents=True, exist_ok=True)
            (checkout_root / "average_mean.py").write_text(
                raw_source.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (checkout_root / "helpers.py").write_text(
                (raw_source.parent / "helpers.py").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (checkout_root / "tests" / "test_average_mean.py").write_text(
                "from average_mean import mean\n\n"
                "def test_mean_regular_values():\n"
                "    assert mean([1, 2, 3]) == 2\n",
                encoding="utf-8",
            )
            return {
                "status": "pass",
                "reason": "fake_checkout_created",
                "message": "Fake checkout created for passing-test guard.",
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

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            auto_fallback=False,
            auto_controller_actions=True,
            auto_controller_max_actions=3,
            opener=opener,
        )
        summary = github_repo_intelligence_summary(report)
        markdown = render_github_repo_intelligence_summary(report)

        assert report.summary["planned_repository_test_result_status"] == "pass"
        assert report.summary["repository_test_dynamic_evidence_level"] == (
            "passing_tests"
        )
        assert report.summary["repository_test_regression_guard_status"] == "pass"
        assert report.summary["repository_test_regression_guard_reason"] == (
            "repository_tests_passed_registered_as_regression_guard"
        )
        guard_json = Path(report.output_paths["repository_test_regression_guard_json"])
        guard_markdown = Path(
            report.output_paths["repository_test_regression_guard_markdown"]
        )
        assert guard_json.exists()
        assert guard_markdown.exists()
        guard = json.loads(guard_json.read_text(encoding="utf-8"))
        assert guard["status"] == "pass"
        assert guard["guard_role"] == "regression_validation_only"
        assert guard["usable_for_localization"] is False
        assert guard["usable_for_patch_validation"] is True
        assert guard["repair_claim_allowed"] is False
        assert summary["repository_test_regression_guard"]["status"] == "pass"
        assert summary["agent_auto_action_count"] == 2
        assert [item["action_id"] for item in summary["agent_auto_actions"]] == [
            "run_repository_tests_with_checkout",
            "convert_passing_tests_to_regression_guard",
        ]
        assert summary["agent_auto_actions"][1][
            "after_regression_guard_status"
        ] == "pass"
        assert len(summary["agent_auto_trace"]) == 3
        assert summary["agent_auto_trace"][0]["auto_executed"] is True
        assert summary["agent_auto_trace"][1]["auto_executed"] is True
        assert summary["agent_auto_trace"][1]["plan_selected_action"] == (
            "convert_passing_tests_to_regression_guard"
        )
        assert summary["agent_auto_trace"][1]["verify_regression_guard_status"] == (
            "pass"
        )
        assert summary["agent_auto_trace"][2]["auto_executed"] is False
        assert summary["agent_auto_trace"][2]["plan_selected_action"] == (
            "extend_failure_overlay_or_provide_bug_report"
        )
        assert summary["agent_auto_stop_reason"] == "selected_action_not_executable"
        assert summary["agent_auto_stop_state"]["category"] == "manual_or_blocked"
        assert summary["agent_auto_stop_state"]["action_id"] == (
            "extend_failure_overlay_or_provide_bug_report"
        )
        assert summary["agent_auto_stop_state"]["recovery_policy"] == (
            "provide_failing_test_bug_report_or_overlay_rule"
        )
        assert summary["agent_auto_stop_state"]["external_input_kind"] == (
            "failing_test_or_bug_report"
        )
        assert summary["agent_auto_stop_state"]["requires_user_action"] is True
        assert summary["agent_auto_stop_state"][
            "requires_environment_change"
        ] is False
        assert "Provide a failing test" in summary["agent_auto_stop_state"][
            "recommended_next_action"
        ]
        assert summary["agent_auto_trace"][2]["stop_category"] == (
            "manual_or_blocked"
        )
        assert summary["agent_auto_trace"][2]["stop_recovery_policy"] == (
            "provide_failing_test_bug_report_or_overlay_rule"
        )
        assert summary["agent_auto_trace"][2]["stop_external_input_kind"] == (
            "failing_test_or_bug_report"
        )
        assert summary["agent_auto_trace"][2]["stop_requires_user_action"] is True
        controller = summary["agent_controller"]
        assert controller["status"] == "blocked"
        assert controller["selected_action"]["id"] == (
            "extend_failure_overlay_or_provide_bug_report"
        )
        assert controller["auto_controller"]["stop_recovery_policy"] == (
            "provide_failing_test_bug_report_or_overlay_rule"
        )
        assert controller["auto_controller"]["stop_external_input_kind"] == (
            "failing_test_or_bug_report"
        )
        assert controller["auto_controller"]["stop_requires_user_action"] is True
        observations = {
            item["signal"]: item["value"]
            for item in controller["observations"]
        }
        assert observations["repository_test_regression_guard_status"] == "pass"
        assert observations["repository_test_failure_overlay_status"] == "skipped"
        assert observations["repository_test_failure_overlay_reason"] == (
            "no_supported_overlay_candidates"
        )
        assert summary["agent_answers"]["testability"]["status"] == (
            "overlay_not_usable"
        )
        assert summary["agent_answers"]["testability"][
            "failure_overlay_reason"
        ] == "no_supported_overlay_candidates"
        goal_criteria = {
            item["name"]: item
            for item in summary["agent_goal_readiness"]["criteria"]
        }
        overlay_goal = goal_criteria["failure_overlay_route_audited"]
        assert overlay_goal["passed"] is True
        assert "attempted=true" in overlay_goal["evidence"]
        assert "status=skipped" in overlay_goal["evidence"]
        assert "testability=overlay_not_usable" in overlay_goal["evidence"]
        assert "controlled failure overlay" in (
            summary["agent_answers"]["testability_answer"]
        )
        assert "no_supported_overlay_candidates" in (
            summary["agent_answers"]["testability_answer"]
        )
        overlay_json = Path(report.output_paths["repository_test_failure_overlay_json"])
        overlay_markdown = Path(
            report.output_paths["repository_test_failure_overlay_markdown"]
        )
        assert overlay_json.exists()
        assert overlay_markdown.exists()
        test_artifacts = {
            item["name"]: item
            for item in summary["artifact_inventory"]["groups"]["test"]
        }
        assert test_artifacts["repository_test_failure_overlay.json"][
            "required_now"
        ] is True
        assert test_artifacts["repository_test_failure_overlay.json"][
            "available"
        ] is True
        assert test_artifacts["repository_test_failure_overlay.md"][
            "required_now"
        ] is True
        assert test_artifacts["repository_test_failure_overlay.md"][
            "available"
        ] is True
        assert "Regression Guard" in markdown
        assert "Failure Overlay" in markdown
        assert "skipped(no_supported_overlay_candidates)" in markdown
        assert "repository_tests_passed_registered_as_regression_guard" in markdown
        assert "extend_failure_overlay_or_provide_bug_report" in markdown
        assert "Agent Auto Recommended Next Action" in markdown
        assert "provide_failing_test_bug_report_or_overlay_rule" in markdown

        write_github_repo_intelligence_artifacts(report, summary)
        controller_markdown = (
            output_dir / "github_repo_agent_controller.md"
        ).read_text(encoding="utf-8")
        assert "Regression Guard" in controller_markdown
        assert "Failure Overlay" in controller_markdown
        assert "skipped(no_supported_overlay_candidates)" in controller_markdown
        assert "Stop Category: `manual_or_blocked`" in controller_markdown
        assert (
            "Stop Recovery Policy: "
            "`provide_failing_test_bug_report_or_overlay_rule`"
        ) in controller_markdown
        assert "External Input Kind: `failing_test_or_bug_report`" in (
            controller_markdown
        )
        assert "Requires User Action: true" in controller_markdown
        assert "convert_passing_tests_to_regression_guard" in controller_markdown
        assert "extend_failure_overlay_or_provide_bug_report" in controller_markdown


def test_github_repo_intelligence_auto_controller_reruns_static_mining_for_topk_gap(
    monkeypatch,
):
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir) / "repo_intelligence"
        output_dir.mkdir(parents=True, exist_ok=True)
        initial_report = intelligence_module.GitHubRepoAgentReport(
            repo_spec="example/project",
            owner="example",
            repo="project",
            output_dir=str(output_dir),
            preset="smoke",
            status="pass",
            summary={"marker": "initial"},
            output_paths={},
            onboarding_report={},
        )
        captured = {}

        def make_summary(marker):
            if marker == "after":
                summary = {
                    "repo": "example/project",
                    "repo_spec": "example/project",
                    "output_dir": str(output_dir),
                    "analysis_readiness": {
                        "current_stage": "phase2_static_graph_fault_localization",
                        "next_stage": "phase3_repository_test_execution",
                        "blocker": "dynamic_tests_not_executed",
                        "dynamic_evidence_level": "not_executed",
                    },
                    "fault_localization": {
                        "mode": "static_fallback",
                        "status": "pass",
                        "top_function": "target",
                        "rankings": [
                            {
                                "rank": 1,
                                "function": "target",
                                "score": 0.9,
                            }
                        ],
                    },
                    "agent_goal_readiness": {
                        "status": "pass",
                        "failed_criteria_count": 0,
                        "failed_criteria": [],
                    },
                }
            else:
                summary = {
                    "repo": "example/project",
                    "repo_spec": "example/project",
                    "output_dir": str(output_dir),
                    "analysis_readiness": {
                        "current_stage": "phase2_static_bug_signal_mining",
                        "next_stage": "phase2_static_graph_fault_localization",
                        "blocker": "",
                        "dynamic_evidence_level": "not_executed",
                    },
                    "fault_localization": {
                        "mode": "none",
                        "status": "warning",
                        "rankings": [],
                    },
                    "agent_goal_readiness": {
                        "status": "warning",
                        "failed_criteria_count": 1,
                        "failed_criteria": [
                            "topk_fault_localization_or_actionable_blocker"
                        ],
                    },
                }
            summary["agent_controller"] = intelligence_module.build_agent_controller_plan(
                summary
            )
            return summary

        def fake_summary(report):
            return make_summary(report.summary.get("marker"))

        def fake_run_github_repo_agent(repo_spec, output_dir_arg, **kwargs):
            captured.update(kwargs)
            return intelligence_module.GitHubRepoAgentReport(
                repo_spec=repo_spec,
                owner="example",
                repo="project",
                output_dir=str(output_dir_arg),
                preset=str(kwargs.get("preset") or ""),
                status="pass",
                summary={"marker": "after"},
                output_paths={},
                onboarding_report={},
            )

        monkeypatch.setattr(
            intelligence_module,
            "github_repo_intelligence_summary",
            fake_summary,
        )
        monkeypatch.setattr(
            intelligence_module,
            "run_github_repo_agent",
            fake_run_github_repo_agent,
        )
        monkeypatch.setattr(
            intelligence_module,
            "_write_auto_controller_snapshot",
            lambda summary, output_root, suffix: {
                "pre_action_intelligence_json": str(output_dir / f"{suffix}.json"),
                "pre_action_intelligence_markdown": str(output_dir / f"{suffix}.md"),
                "pre_action_controller_json": str(
                    output_dir / f"{suffix}_controller.json"
                ),
                "pre_action_controller_markdown": str(
                    output_dir / f"{suffix}_controller.md"
                ),
            },
        )

        report = intelligence_module._run_auto_controller_actions(
            initial_report,
            repo_spec="example/project",
            output_dir=output_dir,
            agent_kwargs={
                "preset": "smoke",
                "include": ["src/**/*.py"],
                "exclude": ["src/pkg/buggy.py"],
                "target_prefix": "src/pkg",
                "max_sources": 3,
                "max_candidates": 2,
            },
            max_actions=1,
        )

        assert captured["preset"] == "mining"
        assert captured["include"] is None
        assert captured["exclude"] is None
        assert captured["target_prefix"] == ""
        assert captured["max_sources"] == intelligence_module.DEFAULT_MAX_SOURCES
        assert captured["max_candidates"] == (
            intelligence_module.DEFAULT_MAX_CANDIDATES
        )
        assert report.summary["agent_auto_action_count"] == 1
        assert report.summary["agent_auto_actions"][0]["action_id"] == (
            "build_static_graph_fault_ranking"
        )
        assert report.summary["agent_auto_actions"][0][
            "loop_verify_outcome"
        ] == "agent_goal_readiness_passed"
        assert report.summary["agent_auto_actions"][0][
            "loop_verify_agent_goal_readiness_status"
        ] == "pass"
        assert report.summary["agent_auto_trace"][0]["auto_executed"] is True
        assert report.summary["agent_auto_trace"][0][
            "observe_agent_goal_readiness_failed_criteria"
        ] == ["topk_fault_localization_or_actionable_blocker"]
        assert report.summary["agent_auto_trace"][0][
            "verify_agent_goal_readiness_status"
        ] == "pass"
        assert report.summary["agent_auto_loop_audit"][
            "goal_readiness_status_counts"
        ] == {"pass": 1}
        assert report.summary["agent_auto_stop_state"]["category"] == (
            "budget_exhausted"
        )
        assert report.summary["agent_auto_trace"][1]["stop_category"] == (
            "budget_exhausted"
        )


def test_github_repo_intelligence_auto_controller_reruns_application_source_focus():
    current_kwargs = {
        "preset": "smoke",
        "include": ["src/**/*.py"],
        "exclude": ["tests/**"],
        "target_prefix": "src/pkg",
        "max_sources": 3,
        "max_candidates": 2,
    }

    rerun = intelligence_module._auto_action_rerun_kwargs(
        "adjust_application_source_focus",
        current_kwargs,
    )

    assert rerun is not None
    assert rerun["preset"] == "mining"
    assert rerun["include"] is None
    assert rerun["exclude"] is None
    assert rerun["target_prefix"] == ""
    assert rerun["max_sources"] == intelligence_module.DEFAULT_MAX_SOURCES
    assert rerun["max_candidates"] == intelligence_module.DEFAULT_MAX_CANDIDATES

    broad_kwargs = {
        "preset": "mining",
        "include": None,
        "exclude": None,
        "target_prefix": "",
        "max_sources": intelligence_module.DEFAULT_MAX_SOURCES,
        "max_candidates": intelligence_module.DEFAULT_MAX_CANDIDATES,
    }
    stop_reason = intelligence_module._auto_stop_reason(
        "adjust_application_source_focus",
        {
            "id": "adjust_application_source_focus",
            "executable_now": True,
        },
        broad_kwargs,
    )

    assert stop_reason == "application_source_focus_already_broad"
    assert intelligence_module._auto_stop_category(stop_reason) == (
        "no_additional_auto_action"
    )


def test_github_repo_intelligence_auto_controller_records_environment_repair_plan():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        blocked_summary = dict(report.summary)
        blocked_summary.update(
            {
                "repository_test_dynamic_evidence_level": "not_executed",
                "repository_test_dynamic_usable_for_localization": False,
                "repository_test_setup_doctor_status": "blocked",
                "repository_test_setup_doctor_blocker": (
                    "environment:test_tool_missing"
                ),
                "repository_test_setup_doctor_next_action": (
                    "Install test runner nox before executing repository tests."
                ),
                "planned_repository_test_command": "python -m nox",
                "planned_repository_test_executable_now": False,
                "planned_repository_test_result_status": "",
                "repository_test_environment_status": "warning",
                "repository_test_environment_reason": "test_tool_missing",
                "repository_test_environment_setup_status": "warning",
                "repository_test_environment_setup_reason": (
                    "install_command_supported"
                ),
                "repository_test_environment_setup_supported": True,
                "repository_test_environment_setup_result_status": "skipped",
                "repository_test_environment_setup_result_reason": (
                    "setup_execution_disabled"
                ),
                "recommended_install_command": "python -m pip install nox",
                "repository_test_tool_available": False,
                "repository_test_ci_install_command_candidates": [
                    "python -m pip install -e .",
                    "python -m pip install nox",
                ],
                "planned_repository_test_environment_variable_names": [
                    "PYTHONPATH"
                ],
            }
        )
        report = replace(report, summary=blocked_summary)

        report = intelligence_module._run_auto_controller_actions(
            report,
            repo_spec="example/project",
            output_dir=output_dir,
            agent_kwargs={
                "repository_test_timeout": 20,
                "checkout_repository_tests": True,
            },
            max_actions=2,
        )
        summary = github_repo_intelligence_summary(report)
        markdown = render_github_repo_intelligence_summary(report)

        assert report.summary["agent_auto_action_count"] == 1
        assert report.summary["agent_auto_stop_reason"] == (
            "selected_action_not_executable"
        )
        assert report.summary["agent_auto_stop_state"]["category"] == (
            "manual_or_blocked"
        )
        assert report.summary["agent_auto_stop_state"]["recovery_policy"] == (
            "apply_environment_repair_then_rerun_agent"
        )
        assert report.summary["agent_auto_stop_state"]["external_input_kind"] == (
            "environment"
        )
        assert report.summary["agent_auto_stop_state"][
            "requires_environment_change"
        ] is True
        assert report.summary["agent_auto_stop_state"][
            "requires_user_action"
        ] is True
        assert "Apply repository_test_environment_repair_plan" in (
            report.summary["agent_auto_stop_state"]["recommended_next_action"]
        )
        assert report.summary["repository_test_environment_repair_plan_status"] == (
            "pass"
        )
        assert report.summary[
            "repository_test_environment_repair_plan_recommended_install_command"
        ] == "python -m pip install nox"
        assert report.summary["agent_auto_actions"][0]["action_id"] == (
            "prepare_repository_test_environment"
        )
        assert report.summary["agent_auto_actions"][0][
            "after_environment_repair_plan_status"
        ] == "pass"
        assert report.summary["agent_auto_trace"][0]["auto_executed"] is True
        assert report.summary["agent_auto_trace"][0][
            "verify_environment_repair_plan_status"
        ] == "pass"
        assert report.summary["agent_auto_trace"][1]["auto_executed"] is False
        assert report.summary["agent_auto_trace"][1]["plan_selected_action"] == (
            "await_environment_repair"
        )
        assert report.summary["agent_auto_trace"][1]["stop_recovery_policy"] == (
            "apply_environment_repair_then_rerun_agent"
        )
        assert report.summary["agent_auto_trace"][1]["stop_external_input_kind"] == (
            "environment"
        )
        assert report.summary["agent_auto_trace"][1][
            "stop_requires_environment_change"
        ] is True

        plan_json = Path(
            report.output_paths["repository_test_environment_repair_plan_json"]
        )
        plan_markdown = Path(
            report.output_paths[
                "repository_test_environment_repair_plan_markdown"
            ]
        )
        assert plan_json.exists()
        assert plan_markdown.exists()
        plan = json.loads(plan_json.read_text(encoding="utf-8"))
        assert plan["status"] == "pass"
        assert plan["blocker"] == "environment:test_tool_missing"
        assert plan["recommended_install_command"] == (
            "python -m pip install nox"
        )
        assert plan["recommended_test_command"] == "python -m nox"
        assert plan["test_tool_available"] is False
        assert plan["auto_installed_dependencies"] is False
        assert any(
            "python -m pip install nox" in action
            for action in plan["next_actions"]
        )

        assert summary["repository_test_environment_repair_plan"]["status"] == (
            "pass"
        )
        assert summary["repository_test_environment_repair_plan"][
            "recommended_install_command"
        ] == "python -m pip install nox"
        assert summary["agent_controller"]["status"] == "blocked"
        assert summary["agent_controller"]["selected_action"]["id"] == (
            "await_environment_repair"
        )
        observations = {
            item["signal"]: item["value"]
            for item in summary["agent_controller"]["observations"]
        }
        assert observations["repository_test_environment_repair_plan_status"] == (
            "pass"
        )
        assert "Environment Repair Plan" in markdown
        assert "python -m pip install nox" in markdown
        assert "await_environment_repair" in markdown

        write_github_repo_intelligence_artifacts(report, summary)
        controller_markdown = (
            output_dir / "github_repo_agent_controller.md"
        ).read_text(encoding="utf-8")
        assert "Environment Repair" in controller_markdown
        assert (
            "Stop Recovery Policy: "
            "`apply_environment_repair_then_rerun_agent`"
        ) in controller_markdown
        assert "External Input Kind: `environment`" in controller_markdown
        assert "Requires Environment Change: true" in controller_markdown
        assert "prepare_repository_test_environment" in controller_markdown
        assert "await_environment_repair" in controller_markdown


def test_environment_repair_plan_extracts_missing_dependency_module():
    summary = {
        "analysis_readiness": {
            "blocker": "dynamic_evidence_not_usable:missing_dependency",
            "repository_test_setup_doctor_blocker": (
                "execution_failure:missing_dependency"
            ),
            "planned_repository_test_command": "python -m pytest -q tests",
        },
        "planned_repository_test_failure_category": "missing_dependency",
        "planned_repository_test_failure_signal": "missing_module:requests",
        "repository_test_environment_status": "warning",
        "repository_test_environment_reason": "planned_test_failed",
        "repository_test_tool_available": True,
    }
    controller = {
        "current_stage": "phase2_static_graph_fault_localization",
        "primary_blocker": "dynamic_evidence_not_usable:missing_dependency",
    }
    selected_action = {
        "id": "prepare_repository_test_environment",
        "reason": "Repository test execution exposed a missing dependency.",
    }

    plan = intelligence_module._build_repository_test_environment_repair_plan(
        summary,
        controller=controller,
        selected_action=selected_action,
        snapshot_paths={},
    )
    markdown = intelligence_module._render_repository_test_environment_repair_plan_markdown(
        plan
    )

    assert plan["blocker"] == "execution_failure:missing_dependency"
    assert plan["planned_failure_category"] == "missing_dependency"
    assert plan["planned_failure_signal"] == "missing_module:requests"
    assert plan["missing_dependency_modules"] == ["requests"]
    assert plan["missing_dependency_install_hint"] == (
        "python -m pip install requests"
    )
    assert any("requests" in action for action in plan["next_actions"])
    assert "Missing Dependency Modules: `requests`" in markdown
    assert "python -m pip install requests" in markdown


def test_write_artifacts_materializes_required_environment_repair_plan():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        summary = github_repo_intelligence_summary(report)

        def write_artifact_pair(stem: str) -> tuple[str, str]:
            json_path = output_dir / f"{stem}.json"
            markdown_path = output_dir / f"{stem}.md"
            json_path.write_text("{}", encoding="utf-8")
            markdown_path.write_text(f"# {stem}\n", encoding="utf-8")
            return str(json_path), str(markdown_path)

        for stem in [
            "repository_test_environment",
            "repository_test_execution_plan",
            "repository_test_execution_result",
            "repository_test_dynamic_evidence",
        ]:
            json_path, markdown_path = write_artifact_pair(stem)
            summary[f"{stem}_json"] = json_path
            summary[f"{stem}_markdown"] = markdown_path

        summary.update(
            {
                "repository_test_dynamic_evidence_level": "collection_failure",
                "repository_test_setup_doctor_status": "blocked",
                "repository_test_setup_doctor_blocker": (
                    "environment:test_tool_missing"
                ),
                "repository_test_setup_doctor_next_action": (
                    "Install or prepare `nox` before executing repository tests."
                ),
                "planned_repository_test_command": "python -m nox",
                "planned_repository_test_result_status": "fail",
                "repository_test_environment_status": "warning",
                "repository_test_environment_reason": "test_tool_missing",
                "repository_test_environment_setup_status": "warning",
                "repository_test_environment_setup_reason": (
                    "install_command_supported"
                ),
                "repository_test_environment_setup_supported": True,
                "repository_test_environment_setup_result_status": "skipped",
                "repository_test_environment_setup_result_reason": (
                    "execution_disabled"
                ),
                "recommended_install_command": "python -m pip install nox",
                "repository_test_tool_available": False,
                "repository_test_environment_repair_plan_json": "",
                "repository_test_environment_repair_plan_markdown": "",
            }
        )
        summary["analysis_readiness"].update(
            {
                "blocker": "dynamic_evidence_not_usable:collection_failure",
                "dynamic_evidence_level": "collection_failure",
                "repository_test_setup_doctor_status": "blocked",
                "repository_test_setup_doctor_blocker": (
                    "environment:test_tool_missing"
                ),
                "repository_test_setup_doctor_next_action": (
                    "Install or prepare `nox` before executing repository tests."
                ),
                "planned_repository_test_command": "python -m nox",
                "planned_repository_test_result_status": "fail",
            }
        )

        paths = write_github_repo_intelligence_artifacts(report, summary)

        plan_json = Path(paths["repository_test_environment_repair_plan_json"])
        plan_markdown = Path(
            paths["repository_test_environment_repair_plan_markdown"]
        )
        saved = json.loads(
            (output_dir / "github_repo_intelligence.json").read_text(
                encoding="utf-8"
            )
        )
        plan = json.loads(plan_json.read_text(encoding="utf-8"))

        assert plan_json.exists()
        assert plan_markdown.exists()
        assert plan["status"] == "pass"
        assert plan["blocker"] == "environment:test_tool_missing"
        assert plan["recommended_install_command"] == "python -m pip install nox"
        assert saved["repository_test_environment_repair_plan"]["status"] == "pass"
        assert saved["repository_test_environment_repair_plan_json"].endswith(
            "repository_test_environment_repair_plan.json"
        )
        assert saved["artifact_inventory"]["status"] == "pass"
        assert saved["artifact_inventory"]["missing_required_artifacts"] == []
        assert saved["agent_answers"]["testability"]["status"] == (
            "tests_failed_without_localizable_evidence"
        )
        assert "did not produce usable failing-test evidence" in (
            saved["agent_answers"]["testability_answer"]
        )
        test_rows = {
            item["name"]: item
            for item in saved["artifact_inventory"]["groups"]["test"]
        }
        assert test_rows["repository_test_environment_repair_plan.json"][
            "required_now"
        ] is True
        assert test_rows["repository_test_environment_repair_plan.json"][
            "available"
        ] is True


def test_agent_answers_explain_failure_overlay_dynamic_testability():
    answers = intelligence_module._agent_answers_summary(
        {
            "repository_structure": {
                "analyzed_file_count": 1,
                "function_count": 1,
                "class_count": 0,
                "total_loc": 12,
                "max_cyclomatic_complexity": 2,
            },
            "fault_localization": {
                "mode": "dynamic",
                "status": "pass",
                "top_function": "gronsfeld",
                "rankings": [
                    {
                        "rank": 1,
                        "function_name": "gronsfeld",
                        "file_path": "ciphers/gronsfeld_cipher.py",
                        "final_score": 0.8,
                        "static_rule_score": 0.9,
                        "graph_score": 0.3,
                    }
                ],
            },
            "analysis_readiness": {
                "blocker": "",
                "planned_repository_test_command": "python -m pytest project_euler",
                "planned_repository_test_result_status": "fail",
                "dynamic_evidence_level": "collection_failure",
                "patch_validation_status": "pass",
                "patch_validation_reason": "patch_validation_success",
                "repair_ready": True,
                "repair_validation_scope": "narrow_only",
            },
            "repository_test_failure_overlay_status": "pass",
            "repository_test_failure_overlay_dynamic_evidence_level": "failing_tests",
            "repository_test_failure_overlay_selected_rule": (
                "missing_len_zero_guard"
            ),
            "repository_test_failure_overlay_selected_function": "gronsfeld",
            "repository_test_failure_overlay_validation_command": (
                "python -m pytest -q tests/test_cia_overlay.py::test_gronsfeld"
            ),
            "reflection_summary": {
                "repair_ready": True,
            },
            "artifact_inventory": {
                "status": "pass",
                "missing_core_artifacts": [],
                "missing_required_artifacts": [],
            },
            "agent_controller": {
                "primary_blocker": "",
                "selected_action": {
                    "id": "run_search_and_ablation_evaluation",
                    "reason": "Patch validation is ready.",
                },
            },
        }
    )

    testability = answers["testability"]
    assert testability["status"] == "overlay_failing_tests_available"
    assert testability["failure_overlay_dynamic_evidence_level"] == "failing_tests"
    assert testability["failure_overlay_selected_function"] == "gronsfeld"
    assert "controlled failure overlay produced usable failing_tests" in (
        answers["testability_answer"]
    )
    assert "collection_failure" in answers["testability_answer"]
    assert "missing_len_zero_guard" in answers["testability_answer"]
    assert "overlay_failing_tests_available" in answers["executive_summary"]


def test_agent_answers_explain_unusable_failure_overlay_blocker():
    answers = intelligence_module._agent_answers_summary(
        {
            "repository_structure": {
                "analyzed_file_count": 1,
                "function_count": 1,
                "class_count": 0,
                "total_loc": 12,
                "max_cyclomatic_complexity": 2,
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "target",
                "rankings": [
                    {
                        "rank": 1,
                        "function_name": "target",
                        "file_path": "sample.py",
                        "final_score": 0.8,
                        "static_rule_score": 0.9,
                        "graph_score": 0.3,
                    }
                ],
            },
            "analysis_readiness": {
                "blocker": "dynamic_evidence_not_usable:collection_failure",
                "planned_repository_test_command": "python -m pytest",
                "planned_repository_test_result_status": "fail",
                "dynamic_evidence_level": "collection_failure",
            },
            "repository_test_failure_overlay_status": "skipped",
            "repository_test_failure_overlay_reason": "no_supported_overlay_candidates",
            "repository_test_failure_overlay_supported_candidates": 0,
            "repository_test_failure_overlay_attempted_cases": 0,
            "repository_test_failure_overlay_candidate_limit": 5,
            "repository_test_failure_overlay_next_actionable_extension": {
                "recommendation": (
                    "Add a deterministic overlay builder for the dominant static rule."
                )
            },
            "reflection_summary": {},
            "artifact_inventory": {
                "status": "pass",
                "missing_core_artifacts": [],
                "missing_required_artifacts": [],
            },
            "agent_controller": {
                "primary_blocker": "dynamic_evidence_not_usable:collection_failure",
                "selected_action": {
                    "id": "extend_failure_overlay_or_provide_bug_report",
                    "reason": "Failure overlay did not produce evidence.",
                },
            },
        }
    )

    testability = answers["testability"]
    assert testability["status"] == "overlay_not_usable"
    assert testability["failure_overlay_reason"] == (
        "no_supported_overlay_candidates"
    )
    assert testability["failure_overlay_supported_candidates"] == 0
    assert testability["failure_overlay_attempted_cases"] == 0
    assert testability["failure_overlay_candidate_limit"] == 5
    assert "controlled failure overlay" in answers["testability_answer"]
    assert "no_supported_overlay_candidates" in answers["testability_answer"]
    assert "supported=0, attempted=0, limit=5" in answers["testability_answer"]
    assert "Add a deterministic overlay builder" in answers["testability_answer"]
    assert "overlay_not_usable" in answers["executive_summary"]


def test_agent_answers_explain_runner_fallback_testability():
    answers = intelligence_module._agent_answers_summary(
        {
            "repository_structure": {
                "analyzed_file_count": 1,
                "function_count": 1,
                "class_count": 0,
                "total_loc": 10,
                "max_cyclomatic_complexity": 1,
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "pass",
                "top_function": "target",
                "rankings": [
                    {
                        "rank": 1,
                        "function_name": "target",
                        "file_path": "pkg/core.py",
                        "final_score": 0.5,
                        "static_rule_score": 0.7,
                    }
                ],
            },
            "analysis_readiness": {
                "blocker": "dynamic_tests_not_executed",
                "planned_repository_test_command": (
                    "python -m pytest -q tests/test_core.py"
                ),
                "planned_repository_test_runner": "pytest",
                "planned_repository_test_preferred_runner": "tox",
                "planned_repository_test_runner_fallback_used": True,
                "planned_repository_test_runner_fallback_reason": (
                    "missing_runner:tox"
                ),
                "planned_repository_test_runner_fallback_from": "tox",
                "planned_repository_test_runner_fallback_to": "pytest",
                "planned_repository_test_executable_now": True,
                "planned_repository_test_result_status": "",
                "dynamic_evidence_level": "not_executed",
                "patch_validation_status": "skipped",
                "repair_ready": False,
                "repair_validation_scope": "none",
            },
            "reflection_summary": {},
            "artifact_inventory": {
                "status": "pass",
                "missing_core_artifacts": [],
                "missing_required_artifacts": [],
            },
            "agent_controller": {
                "primary_blocker": "dynamic_tests_not_executed",
                "selected_action": {
                    "id": "run_repository_tests_with_checkout",
                    "reason": "Execution planning selected a safe fallback runner.",
                },
            },
        }
    )

    testability = answers["testability"]
    assert testability["status"] == "can_execute_now"
    assert testability["runner_fallback_used"] is True
    assert testability["runner_fallback_reason"] == "missing_runner:tox"
    assert testability["runner_fallback_from"] == "tox"
    assert testability["runner_fallback_to"] == "pytest"
    assert "Tests can be executed now" in answers["testability_answer"]
    assert "The Agent selected a runner fallback `tox` -> `pytest`" in (
        answers["testability_answer"]
    )
    assert "missing_runner:tox" in answers["testability_answer"]


def test_agent_answers_explain_safety_gate_blocked_repairability():
    answers = intelligence_module._agent_answers_summary(
        {
            "repository_structure": {
                "analyzed_file_count": 1,
                "function_count": 1,
                "class_count": 0,
                "total_loc": 10,
                "max_cyclomatic_complexity": 1,
            },
            "fault_localization": {
                "mode": "dynamic",
                "status": "pass",
                "top_function": "target",
                "rankings": [
                    {
                        "rank": 1,
                        "function_name": "target",
                        "file_path": "pkg/core.py",
                        "final_score": 0.9,
                        "dynamic_evidence_score": 1.0,
                    }
                ],
            },
            "analysis_readiness": {
                "current_stage": "phase3_patch_validation",
                "next_stage": "phase3_patch_reflection_or_expansion",
                "blocker": "patch_candidates_blocked_by_safety_gate",
                "dynamic_evidence_level": "assertion_failure",
                "patch_validation_status": "skipped",
                "patch_validation_reason": "all_candidates_blocked_by_safety_gate",
                "patch_validation_input_candidate_count": 1,
                "patch_validation_candidate_count": 0,
                "patch_validation_safety_blocked_candidate_count": 1,
                "repair_ready": False,
                "repair_validation_scope": "none",
                "can_attempt_patch_repair": True,
            },
            "reflection_summary": {},
            "artifact_inventory": {
                "status": "pass",
                "missing_core_artifacts": [],
                "missing_required_artifacts": [],
            },
            "agent_controller": {
                "primary_blocker": "patch_candidates_blocked_by_safety_gate",
                "selected_action": {
                    "id": "regenerate_safe_patch_candidates",
                    "reason": "Regenerate safe patch candidates.",
                },
            },
        }
    )

    repairability = answers["repairability"]
    assert repairability["status"] == "patch_candidates_blocked_by_safety_gate"
    assert repairability["can_repair"] is True
    assert repairability["patch_validation_safety_blocked_candidate_count"] == 1
    assert "pre-sandbox safety gate" in answers["repairability_answer"]
    assert "Regenerate safe patch candidates" in answers["executive_summary"]


def test_agent_answers_repairability_lifts_reflection_strategy_taxonomy():
    answers = intelligence_module._agent_answers_summary(
        {
            "repository_structure": {
                "analyzed_file_count": 1,
                "function_count": 1,
                "class_count": 0,
                "total_loc": 12,
                "max_cyclomatic_complexity": 2,
            },
            "fault_localization": {
                "mode": "dynamic",
                "status": "pass",
                "top_function": "target",
                "rankings": [
                    {
                        "rank": 1,
                        "function_name": "target",
                        "file_path": "pkg/core.py",
                        "final_score": 0.91,
                        "dynamic_evidence_score": 1.0,
                    }
                ],
            },
            "analysis_readiness": {
                "current_stage": "phase3_patch_validation",
                "next_stage": "phase3_patch_reflection_or_expansion",
                "blocker": "no_candidate_passed_repository_tests",
                "dynamic_evidence_level": "assertion_failure",
                "patch_validation_status": "fail",
                "patch_validation_reason": "no_candidate_passed_repository_tests",
                "patch_validation_input_candidate_count": 1,
                "patch_validation_candidate_count": 1,
                "patch_validation_safety_blocked_candidate_count": 0,
                "repair_ready": False,
                "repair_validation_scope": "repository_tests",
                "can_attempt_patch_repair": True,
            },
            "reflection_summary": {
                "available": True,
                "reflection_candidate_count": 2,
                "successful_reflection_candidate_count": 0,
                "initial_failure_type_counts": {"test_failure": 1},
                "reflection_failure_type_counts": {"test_failure": 2},
                "reflection_parent_failure_type_counts": {"test_failure": 2},
                "successful_reflection_parent_failure_type_counts": {},
                "initial_strategy_counts": {
                    "compare_assertion_and_preserve_contract": 1,
                },
                "recommended_reflection_strategies": [
                    {
                        "id": "compare_assertion_and_preserve_contract",
                        "failure_types": ["test_failure"],
                        "action": (
                            "Compare the failed assertion and preserve already "
                            "passing behavior."
                        ),
                        "reason": "test failure remains after depth-0 patch",
                    }
                ],
                "primary_reflection_strategy_id": (
                    "compare_assertion_and_preserve_contract"
                ),
                "primary_reflection_strategy_action": (
                    "Compare the failed assertion and preserve already passing "
                    "behavior."
                ),
                "primary_reflection_strategy_reason": (
                    "test failure remains after depth-0 patch"
                ),
            },
            "artifact_inventory": {
                "status": "pass",
                "missing_core_artifacts": [],
                "missing_required_artifacts": [],
            },
            "agent_controller": {
                "primary_blocker": "no_candidate_passed_repository_tests",
                "selected_action": {
                    "id": "expand_patch_candidates_or_reflection",
                    "reason": (
                        "Patch validation ran but no candidate produced a "
                        "verified repair."
                    ),
                },
            },
        }
    )

    repairability = answers["repairability"]
    assert repairability["status"] == "reflection_attempted_but_not_repaired"
    assert repairability["initial_failure_type_counts"] == {"test_failure": 1}
    assert repairability["reflection_failure_type_counts"] == {"test_failure": 2}
    assert repairability["reflection_parent_failure_type_counts"] == {
        "test_failure": 2,
    }
    assert repairability["initial_strategy_counts"] == {
        "compare_assertion_and_preserve_contract": 1,
    }
    assert repairability["recommended_reflection_strategy_count"] == 1
    assert repairability["primary_reflection_strategy_id"] == (
        "compare_assertion_and_preserve_contract"
    )
    assert "Patch validation failed after 2 reflection candidate(s)" in (
        answers["repairability_answer"]
    )
    assert "initial failures=test_failure=1" in answers["repairability_answer"]
    assert "reflection failures=test_failure=2" in (
        answers["repairability_answer"]
    )
    assert (
        "primary reflection strategy `compare_assertion_and_preserve_contract`"
        in answers["repairability_answer"]
    )


def test_intelligence_status_promotes_verified_repair_ready_report():
    status = intelligence_module._github_repo_intelligence_status_summary(
        {
            "status": "fail",
            "upstream_agent_status": "fail",
            "repository_test_patch_validation_status": "pass",
            "repository_test_patch_validation_reason": "patch_validation_success",
            "repository_test_repair_ready": True,
            "artifact_inventory": {
                "status": "pass",
                "missing_core_artifacts": [],
                "missing_required_artifacts": [],
            },
        }
    )

    assert status["status"] == "pass"
    assert status["passed"] is True
    assert status["status_reason"] == "patch_validation_success"
    assert status["status_source"] == "repository_test_patch_validation"
    assert status["upstream_agent_status"] == "fail"
    assert status["upstream_agent_passed"] is False


def test_intelligence_status_does_not_promote_unverified_repair_report():
    status = intelligence_module._github_repo_intelligence_status_summary(
        {
            "status": "fail",
            "upstream_agent_status": "fail",
            "repository_test_patch_validation_status": "pass",
            "repository_test_patch_validation_reason": "patch_validation_success",
            "repository_test_repair_ready": True,
            "artifact_inventory": {
                "status": "warning",
                "missing_required_artifacts": ["repository_test_patch_validation"],
            },
        }
    )

    assert status["status"] == "fail"
    assert status["passed"] is False
    assert status["status_source"] == "github_repo_agent"


def test_intelligence_status_treats_complete_source_blocker_report_as_pass():
    status = intelligence_module._github_repo_intelligence_status_summary(
        {
            "status": "fail",
            "upstream_agent_status": "fail",
            "analysis_readiness": {
                "current_stage": "source_import_blocked",
                "blocker": "source_import_or_parse_missing",
            },
            "artifact_inventory": {
                "status": "pass",
                "missing_core_artifacts": [],
                "missing_required_artifacts": [],
            },
        }
    )

    assert status["status"] == "pass"
    assert status["passed"] is True
    assert status["status_reason"] == "source_import_blocked_report_ready"
    assert status["status_source"] == "analysis_readiness"
    assert status["upstream_agent_status"] == "fail"


def test_intelligence_status_treats_no_static_candidates_report_as_pass():
    status = intelligence_module._github_repo_intelligence_status_summary(
        {
            "status": "fail",
            "upstream_agent_status": "fail",
            "analysis_readiness": {
                "current_stage": "phase1_repo_understanding",
                "blocker": "no_static_candidates",
            },
            "artifact_inventory": {
                "status": "pass",
                "missing_core_artifacts": [],
                "missing_required_artifacts": [],
            },
        }
    )

    assert status["status"] == "pass"
    assert status["passed"] is True
    assert status["status_reason"] == "no_static_candidates_report_ready"
    assert status["status_source"] == "analysis_readiness"
    assert status["upstream_agent_status"] == "fail"
    assert status["upstream_agent_passed"] is False


def test_agent_controller_expands_static_search_when_no_candidates_and_no_tests():
    controller = build_agent_controller_plan(
        {
            "repo": "example/project",
            "repo_spec": "example/project",
            "output_dir": "outputs/repo_intelligence",
            "analysis_readiness": {
                "current_stage": "phase1_repo_understanding",
                "next_stage": "phase2_static_bug_signal_mining",
                "blocker": "no_static_candidates",
                "static_signal_count": 0,
                "can_attempt_dynamic_tests": False,
                "dynamic_evidence_level": "none",
                "planned_repository_test_command": "",
                "repository_test_setup_doctor_blocker": "",
            },
            "fault_localization": {
                "mode": "static_fallback",
                "status": "skipped",
                "top_function": "",
            },
        }
    )

    selected = controller["selected_action"]
    assert selected["id"] == "expand_static_candidate_search"
    assert selected["phase"] == "phase2"
    assert selected["executable_now"] is True
    assert "--max-sources 200" in selected["command"]
    assert "--max-candidates 50" in selected["command"]
    assert controller["verification"]["expected_artifact"] == "source_mining.json"
    assert controller["reflection"]["fallback_action"] == "adjust_source_filters"
    assert controller["replan"]["trigger"] == "no_static_candidates"
    assert controller["loop_iteration_audit"]["status"] == "pass"
    assert controller["decision_trace"][0]["phase"] == "observe"
    assert controller["decision_trace"][-1]["phase"] == "replan"


def test_auto_controller_discover_tests_rerun_broadens_checkout_and_filters():
    rerun = intelligence_module._auto_action_rerun_kwargs(
        "discover_repository_tests",
        {
            "run_repository_test_command": False,
            "checkout_repository_tests": False,
            "repository_test_root": None,
            "include": ["src/pkg/core.py"],
            "exclude": ["tests/slow"],
            "target_prefix": "src/pkg",
            "max_sources": 5,
            "max_candidates": 3,
            "repository_test_timeout": (
                intelligence_module.DEFAULT_REPOSITORY_TEST_TIMEOUT
            ),
        },
    )

    assert rerun is not None
    assert rerun["run_repository_test_command"] is True
    assert rerun["checkout_repository_tests"] is True
    assert rerun["include"] is None
    assert rerun["exclude"] is None
    assert rerun["target_prefix"] == ""
    assert rerun["max_sources"] == intelligence_module.DEFAULT_MAX_SOURCES
    assert rerun["max_candidates"] == intelligence_module.DEFAULT_MAX_CANDIDATES
    assert rerun["repository_test_timeout"] == 30


def test_auto_controller_timeout_narrowing_rerun_enables_retry_execution():
    current_kwargs = {
        "run_repository_test_command": True,
        "checkout_repository_tests": True,
        "repository_test_timeout": (
            intelligence_module.DEFAULT_REPOSITORY_TEST_TIMEOUT
        ),
        "run_repository_test_retry": False,
        "run_repository_test_retry_prerequisites": False,
        "auto_repository_test_retry": False,
        "auto_repository_test_retry_max_risk": "low",
        "auto_repository_test_retry_allowed_runners": [],
    }

    rerun = intelligence_module._auto_action_rerun_kwargs(
        "narrow_repository_tests_after_timeout",
        current_kwargs,
    )

    assert rerun is not None
    assert rerun["run_repository_test_command"] is True
    assert rerun["checkout_repository_tests"] is True
    assert rerun["run_repository_test_retry"] is True
    assert rerun["run_repository_test_retry_prerequisites"] is True
    assert rerun["auto_repository_test_retry"] is True
    assert rerun["auto_repository_test_retry_max_risk"] == "medium"
    assert "pytest" in rerun["auto_repository_test_retry_allowed_runners"]
    assert rerun["repository_test_timeout"] == 30

    stop_reason = intelligence_module._auto_stop_reason(
        "narrow_repository_tests_after_timeout",
        {
            "id": "narrow_repository_tests_after_timeout",
            "executable_now": True,
        },
        rerun,
    )

    assert stop_reason == "selected_action_already_applied"


@pytest.mark.parametrize(
    ("action_id", "expected_mode"),
    [
        ("generate_llm_patch_candidates", "llm"),
        ("generate_hybrid_patch_candidates", "hybrid"),
    ],
)
def test_auto_controller_llm_patch_actions_force_target_candidate_mode(
    action_id,
    expected_mode,
):
    current_kwargs = {
        "run_repository_test_command": False,
        "checkout_repository_tests": False,
        "repository_test_timeout": (
            intelligence_module.DEFAULT_REPOSITORY_TEST_TIMEOUT
        ),
        "run_repository_test_retry_prerequisites": False,
        "auto_repository_test_retry": False,
        "auto_repository_test_retry_max_risk": "low",
        "auto_repository_test_retry_allowed_runners": [],
        "repository_patch_generation_mode": "rule",
        "repository_llm_patch_candidate_limit": 1,
        "max_candidates": 3,
    }

    rerun = intelligence_module._auto_action_rerun_kwargs(
        action_id,
        current_kwargs,
    )

    assert rerun is not None
    assert rerun["run_repository_test_command"] is True
    assert rerun["checkout_repository_tests"] is True
    assert rerun["run_repository_test_retry_prerequisites"] is True
    assert rerun["auto_repository_test_retry"] is True
    assert rerun["auto_repository_test_retry_max_risk"] == "medium"
    assert rerun["repository_patch_generation_mode"] == expected_mode
    assert rerun["repository_llm_patch_candidate_limit"] == 3
    assert rerun["max_candidates"] == intelligence_module.DEFAULT_MAX_CANDIDATES
    assert "pytest" in rerun["auto_repository_test_retry_allowed_runners"]
    assert "unittest" in rerun["auto_repository_test_retry_allowed_runners"]

    stop_reason = intelligence_module._auto_stop_reason(
        action_id,
        {
            "id": action_id,
            "executable_now": True,
        },
        rerun,
    )

    assert stop_reason == "selected_action_already_applied"


def test_auto_controller_executes_selected_llm_patch_action(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir) / "repo_intelligence"
        output_dir.mkdir(parents=True, exist_ok=True)
        initial_report = intelligence_module.GitHubRepoAgentReport(
            repo_spec="example/project",
            owner="example",
            repo="project",
            output_dir=str(output_dir),
            preset="smoke",
            status="pass",
            summary={"marker": "initial"},
            output_paths={},
            onboarding_report={},
        )
        captured = {}

        def make_summary(marker):
            summary = {
                "repo": "example/project",
                "repo_spec": "example/project",
                "output_dir": str(output_dir),
                "repository_patch_generation_mode": "llm",
                "repository_llm_patch_generation_status": "ready",
                "repository_llm_patch_generation_reason": "llm_client_ready",
                "repository_llm_patch_generation_audit": {
                    "status": "ready",
                    "reason": "llm_client_ready",
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                    "api_key_env": "CIA_LLM_API_KEY",
                    "api_key_present": True,
                },
                "fault_localization": {
                    "mode": "dynamic",
                    "status": "pass",
                    "top_function": "target",
                    "rankings": [{"rank": 1, "function": "target", "score": 0.9}],
                },
                "agent_goal_readiness": {
                    "status": "warning",
                    "failed_criteria_count": 1,
                    "failed_criteria": ["sandbox_verified_patch_repair"],
                },
            }
            if marker == "after":
                summary["analysis_readiness"] = {
                    "current_stage": "phase3_patch_validation",
                    "next_stage": "phase4_search_and_evaluation",
                    "blocker": "",
                    "dynamic_evidence_level": "failing_tests",
                    "patch_validation_status": "pass",
                    "patch_validation_reason": "patch_validation_success",
                    "repair_ready": True,
                    "can_attempt_patch_repair": True,
                }
                summary["repository_test_patch_validation_status"] = "pass"
                summary["repository_test_patch_validation_success_count"] = 1
                summary["repository_test_repair_ready"] = True
            else:
                summary["analysis_readiness"] = {
                    "current_stage": "phase2_dynamic_fault_localization",
                    "next_stage": "phase3_patch_validation",
                    "blocker": "",
                    "dynamic_evidence_level": "failing_tests",
                    "patch_validation_status": "",
                    "repair_ready": False,
                    "can_attempt_patch_repair": True,
                }
            summary["agent_controller"] = (
                intelligence_module.build_agent_controller_plan(summary)
            )
            return summary

        def fake_summary(report):
            return make_summary(report.summary.get("marker"))

        def fake_run_github_repo_agent(repo_spec, output_dir_arg, **kwargs):
            captured.update(kwargs)
            return intelligence_module.GitHubRepoAgentReport(
                repo_spec=repo_spec,
                owner="example",
                repo="project",
                output_dir=str(output_dir_arg),
                preset=str(kwargs.get("preset") or ""),
                status="pass",
                summary={"marker": "after"},
                output_paths={},
                onboarding_report={},
            )

        monkeypatch.setattr(
            intelligence_module,
            "github_repo_intelligence_summary",
            fake_summary,
        )
        monkeypatch.setattr(
            intelligence_module,
            "run_github_repo_agent",
            fake_run_github_repo_agent,
        )
        monkeypatch.setattr(
            intelligence_module,
            "_write_auto_controller_snapshot",
            lambda summary, output_root, suffix: {
                "pre_action_intelligence_json": str(output_dir / f"{suffix}.json"),
                "pre_action_intelligence_markdown": str(output_dir / f"{suffix}.md"),
                "pre_action_controller_json": str(
                    output_dir / f"{suffix}_controller.json"
                ),
                "pre_action_controller_markdown": str(
                    output_dir / f"{suffix}_controller.md"
                ),
            },
        )

        report = intelligence_module._run_auto_controller_actions(
            initial_report,
            repo_spec="example/project",
            output_dir=output_dir,
            agent_kwargs={
                "run_repository_test_command": True,
                "checkout_repository_tests": False,
                "repository_test_timeout": 20,
                "run_repository_test_retry_prerequisites": False,
                "auto_repository_test_retry": False,
                "auto_repository_test_retry_max_risk": "low",
                "auto_repository_test_retry_allowed_runners": [],
                "repository_patch_generation_mode": "rule",
                "repository_llm_patch_candidate_limit": 1,
                "max_candidates": 3,
            },
            max_actions=1,
        )

        assert captured["repository_patch_generation_mode"] == "llm"
        assert captured["repository_llm_patch_candidate_limit"] == 3
        assert captured["checkout_repository_tests"] is True
        assert captured["run_repository_test_retry_prerequisites"] is True
        assert captured["auto_repository_test_retry"] is True
        action = report.summary["agent_auto_actions"][0]
        assert action["action_id"] == "generate_llm_patch_candidates"
        assert action["repository_patch_generation_mode"] == "llm"
        assert report.summary["agent_auto_trace"][0]["auto_executed"] is True
        assert report.summary["agent_auto_trace"][0]["plan_selected_action"] == (
            "generate_llm_patch_candidates"
        )


def test_auto_controller_executes_selected_llm_reflection_action(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir) / "repo_intelligence"
        output_dir.mkdir(parents=True, exist_ok=True)
        initial_report = intelligence_module.GitHubRepoAgentReport(
            repo_spec="example/project",
            owner="example",
            repo="project",
            output_dir=str(output_dir),
            preset="smoke",
            status="pass",
            summary={"marker": "initial"},
            output_paths={},
            onboarding_report={},
        )
        captured = {}

        def make_summary(marker):
            summary = {
                "repo": "example/project",
                "repo_spec": "example/project",
                "output_dir": str(output_dir),
                "repository_test_patch_validation_reflection_mode": "llm",
                "repository_llm_reflection_status": "ready",
                "repository_llm_reflection_reason": "llm_refiner",
                "repository_llm_reflection_provider": "deepseek",
                "repository_llm_reflection_model": "deepseek-v4-pro",
                "repository_llm_reflection_api_key_env": "CIA_LLM_API_KEY",
                "repository_llm_reflection_audit": {
                    "mode": "llm",
                    "status": "ready",
                    "reason": "llm_refiner",
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                    "api_key_env": "CIA_LLM_API_KEY",
                    "api_key_present": True,
                },
                "fault_localization": {
                    "mode": "dynamic",
                    "status": "pass",
                    "top_function": "target",
                    "rankings": [{"rank": 1, "function": "target", "score": 0.9}],
                },
                "reflection_summary": {
                    "reflection_mode": "llm",
                    "reflection_refiner_status": "ready",
                    "reflection_refiner_reason": "llm_refiner",
                    "reflection_candidate_count": 0,
                    "max_depth_executed": 0,
                },
                "agent_goal_readiness": {
                    "status": "warning",
                    "failed_criteria_count": 1,
                    "failed_criteria": ["sandbox_verified_patch_repair"],
                },
            }
            if marker == "after":
                summary["analysis_readiness"] = {
                    "current_stage": "phase3_patch_validation",
                    "next_stage": "phase4_search_and_evaluation",
                    "blocker": "",
                    "dynamic_evidence_level": "failing_tests",
                    "patch_validation_status": "pass",
                    "patch_validation_reason": "patch_validation_reflection_success",
                    "repair_ready": True,
                    "can_attempt_patch_repair": True,
                }
                summary["repository_test_patch_validation_status"] = "pass"
                summary["repository_test_patch_validation_success_count"] = 1
                summary["repository_test_patch_validation_reflection_candidate_count"] = 1
                summary[
                    "repository_test_patch_validation_successful_reflection_count"
                ] = 1
                summary["repository_test_repair_ready"] = True
                summary["reflection_summary"] = {
                    **summary["reflection_summary"],
                    "reflection_candidate_count": 1,
                    "successful_reflection_candidate_count": 1,
                }
            else:
                summary["analysis_readiness"] = {
                    "current_stage": "phase3_patch_validation",
                    "next_stage": "phase3_patch_reflection_or_expansion",
                    "blocker": "no_candidate_passed_repository_tests",
                    "dynamic_evidence_level": "failing_tests",
                    "patch_validation_status": "fail",
                    "patch_validation_reason": "no_candidate_passed_repository_tests",
                    "repair_ready": False,
                    "can_attempt_patch_repair": True,
                }
                summary["repository_test_patch_validation_status"] = "fail"
                summary["repository_test_patch_validation_success_count"] = 0
            summary["agent_controller"] = (
                intelligence_module.build_agent_controller_plan(summary)
            )
            return summary

        def fake_summary(report):
            return make_summary(report.summary.get("marker"))

        def fake_run_github_repo_agent(repo_spec, output_dir_arg, **kwargs):
            captured.update(kwargs)
            return intelligence_module.GitHubRepoAgentReport(
                repo_spec=repo_spec,
                owner="example",
                repo="project",
                output_dir=str(output_dir_arg),
                preset=str(kwargs.get("preset") or ""),
                status="pass",
                summary={
                    "marker": "after",
                    "repository_test_patch_validation_reflection_mode": "llm",
                    "repository_test_patch_validation_reflection_candidate_count": 1,
                    "repository_test_patch_validation_successful_reflection_count": 1,
                },
                output_paths={},
                onboarding_report={},
            )

        monkeypatch.setattr(
            intelligence_module,
            "github_repo_intelligence_summary",
            fake_summary,
        )
        monkeypatch.setattr(
            intelligence_module,
            "run_github_repo_agent",
            fake_run_github_repo_agent,
        )
        monkeypatch.setattr(
            intelligence_module,
            "_write_auto_controller_snapshot",
            lambda summary, output_root, suffix: {
                "pre_action_intelligence_json": str(output_dir / f"{suffix}.json"),
                "pre_action_intelligence_markdown": str(output_dir / f"{suffix}.md"),
                "pre_action_controller_json": str(
                    output_dir / f"{suffix}_controller.json"
                ),
                "pre_action_controller_markdown": str(
                    output_dir / f"{suffix}_controller.md"
                ),
            },
        )

        report = intelligence_module._run_auto_controller_actions(
            initial_report,
            repo_spec="example/project",
            output_dir=output_dir,
            agent_kwargs={
                "run_repository_test_command": True,
                "checkout_repository_tests": False,
                "repository_test_timeout": 20,
                "run_repository_test_retry_prerequisites": False,
                "auto_repository_test_retry": False,
                "auto_repository_test_retry_max_risk": "low",
                "auto_repository_test_retry_allowed_runners": [],
                "repository_test_reflection_mode": "llm",
                "repository_test_reflection_rounds": 0,
                "repository_test_reflection_width": 0,
            },
            max_actions=1,
        )

        assert captured["repository_test_reflection_mode"] == "llm"
        assert captured["repository_test_reflection_rounds"] == 1
        assert captured["repository_test_reflection_width"] == 1
        assert captured["checkout_repository_tests"] is True
        assert captured["run_repository_test_retry_prerequisites"] is True
        assert captured["auto_repository_test_retry"] is True
        action = report.summary["agent_auto_actions"][0]
        assert action["action_id"] == "run_llm_patch_reflection_loop"
        assert action["repository_test_reflection_mode"] == "llm"
        assert action["after_reflection_candidate_count"] == 1
        assert report.summary["agent_auto_trace"][0]["auto_executed"] is True
        assert report.summary["agent_auto_trace"][0]["plan_selected_action"] == (
            "run_llm_patch_reflection_loop"
        )


def test_auto_transition_audit_counts_timeout_narrowing_progress():
    class _Report:
        status = "pass"
        summary = {}

    before_summary = {
        "analysis_readiness": {
            "current_stage": "phase2_static_graph_fault_localization",
            "blocker": "dynamic_evidence_not_usable:timeout",
            "dynamic_evidence_level": "timeout",
        },
        "fault_localization": {
            "mode": "static_fallback",
            "status": "pass",
            "application_candidate_count": 1,
        },
        "repository_test_timeout_narrowing_status": "",
        "agent_goal_readiness": {"status": "warning", "failed_criteria_count": 1},
    }
    after_summary = {
        **before_summary,
        "repository_test_timeout_narrowing": {
            "status": "fail",
            "reason": "timeout_narrowing_selected_non_timeout_result",
            "executed": True,
            "attempt_count": 2,
            "selected_failure_category": "test_assertion_failure",
        },
        "agent_goal_readiness": {"status": "warning", "failed_criteria_count": 1},
    }

    audit = intelligence_module._auto_transition_audit(
        action_id="narrow_repository_tests_after_timeout",
        selected_action={
            "id": "narrow_repository_tests_after_timeout",
            "reason": "Timed out; try narrower pytest targets.",
        },
        before_summary=before_summary,
        after_summary=after_summary,
        after_report=_Report(),
    )
    trace_fields = intelligence_module._auto_trace_after_fields(
        after_summary,
        _Report(),
    )

    assert audit["loop_verify_outcome"] == "timeout_narrowing_executed"
    assert audit["loop_verify_progress"] is True
    assert audit["loop_reflect_status"] == "verified_progress"
    assert "attempts=2" in audit["loop_verify_evidence"]
    assert trace_fields["verify_timeout_narrowing_status"] == "fail"
    assert trace_fields["verify_timeout_narrowing_reason"] == (
        "timeout_narrowing_selected_non_timeout_result"
    )
    assert trace_fields["verify_timeout_narrowing_executed"] is True
    assert trace_fields["verify_timeout_narrowing_attempt_count"] == 2
    assert trace_fields[
        "verify_timeout_narrowing_selected_failure_category"
    ] == "test_assertion_failure"


def test_auto_controller_reflection_rerun_downgrades_llm_without_api_key(
    monkeypatch,
):
    for env_name in (
        "CIA_LLM_API_KEY",
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
        "ALIBABA_API_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)

    rerun = intelligence_module._auto_action_rerun_kwargs(
        "run_patch_reflection_loop",
        {
            "run_repository_test_command": True,
            "checkout_repository_tests": True,
            "repository_test_timeout": 20,
            "run_repository_test_retry_prerequisites": False,
            "auto_repository_test_retry": False,
            "auto_repository_test_retry_max_risk": "low",
            "auto_repository_test_retry_allowed_runners": [],
            "repository_test_reflection_mode": "llm",
            "repository_test_reflection_rounds": 0,
            "repository_test_reflection_width": 0,
        },
    )

    assert rerun["repository_test_reflection_mode"] == "rule"
    assert rerun["repository_test_reflection_rounds"] == 1
    assert rerun["repository_test_reflection_width"] == 1
    assert rerun["run_repository_test_retry_prerequisites"] is True
    assert rerun["auto_repository_test_retry"] is True


def test_auto_controller_reflection_rerun_keeps_llm_with_api_key(monkeypatch):
    monkeypatch.setenv("CIA_LLM_API_KEY", "fake-key")
    monkeypatch.setenv("CIA_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("CIA_LLM_MODEL", "deepseekv4PRO")

    for action_id in ("run_patch_reflection_loop", "run_llm_patch_reflection_loop"):
        rerun = intelligence_module._auto_action_rerun_kwargs(
            action_id,
            {
                "run_repository_test_command": True,
                "checkout_repository_tests": True,
                "repository_test_timeout": 20,
                "run_repository_test_retry_prerequisites": False,
                "auto_repository_test_retry": False,
                "auto_repository_test_retry_max_risk": "low",
                "auto_repository_test_retry_allowed_runners": [],
                "repository_test_reflection_mode": "llm",
                "repository_test_reflection_rounds": 0,
                "repository_test_reflection_width": 0,
            },
        )

        assert rerun["repository_test_reflection_mode"] == "llm"
        assert rerun["repository_test_reflection_rounds"] == 1
        assert rerun["repository_test_reflection_width"] == 1


def test_auto_trace_observes_llm_patch_and_reflection_state():
    item = intelligence_module._auto_trace_item(
        iteration=0,
        summary={
            "analysis_readiness": {
                "current_stage": "phase3_patch_validation",
                "blocker": "no_candidate_passed_repository_tests",
                "dynamic_evidence_level": "failing_tests",
            },
            "fault_localization": {
                "mode": "dynamic",
                "status": "pass",
            },
            "agent_goal_readiness": {
                "status": "warning",
                "failed_criteria_count": 1,
                "failed_criteria": ["reflection_on_patch_failure"],
            },
            "repository_llm_patch_generation_status": "blocked",
            "repository_llm_patch_generation_reason": "missing_llm_api_key",
            "repository_llm_patch_generation_fallback_used": True,
            "repository_llm_reflection_status": "unavailable",
            "repository_llm_reflection_reason": "missing_api_key:CIA_LLM_API_KEY",
            "repository_llm_reflection_blocked": True,
        },
        controller={"status": "ready"},
        selected_action={
            "id": "run_patch_reflection_loop",
            "phase": "phase3",
            "tool": "repository_test_patch_validation",
            "executable_now": True,
            "reason": "Run reflection.",
        },
    )

    assert item["observe_repository_llm_patch_generation_status"] == "blocked"
    assert item["observe_repository_llm_patch_generation_reason"] == (
        "missing_llm_api_key"
    )
    assert item["observe_repository_llm_patch_generation_fallback_used"] is True
    assert item["observe_repository_llm_reflection_status"] == "unavailable"
    assert item["observe_repository_llm_reflection_reason"] == (
        "missing_api_key:CIA_LLM_API_KEY"
    )
    assert item["observe_repository_llm_reflection_blocked"] is True


def test_github_repo_intelligence_prefers_dynamic_fault_localization():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        onboarding_report = dict(report.onboarding_report or {})
        onboarding_report["repository_test_fault_localization"] = {
            "status": "pass",
            "reason": "localized_from_dynamic_evidence",
            "ranking_count": 1,
            "matched_failed_test_count": 1,
            "unmatched_failed_test_count": 0,
            "dynamic_evidence_level": "failing_tests",
            "top_function": "normalize",
            "top_function_id": "helpers.py::normalize",
            "top_score": 0.91,
            "rankings": [
                {
                    "rank": 1,
                    "function_id": "helpers.py::normalize",
                    "function_name": "normalize",
                    "file_path": "helpers.py",
                    "start_line": 1,
                    "end_line": 2,
                    "score": 0.91,
                    "signals": {
                        "dynamic_test_evidence": 1.0,
                        "static": 0.2,
                        "graph": 0.7,
                        "sbfl": 1.0,
                    },
                }
            ],
        }
        report = replace(report, onboarding_report=onboarding_report)

        summary = github_repo_intelligence_summary(report)
        markdown = render_github_repo_intelligence_summary(report)

        localization = summary["fault_localization"]
        assert localization["mode"] == "dynamic"
        assert localization["status"] == "pass"
        assert localization["reason"] == "localized_from_dynamic_evidence"
        assert localization["source"] == "repository_test_fault_localization"
        assert localization["top_function"] == "normalize"
        assert localization["static_fallback_available"] is True
        assert localization["static_fallback_top_function"] == "mean"
        assert localization["rankings"][0]["dynamic_test_evidence_score"] == 1.0
        assert localization["rankings"][0]["sbfl_score"] == 1.0
        assert localization["rankings"][0]["final_score"] == 0.91
        readiness = summary["analysis_readiness"]
        assert readiness["current_stage"] == "phase2_dynamic_fault_localization"
        assert readiness["next_stage"] == "phase3_patch_generation"
        assert readiness["can_attempt_patch_repair"] is True
        assert "phase2_dynamic_fault_localization" in readiness["completed_phases"]
        assert summary["agent_controller"]["selected_action"]["id"] == (
            "generate_and_validate_patches"
        )
        assert summary["agent_controller"]["selected_action"]["executable_now"] is True
        assert "Mode: `dynamic`" in markdown
        assert "| 1 | normalize | helpers.py |" in markdown


def test_github_repo_intelligence_auto_controller_runs_patch_validation_action(
    monkeypatch,
):
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        dynamic_localization = {
            "status": "pass",
            "reason": "localized_from_dynamic_evidence",
            "ranking_count": 1,
            "matched_failed_test_count": 1,
            "unmatched_failed_test_count": 0,
            "dynamic_evidence_level": "failing_tests",
            "top_function": "mean",
            "top_function_id": "average_mean.py::mean",
            "top_score": 0.94,
            "rankings": [
                {
                    "rank": 1,
                    "function_id": "average_mean.py::mean",
                    "function_name": "mean",
                    "file_path": "average_mean.py",
                    "start_line": 3,
                    "end_line": 7,
                    "score": 0.94,
                    "signals": {
                        "dynamic_test_evidence": 1.0,
                        "static": 1.0,
                        "graph": 0.8,
                        "sbfl": 1.0,
                    },
                }
            ],
        }
        onboarding_report = dict(report.onboarding_report or {})
        onboarding_report["repository_test_fault_localization"] = (
            dynamic_localization
        )
        report = replace(report, onboarding_report=onboarding_report)

        captured = {}

        def fake_run_github_repo_agent(repo_spec, output_dir_arg, **kwargs):
            captured["repo_spec"] = repo_spec
            captured["output_dir"] = str(output_dir_arg)
            captured.update(kwargs)
            patched_onboarding = dict(onboarding_report)
            patched_onboarding["repository_test_fault_localization"] = (
                dynamic_localization
            )
            patched_onboarding["repository_test_patch_candidates"] = {
                "status": "pass",
                "reason": "generated_from_dynamic_fault_localization",
                "candidate_count": 1,
            }
            patched_onboarding["repository_test_patch_validation"] = {
                "status": "pass",
                "reason": "patch_validation_success",
                "executed_count": 1,
                "success_count": 1,
                "repair_ready": True,
                "regression_ready": True,
                "repair_validation_scope": "repository_tests",
                "best_candidate_id": "candidate_1",
                "best_candidate_rule_id": "missing_len_zero_guard",
                "best_candidate_variant": "guard_empty_sequence",
                "best_candidate_success": True,
                "results": [
                    {
                        "candidate_id": "candidate_1",
                        "depth": 0,
                        "success": True,
                        "failure_type": "success",
                    }
                ],
            }
            patched_summary = {
                **report.summary,
                "repository_test_patch_candidates_status": "pass",
                "repository_test_patch_validation_status": "pass",
                "repository_test_patch_validation_reason": (
                    "patch_validation_success"
                ),
                "repository_test_patch_validation_executed_count": 1,
                "repository_test_patch_validation_success_count": 1,
                "repository_test_repair_ready": True,
                "repository_test_patch_validation_reflection_mode": "rule",
                "repository_test_patch_validation_reflection_candidate_count": 0,
                "repository_test_patch_validation_successful_reflection_count": 0,
            }
            return replace(
                report,
                summary=patched_summary,
                onboarding_report=patched_onboarding,
            )

        monkeypatch.setattr(
            intelligence_module,
            "run_github_repo_agent",
            fake_run_github_repo_agent,
        )

        report = intelligence_module._run_auto_controller_actions(
            report,
            repo_spec="example/project",
            output_dir=output_dir,
            agent_kwargs={
                "repository_test_timeout": 20,
                "checkout_repository_tests": False,
                "run_repository_test_command": True,
                "run_repository_test_retry_prerequisites": False,
                "auto_repository_test_retry": False,
                "auto_repository_test_retry_max_risk": "low",
                "auto_repository_test_retry_allowed_runners": [],
                "repository_test_reflection_mode": "rule",
                "repository_test_reflection_rounds": 1,
                "repository_test_reflection_width": 1,
            },
            max_actions=2,
        )
        summary = github_repo_intelligence_summary(report)

        assert captured["repo_spec"] == "example/project"
        assert captured["checkout_repository_tests"] is True
        assert captured["run_repository_test_command"] is True
        assert captured["run_repository_test_retry_prerequisites"] is True
        assert captured["auto_repository_test_retry"] is True
        assert captured["auto_repository_test_retry_max_risk"] == "medium"
        assert captured["auto_repository_test_retry_allowed_runners"] == [
            "pytest",
            "unittest",
        ]
        assert captured["repository_test_timeout"] == 30

        assert summary["agent_auto_action_count"] == 1
        assert summary["agent_auto_stop_reason"] == (
            "phase_goal_reached:patch_validation_ready"
        )
        assert summary["agent_auto_actions"][0]["action_id"] == (
            "generate_and_validate_patches"
        )
        assert summary["agent_auto_actions"][0][
            "after_patch_validation_status"
        ] == "pass"
        assert summary["agent_auto_actions"][0][
            "after_patch_validation_success_count"
        ] == 1
        assert summary["agent_auto_actions"][0]["after_repair_ready"] is True
        assert summary["agent_auto_actions"][0][
            "run_repository_test_retry_prerequisites"
        ] is True
        assert summary["agent_auto_actions"][0]["auto_repository_test_retry"] is True
        assert summary["agent_auto_trace"][0]["auto_executed"] is True
        assert summary["agent_auto_trace"][0]["plan_selected_action"] == (
            "generate_and_validate_patches"
        )
        assert summary["agent_auto_trace"][0][
            "verify_patch_validation_status"
        ] == "pass"
        assert summary["agent_auto_trace"][1]["auto_executed"] is False
        assert summary["agent_auto_trace"][1]["plan_selected_action"] == (
            "run_search_and_ablation_evaluation"
        )
        assert summary["agent_auto_trace"][1]["stop_reason"] == (
            "phase_goal_reached:patch_validation_ready"
        )
        assert summary["analysis_readiness"]["current_stage"] == (
            "phase3_patch_validation"
        )
        assert summary["agent_controller"]["selected_action"]["id"] == (
            "run_search_and_ablation_evaluation"
        )


def test_github_repo_intelligence_controller_replans_after_patch_failure():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        onboarding_report = dict(report.onboarding_report or {})
        onboarding_report["repository_test_fault_localization"] = {
            "status": "pass",
            "reason": "localized_from_dynamic_evidence",
            "ranking_count": 1,
            "dynamic_evidence_level": "failing_tests",
            "top_function": "normalize",
            "top_function_id": "helpers.py::normalize",
            "rankings": [
                {
                    "rank": 1,
                    "function_id": "helpers.py::normalize",
                    "function_name": "normalize",
                    "file_path": "helpers.py",
                    "score": 0.91,
                    "signals": {"dynamic_test_evidence": 1.0},
                }
            ],
        }
        report = replace(
            report,
            summary={
                **report.summary,
                "repository_test_patch_validation_status": "fail",
                "repository_test_patch_validation_reason": (
                    "no_candidate_passed_repository_tests"
                ),
                "repository_test_patch_validation_reflection_candidate_count": 2,
                "repository_test_patch_validation_successful_reflection_count": 0,
                "reflection_trace_markdown": "out/reflection_trace.md",
            },
            onboarding_report=onboarding_report,
        )

        summary = github_repo_intelligence_summary(report)

        readiness = summary["analysis_readiness"]
        assert readiness["current_stage"] == "phase3_patch_validation"
        assert readiness["next_stage"] == "phase3_patch_reflection_or_expansion"
        assert readiness["blocker"] == "no_candidate_passed_repository_tests"
        assert readiness["patch_validation_status"] == "fail"
        assert "phase3_patch_validation" in readiness["completed_phases"]
        reflection = summary["reflection_summary"]
        assert reflection["patch_validation_status"] == "fail"
        assert reflection["patch_validation_reason"] == (
            "no_candidate_passed_repository_tests"
        )
        assert reflection["reflection_candidate_count"] == 2
        assert reflection["successful_reflection_candidate_count"] == 0
        controller = summary["agent_controller"]
        assert controller["selected_action"]["id"] == (
            "expand_patch_candidates_or_reflection"
        )
        assert controller["selected_action"]["executable_now"] is False
        assert controller["reflection"]["fallback_action"] == (
            "expand_patch_candidates_or_reflection"
        )


def test_github_repo_intelligence_controller_reflects_when_patch_not_repair_ready():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        onboarding_report = dict(report.onboarding_report or {})
        onboarding_report["repository_test_fault_localization"] = {
            "status": "pass",
            "reason": "localized_from_dynamic_evidence",
            "ranking_count": 1,
            "dynamic_evidence_level": "failing_tests",
            "top_function": "mean",
            "top_function_id": "average_mean.py::mean",
            "rankings": [
                {
                    "rank": 1,
                    "function_id": "average_mean.py::mean",
                    "function_name": "mean",
                    "file_path": "average_mean.py",
                    "score": 0.94,
                    "signals": {
                        "dynamic_test_evidence": 1.0,
                        "static": 1.0,
                        "graph": 0.8,
                        "sbfl": 1.0,
                    },
                }
            ],
        }
        report = replace(
            report,
            summary={
                **report.summary,
                "repository_test_patch_candidates_status": "pass",
                "repository_test_patch_validation_status": "pass",
                "repository_test_patch_validation_reason": (
                    "patch_validation_success"
                ),
                "repository_test_patch_validation_executed_count": 4,
                "repository_test_patch_validation_success_count": 1,
                "repository_test_repair_ready": False,
                "repository_test_repair_validation_scope": "regression_failed",
                "repository_test_patch_validation_reflection_mode": "none",
                "repository_test_patch_validation_reflection_candidate_count": 0,
                "repository_test_patch_validation_successful_reflection_count": 0,
                "repository_test_patch_validation_max_depth": 0,
                "repository_test_setup_doctor_next_action": (
                    "Inspect repository_test_environment_setup_result."
                ),
            },
            onboarding_report=onboarding_report,
        )

        summary = github_repo_intelligence_summary(report)

        readiness = summary["analysis_readiness"]
        assert readiness["current_stage"] == "phase3_patch_validation"
        assert readiness["next_stage"] == "phase3_patch_reflection_or_expansion"
        assert readiness["repair_ready"] is False
        assert readiness["repair_validation_scope"] == "regression_failed"
        assert readiness["blocker"] == (
            "patch_validation_not_repair_ready:regression_failed"
        )
        assert readiness["next_action"] == (
            "Patch validation produced candidate evidence, but repair is not "
            "fully verified; inspect regression failures and run reflection or "
            "expand patch candidates."
        )
        assert (
            "phase3_patch_validation" in readiness["completed_phases"]
        )

        answers = summary["agent_answers"]
        assert answers["repairability"]["status"] == (
            "patch_validated_but_not_repair_ready"
        )
        assert answers["repairability"]["repair_ready"] is False
        assert answers["repairability"]["repair_validation_scope"] == (
            "regression_failed"
        )

        controller = summary["agent_controller"]
        assert controller["selected_action"]["id"] == "run_patch_reflection_loop"
        assert controller["selected_action"]["executable_now"] is True
        assert controller["verification"]["status"] == "repair_not_verified"
        assert controller["reflection"]["fallback_action"] == (
            "run_patch_reflection_loop"
        )
        assert controller["termination"]["status"] == "blocked"
        assert controller["termination"]["reason"] == (
            "patch_validation_not_repair_ready:regression_failed"
        )
        assert controller["selected_action"]["id"] != (
            "run_search_and_ablation_evaluation"
        )


def test_github_repo_intelligence_repair_ready_prefers_baseline_caveat_action():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        onboarding_report = dict(report.onboarding_report or {})
        onboarding_report["repository_test_fault_localization"] = {
            "status": "pass",
            "reason": "localized_from_dynamic_evidence",
            "ranking_count": 1,
            "dynamic_evidence_level": "failing_tests",
            "top_function": "mean",
            "top_function_id": "average_mean.py::mean",
            "rankings": [
                {
                    "rank": 1,
                    "function_id": "average_mean.py::mean",
                    "function_name": "mean",
                    "file_path": "average_mean.py",
                    "score": 0.94,
                    "signals": {"dynamic_test_evidence": 1.0},
                }
            ],
        }
        report = replace(
            report,
            summary={
                **report.summary,
                "repository_test_patch_candidates_status": "pass",
                "repository_test_patch_validation_status": "pass",
                "repository_test_patch_validation_reason": (
                    "patch_validation_success"
                ),
                "repository_test_patch_validation_executed_count": 4,
                "repository_test_patch_validation_success_count": 1,
                "repository_test_repair_ready": True,
                "repository_test_repair_validation_scope": (
                    "narrow_and_unchanged_regression_baseline"
                ),
                "repository_test_setup_doctor_next_action": (
                    "Inspect repository_test_environment_setup_result."
                ),
            },
            onboarding_report=onboarding_report,
        )
        patch_candidates_path = output_dir / "repository_test_patch_candidates.json"
        patch_validation_path = output_dir / "repository_test_patch_validation.json"
        expected_patch_args = [
            "tests/test_average_mean.py::TestMean::test_empty_sequence"
        ]
        patch_candidates_payload = {
            "status": "pass",
            "reason": "patch_candidates_generated",
            "candidate_count": 4,
            "recommended_pytest_args": expected_patch_args,
            "recommended_pytest_args_source": "dynamic_evidence_nodeids",
            "candidates": [
                {
                    "id": "candidate-1",
                    "rule_id": "missing_len_zero_guard",
                    "variant": "raise_value_error",
                },
                {
                    "id": "candidate-2",
                    "rule_id": "missing_len_zero_guard",
                    "variant": "return_default_on_empty",
                },
                {
                    "id": "candidate-3",
                    "rule_id": "type_guard",
                    "variant": "coerce_input",
                },
                {
                    "id": "candidate-4",
                    "rule_id": "bounds_guard",
                    "variant": "skip_empty",
                },
            ],
        }
        patch_validation_payload = {
            "status": "pass",
            "reason": "patch_validation_success",
            "candidate_count": 4,
            "executed_count": 4,
            "success_count": 1,
            "regression_validation": {
                "status": "baseline_failed_unchanged",
                "reason": "regression_baseline_failed_unchanged",
                "baseline_failed_unchanged": True,
            },
            "results": [
                {
                    "candidate_id": "candidate-1",
                    "target_function_id": "average_mean.py::mean",
                    "target_function_name": "mean",
                    "relative_file_path": "average_mean.py",
                    "rule_id": "missing_len_zero_guard",
                    "variant": "raise_value_error",
                    "depth": 0,
                    "success": False,
                    "failure_type": "test_failure",
                    "score": 0.91,
                    "feedback_score": 0.1,
                    "search_prior_score": 0.91,
                },
                {
                    "candidate_id": "candidate-2",
                    "target_function_id": "average_mean.py::mean",
                    "target_function_name": "mean",
                    "relative_file_path": "average_mean.py",
                    "rule_id": "missing_len_zero_guard",
                    "variant": "return_default_on_empty",
                    "depth": 0,
                    "success": True,
                    "failure_type": "success",
                    "score": 0.89,
                    "feedback_score": 1.0,
                    "search_prior_score": 0.89,
                },
                {
                    "candidate_id": "candidate-3",
                    "target_function_id": "average_mean.py::mean",
                    "target_function_name": "mean",
                    "relative_file_path": "average_mean.py",
                    "rule_id": "type_guard",
                    "variant": "coerce_input",
                    "depth": 0,
                    "success": False,
                    "failure_type": "attribute_error",
                    "score": 0.42,
                    "feedback_score": 0.0,
                    "search_prior_score": 0.42,
                },
                {
                    "candidate_id": "candidate-4",
                    "target_function_id": "average_mean.py::mean",
                    "target_function_name": "mean",
                    "relative_file_path": "average_mean.py",
                    "rule_id": "bounds_guard",
                    "variant": "skip_empty",
                    "depth": 0,
                    "success": False,
                    "failure_type": "attribute_error",
                    "score": 0.31,
                    "feedback_score": 0.0,
                    "search_prior_score": 0.31,
                },
            ],
        }
        patch_candidates_path.write_text(
            json.dumps(patch_candidates_payload, indent=2),
            encoding="utf-8",
        )
        patch_validation_path.write_text(
            json.dumps(patch_validation_payload, indent=2),
            encoding="utf-8",
        )
        report = replace(
            report,
            output_paths={
                **report.output_paths,
                "repository_test_patch_candidates_json": str(patch_candidates_path),
                "repository_test_patch_validation_json": str(patch_validation_path),
            },
        )

        summary = github_repo_intelligence_summary(report)
        readiness = summary["analysis_readiness"]

        assert readiness["current_stage"] == "phase3_patch_validation"
        assert readiness["next_stage"] == "phase4_search_and_evaluation"
        assert readiness["blocker"] == ""
        assert readiness["repair_ready"] is True
        assert readiness["repair_validation_scope"] == (
            "narrow_and_unchanged_regression_baseline"
        )
        assert readiness["next_action"] == (
            "Patch validation is repair-ready for the target failure, but "
            "broad regression has an unchanged baseline failure; fix or narrow "
            "that regression command before claiming full-suite green status."
        )
        phase4 = summary["phase4_search_evaluation"]
        assert phase4["status"] == "ready"
        assert phase4["reason"] == (
            "target_repair_ready_with_unchanged_regression_baseline"
        )
        assert phase4["ready_for_phase4"] is True
        assert phase4["baseline_regression_caveat"] is True
        assert phase4["full_suite_green_claim_allowed"] is False
        assert phase4["search_budget"]["executed_count"] == 4
        assert phase4["search_budget"]["success_count"] == 1
        assert phase4["evaluation_gates"][2]["name"] == "repair_ready"
        assert phase4["evaluation_gates"][2]["passed"] is True
        assert phase4["evaluation_gates"][3]["name"] == (
            "full_suite_green_claim_allowed"
        )
        assert phase4["evaluation_gates"][3]["passed"] is False
        assert "unchanged broad regression command" in phase4["next_actions"][1]
        assert (
            summary["repository_test_patch_recommended_pytest_args"]
            == expected_patch_args
        )
        assert (
            summary["repository_test_patch_recommended_pytest_args_source"]
            == "dynamic_evidence_nodeids"
        )

        write_github_repo_intelligence_artifacts(report, summary)
        saved = json.loads(
            (output_dir / "github_repo_intelligence.json").read_text(
                encoding="utf-8"
            )
        )
        intelligence_markdown = (
            output_dir / "github_repo_intelligence.md"
        ).read_text(encoding="utf-8")
        phase4_markdown = (
            output_dir / "phase4_search_evaluation.md"
        ).read_text(encoding="utf-8")
        phase4_rows = {
            item["name"]: item
            for item in saved["artifact_inventory"]["groups"]["phase4"]
        }
        assert (output_dir / "phase4_search_evaluation.json").exists()
        assert (output_dir / "phase4_search_evaluation.md").exists()
        assert saved["phase4_search_evaluation"]["ready_for_phase4"] is True
        assert (
            saved["repository_test_patch_recommended_pytest_args"]
            == expected_patch_args
        )
        assert (
            saved["repository_test_patch_recommended_pytest_args_source"]
            == "dynamic_evidence_nodeids"
        )
        assert phase4_rows["phase4_search_evaluation.json"]["required_now"] is True
        assert phase4_rows["phase4_search_evaluation.json"]["available"] is True
        assert "Phase 4 Search Evaluation" in phase4_markdown
        assert "Full-Suite Green Claim Allowed: false" in phase4_markdown
        assert "Patch Validation Args Source: `dynamic_evidence_nodeids`" in (
            intelligence_markdown
        )
        assert "tests/test_average_mean.py::TestMean::test_empty_sequence" in (
            intelligence_markdown
        )

        auto_report = intelligence_module._run_auto_controller_actions(
            report,
            repo_spec="example/project",
            output_dir=output_dir,
            agent_kwargs={},
            max_actions=1,
            auto_phase4_evaluation=True,
        )
        auto_summary = github_repo_intelligence_summary(auto_report)
        auto_phase4 = auto_summary["phase4_search_evaluation"]
        execution = auto_phase4["execution"]

        assert auto_report.summary["agent_auto_action_count"] == 1
        assert auto_report.summary["agent_auto_actions"][0]["action_id"] == (
            "run_search_and_ablation_evaluation"
        )
        assert auto_report.summary["agent_auto_actions"][0][
            "loop_verify_outcome"
        ] == "phase4_evaluation_recorded"
        assert auto_report.summary["agent_auto_actions"][0][
            "after_phase4_evaluation_executed"
        ] is True
        assert auto_report.summary["agent_auto_trace"][0][
            "verify_phase4_evaluation_executed"
        ] is True
        assert execution["executed"] is True
        assert execution["status"] == "pass"
        assert execution["reason"] == "repository_phase4_search_ablation_evaluated"
        assert execution["mode"] == "repository_search_ablation_phase4"
        assert auto_phase4["ready_for_phase4"] is True
        assert Path(execution["json"]).exists()
        assert Path(execution["markdown"]).exists()
        execution_payload = json.loads(Path(execution["json"]).read_text(
            encoding="utf-8"
        ))
        execution_markdown = Path(execution["markdown"]).read_text(encoding="utf-8")
        ablations = {
            row["variant"]: row for row in execution_payload["ablation_variants"]
        }
        assert execution_payload["search_evaluation"]["evidence_level"] == (
            "validation_results"
        )
        assert execution_payload["candidate_ranking"][1]["candidate_id"] == (
            "candidate-2"
        )
        assert execution_payload["candidate_ranking"][1]["success"] is True
        assert execution_payload["strategy_evaluation"]["full_search"][
            "first_success_rank"
        ] == 2
        assert ablations["top1_only"]["status"] == "regression"
        assert execution_payload["search_claim"]["claim"] == (
            "target_repair_validated_with_baseline_regression_caveat"
        )
        assert "Phase 4 Search Evaluation Execution" in execution_markdown
        assert "Ablation Variants" in execution_markdown
        assert "Candidate Ranking" in execution_markdown


def test_github_repo_intelligence_phase4_can_rerun_search_strategies(monkeypatch):
    class FakeSandbox:
        def __init__(self, timeout=0):
            self.timeout = timeout

        def apply_patch_and_test(self, repo_path, candidate, test_args=None):
            success = candidate.id == "candidate-2"
            return ExecutionResult(
                success=success,
                returncode=0 if success else 1,
                stdout="",
                stderr="",
                traceback="",
                passed=1 if success else 0,
                failed=0 if success else 1,
                timeout=False,
                command=["pytest", *(test_args or [])],
            )

    monkeypatch.setattr(intelligence_module, "Sandbox", FakeSandbox)
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))
        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        repo_root = output_dir / "repository_checkout"
        repo_root.mkdir(parents=True)
        target_file = repo_root / "average_mean.py"
        old_source = (
            "def mean(values):\n"
            "    total = sum(values)\n"
            "    return total / len(values)\n"
        )
        target_file.write_text(old_source, encoding="utf-8")
        candidates = []
        validation_results = []
        variants = [
            ("candidate-1", "raise_value_error", False, 0.91),
            ("candidate-2", "return_default_on_empty", True, 0.89),
            ("candidate-3", "return_none", False, 0.41),
        ]
        for index, (candidate_id, variant, success, score) in enumerate(
            variants,
            start=1,
        ):
            new_source = (
                old_source
                + f"\n# candidate {index}: {variant}\n"
            )
            candidates.append(
                {
                    "id": candidate_id,
                    "target_file": str(target_file),
                    "relative_file_path": "average_mean.py",
                    "target_function_id": "average_mean.py::mean",
                    "target_function_name": "mean",
                    "rule_id": "missing_len_zero_guard",
                    "description": variant,
                    "old_source": old_source,
                    "new_source": new_source,
                    "diff": f"@@ candidate {index} {variant} @@",
                    "metadata": {"variant": variant},
                }
            )
            validation_results.append(
                {
                    "candidate_id": candidate_id,
                    "target_function_id": "average_mean.py::mean",
                    "target_function_name": "mean",
                    "relative_file_path": "average_mean.py",
                    "rule_id": "missing_len_zero_guard",
                    "variant": variant,
                    "depth": 0,
                    "success": success,
                    "failure_type": "success" if success else "test_failure",
                    "score": score,
                    "feedback_score": 1.0 if success else 0.0,
                    "search_prior_score": score,
                }
            )
        patch_candidates_path = output_dir / "repository_test_patch_candidates.json"
        patch_validation_path = output_dir / "repository_test_patch_validation.json"
        patch_candidates_path.write_text(
            json.dumps(
                {
                    "status": "pass",
                    "reason": "patch_candidates_generated",
                    "candidate_count": len(candidates),
                    "candidates": candidates,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        patch_validation_path.write_text(
            json.dumps(
                {
                    "status": "pass",
                    "reason": "patch_validation_success",
                    "repository_root": str(repo_root),
                    "candidate_count": len(candidates),
                    "executed_count": len(validation_results),
                    "success_count": 1,
                    "repair_ready": True,
                    "repair_validation_scope": (
                        "narrow_and_unchanged_regression_baseline"
                    ),
                    "recommended_pytest_args": [
                        "tests/test_average_mean.py::test_empty"
                    ],
                    "regression_validation": {
                        "status": "baseline_failed_unchanged",
                        "reason": "regression_baseline_failed_unchanged",
                        "baseline_failed_unchanged": True,
                    },
                    "results": validation_results,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        report = replace(
            report,
            summary={
                **report.summary,
                "repository_test_patch_candidates_status": "pass",
                "repository_test_patch_validation_status": "pass",
                "repository_test_patch_validation_reason": (
                    "patch_validation_success"
                ),
                "repository_test_patch_validation_executed_count": 3,
                "repository_test_patch_validation_success_count": 1,
                "repository_test_repair_ready": True,
                "repository_test_repair_validation_scope": (
                    "narrow_and_unchanged_regression_baseline"
                ),
            },
            output_paths={
                **report.output_paths,
                "repository_test_patch_candidates_json": str(patch_candidates_path),
                "repository_test_patch_validation_json": str(patch_validation_path),
            },
        )

        auto_report = intelligence_module._run_auto_controller_actions(
            report,
            repo_spec="example/project",
            output_dir=output_dir,
            agent_kwargs={},
            max_actions=1,
            auto_phase4_evaluation=True,
            auto_phase4_strategy_reruns=True,
            phase4_strategy_rerun_limit=2,
            phase4_strategy_rerun_timeout=7,
        )
        summary = github_repo_intelligence_summary(auto_report)
        execution = summary["phase4_search_evaluation"]["execution"]
        payload = json.loads(Path(execution["json"]).read_text(encoding="utf-8"))
        rerun = payload["strategy_rerun"]

        assert execution["strategy_rerun_status"] == "pass", execution.get(
            "strategy_rerun_reason"
        )
        assert execution["strategy_rerun_strategy_count"] == 4
        assert execution["strategy_rerun_total_evaluated_count"] >= 4
        assert rerun["enabled"] is True
        assert rerun["status"] == "pass"
        assert rerun["reason"] == "strategy_rerun_completed"
        assert rerun["rerun_limit"] == 2
        assert rerun["timeout"] == 7
        assert {row["name"] for row in rerun["strategies"]} == {
            "beam_full",
            "without_prior_ranking",
            "without_diversity_reranking",
            "without_candidate_deduplication",
        }
        assert all(row["evaluated_count"] <= 2 for row in rerun["strategies"])
        assert any(row["success_count"] >= 1 for row in rerun["strategies"])
        assert {row["variant"] for row in rerun["ablation_deltas"]} == {
            "without_prior_ranking",
            "without_diversity_reranking",
            "without_candidate_deduplication",
        }
        markdown = Path(execution["markdown"]).read_text(encoding="utf-8")
        assert "Strategy Rerun" in markdown
        assert "without_prior_ranking" in markdown


def test_github_repo_intelligence_auto_controller_reflects_not_repair_ready_patch(
    monkeypatch,
):
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        dynamic_localization = {
            "status": "pass",
            "reason": "localized_from_dynamic_evidence",
            "ranking_count": 1,
            "dynamic_evidence_level": "failing_tests",
            "top_function": "mean",
            "top_function_id": "average_mean.py::mean",
            "rankings": [
                {
                    "rank": 1,
                    "function_id": "average_mean.py::mean",
                    "function_name": "mean",
                    "file_path": "average_mean.py",
                    "score": 0.94,
                    "signals": {"dynamic_test_evidence": 1.0},
                }
            ],
        }
        onboarding_report = dict(report.onboarding_report or {})
        onboarding_report["repository_test_fault_localization"] = (
            dynamic_localization
        )
        report = replace(
            report,
            summary={
                **report.summary,
                "repository_test_patch_candidates_status": "pass",
                "repository_test_patch_validation_status": "pass",
                "repository_test_patch_validation_reason": (
                    "patch_validation_success"
                ),
                "repository_test_patch_validation_executed_count": 4,
                "repository_test_patch_validation_success_count": 1,
                "repository_test_repair_ready": False,
                "repository_test_repair_validation_scope": "regression_failed",
                "repository_test_patch_validation_reflection_mode": "none",
                "repository_test_patch_validation_reflection_candidate_count": 0,
                "repository_test_patch_validation_successful_reflection_count": 0,
                "repository_test_patch_validation_max_depth": 0,
            },
            onboarding_report=onboarding_report,
        )
        captured = {}

        def fake_run_github_repo_agent(repo_spec, output_dir_arg, **kwargs):
            captured["repo_spec"] = repo_spec
            captured["output_dir"] = str(output_dir_arg)
            captured.update(kwargs)
            reflected_onboarding = dict(onboarding_report)
            reflected_onboarding["repository_test_fault_localization"] = (
                dynamic_localization
            )
            reflected_summary = {
                **report.summary,
                "repository_test_patch_validation_status": "pass",
                "repository_test_patch_validation_reason": (
                    "patch_validation_success"
                ),
                "repository_test_patch_validation_executed_count": 5,
                "repository_test_patch_validation_success_count": 2,
                "repository_test_repair_ready": True,
                "repository_test_repair_validation_scope": "repository_tests",
                "repository_test_patch_validation_reflection_mode": "rule",
                "repository_test_patch_validation_reflection_candidate_count": 1,
                "repository_test_patch_validation_successful_reflection_count": 1,
                "repository_test_patch_validation_max_depth": 1,
            }
            return replace(
                report,
                summary=reflected_summary,
                onboarding_report=reflected_onboarding,
            )

        monkeypatch.setattr(
            intelligence_module,
            "run_github_repo_agent",
            fake_run_github_repo_agent,
        )

        report = intelligence_module._run_auto_controller_actions(
            report,
            repo_spec="example/project",
            output_dir=output_dir,
            agent_kwargs={
                "repository_test_timeout": 20,
                "checkout_repository_tests": False,
                "run_repository_test_command": True,
                "run_repository_test_retry_prerequisites": False,
                "auto_repository_test_retry": False,
                "auto_repository_test_retry_max_risk": "low",
                "auto_repository_test_retry_allowed_runners": [],
                "repository_test_reflection_mode": "none",
                "repository_test_reflection_rounds": 0,
                "repository_test_reflection_width": 0,
            },
            max_actions=2,
        )
        summary = github_repo_intelligence_summary(report)

        assert captured["repo_spec"] == "example/project"
        assert captured["repository_test_reflection_mode"] == "rule"
        assert captured["repository_test_reflection_rounds"] == 1
        assert captured["repository_test_reflection_width"] == 1
        assert captured["run_repository_test_retry_prerequisites"] is True
        assert captured["auto_repository_test_retry"] is True

        assert summary["agent_auto_action_count"] == 1
        assert summary["agent_auto_stop_reason"] == (
            "phase_goal_reached:patch_validation_ready"
        )
        assert summary["agent_auto_actions"][0]["action_id"] == (
            "run_patch_reflection_loop"
        )
        assert summary["agent_auto_actions"][0]["loop_verify_outcome"] == (
            "phase_goal_reached:patch_validation_ready"
        )
        assert summary["agent_auto_trace"][0]["auto_executed"] is True
        assert summary["agent_auto_trace"][0]["plan_selected_action"] == (
            "run_patch_reflection_loop"
        )
        assert summary["agent_auto_trace"][1]["auto_executed"] is False
        assert summary["agent_auto_trace"][1]["plan_selected_action"] == (
            "run_search_and_ablation_evaluation"
        )
        assert summary["agent_controller"]["selected_action"]["id"] == (
            "run_search_and_ablation_evaluation"
        )
        assert summary["agent_answers"]["repairability"]["status"] == (
            "repair_ready"
        )


def test_github_repo_intelligence_auto_controller_regenerates_safety_blocked_candidates(
    monkeypatch,
):
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        dynamic_localization = {
            "status": "pass",
            "reason": "localized_from_dynamic_evidence",
            "ranking_count": 1,
            "dynamic_evidence_level": "failing_tests",
            "top_function": "mean",
            "top_function_id": "average_mean.py::mean",
            "rankings": [
                {
                    "rank": 1,
                    "function_id": "average_mean.py::mean",
                    "function_name": "mean",
                    "file_path": "average_mean.py",
                    "score": 0.94,
                    "signals": {"dynamic_test_evidence": 1.0},
                }
            ],
        }
        onboarding_report = dict(report.onboarding_report or {})
        onboarding_report["repository_test_fault_localization"] = (
            dynamic_localization
        )
        report = replace(
            report,
            summary={
                **report.summary,
                "repository_test_patch_candidates_status": "pass",
                "repository_patch_generation_mode": "rule",
                "repository_patch_safety_gate_status": "blocked",
                "repository_patch_safety_gate_blocked_count": 1,
                "repository_test_patch_validation_status": "skipped",
                "repository_test_patch_validation_reason": (
                    "all_candidates_blocked_by_safety_gate"
                ),
                "repository_test_patch_validation_input_candidate_count": 1,
                "repository_test_patch_validation_candidate_count": 0,
                "repository_test_patch_validation_safety_blocked_candidate_count": 1,
                "repository_test_patch_validation_executed_count": 0,
                "repository_test_patch_validation_success_count": 0,
                "repository_test_repair_ready": False,
                "repository_test_repair_validation_scope": "none",
            },
            onboarding_report=onboarding_report,
        )
        captured = {}

        def fake_run_github_repo_agent(repo_spec, output_dir_arg, **kwargs):
            captured["repo_spec"] = repo_spec
            captured["output_dir"] = str(output_dir_arg)
            captured.update(kwargs)
            regenerated_summary = {
                **report.summary,
                "repository_test_patch_candidates_status": "pass",
                "repository_patch_generation_mode": "hybrid",
                "repository_patch_safety_gate_status": "pass",
                "repository_patch_safety_gate_blocked_count": 0,
                "repository_test_patch_validation_status": "pass",
                "repository_test_patch_validation_reason": (
                    "patch_validation_success"
                ),
                "repository_test_patch_validation_input_candidate_count": 1,
                "repository_test_patch_validation_candidate_count": 1,
                "repository_test_patch_validation_safety_blocked_candidate_count": 0,
                "repository_test_patch_validation_executed_count": 1,
                "repository_test_patch_validation_success_count": 1,
                "repository_test_repair_ready": True,
                "repository_test_repair_validation_scope": "narrow_only",
            }
            return replace(
                report,
                summary=regenerated_summary,
                onboarding_report=onboarding_report,
            )

        monkeypatch.setattr(
            intelligence_module,
            "run_github_repo_agent",
            fake_run_github_repo_agent,
        )

        report = intelligence_module._run_auto_controller_actions(
            report,
            repo_spec="example/project",
            output_dir=output_dir,
            agent_kwargs={
                "repository_test_timeout": 20,
                "checkout_repository_tests": False,
                "run_repository_test_command": True,
                "run_repository_test_retry_prerequisites": False,
                "auto_repository_test_retry": False,
                "auto_repository_test_retry_max_risk": "low",
                "auto_repository_test_retry_allowed_runners": [],
                "repository_patch_generation_mode": "rule",
                "repository_llm_patch_candidate_limit": None,
                "repository_test_reflection_mode": "none",
                "repository_test_reflection_rounds": 0,
                "repository_test_reflection_width": 0,
                "max_candidates": 5,
            },
            max_actions=2,
        )
        summary = github_repo_intelligence_summary(report)

        assert captured["repo_spec"] == "example/project"
        assert captured["checkout_repository_tests"] is True
        assert captured["run_repository_test_retry_prerequisites"] is True
        assert captured["auto_repository_test_retry"] is True
        assert captured["repository_patch_generation_mode"] == "hybrid"
        assert captured["repository_llm_patch_candidate_limit"] == 3
        assert captured["max_candidates"] == intelligence_module.DEFAULT_MAX_CANDIDATES

        assert summary["agent_auto_action_count"] == 1
        assert summary["agent_auto_stop_reason"] == (
            "phase_goal_reached:patch_validation_ready"
        )
        action = summary["agent_auto_actions"][0]
        assert action["action_id"] == "regenerate_safe_patch_candidates"
        assert action["repository_patch_generation_mode"] == "hybrid"
        assert action["repository_llm_patch_candidate_limit"] == 3
        assert action["after_patch_safety_gate_status"] == "pass"
        assert action["after_patch_validation_safety_blocked_candidate_count"] == 0
        assert action["after_repair_ready"] is True
        assert summary["agent_auto_trace"][0]["auto_executed"] is True
        assert summary["agent_auto_trace"][0]["plan_selected_action"] == (
            "regenerate_safe_patch_candidates"
        )
        assert summary["agent_auto_trace"][0][
            "verify_patch_validation_safety_blocked_candidate_count"
        ] == 0
        assert summary["agent_auto_trace"][1]["plan_selected_action"] == (
            "run_search_and_ablation_evaluation"
        )
        assert summary["agent_controller"]["selected_action"]["id"] == (
            "run_search_and_ablation_evaluation"
        )
        assert summary["agent_answers"]["repairability"]["status"] == (
            "repair_ready"
        )


def test_github_repo_intelligence_controller_runs_reflection_before_manual_expansion():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        onboarding_report = dict(report.onboarding_report or {})
        onboarding_report["repository_test_fault_localization"] = {
            "status": "pass",
            "reason": "localized_from_dynamic_evidence",
            "ranking_count": 1,
            "dynamic_evidence_level": "failing_tests",
            "top_function": "mean",
            "top_function_id": "average_mean.py::mean",
            "rankings": [
                {
                    "rank": 1,
                    "function_id": "average_mean.py::mean",
                    "function_name": "mean",
                    "file_path": "average_mean.py",
                    "score": 0.91,
                    "signals": {"dynamic_test_evidence": 1.0},
                }
            ],
        }
        report = replace(
            report,
            summary={
                **report.summary,
                "repository_test_patch_validation_status": "fail",
                "repository_test_patch_validation_reason": (
                    "no_candidate_passed_repository_tests"
                ),
                "repository_test_patch_validation_reflection_mode": "none",
                "repository_test_patch_validation_reflection_candidate_count": 0,
                "repository_test_patch_validation_successful_reflection_count": 0,
                "repository_test_patch_validation_max_depth": 0,
            },
            onboarding_report=onboarding_report,
        )

        summary = github_repo_intelligence_summary(report)

        reflection = summary["reflection_summary"]
        assert reflection["patch_validation_status"] == "fail"
        assert reflection["reflection_candidate_count"] == 0
        assert reflection["max_depth_executed"] == 0
        controller = summary["agent_controller"]
        assert controller["selected_action"]["id"] == "run_patch_reflection_loop"
        assert controller["selected_action"]["executable_now"] is True
        assert controller["verification"]["expected_artifact"] == (
            "reflection_trace.json"
        )
        assert "reflected candidates" in controller["verification"][
            "success_condition"
        ]


def test_github_repo_intelligence_auto_controller_runs_patch_reflection_loop(
    monkeypatch,
):
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        dynamic_localization = {
            "status": "pass",
            "reason": "localized_from_dynamic_evidence",
            "ranking_count": 1,
            "dynamic_evidence_level": "failing_tests",
            "top_function": "mean",
            "top_function_id": "average_mean.py::mean",
            "rankings": [
                {
                    "rank": 1,
                    "function_id": "average_mean.py::mean",
                    "function_name": "mean",
                    "file_path": "average_mean.py",
                    "score": 0.91,
                    "signals": {"dynamic_test_evidence": 1.0},
                }
            ],
        }
        onboarding_report = dict(report.onboarding_report or {})
        onboarding_report["repository_test_fault_localization"] = (
            dynamic_localization
        )
        report = replace(
            report,
            summary={
                **report.summary,
                "repository_test_patch_validation_status": "fail",
                "repository_test_patch_validation_reason": (
                    "no_candidate_passed_repository_tests"
                ),
                "repository_test_patch_validation_reflection_mode": "none",
                "repository_test_patch_validation_reflection_candidate_count": 0,
                "repository_test_patch_validation_successful_reflection_count": 0,
                "repository_test_patch_validation_max_depth": 0,
            },
            onboarding_report=onboarding_report,
        )
        captured = {}

        def fake_run_github_repo_agent(repo_spec, output_dir_arg, **kwargs):
            captured["repo_spec"] = repo_spec
            captured["output_dir"] = str(output_dir_arg)
            captured.update(kwargs)
            reflected_onboarding = dict(onboarding_report)
            reflected_onboarding["repository_test_fault_localization"] = (
                dynamic_localization
            )
            reflected_summary = {
                **report.summary,
                "repository_test_patch_validation_status": "fail",
                "repository_test_patch_validation_reason": (
                    "reflection_attempted_no_success"
                ),
                "repository_test_patch_validation_reflection_mode": "rule",
                "repository_test_patch_validation_reflection_candidate_count": 1,
                "repository_test_patch_validation_successful_reflection_count": 0,
                "repository_test_patch_validation_max_depth": 1,
                "repository_test_patch_validation_failure_type_counts": {
                    "test_failure": 1
                },
            }
            return replace(
                report,
                summary=reflected_summary,
                onboarding_report=reflected_onboarding,
            )

        monkeypatch.setattr(
            intelligence_module,
            "run_github_repo_agent",
            fake_run_github_repo_agent,
        )

        report = intelligence_module._run_auto_controller_actions(
            report,
            repo_spec="example/project",
            output_dir=output_dir,
            agent_kwargs={
                "repository_test_timeout": 20,
                "checkout_repository_tests": False,
                "run_repository_test_command": True,
                "run_repository_test_retry_prerequisites": False,
                "auto_repository_test_retry": False,
                "auto_repository_test_retry_max_risk": "low",
                "auto_repository_test_retry_allowed_runners": [],
                "repository_test_reflection_mode": "none",
                "repository_test_reflection_rounds": 0,
                "repository_test_reflection_width": 0,
            },
            max_actions=2,
        )
        summary = github_repo_intelligence_summary(report)

        assert captured["repo_spec"] == "example/project"
        assert captured["checkout_repository_tests"] is True
        assert captured["run_repository_test_retry_prerequisites"] is True
        assert captured["auto_repository_test_retry"] is True
        assert captured["repository_test_reflection_mode"] == "rule"
        assert captured["repository_test_reflection_rounds"] == 1
        assert captured["repository_test_reflection_width"] == 1
        assert captured["repository_test_timeout"] == 30

        assert summary["agent_auto_action_count"] == 1
        assert summary["agent_auto_actions"][0]["action_id"] == (
            "run_patch_reflection_loop"
        )
        assert summary["agent_auto_actions"][0][
            "repository_test_reflection_mode"
        ] == "rule"
        assert summary["agent_auto_actions"][0][
            "repository_test_reflection_rounds"
        ] == 1
        assert summary["agent_auto_actions"][0][
            "after_reflection_candidate_count"
        ] == 1
        assert summary["agent_auto_trace"][0]["auto_executed"] is True
        assert summary["agent_auto_trace"][0]["plan_selected_action"] == (
            "run_patch_reflection_loop"
        )
        assert summary["agent_auto_trace"][1]["auto_executed"] is False
        assert summary["agent_auto_trace"][1]["plan_selected_action"] == (
            "expand_patch_candidates_or_reflection"
        )
        assert summary["agent_auto_trace"][1]["stop_reason"] == (
            "selected_action_not_executable"
        )
        assert summary["agent_controller"]["selected_action"]["id"] == (
            "expand_patch_candidates_or_reflection"
        )


def test_github_repo_intelligence_controller_builds_dynamic_localization_from_traceback():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        report = replace(
            report,
            summary={
                **report.summary,
                "repository_test_dynamic_evidence_level": "traceback",
                "repository_test_dynamic_usable_for_localization": True,
                "repository_test_fault_localization_status": "skipped",
                "repository_test_fault_localization_reason": (
                    "repository_root_missing"
                ),
            },
        )

        summary = github_repo_intelligence_summary(report)

        readiness = summary["analysis_readiness"]
        assert readiness["current_stage"] == (
            "phase2_static_graph_fault_localization"
        )
        assert readiness["dynamic_evidence_level"] == "traceback"
        assert readiness["dynamic_evidence_usable_for_localization"] is True
        assert readiness["blocker"] == (
            "dynamic_fault_localization_not_ready:repository_root_missing"
        )
        assert readiness["next_action"] == (
            "Build repository_test_fault_localization from usable dynamic evidence "
            "or inspect unmatched traceback/nodeid evidence."
        )
        controller = summary["agent_controller"]
        assert controller["selected_action"]["id"] == (
            "build_dynamic_fault_localization"
        )
        assert controller["selected_action"]["tool"] == (
            "repository_test_fault_localization"
        )
        assert controller["selected_action"]["executable_now"] is True
        assert any(
            item["signal"] == "dynamic_evidence_usable_for_localization"
            and item["value"] == "True"
            for item in controller["observations"]
        )


def test_github_repo_intelligence_auto_controller_builds_dynamic_localization(
    monkeypatch,
):
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        report = replace(
            report,
            summary={
                **report.summary,
                "repository_test_dynamic_evidence_level": "traceback",
                "repository_test_dynamic_usable_for_localization": True,
                "repository_test_fault_localization_status": "skipped",
                "repository_test_fault_localization_reason": (
                    "repository_root_missing"
                ),
            },
        )
        captured = {}

        def fake_run_github_repo_agent(repo_spec, output_dir_arg, **kwargs):
            captured["repo_spec"] = repo_spec
            captured["output_dir"] = str(output_dir_arg)
            captured.update(kwargs)
            localized_onboarding = dict(report.onboarding_report or {})
            localized_onboarding["repository_test_fault_localization"] = {
                "status": "pass",
                "reason": "localized_from_dynamic_evidence",
                "ranking_count": 1,
                "matched_failed_test_count": 1,
                "unmatched_failed_test_count": 0,
                "dynamic_evidence_level": "traceback",
                "top_function": "mean",
                "top_function_id": "average_mean.py::mean",
                "top_score": 0.93,
                "rankings": [
                    {
                        "rank": 1,
                        "function_id": "average_mean.py::mean",
                        "function_name": "mean",
                        "file_path": "average_mean.py",
                        "start_line": 3,
                        "end_line": 7,
                        "score": 0.93,
                        "signals": {
                            "dynamic_test_evidence": 1.0,
                            "static": 1.0,
                            "graph": 0.8,
                            "sbfl": 1.0,
                        },
                    }
                ],
            }
            localized_summary = {
                **report.summary,
                "repository_test_dynamic_evidence_level": "traceback",
                "repository_test_dynamic_usable_for_localization": True,
                "repository_test_fault_localization_status": "pass",
                "repository_test_fault_localization_reason": (
                    "localized_from_dynamic_evidence"
                ),
                "repository_test_fault_localization_ranking_count": 1,
                "repository_test_fault_localization_top_function": "mean",
            }
            return replace(
                report,
                summary=localized_summary,
                onboarding_report=localized_onboarding,
            )

        monkeypatch.setattr(
            intelligence_module,
            "run_github_repo_agent",
            fake_run_github_repo_agent,
        )

        report = intelligence_module._run_auto_controller_actions(
            report,
            repo_spec="example/project",
            output_dir=output_dir,
            agent_kwargs={
                "repository_test_timeout": 20,
                "checkout_repository_tests": False,
                "run_repository_test_command": True,
                "run_repository_test_retry_prerequisites": False,
                "auto_repository_test_retry": False,
                "auto_repository_test_retry_max_risk": "low",
                "auto_repository_test_retry_allowed_runners": [],
                "repository_test_reflection_mode": "rule",
                "repository_test_reflection_rounds": 1,
                "repository_test_reflection_width": 1,
            },
            max_actions=1,
        )
        summary = github_repo_intelligence_summary(report)

        assert captured["repo_spec"] == "example/project"
        assert captured["checkout_repository_tests"] is True
        assert captured["run_repository_test_command"] is True
        assert captured["run_repository_test_retry_prerequisites"] is True
        assert captured["auto_repository_test_retry"] is True
        assert captured["auto_repository_test_retry_max_risk"] == "medium"
        assert captured["auto_repository_test_retry_allowed_runners"] == [
            "pytest",
            "unittest",
        ]
        assert captured["repository_test_timeout"] == 30

        assert summary["agent_auto_action_count"] == 1
        assert summary["agent_auto_actions"][0]["action_id"] == (
            "build_dynamic_fault_localization"
        )
        assert summary["agent_auto_actions"][0][
            "after_fault_localization_mode"
        ] == "dynamic"
        assert summary["agent_auto_actions"][0][
            "after_fault_localization_status"
        ] == "pass"
        assert summary["agent_auto_trace"][0]["auto_executed"] is True
        assert summary["agent_auto_trace"][0]["plan_selected_action"] == (
            "build_dynamic_fault_localization"
        )
        assert summary["agent_auto_trace"][0][
            "verify_fault_localization_mode"
        ] == "dynamic"
        assert summary["agent_auto_trace"][1]["auto_executed"] is False
        assert summary["agent_auto_trace"][1]["stop_reason"] == (
            "max_actions_reached"
        )
        assert summary["fault_localization"]["mode"] == "dynamic"
        assert summary["fault_localization"]["top_function"] == "mean"
        assert summary["analysis_readiness"]["current_stage"] == (
            "phase2_dynamic_fault_localization"
        )
        assert summary["agent_controller"]["selected_action"]["id"] == (
            "generate_and_validate_patches"
        )


def test_github_repo_intelligence_parser_accepts_phase3_execution_options():
    args = build_arg_parser().parse_args(
        [
            "example/project",
            "out",
            "--repository-test-root",
            "checkout",
            "--repository-test-timeout",
            "7",
            "--repository-test-failure-overlay-candidate-limit",
            "2",
            "--repository-test-reflection-mode",
            "none",
            "--repository-test-reflection-rounds",
            "3",
            "--repository-test-reflection-width",
            "4",
            "--patch-judge-mode",
            "llm",
            "--no-repository-test-command",
            "--run-repository-test-environment-setup",
            "--run-repository-test-retry",
            "--run-repository-test-retry-prerequisites",
            "--auto-repository-test-retry",
            "--auto-repository-test-retry-max-risk",
            "medium",
            "--auto-repository-test-retry-runner",
            "pytest",
            "--auto-repository-test-retry-runner",
            "unittest",
            "--repository-test-environment-setup-timeout",
            "9",
            "--checkout-repository-tests",
            "--repository-checkout-timeout",
            "11",
            "--repository-checkout-depth",
            "2",
        ]
    )

    assert args.repository_test_root == "checkout"
    assert args.repository_test_timeout == 7
    assert args.repository_test_failure_overlay_candidate_limit == 2
    assert args.repository_test_reflection_mode == "none"
    assert args.repository_test_reflection_rounds == 3
    assert args.repository_test_reflection_width == 4
    assert args.patch_judge_mode == "llm"
    assert args.no_repository_test_command is True
    assert args.run_repository_test_environment_setup is True
    assert args.run_repository_test_retry is True
    assert args.run_repository_test_retry_prerequisites is True
    assert args.auto_repository_test_retry is True
    assert args.auto_repository_test_retry_max_risk == "medium"
    assert args.auto_repository_test_retry_runner == ["pytest", "unittest"]
    assert args.repository_test_environment_setup_timeout == 9
    assert args.checkout_repository_tests is True
    assert args.repository_checkout_timeout == 11
    assert args.repository_checkout_depth == 2

    checkout_args = build_arg_parser().parse_args(
        ["example/project", "out", "--execution-profile", "checkout"]
    )
    assert checkout_args.execution_profile == "checkout"

    agent_auto_args = build_arg_parser().parse_args(
        [
            "example/project",
            "out",
            "--execution-profile",
            "agent-auto",
            "--auto-controller-max-actions",
            "3",
        ]
    )
    assert agent_auto_args.execution_profile == "agent-auto"
    assert agent_auto_args.auto_controller_actions is False
    assert agent_auto_args.auto_controller_max_actions == 3


def test_github_repo_intelligence_parser_allows_default_output_dir():
    args = build_arg_parser().parse_args(
        ["https://github.com/example/project", "--agent"]
    )

    assert args.repo == "https://github.com/example/project"
    assert args.output_dir is None
    assert args.agent is True
    assert str(
        intelligence_module._default_output_dir_for_repo(
            "https://github.com/example/project/tree/main",
            execution_profile="agent-auto",
        )
    ).replace("\\", "/") == "outputs/repo_intelligence_agent_example_project"
    assert str(
        intelligence_module._default_output_dir_for_repo(
            "example/project",
            execution_profile="checkout",
        )
    ).replace("\\", "/") == "outputs/repo_intelligence_checkout_example_project"


def test_github_repo_intelligence_cli_phase3_fast_profile(monkeypatch, capsys):
    captured = {}

    def fake_run(repo_spec, output_dir, **kwargs):
        captured.update(
            {
                "repo_spec": repo_spec,
                "output_dir": output_dir,
                **kwargs,
            }
        )
        return intelligence_module.GitHubRepoAgentReport(
            repo_spec=repo_spec,
            owner="example",
            repo="project",
            output_dir=str(output_dir),
            preset=str(kwargs.get("preset") or ""),
            status="pass",
            summary={},
            output_paths={},
            onboarding_report=None,
        )

    monkeypatch.setattr(
        intelligence_module,
        "run_github_repo_intelligence",
        fake_run,
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir) / "repo_intelligence"

        with pytest.raises(SystemExit) as exc_info:
            repo_intelligence_main(
                [
                    "example/project",
                    str(output_dir),
                    "--execution-profile",
                    "phase3-fast",
                    "--format",
                    "json",
                ],
            )

        printed = json.loads(capsys.readouterr().out)
        assert exc_info.value.code == 0
        assert printed["analysis_readiness"]["current_stage"] == (
            "source_import_blocked"
        )
        assert printed["agent_controller"]["control_loop"] == [
            "observe",
            "plan",
            "act",
            "verify",
            "reflect",
            "replan",
        ]
        assert printed["agent_controller"]["selected_action"]["id"] == (
            "adjust_source_filters"
        )
        assert captured["checkout_repository_tests"] is True
        assert captured["run_repository_test_retry_prerequisites"] is True
        assert captured["auto_repository_test_retry"] is True
        assert captured["auto_repository_test_retry_max_risk"] == "medium"
        assert captured["auto_repository_test_retry_allowed_runners"] == [
            "pytest",
            "unittest",
        ]
        assert captured["repository_test_timeout"] == 30


def test_github_repo_intelligence_cli_agent_auto_profile(monkeypatch, capsys):
    captured = {}

    def fake_run(repo_spec, output_dir, **kwargs):
        captured.update(
            {
                "repo_spec": repo_spec,
                "output_dir": output_dir,
                **kwargs,
            }
        )
        return intelligence_module.GitHubRepoAgentReport(
            repo_spec=repo_spec,
            owner="example",
            repo="project",
            output_dir=str(output_dir),
            preset=str(kwargs.get("preset") or ""),
            status="pass",
            summary={},
            output_paths={},
            onboarding_report=None,
        )

    monkeypatch.setattr(
        intelligence_module,
        "run_github_repo_intelligence",
        fake_run,
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir) / "repo_intelligence"

        with pytest.raises(SystemExit) as exc_info:
            repo_intelligence_main(
                [
                    "example/project",
                    str(output_dir),
                    "--execution-profile",
                    "agent-auto",
                    "--format",
                    "json",
                ],
            )

        printed = json.loads(capsys.readouterr().out)
        assert exc_info.value.code == 0
        assert printed["agent_controller"]["control_loop"] == [
            "observe",
            "plan",
            "act",
            "verify",
            "reflect",
            "replan",
        ]
        assert captured["auto_controller_actions"] is True
        assert captured["auto_controller_max_actions"] == 2
        assert captured["execution_profile"] == "agent-auto"
        assert captured["agent_shortcut"] is False
        assert captured["checkout_repository_tests"] is False
        assert captured["repository_test_timeout"] == 30


def test_github_repo_intelligence_cli_agent_shortcut(monkeypatch, capsys):
    captured = {}

    def fake_run(repo_spec, output_dir, **kwargs):
        captured.update(
            {
                "repo_spec": repo_spec,
                "output_dir": output_dir,
                **kwargs,
            }
        )
        return intelligence_module.GitHubRepoAgentReport(
            repo_spec=repo_spec,
            owner="example",
            repo="project",
            output_dir=str(output_dir),
            preset=str(kwargs.get("preset") or ""),
            status="pass",
            summary={},
            output_paths={},
            onboarding_report=None,
        )

    monkeypatch.setattr(
        intelligence_module,
        "run_github_repo_intelligence",
        fake_run,
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir) / "repo_intelligence"

        with pytest.raises(SystemExit) as exc_info:
            repo_intelligence_main(
                [
                    "https://github.com/example/project",
                    str(output_dir),
                    "--agent",
                    "--format",
                    "json",
                ],
            )

        printed = json.loads(capsys.readouterr().out)
        assert exc_info.value.code == 0
        assert printed["agent_controller"]["control_loop"] == [
            "observe",
            "plan",
            "act",
            "verify",
            "reflect",
            "replan",
        ]
        assert captured["repo_spec"] == "https://github.com/example/project"
        assert captured["execution_profile"] == "agent-auto"
        assert captured["agent_shortcut"] is True
        assert captured["auto_controller_actions"] is True
        assert captured["auto_controller_max_actions"] == 4
        assert captured["repository_test_timeout"] == 30
        assert captured["source_cache_dir"] == str(output_dir / "source_cache")


def test_github_repo_intelligence_cli_agent_shortcut_defaults_output_dir(
    monkeypatch,
    capsys,
):
    captured = {}

    def fake_run(repo_spec, output_dir, **kwargs):
        captured.update(
            {
                "repo_spec": repo_spec,
                "output_dir": output_dir,
                **kwargs,
            }
        )
        return intelligence_module.GitHubRepoAgentReport(
            repo_spec=repo_spec,
            owner="example",
            repo="project",
            output_dir=str(output_dir),
            preset=str(kwargs.get("preset") or ""),
            status="pass",
            summary={},
            output_paths={},
            onboarding_report=None,
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        with monkeypatch.context() as scoped_monkeypatch:
            scoped_monkeypatch.setattr(
                intelligence_module,
                "run_github_repo_intelligence",
                fake_run,
            )
            scoped_monkeypatch.chdir(root)

            with pytest.raises(SystemExit) as exc_info:
                repo_intelligence_main(
                    [
                        "https://github.com/example/project",
                        "--agent",
                        "--format",
                        "json",
                    ],
                )

            capsys.readouterr()
        expected_output_dir = Path("outputs") / "repo_intelligence_agent_example_project"
        assert exc_info.value.code == 0
        assert captured["repo_spec"] == "https://github.com/example/project"
        assert captured["output_dir"] == str(expected_output_dir)
        assert captured["execution_profile"] == "agent-auto"
        assert captured["agent_shortcut"] is True
        assert captured["auto_controller_actions"] is True
        assert captured["auto_controller_max_actions"] == 4
        assert captured["source_cache_dir"] == str(
            expected_output_dir / "source_cache"
        )


def test_github_repo_intelligence_cli_agent_preserves_explicit_source_cache(
    monkeypatch,
    capsys,
):
    captured = {}

    def fake_run(repo_spec, output_dir, **kwargs):
        captured.update(
            {
                "repo_spec": repo_spec,
                "output_dir": output_dir,
                **kwargs,
            }
        )
        return intelligence_module.GitHubRepoAgentReport(
            repo_spec=repo_spec,
            owner="example",
            repo="project",
            output_dir=str(output_dir),
            preset=str(kwargs.get("preset") or ""),
            status="pass",
            summary={},
            output_paths={},
            onboarding_report=None,
        )

    monkeypatch.setattr(
        intelligence_module,
        "run_github_repo_intelligence",
        fake_run,
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        explicit_cache = root / "shared_source_cache"

        with pytest.raises(SystemExit) as exc_info:
            repo_intelligence_main(
                [
                    "example/project",
                    str(root / "repo_intelligence"),
                    "--agent",
                    "--source-cache-dir",
                    str(explicit_cache),
                    "--format",
                    "json",
                ],
            )

        capsys.readouterr()
        assert exc_info.value.code == 0
        assert captured["source_cache_dir"] == str(explicit_cache)


def test_github_repo_intelligence_cli_profiles_preserve_explicit_timeout(
    monkeypatch,
    capsys,
):
    captured_timeouts = []
    captured_caches = []

    def fake_run(repo_spec, output_dir, **kwargs):
        captured_timeouts.append(kwargs.get("repository_test_timeout"))
        captured_caches.append(kwargs.get("source_cache_dir"))
        return intelligence_module.GitHubRepoAgentReport(
            repo_spec=repo_spec,
            owner="example",
            repo="project",
            output_dir=str(output_dir),
            preset=str(kwargs.get("preset") or ""),
            status="pass",
            summary={},
            output_paths={},
            onboarding_report=None,
        )

    monkeypatch.setattr(
        intelligence_module,
        "run_github_repo_intelligence",
        fake_run,
    )
    cases = [
        ["--execution-profile", "phase3-fast", "--repository-test-timeout", "20"],
        ["--execution-profile", "agent-auto", "--repository-test-timeout=20"],
        ["--agent", "--repository-test-timeout", "7"],
    ]
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        for index, extra_args in enumerate(cases):
            with pytest.raises(SystemExit) as exc_info:
                repo_intelligence_main(
                    [
                        "example/project",
                        str(root / f"repo_intelligence_{index}"),
                        *extra_args,
                        "--format",
                        "json",
                    ],
                )
            assert exc_info.value.code == 0
            capsys.readouterr()

    assert captured_timeouts == [20, 20, 7]
    assert captured_caches[0] is None
    assert captured_caches[1] == str(root / "repo_intelligence_1" / "source_cache")
    assert captured_caches[2] == str(root / "repo_intelligence_2" / "source_cache")


def test_github_repo_intelligence_forwards_phase3_execution_options(monkeypatch):
    captured = {}

    def fake_repo_agent(repo_spec, output_dir, **kwargs):
        captured.update(
            {
                "repo_spec": repo_spec,
                "output_dir": output_dir,
                **kwargs,
            }
        )
        return intelligence_module.GitHubRepoAgentReport(
            repo_spec=repo_spec,
            owner="example",
            repo="project",
            output_dir=str(output_dir),
            preset=str(kwargs.get("preset") or ""),
            status="pass",
            summary={},
            output_paths={},
            onboarding_report=None,
        )

    monkeypatch.setattr(
        intelligence_module,
        "run_github_repo_agent",
        fake_repo_agent,
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        output_dir = root / "repo_intelligence"
        repository_root = root / "checkout"

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            repository_test_root=repository_root,
            repository_test_timeout=7,
            repository_test_failure_overlay_candidate_limit=2,
            repository_test_reflection_mode="none",
            repository_test_reflection_rounds=3,
            repository_test_reflection_width=4,
            patch_judge_mode="llm",
            run_repository_test_command=False,
            run_repository_test_environment_setup=True,
            run_repository_test_retry=True,
            run_repository_test_retry_prerequisites=True,
            auto_repository_test_retry=True,
            auto_repository_test_retry_max_risk="medium",
            auto_repository_test_retry_allowed_runners=["pytest", "unittest"],
            repository_test_environment_setup_timeout=9,
            checkout_repository_tests=True,
            repository_checkout_timeout=11,
            repository_checkout_depth=2,
        )

        assert report.output_dir == str(output_dir)
        assert captured["repo_spec"] == "example/project"
        assert captured["output_dir"] == output_dir
        assert captured["repository_test_root"] == repository_root
        assert captured["repository_test_timeout"] == 7
        assert captured["repository_test_failure_overlay_candidate_limit"] == 2
        assert captured["repository_test_reflection_mode"] == "none"
        assert captured["repository_test_reflection_rounds"] == 3
        assert captured["repository_test_reflection_width"] == 4
        assert captured["patch_judge_mode"] == "llm"
        assert captured["run_repository_test_command"] is False
        assert captured["run_repository_test_environment_setup"] is True
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


def test_github_repo_intelligence_cli_writes_blocked_artifacts_without_python_sources(
    capsys,
):
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        output_dir = root / "repo_intelligence"
        output_summary = root / "blocked_intelligence_summary.json"
        opener = _FakeOpener(_repo_payloads_without_python())

        with pytest.raises(SystemExit) as exc_info:
            repo_intelligence_main(
                [
                    "example/project",
                    str(output_dir),
                    "--format",
                    "json",
                    "--output-summary",
                    str(output_summary),
                    "--require-analysis-ready",
                ],
                opener=opener,
            )

        printed = json.loads(capsys.readouterr().out)
        saved = json.loads(output_summary.read_text(encoding="utf-8"))
        default_json = json.loads(
            (output_dir / "github_repo_intelligence.json").read_text(
                encoding="utf-8"
            )
        )
        default_markdown = (
            output_dir / "github_repo_intelligence.md"
        ).read_text(encoding="utf-8")

        assert exc_info.value.code == 1
        assert saved == printed
        assert default_json == printed
        assert printed["status"] == "pass"
        assert printed["status_reason"] == "source_import_blocked_report_ready"
        assert printed["status_source"] == "analysis_readiness"
        assert printed["upstream_agent_status"] == "fail"
        assert printed["static_intelligence_status"] == "blocked"
        assert printed["static_intelligence_reason"] == "no_imported_sources"
        assert printed["imported_source_count"] == 0
        assert printed["selected_source_count"] == 0
        readiness = printed["analysis_readiness"]
        assert readiness["current_stage"] == "source_import_blocked"
        assert readiness["stage_number"] == 0
        assert readiness["blocker"] == "source_import_or_parse_missing"
        assert readiness["next_action"] == (
            "Adjust include/exclude filters or target a Python package path."
        )
        assert readiness["can_generate_static_report"] is False
        assert readiness["can_attempt_dynamic_tests"] is False
        assert readiness["can_attempt_patch_repair"] is False
        controller = printed["agent_controller"]
        assert controller["selected_action"]["id"] == "adjust_source_filters"
        assert controller["selected_action"]["executable_now"] is True
        assert controller["control_loop"] == [
            "observe",
            "plan",
            "act",
            "verify",
            "reflect",
            "replan",
        ]
        answers = printed["agent_answers"]
        assert "Analyzed 0 Python files" in (
            answers["repository_structure_answer"]
        )
        assert answers["top_suspicious_functions"] == []
        assert answers["testability"]["status"] == "blocked"
        assert answers["repairability"]["status"] == "not_ready"
        assert answers["blocker"] == "source_import_or_parse_missing"
        assert answers["selected_controller_action"] == "adjust_source_filters"
        assert "Top suspicious function: none" in answers["executive_summary"]
        assert "Blocker: source_import_or_parse_missing" in (
            answers["executive_summary"]
        )
        inventory = printed["artifact_inventory"]
        assert inventory["status"] == "pass"
        assert inventory["reason"] == "core_artifacts_written"
        assert inventory["file_check_enabled"] is True
        assert inventory["missing_core_artifacts"] == []
        core_artifacts = inventory["groups"]["core"]
        assert all(item["file_checked"] is True for item in core_artifacts)
        assert all(item["file_exists"] is True for item in core_artifacts)
        assert all(item["file_nonempty"] is True for item in core_artifacts)
        assert all(item["file_size_bytes"] > 0 for item in core_artifacts)
        assert (output_dir / "github_repo_agent_controller.json").exists()
        invocation = json.loads(
            (output_dir / "agent_invocation.json").read_text(encoding="utf-8")
        )
        assert invocation["output_dir_defaulted"] is False
        assert invocation["default_output_dir"] == ""
        assert (output_dir / "agent_goal_readiness.json").exists()
        assert (output_dir / "final_report.json").exists()
        assert (output_dir / "final_report.md").exists()
        assert (output_dir / "repository_structure.json").exists()
        assert (output_dir / "repo_graph.json").exists()
        assert (output_dir / "fault_localization.json").exists()
        assert (output_dir / "analysis_readiness.json").exists()
        assert (output_dir / "artifact_inventory.json").exists()
        assert printed["final_report"]["blocker"] == "source_import_or_parse_missing"
        assert printed["final_report"]["controller"]["selected_action"] == (
            "adjust_source_filters"
        )
        assert printed["final_report"]["verification"][
            "answer_coverage_complete"
        ] is True
        assert "source_import_blocked" in default_markdown
        assert "adjust_source_filters" in default_markdown
        assert "## Final Auditable Report" in default_markdown
        assert "File Check Enabled: true" in default_markdown
        assert printed["acceptance_gate"]["status"] == "pass"
        assert printed["acceptance_gate"]["failed_checks"] == []
        assert "Acceptance Gate: `pass`" in default_markdown


def test_github_repo_intelligence_cli_agent_default_output_dir_writes_core_artifacts(
    monkeypatch,
    capsys,
):
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        opener = _FakeOpener(_repo_payloads_without_python())
        with monkeypatch.context() as scoped_monkeypatch:
            scoped_monkeypatch.chdir(root)

            with pytest.raises(SystemExit) as exc_info:
                repo_intelligence_main(
                    [
                        "example/project",
                        "--agent",
                        "--format",
                        "json",
                    ],
                    opener=opener,
                )

            printed = json.loads(capsys.readouterr().out)

        output_dir = root / "outputs" / "repo_intelligence_agent_example_project"
        saved = json.loads(
            (output_dir / "github_repo_intelligence.json").read_text(
                encoding="utf-8"
            )
        )
        invocation = json.loads(
            (output_dir / "agent_invocation.json").read_text(encoding="utf-8")
        )

        assert exc_info.value.code == 0
        assert saved == printed
        assert invocation["agent_mode"] is True
        assert invocation["agent_shortcut"] is True
        assert invocation["effective_execution_profile"] == "agent-auto"
        assert invocation["output_dir_defaulted"] is True
        assert invocation["default_output_dir"] == str(
            Path("outputs") / "repo_intelligence_agent_example_project"
        )
        assert invocation["source_cache_dir"] == str(
            Path("outputs")
            / "repo_intelligence_agent_example_project"
            / "source_cache"
        )
        assert printed["agent_controller"]["control_loop"] == [
            "observe",
            "plan",
            "act",
            "verify",
            "reflect",
            "replan",
        ]
        assert printed["acceptance_gate"]["status"] == "pass"
        criteria = {
            item["name"]: item for item in printed["agent_goal_readiness"]["criteria"]
        }
        one_command = criteria["one_command_input"]
        assert one_command["passed"] is True
        assert "output_dir_defaulted=true" in one_command["evidence"]
        assert "default_output_dir=" in one_command["evidence"]
        assert "repo_intelligence_agent_example_project" in one_command["evidence"]
        compliance_sections = {
            item["id"]: item
            for item in printed["final_report"]["objective_compliance"]["sections"]
        }
        github_input_section = compliance_sections[
            "github_input_checkout_and_cache"
        ]
        github_criteria = {
            item["name"]: item for item in github_input_section["criteria"]
        }
        assert "output_dir_defaulted=true" in github_criteria[
            "one_command_input"
        ]["evidence"]
        assert "output-defaulted=true" in (
            output_dir / "github_repo_intelligence.md"
        ).read_text(encoding="utf-8")
        expected_artifacts = [
            "github_repo_intelligence.json",
            "github_repo_intelligence.md",
            "github_repo_agent_controller.json",
            "github_repo_agent_controller.md",
            "repository_structure.json",
            "repository_structure.md",
            "repo_graph.json",
            "repo_graph.md",
            "fault_localization.json",
            "fault_localization.md",
            "analysis_readiness.json",
            "analysis_readiness.md",
        ]
        for name in expected_artifacts:
            path = output_dir / name
            assert path.is_file()
            assert path.stat().st_size > 0


def test_github_repo_intelligence_writes_rate_limit_blocker_report():
    class RateLimitOpener:
        def __init__(self):
            self.urls = []

        def __call__(self, request, timeout):
            del timeout
            self.urls.append(request.full_url)
            raise GitHubAPIError(
                "rate limit exceeded",
                status_code=403,
                url=request.full_url,
                rate_limit_remaining="0",
                rate_limit_reset="1729",
                response_body="API rate limit exceeded",
            )

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        output_dir = root / "repo_intelligence"
        opener = RateLimitOpener()

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            opener=opener,
            auto_controller_actions=True,
            auto_controller_max_actions=2,
        )
        summary = github_repo_intelligence_summary(report)

        assert report.status == "fail"
        assert (output_dir / "github_repo_agent.json").exists()
        assert summary["github_error"]["status_code"] == 403
        assert summary["github_error"]["rate_limit_remaining"] == "0"
        assert "GITHUB_TOKEN" in "\n".join(summary["github_error_next_actions"])
        assert summary["analysis_readiness"]["current_stage"] == (
            "source_import_blocked"
        )
        assert summary["analysis_readiness"]["blocker"] == (
            "github_fetch:github_api_error"
        )
        controller = summary["agent_controller"]
        assert controller["status"] == "blocked"
        assert controller["selected_action"]["id"] == (
            "retry_with_github_token_or_cache"
        )
        assert controller["selected_action"]["executable_now"] is False
        assert "GITHUB_TOKEN" in controller["selected_action"]["command"]
        assert report.summary["agent_auto_trace"][0]["stop_reason"] == (
            "selected_action_not_executable"
        )

        write_github_repo_intelligence_artifacts(report, summary)
        saved = json.loads(
            (output_dir / "github_repo_intelligence.json").read_text(
                encoding="utf-8"
            )
        )
        markdown = (output_dir / "github_repo_intelligence.md").read_text(
            encoding="utf-8"
        )
        controller_markdown = (
            output_dir / "github_repo_agent_controller.md"
        ).read_text(encoding="utf-8")

        assert saved["status"] == "pass"
        assert saved["status_reason"] == "source_import_blocked_report_ready"
        assert saved["github_error"]["rate_limit_reset"] == "1729"
        assert saved["agent_controller"]["selected_action"]["id"] == (
            "retry_with_github_token_or_cache"
        )
        assert saved["final_report"]["blocker"] == "github_fetch:github_api_error"
        assert saved["final_report"]["controller"]["selected_action"] == (
            "retry_with_github_token_or_cache"
        )
        assert "## GitHub Fetch Error" in markdown
        assert "## Final Auditable Report" in markdown
        assert "retry_with_github_token_or_cache" in controller_markdown


def test_github_repo_intelligence_cli_writes_json_summary(capsys):
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        output_summary = root / "intelligence_summary.json"
        opener = _FakeOpener(_repo_payloads(raw_source))

        with pytest.raises(SystemExit) as exc_info:
            repo_intelligence_main(
                [
                    "example/project",
                    str(output_dir),
                    "--recipe",
                    "missing_len_zero_guard",
                    "--format",
                    "json",
                    "--output-summary",
                    str(output_summary),
                    "--require-analysis-ready",
                ],
                opener=opener,
            )
        printed = json.loads(capsys.readouterr().out)
        saved = json.loads(output_summary.read_text(encoding="utf-8"))
        default_json = json.loads(
            (output_dir / "github_repo_intelligence.json").read_text(
                encoding="utf-8"
            )
        )
        default_markdown = (
            output_dir / "github_repo_intelligence.md"
        ).read_text(encoding="utf-8")
        controller_json = json.loads(
            (output_dir / "github_repo_agent_controller.json").read_text(
                encoding="utf-8"
            )
        )

        assert exc_info.value.code == 0
        assert printed["static_intelligence_status"] == "analysis_ready"
        assert saved == printed
        assert default_json == printed
        assert controller_json == saved["agent_controller"]
        assert (output_dir / "github_repo_agent_controller.md").exists()
        assert (output_dir / "agent_invocation.json").exists()
        assert (output_dir / "agent_invocation.md").exists()
        assert (output_dir / "agent_goal_readiness.json").exists()
        assert (output_dir / "agent_goal_readiness.md").exists()
        assert (output_dir / "agent_decision_timeline.json").exists()
        assert (output_dir / "agent_decision_timeline.md").exists()
        assert (output_dir / "repository_structure.json").exists()
        assert (output_dir / "repository_structure.md").exists()
        assert (output_dir / "repo_graph.json").exists()
        assert (output_dir / "repo_graph.md").exists()
        assert (output_dir / "fault_localization.json").exists()
        assert (output_dir / "fault_localization.md").exists()
        assert (output_dir / "analysis_readiness.json").exists()
        assert (output_dir / "analysis_readiness.md").exists()
        assert (output_dir / "phase4_search_evaluation.json").exists()
        assert (output_dir / "phase4_search_evaluation.md").exists()
        assert (output_dir / "artifact_inventory.json").exists()
        assert (output_dir / "artifact_inventory.md").exists()
        assert (output_dir / "repository_test_environment.json").exists()
        assert (output_dir / "repository_test_environment.md").exists()
        assert (output_dir / "repository_test_execution_plan.json").exists()
        assert (output_dir / "repository_test_execution_plan.md").exists()
        assert (output_dir / "repository_test_execution_result.json").exists()
        assert (output_dir / "repository_test_execution_result.md").exists()
        assert (output_dir / "repository_test_dynamic_evidence.json").exists()
        assert (output_dir / "repository_test_dynamic_evidence.md").exists()
        assert (output_dir / "repository_test_patch_candidates.json").exists()
        assert (output_dir / "repository_test_patch_candidates.md").exists()
        assert (output_dir / "repository_test_patch_validation.json").exists()
        assert (output_dir / "repository_test_patch_validation.md").exists()
        assert (output_dir / "reflection_trace.json").exists()
        assert (output_dir / "reflection_trace.md").exists()
        assert saved["preset"] == "mining"
        assert saved["selected_signal_count"] == 1
        assert saved["repository_structure"]["analyzed_file_count"] == 2
        assert saved["repository_structure"]["function_count"] == 2
        assert saved["repository_structure"]["test_structure"][
            "test_command_runner_counts"
        ] == {"pytest": 1}
        assert saved["repository_structure"]["test_structure"][
            "test_command_candidates"
        ][0]["runner"] == "pytest"
        assert saved["repo_graph"]["file_dependency_edge_count"] == 1
        assert saved["repo_graph"]["function_call_edge_count"] == 1
        assert saved["static_fault_localization"]["top_function"] == "mean"
        assert saved["static_fault_localization"]["rankings"][0][
            "final_score"
        ] == 1.0
        assert saved["fault_localization"]["mode"] == "static_fallback"
        assert saved["fault_localization"]["top_function"] == "mean"
        assert saved["fault_localization"]["rankings"][0]["final_score"] == 1.0
        assert saved["analysis_readiness"]["stage_number"] == 2
        assert saved["analysis_readiness"]["next_stage"] == (
            "phase3_repository_test_execution"
        )
        assert saved["agent_controller"]["selected_action"]["id"] == (
            "run_repository_tests_with_checkout"
        )
        assert [step["phase"] for step in saved["agent_controller"]["decision_trace"]] == [
            "observe",
            "plan",
            "act",
            "verify",
            "reflect",
            "replan",
        ]
        assert saved["intelligence_json"].endswith("github_repo_intelligence.json")
        assert saved["intelligence_markdown"].endswith("github_repo_intelligence.md")
        assert saved["agent_controller_json"].endswith(
            "github_repo_agent_controller.json"
        )
        assert saved["agent_controller_markdown"].endswith(
            "github_repo_agent_controller.md"
        )
        assert saved["agent_invocation_json"].endswith("agent_invocation.json")
        assert saved["agent_invocation_markdown"].endswith("agent_invocation.md")
        assert saved["agent_goal_readiness_json"].endswith(
            "agent_goal_readiness.json"
        )
        assert saved["agent_goal_readiness_markdown"].endswith(
            "agent_goal_readiness.md"
        )
        assert json.loads(
            (output_dir / "agent_invocation.json").read_text(encoding="utf-8")
        ) == saved["agent_invocation"]
        assert json.loads(
            (output_dir / "agent_goal_readiness.json").read_text(
                encoding="utf-8"
            )
        ) == saved["agent_goal_readiness"]
        assert saved["agent_decision_timeline_json"].endswith(
            "agent_decision_timeline.json"
        )
        assert saved["agent_decision_timeline_markdown"].endswith(
            "agent_decision_timeline.md"
        )
        assert saved["agent_decision_timeline"]["status"] == "pass"
        assert saved["agent_decision_timeline"]["source"] == "agent_controller"
        assert saved["agent_decision_timeline"]["step_count"] == 1
        assert saved["agent_decision_timeline"]["complete_step_count"] == 1
        assert saved["final_report_json"].endswith("final_report.json")
        assert saved["final_report_markdown"].endswith("final_report.md")
        assert json.loads(
            (output_dir / "final_report.json").read_text(encoding="utf-8")
        ) == saved["final_report"]
        assert saved["final_report"]["top_suspicious_function"] == "mean"
        compliance = saved["final_report"]["objective_compliance"]
        assert compliance["status"] == "pass"
        assert compliance["passed"] is True
        assert compliance["passed_section_count"] == compliance["section_count"]
        sections = {item["id"]: item for item in compliance["sections"]}
        assert sections["agent_controller_and_auditable_reports"]["passed"] is True
        assert sections["patch_validation_and_reflection"]["passed"] is True
        legacy_saved = json.loads(json.dumps(saved))
        legacy_saved["final_report"].pop("objective_compliance", None)
        refreshed_legacy = (
            intelligence_module.refresh_github_repo_intelligence_summary_status(
                legacy_saved
            )
        )
        assert refreshed_legacy["final_report"]["objective_compliance"][
            "status"
        ] == "pass"
        assert refreshed_legacy["final_report"]["objective_compliance"][
            "passed"
        ] is True
        assert saved["final_report"]["verification"]["acceptance_gate_status"] == (
            "pass"
        )
        assert saved["repository_structure_json"].endswith(
            "repository_structure.json"
        )
        assert saved["repo_graph_json"].endswith("repo_graph.json")
        assert saved["fault_localization_json"].endswith("fault_localization.json")
        assert saved["analysis_readiness_json"].endswith("analysis_readiness.json")
        assert saved["phase4_search_evaluation_json"].endswith(
            "phase4_search_evaluation.json"
        )
        assert saved["phase4_search_evaluation_markdown"].endswith(
            "phase4_search_evaluation.md"
        )
        assert saved["artifact_inventory_json"].endswith("artifact_inventory.json")
        assert saved["artifact_inventory_markdown"].endswith("artifact_inventory.md")
        assert saved["artifact_inventory"]["status"] == "pass"
        assert saved["artifact_inventory"]["reason"] == "core_artifacts_written"
        assert saved["artifact_inventory"]["file_check_enabled"] is True
        assert saved["artifact_inventory"]["missing_core_artifacts"] == []
        core_artifacts = saved["artifact_inventory"]["groups"]["core"]
        assert all(item["file_checked"] is True for item in core_artifacts)
        assert all(item["file_exists"] is True for item in core_artifacts)
        assert all(item["file_nonempty"] is True for item in core_artifacts)
        assert all(item["file_size_bytes"] > 0 for item in core_artifacts)
        core_names = [item["name"] for item in core_artifacts]
        assert "agent_invocation.json" in core_names
        assert "agent_invocation.md" in core_names
        assert "agent_goal_readiness.json" in core_names
        assert "agent_goal_readiness.md" in core_names
        assert "final_report.json" in core_names
        assert "final_report.md" in core_names
        assert saved["artifact_inventory"]["groups"]["core"][0]["name"] == (
            "github_repo_intelligence.json"
        )
        assert saved["acceptance_gate"]["status"] == "pass"
        assert saved["acceptance_gate"]["passed"] is True
        assert saved["acceptance_gate"]["failed_checks"] == []
        assert "Acceptance Gate: `pass`" in default_markdown
        assert saved["agent_answers"]["artifact_inventory"]["status"] == "pass"
        assert "written and verified" in (
            saved["agent_answers"]["artifact_inventory_answer"]
        )
        assert saved["repository_test_environment_json"].endswith(
            "repository_test_environment.json"
        )
        assert saved["repository_test_execution_plan_json"].endswith(
            "repository_test_execution_plan.json"
        )
        assert saved["repository_test_execution_result_json"].endswith(
            "repository_test_execution_result.json"
        )
        assert saved["repository_test_dynamic_evidence_json"].endswith(
            "repository_test_dynamic_evidence.json"
        )
        assert saved["repository_test_patch_candidates_json"].endswith(
            "repository_test_patch_candidates.json"
        )
        assert saved["repository_test_patch_validation_json"].endswith(
            "repository_test_patch_validation.json"
        )
        assert saved["repository_patch_generation_mode"] == "rule"
        assert saved["repository_patch_generator_counts"] == {"rule": 0, "llm": 0}
        assert saved["repository_llm_patch_generation_status"] == "disabled"
        assert saved["repository_llm_patch_generation_reason"] == (
            "patch_generation_mode_rule"
        )
        assert saved["repository_patch_safety_gate_status"] == "skipped"
        assert saved["repository_patch_safety_gate_blocked_count"] == 0
        assert saved["reflection_trace_json"].endswith("reflection_trace.json")
        assert saved["reflection_trace_markdown"].endswith("reflection_trace.md")
        assert "GitHub Repository Intelligence Summary" in default_markdown
        assert "## Analysis Readiness" in default_markdown
        assert "## Agent Controller" in default_markdown
        assert "### Phase 4 Search Evaluation" in default_markdown
        assert "## Final Auditable Report" in default_markdown
        assert "### Objective Compliance" in default_markdown
        assert "agent_controller_and_auditable_reports" in default_markdown
        assert "### Artifact Inventory" in default_markdown
        assert "File Check Enabled: true" in default_markdown
        assert "Required Coverage:" in default_markdown
        assert "Missing Required Artifacts:" in default_markdown
        assert "## Fault Localization" in default_markdown
        assert "Artifact Inventory JSON" in default_markdown
        assert "Final Report JSON" in default_markdown
        assert "Phase 4 Search Evaluation JSON" in default_markdown
        assert "Repository Test Environment JSON" in default_markdown
        assert "## Patch Generation Audit" in default_markdown
        assert "Patch Generation Mode: `rule`" in default_markdown
        assert "LLM Generation: `disabled`/`patch_generation_mode_rule`" in (
            default_markdown
        )
        assert "Safety Gate: `skipped` (blocked=0)" in default_markdown
        assert "Repository Test Patch Validation JSON" in default_markdown
        assert "Reflection Trace JSON" in default_markdown
        assert saved["repository_structure"]["top_complexity_functions"][0][
            "cyclomatic_complexity"
        ] == 2
        assert saved["source_mining_markdown"].endswith("source_mining.md")


def test_artifact_inventory_flags_missing_current_stage_required_artifacts(tmp_path):
    def write_artifact(name: str) -> str:
        path = tmp_path / name
        path.write_text("{}", encoding="utf-8")
        return str(path)

    payload = {
        "intelligence_json": write_artifact("github_repo_intelligence.json"),
        "intelligence_markdown": write_artifact("github_repo_intelligence.md"),
        "agent_controller_json": write_artifact("github_repo_agent_controller.json"),
        "agent_controller_markdown": write_artifact("github_repo_agent_controller.md"),
        "agent_action_registry_json": write_artifact("agent_action_registry.json"),
        "agent_action_registry_markdown": write_artifact("agent_action_registry.md"),
        "agent_policy_trace_json": write_artifact("agent_policy_trace.json"),
        "agent_policy_trace_markdown": write_artifact("agent_policy_trace.md"),
        "agent_invocation_json": write_artifact("agent_invocation.json"),
        "agent_invocation_markdown": write_artifact("agent_invocation.md"),
        "agent_goal_readiness_json": write_artifact("agent_goal_readiness.json"),
        "agent_goal_readiness_markdown": write_artifact("agent_goal_readiness.md"),
        "agent_decision_timeline_json": write_artifact("agent_decision_timeline.json"),
        "agent_decision_timeline_markdown": write_artifact("agent_decision_timeline.md"),
        "final_report_json": write_artifact("final_report.json"),
        "final_report_markdown": write_artifact("final_report.md"),
        "repository_structure_json": write_artifact("repository_structure.json"),
        "repository_structure_markdown": write_artifact("repository_structure.md"),
        "repository_test_discovery_json": write_artifact(
            "repository_test_discovery.json"
        ),
        "repository_test_discovery_markdown": write_artifact(
            "repository_test_discovery.md"
        ),
        "repo_graph_json": write_artifact("repo_graph.json"),
        "repo_graph_markdown": write_artifact("repo_graph.md"),
        "fault_localization_json": write_artifact("fault_localization.json"),
        "fault_localization_markdown": write_artifact("fault_localization.md"),
        "analysis_readiness_json": write_artifact("analysis_readiness.json"),
        "analysis_readiness_markdown": write_artifact("analysis_readiness.md"),
        "repository_test_patch_candidates_json": write_artifact(
            "repository_test_patch_candidates.json"
        ),
        "repository_test_patch_candidates_markdown": write_artifact(
            "repository_test_patch_candidates.md"
        ),
        "repository_test_patch_validation_json": write_artifact(
            "repository_test_patch_validation.json"
        ),
        "repository_test_patch_validation_markdown": write_artifact(
            "repository_test_patch_validation.md"
        ),
        "reflection_trace_json": str(tmp_path / "missing_reflection_trace.json"),
        "reflection_trace_markdown": str(tmp_path / "missing_reflection_trace.md"),
        "repository_test_patch_candidates_status": "pass",
        "repository_test_patch_validation_status": "pass",
        "repository_test_patch_validation_executed_count": 2,
        "analysis_readiness": {
            "current_stage": "phase3_patch_validation",
            "next_stage": "phase3_patch_reflection_or_expansion",
        },
    }

    inventory = intelligence_module._artifact_inventory_summary(
        payload,
        check_files=True,
    )
    answer = intelligence_module._agent_answer_artifact_audit(inventory)
    acceptance_payload = {
        **payload,
        "artifact_inventory": inventory,
        "agent_controller": {
            "control_loop": [
                "observe",
                "plan",
                "act",
                "verify",
                "reflect",
                "replan",
            ],
            "selected_action": {"id": "run_patch_reflection_loop"},
        },
        "agent_decision_timeline": {
            "status": "pass",
            "step_count": 1,
            "complete_step_count": 1,
        },
        "repository_structure": {"analyzed_file_count": 1},
        "fault_localization": {"rankings": [{"rank": 1}]},
        "agent_answers": {
            "answer_coverage": {
                "complete": True,
                "answered_question_count": 7,
                "required_question_count": 7,
            }
        },
        "repository_test_repair_ready": False,
        "repository_test_patch_validation_success_count": 0,
    }
    gate = intelligence_module._acceptance_gate_summary(acceptance_payload)

    assert inventory["status"] == "warning"
    assert inventory["reason"] == "required_artifacts_missing"
    assert inventory["missing_core_artifacts"] == []
    assert inventory["missing_required_artifacts"] == [
        "reflection_trace.json",
        "reflection_trace.md",
    ]
    assert inventory["required_count"] == 32
    assert inventory["required_available_count"] == 30
    repair_rows = {item["name"]: item for item in inventory["groups"]["repair"]}
    assert repair_rows["reflection_trace.json"]["required_now"] is True
    assert repair_rows["reflection_trace.json"]["available"] is False
    assert answer["core_ready"] is True
    assert answer["required_ready"] is False
    assert answer["missing_required_artifacts"] == [
        "reflection_trace.json",
        "reflection_trace.md",
    ]
    assert gate["status"] == "warning"
    assert "current_stage_required_artifacts" in gate["failed_checks"]
    assert "conditional_repair_artifacts" in gate["failed_checks"]


def test_artifact_inventory_requires_failure_overlay_artifacts_when_attempted(
    tmp_path,
):
    def write_artifact(name: str) -> str:
        path = tmp_path / name
        path.write_text("{}", encoding="utf-8")
        return str(path)

    payload = {
        "intelligence_json": write_artifact("github_repo_intelligence.json"),
        "intelligence_markdown": write_artifact("github_repo_intelligence.md"),
        "agent_controller_json": write_artifact("github_repo_agent_controller.json"),
        "agent_controller_markdown": write_artifact("github_repo_agent_controller.md"),
        "agent_action_registry_json": write_artifact("agent_action_registry.json"),
        "agent_action_registry_markdown": write_artifact("agent_action_registry.md"),
        "agent_policy_trace_json": write_artifact("agent_policy_trace.json"),
        "agent_policy_trace_markdown": write_artifact("agent_policy_trace.md"),
        "agent_invocation_json": write_artifact("agent_invocation.json"),
        "agent_invocation_markdown": write_artifact("agent_invocation.md"),
        "agent_goal_readiness_json": write_artifact("agent_goal_readiness.json"),
        "agent_goal_readiness_markdown": write_artifact("agent_goal_readiness.md"),
        "agent_decision_timeline_json": write_artifact("agent_decision_timeline.json"),
        "agent_decision_timeline_markdown": write_artifact("agent_decision_timeline.md"),
        "final_report_json": write_artifact("final_report.json"),
        "final_report_markdown": write_artifact("final_report.md"),
        "repository_structure_json": write_artifact("repository_structure.json"),
        "repository_structure_markdown": write_artifact("repository_structure.md"),
        "repository_test_discovery_json": write_artifact(
            "repository_test_discovery.json"
        ),
        "repository_test_discovery_markdown": write_artifact(
            "repository_test_discovery.md"
        ),
        "repo_graph_json": write_artifact("repo_graph.json"),
        "repo_graph_markdown": write_artifact("repo_graph.md"),
        "fault_localization_json": write_artifact("fault_localization.json"),
        "fault_localization_markdown": write_artifact("fault_localization.md"),
        "analysis_readiness_json": write_artifact("analysis_readiness.json"),
        "analysis_readiness_markdown": write_artifact("analysis_readiness.md"),
        "repository_test_failure_overlay_status": "skipped",
        "repository_test_failure_overlay_reason": "no_supported_overlay_candidates",
        "repository_test_failure_overlay_supported_candidates": 0,
        "repository_test_failure_overlay_attempted_cases": 0,
        "repository_test_failure_overlay_json": "",
        "repository_test_failure_overlay_markdown": "",
    }

    inventory = intelligence_module._artifact_inventory_summary(
        payload,
        check_files=True,
    )
    test_rows = {item["name"]: item for item in inventory["groups"]["test"]}

    assert inventory["status"] == "warning"
    assert inventory["missing_core_artifacts"] == []
    assert inventory["missing_required_artifacts"] == [
        "repository_test_failure_overlay.json",
        "repository_test_failure_overlay.md",
    ]
    assert test_rows["repository_test_failure_overlay.json"]["required_now"] is True
    assert test_rows["repository_test_failure_overlay.json"]["available"] is False
    assert test_rows["repository_test_failure_overlay.md"]["required_now"] is True
    assert test_rows["repository_test_failure_overlay.md"]["available"] is False


def test_github_repo_intelligence_lifts_timeout_narrowing_audit():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "repo_intelligence"
        opener = _FakeOpener(_repo_payloads(raw_source))

        report = run_github_repo_intelligence(
            "example/project",
            output_dir,
            recipes=["missing_len_zero_guard"],
            max_sources=5,
            max_candidates=5,
            opener=opener,
        )
        timeout_json = output_dir / "repository_test_timeout_narrowing.json"
        timeout_markdown = output_dir / "repository_test_timeout_narrowing.md"
        timeout_json.write_text(
            json.dumps(
                {
                    "status": "pass",
                    "reason": "timeout_narrowing_selected_non_timeout_result",
                    "executed": True,
                    "attempt_count": 2,
                    "selected_command": (
                        "python -m pytest -q --maxfail=1 tests/test_sample.py"
                    ),
                    "selected_failure_category": "none",
                }
            ),
            encoding="utf-8",
        )
        timeout_markdown.write_text(
            "# Repository Test Timeout Narrowing\n",
            encoding="utf-8",
        )
        report = replace(
            report,
            summary={
                **report.summary,
                "repository_test_timeout_narrowing_status": "pass",
                "repository_test_timeout_narrowing_reason": (
                    "timeout_narrowing_selected_non_timeout_result"
                ),
                "repository_test_timeout_narrowing_executed": True,
                "repository_test_timeout_narrowing_attempt_count": 2,
                "repository_test_timeout_narrowing_selected_command": (
                    "python -m pytest -q --maxfail=1 tests/test_sample.py"
                ),
                "repository_test_timeout_narrowing_selected_failure_category": (
                    "none"
                ),
                "repository_test_dynamic_evidence_level": "passing_tests",
            },
            output_paths={
                **report.output_paths,
                "repository_test_timeout_narrowing_json": str(timeout_json),
                "repository_test_timeout_narrowing_markdown": str(
                    timeout_markdown
                ),
            },
        )
        summary = github_repo_intelligence_summary(report)
        paths = write_github_repo_intelligence_artifacts(report, summary)
        saved = json.loads(
            Path(paths["github_repo_intelligence_json"]).read_text(
                encoding="utf-8"
            )
        )
        test_rows = {
            item["name"]: item for item in saved["artifact_inventory"]["groups"]["test"]
        }

        assert summary["repository_test_timeout_narrowing_status"] == "pass"
        assert summary["repository_test_timeout_narrowing_reason"] == (
            "timeout_narrowing_selected_non_timeout_result"
        )
        assert summary["repository_test_timeout_narrowing_executed"] is True
        assert summary["repository_test_timeout_narrowing_attempt_count"] == 2
        assert summary["repository_test_timeout_narrowing_selected_command"] == (
            "python -m pytest -q --maxfail=1 tests/test_sample.py"
        )
        assert summary[
            "repository_test_timeout_narrowing_selected_failure_category"
        ] == "none"
        assert test_rows["repository_test_timeout_narrowing.json"][
            "required_now"
        ] is True
        assert test_rows["repository_test_timeout_narrowing.json"][
            "available"
        ] is True
        assert test_rows["repository_test_timeout_narrowing.md"][
            "required_now"
        ] is True
        assert test_rows["repository_test_timeout_narrowing.md"]["available"] is True
        assert "repository_test_timeout_narrowing.json" not in saved[
            "artifact_inventory"
        ]["missing_required_artifacts"]


def test_acceptance_gate_requires_audited_artifact_inventory():
    payload = {
        "agent_controller": {
            "control_loop": [
                "observe",
                "plan",
                "act",
                "verify",
                "reflect",
                "replan",
            ],
            "selected_action": {"id": "collect_static_signals"},
        },
        "agent_decision_timeline": {
            "status": "pass",
            "step_count": 1,
            "complete_step_count": 1,
        },
        "repository_structure": {"analyzed_file_count": 1},
        "fault_localization": {"rankings": [{"rank": 1}]},
        "agent_answers": {
            "answer_coverage": {
                "complete": True,
                "answered_question_count": 7,
                "required_question_count": 7,
            }
        },
    }

    gate = intelligence_module._acceptance_gate_summary(payload)

    assert gate["status"] == "warning"
    assert "core_artifacts" in gate["failed_checks"]
    assert "current_stage_required_artifacts" in gate["failed_checks"]


def test_acceptance_gate_requires_repair_decision_audit_for_llm_patch():
    payload = {
        "artifact_inventory": {
            "artifact_count": 1,
            "available_count": 1,
            "required_count": 0,
            "required_available_count": 0,
            "missing_core_artifacts": [],
            "missing_required_artifacts": [],
            "groups": {"core": [], "test": [], "repair": [], "phase4": []},
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
            "selected_action": {"id": "validate_patch_candidates"},
            "loop_iteration_audit": {
                "status": "pass",
                "iteration_count": 1,
                "complete_iteration_count": 1,
            },
        },
        "agent_decision_timeline": {
            "status": "pass",
            "step_count": 1,
            "complete_step_count": 1,
        },
        "repository_structure": {"analyzed_file_count": 1},
        "fault_localization": {"rankings": [{"rank": 1}]},
        "agent_answers": {
            "answer_coverage": {
                "complete": True,
                "answered_question_count": 7,
                "required_question_count": 7,
            }
        },
        "repository_patch_generation_mode": "hybrid",
        "repository_llm_patch_generation_status": "blocked",
        "repository_llm_patch_generation_reason": "missing_llm_api_key",
        "repository_test_repair_ready": False,
    }

    gate = intelligence_module._acceptance_gate_summary(payload)
    decision_audit = intelligence_module._repair_decision_audit_summary(payload)

    assert decision_audit["passed"] is False
    assert "llm_patch_provider_model" in decision_audit["failed_checks"]
    assert "llm_patch_api_key_env" in decision_audit["failed_checks"]
    assert gate["status"] == "warning"
    assert "repair_decision_audit" in gate["failed_checks"]


def test_environment_repair_not_required_after_passing_test_evidence():
    blocked_payload = {
        "analysis_readiness": {
            "blocker": "environment:test_tool_missing",
            "dynamic_evidence_level": "not_executed",
        },
        "repository_test_setup_doctor_blocker": "environment:test_tool_missing",
    }
    passing_payload = {
        "analysis_readiness": {
            "blocker": "dynamic_evidence_not_usable:passing_tests",
            "dynamic_evidence_level": "passing_tests",
            "repository_test_setup_doctor_blocker": "environment:test_tool_missing",
        },
        "repository_test_setup_doctor_blocker": "environment:test_tool_missing",
    }

    assert intelligence_module._environment_repair_required(blocked_payload) is True
    assert intelligence_module._environment_repair_required(passing_payload) is False


def _agent_goal_readiness_reflection_payload(
    *,
    controller_action: str,
    reflection_summary: dict,
) -> dict:
    return {
        "repo_spec": "example/project",
        "repo": "example/project",
        "repo_input": {
            "kind": "owner_repo",
            "ref_selection_source": "default_branch",
        },
        "repository_ref": "main",
        "requested_ref": "",
        "ref_source": "default_branch",
        "source_cache_dir": "outputs/source_cache",
        "agent_invocation": {
            "source_cache_dir": "outputs/source_cache",
            "include": [],
            "exclude": [],
            "target_prefix": "",
            "max_sources": 5,
            "max_candidates": 5,
            "repository_checkout_depth": 1,
            "checkout_repository_tests": True,
        },
        "analysis_readiness": {
            "dynamic_evidence_level": "failing_tests",
            "dynamic_evidence_usable_for_localization": True,
            "planned_repository_test_runner": "pytest",
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
            "selected_action": {"id": controller_action},
            "loop_iteration_audit": {
                "status": "pass",
                "iteration_count": 1,
                "complete_iteration_count": 1,
            },
        },
        "artifact_inventory": {
            "missing_core_artifacts": [],
            "artifact_count": 10,
            "available_count": 10,
        },
        "agent_answers": {
            "answer_coverage": {
                "complete": True,
                "answered_question_count": 7,
                "required_question_count": 7,
            }
        },
        "acceptance_gate": {
            "passed": True,
            "passed_check_count": 12,
            "check_count": 12,
        },
        "repository_structure": {
            "analyzed_file_count": 1,
            "function_count": 1,
        },
        "repo_graph": {
            "file_node_count": 1,
            "function_node_count": 1,
            "program_graph": {"available": True},
        },
        "fault_localization": {
            "mode": "dynamic",
            "top_function": "shift_left",
            "rankings": [
                {
                    "function_name": "shift_left",
                    "static_rule_score": 0.1,
                    "graph_score": 0.2,
                    "sbfl_score": 0.3,
                    "dynamic_test_evidence_score": 1.0,
                    "final_score": 0.9,
                }
            ],
        },
        "selected_signal_count": 1,
        "total_signal_count": 1,
        "static_intelligence_status": "analysis_ready",
        "repository_test_setup_doctor_status": "pass",
        "repository_test_environment_status": "pass",
        "repository_test_patch_validation_status": "fail",
        "repository_test_patch_validation_success_count": 0,
        "repository_test_patch_validation_reflection_candidate_count": 0,
        "repository_test_repair_ready": False,
        "reflection_summary": reflection_summary,
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
