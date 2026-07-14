import json
from pathlib import Path

from code_intelligence_agent.evaluation.unfamiliar_repository_evaluation import (
    evaluate_unfamiliar_repository_suite,
    render_unfamiliar_repository_evaluation_markdown,
    write_unfamiliar_repository_evaluation_artifacts,
)


MANIFEST_PATH = Path(
    "datasets/github_cases/v2_unfamiliar_python_repositories_20.json"
)


def test_unfamiliar_repository_manifest_is_fixed_sha_holdout():
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    runs = payload["runs"]

    assert payload["suite_name"] == "v2_unfamiliar_python_repositories_20"
    assert payload["selection_policy"]["role"] == "unfamiliar_holdout"
    assert payload["selection_policy"]["fixed_commit_required"] is True
    assert len(runs) == 20
    assert len({run["repo"] for run in runs}) == 20
    assert len({run["ref"] for run in runs}) == 20
    assert all(len(run["ref"]) == 40 for run in runs)
    assert all(set(run["ref"]) <= set("0123456789abcdef") for run in runs)
    assert all(run["expected_status"] == "pass" for run in runs)
    assert all("pinned_sha" in run["scenario_tags"] for run in runs)
    assert sum(bool(run.get("checkout_repository_tests")) for run in runs) == 7
    categories = {category for run in runs for category in run["categories"]}
    assert {
        "small_library",
        "cli",
        "web",
        "data_processing",
        "src_layout",
        "flat_layout",
        "multi_package",
        "native_extension",
        "tox_or_nox",
        "modern_pyproject",
    }.issubset(categories)
    defaults = payload["defaults"]
    assert defaults["run_repository_test_environment_setup"] is False
    assert defaults["repository_test_timeout"] == 8
    assert defaults["clear_llm_api_keys"] is True


def test_unfamiliar_repository_evaluation_aggregates_real_outcome_contract(tmp_path):
    manifest_runs = []
    suite_runs = []
    for index in range(20):
        name = f"case_{index:02d}"
        report_path = tmp_path / name / "github_repo_intelligence.json"
        report_path.parent.mkdir(parents=True)
        report_path.write_text("{}", encoding="utf-8")
        manifest_runs.append(
            {
                "name": name,
                "repo": f"example/repo-{index:02d}",
                "ref": f"{index + 1:040x}",
                "categories": ["small_library" if index < 10 else "cli"],
            }
        )
        static_success = index < 19
        test_started = index == 0
        metrics = {
            "imported_source_count": 3 if static_success else 0,
            "repository_structure_modeled": static_success,
            "repo_graph_ready": static_success,
            "repository_source_root_count": 1 if static_success else 0,
            "repository_test_root_count": 1 if index < 10 else 0,
            "planned_repository_test_command": (
                "python -m pytest" if index < 10 else ""
            ),
            "planned_repository_test_result_executed": test_started,
            "planned_repository_test_result_status": "fail" if test_started else "skipped",
            "planned_repository_test_failure_category": (
                "missing_dependency" if test_started else ""
            ),
            "repository_compatibility_status": (
                "blocked" if index == 19 else "partial"
            ),
            "repository_compatibility_termination_reason": (
                "unsupported_scope"
                if index == 19
                else "checkout:repository_root_not_materialized"
            ),
            "repository_compatibility_primary_blocker": (
                "unsupported_scope" if index == 19 else ""
            ),
            "repository_install_risk": "medium",
            "repository_python_compatibility_status": "compatible",
            "repository_dependency_access_blocker_count": 0,
        }
        suite_runs.append(
            {
                "name": name,
                "status": "pass",
                "report_path": str(report_path),
                "metrics": metrics,
                "elapsed_ms": 100 + index,
            }
        )

    payload = evaluate_unfamiliar_repository_suite(
        {"suite_name": "holdout", "runs": manifest_runs},
        {"runs": suite_runs},
    )
    paths = write_unfamiliar_repository_evaluation_artifacts(payload, tmp_path / "out")
    markdown = render_unfamiliar_repository_evaluation_markdown(payload)

    assert payload["passed"] is True
    assert payload["metrics"]["case_count"] == 20
    assert payload["metrics"]["structured_report_rate"] == 1.0
    assert payload["metrics"]["static_analysis_success_rate"] == 0.95
    assert payload["metrics"]["test_command_discovery_rate"] == 0.5
    assert payload["metrics"]["test_start_rate"] == 0.05
    assert payload["metrics"]["blocker_classification_rate"] == 1.0
    assert payload["metrics"]["test_failure_layer_classification_rate"] == 1.0
    assert payload["metrics"]["elapsed_ms_semantics"] == "suite_execution_elapsed"
    assert payload["metrics"]["end_to_end_elapsed_available"] is True
    assert payload["metrics"]["existing_report_reuse_count"] == 0
    assert payload["metrics"]["outcome_counts"] == {
        "blocked": 1,
        "partial": 18,
        "success": 1,
    }
    assert payload["cases"][0]["failure_layer"] == "environment"
    assert "Static Analysis Success Rate: 0.9500" in markdown
    assert Path(paths["unfamiliar_repository_evaluation_json"]).is_file()
    assert Path(paths["unfamiliar_repository_evaluation_markdown"]).is_file()

    for run in suite_runs:
        run["metrics"]["existing_report_reuse"] = True
    reused_payload = evaluate_unfamiliar_repository_suite(
        {"suite_name": "holdout", "runs": manifest_runs},
        {"runs": suite_runs},
    )
    assert reused_payload["metrics"]["elapsed_ms_semantics"] == (
        "report_reuse_overhead"
    )
    assert reused_payload["metrics"]["end_to_end_elapsed_available"] is False
    assert reused_payload["metrics"]["existing_report_reuse_count"] == 20


def test_unfamiliar_repository_evaluation_fails_missing_report_and_branch_ref(
    tmp_path,
):
    payload = evaluate_unfamiliar_repository_suite(
        {
            "suite_name": "invalid",
            "runs": [
                {
                    "name": f"case_{index}",
                    "repo": f"example/repo-{index}",
                    "ref": "main" if index == 0 else f"{index + 1:040x}",
                }
                for index in range(20)
            ],
        },
        {"runs": []},
    )

    assert payload["passed"] is False
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["unique_fixed_sha_repositories"]["passed"] is False
    assert checks["all_structured_reports"]["passed"] is False
    assert payload["metrics"]["structured_report_count"] == 0
