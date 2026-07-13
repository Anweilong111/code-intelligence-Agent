from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class CodeEntity:
    id: str
    type: str
    name: str
    file_path: str
    start_line: int
    end_line: int
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ImportInfo:
    module: str | None
    names: list[str]
    level: int
    file_path: str
    line: int
    aliases: dict[str, str] = field(default_factory=dict)
    kind: str = "static"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CallSite:
    caller_id: str
    caller_name: str
    callee: str
    file_path: str
    line: int
    col: int
    arg_names: list[list[str]] = field(default_factory=list)
    assigned_to: list[str] = field(default_factory=list)
    is_awaited: bool = False
    async_kind: str = ""
    local_symbol_alias: str = ""
    local_symbol_alias_source: str = ""
    local_symbol_alias_callee: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FileAnalysis:
    file_path: str
    source: str
    functions: list[CodeEntity] = field(default_factory=list)
    classes: list[CodeEntity] = field(default_factory=list)
    imports: list[ImportInfo] = field(default_factory=list)
    calls: list[CallSite] = field(default_factory=list)
    chunks: list[CodeEntity] = field(default_factory=list)

    def tests(self) -> list[CodeEntity]:
        return [entity for entity in self.functions if entity.metadata.get("is_test")]


@dataclass
class RepoParseResult:
    root_path: str
    files: list[FileAnalysis] = field(default_factory=list)

    @property
    def functions(self) -> list[CodeEntity]:
        return [entity for file in self.files for entity in file.functions]

    @property
    def classes(self) -> list[CodeEntity]:
        return [entity for file in self.files for entity in file.classes]

    @property
    def tests(self) -> list[CodeEntity]:
        return [entity for file in self.files for entity in file.tests()]

    @property
    def imports(self) -> list[ImportInfo]:
        return [item for file in self.files for item in file.imports]

    @property
    def calls(self) -> list[CallSite]:
        return [call for file in self.files for call in file.calls]

    @property
    def chunks(self) -> list[CodeEntity]:
        return [chunk for file in self.files for chunk in file.chunks]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_path": self.root_path,
            "files": [
                {
                    "file_path": file.file_path,
                    "functions": [entity.to_dict() for entity in file.functions],
                    "classes": [entity.to_dict() for entity in file.classes],
                    "imports": [item.to_dict() for item in file.imports],
                    "calls": [call.to_dict() for call in file.calls],
                    "chunks": [chunk.to_dict() for chunk in file.chunks],
                }
                for file in self.files
            ],
            "functions": [entity.to_dict() for entity in self.functions],
            "classes": [entity.to_dict() for entity in self.classes],
            "tests": [entity.to_dict() for entity in self.tests],
            "imports": [item.to_dict() for item in self.imports],
            "calls": [call.to_dict() for call in self.calls],
            "chunks": [chunk.to_dict() for chunk in self.chunks],
        }


@dataclass(frozen=True)
class BugFinding:
    rule_id: str
    bug_type: str
    message: str
    file_path: str
    function_id: str
    function_name: str
    line: int
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SuspiciousFunction:
    function_id: str
    function_name: str
    file_path: str
    start_line: int
    end_line: int
    static_rule_score: float
    graph_score: float
    final_score: float
    findings: list[BugFinding]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["findings"] = [finding.to_dict() for finding in self.findings]
        return data


@dataclass(frozen=True)
class FaultLocalizationResult:
    function_id: str
    function_name: str
    file_path: str
    start_line: int
    end_line: int
    score: float
    rank: int
    signals: dict[str, float]
    findings: list[BugFinding]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["findings"] = [finding.to_dict() for finding in self.findings]
        return data


@dataclass(frozen=True)
class TestExecutionSummary:
    __test__ = False

    failed_tests: set[str] = field(default_factory=set)
    passed_tests: set[str] = field(default_factory=set)
    coverage: dict[str, set[str]] = field(default_factory=dict)
    line_coverage: dict[str, dict[str, float]] = field(default_factory=dict)
    covered_lines: dict[str, dict[str, set[int]]] = field(default_factory=dict)
    branch_coverage: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    path_coverage: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    traceback_function_ids: set[str] = field(default_factory=set)
    dynamic_traceback_function_ids: set[str] = field(default_factory=set)
    test_names: dict[str, str] = field(default_factory=dict)
    failure_messages: dict[str, str] = field(default_factory=dict)
    dynamic_evidence_test_ids: set[str] = field(default_factory=set)
    dynamic_evidence_nodeids: dict[str, str] = field(default_factory=dict)
    dynamic_evidence_unmatched_nodeids: set[str] = field(default_factory=set)

    def has_coverage(self) -> bool:
        return bool(self.failed_tests or self.passed_tests or self.coverage)


@dataclass(frozen=True)
class PatchCandidate:
    id: str
    target_file: str
    relative_file_path: str
    target_function_id: str
    target_function_name: str
    rule_id: str
    description: str
    old_source: str
    new_source: str
    diff: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    returncode: int
    stdout: str
    stderr: str
    traceback: str
    passed: int
    failed: int
    timeout: bool
    command: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
