from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from code_intelligence_agent.agents.patch_generator import SUPPORTED_RULE_IDS
from code_intelligence_agent.core.models import FaultLocalizationResult


@dataclass(frozen=True)
class PatchGenerationPlan:
    mode: str
    strategy: str
    generation_order: list[str]
    candidate_budget: int
    generator_budgets: dict[str, int]
    reason: str
    llm_available: bool
    evidence: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def plan_patch_generation(
    *,
    mode: str,
    ranked: list[FaultLocalizationResult],
    candidate_limit: int,
    llm_candidate_limit: int | None,
    llm_available: bool,
) -> PatchGenerationPlan:
    total = max(0, int(candidate_limit))
    llm_cap = total if llm_candidate_limit is None else min(
        total,
        max(0, int(llm_candidate_limit)),
    )
    evidence = _planning_evidence(ranked)
    if mode == "rule":
        return PatchGenerationPlan(
            mode=mode,
            strategy="rule_only",
            generation_order=["rule"],
            candidate_budget=total,
            generator_budgets={"rule": total, "llm": 0},
            reason="explicit_rule_mode",
            llm_available=llm_available,
            evidence=evidence,
        )
    if mode == "llm":
        return PatchGenerationPlan(
            mode=mode,
            strategy="llm_only",
            generation_order=["llm"],
            candidate_budget=total,
            generator_budgets={"rule": 0, "llm": llm_cap},
            reason="explicit_llm_mode",
            llm_available=llm_available,
            evidence=evidence,
        )

    supported_count = int(evidence["supported_rule_finding_count"])
    semantic_pressure = float(evidence["semantic_pressure"])
    static_pressure = float(evidence["static_pressure"])
    prefer_llm = supported_count == 0 or semantic_pressure > static_pressure + 0.15
    if not llm_available or llm_cap <= 0:
        return PatchGenerationPlan(
            mode=mode,
            strategy="adaptive_rule_fallback",
            generation_order=["rule", "llm"],
            candidate_budget=total,
            generator_budgets={"rule": total, "llm": 0},
            reason=(
                "llm_unavailable_rule_budget_reclaimed"
                if not llm_available
                else "llm_budget_disabled"
            ),
            llm_available=llm_available,
            evidence=evidence,
        )

    if prefer_llm:
        llm_budget = min(llm_cap, total if supported_count == 0 else max(1, math.ceil(total * 0.7)))
        rule_budget = max(0, total - llm_budget)
        order = ["llm", "rule"]
        strategy = "adaptive_llm_first"
        reason = (
            "no_supported_rule_for_top_ranked_evidence"
            if supported_count == 0
            else "semantic_pressure_exceeds_static_pressure"
        )
    else:
        llm_budget = min(llm_cap, max(1, math.floor(total * 0.4))) if total > 1 else 0
        rule_budget = max(0, total - llm_budget)
        order = ["rule", "llm"]
        strategy = "adaptive_rule_first"
        reason = "supported_deterministic_rule_evidence"
    return PatchGenerationPlan(
        mode=mode,
        strategy=strategy,
        generation_order=order,
        candidate_budget=total,
        generator_budgets={"rule": rule_budget, "llm": llm_budget},
        reason=reason,
        llm_available=llm_available,
        evidence=evidence,
    )


def _planning_evidence(
    ranked: list[FaultLocalizationResult],
) -> dict[str, object]:
    top = ranked[:5]
    rule_ids = [
        finding.rule_id
        for result in top
        for finding in result.findings
    ]
    supported = [rule_id for rule_id in rule_ids if rule_id in SUPPORTED_RULE_IDS]
    semantic_pressure = max(
        (
            max(
                float(result.signals.get("semantic", 0.0)),
                float(result.signals.get("llm", 0.0)),
                float(result.signals.get("traceback", 0.0)),
            )
            for result in top
        ),
        default=0.0,
    )
    static_pressure = max(
        (float(result.signals.get("static", 0.0)) for result in top),
        default=0.0,
    )
    return {
        "top_k_considered": len(top),
        "rule_finding_count": len(rule_ids),
        "supported_rule_finding_count": len(supported),
        "supported_rule_ids": sorted(set(supported)),
        "unsupported_rule_ids": sorted(set(rule_ids).difference(supported)),
        "semantic_pressure": round(semantic_pressure, 4),
        "static_pressure": round(static_pressure, 4),
    }
