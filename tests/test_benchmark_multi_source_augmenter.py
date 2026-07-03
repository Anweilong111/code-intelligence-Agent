import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.benchmark_cross_file_composer import (
    compose_cross_file_benchmarks,
)
from code_intelligence_agent.evaluation.benchmark_materializer import (
    BenchmarkMaterializer,
)
from code_intelligence_agent.evaluation.benchmark_multi_source_augmenter import (
    augment_template_with_dependency_sources,
    render_multi_source_augmentation_markdown,
)
from code_intelligence_agent.evaluation.benchmark_recipe_generator import (
    generate_benchmark_recipes,
)
from code_intelligence_agent.evaluation.benchmark_runner import BenchmarkRunner
from code_intelligence_agent.evaluation.benchmark_validator import BenchmarkValidator


def test_augmenter_adds_dependency_source_and_runs_benchmark():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source, helper_source = _write_mean_with_dependency(root)
        template_payload = _cross_file_template_payload(raw_source)
        sources_payload = _sources_payload(raw_source, helper_source)

        report = augment_template_with_dependency_sources(
            template_payload,
            sources_payload,
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        template = root / "augmented_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert report.case_count == 1
        assert report.augmented_count == 1
        assert report.rows[0].added_sources == ["number_tools.py"]
        assert report.rows[0].matched_imports == ["number_tools"]
        assert len(template_case["sources"]) == 2
        assert template_case["benchmark"]["metadata"]["multi_source_raw"] is True
        assert template_case["benchmark"]["metadata"]["dependency_source_targets"] == [
            "number_tools.py"
        ]
        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0


def test_augmenter_reports_unresolved_local_import():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source, helper_source = _write_mean_with_dependency(root)
        template_payload = _cross_file_template_payload(raw_source)
        sources_payload = {"sources": [sources_payload_source(raw_source, "average_mean.py")]}

        report = augment_template_with_dependency_sources(
            template_payload,
            sources_payload,
        )
        markdown = render_multi_source_augmentation_markdown(report)

        assert report.augmented_count == 0
        assert report.unchanged_count == 1
        assert report.rows[0].unresolved_imports == ["number_tools"]
        assert "number_tools" in markdown
        assert helper_source.exists()


def test_augmenter_follows_recursive_package_relative_dependencies():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source, helper_source, core_source = _write_package_mean_with_dependencies(
            root
        )
        report = augment_template_with_dependency_sources(
            _package_template_payload(raw_source),
            {
                "sources": [
                    sources_payload_source(raw_source, "pkg/average_mean.py"),
                    sources_payload_source(helper_source, "pkg/number_tools.py"),
                    sources_payload_source(core_source, "pkg/math_core.py"),
                ]
            },
            max_depth=2,
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        template = root / "recursive_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_recursive",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert report.augmented_count == 1
        assert report.rows[0].added_sources == [
            "pkg/number_tools.py",
            "pkg/math_core.py",
        ]
        assert report.rows[0].matched_imports == [
            "pkg.math_core",
            "pkg.number_tools",
        ]
        assert template_case["benchmark"]["metadata"]["dependency_max_depth"] == 2
        assert template_case["benchmark"]["metadata"]["dependency_source_targets"] == [
            "pkg/number_tools.py",
            "pkg/math_core.py",
        ]
        assert len(template_case["sources"]) == 3
        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0


def test_augmenter_indexes_package_init_as_package_module():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        debug_dir = root / "pkg" / "debug"
        datastructures_dir = root / "pkg" / "datastructures"
        debug_dir.mkdir(parents=True)
        datastructures_dir.mkdir(parents=True)
        tbtools_source = debug_dir / "tbtools.py"
        tbtools_source.write_text(
            "from ..utils import cached_property\n\n\n"
            "def render():\n"
            "    line_idx = 0\n"
            "    return line_idx\n",
            encoding="utf-8",
        )
        utils_source = root / "pkg" / "utils.py"
        utils_source.write_text(
            "from .datastructures import Headers\n\n\n"
            "def cached_property(value):\n"
            "    return Headers(value)\n",
            encoding="utf-8",
        )
        datastructures_source = datastructures_dir / "__init__.py"
        datastructures_source.write_text(
            "class Headers:\n"
            "    def __init__(self, value):\n"
            "        self.value = value\n",
            encoding="utf-8",
        )

        report = augment_template_with_dependency_sources(
            _package_init_dependency_template(tbtools_source),
            {
                "sources": [
                    sources_payload_source(tbtools_source, "pkg/debug/tbtools.py"),
                    sources_payload_source(utils_source, "pkg/utils.py"),
                    sources_payload_source(
                        datastructures_source,
                        "pkg/datastructures/__init__.py",
                    ),
                ]
            },
            max_depth=3,
        )

        assert report.augmented_count == 1
        assert report.rows[0].added_sources == [
            "pkg/utils.py",
            "pkg/datastructures/__init__.py",
        ]
        assert report.rows[0].matched_imports == [
            "pkg.datastructures",
            "pkg.utils",
        ]
        assert "pkg.datastructures" not in report.rows[0].unresolved_imports


def test_augmenter_follows_relative_imported_submodule_name():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        package = root / "pkg"
        package.mkdir()
        utils_source = package / "utils.py"
        utils_source.write_text(
            "from . import certs\n\n\n"
            "def where():\n"
            "    return certs.path()\n",
            encoding="utf-8",
        )
        certs_source = package / "certs.py"
        certs_source.write_text(
            "def path():\n"
            "    return \"bundle.pem\"\n",
            encoding="utf-8",
        )

        report = augment_template_with_dependency_sources(
            _submodule_dependency_template(utils_source),
            {
                "sources": [
                    sources_payload_source(utils_source, "pkg/utils.py"),
                    sources_payload_source(certs_source, "pkg/certs.py"),
                ]
            },
            max_depth=1,
        )

        assert report.augmented_count == 1
        assert report.rows[0].added_sources == ["pkg/certs.py"]
        assert "pkg.certs" in report.rows[0].matched_imports
        assert "pkg.certs" not in report.rows[0].unresolved_imports


def test_augmenter_cli_writes_report_and_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source, helper_source = _write_mean_with_dependency(root)
        template = root / "cross_file_template.json"
        sources = root / "sources.json"
        output_json = root / "multi_source.json"
        output_markdown = root / "multi_source.md"
        output_template = root / "multi_source_template.json"
        template.write_text(
            json.dumps(_cross_file_template_payload(raw_source)),
            encoding="utf-8",
        )
        sources.write_text(
            json.dumps(_sources_payload(raw_source, helper_source)),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_multi_source_augmenter",
                str(template),
                str(sources),
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
        template_payload = json.loads(output_template.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert "# Multi-Source Benchmark Augmentation" in completed.stdout
        assert report_payload["augmented_count"] == 1
        assert "number_tools.py" in output_markdown.read_text(encoding="utf-8")
        assert len(template_payload["cases"][0]["sources"]) == 2
        assert BenchmarkValidator().validate_template(output_template).is_valid


def _cross_file_template_payload(raw_source: Path) -> dict:
    recipe_report = generate_benchmark_recipes(
        {
            "sources": [
                sources_payload_source(raw_source, "average_mean.py"),
            ]
        },
        recipe="missing_len_zero_guard",
    )
    composition = compose_cross_file_benchmarks(
        recipe_report.to_dict()["catalog"],
        include_rules=["missing_len_zero_guard"],
    )
    return composition.to_dict()["template"]


def _sources_payload(raw_source: Path, helper_source: Path) -> dict:
    return {
        "sources": [
            sources_payload_source(raw_source, "average_mean.py"),
            sources_payload_source(helper_source, "number_tools.py"),
        ]
    }


def sources_payload_source(raw_source: Path, target_path: str) -> dict:
    return {
        "raw_url": str(raw_source),
        "target_path": target_path,
    }


def _write_mean_with_dependency(root: Path) -> tuple[Path, Path]:
    helper_source = root / "number_tools.py"
    helper_source.write_text(
        "def normalize_values(values):\n"
        "    return list(values)\n",
        encoding="utf-8",
    )
    raw_source = root / "average_mean.py"
    raw_source.write_text(
        "from number_tools import normalize_values\n\n\n"
        "def mean(nums):\n"
        "    nums = normalize_values(nums)\n"
        "    if not nums:\n"
        "        raise ValueError(\"List is empty\")\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
    return raw_source, helper_source


def _write_package_mean_with_dependencies(root: Path) -> tuple[Path, Path, Path]:
    package = root / "pkg_raw"
    package.mkdir()
    core_source = package / "math_core.py"
    core_source.write_text(
        "def coerce_number(value):\n"
        "    return value\n",
        encoding="utf-8",
    )
    helper_source = package / "number_tools.py"
    helper_source.write_text(
        "from .math_core import coerce_number\n\n\n"
        "def normalize_values(values):\n"
        "    return [coerce_number(value) for value in values]\n",
        encoding="utf-8",
    )
    raw_source = package / "average_mean.py"
    raw_source.write_text(
        "from .number_tools import normalize_values\n\n\n"
        "def mean(nums):\n"
        "    nums = normalize_values(nums)\n"
        "    if not nums:\n"
        "        raise ValueError(\"List is empty\")\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
    return raw_source, helper_source, core_source


def _package_template_payload(raw_source: Path) -> dict:
    return {
        "cases": [
            {
                "name": "recursive_package_average_mean",
                "repo_path": "recursive_package_average_mean_repo",
                "sources": [
                    sources_payload_source(raw_source, "pkg/average_mean.py"),
                ],
                "mutations": [
                    {
                        "target_path": "pkg/average_mean.py",
                        "find": (
                            "    if not nums:\n"
                            "        raise ValueError(\"List is empty\")\n"
                            "    return sum(nums) / len(nums)"
                        ),
                        "replace": (
                            "    n = len(nums)\n"
                            "    return sum(nums) / n"
                        ),
                        "count": 1,
                        "description": "Remove package mean's empty-input guard.",
                    }
                ],
                "files": [
                    {
                        "target_path": "service.py",
                        "content": (
                            "from pkg.average_mean import mean\n\n\n"
                            "def compute_average(nums):\n"
                            "    return mean(nums)\n"
                        ),
                    },
                    {
                        "target_path": "test_service.py",
                        "content": (
                            "from service import compute_average\n\n\n"
                            "def test_empty_average_raises_value_error():\n"
                            "    try:\n"
                            "        compute_average([])\n"
                            "    except ValueError:\n"
                            "        return\n"
                            "    except Exception as exc:\n"
                            "        raise AssertionError(\n"
                            "            f'expected ValueError, got {type(exc).__name__}'\n"
                            "        ) from exc\n"
                            "    raise AssertionError('empty input should raise')\n"
                        ),
                    },
                ],
                "benchmark": {
                    "buggy_functions": ["mean"],
                    "expected_rule_ids": ["missing_len_zero_guard"],
                    "failing_tests": ["test_empty_average_raises_value_error"],
                    "passed_tests": [],
                    "test_args": [],
                    "metadata": {
                        "source": "local_recursive_package_dependency",
                        "bug_type": "zero division error",
                    },
                },
            }
        ]
    }


def _package_init_dependency_template(raw_source: Path) -> dict:
    return {
        "cases": [
            {
                "name": "package_init_dependency",
                "repo_path": "package_init_dependency_repo",
                "sources": [
                    sources_payload_source(raw_source, "pkg/debug/tbtools.py"),
                ],
                "mutations": [
                    {
                        "target_path": "pkg/debug/tbtools.py",
                        "find": "    line_idx = 0",
                        "replace": "    line_idx = str(0)",
                        "count": 1,
                        "description": "Stringify numeric value.",
                    }
                ],
                "files": [
                    {
                        "target_path": "test_render.py",
                        "content": (
                            "from pkg.debug.tbtools import render\n\n\n"
                            "def test_render():\n"
                            "    assert render() == 0\n"
                        ),
                    }
                ],
                "benchmark": {
                    "buggy_functions": ["render"],
                    "expected_rule_ids": ["stringified_numeric_value"],
                    "failing_tests": ["test_render"],
                    "passed_tests": [],
                    "test_args": [],
                    "metadata": {"source": "unit"},
                },
            }
        ]
    }


def _submodule_dependency_template(raw_source: Path) -> dict:
    return {
        "cases": [
            {
                "name": "submodule_dependency",
                "repo_path": "submodule_dependency_repo",
                "sources": [
                    sources_payload_source(raw_source, "pkg/utils.py"),
                ],
                "mutations": [
                    {
                        "target_path": "pkg/utils.py",
                        "find": "    return certs.path()",
                        "replace": "    return \"missing\"",
                        "count": 1,
                        "description": "Break submodule call.",
                    }
                ],
                "files": [
                    {
                        "target_path": "test_where.py",
                        "content": (
                            "from pkg.utils import where\n\n\n"
                            "def test_where():\n"
                            "    assert where() == \"bundle.pem\"\n"
                        ),
                    }
                ],
                "benchmark": {
                    "buggy_functions": ["where"],
                    "expected_rule_ids": ["stringified_numeric_value"],
                    "failing_tests": ["test_where"],
                    "passed_tests": [],
                    "test_args": [],
                    "metadata": {
                        "source": "local_submodule_dependency",
                        "bug_type": "dependency import error",
                    },
                },
            }
        ]
    }
