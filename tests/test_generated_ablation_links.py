from code_intelligence_agent.evaluation.generated_ablation_links import (
    SLICE_GROUNDING_TARGET_SIGNALS,
    ablation_component,
    ablation_rationale_for_generated_signal,
    ablation_variants_for_generated_signal,
    signal_variant_link_priority,
)


def test_generated_signal_ablation_mapping_covers_search_diversity():
    variants = ablation_variants_for_generated_signal("search_diversity_reranking")

    assert variants[0] == "without_diversity_reranking"
    assert "without_beam_search" in variants
    assert ablation_component("without_diversity_reranking") == "search_and_repair"
    assert signal_variant_link_priority(
        "search_diversity_reranking",
        "without_diversity_reranking",
    ) > signal_variant_link_priority(
        "search_diversity_reranking",
        "without_beam_search",
    )


def test_generated_signal_ablation_mapping_covers_slice_and_ranking_signals():
    assert "weak_failed_test_reachability" in SLICE_GROUNDING_TARGET_SIGNALS
    assert ablation_variants_for_generated_signal(
        "weak_failed_test_reachability"
    ) == [
        "without_test_signals",
        "without_line_coverage",
        "without_branch_coverage",
        "without_path_coverage",
        "without_caller_impact",
    ]
    assert "slice-grounded evidence" in ablation_rationale_for_generated_signal(
        "weak_failed_test_reachability"
    )
    assert ablation_component("without_path_coverage") == "test_signal_scoring"

    assert "without_semantic_similarity" in ablation_variants_for_generated_signal(
        "fragile_top1_margin"
    )
    assert "ranking margin" in ablation_rationale_for_generated_signal(
        "fragile_top1_margin"
    )

    assert ablation_variants_for_generated_signal("subscript_key_flow") == [
        "without_data_dependency"
    ]
    assert "key-to-mapping access evidence" in ablation_rationale_for_generated_signal(
        "subscript_key_flow"
    )

    assert ablation_variants_for_generated_signal("cross_function_data_flow") == [
        "without_data_dependency"
    ]
    assert "call-boundary data-flow evidence" in (
        ablation_rationale_for_generated_signal("cross_function_data_flow")
    )

    assert ablation_variants_for_generated_signal("without_data_dependency") == [
        "without_data_dependency"
    ]
    assert ablation_variants_for_generated_signal("without_multi_patch_repair") == [
        "without_multi_patch_repair"
    ]
    assert ablation_variants_for_generated_signal("without_graph_bundle_search") == [
        "without_graph_bundle_search"
    ]
    assert ablation_variants_for_generated_signal("without_rule_precision_filter") == [
        "without_rule_precision_filter"
    ]
    assert ablation_variants_for_generated_signal("without_reflection") == [
        "without_reflection"
    ]
    assert ablation_variants_for_generated_signal("without_patch_prior") == [
        "without_patch_prior"
    ]
    assert ablation_variants_for_generated_signal(
        "without_diversity_reranking"
    ) == ["without_diversity_reranking"]
    assert ablation_variants_for_generated_signal(
        "without_candidate_deduplication"
    ) == ["without_candidate_deduplication"]


def test_generated_signal_ablation_mapping_covers_candidate_deduplication_pressure():
    variants = ablation_variants_for_generated_signal(
        "candidate_deduplication_pressure"
    )

    assert variants[0] == "without_candidate_deduplication"
    assert "without_beam_search" in variants
    assert ablation_component("without_candidate_deduplication") == (
        "search_and_repair"
    )
