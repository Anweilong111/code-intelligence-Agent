from dataclasses import replace
from pathlib import Path
import importlib.util
import tempfile

from code_intelligence_agent.agents.bug_detector import RuleBasedBugDetector
from code_intelligence_agent.agents.multi_patch_repair import MultiPatchRepair
from code_intelligence_agent.agents.patch_generator import PatchGenerator
from code_intelligence_agent.agents.repair_loop import RepairLoop
from code_intelligence_agent.agents.reflector import ReflectionAgent
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.fault_localizer import FaultLocalizer
from code_intelligence_agent.core.models import ExecutionResult, PatchCandidate
from code_intelligence_agent.core.program_graph import ProgramGraph, build_program_graph
from code_intelligence_agent.core.repo_parser import RepoParser
from code_intelligence_agent.search.execution_feedback import analyze_execution_feedback
from code_intelligence_agent.search.failure_taxonomy import classify_execution_result
from code_intelligence_agent.tools.diff_utils import apply_patch_candidate
from code_intelligence_agent.tools.sandbox import Sandbox


FIXTURE = Path(__file__).parent / "fixtures" / "buggy_sample.py"


def test_patch_generator_creates_unified_diff_for_index_overrun():
    parsed = RepoParser().parse(FIXTURE)
    graph = build_call_graph(parsed.functions, parsed.calls)
    program_graph = build_program_graph(parsed, graph)
    detector = RuleBasedBugDetector()
    findings = detector.detect(parsed.functions)
    ranked = FaultLocalizer().rank(program_graph, findings)

    candidates = PatchGenerator().generate(FIXTURE.parent, parsed.functions, ranked)
    by_variant = {candidate.metadata.get("variant"): candidate for candidate in candidates}

    assert "shrink_range_upper_bound" in by_variant
    assert "overly_conservative_range_bound" in by_variant
    assert "range(len(values) - 1)" in by_variant["shrink_range_upper_bound"].new_source
    assert "--- a/buggy_sample.py" in by_variant["shrink_range_upper_bound"].diff
    shrink_metadata = by_variant["shrink_range_upper_bound"].metadata
    conservative_metadata = by_variant["overly_conservative_range_bound"].metadata
    assert shrink_metadata["rule_confidence"] > conservative_metadata["rule_confidence"]
    assert shrink_metadata["raw_rule_confidence"] == 0.85
    assert shrink_metadata["confidence_calibration"]["score"] == shrink_metadata["rule_confidence"]
    assert "positive_offset_index_evidence" in shrink_metadata["confidence_calibration"]["reasons"]


def test_apply_patch_candidate_rewrites_target_file():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        target = repo / "buggy_sample.py"
        target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

        parsed = RepoParser().parse(target)
        graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, graph)
        detector = RuleBasedBugDetector()
        findings = detector.detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidate = next(
            item
            for item in PatchGenerator().generate(repo, parsed.functions, ranked)
            if item.rule_id == "possible_index_overrun"
        )

        apply_patch_candidate(repo, candidate)

        assert "range(len(values) - 1)" in target.read_text(encoding="utf-8")


def test_patch_generator_rewrites_always_true_len_check_to_truthiness_guard():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        target = repo / "guards.py"
        target.write_text(
            "def require_scheme(scheme):\n"
            "    if len(scheme) >= 0:\n"
            "        return scheme\n"
            "    raise ValueError(\"missing\")\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(repo)
        graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, graph)
        findings = RuleBasedBugDetector().detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidate = next(
            item
            for item in PatchGenerator().generate(repo, parsed.functions, ranked)
            if item.rule_id == "always_true_len_check"
        )

        assert "if bool(scheme):" in candidate.new_source
        assert "len(scheme) > 0" not in candidate.new_source


def test_patch_generator_preserves_mutable_default_container_type():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        target = repo / "mutable_defaults.py"
        target.write_text(
            "def remember_mapping(key, value, cache={}):\n"
            "    cache[key] = value\n"
            "    return cache\n\n\n"
            "def remember_set(item, seen=set()):\n"
            "    seen.add(item)\n"
            "    return seen\n\n\n"
            "def remember_list(item, bucket=list()):\n"
            "    bucket.append(item)\n"
            "    return bucket\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(repo)
        graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, graph)
        findings = RuleBasedBugDetector().detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = PatchGenerator().generate(repo, parsed.functions, ranked)
        by_function = {
            candidate.target_function_name: candidate for candidate in candidates
        }

        assert by_function["remember_mapping"].rule_id == "mutable_default_arg"
        assert "cache = {}" in by_function["remember_mapping"].new_source
        assert "cache = []" not in by_function["remember_mapping"].new_source
        assert by_function["remember_set"].rule_id == "mutable_default_arg"
        assert "seen = set()" in by_function["remember_set"].new_source
        assert by_function["remember_list"].rule_id == "mutable_default_arg"
        assert "bucket = list()" in by_function["remember_list"].new_source


def test_sandbox_runs_pytest_after_applying_patch_when_pytest_available():
    if importlib.util.find_spec("pytest") is None:
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def shift_left(values):\n"
            "    for i in range(len(values)):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "from sample import shift_left\n\n"
            "def test_shift_left():\n"
            "    assert shift_left([1, 2, 3])[:2] == [2, 3]\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(repo)
        graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, graph)
        detector = RuleBasedBugDetector()
        findings = detector.detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidate = PatchGenerator().generate(repo, parsed.functions, ranked)[0]

        sandbox = Sandbox(timeout=10)
        before = sandbox.run_tests(repo)
        after = sandbox.apply_patch_and_test(repo, candidate)

        assert before.success is False
        assert after.success is True


def test_sandbox_bootstraps_pytest_outside_repo_when_package_shadows_pytest_dependency():
    if importlib.util.find_spec("pytest") is None:
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pluggy"
        package.mkdir()
        (package / "__init__.py").write_text(
            "def local_value():\n"
            "    return 'local'\n",
            encoding="utf-8",
        )
        (repo / "test_shadowed_dependency.py").write_text(
            "def test_pytest_can_start_with_local_pluggy_package():\n"
            "    assert True\n",
            encoding="utf-8",
        )

        result = Sandbox(timeout=10).run_tests(repo)

        assert result.success is True
        assert result.passed == 1
        assert "ImportError: cannot import name 'HookimplMarker'" not in (
            result.stdout + result.stderr
        )


def test_sandbox_classifies_stale_patch_as_patch_apply_error():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def value():\n"
            "    return 1\n",
            encoding="utf-8",
        )
        candidate = PatchCandidate(
            id="stale_patch",
            target_file=str(repo / "sample.py"),
            relative_file_path="sample.py",
            target_function_id="sample.py::value",
            target_function_name="value",
            rule_id="stale_patch",
            description="Patch no longer matches the target source.",
            old_source="def value():\n    return 2\n",
            new_source="def value():\n    return 3\n",
            diff="",
        )

        result = Sandbox(timeout=10).apply_patch_and_test(repo, candidate)

        assert result.success is False
        assert result.returncode == -1
        assert classify_execution_result(result) == "patch_apply_error"


def test_multi_patch_repair_combines_independent_function_fixes():
    if importlib.util.find_spec("pytest") is None:
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def shift_left(values):\n"
            "    for i in range(len(values)):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n\n\n"
            "def has_items(values):\n"
            "    if len(values) >= 0:\n"
            "        return True\n"
            "    return False\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "from sample import has_items, shift_left\n\n"
            "def test_shift_left():\n"
            "    assert shift_left([1, 2, 3])[:2] == [2, 3]\n\n"
            "def test_has_items_empty():\n"
            "    assert has_items([]) is False\n\n"
            "def test_has_items_non_empty():\n"
            "    assert has_items([1]) is True\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(repo)
        graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, graph)
        findings = RuleBasedBugDetector().detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = PatchGenerator().generate(repo, parsed.functions, ranked)
        localization_scores = {item.function_id: item.score for item in ranked}

        sandbox = Sandbox(timeout=10)
        single_result = sandbox.apply_patch_and_test(repo, candidates[0])
        result = MultiPatchRepair(sandbox=sandbox).run(
            repo,
            candidates,
            localization_scores=localization_scores,
        )

        assert single_result.success is False
        assert result.success is True
        assert result.bundle_size == 2
        assert result.attempts[0].success is True
        assert result.attempts[0].rule_ids == [
            "always_true_len_check",
            "possible_index_overrun",
        ]


def test_multi_patch_repair_prioritizes_graph_connected_bundle():
    graph = ProgramGraph()
    graph.add_edge("b_function", "z_function", "calls")
    graph.add_edge(
        "b_function",
        "z_function",
        "module_depends_on",
        package_distance=2,
        is_relative_import=True,
    )
    graph.add_edge(
        "b_function::var::value",
        "z_function::var::value",
        "arg_flows_to_param",
        caller_function_id="b_function",
        callee_function_id="z_function",
    )
    graph.add_edge(
        "b_function::key::name",
        "b_function::mapping::scores",
        "key_flows_to_subscript",
        function_id="b_function",
        key_variable="name",
        mapping_variable="scores",
        line=3,
    )
    candidates = [
        _candidate("a_patch", "a_function", "a.py"),
        _candidate("b_patch", "b_function", "b.py"),
        _candidate("z_patch", "z_function", "z.py"),
    ]
    sandbox = BundleRecordingSandbox(success_ids={"b_patch", "z_patch"})

    result = MultiPatchRepair(sandbox=sandbox, max_attempts=1).run(
        Path("."),
        candidates,
        localization_scores={
            "a_function": 0.5,
            "b_function": 0.5,
            "z_function": 0.5,
        },
        program_graph=graph,
    )

    assert result.success is True
    assert sandbox.seen == [{"b_patch", "z_patch"}]
    assert result.attempts[0].graph_evidence["direct_call_edges"] == 1
    assert result.attempts[0].graph_evidence["module_dependency_edges"] == 1
    assert result.attempts[0].graph_evidence["relative_import_edges"] == 1
    assert result.attempts[0].graph_evidence["max_package_distance"] == 2
    assert result.attempts[0].graph_evidence["package_distance_bonus"] == 0.02
    assert result.attempts[0].graph_evidence["data_flow_edges"] == 1
    assert result.attempts[0].graph_evidence["key_flow_edges"] == 1
    assert result.attempts[0].graph_evidence["cross_file"] is True
    assert result.attempts[0].graph_evidence["graph_bonus"] > 0.0

    no_graph_sandbox = BundleRecordingSandbox(success_ids={"b_patch", "z_patch"})
    no_graph_result = MultiPatchRepair(
        sandbox=no_graph_sandbox,
        max_attempts=1,
        use_graph_bundle_ranking=False,
    ).run(
        Path("."),
        candidates,
        localization_scores={
            "a_function": 0.5,
            "b_function": 0.5,
            "z_function": 0.5,
        },
        program_graph=graph,
    )

    assert no_graph_result.success is False
    assert no_graph_sandbox.seen == [{"a_patch", "b_patch"}]
    assert no_graph_result.attempts[0].graph_evidence["graph_bonus"] == 0.0


def test_multi_patch_repair_ignores_self_edges_for_bundle_graph_bonus():
    graph = ProgramGraph()
    graph.add_edge("a_function", "a_function", "calls")
    graph.add_edge(
        "a_function",
        "a_function",
        "module_depends_on",
        package_distance=1,
        is_relative_import=True,
    )
    graph.add_edge(
        "a_function::var::value",
        "a_function::var::value",
        "arg_flows_to_param",
        caller_function_id="a_function",
        callee_function_id="a_function",
    )
    candidates = [
        _candidate("a_patch", "a_function", "a.py"),
        _candidate("b_patch", "b_function", "b.py"),
    ]
    sandbox = BundleRecordingSandbox(success_ids={"a_patch", "b_patch"})

    result = MultiPatchRepair(sandbox=sandbox, max_attempts=1).run(
        Path("."),
        candidates,
        localization_scores={
            "a_function": 0.5,
            "b_function": 0.5,
        },
        program_graph=graph,
    )

    assert result.success is True
    assert result.attempts[0].graph_evidence["direct_call_edges"] == 0
    assert result.attempts[0].graph_evidence["module_dependency_edges"] == 0
    assert result.attempts[0].graph_evidence["relative_import_edges"] == 0
    assert result.attempts[0].graph_evidence["data_flow_edges"] == 0
    assert result.attempts[0].graph_evidence["graph_bonus"] == 0.0


def test_multi_patch_repair_prioritizes_package_distance_bundle_bonus():
    graph = ProgramGraph()
    graph.add_edge(
        "near_a",
        "near_b",
        "module_depends_on",
        package_distance=0,
    )
    graph.add_edge(
        "deep_a",
        "deep_b",
        "module_depends_on",
        package_distance=4,
        is_relative_import=True,
    )
    candidates = [
        _candidate("deep_a_patch", "deep_a", "pkg/deep/a.py"),
        _candidate("deep_b_patch", "deep_b", "pkg/deep/sub/b.py"),
        _candidate("near_a_patch", "near_a", "pkg/a.py"),
        _candidate("near_b_patch", "near_b", "pkg/b.py"),
    ]
    sandbox = BundleRecordingSandbox(success_ids={"deep_a_patch", "deep_b_patch"})

    result = MultiPatchRepair(sandbox=sandbox, max_attempts=1).run(
        Path("."),
        candidates,
        localization_scores={
            "deep_a": 0.5,
            "deep_b": 0.5,
            "near_a": 0.5,
            "near_b": 0.5,
        },
        program_graph=graph,
    )

    assert result.success is True
    assert sandbox.seen == [{"deep_a_patch", "deep_b_patch"}]
    assert result.attempts[0].graph_evidence["max_package_distance"] == 4
    assert result.attempts[0].graph_evidence["package_distance_bonus"] == 0.04


def test_patch_generator_fixes_inplace_api_return_assignment():
    if importlib.util.find_spec("pytest") is None:
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def sorted_values(values):\n"
            "    ordered = values.sort()\n"
            "    return ordered\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "from sample import sorted_values\n\n"
            "def test_sorted_values():\n"
            "    assert sorted_values([3, 1, 2]) == [1, 2, 3]\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(repo)
        graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, graph)
        findings = RuleBasedBugDetector().detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = PatchGenerator().generate(repo, parsed.functions, ranked)
        candidate = next(
            item for item in candidates if item.rule_id == "inplace_api_return_value"
        )

        assert "values.sort()" in candidate.new_source
        assert "ordered = values" in candidate.new_source
        assert Sandbox(timeout=10).run_tests(repo).success is False
        assert Sandbox(timeout=10).apply_patch_and_test(repo, candidate).success is True


def test_patch_generator_fixes_stringified_numeric_value():
    if importlib.util.find_spec("pytest") is None:
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def middle_value(values):\n"
            "    index = str(len(values) // 2)\n"
            "    return values[index]\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "from sample import middle_value\n\n"
            "def test_middle_value():\n"
            "    assert middle_value([1, 2, 3]) == 2\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(repo)
        graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, graph)
        findings = RuleBasedBugDetector().detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = PatchGenerator().generate(repo, parsed.functions, ranked)
        candidate = next(
            item for item in candidates if item.rule_id == "stringified_numeric_value"
        )

        assert "index = len(values) // 2" in candidate.new_source
        assert Sandbox(timeout=10).run_tests(repo).success is False
        assert Sandbox(timeout=10).apply_patch_and_test(repo, candidate).success is True


def test_patch_generator_fixes_missing_len_zero_guard():
    if importlib.util.find_spec("pytest") is None:
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def average_value(values):\n"
            "    n = len(values)\n"
            "    return sum(values) / n\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "from sample import average_value\n\n"
            "def test_average_value_non_empty():\n"
            "    assert average_value([2, 4]) == 3\n\n"
            "def test_average_value_empty_raises_value_error():\n"
            "    try:\n"
            "        average_value([])\n"
            "    except ValueError:\n"
            "        return\n"
            "    raise AssertionError('empty input should raise ValueError')\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(repo)
        graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, graph)
        findings = RuleBasedBugDetector().detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = PatchGenerator().generate(repo, parsed.functions, ranked)
        missing_len_candidates = [
            item for item in candidates if item.rule_id == "missing_len_zero_guard"
        ]
        by_variant = {
            str(item.metadata.get("variant")): item for item in missing_len_candidates
        }
        candidate = by_variant["insert_len_zero_guard"]
        decoy = by_variant["return_default_on_empty"]

        assert "if not n:" in candidate.new_source
        assert "raise ValueError" in candidate.new_source
        assert "if not n:" in decoy.new_source
        assert "return 0" in decoy.new_source
        assert Sandbox(timeout=10).run_tests(repo).success is False
        assert Sandbox(timeout=10).apply_patch_and_test(repo, decoy).success is False
        assert Sandbox(timeout=10).apply_patch_and_test(repo, candidate).success is True


def test_patch_generator_uses_len_source_evidence_for_missing_len_guard():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "from string import ascii_uppercase\n\n"
            "def gronsfeld_like(text, key):\n"
            "    ascii_len = len(ascii_uppercase)\n"
            "    key_len = len(key)\n"
            "    keys = [int(char) for char in key]\n"
            "    encrypted = ''\n"
            "    for i, char in enumerate(text.upper()):\n"
            "        if char in ascii_uppercase:\n"
            "            new_position = (ascii_uppercase.index(char) + keys[i % key_len]) % ascii_len\n"
            "            encrypted += ascii_uppercase[new_position]\n"
            "    return encrypted\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(repo)
        graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, graph)
        findings = RuleBasedBugDetector().detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = PatchGenerator().generate(repo, parsed.functions, ranked)
        missing_len_candidates = [
            item for item in candidates if item.rule_id == "missing_len_zero_guard"
        ]
        key_len_candidates = [
            item
            for item in missing_len_candidates
            if item.metadata["finding_evidence"]["len_source"] == "key"
        ]
        by_variant = {
            str(item.metadata.get("variant")): item for item in key_len_candidates
        }
        candidate_ids = [item.id for item in missing_len_candidates]

        assert len(missing_len_candidates) >= 4
        assert len(candidate_ids) == len(set(candidate_ids))
        assert "if not key_len:" in by_variant["insert_len_zero_guard"].new_source
        assert "if not ascii_len:" not in by_variant["insert_len_zero_guard"].new_source
        assert "if not key_len:" in by_variant["return_default_on_empty"].new_source
        assert "if not ascii_len:" not in by_variant["return_default_on_empty"].new_source
        assert by_variant["insert_len_zero_guard"].metadata[
            "finding_evidence_fingerprint"
        ].startswith("ev_key_len_")


def test_patch_generator_reflects_failed_missing_len_guard_to_default_return():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def gronsfeld_like(text, key):\n"
            "    key_len = len(key)\n"
            "    encrypted = ''\n"
            "    for i, char in enumerate(text.upper()):\n"
            "        encrypted += key[i % key_len]\n"
            "    return encrypted\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(repo)
        graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, graph)
        findings = RuleBasedBugDetector().detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = PatchGenerator().generate(repo, parsed.functions, ranked)
        failed_candidate = next(
            item
            for item in candidates
            if item.rule_id == "missing_len_zero_guard"
            and item.metadata.get("variant") == "insert_len_zero_guard"
        )
        execution_result = ExecutionResult(
            success=False,
            returncode=1,
            stdout="FAILED test_sample.py::test_empty_key",
            stderr="",
            traceback="ZeroDivisionError: integer modulo by zero",
            passed=0,
            failed=1,
            timeout=False,
            command=["python", "-m", "pytest", "-q"],
        )

        refined = PatchGenerator().refine_many(
            repo_path=repo,
            previous_patch=failed_candidate,
            execution_result=execution_result,
            round_index=1,
            limit=1,
        )

        assert len(refined) == 1
        assert refined[0].metadata["variant"] == "reflection_return_default_on_empty"
        assert refined[0].metadata["reflection_parent_variant"] == (
            "insert_len_zero_guard"
        )
        assert "if not key_len:" in refined[0].new_source
        assert "return " in refined[0].new_source


def test_patch_generator_fixes_enumerate_start_zero_counter():
    if importlib.util.find_spec("pytest") is None:
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def iterator_average(iterable):\n"
            "    n = 0\n\n"
            "    def count_items():\n"
            "        nonlocal n\n"
            "        for n, value in enumerate(iterable, start=0):\n"
            "            yield value\n\n"
            "    total = sum(count_items())\n"
            "    return total / n\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "from sample import iterator_average\n\n"
            "def one_item():\n"
            "    yield 4\n\n"
            "def test_iterator_average_single_item():\n"
            "    assert iterator_average(one_item()) == 4\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(repo)
        graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, graph)
        findings = RuleBasedBugDetector().detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = PatchGenerator().generate(repo, parsed.functions, ranked)
        candidate = next(
            item for item in candidates if item.rule_id == "enumerate_start_zero_counter"
        )

        assert "enumerate(iterable, start=1)" in candidate.new_source
        assert Sandbox(timeout=10).run_tests(repo).success is False
        assert Sandbox(timeout=10).apply_patch_and_test(repo, candidate).success is True


def test_patch_generator_fixes_inverted_empty_guard():
    if importlib.util.find_spec("pytest") is None:
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def mean(values):\n"
            "    if values:\n"
            "        raise ValueError('empty input')\n"
            "    return sum(values) / len(values)\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "from sample import mean\n\n"
            "def test_mean_non_empty():\n"
            "    assert mean([1, 2, 3]) == 2\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(repo)
        graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, graph)
        findings = RuleBasedBugDetector().detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = PatchGenerator().generate(repo, parsed.functions, ranked)
        candidate = next(
            item for item in candidates if item.rule_id == "inverted_empty_guard"
        )

        assert "if not values:" in candidate.new_source
        assert Sandbox(timeout=10).run_tests(repo).success is False
        assert Sandbox(timeout=10).apply_patch_and_test(repo, candidate).success is True


def test_patch_generator_fixes_identity_comparison_literal():
    if importlib.util.find_spec("pytest") is None:
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def is_admin(token):\n"
            "    return token is 'admin'\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "from sample import is_admin\n\n"
            "def test_is_admin_uses_equality():\n"
            "    literal = 'admin'\n"
            "    value = ''.join(['ad', 'min'])\n"
            "    assert value == literal\n"
            "    assert value is not literal\n"
            "    assert is_admin(value) is True\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(repo)
        graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, graph)
        findings = RuleBasedBugDetector().detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = PatchGenerator().generate(repo, parsed.functions, ranked)
        candidate = next(
            item for item in candidates if item.rule_id == "identity_comparison_literal"
        )

        assert "token == 'admin'" in candidate.new_source
        assert Sandbox(timeout=10).run_tests(repo).success is False
        assert Sandbox(timeout=10).apply_patch_and_test(repo, candidate).success is True


def test_patch_generator_fixes_iterator_double_consumption():
    if importlib.util.find_spec("pytest") is None:
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def average_iterable(values):\n"
            "    total = sum(values)\n"
            "    count = len(list(values))\n"
            "    return total / count\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "from sample import average_iterable\n\n"
            "def one_two_three():\n"
            "    yield 1\n"
            "    yield 2\n"
            "    yield 3\n\n"
            "def test_average_iterable_generator():\n"
            "    assert average_iterable(one_two_three()) == 2\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(repo)
        graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, graph)
        findings = RuleBasedBugDetector().detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = PatchGenerator().generate(repo, parsed.functions, ranked)
        candidate = next(
            item for item in candidates if item.rule_id == "iterator_double_consumption"
        )

        assert "values = list(values)" in candidate.new_source
        assert "count = len(values)" in candidate.new_source
        assert Sandbox(timeout=10).run_tests(repo).success is False
        assert Sandbox(timeout=10).apply_patch_and_test(repo, candidate).success is True


def test_patch_generator_fixes_dict_missing_key_guard():
    if importlib.util.find_spec("pytest") is None:
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def score_for(scores, name):\n"
            "    return scores[name]\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "from sample import score_for\n\n"
            "def test_score_for_missing_key_default():\n"
            "    assert score_for({'alice': 3}, 'missing') == 0\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(repo)
        graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, graph)
        findings = RuleBasedBugDetector().detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = PatchGenerator().generate(repo, parsed.functions, ranked)
        candidate = next(
            item for item in candidates if item.rule_id == "dict_missing_key_guard"
        )

        assert "scores.get(name, 0)" in candidate.new_source
        assert Sandbox(timeout=10).run_tests(repo).success is False
        assert Sandbox(timeout=10).apply_patch_and_test(repo, candidate).success is True


def test_reflection_agent_classifies_failed_execution():
    if importlib.util.find_spec("pytest") is None:
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "test_failure.py").write_text(
            "def test_failure():\n    assert 1 == 2\n",
            encoding="utf-8",
        )
        result = Sandbox(timeout=10).run_tests(repo)
        decision = ReflectionAgent().reflect(
            patch=None,
            result=result,
            round_index=0,
            max_rounds=3,
        )

        assert decision.should_retry is True
        assert decision.error_type in {"AssertionError", "TestFailure"}


def test_execution_feedback_adds_structured_refinement_guidance():
    candidate = PatchCandidate(
        id="test",
        target_file="sample.py",
        relative_file_path="sample.py",
        target_function_id="sample.py::target",
        target_function_name="target",
        rule_id="test_rule",
        description="test candidate",
        old_source="def target():\n    return 1\n",
        new_source="def target():\n    return 2\n",
        diff="--- a/sample.py\n+++ b/sample.py\n",
    )
    test_failure = ExecutionResult(
        success=False,
        returncode=1,
        stdout="FAILED test_sample.py::test_target",
        stderr="AssertionError in target",
        traceback="Traceback\n  target()",
        passed=2,
        failed=1,
        timeout=False,
        command=[],
    )
    syntax_failure = ExecutionResult(
        success=False,
        returncode=1,
        stdout="",
        stderr="SyntaxError: invalid syntax",
        traceback="",
        passed=0,
        failed=0,
        timeout=False,
        command=[],
    )

    recoverable = analyze_execution_feedback(candidate, test_failure)
    hard_failure = analyze_execution_feedback(candidate, syntax_failure)

    assert recoverable.failure_stage == "test_assertion"
    assert recoverable.recoverability == "high"
    assert recoverable.target_traceback_hit is True
    assert recoverable.refinement_hints
    assert "failure_type=test_failure" in recoverable.prompt_summary
    assert hard_failure.failure_stage == "static_validation"
    assert hard_failure.recoverability == "low"
    assert any("syntactically valid" in hint for hint in hard_failure.refinement_hints)


def test_repair_loop_applies_candidate_until_success():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def shift_left(values):\n"
            "    for i in range(len(values)):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "from sample import shift_left\n\n"
            "def test_shift_left():\n"
            "    assert shift_left([1, 2, 3])[:2] == [2, 3]\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(repo)
        graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, graph)
        detector = RuleBasedBugDetector()
        findings = detector.detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = PatchGenerator().generate(repo, parsed.functions, ranked)

        result = RepairLoop(Sandbox(timeout=10), max_rounds=3).run(repo, candidates)

        assert result.success is True
        assert result.rounds == 1
        assert result.best_candidate is not None


def test_repair_loop_respects_test_args():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def shift_left(values):\n"
            "    for i in range(len(values)):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "from sample import shift_left\n\n"
            "def test_shift_left():\n"
            "    assert shift_left([1, 2, 3])[:2] == [2, 3]\n",
            encoding="utf-8",
        )
        (repo / "test_unrelated.py").write_text(
            "def test_unrelated_failure():\n"
            "    assert False\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(repo)
        graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, graph)
        detector = RuleBasedBugDetector()
        findings = detector.detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = PatchGenerator().generate(repo, parsed.functions, ranked)

        result = RepairLoop(Sandbox(timeout=10), max_rounds=3).run(
            repo,
            candidates,
            test_args=["test_sample.py"],
        )

        assert result.success is True


def test_patch_generator_refines_conservative_index_patch_after_failure():
    old_source = (
        "def pairwise_deltas(values):\n"
        "    deltas = []\n"
        "    for i in range(len(values)):\n"
        "        deltas.append(values[i + 1] - values[i])\n"
        "    return deltas\n"
    )
    failed_source = (
        "def pairwise_deltas(values):\n"
        "    deltas = []\n"
        "    for i in range(max(0, len(values) - 2)):\n"
        "        deltas.append(values[i + 1] - values[i])\n"
        "    return deltas\n"
    )
    previous = PatchCandidate(
        id="sample.py::pairwise_deltas::possible_index_overrun::overly",
        target_file="sample.py",
        relative_file_path="sample.py",
        target_function_id="sample.py::pairwise_deltas",
        target_function_name="pairwise_deltas",
        rule_id="possible_index_overrun",
        description="overly conservative near-miss",
        old_source=old_source,
        new_source=failed_source,
        diff="",
        metadata={
            "generator": "rule_based",
            "variant": "overly_conservative_range_bound",
            "confidence": 0.75,
            "rule_confidence": 0.75,
        },
    )
    failure = ExecutionResult(
        success=False,
        returncode=1,
        stdout="F",
        stderr="AssertionError: right edge was dropped",
        traceback="",
        passed=0,
        failed=1,
        timeout=False,
        command=[],
    )

    refined = PatchGenerator().refine(
        repo_path=Path("."),
        previous_patch=previous,
        execution_result=failure,
        round_index=1,
    )

    assert refined is not None
    assert refined.metadata["generator"] == "rule_based_reflection"
    assert refined.metadata["variant"] == "reflection_shrink_range_upper_bound"
    assert refined.metadata["reflection_parent_variant"] == (
        "overly_conservative_range_bound"
    )
    assert "range(len(values) - 1)" in refined.new_source
    assert "max(0, len(values) - 2)" not in refined.new_source


def test_repair_loop_expands_multiple_refined_children_per_failed_patch():
    sandbox = SingleCandidateSandbox(success_id="good_child")
    root = _repair_loop_candidate("root", "def f():\n    return 0\n")
    bad_child = _repair_loop_candidate("bad_child", "def f():\n    return 1\n")
    good_child = _repair_loop_candidate("good_child", "def f():\n    return 2\n")
    refiner = BatchRepairLoopRefiner([bad_child, good_child])

    result = RepairLoop(
        sandbox=sandbox,
        refiner=refiner,
        max_rounds=3,
        use_prior_ranking=False,
        refinement_width=2,
    ).run(Path("."), [root])

    assert result.success is True
    assert sandbox.seen == ["root", "bad_child", "good_child"]
    assert refiner.calls == [("root", 1, 2), ("bad_child", 2, 2)]
    assert result.best_candidate is not None
    assert result.best_candidate.id == "good_child"
    assert result.attempts[1].candidate.metadata["repair_loop_parent_id"] == "root"
    assert result.attempts[1].candidate.metadata["repair_loop_child_index"] == 0
    assert result.attempts[1].candidate.metadata["repair_loop_sibling_count"] == 2
    assert result.attempts[2].candidate.metadata["repair_loop_parent_id"] == "root"
    assert result.attempts[2].candidate.metadata["repair_loop_child_index"] == 1


def test_repair_loop_safety_gates_refined_children_before_sandbox():
    sandbox = SingleCandidateSandbox(success_id="good_child")
    root = _repair_loop_candidate("root", "def f():\n    return 0\n")
    invalid_child = _repair_loop_candidate("invalid_child", "def f(:\n")
    good_child = _repair_loop_candidate("good_child", "def f():\n    return 2\n")
    refiner = BatchRepairLoopRefiner([invalid_child, good_child])

    result = RepairLoop(
        sandbox=sandbox,
        refiner=refiner,
        max_rounds=3,
        use_prior_ranking=False,
        refinement_width=2,
    ).run(Path("."), [root])

    assert result.success is True
    assert sandbox.seen == ["root", "good_child"]
    blocked_attempt = result.attempts[1]
    assert blocked_attempt.candidate.id == "invalid_child"
    assert blocked_attempt.execution_result.command == ["safety_gate"]
    safety = blocked_attempt.candidate.metadata["safety_gate"]
    assert safety["status"] == "blocked"
    assert safety["ast_valid"] is False


def test_repair_loop_deduplicates_initial_candidates_before_sandbox():
    sandbox = SingleCandidateSandbox(success_id="never")
    primary = _repair_loop_candidate("dedupe_primary", "def f():\n    return 1\n")
    duplicate = replace(
        primary,
        id="dedupe_duplicate",
        metadata={**primary.metadata, "variant": "dedupe_duplicate"},
    )

    result = RepairLoop(
        sandbox=sandbox,
        max_rounds=2,
        use_prior_ranking=False,
    ).run(Path("."), [primary, duplicate])

    assert sandbox.seen == ["dedupe_primary"]
    assert result.rounds == 1
    deduplication = result.attempts[0].candidate.metadata["search_deduplication"]
    assert deduplication["canonical_id"] == "dedupe_primary"
    assert deduplication["duplicate_count"] == 1
    assert deduplication["duplicate_ids"] == ["dedupe_duplicate"]


class SingleCandidateSandbox:
    def __init__(self, success_id: str) -> None:
        self.success_id = success_id
        self.seen: list[str] = []

    def apply_patch_and_test(
        self,
        repo_path,
        candidate: PatchCandidate,
        test_args: list[str] | None = None,
    ) -> ExecutionResult:
        del repo_path, test_args
        self.seen.append(candidate.id)
        success = candidate.id == self.success_id
        return ExecutionResult(
            success=success,
            returncode=0 if success else 1,
            stdout="." if success else "F",
            stderr="" if success else "AssertionError",
            traceback="",
            passed=1 if success else 0,
            failed=0 if success else 1,
            timeout=False,
            command=[],
        )


class BatchRepairLoopRefiner:
    def __init__(self, children: list[PatchCandidate]) -> None:
        self.children = children
        self.calls: list[tuple[str, int, int]] = []

    def refine_many(
        self,
        repo_path,
        previous_patch: PatchCandidate,
        execution_result: ExecutionResult,
        round_index: int,
        limit: int = 1,
    ) -> list[PatchCandidate]:
        del repo_path, execution_result
        self.calls.append((previous_patch.id, round_index, limit))
        return self.children[:limit]


def _repair_loop_candidate(candidate_id: str, new_source: str) -> PatchCandidate:
    return PatchCandidate(
        id=candidate_id,
        target_file="sample.py",
        relative_file_path="sample.py",
        target_function_id="sample.py::f",
        target_function_name="f",
        rule_id="test_rule",
        description="test repair-loop candidate",
        old_source="def f():\n    return -1\n",
        new_source=new_source,
        diff=(
            "--- a/sample.py\n"
            "+++ b/sample.py\n"
            "-    return -1\n"
            f"+    return {candidate_id!r}\n"
        ),
        metadata={"variant": candidate_id},
    )


class BundleRecordingSandbox:
    def __init__(self, success_ids: set[str]) -> None:
        self.success_ids = success_ids
        self.seen: list[set[str]] = []

    def apply_patches_and_test(
        self,
        repo_path,
        candidates: list[PatchCandidate],
        test_args: list[str] | None = None,
    ) -> ExecutionResult:
        del repo_path, test_args
        candidate_ids = {candidate.id for candidate in candidates}
        self.seen.append(candidate_ids)
        success = candidate_ids == self.success_ids
        return ExecutionResult(
            success=success,
            returncode=0 if success else 1,
            stdout="." if success else "F",
            stderr="" if success else "AssertionError",
            traceback="",
            passed=1 if success else 0,
            failed=0 if success else 1,
            timeout=False,
            command=[],
        )


def _candidate(
    candidate_id: str,
    function_id: str,
    relative_file_path: str,
) -> PatchCandidate:
    return PatchCandidate(
        id=candidate_id,
        target_file=relative_file_path,
        relative_file_path=relative_file_path,
        target_function_id=function_id,
        target_function_name=function_id,
        rule_id="test_rule",
        description="test candidate",
        old_source="def f():\n    return 1\n",
        new_source="def f():\n    return 2\n",
        diff=(
            f"--- a/{relative_file_path}\n"
            f"+++ b/{relative_file_path}\n"
            "-    return 1\n"
            "+    return 2\n"
        ),
        metadata={"variant": candidate_id},
    )
