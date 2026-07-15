from __future__ import annotations

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
from code_intelligence_agent.tools.runtime_security import (
    audit_repository_tree,
    build_restricted_environment,
    run_restricted_process,
)

_PYTEST_BOOTSTRAP = Path(__file__).with_name("pytest_bootstrap.py")
_IMPORT_VALIDATION_BOOTSTRAP = Path(__file__).with_name(
    "import_validation_bootstrap.py"
)


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
        tree_audit = audit_repository_tree(repo)
        if tree_audit["status"] != "pass":
            return _security_error(str(tree_audit["reason"]))
        command = [sys.executable, str(_PYTEST_BOOTSTRAP), str(repo), "-q", *args]
        try:
            with tempfile.TemporaryDirectory(prefix="cia_runtime_home_") as home:
                env, _ = build_restricted_environment(sandbox_home=home)
                completed = run_restricted_process(
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
            try:
                _copy_repo(source_repo, sandbox_repo)
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
            try:
                _copy_repo(source_repo, sandbox_repo)
                apply_patch_candidates(sandbox_repo, candidates)
            except (FileNotFoundError, ValueError) as exc:
                return _patch_apply_error(str(exc))
            return self.run_tests(sandbox_repo, test_args=test_args)

    def validate_candidate_imports(
        self,
        repo_path: str | Path,
        candidate: PatchCandidate,
        *,
        apply_patch: bool,
    ) -> ExecutionResult:
        source_repo = Path(repo_path)
        with tempfile.TemporaryDirectory(prefix="cia_import_validation_") as tmp_dir:
            sandbox_repo = Path(tmp_dir) / "repo"
            try:
                _copy_repo(source_repo, sandbox_repo)
                if apply_patch:
                    apply_patch_candidate(sandbox_repo, candidate)
            except (FileNotFoundError, ValueError) as exc:
                return _patch_apply_error(str(exc))
            command = [
                sys.executable,
                str(_IMPORT_VALIDATION_BOOTSTRAP),
                str(sandbox_repo),
                candidate.relative_file_path,
            ]
            try:
                with tempfile.TemporaryDirectory(prefix="cia_runtime_home_") as home:
                    env, _ = build_restricted_environment(sandbox_home=home)
                    completed = run_restricted_process(
                        command,
                        cwd=tempfile.gettempdir(),
                        capture_output=True,
                        text=True,
                        timeout=self.timeout,
                        check=False,
                        env=env,
                    )
                return ExecutionResult(
                    success=completed.returncode == 0,
                    returncode=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    traceback=_extract_traceback(
                        completed.stdout + "\n" + completed.stderr
                    ),
                    passed=0,
                    failed=0,
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


def _copy_repo(source: Path, target: Path) -> None:
    audit = audit_repository_tree(source)
    if audit["status"] != "pass":
        raise ValueError(str(audit["reason"]))
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


def _security_error(reason: str) -> ExecutionResult:
    return ExecutionResult(
        success=False,
        returncode=-1,
        stdout="",
        stderr=reason,
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
