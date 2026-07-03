from code_intelligence_agent.evaluation.hard_case_mining import (
    hard_case_mining_report,
    render_hard_case_mining_markdown,
)


def test_hard_case_mining_prioritizes_coverage_and_ablation_gaps():
    report = hard_case_mining_report(
        {
            "benchmark_report": {
                "summary": {
                    "case_count": 10,
                    "difficulty_report": {
                        "bucket_counts": {"easy": 8, "medium": 2, "hard": 0},
                        "label_counts": {
                            "cross_file_patch": 1,
                            "patch_candidate_competition": 1,
                        },
                        "cases": [
                            {
                                "case_name": "cross_file_example",
                                "bucket": "medium",
                                "labels": ["cross_file_patch"],
                            }
                        ],
                    },
                    "generalization_report": {
                        "case_count": 10,
                        "source_group_count": 2,
                        "source_groups": {
                            "repo/a": {"case_count": 8},
                            "repo/b": {"case_count": 2},
                        },
                        "holdout_splits": [
                            {"holdout_group": "repo/a", "top1_gap": 0.0},
                            {"holdout_group": "repo/b", "top1_gap": 0.35},
                        ],
                        "max_top1_gap": 0.35,
                        "max_map_gap": 0.0,
                        "max_patch_success_gap": 0.0,
                        "max_search_efficiency_gap": 0.0,
                    },
                    "search_competition_analysis": {
                        "beam_case_count": 10,
                        "multi_candidate_case_count": 9,
                        "score_inversion_count": 0,
                        "average_failure_pressure": 0.08,
                        "rows": [
                            {
                                "case": "beam_decoy_candidate",
                                "evaluated_nodes": 3,
                                "failure_pressure": 0.67,
                                "score_inversion": False,
                            }
                        ],
                    },
                },
                "cases": [],
            },
            "ablation_impact": {
                "rows": [
                    {
                        "variant": "without_beam_search",
                        "impact_score": -0.12,
                        "direction": "regression",
                    }
                ]
            },
        }
    )
    by_signal = {
        suggestion.target_signal: suggestion
        for suggestion in report.suggestions
    }

    assert report.suggestion_count >= 5
    assert report.high_priority_count >= 1
    assert by_signal["hard"].priority == "high"
    assert by_signal["source_group_count"].benchmark_focus == (
        "cross-project generalization expansion"
    )
    assert by_signal["repo/b"].benchmark_focus == "underrepresented source group"
    assert by_signal["max_top1_gap"].benchmark_focus == (
        "localization generalization gap"
    )
    assert by_signal["without_beam_search"].benchmark_focus == (
        "beam-search-dependent repair"
    )
    assert by_signal["search_score_inversion"].benchmark_focus == (
        "top-rank decoy patch pressure"
    )
    assert by_signal["search_candidate_competition"].source == (
        "search_competition_gap"
    )


def test_hard_case_mining_maps_direct_search_repair_ablation_regressions():
    report = hard_case_mining_report(
        {
            "benchmark_report": {"summary": {"case_count": 62}, "cases": []},
            "ablation_impact": {
                "rows": [
                    {
                        "variant": "without_reflection",
                        "impact_score": -0.11,
                        "direction": "regression",
                    },
                    {
                        "variant": "without_patch_prior",
                        "impact_score": -0.09,
                        "direction": "regression",
                    },
                    {
                        "variant": "without_diversity_reranking",
                        "impact_score": -0.07,
                        "direction": "regression",
                    },
                ]
            },
        }
    )
    by_signal = {
        suggestion.target_signal: suggestion
        for suggestion in report.suggestions
    }

    assert by_signal["without_reflection"].benchmark_focus == (
        "execution-feedback reflection recovery"
    )
    assert by_signal["without_patch_prior"].benchmark_focus == (
        "patch-prior-sensitive repair ranking"
    )
    assert by_signal["without_diversity_reranking"].benchmark_focus == (
        "diversity reranking lift pressure"
    )


def test_hard_case_mining_keeps_pressure_on_mature_search_suite():
    report = hard_case_mining_report(
        {
            "benchmark_report": {
                "summary": {
                    "case_count": 62,
                    "search_competition_analysis": {
                        "beam_case_count": 62,
                        "multi_candidate_case_count": 16,
                        "score_inversion_count": 0,
                        "average_failure_pressure": 0.1371,
                        "rows": [],
                    },
                },
                "cases": [],
            },
        }
    )
    by_signal = {
        suggestion.target_signal: suggestion
        for suggestion in report.suggestions
    }

    assert by_signal["search_candidate_competition"].target_count == 18
    assert by_signal["search_score_inversion"].target_count == 2
    assert by_signal["search_failure_pressure"].target_count == 20
    assert by_signal["search_diversity_reranking"].target_count == 1
    assert by_signal["search_candidate_competition"].priority == "medium"
    assert by_signal["search_score_inversion"].priority == "high"
    assert by_signal["search_diversity_reranking"].priority == "high"
    assert by_signal["search_diversity_reranking"].benchmark_focus == (
        "diversity reranking lift pressure"
    )
    assert by_signal["search_failure_pressure"].priority == "low"


def test_hard_case_mining_skips_search_diversity_when_lift_is_covered():
    report = hard_case_mining_report(
        {
            "benchmark_report": {
                "summary": {
                    "case_count": 62,
                    "search_competition_analysis": {
                        "beam_case_count": 62,
                        "multi_candidate_case_count": 20,
                        "score_inversion_count": 2,
                        "average_failure_pressure": 0.2,
                        "diversity_assisted_success_count": 2,
                        "average_success_diversity_lift": 1.5,
                        "budget_sensitive_diversity_success_count": 1,
                        "rows": [],
                    },
                },
                "cases": [],
            },
        }
    )
    by_signal = {
        suggestion.target_signal: suggestion
        for suggestion in report.suggestions
    }

    assert "search_diversity_reranking" not in by_signal


def test_hard_case_mining_flags_missing_candidate_deduplication_pressure():
    report = hard_case_mining_report(
        {
            "benchmark_report": {
                "summary": {
                    "case_count": 62,
                    "search_budget_analysis": {
                        "case_count": 62,
                        "evaluated_case_count": 62,
                        "dedupe_affected_case_count": 0,
                        "total_deduplicated_candidates": 0,
                        "average_duplicate_pressure": 0.0,
                        "rows": [
                            {
                                "case": "no_dedupe_pressure",
                                "deduplicated_candidates": 0,
                                "effective_candidate_pool": 4,
                                "duplicate_pressure": 0.0,
                            }
                        ],
                    },
                },
                "cases": [],
            },
        }
    )
    by_signal = {
        suggestion.target_signal: suggestion
        for suggestion in report.suggestions
    }

    suggestion = by_signal["candidate_deduplication_pressure"]
    assert suggestion.source == "search_budget_gap"
    assert suggestion.priority == "high"
    assert suggestion.current_count == 0
    assert suggestion.target_count == 1
    assert suggestion.benchmark_focus == "candidate deduplication budget pressure"
    assert suggestion.examples == [
        "no_dedupe_pressure:deduped=0;pressure=0.0000;effective_pool=4"
    ]


def test_hard_case_mining_skips_candidate_deduplication_when_pressure_is_covered():
    report = hard_case_mining_report(
        {
            "benchmark_report": {
                "summary": {
                    "case_count": 62,
                    "search_budget_analysis": {
                        "case_count": 62,
                        "evaluated_case_count": 62,
                        "dedupe_affected_case_count": 1,
                        "total_deduplicated_candidates": 3,
                        "average_duplicate_pressure": 0.12,
                        "rows": [],
                    },
                },
                "cases": [],
            },
        }
    )
    by_signal = {
        suggestion.target_signal: suggestion
        for suggestion in report.suggestions
    }

    assert "candidate_deduplication_pressure" not in by_signal


def test_hard_case_mining_flags_missing_reflection_recovery_evidence():
    report = hard_case_mining_report(
        {
            "benchmark_report": {
                "summary": {
                    "case_count": 62,
                    "reflection_analysis": {
                        "case_count": 62,
                        "reflection_case_count": 1,
                        "reflection_success_case_count": 0,
                        "reflection_candidate_count": 1,
                        "successful_reflection_candidate_count": 0,
                        "rows": [
                            {
                                "case": "near_miss_reflection_case",
                                "reflection_candidate_count": 1,
                                "successful_reflection_candidate_count": 0,
                                "parent_failure_types": ["test_failure"],
                            }
                        ],
                    },
                },
                "cases": [],
            },
        }
    )
    by_signal = {
        suggestion.target_signal: suggestion
        for suggestion in report.suggestions
    }

    suggestion = by_signal["reflection_depth"]
    assert suggestion.source == "reflection_analysis_gap"
    assert suggestion.priority == "medium"
    assert suggestion.current_count == 0
    assert suggestion.target_count == 1
    assert suggestion.benchmark_focus == "execution-feedback reflection recovery"
    assert suggestion.examples == [
        "near_miss_reflection_case:reflections=1:successes=0:parent=test_failure"
    ]


def test_hard_case_mining_skips_reflection_gap_when_recovery_is_covered():
    report = hard_case_mining_report(
        {
            "benchmark_report": {
                "summary": {
                    "case_count": 62,
                    "reflection_analysis": {
                        "case_count": 62,
                        "reflection_case_count": 1,
                        "reflection_success_case_count": 1,
                        "reflection_candidate_count": 2,
                        "successful_reflection_candidate_count": 1,
                        "rows": [],
                    },
                },
                "cases": [],
            },
        }
    )
    by_source = {
        suggestion.source: suggestion
        for suggestion in report.suggestions
    }

    assert "reflection_analysis_gap" not in by_source


def test_hard_case_mining_uses_case_audit_to_cover_budget_sensitive_diversity():
    report = hard_case_mining_report(
        {
            "benchmark_report": {
                "summary": {
                    "case_count": 62,
                    "search_competition_analysis": {
                        "beam_case_count": 62,
                        "multi_candidate_case_count": 20,
                        "score_inversion_count": 2,
                        "average_failure_pressure": 0.2,
                        "diversity_assisted_success_count": 2,
                        "average_success_diversity_lift": 1.5,
                        "rows": [],
                    },
                },
                "cases": [
                    {
                        "case_name": "budget_sensitive_case",
                        "search_competition_audit": {
                            "case": "budget_sensitive_case",
                            "evaluated_nodes": 4,
                            "success_diversity_lift": 3,
                            "success_budget_gap_before_rerank": 2,
                            "success_budget_margin_after_rerank": 1,
                            "budget_sensitive_diversity_success": True,
                            "counterfactual_condition": (
                                "base_rank_outside_budget_and_reranked_inside_budget"
                            ),
                        },
                    }
                ],
            },
        }
    )
    by_signal = {
        suggestion.target_signal: suggestion
        for suggestion in report.suggestions
    }

    assert "search_diversity_reranking" not in by_signal


def test_hard_case_mining_requires_budget_sensitive_diversity_evidence():
    report = hard_case_mining_report(
        {
            "benchmark_report": {
                "summary": {
                    "case_count": 62,
                    "search_competition_analysis": {
                        "beam_case_count": 62,
                        "multi_candidate_case_count": 20,
                        "score_inversion_count": 2,
                        "average_failure_pressure": 0.2,
                        "diversity_assisted_success_count": 2,
                        "average_success_diversity_lift": 1.5,
                        "budget_sensitive_diversity_success_count": 0,
                        "rows": [
                            {
                                "case": "no_success_candidate_case",
                                "evaluated_nodes": 4,
                                "failure_pressure": 1.0,
                                "successful_nodes": 0,
                                "counterfactual_condition": "no_success_candidate",
                            },
                            {
                                "case": "summary_diversity_case",
                                "evaluated_nodes": 4,
                                "failure_pressure": 0.75,
                                "success_diversity_lift": 3,
                                "success_budget_gap_before_rerank": 0,
                                "success_budget_margin_after_rerank": 2,
                                "counterfactual_condition": (
                                    "base_rank_already_inside_budget"
                                ),
                            }
                        ],
                    },
                },
                "cases": [
                    {
                        "case_name": "outside_budget_still_lost_case",
                        "search_competition_audit": {
                            "case": "outside_budget_still_lost_case",
                            "evaluated_nodes": 4,
                            "failure_pressure": 0.75,
                            "success_diversity_lift": 3,
                            "success_budget_gap_before_rerank": 2,
                            "success_budget_margin_after_rerank": -1,
                            "budget_sensitive_diversity_success": False,
                            "counterfactual_condition": (
                                "base_rank_outside_budget_and_still_outside_budget"
                            ),
                        },
                    }
                ],
            },
        }
    )
    by_signal = {
        suggestion.target_signal: suggestion
        for suggestion in report.suggestions
    }

    suggestion = by_signal["search_diversity_reranking"]
    assert suggestion.priority == "high"
    assert suggestion.current_count == 0
    assert suggestion.target_count == 1
    assert suggestion.examples[0] == (
        "outside_budget_still_lost_case:"
        "base_rank_outside_budget_and_still_outside_budget:gap=2:margin=-1"
    )
    assert all(":no_success_candidate" not in item for item in suggestion.examples)


def test_hard_case_mining_flags_slice_grounding_and_fragile_localization_gaps():
    report = hard_case_mining_report(
        {
            "benchmark_report": {
                "summary": {
                    "case_count": 10,
                    "slice_grounded_case_count": 6,
                    "average_top1_slice_support": 0.62,
                    "average_top1_slice_failed_test_reachability": 0.70,
                    "average_top1_slice_call_chain_coverage": 0.40,
                    "localization_attribution": {
                        "fragile_top1_rate": 0.18,
                        "fragile_top1_case_count": 2,
                        "fragile_top1_cases": [
                            {
                                "case_name": "near_tie_case",
                                "top1_margin": 0.012,
                            }
                        ],
                    },
                },
                "cases": [
                    {
                        "case_name": "weak_slice_case",
                        "localization_details": [
                            {
                                "function_name": "service.target",
                                "slice_grounding": {
                                    "support_score": 0.30,
                                    "failed_test_reachability": 0.20,
                                    "call_chain_edge_coverage": 0.10,
                                },
                            }
                        ],
                    }
                ],
            },
        }
    )
    by_signal = {
        suggestion.target_signal: suggestion
        for suggestion in report.suggestions
    }

    assert by_signal["weak_slice_grounding"].source == "slice_grounding_gap"
    assert by_signal["weak_slice_grounding"].priority == "high"
    assert by_signal["weak_slice_support"].benchmark_focus == (
        "program-slice support score hardening"
    )
    assert by_signal["weak_failed_test_reachability"].benchmark_focus == (
        "failed-test reachability grounding"
    )
    assert by_signal["weak_call_chain_coverage"].benchmark_focus == (
        "call-chain edge coverage grounding"
    )
    assert by_signal["weak_call_chain_coverage"].examples == [
        "weak_slice_case:service.target:0.100"
    ]
    assert by_signal["fragile_top1_margin"].source == (
        "localization_attribution_gap"
    )
    assert by_signal["fragile_top1_margin"].examples == [
        "near_tie_case:margin=0.0120"
    ]


def test_hard_case_mining_keeps_pressure_on_mature_slice_reachability():
    report = hard_case_mining_report(
        {
            "benchmark_report": {
                "summary": {
                    "case_count": 62,
                    "slice_grounded_case_count": 62,
                    "average_top1_slice_support": 0.9839,
                    "average_top1_slice_failed_test_reachability": 0.9597,
                    "average_top1_slice_call_chain_coverage": 0.9839,
                },
                "cases": [
                    {
                        "case_name": "lowest_reachability_case",
                        "localization_details": [
                            {
                                "function_name": "service.target",
                                "slice_grounding": {
                                    "support_score": 0.91,
                                    "failed_test_reachability": 0.50,
                                    "call_chain_edge_coverage": 1.0,
                                },
                            }
                        ],
                    }
                ],
            },
        }
    )
    by_signal = {
        suggestion.target_signal: suggestion
        for suggestion in report.suggestions
    }

    assert "weak_slice_support" not in by_signal
    assert "weak_call_chain_coverage" not in by_signal
    assert by_signal["weak_failed_test_reachability"].priority == "medium"
    assert by_signal["weak_failed_test_reachability"].current_count == 96
    assert by_signal["weak_failed_test_reachability"].target_count == 98
    assert by_signal["weak_failed_test_reachability"].examples == [
        "lowest_reachability_case:service.target:0.500"
    ]


def test_hard_case_mining_markdown_renders_suggestions():
    report = hard_case_mining_report(
        {
            "benchmark_report": {
                "summary": {
                    "case_count": 1,
                    "difficulty_report": {
                        "bucket_counts": {"easy": 1},
                        "label_counts": {},
                        "cases": [],
                    },
                },
                "cases": [],
            }
        }
    )
    markdown = render_hard_case_mining_markdown(report)

    assert "# Hard-Case Mining" in markdown
    assert "multi-patch repair" in markdown
    assert "Suggested Case Shape" in markdown
