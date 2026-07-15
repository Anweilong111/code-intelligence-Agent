from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

from code_intelligence_agent.evaluation.v3_experiment_protocol import (
    canonical_json_sha256,
    compute_run_record_cost,
    load_experiment_protocol,
    sha256_file,
    validate_experiment_protocol,
    validate_run_record,
    validate_run_records,
    write_protocol_audit_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "datasets" / "v3_real_bugs" / "experiment_protocol.json"


def test_repository_v3_protocol_is_frozen_and_valid():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    audit = validate_experiment_protocol(protocol, root=ROOT)

    assert audit["status"] == "pass", audit["errors"]
    assert audit["warning_count"] == 0
    assert len(audit["prompt_hashes"]) == 6
    assert protocol["model"]["model_id"] == "deepseek-v4-pro"
    assert protocol["model"]["access_preflight"] == {
        "enabled": True,
        "prompt_id": "provider_access_preflight_v3",
        "request_prompt_sha256": (
            "4ee9209b03518fb9edab4aba6eedec48ab057eb4f340df0b4f08efa817b4acdd"
        ),
        "max_output_tokens": 16,
        "thinking": "disabled",
        "reasoning_effort": None,
        "runs_once_per_evaluation": True,
        "counts_as_repair_trial": False,
        "success_condition": (
            "http_200_valid_chat_completion_envelope_and_exact_response_model"
        ),
    }
    assert protocol["randomness"]["llm_trials"] == 3
    assert protocol["randomness"]["hybrid_trials"] == 3


def test_protocol_detects_prompt_drift(tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("original", encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pytest\n", encoding="utf-8")
    protocol = _minimal_protocol(tmp_path, prompt, requirements)
    prompt.write_text("changed", encoding="utf-8")

    audit = validate_experiment_protocol(protocol, root=tmp_path)

    assert audit["status"] == "fail"
    assert "prompt:patch_generation_v3_sha256_mismatch" in audit["errors"]


def test_protocol_rejects_preflight_that_can_count_as_repair_trial(tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("original", encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pytest\n", encoding="utf-8")
    protocol = _minimal_protocol(tmp_path, prompt, requirements)
    protocol["model"]["access_preflight"]["counts_as_repair_trial"] = True
    protocol.pop("protocol_sha256")

    audit = validate_experiment_protocol(protocol, root=tmp_path)

    assert audit["status"] == "fail"
    assert "model.access_preflight.must_not_count_as_repair_trial" in audit["errors"]


def test_protocol_rejects_unfrozen_preflight_request_prompt(tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("original", encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pytest\n", encoding="utf-8")
    protocol = _minimal_protocol(tmp_path, prompt, requirements)
    protocol["model"]["access_preflight"]["request_prompt_sha256"] = "invalid"
    protocol.pop("protocol_sha256")

    audit = validate_experiment_protocol(protocol, root=tmp_path)

    assert audit["status"] == "fail"
    assert (
        "model.access_preflight.request_prompt_sha256_is_invalid"
        in audit["errors"]
    )


def test_protocol_audit_writer_uses_lf_on_windows(tmp_path):
    paths = write_protocol_audit_artifacts(
        {"status": "pass", "notes": ["first", "second"]},
        tmp_path / "protocol_audit",
    )

    assert b"\r\n" not in Path(paths["json"]).read_bytes()
    assert b"\r\n" not in Path(paths["markdown"]).read_bytes()


def test_verified_llm_run_record_requires_every_authoritative_gate():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    record = _valid_record(protocol, mode="llm", family="llm")
    assert validate_run_record(record, protocol=protocol)["status"] == "pass"

    record["validation"]["full_regression"] = "fail"
    audit = validate_run_record(record, protocol=protocol)

    assert audit["status"] == "fail"
    assert "verified_repair_requires_full_regression_pass" in audit["errors"]

    record = _valid_record(protocol, mode="llm", family="llm")
    record["validation"]["semantic_validation"] = "not_applicable"
    record["validation"]["semantic_justification"] = "No complete oracle."
    record["validation"].pop("semantic_validation_details", None)
    audit = validate_run_record(record, protocol=protocol)
    assert "verified_repair_requires_semantic_validation_pass" in audit["errors"]


def test_semantic_pass_requires_auditable_claim_eligible_details():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    record = _valid_record(protocol, mode="llm", family="llm")
    record["validation"]["semantic_validation"] = "pass"
    record["validation"].pop("semantic_validation_details", None)

    missing = validate_run_record(record, protocol=protocol)

    assert "semantic_pass_requires_validation_details" in missing["errors"]
    record["validation"]["semantic_validation_details"] = {
        "status": "pass",
        "claim_eligible": False,
    }
    ineligible = validate_run_record(record, protocol=protocol)
    assert "semantic_pass_requires_claim_eligible_details" in ineligible["errors"]
    record["validation"]["semantic_validation_details"]["claim_eligible"] = True
    assert validate_run_record(record, protocol=protocol)["status"] == "pass"


def test_rule_run_has_one_trial_zero_tokens_and_zero_cost():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    record = _valid_record(protocol, mode="rule", family="rule")

    assert validate_run_record(record, protocol=protocol)["status"] == "pass"

    record["strategy"]["trial_index"] = 2
    record["usage"]["input_tokens"] = 1
    record["usage"]["cache_miss_input_tokens"] = 1
    record["usage"]["total_tokens"] = 1
    audit = validate_run_record(record, protocol=protocol)

    assert "rule_strategy_trial_index_must_equal_one" in audit["errors"]
    assert "rule_candidate_tokens_must_equal_zero" in audit["errors"]


def test_hybrid_record_preserves_winning_generator_attribution():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    rule_candidate = _valid_record(protocol, mode="hybrid", family="rule")
    llm_candidate = _valid_record(protocol, mode="hybrid", family="llm")
    llm_candidate["strategy"] = copy.deepcopy(rule_candidate["strategy"])
    llm_candidate["run_id"] = str(uuid.uuid4())
    llm_candidate["candidate"]["candidate_index"] = 2
    llm_candidate["candidate"]["candidate_id"] = "hybrid-t1-llm-c2"

    audit = validate_run_records(
        [rule_candidate, llm_candidate],
        protocol=protocol,
        require_complete=False,
    )

    assert audit["status"] == "pass", audit["errors"]
    assert rule_candidate["candidate"]["generator_family"] == "rule"
    assert llm_candidate["candidate"]["generator_family"] == "llm"


def test_llm_only_mode_rejects_rule_generator_and_prompt_drift():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    record = _valid_record(protocol, mode="llm", family="rule")

    audit = validate_run_record(record, protocol=protocol)

    assert "llm_strategy_requires_llm_generator" in audit["errors"]

    llm_record = _valid_record(protocol, mode="llm", family="llm")
    llm_record["model"]["prompt_sha256"] = "f" * 64
    audit = validate_run_record(llm_record, protocol=protocol)

    assert "model.prompt_sha256_differs_from_protocol" in audit["errors"]


def test_run_record_rejects_raw_provider_body_and_secret_value():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    record = _valid_record(protocol, mode="llm", family="llm")
    secret_like_value = "s" + "k-" + "abcdefghijklmnop1234"
    record["model"]["raw_response"] = {"token": secret_like_value}

    audit = validate_run_record(record, protocol=protocol)

    assert audit["status"] == "fail"
    assert any(item.startswith("sensitive_run_record_field:") for item in audit["errors"])


def test_cost_uses_cache_hit_cache_miss_and_output_rates():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    record = _valid_record(protocol, mode="llm", family="llm")
    record["usage"].update(
        {
            "input_tokens": 1_000_000,
            "cache_hit_input_tokens": 250_000,
            "cache_miss_input_tokens": 750_000,
            "output_tokens": 100_000,
            "total_tokens": 1_100_000,
        }
    )

    assert compute_run_record_cost(record, protocol) == 0.41415625


def test_complete_trial_audit_requires_rule_once_and_model_modes_three_times():
    protocol = load_experiment_protocol(PROTOCOL_PATH)
    records = [_valid_record(protocol, mode="rule", family="rule")]
    for mode in ("llm", "hybrid"):
        for trial_index in range(1, 4):
            record = _valid_record(protocol, mode=mode, family="llm")
            record["strategy"]["trial_index"] = trial_index
            record["strategy"]["trial_id"] = str(uuid.uuid4())
            record["run_id"] = str(uuid.uuid4())
            record["candidate"]["candidate_id"] = f"{mode}-t{trial_index}-c1"
            records.append(record)

    audit = validate_run_records(records, protocol=protocol, require_complete=True)

    assert audit["status"] == "pass", audit["errors"]
    assert audit["completeness"]["case-001"]["hybrid"]["complete"] is True


def _minimal_protocol(root: Path, prompt: Path, requirements: Path) -> dict:
    del root
    protocol = {
        "schema_version": "3.0",
        "baseline": {"tag": "v2-baseline", "commit_sha": "a" * 40},
        "runtime": {
            "benchmark_default_python": "3.11",
            "requirements_path": requirements.name,
            "requirements_sha256": sha256_file(requirements),
        },
        "model": {
            "provider": "deepseek",
            "model_id": "deepseek-v4-pro",
            "api_base": "https://api.deepseek.com",
            "endpoint": "/chat/completions",
            "temperature": 0,
            "api_key_source": "environment_only",
            "api_key_env_names": ["CIA_LLM_API_KEY"],
            "access_preflight": {
                "enabled": True,
                "prompt_id": "patch_generation_v3",
                "request_prompt_sha256": "a" * 64,
                "max_output_tokens": 16,
                "thinking": "disabled",
                "reasoning_effort": None,
                "runs_once_per_evaluation": True,
                "counts_as_repair_trial": False,
                "success_condition": (
                    "http_200_valid_chat_completion_envelope_and_exact_response_model"
                ),
            },
        },
        "randomness": {
            "rule_trials": 1,
            "llm_trials": 3,
            "hybrid_trials": 3,
            "seed": None,
            "provider_retry_creates_new_trial": False,
            "share_candidate_history_between_trials": False,
        },
        "pricing": {
            "snapshot_id": "test",
            "currency": "USD",
            "cache_hit_input_usd_per_million_tokens": 0.1,
            "cache_miss_input_usd_per_million_tokens": 0.2,
            "output_usd_per_million_tokens": 0.3,
            "source_urls": ["https://example.test/pricing"],
        },
        "prompts": [
            {"id": "patch_generation_v3", "path": prompt.name, "sha256": sha256_file(prompt)}
        ],
        "required_metrics": [
            "pass_at_1",
            "pass_at_3",
            "ast_valid_rate",
            "safety_pass_rate",
            "targeted_test_pass_rate",
            "full_regression_pass_rate",
            "verified_repair_rate",
            "reflection_recovery_rate",
            "token_usage",
            "actual_cost_usd",
            "latency_ms",
        ],
        "safety": {
            "gold_patch_in_model_context": False,
            "fix_commit_content_in_model_context": False,
            "free_form_shell": False,
            "raw_provider_response_in_run_record": False,
        },
    }
    protocol["protocol_sha256"] = canonical_json_sha256(protocol)
    return protocol


def _valid_record(protocol: dict, *, mode: str, family: str) -> dict:
    prompt = protocol["prompts"][0]
    is_llm = family == "llm"
    usage = {
        "source": "provider_usage" if is_llm else "not_applicable",
        "input_tokens": 100 if is_llm else 0,
        "cache_hit_input_tokens": 0,
        "cache_miss_input_tokens": 100 if is_llm else 0,
        "output_tokens": 20 if is_llm else 0,
        "total_tokens": 120 if is_llm else 0,
    }
    record = {
        "schema_version": "3.0",
        "run_id": str(uuid.uuid4()),
        "case": {
            "case_id": "case-001",
            "repository": "owner/project",
            "bug_commit_sha": "b" * 40,
            "benchmark_split": "development",
        },
        "strategy": {
            "mode": mode,
            "trial_index": 1,
            "trial_id": str(uuid.uuid4()),
            "independent_trial": True,
        },
        "candidate": {
            "candidate_index": 1,
            "candidate_id": f"{mode}-t1-{family}-c1",
            "generator_family": family,
            "generator_id": "llm_direct" if is_llm else "rule_based",
            "parent_candidate_id": "",
            "reflection_round": 0,
        },
        "model": {
            "provider": protocol["model"]["provider"] if is_llm else "",
            "model_id": protocol["model"]["model_id"] if is_llm else "",
            "prompt_id": prompt["id"] if is_llm else "",
            "prompt_sha256": prompt["sha256"] if is_llm else "",
            "temperature": 0 if is_llm else None,
            "seed": None,
        },
        "usage": usage,
        "cost": {
            "currency": "USD",
            "pricing_snapshot_id": protocol["pricing"]["snapshot_id"] if is_llm else "",
            "actual_cost_usd": 0.0,
        },
        "timing": {
            "latency_ms": 50.0,
            "provider_retry_count": 0,
            "provider_retry_reasons": [],
        },
        "validation": {
            "ast_valid": True,
            "safety_gate": "pass",
            "targeted_tests": "pass",
            "full_regression": "pass",
            "semantic_validation": "pass",
            "semantic_justification": "All required semantic gates passed.",
            "semantic_validation_details": {
                "status": "pass",
                "claim_eligible": True,
                "checks": [],
            },
        },
        "outcome": {
            "status": "verified_repair",
            "direct_success": True,
            "reflection_recovered": False,
        },
        "failure": {"layer": "none", "category": "none", "reason": ""},
        "model_context": {
            "artifact_ref": "contexts/case-001.json",
            "contains_gold_patch": False,
            "contains_fix_commit": False,
            "contains_test_answer": False,
        },
        "artifacts": {
            "patch": "patches/case-001.diff",
            "targeted_test": "tests/case-001-targeted.json",
            "full_regression": "tests/case-001-regression.json",
        },
        "timestamps": {
            "started_at": "2026-07-14T00:00:00+00:00",
            "completed_at": "2026-07-14T00:00:01+00:00",
        },
    }
    if is_llm:
        record["cost"]["actual_cost_usd"] = compute_run_record_cost(record, protocol)
    return record
