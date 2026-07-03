from dataclasses import replace
from pathlib import Path
import json
import subprocess
import sys
import tempfile

from code_intelligence_agent.agents.bug_detector import RuleBasedBugDetector
from code_intelligence_agent.agents.patch_generator import PatchGenerator
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.fault_localizer import FaultLocalizer, ScoreWeights
from code_intelligence_agent.core.models import (
    ExecutionResult,
    FaultLocalizationResult,
    PatchCandidate,
)
from code_intelligence_agent.core.program_graph import build_program_graph
from code_intelligence_agent.core.repo_parser import RepoParser
from code_intelligence_agent.evaluation.ablation import (
    AblationRunner,
    BenchmarkAblationRunner,
    CalibrationEvaluationRun,
    RuleEvaluationRun,
)
from code_intelligence_agent.evaluation.ablation_impact import (
    ablation_impact_report,
)
from code_intelligence_agent.evaluation.benchmark_loader import BenchmarkCase, BenchmarkLoader
from code_intelligence_agent.evaluation.benchmark_runner import (
    BenchmarkCaseResult,
    BenchmarkReport,
    BenchmarkRunner,
)
from code_intelligence_agent.evaluation.localization_calibration import (
    localization_calibration_report,
)
from code_intelligence_agent.evaluation.metric_uncertainty import (
    benchmark_metric_uncertainty_report,
)
from code_intelligence_agent.evaluation.search_budget_analysis import (
    search_budget_analysis_report,
)
from code_intelligence_agent.evaluation.search_competition_analysis import (
    search_competition_analysis_report,
)
from code_intelligence_agent.evaluation.reflection_analysis import (
    reflection_analysis_report,
)
from code_intelligence_agent.evaluation.github_fetcher import (
    FetchSource,
    GitHubBenchmarkFetcher,
    github_raw_url,
)
from code_intelligence_agent.evaluation.metrics import (
    LocalizationRun,
    average_precision,
    exam_score,
    mean_exam_score,
    mean_average_precision,
    mean_ndcg,
    mean_reciprocal_rank,
    normalized_discounted_cumulative_gain,
    patch_success_rate,
    top_k_accuracy,
)
from code_intelligence_agent.evaluation.report import (
    render_ablation_markdown,
    render_benchmark_markdown,
    render_patch_weight_search_markdown,
    render_weight_search_markdown,
)
from code_intelligence_agent.evaluation.patch_weight_search import (
    PatchWeightProfile,
    PatchWeightSearchResult,
    PatchWeightSearchRunner,
    annotate_patch_weight_search_pareto_frontier,
    generate_patch_weight_grid,
    patch_judge_fusion_summary,
)
from code_intelligence_agent.evaluation.weight_search import (
    _PreparedCase,
    _evaluate_profile,
    WeightProfile,
    WeightSearchRunner,
    annotate_weight_search_pareto_frontier,
    generate_weight_grid,
    rerank_with_weights,
)
from code_intelligence_agent.search.beam_patch_search import BeamPatchSearch
from code_intelligence_agent.search.beam_search import BeamNode, BeamSearch
from code_intelligence_agent.search.patch_judge import PatchJudgment
from code_intelligence_agent.search.patch_search import PatchSearch
from code_intelligence_agent.search.scoring import PatchScoreWeights, diff_size
from code_intelligence_agent.tools.sandbox import Sandbox


MANIFEST = Path("datasets/toy_bugs/manifest.json")
MULTI_BUG_MANIFEST = Path("datasets/toy_bugs/multi_bug_manifest.json")


class RecordingSandbox:
    def __init__(self) -> None:
        self.seen: list[str] = []
        self.test_args_seen: list[list[str] | None] = []

    def apply_patch_and_test(
        self,
        repo_path,
        candidate: PatchCandidate,
        test_args: list[str] | None = None,
    ) -> ExecutionResult:
        self.seen.append(candidate.id)
        self.test_args_seen.append(test_args)
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


class StaticPatchJudge:
    def __init__(self) -> None:
        self.calls = 0

    def judge_patch(
        self,
        *,
        candidate: PatchCandidate,
        execution_result: ExecutionResult,
        localization_confidence: float = 0.0,
        patch_risk: float = 0.0,
    ) -> PatchJudgment:
        del candidate, localization_confidence, patch_risk
        self.calls += 1
        if execution_result.success:
            return PatchJudgment(
                score=0.95,
                verdict="prefer",
                reason="Successful patch evidence.",
            )
        return PatchJudgment(
            score=0.15,
            verdict="reject",
            reason="Failed patch evidence.",
        )


class SuccessfulSandbox:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def apply_patch_and_test(
        self,
        repo_path,
        candidate: PatchCandidate,
        test_args: list[str] | None = None,
    ) -> ExecutionResult:
        self.calls.append(candidate.id)
        return ExecutionResult(
            success=True,
            returncode=0,
            stdout=".",
            stderr="",
            traceback="",
            passed=1,
            failed=0,
            timeout=False,
            command=[],
        )


class SingleCandidateGenerator:
    def generate(self, repo_path, functions, ranked) -> list[PatchCandidate]:
        target = ranked[0]
        return [
            PatchCandidate(
                id="beam_primary_patch",
                target_file=target.file_path,
                relative_file_path=Path(target.file_path).name,
                target_function_id=target.function_id,
                target_function_name=target.function_name,
                rule_id="beam_primary_rule",
                description="beam primary candidate",
                old_source="def target():\n    return 1\n",
                new_source="def target():\n    return 2\n",
                diff=(
                    "--- a/sample.py\n"
                    "+++ b/sample.py\n"
                    "-    return 1\n"
                    "+    return 2\n"
                ),
                metadata={"variant": "beam_primary"},
            )
        ]


class DuplicateCandidateGenerator:
    def generate(self, repo_path, functions, ranked) -> list[PatchCandidate]:
        del repo_path, ranked
        target = next(function for function in functions if function.name == "target")
        primary = PatchCandidate(
            id="dedupe_primary_patch",
            target_file=target.file_path,
            relative_file_path=Path(target.file_path).name,
            target_function_id=target.id,
            target_function_name=target.name,
            rule_id="dedupe_rule",
            description="dedupe primary candidate",
            old_source="def target():\n    return 1\n",
            new_source="def target():\n    return 2\n",
            diff=(
                "--- a/sample.py\n"
                "+++ b/sample.py\n"
                "-    return 1\n"
                "+    return 2\n"
            ),
            metadata={"variant": "dedupe_primary"},
        )
        return [
            primary,
            replace(
                primary,
                id="dedupe_duplicate_patch",
                metadata={"variant": "dedupe_duplicate"},
            ),
        ]


class FlatLocalizationForBundlePressure:
    llm_scorer = None

    def rank(
        self,
        program_graph,
        findings,
        test_summary=None,
        top_k: int | None = None,
    ) -> list[FaultLocalizationResult]:
        del findings, test_summary
        production_functions = [
            function
            for function in program_graph.functions.values()
            if not function.metadata.get("is_test")
            and not function.metadata.get("is_test_file")
        ]
        ordered = sorted(
            production_functions,
            key=lambda function: (
                function.metadata.get("qualified_name", function.name)
                not in {"z_left", "z_right"},
                function.metadata.get("qualified_name", function.name),
            ),
        )
        results = [
            FaultLocalizationResult(
                function_id=function.id,
                function_name=function.metadata.get("qualified_name", function.name),
                file_path=function.file_path,
                start_line=function.start_line,
                end_line=function.end_line,
                score=0.5,
                rank=index + 1,
                signals={"static": 0.0, "graph": 0.0, "sbfl": 0.0},
                findings=[],
                reason="flat localization for graph bundle pressure",
            )
            for index, function in enumerate(ordered)
        ]
        return results[:top_k] if top_k is not None else results


class GraphBundlePressurePatchGenerator:
    def generate(
        self,
        repo_path,
        functions,
        ranked,
        limit: int = 5,
    ) -> list[PatchCandidate]:
        del repo_path, ranked, limit
        by_name = {
            function.metadata.get("qualified_name", function.name): function
            for function in functions
        }
        names = [
            *(f"a{index}_decoy" for index in range(10)),
            "z_left",
            "z_right",
        ]
        return [_pressure_candidate(by_name[name]) for name in names]


class FlatLocalizationForDiversityPressure:
    llm_scorer = None

    def rank(
        self,
        program_graph,
        findings,
        test_summary=None,
        top_k: int | None = None,
    ) -> list[FaultLocalizationResult]:
        del findings, test_summary
        production_functions = [
            function
            for function in program_graph.functions.values()
            if not function.metadata.get("is_test")
            and not function.metadata.get("is_test_file")
        ]
        ordered = sorted(
            production_functions,
            key=lambda function: (
                function.metadata.get("qualified_name", function.name) != "target",
                function.metadata.get("qualified_name", function.name),
            ),
        )
        results = [
            FaultLocalizationResult(
                function_id=function.id,
                function_name=function.metadata.get("qualified_name", function.name),
                file_path=function.file_path,
                start_line=function.start_line,
                end_line=function.end_line,
                score=0.5,
                rank=index + 1,
                signals={"static": 0.0, "graph": 0.0, "sbfl": 0.0},
                findings=[],
                reason="flat localization for diversity pressure",
            )
            for index, function in enumerate(ordered)
        ]
        return results[:top_k] if top_k is not None else results


class DiversityPressurePatchGenerator:
    def generate(
        self,
        repo_path,
        functions,
        ranked,
        limit: int = 5,
    ) -> list[PatchCandidate]:
        del repo_path, ranked, limit
        by_name = {
            function.metadata.get("qualified_name", function.name): function
            for function in functions
        }
        target = by_name["target"]
        return [
            _diversity_pressure_candidate(
                target,
                candidate_id=f"duplicate_decoy_{index}",
                rule_id="duplicate_rule",
                variant=f"duplicate_decoy_{index}",
                replacement=f"value + {index + 1}",
            )
            for index in range(1, 5)
        ] + [
            _diversity_pressure_candidate(
                target,
                candidate_id="diverse_success_patch",
                rule_id="diverse_success_rule",
                variant="diverse_success",
                replacement="value",
            )
        ]


class CandidateDeduplicationPressurePatchGenerator:
    def generate(
        self,
        repo_path,
        functions,
        ranked,
        limit: int = 5,
    ) -> list[PatchCandidate]:
        del repo_path, ranked, limit
        target = next(
            function
            for function in functions
            if function.metadata.get("qualified_name", function.name) == "target"
        )
        failed = _candidate_deduplication_pressure_candidate(
            target,
            candidate_id="dedupe_duplicate_1",
            replacement="value + 10",
        )
        duplicates = [
            replace(
                failed,
                id=f"dedupe_duplicate_{index}",
                metadata={
                    **failed.metadata,
                    "variant_index": index,
                },
            )
            for index in range(1, 5)
        ]
        success = _candidate_deduplication_pressure_candidate(
            target,
            candidate_id="dedupe_success_patch",
            replacement="value",
        )
        return [*duplicates, success]


class BundleOnlySandbox:
    def __init__(self, success_ids: set[str]) -> None:
        self.success_ids = success_ids
        self.single_seen: list[str] = []
        self.bundle_seen: list[set[str]] = []

    def apply_patch_and_test(
        self,
        repo_path,
        candidate: PatchCandidate,
        test_args: list[str] | None = None,
    ) -> ExecutionResult:
        del repo_path, test_args
        self.single_seen.append(candidate.id)
        return _execution_failure()

    def apply_patches_and_test(
        self,
        repo_path,
        candidates: list[PatchCandidate],
        test_args: list[str] | None = None,
    ) -> ExecutionResult:
        del repo_path, test_args
        candidate_ids = {candidate.id for candidate in candidates}
        self.bundle_seen.append(candidate_ids)
        if candidate_ids == self.success_ids:
            return ExecutionResult(
                success=True,
                returncode=0,
                stdout=".",
                stderr="",
                traceback="",
                passed=1,
                failed=0,
                timeout=False,
                command=[],
            )
        return _execution_failure()


class DiversityPressureSandbox:
    def __init__(self, success_id: str) -> None:
        self.success_id = success_id
        self.single_seen: list[str] = []
        self.bundle_seen: list[set[str]] = []

    def apply_patch_and_test(
        self,
        repo_path,
        candidate: PatchCandidate,
        test_args: list[str] | None = None,
    ) -> ExecutionResult:
        del repo_path, test_args
        self.single_seen.append(candidate.id)
        if candidate.id == self.success_id:
            return _execution_success()
        return _execution_failure()

    def apply_patches_and_test(
        self,
        repo_path,
        candidates: list[PatchCandidate],
        test_args: list[str] | None = None,
    ) -> ExecutionResult:
        del repo_path, test_args
        self.bundle_seen.append({candidate.id for candidate in candidates})
        return _execution_failure()


def _candidate(
    candidate_id: str,
    function_id: str,
    variant_rank: int = 0,
) -> PatchCandidate:
    return PatchCandidate(
        id=candidate_id,
        target_file=f"{candidate_id}.py",
        relative_file_path=f"{candidate_id}.py",
        target_function_id=function_id,
        target_function_name=function_id,
        rule_id="test_rule",
        description="test candidate",
        old_source="def f():\n    return 1\n",
        new_source="def f():\n    return 2\n",
        diff=f"--- a/{candidate_id}.py\n+++ b/{candidate_id}.py\n-    return 1\n+    return 2\n",
        metadata={"variant": candidate_id, "variant_rank": variant_rank},
    )


def _diversity_pressure_candidate(
    function,
    *,
    candidate_id: str,
    rule_id: str,
    variant: str,
    replacement: str,
) -> PatchCandidate:
    qualified = function.metadata.get("qualified_name", function.name)
    new_source = function.source.replace("value + 1", replacement, 1)
    return PatchCandidate(
        id=candidate_id,
        target_file=function.file_path,
        relative_file_path=Path(function.file_path).name,
        target_function_id=function.id,
        target_function_name=qualified,
        rule_id=rule_id,
        description="diversity pressure candidate",
        old_source=function.source,
        new_source=new_source,
        diff=(
            f"--- a/{Path(function.file_path).name}\n"
            f"+++ b/{Path(function.file_path).name}\n"
            "-    return value + 1\n"
            f"+    return {replacement}\n"
        ),
        metadata={
            "variant": variant,
            "variant_rank": 0,
            "confidence": 0.8,
            "rule_confidence": 0.8,
        },
    )


def _candidate_deduplication_pressure_candidate(
    function,
    *,
    candidate_id: str,
    replacement: str,
) -> PatchCandidate:
    qualified = function.metadata.get("qualified_name", function.name)
    new_source = function.source.replace("value + 1", replacement, 1)
    return PatchCandidate(
        id=candidate_id,
        target_file=function.file_path,
        relative_file_path=Path(function.file_path).name,
        target_function_id=function.id,
        target_function_name=qualified,
        rule_id="dedupe_pressure_rule",
        description="candidate deduplication pressure candidate",
        old_source=function.source,
        new_source=new_source,
        diff=(
            f"--- a/{Path(function.file_path).name}\n"
            f"+++ b/{Path(function.file_path).name}\n"
            "-    return value + 1\n"
            f"+    return {replacement}\n"
        ),
        metadata={
            "variant": "dedupe_pressure",
            "variant_rank": 0,
            "confidence": 0.8,
            "rule_confidence": 0.8,
        },
    )


def _pressure_candidate(function) -> PatchCandidate:
    qualified = function.metadata.get("qualified_name", function.name)
    new_source = function.source.replace("return", "return", 1)
    if new_source == function.source:
        new_source = f"{function.source}\n"
    return PatchCandidate(
        id=f"{qualified}_patch",
        target_file=function.file_path,
        relative_file_path=Path(function.file_path).name,
        target_function_id=function.id,
        target_function_name=qualified,
        rule_id="graph_bundle_pressure_rule",
        description="graph bundle pressure candidate",
        old_source=function.source,
        new_source=new_source,
        diff=(
            f"--- a/{Path(function.file_path).name}\n"
            f"+++ b/{Path(function.file_path).name}\n"
            "-    return value\n"
            "+    return value\n"
        ),
        metadata={"variant": f"{qualified}_patch"},
    )


def _execution_failure() -> ExecutionResult:
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


def _execution_success() -> ExecutionResult:
    return ExecutionResult(
        success=True,
        returncode=0,
        stdout=".",
        stderr="",
        traceback="",
        passed=1,
        failed=0,
        timeout=False,
        command=[],
    )


def test_beam_search_keeps_highest_scoring_nodes():
    search = BeamSearch[int](beam_width=2, max_depth=2)
    initial = [BeamNode(state=1, score=0.1), BeamNode(state=2, score=0.2)]

    def expand(node):
        return [
            BeamNode(state=node.state * 10, score=node.score + 0.1, depth=node.depth + 1),
            BeamNode(state=node.state * 10 + 1, score=node.score + 0.2, depth=node.depth + 1),
        ]

    result = search.search(initial, expand)

    assert len(result) == 2
    assert result[0].score >= result[1].score


def test_benchmark_loader_resolves_manifest_paths():
    cases = BenchmarkLoader().load_manifest(MANIFEST)

    assert len(cases) == 5
    assert cases[0].name == "buggy_sample_shift_left"
    assert Path(cases[0].repo_path).exists()
    assert cases[0].buggy_functions == ["shift_left"]


def test_evaluation_metrics_compute_topk_mrr_and_patch_success():
    runs = [
        LocalizationRun(ranked=["a", "b", "c"], ground_truth={"a"}),
        LocalizationRun(ranked=["a", "b", "c"], ground_truth={"c"}),
    ]

    assert top_k_accuracy(runs, 1) == 0.5
    assert top_k_accuracy(runs, 3) == 1.0
    assert round(mean_reciprocal_rank(runs), 4) == 0.6667
    assert (
        round(
            average_precision(
                LocalizationRun(
                    ranked=["a", "x", "b", "c"],
                    ground_truth={"a", "b"},
                )
            ),
            4,
        )
        == 0.8333
    )
    assert round(mean_average_precision(runs), 4) == 0.6667
    multi_bug_run = LocalizationRun(
        ranked=["clean", "bug_a", "bug_b", "late_bug"],
        ground_truth={"bug_a", "bug_b", "late_bug"},
    )
    assert round(normalized_discounted_cumulative_gain(multi_bug_run, 3), 4) == 0.5307
    assert round(mean_ndcg([runs[0], multi_bug_run], 3), 4) == 0.7654
    assert round(exam_score(multi_bug_run), 4) == 0.25
    assert round(mean_exam_score([runs[0], multi_bug_run]), 4) == 0.125
    assert patch_success_rate([True, False, True]) == 2 / 3


def test_weight_reranking_uses_configured_final_score_weights():
    ranked = [
        FaultLocalizationResult(
            function_id="static",
            function_name="static_candidate",
            file_path="sample.py",
            start_line=1,
            end_line=2,
            score=0.4,
            rank=1,
            signals={
                "sbfl": 0.0,
                "graph": 0.1,
                "static": 0.9,
                "semantic": 0.0,
                "llm": 0.0,
                "risk": 0.0,
            },
            findings=[],
            reason="",
        ),
        FaultLocalizationResult(
            function_id="graph",
            function_name="graph_candidate",
            file_path="sample.py",
            start_line=4,
            end_line=5,
            score=0.3,
            rank=2,
            signals={
                "sbfl": 0.0,
                "graph": 0.8,
                "static": 0.0,
                "semantic": 0.0,
                "llm": 0.0,
                "risk": 0.0,
            },
            findings=[],
            reason="",
        ),
    ]

    static_first = rerank_with_weights(
        ranked,
        ScoreWeights(sbfl=0.0, graph=0.0, static=1.0, semantic=0.0, llm=0.0, risk=0.0),
    )
    graph_first = rerank_with_weights(
        ranked,
        ScoreWeights(sbfl=0.0, graph=1.0, static=0.0, semantic=0.0, llm=0.0, risk=0.0),
    )

    assert static_first[0].function_name == "static_candidate"
    assert graph_first[0].function_name == "graph_candidate"
    assert graph_first[0].rank == 1


def test_weight_search_profile_records_holdout_robustness():
    ranked = [
        _fault_result("static_candidate", rank=1, static=1.0, graph=0.0),
        _fault_result("graph_candidate", rank=2, static=0.0, graph=1.0),
    ]
    cases = [
        _PreparedCase(
            ranked=ranked,
            ground_truth={"static_candidate"},
            has_coverage=True,
            source_group="repo/static",
        ),
        _PreparedCase(
            ranked=ranked,
            ground_truth={"static_candidate"},
            has_coverage=True,
            source_group="repo/static",
        ),
        _PreparedCase(
            ranked=ranked,
            ground_truth={"graph_candidate"},
            has_coverage=True,
            source_group="repo/graph",
        ),
    ]

    result = _evaluate_profile(
        cases,
        WeightProfile(
            "static_only",
            ScoreWeights(
                sbfl=0.0,
                graph=0.0,
                static=1.0,
                semantic=0.0,
                llm=0.0,
                risk=0.0,
            ),
        ),
    )

    assert result.source_group_count == 2
    assert result.min_source_group_cases == 1
    assert result.source_groups["repo/static"]["case_count"] == 2
    assert result.source_groups["repo/static"]["top1"] == 1.0
    assert result.source_groups["repo/graph"]["case_count"] == 1
    assert result.source_groups["repo/graph"]["map"] == 0.5
    assert len(result.holdout_splits) == 2
    graph_split = {
        split["holdout_group"]: split for split in result.holdout_splits
    }["repo/graph"]
    assert graph_split["train_groups"] == ["repo/static"]
    assert graph_split["top1_gap"] == 1.0
    assert graph_split["map_gap"] == 0.5
    assert result.max_top1_gap == 1.0
    assert result.max_map_gap == 0.5
    assert result.robust_validation_score == round(result.validation_score - 0.15, 4)


def test_weight_search_pareto_frontier_marks_dominated_profiles():
    ranked = [
        _fault_result("static_candidate", rank=1, static=1.0, graph=0.0),
        _fault_result("graph_candidate", rank=2, static=0.0, graph=1.0),
    ]
    cases = [
        _PreparedCase(
            ranked=ranked,
            ground_truth={"static_candidate"},
            has_coverage=True,
            source_group="repo/static",
        ),
        _PreparedCase(
            ranked=ranked,
            ground_truth={"static_candidate"},
            has_coverage=True,
            source_group="repo/static",
        ),
    ]
    static_result = _evaluate_profile(
        cases,
        WeightProfile(
            "static_only",
            ScoreWeights(
                sbfl=0.0,
                graph=0.0,
                static=1.0,
                semantic=0.0,
                llm=0.0,
                risk=0.0,
            ),
        ),
    )
    graph_result = _evaluate_profile(
        cases,
        WeightProfile(
            "graph_only",
            ScoreWeights(
                sbfl=0.0,
                graph=1.0,
                static=0.0,
                semantic=0.0,
                llm=0.0,
                risk=0.0,
            ),
        ),
    )

    annotated = annotate_weight_search_pareto_frontier(
        [static_result, graph_result]
    )
    by_profile = {result.profile: result for result in annotated}

    assert by_profile["static_only"].pareto_optimal is True
    assert by_profile["static_only"].dominates_count == 1
    assert by_profile["static_only"].dominated_by_count == 0
    assert by_profile["graph_only"].pareto_optimal is False
    assert by_profile["graph_only"].dominates_count == 0
    assert by_profile["graph_only"].dominated_by_count == 1


def test_weight_search_runner_scores_manifest_profiles():
    profiles = [
        WeightProfile("default", ScoreWeights()),
        WeightProfile(
            "static_heavy",
            ScoreWeights(
                sbfl=0.05,
                graph=0.10,
                static=0.75,
                semantic=0.05,
                llm=0.0,
                risk=0.0,
            ),
        ),
    ]

    results = WeightSearchRunner(use_dynamic_coverage=False).search_manifest(
        MANIFEST,
        profiles=profiles,
    )

    assert {result.profile for result in results} == {"default", "static_heavy"}
    assert results[0].case_count == 5
    assert results[0].robust_validation_score >= results[-1].robust_validation_score
    assert results[0].robust_validation_score <= results[0].validation_score
    assert results[0].source_group_count >= 1
    assert results[0].min_source_group_cases >= 1
    assert results[0].source_groups
    assert isinstance(results[0].holdout_splits, list)
    assert results[0].pareto_optimal is True
    assert results[0].dominated_by_count == 0
    assert results[0].top3 == 1.0
    assert results[0].ndcg_at_3 == 1.0
    assert results[0].mean_exam_score < 1.0
    assert len(generate_weight_grid()) > len(profiles)

    markdown = render_weight_search_markdown(results, top_n=2)
    assert "Robust Score" in markdown
    assert "Pareto" in markdown
    assert "FinalScore Pareto Frontier" in markdown
    assert "Validation Score" in markdown
    assert "Source Groups" in markdown
    assert "Top-1 Gap" in markdown
    assert "MAP Gap" in markdown
    assert "Best Profile Source Groups" in markdown
    assert "nDCG@3" in markdown
    assert "Mean EXAM Score" in markdown
    assert "Mean EXAM" in markdown
    assert "SBFL" in markdown


def _fault_result(
    function_name: str,
    *,
    rank: int,
    static: float,
    graph: float,
) -> FaultLocalizationResult:
    return FaultLocalizationResult(
        function_id=function_name,
        function_name=function_name,
        file_path="sample.py",
        start_line=rank,
        end_line=rank + 1,
        score=0.0,
        rank=rank,
        signals={
            "sbfl": 0.0,
            "graph": graph,
            "static": static,
            "semantic": 0.0,
            "llm": 0.0,
            "risk": 0.0,
        },
        findings=[],
        reason="synthetic robust weight-search candidate",
    )


def test_patch_weight_search_runner_scores_manifest_profiles():
    profiles = [
        PatchWeightProfile("default", PatchScoreWeights()),
        PatchWeightProfile(
            "no_feedback",
            PatchScoreWeights(execution_feedback=0.0),
        ),
    ]

    results = PatchWeightSearchRunner(use_dynamic_coverage=False).search_manifest(
        MANIFEST,
        profiles=profiles,
    )

    assert {result.profile for result in results} == {"default", "no_feedback"}
    assert results[0].case_count == 5
    assert results[0].top1_success == 1.0
    assert results[0].mrr == 1.0
    assert results[0].pareto_optimal is True
    assert results[0].dominated_by_count == 0
    assert len(generate_patch_weight_grid()) > len(profiles)

    markdown = render_patch_weight_search_markdown(results, top_n=2)
    assert "Pareto" in markdown
    assert "PatchScore Pareto Frontier" in markdown
    assert "Top-1 Success" in markdown
    assert "Feedback" in markdown
    assert "Judge Weight" in markdown


def test_patch_weight_search_pareto_frontier_marks_dominated_profiles():
    strong = PatchWeightSearchResult(
        profile="strong",
        weights=PatchScoreWeights(),
        validation_score=0.90,
        top1_success=1.0,
        mrr=1.0,
        average_first_success_rank=1.0,
        average_success_score_margin=0.20,
        case_count=4,
    )
    weak = PatchWeightSearchResult(
        profile="weak",
        weights=PatchScoreWeights(execution_feedback=0.0),
        validation_score=0.75,
        top1_success=0.75,
        mrr=0.75,
        average_first_success_rank=2.0,
        average_success_score_margin=0.05,
        case_count=4,
    )

    annotated = annotate_patch_weight_search_pareto_frontier([strong, weak])
    by_profile = {result.profile: result for result in annotated}

    assert by_profile["strong"].pareto_optimal is True
    assert by_profile["strong"].dominates_count == 1
    assert by_profile["strong"].dominated_by_count == 0
    assert by_profile["weak"].pareto_optimal is False
    assert by_profile["weak"].dominates_count == 0
    assert by_profile["weak"].dominated_by_count == 1


def test_patch_weight_search_runner_validates_patch_judge_weight_profiles():
    profiles = [
        PatchWeightProfile("default", PatchScoreWeights()),
        PatchWeightProfile(
            "judge_weighted",
            PatchScoreWeights(),
            patch_judge_weight=0.20,
        ),
    ]
    judge = StaticPatchJudge()

    results = PatchWeightSearchRunner(
        patch_judge=judge,
        use_dynamic_coverage=False,
    ).search_manifest(
        MANIFEST,
        profiles=profiles,
    )

    by_profile = {result.profile: result for result in results}
    assert judge.calls > 0
    assert by_profile["judge_weighted"].patch_judge_weight == 0.20
    assert by_profile["judge_weighted"].case_count == 5
    assert by_profile["judge_weighted"].top1_success == 1.0
    assert by_profile["judge_weighted"].to_dict()["patch_judge_weight"] == 0.20

    markdown = render_patch_weight_search_markdown(results, top_n=2)
    assert "Judge Weight" in markdown
    assert "0.20" in markdown
    assert "Patch Judge Fusion Summary" in markdown


def test_patch_judge_fusion_summary_reports_weighted_profile_delta():
    baseline = PatchWeightSearchResult(
        profile="default",
        weights=PatchScoreWeights(),
        validation_score=0.70,
        top1_success=0.60,
        mrr=0.70,
        average_first_success_rank=2.0,
        average_success_score_margin=0.05,
        case_count=5,
        patch_judge_weight=0.0,
    )
    judge = PatchWeightSearchResult(
        profile="default_judge08",
        weights=PatchScoreWeights(),
        validation_score=0.82,
        top1_success=0.80,
        mrr=0.85,
        average_first_success_rank=1.4,
        average_success_score_margin=0.11,
        case_count=5,
        patch_judge_weight=0.08,
    )

    summary = patch_judge_fusion_summary([baseline, judge])
    payload = summary.to_dict()

    assert payload["status"] == "improved"
    assert payload["profile_count"] == 2
    assert payload["judge_profile_count"] == 1
    assert payload["baseline_profile"] == "default"
    assert payload["best_judge_profile"] == "default_judge08"
    assert payload["best_judge_weight"] == 0.08
    assert payload["validation_delta"] == 0.12
    assert payload["top1_delta"] == 0.2
    assert payload["mrr_delta"] == 0.15
    assert payload["success_margin_delta"] == 0.06
    assert payload["first_success_rank_delta"] == 0.6


def test_patch_weight_search_cli_outputs_json_report():
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_intelligence_agent.evaluation.run_patch_weight_search",
            str(MANIFEST),
            "--format",
            "json",
            "--top-n",
            "1",
            "--no-dynamic-coverage",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert len(payload) == 1
    assert payload[0]["case_count"] == 5
    assert payload[0]["top1_success"] == 1.0
    assert payload[0]["pareto_optimal"] is True
    assert payload[0]["dominated_by_count"] == 0


def test_ablation_runner_summarizes_variants():
    full = [LocalizationRun(ranked=["buggy"], ground_truth={"buggy"})]
    weak = [LocalizationRun(ranked=["clean", "buggy"], ground_truth={"buggy"})]

    results = AblationRunner().summarize(
        {"full": full, "without_graph": weak},
        {
            "full": [RuleEvaluationRun(expected={"rule"}, detected={"rule"})],
            "without_graph": [
                RuleEvaluationRun(expected={"rule"}, detected={"rule", "extra"})
            ],
        },
        calibration_runs_by_variant={
            "full": [
                CalibrationEvaluationRun(confidence=0.95, top1_hit=True),
                CalibrationEvaluationRun(confidence=0.90, top1_hit=True),
            ],
            "without_graph": [
                CalibrationEvaluationRun(confidence=0.90, top1_hit=False),
                CalibrationEvaluationRun(confidence=0.20, top1_hit=True),
            ],
        },
    )
    by_variant = {result.variant: result for result in results}

    assert results[0].variant == "full"
    assert results[0].top1 == 1.0
    assert by_variant["full"].map == 1.0
    assert by_variant["full"].ndcg_at_3 == 1.0
    assert by_variant["full"].mean_exam_score == 0.0
    assert by_variant["full"].expected_rule_precision == 1.0
    assert by_variant["full"].localization_calibration_cases == 2
    assert by_variant["full"].localization_calibrated_expected_calibration_error < (
        by_variant["full"].localization_expected_calibration_error
    )
    assert by_variant["without_graph"].expected_rule_precision == 0.5
    assert by_variant["without_graph"].average_extra_rules == 1.0
    assert (
        by_variant["without_graph"].localization_calibrated_expected_calibration_error
        != by_variant["full"].localization_calibrated_expected_calibration_error
    )

    impact = ablation_impact_report(results)
    assert impact.baseline_variant == "full"
    assert impact.impacted_variant_count == 1
    assert impact.rows[0].variant == "without_graph"
    assert impact.rows[0].delta_top1 == -1.0
    assert impact.rows[0].delta_rule_precision == -0.5
    assert impact.rows[0].delta_calibrated_ece_improvement != 0.0
    assert impact.rows[0].delta_calibrated_brier_improvement != 0.0
    assert impact.rows[0].impact_score < 0.0
    assert impact.rows[0].direction == "regression"
    assert impact.rows[0].baseline_case_count == 1
    assert impact.rows[0].variant_case_count == 1
    assert impact.rows[0].paired_case_count == 1
    assert impact.rows[0].dominant_signal in impact.rows[0].signal_contributions
    assert impact.rows[0].dominant_contribution < 0.0
    assert impact.rows[0].regression_signal_count >= 1


def test_benchmark_ablation_runner_uses_manifest_cases():
    results = BenchmarkAblationRunner().run_manifest(MANIFEST)
    variants = {result.variant: result for result in results}

    assert set(variants) == {
        "full",
        "without_rule_precision_filter",
        "without_reflection",
        "without_beam_search",
        "without_patch_prior",
        "without_diversity_reranking",
        "without_candidate_deduplication",
        "without_multi_patch_repair",
        "without_graph_bundle_search",
        "without_static_rules",
        "without_test_signals",
        "without_line_coverage",
        "without_branch_coverage",
        "without_path_coverage",
        "without_data_dependency",
        "without_control_flow",
        "without_pagerank",
        "without_caller_impact",
        "without_module_dependency",
        "without_async_call_graph",
        "without_semantic_similarity",
        "without_llm_score",
    }
    assert variants["full"].top1 == 1.0
    assert variants["full"].mrr == 1.0
    assert variants["full"].map == 1.0
    assert variants["full"].expected_rule_recall == 1.0
    assert variants["full"].expected_rule_precision == 1.0
    assert variants["full"].patch_success_rate == 1.0
    assert variants["full"].beam_success_rate == 1.0
    assert variants["without_beam_search"].beam_success_rate == 0.0
    assert variants["without_patch_prior"].beam_success_rate == 1.0
    assert variants["without_diversity_reranking"].beam_success_rate == 1.0
    assert variants["without_diversity_reranking"].patch_success_rate == 1.0
    assert variants["without_candidate_deduplication"].patch_success_rate == 1.0
    assert variants["without_multi_patch_repair"].patch_success_rate == 1.0
    assert variants["without_graph_bundle_search"].patch_success_rate == 1.0
    assert variants["without_reflection"].patch_success_rate == 1.0
    assert variants["without_static_rules"].expected_rule_recall == 0.0
    assert variants["without_rule_precision_filter"].expected_rule_precision <= 1.0

    markdown = render_ablation_markdown(results)
    assert "Patch Success" in markdown
    assert "Loc Cal ECE" in markdown
    assert "dCal ECE Improve" in markdown
    assert "Beam Success" in markdown
    assert "Multi-Patch Success" in markdown
    assert "without_beam_search" in markdown
    assert "without_patch_prior" in markdown
    assert "without_diversity_reranking" in markdown
    assert "without_candidate_deduplication" in markdown
    assert "without_graph_bundle_search" in markdown
    assert "Ablation Impact" in markdown
    assert "Impact" in markdown
    assert "Direction" in markdown


def test_benchmark_ablation_captures_graph_bundle_search_regression():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        decoy_functions = "\n\n".join(
            f"def a{index}_decoy(value):\n    return value"
            for index in range(10)
        )
        (repo / "sample.py").write_text(
            f"{decoy_functions}\n\n"
            "def z_left(value):\n"
            "    return z_right(value)\n\n"
            "def z_right(value):\n"
            "    return value\n",
            encoding="utf-8",
        )
        sandbox = BundleOnlySandbox(
            success_ids={"z_left_patch", "z_right_patch"}
        )

        results = BenchmarkAblationRunner(
            localizer=FlatLocalizationForBundlePressure(),
            patch_generator=GraphBundlePressurePatchGenerator(),
            sandbox=sandbox,
            use_dynamic_coverage=False,
        ).run_cases(
            [
                BenchmarkCase(
                    name="graph_bundle_pressure",
                    repo_path=str(repo),
                    buggy_functions=["z_left", "z_right"],
                    expected_rule_ids=[],
                    failing_tests=[],
                    passed_tests=[],
                    test_args=[],
                    metadata={"bug_type": "graph bundle pressure"},
                )
            ]
        )

    variants = {result.variant: result for result in results}

    assert variants["full"].patch_success_rate == 1.0
    assert variants["full"].multi_patch_success_rate == 1.0
    assert variants["without_graph_bundle_search"].patch_success_rate == 0.0
    assert variants["without_graph_bundle_search"].multi_patch_success_rate == 0.0
    success_bundle = {"z_left_patch", "z_right_patch"}
    assert success_bundle in sandbox.bundle_seen
    assert any(
        all(bundle != success_bundle for bundle in sandbox.bundle_seen[index : index + 8])
        for index in range(0, max(0, len(sandbox.bundle_seen) - 7))
    )


def test_benchmark_ablation_captures_diversity_reranking_regression():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def target(value):\n"
            "    return value + 1\n",
            encoding="utf-8",
        )
        sandbox = DiversityPressureSandbox(success_id="diverse_success_patch")

        results = BenchmarkAblationRunner(
            localizer=FlatLocalizationForDiversityPressure(),
            patch_generator=DiversityPressurePatchGenerator(),
            sandbox=sandbox,
            use_dynamic_coverage=False,
        ).run_cases(
            [
                BenchmarkCase(
                    name="diversity_reranking_pressure",
                    repo_path=str(repo),
                    buggy_functions=["target"],
                    expected_rule_ids=[],
                    failing_tests=[],
                    passed_tests=[],
                    test_args=[],
                    metadata={"bug_type": "search diversity pressure"},
                )
            ]
        )

    variants = {result.variant: result for result in results}

    assert variants["full"].patch_success_rate == 1.0
    assert variants["full"].beam_success_rate == 1.0
    assert variants["without_diversity_reranking"].patch_success_rate == 0.0
    assert variants["without_diversity_reranking"].beam_success_rate == 0.0
    assert variants["without_diversity_reranking"].multi_patch_success_rate == 0.0
    impact = ablation_impact_report(results)
    impact_rows = {row.variant: row for row in impact.rows}
    diversity_impact = impact_rows["without_diversity_reranking"]
    assert diversity_impact.direction == "regression"
    assert diversity_impact.delta_patch_success == -1.0
    assert diversity_impact.delta_beam_success == -1.0
    assert diversity_impact.dominant_signal == "patch_success"
    assert sandbox.single_seen[:4] == [
        "duplicate_decoy_1",
        "diverse_success_patch",
        "duplicate_decoy_2",
        "duplicate_decoy_3",
    ]
    assert "diverse_success_patch" in sandbox.single_seen
    assert all("diverse_success_patch" not in bundle for bundle in sandbox.bundle_seen)


def test_benchmark_ablation_captures_candidate_deduplication_regression():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def target(value):\n"
            "    return value + 1\n",
            encoding="utf-8",
        )
        sandbox = DiversityPressureSandbox(success_id="dedupe_success_patch")

        results = BenchmarkAblationRunner(
            localizer=FlatLocalizationForDiversityPressure(),
            patch_generator=CandidateDeduplicationPressurePatchGenerator(),
            sandbox=sandbox,
            use_dynamic_coverage=False,
        ).run_cases(
            [
                BenchmarkCase(
                    name="candidate_deduplication_pressure",
                    repo_path=str(repo),
                    buggy_functions=["target"],
                    expected_rule_ids=[],
                    failing_tests=[],
                    passed_tests=[],
                    test_args=[],
                    metadata={"bug_type": "candidate deduplication pressure"},
                )
            ]
        )

    variants = {result.variant: result for result in results}

    assert variants["full"].patch_success_rate == 1.0
    assert variants["full"].beam_success_rate == 1.0
    assert variants["without_candidate_deduplication"].patch_success_rate == 0.0
    assert variants["without_candidate_deduplication"].beam_success_rate == 0.0
    assert (
        variants["without_candidate_deduplication"].multi_patch_success_rate
        == 0.0
    )
    impact = ablation_impact_report(results)
    impact_rows = {row.variant: row for row in impact.rows}
    dedupe_impact = impact_rows["without_candidate_deduplication"]
    assert dedupe_impact.direction == "regression"
    assert dedupe_impact.delta_patch_success == -1.0
    assert dedupe_impact.delta_beam_success == -1.0
    assert dedupe_impact.dominant_signal == "patch_success"
    assert "dedupe_success_patch" in sandbox.single_seen
    assert any(
        sandbox.single_seen[index : index + 4]
        == [
            "dedupe_duplicate_1",
            "dedupe_duplicate_2",
            "dedupe_duplicate_3",
            "dedupe_duplicate_4",
        ]
        for index in range(0, max(0, len(sandbox.single_seen) - 3))
    )


def test_rule_precision_filter_reduces_static_false_positive_rules():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def shift_left(values):\n"
            "    for i in range(len(values)):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n\n"
            "def guarded_by_source(values):\n"
            "    n = len(values)\n"
            "    if not values:\n"
            "        raise ValueError('empty')\n"
            "    return sum(values) / n\n\n"
            "def mapping_lookup(values, mapping):\n"
            "    index = str(len(values) // 2)\n"
            "    return mapping[index]\n\n"
            "class Recorder:\n"
            "    def add(self, item):\n"
            "        result = self.builder.append(item)\n"
            "        return result\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "from sample import shift_left\n\n"
            "def test_shift_left():\n"
            "    assert shift_left([1, 2, 3])[:2] == [2, 3]\n",
            encoding="utf-8",
        )

        results = BenchmarkAblationRunner(use_dynamic_coverage=False).run_cases(
            [
                BenchmarkCase(
                    name="rule_precision_filter_case",
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

    variants = {result.variant: result for result in results}
    assert variants["full"].expected_rule_precision == 1.0
    assert variants["without_rule_precision_filter"].expected_rule_precision < 1.0
    assert variants["without_rule_precision_filter"].average_extra_rules >= 3.0


def test_patch_search_executes_and_scores_candidates():
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
        call_graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, call_graph)
        detector = RuleBasedBugDetector()
        findings = detector.detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidates = PatchGenerator().generate(repo, parsed.functions, ranked)
        search_results = PatchSearch(Sandbox(timeout=10), beam_width=3).search(
            repo,
            candidates,
            program_graph=program_graph,
        )

        assert search_results[0].success is True
        assert search_results[0].candidate.metadata.get("variant") == "shrink_range_upper_bound"
        assert search_results[0].score > 0.8
        assert diff_size(search_results[0].candidate.diff) > 0


def test_patch_search_prioritizes_candidates_before_beam_execution():
    sandbox = RecordingSandbox()
    low_localization = _candidate("low_localization", "low_function")
    high_localization = _candidate(
        "high_localization",
        "high_function",
        variant_rank=1,
    )

    results = PatchSearch(sandbox=sandbox, beam_width=1).search(
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


def test_patch_search_passes_test_args_to_sandbox():
    sandbox = RecordingSandbox()
    candidate = _candidate("scoped_test_candidate", "target_function")

    PatchSearch(sandbox=sandbox, beam_width=1).search(
        Path("."),
        [candidate],
        test_args=["tests/test_scoped.py::test_specific_case"],
    )

    assert sandbox.test_args_seen == [["tests/test_scoped.py::test_specific_case"]]


def test_patch_search_can_disable_prior_ranking_for_ablation():
    sandbox = RecordingSandbox()
    low_localization = _candidate("low_localization", "low_function")
    high_localization = _candidate("high_localization", "high_function")

    results = PatchSearch(
        sandbox=sandbox,
        beam_width=1,
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


def test_patch_search_deduplicates_equivalent_candidates_before_sandbox():
    sandbox = RecordingSandbox()
    primary = _candidate("dedupe_primary", "target_function")
    duplicate = replace(
        primary,
        id="dedupe_duplicate",
        metadata={**primary.metadata, "variant": "dedupe_duplicate"},
    )
    unique = _candidate("dedupe_unique", "target_function", variant_rank=1)

    results = PatchSearch(
        sandbox=sandbox,
        beam_width=3,
        use_prior_ranking=False,
    ).search(Path("."), [primary, duplicate, unique])

    assert sandbox.seen == ["dedupe_primary", "dedupe_unique"]
    assert [result.candidate.id for result in results] == [
        "dedupe_primary",
        "dedupe_unique",
    ]
    deduplication = results[0].candidate.metadata["search_deduplication"]
    assert deduplication["canonical_id"] == "dedupe_primary"
    assert deduplication["duplicate_count"] == 1
    assert deduplication["duplicate_ids"] == ["dedupe_duplicate"]
    assert results[0].candidate.metadata["search_duplicate_count"] == 1


def test_patch_search_can_disable_candidate_deduplication_for_ablation():
    sandbox = RecordingSandbox()
    primary = _candidate("dedupe_primary", "target_function")
    duplicate = replace(
        primary,
        id="dedupe_duplicate",
        metadata={**primary.metadata, "variant": "dedupe_duplicate"},
    )

    PatchSearch(
        sandbox=sandbox,
        beam_width=2,
        use_prior_ranking=False,
        use_candidate_deduplication=False,
    ).search(Path("."), [primary, duplicate])

    assert sandbox.seen == ["dedupe_primary", "dedupe_duplicate"]


def test_beam_patch_search_deduplicates_initial_pool_before_sandbox():
    sandbox = RecordingSandbox()
    primary = _candidate("beam_dedupe_primary", "target_function")
    duplicate = replace(
        primary,
        id="beam_dedupe_duplicate",
        metadata={**primary.metadata, "variant": "beam_dedupe_duplicate"},
    )
    unique = _candidate("beam_dedupe_unique", "target_function", variant_rank=1)

    nodes = BeamPatchSearch(
        sandbox=sandbox,
        beam_width=2,
        candidate_pool_size=3,
        max_depth=0,
        use_prior_ranking=False,
    ).search(Path("."), [primary, duplicate, unique])

    assert sandbox.seen == ["beam_dedupe_primary", "beam_dedupe_unique"]
    candidates = {node.candidate.id: node.candidate for node in nodes}
    deduplication = candidates["beam_dedupe_primary"].metadata[
        "search_deduplication"
    ]
    assert deduplication["duplicate_count"] == 1
    assert deduplication["duplicate_ids"] == ["beam_dedupe_duplicate"]


def test_benchmark_runner_reports_candidate_deduplication_budget_savings():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def target():\n"
            "    return 1\n",
            encoding="utf-8",
        )

        report = BenchmarkRunner(
            patch_generator=DuplicateCandidateGenerator(),
            sandbox=SuccessfulSandbox(),
            use_dynamic_coverage=False,
        ).run_cases(
            [
                BenchmarkCase(
                    name="candidate_deduplication_budget",
                    repo_path=str(repo),
                    buggy_functions=["target"],
                    expected_rule_ids=[],
                    failing_tests=[],
                    passed_tests=[],
                    test_args=[],
                    metadata={"bug_type": "search deduplication pressure"},
                )
            ]
        )

    result = report.cases[0]
    assert result.patch_candidates_count == 2
    assert len(result.beam_search_results) == 1
    assert result.beam_search_results[0]["candidate_id"] == "dedupe_primary_patch"
    assert result.beam_search_results[0]["search_duplicate_count"] == 1
    assert result.search_analysis["evaluated_nodes"] == 1
    assert result.search_analysis["deduplicated_candidates"] == 1
    assert result.search_analysis["effective_candidate_pool"] == 2
    assert result.search_analysis["deduplication_savings_ratio"] == 0.5

    summary = report.to_dict()["summary"]["search_budget_analysis"]
    assert summary["dedupe_affected_case_count"] == 1
    assert summary["total_deduplicated_candidates"] == 1
    assert summary["average_duplicate_pressure"] == 0.5


def test_patch_search_uses_execution_feedback_for_scoring_and_tie_breaker():
    syntax_candidate = _candidate("syntax_candidate", "same_function")
    assertion_candidate = _candidate("assertion_candidate", "same_function")

    results = PatchSearch(
        sandbox=FeedbackSandbox(),
        beam_width=2,
        use_prior_ranking=False,
    ).search(Path("."), [syntax_candidate, assertion_candidate])

    assert results[0].candidate.id == "assertion_candidate"
    assert results[0].score > results[1].score
    assert results[0].feedback_score > results[1].feedback_score
    assert results[0].candidate.metadata["execution_feedback"]["failure_type"] == "test_failure"
    assert results[1].candidate.metadata["execution_feedback"]["failure_type"] == "syntax_error"

    neutral_results = PatchSearch(
        sandbox=FeedbackSandbox(),
        beam_width=2,
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


def test_benchmark_runner_reports_localization_and_patch_success():
    report = BenchmarkRunner().run_manifest(MANIFEST)

    rules = {case.best_patch_rule_id for case in report.cases}

    cross_file_case = next(
        case for case in report.cases if case.case_name == "buggy_sample_cross_file_patch_risk"
    )

    assert len(report.cases) == 5
    assert report.top1 == 1.0
    assert report.top3 == 1.0
    assert report.mrr == 1.0
    assert report.map == 1.0
    assert report.expected_rule_recall == 1.0
    assert report.expected_rule_precision == 1.0
    assert report.patch_success_rate == 1.0
    assert report.multi_patch_success_rate == 0.0
    assert report.average_repair_rounds == 1.0
    assert report.average_patch_candidates == 1.4
    assert report.average_patch_size == 2.2
    assert report.average_patch_risk == 0.232
    assert report.reflection_success_rate == 0.0
    assert report.beam_success_rate == 1.0
    assert report.patch_search_top1_success_rate == 1.0
    assert report.patch_search_mrr == 1.0
    assert report.average_first_success_rank == 1.0
    assert report.average_beam_depth == 0.0
    assert report.average_evaluated_nodes >= 1.0
    assert report.average_failed_attempts_before_success == 0.0
    assert report.average_success_depth == 0.0
    assert report.search_efficiency == 1.0
    assert report.hypothesis_top1 == 1.0
    assert report.hypothesis_mrr == 1.0
    assert report.hypothesis_map == 1.0
    assert report.average_hypothesis_depth == 2.0
    assert report.average_hypothesis_evidence_count > 0.0
    assert report.bug_type_metrics["boundary error"]["case_count"] == 2
    assert report.bug_type_metrics["boundary error"]["top1"] == 1.0
    assert report.bug_type_metrics["boundary error"]["map"] == 1.0
    assert report.bug_type_metrics["boundary error"]["hypothesis_top1"] == 1.0
    assert report.bug_type_metrics["boundary error"]["hypothesis_mrr"] == 1.0
    assert report.bug_type_metrics["boundary error"]["hypothesis_map"] == 1.0
    assert report.bug_type_metrics["condition error"]["patch_success_rate"] == 1.0
    assert report.rule_metrics["possible_index_overrun"]["case_count"] == 2
    assert report.rule_metrics["possible_index_overrun"]["top1"] == 1.0
    assert report.rule_metrics["always_true_len_check"]["patch_success_rate"] == 1.0
    summary = report.to_dict()["summary"]
    assert summary["average_patch_size"] == 2.2
    assert summary["map"] == 1.0
    assert summary["multi_patch_success_rate"] == 0.0
    assert summary["expected_rule_precision"] == 1.0
    assert summary["patch_search_top1_success_rate"] == 1.0
    assert summary["patch_search_mrr"] == 1.0
    assert summary["average_first_success_rank"] == 1.0
    assert summary["average_evaluated_nodes"] >= 1.0
    assert summary["average_failed_attempts_before_success"] == 0.0
    assert summary["average_success_depth"] == 0.0
    assert summary["search_efficiency"] == 1.0
    assert summary["hypothesis_top1"] == 1.0
    assert summary["hypothesis_mrr"] == 1.0
    assert summary["hypothesis_map"] == 1.0
    assert summary["average_hypothesis_depth"] == 2.0
    assert summary["average_hypothesis_evidence_count"] > 0.0
    assert summary["data_flow_evidence_case_count"] == 2
    assert summary["cross_function_data_flow_case_count"] == 1
    assert summary["subscript_key_flow_case_count"] == 2
    assert summary["average_top1_data_dependency"] == 0.4
    assert summary["program_slice_case_count"] == 5
    assert summary["average_top1_slice_edges"] > 0.0
    assert summary["average_top1_slice_cross_function_edges"] >= 0.0
    assert summary["slice_grounded_case_count"] == 5
    assert summary["average_top1_slice_support"] > 0.0
    assert summary["average_top1_slice_failed_test_reachability"] == 1.0
    assert summary["average_top1_slice_call_chain_coverage"] == 1.0
    assert summary["patch_failure_taxonomy"]["success"] >= 1
    assert summary["bug_type_metrics"]["state leakage"]["mrr"] == 1.0
    assert summary["bug_type_metrics"]["state leakage"]["hypothesis_mrr"] == 1.0
    assert summary["bug_type_metrics"]["state leakage"]["expected_rule_precision"] == 1.0
    assert (
        summary["bug_type_metrics"]["state leakage"]["patch_search_mrr"]
        == 1.0
    )
    assert summary["bug_type_metrics"]["state leakage"]["search_efficiency"] == 1.0
    assert summary["rule_metrics"]["mutable_default_arg"]["mrr"] == 1.0
    assert summary["rule_metrics"]["possible_index_overrun"]["case_count"] == 2
    generalization = summary["generalization_report"]
    assert generalization["case_count"] == 5
    assert generalization["source_group_count"] == 1
    assert generalization["source_groups"]["unspecified"]["case_count"] == 5
    difficulty = summary["difficulty_report"]
    assert difficulty["case_count"] == 5
    assert sum(difficulty["bucket_counts"].values()) == 5
    assert difficulty["label_metrics"]["cross_file_patch"]["case_count"] == 1
    assert (
        difficulty["label_metrics"]["patch_candidate_competition"]["case_count"]
        >= 1
    )
    cross_file_difficulty = {
        row["case_name"]: row for row in difficulty["cases"]
    }["buggy_sample_cross_file_patch_risk"]
    assert cross_file_difficulty["bucket"] == "hard"
    assert "cross_function_trace" in cross_file_difficulty["labels"]
    assert "cross_file_patch" in cross_file_difficulty["labels"]
    assert {case.coverage_mode for case in report.cases} == {"dynamic_trace"}
    assert report.cases[0].bug_type == "boundary error"
    assert report.cases[0].metadata["bug_type"] == "boundary error"
    assert report.cases[0].to_dict()["metadata"]["bug_type"] == "boundary error"
    assert report.cases[0].average_precision == 1.0
    assert report.cases[0].expected_rule_precision == 1.0
    assert report.cases[0].extra_rule_ids == []
    assert report.cases[0].to_dict()["extra_rule_ids"] == []
    assert report.cases[0].localization_details[0]["failed_covered"] == 1
    assert report.cases[0].localization_details[0]["ochiai"] == 1.0
    assert report.cases[0].localization_details[0]["graph_components"]["test_coverage"] == 1.0
    assert report.cases[0].localization_details[0]["graph_components"]["line_coverage"] > 0.0
    assert report.cases[0].localization_details[0]["graph_components"]["data_dependency"] > 0.0
    assert report.cases[0].localization_details[0]["data_flow_evidence"] == {
        "internal_edges": 5,
        "key_flow_edges": 2,
        "arg_flow_edges": 0,
        "return_flow_edges": 0,
        "cross_function_edges": 0,
        "total_edges": 7,
    }
    program_slice = report.cases[0].localization_details[0]["program_slice"]
    assert program_slice["edge_count"] > 0
    assert program_slice["data_flow_edge_count"] >= 5
    assert program_slice["cfg_edge_count"] > 0
    assert "values" in program_slice["variables"]
    slice_grounding = report.cases[0].localization_details[0]["slice_grounding"]
    assert slice_grounding["grounded"] is True
    assert slice_grounding["support_score"] > 0.0
    assert slice_grounding["failed_test_reachability"] == 1.0
    assert "failed_test_support" in slice_grounding["support_reasons"]
    assert report.cases[0].localization_details[0]["graph_components"]["control_flow"] > 0.0
    assert report.cases[0].localization_details[0]["graph_components"]["pagerank"] > 0.0
    assert report.cases[0].best_patch_risk is not None
    assert report.cases[0].best_patch_risk["diff_size"] > 0
    assert report.cases[0].best_patch_risk["return_or_control_changed"] is True
    assert "values" in report.cases[0].best_patch_risk["changed_variables"]
    assert report.cases[0].multi_patch_success is False
    assert report.cases[0].multi_patch_results == []
    assert report.cases[0].repair_strategy == "beam_search"
    assert report.cases[0].to_dict()["repair_strategy"] == "beam_search"
    assert report.cases[0].repair_results == []
    assert report.cases[0].patch_search_results
    assert report.cases[0].patch_search_results[0]["variant"] == "shrink_range_upper_bound"
    assert report.cases[0].patch_search_results[0]["prior_score"] > 0.0
    assert report.cases[0].patch_search_results[0]["success"] is True
    assert report.cases[0].patch_search_results[0]["failure_type"] == "success"
    assert report.cases[0].beam_search_results
    assert report.cases[0].beam_search_results[0]["depth"] == 0
    assert report.cases[0].beam_search_results[0]["prior_score"] > 0.0
    assert report.cases[0].beam_search_results[0]["success"] is True
    assert report.cases[0].beam_search_results[0]["passed"] >= 1
    assert report.cases[0].beam_search_results[0]["failure_type"] == "success"
    assert report.cases[0].search_analysis["evaluated_nodes"] >= 1
    assert report.cases[0].search_analysis["first_success_rank"] == 1
    assert report.cases[0].search_analysis["first_success_depth"] == 0
    assert report.cases[0].search_analysis["failures_before_success"] == 0
    assert report.cases[0].search_analysis["efficiency"] == 1.0
    assert report.cases[0].to_dict()["search_analysis"]["first_success_rank"] == 1
    assert report.cases[0].hypothesis_top1_hit is True
    assert report.cases[0].hypothesis_mrr == 1.0
    assert report.cases[0].hypothesis_average_precision == 1.0
    assert report.cases[0].hypothesis_results
    assert report.cases[0].hypothesis_results[0]["function_name"] == "shift_left"
    assert report.cases[0].hypothesis_results[0]["depth"] == 2
    assert report.cases[0].hypothesis_results[0]["rule_ids"] == [
        "possible_index_overrun"
    ]
    assert report.cases[0].hypothesis_results[0]["evidence"]["candidate_count"] == 2
    assert "reasoning_steps" in report.cases[0].to_dict()["hypothesis_results"][0]
    assert cross_file_case.best_patch_risk is not None
    assert cross_file_case.best_patch_risk["affected_callers"] == 1
    assert cross_file_case.best_patch_risk["cross_file_callers"] == 1
    assert cross_file_case.best_patch_risk["return_or_control_changed"] is True
    assert (
        cross_file_case.localization_details[0]["graph_components"]["caller_impact"]
        > 0.0
    )
    assert cross_file_case.localization_details[0]["data_flow_evidence"][
        "cross_function_edges"
    ] == 1
    assert cross_file_case.localization_details[0]["call_chain"] == [
        "test_normalize_window",
        "normalize_window",
        "shift_left",
    ]
    assert rules == {
        "possible_index_overrun",
        "always_true_len_check",
        "broad_exception_pass",
        "mutable_default_arg",
    }

    markdown = render_benchmark_markdown(report)
    assert "Benchmark Report" in markdown
    assert "buggy_sample_shift_left" in markdown
    assert "MAP" in markdown
    assert "nDCG@3" in markdown
    assert "Expected Rule Recall" in markdown
    assert "Data Fanout" in markdown
    assert "Changed Vars" in markdown
    assert "Slice-grounded Localization" in markdown
    assert "Expected Rule Precision" in markdown
    assert "Extra Rules" in markdown
    assert "Average Repair Rounds" in markdown
    assert "Average Patch Size" in markdown
    assert "Reflection Success Rate" in markdown
    assert "Beam Success Rate" in markdown
    assert "Patch Search Top-1 Success Rate" in markdown
    assert "Multi-Patch Success Rate" in markdown
    assert "Patch Search MRR" in markdown
    assert "Patch Failure Taxonomy" in markdown
    assert "Failure Type" in markdown
    assert "Prior" in markdown
    assert "Average First Success Rank" in markdown
    assert "Average Beam Depth" in markdown
    assert "Average Evaluated Nodes" in markdown
    assert "Average Failed Attempts Before Success" in markdown
    assert "Search Efficiency" in markdown
    assert "Hypothesis Top-1" in markdown
    assert "Hypothesis MRR" in markdown
    assert "Hypothesis MAP" in markdown
    assert "Average Hypothesis Depth" in markdown
    assert "Data-flow Evidence Cases" in markdown
    assert "Cross-function Data-flow Cases" in markdown
    assert "Data-flow Evidence" in markdown
    assert "Metrics by Bug Type" in markdown
    assert "Metrics by Expected Rule" in markdown
    assert "boundary error" in markdown
    assert "possible_index_overrun" in markdown
    assert "Localization Details" in markdown
    assert "Ochiai" in markdown
    assert "Line Coverage" in markdown
    assert "Statement SBFL" in markdown
    assert "Branch SBFL" in markdown
    assert "Path SBFL" in markdown
    assert "Semantic" in markdown
    assert "Data Dependency" in markdown
    assert "Control Flow" in markdown
    assert "PageRank" in markdown
    assert "Caller Impact" in markdown
    assert "Module Dependency" in markdown
    assert "Async Call" in markdown
    assert "Call Chain" in markdown
    assert "test_normalize_window -> normalize_window -> shift_left" in markdown
    assert "Patch Risk" in markdown
    assert "Patch Risk Details" in markdown
    assert "Strategy" in markdown
    assert "Repair Results" in markdown
    assert "Patch Search Results" in markdown
    assert "Multi-Patch Results" in markdown
    assert "Relative Imports" in markdown
    assert "Max Package Distance" in markdown
    assert "Package Distance Bonus" in markdown
    assert "Key Flow" in markdown
    assert "Beam Search Results" in markdown
    assert "Patch Judge" in markdown
    assert "Search Analysis" in markdown
    assert "Hypothesis Search Results" in markdown
    assert "Benchmark Difficulty" in markdown
    assert "Difficulty Buckets" in markdown
    assert "Benchmark Generalization" in markdown
    assert "Source Group" in markdown
    assert "Source Balance Entropy" in markdown
    assert "Stability Score" in markdown
    assert "Worst Gap Score" in markdown
    assert "Benchmark Provenance Audit" in markdown
    assert "Case Provenance Coverage" in markdown
    assert "Leakage Risk Score" in markdown
    assert "patch_candidate_competition" in markdown
    assert "cross_file_patch" in markdown
    assert "Added patch-candidate availability" in markdown
    assert "shrink_range_upper_bound" in markdown
    assert "Proximity" in markdown


def test_benchmark_runner_uses_beam_search_as_primary_repair_strategy():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def target():\n"
            "    return 1\n",
            encoding="utf-8",
        )
        case = BenchmarkCase(
            name="beam_primary_case",
            repo_path=str(repo),
            buggy_functions=["target"],
            expected_rule_ids=[],
            failing_tests=[],
            passed_tests=[],
            test_args=[],
            metadata={"bug_type": "synthetic"},
        )
        sandbox = SuccessfulSandbox()
        report = BenchmarkRunner(
            patch_generator=SingleCandidateGenerator(),
            sandbox=sandbox,
        ).run_cases([case])

    result = report.cases[0]

    assert sandbox.calls == ["beam_primary_patch"]
    assert result.patch_success is True
    assert result.repair_strategy == "beam_search"
    assert result.repair_rounds == 1
    assert result.best_patch_rule_id == "beam_primary_rule"
    assert result.repair_results == []
    assert result.beam_search_results[0]["success"] is True
    assert result.beam_search_results[0]["failure_type"] == "success"


def test_benchmark_runner_records_depth_one_reflection_recovery():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def pairwise_deltas(values):\n"
            "    deltas = []\n"
            "    for i in range(len(values)):\n"
            "        deltas.append(values[i + 1] - values[i])\n"
            "    return deltas\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "from sample import pairwise_deltas\n\n"
            "def test_pairwise_deltas():\n"
            "    assert pairwise_deltas([1, 3, 6, 10]) == [2, 3, 4]\n",
            encoding="utf-8",
        )
        case = BenchmarkCase(
            name="reflection_depth_probe_case",
            repo_path=str(repo),
            buggy_functions=["pairwise_deltas"],
            expected_rule_ids=["possible_index_overrun"],
            failing_tests=["test_pairwise_deltas"],
            passed_tests=[],
            test_args=["test_sample.py"],
            metadata={
                "bug_type": "boundary error",
                "patch_score_profile": "reflection_depth_probe",
                "reflection_seed_variant": "overly_conservative_range_bound",
            },
        )

        report = BenchmarkRunner(use_dynamic_coverage=False).run_cases([case])

    result = report.cases[0]
    failed_seed = [
        node
        for node in result.beam_search_results
        if node["variant"] == "overly_conservative_range_bound"
    ]
    refined_success = [
        node
        for node in result.beam_search_results
        if node["variant"] == "reflection_shrink_range_upper_bound"
    ]
    reflection = report.to_dict()["summary"]["reflection_analysis"]

    assert result.patch_success is True
    assert result.repair_strategy == "beam_search"
    assert result.repair_rounds == 2
    assert result.search_analysis["first_success_depth"] == 1
    assert failed_seed
    assert failed_seed[0]["depth"] == 0
    assert failed_seed[0]["success"] is False
    assert failed_seed[0]["search_profile_role"] == "reflection_seed"
    assert refined_success
    assert refined_success[0]["depth"] == 1
    assert refined_success[0]["success"] is True
    assert refined_success[0]["parent_id"] == failed_seed[0]["candidate_id"]
    assert refined_success[0]["search_profile_role"] == (
        "reflection_refined_candidate"
    )
    assert reflection["reflection_case_count"] == 1
    assert reflection["reflection_success_case_count"] == 1
    assert reflection["reflection_candidate_count"] == 1
    assert reflection["reflection_candidate_success_rate"] == 1.0


def test_benchmark_runner_reports_map_for_multi_bug_case():
    report = BenchmarkRunner().run_manifest(MULTI_BUG_MANIFEST)
    case = report.cases[0]
    summary = report.to_dict()["summary"]

    assert len(report.cases) == 1
    assert case.case_name == "buggy_sample_multi_function"
    assert case.ground_truth == {"shift_left", "has_items"}
    assert case.ranked_functions[:2] == ["shift_left", "has_items"]
    assert report.map == 1.0
    assert case.average_precision == 1.0
    assert report.hypothesis_map == 1.0
    assert case.hypothesis_average_precision == 1.0
    assert summary["map"] == 1.0
    assert summary["ndcg_at_3"] == 1.0
    assert summary["mean_exam_score"] == 0.0
    assert summary["hypothesis_map"] == 1.0
    assert summary["hypothesis_ndcg_at_3"] == 1.0
    assert summary["hypothesis_mean_exam_score"] == 0.0
    assert summary["bug_type_metrics"]["multi bug"]["map"] == 1.0
    assert summary["bug_type_metrics"]["multi bug"]["hypothesis_map"] == 1.0
    assert summary["rule_metrics"]["always_true_len_check"]["case_count"] == 1
    assert summary["rule_metrics"]["possible_index_overrun"]["case_count"] == 1
    assert summary["rule_metrics"]["always_true_len_check"]["map"] == 1.0
    assert case.patch_success is True
    assert report.patch_success_rate == 1.0
    assert report.multi_patch_success_rate == 1.0
    assert summary["multi_patch_success_rate"] == 1.0
    assert summary["bug_type_metrics"]["multi bug"]["patch_success_rate"] == 1.0
    assert summary["bug_type_metrics"]["multi bug"]["multi_patch_success_rate"] == 1.0
    assert case.multi_patch_success is True
    assert case.multi_patch_bundle_size == 2
    assert case.multi_patch_rules == [
        "always_true_len_check",
        "possible_index_overrun",
    ]
    assert case.best_patch_rule_id == "always_true_len_check+possible_index_overrun"
    assert case.best_patch_risk is not None
    assert case.best_patch_risk["bundle_size"] == 2
    assert case.multi_patch_results[0]["success"] is True
    assert case.multi_patch_results[0]["passed"] == 3
    difficulty = summary["difficulty_report"]
    difficulty_case = difficulty["cases"][0]
    assert difficulty["bucket_counts"]["hard"] == 1
    assert difficulty_case["bucket"] == "hard"
    assert difficulty_case["ground_truth_count"] == 2
    assert difficulty_case["multi_patch_bundle_size"] == 2
    assert "multi_ground_truth" in difficulty_case["labels"]
    assert "multi_patch_bundle" in difficulty_case["labels"]


def test_localization_confidence_calibration_reports_brier_and_ece():
    report = BenchmarkReport(
        cases=[
            _calibration_case(
                "aligned_hit",
                0.90,
                True,
                source_group="repo/a",
                bug_type="guard",
                expected_rule_ids=["rule_a"],
            ),
            _calibration_case(
                "overconfident_miss",
                0.80,
                False,
                source_group="repo/a",
                bug_type="guard",
                expected_rule_ids=["rule_a"],
            ),
            _calibration_case(
                "underconfident_hit",
                0.20,
                True,
                source_group="repo/b",
                bug_type="type",
                expected_rule_ids=["rule_b"],
            ),
        ]
    )

    calibration = localization_calibration_report(report, bin_count=5)

    assert calibration.case_count == 3
    assert calibration.top1_positive_count == 2
    assert calibration.top1_accuracy == 0.6667
    assert calibration.brier_score == 0.43
    assert calibration.expected_calibration_error == 0.5
    assert calibration.calibration_model == "leave_one_out_beta_binning"
    assert calibration.calibrated_brier_score < calibration.brier_score
    assert (
        calibration.calibrated_expected_calibration_error
        < calibration.expected_calibration_error
    )
    assert calibration.brier_score_improvement > 0.0
    assert calibration.expected_calibration_error_improvement > 0.0
    assert calibration.overconfidence_rate == 0.3333
    assert calibration.underconfidence_rate == 0.3333
    assert calibration.agreement_counts == {
        "aligned": 1,
        "overconfident": 1,
        "underconfident": 1,
    }
    assert calibration.rows[1].agreement == "overconfident"
    assert calibration.rows[2].agreement == "underconfident"
    assert calibration.rows[1].calibrated_confidence != calibration.rows[1].confidence
    stratified = {
        (row.dimension, row.group): row
        for row in calibration.stratified_groups
    }
    assert stratified[("source_group", "repo/a")].case_count == 2
    assert stratified[("source_group", "repo/b")].case_count == 1
    assert stratified[("bug_type", "guard")].case_count == 2
    assert stratified[("expected_rule", "rule_a")].case_count == 2
    assert stratified[("expected_rule", "rule_b")].case_count == 1
    holdouts = {
        split.holdout_group: split
        for split in calibration.source_group_holdout_splits
    }
    assert set(holdouts) == {"repo/a", "repo/b"}
    assert holdouts["repo/a"].train_case_count == 1
    assert holdouts["repo/a"].holdout_case_count == 2
    assert holdouts["repo/b"].train_case_count == 2
    assert holdouts["repo/b"].holdout_case_count == 1
    assert (
        holdouts["repo/b"].holdout_calibrated_brier_score
        < holdouts["repo/b"].holdout_brier_score
    )

    markdown = render_benchmark_markdown(report)
    assert "Localization Confidence Calibration" in markdown
    assert "FinalScore Attribution" in markdown
    assert "Attribution Coverage" in markdown
    assert "Program Slice Evidence" in markdown
    assert "Key Flow Edges" in markdown
    assert "Localization Calibration Brier Score" in markdown
    assert "Expected Calibration Error" in markdown
    assert "Calibrated Expected Calibration Error" in markdown
    assert "Localization Calibration Stratification" in markdown
    assert "Source-Group Holdout Calibration" in markdown
    assert "source_group" in markdown
    assert "overconfident" in markdown

    summary = report.to_dict()["summary"]["localization_calibration"]
    assert summary["brier_score"] == 0.43
    assert summary["expected_calibration_error"] == (
        localization_calibration_report(report).expected_calibration_error
    )
    assert summary["calibrated_brier_score"] == (
        localization_calibration_report(report).calibrated_brier_score
    )
    assert len(summary["stratified_groups"]) == 6
    assert len(summary["source_group_holdout_splits"]) == 2
    attribution = report.to_dict()["summary"]["localization_attribution"]
    assert attribution["case_count"] == 3
    assert attribution["attribution_coverage"] == 1.0


def test_metric_uncertainty_reports_bootstrap_confidence_intervals():
    report = BenchmarkReport(
        cases=[
            _calibration_case("case_a", 0.90, True, patch_success=True),
            _calibration_case("case_b", 0.70, False, patch_success=False),
            _calibration_case("case_c", 0.60, True, patch_success=True),
            _calibration_case("case_d", 0.40, True, patch_success=False),
        ]
    )

    uncertainty = benchmark_metric_uncertainty_report(
        report,
        bootstrap_samples=200,
        seed=7,
    )

    assert uncertainty.case_count == 4
    assert uncertainty.bootstrap_samples == 200
    assert uncertainty.metrics["top1"].mean == 0.75
    assert uncertainty.metrics["patch_success_rate"].mean == 0.5
    assert uncertainty.metrics["mean_exam_score"].mean == 0.25
    assert 0.0 <= uncertainty.metrics["top1"].lower <= 0.75
    assert 0.75 <= uncertainty.metrics["top1"].upper <= 1.0
    assert uncertainty.metrics["top1"].width > 0.0

    second = benchmark_metric_uncertainty_report(
        report,
        bootstrap_samples=200,
        seed=7,
    )
    assert second.to_dict() == uncertainty.to_dict()

    markdown = render_benchmark_markdown(report)
    assert "## Metric Uncertainty" in markdown
    assert "Bootstrap Samples" in markdown
    assert "patch_success_rate" in markdown

    summary = report.to_dict()["summary"]["metric_uncertainty"]
    assert summary["metrics"]["top1"]["mean"] == 0.75
    assert summary["metrics"]["patch_success_rate"]["mean"] == 0.5


def test_search_budget_analysis_reports_success_curve_and_effort():
    report = BenchmarkReport(
        cases=[
            _search_budget_case(
                "early_success",
                evaluated=4,
                first_success=1,
                deduplicated=2,
            ),
            _search_budget_case("late_success", evaluated=4, first_success=3),
            _search_budget_case("missed_success", evaluated=4, first_success=None),
        ]
    )

    analysis = search_budget_analysis_report(report)

    assert analysis.case_count == 3
    assert analysis.evaluated_case_count == 3
    assert analysis.successful_case_count == 2
    assert analysis.max_budget == 4
    assert analysis.success_at_budget == {
        "1": 0.3333,
        "2": 0.3333,
        "3": 0.6667,
        "4": 0.6667,
    }
    assert analysis.budget_auc == 0.5
    assert analysis.first_success_rank_p50 == 2.0
    assert analysis.first_success_rank_p90 == 2.8
    assert analysis.average_normalized_effort == 0.6667
    assert analysis.average_wasted_nodes_after_success == 2.6667
    assert analysis.dedupe_affected_case_count == 1
    assert analysis.total_deduplicated_candidates == 2
    assert analysis.max_deduplicated_candidates == 2
    assert analysis.average_deduplicated_candidates == 0.6667
    assert analysis.average_duplicate_pressure == 0.1111
    assert analysis.rows[0].deduplicated_candidates == 2
    assert analysis.rows[0].effective_candidate_pool == 6
    assert analysis.rows[0].duplicate_pressure == 0.3333
    assert analysis.budget_points[2].marginal_success_count == 1

    summary = report.to_dict()["summary"]["search_budget_analysis"]
    assert summary["budget_auc"] == 0.5
    assert summary["success_at_budget"]["1"] == 0.3333
    assert summary["first_success_rank_p90"] == 2.8
    assert summary["total_deduplicated_candidates"] == 2
    assert summary["average_duplicate_pressure"] == 0.1111

    markdown = render_benchmark_markdown(report)
    assert "Search Budget Analysis" in markdown
    assert "Budget AUC" in markdown
    assert "Deduped Candidates" in markdown
    assert "Duplicate Pressure" in markdown
    assert "First Success Rank p50/p90" in markdown
    assert "early_success" in markdown


def test_search_competition_analysis_reports_candidate_pressure():
    report = BenchmarkReport(
        cases=[
            _search_competition_case(
                "reranked_success",
                [
                    _beam_node(
                        rank=1,
                        candidate_id="decoy",
                        rule_id="rule_a",
                        score=0.90,
                        success=False,
                        failure_type="test_failure",
                        bucket="near_miss",
                    ),
                    _beam_node(
                        rank=2,
                        candidate_id="fix",
                        rule_id="rule_b",
                        score=0.80,
                        success=True,
                        failure_type="success",
                        bucket="success",
                        diversity_base_rank=5,
                        diversity_rank=2,
                        diversity_bonus=0.55,
                    ),
                ],
            ),
            _search_competition_case(
                "single_success",
                [
                    _beam_node(
                        rank=1,
                        candidate_id="direct_fix",
                        rule_id="rule_a",
                        score=0.95,
                        success=True,
                        failure_type="success",
                        bucket="success",
                    )
                ],
            ),
        ]
    )

    analysis = search_competition_analysis_report(report)

    assert analysis.case_count == 2
    assert analysis.beam_case_count == 2
    assert analysis.multi_candidate_case_count == 1
    assert analysis.successful_case_count == 2
    assert analysis.top_rank_success_count == 1
    assert analysis.score_inversion_count == 1
    assert analysis.to_dict()["score_inversion_rate"] == 0.5
    assert analysis.average_failure_pressure == 0.25
    assert analysis.average_rule_diversity == 1.5
    assert analysis.average_failure_type_diversity == 0.5
    assert analysis.average_retention_bucket_diversity == 1.5
    assert analysis.multi_candidate_average_rule_diversity == 2.0
    assert analysis.multi_candidate_average_failure_type_diversity == 1.0
    assert analysis.multi_candidate_average_retention_bucket_diversity == 2.0
    assert analysis.average_competing_failures_before_success == 0.5
    assert analysis.diversity_lift_case_count == 1
    assert analysis.diversity_assisted_success_count == 1
    assert analysis.average_diversity_lift == 1.5
    assert analysis.average_success_diversity_lift == 3.0
    assert analysis.average_diversity_bonus == 0.1375
    assert analysis.average_success_diversity_bonus == 0.55
    assert analysis.budget_sensitive_diversity_success_count == 1
    assert analysis.average_success_budget_gap_before_rerank == 3.0
    assert analysis.average_success_budget_margin_after_rerank == 0.0
    assert analysis.max_diversity_lift == 3
    assert analysis.rows[0].max_diversity_lift == 3
    assert analysis.rows[0].success_diversity_lift == 3
    assert analysis.rows[0].success_diversity_bonus == 0.55
    assert analysis.rows[0].diversity_assisted_success is True
    assert analysis.rows[0].success_base_rank == 5
    assert analysis.rows[0].success_diversity_rank == 2
    assert analysis.rows[0].success_actual_rank == 2
    assert analysis.rows[0].success_budget_gap_before_rerank == 3
    assert analysis.rows[0].success_budget_margin_after_rerank == 0
    assert analysis.rows[0].budget_sensitive_diversity_success is True
    assert (
        analysis.rows[0].counterfactual_condition
        == "base_rank_outside_budget_and_reranked_inside_budget"
    )
    assert analysis.top_failure_type_counts == {"test_failure": 1}

    summary = report.to_dict()["summary"]["search_competition_analysis"]
    assert summary["multi_candidate_case_count"] == 1
    assert summary["score_inversion_rate"] == 0.5
    assert summary["diversity_lift_case_rate"] == 0.5
    assert summary["diversity_assisted_success_rate"] == 0.5
    assert summary["average_success_diversity_lift"] == 3.0
    assert summary["budget_sensitive_diversity_success_count"] == 1
    assert summary["budget_sensitive_diversity_success_rate"] == 0.5
    assert summary["projected_without_diversity_success_count"] == 1
    assert summary["projected_without_diversity_success_rate"] == 0.5
    assert summary["projected_without_diversity_success_delta"] == -0.5
    assert summary["average_success_budget_gap_before_rerank"] == 3.0
    assert summary["average_success_budget_margin_after_rerank"] == 0.0
    assert summary["multi_candidate_average_rule_diversity"] == 2.0
    assert summary["multi_candidate_average_failure_type_diversity"] == 1.0
    assert summary["multi_candidate_average_retention_bucket_diversity"] == 2.0
    assert summary["rows"][0]["budget_sensitive_diversity_success"] is True
    case_payload = report.to_dict()["cases"][0]
    case_audit = case_payload["search_competition_audit"]
    assert case_audit["success_base_rank"] == 5
    assert case_audit["success_diversity_rank"] == 2
    assert case_audit["success_budget_gap_before_rerank"] == 3
    assert case_audit["budget_sensitive_diversity_success"] is True

    markdown = render_benchmark_markdown(report)
    assert "Search Competition Analysis" in markdown
    assert "Score Inversion Rate" in markdown
    assert "Diversity-Assisted Successes" in markdown
    assert "Success Diversity Lift" in markdown
    assert "Budget-Sensitive Diversity Successes" in markdown
    assert "Projected Without-Diversity Success Delta" in markdown
    assert "base_rank_outside_budget_and_reranked_inside_budget" in markdown
    assert "Multi-Candidate Failure Diversity" in markdown
    assert "reranked_success" in markdown


def test_reflection_analysis_reports_refined_candidate_recovery():
    success_parent = _beam_node(
        rank=1,
        candidate_id="seed_patch",
        rule_id="rule_seed",
        score=0.20,
        success=False,
        failure_type="test_failure",
        bucket="test_failure",
    )
    success_child = {
        **_beam_node(
            rank=2,
            candidate_id="refined_patch",
            rule_id="rule_refined",
            score=0.85,
            success=True,
            failure_type="success",
            bucket="success",
        ),
        "depth": 1,
        "parent_id": "seed_patch",
        "child_index": 0,
        "sibling_count": 1,
    }
    failed_parent = _beam_node(
        rank=1,
        candidate_id="syntax_seed",
        rule_id="rule_seed",
        score=0.10,
        success=False,
        failure_type="syntax_error",
        bucket="hard_failure",
    )
    failed_child = {
        **_beam_node(
            rank=2,
            candidate_id="syntax_refined",
            rule_id="rule_refined",
            score=0.12,
            success=False,
            failure_type="syntax_error",
            bucket="hard_failure",
        ),
        "depth": 1,
        "parent_id": "syntax_seed",
        "child_index": 0,
        "sibling_count": 1,
        "retained": False,
    }
    report = BenchmarkReport(
        cases=[
            replace(
                _calibration_case(
                    "reflection_success",
                    confidence=0.8,
                    top1_hit=True,
                    patch_success=True,
                ),
                beam_search_results=[success_parent, success_child],
            ),
            replace(
                _calibration_case(
                    "reflection_failure",
                    confidence=0.7,
                    top1_hit=True,
                    patch_success=False,
                ),
                beam_search_results=[failed_parent, failed_child],
            ),
            replace(
                _calibration_case(
                    "direct_success",
                    confidence=0.9,
                    top1_hit=True,
                    patch_success=True,
                ),
                beam_search_results=[
                    _beam_node(
                        rank=1,
                        candidate_id="direct_fix",
                        rule_id="rule_direct",
                        score=0.95,
                        success=True,
                        failure_type="success",
                        bucket="success",
                    )
                ],
            ),
        ]
    )

    analysis = reflection_analysis_report(report)

    assert analysis.case_count == 3
    assert analysis.reflection_case_count == 2
    assert analysis.reflection_success_case_count == 1
    assert analysis.reflection_candidate_count == 2
    assert analysis.retained_reflection_candidate_count == 1
    assert analysis.successful_reflection_candidate_count == 1
    assert analysis.reflection_case_success_rate == 0.5
    assert analysis.reflection_candidate_success_rate == 0.5
    assert analysis.average_reflection_depth == 1.0
    assert analysis.average_success_reflection_depth == 1.0
    assert analysis.average_score_delta_from_parent == 0.335
    assert analysis.parent_failure_type_counts == {
        "syntax_error": 1,
        "test_failure": 1,
    }
    assert analysis.parent_retention_bucket_counts == {
        "hard_failure": 1,
        "test_failure": 1,
    }
    assert analysis.success_parent_failure_type_counts == {"test_failure": 1}
    assert analysis.rows[0].reflection_success is True
    assert analysis.rows[0].success_parent_failure_type == "test_failure"

    summary = report.to_dict()["summary"]["reflection_analysis"]
    assert summary["reflection_case_count"] == 2
    assert summary["reflection_candidate_success_rate"] == 0.5
    assert summary["rows"][0]["success_parent_retention_bucket"] == "test_failure"

    markdown = render_benchmark_markdown(report)
    assert "Reflection Analysis" in markdown
    assert "Candidate Success Rate" in markdown
    assert "reflection_success" in markdown
    assert "test_failure" in markdown


def test_github_fetcher_builds_raw_url_and_fetches_local_source():
    assert (
        github_raw_url("owner", "repo", "main", "path/to/file.py")
        == "https://raw.githubusercontent.com/owner/repo/main/path/to/file.py"
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        source = root / "source.py"
        source.write_text("VALUE = 42\n", encoding="utf-8")
        output = root / "out"

        written = GitHubBenchmarkFetcher().fetch_sources(
            [
                FetchSource(
                    raw_url=str(source),
                    target_path="case/source.py",
                )
            ],
            output,
        )

        assert written == [output / "case" / "source.py"]
        assert written[0].read_text(encoding="utf-8") == "VALUE = 42\n"


def _calibration_case(
    case_name: str,
    confidence: float,
    top1_hit: bool,
    patch_success: bool = False,
    source_group: str = "synthetic/repo",
    bug_type: str = "calibration",
    expected_rule_ids: list[str] | None = None,
) -> BenchmarkCaseResult:
    top_function = f"{case_name}_top"
    truth = top_function if top1_hit else f"{case_name}_truth"
    rules = expected_rule_ids if expected_rule_ids is not None else []
    return BenchmarkCaseResult(
        case_name=case_name,
        bug_type=bug_type,
        ranked_functions=[top_function],
        ground_truth={truth},
        top1_hit=top1_hit,
        top3_hit=top1_hit,
        mrr=1.0 if top1_hit else 0.0,
        average_precision=1.0 if top1_hit else 0.0,
        ndcg_at_3=1.0 if top1_hit else 0.0,
        exam_score=0.0 if top1_hit else 1.0,
        findings_count=1,
        patch_candidates_count=0,
        expected_rule_ids=rules,
        detected_rule_ids=[],
        expected_rule_recall=1.0,
        expected_rule_precision=1.0,
        extra_rule_ids=[],
        coverage_mode="synthetic",
        localization_details=[
            {
                "rank": 1,
                "function_name": top_function,
                "score": confidence,
                "failed_covered": 1 if top1_hit else 0,
                "passed_covered": 0,
                "total_failed": 1,
                "ochiai": confidence,
                "signals": {"sbfl": confidence},
                "call_chain": [],
                "data_flow_evidence": {},
                "graph_components": {},
            }
        ],
        patch_success=patch_success,
        repair_rounds=0,
        repair_strategy="none",
        repair_results=[],
        best_patch_rule_id="synthetic_rule" if patch_success else None,
        best_patch_risk=None,
        multi_patch_success=False,
        multi_patch_bundle_size=0,
        multi_patch_rules=[],
        multi_patch_results=[],
        patch_search_results=[],
        beam_search_results=[],
        search_analysis={},
        hypothesis_results=[],
        hypothesis_top1_hit=top1_hit,
        hypothesis_mrr=1.0 if top1_hit else 0.0,
        hypothesis_average_precision=1.0 if top1_hit else 0.0,
        hypothesis_ndcg_at_3=1.0 if top1_hit else 0.0,
        hypothesis_exam_score=0.0 if top1_hit else 1.0,
        metadata={"upstream": source_group, "bug_type": bug_type},
    )


def _search_budget_case(
    case_name: str,
    *,
    evaluated: int,
    first_success: int | None,
    deduplicated: int = 0,
) -> BenchmarkCaseResult:
    base = _calibration_case(
        case_name,
        confidence=0.8,
        top1_hit=first_success is not None,
        patch_success=first_success is not None,
    )
    return replace(
        base,
        search_analysis={
            "evaluated_nodes": evaluated,
            "successful_nodes": 1 if first_success is not None else 0,
            "max_depth": 0,
            "first_success_rank": first_success,
            "first_success_depth": 0 if first_success is not None else None,
            "failures_before_success": (
                first_success - 1 if first_success is not None else evaluated
            ),
            "success_score_margin": 0.1 if first_success is not None else 0.0,
            "efficiency": (
                round(1.0 / first_success, 4)
                if first_success is not None and first_success > 0
                else 0.0
            ),
            "deduplicated_candidates": deduplicated,
            "effective_candidate_pool": evaluated + deduplicated,
            "deduplication_savings_ratio": (
                round(deduplicated / (evaluated + deduplicated), 4)
                if evaluated + deduplicated > 0
                else 0.0
            ),
        },
    )


def _search_competition_case(
    case_name: str,
    beam_nodes: list[dict],
) -> BenchmarkCaseResult:
    first_success_rank = next(
        (
            index
            for index, node in enumerate(beam_nodes, start=1)
            if node.get("success", False)
        ),
        None,
    )
    return replace(
        _calibration_case(
            case_name,
            0.8,
            top1_hit=first_success_rank is not None,
            patch_success=first_success_rank is not None,
        ),
        beam_search_results=beam_nodes,
        search_analysis={
            "evaluated_nodes": len(beam_nodes),
            "successful_nodes": sum(
                1 for node in beam_nodes if node.get("success", False)
            ),
            "max_depth": max((int(node.get("depth", 0)) for node in beam_nodes), default=0),
            "first_success_rank": first_success_rank,
            "first_success_depth": (
                beam_nodes[first_success_rank - 1].get("depth", 0)
                if first_success_rank is not None
                else None
            ),
            "failures_before_success": (
                first_success_rank - 1
                if first_success_rank is not None
                else len(beam_nodes)
            ),
            "success_score_margin": 0.0,
            "efficiency": (
                round(1.0 / first_success_rank, 4)
                if first_success_rank is not None
                else 0.0
            ),
        },
    )


def _beam_node(
    *,
    rank: int,
    candidate_id: str,
    rule_id: str,
    score: float,
    success: bool,
    failure_type: str,
    bucket: str,
    diversity_base_rank: int | None = None,
    diversity_rank: int | None = None,
    diversity_bonus: float = 0.0,
) -> dict:
    node = {
        "rank": rank,
        "candidate_id": candidate_id,
        "parent_id": None,
        "variant": candidate_id,
        "rule_id": rule_id,
        "depth": 0,
        "child_index": None,
        "sibling_count": None,
        "prior_score": score,
        "score": score,
        "feedback_score": 1.0 if success else 0.0,
        "retained": True,
        "retention_bucket": bucket,
        "retention_reason": "synthetic search competition node",
        "success": success,
        "passed": 1 if success else 0,
        "failed": 0 if success else 1,
        "failure_type": failure_type,
        "failure_reason": "",
        "trace": [],
    }
    if diversity_base_rank is not None or diversity_rank is not None:
        resolved_rank = diversity_rank if diversity_rank is not None else rank
        resolved_base = diversity_base_rank if diversity_base_rank is not None else rank
        node.update(
            {
                "diversity_rank": resolved_rank,
                "diversity_bonus": diversity_bonus,
                "diversity_score": score + diversity_bonus,
                "search_diversity": {
                    "base_rank": resolved_base,
                    "rank": resolved_rank,
                    "bonus": diversity_bonus,
                    "score": score + diversity_bonus,
                    "reasons": ["new_rule", "new_variant"],
                },
            }
        )
    return node
