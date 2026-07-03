from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from code_intelligence_agent.core.models import ExecutionResult, PatchCandidate
from code_intelligence_agent.core.program_graph import ProgramGraph
from code_intelligence_agent.search.candidate_ranking import (
    dedupe_patch_candidates,
    patch_risk_score,
    rank_patch_candidates,
)
from code_intelligence_agent.search.execution_feedback import (
    annotate_execution_feedback,
    execution_feedback_score,
)
from code_intelligence_agent.search.scoring import PatchScoreWeights, score_patch
from code_intelligence_agent.tools.sandbox import Sandbox


@dataclass(frozen=True)
class PatchSearchResult:
    candidate: PatchCandidate
    execution_result: ExecutionResult
    score: float
    feedback_score: float = 0.0

    @property
    def success(self) -> bool:
        return self.execution_result.success


class PatchSearch:
    def __init__(
        self,
        sandbox: Sandbox | None = None,
        beam_width: int = 3,
        use_prior_ranking: bool = True,
        use_diversity_reranking: bool = True,
        diversity_weight: float = 0.06,
        patch_score_weights: PatchScoreWeights | None = None,
        use_candidate_deduplication: bool = True,
    ) -> None:
        self.sandbox = sandbox or Sandbox()
        self.beam_width = beam_width
        self.use_prior_ranking = use_prior_ranking
        self.use_diversity_reranking = use_diversity_reranking
        self.diversity_weight = diversity_weight
        self.patch_score_weights = patch_score_weights
        self.use_candidate_deduplication = use_candidate_deduplication

    def search(
        self,
        repo_path: str | Path,
        candidates: list[PatchCandidate],
        localization_scores: dict[str, float] | None = None,
        program_graph: ProgramGraph | None = None,
        test_args: list[str] | None = None,
    ) -> list[PatchSearchResult]:
        localization_scores = localization_scores or {}
        results: list[PatchSearchResult] = []
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
        for candidate in ranked_candidates[: self.beam_width]:
            execution_result = self.sandbox.apply_patch_and_test(
                repo_path,
                candidate,
                test_args=test_args,
            )
            candidate = annotate_execution_feedback(candidate, execution_result)
            score = score_patch(
                candidate=candidate,
                result=execution_result,
                localization_confidence=localization_scores.get(
                    candidate.target_function_id, 0.0
                ),
                patch_risk=patch_risk_score(candidate),
                weights=self.patch_score_weights,
            )
            results.append(
                PatchSearchResult(
                    candidate=candidate,
                    execution_result=execution_result,
                    score=score,
                    feedback_score=execution_feedback_score(candidate),
                )
            )
        return sorted(
            results,
            key=lambda item: (item.score, item.feedback_score),
            reverse=True,
        )
