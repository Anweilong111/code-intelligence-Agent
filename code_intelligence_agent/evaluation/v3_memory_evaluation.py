from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from code_intelligence_agent.agents.evidence_memory import (
    compact_turn_history,
    memory_policy_hints,
    normalize_memory_record,
    promote_verified_repair_patterns,
    retrieve_evidence_memories,
)


def evaluate_v3_memory_generalization(dataset: dict[str, Any]) -> dict[str, Any]:
    cases = [_dict(item) for item in _list(dataset.get("cases"))]
    runs = []
    for case in cases:
        runs.append(_run_retrieval_case(case, mode="without_memory", enabled=False))
        runs.append(_run_retrieval_case(case, mode="structured_v2", enabled=True))
    metrics = {
        mode: _aggregate([item for item in runs if item["mode"] == mode])
        for mode in ("without_memory", "structured_v2")
    }
    strategy_confidence = _evaluate_strategy_confidence(
        _list(dataset.get("strategy_trials"))
    )
    long_session = _evaluate_long_session_summary(
        _dict(dataset.get("long_session"))
    )
    structured = metrics["structured_v2"]
    without = metrics["without_memory"]
    embedding_decision = {
        "status": "not_retained",
        "implemented": False,
        "evaluated": False,
        "measured_incremental_benefit": None,
        "reason": (
            "The controlled benchmark requires exact provenance, scope, conflict, and "
            "authority matching. No semantic-near retrieval subset currently demonstrates "
            "incremental benefit over structured_v2, so adding an embedding store would be "
            "unsupported complexity."
        ),
        "revisit_gate": (
            "Add a blind paraphrase/cross-repository subset and retain embeddings only if "
            "completion improves without stale, conflict, or advisory execution violations."
        ),
    }
    gates = {
        "structured_memory_completes_all_controlled_cases": (
            structured["task_completion_rate"] == 1.0
        ),
        "structured_memory_improves_over_no_memory": (
            structured["task_completion_rate"]
            > without["task_completion_rate"]
        ),
        "stale_memory_never_reused": structured["stale_reuse_count"] == 0,
        "conflicting_memory_never_becomes_execution_hint": (
            structured["conflict_execution_violation_count"] == 0
        ),
        "cross_repo_memory_is_advisory_only": (
            structured["advisory_execution_violation_count"] == 0
        ),
        "strategy_confidence_uses_success_and_failure_evidence": bool(
            strategy_confidence.get("status") == "pass"
        ),
        "long_session_summary_preserves_decision_facts": bool(
            long_session.get("status") == "pass"
        ),
        "embedding_store_not_added_without_uplift_evidence": (
            embedding_decision["status"] == "not_retained"
            and not embedding_decision["implemented"]
        ),
    }
    passed = all(gates.values())
    return {
        "schema_version": 1,
        "suite_name": str(
            dataset.get("suite_name") or "v3_memory_generalization"
        ),
        "status": "pass" if passed else "fail",
        "reason": (
            "all_memory_generalization_gates_passed"
            if passed
            else "one_or_more_memory_generalization_gates_failed"
        ),
        "case_count": len(cases),
        "run_count": len(runs),
        "metrics": metrics,
        "strategy_confidence": strategy_confidence,
        "long_session_summary": long_session,
        "embedding_retrieval_decision": embedding_decision,
        "acceptance_gates": gates,
        "runs": runs,
        "claim_boundary": (
            "This is a deterministic memory-policy benchmark. It measures scope, authority, "
            "conflict handling, and retrieval utility, not live-model reasoning quality."
        ),
    }


def render_v3_memory_markdown(payload: dict[str, Any]) -> str:
    metrics = _dict(payload.get("metrics"))
    lines = [
        "# V3 Memory Generalization Evaluation",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Reason: `{payload.get('reason')}`",
        f"- Cases: {payload.get('case_count')}",
        f"- Runs: {payload.get('run_count')}",
        "",
        "## Retrieval Ablation",
        "",
        "| Mode | Completion | Recall | Avg Selected | Stale Reuse | Conflict Execution | Advisory Execution | Avg Runtime (ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode in ("without_memory", "structured_v2"):
        item = _dict(metrics.get(mode))
        lines.append(
            f"| `{mode}` | {_float(item.get('task_completion_rate')):.4f} | "
            f"{_float(item.get('expected_record_recall')):.4f} | "
            f"{_float(item.get('average_selected_count')):.4f} | "
            f"{_int(item.get('stale_reuse_count'))} | "
            f"{_int(item.get('conflict_execution_violation_count'))} | "
            f"{_int(item.get('advisory_execution_violation_count'))} | "
            f"{_float(item.get('average_runtime_ms')):.4f} |"
        )
    strategy = _dict(payload.get("strategy_confidence"))
    long_session = _dict(payload.get("long_session_summary"))
    embedding = _dict(payload.get("embedding_retrieval_decision"))
    lines.extend(
        [
            "",
            "## Strategy Confidence",
            "",
            f"- Status: `{strategy.get('status')}`",
            f"- Evidence: {strategy.get('evidence_count', 0)} attempts across {strategy.get('source_repository_count', 0)} repositories",
            f"- Success / failure: {strategy.get('success_count', 0)} / {strategy.get('failure_count', 0)}",
            f"- Confidence: {strategy.get('confidence', 0)} (`{strategy.get('confidence_method', '')}`)",
            f"- Decision use: `{strategy.get('decision_use', '')}`",
            "",
            "## Long Session Summary",
            "",
            f"- Status: `{long_session.get('status')}`",
            f"- Compacted / retained: {long_session.get('compacted_turn_count', 0)} / {long_session.get('retained_turn_count', 0)}",
            f"- Preserved constraints: {', '.join(_list(long_session.get('active_constraints'))) or 'none'}",
            f"- Preserved blockers: {', '.join(_list(long_session.get('blockers'))) or 'none'}",
            "",
            "## Embedding Decision",
            "",
            f"- Status: `{embedding.get('status')}`",
            f"- Implemented: {str(bool(embedding.get('implemented'))).lower()}",
            f"- Reason: {embedding.get('reason')}",
            f"- Revisit gate: {embedding.get('revisit_gate')}",
            "",
            "## Acceptance Gates",
            "",
        ]
    )
    for name, passed in _dict(payload.get("acceptance_gates")).items():
        lines.append(f"- `{name}`: {'pass' if passed else 'fail'}")
    lines.extend(["", "## Claim Boundary", "", str(payload.get("claim_boundary"))])
    return "\n".join(lines) + "\n"


def write_v3_memory_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "phase6_memory_evaluation.json"
    markdown_path = root / "phase6_memory_evaluation.md"
    _write_text_lf(
        json_path,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    _write_text_lf(markdown_path, render_v3_memory_markdown(payload))
    return {"memory_json": str(json_path), "memory_markdown": str(markdown_path)}


def _write_text_lf(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _run_retrieval_case(
    case: dict[str, Any],
    *,
    mode: str,
    enabled: bool,
) -> dict[str, Any]:
    evidence = {
        "schema_version": 2,
        "records": [
            normalize_memory_record(_dict(item))
            for item in _list(case.get("records"))
        ],
    }
    started = time.perf_counter()
    retrieval = retrieve_evidence_memories(
        evidence,
        case.get("query") or "",
        repo=str(case.get("repo") or ""),
        repository_ref=str(case.get("repository_ref") or ""),
        session_id=str(case.get("session_id") or ""),
        top_k=_int(case.get("top_k", 10)),
        enabled=enabled,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    selected = set(_strings(retrieval.get("selected_memory_ids")))
    execution = set(_strings(retrieval.get("execution_hint_memory_ids")))
    advisory = set(_strings(retrieval.get("advisory_memory_ids")))
    audit_only = set(_strings(retrieval.get("audit_only_memory_ids")))
    expected_selected = set(_strings(case.get("expected_selected_ids")))
    expected_execution = set(_strings(case.get("expected_execution_ids")))
    expected_advisory = set(_strings(case.get("expected_advisory_ids")))
    expected_audit = set(_strings(case.get("expected_audit_ids")))
    excluded = set(_strings(case.get("expected_excluded_ids")))
    clarification = bool(
        _dict(retrieval.get("conflicts")).get("group_count", 0)
    )
    expected_clarification = bool(case.get("expected_requires_clarification", False))
    checks = {
        "selected": expected_selected.issubset(selected),
        "execution_authority": expected_execution.issubset(execution),
        "advisory_authority": expected_advisory.issubset(advisory),
        "audit_authority": expected_audit.issubset(audit_only),
        "excluded": not bool(selected & excluded),
        "clarification": clarification == expected_clarification,
    }
    required_count = (
        len(expected_selected)
        + len(expected_execution)
        + len(expected_advisory)
        + len(expected_audit)
    )
    recalled_count = (
        len(expected_selected & selected)
        + len(expected_execution & execution)
        + len(expected_advisory & advisory)
        + len(expected_audit & audit_only)
    )
    hints = memory_policy_hints(retrieval)
    return {
        "case_id": str(case.get("id") or ""),
        "mode": mode,
        "task_completed": all(checks.values()),
        "checks": checks,
        "selected_memory_ids": sorted(selected),
        "execution_hint_memory_ids": sorted(execution),
        "advisory_memory_ids": sorted(advisory),
        "audit_only_memory_ids": sorted(audit_only),
        "expected_selected_ids": sorted(expected_selected),
        "expected_excluded_ids": sorted(excluded),
        "required_record_count": required_count,
        "recalled_record_count": recalled_count,
        "stale_reuse_count": len(selected & excluded),
        "conflict_execution_violation_count": len(execution & expected_audit),
        "advisory_execution_violation_count": len(execution & expected_advisory),
        "requires_clarification": clarification,
        "policy_requires_clarification": bool(hints.get("requires_clarification")),
        "selected_count": _int(retrieval.get("selected_count")),
        "runtime_ms": round(elapsed_ms, 4),
        "discarded_counts": _dict(retrieval.get("discarded_counts")),
    }


def _aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(runs)
    required = sum(_int(item.get("required_record_count")) for item in runs)
    recalled = sum(_int(item.get("recalled_record_count")) for item in runs)
    return {
        "run_count": count,
        "task_completion_rate": _ratio(
            sum(bool(item.get("task_completed")) for item in runs), count
        ),
        "expected_record_recall": _ratio(recalled, required),
        "average_selected_count": _ratio(
            sum(_int(item.get("selected_count")) for item in runs), count
        ),
        "stale_reuse_count": sum(
            _int(item.get("stale_reuse_count")) for item in runs
        ),
        "conflict_execution_violation_count": sum(
            _int(item.get("conflict_execution_violation_count")) for item in runs
        ),
        "advisory_execution_violation_count": sum(
            _int(item.get("advisory_execution_violation_count")) for item in runs
        ),
        "average_runtime_ms": _ratio(
            sum(_float(item.get("runtime_ms")) for item in runs), count
        ),
    }


def _evaluate_strategy_confidence(trials: list[Any]) -> dict[str, Any]:
    patterns: list[dict[str, Any]] = []
    for index, value in enumerate(trials):
        trial = _dict(value)
        succeeded = str(trial.get("outcome") or "") == "success"
        record = {
            "memory_id": f"strategy_trial_{index}",
            "layer": "repair_memory",
            "kind": "patch_attempt",
            "status": "active",
            "source": "patch_validation",
            "repo": str(trial.get("repo") or ""),
            "repository_ref": str(trial.get("repository_ref") or ""),
            "session_id": str(trial.get("session_id") or f"session-{index}"),
            "evidence_path": "benchmark:patch_validation",
            "confidence": 1.0,
            "version_scope": "repo_commit",
            "validation": {
                "status": "verified" if succeeded else "failed",
                "authority": "sandbox_pytest",
            },
            "content": {
                "candidate_id": str(trial.get("candidate_id") or f"candidate-{index}"),
                "target_function": str(trial.get("target_function") or "pkg.load"),
                "failure_type": str(trial.get("failure_type") or "semantic_bug"),
                "generator": str(trial.get("generator") or "hybrid"),
                "diff_fingerprint": str(trial.get("diff_fingerprint") or f"diff-{index}"),
                "sandbox_status": "pass" if succeeded else "fail",
                "sandbox_verified": succeeded,
            },
        }
        patterns = promote_verified_repair_patterns(
            {"records": [record]},
            existing_records=patterns,
            now=f"2026-07-15T00:{index:02d}:00Z",
        )
    if not patterns:
        return {"status": "fail", "reason": "no_promoted_pattern"}
    pattern = patterns[0]
    before = _int(pattern.get("evidence_count"))
    duplicate = promote_verified_repair_patterns(
        {"records": []},
        existing_records=patterns,
        now="2026-07-15T01:00:00Z",
    )[0]
    passed = bool(
        _int(pattern.get("success_count"))
        + _int(pattern.get("failure_count"))
        == _int(pattern.get("evidence_count"))
        and _int(pattern.get("source_repository_count")) >= 2
        and 0.0 <= _float(pattern.get("confidence")) < 1.0
        and pattern.get("decision_use") == "advisory_only"
        and _int(duplicate.get("evidence_count")) == before
    )
    return {
        "status": "pass" if passed else "fail",
        "reason": (
            "strategy_confidence_calibrated_from_sandbox_outcomes"
            if passed
            else "strategy_confidence_calibration_failed"
        ),
        "evidence_count": pattern.get("evidence_count"),
        "success_count": pattern.get("success_count"),
        "failure_count": pattern.get("failure_count"),
        "source_repository_count": pattern.get("source_repository_count"),
        "confidence": pattern.get("confidence"),
        "confidence_method": pattern.get("confidence_method"),
        "decision_use": pattern.get("decision_use"),
        "duplicate_evidence_count_unchanged": _int(duplicate.get("evidence_count"))
        == before,
    }


def _evaluate_long_session_summary(config: dict[str, Any]) -> dict[str, Any]:
    turn_count = max(45, _int(config.get("turn_count", 45)))
    constraints = _strings(config.get("constraints"))
    strategies = _strings(config.get("repair_strategy_preferences"))
    fingerprints = _strings(config.get("failed_patch_fingerprints"))
    blockers = _strings(config.get("blockers"))
    turns = []
    for index in range(turn_count):
        turns.append(
            {
                "created_at": f"2026-07-15T00:{index:02d}:00Z",
                "intent": "continue_repair",
                "constraints": constraints if index == 1 else [],
                "repair_strategy_preferences": strategies if index == 2 else [],
                "loop": {
                    "act": {"action_id": "generate_patch"},
                    "verify": {
                        "status": "fail" if index == 3 else "pass",
                        "failed_patch_fingerprints": fingerprints
                        if index == 3
                        else [],
                        "blocker": blockers[0]
                        if blockers and index == 3
                        else "",
                    },
                },
            }
        )
    retained, summary, report = compact_turn_history(turns)
    checks = {
        "constraints": set(constraints).issubset(
            set(_strings(summary.get("active_constraints")))
        ),
        "strategies": set(strategies).issubset(
            set(_strings(summary.get("repair_strategy_preferences")))
        ),
        "failed_patch_fingerprints": set(fingerprints).issubset(
            set(_strings(summary.get("failed_patch_fingerprints")))
        ),
        "blockers": set(blockers).issubset(set(_strings(summary.get("blockers")))),
        "fingerprint": len(str(summary.get("summary_fingerprint") or "")) == 64,
    }
    return {
        "status": "pass" if all(checks.values()) else "fail",
        "reason": (
            "decision_relevant_facts_preserved_after_compaction"
            if all(checks.values())
            else "long_session_fact_loss_detected"
        ),
        "checks": checks,
        "compacted_turn_count": report.get("compacted_now"),
        "retained_turn_count": len(retained),
        "active_constraints": _list(summary.get("active_constraints")),
        "repair_strategy_preferences": _list(
            summary.get("repair_strategy_preferences")
        ),
        "failed_patch_fingerprints": _list(
            summary.get("failed_patch_fingerprints")
        ),
        "blockers": _list(summary.get("blockers")),
        "summary_fingerprint": summary.get("summary_fingerprint"),
    }


def _strings(value: Any) -> list[str]:
    return [str(item) for item in _list(value) if str(item or "")]


def _ratio(numerator: float, denominator: float) -> float:
    return round(float(numerator) / float(denominator), 6) if denominator else 0.0


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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate V3 memory scope, conflict, authority, and utility."
    )
    parser.add_argument("dataset")
    parser.add_argument("output_dir")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args(argv)
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    payload = evaluate_v3_memory_generalization(dataset)
    write_v3_memory_artifacts(payload, args.output_dir)
    print(
        json.dumps(payload, indent=2, ensure_ascii=False)
        if args.format == "json"
        else render_v3_memory_markdown(payload)
    )
    if args.require_pass and payload["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
