from __future__ import annotations

import json
import re
import shlex
from pathlib import Path, PurePosixPath
from typing import Any


def plan_repository_test_execution(
    repository_profile: dict[str, Any],
    *,
    repository_test_environment: dict[str, Any] | None = None,
    repository_test_environment_setup: dict[str, Any] | None = None,
    repository_test_environment_setup_result: dict[str, Any] | None = None,
    repository_root: str | Path | None = None,
    max_narrow_tests: int = 5,
) -> dict[str, Any]:
    command = str(repository_profile.get("recommended_test_command") or "").strip()
    environment = _dict(repository_test_environment)
    setup = _dict(repository_test_environment_setup)
    setup_result = _dict(repository_test_environment_setup_result)
    prepared_test_runner = _prepared_test_runner(setup, setup_result)
    test_module = _test_module(command) or str(environment.get("test_module") or "")
    pytest_config = _dict(environment.get("pytest_configuration"))
    pytest_addopts = _safe_pytest_addopts(_list(pytest_config.get("addopts")))
    configured_test_paths = _safe_configured_test_paths(
        _list(pytest_config.get("testpaths"))
    )
    test_paths = _test_paths(repository_profile)
    selected_paths = (
        configured_test_paths[: max(0, max_narrow_tests)]
        if configured_test_paths
        else test_paths[: max(0, max_narrow_tests)]
    )
    root_path = Path(repository_root) if repository_root is not None else None
    root_present = bool(root_path and root_path.exists() and root_path.is_dir())
    planned_environment_variables = _safe_environment_variables(
        _dict(environment.get("planned_environment_variables"))
    )
    profile_command_candidates = _profile_command_candidates(
        repository_profile,
        fallback_command=command,
    )
    ci_command_candidates = _ci_command_candidates(environment)
    has_execution_seed = bool(command) or bool(ci_command_candidates)
    candidates = _candidate_commands(
        command=command,
        test_module=test_module,
        selected_paths=selected_paths,
        test_path_count=len(test_paths),
        profile_command_candidates=profile_command_candidates,
        ci_command_candidates=ci_command_candidates,
        environment=environment,
        pytest_addopts=pytest_addopts,
        root_path=root_path if root_present else None,
        prepared_test_runner=prepared_test_runner,
        fallback_working_dir=str(
            repository_profile.get("recommended_test_working_dir") or ""
        ),
    )
    recommended = candidates[0] if candidates else {}
    recommended_working_dir = str(recommended.get("working_dir") or "")
    recommended_selected_paths = [
        str(item) for item in _list(recommended.get("selected_test_paths"))
    ]
    recommended_cwd = (
        _candidate_cwd(root_path, recommended_working_dir)
        if root_present and root_path is not None
        else None
    )
    working_dir_missing = bool(
        root_present
        and root_path is not None
        and recommended_working_dir
        and (recommended_cwd is None or not recommended_cwd.is_dir())
    )
    input_missing_selected_paths = (
        _missing_paths(selected_paths, root_path)
        if root_present and root_path is not None and selected_paths
        else []
    )
    missing_selected_paths = (
        _missing_paths(recommended_selected_paths, recommended_cwd)
        if root_present and recommended_cwd is not None and recommended_selected_paths
        else []
    )
    if not recommended_selected_paths and input_missing_selected_paths:
        missing_selected_paths = input_missing_selected_paths
    recommended_runner = str(recommended.get("runner") or _test_module(str(recommended.get("command") or "")))
    recommended_source = str(recommended.get("source") or "")
    recommended_reason = str(recommended.get("reason") or "")
    runner_fallback = _runner_fallback_summary(
        preferred_runner=test_module,
        selected_runner=recommended_runner,
        selected_source=recommended_source,
        selected_reason=recommended_reason,
        environment=environment,
        prepared_test_runner=prepared_test_runner,
    )
    planned_runner_unprepared = (
        bool(prepared_test_runner)
        and bool(recommended_runner)
        and recommended_runner != prepared_test_runner
    )
    selected_tool_missing = (
        planned_runner_unprepared
        or (
            environment.get("test_tool_available") is False
            and recommended_runner
            and recommended_runner != prepared_test_runner
            and (
                not str(environment.get("test_module") or "")
                or recommended_runner == str(environment.get("test_module") or "")
            )
        )
    )
    executable_now = (
        has_execution_seed
        and root_present
        and bool(candidates)
        and not selected_tool_missing
        and not working_dir_missing
        and not missing_selected_paths
    )
    status = "pass"
    reason = "execution_plan_built"
    if not has_execution_seed:
        status = "skipped"
        reason = "no_recommended_test_command"
    elif not root_present:
        status = "warning"
        reason = "full_repo_not_materialized"
    elif planned_runner_unprepared:
        status = "warning"
        reason = "planned_runner_not_prepared"
    elif selected_tool_missing:
        status = "warning"
        reason = "test_environment_warning"
    elif working_dir_missing:
        status = "warning"
        reason = "selected_working_dir_missing"
    elif environment.get("status") == "warning" and str(
        environment.get("reason") or ""
    ) != "test_tool_missing":
        status = "warning"
        reason = "test_environment_warning"
    elif missing_selected_paths:
        status = "warning"
        reason = "selected_tests_missing_in_checkout"
    return {
        "status": status,
        "reason": reason,
        "recommended_test_command": command,
        "recommended_execution_command": str(recommended.get("command") or ""),
        "recommended_execution_level": str(recommended.get("level") or ""),
        "recommended_execution_scope": str(recommended.get("scope") or ""),
        "recommended_execution_risk": str(recommended.get("risk") or ""),
        "recommended_execution_runner": recommended_runner,
        "recommended_execution_source": recommended_source,
        "recommended_execution_reason": recommended_reason,
        "recommended_working_dir": recommended_working_dir,
        "recommended_execution_cwd": str(recommended_cwd or ""),
        "recommended_working_dir_present": bool(
            recommended_cwd is not None and recommended_cwd.is_dir()
        )
        if root_present
        else False,
        "recommended_execution_profile_reason": str(
            recommended.get("profile_reason") or ""
        ),
        "preferred_test_runner": test_module,
        "runner_fallback_used": bool(runner_fallback.get("used", False)),
        "runner_fallback_reason": str(runner_fallback.get("reason") or ""),
        "runner_fallback_from": str(runner_fallback.get("from") or ""),
        "runner_fallback_to": str(runner_fallback.get("to") or ""),
        "prepared_test_runner": prepared_test_runner,
        "planned_runner_prepared": not planned_runner_unprepared,
        "repository_root": str(root_path) if root_path is not None else "",
        "repository_root_present": root_present,
        "executable_now": executable_now,
        "test_module": test_module,
        "profile_test_command_candidate_count": len(profile_command_candidates),
        "ci_test_command_candidate_count": len(ci_command_candidates),
        "ci_test_command_candidates": [
            str(candidate.get("command") or "")
            for candidate in ci_command_candidates
            if str(candidate.get("command") or "")
        ],
        "test_source_count": len(test_paths),
        "configured_test_paths": configured_test_paths,
        "pytest_addopts": pytest_addopts,
        "pytest_config_source_count": _int(pytest_config.get("source_count", 0)),
        "selected_test_paths": recommended_selected_paths or selected_paths,
        "missing_selected_test_paths": missing_selected_paths,
        "max_narrow_tests": max_narrow_tests,
        "framework_signals": [
            str(item) for item in _list(environment.get("framework_signals"))
        ],
        "planned_environment_variables": planned_environment_variables,
        "planned_environment_variable_names": sorted(planned_environment_variables),
        "candidate_commands": candidates,
        "next_actions": _next_actions(
            command=command,
            recommended_command=str(recommended.get("command") or ""),
            recommended_level=str(recommended.get("level") or ""),
            executable_now=executable_now,
            root_present=root_present,
            environment=environment,
            reason=reason,
            selected_tool_missing=selected_tool_missing,
            planned_runner_unprepared=planned_runner_unprepared,
            recommended_runner=recommended_runner,
            runner_fallback_reason=str(runner_fallback.get("reason") or ""),
            prepared_test_runner=prepared_test_runner,
            planned_environment_variables=planned_environment_variables,
            pytest_addopts=pytest_addopts,
            configured_test_paths=configured_test_paths,
            ci_command_candidates=ci_command_candidates,
        ),
    }


def render_repository_test_execution_plan_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Repository Test Execution Plan",
        "",
        f"- Status: `{_markdown_cell(payload.get('status', ''))}`",
        f"- Reason: `{_markdown_cell(payload.get('reason', ''))}`",
        (
            "- Recommended Execution Command: "
            f"`{_markdown_cell(payload.get('recommended_execution_command') or 'none')}`"
        ),
        (
            "- Recommended Execution Level: "
            f"`{_markdown_cell(payload.get('recommended_execution_level') or 'none')}`"
        ),
        (
            "- Recommended Execution Risk: "
            f"`{_markdown_cell(payload.get('recommended_execution_risk') or 'none')}`"
        ),
        (
            "- Recommended Execution Runner: "
            f"`{_markdown_cell(payload.get('recommended_execution_runner') or 'none')}`"
        ),
        (
            "- Recommended Execution Source: "
            f"`{_markdown_cell(payload.get('recommended_execution_source') or 'none')}`"
        ),
        (
            "- Preferred Test Runner: "
            f"`{_markdown_cell(payload.get('preferred_test_runner') or 'none')}`"
        ),
        (
            "- Runner Fallback Used: "
            f"{str(bool(payload.get('runner_fallback_used', False))).lower()}"
        ),
        (
            "- Runner Fallback Reason: "
            f"`{_markdown_cell(payload.get('runner_fallback_reason') or 'none')}`"
        ),
        (
            "- Prepared Test Runner: "
            f"`{_markdown_cell(payload.get('prepared_test_runner') or 'none')}`"
        ),
        (
            "- Planned Runner Prepared: "
            f"{str(bool(payload.get('planned_runner_prepared', True))).lower()}"
        ),
        f"- Executable Now: {str(bool(payload.get('executable_now'))).lower()}",
        f"- Repository Root: `{_markdown_cell(payload.get('repository_root') or 'none')}`",
        f"- Repository Root Present: {str(bool(payload.get('repository_root_present'))).lower()}",
        f"- Recommended Working Dir: `{_markdown_cell(payload.get('recommended_working_dir') or '.')}`",
        f"- Recommended Execution CWD: `{_markdown_cell(payload.get('recommended_execution_cwd') or 'none')}`",
        f"- Test Module: `{_markdown_cell(payload.get('test_module') or 'none')}`",
        f"- Test Sources: {_int(payload.get('test_source_count', 0))}",
        (
            "- Pytest Config Sources: "
            f"{_int(payload.get('pytest_config_source_count', 0))}"
        ),
        (
            "- Pytest Addopts: "
            f"{' '.join(str(item) for item in _list(payload.get('pytest_addopts'))) or 'none'}"
        ),
        (
            "- CI Test Command Candidates: "
            f"{_int(payload.get('ci_test_command_candidate_count', 0))}"
        ),
        (
            "- Framework Signals: "
            f"{', '.join(str(item) for item in _list(payload.get('framework_signals'))) or 'none'}"
        ),
        (
            "- Planned Environment Variables: "
            f"{', '.join(str(item) for item in _list(payload.get('planned_environment_variable_names'))) or 'none'}"
        ),
        "",
        "## Candidate Commands",
        "",
        "| Recommended | Level | Risk | Runner | Working Dir | Source | Scope | Command | Reason |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for candidate in _list(payload.get("candidate_commands")):
        row = _dict(candidate)
        lines.append(
            "| "
            f"{str(bool(row.get('recommended', False))).lower()} | "
            f"{_markdown_cell(row.get('level', ''))} | "
            f"{_markdown_cell(row.get('risk', ''))} | "
            f"{_markdown_cell(row.get('runner', ''))} | "
            f"`{_markdown_cell(row.get('working_dir') or '.')}` | "
            f"{_markdown_cell(row.get('source', ''))} | "
            f"{_markdown_cell(row.get('scope', ''))} | "
            f"`{_markdown_cell(row.get('command', ''))}` | "
            f"{_markdown_cell(row.get('reason', ''))} |"
        )
    if not _list(payload.get("candidate_commands")):
        lines.append("| false | none | none | none | none | none | none | none | none |")
    lines.extend(["", "## Selected Test Paths", ""])
    for path in _list(payload.get("selected_test_paths")):
        lines.append(f"- `{_markdown_cell(path)}`")
    if not _list(payload.get("selected_test_paths")):
        lines.append("- none")
    configured_paths = _list(payload.get("configured_test_paths"))
    if configured_paths:
        lines.extend(["", "## Configured Test Paths", ""])
        for path in configured_paths:
            lines.append(f"- `{_markdown_cell(path)}`")
    missing = _list(payload.get("missing_selected_test_paths"))
    if missing:
        lines.extend(["", "## Missing Selected Test Paths", ""])
        for path in missing:
            lines.append(f"- `{_markdown_cell(path)}`")
    environment_variables = _dict(payload.get("planned_environment_variables"))
    lines.extend(["", "## Planned Environment Variables", ""])
    for key, value in environment_variables.items():
        lines.append(f"- `{_markdown_cell(key)}={_markdown_cell(value)}`")
    if not environment_variables:
        lines.append("- none")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    return "\n".join(lines)


def write_repository_test_execution_plan_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_execution_plan.json"
    markdown_path = root / "repository_test_execution_plan.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_execution_plan_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_execution_plan_json": str(json_path),
        "repository_test_execution_plan_markdown": str(markdown_path),
    }


def _candidate_commands(
    *,
    command: str,
    test_module: str,
    selected_paths: list[str],
    test_path_count: int,
    profile_command_candidates: list[dict[str, Any]],
    ci_command_candidates: list[dict[str, Any]],
    environment: dict[str, Any],
    pytest_addopts: list[str],
    root_path: Path | None,
    prepared_test_runner: str = "",
    fallback_working_dir: str = "",
) -> list[dict[str, Any]]:
    if not command and not profile_command_candidates and not ci_command_candidates:
        return []
    candidates: list[dict[str, Any]] = []
    command_specs = _command_specs(
        command=command,
        test_module=test_module,
        profile_command_candidates=profile_command_candidates,
        ci_command_candidates=ci_command_candidates,
        fallback_working_dir=fallback_working_dir,
    )
    for spec in command_specs:
        working_dir = str(spec.get("working_dir") or "")
        spec_selected_paths = _paths_for_working_dir(selected_paths, working_dir)
        candidates.extend(
            _candidate_commands_for_command(
                command=str(spec.get("command") or ""),
                test_module=str(spec.get("runner") or _test_module(str(spec.get("command") or ""))),
                selected_paths=spec_selected_paths,
                test_path_count=(
                    len(spec_selected_paths)
                    if working_dir and spec_selected_paths
                    else test_path_count
                ),
                source=str(spec.get("source") or ""),
                profile_rank=_int(spec.get("profile_rank", 0)),
                profile_reason=str(spec.get("profile_reason") or ""),
                profile_confidence=_float(spec.get("profile_confidence", 0.0)),
                pytest_addopts=pytest_addopts,
                working_dir=working_dir,
            )
        )
    return _mark_recommended(
        _dedupe_candidate_commands(candidates),
        environment=environment,
        root_path=root_path,
        prepared_test_runner=prepared_test_runner,
    )


def _candidate_commands_for_command(
    *,
    command: str,
    test_module: str,
    selected_paths: list[str],
    test_path_count: int,
    source: str,
    profile_rank: int,
    profile_reason: str,
    profile_confidence: float,
    pytest_addopts: list[str],
    working_dir: str = "",
) -> list[dict[str, Any]]:
    if not command:
        return []
    candidates: list[dict[str, Any]] = []
    if test_module == "pytest":
        if source == "ci_config":
            ci_selected_paths = _pytest_test_paths_from_command(command)
            candidates.append(
                {
                    "level": "ci",
                    "command": command,
                    "scope": (
                        f"{len(ci_selected_paths)} CI-selected test targets"
                        if ci_selected_paths
                        else "CI pytest discovery"
                    ),
                    "risk": "low" if ci_selected_paths else "medium",
                    "reason": "ci_test_command_candidate",
                    "runner": "pytest",
                    "source": source,
                    "profile_rank": profile_rank,
                    "profile_reason": profile_reason,
                    "profile_confidence": profile_confidence,
                    "selected_test_paths": ci_selected_paths,
                    "total_test_path_count": test_path_count,
                }
            )
            if ci_selected_paths:
                candidates.append(
                    {
                        "level": "focused",
                        "command": _join_command(
                            _pytest_focused_command_args(
                                pytest_addopts,
                                ci_selected_paths,
                            )
                        ),
                        "scope": (
                            f"first failure from {len(ci_selected_paths)} "
                            "CI-selected test targets"
                        ),
                        "risk": "low",
                        "reason": "ci_pytest_focused_first_failure",
                        "runner": "pytest",
                        "source": source,
                        "profile_rank": profile_rank,
                        "profile_reason": profile_reason,
                        "profile_confidence": profile_confidence,
                        "selected_test_paths": ci_selected_paths,
                        "total_test_path_count": test_path_count,
                    }
                )
        if selected_paths:
            candidates.append(
                {
                    "level": "narrow",
                    "command": _join_command(
                        [*_pytest_command_base_args(pytest_addopts), *selected_paths]
                    ),
                    "scope": f"{len(selected_paths)} selected test targets",
                    "risk": "low",
                    "reason": (
                        "ci_pytest_configured_testpath_selection"
                        if source == "ci_config"
                        else (
                            "pytest_configured_testpath_selection"
                            if pytest_addopts
                            else "pytest_test_file_selection"
                        )
                    ),
                    "runner": "pytest",
                    "source": source,
                    "profile_rank": profile_rank,
                    "profile_reason": profile_reason,
                    "profile_confidence": profile_confidence,
                    "selected_test_paths": selected_paths,
                    "total_test_path_count": test_path_count,
                }
            )
            candidates.append(
                {
                    "level": "focused",
                    "command": _join_command(
                        _pytest_focused_command_args(pytest_addopts, selected_paths)
                    ),
                    "scope": f"first failure from {len(selected_paths)} selected test targets",
                    "risk": "low",
                    "reason": (
                        "ci_pytest_configured_first_failure"
                        if source == "ci_config"
                        else "pytest_focused_first_failure"
                    ),
                    "runner": "pytest",
                    "source": source,
                    "profile_rank": profile_rank,
                    "profile_reason": profile_reason,
                    "profile_confidence": profile_confidence,
                    "selected_test_paths": selected_paths,
                    "total_test_path_count": test_path_count,
                }
            )
        candidates.append(
            {
                "level": "smoke",
                "command": _join_command(_pytest_command_base_args(pytest_addopts)),
                "scope": "pytest discovery with quiet output",
                "risk": "medium" if selected_paths else "medium_high",
                "reason": (
                    "ci_pytest_smoke_discovery"
                    if source == "ci_config"
                    else (
                        "pytest_configured_smoke_discovery"
                        if pytest_addopts
                        else "pytest_smoke_discovery"
                    )
                ),
                "runner": "pytest",
                "source": source,
                "profile_rank": profile_rank,
                "profile_reason": profile_reason,
                "profile_confidence": profile_confidence,
                "selected_test_paths": [],
                "total_test_path_count": test_path_count,
            }
        )
        if source != "ci_config" and command != "python -m pytest -q":
            candidates.append(
                {
                    "level": "full",
                    "command": command,
                    "scope": "profile recommended command",
                    "risk": "medium_high",
                    "reason": (
                        "ci_test_command_candidate"
                        if source == "ci_config"
                        else "profile_recommended_full_command"
                    ),
                    "runner": "pytest",
                    "source": source,
                    "profile_rank": profile_rank,
                    "profile_reason": profile_reason,
                    "profile_confidence": profile_confidence,
                    "selected_test_paths": [],
                    "total_test_path_count": test_path_count,
                }
            )
    elif test_module == "unittest":
        unittest_targets = _unittest_discover_targets(selected_paths)
        for target in unittest_targets:
            candidates.append(
                {
                    "level": "narrow",
                    "command": _join_command(
                        _unittest_discover_command_args(
                            str(target.get("start_dir") or "."),
                            str(target.get("pattern") or "test*.py"),
                        )
                    ),
                    "scope": str(target.get("scope") or "selected unittest tests"),
                    "risk": "low",
                    "reason": "unittest_test_file_selection",
                    "runner": "unittest",
                    "source": source,
                    "profile_rank": profile_rank,
                    "profile_reason": profile_reason,
                    "profile_confidence": profile_confidence,
                    "selected_test_paths": [
                        str(item) for item in _list(target.get("selected_test_paths"))
                    ],
                    "total_test_path_count": test_path_count,
                }
            )
        candidates.append(
            {
                "level": "full",
                "command": command,
                "scope": "profile recommended command",
                "risk": "medium_high" if selected_paths else "high",
                "reason": (
                    "unittest_full_discovery_fallback"
                    if selected_paths
                    else "profile_recommended_full_command"
                ),
                "runner": "unittest",
                "source": source,
                "profile_rank": profile_rank,
                "profile_reason": profile_reason,
                "profile_confidence": profile_confidence,
                "selected_test_paths": [],
                "total_test_path_count": test_path_count,
            }
        )
    elif test_module in {"tox", "nox"}:
        candidates.append(
            {
                "level": "full",
                "command": command,
                "scope": f"{test_module} managed test environment",
                "risk": "high",
                "reason": f"{test_module}_runner_requires_project_environment",
                "runner": test_module,
                "source": source,
                "profile_rank": profile_rank,
                "profile_reason": profile_reason,
                "profile_confidence": profile_confidence,
                "selected_test_paths": [],
                "total_test_path_count": test_path_count,
            }
        )
    else:
        candidates.append(
            {
                "level": "full",
                "command": command,
                "scope": "profile recommended command",
                "risk": "medium_high",
                "reason": "no_safe_narrowing_strategy",
                "runner": test_module,
                "source": source,
                "profile_rank": profile_rank,
                "profile_reason": profile_reason,
                "profile_confidence": profile_confidence,
                "selected_test_paths": [],
                "total_test_path_count": test_path_count,
            }
        )
    for candidate in candidates:
        candidate["working_dir"] = working_dir
    return candidates


def _mark_recommended(
    candidates: list[dict[str, Any]],
    *,
    environment: dict[str, Any],
    root_path: Path | None,
    prepared_test_runner: str = "",
) -> list[dict[str, Any]]:
    selected_index = _recommended_candidate_index(
        candidates,
        environment=environment,
        root_path=root_path,
        prepared_test_runner=prepared_test_runner,
    )
    ordered = []
    if 0 <= selected_index < len(candidates):
        ordered.append(candidates[selected_index])
        ordered.extend(
            candidate
            for index, candidate in enumerate(candidates)
            if index != selected_index
        )
    else:
        ordered = list(candidates)
    marked = []
    for index, candidate in enumerate(ordered):
        row = dict(candidate)
        row["recommended"] = index == 0
        marked.append(row)
    return marked


def _recommended_candidate_index(
    candidates: list[dict[str, Any]],
    *,
    environment: dict[str, Any],
    root_path: Path | None,
    prepared_test_runner: str = "",
) -> int:
    if not candidates:
        return -1
    missing_module = (
        str(environment.get("test_module") or "")
        if environment.get("test_tool_available") is False
        else ""
    )
    eligible_indices: list[int] = []
    for index, candidate in enumerate(candidates):
        runner = str(candidate.get("runner") or _test_module(str(candidate.get("command") or "")))
        if missing_module and runner == missing_module and runner != prepared_test_runner:
            continue
        if prepared_test_runner and runner and runner != prepared_test_runner:
            continue
        selected_paths = [str(item) for item in _list(candidate.get("selected_test_paths"))]
        candidate_cwd = _candidate_cwd(root_path, str(candidate.get("working_dir") or ""))
        if root_path is not None and candidate_cwd is None:
            continue
        if root_path is not None and candidate_cwd is not None and not candidate_cwd.is_dir():
            continue
        if candidate_cwd is not None and _missing_paths(selected_paths, candidate_cwd):
            continue
        eligible_indices.append(index)
    for index in eligible_indices:
        candidate = candidates[index]
        if (
            str(candidate.get("source") or "") == "ci_config"
            and _list(candidate.get("selected_test_paths"))
            and str(candidate.get("risk") or "") == "low"
        ):
            return index
    if eligible_indices:
        return eligible_indices[0]
    return 0


def _runner_fallback_summary(
    *,
    preferred_runner: str,
    selected_runner: str,
    selected_source: str,
    selected_reason: str,
    environment: dict[str, Any],
    prepared_test_runner: str,
) -> dict[str, Any]:
    preferred = str(preferred_runner or "")
    selected = str(selected_runner or "")
    if not preferred or not selected or preferred == selected:
        return {"used": False, "reason": "", "from": preferred, "to": selected}
    reason = _runner_fallback_reason(
        preferred_runner=preferred,
        selected_runner=selected,
        selected_source=selected_source,
        selected_reason=selected_reason,
        environment=environment,
        prepared_test_runner=prepared_test_runner,
    )
    return {"used": True, "reason": reason, "from": preferred, "to": selected}


def _runner_fallback_reason(
    *,
    preferred_runner: str,
    selected_runner: str,
    selected_source: str,
    selected_reason: str,
    environment: dict[str, Any],
    prepared_test_runner: str,
) -> str:
    missing_runner = str(environment.get("test_module") or "")
    if (
        environment.get("test_tool_available") is False
        and missing_runner == preferred_runner
    ):
        return f"missing_runner:{preferred_runner}"
    if prepared_test_runner and selected_runner == prepared_test_runner:
        return f"prepared_runner_available:{prepared_test_runner}"
    if selected_source == "ci_config":
        return f"ci_safe_candidate:{selected_runner}"
    if selected_source == "profile_fallback":
        return f"profile_fallback_candidate:{selected_runner}"
    if selected_reason:
        return f"safe_candidate:{selected_reason}"
    return f"runner_switch:{preferred_runner}->{selected_runner}"


def _command_specs(
    *,
    command: str,
    test_module: str,
    profile_command_candidates: list[dict[str, Any]],
    ci_command_candidates: list[dict[str, Any]],
    fallback_working_dir: str = "",
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    fallback_specs: list[dict[str, Any]] = []
    if profile_command_candidates:
        for candidate in profile_command_candidates:
            candidate_command = str(candidate.get("command") or "").strip()
            if not candidate_command:
                continue
            source = (
                "profile_recommended"
                if candidate_command == command
                else "profile_fallback"
            )
            spec = {
                "command": candidate_command,
                "runner": str(candidate.get("runner") or _test_module(candidate_command)),
                "source": source,
                "profile_rank": _int(candidate.get("rank", 0)),
                "profile_reason": str(candidate.get("reason") or ""),
                "profile_confidence": _float(candidate.get("confidence", 0.0)),
                "working_dir": str(candidate.get("working_dir") or ""),
            }
            if source == "profile_recommended":
                specs.append(spec)
            else:
                fallback_specs.append(spec)
    if command and all(str(spec.get("command") or "") != command for spec in specs):
        specs.insert(
            0,
            {
                "command": command,
                "runner": test_module or _test_module(command),
                "source": "profile_recommended",
                "profile_rank": 1,
                "profile_reason": "recommended_test_command",
                "profile_confidence": 1.0,
                "working_dir": fallback_working_dir,
            },
        )
    next_rank = len(specs) + 1
    for candidate in ci_command_candidates:
        candidate_command = str(candidate.get("command") or "").strip()
        if not candidate_command:
            continue
        specs.append(
            {
                "command": candidate_command,
                "runner": str(candidate.get("runner") or _test_module(candidate_command)),
                "source": "ci_config",
                "profile_rank": _int(candidate.get("rank", next_rank)),
                "profile_reason": str(candidate.get("reason") or "ci_test_command_candidate"),
                "profile_confidence": _float(candidate.get("confidence", 0.74)),
                "working_dir": str(candidate.get("working_dir") or ""),
            }
        )
        next_rank += 1
    specs.extend(fallback_specs)
    return _dedupe_command_specs(specs)


def _ci_command_candidates(environment: dict[str, Any]) -> list[dict[str, Any]]:
    ci_config = _dict(environment.get("ci_configuration"))
    raw_candidates = _list(ci_config.get("test_commands"))
    if not raw_candidates:
        raw_candidates = [
            {"command": command, "reason": "ci_test_command_candidate"}
            for command in _list(environment.get("ci_test_command_candidates"))
        ]
    candidates: list[dict[str, Any]] = []
    for index, value in enumerate(raw_candidates, start=1):
        raw = _dict(value)
        parsed = _safe_ci_test_command(str(raw.get("command") or ""))
        if not parsed:
            continue
        candidates.append(
            {
                "rank": index,
                "command": parsed["command"],
                "runner": parsed["runner"],
                "reason": str(raw.get("reason") or "ci_test_command_candidate"),
                "confidence": 0.74,
                "source": str(raw.get("source") or ""),
            }
        )
    return _dedupe_command_specs(candidates)[:12]


def _profile_command_candidates(
    repository_profile: dict[str, Any],
    *,
    fallback_command: str,
) -> list[dict[str, Any]]:
    candidates = []
    for value in _list(repository_profile.get("test_command_candidates")):
        candidate = _dict(value)
        command = str(candidate.get("command") or "").strip()
        if command:
            candidates.append(candidate)
    if not candidates and fallback_command:
        candidates.append(
            {
                "command": fallback_command,
                "runner": _test_module(fallback_command),
                "rank": 1,
                "reason": "recommended_test_command",
                "confidence": 1.0,
                "working_dir": str(
                    repository_profile.get("recommended_test_working_dir") or ""
                ),
            }
        )
    return candidates


def _paths_for_working_dir(paths: list[str], working_dir: str) -> list[str]:
    normalized = _clean_path(working_dir)
    if not normalized:
        return list(paths)
    prefix = f"{normalized}/"
    scoped: list[str] = []
    for path in paths:
        clean = _clean_path(path)
        if clean == normalized:
            scoped.append(".")
        elif clean.startswith(prefix):
            scoped.append(clean[len(prefix) :])
    return scoped


def _candidate_cwd(root_path: Path | None, working_dir: str) -> Path | None:
    if root_path is None:
        return None
    normalized = _clean_path(working_dir)
    if not normalized:
        return root_path
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        return None
    return root_path.joinpath(*pure.parts)


def _dedupe_command_specs(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for spec in specs:
        command = str(spec.get("command") or "")
        key = (str(spec.get("working_dir") or ""), command)
        if not command or key in seen:
            continue
        seen.add(key)
        deduped.append(spec)
    return deduped


def _dedupe_candidate_commands(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for candidate in candidates:
        key = (
            str(candidate.get("level") or ""),
            str(candidate.get("working_dir") or ""),
            str(candidate.get("command") or ""),
        )
        if not key[2] or key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _test_paths(repository_profile: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    paths: list[tuple[int, int, str]] = []
    for index, item in enumerate(_list(repository_profile.get("test_source_paths"))):
        path = _clean_path(str(item))
        if path and path not in seen and _is_python_test_path(path):
            seen.add(path)
            paths.append((_test_path_priority(path), index, path))
    return [path for _, _, path in sorted(paths)]


def _test_path_priority(path: str) -> int:
    normalized = path.lower().replace("\\", "/")
    name = PurePosixPath(normalized).name
    if name == "__init__.py":
        return 30
    if name.startswith("test_") or name.endswith("_test.py"):
        return 0
    if "/tests/" in f"/{normalized}":
        return 10
    return 20


def _test_module(command: str) -> str:
    try:
        args = shlex.split(command)
    except ValueError:
        return ""
    if len(args) >= 3 and args[1] == "-m":
        return args[2]
    return ""


def _prepared_test_runner(
    setup: dict[str, Any],
    setup_result: dict[str, Any],
) -> str:
    if str(setup_result.get("status") or "") != "pass":
        return ""
    if not bool(setup_result.get("executed", False)):
        return ""
    return str(setup.get("test_module") or setup_result.get("test_module") or "")


def _safe_ci_test_command(command: str) -> dict[str, str]:
    if not _safe_ci_line(command):
        return {}
    try:
        parts = shlex.split(command)
    except ValueError:
        return {}
    if not parts:
        return {}
    runner, args = _ci_test_runner_parts(parts)
    if runner == "pytest":
        addopts = _safe_pytest_addopts(args)
        paths = _safe_configured_test_paths(
            [part for part in args if not str(part).startswith("-") and "=" not in str(part)]
        )
        return {
            "runner": "pytest",
            "command": _join_command(["python", "-m", "pytest", *addopts, *paths]),
        }
    if runner in {"tox", "nox"}:
        return {"runner": runner, "command": _join_command(["python", "-m", runner])}
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


def _pytest_test_paths_from_command(command: str) -> list[str]:
    try:
        parts = shlex.split(command)
    except ValueError:
        return []
    args: list[str] = []
    if len(parts) >= 3 and _is_python_command(parts[0]) and parts[1:3] == [
        "-m",
        "pytest",
    ]:
        args = parts[3:]
    elif parts and parts[0] in {"pytest", "py.test"}:
        args = parts[1:]
    paths = [
        part
        for part in args
        if not str(part).startswith("-") and "=" not in str(part)
    ]
    return _safe_configured_test_paths(paths)


def _safe_ci_line(command: str) -> bool:
    text = str(command or "").strip()
    if not text or len(text) > 400:
        return False
    if any(token in text for token in ("&&", "||", ";", "`", "$", "{", "}", ">", "<")):
        return False
    return True


def _is_python_command(value: str) -> bool:
    return value in {"python", "python3"} or bool(
        re.fullmatch(r"python\d(?:\.\d+)?", value)
    )


def _missing_paths(paths: list[str], root: Path) -> list[str]:
    return [path for path in paths if not (root / path).exists()]


def _next_actions(
    *,
    command: str,
    recommended_command: str,
    recommended_level: str,
    executable_now: bool,
    root_present: bool,
    environment: dict[str, Any],
    reason: str,
    selected_tool_missing: bool,
    planned_runner_unprepared: bool,
    recommended_runner: str,
    runner_fallback_reason: str,
    prepared_test_runner: str,
    planned_environment_variables: dict[str, str],
    pytest_addopts: list[str],
    configured_test_paths: list[str],
    ci_command_candidates: list[dict[str, Any]],
) -> list[str]:
    if not command and not ci_command_candidates:
        return ["Profile must infer a repository test command before execution planning."]
    actions: list[str] = []
    install_command = str(environment.get("recommended_install_command") or "")
    if install_command:
        actions.append(f"Prepare repository dependencies with: {install_command}")
    if not root_present:
        actions.append(
            "Provide --repository-test-root or --checkout-repository-tests before executing repository tests."
        )
    if selected_tool_missing and recommended_runner:
        if planned_runner_unprepared and prepared_test_runner:
            actions.append(
                "The isolated setup prepared "
                f"`{prepared_test_runner}`, but the planned command uses "
                f"`{recommended_runner}`; select a prepared-runner command or "
                f"install `{recommended_runner}` in the isolated environment."
            )
        else:
            actions.append(
                f"Install or expose the `{recommended_runner}` test runner before executing this planned command."
            )
    if runner_fallback_reason:
        actions.append(
            "Agent selected a safe fallback test runner because "
            f"{runner_fallback_reason}."
        )
    if reason == "selected_tests_missing_in_checkout":
        actions.append("Verify checkout ref/depth because selected test files are missing.")
    if reason == "selected_working_dir_missing":
        actions.append(
            "Verify monorepo subproject checkout paths because the selected working directory is missing."
        )
    if planned_environment_variables:
        actions.append(
            "Execute with planned environment variables: "
            + ", ".join(
                f"{key}={value}"
                for key, value in planned_environment_variables.items()
            )
        )
    if pytest_addopts:
        actions.append(
            "Execute with safe pytest addopts from project config: "
            + " ".join(pytest_addopts)
        )
    if configured_test_paths:
        actions.append(
            "Use configured pytest testpaths first: "
            + ", ".join(configured_test_paths)
        )
    ci_commands = [
        str(candidate.get("command") or "")
        for candidate in ci_command_candidates
        if str(candidate.get("command") or "")
    ]
    if ci_commands:
        actions.append(
            "Compare planned command against CI test command candidates: "
            + "; ".join(ci_commands[:3])
        )
    if recommended_command:
        actions.append(f"Start with the planned {recommended_level} command: {recommended_command}")
    if recommended_level == "narrow":
        actions.append("Only expand to the full repository test command after the narrow command passes.")
    if executable_now:
        actions.append("The planned command can be executed from the current repository root.")
    return actions


def _join_command(args: list[str]) -> str:
    return shlex.join(args)


def _pytest_command_base_args(pytest_addopts: list[str]) -> list[str]:
    args = ["python", "-m", "pytest"]
    if not any(token in {"-q", "--quiet"} for token in pytest_addopts):
        args.append("-q")
    args.extend(pytest_addopts)
    return args


def _pytest_focused_command_args(
    pytest_addopts: list[str],
    selected_paths: list[str],
) -> list[str]:
    args = _pytest_command_base_args(pytest_addopts)
    if not any(
        token == "-x" or str(token).startswith("--maxfail=")
        for token in args
    ):
        args.append("--maxfail=1")
    return [*args, *selected_paths]


def _unittest_discover_targets(selected_paths: list[str]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for path in selected_paths:
        normalized = _clean_path(path)
        if not normalized or not _is_python_test_path(normalized):
            continue
        parsed = PurePosixPath(normalized)
        start_dir = str(parsed.parent) if str(parsed.parent) != "." else "."
        pattern = parsed.name
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.py", pattern):
            continue
        targets.append(
            {
                "start_dir": start_dir,
                "pattern": pattern,
                "scope": f"unittest discovery for {normalized}",
                "selected_test_paths": [normalized],
            }
        )
    return targets[:5]


def _unittest_discover_command_args(start_dir: str, pattern: str) -> list[str]:
    return [
        "python",
        "-m",
        "unittest",
        "discover",
        "-s",
        start_dir or ".",
        "-p",
        pattern or "test*.py",
    ]


def _safe_pytest_addopts(values: list[Any]) -> list[str]:
    tokens = [str(value) for value in values]
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


def _safe_configured_test_paths(values: list[Any]) -> list[str]:
    safe: list[str] = []
    for value in values:
        normalized = _clean_path(str(value))
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


def _is_python_test_path(path: str) -> bool:
    normalized = path.lower().replace("\\", "/")
    name = PurePosixPath(normalized).name
    return name.endswith(".py") and (
        "/tests/" in f"/{normalized}"
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _clean_path(value: str) -> str:
    return value.replace("\\", "/").strip().lstrip("/")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")
