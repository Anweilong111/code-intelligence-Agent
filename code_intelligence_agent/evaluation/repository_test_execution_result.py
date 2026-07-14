from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any


def execute_repository_test_plan(
    execution_plan: dict[str, Any],
    *,
    repository_root: str | Path | None = None,
    timeout: int = 20,
    python_executable: str | Path | None = None,
    python_executable_source: str = "current_interpreter",
    runner=None,
) -> dict[str, Any]:
    command = str(execution_plan.get("recommended_execution_command") or "").strip()
    level = str(execution_plan.get("recommended_execution_level") or "")
    risk = str(execution_plan.get("recommended_execution_risk") or "")
    scope = str(execution_plan.get("recommended_execution_scope") or "")
    planned_environment_variables = _safe_environment_variables(
        _dict(execution_plan.get("planned_environment_variables"))
    )
    root_value = repository_root or execution_plan.get("repository_root") or None
    resolved_python = str(python_executable or sys.executable)
    resolved_python_source = str(python_executable_source or "current_interpreter")
    if not command:
        return _skipped(
            command=command,
            level=level,
            risk=risk,
            scope=scope,
            python_executable=resolved_python,
            python_executable_source=resolved_python_source,
            planned_environment_variables=planned_environment_variables,
            reason="no_planned_command",
            message="Repository test execution plan did not produce a runnable command.",
        )
    if not bool(execution_plan.get("executable_now", False)):
        return _skipped(
            command=command,
            level=level,
            risk=risk,
            scope=scope,
            python_executable=resolved_python,
            python_executable_source=resolved_python_source,
            planned_environment_variables=planned_environment_variables,
            reason="plan_not_executable",
            message="Planned repository test command is not executable in the current context.",
        )
    if root_value is None:
        return _skipped(
            command=command,
            level=level,
            risk=risk,
            scope=scope,
            python_executable=resolved_python,
            python_executable_source=resolved_python_source,
            planned_environment_variables=planned_environment_variables,
            reason="repository_root_missing",
            message="Planned repository test command requires a full repository checkout.",
        )
    repo_path = Path(root_value)
    if not repo_path.exists() or not repo_path.is_dir():
        return _skipped(
            command=command,
            level=level,
            risk=risk,
            scope=scope,
            cwd=str(repo_path),
            python_executable=resolved_python,
            python_executable_source=resolved_python_source,
            planned_environment_variables=planned_environment_variables,
            reason="repository_root_missing",
            message="Repository test root does not exist or is not a directory.",
        )
    working_dir = str(execution_plan.get("recommended_working_dir") or "")
    execution_cwd = _execution_cwd(repo_path, working_dir)
    if execution_cwd is None or not execution_cwd.exists() or not execution_cwd.is_dir():
        return _skipped(
            command=command,
            level=level,
            risk=risk,
            scope=scope,
            cwd=str(execution_cwd or repo_path),
            python_executable=resolved_python,
            python_executable_source=resolved_python_source,
            planned_environment_variables=planned_environment_variables,
            reason="selected_working_dir_missing",
            message="Planned repository test working directory is missing or unsafe.",
        )
    command_args = _safe_python_module_command(
        command,
        python_executable=resolved_python,
    )
    if not command_args:
        return _skipped(
            command=command,
            level=level,
            risk=risk,
            scope=scope,
            cwd=str(execution_cwd),
            python_executable=resolved_python,
            python_executable_source=resolved_python_source,
            planned_environment_variables=planned_environment_variables,
            reason="unsupported_command",
            message="Only python -m module style planned commands are executed.",
        )
    execution_runner = str(
        execution_plan.get("recommended_execution_runner")
        or _module_from_command_args(command_args)
    )
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(planned_environment_variables)
    automatic_environment_variables = _automatic_environment_variables(
        execution_cwd,
        env=env,
    )
    env.update(automatic_environment_variables)
    run = runner or subprocess.run
    try:
        completed = run(
            command_args,
            cwd=execution_cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        failure_context = _failure_context_excerpt(
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or ""),
            failure_signal=f"timeout>{timeout}s",
        )
        return {
            "status": "fail",
            "executed": True,
            "reason": "timeout",
            "message": f"Planned repository test command exceeded {timeout}s timeout.",
            "command": command,
            "command_args": command_args,
            "execution_level": level,
            "execution_risk": risk,
            "execution_scope": scope,
            "cwd": str(execution_cwd),
            "repository_root": str(repo_path),
            "working_dir": working_dir,
            "python_executable": resolved_python,
            "python_executable_source": resolved_python_source,
            "planned_environment_variables": planned_environment_variables,
            "planned_environment_variable_names": sorted(
                planned_environment_variables
            ),
            "automatic_environment_variables": automatic_environment_variables,
            "automatic_environment_variable_names": sorted(
                automatic_environment_variables
            ),
            "returncode": -1,
            "timeout": True,
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "test_count": 0,
            "test_count_source": "",
            "parsed_test_counts": _empty_test_count_summary(),
            "failure_category": "timeout",
            "failure_signal": f"timeout>{timeout}s",
            "diagnostic_summary": (
                "The planned repository test command did not finish before the timeout."
            ),
            "failure_context": failure_context,
            "failure_context_line_count": _line_count(failure_context),
            "stdout_preview": _preview(exc.stdout or ""),
            "stderr_preview": _preview(exc.stderr or ""),
            "next_actions": [
                "Increase repository_test_timeout or choose a narrower planned command.",
            ],
        }
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    success = completed.returncode == 0
    diagnostic = _diagnose_execution_result(
        stdout=stdout,
        stderr=stderr,
        success=success,
        returncode=completed.returncode,
        runner_module=execution_runner,
        command=command,
    )
    test_counts = _test_count_summary(
        stdout=stdout,
        stderr=stderr,
        success=success,
    )
    failure_context = _failure_context_excerpt(
        stdout=stdout,
        stderr=stderr,
        failure_signal=str(diagnostic.get("failure_signal") or ""),
    )
    return {
        "status": "pass" if success else "fail",
        "executed": True,
        "reason": "command_returncode",
        "message": (
            "Planned repository test command completed successfully."
            if success
            else "Planned repository test command returned a non-zero exit code."
        ),
        "command": command,
        "command_args": command_args,
        "execution_level": level,
        "execution_risk": risk,
        "execution_scope": scope,
        "execution_runner": execution_runner,
        "cwd": str(execution_cwd),
        "repository_root": str(repo_path),
        "working_dir": working_dir,
        "python_executable": resolved_python,
        "python_executable_source": resolved_python_source,
        "planned_environment_variables": planned_environment_variables,
        "planned_environment_variable_names": sorted(planned_environment_variables),
        "automatic_environment_variables": automatic_environment_variables,
        "automatic_environment_variable_names": sorted(automatic_environment_variables),
        "returncode": completed.returncode,
        "timeout": False,
        "passed": _int(test_counts.get("passed", 0)),
        "failed": _int(test_counts.get("failed", 0)),
        "errors": _int(test_counts.get("errors", 0)),
        "skipped": _int(test_counts.get("skipped", 0)),
        "test_count": _int(test_counts.get("total", 0)),
        "test_count_source": str(test_counts.get("source") or ""),
        "parsed_test_counts": test_counts,
        "failure_category": diagnostic["failure_category"],
        "failure_signal": diagnostic["failure_signal"],
        "diagnostic_summary": diagnostic["diagnostic_summary"],
        "failure_context": failure_context,
        "failure_context_line_count": _line_count(failure_context),
        "stdout_preview": _preview(stdout),
        "stderr_preview": _preview(stderr),
        "next_actions": []
        if success
        else diagnostic["next_actions"],
    }


def render_repository_test_execution_result_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Repository Test Execution Result",
        "",
        f"- Status: `{_markdown_cell(payload.get('status', ''))}`",
        f"- Executed: {str(bool(payload.get('executed', False))).lower()}",
        f"- Reason: `{_markdown_cell(payload.get('reason', ''))}`",
        f"- Command: `{_markdown_cell(payload.get('command', ''))}`",
        f"- Execution Level: `{_markdown_cell(payload.get('execution_level', ''))}`",
        f"- Execution Risk: `{_markdown_cell(payload.get('execution_risk', ''))}`",
        f"- Execution Scope: `{_markdown_cell(payload.get('execution_scope', ''))}`",
        f"- Execution Runner: `{_markdown_cell(payload.get('execution_runner', ''))}`",
        f"- Repository Root: `{_markdown_cell(payload.get('repository_root') or 'none')}`",
        f"- Working Dir: `{_markdown_cell(payload.get('working_dir') or '.')}`",
        f"- CWD: `{_markdown_cell(payload.get('cwd', ''))}`",
        f"- Python Executable: `{_markdown_cell(payload.get('python_executable', ''))}`",
        (
            "- Python Executable Source: "
            f"`{_markdown_cell(payload.get('python_executable_source', ''))}`"
        ),
        (
            "- Planned Environment Variables: "
            f"{', '.join(str(item) for item in _list(payload.get('planned_environment_variable_names'))) or 'none'}"
        ),
        (
            "- Automatic Environment Variables: "
            f"{', '.join(str(item) for item in _list(payload.get('automatic_environment_variable_names'))) or 'none'}"
        ),
        f"- Return Code: {_markdown_cell(payload.get('returncode'))}",
        f"- Timeout: {str(bool(payload.get('timeout', False))).lower()}",
        f"- Test Count: {_int(payload.get('test_count', 0))}",
        f"- Test Count Source: `{_markdown_cell(payload.get('test_count_source', ''))}`",
        f"- Passed: {_int(payload.get('passed', 0))}",
        f"- Failed: {_int(payload.get('failed', 0))}",
        f"- Errors: {_int(payload.get('errors', 0))}",
        f"- Skipped: {_int(payload.get('skipped', 0))}",
        f"- Failure Category: `{_markdown_cell(payload.get('failure_category', ''))}`",
        f"- Failure Signal: `{_markdown_cell(payload.get('failure_signal', ''))}`",
        f"- Failure Context Lines: {_int(payload.get('failure_context_line_count', 0))}",
        "",
        "## Message",
        "",
        _markdown_cell(payload.get("message", "")) or "none",
        "",
        "## Diagnostic Summary",
        "",
        _markdown_cell(payload.get("diagnostic_summary", "")) or "none",
        "",
        "## Failure Context",
        "",
        "```text",
        str(payload.get("failure_context") or ""),
        "```",
        "",
        "## Next Actions",
        "",
    ]
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


def write_repository_test_execution_result_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_execution_result.json"
    markdown_path = root / "repository_test_execution_result.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_execution_result_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_execution_result_json": str(json_path),
        "repository_test_execution_result_markdown": str(markdown_path),
    }


def _safe_python_module_command(
    command: str,
    *,
    python_executable: str | Path | None = None,
) -> list[str]:
    try:
        args = shlex.split(command)
    except ValueError:
        return []
    if len(args) < 3:
        return []
    executable = Path(args[0]).name.lower()
    if executable not in {
        "python",
        "python.exe",
        "python3",
        "python3.exe",
        "py",
        "py.exe",
    }:
        return []
    if args[1] != "-m":
        return []
    module = args[2]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", module):
        return []
    return [str(python_executable or sys.executable), "-m", module, *args[3:]]


def _module_from_command_args(command_args: list[str]) -> str:
    if len(command_args) >= 3 and command_args[1] == "-m":
        return str(command_args[2])
    return ""


def _execution_cwd(repository_root: Path, working_dir: str) -> Path | None:
    normalized = str(working_dir or "").replace("\\", "/").strip().strip("/")
    if not normalized:
        return repository_root
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        return None
    return repository_root.joinpath(*pure.parts)


def _skipped(
    *,
    command: str,
    level: str,
    risk: str,
    scope: str,
    reason: str,
    message: str,
    cwd: str = "",
    python_executable: str = "",
    python_executable_source: str = "",
    planned_environment_variables: dict[str, str] | None = None,
) -> dict[str, Any]:
    planned_environment_variables = dict(planned_environment_variables or {})
    return {
        "status": "skipped",
        "executed": False,
        "reason": reason,
        "message": message,
        "command": command,
        "command_args": [],
        "execution_level": level,
        "execution_risk": risk,
        "execution_scope": scope,
        "cwd": cwd,
        "python_executable": python_executable,
        "python_executable_source": python_executable_source,
        "planned_environment_variables": planned_environment_variables,
        "planned_environment_variable_names": sorted(planned_environment_variables),
        "returncode": None,
        "timeout": False,
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "test_count": 0,
        "test_count_source": "",
        "parsed_test_counts": _empty_test_count_summary(),
        "failure_category": "not_executed",
        "failure_signal": reason,
        "diagnostic_summary": message,
        "failure_context": "",
        "failure_context_line_count": 0,
        "stdout_preview": "",
        "stderr_preview": "",
        "next_actions": _skipped_next_actions(reason),
    }


def _diagnose_execution_result(
    *,
    stdout: str,
    stderr: str,
    success: bool,
    returncode: int,
    runner_module: str = "",
    command: str = "",
) -> dict[str, Any]:
    if success:
        return {
            "failure_category": "none",
            "failure_signal": "",
            "diagnostic_summary": "The planned repository test command passed.",
            "next_actions": [],
        }
    combined = f"{stderr}\n{stdout}"
    lowered = combined.lower()
    missing_module = _missing_module_name(combined)
    missing_runner = _missing_runner_module_name(
        missing_module=missing_module,
        runner_module=runner_module,
    )
    if missing_runner:
        return {
            "failure_category": "missing_test_runner",
            "failure_signal": f"missing_runner:{missing_runner}",
            "diagnostic_summary": (
                "The planned test command could not start because the selected test runner module is not installed in the isolated environment."
            ),
            "next_actions": [
                (
                    f"Install `{missing_runner}` in the isolated test environment "
                    "or select a planned command that uses an installed runner."
                ),
                "Regenerate or retry the repository test execution plan after runner setup is aligned.",
            ],
        }
    if missing_module:
        return {
            "failure_category": "missing_dependency",
            "failure_signal": f"missing_module:{missing_module}",
            "diagnostic_summary": (
                "The test command failed because a repository dependency could not be imported."
            ),
            "next_actions": [
                (
                    "Install repository dependencies in the isolated test environment "
                    f"or add `{missing_module}` to the dependency manifest."
                ),
                "Rerun with --run-repository-test-environment-setup after dependency planning succeeds.",
            ],
        }
    missing_native_extension = _missing_native_extension_signal(combined)
    if missing_native_extension:
        return {
            "failure_category": "missing_native_extension",
            "failure_signal": missing_native_extension,
            "diagnostic_summary": (
                "Repository imports reached a package that requires a compiled "
                "native extension, but the binary artifact is unavailable."
            ),
            "next_actions": [
                "Build or install the repository's native extension in a compatible isolated environment.",
                "Do not classify this startup failure as an application-code defect.",
            ],
        }
    missing_fixture = _missing_pytest_fixture_name(combined)
    if missing_fixture:
        plugin_hint = _pytest_fixture_plugin_hint(missing_fixture)
        next_actions = [
            (
                "Install repository pytest plugins/dependencies or verify "
                f"that the `{missing_fixture}` fixture is provided by conftest.py."
            ),
            "Retry after environment setup passes; if it still fails, inspect pytest fixture/plugin configuration.",
        ]
        if plugin_hint:
            next_actions.insert(
                0,
                (
                    f"The `{missing_fixture}` fixture is commonly provided by "
                    f"`{plugin_hint}`; add or install that pytest plugin if the "
                    "repository dependency files reference it."
                ),
            )
        return {
            "failure_category": "missing_pytest_fixture",
            "failure_signal": f"missing_fixture:{missing_fixture}",
            "diagnostic_summary": (
                "Pytest collected tests but failed during setup because a required fixture was unavailable."
            ),
            "next_actions": next_actions,
        }
    if "syntaxerror:" in lowered or "indentationerror:" in lowered:
        return {
            "failure_category": "syntax_error",
            "failure_signal": _first_matching_line(
                combined,
                ("SyntaxError:", "IndentationError:"),
            ),
            "diagnostic_summary": (
                "The repository tests failed during Python parsing before normal assertions ran."
            ),
            "next_actions": [
                "Inspect the syntax error location in stderr and verify the checkout/ref is valid.",
                "Skip patch generation until the repository source imports cleanly.",
            ],
        }
    import_path_signal = _import_path_error_signal(combined)
    if import_path_signal:
        return {
            "failure_category": "import_path_error",
            "failure_signal": import_path_signal,
            "diagnostic_summary": (
                "Pytest failed because the checkout/package import path does not match the selected test command."
            ),
            "next_actions": [
                "Verify repository_test_root points at the package root used by pytest.",
                "Check target_prefix, src-layout package discovery, and duplicate test module names.",
                "Retry with the narrow planned command before broad pytest discovery.",
            ],
        }
    framework_config_signal = _framework_configuration_error_signal(combined)
    if framework_config_signal:
        return {
            "failure_category": "framework_configuration_error",
            "failure_signal": framework_config_signal,
            "diagnostic_summary": (
                "Pytest reached repository code but failed because a framework test configuration was missing."
            ),
            "next_actions": [
                "Run repository environment setup and load project-specific test settings before retrying.",
                (
                    "For Django projects, provide DJANGO_SETTINGS_MODULE or pytest-django "
                    "configuration that matches the checked-out project."
                ),
                "Inspect pytest.ini, pyproject.toml, conftest.py, and framework-specific test bootstrap files.",
            ],
        }
    warning_error_signal = _pytest_warning_as_error_signal(combined)
    if warning_error_signal:
        return {
            "failure_category": "pytest_warning_as_error",
            "failure_signal": warning_error_signal,
            "diagnostic_summary": (
                "Pytest failed during collection because repository warning policy turned warnings into errors."
            ),
            "next_actions": [
                "Inspect pytest warning filters in setup.cfg, pytest.ini, pyproject.toml, or tox.ini.",
                "Use an environment compatible with the repository's pinned pytest/Python versions before treating this as a code bug.",
                "If needed, retry with the same narrow command after dependency and toolchain setup.",
            ],
        }
    if "error collecting" in lowered or "importerror while importing test module" in lowered:
        return {
            "failure_category": "pytest_collection_error",
            "failure_signal": _first_matching_line(
                combined,
                ("ERROR collecting", "ImportError while importing test module"),
            ),
            "diagnostic_summary": (
                "Pytest failed while collecting tests, usually before executing test bodies."
            ),
            "next_actions": [
                "Inspect collection stderr for missing imports, invalid test paths, or plugin issues.",
                "Use the narrow planned command first, then expand only after collection passes.",
            ],
        }
    if "no tests ran" in lowered or "collected 0 items" in lowered:
        return {
            "failure_category": "no_tests_collected",
            "failure_signal": _first_matching_line(
                combined,
                ("no tests ran", "collected 0 items"),
            ),
            "diagnostic_summary": (
                "The planned command executed but did not discover runnable tests."
            ),
            "next_actions": [
                "Check repository_profile.json test_source_paths and pytest configuration.",
                "Provide a repository_test_root that matches the profiled checkout ref.",
            ],
        }
    if "error: usage:" in lowered or "unrecognized arguments" in lowered:
        return {
            "failure_category": "command_usage_error",
            "failure_signal": _first_matching_line(
                combined,
                ("ERROR: usage:", "unrecognized arguments"),
            ),
            "diagnostic_summary": (
                "The planned command reached the test runner but the arguments were rejected."
            ),
            "next_actions": [
                "Inspect the generated repository_test_execution_plan.json command candidates.",
                "Prefer a simpler python -m pytest -q command before using project-specific options.",
            ],
        }
    tox_python_signal = _tox_missing_python_signal(combined)
    if tox_python_signal:
        return {
            "failure_category": "tox_missing_python_interpreter",
            "failure_signal": tox_python_signal,
            "diagnostic_summary": (
                "Tox started but could not find one or more Python interpreters required by the repository envlist."
            ),
            "next_actions": [
                "Retry with a low-risk pytest command that uses the current isolated Python environment.",
                "If full tox fidelity is required, install the Python versions requested by tox.ini.",
            ],
        }
    pytest_failure_signal = _pytest_assertion_failure_signal(combined)
    if pytest_failure_signal:
        return {
            "failure_category": "test_assertion_failure",
            "failure_signal": pytest_failure_signal,
            "diagnostic_summary": (
                "The planned command collected tests and at least one test assertion failed."
            ),
            "next_actions": [
                "Inspect failing test names and use them as dynamic evidence for bug localization.",
                "Keep the narrow failing test command for patch validation.",
            ],
        }
    nodeid_scoped_signal = _nodeid_scoped_pytest_failure_signal(
        command=command,
        returncode=returncode,
        runner_module=runner_module,
    )
    if nodeid_scoped_signal:
        return {
            "failure_category": "test_assertion_failure",
            "failure_signal": nodeid_scoped_signal,
            "diagnostic_summary": (
                "The nodeid-scoped pytest command failed after collection; treat the selected test as dynamic evidence."
            ),
            "next_actions": [
                "Use the nodeid-scoped pytest command as dynamic evidence for bug localization.",
                "Validate generated patches with the same nodeid-scoped pytest command.",
            ],
        }
    unittest_failure_signal = _unittest_failure_signal(combined)
    if unittest_failure_signal:
        return {
            "failure_category": "test_assertion_failure",
            "failure_signal": unittest_failure_signal,
            "diagnostic_summary": (
                "The unittest command ran tests and at least one test failed or errored."
            ),
            "next_actions": [
                "Use the failing unittest test identifier as dynamic evidence for bug localization.",
                "Validate generated patches with the same unittest command.",
            ],
        }
    return {
        "failure_category": "command_failed",
        "failure_signal": f"returncode:{returncode}",
        "diagnostic_summary": (
            "The planned repository test command failed without a more specific known signature."
        ),
        "next_actions": [
            "Inspect planned command stderr/stdout preview and verify repository dependencies.",
            "If the planned command is full/high-risk, rerun with a narrower pytest selection.",
        ],
    }


def _missing_module_name(text: str) -> str:
    patterns = (
        r"ModuleNotFoundError:\s+No module named ['\"]([^'\"]+)['\"]",
        r"ImportError:\s+No module named ['\"]([^'\"]+)['\"]",
        r"No module named ['\"]([^'\"]+)['\"]",
        r"No module named\s+([A-Za-z_][A-Za-z0-9_.]*)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def _missing_native_extension_signal(text: str) -> str:
    patterns = (
        r"[^\n]*binary is missing![^\n]*",
        r"[^\n]*DLL load failed[^\n]*",
        r"[^\n]*cannot open shared object file[^\n]*",
        r"[^\n]*undefined symbol[^\n]*",
        r"[^\n]*native extension[^\n]*(?:missing|not found|unavailable)[^\n]*",
    )
    for pattern in patterns:
        line = _first_matching_regex_line(text, pattern)
        if line:
            return line
    return ""


def _missing_runner_module_name(*, missing_module: str, runner_module: str) -> str:
    missing = str(missing_module or "").strip()
    runner = str(runner_module or "").strip()
    if missing and runner and missing == runner:
        return missing
    return ""


def _tox_missing_python_signal(text: str) -> str:
    if "could not find python interpreter matching any of the specs" not in text.lower():
        return ""
    return _first_matching_line(
        text,
        ("could not find python interpreter matching any of the specs",),
    )


def _missing_pytest_fixture_name(text: str) -> str:
    patterns = (
        r"fixture ['\"]([^'\"]+)['\"] not found",
        r"fixture ['\"]([^'\"]+)['\"]\s+not found",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _import_path_error_signal(text: str) -> str:
    regex_patterns = (
        r"ImportPathMismatchError[^\n]*",
        r"import file mismatch[^\n]*",
        r"attempted relative import with no known parent package[^\n]*",
        r"attempted relative import beyond top-level package[^\n]*",
    )
    for pattern in regex_patterns:
        line = _first_matching_regex_line(text, pattern)
        if line:
            return line
    return ""


def _framework_configuration_error_signal(text: str) -> str:
    django_tokens = (
        "django.core.exceptions.improperlyconfigured",
        "django_settings_module",
        "requested setting",
        "settings are not configured",
        "configured django settings",
    )
    lowered = text.lower()
    if any(token in lowered for token in django_tokens):
        line = _first_matching_line(
            text,
            (
                "django.core.exceptions.ImproperlyConfigured",
                "DJANGO_SETTINGS_MODULE",
                "settings are not configured",
                "Requested setting",
                "configured Django settings",
            ),
        )
        return line or "framework_config:django_settings_not_configured"
    regex_patterns = (
        r"RuntimeError:\s+Working outside of application context[^\n]*",
        r"ImproperlyConfigured[^\n]*",
    )
    for pattern in regex_patterns:
        line = _first_matching_regex_line(text, pattern)
        if line:
            return line
    return ""


def _pytest_warning_as_error_signal(text: str) -> str:
    lowered = text.lower()
    warning_tokens = (
        "warning",
        "warnings.warn",
        "pytestremoved",
        "deprecationwarning",
        "pytestwarning",
    )
    collection_tokens = (
        "error collecting",
        "pytest_generate_tests",
        "filterwarnings",
    )
    if not any(token in lowered for token in warning_tokens):
        return ""
    if not any(token in lowered for token in collection_tokens):
        return ""
    line = _first_matching_regex_line(
        text,
        r"(Pytest[A-Za-z0-9_]*Warning|DeprecationWarning|FutureWarning|UserWarning)[^\n]*",
    )
    return line or "pytest_warning_as_error"


def _pytest_assertion_failure_signal(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _failed_prefix_nodeid(stripped) or _inline_failed_nodeid(stripped):
            return stripped
    return ""


def _nodeid_scoped_pytest_failure_signal(
    *,
    command: str,
    returncode: int,
    runner_module: str,
) -> str:
    if returncode != 1:
        return ""
    if str(runner_module or "") != "pytest":
        return ""
    nodeids = _pytest_nodeids_from_command(command)
    if not nodeids:
        return ""
    return f"FAILED {nodeids[0]} (nodeid-scoped pytest command)"


def _unittest_failure_signal(text: str) -> str:
    if "FAILED (" not in text:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^(FAIL|ERROR):\s+\S+\s+\([^)]+\)", stripped):
            return stripped
    return ""


def _pytest_nodeids_from_command(command: str) -> list[str]:
    try:
        parts = shlex.split(command)
    except ValueError:
        return []
    if len(parts) < 3:
        return []
    executable = Path(parts[0]).name.lower()
    if executable not in {
        "python",
        "python.exe",
        "python3",
        "python3.exe",
        "py",
        "py.exe",
    }:
        return []
    if parts[1:3] != ["-m", "pytest"]:
        return []
    nodeids: list[str] = []
    skip_next = False
    options_with_values = {
        "--basetemp",
        "--cache-clear",
        "--color",
        "--confcutdir",
        "--ignore",
        "--ignore-glob",
        "--import-mode",
        "--junit-prefix",
        "--junitxml",
        "--maxfail",
        "--override-ini",
        "--rootdir",
        "--tb",
        "-c",
        "-k",
        "-m",
        "-o",
        "-p",
    }
    for part in parts[3:]:
        if skip_next:
            skip_next = False
            continue
        if part in options_with_values:
            skip_next = True
            continue
        if part.startswith("-"):
            continue
        if "=" in part and part.split("=", 1)[0] in options_with_values:
            continue
        nodeid = _safe_pytest_nodeid(part)
        if nodeid and nodeid not in nodeids:
            nodeids.append(nodeid)
    return nodeids


def _safe_pytest_nodeid(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or "::" not in text:
        return ""
    if any(char in text for char in ("\x00", "\n", "\r")):
        return ""
    path_text, separator, suffix = text.partition("::")
    if not separator or not path_text.endswith(".py"):
        return ""
    pure = PurePosixPath(path_text)
    if pure.is_absolute() or ".." in pure.parts:
        return ""
    node_parts = [part.strip() for part in _split_nodeid_parts(suffix)]
    if not node_parts or any(
        not part or any(char in part for char in ("\x00", "\n", "\r"))
        for part in node_parts
    ):
        return ""
    return f"{pure.as_posix()}::{'::'.join(node_parts)}"


def _split_nodeid_parts(value: str) -> list[str]:
    text = str(value or "")
    parts: list[str] = []
    start = 0
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]" and depth > 0:
            depth -= 1
        if depth == 0 and text.startswith("::", index):
            parts.append(text[start:index])
            index += 2
            start = index
            continue
        index += 1
    parts.append(text[start:])
    return parts


def _failed_prefix_nodeid(line: str) -> str:
    if not line.startswith("FAILED "):
        return ""
    candidate = _strip_pytest_nodeid_suffix(line[len("FAILED ") :].strip())
    return _safe_pytest_nodeid(candidate)


def _inline_failed_nodeid(line: str) -> str:
    candidate = _split_outside_brackets(line, " FAILED")
    return _safe_pytest_nodeid(candidate)


def _strip_pytest_nodeid_suffix(value: str) -> str:
    text = str(value or "").strip()
    if text.endswith(" (nodeid-scoped pytest command)"):
        text = text[: -len(" (nodeid-scoped pytest command)")].strip()
    return _split_outside_brackets(text, " - ")


def _split_outside_brackets(value: str, delimiter: str) -> str:
    text = str(value or "")
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]" and depth > 0:
            depth -= 1
        if depth == 0 and text.startswith(delimiter, index):
            return text[:index].strip()
        index += 1
    return text.strip()


def _first_matching_line(text: str, tokens: tuple[str, ...]) -> str:
    lowered_tokens = tuple(token.lower() for token in tokens)
    for line in text.splitlines():
        lowered = line.lower()
        if any(token in lowered for token in lowered_tokens):
            return line.strip()
    return ""


def _first_matching_regex_line(text: str, pattern: str) -> str:
    regex = re.compile(pattern)
    for line in text.splitlines():
        if regex.search(line):
            return line.strip()
    return ""


def _failure_context_excerpt(
    *,
    stdout: str,
    stderr: str,
    failure_signal: str = "",
    window_before: int = 2,
    window_after: int = 8,
    max_lines: int = 120,
    max_chars: int = 8000,
) -> str:
    lines: list[str] = []
    if failure_signal:
        lines.append(str(failure_signal))
    for source, text in (("stdout", stdout), ("stderr", stderr)):
        for line in str(text or "").splitlines():
            lines.append(f"[{source}] {line}")
    if not lines:
        return ""
    selected: set[int] = set()
    for index, line in enumerate(lines):
        if _failure_context_anchor(line):
            start = max(0, index - window_before)
            end = min(len(lines), index + window_after + 1)
            selected.update(range(start, end))
    if not selected and failure_signal:
        selected.add(0)
    excerpt_lines: list[str] = []
    previous_index = -1
    for index in sorted(selected):
        if len(excerpt_lines) >= max_lines:
            break
        if previous_index >= 0 and index > previous_index + 1:
            excerpt_lines.append("...")
        excerpt_lines.append(lines[index])
        previous_index = index
    excerpt = "\n".join(excerpt_lines)
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars].rstrip() + "\n...[truncated]"
    return excerpt


def _failure_context_anchor(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    normalized = re.sub(r"^\[(stdout|stderr)\]\s+", "", text)
    if any(
        token in text
        for token in (
            "Traceback (most recent call last):",
            "AssertionError",
            "FAILED (",
            "ERROR: ",
            "FAIL: ",
        )
    ):
        return True
    if _failed_prefix_nodeid(normalized) or _inline_failed_nodeid(normalized):
        return True
    if re.search(r'File "[^"]+\.py", line \d+, in [A-Za-z_][\w]*', normalized):
        return True
    if re.search(r"[^:\s][^:]*\.py:\d+:\s+in\s+[A-Za-z_][\w]*", normalized):
        return True
    return False


def _skipped_next_actions(reason: str) -> list[str]:
    if reason == "plan_not_executable":
        return [
            "Prepare the repository checkout and dependencies before executing the planned command.",
            "Use repository_test_execution_plan.md to inspect the planned level, risk, and selected tests.",
        ]
    if reason == "repository_root_missing":
        return ["Verify repository_test_root points to a local full repository checkout."]
    if reason == "unsupported_command":
        return ["Use a python -m module style planned command for safe non-shell execution."]
    if reason == "no_planned_command":
        return ["Inspect repository_profile.json and repository_test_execution_plan.json."]
    return []


def _safe_environment_variables(values: dict[str, Any]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in values.items():
        name = str(key)
        text = str(value)
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name):
            continue
        if not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?::[A-Za-z_][A-Za-z0-9_]*)?",
            text,
        ):
            continue
        safe[name] = text
    return safe


def _automatic_environment_variables(
    repository_root: Path,
    *,
    env: dict[str, str],
) -> dict[str, str]:
    src_path = repository_root / "src"
    if not _looks_like_src_layout(src_path):
        return {}
    src_text = str(src_path.resolve())
    existing = str(env.get("PYTHONPATH") or "")
    parts = [part for part in existing.split(os.pathsep) if part]
    normalized_src = _normalized_path(src_text)
    if any(_normalized_path(part) == normalized_src for part in parts):
        return {}
    value = os.pathsep.join([src_text, *parts]) if parts else src_text
    return {"PYTHONPATH": value}


def _looks_like_src_layout(src_path: Path) -> bool:
    if not src_path.exists() or not src_path.is_dir():
        return False
    for child in src_path.iterdir():
        if child.name.startswith("."):
            continue
        if child.is_file() and child.suffix == ".py":
            return True
        if child.is_dir() and (child / "__init__.py").exists():
            return True
    return False


def _pytest_fixture_plugin_hint(fixture: str) -> str:
    return {
        "aiohttp_client": "pytest-aiohttp",
        "aiohttp_server": "pytest-aiohttp",
        "benchmark": "pytest-benchmark",
        "cov": "pytest-cov",
        "django_assert_num_queries": "pytest-django",
        "django_db_blocker": "pytest-django",
        "httpbin": "pytest-httpbin",
        "httpbin_secure": "pytest-httpbin",
        "mocker": "pytest-mock",
        "requests_mock": "requests-mock",
        "subtests": "pytest-subtests",
    }.get(str(fixture or "").strip(), "")


def _normalized_path(value: str) -> str:
    try:
        return str(Path(value).resolve()).lower()
    except (OSError, RuntimeError, ValueError):
        return value.lower()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _preview(value: str, limit: int = 4000) -> str:
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _line_count(value: str) -> int:
    return len(str(value or "").splitlines())


def _test_count_summary(
    *,
    stdout: str,
    stderr: str,
    success: bool,
) -> dict[str, Any]:
    text = f"{stdout}\n{stderr}"
    pytest_counts = _pytest_count_summary(text)
    if pytest_counts.get("source"):
        return pytest_counts
    unittest_counts = _unittest_count_summary(text, success=success)
    if unittest_counts.get("source"):
        return unittest_counts
    return _empty_test_count_summary()


def _empty_test_count_summary() -> dict[str, Any]:
    return {
        "source": "",
        "total": 0,
        "passed": 0,
        "failed": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
    }


def _pytest_count_summary(text: str) -> dict[str, Any]:
    counts = _empty_test_count_summary()
    label_map = {
        "passed": "passed",
        "failed": "failures",
        "error": "errors",
        "errors": "errors",
        "skipped": "skipped",
    }
    for match in re.finditer(
        r"(?<![A-Za-z])(\d+)\s+"
        r"(passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b",
        str(text or ""),
        flags=re.IGNORECASE,
    ):
        label = match.group(2).lower()
        target = label_map.get(label)
        if not target:
            continue
        counts[target] = _int(counts.get(target, 0)) + int(match.group(1))
    if not any(_int(counts.get(key, 0)) for key in ("passed", "failures", "errors", "skipped")):
        return counts
    counts["source"] = "pytest_summary"
    counts["failed"] = _int(counts.get("failures", 0)) + _int(
        counts.get("errors", 0)
    )
    counts["total"] = (
        _int(counts.get("passed", 0))
        + _int(counts.get("failed", 0))
        + _int(counts.get("skipped", 0))
    )
    return counts


def _unittest_count_summary(text: str, *, success: bool) -> dict[str, Any]:
    counts = _empty_test_count_summary()
    ran_match = re.search(
        r"^\s*Ran\s+(\d+)\s+tests?\s+in\s+",
        str(text or ""),
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if not ran_match:
        return counts
    total = int(ran_match.group(1))
    summary_fields = _unittest_result_fields(text)
    failures = _int(summary_fields.get("failures", 0))
    errors = _int(summary_fields.get("errors", 0))
    skipped = _int(summary_fields.get("skipped", 0))
    failed = failures + errors
    passed = max(total - failed - skipped, 0)
    if success and not summary_fields:
        passed = total
    return {
        "source": "unittest_summary",
        "total": total,
        "passed": passed,
        "failed": failed,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
    }


def _unittest_result_fields(text: str) -> dict[str, int]:
    fields: dict[str, int] = {}
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not (
            stripped.startswith("FAILED (")
            or stripped.startswith("OK (")
        ):
            continue
        inside = stripped.partition("(")[2].rpartition(")")[0]
        for name, value in re.findall(r"([A-Za-z_]+)=(\d+)", inside):
            fields[name.lower()] = int(value)
    return fields


def _extract_count(output: str, label: str) -> int:
    match = re.search(rf"(\d+)\s+{label}", output)
    return int(match.group(1)) if match else 0


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")
