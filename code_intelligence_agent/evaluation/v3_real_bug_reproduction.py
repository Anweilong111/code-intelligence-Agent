from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from code_intelligence_agent.evaluation.github_repository_checkout import (
    checkout_github_repository,
)
from code_intelligence_agent.evaluation.repository_test_execution_result import (
    execute_repository_test_plan,
)


CheckoutFunction = Callable[..., dict[str, Any]]
ENVIRONMENT_FAILURE_CATEGORIES = {
    "missing_dependency",
    "missing_test_runner",
    "missing_pytest_fixture",
    "import_path_error",
    "framework_configuration_error",
    "pytest_collection_error",
    "no_tests_collected",
    "command_usage_error",
    "tox_missing_python_interpreter",
    "timeout",
    "execution_error",
}


def prepare_real_bug_case(
    case: dict[str, Any],
    output_dir: str | Path,
    *,
    checkout: CheckoutFunction = checkout_github_repository,
    checkout_timeout: int = 180,
    fresh_checkout: bool = True,
) -> dict[str, Any]:
    output_root = Path(output_dir).resolve()
    repository = _dict(case.get("repository"))
    owner_repo = str(repository.get("owner_repo") or "")
    parts = owner_repo.split("/", 1)
    if len(parts) != 2 or not all(parts):
        return _preparation_failure(case, "invalid_repository_identity")
    owner, repo = parts
    if fresh_checkout:
        reset = _reset_checkout_slots(output_root)
        if reset["status"] != "pass":
            return _preparation_failure(case, str(reset["reason"]))
    bug_result = checkout(
        owner=owner,
        repo=repo,
        output_dir=output_root / "bug",
        ref=str(case.get("bug_commit_sha") or ""),
        timeout=checkout_timeout,
    )
    fix_result = checkout(
        owner=owner,
        repo=repo,
        output_dir=output_root / "fix",
        ref=str(case.get("fix_commit_sha") or ""),
        timeout=checkout_timeout,
    )
    if bug_result.get("status") != "pass" or fix_result.get("status") != "pass":
        return {
            "status": "fail",
            "reason": "checkout_failed",
            "case_id": str(case.get("case_id") or ""),
            "bug_checkout": bug_result,
            "fix_checkout": fix_result,
            "test_overlay": {"status": "not_run", "files": []},
        }
    bug_root = Path(str(bug_result.get("checkout_path") or "")).resolve()
    fix_root = Path(str(fix_result.get("checkout_path") or "")).resolve()
    overlay = copy_test_overlay(
        bug_root,
        fix_root,
        [str(item) for item in _list(case.get("test_overlay_paths"))],
    )
    bug_preparation_files = materialize_preparation_files(
        bug_root,
        [_dict(item) for item in _list(case.get("preparation_files"))],
    )
    fix_preparation_files = materialize_preparation_files(
        fix_root,
        [_dict(item) for item in _list(case.get("preparation_files"))],
    )
    prepared = (
        overlay["status"] == "pass"
        and bug_preparation_files["status"] == "pass"
        and fix_preparation_files["status"] == "pass"
    )
    return {
        "status": "pass" if prepared else "fail",
        "reason": "case_prepared" if prepared else "case_preparation_failed",
        "case_id": str(case.get("case_id") or ""),
        "bug_checkout": bug_result,
        "fix_checkout": fix_result,
        "bug_root": str(bug_root),
        "fix_root": str(fix_root),
        "test_overlay": overlay,
        "bug_preparation_files": bug_preparation_files,
        "fix_preparation_files": fix_preparation_files,
    }


def copy_test_overlay(
    bug_root: str | Path,
    fix_root: str | Path,
    relative_paths: list[str],
) -> dict[str, Any]:
    bug_path = Path(bug_root).resolve()
    fix_path = Path(fix_root).resolve()
    copied: list[dict[str, Any]] = []
    errors: list[str] = []
    if not relative_paths:
        errors.append("test_overlay_paths_missing")
    for relative in relative_paths:
        if not _safe_relative_path(relative):
            errors.append(f"unsafe_test_overlay_path:{relative}")
            continue
        source = (fix_path / Path(*PurePosixPath(relative.replace("\\", "/")).parts)).resolve()
        target = (bug_path / Path(*PurePosixPath(relative.replace("\\", "/")).parts)).resolve()
        if not _within(source, fix_path) or not _within(target, bug_path):
            errors.append(f"test_overlay_path_escape:{relative}")
            continue
        if not source.is_file() or source.is_symlink():
            errors.append(f"test_overlay_source_missing_or_symlink:{relative}")
            continue
        if target.exists() and target.is_symlink():
            errors.append(f"test_overlay_target_is_symlink:{relative}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(
            {
                "path": PurePosixPath(relative.replace("\\", "/")).as_posix(),
                "sha256": _sha256_file(target),
                "size_bytes": target.stat().st_size,
            }
        )
    return {
        "status": "pass" if not errors else "fail",
        "copied_count": len(copied),
        "files": copied,
        "errors": errors,
        "policy": "copy_test_files_from_fix_commit_to_bug_commit",
    }


def materialize_preparation_files(
    repository_root: str | Path,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    written: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in files:
        relative = str(item.get("path") or "")
        content = item.get("content")
        reason = str(item.get("reason") or "")
        if not _safe_relative_path(relative):
            errors.append(f"unsafe_preparation_file_path:{relative}")
            continue
        if not isinstance(content, str) or len(content.encode("utf-8")) > 4096:
            errors.append(f"invalid_preparation_file_content:{relative}")
            continue
        if not reason:
            errors.append(f"missing_preparation_file_reason:{relative}")
            continue
        candidate = root / Path(*PurePosixPath(relative.replace("\\", "/")).parts)
        if _path_or_existing_parent_is_symlink(candidate, root):
            errors.append(f"unsafe_preparation_file_symlink:{relative}")
            continue
        target = candidate.resolve()
        if not _within(target, root):
            errors.append(f"preparation_file_path_escape:{relative}")
            continue
        if target.exists() and (target.is_symlink() or not target.is_file()):
            errors.append(f"unsafe_preparation_file_target:{relative}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(
            {
                "path": PurePosixPath(relative.replace("\\", "/")).as_posix(),
                "sha256": _sha256_file(target),
                "size_bytes": target.stat().st_size,
                "reason": reason,
            }
        )
    return {
        "status": "pass" if not errors else "fail",
        "requested_count": len(files),
        "written_count": len(written),
        "files": written,
        "errors": errors,
        "repository_code_executed": False,
    }


def audit_python_runtime(
    python_executable: str | Path,
    *,
    expected_version: str,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict[str, Any]:
    executable = Path(python_executable).resolve()
    if not executable.is_file():
        return {
            "status": "fail",
            "reason": "python_executable_missing",
            "python_executable": str(executable),
            "expected_version": expected_version,
            "observed_version": "",
            "exact_match": False,
        }
    runtime_runner = _runner_with_runtime_environment(executable, runner)
    try:
        completed = runtime_runner(
            [str(executable), "-c", "import sys; print(sys.version.split()[0])"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "fail",
            "reason": "python_version_probe_failed",
            "python_executable": str(executable),
            "expected_version": expected_version,
            "observed_version": "",
            "exact_match": False,
            "error": type(exc).__name__,
        }
    observed = str(completed.stdout or "").strip()
    exact = completed.returncode == 0 and observed == expected_version
    return {
        "status": "pass" if exact else "fail",
        "reason": "exact_python_version" if exact else "python_version_mismatch",
        "python_executable": str(executable),
        "expected_version": expected_version,
        "observed_version": observed,
        "exact_match": exact,
        "returncode": completed.returncode,
    }


def reproduce_real_bug_case(
    case: dict[str, Any],
    preparation: dict[str, Any],
    *,
    python_executable: str | Path,
    targeted_timeout: int = 120,
    regression_timeout: int = 900,
    run_full_regression: bool = True,
    runner=None,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    runtime = audit_python_runtime(
        python_executable,
        expected_version=str(case.get("python_version") or ""),
        runner=runner or subprocess.run,
    )
    if preparation.get("status") != "pass":
        return _reproduction_blocker(
            case,
            started_at=started_at,
            reason="preparation_failed",
            runtime=runtime,
            preparation=preparation,
        )
    if runtime.get("status") != "pass":
        return _reproduction_blocker(
            case,
            started_at=started_at,
            reason="python_version_mismatch",
            runtime=runtime,
            preparation=preparation,
        )
    bug_root = Path(str(preparation.get("bug_root") or ""))
    fix_root = Path(str(preparation.get("fix_root") or ""))
    commands = [
        [str(part) for part in _list(item)]
        for item in _list(case.get("targeted_test_commands"))
    ]
    bug_targeted = execute_test_commands(
        commands,
        repository_root=bug_root,
        python_executable=python_executable,
        timeout=targeted_timeout,
        runner=runner,
        test_environment=_dict(case.get("test_environment")),
    )
    benchmark_input_blocker = _missing_test_overlay_support_blocker(
        bug_targeted,
        bug_root=bug_root,
        fix_root=fix_root,
    )
    if benchmark_input_blocker:
        bug_targeted["environment_blocker"] = True
        bug_targeted["benchmark_input_blocker"] = benchmark_input_blocker
    fix_targeted = execute_test_commands(
        commands,
        repository_root=fix_root,
        python_executable=python_executable,
        timeout=targeted_timeout,
        runner=runner,
        test_environment=_dict(case.get("test_environment")),
    )
    if run_full_regression:
        full_commands = [
            [str(part) for part in _list(case.get("regression_command"))]
        ]
        fix_regression = execute_test_commands(
            full_commands,
            repository_root=fix_root,
            python_executable=python_executable,
            timeout=regression_timeout,
            runner=runner,
            test_environment=_dict(case.get("test_environment")),
        )
    else:
        fix_regression = {
            "status": "not_run",
            "reason": "full_regression_disabled",
            "command_count": 0,
            "results": [],
            "environment_blocker": False,
        }
    bug_failed_as_expected = (
        bug_targeted.get("status") == "fail"
        and not bool(bug_targeted.get("environment_blocker", False))
        and any(
            str(_dict(item).get("failure_category") or "")
            == "test_assertion_failure"
            and int(_dict(item).get("test_count") or 0) > 0
            for item in _list(bug_targeted.get("results"))
        )
    )
    fix_targeted_passed = _successful_nonempty_test_group(fix_targeted)
    fix_regression_passed = _successful_nonempty_test_group(fix_regression)
    reproducible = bug_failed_as_expected and fix_targeted_passed and fix_regression_passed
    blocker = benchmark_input_blocker or _first_environment_blocker(
        bug_targeted, fix_targeted, fix_regression
    )
    return {
        "schema_version": "3.0",
        "case_id": str(case.get("case_id") or ""),
        "status": "pass" if reproducible else "fail",
        "reason": (
            "real_bug_reproduced"
            if reproducible
            else "benchmark_input_blocker"
            if benchmark_input_blocker
            else "environment_blocker"
            if blocker
            else "bug_fix_behavior_not_reproduced"
        ),
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "runtime": runtime,
        "preparation": _portable_preparation(preparation),
        "bug_targeted": bug_targeted,
        "fix_targeted": fix_targeted,
        "fix_full_regression": fix_regression,
        "acceptance": {
            "bug_targeted_failed": bug_failed_as_expected,
            "fix_targeted_passed": fix_targeted_passed,
            "fix_full_regression_passed": fix_regression_passed,
            "reproducible": reproducible,
        },
        "blocker": blocker,
        "gold_patch_visible_to_execution": False,
    }


def execute_test_commands(
    commands: list[list[str]],
    *,
    repository_root: str | Path,
    python_executable: str | Path,
    timeout: int,
    runner=None,
    test_environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    repository_path = Path(repository_root).resolve()
    sandbox_home = repository_path / ".cia-test-home"
    if sandbox_home.is_symlink() or (
        sandbox_home.exists() and not sandbox_home.is_dir()
    ):
        return {
            "status": "fail",
            "reason": "unsafe_sandbox_home",
            "command_count": 0,
            "passed_command_count": 0,
            "failed_command_count": 0,
            "environment_blocker": True,
            "results": [],
        }
    sandbox_home.mkdir(parents=True, exist_ok=True)
    environment_result = _build_test_environment(
        repository_path,
        _dict(test_environment),
    )
    if environment_result["status"] != "pass":
        return {
            "status": "fail",
            "reason": str(environment_result["reason"]),
            "command_count": 0,
            "passed_command_count": 0,
            "failed_command_count": 0,
            "environment_blocker": True,
            "environment": environment_result,
            "results": [],
        }
    execution_runner = _runner_with_runtime_environment(
        Path(python_executable).resolve(),
        runner or subprocess.run,
        sandbox_home=sandbox_home,
        environment_overrides=_dict(environment_result.get("overrides")),
    )
    for command in commands:
        if not _safe_normalized_command(command):
            results.append(
                {
                    "status": "fail",
                    "executed": False,
                    "reason": "unsafe_command",
                    "command_args": command,
                    "failure_category": "execution_error",
                }
            )
            continue
        command_text = "python -m " + " ".join(command[2:])
        plan = {
            "recommended_execution_command": command_text,
            "recommended_execution_level": "targeted",
            "recommended_execution_risk": "low",
            "recommended_execution_scope": "v3_real_bug_reproduction",
            "recommended_execution_runner": command[2],
            "recommended_working_dir": "",
            "planned_environment_variables": {},
            "executable_now": True,
        }
        result = execute_repository_test_plan(
            plan,
            repository_root=repository_root,
            timeout=timeout,
            python_executable=python_executable,
            python_executable_source="v3_pinned_runtime",
            runner=execution_runner,
        )
        results.append(_portable_execution_result(result))
    environment_blocker = any(
        str(result.get("failure_category") or "") in ENVIRONMENT_FAILURE_CATEGORIES
        and result.get("status") != "pass"
        for result in results
    )
    passed = bool(results) and all(result.get("status") == "pass" for result in results)
    return {
        "status": "pass" if passed else "fail",
        "reason": "all_commands_passed" if passed else "one_or_more_commands_failed",
        "command_count": len(results),
        "passed_command_count": sum(result.get("status") == "pass" for result in results),
        "failed_command_count": sum(result.get("status") != "pass" for result in results),
        "environment_blocker": environment_blocker,
        "environment": {
            "status": environment_result["status"],
            "pythonpath_entries": environment_result["pythonpath_entries"],
            "optional_pythonpath_entries_missing": environment_result[
                "optional_pythonpath_entries_missing"
            ],
            "required_tools": environment_result["required_tools"],
        },
        "results": results,
    }


def render_reproduction_markdown(result: dict[str, Any]) -> str:
    acceptance = _dict(result.get("acceptance"))
    runtime = _dict(result.get("runtime"))
    lines = [
        "# V3 Real Bug Reproduction",
        "",
        f"- Case: `{result.get('case_id')}`",
        f"- Status: `{result.get('status')}`",
        f"- Reason: `{result.get('reason')}`",
        f"- Python: `{runtime.get('observed_version')}` (expected `{runtime.get('expected_version')}`)",
        f"- Bug targeted failed: `{acceptance.get('bug_targeted_failed')}`",
        f"- Fix targeted passed: `{acceptance.get('fix_targeted_passed')}`",
        f"- Fix full regression passed: `{acceptance.get('fix_full_regression_passed')}`",
        f"- Reproducible: `{acceptance.get('reproducible')}`",
        f"- Blocker: `{result.get('blocker') or 'none'}`",
        "",
        "The benchmark shell and setup scripts were not executed. Test files were copied from the fixed commit to the buggy commit using repository-relative paths before the targeted run.",
        "",
    ]
    return "\n".join(lines)


def write_reproduction_artifacts(
    result: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "reproduction.json"
    markdown_path = root / "reproduction.md"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_path.write_text(render_reproduction_markdown(result), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def _safe_normalized_command(command: list[str]) -> bool:
    if len(command) < 3 or command[:2] != ["{python}", "-m"]:
        return False
    if command[2] not in {"nose", "pytest", "unittest"}:
        return False
    return all(
        isinstance(part, str)
        and part
        and not any(token in part for token in ("&&", "||", ";", "|", ">", "<", "`", "$("))
        and "\x00" not in part
        and "\n" not in part
        and "\r" not in part
        for part in command
    )


def _runner_with_runtime_environment(
    python_executable: Path,
    runner: Callable[..., subprocess.CompletedProcess],
    *,
    sandbox_home: Path | None = None,
    environment_overrides: dict[str, Any] | None = None,
) -> Callable[..., subprocess.CompletedProcess]:
    prefix = python_executable.parent
    conda_environment = (prefix / "conda-meta").is_dir()

    def configured_runner(command, **kwargs):
        env = dict(kwargs.get("env") or os.environ)
        if sandbox_home is not None:
            env["HOME"] = str(sandbox_home)
            env["USERPROFILE"] = str(sandbox_home)
        for key, value in _dict(environment_overrides).items():
            env[str(key)] = str(value)
        if conda_environment:
            runtime_paths = [
                str(prefix),
                str(prefix / "Library" / "bin"),
                str(prefix / "Scripts"),
            ]
            existing = str(env.get("PATH") or "")
            env["PATH"] = os.pathsep.join(
                [*runtime_paths, *([existing] if existing else [])]
            )
            env["CONDA_PREFIX"] = str(prefix)
        kwargs["env"] = env
        return runner(command, **kwargs)

    return configured_runner


def _build_test_environment(
    repository_root: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    entries: list[str] = []
    resolved_entries: list[str] = []
    optional_missing: list[str] = []
    required_tools: list[str] = []
    tool_directories: list[str] = []
    configured_entries = [
        (str(item), False) for item in _list(config.get("pythonpath_entries"))
    ] + [
        (str(item), True)
        for item in _list(config.get("optional_pythonpath_entries"))
    ]
    for entry, optional in configured_entries:
        if not _safe_relative_path(entry):
            return {
                "status": "fail",
                "reason": "unsafe_pythonpath_entry",
                "pythonpath_entries": entries,
                "optional_pythonpath_entries_missing": optional_missing,
                "required_tools": required_tools,
                "overrides": {},
            }
        candidate = repository_root / Path(
            *PurePosixPath(entry.replace("\\", "/")).parts
        )
        if _path_or_existing_parent_is_symlink(candidate, repository_root):
            return {
                "status": "fail",
                "reason": "pythonpath_symlink_rejected",
                "pythonpath_entries": entries,
                "optional_pythonpath_entries_missing": optional_missing,
                "required_tools": required_tools,
                "overrides": {},
            }
        resolved = candidate.resolve()
        if not _within(resolved, repository_root):
            return {
                "status": "fail",
                "reason": "pythonpath_directory_missing_or_outside_repository",
                "pythonpath_entries": entries,
                "optional_pythonpath_entries_missing": optional_missing,
                "required_tools": required_tools,
                "overrides": {},
            }
        if not resolved.is_dir() and optional:
            optional_missing.append(
                PurePosixPath(entry.replace("\\", "/")).as_posix()
            )
            continue
        if not resolved.is_dir():
            return {
                "status": "fail",
                "reason": "pythonpath_directory_missing_or_outside_repository",
                "pythonpath_entries": entries,
                "optional_pythonpath_entries_missing": optional_missing,
                "required_tools": required_tools,
                "overrides": {},
            }
        entries.append(PurePosixPath(entry.replace("\\", "/")).as_posix())
        resolved_entries.append(str(resolved))
    for raw_tool in _list(config.get("required_tools")):
        tool = str(raw_tool)
        executable = _resolve_required_tool(tool)
        if executable is None:
            return {
                "status": "fail",
                "reason": f"required_test_tool_missing:{tool}",
                "pythonpath_entries": entries,
                "optional_pythonpath_entries_missing": optional_missing,
                "required_tools": required_tools,
                "overrides": {},
            }
        required_tools.append(tool)
        directory = str(executable.parent)
        if directory not in tool_directories:
            tool_directories.append(directory)
    overrides = {}
    if resolved_entries:
        overrides["PYTHONPATH"] = os.pathsep.join(resolved_entries)
    if tool_directories:
        existing_path = str(os.environ.get("PATH") or "")
        overrides["PATH"] = os.pathsep.join(
            [*tool_directories, *([existing_path] if existing_path else [])]
        )
    return {
        "status": "pass",
        "reason": "safe_test_environment",
        "pythonpath_entries": entries,
        "optional_pythonpath_entries_missing": optional_missing,
        "required_tools": required_tools,
        "overrides": overrides,
    }


def _resolve_required_tool(tool: str) -> Path | None:
    if tool != "ls":
        return None
    direct = shutil.which("ls")
    if direct:
        return Path(direct).resolve()
    if os.name != "nt":
        return None
    git = shutil.which("git")
    if not git:
        return None
    candidate = Path(git).resolve().parent.parent / "usr" / "bin" / "ls.exe"
    return candidate.resolve() if candidate.is_file() and not candidate.is_symlink() else None


def _first_environment_blocker(*groups: dict[str, Any]) -> dict[str, Any]:
    for group in groups:
        for result_value in _list(group.get("results")):
            result = _dict(result_value)
            category = str(result.get("failure_category") or "")
            if category in ENVIRONMENT_FAILURE_CATEGORIES and result.get("status") != "pass":
                return {
                    "layer": "environment",
                    "category": category,
                    "signal": str(result.get("failure_signal") or ""),
                    "diagnostic": str(result.get("diagnostic_summary") or ""),
                }
    return {}


def _missing_test_overlay_support_blocker(
    group: dict[str, Any],
    *,
    bug_root: str | Path,
    fix_root: str | Path,
) -> dict[str, Any]:
    bug = Path(bug_root).resolve()
    fix = Path(fix_root).resolve()
    for result_value in _list(group.get("results")):
        context = str(_dict(result_value).get("failure_context") or "")
        if "FileNotFoundError" not in context or "No such file or directory" not in context:
            continue
        for missing_value in re.findall(
            r"No such file or directory:\s*['\"]([^'\"]+)['\"]",
            context,
        ):
            missing_path = Path(missing_value)
            missing = (
                missing_path.resolve()
                if missing_path.is_absolute()
                else (bug / missing_path).resolve()
            )
            try:
                relative = missing.relative_to(bug)
            except (OSError, ValueError):
                continue
            relative_parts = tuple(part.lower() for part in relative.parts)
            if not relative_parts or relative_parts[0] not in {"test", "tests"}:
                continue
            fix_file = (fix / relative).resolve()
            if (
                not _within(fix_file, fix)
                or not fix_file.is_file()
                or fix_file.is_symlink()
            ):
                continue
            return {
                "layer": "benchmark_input",
                "category": "missing_test_overlay_support_file",
                "relative_path": relative.as_posix(),
                "signal": "Bug-side targeted test referenced a missing test support file.",
                "diagnostic": (
                    "The same test-only path exists at the fix revision and must be "
                    "declared in test_overlay_paths before this case can be accepted."
                ),
            }
    return {}


def _successful_nonempty_test_group(group: dict[str, Any]) -> bool:
    results = [_dict(item) for item in _list(group.get("results"))]
    return (
        group.get("status") == "pass"
        and bool(results)
        and all(
            result.get("status") == "pass"
            and int(result.get("test_count") or 0) > 0
            for result in results
        )
    )


def _portable_execution_result(result: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "status",
        "executed",
        "reason",
        "message",
        "command",
        "command_args",
        "execution_runner",
        "returncode",
        "timeout",
        "passed",
        "failed",
        "errors",
        "skipped",
        "test_count",
        "test_count_source",
        "failure_category",
        "failure_signal",
        "diagnostic_summary",
        "failure_context",
        "stdout_preview",
        "stderr_preview",
    }
    return {key: value for key, value in result.items() if key in allowed}


def _portable_preparation(preparation: dict[str, Any]) -> dict[str, Any]:
    bug = _dict(preparation.get("bug_checkout"))
    fix = _dict(preparation.get("fix_checkout"))
    return {
        "status": str(preparation.get("status") or ""),
        "reason": str(preparation.get("reason") or ""),
        "bug_checkout": {
            "status": str(bug.get("status") or ""),
            "reason": str(bug.get("reason") or ""),
            "ref": str(bug.get("ref") or ""),
            "checkout_method": str(bug.get("checkout_method") or ""),
        },
        "fix_checkout": {
            "status": str(fix.get("status") or ""),
            "reason": str(fix.get("reason") or ""),
            "ref": str(fix.get("ref") or ""),
            "checkout_method": str(fix.get("checkout_method") or ""),
        },
        "test_overlay": _dict(preparation.get("test_overlay")),
        "bug_preparation_files": _dict(preparation.get("bug_preparation_files")),
        "fix_preparation_files": _dict(preparation.get("fix_preparation_files")),
    }


def _preparation_failure(case: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "status": "fail",
        "reason": reason,
        "case_id": str(case.get("case_id") or ""),
        "bug_checkout": {},
        "fix_checkout": {},
        "test_overlay": {"status": "not_run", "files": []},
    }


def _reset_checkout_slots(output_root: Path) -> dict[str, str]:
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        for name in ("bug", "fix"):
            slot = output_root / name
            if slot.resolve().parent != output_root or slot.name != name:
                return {"status": "fail", "reason": "unsafe_checkout_reset_path"}
            if slot.is_symlink():
                return {"status": "fail", "reason": "checkout_reset_symlink_rejected"}
            if slot.exists():
                if not slot.is_dir():
                    return {"status": "fail", "reason": "checkout_reset_not_directory"}
                shutil.rmtree(slot)
    except OSError as exc:
        return {
            "status": "fail",
            "reason": f"checkout_reset_failed:{type(exc).__name__}",
        }
    return {"status": "pass", "reason": "fresh_checkout_slots"}


def _reproduction_blocker(
    case: dict[str, Any],
    *,
    started_at: str,
    reason: str,
    runtime: dict[str, Any],
    preparation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "3.0",
        "case_id": str(case.get("case_id") or ""),
        "status": "fail",
        "reason": reason,
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "runtime": runtime,
        "preparation": _portable_preparation(preparation),
        "bug_targeted": {"status": "not_run", "results": []},
        "fix_targeted": {"status": "not_run", "results": []},
        "fix_full_regression": {"status": "not_run", "results": []},
        "acceptance": {
            "bug_targeted_failed": False,
            "fix_targeted_passed": False,
            "fix_full_regression_passed": False,
            "reproducible": False,
        },
        "blocker": {"layer": "environment", "category": reason},
        "gold_patch_visible_to_execution": False,
    }


def _safe_relative_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if not value or normalized.startswith("//"):
        return False
    if len(normalized) >= 2 and normalized[0].isalpha() and normalized[1] == ":":
        return False
    pure = PurePosixPath(normalized)
    return not pure.is_absolute() and ".." not in pure.parts


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _path_or_existing_parent_is_symlink(path: Path, root: Path) -> bool:
    current = path
    while current != root:
        if current.is_symlink():
            return True
        parent = current.parent
        if parent == current:
            return True
        current = parent
    return root.is_symlink()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reproduce one fixed-SHA real Python bug without executing benchmark shell scripts."
    )
    parser.add_argument("catalog", help="Candidate real-bug catalog JSON.")
    parser.add_argument("case_id", help="Case identifier from the catalog.")
    parser.add_argument("output_dir", help="Ignored output directory for checkout and evidence.")
    parser.add_argument("--python-executable", required=True)
    parser.add_argument("--targeted-timeout", type=int, default=120)
    parser.add_argument("--regression-timeout", type=int, default=900)
    parser.add_argument("--skip-full-regression", action="store_true")
    parser.add_argument("--require-pass", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    cases = [_dict(item) for item in _list(_dict(catalog).get("cases"))]
    case = next(
        (item for item in cases if str(item.get("case_id") or "") == args.case_id),
        None,
    )
    if case is None:
        raise SystemExit(f"Unknown case_id: {args.case_id}")
    preparation = prepare_real_bug_case(case, args.output_dir)
    result = reproduce_real_bug_case(
        case,
        preparation,
        python_executable=args.python_executable,
        targeted_timeout=args.targeted_timeout,
        regression_timeout=args.regression_timeout,
        run_full_regression=not args.skip_full_regression,
    )
    write_reproduction_artifacts(result, args.output_dir)
    print(render_reproduction_markdown(result))
    if args.require_pass and result["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
