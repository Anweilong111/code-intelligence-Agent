import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.benchmark_materializer import (
    BenchmarkMaterializer,
)
from code_intelligence_agent.evaluation.benchmark_runner import BenchmarkRunner
from code_intelligence_agent.evaluation.benchmark_source_miner import (
    mine_recipe_sources,
    render_source_mining_markdown,
)
from code_intelligence_agent.evaluation.benchmark_validator import BenchmarkValidator


def test_source_miner_builds_recipe_catalog_and_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        payload = _sources_payload(root)
        report = mine_recipe_sources(
            payload,
            recipes=[
                "missing_len_zero_guard",
                "possible_index_overrun",
                "enumerate_start_zero_counter",
            ],
        )
        report_payload = report.to_dict()
        template = root / "mined_template.json"
        template.write_text(json.dumps(report_payload["template"]), encoding="utf-8")
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert report.source_count == 3
        assert report.recipe_count == 3
        assert report.generated_source_count == 3
        assert report.generated_count == 3
        assert report.rule_counts == {
            "enumerate_start_zero_counter": 1,
            "missing_len_zero_guard": 1,
            "possible_index_overrun": 1,
        }
        assert report.quality_summary["candidate_count"] == 3
        assert report.quality_summary["source_hit_rate"] == 1.0
        assert report.quality_summary["source_sha256_coverage"] == 1.0
        assert report.quality_summary["stable_ref_coverage"] == 1.0
        assert report.quality_summary["license_coverage"] == 1.0
        assert report.quality_summary["ready_for_benchmark"] is True
        assert len(report_payload["source_candidates"]) == 3
        assert report_payload["source_candidates"][0]["license"] == "MIT"
        assert report_payload["quality_summary"]["quality_score"] > 0.80
        assert len(report_payload["catalog"]["candidates"]) == 3
        assert BenchmarkValidator().validate_template(template).is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0


def test_source_miner_markdown_summarizes_matrix():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        report = mine_recipe_sources(
            _sources_payload(root),
            recipes=["missing_len_zero_guard", "possible_index_overrun"],
        )
        markdown = render_source_mining_markdown(report)

        assert "# Benchmark Source Mining" in markdown
        assert "average_mean.py" in markdown
        assert "missing_len_zero_guard" in markdown
        assert "possible_index_overrun" in markdown
        assert "Quality Score" in markdown
        assert "stable_ref=1.000" in markdown


def test_source_miner_cli_writes_outputs():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        sources = root / "sources.json"
        output_json = root / "source_mining.json"
        output_markdown = root / "source_mining.md"
        output_catalog = root / "source_catalog.json"
        output_template = root / "source_template.json"
        output_sources = root / "candidate_sources.json"
        sources.write_text(json.dumps(_sources_payload(root)), encoding="utf-8")

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_source_miner",
                str(sources),
                "--recipe",
                "missing_len_zero_guard",
                "--recipe",
                "possible_index_overrun",
                "--format",
                "markdown",
                "--output-json",
                str(output_json),
                "--output-markdown",
                str(output_markdown),
                "--output-catalog",
                str(output_catalog),
                "--output-template",
                str(output_template),
                "--output-sources",
                str(output_sources),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        report_payload = json.loads(output_json.read_text(encoding="utf-8"))
        candidate_sources = json.loads(output_sources.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert "# Benchmark Source Mining" in completed.stdout
        assert report_payload["generated_count"] == 2
        assert report_payload["quality_summary"]["source_sha256_coverage"] == 1.0
        assert report_payload["quality_summary"]["stable_ref_coverage"] == 1.0
        assert len(json.loads(output_catalog.read_text(encoding="utf-8"))["candidates"]) == 2
        assert len(json.loads(output_template.read_text(encoding="utf-8"))["cases"]) == 2
        assert len(candidate_sources["sources"]) == 2
        assert "possible_index_overrun" in output_markdown.read_text(encoding="utf-8")


def _sources_payload(root: Path) -> dict:
    average = _write_average_mean(root)
    bubble = _write_bubble_sort(root)
    iterator = _write_iterator_average(root)
    return {
        "sources": [
            _source_entry(average, "maths/average_mean.py", "average_mean.py"),
            _source_entry(bubble, "sorts/bubble_sort.py", "bubble_sort.py"),
            _source_entry(iterator, "maths/iterator_average.py", "iterator_average.py"),
        ]
    }


def _source_entry(path: Path, source_path: str, target_path: str) -> dict:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "raw_url": str(path),
        "target_path": target_path,
        "owner": "example",
        "repo": "algorithms",
        "ref": "v1.0.0",
        "source_path": source_path,
        "sha256": digest,
        "license": "MIT",
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


def _write_iterator_average(root: Path) -> Path:
    raw_source = root / "iterator_average.py"
    raw_source.write_text(
        "def iterator_average(iterable):\n"
        "    n = 0\n\n"
        "    def count_items():\n"
        "        nonlocal n\n"
        "        for n, value in enumerate(iterable, start=1):\n"
        "            yield value\n\n"
        "    total = sum(count_items())\n"
        "    return total / n\n",
        encoding="utf-8",
    )
    return raw_source
