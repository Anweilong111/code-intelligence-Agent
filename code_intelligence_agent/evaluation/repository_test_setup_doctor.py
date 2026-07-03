from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_repository_test_setup_doctor(
    *,
    repository_profile: dict[str, Any] | None = None,
    repository_test_command: dict[str, Any] | None = None,
    repository_test_environment: dict[str, Any] | None = None,
    repository_test_environment_setup: dict[str, Any] | None = None,
    repository_test_environment_setup_result: dict[str, Any] | None = None,
    repository_test_execution_plan: dict[str, Any] | None = None,
    repository_test_execution_result: dict[str, Any] | None = None,
    repository_test_retry_plan: dict[str, Any] | None = None,
    repository_test_retry_execution_result: dict[str, Any] | None = None,
    repository_test_dynamic_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = _dict(repository_profile)
    command = _dict(repository_test_command)
    environment = _dict(repository_test_environment)
    setup = _dict(repository_test_environment_setup)
    setup_result = _dict(repository_test_environment_setup_result)
    execution_plan = _dict(repository_test_execution_plan)
    execution_result = _dict(repository_test_execution_result)
    retry_plan = _dict(repository_test_retry_plan)
    retry_result = _dict(repository_test_retry_execution_result)
    dynamic_evidence = _dict(repository_test_dynamic_evidence)
    signals = _signals(
        profile=profile,
        command=command,
        environment=environment,
        setup=setup,
        setup_result=setup_result,
        execution_plan=execution_plan,
        execution_result=execution_result,
        retry_plan=retry_plan,
        retry_result=retry_result,
        dynamic_evidence=dynamic_evidence,
    )
    checks = [
        _profile_check(profile, signals),
        _command_check(profile, command, signals),
        _checkout_check(command, execution_plan, signals),
        _environment_check(environment, signals),
        _setup_check(setup, setup_result, signals),
        _execution_plan_check(execution_plan, signals),
        _execution_result_check(execution_result, signals),
        _dynamic_evidence_check(dynamic_evidence, signals),
    ]
    configured = any(
        bool(item)
        for item in (
            profile,
            command,
            environment,
            setup,
            setup_result,
            execution_plan,
            execution_result,
            retry_plan,
            retry_result,
            dynamic_evidence,
        )
    )
    status, blocker, next_action = _doctor_status(
        checks,
        configured=configured,
        signals=signals,
    )
    check_status_counts = _counts_by_field(checks, "status")
    return {
        "status": status,
        "blocker": blocker,
        "score": _score(checks),
        "next_action": next_action,
        "check_count": len(checks),
        "passed_check_count": _status_count(checks, "pass"),
        "warning_check_count": _status_count(checks, "warning"),
        "blocked_check_count": _status_count(checks, "blocked"),
        "skipped_check_count": _status_count(checks, "skipped"),
        "check_status_counts": check_status_counts,
        "blocked_check_names": _names_by_status(checks, "blocked"),
        "warning_check_names": _names_by_status(checks, "warning"),
        "checks": checks,
        "signals": signals,
    }


def render_repository_test_setup_doctor_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Repository Test Setup Doctor",
        "",
        f"- Status: `{_markdown_cell(payload.get('status') or '')}`",
        f"- Blocker: `{_markdown_cell(payload.get('blocker') or 'none')}`",
        f"- Score: {_float(payload.get('score', 0.0)):.4f}",
        f"- Next Action: {_markdown_cell(payload.get('next_action') or 'none')}",
        f"- Checks: {_int(payload.get('passed_check_count', 0))}/{_int(payload.get('check_count', 0))} pass",
        f"- Check Statuses: {_format_counts(_dict(payload.get('check_status_counts')))}",
        "",
        "## Checks",
        "",
        "| Check | Status | Actual | Next Action |",
        "| --- | --- | --- | --- |",
    ]
    for check in _list(payload.get("checks")):
        item = _dict(check)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('name') or '')} | "
            f"`{_markdown_cell(item.get('status') or '')}` | "
            f"{_markdown_cell(item.get('actual') or 'none')} | "
            f"{_markdown_cell(item.get('next_action') or 'none')} |"
        )
    if not _list(payload.get("checks")):
        lines.append("| none | `skipped` | none | none |")
    signals = _dict(payload.get("signals"))
    lines.extend(
        [
            "",
            "## Key Signals",
            "",
            "| Signal | Value |",
            "| --- | --- |",
        ]
    )
    for key in (
        "profile_doctor_status",
        "profile_doctor_blocker",
        "command_status",
        "command_reason",
        "environment_status",
        "environment_reason",
        "setup_status",
        "setup_result_status",
        "setup_install_failure_category",
        "execution_plan_status",
        "execution_plan_reason",
        "execution_plan_executable_now",
        "execution_result_status",
        "execution_result_failure_category",
        "retry_status",
        "dynamic_evidence_level",
        "dynamic_usable_for_localization",
        "dynamic_usable_for_patch_validation",
    ):
        lines.append(f"| {key} | {_markdown_cell(signals.get(key))} |")
    return "\n".join(lines)


def write_repository_test_setup_doctor_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_setup_doctor.json"
    markdown_path = root / "repository_test_setup_doctor.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_setup_doctor_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_setup_doctor_json": str(json_path),
        "repository_test_setup_doctor_markdown": str(markdown_path),
    }


def _signals(
    *,
    profile: dict[str, Any],
    command: dict[str, Any],
    environment: dict[str, Any],
    setup: dict[str, Any],
    setup_result: dict[str, Any],
    execution_plan: dict[str, Any],
    execution_result: dict[str, Any],
    retry_plan: dict[str, Any],
    retry_result: dict[str, Any],
    dynamic_evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "profile_doctor_status": str(profile.get("doctor_status") or ""),
        "profile_doctor_blocker": str(profile.get("doctor_blocker") or ""),
        "profile_doctor_next_action": str(profile.get("doctor_next_action") or ""),
        "recommended_test_command": str(
            profile.get("recommended_test_command")
            or command.get("command")
            or execution_plan.get("recommended_test_command")
            or ""
        ),
        "command_status": str(command.get("status") or ""),
        "command_reason": str(command.get("reason") or ""),
        "command_executed": bool(command.get("executed", False)),
        "environment_status": str(environment.get("status") or ""),
        "environment_reason": str(environment.get("reason") or ""),
        "environment_test_tool_available": environment.get("test_tool_available"),
        "environment_test_module": str(environment.get("test_module") or ""),
        "setup_status": str(setup.get("status") or ""),
        "setup_reason": str(setup.get("reason") or ""),
        "setup_install_command_supported": bool(
            setup.get("install_command_supported", False)
        ),
        "setup_result_status": str(setup_result.get("status") or ""),
        "setup_result_reason": str(setup_result.get("reason") or ""),
        "setup_result_executed": bool(setup_result.get("executed", False)),
        "setup_install_failure_category": str(
            setup_result.get("install_failure_category") or ""
        ),
        "setup_install_failure_signal": str(
            setup_result.get("install_failure_signal") or ""
        ),
        "setup_install_fallback_executed": bool(
            setup_result.get("install_fallback_executed", False)
        ),
        "setup_install_fallback_returncode": setup_result.get(
            "install_fallback_returncode"
        ),
        "execution_plan_status": str(execution_plan.get("status") or ""),
        "execution_plan_reason": str(execution_plan.get("reason") or ""),
        "execution_plan_executable_now": bool(
            execution_plan.get("executable_now", False)
        ),
        "execution_plan_repository_root_present": bool(
            execution_plan.get("repository_root_present", False)
        ),
        "execution_plan_recommended_command": str(
            execution_plan.get("recommended_execution_command") or ""
        ),
        "execution_plan_recommended_runner": str(
            execution_plan.get("recommended_execution_runner") or ""
        ),
        "execution_plan_prepared_runner": str(
            execution_plan.get("prepared_test_runner") or ""
        ),
        "execution_result_status": str(execution_result.get("status") or ""),
        "execution_result_reason": str(execution_result.get("reason") or ""),
        "execution_result_executed": bool(execution_result.get("executed", False)),
        "execution_result_failure_category": str(
            execution_result.get("failure_category") or ""
        ),
        "execution_result_returncode": execution_result.get("returncode"),
        "retry_status": str(retry_plan.get("status") or ""),
        "retry_reason": str(retry_plan.get("reason") or ""),
        "retry_strategy": str(
            retry_plan.get("retry_strategy") or retry_result.get("retry_strategy") or ""
        ),
        "retry_execution_status": str(retry_result.get("status") or ""),
        "retry_execution_reason": str(retry_result.get("reason") or ""),
        "dynamic_status": str(dynamic_evidence.get("status") or ""),
        "dynamic_reason": str(dynamic_evidence.get("reason") or ""),
        "dynamic_evidence_level": str(dynamic_evidence.get("evidence_level") or ""),
        "dynamic_usable_for_localization": bool(
            dynamic_evidence.get("usable_for_localization", False)
        ),
        "dynamic_usable_for_regression_validation": bool(
            dynamic_evidence.get("usable_for_regression_validation", False)
        ),
        "dynamic_usable_for_patch_validation": bool(
            dynamic_evidence.get("usable_for_patch_validation", False)
        ),
    }


def _profile_check(
    profile: dict[str, Any],
    signals: dict[str, Any],
) -> dict[str, Any]:
    status = str(signals.get("profile_doctor_status") or "")
    blocker = str(signals.get("profile_doctor_blocker") or "")
    if not profile:
        return _check(
            "profile_doctor",
            "skipped",
            "no repository profile payload",
            "Build repository_profile before repository-test setup diagnosis.",
        )
    if status == "fail":
        return _check(
            "profile_doctor",
            "blocked",
            f"{status}:{blocker or 'unknown'}",
            str(signals.get("profile_doctor_next_action") or "")
            or "Fix repository profile blockers before test setup.",
            blocker=f"profile:{blocker or 'unknown'}",
        )
    if status == "warn":
        return _check(
            "profile_doctor",
            "warning",
            f"{status}:{blocker or 'unknown'}",
            str(signals.get("profile_doctor_next_action") or "")
            or "Review repository profile warnings before relying on test setup.",
            blocker=f"profile:{blocker or 'unknown'}",
        )
    return _check("profile_doctor", "pass", status or "pass", "none")


def _command_check(
    profile: dict[str, Any],
    command: dict[str, Any],
    signals: dict[str, Any],
) -> dict[str, Any]:
    recommended = str(signals.get("recommended_test_command") or "")
    command_status = str(signals.get("command_status") or "")
    command_reason = str(signals.get("command_reason") or "")
    if not recommended:
        return _check(
            "recommended_test_command",
            "blocked",
            "no recommended test command",
            "Add or infer a python -m pytest style repository test command.",
            blocker="test_command:no_recommended_test_command",
        )
    if not command:
        return _check(
            "recommended_test_command",
            "warning",
            recommended,
            "Run repository_test_command validation for the recommended command.",
        )
    if command_status == "skipped" and command_reason == "no_recommended_test_command":
        return _check(
            "recommended_test_command",
            "blocked",
            command_reason,
            "Add or infer a python -m pytest style repository test command.",
            blocker="test_command:no_recommended_test_command",
        )
    if command_status == "skipped" and command_reason == "unsupported_command":
        return _check(
            "recommended_test_command",
            "blocked",
            command_reason,
            "Use a python -m module style command for safe execution.",
            blocker="test_command:unsupported_command",
        )
    if command_status == "fail":
        return _check(
            "recommended_test_command",
            "warning",
            command_reason or "command_failed",
            "Inspect repository_test_command stdout/stderr and dependency signals.",
            blocker=f"test_command:{command_reason or 'command_failed'}",
        )
    return _check(
        "recommended_test_command",
        "pass",
        recommended,
        "none",
    )


def _checkout_check(
    command: dict[str, Any],
    execution_plan: dict[str, Any],
    signals: dict[str, Any],
) -> dict[str, Any]:
    command_reason = str(signals.get("command_reason") or "")
    plan_reason = str(signals.get("execution_plan_reason") or "")
    root_present = bool(signals.get("execution_plan_repository_root_present", False))
    if command_reason == "full_repo_not_materialized" or plan_reason == (
        "full_repo_not_materialized"
    ):
        return _check(
            "full_repository_checkout",
            "blocked",
            "full_repo_not_materialized",
            "Run with --checkout-repository-tests or pass repository_test_root.",
            blocker="checkout:full_repo_not_materialized",
        )
    if command_reason == "repository_root_missing":
        return _check(
            "full_repository_checkout",
            "blocked",
            "repository_root_missing",
            "Verify repository_test_root points to an existing checkout.",
            blocker="checkout:repository_root_missing",
        )
    if root_present:
        return _check("full_repository_checkout", "pass", "root present", "none")
    if command or execution_plan:
        return _check(
            "full_repository_checkout",
            "warning",
            "root presence not confirmed",
            "Pass repository_test_root when repository tests should execute.",
        )
    return _check(
        "full_repository_checkout",
        "skipped",
        "no repository test command or plan",
        "Enable repository test command diagnosis.",
    )


def _environment_check(
    environment: dict[str, Any],
    signals: dict[str, Any],
) -> dict[str, Any]:
    if not environment:
        return _check(
            "test_environment",
            "skipped",
            "no environment payload",
            "Build repository_test_environment before setup diagnosis.",
        )
    status = str(signals.get("environment_status") or "")
    reason = str(signals.get("environment_reason") or "")
    if status == "warning" and reason == "test_tool_missing":
        module = str(signals.get("environment_test_module") or "test runner")
        return _check(
            "test_environment",
            "blocked",
            reason,
            f"Install or prepare `{module}` before executing repository tests.",
            blocker="environment:test_tool_missing",
        )
    if status in {"warning", "fail"}:
        return _check(
            "test_environment",
            "warning",
            reason or status,
            "Inspect repository_test_environment for missing tool or config signals.",
            blocker=f"environment:{reason or status}",
        )
    return _check("test_environment", "pass", status or "pass", "none")


def _setup_check(
    setup: dict[str, Any],
    setup_result: dict[str, Any],
    signals: dict[str, Any],
) -> dict[str, Any]:
    failure_category = str(signals.get("setup_install_failure_category") or "")
    if failure_category:
        return _check(
            "environment_setup",
            "blocked",
            failure_category,
            _setup_failure_next_action(failure_category),
            blocker=f"setup_install_failure:{failure_category}",
        )
    setup_result_status = str(signals.get("setup_result_status") or "")
    setup_result_reason = str(signals.get("setup_result_reason") or "")
    if setup_result_status == "fail":
        reason = setup_result_reason or "setup_failed"
        return _check(
            "environment_setup",
            "blocked",
            reason,
            "Inspect repository_test_environment_setup_result and rerun after fixing install/setup output.",
            blocker=f"setup:{reason}",
        )
    if setup_result_status == "pass":
        return _check("environment_setup", "pass", "setup executed", "none")
    setup_status = str(signals.get("setup_status") or "")
    setup_reason = str(signals.get("setup_reason") or "")
    if setup_status == "warning":
        return _check(
            "environment_setup",
            "warning",
            setup_reason or "setup_warning",
            "Review repository_test_environment_setup before relying on execution.",
            blocker=f"setup:{setup_reason or 'setup_warning'}",
        )
    if setup_status == "pass":
        return _check(
            "environment_setup",
            "warning",
            "setup planned but not executed",
            "Run repository test environment setup when dependencies are not already installed.",
        )
    if not setup and not setup_result:
        return _check(
            "environment_setup",
            "skipped",
            "no setup payload",
            "Build repository_test_environment_setup for dependency diagnosis.",
        )
    return _check(
        "environment_setup",
        "warning",
        setup_result_reason or setup_reason or "setup_not_executed",
        "Run or inspect repository_test_environment_setup_result.",
    )


def _execution_plan_check(
    execution_plan: dict[str, Any],
    signals: dict[str, Any],
) -> dict[str, Any]:
    if not execution_plan:
        return _check(
            "execution_plan",
            "skipped",
            "no execution plan",
            "Build repository_test_execution_plan for a runnable command.",
        )
    reason = str(signals.get("execution_plan_reason") or "")
    executable = bool(signals.get("execution_plan_executable_now", False))
    if reason == "no_recommended_test_command":
        return _check(
            "execution_plan",
            "blocked",
            reason,
            "Add or infer a repository test command before execution planning.",
            blocker="test_command:no_recommended_test_command",
        )
    if reason == "full_repo_not_materialized":
        return _check(
            "execution_plan",
            "blocked",
            reason,
            "Run with --checkout-repository-tests or pass repository_test_root.",
            blocker="checkout:full_repo_not_materialized",
        )
    if reason == "planned_runner_not_prepared":
        runner = str(signals.get("execution_plan_recommended_runner") or "")
        return _check(
            "execution_plan",
            "blocked",
            reason,
            f"Prepare the planned runner `{runner or 'test runner'}` before execution.",
            blocker="execution_plan:planned_runner_not_prepared",
        )
    if reason == "test_environment_warning":
        return _check(
            "execution_plan",
            "blocked",
            reason,
            "Fix repository test environment warnings or run setup before execution.",
            blocker="execution_plan:test_environment_warning",
        )
    if executable:
        return _check(
            "execution_plan",
            "pass",
            str(signals.get("execution_plan_recommended_command") or "executable"),
            "none",
        )
    return _check(
        "execution_plan",
        "warning",
        reason or "plan_not_executable",
        "Inspect repository_test_execution_plan.next_actions.",
        blocker=f"execution_plan:{reason or 'plan_not_executable'}",
    )


def _execution_result_check(
    execution_result: dict[str, Any],
    signals: dict[str, Any],
) -> dict[str, Any]:
    if not execution_result:
        if bool(signals.get("execution_plan_executable_now", False)):
            return _check(
                "execution_result",
                "warning",
                "plan executable but not run",
                "Execute the planned repository test command.",
                blocker="execution_result:not_run",
            )
        return _check(
            "execution_result",
            "skipped",
            "no execution result",
            "Execute repository_test_execution_plan when it is runnable.",
        )
    status = str(signals.get("execution_result_status") or "")
    failure_category = str(signals.get("execution_result_failure_category") or "")
    if status == "pass":
        return _check("execution_result", "pass", "tests passed", "none")
    if failure_category in _INFRA_FAILURE_CATEGORIES:
        return _check(
            "execution_result",
            "blocked",
            failure_category,
            _execution_failure_next_action(failure_category),
            blocker=f"execution_failure:{failure_category}",
        )
    if status in {"fail", "error", "timeout"}:
        return _check(
            "execution_result",
            "warning",
            failure_category or status,
            "Inspect dynamic evidence; assertion failures may still be useful for localization.",
            blocker=f"execution_result:{failure_category or status}",
        )
    return _check(
        "execution_result",
        "warning",
        status or "unknown",
        "Inspect repository_test_execution_result.",
    )


def _dynamic_evidence_check(
    dynamic_evidence: dict[str, Any],
    signals: dict[str, Any],
) -> dict[str, Any]:
    if not dynamic_evidence:
        return _check(
            "dynamic_evidence",
            "skipped",
            "no dynamic evidence",
            "Run repository tests or failure overlay to produce dynamic evidence.",
        )
    usable = (
        bool(signals.get("dynamic_usable_for_localization", False))
        or bool(signals.get("dynamic_usable_for_regression_validation", False))
        or bool(signals.get("dynamic_usable_for_patch_validation", False))
    )
    level = str(signals.get("dynamic_evidence_level") or "")
    if usable:
        return _check(
            "dynamic_evidence",
            "pass",
            level or "usable",
            "Use dynamic evidence for localization and patch validation.",
        )
    return _check(
        "dynamic_evidence",
        "warning",
        str(signals.get("dynamic_reason") or level or "not_usable"),
        "Inspect repository_test_dynamic_evidence and retry/failure-overlay outputs.",
        blocker="dynamic_evidence:not_usable",
    )


def _doctor_status(
    checks: list[dict[str, Any]],
    *,
    configured: bool,
    signals: dict[str, Any],
) -> tuple[str, str, str]:
    if not configured:
        return (
            "skipped",
            "repository_test_not_configured",
            "Enable repository-test command diagnosis or provide repository_test_root.",
        )
    for check in checks:
        if str(check.get("status") or "") == "blocked":
            return (
                "blocked",
                str(check.get("blocker") or check.get("name") or "blocked"),
                str(check.get("next_action") or "Inspect repository test setup outputs."),
            )
    if bool(signals.get("dynamic_usable_for_localization", False)) or bool(
        signals.get("dynamic_usable_for_patch_validation", False)
    ):
        return (
            "pass",
            "none",
            "Use repository dynamic evidence for fault localization and patch validation.",
        )
    if str(signals.get("execution_result_status") or "") == "pass":
        return (
            "pass",
            "none",
            "Use passing repository tests as regression evidence.",
        )
    for check in checks:
        if str(check.get("status") or "") == "warning":
            return (
                "warning",
                str(check.get("blocker") or check.get("name") or "warning"),
                str(check.get("next_action") or "Inspect repository test setup outputs."),
            )
    return (
        "pass",
        "none",
        "Repository test setup signals are ready.",
    )


def _check(
    name: str,
    status: str,
    actual: str,
    next_action: str,
    *,
    blocker: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "expected": _expected_for_check(name),
        "actual": actual,
        "message": actual,
        "next_action": next_action,
        "blocker": blocker,
    }


def _expected_for_check(name: str) -> str:
    return {
        "profile_doctor": "repository profile has importable Python sources",
        "recommended_test_command": "profile has a runnable python -m test command",
        "full_repository_checkout": "repository tests have a materialized checkout",
        "test_environment": "test runner and framework configuration are known",
        "environment_setup": "dependencies are installed or setup is not required",
        "execution_plan": "a repository test command can run now",
        "execution_result": "planned repository test command executed",
        "dynamic_evidence": "execution produced usable failing/passing evidence",
    }.get(name, "")


def _setup_failure_next_action(category: str) -> str:
    if category == "missing_requirement_file":
        return "Fix the referenced requirements path or use a valid project dependency file."
    if category == "editable_backend_unsupported":
        return "Retry with a non-editable project install or add PEP 660 editable support."
    if category == "python_version_incompatible":
        return "Use a Python version compatible with the repository metadata and CI config."
    if category in {"dependency_conflict", "package_resolution_failed"}:
        return "Pin compatible dependency versions or reuse the repository lock/CI install path."
    if category == "network_or_index_error":
        return "Check package index/network access, then rerun repository environment setup."
    if category == "build_backend_missing":
        return "Install the required build backend or use the repository CI install command."
    return "Inspect repository_test_environment_setup_result for the dependency setup failure."


def _execution_failure_next_action(category: str) -> str:
    if category == "missing_test_runner":
        return "Install the selected test runner or rerun setup with runner dependencies."
    if category == "missing_dependency":
        return "Install missing runtime/test dependencies and rerun the planned command."
    if category == "framework_configuration_error":
        return "Apply detected framework environment variables or test settings before rerun."
    if category == "tox_missing_python_interpreter":
        return "Install the tox target Python interpreter or select a pytest fallback command."
    return "Inspect repository_test_execution_result diagnostics before rerunning."


def _score(checks: list[dict[str, Any]]) -> float:
    scored = [
        check
        for check in checks
        if str(check.get("status") or "") not in {"", "skipped"}
    ]
    if not scored:
        return 0.0
    passed = sum(1 for check in scored if str(check.get("status") or "") == "pass")
    return round(passed / len(scored), 4)


def _status_count(checks: list[dict[str, Any]], status: str) -> int:
    return sum(1 for check in checks if str(check.get("status") or "") == status)


def _counts_by_field(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(field) or "")
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _names_by_status(checks: list[dict[str, Any]], status: str) -> list[str]:
    return [
        str(check.get("name") or "")
        for check in checks
        if str(check.get("status") or "") == status
        and str(check.get("name") or "")
    ]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(
        f"{key}={_int(value)}"
        for key, value in sorted(counts.items())
    )


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


_INFRA_FAILURE_CATEGORIES = {
    "missing_test_runner",
    "missing_dependency",
    "framework_configuration_error",
    "tox_missing_python_interpreter",
}
