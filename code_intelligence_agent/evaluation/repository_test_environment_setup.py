from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


SUPPORTED_REPOSITORY_TEST_ENVIRONMENT_SETUP_MODES = {"project", "runner_probe"}
RUNNER_PROBE_TEST_MODULE = "pytest"


def plan_repository_test_environment_setup(
    repository_test_environment: dict[str, Any],
    *,
    output_dir: str | Path,
    repository_root: str | Path | None = None,
    venv_name: str = ".repo_test_venv",
    allow_high_risk_install: bool = False,
    setup_mode: str = "project",
) -> dict[str, Any]:
    normalized_setup_mode = _normalize_setup_mode(setup_mode)
    environment = _dict(repository_test_environment)
    install_command = str(environment.get("recommended_install_command") or "").strip()
    root_value = repository_root or environment.get("repository_root") or None
    root_path = Path(root_value) if root_value else None
    root_present = bool(root_path and root_path.exists() and root_path.is_dir())
    compatibility = _dict(environment.get("repository_compatibility"))
    install_policy = _dict(compatibility.get("install_policy"))
    if not install_policy:
        install_policy = _fallback_install_policy(
            environment,
            repository_root=root_path if root_present else None,
        )
    install_risk = str(install_policy.get("risk") or "unknown")
    dependency_access_blockers = [
        str(item)
        for item in _list(environment.get("dependency_access_blockers"))
        if str(item)
    ]
    python_compatibility_status = str(
        environment.get("python_compatibility_status") or "unknown"
    )
    output_root = Path(output_dir)
    venv_path = output_root / venv_name
    venv_python = _venv_python_path(venv_path)
    create_args = [sys.executable, "-m", "venv", str(venv_path)]
    if normalized_setup_mode == "runner_probe":
        install_args = [
            str(venv_python),
            "-m",
            "pip",
            "install",
            RUNNER_PROBE_TEST_MODULE,
        ]
        install_reason = "runner_probe_pytest_only"
        additional_runner_modules: list[str] = []
        pytest_plugin_candidates: list[str] = []
        pytest_plugin_candidate_sources: list[dict[str, str]] = []
        pytest_plugin_specs: list[str] = []
        pytest_plugin_sources: list[dict[str, str]] = []
        project_metadata_files = _local_project_metadata_files(environment)
        project_install_augmented = False
        install_risk = "low"
        effective_install_policy = {
            "risk": "low",
            "reasons": [
                "Runner probe installs only pytest into an isolated virtual environment."
            ],
            "requires_explicit_authorization": False,
            "auto_execution_allowed": True,
        }
    else:
        install_args, install_reason = _venv_install_args(
            install_command,
            venv_python=venv_python,
        )
        additional_runner_modules = _additional_test_runner_modules(
            environment,
            install_args,
        )
        install_args = _augment_pip_install_args(
            install_args,
            additional_runner_modules,
        )
        pytest_plugin_candidates, pytest_plugin_candidate_sources = (
            _pytest_plugin_dependency_candidates(
                environment,
                install_args,
                repository_root=root_path if root_present else None,
            )
        )
        pytest_plugin_specs, pytest_plugin_sources = (
            _pytest_plugin_dependency_specs_to_install(
                pytest_plugin_candidates,
                pytest_plugin_candidate_sources,
            )
        )
        install_args = _augment_pip_install_args(
            install_args,
            pytest_plugin_specs,
        )
        project_metadata_files = _local_project_metadata_files(environment)
        project_install_augmented = _should_augment_with_editable_project_install(
            environment,
            install_args,
            project_metadata_files,
        )
        if project_install_augmented:
            install_args = _augment_pip_install_args(install_args, ["-e", "."])
        effective_install_policy = install_policy
    root_required = _install_requires_repository_root(install_args)
    missing_config_files = _list(environment.get("missing_config_files"))
    status = "pass"
    reason = (
        "runner_probe_setup_plan_built"
        if normalized_setup_mode == "runner_probe"
        else "environment_setup_plan_built"
    )
    if normalized_setup_mode == "project" and not install_command:
        status = "skipped"
        reason = "no_install_command"
    elif not install_args:
        status = "warning"
        reason = "unsupported_install_command"
    elif python_compatibility_status == "incompatible":
        status = "warning"
        reason = "python_version_incompatible"
    elif normalized_setup_mode == "project" and dependency_access_blockers:
        status = "warning"
        reason = "dependency_access_blocker"
    elif (
        normalized_setup_mode == "project"
        and install_risk == "high"
        and not allow_high_risk_install
    ):
        status = "warning"
        reason = "high_risk_install_requires_authorization"
    elif root_required and not root_present:
        status = "warning"
        reason = "repository_root_missing_for_install"
    elif root_required and missing_config_files:
        status = "warning"
        reason = "config_files_missing_in_checkout"
    return {
        "status": status,
        "reason": reason,
        "setup_mode": normalized_setup_mode,
        "setup_intent": (
            "isolated_test_runner_probe"
            if normalized_setup_mode == "runner_probe"
            else "repository_project_environment"
        ),
        "repository_code_install_requested": bool(
            _pip_install_mentions_local_project(install_args)
        ),
        "repository_dependency_install_requested": bool(
            normalized_setup_mode == "project" and install_args
        ),
        "install_command_overridden": normalized_setup_mode == "runner_probe",
        "safety_boundary": (
            "Installs only pytest into an isolated venv; repository imports and "
            "tests may still execute in the separately authorized test step."
            if normalized_setup_mode == "runner_probe"
            else "Project dependency installation may execute package build hooks."
        ),
        "isolation_mode": "venv",
        "venv_path": str(venv_path),
        "venv_python": str(venv_python),
        "venv_create_command": shlex.join(create_args),
        "venv_create_args": create_args,
        "recommended_install_command": install_command,
        "install_command_args": install_args,
        "install_command_reason": str(environment.get("install_command_reason") or ""),
        "install_command_translation_reason": install_reason,
        "install_risk": install_risk,
        "install_risk_reasons": [
            str(item) for item in _list(effective_install_policy.get("reasons"))
        ],
        "install_requires_explicit_authorization": bool(
            effective_install_policy.get("requires_explicit_authorization", False)
        ),
        "high_risk_install_authorized": bool(allow_high_risk_install),
        "install_auto_execution_allowed": bool(
            effective_install_policy.get("auto_execution_allowed", False)
        ),
        "python_compatibility_status": python_compatibility_status,
        "dependency_access_blockers": dependency_access_blockers,
        "install_command_augmented": bool(
            additional_runner_modules
            or pytest_plugin_specs
            or project_install_augmented
        ),
        "additional_test_runner_modules": additional_runner_modules,
        "pytest_plugin_dependencies_augmented": bool(pytest_plugin_specs),
        "pytest_plugin_dependency_specs": pytest_plugin_specs,
        "pytest_plugin_dependency_sources": pytest_plugin_sources,
        "pytest_plugin_dependency_candidates": pytest_plugin_candidates,
        "pytest_plugin_dependency_candidate_sources": pytest_plugin_candidate_sources,
        "project_install_augmented": project_install_augmented,
        "project_install_augmentation_reason": (
            "runner_install_with_project_metadata"
            if project_install_augmented
            else ""
        ),
        "project_install_metadata_files": project_metadata_files,
        "install_command_supported": bool(install_args),
        "install_requires_repository_root": root_required,
        "repository_root": str(root_path) if root_path is not None else "",
        "repository_root_present": root_present,
        "test_module": (
            RUNNER_PROBE_TEST_MODULE
            if normalized_setup_mode == "runner_probe"
            else str(environment.get("test_module") or "")
        ),
        "test_tool_available": environment.get("test_tool_available"),
        "dependency_files": _list(environment.get("dependency_files")),
        "missing_config_files": missing_config_files,
        "next_actions": _next_actions(
            status=status,
            reason=reason,
            create_command=shlex.join(create_args),
            install_args=install_args,
            root_required=root_required,
        ),
    }


def render_repository_test_environment_setup_markdown(payload: dict[str, Any]) -> str:
    install_args = _list(payload.get("install_command_args"))
    lines = [
        "# Repository Test Environment Setup Plan",
        "",
        f"- Status: `{_markdown_cell(payload.get('status', ''))}`",
        f"- Reason: `{_markdown_cell(payload.get('reason', ''))}`",
        f"- Setup Mode: `{_markdown_cell(payload.get('setup_mode') or 'project')}`",
        f"- Setup Intent: `{_markdown_cell(payload.get('setup_intent') or 'none')}`",
        (
            "- Repository Code Install Requested: "
            f"{str(bool(payload.get('repository_code_install_requested'))).lower()}"
        ),
        (
            "- Repository Dependency Install Requested: "
            f"{str(bool(payload.get('repository_dependency_install_requested'))).lower()}"
        ),
        f"- Safety Boundary: {_markdown_cell(payload.get('safety_boundary') or 'none')}",
        f"- Isolation Mode: `{_markdown_cell(payload.get('isolation_mode', ''))}`",
        f"- Venv Path: `{_markdown_cell(payload.get('venv_path') or 'none')}`",
        f"- Venv Python: `{_markdown_cell(payload.get('venv_python') or 'none')}`",
        f"- Venv Create Command: `{_markdown_cell(payload.get('venv_create_command') or 'none')}`",
        (
            "- Recommended Install Command: "
            f"`{_markdown_cell(payload.get('recommended_install_command') or 'none')}`"
        ),
        (
            "- Venv Install Command: "
            f"`{_markdown_cell(shlex.join(str(item) for item in install_args) if install_args else 'none')}`"
        ),
        (
            "- Install Translation Reason: "
            f"`{_markdown_cell(payload.get('install_command_translation_reason') or 'none')}`"
        ),
        f"- Install Risk: `{_markdown_cell(payload.get('install_risk') or 'unknown')}`",
        (
            "- High Risk Install Authorized: "
            f"{str(bool(payload.get('high_risk_install_authorized'))).lower()}"
        ),
        (
            "- Install Requires Explicit Authorization: "
            f"{str(bool(payload.get('install_requires_explicit_authorization'))).lower()}"
        ),
        f"- Install Command Augmented: {str(bool(payload.get('install_command_augmented', False))).lower()}",
        (
            "- Additional Test Runner Modules: "
            f"{', '.join(str(item) for item in _list(payload.get('additional_test_runner_modules'))) or 'none'}"
        ),
        (
            "- Pytest Plugin Dependencies Augmented: "
            f"{str(bool(payload.get('pytest_plugin_dependencies_augmented', False))).lower()}"
        ),
        (
            "- Pytest Plugin Dependency Specs: "
            f"{', '.join(str(item) for item in _list(payload.get('pytest_plugin_dependency_specs'))) or 'none'}"
        ),
        (
            "- Pytest Plugin Dependency Candidates: "
            f"{', '.join(str(item) for item in _list(payload.get('pytest_plugin_dependency_candidates'))) or 'none'}"
        ),
        (
            "- Project Install Augmented: "
            f"{str(bool(payload.get('project_install_augmented', False))).lower()}"
        ),
        (
            "- Project Install Augmentation Reason: "
            f"`{_markdown_cell(payload.get('project_install_augmentation_reason') or 'none')}`"
        ),
        f"- Install Command Supported: {str(bool(payload.get('install_command_supported'))).lower()}",
        f"- Install Requires Repository Root: {str(bool(payload.get('install_requires_repository_root'))).lower()}",
        f"- Repository Root: `{_markdown_cell(payload.get('repository_root') or 'none')}`",
        f"- Repository Root Present: {str(bool(payload.get('repository_root_present'))).lower()}",
        "",
        "## Dependency Files",
        "",
    ]
    for path in _list(payload.get("dependency_files")):
        lines.append(f"- `{_markdown_cell(path)}`")
    if not _list(payload.get("dependency_files")):
        lines.append("- none")
    project_metadata_files = _list(payload.get("project_install_metadata_files"))
    if project_metadata_files:
        lines.extend(["", "## Project Install Metadata Files", ""])
        for path in project_metadata_files:
            lines.append(f"- `{_markdown_cell(path)}`")
    plugin_sources = _list(payload.get("pytest_plugin_dependency_sources"))
    if plugin_sources:
        lines.extend(["", "## Pytest Plugin Dependency Sources", ""])
        for item in plugin_sources:
            row = _dict(item)
            lines.append(
                "- "
                f"`{_markdown_cell(row.get('requirement') or '')}` "
                f"from `{_markdown_cell(row.get('source') or '')}`"
            )
    plugin_candidate_sources = _list(
        payload.get("pytest_plugin_dependency_candidate_sources")
    )
    if plugin_candidate_sources:
        lines.extend(["", "## Pytest Plugin Dependency Candidates", ""])
        for item in plugin_candidate_sources:
            row = _dict(item)
            lines.append(
                "- "
                f"`{_markdown_cell(row.get('requirement') or '')}` "
                f"from `{_markdown_cell(row.get('source') or '')}`"
            )
    missing = _list(payload.get("missing_config_files"))
    if missing:
        lines.extend(["", "## Missing Config Files In Checkout", ""])
        for path in missing:
            lines.append(f"- `{_markdown_cell(path)}`")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    return "\n".join(lines)


def write_repository_test_environment_setup_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_environment_setup.json"
    markdown_path = root / "repository_test_environment_setup.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_environment_setup_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_environment_setup_json": str(json_path),
        "repository_test_environment_setup_markdown": str(markdown_path),
    }


def execute_repository_test_environment_setup(
    setup_plan: dict[str, Any],
    *,
    enabled: bool = False,
    timeout: int = 120,
    runner=None,
    allow_high_risk_install: bool = False,
) -> dict[str, Any]:
    plan = _dict(setup_plan)
    create_args = [str(item) for item in _list(plan.get("venv_create_args"))]
    install_args = [str(item) for item in _list(plan.get("install_command_args"))]
    repository_root = str(plan.get("repository_root") or "")
    root_required = bool(plan.get("install_requires_repository_root", False))
    if not enabled:
        return _execution_skipped(
            plan,
            reason="execution_disabled",
            message=(
                "Repository test environment setup execution is disabled; "
                "only the setup plan was written."
            ),
        )
    if (
        str(plan.get("install_risk") or "") == "high"
        and not (
            allow_high_risk_install
            or bool(plan.get("high_risk_install_authorized", False))
        )
    ):
        return _execution_skipped(
            plan,
            reason="high_risk_install_requires_authorization",
            message=(
                "Repository dependency setup contains a high-risk build hook and "
                "was not explicitly authorized."
            ),
        )
    plan_ready = str(plan.get("status") or "") == "pass"
    if (
        str(plan.get("reason") or "") == "high_risk_install_requires_authorization"
        and allow_high_risk_install
    ):
        plan_ready = True
    if not plan_ready:
        return _execution_skipped(
            plan,
            reason="setup_plan_not_ready",
            message="Repository test environment setup plan is not ready to execute.",
        )
    if not create_args:
        return _execution_skipped(
            plan,
            reason="missing_venv_create_command",
            message="Setup plan did not include a venv creation command.",
        )
    if not install_args:
        return _execution_skipped(
            plan,
            reason="no_install_command",
            message="Setup plan did not include an install command to execute.",
        )
    if root_required and not repository_root:
        return _execution_skipped(
            plan,
            reason="repository_root_missing",
            message="Root-relative dependency installation requires a repository root.",
        )
    cwd = Path(repository_root) if root_required else None
    if cwd is not None and (not cwd.exists() or not cwd.is_dir()):
        return _execution_skipped(
            plan,
            reason="repository_root_missing",
            message="Repository root does not exist or is not a directory.",
            cwd=str(cwd),
        )
    run = runner or subprocess.run
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    create = _run_setup_command(
        run,
        create_args,
        cwd=None,
        timeout=timeout,
        env=env,
    )
    if create.get("timeout") or _int(create.get("returncode")) != 0:
        return _execution_result(
            plan,
            status="fail",
            reason="venv_create_failed",
            message="Isolated venv creation failed.",
            create=create,
            install=None,
            install_fallback=None,
            cwd=str(cwd) if cwd is not None else "",
        )
    install = _run_setup_command(
        run,
        install_args,
        cwd=cwd,
        timeout=timeout,
        env=env,
    )
    if install.get("timeout") or _int(install.get("returncode")) != 0:
        install_fallback_args = _install_fallback_args(install_args)
        install_fallback = None
        if install_fallback_args:
            install_fallback = _run_setup_command(
                run,
                install_fallback_args,
                cwd=cwd,
                timeout=timeout,
                env=env,
            )
            if not install_fallback.get("timeout") and _int(
                install_fallback.get("returncode")
            ) == 0:
                return _execution_result(
                    plan,
                    status="pass",
                    reason="environment_setup_executed_with_install_fallback",
                    message=(
                        "Repository test dependency installation completed "
                        "after falling back from editable to non-editable "
                        "project install."
                    ),
                    create=create,
                    install=install,
                    install_fallback=install_fallback,
                    cwd=str(cwd) if cwd is not None else "",
                )
        return _execution_result(
            plan,
            status="fail",
            reason="dependency_install_failed",
            message="Repository test dependency installation failed.",
            create=create,
            install=install,
            install_fallback=install_fallback,
            cwd=str(cwd) if cwd is not None else "",
        )
    return _execution_result(
        plan,
        status="pass",
        reason="environment_setup_executed",
        message="Repository test environment setup completed successfully.",
        create=create,
        install=install,
        install_fallback=None,
        cwd=str(cwd) if cwd is not None else "",
    )


def render_repository_test_environment_setup_result_markdown(
    payload: dict[str, Any],
) -> str:
    lines = [
        "# Repository Test Environment Setup Result",
        "",
        f"- Status: `{_markdown_cell(payload.get('status', ''))}`",
        f"- Executed: {str(bool(payload.get('executed', False))).lower()}",
        f"- Reason: `{_markdown_cell(payload.get('reason', ''))}`",
        f"- Setup Mode: `{_markdown_cell(payload.get('setup_mode') or 'project')}`",
        f"- Test Module: `{_markdown_cell(payload.get('test_module') or 'none')}`",
        (
            "- Repository Code Install Requested: "
            f"{str(bool(payload.get('repository_code_install_requested'))).lower()}"
        ),
        (
            "- Repository Dependency Install Requested: "
            f"{str(bool(payload.get('repository_dependency_install_requested'))).lower()}"
        ),
        f"- Message: {_markdown_cell(payload.get('message', ''))}",
        f"- Venv Path: `{_markdown_cell(payload.get('venv_path') or 'none')}`",
        f"- Create Executed: {str(bool(payload.get('create_executed', False))).lower()}",
        f"- Create Return Code: {_markdown_cell(payload.get('create_returncode'))}",
        f"- Install Executed: {str(bool(payload.get('install_executed', False))).lower()}",
        f"- Install Return Code: {_markdown_cell(payload.get('install_returncode'))}",
        f"- Install Failure Category: `{_markdown_cell(payload.get('install_failure_category') or 'none')}`",
        f"- Install Failure Signal: `{_markdown_cell(payload.get('install_failure_signal') or 'none')}`",
        f"- Install Fallback Executed: {str(bool(payload.get('install_fallback_executed', False))).lower()}",
        f"- Install Fallback Return Code: {_markdown_cell(payload.get('install_fallback_returncode'))}",
        f"- Triggered By: `{_markdown_cell(payload.get('triggered_by') or 'none')}`",
        (
            "- Auto Retry Prerequisite: "
            f"{str(bool(payload.get('auto_retry_prerequisite', False))).lower()}"
        ),
        f"- CWD: `{_markdown_cell(payload.get('cwd', ''))}`",
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
            "## Create Stdout Preview",
            "",
            "```text",
            str(payload.get("create_stdout_preview") or ""),
            "```",
            "",
            "## Create Stderr Preview",
            "",
            "```text",
            str(payload.get("create_stderr_preview") or ""),
            "```",
            "",
            "## Install Stdout Preview",
            "",
            "```text",
            str(payload.get("install_stdout_preview") or ""),
            "```",
            "",
            "## Install Stderr Preview",
            "",
            "```text",
            str(payload.get("install_stderr_preview") or ""),
            "```",
            "",
            "## Install Fallback Stdout Preview",
            "",
            "```text",
            str(payload.get("install_fallback_stdout_preview") or ""),
            "```",
            "",
            "## Install Fallback Stderr Preview",
            "",
            "```text",
            str(payload.get("install_fallback_stderr_preview") or ""),
            "```",
        ]
    )
    return "\n".join(lines)


def write_repository_test_environment_setup_result_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_environment_setup_result.json"
    markdown_path = root / "repository_test_environment_setup_result.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_environment_setup_result_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_environment_setup_result_json": str(json_path),
        "repository_test_environment_setup_result_markdown": str(markdown_path),
    }


def _venv_install_args(
    install_command: str,
    *,
    venv_python: Path,
) -> tuple[list[str], str]:
    if not install_command:
        return [], "no_install_command"
    try:
        args = shlex.split(install_command)
    except ValueError:
        return [], "parse_error"
    if len(args) >= 4 and _is_python_executable(args[0]) and args[1:4] == [
        "-m",
        "pip",
        "install",
    ]:
        return [str(venv_python), "-m", "pip", "install", *args[4:]], "pip_install"
    managed = _managed_project_install_args(args, venv_python=venv_python)
    if managed:
        return managed
    return [], "unsupported"


def _normalize_setup_mode(value: str) -> str:
    normalized = str(value or "project").strip().lower().replace("-", "_")
    if normalized not in SUPPORTED_REPOSITORY_TEST_ENVIRONMENT_SETUP_MODES:
        raise ValueError(f"unsupported repository test environment setup mode: {value}")
    return normalized


def _managed_project_install_args(
    args: list[str],
    *,
    venv_python: Path,
) -> tuple[list[str], str]:
    if len(args) >= 2 and args[0] == "uv" and args[1] == "sync":
        return _pip_editable_project_args(venv_python), "uv_sync_to_pip_editable"
    if len(args) >= 2 and args[0] == "pdm" and args[1] == "install":
        return _pip_editable_project_args(venv_python), "pdm_install_to_pip_editable"
    if len(args) >= 2 and args[0] == "poetry" and args[1] == "install":
        return _pip_editable_project_args(venv_python), "poetry_install_to_pip_editable"
    if len(args) >= 3 and args[0] == "hatch" and args[1:3] == ["env", "create"]:
        return _pip_editable_project_args(venv_python), "hatch_env_to_pip_editable"
    if len(args) >= 2 and args[0] == "pipenv" and args[1] == "install":
        return _pip_editable_project_args(venv_python), "pipenv_install_to_pip_editable"
    return [], ""


def _pip_editable_project_args(venv_python: Path) -> list[str]:
    return [str(venv_python), "-m", "pip", "install", "-e", "."]


def _additional_test_runner_modules(
    environment: dict[str, Any],
    install_args: list[str],
) -> list[str]:
    if not _is_pip_install_args(install_args):
        return []
    runners: list[str] = []
    for runner in _test_runner_modules_from_environment(environment):
        if runner not in runners and not _pip_install_mentions_runner(
            install_args,
            runner,
        ):
            runners.append(runner)
    return runners


def _augment_pip_install_args(
    install_args: list[str],
    modules: list[str],
) -> list[str]:
    if not modules or not _is_pip_install_args(install_args):
        return install_args
    return [*install_args, *modules]


def _pytest_plugin_dependency_candidates(
    environment: dict[str, Any],
    install_args: list[str],
    *,
    repository_root: Path | None,
) -> tuple[list[str], list[dict[str, str]]]:
    if repository_root is None or not _is_pip_install_args(install_args):
        return [], []
    specs: list[str] = []
    sources: list[dict[str, str]] = []
    seen_names = {_requirement_name(item) for item in install_args[4:]}
    for rel_path in _requirement_dependency_files(environment):
        path = repository_root / rel_path
        if not path.exists() or not path.is_file():
            continue
        for line in _read_requirement_lines(path):
            spec = _safe_pytest_plugin_requirement(line)
            if not spec:
                continue
            name = _requirement_name(spec)
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            specs.append(spec)
            sources.append(
                {
                    "source": rel_path,
                    "requirement": spec,
                    "package": name,
                }
            )
            if len(specs) >= 8:
                return specs, sources
    return specs, sources


def _pytest_plugin_dependency_specs_to_install(
    candidates: list[str],
    candidate_sources: list[dict[str, str]],
) -> tuple[list[str], list[dict[str, str]]]:
    del candidates, candidate_sources
    return [], []


def _requirement_dependency_files(environment: dict[str, Any]) -> list[str]:
    files: list[str] = []
    for field in ("dependency_files", "project_config_files"):
        for item in _list(environment.get(field)):
            path = _root_requirement_file(str(item))
            if path and path not in files:
                files.append(path)
    return files


def _root_requirement_file(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if "/" in text or text.startswith("."):
        return ""
    name = Path(text).name
    if name in {"requirements-dev.txt", "requirements-test.txt", "requirements.txt"}:
        return name
    return ""


def _read_requirement_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []


def _safe_pytest_plugin_requirement(line: str) -> str:
    text = str(line or "").split("#", 1)[0].strip()
    if not text or text.startswith(("-", ".", "/", "\\")):
        return ""
    token = text.split()[0]
    if not token or token.startswith(("-", ".", "/", "\\")):
        return ""
    if token.startswith(("http://", "https://", "git+")):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_.\-\[\],<>=!~*]+", token):
        return ""
    name = _requirement_name(token)
    if not _is_pytest_plugin_requirement_name(name):
        return ""
    return token


def _is_pytest_plugin_requirement_name(name: str) -> bool:
    normalized = str(name or "").strip().lower().replace("_", "-")
    if normalized in {"", "pytest", "pytest-runner"}:
        return False
    if normalized.startswith("pytest-"):
        return True
    return normalized in {"requests-mock"}


def _should_augment_with_editable_project_install(
    environment: dict[str, Any],
    install_args: list[str],
    project_metadata_files: list[str],
) -> bool:
    if not project_metadata_files or not _is_pip_install_args(install_args):
        return False
    if _pip_install_mentions_local_project(install_args):
        return False
    reason = str(environment.get("install_command_reason") or "")
    return reason in {"tox_runner", "nox_runner"}


def _local_project_metadata_files(environment: dict[str, Any]) -> list[str]:
    files: list[str] = []
    for field in ("dependency_files", "project_config_files"):
        for item in _list(environment.get(field)):
            path = _root_project_metadata_file(str(item))
            if path and path not in files:
                files.append(path)
    return files


def _root_project_metadata_file(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text if text in {"pyproject.toml", "setup.py", "setup.cfg"} else ""


def _fallback_install_policy(
    environment: dict[str, Any],
    *,
    repository_root: Path | None,
) -> dict[str, Any]:
    config_files = [
        str(item).replace("\\", "/").strip("/")
        for field in ("dependency_files", "project_config_files")
        for item in _list(environment.get(field))
        if str(item)
    ]
    root_names = {Path(path).name for path in config_files if "/" not in path}
    risk = "low"
    reasons = ["Only isolated runner installation was inferred."]
    if "setup.py" in root_names and "pyproject.toml" not in root_names:
        risk = "high"
        reasons = [
            "Legacy setup.py can execute arbitrary repository code during installation."
        ]
    elif "pyproject.toml" in root_names:
        risk = "medium"
        reasons = [
            "Project installation invokes an isolated packaging backend and may access the network."
        ]
        if repository_root is not None:
            path = repository_root / "pyproject.toml"
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
                data = {}
                if path.is_file():
                    risk = "high"
                    reasons = [
                        "pyproject.toml could not be parsed, so build-hook behavior is unknown."
                    ]
            build_system = _dict(data.get("build-system"))
            if _list(build_system.get("backend-path")):
                risk = "high"
                reasons = [
                    "pyproject.toml uses a repository-local backend-path build hook."
                ]
    elif "setup.cfg" in root_names or any(
        name.startswith("requirements") for name in root_names
    ):
        risk = "medium"
        reasons = ["Dependency installation may download or build third-party packages."]
    return {
        "risk": risk,
        "reasons": reasons,
        "requires_explicit_authorization": risk == "high",
        "auto_execution_allowed": risk != "high",
    }


def _pip_install_mentions_local_project(args: list[str]) -> bool:
    for index, item in enumerate(args):
        if item in {"-e", "--editable"} and index + 1 < len(args):
            if args[index + 1] in {".", "./"}:
                return True
        if item in {"--editable=.", "--editable=./", ".", "./"}:
            return True
    return False


def _test_runner_modules_from_environment(
    environment: dict[str, Any],
) -> list[str]:
    runners: list[str] = []
    for runner in [str(environment.get("test_module") or "")]:
        normalized = _safe_test_runner_module(runner)
        if normalized and normalized not in runners:
            runners.append(normalized)
    ci_config = _dict(environment.get("ci_configuration"))
    for raw in _list(ci_config.get("test_commands")):
        item = _dict(raw)
        runner = _safe_test_runner_module(str(item.get("runner") or ""))
        if not runner:
            runner = _runner_from_command(str(item.get("command") or ""))
        if runner and runner not in runners:
            runners.append(runner)
    for command in _list(environment.get("ci_test_command_candidates")):
        runner = _runner_from_command(str(command or ""))
        if runner and runner not in runners:
            runners.append(runner)
    return runners


def _runner_from_command(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return ""
    if len(parts) >= 3 and _is_python_executable(parts[0]) and parts[1] == "-m":
        return _safe_test_runner_module(parts[2])
    if parts:
        return _safe_test_runner_module(parts[0])
    return ""


def _safe_test_runner_module(value: str) -> str:
    runner = str(value or "").strip()
    if runner == "py.test":
        return "pytest"
    return runner if runner in {"pytest", "tox", "nox"} else ""


def _is_pip_install_args(args: list[str]) -> bool:
    return len(args) >= 4 and args[1:4] == ["-m", "pip", "install"]


def _pip_install_mentions_runner(args: list[str], runner: str) -> bool:
    return any(_requirement_name(token) == runner for token in args[4:])


def _requirement_name(value: str) -> str:
    token = str(value or "").strip()
    if not token or token.startswith("-"):
        return ""
    if token in {".", "./"} or "/" in token or "\\" in token:
        return ""
    if token.startswith(("http://", "https://", "git+")):
        return ""
    token = re.split(r"[<>=~!;\[]", token, maxsplit=1)[0]
    return token.lower().replace("_", "-")


def _install_fallback_args(args: list[str]) -> list[str]:
    for index, item in enumerate(args):
        if item in {"-e", "--editable"} and index + 1 < len(args):
            target = args[index + 1]
            if target in {".", "./"}:
                return [*args[:index], ".", *args[index + 2 :]]
        if item in {"--editable=.", "--editable=./"}:
            return [*args[:index], ".", *args[index + 1 :]]
    return []


def _run_setup_command(
    runner,
    args: list[str],
    *,
    cwd: Path | None,
    timeout: int,
    env: dict[str, str],
) -> dict[str, Any]:
    try:
        completed = runner(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "args": args,
            "cwd": str(cwd) if cwd is not None else "",
            "returncode": -1,
            "timeout": True,
            "stdout_preview": _preview(exc.stdout or ""),
            "stderr_preview": _preview(exc.stderr or ""),
        }
    return {
        "args": args,
        "cwd": str(cwd) if cwd is not None else "",
        "returncode": completed.returncode,
        "timeout": False,
        "stdout_preview": _preview(completed.stdout or ""),
        "stderr_preview": _preview(completed.stderr or ""),
    }


def _diagnose_install_failure(install: dict[str, Any]) -> dict[str, str]:
    if not install:
        return {"category": "none", "signal": ""}
    if not install.get("timeout") and _int(install.get("returncode")) == 0:
        return {"category": "none", "signal": ""}
    text = "\n".join(
        [
            str(install.get("stdout_preview") or ""),
            str(install.get("stderr_preview") or ""),
        ]
    )
    lowered = text.lower()
    if bool(install.get("timeout", False)):
        return {"category": "install_timeout", "signal": "install command timed out"}
    if "editable" in lowered and (
        "not support" in lowered
        or "unsupported" in lowered
        or "build_editable" in lowered
        or "pep 660" in lowered
    ):
        return {
            "category": "editable_backend_unsupported",
            "signal": _first_signal_line(text),
        }
    if "could not open requirements file" in lowered or (
        "no such file or directory" in lowered and "requirements" in lowered
    ):
        return {"category": "missing_requirement_file", "signal": _first_signal_line(text)}
    if (
        "requires-python" in lowered
        or "requires a different python" in lowered
        or "unsupported python version" in lowered
    ):
        return {"category": "python_version_incompatible", "signal": _first_signal_line(text)}
    if (
        "no matching distribution found" in lowered
        or "could not find a version that satisfies" in lowered
    ):
        return {"category": "package_resolution_failed", "signal": _first_signal_line(text)}
    if "resolutionimpossible" in lowered or "conflicting dependencies" in lowered:
        return {"category": "dependency_conflict", "signal": _first_signal_line(text)}
    if (
        "temporary failure" in lowered
        or "connection error" in lowered
        or "connection reset" in lowered
        or "read timed out" in lowered
    ):
        return {"category": "network_or_index_error", "signal": _first_signal_line(text)}
    if "modulenotfounderror" in lowered and "backend" in lowered:
        return {"category": "build_backend_missing", "signal": _first_signal_line(text)}
    return {"category": "unknown_install_failure", "signal": _first_signal_line(text)}


def _first_signal_line(text: str, *, limit: int = 220) -> str:
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if line:
            return line[:limit]
    return ""


def _install_failure_next_actions(category: str) -> list[str]:
    if category == "editable_backend_unsupported":
        return [
            "Retry with non-editable project install if the fallback did not already succeed."
        ]
    if category == "missing_requirement_file":
        return ["Verify checkout ref/depth and dependency file paths."]
    if category == "python_version_incompatible":
        return ["Use a Python version compatible with the repository metadata."]
    if category in {"package_resolution_failed", "dependency_conflict"}:
        return ["Inspect dependency pins, extras, and package index availability."]
    if category == "network_or_index_error":
        return ["Retry dependency setup after network/package-index connectivity is stable."]
    if category == "build_backend_missing":
        return ["Inspect pyproject.toml build-system.requires and build backend configuration."]
    return []


def _execution_result(
    plan: dict[str, Any],
    *,
    status: str,
    reason: str,
    message: str,
    create: dict[str, Any],
    install: dict[str, Any] | None,
    install_fallback: dict[str, Any] | None,
    cwd: str,
) -> dict[str, Any]:
    install_payload = _dict(install)
    install_fallback_payload = _dict(install_fallback)
    install_diagnostic = _diagnose_install_failure(install_payload)
    next_actions = []
    if status != "pass":
        next_actions.append(
            "Inspect setup stdout/stderr previews before executing repository tests."
        )
    elif install_fallback_payload:
        next_actions.append(
            "Initial editable install failed, but non-editable project install succeeded."
        )
    if install_diagnostic["category"] != "none":
        next_actions.extend(_install_failure_next_actions(install_diagnostic["category"]))
    if install_fallback_payload and status != "pass":
        next_actions.append(
            "Editable and non-editable project installs failed; inspect pyproject build backend and dependency metadata."
        )
    return {
        "status": status,
        "executed": True,
        "reason": reason,
        "message": message,
        "setup_mode": str(plan.get("setup_mode") or "project"),
        "test_module": str(plan.get("test_module") or ""),
        "repository_code_install_requested": bool(
            plan.get("repository_code_install_requested", False)
        ),
        "repository_dependency_install_requested": bool(
            plan.get("repository_dependency_install_requested", False)
        ),
        "safety_boundary": str(plan.get("safety_boundary") or ""),
        "venv_path": str(plan.get("venv_path") or ""),
        "venv_python": str(plan.get("venv_python") or ""),
        "create_command_args": _list(create.get("args")),
        "install_command_args": _list(install_payload.get("args")),
        "create_executed": True,
        "install_executed": bool(install),
        "create_returncode": create.get("returncode"),
        "install_returncode": install_payload.get("returncode"),
        "install_failure_category": install_diagnostic["category"],
        "install_failure_signal": install_diagnostic["signal"],
        "install_fallback_command_args": _list(install_fallback_payload.get("args")),
        "install_fallback_executed": bool(install_fallback),
        "install_fallback_returncode": install_fallback_payload.get("returncode"),
        "create_timeout": bool(create.get("timeout", False)),
        "install_timeout": bool(install_payload.get("timeout", False)),
        "install_fallback_timeout": bool(
            install_fallback_payload.get("timeout", False)
        ),
        "cwd": cwd,
        "create_stdout_preview": str(create.get("stdout_preview") or ""),
        "create_stderr_preview": str(create.get("stderr_preview") or ""),
        "install_stdout_preview": str(install_payload.get("stdout_preview") or ""),
        "install_stderr_preview": str(install_payload.get("stderr_preview") or ""),
        "install_fallback_stdout_preview": str(
            install_fallback_payload.get("stdout_preview") or ""
        ),
        "install_fallback_stderr_preview": str(
            install_fallback_payload.get("stderr_preview") or ""
        ),
        "next_actions": next_actions,
    }


def _execution_skipped(
    plan: dict[str, Any],
    *,
    reason: str,
    message: str,
    cwd: str = "",
) -> dict[str, Any]:
    return {
        "status": "skipped",
        "executed": False,
        "reason": reason,
        "message": message,
        "setup_mode": str(plan.get("setup_mode") or "project"),
        "test_module": str(plan.get("test_module") or ""),
        "repository_code_install_requested": bool(
            plan.get("repository_code_install_requested", False)
        ),
        "repository_dependency_install_requested": bool(
            plan.get("repository_dependency_install_requested", False)
        ),
        "safety_boundary": str(plan.get("safety_boundary") or ""),
        "venv_path": str(plan.get("venv_path") or ""),
        "venv_python": str(plan.get("venv_python") or ""),
        "create_command_args": _list(plan.get("venv_create_args")),
        "install_command_args": _list(plan.get("install_command_args")),
        "create_executed": False,
        "install_executed": False,
        "create_returncode": None,
        "install_returncode": None,
        "install_failure_category": "none",
        "install_failure_signal": "",
        "install_fallback_command_args": [],
        "install_fallback_executed": False,
        "install_fallback_returncode": None,
        "create_timeout": False,
        "install_timeout": False,
        "install_fallback_timeout": False,
        "cwd": cwd,
        "create_stdout_preview": "",
        "create_stderr_preview": "",
        "install_stdout_preview": "",
        "install_stderr_preview": "",
        "install_fallback_stdout_preview": "",
        "install_fallback_stderr_preview": "",
        "next_actions": _setup_execution_skipped_actions(reason),
    }


def _setup_execution_skipped_actions(reason: str) -> list[str]:
    if reason == "execution_disabled":
        return [
            "Enable repository test environment setup execution only when dependency installation is acceptable."
        ]
    if reason == "setup_plan_not_ready":
        return ["Inspect repository_test_environment_setup.md and resolve warnings first."]
    if reason == "repository_root_missing":
        return ["Provide --repository-test-root or --checkout-repository-tests."]
    if reason == "high_risk_install_requires_authorization":
        return [
            "Review the repository build hook, then explicitly authorize the high-risk install only if it is trusted."
        ]
    return []


def _install_requires_repository_root(args: list[str]) -> bool:
    if not args:
        return False
    for index, item in enumerate(args):
        if item in {"-r", "--requirement"} and index + 1 < len(args):
            return True
        if item in {"-e", "--editable"} and index + 1 < len(args):
            target = args[index + 1]
            if target in {".", "./"} or not _looks_like_remote_spec(target):
                return True
    return False


def _looks_like_remote_spec(value: str) -> bool:
    text = value.lower()
    return "://" in text or text.startswith("git+")


def _venv_python_path(venv_path: Path) -> Path:
    if sys.platform.startswith("win"):
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def _is_python_executable(value: str) -> bool:
    name = Path(value).name.lower()
    return name in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}


def _preview(value: str, limit: int = 4000) -> str:
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _next_actions(
    *,
    status: str,
    reason: str,
    create_command: str,
    install_args: list[str],
    root_required: bool,
) -> list[str]:
    if status == "skipped" and reason == "no_install_command":
        return ["No dependency setup command is required before planned test execution."]
    actions = [f"Create the isolated test environment with: {create_command}"]
    if install_args and reason != "high_risk_install_requires_authorization":
        actions.append(
            "Install repository test dependencies inside the isolated environment with: "
            + shlex.join(str(item) for item in install_args)
        )
    if status == "warning" and reason == "high_risk_install_requires_authorization":
        actions.append(
            "Do not execute the repository install hook until its build configuration is reviewed and explicitly authorized."
        )
    if status == "warning" and reason == "python_version_incompatible":
        actions.append(
            "Create the isolated environment with a Python version allowed by repository metadata."
        )
    if status == "warning" and reason == "dependency_access_blocker":
        actions.append(
            "Resolve private index, VCS, or credential requirements before dependency installation."
        )
    if status == "warning" and reason == "unsupported_install_command":
        actions.append(
            "The recommended install command is not a supported python -m pip install form; prepare dependencies manually or add a safe adapter."
        )
    if status == "warning" and reason == "repository_root_missing_for_install":
        actions.append(
            "Provide --repository-test-root or --checkout-repository-tests before running root-relative dependency installation."
        )
    if status == "warning" and reason == "config_files_missing_in_checkout":
        actions.append(
            "Verify checkout ref/depth because root-relative dependency files are missing."
        )
    if root_required:
        actions.append("Run the install command from the full repository checkout.")
    return actions


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")
