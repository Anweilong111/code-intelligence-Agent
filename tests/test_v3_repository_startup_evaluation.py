import json
from pathlib import Path

from code_intelligence_agent.evaluation.v3_repository_startup_evaluation import (
    evaluate_v3_repository_startup,
    render_v3_repository_startup_evaluation_markdown,
    write_v3_repository_startup_evaluation_artifacts,
)


V2_MANIFEST = Path(
    "datasets/github_cases/v2_unfamiliar_python_repositories_20.json"
)
V3_MANIFEST = Path(
    "datasets/github_cases/v3_fixed_sha_repository_startup_20.json"
)


def test_v3_startup_manifest_is_paired_and_runner_only():
    v2 = json.loads(V2_MANIFEST.read_text(encoding="utf-8"))
    v3 = json.loads(V3_MANIFEST.read_text(encoding="utf-8"))
    v2_pairs = [(run["repo"], run["ref"]) for run in v2["runs"]]
    v3_pairs = [(run["repo"], run["ref"]) for run in v3["runs"]]

    assert v3["protocol"]["role"] == "paired_v2_v3_startup_comparison"
    assert v3_pairs == v2_pairs
    assert len(v3_pairs) == 20
    assert v3["defaults"]["checkout_repository_tests"] is True
    assert v3["defaults"]["run_repository_test_environment_setup"] is True
    assert v3["defaults"]["repository_test_environment_setup_mode"] == (
        "runner_probe"
    )
    assert v3["defaults"]["run_repository_test_retry"] is False
    assert v3["defaults"]["auto_repository_test_retry"] is False
    assert "not install the repository" in v3["protocol"]["safety_boundary"]


def test_v3_startup_evaluator_accepts_fourteen_isolated_starts(tmp_path):
    manifest = json.loads(V3_MANIFEST.read_text(encoding="utf-8"))
    suite_runs = []
    for index, entry in enumerate(manifest["runs"]):
        report_path = tmp_path / entry["name"] / "github_repo_intelligence.json"
        report_path.parent.mkdir(parents=True)
        report_path.write_text("{}", encoding="utf-8")
        started = index < 14
        metrics = {
            "planned_repository_test_command": "python -m pytest -q tests",
            "planned_repository_test_result_executed": started,
            "planned_repository_test_result_status": "fail" if started else "skipped",
            "planned_repository_test_failure_category": (
                "missing_dependency" if started else ""
            ),
            "repository_test_environment_setup_mode": "runner_probe",
            "repository_test_environment_setup_executed": started,
            "repository_test_environment_setup_result_status": (
                "pass" if started else "skipped"
            ),
            "repository_test_environment_setup_repository_code_install_requested": False,
            "repository_test_environment_setup_repository_dependency_install_requested": False,
            "planned_repository_test_python_source": (
                "repository_test_environment_setup" if started else "current_interpreter"
            ),
            "repository_test_setup_doctor_blocker": (
                "stale_controller_timeout" if not started else ""
            ),
            "repository_compatibility_primary_blocker": (
                "environment:python_version_incompatible" if not started else ""
            ),
        }
        suite_runs.append(
            {
                "name": entry["name"],
                "status": "pass",
                "report_path": str(report_path),
                "metrics": metrics,
                "elapsed_ms": 1000 + index,
            }
        )
    baseline = {
        "selection": {"repository_count": 20},
        "outcome_evaluation": {
            "test_start_rate": 0.35,
            "outcome_counts": {"success": 7, "partial": 13},
        },
    }

    payload = evaluate_v3_repository_startup(
        manifest,
        {"runs": suite_runs},
        baseline_metrics_payload=baseline,
    )
    paths = write_v3_repository_startup_evaluation_artifacts(
        payload,
        tmp_path / "evaluation",
    )
    markdown = render_v3_repository_startup_evaluation_markdown(payload)

    assert payload["passed"] is True
    assert payload["metrics"]["started_and_terminated_count"] == 14
    assert payload["metrics"]["started_and_terminated_rate"] == 0.7
    assert payload["metrics"]["startup_count_uplift"] == 7
    assert payload["metrics"]["startup_rate_uplift"] == 0.35
    assert payload["metrics"]["project_code_install_requested_count"] == 0
    assert payload["metrics"]["manifest_checkout_authorized_count"] == 20
    assert payload["metrics"]["runner_probe_report_count"] == 20
    assert payload["metrics"]["classified_not_started_blocker_count"] == 6
    assert payload["metrics"]["failure_layer_counts"] == {"environment": 14}
    assert {
        row["blocker"] for row in payload["cases"] if not row["test_started"]
    } == {"environment:python_version_incompatible"}
    assert "Started And Terminated: 14 / 20" in markdown
    assert Path(paths["v3_repository_startup_evaluation_json"]).is_file()
    assert Path(paths["v3_repository_startup_evaluation_markdown"]).is_file()


def test_v3_startup_evaluator_rejects_project_install_and_missed_target(tmp_path):
    manifest = json.loads(V3_MANIFEST.read_text(encoding="utf-8"))
    suite_runs = []
    for index, entry in enumerate(manifest["runs"]):
        report_path = tmp_path / entry["name"] / "github_repo_intelligence.json"
        report_path.parent.mkdir(parents=True)
        report_path.write_text("{}", encoding="utf-8")
        started = index < 13
        suite_runs.append(
            {
                "name": entry["name"],
                "status": "pass",
                "report_path": str(report_path),
                "metrics": {
                    "planned_repository_test_command": "python -m pytest -q",
                    "planned_repository_test_result_executed": started,
                    "planned_repository_test_result_status": (
                        "pass" if started else "skipped"
                    ),
                    "repository_test_environment_setup_mode": "runner_probe",
                    "repository_test_environment_setup_executed": started,
                    "repository_test_environment_setup_result_status": (
                        "pass" if started else "skipped"
                    ),
                    "repository_test_environment_setup_repository_code_install_requested": (
                        index == 0
                    ),
                    "repository_test_environment_setup_repository_dependency_install_requested": False,
                    "planned_repository_test_python_source": (
                        "repository_test_environment_setup"
                        if started
                        else "current_interpreter"
                    ),
                    "repository_test_setup_doctor_blocker": (
                        "setup_not_ready" if not started else ""
                    ),
                },
            }
        )

    payload = evaluate_v3_repository_startup(manifest, {"runs": suite_runs})
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["passed"] is False
    assert checks["no_repository_code_install"]["passed"] is False
    assert checks["minimum_started_and_terminated"]["passed"] is False
    assert payload["metrics"]["started_and_terminated_count"] == 12
