from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.generated_ablation_links import (
    SLICE_GROUNDING_TARGET_SIGNALS,
    ablation_component as _ablation_component,
    ablation_rationale_for_generated_signal as _ablation_rationale_for_generated_signal,
    ablation_variants_for_generated_signal as _ablation_variants_for_generated_signal,
    signal_variant_link_priority as _signal_variant_link_priority,
)
from code_intelligence_agent.evaluation.generated_diversity_evidence import (
    generated_diversity_budget_evidence,
)


GENERATED_SLICE_EVIDENCE_MIN = 0.70

KEY_METRICS = (
    "case_count",
    "top1",
    "top3",
    "mrr",
    "map",
    "ndcg_at_3",
    "expected_rule_recall",
    "expected_rule_precision",
    "patch_success_rate",
    "multi_patch_success_rate",
    "beam_success_rate",
    "patch_search_top1_success_rate",
        "patch_search_mrr",
        "search_efficiency",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a resume-oriented algorithm showcase report from a CIA "
            "suite.json or benchmark_report.json artifact."
        )
    )
    parser.add_argument("artifact", help="Path to suite.json or benchmark JSON")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
        help="Report format",
    )
    parser.add_argument("--output-json", help="Optional path for JSON output")
    parser.add_argument("--output-markdown", help="Optional path for Markdown output")
    parser.add_argument(
        "--output-resume-markdown",
        help="Optional path for compact resume showcase Markdown output",
    )
    args = parser.parse_args()

    payload = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
    report = build_showcase_report(payload)
    json_report = json.dumps(report, indent=2, ensure_ascii=False)
    markdown_report = render_showcase_markdown(report)
    resume_markdown_report = render_resume_showcase_markdown(report)
    if args.output_json:
        Path(args.output_json).write_text(json_report, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown_report, encoding="utf-8")
    if args.output_resume_markdown:
        Path(args.output_resume_markdown).write_text(
            resume_markdown_report,
            encoding="utf-8",
        )
    if args.format == "json":
        print(json_report)
    else:
        print(markdown_report)


def build_showcase_report(payload: dict[str, Any]) -> dict[str, Any]:
    benchmark = _extract_benchmark_report(payload)
    summary = dict(benchmark.get("summary", {}))
    cases = list(benchmark.get("cases", []))
    if "case_count" not in summary:
        summary["case_count"] = len(cases)

    headline = _headline_metrics(summary)
    difficulty = _dict(summary.get("difficulty_report"))
    generalization = _dict(summary.get("generalization_report"))
    quality_gate = _dict(payload.get("quality_gate"))
    hard_case_mining = _dict(payload.get("hard_case_mining"))
    benchmark_mining = _dict(payload.get("benchmark_mining"))
    hard_case_generation = _dict(payload.get("hard_case_generation"))
    hard_case_generated_benchmark = _dict(
        payload.get("hard_case_generated_benchmark")
    )
    patch_judge_fusion = _dict(payload.get("patch_judge_fusion_summary"))
    ablation_impact = _dict(payload.get("ablation_impact"))
    weight_search_results = _list(payload.get("weight_search_results"))
    patch_weight_search_results = _list(payload.get("patch_weight_search_results"))

    phase_readiness = _phase_readiness(
        summary=summary,
        cases=cases,
        payload=payload,
        quality_gate=quality_gate,
    )
    phase_milestone_audit = _phase_milestone_audit(
        summary=summary,
        cases=cases,
        payload=payload,
        quality_gate=quality_gate,
        hard_case_generation=hard_case_generation,
        hard_case_generated_benchmark=hard_case_generated_benchmark,
        weight_search_results=weight_search_results,
        patch_weight_search_results=patch_weight_search_results,
    )
    algorithm_evidence = _algorithm_evidence(
        summary=summary,
        difficulty=difficulty,
        generalization=generalization,
        quality_gate=quality_gate,
        hard_case_mining=hard_case_mining,
        benchmark_mining=benchmark_mining,
        hard_case_generation=hard_case_generation,
        hard_case_generated_benchmark=hard_case_generated_benchmark,
        patch_judge_fusion=patch_judge_fusion,
        ablation_impact=ablation_impact,
        weight_search_results=weight_search_results,
        patch_weight_search_results=patch_weight_search_results,
    )
    generated_hard_case_breakdown = _generated_hard_case_breakdown(
        hard_case_generation=hard_case_generation,
        hard_case_generated_benchmark=hard_case_generated_benchmark,
    )
    generated_hard_case_evidence = _generated_hard_case_evidence(
        hard_case_generated_benchmark
    )
    generated_hard_case_ablation = _generated_hard_case_ablation_links(
        generated_hard_case_evidence["traces"],
        ablation_impact,
        _dict(
            algorithm_evidence.get("generated_search_diversity_reranking")
        ),
    )
    case_evidence_traces = _case_evidence_traces(cases)
    case_evidence_trace_summary = _case_evidence_trace_summary(
        case_evidence_traces
    )
    ablation_highlights = _ablation_highlights(ablation_impact)
    ablation_component_summary = _ablation_component_summary(ablation_impact)
    quality_summary = _quality_summary(quality_gate)
    gaps = _gaps(
        summary=summary,
        payload=payload,
        quality_summary=quality_summary,
        hard_case_mining=hard_case_mining,
        benchmark_mining=benchmark_mining,
        hard_case_generation=hard_case_generation,
    )
    return {
        "artifact_kind": _artifact_kind(payload),
        "headline": headline,
        "readiness_score": _readiness_score(phase_readiness),
        "phase_readiness": phase_readiness,
        "phase_milestone_audit": phase_milestone_audit,
        "algorithm_evidence": algorithm_evidence,
        "generated_hard_case_breakdown": generated_hard_case_breakdown,
        "generated_hard_case_evidence_summary": generated_hard_case_evidence[
            "summary"
        ],
        "generated_hard_case_evidence_traces": generated_hard_case_evidence[
            "traces"
        ],
        "generated_hard_case_ablation_link_summary": (
            generated_hard_case_ablation["summary"]
        ),
        "generated_hard_case_ablation_links": (
            generated_hard_case_ablation["links"]
        ),
        "case_evidence_trace_summary": case_evidence_trace_summary,
        "case_evidence_traces": case_evidence_traces,
        "ablation_highlights": ablation_highlights,
        "ablation_component_summary": ablation_component_summary,
        "quality_summary": quality_summary,
        "resume_bullets": _resume_bullets(
            headline=headline,
            algorithm_evidence=algorithm_evidence,
            ablation_highlights=ablation_highlights,
        ),
        "gaps": gaps,
    }


def render_showcase_markdown(report: dict[str, Any]) -> str:
    headline = _dict(report.get("headline"))
    evidence = _dict(report.get("algorithm_evidence"))
    quality = _dict(report.get("quality_summary"))
    lines = [
        "# Algorithm Showcase Report",
        "",
        f"- Artifact: {report.get('artifact_kind', 'unknown')}",
        f"- Readiness Score: {_fmt_number(report.get('readiness_score', 0.0))}",
        f"- Quality Gate: {quality.get('status', 'missing')}",
        "",
        "## Headline Metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for key, value in headline.items():
        lines.append(f"| {key} | {_fmt_number(value)} |")

    lines.extend(
        [
            "",
            "## Phase Readiness",
            "",
            "| Phase | Status | Evidence |",
            "| --- | --- | --- |",
        ]
    )
    for row in _list(report.get("phase_readiness")):
        lines.append(
            "| "
            f"{row.get('phase', '')}: {row.get('name', '')} | "
            f"{row.get('status', '')} | "
            f"{_markdown_cell('; '.join(_list(row.get('evidence'))))} |"
        )

    lines.extend(
        [
            "",
            "## Phase Milestone Audit",
            "",
            "| Phase | Milestone | Status | Evidence |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in _list(report.get("phase_milestone_audit")):
        if not isinstance(row, dict):
            continue
        lines.append(
            "| "
            f"{_markdown_cell(row.get('phase', ''))} | "
            f"{_markdown_cell(row.get('milestone', ''))} | "
            f"{_markdown_cell(row.get('status', ''))} | "
            f"{_markdown_cell('; '.join(_list(row.get('evidence'))))} |"
        )

    trace_summary = _dict(report.get("case_evidence_trace_summary"))
    lines.extend(
        [
            "",
            "## Case-Level Evidence Trace",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| traced_cases | {_int(trace_summary.get('traced_cases', 0))} |",
            f"| top1_hits | {_int(trace_summary.get('top1_hits', 0))} |",
            f"| patch_successes | {_int(trace_summary.get('patch_successes', 0))} |",
            f"| score_inversions | {_int(trace_summary.get('score_inversions', 0))} |",
            f"| cross_function_traces | {_int(trace_summary.get('cross_function_traces', 0))} |",
            "",
            "| Case | Rule | Top Function | Signals | Graph Evidence | Search | Patch |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in _list(report.get("case_evidence_traces"))[:12]:
        if not isinstance(row, dict):
            continue
        top = _dict(row.get("top_localization"))
        signals = _dict(top.get("signals"))
        graph = _dict(top.get("graph_evidence"))
        search = _dict(row.get("search_trace"))
        patch = _dict(row.get("patch_trace"))
        candidate = _dict(row.get("best_candidate"))
        lines.append(
            "| "
            f"{_markdown_cell(row.get('case', ''))} | "
            f"{_markdown_cell(', '.join(_list(row.get('expected_rules'))))} | "
            f"{_markdown_cell(top.get('function', ''))} | "
            f"{_markdown_cell(_trace_signal_summary(signals))} | "
            f"{_markdown_cell(_trace_graph_summary(graph, top))} | "
            f"{_markdown_cell(_trace_search_summary(search))} | "
            f"{_markdown_cell(_trace_patch_summary(patch, candidate))} |"
        )

    lines.extend(["", "## Algorithm Evidence", ""])
    for name, section in evidence.items():
        if not isinstance(section, dict):
            continue
        lines.extend(
            [
                f"### {_title(name)}",
                "",
                "| Signal | Value |",
                "| --- | ---: |",
            ]
        )
        for key, value in section.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            lines.append(f"| {key} | {_markdown_cell(_fmt_number(value))} |")
        lines.append("")

    breakdown = _dict(report.get("generated_hard_case_breakdown"))
    if _int(breakdown.get("case_count", 0)) > 0:
        lines.extend(
            [
                "## Generated Hard-Case Breakdown",
                "",
                "| Signal | Cases | Patch Success | Score Inversions | Failure Pressure | Rules |",
                "| --- | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for row in _list(breakdown.get("by_signal")):
            if not isinstance(row, dict):
                continue
            lines.append(
                "| "
                f"{_markdown_cell(row.get('signal', ''))} | "
                f"{_int(row.get('case_count', 0))} | "
                f"{_fmt_number(_float(row.get('patch_success_rate', 0.0)))} | "
                f"{_int(row.get('score_inversion_count', 0))} | "
                f"{_fmt_number(_float(row.get('average_failure_pressure', 0.0)))} | "
                f"{_markdown_cell(_join_items(row.get('rules')))} |"
            )
        lines.extend(
            [
                "",
                "| Rule | Cases | Patch Success | Score Inversions | Failure Pressure | Signals |",
                "| --- | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for row in _list(breakdown.get("by_rule")):
            if not isinstance(row, dict):
                continue
            lines.append(
                "| "
                f"{_markdown_cell(row.get('rule', ''))} | "
                f"{_int(row.get('case_count', 0))} | "
                f"{_fmt_number(_float(row.get('patch_success_rate', 0.0)))} | "
                f"{_int(row.get('score_inversion_count', 0))} | "
                f"{_fmt_number(_float(row.get('average_failure_pressure', 0.0)))} | "
                f"{_markdown_cell(_join_items(row.get('signals')))} |"
            )
        lines.append("")

    generated_trace_summary = _dict(
        report.get("generated_hard_case_evidence_summary")
    )
    if _int(generated_trace_summary.get("generated_cases", 0)) > 0:
        lines.extend(
            [
                "## Generated Hard-Case Evidence Trace",
                "",
                "| Metric | Value |",
                "| --- | ---: |",
                f"| generated_cases | {_int(generated_trace_summary.get('generated_cases', 0))} |",
                f"| target_signals | {_int(generated_trace_summary.get('target_signals', 0))} |",
                f"| patch_successes | {_int(generated_trace_summary.get('patch_successes', 0))} |",
                f"| score_inversions | {_int(generated_trace_summary.get('score_inversions', 0))} |",
                f"| expected_diversity_reranking_cases | {_int(generated_trace_summary.get('expected_diversity_reranking_cases', 0))} |",
                f"| diversity_assisted_successes | {_int(generated_trace_summary.get('diversity_assisted_successes', 0))} |",
                f"| average_success_diversity_lift | {_fmt_number(_float(generated_trace_summary.get('average_success_diversity_lift', 0.0)))} |",
                f"| average_success_diversity_bonus | {_fmt_number(_float(generated_trace_summary.get('average_success_diversity_bonus', 0.0)))} |",
                f"| expected_reflection_depth_cases | {_int(generated_trace_summary.get('expected_reflection_depth_cases', 0))} |",
                f"| reflection_success_cases | {_int(generated_trace_summary.get('reflection_success_cases', 0))} |",
                f"| reflection_candidates | {_int(generated_trace_summary.get('reflection_candidates', 0))} |",
                f"| successful_reflection_candidates | {_int(generated_trace_summary.get('successful_reflection_candidates', 0))} |",
                f"| reflection_candidate_success_rate | {_fmt_number(_float(generated_trace_summary.get('reflection_candidate_success_rate', 0.0)))} |",
                f"| average_success_reflection_depth | {_fmt_number(_float(generated_trace_summary.get('average_success_reflection_depth', 0.0)))} |",
                f"| candidate_competition_cases | {_int(generated_trace_summary.get('candidate_competition_cases', 0))} |",
                f"| expected_candidate_deduplication_cases | {_int(generated_trace_summary.get('expected_candidate_deduplication_cases', 0))} |",
                f"| candidate_deduplication_evidence_cases | {_int(generated_trace_summary.get('candidate_deduplication_evidence_cases', 0))} |",
                f"| deduplicated_candidates | {_int(generated_trace_summary.get('deduplicated_candidates', 0))} |",
                f"| max_deduplicated_candidates | {_int(generated_trace_summary.get('max_deduplicated_candidates', 0))} |",
                f"| average_candidate_deduplication_pressure | {_fmt_number(_float(generated_trace_summary.get('average_candidate_deduplication_pressure', 0.0)))} |",
                f"| failure_pressure_cases | {_int(generated_trace_summary.get('failure_pressure_cases', 0))} |",
                f"| average_failure_pressure | {_fmt_number(_float(generated_trace_summary.get('average_failure_pressure', 0.0)))} |",
                f"| slice_grounding_target_cases | {_int(generated_trace_summary.get('slice_grounding_target_cases', 0))} |",
                f"| slice_grounding_evidence_cases | {_int(generated_trace_summary.get('slice_grounding_evidence_cases', 0))} |",
                f"| slice_grounding_evidence_rate | {_fmt_number(_float(generated_trace_summary.get('slice_grounding_evidence_rate', 0.0)))} |",
                f"| slice_grounding_pressure_cases | {_int(generated_trace_summary.get('slice_grounding_pressure_cases', 0))} |",
                f"| average_slice_grounding_pressure | {_fmt_number(_float(generated_trace_summary.get('average_slice_grounding_pressure', 0.0)))} |",
                f"| average_slice_grounding_target_pressure | {_fmt_number(_float(generated_trace_summary.get('average_slice_grounding_target_pressure', 0.0)))} |",
                f"| provenance_selected_cases | {_int(generated_trace_summary.get('provenance_selected_cases', 0))} |",
                f"| average_provenance_bonus | {_fmt_number(_float(generated_trace_summary.get('average_provenance_bonus', 0.0)))} |",
                f"| average_provenance_stable_ref | {_fmt_number(_float(generated_trace_summary.get('average_provenance_stable_ref', 0.0)))} |",
                f"| average_provenance_license | {_fmt_number(_float(generated_trace_summary.get('average_provenance_license', 0.0)))} |",
                f"| average_provenance_leakage_risk | {_fmt_number(_float(generated_trace_summary.get('average_provenance_leakage_risk', 0.0)))} |",
                "",
                "| Case | Target Signal | Strategy | Selection | Search Pressure | Slice Pressure | Reflection Pressure | Decoy -> Success | Patch Evidence |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in _list(report.get("generated_hard_case_evidence_traces")):
            if not isinstance(row, dict):
                continue
            search = _dict(row.get("search_pressure"))
            slice_pressure = _dict(row.get("slice_grounding_pressure"))
            reflection_pressure = _dict(row.get("reflection_pressure"))
            patch = _dict(row.get("patch_evidence"))
            lines.append(
                "| "
                f"{_markdown_cell(row.get('case', ''))} | "
                f"{_markdown_cell(_join_items(row.get('target_signals')))} | "
                f"{_markdown_cell(row.get('generation_strategy', ''))} | "
                f"{_markdown_cell(_generated_selection_summary(row))} | "
                f"{_markdown_cell(_generated_search_pressure_summary(search))} | "
                f"{_markdown_cell(_generated_slice_pressure_summary(slice_pressure))} | "
                f"{_markdown_cell(_generated_reflection_pressure_summary(reflection_pressure))} | "
                f"{_markdown_cell(_generated_decoy_summary(row))} | "
                f"{_markdown_cell(_generated_patch_evidence_summary(patch))} |"
            )
        lines.append("")

    ablation_link_summary = _dict(
        report.get("generated_hard_case_ablation_link_summary")
    )
    if _int(ablation_link_summary.get("linked_cases", 0)) > 0:
        lines.extend(
            [
                "## Generated Hard-Case Ablation Links",
                "",
                "| Metric | Value |",
                "| --- | ---: |",
                f"| linked_cases | {_int(ablation_link_summary.get('linked_cases', 0))} |",
                f"| regression_linked_cases | {_int(ablation_link_summary.get('regression_linked_cases', 0))} |",
                f"| direct_variant_links | {_int(ablation_link_summary.get('direct_variant_links', 0))} |",
                f"| component_proxy_links | {_int(ablation_link_summary.get('component_proxy_links', 0))} |",
                f"| generated_counterfactual_links | {_int(ablation_link_summary.get('generated_counterfactual_links', 0))} |",
                "",
            ]
        )
        by_signal = _list(ablation_link_summary.get("by_signal"))
        if by_signal:
            lines.extend(
                [
                    "### Generated Ablation Links By Signal",
                    "",
                    "| Signal | Linked Cases | Regressions | Components | Variants | Strongest Delta |",
                    "| --- | ---: | ---: | --- | --- | --- |",
                ]
            )
            for row in by_signal:
                if not isinstance(row, dict):
                    continue
                lines.append(
                    "| "
                    f"{_markdown_cell(row.get('signal', ''))} | "
                    f"{_int(row.get('linked_cases', 0))} | "
                    f"{_int(row.get('regression_linked_cases', 0))} | "
                    f"{_markdown_cell(_join_items(row.get('components')))} | "
                    f"{_markdown_cell(_join_items(row.get('variants')))} | "
                    f"{_markdown_cell(row.get('strongest_delta_metric', ''))}="
                    f"{_fmt_number(row.get('strongest_delta', 0.0))} |"
                )
            lines.append("")
        by_component = _list(ablation_link_summary.get("by_component"))
        if by_component:
            lines.extend(
                [
                    "### Generated Ablation Links By Component",
                    "",
                    "| Component | Linked Cases | Regressions | Signals | Variants | Strongest Delta |",
                    "| --- | ---: | ---: | --- | --- | --- |",
                ]
            )
            for row in by_component:
                if not isinstance(row, dict):
                    continue
                lines.append(
                    "| "
                    f"{_markdown_cell(row.get('component', ''))} | "
                    f"{_int(row.get('linked_cases', 0))} | "
                    f"{_int(row.get('regression_linked_cases', 0))} | "
                    f"{_markdown_cell(_join_items(row.get('target_signals')))} | "
                    f"{_markdown_cell(_join_items(row.get('variants')))} | "
                    f"{_markdown_cell(row.get('strongest_delta_metric', ''))}="
                    f"{_fmt_number(row.get('strongest_delta', 0.0))} |"
                )
            lines.append("")
        lines.extend(
            [
                "| Case | Target Signal | Link Type | Component | Variant | Direction | Main Delta | Evidence |",
                "| --- | --- | --- | --- | --- | --- | ---: | --- |",
            ]
        )
        for row in _list(report.get("generated_hard_case_ablation_links")):
            if not isinstance(row, dict):
                continue
            lines.append(
                "| "
                f"{_markdown_cell(row.get('case', ''))} | "
                f"{_markdown_cell(row.get('target_signal', ''))} | "
                f"{_markdown_cell(row.get('link_type', ''))} | "
                f"{_markdown_cell(row.get('component', ''))} | "
                f"{_markdown_cell(row.get('variant', ''))} | "
                f"{_markdown_cell(row.get('direction', ''))} | "
                f"{_markdown_cell(row.get('main_delta_metric', ''))}="
                f"{_fmt_number(row.get('main_delta', 0.0))} | "
                f"{_markdown_cell(_generated_ablation_link_evidence(row))} |"
            )
        lines.append("")

    highlights = _list(report.get("ablation_highlights"))
    lines.extend(
        [
            "## Ablation Highlights",
            "",
            "| Variant | Direction | Impact | Main Delta |",
            "| --- | --- | ---: | --- |",
        ]
    )
    if highlights:
        for row in highlights:
            lines.append(
                "| "
                f"{row.get('variant', '')} | "
                f"{row.get('direction', '')} | "
                f"{_fmt_number(row.get('impact_score', 0.0))} | "
                f"{row.get('main_delta_metric', '')}="
                f"{_fmt_number(row.get('main_delta', 0.0))} |"
            )
    else:
        lines.append("| none | missing | 0.0000 | n/a |")

    components = _list(report.get("ablation_component_summary"))
    lines.extend(
        [
            "",
            "## Ablation Component Summary",
            "",
            "| Component | Variants | Regressions | Max Impact | Strongest Variant | Main Delta |",
            "| --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    if components:
        for row in components:
            if not isinstance(row, dict):
                continue
            lines.append(
                "| "
                f"{_markdown_cell(row.get('component', ''))} | "
                f"{_int(row.get('variant_count', 0))} | "
                f"{_int(row.get('regression_count', 0))} | "
                f"{_fmt_number(_float(row.get('max_abs_impact', 0.0)))} | "
                f"{_markdown_cell(row.get('strongest_variant', ''))} | "
                f"{_markdown_cell(row.get('strongest_delta_metric', ''))}="
                f"{_fmt_number(_float(row.get('strongest_delta', 0.0)))} |"
            )
    else:
        lines.append("| none | 0 | 0 | 0.0000 | n/a | n/a |")

    lines.extend(["", "## Resume Bullets", ""])
    for bullet in _list(report.get("resume_bullets")):
        lines.append(f"- {bullet}")

    lines.extend(["", "## Gaps", ""])
    gaps = _list(report.get("gaps"))
    if gaps:
        for gap in gaps:
            lines.append(f"- {gap}")
    else:
        lines.append("- No major artifact gaps detected.")
    return "\n".join(lines)


def render_resume_showcase_markdown(report: dict[str, Any]) -> str:
    headline = _dict(report.get("headline"))
    evidence = _dict(report.get("algorithm_evidence"))
    graph = _dict(evidence.get("static_graph_reasoning"))
    robust = _dict(evidence.get("robustness_and_generalization"))
    provenance = _dict(evidence.get("benchmark_provenance"))
    attribution = _dict(evidence.get("localization_attribution"))
    calibration = _dict(evidence.get("confidence_calibration"))
    expansion = _dict(evidence.get("benchmark_expansion"))
    quality = _dict(report.get("quality_summary"))
    generated_summary = _dict(report.get("generated_hard_case_evidence_summary"))
    ablation_links = _dict(report.get("generated_hard_case_ablation_link_summary"))
    generated_diversity = _dict(
        evidence.get("generated_search_diversity_reranking")
    )
    generated_deduplication = _dict(
        evidence.get("generated_candidate_deduplication")
    )
    lines = [
        "# Code Intelligence Agent Resume Showcase",
        "",
        "## Project Positioning",
        "",
        (
            "A graph-guided LLM code intelligence agent for Python repositories, "
            "covering static analysis, function-level fault localization, "
            "sandbox-validated patch generation, beam-search repair, hard-case "
            "generation, and ablation-based evaluation."
        ),
        "",
        "## Key Metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| benchmark_cases | {_int(headline.get('case_count', 0))} |",
        f"| top1_localization | {_fmt_number(_float(headline.get('top1', 0.0)))} |",
        f"| top3_localization | {_fmt_number(_float(headline.get('top3', 0.0)))} |",
        f"| map | {_fmt_number(_float(headline.get('map', 0.0)))} |",
        f"| patch_success_rate | {_fmt_number(_float(headline.get('patch_success_rate', 0.0)))} |",
        f"| beam_success_rate | {_fmt_number(_float(headline.get('beam_success_rate', 0.0)))} |",
        f"| localization_brier_score | {_fmt_number(_float(calibration.get('localization_brier_score', 0.0)))} |",
        f"| localization_expected_calibration_error | {_fmt_number(_float(calibration.get('localization_expected_calibration_error', 0.0)))} |",
        f"| localization_calibrated_brier_score | {_fmt_number(_float(calibration.get('localization_calibrated_brier_score', 0.0)))} |",
        f"| localization_calibrated_expected_calibration_error | {_fmt_number(_float(calibration.get('localization_calibrated_expected_calibration_error', 0.0)))} |",
        f"| cross_function_data_flow_cases | {_int(graph.get('cross_function_data_flow_cases', 0))} |",
        f"| program_slice_cases | {_int(graph.get('program_slice_cases', 0))} |",
        f"| slice_grounded_cases | {_int(graph.get('slice_grounded_cases', 0))} |",
        f"| average_top1_slice_support | {_fmt_number(_float(graph.get('average_top1_slice_support', 0.0)))} |",
        f"| average_top1_slice_edges | {_fmt_number(_float(graph.get('average_top1_slice_edges', 0.0)))} |",
        f"| source_groups | {_int(robust.get('source_group_count', 0))} |",
        f"| source_balance_entropy | {_fmt_number(_float(robust.get('source_balance_entropy', 0.0)))} |",
        f"| generalization_stability_score | {_fmt_number(_float(robust.get('stability_score', 0.0)))} |",
        f"| worst_holdout_gap_score | {_fmt_number(_float(robust.get('worst_holdout_gap_score', 0.0)))} |",
        f"| provenance_case_coverage | {_fmt_number(_float(provenance.get('case_provenance_coverage', 0.0)))} |",
        f"| provenance_source_sha256_coverage | {_fmt_number(_float(provenance.get('source_sha256_coverage', 0.0)))} |",
        f"| provenance_stable_ref_coverage | {_fmt_number(_float(provenance.get('stable_ref_coverage', 0.0)))} |",
        f"| provenance_leakage_risk_score | {_fmt_number(_float(provenance.get('leakage_risk_score', 0.0)))} |",
        f"| attribution_coverage | {_fmt_number(_float(attribution.get('attribution_coverage', 0.0)))} |",
        f"| attribution_mean_top1_margin | {_fmt_number(_float(attribution.get('mean_top1_margin', 0.0)))} |",
        f"| attribution_fragile_top1_rate | {_fmt_number(_float(attribution.get('fragile_top1_rate', 0.0)))} |",
        f"| generated_hard_cases | {_int(expansion.get('generated_hard_cases', 0))} |",
        f"| generated_score_inversions | {_int(expansion.get('generated_benchmark_score_inversions', 0))} |",
        f"| generated_diversity_assisted_successes | {_int(expansion.get('generated_benchmark_diversity_assisted_successes', 0))} |",
        f"| generated_average_success_diversity_lift | {_fmt_number(_float(expansion.get('generated_benchmark_average_success_diversity_lift', 0.0)))} |",
        f"| generated_diversity_budget_sensitive_successes | {_int(generated_diversity.get('budget_sensitive_successes', 0))} |",
        f"| generated_projected_without_diversity_patch_delta | {_fmt_number(_float(generated_diversity.get('projected_patch_success_delta', 0.0)))} |",
        f"| generated_dedupe_affected_cases | {_int(generated_deduplication.get('dedupe_affected_cases', 0))} |",
        f"| generated_deduplicated_candidates | {_int(generated_deduplication.get('total_deduplicated_candidates', 0))} |",
        f"| generated_duplicate_pressure | {_fmt_number(_float(generated_deduplication.get('average_duplicate_pressure', 0.0)))} |",
        f"| generated_reflection_success_cases | {_int(expansion.get('generated_benchmark_reflection_success_cases', 0))} |",
        f"| generated_reflection_candidates | {_int(expansion.get('generated_benchmark_reflection_candidates', 0))} |",
        f"| generated_reflection_candidate_success_rate | {_fmt_number(_float(expansion.get('generated_benchmark_reflection_candidate_success_rate', 0.0)))} |",
        f"| generated_slice_grounding_target_cases | {_int(generated_summary.get('slice_grounding_target_cases', 0))} |",
        f"| generated_slice_grounding_evidence_cases | {_int(generated_summary.get('slice_grounding_evidence_cases', 0))} |",
        f"| generated_slice_grounding_evidence_rate | {_fmt_number(_float(generated_summary.get('slice_grounding_evidence_rate', 0.0)))} |",
        f"| generated_provenance_selected_cases | {_int(generated_summary.get('provenance_selected_cases', 0))} |",
        f"| generated_average_provenance_bonus | {_fmt_number(_float(generated_summary.get('average_provenance_bonus', 0.0)))} |",
        f"| generated_average_provenance_stable_ref | {_fmt_number(_float(generated_summary.get('average_provenance_stable_ref', 0.0)))} |",
        f"| ablation_linked_generated_cases | {_int(ablation_links.get('linked_cases', 0))} |",
        "",
        "## Algorithm Stack",
        "",
        "| Phase | Evidence |",
        "| --- | --- |",
    ]
    for row in _list(report.get("phase_readiness")):
        if not isinstance(row, dict):
            continue
        lines.append(
            "| "
            f"{_markdown_cell(row.get('phase', ''))} | "
            f"{_markdown_cell(row.get('status', ''))}: "
            f"{_markdown_cell('; '.join(_list(row.get('evidence'))))} |"
        )
    lines.extend(
        [
            "",
            "## Representative Case Traces",
            "",
            "| Case | Top Function | Signals | Search | Patch Evidence |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in _list(report.get("case_evidence_traces"))[:5]:
        if not isinstance(row, dict):
            continue
        top = _dict(row.get("top_localization"))
        signals = _dict(top.get("signals"))
        search_trace = _dict(row.get("search_trace"))
        patch = _dict(row.get("patch_trace"))
        candidate = _dict(row.get("best_candidate"))
        lines.append(
            "| "
            f"{_markdown_cell(row.get('case', ''))} | "
            f"{_markdown_cell(top.get('function', ''))} | "
            f"{_markdown_cell(_trace_signal_summary(signals))} | "
            f"{_markdown_cell(_trace_search_summary(search_trace))} | "
            f"{_markdown_cell(_trace_patch_summary(patch, candidate))} |"
        )
    lines.extend(
        [
            "",
            "## Confidence Calibration",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| localization_cases | {_int(calibration.get('localization_case_count', 0))} |",
            f"| top1_accuracy | {_fmt_number(_float(calibration.get('localization_top1_accuracy', 0.0)))} |",
            f"| average_confidence | {_fmt_number(_float(calibration.get('localization_average_confidence', 0.0)))} |",
            f"| brier_score | {_fmt_number(_float(calibration.get('localization_brier_score', 0.0)))} |",
            f"| expected_calibration_error | {_fmt_number(_float(calibration.get('localization_expected_calibration_error', 0.0)))} |",
            f"| calibrated_average_confidence | {_fmt_number(_float(calibration.get('localization_calibrated_average_confidence', 0.0)))} |",
            f"| calibrated_brier_score | {_fmt_number(_float(calibration.get('localization_calibrated_brier_score', 0.0)))} |",
            f"| calibrated_expected_calibration_error | {_fmt_number(_float(calibration.get('localization_calibrated_expected_calibration_error', 0.0)))} |",
            f"| brier_score_improvement | {_fmt_number(_float(calibration.get('localization_brier_score_improvement', 0.0)))} |",
            f"| expected_calibration_error_improvement | {_fmt_number(_float(calibration.get('localization_expected_calibration_error_improvement', 0.0)))} |",
            f"| mean_absolute_error | {_fmt_number(_float(calibration.get('localization_mean_absolute_error', 0.0)))} |",
            f"| stratified_groups | {_int(calibration.get('localization_stratified_group_count', 0))} |",
            f"| max_group_calibrated_ece | {_fmt_number(_float(calibration.get('localization_max_group_calibrated_ece', 0.0)))} |",
            f"| worst_calibrated_group | {_markdown_cell(calibration.get('localization_worst_calibrated_group', ''))} |",
            f"| source_holdout_splits | {_int(calibration.get('localization_source_holdout_split_count', 0))} |",
            f"| max_holdout_calibrated_ece | {_fmt_number(_float(calibration.get('localization_max_holdout_calibrated_ece', 0.0)))} |",
            f"| worst_holdout_group | {_markdown_cell(calibration.get('localization_worst_holdout_group', ''))} |",
            "",
            "## Generated Hard-Case Evidence",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| generated_cases | {_int(generated_summary.get('generated_cases', 0))} |",
            f"| target_signals | {_int(generated_summary.get('target_signals', 0))} |",
            f"| score_inversions | {_int(generated_summary.get('score_inversions', 0))} |",
            f"| candidate_competition_cases | {_int(generated_summary.get('candidate_competition_cases', 0))} |",
            f"| expected_candidate_deduplication_cases | {_int(generated_summary.get('expected_candidate_deduplication_cases', 0))} |",
            f"| candidate_deduplication_evidence_cases | {_int(generated_summary.get('candidate_deduplication_evidence_cases', 0))} |",
            f"| deduplicated_candidates | {_int(generated_summary.get('deduplicated_candidates', 0))} |",
            f"| average_candidate_deduplication_pressure | {_fmt_number(_float(generated_summary.get('average_candidate_deduplication_pressure', 0.0)))} |",
            f"| average_failure_pressure | {_fmt_number(_float(generated_summary.get('average_failure_pressure', 0.0)))} |",
            "",
            "## Ablation-Linked Hard Cases",
            "",
            "| Signal | Linked Cases | Component | Variants | Strongest Delta |",
            "| --- | ---: | --- | --- | --- |",
        ]
    )
    for row in _list(ablation_links.get("by_signal"))[:6]:
        if not isinstance(row, dict):
            continue
        lines.append(
            "| "
            f"{_markdown_cell(row.get('signal', ''))} | "
            f"{_int(row.get('linked_cases', 0))} | "
            f"{_markdown_cell(_join_items(row.get('components')))} | "
            f"{_markdown_cell(_join_items(row.get('variants')))} | "
            f"{_markdown_cell(row.get('strongest_delta_metric', ''))}="
            f"{_fmt_number(row.get('strongest_delta', 0.0))} |"
        )
    lines.extend(
        [
            "",
            "| Component | Linked Cases | Regressions | Signals | Strongest Delta |",
            "| --- | ---: | ---: | --- | --- |",
        ]
    )
    for row in _list(ablation_links.get("by_component"))[:6]:
        if not isinstance(row, dict):
            continue
        lines.append(
            "| "
            f"{_markdown_cell(row.get('component', ''))} | "
            f"{_int(row.get('linked_cases', 0))} | "
            f"{_int(row.get('regression_linked_cases', 0))} | "
            f"{_markdown_cell(_join_items(row.get('target_signals')))} | "
            f"{_markdown_cell(row.get('strongest_delta_metric', ''))}="
            f"{_fmt_number(row.get('strongest_delta', 0.0))} |"
        )
    lines.extend(
        [
            "",
            "| Case | Target Signal | Link Type | Component | Variant | Main Delta | Evidence |",
            "| --- | --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for row in _list(report.get("generated_hard_case_ablation_links"))[:6]:
        if not isinstance(row, dict):
            continue
        lines.append(
            "| "
            f"{_markdown_cell(row.get('case', ''))} | "
            f"{_markdown_cell(row.get('target_signal', ''))} | "
            f"{_markdown_cell(row.get('link_type', ''))} | "
            f"{_markdown_cell(row.get('component', ''))} | "
            f"{_markdown_cell(row.get('variant', ''))} | "
            f"{_markdown_cell(row.get('main_delta_metric', ''))}="
            f"{_fmt_number(row.get('main_delta', 0.0))} | "
            f"{_markdown_cell(_generated_ablation_link_evidence(row))} |"
        )
    lines.extend(["", "## Resume Bullets", ""])
    for bullet in _list(report.get("resume_bullets")):
        lines.append(f"- {bullet}")
    gaps = _list(report.get("gaps"))
    lines.extend(
        [
            "",
            "## Validation Snapshot",
            "",
            f"- Readiness Score: {_fmt_number(report.get('readiness_score', 0.0))}",
            f"- Quality Gate: {quality.get('status', 'missing')}",
            f"- Artifact Gaps: {len(gaps)}",
        ]
    )
    if gaps:
        for gap in gaps[:5]:
            lines.append(f"- Gap: {gap}")
    else:
        lines.append("- No major artifact gaps detected.")
    return "\n".join(lines)


def _extract_benchmark_report(payload: dict[str, Any]) -> dict[str, Any]:
    report = payload.get("benchmark_report")
    if isinstance(report, dict):
        return report
    if "summary" in payload and isinstance(payload.get("summary"), dict):
        return payload
    raise ValueError("Artifact must contain benchmark_report or summary.")


def _headline_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    return {key: summary[key] for key in KEY_METRICS if key in summary}


def _phase_readiness(
    *,
    summary: dict[str, Any],
    cases: list[Any],
    payload: dict[str, Any],
    quality_gate: dict[str, Any],
) -> list[dict[str, Any]]:
    case_count = _int(summary.get("case_count", len(cases)))
    expected_rule_recall = _float(summary.get("expected_rule_recall", 0.0))
    expected_rule_precision = _float(summary.get("expected_rule_precision", 0.0))
    top1 = _float(summary.get("top1", 0.0))
    top3 = _float(summary.get("top3", 0.0))
    patch_success = _float(summary.get("patch_success_rate", 0.0))
    beam_success = _float(summary.get("beam_success_rate", 0.0))
    evaluation_signals = [
        bool(payload.get("ablation_results")),
        bool(payload.get("weight_search_results")),
        bool(payload.get("patch_weight_search_results")),
        bool(summary.get("difficulty_report")),
        bool(summary.get("generalization_report")),
        bool(summary.get("patch_judge_reliability")),
        bool(summary.get("search_budget_analysis")),
        bool(summary.get("search_competition_analysis")),
        _has_benchmark_mining_signal(payload),
        bool(payload.get("hard_case_mining")),
        bool(payload.get("hard_case_generation")),
        bool(quality_gate),
    ]
    evaluation_signal_count = sum(1 for value in evaluation_signals if value)
    has_experiment_controls = any(
        [
            payload.get("ablation_results"),
            payload.get("weight_search_results"),
            payload.get("patch_weight_search_results"),
            quality_gate,
        ]
    )
    return [
        {
            "phase": "Phase 1",
            "name": "Static analysis and rule detection",
            "status": _status(case_count > 0 and expected_rule_recall > 0.0),
            "evidence": [
                f"cases={case_count}",
                f"rule_recall={expected_rule_recall:.4f}",
                f"rule_precision={expected_rule_precision:.4f}",
            ],
        },
        {
            "phase": "Phase 2",
            "name": "Function-level fault localization",
            "status": _status(case_count > 0 and top1 > 0.0 and top3 > 0.0),
            "evidence": [
                f"top1={top1:.4f}",
                f"top3={top3:.4f}",
                "cross_function_data_flow_cases="
                f"{_int(summary.get('cross_function_data_flow_case_count', 0))}",
            ],
        },
        {
            "phase": "Phase 3",
            "name": "Patch generation and sandbox repair loop",
            "status": _status(patch_success > 0.0, partial=beam_success > 0.0),
            "evidence": [
                f"patch_success_rate={patch_success:.4f}",
                f"beam_success_rate={beam_success:.4f}",
                "patch_search_top1_success_rate="
                f"{_float(summary.get('patch_search_top1_success_rate', 0.0)):.4f}",
            ],
        },
        {
            "phase": "Phase 4",
            "name": "Search enhancement and evaluation",
            "status": _status(
                evaluation_signal_count >= 3 and bool(has_experiment_controls),
                partial=evaluation_signal_count > 0,
            ),
            "evidence": [
                f"ablation_results={len(_list(payload.get('ablation_results')))}",
                f"quality_gate={quality_gate.get('passed', 'missing')}",
                f"evaluation_signals={evaluation_signal_count}",
                f"source_groups={_source_group_count(summary)}",
            ],
        },
    ]


def _phase_milestone_audit(
    *,
    summary: dict[str, Any],
    cases: list[Any],
    payload: dict[str, Any],
    quality_gate: dict[str, Any],
    hard_case_generation: dict[str, Any],
    hard_case_generated_benchmark: dict[str, Any],
    weight_search_results: list[Any],
    patch_weight_search_results: list[Any],
) -> list[dict[str, Any]]:
    case_count = _int(summary.get("case_count", len(cases)))
    rule_recall = _float(summary.get("expected_rule_recall", 0.0))
    rule_precision = _float(summary.get("expected_rule_precision", 0.0))
    top1 = _float(summary.get("top1", 0.0))
    top3 = _float(summary.get("top3", 0.0))
    patch_success = _float(summary.get("patch_success_rate", 0.0))
    beam_success = _float(summary.get("beam_success_rate", 0.0))
    patch_search_top1 = _float(
        summary.get("patch_search_top1_success_rate", 0.0)
    )
    search_competition = _dict(summary.get("search_competition_analysis"))
    reflection = _dict(summary.get("reflection_analysis"))
    difficulty = _dict(summary.get("difficulty_report"))
    generalization = _dict(summary.get("generalization_report"))
    generated_benchmark_report = _dict(
        hard_case_generated_benchmark.get("benchmark_report")
    )
    generated_report = _dict(generated_benchmark_report.get("summary"))
    generated_competition = _dict(
        generated_report.get("search_competition_analysis")
    )
    generated_reflection = _generated_reflection_evidence_summary(
        generated_benchmark_report
    )
    reflection_candidate_count = _int(
        reflection.get("reflection_candidate_count", 0)
    )
    generated_reflection_candidate_count = _int(
        generated_reflection.get("reflection_candidates", 0)
    )
    first_case = _first_dict(cases)
    first_localization = _first_dict(_list(first_case.get("localization_details")))
    score_signals = _dict(first_localization.get("signals"))
    score_signal_count = len([key for key, value in score_signals.items() if value is not None])

    return [
        _milestone(
            "Phase 1",
            "Repo parser and AST analyzer",
            case_count > 0,
            [
                f"cases={case_count}",
                f"parsed_cases={len(cases)}",
            ],
        ),
        _milestone(
            "Phase 1",
            "Call graph extraction",
            _int(summary.get("cross_function_data_flow_case_count", 0)) > 0,
            [
                "cross_function_data_flow_cases="
                f"{_int(summary.get('cross_function_data_flow_case_count', 0))}",
                "data_flow_evidence_cases="
                f"{_int(summary.get('data_flow_evidence_case_count', 0))}",
            ],
        ),
        _milestone(
            "Phase 1",
            "Rule-based bug detector",
            rule_recall > 0.0 and rule_precision > 0.0,
            [
                f"rule_recall={rule_recall:.4f}",
                f"rule_precision={rule_precision:.4f}",
            ],
        ),
        _milestone(
            "Phase 2",
            "Program graph evidence",
            _int(summary.get("data_flow_evidence_case_count", 0)) > 0,
            [
                "data_flow_evidence_cases="
                f"{_int(summary.get('data_flow_evidence_case_count', 0))}",
                "average_top1_data_dependency="
                f"{_float(summary.get('average_top1_data_dependency', 0.0)):.4f}",
            ],
        ),
        _milestone(
            "Phase 2",
            "Static/Graph/FinalScore suspicious ranking",
            top1 > 0.0 and top3 > 0.0 and score_signal_count >= 3,
            [
                f"top1={top1:.4f}",
                f"top3={top3:.4f}",
                f"score_signal_count={score_signal_count}",
            ],
        ),
        _milestone(
            "Phase 2",
            "Weight search and Top-k evaluation",
            bool(weight_search_results),
            [
                f"weight_profiles={len(weight_search_results)}",
                f"map={_float(summary.get('map', 0.0)):.4f}",
                f"mrr={_float(summary.get('mrr', 0.0)):.4f}",
            ],
        ),
        _milestone(
            "Phase 3",
            "Patch generation and sandbox pytest validation",
            patch_success > 0.0,
            [
                f"patch_success_rate={patch_success:.4f}",
                "average_patch_candidates="
                f"{_float(summary.get('average_patch_candidates', 0.0)):.4f}",
            ],
        ),
        _milestone(
            "Phase 3",
            "Patch search and reflection loop",
            beam_success > 0.0
            and patch_search_top1 > 0.0
            and (
                reflection_candidate_count > 0
                or generated_reflection_candidate_count > 0
            ),
            [
                f"beam_success_rate={beam_success:.4f}",
                f"patch_search_top1_success_rate={patch_search_top1:.4f}",
                "average_repair_rounds="
                f"{_float(summary.get('average_repair_rounds', 0.0)):.4f}",
                f"reflection_candidates={reflection_candidate_count}",
                "reflection_candidate_success_rate="
                f"{_float(reflection.get('reflection_candidate_success_rate', 0.0)):.4f}",
                "generated_reflection_candidates="
                f"{generated_reflection_candidate_count}",
                "generated_reflection_success_cases="
                f"{_int(generated_reflection.get('reflection_success_cases', 0))}",
                "generated_reflection_candidate_success_rate="
                f"{_float(generated_reflection.get('reflection_candidate_success_rate', 0.0)):.4f}",
            ],
        ),
        _milestone(
            "Phase 3",
            "PatchScore / patch-risk evaluation",
            bool(patch_weight_search_results),
            [
                f"patch_weight_profiles={len(patch_weight_search_results)}",
                "average_patch_risk="
                f"{_float(summary.get('average_patch_risk', 0.0)):.4f}",
            ],
        ),
        _milestone(
            "Phase 4",
            "Beam search competition analysis",
            _int(search_competition.get("multi_candidate_case_count", 0)) > 0,
            [
                "multi_candidate_cases="
                f"{_int(search_competition.get('multi_candidate_case_count', 0))}",
                "failure_pressure="
                f"{_float(search_competition.get('average_failure_pressure', 0.0)):.4f}",
                "diversity_assisted_successes="
                f"{_int(search_competition.get('diversity_assisted_success_count', 0))}",
            ],
        ),
        _milestone(
            "Phase 4",
            "Benchmark difficulty and generalization reports",
            bool(difficulty) and bool(generalization),
            [
                f"difficulty_cases={_int(difficulty.get('case_count', 0))}",
                f"source_groups={_int(generalization.get('source_group_count', 0))}",
            ],
        ),
        _milestone(
            "Phase 4",
            "Ablation study and quality gate",
            bool(payload.get("ablation_results")) and bool(quality_gate),
            [
                f"ablation_results={len(_list(payload.get('ablation_results')))}",
                f"quality_gate={quality_gate.get('passed', 'missing')}",
            ],
        ),
        _milestone(
            "Phase 4",
            "Hard-case generation and generated benchmark",
            _int(hard_case_generation.get("generated_count", 0)) > 0
            and _int(generated_report.get("case_count", 0)) > 0,
            [
                f"generated_cases={_int(hard_case_generation.get('generated_count', 0))}",
                "generated_score_inversions="
                f"{_int(generated_competition.get('score_inversion_count', 0))}",
            ],
        ),
    ]


def _milestone(
    phase: str,
    milestone: str,
    ready: bool,
    evidence: list[str],
) -> dict[str, Any]:
    return {
        "phase": phase,
        "milestone": milestone,
        "status": "ready" if ready else "missing",
        "evidence": evidence,
    }


def _case_evidence_traces(cases: list[Any]) -> list[dict[str, Any]]:
    traces = [
        _case_evidence_trace(case)
        for case in cases
        if isinstance(case, dict)
    ]
    traces.sort(
        key=lambda trace: (
            _float(trace.get("interest_score", 0.0)),
            str(trace.get("case", "")),
        ),
        reverse=True,
    )
    return traces


def _case_evidence_trace(case: dict[str, Any]) -> dict[str, Any]:
    top_localization = _top_localization_trace(
        _first_dict(_list(case.get("localization_details")))
    )
    search_trace = _search_trace(case)
    patch_trace = _patch_trace(case)
    best_candidate = _best_candidate_trace(case)
    metadata = _dict(case.get("metadata"))
    trace = {
        "case": str(case.get("case_name", "")),
        "source_group": str(
            metadata.get("upstream")
            or metadata.get("source")
            or metadata.get("source_group")
            or ""
        ),
        "bug_type": str(case.get("bug_type") or metadata.get("bug_type") or ""),
        "expected_rules": [str(item) for item in _list(case.get("expected_rule_ids"))],
        "ground_truth": [str(item) for item in _list(case.get("ground_truth"))],
        "top1_hit": bool(case.get("top1_hit", False)),
        "top3_hit": bool(case.get("top3_hit", False)),
        "top_localization": top_localization,
        "search_trace": search_trace,
        "patch_trace": patch_trace,
        "best_candidate": best_candidate,
    }
    trace["interest_score"] = _case_trace_interest_score(trace)
    return trace


def _top_localization_trace(detail: dict[str, Any]) -> dict[str, Any]:
    signals = _dict(detail.get("signals"))
    graph = _dict(detail.get("graph_components"))
    data_flow = _dict(detail.get("data_flow_evidence"))
    program_slice = _dict(detail.get("program_slice"))
    slice_grounding = _dict(detail.get("slice_grounding"))
    return {
        "rank": _int(detail.get("rank", 0)),
        "function": str(
            detail.get("function_name")
            or detail.get("function")
            or ""
        ),
        "score": round(_float(detail.get("score", 0.0)), 4),
        "signals": {
            "sbfl": round(_float(signals.get("sbfl", detail.get("ochiai", 0.0))), 4),
            "static": round(_float(signals.get("static", 0.0)), 4),
            "graph": round(_float(signals.get("graph", 0.0)), 4),
            "semantic": round(_float(signals.get("semantic", 0.0)), 4),
            "llm": round(_float(signals.get("llm", 0.0)), 4),
            "risk": round(
                _float(signals.get("patch_risk", signals.get("risk", 0.0))),
                4,
            ),
        },
        "graph_evidence": {
            "traceback_hit": round(_float(graph.get("traceback_hit", 0.0)), 4),
            "test_coverage": round(_float(graph.get("test_coverage", 0.0)), 4),
            "data_dependency": round(_float(graph.get("data_dependency", 0.0)), 4),
            "control_flow": round(_float(graph.get("control_flow", 0.0)), 4),
            "pagerank": round(_float(graph.get("pagerank", 0.0)), 4),
            "caller_impact": round(_float(graph.get("caller_impact", 0.0)), 4),
            "module_dependency": round(
                _float(graph.get("module_dependency", 0.0)),
                4,
            ),
            "async_call": round(_float(graph.get("async_call", 0.0)), 4),
            "patch_risk": round(_float(graph.get("patch_risk", 0.0)), 4),
            "data_flow_edges": _int(data_flow.get("total_edges", 0)),
            "key_flow_edges": _int(data_flow.get("key_flow_edges", 0)),
            "cross_function_edges": _int(data_flow.get("cross_function_edges", 0)),
            "slice_edges": _int(program_slice.get("edge_count", 0)),
            "slice_data_flow_edges": _int(
                program_slice.get("data_flow_edge_count", 0)
            ),
            "slice_cfg_edges": _int(program_slice.get("cfg_edge_count", 0)),
            "slice_support": round(
                _float(slice_grounding.get("support_score", 0.0)),
                4,
            ),
            "slice_failed_test_reachability": round(
                _float(slice_grounding.get("failed_test_reachability", 0.0)),
                4,
            ),
            "slice_call_chain_coverage": round(
                _float(slice_grounding.get("call_chain_edge_coverage", 0.0)),
                4,
            ),
            "slice_failing_coverage_ratio": round(
                _float(slice_grounding.get("failing_coverage_ratio", 0.0)),
                4,
            ),
            "slice_data_flow_support": round(
                _float(slice_grounding.get("data_flow_support", 0.0)),
                4,
            ),
            "slice_control_flow_support": round(
                _float(slice_grounding.get("control_flow_support", 0.0)),
                4,
            ),
            "slice_cross_boundary_support": round(
                _float(slice_grounding.get("cross_boundary_support", 0.0)),
                4,
            ),
            "slice_evidence_dimensions": _int(
                slice_grounding.get("evidence_dimension_count", 0)
            ),
            "slice_grounded": bool(slice_grounding.get("grounded", False)),
        },
        "call_chain": [str(item) for item in _list(detail.get("call_chain"))],
        "call_chain_length": _int(detail.get("call_chain_length", 0)),
    }


def _beam_duplicate_candidate_count(beam_results: list[dict[str, Any]]) -> int:
    return sum(
        max(0, _int(item.get("search_duplicate_count", 0)))
        for item in beam_results
    )


def _max_search_duplicate_count(beam_results: list[dict[str, Any]]) -> int:
    return max(
        (max(0, _int(item.get("search_duplicate_count", 0))) for item in beam_results),
        default=0,
    )


def _search_duplicate_pressure(
    search: dict[str, Any],
    *,
    deduplicated_candidates: int,
    effective_candidate_pool: int,
) -> float:
    explicit_pressure = search.get(
        "duplicate_pressure",
        search.get("deduplication_savings_ratio"),
    )
    if explicit_pressure is not None:
        return round(_float(explicit_pressure), 4)
    return _ratio(deduplicated_candidates, effective_candidate_pool)


def _search_effective_candidate_pool(
    case: dict[str, Any],
    search: dict[str, Any],
    beam_results: list[dict[str, Any]],
    *,
    deduplicated_candidates: int,
) -> int:
    evaluated_nodes = _int(search.get("evaluated_nodes", len(beam_results)))
    return max(
        evaluated_nodes + deduplicated_candidates,
        _int(search.get("effective_candidate_pool", 0)),
        _int(case.get("patch_candidates_count", 0)),
    )


def _search_trace(case: dict[str, Any]) -> dict[str, Any]:
    search = _dict(case.get("search_analysis"))
    beam_results = [
        item for item in _list(case.get("beam_search_results"))
        if isinstance(item, dict)
    ]
    deduplicated_candidates = _int(
        search.get(
            "deduplicated_candidates",
            _beam_duplicate_candidate_count(beam_results),
        )
    )
    evaluated_nodes = _int(search.get("evaluated_nodes", len(beam_results)))
    effective_candidate_pool = _search_effective_candidate_pool(
        case,
        search,
        beam_results,
        deduplicated_candidates=deduplicated_candidates,
    )
    successful_nodes = [
        item for item in beam_results if bool(item.get("success", False))
    ]
    failed_nodes = [
        item for item in beam_results if not bool(item.get("success", False))
    ]
    return {
        "patch_candidates": _int(case.get("patch_candidates_count", 0)),
        "beam_nodes": len(beam_results),
        "successful_nodes": _int(
            search.get("successful_nodes", len(successful_nodes))
        ),
        "failed_nodes": max(
            0,
            _int(search.get("evaluated_nodes", len(beam_results)))
            - _int(search.get("successful_nodes", len(successful_nodes))),
        )
        if search
        else len(failed_nodes),
        "evaluated_nodes": evaluated_nodes,
        "first_success_rank": (
            _int(search.get("first_success_rank"))
            if search.get("first_success_rank") is not None
            else None
        ),
        "first_success_depth": (
            _int(search.get("first_success_depth"))
            if search.get("first_success_depth") is not None
            else None
        ),
        "failures_before_success": _int(
            search.get("failures_before_success", 0)
        ),
        "success_score_margin": round(
            _float(search.get("success_score_margin", 0.0)),
            4,
        ),
        "efficiency": round(_float(search.get("efficiency", 0.0)), 4),
        "score_inversion": _case_score_inversion(case),
        "deduplicated_candidates": deduplicated_candidates,
        "effective_candidate_pool": effective_candidate_pool,
        "deduplication_savings_ratio": _search_duplicate_pressure(
            search,
            deduplicated_candidates=deduplicated_candidates,
            effective_candidate_pool=effective_candidate_pool,
        ),
        "max_search_duplicate_count": _max_search_duplicate_count(beam_results),
    }


def _patch_trace(case: dict[str, Any]) -> dict[str, Any]:
    risk = _dict(case.get("best_patch_risk"))
    return {
        "success": bool(case.get("patch_success", False)),
        "multi_patch_success": bool(case.get("multi_patch_success", False)),
        "strategy": str(case.get("repair_strategy", "")),
        "best_rule": str(case.get("best_patch_rule_id", "")),
        "repair_rounds": _int(case.get("repair_rounds", 0)),
        "risk_score": round(_float(risk.get("score", case.get("best_patch_risk", 0.0))), 4),
        "diff_size": _int(risk.get("diff_size", 0)),
        "affected_callers": _int(risk.get("affected_callers", 0)),
        "cross_file_callers": _int(risk.get("cross_file_callers", 0)),
        "risk_reasons": [str(item) for item in _list(risk.get("risk_reasons"))],
    }


def _best_candidate_trace(case: dict[str, Any]) -> dict[str, Any]:
    multi_patch_results = [
        item for item in _list(case.get("multi_patch_results"))
        if isinstance(item, dict)
    ]
    if (
        bool(case.get("multi_patch_success", False))
        or str(case.get("repair_strategy", "")) == "multi_patch"
    ) and multi_patch_results:
        best_bundle = (
            _first_successful_candidate(multi_patch_results)
            or _first_dict(multi_patch_results)
        )
        return _multi_patch_candidate_trace(best_bundle)

    candidates = [
        item for item in _list(case.get("beam_search_results"))
        if isinstance(item, dict)
    ]
    if not candidates:
        candidates = [
            item for item in _list(case.get("patch_search_results"))
            if isinstance(item, dict)
        ]
    best = _first_successful_candidate(candidates) or _first_dict(candidates)
    if not best:
        return {}
    patch_judgment = _dict(best.get("patch_judgment"))
    return {
        "rank": _int(best.get("rank", 0)),
        "kind": "patch_candidate",
        "depth": _int(best.get("depth", 0)),
        "variant": str(best.get("variant", "")),
        "rule": str(best.get("rule_id", "")),
        "success": bool(best.get("success", False)),
        "score": round(_float(best.get("score", 0.0)), 4),
        "prior_score": round(_float(best.get("prior_score", 0.0)), 4),
        "diversity_rank": _int(best.get("diversity_rank", 0)),
        "diversity_bonus": round(_float(best.get("diversity_bonus", 0.0)), 4),
        "diversity_score": round(_float(best.get("diversity_score", 0.0)), 4),
        "feedback_score": round(_float(best.get("feedback_score", 0.0)), 4),
        "risk_score": round(_float(best.get("risk_score", 0.0)), 4),
        "passed": _int(best.get("passed", 0)),
        "failed": _int(best.get("failed", 0)),
        "failure_type": str(best.get("failure_type", "")),
        "retained": bool(best.get("retained", True)),
        "retention_bucket": str(best.get("retention_bucket", "")),
        "patch_judge_score": round(_float(patch_judgment.get("score", 0.0)), 4),
        "patch_judge_calibrated_score": round(
            _float(patch_judgment.get("calibrated_score", 0.0)),
            4,
        ),
    }


def _multi_patch_candidate_trace(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": _int(bundle.get("rank", 0)),
        "kind": "multi_patch_bundle",
        "depth": 0,
        "variant": "+".join(str(item) for item in _list(bundle.get("variants"))),
        "rule": "+".join(str(item) for item in _list(bundle.get("rules"))),
        "success": bool(bundle.get("success", False)),
        "score": round(_float(bundle.get("score", 0.0)), 4),
        "prior_score": 0.0,
        "feedback_score": 0.0,
        "risk_score": 0.0,
        "passed": _int(bundle.get("passed", 0)),
        "failed": _int(bundle.get("failed", 0)),
        "failure_type": "success" if bundle.get("success") else "test_failure",
        "retained": True,
        "retention_bucket": "multi_patch_bundle",
        "patch_judge_score": 0.0,
        "patch_judge_calibrated_score": 0.0,
        "bundle_size": _int(bundle.get("bundle_size", 0)),
        "functions": [str(item) for item in _list(bundle.get("functions"))],
        "cross_file": bool(bundle.get("cross_file", False)),
        "data_flow_edges": _int(bundle.get("data_flow_edges", 0)),
        "key_flow_edges": _int(bundle.get("key_flow_edges", 0)),
    }


def _first_successful_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    for candidate in candidates:
        if bool(candidate.get("success", False)):
            return candidate
    return {}


def _case_evidence_trace_summary(
    traces: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "traced_cases": len(traces),
        "top1_hits": sum(1 for trace in traces if trace.get("top1_hit")),
        "top3_hits": sum(1 for trace in traces if trace.get("top3_hit")),
        "patch_successes": sum(
            1
            for trace in traces
            if _dict(trace.get("patch_trace")).get("success")
        ),
        "score_inversions": sum(
            1
            for trace in traces
            if _dict(trace.get("search_trace")).get("score_inversion")
        ),
        "cross_function_traces": sum(
            1
            for trace in traces
            if _int(_dict(trace.get("top_localization")).get("call_chain_length", 0))
            > 1
        ),
        "data_flow_evidence_cases": sum(
            1
            for trace in traces
            if _int(
                _dict(_dict(trace.get("top_localization")).get("graph_evidence")).get(
                    "data_flow_edges",
                    0,
                )
            )
            > 0
        ),
    }


def _case_trace_interest_score(trace: dict[str, Any]) -> float:
    top = _dict(trace.get("top_localization"))
    graph = _dict(top.get("graph_evidence"))
    search = _dict(trace.get("search_trace"))
    patch = _dict(trace.get("patch_trace"))
    score = 0.0
    score += 4.0 if search.get("score_inversion") else 0.0
    score += min(3.0, _int(search.get("beam_nodes", 0)) / 2.0)
    score += min(2.0, _int(search.get("patch_candidates", 0)) / 2.0)
    score += min(2.0, _int(top.get("call_chain_length", 0)))
    score += 1.0 if _int(graph.get("cross_function_edges", 0)) > 0 else 0.0
    score += 1.0 if _int(patch.get("cross_file_callers", 0)) > 0 else 0.0
    score += 1.0 if _float(patch.get("risk_score", 0.0)) >= 0.5 else 0.0
    score += 1.0 if patch.get("multi_patch_success") else 0.0
    return round(score, 4)


def _trace_signal_summary(signals: dict[str, Any]) -> str:
    keys = ["sbfl", "static", "graph", "semantic", "llm", "risk"]
    return ", ".join(f"{key}={_float(signals.get(key, 0.0)):.4f}" for key in keys)


def _trace_graph_summary(
    graph: dict[str, Any],
    top_localization: dict[str, Any],
) -> str:
    return (
        f"call_chain={_int(top_localization.get('call_chain_length', 0))}, "
        f"trace={_float(graph.get('traceback_hit', 0.0)):.4f}, "
        f"data_flow={_int(graph.get('data_flow_edges', 0))}, "
        f"cross_fn={_int(graph.get('cross_function_edges', 0))}"
    )


def _trace_search_summary(search: dict[str, Any]) -> str:
    first_success = search.get("first_success_rank")
    first_success_text = "" if first_success is None else str(first_success)
    return (
        f"candidates={_int(search.get('patch_candidates', 0))}, "
        f"beam={_int(search.get('beam_nodes', 0))}, "
        f"first_success={first_success_text}, "
        f"failures={_int(search.get('failures_before_success', 0))}, "
        f"inversion={bool(search.get('score_inversion', False))}"
    )


def _trace_patch_summary(
    patch: dict[str, Any],
    candidate: dict[str, Any],
) -> str:
    sandbox = ""
    if candidate:
        kind = str(candidate.get("kind", "patch_candidate"))
        sandbox = (
            f", sandbox={_int(candidate.get('passed', 0))}/"
            f"{_int(candidate.get('failed', 0))}"
        )
        if kind == "multi_patch_bundle":
            sandbox += f", bundle={_int(candidate.get('bundle_size', 0))}"
    return (
        f"success={bool(patch.get('success', False))}, "
        f"strategy={patch.get('strategy', '')}, "
        f"rule={patch.get('best_rule', '')}, "
        f"risk={_float(patch.get('risk_score', 0.0)):.4f}, "
        f"candidate={candidate.get('variant', '')}{sandbox}"
    )


def _algorithm_evidence(
    *,
    summary: dict[str, Any],
    difficulty: dict[str, Any],
    generalization: dict[str, Any],
    quality_gate: dict[str, Any],
    hard_case_mining: dict[str, Any],
    benchmark_mining: dict[str, Any],
    hard_case_generation: dict[str, Any],
    hard_case_generated_benchmark: dict[str, Any],
    patch_judge_fusion: dict[str, Any],
    ablation_impact: dict[str, Any],
    weight_search_results: list[Any],
    patch_weight_search_results: list[Any],
) -> dict[str, dict[str, Any]]:
    selection_audit = _dict(hard_case_generation.get("selection_audit"))
    best_weight_search = _first_dict(weight_search_results)
    best_patch_weight_search = _first_dict(patch_weight_search_results)
    case_judge_reliability = _dict(summary.get("llm_judge_reliability"))
    patch_judge_reliability = _dict(summary.get("patch_judge_reliability"))
    localization_calibration = _dict(summary.get("localization_calibration"))
    localization_attribution = _dict(summary.get("localization_attribution"))
    stratified_calibration = [
        item
        for item in _list(localization_calibration.get("stratified_groups"))
        if isinstance(item, dict)
    ]
    stratified_dimension_counts = _dimension_counts(stratified_calibration)
    worst_calibrated_group = _worst_calibration_group(stratified_calibration)
    source_holdout_splits = [
        item
        for item in _list(localization_calibration.get("source_group_holdout_splits"))
        if isinstance(item, dict)
    ]
    worst_holdout_split = _worst_holdout_split(source_holdout_splits)
    search_budget = _dict(summary.get("search_budget_analysis"))
    search_competition = _dict(summary.get("search_competition_analysis"))
    reflection = _dict(summary.get("reflection_analysis"))
    provenance = _dict(summary.get("benchmark_provenance_audit"))
    generated_benchmark_report = _dict(
        hard_case_generated_benchmark.get("benchmark_report")
    )
    generated_summary = _dict(generated_benchmark_report.get("summary"))
    generated_competition = _dict(
        generated_summary.get("search_competition_analysis")
    )
    generated_diversity_evidence = generated_diversity_budget_evidence(
        hard_case_generated_benchmark
    )
    generated_deduplication_evidence = (
        _generated_candidate_deduplication_evidence(
            generated_benchmark_report
        )
    )
    generated_reflection_evidence = _generated_reflection_evidence_summary(
        generated_benchmark_report
    )
    generated_slice_evidence = _generated_slice_grounding_evidence_summary(
        generated_benchmark_report
    )
    generated_provenance = _generated_selection_provenance_summary(
        generated_benchmark_report
    )
    calibration_ablation = _calibration_ablation_summary(ablation_impact)
    search_diversity_evidence = _search_diversity_reranking_evidence(
        ablation_impact,
        search_competition,
        generated_diversity_evidence,
    )
    success_at_budget = _dict(search_budget.get("success_at_budget"))
    reflection_success_cases = _int(
        reflection.get("reflection_success_case_count", 0)
    )
    reflection_candidates = _int(
        reflection.get("reflection_candidate_count", 0)
    )
    successful_reflection_candidates = _int(
        reflection.get("successful_reflection_candidate_count", 0)
    )
    generated_reflection_success_cases = _int(
        generated_reflection_evidence.get("reflection_success_cases", 0)
    )
    generated_reflection_candidates = _int(
        generated_reflection_evidence.get("reflection_candidates", 0)
    )
    generated_successful_reflection_candidates = _int(
        generated_reflection_evidence.get(
            "successful_reflection_candidates",
            0,
        )
    )
    combined_reflection_candidates = (
        reflection_candidates + generated_reflection_candidates
    )
    combined_successful_reflection_candidates = (
        successful_reflection_candidates
        + generated_successful_reflection_candidates
    )
    return {
        "static_graph_reasoning": {
            "data_flow_evidence_cases": _int(
                summary.get("data_flow_evidence_case_count", 0)
            ),
            "cross_function_data_flow_cases": _int(
                summary.get("cross_function_data_flow_case_count", 0)
            ),
            "average_top1_data_dependency": _float(
                summary.get("average_top1_data_dependency", 0.0)
            ),
            "program_slice_cases": _int(
                summary.get("program_slice_case_count", 0)
            ),
            "average_top1_slice_edges": _float(
                summary.get("average_top1_slice_edges", 0.0)
            ),
            "average_top1_slice_cross_function_edges": _float(
                summary.get("average_top1_slice_cross_function_edges", 0.0)
            ),
            "slice_grounded_cases": _int(
                summary.get("slice_grounded_case_count", 0)
            ),
            "average_top1_slice_support": _float(
                summary.get("average_top1_slice_support", 0.0)
            ),
            "average_top1_slice_failed_test_reachability": _float(
                summary.get("average_top1_slice_failed_test_reachability", 0.0)
            ),
            "average_top1_slice_call_chain_coverage": _float(
                summary.get("average_top1_slice_call_chain_coverage", 0.0)
            ),
        },
        "search_and_repair": {
            "beam_success_rate": _float(summary.get("beam_success_rate", 0.0)),
            "patch_search_top1_success_rate": _float(
                summary.get("patch_search_top1_success_rate", 0.0)
            ),
            "patch_search_mrr": _float(summary.get("patch_search_mrr", 0.0)),
            "search_efficiency": _float(summary.get("search_efficiency", 0.0)),
            "search_budget_auc": _float(search_budget.get("budget_auc", 0.0)),
            "search_success_at_1": _float(success_at_budget.get("1", 0.0)),
            "search_first_success_rank_p50": _float(
                search_budget.get("first_success_rank_p50", 0.0)
            ),
            "search_first_success_rank_p90": _float(
                search_budget.get("first_success_rank_p90", 0.0)
            ),
            "average_search_normalized_effort": _float(
                search_budget.get("average_normalized_effort", 0.0)
            ),
            "search_dedupe_affected_cases": _int(
                search_budget.get("dedupe_affected_case_count", 0)
            ),
            "search_total_deduplicated_candidates": _int(
                search_budget.get("total_deduplicated_candidates", 0)
            ),
            "search_max_deduplicated_candidates": _int(
                search_budget.get("max_deduplicated_candidates", 0)
            ),
            "search_average_deduplicated_candidates": _float(
                search_budget.get("average_deduplicated_candidates", 0.0)
            ),
            "search_average_duplicate_pressure": _float(
                search_budget.get("average_duplicate_pressure", 0.0)
            ),
            "generated_dedupe_affected_cases": _int(
                generated_deduplication_evidence.get(
                    "dedupe_affected_cases",
                    0,
                )
            ),
            "generated_total_deduplicated_candidates": _int(
                generated_deduplication_evidence.get(
                    "total_deduplicated_candidates",
                    0,
                )
            ),
            "generated_average_duplicate_pressure": _float(
                generated_deduplication_evidence.get(
                    "average_duplicate_pressure",
                    0.0,
                )
            ),
            "combined_total_deduplicated_candidates": (
                _int(search_budget.get("total_deduplicated_candidates", 0))
                + _int(
                    generated_deduplication_evidence.get(
                        "total_deduplicated_candidates",
                        0,
                    )
                )
            ),
            "search_competition_cases": _int(
                search_competition.get("multi_candidate_case_count", 0)
            ),
            "search_score_inversion_rate": _float(
                search_competition.get("score_inversion_rate", 0.0)
            ),
            "average_search_failure_pressure": _float(
                search_competition.get("average_failure_pressure", 0.0)
            ),
            "search_diversity_lift_case_rate": _float(
                search_competition.get("diversity_lift_case_rate", 0.0)
            ),
            "search_diversity_assisted_success_rate": _float(
                search_competition.get("diversity_assisted_success_rate", 0.0)
            ),
            "search_diversity_assisted_successes": _int(
                search_competition.get("diversity_assisted_success_count", 0)
            ),
            "search_average_success_diversity_lift": _float(
                search_competition.get("average_success_diversity_lift", 0.0)
            ),
            "search_average_success_diversity_bonus": _float(
                search_competition.get("average_success_diversity_bonus", 0.0)
            ),
            "search_multi_candidate_rule_diversity": _float(
                search_competition.get(
                    "multi_candidate_average_rule_diversity",
                    0.0,
                )
            ),
            "search_multi_candidate_failure_type_diversity": _float(
                search_competition.get(
                    "multi_candidate_average_failure_type_diversity",
                    0.0,
                )
            ),
            "search_multi_candidate_retention_bucket_diversity": _float(
                search_competition.get(
                    "multi_candidate_average_retention_bucket_diversity",
                    0.0,
                )
            ),
            "average_evaluated_nodes": _float(
                summary.get("average_evaluated_nodes", 0.0)
            ),
            "reflection_cases": _int(
                reflection.get("reflection_case_count", 0)
            ),
            "reflection_success_cases": reflection_success_cases,
            "reflection_candidates": reflection_candidates,
            "reflection_candidate_success_rate": _float(
                reflection.get("reflection_candidate_success_rate", 0.0)
            ),
            "average_success_reflection_depth": _float(
                reflection.get("average_success_reflection_depth", 0.0)
            ),
            "generated_reflection_success_cases": (
                generated_reflection_success_cases
            ),
            "generated_reflection_candidates": generated_reflection_candidates,
            "generated_successful_reflection_candidates": (
                generated_successful_reflection_candidates
            ),
            "generated_reflection_candidate_success_rate": _float(
                generated_reflection_evidence.get(
                    "reflection_candidate_success_rate",
                    0.0,
                )
            ),
            "generated_average_success_reflection_depth": _float(
                generated_reflection_evidence.get(
                    "average_success_reflection_depth",
                    0.0,
                )
            ),
            "combined_reflection_success_cases": (
                reflection_success_cases + generated_reflection_success_cases
            ),
            "combined_reflection_candidates": combined_reflection_candidates,
            "combined_successful_reflection_candidates": (
                combined_successful_reflection_candidates
            ),
            "combined_reflection_candidate_success_rate": _ratio(
                combined_successful_reflection_candidates,
                combined_reflection_candidates,
            ),
            "multi_patch_success_rate": _float(
                summary.get("multi_patch_success_rate", 0.0)
            ),
        },
        "search_diversity_reranking": search_diversity_evidence,
        "generated_search_diversity_reranking": generated_diversity_evidence,
        "generated_candidate_deduplication": generated_deduplication_evidence,
        "robustness_and_generalization": {
            "difficulty_case_count": _int(difficulty.get("case_count", 0)),
            "hard_bucket_count": _int(
                _dict(difficulty.get("bucket_counts")).get("hard", 0)
            ),
            "source_group_count": _int(generalization.get("source_group_count", 0)),
            "source_balance_entropy": _float(
                generalization.get("source_balance_entropy", 0.0)
            ),
            "source_imbalance_ratio": _float(
                generalization.get("source_imbalance_ratio", 0.0)
            ),
            "max_top1_gap": _float(generalization.get("max_top1_gap", 0.0)),
            "max_patch_success_gap": _float(
                generalization.get("max_patch_success_gap", 0.0)
            ),
            "worst_holdout_group": str(
                generalization.get("worst_holdout_group", "")
            ),
            "worst_holdout_gap_score": _float(
                generalization.get("worst_holdout_gap_score", 0.0)
            ),
            "stability_score": _float(generalization.get("stability_score", 0.0)),
            "risk_level": str(generalization.get("risk_level", "")),
        },
        "benchmark_provenance": {
            "case_count": _int(provenance.get("case_count", 0)),
            "source_group_count": _int(provenance.get("source_group_count", 0)),
            "source_ref_count": _int(provenance.get("source_ref_count", 0)),
            "source_sha256_present_count": _int(
                provenance.get("source_sha256_present_count", 0)
            ),
            "stable_ref_count": _int(provenance.get("stable_ref_count", 0)),
            "floating_ref_count": _int(provenance.get("floating_ref_count", 0)),
            "case_provenance_coverage": _float(
                provenance.get("case_provenance_coverage", 0.0)
            ),
            "source_sha256_coverage": _float(
                provenance.get("source_sha256_coverage", 0.0)
            ),
            "stable_ref_coverage": _float(
                provenance.get("stable_ref_coverage", 0.0)
            ),
            "license_coverage": _float(provenance.get("license_coverage", 0.0)),
            "materialized_mutation_coverage": _float(
                provenance.get("materialized_mutation_coverage", 0.0)
            ),
            "duplicate_signature_count": _int(
                provenance.get("duplicate_signature_count", 0)
            ),
            "max_source_group_case_share": _float(
                provenance.get("max_source_group_case_share", 0.0)
            ),
            "max_source_file_case_share": _float(
                provenance.get("max_source_file_case_share", 0.0)
            ),
            "leakage_risk_score": _float(
                provenance.get("leakage_risk_score", 0.0)
            ),
            "risk_level": str(provenance.get("risk_level", "")),
        },
        "localization_attribution": {
            "case_count": _int(localization_attribution.get("case_count", 0)),
            "attributed_case_count": _int(
                localization_attribution.get("attributed_case_count", 0)
            ),
            "attribution_coverage": _float(
                localization_attribution.get("attribution_coverage", 0.0)
            ),
            "mean_top1_margin": _float(
                localization_attribution.get("mean_top1_margin", 0.0)
            ),
            "min_top1_margin": _float(
                localization_attribution.get("min_top1_margin", 0.0)
            ),
            "fragile_top1_case_count": _int(
                localization_attribution.get("fragile_top1_case_count", 0)
            ),
            "fragile_top1_rate": _float(
                localization_attribution.get("fragile_top1_rate", 0.0)
            ),
            "counterfactual_flip_case_count": _int(
                localization_attribution.get("counterfactual_flip_case_count", 0)
            ),
            "counterfactual_flip_rate": _float(
                localization_attribution.get("counterfactual_flip_rate", 0.0)
            ),
            "primary_component_entropy": _float(
                localization_attribution.get("primary_component_entropy", 0.0)
            ),
            "average_reconstruction_error": _float(
                localization_attribution.get("average_reconstruction_error", 0.0)
            ),
        },
        "confidence_calibration": {
            "localization_calibration_model": str(
                localization_calibration.get("calibration_model", "missing")
            ),
            "localization_case_count": _int(
                localization_calibration.get("case_count", 0)
            ),
            "localization_top1_accuracy": _float(
                localization_calibration.get("top1_accuracy", 0.0)
            ),
            "localization_average_confidence": _float(
                localization_calibration.get("average_confidence", 0.0)
            ),
            "localization_brier_score": _float(
                localization_calibration.get("brier_score", 0.0)
            ),
            "localization_expected_calibration_error": _float(
                localization_calibration.get("expected_calibration_error", 0.0)
            ),
            "localization_calibrated_average_confidence": _float(
                localization_calibration.get("calibrated_average_confidence", 0.0)
            ),
            "localization_calibrated_brier_score": _float(
                localization_calibration.get("calibrated_brier_score", 0.0)
            ),
            "localization_calibrated_expected_calibration_error": _float(
                localization_calibration.get(
                    "calibrated_expected_calibration_error",
                    0.0,
                )
            ),
            "localization_brier_score_improvement": _float(
                localization_calibration.get("brier_score_improvement", 0.0)
            ),
            "localization_expected_calibration_error_improvement": _float(
                localization_calibration.get(
                    "expected_calibration_error_improvement",
                    0.0,
                )
            ),
            "localization_maximum_calibration_error": _float(
                localization_calibration.get("maximum_calibration_error", 0.0)
            ),
            "localization_mean_absolute_error": _float(
                localization_calibration.get("mean_absolute_error", 0.0)
            ),
            "localization_overconfidence_rate": _float(
                localization_calibration.get("overconfidence_rate", 0.0)
            ),
            "localization_underconfidence_rate": _float(
                localization_calibration.get("underconfidence_rate", 0.0)
            ),
            "localization_bin_count": len(
                _list(localization_calibration.get("bins"))
            ),
            "localization_calibrated_bin_count": len(
                _list(localization_calibration.get("calibrated_bins"))
            ),
            "localization_stratified_group_count": len(stratified_calibration),
            "localization_source_group_calibration_count": _int(
                stratified_dimension_counts.get("source_group", 0)
            ),
            "localization_bug_type_calibration_count": _int(
                stratified_dimension_counts.get("bug_type", 0)
            ),
            "localization_expected_rule_calibration_count": _int(
                stratified_dimension_counts.get("expected_rule", 0)
            ),
            "localization_max_group_raw_ece": _max_float(
                row.get("expected_calibration_error", 0.0)
                for row in stratified_calibration
            ),
            "localization_max_group_calibrated_ece": _max_float(
                row.get("calibrated_expected_calibration_error", 0.0)
                for row in stratified_calibration
            ),
            "localization_worst_calibrated_group": (
                f"{worst_calibrated_group.get('dimension', '')}:"
                f"{worst_calibrated_group.get('group', '')}"
                if worst_calibrated_group
                else ""
            ),
            "localization_worst_calibrated_group_cases": _int(
                worst_calibrated_group.get("case_count", 0)
            ),
            "localization_source_holdout_split_count": len(source_holdout_splits),
            "localization_min_holdout_train_cases": _min_int(
                row.get("train_case_count", 0) for row in source_holdout_splits
            ),
            "localization_max_holdout_raw_ece": _max_float(
                row.get("holdout_expected_calibration_error", 0.0)
                for row in source_holdout_splits
            ),
            "localization_max_holdout_calibrated_ece": _max_float(
                row.get("holdout_calibrated_expected_calibration_error", 0.0)
                for row in source_holdout_splits
            ),
            "localization_average_holdout_calibrated_ece": _avg_float(
                row.get("holdout_calibrated_expected_calibration_error", 0.0)
                for row in source_holdout_splits
            ),
            "localization_worst_holdout_group": str(
                worst_holdout_split.get("holdout_group", "")
            ),
            "localization_worst_holdout_group_cases": _int(
                worst_holdout_split.get("holdout_case_count", 0)
            ),
        },
        "weight_and_ablation_search": {
            "final_score_profiles": len(weight_search_results),
            "patch_score_profiles": len(patch_weight_search_results),
            "best_final_score_top1": _float(
                best_weight_search.get("top1", 0.0)
            ),
            "best_final_score_robust_score": _float(
                best_weight_search.get(
                    "robust_validation_score",
                    best_weight_search.get(
                        "validation_score",
                        0.0,
                    ),
                )
            ),
            "final_score_source_groups": _int(
                best_weight_search.get("source_group_count", 0)
            ),
            "final_score_source_group_rows": len(
                _dict(best_weight_search.get("source_groups"))
            ),
            "final_score_holdout_splits": len(
                _list(best_weight_search.get("holdout_splits"))
            ),
            "final_score_pareto_profiles": len(
                [
                    item
                    for item in weight_search_results
                    if isinstance(item, dict) and item.get("pareto_optimal")
                ]
            ),
            "best_final_score_pareto_optimal": bool(
                best_weight_search.get("pareto_optimal", False)
            ),
            "best_final_score_dominates_count": _int(
                best_weight_search.get("dominates_count", 0)
            ),
            "best_final_score_dominated_by_count": _int(
                best_weight_search.get("dominated_by_count", 0)
            ),
            "final_score_max_top1_gap": _float(
                best_weight_search.get("max_top1_gap", 0.0)
            ),
            "final_score_max_map_gap": _float(
                best_weight_search.get("max_map_gap", 0.0)
            ),
            "best_patch_score_top1_success": _float(
                best_patch_weight_search.get(
                    "top1_success",
                    best_patch_weight_search.get(
                        "top1_success_rate",
                        0.0,
                    ),
                )
            ),
            "patch_score_pareto_profiles": len(
                [
                    item
                    for item in patch_weight_search_results
                    if isinstance(item, dict) and item.get("pareto_optimal")
                ]
            ),
            "best_patch_score_pareto_optimal": bool(
                best_patch_weight_search.get("pareto_optimal", False)
            ),
            "best_patch_score_dominates_count": _int(
                best_patch_weight_search.get("dominates_count", 0)
            ),
            "best_patch_score_dominated_by_count": _int(
                best_patch_weight_search.get("dominated_by_count", 0)
            ),
            "patch_judge_fusion_status": str(
                patch_judge_fusion.get("status", "missing")
            ),
            "patch_judge_fusion_profiles": _int(
                patch_judge_fusion.get("judge_profile_count", 0)
            ),
            "patch_judge_fusion_best_weight": _float(
                patch_judge_fusion.get("best_judge_weight", 0.0)
            ),
            "patch_judge_fusion_validation_delta": _float(
                patch_judge_fusion.get("validation_delta", 0.0)
            ),
            "patch_judge_fusion_top1_delta": _float(
                patch_judge_fusion.get("top1_delta", 0.0)
            ),
            "patch_judge_fusion_mrr_delta": _float(
                patch_judge_fusion.get("mrr_delta", 0.0)
            ),
            "calibration_ablation_variant_count": _int(
                calibration_ablation.get("variant_count", 0)
            ),
            "calibration_ablation_regression_count": _int(
                calibration_ablation.get("regression_count", 0)
            ),
            "max_calibrated_ece_regression": _float(
                calibration_ablation.get("max_calibrated_ece_regression", 0.0)
            ),
            "strongest_calibration_variant": str(
                calibration_ablation.get("strongest_variant", "")
            ),
        },
        "benchmark_expansion": {
            "hard_case_suggestions": _int(hard_case_mining.get("suggestion_count", 0)),
            "judge_mining_judged_candidates": _int(
                benchmark_mining.get("judged_candidate_count", 0)
            ),
            "judge_mining_failure_clusters": _int(
                benchmark_mining.get("cluster_count", 0)
            ),
            "judge_mining_template_seeds": len(
                _list(benchmark_mining.get("template_seeds"))
            ),
            "generated_hard_cases": _int(hard_case_generation.get("generated_count", 0)),
            "generated_benchmark_cases": _int(
                generated_summary.get("case_count", 0)
            ),
            "generated_benchmark_patch_success_rate": _float(
                generated_summary.get("patch_success_rate", 0.0)
            ),
            "generated_benchmark_multi_candidate_cases": _int(
                generated_competition.get("multi_candidate_case_count", 0)
            ),
            "generated_benchmark_score_inversions": _int(
                generated_competition.get("score_inversion_count", 0)
            ),
            "generated_benchmark_score_inversion_rate": _float(
                generated_competition.get("score_inversion_rate", 0.0)
            ),
            "generated_benchmark_failure_pressure": _float(
                generated_competition.get("average_failure_pressure", 0.0)
            ),
            "generated_benchmark_dedupe_affected_cases": _int(
                generated_deduplication_evidence.get("dedupe_affected_cases", 0)
            ),
            "generated_benchmark_deduplicated_candidates": _int(
                generated_deduplication_evidence.get(
                    "total_deduplicated_candidates",
                    0,
                )
            ),
            "generated_benchmark_max_deduplicated_candidates": _int(
                generated_deduplication_evidence.get(
                    "max_deduplicated_candidates",
                    0,
                )
            ),
            "generated_benchmark_average_deduplicated_candidates": _float(
                generated_deduplication_evidence.get(
                    "average_deduplicated_candidates",
                    0.0,
                )
            ),
            "generated_benchmark_duplicate_pressure": _float(
                generated_deduplication_evidence.get(
                    "average_duplicate_pressure",
                    0.0,
                )
            ),
            "generated_benchmark_expected_deduplication_cases": _int(
                generated_deduplication_evidence.get("expected_cases", 0)
            ),
            "generated_benchmark_deduplication_evidence_cases": _int(
                generated_deduplication_evidence.get("evidence_cases", 0)
            ),
            "generated_benchmark_diversity_assisted_successes": _int(
                generated_competition.get("diversity_assisted_success_count", 0)
            ),
            "generated_benchmark_average_success_diversity_lift": _float(
                generated_competition.get("average_success_diversity_lift", 0.0)
            ),
            "generated_benchmark_average_success_diversity_bonus": _float(
                generated_competition.get("average_success_diversity_bonus", 0.0)
            ),
            "generated_benchmark_expected_reflection_depth_cases": _int(
                generated_reflection_evidence.get("expected_reflection_depth_cases", 0)
            ),
            "generated_benchmark_reflection_success_cases": _int(
                generated_reflection_evidence.get("reflection_success_cases", 0)
            ),
            "generated_benchmark_reflection_candidates": _int(
                generated_reflection_evidence.get("reflection_candidates", 0)
            ),
            "generated_benchmark_successful_reflection_candidates": _int(
                generated_reflection_evidence.get(
                    "successful_reflection_candidates",
                    0,
                )
            ),
            "generated_benchmark_reflection_candidate_success_rate": _float(
                generated_reflection_evidence.get(
                    "reflection_candidate_success_rate",
                    0.0,
                )
            ),
            "generated_benchmark_slice_grounding_target_cases": _int(
                generated_slice_evidence.get("slice_grounding_target_cases", 0)
            ),
            "generated_benchmark_slice_grounding_evidence_cases": _int(
                generated_slice_evidence.get("slice_grounding_evidence_cases", 0)
            ),
            "generated_benchmark_slice_grounding_evidence_rate": _float(
                generated_slice_evidence.get("slice_grounding_evidence_rate", 0.0)
            ),
            "selected_candidate_count": _int(
                selection_audit.get("selected_candidate_count", 0)
            ),
            "selected_rule_count": _int(selection_audit.get("selected_rule_count", 0)),
            "average_candidate_score": _float(
                selection_audit.get("average_candidate_score", 0.0)
            ),
            "generated_provenance_selected_cases": _int(
                generated_provenance.get("provenance_selected_cases", 0)
            ),
            "generated_average_provenance_bonus": _float(
                generated_provenance.get("average_provenance_bonus", 0.0)
            ),
            "generated_average_provenance_stable_ref": _float(
                generated_provenance.get("average_provenance_stable_ref", 0.0)
            ),
            "generated_average_provenance_license": _float(
                generated_provenance.get("average_provenance_license", 0.0)
            ),
            "generated_average_provenance_leakage_risk": _float(
                generated_provenance.get("average_provenance_leakage_risk", 0.0)
            ),
        },
        "judge_reliability": {
            "case_judged_count": _int(
                case_judge_reliability.get("judged_case_count", 0)
            ),
            "case_judge_brier_score": _float(
                case_judge_reliability.get("brier_score", 0.0)
            ),
            "case_judge_ece": _float(
                case_judge_reliability.get("expected_calibration_error", 0.0)
            ),
            "patch_judged_candidate_count": _int(
                patch_judge_reliability.get("judged_candidate_count", 0)
            ),
            "patch_judge_brier_score": _float(
                patch_judge_reliability.get("brier_score", 0.0)
            ),
            "patch_judge_ece": _float(
                patch_judge_reliability.get("expected_calibration_error", 0.0)
            ),
            "patch_judge_agreement_rate": _float(
                patch_judge_reliability.get("agreement_rate", 0.0)
            ),
        },
        "quality_governance": {
            "quality_gate_passed": quality_gate.get("passed", "missing"),
            "failed_check_count": len(
                [
                    check
                    for check in _list(quality_gate.get("checks"))
                    if isinstance(check, dict) and not check.get("passed", False)
                ]
            ),
        },
    }


def _generated_hard_case_breakdown(
    *,
    hard_case_generation: dict[str, Any],
    hard_case_generated_benchmark: dict[str, Any],
) -> dict[str, Any]:
    report = _dict(hard_case_generated_benchmark.get("benchmark_report"))
    cases = [case for case in _list(report.get("cases")) if isinstance(case, dict)]
    selection_audit = _dict(hard_case_generation.get("selection_audit"))
    signal_rows: dict[str, dict[str, Any]] = {}
    rule_rows: dict[str, dict[str, Any]] = {}

    for case in cases:
        metadata = _dict(case.get("metadata"))
        signal = str(metadata.get("hard_case_target_signal", "")) or "unknown"
        rules = _case_rule_ids(case)
        if not rules:
            rules = ["unknown"]
        patch_success = bool(case.get("patch_success", False))
        score_inversion = _case_score_inversion(case)
        failure_pressure = _case_failure_pressure(case)

        signal_row = signal_rows.setdefault(signal, _empty_breakdown_row(signal))
        _add_case_to_breakdown_row(
            signal_row,
            patch_success=patch_success,
            score_inversion=score_inversion,
            failure_pressure=failure_pressure,
        )
        signal_row.setdefault("rules", set()).update(rules)

        for rule in rules:
            rule_row = rule_rows.setdefault(rule, _empty_breakdown_row(rule))
            _add_case_to_breakdown_row(
                rule_row,
                patch_success=patch_success,
                score_inversion=score_inversion,
                failure_pressure=failure_pressure,
            )
            rule_row.setdefault("signals", set()).add(signal)

    by_signal = [
        _finalize_breakdown_row(
            row,
            name_key="signal",
            related_key="rules",
        )
        for row in signal_rows.values()
    ]
    by_rule = [
        _finalize_breakdown_row(
            row,
            name_key="rule",
            related_key="signals",
        )
        for row in rule_rows.values()
    ]
    by_signal.sort(
        key=lambda row: (
            row["case_count"],
            row["score_inversion_count"],
            row["signal"],
        ),
        reverse=True,
    )
    by_rule.sort(
        key=lambda row: (
            row["case_count"],
            row["score_inversion_count"],
            row["rule"],
        ),
        reverse=True,
    )
    return {
        "case_count": len(cases),
        "signal_count": len(by_signal)
        or _int(selection_audit.get("target_signal_count", 0)),
        "rule_count": len(by_rule)
        or _int(selection_audit.get("selected_rule_count", 0)),
        "score_inversion_count": sum(
            row["score_inversion_count"] for row in by_signal
        ),
        "by_signal": by_signal,
        "by_rule": by_rule,
    }


def _empty_breakdown_row(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "case_count": 0,
        "patch_success_count": 0,
        "score_inversion_count": 0,
        "failure_pressure_total": 0.0,
    }


def _add_case_to_breakdown_row(
    row: dict[str, Any],
    *,
    patch_success: bool,
    score_inversion: bool,
    failure_pressure: float,
) -> None:
    row["case_count"] += 1
    row["patch_success_count"] += 1 if patch_success else 0
    row["score_inversion_count"] += 1 if score_inversion else 0
    row["failure_pressure_total"] += failure_pressure


def _finalize_breakdown_row(
    row: dict[str, Any],
    *,
    name_key: str,
    related_key: str,
) -> dict[str, Any]:
    case_count = _int(row.get("case_count", 0))
    return {
        name_key: str(row.get("name", "")),
        "case_count": case_count,
        "patch_success_count": _int(row.get("patch_success_count", 0)),
        "patch_success_rate": _ratio(
            _int(row.get("patch_success_count", 0)),
            case_count,
        ),
        "score_inversion_count": _int(row.get("score_inversion_count", 0)),
        "average_failure_pressure": _ratio(
            _float(row.get("failure_pressure_total", 0.0)),
            case_count,
        ),
        related_key: sorted(str(item) for item in row.get(related_key, set())),
    }


def _case_rule_ids(case: dict[str, Any]) -> list[str]:
    metadata = _dict(case.get("metadata"))
    rules: list[str] = []
    for value in _list(case.get("expected_rule_ids")):
        if str(value):
            rules.append(str(value))
    for value in _list(metadata.get("composed_rules")):
        if str(value):
            rules.append(str(value))
    recipe = str(metadata.get("recipe", ""))
    if recipe:
        rules.append(recipe)
    best_rule = str(case.get("best_patch_rule_id", ""))
    if best_rule and "+" not in best_rule:
        rules.append(best_rule)
    return sorted(set(rules))


def _case_score_inversion(case: dict[str, Any]) -> bool:
    search = _dict(case.get("search_analysis"))
    first_success_rank = search.get("first_success_rank")
    try:
        return first_success_rank is not None and int(first_success_rank) > 1
    except (TypeError, ValueError):
        return False


def _case_failure_pressure(case: dict[str, Any]) -> float:
    beam_results = [item for item in _list(case.get("beam_search_results")) if isinstance(item, dict)]
    if beam_results:
        failed = sum(1 for item in beam_results if not bool(item.get("success", False)))
        return _ratio(failed, len(beam_results))
    search = _dict(case.get("search_analysis"))
    evaluated = _int(search.get("evaluated_nodes", 0))
    failed = evaluated - _int(search.get("successful_nodes", 0))
    return _ratio(failed, evaluated)


def _generated_hard_case_evidence(
    hard_case_generated_benchmark: dict[str, Any],
) -> dict[str, Any]:
    report = _dict(hard_case_generated_benchmark.get("benchmark_report"))
    summary = _dict(report.get("summary"))
    search_competition = _dict(summary.get("search_competition_analysis"))
    cases = [case for case in _list(report.get("cases")) if isinstance(case, dict)]
    traces = [_generated_hard_case_trace(case) for case in cases]
    traces.sort(
        key=lambda trace: (
            bool(_dict(trace.get("search_pressure")).get("score_inversion")),
            _float(_dict(trace.get("search_pressure")).get("failure_pressure", 0.0)),
            _int(_dict(trace.get("search_pressure")).get("beam_nodes", 0)),
            str(trace.get("case", "")),
        ),
        reverse=True,
    )
    return {
        "summary": _generated_hard_case_trace_summary(
            traces,
            search_competition=search_competition,
        ),
        "traces": traces,
    }


def _generated_slice_grounding_evidence_summary(
    generated_benchmark_report: dict[str, Any],
) -> dict[str, Any]:
    cases = [
        case
        for case in _list(generated_benchmark_report.get("cases"))
        if isinstance(case, dict)
    ]
    traces = [_generated_hard_case_trace(case) for case in cases]
    summary = _generated_hard_case_trace_summary(traces)
    return {
        "slice_grounding_target_cases": _int(
            summary.get("slice_grounding_target_cases", 0)
        ),
        "slice_grounding_evidence_cases": _int(
            summary.get("slice_grounding_evidence_cases", 0)
        ),
        "slice_grounding_evidence_rate": _float(
            summary.get("slice_grounding_evidence_rate", 0.0)
        ),
    }


def _generated_selection_provenance_summary(
    generated_benchmark_report: dict[str, Any],
) -> dict[str, Any]:
    cases = [
        case
        for case in _list(generated_benchmark_report.get("cases"))
        if isinstance(case, dict)
    ]
    rows: list[dict[str, Any]] = []
    for case in cases:
        metadata = _dict(case.get("metadata"))
        selected_candidate_id = _selected_candidate_id(metadata)
        reasons = _selection_reasons(
            metadata,
            selected_candidate_id,
            limit=20,
        )
        provenance = _selection_provenance_summary(reasons)
        if _float(provenance.get("bonus", 0.0)) > 0.0:
            rows.append(provenance)
    return {
        "provenance_selected_cases": len(rows),
        "average_provenance_bonus": _avg_metric(rows, "bonus"),
        "average_provenance_stable_ref": _avg_metric(rows, "stable_ref"),
        "average_provenance_license": _avg_metric(rows, "license"),
        "average_provenance_leakage_risk": _avg_metric(rows, "leakage_risk"),
    }


def _generated_candidate_deduplication_evidence(
    generated_benchmark_report: dict[str, Any],
) -> dict[str, Any]:
    summary = _dict(generated_benchmark_report.get("summary"))
    search_budget = _dict(summary.get("search_budget_analysis"))
    cases = [
        case
        for case in _list(generated_benchmark_report.get("cases"))
        if isinstance(case, dict)
    ]
    rows: list[dict[str, Any]] = []
    pressure_values: list[float] = []
    for case in cases:
        search = _dict(case.get("search_analysis"))
        beam_results = [
            item
            for item in _list(case.get("beam_search_results"))
            if isinstance(item, dict)
        ]
        deduplicated_candidates = _int(
            search.get(
                "deduplicated_candidates",
                _beam_duplicate_candidate_count(beam_results),
            )
        )
        effective_candidate_pool = _search_effective_candidate_pool(
            case,
            search,
            beam_results,
            deduplicated_candidates=deduplicated_candidates,
        )
        duplicate_pressure = _search_duplicate_pressure(
            search,
            deduplicated_candidates=deduplicated_candidates,
            effective_candidate_pool=effective_candidate_pool,
        )
        pressure_values.append(duplicate_pressure)
        metadata = _case_metadata(case)
        target_signals = _generated_target_signals(metadata)
        expected = _expected_candidate_deduplication(metadata, target_signals)
        if expected or deduplicated_candidates > 0 or duplicate_pressure > 0.0:
            rows.append(
                {
                    "case": str(case.get("case_name", "")),
                    "expected_candidate_deduplication": expected,
                    "target_signals": target_signals,
                    "deduplicated_candidates": deduplicated_candidates,
                    "effective_candidate_pool": effective_candidate_pool,
                    "duplicate_pressure": duplicate_pressure,
                    "max_search_duplicate_count": _max_search_duplicate_count(
                        beam_results
                    ),
                    "patch_success": bool(case.get("patch_success", False)),
                }
            )
    fallback_dedupe_affected_cases = sum(
        1 for row in rows if _int(row.get("deduplicated_candidates", 0)) > 0
    )
    fallback_total_deduplicated_candidates = sum(
        _int(row.get("deduplicated_candidates", 0)) for row in rows
    )
    fallback_max_deduplicated_candidates = max(
        (_int(row.get("deduplicated_candidates", 0)) for row in rows),
        default=0,
    )
    expected_cases = sum(
        1 for row in rows if bool(row.get("expected_candidate_deduplication", False))
    )
    dedupe_affected_cases = _int(
        search_budget.get(
            "dedupe_affected_case_count",
            fallback_dedupe_affected_cases,
        )
    )
    evidence_cases = max(fallback_dedupe_affected_cases, dedupe_affected_cases)
    total_deduplicated_candidates = _int(
        search_budget.get(
            "total_deduplicated_candidates",
            fallback_total_deduplicated_candidates,
        )
    )
    average_duplicate_pressure = _float(
        search_budget.get(
            "average_duplicate_pressure",
            _ratio(sum(pressure_values), len(pressure_values)),
        )
    )
    proof = bool(
        expected_cases > 0
        and evidence_cases > 0
        and total_deduplicated_candidates > 0
        and average_duplicate_pressure > 0.0
    )
    status = "validated" if proof else ("observed" if evidence_cases else "missing")
    return {
        "status": status,
        "expected_cases": expected_cases,
        "evidence_cases": evidence_cases,
        "dedupe_affected_cases": dedupe_affected_cases,
        "total_deduplicated_candidates": total_deduplicated_candidates,
        "max_deduplicated_candidates": _int(
            search_budget.get(
                "max_deduplicated_candidates",
                fallback_max_deduplicated_candidates,
            )
        ),
        "average_deduplicated_candidates": _float(
            search_budget.get(
                "average_deduplicated_candidates",
                _ratio(total_deduplicated_candidates, len(cases)),
            )
        ),
        "average_duplicate_pressure": average_duplicate_pressure,
        "budget_pressure_proof": proof,
        "cases": rows,
    }


def _generated_hard_case_trace(case: dict[str, Any]) -> dict[str, Any]:
    metadata = _case_metadata(case)
    base_trace = _case_evidence_trace(case)
    target_signals = _generated_target_signals(metadata)
    selected_candidate_id = _selected_candidate_id(metadata)
    selection_score = _selection_score(metadata, selected_candidate_id)
    full_selection_reasons = _selection_reasons(
        metadata,
        selected_candidate_id,
        limit=20,
    )
    selection_reasons = _selection_reasons(metadata, selected_candidate_id)
    selection_provenance = _selection_provenance_summary(full_selection_reasons)
    beam_results = [
        item for item in _list(case.get("beam_search_results"))
        if isinstance(item, dict)
    ]
    search = _dict(base_trace.get("search_trace"))
    decoy_variant = str(metadata.get("score_inversion_decoy_variant", ""))
    if not decoy_variant:
        decoy_variant = _first_failed_variant(beam_results)
    success_variant = str(metadata.get("score_inversion_success_variant", ""))
    if not success_variant:
        success_variant = str(_dict(base_trace.get("best_candidate")).get("variant", ""))
    failure_pressure = _case_failure_pressure(case)
    score_inversion = _case_score_inversion(case)
    candidate_competition = (
        _int(case.get("patch_candidates_count", 0)) > 1
        or _int(search.get("evaluated_nodes", 0)) > 1
        or len(beam_results) > 1
    )
    deduplicated_candidates = _int(search.get("deduplicated_candidates", 0))
    effective_candidate_pool = _search_effective_candidate_pool(
        case,
        search,
        beam_results,
        deduplicated_candidates=deduplicated_candidates,
    )
    duplicate_pressure = _search_duplicate_pressure(
        search,
        deduplicated_candidates=deduplicated_candidates,
        effective_candidate_pool=effective_candidate_pool,
    )
    patch = _dict(base_trace.get("patch_trace"))
    candidate = _dict(base_trace.get("best_candidate"))
    slice_pressure = _generated_slice_grounding_pressure(
        base_trace,
        target_signals,
    )
    reflection_pressure = _generated_reflection_pressure(
        case,
        target_signals,
    )
    return {
        "case": str(case.get("case_name", "")),
        "target_signals": target_signals,
        "generation_strategy": str(
            metadata.get("hard_case_generation_strategy", "")
        ),
        "generation_source": str(
            metadata.get("hard_case_generation_source", "")
        ),
        "priority": str(metadata.get("hard_case_generation_priority", "")),
        "focus": str(metadata.get("hard_case_generation_focus", "")),
        "selection_policy": str(metadata.get("hard_case_selection_policy", "")),
        "selected_candidate_id": selected_candidate_id,
        "selection_score": selection_score,
        "selection_reasons": selection_reasons,
        "selection_provenance": selection_provenance,
        "expected_rules": [str(item) for item in _list(case.get("expected_rule_ids"))],
        "search_pressure": {
            "patch_candidates": _int(case.get("patch_candidates_count", 0)),
            "beam_nodes": _int(search.get("beam_nodes", len(beam_results))),
            "evaluated_nodes": _int(search.get("evaluated_nodes", len(beam_results))),
            "first_success_rank": search.get("first_success_rank"),
            "failures_before_success": _int(
                search.get("failures_before_success", 0)
            ),
            "failure_pressure": failure_pressure,
            "score_inversion": score_inversion,
            "expected_score_inversion": _expected_score_inversion(
                metadata,
                target_signals,
            ),
            "expected_diversity_reranking": _expected_diversity_reranking(
                metadata,
                target_signals,
            ),
            "expected_candidate_deduplication": (
                _expected_candidate_deduplication(
                    metadata,
                    target_signals,
                )
            ),
            "deduplicated_candidates": deduplicated_candidates,
            "effective_candidate_pool": effective_candidate_pool,
            "duplicate_pressure": duplicate_pressure,
            "deduplication_savings_ratio": duplicate_pressure,
            "max_search_duplicate_count": _max_search_duplicate_count(
                beam_results
            ),
            "candidate_competition": candidate_competition,
            "success_score_margin": _float(search.get("success_score_margin", 0.0)),
        },
        "slice_grounding_pressure": slice_pressure,
        "reflection_pressure": reflection_pressure,
        "decoy_variant": decoy_variant,
        "success_variant": success_variant,
        "patch_evidence": {
            "patch_success": bool(patch.get("success", False)),
            "multi_patch_success": bool(patch.get("multi_patch_success", False)),
            "strategy": str(patch.get("strategy", "")),
            "best_rule": str(patch.get("best_rule", "")),
            "best_candidate_kind": str(candidate.get("kind", "")),
            "best_candidate_variant": str(candidate.get("variant", "")),
            "sandbox_passed": _int(candidate.get("passed", 0)),
            "sandbox_failed": _int(candidate.get("failed", 0)),
            "risk_score": _float(patch.get("risk_score", 0.0)),
            "bundle_size": _int(candidate.get("bundle_size", 0)),
        },
        "top_localization": base_trace.get("top_localization", {}),
    }


def _generated_hard_case_trace_summary(
    traces: list[dict[str, Any]],
    *,
    search_competition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    search_competition = search_competition or {}
    target_signals = {
        signal
        for trace in traces
        for signal in _list(trace.get("target_signals"))
        if signal
    }
    failure_pressures = [
        _float(_dict(trace.get("search_pressure")).get("failure_pressure", 0.0))
        for trace in traces
    ]
    dedupe_rows = [
        _dict(trace.get("search_pressure"))
        for trace in traces
        if _dict(trace.get("search_pressure")).get(
            "expected_candidate_deduplication",
            False,
        )
        or _int(
            _dict(trace.get("search_pressure")).get(
                "deduplicated_candidates",
                0,
            )
        )
        > 0
        or _float(
            _dict(trace.get("search_pressure")).get("duplicate_pressure", 0.0)
        )
        > 0.0
    ]
    dedupe_evidence_cases = sum(
        1
        for row in dedupe_rows
        if _int(row.get("deduplicated_candidates", 0)) > 0
        or _float(row.get("duplicate_pressure", 0.0)) > 0.0
    )
    slice_pressures = [
        _float(_dict(trace.get("slice_grounding_pressure")).get("pressure", 0.0))
        for trace in traces
    ]
    slice_rows = [
        _dict(trace.get("slice_grounding_pressure"))
        for trace in traces
        if _dict(trace.get("slice_grounding_pressure")).get(
            "weak_slice_target",
            False,
        )
    ]
    slice_evidence_cases = sum(
        1 for row in slice_rows if bool(row.get("execution_evidence", False))
    )
    reflection_rows = [
        _dict(trace.get("reflection_pressure"))
        for trace in traces
        if _dict(trace.get("reflection_pressure")).get(
            "expected_reflection_depth",
            False,
        )
        or _int(
            _dict(trace.get("reflection_pressure")).get(
                "reflection_candidate_count",
                0,
            )
        )
        > 0
    ]
    reflection_candidates = sum(
        _int(row.get("reflection_candidate_count", 0))
        for row in reflection_rows
    )
    successful_reflection_candidates = sum(
        _int(row.get("successful_reflection_candidate_count", 0))
        for row in reflection_rows
    )
    success_depths = [
        _float(row.get("first_success_reflection_depth", 0.0))
        for row in reflection_rows
        if row.get("first_success_reflection_depth") is not None
    ]
    provenance_rows = [
        _dict(trace.get("selection_provenance"))
        for trace in traces
        if _float(_dict(trace.get("selection_provenance")).get("bonus", 0.0)) > 0.0
    ]
    return {
        "generated_cases": len(traces),
        "target_signals": len(target_signals),
        "patch_successes": sum(
            1
            for trace in traces
            if _dict(trace.get("patch_evidence")).get("patch_success")
        ),
        "score_inversions": sum(
            1
            for trace in traces
            if _dict(trace.get("search_pressure")).get("score_inversion")
        ),
        "expected_score_inversions": sum(
            1
            for trace in traces
            if _dict(trace.get("search_pressure")).get("expected_score_inversion")
        ),
        "expected_diversity_reranking_cases": sum(
            1
            for trace in traces
            if _dict(trace.get("search_pressure")).get(
                "expected_diversity_reranking"
            )
        ),
        "expected_reflection_depth_cases": sum(
            1
            for row in reflection_rows
            if row.get("expected_reflection_depth")
        ),
        "reflection_candidate_cases": sum(
            1
            for row in reflection_rows
            if _int(row.get("reflection_candidate_count", 0)) > 0
        ),
        "reflection_success_cases": sum(
            1 for row in reflection_rows if row.get("reflection_success")
        ),
        "reflection_candidates": reflection_candidates,
        "successful_reflection_candidates": successful_reflection_candidates,
        "reflection_candidate_success_rate": _ratio(
            successful_reflection_candidates,
            reflection_candidates,
        ),
        "average_success_reflection_depth": _ratio(
            sum(success_depths),
            len(success_depths),
        ),
        "diversity_assisted_successes": _int(
            search_competition.get("diversity_assisted_success_count", 0)
        ),
        "average_success_diversity_lift": _float(
            search_competition.get("average_success_diversity_lift", 0.0)
        ),
        "average_success_diversity_bonus": _float(
            search_competition.get("average_success_diversity_bonus", 0.0)
        ),
        "candidate_competition_cases": sum(
            1
            for trace in traces
            if _dict(trace.get("search_pressure")).get("candidate_competition")
        ),
        "expected_candidate_deduplication_cases": sum(
            1
            for row in dedupe_rows
            if row.get("expected_candidate_deduplication")
        ),
        "candidate_deduplication_evidence_cases": dedupe_evidence_cases,
        "deduplicated_candidates": sum(
            _int(row.get("deduplicated_candidates", 0)) for row in dedupe_rows
        ),
        "max_deduplicated_candidates": max(
            (_int(row.get("deduplicated_candidates", 0)) for row in dedupe_rows),
            default=0,
        ),
        "average_candidate_deduplication_pressure": _ratio(
            sum(_float(row.get("duplicate_pressure", 0.0)) for row in dedupe_rows),
            len(dedupe_rows),
        ),
        "failure_pressure_cases": sum(
            1 for value in failure_pressures if value >= 0.5
        ),
        "average_failure_pressure": _ratio(
            sum(failure_pressures),
            len(failure_pressures),
        ),
        "slice_grounding_pressure_cases": sum(
            1 for value in slice_pressures if value >= 0.5
        ),
        "average_slice_grounding_pressure": _ratio(
            sum(slice_pressures),
            len(slice_pressures),
        ),
        "slice_grounding_target_cases": len(slice_rows),
        "slice_grounding_evidence_cases": slice_evidence_cases,
        "slice_grounding_evidence_rate": _ratio(
            slice_evidence_cases,
            len(slice_rows),
        ),
        "average_slice_grounding_target_pressure": _ratio(
            sum(_float(row.get("pressure", 0.0)) for row in slice_rows),
            len(slice_rows),
        ),
        "provenance_selected_cases": len(provenance_rows),
        "average_provenance_bonus": _avg_metric(provenance_rows, "bonus"),
        "average_provenance_case_coverage": _avg_metric(
            provenance_rows,
            "case_provenance",
        ),
        "average_provenance_source_sha256": _avg_metric(
            provenance_rows,
            "source_sha256",
        ),
        "average_provenance_stable_ref": _avg_metric(
            provenance_rows,
            "stable_ref",
        ),
        "average_provenance_license": _avg_metric(provenance_rows, "license"),
        "average_provenance_leakage_risk": _avg_metric(
            provenance_rows,
            "leakage_risk",
        ),
    }


def _generated_target_signals(metadata: dict[str, Any]) -> list[str]:
    values = [
        *_list(metadata.get("hard_case_target_signals")),
        *_list(metadata.get("target_benchmark_signals")),
    ]
    if metadata.get("hard_case_target_signal"):
        values.append(metadata.get("hard_case_target_signal"))
    return sorted({str(value) for value in values if str(value)})


def _case_metadata(case: dict[str, Any]) -> dict[str, Any]:
    metadata = _dict(case.get("metadata"))
    benchmark_metadata = _dict(_dict(case.get("benchmark")).get("metadata"))
    return {**benchmark_metadata, **metadata}


def _selected_candidate_id(metadata: dict[str, Any]) -> str:
    source_candidate = str(metadata.get("source_candidate_id", ""))
    if source_candidate:
        return source_candidate
    selected = _list(metadata.get("hard_case_selected_candidate_ids"))
    return str(selected[0]) if selected else ""


def _selection_score(metadata: dict[str, Any], candidate_id: str) -> float:
    scores = _dict(metadata.get("hard_case_selected_candidate_scores"))
    if candidate_id and candidate_id in scores:
        return _float(scores.get(candidate_id))
    for value in scores.values():
        return _float(value)
    return 0.0


def _selection_reasons(
    metadata: dict[str, Any],
    candidate_id: str,
    *,
    limit: int = 4,
) -> list[str]:
    reasons = _dict(metadata.get("hard_case_selected_candidate_reasons"))
    values: Any = reasons.get(candidate_id) if candidate_id else None
    if values is None and reasons:
        values = next(iter(reasons.values()))
    if limit <= 0:
        return [str(item) for item in _list(values)]
    selected: list[str] = []
    for item in _list(values):
        text = str(item)
        if len(selected) < limit or text.startswith("provenance_"):
            selected.append(text)
    return selected


def _selection_provenance_summary(reasons: list[str]) -> dict[str, Any]:
    metrics: dict[str, float] = {}
    for reason in reasons:
        if reason.startswith("provenance_bonus="):
            metrics["bonus"] = _float(reason.split("=", 1)[1])
            continue
        if reason.startswith("provenance_metrics="):
            payload = reason.split("=", 1)[1]
            for item in payload.split(";"):
                if "=" not in item:
                    continue
                key, value = item.split("=", 1)
                metrics[key.strip()] = _float(value)
    return {
        "bonus": round(metrics.get("bonus", 0.0), 4),
        "case_provenance": round(metrics.get("case_provenance", 0.0), 4),
        "source_sha256": round(metrics.get("source_sha256", 0.0), 4),
        "stable_ref": round(metrics.get("stable_ref", 0.0), 4),
        "license": round(metrics.get("license", 0.0), 4),
        "leakage_risk": round(metrics.get("leakage_risk", 0.0), 4),
    }


def _expected_score_inversion(
    metadata: dict[str, Any],
    target_signals: list[str],
) -> bool:
    return bool(
        metadata.get("expected_score_inversion")
        or metadata.get("search_score_inversion_profile")
        or "search_score_inversion" in target_signals
    )


def _expected_diversity_reranking(
    metadata: dict[str, Any],
    target_signals: list[str],
) -> bool:
    return bool(
        metadata.get("expected_diversity_reranking")
        or metadata.get("expected_diversity_assisted_success")
        or metadata.get("search_diversity_profile")
        or "search_diversity_reranking" in target_signals
    )


def _expected_candidate_deduplication(
    metadata: dict[str, Any],
    target_signals: list[str],
) -> bool:
    return bool(
        metadata.get("expected_candidate_deduplication")
        or metadata.get("patch_score_profile") == "candidate_deduplication_probe"
        or metadata.get("search_pressure") == "candidate_deduplication_probe"
        or metadata.get("hard_case_target_signal")
        == "without_candidate_deduplication"
        or "without_candidate_deduplication" in target_signals
        or "candidate_deduplication_pressure" in target_signals
    )


def _expected_reflection_depth(
    metadata: dict[str, Any],
    target_signals: list[str],
) -> bool:
    return bool(
        metadata.get("expected_reflection_depth")
        or metadata.get("patch_score_profile") == "reflection_depth_probe"
        or metadata.get("search_pressure") == "reflection_depth_probe"
        or "reflection_depth" in target_signals
    )


def _generated_reflection_evidence_summary(
    generated_benchmark_report: dict[str, Any],
) -> dict[str, Any]:
    summary = _dict(generated_benchmark_report.get("summary"))
    cases = [
        case
        for case in _list(generated_benchmark_report.get("cases"))
        if isinstance(case, dict)
    ]
    rows = [
        _generated_reflection_pressure(
            case,
            _generated_target_signals(_case_metadata(case)),
        )
        for case in cases
    ]
    fallback = _generated_reflection_summary_from_rows(rows)
    reflection = _dict(summary.get("reflection_analysis"))
    if not reflection:
        return fallback
    candidate_count = _int(reflection.get("reflection_candidate_count", 0))
    success_cases = _int(reflection.get("reflection_success_case_count", 0))
    successful_candidates = _int(
        reflection.get("successful_reflection_candidate_count", 0)
    )
    return {
        **fallback,
        "reflection_success_cases": success_cases,
        "reflection_candidates": candidate_count,
        "successful_reflection_candidates": successful_candidates,
        "reflection_candidate_success_rate": _float(
            reflection.get(
                "reflection_candidate_success_rate",
                _ratio(successful_candidates, candidate_count),
            )
        ),
        "average_success_reflection_depth": _float(
            reflection.get(
                "average_success_reflection_depth",
                fallback.get("average_success_reflection_depth", 0.0),
            )
        ),
    }


def _generated_reflection_summary_from_rows(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    relevant = [
        row
        for row in rows
        if row.get("expected_reflection_depth")
        or _int(row.get("reflection_candidate_count", 0)) > 0
    ]
    candidate_count = sum(
        _int(row.get("reflection_candidate_count", 0))
        for row in relevant
    )
    successful_candidates = sum(
        _int(row.get("successful_reflection_candidate_count", 0))
        for row in relevant
    )
    success_depths = [
        _float(row.get("first_success_reflection_depth", 0.0))
        for row in relevant
        if row.get("first_success_reflection_depth") is not None
    ]
    return {
        "expected_reflection_depth_cases": sum(
            1 for row in relevant if row.get("expected_reflection_depth")
        ),
        "reflection_candidate_cases": sum(
            1
            for row in relevant
            if _int(row.get("reflection_candidate_count", 0)) > 0
        ),
        "reflection_success_cases": sum(
            1 for row in relevant if row.get("reflection_success")
        ),
        "reflection_candidates": candidate_count,
        "successful_reflection_candidates": successful_candidates,
        "reflection_candidate_success_rate": _ratio(
            successful_candidates,
            candidate_count,
        ),
        "average_success_reflection_depth": _ratio(
            sum(success_depths),
            len(success_depths),
        ),
    }


def _generated_reflection_pressure(
    case: dict[str, Any],
    target_signals: list[str],
) -> dict[str, Any]:
    metadata = _case_metadata(case)
    expected = _expected_reflection_depth(metadata, target_signals)
    observations = _reflection_observations(case)
    successful = [item for item in observations if bool(item.get("success"))]
    depths = [_reflection_depth(item) for item in observations]
    success_depths = [_reflection_depth(item) for item in successful]
    first_success_depth = min(success_depths) if success_depths else None
    return {
        "expected_reflection_depth": expected,
        "reflection_candidate_count": len(observations),
        "successful_reflection_candidate_count": len(successful),
        "reflection_success": bool(successful),
        "max_reflection_depth": max(depths, default=0),
        "first_success_reflection_depth": first_success_depth,
    }


def _reflection_observations(case: dict[str, Any]) -> list[dict[str, Any]]:
    observations = []
    for item in _list(case.get("beam_search_results")):
        if not isinstance(item, dict):
            continue
        if item.get("parent_id") or _reflection_depth(item) > 0:
            observations.append(item)
    for item in _list(case.get("repair_results")):
        if not isinstance(item, dict):
            continue
        if item.get("repair_loop_parent_id") or _reflection_depth(item) > 0:
            observations.append(item)
    return observations


def _reflection_depth(item: dict[str, Any]) -> int:
    return _int(item.get("depth", item.get("repair_loop_round_index", 0)))


def _generated_slice_grounding_pressure(
    base_trace: dict[str, Any],
    target_signals: list[str],
) -> dict[str, Any]:
    top = _dict(base_trace.get("top_localization"))
    graph = _dict(top.get("graph_evidence"))
    target_signal_set = {str(signal) for signal in target_signals}
    support = _float(graph.get("slice_support", 0.0))
    failed_reachability = _float(
        graph.get("slice_failed_test_reachability", 0.0)
    )
    call_chain_coverage = _float(
        graph.get("slice_call_chain_coverage", 0.0)
    )
    weak_slice_target = bool(target_signal_set & SLICE_GROUNDING_TARGET_SIGNALS)
    grounded = bool(graph.get("slice_grounded", False))
    pressure_components: list[float] = []
    if target_signal_set & {"weak_slice_grounding", "weak_slice_support"}:
        pressure = 1.0 - support
        if not grounded:
            pressure = max(pressure, 0.5)
        pressure_components.append(pressure)
    if "weak_failed_test_reachability" in target_signal_set:
        pressure_components.append(1.0 - failed_reachability)
    if "weak_call_chain_coverage" in target_signal_set:
        pressure_components.append(1.0 - call_chain_coverage)
    if weak_slice_target and not pressure_components:
        pressure_components.append(1.0 - support)
    pressure = max(pressure_components, default=0.0)
    execution_evidence = bool(
        weak_slice_target
        and grounded
        and support >= GENERATED_SLICE_EVIDENCE_MIN
        and failed_reachability >= GENERATED_SLICE_EVIDENCE_MIN
        and call_chain_coverage >= GENERATED_SLICE_EVIDENCE_MIN
    )
    return {
        "weak_slice_target": weak_slice_target,
        "pressure": round(max(0.0, min(1.0, pressure)), 4),
        "slice_support": round(support, 4),
        "failed_test_reachability": round(failed_reachability, 4),
        "call_chain_coverage": round(call_chain_coverage, 4),
        "execution_evidence": execution_evidence,
        "evidence_threshold": GENERATED_SLICE_EVIDENCE_MIN,
        "slice_grounded": grounded,
        "slice_edges": _int(graph.get("slice_edges", 0)),
        "call_chain_length": _int(top.get("call_chain_length", 0)),
    }


def _first_failed_variant(candidates: list[dict[str, Any]]) -> str:
    for candidate in candidates:
        if not bool(candidate.get("success", False)):
            return str(candidate.get("variant", ""))
    return ""


def _generated_selection_summary(row: dict[str, Any]) -> str:
    reasons = _list(row.get("selection_reasons"))
    reason_text = "; ".join(str(item) for item in reasons[:2])
    summary = (
        f"priority={row.get('priority', '')}, "
        f"score={_float(row.get('selection_score', 0.0)):.2f}, "
        f"policy={row.get('selection_policy', '')}, "
        f"reason={reason_text}"
    )
    provenance = _dict(row.get("selection_provenance"))
    bonus = _float(provenance.get("bonus", 0.0))
    if bonus > 0.0:
        summary += (
            ", provenance="
            f"bonus={bonus:.4f};"
            f"stable_ref={_float(provenance.get('stable_ref', 0.0)):.4f};"
            f"license={_float(provenance.get('license', 0.0)):.4f};"
            f"leakage={_float(provenance.get('leakage_risk', 0.0)):.4f}"
        )
    return summary


def _generated_search_pressure_summary(search: dict[str, Any]) -> str:
    first_success = search.get("first_success_rank")
    first_success_text = "" if first_success is None else str(first_success)
    return (
        f"candidates={_int(search.get('patch_candidates', 0))}, "
        f"beam={_int(search.get('beam_nodes', 0))}, "
        f"first_success={first_success_text}, "
        f"failures={_int(search.get('failures_before_success', 0))}, "
        f"pressure={_float(search.get('failure_pressure', 0.0)):.4f}, "
        f"inversion={bool(search.get('score_inversion', False))}, "
        f"diversity_expected={bool(search.get('expected_diversity_reranking', False))}, "
        "dedupe_expected="
        f"{bool(search.get('expected_candidate_deduplication', False))}, "
        f"deduped={_int(search.get('deduplicated_candidates', 0))}, "
        "duplicate_pressure="
        f"{_float(search.get('duplicate_pressure', 0.0)):.4f}"
    )


def _generated_slice_pressure_summary(slice_pressure: dict[str, Any]) -> str:
    return (
        f"target={bool(slice_pressure.get('weak_slice_target', False))}, "
        f"support={_float(slice_pressure.get('slice_support', 0.0)):.4f}, "
        "reachability="
        f"{_float(slice_pressure.get('failed_test_reachability', 0.0)):.4f}, "
        "call_chain="
        f"{_float(slice_pressure.get('call_chain_coverage', 0.0)):.4f}, "
        f"grounded={bool(slice_pressure.get('slice_grounded', False))}, "
        f"evidence={bool(slice_pressure.get('execution_evidence', False))}, "
        f"pressure={_float(slice_pressure.get('pressure', 0.0)):.4f}"
    )


def _generated_reflection_pressure_summary(reflection_pressure: dict[str, Any]) -> str:
    first_success_depth = reflection_pressure.get("first_success_reflection_depth")
    first_success_text = (
        "" if first_success_depth is None else str(first_success_depth)
    )
    return (
        f"expected={bool(reflection_pressure.get('expected_reflection_depth', False))}, "
        f"candidates={_int(reflection_pressure.get('reflection_candidate_count', 0))}, "
        f"successes={_int(reflection_pressure.get('successful_reflection_candidate_count', 0))}, "
        f"max_depth={_int(reflection_pressure.get('max_reflection_depth', 0))}, "
        f"first_success_depth={first_success_text}"
    )


def _generated_decoy_summary(row: dict[str, Any]) -> str:
    decoy = str(row.get("decoy_variant", ""))
    success = str(row.get("success_variant", ""))
    if decoy or success:
        return f"{decoy} -> {success}"
    return "n/a"


def _generated_patch_evidence_summary(patch: dict[str, Any]) -> str:
    return (
        f"success={bool(patch.get('patch_success', False))}, "
        f"strategy={patch.get('strategy', '')}, "
        f"rule={patch.get('best_rule', '')}, "
        f"sandbox={_int(patch.get('sandbox_passed', 0))}/"
        f"{_int(patch.get('sandbox_failed', 0))}, "
        f"risk={_float(patch.get('risk_score', 0.0)):.4f}"
    )


def _generated_hard_case_ablation_links(
    traces: list[dict[str, Any]],
    ablation_impact: dict[str, Any],
    generated_diversity_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = [row for row in _list(ablation_impact.get("rows")) if isinstance(row, dict)]
    rows_by_variant = {str(row.get("variant", "")): row for row in rows}
    diversity_cases = _generated_diversity_cases_by_name(
        generated_diversity_evidence or {}
    )
    links = []
    for trace in traces:
        link = _generated_hard_case_ablation_link(
            trace,
            rows_by_variant,
            diversity_cases,
        )
        if link:
            links.append(link)
    links.sort(
        key=lambda row: (
            _generated_ablation_link_type_rank(str(row.get("link_type", ""))),
            str(row.get("direction", "")) == "regression",
            abs(_float(row.get("impact_score", 0.0))),
            str(row.get("case", "")),
        ),
        reverse=True,
    )
    return {
        "summary": _generated_hard_case_ablation_link_summary(links),
        "links": links,
    }


def _generated_hard_case_ablation_link(
    trace: dict[str, Any],
    rows_by_variant: dict[str, dict[str, Any]],
    diversity_cases: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    diversity_link = _generated_diversity_counterfactual_link(
        trace,
        diversity_cases,
    )
    if diversity_link:
        return diversity_link

    candidates: list[tuple[str, str, dict[str, Any], str]] = []
    for signal in _list(trace.get("target_signals")):
        signal_text = str(signal)
        if signal_text in rows_by_variant:
            candidates.append(
                (
                    signal_text,
                    "direct_variant",
                    rows_by_variant[signal_text],
                    "target signal directly matches an ablation variant",
                )
            )
        for variant in _ablation_variants_for_generated_signal(signal_text):
            row = rows_by_variant.get(variant)
            if row:
                candidates.append(
                    (
                        signal_text,
                        "component_proxy",
                        row,
                        _ablation_rationale_for_generated_signal(signal_text),
                    )
                )
    if not candidates:
        return {}
    signal, link_type, row, rationale = max(
        candidates,
        key=lambda candidate: (
            _signal_variant_link_priority(
                candidate[0],
                str(candidate[2].get("variant", "")),
            ),
            *_ablation_link_rank(candidate[1], candidate[2]),
        ),
    )
    metric, delta = _main_delta(row)
    search = _dict(trace.get("search_pressure"))
    patch = _dict(trace.get("patch_evidence"))
    variant = str(row.get("variant", ""))
    return {
        "case": str(trace.get("case", "")),
        "target_signal": signal,
        "link_type": link_type,
        "component": _ablation_component(variant),
        "variant": variant,
        "direction": str(row.get("direction", "neutral")),
        "impact_score": _float(row.get("impact_score", 0.0)),
        "main_delta_metric": metric,
        "main_delta": delta,
        "rationale": rationale,
        "score_inversion": bool(search.get("score_inversion", False)),
        "candidate_competition": bool(search.get("candidate_competition", False)),
        "expected_candidate_deduplication": bool(
            search.get("expected_candidate_deduplication", False)
        ),
        "deduplicated_candidates": _int(
            search.get("deduplicated_candidates", 0)
        ),
        "duplicate_pressure": _float(search.get("duplicate_pressure", 0.0)),
        "failure_pressure": _float(search.get("failure_pressure", 0.0)),
        "patch_success": bool(patch.get("patch_success", False)),
        "sandbox_passed": _int(patch.get("sandbox_passed", 0)),
        "sandbox_failed": _int(patch.get("sandbox_failed", 0)),
    }


def _generated_diversity_counterfactual_link(
    trace: dict[str, Any],
    diversity_cases: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if "search_diversity_reranking" not in {
        str(signal) for signal in _list(trace.get("target_signals"))
    }:
        return {}
    case_name = str(trace.get("case", ""))
    case = diversity_cases.get(case_name, {})
    if not case or not bool(case.get("budget_sensitive_success", False)):
        return {}
    search = _dict(trace.get("search_pressure"))
    patch = _dict(trace.get("patch_evidence"))
    delta = _float(case.get("projected_patch_success_delta", -1.0))
    return {
        "case": case_name,
        "target_signal": "search_diversity_reranking",
        "link_type": "generated_counterfactual",
        "component": _ablation_component("without_diversity_reranking"),
        "variant": "without_diversity_reranking",
        "direction": "regression" if delta < 0.0 else "neutral",
        "impact_score": delta,
        "main_delta_metric": "projected_patch_success_delta",
        "main_delta": delta,
        "rationale": (
            "generated budget-sensitive diversity reranking counterfactual"
        ),
        "score_inversion": bool(search.get("score_inversion", False)),
        "candidate_competition": bool(search.get("candidate_competition", False)),
        "expected_candidate_deduplication": bool(
            search.get("expected_candidate_deduplication", False)
        ),
        "deduplicated_candidates": _int(
            search.get("deduplicated_candidates", 0)
        ),
        "duplicate_pressure": _float(search.get("duplicate_pressure", 0.0)),
        "failure_pressure": _float(search.get("failure_pressure", 0.0)),
        "patch_success": bool(patch.get("patch_success", False)),
        "sandbox_passed": _int(patch.get("sandbox_passed", 0)),
        "sandbox_failed": _int(patch.get("sandbox_failed", 0)),
        "success_base_rank": _int(case.get("success_base_rank", 0)),
        "success_diversity_rank": _int(case.get("success_diversity_rank", 0)),
        "success_budget_gap_before_rerank": _int(
            case.get("success_budget_gap_before_rerank", 0)
        ),
        "success_budget_margin_after_rerank": _int(
            case.get("success_budget_margin_after_rerank", 0)
        ),
        "counterfactual_condition": str(
            case.get("counterfactual_condition", "")
        ),
    }


def _generated_diversity_cases_by_name(
    generated_diversity_evidence: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    delta = _float(generated_diversity_evidence.get("projected_patch_success_delta", 0.0))
    for row in _list(generated_diversity_evidence.get("cases")):
        if not isinstance(row, dict):
            continue
        name = str(row.get("case", ""))
        if not name:
            continue
        output[name] = {
            **row,
            "projected_patch_success_delta": delta,
        }
    return output


def _ablation_link_rank(
    link_type: str,
    row: dict[str, Any],
) -> tuple[int, int, float, float, str]:
    direction = str(row.get("direction", "neutral"))
    return (
        1 if link_type == "direct_variant" else 0,
        1 if direction == "regression" else 0,
        abs(_float(row.get("impact_score", 0.0))),
        abs(_main_delta(row)[1]),
        str(row.get("variant", "")),
    )


def _generated_ablation_link_type_rank(link_type: str) -> int:
    return {
        "direct_variant": 3,
        "generated_counterfactual": 2,
        "component_proxy": 1,
    }.get(link_type, 0)


def _generated_hard_case_ablation_link_summary(
    links: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "linked_cases": len({str(link.get("case", "")) for link in links}),
        "regression_linked_cases": len(
            {
                str(link.get("case", ""))
                for link in links
                if str(link.get("direction", "")) == "regression"
            }
        ),
        "direct_variant_links": sum(
            1 for link in links if link.get("link_type") == "direct_variant"
        ),
        "component_proxy_links": sum(
            1 for link in links if link.get("link_type") == "component_proxy"
        ),
        "generated_counterfactual_links": sum(
            1
            for link in links
            if link.get("link_type") == "generated_counterfactual"
        ),
        "linked_components": sorted(
            {
                str(link.get("component", ""))
                for link in links
                if str(link.get("component", ""))
            }
        ),
        "by_signal": _generated_ablation_link_group_summaries(
            links,
            group_key="target_signal",
            output_key="signal",
        ),
        "by_component": _generated_ablation_link_group_summaries(
            links,
            group_key="component",
            output_key="component",
        ),
    }


def _generated_ablation_link_group_summaries(
    links: list[dict[str, Any]],
    *,
    group_key: str,
    output_key: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for link in links:
        name = str(link.get(group_key, ""))
        if not name:
            continue
        grouped.setdefault(name, []).append(link)

    summaries = []
    for name, rows in grouped.items():
        strongest = max(
            rows,
            key=lambda row: (
                abs(_float(row.get("impact_score", 0.0))),
                abs(_float(row.get("main_delta", 0.0))),
                str(row.get("variant", "")),
            ),
        )
        summaries.append(
            {
                output_key: name,
                "linked_cases": len(
                    {str(row.get("case", "")) for row in rows}
                ),
                "regression_linked_cases": len(
                    {
                        str(row.get("case", ""))
                        for row in rows
                        if str(row.get("direction", "")) == "regression"
                    }
                ),
                "direct_variant_links": sum(
                    1 for row in rows if row.get("link_type") == "direct_variant"
                ),
                "component_proxy_links": sum(
                    1 for row in rows if row.get("link_type") == "component_proxy"
                ),
                "generated_counterfactual_links": sum(
                    1
                    for row in rows
                    if row.get("link_type") == "generated_counterfactual"
                ),
                "target_signals": sorted(
                    {
                        str(row.get("target_signal", ""))
                        for row in rows
                        if str(row.get("target_signal", ""))
                    }
                ),
                "components": sorted(
                    {
                        str(row.get("component", ""))
                        for row in rows
                        if str(row.get("component", ""))
                    }
                ),
                "variants": sorted(
                    {
                        str(row.get("variant", ""))
                        for row in rows
                        if str(row.get("variant", ""))
                    }
                ),
                "max_abs_impact": abs(_float(strongest.get("impact_score", 0.0))),
                "strongest_variant": str(strongest.get("variant", "")),
                "strongest_delta_metric": str(
                    strongest.get("main_delta_metric", "")
                ),
                "strongest_delta": _float(strongest.get("main_delta", 0.0)),
            }
        )
    summaries.sort(
        key=lambda row: (
            _int(row.get("regression_linked_cases", 0)),
            _float(row.get("max_abs_impact", 0.0)),
            str(row.get(output_key, "")),
        ),
        reverse=True,
    )
    return summaries


def _generated_ablation_link_evidence(row: dict[str, Any]) -> str:
    parts = [
        f"impact={_float(row.get('impact_score', 0.0)):.4f}",
        f"main_delta={row.get('main_delta_metric', '')}:"
        f"{_float(row.get('main_delta', 0.0)):.4f}",
        f"pressure={_float(row.get('failure_pressure', 0.0)):.4f}",
        f"inversion={bool(row.get('score_inversion', False))}",
        f"competition={bool(row.get('candidate_competition', False))}",
        f"sandbox={_int(row.get('sandbox_passed', 0))}/"
        f"{_int(row.get('sandbox_failed', 0))}",
    ]
    if (
        row.get("expected_candidate_deduplication")
        or _int(row.get("deduplicated_candidates", 0)) > 0
        or _float(row.get("duplicate_pressure", 0.0)) > 0.0
    ):
        parts.append(
            "dedupe="
            f"expected={bool(row.get('expected_candidate_deduplication', False))};"
            f"deduped={_int(row.get('deduplicated_candidates', 0))};"
            f"pressure={_float(row.get('duplicate_pressure', 0.0)):.4f}"
        )
    if row.get("link_type") == "generated_counterfactual":
        parts.append(
            "counterfactual="
            f"{row.get('counterfactual_condition', '')}, "
            "base_rank="
            f"{_int(row.get('success_base_rank', 0))}, "
            "rerank="
            f"{_int(row.get('success_diversity_rank', 0))}, "
            "gap="
            f"{_int(row.get('success_budget_gap_before_rerank', 0))}"
        )
    parts.append(str(row.get("rationale", "")))
    return ", ".join(parts)


def _ratio(numerator: float, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _avg_metric(rows: list[dict[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return round(sum(_float(row.get(key, 0.0)) for row in rows) / len(rows), 4)


def _ablation_highlights(ablation_impact: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [row for row in _list(ablation_impact.get("rows")) if isinstance(row, dict)]
    rows.sort(
        key=lambda row: (
            abs(_float(row.get("impact_score", 0.0))),
            abs(_float(row.get("delta_patch_success", 0.0))),
            row.get("variant", ""),
        ),
        reverse=True,
    )
    return [
        {
            "variant": str(row.get("variant", "")),
            "direction": str(row.get("direction", "neutral")),
            "impact_score": _float(row.get("impact_score", 0.0)),
            "main_delta_metric": _main_delta(row)[0],
            "main_delta": _main_delta(row)[1],
        }
        for row in rows[:5]
    ]


def _ablation_component_summary(
    ablation_impact: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = [row for row in _list(ablation_impact.get("rows")) if isinstance(row, dict)]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_ablation_component(str(row.get("variant", ""))), []).append(
            row
        )
    output = []
    for component, component_rows in grouped.items():
        strongest = max(
            component_rows,
            key=lambda row: (
                abs(_float(row.get("impact_score", 0.0))),
                abs(_main_delta(row)[1]),
                str(row.get("variant", "")),
            ),
        )
        metric, delta = _main_delta(strongest)
        output.append(
            {
                "component": component,
                "variant_count": len(component_rows),
                "regression_count": sum(
                    1
                    for row in component_rows
                    if str(row.get("direction", "")) == "regression"
                ),
                "improvement_count": sum(
                    1
                    for row in component_rows
                    if str(row.get("direction", "")) == "improvement"
                ),
                "max_abs_impact": round(
                    max(
                        abs(_float(row.get("impact_score", 0.0)))
                        for row in component_rows
                    ),
                    4,
                ),
                "strongest_variant": str(strongest.get("variant", "")),
                "strongest_direction": str(strongest.get("direction", "neutral")),
                "strongest_delta_metric": metric,
                "strongest_delta": delta,
            }
        )
    output.sort(
        key=lambda row: (
            _float(row.get("max_abs_impact", 0.0)),
            _int(row.get("regression_count", 0)),
            str(row.get("component", "")),
        ),
        reverse=True,
    )
    return output


def _calibration_ablation_summary(
    ablation_impact: dict[str, Any],
) -> dict[str, Any]:
    rows = [row for row in _list(ablation_impact.get("rows")) if isinstance(row, dict)]
    rows = [
        row
        for row in rows
        if "delta_calibrated_ece_improvement" in row
        or "delta_calibrated_brier_improvement" in row
    ]
    if not rows:
        return {
            "variant_count": 0,
            "regression_count": 0,
            "max_calibrated_ece_regression": 0.0,
            "strongest_variant": "",
        }
    regressions = [
        row
        for row in rows
        if _float(row.get("delta_calibrated_ece_improvement", 0.0)) < 0.0
    ]
    strongest = min(
        rows,
        key=lambda row: (
            _float(row.get("delta_calibrated_ece_improvement", 0.0)),
            _float(row.get("delta_calibrated_brier_improvement", 0.0)),
            str(row.get("variant", "")),
        ),
    )
    return {
        "variant_count": len(rows),
        "regression_count": len(regressions),
        "max_calibrated_ece_regression": round(
            max(
                (
                    abs(_float(row.get("delta_calibrated_ece_improvement", 0.0)))
                    for row in regressions
                ),
                default=0.0,
            ),
            4,
        ),
        "strongest_variant": str(strongest.get("variant", "")),
    }


def _search_diversity_reranking_evidence(
    ablation_impact: dict[str, Any],
    search_competition: dict[str, Any],
    generated_diversity_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    generated_diversity_evidence = generated_diversity_evidence or {}
    row = next(
        (
            item
            for item in _list(ablation_impact.get("rows"))
            if isinstance(item, dict)
            and str(item.get("variant", "")) == "without_diversity_reranking"
        ),
        {},
    )
    direction = str(row.get("direction", "missing")) if row else "missing"
    delta_patch = _float(row.get("delta_patch_success", 0.0)) if row else 0.0
    delta_beam = _float(row.get("delta_beam_success", 0.0)) if row else 0.0
    assisted = _int(search_competition.get("diversity_assisted_success_count", 0))
    success_lift = _float(
        search_competition.get("average_success_diversity_lift", 0.0)
    )
    success_bonus = _float(
        search_competition.get("average_success_diversity_bonus", 0.0)
    )
    has_ablation_regression = bool(
        row
        and direction == "regression"
        and (delta_patch < 0.0 or delta_beam < 0.0)
    )
    has_lift_evidence = bool(assisted > 0 or success_lift > 0.0 or success_bonus > 0.0)
    generated_budget_sensitive_successes = _int(
        generated_diversity_evidence.get("budget_sensitive_successes", 0)
    )
    generated_projected_patch_delta = _float(
        generated_diversity_evidence.get("projected_patch_success_delta", 0.0)
    )
    generated_projected_beam_delta = _float(
        generated_diversity_evidence.get("projected_beam_success_delta", 0.0)
    )
    has_generated_counterfactual = bool(
        generated_budget_sensitive_successes > 0
        and (generated_projected_patch_delta < 0.0 or generated_projected_beam_delta < 0.0)
    )
    combined_proof = bool(
        (has_ablation_regression and has_lift_evidence)
        or has_generated_counterfactual
    )
    proof_source = ""
    if has_ablation_regression and has_lift_evidence:
        proof_source = "main_ablation_and_search_competition"
    elif has_generated_counterfactual:
        proof_source = "generated_budget_counterfactual"
    elif row or has_lift_evidence:
        proof_source = "partial_main_or_lift_evidence"
    return {
        "status": (
            "validated"
            if combined_proof
            else "partial"
            if row or has_lift_evidence
            else "missing"
        ),
        "variant": (
            "without_diversity_reranking"
            if row or has_generated_counterfactual
            else ""
        ),
        "proof_source": proof_source,
        "ablation_direction": direction,
        "ablation_impact_score": _float(row.get("impact_score", 0.0)) if row else 0.0,
        "delta_patch_success": delta_patch,
        "delta_beam_success": delta_beam,
        "delta_multi_patch_success": (
            _float(row.get("delta_multi_patch_success", 0.0)) if row else 0.0
        ),
        "diversity_assisted_successes": assisted,
        "diversity_assisted_success_rate": _float(
            search_competition.get("diversity_assisted_success_rate", 0.0)
        ),
        "average_success_diversity_lift": success_lift,
        "average_success_diversity_bonus": success_bonus,
        "generated_budget_sensitive_successes": generated_budget_sensitive_successes,
        "generated_projected_patch_success_delta": generated_projected_patch_delta,
        "generated_projected_beam_success_delta": generated_projected_beam_delta,
        "generated_average_success_diversity_lift": _float(
            generated_diversity_evidence.get("average_success_diversity_lift", 0.0)
        ),
        "generated_average_success_diversity_bonus": _float(
            generated_diversity_evidence.get("average_success_diversity_bonus", 0.0)
        ),
        "generated_counterfactual_proof": has_generated_counterfactual,
        "combined_proof": combined_proof,
    }


def _quality_summary(quality_gate: dict[str, Any]) -> dict[str, Any]:
    if not quality_gate:
        return {"status": "missing", "failed_checks": []}
    failed = [
        str(check.get("name", "unknown"))
        for check in _list(quality_gate.get("checks"))
        if isinstance(check, dict) and not check.get("passed", False)
    ]
    return {
        "status": "pass" if quality_gate.get("passed") else "fail",
        "failed_checks": failed,
    }


def _gaps(
    *,
    summary: dict[str, Any],
    payload: dict[str, Any],
    quality_summary: dict[str, Any],
    hard_case_mining: dict[str, Any],
    benchmark_mining: dict[str, Any],
    hard_case_generation: dict[str, Any],
) -> list[str]:
    gaps = []
    if quality_summary.get("status") == "missing":
        gaps.append("Quality gate artifact is missing.")
    elif quality_summary.get("status") == "fail":
        failed = ", ".join(_list(quality_summary.get("failed_checks"))[:5])
        gaps.append(f"Quality gate has failing checks: {failed}.")
    if not payload.get("ablation_results"):
        gaps.append("Ablation results are missing or skipped.")
    if _source_group_count(summary) < 3:
        gaps.append("Generalization report has fewer than 3 source groups.")
    suggestions = _int(hard_case_mining.get("suggestion_count", 0))
    generated = _int(hard_case_generation.get("generated_count", 0))
    if suggestions > 0 and generated == 0:
        gaps.append("Hard-case mining produced suggestions but no generated cases.")
    patch_reliability = _dict(summary.get("patch_judge_reliability"))
    if (
        _int(patch_reliability.get("judged_candidate_count", 0)) > 0
        and not benchmark_mining
    ):
        gaps.append(
            "Patch judge reliability is present but benchmark mining artifact is missing."
        )
    return gaps


def _resume_bullets(
    *,
    headline: dict[str, Any],
    algorithm_evidence: dict[str, dict[str, Any]],
    ablation_highlights: list[dict[str, Any]],
) -> list[str]:
    graph = algorithm_evidence["static_graph_reasoning"]
    search = algorithm_evidence["search_and_repair"]
    robust = algorithm_evidence["robustness_and_generalization"]
    provenance = algorithm_evidence["benchmark_provenance"]
    attribution = algorithm_evidence["localization_attribution"]
    calibration = algorithm_evidence["confidence_calibration"]
    weight = algorithm_evidence["weight_and_ablation_search"]
    expansion = algorithm_evidence["benchmark_expansion"]
    diversity = algorithm_evidence.get("search_diversity_reranking", {})
    generated_diversity = _dict(
        algorithm_evidence.get("generated_search_diversity_reranking")
    )
    generated_deduplication = _dict(
        algorithm_evidence.get("generated_candidate_deduplication")
    )
    dedupe_clause = ""
    if _int(search.get("search_total_deduplicated_candidates", 0)) > 0:
        dedupe_clause = (
            ", deduped candidates="
            f"{_int(search.get('search_total_deduplicated_candidates', 0))}, "
            "duplicate pressure="
            f"{_float(search.get('search_average_duplicate_pressure', 0.0)):.3f}"
        )
    search_budget_clause = (
        f"budget AUC={search['search_budget_auc']:.3f}{dedupe_clause}"
    )
    bullets = [
        (
            "Built a graph-guided code intelligence agent evaluated on "
            f"{_int(headline.get('case_count', 0))} benchmark cases with "
            f"Top-1={_float(headline.get('top1', 0.0)):.3f}, "
            f"Top-3={_float(headline.get('top3', 0.0)):.3f}, "
            f"MAP={_float(headline.get('map', 0.0)):.3f}."
        ),
        (
            "Combined static rules, program graph evidence and data-flow features; "
            f"captured {graph['cross_function_data_flow_cases']} cross-function "
            "data-flow cases and "
            f"{graph.get('program_slice_cases', 0)} program-slice evidence cases; "
            f"slice-grounded cases={graph.get('slice_grounded_cases', 0)}, "
            f"avg slice support={_float(graph.get('average_top1_slice_support', 0.0)):.3f}."
        ),
        (
            "Closed the repair loop with sandbox execution and beam search; "
            f"patch success={_float(headline.get('patch_success_rate', 0.0)):.3f}, "
            f"beam success={search['beam_success_rate']:.3f}, "
            f"search efficiency={search['search_efficiency']:.3f}, "
            f"{search_budget_clause}, "
            f"competition cases={search['search_competition_cases']}, "
            "diversity-assisted successes="
            f"{search['search_diversity_assisted_successes']}."
        ),
        (
            "Measured robustness through difficulty buckets and leave-one-project-out "
            f"generalization across {robust['source_group_count']} source groups; "
            f"stability_score={robust['stability_score']:.4f}, "
            f"worst_holdout={robust['worst_holdout_group']}, "
            f"risk={robust['risk_level']}."
        ),
    ]
    if provenance["case_count"]:
        bullets.append(
            "Audited benchmark provenance to reduce evaluation leakage risk; "
            f"case_coverage={provenance['case_provenance_coverage']:.4f}, "
            f"sha256_coverage={provenance['source_sha256_coverage']:.4f}, "
            f"stable_ref_coverage={provenance['stable_ref_coverage']:.4f}, "
            f"license_coverage={provenance['license_coverage']:.4f}, "
            f"duplicate_signatures={provenance['duplicate_signature_count']}, "
            f"leakage_risk={provenance['leakage_risk_score']:.4f}."
        )
    if attribution["case_count"]:
        bullets.append(
            "Explained FinalScore decisions with component attribution and "
            f"counterfactual ranking checks; coverage="
            f"{attribution['attribution_coverage']:.4f}, "
            f"mean_top1_margin={attribution['mean_top1_margin']:.4f}, "
            f"fragile_top1_rate={attribution['fragile_top1_rate']:.4f}, "
            "counterfactual_flip_rate="
            f"{attribution['counterfactual_flip_rate']:.4f}."
        )
    if calibration["localization_case_count"]:
        bullets.append(
            "Audited FinalScore confidence calibration for localization ranking; "
            f"cases={calibration['localization_case_count']}, "
            "raw->calibrated Brier="
            f"{calibration['localization_brier_score']:.4f}->"
            f"{calibration['localization_calibrated_brier_score']:.4f}, "
            "raw->calibrated ECE="
            f"{calibration['localization_expected_calibration_error']:.4f}->"
            f"{calibration['localization_calibrated_expected_calibration_error']:.4f}, "
            "average_confidence="
            f"{calibration['localization_average_confidence']:.4f}->"
            f"{calibration['localization_calibrated_average_confidence']:.4f}, "
            "stratified_groups="
            f"{calibration['localization_stratified_group_count']}, "
            "source_holdout_splits="
            f"{calibration['localization_source_holdout_split_count']}, "
            "max_holdout_calibrated_ECE="
            f"{calibration['localization_max_holdout_calibrated_ece']:.4f}."
        )
    if _dict(diversity).get("combined_proof"):
        bullets.append(
            "Validated diversity-aware patch reranking with paired evidence; "
            f"proof_source={diversity.get('proof_source', '')}, "
            f"without_diversity impact="
            f"{_float(diversity.get('ablation_impact_score', 0.0)):.4f}, "
            f"delta_patch_success="
            f"{_float(diversity.get('delta_patch_success', 0.0)):.4f}, "
            f"delta_beam_success="
            f"{_float(diversity.get('delta_beam_success', 0.0)):.4f}, "
            "assisted_successes="
            f"{_int(diversity.get('diversity_assisted_successes', 0))}, "
            "avg_success_lift="
            f"{_float(diversity.get('average_success_diversity_lift', 0.0)):.4f}, "
            "generated_budget_sensitive_successes="
            f"{_int(diversity.get('generated_budget_sensitive_successes', 0))}, "
            "generated_projected_patch_delta="
            f"{_float(diversity.get('generated_projected_patch_success_delta', 0.0)):.4f}, "
            "generated_success_lift="
            f"{_float(diversity.get('generated_average_success_diversity_lift', 0.0)):.4f}."
        )
    if ablation_highlights:
        top = ablation_highlights[0]
        bullets.append(
            "Validated algorithm contribution with ablation impact analysis; "
            f"top signal={top['variant']} ({top['direction']}, "
            f"impact={top['impact_score']:.4f})."
        )
    if weight["calibration_ablation_variant_count"]:
        bullets.append(
            "Linked ablation study to confidence calibration; "
            f"calibration_variants={weight['calibration_ablation_variant_count']}, "
            f"calibration_regressions="
            f"{weight['calibration_ablation_regression_count']}, "
            "max_calibrated_ECE_regression="
            f"{weight['max_calibrated_ece_regression']:.4f}."
        )
    if (
        expansion["hard_case_suggestions"]
        or expansion["generated_hard_cases"]
        or expansion["judge_mining_template_seeds"]
    ):
        bullets.append(
            "Added failure-driven hard-case mining and candidate generation; "
            f"suggestions={expansion['hard_case_suggestions']}, "
            f"generated={expansion['generated_hard_cases']}, "
            f"generated_score_inversions="
            f"{expansion['generated_benchmark_score_inversions']}, "
            "generated_diversity_assisted_successes="
            f"{expansion.get('generated_benchmark_diversity_assisted_successes', 0)}, "
            "generated_avg_diversity_lift="
            f"{_float(expansion.get('generated_benchmark_average_success_diversity_lift', 0.0)):.4f}, "
            "generated_avg_diversity_bonus="
            f"{_float(expansion.get('generated_benchmark_average_success_diversity_bonus', 0.0)):.4f}, "
            "generated_budget_sensitive_successes="
            f"{_int(generated_diversity.get('budget_sensitive_successes', 0))}, "
            "projected_without_diversity_delta="
            f"{_float(generated_diversity.get('projected_patch_success_delta', 0.0)):.4f}, "
            "generated_dedupe_affected_cases="
            f"{expansion.get('generated_benchmark_dedupe_affected_cases', 0)}, "
            "generated_deduplicated_candidates="
            f"{expansion.get('generated_benchmark_deduplicated_candidates', 0)}, "
            "generated_duplicate_pressure="
            f"{_float(expansion.get('generated_benchmark_duplicate_pressure', 0.0)):.4f}, "
            "generated_reflection_success_cases="
            f"{expansion.get('generated_benchmark_reflection_success_cases', 0)}, "
            "generated_reflection_candidates="
            f"{expansion.get('generated_benchmark_reflection_candidates', 0)}, "
            "generated_slice_evidence="
            f"{expansion.get('generated_benchmark_slice_grounding_evidence_cases', 0)}/"
            f"{expansion.get('generated_benchmark_slice_grounding_target_cases', 0)}, "
            "provenance_selected="
            f"{expansion.get('generated_provenance_selected_cases', 0)}, "
            "avg_provenance_bonus="
            f"{_float(expansion.get('generated_average_provenance_bonus', 0.0)):.4f}, "
            f"judge_seed_templates={expansion['judge_mining_template_seeds']}."
        )
    if generated_deduplication.get("budget_pressure_proof"):
        bullets.append(
            "Validated generated candidate deduplication pressure with sandbox "
            "budget evidence; "
            f"expected_cases={_int(generated_deduplication.get('expected_cases', 0))}, "
            f"evidence_cases={_int(generated_deduplication.get('evidence_cases', 0))}, "
            "dedupe_affected_cases="
            f"{_int(generated_deduplication.get('dedupe_affected_cases', 0))}, "
            "deduplicated_candidates="
            f"{_int(generated_deduplication.get('total_deduplicated_candidates', 0))}, "
            "duplicate_pressure="
            f"{_float(generated_deduplication.get('average_duplicate_pressure', 0.0)):.4f}."
        )
    return bullets


def _readiness_score(rows: list[dict[str, Any]]) -> float:
    score_by_status = {"ready": 1.0, "partial": 0.5, "missing": 0.0}
    if not rows:
        return 0.0
    return round(
        100.0
        * sum(score_by_status.get(row.get("status", "missing"), 0.0) for row in rows)
        / len(rows),
        2,
    )


def _artifact_kind(payload: dict[str, Any]) -> str:
    if "benchmark_report" in payload:
        return "experiment_suite"
    if "summary" in payload:
        return "benchmark_report"
    return "unknown"


def _has_benchmark_mining_signal(payload: dict[str, Any]) -> bool:
    mining = _dict(payload.get("benchmark_mining"))
    if not mining:
        return False
    return any(
        [
            _int(mining.get("judged_candidate_count", 0)) > 0,
            _int(mining.get("cluster_count", 0)) > 0,
            bool(_list(mining.get("template_seeds"))),
        ]
    )


def _main_delta(row: dict[str, Any]) -> tuple[str, float]:
    candidates = [
        "delta_patch_success",
        "delta_multi_patch_success",
        "delta_beam_success",
        "delta_calibrated_ece_improvement",
        "delta_calibrated_brier_improvement",
        "delta_map",
        "delta_top1",
        "delta_rule_recall",
        "delta_rule_precision",
    ]
    best_key = "impact_score"
    best_value = _float(row.get(best_key, 0.0))
    for key in candidates:
        value = row.get(key)
        if value is None:
            continue
        value_float = _float(value)
        if abs(value_float) > abs(best_value):
            best_key = key
            best_value = value_float
    return best_key, best_value


def _source_group_count(summary: dict[str, Any]) -> int:
    return _int(_dict(summary.get("generalization_report")).get("source_group_count", 0))


def _dimension_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        dimension = str(row.get("dimension", ""))
        if not dimension:
            continue
        counts[dimension] = counts.get(dimension, 0) + 1
    return counts


def _worst_calibration_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    return max(
        rows,
        key=lambda row: (
            _float(row.get("calibrated_expected_calibration_error", 0.0)),
            _int(row.get("case_count", 0)),
        ),
    )


def _worst_holdout_split(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    return max(
        rows,
        key=lambda row: (
            _float(row.get("holdout_calibrated_expected_calibration_error", 0.0)),
            _int(row.get("holdout_case_count", 0)),
        ),
    )


def _max_float(values: Any) -> float:
    return round(max((_float(value) for value in values), default=0.0), 4)


def _min_int(values: Any) -> int:
    collected = [_int(value) for value in values]
    return min(collected, default=0)


def _avg_float(values: Any) -> float:
    collected = [_float(value) for value in values]
    if not collected:
        return 0.0
    return round(sum(collected) / len(collected), 4)


def _status(condition: bool, partial: bool = False) -> str:
    if condition:
        return "ready"
    if partial:
        return "partial"
    return "missing"


def _first_dict(values: list[Any]) -> dict[str, Any]:
    if values and isinstance(values[0], dict):
        return values[0]
    return {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _fmt_number(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _title(value: str) -> str:
    return value.replace("_", " ").title()


def _markdown_cell(value: str) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def _join_items(value: Any) -> str:
    return ", ".join(str(item) for item in _list(value))


if __name__ == "__main__":
    main()
