from code_intelligence_agent.evaluation.localization_attribution import (
    localization_attribution_report,
    localization_attribution_summary,
)
from code_intelligence_agent.evaluation.quality_gate import (
    QualityGateThresholds,
    evaluate_quality_gate,
)


def test_localization_attribution_reports_component_contributions_and_flips():
    cases = [
        _case(
            "case_flip",
            top_score=0.55,
            top_signals={"sbfl": 1.0, "graph": 0.2, "static": 0.2, "risk": 0.0},
            runner_score=0.40,
            runner_signals={"sbfl": 0.0, "graph": 0.2, "static": 0.2, "risk": 0.0},
        ),
        _case(
            "case_stable",
            top_score=0.80,
            top_signals={"sbfl": 0.1, "graph": 1.0, "static": 1.0, "risk": 0.2},
            runner_score=0.50,
            runner_signals={"sbfl": 0.1, "graph": 0.2, "static": 0.2, "risk": 0.0},
        ),
    ]

    report = localization_attribution_report(cases)
    summary = localization_attribution_summary(cases)

    assert report.case_count == 2
    assert report.attributed_case_count == 2
    assert report.attribution_coverage == 1.0
    assert report.primary_component_counts == {"graph": 1, "sbfl": 1}
    assert report.counterfactual_flip_case_count == 1
    assert report.counterfactual_flip_rate == 0.5
    assert report.fragile_top1_case_count == 1
    assert report.top_fragile_cases[0].case == "case_flip"
    assert report.top_fragile_cases[0].counterfactual_flip_components == ["sbfl"]
    assert summary["average_component_contributions"]["risk"] < 0.0


def test_quality_gate_checks_localization_attribution_when_present():
    payload = {
        "benchmark_report": {
            "summary": {
                "case_count": 1,
                "top1": 1.0,
                "top3": 1.0,
                "patch_success_rate": 1.0,
                "localization_attribution": {
                    "case_count": 1,
                    "attributed_case_count": 1,
                    "attribution_coverage": 1.0,
                    "fragile_top1_rate": 0.0,
                    "counterfactual_flip_rate": 0.0,
                    "average_reconstruction_error": 0.01,
                    "primary_component_counts": {"sbfl": 1},
                },
            },
            "cases": [
                {
                    "case_name": "case_0",
                    "patch_success": True,
                    "localization_details": [
                        {
                            "rank": 1,
                            "signals": {
                                "sbfl": 1.0,
                                "graph": 0.8,
                                "static": 0.7,
                                "risk": 0.0,
                                "llm": 0.0,
                            },
                        }
                    ],
                    "beam_search_results": [
                        {
                            "success": True,
                            "failure_type": "success",
                            "passed": 1,
                            "failed": 0,
                        }
                    ],
                }
            ],
        }
    }

    passed = evaluate_quality_gate(
        payload,
        thresholds=QualityGateThresholds(
            min_cases=1,
            min_top1=1.0,
            min_top3=1.0,
            min_patch_success_rate=1.0,
            min_localization_attribution_coverage=1.0,
            max_localization_attribution_fragile_rate=0.0,
            max_localization_attribution_counterfactual_flip_rate=0.0,
            max_localization_attribution_reconstruction_error=0.05,
        ),
    )
    weak = evaluate_quality_gate(
        {
            **payload,
            "benchmark_report": {
                **payload["benchmark_report"],
                "summary": {
                    **payload["benchmark_report"]["summary"],
                    "localization_attribution": {
                        **payload["benchmark_report"]["summary"][
                            "localization_attribution"
                        ],
                        "attribution_coverage": 0.5,
                    },
                },
            },
        },
        thresholds=QualityGateThresholds(
            min_cases=1,
            min_top1=1.0,
            min_top3=1.0,
            min_patch_success_rate=1.0,
            min_localization_attribution_coverage=1.0,
        ),
    )

    assert passed.passed is True
    assert weak.passed is False
    failed_names = {check.name for check in weak.checks if not check.passed}
    assert "localization_attribution_coverage" in failed_names


def _case(
    name: str,
    *,
    top_score: float,
    top_signals: dict[str, float],
    runner_score: float,
    runner_signals: dict[str, float],
) -> dict:
    return {
        "case_name": name,
        "top1_hit": True,
        "coverage_mode": "dynamic_trace",
        "ground_truth": [f"{name}.target"],
        "localization_details": [
            {
                "rank": 1,
                "function_name": f"{name}.target",
                "score": top_score,
                "signals": top_signals,
            },
            {
                "rank": 2,
                "function_name": f"{name}.runner",
                "score": runner_score,
                "signals": runner_signals,
            },
        ],
    }
