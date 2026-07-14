from pathlib import Path

from code_intelligence_agent.evaluation.repository_test_setup_doctor import (
    build_repository_test_setup_doctor,
    render_repository_test_setup_doctor_markdown,
    write_repository_test_setup_doctor_artifacts,
)


def test_repository_test_setup_doctor_blocks_without_full_checkout(tmp_path):
    payload = build_repository_test_setup_doctor(
        repository_profile={
            "doctor_status": "pass",
            "doctor_blocker": "none",
            "recommended_test_command": "python -m pytest",
        },
        repository_test_command={
            "status": "skipped",
            "reason": "full_repo_not_materialized",
            "command": "python -m pytest",
            "executed": False,
        },
        repository_test_execution_plan={
            "status": "warning",
            "reason": "full_repo_not_materialized",
            "recommended_execution_command": "python -m pytest",
            "repository_root_present": False,
            "executable_now": False,
        },
    )

    assert payload["status"] == "blocked"
    assert payload["blocker"] == "checkout:full_repo_not_materialized"
    assert payload["check_count"] == 8
    assert payload["passed_check_count"] == 2
    assert payload["blocked_check_count"] == 2
    assert payload["skipped_check_count"] == 4
    assert payload["check_status_counts"] == {
        "blocked": 2,
        "pass": 2,
        "skipped": 4,
    }
    assert payload["blocked_check_names"] == [
        "full_repository_checkout",
        "execution_plan",
    ]
    assert payload["warning_check_names"] == []
    assert "--checkout-repository-tests" in payload["next_action"]
    markdown = render_repository_test_setup_doctor_markdown(payload)
    assert "checkout:full_repo_not_materialized" in markdown
    assert "Checks: 2/8 pass" in markdown
    assert "Check Statuses: blocked=2, pass=2, skipped=4" in markdown

    paths = write_repository_test_setup_doctor_artifacts(payload, tmp_path)
    assert Path(paths["repository_test_setup_doctor_json"]).exists()
    assert Path(paths["repository_test_setup_doctor_markdown"]).exists()


def test_repository_test_setup_doctor_blocks_setup_install_failure():
    payload = build_repository_test_setup_doctor(
        repository_profile={
            "doctor_status": "pass",
            "doctor_blocker": "none",
            "recommended_test_command": "python -m pytest",
        },
        repository_test_command={
            "status": "pass",
            "reason": "command_returncode",
            "command": "python -m pytest",
            "executed": True,
        },
        repository_test_environment={"status": "pass", "reason": "environment_ready"},
        repository_test_environment_setup={
            "status": "pass",
            "reason": "setup_plan_built",
            "install_command_supported": True,
        },
        repository_test_environment_setup_result={
            "status": "fail",
            "reason": "install_failed",
            "executed": True,
            "install_failure_category": "missing_requirement_file",
        },
        repository_test_execution_plan={
            "status": "pass",
            "reason": "execution_plan_built",
            "recommended_execution_command": "python -m pytest",
            "repository_root_present": True,
            "executable_now": True,
        },
    )

    assert payload["status"] == "blocked"
    assert payload["blocker"] == "setup_install_failure:missing_requirement_file"
    assert payload["blocked_check_count"] == 1
    assert payload["blocked_check_names"] == ["environment_setup"]
    assert payload["check_status_counts"]["blocked"] == 1
    assert "requirements path" in payload["next_action"]


def test_repository_test_setup_doctor_does_not_treat_none_sentinel_as_failure():
    payload = build_repository_test_setup_doctor(
        repository_test_environment_setup={
            "status": "pass",
            "reason": "setup_plan_built",
            "install_command_supported": True,
        },
        repository_test_environment_setup_result={
            "status": "skipped",
            "reason": "execution_disabled",
            "executed": False,
            "install_failure_category": "none",
        },
    )
    setup_check = next(
        check for check in payload["checks"] if check["name"] == "environment_setup"
    )

    assert setup_check["status"] == "warning"
    assert setup_check.get("blocker", "") != "setup_install_failure:none"
    assert payload["blocker"] != "setup_install_failure:none"


def test_repository_test_setup_doctor_passes_with_usable_dynamic_evidence():
    payload = build_repository_test_setup_doctor(
        repository_profile={
            "doctor_status": "pass",
            "doctor_blocker": "none",
            "recommended_test_command": "python -m pytest",
        },
        repository_test_command={
            "status": "pass",
            "reason": "command_returncode",
            "command": "python -m pytest",
            "executed": True,
        },
        repository_test_environment={"status": "pass", "reason": "environment_ready"},
        repository_test_environment_setup={
            "status": "pass",
            "reason": "setup_plan_built",
            "install_command_supported": True,
        },
        repository_test_environment_setup_result={
            "status": "pass",
            "reason": "setup_succeeded",
            "executed": True,
        },
        repository_test_execution_plan={
            "status": "pass",
            "reason": "execution_plan_built",
            "recommended_execution_command": "python -m pytest tests/test_bug.py",
            "repository_root_present": True,
            "executable_now": True,
        },
        repository_test_execution_result={
            "status": "fail",
            "reason": "command_returncode",
            "executed": True,
            "failure_category": "assertion_failure",
        },
        repository_test_dynamic_evidence={
            "status": "pass",
            "reason": "failing_tests_available",
            "evidence_level": "failing_tests",
            "usable_for_localization": True,
            "usable_for_patch_validation": True,
        },
    )

    assert payload["status"] == "pass"
    assert payload["blocker"] == "none"
    assert payload["check_count"] == 8
    assert payload["passed_check_count"] == 7
    assert payload["blocked_check_count"] == 0
    assert payload["warning_check_count"] == 1
    assert payload["check_status_counts"] == {"pass": 7, "warning": 1}
    assert payload["warning_check_names"] == ["execution_result"]
    assert payload["signals"]["dynamic_usable_for_localization"] is True
