from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any


SHOWCASE_CLASSES = [
    "llm_direct_success",
    "llm_reflection_success",
    "llm_blocker",
]

P6_EVALUATION_TARGETS = {
    "case_count": 20,
    "llm_direct_success": 5,
    "llm_reflection_success": 3,
    "llm_blocker": 5,
}


def build_llm_repair_showcase_matrix(
    suite_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for suite in suite_reports:
        suite_name = str(suite.get("suite_name") or "")
        suite_path = str(suite.get("suite_report_path") or "")
        for run in _list(suite.get("runs")):
            rows.append(
                _showcase_row(
                    _dict(run),
                    suite_name=suite_name,
                    suite_report_path=suite_path,
                )
            )
    class_counts = Counter(str(row.get("class") or "") for row in rows)
    requirement_status = {
        "llm_direct_success": class_counts["llm_direct_success"] >= 1,
        "llm_reflection_success": class_counts["llm_reflection_success"] >= 1,
        "llm_blocker": class_counts["llm_blocker"] >= 1,
    }
    return {
        "status": "pass" if all(requirement_status.values()) else "incomplete",
        "reason": (
            "all_llm_repair_showcase_classes_present"
            if all(requirement_status.values())
            else "missing_required_llm_repair_showcase_classes"
        ),
        "required_classes": SHOWCASE_CLASSES,
        "requirement_status": requirement_status,
        "class_counts": dict(sorted(class_counts.items())),
        "case_count": len(rows),
        "matrix": rows,
    }


def build_llm_repair_evaluation_matrix(
    suite_reports: list[dict[str, Any]],
    *,
    targets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    showcase = build_llm_repair_showcase_matrix(suite_reports)
    return build_llm_repair_evaluation_matrix_from_showcase(
        showcase,
        targets=targets,
    )


def build_llm_repair_evaluation_matrix_from_showcase(
    showcase: dict[str, Any],
    *,
    targets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = [_dict(row) for row in _list(showcase.get("matrix"))]
    target_values = {
        **P6_EVALUATION_TARGETS,
        **_dict(targets),
    }
    metrics_report = build_llm_repair_metrics_report(rows, targets=target_values)
    target_summary = _dict(metrics_report.get("target_summary"))
    status = "pass" if bool(target_summary.get("all_targets_met", False)) else "incomplete"
    return {
        "status": status,
        "reason": (
            "p6_llm_repair_evaluation_targets_met"
            if status == "pass"
            else "p6_llm_repair_evaluation_targets_not_met"
        ),
        "targets": target_values,
        "case_count": len(rows),
        "class_counts": _dict(showcase.get("class_counts")),
        "showcase_status": str(showcase.get("status") or ""),
        "showcase_reason": str(showcase.get("reason") or ""),
        "metrics_report": metrics_report,
        "matrix": rows,
    }


def build_llm_repair_metrics_report(
    rows: list[dict[str, Any]],
    *,
    targets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target_values = {
        **P6_EVALUATION_TARGETS,
        **_dict(targets),
    }
    class_counts = Counter(str(row.get("class") or "unknown") for row in rows)
    case_count = len(rows)
    direct_success_count = class_counts["llm_direct_success"]
    reflection_success_count = class_counts["llm_reflection_success"]
    blocker_count = class_counts["llm_blocker"]
    sandbox_success_case_count = sum(
        1 for row in rows if _int(row.get("patch_validation_success_count", 0)) > 0
    )
    validation_attempt_case_count = sum(
        1
        for row in rows
        if _int(row.get("patch_validation_candidate_count", 0)) > 0
        or _int(row.get("patch_validation_executed_count", 0)) > 0
        or str(row.get("patch_validation_status") or "")
    )
    reflection_attempt_case_count = sum(
        1
        for row in rows
        if _int(row.get("reflection_candidate_count", 0)) > 0
        or str(row.get("reflection_mode") or "").lower() not in {"", "none"}
    )
    first_success_ranks = [
        _int(row.get("first_success_rank"))
        for row in rows
        if row.get("first_success_rank") is not None
    ]
    candidate_count = sum(_int(row.get("patch_validation_candidate_count", 0)) for row in rows)
    executed_count = sum(_int(row.get("patch_validation_executed_count", 0)) for row in rows)
    success_count = sum(_int(row.get("patch_validation_success_count", 0)) for row in rows)
    safety_blocked_count = sum(
        _int(row.get("patch_validation_safety_blocked_count", 0)) for row in rows
    )
    judge_candidate_count = sum(_int(row.get("patch_judge_candidate_count", 0)) for row in rows)
    judge_agreement_counts: Counter[str] = Counter()
    judge_verdict_counts: Counter[str] = Counter()
    for row in rows:
        judge_agreement_counts.update(
            _normalized_judge_agreement_counts(
                _dict(row.get("patch_judge_agreement_counts"))
            )
        )
        judge_verdict_counts.update(_dict(row.get("patch_judge_verdict_counts")))
    runtime_values = [
        _float(row.get("runtime_seconds"))
        for row in rows
        if row.get("runtime_seconds") is not None
    ]
    token_values = [
        _float(row.get("llm_token_count"))
        for row in rows
        if row.get("llm_token_count") is not None
    ]
    cost_values = [
        _float(row.get("llm_estimated_cost"))
        for row in rows
        if row.get("llm_estimated_cost") is not None
    ]
    target_checks = _evaluation_target_checks(
        targets=target_values,
        case_count=case_count,
        direct_success_count=direct_success_count,
        reflection_success_count=reflection_success_count,
        blocker_count=blocker_count,
    )
    loop_complete_count = sum(
        1 for row in rows if _agent_loop_trace_complete(_dict(row.get("agent_loop_evidence")))
    )
    blocker_distribution = Counter(
        str(row.get("blocker") or row.get("class") or "unknown") for row in rows
    )
    return {
        "status": "pass" if all(bool(check.get("passed")) for check in target_checks) else "incomplete",
        "reason": (
            "p6_llm_repair_metrics_targets_met"
            if all(bool(check.get("passed")) for check in target_checks)
            else "p6_llm_repair_metrics_targets_not_met"
        ),
        "targets": target_values,
        "target_checks": target_checks,
        "target_summary": {
            "all_targets_met": all(bool(check.get("passed")) for check in target_checks),
            "failed_target_count": sum(1 for check in target_checks if not bool(check.get("passed"))),
        },
        "case_count": case_count,
        "class_counts": dict(sorted(class_counts.items())),
        "llm_direct_success_count": direct_success_count,
        "llm_reflection_success_count": reflection_success_count,
        "llm_blocker_count": blocker_count,
        "llm_direct_success_rate": _rate(direct_success_count, case_count),
        "reflection_success_rate": _rate(
            reflection_success_count,
            reflection_attempt_case_count,
        ),
        "reflection_success_case_rate": _rate(reflection_success_count, case_count),
        "patch_success_case_count": sandbox_success_case_count,
        "patch_success_case_rate": _rate(sandbox_success_case_count, case_count),
        "patch_success_at": {
            "1": _success_at(first_success_ranks, 1),
            "3": _success_at(first_success_ranks, 3),
            "5": _success_at(first_success_ranks, 5),
        },
        "rank_evidence_case_count": len(first_success_ranks),
        "validation_attempt_case_count": validation_attempt_case_count,
        "reflection_attempt_case_count": reflection_attempt_case_count,
        "patch_validation_candidate_count": candidate_count,
        "patch_validation_executed_count": executed_count,
        "patch_validation_success_count": success_count,
        "sandbox_pass_rate": _rate(success_count, executed_count),
        "safety_gate_blocked_candidate_count": safety_blocked_count,
        "safety_gate_block_rate": _rate(safety_blocked_count, candidate_count),
        "patch_judge_candidate_count": judge_candidate_count,
        "judge_sandbox_agreement_counts": dict(sorted(judge_agreement_counts.items())),
        "judge_sandbox_agreement_rate": _rate(
            judge_agreement_counts.get("aligned", 0),
            sum(judge_agreement_counts.values()),
        ),
        "patch_judge_verdict_counts": dict(sorted(judge_verdict_counts.items())),
        "blocker_type_distribution": dict(sorted(blocker_distribution.items())),
        "agent_loop_trace_complete_count": loop_complete_count,
        "agent_loop_trace_complete_rate": _rate(loop_complete_count, case_count),
        "sandbox_authority": "sandbox_pytest_decides_success",
        "average_runtime_seconds": _average(runtime_values),
        "runtime_case_count": len(runtime_values),
        "llm_token_cost": {
            "total_tokens": round(sum(token_values), 4),
            "average_tokens_per_case": _average(token_values),
            "token_case_count": len(token_values),
            "total_estimated_cost": round(sum(cost_values), 6),
            "average_estimated_cost_per_case": _average(cost_values, digits=6),
            "cost_case_count": len(cost_values),
        },
    }


def render_llm_repair_showcase_matrix_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LLM Repair Showcase Matrix",
        "",
        f"- Status: `{_markdown_cell(payload.get('status'))}`",
        f"- Reason: `{_markdown_cell(payload.get('reason'))}`",
        f"- Case Count: {_int(payload.get('case_count', 0))}",
        f"- Class Counts: {_format_counts(_dict(payload.get('class_counts')))}",
        "",
        "## Requirement Status",
        "",
        "| Required Class | Present |",
        "| --- | ---: |",
    ]
    requirement_status = _dict(payload.get("requirement_status"))
    for class_name in SHOWCASE_CLASSES:
        lines.append(
            "| "
            f"`{_markdown_cell(class_name)}` | "
            f"{str(bool(requirement_status.get(class_name, False))).lower()} |"
        )
    lines.extend(
        [
            "",
            "## Cases",
            "",
            (
                "| Case | Suite | Repo | Class | Status | Patch Mode | LLM Patch | "
                "Provider | Model | LLM Candidates | Validation Successes | "
                "Reflection Successes | Repair Action | Reflection Action | "
                "Blocker | Report |"
            ),
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |",
        ]
    )
    for row in _list(payload.get("matrix")):
        item = _dict(row)
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(item.get("name")),
                    _markdown_cell(item.get("suite_name")),
                    _markdown_cell(item.get("repo")),
                    _markdown_cell(item.get("class")),
                    _markdown_cell(item.get("status")),
                    _markdown_cell(item.get("patch_generation_mode")),
                    _markdown_cell(item.get("llm_patch_status")),
                    _markdown_cell(item.get("llm_provider")),
                    _markdown_cell(item.get("llm_model")),
                    str(_int(item.get("llm_candidate_count", 0))),
                    str(_int(item.get("patch_validation_success_count", 0))),
                    str(_int(item.get("successful_reflection_count", 0))),
                    _markdown_cell(item.get("repair_action_id")),
                    _markdown_cell(item.get("reflection_action_id")),
                    _markdown_cell(item.get("blocker")),
                    _markdown_cell(item.get("report_path")),
                ]
            )
            + " |"
        )
    if not _list(payload.get("matrix")):
        lines.append("| none | none | none | none | none | none | none | none | none | 0 | 0 | 0 | none | none | none | none |")
    lines.extend(["", "## Agent Loop Evidence", ""])
    for row in _list(payload.get("matrix")):
        item = _dict(row)
        loop = _dict(item.get("agent_loop_evidence"))
        lines.extend(
            [
                f"### {_markdown_cell(item.get('name'))}",
                "",
                f"- Observe: {_markdown_cell(loop.get('observe'))}",
                f"- Plan: {_markdown_cell(loop.get('plan'))}",
                f"- Act: {_markdown_cell(loop.get('act'))}",
                f"- Verify: {_markdown_cell(loop.get('verify'))}",
                f"- Reflect: {_markdown_cell(loop.get('reflect'))}",
                f"- Replan: {_markdown_cell(loop.get('replan'))}",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def render_llm_repair_evaluation_matrix_markdown(payload: dict[str, Any]) -> str:
    metrics = _dict(payload.get("metrics_report"))
    lines = [
        "# LLM Repair Evaluation Matrix",
        "",
        f"- Status: `{_markdown_cell(payload.get('status'))}`",
        f"- Reason: `{_markdown_cell(payload.get('reason'))}`",
        f"- Case Count: {_int(payload.get('case_count', 0))}",
        f"- Class Counts: {_format_counts(_dict(payload.get('class_counts')))}",
        f"- Sandbox Authority: `{_markdown_cell(metrics.get('sandbox_authority') or 'sandbox_pytest_decides_success')}`",
        "",
        "## P6 Target Checks",
        "",
        "| Target | Actual | Expected | Passed |",
        "| --- | ---: | ---: | ---: |",
    ]
    for check in _list(metrics.get("target_checks")):
        item = _dict(check)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('name'))} | "
            f"{_markdown_cell(item.get('actual'))} | "
            f"{_markdown_cell(item.get('expected'))} | "
            f"{str(bool(item.get('passed'))).lower()} |"
        )
    lines.extend(
        [
            "",
            "## Metrics",
            "",
            f"- LLM Direct Success Rate: {_float(metrics.get('llm_direct_success_rate', 0.0)):.4f}",
            f"- Reflection Success Rate: {_float(metrics.get('reflection_success_rate', 0.0)):.4f}",
            f"- Patch Success@1/@3/@5: {_format_success_at(_dict(metrics.get('patch_success_at')))}",
            f"- Sandbox Pass Rate: {_float(metrics.get('sandbox_pass_rate', 0.0)):.4f}",
            f"- Safety Gate Block Rate: {_float(metrics.get('safety_gate_block_rate', 0.0)):.4f}",
            f"- Judge-Sandbox Agreement Rate: {_float(metrics.get('judge_sandbox_agreement_rate', 0.0)):.4f}",
            f"- Average Runtime Seconds: {_float(metrics.get('average_runtime_seconds', 0.0)):.4f}",
            "",
            "## Cases",
            "",
            (
                "| Case | Repo | Class | LLM Candidates | First Success Rank | "
                "Sandbox Successes | Judge Agreement | Agent Loop | Artifacts |"
            ),
            "| --- | --- | --- | ---: | --- | ---: | --- | ---: | --- |",
        ]
    )
    for row in _list(payload.get("matrix")):
        item = _dict(row)
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(item.get("name")),
                    _markdown_cell(item.get("repo")),
                    _markdown_cell(item.get("class")),
                    str(_int(item.get("llm_candidate_count", 0))),
                    _markdown_cell(item.get("first_success_rank") or "none"),
                    str(_int(item.get("patch_validation_success_count", 0))),
                    _format_counts(_dict(item.get("patch_judge_agreement_counts"))),
                    str(_agent_loop_trace_complete(_dict(item.get("agent_loop_evidence")))).lower(),
                    _markdown_cell(item.get("report_path")),
                ]
            )
            + " |"
        )
    if not _list(payload.get("matrix")):
        lines.append("| none | none | none | 0 | none | 0 | none | false | none |")
    return "\n".join(lines) + "\n"


def render_llm_repair_metrics_report_markdown(payload: dict[str, Any]) -> str:
    token_cost = _dict(payload.get("llm_token_cost"))
    lines = [
        "# LLM Repair Metrics Report",
        "",
        f"- Status: `{_markdown_cell(payload.get('status'))}`",
        f"- Reason: `{_markdown_cell(payload.get('reason'))}`",
        f"- Case Count: {_int(payload.get('case_count', 0))}",
        f"- Direct Successes: {_int(payload.get('llm_direct_success_count', 0))}",
        f"- Reflection Successes: {_int(payload.get('llm_reflection_success_count', 0))}",
        f"- Blocker Cases: {_int(payload.get('llm_blocker_count', 0))}",
        f"- Patch Success Cases: {_int(payload.get('patch_success_case_count', 0))}",
        f"- Rank Evidence Cases: {_int(payload.get('rank_evidence_case_count', 0))}",
        f"- Patch Success@1/@3/@5: {_format_success_at(_dict(payload.get('patch_success_at')))}",
        f"- Sandbox Pass Rate: {_float(payload.get('sandbox_pass_rate', 0.0)):.4f}",
        f"- Safety Gate Block Rate: {_float(payload.get('safety_gate_block_rate', 0.0)):.4f}",
        f"- Judge-Sandbox Agreement: {_format_counts(_dict(payload.get('judge_sandbox_agreement_counts')))}",
        f"- Judge-Sandbox Agreement Rate: {_float(payload.get('judge_sandbox_agreement_rate', 0.0)):.4f}",
        f"- Average Runtime Seconds: {_float(payload.get('average_runtime_seconds', 0.0)):.4f}",
        f"- Average LLM Tokens Per Case: {_float(token_cost.get('average_tokens_per_case', 0.0)):.4f}",
        f"- Average Estimated LLM Cost Per Case: {_float(token_cost.get('average_estimated_cost_per_case', 0.0)):.6f}",
        "",
        "## Target Checks",
        "",
        "| Target | Actual | Expected | Passed |",
        "| --- | ---: | ---: | ---: |",
    ]
    for check in _list(payload.get("target_checks")):
        item = _dict(check)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('name'))} | "
            f"{_markdown_cell(item.get('actual'))} | "
            f"{_markdown_cell(item.get('expected'))} | "
            f"{str(bool(item.get('passed'))).lower()} |"
        )
    lines.extend(
        [
            "",
            "## Blocker Distribution",
            "",
            "| Blocker | Count |",
            "| --- | ---: |",
        ]
    )
    for key, value in sorted(_dict(payload.get("blocker_type_distribution")).items()):
        lines.append(f"| {_markdown_cell(key)} | {_int(value)} |")
    if not _dict(payload.get("blocker_type_distribution")):
        lines.append("| none | 0 |")
    return "\n".join(lines) + "\n"


def write_llm_repair_showcase_matrix_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "llm_repair_showcase_matrix.json"
    markdown_path = root / "llm_repair_showcase_matrix.md"
    evaluation = build_llm_repair_evaluation_matrix_from_showcase(payload)
    evaluation_json_path = root / "llm_repair_evaluation_matrix.json"
    evaluation_markdown_path = root / "llm_repair_evaluation_matrix.md"
    metrics_report = _dict(evaluation.get("metrics_report"))
    metrics_json_path = root / "llm_repair_metrics_report.json"
    metrics_markdown_path = root / "llm_repair_metrics_report.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_llm_repair_showcase_matrix_markdown(payload),
        encoding="utf-8",
    )
    evaluation_json_path.write_text(
        json.dumps(evaluation, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    evaluation_markdown_path.write_text(
        render_llm_repair_evaluation_matrix_markdown(evaluation),
        encoding="utf-8",
    )
    metrics_json_path.write_text(
        json.dumps(metrics_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    metrics_markdown_path.write_text(
        render_llm_repair_metrics_report_markdown(metrics_report),
        encoding="utf-8",
    )
    return {
        "llm_repair_showcase_matrix_json": str(json_path),
        "llm_repair_showcase_matrix_markdown": str(markdown_path),
        "llm_repair_evaluation_matrix_json": str(evaluation_json_path),
        "llm_repair_evaluation_matrix_markdown": str(evaluation_markdown_path),
        "llm_repair_metrics_report_json": str(metrics_json_path),
        "llm_repair_metrics_report_markdown": str(metrics_markdown_path),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a three-class LLM repair showcase matrix from GitHub repo "
            "intelligence suite artifacts."
        )
    )
    parser.add_argument(
        "suite_report",
        nargs="+",
        help=(
            "Path to github_repo_intelligence_suite.json, or a directory "
            "containing that file."
        ),
    )
    parser.add_argument("output_dir")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Exit non-zero unless direct-success, reflection-success, and blocker cases are all present.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    suite_reports = [_load_suite_report(Path(path)) for path in args.suite_report]
    matrix = build_llm_repair_showcase_matrix(suite_reports)
    write_llm_repair_showcase_matrix_artifacts(matrix, args.output_dir)
    if args.format == "json":
        print(json.dumps(matrix, indent=2, ensure_ascii=False))
    else:
        print(render_llm_repair_showcase_matrix_markdown(matrix))
    if args.require_complete and matrix["status"] != "pass":
        raise SystemExit(1)


def _load_suite_report(path: Path) -> dict[str, Any]:
    report_path = (
        path / "github_repo_intelligence_suite.json"
        if path.is_dir()
        else path
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Suite report must be a JSON object: {report_path}")
    payload["suite_report_path"] = str(report_path)
    return payload


def _showcase_row(
    run: dict[str, Any],
    *,
    suite_name: str,
    suite_report_path: str,
) -> dict[str, Any]:
    metrics = _dict(run.get("metrics"))
    llm_candidate_count = _first_int(
        metrics.get("repository_patch_generator_llm_candidate_count"),
        metrics.get("repository_patch_generator_llm_count"),
        metrics.get("repository_test_patch_generator_llm_candidate_count"),
    )
    validation_success_count = _int(
        metrics.get("repository_test_patch_validation_success_count", 0)
    )
    successful_reflection_count = _first_int(
        metrics.get("repository_test_patch_validation_successful_reflection_count"),
        metrics.get(
            "repository_test_patch_validation_successful_regression_reflection_count"
        ),
    )
    blocker = str(
        metrics.get("blocker")
        or metrics.get("repository_llm_reflection_blocker")
        or (
            metrics.get("repository_llm_patch_generation_reason")
            if str(metrics.get("repository_llm_patch_generation_status") or "").lower()
            in {"blocked", "unavailable", "failed"}
            else ""
        )
        or ""
    )
    patch_mode = str(metrics.get("repository_patch_generation_mode") or "")
    llm_patch_status = str(
        metrics.get("repository_llm_patch_generation_status") or ""
    )
    repair_action_id = _repair_action_id(
        patch_mode=patch_mode,
        llm_patch_status=llm_patch_status,
    )
    reflection_action_id = _reflection_action_id(metrics)
    row_class = _classify_case(
        metrics,
        llm_candidate_count=llm_candidate_count,
        validation_success_count=validation_success_count,
        successful_reflection_count=successful_reflection_count,
        blocker=blocker,
    )
    return {
        "name": str(run.get("name") or ""),
        "suite_name": suite_name,
        "suite_report_path": suite_report_path,
        "repo": str(run.get("repo") or ""),
        "output_dir": str(run.get("output_dir") or ""),
        "report_path": str(run.get("report_path") or ""),
        "status": str(run.get("status") or metrics.get("status") or ""),
        "passed": bool(run.get("passed", False)),
        "class": row_class,
        "repair_action_id": repair_action_id,
        "reflection_action_id": reflection_action_id,
        "class_reason": _class_reason(
            row_class,
            llm_candidate_count=llm_candidate_count,
            validation_success_count=validation_success_count,
            successful_reflection_count=successful_reflection_count,
            blocker=blocker,
        ),
        "patch_generation_mode": patch_mode,
        "llm_patch_status": llm_patch_status,
        "llm_provider": str(metrics.get("repository_llm_patch_provider") or ""),
        "llm_model": str(metrics.get("repository_llm_patch_model") or ""),
        "llm_api_key_present": bool(
            metrics.get("repository_llm_patch_api_key_present", False)
        ),
        "llm_candidate_count": llm_candidate_count,
        "patch_validation_status": str(
            metrics.get("repository_test_patch_validation_status") or ""
        ),
        "patch_validation_candidate_count": _first_int(
            metrics.get("repository_test_patch_validation_candidate_count"),
            metrics.get("repository_test_patch_validation_input_candidate_count"),
            metrics.get("phase4_search_candidate_count"),
        ),
        "patch_validation_executed_count": _first_int(
            metrics.get("repository_test_patch_validation_executed_count"),
            metrics.get("phase4_search_executed_count"),
        ),
        "patch_validation_success_count": validation_success_count,
        "patch_validation_safety_blocked_count": _int(
            metrics.get(
                "repository_test_patch_validation_safety_blocked_candidate_count",
                0,
            )
        ),
        "first_success_rank": _first_success_rank_from_metrics(metrics),
        "patch_judge_mode": str(
            metrics.get("repository_test_patch_judge_mode") or ""
        ),
        "patch_judge_status": str(
            metrics.get("repository_test_patch_judge_status") or ""
        ),
        "patch_judge_candidate_count": _int(
            metrics.get("repository_test_patch_judge_candidate_count", 0)
        ),
        "patch_judge_verdict_counts": _dict(
            metrics.get("repository_test_patch_judge_verdict_counts")
        ),
        "patch_judge_agreement_counts": _normalized_judge_agreement_counts(
            _dict(metrics.get("repository_test_patch_judge_agreement_counts"))
        ),
        "patch_judge_authority": str(
            metrics.get("repository_test_patch_judge_authority")
            or "sandbox_pytest_decides_success"
        ),
        "reflection_mode": str(
            metrics.get("repository_test_patch_validation_reflection_mode")
            or ""
        ),
        "reflection_candidate_count": _int(
            metrics.get("repository_test_patch_validation_reflection_candidate_count", 0)
        ),
        "successful_reflection_count": successful_reflection_count,
        "blocker": blocker,
        "next_action": str(
            metrics.get("agent_answers_next_action")
            or metrics.get("analysis_next_action")
            or metrics.get("next_action")
            or ""
        ),
        "runtime_seconds": _first_optional_float(
            run.get("runtime_seconds"),
            run.get("elapsed_seconds"),
            metrics.get("runtime_seconds"),
            metrics.get("elapsed_seconds"),
            metrics.get("duration_seconds"),
            metrics.get("command_runtime_seconds"),
            metrics.get("repository_runtime_seconds"),
            metrics.get("agent_runtime_seconds"),
        ),
        "llm_token_count": _llm_token_count(metrics),
        "llm_estimated_cost": _first_optional_float(
            metrics.get("llm_estimated_cost"),
            metrics.get("llm_total_cost"),
            metrics.get("repository_llm_patch_cost"),
            metrics.get("repository_llm_reflection_cost"),
            metrics.get("repository_test_patch_judge_cost"),
        ),
        "artifact_paths": _artifact_paths(run, metrics),
        "agent_loop_evidence": _agent_loop_evidence(
            metrics,
            row_class=row_class,
            llm_candidate_count=llm_candidate_count,
            validation_success_count=validation_success_count,
            successful_reflection_count=successful_reflection_count,
            blocker=blocker,
            repair_action_id=repair_action_id,
            reflection_action_id=reflection_action_id,
        ),
    }


def _classify_case(
    metrics: dict[str, Any],
    *,
    llm_candidate_count: int,
    validation_success_count: int,
    successful_reflection_count: int,
    blocker: str,
) -> str:
    patch_mode = str(metrics.get("repository_patch_generation_mode") or "").lower()
    llm_status = str(
        metrics.get("repository_llm_patch_generation_status") or ""
    ).lower()
    if successful_reflection_count > 0:
        return "llm_reflection_success"
    if (
        patch_mode in {"llm", "hybrid"}
        and llm_status == "pass"
        and llm_candidate_count > 0
        and validation_success_count > 0
    ):
        return "llm_direct_success"
    if blocker or llm_status in {"blocked", "unavailable", "failed"}:
        return "llm_blocker"
    if validation_success_count <= 0:
        return "llm_blocker"
    return "llm_blocker"


def _class_reason(
    row_class: str,
    *,
    llm_candidate_count: int,
    validation_success_count: int,
    successful_reflection_count: int,
    blocker: str,
) -> str:
    if row_class == "llm_reflection_success":
        return (
            "A refined LLM reflection candidate passed sandbox validation "
            f"({successful_reflection_count} successes)."
        )
    if row_class == "llm_direct_success":
        return (
            "An LLM-generated candidate passed sandbox validation without a "
            "recorded reflection success."
        )
    if blocker:
        return f"Agent reported blocker: {blocker}."
    return (
        "No validated LLM repair success was recorded "
        f"(llm_candidates={llm_candidate_count}, "
        f"validation_successes={validation_success_count})."
    )


def _agent_loop_evidence(
    metrics: dict[str, Any],
    *,
    row_class: str,
    llm_candidate_count: int,
    validation_success_count: int,
    successful_reflection_count: int,
    blocker: str,
    repair_action_id: str,
    reflection_action_id: str,
) -> dict[str, str]:
    patch_mode = str(metrics.get("repository_patch_generation_mode") or "none")
    llm_status = str(
        metrics.get("repository_llm_patch_generation_status") or "none"
    )
    provider = str(metrics.get("repository_llm_patch_provider") or "none")
    model = str(metrics.get("repository_llm_patch_model") or "none")
    action = str(
        metrics.get("controller_action_id")
        or metrics.get("agent_auto_last_action_id")
        or metrics.get("agent_auto_action_id")
        or "none"
    )
    patch_validation_status = str(
        metrics.get("repository_test_patch_validation_status") or "none"
    )
    reflection_mode = _reflection_mode_label(metrics)
    next_action = str(
        metrics.get("agent_answers_next_action")
        or metrics.get("analysis_next_action")
        or metrics.get("next_action")
        or "none"
    )
    return {
        "observe": (
            f"patch_mode={patch_mode}, llm_status={llm_status}, "
            f"provider={provider}, model={model}, blocker={blocker or 'none'}"
        ),
        "plan": (
            f"classify as {row_class}; repair_action={repair_action_id or 'none'}; "
            f"reflection_action={reflection_action_id or 'none'}; "
            f"controller_next_action={action}; LLM repair remains gated by "
            "explicit provider/model/key audit"
        ),
        "act": (
            f"llm_candidates={llm_candidate_count}; "
            f"patch_generation_status={llm_status}; "
            f"rule_candidates={_int(metrics.get('repository_patch_generator_rule_count') or 0)}"
        ),
        "verify": (
            f"sandbox_validation_status={patch_validation_status}; "
            f"successes={validation_success_count}; "
            f"patch_judge={metrics.get('repository_test_patch_judge_mode') or 'none'}/"
            f"{metrics.get('repository_test_patch_judge_status') or 'none'}; "
            "authority=sandbox_pytest_decides_success"
        ),
        "reflect": (
            f"reflection_mode={reflection_mode}; "
            f"successful_reflections={successful_reflection_count}"
        ),
        "replan": next_action,
    }


def _repair_action_id(*, patch_mode: str, llm_patch_status: str) -> str:
    mode = str(patch_mode or "").lower()
    status = str(llm_patch_status or "").lower()
    if mode == "llm":
        return (
            "configure_llm_patch_api_key"
            if status in {"blocked", "unavailable", "failed"}
            else "generate_llm_patch_candidates"
        )
    if mode == "hybrid":
        return "generate_hybrid_patch_candidates"
    return ""


def _reflection_action_id(metrics: dict[str, Any]) -> str:
    reflection_mode = _reflection_mode_label(metrics).lower()
    reflection_count = _first_int(
        metrics.get("repository_test_patch_validation_reflection_candidate_count"),
        metrics.get(
            "repository_test_patch_validation_successful_reflection_count"
        ),
    )
    if reflection_mode == "llm" and reflection_count > 0:
        return "run_llm_patch_reflection_loop"
    return ""


def _reflection_mode_label(metrics: dict[str, Any]) -> str:
    explicit = str(
        metrics.get("repository_test_patch_validation_reflection_mode") or ""
    ).lower()
    if explicit:
        return explicit
    status = str(metrics.get("repository_llm_reflection_status") or "").lower()
    provider = str(metrics.get("repository_llm_reflection_provider") or "")
    reflection_count = _first_int(
        metrics.get("repository_test_patch_validation_reflection_candidate_count"),
        metrics.get(
            "repository_test_patch_validation_successful_reflection_count"
        ),
    )
    if reflection_count > 0 and (provider or status in {"ready", "pass"}):
        return "llm"
    return status or "none"


def _artifact_paths(run: dict[str, Any], metrics: dict[str, Any]) -> dict[str, str]:
    keys = [
        "agent_controller_json",
        "agent_controller_markdown",
        "controller_report_path",
        "repository_test_patch_candidates_json",
        "repository_test_patch_candidates_markdown",
        "repository_test_patch_validation_json",
        "repository_test_patch_validation_markdown",
        "reflection_trace_json",
        "reflection_trace_markdown",
    ]
    paths = {
        "suite_run_report": str(run.get("report_path") or ""),
        "output_dir": str(run.get("output_dir") or ""),
    }
    for key in keys:
        value = str(metrics.get(key) or "")
        if value:
            paths[key] = value
    return paths


def _evaluation_target_checks(
    *,
    targets: dict[str, Any],
    case_count: int,
    direct_success_count: int,
    reflection_success_count: int,
    blocker_count: int,
) -> list[dict[str, Any]]:
    values = {
        "case_count": case_count,
        "llm_direct_success": direct_success_count,
        "llm_reflection_success": reflection_success_count,
        "llm_blocker": blocker_count,
    }
    checks: list[dict[str, Any]] = []
    for name in (
        "case_count",
        "llm_direct_success",
        "llm_reflection_success",
        "llm_blocker",
    ):
        actual = _int(values.get(name, 0))
        expected = _int(targets.get(name, 0))
        checks.append(
            {
                "name": name,
                "actual": actual,
                "expected": expected,
                "passed": actual >= expected,
            }
        )
    return checks


def _first_success_rank_from_metrics(metrics: dict[str, Any]) -> int | None:
    for key in (
        "repository_test_patch_validation_first_success_rank",
        "repository_patch_first_success_rank",
        "phase4_search_first_success_rank",
        "search_budget_first_success_rank",
        "first_success_rank",
    ):
        value = metrics.get(key)
        if value in (None, "", "none"):
            continue
        rank = _int(value)
        if rank > 0:
            return rank
    return None


def _normalized_judge_agreement_counts(
    counts: dict[str, Any],
) -> dict[str, int]:
    aliases = {
        "judge_more_optimistic": "judge_overoptimistic",
        "judge_over_optimistic": "judge_overoptimistic",
        "overoptimistic": "judge_overoptimistic",
        "judge_more_conservative": "judge_more_conservative",
        "conservative": "judge_more_conservative",
        "aligned": "aligned",
    }
    normalized: Counter[str] = Counter()
    for key, value in counts.items():
        raw = str(key or "unknown").strip().lower()
        normalized[aliases.get(raw, raw or "unknown")] += _int(value)
    return dict(sorted(normalized.items()))


def _agent_loop_trace_complete(loop: dict[str, Any]) -> bool:
    return all(str(loop.get(step) or "").strip() for step in (
        "observe",
        "plan",
        "act",
        "verify",
        "reflect",
        "replan",
    ))


def _success_at(ranks: list[int], budget: int) -> float:
    if not ranks:
        return 0.0
    return _rate(sum(1 for rank in ranks if rank <= budget), len(ranks))


def _llm_token_count(metrics: dict[str, Any]) -> float | None:
    direct = _first_optional_float(
        metrics.get("llm_total_tokens"),
        metrics.get("repository_llm_total_tokens"),
        metrics.get("repository_llm_patch_total_tokens"),
    )
    if direct is not None:
        return direct
    parts = [
        _first_optional_float(
            metrics.get("repository_llm_patch_prompt_tokens"),
            metrics.get("repository_llm_patch_input_tokens"),
        ),
        _first_optional_float(
            metrics.get("repository_llm_patch_completion_tokens"),
            metrics.get("repository_llm_patch_output_tokens"),
        ),
        _first_optional_float(
            metrics.get("repository_llm_reflection_prompt_tokens"),
            metrics.get("repository_llm_reflection_input_tokens"),
        ),
        _first_optional_float(
            metrics.get("repository_llm_reflection_completion_tokens"),
            metrics.get("repository_llm_reflection_output_tokens"),
        ),
        _first_optional_float(
            metrics.get("repository_test_patch_judge_prompt_tokens"),
            metrics.get("repository_test_patch_judge_input_tokens"),
        ),
        _first_optional_float(
            metrics.get("repository_test_patch_judge_completion_tokens"),
            metrics.get("repository_test_patch_judge_output_tokens"),
        ),
    ]
    known = [value for value in parts if value is not None]
    if not known:
        return None
    return sum(known)


def _first_int(*values: Any) -> int:
    for value in values:
        if value is not None:
            return _int(value)
    return 0


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


def _first_optional_float(*values: Any) -> float | None:
    for value in values:
        if value in (None, "", "none"):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _rate(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _average(values: list[float], *, digits: int = 4) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), digits)


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(
        f"{key}={_int(value)}" for key, value in sorted(counts.items())
    )


def _format_success_at(values: dict[str, Any]) -> str:
    if not values:
        return "1=0.0000, 3=0.0000, 5=0.0000"
    return ", ".join(
        f"{key}={_float(value):.4f}" for key, value in sorted(values.items())
    )


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


if __name__ == "__main__":
    main()
