from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.github_onboarding_matrix import (
    REQUIRED_ONBOARDING_ARTIFACTS,
    REQUIRED_SCENARIOS,
)


P6_READINESS_TARGETS = {
    "onboarding_case_count": 10,
    "onboarding_scenario_coverage": len(REQUIRED_SCENARIOS),
    "onboarding_artifact_groups_complete": len(REQUIRED_ONBOARDING_ARTIFACTS),
    "repair_case_count": 20,
    "llm_direct_success": 5,
    "llm_reflection_success": 3,
    "llm_blocker": 5,
    "llm_direct_evidence_complete": 5,
    "llm_reflection_evidence_complete": 3,
    "llm_blocker_evidence_complete": 5,
    "llm_patch_judge_ready": 1,
    "llm_patch_judge_accept_success": 1,
    "llm_patch_judge_reject_failure": 1,
    "llm_failed_blocker": 1,
    "environment_blocker": 1,
    "no_test_oracle_blocker": 1,
    "safety_gate_blocker": 1,
    "agent_loop_trace_complete": 20,
}

SANDBOX_AUTHORITY = "sandbox_pytest_decides_success"


def build_p6_readiness_audit(
    onboarding_matrix: dict[str, Any],
    repair_matrix: dict[str, Any],
    *,
    onboarding_matrix_path: str = "",
    repair_matrix_path: str = "",
    targets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target_values = {**P6_READINESS_TARGETS, **_dict(targets)}
    onboarding = _onboarding_summary(
        onboarding_matrix,
        path=onboarding_matrix_path,
        targets=target_values,
    )
    repair = _repair_summary(
        repair_matrix,
        path=repair_matrix_path,
        targets=target_values,
    )
    checks = [
        *_onboarding_checks(onboarding, targets=target_values),
        *_repair_checks(repair, targets=target_values),
    ]
    failed = [check for check in checks if not bool(check.get("passed"))]
    status = "pass" if checks and not failed else "incomplete"
    return {
        "status": status,
        "reason": (
            "p6_readiness_targets_met"
            if status == "pass"
            else "p6_readiness_targets_not_met"
        ),
        "targets": target_values,
        "source_paths": {
            "github_onboarding_matrix": onboarding_matrix_path,
            "llm_repair_evaluation_matrix": repair_matrix_path,
        },
        "summary": {
            "check_count": len(checks),
            "passed_check_count": sum(
                1 for check in checks if bool(check.get("passed"))
            ),
            "failed_check_count": len(failed),
            "onboarding_status": onboarding["status"],
            "repair_status": repair["status"],
            "sandbox_authority": repair["sandbox_authority"],
            "agent_loop": "Observe -> Plan -> Act -> Verify -> Reflect -> Replan",
        },
        "onboarding": onboarding,
        "repair": repair,
        "target_checks": checks,
        "missing": [str(check.get("name") or "") for check in failed],
        "next_actions": _next_actions(failed),
        "sandbox_authority": SANDBOX_AUTHORITY,
        "agent_loop": "Observe -> Plan -> Act -> Verify -> Reflect -> Replan",
    }


def write_p6_readiness_audit_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "p6_readiness_audit.json"
    markdown_path = root / "p6_readiness_audit.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_p6_readiness_audit_markdown(payload),
        encoding="utf-8",
    )
    return {
        "p6_readiness_audit_json": str(json_path),
        "p6_readiness_audit_markdown": str(markdown_path),
    }


def render_p6_readiness_audit_markdown(payload: dict[str, Any]) -> str:
    summary = _dict(payload.get("summary"))
    onboarding = _dict(payload.get("onboarding"))
    repair = _dict(payload.get("repair"))
    lines = [
        "# P6 Readiness Audit",
        "",
        f"- Status: `{_markdown_cell(payload.get('status') or 'unknown')}`",
        f"- Reason: `{_markdown_cell(payload.get('reason') or 'none')}`",
        (
            "- Checks: "
            f"{_int(summary.get('passed_check_count'))}/"
            f"{_int(summary.get('check_count'))} passed"
        ),
        f"- Sandbox Authority: `{_markdown_cell(payload.get('sandbox_authority') or SANDBOX_AUTHORITY)}`",
        f"- Agent Loop: `{_markdown_cell(payload.get('agent_loop') or '')}`",
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
            f"{_markdown_cell(check.get('group') or '')} | "
            f"{_markdown_cell(check.get('name') or '')} | "
            f"{_markdown_cell(check.get('actual'))} | "
            f"{_markdown_cell(check.get('expected'))} | "
            f"{str(bool(check.get('passed'))).lower()} |"
        )
    if not _list(payload.get("target_checks")):
        lines.append("| none | none | 0 | 0 | false |")
    lines.extend(
        [
            "",
            "## Onboarding",
            "",
            f"- Status: `{_markdown_cell(onboarding.get('status') or 'unknown')}`",
            f"- Cases: {_int(onboarding.get('case_count'))}/{_int(onboarding.get('required_case_count'))}",
            f"- Scenario Coverage: {_int(onboarding.get('covered_scenario_count'))}/{_int(onboarding.get('required_scenario_count'))}",
            f"- Artifact Groups Complete: {_int(onboarding.get('complete_artifact_group_count'))}/{_int(onboarding.get('required_artifact_group_count'))}",
            f"- Agent Policy Trace Complete: `{str(bool(onboarding.get('agent_policy_trace_complete'))).lower()}`",
            f"- Missing Scenarios: `{_markdown_cell(_format_list(_list(onboarding.get('missing_scenarios'))))}`",
            f"- Missing Artifact Groups: `{_markdown_cell(_format_list(_list(onboarding.get('missing_artifact_groups'))))}`",
            "",
            "## Repair",
            "",
            f"- Status: `{_markdown_cell(repair.get('status') or 'unknown')}`",
            f"- Metrics Status: `{_markdown_cell(repair.get('metrics_status') or 'unknown')}`",
            f"- Cases: {_int(repair.get('case_count'))}/{_int(payload.get('targets', {}).get('repair_case_count'))}",
            f"- LLM Direct Successes: {_int(repair.get('llm_direct_success_count'))}",
            f"- LLM Reflection Successes: {_int(repair.get('llm_reflection_success_count'))}",
            f"- LLM Blockers: {_int(repair.get('llm_blocker_count'))}",
            f"- Patch Judge Ready Cases: {_int(repair.get('patch_judge_llm_ready_case_count'))}",
            f"- Agent Loop Complete Cases: {_int(repair.get('agent_loop_trace_complete_count'))}",
            f"- Sandbox Authority: `{_markdown_cell(repair.get('sandbox_authority') or '')}`",
            "",
            "## Next Actions",
            "",
        ]
    )
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {action}")
    if not _list(payload.get("next_actions")):
        lines.append("- P6 readiness targets are met.")
    return "\n".join(lines) + "\n"


def _onboarding_summary(
    matrix: dict[str, Any],
    *,
    path: str,
    targets: dict[str, Any],
) -> dict[str, Any]:
    scenario_coverage = _dict(matrix.get("scenario_coverage"))
    required_scenarios = _required_scenario_ids(matrix)
    covered_scenarios = [
        scenario
        for scenario in required_scenarios
        if _int(_dict(scenario_coverage.get(scenario)).get("count")) > 0
    ]
    missing_scenarios = [
        scenario for scenario in required_scenarios if scenario not in covered_scenarios
    ]
    artifact_coverage = _dict(matrix.get("artifact_coverage"))
    required_artifacts = [item[0] for item in REQUIRED_ONBOARDING_ARTIFACTS]
    complete_artifacts = [
        name
        for name in required_artifacts
        if _int(_dict(artifact_coverage.get(name)).get("missing")) == 0
        and _int(_dict(artifact_coverage.get(name)).get("present")) > 0
    ]
    missing_artifacts = [
        name for name in required_artifacts if name not in complete_artifacts
    ]
    rows = [_dict(row) for row in _list(matrix.get("rows"))]
    policy_complete = bool(rows) and all(_row_policy_trace_complete(row) for row in rows)
    check_count = _int(matrix.get("check_count"))
    passed_check_count = _int(matrix.get("passed_check_count"))
    all_matrix_checks_passed = bool(check_count) and passed_check_count == check_count
    return {
        "path": path,
        "status": str(matrix.get("status") or "missing"),
        "reason": str(matrix.get("reason") or ""),
        "case_count": _int(matrix.get("case_count")),
        "required_case_count": _int(
            matrix.get("required_case_count"),
            _int(targets.get("onboarding_case_count")),
        ),
        "check_count": check_count,
        "passed_check_count": passed_check_count,
        "all_matrix_checks_passed": all_matrix_checks_passed,
        "required_scenario_count": len(required_scenarios),
        "covered_scenario_count": len(covered_scenarios),
        "missing_scenarios": missing_scenarios,
        "required_artifact_group_count": len(required_artifacts),
        "complete_artifact_group_count": len(complete_artifacts),
        "missing_artifact_groups": missing_artifacts,
        "agent_policy_trace_complete": policy_complete,
    }


def _repair_summary(
    matrix: dict[str, Any],
    *,
    path: str,
    targets: dict[str, Any],
) -> dict[str, Any]:
    metrics = _dict(matrix.get("metrics_report"))
    target_summary = _dict(metrics.get("target_summary"))
    metrics_case_count = _int(metrics.get("case_count"))
    matrix_case_count = _int(matrix.get("case_count"))
    case_count = metrics_case_count if metrics else matrix_case_count
    sandbox_authority = str(
        metrics.get("sandbox_authority")
        or matrix.get("sandbox_authority")
        or ""
    )
    return {
        "path": path,
        "status": str(matrix.get("status") or "missing"),
        "reason": str(matrix.get("reason") or ""),
        "case_count": case_count,
        "matrix_case_count": matrix_case_count,
        "metrics_case_count": metrics_case_count,
        "metrics_status": str(metrics.get("status") or "missing"),
        "metrics_reason": str(metrics.get("reason") or ""),
        "all_repair_targets_met": bool(target_summary.get("all_targets_met")),
        "llm_direct_success_count": _int(metrics.get("llm_direct_success_count")),
        "llm_reflection_success_count": _int(
            metrics.get("llm_reflection_success_count")
        ),
        "llm_blocker_count": _int(metrics.get("llm_blocker_count")),
        "llm_direct_evidence_complete_count": _int(
            metrics.get("llm_direct_evidence_complete_count")
        ),
        "llm_reflection_evidence_complete_count": _int(
            metrics.get("llm_reflection_evidence_complete_count")
        ),
        "llm_blocker_evidence_complete_count": _int(
            metrics.get("llm_blocker_evidence_complete_count")
        ),
        "patch_judge_llm_ready_case_count": _int(
            metrics.get("patch_judge_llm_ready_case_count")
        ),
        "patch_judge_accept_success_count": _int(
            metrics.get("patch_judge_accept_success_count")
        ),
        "patch_judge_reject_failure_count": _int(
            metrics.get("patch_judge_reject_failure_count")
        ),
        "llm_failed_blocker_count": _int(metrics.get("llm_failed_blocker_count")),
        "environment_blocker_count": _int(metrics.get("environment_blocker_count")),
        "no_test_oracle_blocker_count": _int(
            metrics.get("no_test_oracle_blocker_count")
        ),
        "safety_gate_blocker_count": _int(metrics.get("safety_gate_blocker_count")),
        "agent_loop_trace_complete_count": _int(
            metrics.get("agent_loop_trace_complete_count")
        ),
        "sandbox_authority": sandbox_authority,
        "sandbox_authority_complete": sandbox_authority == SANDBOX_AUTHORITY,
        "blocker_category_counts": _dict(metrics.get("blocker_category_counts")),
        "patch_judge_outcome_counts": _dict(metrics.get("patch_judge_outcome_counts")),
        "targets": _dict(matrix.get("targets")) or _dict(metrics.get("targets")) or targets,
    }


def _onboarding_checks(
    onboarding: dict[str, Any],
    *,
    targets: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _equals_check(
            "onboarding",
            "onboarding_matrix_status_pass",
            1 if onboarding.get("status") == "pass" else 0,
            1,
        ),
        _min_check(
            "onboarding",
            "onboarding_case_count",
            onboarding.get("case_count"),
            targets.get("onboarding_case_count"),
        ),
        _equals_check(
            "onboarding",
            "onboarding_all_matrix_checks_passed",
            1 if onboarding.get("all_matrix_checks_passed") else 0,
            1,
        ),
        _min_check(
            "onboarding",
            "onboarding_scenario_coverage",
            onboarding.get("covered_scenario_count"),
            targets.get("onboarding_scenario_coverage"),
        ),
        _min_check(
            "onboarding",
            "onboarding_artifact_groups_complete",
            onboarding.get("complete_artifact_group_count"),
            targets.get("onboarding_artifact_groups_complete"),
        ),
        _equals_check(
            "onboarding",
            "onboarding_agent_policy_trace_complete",
            1 if onboarding.get("agent_policy_trace_complete") else 0,
            1,
        ),
    ]


def _repair_checks(
    repair: dict[str, Any],
    *,
    targets: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _equals_check(
            "repair",
            "repair_matrix_status_pass",
            1 if repair.get("status") == "pass" else 0,
            1,
        ),
        _equals_check(
            "repair",
            "repair_metrics_status_pass",
            1 if repair.get("metrics_status") == "pass" else 0,
            1,
        ),
        _min_check(
            "repair",
            "repair_case_count",
            repair.get("case_count"),
            targets.get("repair_case_count"),
        ),
        _min_check(
            "repair",
            "llm_direct_success",
            repair.get("llm_direct_success_count"),
            targets.get("llm_direct_success"),
        ),
        _min_check(
            "repair",
            "llm_reflection_success",
            repair.get("llm_reflection_success_count"),
            targets.get("llm_reflection_success"),
        ),
        _min_check(
            "repair",
            "llm_blocker",
            repair.get("llm_blocker_count"),
            targets.get("llm_blocker"),
        ),
        _min_check(
            "repair",
            "llm_direct_evidence_complete",
            repair.get("llm_direct_evidence_complete_count"),
            targets.get("llm_direct_evidence_complete"),
        ),
        _min_check(
            "repair",
            "llm_reflection_evidence_complete",
            repair.get("llm_reflection_evidence_complete_count"),
            targets.get("llm_reflection_evidence_complete"),
        ),
        _min_check(
            "repair",
            "llm_blocker_evidence_complete",
            repair.get("llm_blocker_evidence_complete_count"),
            targets.get("llm_blocker_evidence_complete"),
        ),
        _min_check(
            "repair",
            "llm_patch_judge_ready",
            repair.get("patch_judge_llm_ready_case_count"),
            targets.get("llm_patch_judge_ready"),
        ),
        _min_check(
            "repair",
            "llm_patch_judge_accept_success",
            repair.get("patch_judge_accept_success_count"),
            targets.get("llm_patch_judge_accept_success"),
        ),
        _min_check(
            "repair",
            "llm_patch_judge_reject_failure",
            repair.get("patch_judge_reject_failure_count"),
            targets.get("llm_patch_judge_reject_failure"),
        ),
        _min_check(
            "repair",
            "llm_failed_blocker",
            repair.get("llm_failed_blocker_count"),
            targets.get("llm_failed_blocker"),
        ),
        _min_check(
            "repair",
            "environment_blocker",
            repair.get("environment_blocker_count"),
            targets.get("environment_blocker"),
        ),
        _min_check(
            "repair",
            "no_test_oracle_blocker",
            repair.get("no_test_oracle_blocker_count"),
            targets.get("no_test_oracle_blocker"),
        ),
        _min_check(
            "repair",
            "safety_gate_blocker",
            repair.get("safety_gate_blocker_count"),
            targets.get("safety_gate_blocker"),
        ),
        _min_check(
            "repair",
            "agent_loop_trace_complete",
            repair.get("agent_loop_trace_complete_count"),
            targets.get("agent_loop_trace_complete"),
        ),
        _equals_check(
            "repair",
            "sandbox_authority",
            1 if repair.get("sandbox_authority_complete") else 0,
            1,
        ),
    ]


def _next_actions(failed_checks: list[dict[str, Any]]) -> list[str]:
    failed_names = {str(check.get("name") or "") for check in failed_checks}
    actions: list[str] = []
    if any(name.startswith("onboarding_") for name in failed_names):
        actions.append(
            "Run or refresh at least 10 real Python GitHub onboarding cases and regenerate github_onboarding_matrix.json/md."
        )
    if "onboarding_scenario_coverage" in failed_names:
        actions.append(
            "Add onboarding cases for every required scenario: pytest, src layout, pyproject, requirements, tox/nox, no sources, no tests, dependency blocker, timeout, and failing-test evidence."
        )
    if "onboarding_artifact_groups_complete" in failed_names:
        actions.append(
            "Backfill or regenerate repository profile, structure, test discovery, environment, execution plan, and agent policy trace artifacts for each onboarding case."
        )
    if any(name.startswith("llm_") for name in failed_names) or any(
        name.startswith("repair_") for name in failed_names
    ):
        actions.append(
            "Run or refresh the LLM repair evaluation suite until the 20-case matrix meets direct success, reflection success, blocker, evidence, and patch-judge targets."
        )
    if {
        "llm_patch_judge_ready",
        "llm_patch_judge_accept_success",
        "llm_patch_judge_reject_failure",
    } & failed_names:
        actions.append(
            "Enable LLM patch judge mode only through environment variables, then capture accept-success and reject-failure judge/sandbox outcome evidence."
        )
    if {
        "llm_failed_blocker",
        "environment_blocker",
        "no_test_oracle_blocker",
        "safety_gate_blocker",
    } & failed_names:
        actions.append(
            "Add blocker cases that separately cover missing LLM configuration, environment failure, no test oracle, and safety-gate rejection."
        )
    if "agent_loop_trace_complete" in failed_names:
        actions.append(
            "Regenerate AgentController artifacts so every repair case exposes Observe, Plan, Act, Verify, Reflect, and Replan evidence."
        )
    if "sandbox_authority" in failed_names:
        actions.append(
            "Preserve sandbox pytest as the final success authority; judge output can rank candidates but must not replace execution validation."
        )
    return _unique(actions)


def _required_scenario_ids(matrix: dict[str, Any]) -> list[str]:
    required = []
    for item in _list(matrix.get("required_scenarios")):
        row = _dict(item)
        scenario_id = str(row.get("id") or "")
        if scenario_id:
            required.append(scenario_id)
    if required:
        return _unique(required)
    return [item[0] for item in REQUIRED_SCENARIOS]


def _row_policy_trace_complete(row: dict[str, Any]) -> bool:
    policy = _dict(row.get("policy_trace"))
    if not bool(policy.get("present")):
        return False
    if not str(policy.get("canonical_action") or policy.get("selected_action") or ""):
        return False
    loop = [str(item).lower() for item in _list(policy.get("loop"))]
    return all(step in loop for step in ("observe", "plan", "act", "verify", "reflect", "replan"))


def _min_check(
    group: str,
    name: str,
    actual: Any,
    expected: Any,
) -> dict[str, Any]:
    actual_int = _int(actual)
    expected_int = _int(expected)
    return {
        "group": group,
        "name": name,
        "actual": actual_int,
        "expected": expected_int,
        "passed": actual_int >= expected_int,
    }


def _equals_check(
    group: str,
    name: str,
    actual: Any,
    expected: Any,
) -> dict[str, Any]:
    actual_int = _int(actual)
    expected_int = _int(expected)
    return {
        "group": group,
        "name": name,
        "actual": actual_int,
        "expected": expected_int,
        "passed": actual_int == expected_int,
    }


def _load_matrix_path(path: str | Path, default_filename: str) -> tuple[dict[str, Any], str]:
    value = Path(path)
    resolved = value / default_filename if value.is_dir() else value
    if not resolved.is_file():
        return {}, str(resolved)
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Matrix JSON must be an object: {resolved}")
    return payload, str(resolved)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _format_list(values: list[Any]) -> str:
    return ", ".join(str(value) for value in values) if values else "none"


def _markdown_cell(value: Any) -> str:
    text = str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit whether P6 onboarding and LLM repair readiness targets are met.",
    )
    parser.add_argument(
        "--onboarding-matrix",
        required=True,
        help=(
            "Path to github_onboarding_matrix.json, or a directory containing it."
        ),
    )
    parser.add_argument(
        "--repair-matrix",
        required=True,
        help=(
            "Path to llm_repair_evaluation_matrix.json, or a directory containing it."
        ),
    )
    parser.add_argument("--output-dir", default="", help="Write audit artifacts here.")
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Console output format.",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Exit non-zero unless every P6 readiness check passes.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    onboarding, onboarding_path = _load_matrix_path(
        args.onboarding_matrix,
        "github_onboarding_matrix.json",
    )
    repair, repair_path = _load_matrix_path(
        args.repair_matrix,
        "llm_repair_evaluation_matrix.json",
    )
    audit = build_p6_readiness_audit(
        onboarding,
        repair,
        onboarding_matrix_path=onboarding_path,
        repair_matrix_path=repair_path,
    )
    if args.output_dir:
        write_p6_readiness_audit_artifacts(audit, args.output_dir)
    if args.format == "json":
        print(json.dumps(audit, indent=2, ensure_ascii=False))
    else:
        print(render_p6_readiness_audit_markdown(audit))
    if args.require_complete and audit["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
