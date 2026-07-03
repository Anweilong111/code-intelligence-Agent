from pathlib import Path
import tempfile

from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.program_graph import build_program_graph
from code_intelligence_agent.core.program_slicer import program_slice_evidence
from code_intelligence_agent.core.repo_parser import RepoParser


def test_program_slice_captures_calls_data_flow_and_cfg_neighborhood():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "core.py").write_text(
            "def add_one(value):\n"
            "    result = value + 1\n"
            "    return result\n\n"
            "def clamp(value):\n"
            "    if value < 0:\n"
            "        return 0\n"
            "    return value\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from core import add_one, clamp\n\n"
            "def pipeline(items):\n"
            "    seed = items[0]\n"
            "    shifted = add_one(seed)\n"
            "    return clamp(shifted)\n\n"
            "def lookup_score(scores, name):\n"
            "    return scores[name]\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }

    pipeline_slice = program_slice_evidence(program_graph, by_name["pipeline"].id)
    clamp_slice = program_slice_evidence(program_graph, by_name["clamp"].id)
    lookup_slice = program_slice_evidence(program_graph, by_name["lookup_score"].id)

    assert pipeline_slice.target_function_name == "pipeline"
    assert pipeline_slice.node_count > 0
    assert pipeline_slice.edge_type_counts["calls"] == 2
    assert pipeline_slice.cross_function_data_flow_edge_count >= 2
    assert pipeline_slice.cfg_edge_count >= 1
    assert {"items", "seed", "shifted"}.issubset(set(pipeline_slice.variables))
    assert pipeline_slice.outgoing_callees == ["add_one", "clamp"]
    assert any(
        edge["type"] == "arg_flows_to_param"
        and edge["source_variable"] == "seed"
        and edge["target_variable"] == "value"
        for edge in pipeline_slice.compact_edges
    )

    assert "pipeline" in clamp_slice.incoming_callers
    assert clamp_slice.control_flow_edge_count >= 1
    assert any(item.startswith("if:") for item in clamp_slice.control_statements)

    assert lookup_slice.edge_type_counts["key_flows_to_subscript"] == 1
    assert lookup_slice.data_flow_edge_count >= 1
    assert {"scores", "name"}.issubset(set(lookup_slice.variables))
    assert any(
        edge["type"] == "key_flows_to_subscript"
        and edge["key_variable"] == "name"
        and edge["mapping_variable"] == "scores"
        for edge in lookup_slice.compact_edges
    )
