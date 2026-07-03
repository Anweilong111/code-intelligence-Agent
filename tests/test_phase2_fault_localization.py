from pathlib import Path
import tempfile

from code_intelligence_agent.agents.bug_detector import RuleBasedBugDetector
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.fault_localizer import (
    FaultLocalizationConfig,
    FaultLocalizer,
    branch_ochiai,
    ochiai,
    path_ochiai,
    statement_ochiai,
)
from code_intelligence_agent.core.models import TestExecutionSummary
from code_intelligence_agent.core.program_graph import build_program_graph
from code_intelligence_agent.core.repo_parser import RepoParser


FIXTURE = Path(__file__).parent / "fixtures" / "buggy_sample.py"


def _parsed_context():
    parsed = RepoParser().parse(FIXTURE)
    call_graph = build_call_graph(parsed.functions, parsed.calls)
    program_graph = build_program_graph(parsed, call_graph)
    detector = RuleBasedBugDetector()
    findings = detector.detect(parsed.functions)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    return parsed, program_graph, findings, by_name


def test_program_graph_contains_heterogeneous_nodes_and_edges():
    parsed, program_graph, _, by_name = _parsed_context()
    shift_left = by_name["shift_left"]
    test_shift = by_name["test_shift_left"]

    node_types = {node["type"] for node in program_graph.nodes.values()}
    edge_types = {edge["type"] for edge in program_graph.edges}

    assert {
        "file",
        "class",
        "function",
        "import",
        "variable",
        "statement",
        "basic_block",
    }.issubset(node_types)
    assert {
        "contains",
        "imports",
        "calls",
        "tested_by",
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
    }.issubset(edge_types)
    assert len(program_graph.functions) == len(parsed.functions)
    assert any(
        edge["type"] == "data_depends_on"
        and edge.get("function_id", "").endswith("::shift_left")
        and edge.get("target_variable") == "values"
        for edge in program_graph.edges
    )
    assert any(
        edge["type"] == "key_flows_to_subscript"
        and edge.get("function_id", "").endswith("::middle_value")
        and edge.get("key_variable") == "index"
        and edge.get("mapping_variable") == "values"
        for edge in program_graph.edges
    )
    assert any(
        edge["type"] == "arg_flows_to_param"
        and edge.get("caller_function_id", "").endswith("::Calculator.total")
        and edge.get("callee_function_id", "").endswith("::Calculator.add")
        and edge.get("source_variable") == "total"
        and edge.get("target_variable") == "a"
        for edge in program_graph.edges
    )
    assert any(
        edge["type"] == "return_flows_to_var"
        and edge.get("caller_function_id", "").endswith("::Calculator.total")
        and edge.get("callee_function_id", "").endswith("::Calculator.add")
        and edge.get("target_variable") == "total"
        for edge in program_graph.edges
    )
    assert program_graph.shortest_path(
        test_shift.id,
        shift_left.id,
        edge_types={"calls", "tested_by"},
    ) == [test_shift.id, shift_left.id]
    assert (
        program_graph.shortest_path_distance(
            test_shift.id,
            shift_left.id,
            edge_types={"calls", "tested_by"},
        )
        == 1
    )
    assert any(
        edge["type"] == "controls"
        and edge.get("function_id", "").endswith("::shift_left")
        and edge.get("statement_type") == "for"
        for edge in program_graph.edges
    )
    assert any(
        node["type"] == "basic_block"
        and node.get("function_id", "").endswith("::shift_left")
        and node.get("kind") == "for"
        for node in program_graph.nodes.values()
    )
    assert any(
        edge["type"] == "cfg_loop"
        and edge.get("function_id", "").endswith("::shift_left")
        and edge.get("branch") == "taken"
        for edge in program_graph.edges
    )
    assert any(
        edge["type"] == "cfg_branch"
        and edge.get("function_id", "").endswith("::has_items")
        and edge.get("branch") == "true"
        for edge in program_graph.edges
    )
    assert any(
        edge["type"] == "cfg_exception"
        and edge.get("function_id", "").endswith("::hidden_error")
        and edge.get("branch") == "except"
        for edge in program_graph.edges
    )


def test_ochiai_scores_failed_covered_functions():
    _, _, _, by_name = _parsed_context()
    shift_left = by_name["shift_left"]
    total = by_name["Calculator.total"]
    test_shift = by_name["test_shift_left"]
    test_total = by_name["test_total"]

    summary = TestExecutionSummary(
        failed_tests={test_shift.id},
        passed_tests={test_total.id},
        coverage={
            test_shift.id: {shift_left.id},
            test_total.id: {total.id},
        },
    )

    assert ochiai(shift_left.id, summary) == 1.0
    assert ochiai(total.id, summary) == 0.0


def test_statement_ochiai_scores_failed_only_statement_lines():
    _, _, _, by_name = _parsed_context()
    shift_left = by_name["shift_left"]
    test_shift = by_name["test_shift_left"]
    test_total = by_name["test_total"]
    shared_line = shift_left.start_line + 1
    failed_only_line = shift_left.start_line + 2

    summary = TestExecutionSummary(
        failed_tests={test_shift.id},
        passed_tests={test_total.id},
        coverage={
            test_shift.id: {shift_left.id},
            test_total.id: {shift_left.id},
        },
        covered_lines={
            test_shift.id: {shift_left.id: {shared_line, failed_only_line}},
            test_total.id: {shift_left.id: {shared_line}},
        },
    )

    assert round(statement_ochiai(shift_left.id, summary), 4) == 1.0


def test_branch_ochiai_scores_failed_only_branch_outcomes():
    _, _, _, by_name = _parsed_context()
    shift_left = by_name["shift_left"]
    test_shift = by_name["test_shift_left"]
    test_total = by_name["test_total"]
    branch_outcome = f"if:{shift_left.start_line + 1}:true"
    shared_outcome = f"loop:{shift_left.start_line + 2}:taken"

    summary = TestExecutionSummary(
        failed_tests={test_shift.id},
        passed_tests={test_total.id},
        coverage={
            test_shift.id: {shift_left.id},
            test_total.id: {shift_left.id},
        },
        branch_coverage={
            test_shift.id: {shift_left.id: {branch_outcome, shared_outcome}},
            test_total.id: {shift_left.id: {shared_outcome}},
        },
    )

    assert round(branch_ochiai(shift_left.id, summary), 4) == 1.0


def test_path_ochiai_scores_failed_only_path_fragments():
    _, _, _, by_name = _parsed_context()
    shift_left = by_name["shift_left"]
    test_shift = by_name["test_shift_left"]
    test_total = by_name["test_total"]
    failed_fragment = "test_shift_left -> shift_left"
    shared_fragment = "test_total -> shift_left"

    summary = TestExecutionSummary(
        failed_tests={test_shift.id},
        passed_tests={test_total.id},
        coverage={
            test_shift.id: {shift_left.id},
            test_total.id: {shift_left.id},
        },
        path_coverage={
            test_shift.id: {shift_left.id: {failed_fragment, shared_fragment}},
            test_total.id: {shift_left.id: {shared_fragment}},
        },
    )

    assert round(path_ochiai(shift_left.id, summary), 4) == 1.0


def test_fault_localizer_ranks_failed_covered_bug_function_first():
    _, program_graph, findings, by_name = _parsed_context()
    shift_left = by_name["shift_left"]
    test_shift = by_name["test_shift_left"]
    test_total = by_name["test_total"]
    total = by_name["Calculator.total"]
    add = by_name["Calculator.add"]

    summary = TestExecutionSummary(
        failed_tests={test_shift.id},
        passed_tests={test_total.id},
        coverage={
            test_shift.id: {shift_left.id},
            test_total.id: {total.id, add.id},
        },
        line_coverage={
            test_shift.id: {shift_left.id: 1.0},
            test_total.id: {total.id: 1.0, add.id: 1.0},
        },
        covered_lines={
            test_shift.id: {
                shift_left.id: {
                    shift_left.start_line + 1,
                    shift_left.start_line + 2,
                }
            },
            test_total.id: {
                total.id: {total.start_line + 1},
                add.id: {add.start_line + 1},
            },
        },
        branch_coverage={
            test_shift.id: {
                shift_left.id: {
                    f"if:{shift_left.start_line + 1}:true",
                }
            },
            test_total.id: {
                total.id: {f"if:{total.start_line + 1}:true"},
                add.id: {f"if:{add.start_line + 1}:true"},
            },
        },
        path_coverage={
            test_shift.id: {
                shift_left.id: {"test_shift_left -> shift_left"},
            },
            test_total.id: {
                total.id: {"test_total -> Calculator.total"},
                add.id: {"test_total -> Calculator.add"},
            },
        },
        traceback_function_ids={shift_left.id},
    )

    ranked = FaultLocalizer().rank(program_graph, findings, summary, top_k=3)

    assert ranked[0].function_name == "shift_left"
    assert ranked[0].signals["sbfl"] == 1.0
    assert ranked[0].signals["traceback_hit"] == 1.0
    assert ranked[0].signals["test_coverage"] == 1.0
    assert ranked[0].signals["line_coverage"] == 1.0
    assert ranked[0].signals["statement_sbfl"] == 1.0
    assert ranked[0].signals["branch_sbfl"] == 1.0
    assert ranked[0].signals["path_sbfl"] == 1.0
    assert ranked[0].signals["data_dependency"] > 0.0
    assert ranked[0].signals["control_flow"] > 0.0
    assert ranked[0].signals["pagerank"] > 0.0
    assert ranked[0].signals["proximity"] >= 0.0
    assert ranked[0].signals["centrality"] >= 0.0
    assert ranked[0].signals["patch_risk"] >= 0.0
    assert ranked[0].rank == 1


def test_fault_localizer_scores_semantic_similarity_from_failure_context():
    _, program_graph, findings, by_name = _parsed_context()
    shift_left = by_name["shift_left"]
    test_shift = by_name["test_shift_left"]

    summary = TestExecutionSummary(
        failed_tests={test_shift.id},
        coverage={test_shift.id: {shift_left.id}},
        traceback_function_ids={shift_left.id},
        test_names={test_shift.id: "test_shift_left"},
        failure_messages={
            test_shift.id: "IndexError while shifting left window in shift_left"
        },
    )

    with_semantic = FaultLocalizer().rank(program_graph, findings, summary, top_k=1)[0]
    without_semantic = FaultLocalizer(
        FaultLocalizationConfig(use_semantic_similarity=False)
    ).rank(program_graph, findings, summary, top_k=1)[0]

    assert with_semantic.function_name == "shift_left"
    assert with_semantic.signals["semantic"] > 0.0
    assert without_semantic.signals["semantic"] == 0.0
    assert with_semantic.score > without_semantic.score


def test_fault_localizer_can_ablate_line_coverage_signal():
    _, program_graph, findings, by_name = _parsed_context()
    shift_left = by_name["shift_left"]
    test_shift = by_name["test_shift_left"]

    summary = TestExecutionSummary(
        failed_tests={test_shift.id},
        coverage={test_shift.id: {shift_left.id}},
        line_coverage={test_shift.id: {shift_left.id: 1.0}},
        covered_lines={
            test_shift.id: {
                shift_left.id: {
                    shift_left.start_line + 1,
                    shift_left.start_line + 2,
                }
            }
        },
        traceback_function_ids={shift_left.id},
    )

    with_line = FaultLocalizer().rank(program_graph, findings, summary, top_k=1)[0]
    without_line = FaultLocalizer(
        FaultLocalizationConfig(use_line_coverage=False)
    ).rank(program_graph, findings, summary, top_k=1)[0]

    assert with_line.function_name == "shift_left"
    assert without_line.function_name == "shift_left"
    assert with_line.signals["line_coverage"] == 1.0
    assert with_line.signals["statement_sbfl"] == 1.0
    assert without_line.signals["statement_sbfl"] == 1.0
    assert with_line.signals["graph"] > without_line.signals["graph"]


def test_fault_localizer_can_ablate_branch_coverage_signal():
    _, program_graph, findings, by_name = _parsed_context()
    shift_left = by_name["shift_left"]
    test_shift = by_name["test_shift_left"]

    summary = TestExecutionSummary(
        failed_tests={test_shift.id},
        coverage={test_shift.id: {shift_left.id}},
        branch_coverage={
            test_shift.id: {
                shift_left.id: {
                    f"if:{shift_left.start_line + 1}:true",
                }
            }
        },
        traceback_function_ids={shift_left.id},
    )

    with_branch = FaultLocalizer().rank(program_graph, findings, summary, top_k=1)[0]
    without_branch = FaultLocalizer(
        FaultLocalizationConfig(use_branch_coverage=False)
    ).rank(program_graph, findings, summary, top_k=1)[0]

    assert with_branch.function_name == "shift_left"
    assert without_branch.function_name == "shift_left"
    assert with_branch.signals["branch_sbfl"] == 1.0
    assert without_branch.signals["branch_sbfl"] == 1.0
    assert with_branch.signals["graph"] > without_branch.signals["graph"]


def test_fault_localizer_can_ablate_path_coverage_signal():
    _, program_graph, findings, by_name = _parsed_context()
    shift_left = by_name["shift_left"]
    test_shift = by_name["test_shift_left"]

    summary = TestExecutionSummary(
        failed_tests={test_shift.id},
        coverage={test_shift.id: {shift_left.id}},
        path_coverage={
            test_shift.id: {
                shift_left.id: {
                    "test_shift_left -> shift_left",
                }
            }
        },
        traceback_function_ids={shift_left.id},
    )

    with_path = FaultLocalizer().rank(program_graph, findings, summary, top_k=1)[0]
    without_path = FaultLocalizer(
        FaultLocalizationConfig(use_path_coverage=False)
    ).rank(program_graph, findings, summary, top_k=1)[0]

    assert with_path.function_name == "shift_left"
    assert without_path.function_name == "shift_left"
    assert with_path.signals["path_sbfl"] == 1.0
    assert without_path.signals["path_sbfl"] == 1.0
    assert with_path.signals["graph"] > without_path.signals["graph"]


def test_fault_localizer_scores_exception_path_fragments():
    _, program_graph, findings, by_name = _parsed_context()
    shift_left = by_name["shift_left"]
    test_shift = by_name["test_shift_left"]

    summary = TestExecutionSummary(
        failed_tests={test_shift.id},
        coverage={test_shift.id: {shift_left.id}},
        path_coverage={
            test_shift.id: {
                shift_left.id: {
                    "exception_path:test_shift_left -> helper -> shift_left:ValueError",
                }
            }
        },
        traceback_function_ids={shift_left.id},
    )

    with_path = FaultLocalizer().rank(program_graph, findings, summary, top_k=1)[0]
    without_path = FaultLocalizer(
        FaultLocalizationConfig(use_path_coverage=False)
    ).rank(program_graph, findings, summary, top_k=1)[0]

    assert with_path.function_name == "shift_left"
    assert with_path.signals["path_sbfl"] == 1.0
    assert with_path.signals["graph"] > without_path.signals["graph"]


def test_fault_localizer_scores_async_path_fragments():
    _, program_graph, findings, by_name = _parsed_context()
    shift_left = by_name["shift_left"]
    test_shift = by_name["test_shift_left"]

    summary = TestExecutionSummary(
        failed_tests={test_shift.id},
        coverage={test_shift.id: {shift_left.id}},
        path_coverage={
            test_shift.id: {
                shift_left.id: {
                    "asyncseq:test_shift_left -> async_wrapper -> shift_left",
                }
            }
        },
        traceback_function_ids={shift_left.id},
    )

    with_path = FaultLocalizer().rank(program_graph, findings, summary, top_k=1)[0]
    without_path = FaultLocalizer(
        FaultLocalizationConfig(use_path_coverage=False)
    ).rank(program_graph, findings, summary, top_k=1)[0]

    assert with_path.function_name == "shift_left"
    assert with_path.signals["path_sbfl"] == 1.0
    assert with_path.signals["graph"] > without_path.signals["graph"]


def test_fault_localizer_can_ablate_data_dependency_signal():
    _, program_graph, findings, by_name = _parsed_context()
    shift_left = by_name["shift_left"]
    test_shift = by_name["test_shift_left"]

    summary = TestExecutionSummary(
        failed_tests={test_shift.id},
        coverage={test_shift.id: {shift_left.id}},
        line_coverage={test_shift.id: {shift_left.id: 1.0}},
        traceback_function_ids={shift_left.id},
    )

    with_data = FaultLocalizer().rank(program_graph, findings, summary, top_k=1)[0]
    without_data = FaultLocalizer(
        FaultLocalizationConfig(use_data_dependency=False)
    ).rank(program_graph, findings, summary, top_k=1)[0]

    assert with_data.function_name == "shift_left"
    assert without_data.function_name == "shift_left"
    assert with_data.signals["data_dependency"] > 0.0
    assert with_data.signals["graph"] > without_data.signals["graph"]


def test_fault_localizer_data_dependency_includes_cross_function_flow():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def add_one(value):\n"
            "    result = value + 1\n"
            "    return result\n\n"
            "def pipeline(items):\n"
            "    seed = items[0]\n"
            "    shifted = add_one(seed)\n"
            "    return shifted\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls)
    program_graph = build_program_graph(parsed, call_graph)
    findings = RuleBasedBugDetector().detect(parsed.functions)

    with_data = FaultLocalizer().rank(program_graph, findings)
    without_data = FaultLocalizer(
        FaultLocalizationConfig(use_data_dependency=False)
    ).rank(program_graph, findings)

    pipeline = next(item for item in with_data if item.function_name == "pipeline")
    ablated_pipeline = next(
        item for item in without_data if item.function_name == "pipeline"
    )

    assert any(
        edge["type"] == "arg_flows_to_param"
        and edge.get("source_variable") == "seed"
        and edge.get("target_variable") == "value"
        for edge in program_graph.edges
    )
    assert any(
        edge["type"] == "return_flows_to_var"
        and edge.get("target_variable") == "shifted"
        for edge in program_graph.edges
    )
    assert pipeline.signals["data_dependency"] > 0.0
    assert pipeline.signals["graph"] > ablated_pipeline.signals["graph"]


def test_fault_localizer_data_dependency_includes_subscript_key_flow():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def score_for(scores, name):\n"
            "    return scores[name]\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls)
    program_graph = build_program_graph(parsed, call_graph)
    findings = RuleBasedBugDetector().detect(parsed.functions)

    with_data = FaultLocalizer().rank(program_graph, findings)
    without_data = FaultLocalizer(
        FaultLocalizationConfig(use_data_dependency=False)
    ).rank(program_graph, findings)

    score_for = next(item for item in with_data if item.function_name == "score_for")
    ablated_score_for = next(
        item for item in without_data if item.function_name == "score_for"
    )

    assert any(
        edge["type"] == "key_flows_to_subscript"
        and edge.get("key_variable") == "name"
        and edge.get("mapping_variable") == "scores"
        for edge in program_graph.edges
    )
    assert score_for.signals["data_dependency"] > 0.0
    assert score_for.signals["graph"] > ablated_score_for.signals["graph"]


def test_fault_localizer_can_ablate_control_flow_signal():
    _, program_graph, findings, by_name = _parsed_context()
    shift_left = by_name["shift_left"]
    test_shift = by_name["test_shift_left"]

    summary = TestExecutionSummary(
        failed_tests={test_shift.id},
        coverage={test_shift.id: {shift_left.id}},
        line_coverage={test_shift.id: {shift_left.id: 1.0}},
        traceback_function_ids={shift_left.id},
    )

    with_control = FaultLocalizer().rank(program_graph, findings, summary, top_k=1)[0]
    without_control = FaultLocalizer(
        FaultLocalizationConfig(use_control_flow=False)
    ).rank(program_graph, findings, summary, top_k=1)[0]

    assert with_control.function_name == "shift_left"
    assert without_control.function_name == "shift_left"
    assert with_control.signals["control_flow"] > 0.0
    assert with_control.signals["graph"] > without_control.signals["graph"]


def test_fault_localizer_can_ablate_pagerank_signal():
    _, program_graph, findings, by_name = _parsed_context()
    shift_left = by_name["shift_left"]
    test_shift = by_name["test_shift_left"]

    summary = TestExecutionSummary(
        failed_tests={test_shift.id},
        coverage={test_shift.id: {shift_left.id}},
        line_coverage={test_shift.id: {shift_left.id: 1.0}},
        traceback_function_ids={shift_left.id},
    )

    with_pagerank = FaultLocalizer().rank(program_graph, findings, summary, top_k=1)[0]
    without_pagerank = FaultLocalizer(
        FaultLocalizationConfig(use_pagerank=False)
    ).rank(program_graph, findings, summary, top_k=1)[0]

    assert with_pagerank.function_name == "shift_left"
    assert without_pagerank.function_name == "shift_left"
    assert with_pagerank.signals["pagerank"] > 0.0
    assert with_pagerank.signals["graph"] > without_pagerank.signals["graph"]


def test_fault_localizer_scores_cross_file_caller_impact():
    parsed = RepoParser().parse(Path("datasets/toy_bugs/cross_file_repo"))
    call_graph = build_call_graph(parsed.functions, parsed.calls)
    program_graph = build_program_graph(parsed, call_graph)
    findings = RuleBasedBugDetector().detect(parsed.functions)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    shift_left = by_name["shift_left"]
    test_normalize = by_name["test_normalize_window"]

    summary = TestExecutionSummary(
        failed_tests={test_normalize.id},
        coverage={test_normalize.id: {shift_left.id}},
        traceback_function_ids={shift_left.id},
    )

    with_impact = FaultLocalizer().rank(program_graph, findings, summary)
    without_impact = FaultLocalizer(
        FaultLocalizationConfig(use_caller_impact=False)
    ).rank(program_graph, findings, summary)
    target = next(item for item in with_impact if item.function_name == "shift_left")
    ablated_target = next(
        item for item in without_impact if item.function_name == "shift_left"
    )

    path = program_graph.shortest_path(
        test_normalize.id,
        shift_left.id,
        edge_types={"calls", "tested_by"},
    )
    path_names = [program_graph.functions[node_id].name for node_id in path or []]

    assert path_names == ["test_normalize_window", "normalize_window", "shift_left"]
    assert target.signals["caller_impact"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_transitive_async_caller_impact():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "leaf.py").write_text(
            "async def pick(values):\n"
            "    return values[1]\n",
            encoding="utf-8",
        )
        (repo / "pipeline.py").write_text(
            "from leaf import pick\n\n"
            "async def transform(values):\n"
            "    return await pick(values)\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from pipeline import transform\n\n"
            "async def run(values):\n"
            "    return await transform(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "async def test_run_short_values():\n"
            "    await run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    pick = by_name["pick"]
    transform = by_name["transform"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {pick.id}},
        traceback_function_ids={pick.id},
    )

    with_impact = FaultLocalizer().rank(program_graph, [], summary)
    without_impact = FaultLocalizer(
        FaultLocalizationConfig(use_caller_impact=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_impact if item.function_name == "pick")
    caller = next(item for item in with_impact if item.function_name == "transform")
    ablated_target = next(
        item for item in without_impact if item.function_name == "pick"
    )

    path = program_graph.shortest_path(
        test_run.id,
        pick.id,
        edge_types={"calls", "tested_by"},
    )
    path_names = [program_graph.functions[node_id].name for node_id in path or []]

    assert path_names == ["test_run_short_values", "run", "transform", "pick"]
    assert target.signals["caller_impact"] == 1.0
    assert caller.signals["caller_impact"] < target.signals["caller_impact"]
    assert target.signals["async_call"] > 0.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_method_receiver_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "service.py").write_text(
            "class Worker:\n"
            "    @classmethod\n"
            "    def run(cls, values):\n"
            "        return cls.compute(values)\n\n"
            "    @staticmethod\n"
            "    def compute(values):\n"
            "        return values[1]\n\n"
            "class Other:\n"
            "    @staticmethod\n"
            "    def compute(values):\n"
            "        return values[0]\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import Worker\n\n"
            "def test_run_short_values():\n"
            "    Worker.run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["Worker.compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_impact = FaultLocalizer().rank(program_graph, [], summary)
    without_impact = FaultLocalizer(
        FaultLocalizationConfig(use_caller_impact=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_impact if item.function_id == compute.id)
    ablated_target = next(
        item for item in without_impact if item.function_id == compute.id
    )
    path = program_graph.shortest_path(
        test_run.id,
        compute.id,
        edge_types={"calls", "tested_by"},
    )
    path_names = [program_graph.functions[node_id].name for node_id in path or []]

    assert any(
        edge["type"] == "calls"
        and edge["target"] == compute.id
        and edge.get("resolution") == "method_receiver"
        and edge.get("receiver_alias") == "cls"
        and edge.get("class_name") == "Worker"
        for edge in program_graph.edges
    )
    assert path_names == ["test_run_short_values", "run", "compute"]
    assert target.signals["caller_impact"] > 0.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_cross_module_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "core.py").write_text(
            "def compute(values):\n"
            "    return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "import core as core_mod\n\n"
            "def run(values):\n"
            "    return core_mod.compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "module_import_alias"
        and edge.get("import_module") == "core"
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_package_relative_module_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "core.py").write_text(
            "def compute(values):\n"
            "    return values[1]\n",
            encoding="utf-8",
        )
        (package / "service.py").write_text(
            "from . import core as core_mod\n\n"
            "def run(values):\n"
            "    return core_mod.compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from pkg.service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "module_import_alias"
        and edge.get("import_module") == "pkg.core"
        and edge.get("is_relative_import") is True
        and edge.get("package_distance") == 0
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_from_package_submodule_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "worker.py").write_text(
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from pkg import worker\n\n"
            "def run(values):\n"
            "    instance = worker.Worker()\n"
            "    return instance.compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["Worker.compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "Worker.compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "Worker.compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "instance_method"
        and edge.get("instance_alias") == "instance"
        and edge.get("class_name") == "Worker"
        and edge.get("import_alias") == "worker"
        and edge.get("import_module") == "pkg.worker"
        and edge.get("import_name") == "worker"
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_calibrates_module_dependency_by_package_distance():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        subpackage = package / "sub"
        package.mkdir()
        subpackage.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (subpackage / "__init__.py").write_text("", encoding="utf-8")
        (package / "near.py").write_text(
            "def compute_near(values):\n"
            "    return values[0]\n",
            encoding="utf-8",
        )
        (subpackage / "deep.py").write_text(
            "def compute_deep(values):\n"
            "    return values[1]\n",
            encoding="utf-8",
        )
        (package / "service.py").write_text(
            "from . import near\n"
            "from .sub import deep\n\n"
            "def run(values):\n"
            "    return near.compute_near(values) + deep.compute_deep(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from pkg.service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    near = by_name["compute_near"]
    deep = by_name["compute_deep"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {near.id, deep.id}},
        traceback_function_ids={near.id, deep.id},
    )

    ranked = FaultLocalizer().rank(program_graph, [], summary)
    near_result = next(item for item in ranked if item.function_name == "compute_near")
    deep_result = next(item for item in ranked if item.function_name == "compute_deep")
    near_edge = next(
        edge
        for edge in program_graph.edges
        if edge["type"] == "module_depends_on" and edge["target"] == near.id
    )
    deep_edge = next(
        edge
        for edge in program_graph.edges
        if edge["type"] == "module_depends_on" and edge["target"] == deep.id
    )

    assert near_edge["package_distance"] == 0
    assert deep_edge["package_distance"] == 1
    assert deep_result.signals["module_dependency"] > near_result.signals[
        "module_dependency"
    ]


def test_fault_localizer_scores_dynamic_import_module_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "core.py").write_text(
            "def compute(values):\n"
            "    return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from importlib import import_module as load_module\n\n"
            "PREFIX = 'co'\n\n"
            "def run(values):\n"
            "    suffix = 're'\n"
            "    module_name = f'{PREFIX}{suffix}'\n"
            "    core_mod = load_module(module_name)\n"
            "    return core_mod.compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "compute"
    )

    assert any(
        edge["type"] == "imports"
        and edge["target"] == "import::core"
        and edge.get("import_kind") == "dynamic"
        for edge in program_graph.edges
    )
    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "module_import_alias"
        and edge.get("import_alias") == "core_mod"
        and edge.get("import_module") == "core"
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_direct_dynamic_import_member_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "core.py").write_text(
            "def compute(values):\n"
            "    return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from importlib import import_module as load_module\n\n"
            "PREFIX = 'co'\n\n"
            "def run(values):\n"
            "    suffix = 're'\n"
            "    module_name = f'{PREFIX}{suffix}'\n"
            "    return load_module(module_name).compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "module_import_alias"
        and edge.get("import_alias") == "load_module"
        and edge.get("import_module") == "core"
        and edge.get("import_kind") == "dynamic"
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_getattr_dynamic_import_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "core.py").write_text(
            "def compute(values):\n"
            "    return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from importlib import import_module as load_module\n\n"
            "PREFIX = 'co'\n\n"
            "def run(values):\n"
            "    suffix = 're'\n"
            "    module_name = f'{PREFIX}{suffix}'\n"
            "    prefix = 'com'\n"
            "    member = f'{prefix}pute'\n"
            "    return getattr(load_module(module_name), member)(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "module_import_alias"
        and edge.get("import_alias") == "load_module"
        and edge.get("import_module") == "core"
        and edge.get("import_kind") == "dynamic"
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_builtin_import_fromlist_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "core.py").write_text(
            "def compute(values):\n"
            "    return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "def run(values):\n"
            "    module_name = 'pkg.core'\n"
            "    return __import__(module_name, fromlist=['compute']).compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "module_import_alias"
        and edge.get("import_alias") == "__import__"
        and edge.get("import_module") == "pkg.core"
        and edge.get("import_kind") == "dynamic"
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_imported_instance_method_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "worker.py").write_text(
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from worker import Worker\n\n"
            "def run(values):\n"
            "    worker = Worker()\n"
            "    return worker.compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["Worker.compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "Worker.compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "Worker.compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "instance_method"
        and edge.get("instance_alias") == "worker"
        and edge.get("class_name") == "Worker"
        and edge.get("import_module") == "worker"
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_dynamic_imported_class_alias_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "worker.py").write_text(
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from importlib import import_module as load_module\n\n"
            "def run(values):\n"
            "    module_name = 'worker'\n"
            "    class_name = 'Worker'\n"
            "    WorkerAlias = getattr(load_module(module_name), class_name)\n"
            "    worker = WorkerAlias()\n"
            "    return worker.compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["Worker.compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "Worker.compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "Worker.compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "instance_method"
        and edge.get("instance_alias") == "worker"
        and edge.get("class_name") == "Worker"
        and edge.get("import_alias") == "WorkerAlias"
        and edge.get("import_module") == "worker"
        and edge.get("import_name") == "Worker"
        and edge.get("import_kind") == "dynamic_member"
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_star_imported_class_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "worker.py").write_text(
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from worker import *\n\n"
            "def run(values):\n"
            "    worker = Worker()\n"
            "    return worker.compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["Worker.compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "Worker.compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "Worker.compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "instance_method"
        and edge.get("instance_alias") == "worker"
        and edge.get("class_name") == "Worker"
        and edge.get("import_alias") == "Worker"
        and edge.get("import_module") == "worker"
        and edge.get("import_name") == "Worker"
        and edge.get("import_kind") == "static"
        and edge.get("is_star_import") is True
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_module_all_star_import_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "worker.py").write_text(
            "__all__ = ['Worker']\n\n"
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n\n"
            "class HiddenWorker:\n"
            "    def compute(self, values):\n"
            "        return values[0]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from worker import *\n\n"
            "def run(values):\n"
            "    worker = Worker()\n"
            "    return worker.compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["Worker.compute"]
    hidden_compute = by_name["HiddenWorker.compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    ranked = FaultLocalizer().rank(program_graph, [], summary)
    target = next(item for item in ranked if item.function_name == "Worker.compute")

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "instance_method"
        and edge.get("import_alias") == "Worker"
        and edge.get("import_module") == "worker"
        and edge.get("import_name") == "Worker"
        and edge.get("is_star_import") is True
        and edge.get("star_import_uses_all") is True
        for edge in program_graph.edges
    )
    assert not any(
        edge["type"] == "module_depends_on"
        and edge["target"] == hidden_compute.id
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0


def test_fault_localizer_scores_package_reexport_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "worker.py").write_text(
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (package / "__init__.py").write_text(
            "from .worker import Worker as ExportedWorker\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from pkg import ExportedWorker\n\n"
            "def run(values):\n"
            "    worker = ExportedWorker()\n"
            "    return worker.compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["Worker.compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "Worker.compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "Worker.compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "instance_method"
        and edge.get("import_alias") == "ExportedWorker"
        and edge.get("import_module") == "pkg"
        and edge.get("import_name") == "ExportedWorker"
        and edge.get("is_reexport") is True
        and edge.get("reexport_module") == "pkg.worker"
        and edge.get("reexport_name") == "Worker"
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_imported_symbol_alias_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "worker.py").write_text(
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (package / "__init__.py").write_text("", encoding="utf-8")
        (repo / "service.py").write_text(
            "from pkg import worker\n\n"
            "Handler = worker.Worker\n\n"
            "def run(values):\n"
            "    instance = Handler()\n"
            "    return instance.compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["Worker.compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "Worker.compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "Worker.compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "instance_method"
        and edge.get("import_alias") == "Handler"
        and edge.get("import_module") == "pkg.worker"
        and edge.get("import_name") == "Worker"
        and edge.get("is_symbol_alias") is True
        and edge.get("symbol_alias_source") == "worker.Worker"
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_function_local_symbol_alias_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "worker.py").write_text(
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (package / "__init__.py").write_text("", encoding="utf-8")
        (repo / "service.py").write_text(
            "from pkg import worker\n\n"
            "def run(values):\n"
            "    Handler = worker.Worker\n"
            "    instance = Handler()\n"
            "    return instance.compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["Worker.compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "Worker.compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "Worker.compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "instance_method"
        and edge.get("import_alias") == "Handler"
        and edge.get("import_module") == "pkg.worker"
        and edge.get("import_name") == "Worker"
        and edge.get("is_symbol_alias") is True
        and edge.get("symbol_alias_scope") == "local"
        and edge.get("symbol_alias_source") == "worker.Worker"
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_branch_merged_local_alias_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "worker.py").write_text(
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (package / "__init__.py").write_text("", encoding="utf-8")
        (repo / "service.py").write_text(
            "from pkg import worker\n\n"
            "def run(flag, values):\n"
            "    if flag:\n"
            "        Handler = worker.Worker\n"
            "    else:\n"
            "        Handler = worker.Worker\n"
            "    instance = Handler()\n"
            "    return instance.compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "def test_run_short_values():\n"
            "    run(True, [1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["Worker.compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "Worker.compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "Worker.compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "instance_method"
        and edge.get("import_alias") == "Handler"
        and edge.get("import_module") == "pkg.worker"
        and edge.get("import_name") == "Worker"
        and edge.get("is_symbol_alias") is True
        and edge.get("symbol_alias_scope") == "local"
        and edge.get("symbol_alias_source") == "worker.Worker"
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_try_merged_local_alias_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "worker.py").write_text(
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (package / "__init__.py").write_text("", encoding="utf-8")
        (repo / "service.py").write_text(
            "from pkg import worker\n\n"
            "def run(values):\n"
            "    try:\n"
            "        Handler = worker.Worker\n"
            "    except ValueError:\n"
            "        Handler = worker.Worker\n"
            "    instance = Handler()\n"
            "    return instance.compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["Worker.compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "Worker.compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "Worker.compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "instance_method"
        and edge.get("import_alias") == "Handler"
        and edge.get("import_module") == "pkg.worker"
        and edge.get("import_name") == "Worker"
        and edge.get("is_symbol_alias") is True
        and edge.get("symbol_alias_scope") == "local"
        and edge.get("symbol_alias_source") == "worker.Worker"
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_loop_preserved_local_alias_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "worker.py").write_text(
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (package / "__init__.py").write_text("", encoding="utf-8")
        (repo / "service.py").write_text(
            "from pkg import worker\n\n"
            "def run(values):\n"
            "    Handler = worker.Worker\n"
            "    for item in values:\n"
            "        item = item\n"
            "    instance = Handler()\n"
            "    return instance.compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["Worker.compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "Worker.compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "Worker.compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "instance_method"
        and edge.get("import_alias") == "Handler"
        and edge.get("import_module") == "pkg.worker"
        and edge.get("import_name") == "Worker"
        and edge.get("is_symbol_alias") is True
        and edge.get("symbol_alias_scope") == "local"
        and edge.get("symbol_alias_source") == "worker.Worker"
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_self_attribute_instance_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "worker.py").write_text(
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from worker import Worker\n\n"
            "class Service:\n"
            "    def __init__(self):\n"
            "        self.worker = Worker()\n\n"
            "    def run(self, values):\n"
            "        return self.worker.compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import Service\n\n"
            "def test_run_short_values():\n"
            "    Service().run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["Worker.compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "Worker.compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "Worker.compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "instance_method"
        and edge.get("instance_alias") == "self.worker"
        and edge.get("class_name") == "Worker"
        and edge.get("import_module") == "worker"
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_super_method_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "base.py").write_text(
            "class Base:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from base import Base\n\n"
            "class Child(Base):\n"
            "    def run(self, values):\n"
            "        return super().compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import Child\n\n"
            "def test_run_short_values():\n"
            "    Child().run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["Base.compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "Base.compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "Base.compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "super_method"
        and edge.get("class_name") == "Child"
        and edge.get("base_class") == "Base"
        and edge.get("base_module") == "base"
        and edge.get("import_module") == "base"
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_context_manager_instance_dependency_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "worker.py").write_text(
            "class Worker:\n"
            "    def __enter__(self):\n"
            "        return self\n\n"
            "    def __exit__(self, exc_type, exc, tb):\n"
            "        return False\n\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from worker import Worker\n\n"
            "def run(values):\n"
            "    with Worker() as worker:\n"
            "        return worker.compute(values)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "def test_run_short_values():\n"
            "    run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    compute = by_name["Worker.compute"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {compute.id}},
        traceback_function_ids={compute.id},
    )

    with_module = FaultLocalizer().rank(program_graph, [], summary)
    without_module = FaultLocalizer(
        FaultLocalizationConfig(use_module_dependency=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_module if item.function_name == "Worker.compute")
    ablated_target = next(
        item for item in without_module if item.function_name == "Worker.compute"
    )

    assert any(
        edge["type"] == "module_depends_on"
        and edge["target"] == compute.id
        and edge.get("resolution") == "instance_method"
        and edge.get("instance_alias") == "worker"
        and edge.get("class_name") == "Worker"
        and edge.get("import_module") == "worker"
        for edge in program_graph.edges
    )
    assert target.signals["module_dependency"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_async_await_call_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "client.py").write_text(
            "async def fetch(values):\n"
            "    return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from client import fetch\n\n"
            "async def run(values):\n"
            "    result = await fetch(values)\n"
            "    return result\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "async def test_run_short_values():\n"
            "    await run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    fetch = by_name["fetch"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {fetch.id}},
        traceback_function_ids={fetch.id},
    )

    with_async = FaultLocalizer().rank(program_graph, [], summary)
    without_async = FaultLocalizer(
        FaultLocalizationConfig(use_async_calls=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_async if item.function_name == "fetch")
    ablated_target = next(
        item for item in without_async if item.function_name == "fetch"
    )

    assert any(
        edge["type"] == "calls"
        and edge["target"] == fetch.id
        and edge.get("is_awaited") is True
        for edge in program_graph.edges
    )
    assert any(
        edge["type"] == "awaits"
        and edge["target"] == fetch.id
        and edge.get("callee") == "fetch"
        for edge in program_graph.edges
    )
    assert target.signals["async_call"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_asyncio_task_scheduling_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "client.py").write_text(
            "async def fetch(values):\n"
            "    return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from asyncio import create_task\n"
            "from client import fetch\n\n"
            "async def run(values):\n"
            "    task = create_task(fetch(values))\n"
            "    return await task\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "async def test_run_short_values():\n"
            "    await run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    fetch = by_name["fetch"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {fetch.id}},
        traceback_function_ids={fetch.id},
    )

    with_async = FaultLocalizer().rank(program_graph, [], summary)
    without_async = FaultLocalizer(
        FaultLocalizationConfig(use_async_calls=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_async if item.function_name == "fetch")
    ablated_target = next(
        item for item in without_async if item.function_name == "fetch"
    )

    assert any(
        edge["type"] == "calls"
        and edge["target"] == fetch.id
        and edge.get("async_kind") == "task"
        and edge.get("is_awaited") is False
        for edge in program_graph.edges
    )
    assert any(
        edge["type"] == "awaits"
        and edge["target"] == fetch.id
        and edge.get("async_kind") == "task"
        and edge.get("is_awaited") is False
        for edge in program_graph.edges
    )
    assert target.signals["async_call"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_scores_task_group_scheduling_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "client.py").write_text(
            "async def fetch(values):\n"
            "    return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "import asyncio\n"
            "from client import fetch\n\n"
            "async def run(values):\n"
            "    async with asyncio.TaskGroup() as group:\n"
            "        group.create_task(fetch(values))\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from service import run\n\n"
            "async def test_run_short_values():\n"
            "    await run([1])\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {
        function.metadata["qualified_name"]: function for function in parsed.functions
    }
    fetch = by_name["fetch"]
    test_run = by_name["test_run_short_values"]
    summary = TestExecutionSummary(
        failed_tests={test_run.id},
        coverage={test_run.id: {fetch.id}},
        traceback_function_ids={fetch.id},
    )

    with_async = FaultLocalizer().rank(program_graph, [], summary)
    without_async = FaultLocalizer(
        FaultLocalizationConfig(use_async_calls=False)
    ).rank(program_graph, [], summary)
    target = next(item for item in with_async if item.function_name == "fetch")
    ablated_target = next(
        item for item in without_async if item.function_name == "fetch"
    )

    assert any(
        edge["type"] == "calls"
        and edge["target"] == fetch.id
        and edge.get("async_kind") == "task"
        for edge in program_graph.edges
    )
    assert any(
        edge["type"] == "awaits"
        and edge["target"] == fetch.id
        and edge.get("async_kind") == "task"
        for edge in program_graph.edges
    )
    assert target.signals["async_call"] == 1.0
    assert target.signals["graph"] > ablated_target.signals["graph"]


def test_fault_localizer_excludes_test_functions_from_rankings():
    _, program_graph, findings, by_name = _parsed_context()
    shift_left = by_name["shift_left"]
    test_shift = by_name["test_shift_left"]

    summary = TestExecutionSummary(
        failed_tests={test_shift.id},
        coverage={test_shift.id: {shift_left.id}},
    )

    ranked = FaultLocalizer().rank(program_graph, findings, summary)

    assert all(not item.function_name.startswith("test_") for item in ranked)
