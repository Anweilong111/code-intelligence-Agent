from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from code_intelligence_agent.evaluation.github_fetcher import (
    FetchSource,
    GitHubBenchmarkFetcher,
    read_source_bytes,
    source_from_dict,
)

SUPPORTED_RECIPES = {
    "always_true_len_check",
    "broad_exception_pass",
    "dict_missing_key_guard",
    "enumerate_start_zero_counter",
    "identity_comparison_literal",
    "inplace_api_return_value",
    "inverted_empty_guard",
    "iterator_double_consumption",
    "missing_len_zero_guard",
    "mutable_default_arg",
    "possible_index_overrun",
    "stringified_numeric_value",
}


@dataclass(frozen=True)
class RecipeGenerationResult:
    source: dict[str, Any]
    status: str
    generated_count: int
    candidates: list[dict[str, Any]]
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecipeGenerationReport:
    source_path: str
    recipe: str
    source_count: int
    generated_count: int
    results: list[RecipeGenerationResult]

    def to_dict(self) -> dict[str, Any]:
        candidates = [
            candidate
            for result in self.results
            for candidate in result.candidates
        ]
        return {
            "source_path": self.source_path,
            "recipe": self.recipe,
            "source_count": self.source_count,
            "generated_count": self.generated_count,
            "results": [result.to_dict() for result in self.results],
            "catalog": {"candidates": candidates},
            "template": {
                "cases": [candidate["template_case"] for candidate in candidates]
            },
        }


def generate_benchmark_recipes(
    payload: dict[str, Any],
    recipe: str = "missing_len_zero_guard",
    source_path: str = "",
    source_cache_dir: str | Path | None = None,
) -> RecipeGenerationReport:
    if recipe not in SUPPORTED_RECIPES:
        raise ValueError(f"Unsupported recipe: {recipe}")
    sources = _extract_sources(payload)
    results = [
        _generate_for_source(
            source,
            recipe=recipe,
            source_cache_dir=source_cache_dir,
        )
        for source in sources
    ]
    return RecipeGenerationReport(
        source_path=source_path,
        recipe=recipe,
        source_count=len(sources),
        generated_count=sum(result.generated_count for result in results),
        results=results,
    )


def render_recipe_generation_markdown(report: RecipeGenerationReport) -> str:
    lines = [
        "# Benchmark Recipe Generation",
        "",
        f"- Source: `{report.source_path or '<memory>'}`",
        f"- Recipe: `{report.recipe}`",
        f"- Input Sources: {report.source_count}",
        f"- Generated Candidates: {report.generated_count}",
        "",
        "| Target | Status | Generated | Reasons |",
        "| --- | --- | ---: | --- |",
    ]
    for result in report.results:
        lines.append(
            "| "
            f"{_markdown_cell(result.source.get('target_path', ''))} | "
            f"{_markdown_cell(result.status)} | "
            f"{result.generated_count} | "
            f"{_markdown_cell(', '.join(result.reasons))} |"
        )
    return "\n".join(lines)


def _generate_for_source(
    source: FetchSource,
    recipe: str,
    source_cache_dir: str | Path | None = None,
) -> RecipeGenerationResult:
    source_dict = _source_to_dict(source)
    try:
        content = _read_source_bytes(source, source_cache_dir=source_cache_dir)
        _validate_sha256(content, source)
        text = content.decode("utf-8")
    except Exception as exc:
        return RecipeGenerationResult(
            source=source_dict,
            status="failed",
            generated_count=0,
            candidates=[],
            reasons=[f"source_read_error={type(exc).__name__}: {exc}"],
        )
    candidates = _generate_candidates(text, source, recipe)
    reasons = []
    if not candidates:
        reasons.append(_no_candidate_reason(recipe))
    return RecipeGenerationResult(
        source=source_dict,
        status="generated" if candidates else "skipped",
        generated_count=len(candidates),
        candidates=candidates,
        reasons=reasons,
    )


def _read_source_bytes(
    source: FetchSource,
    source_cache_dir: str | Path | None = None,
) -> bytes:
    if source_cache_dir is None:
        return read_source_bytes(source.resolved_url)
    with tempfile.TemporaryDirectory() as tmp_dir:
        written = GitHubBenchmarkFetcher().fetch_sources(
            [source],
            tmp_dir,
            cache_dir=source_cache_dir,
        )
        if not written:
            raise FileNotFoundError(f"No source fetched for {source.target_path}")
        return written[0].read_bytes()


def _generate_candidates(
    source_text: str,
    source: FetchSource,
    recipe: str,
) -> list[dict[str, Any]]:
    if recipe == "missing_len_zero_guard":
        return _missing_len_zero_guard_candidates(source_text, source)
    if recipe == "possible_index_overrun":
        return _possible_index_overrun_candidates(source_text, source)
    if recipe == "dict_missing_key_guard":
        return _dict_missing_key_guard_candidates(source_text, source)
    if recipe == "inplace_api_return_value":
        return _inplace_api_return_value_candidates(source_text, source)
    if recipe == "stringified_numeric_value":
        return _stringified_numeric_value_candidates(source_text, source)
    if recipe == "mutable_default_arg":
        return _mutable_default_arg_candidates(source_text, source)
    if recipe == "broad_exception_pass":
        return _broad_exception_pass_candidates(source_text, source)
    if recipe == "always_true_len_check":
        return _always_true_len_check_candidates(source_text, source)
    if recipe == "enumerate_start_zero_counter":
        return _enumerate_start_zero_counter_candidates(source_text, source)
    if recipe == "identity_comparison_literal":
        return _identity_comparison_literal_candidates(source_text, source)
    if recipe == "iterator_double_consumption":
        return _iterator_double_consumption_candidates(source_text, source)
    if recipe == "inverted_empty_guard":
        return _inverted_empty_guard_candidates(source_text, source)
    return []


def _no_candidate_reason(recipe: str) -> str:
    return {
        "missing_len_zero_guard": "no_empty_guard_len_denominator_function",
        "possible_index_overrun": "no_bounded_positive_offset_index_loop",
        "dict_missing_key_guard": "no_mapping_get_default_lookup",
        "inplace_api_return_value": "no_sorted_assignment_for_inplace_api_mutation",
        "stringified_numeric_value": "no_numeric_assignment_for_stringified_value_mutation",
        "mutable_default_arg": "no_function_suitable_for_mutable_default_cache_mutation",
        "broad_exception_pass": "no_single_argument_function_for_broad_exception_mutation",
        "always_true_len_check": "no_empty_guard_with_following_main_logic",
        "enumerate_start_zero_counter": "no_yielding_one_based_enumerate_loop",
        "identity_comparison_literal": "no_returned_literal_equality_comparison",
        "iterator_double_consumption": "no_materialized_iterator_average_pattern",
        "inverted_empty_guard": "no_empty_guard_with_non_empty_oracle",
    }.get(recipe, "no_recipe_candidate")


def _missing_len_zero_guard_candidates(
    source_text: str,
    source: FetchSource,
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    candidates = []
    lines = source_text.splitlines()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(node, ast.ClassDef) and _class_can_instantiate_without_args(
                node
            ):
                for statement in node.body:
                    if not isinstance(
                        statement,
                        (ast.FunctionDef, ast.AsyncFunctionDef),
                    ):
                        continue
                    if statement.name.startswith("__"):
                        continue
                    recipe = _missing_len_zero_guard_recipe(
                        statement,
                        lines,
                        source,
                        class_name=node.name,
                    )
                    if recipe is not None:
                        candidates.append(recipe)
            continue
        recipe = _missing_len_zero_guard_recipe(node, lines, source)
        if recipe is not None:
            candidates.append(recipe)
    return candidates


def _missing_len_zero_guard_recipe(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    source: FetchSource,
    *,
    class_name: str | None = None,
) -> dict[str, Any] | None:
    for index, statement in enumerate(node.body):
        if not isinstance(statement, ast.If):
            continue
        guard_name = _empty_guard_name(statement.test)
        if not guard_name:
            continue
        exception_name = _raised_exception_name(statement)
        if exception_name not in {"ValueError", "StatisticsError"}:
            continue
        return_node = _first_len_denominator_return(
            node.body[index + 1 :],
            guard_name,
        )
        if return_node is None:
            continue
        find = _source_segment(lines, statement.lineno, return_node.end_lineno)
        replace = _missing_guard_replacement(
            guard_name=guard_name,
            return_node=return_node,
            indent=_line_indent(lines[statement.lineno - 1]),
        )
        if not find or not replace:
            continue
        module_name = _module_import_name(source.target_path)
        function_label = f"{class_name}.{node.name}" if class_name else node.name
        call_expression = (
            f"{class_name}().{node.name}"
            if class_name
            else node.name
        )
        test_name = (
            f"test_{_slug(class_name)}_{node.name}_empty_input_raises_{_slug(exception_name)}"
            if class_name
            else f"test_{node.name}_empty_input_raises_{_slug(exception_name)}"
        )
        candidate_id = (
            f"generated_{_slug(module_name)}_{_slug(function_label)}_missing_zero_guard"
        )
        files = _package_init_overlay_files(source.target_path)
        files.append(
            {
                "target_path": f"test_{_slug(function_label)}_zero_guard.py",
                "content": _empty_input_test_content(
                    module_name=module_name,
                    import_name=class_name or node.name,
                    exception_name=exception_name,
                    test_name=test_name,
                    call_expression=call_expression,
                ),
            }
        )
        template_case = {
            "name": candidate_id,
            "repo_path": f"{candidate_id}_repo",
            "sources": [_source_to_dict(source)],
            "mutations": [
                {
                    "target_path": source.target_path,
                    "find": find,
                    "replace": replace,
                    "count": 1,
                    "description": (
                        f"Remove {function_label}'s empty-input guard before a "
                        "len-derived denominator."
                    ),
                }
            ],
            "files": files,
            "benchmark": {
                "buggy_functions": [function_label],
                "expected_rule_ids": ["missing_len_zero_guard"],
                "failing_tests": [test_name],
                "passed_tests": [],
                "test_args": [],
                "metadata": {
                    "source": "github_raw_recipe_generation",
                    "recipe": "missing_len_zero_guard",
                    "bug_type": "zero division error",
                    "upstream": _upstream_name(source),
                    "upstream_ref": source.ref or "",
                    "upstream_path": source.source_path or source.raw_url or "",
                    "target_path": source.target_path,
                    "license": source.license or "",
                },
            },
        }
        return {
            "id": candidate_id,
            "rule_ids": ["missing_len_zero_guard"],
            "function_name": function_label,
            "target_path": source.target_path,
            "bug_type": "zero division error",
            "failure_types": [
                "patch_apply_error",
                "runtime_error",
                "syntax_error",
                "test_failure",
                "zero_division_error",
            ],
            "benchmark_focuses": [
                "execution-evidence calibration",
                "judge false-positive hardening",
                "near-miss semantic repair",
                "runtime traceback calibration",
            ],
            "patterns": [
                "capped_by_execution_evidence",
                "failure_type=patch_apply_error",
                "failure_type=runtime_error",
                "failure_type=syntax_error",
                "failure_type=test_failure",
                "failure_type=zero_division_error",
            ],
            "source_summary": {
                "target_path": source.target_path,
                "function": function_label,
                "guard_name": guard_name,
                "exception_name": exception_name,
            },
            "mutation_summary": {
                "description": template_case["mutations"][0]["description"],
            },
            "template_case": template_case,
        }
    return None


def _possible_index_overrun_candidates(
    source_text: str,
    source: FetchSource,
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    candidates = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(node, ast.ClassDef) and _class_can_instantiate_without_args(
                node
            ):
                for statement in node.body:
                    if not isinstance(
                        statement,
                        (ast.FunctionDef, ast.AsyncFunctionDef),
                    ):
                        continue
                    if statement.name.startswith("__"):
                        continue
                    recipe = _possible_index_overrun_recipe(
                        statement,
                        source,
                        class_name=node.name,
                    )
                    if recipe is not None:
                        candidates.append(recipe)
            continue
        recipe = _possible_index_overrun_recipe(node, source)
        if recipe is not None:
            candidates.append(recipe)
    return candidates


def _possible_index_overrun_recipe(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source: FetchSource,
    *,
    class_name: str | None = None,
) -> dict[str, Any] | None:
    len_assignments = _len_assignments(node)
    for child in ast.walk(node):
        if not isinstance(child, ast.For) or not isinstance(child.target, ast.Name):
            continue
        loop_info = _bounded_len_loop_info(child.iter, len_assignments)
        if loop_info is None:
            continue
        collection_name, find_expression, replace_expression = loop_info
        if not _loop_reads_positive_offset(
            child.body,
            collection_name=collection_name,
            index_name=child.target.id,
        ):
            continue
        module_name = _module_import_name(source.target_path)
        function_label = f"{class_name}.{node.name}" if class_name else node.name
        call_expression = (
            f"{class_name}().{node.name}"
            if class_name
            else node.name
        )
        test_name = (
            f"test_{_slug(class_name)}_{node.name}_does_not_overrun"
            if class_name
            else f"test_{node.name}_does_not_overrun"
        )
        candidate_id = (
            f"generated_{_slug(module_name)}_{_slug(function_label)}_index_overrun"
        )
        files = _package_init_overlay_files(source.target_path)
        files.append(
            {
                "target_path": f"test_{_slug(function_label)}_index.py",
                "content": _index_overrun_test_content(
                    module_name=module_name,
                    import_name=class_name or node.name,
                    function_name=node.name,
                    call_expression=call_expression,
                    test_name=test_name,
                ),
            }
        )
        template_case = {
            "name": candidate_id,
            "repo_path": f"{candidate_id}_repo",
            "sources": [_source_to_dict(source)],
            "mutations": [
                {
                    "target_path": source.target_path,
                    "find": find_expression,
                    "replace": replace_expression,
                    "count": 1,
                    "description": (
                        f"Expand {function_label}'s bounded loop to range(len({collection_name})) "
                        f"while it still reads {collection_name}[{child.target.id} + 1]."
                    ),
                }
            ],
            "files": files,
            "benchmark": {
                "buggy_functions": [function_label],
                "expected_rule_ids": ["possible_index_overrun"],
                "failing_tests": [test_name],
                "passed_tests": [],
                "test_args": [],
                "metadata": {
                    "source": "github_raw_recipe_generation",
                    "recipe": "possible_index_overrun",
                    "bug_type": "boundary error",
                    "upstream": _upstream_name(source),
                    "upstream_ref": source.ref or "",
                    "upstream_path": source.source_path or source.raw_url or "",
                    "target_path": source.target_path,
                    "license": source.license or "",
                },
            },
        }
        return {
            "id": candidate_id,
            "rule_ids": ["possible_index_overrun"],
            "function_name": function_label,
            "target_path": source.target_path,
            "bug_type": "boundary error",
            "failure_types": [
                "index_error",
                "patch_apply_error",
                "runtime_error",
                "syntax_error",
                "test_failure",
            ],
            "benchmark_focuses": [
                "execution-evidence calibration",
                "judge false-positive hardening",
                "near-miss semantic repair",
                "runtime traceback calibration",
            ],
            "patterns": [
                "capped_by_execution_evidence",
                "failure_type=index_error",
                "failure_type=patch_apply_error",
                "failure_type=runtime_error",
                "failure_type=syntax_error",
                "failure_type=test_failure",
            ],
            "source_summary": {
                "target_path": source.target_path,
                "function": function_label,
                "collection_name": collection_name,
                "index_name": child.target.id,
            },
            "mutation_summary": {
                "description": template_case["mutations"][0]["description"],
            },
            "template_case": template_case,
        }
    return None


def _dict_missing_key_guard_candidates(
    source_text: str,
    source: FetchSource,
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    candidates = []
    lines = source_text.splitlines()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            if isinstance(node, ast.ClassDef) and _class_can_instantiate_without_args(
                node
            ):
                for statement in node.body:
                    if not isinstance(statement, ast.FunctionDef):
                        continue
                    if statement.name.startswith("__"):
                        continue
                    recipe = _dict_missing_key_guard_recipe(
                        statement,
                        lines=lines,
                        source_text=source_text,
                        source=source,
                        class_name=node.name,
                    )
                    if recipe is not None:
                        candidates.append(recipe)
            continue
        recipe = _dict_missing_key_guard_recipe(
            node,
            lines=lines,
            source_text=source_text,
            source=source,
        )
        if recipe is not None:
            candidates.append(recipe)
    return candidates


def _dict_missing_key_guard_recipe(
    node: ast.FunctionDef,
    lines: list[str],
    source_text: str,
    source: FetchSource,
    *,
    class_name: str | None = None,
) -> dict[str, Any] | None:
    info = _mapping_get_default_info(node, source_text)
    if info is None:
        return None
    statement = info["statement"]
    find = _source_segment(lines, statement.lineno, statement.end_lineno)
    if not find:
        return None
    replace = find.replace(info["call_source"], info["subscript_source"], 1)
    if replace == find:
        return None
    module_name = _module_import_name(source.target_path)
    function_label = f"{class_name}.{node.name}" if class_name else node.name
    test_name = (
        f"test_{_slug(class_name)}_{node.name}_missing_key_uses_default"
        if class_name
        else f"test_{node.name}_missing_key_uses_default"
    )
    candidate_id = (
        f"generated_{_slug(module_name)}_{_slug(function_label)}_dict_missing_key_guard"
    )
    files = _package_init_overlay_files(source.target_path)
    files.append(
        {
            "target_path": f"test_{_slug(function_label)}_mapping.py",
            "content": _dict_missing_key_guard_test_content(
                module_name=module_name,
                import_name=class_name or node.name,
                call_expression=(
                    f"{class_name}().{node.name}"
                    if class_name
                    else node.name
                ),
                test_name=test_name,
                default_value=info["default_value"],
            ),
        }
    )
    template_case = {
        "name": candidate_id,
        "repo_path": f"{candidate_id}_repo",
        "sources": [_source_to_dict(source)],
        "mutations": [
            {
                "target_path": source.target_path,
                "find": find,
                "replace": replace,
                "count": 1,
                "description": (
                    f"Replace {function_label}'s mapping.get default lookup with "
                    "unguarded subscript access."
                ),
            }
        ],
        "files": files,
        "benchmark": {
            "buggy_functions": [function_label],
            "expected_rule_ids": ["dict_missing_key_guard"],
            "failing_tests": [test_name],
            "passed_tests": [],
            "test_args": [],
            "metadata": {
                "source": "github_raw_recipe_generation",
                "recipe": "dict_missing_key_guard",
                "bug_type": "key error",
                "upstream": _upstream_name(source),
                "upstream_ref": source.ref or "",
                "upstream_path": source.source_path or source.raw_url or "",
                "target_path": source.target_path,
                "license": source.license or "",
            },
        },
    }
    return {
        "id": candidate_id,
        "rule_ids": ["dict_missing_key_guard"],
        "function_name": function_label,
        "target_path": source.target_path,
        "bug_type": "key error",
        "failure_types": [
            "key_error",
            "patch_apply_error",
            "runtime_error",
            "syntax_error",
            "test_failure",
        ],
        "benchmark_focuses": [
            "data-access guard calibration",
            "execution-evidence calibration",
            "mapping default semantics",
            "near-miss semantic repair",
        ],
        "patterns": [
            "capped_by_execution_evidence",
            "failure_type=key_error",
            "failure_type=patch_apply_error",
            "failure_type=runtime_error",
            "failure_type=syntax_error",
            "failure_type=test_failure",
            "missing_mapping_key_default",
        ],
        "source_summary": {
            "target_path": source.target_path,
            "function": function_label,
            "mapping_name": info["mapping_name"],
            "key_name": info["key_name"],
            "default_value": info["default_value"],
        },
        "mutation_summary": {
            "description": template_case["mutations"][0]["description"],
        },
        "template_case": template_case,
    }


def _inplace_api_return_value_candidates(
    source_text: str,
    source: FetchSource,
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    candidates = []
    lines = source_text.splitlines()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(node, ast.ClassDef) and _class_can_instantiate_without_args(
                node
            ):
                for statement in node.body:
                    if not isinstance(
                        statement,
                        (ast.FunctionDef, ast.AsyncFunctionDef),
                    ):
                        continue
                    if statement.name.startswith("__"):
                        continue
                    recipe = _inplace_api_return_value_recipe(
                        statement,
                        lines,
                        source,
                        class_name=node.name,
                    )
                    if recipe is not None:
                        candidates.append(recipe)
            continue
        recipe = _inplace_api_return_value_recipe(node, lines, source)
        if recipe is not None:
            candidates.append(recipe)
    return candidates


def _inplace_api_return_value_recipe(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    source: FetchSource,
    *,
    class_name: str | None = None,
) -> dict[str, Any] | None:
    for child in ast.walk(node):
        assignment = _sorted_assignment_info(child)
        if assignment is not None:
            target_name, receiver_name, sort_args = assignment
            find = _source_segment(lines, child.lineno, child.end_lineno)
            if not find or "\n" in find:
                continue
            call_expression = (
                f"{class_name}().{node.name}"
                if class_name
                else node.name
            )
            assertion = _inplace_api_assertion(
                function_name=node.name,
                result_name=target_name,
                node=node,
                call_expression=call_expression,
            )
            if not assertion:
                continue
            indent = _line_indent(lines[child.lineno - 1])
            replace = f"{indent}{target_name} = {receiver_name}.sort({sort_args})"
            return _inplace_api_candidate(
                node=node,
                source=source,
                target_name=target_name,
                receiver_name=receiver_name,
                find=find,
                replace=replace,
                assertion=assertion,
                class_name=class_name,
                description=(
                    f"Assign list.sort() as if it returned a sorted value "
                    f"in {f'{class_name}.{node.name}' if class_name else node.name}."
                ),
            )
        inplace_call = _inplace_sort_statement_info(child)
        if inplace_call is None:
            continue
        receiver_name, sort_args = inplace_call
        find = _source_segment(lines, child.lineno, child.end_lineno)
        if not find or "\n" in find:
            continue
        call_expression = (
            f"{class_name}().{node.name}"
            if class_name
            else node.name
        )
        assertion = _inplace_api_assertion(
            function_name=node.name,
            result_name=receiver_name,
            node=node,
            call_expression=call_expression,
        )
        if not assertion:
            continue
        indent = _line_indent(lines[child.lineno - 1])
        replace = f"{indent}{receiver_name} = {receiver_name}.sort({sort_args})"
        return _inplace_api_candidate(
            node=node,
            source=source,
            target_name=receiver_name,
            receiver_name=receiver_name,
            find=find,
            replace=replace,
            assertion=assertion,
            class_name=class_name,
            description=(
                f"Assign {receiver_name}.sort() as if it returned the "
                "updated collection in "
                f"{f'{class_name}.{node.name}' if class_name else node.name}."
            ),
        )
    return None


def _inplace_api_candidate(
    *,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source: FetchSource,
    target_name: str,
    receiver_name: str,
    find: str,
    replace: str,
    assertion: str,
    class_name: str | None = None,
    description: str,
) -> dict[str, Any]:
    module_name = _module_import_name(source.target_path)
    function_label = f"{class_name}.{node.name}" if class_name else node.name
    test_name = (
        f"test_{_slug(class_name)}_{node.name}_inplace_api_return_value"
        if class_name
        else f"test_{node.name}_inplace_api_return_value"
    )
    candidate_id = f"generated_{_slug(module_name)}_{_slug(function_label)}_inplace_api"
    files = _package_init_overlay_files(source.target_path)
    files.append(
        {
            "target_path": f"test_{_slug(function_label)}_api.py",
            "content": _inplace_api_test_content(
                module_name=module_name,
                import_name=class_name or node.name,
                test_name=test_name,
                assertion=assertion,
            ),
        }
    )
    template_case = {
        "name": candidate_id,
        "repo_path": f"{candidate_id}_repo",
        "sources": [_source_to_dict(source)],
        "mutations": [
            {
                "target_path": source.target_path,
                "find": find,
                "replace": replace,
                "count": 1,
                "description": description,
            }
        ],
        "files": files,
        "benchmark": {
            "buggy_functions": [function_label],
            "expected_rule_ids": ["inplace_api_return_value"],
            "failing_tests": [test_name],
            "passed_tests": [],
            "test_args": [],
            "metadata": {
                "source": "github_raw_recipe_generation",
                "recipe": "inplace_api_return_value",
                "bug_type": "api misuse",
                "upstream": _upstream_name(source),
                "upstream_ref": source.ref or "",
                "upstream_path": source.source_path or source.raw_url or "",
                "target_path": source.target_path,
                "license": source.license or "",
            },
        },
    }
    return {
        "id": candidate_id,
        "rule_ids": ["inplace_api_return_value"],
        "function_name": function_label,
        "target_path": source.target_path,
        "bug_type": "api misuse",
        "failure_types": [
            "patch_apply_error",
            "runtime_error",
            "syntax_error",
            "test_failure",
            "type_error",
        ],
        "benchmark_focuses": [
            "execution-evidence calibration",
            "judge false-positive hardening",
            "near-miss semantic repair",
            "runtime traceback calibration",
        ],
        "patterns": [
            "capped_by_execution_evidence",
            "failure_type=patch_apply_error",
            "failure_type=runtime_error",
            "failure_type=syntax_error",
            "failure_type=test_failure",
            "failure_type=type_error",
        ],
        "source_summary": {
            "target_path": source.target_path,
            "function": function_label,
            "target_name": target_name,
            "receiver_name": receiver_name,
        },
        "mutation_summary": {
            "description": template_case["mutations"][0]["description"],
        },
        "template_case": template_case,
    }


def _stringified_numeric_value_candidates(
    source_text: str,
    source: FetchSource,
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    candidates = []
    lines = source_text.splitlines()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(node, ast.ClassDef) and _class_can_instantiate_without_args(
                node
            ):
                for statement in node.body:
                    if not isinstance(
                        statement,
                        (ast.FunctionDef, ast.AsyncFunctionDef),
                    ):
                        continue
                    if statement.name.startswith("__"):
                        continue
                    recipe = _stringified_numeric_value_recipe(
                        statement,
                        lines,
                        source,
                        class_name=node.name,
                    )
                    if recipe is not None:
                        candidates.append(recipe)
            continue
        recipe = _stringified_numeric_value_recipe(node, lines, source)
        if recipe is not None:
            candidates.append(recipe)
    return candidates


def _stringified_numeric_value_recipe(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    source: FetchSource,
    *,
    class_name: str | None = None,
) -> dict[str, Any] | None:
    for child in ast.walk(node):
        assignment = _numeric_assignment_info(child)
        if assignment is None:
            continue
        target_name, expression = assignment
        if not _numeric_context_uses_name(node, target_name):
            continue
        call_expression = (
            f"{class_name}().{node.name}"
            if class_name
            else node.name
        )
        assertion = _stringified_numeric_assertion(
            function_name=node.name,
            target_name=target_name,
            node=node,
            call_expression=call_expression,
        )
        if not assertion:
            continue
        end_line = _stringified_numeric_mutation_end_line(
            node,
            target_name=target_name,
            assignment_line=child.lineno,
        )
        find = _source_segment(lines, child.lineno, end_line)
        if not find:
            continue
        indent = _line_indent(lines[child.lineno - 1])
        replacement_lines = find.splitlines()
        replacement_lines[0] = f"{indent}{target_name} = str({expression})"
        replace = "\n".join(replacement_lines)
        module_name = _module_import_name(source.target_path)
        function_label = f"{class_name}.{node.name}" if class_name else node.name
        test_name = (
            f"test_{_slug(class_name)}_{node.name}_stringified_numeric_value"
            if class_name
            else f"test_{node.name}_stringified_numeric_value"
        )
        candidate_id = (
            f"generated_{_slug(module_name)}_{_slug(function_label)}_stringified_numeric"
        )
        files = _package_init_overlay_files(source.target_path)
        files.append(
            {
                "target_path": f"test_{_slug(function_label)}_type.py",
                "content": _stringified_numeric_test_content(
                    module_name=module_name,
                    import_name=class_name or node.name,
                    test_name=test_name,
                    assertion=assertion,
                ),
            }
        )
        template_case = {
            "name": candidate_id,
            "repo_path": f"{candidate_id}_repo",
            "sources": [_source_to_dict(source)],
            "mutations": [
                {
                    "target_path": source.target_path,
                    "find": find,
                    "replace": replace,
                    "count": 1,
                    "description": (
                        f"Stringify numeric value {target_name} before numeric "
                        f"use in {function_label}."
                    ),
                }
            ],
            "files": files,
            "benchmark": {
                "buggy_functions": [function_label],
                "expected_rule_ids": ["stringified_numeric_value"],
                "failing_tests": [test_name],
                "passed_tests": [],
                "test_args": [],
                "metadata": {
                    "source": "github_raw_recipe_generation",
                    "recipe": "stringified_numeric_value",
                    "bug_type": "type error",
                    "upstream": _upstream_name(source),
                    "upstream_ref": source.ref or "",
                    "upstream_path": source.source_path or source.raw_url or "",
                    "target_path": source.target_path,
                    "license": source.license or "",
                },
            },
        }
        return {
            "id": candidate_id,
            "rule_ids": ["stringified_numeric_value"],
            "function_name": function_label,
            "target_path": source.target_path,
            "bug_type": "type error",
            "failure_types": [
                "patch_apply_error",
                "runtime_error",
                "syntax_error",
                "test_failure",
                "type_error",
            ],
            "benchmark_focuses": [
                "execution-evidence calibration",
                "judge false-positive hardening",
                "near-miss semantic repair",
                "runtime traceback calibration",
            ],
            "patterns": [
                "capped_by_execution_evidence",
                "failure_type=patch_apply_error",
                "failure_type=runtime_error",
                "failure_type=syntax_error",
                "failure_type=test_failure",
                "failure_type=type_error",
            ],
            "source_summary": {
                "target_path": source.target_path,
                "function": function_label,
                "target_name": target_name,
                "expression": expression,
            },
            "mutation_summary": {
                "description": template_case["mutations"][0]["description"],
            },
            "template_case": template_case,
        }
    return None


def _mutable_default_arg_candidates(
    source_text: str,
    source: FetchSource,
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    candidates = []
    lines = source_text.splitlines()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(node, ast.ClassDef) and _class_can_instantiate_without_args(
                node
            ):
                for statement in node.body:
                    if not isinstance(
                        statement,
                        (ast.FunctionDef, ast.AsyncFunctionDef),
                    ):
                        continue
                    recipe = _mutable_default_arg_recipe(
                        statement,
                        lines,
                        source,
                        class_name=node.name,
                    )
                    if recipe is not None:
                        candidates.append(recipe)
            continue
        recipe = _mutable_default_arg_recipe(node, lines, source)
        if recipe is not None:
            candidates.append(recipe)
    return candidates


def _mutable_default_arg_recipe(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    source: FetchSource,
    *,
    class_name: str | None = None,
) -> dict[str, Any] | None:
    if not node.args.args or any(argument.arg == "_cache" for argument in node.args.args):
        return None
    parameter_name = _mutable_default_parameter_name(node, class_name=class_name)
    if parameter_name is None:
        return None
    updated_header = _mutable_default_header(lines[node.lineno - 1], node)
    if not updated_header:
        return None
    insertion_statement = _first_executable_statement(node)
    if insertion_statement is None:
        return None
    if class_name is None:
        test_statements = _mutable_default_assertions(node.name)
        if not test_statements:
            return None
    else:
        test_statements = _mutable_default_method_statements(
            class_name=class_name,
            method_name=node.name,
            parameter_name=parameter_name,
            node=node,
        )
        if not test_statements:
            return None
    find = _source_segment(lines, node.lineno, insertion_statement.end_lineno)
    if not find:
        return None
    replacement_lines = find.splitlines()
    replacement_lines[0] = updated_header
    insertion_index = insertion_statement.lineno - node.lineno
    body_indent = _line_indent(lines[insertion_statement.lineno - 1])
    if class_name is None:
        cache_lines = [
            f"{body_indent}_cache.append(list({parameter_name}))",
            f"{body_indent}if len(_cache) > 1:",
            f"{body_indent}    return _cache[0]",
        ]
    else:
        cache_lines = [
            f"{body_indent}_cache.append({parameter_name})",
            f"{body_indent}if len(_cache) > 1:",
            f"{body_indent}    {parameter_name} = _cache[0]",
        ]
    replacement_lines[insertion_index:insertion_index] = cache_lines
    replace = "\n".join(replacement_lines)
    module_name = _module_import_name(source.target_path)
    function_label = f"{class_name}.{node.name}" if class_name else node.name
    test_name = (
        f"test_{_slug(class_name)}_{node.name}_mutable_default_arg"
        if class_name
        else f"test_{node.name}_mutable_default_arg"
    )
    candidate_id = (
        f"generated_{_slug(module_name)}_{_slug(function_label)}_mutable_default"
    )
    files = _package_init_overlay_files(source.target_path)
    files.append(
        {
            "target_path": f"test_{_slug(function_label)}_state.py",
            "content": _mutable_default_test_content(
                module_name=module_name,
                import_name=class_name or node.name,
                test_name=test_name,
                statements=test_statements,
            ),
        }
    )
    template_case = {
        "name": candidate_id,
        "repo_path": f"{candidate_id}_repo",
        "sources": [_source_to_dict(source)],
        "mutations": [
            {
                "target_path": source.target_path,
                "find": find,
                "replace": replace,
                "count": 1,
                "description": (
                    f"Introduce a shared mutable default cache into {function_label}."
                ),
            }
        ],
        "files": files,
        "benchmark": {
            "buggy_functions": [function_label],
            "expected_rule_ids": ["mutable_default_arg"],
            "failing_tests": [test_name],
            "passed_tests": [],
            "test_args": [],
            "metadata": {
                "source": "github_raw_recipe_generation",
                "recipe": "mutable_default_arg",
                "bug_type": "state leakage",
                "upstream": _upstream_name(source),
                "upstream_ref": source.ref or "",
                "upstream_path": source.source_path or source.raw_url or "",
                "target_path": source.target_path,
                "license": source.license or "",
            },
        },
    }
    return {
        "id": candidate_id,
        "rule_ids": ["mutable_default_arg"],
        "bug_type": "state leakage",
        "failure_types": [
            "patch_apply_error",
            "runtime_error",
            "syntax_error",
            "test_failure",
        ],
        "benchmark_focuses": [
            "execution-evidence calibration",
            "judge false-positive hardening",
            "near-miss semantic repair",
            "state leakage regression",
        ],
        "patterns": [
            "capped_by_execution_evidence",
            "failure_type=patch_apply_error",
            "failure_type=runtime_error",
            "failure_type=syntax_error",
            "failure_type=test_failure",
        ],
        "source_summary": {
            "target_path": source.target_path,
            "function": function_label,
            "default_name": "_cache",
            "first_parameter": parameter_name,
        },
        "mutation_summary": {
            "description": template_case["mutations"][0]["description"],
        },
        "template_case": template_case,
    }


def _broad_exception_pass_candidates(
    source_text: str,
    source: FetchSource,
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    candidates = []
    lines = source_text.splitlines()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            if isinstance(node, ast.ClassDef) and _class_can_instantiate_without_args(
                node
            ):
                for statement in node.body:
                    if not isinstance(statement, ast.FunctionDef):
                        continue
                    if statement.name.startswith("__"):
                        continue
                    recipe = _broad_exception_pass_recipe(
                        statement,
                        lines,
                        source,
                        class_name=node.name,
                    )
                    if recipe is not None:
                        candidates.append(recipe)
            continue
        recipe = _broad_exception_pass_recipe(node, lines, source)
        if recipe is not None:
            candidates.append(recipe)
    return candidates


def _broad_exception_pass_recipe(
    node: ast.FunctionDef,
    lines: list[str],
    source: FetchSource,
    *,
    class_name: str | None = None,
) -> dict[str, Any] | None:
    first_parameter = _broad_exception_parameter_name(
        node,
        class_name=class_name,
    )
    if first_parameter is None:
        return None
    executable = _function_executable_body(node)
    if not executable:
        return None
    exception_name, call_argument, expectation = _broad_exception_oracle(
        node,
        first_parameter=first_parameter,
    )
    if not call_argument:
        return None
    start_line = executable[0].lineno
    end_line = executable[-1].end_lineno
    find = _source_segment(lines, start_line, end_line)
    if not find:
        return None
    body_indent = _line_indent(lines[start_line - 1])
    replace = _wrap_body_in_broad_exception_handler(find, body_indent)
    if not replace:
        return None
    module_name = _module_import_name(source.target_path)
    function_label = f"{class_name}.{node.name}" if class_name else node.name
    call_expression = (
        f"{class_name}().{node.name}"
        if class_name
        else node.name
    )
    test_name = (
        f"test_{_slug(class_name)}_{node.name}_broad_exception_pass"
        if class_name
        else f"test_{node.name}_broad_exception_pass"
    )
    candidate_id = (
        f"generated_{_slug(module_name)}_{_slug(function_label)}_broad_exception"
    )
    files = _package_init_overlay_files(source.target_path)
    files.append(
        {
            "target_path": f"test_{_slug(function_label)}_exception.py",
            "content": _broad_exception_test_content(
                module_name=module_name,
                import_name=class_name or node.name,
                test_name=test_name,
                call_argument=call_argument,
                exception_name=exception_name,
                expectation=expectation,
                call_expression=call_expression,
            ),
        }
    )
    template_case = {
        "name": candidate_id,
        "repo_path": f"{candidate_id}_repo",
        "sources": [_source_to_dict(source)],
        "mutations": [
            {
                "target_path": source.target_path,
                "find": find,
                "replace": replace,
                "count": 1,
                "description": (
                    f"Wrap {function_label}'s body in a broad exception handler "
                    "that silently passes."
                ),
            }
        ],
        "files": files,
        "benchmark": {
            "buggy_functions": [function_label],
            "expected_rule_ids": ["broad_exception_pass"],
            "failing_tests": [test_name],
            "passed_tests": [],
            "test_args": [],
            "metadata": {
                "source": "github_raw_recipe_generation",
                "recipe": "broad_exception_pass",
                "bug_type": "exception handling error",
                "upstream": _upstream_name(source),
                "upstream_ref": source.ref or "",
                "upstream_path": source.source_path or source.raw_url or "",
                "target_path": source.target_path,
                "license": source.license or "",
            },
        },
    }
    return {
        "id": candidate_id,
        "rule_ids": ["broad_exception_pass"],
        "function_name": function_label,
        "target_path": source.target_path,
        "bug_type": "exception handling error",
        "failure_types": [
            "patch_apply_error",
            "runtime_error",
            "syntax_error",
            "test_failure",
        ],
        "benchmark_focuses": [
            "exception propagation calibration",
            "execution-evidence calibration",
            "judge false-positive hardening",
            "near-miss semantic repair",
        ],
        "patterns": [
            "capped_by_execution_evidence",
            "failure_type=patch_apply_error",
            "failure_type=runtime_error",
            "failure_type=syntax_error",
            "failure_type=test_failure",
        ],
        "source_summary": {
            "target_path": source.target_path,
            "function": function_label,
            "first_parameter": first_parameter,
            "expected_exception": exception_name,
        },
        "mutation_summary": {
            "description": template_case["mutations"][0]["description"],
        },
        "template_case": template_case,
    }


def _always_true_len_check_candidates(
    source_text: str,
    source: FetchSource,
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    candidates = []
    lines = source_text.splitlines()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(node, ast.ClassDef) and _class_can_instantiate_without_args(
                node
            ):
                for statement in node.body:
                    if not isinstance(
                        statement,
                        (ast.FunctionDef, ast.AsyncFunctionDef),
                    ):
                        continue
                    if statement.name.startswith("__"):
                        continue
                    recipe = _always_true_len_check_recipe(
                        statement,
                        lines,
                        source,
                        class_name=node.name,
                    )
                    if recipe is not None:
                        candidates.append(recipe)
            continue
        recipe = _always_true_len_check_recipe(node, lines, source)
        if recipe is not None:
            candidates.append(recipe)
    return candidates


def _always_true_len_check_recipe(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    source: FetchSource,
    *,
    class_name: str | None = None,
) -> dict[str, Any] | None:
    for index, statement in enumerate(node.body):
        if not isinstance(statement, ast.If):
            continue
        guard_name = _empty_guard_name(statement.test)
        if not guard_name or statement.orelse or not statement.body:
            continue
        if _guard_name_is_regex_match_result(node.body[:index], guard_name):
            continue
        if _guard_name_is_parameter_method_result(node, node.body[:index], guard_name):
            continue
        following = node.body[index + 1 :]
        if not following:
            continue
        exception_name = _raised_exception_name(statement)
        fallback_returns_empty = _body_returns_empty_list(statement.body)
        if not exception_name and not fallback_returns_empty:
            continue
        find = _source_segment(lines, statement.lineno, following[-1].end_lineno)
        if not find:
            continue
        indent = _line_indent(lines[statement.lineno - 1])
        replace = _always_true_len_replacement(
            guard_name=guard_name,
            fallback_body=statement.body,
            main_body=following,
            lines=lines,
            indent=indent,
        )
        if not replace:
            continue
        module_name = _module_import_name(source.target_path)
        function_label = f"{class_name}.{node.name}" if class_name else node.name
        call_expression = (
            f"{class_name}().{node.name}"
            if class_name
            else node.name
        )
        test_name = (
            f"test_{_slug(class_name)}_{node.name}_always_true_len_check"
            if class_name
            else f"test_{node.name}_always_true_len_check"
        )
        assertion = _always_true_len_assertion(
            node=node,
            function_name=node.name,
            exception_name=exception_name,
            fallback_returns_empty=fallback_returns_empty,
            call_expression=call_expression,
            call_arguments=_empty_input_call_arguments(
                node,
                class_name=class_name,
                empty_name=guard_name,
            ),
        )
        if not assertion:
            continue
        candidate_id = (
            f"generated_{_slug(module_name)}_{_slug(function_label)}_always_true_len"
        )
        files = _package_init_overlay_files(source.target_path)
        files.append(
            {
                "target_path": f"test_{_slug(function_label)}_condition.py",
                "content": _always_true_len_test_content(
                    module_name=module_name,
                    import_name=class_name or node.name,
                    exception_name=exception_name,
                    test_name=test_name,
                    assertion=assertion,
                ),
            }
        )
        template_case = {
            "name": candidate_id,
            "repo_path": f"{candidate_id}_repo",
            "sources": [_source_to_dict(source)],
            "mutations": [
                {
                    "target_path": source.target_path,
                    "find": find,
                    "replace": replace,
                    "count": 1,
                    "description": (
                        f"Rewrite {function_label}'s empty-input guard into an "
                        "always-true len check."
                    ),
                }
            ],
            "files": files,
            "benchmark": {
                "buggy_functions": [function_label],
                "expected_rule_ids": ["always_true_len_check"],
                "failing_tests": [test_name],
                "passed_tests": [],
                "test_args": [],
                "metadata": {
                    "source": "github_raw_recipe_generation",
                    "recipe": "always_true_len_check",
                    "bug_type": "condition error",
                    "upstream": _upstream_name(source),
                    "upstream_ref": source.ref or "",
                    "upstream_path": source.source_path or source.raw_url or "",
                    "target_path": source.target_path,
                    "license": source.license or "",
                },
            },
        }
        return {
            "id": candidate_id,
            "rule_ids": ["always_true_len_check"],
            "function_name": function_label,
            "target_path": source.target_path,
            "bug_type": "condition error",
            "failure_types": [
                "patch_apply_error",
                "runtime_error",
                "syntax_error",
                "test_failure",
            ],
            "benchmark_focuses": [
                "condition-guard calibration",
                "execution-evidence calibration",
                "judge false-positive hardening",
                "near-miss semantic repair",
            ],
            "patterns": [
                "capped_by_execution_evidence",
                "failure_type=patch_apply_error",
                "failure_type=runtime_error",
                "failure_type=syntax_error",
                "failure_type=test_failure",
            ],
            "source_summary": {
                "target_path": source.target_path,
                "function": function_label,
                "guard_name": guard_name,
                "exception_name": exception_name,
                "fallback_returns_empty": fallback_returns_empty,
            },
            "mutation_summary": {
                "description": template_case["mutations"][0]["description"],
            },
            "template_case": template_case,
        }
    return None


def _inverted_empty_guard_candidates(
    source_text: str,
    source: FetchSource,
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    candidates = []
    lines = source_text.splitlines()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(node, ast.ClassDef) and _class_can_instantiate_without_args(
                node
            ):
                for statement in node.body:
                    if not isinstance(
                        statement,
                        (ast.FunctionDef, ast.AsyncFunctionDef),
                    ):
                        continue
                    if statement.name.startswith("__"):
                        continue
                    recipe = _inverted_empty_guard_recipe(
                        statement,
                        lines,
                        source,
                        class_name=node.name,
                    )
                    if recipe is not None:
                        candidates.append(recipe)
            continue
        recipe = _inverted_empty_guard_recipe(node, lines, source)
        if recipe is not None:
            candidates.append(recipe)
    return candidates


def _inverted_empty_guard_recipe(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    source: FetchSource,
    *,
    class_name: str | None = None,
) -> dict[str, Any] | None:
    call_expression = (
        f"{class_name}().{node.name}"
        if class_name
        else node.name
    )
    assertion = _non_empty_guard_assertion(
        node.name,
        call_expression=call_expression,
    )
    if not assertion:
        return None
    for statement in node.body:
        if not isinstance(statement, ast.If):
            continue
        guard_name = _empty_guard_name(statement.test)
        if not guard_name:
            continue
        exception_name = _raised_exception_name(statement)
        if exception_name not in {"ValueError", "StatisticsError"}:
            continue
        find = lines[statement.lineno - 1]
        replace = _inverted_empty_guard_header(find, guard_name)
        if not replace or replace == find:
            continue
        module_name = _module_import_name(source.target_path)
        function_label = f"{class_name}.{node.name}" if class_name else node.name
        test_name = (
            f"test_{_slug(class_name)}_{node.name}_non_empty_input_is_allowed"
            if class_name
            else f"test_{node.name}_non_empty_input_is_allowed"
        )
        candidate_id = (
            f"generated_{_slug(module_name)}_{_slug(function_label)}_inverted_empty_guard"
        )
        files = _package_init_overlay_files(source.target_path)
        files.append(
            {
                "target_path": f"test_{_slug(function_label)}_inverted_guard.py",
                "content": _simple_assertion_test_content(
                    module_name=module_name,
                    import_name=class_name or node.name,
                    test_name=test_name,
                    assertion=assertion,
                ),
            }
        )
        template_case = {
            "name": candidate_id,
            "repo_path": f"{candidate_id}_repo",
            "sources": [_source_to_dict(source)],
            "mutations": [
                {
                    "target_path": source.target_path,
                    "find": find,
                    "replace": replace,
                    "count": 1,
                    "description": (
                        f"Invert {function_label}'s empty-input guard so non-empty "
                        "input raises incorrectly."
                    ),
                }
            ],
            "files": files,
            "benchmark": {
                "buggy_functions": [function_label],
                "expected_rule_ids": ["inverted_empty_guard"],
                "failing_tests": [test_name],
                "passed_tests": [],
                "test_args": [],
                "metadata": {
                    "source": "github_raw_recipe_generation",
                    "recipe": "inverted_empty_guard",
                    "bug_type": "condition error",
                    "upstream": _upstream_name(source),
                    "upstream_ref": source.ref or "",
                    "upstream_path": source.source_path or source.raw_url or "",
                    "target_path": source.target_path,
                    "license": source.license or "",
                },
            },
        }
        return {
            "id": candidate_id,
            "rule_ids": ["inverted_empty_guard"],
            "function_name": function_label,
            "target_path": source.target_path,
            "bug_type": "condition error",
            "failure_types": [
                "patch_apply_error",
                "runtime_error",
                "syntax_error",
                "test_failure",
            ],
            "benchmark_focuses": [
                "condition-guard calibration",
                "execution-evidence calibration",
                "judge false-positive hardening",
                "near-miss semantic repair",
            ],
            "patterns": [
                "capped_by_execution_evidence",
                "failure_type=patch_apply_error",
                "failure_type=runtime_error",
                "failure_type=syntax_error",
                "failure_type=test_failure",
            ],
            "source_summary": {
                "target_path": source.target_path,
                "function": function_label,
                "guard_name": guard_name,
                "exception_name": exception_name,
            },
            "mutation_summary": {
                "description": template_case["mutations"][0]["description"],
            },
            "template_case": template_case,
        }
    return None


def _identity_comparison_literal_candidates(
    source_text: str,
    source: FetchSource,
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    candidates = []
    lines = source_text.splitlines()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            if isinstance(node, ast.ClassDef) and _class_can_instantiate_without_args(
                node
            ):
                for statement in node.body:
                    if not isinstance(statement, ast.FunctionDef):
                        continue
                    if statement.name.startswith("__"):
                        continue
                    recipe = _identity_comparison_literal_recipe(
                        statement,
                        lines=lines,
                        source_text=source_text,
                        source=source,
                        class_name=node.name,
                    )
                    if recipe is not None:
                        candidates.append(recipe)
            continue
        recipe = _identity_comparison_literal_recipe(
            node,
            lines=lines,
            source_text=source_text,
            source=source,
        )
        if recipe is not None:
            candidates.append(recipe)
    return candidates


def _identity_comparison_literal_recipe(
    node: ast.FunctionDef,
    lines: list[str],
    source_text: str,
    source: FetchSource,
    *,
    class_name: str | None = None,
) -> dict[str, Any] | None:
    parameter_name = _single_business_parameter_name(
        node,
        class_name=class_name,
    )
    if parameter_name is None:
        return None
    for statement in node.body:
        if not isinstance(statement, ast.Return):
            continue
        if not isinstance(statement.value, ast.Compare):
            continue
        comparison = _literal_equality_comparison_info(
            statement.value,
            parameter_name=parameter_name,
            source_text=source_text,
        )
        if comparison is None:
            continue
        find = _source_segment(lines, statement.lineno, statement.end_lineno)
        if not find:
            continue
        replace = find.replace(
            comparison["comparison_source"],
            comparison["mutated_comparison_source"],
            1,
        )
        if replace == find:
            continue
        module_name = _module_import_name(source.target_path)
        function_label = f"{class_name}.{node.name}" if class_name else node.name
        call_expression = (
            f"{class_name}().{node.name}"
            if class_name
            else node.name
        )
        test_name = (
            f"test_{_slug(class_name)}_{node.name}_uses_equality_not_identity"
            if class_name
            else f"test_{node.name}_uses_equality_not_identity"
        )
        candidate_id = (
            f"generated_{_slug(module_name)}_{_slug(function_label)}_identity_literal"
        )
        files = _package_init_overlay_files(source.target_path)
        files.append(
            {
                "target_path": f"test_{_slug(function_label)}_identity.py",
                "content": _identity_comparison_literal_test_content(
                    module_name=module_name,
                    import_name=class_name or node.name,
                    test_name=test_name,
                    literal=comparison["literal"],
                    expected_result=comparison["expected_result"],
                    call_expression=call_expression,
                ),
            }
        )
        template_case = {
            "name": candidate_id,
            "repo_path": f"{candidate_id}_repo",
            "sources": [_source_to_dict(source)],
            "mutations": [
                {
                    "target_path": source.target_path,
                    "find": find,
                    "replace": replace,
                    "count": 1,
                    "description": (
                        f"Change {function_label}'s literal equality comparison "
                        "into identity comparison."
                    ),
                }
            ],
            "files": files,
            "benchmark": {
                "buggy_functions": [function_label],
                "expected_rule_ids": ["identity_comparison_literal"],
                "failing_tests": [test_name],
                "passed_tests": [],
                "test_args": [],
                "metadata": {
                    "source": "github_raw_recipe_generation",
                    "recipe": "identity_comparison_literal",
                    "bug_type": "comparison semantics error",
                    "upstream": _upstream_name(source),
                    "upstream_ref": source.ref or "",
                    "upstream_path": source.source_path or source.raw_url or "",
                    "target_path": source.target_path,
                    "license": source.license or "",
                },
            },
        }
        return {
            "id": candidate_id,
            "rule_ids": ["identity_comparison_literal"],
            "function_name": function_label,
            "target_path": source.target_path,
            "bug_type": "comparison semantics error",
            "failure_types": [
                "patch_apply_error",
                "runtime_error",
                "syntax_error",
                "test_failure",
            ],
            "benchmark_focuses": [
                "comparison semantics calibration",
                "execution-evidence calibration",
                "judge false-positive hardening",
                "near-miss semantic repair",
            ],
            "patterns": [
                "capped_by_execution_evidence",
                "failure_type=patch_apply_error",
                "failure_type=runtime_error",
                "failure_type=syntax_error",
                "failure_type=test_failure",
                "literal_identity_comparison",
            ],
            "source_summary": {
                "target_path": source.target_path,
                "function": function_label,
                "parameter_name": parameter_name,
                "literal": comparison["literal"],
                "operator": comparison["operator"],
            },
            "mutation_summary": {
                "description": template_case["mutations"][0]["description"],
            },
            "template_case": template_case,
        }
    return None


def _iterator_double_consumption_candidates(
    source_text: str,
    source: FetchSource,
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    candidates = []
    lines = source_text.splitlines()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            if isinstance(node, ast.ClassDef) and _class_can_instantiate_without_args(
                node
            ):
                for statement in node.body:
                    if not isinstance(statement, ast.FunctionDef):
                        continue
                    if statement.name.startswith("__"):
                        continue
                    recipe = _iterator_double_consumption_recipe(
                        statement,
                        lines,
                        source,
                        class_name=node.name,
                    )
                    if recipe is not None:
                        candidates.append(recipe)
            continue
        recipe = _iterator_double_consumption_recipe(node, lines, source)
        if recipe is not None:
            candidates.append(recipe)
    return candidates


def _iterator_double_consumption_recipe(
    node: ast.FunctionDef,
    lines: list[str],
    source: FetchSource,
    *,
    class_name: str | None = None,
) -> dict[str, Any] | None:
    info = _iterator_materialization_average_info(node, class_name=class_name)
    if info is None:
        return None
    parameter_name = info["parameter_name"]
    snapshot = info["snapshot"]
    sum_statement = info["sum_statement"]
    count_statement = info["count_statement"]
    find = _source_segment(lines, snapshot.lineno, count_statement.end_lineno)
    if not find:
        return None
    replacement_lines = find.splitlines()
    if len(replacement_lines) < 2:
        return None
    replacement_lines = replacement_lines[1:]
    count_offset = count_statement.lineno - snapshot.lineno - 1
    if not (0 <= count_offset < len(replacement_lines)):
        return None
    replacement_lines[count_offset] = replacement_lines[count_offset].replace(
        f"len({parameter_name})",
        f"len(list({parameter_name}))",
        1,
    )
    replace = "\n".join(replacement_lines)
    if replace == find:
        return None
    module_name = _module_import_name(source.target_path)
    function_label = f"{class_name}.{node.name}" if class_name else node.name
    call_expression = (
        f"{class_name}().{node.name}"
        if class_name
        else node.name
    )
    test_name = (
        f"test_{_slug(class_name)}_{node.name}_iterator_not_consumed_twice"
        if class_name
        else f"test_{node.name}_iterator_not_consumed_twice"
    )
    candidate_id = (
        f"generated_{_slug(module_name)}_{_slug(function_label)}_iterator_double_consumption"
    )
    files = _package_init_overlay_files(source.target_path)
    files.append(
        {
            "target_path": f"test_{_slug(function_label)}_iterator.py",
            "content": _iterator_double_consumption_test_content(
                module_name=module_name,
                import_name=class_name or node.name,
                test_name=test_name,
                call_expression=call_expression,
            ),
        }
    )
    template_case = {
        "name": candidate_id,
        "repo_path": f"{candidate_id}_repo",
        "sources": [_source_to_dict(source)],
        "mutations": [
            {
                "target_path": source.target_path,
                "find": find,
                "replace": replace,
                "count": 1,
                "description": (
                    f"Remove {function_label}'s iterator materialization before "
                    "a repeated sum/len consumption path."
                ),
            }
        ],
        "files": files,
        "benchmark": {
            "buggy_functions": [function_label],
            "expected_rule_ids": ["iterator_double_consumption"],
            "failing_tests": [test_name],
            "passed_tests": [],
            "test_args": [],
            "metadata": {
                "source": "github_raw_recipe_generation",
                "recipe": "iterator_double_consumption",
                "bug_type": "iterator state error",
                "upstream": _upstream_name(source),
                "upstream_ref": source.ref or "",
                "upstream_path": source.source_path or source.raw_url or "",
                "target_path": source.target_path,
                "license": source.license or "",
            },
        },
    }
    return {
        "id": candidate_id,
        "rule_ids": ["iterator_double_consumption"],
        "function_name": function_label,
        "target_path": source.target_path,
        "bug_type": "iterator state error",
        "failure_types": [
            "patch_apply_error",
            "runtime_error",
            "syntax_error",
            "test_failure",
            "zero_division_error",
        ],
        "benchmark_focuses": [
            "data-flow order calibration",
            "execution-evidence calibration",
            "iterator runtime semantics",
            "near-miss semantic repair",
        ],
        "patterns": [
            "capped_by_execution_evidence",
            "failure_type=patch_apply_error",
            "failure_type=runtime_error",
            "failure_type=syntax_error",
            "failure_type=test_failure",
            "failure_type=zero_division_error",
            "iterator_consumed_before_length",
        ],
        "source_summary": {
            "target_path": source.target_path,
            "function": function_label,
            "parameter_name": parameter_name,
            "sum_line": sum_statement.lineno,
            "count_line": count_statement.lineno,
        },
        "mutation_summary": {
            "description": template_case["mutations"][0]["description"],
        },
        "template_case": template_case,
    }


def _enumerate_start_zero_counter_candidates(
    source_text: str,
    source: FetchSource,
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    candidates = []
    lines = source_text.splitlines()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(node, ast.ClassDef) and _class_can_instantiate_without_args(
                node
            ):
                for statement in node.body:
                    if not isinstance(
                        statement,
                        (ast.FunctionDef, ast.AsyncFunctionDef),
                    ):
                        continue
                    if statement.name.startswith("__"):
                        continue
                    recipe = _enumerate_start_zero_counter_recipe(
                        statement,
                        lines,
                        source,
                        class_name=node.name,
                    )
                    if recipe is not None:
                        candidates.append(recipe)
            continue
        recipe = _enumerate_start_zero_counter_recipe(node, lines, source)
        if recipe is not None:
            candidates.append(recipe)
    return candidates


def _enumerate_start_zero_counter_recipe(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    source: FetchSource,
    *,
    class_name: str | None = None,
) -> dict[str, Any] | None:
    call_expression = (
        f"{class_name}().{node.name}"
        if class_name
        else node.name
    )
    assertion = _enumerate_counter_assertion(
        node.name,
        call_expression=call_expression,
    )
    if not assertion:
        return None
    for child in ast.walk(node):
        if not isinstance(child, ast.For) or not _loop_body_yields(child.body):
            continue
        counter_name = _enumerate_one_based_counter(child)
        if not counter_name:
            continue
        find = _source_segment(lines, child.lineno, child.end_lineno)
        replace = _enumerate_start_zero_replacement(find)
        if not find or not replace or find == replace:
            continue
        module_name = _module_import_name(source.target_path)
        nested_function = _nested_function_name_for_line(node, child.lineno)
        qualified_function = (
            f"{class_name}.{nested_function}" if class_name else nested_function
        )
        test_name = (
            f"test_{_slug(class_name)}_{node.name}_enumerate_start_zero_counter"
            if class_name
            else f"test_{node.name}_enumerate_start_zero_counter"
        )
        candidate_id = (
            f"generated_{_slug(module_name)}_"
            f"{_slug(qualified_function)}_enumerate_start_zero"
        )
        files = _package_init_overlay_files(source.target_path)
        files.append(
            {
                "target_path": f"test_{_slug(qualified_function)}_counter.py",
                "content": _enumerate_counter_test_content(
                    module_name=module_name,
                    import_name=class_name or node.name,
                    test_name=test_name,
                    assertion=assertion,
                ),
            }
        )
        template_case = {
            "name": candidate_id,
            "repo_path": f"{candidate_id}_repo",
            "sources": [_source_to_dict(source)],
            "mutations": [
                {
                    "target_path": source.target_path,
                    "find": find,
                    "replace": replace,
                    "count": 1,
                    "description": (
                        f"Change {qualified_function}'s iterator item counter "
                        "from one-based to zero-based enumeration."
                    ),
                }
            ],
            "files": files,
            "benchmark": {
                "buggy_functions": [qualified_function],
                "expected_rule_ids": ["enumerate_start_zero_counter"],
                "failing_tests": [test_name],
                "passed_tests": [],
                "test_args": [],
                "metadata": {
                    "source": "github_raw_recipe_generation",
                    "recipe": "enumerate_start_zero_counter",
                    "bug_type": "off-by-one counting error",
                    "upstream": _upstream_name(source),
                    "upstream_ref": source.ref or "",
                    "upstream_path": source.source_path or source.raw_url or "",
                    "target_path": source.target_path,
                    "license": source.license or "",
                },
            },
        }
        return {
            "id": candidate_id,
            "rule_ids": ["enumerate_start_zero_counter"],
            "function_name": qualified_function,
            "target_path": source.target_path,
            "bug_type": "off-by-one counting error",
            "failure_types": [
                "patch_apply_error",
                "runtime_error",
                "syntax_error",
                "test_failure",
                "zero_division_error",
            ],
            "benchmark_focuses": [
                "execution-evidence calibration",
                "generator counter calibration",
                "judge false-positive hardening",
                "near-miss semantic repair",
            ],
            "patterns": [
                "capped_by_execution_evidence",
                "failure_type=patch_apply_error",
                "failure_type=runtime_error",
                "failure_type=syntax_error",
                "failure_type=test_failure",
                "failure_type=zero_division_error",
            ],
            "source_summary": {
                "target_path": source.target_path,
                "function": qualified_function,
                "counter_name": counter_name,
            },
            "mutation_summary": {
                "description": template_case["mutations"][0]["description"],
            },
            "template_case": template_case,
        }
    return None


def _len_assignments(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, str]:
    assignments = {}
    for child in ast.walk(node):
        if not isinstance(child, ast.Assign):
            continue
        if len(child.targets) != 1 or not isinstance(child.targets[0], ast.Name):
            continue
        if _is_len_call(child.value) and isinstance(child.value, ast.Call):
            argument = child.value.args[0]
            if isinstance(argument, ast.Name):
                assignments[child.targets[0].id] = argument.id
    return assignments


def _bounded_len_loop_info(
    node: ast.AST,
    len_assignments: dict[str, str],
) -> tuple[str, str, str] | None:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "range"
        and len(node.args) == 1
    ):
        return None
    bound = node.args[0]
    if not (
        isinstance(bound, ast.BinOp)
        and isinstance(bound.op, ast.Sub)
        and _number_value(bound.right) == 1
    ):
        return None
    collection_name = ""
    if _is_len_call(bound.left) and isinstance(bound.left, ast.Call):
        argument = bound.left.args[0]
        if isinstance(argument, ast.Name):
            collection_name = argument.id
    elif isinstance(bound.left, ast.Name):
        collection_name = len_assignments.get(bound.left.id, "")
    if not collection_name:
        return None
    return (
        collection_name,
        ast.unparse(node),
        f"range(len({collection_name}))",
    )


def _loop_reads_positive_offset(
    body: list[ast.stmt],
    collection_name: str,
    index_name: str,
) -> bool:
    for statement in body:
        for child in ast.walk(statement):
            if not isinstance(child, ast.Subscript):
                continue
            if not isinstance(child.value, ast.Name) or child.value.id != collection_name:
                continue
            if _is_positive_offset_index(child.slice, index_name):
                return True
    return False


def _is_positive_offset_index(node: ast.AST, index_name: str) -> bool:
    return (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Add)
        and isinstance(node.left, ast.Name)
        and node.left.id == index_name
        and isinstance(node.right, ast.Constant)
        and isinstance(node.right.value, int)
        and node.right.value > 0
    )


def _empty_guard_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        if isinstance(node.operand, ast.Name):
            return node.operand.id
        if _is_len_call(node.operand):
            assert isinstance(node.operand, ast.Call)
            if isinstance(node.operand.args[0], ast.Name):
                return node.operand.args[0].id
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return None
    left = node.left
    right = node.comparators[0]
    op = node.ops[0]
    if isinstance(op, (ast.Eq, ast.LtE)):
        if _number_value(right) == 0:
            return _name_or_len_name(left)
    if isinstance(op, (ast.Eq, ast.GtE)):
        if _number_value(left) == 0:
            return _name_or_len_name(right)
    return None


def _guard_name_is_regex_match_result(
    previous_statements: list[ast.stmt],
    guard_name: str,
) -> bool:
    for statement in reversed(previous_statements):
        value: ast.AST | None = None
        targets: list[ast.expr] = []
        if isinstance(statement, ast.Assign):
            value = statement.value
            targets = list(statement.targets)
        elif isinstance(statement, ast.AnnAssign):
            value = statement.value
            targets = [statement.target]
        else:
            continue
        if value is None or not _is_regex_match_call(value):
            continue
        if any(isinstance(target, ast.Name) and target.id == guard_name for target in targets):
            return True
    return False


def _guard_name_is_parameter_method_result(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    previous_statements: list[ast.stmt],
    guard_name: str,
) -> bool:
    parameter_names = _call_parameter_names(node)
    if guard_name in parameter_names:
        return False
    for statement in reversed(previous_statements):
        value: ast.AST | None = None
        targets: list[ast.expr] = []
        if isinstance(statement, ast.Assign):
            value = statement.value
            targets = list(statement.targets)
        elif isinstance(statement, ast.AnnAssign):
            value = statement.value
            targets = [statement.target]
        else:
            continue
        if value is None or not _is_parameter_method_call(value, parameter_names):
            continue
        if any(isinstance(target, ast.Name) and target.id == guard_name for target in targets):
            return True
    return False


def _call_parameter_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = {argument.arg for argument in node.args.posonlyargs}
    names.update(argument.arg for argument in node.args.args)
    names.update(argument.arg for argument in node.args.kwonlyargs)
    return names


def _is_parameter_method_call(node: ast.AST, parameter_names: set[str]) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in parameter_names
    )


def _is_regex_match_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr in {
        "fullmatch",
        "match",
        "search",
    }


def _raised_exception_name(node: ast.If) -> str:
    for statement in node.body:
        if not isinstance(statement, ast.Raise) or statement.exc is None:
            continue
        exc = statement.exc
        if isinstance(exc, ast.Call):
            exc = exc.func
        if isinstance(exc, ast.Name):
            return exc.id
    return ""


def _first_len_denominator_return(
    statements: list[ast.stmt],
    guard_name: str,
) -> ast.Return | None:
    for statement in statements:
        for child in ast.walk(statement):
            if not isinstance(child, ast.Return) or child.value is None:
                continue
            if _has_len_denominator(child.value, guard_name):
                return child
    return None


def _has_len_denominator(node: ast.AST, guard_name: str) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.BinOp):
            continue
        if not isinstance(child.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            continue
        if _contains_len_name(child.right, guard_name):
            return True
    return False


def _contains_len_name(node: ast.AST, guard_name: str) -> bool:
    return any(_is_len_name(child, guard_name) for child in ast.walk(node))


def _missing_guard_replacement(
    guard_name: str,
    return_node: ast.Return,
    indent: str,
) -> str:
    if return_node.value is None:
        return ""
    expression = ast.unparse(return_node.value)
    expression = re.sub(
        rf"\blen\(\s*{re.escape(guard_name)}\s*\)",
        "n",
        expression,
        count=1,
    )
    return "\n".join(
        [
            f"{indent}n = len({guard_name})",
            f"{indent}return {expression}",
        ]
    )


def _empty_input_test_content(
    module_name: str,
    import_name: str,
    exception_name: str,
    test_name: str,
    call_expression: str | None = None,
) -> str:
    call = call_expression or import_name
    if exception_name in {"ValueError", "TypeError", "ZeroDivisionError"}:
        import_block = _local_import_block(module_name, [import_name])
    else:
        import_block = _local_import_block(module_name, [exception_name, import_name])
    return (
        f"{import_block}"
        f"def {test_name}():\n"
        "    try:\n"
        f"        {call}([])\n"
        f"    except {exception_name}:\n"
        "        return\n"
        "    except Exception as exc:\n"
        "        raise AssertionError(\n"
        f"            f'expected {exception_name}, got {{type(exc).__name__}}'\n"
        "        ) from exc\n"
        f"    raise AssertionError('empty input should raise {exception_name}')\n"
    )


def _inverted_empty_guard_header(header: str, guard_name: str) -> str:
    replaced = re.sub(
        rf"if\s+not\s+{re.escape(guard_name)}\s*:",
        f"if {guard_name}:",
        header,
        count=1,
    )
    if replaced != header:
        return replaced
    replaced = re.sub(
        rf"if\s+not\s+len\(\s*{re.escape(guard_name)}\s*\)\s*:",
        f"if len({guard_name}) > 0:",
        header,
        count=1,
    )
    if replaced != header:
        return replaced
    replaced = re.sub(
        rf"if\s+len\(\s*{re.escape(guard_name)}\s*\)\s*==\s*0\s*:",
        f"if len({guard_name}) != 0:",
        header,
        count=1,
    )
    if replaced != header:
        return replaced
    return re.sub(
        rf"if\s+{re.escape(guard_name)}\s*==\s*0\s*:",
        f"if {guard_name} != 0:",
        header,
        count=1,
    )


def _non_empty_guard_assertion(
    function_name: str,
    call_expression: str | None = None,
) -> str:
    lower_name = function_name.lower()
    call = call_expression or function_name
    if lower_name in {"mean", "fmean"}:
        return f"assert {call}([1, 2, 3]) == 2"
    if lower_name == "median":
        return f"assert {call}([4, 1, 3, 2]) == 2.5"
    if "sort" in lower_name:
        return f"assert {call}([3, 1, 2]) == [1, 2, 3]"
    return ""


def _simple_assertion_test_content(
    module_name: str,
    import_name: str,
    test_name: str,
    assertion: str,
) -> str:
    return (
        f"{_local_import_block(module_name, [import_name])}"
        f"def {test_name}():\n"
        f"    {assertion}\n"
    )


def _index_overrun_test_content(
    module_name: str,
    import_name: str,
    function_name: str,
    call_expression: str,
    test_name: str,
) -> str:
    if "sort" in function_name.lower():
        assertion = f"assert {call_expression}([3, 2, 1]) == [1, 2, 3]"
    else:
        assertion = f"assert {call_expression}([1, 2, 3])[:2] == [2, 3]"
    return (
        f"{_local_import_block(module_name, [import_name])}"
        f"def {test_name}():\n"
        f"    {assertion}\n"
    )


def _mapping_get_default_info(
    node: ast.FunctionDef,
    source_text: str,
) -> dict[str, Any] | None:
    parameter_names = {argument.arg for argument in node.args.args}
    for statement in ast.walk(node):
        if not isinstance(statement, (ast.Return, ast.Assign)):
            continue
        for child in ast.walk(statement):
            if not isinstance(child, ast.Call):
                continue
            if not (
                isinstance(child.func, ast.Attribute)
                and child.func.attr == "get"
                and isinstance(child.func.value, ast.Name)
            ):
                continue
            if len(child.args) < 2:
                continue
            mapping_name = child.func.value.id
            if mapping_name not in parameter_names or not _looks_mapping_name(mapping_name):
                continue
            key = child.args[0]
            if not isinstance(key, ast.Name) or key.id not in parameter_names:
                continue
            default_value = _literal_default_value(child.args[1])
            if default_value is None:
                continue
            call_source = ast.get_source_segment(source_text, child) or ast.unparse(child)
            key_source = ast.get_source_segment(source_text, key) or ast.unparse(key)
            return {
                "statement": statement,
                "mapping_name": mapping_name,
                "key_name": key.id,
                "default_value": default_value,
                "call_source": call_source,
                "subscript_source": f"{mapping_name}[{key_source}]",
            }
    return None


def _literal_default_value(node: ast.AST) -> int | float | str | bool | None:
    if not isinstance(node, ast.Constant):
        return None
    if isinstance(node.value, (int, float, str, bool)):
        return node.value
    return None


def _dict_missing_key_guard_test_content(
    module_name: str,
    import_name: str,
    call_expression: str,
    test_name: str,
    default_value: int | float | str | bool,
) -> str:
    return (
        f"{_local_import_block(module_name, [import_name])}"
        f"def {test_name}():\n"
        f"    assert {call_expression}({{'alice': 3}}, 'missing') == {default_value!r}\n"
    )


def _sorted_assignment_info(node: ast.AST) -> tuple[str, str, str] | None:
    target_name = ""
    value: ast.AST | None = None
    if isinstance(node, ast.Assign):
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            return None
        target_name = node.targets[0].id
        value = node.value
    elif isinstance(node, ast.AnnAssign):
        if not isinstance(node.target, ast.Name) or node.value is None:
            return None
        target_name = node.target.id
        value = node.value
    if not (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "sorted"
        and value.args
        and isinstance(value.args[0], ast.Name)
    ):
        return None
    sort_arguments = [ast.unparse(argument) for argument in value.args[1:]]
    for keyword in value.keywords:
        if keyword.arg is None:
            sort_arguments.append(f"**{ast.unparse(keyword.value)}")
        else:
            sort_arguments.append(f"{keyword.arg}={ast.unparse(keyword.value)}")
    return target_name, value.args[0].id, ", ".join(sort_arguments)


def _inplace_sort_statement_info(node: ast.AST) -> tuple[str, str] | None:
    if not isinstance(node, ast.Expr):
        return None
    value = node.value
    if not (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr == "sort"
        and isinstance(value.func.value, ast.Name)
    ):
        return None
    sort_arguments = [ast.unparse(argument) for argument in value.args]
    for keyword in value.keywords:
        if keyword.arg is None:
            sort_arguments.append(f"**{ast.unparse(keyword.value)}")
        else:
            sort_arguments.append(f"{keyword.arg}={ast.unparse(keyword.value)}")
    return value.func.value.id, ", ".join(sort_arguments)


def _inplace_api_assertion(
    function_name: str,
    result_name: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    call_expression: str | None = None,
) -> str:
    lower_name = function_name.lower()
    call = call_expression or function_name
    if lower_name == "join_options":
        return f"assert {call}(['--long', '-s'])[0] == '-s, --long'"
    if "median" in lower_name:
        return f"assert {call}([4, 1, 3, 2]) == 2.5"
    if "sort" in lower_name or _function_returns_name(node, result_name):
        return f"assert {call}([3, 1, 2]) == [1, 2, 3]"
    return ""


def _function_returns_name(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
) -> bool:
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Return)
            and isinstance(child.value, ast.Name)
            and child.value.id == name
        ):
            return True
    return False


def _inplace_api_test_content(
    module_name: str,
    import_name: str,
    test_name: str,
    assertion: str,
) -> str:
    return (
        f"{_local_import_block(module_name, [import_name])}"
        f"def {test_name}():\n"
        f"    {assertion}\n"
    )


def _numeric_assignment_info(node: ast.AST) -> tuple[str, str] | None:
    if not isinstance(node, ast.Assign):
        return None
    if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
        return None
    if not _looks_stringifiable_numeric_expression(node.value):
        return None
    return node.targets[0].id, ast.unparse(node.value)


def _looks_stringifiable_numeric_expression(node: ast.AST) -> bool:
    if _is_len_call(node):
        return True
    if isinstance(node, ast.BinOp) and isinstance(
        node.op,
        (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod),
    ):
        return True
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return True
    return False


def _numeric_context_uses_name(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
) -> bool:
    visitor = _NumericContextUseVisitor(name)
    visitor.visit(node)
    return visitor.found


def _stringified_numeric_mutation_end_line(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    target_name: str,
    assignment_line: int,
) -> int:
    for statement in node.body:
        if getattr(statement, "lineno", 0) <= assignment_line:
            continue
        visitor = _NumericContextUseVisitor(target_name)
        visitor.visit(statement)
        if visitor.found:
            return getattr(statement, "end_lineno", None) or getattr(
                statement,
                "lineno",
                assignment_line,
            )
    return assignment_line


class _NumericContextUseVisitor(ast.NodeVisitor):
    def __init__(self, name: str) -> None:
        self.name = name
        self.found = False

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(
            node.op,
            (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod),
        ):
            if _is_name(node.left, self.name) or _is_name(node.right, self.name):
                self.found = True
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        values = [node.left, *node.comparators]
        if any(_is_numeric_constant(value) for value in values):
            if any(_is_name(value, self.name) for value in values):
                self.found = True
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _is_name(node.slice, self.name):
            self.found = True
        self.generic_visit(node)


def _stringified_numeric_assertion(
    function_name: str,
    target_name: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    call_expression: str | None = None,
) -> str:
    call = call_expression or function_name
    lower_name = function_name.lower()
    if "median" in lower_name:
        return f"assert {call}([4, 1, 3, 2]) == 2.5"
    if "sort" in lower_name:
        return f"assert {call}([3, 2, 1]) == [1, 2, 3]"
    if _function_uses_name_as_subscript_index(node, target_name):
        return f"assert {call}([1, 2, 3]) == 2"
    return ""


def _function_uses_name_as_subscript_index(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Subscript) and _is_name(child.slice, name):
            return True
    return False


def _stringified_numeric_test_content(
    module_name: str,
    import_name: str,
    test_name: str,
    assertion: str,
) -> str:
    return (
        f"{_local_import_block(module_name, [import_name])}"
        f"def {test_name}():\n"
        f"    {assertion}\n"
    )


def _mutable_default_header(
    header: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str:
    if node.lineno != getattr(node, "end_lineno", node.lineno) and "(" not in header:
        return ""
    parameter_text = header[header.find("(") + 1 : header.rfind(")")]
    if "*" in parameter_text or "_cache" in parameter_text:
        return ""
    match = re.search(r"\)(\s*(?:->.+)?):\s*$", header)
    if not match:
        return ""
    return f"{header[:match.start()]}, _cache=[]{header[match.start():]}"


def _first_executable_statement(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.stmt | None:
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return body[0] if body else None


def _mutable_default_parameter_name(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    class_name: str | None,
) -> str | None:
    if class_name is None:
        return node.args.args[0].arg if node.args.args else None
    if len(node.args.args) < 2:
        return None
    receiver_name = node.args.args[0].arg
    if receiver_name not in {"self", "cls"}:
        return None
    if node.name.startswith("__"):
        return None
    return node.args.args[1].arg


def _class_can_instantiate_without_args(node: ast.ClassDef) -> bool:
    for statement in node.body:
        if not isinstance(statement, ast.FunctionDef) or statement.name != "__init__":
            continue
        positional = [*statement.args.posonlyargs, *statement.args.args]
        positional = positional[1:]
        required_count = len(positional) - len(statement.args.defaults)
        required_keyword_only = [
            argument
            for argument, default in zip(
                statement.args.kwonlyargs,
                statement.args.kw_defaults,
            )
            if default is None
        ]
        return required_count <= 0 and not required_keyword_only
    return True


def _mutable_default_assertions(function_name: str) -> list[str]:
    lower_name = function_name.lower()
    if "sort" in lower_name:
        return [
            f"assert {function_name}([2, 1]) == [1, 2]",
            f"assert {function_name}([3, 1, 2]) == [1, 2, 3]",
        ]
    if "median" in lower_name:
        return [
            f"assert {function_name}([1]) == 1",
            f"assert {function_name}([4, 1, 3, 2]) == 2.5",
        ]
    if "mean" in lower_name:
        return [
            f"assert {function_name}([1]) == 1",
            f"assert {function_name}([2]) == 2",
        ]
    return []


def _mutable_default_method_statements(
    *,
    class_name: str,
    method_name: str,
    parameter_name: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    if method_name != "get":
        return []
    if parameter_name not in {"name", "tag", "key"}:
        return []
    if not _method_return_can_expose_tags(node, parameter_name):
        return []
    return [
        f"instance = {class_name}()",
        f'assert instance.{method_name}("first").tags == ("first",)',
        f'assert instance.{method_name}("second").tags == ("second",)',
    ]


def _method_return_can_expose_tags(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    parameter_name: str,
) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Return) or child.value is None:
            continue
        names = {
            name.id
            for name in ast.walk(child.value)
            if isinstance(name, ast.Name)
        }
        if parameter_name not in names:
            continue
        if any(
            isinstance(attribute, ast.Attribute) and attribute.attr == "tags"
            for attribute in ast.walk(child.value)
        ):
            return True
        if any(
            isinstance(call, ast.Call)
            and _call_name(call.func).lower().endswith("tracersub")
            for call in ast.walk(child.value)
        ):
            return True
    return False


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _mutable_default_test_content(
    module_name: str,
    import_name: str,
    test_name: str,
    statements: list[str],
) -> str:
    body = "\n".join(f"    {statement}" for statement in statements)
    return (
        f"{_local_import_block(module_name, [import_name])}"
        f"def {test_name}():\n"
        f"{body}\n"
    )


def _function_executable_body(node: ast.FunctionDef) -> list[ast.stmt]:
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return body


def _broad_exception_parameter_name(
    node: ast.FunctionDef,
    *,
    class_name: str | None,
) -> str | None:
    if class_name is None:
        return node.args.args[0].arg if len(node.args.args) == 1 else None
    if len(node.args.args) != 2:
        return None
    receiver_name = node.args.args[0].arg
    if receiver_name not in {"self", "cls"}:
        return None
    return node.args.args[1].arg


def _broad_exception_oracle(
    node: ast.FunctionDef,
    first_parameter: str,
) -> tuple[str, str, str]:
    exception_name = _first_raised_exception_name(node)
    if exception_name in {"ValueError", "StatisticsError"}:
        return exception_name, "[]", f"empty {first_parameter} should raise"
    return "Exception", "[1, object()]", f"invalid {first_parameter} should raise"


def _first_raised_exception_name(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    for child in ast.walk(node):
        if not isinstance(child, ast.Raise) or child.exc is None:
            continue
        exc = child.exc
        if isinstance(exc, ast.Call):
            exc = exc.func
        if isinstance(exc, ast.Name):
            return exc.id
    return ""


def _wrap_body_in_broad_exception_handler(find: str, body_indent: str) -> str:
    inner_indent = f"{body_indent}    "
    try_lines = [f"{body_indent}try:"]
    for line in find.splitlines():
        if line.strip():
            try_lines.append(f"{inner_indent}{line[len(body_indent):]}")
        else:
            try_lines.append("")
    try_lines.extend(
        [
            f"{body_indent}except Exception:",
            f"{inner_indent}pass",
        ]
    )
    return "\n".join(try_lines)


def _broad_exception_test_content(
    module_name: str,
    import_name: str,
    test_name: str,
    call_argument: str,
    exception_name: str,
    expectation: str,
    call_expression: str | None = None,
) -> str:
    call = call_expression or import_name
    return (
        f"{_local_import_block(module_name, [import_name])}"
        f"def {test_name}():\n"
        "    try:\n"
        f"        {call}({call_argument})\n"
        f"    except {exception_name}:\n"
        "        return\n"
        "    except Exception as exc:\n"
        "        raise AssertionError(\n"
        f"            f'expected {exception_name}, got {{type(exc).__name__}}'\n"
        "        ) from exc\n"
        f"    raise AssertionError('{expectation}')\n"
    )


def _body_returns_empty_list(body: list[ast.stmt]) -> bool:
    for statement in body:
        if (
            isinstance(statement, ast.Return)
            and isinstance(statement.value, ast.List)
            and not statement.value.elts
        ):
            return True
    return False


def _always_true_len_replacement(
    guard_name: str,
    fallback_body: list[ast.stmt],
    main_body: list[ast.stmt],
    lines: list[str],
    indent: str,
) -> str:
    body_indent = f"{indent}    "
    replacement = [f"{indent}if len({guard_name}) >= 0:"]
    main_segment = _source_segment(
        lines,
        main_body[0].lineno,
        main_body[-1].end_lineno,
    )
    fallback_segment = _source_segment(
        lines,
        fallback_body[0].lineno,
        fallback_body[-1].end_lineno,
    )
    if not main_segment or not fallback_segment:
        return ""
    for line in main_segment.splitlines():
        if line.strip():
            replacement.append(f"{body_indent}{line[len(indent):]}")
        else:
            replacement.append("")
    replacement.append(f"{indent}else:")
    replacement.extend(fallback_segment.splitlines())
    return "\n".join(replacement)


def _always_true_len_assertion(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    function_name: str,
    exception_name: str,
    fallback_returns_empty: bool,
    call_expression: str | None = None,
    call_arguments: str | None = None,
) -> str:
    call = call_expression or function_name
    args = call_arguments or "[]"
    if exception_name:
        return _empty_input_exception_assertion(call, exception_name, args)
    if fallback_returns_empty:
        return f"assert {call}({args}) == []"
    return ""


def _empty_input_exception_assertion(
    function_name: str,
    exception_name: str,
    call_arguments: str = "[]",
) -> str:
    return (
        "try:\n"
        f"    {function_name}({call_arguments})\n"
        f"except {exception_name}:\n"
        "    return\n"
        "except Exception as exc:\n"
        "    raise AssertionError(\n"
        f"        f'expected {exception_name}, got {{type(exc).__name__}}'\n"
        "    ) from exc\n"
        f"raise AssertionError('empty input should raise {exception_name}')"
    )


def _empty_input_call_arguments(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    class_name: str | None,
    empty_name: str,
) -> str:
    positional = list(node.args.args)
    if class_name and positional and positional[0].arg in {"self", "cls"}:
        positional = positional[1:]
    required_count = max(0, len(positional) - len(node.args.defaults))
    required = positional[:required_count]
    if not required:
        return "[]"
    empty_index = next(
        (index for index, argument in enumerate(required) if argument.arg == empty_name),
        None,
    )
    if empty_index is None:
        empty_index = 0
    empty_value = _empty_call_value_for_parameter(required[empty_index].arg)
    arguments = [
        empty_value if index == empty_index else _safe_call_placeholder(argument.arg)
        for index, argument in enumerate(required)
    ]
    for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        if default is not None:
            continue
        value = (
            _empty_call_value_for_parameter(argument.arg)
            if argument.arg == empty_name
            else _safe_call_placeholder(argument.arg)
        )
        arguments.append(f"{argument.arg}={value}")
    return ", ".join(arguments) or "[]"


def _empty_call_value_for_parameter(name: str) -> str:
    lower_name = name.lower()
    if any(
        token in lower_name
        for token in (
            "items",
            "values",
            "list",
            "seq",
            "nums",
            "numbers",
            "rows",
        )
    ):
        return "[]"
    if any(token in lower_name for token in ("headers", "mapping", "dict")):
        return "{}"
    if any(
        token in lower_name
        for token in (
            "url",
            "uri",
            "path",
            "host",
            "scheme",
            "domain",
            "text",
            "string",
            "name",
        )
    ):
        return '""'
    return "[]"


def _safe_call_placeholder(name: str) -> str:
    lower_name = name.lower()
    if "param" in lower_name or "query" in lower_name:
        return "None"
    if "headers" in lower_name or "mapping" in lower_name or "dict" in lower_name:
        return "{}"
    if "items" in lower_name or "values" in lower_name or "list" in lower_name:
        return "[]"
    if "flag" in lower_name or lower_name.startswith("is_"):
        return "False"
    return "None"


def _always_true_len_test_content(
    module_name: str,
    import_name: str,
    exception_name: str,
    test_name: str,
    assertion: str,
) -> str:
    indented_assertion = "\n".join(
        f"    {line}" if line else "" for line in assertion.splitlines()
    )
    import_names = [import_name]
    if exception_name and exception_name not in {
        "ValueError",
        "TypeError",
        "ZeroDivisionError",
    }:
        import_names.insert(0, exception_name)
    return (
        f"{_local_import_block(module_name, import_names)}"
        f"def {test_name}():\n"
        f"{indented_assertion}\n"
    )


def _enumerate_one_based_counter(node: ast.For) -> str | None:
    if not isinstance(node.target, ast.Tuple) or not node.target.elts:
        return None
    counter = node.target.elts[0]
    if not isinstance(counter, ast.Name):
        return None
    call = node.iter
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "enumerate"
    ):
        return None
    for keyword in call.keywords:
        if keyword.arg == "start" and _number_value(keyword.value) == 1:
            return counter.id
    if len(call.args) >= 2 and _number_value(call.args[1]) == 1:
        return counter.id
    return None


def _loop_body_yields(body: list[ast.stmt]) -> bool:
    return any(
        isinstance(node, (ast.Yield, ast.YieldFrom))
        for statement in body
        for node in ast.walk(statement)
    )


def _enumerate_start_zero_replacement(source: str) -> str:
    replaced = re.sub(
        r"enumerate\(([^)]*?),\s*start\s*=\s*1\)",
        r"enumerate(\1, start=0)",
        source,
        count=1,
    )
    if replaced != source:
        return replaced
    return re.sub(
        r"enumerate\(([^)]*?),\s*1\)",
        r"enumerate(\1, 0)",
        source,
        count=1,
    )


def _enumerate_counter_assertion(
    function_name: str,
    call_expression: str | None = None,
) -> str:
    lower_name = function_name.lower()
    if "mean" not in lower_name and "average" not in lower_name:
        return ""
    call = call_expression or function_name
    return f"assert {call}(one_item_generator()) == 4.0"


def _enumerate_counter_test_content(
    module_name: str,
    import_name: str,
    test_name: str,
    assertion: str,
) -> str:
    return (
        f"{_local_import_block(module_name, [import_name])}"
        "def one_item_generator():\n"
        "    yield 4.0\n\n\n"
        f"def {test_name}():\n"
        f"    {assertion}\n"
    )


def _iterator_materialization_average_info(
    node: ast.FunctionDef,
    *,
    class_name: str | None = None,
) -> dict[str, Any] | None:
    parameter_name = _iterator_materialization_parameter_name(
        node,
        class_name=class_name,
    )
    if parameter_name is None:
        return None
    body = _function_executable_body(node)
    for index, statement in enumerate(body):
        if not _is_parameter_list_assignment(statement, parameter_name):
            continue
        sum_statement: ast.Assign | None = None
        count_statement: ast.Assign | None = None
        count_name = ""
        for later in body[index + 1 :]:
            if sum_statement is None and _assignment_calls_name(
                later,
                function_name="sum",
                argument_name=parameter_name,
            ):
                assert isinstance(later, ast.Assign)
                sum_statement = later
                continue
            if sum_statement is None:
                continue
            count_name = _len_assignment_target(later, parameter_name)
            if count_name:
                assert isinstance(later, ast.Assign)
                count_statement = later
                break
        if sum_statement is None or count_statement is None or not count_name:
            continue
        if not _later_return_divides_by_name(body, count_statement, count_name):
            continue
        return {
            "parameter_name": parameter_name,
            "snapshot": statement,
            "sum_statement": sum_statement,
            "count_statement": count_statement,
            "count_name": count_name,
        }
    return None


def _iterator_materialization_parameter_name(
    node: ast.FunctionDef,
    *,
    class_name: str | None,
) -> str | None:
    if class_name is None:
        return node.args.args[0].arg if len(node.args.args) == 1 else None
    if len(node.args.args) != 2:
        return None
    receiver_name = node.args.args[0].arg
    if receiver_name not in {"self", "cls"}:
        return None
    return node.args.args[1].arg


def _is_parameter_list_assignment(node: ast.stmt, parameter_name: str) -> bool:
    if not isinstance(node, ast.Assign):
        return False
    if len(node.targets) != 1 or not _is_name(node.targets[0], parameter_name):
        return False
    return _is_call_of_name(node.value, "list", parameter_name)


def _assignment_calls_name(
    node: ast.stmt,
    function_name: str,
    argument_name: str,
) -> bool:
    if not isinstance(node, ast.Assign):
        return False
    return _is_call_of_name(node.value, function_name, argument_name)


def _len_assignment_target(node: ast.stmt, parameter_name: str) -> str:
    if not isinstance(node, ast.Assign):
        return ""
    if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
        return ""
    if _is_len_name(node.value, parameter_name):
        return node.targets[0].id
    return ""


def _later_return_divides_by_name(
    body: list[ast.stmt],
    count_statement: ast.Assign,
    count_name: str,
) -> bool:
    for statement in body:
        if getattr(statement, "lineno", 0) <= getattr(count_statement, "lineno", 0):
            continue
        for child in ast.walk(statement):
            if isinstance(child, ast.Return) and child.value is not None:
                if _expression_divides_by_name(child.value, count_name):
                    return True
    return False


def _expression_divides_by_name(node: ast.AST, name: str) -> bool:
    for child in ast.walk(node):
        if (
            isinstance(child, ast.BinOp)
            and isinstance(child.op, (ast.Div, ast.FloorDiv))
            and _is_name(child.right, name)
        ):
            return True
    return False


def _is_call_of_name(node: ast.AST, function_name: str, argument_name: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == function_name
        and len(node.args) == 1
        and _is_name(node.args[0], argument_name)
    )


def _iterator_double_consumption_test_content(
    module_name: str,
    import_name: str,
    test_name: str,
    call_expression: str | None = None,
) -> str:
    call = call_expression or import_name
    return (
        f"{_local_import_block(module_name, [import_name])}"
        "def one_two_three():\n"
        "    yield 1\n"
        "    yield 2\n"
        "    yield 3\n\n\n"
        f"def {test_name}():\n"
        f"    assert {call}(one_two_three()) == 2\n"
    )


def _literal_equality_comparison_info(
    node: ast.Compare,
    parameter_name: str,
    source_text: str,
) -> dict[str, Any] | None:
    if len(node.ops) != 1 or len(node.comparators) != 1:
        return None
    op = node.ops[0]
    if not isinstance(op, (ast.Eq, ast.NotEq)):
        return None
    left = node.left
    right = node.comparators[0]
    literal = ""
    if _is_name(left, parameter_name):
        literal = _string_literal_value(right)
    elif _is_name(right, parameter_name):
        literal = _string_literal_value(left)
    if len(literal) < 2:
        return None
    comparison_source = ast.get_source_segment(source_text, node) or ast.unparse(node)
    left_source = ast.get_source_segment(source_text, left) or ast.unparse(left)
    right_source = ast.get_source_segment(source_text, right) or ast.unparse(right)
    identity_operator = "is not" if isinstance(op, ast.NotEq) else "is"
    return {
        "literal": literal,
        "operator": "!=" if isinstance(op, ast.NotEq) else "==",
        "comparison_source": comparison_source,
        "mutated_comparison_source": (
            f"{left_source} {identity_operator} {right_source}"
        ),
        "expected_result": isinstance(op, ast.Eq),
    }


def _string_literal_value(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _single_business_parameter_name(
    node: ast.FunctionDef,
    *,
    class_name: str | None,
) -> str | None:
    if class_name is None:
        return node.args.args[0].arg if len(node.args.args) == 1 else None
    if len(node.args.args) != 2:
        return None
    receiver_name = node.args.args[0].arg
    if receiver_name not in {"self", "cls"}:
        return None
    return node.args.args[1].arg


def _identity_comparison_literal_test_content(
    module_name: str,
    import_name: str,
    test_name: str,
    literal: str,
    expected_result: bool,
    call_expression: str | None = None,
) -> str:
    expected = "True" if expected_result else "False"
    literal_repr = repr(literal)
    dynamic_value = _dynamic_string_literal_expression(literal)
    call = call_expression or import_name
    return (
        f"{_local_import_block(module_name, [import_name])}"
        f"def {test_name}():\n"
        f"    literal = {literal_repr}\n"
        f"    value = {dynamic_value}\n"
        "    assert value == literal\n"
        "    assert value is not literal\n"
        f"    assert {call}(value) is {expected}\n"
    )


def _dynamic_string_literal_expression(literal: str) -> str:
    split_at = max(1, len(literal) // 2)
    return f"''.join([{literal[:split_at]!r}, {literal[split_at:]!r}])"


def _nested_function_name_for_line(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    line: int,
) -> str:
    best = node.name
    best_start = getattr(node, "lineno", 0)
    for child in ast.walk(node):
        if child is node or not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = getattr(child, "lineno", 0)
        end = getattr(child, "end_lineno", 0)
        if start <= line <= end and start >= best_start:
            best = f"{node.name}.{child.name}"
            best_start = start
    return best


def _source_segment(lines: list[str], start_line: int, end_line: int | None) -> str:
    if end_line is None:
        return ""
    return "\n".join(lines[start_line - 1 : end_line])


def _line_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _name_or_len_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if _is_len_call(node):
        assert isinstance(node, ast.Call)
        if isinstance(node.args[0], ast.Name):
            return node.args[0].id
    return None


def _is_len_name(node: ast.AST, name: str) -> bool:
    return (
        _is_len_call(node)
        and isinstance(node, ast.Call)
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == name
    )


def _is_len_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "len"
        and len(node.args) == 1
    )


def _number_value(node: ast.AST) -> int | float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    return None


def _is_name(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _is_numeric_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float))


def _looks_mapping_name(name: str) -> bool:
    lowered = name.lower()
    return any(
        token in lowered
        for token in (
            "dict",
            "map",
            "mapping",
            "lookup",
            "table",
            "score",
            "weight",
            "count",
            "cache",
        )
    )


def _extract_sources(payload: dict[str, Any]) -> list[FetchSource]:
    sources = payload.get("sources", [])
    if not isinstance(sources, list):
        return []
    return [source_from_dict(source) for source in sources if isinstance(source, dict)]


def _source_to_dict(source: FetchSource) -> dict[str, Any]:
    data = {
        "target_path": source.target_path,
        "raw_url": source.raw_url,
        "owner": source.owner,
        "repo": source.repo,
        "ref": source.ref,
        "source_path": source.source_path,
        "sha256": source.sha256,
        "license": source.license,
    }
    return {key: value for key, value in data.items() if value}


def _module_import_name(target_path: str) -> str:
    path = PurePosixPath(str(target_path).replace("\\", "/"))
    if path.suffix == ".py":
        parts = list(path.with_suffix("").parts)
        if parts and all(part.isidentifier() for part in parts):
            return ".".join(parts)
    return Path(target_path).stem


def _local_import_block(module_name: str, import_names: list[str]) -> str:
    names = ", ".join(import_names)
    return f"{_local_import_preamble(module_name)}from {module_name} import {names}\n\n\n"


def _local_import_preamble(module_name: str) -> str:
    parts = [part for part in module_name.split(".") if part]
    if not parts or not all(part.isidentifier() for part in parts):
        return ""
    module_chain = tuple(".".join(parts[:index]) for index in range(1, len(parts) + 1))
    return (
        "import sys\n"
        "from pathlib import Path\n\n"
        "__CIA_REPO_ROOT = Path(__file__).resolve().parent\n"
        "if str(__CIA_REPO_ROOT) not in sys.path:\n"
        "    sys.path.insert(0, str(__CIA_REPO_ROOT))\n"
        f"__CIA_IMPORT_MODULES = {module_chain!r}\n"
        "for __CIA_MODULE_NAME in __CIA_IMPORT_MODULES:\n"
        "    __CIA_MODULE = sys.modules.get(__CIA_MODULE_NAME)\n"
        "    if __CIA_MODULE is None:\n"
        "        continue\n"
        "    __CIA_FILE = getattr(__CIA_MODULE, '__file__', '')\n"
        "    if not __CIA_FILE or not str(Path(__CIA_FILE).resolve()).startswith(str(__CIA_REPO_ROOT)):\n"
        "        sys.modules.pop(__CIA_MODULE_NAME, None)\n\n"
    )


def _package_init_overlay_files(target_path: str) -> list[dict[str, str]]:
    path = PurePosixPath(str(target_path).replace("\\", "/"))
    package_parts = list(path.parent.parts)
    if not package_parts or not all(part.isidentifier() for part in package_parts):
        return []
    overlays = []
    current: list[str] = []
    for part in package_parts:
        current.append(part)
        overlays.append(
            {
                "target_path": "/".join([*current, "__init__.py"]),
                "content": "",
            }
        )
    return overlays


def _validate_sha256(content: bytes, source: FetchSource) -> None:
    if not source.sha256:
        return
    digest = hashlib.sha256(content).hexdigest()
    if digest != source.sha256:
        raise ValueError(
            f"sha256 mismatch for {source.target_path}: expected "
            f"{source.sha256}, got {digest}"
        )


def _upstream_name(source: FetchSource) -> str:
    if source.owner and source.repo:
        return f"{source.owner}/{source.repo}"
    return source.raw_url or ""


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "unknown"


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate benchmark candidate recipes from GitHub/raw source candidates."
        )
    )
    parser.add_argument("sources", help="JSON file containing a sources array")
    parser.add_argument(
        "--recipe",
        choices=sorted(SUPPORTED_RECIPES),
        default="missing_len_zero_guard",
        help="Recipe family to generate.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format.",
    )
    parser.add_argument("--output-json", help="Optional full report JSON path.")
    parser.add_argument("--output-markdown", help="Optional markdown report path.")
    parser.add_argument("--output-catalog", help="Optional catalog JSON path.")
    parser.add_argument("--output-template", help="Optional template JSON path.")
    parser.add_argument(
        "--source-cache-dir",
        help=(
            "Optional shared raw-source cache directory used before network fetch."
        ),
    )
    args = parser.parse_args()

    report = generate_benchmark_recipes(
        load_json(args.sources),
        recipe=args.recipe,
        source_path=str(args.sources),
        source_cache_dir=Path(args.source_cache_dir)
        if args.source_cache_dir
        else None,
    )
    payload = report.to_dict()
    json_report = json.dumps(payload, indent=2, ensure_ascii=False)
    markdown_report = render_recipe_generation_markdown(report)
    if args.output_json:
        Path(args.output_json).write_text(json_report, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown_report, encoding="utf-8")
    if args.output_catalog:
        Path(args.output_catalog).write_text(
            json.dumps(payload["catalog"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if args.output_template:
        Path(args.output_template).write_text(
            json.dumps(payload["template"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if args.format == "markdown":
        print(markdown_report)
    else:
        print(json_report)


if __name__ == "__main__":
    main()
