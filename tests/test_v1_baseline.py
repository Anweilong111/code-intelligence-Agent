from __future__ import annotations

import json
from pathlib import Path

from code_intelligence_agent.evaluation.v1_baseline import (
    AGENT_LOOP,
    build_v1_baseline,
    main,
    parse_pytest_summary,
    render_v1_baseline_markdown,
)


def test_v1_baseline_combines_tests_metrics_and_release_evidence():
    payload = build_v1_baseline(
        readiness_audit=_readiness(),
        evaluation_summary=_evaluation(),
        goal_audit={"status": "pass", "passed_check_count": 7, "check_count": 7},
        release_hygiene={
            "status": "pass",
            "passed_check_count": 5,
            "check_count": 5,
        },
        test_summary={
            "status": "pass",
            "passed_count": 1123,
            "failed_count": 0,
            "duration_seconds": 696.67,
        },
    )

    assert payload["status"] == "pass"
    assert payload["failed_checks"] == []
    assert payload["summary"]["onboarding_case_count"] == 30
    assert payload["summary"]["repair_case_count"] == 50
    assert payload["summary"]["test_passed_count"] == 1123
    assert len(payload["metrics"]) == 9
    assert AGENT_LOOP in render_v1_baseline_markdown(payload)


def test_v1_baseline_refuses_incomplete_or_failed_evidence():
    evaluation = _evaluation()
    evaluation["summary"]["missing_metric_count"] = 1
    payload = build_v1_baseline(
        readiness_audit=_readiness(),
        evaluation_summary=evaluation,
        goal_audit={"status": "incomplete"},
        release_hygiene={"status": "fail"},
        test_summary={"status": "fail", "passed_count": 5, "failed_count": 1},
    )

    assert payload["status"] == "incomplete"
    assert set(payload["failed_checks"]) == {
        "evaluation_metrics",
        "v1_goal_audit",
        "full_test_suite",
        "release_hygiene",
    }


def test_parse_pytest_summary_handles_pass_and_failure_counts():
    parsed = parse_pytest_summary(
        "1 failed, 1123 passed, 2 skipped in 696.67s (0:11:36)"
    )

    assert parsed == {
        "passed_count": 1123,
        "failed_count": 1,
        "skipped_count": 2,
        "duration_seconds": 696.67,
    }


def test_v1_baseline_cli_writes_required_artifact_names(tmp_path):
    readiness = _write_json(tmp_path / "readiness.json", _readiness())
    evaluation = _write_json(tmp_path / "evaluation.json", _evaluation())
    goal = _write_json(
        tmp_path / "goal.json",
        {"status": "pass", "passed_check_count": 7, "check_count": 7},
    )
    _write_release_files(tmp_path)
    output = tmp_path / "baseline"

    main(
        [
            str(output),
            "--root",
            str(tmp_path),
            "--readiness-audit",
            str(readiness.relative_to(tmp_path)),
            "--evaluation-summary",
            str(evaluation.relative_to(tmp_path)),
            "--goal-audit",
            str(goal.relative_to(tmp_path)),
            "--test-passed-count",
            "10",
            "--test-duration-seconds",
            "1.25",
            "--format",
            "json",
            "--require-pass",
        ]
    )

    payload = json.loads((output / "baseline_metrics.json").read_text(encoding="utf-8"))
    assert payload["status"] == "pass"
    assert (output / "baseline_metrics.md").exists()


def _readiness() -> dict:
    return {
        "status": "pass",
        "summary": {
            "onboarding_case_count": 30,
            "repair_case_count": 50,
            "required_metric_contract_count": 9,
            "agent_loop": AGENT_LOOP,
        },
    }


def _evaluation() -> dict:
    return {
        "status": "pass",
        "summary": {
            "required_metric_count": 9,
            "measured_metric_count": 9,
            "proxy_metric_count": 0,
            "missing_metric_count": 0,
            "agent_loop": AGENT_LOOP,
        },
        "metrics": [
            {
                "metric_id": f"metric_{index}",
                "evidence_status": "measured",
                "value": 1.0,
                "reason": "fixture",
            }
            for index in range(9)
        ],
    }


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_release_files(root: Path) -> None:
    (root / ".gitignore").write_text(
        "outputs/\noutputs_v2/\noutputs_v3/\noutputs_demo/\noutputs_smoke/\noutputs_live/\n"
        "htmlcov/\n.pytest_cache/\n*.docx\n.env\n.env.*\n",
        encoding="utf-8",
    )
