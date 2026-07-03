from __future__ import annotations

from pathlib import Path

from code_intelligence_agent.core.ast_analyzer import ASTAnalyzer
from code_intelligence_agent.core.models import RepoParseResult


DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".source_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


class RepoParser:
    def __init__(
        self,
        analyzer: ASTAnalyzer | None = None,
        excluded_dirs: set[str] | None = None,
    ) -> None:
        self.analyzer = analyzer or ASTAnalyzer()
        self.excluded_dirs = excluded_dirs or DEFAULT_EXCLUDED_DIRS

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
            if any(part in self.excluded_dirs for part in file_path.parts):
                continue
            candidates.append(file_path)
        return sorted(candidates)

    def _parse_file(self, file_path: Path):
        source = file_path.read_text(encoding="utf-8")
        return self.analyzer.analyze_file(file_path=file_path, source=source)


def repo_parser(path: str | Path) -> RepoParseResult:
    return RepoParser().parse(path)
