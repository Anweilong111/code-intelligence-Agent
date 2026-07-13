from __future__ import annotations

from code_intelligence_agent.agents.patch_generation_policy import (
    plan_patch_generation,
)
from code_intelligence_agent.core.models import BugFinding, FaultLocalizationResult


def test_hybrid_plan_reserves_llm_budget_for_supported_rule_evidence():
    plan = plan_patch_generation(
        mode="hybrid",
        ranked=[_ranked(rule_id="possible_index_overrun", static=0.9)],
        candidate_limit=5,
        llm_candidate_limit=None,
        llm_available=True,
    )

    assert plan.strategy == "adaptive_rule_first"
    assert plan.generation_order == ["rule", "llm"]
    assert plan.generator_budgets == {"rule": 3, "llm": 2}
    assert plan.evidence["supported_rule_finding_count"] == 1


def test_hybrid_plan_uses_llm_first_without_supported_rule():
    plan = plan_patch_generation(
        mode="hybrid",
        ranked=[_ranked(rule_id="semantic_contract_bug", semantic=0.8)],
        candidate_limit=4,
        llm_candidate_limit=None,
        llm_available=True,
    )

    assert plan.strategy == "adaptive_llm_first"
    assert plan.generation_order == ["llm", "rule"]
    assert plan.generator_budgets == {"rule": 0, "llm": 4}
    assert plan.reason == "no_supported_rule_for_top_ranked_evidence"


def test_hybrid_plan_reclaims_budget_when_llm_is_unavailable():
    plan = plan_patch_generation(
        mode="hybrid",
        ranked=[_ranked(rule_id="possible_index_overrun", static=0.9)],
        candidate_limit=4,
        llm_candidate_limit=2,
        llm_available=False,
    )

    assert plan.strategy == "adaptive_rule_fallback"
    assert plan.generator_budgets == {"rule": 4, "llm": 0}
    assert plan.llm_available is False


def _ranked(
    *,
    rule_id: str,
    static: float = 0.0,
    semantic: float = 0.0,
) -> FaultLocalizationResult:
    finding = BugFinding(
        rule_id=rule_id,
        bug_type="test",
        message="test",
        function_id="sample.py::f",
        function_name="f",
        file_path="sample.py",
        line=1,
        confidence=static,
        evidence={},
    )
    return FaultLocalizationResult(
        function_id="sample.py::f",
        function_name="f",
        file_path="sample.py",
        start_line=1,
        end_line=2,
        score=max(static, semantic),
        rank=1,
        signals={"static": static, "semantic": semantic},
        findings=[finding],
        reason="test",
    )
