from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE = ROOT / "docs" / "v3" / "v3_architecture_and_agent_design_cn.md"
DEMO = ROOT / "docs" / "v3" / "v3_ten_minute_demo_guide_cn.md"
CAREER = ROOT / "docs" / "career" / "v3_resume_interview_pack_cn.md"
README = ROOT / "README.MD"
DOCUMENTS = (ARCHITECTURE, DEMO, CAREER)


def test_readme_links_the_v3_release_packaging_documents():
    readme = README.read_text(encoding="utf-8")

    assert "docs/v3/v3_architecture_and_agent_design_cn.md" in readme
    assert "docs/v3/v3_ten_minute_demo_guide_cn.md" in readme
    assert "docs/career/v3_resume_interview_pack_cn.md" in readme
    assert "docs/v3/phase7_packaging_verification.md" in readme
    assert "Live LLM/Hybrid repair metrics" in readme
    assert "all 120 trials" in readme


def test_v3_architecture_uses_current_algorithm_and_evidence_boundaries():
    text = ARCHITECTURE.read_text(encoding="utf-8")

    assert text.count("```mermaid") >= 3
    assert "Observe -> Plan -> Act -> Verify -> Reflect -> Replan" in text
    assert "0.225 * SBFLScore" in text
    assert "0.250 * SemanticScore" in text
    assert "141 个候选 profile" in text
    assert "Top-1 | 0.60" in text
    assert "Rule pass@1 | 0/20" in text
    assert "LLM/Hybrid 修复率 | pending" in text
    assert "19/20" in text
    assert "1381 passed, 2 skipped" in text
    assert "不是 Agent repair" in text
    assert "不是 Windows 上的\n容器级安全边界" in text


def test_v3_demo_uses_real_cli_and_preserves_live_trial_gate():
    text = DEMO.read_text(encoding="utf-8")

    assert "python -m code_intelligence_agent v3-release-eval" in text
    assert "--require-offline-pass" in text
    assert "python -m code_intelligence_agent agent" in text
    assert "python -m code_intelligence_agent chat-ui" in text
    assert "python -m code_intelligence_agent v3-repair-eval" in text
    assert "--case-id bugsinpy-pysnooper-3" in text
    assert "--live-model" in text
    assert "119 个 trial" in text
    assert "60 LLM + 60 Hybrid" in text
    assert "只有命令零退出且报告 `status=pass`" in text


def test_v3_career_pack_exposes_resume_text_and_honest_metrics():
    text = CAREER.read_text(encoding="utf-8")

    assert "三条标准版" in text
    assert "偏算法岗位版" in text
    assert "偏大模型 Agent 岗位版" in text
    assert "当前允许写的量化结果" in text
    assert "20 accepted / 5 rejected / 6 repos" in text
    assert "0.60/0.80/1.00" in text
    assert "Rule pass@1 | 0/20" in text
    assert "LLM/Hybrid pass@k | pending" in text
    assert "完成 live 评估后的更新模板" in text
    assert "真实 LLM/Hybrid 评估仍待完成" in text
    assert "不是 Agent repair" in text
    assert "1381 passed, 2 skipped" in text


def test_v3_packaging_markdown_links_resolve_and_files_use_lf():
    for document in DOCUMENTS:
        raw = document.read_bytes()
        text = raw.decode("utf-8")

        assert b"\r\n" not in raw, document
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "#")):
                continue
            relative = target.split("#", 1)[0]
            assert (document.parent / relative).resolve().is_file(), (
                document,
                target,
            )


def test_committed_v3_packaging_verification_hashes_current_artifacts():
    path = ROOT / "docs" / "v3" / "phase7_packaging_verification.json"
    verification = json.loads(path.read_text(encoding="utf-8"))

    assert verification["status"] == "pass"
    assert verification["clean_archive"]["git_metadata_present"] is False
    assert verification["clean_archive"]["top_level_outputs_count"] == 0
    assert verification["clean_archive"]["candidate_file_count"] == 520
    assert verification["clean_archive"]["release_hygiene"]["status"] == "pass"
    assert verification["clean_archive"]["tests"]["passed"] == 33
    assert verification["clean_archive"]["release_cli"]["offline_status"] == "pass"
    for relative_path, expected_hash in verification["artifacts"].items():
        artifact = ROOT / relative_path
        assert artifact.is_file(), relative_path
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == expected_hash
    for relative_path in verification["lf_normalized_files"]:
        assert b"\r\n" not in (ROOT / relative_path).read_bytes()
