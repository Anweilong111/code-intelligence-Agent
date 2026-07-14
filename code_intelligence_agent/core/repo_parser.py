from __future__ import annotations

from pathlib import Path

from code_intelligence_agent.core.ast_analyzer import ASTAnalyzer
from code_intelligence_agent.core.models import RepoParseResult


ALWAYS_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".source_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
    "site-packages",
}

ROOT_ONLY_EXCLUDED_DIRS = {
    "build",
    "dist",
    "env",
    "venv",
}

DEFAULT_EXCLUDED_DIRS = ALWAYS_EXCLUDED_DIRS | ROOT_ONLY_EXCLUDED_DIRS


def is_default_excluded_repo_path(parts: tuple[str, ...] | list[str]) -> bool:
    normalized = tuple(str(part).lower() for part in parts if str(part))
    if any(part in ALWAYS_EXCLUDED_DIRS for part in normalized):
        return True
    return bool(normalized and normalized[0] in ROOT_ONLY_EXCLUDED_DIRS)


class RepoParser:
    def __init__(
        self,
        analyzer: ASTAnalyzer | None = None,
        excluded_dirs: set[str] | None = None,
    ) -> None:
        self.analyzer = analyzer or ASTAnalyzer()
        self._uses_default_exclusions = excluded_dirs is None
        self.excluded_dirs = (
            set(DEFAULT_EXCLUDED_DIRS) if excluded_dirs is None else set(excluded_dirs)
        )

    def parse(self, path: str | Path) -> RepoParseResult:
        root = Path(path)
        if root.is_file():
            return RepoParseResult(
                root_path=str(root.parent),
                files=[self._parse_file(root)],
            )
        if not root.exists():
            raise FileNotFoundError(f"Path does not exist: {root}")
        files = []
        for file_path in self._iter_python_files(root):
            try:
                files.append(self._parse_file(file_path))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
        return RepoParseResult(root_path=str(root), files=files)

    def _iter_python_files(self, root: Path) -> list[Path]:
        candidates = []
        for file_path in root.rglob("*.py"):
            relative_parts = file_path.relative_to(root).parts
            if self._uses_default_exclusions and is_default_excluded_repo_path(
                relative_parts
            ):
                continue
            if not self._uses_default_exclusions and any(
                part in self.excluded_dirs for part in relative_parts
            ):
                continue
            candidates.append(file_path)
        return sorted(candidates)

    def _parse_file(self, file_path: Path):
        source = file_path.read_text(encoding="utf-8")
        return self.analyzer.analyze_file(file_path=file_path, source=source)


def repo_parser(path: str | Path) -> RepoParseResult:
    return RepoParser().parse(path)
