from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from code_intelligence_agent.core.models import (
    BugFinding,
    FaultLocalizationResult,
    PatchCandidate,
    TestExecutionSummary,
)
from code_intelligence_agent.core.program_graph import ProgramGraph


@dataclass(frozen=True)
class BugHypothesis:
    id: str
    function_id: str
    function_name: str
    file_path: str
    bug_type: str
    score: float
    depth: int
    parent_id: str | None
    rule_ids: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    reasoning_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BugHypothesisSearch:
    """Beam-style tree-of-thought search over bug explanations."""

    def __init__(
        self,
        beam_width: int = 4,
        max_depth: int = 2,
        top_k_functions: int = 5,
    ) -> None:
        self.beam_width = beam_width
        self.max_depth = max_depth
        self.top_k_functions = top_k_functions

    def search(
        self,
        *,
        ranked_functions: list[FaultLocalizationResult],
        findings: list[BugFinding],
        test_summary: TestExecutionSummary,
        program_graph: ProgramGraph,
        patch_candidates: list[PatchCandidate] | None = None,
    ) -> list[BugHypothesis]:
        findings_by_function = _findings_by_function(findings)
        candidates_by_function = _candidates_by_function(patch_candidates or [])
        initial = self._initial_hypotheses(
            ranked_functions=ranked_functions[: self.top_k_functions],
            findings_by_function=findings_by_function,
        )
        beam = _topk(initial, self.beam_width)
        visited = list(beam)

        for depth in range(1, self.max_depth + 1):
            expanded: list[BugHypothesis] = []
            for hypothesis in beam:
                if depth == 1:
                    expanded.extend(
                        self._expand_with_test_and_graph_evidence(
                            hypothesis=hypothesis,
                            ranked_functions=ranked_functions,
                            test_summary=test_summary,
                            program_graph=program_graph,
                        )
                    )
                elif depth == 2:
                    expanded.extend(
                        self._expand_with_patch_evidence(
                            hypothesis=hypothesis,
                            candidates_by_function=candidates_by_function,
                        )
                    )
            if not expanded:
                break
            beam = _topk(expanded, self.beam_width)
            visited.extend(beam)

        return _topk(_dedupe_hypotheses(visited), len(visited))

    def _initial_hypotheses(
        self,
        *,
        ranked_functions: list[FaultLocalizationResult],
        findings_by_function: dict[str, list[BugFinding]],
    ) -> list[BugHypothesis]:
        hypotheses: list[BugHypothesis] = []
        for ranked in ranked_functions:
            function_findings = findings_by_function.get(ranked.function_id, [])
            if function_findings:
                for finding in function_findings:
                    score = _clamp(
                        0.55 * ranked.score
                        + 0.30 * finding.confidence
                        + 0.10 * ranked.signals.get("static", 0.0)
                        + 0.05 * ranked.signals.get("semantic", 0.0)
                    )
                    hypotheses.append(
                        BugHypothesis(
                            id=_hypothesis_id(ranked.function_id, finding.rule_id, 0),
                            function_id=ranked.function_id,
                            function_name=ranked.function_name,
                            file_path=ranked.file_path,
                            bug_type=finding.bug_type,
                            score=round(score, 4),
                            depth=0,
                            parent_id=None,
                            rule_ids=[finding.rule_id],
                            evidence={
                                "lens": "static_rule",
                                "localization_score": ranked.score,
                                "rule_confidence": finding.confidence,
                                "static": ranked.signals.get("static", 0.0),
                                "semantic": ranked.signals.get("semantic", 0.0),
                                "line": finding.line,
                            },
                            reasoning_steps=[
                                f"Static rule {finding.rule_id} matched {ranked.function_name}.",
                                f"Localization score is {ranked.score:.4f}.",
                            ],
                        )
                    )
            else:
                score = _clamp(
                    0.70 * ranked.score
                    + 0.15 * ranked.signals.get("graph", 0.0)
                    + 0.15 * ranked.signals.get("semantic", 0.0)
                )
                hypotheses.append(
                    BugHypothesis(
                        id=_hypothesis_id(ranked.function_id, "localization", 0),
                        function_id=ranked.function_id,
                        function_name=ranked.function_name,
                        file_path=ranked.file_path,
                        bug_type="unknown",
                        score=round(score, 4),
                        depth=0,
                        parent_id=None,
                        evidence={
                            "lens": "localization",
                            "localization_score": ranked.score,
                            "graph": ranked.signals.get("graph", 0.0),
                            "semantic": ranked.signals.get("semantic", 0.0),
                        },
                        reasoning_steps=[
                            f"{ranked.function_name} is highly ranked by localization signals."
                        ],
                    )
                )
        return hypotheses

    def _expand_with_test_and_graph_evidence(
        self,
        *,
        hypothesis: BugHypothesis,
        ranked_functions: list[FaultLocalizationResult],
        test_summary: TestExecutionSummary,
        program_graph: ProgramGraph,
    ) -> list[BugHypothesis]:
        ranked = _ranked_by_id(ranked_functions).get(hypothesis.function_id)
        if ranked is None:
            return []
        failed_covered = sum(
            1
            for test_id in test_summary.failed_tests
            if hypothesis.function_id in test_summary.coverage.get(test_id, set())
        )
        passed_covered = sum(
            1
            for test_id in test_summary.passed_tests
            if hypothesis.function_id in test_summary.coverage.get(test_id, set())
        )
        call_chain = _shortest_failing_call_chain(
            function_id=hypothesis.function_id,
            test_summary=test_summary,
            program_graph=program_graph,
        )
        graph_evidence = {
            "lens": "test_graph",
            "failed_covered": failed_covered,
            "passed_covered": passed_covered,
            "traceback_hit": ranked.signals.get("traceback_hit", 0.0),
            "test_coverage": ranked.signals.get("test_coverage", 0.0),
            "line_coverage": ranked.signals.get("line_coverage", 0.0),
            "proximity": ranked.signals.get("proximity", 0.0),
            "caller_impact": ranked.signals.get("caller_impact", 0.0),
            "data_dependency": ranked.signals.get("data_dependency", 0.0),
            "control_flow": ranked.signals.get("control_flow", 0.0),
            "call_chain": call_chain,
        }
        boost = (
            0.20 * ranked.signals.get("sbfl", 0.0)
            + 0.16 * ranked.signals.get("traceback_hit", 0.0)
            + 0.14 * ranked.signals.get("test_coverage", 0.0)
            + 0.10 * ranked.signals.get("line_coverage", 0.0)
            + 0.08 * ranked.signals.get("proximity", 0.0)
            + 0.06 * ranked.signals.get("caller_impact", 0.0)
            + 0.04 * ranked.signals.get("data_dependency", 0.0)
            + 0.04 * ranked.signals.get("control_flow", 0.0)
        )
        if not call_chain:
            boost *= 0.75
        return [
            _expanded(
                hypothesis,
                suffix="test_graph",
                depth=1,
                score=_combine_score(hypothesis.score, boost),
                evidence=graph_evidence,
                step=(
                    "Added failing-test coverage, graph proximity, and shortest "
                    "call-chain evidence."
                ),
            )
        ]

    def _expand_with_patch_evidence(
        self,
        *,
        hypothesis: BugHypothesis,
        candidates_by_function: dict[str, list[PatchCandidate]],
    ) -> list[BugHypothesis]:
        candidates = candidates_by_function.get(hypothesis.function_id, [])
        if not candidates:
            return []
        matching = [
            candidate
            for candidate in candidates
            if not hypothesis.rule_ids or candidate.rule_id in set(hypothesis.rule_ids)
        ]
        usable = matching or candidates
        risks = [_patch_risk(candidate) for candidate in usable]
        min_risk = min(risks, default=0.0)
        candidate_rules = sorted({candidate.rule_id for candidate in usable})
        boost = _clamp(0.18 + 0.04 * min(len(usable), 3) + 0.08 * (1.0 - min_risk))
        return [
            _expanded(
                hypothesis,
                suffix="patch",
                depth=2,
                score=_combine_score(hypothesis.score, boost),
                evidence={
                    "lens": "patch_search",
                    "candidate_count": len(usable),
                    "candidate_rules": candidate_rules,
                    "variants": [
                        candidate.metadata.get("variant", "") for candidate in usable[:3]
                    ],
                    "min_patch_risk": round(min_risk, 4),
                },
                step="Added patch-candidate availability and patch-risk evidence.",
            )
        ]


def _expanded(
    hypothesis: BugHypothesis,
    *,
    suffix: str,
    depth: int,
    score: float,
    evidence: dict[str, Any],
    step: str,
) -> BugHypothesis:
    merged_evidence = {**hypothesis.evidence, **evidence}
    return BugHypothesis(
        id=_hypothesis_id(hypothesis.function_id, suffix, depth, parent=hypothesis.id),
        function_id=hypothesis.function_id,
        function_name=hypothesis.function_name,
        file_path=hypothesis.file_path,
        bug_type=hypothesis.bug_type,
        score=round(score, 4),
        depth=depth,
        parent_id=hypothesis.id,
        rule_ids=hypothesis.rule_ids,
        evidence=merged_evidence,
        reasoning_steps=[*hypothesis.reasoning_steps, step],
    )


def _findings_by_function(findings: list[BugFinding]) -> dict[str, list[BugFinding]]:
    grouped: dict[str, list[BugFinding]] = defaultdict(list)
    for finding in findings:
        grouped[finding.function_id].append(finding)
    return grouped


def _candidates_by_function(
    candidates: list[PatchCandidate],
) -> dict[str, list[PatchCandidate]]:
    grouped: dict[str, list[PatchCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.target_function_id].append(candidate)
    return grouped


def _ranked_by_id(
    ranked_functions: list[FaultLocalizationResult],
) -> dict[str, FaultLocalizationResult]:
    return {item.function_id: item for item in ranked_functions}


def _shortest_failing_call_chain(
    *,
    function_id: str,
    test_summary: TestExecutionSummary,
    program_graph: ProgramGraph,
) -> list[str]:
    best_path: list[str] | None = None
    for test_id in test_summary.failed_tests:
        path = program_graph.shortest_path(
            source=test_id,
            target=function_id,
            edge_types={"calls", "tested_by"},
        )
        if path is None:
            continue
        if best_path is None or len(path) < len(best_path):
            best_path = path
    if best_path is None:
        return []
    return [_node_display_name(program_graph, node_id) for node_id in best_path]


def _node_display_name(program_graph: ProgramGraph, node_id: str) -> str:
    function = program_graph.functions.get(node_id)
    if function is not None:
        return str(function.metadata.get("qualified_name", function.name))
    node = program_graph.nodes.get(node_id, {})
    return str(node.get("qualified_name") or node.get("name") or node_id)


def _patch_risk(candidate: PatchCandidate) -> float:
    risk = candidate.metadata.get("risk", {})
    if isinstance(risk, dict):
        return float(risk.get("score", 0.0))
    return 0.0


def _topk(hypotheses: list[BugHypothesis], k: int) -> list[BugHypothesis]:
    return sorted(
        hypotheses,
        key=lambda item: (item.score, item.depth, -len(item.reasoning_steps)),
        reverse=True,
    )[:k]


def _dedupe_hypotheses(hypotheses: list[BugHypothesis]) -> list[BugHypothesis]:
    best_by_id: dict[str, BugHypothesis] = {}
    for hypothesis in hypotheses:
        current = best_by_id.get(hypothesis.id)
        if current is None or hypothesis.score > current.score:
            best_by_id[hypothesis.id] = hypothesis
    return list(best_by_id.values())


def _combine_score(base: float, boost: float) -> float:
    return round(_clamp(1 - (1 - base) * (1 - boost)), 4)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _hypothesis_id(
    function_id: str,
    suffix: str,
    depth: int,
    parent: str | None = None,
) -> str:
    path = Path(function_id).as_posix().replace("/", "_").replace(":", "_")
    parent_part = ""
    if parent is not None:
        parent_part = f"::{hashlib.sha1(parent.encode('utf-8')).hexdigest()[:8]}"
    return f"hyp::{path}::{suffix}::{depth}{parent_part}"
