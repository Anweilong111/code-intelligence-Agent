from __future__ import annotations

import math
import re
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

    def to_dict(self) -> dict[str, float]:
        return {
            "sbfl": self.sbfl,
            "graph": self.graph,
            "static": self.static,
            "semantic": self.semantic,
            "llm": self.llm,
            "risk": self.risk,
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


@dataclass(frozen=True)
class FaultLocalizationConfig:
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
    use_semantic_similarity: bool = True
    use_llm_score: bool = True
    coverage_weights: ScoreWeights = field(default_factory=ScoreWeights)
    static_only_weights: ScoreWeights = field(
        default_factory=lambda: DEFAULT_STATIC_ONLY_WEIGHTS
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
            semantic_score = (
                _semantic_similarity(
                    function=function,
                    findings=function_findings,
                    query_tokens=semantic_query_tokens,
                )
                if self.config.use_semantic_similarity
                else 0.0
            )
            llm_score = llm_scores.get(function_id, 0.0)
            weights = (
                self.config.coverage_weights
                if test_summary.has_coverage()
                else self.config.static_only_weights
            )
            final_score = score_with_weights(
                {
                    "sbfl": sbfl_score,
                    "graph": graph_score,
                    "static": static_score,
                    "semantic": semantic_score,
                    "llm": llm_score,
                    "risk": patch_risk,
                },
                weights,
            )
            signals = {
                "sbfl": round(sbfl_score, 4),
                "graph": round(graph_score, 4),
                "static": round(static_score, 4),
                "semantic": round(semantic_score, 4),
                "llm": round(llm_score, 4),
                "risk": round(patch_risk, 4),
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
            }
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
        return {
            "graph": graph,
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
            )
            if distance is not None:
                scores.append(1 / (1 + distance))
        return max(scores, default=0.0)

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
            )
            if distance is not None:
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
    return _clamp(
        weights.sbfl * signals.get("sbfl", 0.0)
        + weights.graph * signals.get("graph", 0.0)
        + weights.static * signals.get("static", 0.0)
        + weights.semantic * signals.get("semantic", 0.0)
        + weights.llm * signals.get("llm", 0.0)
        - weights.risk * signals.get(
            "risk",
            signals.get("patch_risk", 0.0),
        )
    )


def _combine_confidence(confidences) -> float:
    score = 0.0
    for confidence in confidences:
        score = 1 - (1 - score) * (1 - confidence)
    return score


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
    if signals["llm"] > 0:
        return "Suspicious according to LLM fault-localization scoring."
    if signals.get("dynamic_test_evidence", 0.0) > 0:
        return "Linked to failing repository test dynamic evidence."
    if signals["graph"] > 0:
        return "Suspicious due to graph proximity or centrality."
    if signals["semantic"] > 0:
        return "Semantically similar to failing test or error context."
    return "No strong suspicious signal."
