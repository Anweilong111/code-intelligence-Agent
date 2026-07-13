from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from code_intelligence_agent.agents.reflector import ReflectionAgent, ReflectionDecision
from code_intelligence_agent.core.models import ExecutionResult, PatchCandidate
from code_intelligence_agent.core.program_graph import ProgramGraph
from code_intelligence_agent.search.candidate_ranking import (
    dedupe_patch_candidates,
    rank_patch_candidates,
)
from code_intelligence_agent.search.execution_feedback import annotate_execution_feedback
from code_intelligence_agent.search.refinement_context import annotate_refinement_context
from code_intelligence_agent.search.scoring import PatchScoreWeights, score_patch
from code_intelligence_agent.tools.patch_validation import (
    allow_signature_change_for_rules,
)
from code_intelligence_agent.tools.patch_safety import (
    PatchSafetyPolicy,
    apply_patch_safety_gate,
)
from code_intelligence_agent.tools.sandbox import Sandbox


class PatchRefiner(Protocol):
    def refine(
        self,
        repo_path: str | Path,
        previous_patch: PatchCandidate,
        execution_result: ExecutionResult,
        round_index: int,
    ) -> PatchCandidate | None:
        ...


class PatchBatchRefiner(PatchRefiner, Protocol):
    def refine_many(
        self,
        repo_path: str | Path,
        previous_patch: PatchCandidate,
        execution_result: ExecutionResult,
        round_index: int,
        limit: int = 1,
    ) -> list[PatchCandidate]:
        ...


@dataclass(frozen=True)
class RepairAttempt:
    round_index: int
    candidate: PatchCandidate
    execution_result: ExecutionResult
    score: float
    reflection: ReflectionDecision


@dataclass(frozen=True)
class RepairResult:
    success: bool
    best_candidate: PatchCandidate | None
    best_result: ExecutionResult | None
    attempts: list[RepairAttempt] = field(default_factory=list)

    @property
    def rounds(self) -> int:
        return len(self.attempts)


class RepairLoop:
    def __init__(
        self,
        sandbox: Sandbox | None = None,
        reflector: ReflectionAgent | None = None,
        refiner: PatchRefiner | None = None,
        max_rounds: int = 3,
        use_prior_ranking: bool = True,
        use_diversity_reranking: bool = True,
        diversity_weight: float = 0.06,
        patch_score_weights: PatchScoreWeights | None = None,
        refinement_width: int = 1,
        use_candidate_deduplication: bool = True,
    ) -> None:
        self.sandbox = sandbox or Sandbox()
        self.reflector = reflector or ReflectionAgent()
        self.refiner = refiner
        self.max_rounds = max_rounds
        self.use_prior_ranking = use_prior_ranking
        self.use_diversity_reranking = use_diversity_reranking
        self.diversity_weight = diversity_weight
        self.patch_score_weights = patch_score_weights
        self.refinement_width = max(1, refinement_width)
        self.use_candidate_deduplication = use_candidate_deduplication

    def run(
        self,
        repo_path: str | Path,
        candidates: list[PatchCandidate],
        localization_scores: dict[str, float] | None = None,
        test_args: list[str] | None = None,
        program_graph: ProgramGraph | None = None,
    ) -> RepairResult:
        localization_scores = localization_scores or {}
        attempts: list[RepairAttempt] = []
        best_candidate: PatchCandidate | None = None
        best_result: ExecutionResult | None = None
        best_score = -1.0

        queue = rank_patch_candidates(
            candidates,
            localization_scores=localization_scores,
            program_graph=program_graph,
            use_prior_ranking=self.use_prior_ranking,
            use_diversity_reranking=self.use_diversity_reranking,
            diversity_weight=self.diversity_weight,
        )
        if self.use_candidate_deduplication:
            queue = dedupe_patch_candidates(queue)
        queue = [
            annotate_refinement_context(candidate, program_graph)
            for candidate in queue
        ]
        round_index = 0
        while round_index < self.max_rounds and round_index < len(queue):
            candidate = _with_safety_gate_metadata(
                queue[round_index],
                repository_root=repo_path,
            )
            if _candidate_blocked_by_safety(candidate):
                execution_result = _safety_blocked_execution_result(candidate)
            else:
                execution_result = self.sandbox.apply_patch_and_test(
                    repo_path,
                    candidate,
                    test_args=test_args,
                )
            candidate = annotate_execution_feedback(candidate, execution_result)
            candidate_score = score_patch(
                candidate=candidate,
                result=execution_result,
                localization_confidence=localization_scores.get(
                    candidate.target_function_id, 0.0
                ),
                patch_risk=_patch_risk(candidate),
                weights=self.patch_score_weights,
            )
            reflection = self.reflector.reflect(
                patch=candidate,
                result=execution_result,
                round_index=round_index,
                max_rounds=self.max_rounds,
            )
            attempts.append(
                RepairAttempt(
                    round_index=round_index,
                    candidate=candidate,
                    execution_result=execution_result,
                    score=candidate_score,
                    reflection=reflection,
                )
            )
            if candidate_score > best_score:
                best_score = candidate_score
                best_candidate = candidate
                best_result = execution_result
            if execution_result.success:
                return RepairResult(
                    success=True,
                    best_candidate=candidate,
                    best_result=execution_result,
                    attempts=attempts,
                )
            if not reflection.should_retry:
                break
            if self.refiner is not None:
                refined_candidates = self._refine_candidates(
                    repo_path=repo_path,
                    previous_patch=candidate,
                    execution_result=execution_result,
                    round_index=round_index + 1,
                    queue=queue,
                )
                if refined_candidates:
                    queue[round_index + 1 : round_index + 1] = refined_candidates
            round_index += 1

        return RepairResult(
            success=False,
            best_candidate=best_candidate,
            best_result=best_result,
            attempts=attempts,
        )

    def _refine_candidates(
        self,
        *,
        repo_path: str | Path,
        previous_patch: PatchCandidate,
        execution_result: ExecutionResult,
        round_index: int,
        queue: list[PatchCandidate],
    ) -> list[PatchCandidate]:
        if self.refiner is None:
            return []
        refine_many = getattr(self.refiner, "refine_many", None)
        if callable(refine_many):
            refined = refine_many(
                repo_path=repo_path,
                previous_patch=previous_patch,
                execution_result=execution_result,
                round_index=round_index,
                limit=self.refinement_width,
            )
            candidates = list(refined or [])
        else:
            refined = self.refiner.refine(
                repo_path=repo_path,
                previous_patch=previous_patch,
                execution_result=execution_result,
                round_index=round_index,
            )
            candidates = [refined] if refined is not None else []
        deduped = _dedupe_refined_candidates(
            candidates,
            existing=queue,
            limit=self.refinement_width,
        )
        return [
            _with_repair_child_metadata(
                candidate,
                parent_id=previous_patch.id,
                child_index=child_index,
                sibling_count=len(deduped),
                round_index=round_index,
            )
            for child_index, candidate in enumerate(deduped)
        ]


def _patch_risk(candidate: PatchCandidate) -> float:
    risk = candidate.metadata.get("risk", {})
    if isinstance(risk, dict):
        return float(risk.get("score", 0.0))
    return 0.0


def _dedupe_refined_candidates(
    candidates: list[PatchCandidate],
    *,
    existing: list[PatchCandidate],
    limit: int,
) -> list[PatchCandidate]:
    output: list[PatchCandidate] = []
    seen = {_candidate_key(candidate) for candidate in existing}
    for candidate in candidates:
        key = _candidate_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        output.append(candidate)
        if len(output) >= limit:
            break
    return output


def _candidate_key(candidate: PatchCandidate) -> tuple[str, str, str]:
    return (
        candidate.target_function_id,
        candidate.new_source,
        candidate.diff,
    )


def _with_repair_child_metadata(
    candidate: PatchCandidate,
    *,
    parent_id: str,
    child_index: int,
    sibling_count: int,
    round_index: int,
) -> PatchCandidate:
    return replace(
        candidate,
        metadata={
            **candidate.metadata,
            "repair_loop_parent_id": parent_id,
            "repair_loop_child_index": child_index,
            "repair_loop_sibling_count": sibling_count,
            "repair_loop_round_index": round_index,
        },
    )


def _with_safety_gate_metadata(
    candidate: PatchCandidate,
    *,
    repository_root: str | Path | None = None,
) -> PatchCandidate:
    existing_safety = candidate.metadata.get("safety_gate")
    if (
        isinstance(existing_safety, dict)
        and existing_safety.get("source") == "repair_loop_reflection_candidate_safety_gate"
    ):
        return candidate
    static_rule_ids = candidate.metadata.get("static_rule_ids")
    rule_ids = [candidate.rule_id]
    if isinstance(static_rule_ids, list):
        rule_ids.extend(str(item) for item in static_rule_ids)
    return apply_patch_safety_gate(
        candidate,
        repository_root=repository_root,
        policy=PatchSafetyPolicy(
            allow_signature_change=allow_signature_change_for_rules(rule_ids),
            authorized_files=(candidate.relative_file_path,),
        ),
        source="repair_loop_reflection_candidate_safety_gate",
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
        reasons = [str(item) for item in safety.get("reasons", [])]
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
