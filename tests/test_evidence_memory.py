from __future__ import annotations

from code_intelligence_agent.agents.evidence_memory import (
    MEMORY_LAYERS,
    build_evidence_memory,
    compact_turn_history,
    memory_policy_hints,
    promote_verified_repair_patterns,
    retrieve_evidence_memories,
    total_turn_count,
)


NOW = "2026-07-14T00:00:00Z"


def test_evidence_memory_records_have_provenance_and_five_layers():
    memory = _memory()
    session = _session()

    evidence = build_evidence_memory(memory, session, now=NOW)

    assert evidence["record_count"] >= 9
    layers = {item["layer"] for item in evidence["records"]}
    assert set(MEMORY_LAYERS[:-1]).issubset(layers)
    for item in evidence["records"]:
        assert item["memory_id"].startswith("mem_")
        assert item["source"]
        assert item["created_at"] == NOW
        assert item["repo"] == "example/project"
        assert item["evidence_path"]
        assert 0 <= item["confidence"] <= 1
        assert item["validation"]["authority"]


def test_structured_retrieval_returns_top_k_with_usage_reasons():
    evidence = build_evidence_memory(_memory(), _session(), now=NOW)

    result = retrieve_evidence_memories(
        evidence,
        {
            "goal": "repair the pytest failure",
            "blocker": "patch validation failure",
            "constraints": ["do not modify public API"],
        },
        repo="example/project",
        repository_ref="abc123",
        session_id="session-1",
        top_k=4,
        now=NOW,
    )

    assert result["status"] == "pass"
    assert result["selected_count"] == 4
    assert len(result["selected_memory_ids"]) == 4
    assert any(item["kind"] == "user_constraint" for item in result["records"])
    assert all(item["retrieval_reason"] for item in result["records"])
    assert all(item["evidence_path"] for item in result["records"])
    hints = memory_policy_hints(result)
    assert hints["constraints"] == ["do not modify public API"]
    assert hints["source_memory_ids"]["constraints"]


def test_repo_commit_mismatch_excludes_stale_localization_and_repair_memory():
    old_evidence = build_evidence_memory(_memory(), _session(), now=NOW)

    result = retrieve_evidence_memories(
        old_evidence,
        "repair failing function",
        repo="example/project",
        repository_ref="new456",
        session_id="session-1",
        top_k=20,
        now=NOW,
    )

    assert result["discarded_counts"]["stale_repository_version"] >= 4
    assert all(
        item["layer"] not in {"repo_memory", "repair_memory"}
        for item in result["records"]
    )


def test_expired_memory_is_filtered_before_scoring():
    evidence = build_evidence_memory(_memory(), _session(), now=NOW)
    expired = dict(evidence["records"][0])
    expired["memory_id"] = "mem_expired"
    expired["expires_at"] = "2026-07-13T23:59:59Z"
    evidence["records"] = [expired]

    result = retrieve_evidence_memories(
        evidence,
        "current agent state",
        repo="example/project",
        repository_ref="abc123",
        session_id="session-1",
        top_k=3,
        now=NOW,
    )

    assert result["selected_count"] == 0
    assert result["discarded_counts"] == {"expired": 1}


def test_only_sandbox_verified_patch_is_promoted_to_cross_repo_memory():
    memory = _memory()
    memory["patch_attempt_history"].append(
        {
            "candidate_id": "patch-ok",
            "target_function": "pkg.core.load_user",
            "status": "pass",
            "sandbox_status": "pass",
            "passed": True,
            "failure_type": "missing_key",
            "diff_fingerprint": "verified-diff",
            "generator": "hybrid",
        }
    )
    evidence = build_evidence_memory(memory, _session(), now=NOW)

    patterns = promote_verified_repair_patterns(evidence, now=NOW)

    assert len(patterns) == 1
    assert patterns[0]["kind"] == "verified_repair_pattern"
    assert patterns[0]["validation"] == {
        "status": "verified",
        "authority": "sandbox_pytest",
    }
    assert patterns[0]["content"]["generator"] == "hybrid"
    assert patterns[0]["evidence_count"] == 1


def test_long_conversation_compaction_preserves_total_turn_count():
    turns = [
        {
            "created_at": f"2026-07-14T00:{index:02d}:00Z",
            "intent": "inspect_status" if index % 2 == 0 else "continue_repair",
            "loop": {"act": {"action_id": "inspect" if index % 2 == 0 else "repair"}},
        }
        for index in range(45)
    ]

    retained, summary, report = compact_turn_history(turns, now=NOW)
    memory = {"turns": retained, "conversation_summary": summary}

    assert report["status"] == "compacted"
    assert len(retained) == 24
    assert summary["compacted_turn_count"] == 21
    assert total_turn_count(memory) == 45
    assert summary["intent_counts"]["inspect_status"] == 11
    assert summary["intent_counts"]["continue_repair"] == 10


def _session() -> dict:
    return {
        "session_id": "session-1",
        "repo": "example/project",
        "repo_spec": "https://github.com/example/project",
        "repository_ref": "abc123",
        "user_goal": "repair failing tests",
        "status": "partial",
        "memory_path": "outputs/agent_memory.json",
        "session_path": "outputs/agent_session.json",
        "current_state": {
            "current_stage": "patch_generation",
            "primary_blocker": "patch_validation_failed",
        },
        "report_paths": {
            "repository_test_patch_validation_json": "outputs/patch_validation.json",
            "repository_test_fault_localization_json": "outputs/localization.json",
        },
    }


def _memory() -> dict:
    return {
        "current_status": "partial",
        "constraints": ["do not modify public API"],
        "repair_strategy_preferences": ["hybrid"],
        "active_scope": "pkg",
        "repo_profile": {
            "repo": "example/project",
            "repository_ref": "abc123",
            "layout": "src_layout",
            "function_count": 10,
        },
        "graph_memory": {
            "program_graph_available": True,
            "program_graph_nodes": 20,
            "program_graph_edges": 30,
        },
        "topk_suspicious_functions": [
            {
                "function": "pkg.core.load_user",
                "file": "pkg/core.py",
                "final_score": 0.91,
                "why": "pytest traceback contains KeyError",
            }
        ],
        "test_results": {
            "status": "fail",
            "command": "python -m pytest tests",
            "failure_category": "assertion_failure",
            "failure_signal": "KeyError: name",
        },
        "patch_attempt_history": [
            {
                "candidate_id": "patch-fail",
                "target_function": "pkg.core.load_user",
                "status": "fail",
                "sandbox_status": "fail",
                "passed": False,
                "failure_type": "assertion_failure",
                "diff_fingerprint": "failed-diff",
                "generator": "llm",
            }
        ],
        "reflection_trace": {
            "status": "complete",
            "reason": "assertion still failed",
        },
        "blocker_evolution": [
            {
                "source": "patch_validation",
                "blocker": "patch_validation_failed",
                "next_action": "generate another patch",
            }
        ],
        "agent_controller_history": {
            "selected_action": {"id": "generate_hybrid_patch_candidates"}
        },
        "turns": [],
    }
