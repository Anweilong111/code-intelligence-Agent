from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from code_intelligence_agent.core.models import CodeEntity, PatchCandidate
from code_intelligence_agent.core.program_graph import ProgramGraph


def annotate_refinement_context(
    candidate: PatchCandidate,
    program_graph: ProgramGraph | None,
    *,
    max_related: int = 4,
    max_source_chars: int = 1200,
) -> PatchCandidate:
    if program_graph is None or isinstance(
        candidate.metadata.get("refinement_context"),
        dict,
    ):
        return candidate
    context = build_refinement_context(
        candidate,
        program_graph,
        max_related=max_related,
        max_source_chars=max_source_chars,
    )
    if not context.get("available"):
        return candidate
    return replace(
        candidate,
        metadata={
            **candidate.metadata,
            "refinement_context": context,
        },
    )


def build_refinement_context(
    candidate: PatchCandidate,
    program_graph: ProgramGraph,
    *,
    max_related: int = 4,
    max_source_chars: int = 1200,
) -> dict[str, Any]:
    target = program_graph.functions.get(candidate.target_function_id)
    if target is None:
        return {"available": False, "reason": "target function not in program graph"}

    return {
        "available": True,
        "target": _function_summary(target, include_source=False),
        "callers": _related_call_functions(
            target.id,
            program_graph,
            direction="incoming",
            max_related=max_related,
            max_source_chars=max_source_chars,
        ),
        "callees": _related_call_functions(
            target.id,
            program_graph,
            direction="outgoing",
            max_related=max_related,
            max_source_chars=max_source_chars,
        ),
        "module_dependencies": _module_dependencies(
            target.id,
            program_graph,
            max_related=max_related,
            max_source_chars=max_source_chars,
        ),
        "data_flow_neighbors": _data_flow_neighbors(
            target.id,
            program_graph,
            max_related=max_related,
            max_source_chars=max_source_chars,
        ),
        "selection_policy": {
            "max_related_per_section": max_related,
            "max_source_chars_per_function": max_source_chars,
            "cross_file_first": True,
        },
    }


def _related_call_functions(
    function_id: str,
    program_graph: ProgramGraph,
    *,
    direction: str,
    max_related: int,
    max_source_chars: int,
) -> list[dict[str, Any]]:
    target = program_graph.functions[function_id]
    related: dict[str, dict[str, Any]] = {}
    for edge in program_graph.edges:
        if edge["type"] not in {"calls", "awaits"}:
            continue
        if direction == "incoming":
            if edge["target"] != function_id:
                continue
            related_id = edge["source"]
        else:
            if edge["source"] != function_id:
                continue
            related_id = edge["target"]
        function = program_graph.functions.get(related_id)
        if function is None or _is_test_function(function):
            continue
        entry = related.setdefault(
            related_id,
            {
                **_function_summary(
                    function,
                    max_source_chars=max_source_chars,
                ),
                "relation": direction,
                "edge_types": [],
                "is_cross_file": _is_cross_file(target, function),
                "lines": [],
                "is_awaited": False,
                "async_kinds": [],
                "import_kind": "",
                "is_relative_import": False,
                "package_distance": 0,
            },
        )
        _merge_edge(entry, edge)

    return _prioritized(related.values())[:max_related]


def _module_dependencies(
    function_id: str,
    program_graph: ProgramGraph,
    *,
    max_related: int,
    max_source_chars: int,
) -> list[dict[str, Any]]:
    target = program_graph.functions[function_id]
    dependencies: list[dict[str, Any]] = []
    for edge in program_graph.edges:
        if edge["type"] != "module_depends_on":
            continue
        direction = ""
        related_id = ""
        if edge["target"] == function_id:
            direction = "incoming"
            related_id = str(edge["source"])
        elif edge["source"] == function_id:
            direction = "outgoing"
            related_id = str(edge["target"])
        if not related_id:
            continue
        function = program_graph.functions.get(related_id)
        if function is None or _is_test_function(function):
            continue
        item = {
            **_function_summary(function, max_source_chars=max_source_chars),
            "relation": direction,
            "is_cross_file": _is_cross_file(target, function),
            "line": edge.get("line"),
            "import_alias": edge.get("import_alias", ""),
            "import_module": edge.get("import_module", ""),
            "import_name": edge.get("import_name", ""),
            "import_kind": edge.get("import_kind", ""),
            "async_kind": edge.get("async_kind", ""),
            "is_relative_import": bool(edge.get("is_relative_import", False)),
            "package_distance": int(edge.get("package_distance", 0) or 0),
        }
        dependencies.append(item)
    return _prioritized(dependencies)[:max_related]


def _data_flow_neighbors(
    function_id: str,
    program_graph: ProgramGraph,
    *,
    max_related: int,
    max_source_chars: int,
) -> list[dict[str, Any]]:
    neighbors: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in program_graph.edges:
        edge_type = edge["type"]
        if edge_type == "key_flows_to_subscript":
            if edge.get("function_id") != function_id:
                continue
            function = program_graph.functions[function_id]
            key = (function_id, edge_type)
            item = neighbors.setdefault(
                key,
                {
                    **_function_summary(function, max_source_chars=max_source_chars),
                    "relation": "local",
                    "edge_type": edge_type,
                    "is_cross_file": False,
                    "flows": [],
                },
            )
            item["flows"].append(
                {
                    "key_variable": edge.get("key_variable", ""),
                    "mapping_variable": edge.get("mapping_variable", ""),
                    "line": edge.get("line"),
                }
            )
            continue
        if edge_type not in {"arg_flows_to_param", "return_flows_to_var"}:
            continue
        caller_id = edge.get("caller_function_id")
        callee_id = edge.get("callee_function_id")
        if function_id not in {caller_id, callee_id}:
            continue
        related_id = callee_id if caller_id == function_id else caller_id
        function = program_graph.functions.get(str(related_id))
        if function is None or _is_test_function(function):
            continue
        relation = "outgoing" if caller_id == function_id else "incoming"
        key = (str(related_id), edge_type)
        item = neighbors.setdefault(
            key,
            {
                **_function_summary(function, max_source_chars=max_source_chars),
                "relation": relation,
                "edge_type": edge_type,
                "is_cross_file": _is_cross_file(
                    program_graph.functions[function_id],
                    function,
                ),
                "flows": [],
            },
        )
        item["flows"].append(
            {
                "source_variable": edge.get("source_variable", ""),
                "target_variable": edge.get("target_variable", ""),
                "target_variable_in_caller": edge.get("target_variable", ""),
                "argument_position": edge.get("argument_position"),
                "line": edge.get("line"),
            }
        )
    return _prioritized(neighbors.values())[:max_related]


def _merge_edge(entry: dict[str, Any], edge: dict[str, Any]) -> None:
    edge_type = str(edge.get("type", ""))
    if edge_type and edge_type not in entry["edge_types"]:
        entry["edge_types"].append(edge_type)
    line = edge.get("line")
    if line is not None and line not in entry["lines"]:
        entry["lines"].append(line)
    entry["is_awaited"] = bool(entry["is_awaited"] or edge.get("is_awaited", False))
    async_kind = edge.get("async_kind", "")
    if async_kind and async_kind not in entry["async_kinds"]:
        entry["async_kinds"].append(async_kind)
    if edge.get("import_kind"):
        entry["import_kind"] = edge.get("import_kind", "")
    if edge.get("is_relative_import", False):
        entry["is_relative_import"] = True
    entry["package_distance"] = max(
        int(entry.get("package_distance", 0) or 0),
        int(edge.get("package_distance", 0) or 0),
    )


def _function_summary(
    function: CodeEntity,
    *,
    include_source: bool = True,
    max_source_chars: int = 1200,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "function_id": function.id,
        "name": function.name,
        "qualified_name": function.metadata.get("qualified_name", function.name),
        "file_path": Path(function.file_path).as_posix(),
        "start_line": function.start_line,
        "end_line": function.end_line,
    }
    if include_source:
        excerpt, truncated = _source_excerpt(function.source, max_source_chars)
        summary["source_excerpt"] = excerpt
        summary["source_truncated"] = truncated
    return summary


def _source_excerpt(source: str, limit: int) -> tuple[str, bool]:
    normalized = "\n".join(line.rstrip() for line in source.strip().splitlines())
    if len(normalized) <= limit:
        return normalized, False
    return normalized[:limit] + "\n...[truncated]", True


def _prioritized(items) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            bool(item.get("is_cross_file", False)),
            bool(item.get("is_relative_import", False)),
            int(item.get("package_distance", 0) or 0),
            len(item.get("edge_types", [])),
            str(item.get("qualified_name", "")),
        ),
        reverse=True,
    )


def _is_cross_file(left: CodeEntity, right: CodeEntity) -> bool:
    return Path(left.file_path).resolve() != Path(right.file_path).resolve()


def _is_test_function(function: CodeEntity) -> bool:
    return bool(
        function.metadata.get("is_test") or function.metadata.get("is_test_file")
    )
