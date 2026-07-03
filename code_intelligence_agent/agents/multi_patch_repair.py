from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations, product
from pathlib import Path
from typing import Any

from code_intelligence_agent.core.models import ExecutionResult, PatchCandidate
from code_intelligence_agent.core.program_graph import ProgramGraph
from code_intelligence_agent.search.scoring import diff_size
from code_intelligence_agent.tools.sandbox import Sandbox


@dataclass(frozen=True)
class MultiPatchAttempt:
    candidates: tuple[PatchCandidate, ...]
    execution_result: ExecutionResult
    score: float
    graph_evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.execution_result.success

    @property
    def rule_ids(self) -> list[str]:
        return sorted({candidate.rule_id for candidate in self.candidates})

    @property
    def target_function_names(self) -> list[str]:
        return [candidate.target_function_name for candidate in self.candidates]


@dataclass(frozen=True)
class MultiPatchResult:
    success: bool
    best_candidates: tuple[PatchCandidate, ...] = ()
    best_result: ExecutionResult | None = None
    attempts: list[MultiPatchAttempt] = field(default_factory=list)

    @property
    def rounds(self) -> int:
        return len(self.attempts)

    @property
    def bundle_size(self) -> int:
        return len(self.best_candidates)


class MultiPatchRepair:
    def __init__(
        self,
        sandbox: Sandbox | None = None,
        max_bundle_size: int = 3,
        variants_per_function: int = 2,
        max_attempts: int = 8,
        use_graph_bundle_ranking: bool = True,
    ) -> None:
        self.sandbox = sandbox or Sandbox()
        self.max_bundle_size = max_bundle_size
        self.variants_per_function = variants_per_function
        self.max_attempts = max_attempts
        self.use_graph_bundle_ranking = use_graph_bundle_ranking

    def run(
        self,
        repo_path: str | Path,
        candidates: list[PatchCandidate],
        localization_scores: dict[str, float] | None = None,
        program_graph: ProgramGraph | None = None,
        test_args: list[str] | None = None,
    ) -> MultiPatchResult:
        localization_scores = localization_scores or {}
        graph_for_ranking = program_graph if self.use_graph_bundle_ranking else None
        bundles = _candidate_bundles(
            candidates=candidates,
            localization_scores=localization_scores,
            program_graph=graph_for_ranking,
            max_bundle_size=self.max_bundle_size,
            variants_per_function=self.variants_per_function,
        )[: self.max_attempts]
        attempts: list[MultiPatchAttempt] = []
        best_attempt: MultiPatchAttempt | None = None

        for bundle in bundles:
            try:
                execution_result = self.sandbox.apply_patches_and_test(
                    repo_path,
                    list(bundle),
                    test_args=test_args,
                )
            except (FileNotFoundError, ValueError) as exc:
                execution_result = ExecutionResult(
                    success=False,
                    returncode=-1,
                    stdout="",
                    stderr=str(exc),
                    traceback="",
                    passed=0,
                    failed=0,
                    timeout=False,
                    command=[],
                )
            graph_evidence = _bundle_graph_evidence(bundle, graph_for_ranking)
            score = _score_bundle(
                bundle,
                execution_result,
                localization_scores,
                graph_evidence,
            )
            attempt = MultiPatchAttempt(
                candidates=bundle,
                execution_result=execution_result,
                score=score,
                graph_evidence=graph_evidence,
            )
            attempts.append(attempt)
            if best_attempt is None or attempt.score > best_attempt.score:
                best_attempt = attempt
            if attempt.success:
                return MultiPatchResult(
                    success=True,
                    best_candidates=bundle,
                    best_result=execution_result,
                    attempts=attempts,
                )

        return MultiPatchResult(
            success=False,
            best_candidates=best_attempt.candidates if best_attempt else (),
            best_result=best_attempt.execution_result if best_attempt else None,
            attempts=attempts,
        )


def _candidate_bundles(
    *,
    candidates: list[PatchCandidate],
    localization_scores: dict[str, float],
    program_graph: ProgramGraph | None = None,
    max_bundle_size: int,
    variants_per_function: int,
) -> list[tuple[PatchCandidate, ...]]:
    grouped: dict[str, list[PatchCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.target_function_id, []).append(candidate)
    if len(grouped) < 2:
        return []

    candidate_groups = [
        sorted(
            items,
            key=lambda candidate: _candidate_priority(candidate, localization_scores),
            reverse=True,
        )[:variants_per_function]
        for _, items in sorted(grouped.items())
    ]
    bundles: list[tuple[PatchCandidate, ...]] = []
    max_size = min(max_bundle_size, len(candidate_groups))
    for size in range(2, max_size + 1):
        for group_combo in combinations(candidate_groups, size):
            for bundle in product(*group_combo):
                bundles.append(tuple(bundle))
    return sorted(
        bundles,
        key=lambda bundle: _bundle_priority(
            bundle,
            localization_scores,
            _bundle_graph_evidence(bundle, program_graph),
        ),
        reverse=True,
    )


def _candidate_priority(
    candidate: PatchCandidate,
    localization_scores: dict[str, float],
) -> float:
    variant_rank = float(candidate.metadata.get("variant_rank", 0))
    return (
        localization_scores.get(candidate.target_function_id, 0.0)
        + 0.10 * (1.0 - min(1.0, _patch_risk(candidate)))
        - 0.03 * variant_rank
        - 0.02 * min(1.0, diff_size(candidate.diff) / 20)
    )


def _bundle_priority(
    bundle: tuple[PatchCandidate, ...],
    localization_scores: dict[str, float],
    graph_evidence: dict[str, Any] | None = None,
) -> float:
    if not bundle:
        return 0.0
    graph_evidence = graph_evidence or {}
    avg_localization = sum(
        localization_scores.get(candidate.target_function_id, 0.0)
        for candidate in bundle
    ) / len(bundle)
    avg_risk = sum(_patch_risk(candidate) for candidate in bundle) / len(bundle)
    total_diff = sum(diff_size(candidate.diff) for candidate in bundle)
    graph_bonus = float(graph_evidence.get("graph_bonus", 0.0))
    return (
        avg_localization
        + 0.08 * len({candidate.rule_id for candidate in bundle})
        + graph_bonus
        - 0.08 * avg_risk
        - 0.02 * min(1.0, total_diff / 40)
    )


def _score_bundle(
    bundle: tuple[PatchCandidate, ...],
    result: ExecutionResult,
    localization_scores: dict[str, float],
    graph_evidence: dict[str, Any] | None = None,
) -> float:
    graph_evidence = graph_evidence or {}
    total_tests = result.passed + result.failed
    if total_tests:
        tests_passed_ratio = result.passed / total_tests
    else:
        tests_passed_ratio = 1.0 if result.success else 0.0
    static_check_pass = 1.0 if not result.timeout and result.returncode != -1 else 0.0
    avg_localization = (
        sum(
            localization_scores.get(candidate.target_function_id, 0.0)
            for candidate in bundle
        )
        / len(bundle)
        if bundle
        else 0.0
    )
    avg_risk = sum(_patch_risk(candidate) for candidate in bundle) / len(bundle)
    diff_penalty = min(1.0, sum(diff_size(candidate.diff) for candidate in bundle) / 60)
    graph_bonus = float(graph_evidence.get("graph_bonus", 0.0))
    score = (
        0.62 * tests_passed_ratio
        + 0.22 * avg_localization
        + 0.10 * static_check_pass
        + 0.10 * min(1.0, graph_bonus)
        - 0.04 * avg_risk
        - 0.02 * diff_penalty
    )
    if result.success:
        score += 0.14
    return round(max(0.0, min(1.0, score)), 4)


def _patch_risk(candidate: PatchCandidate) -> float:
    risk = candidate.metadata.get("risk", {})
    if isinstance(risk, dict):
        return float(risk.get("score", 0.0))
    return 0.0


def _bundle_graph_evidence(
    bundle: tuple[PatchCandidate, ...],
    program_graph: ProgramGraph | None,
) -> dict[str, Any]:
    function_ids = [candidate.target_function_id for candidate in bundle]
    unique_ids = set(function_ids)
    files = {Path(candidate.target_file).resolve().as_posix() for candidate in bundle}
    evidence: dict[str, Any] = {
        "cross_function": len(unique_ids) > 1,
        "cross_file": len(files) > 1,
        "direct_call_edges": 0,
        "module_dependency_edges": 0,
        "relative_import_edges": 0,
        "data_flow_edges": 0,
        "key_flow_edges": 0,
        "package_distance_sum": 0,
        "max_package_distance": 0,
        "average_package_distance": 0.0,
        "package_distance_bonus": 0.0,
        "shortest_call_distance": None,
        "connected_pairs": 0,
        "pair_count": _pair_count(len(unique_ids)),
        "graph_bonus": 0.0,
    }
    if program_graph is None or len(unique_ids) < 2:
        return evidence

    for edge in program_graph.edges:
        edge_type = edge.get("type")
        if edge_type in {"calls", "module_depends_on"}:
            source = edge.get("source")
            target = edge.get("target")
            if source == target:
                continue
            if source in unique_ids and target in unique_ids:
                if edge_type == "calls":
                    evidence["direct_call_edges"] += 1
                else:
                    evidence["module_dependency_edges"] += 1
                    package_distance = int(edge.get("package_distance", 0) or 0)
                    evidence["package_distance_sum"] += package_distance
                    evidence["max_package_distance"] = max(
                        evidence["max_package_distance"],
                        package_distance,
                    )
                    if edge.get("is_relative_import"):
                        evidence["relative_import_edges"] += 1
        elif edge_type in {"arg_flows_to_param", "return_flows_to_var"}:
            caller_id = edge.get("caller_function_id")
            callee_id = edge.get("callee_function_id")
            if caller_id == callee_id:
                continue
            if caller_id in unique_ids and callee_id in unique_ids:
                evidence["data_flow_edges"] += 1
        elif edge_type == "key_flows_to_subscript":
            if edge.get("function_id") in unique_ids:
                evidence["key_flow_edges"] += 1

    distances: list[int] = []
    ids = sorted(unique_ids)
    for left, right in combinations(ids, 2):
        distance = _shortest_bidirectional_call_distance(
            program_graph,
            left,
            right,
        )
        if distance is not None:
            distances.append(distance)
    if distances:
        evidence["shortest_call_distance"] = min(distances)
        evidence["connected_pairs"] = len(distances)

    graph_bonus = 0.0
    if evidence["direct_call_edges"]:
        graph_bonus += 0.08
    if evidence["module_dependency_edges"]:
        graph_bonus += 0.07
        evidence["average_package_distance"] = round(
            evidence["package_distance_sum"] / evidence["module_dependency_edges"],
            4,
        )
        evidence["package_distance_bonus"] = round(
            min(0.04, 0.01 * evidence["package_distance_sum"]),
            4,
        )
        graph_bonus += evidence["package_distance_bonus"]
    if evidence["relative_import_edges"]:
        graph_bonus += 0.02
    if evidence["data_flow_edges"]:
        graph_bonus += 0.06
    if evidence["connected_pairs"]:
        graph_bonus += 0.04 * (
            evidence["connected_pairs"] / max(1, evidence["pair_count"])
        )
    if evidence["cross_file"] and evidence["module_dependency_edges"]:
        graph_bonus += 0.04
    evidence["graph_bonus"] = round(min(0.20, graph_bonus), 4)
    return evidence


def _shortest_bidirectional_call_distance(
    program_graph: ProgramGraph,
    left: str,
    right: str,
) -> int | None:
    edge_types = {"calls", "module_depends_on"}
    forward = program_graph.shortest_path_distance(
        left,
        right,
        edge_types=edge_types,
        max_depth=4,
    )
    backward = program_graph.shortest_path_distance(
        right,
        left,
        edge_types=edge_types,
        max_depth=4,
    )
    distances = [distance for distance in [forward, backward] if distance is not None]
    return min(distances) if distances else None


def _pair_count(size: int) -> int:
    return max(0, size * (size - 1) // 2)
