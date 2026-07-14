from __future__ import annotations

import argparse
from collections import Counter
import json
import re
from pathlib import Path
from typing import Any


_CODE_FAILURE_CATEGORIES = {
    "syntax_error",
    "test_assertion_failure",
}

_ENVIRONMENT_FAILURE_CATEGORIES = {
    "command_usage_error",
    "framework_configuration_error",
    "import_path_error",
    "missing_dependency",
    "missing_native_extension",
    "missing_pytest_fixture",
    "missing_test_runner",
    "no_tests_collected",
    "pytest_collection_error",
    "pytest_warning_as_error",
    "timeout",
    "tox_missing_python_interpreter",
}


def evaluate_unfamiliar_repository_suite(
    manifest_payload: dict[str, Any],
    suite_payload: dict[str, Any],
) -> dict[str, Any]:
    manifest_runs = [_dict(item) for item in _list(manifest_payload.get("runs"))]
    suite_runs = {
        str(_dict(item).get("name") or ""): _dict(item)
        for item in _list(suite_payload.get("runs"))
        if str(_dict(item).get("name") or "")
    }
    rows: list[dict[str, Any]] = []
    for entry in manifest_runs:
        name = str(entry.get("name") or "")
        run = suite_runs.get(name, {})
        metrics = _dict(run.get("metrics"))
        report_path = Path(str(run.get("report_path") or ""))
        structured_report = bool(
            run
            and not str(run.get("error") or "")
            and str(run.get("status") or "") not in {"", "command_error"}
            and report_path.is_file()
        )
        static_success = bool(
            _int(metrics.get("imported_source_count")) > 0
            and bool(metrics.get("repository_structure_modeled", False))
            and bool(metrics.get("repo_graph_ready", False))
        )
        test_command_discovered = bool(
            str(metrics.get("planned_repository_test_command") or "")
        )
        test_started = bool(
            metrics.get("planned_repository_test_result_executed", False)
        )
        failure_category = str(
            metrics.get("planned_repository_test_failure_category") or ""
        )
        failure_layer = _failure_layer(failure_category)
        compatibility_status = str(
            metrics.get("repository_compatibility_status") or "unknown"
        )
        compatibility_reason = str(
            metrics.get("repository_compatibility_termination_reason") or ""
        )
        primary_blocker = str(
            metrics.get("repository_compatibility_primary_blocker")
            or metrics.get("blocker")
            or ""
        )
        outcome, termination_reason = _outcome(
            structured_report=structured_report,
            static_success=static_success,
            test_started=test_started,
            test_status=str(metrics.get("planned_repository_test_result_status") or ""),
            compatibility_status=compatibility_status,
            compatibility_reason=compatibility_reason,
            primary_blocker=primary_blocker,
            run_error=str(run.get("error") or ""),
        )
        rows.append(
            {
                "name": name,
                "repo": str(entry.get("repo") or ""),
                "commit_sha": str(entry.get("ref") or ""),
                "fixed_sha_valid": _is_commit_sha(str(entry.get("ref") or "")),
                "categories": [str(item) for item in _list(entry.get("categories"))],
                "structured_report": structured_report,
                "static_analysis_success": static_success,
                "source_roots_discovered": _int(
                    metrics.get("repository_source_root_count")
                )
                > 0,
                "test_roots_discovered": _int(
                    metrics.get("repository_test_root_count")
                )
                > 0,
                "test_command_discovered": test_command_discovered,
                "test_started": test_started,
                "test_status": str(
                    metrics.get("planned_repository_test_result_status") or ""
                ),
                "test_failure_category": failure_category,
                "failure_layer": failure_layer,
                "compatibility_status": compatibility_status,
                "compatibility_reason": compatibility_reason,
                "primary_blocker": primary_blocker,
                "install_risk": str(
                    metrics.get("repository_install_risk") or "unknown"
                ),
                "python_compatibility": str(
                    metrics.get("repository_python_compatibility_status")
                    or "unknown"
                ),
                "dependency_access_blocker_count": _int(
                    metrics.get("repository_dependency_access_blocker_count")
                ),
                "outcome": outcome,
                "termination_reason": termination_reason,
                "elapsed_ms": _int(run.get("elapsed_ms")),
                "existing_report_reuse": bool(
                    metrics.get("existing_report_reuse", False)
                ),
                "report_path": str(report_path) if str(run.get("report_path") or "") else "",
            }
        )

    counts = Counter(str(row["outcome"]) for row in rows)
    blocker_rows = [row for row in rows if row["outcome"] in {"partial", "blocked"}]
    failure_rows = [
        row
        for row in rows
        if row["test_started"] and row["test_status"] not in {"", "pass"}
    ]
    categories = Counter(
        category for row in rows for category in _list(row.get("categories"))
    )
    elapsed_total = sum(_int(row["elapsed_ms"]) for row in rows)
    report_reuse_count = sum(1 for row in rows if row["existing_report_reuse"])
    if report_reuse_count == 0:
        elapsed_semantics = "suite_execution_elapsed"
    elif report_reuse_count == len(rows):
        elapsed_semantics = "report_reuse_overhead"
    else:
        elapsed_semantics = "mixed_execution_and_reuse_not_comparable"
    metrics = {
        "case_count": len(rows),
        "unique_repository_count": len({str(row["repo"]) for row in rows}),
        "fixed_sha_count": sum(1 for row in rows if row["fixed_sha_valid"]),
        "structured_report_count": sum(1 for row in rows if row["structured_report"]),
        "static_analysis_success_count": sum(
            1 for row in rows if row["static_analysis_success"]
        ),
        "source_root_discovery_count": sum(
            1 for row in rows if row["source_roots_discovered"]
        ),
        "test_root_discovery_count": sum(
            1 for row in rows if row["test_roots_discovered"]
        ),
        "test_command_discovery_count": sum(
            1 for row in rows if row["test_command_discovered"]
        ),
        "test_start_count": sum(1 for row in rows if row["test_started"]),
        "classified_blocker_count": sum(
            1 for row in blocker_rows if str(row["termination_reason"])
        ),
        "blocker_case_count": len(blocker_rows),
        "classified_test_failure_layer_count": sum(
            1 for row in failure_rows if row["failure_layer"] != "unknown"
        ),
        "test_failure_case_count": len(failure_rows),
        "outcome_counts": dict(sorted(counts.items())),
        "failure_layer_counts": dict(
            sorted(Counter(str(row["failure_layer"]) for row in failure_rows).items())
        ),
        "category_counts": dict(sorted(categories.items())),
        "structured_report_rate": _rate(
            sum(1 for row in rows if row["structured_report"]), len(rows)
        ),
        "static_analysis_success_rate": _rate(
            sum(1 for row in rows if row["static_analysis_success"]), len(rows)
        ),
        "source_root_discovery_rate": _rate(
            sum(1 for row in rows if row["source_roots_discovered"]), len(rows)
        ),
        "test_command_discovery_rate": _rate(
            sum(1 for row in rows if row["test_command_discovered"]), len(rows)
        ),
        "test_start_rate": _rate(
            sum(1 for row in rows if row["test_started"]), len(rows)
        ),
        "blocker_classification_rate": _rate(
            sum(1 for row in blocker_rows if str(row["termination_reason"])),
            len(blocker_rows),
            empty=1.0,
        ),
        "test_failure_layer_classification_rate": _rate(
            sum(1 for row in failure_rows if row["failure_layer"] != "unknown"),
            len(failure_rows),
            empty=1.0,
        ),
        "elapsed_ms_total": elapsed_total,
        "elapsed_ms_average": _rate(elapsed_total, len(rows)),
        "elapsed_ms_semantics": elapsed_semantics,
        "existing_report_reuse_count": report_reuse_count,
        "end_to_end_elapsed_available": report_reuse_count == 0,
    }
    checks = [
        _check("minimum_20_cases", len(rows) >= 20, ">=20", len(rows)),
        _check(
            "unique_fixed_sha_repositories",
            metrics["unique_repository_count"] == len(rows)
            and metrics["fixed_sha_count"] == len(rows),
            len(rows),
            min(metrics["unique_repository_count"], metrics["fixed_sha_count"]),
        ),
        _check(
            "all_structured_reports",
            metrics["structured_report_count"] == len(rows),
            len(rows),
            metrics["structured_report_count"],
        ),
        _check(
            "all_cases_terminate_explicitly",
            all(str(row["termination_reason"]) for row in rows),
            len(rows),
            sum(1 for row in rows if str(row["termination_reason"])),
        ),
        _check(
            "environment_and_code_failures_separated",
            metrics["test_failure_layer_classification_rate"] == 1.0,
            1.0,
            metrics["test_failure_layer_classification_rate"],
        ),
        _check(
            "blockers_classified",
            metrics["blocker_classification_rate"] == 1.0,
            1.0,
            metrics["blocker_classification_rate"],
        ),
    ]
    return {
        "schema_version": "unfamiliar_repository_evaluation_v2",
        "suite_name": str(manifest_payload.get("suite_name") or ""),
        "selection_policy": _dict(manifest_payload.get("selection_policy")),
        "passed": all(bool(check["passed"]) for check in checks),
        "metrics": metrics,
        "checks": checks,
        "cases": rows,
    }


def render_unfamiliar_repository_evaluation_markdown(payload: dict[str, Any]) -> str:
    metrics = _dict(payload.get("metrics"))
    lines = [
        "# Unfamiliar Repository Evaluation",
        "",
        f"- Passed: {str(bool(payload.get('passed'))).lower()}",
        f"- Cases: {_int(metrics.get('case_count'))}",
        f"- Structured Report Rate: {_float(metrics.get('structured_report_rate')):.4f}",
        f"- Static Analysis Success Rate: {_float(metrics.get('static_analysis_success_rate')):.4f}",
        f"- Source Root Discovery Rate: {_float(metrics.get('source_root_discovery_rate')):.4f}",
        f"- Test Command Discovery Rate: {_float(metrics.get('test_command_discovery_rate')):.4f}",
        f"- Test Start Rate: {_float(metrics.get('test_start_rate')):.4f}",
        f"- Blocker Classification Rate: {_float(metrics.get('blocker_classification_rate')):.4f}",
        f"- Test Failure Layer Classification Rate: {_float(metrics.get('test_failure_layer_classification_rate')):.4f}",
        f"- Timing Semantics: `{_markdown_cell(metrics.get('elapsed_ms_semantics') or 'unknown')}`",
        (
            "- End-to-End Timing Available: "
            f"{str(bool(metrics.get('end_to_end_elapsed_available', False))).lower()}"
        ),
        (
            "- Outcomes: "
            + _format_counts(_dict(metrics.get("outcome_counts")))
        ),
        "",
        "## Acceptance Checks",
        "",
        "| Check | Passed | Expected | Actual |",
        "| --- | --- | --- | --- |",
    ]
    for check in _list(payload.get("checks")):
        row = _dict(check)
        lines.append(
            "| "
            f"{_markdown_cell(row.get('name') or '')} | "
            f"{str(bool(row.get('passed'))).lower()} | "
            f"{_markdown_cell(row.get('expected'))} | "
            f"{_markdown_cell(row.get('actual'))} |"
        )
    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| Repository | SHA | Outcome | Static | Test Started | Failure Layer | Termination |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for case in _list(payload.get("cases")):
        row = _dict(case)
        lines.append(
            "| "
            f"{_markdown_cell(row.get('repo') or '')} | "
            f"`{_markdown_cell(str(row.get('commit_sha') or '')[:12])}` | "
            f"{_markdown_cell(row.get('outcome') or '')} | "
            f"{str(bool(row.get('static_analysis_success'))).lower()} | "
            f"{str(bool(row.get('test_started'))).lower()} | "
            f"{_markdown_cell(row.get('failure_layer') or 'none')} | "
            f"{_markdown_cell(row.get('termination_reason') or '')} |"
        )
    return "\n".join(lines)


def write_unfamiliar_repository_evaluation_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "unfamiliar_repository_evaluation.json"
    markdown_path = root / "unfamiliar_repository_evaluation.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_unfamiliar_repository_evaluation_markdown(payload),
        encoding="utf-8",
    )
    return {
        "unfamiliar_repository_evaluation_json": str(json_path),
        "unfamiliar_repository_evaluation_markdown": str(markdown_path),
    }


def _outcome(
    *,
    structured_report: bool,
    static_success: bool,
    test_started: bool,
    test_status: str,
    compatibility_status: str,
    compatibility_reason: str,
    primary_blocker: str,
    run_error: str,
) -> tuple[str, str]:
    if not structured_report:
        return "blocked", run_error or "structured_report_not_produced"
    if not static_success:
        return "blocked", primary_blocker or compatibility_reason or "static_analysis_unavailable"
    if test_started:
        suffix = test_status or "completed"
        return "success", f"test_execution:{suffix}"
    if compatibility_status == "ready":
        return "partial", primary_blocker or "test_execution:not_started_within_budget"
    return "partial", primary_blocker or compatibility_reason or "test_execution:not_started"


def _failure_layer(category: str) -> str:
    if not category or category == "none":
        return "none"
    if category in _CODE_FAILURE_CATEGORIES:
        return "code"
    if category in _ENVIRONMENT_FAILURE_CATEGORIES:
        return "environment"
    return "unknown"


def _is_commit_sha(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{40}", str(value or "")))


def _check(name: str, passed: bool, expected: Any, actual: Any) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "expected": expected,
        "actual": actual,
    }


def _rate(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    if denominator <= 0:
        return empty
    return round(float(numerator) / float(denominator), 4)


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={_int(value)}" for key, value in sorted(counts.items()))


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


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Aggregate the pinned unfamiliar-repository Phase 6 suite."
    )
    parser.add_argument("manifest")
    parser.add_argument("suite_report")
    parser.add_argument("output_dir")
    parser.add_argument("--require-success", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    suite = json.loads(Path(args.suite_report).read_text(encoding="utf-8"))
    payload = evaluate_unfamiliar_repository_suite(manifest, suite)
    write_unfamiliar_repository_evaluation_artifacts(payload, args.output_dir)
    print(render_unfamiliar_repository_evaluation_markdown(payload))
    raise SystemExit(0 if payload["passed"] or not args.require_success else 1)


if __name__ == "__main__":
    main()
