from __future__ import annotations


SLICE_GROUNDING_TARGET_SIGNALS = {
    "weak_slice_grounding",
    "weak_slice_support",
    "weak_failed_test_reachability",
    "weak_call_chain_coverage",
}


GENERATED_SIGNAL_ABLATION_VARIANTS: dict[str, tuple[str, ...]] = {
    "search_score_inversion": (
        "without_patch_prior",
        "without_diversity_reranking",
        "without_beam_search",
        "without_reflection",
    ),
    "search_candidate_competition": (
        "without_beam_search",
        "without_candidate_deduplication",
        "without_diversity_reranking",
        "without_multi_patch_repair",
        "without_patch_prior",
    ),
    "search_failure_pressure": (
        "without_beam_search",
        "without_reflection",
        "without_diversity_reranking",
        "without_patch_prior",
    ),
    "reflection_depth": (
        "without_reflection",
        "without_beam_search",
    ),
    "search_diversity_reranking": (
        "without_diversity_reranking",
        "without_beam_search",
        "without_patch_prior",
    ),
    "candidate_deduplication_pressure": (
        "without_candidate_deduplication",
        "without_beam_search",
    ),
    "weak_slice_grounding": (
        "without_data_dependency",
        "without_control_flow",
        "without_caller_impact",
        "without_module_dependency",
        "without_line_coverage",
        "without_path_coverage",
        "without_branch_coverage",
    ),
    "weak_slice_support": (
        "without_data_dependency",
        "without_control_flow",
        "without_caller_impact",
        "without_module_dependency",
        "without_line_coverage",
        "without_path_coverage",
    ),
    "weak_failed_test_reachability": (
        "without_test_signals",
        "without_line_coverage",
        "without_branch_coverage",
        "without_path_coverage",
        "without_caller_impact",
    ),
    "weak_call_chain_coverage": (
        "without_caller_impact",
        "without_module_dependency",
        "without_async_call_graph",
        "without_pagerank",
    ),
    "fragile_top1_margin": (
        "without_data_dependency",
        "without_control_flow",
        "without_test_signals",
        "without_semantic_similarity",
        "without_pagerank",
        "without_static_rules",
    ),
    "cross_function_data_flow": ("without_data_dependency",),
    "subscript_key_flow": ("without_data_dependency",),
    "without_data_dependency": ("without_data_dependency",),
    "without_graph_bundle_search": ("without_graph_bundle_search",),
    "without_multi_patch_repair": ("without_multi_patch_repair",),
    "without_static_rules": ("without_static_rules",),
    "without_rule_precision_filter": ("without_rule_precision_filter",),
    "without_beam_search": ("without_beam_search",),
    "without_reflection": ("without_reflection",),
    "without_patch_prior": ("without_patch_prior",),
    "without_diversity_reranking": ("without_diversity_reranking",),
    "without_candidate_deduplication": ("without_candidate_deduplication",),
}


ABLATION_VARIANT_COMPONENTS: dict[str, str] = {
    "without_static_rules": "static_rule_reasoning",
    "without_rule_precision_filter": "static_rule_reasoning",
    "without_test_signals": "test_signal_scoring",
    "without_line_coverage": "test_signal_scoring",
    "without_branch_coverage": "test_signal_scoring",
    "without_path_coverage": "test_signal_scoring",
    "without_data_dependency": "program_graph_reasoning",
    "without_control_flow": "program_graph_reasoning",
    "without_pagerank": "program_graph_reasoning",
    "without_caller_impact": "program_graph_reasoning",
    "without_module_dependency": "program_graph_reasoning",
    "without_async_call_graph": "program_graph_reasoning",
    "without_graph_bundle_search": "program_graph_reasoning",
    "without_beam_search": "search_and_repair",
    "without_reflection": "search_and_repair",
    "without_patch_prior": "search_and_repair",
    "without_diversity_reranking": "search_and_repair",
    "without_candidate_deduplication": "search_and_repair",
    "without_multi_patch_repair": "search_and_repair",
    "without_semantic_similarity": "semantic_llm_scoring",
    "without_llm_score": "semantic_llm_scoring",
}


def ablation_variants_for_generated_signal(signal: str) -> list[str]:
    return list(GENERATED_SIGNAL_ABLATION_VARIANTS.get(signal, ()))


def ablation_rationale_for_generated_signal(signal: str) -> str:
    if signal in SLICE_GROUNDING_TARGET_SIGNALS:
        return (
            "target signal probes slice-grounded evidence stability for this "
            "graph/test component"
        )
    if signal == "fragile_top1_margin":
        return (
            "target signal probes whether ranking margin depends on this "
            "scoring component"
        )
    if signal == "subscript_key_flow":
        return (
            "target signal probes whether key-to-mapping access evidence depends "
            "on the data-dependency graph component"
        )
    if signal == "cross_function_data_flow":
        return (
            "target signal probes whether call-boundary data-flow evidence "
            "depends on the data-dependency graph component"
        )
    return "target signal maps to the same algorithm component"


def signal_variant_link_priority(signal: str, variant: str) -> int:
    if signal == "search_diversity_reranking" and (
        variant == "without_diversity_reranking"
    ):
        return 1
    return 0


def ablation_component(variant: str) -> str:
    return ABLATION_VARIANT_COMPONENTS.get(variant, "other")
