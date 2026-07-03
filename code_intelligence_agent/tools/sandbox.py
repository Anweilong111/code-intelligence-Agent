from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.core.models import ExecutionResult, PatchCandidate
from code_intelligence_agent.tools.diff_utils import (
    apply_patch_candidate,
    apply_patch_candidates,
)

_PYTEST_BOOTSTRAP = Path(__file__).with_name("pytest_bootstrap.py")


class Sandbox:
    def __init__(self, timeout: int = 5) -> None:
        self.timeout = timeout

    def run_tests(
        self,
        repo_path: str | Path,
        test_args: list[str] | None = None,
    ) -> ExecutionResult:
        args = test_args or []
        repo = Path(repo_path).resolve()
        command = [sys.executable, str(_PYTEST_BOOTSTRAP), str(repo), "-q", *args]
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            completed = subprocess.run(
                command,
                cwd=tempfile.gettempdir(),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
                env=env,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            return ExecutionResult(
                success=completed.returncode == 0,
                returncode=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                traceback=_extract_traceback(stdout + "\n" + stderr),
                passed=_extract_count(stdout, "passed"),
                failed=_extract_count(stdout, "failed"),
                timeout=False,
                command=command,
            )
        except subprocess.TimeoutExpired as exc:
            return ExecutionResult(
                success=False,
                returncode=-1,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                traceback="",
                passed=0,
                failed=0,
                timeout=True,
                command=command,
            )

    def apply_patch_and_test(
        self,
        repo_path: str | Path,
        candidate: PatchCandidate,
        test_args: list[str] | None = None,
    ) -> ExecutionResult:
        source_repo = Path(repo_path)
        with tempfile.TemporaryDirectory(prefix="cia_sandbox_") as tmp_dir:
            sandbox_repo = Path(tmp_dir) / "repo"
            _copy_repo(source_repo, sandbox_repo)
            try:
                apply_patch_candidate(sandbox_repo, candidate)
            except (FileNotFoundError, ValueError) as exc:
                return _patch_apply_error(str(exc))
            return self.run_tests(sandbox_repo, test_args=test_args)

    def apply_patches_and_test(
        self,
        repo_path: str | Path,
        candidates: list[PatchCandidate],
        test_args: list[str] | None = None,
    ) -> ExecutionResult:
        source_repo = Path(repo_path)
        with tempfile.TemporaryDirectory(prefix="cia_sandbox_") as tmp_dir:
            sandbox_repo = Path(tmp_dir) / "repo"
            _copy_repo(source_repo, sandbox_repo)
            try:
                apply_patch_candidates(sandbox_repo, candidates)
            except (FileNotFoundError, ValueError) as exc:
                return _patch_apply_error(str(exc))
            return self.run_tests(sandbox_repo, test_args=test_args)


def _copy_repo(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )


def _patch_apply_error(message: str) -> ExecutionResult:
    return ExecutionResult(
        success=False,
        returncode=-1,
        stdout="",
        stderr=message,
        traceback="",
        passed=0,
        failed=0,
        timeout=False,
        command=[],
    )


def _extract_count(output: str, label: str) -> int:
    match = re.search(rf"(\d+)\s+{label}", output)
    return int(match.group(1)) if match else 0


def _extract_traceback(output: str) -> str:
    marker = "Traceback (most recent call last):"
    if marker not in output:
        return ""
    return output[output.index(marker) :]
