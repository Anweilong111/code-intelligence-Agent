from __future__ import annotations

import configparser
import json
import re
import sys
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any


_ALLOWED_BUILD_BACKENDS = {
    "flit_core.buildapi",
    "hatchling.build",
    "mesonpy",
    "pdm.backend",
    "poetry.core.masonry.api",
    "scikit_build_core.build",
    "setuptools.build_meta",
    "setuptools.build_meta:__legacy__",
}

_PRIVATE_MARKERS = {
    "artifactory",
    "devpi",
    "internal",
    "private",
    "pkgs.dev.azure.com",
    "nexus",
}


def assess_repository_compatibility(
    repository_profile: dict[str, Any],
    *,
    repository_root: str | Path | None = None,
    repository_execution_root: str | Path | None = None,
    current_python: str | tuple[int, ...] | None = None,
) -> dict[str, Any]:
    profile = _dict(repository_profile)
    root = Path(repository_root) if repository_root is not None else None
    root_present = bool(root and root.exists() and root.is_dir())
    if repository_execution_root is None:
        execution_root = root
    elif str(repository_execution_root).strip():
        execution_root = Path(repository_execution_root)
    else:
        execution_root = None
    execution_root_present = bool(
        execution_root and execution_root.exists() and execution_root.is_dir()
    )
    scope = _scope_profile(profile)
    python_profile = _python_compatibility_profile(
        root if root_present else None,
        profile,
        current_python=current_python,
    )
    dependency_access = _dependency_access_profile(
        root if root_present else None,
        profile,
    )
    install_policy = _install_policy(
        root if root_present else None,
        profile,
        dependency_access=dependency_access,
        python_profile=python_profile,
    )
    test_command = str(profile.get("recommended_test_command") or "")
    test_roots = [str(item) for item in _list(profile.get("test_roots"))]
    source_roots = [str(item) for item in _list(profile.get("source_roots"))]
    blockers: list[dict[str, str]] = []
    if str(scope.get("status")) == "unsupported_scope":
        blockers.append(
            _blocker(
                "unsupported_scope",
                "repository_scope",
                "No Python source files were discovered in the selected repository ref.",
            )
        )
    elif not bool(scope.get("analysis_available")):
        blockers.append(
            _blocker(
                "source_selection:no_python_sources_imported",
                "source_selection",
                "Python files exist, but the current source filters selected none for analysis.",
            )
        )
    if str(python_profile.get("status")) == "incompatible":
        blockers.append(
            _blocker(
                "environment:python_version_incompatible",
                "environment",
                str(python_profile.get("reason") or "Python version is incompatible."),
            )
        )
    for category in _list(dependency_access.get("blockers")):
        blockers.append(
            _blocker(
                f"dependency:{category}",
                "dependency_access",
                _dependency_blocker_message(str(category)),
            )
        )

    status = "ready"
    termination_reason = "analysis_and_test_planning_ready"
    if any(row["category"] == "unsupported_scope" for row in blockers):
        status = "blocked"
        termination_reason = "unsupported_scope"
    elif blockers:
        status = "blocked"
        termination_reason = str(blockers[0]["category"])
    elif not test_command:
        status = "partial"
        termination_reason = "test_command:no_recommended_test_command"
    elif not execution_root_present:
        status = "partial"
        termination_reason = "checkout:repository_root_not_materialized"
    elif str(install_policy.get("risk")) == "high":
        status = "partial"
        termination_reason = "safety:high_risk_install_requires_authorization"

    return {
        "schema_version": "repository_compatibility_v2",
        "status": status,
        "termination_reason": termination_reason,
        "primary_blocker": str(blockers[0]["category"]) if blockers else "",
        "blockers": blockers,
        "blocker_count": len(blockers),
        "repository_root": str(root) if root is not None else "",
        "repository_root_present": root_present,
        "repository_execution_root": (
            str(execution_root) if execution_root is not None else ""
        ),
        "repository_execution_root_present": execution_root_present,
        "scope": scope,
        "layout": {
            "type": str(profile.get("layout_type") or "unknown"),
            "monorepo_candidate": bool(profile.get("monorepo_candidate", False)),
            "multi_package": bool(profile.get("multi_package", False)),
            "source_roots": source_roots,
            "test_roots": test_roots,
            "recommended_analysis_roots": [
                str(item)
                for item in _list(profile.get("recommended_analysis_roots"))
            ],
        },
        "tooling": _tooling_profile(profile),
        "python": python_profile,
        "dependency_access": dependency_access,
        "install_policy": install_policy,
        "test_execution": {
            "recommended_command": test_command,
            "command_available": bool(test_command),
            "test_source_count": _int(profile.get("test_source_count")),
            "test_roots": test_roots,
            "readiness": (
                "unsupported"
                if str(scope.get("status")) == "unsupported_scope"
                else "blocked"
                if blockers
                else "checkout_required"
                if not execution_root_present
                else "authorization_required"
                if str(install_policy.get("risk")) == "high"
                else "planned"
                if test_command
                else "no_test_oracle"
            ),
        },
        "next_actions": _next_actions(
            status=status,
            termination_reason=termination_reason,
            install_policy=install_policy,
            source_roots=source_roots,
        ),
    }


def render_repository_compatibility_markdown(payload: dict[str, Any]) -> str:
    scope = _dict(payload.get("scope"))
    layout = _dict(payload.get("layout"))
    python_profile = _dict(payload.get("python"))
    dependency = _dict(payload.get("dependency_access"))
    install = _dict(payload.get("install_policy"))
    tooling = _dict(payload.get("tooling"))
    lines = [
        "# Repository Compatibility Assessment",
        "",
        f"- Status: `{_markdown_cell(payload.get('status') or 'unknown')}`",
        f"- Termination Reason: `{_markdown_cell(payload.get('termination_reason') or 'none')}`",
        f"- Primary Blocker: `{_markdown_cell(payload.get('primary_blocker') or 'none')}`",
        f"- Config Root Present: {str(bool(payload.get('repository_root_present'))).lower()}",
        f"- Execution Root Present: {str(bool(payload.get('repository_execution_root_present'))).lower()}",
        f"- Scope: `{_markdown_cell(scope.get('status') or 'unknown')}`",
        f"- Static Analysis Available: {str(bool(scope.get('analysis_available'))).lower()}",
        f"- Layout: `{_markdown_cell(layout.get('type') or 'unknown')}`",
        f"- Monorepo Candidate: {str(bool(layout.get('monorepo_candidate'))).lower()}",
        f"- Multi Package: {str(bool(layout.get('multi_package'))).lower()}",
        (
            "- Source Roots: "
            + (", ".join(str(item) for item in _list(layout.get("source_roots"))) or "none")
        ),
        (
            "- Test Roots: "
            + (", ".join(str(item) for item in _list(layout.get("test_roots"))) or "none")
        ),
        f"- Python Compatibility: `{_markdown_cell(python_profile.get('status') or 'unknown')}`",
        f"- Current Python: `{_markdown_cell(python_profile.get('current_version') or 'unknown')}`",
        f"- Install Risk: `{_markdown_cell(install.get('risk') or 'unknown')}`",
        f"- Auto Install Allowed: {str(bool(install.get('auto_execution_allowed'))).lower()}",
        f"- Dependency Access Blockers: {_int(dependency.get('blocker_count'))}",
        "",
        "## Tooling",
        "",
        (
            "- Dependency Tools: "
            + (", ".join(str(item) for item in _list(tooling.get("dependency_tools"))) or "none")
        ),
        (
            "- Test Runners: "
            + (", ".join(str(item) for item in _list(tooling.get("test_runners"))) or "none")
        ),
        (
            "- Config Files: "
            + (", ".join(str(item) for item in _list(tooling.get("config_files"))) or "none")
        ),
        "",
        "## Python Constraints",
        "",
        "| Constraint | Source | Evaluation |",
        "| --- | --- | --- |",
    ]
    for row in _list(python_profile.get("constraints")):
        item = _dict(row)
        lines.append(
            "| "
            f"`{_markdown_cell(item.get('constraint') or '')}` | "
            f"`{_markdown_cell(item.get('source') or '')}` | "
            f"`{_markdown_cell(item.get('evaluation') or '')}` |"
        )
    if not _list(python_profile.get("constraints")):
        lines.append("| none | none | unknown |")
    lines.extend(
        [
            "",
            "## Dependency Access Signals",
            "",
            "| Kind | Source | Blocking |",
            "| --- | --- | --- |",
        ]
    )
    for row in _list(dependency.get("signals")):
        item = _dict(row)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('kind') or '')} | "
            f"`{_markdown_cell(item.get('source') or '')}` | "
            f"{str(bool(item.get('blocking'))).lower()} |"
        )
    if not _list(dependency.get("signals")):
        lines.append("| none | none | false |")
    lines.extend(["", "## Install Policy", ""])
    for reason in _list(install.get("reasons")):
        lines.append(f"- {_markdown_cell(reason)}")
    if not _list(install.get("reasons")):
        lines.append("- No project dependency installation was inferred.")
    lines.extend(["", "## Blockers", ""])
    for blocker in _list(payload.get("blockers")):
        row = _dict(blocker)
        lines.append(
            f"- `{_markdown_cell(row.get('category') or '')}`: "
            f"{_markdown_cell(row.get('message') or '')}"
        )
    if not _list(payload.get("blockers")):
        lines.append("- none")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    return "\n".join(lines)


def write_repository_compatibility_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_compatibility.json"
    markdown_path = root / "repository_compatibility.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_compatibility_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_compatibility_json": str(json_path),
        "repository_compatibility_markdown": str(markdown_path),
    }


def _scope_profile(profile: dict[str, Any]) -> dict[str, Any]:
    discovered = _int(profile.get("discovered_python_source_count"))
    imported = _int(profile.get("imported_source_count"))
    status = str(profile.get("scope_status") or "")
    if not status:
        status = "supported" if imported > 0 else "unsupported_scope"
    return {
        "status": status,
        "reason": str(profile.get("scope_reason") or ""),
        "blocker": str(profile.get("scope_blocker") or ""),
        "discovered_python_source_count": discovered,
        "imported_python_source_count": imported,
        "analysis_available": imported > 0,
    }


def _tooling_profile(profile: dict[str, Any]) -> dict[str, Any]:
    dependency = _dict(profile.get("dependency_manager_profile"))
    return {
        "dependency_tools": [
            str(item) for item in _list(dependency.get("tool_signals"))
        ],
        "test_runners": [
            str(item) for item in _list(profile.get("test_framework_signals"))
        ],
        "config_files": [
            str(item) for item in _list(profile.get("project_config_files"))
        ],
    }


def _python_compatibility_profile(
    root: Path | None,
    profile: dict[str, Any],
    *,
    current_python: str | tuple[int, ...] | None,
) -> dict[str, Any]:
    current = _version_tuple(current_python or sys.version_info[:3])
    constraints = _python_constraints(root, profile)
    evaluated = []
    for row in constraints:
        evaluation = _evaluate_python_constraint(
            str(row.get("constraint") or ""),
            current,
        )
        evaluated.append({**row, "evaluation": evaluation})
    if any(row["evaluation"] == "incompatible" for row in evaluated):
        status = "incompatible"
        reason = (
            f"Current Python {_format_version(current)} does not satisfy one or more "
            "repository version constraints."
        )
    elif evaluated and all(row["evaluation"] == "compatible" for row in evaluated):
        status = "compatible"
        reason = "Current Python satisfies all parsed repository constraints."
    elif evaluated:
        status = "unknown"
        reason = "At least one Python constraint could not be evaluated safely."
    else:
        status = "unknown"
        reason = "No explicit Python version constraint was materialized."
    return {
        "status": status,
        "reason": reason,
        "current_version": _format_version(current),
        "constraints": evaluated,
        "constraint_count": len(evaluated),
    }


def _python_constraints(root: Path | None, profile: dict[str, Any]) -> list[dict[str, str]]:
    if root is None:
        return []
    rows: list[dict[str, str]] = []
    config_files = [str(item) for item in _list(profile.get("project_config_files"))]
    for rel_path in config_files[:80]:
        path = _safe_repo_file(root, rel_path)
        if path is None or not path.is_file():
            continue
        name = path.name
        if name == ".python-version":
            value = _first_data_line(path)
            if value:
                rows.append({"constraint": value, "source": rel_path})
        elif name == "pyproject.toml":
            data = _read_toml(path)
            project_constraint = str(_dict(data.get("project")).get("requires-python") or "")
            if project_constraint:
                rows.append({"constraint": project_constraint, "source": f"{rel_path}:project.requires-python"})
            poetry_python = _dict(
                _dict(_dict(data.get("tool")).get("poetry")).get("dependencies")
            ).get("python")
            if isinstance(poetry_python, str) and poetry_python:
                rows.append({"constraint": poetry_python, "source": f"{rel_path}:tool.poetry.dependencies.python"})
        elif name == "setup.cfg":
            parser = configparser.ConfigParser(interpolation=None)
            try:
                parser.read(path, encoding="utf-8")
            except (OSError, configparser.Error):
                continue
            value = parser.get("options", "python_requires", fallback="").strip()
            if value:
                rows.append({"constraint": value, "source": f"{rel_path}:options.python_requires"})
    return _dedupe_dict_rows(rows, keys=("constraint", "source"))


def _dependency_access_profile(root: Path | None, profile: dict[str, Any]) -> dict[str, Any]:
    if root is None:
        return {
            "status": "unknown",
            "reason": "repository_root_not_materialized",
            "signals": [],
            "blockers": [],
            "blocker_count": 0,
            "requires_network": None,
        }
    signals: list[dict[str, Any]] = []
    config_files = [str(item) for item in _list(profile.get("project_config_files"))]
    for rel_path in config_files[:100]:
        path = _safe_repo_file(root, rel_path)
        if path is None or not path.is_file():
            continue
        lower_name = path.name.lower()
        if lower_name.startswith(("requirements", "constraints")) and path.suffix == ".txt":
            signals.extend(_requirement_access_signals(path, rel_path))
        elif lower_name == "pyproject.toml":
            signals.extend(_pyproject_access_signals(path, rel_path))
        elif lower_name == "pipfile":
            signals.extend(_pipfile_access_signals(path, rel_path))
    signals = _dedupe_dict_rows(signals, keys=("kind", "source", "blocking"))
    blockers = sorted(
        {
            str(row.get("kind") or "")
            for row in signals
            if bool(row.get("blocking")) and str(row.get("kind") or "")
        }
    )
    return {
        "status": "blocked" if blockers else "pass",
        "reason": "dependency_access_blocker_detected" if blockers else "dependency_access_profile_built",
        "signals": signals,
        "signal_count": len(signals),
        "blockers": blockers,
        "blocker_count": len(blockers),
        "requires_network": bool(
            signals
            or _list(_dict(profile.get("dependency_manager_profile")).get("dependency_files"))
        ),
    }


def _install_policy(
    root: Path | None,
    profile: dict[str, Any],
    *,
    dependency_access: dict[str, Any],
    python_profile: dict[str, Any],
) -> dict[str, Any]:
    config_files = [str(item) for item in _list(profile.get("project_config_files"))]
    root_names = {
        PurePosixPath(path).name
        for path in config_files
        if len(PurePosixPath(path).parts) == 1
    }
    reasons: list[str] = []
    risk = "low"
    backend = ""
    backend_path = False
    pyproject_parse_error = False
    if root is not None and "pyproject.toml" in root_names:
        pyproject = root / "pyproject.toml"
        data = _read_toml(pyproject)
        if not data and pyproject.is_file() and pyproject.stat().st_size > 0:
            pyproject_parse_error = True
        build_system = _dict(data.get("build-system"))
        backend = str(build_system.get("build-backend") or "")
        backend_path = bool(_list(build_system.get("backend-path")))
    if "setup.py" in root_names and "pyproject.toml" not in root_names:
        risk = "high"
        reasons.append("Legacy setup.py can execute arbitrary repository code during installation.")
    elif pyproject_parse_error:
        risk = "high"
        reasons.append("pyproject.toml could not be parsed, so build-hook behavior is unknown.")
    elif backend_path:
        risk = "high"
        reasons.append("pyproject.toml uses a repository-local backend-path build hook.")
    elif backend and backend not in _ALLOWED_BUILD_BACKENDS:
        risk = "high"
        reasons.append(f"Custom build backend `{backend}` is outside the audited allowlist.")
    elif "pyproject.toml" in root_names or "setup.cfg" in root_names:
        risk = "medium"
        reasons.append("Project installation invokes an isolated packaging backend and may access the network.")
    elif any(name.startswith("requirements") for name in root_names):
        risk = "medium"
        reasons.append("Requirements installation downloads and builds third-party packages.")
    elif _list(_dict(profile.get("dependency_manager_profile")).get("dependency_files")):
        risk = "medium"
        reasons.append("Dependency installation may download or build third-party packages.")
    else:
        reasons.append("Only the isolated test runner or no dependencies need installation.")
    access_blocked = _int(dependency_access.get("blocker_count")) > 0
    python_blocked = str(python_profile.get("status")) == "incompatible"
    return {
        "risk": risk,
        "reasons": reasons,
        "build_backend": backend,
        "build_backend_allowlisted": bool(backend and backend in _ALLOWED_BUILD_BACKENDS),
        "backend_path_detected": backend_path,
        "legacy_setup_py_only": "setup.py" in root_names and "pyproject.toml" not in root_names,
        "requires_explicit_authorization": risk == "high",
        "auto_execution_allowed": risk != "high" and not access_blocked and not python_blocked,
    }


def _requirement_access_signals(path: Path, rel_path: str) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for line_number, raw in enumerate(_read_lines(path), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        source = f"{rel_path}:{line_number}"
        lowered = line.lower()
        if line.startswith(("--index-url", "--extra-index-url")):
            blocking = _contains_private_marker(lowered) or _contains_credential_reference(line)
            signals.append({"kind": "private_index" if blocking else "external_index", "source": source, "blocking": blocking})
        elif lowered.startswith(("git+ssh://", "ssh://", "git@")):
            signals.append({"kind": "private_vcs_dependency", "source": source, "blocking": True})
        elif _contains_credential_reference(line):
            signals.append({"kind": "credential_reference", "source": source, "blocking": True})
        elif lowered.startswith(("git+", "http://", "https://")) or " @ http" in lowered:
            signals.append({"kind": "external_network_dependency", "source": source, "blocking": False})
        elif line.startswith((".", "/", "\\")) or " @ file:" in lowered:
            signals.append({"kind": "local_path_dependency", "source": source, "blocking": False})
    return signals


def _pyproject_access_signals(path: Path, rel_path: str) -> list[dict[str, Any]]:
    data = _read_toml(path)
    signals: list[dict[str, Any]] = []
    project = _dict(data.get("project"))
    dependency_groups = [
        ("project.dependencies", _list(project.get("dependencies"))),
    ]
    for group, values in _dict(project.get("optional-dependencies")).items():
        dependency_groups.append((f"project.optional-dependencies.{group}", _list(values)))
    for group, values in dependency_groups:
        for index, value in enumerate(values):
            text = str(value)
            if _contains_credential_reference(text):
                signals.append({"kind": "credential_reference", "source": f"{rel_path}:{group}[{index}]", "blocking": True})
            elif " @ " in text and any(token in text.lower() for token in ("http://", "https://", "git+")):
                signals.append({"kind": "external_network_dependency", "source": f"{rel_path}:{group}[{index}]", "blocking": False})
    poetry = _dict(_dict(data.get("tool")).get("poetry"))
    for name, value in _dict(poetry.get("dependencies")).items():
        if name == "python" or not isinstance(value, dict):
            continue
        source = f"{rel_path}:tool.poetry.dependencies.{name}"
        if value.get("path"):
            signals.append({"kind": "local_path_dependency", "source": source, "blocking": False})
        elif value.get("git"):
            git_value = str(value.get("git") or "")
            blocking = git_value.startswith(("ssh://", "git@")) or _contains_credential_reference(git_value)
            signals.append({"kind": "private_vcs_dependency" if blocking else "external_network_dependency", "source": source, "blocking": blocking})
        elif value.get("url"):
            url = str(value.get("url") or "")
            blocking = _contains_credential_reference(url) or _contains_private_marker(url)
            signals.append({"kind": "private_dependency_url" if blocking else "external_network_dependency", "source": source, "blocking": blocking})
    for index, source_config in enumerate(_list(poetry.get("source"))):
        source_row = _dict(source_config)
        name = str(source_row.get("name") or "")
        url = str(source_row.get("url") or "")
        blocking = _contains_private_marker(f"{name} {url}") or _contains_credential_reference(url)
        signals.append({"kind": "private_index" if blocking else "external_index", "source": f"{rel_path}:tool.poetry.source[{index}]", "blocking": blocking})
    return signals


def _pipfile_access_signals(path: Path, rel_path: str) -> list[dict[str, Any]]:
    data = _read_toml(path)
    signals: list[dict[str, Any]] = []
    for index, source_config in enumerate(_list(data.get("source"))):
        source_row = _dict(source_config)
        name = str(source_row.get("name") or "")
        url = str(source_row.get("url") or "")
        blocking = _contains_private_marker(f"{name} {url}") or _contains_credential_reference(url)
        signals.append({"kind": "private_index" if blocking else "external_index", "source": f"{rel_path}:source[{index}]", "blocking": blocking})
    return signals


def _evaluate_python_constraint(constraint: str, current: tuple[int, ...]) -> str:
    text = constraint.strip()
    if not text or "||" in text or ";" in text:
        return "unknown"
    if text.startswith("^"):
        lower = _version_tuple(text[1:])
        upper = (lower[0] + 1, 0, 0) if lower else ()
        return _range_result(current, lower=lower, upper=upper)
    if text.startswith("~") and not text.startswith("~="):
        lower = _version_tuple(text[1:])
        upper = (lower[0], lower[1] + 1, 0) if len(lower) >= 2 else ()
        return _range_result(current, lower=lower, upper=upper)
    if re.fullmatch(r"\d+(?:\.\d+){0,2}", text):
        expected = _version_tuple(text)
        return "compatible" if current[: len(expected)] == expected else "incompatible"
    clauses = [part.strip() for part in text.split(",") if part.strip()]
    if not clauses:
        return "unknown"
    results = [_evaluate_python_clause(clause, current) for clause in clauses]
    if "incompatible" in results:
        return "incompatible"
    if all(result == "compatible" for result in results):
        return "compatible"
    return "unknown"


def _evaluate_python_clause(clause: str, current: tuple[int, ...]) -> str:
    match = re.fullmatch(r"(===|==|!=|>=|<=|>|<|~=)\s*(\d+(?:\.\d+){0,2})(\.\*)?", clause)
    if not match:
        return "unknown"
    operator, raw_version, wildcard = match.groups()
    expected = _version_tuple(raw_version)
    padded_current = _pad_version(current)
    padded_expected = _pad_version(expected)
    if wildcard:
        equal = current[: len(expected)] == expected
    else:
        equal = padded_current == padded_expected
    if operator in {"==", "==="}:
        return "compatible" if equal else "incompatible"
    if operator == "!=":
        return "compatible" if not equal else "incompatible"
    if operator == ">=":
        return "compatible" if padded_current >= padded_expected else "incompatible"
    if operator == "<=":
        return "compatible" if padded_current <= padded_expected else "incompatible"
    if operator == ">":
        return "compatible" if padded_current > padded_expected else "incompatible"
    if operator == "<":
        return "compatible" if padded_current < padded_expected else "incompatible"
    if operator == "~=":
        if len(expected) >= 3:
            upper = (expected[0], expected[1] + 1, 0)
        else:
            upper = (expected[0] + 1, 0, 0)
        return _range_result(current, lower=expected, upper=upper)
    return "unknown"


def _range_result(
    current: tuple[int, ...],
    *,
    lower: tuple[int, ...],
    upper: tuple[int, ...],
) -> str:
    if not lower or not upper:
        return "unknown"
    padded = _pad_version(current)
    return "compatible" if _pad_version(lower) <= padded < _pad_version(upper) else "incompatible"


def _version_tuple(value: Any) -> tuple[int, ...]:
    if isinstance(value, tuple):
        return tuple(int(item) for item in value[:3])
    if isinstance(value, list):
        return tuple(int(item) for item in value[:3])
    match = re.match(r"\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(value or ""))
    if not match:
        return ()
    return tuple(int(item) for item in match.groups() if item is not None)


def _pad_version(value: tuple[int, ...]) -> tuple[int, int, int]:
    return tuple((*value, 0, 0, 0)[:3])  # type: ignore[return-value]


def _format_version(value: tuple[int, ...]) -> str:
    return ".".join(str(item) for item in value) if value else "unknown"


def _safe_repo_file(root: Path, rel_path: str) -> Path | None:
    normalized = str(rel_path or "").replace("\\", "/").strip("/")
    if not normalized:
        return None
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or ".." in pure.parts:
        return None
    candidate = (root / Path(*pure.parts)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


def _read_lines(path: Path, *, limit: int = 500) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()[:limit]
    except OSError:
        return []


def _first_data_line(path: Path) -> str:
    for raw in _read_lines(path, limit=20):
        line = raw.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def _contains_private_marker(value: str) -> bool:
    lowered = str(value or "").lower()
    return any(marker in lowered for marker in _PRIVATE_MARKERS)


def _contains_credential_reference(value: str) -> bool:
    text = str(value or "")
    lowered = text.lower()
    if re.search(r"https?://[^/@\s]+:[^/@\s]+@", text):
        return True
    return bool(
        re.search(r"\$\{[^}]+\}|\$\([^)]+\)|\{\{[^}]+\}\}", text)
        or any(token in lowered for token in ("${token", "${password", "${username", "secrets."))
    )


def _dedupe_dict_rows(
    rows: list[dict[str, Any]],
    *,
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    seen: set[tuple[str, ...]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        fingerprint = tuple(str(row.get(key) or "") for key in keys)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        result.append(row)
    return result


def _blocker(category: str, layer: str, message: str) -> dict[str, str]:
    return {"category": category, "layer": layer, "message": message}


def _dependency_blocker_message(category: str) -> str:
    return {
        "credential_reference": "A dependency source requires credentials that are not available to the Agent.",
        "private_dependency_url": "A dependency URL appears private or credential protected.",
        "private_index": "A private package index requires explicit network and credential configuration.",
        "private_vcs_dependency": "A VCS dependency requires SSH or private repository access.",
    }.get(category, "Dependency access cannot be completed safely without user input.")


def _next_actions(
    *,
    status: str,
    termination_reason: str,
    install_policy: dict[str, Any],
    source_roots: list[str],
) -> list[str]:
    if termination_reason == "unsupported_scope":
        return ["Select a Python repository or provide a Python subproject path."]
    if termination_reason == "source_selection:no_python_sources_imported":
        return ["Broaden include filters to one of the discovered Python source roots."]
    if termination_reason == "environment:python_version_incompatible":
        return ["Create an isolated environment with a Python version allowed by repository metadata."]
    if termination_reason.startswith("dependency:"):
        return ["Provide approved dependency credentials or replace the private source before test execution."]
    actions: list[str] = []
    if status == "partial" and source_roots:
        actions.append("Continue static analysis on: " + ", ".join(source_roots[:5]))
    if bool(install_policy.get("requires_explicit_authorization")):
        actions.append("Request explicit authorization before running the high-risk repository install hook.")
    if termination_reason == "test_command:no_recommended_test_command":
        actions.append("Complete static analysis and report a no-test-oracle blocker.")
    if termination_reason == "checkout:repository_root_not_materialized":
        actions.append("Materialize the pinned repository checkout before dependency or test execution.")
    if not actions:
        actions.append("Proceed with isolated dependency setup and the recommended bounded test command.")
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
