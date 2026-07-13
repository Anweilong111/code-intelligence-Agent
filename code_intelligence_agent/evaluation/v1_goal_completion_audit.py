from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


AGENT_LOOP = "Observe -> Plan -> Act -> Verify -> Reflect -> Replan"
REQUIRED_METRIC_IDS = {
    "onboarding_success_rate",
    "topk_localization_accuracy",
    "pass_at_1",
    "pass_at_k",
    "reflection_uplift",
    "blocker_accuracy",
    "sandbox_success_rate",
    "average_runtime_ms",
    "llm_cost_usd",
}
REQUIRED_PUBLIC_DOCS = [
    "README.MD",
    "RESUME_AGENT_PROJECT.md",
    "INTERVIEW_QA_AGENT_PROJECT.md",
    "docs/showcase/github_release_guide.md",
    "docs/examples/README.md",
    "docs/examples/v1_sample_reports.md",
    "docs/examples/top_level_agent_live_smoke.md",
    "docs/examples/llm_repair_readiness.md",
]
BAD_BOUNDARY_PHRASES = [
    "LLM judge " + "可以替代 pytest sandbox",
    "LLM judge 是最终成功标准",
    "可以绕过 pytest sandbox",
]


def build_v1_goal_completion_audit(
    *,
    root: str | Path,
    v1_summary: dict[str, Any] | None = None,
    final_report: dict[str, Any] | None = None,
    controller: dict[str, Any] | None = None,
    test_result: dict[str, Any] | None = None,
    artifact_inventory: dict[str, Any] | None = None,
    release_hygiene: dict[str, Any] | None = None,
    source_paths: dict[str, str] | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    v1 = _dict(v1_summary)
    final = _dict(final_report)
    ctl = _dict(controller)
    test = _dict(test_result)
    inventory = _dict(artifact_inventory)
    hygiene = _dict(release_hygiene)
    checks = [
        _v1_metric_evidence_check(v1),
        _new_repo_smoke_check(final, test, inventory),
        _agent_controller_check(ctl),
        _sandbox_llm_boundary_check(ctl, final),
        _public_docs_check(root_path),
        _sample_reports_check(root_path),
        _release_hygiene_check(hygiene),
    ]
    failed = [check for check in checks if not bool(check.get("passed", False))]
    return {
        "status": "pass" if not failed else "incomplete",
        "reason": (
            "v1_goal_completion_evidence_complete"
            if not failed
            else "v1_goal_completion_evidence_incomplete"
        ),
        "source_paths": _dict(source_paths),
        "check_count": len(checks),
        "passed_check_count": len(checks) - len(failed),
        "failed_check_count": len(failed),
        "failed_checks": [str(check.get("name") or "") for check in failed],
        "checks": checks,
        "completion_statement": (
            "Arbitrary Python GitHub Repo Code Intelligence Agent v1 has "
            "audited evidence for repo understanding, graph modeling, test "
            "diagnosis, Top-k/blocker localization, optional LLM repair "
            "boundaries, sandbox authority, AgentController trace, V1 metrics, "
            "public docs, and release hygiene."
        ),
    }


def render_v1_goal_completion_audit_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# V1 Goal Completion Audit",
        "",
        f"- Status: `{_markdown_cell(audit.get('status'))}`",
        f"- Reason: `{_markdown_cell(audit.get('reason'))}`",
        f"- Checks: `{_int(audit.get('passed_check_count'))}/{_int(audit.get('check_count'))}` pass",
        f"- Agent Loop: `{AGENT_LOOP}`",
        "",
        "## Checks",
        "",
        "| Check | Status | Evidence |",
        "| --- | --- | --- |",
    ]
    for check_value in _list(audit.get("checks")):
        check = _dict(check_value)
        evidence = str(check.get("evidence") or "")
        missing = [str(item) for item in _list(check.get("missing"))]
        if missing:
            evidence = f"{evidence}; missing=" + ", ".join(missing[:12])
        lines.append(
            "| "
            f"{_markdown_cell(check.get('name'))} | "
            f"{'pass' if check.get('passed') else 'fail'} | "
            f"{_markdown_cell(evidence)} |"
        )
    lines.extend(
        [
            "",
            "## Completion Statement",
            "",
            str(audit.get("completion_statement") or ""),
            "",
        ]
    )
    return "\n".join(lines)


def write_v1_goal_completion_audit_artifacts(
    audit: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "v1_goal_completion_audit.json"
    markdown_path = root / "v1_goal_completion_audit.md"
    json_path.write_text(
        json.dumps(audit, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_v1_goal_completion_audit_markdown(audit),
        encoding="utf-8",
    )
    return {
        "v1_goal_completion_audit_json": str(json_path),
        "v1_goal_completion_audit_markdown": str(markdown_path),
    }


def _v1_metric_evidence_check(v1: dict[str, Any]) -> dict[str, Any]:
    summary = _dict(v1.get("summary"))
    metrics = [_dict(item) for item in _list(v1.get("metrics"))]
    metric_ids = {str(item.get("metric_id") or "") for item in metrics}
    missing = sorted(REQUIRED_METRIC_IDS - metric_ids)
    measured = _int(summary.get("measured_metric_count"))
    proxy = _int(summary.get("proxy_metric_count"))
    missing_count = _int(summary.get("missing_metric_count"))
    onboarding = next(
        (item for item in metrics if item.get("metric_id") == "onboarding_success_rate"),
        {},
    )
    passed = bool(
        v1.get("status") == "pass"
        and measured >= len(REQUIRED_METRIC_IDS)
        and proxy == 0
        and missing_count == 0
        and not missing
        and _int(onboarding.get("denominator")) >= 30
    )
    return _check(
        "v1_30_50_9_metric_evidence",
        passed,
        (
            f"status={v1.get('status')}; measured={measured}; proxy={proxy}; "
            f"missing={missing_count}; onboarding={onboarding.get('numerator')}/"
            f"{onboarding.get('denominator')}"
        ),
        missing,
    )


def _new_repo_smoke_check(
    final: dict[str, Any],
    test: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    structure = _dict(final.get("repository_structure"))
    package = _dict(structure.get("package_structure"))
    verification = _dict(final.get("verification"))
    compliance = _dict(final.get("objective_compliance"))
    testability = _dict(final.get("testability"))
    evidence_artifacts = _dict(final.get("evidence_artifacts"))
    required = {
        "repo_profile": _int(structure.get("analyzed_files")) > 0,
        "structure_graph": bool(evidence_artifacts.get("repo_graph_json")),
        "package_layout": bool(package.get("layout_type")),
        "test_diagnosis": bool(testability) and _int(test.get("test_count")) > 0,
        "topk_or_blocker": "top_suspicious_functions" in final
        and bool(final.get("blocker") or final.get("top_suspicious_function")),
        "final_audit": bool(
            verification.get("acceptance_gate_passed")
            and compliance.get("passed")
        ),
        "artifact_inventory": inventory.get("status") == "pass",
    }
    missing = [name for name, ok in required.items() if not ok]
    return _check(
        "new_public_repo_agent_smoke",
        final.get("status") == "pass" and not missing,
        (
            f"repo={final.get('repo_spec')}; files={structure.get('analyzed_files')}; "
            f"tests={test.get('test_count')}; blocker={final.get('blocker')}; "
            f"artifacts={inventory.get('artifact_count')}"
        ),
        missing,
    )


def _agent_controller_check(controller: dict[str, Any]) -> dict[str, Any]:
    action_audit = _dict(controller.get("action_decision_audit"))
    loop_audit = _dict(controller.get("loop_iteration_audit"))
    auto = _dict(controller.get("auto_controller"))
    required = {
        "loop_contract": [str(item) for item in _list(controller.get("control_loop"))]
        == ["observe", "plan", "act", "verify", "reflect", "replan"],
        "action_decision_audit": action_audit.get("status") == "pass"
        and _int(action_audit.get("incomplete_action_count")) == 0,
        "loop_iteration_audit": loop_audit.get("status") == "pass",
        "complete_auto_loop": bool(auto.get("complete_loop_recorded")),
        "blocker_or_next_action": bool(
            controller.get("primary_blocker") or _dict(controller.get("selected_action")).get("id")
        ),
    }
    missing = [name for name, ok in required.items() if not ok]
    return _check(
        "agent_controller_decision_loop",
        not missing,
        (
            f"status={controller.get('status')}; action_audit={action_audit.get('status')}; "
            f"loop_audit={loop_audit.get('status')}; complete_loop={auto.get('complete_loop_recorded')}"
        ),
        missing,
    )


def _sandbox_llm_boundary_check(
    controller: dict[str, Any],
    final: dict[str, Any],
) -> dict[str, Any]:
    llm_audit = _dict(controller.get("llm_repair_action_audit"))
    verification = _dict(final.get("verification"))
    required = {
        "sandbox_authority": llm_audit.get("sandbox_authority")
        == "sandbox_pytest_decides_success",
        "llm_not_final_authority": verification.get("repair_success_claim")
        in {"not_claimed", "sandbox_verified"},
        "llm_loop_evidence": set(_dict(llm_audit.get("agent_loop_evidence")).keys())
        >= {"observe", "plan", "act", "verify", "reflect", "replan"},
    }
    missing = [name for name, ok in required.items() if not ok]
    return _check(
        "llm_repair_sandbox_authority_boundary",
        not missing,
        (
            f"sandbox_authority={llm_audit.get('sandbox_authority')}; "
            f"claim={verification.get('repair_success_claim')}; "
            f"llm_status={llm_audit.get('status')}"
        ),
        missing,
    )


def _public_docs_check(root: Path) -> dict[str, Any]:
    missing_files = [
        path for path in REQUIRED_PUBLIC_DOCS if not (root / path).exists()
    ]
    bad_boundary: list[str] = []
    required_terms: list[str] = []
    for path in REQUIRED_PUBLIC_DOCS:
        doc = root / path
        if not doc.exists():
            continue
        text = doc.read_text(encoding="utf-8", errors="replace")
        if any(phrase in text for phrase in BAD_BOUNDARY_PHRASES):
            bad_boundary.append(path)
    readme_path = root / "README.MD"
    readme = (
        readme_path.read_text(encoding="utf-8", errors="replace")
        if readme_path.exists()
        else ""
    )
    if readme:
        for term in [
            AGENT_LOOP,
            "release_hygiene_audit",
            "sandbox_pytest_decides_success",
        ]:
            if term not in readme:
                required_terms.append(f"README:{term}")
    missing = missing_files + bad_boundary + required_terms
    return _check(
        "public_docs_resume_interview_github_packaging",
        not missing,
        f"docs={len(REQUIRED_PUBLIC_DOCS) - len(missing_files)}/{len(REQUIRED_PUBLIC_DOCS)}",
        missing,
    )


def _sample_reports_check(root: Path) -> dict[str, Any]:
    reports_dir = root / "docs" / "examples" / "v1_reports"
    reports = sorted(reports_dir.glob("*.md")) if reports_dir.exists() else []
    case_reports = [path for path in reports if path.name.lower() != "readme.md"]
    top_level = root / "docs" / "examples" / "top_level_agent_live_smoke.md"
    missing: list[str] = []
    if len(case_reports) < 3:
        missing.append("at_least_3_v1_case_reports")
    if not top_level.exists():
        missing.append("top_level_agent_live_smoke.md")
    return _check(
        "github_showcase_sample_reports",
        not missing,
        f"case_reports={len(case_reports)}; top_level={top_level.exists()}",
        missing,
    )


def _release_hygiene_check(hygiene: dict[str, Any]) -> dict[str, Any]:
    missing = []
    if hygiene.get("status") != "pass":
        missing.append("release_hygiene_status_pass")
    if _int(hygiene.get("failed_check_count")) != 0:
        missing.extend([str(item) for item in _list(hygiene.get("failed_checks"))])
    return _check(
        "release_hygiene_gate",
        not missing,
        (
            f"status={hygiene.get('status')}; checks="
            f"{hygiene.get('passed_check_count')}/{hygiene.get('check_count')}; "
            f"candidates={hygiene.get('candidate_file_count')}"
        ),
        missing,
    )


def _check(
    name: str,
    passed: bool,
    evidence: str,
    missing: list[str],
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "evidence": evidence,
        "missing": missing,
    }


def _load_json(path: str | Path) -> dict[str, Any]:
    if not str(path):
        return {}
    file_path = Path(path)
    if not file_path.exists():
        return {}
    return _dict(json.loads(file_path.read_text(encoding="utf-8")))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an end-to-end V1 goal completion audit."
    )
    parser.add_argument("output_dir")
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--v1-summary",
        default="outputs_smoke/v1_evaluation_summary_complete_current/v1_evaluation_summary.json",
    )
    parser.add_argument(
        "--final-report",
        default="outputs_smoke/final_new_repo_iniconfig_current/final_report.json",
    )
    parser.add_argument(
        "--controller",
        default="outputs_smoke/final_new_repo_iniconfig_current/github_repo_agent_controller.json",
    )
    parser.add_argument(
        "--test-result",
        default="outputs_smoke/final_new_repo_iniconfig_current/repository_test_execution_result.json",
    )
    parser.add_argument(
        "--artifact-inventory",
        default="outputs_smoke/final_new_repo_iniconfig_current/artifact_inventory.json",
    )
    parser.add_argument(
        "--release-hygiene",
        default="outputs_smoke/release_hygiene_audit_current/release_hygiene_audit.json",
    )
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--require-pass", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    source_paths = {
        "v1_summary": args.v1_summary,
        "final_report": args.final_report,
        "controller": args.controller,
        "test_result": args.test_result,
        "artifact_inventory": args.artifact_inventory,
        "release_hygiene": args.release_hygiene,
    }
    audit = build_v1_goal_completion_audit(
        root=args.root,
        v1_summary=_load_json(args.v1_summary),
        final_report=_load_json(args.final_report),
        controller=_load_json(args.controller),
        test_result=_load_json(args.test_result),
        artifact_inventory=_load_json(args.artifact_inventory),
        release_hygiene=_load_json(args.release_hygiene),
        source_paths=source_paths,
    )
    write_v1_goal_completion_audit_artifacts(audit, args.output_dir)
    if args.format == "markdown":
        print(render_v1_goal_completion_audit_markdown(audit))
    else:
        print(json.dumps(audit, indent=2, ensure_ascii=False))
    if args.require_pass and audit["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
