import json
from pathlib import Path

import pytest

from code_intelligence_agent.evaluation.github_onboarding_matrix import (
    REQUIRED_ONBOARDING_ARTIFACTS,
    REQUIRED_SCENARIOS,
)
from code_intelligence_agent.evaluation.p6_readiness_audit import (
    P6_READINESS_TARGETS,
    build_p6_readiness_audit,
    main,
    render_p6_readiness_audit_markdown,
    write_p6_readiness_audit_artifacts,
)


def test_p6_readiness_audit_passes_when_onboarding_and_repair_targets_met():
    audit = build_p6_readiness_audit(
        _complete_onboarding_matrix(),
        _complete_repair_matrix(),
        onboarding_matrix_path="out/onboarding/github_onboarding_matrix.json",
        repair_matrix_path="out/repair/llm_repair_evaluation_matrix.json",
    )
    markdown = render_p6_readiness_audit_markdown(audit)

    assert audit["status"] == "pass"
    assert audit["reason"] == "p6_readiness_targets_met"
    assert audit["summary"]["failed_check_count"] == 0
    assert audit["missing"] == []
    assert all(check["passed"] is True for check in audit["target_checks"])
    assert audit["onboarding"]["covered_scenario_count"] == len(REQUIRED_SCENARIOS)
    assert audit["repair"]["agent_loop_trace_complete_count"] == 20
    assert audit["sandbox_authority"] == "sandbox_pytest_decides_success"
    assert "P6 Readiness Audit" in markdown
    assert "Observe -> Plan -> Act -> Verify -> Reflect -> Replan" in markdown


def test_p6_readiness_audit_reports_missing_onboarding_and_repair_targets():
    onboarding = _complete_onboarding_matrix()
    onboarding["status"] = "incomplete"
    onboarding["case_count"] = 2
    onboarding["passed_check_count"] = 1
    onboarding["check_count"] = 3
    first_scenario = REQUIRED_SCENARIOS[0][0]
    onboarding["scenario_coverage"][first_scenario] = {"count": 0, "runs": []}
    first_artifact = REQUIRED_ONBOARDING_ARTIFACTS[0][0]
    onboarding["artifact_coverage"][first_artifact] = {"present": 1, "missing": 1}
    onboarding["rows"][0]["policy_trace"] = {"present": False}

    repair = _complete_repair_matrix()
    repair["status"] = "incomplete"
    metrics = repair["metrics_report"]
    metrics["status"] = "incomplete"
    metrics["case_count"] = 3
    metrics["llm_direct_success_count"] = 1
    metrics["llm_reflection_success_count"] = 0
    metrics["llm_blocker_count"] = 1
    metrics["patch_judge_llm_ready_case_count"] = 0
    metrics["patch_judge_accept_success_count"] = 0
    metrics["patch_judge_reject_failure_count"] = 0
    metrics["environment_blocker_count"] = 0
    metrics["agent_loop_trace_complete_count"] = 1
    metrics["sandbox_authority"] = "llm_judge"

    audit = build_p6_readiness_audit(onboarding, repair)

    assert audit["status"] == "incomplete"
    assert "onboarding_case_count" in audit["missing"]
    assert "onboarding_scenario_coverage" in audit["missing"]
    assert "onboarding_artifact_groups_complete" in audit["missing"]
    assert "onboarding_agent_policy_trace_complete" in audit["missing"]
    assert "repair_case_count" in audit["missing"]
    assert "llm_direct_success" in audit["missing"]
    assert "llm_reflection_success" in audit["missing"]
    assert "llm_patch_judge_ready" in audit["missing"]
    assert "environment_blocker" in audit["missing"]
    assert "sandbox_authority" in audit["missing"]
    assert any("10 real Python GitHub onboarding" in item for item in audit["next_actions"])
    assert any("LLM repair evaluation suite" in item for item in audit["next_actions"])
    assert any("sandbox pytest" in item for item in audit["next_actions"])


def test_p6_readiness_audit_accepts_filename_artifact_coverage_and_policy_status():
    onboarding = _complete_onboarding_matrix()
    case_count = onboarding["case_count"]
    onboarding["artifact_coverage"] = {
        filename: {"present": case_count, "missing": 0}
        for _name, _path_key, filename in REQUIRED_ONBOARDING_ARTIFACTS
    }
    onboarding["rows"][0]["policy_trace"] = {
        "present": True,
        "status": "warning",
        "selected_action": "expand_static_candidate_search",
        "canonical_action": "expand_static_candidate_search",
        "loop": [],
    }

    audit = build_p6_readiness_audit(onboarding, _complete_repair_matrix())

    assert audit["status"] == "pass"
    assert audit["onboarding"]["complete_artifact_group_count"] == len(
        REQUIRED_ONBOARDING_ARTIFACTS
    )
    assert audit["onboarding"]["agent_policy_trace_complete"] is True


def test_p6_readiness_audit_cli_writes_artifacts_and_requires_complete(
    tmp_path,
    capsys,
):
    onboarding_dir = tmp_path / "onboarding"
    repair_dir = tmp_path / "repair"
    output_dir = tmp_path / "audit"
    onboarding_dir.mkdir()
    repair_dir.mkdir()
    _write_json(
        onboarding_dir / "github_onboarding_matrix.json",
        _complete_onboarding_matrix(),
    )
    incomplete_repair = _complete_repair_matrix()
    incomplete_repair["status"] = "incomplete"
    incomplete_repair["metrics_report"]["status"] = "incomplete"
    incomplete_repair["metrics_report"]["case_count"] = 0
    _write_json(
        repair_dir / "llm_repair_evaluation_matrix.json",
        incomplete_repair,
    )

    main(
        [
            "--onboarding-matrix",
            str(onboarding_dir),
            "--repair-matrix",
            str(repair_dir),
            "--output-dir",
            str(output_dir),
            "--format",
            "json",
        ]
    )
    stdout = capsys.readouterr().out
    payload = json.loads((output_dir / "p6_readiness_audit.json").read_text())
    markdown = (output_dir / "p6_readiness_audit.md").read_text(encoding="utf-8")

    assert payload["status"] == "incomplete"
    assert "repair_case_count" in payload["missing"]
    assert "P6 Readiness Audit" in markdown
    assert "sk-" not in stdout
    assert "sk-" not in json.dumps(payload)

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--onboarding-matrix",
                str(onboarding_dir),
                "--repair-matrix",
                str(repair_dir),
                "--require-complete",
            ]
        )
    assert exc.value.code == 1


def test_p6_readiness_audit_writes_pass_artifacts(tmp_path):
    audit = build_p6_readiness_audit(
        _complete_onboarding_matrix(),
        _complete_repair_matrix(),
    )
    paths = write_p6_readiness_audit_artifacts(audit, tmp_path)

    assert Path(paths["p6_readiness_audit_json"]).exists()
    assert Path(paths["p6_readiness_audit_markdown"]).exists()
    payload = json.loads(Path(paths["p6_readiness_audit_json"]).read_text())
    assert payload["status"] == "pass"


def _complete_onboarding_matrix() -> dict:
    case_count = P6_READINESS_TARGETS["onboarding_case_count"]
    return {
        "status": "pass",
        "reason": "p6_onboarding_matrix_complete",
        "required_case_count": case_count,
        "case_count": case_count,
        "passed_check_count": 4,
        "check_count": 4,
        "required_scenarios": [
            {"id": scenario_id, "description": description}
            for scenario_id, description in REQUIRED_SCENARIOS
        ],
        "scenario_coverage": {
            scenario_id: {"count": 1, "runs": [f"repo_{index}"]}
            for index, (scenario_id, _description) in enumerate(REQUIRED_SCENARIOS)
        },
        "artifact_coverage": {
            artifact_name: {"present": case_count, "missing": 0}
            for artifact_name, _path_key, _filename in REQUIRED_ONBOARDING_ARTIFACTS
        },
        "rows": [
            {
                "name": f"repo_{index}",
                "repo": f"example/repo_{index}",
                "status": "pass",
                "missing_required_artifacts": [],
                "policy_trace": {
                    "present": True,
                    "canonical_action": "generate_final_agent_report",
                    "loop": [
                        "observe",
                        "plan",
                        "act",
                        "verify",
                        "reflect",
                        "replan",
                    ],
                },
            }
            for index in range(case_count)
        ],
    }


def _complete_repair_matrix() -> dict:
    metrics = {
        "status": "pass",
        "reason": "p6_llm_repair_metrics_targets_met",
        "target_summary": {
            "all_targets_met": True,
            "failed_target_count": 0,
        },
        "case_count": 20,
        "llm_direct_success_count": 5,
        "llm_reflection_success_count": 3,
        "llm_blocker_count": 12,
        "llm_direct_evidence_complete_count": 5,
        "llm_reflection_evidence_complete_count": 3,
        "llm_blocker_evidence_complete_count": 12,
        "patch_judge_llm_ready_case_count": 8,
        "patch_judge_accept_success_count": 8,
        "patch_judge_reject_failure_count": 3,
        "llm_failed_blocker_count": 3,
        "environment_blocker_count": 3,
        "no_test_oracle_blocker_count": 3,
        "safety_gate_blocker_count": 3,
        "agent_loop_trace_complete_count": 20,
        "sandbox_authority": "sandbox_pytest_decides_success",
        "blocker_category_counts": {
            "llm_failed_blocker": 3,
            "environment_blocker": 3,
            "no_test_oracle_blocker": 3,
            "safety_gate_blocker": 3,
        },
        "patch_judge_outcome_counts": {
            "accept_success": 8,
            "reject_failure": 3,
        },
    }
    return {
        "status": "pass",
        "reason": "p6_llm_repair_evaluation_targets_met",
        "targets": P6_READINESS_TARGETS,
        "case_count": 20,
        "metrics_report": metrics,
        "matrix": [],
    }


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
