from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any


V1_AGENT_LOOP = "Observe -> Plan -> Act -> Verify -> Reflect -> Replan"
V1_READINESS_TARGETS = {
    "onboarding_case_count": 30,
    "onboarding_unique_repo_count": 30,
    "onboarding_scenario_coverage": 13,
    "repair_case_count": 50,
    "repair_class_kind_count": 3,
    "repair_blocker_category_kind_count": 8,
    "required_metric_contract_count": 9,
}
REQUIRED_ONBOARDING_SCENARIOS = [
    "v1_onboarding",
    "github_url_input",
    "owner_repo_input",
    "pytest_project",
    "src_layout_project",
    "pyproject_project",
    "requirements_project",
    "tox_or_nox_project",
    "no_python_sources",
    "no_tests",
    "dependency_missing",
    "timeout",
    "failing_test_evidence",
]
REQUIRED_REPAIR_CLASSES = [
    "llm_direct_success",
    "llm_reflection_success",
    "llm_blocker",
]
REQUIRED_REPAIR_BLOCKER_CATEGORIES = [
    "llm_failed_blocker",
    "environment_blocker",
    "no_test_oracle_blocker",
    "safety_gate_blocker",
    "localization_failure",
    "generation_failure",
    "dependency_failure",
    "timeout_blocker",
]
REQUIRED_EVALUATION_METRICS = [
    "onboarding_success_rate",
    "topk_localization_accuracy",
    "pass_at_1",
    "pass_at_k",
    "reflection_uplift",
    "blocker_accuracy",
    "sandbox_success_rate",
    "average_runtime_ms",
    "llm_cost_usd",
]


def build_v1_readiness_dataset_audit(
    onboarding_manifest: dict[str, Any],
    repair_catalog: dict[str, Any],
    *,
    onboarding_manifest_path: str = "",
    repair_catalog_path: str = "",
    targets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target_values = {**V1_READINESS_TARGETS, **_dict(targets)}
    onboarding = _onboarding_summary(
        onboarding_manifest,
        path=onboarding_manifest_path,
    )
    repair = _repair_summary(repair_catalog, path=repair_catalog_path)
    metrics = _metric_contract_summary(onboarding_manifest, repair_catalog)
    checks = [
        *_onboarding_checks(onboarding, target_values),
        *_repair_checks(repair, target_values),
        *_metric_contract_checks(metrics, target_values),
    ]
    failed = [check for check in checks if not bool(check.get("passed"))]
    status = "pass" if checks and not failed else "incomplete"
    return {
        "status": status,
        "reason": (
            "v1_readiness_dataset_targets_met"
            if status == "pass"
            else "v1_readiness_dataset_targets_not_met"
        ),
        "targets": target_values,
        "source_paths": {
            "onboarding_manifest": onboarding_manifest_path,
            "repair_catalog": repair_catalog_path,
        },
        "summary": {
            "check_count": len(checks),
            "passed_check_count": sum(
                1 for check in checks if bool(check.get("passed"))
            ),
            "failed_check_count": len(failed),
            "onboarding_case_count": onboarding["case_count"],
            "repair_case_count": repair["case_count"],
            "required_metric_contract_count": metrics["covered_required_metric_count"],
            "agent_loop": V1_AGENT_LOOP,
        },
        "onboarding": onboarding,
        "repair": repair,
        "metrics": metrics,
        "target_checks": checks,
        "missing": [str(check.get("name") or "") for check in failed],
        "next_actions": _next_actions(failed),
        "agent_loop": V1_AGENT_LOOP,
    }


def write_v1_readiness_dataset_audit_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "v1_readiness_dataset_audit.json"
    markdown_path = root / "v1_readiness_dataset_audit.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_v1_readiness_dataset_audit_markdown(payload),
        encoding="utf-8",
    )
    return {
        "v1_readiness_dataset_audit_json": str(json_path),
        "v1_readiness_dataset_audit_markdown": str(markdown_path),
    }


def render_v1_readiness_dataset_audit_markdown(payload: dict[str, Any]) -> str:
    summary = _dict(payload.get("summary"))
    onboarding = _dict(payload.get("onboarding"))
    repair = _dict(payload.get("repair"))
    metrics = _dict(payload.get("metrics"))
    lines = [
        "# V1 Readiness Dataset Audit",
        "",
        f"- Status: `{_markdown_cell(payload.get('status') or 'unknown')}`",
        f"- Reason: `{_markdown_cell(payload.get('reason') or 'none')}`",
        f"- Checks: {_int(summary.get('passed_check_count'))}/{_int(summary.get('check_count'))} passed",
        f"- Agent Loop: `{_markdown_cell(payload.get('agent_loop') or V1_AGENT_LOOP)}`",
        "",
        "## Target Checks",
        "",
        "| Group | Target | Actual | Expected | Passed |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for check_value in _list(payload.get("target_checks")):
        check = _dict(check_value)
        lines.append(
            "| "
            f"{_markdown_cell(check.get('group'))} | "
            f"{_markdown_cell(check.get('name'))} | "
            f"{_markdown_cell(check.get('actual'))} | "
            f"{_markdown_cell(check.get('expected'))} | "
            f"{str(bool(check.get('passed'))).lower()} |"
        )
    lines.extend(
        [
            "",
            "## Onboarding",
            "",
            f"- Cases: {_int(onboarding.get('case_count'))}",
            f"- Unique Repos: {_int(onboarding.get('unique_repo_count'))}",
            f"- GitHub Repo Cases: {_int(onboarding.get('github_repo_case_count'))}",
            f"- Scenario Coverage: {_format_list(_list(onboarding.get('covered_required_scenarios')))}",
            f"- Missing Scenarios: {_format_list(_list(onboarding.get('missing_required_scenarios')))}",
            f"- Suite Threshold Min Run Count: {_int(onboarding.get('suite_threshold_min_run_count'))}",
            "",
            "## Repair/Evaluation",
            "",
            f"- Cases: {_int(repair.get('case_count'))}",
            f"- Classes: {_format_counts(_dict(repair.get('class_counts')))}",
            f"- Blocker Categories: {_format_counts(_dict(repair.get('blocker_category_counts')))}",
            f"- Missing Classes: {_format_list(_list(repair.get('missing_required_classes')))}",
            f"- Missing Blocker Categories: {_format_list(_list(repair.get('missing_required_blocker_categories')))}",
            "",
            "## Required Metrics",
            "",
            f"- Metric Contracts: {_int(metrics.get('metric_contract_count'))}",
            f"- Covered Required Metrics: {_int(metrics.get('covered_required_metric_count'))}/{len(REQUIRED_EVALUATION_METRICS)}",
            f"- Missing Required Metrics: {_format_list(_list(metrics.get('missing_required_metrics')))}",
            f"- Incomplete Metric Contracts: {_format_list(_list(metrics.get('incomplete_metric_contracts')))}",
            "",
            "| Metric | Evidence Source | Computed From |",
            "| --- | --- | --- |",
        ]
    )
    for metric_value in _list(metrics.get("required_metric_contracts")):
        metric = _dict(metric_value)
        lines.append(
            "| "
            f"`{_markdown_cell(metric.get('metric_id'))}` | "
            f"{_format_list(_list(metric.get('evidence_artifacts')))} | "
            f"{_format_list(_list(metric.get('computed_from')))} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Audit V1 onboarding and repair/evaluation target datasets."
    )
    parser.add_argument("onboarding_manifest", help="Path to the 30-case onboarding manifest.")
    parser.add_argument("repair_catalog", help="Path to the 50-case repair catalog.")
    parser.add_argument("output_dir", help="Directory for audit artifacts.")
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="Exit with status 1 if the target dataset audit is incomplete.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format printed to stdout.",
    )
    args = parser.parse_args(argv)

    onboarding_path = Path(args.onboarding_manifest)
    repair_path = Path(args.repair_catalog)
    audit = build_v1_readiness_dataset_audit(
        _load_json(onboarding_path),
        _load_json(repair_path),
        onboarding_manifest_path=str(onboarding_path),
        repair_catalog_path=str(repair_path),
    )
    write_v1_readiness_dataset_audit_artifacts(audit, args.output_dir)
    if args.format == "markdown":
        print(render_v1_readiness_dataset_audit_markdown(audit), end="")
    else:
        print(json.dumps(audit, indent=2, ensure_ascii=False))
    if args.require_pass and audit["status"] != "pass":
        raise SystemExit(1)


def _onboarding_summary(manifest: dict[str, Any], *, path: str) -> dict[str, Any]:
    runs = [_dict(item) for item in _list(manifest.get("runs"))]
    repos = [str(run.get("repo") or "") for run in runs]
    normalized_repos = [_normalize_repo(repo) for repo in repos if repo]
    scenario_counts: Counter[str] = Counter()
    for run in runs:
        scenario_counts.update(
            str(tag) for tag in _list(run.get("scenario_tags")) if str(tag)
        )
    missing_scenarios = [
        scenario
        for scenario in REQUIRED_ONBOARDING_SCENARIOS
        if scenario_counts.get(scenario, 0) <= 0
    ]
    incomplete_runs = [
        str(run.get("name") or index)
        for index, run in enumerate(runs)
        if not str(run.get("name") or "")
        or not str(run.get("repo") or "")
        or not _list(run.get("scenario_tags"))
    ]
    github_repo_count = sum(1 for repo in repos if _is_github_repo(repo))
    thresholds = _dict(manifest.get("suite_thresholds"))
    return {
        "path": path,
        "suite_name": str(manifest.get("suite_name") or ""),
        "case_count": len(runs),
        "unique_repo_count": len(set(normalized_repos)),
        "github_repo_case_count": github_repo_count,
        "scenario_counts": dict(sorted(scenario_counts.items())),
        "covered_required_scenarios": [
            scenario
            for scenario in REQUIRED_ONBOARDING_SCENARIOS
            if scenario_counts.get(scenario, 0) > 0
        ],
        "missing_required_scenarios": missing_scenarios,
        "incomplete_run_count": len(incomplete_runs),
        "incomplete_runs": incomplete_runs,
        "suite_threshold_min_run_count": _int(thresholds.get("min_run_count")),
        "agent_loop": V1_AGENT_LOOP,
    }


def _repair_summary(catalog: dict[str, Any], *, path: str) -> dict[str, Any]:
    cases = [_dict(item) for item in _list(catalog.get("cases"))]
    class_counts: Counter[str] = Counter()
    blocker_counts: Counter[str] = Counter()
    incomplete_cases: list[str] = []
    for index, case in enumerate(cases):
        case_id = str(case.get("case_id") or "")
        expected_class = str(case.get("expected_class") or "")
        blocker = str(case.get("expected_blocker_category") or "")
        if expected_class:
            class_counts.update([expected_class])
        if blocker:
            blocker_counts.update([blocker])
        if not case_id or not expected_class:
            incomplete_cases.append(case_id or str(index))
    missing_classes = [
        class_name
        for class_name in REQUIRED_REPAIR_CLASSES
        if class_counts.get(class_name, 0) <= 0
    ]
    missing_blockers = [
        category
        for category in REQUIRED_REPAIR_BLOCKER_CATEGORIES
        if blocker_counts.get(category, 0) <= 0
    ]
    return {
        "path": path,
        "catalog_name": str(catalog.get("name") or ""),
        "case_count": len(cases),
        "class_counts": dict(sorted(class_counts.items())),
        "blocker_category_counts": dict(sorted(blocker_counts.items())),
        "missing_required_classes": missing_classes,
        "missing_required_blocker_categories": missing_blockers,
        "incomplete_case_count": len(incomplete_cases),
        "incomplete_cases": incomplete_cases,
        "agent_loop": str(catalog.get("agent_loop") or V1_AGENT_LOOP),
    }


def _metric_contract_summary(
    onboarding_manifest: dict[str, Any],
    repair_catalog: dict[str, Any],
) -> dict[str, Any]:
    contracts = [
        _dict(item)
        for item in [
            *_list(onboarding_manifest.get("evaluation_metrics")),
            *_list(repair_catalog.get("evaluation_metrics")),
        ]
    ]
    by_id: dict[str, dict[str, Any]] = {}
    incomplete: list[str] = []
    for index, contract in enumerate(contracts):
        metric_id = str(contract.get("metric_id") or "")
        if metric_id and metric_id not in by_id:
            by_id[metric_id] = contract
        if (
            not metric_id
            or not _list(contract.get("computed_from"))
            or not _list(contract.get("evidence_artifacts"))
        ):
            incomplete.append(metric_id or str(index))
    missing = [
        metric_id
        for metric_id in REQUIRED_EVALUATION_METRICS
        if metric_id not in by_id
    ]
    required_contracts = [
        by_id[metric_id]
        for metric_id in REQUIRED_EVALUATION_METRICS
        if metric_id in by_id
    ]
    return {
        "required_metrics": list(REQUIRED_EVALUATION_METRICS),
        "metric_contract_count": len(contracts),
        "covered_required_metric_count": len(required_contracts),
        "required_metric_contracts": required_contracts,
        "missing_required_metrics": missing,
        "incomplete_metric_contract_count": len(incomplete),
        "incomplete_metric_contracts": incomplete,
        "agent_loop": V1_AGENT_LOOP,
    }


def _onboarding_checks(
    onboarding: dict[str, Any],
    targets: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _check(
            "onboarding",
            "onboarding_case_count",
            onboarding.get("case_count"),
            targets.get("onboarding_case_count"),
        ),
        _check(
            "onboarding",
            "onboarding_unique_repo_count",
            onboarding.get("unique_repo_count"),
            targets.get("onboarding_unique_repo_count"),
        ),
        _check(
            "onboarding",
            "github_repo_case_count",
            onboarding.get("github_repo_case_count"),
            targets.get("onboarding_case_count"),
        ),
        _check(
            "onboarding",
            "onboarding_scenario_coverage",
            len(_list(onboarding.get("covered_required_scenarios"))),
            targets.get("onboarding_scenario_coverage"),
        ),
        _check(
            "onboarding",
            "incomplete_run_count",
            0 if _int(onboarding.get("incomplete_run_count")) == 0 else -1,
            0,
        ),
        _check(
            "onboarding",
            "suite_threshold_min_run_count",
            onboarding.get("suite_threshold_min_run_count"),
            targets.get("onboarding_case_count"),
        ),
    ]


def _repair_checks(
    repair: dict[str, Any],
    targets: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _check(
            "repair",
            "repair_case_count",
            repair.get("case_count"),
            targets.get("repair_case_count"),
        ),
        _check(
            "repair",
            "repair_class_kind_count",
            len(_dict(repair.get("class_counts"))),
            targets.get("repair_class_kind_count"),
        ),
        _check(
            "repair",
            "repair_blocker_category_kind_count",
            len(_dict(repair.get("blocker_category_counts"))),
            targets.get("repair_blocker_category_kind_count"),
        ),
        _check(
            "repair",
            "incomplete_case_count",
            0 if _int(repair.get("incomplete_case_count")) == 0 else -1,
            0,
        ),
    ]


def _metric_contract_checks(
    metrics: dict[str, Any],
    targets: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _check(
            "metrics",
            "required_metric_contract_count",
            metrics.get("covered_required_metric_count"),
            targets.get("required_metric_contract_count"),
        ),
        _check(
            "metrics",
            "incomplete_metric_contract_count",
            0 if _int(metrics.get("incomplete_metric_contract_count")) == 0 else -1,
            0,
        ),
    ]


def _check(group: str, name: str, actual: Any, expected: Any) -> dict[str, Any]:
    actual_value = _int(actual)
    expected_value = _int(expected)
    return {
        "group": group,
        "name": name,
        "actual": actual_value,
        "expected": expected_value,
        "passed": actual_value >= expected_value,
    }


def _next_actions(failed: list[dict[str, Any]]) -> list[str]:
    failed_names = {str(check.get("name") or "") for check in failed}
    actions: list[str] = []
    if any(name.startswith("onboarding_") for name in failed_names):
        actions.append(
            "Add or refresh public GitHub repository onboarding runs until the v1 30-case target and required scenario tags are covered."
        )
    if "suite_threshold_min_run_count" in failed_names:
        actions.append(
            "Raise the onboarding suite min_run_count threshold to match the declared v1 target."
        )
    if any(name.startswith("repair_") for name in failed_names):
        actions.append(
            "Add repair/evaluation catalog cases until direct, reflection, blocker, and failure-category coverage reaches the v1 50-case target."
        )
    if any(name.startswith("required_metric_") or name.startswith("incomplete_metric_") for name in failed_names):
        actions.append(
            "Declare every required v1 metric with computed_from fields and evidence artifacts before using the catalog as evaluation evidence."
        )
    if "incomplete_run_count" in failed_names or "incomplete_case_count" in failed_names:
        actions.append(
            "Fill every dataset item with stable id, repo, class or scenario metadata before using it as readiness evidence."
        )
    return actions or ["Run the v1 onboarding manifest and repair catalog to generate evidence reports."]


def _is_github_repo(value: str) -> bool:
    text = str(value or "").strip()
    if text.startswith("https://github.com/"):
        parts = [part for part in text.removeprefix("https://github.com/").split("/") if part]
        return len(parts) >= 2
    parts = [part for part in text.split("/") if part]
    return len(parts) == 2 and not text.startswith("controlled/")


def _normalize_repo(value: str) -> str:
    text = str(value or "").strip().removesuffix(".git")
    if text.startswith("https://github.com/"):
        text = text.removeprefix("https://github.com/")
    return "/".join(part for part in text.split("/")[:2] if part).lower()


def _load_json(path: Path) -> dict[str, Any]:
    return _dict(json.loads(path.read_text(encoding="utf-8")))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={_int(value)}" for key, value in sorted(counts.items()))


def _format_list(values: list[Any]) -> str:
    items = [str(value) for value in values if str(value)]
    return ", ".join(items) if items else "none"


def _markdown_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":  # pragma: no cover
    main()
