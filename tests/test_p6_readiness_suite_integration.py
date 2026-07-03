import json
from pathlib import Path

from code_intelligence_agent.evaluation.github_onboarding_matrix import (
    REQUIRED_ONBOARDING_ARTIFACTS,
)
from code_intelligence_agent.evaluation.github_repo_intelligence_suite import (
    run_github_repo_intelligence_suite,
)


def test_suite_writes_onboarding_repair_and_p6_readiness_artifacts(tmp_path):
    output_dir = tmp_path / "suite"
    manifest_path = tmp_path / "manifest.json"
    run_dir = output_dir / "case_one"
    _write_reusable_repo_report(run_dir, "example/case-one")
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "p6_readiness_integration",
                "run_github_onboarding_matrix": True,
                "run_llm_repair_showcase_matrix": True,
                "run_p6_readiness_audit": True,
                "github_onboarding_matrix_required_case_count": 1,
                "runs": [
                    {
                        "name": "case_one",
                        "repo": "example/case-one",
                        "reuse_existing_report": True,
                        "output_dir": str(run_dir),
                        "expected_status": "pass",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    report = run_github_repo_intelligence_suite(manifest_path, output_dir)
    summary = report.summary
    onboarding_path = output_dir / "github_onboarding_matrix.json"
    repair_path = output_dir / "llm_repair_evaluation_matrix.json"
    readiness_path = output_dir / "p6_readiness_audit.json"
    suite_markdown = (output_dir / "github_repo_intelligence_suite.md").read_text(
        encoding="utf-8"
    )

    assert onboarding_path.exists()
    assert repair_path.exists()
    assert readiness_path.exists()
    assert summary["github_onboarding_matrix_json"] == str(onboarding_path)
    assert summary["llm_repair_evaluation_matrix_json"] == str(repair_path)
    assert summary["p6_readiness_audit_json"] == str(readiness_path)
    assert summary["github_onboarding_matrix_status"] == "incomplete"
    assert summary["llm_repair_evaluation_matrix_status"] == "incomplete"
    assert summary["p6_readiness_audit_status"] == "incomplete"
    assert "repair_case_count" in summary["p6_readiness_audit_missing"]
    assert "P6 Readiness Audit Status: `incomplete`" in suite_markdown
    assert "GitHub Onboarding Matrix Status: `incomplete`" in suite_markdown


def test_suite_marks_p6_readiness_not_run_when_required_matrix_missing(tmp_path):
    output_dir = tmp_path / "suite"
    manifest_path = tmp_path / "manifest.json"
    run_dir = output_dir / "case_one"
    _write_reusable_repo_report(run_dir, "example/case-one")
    manifest_path.write_text(
        json.dumps(
            {
                "suite_name": "p6_missing_matrix",
                "run_github_onboarding_matrix": True,
                "run_p6_readiness_audit": True,
                "github_onboarding_matrix_required_case_count": 1,
                "runs": [
                    {
                        "name": "case_one",
                        "repo": "example/case-one",
                        "reuse_existing_report": True,
                        "output_dir": str(run_dir),
                        "expected_status": "pass",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    report = run_github_repo_intelligence_suite(manifest_path, output_dir)

    assert report.summary["github_onboarding_matrix_status"] == "incomplete"
    assert report.summary["p6_readiness_audit_status"] == "not_run"
    assert report.summary["p6_readiness_audit_reason"] == "required_matrices_missing"
    assert report.summary["p6_readiness_audit_missing"] == [
        "llm_repair_evaluation_matrix"
    ]
    assert not (output_dir / "p6_readiness_audit.json").exists()


def _write_reusable_repo_report(root: Path, repo: str) -> None:
    root.mkdir(parents=True)
    candidates = [
        {
            "rank": 1,
            "runner": "pytest",
            "command": "python -m pytest",
            "confidence": 0.9,
            "reason": "pytest_signal",
            "evidence": ["pytest.ini"],
        }
    ]
    profile = {
        "imported_source_count": 2,
        "python_source_ratio": 1.0,
        "test_source_count": 1,
        "test_source_paths": ["tests/test_bug.py"],
        "package_roots": ["demo"],
        "src_layout_packages": [],
        "project_config_files": ["pytest.ini"],
        "test_framework_signals": ["pytest"],
        "test_command_candidates": candidates,
        "recommended_test_command": "python -m pytest",
    }
    structure = {
        "analyzed_file_count": 2,
        "package_structure": {
            "package_roots": ["demo"],
            "src_layout_packages": [],
            "recommended_target_prefix": "demo",
        },
        "project_config": {
            "project_config_files": ["pytest.ini"],
            "dependency_tool_signals": ["pytest"],
        },
        "test_structure": {
            "test_source_count": 1,
            "test_source_paths": ["tests/test_bug.py"],
            "test_directories": ["tests"],
            "test_framework_signals": ["pytest"],
            "recommended_test_command": "python -m pytest",
            "test_command_candidates": candidates,
            "test_command_candidate_count": 1,
        },
    }
    discovery = {
        "status": "pass",
        "reason": "test_sources_discovered",
        "blocker": "",
        "test_source_count": 1,
        "test_source_paths": ["tests/test_bug.py"],
        "test_directories": ["tests"],
        "test_framework_signals": ["pytest"],
        "recommended_test_command": "python -m pytest",
        "test_command_candidates": candidates,
        "project_config_files": ["pytest.ini"],
    }
    environment = {
        "status": "pass",
        "reason": "test_environment_ready",
        "recommended_install_command": "",
    }
    execution_plan = {
        "command": "python -m pytest",
        "runner": "pytest",
        "candidate_commands": candidates,
    }
    policy_trace = {
        "status": "pass",
        "selected_action": {"id": "generate_llm_patch_candidates"},
        "canonical_action": {"id": "generate_llm_patch_candidates"},
        "loop": ["observe", "plan", "act", "verify", "reflect", "replan"],
    }
    for filename, payload in (
        ("repository_profile.json", profile),
        ("repository_structure.json", structure),
        ("repository_test_discovery.json", discovery),
        ("repository_test_environment.json", environment),
        ("repository_test_execution_plan.json", execution_plan),
        ("agent_policy_trace.json", policy_trace),
    ):
        _write_json(root / filename, payload)
    for artifact_name, _path_key, filename in REQUIRED_ONBOARDING_ARTIFACTS:
        if filename.endswith(".md"):
            (root / filename).write_text(f"# {artifact_name}\n", encoding="utf-8")
    summary = {
        "repo": repo,
        "repo_spec": repo,
        "output_dir": str(root),
        "status": "pass",
        "passed": True,
        "intelligence_json": str(root / "github_repo_intelligence.json"),
        "repository_patch_generation_mode": "llm",
        "repository_llm_patch_generation_status": "pass",
        "repository_llm_patch_provider": "deepseek",
        "repository_llm_patch_model": "deepseek-v4-pro",
        "repository_llm_patch_api_key_present": True,
        "repository_patch_generator_llm_candidate_count": 2,
        "repository_test_patch_validation_status": "pass",
        "repository_test_patch_validation_json": str(
            root / "repository_test_patch_validation.json"
        ),
        "repository_test_patch_validation_markdown": str(
            root / "repository_test_patch_validation.md"
        ),
        "repository_test_patch_validation_input_candidate_count": 2,
        "repository_test_patch_validation_candidate_count": 2,
        "repository_test_patch_validation_executed_count": 1,
        "repository_test_patch_validation_success_count": 1,
        "repository_test_patch_validation_first_success_rank": 1,
        "repository_test_patch_judge_mode": "llm",
        "repository_test_patch_judge_status": "ready",
        "repository_test_patch_judge_enabled": True,
        "repository_test_patch_judge_candidate_count": 1,
        "repository_test_patch_judge_authority": (
            "sandbox_pytest_decides_success"
        ),
        "repository_test_patch_judge_agreement_counts": {"aligned": 1},
        "repository_test_patch_judge_outcome_counts": {"accept_success": 1},
        "repository_test_patch_judge_accept_success_count": 1,
        "repository_llm_patch_total_tokens": 100,
        "agent_answers_next_action": "Generate final agent report.",
    }
    _write_json(root / "repository_test_patch_validation.json", {"status": "pass"})
    (root / "repository_test_patch_validation.md").write_text(
        "# Patch Validation\n",
        encoding="utf-8",
    )
    _write_json(root / "github_repo_intelligence.json", summary)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
