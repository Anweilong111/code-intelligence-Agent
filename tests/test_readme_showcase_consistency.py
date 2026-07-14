import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.readme_showcase_sync import (
    METRIC_LABELS,
    extract_readme_showcase_metrics,
    readme_showcase_mismatches,
    showcase_overview_metrics,
    sync_readme_showcase_text,
)


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.MD"
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "release_evidence"
SHOWCASE = FIXTURE_ROOT / "showcase_report.json"
P6_EVIDENCE = FIXTURE_ROOT / "readme_p6_evidence.json"


def test_readme_p6_summary_matches_current_artifacts():
    readme = README.read_text(encoding="utf-8")
    evidence = json.loads(P6_EVIDENCE.read_text(encoding="utf-8"))
    onboarding = evidence["onboarding"]
    repair = evidence["repair"]
    catalog = evidence["catalog"]
    audit = evidence["audit"]
    audit_summary = audit["summary"]
    repair_metrics = repair["metrics_report"]
    catalog_summary = catalog["summary"]

    assert audit["status"] == "pass"
    assert repair["status"] == "pass"
    assert catalog["status"] == "pass"
    assert onboarding["status"] == "pass"
    assert (
        f"| P6 readiness checks | "
        f"{audit_summary['passed_check_count']}/{audit_summary['check_count']} pass |"
        in readme
    )
    assert f"| Real GitHub onboarding cases | {onboarding['case_count']} |" in readme
    assert (
        f"| Onboarding matrix checks | "
        f"{onboarding['passed_check_count']}/{onboarding['check_count']} pass |"
        in readme
    )
    assert f"| Repair/evaluation cases | {repair['case_count']} |" in readme
    assert (
        f"| LLM direct success cases | "
        f"{repair_metrics['llm_direct_success_count']} |"
        in readme
    )
    assert (
        f"| LLM reflection success cases | "
        f"{repair_metrics['llm_reflection_success_count']} |"
        in readme
    )
    assert (
        f"| LLM blocker cases | {repair_metrics['llm_blocker_count']} |"
        in readme
    )
    assert (
        f"| Reflection evidence complete | "
        f"{repair_metrics['llm_reflection_evidence_complete_count']} |"
        in readme
    )
    assert (
        f"| Declared catalog cases matched | "
        f"{catalog_summary['matched_case_count']}/"
        f"{catalog_summary['declared_case_count']} |"
        in readme
    )


def test_readme_links_public_materials_and_states_boundaries():
    readme = README.read_text(encoding="utf-8")

    for relative_path in [
        "RESUME_AGENT_PROJECT.md",
        "INTERVIEW_QA_AGENT_PROJECT.md",
        "docs/showcase/github_release_guide.md",
        "docs/examples/README.md",
        "docs/examples/v1_sample_reports.md",
        "docs/examples/top_level_agent_live_smoke.md",
        "docs/examples/llm_repair_readiness.md",
    ]:
        assert f"]({relative_path})" in readme
        assert (ROOT / relative_path).exists()

    assert "sandbox_pytest_decides_success" in readme
    assert "LLM judge 可以参与候选排序" in readme
    assert "不承诺" in readme
    assert "API key 只能通过环境变量注入" in readme
    assert "Observe -> Plan -> Act -> Verify -> Reflect -> Replan" in readme
    assert "release_hygiene_audit" in readme
    assert "v1_goal_completion_audit" in readme
    assert "LLM judge " + "可以替代 pytest sandbox" not in readme
    assert "不要把 LLM judge 写成最终成功标准" in readme


def test_readme_showcase_sync_updates_stale_metrics_from_fixture():
    showcase = json.loads(SHOWCASE.read_text(encoding="utf-8"))
    expected = showcase_overview_metrics(showcase)
    readme = _showcase_table(expected)
    stale = readme.replace("| Benchmark Cases | 62 |", "| Benchmark Cases | 0 |")

    assert readme_showcase_mismatches(stale, expected) == [
        {
            "metric": "Benchmark Cases",
            "readme": "0",
            "expected": "62",
        }
    ]
    assert sync_readme_showcase_text(stale, expected) == readme
    assert extract_readme_showcase_metrics(readme) == expected


def test_readme_showcase_sync_cli_can_update_file():
    showcase_payload = json.loads(SHOWCASE.read_text(encoding="utf-8"))
    expected = showcase_overview_metrics(showcase_payload)
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        readme_path = root / "README.MD"
        showcase_path = root / "showcase_report.json"
        readme_path.write_text(
            _showcase_table(expected).replace(
                "| Generated Hard Cases | 5 |",
                "| Generated Hard Cases | 0 |",
            ),
            encoding="utf-8",
        )
        showcase_path.write_text(
            json.dumps(showcase_payload),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.readme_showcase_sync",
                str(readme_path),
                str(showcase_path),
                "--in-place",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        output = json.loads(completed.stdout)

        assert completed.returncode == 0
        assert output["changed"] is True
        assert output["mismatch_count"] == 1
        assert extract_readme_showcase_metrics(
            readme_path.read_text(encoding="utf-8")
        ) == expected


def _showcase_table(metrics: dict[str, str]) -> str:
    lines = [
        "# Synthetic README",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for label in METRIC_LABELS:
        lines.append(f"| {label} | {metrics[label]} |")
    return "\n".join(lines) + "\n"
