from pathlib import Path
import tempfile

from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.models import TestExecutionSummary
from code_intelligence_agent.core.program_graph import build_program_graph
from code_intelligence_agent.core.program_slicer import program_slice_evidence
from code_intelligence_agent.core.repo_parser import RepoParser
from code_intelligence_agent.evaluation.slice_grounding import (
    slice_grounding_evidence,
)


def test_slice_grounding_scores_failed_test_path_and_slice_evidence():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "core.py").write_text(
            "def clamp(value):\n"
            "    if value < 0:\n"
            "        return 0\n"
            "    return value\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from core import clamp\n\n"
            "def pipeline(items):\n"
            "    seed = items[0]\n"
            "    return clamp(seed)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import pipeline\n\n"
            "def test_pipeline_clamps_negative():\n"
            "    assert pipeline([-1]) == 0\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    target = by_name["clamp"]
    test = by_name["test_pipeline_clamps_negative"]
    summary = TestExecutionSummary(
        failed_tests={test.id},
        coverage={test.id: {target.id}},
        test_names={test.id: test.name},
    )

    program_slice = program_slice_evidence(program_graph, target.id)
    grounding = slice_grounding_evidence(
        function_id=target.id,
        function_name="clamp",
        summary=summary,
        program_graph=program_graph,
        program_slice=program_slice,
    )

    assert grounding.grounded is True
    assert grounding.failed_test_reachability == 1.0
    assert grounding.failing_coverage_ratio == 1.0
    assert grounding.call_chain_edge_coverage == 1.0
    assert grounding.data_flow_support > 0.0
    assert grounding.control_flow_support > 0.0
    assert grounding.cross_boundary_support > 0.0
    assert grounding.support_score >= 0.8
    assert grounding.shortest_failed_call_chain == [
        "test_pipeline_clamps_negative",
        "pipeline",
        "clamp",
    ]
    assert set(grounding.support_reasons) == {
        "failed_test_support",
        "call_chain_supported",
        "data_flow_supported",
        "control_or_cfg_supported",
        "cross_boundary_supported",
    }
