from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any


def plan_repository_test_pytest_plugin_repair(
    setup_plan: dict[str, Any] | None,
    retry_execution_result: dict[str, Any] | None,
) -> dict[str, Any]:
    setup = _dict(setup_plan)
    retry = _dict(retry_execution_result)
    fixture = _missing_fixture_name(retry)
    if not fixture:
        return _skipped_plan(
            setup,
            retry,
            reason="no_missing_pytest_fixture",
            message="Retry execution did not fail because of a missing pytest fixture.",
        )
    plugin_package = _pytest_fixture_plugin_hint(fixture)
    if not plugin_package:
        return _warning_plan(
            setup,
            retry,
            fixture=fixture,
            plugin_package="",
            reason="fixture_plugin_unknown",
            message=(
                "The missing pytest fixture does not have a known low-risk plugin "
                "mapping."
            ),
        )
    candidate = _matching_plugin_candidate(setup, plugin_package)
    if not candidate:
        return _warning_plan(
            setup,
            retry,
            fixture=fixture,
            plugin_package=plugin_package,
            reason="no_matching_pytest_plugin_candidate",
            message=(
                "The setup plan did not record a pytest plugin candidate matching "
                f"`{plugin_package}`."
            ),
        )
    venv_python = str(setup.get("venv_python") or "")
    if not venv_python:
        return _warning_plan(
            setup,
            retry,
            fixture=fixture,
            plugin_package=plugin_package,
            reason="venv_python_missing",
            message="The setup plan did not include an isolated venv Python path.",
            candidate=candidate,
        )
    requirement = str(candidate.get("requirement") or plugin_package)
    install_args = [venv_python, "-m", "pip", "install", requirement]
    return {
        "status": "pass",
        "reason": "pytest_plugin_repair_plan_built",
        "message": (
            "A single pytest plugin candidate can be installed in the isolated "
            "environment to repair the missing fixture retry failure."
        ),
        "fixture": fixture,
        "plugin_package": plugin_package,
        "plugin_requirement": requirement,
        "candidate_source": _dict(candidate),
        "candidate_sources": [
            item
            for item in _list(setup.get("pytest_plugin_dependency_candidate_sources"))
            if _normalize_package_name(_dict(item).get("package")) == plugin_package
        ],
        "venv_python": venv_python,
        "install_command": shlex.join(install_args),
        "install_command_args": install_args,
        "retry_failure_category": str(retry.get("failure_category") or ""),
        "retry_failure_signal": str(retry.get("failure_signal") or ""),
        "retry_command": str(retry.get("command") or retry.get("retry_command") or ""),
        "safe_to_execute": True,
        "next_actions": [
            (
                "Install only the matched pytest plugin in the existing isolated "
                "venv, then rerun the same retry command."
            )
        ],
    }


def execute_repository_test_pytest_plugin_repair(
    repair_plan: dict[str, Any] | None,
    *,
    enabled: bool = False,
    timeout: int = 120,
    runner=None,
) -> dict[str, Any]:
    plan = _dict(repair_plan)
    install_args = [str(item) for item in _list(plan.get("install_command_args"))]
    if not enabled:
        return _skipped_execution(
            plan,
            reason="execution_disabled",
            message="Pytest plugin repair execution is disabled.",
        )
    if str(plan.get("status") or "") != "pass":
        return _skipped_execution(
            plan,
            reason="repair_plan_not_ready",
            message="Pytest plugin repair plan is not ready to execute.",
        )
    if not bool(plan.get("safe_to_execute", False)):
        return _skipped_execution(
            plan,
            reason="repair_plan_not_safe",
            message="Pytest plugin repair plan was not marked safe to execute.",
        )
    if not install_args:
        return _skipped_execution(
            plan,
            reason="missing_install_command",
            message="Pytest plugin repair plan did not include an install command.",
        )
    venv_python = str(plan.get("venv_python") or "")
    if not venv_python:
        return _skipped_execution(
            plan,
            reason="venv_python_missing",
            message="Pytest plugin repair requires an isolated venv Python path.",
        )
    run = runner or subprocess.run
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = run(
            install_args,
            cwd=None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return _execution_result(
            plan,
            status="fail",
            reason="pytest_plugin_install_timeout",
            message="Pytest plugin repair install command timed out.",
            returncode=-1,
            timeout=True,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
        )
    success = completed.returncode == 0
    return _execution_result(
        plan,
        status="pass" if success else "fail",
        reason=(
            "pytest_plugin_install_executed"
            if success
            else "pytest_plugin_install_failed"
        ),
        message=(
            "Pytest plugin repair install command completed successfully."
            if success
            else "Pytest plugin repair install command failed."
        ),
        returncode=completed.returncode,
        timeout=False,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def render_repository_test_pytest_plugin_repair_markdown(
    payload: dict[str, Any],
) -> str:
    lines = [
        "# Repository Test Pytest Plugin Repair",
        "",
        f"- Status: `{_markdown_cell(payload.get('status', ''))}`",
        f"- Executed: {str(bool(payload.get('executed', False))).lower()}",
        f"- Reason: `{_markdown_cell(payload.get('reason', ''))}`",
        f"- Fixture: `{_markdown_cell(payload.get('fixture') or 'none')}`",
        f"- Plugin Package: `{_markdown_cell(payload.get('plugin_package') or 'none')}`",
        (
            "- Plugin Requirement: "
            f"`{_markdown_cell(payload.get('plugin_requirement') or 'none')}`"
        ),
        (
            "- Install Command: "
            f"`{_markdown_cell(payload.get('install_command') or 'none')}`"
        ),
        f"- Return Code: {_markdown_cell(payload.get('returncode'))}",
        f"- Timeout: {str(bool(payload.get('timeout', False))).lower()}",
        "",
        "## Message",
        "",
        _markdown_cell(payload.get("message", "")) or "none",
        "",
        "## Candidate Sources",
        "",
    ]
    for item in _list(payload.get("candidate_sources")):
        row = _dict(item)
        lines.append(
            "- "
            f"`{_markdown_cell(row.get('requirement') or '')}` "
            f"from `{_markdown_cell(row.get('source') or '')}`"
        )
    if not _list(payload.get("candidate_sources")):
        lines.append("- none")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Stdout Preview",
            "",
            "```text",
            str(payload.get("stdout_preview") or ""),
            "```",
            "",
            "## Stderr Preview",
            "",
            "```text",
            str(payload.get("stderr_preview") or ""),
            "```",
        ]
    )
    return "\n".join(lines)


def write_repository_test_pytest_plugin_repair_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_pytest_plugin_repair.json"
    markdown_path = root / "repository_test_pytest_plugin_repair.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_pytest_plugin_repair_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_pytest_plugin_repair_json": str(json_path),
        "repository_test_pytest_plugin_repair_markdown": str(markdown_path),
    }


def write_repository_test_pytest_plugin_repair_retry_execution_result_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    from code_intelligence_agent.evaluation.repository_test_retry_execution_result import (
        render_repository_test_retry_execution_result_markdown,
    )

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_pytest_plugin_repair_retry_execution_result.json"
    markdown_path = root / "repository_test_pytest_plugin_repair_retry_execution_result.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_retry_execution_result_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_pytest_plugin_repair_retry_execution_result_json": str(
            json_path
        ),
        "repository_test_pytest_plugin_repair_retry_execution_result_markdown": str(
            markdown_path
        ),
    }


def _skipped_plan(
    setup: dict[str, Any],
    retry: dict[str, Any],
    *,
    reason: str,
    message: str,
) -> dict[str, Any]:
    return {
        "status": "skipped",
        "reason": reason,
        "message": message,
        "fixture": "",
        "plugin_package": "",
        "plugin_requirement": "",
        "candidate_source": {},
        "candidate_sources": _list(
            setup.get("pytest_plugin_dependency_candidate_sources")
        ),
        "venv_python": str(setup.get("venv_python") or ""),
        "install_command": "",
        "install_command_args": [],
        "retry_failure_category": str(retry.get("failure_category") or ""),
        "retry_failure_signal": str(retry.get("failure_signal") or ""),
        "retry_command": str(retry.get("command") or retry.get("retry_command") or ""),
        "safe_to_execute": False,
        "next_actions": [],
    }


def _warning_plan(
    setup: dict[str, Any],
    retry: dict[str, Any],
    *,
    fixture: str,
    plugin_package: str,
    reason: str,
    message: str,
    candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_payload = _dict(candidate)
    requirement = str(candidate_payload.get("requirement") or "")
    return {
        "status": "warning",
        "reason": reason,
        "message": message,
        "fixture": fixture,
        "plugin_package": plugin_package,
        "plugin_requirement": requirement,
        "candidate_source": candidate_payload,
        "candidate_sources": [
            item
            for item in _list(setup.get("pytest_plugin_dependency_candidate_sources"))
            if not plugin_package
            or _normalize_package_name(_dict(item).get("package")) == plugin_package
        ],
        "venv_python": str(setup.get("venv_python") or ""),
        "install_command": "",
        "install_command_args": [],
        "retry_failure_category": str(retry.get("failure_category") or ""),
        "retry_failure_signal": str(retry.get("failure_signal") or ""),
        "retry_command": str(retry.get("command") or retry.get("retry_command") or ""),
        "safe_to_execute": False,
        "next_actions": _warning_next_actions(reason, fixture, plugin_package),
    }


def _skipped_execution(
    plan: dict[str, Any],
    *,
    reason: str,
    message: str,
) -> dict[str, Any]:
    payload = dict(plan)
    payload.update(
        {
            "status": "skipped",
            "executed": False,
            "reason": reason,
            "message": message,
            "returncode": None,
            "timeout": False,
            "stdout_preview": "",
            "stderr_preview": "",
            "next_actions": _execution_skipped_actions(reason),
        }
    )
    return payload


def _execution_result(
    plan: dict[str, Any],
    *,
    status: str,
    reason: str,
    message: str,
    returncode: int,
    timeout: bool,
    stdout: str,
    stderr: str,
) -> dict[str, Any]:
    next_actions = []
    if status != "pass":
        next_actions.append(
            "Inspect pytest plugin repair stdout/stderr before rerunning repository tests."
        )
    else:
        next_actions.append("Rerun the same repository test retry command.")
    payload = dict(plan)
    payload.update(
        {
            "status": status,
            "executed": True,
            "reason": reason,
            "message": message,
            "returncode": returncode,
            "timeout": timeout,
            "stdout_preview": _preview(stdout),
            "stderr_preview": _preview(stderr),
            "next_actions": next_actions,
        }
    )
    return payload


def _missing_fixture_name(retry: dict[str, Any]) -> str:
    if str(retry.get("failure_category") or "") != "missing_pytest_fixture":
        return ""
    signal = str(retry.get("failure_signal") or "").strip()
    prefix = "missing_fixture:"
    if signal.startswith(prefix):
        return signal[len(prefix) :].strip()
    return ""


def _matching_plugin_candidate(
    setup: dict[str, Any],
    plugin_package: str,
) -> dict[str, Any]:
    target = _normalize_package_name(plugin_package)
    for item in _list(setup.get("pytest_plugin_dependency_candidate_sources")):
        candidate = _dict(item)
        if _normalize_package_name(candidate.get("package")) == target:
            return candidate
    return {}


def _pytest_fixture_plugin_hint(fixture: str) -> str:
    return {
        "mocker": "pytest-mock",
        "httpbin": "pytest-httpbin",
        "httpbin_secure": "pytest-httpbin",
        "subtests": "pytest-subtests",
        "requests_mock": "requests-mock",
        "django_db": "pytest-django",
        "django_db_blocker": "pytest-django",
        "settings": "pytest-django",
        "client": "pytest-django",
        "aiohttp_client": "pytest-aiohttp",
        "aiohttp_server": "pytest-aiohttp",
        "event_loop": "pytest-asyncio",
    }.get(str(fixture or "").strip(), "")


def _warning_next_actions(
    reason: str,
    fixture: str,
    plugin_package: str,
) -> list[str]:
    if reason == "no_matching_pytest_plugin_candidate":
        return [
            (
                f"Check repository dependency files for `{plugin_package}` or "
                f"verify that `{fixture}` is provided by a local conftest.py."
            )
        ]
    if reason == "fixture_plugin_unknown":
        return [
            (
                f"Inspect pytest fixture `{fixture}` in conftest.py and repository "
                "plugin declarations."
            )
        ]
    if reason == "venv_python_missing":
        return ["Run repository test environment setup before fixture plugin repair."]
    return []


def _execution_skipped_actions(reason: str) -> list[str]:
    if reason == "execution_disabled":
        return ["Enable pytest plugin repair execution after a missing fixture retry."]
    if reason == "repair_plan_not_ready":
        return ["Inspect repository_test_pytest_plugin_repair.md before rerunning tests."]
    if reason == "venv_python_missing":
        return ["Run repository test environment setup before fixture plugin repair."]
    return []


def _normalize_package_name(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _preview(value: str, limit: int = 4000) -> str:
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")
