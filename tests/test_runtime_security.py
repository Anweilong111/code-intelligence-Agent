from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from code_intelligence_agent.evaluation.repository_test_execution_result import (
    execute_repository_test_plan,
)
from code_intelligence_agent.tools.runtime_security import (
    audit_repository_tree,
    build_restricted_environment,
    run_restricted_process,
)


def test_restricted_environment_removes_host_and_override_secrets(tmp_path):
    canary = "canary-secret-value"
    env, audit = build_restricted_environment(
        base={
            "PATH": os.environ.get("PATH", ""),
            "CIA_LLM_API_KEY": canary,
            "UNRELATED_API_TOKEN": canary,
            "LANG": "C",
        },
        overrides={
            "PYTHONPATH": str(tmp_path),
            "PROJECT_MODE": "test",
            "SECOND_SECRET": canary,
        },
        sandbox_home=tmp_path / "home",
    )

    serialized = json.dumps(env, sort_keys=True)
    assert canary not in serialized
    assert "CIA_LLM_API_KEY" not in env
    assert "UNRELATED_API_TOKEN" not in env
    assert "SECOND_SECRET" not in env
    assert env["PROJECT_MODE"] == "test"
    assert env["LANG"] == "C"
    assert audit["blocked_sensitive_variable_count"] == 2
    assert audit["rejected_override_names"] == ["SECOND_SECRET"]
    assert audit["network_enforcement"] == (
        "python_external_socket_guard_loopback_allowed"
    )


def test_repository_python_process_cannot_read_secret_or_open_network(tmp_path):
    canary = "canary-secret-value"
    env, _ = build_restricted_environment(
        base={"PATH": os.environ.get("PATH", ""), "CIA_LLM_API_KEY": canary},
        sandbox_home=tmp_path,
    )
    secret_probe = run_restricted_process(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ.get('CIA_LLM_API_KEY', 'ABSENT'))",
        ],
        env=env,
        timeout=5,
    )
    network_probe = run_restricted_process(
        [
            sys.executable,
            "-c",
            (
                "import socket; "
                "socket.create_connection(('example.com', 80), timeout=1)"
            ),
        ],
        env=env,
        timeout=5,
    )

    assert secret_probe.returncode == 0
    assert secret_probe.stdout.strip() == "ABSENT"
    assert network_probe.returncode != 0
    assert "CIA runtime policy blocks repository network access" in (
        network_probe.stderr or ""
    )


def test_restricted_runner_terminates_infinite_python_process(tmp_path):
    env, _ = build_restricted_environment(sandbox_home=tmp_path)

    with pytest.raises(subprocess.TimeoutExpired):
        run_restricted_process(
            [sys.executable, "-c", "while True: pass"],
            env=env,
            timeout=0.25,
        )


def test_repository_tree_audit_and_test_executor_reject_symlink(tmp_path):
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    link = repo / "linked.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("This Windows environment cannot create symlinks.")

    audit = audit_repository_tree(repo)
    result = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q",
            "recommended_execution_level": "targeted",
            "recommended_execution_risk": "low",
            "recommended_execution_scope": "hostile_fixture",
            "executable_now": True,
        },
        repository_root=repo,
    )

    assert audit["status"] == "fail"
    assert audit["reason"] == "repository_symlink_rejected"
    assert audit["symlink_paths"] == ["linked.txt"]
    assert result["status"] == "skipped"
    assert result["reason"] == "unsafe_repository_tree"
    assert result["executed"] is False
