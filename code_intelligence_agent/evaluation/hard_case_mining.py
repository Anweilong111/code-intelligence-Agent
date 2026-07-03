from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class HardCaseSuggestion:
    priority: str
    source: str
    benchmark_focus: str
    target_signal: str
    current_count: int
    target_count: int
    suggested_case_shape: str
    rationale: str
    examples: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HardCaseMiningReport:
    case_count: int
    suggestion_count: int
    high_priority_count: int
    suggestions: list[HardCaseSuggestion]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_count": self.case_count,
            "suggestion_count": self.suggestion_count,
            "high_priority_count": self.high_priority_count,
            "suggestions": [suggestion.to_dict() for suggestion in self.suggestions],
        }


DIFFICULTY_BUCKET_TARGETS: dict[str, int] = {
    "hard": 5,
}

DIFFICULTY_LABEL_TARGETS: dict[str, int] = {
    "multi_ground_truth": 2,
    "multi_patch_bundle": 2,
    "wide_beam_search": 2,
    "failed_before_success": 2,
    "reflection_depth": 2,
    "cross_file_patch": 3,
    "patch_candidate_competition": 8,
    "cross_function_trace": 8,
}

MIN_SOURCE_GROUPS = 4
MIN_CASES_PER_SOURCE_GROUP = 5
MAX_GENERALIZATION_GAP = 0.20
MIN_SEARCH_COMPETITION_CASES = 18
MIN_SEARCH_SCORE_INVERSION_CASES = 2
MIN_SEARCH_FAILURE_PRESSURE = 0.20
MIN_SEARCH_DIVERSITY_ASSISTED_SUCCESSES = 1
MIN_SEARCH_SUCCESS_DIVERSITY_LIFT = 1.0
MIN_SEARCH_BUDGET_SENSITIVE_DIVERSITY_SUCCESSES = 1
MIN_SEARCH_DEDUPE_AFFECTED_CASES = 1
MIN_SEARCH_DEDUPLICATED_CANDIDATES = 1
MIN_SEARCH_DUPLICATE_PRESSURE = 0.0001
MIN_REFLECTION_SUCCESS_CASES = 1
MIN_SLICE_GROUNDED_CASE_RATIO = 0.95
MIN_AVERAGE_SLICE_SUPPORT = 0.80
# Mining targets are intentionally stricter than quality-gate thresholds: the
# gate checks acceptance, while mining should keep proposing harder cases.
MIN_AVERAGE_SLICE_FAILED_TEST_REACHABILITY = 0.98
MIN_AVERAGE_SLICE_CALL_CHAIN_COVERAGE = 0.85
MAX_FRAGILE_TOP1_RATE = 0.05
MIN_SUBSCRIPT_KEY_FLOW_CASES = 3
MIN_CROSS_FUNCTION_DATA_FLOW_CASES = 8


def hard_case_mining_report(payload: dict[str, Any]) -> HardCaseMiningReport:
    benchmark = _benchmark_report(payload)
    summary = _summary(benchmark)
    suggestions: list[HardCaseSuggestion] = []
    suggestions.extend(_difficulty_suggestions(summary))
    suggestions.extend(_generalization_suggestions(summary))
    suggestions.extend(_search_competition_suggestions(benchmark, summary))
    suggestions.extend(_search_budget_suggestions(summary))
    suggestions.extend(_reflection_suggestions(benchmark, summary))
    suggestions.extend(_data_flow_evidence_suggestions(benchmark, summary))
    suggestions.extend(_slice_grounding_suggestions(benchmark, summary))
    suggestions.extend(_localization_stability_suggestions(summary))
    suggestions.extend(_ablation_suggestions(payload))
    suggestions = _dedupe_and_sort(suggestions)
    return HardCaseMiningReport(
        case_count=int(summary.get("case_count", len(benchmark.get("cases", [])))),
        suggestion_count=len(suggestions),
        high_priority_count=sum(1 for item in suggestions if item.priority == "high"),
        suggestions=suggestions,
    )


def render_hard_case_mining_markdown(report: HardCaseMiningReport | dict[str, Any]) -> str:
    payload = report.to_dict() if hasattr(report, "to_dict") else dict(report)
    lines = [
        "# Hard-Case Mining",
        "",
        (
            f"- Suggestions: {int(payload.get('suggestion_count', 0))}; "
            f"High Priority: {int(payload.get('high_priority_count', 0))}"
        ),
        "",
        "| Priority | Source | Focus | Signal | Current | Target | Suggested Case Shape | Rationale | Examples |",
        "| --- | --- | --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for item in payload.get("suggestions", []):
        if not isinstance(item, dict):
            continue
        examples = item.get("examples", [])
        if not isinstance(examples, list):
            examples = []
        lines.append(
            "| "
            f"{_markdown_cell(item.get('priority', ''))} | "
            f"{_markdown_cell(item.get('source', ''))} | "
            f"{_markdown_cell(item.get('benchmark_focus', ''))} | "
            f"{_markdown_cell(item.get('target_signal', ''))} | "
            f"{int(item.get('current_count', 0))} | "
            f"{int(item.get('target_count', 0))} | "
            f"{_markdown_cell(item.get('suggested_case_shape', ''))} | "
            f"{_markdown_cell(item.get('rationale', ''))} | "
            f"{_markdown_cell(', '.join(str(value) for value in examples[:3]))} |"
        )
    return "\n".join(lines)


def _benchmark_report(payload: dict[str, Any]) -> dict[str, Any]:
    benchmark = payload.get("benchmark_report", payload)
    return benchmark if isinstance(benchmark, dict) else {}


def _summary(benchmark: dict[str, Any]) -> dict[str, Any]:
    summary = benchmark.get("summary", {})
    return summary if isinstance(summary, dict) else {}


def _difficulty_suggestions(summary: dict[str, Any]) -> list[HardCaseSuggestion]:
    difficulty = summary.get("difficulty_report", {})
    if not isinstance(difficulty, dict) or not difficulty:
        return []
    suggestions: list[HardCaseSuggestion] = []
    bucket_counts = difficulty.get("bucket_counts", {})
    if not isinstance(bucket_counts, dict):
        bucket_counts = {}
    for bucket, target in DIFFICULTY_BUCKET_TARGETS.items():
        current = int(bucket_counts.get(bucket, 0))
        if current < target:
            suggestions.append(
                _coverage_suggestion(
                    source="difficulty_bucket",
                    signal=bucket,
                    current=current,
                    target=target,
                    examples=_difficulty_examples(difficulty, bucket=bucket),
                )
            )

    label_counts = difficulty.get("label_counts", {})
    if not isinstance(label_counts, dict):
        label_counts = {}
    for label, target in DIFFICULTY_LABEL_TARGETS.items():
        current = int(label_counts.get(label, 0))
        if current < target:
            suggestions.append(
                _coverage_suggestion(
                    source="difficulty_label",
                    signal=label,
                    current=current,
                    target=target,
                    examples=_difficulty_examples(difficulty, label=label),
                )
            )
    return suggestions


def _generalization_suggestions(summary: dict[str, Any]) -> list[HardCaseSuggestion]:
    report = summary.get("generalization_report", {})
    if not isinstance(report, dict) or not report:
        return []
    suggestions = []
    source_group_count = int(report.get("source_group_count", 0))
    if source_group_count < MIN_SOURCE_GROUPS:
        suggestions.append(
            HardCaseSuggestion(
                priority=_priority(0, MIN_SOURCE_GROUPS - source_group_count),
                source="generalization_source_groups",
                benchmark_focus="cross-project generalization expansion",
                target_signal="source_group_count",
                current_count=source_group_count,
                target_count=MIN_SOURCE_GROUPS,
                suggested_case_shape=(
                    "Add a new upstream repository with distinct coding style, "
                    "dependency shape, and pytest oracle."
                ),
                rationale=(
                    "Current benchmark has fewer source groups than the target "
                    "for cross-project holdout validation."
                ),
                examples=list(_source_groups(report))[:3],
            )
        )
    for group, metrics in _source_groups(report).items():
        case_count = int(metrics.get("case_count", 0))
        if case_count >= MIN_CASES_PER_SOURCE_GROUP:
            continue
        suggestions.append(
            HardCaseSuggestion(
                priority=_priority(case_count, MIN_CASES_PER_SOURCE_GROUP - case_count),
                source="generalization_source_group_balance",
                benchmark_focus="underrepresented source group",
                target_signal=str(group),
                current_count=case_count,
                target_count=MIN_CASES_PER_SOURCE_GROUP,
                suggested_case_shape=(
                    "Add more cases from this upstream project, covering at least "
                    "two bug types and two expected rules."
                ),
                rationale=(
                    f"{group} has fewer holdout cases than the minimum needed for "
                    "stable source-group metrics."
                ),
                examples=[str(group)],
            )
        )

    for gap_key, focus in {
        "max_top1_gap": "localization generalization gap",
        "max_map_gap": "ranking generalization gap",
        "max_patch_success_gap": "repair generalization gap",
        "max_search_efficiency_gap": "search-efficiency generalization gap",
    }.items():
        gap = float(report.get(gap_key, 0.0))
        if gap <= MAX_GENERALIZATION_GAP:
            continue
        suggestions.append(
            HardCaseSuggestion(
                priority="high",
                source="generalization_gap",
                benchmark_focus=focus,
                target_signal=gap_key,
                current_count=int(round(gap * 100)),
                target_count=int(round(MAX_GENERALIZATION_GAP * 100)),
                suggested_case_shape=(
                    "Mine holdout-source cases where train-source performance is "
                    "strong but this metric drops, then preserve the source split."
                ),
                rationale=(
                    f"{gap_key}={gap:.3f} exceeds the configured stability band."
                ),
                examples=_largest_gap_examples(report, gap_key),
            )
        )
    return suggestions


def _search_competition_suggestions(
    benchmark: dict[str, Any],
    summary: dict[str, Any],
) -> list[HardCaseSuggestion]:
    report = summary.get("search_competition_analysis", {})
    if not isinstance(report, dict) or not report:
        return []
    suggestions: list[HardCaseSuggestion] = []
    beam_cases = int(report.get("beam_case_count", 0))
    if beam_cases <= 0:
        return suggestions

    multi_candidate_count = int(report.get("multi_candidate_case_count", 0))
    if multi_candidate_count < MIN_SEARCH_COMPETITION_CASES:
        suggestions.append(
            HardCaseSuggestion(
                priority=_priority(
                    multi_candidate_count,
                    MIN_SEARCH_COMPETITION_CASES - multi_candidate_count,
                ),
                source="search_competition_gap",
                benchmark_focus="beam candidate competition pressure",
                target_signal="search_candidate_competition",
                current_count=multi_candidate_count,
                target_count=MIN_SEARCH_COMPETITION_CASES,
                suggested_case_shape=(
                    "Add cases with several plausible same-function or same-rule "
                    "patch candidates so beam ranking must choose among decoys."
                ),
                rationale=(
                    "Search Competition Analysis reports fewer multi-candidate "
                    "beam cases than the target pressure level."
                ),
                examples=_search_competition_examples(report),
            )
        )

    score_inversion_count = int(report.get("score_inversion_count", 0))
    if score_inversion_count < MIN_SEARCH_SCORE_INVERSION_CASES:
        suggestions.append(
            HardCaseSuggestion(
                priority=_priority(
                    score_inversion_count,
                    MIN_SEARCH_SCORE_INVERSION_CASES - score_inversion_count,
                ),
                source="search_competition_gap",
                benchmark_focus="top-rank decoy patch pressure",
                target_signal="search_score_inversion",
                current_count=score_inversion_count,
                target_count=MIN_SEARCH_SCORE_INVERSION_CASES,
                suggested_case_shape=(
                    "Include a high-prior decoy patch that fails sandbox tests "
                    "before a lower-ranked or bundled candidate succeeds."
                ),
                rationale=(
                    "Search Competition Analysis has too few score inversions; "
                    "add cases that force reranking beyond the first candidate."
                ),
                examples=_search_competition_examples(
                    report,
                    prefer_score_inversion=True,
                ),
            )
        )

    failure_pressure = float(report.get("average_failure_pressure", 0.0))
    if failure_pressure < MIN_SEARCH_FAILURE_PRESSURE and multi_candidate_count > 0:
        suggestions.append(
            HardCaseSuggestion(
                priority="low",
                source="search_competition_gap",
                benchmark_focus="failed candidate retention pressure",
                target_signal="search_failure_pressure",
                current_count=int(round(failure_pressure * 100)),
                target_count=int(round(MIN_SEARCH_FAILURE_PRESSURE * 100)),
                suggested_case_shape=(
                    "Add cases where retained candidates include recoverable "
                    "test failures and near misses, not only immediate successes."
                ),
                rationale=(
                    "Average beam failure pressure is below the target; add "
                    "near-miss candidates to exercise retention and feedback scoring."
                ),
                examples=_search_competition_examples(report),
            )
        )
    diversity_assisted = int(report.get("diversity_assisted_success_count", 0))
    success_diversity_lift = float(
        report.get("average_success_diversity_lift", 0.0)
    )
    budget_sensitive_diversity = max(
        int(report.get("budget_sensitive_diversity_success_count", 0)),
        _budget_sensitive_diversity_success_count(
            report,
            benchmark=benchmark,
        ),
    )
    if (
        diversity_assisted < MIN_SEARCH_DIVERSITY_ASSISTED_SUCCESSES
        or success_diversity_lift < MIN_SEARCH_SUCCESS_DIVERSITY_LIFT
        or budget_sensitive_diversity
        < MIN_SEARCH_BUDGET_SENSITIVE_DIVERSITY_SUCCESSES
    ) and multi_candidate_count > 0:
        suggestions.append(
            HardCaseSuggestion(
                priority=(
                    "high"
                    if budget_sensitive_diversity
                    < MIN_SEARCH_BUDGET_SENSITIVE_DIVERSITY_SUCCESSES
                    else "medium"
                ),
                source="search_competition_gap",
                benchmark_focus="diversity reranking lift pressure",
                target_signal="search_diversity_reranking",
                current_count=budget_sensitive_diversity,
                target_count=MIN_SEARCH_BUDGET_SENSITIVE_DIVERSITY_SUCCESSES,
                suggested_case_shape=(
                    "Add cases where same-rule decoy patches fill the execution "
                    "budget and a different-rule successful candidate only enters "
                    "the beam after diversity-aware reranking."
                ),
                rationale=(
                    "Search Competition Analysis has insufficient diversity-assisted "
                    "budget-sensitive success evidence; add a probe that proves a "
                    "successful candidate starts outside the execution budget and "
                    "is moved inside it by diversity-aware reranking."
                ),
                examples=_search_competition_examples(
                    report,
                    benchmark=benchmark,
                    prefer_budget_sensitive=True,
                ),
            )
        )
    return suggestions


def _search_budget_suggestions(summary: dict[str, Any]) -> list[HardCaseSuggestion]:
    report = summary.get("search_budget_analysis", {})
    if not isinstance(report, dict) or not report:
        return []
    dedupe_cases = int(report.get("dedupe_affected_case_count", 0))
    deduplicated = int(report.get("total_deduplicated_candidates", 0))
    pressure = float(report.get("average_duplicate_pressure", 0.0))
    if (
        dedupe_cases >= MIN_SEARCH_DEDUPE_AFFECTED_CASES
        and deduplicated >= MIN_SEARCH_DEDUPLICATED_CANDIDATES
        and pressure >= MIN_SEARCH_DUPLICATE_PRESSURE
    ):
        return []
    return [
        HardCaseSuggestion(
            priority="high" if dedupe_cases == 0 else "medium",
            source="search_budget_gap",
            benchmark_focus="candidate deduplication budget pressure",
            target_signal="candidate_deduplication_pressure",
            current_count=dedupe_cases,
            target_count=MIN_SEARCH_DEDUPE_AFFECTED_CASES,
            suggested_case_shape=(
                "Add a case where exact duplicate failed patches occupy the "
                "sandbox budget until candidate fingerprint deduplication lets "
                "a later unique successful patch run."
            ),
            rationale=(
                "Search Budget Analysis has insufficient candidate deduplication "
                "pressure evidence; add a probe that proves duplicate filtering "
                "saves execution budget rather than only changing metadata."
            ),
            examples=_search_budget_examples(report),
        )
    ]


def _reflection_suggestions(
    benchmark: dict[str, Any],
    summary: dict[str, Any],
) -> list[HardCaseSuggestion]:
    report = summary.get("reflection_analysis", {})
    if not isinstance(report, dict) or not report:
        return []
    success_cases = int(report.get("reflection_success_case_count", 0))
    if success_cases >= MIN_REFLECTION_SUCCESS_CASES:
        return []
    candidate_count = int(report.get("reflection_candidate_count", 0))
    return [
        HardCaseSuggestion(
            priority="high" if candidate_count == 0 else "medium",
            source="reflection_analysis_gap",
            benchmark_focus="execution-feedback reflection recovery",
            target_signal="reflection_depth",
            current_count=success_cases,
            target_count=MIN_REFLECTION_SUCCESS_CASES,
            suggested_case_shape=(
                "Add a case where the best depth-0 patch fails sandbox tests, "
                "then execution feedback guides a refined child patch to success."
            ),
            rationale=(
                "Reflection analysis has no successful refined-child repair, "
                "so the self-repair loop is not yet proven by benchmark evidence."
            ),
            examples=_reflection_examples(benchmark, report),
        )
    ]


def _slice_grounding_suggestions(
    benchmark: dict[str, Any],
    summary: dict[str, Any],
) -> list[HardCaseSuggestion]:
    slice_keys = {
        "slice_grounded_case_count",
        "average_top1_slice_support",
        "average_top1_slice_failed_test_reachability",
        "average_top1_slice_call_chain_coverage",
    }
    if not any(key in summary for key in slice_keys):
        return []

    suggestions: list[HardCaseSuggestion] = []
    case_count = int(summary.get("case_count", len(benchmark.get("cases", []))) or 0)
    if case_count <= 0:
        return suggestions

    if "slice_grounded_case_count" in summary:
        grounded_count = int(summary.get("slice_grounded_case_count", 0))
        target = int(round(case_count * MIN_SLICE_GROUNDED_CASE_RATIO))
        ratio = grounded_count / case_count
        if ratio < MIN_SLICE_GROUNDED_CASE_RATIO:
            suggestions.append(
                HardCaseSuggestion(
                    priority=_priority(grounded_count, target - grounded_count),
                    source="slice_grounding_gap",
                    benchmark_focus="program-slice grounded localization",
                    target_signal="weak_slice_grounding",
                    current_count=grounded_count,
                    target_count=target,
                    suggested_case_shape=(
                        "Add multi-hop wrapper or cross-file cases where failed "
                        "tests must reach the buggy function through explicit "
                        "calls, data-flow and CFG evidence."
                    ),
                    rationale=(
                        "The share of Top-1 localization results backed by "
                        f"program-slice evidence is {ratio:.3f}, below the "
                        f"target {MIN_SLICE_GROUNDED_CASE_RATIO:.2f}."
                    ),
                    examples=_slice_grounding_examples(
                        benchmark,
                        score_key="support_score",
                    ),
                )
            )

    average_support = summary.get("average_top1_slice_support")
    if average_support is not None:
        average_support = float(average_support)
        if average_support < MIN_AVERAGE_SLICE_SUPPORT:
            suggestions.append(
                _slice_metric_suggestion(
                    benchmark=benchmark,
                    signal="weak_slice_support",
                    focus="program-slice support score hardening",
                    metric_name="average_top1_slice_support",
                    value=average_support,
                    target=MIN_AVERAGE_SLICE_SUPPORT,
                    shape=(
                        "Add cases whose correct target has richer slice evidence "
                        "than decoy functions, including def-use, call-chain and "
                        "control-flow support."
                    ),
                )
            )

    reachability = summary.get("average_top1_slice_failed_test_reachability")
    if reachability is not None:
        reachability = float(reachability)
        if reachability < MIN_AVERAGE_SLICE_FAILED_TEST_REACHABILITY:
            suggestions.append(
                _slice_metric_suggestion(
                    benchmark=benchmark,
                    signal="weak_failed_test_reachability",
                    focus="failed-test reachability grounding",
                    metric_name="average_top1_slice_failed_test_reachability",
                    value=reachability,
                    target=MIN_AVERAGE_SLICE_FAILED_TEST_REACHABILITY,
                    shape=(
                        "Add cases where failing tests are connected to the "
                        "buggy function through explicit test -> caller -> target "
                        "paths rather than relying only on static rule evidence."
                    ),
                )
            )

    call_chain_coverage = summary.get("average_top1_slice_call_chain_coverage")
    if call_chain_coverage is not None:
        call_chain_coverage = float(call_chain_coverage)
        if call_chain_coverage < MIN_AVERAGE_SLICE_CALL_CHAIN_COVERAGE:
            suggestions.append(
                _slice_metric_suggestion(
                    benchmark=benchmark,
                    signal="weak_call_chain_coverage",
                    focus="call-chain edge coverage grounding",
                    metric_name="average_top1_slice_call_chain_coverage",
                    value=call_chain_coverage,
                    target=MIN_AVERAGE_SLICE_CALL_CHAIN_COVERAGE,
                    shape=(
                        "Add two-hop or cross-module failing traces so the "
                        "shortest failed call chain is fully represented in the "
                        "program graph."
                    ),
                )
            )
    return suggestions


def _data_flow_evidence_suggestions(
    benchmark: dict[str, Any],
    summary: dict[str, Any],
) -> list[HardCaseSuggestion]:
    suggestions: list[HardCaseSuggestion] = []
    if "cross_function_data_flow_case_count" in summary:
        current = int(summary.get("cross_function_data_flow_case_count", 0))
        if current < MIN_CROSS_FUNCTION_DATA_FLOW_CASES:
            suggestions.append(
                HardCaseSuggestion(
                    priority=_priority(
                        current,
                        MIN_CROSS_FUNCTION_DATA_FLOW_CASES - current,
                    ),
                    source="data_flow_evidence_gap",
                    benchmark_focus="cross-function data-flow graph evidence",
                    target_signal="cross_function_data_flow",
                    current_count=current,
                    target_count=MIN_CROSS_FUNCTION_DATA_FLOW_CASES,
                    suggested_case_shape=(
                        "Add wrapper or cross-file caller cases where arguments "
                        "flow into callee parameters and assigned return values "
                        "flow back to caller variables."
                    ),
                    rationale=(
                        "Top-1 data-flow evidence has too few cross-function "
                        "data-flow cases; add benchmark cases that require the "
                        "data_dependency graph component to reason across call "
                        "boundaries."
                    ),
                    examples=_cross_function_data_flow_examples(benchmark),
                )
            )

    if "subscript_key_flow_case_count" not in summary:
        return suggestions
    current = int(summary.get("subscript_key_flow_case_count", 0))
    if current >= MIN_SUBSCRIPT_KEY_FLOW_CASES:
        return suggestions
    suggestions.append(
        HardCaseSuggestion(
            priority=_priority(
                current,
                MIN_SUBSCRIPT_KEY_FLOW_CASES - current,
            ),
            source="data_flow_evidence_gap",
            benchmark_focus="subscript key-flow graph evidence",
            target_signal="subscript_key_flow",
            current_count=current,
            target_count=MIN_SUBSCRIPT_KEY_FLOW_CASES,
            suggested_case_shape=(
                "Add mapping lookup cases where a key variable flows into a "
                "subscript access and the repair must restore guarded lookup "
                "semantics."
            ),
            rationale=(
                "Top-1 data-flow evidence has too few subscript key-flow cases; "
                "add dict-key guard benchmarks so the data_dependency graph "
                "component is tested on key-to-mapping access patterns."
            ),
            examples=_subscript_key_flow_examples(benchmark),
        )
    )
    return suggestions


def _slice_metric_suggestion(
    *,
    benchmark: dict[str, Any],
    signal: str,
    focus: str,
    metric_name: str,
    value: float,
    target: float,
    shape: str,
) -> HardCaseSuggestion:
    return HardCaseSuggestion(
        priority="high" if value < target * 0.75 else "medium",
        source="slice_grounding_gap",
        benchmark_focus=focus,
        target_signal=signal,
        current_count=int(round(value * 100)),
        target_count=int(round(target * 100)),
        suggested_case_shape=shape,
        rationale=(
            f"{metric_name}={value:.3f} is below the target {target:.2f}; "
            "generate cases that make program-slice evidence necessary for "
            "credible localization."
        ),
        examples=_slice_grounding_examples(benchmark, score_key=_slice_score_key(signal)),
    )


def _localization_stability_suggestions(
    summary: dict[str, Any],
) -> list[HardCaseSuggestion]:
    attribution = summary.get("localization_attribution", {})
    if not isinstance(attribution, dict) or not attribution:
        return []
    fragile_rate = float(attribution.get("fragile_top1_rate", 0.0))
    fragile_count = int(attribution.get("fragile_top1_case_count", 0))
    if fragile_rate <= MAX_FRAGILE_TOP1_RATE:
        return []
    return [
        HardCaseSuggestion(
            priority="high" if fragile_rate >= 0.15 else "medium",
            source="localization_attribution_gap",
            benchmark_focus="fragile Top-1 localization hardening",
            target_signal="fragile_top1_margin",
            current_count=fragile_count,
            target_count=0,
            suggested_case_shape=(
                "Add near-tie localization cases where static, graph and "
                "semantic signals disagree, then require attribution or "
                "counterfactual checks to explain the final Top-1."
            ),
            rationale=(
                f"fragile_top1_rate={fragile_rate:.3f} exceeds the target "
                f"{MAX_FRAGILE_TOP1_RATE:.2f}; add benchmark seeds that expose "
                "small-margin ranking decisions."
            ),
            examples=_fragile_top1_examples(attribution),
        )
    ]


def _ablation_suggestions(payload: dict[str, Any]) -> list[HardCaseSuggestion]:
    impact = payload.get("ablation_impact", {})
    if not isinstance(impact, dict) or not impact:
        return []
    rows = impact.get("rows", [])
    if not isinstance(rows, list):
        return []
    suggestions = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        score = float(row.get("impact_score", 0.0))
        direction = str(row.get("direction", ""))
        if direction != "regression" and score > -0.05:
            continue
        variant = str(row.get("variant", ""))
        focus, shape = _ablation_focus(variant)
        suggestions.append(
            HardCaseSuggestion(
                priority="high" if score <= -0.10 else "medium",
                source="ablation_regression",
                benchmark_focus=focus,
                target_signal=variant,
                current_count=1,
                target_count=2,
                suggested_case_shape=shape,
                rationale=(
                    f"Ablation variant {variant} regressed with impact_score="
                    f"{score:.3f}; add cases that isolate this dependency."
                ),
                examples=[variant],
            )
        )
    return suggestions


def _coverage_suggestion(
    *,
    source: str,
    signal: str,
    current: int,
    target: int,
    examples: list[str],
) -> HardCaseSuggestion:
    return HardCaseSuggestion(
        priority=_priority(current, target - current),
        source=source,
        benchmark_focus=_focus_for_signal(signal),
        target_signal=signal,
        current_count=current,
        target_count=target,
        suggested_case_shape=_case_shape_for_signal(signal),
        rationale=(
            f"{signal} coverage is {current}, below the target {target}; "
            "mine or compose additional cases for this stress dimension."
        ),
        examples=examples,
    )


def _difficulty_examples(
    difficulty: dict[str, Any],
    *,
    bucket: str | None = None,
    label: str | None = None,
) -> list[str]:
    rows = difficulty.get("cases", [])
    if not isinstance(rows, list):
        return []
    examples = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        labels = row.get("labels", [])
        if not isinstance(labels, list):
            labels = []
        if bucket is not None and row.get("bucket") != bucket:
            continue
        if label is not None and label not in labels:
            continue
        examples.append(str(row.get("case_name", "")))
    return [name for name in examples if name][:3]


def _reflection_examples(
    benchmark: dict[str, Any],
    report: dict[str, Any],
) -> list[str]:
    rows = report.get("rows", [])
    examples = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            case_name = str(row.get("case", ""))
            if not case_name:
                continue
            candidates = int(row.get("reflection_candidate_count", 0))
            successes = int(row.get("successful_reflection_candidate_count", 0))
            if candidates <= 0 and successes <= 0:
                continue
            parent_failures = row.get("parent_failure_types", [])
            if not isinstance(parent_failures, list):
                parent_failures = []
            parent = str(parent_failures[0]) if parent_failures else ""
            label = f"{case_name}:reflections={candidates}:successes={successes}"
            if parent:
                label = f"{label}:parent={parent}"
            examples.append(label)
            if len(examples) >= 3:
                return examples
    cases = benchmark.get("cases", [])
    if not isinstance(cases, list):
        return examples
    for case in cases:
        if not isinstance(case, dict):
            continue
        beam_nodes = case.get("beam_search_results", [])
        if not isinstance(beam_nodes, list) or not beam_nodes:
            continue
        has_reflection = any(
            isinstance(node, dict)
            and (
                int(node.get("depth", 0) or 0) > 0
                or bool(node.get("parent_id"))
            )
            for node in beam_nodes
        )
        if has_reflection:
            continue
        first_failed = next(
            (
                node
                for node in beam_nodes
                if isinstance(node, dict)
                and not bool(node.get("success", False))
            ),
            None,
        )
        if not first_failed:
            continue
        case_name = str(case.get("case_name", case.get("name", "")))
        failure_type = str(first_failed.get("failure_type", ""))
        if case_name:
            examples.append(f"{case_name}:depth0_failure={failure_type}")
        if len(examples) >= 3:
            break
    return examples


def _slice_grounding_examples(
    benchmark: dict[str, Any],
    *,
    score_key: str,
) -> list[str]:
    cases = benchmark.get("cases", [])
    if not isinstance(cases, list):
        return []
    rows = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        details = case.get("localization_details", [])
        if not isinstance(details, list) or not details:
            continue
        top_detail = details[0] if isinstance(details[0], dict) else {}
        grounding = top_detail.get("slice_grounding", {})
        if not isinstance(grounding, dict):
            continue
        value = float(grounding.get(score_key, grounding.get("support_score", 0.0)))
        rows.append(
            (
                value,
                str(case.get("case_name", "")),
                str(top_detail.get("function_name", "")),
            )
        )
    rows.sort(key=lambda item: (item[0], item[1], item[2]))
    examples = []
    for value, case_name, function_name in rows[:3]:
        if not case_name:
            continue
        if function_name:
            examples.append(f"{case_name}:{function_name}:{value:.3f}")
        else:
            examples.append(f"{case_name}:{value:.3f}")
    return examples


def _slice_score_key(signal: str) -> str:
    return {
        "weak_failed_test_reachability": "failed_test_reachability",
        "weak_call_chain_coverage": "call_chain_edge_coverage",
        "weak_slice_support": "support_score",
        "weak_slice_grounding": "support_score",
    }.get(signal, "support_score")


def _subscript_key_flow_examples(benchmark: dict[str, Any]) -> list[str]:
    cases = benchmark.get("cases", [])
    if not isinstance(cases, list):
        return []
    rows = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        details = case.get("localization_details", [])
        if not isinstance(details, list) or not details:
            continue
        top_detail = details[0] if isinstance(details[0], dict) else {}
        evidence = top_detail.get("data_flow_evidence", {})
        if not isinstance(evidence, dict):
            continue
        rows.append(
            (
                int(evidence.get("key_flow_edges", 0) or 0),
                str(case.get("case_name", case.get("name", ""))),
                str(top_detail.get("function_name", "")),
            )
        )
    rows.sort(key=lambda item: (item[0], item[1], item[2]))
    examples = []
    for key_flow_edges, case_name, function_name in rows[:3]:
        if not case_name:
            continue
        label = f"{case_name}:key_flow_edges={key_flow_edges}"
        if function_name:
            label = f"{label}:{function_name}"
        examples.append(label)
    return examples


def _cross_function_data_flow_examples(benchmark: dict[str, Any]) -> list[str]:
    cases = benchmark.get("cases", [])
    if not isinstance(cases, list):
        return []
    rows = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        details = case.get("localization_details", [])
        if not isinstance(details, list) or not details:
            continue
        top_detail = details[0] if isinstance(details[0], dict) else {}
        evidence = top_detail.get("data_flow_evidence", {})
        if not isinstance(evidence, dict):
            continue
        rows.append(
            (
                int(evidence.get("cross_function_edges", 0) or 0),
                str(case.get("case_name", case.get("name", ""))),
                str(top_detail.get("function_name", "")),
            )
        )
    rows.sort(key=lambda item: (item[0], item[1], item[2]))
    examples = []
    for cross_edges, case_name, function_name in rows[:3]:
        if not case_name:
            continue
        label = f"{case_name}:cross_function_edges={cross_edges}"
        if function_name:
            label = f"{label}:{function_name}"
        examples.append(label)
    return examples


def _fragile_top1_examples(attribution: dict[str, Any]) -> list[str]:
    cases = attribution.get("fragile_top1_cases", [])
    if not isinstance(cases, list):
        return []
    examples = []
    for case in cases[:3]:
        if isinstance(case, dict):
            name = str(case.get("case_name", case.get("case", "")))
            margin = case.get("top1_margin", case.get("margin"))
            if name and margin is not None:
                examples.append(f"{name}:margin={float(margin):.4f}")
            elif name:
                examples.append(name)
        elif str(case):
            examples.append(str(case))
    return examples


def _source_groups(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    groups = report.get("source_groups", {})
    if not isinstance(groups, dict):
        return {}
    return {
        str(group): metrics if isinstance(metrics, dict) else {}
        for group, metrics in groups.items()
    }


def _largest_gap_examples(report: dict[str, Any], gap_key: str) -> list[str]:
    field = {
        "max_top1_gap": "top1_gap",
        "max_map_gap": "map_gap",
        "max_patch_success_gap": "patch_success_gap",
        "max_search_efficiency_gap": "search_efficiency_gap",
    }.get(gap_key, "")
    splits = report.get("holdout_splits", [])
    if not isinstance(splits, list) or not field:
        return []
    rows = [
        split
        for split in splits
        if isinstance(split, dict)
    ]
    rows.sort(key=lambda item: abs(float(item.get(field, 0.0))), reverse=True)
    return [str(row.get("holdout_group", "")) for row in rows[:3]]


def _search_competition_examples(
    report: dict[str, Any],
    *,
    benchmark: dict[str, Any] | None = None,
    prefer_score_inversion: bool = False,
    prefer_budget_sensitive: bool = False,
) -> list[str]:
    candidates = _search_competition_example_rows(report, benchmark=benchmark)
    if prefer_budget_sensitive:
        candidates.sort(
            key=lambda row: (
                not bool(row.get("budget_sensitive_diversity_success", False)),
                not _has_success_candidate_evidence(row),
                _budget_condition_rank(row),
                -int(row.get("success_budget_gap_before_rerank", 0)),
                -int(row.get("success_diversity_lift", 0)),
                -int(row.get("evaluated_nodes", 0)),
                str(row.get("case", "")),
            )
        )
    if prefer_score_inversion:
        candidates.sort(
            key=lambda row: (
                not bool(row.get("score_inversion", False)),
                -int(row.get("evaluated_nodes", 0)),
                str(row.get("case", "")),
            )
        )
    elif not prefer_budget_sensitive:
        candidates.sort(
            key=lambda row: (
                -int(row.get("evaluated_nodes", 0)),
                -float(row.get("failure_pressure", 0.0)),
                str(row.get("case", "")),
            )
        )
    return [
        _search_competition_example_label(
            row,
            include_counterfactual=prefer_budget_sensitive,
        )
        for row in candidates[:3]
        if str(row.get("case", ""))
    ]


def _search_budget_examples(report: dict[str, Any]) -> list[str]:
    rows = report.get("rows", [])
    candidates = (
        [row for row in rows if isinstance(row, dict)]
        if isinstance(rows, list)
        else []
    )
    candidates.sort(
        key=lambda row: (
            -int(row.get("deduplicated_candidates", 0)),
            -float(row.get("duplicate_pressure", 0.0)),
            str(row.get("case", "")),
        )
    )
    examples = [
        (
            f"{row.get('case', '')}:"
            f"deduped={int(row.get('deduplicated_candidates', 0))};"
            f"pressure={float(row.get('duplicate_pressure', 0.0)):.4f};"
            f"effective_pool={int(row.get('effective_candidate_pool', 0))}"
        )
        for row in candidates[:3]
        if str(row.get("case", ""))
    ]
    if examples:
        return examples
    return [
        (
            "summary:"
            f"dedupe_cases={int(report.get('dedupe_affected_case_count', 0))};"
            f"deduped={int(report.get('total_deduplicated_candidates', 0))};"
            "avg_pressure="
            f"{float(report.get('average_duplicate_pressure', 0.0)):.4f}"
        )
    ]


def _budget_sensitive_diversity_success_count(
    report: dict[str, Any],
    *,
    benchmark: dict[str, Any] | None = None,
) -> int:
    return sum(
        1
        for row in _search_competition_example_rows(report, benchmark=benchmark)
        if bool(row.get("budget_sensitive_diversity_success", False))
    )


def _search_competition_example_rows(
    report: dict[str, Any],
    *,
    benchmark: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows = report.get("rows", [])
    candidates = (
        [row for row in rows if isinstance(row, dict)]
        if isinstance(rows, list)
        else []
    )
    by_case = {
        str(row.get("case", "")): dict(row)
        for row in candidates
        if str(row.get("case", ""))
    }
    if benchmark is None:
        return candidates
    cases = benchmark.get("cases", [])
    if not isinstance(cases, list):
        return candidates
    for case in cases:
        if not isinstance(case, dict):
            continue
        audit = case.get("search_competition_audit", {})
        if not isinstance(audit, dict):
            continue
        name = str(
            audit.get(
                "case",
                case.get("case_name", case.get("name", "")),
            )
        )
        if not name:
            continue
        merged = {**by_case.get(name, {}), **audit, "case": name}
        by_case[name] = merged
    return list(by_case.values())


def _search_competition_example_label(
    row: dict[str, Any],
    *,
    include_counterfactual: bool,
) -> str:
    case = str(row.get("case", ""))
    if not include_counterfactual:
        return case
    condition = str(row.get("counterfactual_condition", ""))
    if not condition or condition == "no_success_candidate":
        return case
    gap = int(row.get("success_budget_gap_before_rerank", 0))
    margin = int(row.get("success_budget_margin_after_rerank", 0))
    return f"{case}:{condition}:gap={gap}:margin={margin}"


def _has_success_candidate_evidence(row: dict[str, Any]) -> bool:
    if int(row.get("successful_nodes", 0)) > 0:
        return True
    if row.get("first_success_rank") is not None:
        return True
    if row.get("success_actual_rank") is not None:
        return True
    return str(row.get("counterfactual_condition", "")) not in {
        "",
        "no_success_candidate",
    }


def _budget_condition_rank(row: dict[str, Any]) -> int:
    condition = str(row.get("counterfactual_condition", ""))
    return {
        "base_rank_outside_budget_and_reranked_inside_budget": 0,
        "base_rank_outside_budget_and_still_outside_budget": 1,
        "base_rank_already_inside_budget": 2,
        "missing_base_rank": 3,
        "no_success_candidate": 4,
    }.get(condition, 5)


def _ablation_focus(variant: str) -> tuple[str, str]:
    if variant == "without_static_rules":
        return (
            "static-rule-dependent localization",
            "Add ambiguous functions where graph/test signals are noisy and only the static rule identifies the buggy function.",
        )
    if variant == "without_beam_search":
        return (
            "beam-search-dependent repair",
            "Add cases with plausible decoy patches that fail tests before a lower-ranked or refined candidate succeeds.",
        )
    if variant == "without_reflection":
        return (
            "execution-feedback reflection recovery",
            "Add cases where the best depth-0 patch fails and a refined child patch succeeds after sandbox feedback.",
        )
    if variant == "without_patch_prior":
        return (
            "patch-prior-sensitive repair ranking",
            "Add cases where a high-prior decoy patch fails before a lower-ranked successful patch is found.",
        )
    if variant == "without_diversity_reranking":
        return (
            "diversity reranking lift pressure",
            "Add cases where same-rule decoy patches fill the budget until diversity reranking promotes a distinct successful candidate.",
        )
    if variant == "without_candidate_deduplication":
        return (
            "candidate deduplication budget pressure",
            "Add cases where exact duplicate failed patches fill the sandbox budget until candidate fingerprint deduplication frees a later successful patch.",
        )
    if variant == "without_data_dependency":
        return (
            "data-dependency graph reasoning",
            "Add cases where def-use, cross-function data-flow or key-flow evidence changes the Top-1 localization decision.",
        )
    if variant == "without_rule_precision_filter":
        return (
            "rule precision hardening",
            "Add functions with multiple tempting static findings but only one expected rule should survive precision filtering.",
        )
    if variant == "without_multi_patch_repair":
        return (
            "multi-patch repair dependency",
            "Add cases requiring coordinated edits across two buggy functions or files.",
        )
    if variant == "without_graph_bundle_search":
        return (
            "graph-bundle repair dependency",
            "Add cross-file multi-function cases where the correct patch bundle is selected by graph evidence.",
        )
    return (
        "ablation-sensitive benchmark focus",
        "Add cases that isolate the regressed component and keep all other signals controlled.",
    )


def _focus_for_signal(signal: str) -> str:
    return {
        "hard": "hard benchmark coverage",
        "multi_ground_truth": "multi-bug localization",
        "multi_patch_bundle": "multi-patch repair",
        "wide_beam_search": "wide beam search pressure",
        "failed_before_success": "reflection after failed attempts",
        "reflection_depth": "multi-round reflection",
        "cross_file_patch": "cross-file patch risk",
        "patch_candidate_competition": "patch candidate competition",
        "cross_function_trace": "multi-hop call trace",
        "search_candidate_competition": "beam candidate competition pressure",
        "search_score_inversion": "top-rank decoy patch pressure",
        "search_diversity_reranking": "diversity reranking lift pressure",
        "candidate_deduplication_pressure": "candidate deduplication budget pressure",
        "search_failure_pressure": "failed candidate retention pressure",
        "cross_function_data_flow": "cross-function data-flow graph evidence",
        "subscript_key_flow": "subscript key-flow graph evidence",
    }.get(signal, signal)


def _case_shape_for_signal(signal: str) -> str:
    return {
        "hard": "Compose cases with multiple difficulty labels, such as cross-file patch plus candidate competition.",
        "multi_ground_truth": "Create a benchmark case with two independent buggy functions and MAP-oriented localization labels.",
        "multi_patch_bundle": "Create a repair case where tests pass only after applying a coordinated bundle of patches.",
        "wide_beam_search": "Add multiple plausible patch candidates so beam width and retention policy affect success.",
        "failed_before_success": "Include a high-scoring decoy patch that fails sandbox tests before the successful patch.",
        "reflection_depth": "Require at least one refinement round after execution feedback to reach the correct patch.",
        "cross_file_patch": "Use callers from another module so patch risk and module dependency evidence matter.",
        "patch_candidate_competition": "Generate competing rule candidates for the same function and verify ranking by execution feedback.",
        "cross_function_trace": "Use a two-hop or deeper failing call chain from test to buggy function.",
        "search_candidate_competition": "Generate multiple plausible beam candidates for the same repair target and keep execution feedback as the arbiter.",
        "search_score_inversion": "Create a high-scoring decoy patch that fails before a later candidate succeeds.",
        "search_diversity_reranking": "Create same-rule decoys that require diversity reranking to execute a distinct successful patch.",
        "candidate_deduplication_pressure": "Create exact duplicate failed patches that require fingerprint deduplication to preserve sandbox budget for a later success.",
        "search_failure_pressure": "Retain recoverable failed candidates so feedback-aware beam pruning is exercised.",
        "cross_function_data_flow": "Generate wrapper or cross-file caller cases whose graph evidence includes arg-to-param or return-to-variable data-flow.",
        "subscript_key_flow": "Generate dict-key lookup cases where key-to-mapping data-flow affects localization and patch-risk evidence.",
    }.get(signal, "Mine additional cases matching this signal.")


def _priority(current: int, gap: int) -> str:
    if current == 0 or gap >= 3:
        return "high"
    if gap >= 2:
        return "medium"
    return "low"


def _dedupe_and_sort(
    suggestions: list[HardCaseSuggestion],
) -> list[HardCaseSuggestion]:
    deduped: dict[tuple[str, str], HardCaseSuggestion] = {}
    for suggestion in suggestions:
        deduped[(suggestion.source, suggestion.target_signal)] = suggestion
    return sorted(
        deduped.values(),
        key=lambda item: (
            _priority_rank(item.priority),
            item.target_count - item.current_count,
            item.source,
            item.target_signal,
        ),
        reverse=True,
    )


def _priority_rank(priority: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(priority, 0)


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")
