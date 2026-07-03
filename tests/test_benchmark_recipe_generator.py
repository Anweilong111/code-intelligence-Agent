import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.benchmark_materializer import (
    BenchmarkMaterializer,
)
from code_intelligence_agent.evaluation.benchmark_recipe_generator import (
    generate_benchmark_recipes,
    render_recipe_generation_markdown,
)
from code_intelligence_agent.evaluation.benchmark_runner import BenchmarkRunner
from code_intelligence_agent.evaluation.benchmark_seed_realizer import (
    realize_benchmark_template_seeds,
)
from code_intelligence_agent.evaluation.benchmark_validator import BenchmarkValidator


def test_recipe_generator_creates_runnable_missing_zero_guard_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        report = generate_benchmark_recipes(_sources_payload(raw_source))
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "missing_len_zero_guard"
        ]
        assert template_case["benchmark"]["metadata"]["recipe"] == (
            "missing_len_zero_guard"
        )
        assert "n = len(nums)" in template_case["mutations"][0]["replace"]
        assert "except ValueError" in template_case["files"][0]["content"]

        template = root / "generated_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
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
        assert benchmark_report.cases[0].best_patch_rule_id == "missing_len_zero_guard"


def test_recipe_generator_creates_runnable_missing_zero_guard_method_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_mean_stats(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="samplepkg/mean_stats.py"),
            recipe="missing_len_zero_guard",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        mutation = template_case["mutations"][0]
        test_content = next(
            file["content"]
            for file in template_case["files"]
            if file["target_path"].startswith("test_")
        )

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["buggy_functions"] == ["MeanStats.mean"]
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "missing_len_zero_guard"
        ]
        assert mutation["find"] == (
            "        if not nums:\n"
            "            raise ValueError(\"List is empty\")\n"
            "        return sum(nums) / len(nums)"
        )
        assert mutation["replace"] == (
            "        n = len(nums)\n"
            "        return sum(nums) / n"
        )
        assert "from samplepkg.mean_stats import MeanStats" in test_content
        assert "MeanStats().mean([])" in test_content
        assert {"target_path": "samplepkg/__init__.py", "content": ""} in (
            template_case["files"]
        )

        template = root / "generated_missing_guard_method_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_missing_guard_method",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == (
            "missing_len_zero_guard"
        )


def test_recipe_generator_creates_runnable_index_overrun_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_bubble_sort(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="bubble_sort.py"),
            recipe="possible_index_overrun",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "possible_index_overrun"
        ]
        assert template_case["benchmark"]["metadata"]["recipe"] == (
            "possible_index_overrun"
        )
        assert template_case["mutations"][0]["find"] == "range(length - 1)"
        assert template_case["mutations"][0]["replace"] == "range(len(collection))"

        template = root / "generated_index_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_index",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == "possible_index_overrun"


def test_recipe_generator_creates_runnable_index_overrun_method_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_successor_window(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="samplepkg/window.py"),
            recipe="possible_index_overrun",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        mutation = template_case["mutations"][0]
        test_content = next(
            file["content"]
            for file in template_case["files"]
            if file["target_path"].startswith("test_")
        )

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["buggy_functions"] == [
            "SuccessorWindow.next_values"
        ]
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "possible_index_overrun"
        ]
        assert mutation["find"] == "range(length - 1)"
        assert mutation["replace"] == "range(len(values))"
        assert "from samplepkg.window import SuccessorWindow" in test_content
        assert "SuccessorWindow().next_values([1, 2, 3])[:2] == [2, 3]" in (
            test_content
        )
        assert {"target_path": "samplepkg/__init__.py", "content": ""} in (
            template_case["files"]
        )

        template = root / "generated_index_method_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_index_method",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == "possible_index_overrun"


def test_recipe_generator_creates_runnable_dict_missing_key_guard_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_score_lookup(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="score_lookup.py"),
            recipe="dict_missing_key_guard",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "dict_missing_key_guard"
        ]
        assert template_case["benchmark"]["metadata"]["recipe"] == (
            "dict_missing_key_guard"
        )
        assert template_case["mutations"][0]["find"] == (
            "    return scores.get(name, 0)"
        )
        assert template_case["mutations"][0]["replace"] == (
            "    return scores[name]"
        )

        template = root / "generated_dict_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_dict",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == (
            "dict_missing_key_guard"
        )


def test_recipe_generator_creates_runnable_dict_missing_key_guard_method_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_score_table(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="samplepkg/scores.py"),
            recipe="dict_missing_key_guard",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        mutation = template_case["mutations"][0]
        test_content = next(
            file["content"]
            for file in template_case["files"]
            if file["target_path"].startswith("test_")
        )

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["buggy_functions"] == [
            "ScoreTable.score_for"
        ]
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "dict_missing_key_guard"
        ]
        assert mutation["find"] == "        return scores.get(name, 0)"
        assert mutation["replace"] == "        return scores[name]"
        assert "from samplepkg.scores import ScoreTable" in test_content
        assert "ScoreTable().score_for({'alice': 3}, 'missing')" in test_content
        assert {"target_path": "samplepkg/__init__.py", "content": ""} in (
            template_case["files"]
        )

        template = root / "generated_dict_method_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_dict_method",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == (
            "dict_missing_key_guard"
        )


def test_recipe_generator_creates_runnable_inplace_api_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_normalizer(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="normalizer.py"),
            recipe="inplace_api_return_value",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "inplace_api_return_value"
        ]
        assert template_case["benchmark"]["metadata"]["recipe"] == (
            "inplace_api_return_value"
        )
        assert template_case["mutations"][0]["find"] == (
            "    values = sorted(values)"
        )
        assert template_case["mutations"][0]["replace"] == (
            "    values = values.sort()"
        )

        template = root / "generated_api_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_api",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == "inplace_api_return_value"


def test_recipe_generator_creates_runnable_inplace_sort_statement_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_sorting_helper(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="sorting_helper.py"),
            recipe="inplace_api_return_value",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["mutations"][0]["find"] == "    values.sort()"
        assert template_case["mutations"][0]["replace"] == (
            "    values = values.sort()"
        )

        template = root / "generated_sort_statement_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_sort_statement",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == "inplace_api_return_value"


def test_recipe_generator_creates_runnable_inplace_api_method_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_sorting_box(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="samplepkg/sorting_box.py"),
            recipe="inplace_api_return_value",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        mutation = template_case["mutations"][0]
        test_content = next(
            file["content"]
            for file in template_case["files"]
            if file["target_path"].startswith("test_")
        )

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["buggy_functions"] == [
            "SortingBox.sort_values"
        ]
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "inplace_api_return_value"
        ]
        assert mutation["find"] == "        values.sort()"
        assert mutation["replace"] == "        values = values.sort()"
        assert "from samplepkg.sorting_box import SortingBox" in test_content
        assert (
            "SortingBox().sort_values([3, 1, 2]) == [1, 2, 3]"
            in test_content
        )
        assert {"target_path": "samplepkg/__init__.py", "content": ""} in (
            template_case["files"]
        )

        template = root / "generated_sort_method_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_sort_method",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == (
            "inplace_api_return_value"
        )


def test_recipe_generator_mines_click_style_join_options_sort_statement():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_join_options(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="formatting.py"),
            recipe="inplace_api_return_value",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]

        assert report.generated_count == 1
        assert template_case["benchmark"]["buggy_functions"] == ["join_options"]
        assert template_case["mutations"][0]["find"] == (
            "    rv.sort(key=lambda x: x[0])"
        )
        assert template_case["mutations"][0]["replace"] == (
            "    rv = rv.sort(key=lambda x: x[0])"
        )

        template = root / "generated_join_options_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_join_options",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == "inplace_api_return_value"


def test_recipe_generator_uses_package_import_for_nested_inplace_api_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_join_options(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="click/formatting.py"),
            recipe="inplace_api_return_value",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        files = {
            file["target_path"]: file["content"]
            for file in template_case["files"]
        }

        assert report.generated_count == 1
        assert "click/__init__.py" in files
        assert "from click.formatting import join_options" in files[
            "test_join_options_api.py"
        ]

        template = root / "generated_package_join_options_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_package_join_options",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == "inplace_api_return_value"


def test_recipe_generator_creates_runnable_stringified_numeric_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_middle_value(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="middle_value.py"),
            recipe="stringified_numeric_value",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "stringified_numeric_value"
        ]
        assert template_case["benchmark"]["metadata"]["recipe"] == (
            "stringified_numeric_value"
        )
        assert template_case["mutations"][0]["find"] == (
            "    index = len(values) // 2\n"
            "    return values[index]"
        )
        assert template_case["mutations"][0]["replace"] == (
            "    index = str(len(values) // 2)\n"
            "    return values[index]"
        )

        template = root / "generated_type_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_type",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == (
            "stringified_numeric_value"
        )


def test_recipe_generator_creates_runnable_stringified_numeric_method_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_window_picker(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="samplepkg/window.py"),
            recipe="stringified_numeric_value",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        mutation = template_case["mutations"][0]
        test_content = next(
            file["content"]
            for file in template_case["files"]
            if file["target_path"].startswith("test_")
        )

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["buggy_functions"] == [
            "WindowPicker.middle"
        ]
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "stringified_numeric_value"
        ]
        assert mutation["find"] == (
            "        index = len(values) // 2\n"
            "        return values[index]"
        )
        assert mutation["replace"] == (
            "        index = str(len(values) // 2)\n"
            "        return values[index]"
        )
        assert "from samplepkg.window import WindowPicker" in test_content
        assert "WindowPicker().middle([1, 2, 3]) == 2" in test_content
        assert {"target_path": "samplepkg/__init__.py", "content": ""} in (
            template_case["files"]
        )

        template = root / "generated_type_method_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_type_method",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == (
            "stringified_numeric_value"
        )


def test_recipe_generator_skips_method_when_init_requires_keyword_only_args():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = root / "debug_frame.py"
        raw_source.write_text(
            "class DebugFrameSummary:\n"
            "    def __init__(self, *, locals, globals):\n"
            "        self.locals = locals\n"
            "        self.globals = globals\n\n"
            "    def render_html(self, values):\n"
            "        index = len(values) // 2\n"
            "        return values[index]\n",
            encoding="utf-8",
        )

        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="samplepkg/debug_frame.py"),
            recipe="stringified_numeric_value",
        )
        result = report.results[0]

        assert report.source_count == 1
        assert report.generated_count == 0
        assert result.status == "skipped"
        assert result.reasons == [
            "no_numeric_assignment_for_stringified_value_mutation"
        ]


def test_recipe_generator_creates_runnable_mutable_default_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_mean_value(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="mean_value.py"),
            recipe="mutable_default_arg",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "mutable_default_arg"
        ]
        assert template_case["benchmark"]["metadata"]["recipe"] == (
            "mutable_default_arg"
        )
        assert "def mean_value(values, _cache=[]):" in (
            template_case["mutations"][0]["replace"]
        )
        assert "_cache.append(list(values))" in template_case["mutations"][0][
            "replace"
        ]

        template = root / "generated_state_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_state",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == "mutable_default_arg"


def test_recipe_generator_creates_runnable_mutable_default_method_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_tag_tracer(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="samplepkg/_tracing.py"),
            recipe="mutable_default_arg",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        mutation = template_case["mutations"][0]
        test_content = next(
            file["content"]
            for file in template_case["files"]
            if file["target_path"].startswith("test_")
        )

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["buggy_functions"] == ["TagTracer.get"]
        assert "def get(self, name: str, _cache=[])" in mutation["replace"]
        assert "_cache.append(name)" in mutation["replace"]
        assert "name = _cache[0]" in mutation["replace"]
        assert "from samplepkg._tracing import TagTracer" in test_content
        assert {"target_path": "samplepkg/__init__.py", "content": ""} in (
            template_case["files"]
        )

        template = root / "generated_method_state_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_method_state",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == "mutable_default_arg"


def test_recipe_generator_import_guard_handles_loaded_package_name_collision():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_tag_tracer(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="pluggy/_tracing.py"),
            recipe="mutable_default_arg",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        test_content = next(
            file["content"]
            for file in template_case["files"]
            if file["target_path"].startswith("test_")
        )

        assert report.generated_count == 1
        assert "__CIA_IMPORT_MODULES = ('pluggy', 'pluggy._tracing')" in test_content
        assert "from pluggy._tracing import TagTracer" in test_content

        template = root / "generated_loaded_package_collision_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_loaded_package_collision",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == "mutable_default_arg"


def test_recipe_generator_creates_runnable_broad_exception_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source),
            recipe="broad_exception_pass",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "broad_exception_pass"
        ]
        assert template_case["benchmark"]["metadata"]["recipe"] == (
            "broad_exception_pass"
        )
        assert "    try:\n" in template_case["mutations"][0]["replace"]
        assert "    except Exception:\n        pass" in template_case["mutations"][0][
            "replace"
        ]

        template = root / "generated_exception_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_exception",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == "broad_exception_pass"


def test_recipe_generator_creates_runnable_broad_exception_method_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_mean_stats(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="samplepkg/mean_stats.py"),
            recipe="broad_exception_pass",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        mutation = template_case["mutations"][0]
        test_content = next(
            file["content"]
            for file in template_case["files"]
            if file["target_path"].startswith("test_")
        )

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["buggy_functions"] == ["MeanStats.mean"]
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "broad_exception_pass"
        ]
        assert "        try:\n" in mutation["replace"]
        assert "        except Exception:\n            pass" in mutation["replace"]
        assert "from samplepkg.mean_stats import MeanStats" in test_content
        assert "MeanStats().mean([])" in test_content
        assert {"target_path": "samplepkg/__init__.py", "content": ""} in (
            template_case["files"]
        )

        template = root / "generated_exception_method_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_exception_method",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == (
            "broad_exception_pass"
        )


def test_recipe_generator_creates_runnable_always_true_len_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source),
            recipe="always_true_len_check",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "always_true_len_check"
        ]
        assert template_case["benchmark"]["metadata"]["recipe"] == (
            "always_true_len_check"
        )
        assert "if len(nums) >= 0:" in template_case["mutations"][0]["replace"]
        assert "else:\n        raise ValueError" in template_case["mutations"][0][
            "replace"
        ]

        template = root / "generated_condition_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_condition",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == "always_true_len_check"


def test_recipe_generator_imports_custom_exception_for_always_true_len_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = root / "views.py"
        raw_source.write_text(
            "class NotFound(Exception):\n"
            "    pass\n\n\n"
            "def display(url):\n"
            "    if not url:\n"
            "        raise NotFound()\n"
            "    return url[0]\n",
            encoding="utf-8",
        )
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="samplepkg/views.py"),
            recipe="always_true_len_check",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        test_content = next(
            file["content"]
            for file in template_case["files"]
            if file["target_path"].startswith("test_")
        )

        assert report.source_count == 1
        assert report.generated_count == 1
        assert "from samplepkg.views import NotFound, display" in test_content
        assert "except NotFound:" in test_content

        template = root / "generated_condition_custom_exception_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_condition_custom_exception",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == "always_true_len_check"


def test_recipe_generator_creates_runnable_always_true_len_method_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_mean_stats(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="samplepkg/mean_stats.py"),
            recipe="always_true_len_check",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        mutation = template_case["mutations"][0]
        test_content = next(
            file["content"]
            for file in template_case["files"]
            if file["target_path"].startswith("test_")
        )

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["buggy_functions"] == ["MeanStats.mean"]
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "always_true_len_check"
        ]
        assert "        if len(nums) >= 0:" in mutation["replace"]
        assert "        else:\n            raise ValueError" in mutation["replace"]
        assert "from samplepkg.mean_stats import MeanStats" in test_content
        assert "MeanStats().mean([])" in test_content
        assert {"target_path": "samplepkg/__init__.py", "content": ""} in (
            template_case["files"]
        )

        template = root / "generated_condition_method_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_condition_method",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == (
            "always_true_len_check"
        )


def test_recipe_generator_always_true_len_method_supplies_required_extra_args():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = root / "models.py"
        raw_source.write_text(
            "class MissingSchema(Exception):\n"
            "    pass\n\n\n"
            "class PreparedRequest:\n"
            "    def prepare_url(self, url, params):\n"
            "        scheme = url\n"
            "        if not scheme:\n"
            "            raise MissingSchema()\n"
            "        return params\n",
            encoding="utf-8",
        )
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="samplepkg/models.py"),
            recipe="always_true_len_check",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        test_content = next(
            file["content"]
            for file in template_case["files"]
            if file["target_path"].startswith("test_")
        )

        assert report.source_count == 1
        assert report.generated_count == 1
        assert (
            "from samplepkg.models import MissingSchema, PreparedRequest"
            in test_content
        )
        assert 'PreparedRequest().prepare_url("", None)' in test_content

        template = root / "generated_condition_method_extra_arg_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_condition_method_extra_arg",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == (
            "always_true_len_check"
        )


def test_recipe_generator_skips_regex_match_object_empty_guard_for_always_true_len():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = root / "util.py"
        raw_source.write_text(
            "class LocalProtocolError(Exception):\n"
            "    pass\n\n\n"
            "def validate(regex, data):\n"
            "    match = regex.fullmatch(data)\n"
            "    if not match:\n"
            "        raise LocalProtocolError(\"malformed\")\n"
            "    return match.groupdict()\n",
            encoding="utf-8",
        )

        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="samplepkg/util.py"),
            recipe="always_true_len_check",
        )

        assert report.source_count == 1
        assert report.generated_count == 0
        assert report.results[0].reasons == [
            "no_empty_guard_with_following_main_logic"
        ]


def test_recipe_generator_skips_method_result_empty_guard_when_object_argument_is_required():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = root / "readers.py"
        raw_source.write_text(
            "class LocalProtocolError(Exception):\n"
            "    pass\n\n\n"
            "def maybe_read(buf):\n"
            "    lines = buf.maybe_extract_lines()\n"
            "    if not lines:\n"
            "        raise LocalProtocolError(\"no line received\")\n"
            "    return lines[0]\n",
            encoding="utf-8",
        )

        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="samplepkg/readers.py"),
            recipe="always_true_len_check",
        )

        assert report.source_count == 1
        assert report.generated_count == 0
        assert report.results[0].reasons == [
            "no_empty_guard_with_following_main_logic"
        ]


def test_recipe_generator_creates_runnable_inverted_empty_guard_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source),
            recipe="inverted_empty_guard",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "inverted_empty_guard"
        ]
        assert template_case["benchmark"]["metadata"]["recipe"] == (
            "inverted_empty_guard"
        )
        assert template_case["mutations"][0]["find"] == "    if not nums:"
        assert template_case["mutations"][0]["replace"] == "    if nums:"
        assert "assert mean([1, 2, 3]) == 2" in template_case["files"][0]["content"]

        template = root / "generated_inverted_guard_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_inverted_guard",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == "inverted_empty_guard"


def test_recipe_generator_creates_runnable_inverted_empty_guard_method_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_mean_stats(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="samplepkg/mean_stats.py"),
            recipe="inverted_empty_guard",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        mutation = template_case["mutations"][0]
        test_content = next(
            file["content"]
            for file in template_case["files"]
            if file["target_path"].startswith("test_")
        )

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["buggy_functions"] == ["MeanStats.mean"]
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "inverted_empty_guard"
        ]
        assert mutation["find"] == "        if not nums:"
        assert mutation["replace"] == "        if nums:"
        assert "from samplepkg.mean_stats import MeanStats" in test_content
        assert "MeanStats().mean([1, 2, 3]) == 2" in test_content
        assert {"target_path": "samplepkg/__init__.py", "content": ""} in (
            template_case["files"]
        )

        template = root / "generated_inverted_guard_method_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_inverted_guard_method",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == (
            "inverted_empty_guard"
        )


def test_recipe_generator_creates_runnable_enumerate_counter_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_iterator_average(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="iterator_average.py"),
            recipe="enumerate_start_zero_counter",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "enumerate_start_zero_counter"
        ]
        assert template_case["benchmark"]["buggy_functions"] == [
            "iterator_average.count_items"
        ]
        assert "enumerate(iterable, start=0)" in template_case["mutations"][0][
            "replace"
        ]

        template = root / "generated_counter_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_counter",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == (
            "enumerate_start_zero_counter"
        )


def test_recipe_generator_creates_runnable_enumerate_counter_method_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_iterator_average_box(root)
        report = generate_benchmark_recipes(
            _sources_payload(
                raw_source,
                target_path="samplepkg/iterator_average_box.py",
            ),
            recipe="enumerate_start_zero_counter",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        mutation = template_case["mutations"][0]
        test_content = next(
            file["content"]
            for file in template_case["files"]
            if file["target_path"].startswith("test_")
        )

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["buggy_functions"] == [
            "AverageCounter.iterator_average.count_items"
        ]
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "enumerate_start_zero_counter"
        ]
        assert "enumerate(iterable, start=0)" in mutation["replace"]
        assert (
            "from samplepkg.iterator_average_box import AverageCounter"
            in test_content
        )
        assert (
            "AverageCounter().iterator_average(one_item_generator()) == 4.0"
            in test_content
        )
        assert {"target_path": "samplepkg/__init__.py", "content": ""} in (
            template_case["files"]
        )

        template = root / "generated_counter_method_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_counter_method",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == (
            "enumerate_start_zero_counter"
        )


def test_recipe_generator_creates_runnable_identity_comparison_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_token_classifier(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="token_classifier.py"),
            recipe="identity_comparison_literal",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "identity_comparison_literal"
        ]
        assert template_case["benchmark"]["metadata"]["recipe"] == (
            "identity_comparison_literal"
        )
        assert template_case["mutations"][0]["find"] == (
            "    return token == 'admin'"
        )
        assert template_case["mutations"][0]["replace"] == (
            "    return token is 'admin'"
        )
        assert "''.join(['ad', 'min'])" in template_case["files"][0]["content"]

        template = root / "generated_identity_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_identity",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == (
            "identity_comparison_literal"
        )


def test_recipe_generator_creates_runnable_identity_comparison_method_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_token_classifier_box(root)
        report = generate_benchmark_recipes(
            _sources_payload(
                raw_source,
                target_path="samplepkg/token_classifier_box.py",
            ),
            recipe="identity_comparison_literal",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        mutation = template_case["mutations"][0]
        test_content = next(
            file["content"]
            for file in template_case["files"]
            if file["target_path"].startswith("test_")
        )

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["buggy_functions"] == [
            "TokenClassifier.is_admin"
        ]
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "identity_comparison_literal"
        ]
        assert mutation["find"] == "        return token == 'admin'"
        assert mutation["replace"] == "        return token is 'admin'"
        assert (
            "from samplepkg.token_classifier_box import TokenClassifier"
            in test_content
        )
        assert "TokenClassifier().is_admin(value) is True" in test_content
        assert "''.join(['ad', 'min'])" in test_content
        assert {"target_path": "samplepkg/__init__.py", "content": ""} in (
            template_case["files"]
        )

        template = root / "generated_identity_method_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_identity_method",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == (
            "identity_comparison_literal"
        )


def test_recipe_generator_creates_runnable_iterator_double_consumption_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_iterable_average(root)
        report = generate_benchmark_recipes(
            _sources_payload(raw_source, target_path="iterable_average.py"),
            recipe="iterator_double_consumption",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "iterator_double_consumption"
        ]
        assert template_case["benchmark"]["metadata"]["recipe"] == (
            "iterator_double_consumption"
        )
        assert template_case["mutations"][0]["find"] == (
            "    values = list(values)\n"
            "    total = sum(values)\n"
            "    count = len(values)"
        )
        assert template_case["mutations"][0]["replace"] == (
            "    total = sum(values)\n"
            "    count = len(list(values))"
        )
        assert "def one_two_three()" in template_case["files"][0]["content"]

        template = root / "generated_iterator_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_iterator",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == (
            "iterator_double_consumption"
        )


def test_recipe_generator_creates_runnable_iterator_double_consumption_method_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_iterable_average_box(root)
        report = generate_benchmark_recipes(
            _sources_payload(
                raw_source,
                target_path="samplepkg/iterable_average_box.py",
            ),
            recipe="iterator_double_consumption",
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        mutation = template_case["mutations"][0]
        test_content = next(
            file["content"]
            for file in template_case["files"]
            if file["target_path"].startswith("test_")
        )

        assert report.source_count == 1
        assert report.generated_count == 1
        assert template_case["benchmark"]["buggy_functions"] == [
            "IterableAverager.average_iterable"
        ]
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "iterator_double_consumption"
        ]
        assert mutation["find"] == (
            "        values = list(values)\n"
            "        total = sum(values)\n"
            "        count = len(values)"
        )
        assert mutation["replace"] == (
            "        total = sum(values)\n"
            "        count = len(list(values))"
        )
        assert (
            "from samplepkg.iterable_average_box import IterableAverager"
            in test_content
        )
        assert "IterableAverager().average_iterable(one_two_three()) == 2" in (
            test_content
        )
        assert {"target_path": "samplepkg/__init__.py", "content": ""} in (
            template_case["files"]
        )

        template = root / "generated_iterator_method_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_iterator_method",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].best_patch_rule_id == (
            "iterator_double_consumption"
        )


def test_recipe_generator_catalog_realizes_seed():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        report = generate_benchmark_recipes(_sources_payload(raw_source))
        realization = realize_benchmark_template_seeds(
            _seed_payload(),
            report.to_dict()["catalog"],
        )
        template_case = realization.to_dict()["realized_template"]["cases"][0]

        assert realization.realized_count == 1
        assert template_case["benchmark"]["metadata"]["seed_status"] == (
            "realized_from_catalog"
        )
        assert template_case["benchmark"]["metadata"]["realization_candidate_id"].endswith(
            "_mean_missing_zero_guard"
        )
        assert template_case["benchmark"]["metadata"]["mining_failure_type"] == (
            "runtime_error"
        )


def test_recipe_generator_uses_source_cache_without_network_fetch():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        digest = hashlib.sha256(raw_source.read_bytes()).hexdigest()
        cache = root / "cache"
        cache.mkdir()
        (cache / f"{digest}.py").write_bytes(raw_source.read_bytes())
        payload = {
            "sources": [
                {
                    "raw_url": "https://example.invalid/average_mean.py",
                    "target_path": "average_mean.py",
                    "sha256": digest,
                }
            ]
        }

        report = generate_benchmark_recipes(payload, source_cache_dir=cache)

        assert report.generated_count == 1
        assert report.results[0].status == "generated"


def test_recipe_generator_markdown_reports_skipped_sources():
    report = generate_benchmark_recipes(
        {
            "sources": [
                {
                    "raw_url": "missing-file.py",
                    "target_path": "missing.py",
                }
            ]
        }
    )
    markdown = render_recipe_generation_markdown(report)

    assert report.generated_count == 0
    assert "source_read_error" in markdown
    assert "missing.py" in markdown


def test_recipe_generator_cli_writes_report_catalog_and_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        sources = root / "sources.json"
        output_json = root / "recipe_report.json"
        output_markdown = root / "recipe.md"
        output_catalog = root / "catalog.json"
        output_template = root / "template.json"
        sources.write_text(json.dumps(_sources_payload(raw_source)), encoding="utf-8")

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_recipe_generator",
                str(sources),
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
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert "# Benchmark Recipe Generation" in completed.stdout
        assert payload["generated_count"] == 1
        assert json.loads(output_catalog.read_text(encoding="utf-8"))[
            "candidates"
        ][0]["rule_ids"] == ["missing_len_zero_guard"]
        assert BenchmarkValidator().validate_template(output_template).is_valid


def test_recipe_generator_cli_accepts_index_overrun_recipe():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_bubble_sort(root)
        sources = root / "sources.json"
        output_json = root / "recipe_report.json"
        output_template = root / "template.json"
        sources.write_text(
            json.dumps(_sources_payload(raw_source, target_path="bubble_sort.py")),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_recipe_generator",
                str(sources),
                "--recipe",
                "possible_index_overrun",
                "--output-json",
                str(output_json),
                "--output-template",
                str(output_template),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert payload["generated_count"] == 1
        assert json.loads(output_template.read_text(encoding="utf-8"))["cases"][0][
            "benchmark"
        ]["expected_rule_ids"] == ["possible_index_overrun"]


def test_recipe_generator_cli_accepts_dict_missing_key_guard_recipe():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_score_lookup(root)
        sources = root / "sources.json"
        output_json = root / "recipe_report.json"
        output_template = root / "template.json"
        sources.write_text(
            json.dumps(_sources_payload(raw_source, target_path="score_lookup.py")),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_recipe_generator",
                str(sources),
                "--recipe",
                "dict_missing_key_guard",
                "--output-json",
                str(output_json),
                "--output-template",
                str(output_template),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert payload["generated_count"] == 1
        assert json.loads(output_template.read_text(encoding="utf-8"))["cases"][0][
            "benchmark"
        ]["expected_rule_ids"] == ["dict_missing_key_guard"]


def test_recipe_generator_cli_accepts_inplace_api_recipe():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_normalizer(root)
        sources = root / "sources.json"
        output_json = root / "recipe_report.json"
        output_template = root / "template.json"
        sources.write_text(
            json.dumps(_sources_payload(raw_source, target_path="normalizer.py")),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_recipe_generator",
                str(sources),
                "--recipe",
                "inplace_api_return_value",
                "--output-json",
                str(output_json),
                "--output-template",
                str(output_template),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert payload["generated_count"] == 1
        assert json.loads(output_template.read_text(encoding="utf-8"))["cases"][0][
            "benchmark"
        ]["expected_rule_ids"] == ["inplace_api_return_value"]


def test_recipe_generator_cli_accepts_stringified_numeric_recipe():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_middle_value(root)
        sources = root / "sources.json"
        output_json = root / "recipe_report.json"
        output_template = root / "template.json"
        sources.write_text(
            json.dumps(_sources_payload(raw_source, target_path="middle_value.py")),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_recipe_generator",
                str(sources),
                "--recipe",
                "stringified_numeric_value",
                "--output-json",
                str(output_json),
                "--output-template",
                str(output_template),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert payload["generated_count"] == 1
        assert json.loads(output_template.read_text(encoding="utf-8"))["cases"][0][
            "benchmark"
        ]["expected_rule_ids"] == ["stringified_numeric_value"]


def test_recipe_generator_cli_accepts_mutable_default_recipe():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_mean_value(root)
        sources = root / "sources.json"
        output_json = root / "recipe_report.json"
        output_template = root / "template.json"
        sources.write_text(
            json.dumps(_sources_payload(raw_source, target_path="mean_value.py")),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_recipe_generator",
                str(sources),
                "--recipe",
                "mutable_default_arg",
                "--output-json",
                str(output_json),
                "--output-template",
                str(output_template),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert payload["generated_count"] == 1
        assert json.loads(output_template.read_text(encoding="utf-8"))["cases"][0][
            "benchmark"
        ]["expected_rule_ids"] == ["mutable_default_arg"]


def test_recipe_generator_cli_accepts_broad_exception_recipe():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        sources = root / "sources.json"
        output_json = root / "recipe_report.json"
        output_template = root / "template.json"
        sources.write_text(json.dumps(_sources_payload(raw_source)), encoding="utf-8")

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_recipe_generator",
                str(sources),
                "--recipe",
                "broad_exception_pass",
                "--output-json",
                str(output_json),
                "--output-template",
                str(output_template),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert payload["generated_count"] == 1
        assert json.loads(output_template.read_text(encoding="utf-8"))["cases"][0][
            "benchmark"
        ]["expected_rule_ids"] == ["broad_exception_pass"]


def test_recipe_generator_cli_accepts_always_true_len_recipe():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        sources = root / "sources.json"
        output_json = root / "recipe_report.json"
        output_template = root / "template.json"
        sources.write_text(json.dumps(_sources_payload(raw_source)), encoding="utf-8")

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_recipe_generator",
                str(sources),
                "--recipe",
                "always_true_len_check",
                "--output-json",
                str(output_json),
                "--output-template",
                str(output_template),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert payload["generated_count"] == 1
        assert json.loads(output_template.read_text(encoding="utf-8"))["cases"][0][
            "benchmark"
        ]["expected_rule_ids"] == ["always_true_len_check"]


def test_recipe_generator_cli_accepts_enumerate_counter_recipe():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_iterator_average(root)
        sources = root / "sources.json"
        output_json = root / "recipe_report.json"
        output_template = root / "template.json"
        sources.write_text(
            json.dumps(_sources_payload(raw_source, target_path="iterator_average.py")),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_recipe_generator",
                str(sources),
                "--recipe",
                "enumerate_start_zero_counter",
                "--output-json",
                str(output_json),
                "--output-template",
                str(output_template),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert payload["generated_count"] == 1
        assert json.loads(output_template.read_text(encoding="utf-8"))["cases"][0][
            "benchmark"
        ]["expected_rule_ids"] == ["enumerate_start_zero_counter"]


def test_recipe_generator_cli_accepts_identity_comparison_recipe():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_token_classifier(root)
        sources = root / "sources.json"
        output_json = root / "recipe_report.json"
        output_template = root / "template.json"
        sources.write_text(
            json.dumps(_sources_payload(raw_source, target_path="token_classifier.py")),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_recipe_generator",
                str(sources),
                "--recipe",
                "identity_comparison_literal",
                "--output-json",
                str(output_json),
                "--output-template",
                str(output_template),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert payload["generated_count"] == 1
        assert json.loads(output_template.read_text(encoding="utf-8"))["cases"][0][
            "benchmark"
        ]["expected_rule_ids"] == ["identity_comparison_literal"]


def test_recipe_generator_cli_accepts_iterator_double_consumption_recipe():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_iterable_average(root)
        sources = root / "sources.json"
        output_json = root / "recipe_report.json"
        output_template = root / "template.json"
        sources.write_text(
            json.dumps(_sources_payload(raw_source, target_path="iterable_average.py")),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.benchmark_recipe_generator",
                str(sources),
                "--recipe",
                "iterator_double_consumption",
                "--output-json",
                str(output_json),
                "--output-template",
                str(output_template),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert payload["generated_count"] == 1
        assert json.loads(output_template.read_text(encoding="utf-8"))["cases"][0][
            "benchmark"
        ]["expected_rule_ids"] == ["iterator_double_consumption"]


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


def _write_mean_stats(root: Path) -> Path:
    raw_source = root / "mean_stats.py"
    raw_source.write_text(
        "class MeanStats:\n"
        "    def mean(self, nums):\n"
        "        if not nums:\n"
        "            raise ValueError(\"List is empty\")\n"
        "        return sum(nums) / len(nums)\n",
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


def _write_successor_window(root: Path) -> Path:
    raw_source = root / "successor_window.py"
    raw_source.write_text(
        "class SuccessorWindow:\n"
        "    def next_values(self, values):\n"
        "        result = []\n"
        "        length = len(values)\n"
        "        for i in range(length - 1):\n"
        "            result.append(values[i + 1])\n"
        "        return result\n",
        encoding="utf-8",
    )
    return raw_source


def _write_score_lookup(root: Path) -> Path:
    raw_source = root / "score_lookup.py"
    raw_source.write_text(
        "def score_for(scores, name):\n"
        "    return scores.get(name, 0)\n",
        encoding="utf-8",
    )
    return raw_source


def _write_score_table(root: Path) -> Path:
    raw_source = root / "score_table.py"
    raw_source.write_text(
        "class ScoreTable:\n"
        "    def score_for(self, scores, name):\n"
        "        return scores.get(name, 0)\n",
        encoding="utf-8",
    )
    return raw_source


def _write_normalizer(root: Path) -> Path:
    raw_source = root / "normalizer.py"
    raw_source.write_text(
        "def normalize(values):\n"
        "    values = sorted(values)\n"
        "    return values\n",
        encoding="utf-8",
    )
    return raw_source


def _write_sorting_helper(root: Path) -> Path:
    raw_source = root / "sorting_helper.py"
    raw_source.write_text(
        "def sort_values(values):\n"
        "    values = list(values)\n"
        "    values.sort()\n"
        "    return values\n",
        encoding="utf-8",
    )
    return raw_source


def _write_sorting_box(root: Path) -> Path:
    raw_source = root / "sorting_box.py"
    raw_source.write_text(
        "class SortingBox:\n"
        "    def sort_values(self, values):\n"
        "        values = list(values)\n"
        "        values.sort()\n"
        "        return values\n",
        encoding="utf-8",
    )
    return raw_source


def _write_join_options(root: Path) -> Path:
    raw_source = root / "formatting.py"
    raw_source.write_text(
        "def join_options(options):\n"
        "    rv = []\n"
        "    any_prefix_is_slash = False\n"
        "    for opt in options:\n"
        "        prefix = '--' if opt.startswith('--') else opt[:1]\n"
        "        rv.append((len(prefix), opt))\n"
        "    rv.sort(key=lambda x: x[0])\n"
        "    return ', '.join(x[1] for x in rv), any_prefix_is_slash\n",
        encoding="utf-8",
    )
    return raw_source


def _write_middle_value(root: Path) -> Path:
    raw_source = root / "middle_value.py"
    raw_source.write_text(
        "def middle_value(values):\n"
        "    index = len(values) // 2\n"
        "    return values[index]\n",
        encoding="utf-8",
    )
    return raw_source


def _write_window_picker(root: Path) -> Path:
    raw_source = root / "window.py"
    raw_source.write_text(
        "class WindowPicker:\n"
        "    def middle(self, values):\n"
        "        index = len(values) // 2\n"
        "        return values[index]\n",
        encoding="utf-8",
    )
    return raw_source


def _write_mean_value(root: Path) -> Path:
    raw_source = root / "mean_value.py"
    raw_source.write_text(
        "def mean_value(values):\n"
        "    total = sum(values)\n"
        "    return total / len(values)\n",
        encoding="utf-8",
    )
    return raw_source


def _write_tag_tracer(root: Path) -> Path:
    raw_source = root / "_tracing.py"
    raw_source.write_text(
        "class TagTracerSub:\n"
        "    def __init__(self, root, tags):\n"
        "        self.root = root\n"
        "        self.tags = tags\n\n"
        "    def get(self, name: str):\n"
        "        return self.__class__(self.root, self.tags + (name,))\n\n\n"
        "class TagTracer:\n"
        "    def get(self, name: str):\n"
        "        return TagTracerSub(self, (name,))\n",
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


def _write_iterator_average_box(root: Path) -> Path:
    raw_source = root / "iterator_average_box.py"
    raw_source.write_text(
        "class AverageCounter:\n"
        "    def iterator_average(self, iterable):\n"
        "        n = 0\n\n"
        "        def count_items():\n"
        "            nonlocal n\n"
        "            for n, value in enumerate(iterable, start=1):\n"
        "                yield value\n\n"
        "        total = sum(count_items())\n"
        "        return total / n\n",
        encoding="utf-8",
    )
    return raw_source


def _write_token_classifier(root: Path) -> Path:
    raw_source = root / "token_classifier.py"
    raw_source.write_text(
        "def is_admin(token):\n"
        "    return token == 'admin'\n",
        encoding="utf-8",
    )
    return raw_source


def _write_token_classifier_box(root: Path) -> Path:
    raw_source = root / "token_classifier_box.py"
    raw_source.write_text(
        "class TokenClassifier:\n"
        "    def is_admin(self, token):\n"
        "        return token == 'admin'\n",
        encoding="utf-8",
    )
    return raw_source


def _write_iterable_average(root: Path) -> Path:
    raw_source = root / "iterable_average.py"
    raw_source.write_text(
        "def average_iterable(values):\n"
        "    values = list(values)\n"
        "    total = sum(values)\n"
        "    count = len(values)\n"
        "    return total / count\n",
        encoding="utf-8",
    )
    return raw_source


def _write_iterable_average_box(root: Path) -> Path:
    raw_source = root / "iterable_average_box.py"
    raw_source.write_text(
        "class IterableAverager:\n"
        "    def average_iterable(self, values):\n"
        "        values = list(values)\n"
        "        total = sum(values)\n"
        "        count = len(values)\n"
        "        return total / count\n",
        encoding="utf-8",
    )
    return raw_source


def _sources_payload(raw_source: Path, target_path: str = "average_mean.py") -> dict:
    digest = hashlib.sha256(raw_source.read_bytes()).hexdigest()
    return {
        "sources": [
            {
                "raw_url": str(raw_source),
                "target_path": target_path,
                "sha256": digest,
            }
        ]
    }


def _seed_payload() -> dict:
    return {
        "cases": [
            {
                "name": "judge_mining_runtime_error_capped_by_execution_evidence",
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
                    "expected_rule_ids": ["runtime_contract_violation"],
                    "failing_tests": ["test_seed"],
                    "passed_tests": [],
                    "test_args": [],
                    "metadata": {
                        "source": "github_raw_judge_cluster_seed",
                        "seed_status": "needs_human_source_selection",
                        "mining_priority": "high",
                        "mining_focus": "runtime traceback calibration",
                        "mining_pattern": "capped_by_execution_evidence",
                        "mining_failure_type": "runtime_error",
                        "evidence_examples": ["cluster_case#1:bad_patch"],
                    },
                },
            }
        ]
    }
