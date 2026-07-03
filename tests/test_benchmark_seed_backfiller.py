import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.benchmark_materializer import (
    BenchmarkMaterializer,
)
from code_intelligence_agent.evaluation.benchmark_runner import BenchmarkRunner
from code_intelligence_agent.evaluation.benchmark_seed_backfiller import (
    backfill_benchmark_template_seeds,
    render_seed_backfill_markdown,
)
from code_intelligence_agent.evaluation.benchmark_validator import BenchmarkValidator


def test_backfiller_completes_seed_and_outputs_runnable_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_raw_shift_left(root)
        report = backfill_benchmark_template_seeds(
            _seed_payload(),
            _catalog_payload(raw_source),
        )
        payload = report.to_dict()
        template = root / "completed_template.json"
        template.write_text(
            json.dumps(payload["completed_template"]),
            encoding="utf-8",
        )
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert report.seed_count == 1
        assert report.realized_count == 1
        assert report.completed_count == 1
        assert report.incomplete_count == 0
        assert report.unmatched_count == 0
        assert payload["completed_template"]["cases"][0]["benchmark"]["metadata"][
            "seed_status"
        ] == "realized_from_catalog"
        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0


def test_backfiller_rejects_placeholder_candidate():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_raw_shift_left(root)
        catalog = _catalog_payload(raw_source)
        catalog["candidates"][0]["template_case"]["mutations"][0]["find"] = (
            "TODO_original_safe_code"
        )
        report = backfill_benchmark_template_seeds(_seed_payload(), catalog)
        markdown = render_seed_backfill_markdown(report)
        row = report.rows[0]

        assert report.realized_count == 1
        assert report.completed_count == 0
        assert report.incomplete_count == 1
        assert row.status == "incomplete"
        assert "unresolved_todo_placeholder" in row.audit_errors
        assert "# Benchmark Seed Backfill" in markdown
        assert "incomplete" in markdown


def test_backfiller_reports_unmatched_seed():
    report = backfill_benchmark_template_seeds(_seed_payload(), {"candidates": []})

    assert report.completed_count == 0
    assert report.unmatched_count == 1
    assert report.rows[0].status == "unmatched"
    assert "unmatched_seed" in report.rows[0].audit_errors


def test_backfiller_cli_writes_completed_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_raw_shift_left(root)
        seeds = root / "template_seeds.json"
        catalog = root / "catalog.json"
        output_json = root / "backfill.json"
        output_markdown = root / "backfill.md"
        output_template = root / "completed_template.json"
        output_realized = root / "realized_template.json"
        seeds.write_text(json.dumps(_seed_payload()), encoding="utf-8")
        catalog.write_text(json.dumps(_catalog_payload(raw_source)), encoding="utf-8")

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_seed_backfiller",
                str(seeds),
                str(catalog),
                "--format",
                "markdown",
                "--output-json",
                str(output_json),
                "--output-markdown",
                str(output_markdown),
                "--output-template",
                str(output_template),
                "--output-realized-template",
                str(output_realized),
                "--fail-on-incomplete",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        report_payload = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert "# Benchmark Seed Backfill" in completed.stdout
        assert report_payload["completed_count"] == 1
        assert "completed" in output_markdown.read_text(encoding="utf-8")
        assert BenchmarkValidator().validate_template(output_template).is_valid
        assert len(json.loads(output_realized.read_text(encoding="utf-8"))["cases"]) == 1


def _write_raw_shift_left(root: Path) -> Path:
    raw_source = root / "raw_sample.py"
    raw_source.write_text(
        "def shift_left(values):\n"
        "    for i in range(len(values) - 1):\n"
        "        values[i] = values[i + 1]\n"
        "    return values\n",
        encoding="utf-8",
    )
    return raw_source


def _seed_payload() -> dict:
    return {
        "cases": [
            {
                "name": "judge_mining_syntax_error_capped_by_execution_evidence",
                "repo_path": "seed_repo",
                "sources": [
                    {
                        "owner": "TODO_owner",
                        "repo": "TODO_repo",
                        "ref": "TODO_ref",
                        "source_path": "TODO/sample.py",
                        "target_path": "sample.py",
                    }
                ],
                "mutations": [
                    {
                        "target_path": "sample.py",
                        "find": "TODO_original_safe_code",
                        "replace": "TODO_buggy_mutation",
                    }
                ],
                "files": [
                    {
                        "target_path": "test_seed.py",
                        "content": "# TODO\n",
                    }
                ],
                "benchmark": {
                    "buggy_functions": ["TODO_function"],
                    "expected_rule_ids": ["minimal_executable_patch_required"],
                    "failing_tests": ["test_seed"],
                    "passed_tests": [],
                    "test_args": [],
                    "metadata": {
                        "source": "github_raw_judge_cluster_seed",
                        "seed_status": "needs_human_source_selection",
                        "mining_priority": "high",
                        "mining_focus": "judge false-positive hardening",
                        "mining_pattern": "capped_by_execution_evidence",
                        "mining_failure_type": "syntax_error",
                        "evidence_examples": ["cluster_case#1:bad_patch"],
                    },
                },
            }
        ]
    }


def _catalog_payload(raw_source: Path) -> dict:
    return {
        "candidates": [
            {
                "id": "shift_left_boundary",
                "failure_types": ["syntax_error"],
                "benchmark_focuses": ["judge false-positive hardening"],
                "patterns": ["capped_by_execution_evidence"],
                "template_case": {
                    "name": "realized_shift_left_boundary",
                    "repo_path": "realized_shift_left_boundary_repo",
                    "sources": [
                        {
                            "raw_url": str(raw_source),
                            "target_path": "sample.py",
                        }
                    ],
                    "mutations": [
                        {
                            "target_path": "sample.py",
                            "find": "range(len(values) - 1)",
                            "replace": "range(len(values))",
                            "count": 1,
                            "description": "Inject off-by-one boundary bug.",
                        }
                    ],
                    "files": [
                        {
                            "target_path": "test_sample.py",
                            "content": (
                                "from sample import shift_left\n\n"
                                "def test_shift_left():\n"
                                "    assert shift_left([1, 2, 3])[:2] == [2, 3]\n"
                            ),
                        }
                    ],
                    "benchmark": {
                        "buggy_functions": ["shift_left"],
                        "expected_rule_ids": ["possible_index_overrun"],
                        "failing_tests": ["test_shift_left"],
                        "passed_tests": [],
                        "test_args": [],
                        "metadata": {
                            "source": "local_raw_seed_backfill",
                            "bug_type": "boundary error",
                        },
                    },
                },
            }
        ]
    }
