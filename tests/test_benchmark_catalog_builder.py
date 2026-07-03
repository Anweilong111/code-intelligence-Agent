import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.benchmark_catalog_builder import (
    build_seed_realization_catalog,
    render_catalog_markdown,
)
from code_intelligence_agent.evaluation.benchmark_materializer import (
    BenchmarkMaterializer,
)
from code_intelligence_agent.evaluation.benchmark_runner import BenchmarkRunner
from code_intelligence_agent.evaluation.benchmark_seed_realizer import (
    realize_benchmark_template_seeds,
)
from code_intelligence_agent.evaluation.benchmark_validator import BenchmarkValidator


def test_builds_seed_realization_catalog_from_template_cases():
    payload = _template_payload("raw_sample.py")

    report = build_seed_realization_catalog(payload, source_path="template.json")
    candidate = report.candidates[0]

    assert report.case_count == 1
    assert report.candidate_count == 1
    assert report.rule_counts == {"possible_index_overrun": 1}
    assert report.bug_type_counts == {"boundary error": 1}
    assert candidate["id"] == "template_shift_left"
    assert candidate["rule_ids"] == ["possible_index_overrun"]
    assert "syntax_error" in candidate["failure_types"]
    assert "runtime_error" in candidate["failure_types"]
    assert "judge false-positive hardening" in candidate["benchmark_focuses"]
    assert "capped_by_execution_evidence" in candidate["patterns"]
    assert candidate["template_case"]["name"] == "template_shift_left"


def test_catalog_builder_infers_dict_missing_key_guard_tags():
    report = build_seed_realization_catalog(
        _dict_template_payload("score_lookup.py"),
        source_path="dict_template.json",
    )
    candidate = report.candidates[0]

    assert report.rule_counts == {"dict_missing_key_guard": 1}
    assert report.bug_type_counts == {"key error": 1}
    assert candidate["rule_ids"] == ["dict_missing_key_guard"]
    assert "key_error" in candidate["failure_types"]
    assert "runtime_error" in candidate["failure_types"]
    assert "data-access guard calibration" in candidate["benchmark_focuses"]
    assert "mapping default semantics" in candidate["benchmark_focuses"]
    assert "failure_type=key_error" in candidate["patterns"]
    assert "missing_mapping_key_default" in candidate["patterns"]


def test_checked_in_github_template_contains_multi_bug_hard_case():
    template_path = Path("datasets/github_cases/mutation_templates.example.json")
    payload = json.loads(template_path.read_text(encoding="utf-8"))
    cases = {case["name"]: case for case in payload["cases"]}

    case = cases["thealgorithms_mean_and_bubble_sort_multi_bug_cross_file"]
    benchmark = case["benchmark"]
    metadata = benchmark["metadata"]
    report = build_seed_realization_catalog(payload, source_path=str(template_path))

    assert len(case["sources"]) == 2
    assert len(case["mutations"]) == 2
    assert benchmark["buggy_functions"] == ["mean", "bubble_sort_recursive"]
    assert benchmark["expected_rule_ids"] == [
        "missing_len_zero_guard",
        "possible_index_overrun",
    ]
    assert metadata["bug_type"] == "multi bug"
    assert metadata["cross_file_trace"] is True
    assert metadata["wrapper_depth"] == 2
    assert len(metadata["wrapper_specs"]) == 2
    assert report.bug_type_counts["multi bug"] >= 1
    assert report.rule_counts["missing_len_zero_guard"] >= 1
    assert report.rule_counts["possible_index_overrun"] >= 1


def test_checked_in_github_template_contains_wide_beam_multi_patch_case():
    template_path = Path("datasets/github_cases/mutation_templates.example.json")
    payload = json.loads(template_path.read_text(encoding="utf-8"))
    cases = {case["name"]: case for case in payload["cases"]}

    recursive_case = cases[
        "thealgorithms_bubble_sort_and_average_mode_wide_beam_multi_patch"
    ]
    iterative_case = cases[
        "thealgorithms_bubble_sort_iterative_and_average_mode_wide_beam_multi_patch"
    ]

    for case, functions in (
        (recursive_case, ["bubble_sort_recursive", "mode"]),
        (iterative_case, ["bubble_sort_iterative", "mode"]),
    ):
        benchmark = case["benchmark"]
        metadata = benchmark["metadata"]
        assert len(case["sources"]) == 2
        assert len(case["mutations"]) == 2
        assert benchmark["buggy_functions"] == functions
        assert benchmark["expected_rule_ids"] == ["possible_index_overrun"]
        assert metadata["bug_type"] == "multi bug"
        assert metadata["search_pressure"] == "wide_beam_search"
        assert metadata["expected_min_patch_candidates"] == 4
        assert metadata["expected_multi_patch_bundle_size"] == 2


def test_checked_in_github_template_contains_balanced_click_and_pluggy_cases():
    template_path = Path("datasets/github_cases/mutation_templates.example.json")
    payload = json.loads(template_path.read_text(encoding="utf-8"))
    cases = {case["name"]: case for case in payload["cases"]}

    join_options_case = cases["click_formatting_join_options_inplace_sort_api_misuse"]
    join_options_benchmark = join_options_case["benchmark"]
    join_options_metadata = join_options_benchmark["metadata"]
    wrap_text_case = cases[
        "click_formatting_wrap_text_stringified_indent_type_error"
    ]
    wrap_text_benchmark = wrap_text_case["benchmark"]
    wrap_text_metadata = wrap_text_benchmark["metadata"]
    write_dl_type_case = cases[
        "click_formatting_write_dl_stringified_first_col_type_error"
    ]
    write_dl_rows_sort_case = cases[
        "click_formatting_write_dl_inplace_rows_sort_api_misuse"
    ]
    write_dl_lines_sort_case = cases[
        "click_formatting_write_dl_inplace_lines_sort_api_misuse"
    ]
    pluggy_setprocessor_case = cases["pluggy_tracing_setprocessor_mutable_default"]
    pluggy_sub_get_case = cases["pluggy_tracing_sub_get_mutable_default"]

    assert join_options_case["sources"][0]["owner"] == "pallets"
    assert join_options_case["sources"][0]["repo"] == "click"
    assert join_options_case["sources"][0]["target_path"] == "click/formatting.py"
    assert join_options_benchmark["buggy_functions"] == ["join_options"]
    assert join_options_benchmark["expected_rule_ids"] == [
        "inplace_api_return_value"
    ]
    assert join_options_metadata["upstream"] == "pallets/click"
    assert join_options_metadata["upstream_ref"] == "8.1.7"
    assert join_options_metadata["license"] == "BSD-3-Clause"

    assert wrap_text_case["sources"][0]["owner"] == "pallets"
    assert wrap_text_case["sources"][0]["repo"] == "click"
    assert wrap_text_benchmark["buggy_functions"] == ["wrap_text"]
    assert wrap_text_benchmark["expected_rule_ids"] == ["stringified_numeric_value"]
    assert wrap_text_metadata["upstream"] == "pallets/click"
    assert wrap_text_metadata["bug_type"] == "type error"

    assert write_dl_type_case["benchmark"]["buggy_functions"] == [
        "HelpFormatter.write_dl"
    ]
    assert write_dl_type_case["benchmark"]["expected_rule_ids"] == [
        "stringified_numeric_value"
    ]
    assert write_dl_rows_sort_case["benchmark"]["expected_rule_ids"] == [
        "inplace_api_return_value"
    ]
    assert write_dl_lines_sort_case["benchmark"]["expected_rule_ids"] == [
        "inplace_api_return_value"
    ]
    assert {
        cases[name]["benchmark"]["metadata"]["upstream"]
        for name in (
            "click_formatting_write_dl_stringified_first_col_type_error",
            "click_formatting_write_dl_inplace_rows_sort_api_misuse",
            "click_formatting_write_dl_inplace_lines_sort_api_misuse",
        )
    } == {"pallets/click"}

    assert pluggy_setprocessor_case["sources"][0]["owner"] == "pytest-dev"
    assert pluggy_setprocessor_case["sources"][0]["repo"] == "pluggy"
    assert pluggy_setprocessor_case["benchmark"]["buggy_functions"] == [
        "TagTracer.setprocessor"
    ]
    assert pluggy_setprocessor_case["benchmark"]["expected_rule_ids"] == [
        "mutable_default_arg"
    ]
    assert pluggy_sub_get_case["benchmark"]["buggy_functions"] == [
        "TagTracerSub.get"
    ]
    assert pluggy_sub_get_case["benchmark"]["expected_rule_ids"] == [
        "mutable_default_arg"
    ]
    assert {
        cases[name]["benchmark"]["metadata"]["upstream"]
        for name in (
            "pluggy_tracing_get_mutable_default",
            "pluggy_tracing_setprocessor_mutable_default",
            "pluggy_tracing_sub_get_mutable_default",
        )
    } == {"pytest-dev/pluggy"}


def test_catalog_builder_output_realizes_seed_and_runs_benchmark():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_raw_shift_left(root)
        template_payload = _template_payload(str(raw_source))
        catalog = build_seed_realization_catalog(template_payload).to_dict()["catalog"]
        realization = realize_benchmark_template_seeds(_seed_payload(), catalog)
        realized_template = realization.to_dict()["realized_template"]

        template = root / "realized_template.json"
        template.write_text(json.dumps(realized_template), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert realization.realized_count == 1
        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0


def test_catalog_builder_cli_writes_report_and_catalog():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_raw_shift_left(root)
        template = root / "template.json"
        output_json = root / "catalog_report.json"
        output_markdown = root / "catalog.md"
        output_catalog = root / "catalog.json"
        template.write_text(
            json.dumps(_template_payload(str(raw_source))),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_catalog_builder",
                str(template),
                "--format",
                "markdown",
                "--output-json",
                str(output_json),
                "--output-markdown",
                str(output_markdown),
                "--output-catalog",
                str(output_catalog),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        report = json.loads(output_json.read_text(encoding="utf-8"))
        catalog = json.loads(output_catalog.read_text(encoding="utf-8"))
        candidate_template = root / "candidate_template.json"
        candidate_template.write_text(
            json.dumps(
                {"cases": [item["template_case"] for item in catalog["candidates"]]}
            ),
            encoding="utf-8",
        )

        assert completed.returncode == 0
        assert "# Seed Realization Catalog" in completed.stdout
        assert report["candidate_count"] == 1
        assert catalog["candidates"][0]["id"] == "template_shift_left"
        assert "possible_index_overrun=1" in output_markdown.read_text(
            encoding="utf-8"
        )
        assert BenchmarkValidator().validate_template(candidate_template).is_valid


def test_catalog_markdown_summarizes_inferred_tags():
    markdown = render_catalog_markdown(
        build_seed_realization_catalog(_template_payload("raw_sample.py"))
    )

    assert "possible_index_overrun=1" in markdown
    assert "boundary error=1" in markdown
    assert "judge false-positive hardening" in markdown
    assert "failure_type=syntax_error" in markdown


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


def _template_payload(raw_source: str) -> dict:
    return {
        "cases": [
            {
                "name": "template_shift_left",
                "repo_path": "template_shift_left_repo",
                "sources": [
                    {
                        "raw_url": raw_source,
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
                        "source": "local_raw_template",
                        "bug_type": "boundary error",
                    },
                },
            }
        ]
    }


def _dict_template_payload(raw_source: str) -> dict:
    return {
        "cases": [
            {
                "name": "template_score_lookup",
                "repo_path": "template_score_lookup_repo",
                "sources": [
                    {
                        "raw_url": raw_source,
                        "target_path": "score_lookup.py",
                    }
                ],
                "mutations": [
                    {
                        "target_path": "score_lookup.py",
                        "find": "return scores.get(name, 0)",
                        "replace": "return scores[name]",
                        "description": "Remove mapping default lookup.",
                    }
                ],
                "files": [
                    {
                        "target_path": "test_score_lookup.py",
                        "content": (
                            "from score_lookup import score_for\n\n"
                            "def test_missing_score_defaults_to_zero():\n"
                            "    assert score_for({}, 'missing') == 0\n"
                        ),
                    }
                ],
                "benchmark": {
                    "buggy_functions": ["score_for"],
                    "expected_rule_ids": ["dict_missing_key_guard"],
                    "failing_tests": ["test_missing_score_defaults_to_zero"],
                    "passed_tests": [],
                    "test_args": [],
                    "metadata": {
                        "source": "local_raw_template",
                        "bug_type": "key error",
                    },
                },
            }
        ]
    }


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
