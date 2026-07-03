import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.benchmark_cross_file_composer import (
    compose_cross_file_benchmarks,
    render_cross_file_composition_markdown,
)
from code_intelligence_agent.evaluation.benchmark_materializer import (
    BenchmarkMaterializer,
)
from code_intelligence_agent.evaluation.benchmark_recipe_generator import (
    generate_benchmark_recipes,
)
from code_intelligence_agent.evaluation.benchmark_runner import BenchmarkRunner
from code_intelligence_agent.evaluation.benchmark_validator import BenchmarkValidator


def test_composer_creates_runnable_cross_file_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        catalog_payload = _catalog_from_recipe(
            raw_source,
            target_path="average_mean.py",
            recipe="missing_len_zero_guard",
        )

        report = compose_cross_file_benchmarks(
            catalog_payload,
            include_rules=["missing_len_zero_guard"],
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        service_file = template_case["files"][0]
        test_file = template_case["files"][1]
        template = root / "cross_file_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert report.candidate_count == 1
        assert report.composed_count == 1
        assert template_case["name"].startswith("cross_file_")
        assert service_file["target_path"].endswith("_service.py")
        assert "def call_mean" in service_file["content"]
        assert "from average_mean import mean" not in test_file["content"]
        assert "as mean" in test_file["content"]
        assert template_case["benchmark"]["metadata"]["cross_file_trace"] is True
        assert template_case["benchmark"]["metadata"]["wrapped_function"] == "mean"
        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0


def test_composer_creates_runnable_multi_hop_cross_file_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        catalog_payload = _catalog_from_recipe(
            raw_source,
            target_path="average_mean.py",
            recipe="missing_len_zero_guard",
        )

        report = compose_cross_file_benchmarks(
            catalog_payload,
            include_rules=["missing_len_zero_guard"],
            wrapper_depth=2,
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        template = root / "multi_hop_cross_file_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_multi_hop",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        details = benchmark_report.cases[0].localization_details[0]
        metadata = template_case["benchmark"]["metadata"]
        wrapper_targets = metadata["wrapper_targets"]
        wrapper_modules = metadata["wrapper_modules"]

        assert report.candidate_count == 1
        assert report.composed_count == 1
        assert report.rows[0].wrapper_depth == 2
        assert report.rows[0].wrapper_targets == wrapper_targets
        assert wrapper_targets[0].endswith("_service.py")
        assert wrapper_targets[1].endswith("_service_hop2.py")
        assert len(template_case["files"]) == 3
        assert metadata["wrapper_depth"] == 2
        assert metadata["wrapper_functions"] == [
            "call_mean",
            "call_mean_hop2",
        ]
        assert (
            f"from {wrapper_modules[1]} import call_mean_hop2"
            in template_case["files"][0]["content"]
        )
        assert "from average_mean import mean" in template_case["files"][1]["content"]
        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert details["call_chain"] == [
            "test_mean_empty_input_raises_valueerror",
            "call_mean",
            "call_mean_hop2",
            "mean",
        ]
        assert details["call_chain_length"] == 3
        assert details["graph_components"]["caller_impact"] > 0.0


def test_composer_skips_unsupported_dotted_buggy_function():
    catalog_payload = {
        "candidates": [
            {
                "id": "dotted_function",
                "rule_ids": ["enumerate_start_zero_counter"],
                "template_case": {
                    "name": "dotted_function_case",
                    "repo_path": "repo",
                    "sources": [
                        {
                            "raw_url": "sample.py",
                            "target_path": "sample.py",
                        }
                    ],
                    "mutations": [
                        {
                            "target_path": "sample.py",
                            "find": "a",
                            "replace": "b",
                        }
                    ],
                    "files": [
                        {
                            "target_path": "test_sample.py",
                            "content": "from sample import outer\n\n\ndef test_outer():\n    outer()\n",
                        }
                    ],
                    "benchmark": {
                        "buggy_functions": ["outer.inner"],
                        "expected_rule_ids": ["enumerate_start_zero_counter"],
                        "failing_tests": ["test_outer"],
                        "passed_tests": [],
                        "test_args": [],
                    },
                },
            }
        ]
    }

    report = compose_cross_file_benchmarks(catalog_payload)
    markdown = render_cross_file_composition_markdown(report)

    assert report.composed_count == 0
    assert report.skipped_count == 1
    assert report.rows[0].reasons == ["requires_simple_buggy_function"]
    assert "requires_simple_buggy_function" in markdown


def test_composer_cli_writes_report_and_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        catalog = root / "catalog.json"
        output_json = root / "composition.json"
        output_markdown = root / "composition.md"
        output_template = root / "cross_file_template.json"
        catalog.write_text(
            json.dumps(
                _catalog_from_recipe(
                    raw_source,
                    target_path="average_mean.py",
                    recipe="missing_len_zero_guard",
                )
            ),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_cross_file_composer",
                str(catalog),
                "--include-rule",
                "missing_len_zero_guard",
                "--max-cases",
                "1",
                "--format",
                "markdown",
                "--output-json",
                str(output_json),
                "--output-markdown",
                str(output_markdown),
                "--output-template",
                str(output_template),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        report_payload = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert "# Cross-File Benchmark Composition" in completed.stdout
        assert report_payload["composed_count"] == 1
        assert "wrapped_test_import" in output_markdown.read_text(encoding="utf-8")
        assert BenchmarkValidator().validate_template(output_template).is_valid


def _catalog_from_recipe(
    raw_source: Path,
    target_path: str,
    recipe: str,
) -> dict:
    report = generate_benchmark_recipes(
        {
            "sources": [
                {
                    "raw_url": str(raw_source),
                    "target_path": target_path,
                }
            ]
        },
        recipe=recipe,
    )
    return report.to_dict()["catalog"]


def _write_average_mean(root: Path) -> Path:
    raw_source = root / "average_mean.py"
    raw_source.write_text(
        "def mean(nums):\n"
        "    if not nums:\n"
        "        raise ValueError(\"List is empty\")\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
    return raw_source
