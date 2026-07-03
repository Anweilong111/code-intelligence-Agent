from __future__ import annotations

from dataclasses import asdict, dataclass

from code_intelligence_agent.core.models import ExecutionResult, PatchCandidate
from code_intelligence_agent.search.execution_feedback import execution_feedback_score


@dataclass(frozen=True)
class PatchScoreWeights:
    tests_passed: float = 0.60
    localization: float = 0.20
    static_check: float = 0.10
    prior: float = 0.0
    execution_feedback: float = 0.08
    diff_penalty: float = 0.06
    risk_penalty: float = 0.03
    warning_penalty: float = 0.01
    success_bonus: float = 0.12

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_PATCH_SCORE_WEIGHTS = PatchScoreWeights()


def score_patch(
    candidate: PatchCandidate,
    result: ExecutionResult,
    localization_confidence: float = 0.0,
    patch_risk: float = 0.0,
    weights: PatchScoreWeights | None = None,
) -> float:
    weights = weights or DEFAULT_PATCH_SCORE_WEIGHTS
    total_tests = result.passed + result.failed
    if total_tests:
        tests_passed_ratio = result.passed / total_tests
    else:
        tests_passed_ratio = 1.0 if result.success else 0.0
    static_check_pass = 1.0 if not result.timeout and result.returncode != -1 else 0.0
    prior_score = float(candidate.metadata.get("search_prior_score", 0.0) or 0.0)
    feedback_score = execution_feedback_score(candidate)
    diff_penalty = min(1.0, diff_size(candidate.diff) / 50)
    warning_penalty = 1.0 if "warning" in result.stderr.lower() else 0.0
    score = (
        weights.tests_passed * tests_passed_ratio
        + weights.localization * localization_confidence
        + weights.static_check * static_check_pass
        + weights.prior * prior_score
        + weights.execution_feedback * feedback_score
        - weights.diff_penalty * diff_penalty
        - weights.risk_penalty * patch_risk
        - weights.warning_penalty * warning_penalty
    )
    if result.success:
        score += weights.success_bonus
    return round(max(0.0, min(1.0, score)), 4)


def score_patch_prior(
    candidate: PatchCandidate,
    localization_confidence: float = 0.0,
    patch_risk: float = 0.0,
) -> float:
    rule_confidence = float(
        candidate.metadata.get(
            "confidence",
            candidate.metadata.get("rule_confidence", 0.0),
        )
    )
    variant_rank = max(0.0, float(candidate.metadata.get("variant_rank", 0.0)))
    variant_prior = 1.0 / (1.0 + variant_rank)
    diff_penalty = min(1.0, diff_size(candidate.diff) / 50)
    score = (
        0.55 * localization_confidence
        + 0.20 * rule_confidence
        + 0.20 * variant_prior
        - 0.03 * patch_risk
        - 0.02 * diff_penalty
    )
    return round(max(0.0, min(1.0, score)), 4)


def diff_size(diff: str) -> int:
    count = 0
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", "-")):
            count += 1
    return count
