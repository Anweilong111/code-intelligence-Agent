from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any


EVIDENCE_MEMORY_SCHEMA_VERSION = 2
RETRIEVAL_ALGORITHM = "structured_relevance_v2"
MEMORY_LAYERS = (
    "working_memory",
    "session_memory",
    "repo_memory",
    "repair_memory",
    "cross_repo_pattern_memory",
)
DEFAULT_RETRIEVAL_TOP_K = 8
MAX_ACTIVE_RECORDS = 160
MAX_RECENT_TURNS = 24
COMPACTION_TRIGGER_TURNS = 40

_TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*|[\u4e00-\u9fff]{1,4}")
_LAYER_PRIOR = {
    "working_memory": 0.18,
    "session_memory": 0.17,
    "repair_memory": 0.16,
    "repo_memory": 0.14,
    "cross_repo_pattern_memory": 0.10,
}
_DECISION_USES = {"execution_hint", "advisory_only", "audit_only"}
_COMPLETED_SANDBOX_FAILURES = {"fail", "failed", "error", "rejected"}


def build_evidence_memory(
    memory: dict[str, Any],
    session: dict[str, Any],
    *,
    existing: dict[str, Any] | None = None,
    cross_repo_records: list[dict[str, Any]] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Build traceable records from the compact operational session state."""
    timestamp = now or _now()
    previous = _dict(existing)
    previous_records = {
        str(item.get("memory_id") or ""): item
        for item in (_dict(item) for item in _list(previous.get("records")))
        if str(item.get("memory_id") or "")
    }
    deleted_ids = {
        str(item)
        for item in [
            *_list(previous.get("deleted_memory_ids")),
            *_list(memory.get("deleted_memory_ids")),
        ]
        if str(item or "")
    }
    records: list[dict[str, Any]] = []

    def add(
        layer: str,
        kind: str,
        content: dict[str, Any],
        *,
        source: str,
        evidence_path: str,
        confidence: float,
        version_scope: str,
        validation_status: str = "observed",
        validation_authority: str = "agent_artifact",
    ) -> None:
        if not content:
            return
        record = make_memory_record(
            layer=layer,
            kind=kind,
            content=content,
            source=source,
            evidence_path=evidence_path,
            confidence=confidence,
            session=session,
            version_scope=version_scope,
            validation_status=validation_status,
            validation_authority=validation_authority,
            now=timestamp,
        )
        if record["memory_id"] in deleted_ids:
            return
        old = _dict(previous_records.get(record["memory_id"]))
        if old:
            record["created_at"] = str(old.get("created_at") or timestamp)
        records.append(record)

    memory_path = str(session.get("memory_path") or "in_memory:agent_memory")
    report_paths = _dict(session.get("report_paths"))
    latest_turn = _dict(_list(memory.get("turns"))[-1]) if _list(memory.get("turns")) else {}
    latest_loop = _dict(latest_turn.get("loop"))
    controller = _dict(memory.get("agent_controller_history"))
    add(
        "working_memory",
        "current_agent_state",
        {
            "status": str(memory.get("current_status") or session.get("status") or ""),
            "current_state": _dict(session.get("current_state")),
            "latest_intent": str(latest_turn.get("intent") or ""),
            "latest_action": str(
                _dict(latest_loop.get("act")).get("action_id")
                or _dict(controller.get("selected_action")).get("id")
                or ""
            ),
            "latest_verification": str(_dict(latest_loop.get("verify")).get("status") or ""),
            "latest_replan": str(_dict(latest_loop.get("replan")).get("next_action") or ""),
        },
        source="agent_controller_observation",
        evidence_path=memory_path,
        confidence=1.0,
        version_scope="session",
    )

    user_goal = str(session.get("user_goal") or memory.get("user_goal") or "")
    if user_goal:
        add(
            "session_memory",
            "user_goal",
            {"goal": user_goal},
            source="user_input",
            evidence_path=str(session.get("session_path") or memory_path),
            confidence=1.0,
            version_scope="session",
            validation_status="explicit",
            validation_authority="user",
        )
    for constraint in _unique_strings(_list(memory.get("constraints"))):
        add(
            "session_memory",
            "user_constraint",
            {"constraint": constraint},
            source="user_input",
            evidence_path=memory_path,
            confidence=1.0,
            version_scope="session",
            validation_status="explicit",
            validation_authority="user",
        )
    for strategy in _unique_strings(_list(memory.get("repair_strategy_preferences"))):
        add(
            "session_memory",
            "repair_strategy_preference",
            {"strategy": strategy},
            source="user_input",
            evidence_path=memory_path,
            confidence=1.0,
            version_scope="session",
            validation_status="explicit",
            validation_authority="user",
        )
    active_scope = str(memory.get("active_scope") or "")
    if active_scope:
        add(
            "session_memory",
            "active_scope",
            {"scope": active_scope},
            source="user_intent",
            evidence_path=memory_path,
            confidence=1.0,
            version_scope="session",
            validation_status="explicit",
            validation_authority="user",
        )
    conversation_summary = _dict(memory.get("conversation_summary"))
    if conversation_summary:
        add(
            "session_memory",
            "conversation_summary",
            conversation_summary,
            source="deterministic_context_compactor",
            evidence_path=memory_path,
            confidence=0.95,
            version_scope="session",
        )

    repo = _dict(memory.get("repo_profile"))
    if repo:
        add(
            "repo_memory",
            "repository_profile",
            repo,
            source="repository_understanding",
            evidence_path=_first_path(
                report_paths,
                "repository_structure_json",
                "repository_profile_json",
                fallback=memory_path,
            ),
            confidence=0.95,
            version_scope="repo_commit",
        )
    graph = _dict(memory.get("graph_memory"))
    if graph:
        add(
            "repo_memory",
            "program_graph_summary",
            graph,
            source="program_graph_builder",
            evidence_path=_first_path(
                report_paths,
                "program_graph_json",
                "repo_graph_json",
                fallback=memory_path,
            ),
            confidence=0.95,
            version_scope="repo_commit",
        )
    tests = _dict(memory.get("test_results"))
    if tests:
        add(
            "repo_memory",
            "test_environment_and_result",
            tests,
            source="repository_test_diagnosis",
            evidence_path=_first_path(
                report_paths,
                "repository_test_execution_plan_json",
                "repository_test_execution_result_json",
                fallback=memory_path,
            ),
            confidence=0.98 if str(tests.get("status") or "") else 0.85,
            version_scope="repo_commit",
            validation_status=str(tests.get("status") or "observed"),
            validation_authority="sandbox_pytest" if str(tests.get("status") or "") else "agent_artifact",
        )
    for index, item_value in enumerate(_list(memory.get("topk_suspicious_functions"))[:20], start=1):
        item = _dict(item_value)
        add(
            "repo_memory",
            "fault_localization_candidate",
            {"rank": index, **item},
            source="fault_localizer",
            evidence_path=_first_path(
                report_paths,
                "repository_test_fault_localization_json",
                "localization_attribution_json",
                fallback=memory_path,
            ),
            confidence=max(0.35, min(1.0, _float(item.get("final_score") or 0.7))),
            version_scope="repo_commit",
        )

    for item_value in _list(memory.get("patch_attempt_history")):
        item = _dict(item_value)
        verified = is_sandbox_verified_patch(item)
        add(
            "repair_memory",
            "patch_attempt",
            {
                "candidate_id": str(item.get("candidate_id") or ""),
                "target_function": str(item.get("target_function") or ""),
                "status": str(item.get("status") or ""),
                "sandbox_status": str(item.get("sandbox_status") or ""),
                "failure_type": str(item.get("failure_type") or ""),
                "diff_fingerprint": str(item.get("diff_fingerprint") or ""),
                "fixed_source_fingerprint": str(item.get("fixed_source_fingerprint") or ""),
                "generator": str(item.get("generator") or item.get("strategy") or ""),
                "sandbox_verified": verified,
            },
            source="patch_validation",
            evidence_path=_first_path(
                report_paths,
                "repository_test_patch_validation_json",
                fallback=memory_path,
            ),
            confidence=1.0 if verified else 0.92,
            version_scope="repo_commit",
            validation_status="verified" if verified else "failed",
            validation_authority="sandbox_pytest",
        )
    reflection = _dict(memory.get("reflection_trace"))
    if reflection:
        add(
            "repair_memory",
            "reflection_result",
            reflection,
            source="repair_reflection_loop",
            evidence_path=_first_path(
                report_paths,
                "reflection_trace_json",
                "repository_test_reflection_trace_json",
                fallback=memory_path,
            ),
            confidence=0.9,
            version_scope="repo_commit",
        )
    blockers = [_dict(item) for item in _list(memory.get("blocker_evolution"))]
    if blockers:
        add(
            "working_memory",
            "latest_blocker",
            blockers[-1],
            source=str(blockers[-1].get("source") or "agent_blocker_classifier"),
            evidence_path=memory_path,
            confidence=0.9,
            version_scope="repo_commit",
        )

    for item_value in _list(cross_repo_records):
        item = normalize_memory_record(_dict(item_value), now=timestamp)
        if not item or item.get("memory_id") in deleted_ids:
            continue
        validation = _dict(item.get("validation"))
        if (
            validation.get("status") != "verified"
            or validation.get("authority") != "sandbox_pytest"
        ):
            continue
        item["layer"] = "cross_repo_pattern_memory"
        item["version_scope"] = "global"
        records.append(item)

    generated_ids = {str(item.get("memory_id") or "") for item in records}
    current_repo = _repo_id(session)
    current_ref = str(session.get("repository_ref") or "")
    for old in previous_records.values():
        memory_id = str(old.get("memory_id") or "")
        if not memory_id or memory_id in generated_ids or memory_id in deleted_ids:
            continue
        old = normalize_memory_record(old, now=timestamp)
        if not old:
            continue
        if str(old.get("version_scope") or "") == "global":
            old["status"] = "active"
            old["stale_reason"] = ""
        elif str(old.get("version_scope") or "") == "repo_commit" and (
            str(old.get("repo") or "") != current_repo
            or bool(current_ref and str(old.get("repository_ref") or "") != current_ref)
        ):
            old["status"] = "stale"
            old["stale_reason"] = "repository_version_changed"
        else:
            old["status"] = "superseded"
            old["stale_reason"] = "source_state_no_longer_active"
        old["updated_at"] = timestamp
        records.append(old)

    records = _deduplicate_records(records)
    active = [item for item in records if item.get("status") == "active"]
    inactive = [item for item in records if item.get("status") != "active"][-40:]
    records = [*active[-MAX_ACTIVE_RECORDS:], *inactive]
    return {
        "schema_version": EVIDENCE_MEMORY_SCHEMA_VERSION,
        "retrieval_algorithm": RETRIEVAL_ALGORITHM,
        "updated_at": timestamp,
        "repo": current_repo,
        "repository_ref": current_ref,
        "session_id": str(session.get("session_id") or memory.get("session_id") or ""),
        "record_count": len(records),
        "active_record_count": sum(1 for item in records if item.get("status") == "active"),
        "stale_record_count": sum(1 for item in records if item.get("status") == "stale"),
        "deleted_memory_ids": sorted(deleted_ids),
        "records": records,
    }


def make_memory_record(
    *,
    layer: str,
    kind: str,
    content: dict[str, Any],
    source: str,
    evidence_path: str,
    confidence: float,
    session: dict[str, Any],
    version_scope: str,
    validation_status: str,
    validation_authority: str,
    now: str | None = None,
) -> dict[str, Any]:
    timestamp = now or _now()
    repo = _repo_id(session)
    repository_ref = str(session.get("repository_ref") or "")
    session_id = str(session.get("session_id") or "")
    identity = {
        "layer": layer,
        "kind": kind,
        "content": content,
        "scope": version_scope,
        "repo": repo if version_scope != "global" else "",
        "repository_ref": repository_ref if version_scope == "repo_commit" else "",
        "session_id": session_id if version_scope == "session" else "",
    }
    fingerprint = _fingerprint(identity)
    record = {
        "memory_id": f"mem_{fingerprint[:20]}",
        "fingerprint": fingerprint,
        "schema_version": EVIDENCE_MEMORY_SCHEMA_VERSION,
        "layer": layer,
        "kind": kind,
        "status": "active",
        "source": str(source or "unknown"),
        "created_at": timestamp,
        "updated_at": timestamp,
        "repo": repo,
        "repository_ref": repository_ref,
        "session_id": session_id,
        "evidence_path": str(evidence_path or "in_memory:unknown"),
        "confidence": round(max(0.0, min(1.0, float(confidence))), 4),
        "version_scope": version_scope,
        "validation": {
            "status": str(validation_status or "observed"),
            "authority": str(validation_authority or "agent_artifact"),
        },
        "summary": _record_summary(kind, content),
        "content": content,
        "keywords": sorted(_tokens({"kind": kind, "content": content}))[:40],
        "expires_at": "",
        "stale_reason": "",
    }
    record.update(_memory_authority(record))
    return record


def normalize_memory_record(record: dict[str, Any], *, now: str | None = None) -> dict[str, Any]:
    if not record:
        return {}
    normalized = dict(record)
    timestamp = now or _now()
    normalized.setdefault("schema_version", EVIDENCE_MEMORY_SCHEMA_VERSION)
    normalized.setdefault("created_at", timestamp)
    normalized.setdefault("updated_at", timestamp)
    normalized.setdefault("status", "active")
    normalized.setdefault("confidence", 0.5)
    normalized.setdefault("source", "unknown")
    normalized.setdefault("evidence_path", "in_memory:unknown")
    normalized.setdefault("version_scope", "repo_commit")
    normalized.setdefault("validation", {"status": "observed", "authority": "agent_artifact"})
    normalized.setdefault("content", {})
    normalized.setdefault("kind", "memory_fact")
    normalized.setdefault("layer", "session_memory")
    normalized.setdefault("summary", _record_summary(str(normalized["kind"]), _dict(normalized["content"])))
    normalized.setdefault("keywords", sorted(_tokens(normalized))[:40])
    fingerprint = str(normalized.get("fingerprint") or _fingerprint(normalized))
    normalized["fingerprint"] = fingerprint
    normalized.setdefault("memory_id", f"mem_{fingerprint[:20]}")
    authority = _memory_authority(normalized)
    normalized.setdefault("trust_class", authority["trust_class"])
    normalized.setdefault("decision_use", authority["decision_use"])
    if normalized.get("decision_use") not in _DECISION_USES:
        normalized["decision_use"] = "audit_only"
    normalized.setdefault("conflict_status", "none")
    normalized.setdefault("conflict_group", "")
    normalized.setdefault("conflicts_with", [])
    return normalized


def retrieve_evidence_memories(
    evidence_memory: dict[str, Any],
    query: str | dict[str, Any],
    *,
    repo: str,
    repository_ref: str,
    session_id: str,
    top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    enabled: bool = True,
    now: str | None = None,
) -> dict[str, Any]:
    timestamp = now or _now()
    query_payload = query if isinstance(query, dict) else {"query": str(query or "")}
    query_tokens = _tokens(query_payload)
    if not enabled:
        return {
            "schema_version": EVIDENCE_MEMORY_SCHEMA_VERSION,
            "status": "disabled",
            "reason": "memory_retrieval_disabled_for_ablation",
            "algorithm": RETRIEVAL_ALGORITHM,
            "top_k": max(0, int(top_k)),
            "candidate_count": 0,
            "selected_count": 0,
            "selected_memory_ids": [],
            "execution_hint_memory_ids": [],
            "advisory_memory_ids": [],
            "audit_only_memory_ids": [],
            "decision_use_counts": {},
            "conflicts": {
                "status": "clear",
                "group_count": 0,
                "record_count": 0,
                "groups": [],
                "conflicted_memory_ids": [],
            },
            "records": [],
            "discarded_counts": {},
        }
    discarded: Counter[str] = Counter()
    scored: list[tuple[float, dict[str, Any], list[str]]] = []
    for item_value in _list(_dict(evidence_memory).get("records")):
        item = normalize_memory_record(_dict(item_value), now=timestamp)
        reason = _memory_filter_reason(
            item,
            repo=repo,
            repository_ref=repository_ref,
            session_id=session_id,
            now=timestamp,
        )
        if reason:
            discarded[reason] += 1
            continue
        score, reasons = _memory_relevance_score(item, query_tokens, query_payload)
        scored.append((score, item, reasons))
    scored.sort(
        key=lambda row: (
            -row[0],
            -_float(row[1].get("confidence")),
            str(row[1].get("memory_id") or ""),
        )
    )
    conflict_report = _annotate_memory_conflicts([row[1] for row in scored])
    limit = max(0, min(20, int(top_k)))
    selected = []
    for score, item, reasons in scored[:limit]:
        selected.append(
            {
                key: item.get(key)
                for key in (
                    "memory_id",
                    "layer",
                    "kind",
                    "summary",
                    "content",
                    "source",
                    "created_at",
                    "repo",
                    "repository_ref",
                    "evidence_path",
                    "confidence",
                    "validation",
                    "trust_class",
                    "decision_use",
                    "conflict_status",
                    "conflict_group",
                    "conflicts_with",
                )
            }
            | {
                "retrieval_score": round(score, 6),
                "retrieval_reason": reasons,
            }
        )
    decision_counts = Counter(
        str(item.get("decision_use") or "audit_only") for item in selected
    )
    return {
        "schema_version": EVIDENCE_MEMORY_SCHEMA_VERSION,
        "status": "pass",
        "reason": "top_k_structured_memory_retrieved",
        "algorithm": RETRIEVAL_ALGORITHM,
        "query": query_payload,
        "top_k": limit,
        "candidate_count": len(scored),
        "selected_count": len(selected),
        "selected_memory_ids": [str(item.get("memory_id") or "") for item in selected],
        "selected_layers": sorted({str(item.get("layer") or "") for item in selected}),
        "execution_hint_memory_ids": [
            str(item.get("memory_id") or "")
            for item in selected
            if item.get("decision_use") == "execution_hint"
            and item.get("conflict_status") == "none"
        ],
        "advisory_memory_ids": [
            str(item.get("memory_id") or "")
            for item in selected
            if item.get("decision_use") == "advisory_only"
        ],
        "audit_only_memory_ids": [
            str(item.get("memory_id") or "")
            for item in selected
            if item.get("decision_use") == "audit_only"
            or item.get("conflict_status") != "none"
        ],
        "decision_use_counts": dict(sorted(decision_counts.items())),
        "conflicts": conflict_report,
        "records": selected,
        "discarded_counts": dict(sorted(discarded.items())),
    }


def memory_policy_hints(retrieval: dict[str, Any]) -> dict[str, Any]:
    constraints: list[str] = []
    strategies: list[str] = []
    failed_fingerprints: list[str] = []
    test_commands: list[str] = []
    blockers: list[str] = []
    verified_patterns: list[dict[str, Any]] = []
    advisory_patterns: list[dict[str, Any]] = []
    excluded_ids: list[str] = []
    source_ids: dict[str, list[str]] = {}
    for item_value in _list(_dict(retrieval).get("records")):
        item = _dict(item_value)
        content = _dict(item.get("content"))
        memory_id = str(item.get("memory_id") or "")
        kind = str(item.get("kind") or "")
        decision_use = str(item.get("decision_use") or "audit_only")
        conflicting = str(item.get("conflict_status") or "none") != "none"
        if kind == "verified_repair_pattern":
            advisory_patterns.append(content)
            source_ids.setdefault("advisory_patterns", []).append(memory_id)
        if decision_use != "execution_hint" or conflicting:
            excluded_ids.append(memory_id)
            continue
        values: list[tuple[str, str]] = []
        if kind == "user_constraint":
            values.append(("constraints", str(content.get("constraint") or "")))
        if kind == "repair_strategy_preference":
            values.append(("strategies", str(content.get("strategy") or "")))
        if kind == "patch_attempt" and not bool(content.get("sandbox_verified")):
            values.append(
                (
                    "failed_patch_fingerprints",
                    str(content.get("diff_fingerprint") or ""),
                )
            )
        if kind == "test_environment_and_result":
            values.append(("test_commands", str(content.get("command") or "")))
        if kind == "latest_blocker":
            values.append(("blockers", str(content.get("blocker") or "")))
        if kind == "verified_repair_pattern":
            verified_patterns.append(content)
            source_ids.setdefault("verified_patterns", []).append(memory_id)
        for bucket, value in values:
            if not value:
                continue
            {
                "constraints": constraints,
                "strategies": strategies,
                "failed_patch_fingerprints": failed_fingerprints,
                "test_commands": test_commands,
                "blockers": blockers,
            }[bucket].append(value)
            source_ids.setdefault(bucket, []).append(memory_id)
    return {
        "constraints": _unique_strings(constraints),
        "repair_strategy_preferences": _unique_strings(strategies),
        "failed_patch_fingerprints": _unique_strings(failed_fingerprints),
        "test_commands": _unique_strings(test_commands),
        "blockers": _unique_strings(blockers),
        "verified_repair_patterns": verified_patterns,
        "advisory_repair_patterns": advisory_patterns,
        "requires_clarification": bool(
            _list(_dict(_dict(retrieval).get("conflicts")).get("groups"))
        ),
        "conflict_groups": _list(
            _dict(_dict(retrieval).get("conflicts")).get("groups")
        ),
        "excluded_from_execution_memory_ids": _unique_strings(excluded_ids),
        "source_memory_ids": {
            key: _unique_strings(values) for key, values in sorted(source_ids.items())
        },
    }


def promote_verified_repair_patterns(
    evidence_memory: dict[str, Any],
    *,
    existing_records: list[dict[str, Any]] | None = None,
    now: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate sandbox-observed strategy outcomes as advisory cross-repo memory."""
    timestamp = now or _now()
    promoted = [
        normalize_memory_record(_dict(item), now=timestamp)
        for item in _list(existing_records)
    ]
    by_fingerprint: dict[str, dict[str, Any]] = {}
    for item in promoted:
        fingerprint = str(item.get("fingerprint") or "")
        content = _dict(item.get("content"))
        if (
            item.get("kind") == "verified_repair_pattern"
            and content.get("failure_type")
            and (content.get("target_shape") or content.get("target_function"))
            and content.get("generator")
        ):
            pattern_key = {
                "failure_type": str(content.get("failure_type")),
                "target_shape": str(
                    content.get("target_shape")
                    or _target_shape(str(content.get("target_function") or ""))
                ),
                "generator": str(content.get("generator")),
            }
            fingerprint = _fingerprint(pattern_key)
            item["fingerprint"] = fingerprint
            item["memory_id"] = f"pattern_{fingerprint[:20]}"
            item["schema_version"] = EVIDENCE_MEMORY_SCHEMA_VERSION
            item["decision_use"] = "advisory_only"
        if fingerprint:
            by_fingerprint[fingerprint] = item
    for item_value in _list(_dict(evidence_memory).get("records")):
        item = _dict(item_value)
        validation = _dict(item.get("validation"))
        content = _dict(item.get("content"))
        sandbox_status = str(content.get("sandbox_status") or "").lower()
        if item.get("layer") != "repair_memory" or item.get("kind") != "patch_attempt":
            continue
        if validation.get("authority") != "sandbox_pytest":
            continue
        succeeded = bool(
            validation.get("status") == "verified"
            and bool(content.get("sandbox_verified"))
        )
        failed = bool(
            not succeeded
            and (
                validation.get("status") == "failed"
                or sandbox_status in _COMPLETED_SANDBOX_FAILURES
            )
        )
        if not succeeded and not failed:
            continue
        pattern_key = {
            "failure_type": str(content.get("failure_type") or "unknown"),
            "target_shape": _target_shape(str(content.get("target_function") or "")),
            "generator": str(content.get("generator") or "unknown"),
        }
        pattern_fingerprint = _fingerprint(pattern_key)
        existing = _dict(by_fingerprint.get(pattern_fingerprint))
        source_repo = str(item.get("repo") or "")
        source_ref = str(item.get("repository_ref") or "")
        attempts = [_dict(value) for value in _list(existing.get("source_attempts"))]
        attempt = {
            "repo": source_repo,
            "repository_ref": source_ref,
            "session_id": str(item.get("session_id") or ""),
            "candidate_id": str(content.get("candidate_id") or ""),
            "diff_fingerprint": str(content.get("diff_fingerprint") or ""),
            "outcome": "success" if succeeded else "failure",
        }
        attempt_id = _fingerprint(attempt)
        known_attempt_ids = {
            str(value.get("attempt_id") or "") for value in attempts
        }
        new_source = attempt_id not in known_attempt_ids
        if new_source:
            attempts.append({"attempt_id": attempt_id, **attempt})
        success_count = sum(value.get("outcome") == "success" for value in attempts)
        failure_count = sum(value.get("outcome") == "failure" for value in attempts)
        source_versions = _unique_dicts(
            [
                {
                    "repo": str(value.get("repo") or ""),
                    "repository_ref": str(value.get("repository_ref") or ""),
                }
                for value in attempts
            ]
        )
        source_repo_count = len(
            {str(value.get("repo") or "") for value in attempts if value.get("repo")}
        )
        pattern_content = {
            **pattern_key,
            "repair_outcome": "sandbox_observed",
            "success_count": success_count,
            "failure_count": failure_count,
            "source_repository_count": source_repo_count,
        }
        confidence = _wilson_lower_bound(success_count, success_count + failure_count)
        record = {
            "memory_id": f"pattern_{pattern_fingerprint[:20]}",
            "fingerprint": pattern_fingerprint,
            "schema_version": EVIDENCE_MEMORY_SCHEMA_VERSION,
            "layer": "cross_repo_pattern_memory",
            "kind": "verified_repair_pattern",
            "status": "active",
            "source": "sandbox_verified_repair_promotion",
            "created_at": str(existing.get("created_at") or timestamp),
            "updated_at": (
                timestamp
                if new_source or not existing
                else str(existing.get("updated_at") or timestamp)
            ),
            "repo": source_repo,
            "repository_ref": source_ref,
            "session_id": str(item.get("session_id") or ""),
            "evidence_path": str(item.get("evidence_path") or "in_memory:patch_validation"),
            "confidence": confidence,
            "version_scope": "global",
            "validation": {
                "status": "verified" if success_count else "observed_failure_only",
                "authority": "sandbox_pytest",
            },
            "summary": _record_summary("verified_repair_pattern", pattern_content),
            "content": pattern_content,
            "keywords": sorted(_tokens(pattern_content))[:40],
            "expires_at": "",
            "stale_reason": "",
            "trust_class": "cross_repo_verified",
            "decision_use": "advisory_only",
            "conflict_status": "none",
            "conflict_group": "",
            "conflicts_with": [],
            "evidence_count": len(attempts),
            "success_count": success_count,
            "failure_count": failure_count,
            "source_repository_count": source_repo_count,
            "confidence_method": "wilson_lower_bound_95pct",
            "source_versions": source_versions[-20:],
            "source_attempts": attempts[-100:],
        }
        by_fingerprint[pattern_fingerprint] = record
    return sorted(
        [
            item
            for item in by_fingerprint.values()
            if _int(item.get("success_count")) > 0
            or _dict(item.get("validation")).get("status") == "verified"
        ],
        key=lambda item: str(item.get("memory_id") or ""),
    )


def compact_turn_history(
    turns: list[Any],
    existing_summary: dict[str, Any] | None = None,
    *,
    trigger: int = COMPACTION_TRIGGER_TURNS,
    keep_recent: int = MAX_RECENT_TURNS,
    now: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    normalized = [_dict(item) for item in turns]
    summary = dict(_dict(existing_summary))
    if len(normalized) <= max(trigger, keep_recent):
        return normalized, summary, {
            "status": "not_needed",
            "compacted_now": 0,
            "retained_turn_count": len(normalized),
            "total_compacted_turn_count": _int(summary.get("compacted_turn_count")),
        }
    compact_count = len(normalized) - keep_recent
    compacted = normalized[:compact_count]
    retained = normalized[compact_count:]
    intent_counts = Counter(_string(item.get("intent") or "unknown") for item in compacted)
    action_counts = Counter(
        _string(_dict(_dict(item.get("loop")).get("act")).get("action_id") or "unknown")
        for item in compacted
    )
    previous_intents = Counter(_dict(summary.get("intent_counts")))
    previous_actions = Counter(_dict(summary.get("action_counts")))
    previous_intents.update(intent_counts)
    previous_actions.update(action_counts)
    compacted_facts = _summary_facts(compacted)
    active_constraints = _unique_strings(
        [
            *_list(summary.get("active_constraints")),
            *_list(compacted_facts.get("constraints")),
        ]
    )[-20:]
    strategy_preferences = _unique_strings(
        [
            *_list(summary.get("repair_strategy_preferences")),
            *_list(compacted_facts.get("repair_strategy_preferences")),
        ]
    )[-12:]
    failed_fingerprints = _unique_strings(
        [
            *_list(summary.get("failed_patch_fingerprints")),
            *_list(compacted_facts.get("failed_patch_fingerprints")),
        ]
    )[-20:]
    blockers = _unique_strings(
        [
            *_list(summary.get("blockers")),
            *_list(compacted_facts.get("blockers")),
        ]
    )[-20:]
    verification_counts = Counter(_dict(summary.get("verification_counts")))
    verification_counts.update(_dict(compacted_facts.get("verification_counts")))
    summary = {
        "schema_version": 2,
        "updated_at": now or _now(),
        "compacted_turn_count": _int(summary.get("compacted_turn_count")) + compact_count,
        "intent_counts": dict(sorted(previous_intents.items())),
        "action_counts": dict(sorted(previous_actions.items())),
        "first_compacted_at": str(
            summary.get("first_compacted_at")
            or _dict(compacted[0]).get("created_at")
            or ""
        ),
        "last_compacted_at": str(_dict(compacted[-1]).get("created_at") or ""),
        "last_compacted_intent": str(_dict(compacted[-1]).get("intent") or ""),
        "last_compacted_action": str(
            _dict(_dict(_dict(compacted[-1]).get("loop")).get("act")).get("action_id")
            or ""
        ),
        "active_constraints": active_constraints,
        "repair_strategy_preferences": strategy_preferences,
        "failed_patch_fingerprints": failed_fingerprints,
        "blockers": blockers,
        "verification_counts": dict(sorted(verification_counts.items())),
    }
    summary["summary_fingerprint"] = _fingerprint(
        {
            key: summary[key]
            for key in (
                "compacted_turn_count",
                "intent_counts",
                "action_counts",
                "active_constraints",
                "repair_strategy_preferences",
                "failed_patch_fingerprints",
                "blockers",
                "verification_counts",
            )
        }
    )
    return retained, summary, {
        "status": "compacted",
        "compacted_now": compact_count,
        "retained_turn_count": len(retained),
        "total_compacted_turn_count": summary["compacted_turn_count"],
    }


def is_sandbox_verified_patch(item: dict[str, Any]) -> bool:
    status = str(item.get("status") or "").lower()
    sandbox_status = str(item.get("sandbox_status") or "").lower()
    passed = bool(item.get("passed") or item.get("success"))
    return bool(
        passed
        and status in {"pass", "passed", "success", "verified"}
        and sandbox_status in {"pass", "passed", "success", "verified"}
    )


def total_turn_count(memory: dict[str, Any]) -> int:
    return _int(_dict(memory.get("conversation_summary")).get("compacted_turn_count")) + len(
        _list(memory.get("turns"))
    )


def _memory_authority(item: dict[str, Any]) -> dict[str, str]:
    layer = str(item.get("layer") or "")
    kind = str(item.get("kind") or "")
    source = str(item.get("source") or "")
    validation = _dict(item.get("validation"))
    authority = str(validation.get("authority") or "")
    if layer == "cross_repo_pattern_memory":
        return {
            "trust_class": "cross_repo_verified",
            "decision_use": "advisory_only",
        }
    if authority == "user" and source in {"user_input", "user_intent"}:
        return {"trust_class": "trusted_user", "decision_use": "execution_hint"}
    if authority == "sandbox_pytest" and kind in {
        "patch_attempt",
        "test_environment_and_result",
    }:
        return {
            "trust_class": "verified_runtime",
            "decision_use": "execution_hint",
        }
    if kind == "patch_attempt" and source == "patch_validation":
        return {
            "trust_class": "agent_observed_current_repo",
            "decision_use": "execution_hint",
        }
    if kind in {"latest_blocker", "current_agent_state"}:
        return {
            "trust_class": "agent_derived",
            "decision_use": "execution_hint",
        }
    return {"trust_class": "agent_derived", "decision_use": "advisory_only"}


def _annotate_memory_conflicts(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for item in records:
        item["conflict_status"] = "none"
        item["conflict_group"] = ""
        item["conflicts_with"] = []
        signature = _directive_signature(item)
        if not signature:
            continue
        group, value = signature
        grouped.setdefault(group, {}).setdefault(value, []).append(item)
    conflicts = []
    conflicted_ids: set[str] = set()
    for group, values in sorted(grouped.items()):
        if len(values) <= 1:
            continue
        group_ids = sorted(
            {
                str(item.get("memory_id") or "")
                for bucket in values.values()
                for item in bucket
                if item.get("memory_id")
            }
        )
        for bucket in values.values():
            for item in bucket:
                memory_id = str(item.get("memory_id") or "")
                item["conflict_status"] = "conflicting"
                item["conflict_group"] = group
                item["conflicts_with"] = [
                    value for value in group_ids if value != memory_id
                ]
                item["decision_use"] = "audit_only"
                conflicted_ids.add(memory_id)
        conflicts.append(
            {
                "group": group,
                "values": sorted(values),
                "memory_ids": group_ids,
                "resolution": "clarification_required",
            }
        )
    return {
        "status": "conflict" if conflicts else "clear",
        "group_count": len(conflicts),
        "record_count": len(conflicted_ids),
        "groups": conflicts,
        "conflicted_memory_ids": sorted(conflicted_ids),
    }


def _directive_signature(item: dict[str, Any]) -> tuple[str, str] | None:
    kind = str(item.get("kind") or "")
    if kind not in {"user_constraint", "repair_strategy_preference"}:
        return None
    content = _dict(item.get("content"))
    explicit_group = _normalized_directive(str(content.get("conflict_key") or ""))
    explicit_value = _normalized_directive(str(content.get("value") or ""))
    if explicit_group and explicit_value:
        return explicit_group, explicit_value
    if kind == "repair_strategy_preference":
        strategy = _normalized_directive(str(content.get("strategy") or ""))
        return ("repair_strategy", strategy) if strategy else None
    text = _normalized_directive(str(content.get("constraint") or ""))
    if not text:
        return None
    if "public api" in text or "public signature" in text:
        mutation_allowed = not any(
            token in text
            for token in (
                "do not",
                "dont",
                "never",
                "preserve",
                "keep",
                "avoid",
            )
        )
        return "public_api_mutation", "allow" if mutation_allowed else "deny"
    negative = re.match(r"^(?:do not|dont|never|avoid|forbid) (.+)$", text)
    if negative:
        return f"constraint:{negative.group(1)}", "deny"
    positive = re.match(r"^(?:allow|require|must|use|prefer) (.+)$", text)
    if positive:
        return f"constraint:{positive.group(1)}", "allow"
    return None


def _normalized_directive(value: str) -> str:
    text = re.sub(r"[^a-z0-9_. -]+", " ", value.lower())
    return re.sub(r"\s+", " ", text).strip()


def _memory_filter_reason(
    item: dict[str, Any],
    *,
    repo: str,
    repository_ref: str,
    session_id: str,
    now: str,
) -> str:
    if str(item.get("status") or "active") != "active":
        return f"status_{item.get('status')}"
    if str(item.get("layer") or "") == "cross_repo_pattern_memory":
        validation = _dict(item.get("validation"))
        if (
            validation.get("status") != "verified"
            or validation.get("authority") != "sandbox_pytest"
        ):
            return "unverified_cross_repo_pattern"
    expires_at = str(item.get("expires_at") or "")
    if expires_at and expires_at <= now:
        return "expired"
    scope = str(item.get("version_scope") or "repo_commit")
    if scope == "session" and str(item.get("session_id") or "") != session_id:
        return "other_session"
    if scope == "repo_commit":
        if str(item.get("repo") or "") != repo:
            return "other_repository"
        item_ref = str(item.get("repository_ref") or "")
        if repository_ref and item_ref != repository_ref:
            return "stale_repository_version"
    return ""


def _memory_relevance_score(
    item: dict[str, Any],
    query_tokens: set[str],
    query_payload: dict[str, Any],
) -> tuple[float, list[str]]:
    item_tokens = set(str(token).lower() for token in _list(item.get("keywords"))) or _tokens(item)
    overlap = len(query_tokens & item_tokens) / max(1, len(query_tokens))
    layer = str(item.get("layer") or "")
    kind = str(item.get("kind") or "")
    confidence = _float(item.get("confidence"))
    score = 0.35 * confidence + 0.35 * overlap + _LAYER_PRIOR.get(layer, 0.05)
    reasons = [f"confidence={confidence:.2f}", f"token_overlap={overlap:.3f}", f"layer_prior={_LAYER_PRIOR.get(layer, 0.05):.2f}"]
    query_text = json.dumps(query_payload, ensure_ascii=False).lower()
    if any(term in query_text for term in ("patch", "repair", "failure", "修复", "失败", "blocker")) and layer == "repair_memory":
        score += 0.18
        reasons.append("repair_context_match")
    if any(term in query_text for term in ("constraint", "public api", "约束", "不要")) and kind == "user_constraint":
        score += 0.22
        reasons.append("user_constraint_match")
    if any(term in query_text for term in ("test", "pytest", "traceback", "测试")) and kind == "test_environment_and_result":
        score += 0.18
        reasons.append("test_evidence_match")
    if any(term in query_text for term in ("plan", "action", "stage", "规划", "下一步")) and layer == "working_memory":
        score += 0.16
        reasons.append("current_state_match")
    if str(item.get("evidence_path") or ""):
        score += 0.03
        reasons.append("traceable_evidence")
    return min(1.5, score), reasons


def _deduplicate_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item_value in records:
        item = normalize_memory_record(_dict(item_value))
        memory_id = str(item.get("memory_id") or "")
        if not memory_id:
            continue
        current = _dict(merged.get(memory_id))
        if not current or str(item.get("updated_at") or "") >= str(current.get("updated_at") or ""):
            merged[memory_id] = item
    return sorted(merged.values(), key=lambda item: (str(item.get("status") or ""), str(item.get("memory_id") or "")))


def _record_summary(kind: str, content: dict[str, Any]) -> str:
    compact = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(compact) > 360:
        compact = compact[:357] + "..."
    return f"{kind}: {compact}"


def _first_path(mapping: dict[str, Any], *keys: str, fallback: str) -> str:
    for key in keys:
        value = str(mapping.get(key) or "")
        if value:
            return value
    return fallback


def _target_shape(function_name: str) -> str:
    parts = [item for item in function_name.split(".") if item]
    return parts[-1] if parts else "unknown_function"


def _repo_id(session: dict[str, Any]) -> str:
    return str(session.get("repo") or session.get("repo_spec") or "")


def _tokens(value: Any) -> set[str]:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True) if not isinstance(value, str) else value
    return {match.group(0).lower() for match in _TOKEN_PATTERN.finditer(text)}


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _unique_strings(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(item) for item in values if str(item or "")))


def _unique_dicts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for value in values:
        unique[_fingerprint(value)] = value
    return list(unique.values())


def _wilson_lower_bound(successes: int, total: int, *, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    proportion = successes / total
    denominator = 1 + (z * z / total)
    centre = proportion + (z * z / (2 * total))
    margin = z * math.sqrt(
        (proportion * (1 - proportion) / total) + (z * z / (4 * total * total))
    )
    return round(max(0.0, (centre - margin) / denominator), 4)


def _summary_facts(turns: list[dict[str, Any]]) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "constraints": [],
        "repair_strategy_preferences": [],
        "failed_patch_fingerprints": [],
        "blockers": [],
        "verification_counts": Counter(),
    }

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            status = str(value.get("status") or "").lower()
            for child_key, child_value in value.items():
                normalized_key = str(child_key).lower()
                if normalized_key in {"constraint", "constraints"}:
                    facts["constraints"].extend(
                        _list(child_value) if isinstance(child_value, list) else [child_value]
                    )
                elif normalized_key in {
                    "repair_strategy_preference",
                    "repair_strategy_preferences",
                }:
                    facts["repair_strategy_preferences"].extend(
                        _list(child_value) if isinstance(child_value, list) else [child_value]
                    )
                elif normalized_key in {
                    "failed_patch_fingerprint",
                    "failed_patch_fingerprints",
                }:
                    facts["failed_patch_fingerprints"].extend(
                        _list(child_value) if isinstance(child_value, list) else [child_value]
                    )
                elif normalized_key == "diff_fingerprint" and status in {
                    "fail",
                    "failed",
                    "error",
                    "rejected",
                }:
                    facts["failed_patch_fingerprints"].append(child_value)
                elif normalized_key in {"blocker", "primary_blocker"}:
                    facts["blockers"].append(child_value)
                elif normalized_key in {"verification_status", "verify_status"}:
                    facts["verification_counts"][str(child_value or "unknown")] += 1
                visit(child_value, normalized_key)
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif key == "status" and str(value or "") in {"pass", "fail", "blocked"}:
            facts["verification_counts"][str(value)] += 1

    for turn in turns:
        visit(turn)
    for key in (
        "constraints",
        "repair_strategy_preferences",
        "failed_patch_fingerprints",
        "blockers",
    ):
        facts[key] = _unique_strings(facts[key])
    facts["verification_counts"] = dict(facts["verification_counts"])
    return facts


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string(value: Any) -> str:
    return str(value or "")


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
