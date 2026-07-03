from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from code_intelligence_agent.agents.repair_loop import PatchRefiner
from code_intelligence_agent.core.models import ExecutionResult, PatchCandidate
from code_intelligence_agent.core.program_graph import ProgramGraph
from code_intelligence_agent.search.candidate_ranking import (
    dedupe_patch_candidates,
    ensure_patch_risk,
    patch_risk_score,
    rank_patch_candidates,
)
from code_intelligence_agent.search.execution_feedback import (
    annotate_execution_feedback,
    execution_feedback_score,
)
from code_intelligence_agent.search.patch_judge import (
    PatchJudge,
    apply_patch_judgment_score,
    calibrate_patch_judgment,
)
from code_intelligence_agent.search.refinement_context import annotate_refinement_context
from code_intelligence_agent.search.scoring import PatchScoreWeights, score_patch
from code_intelligence_agent.tools.sandbox import Sandbox
from code_intelligence_agent.tools.patch_validation import (
    allow_signature_change_for_rules,
    validate_function_patch,
)


@dataclass(frozen=True)
class BeamPatchNode:
    candidate: PatchCandidate
    execution_result: ExecutionResult
    score: float
    depth: int
    feedback_score: float = 0.0
    retained: bool = True
    retention_bucket: str = ""
    retention_reason: str = ""
    parent_id: str | None = None
    trace: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.execution_result.success


class BeamPatchSearch:
    def __init__(
        self,
        sandbox: Sandbox | None = None,
        refiner: PatchRefiner | None = None,
        beam_width: int = 3,
        max_depth: int = 2,
        use_prior_ranking: bool = True,
        use_diversity_reranking: bool = True,
        diversity_weight: float = 0.06,
        patch_score_weights: PatchScoreWeights | None = None,
        candidate_pool_size: int | None = None,
        use_feedback_retention: bool = True,
        refinement_width: int = 1,
        patch_judge: PatchJudge | None = None,
        patch_judge_weight: float = 0.08,
        use_candidate_deduplication: bool = True,
    ) -> None:
        self.sandbox = sandbox or Sandbox()
        self.refiner = refiner
        self.beam_width = beam_width
        self.max_depth = max_depth
        self.use_prior_ranking = use_prior_ranking
        self.use_diversity_reranking = use_diversity_reranking
        self.diversity_weight = diversity_weight
        self.patch_score_weights = patch_score_weights
        self.candidate_pool_size = candidate_pool_size
        self.use_feedback_retention = use_feedback_retention
        self.refinement_width = max(1, refinement_width)
        self.patch_judge = patch_judge
        self.patch_judge_weight = patch_judge_weight
        self.use_candidate_deduplication = use_candidate_deduplication

    def search(
        self,
        repo_path: str | Path,
        candidates: list[PatchCandidate],
        localization_scores: dict[str, float] | None = None,
        program_graph: ProgramGraph | None = None,
        test_args: list[str] | None = None,
    ) -> list[BeamPatchNode]:
        localization_scores = localization_scores or {}
        ranked_candidates = rank_patch_candidates(
            candidates,
            localization_scores=localization_scores,
            program_graph=program_graph,
            use_prior_ranking=self.use_prior_ranking,
            use_diversity_reranking=self.use_diversity_reranking,
            diversity_weight=self.diversity_weight,
        )
        if self.use_candidate_deduplication:
            ranked_candidates = dedupe_patch_candidates(ranked_candidates)
        initial_pool_size = self._candidate_pool_size(len(ranked_candidates))
        evaluated = [
            self._evaluate(
                repo_path=repo_path,
                candidate=candidate,
                depth=0,
                parent_id=None,
                trace=[],
                localization_scores=localization_scores,
                program_graph=program_graph,
                test_args=test_args,
            )
            for candidate in ranked_candidates[:initial_pool_size]
        ]
        beam = _retain_topk(
            evaluated,
            self.beam_width,
            use_feedback_retention=self.use_feedback_retention,
        )
        visited = _mark_retained(evaluated, beam)

        depth = 0
        while depth < self.max_depth:
            if any(node.success for node in beam):
                break
            if self.refiner is None:
                break
            expanded: list[BeamPatchNode] = []
            for node in beam:
                refined_candidates = self._refine_candidates(
                    repo_path=repo_path,
                    node=node,
                )
                for child_index, refined in enumerate(refined_candidates):
                    expanded.append(
                        self._evaluate(
                            repo_path=repo_path,
                            candidate=_with_child_metadata(
                                refined,
                                parent_id=node.candidate.id,
                                child_index=child_index,
                                sibling_count=len(refined_candidates),
                            ),
                            depth=node.depth + 1,
                            parent_id=node.candidate.id,
                            trace=[
                                *node.trace,
                                f"{node.candidate.id}:{node.score:.4f}",
                            ],
                            localization_scores=localization_scores,
                            program_graph=program_graph,
                            test_args=test_args,
                        )
                    )
            if not expanded:
                break
            beam = _retain_topk(
                expanded,
                self.beam_width,
                use_feedback_retention=self.use_feedback_retention,
            )
            visited.extend(_mark_retained(expanded, beam))
            depth += 1

        return _topk(visited, len(visited))

    def _candidate_pool_size(self, total: int) -> int:
        if total <= 0:
            return 0
        if self.candidate_pool_size is None:
            return min(total, self.beam_width)
        return min(total, max(self.beam_width, self.candidate_pool_size))

    def _refine_candidates(
        self,
        *,
        repo_path: str | Path,
        node: BeamPatchNode,
    ) -> list[PatchCandidate]:
        if self.refiner is None:
            return []
        refine_many = getattr(self.refiner, "refine_many", None)
        if callable(refine_many):
            refined = refine_many(
                repo_path=repo_path,
                previous_patch=node.candidate,
                execution_result=node.execution_result,
                round_index=node.depth + 1,
                limit=self.refinement_width,
            )
            return _dedupe_candidates(list(refined), self.refinement_width)
        refined = self.refiner.refine(
            repo_path=repo_path,
            previous_patch=node.candidate,
            execution_result=node.execution_result,
            round_index=node.depth + 1,
        )
        return [refined] if refined is not None else []

    def _evaluate(
        self,
        repo_path: str | Path,
        candidate: PatchCandidate,
        depth: int,
        parent_id: str | None,
        trace: list[str],
        localization_scores: dict[str, float],
        program_graph: ProgramGraph | None,
        test_args: list[str] | None,
    ) -> BeamPatchNode:
        candidate = ensure_patch_risk(candidate, program_graph)
        candidate = annotate_refinement_context(candidate, program_graph)
        if _candidate_blocked_by_safety(candidate):
            execution_result = _safety_blocked_execution_result(candidate)
        else:
            execution_result = self.sandbox.apply_patch_and_test(
                repo_path,
                candidate,
                test_args=test_args,
            )
        candidate = annotate_execution_feedback(candidate, execution_result)
        localization_confidence = localization_scores.get(
            candidate.target_function_id, 0.0
        )
        candidate_risk = patch_risk_score(candidate)
        score = score_patch(
            candidate=candidate,
            result=execution_result,
            localization_confidence=localization_confidence,
            patch_risk=candidate_risk,
            weights=self.patch_score_weights,
        )
        patch_judgment = None
        if self.patch_judge is not None:
            patch_judgment = self.patch_judge.judge_patch(
                candidate=candidate,
                execution_result=execution_result,
                localization_confidence=localization_confidence,
                patch_risk=candidate_risk,
            )
            patch_judgment = calibrate_patch_judgment(
                patch_judgment,
                candidate=candidate,
                execution_result=execution_result,
                patch_risk=candidate_risk,
            )
            score = apply_patch_judgment_score(
                score,
                patch_judgment,
                self.patch_judge_weight,
            )
        retention = _retention_metadata(candidate, execution_result)
        retention = {
            **retention,
            "diversity_key": "|".join(
                _retention_diversity_key(
                    candidate,
                    failure_type=retention["failure_type"],
                    bucket=retention["bucket"],
                )
            ),
        }
        metadata = {
            **candidate.metadata,
            "beam_retention": retention,
        }
        if patch_judgment is not None:
            metadata["patch_judgment"] = patch_judgment.to_dict()
            metadata["patch_judge_weight"] = self.patch_judge_weight
        candidate = replace(candidate, metadata=metadata)
        return BeamPatchNode(
            candidate=candidate,
            execution_result=execution_result,
            score=score,
            depth=depth,
            feedback_score=execution_feedback_score(candidate),
            retention_bucket=retention["bucket"],
            retention_reason=retention["reason"],
            parent_id=parent_id,
            trace=trace,
        )


def _topk(nodes: list[BeamPatchNode], k: int) -> list[BeamPatchNode]:
    return sorted(
        nodes,
        key=lambda node: (node.score, node.feedback_score),
        reverse=True,
    )[:k]


def _retain_topk(
    nodes: list[BeamPatchNode],
    k: int,
    *,
    use_feedback_retention: bool,
) -> list[BeamPatchNode]:
    ordered = _topk(nodes, len(nodes))
    if k <= 0 or not ordered:
        return []
    if not use_feedback_retention or len(ordered) <= k:
        return ordered[:k]

    selected: list[BeamPatchNode] = []
    selected_ids: set[tuple[str, int, str | None]] = set()
    diversity_keys: set[tuple[str, str]] = set()

    def add(node: BeamPatchNode) -> None:
        selected.append(node)
        selected_ids.add(_node_key(node))
        diversity_keys.add(_diversity_key(node))

    for node in ordered:
        if len(selected) >= k:
            break
        if node.success:
            add(node)

    for node in ordered:
        if len(selected) >= k:
            break
        if _node_key(node) in selected_ids or _is_hard_failure(node):
            continue
        key = _diversity_key(node)
        if key in diversity_keys:
            continue
        add(node)

    for node in ordered:
        if len(selected) >= k:
            break
        if _node_key(node) in selected_ids:
            continue
        key = _diversity_key(node)
        if key in diversity_keys:
            continue
        add(node)

    for node in ordered:
        if len(selected) >= k:
            break
        if _node_key(node) not in selected_ids:
            add(node)

    return selected


def _mark_retained(
    evaluated: list[BeamPatchNode],
    retained: list[BeamPatchNode],
) -> list[BeamPatchNode]:
    retained_keys = {_node_key(node) for node in retained}
    return [
        replace(node, retained=_node_key(node) in retained_keys)
        for node in evaluated
    ]


def _with_child_metadata(
    candidate: PatchCandidate,
    *,
    parent_id: str,
    child_index: int,
    sibling_count: int,
) -> PatchCandidate:
    child = replace(
        candidate,
        metadata={
            **candidate.metadata,
            "beam_parent_id": parent_id,
            "beam_child_index": child_index,
            "beam_sibling_count": sibling_count,
        },
    )
    return _with_reflection_safety_gate_metadata(child)


def _with_reflection_safety_gate_metadata(candidate: PatchCandidate) -> PatchCandidate:
    existing_safety = candidate.metadata.get("safety_gate")
    if (
        isinstance(existing_safety, dict)
        and existing_safety.get("source") == "beam_reflection_candidate_safety_gate"
    ):
        return candidate
    static_rule_ids = candidate.metadata.get("static_rule_ids")
    rule_ids = [candidate.rule_id]
    if isinstance(static_rule_ids, list):
        rule_ids.extend(str(item) for item in static_rule_ids)
    validation = validate_function_patch(
        candidate.old_source,
        candidate.new_source,
        allow_signature_change=allow_signature_change_for_rules(rule_ids),
    )
    safety_gate = {
        **validation.to_dict(),
        "status": "pass" if validation.valid else "blocked",
        "minimal_diff": not (
            "patch_too_large" in validation.reasons
            or "patch_change_ratio_too_large" in validation.reasons
        ),
        "source": "beam_reflection_candidate_safety_gate",
    }
    return replace(
        candidate,
        metadata={
            **candidate.metadata,
            "validation": validation.to_dict(),
            "safety_gate": safety_gate,
        },
    )


def _candidate_blocked_by_safety(candidate: PatchCandidate) -> bool:
    safety = candidate.metadata.get("safety_gate")
    if not isinstance(safety, dict):
        return False
    return str(safety.get("status") or "") == "blocked"


def _safety_blocked_execution_result(candidate: PatchCandidate) -> ExecutionResult:
    safety = candidate.metadata.get("safety_gate")
    reasons = []
    if isinstance(safety, dict):
        reasons = [str(item) for item in safety.get("reasons", []) if str(item)]
    reason_text = ", ".join(reasons) if reasons else "safety_gate_blocked"
    return ExecutionResult(
        success=False,
        returncode=-1,
        stdout="",
        stderr=f"Patch candidate blocked by safety gate: {reason_text}",
        traceback="",
        passed=0,
        failed=0,
        timeout=False,
        command=["safety_gate"],
    )


def _dedupe_candidates(
    candidates: list[PatchCandidate],
    limit: int,
) -> list[PatchCandidate]:
    output: list[PatchCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        key = (
            candidate.target_function_id,
            candidate.new_source,
            candidate.diff,
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(candidate)
        if len(output) >= limit:
            break
    return output


def _node_key(node: BeamPatchNode) -> tuple[str, int, str | None]:
    return (node.candidate.id, node.depth, node.parent_id)


def _diversity_key(node: BeamPatchNode) -> tuple[str, str, str, str]:
    failure_type = _execution_feedback(node).get("failure_type", "")
    return _retention_diversity_key(
        node.candidate,
        failure_type=str(failure_type),
        bucket=node.retention_bucket,
    )


def _retention_diversity_key(
    candidate: PatchCandidate,
    *,
    failure_type: str,
    bucket: str,
) -> tuple[str, str, str, str]:
    return (
        candidate.target_function_id,
        candidate.rule_id,
        str(failure_type),
        bucket,
    )


def _is_hard_failure(node: BeamPatchNode) -> bool:
    return node.retention_bucket in {
        "hard_failure",
        "timeout",
        "execution_error",
    }


def _retention_metadata(
    candidate: PatchCandidate,
    result: ExecutionResult,
) -> dict[str, str]:
    feedback = candidate.metadata.get("execution_feedback", {})
    if not isinstance(feedback, dict):
        feedback = {}
    failure_type = str(feedback.get("failure_type", "unknown_failure"))
    passed_ratio = float(feedback.get("passed_ratio", 0.0))
    feedback_score = float(feedback.get("score", 0.0))

    if result.success:
        bucket = "success"
        reason = "sandbox success"
    elif failure_type == "test_failure" and passed_ratio > 0:
        bucket = "partial_test_failure"
        reason = f"test failure with passed_ratio={passed_ratio:.2f}"
    elif failure_type == "test_failure":
        bucket = "test_failure"
        reason = "test failure remains refinement-worthy"
    elif failure_type in {"type_error", "attribute_error", "runtime_error"}:
        bucket = "recoverable_runtime"
        reason = f"{failure_type} may be refined with traceback feedback"
    elif failure_type == "timeout":
        bucket = "timeout"
        reason = "timeout is deprioritized during retention"
    elif failure_type in {
        "patch_apply_error",
        "syntax_error",
        "import_error",
        "execution_error",
    }:
        bucket = "hard_failure"
        reason = f"{failure_type} is a low-value refinement seed"
    else:
        bucket = "unknown_failure"
        reason = "unknown failure retained only for diversity/fill"

    return {
        "bucket": bucket,
        "reason": reason,
        "failure_type": failure_type,
        "feedback_score": f"{feedback_score:.4f}",
        "passed_ratio": f"{passed_ratio:.4f}",
    }


def _execution_feedback(node: BeamPatchNode) -> dict:
    feedback = node.candidate.metadata.get("execution_feedback", {})
    return feedback if isinstance(feedback, dict) else {}
