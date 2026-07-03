from __future__ import annotations

from dataclasses import replace
from typing import Any

from code_intelligence_agent.core.models import PatchCandidate
from code_intelligence_agent.core.program_graph import ProgramGraph
from code_intelligence_agent.search.candidate_diversity import stable_source_fingerprint
from code_intelligence_agent.search.patch_risk import PatchRiskAnalyzer, annotate_patch_risk
from code_intelligence_agent.search.scoring import score_patch_prior


DEFAULT_DIVERSITY_WEIGHT = 0.06


def rank_patch_candidates(
    candidates: list[PatchCandidate],
    localization_scores: dict[str, float] | None = None,
    program_graph: ProgramGraph | None = None,
    use_prior_ranking: bool = True,
    use_diversity_reranking: bool = True,
    diversity_weight: float = DEFAULT_DIVERSITY_WEIGHT,
) -> list[PatchCandidate]:
    localization_scores = localization_scores or {}
    ranked = [ensure_patch_risk(candidate, program_graph) for candidate in candidates]
    if not use_prior_ranking:
        return [_with_disabled_prior_score(candidate) for candidate in ranked]
    ranked = [_with_prior_score(candidate, localization_scores) for candidate in ranked]
    ranked = sorted(
        ranked,
        key=lambda candidate: float(candidate.metadata.get("search_prior_score", 0.0)),
        reverse=True,
    )
    if use_diversity_reranking:
        ranked = rerank_patch_candidates_for_diversity(
            ranked,
            diversity_weight=diversity_weight,
        )
    return ranked


def rerank_patch_candidates_for_diversity(
    candidates: list[PatchCandidate],
    *,
    diversity_weight: float = DEFAULT_DIVERSITY_WEIGHT,
) -> list[PatchCandidate]:
    remaining = list(enumerate(candidates, start=1))
    selected: list[PatchCandidate] = []
    seen_functions: set[str] = set()
    seen_rules: set[str] = set()
    seen_variants: set[str] = set()
    seen_risk_buckets: set[str] = set()

    while remaining:
        best = max(
            remaining,
            key=lambda item: _diversity_selection_key(
                item[0],
                item[1],
                seen_functions,
                seen_rules,
                seen_variants,
                seen_risk_buckets,
                diversity_weight,
            ),
        )
        remaining.remove(best)
        base_rank, candidate = best
        bonus, reasons = _diversity_bonus(
            candidate,
            seen_functions,
            seen_rules,
            seen_variants,
            seen_risk_buckets,
        )
        diversity_score = _diversity_score(candidate, bonus, diversity_weight)
        selected.append(
            _with_diversity_metadata(
                candidate,
                base_rank=base_rank,
                diversity_rank=len(selected) + 1,
                bonus=bonus,
                diversity_score=diversity_score,
                reasons=reasons,
            )
        )
        seen_functions.add(candidate.target_function_id)
        seen_rules.add(candidate.rule_id)
        seen_variants.add(_variant_key(candidate))
        seen_risk_buckets.add(_risk_bucket(candidate))

    return selected


def ensure_patch_risk(
    candidate: PatchCandidate,
    program_graph: ProgramGraph | None,
) -> PatchCandidate:
    if program_graph is None or isinstance(candidate.metadata.get("risk"), dict):
        return candidate
    return annotate_patch_risk(
        candidate,
        PatchRiskAnalyzer().analyze(candidate, program_graph),
    )


def patch_risk_score(candidate: PatchCandidate) -> float:
    risk = candidate.metadata.get("risk", {})
    if isinstance(risk, dict):
        return float(risk.get("score", 0.0))
    return 0.0


def dedupe_patch_candidates(
    candidates: list[PatchCandidate],
) -> list[PatchCandidate]:
    groups: dict[tuple[str, str, str, str], list[PatchCandidate]] = {}
    order: list[tuple[str, str, str, str]] = []
    for candidate in candidates:
        key = patch_candidate_fingerprint(candidate)
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(candidate)
    return [
        _with_deduplication_metadata(groups[key][0], groups[key])
        for key in order
    ]


def patch_candidate_fingerprint(candidate: PatchCandidate) -> tuple[str, str, str, str]:
    return (
        candidate.target_function_id,
        candidate.relative_file_path,
        stable_source_fingerprint(candidate.new_source),
        stable_source_fingerprint(candidate.diff),
    )


def _with_prior_score(
    candidate: PatchCandidate,
    localization_scores: dict[str, float],
) -> PatchCandidate:
    prior = score_patch_prior(
        candidate=candidate,
        localization_confidence=localization_scores.get(
            candidate.target_function_id,
            0.0,
        ),
        patch_risk=patch_risk_score(candidate),
    )
    return replace(
        candidate,
        metadata={
            **candidate.metadata,
            "search_prior_score": prior,
        },
    )


def _with_disabled_prior_score(candidate: PatchCandidate) -> PatchCandidate:
    return replace(
        candidate,
        metadata={
            **candidate.metadata,
            "search_prior_score": 0.0,
        },
    )


def _with_deduplication_metadata(
    candidate: PatchCandidate,
    group: list[PatchCandidate],
) -> PatchCandidate:
    duplicate_ids = [item.id for item in group[1:]]
    return replace(
        candidate,
        metadata={
            **candidate.metadata,
            "search_deduplication": {
                "fingerprint": "|".join(patch_candidate_fingerprint(candidate)),
                "canonical_id": candidate.id,
                "duplicate_count": len(duplicate_ids),
                "duplicate_ids": duplicate_ids,
            },
            "search_duplicate_count": len(duplicate_ids),
        },
    )


def _diversity_selection_key(
    base_rank: int,
    candidate: PatchCandidate,
    seen_functions: set[str],
    seen_rules: set[str],
    seen_variants: set[str],
    seen_risk_buckets: set[str],
    diversity_weight: float,
) -> tuple[float, float, int]:
    bonus, _ = _diversity_bonus(
        candidate,
        seen_functions,
        seen_rules,
        seen_variants,
        seen_risk_buckets,
    )
    diversity_score = _diversity_score(candidate, bonus, diversity_weight)
    prior = float(candidate.metadata.get("search_prior_score", 0.0))
    return (diversity_score, prior, -base_rank)


def _diversity_bonus(
    candidate: PatchCandidate,
    seen_functions: set[str],
    seen_rules: set[str],
    seen_variants: set[str],
    seen_risk_buckets: set[str],
) -> tuple[float, list[str]]:
    bonus = 0.0
    reasons: list[str] = []
    if candidate.target_function_id not in seen_functions:
        bonus += 0.35
        reasons.append("new_function")
    if candidate.rule_id not in seen_rules:
        bonus += 0.35
        reasons.append("new_rule")
    variant = _variant_key(candidate)
    if variant and variant not in seen_variants:
        bonus += 0.20
        reasons.append("new_variant")
    risk_bucket = _risk_bucket(candidate)
    if risk_bucket not in seen_risk_buckets:
        bonus += 0.10
        reasons.append(f"new_risk_bucket:{risk_bucket}")
    if not reasons:
        reasons.append("duplicate_search_signature")
    return round(bonus, 4), reasons


def _diversity_score(
    candidate: PatchCandidate,
    bonus: float,
    diversity_weight: float,
) -> float:
    prior = float(candidate.metadata.get("search_prior_score", 0.0))
    return round(prior + max(0.0, diversity_weight) * bonus, 4)


def _with_diversity_metadata(
    candidate: PatchCandidate,
    *,
    base_rank: int,
    diversity_rank: int,
    bonus: float,
    diversity_score: float,
    reasons: list[str],
) -> PatchCandidate:
    metadata: dict[str, Any] = {
        **candidate.metadata,
        "search_diversity": {
            "base_rank": base_rank,
            "rank": diversity_rank,
            "bonus": bonus,
            "score": diversity_score,
            "reasons": reasons,
            "function_id": candidate.target_function_id,
            "rule_id": candidate.rule_id,
            "variant": _variant_key(candidate),
            "risk_bucket": _risk_bucket(candidate),
        },
        "search_diversity_rank": diversity_rank,
        "search_diversity_bonus": bonus,
        "search_diversity_score": diversity_score,
    }
    return replace(candidate, metadata=metadata)


def _variant_key(candidate: PatchCandidate) -> str:
    variant = candidate.metadata.get("variant", "")
    return str(variant) if variant else candidate.id


def _risk_bucket(candidate: PatchCandidate) -> str:
    risk = candidate.metadata.get("risk", {})
    if isinstance(risk, dict):
        level = str(risk.get("level", ""))
        if level:
            return level
        score = float(risk.get("score", 0.0))
    else:
        score = 0.0
    if score >= 0.60:
        return "high"
    if score >= 0.30:
        return "medium"
    return "low"
