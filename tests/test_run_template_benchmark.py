import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.run_template_benchmark import (
    run_template_benchmark,
)


def test_run_template_benchmark_validates_materializes_and_runs_report():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        template = _write_shift_left_template(root)
        output = root / "generated"

        result = run_template_benchmark(
            template_path=template,
            output_dir=output,
            use_dynamic_coverage=False,
        )

        report = result["benchmark_report"]

        assert result["template_validation"]["is_valid"] is True
        assert result["manifest_validation"]["is_valid"] is True
        assert Path(result["manifest_path"]).exists()
        assert Path(result["report_artifacts"]["json"]).exists()
        assert Path(result["report_artifacts"]["markdown"]).exists()
        artifact_payload = json.loads(
            Path(result["report_artifacts"]["json"]).read_text(encoding="utf-8")
        )
        assert artifact_payload["summary"]["patch_success_rate"] == 1.0
        assert report.top1 == 1.0
        assert report.map == 1.0
        assert report.patch_success_rate == 1.0
        assert report.beam_success_rate == 1.0
        assert report.hypothesis_top1 == 1.0
        assert report.hypothesis_mrr == 1.0
        assert report.hypothesis_map == 1.0
        assert report.program_slice_case_count == 1
        assert report.average_top1_slice_edges > 0.0
        assert report.slice_grounded_case_count == 1
        assert report.average_top1_slice_support > 0.0
        assert report.cases[0].best_patch_rule_id == "possible_index_overrun"
        assert report.cases[0].hypothesis_results[0]["function_name"] == "shift_left"
        assert report.cases[0].localization_details[0]["program_slice"][
            "edge_count"
        ] > 0
        assert report.cases[0].localization_details[0]["slice_grounding"][
            "grounded"
        ] is True


def test_run_template_benchmark_persists_repository_test_evidence():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        template = _write_shift_left_template(root)
        output = root / "generated"
        repository_test_evidence = _repository_test_evidence()

        result = run_template_benchmark(
            template_path=template,
            output_dir=output,
            use_dynamic_coverage=False,
            repository_test_evidence=repository_test_evidence,
        )
        manifest_payload = json.loads(
            Path(result["manifest_path"]).read_text(encoding="utf-8")
        )
        report_payload = json.loads(
            Path(result["report_artifacts"]["json"]).read_text(encoding="utf-8")
        )
        report_markdown = Path(result["report_artifacts"]["markdown"]).read_text(
            encoding="utf-8"
        )

        assert manifest_payload["repository_test_evidence"] == repository_test_evidence
        assert report_payload["repository_test_evidence"] == repository_test_evidence
        assert result["benchmark_report"].repository_test_evidence == (
            repository_test_evidence
        )
        assert "Repository Test Evidence" in report_markdown
        assert "direct_function: shift_left([1]) -> shift_left" in report_markdown


def test_run_template_benchmark_traces_cross_file_raw_source_case():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        template = _write_cross_file_mean_template(root)
        output = root / "generated"

        result = run_template_benchmark(
            template_path=template,
            output_dir=output,
        )

        report = result["benchmark_report"]
        case = report.cases[0]
        details = case.localization_details[0]

        assert result["template_validation"]["is_valid"] is True
        assert result["manifest_validation"]["is_valid"] is True
        assert report.top1 == 1.0
        assert report.patch_success_rate == 1.0
        assert case.coverage_mode == "dynamic_trace"
        assert case.best_patch_rule_id == "missing_len_zero_guard"
        assert case.ranked_functions[0] == "mean"
        assert details["function_name"] == "mean"
        assert details["failed_covered"] == 1
        assert details["graph_components"]["test_coverage"] == 1.0
        assert details["call_chain"] == [
            "test_empty_average_raises_value_error",
            "compute_average",
            "mean",
        ]
        assert details["program_slice"]["incoming_callers"] == [
            "compute_average"
        ]
        assert details["program_slice"]["module_dependency_edge_count"] >= 1
        assert details["slice_grounding"]["failed_test_reachability"] == 1.0
        assert details["slice_grounding"]["call_chain_edge_coverage"] == 1.0


def test_run_template_benchmark_cli_outputs_json_report():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        template = _write_shift_left_template(root)
        output = root / "generated"

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.run_template_benchmark",
                str(template),
                str(output),
                "--format",
                "json",
                "--no-dynamic-coverage",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(completed.stdout)

        assert completed.returncode == 0
        assert payload["template_validation"]["is_valid"] is True
        assert payload["manifest_validation"]["is_valid"] is True
        assert Path(payload["report_artifacts"]["json"]).exists()
        assert Path(payload["report_artifacts"]["markdown"]).exists()
        assert payload["benchmark_report"]["summary"]["patch_success_rate"] == 1.0
        assert payload["benchmark_report"]["summary"]["map"] == 1.0
        assert payload["benchmark_report"]["summary"]["hypothesis_top1"] == 1.0
        assert payload["benchmark_report"]["summary"]["hypothesis_mrr"] == 1.0
        assert payload["benchmark_report"]["summary"]["hypothesis_map"] == 1.0
        assert payload["benchmark_report"]["summary"]["slice_grounded_case_count"] == 1
        assert payload["benchmark_report"]["cases"][0]["best_patch_rule_id"] == (
            "possible_index_overrun"
        )
        assert payload["benchmark_report"]["cases"][0]["hypothesis_results"][0][
            "function_name"
        ] == "shift_left"


def _repository_test_evidence():
    public_api_evidence = {
        "trigger_scope": "direct_function",
        "internal_target": "shift_left",
        "public_entrypoint": "shift_left",
        "public_call_args": ["[1]"],
        "trigger_expression": "shift_left([1])",
        "call_style": "call",
        "callable_kind": "function",
        "is_nested_target": False,
        "entrypoint_differs_from_internal_target": False,
    }
    overlay_case_context = {
        "rule_id": "possible_index_overrun",
        "function_name": "shift_left",
        "qualified_name": "shift_left",
        "callable_kind": "function",
        "relative_file_path": "sample.py",
        "expected_exception": "IndexError",
        "public_api_evidence": public_api_evidence,
    }
    return {
        "analysis_route": {
            "analysis_source": "failure_overlay_dynamic_evidence",
            "phase2_ready": True,
        },
        "failure_overlay": {
            "status": "pass",
            "reason": "overlay_dynamic_evidence_generated",
            "selected_rule_id": "possible_index_overrun",
            "selected_function": "shift_left",
            "public_api_evidence": public_api_evidence,
            "overlay_case_context": overlay_case_context,
            "recommended_validation_command": (
                "python -m pytest -q tests/test_overlay.py::test_shift_left"
            ),
        },
        "fault_localization": {
            "status": "pass",
            "reason": "localized_from_dynamic_evidence",
            "top_function": "shift_left",
            "top_score": 0.95,
            "public_api_evidence": public_api_evidence,
            "overlay_case_context": overlay_case_context,
        },
    }


def _write_shift_left_template(root: Path) -> Path:
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
                        "name": "template_pipeline_shift_left",
                        "repo_path": "template_pipeline_shift_left_repo",
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
                                "source": "local_raw_source_mutation",
                                "bug_type": "boundary error",
                            },
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return template


def _write_cross_file_mean_template(root: Path) -> Path:
    raw_source = root / "raw_mathlib.py"
    raw_source.write_text(
        "def mean(nums):\n"
        "    n = len(nums)\n"
        "    if n == 0:\n"
        "        raise ValueError('empty input')\n"
        "    return sum(nums) / n\n",
        encoding="utf-8",
    )
    template = root / "cross_file_template.json"
    template.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "name": "template_pipeline_cross_file_mean",
                        "repo_path": "template_pipeline_cross_file_mean_repo",
                        "sources": [
                            {
                                "raw_url": str(raw_source),
                                "target_path": "mathlib.py",
                            }
                        ],
                        "mutations": [
                            {
                                "target_path": "mathlib.py",
                                "find": (
                                    "    if n == 0:\n"
                                    "        raise ValueError('empty input')\n"
                                ),
                                "replace": "",
                                "description": "Remove empty-input guard.",
                            }
                        ],
                        "files": [
                            {
                                "target_path": "service.py",
                                "content": (
                                    "from mathlib import mean\n\n"
                                    "def compute_average(nums):\n"
                                    "    return mean(nums)\n"
                                ),
                            },
                            {
                                "target_path": "test_service.py",
                                "content": (
                                    "from service import compute_average\n\n"
                                    "def test_empty_average_raises_value_error():\n"
                                    "    try:\n"
                                    "        compute_average([])\n"
                                    "    except ValueError:\n"
                                    "        return\n"
                                    "    except Exception as exc:\n"
                                    "        raise AssertionError(\n"
                                    "            f'expected ValueError, got {type(exc).__name__}'\n"
                                    "        ) from exc\n"
                                    "    raise AssertionError(\n"
                                    "        'empty input should raise ValueError'\n"
                                    "    )\n"
                                ),
                            },
                        ],
                        "benchmark": {
                            "buggy_functions": ["mean"],
                            "expected_rule_ids": ["missing_len_zero_guard"],
                            "failing_tests": [
                                "test_empty_average_raises_value_error"
                            ],
                            "passed_tests": [],
                            "test_args": [],
                            "metadata": {
                                "source": "local_raw_source_cross_file_mutation",
                                "bug_type": "zero division error",
                            },
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return template
