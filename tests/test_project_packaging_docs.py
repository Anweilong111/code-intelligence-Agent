from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_resume_and_interview_docs_use_v1_metrics_without_stale_p6_claims():
    for relative_path in [
        "RESUME_AGENT_PROJECT.md",
        "INTERVIEW_QA_AGENT_PROJECT.md",
        "docs/showcase/github_release_guide.md",
    ]:
        text = (ROOT / relative_path).read_text(encoding="utf-8")

        assert "V1" in text
        assert "30/30" in text or "30 个真实 GitHub" in text
        assert "50" in text
        assert "9/9" in text
        assert "Observe -> Plan -> Act -> Verify -> Reflect -> Replan" in text
        if relative_path == "docs/showcase/github_release_guide.md":
            assert "release_hygiene_audit" in text
            assert "v1_goal_completion_audit" in text
        assert "LLM judge " + "可以替代 pytest sandbox" not in text
        assert "P6 验证覆盖 10 个真实仓库 onboarding case" not in text
        assert "10 real GitHub onboarding cases" not in text
