import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from code_intelligence_agent.evaluation.benchmark_materializer import BenchmarkMaterializer
from code_intelligence_agent.evaluation.benchmark_validator import BenchmarkValidator


def test_validator_accepts_mutated_raw_template_and_generated_manifest():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = root / "raw_sample.py"
        raw_source.write_text(
            "def shift_left(values):\n"
            "    for i in range(len(values) - 1):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n",
            encoding="utf-8",
        )
        template = root / "template.json"
        template.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "name": "valid_mutated_case",
                            "repo_path": "repo",
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
                                "expected_rule_ids": ["possible_index_overrun"],
                                "failing_tests": ["test_shift_left"],
                                "passed_tests": [],
                                "test_args": [],
                                "metadata": {"source": "github_raw_mutation"},
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        output = root / "generated"

        template_report = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(template, output)
        manifest_report = BenchmarkValidator().validate_manifest(manifest)
        manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
        metadata = manifest_data["cases"][0]["metadata"]

        assert template_report.is_valid
        assert manifest_report.is_valid
        assert metadata["source_files"][0]["target_path"] == "sample.py"
        assert metadata["materialized_mutations"][0]["target_path"] == "sample.py"


def test_validator_rejects_mutation_target_without_source():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        template = root / "template.json"
        template.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "name": "invalid_mutation_target",
                            "repo_path": "repo",
                            "sources": [
                                {
                                    "raw_url": str(root / "raw.py"),
                                    "target_path": "sample.py",
                                }
                            ],
                            "mutations": [
                                {
                                    "target_path": "other.py",
                                    "find": "a",
                                    "replace": "b",
                                }
                            ],
                            "files": [],
                            "benchmark": {
                                "buggy_functions": ["shift_left"],
                                "expected_rule_ids": ["possible_index_overrun"],
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        report = BenchmarkValidator().validate_template(template)

        assert not report.is_valid
        assert any(
            issue.location.endswith("mutations[0].target_path")
            and "fetched source" in issue.message
            for issue in report.errors
        )


def test_validator_cli_returns_nonzero_for_invalid_manifest():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "name": "missing_repo",
                            "repo_path": "does_not_exist",
                            "buggy_functions": ["x"],
                            "expected_rule_ids": ["rule"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_validator",
                str(manifest),
                "--target",
                "manifest",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 1
        assert "Repository path does not exist" in result.stdout


def test_repository_github_mutation_template_is_valid():
    template_path = Path("datasets/github_cases/mutation_templates.example.json")
    report = BenchmarkValidator().validate_template(
        template_path
    )
    data = json.loads(template_path.read_text(encoding="utf-8"))
    bug_types = {
        case["benchmark"]["metadata"]["bug_type"]
        for case in data["cases"]
    }
    expected_rules = {
        rule
        for case in data["cases"]
        for rule in case["benchmark"]["expected_rule_ids"]
    }
    source_groups = {
        case["benchmark"]["metadata"].get("upstream")
        for case in data["cases"]
    }
    rule_counts = Counter(
        rule
        for case in data["cases"]
        for rule in case["benchmark"]["expected_rule_ids"]
    )

    assert report.is_valid
    assert report.errors == []
    assert report.warnings == []
    cross_file_cases = [
        case
        for case in data["cases"]
        if case["benchmark"]["metadata"].get("cross_file_trace") is True
    ]
    multi_source_cases = [
        case
        for case in data["cases"]
        if case["benchmark"]["metadata"].get("multi_source_raw") is True
    ]

    assert len(data["cases"]) >= 62
    assert source_groups >= {
        "python/cpython",
        "TheAlgorithms/Python",
        "pytest-dev/pluggy",
        "pallets/click",
    }
    assert {
        file["target_path"]
        for case in cross_file_cases
        for file in case.get("files", [])
    } >= {"average_service.py", "test_average_service_zero_guard.py"}
    assert {
        source["target_path"]
        for case in multi_source_cases
        for source in case.get("sources", [])
    } >= {"statistics.py", "bisect.py"}
    assert bug_types == {
        "api misuse",
        "boundary error",
        "condition error",
        "exception handling error",
        "multi bug",
        "off-by-one counting error",
        "state leakage",
        "type error",
        "zero division error",
    }
    assert expected_rules == {
        "always_true_len_check",
        "broad_exception_pass",
        "enumerate_start_zero_counter",
        "inplace_api_return_value",
        "mutable_default_arg",
        "missing_len_zero_guard",
        "possible_index_overrun",
        "stringified_numeric_value",
    }
    assert rule_counts["enumerate_start_zero_counter"] >= 2
    assert rule_counts["missing_len_zero_guard"] >= 2
    assert rule_counts["possible_index_overrun"] >= 2
    assert any(
        case["name"] == "thealgorithms_mean_and_bubble_sort_multi_bug_cross_file"
        and case["benchmark"]["metadata"]["bugs_per_case"] == 2
        and len(case["benchmark"]["buggy_functions"]) == 2
        for case in data["cases"]
    )
    assert any(
        case["name"]
        == "thealgorithms_bubble_sort_and_average_mode_wide_beam_multi_patch"
        and case["benchmark"]["metadata"]["search_pressure"] == "wide_beam_search"
        and case["benchmark"]["metadata"]["expected_min_patch_candidates"] == 4
        for case in data["cases"]
    )
    assert any(
        case["name"]
        == "thealgorithms_bubble_sort_iterative_and_average_mode_wide_beam_multi_patch"
        and case["benchmark"]["metadata"]["search_pressure"] == "wide_beam_search"
        and case["benchmark"]["metadata"]["expected_min_patch_candidates"] == 4
        and case["benchmark"]["metadata"]["expected_multi_patch_bundle_size"] == 2
        for case in data["cases"]
    )
    assert any(
        case["name"] == "click_formatting_join_options_inplace_sort_api_misuse"
        and case["benchmark"]["metadata"]["upstream"] == "pallets/click"
        and case["benchmark"]["buggy_functions"] == ["join_options"]
        for case in data["cases"]
    )
    assert any(
        case["name"] == "click_formatting_wrap_text_stringified_indent_type_error"
        and case["benchmark"]["metadata"]["upstream"] == "pallets/click"
        and case["benchmark"]["buggy_functions"] == ["wrap_text"]
        and case["benchmark"]["expected_rule_ids"] == ["stringified_numeric_value"]
        for case in data["cases"]
    )
    click_write_dl_cases = {
        case["name"]: case
        for case in data["cases"]
        if case["benchmark"]["metadata"].get("upstream") == "pallets/click"
        and case["benchmark"]["buggy_functions"] == ["HelpFormatter.write_dl"]
    }
    assert set(click_write_dl_cases) >= {
        "click_formatting_write_dl_stringified_first_col_type_error",
        "click_formatting_write_dl_inplace_rows_sort_api_misuse",
        "click_formatting_write_dl_inplace_lines_sort_api_misuse",
    }
    assert click_write_dl_cases[
        "click_formatting_write_dl_stringified_first_col_type_error"
    ]["benchmark"]["expected_rule_ids"] == ["stringified_numeric_value"]
    assert click_write_dl_cases[
        "click_formatting_write_dl_inplace_rows_sort_api_misuse"
    ]["benchmark"]["expected_rule_ids"] == ["inplace_api_return_value"]
    assert click_write_dl_cases[
        "click_formatting_write_dl_inplace_lines_sort_api_misuse"
    ]["benchmark"]["expected_rule_ids"] == ["inplace_api_return_value"]
    pluggy_state_cases = {
        case["name"]: case
        for case in data["cases"]
        if case["benchmark"]["metadata"].get("upstream") == "pytest-dev/pluggy"
        and case["benchmark"]["expected_rule_ids"] == ["mutable_default_arg"]
    }
    assert set(pluggy_state_cases) >= {
        "pluggy_tracing_get_mutable_default",
        "pluggy_tracing_setprocessor_mutable_default",
        "pluggy_tracing_sub_get_mutable_default",
    }
    assert pluggy_state_cases[
        "pluggy_tracing_setprocessor_mutable_default"
    ]["benchmark"]["buggy_functions"] == ["TagTracer.setprocessor"]
    assert pluggy_state_cases[
        "pluggy_tracing_sub_get_mutable_default"
    ]["benchmark"]["buggy_functions"] == ["TagTracerSub.get"]
    assert all(
        case["benchmark"]["metadata"]["source"]
        in {"github_raw_mutation", "github_raw_multi_bug_mutation"}
        for case in data["cases"]
    )
