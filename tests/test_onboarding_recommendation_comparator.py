import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.onboarding_recommendation_comparator import (
    compare_onboarding_recommendation_reports,
    render_onboarding_recommendation_comparison_markdown,
)


def test_comparator_detects_recommendation_improvement():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        baseline_path = root / "baseline_runner.json"
        recommended_path = root / "recommended_runner.json"
        _write_runner_report(
            baseline_path,
            suite_name="github_onboarding_smoke",
            recommendations=1,
            runs=[
                _run(
                    "score_lookup",
                    passed=False,
                    candidates=0,
                    cases=0,
                    patch_success=0.0,
                    outcome="validation_failed",
                )
            ],
        )
        _write_runner_report(
            recommended_path,
            suite_name="github_onboarding_smoke",
            runs=[
                _run(
                    "score_lookup",
                    passed=True,
                    candidates=2,
                    cases=2,
                    patch_success=1.0,
                    outcome="pass",
                )
            ],
        )

        comparison = compare_onboarding_recommendation_reports(
            baseline_path,
            recommended_path,
        )
        markdown = render_onboarding_recommendation_comparison_markdown(comparison)

        assert comparison.passed is True
        assert comparison.summary["candidate_delta"] == 2
        assert comparison.summary["benchmark_case_delta"] == 2
        assert comparison.summary["validation_pass_rate_delta"] == 1.0
        assert comparison.summary["improved_run_count"] == 1
        assert comparison.summary["baseline_manifest_recommendations"] == 1
        assert comparison.run_comparisons[0].change == "improved"
        assert "failed_report_became_passed" in (
            comparison.run_comparisons[0].improvement_reasons
        )
        assert "score_lookup" in markdown
        assert "0 -> 2 (+2)" in markdown


def test_comparator_flags_recommendation_regression():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        baseline_path = root / "baseline_runner.json"
        recommended_path = root / "recommended_runner.json"
        _write_runner_report(
            baseline_path,
            suite_name="github_onboarding_smoke",
            runs=[
                _run(
                    "score_lookup",
                    passed=True,
                    candidates=3,
                    cases=3,
                    patch_success=1.0,
                    outcome="pass",
                )
            ],
        )
        _write_runner_report(
            recommended_path,
            suite_name="github_onboarding_smoke",
            runs=[
                _run(
                    "score_lookup",
                    passed=True,
                    candidates=1,
                    cases=1,
                    patch_success=0.5,
                    outcome="pass",
                )
            ],
        )

        comparison = compare_onboarding_recommendation_reports(
            baseline_path,
            recommended_path,
        )

        assert comparison.passed is False
        assert comparison.summary["candidate_delta"] == -2
        assert comparison.summary["regressed_run_count"] == 1
        assert "generated_candidates_decreased" in comparison.regressions
        assert "score_lookup:benchmark_cases_decreased" in comparison.regressions
        assert comparison.run_comparisons[0].change == "regressed"


def test_comparator_cli_writes_reports():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        baseline_path = root / "baseline_runner.json"
        recommended_path = root / "recommended_runner.json"
        output_json = root / "comparison.json"
        output_markdown = root / "comparison.md"
        _write_runner_report(
            baseline_path,
            suite_name="github_onboarding_smoke",
            recommendations=1,
            runs=[
                _run(
                    "score_lookup",
                    passed=False,
                    candidates=0,
                    cases=0,
                    patch_success=0.0,
                    outcome="validation_failed",
                )
            ],
        )
        _write_runner_report(
            recommended_path,
            suite_name="github_onboarding_smoke",
            runs=[
                _run(
                    "score_lookup",
                    passed=True,
                    candidates=1,
                    cases=1,
                    patch_success=1.0,
                    outcome="pass",
                )
            ],
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.onboarding_recommendation_comparator",
                str(baseline_path),
                str(recommended_path),
                "--format",
                "json",
                "--output-json",
                str(output_json),
                "--output-markdown",
                str(output_markdown),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        saved = json.loads(output_json.read_text(encoding="utf-8"))
        markdown = output_markdown.read_text(encoding="utf-8")

        assert completed.returncode == 0
        assert saved["passed"] is True
        assert saved["summary"]["candidate_delta"] == 1
        assert "Onboarding Recommendation Comparison" in markdown
        assert "score_lookup" in completed.stdout


def test_comparator_separates_missing_and_added_runs_from_regressions():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        baseline_path = root / "baseline_runner.json"
        recommended_path = root / "recommended_runner.json"
        _write_runner_report(
            baseline_path,
            suite_name="github_onboarding_smoke",
            runs=[
                _run(
                    "baseline_only",
                    passed=True,
                    candidates=1,
                    cases=1,
                    patch_success=1.0,
                    outcome="pass",
                )
            ],
        )
        _write_runner_report(
            recommended_path,
            suite_name="github_onboarding_smoke",
            runs=[
                _run(
                    "recommended_only",
                    passed=True,
                    candidates=1,
                    cases=1,
                    patch_success=1.0,
                    outcome="pass",
                )
            ],
        )

        comparison = compare_onboarding_recommendation_reports(
            baseline_path,
            recommended_path,
        )

        assert comparison.passed is False
        assert comparison.summary["missing_run_count"] == 1
        assert comparison.summary["added_run_count"] == 1
        assert comparison.summary["regressed_run_count"] == 0
        assert comparison.summary["improved_run_count"] == 0
        assert comparison.summary["unchanged_run_count"] == 0
        assert "baseline_only:missing_in_recommended_report" in comparison.regressions


def _run(
    name: str,
    *,
    passed: bool,
    candidates: int,
    cases: int,
    patch_success: float,
    outcome: str,
) -> dict:
    return {
        "name": name,
        "passed": passed,
        "generated_candidates": candidates,
        "benchmark_cases": cases,
        "top1": patch_success,
        "map": patch_success,
        "patch_success_rate": patch_success,
        "diagnostics_status": "pass" if passed else "warning",
        "outcome": outcome,
    }


def _write_runner_report(
    path: Path,
    *,
    suite_name: str,
    runs: list[dict],
    recommendations: int = 0,
) -> None:
    passed_count = sum(1 for run in runs if run["passed"])
    payload = {
        "manifest_path": str(path.parent / "manifest.json"),
        "output_dir": str(path.parent),
        "suite_name": suite_name,
        "passed": passed_count == len(runs) and bool(runs),
        "summary": {
            "run_count": len(runs),
            "completed_count": len(runs),
            "failed_count": 0,
            "fallback_attempted_count": 0,
            "fallback_improved_count": 0,
            "fallback_recovered_count": recommendations,
            "validation_passed": passed_count == len(runs) and bool(runs),
            "validation_report_count": len(runs),
            "validation_pass_rate": round(passed_count / len(runs), 6)
            if runs
            else 0.0,
            "generated_candidates": sum(run["generated_candidates"] for run in runs),
            "benchmark_cases": sum(run["benchmark_cases"] for run in runs),
            "gap_status": "pass" if passed_count == len(runs) else "fail",
            "gap_action_count": recommendations,
            "manifest_recommendation_count": recommendations,
        },
        "gap_summary": {
            "headline": {
                "status": "pass" if passed_count == len(runs) else "fail",
                "run_count": len(runs),
                "fallback_recovered_runs": recommendations,
            },
            "run_outcomes": [
                {
                    "name": run["name"],
                    "outcome": run["outcome"],
                    "generated_candidates": run["generated_candidates"],
                    "benchmark_cases": run["benchmark_cases"],
                    "fallback_reason": "low_generated_candidates"
                    if recommendations
                    else None,
                }
                for run in runs
            ],
            "manifest_recommendations": [
                {"name": run["name"], "candidate_delta": run["generated_candidates"]}
                for run in runs[:recommendations]
            ],
        },
        "runs": [
            {
                "name": run["name"],
                "mode": "from-discovery",
                "passed": run["passed"],
                "fallback_reason": "low_generated_candidates"
                if recommendations
                else None,
            }
            for run in runs
        ],
        "suite_validation": {
            "suite_name": suite_name,
            "passed": passed_count == len(runs) and bool(runs),
            "summary": {
                "report_count": len(runs),
                "passed_count": passed_count,
                "failed_count": len(runs) - passed_count,
                "pass_rate": round(passed_count / len(runs), 6) if runs else 0.0,
                "generated_candidates": sum(
                    run["generated_candidates"] for run in runs
                ),
                "benchmark_cases": sum(run["benchmark_cases"] for run in runs),
            },
            "reports": [
                {
                    "name": run["name"],
                    "passed": run["passed"],
                    "summary": {
                        "source": "synthetic",
                        "generated_candidates": run["generated_candidates"],
                        "benchmark_cases": run["benchmark_cases"],
                        "top1": run["top1"],
                        "map": run["map"],
                        "patch_success_rate": run["patch_success_rate"],
                        "diagnostics_status": run["diagnostics_status"],
                    },
                    "failed_checks": []
                    if run["passed"]
                    else [{"name": "generated_candidates"}],
                }
                for run in runs
            ],
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
