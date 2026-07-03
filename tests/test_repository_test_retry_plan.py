from pathlib import Path

from code_intelligence_agent.evaluation.repository_test_retry_plan import (
    plan_repository_test_retry,
    render_repository_test_retry_plan_markdown,
    write_repository_test_retry_plan_artifacts,
)


def test_repository_test_retry_plan_skips_when_execution_passed(tmp_path):
    payload = plan_repository_test_retry(
        _execution_plan(),
        {
            "status": "pass",
            "executed": True,
            "command": "python -m pytest -q",
            "failure_category": "none",
        },
    )
    paths = write_repository_test_retry_plan_artifacts(payload, tmp_path / "out")
    markdown = render_repository_test_retry_plan_markdown(payload)

    assert payload["status"] == "skipped"
    assert payload["reason"] == "execution_passed"
    assert payload["retry_recommended"] is False
    assert payload["retry_strategy"] == "none"
    assert "Repository Test Retry Plan" in markdown
    assert Path(paths["repository_test_retry_plan_json"]).exists()
    assert Path(paths["repository_test_retry_plan_markdown"]).exists()


def test_repository_test_retry_plan_runs_setup_before_missing_dependency_retry():
    payload = plan_repository_test_retry(
        _execution_plan(),
        {
            "status": "fail",
            "executed": True,
            "command": "python -m pytest -q",
            "execution_level": "smoke",
            "execution_risk": "medium",
            "failure_category": "missing_dependency",
            "failure_signal": "missing_module:requests",
        },
        repository_test_environment={
            "recommended_install_command": "python -m pip install -e .",
        },
        repository_test_environment_setup={
            "install_command_supported": True,
            "install_command_args": [
                "C:/repo/.repo_test_venv/Scripts/python.exe",
                "-m",
                "pip",
                "install",
                "-e",
                ".",
            ],
        },
        repository_test_environment_setup_result={
            "status": "skipped",
            "reason": "execution_disabled",
        },
    )

    assert payload["status"] == "warning"
    assert payload["reason"] == "setup_then_retry"
    assert payload["retry_recommended"] is True
    assert payload["retry_strategy"] == "run_environment_setup_then_retry"
    assert payload["retry_command"] == "python -m pytest -q"
    assert any("pip install" in action for action in payload["prerequisite_actions"])


def test_repository_test_retry_plan_switches_to_installed_runner_when_runner_missing():
    payload = plan_repository_test_retry(
        {
            "candidate_commands": [
                {
                    "level": "full",
                    "risk": "high",
                    "command": "python -m pytest tests",
                    "runner": "pytest",
                },
                {
                    "level": "full",
                    "risk": "high",
                    "command": "python -m tox",
                    "runner": "tox",
                },
            ],
            "repository_root_present": True,
        },
        {
            "status": "fail",
            "executed": True,
            "command": "python -m pytest tests",
            "execution_level": "full",
            "execution_risk": "high",
            "failure_category": "missing_test_runner",
            "failure_signal": "missing_runner:pytest",
        },
        repository_test_environment={"test_module": "tox"},
        repository_test_environment_setup={
            "install_command_supported": True,
            "test_module": "tox",
        },
        repository_test_environment_setup_result={"status": "pass"},
    )

    assert payload["status"] == "pass"
    assert payload["reason"] == "missing_test_runner"
    assert payload["retry_recommended"] is True
    assert payload["retry_strategy"] == "switch_to_installed_test_runner"
    assert payload["retry_command"] == "python -m tox"
    assert payload["failure_category"] == "missing_test_runner"


def test_repository_test_retry_plan_runs_setup_before_missing_fixture_retry():
    payload = plan_repository_test_retry(
        _execution_plan(),
        {
            "status": "fail",
            "executed": True,
            "command": "python -m pytest -q tests/test_sample.py",
            "execution_level": "narrow",
            "execution_risk": "low",
            "failure_category": "missing_pytest_fixture",
            "failure_signal": "missing_fixture:client",
            "next_actions": [
                "Install repository pytest plugins/dependencies or verify fixture client.",
            ],
        },
        repository_test_environment={
            "recommended_install_command": "python -m pip install -e .",
        },
        repository_test_environment_setup={
            "install_command_supported": True,
            "install_command_args": [
                "C:/repo/.repo_test_venv/Scripts/python.exe",
                "-m",
                "pip",
                "install",
                "-e",
                ".",
            ],
        },
        repository_test_environment_setup_result={
            "status": "skipped",
            "reason": "execution_disabled",
        },
    )

    assert payload["status"] == "warning"
    assert payload["reason"] == "setup_then_retry_fixture_resolution"
    assert payload["retry_recommended"] is True
    assert payload["retry_strategy"] == "run_environment_setup_then_retry"
    assert payload["retry_command"] == "python -m pytest -q tests/test_sample.py"
    assert any("plugin" in action for action in payload["next_actions"])


def test_repository_test_retry_plan_switches_to_narrow_when_no_tests_collected():
    payload = plan_repository_test_retry(
        _execution_plan(),
        {
            "status": "fail",
            "executed": True,
            "command": "python -m pytest -q",
            "failure_category": "no_tests_collected",
            "failure_signal": "collected 0 items",
        },
    )

    assert payload["status"] == "pass"
    assert payload["retry_recommended"] is True
    assert payload["retry_strategy"] == "switch_to_narrow_pytest"
    assert payload["retry_command"] == "python -m pytest -q tests/test_sample.py"
    assert payload["retry_level"] == "narrow"


def test_repository_test_retry_plan_switches_to_narrow_for_import_path_error():
    payload = plan_repository_test_retry(
        _execution_plan(),
        {
            "status": "fail",
            "executed": True,
            "command": "python -m pytest -q",
            "failure_category": "import_path_error",
            "failure_signal": "ImportPathMismatchError: duplicate test module",
            "next_actions": [
                "Verify repository_test_root points at the package root used by pytest.",
            ],
        },
    )

    assert payload["status"] == "pass"
    assert payload["retry_recommended"] is True
    assert payload["retry_strategy"] == "switch_to_narrow_or_smoke_import_path"
    assert payload["retry_command"] == "python -m pytest -q tests/test_sample.py"
    assert payload["retry_level"] == "narrow"


def test_repository_test_retry_plan_runs_setup_before_framework_configuration_retry():
    payload = plan_repository_test_retry(
        _execution_plan(),
        {
            "status": "fail",
            "executed": True,
            "command": "python -m pytest -q tests/test_django_app.py",
            "execution_level": "narrow",
            "execution_risk": "low",
            "failure_category": "framework_configuration_error",
            "failure_signal": (
                "django.core.exceptions.ImproperlyConfigured: Requested setting "
                "INSTALLED_APPS, but settings are not configured."
            ),
            "next_actions": [
                "For Django projects, provide DJANGO_SETTINGS_MODULE or pytest-django configuration.",
            ],
        },
        repository_test_environment={
            "recommended_install_command": "python -m pip install -e .",
        },
        repository_test_environment_setup={
            "install_command_supported": True,
            "install_command_args": [
                "C:/repo/.repo_test_venv/Scripts/python.exe",
                "-m",
                "pip",
                "install",
                "-e",
                ".",
            ],
        },
        repository_test_environment_setup_result={
            "status": "skipped",
            "reason": "execution_disabled",
        },
    )

    assert payload["status"] == "warning"
    assert payload["reason"] == "setup_then_retry_framework_configuration"
    assert payload["retry_recommended"] is True
    assert payload["retry_strategy"] == "run_environment_setup_then_retry"
    assert payload["retry_command"] == "python -m pytest -q tests/test_django_app.py"
    assert any("DJANGO_SETTINGS_MODULE" in action for action in payload["next_actions"])


def test_repository_test_retry_plan_runs_setup_before_warning_policy_retry():
    payload = plan_repository_test_retry(
        _execution_plan(),
        {
            "status": "fail",
            "executed": True,
            "command": "python -m pytest -q tests",
            "execution_level": "narrow",
            "execution_risk": "low",
            "failure_category": "pytest_warning_as_error",
            "failure_signal": "pytest.PytestRemovedIn10Warning: deprecated",
            "next_actions": [
                "Inspect pytest warning filters in setup.cfg.",
            ],
        },
        repository_test_environment={
            "recommended_install_command": "python -m pip install -e .",
        },
        repository_test_environment_setup={
            "install_command_supported": True,
            "install_command_args": [
                "C:/repo/.repo_test_venv/Scripts/python.exe",
                "-m",
                "pip",
                "install",
                "-e",
                ".",
            ],
        },
        repository_test_environment_setup_result={
            "status": "skipped",
            "reason": "execution_disabled",
        },
    )

    assert payload["status"] == "warning"
    assert payload["reason"] == "setup_then_retry_warning_policy"
    assert payload["retry_recommended"] is True
    assert payload["retry_strategy"] == "run_environment_setup_then_retry"
    assert payload["retry_command"] == "python -m pytest -q tests"
    assert any("filterwarnings" in action for action in payload["next_actions"])


def test_repository_test_retry_plan_switches_from_full_runner_to_pytest_fallback():
    payload = plan_repository_test_retry(
        {
            "candidate_commands": [
                {
                    "level": "full",
                    "risk": "high",
                    "command": "python -m tox",
                    "runner": "tox",
                    "scope": "tox managed environments",
                },
                {
                    "level": "narrow",
                    "risk": "low",
                    "command": "python -m pytest -q tests/test_sample.py",
                    "runner": "pytest",
                    "scope": "1 selected test files",
                },
                {
                    "level": "smoke",
                    "risk": "medium",
                    "command": "python -m pytest -q",
                    "runner": "pytest",
                    "scope": "pytest discovery",
                },
            ],
            "repository_root_present": True,
        },
        {
            "status": "fail",
            "executed": True,
            "command": "python -m tox",
            "failure_category": "command_failed",
            "failure_signal": "returncode:1",
        },
    )

    assert payload["status"] == "pass"
    assert payload["retry_recommended"] is True
    assert payload["retry_strategy"] == "switch_to_narrow_or_smoke"
    assert payload["retry_command"] == "python -m pytest -q tests/test_sample.py"
    assert payload["retry_level"] == "narrow"


def test_repository_test_retry_plan_switches_after_tox_missing_python():
    payload = plan_repository_test_retry(
        {
            "candidate_commands": [
                {
                    "level": "full",
                    "risk": "high",
                    "command": "python -m tox",
                    "runner": "tox",
                    "scope": "tox managed environments",
                },
                {
                    "level": "narrow",
                    "risk": "low",
                    "command": "python -m pytest -q tests/test_sample.py",
                    "runner": "pytest",
                    "scope": "1 selected test files",
                },
            ],
            "repository_root_present": True,
        },
        {
            "status": "fail",
            "executed": True,
            "command": "python -m tox",
            "failure_category": "tox_missing_python_interpreter",
            "failure_signal": "could not find python interpreter matching py37",
        },
    )

    assert payload["status"] == "pass"
    assert payload["retry_recommended"] is True
    assert payload["retry_strategy"] == "switch_to_narrow_or_smoke"
    assert payload["reason"] == "tox_missing_python_interpreter"
    assert payload["retry_command"] == "python -m pytest -q tests/test_sample.py"
    assert payload["retry_risk"] == "low"


def test_repository_test_retry_plan_prefers_focused_pytest_after_tox_missing_python():
    payload = plan_repository_test_retry(
        {
            "candidate_commands": [
                {
                    "level": "full",
                    "risk": "high",
                    "command": "python -m tox",
                    "runner": "tox",
                    "scope": "tox managed environments",
                },
                {
                    "level": "ci",
                    "risk": "low",
                    "command": "python -m pytest tests",
                    "runner": "pytest",
                    "scope": "1 CI-selected test target",
                },
                {
                    "level": "focused",
                    "risk": "low",
                    "command": "python -m pytest -q --maxfail=1 tests",
                    "runner": "pytest",
                    "scope": "first failure from CI tests",
                },
                {
                    "level": "narrow",
                    "risk": "low",
                    "command": "python -m pytest -q package tests",
                    "runner": "pytest",
                    "scope": "2 selected test targets",
                },
            ],
            "repository_root_present": True,
        },
        {
            "status": "fail",
            "executed": True,
            "command": "python -m tox",
            "failure_category": "tox_missing_python_interpreter",
            "failure_signal": "could not find python interpreter matching py37",
        },
    )

    assert payload["status"] == "pass"
    assert payload["retry_recommended"] is True
    assert payload["retry_strategy"] == "switch_to_focused_or_narrow"
    assert payload["retry_command"] == "python -m pytest -q --maxfail=1 tests"
    assert payload["retry_level"] == "focused"
    assert payload["retry_risk"] == "low"


def test_repository_test_retry_plan_keeps_assertion_failure_for_localization():
    payload = plan_repository_test_retry(
        _execution_plan(),
        {
            "status": "fail",
            "executed": True,
            "command": "python -m pytest -q tests/test_sample.py",
            "failure_category": "test_assertion_failure",
            "failure_signal": "FAILED tests/test_sample.py::test_bug",
        },
    )

    assert payload["status"] == "warning"
    assert payload["retry_recommended"] is False
    assert payload["retry_strategy"] == "localize_from_failing_test"
    assert payload["retry_command"] == ""
    assert any("dynamic evidence" in action for action in payload["next_actions"])


def _execution_plan():
    return {
        "candidate_commands": [
            {
                "level": "narrow",
                "risk": "low",
                "command": "python -m pytest -q tests/test_sample.py",
                "scope": "1 selected test files",
            },
            {
                "level": "smoke",
                "risk": "medium",
                "command": "python -m pytest -q",
                "scope": "pytest discovery",
            },
        ],
        "repository_root_present": True,
    }
