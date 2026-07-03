from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.benchmark_cross_file_composer import (
    compose_cross_file_benchmarks,
)
from code_intelligence_agent.evaluation.benchmark_multi_bug_composer import (
    compose_multi_bug_benchmarks,
)
from code_intelligence_agent.evaluation.benchmark_provenance import (
    benchmark_provenance_summary,
)
from code_intelligence_agent.evaluation.benchmark_validator import BenchmarkValidator
from code_intelligence_agent.evaluation.hard_case_mining import (
    HardCaseMiningReport,
    HardCaseSuggestion,
    hard_case_mining_report,
)


@dataclass(frozen=True)
class HardCaseGenerationRow:
    target_signal: str
    priority: str
    strategy: str
    status: str
    generated_count: int
    reasons: list[str]
    include_rules: list[str]
    wrapper_depth: int = 0
    source: str = ""
    benchmark_focus: str = ""
    template_case_names: list[str] | None = None
    selection_policy: str = ""
    selected_candidate_ids: list[str] | None = None
    selected_candidate_scores: dict[str, float] | None = None
    selection_reasons: dict[str, list[str]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HardCaseGenerationReport:
    suite_path: str
    catalog_path: str
    suggestion_count: int
    generated_count: int
    skipped_count: int
    rows: list[HardCaseGenerationRow]
    template: dict[str, Any]
    mining_report: HardCaseMiningReport
    selection_audit: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_path": self.suite_path,
            "catalog_path": self.catalog_path,
            "suggestion_count": self.suggestion_count,
            "generated_count": self.generated_count,
            "skipped_count": self.skipped_count,
            "rows": [row.to_dict() for row in self.rows],
            "template": self.template,
            "mining_report": self.mining_report.to_dict(),
            "selection_audit": self.selection_audit,
        }


MULTI_BUG_SIGNALS = {
    "hard",
    "multi_ground_truth",
    "multi_patch_bundle",
    "wide_beam_search",
    "failed_before_success",
    "reflection_depth",
    "patch_candidate_competition",
}

CROSS_FILE_SIGNALS = {
    "cross_file_patch",
    "cross_function_data_flow",
    "cross_function_trace",
}

ABLATION_STATIC_RULE_SIGNALS = {
    "without_static_rules",
}

ABLATION_RULE_PRECISION_FILTER_SIGNALS = {
    "without_rule_precision_filter",
}

ABLATION_BEAM_SEARCH_SIGNALS = {
    "without_beam_search",
}

ABLATION_REFLECTION_SIGNALS = {
    "without_reflection",
}

ABLATION_PATCH_PRIOR_SIGNALS = {
    "without_patch_prior",
}

ABLATION_DIVERSITY_RERANKING_SIGNALS = {
    "without_diversity_reranking",
}

ABLATION_CANDIDATE_DEDUPLICATION_SIGNALS = {
    "without_candidate_deduplication",
}

ABLATION_DATA_DEPENDENCY_SIGNALS = {
    "without_data_dependency",
}

ABLATION_MULTI_PATCH_SIGNALS = {
    "without_multi_patch_repair",
}

ABLATION_GRAPH_BUNDLE_SIGNALS = {
    "without_graph_bundle_search",
}

SEARCH_COMPETITION_SIGNALS = {
    "search_candidate_competition",
    "search_score_inversion",
    "search_diversity_reranking",
    "candidate_deduplication_pressure",
    "search_failure_pressure",
}

SLICE_GROUNDING_SIGNALS = {
    "weak_slice_grounding",
    "weak_slice_support",
    "weak_failed_test_reachability",
    "weak_call_chain_coverage",
}

LOCALIZATION_STABILITY_SIGNALS = {
    "fragile_top1_margin",
}

DATA_FLOW_EVIDENCE_SIGNALS = {
    "subscript_key_flow",
}

WIDE_BEAM_RULES = [
    "possible_index_overrun",
]

REFLECTION_DEPTH_RULES = [
    "possible_index_overrun",
    "missing_len_zero_guard",
]

SEARCH_CANDIDATE_COMPETITION_RULES = [
    "possible_index_overrun",
    "dict_missing_key_guard",
    "inplace_api_return_value",
    "stringified_numeric_value",
    "broad_exception_pass",
]

SEARCH_SCORE_INVERSION_RULES = [
    "possible_index_overrun",
    "broad_exception_pass",
    "inverted_empty_guard",
    "stringified_numeric_value",
    "inplace_api_return_value",
]

SEARCH_FAILURE_PRESSURE_RULES = [
    "broad_exception_pass",
    "mutable_default_arg",
    "dict_missing_key_guard",
    "stringified_numeric_value",
    "possible_index_overrun",
    "inplace_api_return_value",
]

MULTI_PATCH_RULES = [
    "missing_len_zero_guard",
    "possible_index_overrun",
    "dict_missing_key_guard",
    "inplace_api_return_value",
    "stringified_numeric_value",
    "mutable_default_arg",
]

CROSS_FILE_RULES = [
    "missing_len_zero_guard",
    "possible_index_overrun",
    "dict_missing_key_guard",
    "inverted_empty_guard",
]

STATIC_RULE_DEPENDENCY_RULES = [
    "always_true_len_check",
    "broad_exception_pass",
    "dict_missing_key_guard",
    "enumerate_start_zero_counter",
    "inplace_api_return_value",
    "inverted_empty_guard",
    "missing_len_zero_guard",
    "mutable_default_arg",
    "stringified_numeric_value",
]

RULE_PRECISION_FILTER_RULES = [
    "possible_index_overrun",
    "missing_len_zero_guard",
    "stringified_numeric_value",
    "inplace_api_return_value",
]

SLICE_GROUNDING_RULES = [
    "missing_len_zero_guard",
    "possible_index_overrun",
    "dict_missing_key_guard",
    "inverted_empty_guard",
    "inplace_api_return_value",
]

SLICE_GROUNDING_DIVERSITY_BASELINE_RULES = {
    "missing_len_zero_guard",
    "possible_index_overrun",
}

FRAGILE_LOCALIZATION_RULES = [
    "missing_len_zero_guard",
    "possible_index_overrun",
    "dict_missing_key_guard",
    "inplace_api_return_value",
    "stringified_numeric_value",
    "always_true_len_check",
]

SUBSCRIPT_KEY_FLOW_RULES = [
    "dict_missing_key_guard",
]

DATA_DEPENDENCY_RULES = [
    "missing_len_zero_guard",
    "possible_index_overrun",
    "dict_missing_key_guard",
    "inverted_empty_guard",
    "inplace_api_return_value",
]


def generate_hard_case_candidates(
    suite_payload: dict[str, Any],
    catalog_payload: dict[str, Any],
    *,
    suite_path: str = "",
    catalog_path: str = "",
    max_cases_per_suggestion: int = 1,
    max_total_cases: int | None = None,
) -> HardCaseGenerationReport:
    mining = hard_case_mining_report(suite_payload)
    rows: list[HardCaseGenerationRow] = []
    generated_cases: list[dict[str, Any]] = []
    seen_case_names: set[str] = set()
    total_limit = max_total_cases if max_total_cases is not None else 10**9

    for suggestion in mining.suggestions:
        if len(generated_cases) >= total_limit:
            rows.append(_skipped_row(suggestion, "total_case_limit_reached"))
            continue
        row, cases, ranking = _generate_for_suggestion(
            suggestion,
            catalog_payload,
            max_cases=min(max_cases_per_suggestion, total_limit - len(generated_cases)),
        )
        fresh_cases = []
        duplicate_names = []
        for case in cases:
            name = str(case.get("name", ""))
            if not name or name in seen_case_names:
                duplicate_names.append(name)
                continue
            seen_case_names.add(name)
            fresh_cases.append(_annotated_case(case, suggestion, row, ranking))
        if duplicate_names and not fresh_cases:
            row = _replace_row_status(row, "skipped", ["duplicate_template_case"])
        elif duplicate_names:
            row = _replace_row_status(
                row,
                row.status,
                [*row.reasons, "deduped_duplicate_template_case"],
                generated_count=len(fresh_cases),
                names=[str(case.get("name", "")) for case in fresh_cases],
            )
        if fresh_cases:
            generated_cases.extend(fresh_cases)
        rows.append(row)

    template = {"cases": generated_cases}
    validation_errors = _template_validation_errors(template)
    if validation_errors:
        rows.append(
            HardCaseGenerationRow(
                target_signal="template_validation",
                priority="high",
                strategy="validator",
                status="skipped",
                generated_count=0,
                reasons=[f"validator_error={item}" for item in validation_errors],
                include_rules=[],
            )
        )
        template = {"cases": []}

    return HardCaseGenerationReport(
        suite_path=suite_path,
        catalog_path=catalog_path,
        suggestion_count=mining.suggestion_count,
        generated_count=len(template["cases"]),
        skipped_count=sum(1 for row in rows if row.status != "generated"),
        rows=rows,
        template=template,
        mining_report=mining,
        selection_audit=_selection_audit(rows, template),
    )


def render_hard_case_generation_markdown(report: HardCaseGenerationReport) -> str:
    lines = [
        "# Hard-Case Candidate Generation",
        "",
        f"- Suite: `{report.suite_path or '<memory>'}`",
        f"- Catalog: `{report.catalog_path or '<memory>'}`",
        f"- Mining Suggestions: {report.suggestion_count}",
        f"- Generated Cases: {report.generated_count}",
        f"- Skipped Rows: {report.skipped_count}",
        f"- Selected Rules: {report.selection_audit.get('selected_rule_count', 0)}",
        f"- Selected Bug Types: {report.selection_audit.get('selected_bug_type_count', 0)}",
        f"- Average Candidate Score: {float(report.selection_audit.get('average_candidate_score', 0.0)):.3f}",
        f"- Average Diversity Bonus: {float(report.selection_audit.get('average_diversity_bonus', 0.0)):.3f}",
        "",
        "| Signal | Priority | Strategy | Selection Policy | Status | Cases | Candidates | Rules | Wrapper Depth | Reasons |",
        "| --- | --- | --- | --- | --- | ---: | --- | --- | ---: | --- |",
    ]
    for row in report.rows:
        lines.append(
            "| "
            f"{_markdown_cell(row.target_signal)} | "
            f"{_markdown_cell(row.priority)} | "
            f"{_markdown_cell(row.strategy)} | "
            f"{_markdown_cell(row.selection_policy)} | "
            f"{_markdown_cell(row.status)} | "
            f"{row.generated_count} | "
            f"{_markdown_cell(', '.join(row.selected_candidate_ids or []))} | "
            f"{_markdown_cell(', '.join(row.include_rules))} | "
            f"{row.wrapper_depth} | "
            f"{_markdown_cell(', '.join(row.reasons))} |"
        )
    return "\n".join(lines)


def _generate_for_suggestion(
    suggestion: HardCaseSuggestion,
    catalog_payload: dict[str, Any],
    *,
    max_cases: int,
) -> tuple[HardCaseGenerationRow, list[dict[str, Any]], dict[str, dict[str, Any]]]:
    signal = suggestion.target_signal
    if signal == "reflection_depth":
        rules = REFLECTION_DEPTH_RULES
        prioritized_catalog, ranking = _prioritized_catalog_payload(
            catalog_payload,
            suggestion=suggestion,
            include_rules=rules,
            selection_policy="reflection_depth_candidate_priority",
        )
        cases = _reflection_depth_probe_cases(
            prioritized_catalog,
            max_cases=max_cases,
        )
        selected_ids = _selected_candidate_ids_from_cases(cases)
        strategy = "reflection_depth_probe"
        selection_policy = "reflection_depth_candidate_priority"
        reasons = (
            [
                "generated_reflection_depth_probe",
                *_selection_reason_summary(selected_ids, ranking),
            ]
            if cases
            else ["no_reflection_depth_catalog_candidate"]
        )
        if not cases:
            cases = _reflection_depth_pressure_cases(max_cases=max_cases)
            selected_ids = ["synthetic_reflection_depth_pressure"] if cases else []
            ranking = _synthetic_reflection_depth_ranking(cases)
            strategy = "reflection_depth_probe_synthetic"
            selection_policy = "synthetic_reflection_depth_pressure"
            reasons = (
                [
                    "generated_synthetic_reflection_depth_probe",
                    *_selection_reason_summary(selected_ids, ranking),
                ]
                if cases
                else ["no_reflection_depth_case_budget"]
            )
        row = _generated_row(
            suggestion,
            strategy=strategy,
            generated_count=len(cases),
            reasons=reasons,
            include_rules=rules,
            names=[str(case.get("name", "")) for case in cases],
            selection_policy=selection_policy,
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    if signal in ABLATION_REFLECTION_SIGNALS:
        rules = REFLECTION_DEPTH_RULES
        prioritized_catalog, ranking = _prioritized_catalog_payload(
            catalog_payload,
            suggestion=suggestion,
            include_rules=rules,
            selection_policy="ablation_reflection_candidate_priority",
        )
        cases = _reflection_depth_probe_cases(
            prioritized_catalog,
            max_cases=max_cases,
        )
        selected_ids = _selected_candidate_ids_from_cases(cases)
        strategy = "ablation_reflection_depth_probe"
        selection_policy = "ablation_reflection_candidate_priority"
        reasons = (
            [
                "generated_reflection_depth_probe",
                *_selection_reason_summary(selected_ids, ranking),
            ]
            if cases
            else ["no_reflection_depth_catalog_candidate"]
        )
        if not cases:
            cases = _reflection_depth_pressure_cases(max_cases=max_cases)
            selected_ids = ["synthetic_reflection_depth_pressure"] if cases else []
            ranking = _synthetic_reflection_depth_ranking(cases)
            strategy = "ablation_reflection_depth_probe_synthetic"
            selection_policy = "synthetic_reflection_depth_pressure"
            reasons = (
                [
                    "generated_synthetic_reflection_depth_probe",
                    *_selection_reason_summary(selected_ids, ranking),
                ]
                if cases
                else ["no_reflection_depth_case_budget"]
            )
        row = _generated_row(
            suggestion,
            strategy=strategy,
            generated_count=len(cases),
            reasons=reasons,
            include_rules=rules,
            names=[str(case.get("name", "")) for case in cases],
            selection_policy=selection_policy,
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    if signal in ABLATION_PATCH_PRIOR_SIGNALS:
        rules = SEARCH_SCORE_INVERSION_RULES
        prioritized_catalog, ranking = _prioritized_catalog_payload(
            catalog_payload,
            suggestion=suggestion,
            include_rules=rules,
            selection_policy="ablation_patch_prior_candidate_priority",
        )
        cases = _score_inversion_probe_cases(
            prioritized_catalog,
            max_cases=max_cases,
        )
        selected_ids = _selected_candidate_ids_from_cases(cases)
        row = _generated_row(
            suggestion,
            strategy="ablation_patch_prior_score_inversion_probe",
            generated_count=len(cases),
            reasons=(
                [
                    "generated_prior_decoy_score_inversion_probe",
                    *_selection_reason_summary(selected_ids, ranking),
                ]
                if cases
                else ["no_score_inversion_catalog_candidate"]
            ),
            include_rules=rules,
            names=[str(case.get("name", "")) for case in cases],
            selection_policy="ablation_patch_prior_candidate_priority",
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    if signal in ABLATION_DIVERSITY_RERANKING_SIGNALS:
        rules = SEARCH_CANDIDATE_COMPETITION_RULES
        prioritized_catalog, ranking = _prioritized_catalog_payload(
            catalog_payload,
            suggestion=suggestion,
            include_rules=rules,
            selection_policy="ablation_diversity_reranking_candidate_priority",
        )
        cases = _diversity_reranking_probe_cases(
            prioritized_catalog,
            max_cases=max_cases,
        )
        selected_ids = _selected_candidate_ids_from_cases(cases)
        row = _generated_row(
            suggestion,
            strategy="ablation_diversity_reranking_probe",
            generated_count=len(cases),
            reasons=(
                [
                    "generated_diversity_reranking_probe",
                    *_selection_reason_summary(selected_ids, ranking),
                ]
                if cases
                else ["no_diversity_reranking_catalog_candidate"]
            ),
            include_rules=rules,
            names=[str(case.get("name", "")) for case in cases],
            selection_policy="ablation_diversity_reranking_candidate_priority",
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    if signal in ABLATION_CANDIDATE_DEDUPLICATION_SIGNALS:
        rules = WIDE_BEAM_RULES
        prioritized_catalog, ranking = _prioritized_catalog_payload(
            catalog_payload,
            suggestion=suggestion,
            include_rules=rules,
            selection_policy="ablation_candidate_deduplication_candidate_priority",
        )
        cases = _candidate_deduplication_probe_cases(
            prioritized_catalog,
            max_cases=max_cases,
        )
        selected_ids = _selected_candidate_ids_from_cases(cases)
        strategy = "ablation_candidate_deduplication_probe"
        selection_policy = "ablation_candidate_deduplication_candidate_priority"
        reasons = (
            [
                "generated_candidate_deduplication_probe",
                *_selection_reason_summary(selected_ids, ranking),
            ]
            if cases
            else ["no_candidate_deduplication_catalog_candidate"]
        )
        if not cases:
            cases = _candidate_deduplication_pressure_cases(max_cases=max_cases)
            selected_ids = (
                ["synthetic_candidate_deduplication_pressure"] if cases else []
            )
            ranking = _synthetic_candidate_deduplication_ranking(cases)
            strategy = "ablation_candidate_deduplication_probe_synthetic"
            selection_policy = "synthetic_candidate_deduplication_pressure"
            reasons = (
                [
                    "generated_synthetic_candidate_deduplication_probe",
                    *_selection_reason_summary(selected_ids, ranking),
                ]
                if cases
                else ["no_candidate_deduplication_case_budget"]
            )
        row = _generated_row(
            suggestion,
            strategy=strategy,
            generated_count=len(cases),
            reasons=reasons,
            include_rules=rules,
            names=[str(case.get("name", "")) for case in cases],
            selection_policy=selection_policy,
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    if signal in CROSS_FILE_SIGNALS:
        rules = CROSS_FILE_RULES
        wrapper_depth = 2 if signal == "cross_function_trace" else 1
        prioritized_catalog, ranking = _prioritized_catalog_payload(
            catalog_payload,
            suggestion=suggestion,
            include_rules=rules,
            selection_policy="risk_weighted_cross_file_candidate_priority",
        )
        report = compose_cross_file_benchmarks(
            prioritized_catalog,
            include_rules=rules,
            max_cases=max_cases,
            wrapper_depth=wrapper_depth,
        )
        cases = _report_cases(report.to_dict())
        selected_ids = _selected_candidate_ids_from_cases(cases)
        row = _generated_row(
            suggestion,
            strategy="cross_file_composition",
            generated_count=len(cases),
            reasons=[
                *_composition_reasons(report.to_dict()),
                *_selection_reason_summary(selected_ids, ranking),
            ],
            include_rules=rules,
            wrapper_depth=wrapper_depth,
            names=[str(case.get("name", "")) for case in cases],
            selection_policy="risk_weighted_cross_file_candidate_priority",
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    if signal in MULTI_BUG_SIGNALS:
        rules = WIDE_BEAM_RULES if signal == "wide_beam_search" else MULTI_PATCH_RULES
        wrapper_depth = 2 if signal == "hard" else 0
        prioritized_catalog, ranking = _prioritized_catalog_payload(
            catalog_payload,
            suggestion=suggestion,
            include_rules=rules,
            selection_policy="risk_weighted_multi_bug_candidate_priority",
        )
        report = compose_multi_bug_benchmarks(
            prioritized_catalog,
            include_rules=rules,
            max_cases=max_cases,
            bugs_per_case=2,
            wrapper_depth=wrapper_depth,
        )
        cases = _report_cases(report.to_dict())
        selected_ids = _selected_candidate_ids_from_cases(cases)
        row = _generated_row(
            suggestion,
            strategy="multi_bug_composition",
            generated_count=len(cases),
            reasons=[
                *_composition_reasons(report.to_dict()),
                *_selection_reason_summary(selected_ids, ranking),
            ],
            include_rules=rules,
            wrapper_depth=wrapper_depth,
            names=[str(case.get("name", "")) for case in cases],
            selection_policy="risk_weighted_multi_bug_candidate_priority",
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    if signal in ABLATION_STATIC_RULE_SIGNALS:
        rules = STATIC_RULE_DEPENDENCY_RULES
        prioritized_catalog, ranking = _prioritized_catalog_payload(
            catalog_payload,
            suggestion=suggestion,
            include_rules=rules,
            selection_policy="ablation_static_rule_candidate_priority",
        )
        cases = _direct_catalog_cases(
            prioritized_catalog,
            max_cases=max_cases,
            include_rules=rules,
        )
        selected_ids = _selected_candidate_ids_from_cases(cases)
        row = _generated_row(
            suggestion,
            strategy="ablation_static_rule_catalog_selection",
            generated_count=len(cases),
            reasons=(
                [
                    "selected_static_rule_catalog_cases",
                    *_selection_reason_summary(selected_ids, ranking),
                ]
                if cases
                else ["no_static_rule_catalog_candidate"]
            ),
            include_rules=rules,
            names=[str(case.get("name", "")) for case in cases],
            selection_policy="ablation_static_rule_candidate_priority",
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    if signal in ABLATION_RULE_PRECISION_FILTER_SIGNALS:
        cases = _rule_precision_filter_pressure_cases(max_cases=max_cases)
        selected_ids = ["synthetic_rule_precision_filter_pressure"] if cases else []
        ranking = (
            {
                "synthetic_rule_precision_filter_pressure": {
                    "score": 28.0,
                    "rank": 1,
                    "reasons": [
                        "synthetic_rule_precision_filter_pressure",
                        "expected_index_overrun_anchor",
                        "filtered_len_denominator_decoys",
                        "filtered_stringified_mapping_decoy",
                        "filtered_self_inplace_decoy",
                    ],
                }
            }
            if cases
            else {}
        )
        row = _generated_row(
            suggestion,
            strategy="ablation_rule_precision_filter_pressure_synthetic",
            generated_count=len(cases),
            reasons=(
                [
                    "generated_rule_precision_filter_pressure_case",
                    *_selection_reason_summary(selected_ids, ranking),
                ]
                if cases
                else ["no_rule_precision_filter_pressure_case_budget"]
            ),
            include_rules=RULE_PRECISION_FILTER_RULES,
            names=[str(case.get("name", "")) for case in cases],
            selection_policy="synthetic_rule_precision_filter_pressure",
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    if signal in ABLATION_BEAM_SEARCH_SIGNALS:
        rules = WIDE_BEAM_RULES
        prioritized_catalog, ranking = _prioritized_catalog_payload(
            catalog_payload,
            suggestion=suggestion,
            include_rules=rules,
            selection_policy="ablation_beam_search_candidate_priority",
        )
        report = compose_multi_bug_benchmarks(
            prioritized_catalog,
            include_rules=rules,
            max_cases=max_cases,
            bugs_per_case=2,
            wrapper_depth=0,
        )
        cases = _report_cases(report.to_dict())
        if not cases:
            cases = _direct_catalog_cases(
                prioritized_catalog,
                max_cases=max_cases,
                include_rules=rules,
            )
        selected_ids = _selected_candidate_ids_from_cases(cases)
        strategy = (
            "ablation_beam_search_multi_bug_composition"
            if _report_cases(report.to_dict())
            else "ablation_beam_search_catalog_selection"
        )
        row = _generated_row(
            suggestion,
            strategy=strategy,
            generated_count=len(cases),
            reasons=(
                [
                    *_composition_reasons(report.to_dict()),
                    *_selection_reason_summary(selected_ids, ranking),
                ]
                if report.composed_count > 0
                else (
                    [
                        "fallback_single_candidate_beam_pressure_case",
                        *_selection_reason_summary(selected_ids, ranking),
                    ]
                    if cases
                    else ["no_beam_search_catalog_candidate"]
                )
            ),
            include_rules=rules,
            names=[str(case.get("name", "")) for case in cases],
            selection_policy="ablation_beam_search_candidate_priority",
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    if signal in ABLATION_DATA_DEPENDENCY_SIGNALS:
        rules = DATA_DEPENDENCY_RULES
        prioritized_catalog, ranking = _prioritized_catalog_payload(
            catalog_payload,
            suggestion=suggestion,
            include_rules=rules,
            selection_policy="ablation_data_dependency_candidate_priority",
        )
        report = compose_cross_file_benchmarks(
            prioritized_catalog,
            include_rules=rules,
            max_cases=max_cases,
            wrapper_depth=1,
        )
        cases = _report_cases(report.to_dict())
        if not cases:
            cases = _direct_catalog_cases(
                prioritized_catalog,
                max_cases=max_cases,
                include_rules=rules,
            )
        selected_ids = _selected_candidate_ids_from_cases(cases)
        strategy = (
            "ablation_data_dependency_cross_file_composition"
            if _report_cases(report.to_dict())
            else "ablation_data_dependency_catalog_selection"
        )
        row = _generated_row(
            suggestion,
            strategy=strategy,
            generated_count=len(cases),
            reasons=(
                [
                    *_composition_reasons(report.to_dict()),
                    *_selection_reason_summary(selected_ids, ranking),
                ]
                if report.composed_count > 0
                else (
                    [
                        "fallback_single_candidate_data_dependency_case",
                        *_selection_reason_summary(selected_ids, ranking),
                    ]
                    if cases
                    else ["no_data_dependency_catalog_candidate"]
                )
            ),
            include_rules=rules,
            wrapper_depth=1 if _report_cases(report.to_dict()) else 0,
            names=[str(case.get("name", "")) for case in cases],
            selection_policy="ablation_data_dependency_candidate_priority",
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    if signal in ABLATION_MULTI_PATCH_SIGNALS:
        rules = MULTI_PATCH_RULES
        prioritized_catalog, ranking = _prioritized_catalog_payload(
            catalog_payload,
            suggestion=suggestion,
            include_rules=rules,
            selection_policy="ablation_multi_patch_candidate_priority",
        )
        report = compose_multi_bug_benchmarks(
            prioritized_catalog,
            include_rules=rules,
            max_cases=max_cases,
            bugs_per_case=2,
            wrapper_depth=0,
        )
        cases = _report_cases(report.to_dict())
        if not cases:
            cases = _direct_catalog_cases(
                prioritized_catalog,
                max_cases=max_cases,
                include_rules=rules,
            )
        selected_ids = _selected_candidate_ids_from_cases(cases)
        strategy = (
            "ablation_multi_patch_composition"
            if _report_cases(report.to_dict())
            else "ablation_multi_patch_catalog_selection"
        )
        row = _generated_row(
            suggestion,
            strategy=strategy,
            generated_count=len(cases),
            reasons=(
                [
                    *_composition_reasons(report.to_dict()),
                    *_selection_reason_summary(selected_ids, ranking),
                ]
                if report.composed_count > 0
                else (
                    [
                        "fallback_single_candidate_multi_patch_case",
                        *_selection_reason_summary(selected_ids, ranking),
                    ]
                    if cases
                    else ["no_multi_patch_catalog_candidate"]
                )
            ),
            include_rules=rules,
            names=[str(case.get("name", "")) for case in cases],
            selection_policy="ablation_multi_patch_candidate_priority",
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    if signal in ABLATION_GRAPH_BUNDLE_SIGNALS:
        cases = _graph_bundle_pressure_cases(max_cases=max_cases)
        selected_ids = ["synthetic_graph_bundle_pressure"] if cases else []
        ranking = (
            {
                "synthetic_graph_bundle_pressure": {
                    "score": 30.0,
                    "rank": 1,
                    "reasons": [
                        "synthetic_graph_bundle_pressure",
                        "direct_call_connected_target_pair",
                        "decoy_bundle_budget_pressure",
                    ],
                }
            }
            if cases
            else {}
        )
        row = _generated_row(
            suggestion,
            strategy="ablation_graph_bundle_pressure_synthetic",
            generated_count=len(cases),
            reasons=(
                [
                    "generated_graph_bundle_pressure_case",
                    *_selection_reason_summary(selected_ids, ranking),
                ]
                if cases
                else ["no_graph_bundle_pressure_case_budget"]
            ),
            include_rules=["dict_missing_key_guard"],
            names=[str(case.get("name", "")) for case in cases],
            selection_policy="synthetic_graph_bundle_pressure",
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    if signal in SEARCH_COMPETITION_SIGNALS:
        rules = _search_competition_rules(signal)
        prioritized_catalog, ranking = _prioritized_catalog_payload(
            catalog_payload,
            suggestion=suggestion,
            include_rules=rules,
            selection_policy="search_competition_candidate_priority",
        )
        if signal == "search_score_inversion":
            cases = _score_inversion_probe_cases(
                prioritized_catalog,
                max_cases=max_cases,
            )
            selected_ids = _selected_candidate_ids_from_cases(cases)
            if cases:
                row = _generated_row(
                    suggestion,
                    strategy="search_competition_score_inversion_probe",
                    generated_count=len(cases),
                    reasons=[
                        "generated_prior_decoy_score_inversion_probe",
                        *_selection_reason_summary(selected_ids, ranking),
                    ],
                    include_rules=rules,
                    names=[str(case.get("name", "")) for case in cases],
                    selection_policy="search_competition_candidate_priority",
                    selected_candidate_ids=selected_ids,
                    ranking=ranking,
                )
                return row, cases, ranking
        if signal == "search_diversity_reranking":
            cases = _diversity_reranking_probe_cases(
                prioritized_catalog,
                max_cases=max_cases,
            )
            selected_ids = _selected_candidate_ids_from_cases(cases)
            if cases:
                row = _generated_row(
                    suggestion,
                    strategy="search_competition_diversity_reranking_probe",
                    generated_count=len(cases),
                    reasons=[
                        "generated_diversity_reranking_probe",
                        *_selection_reason_summary(selected_ids, ranking),
                    ],
                    include_rules=rules,
                    names=[str(case.get("name", "")) for case in cases],
                    selection_policy="search_competition_candidate_priority",
                    selected_candidate_ids=selected_ids,
                    ranking=ranking,
                )
                return row, cases, ranking
        if signal == "candidate_deduplication_pressure":
            cases = _candidate_deduplication_probe_cases(
                prioritized_catalog,
                max_cases=max_cases,
            )
            selected_ids = _selected_candidate_ids_from_cases(cases)
            if cases:
                row = _generated_row(
                    suggestion,
                    strategy="search_budget_candidate_deduplication_probe",
                    generated_count=len(cases),
                    reasons=[
                        "generated_candidate_deduplication_probe",
                        *_selection_reason_summary(selected_ids, ranking),
                    ],
                    include_rules=rules,
                    names=[str(case.get("name", "")) for case in cases],
                    selection_policy="search_competition_candidate_priority",
                    selected_candidate_ids=selected_ids,
                    ranking=ranking,
                )
                return row, cases, ranking
            cases = _candidate_deduplication_pressure_cases(max_cases=max_cases)
            selected_ids = (
                ["synthetic_candidate_deduplication_pressure"] if cases else []
            )
            ranking = _synthetic_candidate_deduplication_ranking(cases)
            row = _generated_row(
                suggestion,
                strategy="search_budget_candidate_deduplication_probe_synthetic",
                generated_count=len(cases),
                reasons=(
                    [
                        "generated_synthetic_candidate_deduplication_probe",
                        *_selection_reason_summary(selected_ids, ranking),
                    ]
                    if cases
                    else ["no_candidate_deduplication_case_budget"]
                ),
                include_rules=rules,
                names=[str(case.get("name", "")) for case in cases],
                selection_policy="synthetic_candidate_deduplication_pressure",
                selected_candidate_ids=selected_ids,
                ranking=ranking,
            )
            return row, cases, ranking
        report = compose_multi_bug_benchmarks(
            prioritized_catalog,
            include_rules=rules,
            max_cases=max_cases,
            bugs_per_case=2,
            wrapper_depth=0,
        )
        cases = _report_cases(report.to_dict())
        if not cases:
            cases = _direct_catalog_cases(
                prioritized_catalog,
                max_cases=max_cases,
                include_rules=rules,
            )
        selected_ids = _selected_candidate_ids_from_cases(cases)
        strategy = (
            "search_competition_multi_bug_composition"
            if _report_cases(report.to_dict())
            else "search_competition_catalog_selection"
        )
        row = _generated_row(
            suggestion,
            strategy=strategy,
            generated_count=len(cases),
            reasons=(
                [
                    *_composition_reasons(report.to_dict()),
                    *_selection_reason_summary(selected_ids, ranking),
                ]
                if report.composed_count > 0
                else (
                    [
                        "fallback_single_candidate_search_pressure_case",
                        *_selection_reason_summary(selected_ids, ranking),
                    ]
                    if cases
                    else ["no_search_competition_catalog_candidate"]
                )
            ),
            include_rules=rules,
            names=[str(case.get("name", "")) for case in cases],
            selection_policy="search_competition_candidate_priority",
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    if signal in SLICE_GROUNDING_SIGNALS:
        rules = SLICE_GROUNDING_RULES
        wrapper_depth = 2 if signal in {
            "weak_call_chain_coverage",
            "weak_failed_test_reachability",
            "weak_slice_grounding",
        } else 1
        prioritized_catalog, ranking = _prioritized_catalog_payload(
            catalog_payload,
            suggestion=suggestion,
            include_rules=rules,
            selection_policy="slice_grounding_candidate_priority",
        )
        prioritized_catalog = _slice_grounding_catalog_payload(prioritized_catalog)
        report = compose_cross_file_benchmarks(
            prioritized_catalog,
            include_rules=rules,
            max_cases=max_cases,
            wrapper_depth=wrapper_depth,
        )
        cases = _report_cases(report.to_dict())
        if not cases:
            cases = _direct_catalog_cases(
                prioritized_catalog,
                max_cases=max_cases,
                include_rules=rules,
            )
        selected_ids = _selected_candidate_ids_from_cases(cases)
        strategy = (
            "slice_grounding_cross_file_composition"
            if _report_cases(report.to_dict())
            else "slice_grounding_catalog_selection"
        )
        row = _generated_row(
            suggestion,
            strategy=strategy,
            generated_count=len(cases),
            reasons=(
                [
                    *_composition_reasons(report.to_dict()),
                    *_selection_reason_summary(selected_ids, ranking),
                ]
                if report.composed_count > 0
                else (
                    [
                        "fallback_single_candidate_slice_grounding_case",
                        *_selection_reason_summary(selected_ids, ranking),
                    ]
                    if cases
                    else ["no_slice_grounding_catalog_candidate"]
                )
            ),
            include_rules=rules,
            wrapper_depth=wrapper_depth,
            names=[str(case.get("name", "")) for case in cases],
            selection_policy="slice_grounding_candidate_priority",
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    if signal in LOCALIZATION_STABILITY_SIGNALS:
        rules = FRAGILE_LOCALIZATION_RULES
        prioritized_catalog, ranking = _prioritized_catalog_payload(
            catalog_payload,
            suggestion=suggestion,
            include_rules=rules,
            selection_policy="fragile_localization_candidate_priority",
        )
        cases = _direct_catalog_cases(
            prioritized_catalog,
            max_cases=max_cases,
            include_rules=rules,
        )
        selected_ids = _selected_candidate_ids_from_cases(cases)
        row = _generated_row(
            suggestion,
            strategy="fragile_localization_catalog_selection",
            generated_count=len(cases),
            reasons=(
                [
                    "selected_fragile_localization_catalog_cases",
                    *_selection_reason_summary(selected_ids, ranking),
                ]
                if cases
                else ["no_fragile_localization_catalog_candidate"]
            ),
            include_rules=rules,
            names=[str(case.get("name", "")) for case in cases],
            selection_policy="fragile_localization_candidate_priority",
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    if signal in DATA_FLOW_EVIDENCE_SIGNALS:
        rules = SUBSCRIPT_KEY_FLOW_RULES
        prioritized_catalog, ranking = _prioritized_catalog_payload(
            catalog_payload,
            suggestion=suggestion,
            include_rules=rules,
            selection_policy="subscript_key_flow_candidate_priority",
        )
        cases = _direct_catalog_cases(
            prioritized_catalog,
            max_cases=max_cases,
            include_rules=rules,
        )
        selected_ids = _selected_candidate_ids_from_cases(cases)
        row = _generated_row(
            suggestion,
            strategy="subscript_key_flow_catalog_selection",
            generated_count=len(cases),
            reasons=(
                [
                    "selected_subscript_key_flow_catalog_cases",
                    *_selection_reason_summary(selected_ids, ranking),
                ]
                if cases
                else ["no_subscript_key_flow_catalog_candidate"]
            ),
            include_rules=rules,
            names=[str(case.get("name", "")) for case in cases],
            selection_policy="subscript_key_flow_candidate_priority",
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    if suggestion.source == "generalization_source_group_balance":
        prioritized_catalog, ranking = _prioritized_catalog_payload(
            catalog_payload,
            suggestion=suggestion,
            include_rules=[],
            selection_policy="source_group_balance_candidate_priority",
        )
        cases = _direct_catalog_cases(
            prioritized_catalog,
            upstream=suggestion.target_signal,
            max_cases=max_cases,
        )
        selected_ids = _selected_candidate_ids_from_cases(cases)
        row = _generated_row(
            suggestion,
            strategy="source_group_catalog_selection",
            generated_count=len(cases),
            reasons=(
                [
                    "selected_catalog_cases",
                    *_selection_reason_summary(selected_ids, ranking),
                ]
                if cases
                else ["no_matching_source_group"]
            ),
            include_rules=[],
            names=[str(case.get("name", "")) for case in cases],
            selection_policy="source_group_balance_candidate_priority",
            selected_candidate_ids=selected_ids,
            ranking=ranking,
        )
        return row, cases, ranking

    return _skipped_row(suggestion, "unsupported_signal"), [], {}


def _prioritized_catalog_payload(
    catalog_payload: dict[str, Any],
    *,
    suggestion: HardCaseSuggestion,
    include_rules: list[str],
    selection_policy: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    scored = []
    for index, candidate in enumerate(_extract_candidates(catalog_payload)):
        score, reasons = _candidate_priority(
            candidate,
            suggestion=suggestion,
            include_rules=include_rules,
            selection_policy=selection_policy,
        )
        candidate_id = _candidate_id(candidate)
        scored.append((score, candidate_id, index, candidate, reasons))
    scored = _diversity_rerank(scored)
    ranking = {
        candidate_id: {
            "score": round(score, 4),
            "rank": rank,
            "reasons": reasons,
        }
        for rank, (score, candidate_id, _index, _candidate, reasons)
        in enumerate(scored, start=1)
    }
    return {
        "candidates": [
            _deepcopy_json(candidate)
            for _score, _candidate_id, _index, candidate, _reasons in scored
        ],
        "selection_policy": selection_policy,
    }, ranking


def _diversity_rerank(
    scored: list[tuple[float, str, int, dict[str, Any], list[str]]],
) -> list[tuple[float, str, int, dict[str, Any], list[str]]]:
    remaining = [
        {
            "base_score": score,
            "candidate_id": candidate_id,
            "index": index,
            "candidate": candidate,
            "reasons": list(reasons),
        }
        for score, candidate_id, index, candidate, reasons in scored
    ]
    selected: list[dict[str, Any]] = []
    seen: dict[str, set[str]] = {
        "rules": set(),
        "bug_types": set(),
        "upstreams": set(),
        "functions": set(),
        "sources": set(),
    }
    while remaining:
        scored_remaining = []
        for item in remaining:
            if selected:
                bonus, bonus_reasons = _diversity_bonus(item["candidate"], seen)
            else:
                bonus, bonus_reasons = 0.0, ["first_candidate_by_priority_score"]
            final_score = float(item["base_score"]) + bonus
            scored_remaining.append((final_score, bonus, bonus_reasons, item))
        final_score, bonus, bonus_reasons, best = sorted(
            scored_remaining,
            key=lambda item: (-item[0], str(item[3]["candidate_id"]), int(item[3]["index"])),
        )[0]
        best["final_score"] = final_score
        best["diversity_bonus"] = bonus
        best["reasons"] = [
            *best["reasons"],
            f"base_score={float(best['base_score']):.2f}",
            f"diversity_bonus={bonus:.2f}",
            *bonus_reasons,
        ]
        selected.append(best)
        _update_seen_features(seen, best["candidate"])
        remaining = [item for item in remaining if item is not best]
    return [
        (
            float(item["final_score"]),
            str(item["candidate_id"]),
            int(item["index"]),
            item["candidate"],
            item["reasons"][:12],
        )
        for item in selected
    ]


def _diversity_bonus(
    candidate: dict[str, Any],
    seen: dict[str, set[str]],
) -> tuple[float, list[str]]:
    features = _candidate_features(candidate)
    bonus = 0.0
    reasons: list[str] = []
    for rule in sorted(features["rules"] - seen["rules"]):
        bonus += 3.0
        reasons.append(f"new_rule={rule}")
    for bug_type in sorted(features["bug_types"] - seen["bug_types"]):
        bonus += 2.0
        reasons.append(f"new_bug_type={bug_type}")
    for upstream in sorted(features["upstreams"] - seen["upstreams"]):
        bonus += 2.0
        reasons.append(f"new_upstream={upstream}")
    for function in sorted(features["functions"] - seen["functions"]):
        bonus += 1.5
        reasons.append(f"new_function={function}")
    for source in sorted(features["sources"] - seen["sources"]):
        bonus += 1.0
        reasons.append(f"new_source={source}")
    if not reasons:
        reasons.append("no_new_diversity_feature")
    return min(bonus, 8.0), reasons[:5]


def _update_seen_features(
    seen: dict[str, set[str]],
    candidate: dict[str, Any],
) -> None:
    features = _candidate_features(candidate)
    for key, values in features.items():
        seen[key].update(values)


def _candidate_features(candidate: dict[str, Any]) -> dict[str, set[str]]:
    case = _candidate_template_case(candidate)
    metadata = _benchmark_metadata(case)
    bug_type = str(candidate.get("bug_type") or metadata.get("bug_type") or "")
    upstream = str(metadata.get("upstream", ""))
    function = _simple_buggy_function(case)
    return {
        "rules": set(_candidate_rule_ids(candidate)),
        "bug_types": {bug_type} if bug_type else set(),
        "upstreams": {upstream} if upstream else set(),
        "functions": {function} if function else set(),
        "sources": set(_case_source_targets(case)),
    }


def _candidate_priority(
    candidate: dict[str, Any],
    *,
    suggestion: HardCaseSuggestion,
    include_rules: list[str],
    selection_policy: str,
) -> tuple[float, list[str]]:
    case = _candidate_template_case(candidate)
    metadata = _benchmark_metadata(case)
    rules = set(_candidate_rule_ids(candidate))
    include_rule_set = set(include_rules)
    signal = suggestion.target_signal
    score = 0.0
    reasons: list[str] = []

    overlap = sorted(rules & include_rule_set)
    if include_rule_set and overlap:
        score += 10.0 + len(overlap)
        reasons.append(f"rule_overlap={','.join(overlap)}")
    elif include_rule_set:
        score -= 5.0
        reasons.append("rule_not_in_target_set")

    rule_weights = _signal_rule_weights(signal)
    for rule in sorted(rules):
        weight = rule_weights.get(rule, 0.0)
        if weight:
            score += weight
            reasons.append(f"{signal}_rule_weight={rule}:{weight:.1f}")

    bug_type = str(candidate.get("bug_type") or metadata.get("bug_type") or "")
    bug_type_weight = _bug_type_weight(signal, bug_type)
    if bug_type_weight:
        score += bug_type_weight
        reasons.append(f"bug_type={bug_type}:{bug_type_weight:.1f}")

    focus_bonus = _focus_bonus(candidate, metadata, signal)
    if focus_bonus:
        score += focus_bonus
        reasons.append(f"focus_bonus={focus_bonus:.1f}")

    failure_types = _string_set(candidate.get("failure_types"))
    if failure_types:
        bonus = min(3.0, len(failure_types) * 0.5)
        score += bonus
        reasons.append(f"failure_type_diversity={len(failure_types)}")

    function_name = _simple_buggy_function(case)
    if signal in {
        "wide_beam_search",
        "patch_candidate_competition",
        "hard",
        "without_beam_search",
        "without_data_dependency",
        "without_multi_patch_repair",
        "search_candidate_competition",
        "search_score_inversion",
        "search_failure_pressure",
        "weak_slice_grounding",
        "weak_slice_support",
        "weak_failed_test_reachability",
        "weak_call_chain_coverage",
        "cross_function_data_flow",
        "fragile_top1_margin",
        "subscript_key_flow",
    }:
        if any(
            token in function_name
            for token in ("sort", "mode", "search", "mean", "median", "normalize")
        ):
            score += 2.0
            reasons.append(f"candidate_competition_function={function_name}")

    source_targets = _case_source_targets(case)
    if source_targets:
        score += 0.5
        reasons.append("has_raw_source")
    if selection_policy.startswith("risk_weighted_cross_file"):
        if _single_simple_python_source(case):
            score += 3.0
            reasons.append("cross_file_wrappable_source")
        if _test_imports_simple_function(case, function_name):
            score += 2.0
            reasons.append("rewritable_direct_test_import")

    upstream = str(metadata.get("upstream", ""))
    if suggestion.source == "generalization_source_group_balance":
        if upstream == suggestion.target_signal:
            score += 50.0
            reasons.append(f"target_upstream={upstream}")
        elif upstream:
            score -= 5.0
            reasons.append(f"other_upstream={upstream}")

    if _eligible_for_provenance_bonus(
        requires_rule_match=bool(include_rule_set),
        overlap=overlap,
        reasons=reasons,
        signal=signal,
    ):
        provenance_score, provenance_reasons = _candidate_provenance_bonus(case)
        if provenance_score > 0.0:
            score += provenance_score
            reasons.extend(provenance_reasons)

    if not reasons:
        reasons.append("baseline_catalog_candidate")
    return score, reasons[:10]


def _eligible_for_provenance_bonus(
    *,
    requires_rule_match: bool,
    overlap: list[str],
    reasons: list[str],
    signal: str,
) -> bool:
    if overlap:
        return True
    if requires_rule_match:
        return False
    strong_prefixes = (
        f"{signal}_rule_weight=",
        "bug_type=",
        "focus_bonus=",
        "candidate_competition_function=",
        "cross_file_wrappable_source",
        "rewritable_direct_test_import",
        "target_upstream=",
    )
    return any(reason.startswith(strong_prefixes) for reason in reasons)


def _candidate_provenance_bonus(
    candidate_case: dict[str, Any],
) -> tuple[float, list[str]]:
    provenance = benchmark_provenance_summary([candidate_case])
    case_coverage = float(provenance.get("case_provenance_coverage", 0.0))
    source_ref_count = int(provenance.get("source_ref_count", 0))
    source_sha_count = int(provenance.get("source_sha256_present_count", 0))
    sha_coverage = (
        float(provenance.get("source_sha256_coverage", 0.0))
        if source_sha_count
        else 0.0
    )
    stable_ref_coverage = (
        float(provenance.get("stable_ref_coverage", 0.0))
        if source_ref_count
        else 0.0
    )
    license_coverage = float(provenance.get("license_coverage", 0.0))
    leakage_risk = float(provenance.get("leakage_risk_score", 0.0))
    if not any(
        value > 0.0
        for value in (
            case_coverage,
            sha_coverage,
            stable_ref_coverage,
            license_coverage,
        )
    ):
        return 0.0, []
    quality = (
        0.25 * case_coverage
        + 0.25 * sha_coverage
        + 0.20 * stable_ref_coverage
        + 0.20 * license_coverage
        + 0.10 * (1.0 - leakage_risk)
    )
    score = round(2.0 * max(0.0, min(1.0, quality)), 4)
    reasons = [
        f"provenance_bonus={score:.4f}",
        (
            "provenance_metrics="
            f"case_provenance={case_coverage:.4f};"
            f"source_sha256={sha_coverage:.4f};"
            f"stable_ref={stable_ref_coverage:.4f};"
            f"license={license_coverage:.4f};"
            f"leakage_risk={leakage_risk:.4f}"
        ),
    ]
    return score, reasons


def _signal_rule_weights(signal: str) -> dict[str, float]:
    if signal in {
        "wide_beam_search",
        "without_beam_search",
    }:
        return {
            "possible_index_overrun": 14.0,
            "inplace_api_return_value": 4.0,
            "stringified_numeric_value": 3.0,
        }
    if signal == "search_candidate_competition":
        return {
            "possible_index_overrun": 14.0,
            "inplace_api_return_value": 8.0,
            "stringified_numeric_value": 6.0,
            "dict_missing_key_guard": 5.0,
            "broad_exception_pass": 2.0,
        }
    if signal in {"search_score_inversion", "without_patch_prior"}:
        return {
            "possible_index_overrun": 14.0,
            "broad_exception_pass": 9.0,
            "inverted_empty_guard": 8.0,
            "stringified_numeric_value": 6.0,
            "inplace_api_return_value": 3.0,
        }
    if signal in {"search_diversity_reranking", "without_diversity_reranking"}:
        return {
            "possible_index_overrun": 14.0,
            "inplace_api_return_value": 8.0,
            "stringified_numeric_value": 7.0,
            "broad_exception_pass": 4.0,
        }
    if signal in {
        "candidate_deduplication_pressure",
        "without_candidate_deduplication",
    }:
        return {
            "possible_index_overrun": 14.0,
            "missing_len_zero_guard": 4.0,
            "dict_missing_key_guard": 3.0,
        }
    if signal == "search_failure_pressure":
        return {
            "broad_exception_pass": 9.0,
            "mutable_default_arg": 8.0,
            "stringified_numeric_value": 7.0,
            "dict_missing_key_guard": 6.0,
            "possible_index_overrun": 5.0,
            "inplace_api_return_value": 4.0,
        }
    if signal == "without_static_rules":
        return {
            "possible_index_overrun": 9.0,
            "missing_len_zero_guard": 8.0,
            "dict_missing_key_guard": 7.0,
            "inplace_api_return_value": 7.0,
            "stringified_numeric_value": 6.0,
            "broad_exception_pass": 5.0,
            "mutable_default_arg": 5.0,
            "always_true_len_check": 4.0,
            "inverted_empty_guard": 4.0,
            "enumerate_start_zero_counter": 3.0,
        }
    if signal == "without_data_dependency":
        return {
            "missing_len_zero_guard": 10.0,
            "possible_index_overrun": 9.0,
            "dict_missing_key_guard": 8.0,
            "inverted_empty_guard": 6.0,
            "inplace_api_return_value": 5.0,
        }
    if signal in {"reflection_depth", "without_reflection"}:
        return {
            "possible_index_overrun": 9.0,
            "inplace_api_return_value": 8.0,
            "stringified_numeric_value": 7.0,
            "mutable_default_arg": 6.0,
            "missing_len_zero_guard": 2.0,
        }
    if signal in {
        "hard",
        "multi_ground_truth",
        "multi_patch_bundle",
        "failed_before_success",
        "without_multi_patch_repair",
    }:
        return {
            "possible_index_overrun": 9.0,
            "missing_len_zero_guard": 8.0,
            "dict_missing_key_guard": 4.0,
            "inplace_api_return_value": 4.0,
            "stringified_numeric_value": 3.0,
            "mutable_default_arg": 2.0,
        }
    if signal == "patch_candidate_competition":
        return {
            "possible_index_overrun": 8.0,
            "inplace_api_return_value": 5.0,
            "stringified_numeric_value": 4.0,
        }
    if signal in {"cross_file_patch", "cross_function_trace"}:
        return {
            "missing_len_zero_guard": 8.0,
            "possible_index_overrun": 7.0,
            "dict_missing_key_guard": 6.0,
            "inverted_empty_guard": 6.0,
        }
    if signal == "cross_function_data_flow":
        return {
            "missing_len_zero_guard": 10.0,
            "possible_index_overrun": 9.0,
            "dict_missing_key_guard": 7.0,
            "inverted_empty_guard": 6.0,
            "inplace_api_return_value": 5.0,
        }
    if signal in SLICE_GROUNDING_SIGNALS:
        return {
            "missing_len_zero_guard": 10.0,
            "possible_index_overrun": 9.0,
            "inverted_empty_guard": 7.0,
            "dict_missing_key_guard": 6.0,
            "inplace_api_return_value": 5.0,
            "stringified_numeric_value": 4.0,
        }
    if signal == "fragile_top1_margin":
        return {
            "possible_index_overrun": 10.0,
            "missing_len_zero_guard": 9.0,
            "inplace_api_return_value": 7.0,
            "dict_missing_key_guard": 6.0,
            "stringified_numeric_value": 6.0,
            "always_true_len_check": 5.0,
        }
    if signal == "subscript_key_flow":
        return {
            "dict_missing_key_guard": 16.0,
            "stringified_numeric_value": 2.0,
        }
    return {}


def _bug_type_weight(signal: str, bug_type: str) -> float:
    if not bug_type:
        return 0.0
    if signal in {"wide_beam_search", "without_beam_search"} and bug_type == (
        "boundary error"
    ):
        return 5.0
    if signal == "search_candidate_competition":
        return {
            "boundary error": 5.0,
            "api misuse": 2.5,
            "type error": 2.0,
            "key error": 2.0,
            "exception handling error": 1.0,
        }.get(bug_type, 0.5)
    if signal in {"search_score_inversion", "without_patch_prior"}:
        return {
            "boundary error": 4.0,
            "exception handling error": 3.0,
            "condition error": 2.5,
            "type error": 2.0,
            "api misuse": 1.5,
        }.get(bug_type, 0.5)
    if signal in {"search_diversity_reranking", "without_diversity_reranking"}:
        return {
            "boundary error": 4.0,
            "api misuse": 3.0,
            "type error": 2.5,
            "exception handling error": 2.0,
        }.get(bug_type, 0.5)
    if signal in {
        "candidate_deduplication_pressure",
        "without_candidate_deduplication",
    }:
        return {
            "boundary error": 4.0,
            "key error": 2.0,
            "condition error": 1.5,
        }.get(bug_type, 0.5)
    if signal == "search_failure_pressure":
        return {
            "exception handling error": 3.5,
            "state leakage": 3.0,
            "key error": 2.5,
            "type error": 2.5,
            "boundary error": 2.0,
            "api misuse": 1.5,
        }.get(bug_type, 0.5)
    if signal == "without_static_rules":
        return {
            "boundary error": 3.0,
            "zero division error": 3.0,
            "key error": 2.5,
            "api misuse": 2.5,
            "type error": 2.0,
            "state leakage": 2.0,
            "exception handling error": 1.5,
            "condition error": 1.5,
        }.get(bug_type, 0.5)
    if signal == "without_data_dependency":
        return {
            "zero division error": 4.0,
            "boundary error": 4.0,
            "key error": 3.5,
            "api misuse": 2.5,
            "condition error": 2.0,
            "type error": 1.5,
        }.get(bug_type, 0.5)
    if signal in {
        "multi_patch_bundle",
        "failed_before_success",
        "reflection_depth",
        "without_multi_patch_repair",
    }:
        return {
            "boundary error": 4.0,
            "zero division error": 4.0,
            "key error": 2.5,
            "api misuse": 2.0,
            "type error": 1.5,
            "state leakage": 1.0,
        }.get(bug_type, 0.5)
    if signal == "hard":
        return {
            "boundary error": 4.0,
            "zero division error": 4.0,
            "api misuse": 3.0,
            "key error": 3.0,
            "state leakage": 2.0,
            "type error": 2.0,
        }.get(bug_type, 0.5)
    if signal in {"cross_file_patch", "cross_function_trace"}:
        return {
            "zero division error": 3.0,
            "boundary error": 3.0,
            "key error": 2.5,
            "condition error": 2.0,
        }.get(bug_type, 0.5)
    if signal == "cross_function_data_flow":
        return {
            "zero division error": 4.0,
            "boundary error": 4.0,
            "key error": 3.0,
            "api misuse": 2.5,
            "condition error": 2.0,
            "type error": 1.5,
        }.get(bug_type, 0.5)
    if signal in SLICE_GROUNDING_SIGNALS:
        return {
            "zero division error": 4.0,
            "boundary error": 4.0,
            "key error": 3.0,
            "api misuse": 2.5,
            "condition error": 2.0,
            "type error": 1.5,
        }.get(bug_type, 0.5)
    if signal == "fragile_top1_margin":
        return {
            "boundary error": 4.0,
            "api misuse": 3.0,
            "key error": 2.5,
            "condition error": 2.5,
            "type error": 2.0,
            "zero division error": 2.0,
        }.get(bug_type, 0.5)
    if signal == "subscript_key_flow":
        return {
            "key error": 5.0,
            "type error": 1.0,
        }.get(bug_type, 0.5)
    return 0.5


def _search_competition_rules(signal: str) -> list[str]:
    if signal == "search_candidate_competition":
        return SEARCH_CANDIDATE_COMPETITION_RULES
    if signal == "search_score_inversion":
        return SEARCH_SCORE_INVERSION_RULES
    if signal == "search_diversity_reranking":
        return SEARCH_CANDIDATE_COMPETITION_RULES
    if signal == "search_failure_pressure":
        return SEARCH_FAILURE_PRESSURE_RULES
    return WIDE_BEAM_RULES


def _focus_bonus(
    candidate: dict[str, Any],
    metadata: dict[str, Any],
    signal: str,
) -> float:
    focuses = (
        _string_set(candidate.get("benchmark_focuses"))
        | _string_set(metadata.get("target_benchmark_focuses"))
    )
    patterns = (
        _string_set(candidate.get("patterns"))
        | _string_set(metadata.get("target_calibration_patterns"))
    )
    bonus = 0.0
    if "near-miss semantic repair" in focuses:
        bonus += 1.5
    if "execution-evidence calibration" in focuses:
        bonus += 1.0
    if "judge false-positive hardening" in focuses:
        bonus += 1.0
    if signal in {
        "failed_before_success",
        "reflection_depth",
        "without_beam_search",
        "search_score_inversion",
        "search_failure_pressure",
        "fragile_top1_margin",
    } and any(
        pattern.startswith("failure_type=") for pattern in patterns
    ):
        bonus += 1.0
    if signal in SLICE_GROUNDING_SIGNALS and "execution-evidence calibration" in focuses:
        bonus += 1.0
    if signal in SLICE_GROUNDING_SIGNALS and "near-miss semantic repair" in focuses:
        bonus += 1.0
    return bonus


def _direct_catalog_cases(
    prioritized_catalog: dict[str, Any],
    *,
    max_cases: int,
    upstream: str = "",
    include_rules: list[str] | None = None,
) -> list[dict[str, Any]]:
    cases = []
    include_rule_set = set(include_rules or [])
    for candidate in _extract_candidates(prioritized_catalog):
        case = _candidate_template_case(candidate)
        metadata = _benchmark_metadata(case)
        if upstream and str(metadata.get("upstream", "")) != upstream:
            continue
        if include_rule_set and not (set(_candidate_rule_ids(candidate)) & include_rule_set):
            continue
        cases.append(_catalog_case_from_candidate(candidate))
        if len(cases) >= max_cases:
            break
    return cases


def _slice_grounding_catalog_payload(
    prioritized_catalog: dict[str, Any],
) -> dict[str, Any]:
    candidates = _extract_candidates(prioritized_catalog)
    if not candidates:
        return prioritized_catalog
    preferred: list[dict[str, Any]] = []
    fallback: list[dict[str, Any]] = []
    slice_rules = set(SLICE_GROUNDING_RULES)
    baseline_rules = SLICE_GROUNDING_DIVERSITY_BASELINE_RULES
    for candidate in candidates:
        rules = set(_candidate_rule_ids(candidate))
        if rules & slice_rules and not (rules & baseline_rules):
            preferred.append(candidate)
        else:
            fallback.append(candidate)
    if not preferred:
        return prioritized_catalog
    output = dict(prioritized_catalog)
    output["candidates"] = [*preferred, *fallback]
    return output


def _catalog_case_from_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    output = _deepcopy_json(_candidate_template_case(candidate))
    benchmark = dict(output.get("benchmark", {}))
    updated_metadata = dict(benchmark.get("metadata", {}))
    updated_metadata["source_candidate_id"] = str(candidate.get("id", ""))
    benchmark["metadata"] = updated_metadata
    output["benchmark"] = benchmark
    return output


def _score_inversion_probe_cases(
    prioritized_catalog: dict[str, Any],
    *,
    max_cases: int,
) -> list[dict[str, Any]]:
    cases = _direct_catalog_cases(
        prioritized_catalog,
        max_cases=max_cases,
        include_rules=["possible_index_overrun"],
    )
    return [_as_score_inversion_probe(case) for case in cases]


def _diversity_reranking_probe_cases(
    prioritized_catalog: dict[str, Any],
    *,
    max_cases: int,
) -> list[dict[str, Any]]:
    cases = _direct_catalog_cases(
        prioritized_catalog,
        max_cases=max_cases,
        include_rules=["possible_index_overrun"],
    )
    return [_as_diversity_reranking_probe(case) for case in cases]


def _candidate_deduplication_probe_cases(
    prioritized_catalog: dict[str, Any],
    *,
    max_cases: int,
) -> list[dict[str, Any]]:
    cases = _direct_catalog_cases(
        prioritized_catalog,
        max_cases=max_cases,
        include_rules=WIDE_BEAM_RULES,
    )
    return [_as_candidate_deduplication_probe(case) for case in cases]


def _reflection_depth_probe_cases(
    prioritized_catalog: dict[str, Any],
    *,
    max_cases: int,
) -> list[dict[str, Any]]:
    cases = _direct_catalog_cases(
        prioritized_catalog,
        max_cases=max_cases,
        include_rules=REFLECTION_DEPTH_RULES,
    )
    return [_as_reflection_depth_probe(case) for case in cases]


def _reflection_depth_pressure_cases(*, max_cases: int) -> list[dict[str, Any]]:
    if max_cases <= 0:
        return []
    source = (
        "def pairwise_deltas(values):\n"
        "    deltas = []\n"
        "    for i in range(len(values)):\n"
        "        deltas.append(values[i + 1] - values[i])\n"
        "    return deltas\n"
    )
    test_source = (
        "from sample import pairwise_deltas\n\n"
        "def test_pairwise_deltas():\n"
        "    assert pairwise_deltas([1, 3, 6, 10]) == [2, 3, 4]\n"
    )
    return [
        {
            "name": "reflection_depth_pressure",
            "repo_path": "reflection_depth_pressure_repo",
            "files": [
                {"target_path": "sample.py", "content": source},
                {"target_path": "test_sample.py", "content": test_source},
            ],
            "benchmark": {
                "buggy_functions": ["pairwise_deltas"],
                "expected_rule_ids": ["possible_index_overrun"],
                "failing_tests": ["test_sample.py::test_pairwise_deltas"],
                "passed_tests": [],
                "test_args": [],
                "metadata": {
                    "source": "synthetic_reflection_depth_pressure",
                    "bug_type": "reflection depth pressure",
                    "search_pressure": "reflection_depth_probe",
                    "patch_score_profile": "reflection_depth_probe",
                    "reflection_seed_variant": "overly_conservative_range_bound",
                    "reflection_success_variant": "shrink_range_upper_bound",
                    "expected_reflection_depth": True,
                    "target_benchmark_signals": ["reflection_depth"],
                },
            },
        }
    ][:max_cases]


def _candidate_deduplication_pressure_cases(*, max_cases: int) -> list[dict[str, Any]]:
    if max_cases <= 0:
        return []
    source = (
        "def pairwise_deltas(values):\n"
        "    deltas = []\n"
        "    for i in range(len(values)):\n"
        "        deltas.append(values[i + 1] - values[i])\n"
        "    return deltas\n"
    )
    test_source = (
        "from sample import pairwise_deltas\n\n"
        "def test_pairwise_deltas():\n"
        "    assert pairwise_deltas([1, 3, 6, 10]) == [2, 3, 4]\n"
    )
    return [
        {
            "name": "candidate_deduplication_pressure",
            "repo_path": "candidate_deduplication_pressure_repo",
            "files": [
                {"target_path": "sample.py", "content": source},
                {"target_path": "test_sample.py", "content": test_source},
            ],
            "benchmark": {
                "buggy_functions": ["pairwise_deltas"],
                "expected_rule_ids": ["possible_index_overrun"],
                "failing_tests": ["test_sample.py::test_pairwise_deltas"],
                "passed_tests": [],
                "test_args": [],
                "metadata": {
                    "source": "synthetic_candidate_deduplication_pressure",
                    "bug_type": "candidate deduplication pressure",
                    "search_pressure": "candidate_deduplication_probe",
                    "patch_score_profile": "candidate_deduplication_probe",
                    "dedupe_duplicate_variant": "overly_conservative_range_bound",
                    "dedupe_success_variant": "shrink_range_upper_bound",
                    "expected_candidate_deduplication": True,
                    "target_benchmark_signals": [
                        "candidate_deduplication_pressure"
                    ],
                },
            },
        }
    ][:max_cases]


def _synthetic_reflection_depth_ranking(
    cases: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not cases:
        return {}
    return {
        "synthetic_reflection_depth_pressure": {
            "score": 29.0,
            "rank": 1,
            "reasons": [
                "synthetic_reflection_depth_pressure",
                "forced_depth_zero_seed_failure",
                "expected_depth_one_refined_success",
                "catalog_independent_reflection_probe",
            ],
        }
    }


def _synthetic_candidate_deduplication_ranking(
    cases: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not cases:
        return {}
    return {
        "synthetic_candidate_deduplication_pressure": {
            "score": 29.0,
            "rank": 1,
            "reasons": [
                "synthetic_candidate_deduplication_pressure",
                "forced_duplicate_failed_candidates",
                "expected_later_unique_success",
                "catalog_independent_candidate_deduplication_probe",
            ],
        }
    }


def _graph_bundle_pressure_cases(*, max_cases: int) -> list[dict[str, Any]]:
    if max_cases <= 0:
        return []
    decoys = "\n\n".join(
        [
            _graph_bundle_pressure_function("a0_decoy", "a1_decoy"),
            _graph_bundle_pressure_function("a1_decoy", "a2_decoy"),
            _graph_bundle_pressure_function("a2_decoy"),
        ]
    )
    source = (
        f"{decoys}\n\n"
        "def z_left(scores, key):\n"
        "    left = scores[key]\n"
        "    return left + z_right(scores, key)\n\n"
        "def z_right(scores, key):\n"
        "    return scores[key]\n"
    )
    test_source = (
        "from sample import z_left\n\n"
        "def test_graph_bundle_requires_connected_pair():\n"
        "    assert z_left({}, \"missing\") == 0\n"
    )
    return [
        {
            "name": "graph_bundle_pressure",
            "repo_path": "graph_bundle_pressure_repo",
            "files": [
                {"target_path": "sample.py", "content": source},
                {"target_path": "test_sample.py", "content": test_source},
            ],
            "benchmark": {
                "buggy_functions": [
                    "a0_decoy",
                    "a1_decoy",
                    "a2_decoy",
                    "z_left",
                    "z_right",
                ],
                "expected_rule_ids": ["dict_missing_key_guard"],
                "failing_tests": [
                    "test_sample.py::test_graph_bundle_requires_connected_pair"
                ],
                "passed_tests": [],
                "test_args": [],
                "metadata": {
                    "source": "synthetic_graph_bundle_pressure",
                    "bug_type": "graph bundle pressure",
                    "expected_multi_patch_bundle_size": 2,
                    "expected_graph_bundle_pressure": True,
                    "expected_repair_bundle_functions": ["z_left", "z_right"],
                    "target_benchmark_signals": ["without_graph_bundle_search"],
                },
            },
        }
    ][:max_cases]


def _rule_precision_filter_pressure_cases(*, max_cases: int) -> list[dict[str, Any]]:
    if max_cases <= 0:
        return []
    source = (
        "def shift_left(values):\n"
        "    for i in range(len(values)):\n"
        "        values[i] = values[i + 1]\n"
        "    return values\n\n"
        "def guarded_by_source(values):\n"
        "    n = len(values)\n"
        "    if not values:\n"
        "        raise ValueError('empty')\n"
        "    return sum(values) / n\n\n"
        "def guarded_by_len_source(values):\n"
        "    n = len(values)\n"
        "    if len(values) == 0:\n"
        "        raise ValueError('empty')\n"
        "    return sum(values) / n\n\n"
        "def mapping_lookup(values, mapping):\n"
        "    index = str(len(values) // 2)\n"
        "    return mapping[index]\n\n"
        "class Recorder:\n"
        "    def __init__(self):\n"
        "        self.builder = []\n\n"
        "    def add(self, item):\n"
        "        result = self.builder.append(item)\n"
        "        return result\n"
    )
    test_source = (
        "from sample import shift_left\n\n"
        "def test_shift_left():\n"
        "    assert shift_left([1, 2, 3])[:2] == [2, 3]\n"
    )
    return [
        {
            "name": "rule_precision_filter_pressure",
            "repo_path": "rule_precision_filter_pressure_repo",
            "files": [
                {"target_path": "sample.py", "content": source},
                {"target_path": "test_sample.py", "content": test_source},
            ],
            "benchmark": {
                "buggy_functions": ["shift_left"],
                "expected_rule_ids": ["possible_index_overrun"],
                "failing_tests": ["test_sample.py::test_shift_left"],
                "passed_tests": [],
                "test_args": [],
                "metadata": {
                    "source": "synthetic_rule_precision_filter_pressure",
                    "bug_type": "static rule precision pressure",
                    "expected_rule_precision_pressure": True,
                    "expected_filtered_false_positive_rules": [
                        "missing_len_zero_guard",
                        "stringified_numeric_value",
                        "inplace_api_return_value",
                    ],
                    "target_benchmark_signals": [
                        "without_rule_precision_filter"
                    ],
                },
            },
        }
    ][:max_cases]


def _graph_bundle_pressure_function(
    name: str,
    downstream: str | None = None,
) -> str:
    if downstream is None:
        return (
            f"def {name}(scores, key):\n"
            "    return scores[key]\n"
        )
    return (
        f"def {name}(scores, key):\n"
        f"    return scores[key] + {downstream}(scores, key)\n"
    )


def _as_score_inversion_probe(case: dict[str, Any]) -> dict[str, Any]:
    output = _deepcopy_json(case)
    base_name = str(output.get("name", "score_inversion_probe")) or (
        "score_inversion_probe"
    )
    name = f"{base_name}_score_inversion_probe"
    output["name"] = name
    output["repo_path"] = f"{name}_repo"
    benchmark = dict(output.get("benchmark", {}))
    metadata = dict(benchmark.get("metadata", {}))
    target_signals = _string_list(metadata.get("target_benchmark_signals"))
    if "search_score_inversion" not in target_signals:
        target_signals.append("search_score_inversion")
    metadata.update(
        {
            "search_pressure": "score_inversion_probe",
            "patch_score_profile": "prior_decoy_score_inversion",
            "search_score_inversion_profile": "prior_decoy_score_inversion",
            "score_inversion_decoy_variant": "overly_conservative_range_bound",
            "score_inversion_success_variant": "shrink_range_upper_bound",
            "expected_score_inversion": True,
            "target_benchmark_signals": target_signals,
        }
    )
    benchmark["metadata"] = metadata
    output["benchmark"] = benchmark
    return output


def _as_diversity_reranking_probe(case: dict[str, Any]) -> dict[str, Any]:
    output = _deepcopy_json(case)
    base_name = str(output.get("name", "diversity_reranking_probe")) or (
        "diversity_reranking_probe"
    )
    name = f"{base_name}_diversity_reranking_probe"
    output["name"] = name
    output["repo_path"] = f"{name}_repo"
    benchmark = dict(output.get("benchmark", {}))
    metadata = dict(benchmark.get("metadata", {}))
    target_signals = _string_list(metadata.get("target_benchmark_signals"))
    if "search_diversity_reranking" not in target_signals:
        target_signals.append("search_diversity_reranking")
    metadata.update(
        {
            "search_pressure": "diversity_reranking_probe",
            "patch_score_profile": "diversity_reranking_probe",
            "search_diversity_profile": "diversity_reranking_probe",
            "expected_diversity_reranking": True,
            "expected_diversity_assisted_success": True,
            "target_benchmark_signals": target_signals,
        }
    )
    benchmark["metadata"] = metadata
    output["benchmark"] = benchmark
    return output


def _as_candidate_deduplication_probe(case: dict[str, Any]) -> dict[str, Any]:
    output = _deepcopy_json(case)
    base_name = str(output.get("name", "candidate_deduplication_probe")) or (
        "candidate_deduplication_probe"
    )
    name = f"{base_name}_candidate_deduplication_probe"
    output["name"] = name
    output["repo_path"] = f"{name}_repo"
    benchmark = dict(output.get("benchmark", {}))
    metadata = dict(benchmark.get("metadata", {}))
    target_signals = _string_list(metadata.get("target_benchmark_signals"))
    if "candidate_deduplication_pressure" not in target_signals:
        target_signals.append("candidate_deduplication_pressure")
    metadata.update(
        {
            "search_pressure": "candidate_deduplication_probe",
            "patch_score_profile": "candidate_deduplication_probe",
            "dedupe_duplicate_variant": "overly_conservative_range_bound",
            "dedupe_success_variant": "shrink_range_upper_bound",
            "expected_candidate_deduplication": True,
            "target_benchmark_signals": target_signals,
        }
    )
    benchmark["metadata"] = metadata
    output["benchmark"] = benchmark
    return output


def _as_reflection_depth_probe(case: dict[str, Any]) -> dict[str, Any]:
    output = _deepcopy_json(case)
    base_name = str(output.get("name", "reflection_depth_probe")) or (
        "reflection_depth_probe"
    )
    name = f"{base_name}_reflection_depth_probe"
    output["name"] = name
    output["repo_path"] = f"{name}_repo"
    benchmark = dict(output.get("benchmark", {}))
    metadata = dict(benchmark.get("metadata", {}))
    target_signals = _string_list(metadata.get("target_benchmark_signals"))
    if "reflection_depth" not in target_signals:
        target_signals.append("reflection_depth")
    rules = (
        _string_set(metadata.get("expected_rule_ids"))
        | _string_set(metadata.get("rule_ids"))
        | _string_set(metadata.get("composed_rules"))
    )
    seed_variant = "overly_conservative_range_bound"
    success_variant = "shrink_range_upper_bound"
    if "missing_len_zero_guard" in rules and "possible_index_overrun" not in rules:
        seed_variant = "return_default_on_empty"
        success_variant = "insert_len_zero_guard"
    metadata.update(
        {
            "search_pressure": "reflection_depth_probe",
            "patch_score_profile": "reflection_depth_probe",
            "reflection_seed_variant": seed_variant,
            "reflection_success_variant": success_variant,
            "expected_reflection_depth": True,
            "target_benchmark_signals": target_signals,
        }
    )
    benchmark["metadata"] = metadata
    output["benchmark"] = benchmark
    return output


def _annotated_case(
    case: dict[str, Any],
    suggestion: HardCaseSuggestion,
    row: HardCaseGenerationRow,
    ranking: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    output = _deepcopy_json(case)
    benchmark = dict(output.get("benchmark", {}))
    metadata = dict(benchmark.get("metadata", {}))
    selected_ids = _case_selected_candidate_ids(output) or (
        row.selected_candidate_ids or []
    )
    selected_scores = {
        candidate_id: float(ranking[candidate_id]["score"])
        for candidate_id in selected_ids
        if candidate_id in ranking
    }
    selected_reasons = {
        candidate_id: list(ranking[candidate_id]["reasons"])
        for candidate_id in selected_ids
        if candidate_id in ranking
    }
    generated_signals = _string_list(metadata.get("hard_case_target_signals"))
    if suggestion.target_signal not in generated_signals:
        generated_signals.append(suggestion.target_signal)
    target_benchmark_signals = _string_list(metadata.get("target_benchmark_signals"))
    if _should_mark_missing_len_score_inversion(metadata, suggestion):
        if "search_score_inversion" not in generated_signals:
            generated_signals.append("search_score_inversion")
        if "search_score_inversion" not in target_benchmark_signals:
            target_benchmark_signals.append("search_score_inversion")
        metadata.update(
            {
                "search_pressure": "score_inversion_probe",
                "patch_score_profile": "prior_decoy_score_inversion",
                "search_score_inversion_profile": "prior_decoy_score_inversion",
                "score_inversion_decoy_variant": "return_default_on_empty",
                "score_inversion_success_variant": "insert_len_zero_guard",
                "expected_score_inversion": True,
                "target_benchmark_signals": target_benchmark_signals,
            }
        )
    metadata.update(
        {
            "hard_case_generated": True,
            "hard_case_generation_strategy": row.strategy,
            "hard_case_generation_source": suggestion.source,
            "hard_case_generation_priority": suggestion.priority,
            "hard_case_generation_focus": suggestion.benchmark_focus,
            "hard_case_target_signal": suggestion.target_signal,
            "hard_case_target_signals": generated_signals,
            "hard_case_target_count": suggestion.target_count,
            "hard_case_current_count": suggestion.current_count,
            "hard_case_selection_policy": row.selection_policy,
            "hard_case_selected_candidate_ids": selected_ids,
            "hard_case_selected_candidate_scores": selected_scores,
            "hard_case_selected_candidate_reasons": selected_reasons,
        }
    )
    benchmark["metadata"] = metadata
    output["benchmark"] = benchmark
    return output


def _should_mark_missing_len_score_inversion(
    metadata: dict[str, Any],
    suggestion: HardCaseSuggestion,
) -> bool:
    if suggestion.target_signal != "without_static_rules":
        return False
    rules = (
        _string_set(metadata.get("expected_rule_ids"))
        | _string_set(metadata.get("rule_ids"))
        | _string_set(metadata.get("composed_rules"))
    )
    recipe = str(metadata.get("recipe", ""))
    if recipe:
        rules.add(recipe)
    return "missing_len_zero_guard" in rules


def _generated_row(
    suggestion: HardCaseSuggestion,
    *,
    strategy: str,
    generated_count: int,
    reasons: list[str],
    include_rules: list[str],
    wrapper_depth: int = 0,
    names: list[str] | None = None,
    selection_policy: str = "",
    selected_candidate_ids: list[str] | None = None,
    ranking: dict[str, dict[str, Any]] | None = None,
) -> HardCaseGenerationRow:
    status = "generated" if generated_count > 0 else "skipped"
    if reasons:
        effective_reasons = reasons
    elif status == "generated":
        effective_reasons = ["generated_template_cases"]
    else:
        effective_reasons = ["no_template_cases_generated"]
    ranking = ranking or {}
    selected_candidate_ids = selected_candidate_ids or []
    return HardCaseGenerationRow(
        target_signal=suggestion.target_signal,
        priority=suggestion.priority,
        strategy=strategy,
        status=status,
        generated_count=generated_count,
        reasons=effective_reasons,
        include_rules=include_rules,
        wrapper_depth=wrapper_depth,
        source=suggestion.source,
        benchmark_focus=suggestion.benchmark_focus,
        template_case_names=names or [],
        selection_policy=selection_policy,
        selected_candidate_ids=selected_candidate_ids,
        selected_candidate_scores={
            candidate_id: float(ranking[candidate_id]["score"])
            for candidate_id in selected_candidate_ids
            if candidate_id in ranking
        },
        selection_reasons={
            candidate_id: list(ranking[candidate_id]["reasons"])
            for candidate_id in selected_candidate_ids
            if candidate_id in ranking
        },
    )


def _skipped_row(
    suggestion: HardCaseSuggestion,
    reason: str,
) -> HardCaseGenerationRow:
    return HardCaseGenerationRow(
        target_signal=suggestion.target_signal,
        priority=suggestion.priority,
        strategy="unsupported",
        status="skipped",
        generated_count=0,
        reasons=[reason],
        include_rules=[],
        source=suggestion.source,
        benchmark_focus=suggestion.benchmark_focus,
        template_case_names=[],
        selection_policy="",
        selected_candidate_ids=[],
        selected_candidate_scores={},
        selection_reasons={},
    )


def _replace_row_status(
    row: HardCaseGenerationRow,
    status: str,
    reasons: list[str],
    *,
    generated_count: int = 0,
    names: list[str] | None = None,
) -> HardCaseGenerationRow:
    return HardCaseGenerationRow(
        target_signal=row.target_signal,
        priority=row.priority,
        strategy=row.strategy,
        status=status,
        generated_count=generated_count,
        reasons=reasons,
        include_rules=row.include_rules,
        wrapper_depth=row.wrapper_depth,
        source=row.source,
        benchmark_focus=row.benchmark_focus,
        template_case_names=names if names is not None else row.template_case_names,
        selection_policy=row.selection_policy,
        selected_candidate_ids=row.selected_candidate_ids,
        selected_candidate_scores=row.selected_candidate_scores,
        selection_reasons=row.selection_reasons,
    )


def _selection_audit(
    rows: list[HardCaseGenerationRow],
    template: dict[str, Any],
) -> dict[str, Any]:
    selected_candidate_ids: set[str] = set()
    candidate_scores: list[float] = []
    diversity_bonuses: list[float] = []
    target_signals: set[str] = set()
    selection_policies: set[str] = set()

    for row in rows:
        selected_candidate_ids.update(row.selected_candidate_ids or [])
        if row.target_signal:
            target_signals.add(row.target_signal)
        if row.selection_policy:
            selection_policies.add(row.selection_policy)
        for score in (row.selected_candidate_scores or {}).values():
            candidate_scores.append(float(score))
        for reasons in (row.selection_reasons or {}).values():
            diversity_bonuses.extend(_diversity_bonuses_from_reasons(reasons))

    cases = template.get("cases", [])
    if not isinstance(cases, list):
        cases = []
    rules: set[str] = set()
    bug_types: set[str] = set()
    functions: set[str] = set()
    sources: set[str] = set()
    upstreams: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            continue
        benchmark = case.get("benchmark", {})
        if not isinstance(benchmark, dict):
            continue
        rules.update(str(rule) for rule in benchmark.get("expected_rule_ids", []) if str(rule))
        functions.update(
            str(function)
            for function in benchmark.get("buggy_functions", [])
            if str(function)
        )
        metadata = _benchmark_metadata(case)
        bug_type = str(metadata.get("bug_type", ""))
        if bug_type:
            bug_types.add(bug_type)
        composed_rules = metadata.get("composed_rules")
        if isinstance(composed_rules, list):
            rules.update(str(rule) for rule in composed_rules if str(rule))
        composed_functions = metadata.get("composed_functions")
        if isinstance(composed_functions, list):
            functions.update(str(function) for function in composed_functions if str(function))
        upstream = str(metadata.get("upstream", ""))
        if upstream:
            upstreams.add(upstream)
        source_targets = metadata.get("source_targets")
        if isinstance(source_targets, list):
            sources.update(str(source) for source in source_targets if str(source))
        sources.update(_case_source_targets(case))
        case_sources = case.get("sources", [])
        if not isinstance(case_sources, list):
            case_sources = []
        for source in case_sources:
            if not isinstance(source, dict):
                continue
            owner = str(source.get("owner", ""))
            repo = str(source.get("repo", ""))
            if owner and repo:
                upstreams.add(f"{owner}/{repo}")

    return {
        "selected_candidate_count": len(selected_candidate_ids),
        "selected_candidate_ids": sorted(selected_candidate_ids),
        "selected_rule_count": len(rules),
        "selected_rules": sorted(rules),
        "selected_bug_type_count": len(bug_types),
        "selected_bug_types": sorted(bug_types),
        "selected_function_count": len(functions),
        "selected_functions": sorted(functions),
        "selected_source_count": len(sources),
        "selected_sources": sorted(sources),
        "selected_upstream_count": len(upstreams),
        "selected_upstreams": sorted(upstreams),
        "target_signal_count": len(target_signals),
        "target_signals": sorted(target_signals),
        "selection_policies": sorted(selection_policies),
        "average_candidate_score": _mean(candidate_scores),
        "average_diversity_bonus": _mean(diversity_bonuses),
        "max_diversity_bonus": max(diversity_bonuses) if diversity_bonuses else 0.0,
    }


def _diversity_bonuses_from_reasons(reasons: list[str]) -> list[float]:
    values = []
    for reason in reasons:
        if not reason.startswith("diversity_bonus="):
            continue
        try:
            values.append(float(reason.split("=", 1)[1]))
        except ValueError:
            continue
    return values


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _report_cases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    template = payload.get("template", {})
    if not isinstance(template, dict):
        return []
    cases = template.get("cases", [])
    if not isinstance(cases, list):
        return []
    return [case for case in cases if isinstance(case, dict)]


def _composition_reasons(payload: dict[str, Any]) -> list[str]:
    rows = payload.get("rows", [])
    reasons: list[str] = []
    if not isinstance(rows, list):
        return reasons
    for row in rows:
        if not isinstance(row, dict) or row.get("status") != "composed":
            continue
        for reason in row.get("reasons", []):
            text = str(reason)
            if text and text not in reasons:
                reasons.append(text)
    return reasons


def _selected_candidate_ids_from_cases(cases: list[dict[str, Any]]) -> list[str]:
    selected: list[str] = []
    for case in cases:
        for candidate_id in _case_selected_candidate_ids(case):
            if candidate_id not in selected:
                selected.append(candidate_id)
    return selected


def _case_selected_candidate_ids(case: dict[str, Any]) -> list[str]:
    metadata = _benchmark_metadata(case)
    selected: list[str] = []
    composed = metadata.get("composed_candidate_ids")
    if isinstance(composed, list):
        selected.extend(str(item) for item in composed if str(item))
    source_candidate_id = str(metadata.get("source_candidate_id", ""))
    if source_candidate_id:
        selected.append(source_candidate_id)
    realization_candidate_id = str(metadata.get("realization_candidate_id", ""))
    if realization_candidate_id:
        selected.append(realization_candidate_id)
    output: list[str] = []
    for candidate_id in selected:
        if candidate_id not in output:
            output.append(candidate_id)
    return output


def _selection_reason_summary(
    selected_candidate_ids: list[str],
    ranking: dict[str, dict[str, Any]],
) -> list[str]:
    summary = []
    for candidate_id in selected_candidate_ids[:4]:
        item = ranking.get(candidate_id, {})
        if not item:
            continue
        score = float(item.get("score", 0.0))
        rank = int(item.get("rank", 0))
        summary.append(f"selected_candidate={candidate_id}:rank={rank}:score={score:.2f}")
    return summary


def _candidate_id(candidate: dict[str, Any]) -> str:
    candidate_id = str(candidate.get("id", ""))
    if candidate_id:
        return candidate_id
    case = _candidate_template_case(candidate)
    name = str(case.get("name", ""))
    return name or "<anonymous_candidate>"


def _candidate_rule_ids(candidate: dict[str, Any]) -> list[str]:
    rules = candidate.get("rule_ids")
    if isinstance(rules, list):
        return [str(rule) for rule in rules if str(rule)]
    case = _candidate_template_case(candidate)
    benchmark = case.get("benchmark", {}) if isinstance(case, dict) else {}
    if isinstance(benchmark, dict) and isinstance(benchmark.get("expected_rule_ids"), list):
        return [str(rule) for rule in benchmark["expected_rule_ids"] if str(rule)]
    return []


def _simple_buggy_function(case: dict[str, Any]) -> str:
    benchmark = case.get("benchmark", {})
    if not isinstance(benchmark, dict):
        return ""
    functions = benchmark.get("buggy_functions", [])
    if not isinstance(functions, list) or len(functions) != 1:
        return ""
    name = str(functions[0])
    return name if name.isidentifier() else ""


def _case_source_targets(case: dict[str, Any]) -> list[str]:
    sources = case.get("sources", [])
    if not isinstance(sources, list):
        return []
    return [
        str(source.get("target_path", ""))
        for source in sources
        if isinstance(source, dict) and str(source.get("target_path", ""))
    ]


def _single_simple_python_source(case: dict[str, Any]) -> bool:
    targets = _case_source_targets(case)
    return len(targets) == 1 and targets[0].endswith(".py") and "/" not in targets[0]


def _test_imports_simple_function(case: dict[str, Any], function_name: str) -> bool:
    if not function_name:
        return False
    targets = _case_source_targets(case)
    if len(targets) != 1 or not targets[0].endswith(".py"):
        return False
    module = targets[0][:-3].replace("/", ".").replace("\\", ".")
    files = case.get("files", [])
    if not isinstance(files, list):
        return False
    direct_import = f"from {module} import {function_name}"
    return any(
        isinstance(file, dict)
        and direct_import in str(file.get("content", ""))
        for file in files
    )


def _extract_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        catalog = payload.get("catalog", {})
        if isinstance(catalog, dict):
            candidates = catalog.get("candidates", [])
    if not isinstance(candidates, list):
        return []
    return [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict)
        and isinstance(candidate.get("template_case"), dict)
    ]


def _candidate_template_case(candidate: dict[str, Any]) -> dict[str, Any]:
    case = candidate.get("template_case", {})
    return case if isinstance(case, dict) else {}


def _benchmark_metadata(case: dict[str, Any]) -> dict[str, Any]:
    benchmark = case.get("benchmark", {})
    if not isinstance(benchmark, dict):
        return {}
    metadata = benchmark.get("metadata", {})
    return metadata if isinstance(metadata, dict) else {}


def _template_validation_errors(template: dict[str, Any]) -> list[str]:
    if not template.get("cases"):
        return []
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "template.json"
        path.write_text(
            json.dumps(template, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        report = BenchmarkValidator().validate_template(path)
    return [f"{issue.location}:{issue.message}" for issue in report.errors]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _string_set(value: Any) -> set[str]:
    return set(_string_list(value))


def _deepcopy_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate concrete benchmark candidates from hard-case mining gaps "
            "and a recipe/source-mining catalog."
        )
    )
    parser.add_argument("suite", help="Experiment suite JSON or benchmark report JSON.")
    parser.add_argument("catalog", help="Recipe/source-mining catalog JSON.")
    parser.add_argument(
        "--max-cases-per-suggestion",
        type=int,
        default=1,
        help="Maximum generated cases for each mining suggestion.",
    )
    parser.add_argument(
        "--max-total-cases",
        type=int,
        default=None,
        help="Optional global cap for generated cases.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format.",
    )
    parser.add_argument("--output-json", help="Optional full report JSON path.")
    parser.add_argument("--output-markdown", help="Optional markdown report path.")
    parser.add_argument(
        "--output-template",
        help="Optional generated benchmark template JSON path.",
    )
    args = parser.parse_args()

    report = generate_hard_case_candidates(
        load_json(args.suite),
        load_json(args.catalog),
        suite_path=str(args.suite),
        catalog_path=str(args.catalog),
        max_cases_per_suggestion=args.max_cases_per_suggestion,
        max_total_cases=args.max_total_cases,
    )
    payload = report.to_dict()
    json_report = json.dumps(payload, indent=2, ensure_ascii=False)
    markdown_report = render_hard_case_generation_markdown(report)
    if args.output_json:
        Path(args.output_json).write_text(json_report, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown_report, encoding="utf-8")
    if args.output_template:
        template_cases = payload["template"].get("cases", [])
        if template_cases:
            Path(args.output_template).write_text(
                json.dumps(payload["template"], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            print(
                "No generated cases; skipped writing benchmark template "
                f"{args.output_template}.",
                file=sys.stderr,
            )
    if args.format == "markdown":
        print(markdown_report)
    else:
        print(json_report)


if __name__ == "__main__":
    main()
