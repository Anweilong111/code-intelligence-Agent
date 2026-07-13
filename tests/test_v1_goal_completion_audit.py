from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from code_intelligence_agent.evaluation.v1_goal_completion_audit import (
    AGENT_LOOP,
    build_v1_goal_completion_audit,
    render_v1_goal_completion_audit_markdown,
    write_v1_goal_completion_audit_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]


def test_v1_goal_completion_audit_passes_with_complete_fixture(tmp_path):
    _write_public_docs(tmp_path)

    audit = build_v1_goal_completion_audit(
        root=tmp_path,
        v1_summary=_v1_summary(),
        final_report=_final_report(),
        controller=_controller(),
        test_result=_test_result(),
        artifact_inventory=_artifact_inventory(),
        release_hygiene=_release_hygiene(),
    )

    assert audit["status"] == "pass"
    assert audit["failed_checks"] == []
    assert audit["passed_check_count"] == audit["check_count"]


def test_v1_goal_completion_audit_reports_missing_evidence(tmp_path):
    _write_public_docs(tmp_path, include_bad_boundary=True)
    broken_v1 = _v1_summary()
    broken_v1["summary"]["measured_metric_count"] = 8

    audit = build_v1_goal_completion_audit(
        root=tmp_path,
        v1_summary=broken_v1,
        final_report={},
        controller={},
        test_result={},
        artifact_inventory={},
        release_hygiene={"status": "fail", "failed_check_count": 1},
    )
    failed = set(audit["failed_checks"])

    assert audit["status"] == "incomplete"
    assert "v1_30_50_9_metric_evidence" in failed
    assert "new_public_repo_agent_smoke" in failed
    assert "agent_controller_decision_loop" in failed
    assert "release_hygiene_gate" in failed
    assert "public_docs_resume_interview_github_packaging" in failed


def test_v1_goal_completion_audit_cli_writes_artifacts(tmp_path):
    docs_root = tmp_path / "repo"
    _write_public_docs(docs_root)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    paths = {
        "v1": _write_json(evidence_root / "v1.json", _v1_summary()),
        "final": _write_json(evidence_root / "final.json", _final_report()),
        "controller": _write_json(evidence_root / "controller.json", _controller()),
        "test": _write_json(evidence_root / "test.json", _test_result()),
        "inventory": _write_json(evidence_root / "inventory.json", _artifact_inventory()),
        "hygiene": _write_json(evidence_root / "hygiene.json", _release_hygiene()),
    }
    output_dir = tmp_path / "audit"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_intelligence_agent.evaluation.v1_goal_completion_audit",
            str(output_dir),
            "--root",
            str(docs_root),
            "--v1-summary",
            str(paths["v1"]),
            "--final-report",
            str(paths["final"]),
            "--controller",
            str(paths["controller"]),
            "--test-result",
            str(paths["test"]),
            "--artifact-inventory",
            str(paths["inventory"]),
            "--release-hygiene",
            str(paths["hygiene"]),
            "--format",
            "json",
            "--require-pass",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    payload = json.loads((output_dir / "v1_goal_completion_audit.json").read_text())
    markdown = (output_dir / "v1_goal_completion_audit.md").read_text(encoding="utf-8")

    assert payload["status"] == "pass"
    assert json.loads(completed.stdout)["status"] == "pass"
    assert "V1 Goal Completion Audit" in markdown
    assert AGENT_LOOP in markdown


def test_current_workspace_v1_goal_completion_audit_passes():
    audit = build_v1_goal_completion_audit(
        root=ROOT,
        v1_summary=json.loads(
            (
                ROOT
                / "outputs_smoke"
                / "v1_evaluation_summary_complete_current"
                / "v1_evaluation_summary.json"
            ).read_text(encoding="utf-8")
        ),
        final_report=json.loads(
            (
                ROOT
                / "outputs_smoke"
                / "final_new_repo_iniconfig_current"
                / "final_report.json"
            ).read_text(encoding="utf-8")
        ),
        controller=json.loads(
            (
                ROOT
                / "outputs_smoke"
                / "final_new_repo_iniconfig_current"
                / "github_repo_agent_controller.json"
            ).read_text(encoding="utf-8")
        ),
        test_result=json.loads(
            (
                ROOT
                / "outputs_smoke"
                / "final_new_repo_iniconfig_current"
                / "repository_test_execution_result.json"
            ).read_text(encoding="utf-8")
        ),
        artifact_inventory=json.loads(
            (
                ROOT
                / "outputs_smoke"
                / "final_new_repo_iniconfig_current"
                / "artifact_inventory.json"
            ).read_text(encoding="utf-8")
        ),
        release_hygiene=json.loads(
            (
                ROOT
                / "outputs_smoke"
                / "release_hygiene_audit_current"
                / "release_hygiene_audit.json"
            ).read_text(encoding="utf-8")
        ),
    )

    assert audit["status"] == "pass"


def test_v1_goal_completion_audit_markdown_and_write_helpers(tmp_path):
    audit = build_v1_goal_completion_audit(
        root=tmp_path,
        v1_summary={},
        final_report={},
        controller={},
        test_result={},
        artifact_inventory={},
        release_hygiene={},
    )

    paths = write_v1_goal_completion_audit_artifacts(audit, tmp_path / "out")
    markdown = render_v1_goal_completion_audit_markdown(audit)

    assert Path(paths["v1_goal_completion_audit_json"]).exists()
    assert Path(paths["v1_goal_completion_audit_markdown"]).exists()
    assert "V1 Goal Completion Audit" in markdown


def _write_public_docs(root: Path, *, include_bad_boundary: bool = False) -> None:
    docs = [
        "README.MD",
        "RESUME_AGENT_PROJECT.md",
        "INTERVIEW_QA_AGENT_PROJECT.md",
        "docs/showcase/github_release_guide.md",
        "docs/examples/README.md",
        "docs/examples/v1_sample_reports.md",
        "docs/examples/top_level_agent_live_smoke.md",
        "docs/examples/llm_repair_readiness.md",
    ]
    for path in docs:
        file_path = root / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        text = (
            f"{AGENT_LOOP}\n"
            "release_hygiene_audit\n"
            "sandbox_pytest_decides_success\n"
            "30/30 50 9/9\n"
        )
        if include_bad_boundary and path == "README.MD":
            text += "LLM judge " + "可以替代 pytest sandbox\n"
        file_path.write_text(text, encoding="utf-8")
    reports = root / "docs" / "examples" / "v1_reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "README.md").write_text(AGENT_LOOP, encoding="utf-8")
    for name in ["a.md", "b.md", "c.md"]:
        (reports / name).write_text(AGENT_LOOP, encoding="utf-8")


def _v1_summary() -> dict:
    metrics = [
        {"metric_id": metric_id, "evidence_status": "measured", "value": 1}
        for metric_id in [
            "topk_localization_accuracy",
            "pass_at_1",
            "pass_at_k",
            "reflection_uplift",
            "blocker_accuracy",
            "sandbox_success_rate",
            "average_runtime_ms",
            "llm_cost_usd",
        ]
    ]
    metrics.append(
        {
            "metric_id": "onboarding_success_rate",
            "evidence_status": "measured",
            "value": 1,
            "numerator": 30,
            "denominator": 30,
        }
    )
    return {
        "status": "pass",
        "summary": {
            "measured_metric_count": 9,
            "proxy_metric_count": 0,
            "missing_metric_count": 0,
        },
        "metrics": metrics,
    }


def _final_report() -> dict:
    return {
        "status": "pass",
        "repo_spec": "https://github.com/example/project",
        "repository_structure": {
            "analyzed_files": 5,
            "functions": 10,
            "package_structure": {"layout_type": "src_layout"},
        },
        "testability": {"status": "tests_passed"},
        "top_suspicious_functions": [],
        "blocker": "no_static_candidates",
        "verification": {
            "acceptance_gate_passed": True,
            "repair_success_claim": "not_claimed",
        },
        "objective_compliance": {"passed": True},
        "evidence_artifacts": {"repo_graph_json": "repo_graph.json"},
    }


def _controller() -> dict:
    return {
        "status": "ready",
        "control_loop": ["observe", "plan", "act", "verify", "reflect", "replan"],
        "primary_blocker": "no_static_candidates",
        "selected_action": {"id": "run_repository_tests_with_checkout"},
        "action_decision_audit": {
            "status": "pass",
            "incomplete_action_count": 0,
        },
        "loop_iteration_audit": {"status": "pass"},
        "auto_controller": {"complete_loop_recorded": True},
        "llm_repair_action_audit": {
            "status": "not_applicable",
            "sandbox_authority": "sandbox_pytest_decides_success",
            "agent_loop_evidence": {
                "observe": "",
                "plan": "",
                "act": "",
                "verify": "",
                "reflect": "",
                "replan": "",
            },
        },
    }


def _test_result() -> dict:
    return {"status": "pass", "test_count": 10}


def _artifact_inventory() -> dict:
    return {"status": "pass", "artifact_count": 20}


def _release_hygiene() -> dict:
    return {
        "status": "pass",
        "passed_check_count": 5,
        "check_count": 5,
        "failed_check_count": 0,
        "failed_checks": [],
        "candidate_file_count": 100,
    }


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
