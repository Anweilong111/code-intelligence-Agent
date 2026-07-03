import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.github_onboarding_smoke_runner import (
    render_onboarding_smoke_runner_markdown,
    run_onboarding_smoke_suite,
)


def test_runner_executes_multiple_from_discovery_smoke_reports():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        discovery_a = _write_discovery(
            root,
            filename="average_a.py",
            function_name="mean_a",
        )
        discovery_b = _write_discovery(
            root,
            filename="average_b.py",
            function_name="mean_b",
        )
        manifest = root / "runner_manifest.json"
        output_dir = root / "suite_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "two_repo_onboarding_smoke",
                    "defaults": {
                        "mode": "from-discovery",
                        "preset": "smoke",
                        "recipe": ["missing_len_zero_guard"],
                    },
                    "runs": [
                        {"name": "average_a", "discovery": discovery_a.name},
                        {"name": "average_b", "discovery": discovery_b.name},
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = run_onboarding_smoke_suite(manifest, output_dir)
        markdown = render_onboarding_smoke_runner_markdown(report)
        generated_manifest = json.loads(
            Path(report.generated_manifest_path).read_text(encoding="utf-8")
        )

        assert report.passed is True
        assert report.summary["run_count"] == 2
        assert report.summary["completed_count"] == 2
        assert report.summary["failed_count"] == 0
        assert report.summary["generated_candidates"] == 2
        assert report.summary["benchmark_cases"] == 2
        assert report.summary["static_intelligence_run_count"] == 2
        assert report.summary["static_intelligence_analysis_ready_count"] == 2
        assert report.summary["static_intelligence_selected_signal_count"] == 2
        assert report.summary["static_intelligence_total_signal_count"] == 2
        assert report.summary["static_intelligence_status_counts"] == {
            "analysis_ready": 2
        }
        assert report.summary["static_intelligence_rule_counts"] == {
            "missing_len_zero_guard": 2
        }
        assert report.summary["benchmarkization_ready_count"] == 2
        assert report.summary["benchmarkization_status_counts"] == {
            "benchmark_ready": 2
        }
        assert report.summary["benchmarkization_primary_action_counts"] == {
            "publish_benchmark_evidence_bundle": 2
        }
        assert report.summary["benchmarkization_remediation_plan_count"] == 2
        assert report.suite_validation.summary["report_count"] == 2
        assert report.suite_validation.summary["passed_count"] == 2
        assert report.gap_summary["headline"]["status"] == "pass"
        assert report.gap_summary["headline"]["command_failed_runs"] == 0
        assert generated_manifest["suite_name"] == "two_repo_onboarding_smoke"
        assert [item["name"] for item in generated_manifest["reports"]] == [
            "average_a",
            "average_b",
        ]
        assert all(Path(run.report_path).exists() for run in report.runs)
        assert all("--preset" in run.command_args for run in report.runs)
        assert "two_repo_onboarding_smoke" in markdown
        assert "Static Intelligence Analysis Ready Runs: 2" in markdown
        assert "Static Intelligence Rules: missing_len_zero_guard=2" in markdown
        assert "Benchmarkization Statuses: benchmark_ready=2" in markdown
        assert "Benchmarkization Remediation Plans" in markdown
        assert "average_a" in markdown
        assert (output_dir / "onboarding_smoke_suite.json").exists()
        assert (output_dir / "onboarding_smoke_suite.md").exists()
        assert (output_dir / "onboarding_smoke_gaps.json").exists()
        assert (output_dir / "onboarding_smoke_gaps.md").exists()
        assert (output_dir / "onboarding_smoke_recommended_manifest.json").exists()


def test_runner_forwards_repository_test_options_from_manifest():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        discovery = _write_discovery(
            root,
            filename="average_repo_test.py",
            function_name="mean_repo_test",
        )
        repo_root = root / "repo_root"
        repo_root.mkdir()
        manifest = root / "runner_manifest.json"
        output_dir = root / "suite_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "repository_test_option_forwarding",
                    "defaults": {
                        "mode": "from-discovery",
                        "preset": "smoke",
                        "recipe": ["missing_len_zero_guard"],
                        "repository_test_root": str(repo_root),
                        "repository_test_timeout": 17,
                        "repository_test_failure_overlay_candidate_limit": 4,
                        "repository_test_reflection_mode": "none",
                        "repository_test_reflection_rounds": 2,
                        "repository_test_reflection_width": 3,
                        "run_repository_test_environment_setup": True,
                        "run_repository_test_retry": True,
                        "run_repository_test_retry_prerequisites": True,
                        "auto_repository_test_retry": True,
                        "auto_repository_test_retry_max_risk": "medium",
                        "auto_repository_test_retry_allowed_runners": [
                            "pytest",
                            "unittest",
                        ],
                        "repository_test_environment_setup_timeout": 29,
                        "checkout_repository_tests": True,
                        "repository_checkout_timeout": 31,
                        "repository_checkout_depth": 2,
                        "no_repository_test_command": True,
                    },
                    "runs": [
                        {
                            "name": "average_repo_test",
                            "discovery": discovery.name,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = run_onboarding_smoke_suite(manifest, output_dir)
        command_args = report.runs[0].command_args

        assert report.summary["run_count"] == 1
        assert report.runs[0].error is None
        assert "--repository-test-root" in command_args
        assert str(repo_root) in command_args
        assert "--repository-test-timeout" in command_args
        assert "17" in command_args
        assert "--repository-test-failure-overlay-candidate-limit" in command_args
        assert "4" in command_args
        assert "--repository-test-reflection-mode" in command_args
        assert "none" in command_args
        assert "--repository-test-reflection-rounds" in command_args
        assert "2" in command_args
        assert "--repository-test-reflection-width" in command_args
        assert "3" in command_args
        assert "--run-repository-test-environment-setup" in command_args
        assert "--run-repository-test-retry" in command_args
        assert "--run-repository-test-retry-prerequisites" in command_args
        assert "--auto-repository-test-retry" in command_args
        assert "--auto-repository-test-retry-max-risk" in command_args
        assert "medium" in command_args
        assert command_args.count("--auto-repository-test-retry-runner") == 2
        assert "pytest" in command_args
        assert "unittest" in command_args
        assert "--repository-test-environment-setup-timeout" in command_args
        assert "29" in command_args
        assert "--checkout-repository-tests" in command_args
        assert "--repository-checkout-timeout" in command_args
        assert "31" in command_args
        assert "--repository-checkout-depth" in command_args
        assert "--no-repository-test-command" in command_args


def test_runner_cli_writes_machine_readable_report():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        discovery = _write_discovery(
            root,
            filename="average_cli.py",
            function_name="mean_cli",
        )
        manifest = root / "runner_manifest.json"
        output_dir = root / "suite_output"
        output_json = root / "runner.json"
        output_markdown = root / "runner.md"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "cli_onboarding_smoke",
                    "defaults": {
                        "mode": "from-discovery",
                        "preset": "smoke",
                        "recipe": ["missing_len_zero_guard"],
                    },
                    "runs": [{"name": "average_cli", "discovery": discovery.name}],
                }
            ),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.github_onboarding_smoke_runner",
                str(manifest),
                str(output_dir),
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
        assert saved["summary"]["run_count"] == 1
        assert saved["summary"]["gap_status"] == "pass"
        assert saved["suite_validation"]["summary"]["report_count"] == 1
        assert saved["gap_summary"]["headline"]["status"] == "pass"
        assert "cli_onboarding_smoke" in completed.stdout
        assert output_markdown.exists()


def test_runner_gap_summary_groups_validation_failures():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        discovery = _write_discovery(
            root,
            filename="average_threshold.py",
            function_name="mean_threshold",
        )
        manifest = root / "runner_manifest.json"
        output_dir = root / "suite_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "strict_onboarding_smoke",
                    "thresholds": {"min_generated_candidates": 99},
                    "defaults": {
                        "mode": "from-discovery",
                        "preset": "smoke",
                        "recipe": ["missing_len_zero_guard"],
                    },
                    "runs": [
                        {
                            "name": "average_threshold",
                            "discovery": discovery.name,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = run_onboarding_smoke_suite(manifest, output_dir)
        gap_markdown = (output_dir / "onboarding_smoke_gaps.md").read_text(
            encoding="utf-8"
        )

        assert report.passed is False
        assert report.summary["completed_count"] == 1
        assert report.summary["failed_count"] == 0
        assert report.gap_summary["headline"]["status"] == "fail"
        assert report.gap_summary["headline"]["validation_failed_reports"] == 1
        assert report.gap_summary["headline"]["fallback_attempted_runs"] == 1
        assert report.gap_summary["headline"]["fallback_recovered_runs"] == 0
        assert report.gap_summary["validation_failed_check_counts"] == {
            "generated_candidates": 1
        }
        assert report.gap_summary["run_outcomes"][0]["outcome"] == (
            "validation_failed"
        )
        assert "Broaden recipe mining" in report.gap_summary["next_actions"][0]
        assert "generated_candidates=1" in gap_markdown


def test_runner_auto_fallback_recovers_low_candidate_runs():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        discovery = _write_mixed_recipe_discovery(root)
        manifest = root / "runner_manifest.json"
        output_dir = root / "suite_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "low_candidate_fallback_smoke",
                    "thresholds": {"min_generated_candidates": 2},
                    "defaults": {
                        "mode": "from-discovery",
                        "preset": "smoke",
                        "recipe": ["missing_len_zero_guard"],
                    },
                    "runs": [
                        {
                            "name": "mixed_recipes",
                            "discovery": discovery.name,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = run_onboarding_smoke_suite(manifest, output_dir)
        primary_payload = json.loads(
            Path(report.runs[0].primary_report_path).read_text(encoding="utf-8")
        )
        final_payload = json.loads(
            Path(report.runs[0].report_path).read_text(encoding="utf-8")
        )

        assert report.passed is True
        assert report.summary["fallback_attempted_count"] == 1
        assert report.summary["fallback_improved_count"] == 1
        assert report.summary["fallback_recovered_count"] == 1
        assert report.runs[0].fallback_reason == "low_generated_candidates"
        assert report.runs[0].min_generated_candidates == 2
        assert primary_payload["generated_candidate_count"] == 1
        assert final_payload["generated_candidate_count"] >= 2
        assert report.suite_validation.summary["passed_count"] == 1
        assert report.gap_summary["run_outcomes"][0]["outcome"] == (
            "fallback_recovered"
        )
        assert report.gap_summary["run_outcomes"][0][
            "min_generated_candidates"
        ] == 2
        recommendations = report.gap_summary["manifest_recommendations"]
        assert recommendations[0]["name"] == "mixed_recipes"
        assert recommendations[0]["fallback_reason"] == "low_generated_candidates"
        assert recommendations[0]["recommended_fallback"] == {
            "enabled": True,
            "preset": "smoke",
            "max_sources": 50,
            "max_candidates": 20,
        }
        assert recommendations[0]["remove_primary_fields"] == ["recipe"]
        recommended_manifest = json.loads(
            Path(report.recommended_manifest_path).read_text(encoding="utf-8")
        )
        assert recommended_manifest["recommendation_metadata"][
            "applied_recommendation_count"
        ] == 1
        assert recommended_manifest["runs"][0]["fallback"] == {
            "enabled": True,
            "preset": "smoke",
            "max_sources": 50,
            "max_candidates": 20,
        }
        recommended_run = recommended_manifest["runs"][0]
        assert recommended_run["max_sources"] == 50
        assert recommended_run["max_candidates"] == 20
        assert (
            Path(report.recommended_manifest_path).parent
            / recommended_run["discovery"]
        ).resolve() == discovery.resolve()
        assert "recipe" not in recommended_run


def test_runner_auto_fallback_recovers_no_candidate_runs():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        discovery = _write_dict_discovery(root)
        manifest = root / "runner_manifest.json"
        output_dir = root / "suite_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "fallback_onboarding_smoke",
                    "defaults": {
                        "mode": "from-discovery",
                        "preset": "smoke",
                        "recipe": ["missing_len_zero_guard"],
                    },
                    "runs": [
                        {
                            "name": "score_lookup",
                            "discovery": discovery.name,
                            "fallback": {
                                "max_sources": 25,
                                "max_candidates": 15
                            }
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = run_onboarding_smoke_suite(manifest, output_dir)
        primary_payload = json.loads(
            Path(report.runs[0].primary_report_path).read_text(encoding="utf-8")
        )
        final_payload = json.loads(
            Path(report.runs[0].report_path).read_text(encoding="utf-8")
        )
        gap_markdown = (output_dir / "onboarding_smoke_gaps.md").read_text(
            encoding="utf-8"
        )

        assert report.passed is True
        assert report.summary["fallback_attempted_count"] == 1
        assert report.summary["fallback_recovered_count"] == 1
        assert report.summary["gap_status"] == "warning"
        assert report.runs[0].fallback_used is True
        assert primary_payload["generated_candidate_count"] == 0
        assert final_payload["generated_candidate_count"] == 1
        assert final_payload["benchmark_run"]["summary"]["patch_success_rate"] == 1.0
        assert "--recipe" in report.runs[0].primary_command_args
        assert "--recipe" not in report.runs[0].command_args
        assert report.gap_summary["headline"]["fallback_recovered_runs"] == 1
        assert report.gap_summary["run_outcomes"][0]["outcome"] == (
            "fallback_recovered"
        )
        assert report.gap_summary["run_outcomes"][0][
            "primary_generated_candidates"
        ] == 0
        assert report.gap_summary["run_outcomes"][0]["generated_candidates"] == 1
        assert "Promote recovered fallback settings" in (
            report.gap_summary["next_actions"][0]
        )
        assert "fallback_recovered" in gap_markdown
        recommendations = report.gap_summary["manifest_recommendations"]
        assert recommendations[0]["recommended_fallback"] == {
            "enabled": True,
            "preset": "smoke",
            "max_sources": 25,
            "max_candidates": 15,
        }
        assert recommendations[0]["remove_primary_fields"] == ["recipe"]
        assert "Manifest Recommendations" in gap_markdown
        assert "score_lookup" in gap_markdown
        recommended_manifest = json.loads(
            Path(report.recommended_manifest_path).read_text(encoding="utf-8")
        )
        assert recommended_manifest["runs"][0]["fallback"] == {
            "enabled": True,
            "preset": "smoke",
            "max_sources": 25,
            "max_candidates": 15,
        }
        recommended_run = recommended_manifest["runs"][0]
        assert recommended_run["max_sources"] == 25
        assert recommended_run["max_candidates"] == 15
        assert (
            Path(report.recommended_manifest_path).parent
            / recommended_run["discovery"]
        ).resolve() == discovery.resolve()
        assert "recipe" not in recommended_run


def test_runner_records_invalid_run_without_dropping_suite_artifacts():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        manifest = root / "runner_manifest.json"
        output_dir = root / "suite_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "invalid_onboarding_smoke",
                    "runs": [
                        {
                            "name": "missing_discovery",
                            "mode": "from-discovery"
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = run_onboarding_smoke_suite(manifest, output_dir)
        markdown = render_onboarding_smoke_runner_markdown(report)

        assert report.passed is False
        assert report.summary["run_count"] == 1
        assert report.summary["completed_count"] == 0
        assert report.summary["failed_count"] == 1
        assert report.runs[0].passed is False
        assert "requires discovery" in report.runs[0].error
        assert report.gap_summary["headline"]["status"] == "fail"
        assert report.gap_summary["headline"]["command_failed_runs"] == 1
        assert report.gap_summary["command_error_counts"] == {"ValueError": 1}
        assert "Fix manifest entries" in report.gap_summary["next_actions"][0]
        assert report.suite_validation.summary["report_count"] == 0
        assert (output_dir / "onboarding_smoke_manifest.json").exists()
        assert (output_dir / "onboarding_smoke_suite.json").exists()
        assert (output_dir / "onboarding_smoke_gaps.json").exists()
        assert "missing_discovery" in markdown


def _write_discovery(root: Path, *, filename: str, function_name: str) -> Path:
    raw_source = root / filename
    raw_source.write_text(
        f"def {function_name}(nums):\n"
        "    if not nums:\n"
        "        raise ValueError(\"List is empty\")\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
    discovery = root / f"{filename}.discovery.json"
    discovery.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "path": f"maths/{filename}",
                        "raw_url": str(raw_source),
                        "target_path": filename,
                        "owner": "example",
                        "repo": "algorithms",
                        "ref": "v1.0.0",
                        "sha256": hashlib.sha256(raw_source.read_bytes()).hexdigest(),
                        "license": "MIT",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return discovery


def _write_dict_discovery(root: Path) -> Path:
    raw_source = root / "score_lookup.py"
    raw_source.write_text(
        "def score_for(scores, name):\n"
        "    return scores.get(name, 0)\n",
        encoding="utf-8",
    )
    discovery = root / "score_lookup.discovery.json"
    discovery.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "path": "metrics/score_lookup.py",
                        "raw_url": str(raw_source),
                        "target_path": "score_lookup.py",
                        "owner": "example",
                        "repo": "metrics",
                        "ref": "v1.0.0",
                        "sha256": hashlib.sha256(raw_source.read_bytes()).hexdigest(),
                        "license": "MIT",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return discovery


def _write_mixed_recipe_discovery(root: Path) -> Path:
    average_source = root / "average_mean.py"
    average_source.write_text(
        "def mean(nums):\n"
        "    if not nums:\n"
        "        raise ValueError(\"List is empty\")\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
    score_source = root / "score_lookup.py"
    score_source.write_text(
        "def score_for(scores, name):\n"
        "    return scores.get(name, 0)\n",
        encoding="utf-8",
    )
    discovery = root / "mixed_recipes.discovery.json"
    discovery.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "path": "maths/average_mean.py",
                        "raw_url": str(average_source),
                        "target_path": "average_mean.py",
                        "owner": "example",
                        "repo": "mixed",
                        "ref": "v1.0.0",
                        "sha256": hashlib.sha256(
                            average_source.read_bytes()
                        ).hexdigest(),
                        "license": "MIT",
                    },
                    {
                        "path": "metrics/score_lookup.py",
                        "raw_url": str(score_source),
                        "target_path": "score_lookup.py",
                        "owner": "example",
                        "repo": "mixed",
                        "ref": "v1.0.0",
                        "sha256": hashlib.sha256(
                            score_source.read_bytes()
                        ).hexdigest(),
                        "license": "MIT",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return discovery
