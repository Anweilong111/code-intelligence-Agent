import json
from pathlib import Path

import pytest

from code_intelligence_agent.evaluation.v1_readiness_dataset_audit import (
    REQUIRED_EVALUATION_METRICS,
    REQUIRED_ONBOARDING_SCENARIOS,
    REQUIRED_REPAIR_BLOCKER_CATEGORIES,
    build_v1_readiness_dataset_audit,
    main,
    render_v1_readiness_dataset_audit_markdown,
)


ROOT = Path(__file__).resolve().parents[1]
ONBOARDING_MANIFEST = (
    ROOT
    / "datasets"
    / "github_cases"
    / "repo_intelligence_v1_onboarding_30.example.json"
)
REPAIR_CATALOG = (
    ROOT
    / "datasets"
    / "github_cases"
    / "llm_repair_case_catalog_v1_50.example.json"
)


def test_v1_readiness_dataset_audit_passes_declared_30_50_targets():
    onboarding = json.loads(ONBOARDING_MANIFEST.read_text(encoding="utf-8"))
    repair = json.loads(REPAIR_CATALOG.read_text(encoding="utf-8"))

    audit = build_v1_readiness_dataset_audit(
        onboarding,
        repair,
        onboarding_manifest_path=str(ONBOARDING_MANIFEST),
        repair_catalog_path=str(REPAIR_CATALOG),
    )
    markdown = render_v1_readiness_dataset_audit_markdown(audit)

    assert audit["status"] == "pass"
    assert audit["summary"]["onboarding_case_count"] == 30
    assert audit["summary"]["repair_case_count"] == 50
    assert audit["summary"]["required_metric_contract_count"] == len(
        REQUIRED_EVALUATION_METRICS
    )
    assert audit["onboarding"]["unique_repo_count"] == 30
    assert audit["onboarding"]["github_repo_case_count"] == 30
    assert audit["onboarding"]["missing_required_scenarios"] == []
    assert set(audit["onboarding"]["covered_required_scenarios"]) == set(
        REQUIRED_ONBOARDING_SCENARIOS
    )
    assert audit["repair"]["missing_required_classes"] == []
    assert audit["repair"]["missing_required_blocker_categories"] == []
    assert set(audit["repair"]["blocker_category_counts"]) == set(
        REQUIRED_REPAIR_BLOCKER_CATEGORIES
    )
    assert audit["repair"]["class_counts"] == {
        "llm_blocker": 28,
        "llm_direct_success": 12,
        "llm_reflection_success": 10,
    }
    assert audit["metrics"]["missing_required_metrics"] == []
    assert audit["metrics"]["incomplete_metric_contracts"] == []
    assert audit["metrics"]["required_metrics"] == REQUIRED_EVALUATION_METRICS
    assert {
        item["metric_id"] for item in audit["metrics"]["required_metric_contracts"]
    } == set(REQUIRED_EVALUATION_METRICS)
    assert "V1 Readiness Dataset Audit" in markdown
    assert "Cases: 30" in markdown
    assert "Cases: 50" in markdown
    assert "Required Metrics" in markdown
    assert "pass_at_k" in markdown
    assert "llm_cost_usd" in markdown
    assert "Observe -> Plan -> Act -> Verify -> Reflect -> Replan" in markdown
    assert "sk-" not in json.dumps(audit)


def test_v1_readiness_dataset_audit_flags_incomplete_targets():
    audit = build_v1_readiness_dataset_audit(
        {
            "suite_name": "small",
            "suite_thresholds": {"min_run_count": 1},
            "runs": [
                {
                    "name": "one",
                    "repo": "https://github.com/pypa/sampleproject",
                    "scenario_tags": ["v1_onboarding"],
                }
            ],
        },
        {
            "name": "small_repair",
            "cases": [
                {
                    "case_id": "direct",
                    "expected_class": "llm_direct_success",
                }
            ],
        },
    )

    assert audit["status"] == "incomplete"
    assert "onboarding_case_count" in audit["missing"]
    assert "onboarding_scenario_coverage" in audit["missing"]
    assert "repair_case_count" in audit["missing"]
    assert "repair_blocker_category_kind_count" in audit["missing"]
    assert "required_metric_contract_count" in audit["missing"]
    assert audit["next_actions"]
    assert any("required v1 metric" in action for action in audit["next_actions"])


def test_v1_readiness_dataset_audit_flags_incomplete_metric_contracts():
    onboarding = {
        "suite_name": "metric_contract_probe",
        "suite_thresholds": {"min_run_count": 30},
        "runs": [
            {
                "name": f"repo_{index}",
                "repo": f"owner{index}/repo{index}",
                "scenario_tags": REQUIRED_ONBOARDING_SCENARIOS,
            }
            for index in range(30)
        ],
    }
    repair = {
        "name": "metric_contract_probe_repair",
        "evaluation_metrics": [{"metric_id": "pass_at_1"}],
        "cases": [
            {
                "case_id": f"direct_{index}",
                "expected_class": "llm_direct_success",
            }
            for index in range(12)
        ]
        + [
            {
                "case_id": f"reflection_{index}",
                "expected_class": "llm_reflection_success",
            }
            for index in range(10)
        ]
        + [
            {
                "case_id": f"{category}_{index}",
                "expected_class": "llm_blocker",
                "expected_blocker_category": category,
            }
            for category, count in {
                "llm_failed_blocker": 4,
                "environment_blocker": 4,
                "no_test_oracle_blocker": 4,
                "safety_gate_blocker": 4,
                "localization_failure": 3,
                "generation_failure": 3,
                "dependency_failure": 3,
                "timeout_blocker": 3,
            }.items()
            for index in range(count)
        ],
    }

    audit = build_v1_readiness_dataset_audit(onboarding, repair)

    assert audit["status"] == "incomplete"
    assert "required_metric_contract_count" in audit["missing"]
    assert "incomplete_metric_contract_count" in audit["missing"]
    assert audit["metrics"]["covered_required_metric_count"] == 1
    assert audit["metrics"]["incomplete_metric_contracts"] == ["pass_at_1"]


def test_v1_readiness_dataset_audit_cli_writes_artifacts(tmp_path, capsys):
    output_dir = tmp_path / "audit"

    main(
        [
            str(ONBOARDING_MANIFEST),
            str(REPAIR_CATALOG),
            str(output_dir),
            "--format",
            "json",
            "--require-pass",
        ]
    )
    stdout = capsys.readouterr().out
    payload = json.loads(
        (output_dir / "v1_readiness_dataset_audit.json").read_text(
            encoding="utf-8"
        )
    )

    assert payload["status"] == "pass"
    assert "v1_readiness_dataset_targets_met" in stdout
    assert (output_dir / "v1_readiness_dataset_audit.md").exists()

    small_onboarding = tmp_path / "small_onboarding.json"
    small_repair = tmp_path / "small_repair.json"
    small_onboarding.write_text(
        json.dumps({"runs": [{"name": "one"}]}),
        encoding="utf-8",
    )
    small_repair.write_text(
        json.dumps({"cases": []}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        main(
            [
                str(small_onboarding),
                str(small_repair),
                str(output_dir),
                "--require-pass",
            ]
        )
    assert exc.value.code == 1
