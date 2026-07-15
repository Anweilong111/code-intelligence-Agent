from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any


PHASE_EVIDENCE = (
    ("phase0", "docs/v3/phase0_verification.json"),
    ("phase1", "docs/v3/phase1_verification.json"),
    ("phase2", "docs/v3/phase2_verification.json"),
    ("phase3", "docs/v3/phase3_offline_verification.json"),
    ("phase4", "docs/v3/phase4_verification.json"),
    ("phase5", "docs/v3/phase5_verification.json"),
    ("phase6", "docs/v3/phase6_verification.json"),
)
LIVE_STRATEGIES = ("llm", "hybrid")
REQUIRED_LIVE_CASES = 20
REQUIRED_TRIALS_PER_STRATEGY = 60
REQUIRED_TOTAL_LIVE_TRIALS = 120
REQUIRED_LIVE_METRICS = (
    "pass_at_1",
    "pass_at_3",
    "verified_repair_rate",
    "reflection_recovery_rate",
    "ast_valid_rate",
    "safety_pass_rate",
    "targeted_test_pass_rate",
    "full_regression_pass_rate",
    "semantic_claim_eligible_rate",
    "actual_cost_usd",
    "latency_ms",
    "failure_categories",
    "winning_generator_families",
)


def evaluate_v3_release(
    project_root: str | Path,
    *,
    live_evaluation: str | Path | dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    phase_records, phase_payloads, phase_errors = _load_phase_evidence(root)
    frozen_protocol, protocol_errors = _load_frozen_protocol(
        root,
        _dict(phase_payloads.get("phase0")),
    )
    phase_errors.extend(protocol_errors)
    offline_gates = _offline_gates(phase_payloads, phase_errors)
    if protocol_errors:
        offline_gates["phase0_protocol_frozen"] = False
        for record in phase_records:
            if record.get("phase") == "phase0":
                record["gate_status"] = "fail"
                record["error"] = ";".join(protocol_errors)
                break
    live = _load_live_evaluation(
        root,
        live_evaluation,
        frozen_protocol=frozen_protocol,
    )
    metric_registry = _build_metric_registry(phase_payloads, live)
    comparisons = _build_comparison_registry(phase_payloads)
    release_gates = {
        **offline_gates,
        "live_llm_hybrid_120_trials_complete": live["status"] == "pass",
        "rule_llm_hybrid_attribution_available": (
            offline_gates["phase3_offline_foundation_passed"]
            and live["status"] == "pass"
        ),
        "live_cost_latency_and_failure_taxonomy_available": (
            live["status"] == "pass"
        ),
        "full_v3_claim_eligible": False,
    }
    release_gates["full_v3_claim_eligible"] = all(
        value
        for key, value in release_gates.items()
        if key != "full_v3_claim_eligible"
    )
    offline_pass = all(offline_gates.values())
    complete = bool(release_gates["full_v3_claim_eligible"])
    status = "pass" if complete else "partial" if offline_pass else "fail"
    reason = (
        "all_v3_release_gates_passed"
        if complete
        else (
            "offline_release_evidence_passed_live_trials_pending"
            if offline_pass and live["status"] == "pending"
            else (
                "offline_release_evidence_passed_live_artifact_invalid"
                if offline_pass
                else "one_or_more_offline_release_gates_failed"
            )
        )
    )
    phase6 = _dict(phase_payloads.get("phase6"))
    pending = [] if complete else _pending_live_requirements(live)
    return {
        "schema_version": "v3_phase7_unified_evaluation_v1",
        "status": status,
        "reason": reason,
        "offline_release_status": "pass" if offline_pass else "fail",
        "complete_release_status": "pass" if complete else "pending",
        "claim_eligible": complete,
        "phase_evidence": phase_records,
        "phase_evidence_errors": phase_errors,
        "live_evaluation": live,
        "metric_registry": metric_registry,
        "comparison_registry": comparisons,
        "release_gates": release_gates,
        "pending_requirements": pending,
        "latest_regression": _dict(phase6.get("tests")),
        "denominator_policy": {
            "failed_trials_retained": True,
            "provider_and_environment_blockers_separated": True,
            "missing_trials_never_imputed": True,
            "calibration_not_counted_as_agent_repair": True,
            "historical_metrics_compared_only_when_protocols_match": True,
        },
        "case_examples": _case_examples(phase_payloads, live),
        "claim_boundaries": [
            "Offline Phase 0-6 evidence does not substitute for the pending 120 live LLM and Hybrid trials.",
            "Rule metrics, human-fix semantic calibration, and deterministic memory/security fixtures are not live-model repair rates.",
            "V2/V3 numbers are not presented as improvements when their protocols differ.",
            "A complete V3 release requires all failed trials, provider blockers, environment blockers, token usage, cost, latency, and generator attribution in the denominator.",
            "Process-level repository defenses do not provide container-grade isolation for native child processes on Windows.",
        ],
    }


def render_v3_release_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# V3 Unified Evaluation and Release Readiness",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Reason: `{payload.get('reason')}`",
        f"- Offline release: `{payload.get('offline_release_status')}`",
        f"- Complete release: `{payload.get('complete_release_status')}`",
        f"- Claim eligible: {str(bool(payload.get('claim_eligible'))).lower()}",
        "",
        "## Release Gates",
        "",
        "| Gate | Result |",
        "| --- | --- |",
    ]
    for name, passed in _dict(payload.get("release_gates")).items():
        lines.append(f"| `{name}` | {'pass' if passed else 'pending/fail'} |")
    lines.extend(
        [
            "",
            "## Phase Evidence",
            "",
            "| Phase | Source status | Gate | Artifact | SHA-256 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for value in _list(payload.get("phase_evidence")):
        item = _dict(value)
        lines.append(
            f"| `{item.get('phase')}` | `{item.get('source_status')}` | "
            f"`{item.get('gate_status')}` | `{item.get('artifact')}` | "
            f"`{str(item.get('sha256') or '')[:12]}` |"
        )
    lines.extend(
        [
            "",
            "## Metric Registry",
            "",
            "| Dimension | Evidence state | Headline | Source |",
            "| --- | --- | --- | --- |",
        ]
    )
    for name, value in _dict(payload.get("metric_registry")).items():
        if name == "repair_strategies":
            continue
        item = _dict(value)
        lines.append(
            f"| `{name}` | `{item.get('evidence_state')}` | "
            f"{_md(item.get('headline') or 'none')} | "
            f"`{item.get('source') or 'none'}` |"
        )
    lines.extend(
        [
            "",
            "## Repair Strategies",
            "",
            "| Strategy | State | pass@1 | pass@3 | Verified | Reflection | Cost USD | Latency ms |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    strategy_metrics = _dict(
        _dict(payload.get("metric_registry")).get("repair_strategies")
    )
    for strategy in ("rule", "llm", "hybrid"):
        item = _dict(strategy_metrics.get(strategy))
        lines.append(
            f"| `{strategy}` | `{item.get('evidence_state') or 'pending'}` | "
            f"{_metric(item.get('pass_at_1'))} | {_metric(item.get('pass_at_3'))} | "
            f"{_metric(item.get('verified_repair_rate'))} | "
            f"{_strategy_metric(strategy, 'reflection_recovery_rate', item)} | "
            f"{_metric(item.get('actual_cost_usd'))} | "
            f"{_strategy_metric(strategy, 'latency_ms', item)} |"
        )
    environment = _dict(
        _dict(payload.get("metric_registry")).get("repository_environment")
    )
    localization = _dict(
        _dict(payload.get("metric_registry")).get("fault_localization")
    )
    rule = _dict(strategy_metrics.get("rule"))
    lines.extend(
        [
            "",
            "## Proportion Uncertainty",
            "",
            "| Metric | Observed | Wilson 95% interval |",
            "| --- | ---: | --- |",
            _interval_row(
                "Repository startup",
                environment.get("wilson_95pct"),
            ),
            _interval_row("Localization Top-1", localization.get("top1_wilson_95pct")),
            _interval_row("Localization Top-3", localization.get("top3_wilson_95pct")),
            _interval_row("Localization Top-5", localization.get("top5_wilson_95pct")),
            _interval_row("Rule pass@1", rule.get("pass_at_1_wilson_95pct")),
        ]
    )
    lines.extend(
        [
            "",
            "## Protocol Comparisons",
            "",
            "| Comparison | Status | Reason |",
            "| --- | --- | --- |",
        ]
    )
    for name, value in _dict(payload.get("comparison_registry")).items():
        item = _dict(value)
        lines.append(
            f"| `{name}` | `{item.get('status')}` | {_md(item.get('reason') or '')} |"
        )
    lines.extend(["", "## Pending Requirements", ""])
    for value in _list(payload.get("pending_requirements")):
        item = _dict(value)
        lines.append(
            f"- `{item.get('id')}`: {_md(item.get('requirement') or '')}"
        )
    if not _list(payload.get("pending_requirements")):
        lines.append("- none")
    lines.extend(["", "## Claim Boundaries", ""])
    for boundary in _list(payload.get("claim_boundaries")):
        lines.append(f"- {_md(boundary)}")
    return "\n".join(lines) + "\n"


def write_v3_release_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "phase7_unified_evaluation.json"
    markdown_path = root / "phase7_unified_evaluation.md"
    _write_text_lf(
        json_path,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    _write_text_lf(markdown_path, render_v3_release_markdown(payload))
    return {
        "release_json": str(json_path),
        "release_markdown": str(markdown_path),
    }


def _load_phase_evidence(
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    records = []
    payloads: dict[str, dict[str, Any]] = {}
    errors = []
    for phase, relative in PHASE_EVIDENCE:
        path = root / relative
        payload: dict[str, Any] = {}
        error = ""
        if not path.is_file():
            error = "artifact_missing"
        else:
            try:
                payload = _dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                error = "artifact_invalid_json"
        if error:
            errors.append(f"{phase}:{error}:{relative}")
        payloads[phase] = payload
        source_status = str(payload.get("status") or "missing")
        gate_passed = _phase_gate_passed(phase, payload)
        records.append(
            {
                "phase": phase,
                "artifact": relative,
                "sha256": _sha256_file(path) if path.is_file() else "",
                "source_status": source_status,
                "gate_status": "pass" if gate_passed else "fail",
                "phase_name": str(payload.get("phase") or ""),
                "error": error,
            }
        )
    return records, payloads, errors


def _offline_gates(
    payloads: dict[str, dict[str, Any]],
    errors: list[str],
) -> dict[str, bool]:
    return {
        "all_phase_evidence_parseable": not errors,
        "phase0_protocol_frozen": _phase_gate_passed(
            "phase0", _dict(payloads.get("phase0"))
        ),
        "phase1_real_bug_benchmark_passed": _phase_gate_passed(
            "phase1", _dict(payloads.get("phase1"))
        ),
        "phase2_repository_startup_passed": _phase_gate_passed(
            "phase2", _dict(payloads.get("phase2"))
        ),
        "phase3_offline_foundation_passed": _phase_gate_passed(
            "phase3", _dict(payloads.get("phase3"))
        ),
        "phase4_localization_passed": _phase_gate_passed(
            "phase4", _dict(payloads.get("phase4"))
        ),
        "phase5_semantic_validation_passed": _phase_gate_passed(
            "phase5", _dict(payloads.get("phase5"))
        ),
        "phase6_memory_and_security_passed": _phase_gate_passed(
            "phase6", _dict(payloads.get("phase6"))
        ),
    }


def _load_frozen_protocol(
    root: Path,
    phase0: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    descriptor = _dict(phase0.get("protocol"))
    relative = str(descriptor.get("path") or "")
    expected_sha256 = str(descriptor.get("sha256") or "")
    if not relative:
        return {}, ["phase0:protocol_path_missing"]
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return {}, ["phase0:protocol_path_outside_project_root"]
    if not path.is_file():
        return {}, ["phase0:protocol_artifact_missing"]
    try:
        payload = _dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}, ["phase0:protocol_artifact_invalid_json"]
    fingerprint_source = dict(payload)
    fingerprint_source.pop("protocol_sha256", None)
    actual_sha256 = _sha256_json(fingerprint_source)
    if (
        not expected_sha256
        or actual_sha256 != expected_sha256
        or str(payload.get("protocol_sha256") or "") != expected_sha256
    ):
        return {}, ["phase0:protocol_sha256_mismatch"]
    model = _dict(payload.get("model"))
    prompt_hashes = {
        str(_dict(item).get("id") or ""): str(
            _dict(item).get("sha256") or ""
        )
        for item in _list(payload.get("prompts"))
        if str(_dict(item).get("id") or "")
    }
    errors = []
    if not str(model.get("provider") or ""):
        errors.append("phase0:protocol_provider_missing")
    if not str(model.get("model_id") or ""):
        errors.append("phase0:protocol_model_id_missing")
    if not prompt_hashes or any(not digest for digest in prompt_hashes.values()):
        errors.append("phase0:protocol_prompt_hashes_incomplete")
    return payload, errors


def _phase_gate_passed(phase: str, payload: dict[str, Any]) -> bool:
    if phase == "phase3":
        return bool(
            payload.get("offline_foundation_status") == "pass"
            and payload.get("status") in {"partial", "pass"}
        )
    return payload.get("status") == "pass"


def _load_live_evaluation(
    root: Path,
    source: str | Path | dict[str, Any] | None,
    *,
    frozen_protocol: dict[str, Any],
) -> dict[str, Any]:
    if source is None:
        return {
            "status": "pending",
            "reason": "live_evaluation_artifact_not_supplied",
            "source": "none",
            "sha256": "",
            "errors": [],
            "case_count": 0,
            "expected_trial_count": REQUIRED_TOTAL_LIVE_TRIALS,
            "observed_trial_count": 0,
            "metrics": {},
        }
    payload: dict[str, Any]
    source_label = "in_memory:live_evaluation"
    if isinstance(source, dict):
        payload = _dict(source)
        digest = _sha256_json(payload)
    else:
        path = Path(source).resolve()
        source_label = _portable_source_label(root, path)
        if not path.is_file():
            return {
                "status": "invalid",
                "reason": "live_evaluation_artifact_missing",
                "source": source_label,
                "sha256": "",
                "errors": ["artifact_missing"],
                "case_count": 0,
                "expected_trial_count": REQUIRED_TOTAL_LIVE_TRIALS,
                "observed_trial_count": 0,
                "metrics": {},
            }
        try:
            payload = _dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return {
                "status": "invalid",
                "reason": "live_evaluation_artifact_invalid_json",
                "source": source_label,
                "sha256": _sha256_file(path),
                "errors": ["artifact_invalid_json"],
                "case_count": 0,
                "expected_trial_count": REQUIRED_TOTAL_LIVE_TRIALS,
                "observed_trial_count": 0,
                "metrics": {},
            }
        digest = _sha256_file(path)
    errors = _validate_live_evaluation(payload, frozen_protocol)
    completeness = _dict(payload.get("completeness"))
    return {
        "status": "pass" if not errors else "invalid",
        "reason": (
            "complete_live_evaluation_validated"
            if not errors
            else "live_evaluation_failed_release_validation"
        ),
        "source": source_label,
        "sha256": digest,
        "errors": errors,
        "case_count": _int(payload.get("case_count")),
        "record_count": _int(payload.get("record_count")),
        "expected_trial_count": _int(completeness.get("expected_trial_count")),
        "observed_trial_count": _int(completeness.get("observed_trial_count")),
        "missing_trial_count": _int(completeness.get("missing_trial_count")),
        "metrics": {
            strategy: _dict(_dict(payload.get("metrics")).get(strategy))
            for strategy in LIVE_STRATEGIES
        },
        "provider_model_metadata": _dict(payload.get("provider_model_metadata")),
        "case_examples": _extract_live_case_examples(payload),
    }


def _validate_live_evaluation(
    payload: dict[str, Any],
    frozen_protocol: dict[str, Any],
) -> list[str]:
    errors = []
    completeness = _dict(payload.get("completeness"))
    metrics = _dict(payload.get("metrics"))
    if payload.get("status") != "pass":
        errors.append("evaluation_status_not_pass")
    if payload.get("live_model") is not True:
        errors.append("live_model_flag_not_true")
    if _int(payload.get("case_count")) != REQUIRED_LIVE_CASES:
        errors.append("case_count_not_20")
    if _int(payload.get("record_count")) < REQUIRED_TOTAL_LIVE_TRIALS:
        errors.append("record_count_below_120")
    if _dict(payload.get("record_audit")).get("status") != "pass":
        errors.append("run_record_audit_not_pass")
    model_metadata = _dict(payload.get("provider_model_metadata"))
    frozen_model = _dict(frozen_protocol.get("model"))
    expected_provider = str(frozen_model.get("provider") or "")
    expected_model_id = str(frozen_model.get("model_id") or "")
    expected_prompt_hashes = {
        str(_dict(item).get("id") or ""): str(
            _dict(item).get("sha256") or ""
        )
        for item in _list(frozen_protocol.get("prompts"))
        if str(_dict(item).get("id") or "")
    }
    if model_metadata.get("status") != "pass":
        errors.append("provider_model_metadata_not_pass")
    if _int(model_metadata.get("model_record_count")) < REQUIRED_TOTAL_LIVE_TRIALS:
        errors.append("model_record_count_below_120")
    if _int(model_metadata.get("missing_core_metadata_count")) != 0:
        errors.append("provider_model_metadata_incomplete")
    for field in ("protocol_provider", "protocol_model_id"):
        if not str(model_metadata.get(field) or ""):
            errors.append(f"provider_model_metadata_missing:{field}")
    if str(model_metadata.get("protocol_provider") or "") != expected_provider:
        errors.append("protocol_provider_differs_from_frozen_protocol")
    if str(model_metadata.get("protocol_model_id") or "") != expected_model_id:
        errors.append("protocol_model_id_differs_from_frozen_protocol")
    observed_providers = {
        str(value) for value in _list(model_metadata.get("observed_providers"))
    }
    observed_model_ids = {
        str(value) for value in _list(model_metadata.get("observed_model_ids"))
    }
    observed_prompt_ids = {
        str(value) for value in _list(model_metadata.get("observed_prompt_ids"))
    }
    if observed_providers != {expected_provider}:
        errors.append("observed_providers_differ_from_frozen_protocol")
    if observed_model_ids != {expected_model_id}:
        errors.append("observed_model_ids_differ_from_frozen_protocol")
    if not observed_prompt_ids or not observed_prompt_ids.issubset(
        expected_prompt_hashes
    ):
        errors.append("observed_prompt_ids_differ_from_frozen_protocol")
    actual_prompt_hashes = _dict(model_metadata.get("protocol_prompt_hashes"))
    if not actual_prompt_hashes:
        errors.append("provider_model_metadata_missing:protocol_prompt_hashes")
    elif actual_prompt_hashes != expected_prompt_hashes:
        errors.append("protocol_prompt_hashes_differ_from_frozen_protocol")
    if completeness.get("status") != "pass":
        errors.append("completeness_status_not_pass")
    if _int(completeness.get("expected_trial_count")) != REQUIRED_TOTAL_LIVE_TRIALS:
        errors.append("expected_trial_count_not_120")
    if _int(completeness.get("observed_trial_count")) != REQUIRED_TOTAL_LIVE_TRIALS:
        errors.append("observed_trial_count_not_120")
    if _int(completeness.get("missing_trial_count")) != 0:
        errors.append("missing_live_trials")
    strategies = {str(value) for value in _list(payload.get("strategies"))}
    for strategy in LIVE_STRATEGIES:
        if strategy not in strategies:
            errors.append(f"strategy_missing:{strategy}")
        row = _dict(metrics.get(strategy))
        if _int(row.get("case_denominator")) != REQUIRED_LIVE_CASES:
            errors.append(f"case_denominator_not_20:{strategy}")
        if _int(row.get("observed_trial_count")) != REQUIRED_TRIALS_PER_STRATEGY:
            errors.append(f"observed_trial_count_not_60:{strategy}")
        for field in REQUIRED_LIVE_METRICS:
            if field not in row or row.get(field) is None:
                errors.append(f"required_metric_missing:{strategy}:{field}")
        if not isinstance(row.get("token_usage"), dict):
            errors.append(f"token_usage_missing:{strategy}")
    return sorted(set(errors))


def _build_metric_registry(
    phases: dict[str, dict[str, Any]],
    live: dict[str, Any],
) -> dict[str, Any]:
    phase1 = _dict(phases.get("phase1"))
    phase2 = _dict(phases.get("phase2"))
    phase3 = _dict(phases.get("phase3"))
    phase4 = _dict(phases.get("phase4"))
    phase5 = _dict(phases.get("phase5"))
    phase6 = _dict(phases.get("phase6"))
    catalog = _dict(phase1.get("catalog"))
    startup = _dict(phase2.get("startup_evaluation"))
    rule = _dict(phase3.get("rule_baseline"))
    localization = _dict(_dict(phase4.get("evaluation")).get("frozen_test_fusion"))
    localization_n = _int(_dict(phase4.get("evaluation")).get("test_case_count"))
    calibration = _dict(phase5.get("calibration"))
    memory = _dict(phase6.get("memory_evaluation"))
    security = _dict(phase6.get("security_evaluation"))
    repair_strategies = {
        "rule": _repair_metric(
            "measured",
            rule,
            source="docs/v3/phase3_offline_verification.json",
        )
    }
    for strategy in LIVE_STRATEGIES:
        repair_strategies[strategy] = (
            _repair_metric(
                "measured",
                _dict(_dict(live.get("metrics")).get(strategy)),
                source=str(live.get("source") or "live_evaluation"),
            )
            if live.get("status") == "pass"
            else _pending_repair_metric(strategy)
        )
    startup_count = _int(startup.get("started_and_terminated_count"))
    startup_n = _int(startup.get("case_count"))
    return {
        "benchmark": {
            "evidence_state": "measured",
            "headline": (
                f"{_int(catalog.get('accepted_cases'))} accepted real bugs from "
                f"{_int(catalog.get('repository_count'))} repositories"
            ),
            "accepted_case_count": _int(catalog.get("accepted_cases")),
            "rejected_case_count": _int(catalog.get("rejected_cases")),
            "repository_count": _int(catalog.get("repository_count")),
            "source": "docs/v3/phase1_verification.json",
        },
        "repository_environment": {
            "evidence_state": "measured",
            "headline": f"{startup_count}/{startup_n} test processes started and terminated",
            "started_and_terminated_count": startup_count,
            "case_count": startup_n,
            "rate": _float(startup.get("started_and_terminated_rate")),
            "wilson_95pct": _wilson_interval(startup_count, startup_n),
            "classified_not_started_blocker_count": _int(
                startup.get("classified_not_started_blocker_count")
            ),
            "source": "docs/v3/phase2_verification.json",
        },
        "fault_localization": {
            "evidence_state": "measured",
            "headline": (
                f"frozen test Top-1/3/5={_float(localization.get('top1')):.2f}/"
                f"{_float(localization.get('top3')):.2f}/"
                f"{_float(localization.get('top5')):.2f}"
            ),
            "test_case_count": localization_n,
            "top1": _float(localization.get("top1")),
            "top3": _float(localization.get("top3")),
            "top5": _float(localization.get("top5")),
            "mrr": _float(localization.get("mrr")),
            "map": _float(localization.get("map")),
            "exam": _float(localization.get("exam")),
            "top1_wilson_95pct": _wilson_interval(
                _count_from_rate(localization.get("top1"), localization_n),
                localization_n,
            ),
            "top3_wilson_95pct": _wilson_interval(
                _count_from_rate(localization.get("top3"), localization_n),
                localization_n,
            ),
            "top5_wilson_95pct": _wilson_interval(
                _count_from_rate(localization.get("top5"), localization_n),
                localization_n,
            ),
            "source": "docs/v3/phase4_verification.json",
        },
        "repair_strategies": repair_strategies,
        "planner": {
            "evidence_state": "safety_policy_measured",
            "headline": "conflict and repository-injection overrides are rejected",
            "task_outcome_metric_state": "not_separately_measured_in_v3",
            "llm_override_on_conflict": "rejected",
            "llm_override_on_repository_injection": "rejected",
            "source": "docs/v3/phase6_verification.json",
        },
        "reflection": {
            "evidence_state": (
                "measured" if live.get("status") == "pass" else "pending"
            ),
            "headline": (
                "live reflection recovery available"
                if live.get("status") == "pass"
                else "requires 120 live LLM/Hybrid trials"
            ),
            "llm_rate": repair_strategies["llm"].get(
                "reflection_recovery_rate"
            ),
            "hybrid_rate": repair_strategies["hybrid"].get(
                "reflection_recovery_rate"
            ),
            "source": (
                str(live.get("source"))
                if live.get("status") == "pass"
                else "pending_live_evaluation"
            ),
        },
        "semantic_validation": {
            "evidence_state": "calibration_only",
            "headline": (
                f"{_int(calibration.get('semantic_pass_count'))}/"
                f"{_int(calibration.get('human_fix_case_count'))} human fixes accepted"
            ),
            "human_fix_case_count": _int(calibration.get("human_fix_case_count")),
            "semantic_pass_count": _int(calibration.get("semantic_pass_count")),
            "false_rejection_count": _int(calibration.get("false_rejection_count")),
            "reverse_mutation_killed_count": _int(
                calibration.get("reverse_mutation_killed_count")
            ),
            "reverse_mutation_count": _int(calibration.get("reverse_mutation_count")),
            "agent_repair_claim": False,
            "source": "docs/v3/phase5_verification.json",
        },
        "memory": {
            "evidence_state": "measured",
            "headline": (
                f"completion {_float(memory.get('without_memory_completion')):.4f} -> "
                f"{_float(memory.get('structured_v2_completion')):.4f}"
            ),
            **memory,
            "source": "docs/v3/phase6_verification.json",
        },
        "security": {
            "evidence_state": "measured_controlled_fixtures",
            "headline": (
                f"{_int(security.get('passed_case_count'))}/"
                f"{_int(security.get('case_count'))} controlled threats handled"
            ),
            **security,
            "source": "docs/v3/phase6_verification.json",
        },
        "cost_and_latency": {
            "evidence_state": (
                "measured" if live.get("status") == "pass" else "partial"
            ),
            "headline": (
                "Rule cost USD 0; live LLM/Hybrid cost pending"
                if live.get("status") != "pass"
                else "Rule, LLM, and Hybrid cost/latency available"
            ),
            "rule_actual_cost_usd": _float(rule.get("actual_cost_usd")),
            "llm_actual_cost_usd": repair_strategies["llm"].get(
                "actual_cost_usd"
            ),
            "hybrid_actual_cost_usd": repair_strategies["hybrid"].get(
                "actual_cost_usd"
            ),
            "source": (
                "docs/v3/phase3_offline_verification.json and live evaluation"
            ),
        },
    }


def _repair_metric(
    evidence_state: str,
    source_row: dict[str, Any],
    *,
    source: str,
) -> dict[str, Any]:
    denominator = _int(
        source_row.get("case_denominator") or source_row.get("case_count")
    )
    pass1_count = _int(
        source_row.get("pass_at_1_count")
        if source_row.get("pass_at_1_count") is not None
        else _count_from_rate(source_row.get("pass_at_1"), denominator)
    )
    pass3_count = _int(
        source_row.get("pass_at_3_count")
        if source_row.get("pass_at_3_count") is not None
        else _count_from_rate(source_row.get("pass_at_3"), denominator)
    )
    return {
        "evidence_state": evidence_state,
        "case_denominator": denominator,
        "observed_trial_count": _int(source_row.get("observed_trial_count")),
        "pass_at_1": _nullable_float(source_row.get("pass_at_1")),
        "pass_at_1_count": pass1_count,
        "pass_at_1_wilson_95pct": _wilson_interval(pass1_count, denominator),
        "pass_at_3": _nullable_float(source_row.get("pass_at_3")),
        "pass_at_3_count": pass3_count,
        "pass_at_3_wilson_95pct": _wilson_interval(pass3_count, denominator),
        "verified_repair_rate": _nullable_float(
            source_row.get("verified_repair_rate")
        ),
        "reflection_recovery_rate": _nullable_float(
            source_row.get("reflection_recovery_rate")
        ),
        "ast_valid_rate": _nullable_float(source_row.get("ast_valid_rate")),
        "safety_pass_rate": _nullable_float(source_row.get("safety_pass_rate")),
        "targeted_test_pass_rate": _nullable_float(
            source_row.get("targeted_test_pass_rate")
        ),
        "full_regression_pass_rate": _nullable_float(
            source_row.get("full_regression_pass_rate")
        ),
        "semantic_claim_eligible_rate": _nullable_float(
            source_row.get("semantic_claim_eligible_rate")
        ),
        "actual_cost_usd": _nullable_float(source_row.get("actual_cost_usd")),
        "latency_ms": _nullable_float(source_row.get("latency_ms")),
        "token_usage": _dict(source_row.get("token_usage")),
        "failure_layers": _dict(source_row.get("failure_layers")),
        "failure_categories": _dict(source_row.get("failure_categories")),
        "winning_generator_families": _dict(
            source_row.get("winning_generator_families")
        ),
        "source": source,
    }


def _pending_repair_metric(strategy: str) -> dict[str, Any]:
    return {
        "evidence_state": "pending",
        "case_denominator": REQUIRED_LIVE_CASES,
        "required_trial_count": REQUIRED_TRIALS_PER_STRATEGY,
        "observed_trial_count": 0,
        "pass_at_1": None,
        "pass_at_3": None,
        "verified_repair_rate": None,
        "reflection_recovery_rate": None,
        "ast_valid_rate": None,
        "safety_pass_rate": None,
        "targeted_test_pass_rate": None,
        "full_regression_pass_rate": None,
        "semantic_claim_eligible_rate": None,
        "actual_cost_usd": None,
        "latency_ms": None,
        "token_usage": {},
        "failure_layers": {},
        "failure_categories": {},
        "winning_generator_families": {},
        "source": "pending_live_evaluation",
        "reason": f"{strategy}_requires_20_cases_x_3_independent_trials",
    }


def _build_comparison_registry(
    phases: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    paired = _dict(_dict(phases.get("phase2")).get("paired_baseline"))
    protocols_identical = bool(paired.get("protocols_identical"))
    return {
        "v2_v3_repository_startup": {
            "status": "comparable" if protocols_identical else "not_comparable",
            "reason": (
                "paired protocol is identical"
                if protocols_identical
                else "V3 adds isolated runtimes and new startup policy; raw counts are context only"
            ),
            "v2_count": _int(paired.get("v2_started_and_terminated_count")),
            "v2_denominator": _int(paired.get("v2_case_count")),
            "v3_count": _int(paired.get("v3_started_and_terminated_count")),
            "v3_denominator": _int(paired.get("v3_case_count")),
            "difference_claim_eligible": protocols_identical,
        },
        "v2_v3_fault_localization": {
            "status": "not_comparable",
            "reason": "V3 uses a real-bug repository-disjoint split and V2 uses a controlled mutation benchmark",
            "difference_claim_eligible": False,
        },
        "v2_v3_patch_repair": {
            "status": "not_comparable",
            "reason": "V2 LLM metrics use deterministic fixtures; V3 live-model metrics are pending",
            "difference_claim_eligible": False,
        },
        "v2_v3_memory": {
            "status": "not_comparable",
            "reason": "V3 changes authority, conflict, stale-scope, and advisory-memory gates",
            "difference_claim_eligible": False,
        },
    }


def _pending_live_requirements(live: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": "fresh_environment_injected_api_key",
            "requirement": "Inject a fresh provider key through the frozen protocol environment variable; never persist it.",
        },
        {
            "id": "llm_trials",
            "requirement": "Run 20 real bugs x 3 independent LLM trials (60 trials).",
        },
        {
            "id": "hybrid_trials",
            "requirement": "Run 20 real bugs x 3 independent Hybrid trials (60 trials).",
        },
        {
            "id": "complete_live_artifact",
            "requirement": (
                "Supply a passing evaluation with 120/120 trials, zero missing trials, "
                "RunRecord audit pass, pass@1/pass@3, semantic verification, reflection, "
                "token, cost, latency, failure taxonomy, and generator attribution."
            ),
            "current_live_status": str(live.get("status") or "pending"),
            "validation_errors": _list(live.get("errors")),
        },
    ]


def _case_examples(
    phases: dict[str, dict[str, Any]],
    live: dict[str, Any],
) -> dict[str, Any]:
    rule = _dict(_dict(phases.get("phase3")).get("rule_baseline"))
    security = _dict(_dict(phases.get("phase6")).get("security_evaluation"))
    live_examples = _dict(live.get("case_examples"))
    return {
        "direct_live_repair": live_examples.get("direct_repair") or {
            "status": "pending",
            "reason": "live_trials_not_complete",
        },
        "reflection_live_repair": live_examples.get("reflection_repair") or {
            "status": "pending",
            "reason": "live_trials_not_complete",
        },
        "rule_failure_taxonomy": {
            "status": "measured",
            "failure_layers": _dict(rule.get("failure_layers")),
            "failure_categories": _dict(rule.get("failure_categories")),
        },
        "environment_blocker": {
            "status": "measured",
            "count": _int(
                _dict(_dict(phases.get("phase2")).get("startup_evaluation")).get(
                    "classified_not_started_blocker_count"
                )
            ),
        },
        "security_rejections": {
            "status": "measured_controlled_fixtures",
            "case_count": _int(security.get("case_count")),
            "dispositions": _dict(security.get("dispositions")),
        },
    }


def _extract_live_case_examples(payload: dict[str, Any]) -> dict[str, Any]:
    direct: dict[str, Any] = {}
    reflection: dict[str, Any] = {}
    for case_value in _list(payload.get("case_results")):
        case = _dict(case_value)
        case_id = str(case.get("case_id") or "")
        for trial_value in _list(case.get("trials")):
            trial = _dict(trial_value)
            if str(trial.get("status") or "") != "verified_repair":
                continue
            example = {
                "status": "measured",
                "case_id": case_id,
                "strategy": str(trial.get("strategy_mode") or ""),
                "trial_index": _int(trial.get("trial_index")),
                "generator": str(trial.get("best_generator") or ""),
            }
            if trial.get("reflection_recovered") and not reflection:
                reflection = example
            elif not trial.get("reflection_recovered") and not direct:
                direct = example
    return {"direct_repair": direct, "reflection_repair": reflection}


def _wilson_interval(successes: int, denominator: int) -> dict[str, Any]:
    if denominator <= 0:
        return {
            "status": "not_available",
            "successes": successes,
            "denominator": denominator,
            "lower": None,
            "upper": None,
        }
    z = 1.96
    p = successes / denominator
    z2 = z * z
    scale = 1 + z2 / denominator
    center = (p + z2 / (2 * denominator)) / scale
    margin = (
        z
        * math.sqrt(p * (1 - p) / denominator + z2 / (4 * denominator**2))
        / scale
    )
    return {
        "status": "measured",
        "method": "wilson_95pct",
        "successes": successes,
        "denominator": denominator,
        "lower": round(max(0.0, center - margin), 6),
        "upper": round(min(1.0, center + margin), 6),
    }


def _count_from_rate(value: Any, denominator: int) -> int:
    if value is None or denominator <= 0:
        return 0
    return int(round(_float(value) * denominator))


def _portable_source_label(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return "<external-live-evaluation>"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_json(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _write_text_lf(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


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


def _nullable_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric(value: Any) -> str:
    return "pending" if value is None else f"{_float(value):.4f}"


def _strategy_metric(strategy: str, field: str, item: dict[str, Any]) -> str:
    if strategy == "rule" and field in {"reflection_recovery_rate", "latency_ms"}:
        return "n/a"
    return _metric(item.get(field))


def _interval_row(label: str, value: Any) -> str:
    interval = _dict(value)
    if interval.get("status") != "measured":
        return f"| {label} | n/a | n/a |"
    successes = _int(interval.get("successes"))
    denominator = _int(interval.get("denominator"))
    return (
        f"| {label} | {successes}/{denominator} | "
        f"[{_float(interval.get('lower')):.4f}, {_float(interval.get('upper')):.4f}] |"
    )


def _md(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate V3 evidence without fabricating pending live-model metrics."
    )
    parser.add_argument("output_dir")
    parser.add_argument("--root", default=".")
    parser.add_argument("--live-evaluation")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument("--require-offline-pass", action="store_true")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)
    payload = evaluate_v3_release(
        args.root,
        live_evaluation=args.live_evaluation,
    )
    write_v3_release_artifacts(payload, args.output_dir)
    print(
        json.dumps(payload, indent=2, ensure_ascii=False)
        if args.format == "json"
        else render_v3_release_markdown(payload)
    )
    if args.require_offline_pass and payload["offline_release_status"] != "pass":
        raise SystemExit(1)
    if args.require_complete and payload["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
