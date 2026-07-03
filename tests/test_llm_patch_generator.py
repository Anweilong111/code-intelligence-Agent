from pathlib import Path
from dataclasses import replace
import json
import tempfile

from code_intelligence_agent.agents.bug_detector import RuleBasedBugDetector
from code_intelligence_agent.agents.llm_client import SequenceLLMClient, StaticLLMClient
from code_intelligence_agent.agents.llm_patch_generator import (
    LLMPatchGenerator,
    build_patch_prompt,
    parse_fixed_source,
    parse_fixed_sources,
)
from code_intelligence_agent.agents.repair_loop import RepairLoop
from code_intelligence_agent.core.models import (
    CodeEntity,
    ExecutionResult,
    FaultLocalizationResult,
    PatchCandidate,
)
from code_intelligence_agent.evaluation.benchmark_loader import BenchmarkCase
from code_intelligence_agent.evaluation.benchmark_runner import BenchmarkRunner
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.fault_localizer import FaultLocalizer
from code_intelligence_agent.core.program_graph import build_program_graph
from code_intelligence_agent.core.repo_parser import RepoParser
from code_intelligence_agent.tools.sandbox import Sandbox


def test_parse_fixed_source_accepts_json_and_code_fence():
    source = "def f():\n    return 1\n"
    assert parse_fixed_source(json.dumps({"fixed_source": source})) == source
    assert (
        parse_fixed_source("```json\n" + json.dumps({"fixed_source": source}) + "\n```")
        == source
    )


def test_parse_fixed_sources_accepts_ranked_candidate_list():
    first = "def f():\n    return 1\n"
    second = "def f():\n    return 2\n"

    assert parse_fixed_sources(
        json.dumps({"fixed_sources": [first, second, 3, None]})
    ) == [first, second]
    assert parse_fixed_source(json.dumps({"fixed_sources": [first, second]})) == first


def test_llm_patch_generator_builds_candidate_from_fake_client():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        source_path = repo / "sample.py"
        source_path.write_text(
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
        fixed_source = (
            "def shift_left(values):\n"
            "    for i in range(len(values) - 1):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n"
        )
        client = StaticLLMClient(json.dumps({"fixed_source": fixed_source}))

        parsed = RepoParser().parse(repo)
        call_graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, call_graph)
        detector = RuleBasedBugDetector()
        findings = detector.detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = LLMPatchGenerator(client).generate(repo, parsed.functions, ranked)

        assert len(candidates) == 1
        assert candidates[0].rule_id == "llm_patch"
        assert candidates[0].metadata["generator"] == "llm"
        assert candidates[0].metadata["suspicious_rank"] == 1
        assert candidates[0].metadata["suspicious_top_k"] == 5
        assert candidates[0].metadata["constraint"] == "top_k_suspicious_minimal_diff"
        assert candidates[0].metadata["validation"]["valid"] is True
        assert candidates[0].metadata["validation"]["scope_limited"] is True
        assert "possible_index_overrun" in client.prompts[0]
        assert "top-5 suspicious functions" in client.prompts[0]
        assert Sandbox(timeout=10).apply_patch_and_test(repo, candidates[0]).success


def test_llm_patch_generator_accepts_multiple_candidates_from_one_response():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        source_path = repo / "sample.py"
        source_path.write_text(
            "def normalize(values):\n"
            "    return values[0]\n",
            encoding="utf-8",
        )
        function = _function(
            "normalize",
            source_path,
            1,
            2,
            "def normalize(values):\n    return values[0]\n",
        )
        first = "def normalize(values):\n    return values[0] if values else None\n"
        second = (
            "def normalize(values):\n"
            "    if not values:\n"
            "        return None\n"
            "    return values[0]\n"
        )
        client = StaticLLMClient(json.dumps({"fixed_sources": [first, second]}))
        repair_context = {
            "dynamic_evidence_level": "failing_tests",
            "dynamic_evidence_nodeids": {
                "test_empty": "tests/test_sample.py::test_empty"
            },
            "stdout": "F",
            "stderr": "IndexError: list index out of range",
            "traceback": "Traceback...",
            "public_api_evidence": {
                "trigger_expression": "normalize([])",
                "public_call_args": [[]],
            },
            "call_graph_context": {
                "callers": ["api.normalize_request"],
                "callees": [],
            },
            "previous_failed_patch_fingerprints": ["sha256:badpatch"],
        }

        candidates = LLMPatchGenerator(client).generate(
            repo,
            [function],
            [_ranked(function, rank=1)],
            limit=2,
            repair_context=repair_context,
        )

        assert len(candidates) == 2
        assert len(client.prompts) == 1
        prompt_payload = json.loads(client.prompts[0])
        assert prompt_payload["candidate_count"] == 2
        assert "fixed_sources" in prompt_payload["required_schema"]
        assert prompt_payload["failing_test_nodeids"] == [
            "tests/test_sample.py::test_empty"
        ]
        assert prompt_payload["failure_evidence"]["stderr"].startswith("IndexError")
        assert prompt_payload["public_api_evidence"]["trigger_expression"] == (
            "normalize([])"
        )
        assert prompt_payload["call_graph_context"]["callers"] == [
            "api.normalize_request"
        ]
        assert prompt_payload["previous_failed_patch_fingerprints"] == [
            "sha256:badpatch"
        ]
        assert candidates[0].id != candidates[1].id
        assert candidates[0].metadata["generator"] == "llm"
        assert candidates[0].metadata["candidate_id"] == candidates[0].id
        assert candidates[0].metadata["llm_candidate_index"] == 0
        assert candidates[1].metadata["llm_candidate_index"] == 1
        assert candidates[1].metadata["llm_candidate_count_requested"] == 2
        assert candidates[0].metadata["response_parse"] == {
            "status": "pass",
            "schema": "fixed_sources",
            "parsed_candidate_count": 2,
        }
        audit = candidates[0].metadata["prompt_context_audit"]
        assert audit["required_fields"] == {
            "top_k_suspicious_functions": True,
            "target_function_source": True,
            "failing_test_nodeid": True,
            "traceback_or_output_summary": True,
            "public_api_evidence": True,
            "dynamic_oracle": True,
            "call_graph_context": True,
            "previous_failed_patch_fingerprint": True,
        }
        assert audit["missing_fields"] == []


def test_llm_patch_prompt_marks_overlay_expected_exception_as_legacy_failure():
    source_path = Path("sample.py")
    function = _function(
        "safe_mean",
        source_path,
        1,
        2,
        "def safe_mean(values):\n    return sum(values) / len(values)\n",
    )
    repair_context = {
        "dynamic_evidence_level": "failing_tests",
        "recommended_validation_command": (
            "python -m pytest -q tests/test_overlay.py::test_empty_input"
        ),
        "overlay_case_context": {
            "rule_id": "missing_len_zero_guard",
            "expected_exception": "ZeroDivisionError",
            "public_api_evidence": {
                "trigger_expression": "safe_mean([])",
            },
        },
        "oracle_policy": {
            "expected_exception_semantics": (
                "legacy_failure_to_avoid_not_desired_exception"
            ),
        },
    }

    payload = json.loads(
        build_patch_prompt(
            function,
            _ranked(function, rank=1),
            repair_context=repair_context,
        )
    )

    assert payload["dynamic_oracle"] == repair_context
    assert (
        "legacy failure to avoid"
        in " ".join(payload["constraints"])
    )
    assert (
        "safe for those exact runtime arguments"
        in " ".join(payload["constraints"])
    )
    assert (
        payload["dynamic_oracle"]["oracle_policy"][
            "expected_exception_semantics"
        ]
        == "legacy_failure_to_avoid_not_desired_exception"
    )


def test_llm_patch_generator_filters_invalid_ast_candidate():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        source_path = repo / "sample.py"
        source_path.write_text(
            "def first():\n    return 1\n",
            encoding="utf-8",
        )
        functions = [
            _function("first", source_path, 1, 2, "def first():\n    return 1\n")
        ]
        ranked = [_ranked(functions[0], rank=1)]
        client = StaticLLMClient(json.dumps({"fixed_source": "def first(:\n"}))

        candidates = LLMPatchGenerator(client).generate(repo, functions, ranked)

        assert candidates == []


def test_llm_patch_generator_does_not_query_beyond_top_k_suspicious_functions():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        source_path = repo / "sample.py"
        source_path.write_text(
            "def first():\n    return 1\n\n"
            "def second():\n    return 2\n\n"
            "def third():\n    return 3\n",
            encoding="utf-8",
        )
        functions = [
            _function("first", source_path, 1, 2, "def first():\n    return 1\n"),
            _function("second", source_path, 4, 5, "def second():\n    return 2\n"),
            _function("third", source_path, 7, 8, "def third():\n    return 3\n"),
        ]
        ranked = [
            _ranked(functions[0], rank=1),
            _ranked(functions[1], rank=2),
            _ranked(functions[2], rank=3),
        ]
        client = SequenceLLMClient(
            [
                "not json",
                json.dumps({"fixed_source": functions[1].source}),
                json.dumps({"fixed_source": "def third():\n    return 4\n"}),
            ]
        )

        candidates = LLMPatchGenerator(client, top_k_functions=2).generate(
            repo,
            functions,
            ranked,
            limit=1,
        )

        assert candidates == []
        assert len(client.prompts) == 2
        assert "top-2 suspicious functions" in client.prompts[0]
        assert "third" not in "\n".join(client.prompts)


def test_llm_reflection_refines_failed_patch_until_success():
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
        wrong_source = (
            "def shift_left(values):\n"
            "    for i in range(len(values) - 2):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n"
        )
        fixed_source = (
            "def shift_left(values):\n"
            "    for i in range(len(values) - 1):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n"
        )
        client = SequenceLLMClient(
            [
                json.dumps({"fixed_source": wrong_source}),
                json.dumps({"fixed_source": fixed_source}),
            ]
        )
        generator = LLMPatchGenerator(client)

        parsed = RepoParser().parse(repo)
        call_graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, call_graph)
        detector = RuleBasedBugDetector()
        findings = detector.detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = generator.generate(repo, parsed.functions, ranked)

        result = RepairLoop(
            sandbox=Sandbox(timeout=10),
            refiner=generator,
            max_rounds=3,
        ).run(repo, candidates)

        assert result.success is True
        assert result.rounds == 2
        assert result.best_candidate is not None
        assert result.best_candidate.rule_id == "llm_reflection_patch"
        assert len(client.prompts) == 2
        assert "previous_diff" in client.prompts[1]
        assert "execution_result" in client.prompts[1]
        refinement_payload = json.loads(client.prompts[1])
        assert refinement_payload["execution_feedback"]["failure_type"] == "test_failure"
        assert (
            refinement_payload["execution_feedback"]["failure_stage"]
            == "test_assertion"
        )
        assert refinement_payload["failure_analysis"]["recoverability"] in {
            "medium",
            "high",
        }
        assert refinement_payload["failure_analysis"]["refinement_hints"]
        assert refinement_payload["execution_feedback"]["score"] > 0.0


def test_llm_reflection_can_generate_multiple_refined_candidates():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        source_path = repo / "sample.py"
        source_path.write_text(
            "def shift_left(values):\n"
            "    for i in range(len(values)):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n",
            encoding="utf-8",
        )
        original = source_path.read_text(encoding="utf-8")
        wrong = (
            "def shift_left(values):\n"
            "    for i in range(len(values) - 2):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n"
        )
        alternative = (
            "def shift_left(values):\n"
            "    for i in range(max(0, len(values) - 1)):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n"
        )
        fixed = (
            "def shift_left(values):\n"
            "    for i in range(len(values) - 1):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n"
        )
        previous = _patch_candidate(
            repo=repo,
            source_path=source_path,
            old_source=original,
            new_source=wrong,
        )
        refinement_context = {
            "available": True,
            "target": {
                "function_id": previous.target_function_id,
                "qualified_name": "shift_left",
                "file_path": previous.target_file,
            },
            "callers": [
                {
                    "function_id": "service.py::call_shift_left",
                    "qualified_name": "call_shift_left",
                    "file_path": "service.py",
                    "source_excerpt": "def call_shift_left(values):\n    return shift_left(values)",
                    "is_cross_file": True,
                }
            ],
            "callees": [],
            "module_dependencies": [],
            "data_flow_neighbors": [],
        }
        previous = replace(
            previous,
            metadata={
                **previous.metadata,
                "refinement_context": refinement_context,
                "patch_judgment": {
                    "score": 0.2,
                    "verdict": "reject",
                    "reason": "Fails the observed assertion.",
                    "risk": "medium",
                    "confidence": 0.8,
                    "agreement": "aligned",
                },
            },
        )
        execution_result = ExecutionResult(
            success=False,
            returncode=1,
            stdout="F",
            stderr="AssertionError",
            traceback="",
            passed=0,
            failed=1,
            timeout=False,
            command=[],
        )
        client = StaticLLMClient(json.dumps({"fixed_sources": [alternative, fixed]}))
        generator = LLMPatchGenerator(client)

        candidates = generator.refine_many(
            repo,
            previous,
            execution_result,
            round_index=1,
            limit=2,
        )

        assert len(candidates) == 2
        assert candidates[0].metadata["reflection_child_index"] == 0
        assert candidates[1].metadata["reflection_child_index"] == 1
        assert candidates[1].metadata["reflection_candidate_count_requested"] == 2
        assert (
            candidates[0].metadata["parent_execution_feedback"]["failure_type"]
            == "test_failure"
        )
        assert candidates[0].metadata["failure_analysis"]["failure_stage"] == (
            "test_assertion"
        )
        assert candidates[0].metadata["reflection_strategy"]["id"] == (
            "semantic_repair"
        )
        assert candidates[0].metadata["response_parse"]["status"] == "pass"
        assert candidates[0].metadata["reflection_prompt_context_audit"][
            "status"
        ] == "pass"
        assert candidates[0].metadata["failed_source_fingerprints"]
        assert candidates[0].metadata["source_fingerprint"] not in (
            candidates[0].metadata["failed_source_fingerprints"]
        )
        assert candidates[0].metadata["candidate_diversity"]["accepted"] is True
        assert candidates[0].metadata["candidate_diversity"]["novelty_score"] > 0.0
        assert candidates[1].rule_id == "llm_reflection_patch"
        payload = json.loads(client.prompts[0])
        assert payload["candidate_count"] == 2
        assert payload["parent_candidate"]["id"] == "previous_patch"
        assert payload["parent_candidate"]["target_function_id"] == (
            previous.target_function_id
        )
        assert payload["previous_patch"]["diff_fingerprint"]
        assert payload["previous_patch"]["fixed_source_fingerprint"]
        assert payload["target_function_source"] == previous.old_source
        assert payload["reflection_strategy"]["id"] == "semantic_repair"
        assert payload["execution_feedback"]["failure_type"] == "test_failure"
        assert payload["failure_evidence"]["failure_type"] == "test_failure"
        assert payload["failure_evidence"]["pytest_stdout"] == "F"
        assert payload["failure_evidence"]["pytest_stderr"] == "AssertionError"
        assert payload["failure_evidence"]["failed_patch_fingerprint"]
        assert payload["failure_analysis"]["refinement_hints"]
        assert payload["cross_file_context"] == refinement_context
        assert payload["related_caller_callee_context"]["callers"] == (
            refinement_context["callers"]
        )
        assert payload["judge_feedback"]["available"] is True
        assert payload["judge_feedback"]["verdict"] == "reject"
        assert (
            "Use cross_file_context callers, callees, module dependencies, and "
            "data-flow neighbors to preserve the target function's contract."
        ) in payload["constraints"]
        memory = payload["failed_patch_memory"]
        assert memory["previous_patch_id"] == "previous_patch"
        assert memory["failure_type"] == "test_failure"
        assert memory["previous_fixed_source_fingerprint"] in (
            memory["avoid_fixed_source_fingerprints"]
        )
        assert candidates[0].metadata["failed_source_fingerprints"] == (
            memory["avoid_fixed_source_fingerprints"]
        )
        assert candidates[0].metadata["refinement_context"] == refinement_context
        assert payload["diversity_requirements"]["enabled"] is True
        assert payload["diversity_requirements"][
            "avoid_fixed_source_fingerprints"
        ] == memory["avoid_fixed_source_fingerprints"]
        assert "fixed_sources" in payload["required_schema"]
        assert generator.last_reflection_audit[0]["accepted_candidate_count"] == 2
        assert generator.last_reflection_audit[0]["prompt_context_audit"][
            "judge_feedback_available"
        ] is True
        assert generator.last_reflection_audit[0]["response_parse"]["status"] == (
            "pass"
        )


def test_llm_reflection_filters_near_duplicate_refined_sources():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        source_path = repo / "sample.py"
        original = "def adjust(value):\n    return value + 1\n"
        source_path.write_text(original, encoding="utf-8")
        failed = "def adjust(value):\n    return value + 0\n"
        first = "def adjust(value):\n    return value + 2\n"
        near_duplicate = "def adjust(value):\n    return value + 2  \n"
        distinct = (
            "def adjust(value):\n"
            "    if value is None:\n"
            "        return 0\n"
            "    return value + 2\n"
        )
        previous = _patch_candidate(
            repo=repo,
            source_path=source_path,
            old_source=original,
            new_source=failed,
        )
        execution_result = ExecutionResult(
            success=False,
            returncode=1,
            stdout="F",
            stderr="AssertionError",
            traceback="",
            passed=0,
            failed=1,
            timeout=False,
            command=[],
        )
        client = StaticLLMClient(
            json.dumps({"fixed_sources": [first, near_duplicate, distinct]})
        )

        candidates = LLMPatchGenerator(client).refine_many(
            repo,
            previous,
            execution_result,
            round_index=1,
            limit=2,
        )

        assert len(candidates) == 2
        assert candidates[0].new_source == first
        assert candidates[1].new_source == distinct
        assert candidates[0].metadata["reflection_child_index"] == 0
        assert candidates[1].metadata["reflection_child_index"] == 1
        assert (
            candidates[0].metadata["source_fingerprint"]
            != candidates[1].metadata["source_fingerprint"]
        )
        assert candidates[1].metadata["candidate_diversity"]["reason"] == "accepted"
        assert candidates[1].metadata["candidate_diversity"]["novelty_score"] > 0.0


def test_benchmark_runner_uses_llm_refiner_when_available():
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
        wrong_source = (
            "def shift_left(values):\n"
            "    for i in range(len(values) - 2):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n"
        )
        fixed_source = (
            "def shift_left(values):\n"
            "    for i in range(len(values) - 1):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n"
        )
        generator = LLMPatchGenerator(
            SequenceLLMClient(
                [
                    json.dumps({"fixed_source": wrong_source}),
                    json.dumps({"fixed_source": fixed_source}),
                ]
            )
        )

        report = BenchmarkRunner(patch_generator=generator).run_cases(
            [
                BenchmarkCase(
                    name="llm_reflection_case",
                    repo_path=str(repo),
                    buggy_functions=["shift_left"],
                    expected_rule_ids=["possible_index_overrun"],
                    failing_tests=["test_shift_left"],
                    passed_tests=[],
                    test_args=[],
                    metadata={},
                )
            ]
        )

        assert report.patch_success_rate == 1.0
        assert report.average_repair_rounds == 2.0
        assert report.reflection_success_rate == 1.0
        assert report.beam_success_rate == 1.0
        assert report.average_beam_depth == 1.0
        assert report.cases[0].repair_rounds == 2
        assert report.cases[0].best_patch_rule_id == "llm_reflection_patch"
        assert report.cases[0].beam_search_results
        assert report.cases[0].beam_search_results[0]["success"] is True


def _function(
    name: str,
    source_path: Path,
    start_line: int,
    end_line: int,
    source: str,
) -> CodeEntity:
    return CodeEntity(
        id=f"{source_path.as_posix()}::{name}",
        type="function",
        name=name,
        file_path=str(source_path),
        start_line=start_line,
        end_line=end_line,
        source=source,
        metadata={"qualified_name": name},
    )


def _ranked(function: CodeEntity, rank: int) -> FaultLocalizationResult:
    return FaultLocalizationResult(
        function_id=function.id,
        function_name=function.name,
        file_path=function.file_path,
        start_line=function.start_line,
        end_line=function.end_line,
        score=1.0 / rank,
        rank=rank,
        signals={"static": 1.0},
        findings=[],
        reason="test",
    )


def _patch_candidate(
    *,
    repo: Path,
    source_path: Path,
    old_source: str,
    new_source: str,
) -> PatchCandidate:
    from code_intelligence_agent.tools.diff_utils import render_unified_diff

    relative = source_path.relative_to(repo).as_posix()
    return PatchCandidate(
        id="previous_patch",
        target_file=str(source_path),
        relative_file_path=relative,
        target_function_id=f"{source_path.as_posix()}::shift_left",
        target_function_name="shift_left",
        rule_id="test_previous_patch",
        description="previous failing patch",
        old_source=old_source,
        new_source=new_source,
        diff=render_unified_diff(old_source, new_source, relative),
        metadata={
            "execution_feedback": {
                "failure_type": "test_failure",
                "score": 0.55,
                "passed_ratio": 0.0,
            }
        },
    )
