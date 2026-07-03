from __future__ import annotations

import ast
import textwrap
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from code_intelligence_agent.core.call_graph import CallGraph, build_call_graph
from code_intelligence_agent.core.models import CodeEntity, RepoParseResult


@dataclass
class ProgramGraph:
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[dict[str, Any]] = field(default_factory=list)
    functions: dict[str, CodeEntity] = field(default_factory=dict)

    def add_node(self, node_id: str, node_type: str, **attrs: Any) -> None:
        self.nodes[node_id] = {"id": node_id, "type": node_type, **attrs}

    def add_edge(
        self,
        source: str,
        target: str,
        edge_type: str,
        weight: float = 1.0,
        **attrs: Any,
    ) -> None:
        self.edges.append(
            {
                "source": source,
                "target": target,
                "type": edge_type,
                "weight": weight,
                **attrs,
            }
        )

    def degree(self, node_id: str, edge_types: set[str] | None = None) -> int:
        return sum(
            1
            for edge in self.edges
            if (edge_types is None or edge["type"] in edge_types)
            and (edge["source"] == node_id or edge["target"] == node_id)
        )

    def in_degree(self, node_id: str, edge_types: set[str] | None = None) -> int:
        return sum(
            1
            for edge in self.edges
            if (edge_types is None or edge["type"] in edge_types)
            and edge["target"] == node_id
        )

    def out_degree(self, node_id: str, edge_types: set[str] | None = None) -> int:
        return sum(
            1
            for edge in self.edges
            if (edge_types is None or edge["type"] in edge_types)
            and edge["source"] == node_id
        )

    def shortest_path_distance(
        self,
        source: str,
        target: str,
        edge_types: set[str] | None = None,
        max_depth: int = 8,
    ) -> int | None:
        path = self.shortest_path(
            source=source,
            target=target,
            edge_types=edge_types,
            max_depth=max_depth,
        )
        if path is None:
            return None
        return len(path) - 1

    def shortest_path(
        self,
        source: str,
        target: str,
        edge_types: set[str] | None = None,
        max_depth: int = 8,
    ) -> list[str] | None:
        if source == target:
            return [source]
        adjacency: dict[str, list[str]] = defaultdict(list)
        for edge in self.edges:
            if edge_types is not None and edge["type"] not in edge_types:
                continue
            adjacency[edge["source"]].append(edge["target"])
        queue = deque([(source, [source])])
        seen = {source}
        while queue:
            node_id, path = queue.popleft()
            distance = len(path) - 1
            if distance >= max_depth:
                continue
            for next_id in adjacency.get(node_id, []):
                if next_id == target:
                    return [*path, next_id]
                if next_id not in seen:
                    seen.add(next_id)
                    queue.append((next_id, [*path, next_id]))
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": list(self.nodes.values()),
            "edges": self.edges,
        }


class ProgramGraphBuilder:
    def build(
        self, parsed: RepoParseResult, call_graph: CallGraph | None = None
    ) -> ProgramGraph:
        call_graph = call_graph or build_call_graph(
            parsed.functions,
            parsed.calls,
            parsed.imports,
        )
        graph = ProgramGraph(
            functions={function.id: function for function in parsed.functions}
        )
        self._add_file_nodes(parsed, graph)
        self._add_class_nodes(parsed, graph)
        self._add_function_nodes(parsed, graph)
        self._add_import_nodes(parsed, graph)
        self._add_call_edges(call_graph, graph)
        self._add_test_edges(parsed, call_graph, graph)
        self._add_variable_edges(parsed, graph)
        self._add_cross_function_data_flow_edges(call_graph, graph)
        self._add_control_flow_edges(parsed, graph)
        self._add_cfg_edges(parsed, graph)
        return graph

    def _add_file_nodes(self, parsed: RepoParseResult, graph: ProgramGraph) -> None:
        for file in parsed.files:
            file_id = _file_node_id(file.file_path)
            graph.add_node(file_id, "file", file_path=Path(file.file_path).as_posix())

    def _add_class_nodes(self, parsed: RepoParseResult, graph: ProgramGraph) -> None:
        for class_entity in parsed.classes:
            graph.add_node(
                class_entity.id,
                "class",
                name=class_entity.name,
                file_path=Path(class_entity.file_path).as_posix(),
                start_line=class_entity.start_line,
                end_line=class_entity.end_line,
            )
            graph.add_edge(
                _file_node_id(class_entity.file_path),
                class_entity.id,
                "contains",
            )

    def _add_function_nodes(self, parsed: RepoParseResult, graph: ProgramGraph) -> None:
        class_by_name = {
            class_entity.metadata["qualified_name"]: class_entity
            for class_entity in parsed.classes
        }
        for function in parsed.functions:
            graph.add_node(
                function.id,
                "function",
                name=function.name,
                qualified_name=function.metadata.get("qualified_name", function.name),
                file_path=Path(function.file_path).as_posix(),
                start_line=function.start_line,
                end_line=function.end_line,
                is_test=function.metadata.get("is_test", False),
            )
            class_name = function.metadata.get("class_name")
            if class_name and class_name in class_by_name:
                graph.add_edge(class_by_name[class_name].id, function.id, "contains")
            else:
                graph.add_edge(_file_node_id(function.file_path), function.id, "contains")

    def _add_import_nodes(self, parsed: RepoParseResult, graph: ProgramGraph) -> None:
        for item in parsed.imports:
            if item.kind == "module_all":
                continue
            file_id = _file_node_id(item.file_path)
            for name in item.names:
                import_name = f"{item.module}.{name}" if item.module else name
                import_id = f"import::{import_name}"
                if import_id not in graph.nodes:
                    graph.add_node(
                        import_id,
                        "import",
                        name=import_name,
                        kind=item.kind,
                    )
                graph.add_edge(
                    file_id,
                    import_id,
                    "imports",
                    line=item.line,
                    import_kind=item.kind,
                )

    def _add_call_edges(self, call_graph: CallGraph, graph: ProgramGraph) -> None:
        for edge in call_graph.edges:
            graph.add_edge(
                source=edge["source"],
                target=edge["target"],
                edge_type="calls",
                callee=edge.get("callee"),
                line=edge.get("line"),
                weight=edge.get("weight", 1.0),
                is_awaited=edge.get("is_awaited", False),
                async_kind=edge.get("async_kind", ""),
                resolution=edge.get("resolution", ""),
                import_alias=edge.get("import_alias", ""),
                import_module=edge.get("import_module", ""),
                import_name=edge.get("import_name", ""),
                import_level=edge.get("import_level", 0),
                import_kind=edge.get("import_kind", "static"),
                is_relative_import=edge.get("is_relative_import", False),
                is_star_import=edge.get("is_star_import", False),
                star_import_uses_all=edge.get("star_import_uses_all", False),
                is_reexport=edge.get("is_reexport", False),
                reexport_module=edge.get("reexport_module", ""),
                reexport_name=edge.get("reexport_name", ""),
                is_symbol_alias=edge.get("is_symbol_alias", False),
                symbol_alias_scope=edge.get("symbol_alias_scope", ""),
                symbol_alias_source=edge.get("symbol_alias_source", ""),
                instance_alias=edge.get("instance_alias", ""),
                receiver_alias=edge.get("receiver_alias", ""),
                class_name=edge.get("class_name", ""),
                class_module=edge.get("class_module", ""),
                base_class=edge.get("base_class", ""),
                base_module=edge.get("base_module", ""),
            )
            if edge.get("is_awaited", False) or edge.get("async_kind") in {
                "task",
                "gather",
            }:
                graph.add_edge(
                    source=edge["source"],
                    target=edge["target"],
                    edge_type="awaits",
                    callee=edge.get("callee"),
                    line=edge.get("line"),
                    weight=edge.get("weight", 1.0),
                    is_awaited=edge.get("is_awaited", False),
                    async_kind=edge.get("async_kind", ""),
                    resolution=edge.get("resolution", ""),
                    import_alias=edge.get("import_alias", ""),
                    import_module=edge.get("import_module", ""),
                    import_name=edge.get("import_name", ""),
                    import_level=edge.get("import_level", 0),
                    import_kind=edge.get("import_kind", "static"),
                    is_relative_import=edge.get("is_relative_import", False),
                    is_star_import=edge.get("is_star_import", False),
                    star_import_uses_all=edge.get("star_import_uses_all", False),
                    is_reexport=edge.get("is_reexport", False),
                    reexport_module=edge.get("reexport_module", ""),
                    reexport_name=edge.get("reexport_name", ""),
                    is_symbol_alias=edge.get("is_symbol_alias", False),
                    symbol_alias_scope=edge.get("symbol_alias_scope", ""),
                    symbol_alias_source=edge.get("symbol_alias_source", ""),
                    instance_alias=edge.get("instance_alias", ""),
                    receiver_alias=edge.get("receiver_alias", ""),
                    class_name=edge.get("class_name", ""),
                    class_module=edge.get("class_module", ""),
                    base_class=edge.get("base_class", ""),
                    base_module=edge.get("base_module", ""),
                )
            caller = graph.functions.get(edge["source"])
            callee = graph.functions.get(edge["target"])
            if caller is None or callee is None:
                continue
            if Path(caller.file_path).resolve() == Path(callee.file_path).resolve():
                continue
            graph.add_edge(
                source=edge["source"],
                target=edge["target"],
                edge_type="module_depends_on",
                line=edge.get("line"),
                resolution=edge.get("resolution", ""),
                import_alias=edge.get("import_alias", ""),
                import_module=edge.get("import_module", ""),
                import_name=edge.get("import_name", ""),
                import_level=edge.get("import_level", 0),
                import_kind=edge.get("import_kind", "static"),
                is_relative_import=edge.get("is_relative_import", False),
                is_star_import=edge.get("is_star_import", False),
                star_import_uses_all=edge.get("star_import_uses_all", False),
                is_reexport=edge.get("is_reexport", False),
                reexport_module=edge.get("reexport_module", ""),
                reexport_name=edge.get("reexport_name", ""),
                is_symbol_alias=edge.get("is_symbol_alias", False),
                symbol_alias_scope=edge.get("symbol_alias_scope", ""),
                symbol_alias_source=edge.get("symbol_alias_source", ""),
                async_kind=edge.get("async_kind", ""),
                instance_alias=edge.get("instance_alias", ""),
                receiver_alias=edge.get("receiver_alias", ""),
                class_name=edge.get("class_name", ""),
                class_module=edge.get("class_module", ""),
                base_class=edge.get("base_class", ""),
                base_module=edge.get("base_module", ""),
                caller_file=Path(caller.file_path).as_posix(),
                callee_file=Path(callee.file_path).as_posix(),
                package_distance=_package_distance(caller.file_path, callee.file_path),
            )

    def _add_test_edges(
        self, parsed: RepoParseResult, call_graph: CallGraph, graph: ProgramGraph
    ) -> None:
        test_ids = {test.id for test in parsed.tests}
        for edge in call_graph.edges:
            if edge["source"] in test_ids and edge["target"] not in test_ids:
                graph.add_edge(edge["source"], edge["target"], "tested_by")

    def _add_variable_edges(self, parsed: RepoParseResult, graph: ProgramGraph) -> None:
        for function in parsed.functions:
            if function.metadata.get("is_test") or function.metadata.get("is_test_file"):
                continue
            analysis = _analyze_function_variables(function)
            for variable in sorted(analysis.variables):
                variable_id = _variable_node_id(function.id, variable)
                graph.add_node(
                    variable_id,
                    "variable",
                    name=variable,
                    function_id=function.id,
                    function_name=function.metadata.get("qualified_name", function.name),
                    file_path=Path(function.file_path).as_posix(),
                )
            for event in analysis.defines:
                graph.add_edge(
                    function.id,
                    _variable_node_id(function.id, event.name),
                    "defines",
                    line=event.line,
                    variable=event.name,
                )
            for event in analysis.uses:
                graph.add_edge(
                    function.id,
                    _variable_node_id(function.id, event.name),
                    "uses",
                    line=event.line,
                    variable=event.name,
                )
            for dependency in analysis.dependencies:
                graph.add_edge(
                    _variable_node_id(function.id, dependency.source),
                    _variable_node_id(function.id, dependency.target),
                    "data_depends_on",
                    line=dependency.line,
                    function_id=function.id,
                    source_variable=dependency.source,
                    target_variable=dependency.target,
                )
            for dependency in analysis.subscript_key_dependencies:
                graph.add_edge(
                    _variable_node_id(function.id, dependency.source),
                    _variable_node_id(function.id, dependency.target),
                    "key_flows_to_subscript",
                    line=dependency.line,
                    function_id=function.id,
                    key_variable=dependency.source,
                    mapping_variable=dependency.target,
                )

    def _add_control_flow_edges(
        self, parsed: RepoParseResult, graph: ProgramGraph
    ) -> None:
        for function in parsed.functions:
            if function.metadata.get("is_test") or function.metadata.get("is_test_file"):
                continue
            for statement in _control_statements(function):
                statement_id = _statement_node_id(
                    function.id,
                    statement.line,
                    statement.kind,
                )
                graph.add_node(
                    statement_id,
                    "statement",
                    kind=statement.kind,
                    function_id=function.id,
                    function_name=function.metadata.get("qualified_name", function.name),
                    file_path=Path(function.file_path).as_posix(),
                    line=statement.line,
                )
                graph.add_edge(function.id, statement_id, "contains", line=statement.line)
                graph.add_edge(
                    statement_id,
                    function.id,
                    "controls",
                    line=statement.line,
                    function_id=function.id,
                    statement_type=statement.kind,
                )

    def _add_cfg_edges(self, parsed: RepoParseResult, graph: ProgramGraph) -> None:
        for function in parsed.functions:
            if function.metadata.get("is_test") or function.metadata.get("is_test_file"):
                continue
            cfg = _build_function_cfg(function)
            for block in cfg.blocks:
                block_id = _basic_block_node_id(function.id, block.index)
                graph.add_node(
                    block_id,
                    "basic_block",
                    kind=block.kind,
                    function_id=function.id,
                    function_name=function.metadata.get("qualified_name", function.name),
                    file_path=Path(function.file_path).as_posix(),
                    start_line=block.start_line,
                    end_line=block.end_line,
                    statement_kinds=block.statement_kinds,
                )
                graph.add_edge(
                    function.id,
                    block_id,
                    "contains",
                    line=block.start_line,
                    function_id=function.id,
                )
            if cfg.entry_block is not None:
                graph.add_edge(
                    function.id,
                    _basic_block_node_id(function.id, cfg.entry_block),
                    "cfg_entry",
                    function_id=function.id,
                )
            for edge in cfg.edges:
                graph.add_edge(
                    _basic_block_node_id(function.id, edge.source),
                    _basic_block_node_id(function.id, edge.target),
                    edge.edge_type,
                    function_id=function.id,
                    branch=edge.branch,
                )

    def _add_cross_function_data_flow_edges(
        self,
        call_graph: CallGraph,
        graph: ProgramGraph,
    ) -> None:
        for edge in call_graph.edges:
            caller_id = edge["source"]
            callee_id = edge["target"]
            caller = graph.functions.get(caller_id)
            callee = graph.functions.get(callee_id)
            if caller is None or callee is None:
                continue
            if _is_test_function(caller) or _is_test_function(callee):
                continue
            parameter_names = _call_parameter_names(callee)
            for position, source_names in enumerate(edge.get("arg_names", [])):
                if position >= len(parameter_names):
                    continue
                parameter = parameter_names[position]
                for source_name in source_names:
                    source_id = _variable_node_id(caller_id, source_name)
                    target_id = _variable_node_id(callee_id, parameter)
                    if source_id not in graph.nodes or target_id not in graph.nodes:
                        continue
                    graph.add_edge(
                        source_id,
                        target_id,
                        "arg_flows_to_param",
                        line=edge.get("line"),
                        caller_function_id=caller_id,
                        callee_function_id=callee_id,
                        source_variable=source_name,
                        target_variable=parameter,
                        argument_position=position,
                    )
            for target_name in edge.get("assigned_to", []):
                target_id = _variable_node_id(caller_id, target_name)
                if target_id not in graph.nodes:
                    continue
                graph.add_edge(
                    callee_id,
                    target_id,
                    "return_flows_to_var",
                    line=edge.get("line"),
                    caller_function_id=caller_id,
                    callee_function_id=callee_id,
                    target_variable=target_name,
                )


def _file_node_id(file_path: str) -> str:
    return f"file::{Path(file_path).as_posix()}"


def _package_distance(source_file: str | Path, target_file: str | Path) -> int:
    source_parts = Path(source_file).resolve().parent.parts
    target_parts = Path(target_file).resolve().parent.parts
    common_length = 0
    for source_part, target_part in zip(source_parts, target_parts):
        if source_part != target_part:
            break
        common_length += 1
    return (len(source_parts) - common_length) + (
        len(target_parts) - common_length
    )


def _variable_node_id(function_id: str, variable: str) -> str:
    return f"{function_id}::var::{variable}"


def _statement_node_id(function_id: str, line: int, kind: str) -> str:
    return f"{function_id}::stmt::{line}::{kind}"


def _basic_block_node_id(function_id: str, index: int) -> str:
    return f"{function_id}::bb::{index}"


def _is_test_function(function: CodeEntity) -> bool:
    return bool(function.metadata.get("is_test") or function.metadata.get("is_test_file"))


def _call_parameter_names(function: CodeEntity) -> list[str]:
    args = list(function.metadata.get("args", []))
    if function.metadata.get("is_method") and args and args[0] in {"self", "cls"}:
        return args[1:]
    return args


@dataclass(frozen=True)
class _ControlStatement:
    kind: str
    line: int


@dataclass(frozen=True)
class _BasicBlock:
    index: int
    kind: str
    start_line: int
    end_line: int
    statement_kinds: list[str]


@dataclass(frozen=True)
class _CFGEdge:
    source: int
    target: int
    edge_type: str
    branch: str = ""


@dataclass(frozen=True)
class _CFGExit:
    source: int
    edge_type: str = "cfg_next"
    branch: str = ""


@dataclass(frozen=True)
class _FunctionCFG:
    blocks: list[_BasicBlock]
    edges: list[_CFGEdge]
    entry_block: int | None


@dataclass(frozen=True)
class _VariableEvent:
    name: str
    line: int


@dataclass(frozen=True)
class _VariableDependency:
    source: str
    target: str
    line: int


@dataclass(frozen=True)
class _FunctionVariableAnalysis:
    variables: set[str]
    defines: list[_VariableEvent]
    uses: list[_VariableEvent]
    dependencies: list[_VariableDependency]
    subscript_key_dependencies: list[_VariableDependency]


def _analyze_function_variables(function: CodeEntity) -> _FunctionVariableAnalysis:
    try:
        tree = ast.parse(textwrap.dedent(function.source))
    except SyntaxError:
        return _FunctionVariableAnalysis(set(), [], [], [], [])
    root = tree.body[0] if tree.body else None
    if not isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _FunctionVariableAnalysis(set(), [], [], [], [])

    collector = _FunctionVariableCollector(line_offset=function.start_line - 1)
    collector.add_arguments(root)
    for statement in root.body:
        collector.visit(statement)
    return _FunctionVariableAnalysis(
        variables=collector.variables,
        defines=collector.defines,
        uses=collector.uses,
        dependencies=collector.dependencies,
        subscript_key_dependencies=collector.subscript_key_dependencies,
    )


def _control_statements(function: CodeEntity) -> list[_ControlStatement]:
    try:
        tree = ast.parse(textwrap.dedent(function.source))
    except SyntaxError:
        return []
    root = tree.body[0] if tree.body else None
    if not isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    statements: list[_ControlStatement] = []
    line_offset = function.start_line - 1
    for node in ast.walk(root):
        kind = _control_statement_kind(node)
        if not kind:
            continue
        statements.append(
            _ControlStatement(
                kind=kind,
                line=line_offset + getattr(node, "lineno", 1),
            )
        )
    return statements


def _control_statement_kind(node: ast.AST) -> str:
    if isinstance(node, ast.If):
        return "if"
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return "for"
    if isinstance(node, ast.While):
        return "while"
    if isinstance(node, ast.Try):
        return "try"
    if isinstance(node, ast.ExceptHandler):
        return "except"
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return "with"
    if isinstance(node, ast.Match):
        return "match"
    return ""


def _build_function_cfg(function: CodeEntity) -> _FunctionCFG:
    try:
        tree = ast.parse(textwrap.dedent(function.source))
    except SyntaxError:
        return _FunctionCFG([], [], None)
    root = tree.body[0] if tree.body else None
    if not isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _FunctionCFG([], [], None)
    builder = _FunctionCFGBuilder(function)
    exits = builder.build_sequence(root.body, [])
    for exit_item in exits:
        builder.add_exit_marker(exit_item)
    return _FunctionCFG(
        blocks=builder.blocks,
        edges=builder.edges,
        entry_block=builder.entry_block,
    )


class _FunctionCFGBuilder:
    def __init__(self, function: CodeEntity) -> None:
        self.function = function
        self.line_offset = function.start_line - 1
        self.blocks: list[_BasicBlock] = []
        self.edges: list[_CFGEdge] = []
        self.entry_block: int | None = None

    def build_sequence(
        self,
        statements: list[ast.stmt],
        incoming: list[_CFGExit],
    ) -> list[_CFGExit]:
        exits = incoming
        pending: list[ast.stmt] = []
        for statement in statements:
            if _control_statement_kind(statement):
                exits = self._flush_basic_block(pending, exits)
                pending = []
                exits = self._add_control_statement(statement, exits)
                continue
            pending.append(statement)
            if _is_terminal_statement(statement):
                exits = self._flush_basic_block(pending, exits, terminal=True)
                pending = []
        return self._flush_basic_block(pending, exits)

    def add_exit_marker(self, exit_item: _CFGExit) -> None:
        # Function exits are implicit. Keeping them out of the graph avoids adding a
        # synthetic node that would distort centrality-style graph signals.
        _ = exit_item

    def _flush_basic_block(
        self,
        statements: list[ast.stmt],
        incoming: list[_CFGExit],
        terminal: bool = False,
    ) -> list[_CFGExit]:
        if not statements:
            return incoming
        block = self._new_block("basic", statements)
        self._connect(incoming, block.index)
        if terminal:
            return []
        return [_CFGExit(block.index)]

    def _add_control_statement(
        self,
        statement: ast.stmt,
        incoming: list[_CFGExit],
    ) -> list[_CFGExit]:
        kind = _control_statement_kind(statement)
        control = self._new_block(kind, [statement], header_only=True)
        self._connect(incoming, control.index)
        if isinstance(statement, ast.If):
            body_exits = self.build_sequence(
                statement.body,
                [_CFGExit(control.index, "cfg_branch", "true")],
            )
            orelse_exits = (
                self.build_sequence(
                    statement.orelse,
                    [_CFGExit(control.index, "cfg_branch", "false")],
                )
                if statement.orelse
                else [_CFGExit(control.index, "cfg_branch", "false")]
            )
            return [*body_exits, *orelse_exits]
        if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
            body_exits = self.build_sequence(
                statement.body,
                [_CFGExit(control.index, "cfg_loop", "taken")],
            )
            for exit_item in body_exits:
                self.edges.append(
                    _CFGEdge(
                        source=exit_item.source,
                        target=control.index,
                        edge_type="cfg_loop",
                        branch="back",
                    )
                )
            loop_exit = [_CFGExit(control.index, "cfg_loop", "exit")]
            if statement.orelse:
                return self.build_sequence(statement.orelse, loop_exit)
            return loop_exit
        if isinstance(statement, ast.Try):
            try_exits = self.build_sequence(
                statement.body,
                [_CFGExit(control.index, "cfg_exception", "try")],
            )
            handler_exits: list[_CFGExit] = []
            for handler in statement.handlers:
                handler_exits.extend(
                    self.build_sequence(
                        handler.body,
                        [_CFGExit(control.index, "cfg_exception", "except")],
                    )
                )
            normal_exits = (
                self.build_sequence(statement.orelse, try_exits)
                if statement.orelse
                else try_exits
            )
            exits = [*normal_exits, *handler_exits]
            if statement.finalbody:
                return self.build_sequence(statement.finalbody, exits)
            return exits or [_CFGExit(control.index, "cfg_exception", "exit")]
        body = getattr(statement, "body", [])
        if isinstance(body, list) and body:
            return self.build_sequence(
                body,
                [_CFGExit(control.index, "cfg_branch", "body")],
            )
        return [_CFGExit(control.index)]

    def _new_block(
        self,
        kind: str,
        statements: list[ast.stmt],
        header_only: bool = False,
    ) -> _BasicBlock:
        index = len(self.blocks)
        start_line = min(self._absolute_line(statement) for statement in statements)
        end_line = (
            start_line
            if header_only
            else max(self._absolute_end_line(statement) for statement in statements)
        )
        block = _BasicBlock(
            index=index,
            kind=kind,
            start_line=start_line,
            end_line=end_line,
            statement_kinds=[type(statement).__name__ for statement in statements],
        )
        self.blocks.append(block)
        if self.entry_block is None:
            self.entry_block = index
        return block

    def _connect(self, exits: list[_CFGExit], target: int) -> None:
        for exit_item in exits:
            self.edges.append(
                _CFGEdge(
                    source=exit_item.source,
                    target=target,
                    edge_type=exit_item.edge_type,
                    branch=exit_item.branch,
                )
            )

    def _absolute_line(self, node: ast.AST) -> int:
        return self.line_offset + int(getattr(node, "lineno", 1))

    def _absolute_end_line(self, node: ast.AST) -> int:
        return self.line_offset + int(
            getattr(node, "end_lineno", getattr(node, "lineno", 1))
        )


def _is_terminal_statement(statement: ast.stmt) -> bool:
    return isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue))


class _FunctionVariableCollector(ast.NodeVisitor):
    def __init__(self, line_offset: int) -> None:
        self.line_offset = line_offset
        self.variables: set[str] = set()
        self.defines: list[_VariableEvent] = []
        self.uses: list[_VariableEvent] = []
        self.dependencies: list[_VariableDependency] = []
        self.subscript_key_dependencies: list[_VariableDependency] = []

    def add_arguments(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for arg in [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ]:
            self._define(arg.arg, node.lineno)
        if node.args.vararg is not None:
            self._define(node.args.vararg.arg, node.lineno)
        if node.args.kwarg is not None:
            self._define(node.args.kwarg.arg, node.lineno)

    def visit_Assign(self, node: ast.Assign) -> None:
        targets = _assigned_names(node.targets)
        sources = _loaded_names(node.value)
        self._add_assignment_dependencies(targets, sources, node.lineno)
        self.visit(node.value)
        for target in node.targets:
            self.visit(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        targets = _assigned_names([node.target])
        sources = _loaded_names(node.value) if node.value is not None else set()
        self._add_assignment_dependencies(targets, sources, node.lineno)
        if node.value is not None:
            self.visit(node.value)
        self.visit(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        targets = _assigned_names([node.target])
        sources = _loaded_names(node.value).union(targets)
        self._add_assignment_dependencies(targets, sources, node.lineno)
        self.visit(node.value)
        self.visit(node.target)

    def visit_For(self, node: ast.For) -> None:
        targets = _assigned_names([node.target])
        sources = _loaded_names(node.iter)
        self._add_assignment_dependencies(targets, sources, node.lineno)
        self.visit(node.iter)
        self.visit(node.target)
        for statement in node.body:
            self.visit(statement)
        for statement in node.orelse:
            self.visit(statement)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        targets = _assigned_names([node.target])
        sources = _loaded_names(node.value)
        self._add_assignment_dependencies(targets, sources, node.lineno)
        self.visit(node.value)
        self.visit(node.target)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        mapping_name = _assignment_root_name(node.value)
        if mapping_name:
            for key_name in sorted(_loaded_names(node.slice)):
                if key_name == mapping_name:
                    continue
                self.subscript_key_dependencies.append(
                    _VariableDependency(
                        source=key_name,
                        target=mapping_name,
                        line=self._absolute_line(node.lineno),
                    )
                )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self._define(node.id, node.lineno)
        elif isinstance(node.ctx, ast.Load):
            self._use(node.id, node.lineno)

    def _add_assignment_dependencies(
        self, targets: set[str], sources: set[str], lineno: int
    ) -> None:
        for target in sorted(targets):
            self._define(target, lineno)
            for source in sorted(sources):
                self._use(source, lineno)
                self.dependencies.append(
                    _VariableDependency(
                        source=source,
                        target=target,
                        line=self._absolute_line(lineno),
                    )
                )

    def _define(self, name: str, lineno: int) -> None:
        self.variables.add(name)
        event = _VariableEvent(name=name, line=self._absolute_line(lineno))
        if event not in self.defines:
            self.defines.append(event)

    def _use(self, name: str, lineno: int) -> None:
        self.variables.add(name)
        event = _VariableEvent(name=name, line=self._absolute_line(lineno))
        if event not in self.uses:
            self.uses.append(event)

    def _absolute_line(self, lineno: int) -> int:
        return self.line_offset + lineno


def _assigned_names(nodes: list[ast.AST]) -> set[str]:
    names: set[str] = set()
    for node in nodes:
        root_name = _assignment_root_name(node)
        if root_name:
            names.add(root_name)
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                names.add(child.id)
    return names


def _loaded_names(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            names.add(child.id)
    return names


def _assignment_root_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript):
        return _assignment_root_name(node.value)
    if isinstance(node, ast.Attribute):
        return _assignment_root_name(node.value)
    return ""


def build_program_graph(
    parsed: RepoParseResult, call_graph: CallGraph | None = None
) -> ProgramGraph:
    return ProgramGraphBuilder().build(parsed=parsed, call_graph=call_graph)
