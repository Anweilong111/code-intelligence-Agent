from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.MD"
ARCHITECTURE = ROOT / "docs" / "v2" / "architecture_and_design.md"
CASE_STUDIES = ROOT / "docs" / "v2" / "phase8_case_studies.md"
DEMO_GUIDE = ROOT / "docs" / "v2" / "phase8_demo_guide_cn.md"
CAREER_PACK = ROOT / "docs" / "career" / "v2_resume_interview_pack_cn.md"
PHASE7_REPORT = (
    ROOT
    / "docs"
    / "v2"
    / "phase7_artifacts"
    / "phase7_system_evaluation.md"
)


def test_phase8_required_public_documents_exist_and_are_substantive():
    minimum_sizes = {
        ARCHITECTURE: 8_000,
        CASE_STUDIES: 6_000,
        DEMO_GUIDE: 6_000,
        CAREER_PACK: 12_000,
    }

    for path, minimum_size in minimum_sizes.items():
        assert path.exists(), path
        assert len(path.read_text(encoding="utf-8")) >= minimum_size, path


def test_architecture_records_agent_loop_scoring_and_safety_boundaries():
    text = ARCHITECTURE.read_text(encoding="utf-8")

    assert text.count("```mermaid") >= 4
    assert "Observe" in text
    assert "Plan" in text
    assert "Act" in text
    assert "Verify" in text
    assert "Reflect" in text
    assert "Replan" in text
    assert "Action Registry" in text
    assert "LLM 是提议者，不是执行权限持有者" in text
    assert "pytest" in text
    assert "sandbox" in text
    assert "FinalScore = clamp" in text
    assert (
        "| Coverage-aware | 0.22 | 0.18 | 0.15 | 0.05 | 0.05 | "
        "0.15 | 0.10 | 0.05 | 0.05 | 0.05 |"
    ) in text
    assert (
        "| Static-only | 0.00 | 0.25 | 0.45 | 0.10 | 0.05 | "
        "0.00 | 0.00 | 0.10 | 0.05 | 0.05 |"
    ) in text
    assert "没有实际失败测试时" in text
    assert "不把受控 fixture 指标解释为真实模型" in text


def test_case_studies_cover_all_required_terminal_outcomes():
    text = CASE_STUDIES.read_text(encoding="utf-8")

    for marker in [
        "Clean repo：Pluggy",
        "semantic_none_normalization",
        "semantic_parse_port_reflection",
        "环境 blocker：ItsDangerous",
        "未注册动作",
        "非法参数",
        "高风险动作缺少确认",
    ]:
        assert marker in text

    for evidence in [
        "testable_repo.md",
        "top_level_agent_live_smoke.md",
        "patch_strategy_evaluation.md",
        "budget_ablation_evaluation.md",
        "planner_strategy_evaluation.json",
    ]:
        assert evidence in text

    assert "离线受控" in text
    assert "Repair claim | 未声明" in text
    assert "llm_recommended_action_not_registered" in text
    assert "llm_recommended_arguments_rejected" in text
    assert "high_risk_action_requires_confirmation" in text


def test_demo_guide_has_clean_setup_one_click_agent_chat_and_no_key():
    text = DEMO_GUIDE.read_text(encoding="utf-8")

    for command in [
        "py -3.11 -m venv .venv",
        "python -m pip install -r requirements-dev.txt",
        "python -m code_intelligence_agent agent",
        "--execution-profile agent-auto",
        "--planner-mode hybrid",
        "python -m code_intelligence_agent chat-ui",
        "--session outputs_demo/sampleproject_llm/agent_session.json",
        "release_hygiene_audit",
    ]:
        assert command in text

    assert "0:00-1:00" in text
    assert "9:30-10:00" in text
    assert "<your_key>" in text
    assert not re.search(r"\bsk-[A-Za-z0-9._-]{16,}\b", text)


def test_career_pack_uses_traceable_v2_metrics_and_explicit_limits():
    text = CAREER_PACK.read_text(encoding="utf-8")

    for evidence in [
        "20/20 结构化报告",
        "12/20 自动发现测试命令",
        "7/20 真正启动并终止测试进程",
        "14/42",
        "3/9",
        "0.3333/1.0/1.0",
        "completion 0.125 到 1.0",
        "1226 passed",
        "FinalScore = clamp",
        "Rule / LLM / Hybrid",
        "为什么它是 Agent 而不只是工作流",
    ]:
        assert evidence in text

    assert "不作为 live 模型真实仓库修复率" in text
    assert "graph/dynamic 后指标未下降" in text
    assert "LLM Judge 不能决定成功" in text


def test_readme_distinguishes_v2_evidence_from_historical_v1_metrics():
    text = README.read_text(encoding="utf-8")

    assert "## V2 已验证快照" in text
    assert "## 历史 V1/P6 受控证据" in text
    assert "冻结的 V1 历史受控证据" in text
    assert "受控 fixture 不代表 live 模型真实仓库修复率" in text
    assert "本地路径入口支持静态分析，但不等价于完整 GitHub Agent" in text
    for relative in [
        "docs/v2/architecture_and_design.md",
        "docs/v2/phase8_case_studies.md",
        "docs/v2/phase8_demo_guide_cn.md",
        "docs/career/v2_resume_interview_pack_cn.md",
        "docs/career/agent_project_study_interview_guide.md",
    ]:
        assert f"]({relative})" in text
        assert (ROOT / relative).exists()


def test_new_document_local_links_resolve():
    for document in [ARCHITECTURE, CASE_STUDIES, DEMO_GUIDE, CAREER_PACK]:
        for target in _local_markdown_targets(document):
            resolved = (document.parent / target).resolve()
            assert resolved.exists(), f"{document}: missing {target}"


def test_release_tests_use_tracked_compact_evidence_not_local_outputs():
    for relative in [
        "tests/test_readme_showcase_consistency.py",
        "tests/test_v1_goal_completion_audit.py",
    ]:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "release_evidence" in text
        assert 'ROOT / "outputs_smoke"' not in text

    for name in [
        "showcase_report.json",
        "readme_p6_evidence.json",
        "v1_goal_completion_evidence.json",
    ]:
        assert (ROOT / "tests" / "fixtures" / "release_evidence" / name).exists()

    assert PHASE7_REPORT.exists()


def _local_markdown_targets(path: Path) -> list[str]:
    targets: list[str] = []
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
        if "://" in target or target.startswith("#"):
            continue
        clean = target.split("#", 1)[0]
        if clean:
            targets.append(clean)
    return targets
