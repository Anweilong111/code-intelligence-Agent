from __future__ import annotations

import ast
import configparser
import importlib.util
import json
import re
import shlex
import tomllib
from pathlib import Path
from typing import Any


def plan_repository_test_environment(
    repository_profile: dict[str, Any],
    *,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    config_files = [
        str(path)
        for path in _list(repository_profile.get("project_config_files"))
        if path
    ]
    command = str(repository_profile.get("recommended_test_command") or "").strip()
    root_path = Path(repository_root) if repository_root is not None else None
    root_present = bool(root_path and root_path.exists() and root_path.is_dir())
    missing_config_files = (
        _missing_config_files(config_files, root_path) if root_present and root_path else []
    )
    test_module = _test_module(command)
    tool_status = _tool_status(test_module)
    pytest_config = _pytest_configuration(
        config_files=config_files,
        root_path=root_path if root_present else None,
    )
    ci_config = _ci_configuration(
        config_files=config_files,
        root_path=root_path if root_present else None,
    )
    install_command, install_reason = _recommended_install_command(
        config_files,
        command,
        ci_config=ci_config,
    )
    framework_config = _framework_test_configuration(
        repository_profile=repository_profile,
        config_files=config_files,
        root_path=root_path if root_present else None,
    )
    planned_environment_variables = _safe_environment_variables(
        {
            **_dict(framework_config.get("environment_variables")),
            **_dict(pytest_config.get("environment_variables")),
        }
    )
    status = "pass"
    reason = "environment_plan_built"
    if not command:
        status = "skipped"
        reason = "no_recommended_test_command"
    elif tool_status.get("available") is False:
        status = "warning"
        reason = "test_tool_missing"
    elif missing_config_files:
        status = "warning"
        reason = "config_files_missing_in_checkout"
    elif str(framework_config.get("status") or "") == "warning":
        status = "warning"
        reason = "framework_test_configuration_incomplete"
    return {
        "status": status,
        "reason": reason,
        "recommended_test_command": command,
        "recommended_install_command": install_command,
        "install_command_reason": install_reason,
        "repository_root": str(root_path) if root_path is not None else "",
        "repository_root_present": root_present,
        "project_config_files": config_files,
        "dependency_files": _dependency_files(config_files),
        "test_module": test_module,
        "test_tool_available": tool_status.get("available"),
        "test_tool_check": tool_status,
        "missing_config_files": missing_config_files,
        "pytest_configuration": pytest_config,
        "pytest_config_source_count": _int(pytest_config.get("source_count", 0)),
        "pytest_config_addopts": [
            str(item) for item in _list(pytest_config.get("addopts"))
        ],
        "pytest_config_testpaths": [
            str(item) for item in _list(pytest_config.get("testpaths"))
        ],
        "ci_configuration": ci_config,
        "ci_config_source_count": _int(ci_config.get("source_count", 0)),
        "ci_python_versions": [
            str(item) for item in _list(ci_config.get("python_versions"))
        ],
        "ci_install_command_candidates": [
            str(_dict(item).get("command") or "")
            for item in _list(ci_config.get("install_commands"))
            if str(_dict(item).get("command") or "")
        ],
        "ci_test_command_candidates": [
            str(_dict(item).get("command") or "")
            for item in _list(ci_config.get("test_commands"))
            if str(_dict(item).get("command") or "")
        ],
        "tox_envlist": [
            str(item) for item in _list(ci_config.get("tox_envlist"))
        ],
        "framework_signals": [
            str(item) for item in _list(framework_config.get("frameworks"))
        ],
        "framework_test_configuration": framework_config,
        "planned_environment_variables": planned_environment_variables,
        "planned_environment_variable_names": sorted(
            str(key) for key in planned_environment_variables
        ),
        "next_actions": _next_actions(
            command=command,
            install_command=install_command,
            status=status,
            reason=reason,
            test_module=test_module,
            framework_config=framework_config,
            pytest_config=pytest_config,
            ci_config=ci_config,
        ),
    }


def render_repository_test_environment_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Repository Test Environment Plan",
        "",
        f"- Status: `{_markdown_cell(payload.get('status', ''))}`",
        f"- Reason: `{_markdown_cell(payload.get('reason', ''))}`",
        (
            "- Recommended Test Command: "
            f"`{_markdown_cell(payload.get('recommended_test_command') or 'none')}`"
        ),
        (
            "- Recommended Install Command: "
            f"`{_markdown_cell(payload.get('recommended_install_command') or 'none')}`"
        ),
        f"- Install Reason: `{_markdown_cell(payload.get('install_command_reason') or 'none')}`",
        f"- Repository Root: `{_markdown_cell(payload.get('repository_root') or 'none')}`",
        f"- Repository Root Present: {str(bool(payload.get('repository_root_present'))).lower()}",
        f"- Test Module: `{_markdown_cell(payload.get('test_module') or 'none')}`",
        f"- Test Tool Available: `{_markdown_cell(payload.get('test_tool_available'))}`",
        (
            "- Framework Signals: "
            f"{', '.join(str(item) for item in _list(payload.get('framework_signals'))) or 'none'}"
        ),
        (
            "- Planned Environment Variables: "
            f"{', '.join(str(item) for item in _list(payload.get('planned_environment_variable_names'))) or 'none'}"
        ),
        (
            "- Pytest Config Sources: "
            f"{_int(payload.get('pytest_config_source_count', 0))}"
        ),
        "",
        "## Project Config Files",
        "",
    ]
    for path in _list(payload.get("project_config_files")):
        lines.append(f"- `{_markdown_cell(path)}`")
    if not _list(payload.get("project_config_files")):
        lines.append("- none")
    lines.extend(["", "## Dependency Files", ""])
    for path in _list(payload.get("dependency_files")):
        lines.append(f"- `{_markdown_cell(path)}`")
    if not _list(payload.get("dependency_files")):
        lines.append("- none")
    missing = _list(payload.get("missing_config_files"))
    if missing:
        lines.extend(["", "## Missing Config Files In Checkout", ""])
        for path in missing:
            lines.append(f"- `{_markdown_cell(path)}`")
    pytest_config = _dict(payload.get("pytest_configuration"))
    lines.extend(["", "## Pytest Configuration", ""])
    lines.append(f"- Status: `{_markdown_cell(pytest_config.get('status') or 'none')}`")
    lines.append(f"- Reason: `{_markdown_cell(pytest_config.get('reason') or 'none')}`")
    lines.append(
        "- Addopts: "
        + (
            " ".join(str(item) for item in _list(pytest_config.get("addopts")))
            or "none"
        )
    )
    lines.append(
        "- Testpaths: "
        + (
            ", ".join(str(item) for item in _list(pytest_config.get("testpaths")))
            or "none"
        )
    )
    lines.append(
        "- Environment Variables: "
        + (
            ", ".join(
                f"{key}={value}"
                for key, value in _dict(pytest_config.get("environment_variables")).items()
            )
            or "none"
        )
    )
    ci_config = _dict(payload.get("ci_configuration"))
    lines.extend(["", "## CI Test Configuration", ""])
    lines.append(f"- Status: `{_markdown_cell(ci_config.get('status') or 'none')}`")
    lines.append(f"- Reason: `{_markdown_cell(ci_config.get('reason') or 'none')}`")
    lines.append(
        "- Sources: "
        + (
            ", ".join(str(item) for item in _list(ci_config.get("sources")))
            or "none"
        )
    )
    lines.append(
        "- Python Versions: "
        + (
            ", ".join(str(item) for item in _list(ci_config.get("python_versions")))
            or "none"
        )
    )
    lines.append(
        "- Tox Envlist: "
        + (
            ", ".join(str(item) for item in _list(ci_config.get("tox_envlist")))
            or "none"
        )
    )
    lines.append(
        "- Setup Python Detected: "
        + str(bool(ci_config.get("setup_python_detected", False))).lower()
    )
    ci_install_commands = _list(ci_config.get("install_commands"))
    lines.extend(["", "### CI Install Command Candidates", ""])
    for candidate in ci_install_commands:
        row = _dict(candidate)
        lines.append(
            "- "
            f"`{_markdown_cell(row.get('command') or '')}` "
            f"from `{_markdown_cell(row.get('source') or '')}` "
            f"({ _markdown_cell(row.get('reason') or '')})"
        )
    if not ci_install_commands:
        lines.append("- none")
    ci_test_commands = _list(ci_config.get("test_commands"))
    lines.extend(["", "### CI Test Command Candidates", ""])
    for candidate in ci_test_commands:
        row = _dict(candidate)
        lines.append(
            "- "
            f"`{_markdown_cell(row.get('command') or '')}` "
            f"from `{_markdown_cell(row.get('source') or '')}` "
            f"({ _markdown_cell(row.get('runner') or '')}; "
            f"{_markdown_cell(row.get('reason') or '')})"
        )
    if not ci_test_commands:
        lines.append("- none")
    framework_config = _dict(payload.get("framework_test_configuration"))
    lines.extend(["", "## Framework Test Configuration", ""])
    lines.append(f"- Status: `{_markdown_cell(framework_config.get('status') or 'none')}`")
    lines.append(f"- Reason: `{_markdown_cell(framework_config.get('reason') or 'none')}`")
    lines.append(
        "- Environment Variables: "
        + (
            ", ".join(
                f"{key}={value}"
                for key, value in _dict(
                    framework_config.get("environment_variables")
                ).items()
            )
            or "none"
        )
    )
    candidates = _list(framework_config.get("django_settings_candidates"))
    if candidates:
        lines.extend(["", "### Django Settings Candidates", ""])
        for candidate in candidates:
            row = _dict(candidate)
            lines.append(
                "- "
                f"`{_markdown_cell(row.get('module') or '')}` "
                f"from `{_markdown_cell(row.get('source_path') or '')}` "
                f"({ _float(row.get('confidence', 0.0)):.2f})"
            )
    app_candidates = _list(framework_config.get("app_bootstrap_candidates"))
    if app_candidates:
        lines.extend(["", "### App Bootstrap Candidates", ""])
        for candidate in app_candidates:
            row = _dict(candidate)
            lines.append(
                "- "
                f"{_markdown_cell(row.get('framework') or '')}: "
                f"`{_markdown_cell(row.get('app_import') or '')}` "
                f"from `{_markdown_cell(row.get('source_path') or '')}` "
                f"({ _float(row.get('confidence', 0.0)):.2f})"
            )
    bootstrap_signals = _list(framework_config.get("test_bootstrap_signals"))
    if bootstrap_signals:
        lines.extend(["", "### Test Bootstrap Signals", ""])
        for signal in bootstrap_signals:
            row = _dict(signal)
            lines.append(
                "- "
                f"{_markdown_cell(row.get('framework') or '')}: "
                f"{_markdown_cell(row.get('signal') or '')} "
                f"in `{_markdown_cell(row.get('source_path') or '')}`"
            )
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    return "\n".join(lines)


def write_repository_test_environment_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_environment.json"
    markdown_path = root / "repository_test_environment.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_environment_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_environment_json": str(json_path),
        "repository_test_environment_markdown": str(markdown_path),
    }


def _recommended_install_command(
    config_files: list[str],
    test_command: str,
    *,
    ci_config: dict[str, Any] | None = None,
) -> tuple[str, str]:
    names = _root_config_names(config_files)
    if _test_module(test_command) == "tox":
        return "python -m pip install tox", "tox_runner"
    if _test_module(test_command) == "nox":
        return "python -m pip install nox", "nox_runner"
    for name in ("requirements-test.txt", "requirements-dev.txt", "requirements.txt"):
        if name in names:
            return f"python -m pip install -r {name}", name
    if "uv.lock" in names:
        return "uv sync --dev", "uv_lock"
    if "pdm.lock" in names or "pdm.toml" in names:
        return "pdm install -d", "pdm_project"
    if "hatch.toml" in names:
        return "hatch env create", "hatch_project"
    if "poetry.lock" in names:
        return "poetry install --with dev", "poetry_lock"
    if "Pipfile" in names or "Pipfile.lock" in names:
        return "pipenv install --dev", "pipfile"
    if {"pyproject.toml", "setup.py", "setup.cfg"} & names:
        return "python -m pip install -e .", "editable_project"
    for candidate in _list(_dict(ci_config).get("install_commands")):
        command = str(_dict(candidate).get("command") or "").strip()
        if command:
            return command, "ci_install_candidate"
    return "", "no_dependency_manifest"


def _test_module(command: str) -> str:
    try:
        args = shlex.split(command)
    except ValueError:
        return ""
    if len(args) >= 3 and args[1] == "-m":
        return args[2]
    return ""


def _tool_status(module: str) -> dict[str, Any]:
    if not module:
        return {"module": "", "available": None, "reason": "no_module"}
    available = importlib.util.find_spec(module) is not None
    return {
        "module": module,
        "available": available,
        "reason": "module_found" if available else "module_missing",
    }


def _dependency_files(config_files: list[str]) -> list[str]:
    names = {
        "Pipfile",
        "Pipfile.lock",
        "hatch.toml",
        "pdm.lock",
        "pdm.toml",
        "poetry.lock",
        "pyproject.toml",
        "requirements-dev.txt",
        "requirements-test.txt",
        "requirements.txt",
        "setup.cfg",
        "setup.py",
        "uv.lock",
    }
    return [
        path
        for path in config_files
        if Path(path).name in names and _is_root_config_path(path)
    ]


def _root_config_names(config_files: list[str]) -> set[str]:
    return {Path(path).name for path in config_files if _is_root_config_path(path)}


def _is_root_config_path(path: str) -> bool:
    normalized = str(path).replace("\\", "/").strip("/")
    return bool(normalized) and "/" not in normalized


def _missing_config_files(config_files: list[str], root: Path) -> list[str]:
    missing = []
    for path in config_files:
        if not (root / path).exists():
            missing.append(path)
    return missing


def _pytest_configuration(
    *,
    config_files: list[str],
    root_path: Path | None,
) -> dict[str, Any]:
    if root_path is None:
        return {
            "status": "skipped",
            "reason": "repository_root_missing",
            "source_count": 0,
            "sources": [],
            "addopts": [],
            "testpaths": [],
            "environment_variables": {},
            "environment_variable_names": [],
        }
    sources: list[str] = []
    raw_addopts: list[str] = []
    raw_testpaths: list[str] = []
    raw_env: dict[str, str] = {}
    for path in config_files:
        normalized = str(path).replace("\\", "/").strip("/")
        name = Path(normalized).name
        full_path = root_path / normalized
        text = _read_small_text(full_path)
        if not text:
            continue
        if name == "pyproject.toml":
            parsed = _parse_pyproject_pytest_config(text)
        elif name in {"pytest.ini", "setup.cfg", "tox.ini"}:
            parsed = _parse_ini_pytest_config(text, name=name)
        elif normalized.startswith(".github/workflows/"):
            parsed = _parse_github_actions_env(text)
        else:
            parsed = {}
        if not parsed:
            continue
        sources.append(normalized)
        raw_addopts.extend(str(item) for item in _list(parsed.get("addopts")))
        raw_testpaths.extend(str(item) for item in _list(parsed.get("testpaths")))
        raw_env.update(
            {
                str(key): str(value)
                for key, value in _dict(parsed.get("environment_variables")).items()
            }
        )
    addopts = _safe_pytest_addopts(raw_addopts)
    testpaths = _safe_testpaths(raw_testpaths)
    environment_variables = _safe_environment_variables(raw_env)
    return {
        "status": "pass" if sources else "skipped",
        "reason": "pytest_config_detected" if sources else "no_pytest_config_detected",
        "source_count": len(sources),
        "sources": sorted(set(sources)),
        "addopts": addopts,
        "testpaths": testpaths,
        "environment_variables": environment_variables,
        "environment_variable_names": sorted(environment_variables),
        "raw_addopts_count": len(raw_addopts),
        "raw_testpaths_count": len(raw_testpaths),
    }


def _parse_pyproject_pytest_config(text: str) -> dict[str, Any]:
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    options = _dict(_dict(_dict(payload.get("tool")).get("pytest")).get("ini_options"))
    if not options:
        return {}
    return {
        "addopts": _config_value_list(options.get("addopts")),
        "testpaths": _config_value_list(options.get("testpaths")),
        "environment_variables": _env_from_config_value(options.get("env")),
    }


def _parse_ini_pytest_config(text: str, *, name: str) -> dict[str, Any]:
    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error:
        return {}
    addopts: list[str] = []
    testpaths: list[str] = []
    env: dict[str, str] = {}
    pytest_sections = ["pytest", "tool:pytest"]
    for section in pytest_sections:
        if parser.has_section(section):
            addopts.extend(_config_value_list(parser.get(section, "addopts", fallback="")))
            testpaths.extend(_config_value_list(parser.get(section, "testpaths", fallback="")))
            env.update(_env_from_config_value(parser.get(section, "env", fallback="")))
    if name == "tox.ini" and parser.has_section("testenv"):
        env.update(_env_from_config_value(parser.get("testenv", "setenv", fallback="")))
        command_lines = _config_command_lines(parser.get("testenv", "commands", fallback=""))
        for line in command_lines:
            addopts.extend(_pytest_args_from_command_line(line))
    return {
        "addopts": addopts,
        "testpaths": testpaths,
        "environment_variables": env,
    }


def _parse_github_actions_env(text: str) -> dict[str, Any]:
    env: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"\s{2,}([A-Z_][A-Z0-9_]*):\s*['\"]?([^'\"\n#]+)", line)
        if match:
            env[match.group(1)] = match.group(2).strip()
    return {"environment_variables": env} if env else {}


def _ci_configuration(
    *,
    config_files: list[str],
    root_path: Path | None,
) -> dict[str, Any]:
    if root_path is None:
        return {
            "status": "skipped",
            "reason": "repository_root_missing",
            "source_count": 0,
            "sources": [],
            "workflow_files": [],
            "setup_python_detected": False,
            "python_versions": [],
            "install_commands": [],
            "test_commands": [],
            "tox_envlist": [],
            "tox_dependency_files": [],
        }
    sources: list[str] = []
    workflow_files: list[str] = []
    setup_python_detected = False
    python_versions: list[str] = []
    install_commands: list[dict[str, Any]] = []
    test_commands: list[dict[str, Any]] = []
    tox_envlist: list[str] = []
    tox_dependency_files: list[str] = []
    for path in config_files:
        normalized = str(path).replace("\\", "/").strip("/")
        name = Path(normalized).name
        full_path = root_path / normalized
        text = _read_small_text(full_path)
        if not text:
            continue
        parsed: dict[str, Any] = {}
        if normalized.startswith(".github/workflows/"):
            parsed = _parse_github_actions_ci_config(text, source=normalized)
            if parsed:
                workflow_files.append(normalized)
        elif name == "pyproject.toml":
            parsed = _parse_pyproject_ci_config(text, source=normalized)
        elif name == "noxfile.py":
            parsed = _parse_nox_ci_config(text, source=normalized)
        elif name == "tox.ini":
            parsed = _parse_tox_ci_config(text, source=normalized)
        if not parsed:
            continue
        sources.append(normalized)
        setup_python_detected = setup_python_detected or bool(
            parsed.get("setup_python_detected", False)
        )
        python_versions.extend(str(item) for item in _list(parsed.get("python_versions")))
        install_commands.extend(_dict(item) for item in _list(parsed.get("install_commands")))
        test_commands.extend(_dict(item) for item in _list(parsed.get("test_commands")))
        tox_envlist.extend(str(item) for item in _list(parsed.get("tox_envlist")))
        tox_dependency_files.extend(
            str(item) for item in _list(parsed.get("tox_dependency_files"))
        )
    install_commands = _dedupe_command_candidates(install_commands)[:12]
    test_commands = _dedupe_command_candidates(test_commands)[:12]
    python_versions = _dedupe_preserve_order(python_versions)[:12]
    tox_envlist = _dedupe_preserve_order(tox_envlist)[:20]
    tox_dependency_files = _dedupe_preserve_order(tox_dependency_files)[:12]
    return {
        "status": "pass" if sources else "skipped",
        "reason": "ci_config_detected" if sources else "no_ci_config_detected",
        "source_count": len(set(sources)),
        "sources": sorted(set(sources)),
        "workflow_files": sorted(set(workflow_files)),
        "setup_python_detected": setup_python_detected,
        "python_versions": python_versions,
        "install_commands": install_commands,
        "install_command_count": len(install_commands),
        "test_commands": test_commands,
        "test_command_count": len(test_commands),
        "tox_envlist": tox_envlist,
        "tox_dependency_files": tox_dependency_files,
    }


def _parse_github_actions_ci_config(text: str, *, source: str) -> dict[str, Any]:
    lines = text.splitlines()
    python_versions = _python_versions_from_workflow(text)
    install_commands = []
    test_commands = []
    for line in _candidate_ci_lines(lines):
        install_command = _safe_ci_install_command(line)
        if install_command:
            install_commands.append(
                {
                    "command": install_command,
                    "source": source,
                    "reason": "github_actions_install_step",
                }
            )
        test_command = _safe_ci_test_command(line)
        if test_command:
            test_commands.append(
                {
                    "command": test_command["command"],
                    "source": source,
                    "runner": test_command["runner"],
                    "reason": "github_actions_test_step",
                }
            )
    setup_python_detected = bool(
        re.search(r"actions/setup-python(?:@|['\"]|\s)", text, flags=re.IGNORECASE)
    )
    if not (setup_python_detected or python_versions or install_commands or test_commands):
        return {}
    return {
        "setup_python_detected": setup_python_detected,
        "python_versions": python_versions,
        "install_commands": install_commands,
        "test_commands": test_commands,
    }


def _parse_pyproject_ci_config(text: str, *, source: str) -> dict[str, Any]:
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    tool = _dict(payload.get("tool"))
    test_commands: list[dict[str, Any]] = []
    for script in _pyproject_test_script_values(tool):
        test_command = _safe_ci_test_command(script["command"])
        if not test_command:
            continue
        test_commands.append(
            {
                "command": test_command["command"],
                "source": f"{source}:{script['path']}",
                "runner": test_command["runner"],
                "reason": "pyproject_test_script",
            }
        )
    if not test_commands:
        return {}
    return {"test_commands": test_commands}


def _parse_nox_ci_config(text: str, *, source: str) -> dict[str, Any]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    install_commands: list[dict[str, Any]] = []
    test_commands: list[dict[str, Any]] = []
    python_versions: list[str] = []
    dependency_files: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.args.args:
            continue
        session_name = node.args.args[0].arg
        source_name = f"{source}:{node.name}"
        python_versions.extend(_nox_session_python_versions(node))
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            if not isinstance(call.func, ast.Attribute):
                continue
            if not isinstance(call.func.value, ast.Name):
                continue
            if call.func.value.id != session_name:
                continue
            args = _literal_string_args(call)
            if not args:
                continue
            if call.func.attr == "install":
                install_command = _safe_nox_install_command(args)
                if install_command:
                    install_commands.append(
                        {
                            "command": install_command,
                            "source": source_name,
                            "reason": "nox_session_install",
                        }
                    )
                    dependency_files.extend(
                        _requirement_files_from_install(install_command)
                    )
            elif call.func.attr == "run":
                test_command = _safe_ci_test_command(shlex.join(args))
                if test_command:
                    test_commands.append(
                        {
                            "command": test_command["command"],
                            "source": source_name,
                            "runner": test_command["runner"],
                            "reason": "nox_session_run",
                        }
                    )
    if not (install_commands or test_commands or python_versions or dependency_files):
        return {}
    return {
        "python_versions": python_versions,
        "install_commands": install_commands,
        "test_commands": test_commands,
        "tox_dependency_files": dependency_files,
    }


def _nox_session_python_versions(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    versions: list[str] = []
    version_pattern = re.compile(r"(?:pypy-)?\d+(?:\.\d+){1,2}")
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        if not _is_nox_session_decorator(decorator.func):
            continue
        for keyword in decorator.keywords:
            if keyword.arg != "python":
                continue
            for value in _literal_string_values(keyword.value):
                versions.extend(version_pattern.findall(value))
    return _dedupe_preserve_order(versions)


def _is_nox_session_decorator(func: ast.AST) -> bool:
    if isinstance(func, ast.Attribute):
        return (
            func.attr == "session"
            and isinstance(func.value, ast.Name)
            and func.value.id == "nox"
        )
    return isinstance(func, ast.Name) and func.id == "session"


def _literal_string_args(call: ast.Call) -> list[str]:
    values: list[str] = []
    for arg in call.args:
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
            return []
        values.append(arg.value)
    return values


def _literal_string_values(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple)):
        values: list[str] = []
        for item in node.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return []
            values.append(item.value)
        return values
    return []


def _safe_nox_install_command(args: list[str]) -> str:
    safe_parts = _safe_pip_install_parts(args)
    if not safe_parts:
        return ""
    return shlex.join(["python", "-m", "pip", "install", *safe_parts])


def _pyproject_test_script_values(tool: dict[str, Any]) -> list[dict[str, str]]:
    scripts: list[dict[str, str]] = []
    pdm_scripts = _dict(_dict(tool.get("pdm")).get("scripts"))
    scripts.extend(
        _named_script_commands(
            pdm_scripts,
            path_prefix="tool.pdm.scripts",
        )
    )
    hatch_envs = _dict(_dict(tool.get("hatch")).get("envs"))
    for env_name, env_value in hatch_envs.items():
        env = _dict(env_value)
        scripts.extend(
            _named_script_commands(
                _dict(env.get("scripts")),
                path_prefix=f"tool.hatch.envs.{env_name}.scripts",
            )
        )
    poetry_scripts = _dict(_dict(tool.get("poetry")).get("scripts"))
    scripts.extend(
        _named_script_commands(
            poetry_scripts,
            path_prefix="tool.poetry.scripts",
        )
    )
    return scripts


def _named_script_commands(
    scripts: dict[str, Any],
    *,
    path_prefix: str,
) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    for name, value in scripts.items():
        for command in _script_command_values(value):
            if _looks_like_test_script(str(name), command):
                commands.append(
                    {
                        "path": f"{path_prefix}.{name}",
                        "command": command,
                    }
                )
    return commands


def _script_command_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        commands: list[str] = []
        for item in value:
            commands.extend(_script_command_values(item))
        return commands
    if isinstance(value, dict):
        commands: list[str] = []
        for key in ("cmd", "shell"):
            raw = value.get(key)
            if isinstance(raw, str):
                commands.append(raw)
        composite = value.get("composite")
        if isinstance(composite, list):
            commands.extend(
                str(item)
                for item in composite
                if isinstance(item, str)
            )
        return commands
    return []


def _looks_like_test_script(name: str, command: str) -> bool:
    lowered_name = name.lower().replace("-", "_")
    lowered_command = command.lower()
    if any(token in lowered_name for token in ("test", "pytest", "unit")):
        return True
    return bool(re.search(r"\b(pytest|py\.test|tox|nox)\b", lowered_command))


def _parse_tox_ci_config(text: str, *, source: str) -> dict[str, Any]:
    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error:
        return {}
    envlist = []
    install_commands = []
    test_commands = []
    dependency_files = []
    if parser.has_section("tox"):
        envlist.extend(_safe_tox_envlist(parser.get("tox", "envlist", fallback="")))
    if parser.has_section("testenv"):
        deps = _config_value_list(parser.get("testenv", "deps", fallback=""))
        for dep in deps:
            install_command = _safe_tox_dependency_install_command(dep)
            if install_command:
                install_commands.append(
                    {
                        "command": install_command,
                        "source": source,
                        "reason": "tox_dependency_file",
                    }
                )
                dependency_files.extend(_requirement_files_from_install(install_command))
        commands = _config_command_lines(parser.get("testenv", "commands", fallback=""))
        for command in commands:
            test_command = _safe_ci_test_command(command)
            if test_command:
                test_commands.append(
                    {
                        "command": test_command["command"],
                        "source": source,
                        "runner": test_command["runner"],
                        "reason": "tox_test_command",
                    }
                )
    if not (envlist or install_commands or test_commands or dependency_files):
        return {}
    return {
        "tox_envlist": envlist,
        "tox_dependency_files": dependency_files,
        "install_commands": install_commands,
        "test_commands": test_commands,
    }


def _python_versions_from_workflow(text: str) -> list[str]:
    versions: list[str] = []
    version_pattern = r"(?:pypy-)?\d+(?:\.\d+){1,2}"
    for match in re.finditer(
        r"python-version\s*:\s*(?:\[(?P<bracket>[^\]]+)\]|(?P<single>[^\n#]+))",
        text,
        flags=re.IGNORECASE,
    ):
        raw = match.group("bracket") or match.group("single") or ""
        versions.extend(re.findall(version_pattern, raw))
    for match in re.finditer(
        r"python-version\s*:\s*\n(?P<block>(?:\s*-\s*['\"]?(?:pypy-)?\d+(?:\.\d+){1,2}['\"]?\s*\n?)+)",
        text,
        flags=re.IGNORECASE,
    ):
        versions.extend(re.findall(version_pattern, match.group("block") or ""))
    return _dedupe_preserve_order(versions)


def _candidate_ci_lines(lines: list[str]) -> list[str]:
    candidates: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        run_match = re.match(r"-?\s*run\s*:\s*(.+)$", stripped)
        if run_match:
            value = run_match.group(1).strip()
            if value and value not in {"|", ">"}:
                candidates.append(value)
            continue
        if re.match(
            r"(python(?:3)?|pip(?:3)?|pytest|py\.test|tox|nox|hatch|uv|poetry|pdm|coverage)\b",
            stripped,
        ):
            candidates.append(stripped)
    return candidates


def _safe_ci_install_command(line: str) -> str:
    if not _safe_ci_line(line):
        return ""
    try:
        parts = shlex.split(line)
    except ValueError:
        return ""
    if len(parts) >= 4 and _is_python_command(parts[0]) and parts[1:4] == [
        "-m",
        "pip",
        "install",
    ]:
        install_parts = parts[4:]
    elif len(parts) >= 2 and parts[0] in {"pip", "pip3"} and parts[1] == "install":
        install_parts = parts[2:]
    else:
        return ""
    safe_parts = _safe_pip_install_parts(install_parts)
    if not safe_parts:
        return ""
    return shlex.join(["python", "-m", "pip", "install", *safe_parts])


def _safe_tox_dependency_install_command(line: str) -> str:
    text = str(line or "").strip()
    if not text.startswith("-r "):
        return ""
    try:
        parts = shlex.split(text)
    except ValueError:
        return ""
    if len(parts) != 2 or parts[0] != "-r":
        return ""
    path = _safe_requirement_path(parts[1])
    if not path:
        return ""
    return shlex.join(["python", "-m", "pip", "install", "-r", path])


def _safe_ci_test_command(line: str) -> dict[str, str]:
    if not _safe_ci_line(line):
        return {}
    try:
        parts = shlex.split(line)
    except ValueError:
        return {}
    if not parts:
        return {}
    runner, args = _ci_test_runner_parts(parts)
    if runner == "pytest":
        addopts = _safe_pytest_addopts(args)
        testpaths = _safe_testpaths(
            [part for part in args if not part.startswith("-") and "=" not in part]
        )
        command = shlex.join(["python", "-m", "pytest", *addopts, *testpaths])
        return {"runner": "pytest", "command": command}
    if runner in {"tox", "nox"}:
        return {"runner": runner, "command": shlex.join(["python", "-m", runner])}
    return {}


def _ci_test_runner_parts(parts: list[str]) -> tuple[str, list[str]]:
    if not parts:
        return "", []
    if _is_python_command(parts[0]) and len(parts) >= 3 and parts[1] == "-m":
        if parts[2] == "tox":
            tox_pytest = _tox_passthrough_pytest_parts(parts[3:])
            if tox_pytest:
                return tox_pytest
        if parts[2] in {"pytest", "tox", "nox"}:
            return parts[2], parts[3:]
        if parts[2] == "coverage":
            return _coverage_pytest_parts(parts[3:])
    if parts[0] == "tox":
        tox_pytest = _tox_passthrough_pytest_parts(parts[1:])
        if tox_pytest:
            return tox_pytest
    if parts[0] in {"pytest", "py.test", "tox", "nox"}:
        return ("pytest" if parts[0] == "py.test" else parts[0]), parts[1:]
    if parts[0] == "hatch" and len(parts) >= 3 and parts[1] == "run":
        return _ci_test_runner_parts(_strip_hatch_runner_options(parts[2:]))
    if parts[0] in {"uv", "poetry", "pdm"} and len(parts) >= 3 and parts[1] == "run":
        return _ci_test_runner_parts(_strip_managed_runner_options(parts[2:]))
    if parts[0] == "coverage":
        return _coverage_pytest_parts(parts[1:])
    return "", []


def _tox_passthrough_pytest_parts(parts: list[str]) -> tuple[str, list[str]]:
    try:
        separator = parts.index("--")
    except ValueError:
        return "", []
    if separator >= len(parts) - 1:
        return "", []
    return _ci_test_runner_parts(parts[separator + 1 :])


def _coverage_pytest_parts(parts: list[str]) -> tuple[str, list[str]]:
    if not parts or parts[0] != "run":
        return "", []
    for index in range(1, len(parts) - 1):
        if parts[index] == "-m" and parts[index + 1] == "pytest":
            return "pytest", parts[index + 2 :]
    return "", []


def _strip_managed_runner_options(parts: list[str]) -> list[str]:
    index = 0
    value_options = {"--env", "--extra", "--group", "--python", "--with"}
    flag_options = {
        "--all-extras",
        "--dev",
        "--isolated",
        "--locked",
        "--no-dev",
        "--no-sync",
        "--sync",
    }
    while index < len(parts):
        token = parts[index]
        if token in value_options and index + 1 < len(parts):
            index += 2
            continue
        if any(token.startswith(f"{option}=") for option in value_options):
            index += 1
            continue
        if token in flag_options:
            index += 1
            continue
        break
    return parts[index:]


def _strip_hatch_runner_options(parts: list[str]) -> list[str]:
    index = 0
    value_options = {"-e", "--env"}
    flag_options = {"--sync", "--no-sync", "--isolated"}
    while index < len(parts):
        token = parts[index]
        if token in value_options and index + 1 < len(parts):
            index += 2
            continue
        if any(token.startswith(f"{option}=") for option in value_options):
            index += 1
            continue
        if token in flag_options or token.startswith("+"):
            index += 1
            continue
        break
    return parts[index:]


def _safe_ci_line(line: str) -> bool:
    text = str(line or "").strip()
    if not text or len(text) > 400:
        return False
    if any(token in text for token in ("&&", "||", ";", "`", "$", "{", "}", ">", "<")):
        return False
    return True


def _safe_pip_install_parts(parts: list[str]) -> list[str]:
    safe: list[str] = []
    index = 0
    while index < len(parts):
        part = parts[index]
        if part in {"-r", "--requirement"} and index + 1 < len(parts):
            path = _safe_requirement_path(parts[index + 1])
            if not path:
                return []
            safe.extend(["-r", path])
            index += 2
            continue
        if part == "-e" and index + 1 < len(parts):
            editable = parts[index + 1]
            if editable != ".":
                return []
            safe.extend(["-e", "."])
            index += 2
            continue
        if part == ".":
            safe.append(".")
            index += 1
            continue
        if _safe_python_package_name(part):
            safe.append(part)
            index += 1
            continue
        return []
    return safe


def _safe_requirement_path(value: str) -> str:
    normalized = str(value).replace("\\", "/").strip().strip("'\"").strip("/")
    if not normalized or ".." in normalized.split("/"):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", normalized):
        return ""
    if not (normalized.endswith(".txt") or normalized.endswith(".in")):
        return ""
    return normalized


def _safe_python_package_name(value: str) -> bool:
    allowed = {
        "coverage",
        "nox",
        "pytest",
        "pytest-cov",
        "pytest-django",
        "pytest-asyncio",
        "tox",
    }
    return value in allowed or bool(
        re.fullmatch(
            r"(coverage|nox|pytest|pytest-cov|pytest-django|pytest-asyncio|tox)(==|>=|<=|~=)[A-Za-z0-9_.!*+-]+",
            value,
        )
    )


def _safe_tox_envlist(value: str) -> list[str]:
    envs: list[str] = []
    for raw_line in str(value or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [line] if "{" in line and "}" in line else line.split(",")
        for item in parts:
            text = str(item).strip()
            if re.fullmatch(r"[A-Za-z0-9_.{}-]+", text):
                envs.append(text)
    if not envs:
        for item in _config_value_list(value):
            text = str(item).strip().strip(",")
            if re.fullmatch(r"[A-Za-z0-9_.{}-]+", text):
                envs.append(text)
    return _dedupe_preserve_order(envs)


def _requirement_files_from_install(command: str) -> list[str]:
    try:
        parts = shlex.split(command)
    except ValueError:
        return []
    files: list[str] = []
    for index, part in enumerate(parts[:-1]):
        if part == "-r":
            files.append(parts[index + 1])
    return files


def _is_python_command(value: str) -> bool:
    return value in {"python", "python3"} or bool(
        re.fullmatch(r"python\d(?:\.\d+)?", value)
    )


def _dedupe_command_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for candidate in candidates:
        row = _dict(candidate)
        command = str(row.get("command") or "")
        source = str(row.get("source") or "")
        reason = str(row.get("reason") or "")
        key = (command, source, reason)
        if not command or key in seen:
            continue
        seen.add(key)
        deduped.append(
            {
                key_name: row[key_name]
                for key_name in ("command", "source", "reason", "runner")
                if key_name in row and row[key_name]
            }
        )
    return deduped


def _pytest_args_from_command_line(line: str) -> list[str]:
    try:
        parts = shlex.split(line)
    except ValueError:
        return []
    for index, part in enumerate(parts):
        if part in {"pytest", "py.test"}:
            return parts[index + 1 :]
        if part == "-m" and index + 1 < len(parts) and parts[index + 1] == "pytest":
            return parts[index + 2 :]
    return []


def _config_value_list(value: Any) -> list[str]:
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(_config_value_list(item))
        return values
    text = str(value or "").strip()
    if not text:
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        return lines
    try:
        return shlex.split(text)
    except ValueError:
        return [text]


def _config_command_lines(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _env_from_config_value(value: Any) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in _config_value_list(value):
        if "=" not in item:
            continue
        key, raw_value = item.split("=", 1)
        env[key.strip()] = raw_value.strip()
    if isinstance(value, dict):
        env.update({str(key): str(val) for key, val in value.items()})
    return env


def _safe_pytest_addopts(values: list[str]) -> list[str]:
    tokens: list[str] = []
    for value in values:
        try:
            parts = shlex.split(value)
        except ValueError:
            parts = [value]
        tokens.extend(parts)
    safe: list[str] = []
    allowed_exact = {
        "--disable-warnings",
        "--failed-first",
        "--ff",
        "--last-failed",
        "--lf",
        "--strict-config",
        "--strict-markers",
        "-q",
        "-ra",
        "-s",
        "-x",
    }
    allowed_regex = (
        r"--maxfail=\d+",
        r"--tb=(auto|long|short|line|native|no)",
    )
    for token in tokens:
        if token in allowed_exact or any(re.fullmatch(pattern, token) for pattern in allowed_regex):
            safe.append(token)
    return _dedupe_preserve_order(safe)[:12]


def _safe_testpaths(values: list[str]) -> list[str]:
    safe: list[str] = []
    for value in values:
        normalized = str(value).replace("\\", "/").strip().strip("'\"").strip("/")
        if not normalized or normalized.startswith("."):
            continue
        if ".." in normalized.split("/"):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", normalized):
            continue
        safe.append(normalized)
    return _dedupe_preserve_order(safe)[:12]


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _framework_test_configuration(
    *,
    repository_profile: dict[str, Any],
    config_files: list[str],
    root_path: Path | None,
) -> dict[str, Any]:
    profile_framework = _dict(repository_profile.get("framework_profile"))
    frameworks = {
        str(item)
        for item in _list(repository_profile.get("framework_signals"))
        + _list(profile_framework.get("frameworks"))
        if str(item)
    }
    local_frameworks = _local_dependency_frameworks(config_files, root_path)
    app_bootstrap_candidates = _local_app_bootstrap_candidates(root_path)
    test_bootstrap_signals = _local_test_bootstrap_signals(root_path)
    frameworks.update(local_frameworks)
    frameworks.update(
        str(item.get("framework") or "")
        for item in app_bootstrap_candidates
        if str(item.get("framework") or "")
    )
    frameworks.update(
        str(item.get("framework") or "")
        for item in test_bootstrap_signals
        if str(item.get("framework") or "")
    )
    django_settings_candidates = [
        _dict(item)
        for item in _list(profile_framework.get("django_settings_candidates"))
    ]
    local_django_setting = _local_django_settings_module(
        config_files=config_files,
        root_path=root_path,
        candidate_paths=[
            str(item.get("source_path") or "")
            for item in django_settings_candidates
            if str(item.get("source_path") or "")
        ],
    )
    environment_variables: dict[str, str] = {}
    configuration_actions: list[str] = []
    status = "skipped"
    reason = "no_framework_signals"
    if frameworks:
        status = "pass"
        reason = "framework_configuration_plan_built"
    if "django" in frameworks:
        if local_django_setting:
            environment_variables["DJANGO_SETTINGS_MODULE"] = local_django_setting
            reason = "django_settings_module_detected"
        elif django_settings_candidates:
            environment_variables["DJANGO_SETTINGS_MODULE"] = str(
                django_settings_candidates[0].get("module") or ""
            )
            reason = "django_settings_module_inferred_from_layout"
        else:
            status = "warning"
            reason = "django_settings_module_not_inferred"
            configuration_actions.append(
                "Provide DJANGO_SETTINGS_MODULE through pytest configuration or environment variables."
            )
    fastapi_candidates = _framework_app_candidates(
        app_bootstrap_candidates,
        "fastapi",
    )
    if "fastapi" in frameworks:
        if fastapi_candidates:
            if reason == "framework_configuration_plan_built":
                reason = "fastapi_app_bootstrap_detected"
            configuration_actions.append(
                "FastAPI app bootstrap candidate detected: "
                f"{fastapi_candidates[0]['app_import']}."
            )
        else:
            configuration_actions.append(
                "FastAPI tests may require app dependency overrides or async test plugins."
            )
    flask_candidates = _framework_app_candidates(
        app_bootstrap_candidates,
        "flask",
    )
    if "flask" in frameworks:
        if flask_candidates:
            environment_variables["FLASK_APP"] = str(
                flask_candidates[0].get("app_import") or ""
            )
            if reason == "framework_configuration_plan_built":
                reason = "flask_app_bootstrap_detected"
            configuration_actions.append(
                "Flask app bootstrap candidate detected: "
                f"{flask_candidates[0]['app_import']}."
            )
        else:
            configuration_actions.append(
                "Flask tests may require FLASK_APP or app-factory pytest fixtures."
            )
    environment_variables = _safe_environment_variables(environment_variables)
    return {
        "status": status,
        "reason": reason,
        "frameworks": sorted(frameworks),
        "environment_variables": environment_variables,
        "environment_variable_names": sorted(environment_variables),
        "django_settings_candidates": django_settings_candidates[:8],
        "app_bootstrap_candidates": app_bootstrap_candidates[:8],
        "app_bootstrap_candidate_count": len(app_bootstrap_candidates),
        "test_bootstrap_signals": test_bootstrap_signals[:12],
        "test_bootstrap_signal_count": len(test_bootstrap_signals),
        "configuration_actions": configuration_actions,
        "local_dependency_frameworks": sorted(local_frameworks),
    }


def _local_dependency_frameworks(
    config_files: list[str],
    root_path: Path | None,
) -> set[str]:
    if root_path is None:
        return set()
    frameworks: set[str] = set()
    for path in _dependency_files(config_files):
        text = _read_small_text(root_path / path)
        lowered = text.lower()
        if "django" in lowered or "pytest-django" in lowered:
            frameworks.add("django")
        if "fastapi" in lowered:
            frameworks.add("fastapi")
        if "flask" in lowered:
            frameworks.add("flask")
    return frameworks


def _local_django_settings_module(
    *,
    config_files: list[str],
    root_path: Path | None,
    candidate_paths: list[str],
) -> str:
    if root_path is None:
        return ""
    scan_paths = list(config_files)
    scan_paths.extend(["manage.py"])
    scan_paths.extend(candidate_paths)
    for path in sorted(set(path for path in scan_paths if path)):
        value = _extract_django_settings_module(_read_small_text(root_path / path))
        if value:
            return value
    return ""


def _local_app_bootstrap_candidates(root_path: Path | None) -> list[dict[str, Any]]:
    if root_path is None:
        return []
    candidates: list[dict[str, Any]] = []
    for path in _iter_local_python_files(root_path):
        rel_path = _relative_path(root_path, path)
        text = _read_small_text(path)
        if not text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        imports = _framework_factory_imports(tree)
        if not imports:
            continue
        module_name = _module_name_from_local_path(rel_path)
        if not module_name:
            continue
        for node in ast.walk(tree):
            target_name = _assignment_target_name(node)
            call = _assignment_call(node)
            if not target_name or call is None:
                continue
            framework = _framework_from_factory_call(call, imports)
            if not framework:
                continue
            candidates.append(
                {
                    "framework": framework,
                    "app_import": f"{module_name}:{target_name}",
                    "module": module_name,
                    "variable": target_name,
                    "source_path": rel_path,
                    "confidence": _app_bootstrap_confidence(rel_path, target_name),
                    "reason": f"{framework}_app_factory_assignment",
                }
            )
    return sorted(
        _dedupe_app_bootstrap_candidates(candidates),
        key=lambda row: (
            -_float(row.get("confidence", 0.0)),
            str(row.get("framework", "")),
            str(row.get("app_import", "")),
        ),
    )


def _local_test_bootstrap_signals(root_path: Path | None) -> list[dict[str, Any]]:
    if root_path is None:
        return []
    signals: list[dict[str, Any]] = []
    for path in _iter_local_python_files(root_path):
        rel_path = _relative_path(root_path, path)
        normalized = rel_path.lower().replace("\\", "/")
        if "/tests/" not in f"/{normalized}" and _path_name(rel_path) != "conftest.py":
            continue
        lowered = _read_small_text(path).lower()
        if not lowered:
            continue
        if "fastapi.testclient" in lowered or "testclient(" in lowered:
            signals.append(
                {
                    "framework": "fastapi",
                    "signal": "fastapi_testclient_detected",
                    "source_path": rel_path,
                }
            )
        if ".test_client(" in lowered or "flask.testing" in lowered:
            signals.append(
                {
                    "framework": "flask",
                    "signal": "flask_test_client_detected",
                    "source_path": rel_path,
                }
            )
    return _dedupe_test_bootstrap_signals(signals)


def _framework_app_candidates(
    candidates: list[dict[str, Any]],
    framework: str,
) -> list[dict[str, Any]]:
    return [
        candidate
        for candidate in candidates
        if str(candidate.get("framework") or "") == framework
    ]


def _iter_local_python_files(root_path: Path, *, limit: int = 500) -> list[Path]:
    skip_dirs = {
        ".git",
        ".hg",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".repo_test_venv",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
    files: list[Path] = []
    for path in root_path.rglob("*.py"):
        try:
            relative_parts = path.relative_to(root_path).parts
        except ValueError:
            continue
        if any(part in skip_dirs for part in relative_parts):
            continue
        files.append(path)
        if len(files) >= limit:
            break
    return sorted(files)


def _framework_factory_imports(tree: ast.AST) -> dict[str, set[str]]:
    imports = {
        "fastapi_factory_names": set(),
        "fastapi_module_names": set(),
        "flask_factory_names": set(),
        "flask_module_names": set(),
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = str(node.module or "")
            for alias in node.names:
                name = alias.asname or alias.name
                if module == "fastapi" and alias.name == "FastAPI":
                    imports["fastapi_factory_names"].add(name)
                if module == "flask" and alias.name == "Flask":
                    imports["flask_factory_names"].add(name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name
                if alias.name == "fastapi":
                    imports["fastapi_module_names"].add(name)
                if alias.name == "flask":
                    imports["flask_module_names"].add(name)
    return imports


def _assignment_target_name(node: ast.AST) -> str:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name):
            return target.id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return ""


def _assignment_call(node: ast.AST) -> ast.Call | None:
    value = None
    if isinstance(node, ast.Assign):
        value = node.value
    elif isinstance(node, ast.AnnAssign):
        value = node.value
    return value if isinstance(value, ast.Call) else None


def _framework_from_factory_call(
    call: ast.Call,
    imports: dict[str, set[str]],
) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        if func.id in imports["fastapi_factory_names"]:
            return "fastapi"
        if func.id in imports["flask_factory_names"]:
            return "flask"
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if (
            func.value.id in imports["fastapi_module_names"]
            and func.attr == "FastAPI"
        ):
            return "fastapi"
        if func.value.id in imports["flask_module_names"] and func.attr == "Flask":
            return "flask"
    return ""


def _app_bootstrap_confidence(path: str, variable: str) -> float:
    name = _path_name(path)
    confidence = 0.72
    if name in {"main.py", "app.py", "api.py", "server.py"}:
        confidence += 0.12
    if variable in {"app", "application"}:
        confidence += 0.08
    if path.startswith("tests/") or "/tests/" in f"/{path}":
        confidence -= 0.20
    return round(min(max(confidence, 0.1), 0.98), 4)


def _dedupe_app_bootstrap_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for candidate in candidates:
        key = (
            str(candidate.get("framework") or ""),
            str(candidate.get("app_import") or ""),
        )
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _dedupe_test_bootstrap_signals(
    signals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for signal in signals:
        key = (
            str(signal.get("framework") or ""),
            str(signal.get("signal") or ""),
            str(signal.get("source_path") or ""),
        )
        if not key[0] or not key[1] or not key[2] or key in seen:
            continue
        seen.add(key)
        deduped.append(signal)
    return deduped


def _relative_path(root_path: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root_path)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _module_name_from_local_path(path: str) -> str:
    normalized = str(path).replace("\\", "/").strip("/")
    parts = [part for part in normalized.split("/") if part and part != "."]
    if parts and parts[0] == "src":
        parts = parts[1:]
    if not parts:
        return ""
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    else:
        return ""
    if not parts or parts[0] in {"tests", "test"}:
        return ""
    return ".".join(parts)


def _path_name(path: str) -> str:
    return Path(str(path).replace("\\", "/")).name


def _extract_django_settings_module(text: str) -> str:
    patterns = (
        r"DJANGO_SETTINGS_MODULE\s*=\s*['\"]?([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+)",
        r"DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"]([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


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


def _read_small_text(path: Path) -> str:
    try:
        if not path.exists() or not path.is_file():
            return ""
        if path.stat().st_size > 200_000:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _next_actions(
    *,
    command: str,
    install_command: str,
    status: str,
    reason: str,
    test_module: str,
    framework_config: dict[str, Any],
    pytest_config: dict[str, Any],
    ci_config: dict[str, Any],
) -> list[str]:
    actions = []
    if install_command:
        actions.append(
            f"Before running the repository test command, prepare dependencies with: {install_command}"
        )
    if status == "warning" and reason == "test_tool_missing" and test_module:
        actions.append(
            f"Install or expose the `{test_module}` test runner in the Python environment."
        )
    if status == "warning" and reason == "config_files_missing_in_checkout":
        actions.append("Verify checkout depth/ref and ensure project config files exist.")
    environment_variables = _dict(framework_config.get("environment_variables"))
    if environment_variables:
        actions.append(
            "Run tests with inferred framework environment variables: "
            + ", ".join(
                f"{key}={value}" for key, value in environment_variables.items()
            )
        )
    pytest_addopts = _list(pytest_config.get("addopts"))
    pytest_testpaths = _list(pytest_config.get("testpaths"))
    if pytest_addopts:
        actions.append(
            "Apply safe pytest addopts from project config: "
            + " ".join(str(item) for item in pytest_addopts)
        )
    if pytest_testpaths:
        actions.append(
            "Start pytest from configured testpaths: "
            + ", ".join(str(item) for item in pytest_testpaths)
        )
    python_versions = _list(ci_config.get("python_versions"))
    if python_versions:
        actions.append(
            "Match CI Python versions when reproducing repository tests: "
            + ", ".join(str(item) for item in python_versions)
        )
    install_candidates = [
        str(_dict(item).get("command") or "")
        for item in _list(ci_config.get("install_commands"))
        if str(_dict(item).get("command") or "")
    ]
    if install_candidates:
        actions.append(
            "CI install command candidates were extracted for audit: "
            + "; ".join(install_candidates[:3])
        )
    test_candidates = [
        str(_dict(item).get("command") or "")
        for item in _list(ci_config.get("test_commands"))
        if str(_dict(item).get("command") or "")
    ]
    if test_candidates:
        actions.append(
            "CI test command candidates were extracted for comparison: "
            + "; ".join(test_candidates[:3])
        )
    for action in _list(framework_config.get("configuration_actions")):
        actions.append(str(action))
    if command:
        actions.append(f"Then validate with: {command}")
    return actions


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")
