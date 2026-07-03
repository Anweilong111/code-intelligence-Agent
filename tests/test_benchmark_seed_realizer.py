import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.benchmark_materializer import (
    BenchmarkMaterializer,
)
from code_intelligence_agent.evaluation.benchmark_runner import BenchmarkRunner
from code_intelligence_agent.evaluation.benchmark_seed_realizer import (
    realize_benchmark_template_seeds,
    render_seed_realization_markdown,
)
from code_intelligence_agent.evaluation.benchmark_validator import BenchmarkValidator


def test_realizes_template_seed_with_catalog_candidate_and_runs_benchmark():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_raw_shift_left(root)
        seed_payload = _seed_payload()
        catalog_payload = _catalog_payload(raw_source)

        report = realize_benchmark_template_seeds(seed_payload, catalog_payload)
        payload = report.to_dict()
        template_case = payload["realized_template"]["cases"][0]

        assert report.seed_count == 1
        assert report.realized_count == 1
        assert report.unmatched_count == 0
        assert payload["realizations"][0]["candidate_id"] == "shift_left_boundary"
        assert template_case["benchmark"]["metadata"]["seed_status"] == (
            "realized_from_catalog"
        )
        assert template_case["benchmark"]["metadata"]["mining_failure_type"] == (
            "syntax_error"
        )
        assert template_case["benchmark"]["metadata"]["realization_score"] == 9.0

        template = root / "realized_template.json"
        template.write_text(
            json.dumps(payload["realized_template"]),
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

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == "possible_index_overrun"


def test_seed_realization_markdown_reports_unmatched_seed():
    report = realize_benchmark_template_seeds(_seed_payload(), {"candidates": []})
    markdown = render_seed_realization_markdown(report)

    assert report.unmatched_count == 1
    assert "# Benchmark Seed Realization" in markdown
    assert "unmatched" in markdown
    assert "No catalog candidate matched mining metadata" in markdown


def test_seed_realization_prefers_provenance_rich_candidate_on_tie():
    low_quality = _candidate(
        "a_low_quality_match",
        include_provenance=False,
    )
    provenance_rich = _candidate(
        "z_provenance_rich_match",
        include_provenance=True,
    )

    report = realize_benchmark_template_seeds(
        _seed_payload(),
        {"candidates": [low_quality, provenance_rich]},
    )
    realization = report.to_dict()["realizations"][0]
    metadata = realization["template_case"]["benchmark"]["metadata"]

    assert realization["candidate_id"] == "z_provenance_rich_match"
    assert metadata["realization_score"] > 10.0
    assert any(
        reason.startswith("provenance_bonus=")
        for reason in metadata["realization_reasons"]
    )
    assert "stable_ref=1.0000" in metadata["realization_reasons"]


def test_seed_realization_does_not_match_on_provenance_only():
    seed = _seed_payload()
    seed["cases"][0]["benchmark"]["metadata"]["mining_failure_type"] = (
        "unmatched_failure_type"
    )
    seed["cases"][0]["benchmark"]["metadata"]["mining_focus"] = "unmatched_focus"
    seed["cases"][0]["benchmark"]["metadata"]["mining_pattern"] = "unmatched_pattern"
    seed["cases"][0]["benchmark"]["expected_rule_ids"] = ["unmatched_rule"]

    report = realize_benchmark_template_seeds(
        seed,
        {"candidates": [_candidate("provenance_only", include_provenance=True)]},
    )

    assert report.realized_count == 0
    assert report.unmatched_count == 1


def test_seed_realization_cli_writes_report_and_realized_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_raw_shift_left(root)
        seeds = root / "template_seeds.json"
        catalog = root / "catalog.json"
        output_json = root / "realization.json"
        output_markdown = root / "realization.md"
        output_template = root / "realized_template.json"
        seeds.write_text(json.dumps(_seed_payload()), encoding="utf-8")
        catalog.write_text(json.dumps(_catalog_payload(raw_source)), encoding="utf-8")

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_seed_realizer",
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
                "--fail-on-unmatched",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 0
        assert "# Benchmark Seed Realization" in completed.stdout
        assert json.loads(output_json.read_text(encoding="utf-8"))[
            "realized_count"
        ] == 1
        assert "shift_left_boundary" in output_markdown.read_text(encoding="utf-8")
        assert BenchmarkValidator().validate_template(output_template).is_valid


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
                            "source": "local_raw_seed_realization",
                            "bug_type": "boundary error",
                        },
                    },
                },
            }
        ]
    }


def _candidate(candidate_id: str, *, include_provenance: bool) -> dict:
    sources = [
        {
            "raw_url": "https://example.invalid/raw_sample.py",
            "target_path": "sample.py",
        }
    ]
    metadata = {
        "source": "github_raw_recipe_generation",
        "bug_type": "boundary error",
    }
    if include_provenance:
        sources[0].update(
            {
                "owner": "example",
                "repo": "project",
                "ref": "v1.0.0",
                "source_path": "src/sample.py",
                "sha256": "a" * 64,
                "license": "MIT",
            }
        )
        metadata.update(
            {
                "upstream": "example/project",
                "upstream_ref": "v1.0.0",
                "upstream_path": "src/sample.py",
                "license": "MIT",
            }
        )
    return {
        "id": candidate_id,
        "failure_types": ["syntax_error"],
        "benchmark_focuses": ["judge false-positive hardening"],
        "patterns": ["capped_by_execution_evidence"],
        "template_case": {
            "name": candidate_id,
            "repo_path": f"{candidate_id}_repo",
            "sources": sources,
            "mutations": [
                {
                    "target_path": "sample.py",
                    "find": "range(len(values) - 1)",
                    "replace": "range(len(values))",
                }
            ],
            "files": [
                {
                    "target_path": "test_sample.py",
                    "content": "from sample import shift_left\n",
                }
            ],
            "benchmark": {
                "buggy_functions": ["shift_left"],
                "expected_rule_ids": ["minimal_executable_patch_required"],
                "failing_tests": ["test_shift_left"],
                "passed_tests": [],
                "test_args": [],
                "metadata": metadata,
            },
        },
    }
