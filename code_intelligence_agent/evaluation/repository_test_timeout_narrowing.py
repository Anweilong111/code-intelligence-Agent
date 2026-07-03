from __future__ import annotations

import ast
import json
import shlex
from pathlib import Path, PurePosixPath
from typing import Any

from code_intelligence_agent.evaluation.repository_test_execution_result import (
    execute_repository_test_plan,
    render_repository_test_execution_result_markdown,
)


def plan_repository_test_timeout_narrowing(
    execution_plan: dict[str, Any] | None,
    retry_execution_result: dict[str, Any] | None,
    *,
    repository_root: str | Path | None = None,
    max_attempts: int = 5,
) -> dict[str, Any]:
    plan = _dict(execution_plan)
    retry = _dict(retry_execution_result)
    root = Path(repository_root) if repository_root is not None else None
    command = str(retry.get("command") or retry.get("retry_command") or "").strip()
    if str(retry.get("failure_category") or "") != "timeout":
        return _skipped_plan(
            plan,
            retry,
            reason="no_timeout_retry",
            message="Retry execution did not time out.",
            repository_root=root,
        )
    if not _is_python_pytest_command(command):
        return _skipped_plan(
            plan,
            retry,
            reason="unsupported_timeout_command",
            message="Only python -m pytest timeout commands are narrowed automatically.",
            repository_root=root,
        )
    if root is None or not root.exists() or not root.is_dir():
        return _skipped_plan(
            plan,
            retry,
            reason="repository_root_missing",
            message="Timeout narrowing requires a full repository checkout root.",
            repository_root=root,
        )

    selected_targets = _selected_targets_for_timeout_command(plan, command)
    selected_paths = _target_paths(selected_targets)
    expanded_targets = _expand_selected_test_targets(
        selected_targets,
        repository_root=root,
        max_attempts=max(0, max_attempts),
    )
    if not expanded_targets:
        return _skipped_plan(
            plan,
            retry,
            reason="no_narrow_test_files",
            message="No narrower pytest test files could be derived from the timeout command.",
            repository_root=root,
            selected_paths=selected_paths,
            selected_targets=selected_targets,
        )
    narrow_targets = [str(item["target"]) for item in expanded_targets]
    expanded_paths = _target_paths(narrow_targets)
    attempts = [
        {
            "index": index,
            "path": str(target["path"]),
            "target": str(target["target"]),
            "granularity": str(target["granularity"]),
            "command": _pytest_target_command(str(target["target"])),
            "runner": "pytest",
            "risk": "low",
            "source": "timeout_narrowing",
            "reason": "retry_timeout_target_narrowing",
        }
        for index, target in enumerate(expanded_targets)
    ]
    return {
        "status": "pass",
        "reason": "timeout_narrowing_plan_built",
        "message": "Timed-out pytest retry can be narrowed to individual test files or nodeids.",
        "repository_root": str(root),
        "timeout_command": command,
        "timeout_failure_signal": str(retry.get("failure_signal") or ""),
        "selected_test_paths": selected_paths,
        "selected_test_targets": selected_targets,
        "narrow_test_files": expanded_paths,
        "narrow_test_targets": narrow_targets,
        "max_attempts": max(0, max_attempts),
        "attempts": attempts,
        "safe_to_execute": True,
        "next_actions": [
            "Execute file-level pytest attempts until one completes without timing out."
        ],
    }


def execute_repository_test_timeout_narrowing(
    narrowing_plan: dict[str, Any] | None,
    *,
    enabled: bool = False,
    timeout: int = 20,
    python_executable: str | Path | None = None,
    python_executable_source: str = "current_interpreter",
    runner=None,
) -> dict[str, Any]:
    plan = _dict(narrowing_plan)
    if not enabled:
        return _skipped_execution(
            plan,
            reason="execution_disabled",
            message="Repository test timeout narrowing execution is disabled.",
        )
    if str(plan.get("status") or "") != "pass":
        return _skipped_execution(
            plan,
            reason="narrowing_plan_not_ready",
            message="Repository test timeout narrowing plan is not ready to execute.",
        )
    if not bool(plan.get("safe_to_execute", False)):
        return _skipped_execution(
            plan,
            reason="narrowing_plan_not_safe",
            message="Repository test timeout narrowing plan was not marked safe.",
        )
    attempts = [_dict(item) for item in _list(plan.get("attempts"))]
    if not attempts:
        return _skipped_execution(
            plan,
            reason="no_narrowing_attempts",
            message="Repository test timeout narrowing plan did not include attempts.",
        )
    repository_root = str(plan.get("repository_root") or "")
    results: list[dict[str, Any]] = []
    selected: dict[str, Any] = {}
    for attempt in attempts:
        result = execute_repository_test_plan(
            {
                "recommended_execution_command": str(attempt.get("command") or ""),
                "recommended_execution_level": "file",
                "recommended_execution_risk": "low",
                "recommended_execution_scope": "timeout_narrowing",
                "recommended_execution_runner": "pytest",
                "repository_root": repository_root,
                "executable_now": True,
            },
            repository_root=repository_root,
            timeout=timeout,
            python_executable=python_executable,
            python_executable_source=python_executable_source,
            runner=runner,
        )
        result = dict(result)
        result.update(
            {
                "timeout_narrowing_attempt_index": attempt.get("index"),
                "timeout_narrowing_path": str(attempt.get("path") or ""),
                "timeout_narrowing_target": str(attempt.get("target") or ""),
                "timeout_narrowing_granularity": str(
                    attempt.get("granularity") or ""
                ),
                "timeout_narrowing_source": str(attempt.get("source") or ""),
                "triggered_by": "repository_test_timeout_narrowing",
                "previous_timeout_command": str(plan.get("timeout_command") or ""),
                "previous_timeout_failure_signal": str(
                    plan.get("timeout_failure_signal") or ""
                ),
            }
        )
        results.append(result)
        selected = result
        if str(result.get("failure_category") or "") != "timeout":
            break
    status = str(selected.get("status") or "skipped")
    reason = (
        "timeout_narrowing_selected_non_timeout_result"
        if selected and str(selected.get("failure_category") or "") != "timeout"
        else (
            "timeout_narrowing_all_attempts_timed_out"
            if selected
            else "timeout_narrowing_no_attempt_executed"
        )
    )
    payload = dict(plan)
    payload.update(
        {
            "status": status if selected else "skipped",
            "executed": bool(results),
            "reason": reason,
            "message": _execution_message(reason),
            "attempt_count": len(results),
            "attempt_results": results,
            "selected_execution": selected,
            "selected_command": str(selected.get("command") or ""),
            "selected_failure_category": str(selected.get("failure_category") or ""),
            "selected_failure_signal": str(selected.get("failure_signal") or ""),
            "next_actions": _execution_next_actions(reason),
        }
    )
    return payload


def render_repository_test_timeout_narrowing_markdown(payload: dict[str, Any]) -> str:
    selected = _dict(payload.get("selected_execution"))
    lines = [
        "# Repository Test Timeout Narrowing",
        "",
        f"- Status: `{_markdown_cell(payload.get('status', ''))}`",
        f"- Executed: {str(bool(payload.get('executed', False))).lower()}",
        f"- Reason: `{_markdown_cell(payload.get('reason', ''))}`",
        (
            "- Timeout Command: "
            f"`{_markdown_cell(payload.get('timeout_command') or 'none')}`"
        ),
        f"- Attempt Count: {_int(payload.get('attempt_count', 0))}",
        (
            "- Selected Command: "
            f"`{_markdown_cell(payload.get('selected_command') or 'none')}`"
        ),
        (
            "- Selected Failure Category: "
            f"`{_markdown_cell(payload.get('selected_failure_category') or 'none')}`"
        ),
        "",
        "## Narrow Test Files",
        "",
    ]
    for path in _list(payload.get("narrow_test_files")):
        lines.append(f"- `{_markdown_cell(path)}`")
    if not _list(payload.get("narrow_test_files")):
        lines.append("- none")
    lines.extend(["", "## Narrow Test Targets", ""])
    for target in _list(payload.get("narrow_test_targets")):
        lines.append(f"- `{_markdown_cell(target)}`")
    if not _list(payload.get("narrow_test_targets")):
        lines.append("- none")
    lines.extend(["", "## Attempt Results", ""])
    lines.append(
        "| Index | Target | Path | Granularity | Status | Failure Category | Command |"
    )
    lines.append("| ---: | --- | --- | --- | --- | --- | --- |")
    for result in _list(payload.get("attempt_results")):
        row = _dict(result)
        lines.append(
            "| "
            f"{_markdown_cell(row.get('timeout_narrowing_attempt_index'))} | "
            f"`{_markdown_cell(row.get('timeout_narrowing_target') or '')}` | "
            f"`{_markdown_cell(row.get('timeout_narrowing_path') or '')}` | "
            f"{_markdown_cell(row.get('timeout_narrowing_granularity') or '')} | "
            f"{_markdown_cell(row.get('status') or '')} | "
            f"{_markdown_cell(row.get('failure_category') or '')} | "
            f"`{_markdown_cell(row.get('command') or '')}` |"
        )
    if not _list(payload.get("attempt_results")):
        lines.append("| 0 | none | none | none | none |")
    lines.extend(["", "## Selected Execution", ""])
    if selected:
        lines.append(render_repository_test_execution_result_markdown(selected))
    else:
        lines.append("none")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    return "\n".join(lines)


def write_repository_test_timeout_narrowing_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_timeout_narrowing.json"
    markdown_path = root / "repository_test_timeout_narrowing.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_timeout_narrowing_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_timeout_narrowing_json": str(json_path),
        "repository_test_timeout_narrowing_markdown": str(markdown_path),
    }


def _skipped_plan(
    plan: dict[str, Any],
    retry: dict[str, Any],
    *,
    reason: str,
    message: str,
    repository_root: Path | None,
    selected_paths: list[str] | None = None,
    selected_targets: list[str] | None = None,
) -> dict[str, Any]:
    command = str(retry.get("command") or retry.get("retry_command") or "").strip()
    targets = selected_targets or _selected_targets_for_timeout_command(
        plan,
        command,
    )
    return {
        "status": "skipped",
        "reason": reason,
        "message": message,
        "repository_root": str(repository_root) if repository_root is not None else "",
        "timeout_command": command,
        "timeout_failure_signal": str(retry.get("failure_signal") or ""),
        "selected_test_paths": selected_paths or _target_paths(targets),
        "selected_test_targets": targets,
        "narrow_test_files": [],
        "narrow_test_targets": [],
        "max_attempts": 0,
        "attempts": [],
        "safe_to_execute": False,
        "next_actions": _plan_next_actions(reason),
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
            "attempt_count": 0,
            "attempt_results": [],
            "selected_execution": {},
            "selected_command": "",
            "selected_failure_category": "not_executed",
            "selected_failure_signal": reason,
            "next_actions": _execution_next_actions(reason),
        }
    )
    return payload


def _selected_paths_for_timeout_command(
    execution_plan: dict[str, Any],
    command: str,
) -> list[str]:
    return _target_paths(_selected_targets_for_timeout_command(execution_plan, command))


def _selected_targets_for_timeout_command(
    execution_plan: dict[str, Any],
    command: str,
) -> list[str]:
    for candidate in _list(execution_plan.get("candidate_commands")):
        row = _dict(candidate)
        if str(row.get("command") or "").strip() == command:
            targets = _safe_pytest_targets(
                [
                    *_list(row.get("selected_test_targets")),
                    *_list(row.get("selected_test_paths")),
                ]
            )
            if targets:
                return targets
    targets = _safe_pytest_targets(
        [
            *_list(execution_plan.get("selected_test_targets")),
            *_list(execution_plan.get("selected_test_paths")),
        ]
    )
    if targets:
        return targets
    return _pytest_targets_from_command(command)


def _expand_selected_test_targets(
    selected_targets: list[str],
    *,
    repository_root: Path,
    max_attempts: int,
) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    for target in selected_targets:
        if len(targets) >= max_attempts:
            break
        rel_path = _target_path(target)
        path = _safe_repo_path(repository_root, rel_path)
        if path is None:
            continue
        if "::" in target and path.is_file() and _is_test_file(path):
            _append_unique_target(
                targets,
                target=target,
                path=_relative_posix(path, repository_root),
                granularity="nodeid",
            )
        elif path.is_file() and _is_test_file(path):
            nodeids = _pytest_nodeids_for_file(path, repository_root=repository_root)
            if nodeids:
                for nodeid in nodeids:
                    _append_unique_target(
                        targets,
                        target=nodeid,
                        path=_relative_posix(path, repository_root),
                        granularity="nodeid",
                    )
                    if len(targets) >= max_attempts:
                        break
            else:
                _append_unique_target(
                    targets,
                    target=_relative_posix(path, repository_root),
                    path=_relative_posix(path, repository_root),
                    granularity="file",
                )
        elif path.is_dir():
            for child in _iter_test_files(path):
                rel_child = _relative_posix(child, repository_root)
                _append_unique_target(
                    targets,
                    target=rel_child,
                    path=rel_child,
                    granularity="file",
                )
                if len(targets) >= max_attempts:
                    break
    return targets[:max_attempts]


def _pytest_targets_from_command(command: str) -> list[str]:
    if not _is_python_pytest_command(command):
        return []
    try:
        parts = shlex.split(command)
    except ValueError:
        return []
    targets: list[str] = []
    skip_next = False
    options_with_values = {
        "--maxfail",
        "--tb",
        "--junitxml",
        "--rootdir",
        "-k",
        "-m",
        "-o",
    }
    for item in parts[3:]:
        if skip_next:
            skip_next = False
            continue
        if item in options_with_values:
            skip_next = True
            continue
        if item.startswith("-"):
            continue
        if "=" in item and item.split("=", 1)[0] in options_with_values:
            continue
        target = _safe_pytest_target(item)
        if target:
            targets.append(target)
    return targets


def _pytest_target_command(target: str) -> str:
    return shlex.join(["python", "-m", "pytest", "-q", "--maxfail=1", target])


def _is_python_pytest_command(command: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if len(parts) < 3:
        return False
    executable = Path(parts[0]).name.lower()
    return executable in {
        "python",
        "python.exe",
        "python3",
        "python3.exe",
        "py",
        "py.exe",
    } and parts[1:3] == ["-m", "pytest"]


def _iter_test_files(path: Path) -> list[Path]:
    children = [child for child in path.rglob("*.py") if _is_test_file(child)]
    return sorted(children, key=lambda item: item.as_posix())


def _is_test_file(path: Path) -> bool:
    name = path.name
    return bool(
        name.startswith("test_")
        or name.endswith("_test.py")
    )


def _pytest_nodeids_for_file(path: Path, *, repository_root: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    rel_path = _relative_posix(path, repository_root)
    nodeids: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_pytest_function_name(node.name):
                _append_unique(nodeids, f"{rel_path}::{node.name}")
        elif isinstance(node, ast.ClassDef) and (
            _is_pytest_class_name(node.name) or _inherits_unittest_testcase(node)
        ):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if _is_pytest_function_name(child.name):
                        _append_unique(nodeids, f"{rel_path}::{node.name}::{child.name}")
    return nodeids


def _is_pytest_function_name(name: str) -> bool:
    return name.startswith("test_")


def _is_pytest_class_name(name: str) -> bool:
    return name.startswith("Test")


def _inherits_unittest_testcase(node: ast.ClassDef) -> bool:
    for base in node.bases:
        base_name = _ast_name(base)
        if base_name in {"TestCase", "unittest.TestCase"}:
            return True
    return False


def _ast_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _ast_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _safe_repo_path(repository_root: Path, rel_path: str) -> Path | None:
    safe = _safe_rel_path(rel_path)
    if not safe:
        return None
    candidate = (repository_root / safe).resolve()
    root = repository_root.resolve()
    try:
        if not candidate.is_relative_to(root):
            return None
    except ValueError:
        return None
    return candidate


def _safe_rel_paths(values: list[Any]) -> list[str]:
    paths: list[str] = []
    for item in values:
        safe = _safe_rel_path(str(item or ""))
        if safe:
            _append_unique(paths, safe)
    return paths


def _safe_rel_path(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or text.startswith("-"):
        return ""
    if "::" in text:
        text = text.split("::", 1)[0]
    pure = PurePosixPath(text)
    if pure.is_absolute() or ".." in pure.parts:
        return ""
    return pure.as_posix()


def _safe_pytest_targets(values: list[Any]) -> list[str]:
    targets: list[str] = []
    for item in values:
        target = _safe_pytest_target(str(item or ""))
        if target:
            _append_unique(targets, target)
    return targets


def _safe_pytest_target(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or text.startswith("-"):
        return ""
    if any(char in text for char in ("\x00", "\n", "\r")):
        return ""
    path_text, separator, suffix = text.partition("::")
    safe_path = _safe_rel_path(path_text)
    if not safe_path:
        return ""
    if not separator:
        return safe_path
    node_parts = [part.strip() for part in suffix.split("::")]
    if not node_parts or any(
        not part or any(char in part for char in ("\x00", "\n", "\r"))
        for part in node_parts
    ):
        return ""
    return f"{safe_path}::{'::'.join(node_parts)}"


def _target_path(target: str) -> str:
    return str(target or "").split("::", 1)[0]


def _target_paths(targets: list[str]) -> list[str]:
    paths: list[str] = []
    for target in targets:
        safe = _safe_rel_path(_target_path(target))
        if safe:
            _append_unique(paths, safe)
    return paths


def _relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _append_unique_target(
    items: list[dict[str, str]],
    *,
    target: str,
    path: str,
    granularity: str,
) -> None:
    if target and all(str(item.get("target") or "") != target for item in items):
        items.append(
            {
                "target": target,
                "path": path,
                "granularity": granularity,
            }
        )


def _plan_next_actions(reason: str) -> list[str]:
    if reason == "repository_root_missing":
        return ["Provide a full repository checkout before timeout narrowing."]
    if reason == "no_narrow_test_files":
        return ["Inspect repository_profile.json test_source_paths and pytest config."]
    if reason == "unsupported_timeout_command":
        return ["Use a python -m pytest retry command before timeout narrowing."]
    return []


def _execution_message(reason: str) -> str:
    if reason == "timeout_narrowing_selected_non_timeout_result":
        return "A narrower pytest attempt completed without timing out."
    if reason == "timeout_narrowing_all_attempts_timed_out":
        return "All file-level pytest attempts timed out."
    return "Repository test timeout narrowing did not execute a usable attempt."


def _execution_next_actions(reason: str) -> list[str]:
    if reason == "timeout_narrowing_selected_non_timeout_result":
        return ["Use the selected file-level result as dynamic evidence."]
    if reason == "timeout_narrowing_all_attempts_timed_out":
        return ["Increase repository_test_timeout or select an even narrower test nodeid."]
    if reason == "execution_disabled":
        return ["Enable repository test timeout narrowing execution."]
    return []


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
