import json
from pathlib import Path

from code_intelligence_agent.evaluation.github_onboarding_matrix import (
    REQUIRED_ONBOARDING_ARTIFACTS,
    REQUIRED_SCENARIOS,
)


MANIFEST_PATH = Path(
    "datasets/github_cases/repo_intelligence_p6_onboarding_readiness.example.json"
)


def test_p6_onboarding_readiness_manifest_defines_real_ten_repo_gate():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["suite_name"] == "repo_intelligence_p6_onboarding_readiness"
    assert manifest["run_github_onboarding_matrix"] is True
    assert manifest["run_llm_repair_showcase_matrix"] is True
    assert manifest["run_llm_repair_case_catalog_audit"] is True
    assert manifest["run_p6_readiness_audit"] is True
    assert manifest["llm_repair_case_catalog_path"] == (
        "llm_repair_case_catalog.example.json"
    )
    assert manifest["llm_repair_source_reports"] == [
        "../../outputs_smoke/p6_llm_repair/github_repo_intelligence_suite.json",
        "../../outputs_smoke/p6_llm_direct_success/github_repo_intelligence_suite.json",
        "../../outputs_smoke/p6_llm_reflection_success/github_repo_intelligence_suite.json",
        "../../outputs_smoke/p6_onboarding_blockers/github_repo_intelligence_suite.json",
        "../../outputs_smoke/p6_safety_gate_blockers/github_repo_intelligence_suite.json",
    ]
    assert manifest["github_onboarding_matrix_required_case_count"] == 10
    assert manifest["defaults"]["execution_profile"] == "agent-auto"
    assert manifest["defaults"]["repository_patch_generation_mode"] == "rule"
    assert manifest["defaults"]["auto_fallback"] is False
    assert manifest["defaults"]["auto_controller_max_actions"] == 0
    assert manifest["suite_thresholds"]["min_run_count"] == 10
    assert manifest["suite_thresholds"]["min_agent_shortcut_count"] == 2
    assert manifest["suite_thresholds"]["min_github_onboarding_matrix_case_count"] == 10
    assert (
        manifest["suite_thresholds"][
            "min_github_onboarding_matrix_covered_scenario_count"
        ]
        == len(REQUIRED_SCENARIOS)
    )
    assert (
        manifest["suite_thresholds"][
            "min_github_onboarding_matrix_complete_artifact_group_count"
        ]
        == len(REQUIRED_ONBOARDING_ARTIFACTS)
    )
    assert (
        manifest["suite_thresholds"][
            "min_llm_repair_case_catalog_declared_case_count"
        ]
        == 20
    )
    assert manifest["suite_thresholds"]["min_p6_readiness_audit_check_count"] >= 20

    runs = manifest["runs"]
    repos = [run["repo"] for run in runs]
    assert len(runs) == 10
    assert len(set(repos)) == 10
    assert "https://github.com/Anweilong111/code-intelligence-Agent" in repos
    assert all(run["expected_execution_profile"] == "agent-auto" for run in runs)
    agent_shortcut_runs = [run for run in runs if run.get("agent") is True]
    assert {run["name"] for run in agent_shortcut_runs} == {
        "pypa_sampleproject_p6_nox_environment",
        "thealgorithms_p6_failing_test_evidence",
    }
    expected_shortcut_runs = [
        run for run in runs if run.get("expected_agent_shortcut") is True
    ]
    assert {run["name"] for run in expected_shortcut_runs} == {
        "pypa_sampleproject_p6_nox_environment",
        "thealgorithms_p6_failing_test_evidence",
    }

    tags = {tag for run in runs for tag in run.get("scenario_tags", [])}
    for scenario_id, _description in REQUIRED_SCENARIOS:
        assert scenario_id in tags
        assert (
            manifest["suite_thresholds"][f"min_scenario_tag_{scenario_id}_count"]
            == 1
        )
    for tag in (
        "owner_repo_input",
        "github_url_input",
        "source_cache",
        "include_filter",
        "shallow_checkout",
        "p6_onboarding_readiness",
    ):
        assert tag in tags

    cached_runs = [
        run for run in runs if run.get("prefer_cached_discovery") is True
    ]
    assert len(cached_runs) >= 8
    cached_run_names = {run["name"] for run in cached_runs}
    assert {
        "click_p6_src_layout_pyproject",
        "rich_p6_complex_pyproject",
        "fastapi_p6_pyproject_dependency",
    }.issubset(cached_run_names)
    for run in cached_runs:
        assert Path(run["seed_discovery_path"]).is_file()


def test_p6_onboarding_readiness_manifest_keeps_blocker_cases_explicit():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    runs = {run["name"]: run for run in manifest["runs"]}

    no_python = runs["octocat_p6_no_python_sources"]
    assert no_python["expected_analysis_stage"] == "source_import_blocked"
    assert no_python["expected_blocker"] == "source_import_or_parse_missing"
    assert no_python["expected_controller_action"] == "adjust_source_filters"

    no_tests = runs["nanogpt_p6_no_tests"]
    assert "no_tests" in no_tests["scenario_tags"]
    assert no_tests["expected_repository_test_setup_doctor_blocker"] == (
        "test_command:no_recommended_test_command"
    )
    assert no_tests["expected_planned_repository_test_result_status"] == "skipped"

    timeout = runs["code_intelligence_agent_p6_timeout"]
    assert "timeout" in timeout["scenario_tags"]
    assert timeout["repository_test_timeout"] == 1
    assert timeout["checkout_repository_tests"] is True
    assert timeout["run_repository_test_command"] is True
    assert timeout["expected_planned_repository_test_result_status"] == "fail"

    failing = runs["thealgorithms_p6_failing_test_evidence"]
    assert "failing_test_evidence" in failing["scenario_tags"]
    assert failing["expected_patch_validation_status"] == "pass"
    assert (
        failing["metric_thresholds"][
            "repository_test_patch_validation_successful_reflection_count"
        ]
        == 1
    )
