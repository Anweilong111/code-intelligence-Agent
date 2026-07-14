from __future__ import annotations

import argparse
from collections import Counter
import json
import re
from pathlib import Path
from typing import Any


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
_CODE_FAILURE_CATEGORIES = {"syntax_error", "test_assertion_failure"}


def evaluate_v3_repository_startup(
    manifest_payload: dict[str, Any],
    suite_payload: dict[str, Any],
    *,
    baseline_metrics_payload: dict[str, Any] | None = None,
    minimum_started_and_terminated: int = 14,
) -> dict[str, Any]:
    manifest_runs = [_dict(item) for item in _list(manifest_payload.get("runs"))]
    defaults = _dict(manifest_payload.get("defaults"))
    protocol = _dict(manifest_payload.get("protocol"))
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
        agent_summary = _load_agent_summary(report_path)
        structured_report = bool(
            run
            and not str(run.get("error") or "")
            and str(run.get("status") or "") not in {"", "command_error"}
            and report_path.is_file()
        )
        test_started = bool(
            metrics.get("planned_repository_test_result_executed", False)
        )
        test_status = str(
            metrics.get("planned_repository_test_result_status") or ""
        )
        setup_mode = str(
            metrics.get("repository_test_environment_setup_mode")
            or agent_summary.get("repository_test_environment_setup_mode")
            or ""
        )
        project_code_install_requested = bool(
            metrics.get(
                "repository_test_environment_setup_repository_code_install_requested",
                False,
            )
            or agent_summary.get(
                "repository_test_environment_setup_repository_code_install_requested",
                False,
            )
        )
        project_dependency_install_requested = bool(
            metrics.get(
                "repository_test_environment_setup_repository_dependency_install_requested",
                False,
            )
            or agent_summary.get(
                "repository_test_environment_setup_repository_dependency_install_requested",
                False,
            )
        )
        setup_executed = bool(
            metrics.get("repository_test_environment_setup_executed", False)
            or agent_summary.get(
                "repository_test_environment_setup_result_executed",
                False,
            )
        )
        setup_status = str(
            metrics.get("repository_test_environment_setup_result_status")
            or agent_summary.get("repository_test_environment_setup_result_status")
            or ""
        )
        python_source = str(
            metrics.get("planned_repository_test_python_source")
            or agent_summary.get("planned_repository_test_python_source")
            or ""
        )
        isolated_venv_used = (
            python_source == "repository_test_environment_setup"
        )
        explicitly_terminated = test_started and bool(test_status)
        startup_contract_passed = bool(
            test_started
            and explicitly_terminated
            and setup_mode == "runner_probe"
            and setup_executed
            and setup_status == "pass"
            and isolated_venv_used
            and not project_code_install_requested
            and not project_dependency_install_requested
        )
        failure_category = str(
            metrics.get("planned_repository_test_failure_category") or ""
        )
        failure_layer = _failure_layer(failure_category)
        blocker = _startup_blocker(
            metrics,
            agent_summary,
            test_started=test_started,
        )
        rows.append(
            {
                "name": name,
                "repo": str(entry.get("repo") or ""),
                "commit_sha": str(entry.get("ref") or ""),
                "fixed_sha_valid": _is_commit_sha(str(entry.get("ref") or "")),
                "structured_report": structured_report,
                "test_command_discovered": bool(
                    str(metrics.get("planned_repository_test_command") or "")
                ),
                "setup_mode": setup_mode,
                "setup_executed": setup_executed,
                "setup_status": setup_status,
                "project_code_install_requested": project_code_install_requested,
                "project_dependency_install_requested": (
                    project_dependency_install_requested
                ),
                "test_started": test_started,
                "test_status": test_status,
                "explicitly_terminated": explicitly_terminated,
                "isolated_venv_used": isolated_venv_used,
                "startup_contract_passed": startup_contract_passed,
                "failure_category": failure_category,
                "failure_layer": failure_layer,
                "blocker": blocker,
                "python_source": python_source,
                "elapsed_ms": _int(run.get("elapsed_ms")),
                "report_path": str(report_path) if str(run.get("report_path") or "") else "",
            }
        )

    case_count = len(rows)
    manifest_checkout_authorized_count = sum(
        1
        for entry in manifest_runs
        if entry.get(
            "checkout_repository_tests",
            defaults.get("checkout_repository_tests"),
        )
        is True
    )
    started_rows = [row for row in rows if row["test_started"]]
    startup_rows = [row for row in rows if row["startup_contract_passed"]]
    not_started_rows = [row for row in rows if not row["test_started"]]
    started_failure_rows = [
        row
        for row in started_rows
        if str(row["test_status"]) not in {"", "pass"}
    ]
    baseline_count, baseline_rate = _baseline_startup_metrics(
        baseline_metrics_payload or {}
    )
    startup_count = len(startup_rows)
    startup_rate = _rate(startup_count, case_count)
    metrics = {
        "case_count": case_count,
        "unique_repository_count": len({str(row["repo"]) for row in rows}),
        "fixed_sha_count": sum(1 for row in rows if row["fixed_sha_valid"]),
        "manifest_checkout_authorized_count": manifest_checkout_authorized_count,
        "runner_probe_report_count": sum(
            1 for row in rows if row["setup_mode"] == "runner_probe"
        ),
        "structured_report_count": sum(
            1 for row in rows if row["structured_report"]
        ),
        "test_command_discovery_count": sum(
            1 for row in rows if row["test_command_discovered"]
        ),
        "setup_execution_count": sum(1 for row in rows if row["setup_executed"]),
        "raw_test_start_count": len(started_rows),
        "started_and_terminated_count": startup_count,
        "started_and_terminated_rate": startup_rate,
        "isolated_venv_started_count": sum(
            1 for row in started_rows if row["isolated_venv_used"]
        ),
        "project_code_install_requested_count": sum(
            1 for row in rows if row["project_code_install_requested"]
        ),
        "project_dependency_install_requested_count": sum(
            1 for row in rows if row["project_dependency_install_requested"]
        ),
        "not_started_count": len(not_started_rows),
        "classified_not_started_blocker_count": sum(
            1 for row in not_started_rows if str(row["blocker"])
        ),
        "started_failure_count": len(started_failure_rows),
        "classified_started_failure_layer_count": sum(
            1 for row in started_failure_rows if row["failure_layer"] != "unknown"
        ),
        "failure_layer_counts": dict(
            sorted(
                Counter(
                    str(row["failure_layer"]) for row in started_failure_rows
                ).items()
            )
        ),
        "baseline_started_and_terminated_count": baseline_count,
        "baseline_started_and_terminated_rate": baseline_rate,
        "startup_count_uplift": startup_count - baseline_count,
        "startup_rate_uplift": round(startup_rate - baseline_rate, 4),
        "elapsed_ms_total": sum(_int(row["elapsed_ms"]) for row in rows),
    }
    checks = [
        _check("exactly_20_cases", case_count == 20, 20, case_count),
        _check(
            "unique_fixed_sha_repositories",
            metrics["unique_repository_count"] == case_count
            and metrics["fixed_sha_count"] == case_count,
            case_count,
            min(metrics["unique_repository_count"], metrics["fixed_sha_count"]),
        ),
        _check(
            "manifest_authorizes_all_checkouts",
            manifest_checkout_authorized_count == case_count,
            case_count,
            manifest_checkout_authorized_count,
        ),
        _check(
            "manifest_uses_runner_probe",
            str(defaults.get("repository_test_environment_setup_mode") or "")
            == "runner_probe",
            "runner_probe",
            defaults.get("repository_test_environment_setup_mode"),
        ),
        _check(
            "all_structured_reports",
            metrics["structured_report_count"] == case_count,
            case_count,
            metrics["structured_report_count"],
        ),
        _check(
            "all_case_reports_use_runner_probe",
            metrics["runner_probe_report_count"] == case_count,
            case_count,
            metrics["runner_probe_report_count"],
        ),
        _check(
            "all_test_commands_discovered",
            metrics["test_command_discovery_count"] == case_count,
            case_count,
            metrics["test_command_discovery_count"],
        ),
        _check(
            "no_repository_code_install",
            metrics["project_code_install_requested_count"] == 0,
            0,
            metrics["project_code_install_requested_count"],
        ),
        _check(
            "no_repository_dependency_install",
            metrics["project_dependency_install_requested_count"] == 0,
            0,
            metrics["project_dependency_install_requested_count"],
        ),
        _check(
            "all_started_tests_use_isolated_venv",
            metrics["isolated_venv_started_count"] == len(started_rows),
            len(started_rows),
            metrics["isolated_venv_started_count"],
        ),
        _check(
            "all_started_tests_terminate_explicitly",
            all(bool(row["explicitly_terminated"]) for row in started_rows),
            len(started_rows),
            sum(1 for row in started_rows if row["explicitly_terminated"]),
        ),
        _check(
            "minimum_started_and_terminated",
            startup_count >= minimum_started_and_terminated,
            f">={minimum_started_and_terminated}",
            startup_count,
        ),
        _check(
            "not_started_blockers_classified",
            all(str(row["blocker"]) for row in not_started_rows),
            len(not_started_rows),
            metrics["classified_not_started_blocker_count"],
        ),
        _check(
            "started_failure_layers_classified",
            all(row["failure_layer"] != "unknown" for row in started_failure_rows),
            len(started_failure_rows),
            metrics["classified_started_failure_layer_count"],
        ),
    ]
    return {
        "schema_version": "v3_repository_startup_evaluation_v1",
        "suite_name": str(manifest_payload.get("suite_name") or ""),
        "paired_manifest": str(protocol.get("paired_manifest") or ""),
        "minimum_started_and_terminated": minimum_started_and_terminated,
        "passed": all(bool(check["passed"]) for check in checks),
        "metrics": metrics,
        "checks": checks,
        "cases": rows,
        "claim_boundary": str(protocol.get("success_boundary") or ""),
        "comparison_warning": str(protocol.get("comparison_warning") or ""),
    }


def render_v3_repository_startup_evaluation_markdown(
    payload: dict[str, Any],
) -> str:
    metrics = _dict(payload.get("metrics"))
    lines = [
        "# V3 Repository Startup Evaluation",
        "",
        f"- Passed: {str(bool(payload.get('passed'))).lower()}",
        f"- Cases: {_int(metrics.get('case_count'))}",
        (
            "- Started And Terminated: "
            f"{_int(metrics.get('started_and_terminated_count'))} / "
            f"{_int(metrics.get('case_count'))}"
        ),
        (
            "- Startup Rate: "
            f"{_float(metrics.get('started_and_terminated_rate')):.4f}"
        ),
        (
            "- V2 Baseline: "
            f"{_int(metrics.get('baseline_started_and_terminated_count'))} / "
            f"{_int(metrics.get('case_count'))} "
            f"({_float(metrics.get('baseline_started_and_terminated_rate')):.4f})"
        ),
        f"- Startup Count Uplift: {_int(metrics.get('startup_count_uplift'))}",
        f"- Startup Rate Uplift: {_float(metrics.get('startup_rate_uplift')):.4f}",
        (
            "- Repository Code Installs Requested: "
            f"{_int(metrics.get('project_code_install_requested_count'))}"
        ),
        f"- Claim Boundary: {_markdown_cell(payload.get('claim_boundary') or 'none')}",
        f"- Comparison Warning: {_markdown_cell(payload.get('comparison_warning') or 'none')}",
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
            "| Repository | SHA | Setup | Started | Terminated | Venv | Failure | Blocker |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for case in _list(payload.get("cases")):
        row = _dict(case)
        lines.append(
            "| "
            f"{_markdown_cell(row.get('repo') or '')} | "
            f"`{_markdown_cell(str(row.get('commit_sha') or '')[:12])}` | "
            f"{_markdown_cell(row.get('setup_status') or 'none')} | "
            f"{str(bool(row.get('test_started'))).lower()} | "
            f"{str(bool(row.get('explicitly_terminated'))).lower()} | "
            f"{str(bool(row.get('isolated_venv_used'))).lower()} | "
            f"{_markdown_cell(row.get('failure_category') or 'none')} | "
            f"{_markdown_cell(row.get('blocker') or 'none')} |"
        )
    return "\n".join(lines)


def write_v3_repository_startup_evaluation_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "v3_repository_startup_evaluation.json"
    markdown_path = root / "v3_repository_startup_evaluation.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_v3_repository_startup_evaluation_markdown(payload),
        encoding="utf-8",
    )
    return {
        "v3_repository_startup_evaluation_json": str(json_path),
        "v3_repository_startup_evaluation_markdown": str(markdown_path),
    }


def _baseline_startup_metrics(payload: dict[str, Any]) -> tuple[int, float]:
    outcome = _dict(payload.get("outcome_evaluation"))
    selection = _dict(payload.get("selection"))
    case_count = _int(selection.get("repository_count"))
    rate = _float(outcome.get("test_start_rate"))
    counts = _dict(outcome.get("outcome_counts"))
    count = _int(counts.get("success"))
    if not count and case_count:
        count = int(round(rate * case_count))
    return count, round(rate, 4)


def _load_agent_summary(report_path: Path) -> dict[str, Any]:
    if not report_path.is_file():
        return {}
    intelligence = _read_json(report_path)
    candidates = []
    agent_json = str(intelligence.get("agent_json") or "")
    if agent_json:
        candidates.append(Path(agent_json))
    candidates.append(report_path.parent / "github_repo_agent.json")
    for candidate in candidates:
        payload = _read_json(candidate)
        summary = _dict(payload.get("summary"))
        if summary:
            return summary
    return {}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return _dict(payload)


def _failure_layer(category: str) -> str:
    if not category or category == "none":
        return "none"
    if category in _ENVIRONMENT_FAILURE_CATEGORIES:
        return "environment"
    if category in _CODE_FAILURE_CATEGORIES:
        return "code"
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


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


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


def _startup_blocker(
    metrics: dict[str, Any],
    agent_summary: dict[str, Any],
    *,
    test_started: bool,
) -> str:
    if not test_started:
        candidates = (
            metrics.get("repository_compatibility_primary_blocker"),
            agent_summary.get("repository_compatibility_primary_blocker"),
            metrics.get("repository_compatibility_termination_reason"),
            agent_summary.get("repository_compatibility_termination_reason"),
            metrics.get("repository_test_environment_setup_reason"),
            agent_summary.get("repository_test_environment_setup_reason"),
            metrics.get("repository_test_environment_reason"),
            agent_summary.get("repository_test_environment_reason"),
            metrics.get("repository_test_execution_plan_reason"),
            agent_summary.get("repository_test_execution_plan_reason"),
            metrics.get("repository_test_setup_doctor_blocker"),
            metrics.get("blocker"),
        )
    else:
        candidates = (
            metrics.get("repository_test_setup_doctor_blocker"),
            metrics.get("repository_compatibility_primary_blocker"),
            agent_summary.get("repository_compatibility_primary_blocker"),
            metrics.get("blocker"),
            metrics.get("repository_compatibility_termination_reason"),
            agent_summary.get("repository_compatibility_termination_reason"),
        )
    return next((str(value) for value in candidates if str(value or "")), "")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the V3 fixed-SHA isolated repository startup probe."
    )
    parser.add_argument("manifest")
    parser.add_argument("suite_report")
    parser.add_argument("output_dir")
    parser.add_argument("--baseline-metrics")
    parser.add_argument("--minimum-started", type=int, default=14)
    parser.add_argument("--require-success", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    suite = json.loads(Path(args.suite_report).read_text(encoding="utf-8"))
    baseline = (
        json.loads(Path(args.baseline_metrics).read_text(encoding="utf-8"))
        if args.baseline_metrics
        else {}
    )
    payload = evaluate_v3_repository_startup(
        manifest,
        suite,
        baseline_metrics_payload=baseline,
        minimum_started_and_terminated=args.minimum_started,
    )
    write_v3_repository_startup_evaluation_artifacts(payload, args.output_dir)
    print(render_v3_repository_startup_evaluation_markdown(payload))
    raise SystemExit(0 if payload["passed"] or not args.require_success else 1)


if __name__ == "__main__":
    main()
