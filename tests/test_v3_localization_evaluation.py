from __future__ import annotations

import json

from code_intelligence_agent.core.fault_localizer import ScoreWeights, ochiai
from code_intelligence_agent.core.models import TestExecutionSummary
from code_intelligence_agent.evaluation.v3_localization_evaluation import (
    _merge_runtime_coverage,
    audit_v3_localization_artifacts,
    build_v3_ablation_analysis,
    build_v3_localization_metrics,
    build_v3_localization_variants,
    rerank_v3_signal_rows,
    select_v3_localization_profile,
    write_v3_localization_release_artifacts,
)
from code_intelligence_agent.tools.coverage_runner import (
    TestCoverageResult as CoverageResult,
)


def _raw_row(function: str, file_path: str, **signals: float) -> dict:
    return {
        "function_key": f"{file_path}::{function}",
        "file_key": file_path,
        "function_id": f"id::{function}",
        "function_name": function,
        "start_line": 1,
        "end_line": 3,
        "signals": signals,
    }


def _case(
    case_id: str,
    *,
    split: str,
    truth: str | None,
    rows: list[dict],
    file_truth: str = "module.py",
) -> dict:
    return {
        "case_id": case_id,
        "split": split,
        "repository": "example/repository",
        "difficulty_tags": ["static_negative"],
        "raw_rows": rows,
        "ground_truth": {
            "function_rankable": truth is not None,
            "function_keys": [] if truth is None else [truth],
            "file_keys": [file_truth],
        },
        "signal_extraction_latency_ms": 5.0,
    }


def test_reranking_retains_exact_final_score_attribution():
    rows = [
        _raw_row(
            "target",
            "module.py",
            static=0.8,
            graph=0.5,
            risk=0.9,
            semantic=0.2,
        )
    ]
    weights = ScoreWeights(
        sbfl=0.0,
        graph=0.4,
        static=0.7,
        semantic=0.1,
        llm=0.0,
        risk=0.2,
        test_failure=0.0,
        traceback=0.0,
        complexity=0.0,
        change_history=0.0,
    )

    result = rerank_v3_signal_rows(rows, weights)[0]

    assert result["score"] == 0.6
    assert result["contributions"]["static"] == 0.56
    assert result["contributions"]["graph"] == 0.2
    assert result["contributions"]["risk"] == -0.18
    assert result["score_reconstruction"] == result["score"]
    assert result["score_reconstruction_error"] == 0.0


def test_weight_selection_uses_validation_only_and_is_test_oracle_invariant():
    rows = [
        _raw_row("static_target", "module.py", static=1.0, graph=0.0),
        _raw_row("graph_target", "module.py", static=0.0, graph=1.0),
    ]
    validation = [
        _case(
            "validation-1",
            split="validation",
            truth="module.py::graph_target",
            rows=rows,
        )
    ]
    profiles = [
        {"name": "static", "weights": ScoreWeights(static=1.0, graph=0.0)},
        {"name": "graph", "weights": ScoreWeights(static=0.0, graph=1.0)},
    ]

    first = select_v3_localization_profile(
        validation,
        test_case_ids=["test-with-static-oracle"],
        profiles=profiles,
    )
    second = select_v3_localization_profile(
        validation,
        test_case_ids=["test-with-different-oracle"],
        profiles=profiles,
    )

    assert first["selected_profile"]["name"] == "graph"
    assert second["selected_profile"]["name"] == "graph"
    assert first["test_ground_truth_accessed"] is False
    assert second["test_ground_truth_accessed"] is False
    assert first["selected_profile"]["weights"] == second["selected_profile"]["weights"]


def test_metrics_keep_added_function_case_in_file_denominator_only():
    rankable_rows = [
        _raw_row("target", "module.py", static=1.0),
        _raw_row("other", "other.py", static=0.5),
    ]
    added_rows = [_raw_row("existing", "added.py", static=0.2)]
    rankable = _case(
        "rankable",
        split="test",
        truth="module.py::target",
        rows=rankable_rows,
    )
    unrankable = _case(
        "added-function",
        split="test",
        truth=None,
        rows=added_rows,
        file_truth="added.py",
    )
    weights = ScoreWeights(static=1.0, graph=0.0)
    rankings = {
        "rankable": rerank_v3_signal_rows(rankable_rows, weights),
        "added-function": rerank_v3_signal_rows(added_rows, weights),
    }

    metrics = build_v3_localization_metrics([rankable, unrankable], rankings)

    assert metrics["case_count"] == 2
    assert metrics["function_rankable_case_count"] == 1
    assert metrics["function_unrankable_case_ids"] == ["added-function"]
    assert metrics["function"]["top1"] == 1.0
    assert metrics["file"]["case_count"] == 2
    assert metrics["file"]["top1"] == 1.0


def test_ablation_contract_distinguishes_semantic_from_llm():
    variants = build_v3_localization_variants(ScoreWeights())

    assert set(variants) == {
        "rule_only",
        "graph_only",
        "dynamic_only",
        "semantic_only",
        "fusion",
        "without_rule",
        "without_graph",
        "without_dynamic",
        "without_semantic",
        "without_auxiliary",
    }
    assert variants["semantic_only"].semantic == 1.0
    assert variants["semantic_only"].llm == 0.0
    assert variants["without_dynamic"].sbfl == 0.0
    assert variants["without_dynamic"].test_failure == 0.0
    assert variants["without_dynamic"].traceback == 0.0
    assert variants["without_auxiliary"].complexity == 0.0
    assert variants["without_auxiliary"].change_history == 0.0


def test_ablation_analysis_does_not_attribute_zero_weight_families():
    fusion_metrics = {
        "function": {
            "top1": 0.6,
            "top3": 0.8,
            "top5": 1.0,
            "mrr": 0.7,
            "map": 0.6,
            "ndcg_at_3": 0.7,
            "exam": 0.01,
        },
        "file": {"top1": 0.8},
    }
    test_metrics = {"fusion": fusion_metrics}
    for variant in (
        "without_rule",
        "without_graph",
        "without_dynamic",
        "without_semantic",
        "without_auxiliary",
    ):
        test_metrics[variant] = fusion_metrics

    analysis = build_v3_ablation_analysis(
        test_metrics,
        selected_weights=ScoreWeights(
            sbfl=0.3,
            graph=0.0,
            static=0.0,
            semantic=0.2,
            llm=0.0,
            risk=0.0,
            test_failure=0.2,
            traceback=0.1,
            complexity=0.1,
            change_history=0.1,
        ),
    )

    assert analysis["families"]["rule"]["conclusion"] == (
        "inactive_by_validation_weight_selection"
    )
    assert analysis["families"]["graph"]["conclusion"] == (
        "inactive_by_validation_weight_selection"
    )
    assert analysis["families"]["dynamic"]["conclusion"] == (
        "no_observed_test_metric_change"
    )


def test_artifact_audit_checks_oracle_snapshot_split_and_reconstruction():
    ready = []
    results = []
    for split, repository in (
        ("development", "example/development"),
        ("validation", "example/validation"),
        ("test", "example/test"),
    ):
        case_id = f"{split}-case"
        ready.append(
            {
                "case_id": case_id,
                "split": split,
                "repository": repository,
                "ranking_snapshot_sha256": "a" * 64,
                "ground_truth": {
                    "status": "resolved",
                    "ranking_snapshot_sha256": "a" * 64,
                },
                "coverage_summary": {
                    "real_failing_runtime_coverage_available": True
                },
            }
        )
        results.append(
            {"case_id": case_id, "score_reconstruction_pass": True}
        )

    audit = audit_v3_localization_artifacts(
        ready_cases=ready,
        case_results=results,
        selection={
            "scope": "validation_only",
            "test_ground_truth_accessed": False,
        },
        runtime_coverage_required=True,
    )

    assert audit["status"] == "pass"
    assert audit["repository_disjoint_splits"] is True
    assert audit["resolved_ground_truth_case_count"] == 3
    assert audit["score_reconstruction_pass_case_count"] == 3


def test_runtime_coverage_does_not_double_count_baseline_failure():
    baseline = TestExecutionSummary(
        failed_tests={"baseline-test"},
        coverage={"baseline-test": {"target"}},
        dynamic_evidence_test_ids={"baseline-test"},
        test_names={"baseline-test": "test_target"},
    )
    runtime = CoverageResult(
        test_name="test_target",
        test_id="runtime-test",
        success=False,
        returncode=1,
        covered_function_ids={"target"},
        covered_function_lines={"target": {2}},
        covered_function_line_counts={"target": 1},
        function_line_coverage={"target": 1.0},
    )

    merged = _merge_runtime_coverage(baseline, [runtime])

    assert merged.failed_tests == {"runtime-test"}
    assert merged.dynamic_evidence_test_ids == {
        "baseline-test",
        "runtime-test",
    }
    assert ochiai("target", merged) == 1.0


def test_release_artifacts_publish_signal_extraction_version(tmp_path):
    output = tmp_path / "output"
    docs = tmp_path / "docs"
    case_output = output / "case"
    case_output.mkdir(parents=True)
    source_path = output / "v3_localization_evaluation.json"
    source_path.write_text("{}\n", encoding="utf-8")
    ranking_path = case_output / "variant_rankings.json"
    ranking_path.write_text(
        json.dumps(
            {
                "ranking_snapshot_sha256": "a" * 64,
                "selected_profile_sha256": "b" * 64,
                "variants": {
                    "fusion": {
                        "top_rankings": [],
                        "score_reconstruction_pass": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    result = {
        "status": "pass",
        "case_count": 1,
        "ready_case_count": 1,
        "split_case_counts": {"development": 0, "validation": 0, "test": 1},
        "signal_extraction_version": "test-signals-v9",
        "case_results": [
            {
                "case_id": "case-1",
                "split": "test",
                "repository": "example/repository",
                "function_rankable": True,
                "fusion_metrics": {},
                "coverage": {"real_failing_runtime_coverage_available": True},
                "score_reconstruction_pass": True,
                "artifacts": {"variant_rankings": ranking_path.as_posix()},
            }
        ],
    }

    artifacts = write_v3_localization_release_artifacts(
        result,
        source_output_dir=output,
        docs_dir=docs,
    )

    metrics = json.loads(
        (docs / "phase4_localization_metrics.json").read_text(encoding="utf-8")
    )
    attribution = json.loads(
        (docs / "phase4_test_top5_attribution.json").read_text(encoding="utf-8")
    )
    assert metrics["signal_extraction_version"] == "test-signals-v9"
    assert attribution["signal_extraction_version"] == "test-signals-v9"
    assert artifacts["metrics_json"].endswith("phase4_localization_metrics.json")
    assert b"\r\n" not in (docs / "phase4_localization_metrics.json").read_bytes()
    assert b"\r\n" not in (docs / "phase4_test_top5_attribution.json").read_bytes()
    assert b"\r\n" not in (docs / "phase4_difficult_localization.md").read_bytes()
