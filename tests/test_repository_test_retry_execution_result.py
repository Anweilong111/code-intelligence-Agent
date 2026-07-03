import subprocess
import sys
from pathlib import Path

from code_intelligence_agent.evaluation.repository_test_retry_execution_result import (
    execute_repository_test_retry_plan,
    render_repository_test_retry_execution_result_markdown,
    write_repository_test_retry_execution_result_artifacts,
)


def test_repository_test_retry_execution_skips_when_disabled(tmp_path):
    payload = execute_repository_test_retry_plan(
        _retry_plan(),
        repository_root=tmp_path,
        enabled=False,
    )
    paths = write_repository_test_retry_execution_result_artifacts(
        payload,
        tmp_path / "out",
    )
    markdown = render_repository_test_retry_execution_result_markdown(payload)

    assert payload["status"] == "skipped"
    assert payload["reason"] == "execution_disabled"
    assert payload["retry_enabled"] is False
    assert payload["executed"] is False
    assert "Repository Test Retry Execution Result" in markdown
    assert Path(paths["repository_test_retry_execution_result_json"]).exists()
    assert Path(paths["repository_test_retry_execution_result_markdown"]).exists()


def test_repository_test_retry_execution_waits_for_setup_prerequisite(tmp_path):
    payload = execute_repository_test_retry_plan(
        {
            **_retry_plan(),
            "retry_strategy": "run_environment_setup_then_retry",
        },
        repository_root=tmp_path,
        enabled=True,
        repository_test_environment_setup_result={
            "status": "skipped",
            "reason": "execution_disabled",
        },
    )

    assert payload["status"] == "skipped"
    assert payload["reason"] == "prerequisites_pending"
    assert payload["executed"] is False
    assert payload["retry_setup_prerequisite_required"] is True
    assert payload["retry_setup_prerequisite_status"] == "skipped"
    assert payload["retry_setup_prerequisite_satisfied"] is False
    assert any("environment setup" in action for action in payload["next_actions"])


def test_repository_test_retry_execution_runs_safe_retry_command(tmp_path):
    commands = []
    seen_env = {}

    def fake_runner(command, cwd, capture_output, text, timeout, check, env):
        del capture_output, text, timeout, check
        seen_env.update(env)
        commands.append((command, cwd))
        return subprocess.CompletedProcess(command, 0, "1 passed", "")

    payload = execute_repository_test_retry_plan(
        {
            **_retry_plan(),
            "planned_environment_variables": {
                "DJANGO_SETTINGS_MODULE": "mysite.settings",
            },
        },
        repository_root=tmp_path,
        enabled=True,
        runner=fake_runner,
    )

    assert payload["status"] == "pass"
    assert payload["executed"] is True
    assert payload["retry_enabled"] is True
    assert payload["retry_recommended"] is True
    assert payload["retry_strategy"] == "switch_to_narrow_pytest"
    assert payload["retry_setup_prerequisite_required"] is False
    assert payload["retry_setup_prerequisite_satisfied"] is True
    assert payload["command_args"][:3] == [sys.executable, "-m", "pytest"]
    assert payload["passed"] == 1
    assert commands[0][1] == tmp_path
    assert payload["planned_environment_variables"] == {
        "DJANGO_SETTINGS_MODULE": "mysite.settings"
    }
    assert seen_env["DJANGO_SETTINGS_MODULE"] == "mysite.settings"


def _retry_plan():
    return {
        "status": "pass",
        "reason": "no_tests_collected",
        "retry_recommended": True,
        "retry_strategy": "switch_to_narrow_pytest",
        "original_command": "python -m pytest -q",
        "retry_command": "python -m pytest -q tests/test_sample.py",
        "retry_level": "narrow",
        "retry_risk": "low",
        "failure_category": "no_tests_collected",
        "failure_signal": "collected 0 items",
    }
