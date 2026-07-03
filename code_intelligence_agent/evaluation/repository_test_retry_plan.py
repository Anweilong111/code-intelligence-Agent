from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any


def plan_repository_test_retry(
    execution_plan: dict[str, Any],
    execution_result: dict[str, Any],
    *,
    repository_test_environment: dict[str, Any] | None = None,
    repository_test_environment_setup: dict[str, Any] | None = None,
    repository_test_environment_setup_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failure_category = str(execution_result.get("failure_category") or "")
    original_command = str(execution_result.get("command") or "").strip()
    candidates = _list(execution_plan.get("candidate_commands"))
    setup = _dict(repository_test_environment_setup)
    setup_result = _dict(repository_test_environment_setup_result)
    environment = _dict(repository_test_environment)

    if str(execution_result.get("status") or "") == "pass":
        return _payload(
            status="skipped",
            reason="execution_passed",
            retry_recommended=False,
            retry_strategy="none",
            original_command=original_command,
            failure_category=failure_category or "none",
            failure_signal=str(execution_result.get("failure_signal") or ""),
            diagnostic_summary="The planned repository test passed; no retry is needed.",
            planned_environment_variables=_dict(
                execution_result.get("planned_environment_variables")
            ),
        )
    if not bool(execution_result.get("executed", False)):
        return _not_executed_retry_plan(
            execution_plan=execution_plan,
            execution_result=execution_result,
            setup=setup,
            setup_result=setup_result,
            original_command=original_command,
        )

    if failure_category == "missing_test_runner":
        return _missing_test_runner_retry_plan(
            execution_result=execution_result,
            setup=setup,
            setup_result=setup_result,
            original_command=original_command,
            environment=environment,
            candidates=candidates,
        )
    if failure_category == "missing_dependency":
        return _missing_dependency_retry_plan(
            execution_result=execution_result,
            setup=setup,
            setup_result=setup_result,
            original_command=original_command,
            environment=environment,
        )
    if failure_category == "missing_pytest_fixture":
        return _missing_pytest_fixture_retry_plan(
            execution_result=execution_result,
            setup=setup,
            setup_result=setup_result,
            original_command=original_command,
            environment=environment,
        )
    if failure_category == "import_path_error":
        retry = (
            _candidate_by_level(candidates, "narrow", exclude=original_command)
            or _candidate_by_level(candidates, "smoke", exclude=original_command)
        )
        if retry:
            return _retry_payload(
                strategy="switch_to_narrow_or_smoke_import_path",
                reason="import_path_error",
                retry=retry,
                original_command=original_command,
                execution_result=execution_result,
                diagnostic_summary=(
                    "Retry with a lower-risk command while preserving the same repository root and package layout evidence."
                ),
            )
        return _manual_payload(
            reason="import_path_configuration_required",
            strategy="inspect_import_path_or_target_prefix",
            original_command=original_command,
            execution_result=execution_result,
            diagnostic_summary=(
                "The test command failed before assertions because package import paths did not match the checkout layout."
            ),
            next_actions=list(_list(execution_result.get("next_actions"))),
        )
    if failure_category == "framework_configuration_error":
        return _framework_configuration_retry_plan(
            execution_result=execution_result,
            setup=setup,
            setup_result=setup_result,
            original_command=original_command,
            environment=environment,
        )
    if failure_category == "pytest_warning_as_error":
        return _pytest_warning_as_error_retry_plan(
            execution_result=execution_result,
            setup=setup,
            setup_result=setup_result,
            original_command=original_command,
            environment=environment,
        )
    if failure_category == "no_tests_collected":
        retry = _candidate_by_level(candidates, "narrow", exclude=original_command)
        if retry:
            return _retry_payload(
                strategy="switch_to_narrow_pytest",
                reason="no_tests_collected",
                retry=retry,
                original_command=original_command,
                execution_result=execution_result,
                diagnostic_summary=(
                    "Retry with the profiled test files instead of broad pytest discovery."
                ),
            )
        return _manual_payload(
            reason="no_runnable_test_selection",
            strategy="inspect_test_discovery",
            original_command=original_command,
            execution_result=execution_result,
            diagnostic_summary=(
                "No safe retry command is available because the profile did not contain selected test paths."
            ),
            next_actions=[
                "Inspect repository_profile.json test_source_paths and pytest configuration.",
                "Provide include filters or repository_test_root that expose runnable tests.",
            ],
        )
    if failure_category == "command_usage_error":
        retry = _candidate_by_level(candidates, "smoke", exclude=original_command)
        if retry:
            return _retry_payload(
                strategy="switch_to_smoke_pytest",
                reason="command_usage_error",
                retry=retry,
                original_command=original_command,
                execution_result=execution_result,
                diagnostic_summary=(
                    "Retry with the simpler smoke pytest command because the current arguments were rejected."
                ),
            )
    if failure_category in {
        "timeout",
        "pytest_collection_error",
        "command_failed",
        "tox_missing_python_interpreter",
    }:
        retry = _candidate_by_first_level(
            candidates,
            ("focused", "ci", "narrow", "smoke"),
            exclude=original_command,
        )
        if retry:
            retry_level = str(retry.get("level") or "")
            return _retry_payload(
                strategy=(
                    (
                        "focused_after_timeout"
                        if retry_level in {"focused", "ci"}
                        else "narrow_after_timeout"
                    )
                    if failure_category == "timeout"
                    else (
                        "switch_to_focused_or_narrow"
                        if retry_level in {"focused", "ci"}
                        else "switch_to_narrow_or_smoke"
                    )
                ),
                reason=failure_category,
                retry=retry,
                original_command=original_command,
                execution_result=execution_result,
                diagnostic_summary=(
                    "Retry with a lower-risk planned command before expanding back to the original command."
                ),
            )
    if failure_category == "test_assertion_failure":
        return _manual_payload(
            reason="preserve_failing_test_for_localization",
            strategy="localize_from_failing_test",
            original_command=original_command,
            execution_result=execution_result,
            diagnostic_summary=(
                "The command reached test execution; keep this failing command as dynamic evidence for localization."
            ),
            next_actions=[
                "Use the failing test names as dynamic evidence for fault localization.",
                "Validate generated patches with the same failing command before broadening test scope.",
            ],
        )

    return _manual_payload(
        reason=failure_category or "unknown_failure",
        strategy="manual_inspection",
        original_command=original_command,
        execution_result=execution_result,
        diagnostic_summary=(
            "No safe automatic retry command was selected for this failure category."
        ),
        next_actions=list(_list(execution_result.get("next_actions"))),
    )


def render_repository_test_retry_plan_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Repository Test Retry Plan",
        "",
        f"- Status: `{_markdown_cell(payload.get('status', ''))}`",
        f"- Reason: `{_markdown_cell(payload.get('reason', ''))}`",
        f"- Retry Recommended: {str(bool(payload.get('retry_recommended', False))).lower()}",
        f"- Retry Strategy: `{_markdown_cell(payload.get('retry_strategy', ''))}`",
        f"- Failure Category: `{_markdown_cell(payload.get('failure_category', ''))}`",
        f"- Failure Signal: `{_markdown_cell(payload.get('failure_signal', ''))}`",
        f"- Original Command: `{_markdown_cell(payload.get('original_command', '') or 'none')}`",
        f"- Retry Command: `{_markdown_cell(payload.get('retry_command', '') or 'none')}`",
        f"- Retry Level: `{_markdown_cell(payload.get('retry_level', '') or 'none')}`",
        f"- Retry Risk: `{_markdown_cell(payload.get('retry_risk', '') or 'none')}`",
        "",
        "## Diagnostic Summary",
        "",
        _markdown_cell(payload.get("diagnostic_summary", "")) or "none",
        "",
        "## Prerequisite Actions",
        "",
    ]
    for action in _list(payload.get("prerequisite_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("prerequisite_actions")):
        lines.append("- none")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    return "\n".join(lines)


def write_repository_test_retry_plan_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_retry_plan.json"
    markdown_path = root / "repository_test_retry_plan.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_retry_plan_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_retry_plan_json": str(json_path),
        "repository_test_retry_plan_markdown": str(markdown_path),
    }


def _not_executed_retry_plan(
    *,
    execution_plan: dict[str, Any],
    execution_result: dict[str, Any],
    setup: dict[str, Any],
    setup_result: dict[str, Any],
    original_command: str,
) -> dict[str, Any]:
    reason = str(execution_result.get("reason") or "")
    if reason == "plan_not_executable" and not bool(
        execution_plan.get("repository_root_present", False)
    ):
        return _manual_payload(
            reason="checkout_required",
            strategy="materialize_repository_checkout",
            original_command=original_command,
            execution_result=execution_result,
            diagnostic_summary=(
                "The planned test command cannot run until a full repository checkout is available."
            ),
            next_actions=[
                "Rerun with --checkout-repository-tests or provide --repository-test-root.",
            ],
        )
    if (
        bool(setup.get("install_command_supported", False))
        and str(setup_result.get("status") or "") == "skipped"
    ):
        return _manual_payload(
            reason="environment_setup_disabled",
            strategy="enable_environment_setup",
            original_command=original_command,
            execution_result=execution_result,
            diagnostic_summary=(
                "Dependency setup is supported but was not executed before the planned test command."
            ),
            next_actions=[
                "Rerun with --run-repository-test-environment-setup.",
                "After setup passes, rerun the planned repository test command.",
            ],
        )
    return _manual_payload(
        reason=reason or "not_executed",
        strategy="satisfy_execution_prerequisites",
        original_command=original_command,
        execution_result=execution_result,
        diagnostic_summary=str(execution_result.get("diagnostic_summary") or ""),
        next_actions=list(_list(execution_result.get("next_actions"))),
    )


def _missing_test_runner_retry_plan(
    *,
    execution_result: dict[str, Any],
    setup: dict[str, Any],
    setup_result: dict[str, Any],
    original_command: str,
    environment: dict[str, Any],
    candidates: list[Any],
) -> dict[str, Any]:
    if (
        bool(setup.get("install_command_supported", False))
        and str(setup_result.get("status") or "") != "pass"
    ):
        return _payload(
            status="warning",
            reason="setup_then_retry_test_runner",
            retry_recommended=True,
            retry_strategy="run_environment_setup_then_retry",
            original_command=original_command,
            retry_command=original_command,
            retry_level=str(execution_result.get("execution_level") or ""),
            retry_risk=str(execution_result.get("execution_risk") or ""),
            failure_category=str(execution_result.get("failure_category") or ""),
            failure_signal=str(execution_result.get("failure_signal") or ""),
            diagnostic_summary=(
                "The selected test runner is missing; run supported environment setup before retrying the planned command."
            ),
            planned_environment_variables=_dict(
                execution_result.get("planned_environment_variables")
            ),
            prerequisite_actions=[
                "Run repository test environment setup.",
                f"Install command: {_setup_install_command(setup, environment)}",
            ],
            next_actions=[
                "Rerun with --run-repository-test-environment-setup.",
                "Retry the planned command after the selected runner is available.",
            ],
        )

    missing_runner = _missing_runner_from_signal(
        str(execution_result.get("failure_signal") or "")
    )
    installed_runner = str(
        setup.get("test_module") or environment.get("test_module") or ""
    )
    if installed_runner and installed_runner != missing_runner:
        retry = _candidate_by_runner(
            candidates,
            installed_runner,
            exclude=original_command,
        )
        if retry:
            return _retry_payload(
                strategy="switch_to_installed_test_runner",
                reason="missing_test_runner",
                retry=retry,
                original_command=original_command,
                execution_result=execution_result,
                diagnostic_summary=(
                    "Retry with a planned command that uses the test runner already selected by environment setup."
                ),
            )

    next_actions = list(_list(execution_result.get("next_actions"))) or [
        (
            "Install the missing test runner in the isolated environment "
            "or choose a planned command that uses an installed runner."
        ),
        "Regenerate the execution plan after runner setup and command selection are aligned.",
    ]
    return _manual_payload(
        reason="test_runner_install_required",
        strategy="install_missing_test_runner",
        original_command=original_command,
        execution_result=execution_result,
        diagnostic_summary=(
            "The selected test runner is unavailable and no safe alternate installed-runner command was selected."
        ),
        next_actions=next_actions,
    )


def _missing_dependency_retry_plan(
    *,
    execution_result: dict[str, Any],
    setup: dict[str, Any],
    setup_result: dict[str, Any],
    original_command: str,
    environment: dict[str, Any],
) -> dict[str, Any]:
    if (
        bool(setup.get("install_command_supported", False))
        and str(setup_result.get("status") or "") != "pass"
    ):
        return _payload(
            status="warning",
            reason="setup_then_retry",
            retry_recommended=True,
            retry_strategy="run_environment_setup_then_retry",
            original_command=original_command,
            retry_command=original_command,
            retry_level=str(execution_result.get("execution_level") or ""),
            retry_risk=str(execution_result.get("execution_risk") or ""),
            failure_category=str(execution_result.get("failure_category") or ""),
            failure_signal=str(execution_result.get("failure_signal") or ""),
            diagnostic_summary=(
                "Run the supported isolated environment setup before retrying the same planned command."
            ),
            planned_environment_variables=_dict(
                execution_result.get("planned_environment_variables")
            ),
            prerequisite_actions=[
                "Run repository test environment setup.",
                f"Install command: {_setup_install_command(setup, environment)}",
            ],
            next_actions=[
                "Rerun with --run-repository-test-environment-setup.",
                "Retry the planned command after setup_result.status becomes pass.",
            ],
        )
    return _manual_payload(
        reason="dependency_manifest_update_required",
        strategy="manual_dependency_fix",
        original_command=original_command,
        execution_result=execution_result,
        diagnostic_summary=(
            "The isolated setup is unavailable or already passed, so the missing module likely requires dependency manifest updates."
        ),
        next_actions=list(_list(execution_result.get("next_actions"))),
    )


def _missing_pytest_fixture_retry_plan(
    *,
    execution_result: dict[str, Any],
    setup: dict[str, Any],
    setup_result: dict[str, Any],
    original_command: str,
    environment: dict[str, Any],
) -> dict[str, Any]:
    if (
        bool(setup.get("install_command_supported", False))
        and str(setup_result.get("status") or "") != "pass"
    ):
        return _payload(
            status="warning",
            reason="setup_then_retry_fixture_resolution",
            retry_recommended=True,
            retry_strategy="run_environment_setup_then_retry",
            original_command=original_command,
            retry_command=original_command,
            retry_level=str(execution_result.get("execution_level") or ""),
            retry_risk=str(execution_result.get("execution_risk") or ""),
            failure_category=str(execution_result.get("failure_category") or ""),
            failure_signal=str(execution_result.get("failure_signal") or ""),
            diagnostic_summary=(
                "A pytest fixture is missing; run supported dependency/plugin setup before retrying the same command."
            ),
            planned_environment_variables=_dict(
                execution_result.get("planned_environment_variables")
            ),
            prerequisite_actions=[
                "Run repository test environment setup.",
                f"Install command: {_setup_install_command(setup, environment)}",
            ],
            next_actions=[
                "Rerun with --run-repository-test-environment-setup.",
                "If the retry still reports a missing fixture, inspect conftest.py and pytest plugin configuration.",
            ],
        )
    return _manual_payload(
        reason="pytest_fixture_resolution_required",
        strategy="inspect_pytest_fixture_or_plugin",
        original_command=original_command,
        execution_result=execution_result,
        diagnostic_summary=(
            "The test command collected tests but a required pytest fixture was unavailable."
        ),
        next_actions=list(_list(execution_result.get("next_actions"))),
    )


def _framework_configuration_retry_plan(
    *,
    execution_result: dict[str, Any],
    setup: dict[str, Any],
    setup_result: dict[str, Any],
    original_command: str,
    environment: dict[str, Any],
) -> dict[str, Any]:
    if (
        bool(setup.get("install_command_supported", False))
        and str(setup_result.get("status") or "") != "pass"
    ):
        return _payload(
            status="warning",
            reason="setup_then_retry_framework_configuration",
            retry_recommended=True,
            retry_strategy="run_environment_setup_then_retry",
            original_command=original_command,
            retry_command=original_command,
            retry_level=str(execution_result.get("execution_level") or ""),
            retry_risk=str(execution_result.get("execution_risk") or ""),
            failure_category=str(execution_result.get("failure_category") or ""),
            failure_signal=str(execution_result.get("failure_signal") or ""),
            diagnostic_summary=(
                "A framework-specific test configuration is missing; run supported setup before retrying the same command."
            ),
            planned_environment_variables=_dict(
                execution_result.get("planned_environment_variables")
            ),
            prerequisite_actions=[
                "Run repository test environment setup.",
                f"Install command: {_setup_install_command(setup, environment)}",
            ],
            next_actions=[
                "Rerun with --run-repository-test-environment-setup.",
                (
                    "If the retry still fails, configure framework test settings such as "
                    "DJANGO_SETTINGS_MODULE, pytest.ini, pyproject.toml, or conftest.py."
                ),
            ],
        )
    return _manual_payload(
        reason="framework_test_configuration_required",
        strategy="inspect_framework_test_configuration",
        original_command=original_command,
        execution_result=execution_result,
        diagnostic_summary=(
            "The test command imported repository code but framework-specific test settings were not available."
        ),
        next_actions=list(_list(execution_result.get("next_actions"))),
    )


def _pytest_warning_as_error_retry_plan(
    *,
    execution_result: dict[str, Any],
    setup: dict[str, Any],
    setup_result: dict[str, Any],
    original_command: str,
    environment: dict[str, Any],
) -> dict[str, Any]:
    if (
        bool(setup.get("install_command_supported", False))
        and str(setup_result.get("status") or "") != "pass"
    ):
        return _payload(
            status="warning",
            reason="setup_then_retry_warning_policy",
            retry_recommended=True,
            retry_strategy="run_environment_setup_then_retry",
            original_command=original_command,
            retry_command=original_command,
            retry_level=str(execution_result.get("execution_level") or ""),
            retry_risk=str(execution_result.get("execution_risk") or ""),
            failure_category=str(execution_result.get("failure_category") or ""),
            failure_signal=str(execution_result.get("failure_signal") or ""),
            diagnostic_summary=(
                "Pytest warning filters are treating warnings as errors; first reproduce with the repository's planned dependency/toolchain setup."
            ),
            planned_environment_variables=_dict(
                execution_result.get("planned_environment_variables")
            ),
            prerequisite_actions=[
                "Run repository test environment setup.",
                f"Install command: {_setup_install_command(setup, environment)}",
            ],
            next_actions=[
                "Rerun with --run-repository-test-environment-setup.",
                "If warning-as-error collection still fails, inspect pytest filterwarnings and Python/pytest version compatibility.",
            ],
        )
    return _manual_payload(
        reason="pytest_warning_policy_required",
        strategy="inspect_pytest_warning_filters",
        original_command=original_command,
        execution_result=execution_result,
        diagnostic_summary=(
            "The planned command failed during collection because repository warning filters elevated warnings to errors."
        ),
        next_actions=list(_list(execution_result.get("next_actions"))),
    )


def _retry_payload(
    *,
    strategy: str,
    reason: str,
    retry: dict[str, Any],
    original_command: str,
    execution_result: dict[str, Any],
    diagnostic_summary: str,
) -> dict[str, Any]:
    return _payload(
        status="pass",
        reason=reason,
        retry_recommended=True,
        retry_strategy=strategy,
        original_command=original_command,
        retry_command=str(retry.get("command") or ""),
        retry_level=str(retry.get("level") or ""),
        retry_risk=str(retry.get("risk") or ""),
        failure_category=str(execution_result.get("failure_category") or ""),
        failure_signal=str(execution_result.get("failure_signal") or ""),
        diagnostic_summary=diagnostic_summary,
        planned_environment_variables=_dict(
            execution_result.get("planned_environment_variables")
        ),
        next_actions=[
            f"Retry with: {retry.get('command')}",
            "If the retry passes, expand back toward the original planned command.",
        ],
    )


def _manual_payload(
    *,
    reason: str,
    strategy: str,
    original_command: str,
    execution_result: dict[str, Any],
    diagnostic_summary: str,
    next_actions: list[str],
) -> dict[str, Any]:
    return _payload(
        status="warning",
        reason=reason,
        retry_recommended=False,
        retry_strategy=strategy,
        original_command=original_command,
        failure_category=str(execution_result.get("failure_category") or ""),
        failure_signal=str(execution_result.get("failure_signal") or ""),
        diagnostic_summary=diagnostic_summary,
        planned_environment_variables=_dict(
            execution_result.get("planned_environment_variables")
        ),
        next_actions=next_actions,
    )


def _payload(
    *,
    status: str,
    reason: str,
    retry_recommended: bool,
    retry_strategy: str,
    original_command: str,
    failure_category: str,
    failure_signal: str,
    diagnostic_summary: str,
    retry_command: str = "",
    retry_level: str = "",
    retry_risk: str = "",
    prerequisite_actions: list[str] | None = None,
    next_actions: list[str] | None = None,
    planned_environment_variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    planned_environment_variables = _dict(planned_environment_variables)
    return {
        "status": status,
        "reason": reason,
        "retry_recommended": retry_recommended,
        "retry_strategy": retry_strategy,
        "original_command": original_command,
        "retry_command": retry_command,
        "retry_level": retry_level,
        "retry_risk": retry_risk,
        "failure_category": failure_category,
        "failure_signal": failure_signal,
        "diagnostic_summary": diagnostic_summary,
        "prerequisite_actions": list(prerequisite_actions or []),
        "planned_environment_variables": planned_environment_variables,
        "planned_environment_variable_names": sorted(
            str(key) for key in planned_environment_variables
        ),
        "next_actions": list(next_actions or []),
    }


def _candidate_by_level(
    candidates: list[Any],
    level: str,
    *,
    exclude: str,
) -> dict[str, Any]:
    for value in candidates:
        candidate = _dict(value)
        command = str(candidate.get("command") or "")
        if str(candidate.get("level") or "") == level and command and command != exclude:
            return candidate
    return {}


def _candidate_by_first_level(
    candidates: list[Any],
    levels: tuple[str, ...],
    *,
    exclude: str,
) -> dict[str, Any]:
    for level in levels:
        candidate = _candidate_by_level(candidates, level, exclude=exclude)
        if candidate:
            return candidate
    return {}


def _candidate_by_runner(
    candidates: list[Any],
    runner: str,
    *,
    exclude: str,
) -> dict[str, Any]:
    selected_runner = str(runner or "")
    if not selected_runner:
        return {}
    for value in candidates:
        candidate = _dict(value)
        command = str(candidate.get("command") or "")
        if (
            str(candidate.get("runner") or "") == selected_runner
            and command
            and command != exclude
        ):
            return candidate
    return {}


def _missing_runner_from_signal(signal: str) -> str:
    prefix = "missing_runner:"
    text = str(signal or "")
    if text.startswith(prefix):
        return text[len(prefix) :].strip()
    return ""


def _setup_install_command(setup: dict[str, Any], environment: dict[str, Any]) -> str:
    args = [str(item) for item in _list(setup.get("install_command_args"))]
    if args:
        return shlex.join(args)
    return str(environment.get("recommended_install_command") or "none")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")
