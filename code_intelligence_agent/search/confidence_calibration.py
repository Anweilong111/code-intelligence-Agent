from __future__ import annotations

from dataclasses import asdict, dataclass

from code_intelligence_agent.core.models import BugFinding
from code_intelligence_agent.search.scoring import diff_size


@dataclass(frozen=True)
class ConfidenceCalibration:
    score: float
    base_confidence: float
    rule_prior: float
    evidence_bonus: float
    variant_penalty: float
    diff_penalty: float
    reasons: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


RULE_PRIORS = {
    "possible_index_overrun": 0.05,
    "dict_missing_key_guard": 0.04,
    "inplace_api_return_value": 0.04,
    "stringified_numeric_value": 0.04,
    "missing_len_zero_guard": 0.04,
    "identity_comparison_literal": 0.03,
    "iterator_double_consumption": 0.03,
    "inverted_empty_guard": 0.03,
    "enumerate_start_zero_counter": 0.03,
    "always_true_len_check": 0.02,
    "mutable_default_arg": 0.02,
    "broad_exception_pass": 0.01,
}


def calibrate_patch_confidence(
    finding: BugFinding,
    *,
    variant_rank: int,
    diff: str,
) -> ConfidenceCalibration:
    reasons: list[str] = []
    base_confidence = _clamp(finding.confidence)
    rule_prior = RULE_PRIORS.get(finding.rule_id, 0.0)
    if rule_prior:
        reasons.append(f"rule_prior={rule_prior:.2f}")
    evidence_bonus, evidence_reasons = _evidence_bonus(finding)
    reasons.extend(evidence_reasons)
    variant_penalty = min(0.18, max(0, variant_rank) * 0.08)
    if variant_penalty:
        reasons.append(f"variant_penalty={variant_penalty:.2f}")
    size = diff_size(diff)
    diff_penalty = min(0.08, size / 100)
    if diff_penalty:
        reasons.append(f"diff_penalty={diff_penalty:.2f}")
    score = _clamp(
        base_confidence
        + rule_prior
        + evidence_bonus
        - variant_penalty
        - diff_penalty
    )
    return ConfidenceCalibration(
        score=round(score, 4),
        base_confidence=round(base_confidence, 4),
        rule_prior=round(rule_prior, 4),
        evidence_bonus=round(evidence_bonus, 4),
        variant_penalty=round(variant_penalty, 4),
        diff_penalty=round(diff_penalty, 4),
        reasons=reasons,
    )


def _evidence_bonus(finding: BugFinding) -> tuple[float, list[str]]:
    evidence = finding.evidence or {}
    bonus = 0.0
    reasons: list[str] = []
    if finding.rule_id == "possible_index_overrun" and evidence.get("index_line"):
        bonus += 0.04
        reasons.append("positive_offset_index_evidence")
    if finding.rule_id == "missing_len_zero_guard":
        if evidence.get("variable") and evidence.get("denominator_line"):
            bonus += 0.03
            reasons.append("len_denominator_evidence")
        if evidence.get("len_source"):
            bonus += 0.01
            reasons.append("len_source_evidence")
    if finding.rule_id == "inplace_api_return_value":
        if evidence.get("method") and evidence.get("receiver"):
            bonus += 0.03
            reasons.append("mutating_method_receiver_evidence")
    if finding.rule_id == "stringified_numeric_value" and evidence.get("variable"):
        bonus += 0.03
        reasons.append("numeric_reuse_evidence")
    if finding.rule_id == "enumerate_start_zero_counter" and evidence.get("counter"):
        bonus += 0.03
        reasons.append("counter_evidence")
    if finding.rule_id == "inverted_empty_guard" and evidence.get("guard_name"):
        bonus += 0.03
        reasons.append("inverted_guard_evidence")
    if finding.rule_id == "identity_comparison_literal":
        if evidence.get("literal") and evidence.get("operator"):
            bonus += 0.03
            reasons.append("literal_identity_operator_evidence")
    if finding.rule_id == "iterator_double_consumption":
        if evidence.get("iterable") and evidence.get("consumer"):
            bonus += 0.03
            reasons.append("iterator_reuse_evidence")
    if finding.rule_id == "dict_missing_key_guard":
        if evidence.get("mapping") and evidence.get("key"):
            bonus += 0.03
            reasons.append("mapping_key_access_evidence")
    return min(0.08, bonus), reasons


def _clamp(value: float) -> float:
    return max(0.0, min(0.99, value))
