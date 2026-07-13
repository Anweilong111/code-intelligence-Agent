from __future__ import annotations

import ast
import math
import re
import textwrap
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from code_intelligence_agent.core.models import (
    BugFinding,
    FaultLocalizationResult,
    TestExecutionSummary,
)
from code_intelligence_agent.core.program_graph import ProgramGraph


@dataclass(frozen=True)
class ScoreWeights:
    sbfl: float = 0.30
    graph: float = 0.25
    static: float = 0.15
    semantic: float = 0.10
    llm: float = 0.15
    risk: float = 0.05
    test_failure: float = 0.0
    traceback: float = 0.0
    complexity: float = 0.0
    change_history: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "sbfl": self.sbfl,
            "graph": self.graph,
            "static": self.static,
            "semantic": self.semantic,
            "llm": self.llm,
            "risk": self.risk,
            "test_failure": self.test_failure,
            "traceback": self.traceback,
            "complexity": self.complexity,
            "change_history": self.change_history,
        }


DEFAULT_COVERAGE_WEIGHTS = ScoreWeights()
DEFAULT_STATIC_ONLY_WEIGHTS = ScoreWeights(
    sbfl=0.0,
    graph=0.20,
    static=0.60,
    semantic=0.10,
    llm=0.10,
    risk=0.0,
)

LEGACY_V1_FUSION = "legacy_v1"
EVIDENCE_V2_FUSION = "evidence_v2"

DEFAULT_EVIDENCE_V2_COVERAGE_WEIGHTS = ScoreWeights(
    sbfl=0.22,
    graph=0.18,
    static=0.15,
    semantic=0.05,
    llm=0.05,
    risk=0.05,
    test_failure=0.15,
    traceback=0.10,
    complexity=0.05,
    change_history=0.05,
)
DEFAULT_EVIDENCE_V2_STATIC_ONLY_WEIGHTS = ScoreWeights(
    sbfl=0.0,
    graph=0.25,
    static=0.45,
    semantic=0.10,
    llm=0.05,
    risk=0.05,
    test_failure=0.0,
    traceback=0.0,
    complexity=0.10,
    change_history=0.05,
)


@dataclass(frozen=True)
class FaultLocalizationConfig:
    fusion_profile: str = LEGACY_V1_FUSION
    use_line_coverage: bool = True
    use_branch_coverage: bool = True
    use_path_coverage: bool = True
    use_data_dependency: bool = True
    use_control_flow: bool = True
    use_pagerank: bool = True
    use_caller_impact: bool = True
    use_module_dependency: bool = True
    use_async_calls: bool = True
    use_dynamic_test_evidence: bool = True
    use_stacktrace_score: bool = True
    use_complexity_score: bool = True
    use_change_history_score: bool = True
    use_semantic_similarity: bool = True
    use_llm_score: bool = True
    graph_propagation_max_depth: int = 3
    graph_propagation_decay: float = 0.5
    llm_requires_program_evidence: bool = True
    max_llm_contribution: float = 0.10
    coverage_weights: ScoreWeights = field(default_factory=ScoreWeights)
    static_only_weights: ScoreWeights = field(
        default_factory=lambda: DEFAULT_STATIC_ONLY_WEIGHTS
    )


def evidence_v2_localization_config() -> FaultLocalizationConfig:
    return FaultLocalizationConfig(
        fusion_profile=EVIDENCE_V2_FUSION,
        coverage_weights=DEFAULT_EVIDENCE_V2_COVERAGE_WEIGHTS,
        static_only_weights=DEFAULT_EVIDENCE_V2_STATIC_ONLY_WEIGHTS,
    )


class FaultLocalizationLLMScorer(Protocol):
    def score(
        self,
        *,
        program_graph: ProgramGraph,
        findings: list[BugFinding],
        test_summary: TestExecutionSummary,
        candidate_function_ids: list[str],
    ) -> dict[str, float]:
        ...


class FaultLocalizer:
    def __init__(
        self,
        config: FaultLocalizationConfig | None = None,
        llm_scorer: FaultLocalizationLLMScorer | None = None,
    ) -> None:
        self.config = config or FaultLocalizationConfig()
        self.llm_scorer = llm_scorer

    def rank(
        self,
        program_graph: ProgramGraph,
        findings: list[BugFinding],
        test_summary: TestExecutionSummary | None = None,
        top_k: int | None = None,
        change_history_scores: dict[str, float] | None = None,
    ) -> list[FaultLocalizationResult]:
        test_summary = test_summary or TestExecutionSummary()
        findings_by_function: dict[str, list[BugFinding]] = defaultdict(list)
        for finding in findings:
            findings_by_function[finding.function_id].append(finding)

        candidate_functions = {
            function_id: function
            for function_id, function in program_graph.functions.items()
            if not function.metadata.get("is_test")
            and not function.metadata.get("is_test_file")
        }

        max_degree = max(
            (
                program_graph.degree(function_id, {"calls", "tested_by"})
                for function_id in candidate_functions
            ),
            default=1,
        )
        max_in_degree = max(
            (
                program_graph.in_degree(function_id, {"calls"})
                for function_id in candidate_functions
            ),
            default=1,
        )
        max_data_dependency_degree = max(
            (
                _data_dependency_degree(program_graph, function_id)
                for function_id in candidate_functions
            ),
            default=1,
        )
        max_control_flow_degree = max(
            (
                _control_flow_degree(program_graph, function_id)
                for function_id in candidate_functions
            ),
            default=1,
        )
        caller_reverse_edges = _production_reverse_call_edges(program_graph)
        caller_impact_scores = {
            function_id: _caller_impact_score(
                program_graph,
                function_id,
                caller_reverse_edges,
            )
            for function_id in candidate_functions
        }
        max_caller_impact = max(caller_impact_scores.values(), default=1.0)
        max_module_dependency = max(
            (
                _module_dependency_score(program_graph, function_id)
                for function_id in candidate_functions
            ),
            default=1.0,
        )
        max_async_call = max(
            (
                _async_call_score(program_graph, function_id)
                for function_id in candidate_functions
            ),
            default=1.0,
        )
        pagerank_scores = _pagerank_scores(program_graph)
        max_pagerank = max(
            (
                pagerank_scores.get(function_id, 0.0)
                for function_id in candidate_functions
            ),
            default=1.0,
        )
        semantic_query_tokens = _semantic_query_tokens(test_summary, program_graph)
        llm_scores = self._llm_scores(
            program_graph=program_graph,
            findings=findings,
            test_summary=test_summary,
            candidate_function_ids=list(candidate_functions),
        )
        raw_complexities = {
            function_id: _cyclomatic_complexity(function.source)
            for function_id, function in candidate_functions.items()
        }
        max_complexity_excess = max(
            (max(0, value - 1) for value in raw_complexities.values()),
            default=0,
        )
        history_scores = {
            function_id: _clamp(score)
            for function_id, score in (change_history_scores or {}).items()
            if function_id in candidate_functions
        }

        results: list[FaultLocalizationResult] = []
        for function_id, function in candidate_functions.items():
            function_findings = findings_by_function.get(function_id, [])
            static_score = _combine_confidence(
                finding.confidence for finding in function_findings
            )
            sbfl_score = ochiai(function_id, test_summary)
            graph_signals = self._graph_signals(
                function_id=function_id,
                static_score=static_score,
                program_graph=program_graph,
                test_summary=test_summary,
                max_degree=max_degree,
                max_in_degree=max_in_degree,
                max_data_dependency_degree=max_data_dependency_degree,
                max_control_flow_degree=max_control_flow_degree,
                max_caller_impact=max_caller_impact,
                caller_impact_scores=caller_impact_scores,
                max_module_dependency=max_module_dependency,
                max_async_call=max_async_call,
                pagerank_scores=pagerank_scores,
                max_pagerank=max_pagerank,
            )
            graph_score = graph_signals["graph"]
            patch_risk = graph_signals["patch_risk"]
            test_failure_score = graph_signals["dynamic_test_evidence"]
            stacktrace_score = self._stacktrace_score(
                function_id=function_id,
                program_graph=program_graph,
                test_summary=test_summary,
            )
            complexity_score = (
                _normalized_complexity(
                    raw_complexities.get(function_id, 1),
                    max_complexity_excess=max_complexity_excess,
                )
                if self.config.use_complexity_score
                else 0.0
            )
            change_history_score = (
                history_scores.get(function_id, 0.0)
                if self.config.use_change_history_score
                else 0.0
            )
            semantic_score = (
                _semantic_similarity(
                    function=function,
                    findings=function_findings,
                    query_tokens=semantic_query_tokens,
                )
                if self.config.use_semantic_similarity
                else 0.0
            )
            raw_llm_score = llm_scores.get(function_id, 0.0)
            weights = (
                self.config.coverage_weights
                if test_summary.has_coverage()
                else self.config.static_only_weights
            )
            if self.config.fusion_profile == EVIDENCE_V2_FUSION:
                graph_score = graph_signals["structural_graph"]
                llm_score = self._effective_llm_score(
                    raw_llm_score=raw_llm_score,
                    weights=weights,
                    program_evidence=(
                        sbfl_score,
                        static_score,
                        test_failure_score,
                        stacktrace_score,
                        semantic_score,
                    ),
                )
            else:
                llm_score = raw_llm_score
                test_failure_score = 0.0
                stacktrace_score = 0.0
                complexity_score = 0.0
                change_history_score = 0.0
            score_signals = {
                "sbfl": sbfl_score,
                "graph": graph_score,
                "static": static_score,
                "semantic": semantic_score,
                "llm": llm_score,
                "risk": patch_risk,
                "test_failure": test_failure_score,
                "traceback": stacktrace_score,
                "complexity": complexity_score,
                "change_history": change_history_score,
            }
            final_score = score_with_weights(
                score_signals,
                weights,
            )
            contributions = score_contributions(score_signals, weights)
            contribution_sum = sum(contributions.values())
            signals = {
                "sbfl": round(sbfl_score, 4),
                "graph": round(graph_score, 4),
                "static": round(static_score, 4),
                "semantic": round(semantic_score, 4),
                "llm": round(llm_score, 4),
                "llm_raw": round(raw_llm_score, 4),
                "risk": round(patch_risk, 4),
                "test_failure": round(test_failure_score, 4),
                "traceback": round(stacktrace_score, 4),
                "complexity": round(complexity_score, 4),
                "complexity_raw": float(raw_complexities.get(function_id, 1)),
                "change_history": round(change_history_score, 4),
                "structural_graph": round(graph_signals["structural_graph"], 4),
                "traceback_hit": round(graph_signals["traceback_hit"], 4),
                "test_coverage": round(graph_signals["test_coverage"], 4),
                "line_coverage": round(graph_signals["line_coverage"], 4),
                "statement_sbfl": round(graph_signals["statement_sbfl"], 4),
                "branch_sbfl": round(graph_signals["branch_sbfl"], 4),
                "path_sbfl": round(graph_signals["path_sbfl"], 4),
                "data_dependency": round(graph_signals["data_dependency"], 4),
                "control_flow": round(graph_signals["control_flow"], 4),
                "pagerank": round(graph_signals["pagerank"], 4),
                "proximity": round(graph_signals["proximity"], 4),
                "caller_impact": round(graph_signals["caller_impact"], 4),
                "module_dependency": round(graph_signals["module_dependency"], 4),
                "async_call": round(graph_signals["async_call"], 4),
                "dynamic_test_evidence": round(
                    graph_signals["dynamic_test_evidence"],
                    4,
                ),
                "centrality": round(graph_signals["centrality"], 4),
                "patch_risk": round(graph_signals["patch_risk"], 4),
                "test_failure_available": float(
                    bool(test_summary.dynamic_evidence_test_ids)
                ),
                "traceback_available": float(
                    bool(
                        test_summary.dynamic_traceback_function_ids
                        if self.config.fusion_profile == EVIDENCE_V2_FUSION
                        else test_summary.traceback_function_ids
                    )
                ),
                "coverage_available": float(test_summary.has_coverage()),
                "llm_available": float(function_id in llm_scores),
                "change_history_available": float(function_id in history_scores),
                "score_reconstruction": round(_clamp(contribution_sum), 6),
                "contribution_clamp_adjustment": round(
                    final_score - contribution_sum,
                    6,
                ),
            }
            for component, contribution in contributions.items():
                signals[f"contribution_{component}"] = round(contribution, 6)
            for component, weight in weights.to_dict().items():
                signals[f"weight_{component}"] = round(weight, 6)
            results.append(
                FaultLocalizationResult(
                    function_id=function.id,
                    function_name=function.metadata.get("qualified_name", function.name),
                    file_path=function.file_path,
                    start_line=function.start_line,
                    end_line=function.end_line,
                    score=round(final_score, 4),
                    rank=0,
                    signals=signals,
                    findings=function_findings,
                    reason=_reason(signals, function_findings),
                )
            )

        results.sort(key=lambda item: item.score, reverse=True)
        ranked = [
            FaultLocalizationResult(
                function_id=item.function_id,
                function_name=item.function_name,
                file_path=item.file_path,
                start_line=item.start_line,
                end_line=item.end_line,
                score=item.score,
                rank=index + 1,
                signals=item.signals,
                findings=item.findings,
                reason=item.reason,
            )
            for index, item in enumerate(results)
        ]
        if top_k is not None:
            return ranked[:top_k]
        return ranked

    def _effective_llm_score(
        self,
        *,
        raw_llm_score: float,
        weights: ScoreWeights,
        program_evidence: tuple[float, ...],
    ) -> float:
        if raw_llm_score <= 0.0:
            return 0.0
        if self.config.llm_requires_program_evidence and not any(
            value > 0.0 for value in program_evidence
        ):
            return 0.0
        if weights.llm <= 0.0:
            return 0.0
        contribution_cap = max(0.0, self.config.max_llm_contribution)
        return min(raw_llm_score, contribution_cap / weights.llm)

    def _llm_scores(
        self,
        *,
        program_graph: ProgramGraph,
        findings: list[BugFinding],
        test_summary: TestExecutionSummary,
        candidate_function_ids: list[str],
    ) -> dict[str, float]:
        if not self.config.use_llm_score or self.llm_scorer is None:
            return {}
        return {
            function_id: _clamp(score)
            for function_id, score in self.llm_scorer.score(
                program_graph=program_graph,
                findings=findings,
                test_summary=test_summary,
                candidate_function_ids=candidate_function_ids,
            ).items()
        }

    def _graph_signals(
        self,
        function_id: str,
        static_score: float,
        program_graph: ProgramGraph,
        test_summary: TestExecutionSummary,
        max_degree: int,
        max_in_degree: int,
        max_data_dependency_degree: int,
        max_control_flow_degree: int,
        max_caller_impact: float,
        caller_impact_scores: dict[str, float],
        max_module_dependency: float,
        max_async_call: float,
        pagerank_scores: dict[str, float],
        max_pagerank: float,
    ) -> dict[str, float]:
        traceback_hit = 1.0 if function_id in test_summary.traceback_function_ids else 0.0
        test_coverage = (
            1.0
            if any(
                function_id in test_summary.coverage.get(test_id, set())
                for test_id in test_summary.failed_tests
            )
            else 0.0
        )
        line_coverage = max(
            (
                test_summary.line_coverage.get(test_id, {}).get(function_id, 0.0)
                for test_id in test_summary.failed_tests
            ),
            default=0.0,
        )
        statement_sbfl = statement_ochiai(function_id, test_summary)
        branch_sbfl = branch_ochiai(function_id, test_summary)
        path_sbfl = path_ochiai(function_id, test_summary)
        proximity = self._proximity_to_failing_tests(
            function_id=function_id,
            program_graph=program_graph,
            failed_tests=test_summary.failed_tests,
        )
        centrality = _safe_div(
            program_graph.degree(function_id, {"calls", "tested_by"}), max_degree
        )
        patch_risk = _safe_div(
            program_graph.in_degree(function_id, {"calls"}), max_in_degree
        )
        data_dependency = _safe_div(
            _data_dependency_degree(program_graph, function_id),
            max_data_dependency_degree,
        )
        control_flow = _safe_div(
            _control_flow_degree(program_graph, function_id),
            max_control_flow_degree,
        )
        caller_impact = _safe_div(
            caller_impact_scores.get(function_id, 0.0),
            max_caller_impact,
        )
        module_dependency = _safe_div(
            _module_dependency_score(program_graph, function_id),
            max_module_dependency,
        )
        async_call = _safe_div(
            _async_call_score(program_graph, function_id),
            max_async_call,
        )
        dynamic_test_evidence = self._dynamic_test_evidence_score(
            function_id=function_id,
            program_graph=program_graph,
            test_summary=test_summary,
        )
        pagerank = _safe_div(pagerank_scores.get(function_id, 0.0), max_pagerank)
        effective_line_coverage = line_coverage if self.config.use_line_coverage else 0.0
        effective_statement_sbfl = (
            statement_sbfl if self.config.use_line_coverage else 0.0
        )
        effective_branch_sbfl = (
            branch_sbfl if self.config.use_branch_coverage else 0.0
        )
        effective_path_sbfl = path_sbfl if self.config.use_path_coverage else 0.0
        effective_data_dependency = (
            data_dependency if self.config.use_data_dependency else 0.0
        )
        effective_control_flow = control_flow if self.config.use_control_flow else 0.0
        effective_pagerank = pagerank if self.config.use_pagerank else 0.0
        effective_caller_impact = (
            caller_impact if self.config.use_caller_impact else 0.0
        )
        effective_module_dependency = (
            module_dependency if self.config.use_module_dependency else 0.0
        )
        effective_async_call = async_call if self.config.use_async_calls else 0.0
        effective_dynamic_test_evidence = (
            dynamic_test_evidence if self.config.use_dynamic_test_evidence else 0.0
        )
        graph = _clamp(
            0.16 * traceback_hit
            + 0.16 * test_coverage
            + 0.08 * effective_line_coverage
            + 0.08 * effective_statement_sbfl
            + 0.06 * effective_branch_sbfl
            + 0.06 * effective_path_sbfl
            + 0.07 * effective_data_dependency
            + 0.07 * effective_control_flow
            + 0.14 * proximity
            + 0.07 * centrality
            + 0.07 * effective_pagerank
            + 0.06 * effective_caller_impact
            + 0.04 * effective_module_dependency
            + 0.04 * effective_async_call
            + 0.08 * effective_dynamic_test_evidence
            + 0.12 * static_score
            - 0.10 * patch_risk
        )
        structural_graph = _clamp(
            0.18 * effective_data_dependency
            + 0.18 * effective_control_flow
            + 0.14 * centrality
            + 0.14 * effective_pagerank
            + 0.16 * effective_caller_impact
            + 0.10 * effective_module_dependency
            + 0.10 * effective_async_call
        )
        return {
            "graph": graph,
            "structural_graph": structural_graph,
            "traceback_hit": traceback_hit,
            "test_coverage": test_coverage,
            "line_coverage": line_coverage,
            "statement_sbfl": statement_sbfl,
            "branch_sbfl": branch_sbfl,
            "path_sbfl": path_sbfl,
            "data_dependency": data_dependency,
            "control_flow": control_flow,
            "pagerank": pagerank,
            "proximity": proximity,
            "caller_impact": caller_impact,
            "module_dependency": module_dependency,
            "async_call": async_call,
            "dynamic_test_evidence": dynamic_test_evidence,
            "centrality": centrality,
            "patch_risk": patch_risk,
        }

    def _dynamic_test_evidence_score(
        self,
        *,
        function_id: str,
        program_graph: ProgramGraph,
        test_summary: TestExecutionSummary,
    ) -> float:
        if not test_summary.dynamic_evidence_test_ids:
            return 0.0
        scores = []
        for test_id in test_summary.dynamic_evidence_test_ids:
            if test_id not in test_summary.failed_tests:
                continue
            if function_id in test_summary.coverage.get(test_id, set()):
                scores.append(1.0)
                continue
            distance = program_graph.shortest_path_distance(
                test_id,
                function_id,
                edge_types={"calls", "tested_by"},
                max_depth=self.config.graph_propagation_max_depth,
            )
            if distance is not None:
                scores.append(self._propagated_score(distance))
        return max(scores, default=0.0)

    def _stacktrace_score(
        self,
        *,
        function_id: str,
        program_graph: ProgramGraph,
        test_summary: TestExecutionSummary,
    ) -> float:
        if not self.config.use_stacktrace_score:
            return 0.0
        traceback_ids = test_summary.dynamic_traceback_function_ids
        if function_id in traceback_ids:
            return 1.0
        scores = []
        for trace_function_id in traceback_ids:
            distances = [
                program_graph.shortest_path_distance(
                    trace_function_id,
                    function_id,
                    edge_types={"calls", "awaits"},
                    max_depth=self.config.graph_propagation_max_depth,
                ),
                program_graph.shortest_path_distance(
                    function_id,
                    trace_function_id,
                    edge_types={"calls", "awaits"},
                    max_depth=self.config.graph_propagation_max_depth,
                ),
            ]
            valid_distances = [distance for distance in distances if distance is not None]
            if valid_distances:
                scores.append(self._propagated_score(min(valid_distances)))
        return max(scores, default=0.0)

    def _propagated_score(self, distance: int) -> float:
        if distance < 0 or distance > self.config.graph_propagation_max_depth:
            return 0.0
        if self.config.fusion_profile == LEGACY_V1_FUSION:
            return 1 / (1 + distance)
        return _clamp(self.config.graph_propagation_decay) ** max(0, distance - 1)

    def _proximity_to_failing_tests(
        self,
        function_id: str,
        program_graph: ProgramGraph,
        failed_tests: set[str],
    ) -> float:
        scores = []
        for test_id in failed_tests:
            distance = program_graph.shortest_path_distance(
                test_id,
                function_id,
                edge_types={"calls", "tested_by"},
                max_depth=(
                    self.config.graph_propagation_max_depth
                    if self.config.fusion_profile == EVIDENCE_V2_FUSION
                    else 8
                ),
            )
            if distance is not None:
                if self.config.fusion_profile == EVIDENCE_V2_FUSION:
                    scores.append(self._propagated_score(distance))
                else:
                    scores.append(1 / (1 + distance))
        return max(scores, default=0.0)


def ochiai(function_id: str, summary: TestExecutionSummary) -> float:
    total_failed = len(summary.failed_tests)
    if total_failed == 0:
        return 0.0
    failed_covered = sum(
        1
        for test_id in summary.failed_tests
        if function_id in summary.coverage.get(test_id, set())
    )
    if failed_covered == 0:
        return 0.0
    passed_covered = sum(
        1
        for test_id in summary.passed_tests
        if function_id in summary.coverage.get(test_id, set())
    )
    denominator = math.sqrt(total_failed * (failed_covered + passed_covered))
    return failed_covered / denominator if denominator else 0.0


def statement_ochiai(function_id: str, summary: TestExecutionSummary) -> float:
    total_failed = len(summary.failed_tests)
    if total_failed == 0:
        return 0.0
    candidate_lines = _covered_statement_lines(function_id, summary)
    if not candidate_lines:
        return 0.0
    return max(
        _statement_line_ochiai(
            function_id=function_id,
            line=line,
            summary=summary,
            total_failed=total_failed,
        )
        for line in candidate_lines
    )


def branch_ochiai(function_id: str, summary: TestExecutionSummary) -> float:
    total_failed = len(summary.failed_tests)
    if total_failed == 0:
        return 0.0
    candidate_outcomes = _covered_branch_outcomes(function_id, summary)
    if not candidate_outcomes:
        return 0.0
    return max(
        _branch_outcome_ochiai(
            function_id=function_id,
            outcome=outcome,
            summary=summary,
            total_failed=total_failed,
        )
        for outcome in candidate_outcomes
    )


def path_ochiai(function_id: str, summary: TestExecutionSummary) -> float:
    total_failed = len(summary.failed_tests)
    if total_failed == 0:
        return 0.0
    candidate_fragments = _covered_path_fragments(function_id, summary)
    if not candidate_fragments:
        return 0.0
    return max(
        _path_fragment_ochiai(
            function_id=function_id,
            fragment=fragment,
            summary=summary,
            total_failed=total_failed,
        )
        for fragment in candidate_fragments
    )


def score_with_weights(signals: dict[str, float], weights: ScoreWeights) -> float:
    return _clamp(sum(score_contributions(signals, weights).values()))


def score_contributions(
    signals: dict[str, float],
    weights: ScoreWeights,
) -> dict[str, float]:
    return {
        "sbfl": weights.sbfl * signals.get("sbfl", 0.0),
        "graph": weights.graph * signals.get("graph", 0.0),
        "static": weights.static * signals.get("static", 0.0),
        "semantic": weights.semantic * signals.get("semantic", 0.0),
        "llm": weights.llm * signals.get("llm", 0.0),
        "test_failure": weights.test_failure
        * signals.get("test_failure", 0.0),
        "traceback": weights.traceback * signals.get("traceback", 0.0),
        "complexity": weights.complexity * signals.get("complexity", 0.0),
        "change_history": weights.change_history
        * signals.get("change_history", 0.0),
        "risk": -weights.risk
        * signals.get("risk", signals.get("patch_risk", 0.0)),
    }


def _combine_confidence(confidences) -> float:
    score = 0.0
    for confidence in confidences:
        score = 1 - (1 - score) * (1 - confidence)
    return score


def _cyclomatic_complexity(source: str) -> int:
    try:
        tree = ast.parse(textwrap.dedent(source))
    except (IndentationError, SyntaxError, ValueError):
        return 1
    complexity = 1
    for node in ast.walk(tree):
        if isinstance(
            node,
            (
                ast.If,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.IfExp,
                ast.comprehension,
            ),
        ):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            complexity += max(0, len(node.values) - 1)
        elif isinstance(node, ast.Try):
            complexity += len(node.handlers) + int(bool(node.orelse))
        elif hasattr(ast, "Match") and isinstance(node, ast.Match):
            complexity += len(node.cases)
    return complexity


def _normalized_complexity(
    raw_complexity: int,
    *,
    max_complexity_excess: int,
) -> float:
    excess = max(0, raw_complexity - 1)
    if excess == 0 or max_complexity_excess <= 0:
        return 0.0
    return _clamp(math.log1p(excess) / math.log1p(max_complexity_excess))


def _covered_statement_lines(
    function_id: str,
    summary: TestExecutionSummary,
) -> set[int]:
    lines: set[int] = set()
    for per_function in summary.covered_lines.values():
        lines.update(per_function.get(function_id, set()))
    return lines


def _covered_branch_outcomes(
    function_id: str,
    summary: TestExecutionSummary,
) -> set[str]:
    outcomes: set[str] = set()
    for per_function in summary.branch_coverage.values():
        outcomes.update(per_function.get(function_id, set()))
    return outcomes


def _covered_path_fragments(
    function_id: str,
    summary: TestExecutionSummary,
) -> set[str]:
    fragments: set[str] = set()
    for per_function in summary.path_coverage.values():
        fragments.update(per_function.get(function_id, set()))
    return fragments


def _path_fragment_ochiai(
    *,
    function_id: str,
    fragment: str,
    summary: TestExecutionSummary,
    total_failed: int,
) -> float:
    failed_covered = sum(
        1
        for test_id in summary.failed_tests
        if fragment in summary.path_coverage.get(test_id, {}).get(function_id, set())
    )
    if failed_covered == 0:
        return 0.0
    passed_covered = sum(
        1
        for test_id in summary.passed_tests
        if fragment in summary.path_coverage.get(test_id, {}).get(function_id, set())
    )
    denominator = math.sqrt(total_failed * (failed_covered + passed_covered))
    return failed_covered / denominator if denominator else 0.0


def _branch_outcome_ochiai(
    *,
    function_id: str,
    outcome: str,
    summary: TestExecutionSummary,
    total_failed: int,
) -> float:
    failed_covered = sum(
        1
        for test_id in summary.failed_tests
        if outcome in summary.branch_coverage.get(test_id, {}).get(function_id, set())
    )
    if failed_covered == 0:
        return 0.0
    passed_covered = sum(
        1
        for test_id in summary.passed_tests
        if outcome in summary.branch_coverage.get(test_id, {}).get(function_id, set())
    )
    denominator = math.sqrt(total_failed * (failed_covered + passed_covered))
    return failed_covered / denominator if denominator else 0.0


def _statement_line_ochiai(
    *,
    function_id: str,
    line: int,
    summary: TestExecutionSummary,
    total_failed: int,
) -> float:
    failed_covered = sum(
        1
        for test_id in summary.failed_tests
        if line in summary.covered_lines.get(test_id, {}).get(function_id, set())
    )
    if failed_covered == 0:
        return 0.0
    passed_covered = sum(
        1
        for test_id in summary.passed_tests
        if line in summary.covered_lines.get(test_id, {}).get(function_id, set())
    )
    denominator = math.sqrt(total_failed * (failed_covered + passed_covered))
    return failed_covered / denominator if denominator else 0.0


def _safe_div(value: int | float, denominator: int | float) -> float:
    return value / denominator if denominator else 0.0


def _data_dependency_degree(program_graph: ProgramGraph, function_id: str) -> int:
    degree = 0
    for edge in program_graph.edges:
        if edge["type"] in {"data_depends_on", "key_flows_to_subscript"} and edge.get(
            "function_id"
        ) == function_id:
            degree += 1
        elif edge["type"] in {"arg_flows_to_param", "return_flows_to_var"} and (
            edge.get("caller_function_id") == function_id
            or edge.get("callee_function_id") == function_id
        ):
            degree += 1
    return degree


def _control_flow_degree(program_graph: ProgramGraph, function_id: str) -> int:
    return sum(
        1
        for edge in program_graph.edges
        if edge["type"] in {
            "controls",
            "cfg_entry",
            "cfg_next",
            "cfg_branch",
            "cfg_loop",
            "cfg_exception",
        }
        and edge.get("function_id") == function_id
    )


def _caller_impact_score(
    program_graph: ProgramGraph,
    function_id: str,
    reverse_edges: dict[str, dict[str, bool]] | None = None,
) -> float:
    target = program_graph.functions.get(function_id)
    if target is None:
        return 0.0
    target_file = Path(target.file_path).resolve()
    score = 0.0
    for caller_id, distance, is_awaited in _transitive_production_callers(
        program_graph,
        function_id,
        reverse_edges or _production_reverse_call_edges(program_graph),
    ):
        caller = program_graph.functions[caller_id]
        decay = 0.5 ** (distance - 1)
        cross_file_bonus = 0.5 if Path(caller.file_path).resolve() != target_file else 0.0
        await_bonus = 0.15 if is_awaited else 0.0
        score += decay * (1.0 + cross_file_bonus + await_bonus)
    return score


def _module_dependency_score(program_graph: ProgramGraph, function_id: str) -> float:
    target = program_graph.functions.get(function_id)
    if target is None:
        return 0.0
    score = 0.0
    for edge in program_graph.edges:
        if edge["type"] != "module_depends_on":
            continue
        source = program_graph.functions.get(edge["source"])
        target_function = program_graph.functions.get(edge["target"])
        if source is None or target_function is None:
            continue
        if _is_test_function(source) or _is_test_function(target_function):
            continue
        distance_bonus = min(float(edge.get("package_distance", 0)) * 0.10, 0.40)
        relative_bonus = 0.15 if edge.get("is_relative_import") else 0.0
        if edge["target"] == function_id:
            score += 1.5 + distance_bonus + relative_bonus
        elif edge["source"] == function_id:
            score += 0.5
    return score


def _async_call_score(program_graph: ProgramGraph, function_id: str) -> float:
    target = program_graph.functions.get(function_id)
    if target is None:
        return 0.0
    target_file = Path(target.file_path).resolve()
    score = 0.0
    for edge in program_graph.edges:
        if edge["type"] != "awaits":
            continue
        source = program_graph.functions.get(edge["source"])
        callee = program_graph.functions.get(edge["target"])
        if source is None or callee is None:
            continue
        if _is_test_function(source) or _is_test_function(callee):
            continue
        cross_file_bonus = 0.5 if Path(source.file_path).resolve() != target_file else 0.0
        if edge["target"] == function_id:
            score += 1.0 + cross_file_bonus
        elif edge["source"] == function_id:
            score += 0.25
    return score


def _transitive_production_callers(
    program_graph: ProgramGraph,
    function_id: str,
    reverse_edges: dict[str, dict[str, bool]],
    max_depth: int = 3,
) -> list[tuple[str, int, bool]]:
    callers: list[tuple[str, int, bool]] = []
    queue = [(function_id, 0, False)]
    seen = {function_id}
    while queue:
        current_id, distance, path_awaited = queue.pop(0)
        if distance >= max_depth:
            continue
        for caller_id, is_awaited in reverse_edges.get(current_id, {}).items():
            if caller_id in seen:
                continue
            caller_distance = distance + 1
            caller_path_awaited = path_awaited or is_awaited
            seen.add(caller_id)
            callers.append((caller_id, caller_distance, caller_path_awaited))
            queue.append((caller_id, caller_distance, caller_path_awaited))
    return callers


def _production_reverse_call_edges(
    program_graph: ProgramGraph,
) -> dict[str, dict[str, bool]]:
    reverse_edges: dict[str, dict[str, bool]] = defaultdict(dict)
    for edge in program_graph.edges:
        if edge["type"] not in {"calls", "awaits"}:
            continue
        caller = program_graph.functions.get(edge["source"])
        callee = program_graph.functions.get(edge["target"])
        if caller is None or callee is None:
            continue
        if _is_test_function(caller) or _is_test_function(callee):
            continue
        is_awaited = edge["type"] == "awaits" or bool(edge.get("is_awaited", False))
        reverse_edges[edge["target"]][edge["source"]] = (
            reverse_edges[edge["target"]].get(edge["source"], False) or is_awaited
        )
    return reverse_edges


def _is_test_function(function) -> bool:
    return bool(function.metadata.get("is_test") or function.metadata.get("is_test_file"))


def _pagerank_scores(
    program_graph: ProgramGraph,
    edge_types: set[str] | None = None,
    damping: float = 0.85,
    iterations: int = 30,
) -> dict[str, float]:
    edge_types = edge_types or {"calls", "tested_by"}
    node_ids = {
        node_id
        for node_id, node in program_graph.nodes.items()
        if node.get("type") == "function"
    }
    if not node_ids:
        return {}

    outgoing: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for edge in program_graph.edges:
        if edge["type"] not in edge_types:
            continue
        source = edge["source"]
        target = edge["target"]
        if source in node_ids and target in node_ids:
            outgoing[source].add(target)

    count = len(node_ids)
    scores = {node_id: 1.0 / count for node_id in node_ids}
    base = (1.0 - damping) / count
    for _ in range(iterations):
        next_scores = {node_id: base for node_id in node_ids}
        sink_score = sum(
            scores[node_id] for node_id, targets in outgoing.items() if not targets
        )
        sink_share = damping * sink_score / count
        for node_id in node_ids:
            next_scores[node_id] += sink_share
        for source, targets in outgoing.items():
            if not targets:
                continue
            share = damping * scores[source] / len(targets)
            for target in targets:
                next_scores[target] += share
        scores = next_scores
    return scores


def _semantic_query_tokens(
    summary: TestExecutionSummary,
    program_graph: ProgramGraph,
) -> set[str]:
    tokens: set[str] = set()
    for test_id in summary.failed_tests:
        test_name = summary.test_names.get(test_id)
        test_function = program_graph.functions.get(test_id)
        if test_name:
            tokens.update(_tokenize(test_name))
        if test_function is not None:
            tokens.update(
                _tokenize(
                    " ".join(
                        [
                            test_function.name,
                            str(test_function.metadata.get("qualified_name", "")),
                            Path(test_function.file_path).stem,
                        ]
                    )
                )
            )
        else:
            tokens.update(_tokenize(test_id))
        tokens.update(_tokenize(summary.failure_messages.get(test_id, "")))
    return tokens


def _semantic_similarity(
    function,
    findings: list[BugFinding],
    query_tokens: set[str],
) -> float:
    if not query_tokens:
        return 0.0
    document_tokens = _function_semantic_tokens(function, findings)
    if not document_tokens:
        return 0.0
    overlap = query_tokens.intersection(document_tokens)
    if not overlap:
        return 0.0
    return _clamp(len(overlap) / math.sqrt(len(query_tokens) * len(document_tokens)))


def _function_semantic_tokens(function, findings: list[BugFinding]) -> set[str]:
    parts = [
        function.name,
        str(function.metadata.get("qualified_name", "")),
        Path(function.file_path).stem,
        function.source,
    ]
    for finding in findings:
        parts.extend(
            [
                finding.rule_id,
                finding.bug_type,
                finding.message,
            ]
        )
    return _tokenize(" ".join(parts))


def _tokenize(text: str) -> set[str]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", expanded)
    }
    split_tokens: set[str] = set()
    for token in tokens:
        split_tokens.add(token)
        split_tokens.update(part for part in token.split("_") if part)
    return {
        token
        for token in split_tokens
        if len(token) > 1 and token not in _SEMANTIC_STOP_WORDS
    }


_SEMANTIC_STOP_WORDS = {
    "and",
    "any",
    "are",
    "arg",
    "args",
    "assert",
    "bool",
    "call",
    "case",
    "class",
    "code",
    "def",
    "else",
    "error",
    "except",
    "false",
    "for",
    "from",
    "has",
    "if",
    "import",
    "in",
    "is",
    "len",
    "line",
    "list",
    "none",
    "not",
    "or",
    "pass",
    "possible",
    "range",
    "return",
    "self",
    "test",
    "the",
    "to",
    "true",
    "try",
    "value",
    "values",
    "with",
}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _reason(signals: dict[str, float], findings: list[BugFinding]) -> str:
    if findings:
        rule_ids = ", ".join(finding.rule_id for finding in findings)
        return f"Static rules matched: {rule_ids}."
    if signals["sbfl"] > 0:
        return "Covered by failing tests according to SBFL."
    if signals.get("traceback", 0.0) > 0:
        return "Matched or is near a real stack-trace frame."
    if signals.get("test_failure", 0.0) > 0:
        return "Linked to real failing-test evidence with bounded graph propagation."
    if signals["llm"] > 0:
        return "Suspicious according to LLM fault-localization scoring."
    if signals.get("dynamic_test_evidence", 0.0) > 0:
        return "Linked to failing repository test dynamic evidence."
    if signals["graph"] > 0:
        return "Suspicious due to graph proximity or centrality."
    if signals["semantic"] > 0:
        return "Semantically similar to failing test or error context."
    return "No strong suspicious signal."
