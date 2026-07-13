from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V1_DOC_ROOT = ROOT / "docs" / "examples"


def test_v1_sample_report_pack_is_tracked_and_self_contained():
    index = V1_DOC_ROOT / "v1_sample_reports.md"
    pack = V1_DOC_ROOT / "v1_reports" / "README.md"
    case_docs = [
        V1_DOC_ROOT / "v1_reports" / "pypa_sampleproject.md",
        V1_DOC_ROOT / "v1_reports" / "pluggy.md",
        V1_DOC_ROOT / "v1_reports" / "octocat_hello_world.md",
    ]
    docs = [index, pack, *case_docs]

    for path in docs:
        assert path.exists(), path
        text = path.read_text(encoding="utf-8")
        assert "outputs_smoke" not in text
        assert "Observe -> Plan -> Act -> Verify -> Reflect -> Replan" in (
            text if path == pack else (pack.read_text(encoding="utf-8") + text)
        )

    index_text = index.read_text(encoding="utf-8")
    for link in _markdown_links(index_text):
        if link.startswith(("http://", "https://", "#")):
            continue
        assert (index.parent / link).resolve().exists(), link


def test_v1_sample_case_docs_cover_agent_decision_and_audit_fields():
    required_terms = [
        "Agent Loop",
        "Controller Decision",
        "Final Audit",
        "Objective compliance",
        "Repair success claim",
    ]
    for path in (V1_DOC_ROOT / "v1_reports").glob("*.md"):
        text = path.read_text(encoding="utf-8")
        if path.name == "README.md":
            continue
        for term in required_terms:
            assert term in text, f"{path}: missing {term}"


def test_top_level_agent_live_smoke_documents_real_entrypoint_and_audit():
    path = V1_DOC_ROOT / "top_level_agent_live_smoke.md"
    text = path.read_text(encoding="utf-8")

    for term in [
        "python -m code_intelligence_agent agent",
        "https://github.com/pytest-dev/iniconfig",
        "49",
        "no_static_candidates_report_ready",
        "selected_action_already_applied",
        "Complete Agent loop recorded",
        "Observe -> Plan -> Act -> Verify -> Reflect -> Replan",
        "https://github.com/pallets/itsdangerous",
        "Repository Understanding",
        "Top-k Localization",
        "Test Diagnosis",
        "AgentController Trace",
        "Final Audit",
        "await_environment_repair",
        "dynamic_evidence_not_usable:environment_failure",
        "Repair success claim",
        "`not_claimed`",
    ]:
        assert term in text

    assert "outputs_live" not in text
    assert "outputs_smoke" not in text


def test_llm_repair_readiness_documents_env_only_blocker_and_sandbox_authority():
    path = V1_DOC_ROOT / "llm_repair_readiness.md"
    text = path.read_text(encoding="utf-8")

    for term in [
        "repo_intelligence_llm_repair_smoke.example.json",
        "repo_intelligence_hybrid_no_key_smoke.example.json",
        "CIA_LLM_API_KEY",
        "CIA_REPLAN_LLM_API_KEY",
        "DEEPSEEK_API_KEY",
        "llm_replan_advisor",
        "advisory-only",
        "missing_llm_api_key",
        "deepseek-v4-pro",
        "sandbox_pytest_decides_success",
        "Rule candidates",
        "LLM candidates",
        "Successful sandbox candidates",
        "run_search_and_ablation_evaluation",
    ]:
        assert term in text

    assert not re.search(r"\bsk-[A-Za-z0-9._-]{16,}", text)
    assert "outputs_live" not in text
    assert "outputs_smoke" not in text


def test_v1_readiness_audit_doc_tracks_dataset_and_metric_contracts():
    path = V1_DOC_ROOT / "v1_readiness_audit.md"
    text = path.read_text(encoding="utf-8")

    for term in [
        "V1 Readiness Audit",
        "30/30",
        "50/50",
        "9/9",
        "Observe -> Plan -> Act -> Verify -> Reflect -> Replan",
        "onboarding_success_rate",
        "topk_localization_accuracy",
        "pass_at_1",
        "pass_at_k",
        "reflection_uplift",
        "blocker_accuracy",
        "sandbox_success_rate",
        "average_runtime_ms",
        "llm_cost_usd",
        "v1_evaluation_summary",
        "v1_onboarding_slice_aggregate",
        "llm_cost_evidence",
        "measured",
        "proxy",
        "missing_evidence",
        "--start-index",
        "--limit-runs",
    ]:
        assert term in text

    assert not re.search(r"\bsk-[A-Za-z0-9._-]{16,}", text)


def test_v1_evaluation_summary_doc_tracks_current_metric_boundary():
    path = V1_DOC_ROOT / "v1_evaluation_summary.md"
    text = path.read_text(encoding="utf-8")

    for term in [
        "V1 Evaluation Summary",
        "30/30",
        "9/9",
        "0",
        "llm_cost_usd",
        "configured pricing",
        "Observe -> Plan -> Act -> Verify -> Reflect -> Replan",
        "reflection_success_case_rate",
        "sandbox_success_rate",
    ]:
        assert term in text

    assert not re.search(r"\bsk-[A-Za-z0-9._-]{16,}", text)
    assert "outputs_smoke" not in text
    assert "outputs_live" not in text
    assert "outputs_smoke" not in text


def _markdown_links(text: str) -> list[str]:
    return re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
