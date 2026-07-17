from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from code_intelligence_agent import main as cli_module
from code_intelligence_agent.evaluation.v3_release_evaluation import (
    evaluate_v3_release,
    render_v3_release_markdown,
    write_v3_release_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]


def test_current_v3_release_is_offline_ready_but_live_trials_remain_pending():
    payload = evaluate_v3_release(ROOT)

    assert payload["status"] == "partial"
    assert payload["offline_release_status"] == "pass"
    assert payload["complete_release_status"] == "pending"
    assert payload["claim_eligible"] is False
    assert payload["release_gates"]["phase6_memory_and_security_passed"] is True
    assert (
        payload["release_gates"]["live_llm_hybrid_120_trials_complete"] is False
    )
    strategies = payload["metric_registry"]["repair_strategies"]
    assert strategies["rule"]["evidence_state"] == "measured"
    assert strategies["rule"]["pass_at_1"] == 0.0
    assert strategies["llm"]["evidence_state"] == "pending"
    assert strategies["llm"]["pass_at_1"] is None
    assert strategies["hybrid"]["actual_cost_usd"] is None
    assert payload["latest_regression"]["full_pytest"]["passed"] == 1410
    assert payload["latest_regression"]["latest_general_regression_source"] == (
        "docs/v3/phase5_verification.json"
    )
    assert payload["comparison_registry"]["v2_v3_repository_startup"][
        "status"
    ] == "not_comparable"
    assert len(payload["phase_evidence"]) == 7
    assert all(item["sha256"] for item in payload["phase_evidence"])


def test_complete_live_evaluation_unlocks_release_without_changing_rule_attribution():
    payload = evaluate_v3_release(ROOT, live_evaluation=_complete_live_evaluation())

    assert payload["status"] == "pass"
    assert payload["claim_eligible"] is True
    assert all(payload["release_gates"].values())
    strategies = payload["metric_registry"]["repair_strategies"]
    assert strategies["rule"]["pass_at_1"] == 0.0
    assert strategies["llm"]["pass_at_1"] == 0.25
    assert strategies["hybrid"]["pass_at_3"] == 0.45
    assert strategies["llm"]["actual_cost_usd"] == 12.5
    assert payload["release_gates"]["live_provider_access_preflight_passed"] is True
    assert payload["metric_registry"]["cost_and_latency"][
        "provider_preflight_actual_cost_usd"
    ] == 0.000001
    assert payload["pending_requirements"] == []
    assert "validated 120-trial live" in payload["claim_boundaries"][0]
    assert "completed 120-trial live-model" in payload["comparison_registry"][
        "v2_v3_patch_repair"
    ]["reason"]


def test_nominally_passing_but_incomplete_live_artifact_is_rejected():
    live = _complete_live_evaluation()
    live["completeness"]["observed_trial_count"] = 119
    live["completeness"]["missing_trial_count"] = 1

    payload = evaluate_v3_release(ROOT, live_evaluation=live)

    assert payload["status"] == "partial"
    assert payload["offline_release_status"] == "pass"
    assert payload["live_evaluation"]["status"] == "invalid"
    assert "observed_trial_count_not_120" in payload["live_evaluation"]["errors"]
    assert "missing_live_trials" in payload["live_evaluation"]["errors"]
    assert payload["claim_eligible"] is False


def test_live_artifact_with_model_or_prompt_protocol_drift_is_rejected():
    live = _complete_live_evaluation()
    metadata = live["provider_model_metadata"]
    metadata["protocol_model_id"] = "different-model"
    metadata["observed_model_ids"] = ["different-model"]
    metadata["protocol_prompt_hashes"]["patch_generation_v3"] = "0" * 64

    payload = evaluate_v3_release(ROOT, live_evaluation=live)

    assert payload["status"] == "partial"
    assert payload["live_evaluation"]["status"] == "invalid"
    errors = payload["live_evaluation"]["errors"]
    assert "protocol_model_id_differs_from_frozen_protocol" in errors
    assert "observed_model_ids_differ_from_frozen_protocol" in errors
    assert "protocol_prompt_hashes_differ_from_frozen_protocol" in errors


def test_complete_live_evaluation_requires_passing_provider_access_preflight():
    live = _complete_live_evaluation()
    live["provider_access_preflight"]["status"] = "blocked"
    live["provider_access_preflight"]["observed_model_id"] = ""

    payload = evaluate_v3_release(ROOT, live_evaluation=live)

    assert payload["status"] == "partial"
    assert payload["live_evaluation"]["status"] == "invalid"
    errors = payload["live_evaluation"]["errors"]
    assert "provider_access_preflight_not_pass" in errors
    assert "provider_access_preflight_observed_model_mismatch" in errors


def test_complete_live_evaluation_rejects_unfrozen_or_retained_preflight_data():
    live = _complete_live_evaluation()
    preflight = live["provider_access_preflight"]
    preflight["request_prompt_sha256"] = "0" * 64
    preflight["response_content"] = '{"status":"ok"}'
    preflight["latency_ms"] = -1

    payload = evaluate_v3_release(ROOT, live_evaluation=live)

    assert payload["status"] == "partial"
    errors = payload["live_evaluation"]["errors"]
    assert "provider_access_preflight_request_prompt_hash_mismatch" in errors
    assert "provider_access_preflight_response_content_present" in errors
    assert "provider_access_preflight_latency_invalid" in errors


def test_file_live_evaluation_audits_run_records_and_publishes_examples(
    tmp_path,
):
    live = _complete_live_evaluation()
    records = _publication_run_records()
    live["record_count"] = len(records)
    live["case_results"] = [
        {
            "case_id": "case-direct",
            "trials": [
                {
                    "strategy_mode": "llm",
                    "trial_index": 1,
                    "verified_repair": True,
                    "winning_run_id": "direct-run",
                }
            ],
        },
        {
            "case_id": "case-reflection",
            "trials": [
                {
                    "strategy_mode": "hybrid",
                    "trial_index": 1,
                    "verified_repair": True,
                    "winning_run_id": "reflection-run",
                }
            ],
        },
        {
            "case_id": "case-fail",
            "trials": [
                {
                    "strategy_mode": "llm",
                    "trial_index": 2,
                    "verified_repair": False,
                    "winning_run_id": "",
                }
            ],
        },
    ]
    patch = tmp_path / "selected.diff"
    validation = tmp_path / "selected.validation.json"
    patch.write_text("--- a/example.py\n+++ b/example.py\n", encoding="utf-8")
    validation.write_text('{"status":"pass"}\n', encoding="utf-8")
    records[0]["artifacts"] = {
        "patch": str(patch),
        "validation": str(validation),
    }
    evaluation_path = tmp_path / "evaluation.json"
    evaluation_path.write_text(json.dumps(live), encoding="utf-8")
    (tmp_path / "run_records.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    payload = evaluate_v3_release(ROOT, live_evaluation=evaluation_path)

    assert payload["status"] == "pass"
    evidence = payload["live_evaluation"]["run_record_evidence"]
    assert evidence["status"] == "pass"
    assert evidence["record_count"] == 240
    assert evidence["raw_records_persisted_in_release_report"] is False
    distributions = payload["live_evaluation"][
        "trial_cost_latency_distribution"
    ]
    assert distributions["llm"]["trial_count"] == 60
    assert distributions["hybrid"]["trial_count"] == 60
    examples = payload["case_examples"]
    assert examples["direct_live_repair"]["run_id"] == "direct-run"
    assert examples["direct_live_repair"]["patch_sha256"] == hashlib.sha256(
        patch.read_bytes()
    ).hexdigest()
    assert examples["reflection_live_repair"]["run_id"] == "reflection-run"
    assert examples["failed_live_repair"]["case_id"] == "case-fail"
    assert examples["provider_blocker"]["failure_category"] == "timeout"
    markdown = render_v3_release_markdown(payload)
    assert "LLM pass@1" in markdown
    assert "Audited Live Examples" in markdown


def test_existing_but_incomplete_run_record_evidence_invalidates_live_release(
    tmp_path,
):
    live = _complete_live_evaluation()
    evaluation_path = tmp_path / "evaluation.json"
    evaluation_path.write_text(json.dumps(live), encoding="utf-8")
    (tmp_path / "run_records.jsonl").write_text(
        json.dumps(_publication_run_records()[0]) + "\n",
        encoding="utf-8",
    )

    payload = evaluate_v3_release(ROOT, live_evaluation=evaluation_path)

    assert payload["status"] == "partial"
    assert payload["live_evaluation"]["run_record_evidence"]["status"] == (
        "invalid"
    )
    assert any(
        error.startswith("run_record_evidence:record_count_mismatch")
        for error in payload["live_evaluation"]["errors"]
    )


def test_missing_offline_phase_artifact_fails_offline_release(tmp_path):
    docs = tmp_path / "docs" / "v3"
    docs.mkdir(parents=True)
    for _, relative in [
        ("phase0", "phase0_verification.json"),
        ("phase1", "phase1_verification.json"),
    ]:
        (docs / relative).write_text(
            json.dumps({"status": "pass"}),
            encoding="utf-8",
        )

    payload = evaluate_v3_release(tmp_path)

    assert payload["status"] == "fail"
    assert payload["offline_release_status"] == "fail"
    assert payload["phase_evidence_errors"]
    assert payload["claim_eligible"] is False


def test_release_writer_and_top_level_cli_preserve_partial_status(tmp_path, capsys):
    payload = evaluate_v3_release(ROOT)
    paths = write_v3_release_artifacts(payload, tmp_path / "direct")

    cli_module.main(
        [
            "v3-release-eval",
            str(tmp_path / "cli"),
            "--root",
            str(ROOT),
            "--format",
            "json",
            "--require-offline-pass",
        ]
    )
    printed = json.loads(capsys.readouterr().out)

    assert printed["status"] == "partial"
    assert all(Path(path).is_file() for path in paths.values())
    assert (tmp_path / "cli" / "phase7_unified_evaluation.json").is_file()
    assert b"\r\n" not in Path(paths["release_json"]).read_bytes()
    assert b"\r\n" not in Path(paths["release_markdown"]).read_bytes()
    with pytest.raises(SystemExit) as exc_info:
        cli_module.main(
            [
                "v3-release-eval",
                str(tmp_path / "complete"),
                "--root",
                str(ROOT),
                "--require-complete",
            ]
        )
    assert exc_info.value.code == 1


def test_committed_phase7_live_report_is_self_consistent():
    committed = json.loads(
        (ROOT / "docs" / "v3" / "phase7_unified_evaluation.json").read_text(
            encoding="utf-8"
        )
    )
    committed_markdown = (
        ROOT / "docs" / "v3" / "phase7_unified_evaluation.md"
    ).read_text(encoding="utf-8")

    assert committed_markdown == render_v3_release_markdown(committed)
    assert committed["status"] == "pass"
    assert committed["complete_release_status"] == "pass"
    assert committed["claim_eligible"] is True
    assert committed["live_evaluation"]["observed_trial_count"] == 120
    assert committed["live_evaluation"]["missing_trial_count"] == 0
    assert committed["live_evaluation"]["run_record_evidence"]["status"] == "pass"
    assert committed["live_evaluation"]["run_record_evidence"]["record_count"] == 423
    assert committed["live_evaluation"]["provider_model_metadata"][
        "observed_model_ids"
    ] == ["deepseek-v4-pro"]
    strategies = committed["metric_registry"]["repair_strategies"]
    assert strategies["llm"]["pass_at_1"] == 0.4
    assert strategies["llm"]["pass_at_3"] == 0.5
    assert strategies["hybrid"]["pass_at_1"] == 0.3
    assert strategies["hybrid"]["pass_at_3"] == 0.45
    assert strategies["hybrid"]["winning_generator_families"] == {"llm": 22}
    assert strategies["hybrid"]["provider_blocker_record_count"] == 1
    distributions = committed["live_evaluation"][
        "trial_cost_latency_distribution"
    ]
    assert distributions["llm"]["trial_count"] == 60
    assert distributions["hybrid"]["trial_count"] == 60
    assert committed["pending_requirements"] == []
    assert committed["case_examples"]["direct_live_repair"]["status"] == "measured"
    assert committed["case_examples"]["reflection_live_repair"]["status"] == (
        "measured"
    )
    assert len(committed["case_examples"]["direct_live_repair"]["patch_sha256"]) == 64
    assert committed["case_examples"]["provider_blocker"]["failure_category"] == (
        "timeout"
    )
    serialized = json.dumps(committed, ensure_ascii=False)
    assert "sk-" not in serialized
    assert "D:\\" not in serialized
    assert b"\r\n" not in (
        ROOT / "docs" / "v3" / "phase7_unified_evaluation.json"
    ).read_bytes()
    assert b"\r\n" not in (
        ROOT / "docs" / "v3" / "phase7_unified_evaluation.md"
    ).read_bytes()


def test_committed_phase7_final_verification_hashes_release_artifacts():
    verification = json.loads(
        (ROOT / "docs" / "v3" / "phase7_final_verification.json").read_text(
            encoding="utf-8"
        )
    )

    assert verification["status"] == "pass"
    assert verification["offline_release_status"] == "pass"
    assert verification["complete_release_status"] == "pass"
    assert verification["claim_eligible"] is True
    assert verification["live_evaluation"]["observed_trial_count"] == 120
    assert verification["live_evaluation"]["missing_trial_count"] == 0
    assert verification["tests"]["full_pytest"]["status"] == "pass"
    for relative_path, expected_hash in verification["artifacts"].items():
        path = ROOT / relative_path
        assert path.is_file(), relative_path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash
    for relative_path in verification["artifact_audit"]["lf_normalized_files"]:
        assert b"\r\n" not in (ROOT / relative_path).read_bytes()


def _complete_live_evaluation() -> dict:
    protocol = json.loads(
        (
            ROOT
            / "datasets"
            / "v3_real_bugs"
            / "experiment_protocol.json"
        ).read_text(encoding="utf-8")
    )
    model = protocol["model"]
    prompt_hashes = {
        item["id"]: item["sha256"] for item in protocol["prompts"]
    }
    preflight_prompt = next(
        item
        for item in protocol["prompts"]
        if item["id"] == model["access_preflight"]["prompt_id"]
    )
    return {
        "schema_version": "3.0",
        "status": "pass",
        "live_model": True,
        "case_count": 20,
        "record_count": 240,
        "strategies": ["llm", "hybrid"],
        "record_audit": {"status": "pass", "errors": []},
        "provider_model_metadata": {
            "status": "pass",
            "model_record_count": 120,
            "missing_core_metadata_count": 0,
            "protocol_provider": model["provider"],
            "protocol_model_id": model["model_id"],
            "protocol_prompt_hashes": prompt_hashes,
            "observed_providers": [model["provider"]],
            "observed_model_ids": [model["model_id"]],
            "observed_prompt_ids": ["patch_generation_v3", "reflection_v3"],
        },
        "provider_access_preflight": {
            "schema_version": "v3_provider_access_preflight_v1",
            "status": "pass",
            "reason": "provider_access_verified",
            "performed": True,
            "counted_as_repair_trial": False,
            "provider": model["provider"],
            "protocol_model_id": model["model_id"],
            "observed_model_id": model["model_id"],
            "prompt_id": preflight_prompt["id"],
            "prompt_template_sha256": preflight_prompt["sha256"],
            "request_prompt_sha256": model["access_preflight"][
                "request_prompt_sha256"
            ],
            "cost_attribution": "provider_preflight_overhead",
            "response_content_retained": False,
            "response_content_used_for_repair": False,
            "actual_cost_usd": 0.000001,
            "latency_ms": 100,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 2,
                "total_tokens": 12,
            },
        },
        "completeness": {
            "status": "pass",
            "expected_trial_count": 120,
            "observed_trial_count": 120,
            "missing_trial_count": 0,
        },
        "metrics": {
            "llm": _strategy_metrics(pass1=0.25, pass3=0.4, cost=12.5),
            "hybrid": _strategy_metrics(pass1=0.3, pass3=0.45, cost=10.0),
        },
        "case_results": [],
    }


def _strategy_metrics(*, pass1: float, pass3: float, cost: float) -> dict:
    return {
        "case_denominator": 20,
        "expected_trials_per_case": 3,
        "observed_trial_count": 60,
        "pass_at_1": pass1,
        "pass_at_1_count": int(pass1 * 20),
        "pass_at_3": pass3,
        "pass_at_3_count": int(pass3 * 20),
        "verified_repair_rate": pass3,
        "reflection_recovery_rate": 0.1,
        "ast_valid_rate": 0.9,
        "safety_pass_rate": 0.85,
        "targeted_test_pass_rate": 0.5,
        "full_regression_pass_rate": 0.45,
        "semantic_claim_eligible_rate": 0.4,
        "actual_cost_usd": cost,
        "latency_ms": 100000.0,
        "failure_categories": {"targeted_test:test_assertion_failure": 10},
        "winning_generator_families": {"llm": 5},
        "token_usage": {"input_tokens": 1000, "output_tokens": 500},
    }


def _publication_run_records() -> list[dict]:
    records = []
    for mode in ("llm", "hybrid"):
        for trial_number in range(60):
            for candidate_number in range(2):
                records.append(
                    {
                        "run_id": f"{mode}-run-{trial_number}-{candidate_number}",
                        "case": {"case_id": f"case-{mode}-{trial_number}"},
                        "strategy": {
                            "mode": mode,
                            "trial_index": trial_number % 3 + 1,
                            "trial_id": f"{mode}-trial-{trial_number}",
                        },
                        "candidate": {
                            "candidate_id": (
                                f"{mode}-candidate-{trial_number}-{candidate_number}"
                            ),
                            "generator_family": "llm",
                            "generator_id": "llm_direct",
                            "reflection_round": 0,
                        },
                        "usage": {"total_tokens": 100},
                        "cost": {"actual_cost_usd": 0.01},
                        "timing": {"latency_ms": 100.0},
                        "validation": {
                            "ast_valid": True,
                            "safety_gate": "pass",
                            "targeted_tests": "fail",
                            "full_regression": "not_run",
                            "semantic_validation": "not_run",
                        },
                        "outcome": {
                            "status": "failed",
                            "direct_success": False,
                            "reflection_recovered": False,
                        },
                        "failure": {
                            "layer": "targeted_test",
                            "category": "test_assertion_failure",
                        },
                        "artifacts": {},
                    }
                )
    direct = records[0]
    direct["run_id"] = "direct-run"
    direct["case"]["case_id"] = "case-direct"
    direct["outcome"] = {
        "status": "verified_repair",
        "direct_success": True,
        "reflection_recovered": False,
    }
    direct["failure"] = {"layer": "none", "category": "none"}
    direct["validation"] = {
        "ast_valid": True,
        "safety_gate": "pass",
        "targeted_tests": "pass",
        "full_regression": "pass",
        "semantic_validation": "pass",
    }
    failed = records[2]
    failed["case"]["case_id"] = "case-fail"
    reflection = records[120]
    reflection["run_id"] = "reflection-run"
    reflection["case"]["case_id"] = "case-reflection"
    reflection["candidate"]["generator_id"] = "llm_reflection"
    reflection["candidate"]["reflection_round"] = 1
    reflection["outcome"] = {
        "status": "verified_repair",
        "direct_success": False,
        "reflection_recovered": True,
    }
    reflection["failure"] = {"layer": "none", "category": "none"}
    blocker = records[122]
    blocker["case"]["case_id"] = "case-provider"
    blocker["outcome"]["status"] = "provider_blocker"
    blocker["failure"] = {"layer": "provider", "category": "timeout"}
    return records
