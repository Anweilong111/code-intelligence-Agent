from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.release_hygiene_audit import (
    build_release_hygiene_audit,
)


AGENT_LOOP = "Observe -> Plan -> Act -> Verify -> Reflect -> Replan"
DEFAULT_READINESS_AUDIT = (
    "outputs_smoke/v1_readiness_dataset_audit_current/"
    "v1_readiness_dataset_audit.json"
)
DEFAULT_EVALUATION_SUMMARY = (
    "outputs_smoke/v1_evaluation_summary_complete_current/"
    "v1_evaluation_summary.json"
)
DEFAULT_GOAL_AUDIT = (
    "outputs_smoke/v1_goal_completion_audit_current/"
    "v1_goal_completion_audit.json"
)
REPRODUCTION_COMMAND = (
    "python -m code_intelligence_agent.evaluation.v1_baseline "
    "outputs/v1_baseline --run-tests --require-pass"
)
CAPABILITY_BOUNDARIES = [
    "The baseline targets public Python GitHub repositories, not every language.",
    "Repository analysis is general; verified repair still requires executable test evidence.",
    "Dependency, network, credential, timeout, and configuration failures are reported as blockers.",
    "LLM judge scores are advisory; sandbox pytest remains the repair authority.",
    "A clean repository may correctly produce no patch candidate.",
]


def build_v1_baseline(
    *,
    readiness_audit: dict[str, Any],
    evaluation_summary: dict[str, Any],
    goal_audit: dict[str, Any],
    release_hygiene: dict[str, Any],
    test_summary: dict[str, Any],
    baseline_ref: str = "v1-baseline",
    source_paths: dict[str, str] | None = None,
) -> dict[str, Any]:
    readiness_summary = _dict(readiness_audit.get("summary"))
    evaluation = _dict(evaluation_summary.get("summary"))
    tests = _dict(test_summary)
    checks = [
        _check(
            "dataset_readiness",
            readiness_audit.get("status") == "pass",
            (
                f"onboarding={_int(readiness_summary.get('onboarding_case_count'))}; "
                f"repair={_int(readiness_summary.get('repair_case_count'))}; "
                "required_metrics="
                f"{_int(readiness_summary.get('required_metric_contract_count'))}"
            ),
        ),
        _check(
            "evaluation_metrics",
            evaluation_summary.get("status") == "pass"
            and _int(evaluation.get("measured_metric_count"))
            == _int(evaluation.get("required_metric_count"))
            and _int(evaluation.get("proxy_metric_count")) == 0
            and _int(evaluation.get("missing_metric_count")) == 0,
            (
                f"measured={_int(evaluation.get('measured_metric_count'))}; "
                f"required={_int(evaluation.get('required_metric_count'))}; "
                f"proxy={_int(evaluation.get('proxy_metric_count'))}; "
                f"missing={_int(evaluation.get('missing_metric_count'))}"
            ),
        ),
        _check(
            "v1_goal_audit",
            goal_audit.get("status") == "pass",
            (
                f"passed={_int(goal_audit.get('passed_check_count'))}; "
                f"checks={_int(goal_audit.get('check_count'))}"
            ),
        ),
        _check(
            "full_test_suite",
            tests.get("status") == "pass"
            and _int(tests.get("passed_count")) > 0
            and _int(tests.get("failed_count")) == 0,
            (
                f"passed={_int(tests.get('passed_count'))}; "
                f"failed={_int(tests.get('failed_count'))}; "
                f"duration_seconds={_float(tests.get('duration_seconds')):.2f}"
            ),
        ),
        _check(
            "release_hygiene",
            release_hygiene.get("status") == "pass",
            (
                f"passed={_int(release_hygiene.get('passed_check_count'))}; "
                f"checks={_int(release_hygiene.get('check_count'))}"
            ),
        ),
        _check(
            "agent_loop_contract",
            readiness_summary.get("agent_loop") == AGENT_LOOP
            and evaluation.get("agent_loop") == AGENT_LOOP,
            AGENT_LOOP,
        ),
    ]
    failed_checks = [item["name"] for item in checks if not item["passed"]]
    metrics = [_dict(item) for item in _list(evaluation_summary.get("metrics"))]
    return {
        "schema_version": "1.0",
        "baseline_name": "code-intelligence-agent-v1",
        "baseline_ref": str(baseline_ref or "v1-baseline"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if not failed_checks else "incomplete",
        "reason": (
            "v1_baseline_evidence_complete"
            if not failed_checks
            else "v1_baseline_evidence_incomplete"
        ),
        "agent_loop": AGENT_LOOP,
        "source_paths": _dict(source_paths),
        "summary": {
            "check_count": len(checks),
            "passed_check_count": len(checks) - len(failed_checks),
            "failed_check_count": len(failed_checks),
            "onboarding_case_count": _int(
                readiness_summary.get("onboarding_case_count")
            ),
            "repair_case_count": _int(readiness_summary.get("repair_case_count")),
            "required_metric_count": _int(
                readiness_summary.get("required_metric_contract_count")
            ),
            "measured_metric_count": _int(evaluation.get("measured_metric_count")),
            "test_passed_count": _int(tests.get("passed_count")),
            "test_duration_seconds": _float(tests.get("duration_seconds")),
        },
        "checks": checks,
        "failed_checks": failed_checks,
        "metrics": metrics,
        "test_suite": tests,
        "capability_boundaries": list(CAPABILITY_BOUNDARIES),
        "reproduction": {
            "command": REPRODUCTION_COMMAND,
            "requires": [
                "V1 evidence artifacts at the recorded source paths",
                "Python test dependencies installed",
                "network/provider credentials only for refreshing live evidence",
            ],
        },
    }


def render_v1_baseline_markdown(payload: dict[str, Any]) -> str:
    summary = _dict(payload.get("summary"))
    tests = _dict(payload.get("test_suite"))
    lines = [
        "# Code Intelligence Agent V1 Baseline",
        "",
        f"- Status: `{_md(payload.get('status'))}`",
        f"- Baseline Ref: `{_md(payload.get('baseline_ref'))}`",
        f"- Agent Loop: `{AGENT_LOOP}`",
        f"- Evidence Checks: {_int(summary.get('passed_check_count'))}/{_int(summary.get('check_count'))} pass",
        f"- Full Test Suite: {_int(tests.get('passed_count'))} passed, {_int(tests.get('failed_count'))} failed in {_float(tests.get('duration_seconds')):.2f}s",
        "",
        "## Dataset And Evaluation Scope",
        "",
        "| Item | Value |",
        "| --- | ---: |",
        f"| Public GitHub onboarding cases | {_int(summary.get('onboarding_case_count'))} |",
        f"| Repair/evaluation cases | {_int(summary.get('repair_case_count'))} |",
        f"| Required metric contracts | {_int(summary.get('required_metric_count'))} |",
        f"| Directly measured metrics | {_int(summary.get('measured_metric_count'))} |",
        "",
        "## Baseline Metrics",
        "",
        "| Metric | Evidence Status | Value | Note |",
        "| --- | --- | ---: | --- |",
    ]
    for metric in [_dict(item) for item in _list(payload.get("metrics"))]:
        lines.append(
            "| "
            f"`{_md(metric.get('metric_id'))}` | "
            f"`{_md(metric.get('evidence_status'))}` | "
            f"{_md(metric.get('value'))} | "
            f"{_md(metric.get('reason'))} |"
        )
    lines.extend(
        [
            "",
            "## Evidence Gates",
            "",
            "| Gate | Status | Evidence |",
            "| --- | --- | --- |",
        ]
    )
    for check in [_dict(item) for item in _list(payload.get("checks"))]:
        lines.append(
            f"| `{_md(check.get('name'))}` | "
            f"`{'pass' if check.get('passed') else 'fail'}` | "
            f"{_md(check.get('evidence'))} |"
        )
    lines.extend(["", "## Capability Boundaries", ""])
    for boundary in _list(payload.get("capability_boundaries")):
        lines.append(f"- {boundary}")
    lines.extend(
        [
            "",
            "## Reproduce",
            "",
            "```powershell",
            str(_dict(payload.get("reproduction")).get("command") or REPRODUCTION_COMMAND),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def write_v1_baseline_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "baseline_metrics.json"
    markdown_path = root / "baseline_metrics.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_v1_baseline_markdown(payload),
        encoding="utf-8",
    )
    return {
        "baseline_metrics_json": str(json_path),
        "baseline_metrics_markdown": str(markdown_path),
    }


def run_full_test_suite(root: str | Path) -> dict[str, Any]:
    command = [sys.executable, "-m", "pytest", "-q"]
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=str(Path(root).resolve()),
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.perf_counter() - started
    parsed = parse_pytest_summary(completed.stdout + "\n" + completed.stderr)
    parsed.update(
        {
            "status": "pass" if completed.returncode == 0 else "fail",
            "command": "python -m pytest -q",
            "returncode": completed.returncode,
            "duration_seconds": parsed.get("duration_seconds") or round(elapsed, 2),
            "output_tail": "\n".join(
                (completed.stdout + "\n" + completed.stderr).splitlines()[-20:]
            ),
        }
    )
    return parsed


def parse_pytest_summary(text: str) -> dict[str, Any]:
    return {
        "passed_count": _summary_count(text, "passed"),
        "failed_count": _summary_count(text, "failed"),
        "skipped_count": _summary_count(text, "skipped"),
        "duration_seconds": _summary_duration(text),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a reproducible V1 baseline from audited evidence."
    )
    parser.add_argument("output_dir")
    parser.add_argument("--root", default=".")
    parser.add_argument("--readiness-audit", default=DEFAULT_READINESS_AUDIT)
    parser.add_argument("--evaluation-summary", default=DEFAULT_EVALUATION_SUMMARY)
    parser.add_argument("--goal-audit", default=DEFAULT_GOAL_AUDIT)
    parser.add_argument("--baseline-ref", default="v1-baseline")
    parser.add_argument("--run-tests", action="store_true")
    parser.add_argument("--test-passed-count", type=int, default=0)
    parser.add_argument("--test-failed-count", type=int, default=0)
    parser.add_argument("--test-duration-seconds", type=float, default=0.0)
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument("--require-pass", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.run_tests:
        test_summary = run_full_test_suite(root)
    else:
        test_summary = {
            "status": (
                "pass"
                if args.test_passed_count > 0 and args.test_failed_count == 0
                else "missing"
            ),
            "command": "python -m pytest -q",
            "returncode": 0 if args.test_failed_count == 0 else 1,
            "passed_count": args.test_passed_count,
            "failed_count": args.test_failed_count,
            "skipped_count": 0,
            "duration_seconds": args.test_duration_seconds,
        }
    source_paths = {
        "readiness_audit": args.readiness_audit,
        "evaluation_summary": args.evaluation_summary,
        "goal_audit": args.goal_audit,
        "release_hygiene": "generated_from_current_git_candidate_set",
    }
    payload = build_v1_baseline(
        readiness_audit=_load_json(root / args.readiness_audit),
        evaluation_summary=_load_json(root / args.evaluation_summary),
        goal_audit=_load_json(root / args.goal_audit),
        release_hygiene=build_release_hygiene_audit(root),
        test_summary=test_summary,
        baseline_ref=args.baseline_ref,
        source_paths=source_paths,
    )
    write_v1_baseline_artifacts(payload, args.output_dir)
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_v1_baseline_markdown(payload), end="")
    if args.require_pass and payload["status"] != "pass":
        raise SystemExit(1)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return _dict(json.loads(path.read_text(encoding="utf-8")))


def _check(name: str, passed: bool, evidence: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "evidence": evidence}


def _summary_count(text: str, label: str) -> int:
    matches = re.findall(rf"\b(\d+)\s+{re.escape(label)}\b", text)
    return int(matches[-1]) if matches else 0


def _summary_duration(text: str) -> float:
    matches = re.findall(r"\bin\s+([0-9]+(?:\.[0-9]+)?)s\b", text)
    return float(matches[-1]) if matches else 0.0


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


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    main()
