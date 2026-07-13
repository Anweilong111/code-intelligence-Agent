from __future__ import annotations

import math
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from code_intelligence_agent.core.models import CodeEntity


@dataclass(frozen=True)
class GitChangeHistoryResult:
    status: str
    reason: str
    repository_root: str = ""
    commit: str = ""
    scores: dict[str, float] = field(default_factory=dict)
    function_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    analyzed_file_count: int = 0
    skipped_file_count: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GitChangeHistoryAnalyzer:
    """Derive bounded, function-level change evidence from the current Git ref."""

    def __init__(
        self,
        *,
        max_files: int = 200,
        timeout_seconds: float = 5.0,
        recency_half_life_days: float = 180.0,
    ) -> None:
        self.max_files = max(1, max_files)
        self.timeout_seconds = max(0.1, timeout_seconds)
        self.recency_half_life_days = max(1.0, recency_half_life_days)

    def analyze(
        self,
        repository_root: str | Path,
        functions: list[CodeEntity],
        *,
        now_timestamp: float | None = None,
    ) -> GitChangeHistoryResult:
        if shutil.which("git") is None:
            return GitChangeHistoryResult(
                status="skipped",
                reason="git_executable_unavailable",
            )
        requested_root = Path(repository_root).resolve()
        root_result = self._git(requested_root, "rev-parse", "--show-toplevel")
        if root_result.returncode != 0:
            return GitChangeHistoryResult(
                status="skipped",
                reason="not_a_git_repository",
                repository_root=str(requested_root),
                errors=[_command_error(root_result)],
                error_count=1,
            )
        git_root = Path(root_result.stdout.strip()).resolve()
        commit_result = self._git(git_root, "rev-parse", "HEAD")
        commit = commit_result.stdout.strip() if commit_result.returncode == 0 else ""
        functions_by_file = _functions_by_relative_file(git_root, functions)
        selected_files = sorted(functions_by_file)[: self.max_files]
        skipped_file_count = max(0, len(functions_by_file) - len(selected_files))
        line_history: dict[str, dict[int, tuple[str, int]]] = {}
        errors: list[str] = []
        for relative_path in selected_files:
            blame = self._git(
                git_root,
                "blame",
                "--line-porcelain",
                commit or "HEAD",
                "--",
                relative_path,
            )
            if blame.returncode != 0:
                errors.append(f"{relative_path}: {_command_error(blame)}")
                continue
            parsed = _parse_line_porcelain(blame.stdout)
            if parsed:
                line_history[relative_path] = parsed

        raw_evidence = _raw_function_evidence(
            functions_by_file=functions_by_file,
            line_history=line_history,
            now_timestamp=now_timestamp if now_timestamp is not None else time.time(),
            recency_half_life_days=self.recency_half_life_days,
        )
        scores, normalized_evidence = _normalize_function_evidence(raw_evidence)
        if not scores:
            return GitChangeHistoryResult(
                status="warning",
                reason="git_history_evidence_unavailable",
                repository_root=str(git_root),
                commit=commit,
                analyzed_file_count=len(line_history),
                skipped_file_count=skipped_file_count,
                error_count=len(errors),
                errors=errors[:20],
            )
        status = "partial" if errors or skipped_file_count else "available"
        reason = (
            "git_history_partially_analyzed"
            if status == "partial"
            else "git_history_analyzed"
        )
        return GitChangeHistoryResult(
            status=status,
            reason=reason,
            repository_root=str(git_root),
            commit=commit,
            scores=scores,
            function_evidence=normalized_evidence,
            analyzed_file_count=len(line_history),
            skipped_file_count=skipped_file_count,
            error_count=len(errors),
            errors=errors[:20],
        )

    def _git(self, root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(root), *arguments],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(
                args=["git", "-C", str(root), *arguments],
                returncode=124,
                stdout="",
                stderr=str(exc),
            )


def _functions_by_relative_file(
    git_root: Path,
    functions: list[CodeEntity],
) -> dict[str, list[CodeEntity]]:
    grouped: dict[str, list[CodeEntity]] = {}
    for function in functions:
        try:
            relative = Path(function.file_path).resolve().relative_to(git_root)
        except (OSError, ValueError):
            continue
        grouped.setdefault(relative.as_posix(), []).append(function)
    return grouped


def _parse_line_porcelain(output: str) -> dict[int, tuple[str, int]]:
    history: dict[int, tuple[str, int]] = {}
    commit = ""
    final_line = 0
    author_time = 0
    header = re.compile(r"^(\^?[0-9a-fA-F]{7,64})\s+\d+\s+(\d+)(?:\s+\d+)?$")
    for line in output.splitlines():
        match = header.match(line)
        if match:
            commit = match.group(1).lstrip("^")
            final_line = int(match.group(2))
            author_time = 0
            continue
        if line.startswith("author-time "):
            try:
                author_time = int(line.split(" ", 1)[1])
            except (IndexError, ValueError):
                author_time = 0
            continue
        if line.startswith("\t") and commit and final_line > 0:
            history[final_line] = (commit, author_time)
            final_line += 1
    return history


def _raw_function_evidence(
    *,
    functions_by_file: dict[str, list[CodeEntity]],
    line_history: dict[str, dict[int, tuple[str, int]]],
    now_timestamp: float,
    recency_half_life_days: float,
) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for relative_path, functions in functions_by_file.items():
        file_history = line_history.get(relative_path, {})
        if not file_history:
            continue
        for function in functions:
            entries = [
                file_history[line]
                for line in range(function.start_line, function.end_line + 1)
                if line in file_history
            ]
            if not entries:
                continue
            commits = {commit for commit, _ in entries if commit}
            timestamps = [timestamp for _, timestamp in entries if timestamp > 0]
            latest_timestamp = max(timestamps, default=0)
            age_days = (
                max(0.0, now_timestamp - latest_timestamp) / 86_400.0
                if latest_timestamp
                else float("inf")
            )
            recency = (
                math.exp(-math.log(2.0) * age_days / recency_half_life_days)
                if math.isfinite(age_days)
                else 0.0
            )
            line_count = max(1, function.end_line - function.start_line + 1)
            evidence[function.id] = {
                "file_path": relative_path,
                "start_line": function.start_line,
                "end_line": function.end_line,
                "line_count": line_count,
                "blamed_line_count": len(entries),
                "unique_last_change_commits": len(commits),
                "commit_density": len(commits) / line_count,
                "latest_author_time": latest_timestamp,
                "age_days": round(age_days, 4) if math.isfinite(age_days) else None,
                "recency_component": recency,
            }
    return evidence


def _normalize_function_evidence(
    raw_evidence: dict[str, dict[str, Any]],
) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    max_commit_log = max(
        (
            math.log1p(int(item["unique_last_change_commits"]))
            for item in raw_evidence.values()
        ),
        default=0.0,
    )
    max_density = max(
        (float(item["commit_density"]) for item in raw_evidence.values()),
        default=0.0,
    )
    scores: dict[str, float] = {}
    normalized: dict[str, dict[str, Any]] = {}
    for function_id, item in raw_evidence.items():
        commit_component = (
            math.log1p(int(item["unique_last_change_commits"])) / max_commit_log
            if max_commit_log
            else 0.0
        )
        density_component = (
            float(item["commit_density"]) / max_density if max_density else 0.0
        )
        recency_component = float(item["recency_component"])
        score = _clamp(
            0.50 * commit_component
            + 0.30 * density_component
            + 0.20 * recency_component
        )
        scores[function_id] = round(score, 6)
        normalized[function_id] = {
            **item,
            "commit_component": round(commit_component, 6),
            "density_component": round(density_component, 6),
            "recency_component": round(recency_component, 6),
            "score": round(score, 6),
        }
    return scores, normalized


def _command_error(result: subprocess.CompletedProcess[str]) -> str:
    message = (result.stderr or result.stdout or "git command failed").strip()
    return f"returncode={result.returncode}; {message[:500]}"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
