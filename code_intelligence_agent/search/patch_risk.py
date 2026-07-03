from __future__ import annotations

import ast
import difflib
import textwrap
from dataclasses import asdict, dataclass
from pathlib import Path

from code_intelligence_agent.core.program_graph import ProgramGraph
from code_intelligence_agent.core.models import PatchCandidate
from code_intelligence_agent.search.scoring import diff_size


@dataclass(frozen=True)
class PatchRisk:
    score: float
    diff_size: int
    affected_callers: int
    cross_file_callers: int
    target_file_changes: int
    data_dependency_fanout: int
    changed_variables: list[str]
    return_or_control_changed: bool
    signature_changed: bool
    risk_reasons: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


class PatchRiskAnalyzer:
    def analyze(
        self,
        candidate: PatchCandidate,
        program_graph: ProgramGraph | None = None,
    ) -> PatchRisk:
        size = diff_size(candidate.diff)
        target_file_changes = _changed_file_count(candidate.diff)
        affected_callers = 0
        cross_file_callers = 0
        data_dependency_fanout = 0
        change_summary = _analyze_patch_change(candidate)
        if program_graph is not None:
            callers = _callers(candidate.target_function_id, program_graph)
            affected_callers = len(callers)
            target_file = Path(candidate.target_file).resolve()
            cross_file_callers = sum(
                1
                for caller_id in callers
                if Path(program_graph.functions[caller_id].file_path).resolve()
                != target_file
            )
            data_dependency_fanout = _data_dependency_fanout(
                candidate=candidate,
                program_graph=program_graph,
                changed_variables=change_summary.changed_variables,
            )

        risk_score = min(
            1.0,
            0.40 * min(1.0, size / 20)
            + 0.35 * min(1.0, affected_callers / 5)
            + 0.15 * min(1.0, cross_file_callers / 3)
            + 0.10 * min(1.0, target_file_changes / 2)
            + 0.12 * min(1.0, data_dependency_fanout / 4)
            + (0.08 if change_summary.return_or_control_changed else 0.0)
            + (0.05 if change_summary.signature_changed else 0.0),
        )
        reasons = []
        if size:
            reasons.append(f"diff_size={size}")
        if affected_callers:
            reasons.append(f"affected_callers={affected_callers}")
        if cross_file_callers:
            reasons.append(f"cross_file_callers={cross_file_callers}")
        if target_file_changes > 1:
            reasons.append(f"target_file_changes={target_file_changes}")
        if data_dependency_fanout:
            reasons.append(f"data_dependency_fanout={data_dependency_fanout}")
        if change_summary.return_or_control_changed:
            reasons.append("return_or_control_changed")
        if change_summary.signature_changed:
            reasons.append("signature_changed")
        return PatchRisk(
            score=round(risk_score, 4),
            diff_size=size,
            affected_callers=affected_callers,
            cross_file_callers=cross_file_callers,
            target_file_changes=target_file_changes,
            data_dependency_fanout=data_dependency_fanout,
            changed_variables=sorted(change_summary.changed_variables),
            return_or_control_changed=change_summary.return_or_control_changed,
            signature_changed=change_summary.signature_changed,
            risk_reasons=reasons,
        )


def annotate_patch_risk(
    candidate: PatchCandidate,
    risk: PatchRisk,
) -> PatchCandidate:
    metadata = {
        **candidate.metadata,
        "risk": risk.to_dict(),
    }
    return PatchCandidate(
        id=candidate.id,
        target_file=candidate.target_file,
        relative_file_path=candidate.relative_file_path,
        target_function_id=candidate.target_function_id,
        target_function_name=candidate.target_function_name,
        rule_id=candidate.rule_id,
        description=candidate.description,
        old_source=candidate.old_source,
        new_source=candidate.new_source,
        diff=candidate.diff,
        metadata=metadata,
    )


def _callers(function_id: str, program_graph: ProgramGraph) -> set[str]:
    callers = set()
    for edge in program_graph.edges:
        if edge["type"] != "calls" or edge["target"] != function_id:
            continue
        source = edge["source"]
        function = program_graph.functions.get(source)
        if function is None:
            continue
        if function.metadata.get("is_test") or function.metadata.get("is_test_file"):
            continue
        callers.add(source)
    return callers


def _changed_file_count(diff: str) -> int:
    files = set()
    for line in diff.splitlines():
        if line.startswith(("--- a/", "+++ b/")):
            files.add(line[6:])
    return len(files)


@dataclass(frozen=True)
class _PatchChangeSummary:
    changed_variables: set[str]
    return_or_control_changed: bool
    signature_changed: bool


def _analyze_patch_change(candidate: PatchCandidate) -> _PatchChangeSummary:
    old_lines, new_lines = _changed_line_numbers(
        candidate.old_source,
        candidate.new_source,
    )
    old_view = _ASTChangeView(candidate.old_source)
    new_view = _ASTChangeView(candidate.new_source)
    changed_variables = old_view.names_on_lines(old_lines).union(
        new_view.names_on_lines(new_lines)
    )
    return _PatchChangeSummary(
        changed_variables=changed_variables,
        return_or_control_changed=(
            old_view.has_return_or_control_on_lines(old_lines)
            or new_view.has_return_or_control_on_lines(new_lines)
        ),
        signature_changed=old_view.signature != new_view.signature,
    )


def _changed_line_numbers(old_source: str, new_source: str) -> tuple[set[int], set[int]]:
    old_lines = old_source.splitlines()
    new_lines = new_source.splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    changed_old: set[int] = set()
    changed_new: set[int] = set()
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        changed_old.update(range(old_start + 1, old_end + 1))
        changed_new.update(range(new_start + 1, new_end + 1))
    return changed_old, changed_new


class _ASTChangeView:
    def __init__(self, source: str) -> None:
        self.function: ast.FunctionDef | ast.AsyncFunctionDef | None = None
        self.signature = ""
        self._names_by_line: dict[int, set[str]] = {}
        self._return_control_lines: set[int] = set()
        try:
            tree = ast.parse(textwrap.dedent(source))
        except SyntaxError:
            return
        root = tree.body[0] if tree.body else None
        if not isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return
        self.function = root
        self.signature = _signature_dump(root)
        for node in ast.walk(root):
            self._record_name(node)
            if _is_return_or_control(node):
                self._return_control_lines.update(_line_span(node))

    def names_on_lines(self, lines: set[int]) -> set[str]:
        names: set[str] = set()
        for line in lines:
            names.update(self._names_by_line.get(line, set()))
        return names

    def has_return_or_control_on_lines(self, lines: set[int]) -> bool:
        return bool(lines & self._return_control_lines)

    def _record_name(self, node: ast.AST) -> None:
        name = ""
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.arg):
            name = node.arg
        if not name:
            return
        for line in _line_span(node):
            self._names_by_line.setdefault(line, set()).add(name)


def _data_dependency_fanout(
    candidate: PatchCandidate,
    program_graph: ProgramGraph,
    changed_variables: set[str],
) -> int:
    if not changed_variables:
        return 0
    adjacency: dict[str, set[str]] = {}
    touched: set[str] = set()
    touched_key_flow_edges = 0
    for edge in program_graph.edges:
        if edge.get("function_id") != candidate.target_function_id:
            continue
        edge_type = edge["type"]
        if edge_type == "data_depends_on":
            source = str(edge.get("source_variable", ""))
            target = str(edge.get("target_variable", ""))
        elif edge_type == "key_flows_to_subscript":
            source = str(edge.get("key_variable", ""))
            target = str(edge.get("mapping_variable", ""))
        else:
            continue
        if not source or not target:
            continue
        adjacency.setdefault(source, set()).add(target)
        if source in changed_variables or target in changed_variables:
            touched.update({source, target})
            if edge_type == "key_flows_to_subscript":
                touched_key_flow_edges += 1

    reachable: set[str] = set()
    frontier = list(changed_variables)
    seen = set(frontier)
    while frontier:
        current = frontier.pop(0)
        for target in adjacency.get(current, set()):
            if target in seen:
                continue
            seen.add(target)
            reachable.add(target)
            frontier.append(target)
    return len((reachable | touched) - changed_variables) + touched_key_flow_edges


def _line_span(node: ast.AST) -> range:
    start = int(getattr(node, "lineno", 0) or 0)
    end = int(getattr(node, "end_lineno", start) or start)
    if start <= 0:
        return range(0)
    return range(start, end + 1)


def _is_return_or_control(node: ast.AST) -> bool:
    return isinstance(
        node,
        (
            ast.Return,
            ast.Raise,
            ast.If,
            ast.For,
            ast.AsyncFor,
            ast.While,
            ast.Try,
            ast.ExceptHandler,
            ast.Match,
            ast.With,
            ast.AsyncWith,
        ),
    )


def _signature_dump(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return ast.dump(
        ast.Tuple(
            elts=[
                ast.Constant(value=node.name),
                node.args,
                node.returns or ast.Constant(value=None),
                ast.Constant(value=node.type_comment),
            ],
            ctx=ast.Load(),
        ),
        include_attributes=False,
    )
