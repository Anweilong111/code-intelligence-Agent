import subprocess
from pathlib import Path

from code_intelligence_agent.evaluation.repository_test_pytest_plugin_repair import (
    execute_repository_test_pytest_plugin_repair,
    plan_repository_test_pytest_plugin_repair,
    render_repository_test_pytest_plugin_repair_markdown,
    write_repository_test_pytest_plugin_repair_artifacts,
)


def test_pytest_plugin_repair_plans_single_candidate_install(tmp_path):
    plan = plan_repository_test_pytest_plugin_repair(
        _setup_plan(tmp_path),
        _missing_fixture_retry_result(),
    )
    paths = write_repository_test_pytest_plugin_repair_artifacts(
        plan,
        tmp_path / "out",
    )
    markdown = render_repository_test_pytest_plugin_repair_markdown(plan)

    assert plan["status"] == "pass"
    assert plan["reason"] == "pytest_plugin_repair_plan_built"
    assert plan["fixture"] == "mocker"
    assert plan["plugin_package"] == "pytest-mock"
    assert plan["plugin_requirement"] == "pytest-mock==2.0.0"
    assert plan["install_command_args"] == [
        str(tmp_path / ".repo_test_venv" / "Scripts" / "python.exe"),
        "-m",
        "pip",
        "install",
        "pytest-mock==2.0.0",
    ]
    assert plan["safe_to_execute"] is True
    assert "pytest-mock==2.0.0" in markdown
    assert Path(paths["repository_test_pytest_plugin_repair_json"]).exists()
    assert Path(paths["repository_test_pytest_plugin_repair_markdown"]).exists()


def test_pytest_plugin_repair_warns_without_matching_candidate(tmp_path):
    setup = _setup_plan(tmp_path)
    setup["pytest_plugin_dependency_candidate_sources"] = [
        {
            "source": "requirements-dev.txt",
            "requirement": "pytest-httpbin==2.0.0",
            "package": "pytest-httpbin",
        }
    ]

    plan = plan_repository_test_pytest_plugin_repair(
        setup,
        _missing_fixture_retry_result(),
    )

    assert plan["status"] == "warning"
    assert plan["reason"] == "no_matching_pytest_plugin_candidate"
    assert plan["fixture"] == "mocker"
    assert plan["plugin_package"] == "pytest-mock"
    assert plan["install_command_args"] == []
    assert plan["safe_to_execute"] is False


def test_pytest_plugin_repair_skips_without_missing_fixture(tmp_path):
    plan = plan_repository_test_pytest_plugin_repair(
        _setup_plan(tmp_path),
        {
            "status": "fail",
            "executed": True,
            "failure_category": "missing_dependency",
            "failure_signal": "missing_module:requests",
        },
    )

    assert plan["status"] == "skipped"
    assert plan["reason"] == "no_missing_pytest_fixture"
    assert plan["install_command_args"] == []


def test_pytest_plugin_repair_executes_install_with_runner(tmp_path):
    commands = []

    def fake_runner(command, cwd, capture_output, text, timeout, check, env):
        del cwd, capture_output, text, timeout, check, env
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "installed", "")

    plan = plan_repository_test_pytest_plugin_repair(
        _setup_plan(tmp_path),
        _missing_fixture_retry_result(),
    )
    payload = execute_repository_test_pytest_plugin_repair(
        plan,
        enabled=True,
        runner=fake_runner,
    )

    assert payload["status"] == "pass"
    assert payload["executed"] is True
    assert payload["reason"] == "pytest_plugin_install_executed"
    assert payload["returncode"] == 0
    assert payload["stdout_preview"] == "installed"
    assert commands == [plan["install_command_args"]]


def _setup_plan(tmp_path):
    return {
        "status": "pass",
        "venv_python": str(tmp_path / ".repo_test_venv" / "Scripts" / "python.exe"),
        "pytest_plugin_dependency_candidate_sources": [
            {
                "source": "requirements-dev.txt",
                "requirement": "pytest-httpbin==2.0.0",
                "package": "pytest-httpbin",
            },
            {
                "source": "requirements-dev.txt",
                "requirement": "pytest-mock==2.0.0",
                "package": "pytest-mock",
            },
        ],
    }


def _missing_fixture_retry_result():
    return {
        "status": "fail",
        "executed": True,
        "command": "python -m pytest -q --maxfail=1 tests",
        "retry_command": "python -m pytest -q --maxfail=1 tests",
        "returncode": 1,
        "failure_category": "missing_pytest_fixture",
        "failure_signal": "missing_fixture:mocker",
        "diagnostic_summary": "A pytest fixture plugin is missing.",
    }
