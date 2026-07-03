from __future__ import annotations

from collections import Counter, deque
from dataclasses import asdict, dataclass
from typing import Any

from code_intelligence_agent.core.program_graph import ProgramGraph


SLICE_EDGE_TYPES = {
    "calls",
    "awaits",
    "tested_by",
    "module_depends_on",
    "defines",
    "uses",
    "data_depends_on",
    "key_flows_to_subscript",
    "arg_flows_to_param",
    "return_flows_to_var",
    "controls",
    "cfg_entry",
    "cfg_next",
    "cfg_branch",
    "cfg_loop",
    "cfg_exception",
}

DATA_FLOW_EDGE_TYPES = {
    "data_depends_on",
    "key_flows_to_subscript",
    "arg_flows_to_param",
    "return_flows_to_var",
}
CONTROL_FLOW_EDGE_TYPES = {"controls"}
CFG_EDGE_TYPES = {"cfg_entry", "cfg_next", "cfg_branch", "cfg_loop", "cfg_exception"}
CALL_EDGE_TYPES = {"calls", "awaits", "tested_by"}


@dataclass(frozen=True)
class ProgramSliceEvidence:
    target_function_id: str
    target_function_name: str
    max_depth: int
    node_count: int
    edge_count: int
    node_type_counts: dict[str, int]
    edge_type_counts: dict[str, int]
    call_edge_count: int
    data_flow_edge_count: int
    cross_function_data_flow_edge_count: int
    control_flow_edge_count: int
    cfg_edge_count: int
    module_dependency_edge_count: int
    incoming_callers: list[str]
    outgoing_callees: list[str]
    variables: list[str]
    control_statements: list[str]
    basic_blocks: list[str]
    compact_edges: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def program_slice_evidence(
    program_graph: ProgramGraph | None,
    function_id: str,
    *,
    max_depth: int = 2,
    max_edges: int = 40,
) -> ProgramSliceEvidence:
    if program_graph is None or function_id not in program_graph.functions:
        return _empty_slice(function_id=function_id, max_depth=max_depth)

    distances = _slice_distances(program_graph, function_id, max_depth=max_depth)
    slice_nodes = set(distances)
    slice_edges = [
        edge
        for edge in program_graph.edges
        if edge.get("type") in SLICE_EDGE_TYPES
        and edge.get("source") in slice_nodes
        and edge.get("target") in slice_nodes
    ]
    node_type_counts = Counter(
        str(program_graph.nodes.get(node_id, {}).get("type", "unknown"))
        for node_id in slice_nodes
    )
    edge_type_counts = Counter(str(edge.get("type", "unknown")) for edge in slice_edges)
    function = program_graph.functions[function_id]
    target_name = str(function.metadata.get("qualified_name", function.name))
    return ProgramSliceEvidence(
        target_function_id=function_id,
        target_function_name=target_name,
        max_depth=max_depth,
        node_count=len(slice_nodes),
        edge_count=len(slice_edges),
        node_type_counts=dict(sorted(node_type_counts.items())),
        edge_type_counts=dict(sorted(edge_type_counts.items())),
        call_edge_count=sum(
            count
            for edge_type, count in edge_type_counts.items()
            if edge_type in CALL_EDGE_TYPES
        ),
        data_flow_edge_count=sum(
            count
            for edge_type, count in edge_type_counts.items()
            if edge_type in DATA_FLOW_EDGE_TYPES
        ),
        cross_function_data_flow_edge_count=sum(
            count
            for edge_type, count in edge_type_counts.items()
            if edge_type in {"arg_flows_to_param", "return_flows_to_var"}
        ),
        control_flow_edge_count=sum(
            count
            for edge_type, count in edge_type_counts.items()
            if edge_type in CONTROL_FLOW_EDGE_TYPES
        ),
        cfg_edge_count=sum(
            count
            for edge_type, count in edge_type_counts.items()
            if edge_type in CFG_EDGE_TYPES
        ),
        module_dependency_edge_count=edge_type_counts.get("module_depends_on", 0),
        incoming_callers=_incoming_callers(program_graph, function_id, slice_edges),
        outgoing_callees=_outgoing_callees(program_graph, function_id, slice_edges),
        variables=_slice_variables(program_graph, function_id, slice_nodes),
        control_statements=_slice_control_statements(
            program_graph,
            function_id,
            slice_nodes,
        ),
        basic_blocks=_slice_basic_blocks(program_graph, function_id, slice_nodes),
        compact_edges=_compact_edges(program_graph, slice_edges, max_edges=max_edges),
    )


def _slice_distances(
    program_graph: ProgramGraph,
    function_id: str,
    *,
    max_depth: int,
) -> dict[str, int]:
    adjacency: dict[str, list[str]] = {}
    for edge in program_graph.edges:
        if edge.get("type") not in SLICE_EDGE_TYPES:
            continue
        source = str(edge.get("source", ""))
        target = str(edge.get("target", ""))
        adjacency.setdefault(source, []).append(target)
        adjacency.setdefault(target, []).append(source)

    distances = {function_id: 0}
    queue = deque([function_id])
    while queue:
        node_id = queue.popleft()
        distance = distances[node_id]
        if distance >= max_depth:
            continue
        for next_id in adjacency.get(node_id, []):
            if next_id in distances:
                continue
            distances[next_id] = distance + 1
            queue.append(next_id)
    return distances


def _incoming_callers(
    program_graph: ProgramGraph,
    function_id: str,
    edges: list[dict[str, Any]],
) -> list[str]:
    callers = []
    for edge in edges:
        if edge.get("type") not in {"calls", "awaits", "module_depends_on"}:
            continue
        if edge.get("target") != function_id:
            continue
        caller = program_graph.functions.get(str(edge.get("source", "")))
        if caller is None:
            continue
        callers.append(str(caller.metadata.get("qualified_name", caller.name)))
    return sorted(set(callers))


def _outgoing_callees(
    program_graph: ProgramGraph,
    function_id: str,
    edges: list[dict[str, Any]],
) -> list[str]:
    callees = []
    for edge in edges:
        if edge.get("type") not in {"calls", "awaits", "module_depends_on"}:
            continue
        if edge.get("source") != function_id:
            continue
        callee = program_graph.functions.get(str(edge.get("target", "")))
        if callee is None:
            continue
        callees.append(str(callee.metadata.get("qualified_name", callee.name)))
    return sorted(set(callees))


def _slice_variables(
    program_graph: ProgramGraph,
    function_id: str,
    node_ids: set[str],
) -> list[str]:
    names = []
    for node_id in node_ids:
        node = program_graph.nodes.get(node_id, {})
        if node.get("type") != "variable" or node.get("function_id") != function_id:
            continue
        names.append(str(node.get("name", "")))
    return sorted(set(name for name in names if name))


def _slice_control_statements(
    program_graph: ProgramGraph,
    function_id: str,
    node_ids: set[str],
) -> list[str]:
    statements = []
    for node_id in node_ids:
        node = program_graph.nodes.get(node_id, {})
        if node.get("type") != "statement" or node.get("function_id") != function_id:
            continue
        statements.append(f"{node.get('kind', '')}:{node.get('line', '')}")
    return sorted(set(item for item in statements if item != ":"))


def _slice_basic_blocks(
    program_graph: ProgramGraph,
    function_id: str,
    node_ids: set[str],
) -> list[str]:
    blocks = []
    for node_id in node_ids:
        node = program_graph.nodes.get(node_id, {})
        if node.get("type") != "basic_block" or node.get("function_id") != function_id:
            continue
        blocks.append(
            f"{node.get('kind', '')}:{node.get('start_line', '')}-{node.get('end_line', '')}"
        )
    return sorted(set(item for item in blocks if item != ":-"))


def _compact_edges(
    program_graph: ProgramGraph,
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> list[dict[str, Any]]:
    compact = []
    for edge in sorted(
        edges,
        key=lambda item: (
            str(item.get("type", "")),
            int(item.get("line", 0) or 0),
            _node_label(program_graph, str(item.get("source", ""))),
            _node_label(program_graph, str(item.get("target", ""))),
        ),
    )[:max_edges]:
        compact.append(
            {
                "type": edge.get("type", ""),
                "source": _node_label(program_graph, str(edge.get("source", ""))),
                "target": _node_label(program_graph, str(edge.get("target", ""))),
                "line": edge.get("line"),
                "branch": edge.get("branch", ""),
                "source_variable": edge.get("source_variable", ""),
                "target_variable": edge.get("target_variable", ""),
                "key_variable": edge.get("key_variable", ""),
                "mapping_variable": edge.get("mapping_variable", ""),
                "resolution": edge.get("resolution", ""),
            }
        )
    return compact


def _node_label(program_graph: ProgramGraph, node_id: str) -> str:
    function = program_graph.functions.get(node_id)
    if function is not None:
        return str(function.metadata.get("qualified_name", function.name))
    node = program_graph.nodes.get(node_id, {})
    node_type = str(node.get("type", "node"))
    if node_type == "variable":
        return f"var:{node.get('name', node_id)}"
    if node_type == "statement":
        return f"stmt:{node.get('kind', '')}:{node.get('line', '')}"
    if node_type == "basic_block":
        return f"bb:{node.get('kind', '')}:{node.get('start_line', '')}"
    return str(node.get("qualified_name") or node.get("name") or node_id)


def _empty_slice(function_id: str, max_depth: int) -> ProgramSliceEvidence:
    return ProgramSliceEvidence(
        target_function_id=function_id,
        target_function_name="",
        max_depth=max_depth,
        node_count=0,
        edge_count=0,
        node_type_counts={},
        edge_type_counts={},
        call_edge_count=0,
        data_flow_edge_count=0,
        cross_function_data_flow_edge_count=0,
        control_flow_edge_count=0,
        cfg_edge_count=0,
        module_dependency_edge_count=0,
        incoming_callers=[],
        outgoing_callees=[],
        variables=[],
        control_statements=[],
        basic_blocks=[],
        compact_edges=[],
    )
