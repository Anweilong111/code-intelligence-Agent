import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.benchmark_mining import (
    mine_benchmark_template_seeds,
    render_benchmark_mining_markdown,
)
from code_intelligence_agent.evaluation.benchmark_validator import BenchmarkValidator


def test_mines_patch_judge_clusters_into_template_seeds():
    report = mine_benchmark_template_seeds(_suite_payload(), source_path="suite.json")
    payload = report.to_dict()
    seed = payload["template_seeds"][0]
    template_case = seed["template_case"]

    assert report.judged_candidate_count == 1
    assert report.cluster_count == 1
    assert seed["priority"] == "high"
    assert seed["benchmark_focus"] == "judge false-positive hardening"
    assert seed["failure_type"] == "syntax_error"
    assert seed["pattern"] == "capped_by_execution_evidence"
    assert "non-executable repair decoy" == template_case["benchmark"]["metadata"]["bug_type"]
    assert template_case["benchmark"]["metadata"]["seed_status"] == "needs_human_source_selection"
    assert template_case["benchmark"]["expected_rule_ids"] == [
        "minimal_executable_patch_required"
    ]
    assert payload["template_seed_preview"]["cases"][0]["name"].startswith(
        "judge_mining_syntax_error"
    )


def test_benchmark_mining_markdown_summarizes_seed_strategy():
    report = mine_benchmark_template_seeds(_suite_payload(), source_path="suite.json")
    markdown = render_benchmark_mining_markdown(report)

    assert "# Benchmark Mining" in markdown
    assert "Template Seeds: 1" in markdown
    assert "judge false-positive hardening" in markdown
    assert "Select a compact real function" in markdown


def test_benchmark_mining_cli_writes_json_markdown_and_template_seed_files():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        artifact = root / "suite.json"
        output_json = root / "mining.json"
        output_markdown = root / "mining.md"
        output_template = root / "template_seeds.json"
        artifact.write_text(json.dumps(_suite_payload()), encoding="utf-8")

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_mining",
                str(artifact),
                "--format",
                "markdown",
                "--output-json",
                str(output_json),
                "--output-markdown",
                str(output_markdown),
                "--output-template-seeds",
                str(output_template),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 0
        assert "# Benchmark Mining" in completed.stdout
        assert json.loads(output_json.read_text(encoding="utf-8"))[
            "template_seeds"
        ][0]["priority"] == "high"
        assert "judge false-positive hardening" in output_markdown.read_text(
            encoding="utf-8"
        )
        validation = BenchmarkValidator().validate_template(output_template)
        assert validation.is_valid


def _suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 1,
                "top1": 0.0,
                "top3": 0.0,
                "patch_success_rate": 0.0,
            },
            "cases": [
                {
                    "case_name": "cluster_case",
                    "patch_success": False,
                    "beam_search_results": [
                        {
                            "rank": 1,
                            "candidate_id": "bad_patch",
                            "retention_bucket": "hard_failure",
                            "success": False,
                            "failure_type": "syntax_error",
                            "patch_judgment": {
                                "score": 0.98,
                                "calibrated_score": 0.4,
                                "agreement": "judge_more_optimistic",
                                "verdict": "prefer",
                                "calibration_reasons": [
                                    "passed_ratio=0.00",
                                    "failure_type=syntax_error",
                                    "capped_by_execution_evidence=0.40",
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    }
