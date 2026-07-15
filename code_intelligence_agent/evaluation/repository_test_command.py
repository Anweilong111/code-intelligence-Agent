from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from code_intelligence_agent.tools.runtime_security import (
    audit_repository_tree,
    build_restricted_environment,
    run_restricted_process,
)


def validate_repository_test_command(
    repository_profile: dict[str, Any],
    *,
    repository_root: str | Path | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    command = str(repository_profile.get("recommended_test_command") or "").strip()
    working_dir = str(repository_profile.get("recommended_test_working_dir") or "")
    if not command:
        return _skipped(
            command=command,
            working_dir=working_dir,
            reason="no_recommended_test_command",
            message="Repository profile did not infer a runnable test command.",
        )
    if repository_root is None:
        return _skipped(
            command=command,
            working_dir=working_dir,
            reason="full_repo_not_materialized",
            message=(
                "Recommended test command requires a full repository checkout; "
                "current onboarding materializes raw benchmark sources instead."
            ),
        )
    repo_path = Path(repository_root)
    if not repo_path.exists() or not repo_path.is_dir():
        return _skipped(
            command=command,
            repository_root=str(repo_path),
            working_dir=working_dir,
            cwd=str(repo_path),
            reason="repository_root_missing",
            message="Repository test root does not exist or is not a directory.",
        )
    tree_audit = audit_repository_tree(repo_path)
    if tree_audit["status"] != "pass":
        payload = _skipped(
            command=command,
            repository_root=str(repo_path),
            working_dir=working_dir,
            cwd=str(repo_path),
            reason="unsafe_repository_tree",
            message="Repository tree contains a symlink or could not be safely audited.",
        )
        payload["repository_tree_audit"] = tree_audit
        return payload
    command_cwd = _command_cwd(repo_path, working_dir)
    if command_cwd is None or not command_cwd.exists() or not command_cwd.is_dir():
        return _skipped(
            command=command,
            repository_root=str(repo_path),
            working_dir=working_dir,
            cwd=str(command_cwd or repo_path),
            reason="selected_working_dir_missing",
            message="Recommended test working directory is missing or unsafe.",
        )
    command_args = _safe_python_module_command(command)
    if not command_args:
        return _skipped(
            command=command,
            repository_root=str(repo_path),
            working_dir=working_dir,
            cwd=str(command_cwd),
            reason="unsupported_command",
            message="Only python -m module style test commands are executed.",
        )
    sandbox_home = repo_path / ".cia-test-home"
    if sandbox_home.is_symlink() or (
        sandbox_home.exists() and not sandbox_home.is_dir()
    ):
        return _skipped(
            command=command,
            repository_root=str(repo_path),
            working_dir=working_dir,
            cwd=str(command_cwd),
            reason="unsafe_sandbox_home",
            message="Repository test sandbox home is unsafe.",
        )
    sandbox_home.mkdir(parents=True, exist_ok=True)
    env, environment_isolation = build_restricted_environment(
        sandbox_home=sandbox_home
    )
    try:
        completed = run_restricted_process(
            command_args,
            cwd=command_cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "fail",
            "executed": True,
            "reason": "timeout",
            "message": f"Recommended test command exceeded {timeout}s timeout.",
            "command": command,
            "command_args": command_args,
            "repository_root": str(repo_path),
            "working_dir": working_dir,
            "cwd": str(command_cwd),
            "returncode": -1,
            "timeout": True,
            "passed": 0,
            "failed": 0,
            "stdout_preview": _preview(exc.stdout or ""),
            "stderr_preview": _preview(exc.stderr or ""),
            "next_actions": [
                "Increase repository_test_timeout or run a narrower test command.",
            ],
            "environment_isolation": environment_isolation,
            "repository_tree_audit": tree_audit,
        }
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    success = completed.returncode == 0
    return {
        "status": "pass" if success else "fail",
        "executed": True,
        "reason": "command_returncode",
        "message": (
            "Recommended test command completed successfully."
            if success
            else "Recommended test command returned a non-zero exit code."
        ),
        "command": command,
        "command_args": command_args,
        "repository_root": str(repo_path),
        "working_dir": working_dir,
        "cwd": str(command_cwd),
        "returncode": completed.returncode,
        "timeout": False,
        "passed": _extract_count(stdout, "passed"),
        "failed": _extract_count(stdout, "failed"),
        "stdout_preview": _preview(stdout),
        "stderr_preview": _preview(stderr),
        "environment_isolation": environment_isolation,
        "repository_tree_audit": tree_audit,
        "next_actions": []
        if success
        else [
            "Inspect stderr/stdout preview and verify dependencies for the full repository checkout.",
            "If this is a large repo, rerun with a narrower command or test selection.",
        ],
    }


def render_repository_test_command_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Repository Test Command Validation",
        "",
        f"- Status: `{_markdown_cell(payload.get('status', ''))}`",
        f"- Executed: {str(bool(payload.get('executed', False))).lower()}",
        f"- Reason: `{_markdown_cell(payload.get('reason', ''))}`",
        f"- Command: `{_markdown_cell(payload.get('command', ''))}`",
        f"- Repository Root: `{_markdown_cell(payload.get('repository_root') or 'none')}`",
        f"- Working Dir: `{_markdown_cell(payload.get('working_dir') or '.')}`",
        f"- CWD: `{_markdown_cell(payload.get('cwd', ''))}`",
        f"- Return Code: {_markdown_cell(payload.get('returncode'))}",
        f"- Timeout: {str(bool(payload.get('timeout', False))).lower()}",
        f"- Passed: {_int(payload.get('passed', 0))}",
        f"- Failed: {_int(payload.get('failed', 0))}",
        "",
        "## Message",
        "",
        _markdown_cell(payload.get("message", "")) or "none",
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


def write_repository_test_command_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    json_path = root / "repository_test_command.json"
    markdown_path = root / "repository_test_command.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_command_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_command_json": str(json_path),
        "repository_test_command_markdown": str(markdown_path),
    }


def _safe_python_module_command(command: str) -> list[str]:
    try:
        args = shlex.split(command)
    except ValueError:
        return []
    if len(args) < 3:
        return []
    executable = Path(args[0]).name.lower()
    if executable not in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
        return []
    if args[1] != "-m":
        return []
    module = args[2]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", module):
        return []
    return [sys.executable, "-m", module, *args[3:]]


def _command_cwd(repository_root: Path, working_dir: str) -> Path | None:
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
    reason: str,
    message: str,
    repository_root: str = "",
    working_dir: str = "",
    cwd: str = "",
) -> dict[str, Any]:
    return {
        "status": "skipped",
        "executed": False,
        "reason": reason,
        "message": message,
        "command": command,
        "command_args": [],
        "repository_root": repository_root,
        "working_dir": working_dir,
        "cwd": cwd,
        "returncode": None,
        "timeout": False,
        "passed": 0,
        "failed": 0,
        "stdout_preview": "",
        "stderr_preview": "",
        "next_actions": _skipped_next_actions(reason),
    }


def _skipped_next_actions(reason: str) -> list[str]:
    if reason == "full_repo_not_materialized":
        return [
            "Pass a full repository checkout via repository_test_root to execute the recommended command.",
            "Keep using benchmark_run artifacts for mutation benchmark validation when only raw sources are materialized.",
        ]
    if reason == "repository_root_missing":
        return ["Verify repository_test_root points to a local full repository checkout."]
    if reason == "selected_working_dir_missing":
        return [
            "Verify the recommended repository test working directory exists inside the checkout.",
            "If this is a monorepo, rerun repository profiling to select the intended subproject.",
        ]
    if reason == "unsupported_command":
        return ["Use a python -m module style command for safe non-shell execution."]
    return []


def _preview(value: str, limit: int = 4000) -> str:
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


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
