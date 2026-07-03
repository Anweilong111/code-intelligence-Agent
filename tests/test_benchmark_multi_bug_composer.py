import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.benchmark_materializer import (
    BenchmarkMaterializer,
)
from code_intelligence_agent.evaluation.benchmark_multi_bug_composer import (
    compose_multi_bug_benchmarks,
    render_multi_bug_composition_markdown,
)
from code_intelligence_agent.evaluation.benchmark_recipe_generator import (
    generate_benchmark_recipes,
)
from code_intelligence_agent.evaluation.benchmark_runner import BenchmarkRunner
from code_intelligence_agent.evaluation.benchmark_validator import BenchmarkValidator


def test_multi_bug_composer_creates_runnable_multi_patch_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        average = _write_average_mean(root)
        bubble = _write_bubble_sort(root)
        catalog_payload = _catalog_payload(average, bubble)

        report = compose_multi_bug_benchmarks(
            catalog_payload,
            include_rules=["missing_len_zero_guard", "possible_index_overrun"],
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        template = root / "multi_bug_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        case_result = benchmark_report.cases[0]

        assert report.candidate_count == 2
        assert report.composed_count == 1
        assert report.rows[0].functions == ["mean", "bubble_sort_recursive"]
        assert report.rows[0].rules == [
            "missing_len_zero_guard",
            "possible_index_overrun",
        ]
        assert template_case["benchmark"]["metadata"]["source"] == (
            "multi_bug_recipe_composition"
        )
        assert template_case["benchmark"]["metadata"]["bugs_per_case"] == 2
        assert template_case["benchmark"]["buggy_functions"] == [
            "mean",
            "bubble_sort_recursive",
        ]
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "missing_len_zero_guard",
            "possible_index_overrun",
        ]
        assert [file["target_path"] for file in template_case["files"]] == [
            "test_mean_zero_guard_bug1.py",
            "test_bubble_sort_recursive_index_bug2.py",
        ]
        assert validation.is_valid
        assert benchmark_report.map == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.multi_patch_success_rate == 1.0
        assert case_result.multi_patch_success is True
        assert case_result.multi_patch_bundle_size == 2
        assert case_result.best_patch_rule_id == (
            "missing_len_zero_guard+possible_index_overrun"
        )
        assert case_result.multi_patch_results[0]["success"] is True


def test_multi_bug_composer_creates_cross_file_multi_hop_multi_patch_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        average = _write_average_mean(root)
        bubble = _write_bubble_sort(root)
        catalog_payload = _catalog_payload(average, bubble)

        report = compose_multi_bug_benchmarks(
            catalog_payload,
            include_rules=["missing_len_zero_guard", "possible_index_overrun"],
            wrapper_depth=2,
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        metadata = template_case["benchmark"]["metadata"]
        template = root / "cross_file_multi_bug_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_cross_file",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        case_result = benchmark_report.cases[0]
        details_by_function = {
            item["function_name"]: item for item in case_result.localization_details
        }

        assert report.composed_count == 1
        assert report.rows[0].wrapper_depth == 2
        assert report.rows[0].wrapper_targets == metadata["wrapper_targets"]
        assert len(metadata["wrapper_specs"]) == 2
        assert len(metadata["wrapper_targets"]) == 4
        assert metadata["cross_file_trace"] is True
        assert metadata["wrapper_depth"] == 2
        assert len(template_case["files"]) == 6
        assert "from average_mean import mean" not in template_case["files"][2][
            "content"
        ]
        assert "from bubble_sort import bubble_sort_recursive" not in template_case[
            "files"
        ][5]["content"]
        assert "as mean" in template_case["files"][2]["content"]
        assert "as bubble_sort_recursive" in template_case["files"][5]["content"]
        assert validation.is_valid
        assert benchmark_report.map == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.multi_patch_success_rate == 1.0
        assert case_result.multi_patch_success is True
        assert details_by_function["mean"]["call_chain"] == [
            "test_mean_empty_input_raises_valueerror",
            "call_mean",
            "call_mean_hop2",
            "mean",
        ]
        assert details_by_function["bubble_sort_recursive"]["call_chain"] == [
            "test_bubble_sort_recursive_does_not_overrun",
            "call_bubble_sort_recursive",
            "call_bubble_sort_recursive_hop2",
            "bubble_sort_recursive",
        ]


def test_multi_bug_composer_ignores_duplicate_function_groups():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        average = _write_average_mean(root)
        first = _catalog_payload(average, _write_bubble_sort(root))["candidates"][0]
        duplicate = json.loads(json.dumps(first))
        duplicate["id"] = "duplicate_mean"

        report = compose_multi_bug_benchmarks(
            {"candidates": [first, duplicate]},
        )
        markdown = render_multi_bug_composition_markdown(report)

        assert report.composed_count == 0
        assert report.skipped_count == 0
        assert report.to_dict()["template"]["cases"] == []
        assert "# Multi-Bug Benchmark Composition" in markdown


def test_multi_bug_composer_cli_writes_report_and_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog = root / "catalog.json"
        output_json = root / "multi_bug_composition.json"
        output_markdown = root / "multi_bug_composition.md"
        output_template = root / "multi_bug_template.json"
        catalog.write_text(
            json.dumps(
                _catalog_payload(
                    _write_average_mean(root),
                    _write_bubble_sort(root),
                )
            ),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_multi_bug_composer",
                str(catalog),
                "--include-rule",
                "missing_len_zero_guard",
                "--include-rule",
                "possible_index_overrun",
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
        assert "# Multi-Bug Benchmark Composition" in completed.stdout
        assert report_payload["composed_count"] == 1
        assert "merged_recipe_cases" in output_markdown.read_text(encoding="utf-8")
        assert BenchmarkValidator().validate_template(output_template).is_valid


def _catalog_payload(average: Path, bubble: Path) -> dict:
    average_report = generate_benchmark_recipes(
        {
            "sources": [
                {
                    "raw_url": str(average),
                    "target_path": "average_mean.py",
                }
            ]
        },
        recipe="missing_len_zero_guard",
    )
    bubble_report = generate_benchmark_recipes(
        {
            "sources": [
                {
                    "raw_url": str(bubble),
                    "target_path": "bubble_sort.py",
                }
            ]
        },
        recipe="possible_index_overrun",
    )
    return {
        "candidates": [
            *average_report.to_dict()["catalog"]["candidates"],
            *bubble_report.to_dict()["catalog"]["candidates"],
        ]
    }


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


def _write_bubble_sort(root: Path) -> Path:
    raw_source = root / "bubble_sort.py"
    raw_source.write_text(
        "def bubble_sort_recursive(collection):\n"
        "    length = len(collection)\n"
        "    for i in range(length - 1):\n"
        "        if collection[i] > collection[i + 1]:\n"
        "            collection[i], collection[i + 1] = collection[i + 1], collection[i]\n"
        "    if length <= 1:\n"
        "        return collection\n"
        "    return bubble_sort_recursive(collection[:-1]) + [collection[-1]]\n",
        encoding="utf-8",
    )
    return raw_source
