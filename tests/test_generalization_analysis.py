from types import SimpleNamespace

from code_intelligence_agent.evaluation.generalization_analysis import (
    benchmark_generalization_report,
    benchmark_generalization_summary,
)


def test_generalization_report_groups_by_upstream_and_computes_holdout_gaps():
    cases = [
        _case(
            "a_1",
            upstream="repo/a",
            bug_type="boundary",
            expected_rule_ids=["rule_a"],
            top1=True,
            patch=True,
        ),
        _case(
            "a_2",
            upstream="repo/a",
            bug_type="state",
            expected_rule_ids=["rule_b"],
            top1=True,
            patch=True,
        ),
        _case(
            "b_1",
            upstream="repo/b",
            bug_type="boundary",
            expected_rule_ids=["rule_a"],
            top1=False,
            patch=False,
        ),
    ]

    report = benchmark_generalization_report(cases)
    splits = {
        split.holdout_group: split
        for split in report.holdout_splits
    }

    assert report.case_count == 3
    assert report.source_group_count == 2
    assert report.source_groups["repo/a"].case_count == 2
    assert report.source_groups["repo/a"].bug_type_count == 2
    assert report.source_groups["repo/a"].expected_rule_count == 2
    assert report.source_groups["repo/b"].top1 == 0.0
    assert report.source_balance_entropy == 0.9183
    assert report.source_imbalance_ratio == 2.0
    assert splits["repo/b"].train_metrics.top1 == 1.0
    assert splits["repo/b"].holdout_metrics.top1 == 0.0
    assert splits["repo/b"].top1_gap == 1.0
    assert splits["repo/b"].patch_success_gap == 1.0
    assert report.max_top1_gap == 1.0
    assert report.max_patch_success_gap == 1.0
    assert report.worst_holdout_group == "repo/a"
    assert report.worst_holdout_gap_score == 0.85
    assert report.stability_score == 0.15
    assert report.risk_level == "high"


def test_generalization_summary_supports_dict_artifacts_and_source_inference():
    summary = benchmark_generalization_summary(
        {
            "cases": [
                _case("cpython_example", upstream="", top1=True).to_dict(),
                _case("pluggy_example", upstream="", top1=True).to_dict(),
            ]
        }
    )

    assert summary["source_group_count"] == 2
    assert summary["source_balance_entropy"] == 1.0
    assert summary["source_imbalance_ratio"] == 1.0
    assert summary["stability_score"] == 1.0
    assert summary["risk_level"] == "low"
    assert sorted(summary["source_groups"]) == [
        "pytest-dev/pluggy",
        "python/cpython",
    ]
    assert summary["holdout_splits"][0]["holdout_metrics"]["case_count"] == 1


def _case(
    name: str,
    *,
    upstream: str,
    bug_type: str = "boundary",
    expected_rule_ids: list[str] | None = None,
    top1: bool,
    patch: bool = True,
) -> SimpleNamespace:
    ranked = ["target"] if top1 else ["other", "target"]
    return SimpleNamespace(
        case_name=name,
        metadata={"upstream": upstream},
        bug_type=bug_type,
        ranked_functions=ranked,
        ground_truth={"target"},
        expected_rule_ids=expected_rule_ids or ["rule_a"],
        patch_success=patch,
        exam_score=0.0 if top1 else 0.5,
        search_analysis={"efficiency": 1.0 if patch else 0.0, "evaluated_nodes": 1},
        best_patch_risk={"score": 0.1},
        to_dict=lambda: {
            "case_name": name,
            "metadata": {"upstream": upstream},
            "bug_type": bug_type,
            "ranked_functions": ranked,
            "ground_truth": ["target"],
            "expected_rule_ids": expected_rule_ids or ["rule_a"],
            "patch_success": patch,
            "exam_score": 0.0 if top1 else 0.5,
            "search_analysis": {
                "efficiency": 1.0 if patch else 0.0,
                "evaluated_nodes": 1,
            },
            "best_patch_risk": {"score": 0.1},
        },
    )
