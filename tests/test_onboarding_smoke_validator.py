import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.onboarding_smoke_validator import (
    OnboardingSmokeThresholds,
    render_onboarding_smoke_validation_markdown,
    render_onboarding_smoke_suite_markdown,
    validate_onboarding_smoke_manifest,
    validate_onboarding_smoke_report,
)


def test_onboarding_smoke_validator_accepts_passing_report():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        report_path = _write_report(root, _passing_report(root))

        report = validate_onboarding_smoke_report(report_path)
        markdown = render_onboarding_smoke_validation_markdown(report)

        assert report.passed is True
        assert report.summary["benchmarkization_status"] == "benchmark_ready"
        assert report.summary["benchmarkization_stage"] == "complete"
        assert report.summary["benchmarkization_ready"] is True
        assert report.summary["benchmarkization_primary_action_id"] == (
            "publish_benchmark_evidence_bundle"
        )
        assert {check.name for check in report.checks} == {
            "generated_candidates",
            "quality_gate_present",
            "quality_gate_passed",
            "benchmark_run_present",
            "benchmark_cases",
            "benchmark_top1",
            "benchmark_map",
            "benchmark_patch_success_rate",
            "diagnostics_status",
            "required_artifacts",
        }
        assert "Onboarding Smoke Validation" in markdown
        assert "Benchmarkization: `benchmark_ready`" in markdown
        assert "PASS" in markdown


def test_onboarding_smoke_validator_rejects_weak_metrics_and_missing_artifact():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        payload = _passing_report(root)
        payload["benchmark_run"]["summary"]["top1"] = 0.25
        payload["output_paths"]["benchmark_report_json"] = str(root / "missing.json")
        report_path = _write_report(root, payload)

        report = validate_onboarding_smoke_report(report_path)
        checks = {check.name: check for check in report.checks}

        assert report.passed is False
        assert checks["benchmark_top1"].passed is False
        assert checks["required_artifacts"].passed is False
        assert "benchmark_report_json" in checks["required_artifacts"].details[0]


def test_onboarding_smoke_validator_rejects_diagnostics_fail_by_default():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        payload = _passing_report(root)
        payload["diagnostics"]["headline"]["status"] = "fail"
        report_path = _write_report(root, payload)

        report = validate_onboarding_smoke_report(report_path)
        permissive = validate_onboarding_smoke_report(
            report_path,
            thresholds=OnboardingSmokeThresholds(
                allowed_diagnostics_statuses=("pass", "warning", "fail"),
            ),
        )

        assert report.passed is False
        assert permissive.passed is True


def test_onboarding_smoke_validator_cli_writes_reports_and_returns_nonzero():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        payload = _passing_report(root)
        payload["quality_gate"]["passed"] = False
        report_path = _write_report(root, payload)
        output_json = root / "validation.json"
        output_markdown = root / "validation.md"

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.onboarding_smoke_validator",
                str(report_path),
                "--output-json",
                str(output_json),
                "--output-markdown",
                str(output_markdown),
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        saved = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 1
        assert saved["passed"] is False
        assert output_markdown.exists()
        assert "quality_gate_passed" in completed.stdout


def test_onboarding_smoke_manifest_summarizes_multiple_reports():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        passing = root / "passing"
        failing = root / "failing"
        passing.mkdir()
        failing.mkdir()
        passing_report = _write_report(passing, _passing_report(passing))
        failing_payload = _passing_report(failing)
        failing_payload["benchmark_run"]["summary"]["patch_success_rate"] = 0.1
        failing_report = _write_report(failing, failing_payload)
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "two_repo_smoke",
                    "reports": [
                        {"name": "passing_repo", "report_path": str(passing_report)},
                        {"name": "failing_repo", "report_path": str(failing_report)},
                    ],
                }
            ),
            encoding="utf-8",
        )

        suite = validate_onboarding_smoke_manifest(manifest)
        markdown = render_onboarding_smoke_suite_markdown(suite)

        assert suite.passed is False
        assert suite.summary["report_count"] == 2
        assert suite.summary["passed_count"] == 1
        assert suite.summary["pass_rate"] == 0.5
        assert suite.summary["generated_candidates"] == 2
        assert suite.summary["diagnostics_status_counts"] == {"warning": 2}
        assert suite.summary["benchmarkization_ready_count"] == 2
        assert suite.summary["benchmarkization_status_counts"] == {
            "benchmark_ready": 2
        }
        assert suite.summary["benchmarkization_stage_counts"] == {"complete": 2}
        assert suite.summary["benchmarkization_primary_action_counts"] == {
            "publish_benchmark_evidence_bundle": 2
        }
        assert suite.summary["benchmarkization_remediation_plan_count"] == 2
        assert suite.reports[1]["failed_checks"][0]["name"] == (
            "benchmark_patch_success_rate"
        )
        assert "two_repo_smoke" in markdown
        assert "Benchmarkization Statuses: benchmark_ready=2" in markdown
        assert "Benchmarkization Remediation Plans" in markdown
        assert "failing_repo" in markdown


def test_onboarding_smoke_manifest_averages_only_executed_benchmarks():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        benchmark_root = root / "benchmark"
        mining_root = root / "mining"
        benchmark_root.mkdir()
        mining_root.mkdir()
        benchmark_report = _write_report(
            benchmark_root,
            _passing_report(benchmark_root),
        )
        mining_payload = _passing_report(mining_root)
        mining_payload["preset"] = "mining"
        mining_payload["benchmark_run"] = None
        mining_report = _write_report(mining_root, mining_payload)
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "mixed_repo_smoke",
                    "reports": [
                        {
                            "name": "benchmark_repo",
                            "report_path": str(benchmark_report),
                        },
                        {
                            "name": "mining_repo",
                            "report_path": str(mining_report),
                            "thresholds": {
                                "require_benchmark_run": False,
                                "required_artifacts": [
                                    "sources",
                                    "source_mining_json",
                                    "catalog",
                                    "template",
                                    "diagnostics_json",
                                    "diagnostics_markdown",
                                    "quality_gate_json",
                                    "quality_gate_markdown",
                                    "showcase_lite_json",
                                    "showcase_lite_markdown",
                                    "run_config_json",
                                    "run_config_markdown",
                                ],
                            },
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        suite = validate_onboarding_smoke_manifest(manifest)
        markdown = render_onboarding_smoke_suite_markdown(suite)

        assert suite.passed is True
        assert suite.summary["report_count"] == 2
        assert suite.summary["benchmark_report_count"] == 1
        assert suite.summary["benchmark_cases"] == 1
        assert suite.summary["benchmarkization_status_counts"] == {
            "benchmark_ready": 2
        }
        assert suite.summary["average_top1"] == 1.0
        assert suite.summary["average_map"] == 1.0
        assert suite.summary["average_patch_success_rate"] == 1.0
        assert "Benchmark Reports: 1" in markdown


def test_onboarding_smoke_manifest_cli_writes_outputs():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        report_path = _write_report(root, _passing_report(root))
        manifest = root / "manifest.json"
        output_json = root / "suite.json"
        output_markdown = root / "suite.md"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "single_repo_smoke",
                    "reports": [
                        {"name": "repo", "report_path": str(report_path)},
                    ],
                }
            ),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.onboarding_smoke_validator",
                "manifest",
                str(manifest),
                "--output-json",
                str(output_json),
                "--output-markdown",
                str(output_markdown),
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        saved = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert saved["passed"] is True
        assert saved["summary"]["pass_rate"] == 1.0
        assert output_markdown.exists()
        assert "single_repo_smoke" in completed.stdout


def _passing_report(root: Path) -> dict:
    output_paths = _write_required_artifacts(root)
    return {
        "mode": "tree",
        "preset": "smoke",
        "source": "github-tree:example/project@main",
        "generated_candidate_count": 1,
        "output_paths": output_paths,
        "quality_gate": {"passed": True},
        "diagnostics": {
            "headline": {"status": "warning"},
            "summary": {
                "benchmarkization_status": "benchmark_ready",
                "benchmarkization_ready": True,
            },
        },
        "benchmarkization_readiness": {
            "status": "benchmark_ready",
            "stage": "complete",
            "ready": True,
            "remediation_plan": {
                "primary_action_id": "publish_benchmark_evidence_bundle",
                "auto_runnable_action_count": 0,
                "manual_action_count": 2,
                "actions": [
                    {
                        "action_id": "publish_benchmark_evidence_bundle",
                        "stage": "complete",
                    },
                    {
                        "action_id": "scale_to_more_repositories",
                        "stage": "complete",
                    },
                ],
            },
        },
        "benchmark_run": {
            "summary": {
                "case_count": 1,
                "top1": 1.0,
                "map": 1.0,
                "patch_success_rate": 1.0,
            }
        },
    }


def _write_required_artifacts(root: Path) -> dict:
    names = [
        "sources",
        "source_mining_json",
        "catalog",
        "template",
        "diagnostics_json",
        "diagnostics_markdown",
        "quality_gate_json",
        "quality_gate_markdown",
        "showcase_lite_json",
        "showcase_lite_markdown",
        "run_config_json",
        "run_config_markdown",
        "benchmark_manifest",
        "benchmark_report_json",
        "benchmark_report_markdown",
        "benchmarkization_remediation_plan_json",
        "benchmarkization_remediation_plan_markdown",
    ]
    output_paths = {}
    for name in names:
        suffix = ".md" if name.endswith("markdown") else ".json"
        path = root / f"{name}{suffix}"
        path.write_text("{}\n", encoding="utf-8")
        output_paths[name] = str(path)
    return output_paths


def _write_report(root: Path, payload: dict) -> Path:
    report_path = root / "onboarding_report.json"
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    return report_path
