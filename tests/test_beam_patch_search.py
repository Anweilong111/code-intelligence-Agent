from pathlib import Path
import json
import tempfile

from code_intelligence_agent.agents.bug_detector import RuleBasedBugDetector
from code_intelligence_agent.agents.llm_client import StaticLLMClient
from code_intelligence_agent.agents.llm_patch_generator import LLMPatchGenerator
from code_intelligence_agent.agents.patch_generator import PatchGenerator
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.fault_localizer import FaultLocalizer
from code_intelligence_agent.core.models import ExecutionResult, PatchCandidate
from code_intelligence_agent.core.program_graph import build_program_graph
from code_intelligence_agent.core.repo_parser import RepoParser
from code_intelligence_agent.search.beam_patch_search import BeamPatchSearch
from code_intelligence_agent.search.patch_search import PatchSearch
from code_intelligence_agent.search.scoring import PatchScoreWeights
from code_intelligence_agent.tools.diff_utils import render_unified_diff
from code_intelligence_agent.tools.sandbox import Sandbox


def test_beam_patch_search_prioritizes_candidates_before_initial_beam():
    sandbox = RecordingSandbox()
    low_localization = _candidate("low_localization", "low_function")
    high_localization = _candidate(
        "high_localization",
        "high_function",
        variant_rank=1,
    )

    results = BeamPatchSearch(
        sandbox=sandbox,
        beam_width=1,
        max_depth=0,
    ).search(
        Path("."),
        [low_localization, high_localization],
        localization_scores={
            "low_function": 0.1,
            "high_function": 0.9,
        },
    )

    assert sandbox.seen == ["high_localization"]
    assert results[0].candidate.id == "high_localization"
    assert results[0].candidate.metadata["search_prior_score"] > 0.0


def test_beam_patch_search_can_disable_prior_ranking_for_ablation():
    sandbox = RecordingSandbox()
    low_localization = _candidate("low_localization", "low_function")
    high_localization = _candidate("high_localization", "high_function")

    results = BeamPatchSearch(
        sandbox=sandbox,
        beam_width=1,
        max_depth=0,
        use_prior_ranking=False,
    ).search(
        Path("."),
        [low_localization, high_localization],
        localization_scores={
            "low_function": 0.1,
            "high_function": 0.9,
        },
    )

    assert sandbox.seen == ["low_localization"]
    assert results[0].candidate.id == "low_localization"
    assert results[0].candidate.metadata["search_prior_score"] == 0.0


def test_patch_search_prefers_rule_diversity_when_prior_scores_tie():
    sandbox = RecordingSandbox()
    primary = _candidate("primary_rule_a", "same_function", rule_id="rule_a")
    duplicate = _candidate("duplicate_rule_a", "same_function", rule_id="rule_a")
    alternate = _candidate("alternate_rule_b", "same_function", rule_id="rule_b")

    results = PatchSearch(sandbox=sandbox, beam_width=2).search(
        Path("."),
        [primary, duplicate, alternate],
    )

    assert sandbox.seen == ["primary_rule_a", "alternate_rule_b"]
    assert [result.candidate.id for result in results] == [
        "primary_rule_a",
        "alternate_rule_b",
    ]
    assert results[1].candidate.metadata["search_diversity"]["reasons"] == [
        "new_rule",
        "new_variant",
    ]
    assert results[1].candidate.metadata["search_diversity_rank"] == 2


def test_patch_search_can_disable_diversity_reranking_for_ablation():
    sandbox = RecordingSandbox()
    primary = _candidate("primary_rule_a", "same_function", rule_id="rule_a")
    duplicate = _candidate("duplicate_rule_a", "same_function", rule_id="rule_a")
    alternate = _candidate("alternate_rule_b", "same_function", rule_id="rule_b")

    PatchSearch(
        sandbox=sandbox,
        beam_width=2,
        use_diversity_reranking=False,
    ).search(Path("."), [primary, duplicate, alternate])

    assert sandbox.seen == ["primary_rule_a", "duplicate_rule_a"]


def test_beam_patch_search_retains_same_failure_with_different_rules():
    weights = PatchScoreWeights(
        tests_passed=0.0,
        localization=0.0,
        static_check=0.0,
        execution_feedback=0.0,
        diff_penalty=0.0,
        risk_penalty=0.0,
        warning_penalty=0.0,
        success_bonus=0.0,
    )
    candidates = [
        _candidate("rule_a_primary", "same_function", rule_id="rule_a"),
        _candidate("rule_a_duplicate", "same_function", rule_id="rule_a"),
        _candidate("rule_b_alternate", "same_function", rule_id="rule_b"),
    ]

    results = BeamPatchSearch(
        sandbox=RetentionSandbox(),
        beam_width=2,
        candidate_pool_size=3,
        max_depth=0,
        use_prior_ranking=False,
        patch_score_weights=weights,
    ).search(Path("."), candidates)

    retained_ids = {node.candidate.id for node in results if node.retained}
    assert retained_ids == {"rule_a_primary", "rule_b_alternate"}
    assert {
        node.candidate.metadata["beam_retention"]["diversity_key"]
        for node in results
        if node.retained
    } == {
        "same_function|rule_a|test_failure|test_failure",
        "same_function|rule_b|test_failure|test_failure",
    }


class RecordingSandbox:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def apply_patch_and_test(
        self,
        repo_path,
        candidate: PatchCandidate,
        test_args: list[str] | None = None,
    ) -> ExecutionResult:
        self.seen.append(candidate.id)
        return ExecutionResult(
            success=False,
            returncode=1,
            stdout="",
            stderr="AssertionError",
            traceback="",
            passed=0,
            failed=1,
            timeout=False,
            command=[],
        )


class FeedbackSandbox:
    def apply_patch_and_test(
        self,
        repo_path,
        candidate: PatchCandidate,
        test_args: list[str] | None = None,
    ) -> ExecutionResult:
        if candidate.id == "syntax_candidate":
            return ExecutionResult(
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
        return ExecutionResult(
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


class RetentionSandbox:
    def apply_patch_and_test(
        self,
        repo_path,
        candidate: PatchCandidate,
        test_args: list[str] | None = None,
    ) -> ExecutionResult:
        if candidate.id == "type_candidate":
            return ExecutionResult(
                success=False,
                returncode=1,
                stdout="",
                stderr="TypeError: unsupported operand type",
                traceback="Traceback",
                passed=0,
                failed=0,
                timeout=False,
                command=[],
            )
        return ExecutionResult(
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


class ChildExpansionSandbox:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def apply_patch_and_test(
        self,
        repo_path,
        candidate: PatchCandidate,
        test_args: list[str] | None = None,
    ) -> ExecutionResult:
        self.seen.append(candidate.id)
        success = candidate.id == "good_child"
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


class BatchRefiner:
    def refine_many(
        self,
        repo_path,
        previous_patch: PatchCandidate,
        execution_result: ExecutionResult,
        round_index: int,
        limit: int = 1,
    ) -> list[PatchCandidate]:
        del repo_path, execution_result, round_index
        return [
            _candidate("bad_child", previous_patch.target_function_id),
            _candidate("good_child", previous_patch.target_function_id),
        ][:limit]


def _candidate(
    candidate_id: str,
    function_id: str,
    variant_rank: int = 0,
    rule_id: str = "test_rule",
) -> PatchCandidate:
    old_source = "def f():\n    return 1\n"
    new_source = "def f():\n    return 2\n"
    relative_file_path = f"{candidate_id}.py"
    return PatchCandidate(
        id=candidate_id,
        target_file=relative_file_path,
        relative_file_path=relative_file_path,
        target_function_id=function_id,
        target_function_name=function_id,
        rule_id=rule_id,
        description="test candidate",
        old_source=old_source,
        new_source=new_source,
        diff=render_unified_diff(old_source, new_source, relative_file_path),
        metadata={"variant": candidate_id, "variant_rank": variant_rank},
    )


def test_beam_patch_search_uses_execution_feedback_for_scoring_and_tie_breaker():
    syntax_candidate = _candidate("syntax_candidate", "same_function")
    assertion_candidate = _candidate("assertion_candidate", "same_function")

    results = BeamPatchSearch(
        sandbox=FeedbackSandbox(),
        beam_width=2,
        max_depth=0,
        use_prior_ranking=False,
    ).search(Path("."), [syntax_candidate, assertion_candidate])

    assert results[0].candidate.id == "assertion_candidate"
    assert results[0].score > results[1].score
    assert results[0].feedback_score > results[1].feedback_score
    assert results[0].candidate.metadata["execution_feedback"]["failure_type"] == "test_failure"
    assert results[1].candidate.metadata["execution_feedback"]["failure_type"] == "syntax_error"

    neutral_results = BeamPatchSearch(
        sandbox=FeedbackSandbox(),
        beam_width=2,
        max_depth=0,
        use_prior_ranking=False,
        patch_score_weights=PatchScoreWeights(execution_feedback=0.0),
    ).search(
        Path("."),
        [
            _candidate("syntax_candidate", "same_function"),
            _candidate("assertion_candidate", "same_function"),
        ],
    )
    assert neutral_results[0].score == neutral_results[1].score


def test_beam_patch_search_retains_feedback_diverse_candidate_pool():
    weights = PatchScoreWeights(
        tests_passed=0.0,
        localization=0.0,
        static_check=0.0,
        execution_feedback=0.0,
        diff_penalty=0.0,
        risk_penalty=0.0,
        warning_penalty=0.0,
        success_bonus=0.0,
    )
    candidates = [
        _candidate("duplicate_test_a", "same_function"),
        _candidate("duplicate_test_b", "same_function"),
        _candidate("type_candidate", "same_function"),
    ]

    results = BeamPatchSearch(
        sandbox=RetentionSandbox(),
        beam_width=2,
        candidate_pool_size=3,
        max_depth=0,
        use_prior_ranking=False,
        patch_score_weights=weights,
    ).search(Path("."), candidates)

    retained_ids = {node.candidate.id for node in results if node.retained}
    assert retained_ids == {"duplicate_test_a", "type_candidate"}
    assert {node.retention_bucket for node in results if node.retained} == {
        "test_failure",
        "recoverable_runtime",
    }
    assert results[0].candidate.metadata["beam_retention"]["failure_type"] == (
        "test_failure"
    )

    no_retention_results = BeamPatchSearch(
        sandbox=RetentionSandbox(),
        beam_width=2,
        candidate_pool_size=3,
        max_depth=0,
        use_prior_ranking=False,
        use_feedback_retention=False,
        patch_score_weights=weights,
    ).search(Path("."), candidates)

    assert {node.candidate.id for node in no_retention_results if node.retained} == {
        "duplicate_test_a",
        "duplicate_test_b",
    }


def test_beam_patch_search_expands_multiple_refined_children_per_parent():
    sandbox = ChildExpansionSandbox()
    root = _candidate("root", "target_function")

    results = BeamPatchSearch(
        sandbox=sandbox,
        refiner=BatchRefiner(),
        beam_width=1,
        max_depth=1,
        refinement_width=2,
        use_prior_ranking=False,
    ).search(Path("."), [root])

    assert sandbox.seen == ["root", "bad_child", "good_child"]
    assert results[0].candidate.id == "good_child"
    assert results[0].success is True
    assert results[0].depth == 1
    assert results[0].parent_id == "root"
    assert results[0].candidate.metadata["beam_child_index"] == 1
    assert results[0].candidate.metadata["beam_sibling_count"] == 2
    assert any(
        node.candidate.id == "bad_child" and not node.retained
        for node in results
    )


def test_beam_patch_search_expands_failed_candidate_with_refiner():
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
        fixed_source = (
            "def shift_left(values):\n"
            "    for i in range(len(values) - 1):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n"
        )
        parsed = RepoParser().parse(repo)
        call_graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, call_graph)
        detector = RuleBasedBugDetector()
        findings = detector.detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = PatchGenerator().generate(repo, parsed.functions, ranked)
        failed_candidate = next(
            candidate
            for candidate in candidates
            if candidate.metadata.get("variant") == "overly_conservative_range_bound"
        )
        refiner = LLMPatchGenerator(
            StaticLLMClient(json.dumps({"fixed_source": fixed_source}))
        )

        results = BeamPatchSearch(
            sandbox=Sandbox(timeout=10),
            refiner=refiner,
            beam_width=1,
            max_depth=2,
        ).search(
            repo,
            [failed_candidate],
            program_graph=program_graph,
        )

        assert results[0].success is True
        assert results[0].depth == 1
        assert results[0].parent_id == failed_candidate.id
        assert results[0].candidate.rule_id == "llm_reflection_patch"
        assert results[0].score > results[-1].score
