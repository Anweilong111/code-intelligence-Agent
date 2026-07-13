import json

import pytest

from code_intelligence_agent.evaluation.v1_evaluation_summary import (
    build_v1_evaluation_summary,
    main,
    render_v1_evaluation_summary_markdown,
)


def test_v1_evaluation_summary_measures_available_metrics_and_flags_gaps():
    payload = build_v1_evaluation_summary(
        readiness_audit={"status": "pass"},
        onboarding_suite={
            "summary": {
                "run_count": 30,
                "agent_passed_count": 27,
                "suite_run_elapsed_ms_average": 1250.5,
                "repository_llm_patch_cost_available_count": 1,
                "repository_llm_patch_estimated_cost_usd_total": 0.0123,
            }
        },
        repair_metrics={
            "patch_success_at": {"1": 0.4, "3": 0.7, "5": 0.8},
            "reflection_success_rate": 0.55,
            "llm_reflection_success_count": 11,
            "reflection_attempt_case_count": 20,
            "sandbox_pass_rate": 0.75,
            "patch_validation_success_count": 15,
            "patch_validation_executed_count": 20,
            "llm_token_cost": {
                "cost_case_count": 4,
                "total_estimated_cost": 0.02,
            },
        },
        repair_catalog_audit={
            "cases": [
                {
                    "case_id": "blocker_a",
                    "expected_blocker_category": "environment_blocker",
                    "blocker_category_matches": True,
                },
                {
                    "case_id": "blocker_b",
                    "expected_blocker_category": "timeout_blocker",
                    "blocker_category_matches": False,
                },
            ]
        },
    )
    metrics = {item["metric_id"]: item for item in payload["metrics"]}
    markdown = render_v1_evaluation_summary_markdown(payload)

    assert payload["status"] == "partial"
    assert payload["summary"]["measured_metric_count"] == 7
    assert payload["summary"]["proxy_metric_count"] == 1
    assert payload["summary"]["missing_metric_count"] == 1
    assert metrics["onboarding_success_rate"]["value"] == 0.9
    assert metrics["pass_at_1"]["value"] == 0.4
    assert metrics["pass_at_k"]["value"] == 0.8
    assert metrics["pass_at_k"]["k"] == 5
    assert metrics["reflection_uplift"]["evidence_status"] == "proxy"
    assert metrics["blocker_accuracy"]["value"] == 0.5
    assert metrics["average_runtime_ms"]["value"] == 1250.5
    assert metrics["llm_cost_usd"]["value"] == 0.02
    assert metrics["topk_localization_accuracy"]["evidence_status"] == (
        "missing_evidence"
    )
    assert "topk_localization_accuracy" in markdown
    assert "missing_evidence" in markdown
    assert "Observe -> Plan -> Act -> Verify -> Reflect -> Replan" in markdown


def test_v1_evaluation_summary_reaches_pass_with_direct_metric_evidence():
    payload = build_v1_evaluation_summary(
        readiness_audit={"status": "pass"},
        onboarding_suite={
            "summary": {
                "run_count": 10,
                "agent_passed_count": 10,
                "suite_run_elapsed_ms_average": 100.0,
                "repository_llm_patch_cost_available_count": 1,
                "repository_llm_patch_estimated_cost_usd_total": 0.01,
            }
        },
        repair_metrics={
            "patch_success_at": {"1": 0.5, "5": 1.0},
            "reflection_success_rate": 1.0,
            "sandbox_pass_rate": 1.0,
            "patch_validation_success_count": 2,
            "patch_validation_executed_count": 2,
        },
        repair_catalog_audit={
            "cases": [
                {
                    "case_id": "blocker_a",
                    "expected_blocker_category": "environment_blocker",
                    "blocker_category_matches": True,
                }
            ]
        },
        localization_report={"summary": {"top3": 0.9}},
    )

    assert payload["status"] == "partial"
    assert payload["summary"]["missing_metric_count"] == 0
    assert payload["summary"]["proxy_metric_count"] == 1
    assert payload["metrics"][1]["metric_id"] == "topk_localization_accuracy"
    assert payload["metrics"][1]["value"] == 0.9


def test_v1_evaluation_summary_marks_onboarding_slice_as_proxy():
    payload = build_v1_evaluation_summary(
        readiness_audit={"status": "pass"},
        onboarding_suite={
            "summary": {
                "run_count": 3,
                "agent_passed_count": 3,
                "suite_slice_applied": True,
                "suite_run_elapsed_ms_average": 1200,
            }
        },
    )
    metrics = {item["metric_id"]: item for item in payload["metrics"]}

    assert payload["status"] == "partial"
    assert metrics["onboarding_success_rate"]["evidence_status"] == "proxy"
    assert metrics["onboarding_success_rate"]["value"] == 1.0
    assert metrics["average_runtime_ms"]["evidence_status"] == "proxy"
    assert "suite slice" in metrics["average_runtime_ms"]["reason"]


def test_v1_evaluation_summary_measures_reflection_uplift_from_case_delta():
    payload = build_v1_evaluation_summary(
        readiness_audit={"status": "pass"},
        repair_metrics={
            "case_count": 30,
            "llm_direct_success_count": 5,
            "llm_direct_success_rate": 0.1667,
            "llm_reflection_success_count": 4,
            "reflection_success_case_rate": 0.1333,
            "patch_success_case_rate": 0.3,
        },
    )
    metrics = {item["metric_id"]: item for item in payload["metrics"]}

    assert metrics["reflection_uplift"]["evidence_status"] == "measured"
    assert metrics["reflection_uplift"]["value"] == 0.1333
    assert metrics["reflection_uplift"]["numerator"] == 4
    assert metrics["reflection_uplift"]["denominator"] == 30


def test_v1_evaluation_summary_uses_standalone_llm_cost_report():
    payload = build_v1_evaluation_summary(
        readiness_audit={"status": "pass"},
        llm_cost_report={
            "llm_token_cost": {
                "total_tokens": 30,
                "total_estimated_cost": 0.0005,
                "cost_case_count": 1,
            }
        },
    )
    metrics = {item["metric_id"]: item for item in payload["metrics"]}

    assert metrics["llm_cost_usd"]["evidence_status"] == "measured"
    assert metrics["llm_cost_usd"]["value"] == 0.0005
    assert metrics["llm_cost_usd"]["evidence"] == [
        "llm_cost_evidence.llm_token_cost"
    ]


def test_v1_evaluation_summary_cli_writes_artifacts(tmp_path, capsys):
    readiness = tmp_path / "readiness.json"
    suite = tmp_path / "suite.json"
    repair = tmp_path / "repair_metrics.json"
    catalog = tmp_path / "catalog_audit.json"
    localization = tmp_path / "localization.json"
    output_dir = tmp_path / "out"

    readiness.write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    suite.write_text(
        json.dumps(
            {
                "summary": {
                    "run_count": 2,
                    "agent_passed_count": 1,
                    "suite_run_elapsed_ms_average": 10,
                }
            }
        ),
        encoding="utf-8",
    )
    repair.write_text(
        json.dumps(
            {
                "patch_success_at": {"1": 1.0},
                "reflection_success_rate": 0.0,
                "sandbox_pass_rate": 1.0,
                "llm_token_cost": {
                    "cost_case_count": 1,
                    "total_estimated_cost": 0.001,
                },
            }
        ),
        encoding="utf-8",
    )
    catalog.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "expected_blocker_category": "llm_failed_blocker",
                        "blocker_category_matches": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    localization.write_text(json.dumps({"summary": {"top1": 0.5}}), encoding="utf-8")

    main(
        [
            str(output_dir),
            "--readiness-audit",
            str(readiness),
            "--onboarding-suite",
            str(suite),
            "--repair-metrics",
            str(repair),
            "--repair-catalog-audit",
            str(catalog),
            "--localization-report",
            str(localization),
            "--format",
            "markdown",
        ]
    )

    stdout = capsys.readouterr().out
    assert "V1 Evaluation Summary" in stdout
    assert (output_dir / "v1_evaluation_summary.json").exists()
    assert (output_dir / "v1_evaluation_summary.md").exists()
    with pytest.raises(SystemExit):
        main([str(output_dir), "--readiness-audit", str(readiness), "--require-pass"])
