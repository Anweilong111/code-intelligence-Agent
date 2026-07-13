from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.v1_readiness_dataset_audit import (
    REQUIRED_EVALUATION_METRICS,
    V1_AGENT_LOOP,
)


def build_v1_evaluation_summary(
    *,
    readiness_audit: dict[str, Any] | None = None,
    onboarding_suite: dict[str, Any] | None = None,
    repair_metrics: dict[str, Any] | None = None,
    repair_catalog_audit: dict[str, Any] | None = None,
    localization_report: dict[str, Any] | None = None,
    llm_cost_report: dict[str, Any] | None = None,
    source_paths: dict[str, str] | None = None,
) -> dict[str, Any]:
    readiness = _dict(readiness_audit)
    onboarding = _dict(onboarding_suite)
    repair = _dict(repair_metrics)
    catalog = _dict(repair_catalog_audit)
    localization = _dict(localization_report)
    llm_cost = _dict(llm_cost_report)

    metrics = [
        _onboarding_success_metric(onboarding),
        _topk_localization_metric(localization),
        _pass_at_metric(repair, "pass_at_1", "1"),
        _pass_at_metric(repair, "pass_at_k", _largest_success_at_key(repair)),
        _reflection_uplift_metric(repair),
        _blocker_accuracy_metric(catalog),
        _sandbox_success_metric(repair),
        _average_runtime_metric(onboarding, repair),
        _llm_cost_metric(onboarding, repair, llm_cost),
    ]
    by_id = {str(item.get("metric_id")): item for item in metrics}
    missing_contracts = [
        metric_id
        for metric_id in REQUIRED_EVALUATION_METRICS
        if metric_id not in by_id
    ]
    measured_count = sum(
        1 for item in metrics if item.get("evidence_status") == "measured"
    )
    proxy_count = sum(1 for item in metrics if item.get("evidence_status") == "proxy")
    missing_count = sum(
        1 for item in metrics if item.get("evidence_status") == "missing_evidence"
    )
    readiness_status = str(readiness.get("status") or "missing")
    if readiness_status != "pass":
        status = "incomplete"
        reason = "v1_readiness_audit_not_passed"
    elif missing_count or proxy_count or missing_contracts:
        status = "partial"
        reason = "v1_metric_evidence_incomplete"
    else:
        status = "pass"
        reason = "v1_metric_evidence_complete"
    return {
        "status": status,
        "reason": reason,
        "source_paths": _dict(source_paths),
        "summary": {
            "required_metric_count": len(REQUIRED_EVALUATION_METRICS),
            "metric_count": len(metrics),
            "measured_metric_count": measured_count,
            "proxy_metric_count": proxy_count,
            "missing_metric_count": missing_count,
            "missing_contract_count": len(missing_contracts),
            "readiness_status": readiness_status,
            "onboarding_suite_present": bool(onboarding),
            "repair_metrics_present": bool(repair),
            "repair_catalog_audit_present": bool(catalog),
            "localization_report_present": bool(localization),
            "llm_cost_report_present": bool(llm_cost),
            "agent_loop": V1_AGENT_LOOP,
        },
        "metrics": metrics,
        "missing_contracts": missing_contracts,
        "next_actions": _next_actions(metrics, readiness_status, missing_contracts),
        "agent_loop": V1_AGENT_LOOP,
    }


def write_v1_evaluation_summary_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "v1_evaluation_summary.json"
    markdown_path = root / "v1_evaluation_summary.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_v1_evaluation_summary_markdown(payload),
        encoding="utf-8",
    )
    return {
        "v1_evaluation_summary_json": str(json_path),
        "v1_evaluation_summary_markdown": str(markdown_path),
    }


def render_v1_evaluation_summary_markdown(payload: dict[str, Any]) -> str:
    summary = _dict(payload.get("summary"))
    lines = [
        "# V1 Evaluation Summary",
        "",
        f"- Status: `{_markdown_cell(payload.get('status') or 'unknown')}`",
        f"- Reason: `{_markdown_cell(payload.get('reason') or 'none')}`",
        f"- Readiness Audit: `{_markdown_cell(summary.get('readiness_status') or 'missing')}`",
        f"- Metrics: {_int(summary.get('measured_metric_count'))} measured, {_int(summary.get('proxy_metric_count'))} proxy, {_int(summary.get('missing_metric_count'))} missing",
        f"- Agent Loop: `{_markdown_cell(payload.get('agent_loop') or V1_AGENT_LOOP)}`",
        "",
        "## Metrics",
        "",
        "| Metric | Status | Value | Evidence | Note |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for metric_value in _list(payload.get("metrics")):
        metric = _dict(metric_value)
        lines.append(
            "| "
            f"`{_markdown_cell(metric.get('metric_id'))}` | "
            f"`{_markdown_cell(metric.get('evidence_status'))}` | "
            f"{_markdown_cell(_format_metric_value(metric))} | "
            f"{_markdown_cell(_format_list(_list(metric.get('evidence'))))} | "
            f"{_markdown_cell(metric.get('reason'))} |"
        )
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {action}")
    if not _list(payload.get("next_actions")):
        lines.append("- V1 metric evidence is complete.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build the V1 metric summary from readiness and suite evidence."
    )
    parser.add_argument("output_dir", help="Directory for summary artifacts.")
    parser.add_argument("--readiness-audit", default="")
    parser.add_argument("--onboarding-suite", default="")
    parser.add_argument("--repair-metrics", default="")
    parser.add_argument("--repair-catalog-audit", default="")
    parser.add_argument("--localization-report", default="")
    parser.add_argument("--llm-cost-report", default="")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
    )
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="Exit with status 1 unless every required metric is measured.",
    )
    args = parser.parse_args(argv)

    paths = {
        "readiness_audit": args.readiness_audit,
        "onboarding_suite": args.onboarding_suite,
        "repair_metrics": args.repair_metrics,
        "repair_catalog_audit": args.repair_catalog_audit,
        "localization_report": args.localization_report,
        "llm_cost_report": args.llm_cost_report,
    }
    payload = build_v1_evaluation_summary(
        readiness_audit=_load_optional_json(args.readiness_audit),
        onboarding_suite=_load_optional_json(args.onboarding_suite),
        repair_metrics=_load_optional_json(args.repair_metrics),
        repair_catalog_audit=_load_optional_json(args.repair_catalog_audit),
        localization_report=_load_optional_json(args.localization_report),
        llm_cost_report=_load_optional_json(args.llm_cost_report),
        source_paths={key: value for key, value in paths.items() if value},
    )
    write_v1_evaluation_summary_artifacts(payload, args.output_dir)
    if args.format == "markdown":
        print(render_v1_evaluation_summary_markdown(payload), end="")
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    if args.require_pass and payload["status"] != "pass":
        raise SystemExit(1)


def _onboarding_success_metric(onboarding: dict[str, Any]) -> dict[str, Any]:
    summary = _dict(onboarding.get("summary"))
    run_count = _int(summary.get("run_count"))
    passed_count = _first_int(
        summary.get("agent_passed_count"),
        summary.get("expectation_passed_count"),
    )
    if run_count <= 0:
        return _missing_metric(
            "onboarding_success_rate",
            "github_repo_intelligence_suite.json is missing or has no runs",
        )
    if bool(summary.get("suite_slice_applied")):
        return _metric(
            "onboarding_success_rate",
            "proxy",
            _rate(passed_count, run_count),
            evidence=["github_repo_intelligence_suite.summary", "suite_slice"],
            numerator=passed_count,
            denominator=run_count,
            reason=(
                "proxy: computed from a suite slice, not the full v1 onboarding "
                "manifest"
            ),
        )
    return _metric(
        "onboarding_success_rate",
        "measured",
        _rate(passed_count, run_count),
        evidence=["github_repo_intelligence_suite.summary"],
        numerator=passed_count,
        denominator=run_count,
        reason="computed from suite passed runs over total runs",
    )


def _topk_localization_metric(localization: dict[str, Any]) -> dict[str, Any]:
    summary = _dict(localization.get("summary")) or localization
    topk = _dict(summary.get("top_k_accuracy")) or _dict(
        summary.get("topk_localization_accuracy")
    )
    if topk:
        key = _largest_numeric_key(topk)
        return _metric(
            "topk_localization_accuracy",
            "measured",
            _float(topk.get(key)),
            evidence=["localization_report.top_k_accuracy"],
            k=_int(key),
            reason=f"computed from localization ground truth at k={key}",
        )
    if summary.get("top3") is not None:
        return _metric(
            "topk_localization_accuracy",
            "measured",
            _float(summary.get("top3")),
            evidence=["localization_report.summary.top3"],
            k=3,
            reason="computed from benchmark localization top3",
        )
    if summary.get("top1") is not None:
        return _metric(
            "topk_localization_accuracy",
            "measured",
            _float(summary.get("top1")),
            evidence=["localization_report.summary.top1"],
            k=1,
            reason="computed from benchmark localization top1",
        )
    return _missing_metric(
        "topk_localization_accuracy",
        "localization report with ground-truth Top-k metrics is missing",
    )


def _pass_at_metric(
    repair_metrics: dict[str, Any],
    metric_id: str,
    key: str,
) -> dict[str, Any]:
    success_at = _dict(repair_metrics.get("patch_success_at"))
    if not key or key not in success_at:
        return _missing_metric(
            metric_id,
            "llm_repair_metrics_report.patch_success_at is missing",
        )
    return _metric(
        metric_id,
        "measured",
        _float(success_at.get(key)),
        evidence=["llm_repair_metrics_report.patch_success_at"],
        k=_int(key),
        reason=f"computed from first successful sandbox rank at k={key}",
    )


def _reflection_uplift_metric(repair_metrics: dict[str, Any]) -> dict[str, Any]:
    reflection_case_rate = repair_metrics.get("reflection_success_case_rate")
    if reflection_case_rate is not None:
        return _metric(
            "reflection_uplift",
            "measured",
            _float(reflection_case_rate),
            evidence=["llm_repair_metrics_report.reflection_success_case_rate"],
            numerator=_int(repair_metrics.get("llm_reflection_success_count")),
            denominator=_int(repair_metrics.get("case_count")),
            reason="computed as cases recovered by reflection over all repair cases",
        )
    direct_rate = repair_metrics.get("llm_direct_success_rate")
    final_rate = repair_metrics.get("patch_success_case_rate")
    if direct_rate is not None and final_rate is not None:
        return _metric(
            "reflection_uplift",
            "measured",
            round(max(0.0, _float(final_rate) - _float(direct_rate)), 4),
            evidence=[
                "llm_repair_metrics_report.patch_success_case_rate",
                "llm_repair_metrics_report.llm_direct_success_rate",
            ],
            reason="computed as final patch success rate minus direct-only success rate",
        )
    if repair_metrics.get("reflection_success_rate") is None:
        return _missing_metric(
            "reflection_uplift",
            "reflection success metrics are missing",
        )
    return _metric(
        "reflection_uplift",
        "proxy",
        _float(repair_metrics.get("reflection_success_rate")),
        evidence=["llm_repair_metrics_report.reflection_success_rate"],
        numerator=_int(repair_metrics.get("llm_reflection_success_count")),
        denominator=_int(repair_metrics.get("reflection_attempt_case_count")),
        reason=(
            "proxy: current evidence reports reflection recovery rate, not a "
            "direct before/after uplift delta"
        ),
    )


def _blocker_accuracy_metric(catalog_audit: dict[str, Any]) -> dict[str, Any]:
    cases = [_dict(item) for item in _list(catalog_audit.get("cases"))]
    eligible = [
        case for case in cases if str(case.get("expected_blocker_category") or "")
    ]
    if eligible:
        matched = sum(1 for case in eligible if bool(case.get("blocker_category_matches")))
        return _metric(
            "blocker_accuracy",
            "measured",
            _rate(matched, len(eligible)),
            evidence=["llm_repair_case_catalog_audit.cases"],
            numerator=matched,
            denominator=len(eligible),
            reason="computed from expected vs observed blocker category",
        )
    counts = _dict(catalog_audit.get("counts"))
    blocker_count = _int(counts.get("llm_blocker_count"))
    mismatch_count = _int(counts.get("blocker_category_mismatch_count"))
    if blocker_count > 0:
        return _metric(
            "blocker_accuracy",
            "measured",
            _rate(blocker_count - mismatch_count, blocker_count),
            evidence=["llm_repair_case_catalog_audit.counts"],
            numerator=blocker_count - mismatch_count,
            denominator=blocker_count,
            reason="computed from catalog audit blocker mismatch count",
        )
    return _missing_metric(
        "blocker_accuracy",
        "repair catalog audit with blocker category matches is missing",
    )


def _sandbox_success_metric(repair_metrics: dict[str, Any]) -> dict[str, Any]:
    if repair_metrics.get("sandbox_pass_rate") is None:
        return _missing_metric(
            "sandbox_success_rate",
            "llm_repair_metrics_report.sandbox_pass_rate is missing",
        )
    return _metric(
        "sandbox_success_rate",
        "measured",
        _float(repair_metrics.get("sandbox_pass_rate")),
        evidence=["llm_repair_metrics_report.sandbox_pass_rate"],
        numerator=_int(repair_metrics.get("patch_validation_success_count")),
        denominator=_int(repair_metrics.get("patch_validation_executed_count")),
        reason="computed from sandbox-passed candidates over executed candidates",
    )


def _average_runtime_metric(
    onboarding: dict[str, Any],
    repair_metrics: dict[str, Any],
) -> dict[str, Any]:
    summary = _dict(onboarding.get("summary"))
    if summary.get("suite_run_elapsed_ms_average") is not None:
        if bool(summary.get("suite_slice_applied")):
            return _metric(
                "average_runtime_ms",
                "proxy",
                _float(summary.get("suite_run_elapsed_ms_average")),
                evidence=[
                    "github_repo_intelligence_suite.summary.suite_run_elapsed_ms_average",
                    "suite_slice",
                ],
                reason=(
                    "proxy: computed from a suite slice, not the full v1 "
                    "onboarding manifest"
                ),
            )
        return _metric(
            "average_runtime_ms",
            "measured",
            _float(summary.get("suite_run_elapsed_ms_average")),
            evidence=["github_repo_intelligence_suite.summary.suite_run_elapsed_ms_average"],
            reason="computed from suite run elapsed time",
        )
    if repair_metrics.get("average_runtime_seconds") is not None:
        return _metric(
            "average_runtime_ms",
            "measured",
            round(_float(repair_metrics.get("average_runtime_seconds")) * 1000, 2),
            evidence=["llm_repair_metrics_report.average_runtime_seconds"],
            reason="converted from repair metrics average runtime seconds",
        )
    return _missing_metric(
        "average_runtime_ms",
        "suite elapsed runtime evidence is missing",
    )


def _llm_cost_metric(
    onboarding: dict[str, Any],
    repair_metrics: dict[str, Any],
    llm_cost_report: dict[str, Any],
) -> dict[str, Any]:
    token_cost = _dict(repair_metrics.get("llm_token_cost"))
    if _int(token_cost.get("cost_case_count")) > 0:
        return _metric(
            "llm_cost_usd",
            "measured",
            _float(token_cost.get("total_estimated_cost")),
            evidence=["llm_repair_metrics_report.llm_token_cost"],
            reason="computed from repair metrics estimated LLM cost",
        )
    cost_report_token_cost = _dict(llm_cost_report.get("llm_token_cost"))
    if _int(cost_report_token_cost.get("cost_case_count")) > 0:
        return _metric(
            "llm_cost_usd",
            "measured",
            _float(cost_report_token_cost.get("total_estimated_cost")),
            evidence=["llm_cost_evidence.llm_token_cost"],
            reason="computed from standalone LLM token and pricing evidence",
        )
    summary = _dict(onboarding.get("summary"))
    if _int(summary.get("repository_llm_patch_cost_available_count")) > 0:
        return _metric(
            "llm_cost_usd",
            "measured",
            _float(summary.get("repository_llm_patch_estimated_cost_usd_total")),
            evidence=["github_repo_intelligence_suite.summary.repository_llm_patch_estimated_cost_usd_total"],
            reason="computed from suite LLM patch telemetry",
        )
    return _missing_metric(
        "llm_cost_usd",
        "LLM cost evidence is missing or provider pricing was not configured",
    )


def _metric(
    metric_id: str,
    evidence_status: str,
    value: float,
    *,
    evidence: list[str],
    reason: str,
    numerator: int | None = None,
    denominator: int | None = None,
    k: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "metric_id": metric_id,
        "evidence_status": evidence_status,
        "value": value,
        "evidence": evidence,
        "reason": reason,
    }
    if numerator is not None:
        payload["numerator"] = numerator
    if denominator is not None:
        payload["denominator"] = denominator
    if k is not None:
        payload["k"] = k
    return payload


def _missing_metric(metric_id: str, reason: str) -> dict[str, Any]:
    return {
        "metric_id": metric_id,
        "evidence_status": "missing_evidence",
        "value": None,
        "evidence": [],
        "reason": reason,
    }


def _next_actions(
    metrics: list[dict[str, Any]],
    readiness_status: str,
    missing_contracts: list[str],
) -> list[str]:
    actions: list[str] = []
    if readiness_status != "pass":
        actions.append("Run the v1 readiness dataset audit until it reaches pass.")
    missing = [
        str(metric.get("metric_id"))
        for metric in metrics
        if metric.get("evidence_status") == "missing_evidence"
    ]
    proxy = [
        str(metric.get("metric_id"))
        for metric in metrics
        if metric.get("evidence_status") == "proxy"
    ]
    if missing:
        actions.append(
            "Generate or attach evidence artifacts for missing metrics: "
            + ", ".join(missing)
            + "."
        )
    if proxy:
        actions.append(
            "Replace proxy metric evidence with direct measurements for: "
            + ", ".join(proxy)
            + "."
        )
    if missing_contracts:
        actions.append(
            "Add missing metric contracts to the v1 readiness audit: "
            + ", ".join(missing_contracts)
            + "."
        )
    return actions


def _largest_success_at_key(repair_metrics: dict[str, Any]) -> str:
    return _largest_numeric_key(_dict(repair_metrics.get("patch_success_at")))


def _largest_numeric_key(values: dict[str, Any]) -> str:
    numeric = sorted((_int(key), str(key)) for key in values if _int(key) > 0)
    return numeric[-1][1] if numeric else ""


def _format_metric_value(metric: dict[str, Any]) -> str:
    value = metric.get("value")
    if value is None:
        return "none"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _load_optional_json(path: str) -> dict[str, Any]:
    if not path:
        return {}
    candidate = Path(path)
    if not candidate.exists():
        return {}
    return _dict(json.loads(candidate.read_text(encoding="utf-8")))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _first_int(*values: Any) -> int:
    for value in values:
        if value is not None:
            return _int(value)
    return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _format_list(values: list[Any]) -> str:
    items = [str(value) for value in values if str(value)]
    return ", ".join(items) if items else "none"


def _markdown_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":  # pragma: no cover
    main()
