import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.readme_showcase_sync import (
    extract_readme_showcase_metrics,
    readme_showcase_mismatches,
    showcase_overview_metrics,
    sync_readme_showcase_text,
)


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.MD"
SHOWCASE = (
    ROOT
    / "outputs_smoke"
    / "experiment_suite_62case_showcase_final"
    / "showcase_report.json"
)
RESUME_SHOWCASE = (
    ROOT
    / "outputs_smoke"
    / "experiment_suite_62case_showcase_final"
    / "resume_showcase.md"
)


def test_readme_project_overview_matches_showcase_artifact():
    readme = README.read_text(encoding="utf-8")
    showcase = json.loads(SHOWCASE.read_text(encoding="utf-8"))
    metrics = extract_readme_showcase_metrics(readme)
    expected = showcase_overview_metrics(showcase)

    assert metrics == expected
    assert expected["Program Slice Cases"] == "62"
    assert expected["Slice-grounded Cases"] == "62"
    assert expected["Average Top-1 Slice Support"] == "0.9839"
    assert expected["Generated Diversity-Assisted Successes"] == "1"
    assert expected["Generated Diversity Success Lift"] == "3.0000"
    assert expected["Generated Diversity Success Bonus"] == "0.5900"


def test_readme_project_overview_artifact_links_exist():
    readme = README.read_text(encoding="utf-8")
    for relative_path in [
        "outputs_smoke/experiment_suite_62case_showcase_final/suite.json",
        "outputs_smoke/experiment_suite_62case_showcase_final/suite.md",
        "outputs_smoke/experiment_suite_62case_showcase_final/showcase_report.json",
        "outputs_smoke/experiment_suite_62case_showcase_final/showcase_report.md",
        "outputs_smoke/experiment_suite_62case_showcase_final/resume_showcase.md",
    ]:
        assert f"`{relative_path}`" in readme
        assert (ROOT / relative_path).exists()

    resume = RESUME_SHOWCASE.read_text(encoding="utf-8")
    assert "# Code Intelligence Agent Resume Showcase" in resume
    assert "## Key Metrics" in resume
    assert "## Ablation-Linked Hard Cases" in resume


def test_readme_lists_default_output_agent_acceptance_gates():
    readme = README.read_text(encoding="utf-8")
    gates = [
        (
            "repo_intelligence_agent_cli_default_output_smoke.example.json",
            "outputs_smoke/repo_intelligence_agent_cli_default_output_current",
        ),
        (
            "repo_intelligence_agent_cli_default_output_matrix.example.json",
            "outputs_smoke/repo_intelligence_agent_cli_default_output_matrix_current",
        ),
        (
            "repo_intelligence_agent_cli_default_output_repair_smoke.example.json",
            "outputs_smoke/repo_intelligence_agent_cli_default_output_repair_current",
        ),
        (
            "repo_intelligence_agent_cli_default_output_blocker_matrix.example.json",
            "outputs_smoke/repo_intelligence_agent_cli_default_output_blocker_matrix_current",
        ),
        (
            "repo_intelligence_agent_cli_default_output_acceptance.example.json",
            "outputs_smoke/repo_intelligence_agent_cli_default_output_acceptance_current",
        ),
    ]

    for manifest_name, output_dir in gates:
        manifest_path = ROOT / "datasets" / "github_cases" / manifest_name
        assert manifest_name in readme
        assert output_dir in readme
        assert manifest_path.is_file()
        if "acceptance.example.json" not in manifest_name:
            assert (ROOT / output_dir).is_dir()

    assert "current user-facing\nAgent acceptance set" in readme
    assert "single combined acceptance command" in readme
    assert "patch-validation plus reflection recovery" in readme
    assert "environment/test-command blocker diagnosis" in readme
    assert "output_dir_defaulted" in readme


def test_resume_showcase_ablation_rollups_match_showcase_artifact():
    showcase = json.loads(SHOWCASE.read_text(encoding="utf-8"))
    resume = RESUME_SHOWCASE.read_text(encoding="utf-8")
    link_summary = showcase["generated_hard_case_ablation_link_summary"]

    assert "| Signal | Linked Cases | Component | Variants | Strongest Delta |" in resume
    for row in link_summary["by_signal"]:
        expected = (
            f"| {row['signal']} | {row['linked_cases']} | "
            f"{_join(row['components'])} | {_join(row['variants'])} | "
            f"{row['strongest_delta_metric']}={_fmt(row['strongest_delta'])} |"
        )
        assert expected in resume

    assert (
        "| Component | Linked Cases | Regressions | Signals | Strongest Delta |"
        in resume
    )
    for row in link_summary["by_component"]:
        expected = (
            f"| {row['component']} | {row['linked_cases']} | "
            f"{row['regression_linked_cases']} | "
            f"{_join(row['target_signals'])} | "
            f"{row['strongest_delta_metric']}={_fmt(row['strongest_delta'])} |"
        )
        assert expected in resume


def test_readme_showcase_sync_updates_stale_metrics():
    readme = README.read_text(encoding="utf-8")
    showcase = json.loads(SHOWCASE.read_text(encoding="utf-8"))
    stale = readme.replace("| Benchmark Cases | 62 |", "| Benchmark Cases | 0 |")
    expected = showcase_overview_metrics(showcase)

    assert readme_showcase_mismatches(stale, expected) == [
        {
            "metric": "Benchmark Cases",
            "readme": "0",
            "expected": "62",
        }
    ]
    assert sync_readme_showcase_text(stale, expected) == readme


def test_readme_showcase_sync_cli_can_update_file():
    showcase_payload = json.loads(SHOWCASE.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        readme_path = root / "README.MD"
        showcase_path = root / "showcase_report.json"
        readme_path.write_text(
            README.read_text(encoding="utf-8").replace(
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
        ) == showcase_overview_metrics(showcase_payload)


def _join(items: list[str]) -> str:
    return ", ".join(items)


def _fmt(value: float) -> str:
    return f"{float(value):.4f}"
