from __future__ import annotations

from dataclasses import asdict, dataclass

from code_intelligence_agent.core.models import TestExecutionSummary
from code_intelligence_agent.core.program_graph import ProgramGraph
from code_intelligence_agent.core.program_slicer import ProgramSliceEvidence


@dataclass(frozen=True)
class SliceGroundingEvidence:
    target_function_id: str
    target_function_name: str
    support_score: float
    failed_test_reachability: float
    failing_coverage_ratio: float
    call_chain_edge_coverage: float
    data_flow_support: float
    control_flow_support: float
    cross_boundary_support: float
    evidence_dimension_count: int
    grounded: bool
    reachable_failed_tests: list[str]
    shortest_failed_call_chain: list[str]
    support_reasons: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def slice_grounding_evidence(
    *,
    function_id: str,
    function_name: str,
    summary: TestExecutionSummary,
    program_graph: ProgramGraph | None,
    program_slice: ProgramSliceEvidence,
    grounded_threshold: float = 0.5,
) -> SliceGroundingEvidence:
    if program_graph is None:
        return _empty_grounding(
            function_id=function_id,
            function_name=function_name,
            grounded_threshold=grounded_threshold,
        )

    failed_tests = sorted(summary.failed_tests)
    reachable_paths = _reachable_failed_test_paths(
        function_id=function_id,
        failed_tests=failed_tests,
        program_graph=program_graph,
    )
    reachable_failed_tests = [
        _node_display_name(program_graph, test_id) for test_id in reachable_paths
    ]
    shortest_path = min(reachable_paths.values(), key=len) if reachable_paths else []
    shortest_failed_call_chain = [
        _node_display_name(program_graph, node_id) for node_id in shortest_path
    ]
    failed_test_reachability = _ratio(len(reachable_paths), len(failed_tests))
    failing_coverage_ratio = _ratio(
        sum(
            1
            for test_id in failed_tests
            if function_id in summary.coverage.get(test_id, set())
        ),
        len(failed_tests),
    )
    call_chain_edge_coverage = _call_chain_edge_coverage(
        shortest_path,
        program_graph,
    )
    data_flow_support = _bounded_ratio(program_slice.data_flow_edge_count, 3)
    control_flow_support = max(
        _bounded_ratio(program_slice.control_flow_edge_count, 1),
        _bounded_ratio(program_slice.cfg_edge_count, 2),
    )
    cross_boundary_support = max(
        _bounded_ratio(program_slice.cross_function_data_flow_edge_count, 1),
        _bounded_ratio(program_slice.module_dependency_edge_count, 1),
        _bounded_ratio(len(program_slice.incoming_callers), 1),
        _bounded_ratio(len(program_slice.outgoing_callees), 1),
    )
    test_support = max(failed_test_reachability, failing_coverage_ratio)
    support_score = round(
        0.35 * test_support
        + 0.20 * call_chain_edge_coverage
        + 0.20 * data_flow_support
        + 0.15 * control_flow_support
        + 0.10 * cross_boundary_support,
        4,
    )
    support_reasons = _support_reasons(
        test_support=test_support,
        call_chain_edge_coverage=call_chain_edge_coverage,
        data_flow_support=data_flow_support,
        control_flow_support=control_flow_support,
        cross_boundary_support=cross_boundary_support,
    )
    return SliceGroundingEvidence(
        target_function_id=function_id,
        target_function_name=function_name,
        support_score=support_score,
        failed_test_reachability=round(failed_test_reachability, 4),
        failing_coverage_ratio=round(failing_coverage_ratio, 4),
        call_chain_edge_coverage=round(call_chain_edge_coverage, 4),
        data_flow_support=round(data_flow_support, 4),
        control_flow_support=round(control_flow_support, 4),
        cross_boundary_support=round(cross_boundary_support, 4),
        evidence_dimension_count=len(support_reasons),
        grounded=support_score >= grounded_threshold,
        reachable_failed_tests=reachable_failed_tests,
        shortest_failed_call_chain=shortest_failed_call_chain,
        support_reasons=support_reasons,
    )


def _empty_grounding(
    *,
    function_id: str,
    function_name: str,
    grounded_threshold: float,
) -> SliceGroundingEvidence:
    del grounded_threshold
    return SliceGroundingEvidence(
        target_function_id=function_id,
        target_function_name=function_name,
        support_score=0.0,
        failed_test_reachability=0.0,
        failing_coverage_ratio=0.0,
        call_chain_edge_coverage=0.0,
        data_flow_support=0.0,
        control_flow_support=0.0,
        cross_boundary_support=0.0,
        evidence_dimension_count=0,
        grounded=False,
        reachable_failed_tests=[],
        shortest_failed_call_chain=[],
        support_reasons=[],
    )


def _reachable_failed_test_paths(
    *,
    function_id: str,
    failed_tests: list[str],
    program_graph: ProgramGraph,
) -> dict[str, list[str]]:
    paths: dict[str, list[str]] = {}
    for test_id in failed_tests:
        path = program_graph.shortest_path(
            source=test_id,
            target=function_id,
            edge_types={"calls", "awaits", "tested_by", "module_depends_on"},
            max_depth=8,
        )
        if path is not None:
            paths[test_id] = path
    return paths


def _call_chain_edge_coverage(path: list[str], program_graph: ProgramGraph) -> float:
    if len(path) < 2:
        return 0.0
    edge_pairs = list(zip(path, path[1:]))
    covered = 0
    for source, target in edge_pairs:
        if _has_edge(
            source=source,
            target=target,
            program_graph=program_graph,
            edge_types={"calls", "awaits", "tested_by", "module_depends_on"},
        ):
            covered += 1
    return covered / len(edge_pairs)


def _has_edge(
    *,
    source: str,
    target: str,
    program_graph: ProgramGraph,
    edge_types: set[str],
) -> bool:
    return any(
        edge.get("source") == source
        and edge.get("target") == target
        and edge.get("type") in edge_types
        for edge in program_graph.edges
    )


def _support_reasons(
    *,
    test_support: float,
    call_chain_edge_coverage: float,
    data_flow_support: float,
    control_flow_support: float,
    cross_boundary_support: float,
) -> list[str]:
    reasons = []
    if test_support > 0.0:
        reasons.append("failed_test_support")
    if call_chain_edge_coverage > 0.0:
        reasons.append("call_chain_supported")
    if data_flow_support > 0.0:
        reasons.append("data_flow_supported")
    if control_flow_support > 0.0:
        reasons.append("control_or_cfg_supported")
    if cross_boundary_support > 0.0:
        reasons.append("cross_boundary_supported")
    return reasons


def _node_display_name(program_graph: ProgramGraph, node_id: str) -> str:
    function = program_graph.functions.get(node_id)
    if function is not None:
        return str(function.metadata.get("qualified_name", function.name))
    node = program_graph.nodes.get(node_id, {})
    return str(node.get("qualified_name") or node.get("name") or node_id)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _bounded_ratio(value: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return min(1.0, max(0.0, value / denominator))
