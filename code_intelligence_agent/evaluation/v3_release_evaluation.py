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
    comparisons = _build_comparison_registry(phase_payloads, live=live)
    release_gates = {
        **offline_gates,
        "live_provider_access_preflight_passed": (
            live["status"] == "pass"
            and _dict(live.get("provider_access_preflight")).get("status") == "pass"
        ),
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
        "latest_regression": _latest_regression(phase_payloads),
        "denominator_policy": {
            "failed_trials_retained": True,
            "provider_and_environment_blockers_separated": True,
            "missing_trials_never_imputed": True,
            "calibration_not_counted_as_agent_repair": True,
            "historical_metrics_compared_only_when_protocols_match": True,
        },
        "case_examples": _case_examples(phase_payloads, live),
        "claim_boundaries": _claim_boundaries(complete=complete),
    }


def _latest_regression(phase_payloads: dict[str, Any]) -> dict[str, Any]:
    phase5_tests = _dict(_dict(phase_payloads.get("phase5")).get("tests"))
    phase6_tests = _dict(_dict(phase_payloads.get("phase6")).get("tests"))
    latest = dict(phase6_tests)
    for field in (
        "phase5_and_repair_regression",
        "all_v3",
        "full_pytest",
        "release_hygiene",
        "skip_explanation",
    ):
        if field in phase5_tests:
            latest[field] = phase5_tests[field]
    latest["latest_general_regression_source"] = (
        "docs/v3/phase5_verification.json"
    )
    latest["phase6_specialized_regression_source"] = (
        "docs/v3/phase6_verification.json"
    )
    return latest


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
    provider_preflight = _dict(
        _dict(payload.get("live_evaluation")).get("provider_access_preflight")
    )
    run_record_evidence = _dict(
        _dict(payload.get("live_evaluation")).get("run_record_evidence")
    )
    trial_distributions = _dict(
        _dict(payload.get("live_evaluation")).get(
            "trial_cost_latency_distribution"
        )
    )
    lines.extend(
        [
            "",
            "## Provider Access Preflight",
            "",
            f"- Status: `{provider_preflight.get('status') or 'pending'}`",
            "- Counted as repair trial: `false`",
            f"- Cost USD: `{_metric(provider_preflight.get('actual_cost_usd'))}`",
            f"- Latency ms: `{_metric(provider_preflight.get('latency_ms'))}`",
            "",
            "## RunRecord Evidence",
            "",
            f"- Status: `{run_record_evidence.get('status') or 'not_supplied'}`",
            f"- Records: `{_int(run_record_evidence.get('record_count'))}`",
            f"- SHA-256: `{run_record_evidence.get('sha256') or 'not_available'}`",
            "- Raw RunRecords copied into release report: `false`",
            "",
            "## Trial Cost and Latency Distribution",
            "",
            "| Strategy | Trials | Cost mean | Cost stddev | Latency mean ms | Latency stddev ms |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for strategy in LIVE_STRATEGIES:
        distribution = _dict(trial_distributions.get(strategy))
        cost = _dict(distribution.get("cost_usd"))
        latency = _dict(distribution.get("latency_ms"))
        lines.append(
            f"| `{strategy}` | {_int(distribution.get('trial_count'))} | "
            f"{_metric(cost.get('mean'))} | {_metric(cost.get('population_stddev'))} | "
            f"{_metric(latency.get('mean'))} | "
            f"{_metric(latency.get('population_stddev'))} |"
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
            _interval_row(
                "LLM pass@1",
                _dict(strategy_metrics.get("llm")).get("pass_at_1_wilson_95pct"),
            ),
            _interval_row(
                "LLM pass@3",
                _dict(strategy_metrics.get("llm")).get("pass_at_3_wilson_95pct"),
            ),
            _interval_row(
                "Hybrid pass@1",
                _dict(strategy_metrics.get("hybrid")).get("pass_at_1_wilson_95pct"),
            ),
            _interval_row(
                "Hybrid pass@3",
                _dict(strategy_metrics.get("hybrid")).get("pass_at_3_wilson_95pct"),
            ),
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
    lines.extend(
        [
            "",
            "## Generator Attribution",
            "",
            "| Strategy | Winning generator families | Provider blockers | Environment blockers |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    for strategy in ("rule", "llm", "hybrid"):
        item = _dict(strategy_metrics.get(strategy))
        lines.append(
            f"| `{strategy}` | {_md(_mapping_summary(item.get('winning_generator_families')))} | "
            f"{_int(item.get('provider_blocker_record_count'))} | "
            f"{_int(item.get('environment_blocker_record_count'))} |"
        )
    examples = _dict(payload.get("case_examples"))
    lines.extend(
        [
            "",
            "## Audited Live Examples",
            "",
            "| Type | Status | Case | Strategy/trial | Generator | Outcome or failure |",
            "| --- | --- | --- | --- | --- | --- |",
            _case_example_row("Direct repair", examples.get("direct_live_repair")),
            _case_example_row(
                "Reflection repair",
                examples.get("reflection_live_repair"),
            ),
            _case_example_row("Failed repair", examples.get("failed_live_repair")),
            _case_example_row("Provider blocker", examples.get("provider_blocker")),
            "",
            f"- Environment blockers: `{_int(_dict(examples.get('environment_blocker')).get('count'))}`",
            f"- Controlled security cases: `{_int(_dict(examples.get('security_rejections')).get('case_count'))}`",
        ]
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
    source_path: Path | None = None
    source_label = "in_memory:live_evaluation"
    if isinstance(source, dict):
        payload = _dict(source)
        digest = _sha256_json(payload)
    else:
        path = Path(source).resolve()
        source_path = path
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
    run_record_evidence, run_records = _load_live_run_record_evidence(
        root,
        source_path,
        expected_record_count=_int(payload.get("record_count")),
    )
    errors = _validate_live_evaluation(payload, frozen_protocol)
    if run_record_evidence.get("status") == "invalid":
        errors.extend(
            f"run_record_evidence:{value}"
            for value in _list(run_record_evidence.get("errors"))
        )
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
        "provider_access_preflight": _dict(
            payload.get("provider_access_preflight")
        ),
        "run_record_evidence": run_record_evidence,
        "trial_cost_latency_distribution": _trial_cost_latency_distribution(
            run_records
        ),
        "case_examples": _extract_live_case_examples(
            payload,
            run_records=run_records,
        ),
    }


def _load_live_run_record_evidence(
    root: Path,
    evaluation_path: Path | None,
    *,
    expected_record_count: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if evaluation_path is None:
        return (
            {
                "status": "not_supplied",
                "source": "none",
                "sha256": "",
                "record_count": 0,
                "expected_record_count": expected_record_count,
                "errors": [],
                "raw_records_persisted_in_release_report": False,
            },
            [],
        )
    path = evaluation_path.with_name("run_records.jsonl")
    if not path.is_file():
        return (
            {
                "status": "not_supplied",
                "source": _portable_source_label(root, path),
                "sha256": "",
                "record_count": 0,
                "expected_record_count": expected_record_count,
                "errors": [],
                "raw_records_persisted_in_release_report": False,
            },
            [],
        )
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_run_ids: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
        errors.append("artifact_unreadable")
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = _dict(json.loads(line))
        except json.JSONDecodeError:
            errors.append(f"invalid_json_line:{line_number}")
            continue
        if not record:
            errors.append(f"record_not_object:{line_number}")
            continue
        run_id = str(record.get("run_id") or "")
        if not run_id:
            errors.append(f"run_id_missing:{line_number}")
        elif run_id in seen_run_ids:
            errors.append(f"duplicate_run_id:{run_id}")
        else:
            seen_run_ids.add(run_id)
        records.append(record)
    if len(records) != expected_record_count:
        errors.append(
            f"record_count_mismatch:{len(records)}!={expected_record_count}"
        )
    return (
        {
            "status": "pass" if not errors else "invalid",
            "source": _portable_source_label(root, path),
            "sha256": _sha256_file(path),
            "record_count": len(records),
            "expected_record_count": expected_record_count,
            "errors": errors,
            "raw_records_persisted_in_release_report": False,
        },
        records,
    )


def _trial_cost_latency_distribution(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    grouped: dict[tuple[str, str], dict[str, float]] = {}
    for record in records:
        strategy = _dict(record.get("strategy"))
        mode = str(strategy.get("mode") or "")
        trial_id = str(strategy.get("trial_id") or "")
        if mode not in LIVE_STRATEGIES or not trial_id:
            continue
        totals = grouped.setdefault(
            (mode, trial_id),
            {"cost_usd": 0.0, "latency_ms": 0.0},
        )
        totals["cost_usd"] += _float(
            _dict(record.get("cost")).get("actual_cost_usd")
        )
        totals["latency_ms"] += _float(
            _dict(record.get("timing")).get("latency_ms")
        )
    result: dict[str, Any] = {}
    for mode in LIVE_STRATEGIES:
        rows = [
            totals
            for (strategy, _), totals in grouped.items()
            if strategy == mode
        ]
        result[mode] = {
            "trial_count": len(rows),
            "cost_usd": _distribution(
                [row["cost_usd"] for row in rows],
                digits=9,
            ),
            "latency_ms": _distribution(
                [row["latency_ms"] for row in rows],
                digits=3,
            ),
        }
    return result


def _distribution(values: list[float], *, digits: int) -> dict[str, Any]:
    if not values:
        return {
            "status": "not_available",
            "count": 0,
            "total": None,
            "mean": None,
            "population_variance": None,
            "population_stddev": None,
            "minimum": None,
            "median": None,
            "maximum": None,
        }
    ordered = sorted(values)
    count = len(ordered)
    total = sum(ordered)
    mean = total / count
    variance = sum((value - mean) ** 2 for value in ordered) / count
    midpoint = count // 2
    median = (
        ordered[midpoint]
        if count % 2
        else (ordered[midpoint - 1] + ordered[midpoint]) / 2
    )
    return {
        "status": "measured",
        "count": count,
        "total": round(total, digits),
        "mean": round(mean, digits),
        "population_variance": round(variance, digits),
        "population_stddev": round(math.sqrt(variance), digits),
        "minimum": round(ordered[0], digits),
        "median": round(median, digits),
        "maximum": round(ordered[-1], digits),
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
    provider_preflight = _dict(payload.get("provider_access_preflight"))
    frozen_preflight = _dict(frozen_model.get("access_preflight"))
    expected_provider = str(frozen_model.get("provider") or "")
    expected_model_id = str(frozen_model.get("model_id") or "")
    expected_prompt_hashes = {
        str(_dict(item).get("id") or ""): str(
            _dict(item).get("sha256") or ""
        )
        for item in _list(frozen_protocol.get("prompts"))
        if str(_dict(item).get("id") or "")
    }
    expected_preflight_prompt_id = str(frozen_preflight.get("prompt_id") or "")
    expected_preflight_request_hash = str(
        frozen_preflight.get("request_prompt_sha256") or ""
    )
    if str(provider_preflight.get("schema_version") or "") != (
        "v3_provider_access_preflight_v1"
    ):
        errors.append("provider_access_preflight_schema_version_invalid")
    if provider_preflight.get("status") != "pass":
        errors.append("provider_access_preflight_not_pass")
    if provider_preflight.get("performed") is not True:
        errors.append("provider_access_preflight_not_performed")
    if provider_preflight.get("counted_as_repair_trial") is not False:
        errors.append("provider_access_preflight_counted_as_repair_trial")
    if str(provider_preflight.get("provider") or "") != expected_provider:
        errors.append("provider_access_preflight_provider_mismatch")
    if str(provider_preflight.get("protocol_model_id") or "") != expected_model_id:
        errors.append("provider_access_preflight_protocol_model_mismatch")
    if str(provider_preflight.get("observed_model_id") or "") != expected_model_id:
        errors.append("provider_access_preflight_observed_model_mismatch")
    if str(provider_preflight.get("prompt_id") or "") != expected_preflight_prompt_id:
        errors.append("provider_access_preflight_prompt_id_mismatch")
    if str(provider_preflight.get("prompt_template_sha256") or "") != str(
        expected_prompt_hashes.get(expected_preflight_prompt_id) or ""
    ):
        errors.append("provider_access_preflight_prompt_hash_mismatch")
    if str(provider_preflight.get("request_prompt_sha256") or "") != (
        expected_preflight_request_hash
    ):
        errors.append("provider_access_preflight_request_prompt_hash_mismatch")
    if provider_preflight.get("cost_attribution") != "provider_preflight_overhead":
        errors.append("provider_access_preflight_cost_attribution_invalid")
    if provider_preflight.get("response_content_retained") is not False:
        errors.append("provider_access_preflight_response_content_retained")
    if provider_preflight.get("response_content_used_for_repair") is not False:
        errors.append("provider_access_preflight_response_used_for_repair")
    if any(
        field in provider_preflight
        for field in ("response_content", "raw_response", "response_body")
    ):
        errors.append("provider_access_preflight_response_content_present")
    preflight_cost = _nullable_float(provider_preflight.get("actual_cost_usd"))
    if preflight_cost is None or preflight_cost < 0:
        errors.append("provider_access_preflight_cost_invalid")
    preflight_latency = _nullable_float(provider_preflight.get("latency_ms"))
    if preflight_latency is None or preflight_latency < 0:
        errors.append("provider_access_preflight_latency_invalid")
    preflight_usage = _dict(provider_preflight.get("usage"))
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        value = _nullable_float(preflight_usage.get(field))
        if value is None or value < 0:
            errors.append(f"provider_access_preflight_usage_invalid:{field}")
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
    provider_preflight = _dict(live.get("provider_access_preflight"))
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
            "provider_preflight_actual_cost_usd": (
                _float(provider_preflight.get("actual_cost_usd"))
                if live.get("status") == "pass"
                else None
            ),
            "provider_preflight_latency_ms": (
                _int(provider_preflight.get("latency_ms"))
                if live.get("status") == "pass"
                else None
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
        "provider_blocker_record_count": _int(
            source_row.get("provider_blocker_record_count")
        ),
        "environment_blocker_record_count": _int(
            source_row.get("environment_blocker_record_count")
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
    *,
    live: dict[str, Any],
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
            "reason": (
                "V2 LLM metrics use deterministic fixtures while V3 uses a completed "
                "120-trial live-model real-bug protocol; no uplift is calculated"
                if live.get("status") == "pass"
                else "V2 LLM metrics use deterministic fixtures; V3 live-model metrics are pending"
            ),
            "difference_claim_eligible": False,
        },
        "v2_v3_memory": {
            "status": "not_comparable",
            "reason": "V3 changes authority, conflict, stale-scope, and advisory-memory gates",
            "difference_claim_eligible": False,
        },
    }


def _claim_boundaries(*, complete: bool) -> list[str]:
    live_boundary = (
        "Offline Phase 0-6 evidence is combined with a validated 120-trial live LLM/Hybrid evaluation; neither substitutes for the other."
        if complete
        else "Offline Phase 0-6 evidence does not substitute for the pending 120 live LLM and Hybrid trials."
    )
    return [
        live_boundary,
        "Rule metrics, human-fix semantic calibration, and deterministic memory/security fixtures are not live-model repair rates.",
        "V2/V3 numbers are not presented as improvements when their protocols differ.",
        "A complete V3 release retains all failed trials, provider blockers, environment blockers, token usage, cost, latency, and generator attribution in the denominator.",
        "Provider-access preflight overhead is reported separately and never counted as a repair Trial or pass@k success.",
        "Process-level repository defenses do not provide container-grade isolation for native child processes on Windows.",
    ]


def _pending_live_requirements(live: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": "environment_injected_provider_access",
            "requirement": (
                "Use an environment-injected provider key with valid authentication, "
                "billing/quota, and frozen-model access; never persist the key."
            ),
        },
        {
            "id": "provider_access_preflight",
            "requirement": (
                "Record one passing frozen provider-access preflight with exact-model "
                "verification and separate token, cost, and latency overhead."
            ),
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
                "token, cost, latency, failure taxonomy, generator attribution, and a "
                "passing provider-access preflight."
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
    missing_reason = (
        "example_not_available_in_live_artifact"
        if live.get("status") == "pass"
        else "live_trials_not_complete"
    )
    return {
        "direct_live_repair": live_examples.get("direct_repair") or {
            "status": "pending",
            "reason": missing_reason,
        },
        "reflection_live_repair": live_examples.get("reflection_repair") or {
            "status": "pending",
            "reason": missing_reason,
        },
        "failed_live_repair": live_examples.get("failed_repair") or {
            "status": "pending",
            "reason": missing_reason,
        },
        "provider_blocker": live_examples.get("provider_blocker") or {
            "status": "not_observed",
            "reason": (
                "no_provider_blocker_in_live_records"
                if live.get("status") == "pass"
                else "live_trials_not_complete"
            ),
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


def _extract_live_case_examples(
    payload: dict[str, Any],
    *,
    run_records: list[dict[str, Any]],
) -> dict[str, Any]:
    direct: dict[str, Any] = {}
    reflection: dict[str, Any] = {}
    failed: dict[str, Any] = {}
    provider_blocker: dict[str, Any] = {}
    records_by_run_id = {
        str(record.get("run_id") or ""): record
        for record in run_records
        if str(record.get("run_id") or "")
    }
    failed_case_ids: set[str] = set()
    for case_value in _list(payload.get("case_results")):
        case = _dict(case_value)
        case_id = str(case.get("case_id") or "")
        trials = [_dict(value) for value in _list(case.get("trials"))]
        if trials and not any(
            bool(trial.get("verified_repair")) for trial in trials
        ):
            failed_case_ids.add(case_id)
        for trial in trials:
            if not bool(trial.get("verified_repair")):
                continue
            winning_run_id = str(trial.get("winning_run_id") or "")
            winning_record = _dict(records_by_run_id.get(winning_run_id))
            example = (
                _run_record_example(winning_record)
                if winning_record
                else {
                    "status": "measured",
                    "case_id": case_id,
                    "strategy": str(trial.get("strategy_mode") or ""),
                    "trial_index": _int(trial.get("trial_index")),
                    "run_id": winning_run_id,
                    "generator_family": str(
                        trial.get("winning_generator_family") or ""
                    ),
                    "generator_id": str(trial.get("winning_generator_id") or ""),
                    "reflection_round": _int(
                        trial.get("winning_reflection_round")
                    ),
                    "direct_success": bool(trial.get("direct_success")),
                    "reflection_recovered": bool(
                        trial.get("reflection_recovered")
                    ),
                }
            )
            if example.get("reflection_recovered") and not reflection:
                reflection = example
            elif example.get("direct_success") and not direct:
                direct = example
    failure_priority = {
        "targeted_test": 0,
        "full_regression": 1,
        "semantic_validation": 2,
        "syntax": 3,
        "safety": 4,
        "generation": 5,
    }
    failed_candidates = [
        record
        for record in run_records
        if (
            str(_dict(record.get("outcome")).get("status") or "")
            in {"failed", "safety_rejected"}
            and str(_dict(record.get("case")).get("case_id") or "")
            in failed_case_ids
        )
    ]
    failed_candidates.sort(
        key=lambda record: (
            failure_priority.get(
                str(_dict(record.get("failure")).get("layer") or ""),
                99,
            ),
            str(_dict(record.get("case")).get("case_id") or ""),
            _int(_dict(record.get("strategy")).get("trial_index")),
        )
    )
    if failed_candidates:
        failed = _run_record_example(failed_candidates[0])
    for record in run_records:
        if str(_dict(record.get("outcome")).get("status") or "") == (
            "provider_blocker"
        ):
            provider_blocker = _run_record_example(record)
            break
    return {
        "direct_repair": direct,
        "reflection_repair": reflection,
        "failed_repair": failed,
        "provider_blocker": provider_blocker,
    }


def _run_record_example(record: dict[str, Any]) -> dict[str, Any]:
    candidate = _dict(record.get("candidate"))
    outcome = _dict(record.get("outcome"))
    failure = _dict(record.get("failure"))
    strategy = _dict(record.get("strategy"))
    usage = _dict(record.get("usage"))
    artifacts = _dict(record.get("artifacts"))
    return {
        "status": "measured",
        "case_id": str(_dict(record.get("case")).get("case_id") or ""),
        "strategy": str(strategy.get("mode") or ""),
        "trial_index": _int(strategy.get("trial_index")),
        "run_id": str(record.get("run_id") or ""),
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "generator_family": str(candidate.get("generator_family") or ""),
        "generator_id": str(candidate.get("generator_id") or ""),
        "reflection_round": _int(candidate.get("reflection_round")),
        "outcome": str(outcome.get("status") or ""),
        "direct_success": bool(outcome.get("direct_success")),
        "reflection_recovered": bool(outcome.get("reflection_recovered")),
        "failure_layer": str(failure.get("layer") or ""),
        "failure_category": str(failure.get("category") or ""),
        "validation": _dict(record.get("validation")),
        "total_tokens": _int(usage.get("total_tokens")),
        "actual_cost_usd": _float(
            _dict(record.get("cost")).get("actual_cost_usd")
        ),
        "latency_ms": _float(_dict(record.get("timing")).get("latency_ms")),
        "patch_sha256": _optional_artifact_sha256(artifacts.get("patch")),
        "validation_sha256": _optional_artifact_sha256(
            artifacts.get("validation")
        ),
    }


def _optional_artifact_sha256(value: Any) -> str:
    path_text = str(value or "")
    if not path_text:
        return ""
    path = Path(path_text)
    return _sha256_file(path) if path.is_file() else ""


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


def _mapping_summary(value: Any) -> str:
    mapping = _dict(value)
    if not mapping:
        return "none"
    return ", ".join(
        f"{key}:{_int(mapping[key])}"
        for key in sorted(mapping)
    )


def _case_example_row(label: str, value: Any) -> str:
    example = _dict(value)
    status = str(example.get("status") or "pending")
    if status != "measured":
        outcome = str(example.get("reason") or "not_available")
        return (
            f"| {label} | `{_md(status)}` | n/a | n/a | n/a | "
            f"{_md(outcome)} |"
        )
    strategy = str(example.get("strategy") or "")
    trial_index = _int(example.get("trial_index"))
    generator = str(
        example.get("generator_id")
        or example.get("generator_family")
        or "none"
    )
    outcome = str(example.get("outcome") or "")
    failure_layer = str(example.get("failure_layer") or "")
    failure_category = str(example.get("failure_category") or "")
    outcome_or_failure = (
        f"{failure_layer}:{failure_category}"
        if failure_layer and failure_layer != "none"
        else outcome or "none"
    )
    return (
        f"| {label} | `measured` | `{_md(example.get('case_id') or '')}` | "
        f"`{_md(strategy)}/{trial_index}` | `{_md(generator)}` | "
        f"`{_md(outcome_or_failure)}` |"
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
