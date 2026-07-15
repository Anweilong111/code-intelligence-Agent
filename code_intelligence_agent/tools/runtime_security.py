from __future__ import annotations

import os
import re
import signal
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence


RUNTIME_SECURITY_POLICY_VERSION = 1
RUNTIME_GUARD_DIR = Path(__file__).with_name("runtime_guard")
_SAFE_INHERITED_ENVIRONMENT_NAMES = {
    "APPDATA",
    "COMSPEC",
    "CONDA_PREFIX",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LC_ALL",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "TZ",
    "VIRTUAL_ENV",
    "WINDIR",
}
_SENSITIVE_NAME_PATTERN = re.compile(
    r"(?:API[_-]?KEY|AUTH|BEARER|COOKIE|CREDENTIAL|PASSWORD|PRIVATE[_-]?KEY|SECRET|TOKEN)",
    flags=re.IGNORECASE,
)
_SENSITIVE_PREFIXES = (
    "CIA_",
    "OPENAI_",
    "DEEPSEEK_",
    "DASHSCOPE_",
    "ANTHROPIC_",
    "AZURE_OPENAI_",
    "AWS_SECRET_",
    "GOOGLE_API_",
    "GITHUB_TOKEN",
)


def is_sensitive_environment_name(name: str) -> bool:
    normalized = str(name or "").strip().upper()
    return bool(
        normalized.startswith(_SENSITIVE_PREFIXES)
        or _SENSITIVE_NAME_PATTERN.search(normalized)
    )


def build_restricted_environment(
    *,
    base: Mapping[str, Any] | None = None,
    overrides: Mapping[str, Any] | None = None,
    sandbox_home: str | Path | None = None,
    network_policy: str = "deny",
    cpu_seconds: int = 30,
    memory_mb: int = 1024,
    max_file_mb: int = 128,
) -> tuple[dict[str, str], dict[str, Any]]:
    source = dict(os.environ if base is None else base)
    env = {
        str(name): str(value)
        for name, value in source.items()
        if str(name).upper() in _SAFE_INHERITED_ENVIRONMENT_NAMES
        and not is_sensitive_environment_name(str(name))
    }
    blocked_sensitive_names = sorted(
        str(name) for name in source if is_sensitive_environment_name(str(name))
    )
    rejected_override_names: list[str] = []
    override_values = dict(overrides or {})
    for raw_name, raw_value in override_values.items():
        name = str(raw_name)
        if is_sensitive_environment_name(name):
            rejected_override_names.append(name)
            continue
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name):
            rejected_override_names.append(name)
            continue
        env[name] = str(raw_value)

    if sandbox_home is not None:
        home = str(Path(sandbox_home).resolve())
        env["HOME"] = home
        env["USERPROFILE"] = home
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    configured_pythonpath = [
        part
        for part in str(env.get("PYTHONPATH") or "").split(os.pathsep)
        if part
    ]
    guard_path = str(RUNTIME_GUARD_DIR.resolve())
    env["PYTHONPATH"] = os.pathsep.join(
        [guard_path, *[part for part in configured_pythonpath if part != guard_path]]
    )
    effective_network_policy = "deny" if network_policy != "allow" else "allow"
    env["CIA_RUNTIME_NETWORK_POLICY"] = effective_network_policy
    env["CIA_RUNTIME_CPU_SECONDS"] = str(max(1, int(cpu_seconds)))
    env["CIA_RUNTIME_MEMORY_MB"] = str(max(64, int(memory_mb)))
    env["CIA_RUNTIME_MAX_FILE_MB"] = str(max(1, int(max_file_mb)))

    posix_limits_available = os.name != "nt"
    audit = {
        "schema_version": RUNTIME_SECURITY_POLICY_VERSION,
        "status": "pass",
        "reason": "restricted_environment_built",
        "environment_policy": "allowlisted_host_variables_plus_controlled_overrides",
        "inherited_environment_names": sorted(
            name for name in env if name in _SAFE_INHERITED_ENVIRONMENT_NAMES
        ),
        "blocked_sensitive_variable_count": len(blocked_sensitive_names),
        "blocked_sensitive_variable_names": blocked_sensitive_names,
        "rejected_override_names": sorted(set(rejected_override_names)),
        "network_policy": effective_network_policy,
        "network_enforcement": (
            "python_external_socket_guard_loopback_allowed"
            if effective_network_policy == "deny"
            else "not_requested"
        ),
        "network_residual_risk": (
            "native child processes require container-level network isolation"
            if effective_network_policy == "deny"
            else "network explicitly allowed"
        ),
        "resource_limits": {
            "wall_clock_timeout": "enforced_by_parent_runner",
            "cpu_seconds": max(1, int(cpu_seconds)),
            "memory_mb": max(64, int(memory_mb)),
            "max_file_mb": max(1, int(max_file_mb)),
            "posix_rlimit_available": posix_limits_available,
            "platform_limitation": (
                "Windows relies on wall-clock termination; use a container or Job Object for hard CPU, memory, and disk quotas."
                if not posix_limits_available
                else ""
            ),
        },
        "process_tree_policy": (
            "windows_taskkill_tree_on_timeout"
            if os.name == "nt"
            else "posix_process_group_on_timeout"
        ),
    }
    return env, audit


def audit_repository_tree(
    root: str | Path,
    *,
    max_entries: int = 250_000,
) -> dict[str, Any]:
    path = Path(root)
    if not path.exists() or not path.is_dir():
        return {
            "status": "fail",
            "reason": "repository_root_missing",
            "entry_count": 0,
            "symlink_paths": [],
        }
    if path.is_symlink():
        return {
            "status": "fail",
            "reason": "repository_root_symlink_rejected",
            "entry_count": 1,
            "symlink_paths": ["."],
        }
    entry_count = 0
    symlinks: list[str] = []
    try:
        for current, directory_names, file_names in os.walk(path, followlinks=False):
            current_path = Path(current)
            for name in [*directory_names, *file_names]:
                entry_count += 1
                candidate = current_path / name
                if candidate.is_symlink():
                    symlinks.append(candidate.relative_to(path).as_posix())
                if entry_count > max_entries:
                    return {
                        "status": "fail",
                        "reason": "repository_tree_scan_limit_exceeded",
                        "entry_count": entry_count,
                        "symlink_paths": symlinks[:20],
                    }
    except OSError as exc:
        return {
            "status": "fail",
            "reason": "repository_tree_scan_error",
            "entry_count": entry_count,
            "symlink_paths": symlinks[:20],
            "error_type": type(exc).__name__,
        }
    return {
        "status": "fail" if symlinks else "pass",
        "reason": "repository_symlink_rejected" if symlinks else "repository_tree_safe",
        "entry_count": entry_count,
        "symlink_paths": symlinks[:20],
    }


def run_restricted_process(
    command: Sequence[str],
    *,
    cwd: str | Path | None = None,
    capture_output: bool = True,
    text: bool = True,
    timeout: float | None = None,
    check: bool = False,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess:
    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "text": text,
        "env": dict(env or {}),
    }
    if capture_output:
        popen_kwargs["stdout"] = subprocess.PIPE
        popen_kwargs["stderr"] = subprocess.PIPE
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(list(command), **popen_kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            list(command),
            timeout,
            output=stdout if stdout is not None else exc.output,
            stderr=stderr if stderr is not None else exc.stderr,
        ) from None
    completed = subprocess.CompletedProcess(
        list(command),
        process.returncode,
        stdout,
        stderr,
    )
    if check:
        completed.check_returncode()
    return completed


def _terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        taskkill = system_root / "System32" / "taskkill.exe"
        if taskkill.is_file():
            subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
            )
        if process.poll() is None:
            process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        process.kill()
