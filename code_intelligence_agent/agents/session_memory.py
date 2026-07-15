from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from code_intelligence_agent.agents.action_registry import (
    action_spec_for,
    canonical_action_id,
)
from code_intelligence_agent.agents.evidence_memory import (
    MEMORY_LAYERS,
    build_evidence_memory,
    compact_turn_history,
    promote_verified_repair_patterns,
    retrieve_evidence_memories,
    total_turn_count,
)
from code_intelligence_agent.agents.intent_parser import (
    INTENT_ASK_FOR_CLARIFICATION,
    INTENT_CHANGE_CONSTRAINTS,
    INTENT_CHANGE_REPAIR_STRATEGY,
    INTENT_COMPARE_PATCH_CANDIDATES,
    INTENT_CONTINUE_REPAIR,
    INTENT_EXPLAIN_FAILURE,
    INTENT_EXPLAIN_LOCALIZATION,
    INTENT_GENERATE_REPORT,
    INTENT_INSPECT_FUNCTION,
    INTENT_INSPECT_STATUS,
    INTENT_NARROW_SCOPE,
    INTENT_RERUN_TESTS,
    INTENT_ROLLBACK_LAST_ACTION,
    INTENT_STOP_EXECUTION,
)
from code_intelligence_agent.agents.intent_router import route_user_intent
from code_intelligence_agent.agents.llm_client import LLMClient


SESSION_SCHEMA_VERSION = 2
SESSION_FILE = "agent_session.json"
MEMORY_FILE = "agent_memory.json"
EVIDENCE_MEMORY_FILE = "agent_evidence_memory.json"
EVIDENCE_RETRIEVAL_FILE = "agent_memory_retrieval.json"
CROSS_REPO_PATTERN_FILE = "cross_repo_pattern_memory.json"
SESSION_REPORT_FILE = "agent_session_report.md"
AGENT_MEMORY_REPORT_JSON_FILE = "agent_memory_report.json"
AGENT_MEMORY_REPORT_FILE = "agent_memory_report.md"
AGENT_DECISION_REPORT_JSON_FILE = "agent_decision_report.json"
AGENT_DECISION_REPORT_FILE = "agent_decision_report.md"
INDEX_FILE = "index.json"
LOOP = ["observe", "plan", "act", "verify", "reflect", "replan"]

_SECRET_VALUE_PATTERN = re.compile(
    r"\b(?:sk|pk)-[A-Za-z0-9._-]{8,}|\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
)
_SENSITIVE_KEY_PARTS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
}
_VOLATILE_PATH_KEYS = {
    "source_cache_dir",
    "discovery_cache_reuse_source",
    "discovery_cache_fallback_source",
    "discovery_cache_preferred_source",
}


class AgentSessionStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_memory_root()
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def index_path(self) -> Path:
        return self.root / INDEX_FILE

    def upsert_from_summary(
        self,
        summary: dict[str, Any],
        *,
        raw_argv: list[str] | None = None,
        user_goal: str = "",
    ) -> dict[str, Any]:
        summary = _dict(summary)
        session_id = _session_id_for_summary(summary)
        existing = self._load_session_by_id(session_id)
        existing_session = _dict(existing.get("session"))
        existing_memory = _dict(existing.get("memory"))
        now = _now()
        output_dir = str(summary.get("output_dir") or "")
        session_dir = self.root / session_id
        memory_path = session_dir / MEMORY_FILE
        session_path = session_dir / SESSION_FILE
        report_path = session_dir / SESSION_REPORT_FILE

        session = {
            "schema_version": SESSION_SCHEMA_VERSION,
            "session_id": session_id,
            "repo": str(summary.get("repo") or ""),
            "repo_spec": str(summary.get("repo_spec") or ""),
            "repository_ref": str(summary.get("repository_ref") or ""),
            "output_dir": output_dir,
            "status": str(summary.get("status") or ""),
            "current_state": _current_state_from_summary(summary),
            "user_goal": user_goal
            or str(_dict(summary.get("agent_invocation")).get("user_goal") or ""),
            "run_config": _run_config_from_summary(summary, raw_argv=raw_argv),
            "report_paths": _report_paths_from_summary(summary),
            "created_at": str(existing_session.get("created_at") or now),
            "updated_at": now,
            "turn_count": _int(existing_session.get("turn_count", 0)),
            "memory_path": str(memory_path),
            "session_path": str(session_path),
            "session_report_path": str(report_path),
        }
        memory = _memory_from_summary(
            summary,
            session=session,
            existing_memory=existing_memory,
        )
        memory = self._append_turn(
            session,
            memory,
            _analysis_turn(summary, raw_argv=raw_argv),
        )
        memory = self._refresh_evidence_memory(session, memory)
        session["turn_count"] = total_turn_count(memory)
        self._write_session_bundle(session, memory)
        summary["agent_session"] = _session_public_summary(session, memory)
        return summary["agent_session"]

    def chat(
        self,
        session_ref: str,
        message: str,
        *,
        execute: bool = False,
        intent_client: LLMClient | None = None,
        llm_intent_enabled: bool | None = None,
    ) -> dict[str, Any]:
        loaded = self.load(session_ref)
        session = _dict(loaded["session"])
        memory = _dict(loaded["memory"])
        memory = self._refresh_evidence_memory(session, memory, query=message)
        intent = route_user_intent(
            message,
            context=_intent_router_context(session, memory),
            client=intent_client,
            llm_enabled=llm_intent_enabled,
        )
        decision = _decision_for_intent(session, memory, intent, execute=execute)
        turn = _conversation_turn(
            session=session,
            memory=memory,
            intent=intent,
            decision=decision,
        )
        memory = self._append_turn(session, memory, turn)
        _apply_intent_to_memory(memory, intent)
        session["status"] = str(memory.get("current_status") or session.get("status") or "")
        session["current_state"] = _current_state_from_memory(memory)
        session["updated_at"] = _now()
        memory = self._refresh_evidence_memory(session, memory, query=message)
        session["turn_count"] = total_turn_count(memory)
        self._write_session_bundle(session, memory)
        return {
            "status": "pass",
            "session": _session_public_summary(session, memory),
            "intent": intent,
            "decision": decision,
            "turn": turn,
            "answer": decision.get("answer", ""),
            "memory_usage_evidence": turn.get("memory_usage_evidence", {}),
        }

    def resume(self, session_ref: str) -> dict[str, Any]:
        loaded = self.load(session_ref)
        session = _dict(loaded["session"])
        memory = _dict(loaded["memory"])
        memory = self._refresh_evidence_memory(
            session,
            memory,
            query="resume current session status constraints blocker and next action",
        )
        intent = {
            "intent": INTENT_INSPECT_STATUS,
            "message": "resume session",
            "arguments": {},
            "scope": "",
            "constraints": [],
            "strategy": "",
            "function": "",
            "confidence": 1.0,
            "reason": "explicit resume command",
            "required_context": ["session_memory"],
            "source": "explicit_command",
        }
        decision = _decision_for_intent(session, memory, intent, execute=False)
        decision["action_id"] = "resume_session_from_memory"
        decision["answer"] = _resume_answer(session, memory)
        turn = _conversation_turn(
            session=session,
            memory=memory,
            intent=intent,
            decision=decision,
        )
        memory = self._append_turn(session, memory, turn)
        session["updated_at"] = _now()
        memory = self._refresh_evidence_memory(session, memory)
        session["turn_count"] = total_turn_count(memory)
        self._write_session_bundle(session, memory)
        return {
            "status": "pass",
            "session": _session_public_summary(session, memory),
            "intent": intent,
            "decision": decision,
            "turn": turn,
            "answer": decision["answer"],
            "memory_usage_evidence": turn.get("memory_usage_evidence", {}),
        }

    def load(self, session_ref: str) -> dict[str, Any]:
        session_path = self.resolve_session_path(session_ref)
        session = _dict(_read_json(session_path))
        memory_path = Path(str(session.get("memory_path") or ""))
        if not memory_path.exists():
            memory_path = session_path.with_name(MEMORY_FILE)
        memory = _dict(_read_json(memory_path))
        return {"session": session, "memory": memory}

    def inspect_memory(
        self,
        session_ref: str,
        *,
        query: str = "",
        layer: str = "",
        top_k: int = 8,
    ) -> dict[str, Any]:
        loaded = self.load(session_ref)
        session = _dict(loaded["session"])
        memory = self._refresh_evidence_memory(
            session,
            _dict(loaded["memory"]),
            query=query or _default_memory_query(_dict(loaded["memory"]), session),
        )
        evidence = _dict(memory.get("evidence_memory"))
        if layer:
            evidence = {
                **evidence,
                "records": [
                    item
                    for item in _list(evidence.get("records"))
                    if _dict(item).get("layer") == layer
                ],
            }
        retrieval = retrieve_evidence_memories(
            evidence,
            query or _default_memory_query(memory, session),
            repo=str(session.get("repo") or session.get("repo_spec") or ""),
            repository_ref=str(session.get("repository_ref") or ""),
            session_id=str(session.get("session_id") or ""),
            top_k=top_k,
        )
        return {
            "status": "pass",
            "reason": "evidence_memory_inspected",
            "session": _session_public_summary(session, memory),
            "memory_report": build_agent_memory_report_from_memory(
                memory,
                session=session,
            ),
            "retrieval": retrieval,
        }

    def delete_memory(
        self,
        session_ref: str,
        memory_id: str,
    ) -> dict[str, Any]:
        loaded = self.load(session_ref)
        session = _dict(loaded["session"])
        memory = self._refresh_evidence_memory(session, _dict(loaded["memory"]))
        records = [
            _dict(item) for item in _list(_dict(memory.get("evidence_memory")).get("records"))
        ]
        target = next(
            (item for item in records if str(item.get("memory_id") or "") == memory_id),
            None,
        )
        if target is None:
            return {
                "status": "not_found",
                "reason": "memory_id_not_found",
                "memory_id": memory_id,
                "session": _session_public_summary(session, memory),
            }
        memory["deleted_memory_ids"] = _unique_strings(
            [*_list(memory.get("deleted_memory_ids")), memory_id]
        )
        memory["evidence_memory"] = {
            **_dict(memory.get("evidence_memory")),
            "deleted_memory_ids": _list(memory.get("deleted_memory_ids")),
            "records": [
                item
                for item in records
                if str(item.get("memory_id") or "") != memory_id
            ],
        }
        memory = self._refresh_evidence_memory(session, memory)
        self._write_session_bundle(session, memory)
        return {
            "status": "pass",
            "reason": "memory_record_deleted",
            "memory_id": memory_id,
            "deleted_layer": str(target.get("layer") or ""),
            "deleted_kind": str(target.get("kind") or ""),
            "session": _session_public_summary(session, memory),
        }

    def reset_memory(
        self,
        session_ref: str,
        *,
        scope: str = "session",
    ) -> dict[str, Any]:
        if scope not in {"session", "repair", "all"}:
            raise ValueError(f"Unsupported memory reset scope: {scope}")
        loaded = self.load(session_ref)
        session = _dict(loaded["session"])
        memory = self._refresh_evidence_memory(session, _dict(loaded["memory"]))
        records = [
            _dict(item) for item in _list(_dict(memory.get("evidence_memory")).get("records"))
        ]
        reset_layers = {
            "session": {"working_memory", "session_memory"},
            "repair": {"repair_memory"},
            "all": {
                "working_memory",
                "session_memory",
                "repo_memory",
                "repair_memory",
            },
        }[scope]
        tombstones = [
            str(item.get("memory_id") or "")
            for item in records
            if str(item.get("layer") or "") in reset_layers
        ]
        memory["deleted_memory_ids"] = _unique_strings(
            [*_list(memory.get("deleted_memory_ids")), *tombstones]
        )
        if scope in {"session", "all"}:
            for key in (
                "turns",
                "user_intent_history",
                "constraints",
                "repair_strategy_preferences",
            ):
                memory[key] = []
            for key in (
                "active_scope",
                "active_function",
                "conversation_summary",
                "conversation_compaction",
            ):
                memory.pop(key, None)
            memory["execution_stopped"] = False
        if scope in {"repair", "all"}:
            memory["patch_attempt_history"] = []
            memory["reflection_trace"] = {}
            memory["blocker_evolution"] = []
        if scope == "all":
            for key, empty in (
                ("repo_profile", {}),
                ("graph_memory", {}),
                ("topk_suspicious_functions", []),
                ("test_results", {}),
                ("agent_controller_history", {}),
                ("execution_trace", {}),
            ):
                memory[key] = empty
        memory["evidence_memory"] = {
            **_dict(memory.get("evidence_memory")),
            "deleted_memory_ids": _list(memory.get("deleted_memory_ids")),
            "records": [
                item
                for item in records
                if str(item.get("memory_id") or "") not in tombstones
            ],
        }
        memory = self._refresh_evidence_memory(session, memory)
        session["turn_count"] = total_turn_count(memory)
        session["updated_at"] = _now()
        self._write_session_bundle(session, memory)
        return {
            "status": "pass",
            "reason": "memory_scope_reset",
            "scope": scope,
            "deleted_record_count": len(tombstones),
            "session": _session_public_summary(session, memory),
        }

    def resolve_session_path(self, session_ref: str) -> Path:
        ref_path = Path(session_ref)
        if ref_path.is_file():
            return ref_path
        if ref_path.is_dir() and (ref_path / SESSION_FILE).exists():
            return ref_path / SESSION_FILE
        index = self._load_index()
        item = _dict(index.get("sessions", {}).get(session_ref))
        indexed = Path(str(item.get("session_path") or ""))
        if indexed.exists():
            return indexed
        root_candidate = self.root / session_ref / SESSION_FILE
        if root_candidate.exists():
            return root_candidate
        raise FileNotFoundError(f"Agent session not found: {session_ref}")

    def _append_turn(
        self,
        session: dict[str, Any],
        memory: dict[str, Any],
        turn: dict[str, Any],
    ) -> dict[str, Any]:
        turns = _list(memory.get("turns"))
        turns.append(_redact(turn))
        retained, summary, compaction = compact_turn_history(
            turns,
            _dict(memory.get("conversation_summary")),
        )
        memory["turns"] = retained
        memory["conversation_summary"] = summary
        memory["conversation_compaction"] = compaction
        memory["turn_count"] = total_turn_count(memory)
        memory["updated_at"] = _now()
        memory["session_id"] = session.get("session_id")
        return memory

    def _refresh_evidence_memory(
        self,
        session: dict[str, Any],
        memory: dict[str, Any],
        *,
        query: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cross_repo_payload = _dict(_read_json(self.root / CROSS_REPO_PATTERN_FILE))
        cross_repo_records = _list(cross_repo_payload.get("records"))
        evidence = build_evidence_memory(
            memory,
            session,
            existing=_dict(memory.get("evidence_memory")),
            cross_repo_records=cross_repo_records,
        )
        promoted = promote_verified_repair_patterns(
            evidence,
            existing_records=cross_repo_records,
        )
        if promoted != cross_repo_records:
            _write_json(
                self.root / CROSS_REPO_PATTERN_FILE,
                {
                    "schema_version": 1,
                    "updated_at": _now(),
                    "record_count": len(promoted),
                    "records": promoted,
                },
            )
            evidence = build_evidence_memory(
                memory,
                session,
                existing=evidence,
                cross_repo_records=promoted,
            )
        memory["evidence_memory"] = evidence
        retrieval_query = query or _default_memory_query(memory, session)
        memory["latest_memory_retrieval"] = retrieve_evidence_memories(
            evidence,
            retrieval_query,
            repo=str(session.get("repo") or session.get("repo_spec") or ""),
            repository_ref=str(session.get("repository_ref") or ""),
            session_id=str(session.get("session_id") or ""),
            top_k=8,
        )
        memory["memory_layers"] = _memory_layers_from_memory(memory, session=session)
        return memory

    def _write_session_bundle(
        self,
        session: dict[str, Any],
        memory: dict[str, Any],
    ) -> None:
        session_id = str(session["session_id"])
        session_dir = self.root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        session["memory_path"] = str(session_dir / MEMORY_FILE)
        session["session_path"] = str(session_dir / SESSION_FILE)
        session["session_report_path"] = str(session_dir / SESSION_REPORT_FILE)
        session["agent_memory_report_json"] = str(session_dir / AGENT_MEMORY_REPORT_JSON_FILE)
        session["agent_memory_report_path"] = str(session_dir / AGENT_MEMORY_REPORT_FILE)
        session["agent_decision_report_json"] = str(session_dir / AGENT_DECISION_REPORT_JSON_FILE)
        session["agent_decision_report_path"] = str(session_dir / AGENT_DECISION_REPORT_FILE)
        session["evidence_memory_path"] = str(session_dir / EVIDENCE_MEMORY_FILE)
        session["memory_retrieval_path"] = str(session_dir / EVIDENCE_RETRIEVAL_FILE)
        report = render_session_report(session, memory)
        memory_report = build_agent_memory_report_from_memory(memory, session=session)
        decision_report = build_agent_decision_report_from_memory(memory, session=session)
        _write_json(session_dir / SESSION_FILE, session)
        _write_json(session_dir / MEMORY_FILE, memory)
        _write_json(session_dir / EVIDENCE_MEMORY_FILE, _dict(memory.get("evidence_memory")))
        _write_json(
            session_dir / EVIDENCE_RETRIEVAL_FILE,
            _dict(memory.get("latest_memory_retrieval")),
        )
        (session_dir / SESSION_REPORT_FILE).write_text(report, encoding="utf-8")
        _write_json(session_dir / AGENT_MEMORY_REPORT_JSON_FILE, memory_report)
        (session_dir / AGENT_MEMORY_REPORT_FILE).write_text(
            render_agent_memory_report(memory_report),
            encoding="utf-8",
        )
        _write_json(session_dir / AGENT_DECISION_REPORT_JSON_FILE, decision_report)
        (session_dir / AGENT_DECISION_REPORT_FILE).write_text(
            render_agent_decision_report(decision_report),
            encoding="utf-8",
        )

        output_dir_text = str(session.get("output_dir") or "")
        if output_dir_text:
            output_dir = Path(output_dir_text)
            output_dir.mkdir(parents=True, exist_ok=True)
            output_session = dict(session)
            output_session["session_path"] = str(output_dir / SESSION_FILE)
            output_session["memory_path"] = str(output_dir / MEMORY_FILE)
            output_session["session_report_path"] = str(output_dir / SESSION_REPORT_FILE)
            output_session["agent_memory_report_json"] = str(
                output_dir / AGENT_MEMORY_REPORT_JSON_FILE
            )
            output_session["agent_memory_report_path"] = str(
                output_dir / AGENT_MEMORY_REPORT_FILE
            )
            output_session["agent_decision_report_json"] = str(
                output_dir / AGENT_DECISION_REPORT_JSON_FILE
            )
            output_session["agent_decision_report_path"] = str(
                output_dir / AGENT_DECISION_REPORT_FILE
            )
            output_session["evidence_memory_path"] = str(
                output_dir / EVIDENCE_MEMORY_FILE
            )
            output_session["memory_retrieval_path"] = str(
                output_dir / EVIDENCE_RETRIEVAL_FILE
            )
            _write_json(output_dir / SESSION_FILE, output_session)
            _write_json(output_dir / MEMORY_FILE, memory)
            _write_json(
                output_dir / EVIDENCE_MEMORY_FILE,
                _dict(memory.get("evidence_memory")),
            )
            _write_json(
                output_dir / EVIDENCE_RETRIEVAL_FILE,
                _dict(memory.get("latest_memory_retrieval")),
            )
            (output_dir / SESSION_REPORT_FILE).write_text(report, encoding="utf-8")
            _write_json(output_dir / AGENT_MEMORY_REPORT_JSON_FILE, memory_report)
            (output_dir / AGENT_MEMORY_REPORT_FILE).write_text(
                render_agent_memory_report(memory_report),
                encoding="utf-8",
            )
            _write_json(output_dir / AGENT_DECISION_REPORT_JSON_FILE, decision_report)
            (output_dir / AGENT_DECISION_REPORT_FILE).write_text(
                render_agent_decision_report(decision_report),
                encoding="utf-8",
            )

        index = self._load_index()
        sessions = _dict(index.get("sessions"))
        sessions[session_id] = {
            "session_id": session_id,
            "repo": session.get("repo", ""),
            "repo_spec": session.get("repo_spec", ""),
            "repository_ref": session.get("repository_ref", ""),
            "output_dir": session.get("output_dir", ""),
            "status": session.get("status", ""),
            "updated_at": session.get("updated_at", ""),
            "session_path": str(session_dir / SESSION_FILE),
            "memory_path": str(session_dir / MEMORY_FILE),
            "session_report_path": str(session_dir / SESSION_REPORT_FILE),
            "agent_memory_report_json": str(session_dir / AGENT_MEMORY_REPORT_JSON_FILE),
            "agent_memory_report_path": str(session_dir / AGENT_MEMORY_REPORT_FILE),
            "agent_decision_report_json": str(session_dir / AGENT_DECISION_REPORT_JSON_FILE),
            "agent_decision_report_path": str(session_dir / AGENT_DECISION_REPORT_FILE),
            "evidence_memory_path": str(session_dir / EVIDENCE_MEMORY_FILE),
            "memory_retrieval_path": str(session_dir / EVIDENCE_RETRIEVAL_FILE),
        }
        index = {
            "schema_version": SESSION_SCHEMA_VERSION,
            "updated_at": _now(),
            "sessions": sessions,
        }
        _write_json(self.index_path, index)

    def _load_session_by_id(self, session_id: str) -> dict[str, Any]:
        session_path = self.root / session_id / SESSION_FILE
        if not session_path.exists():
            return {}
        session = _dict(_read_json(session_path))
        memory = _dict(_read_json(session_path.with_name(MEMORY_FILE)))
        return {"session": session, "memory": memory}

    def _load_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"schema_version": SESSION_SCHEMA_VERSION, "sessions": {}}
        return _dict(_read_json(self.index_path))


def default_memory_root(cwd: str | Path | None = None) -> Path:
    configured = os.environ.get("CIA_AGENT_MEMORY_ROOT", "").strip()
    if configured:
        return Path(configured)
    return Path(cwd or Path.cwd()) / ".code_intelligence_agent" / "sessions"


def create_or_update_session_from_summary(
    summary: dict[str, Any],
    *,
    raw_argv: list[str] | None = None,
    user_goal: str = "",
    memory_root: str | Path | None = None,
) -> dict[str, Any]:
    return AgentSessionStore(memory_root).upsert_from_summary(
        summary,
        raw_argv=raw_argv,
        user_goal=user_goal,
    )


def chat_with_session(
    session_ref: str,
    message: str,
    *,
    memory_root: str | Path | None = None,
    execute: bool = False,
    intent_client: LLMClient | None = None,
    llm_intent_enabled: bool | None = None,
) -> dict[str, Any]:
    return AgentSessionStore(memory_root).chat(
        session_ref,
        message,
        execute=execute,
        intent_client=intent_client,
        llm_intent_enabled=llm_intent_enabled,
    )


def resume_session(
    session_ref: str,
    *,
    memory_root: str | Path | None = None,
) -> dict[str, Any]:
    return AgentSessionStore(memory_root).resume(session_ref)


def inspect_session_memory(
    session_ref: str,
    *,
    memory_root: str | Path | None = None,
    query: str = "",
    layer: str = "",
    top_k: int = 8,
) -> dict[str, Any]:
    return AgentSessionStore(memory_root).inspect_memory(
        session_ref,
        query=query,
        layer=layer,
        top_k=top_k,
    )


def delete_session_memory(
    session_ref: str,
    memory_id: str,
    *,
    memory_root: str | Path | None = None,
) -> dict[str, Any]:
    return AgentSessionStore(memory_root).delete_memory(session_ref, memory_id)


def reset_session_memory(
    session_ref: str,
    *,
    memory_root: str | Path | None = None,
    scope: str = "session",
) -> dict[str, Any]:
    return AgentSessionStore(memory_root).reset_memory(session_ref, scope=scope)


def render_session_report(session: dict[str, Any], memory: dict[str, Any]) -> str:
    timeline = _list(memory.get("turns"))
    intents = _list(memory.get("user_intent_history"))
    patches = _list(memory.get("patch_attempt_history"))
    blockers = _list(memory.get("blocker_evolution"))
    topk = _list(memory.get("topk_suspicious_functions"))
    lines = [
        "# Agent Session Report",
        "",
        f"- Session ID: `{_md(session.get('session_id'))}`",
        f"- Repo: `{_md(session.get('repo'))}`",
        f"- Repo Spec: `{_md(session.get('repo_spec'))}`",
        f"- Ref: `{_md(session.get('repository_ref') or 'default')}`",
        f"- Status: `{_md(session.get('status'))}`",
        f"- Output Dir: `{_md(session.get('output_dir'))}`",
        f"- Turn Count: {_int(memory.get('turn_count', 0))}",
        "",
        "## Memory Usage Evidence",
        "",
        f"- Repo profile stored: {str(bool(memory.get('repo_profile'))).lower()}",
        f"- Top-k stored: {len(topk)}",
        f"- Test result stored: {str(bool(memory.get('test_results'))).lower()}",
        f"- Patch attempts stored: {len(patches)}",
        f"- Blocker records stored: {len(blockers)}",
        "",
        "## User Intent History",
        "",
        "| Turn | Intent | Message | Scope | Constraints |",
        "| --- | --- | --- | --- | --- |",
    ]
    if intents:
        for idx, item_value in enumerate(intents, start=1):
            item = _dict(item_value)
            lines.append(
                "| "
                f"{idx} | `{_md(item.get('intent'))}` | {_md(item.get('message'))} | "
                f"`{_md(item.get('scope') or 'none')}` | "
                f"{_md(', '.join(str(x) for x in _list(item.get('constraints'))) or 'none')} |"
            )
    else:
        lines.append("| none | none | none | none | none |")
    lines.extend(
        [
            "",
            "## Session Timeline",
            "",
            "| Turn | Source | Intent | Action | Verify | Replan |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    if timeline:
        for idx, item_value in enumerate(timeline, start=1):
            item = _dict(item_value)
            loop = _dict(item.get("loop"))
            lines.append(
                "| "
                f"{idx} | `{_md(item.get('source'))}` | `{_md(item.get('intent'))}` | "
                f"`{_md(_dict(loop.get('act')).get('action_id'))}` | "
                f"`{_md(_dict(loop.get('verify')).get('status'))}` | "
                f"{_md(_dict(loop.get('replan')).get('next_action') or '')} |"
            )
    else:
        lines.append("| none | none | none | none | none | none |")
    lines.extend(
        [
            "",
            "## Blocker Evolution",
            "",
            "| Source | Blocker | Next Action |",
            "| --- | --- | --- |",
        ]
    )
    if blockers:
        for item_value in blockers:
            item = _dict(item_value)
            lines.append(
                "| "
                f"`{_md(item.get('source'))}` | `{_md(item.get('blocker') or 'none')}` | "
                f"{_md(item.get('next_action') or 'none')} |"
            )
    else:
        lines.append("| none | none | none |")
    lines.extend(
        [
            "",
            "## Patch Attempt History",
            "",
            "| Candidate | Target | Status | Failure Type | Sandbox |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    if patches:
        for item_value in patches:
            item = _dict(item_value)
            lines.append(
                "| "
                f"`{_md(item.get('candidate_id') or item.get('id') or 'unknown')}` | "
                f"{_md(item.get('target_function') or 'unknown')} | "
                f"`{_md(item.get('status') or 'unknown')}` | "
                f"`{_md(item.get('failure_type') or 'none')}` | "
                f"`{_md(item.get('sandbox_status') or 'unknown')}` |"
            )
    else:
        lines.append("| none | none | none | none | none |")
    lines.extend(
        [
            "",
            "## AgentController Loop Evidence",
            "",
            "| Loop Step | Latest Evidence |",
            "| --- | --- |",
        ]
    )
    latest = _dict(timeline[-1]) if timeline else {}
    latest_loop = _dict(latest.get("loop"))
    for step in LOOP:
        lines.append(
            "| "
            f"`{step}` | {_md(_dict(latest_loop.get(step)).get('evidence') or 'none')} |"
        )
    lines.append("")
    return "\n".join(lines)


def build_agent_memory_report_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    summary = _dict(summary)
    session = {
        "session_id": str(_dict(summary.get("agent_session")).get("session_id") or ""),
        "repo": str(summary.get("repo") or ""),
        "repo_spec": str(summary.get("repo_spec") or ""),
        "repository_ref": str(summary.get("repository_ref") or ""),
        "output_dir": str(summary.get("output_dir") or ""),
        "status": str(summary.get("status") or ""),
        "user_goal": str(
            _dict(summary.get("agent_invocation")).get("user_goal")
            or summary.get("user_goal")
            or ""
        ),
        "turn_count": _int(_dict(summary.get("agent_session")).get("turn_count", 0)),
        "run_config": _dict(summary.get("agent_invocation")),
        "report_paths": _report_paths_from_summary(summary),
    }
    patch_attempts = _patch_attempts_from_summary(summary)
    memory = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "session_id": session.get("session_id", ""),
        "repo_profile": _repo_profile_from_summary(summary),
        "graph_memory": _graph_memory_from_summary(summary),
        "topk_suspicious_functions": _topk_from_summary(summary),
        "test_results": _test_results_from_summary(summary),
        "blocker_evolution": _blockers_from_summary(summary),
        "patch_attempt_history": patch_attempts,
        "reflection_trace": _reflection_from_summary(summary),
        "agent_controller_history": _controller_history_from_summary(summary),
        "execution_trace": _execution_trace_from_summary(summary),
        "final_report_summary": _final_report_summary_from_summary(summary),
        "user_intent_history": [],
        "constraints": [],
        "repair_strategy_preferences": [],
        "turns": [],
        "turn_count": _int(session.get("turn_count", 0)),
        "current_status": str(summary.get("status") or ""),
    }
    external_memory = _compatible_external_memory(summary, session=session)
    if external_memory:
        memory = _merge_external_memory(memory, external_memory)
    cross_repo_records = _list(
        _dict(
            _read_json(default_memory_root() / CROSS_REPO_PATTERN_FILE)
        ).get("records")
    )
    memory["evidence_memory"] = build_evidence_memory(
        memory,
        session,
        existing=_dict(external_memory.get("evidence_memory")),
        cross_repo_records=cross_repo_records,
    )
    memory["latest_memory_retrieval"] = retrieve_evidence_memories(
        _dict(memory.get("evidence_memory")),
        _default_memory_query(memory, session),
        repo=str(session.get("repo") or session.get("repo_spec") or ""),
        repository_ref=str(session.get("repository_ref") or ""),
        session_id=str(session.get("session_id") or ""),
        top_k=8,
    )
    memory["memory_layers"] = _memory_layers_from_memory(memory, session=session)
    return _redact(build_agent_memory_report_from_memory(memory, session=session))


def build_agent_memory_report_from_memory(
    memory: dict[str, Any],
    *,
    session: dict[str, Any],
) -> dict[str, Any]:
    layers = _dict(memory.get("memory_layers")) or _memory_layers_from_memory(
        memory,
        session=session,
    )
    layer_names = list(MEMORY_LAYERS)
    layer_statuses = {
        name: str(_dict(layers.get(name)).get("status") or "missing")
        for name in layer_names
    }
    ready_count = sum(1 for status in layer_statuses.values() if status == "ready")
    return {
        "schema_version": 1,
        "status": "pass" if ready_count == len(layer_names) else "warning",
        "reason": (
            "layered_memory_ready"
            if ready_count == len(layer_names)
            else "layered_memory_incomplete"
        ),
        "session_id": str(session.get("session_id") or memory.get("session_id") or ""),
        "repo": str(session.get("repo") or _dict(memory.get("repo_profile")).get("repo") or ""),
        "layer_count": len(layer_names),
        "ready_layer_count": ready_count,
        "layer_statuses": layer_statuses,
        "memory_layers": layers,
        "reuse_contract": _memory_reuse_contract(layers),
        "evidence_memory": _evidence_memory_summary(
            _dict(memory.get("evidence_memory"))
        ),
        "retrieval": _dict(memory.get("latest_memory_retrieval")),
        "conversation_compaction": {
            **_dict(memory.get("conversation_compaction")),
            "summary": _dict(memory.get("conversation_summary")),
        },
    }


def render_agent_memory_report(payload: dict[str, Any]) -> str:
    payload = _dict(payload)
    layers = _dict(payload.get("memory_layers"))
    working_layer = _dict(layers.get("working_memory"))
    session_layer = _dict(layers.get("session_memory"))
    repo_layer = _dict(layers.get("repo_memory"))
    repair_layer = _dict(layers.get("repair_memory"))
    pattern_layer = _dict(layers.get("cross_repo_pattern_memory"))
    reuse = _dict(payload.get("reuse_contract"))
    evidence = _dict(payload.get("evidence_memory"))
    retrieval = _dict(payload.get("retrieval"))
    decision_use_counts = _dict(retrieval.get("decision_use_counts"))
    conflict_report = _dict(retrieval.get("conflicts"))
    lines = [
        "# Agent Memory Report",
        "",
        f"- Status: `{_md(payload.get('status') or 'none')}`",
        f"- Reason: `{_md(payload.get('reason') or 'none')}`",
        f"- Session ID: `{_md(payload.get('session_id') or 'none')}`",
        f"- Repo: `{_md(payload.get('repo') or 'none')}`",
        f"- Layers Ready: {_int(payload.get('ready_layer_count', 0))}/{_int(payload.get('layer_count', 0))}",
        "",
        "## Layer Status",
        "",
        "| Layer | Status | Key Evidence |",
        "| --- | --- | --- |",
        _memory_layer_row(
            "Working Memory",
            working_layer,
            f"stage={working_layer.get('current_stage') or 'none'}, action={working_layer.get('latest_action') or 'none'}",
        ),
        _memory_layer_row(
            "Session Memory",
            session_layer,
            f"turns={session_layer.get('turn_count', 0)}, constraints={len(_list(session_layer.get('constraints')))}, strategies={len(_list(session_layer.get('repair_strategy_preferences')))}",
        ),
        _memory_layer_row(
            "Repo Memory",
            repo_layer,
            f"functions={repo_layer.get('function_count', 0)}, test_command={repo_layer.get('test_command') or 'none'}",
        ),
        _memory_layer_row(
            "Repair Memory",
            repair_layer,
            f"failed_patches={repair_layer.get('failed_patch_count', 0)}, successful_patches={repair_layer.get('successful_patch_count', 0)}, strategies={len(_list(repair_layer.get('strategy_preferences')))}",
        ),
        _memory_layer_row(
            "Cross-repo Pattern Memory",
            pattern_layer,
            f"verified_patterns={pattern_layer.get('pattern_count', 0)}, source_repositories={pattern_layer.get('source_repository_count', 0)}",
        ),
        "",
        "## Evidence Retrieval",
        "",
        f"- Algorithm: `{_md(evidence.get('retrieval_algorithm') or retrieval.get('algorithm') or 'none')}`",
        f"- Active Records: {_int(evidence.get('active_record_count', 0))}",
        f"- Stale Records: {_int(evidence.get('stale_record_count', 0))}",
        f"- Selected: {_int(retrieval.get('selected_count', 0))}/{_int(retrieval.get('candidate_count', 0))}",
        f"- Selected IDs: {_md(', '.join(_list(retrieval.get('selected_memory_ids'))) or 'none')}",
        (
            "- Decision Use: "
            f"execution_hint={_int(decision_use_counts.get('execution_hint', 0))}, "
            f"advisory_only={_int(decision_use_counts.get('advisory_only', 0))}, "
            f"audit_only={_int(decision_use_counts.get('audit_only', 0))}"
        ),
        (
            "- Conflicts: "
            f"status={_md(conflict_report.get('status') or 'clear')}, "
            f"groups={_int(conflict_report.get('group_count', 0))}, "
            f"records={_int(conflict_report.get('record_count', 0))}"
        ),
        "",
        "## Reuse Contract",
        "",
        "| Consumer | Enabled | Evidence |",
        "| --- | --- | --- |",
    ]
    for key in [
        "feeds_patch_generation",
        "feeds_reflection",
        "feeds_replan",
        "avoids_repeated_failed_patches",
        "preserves_user_constraints",
    ]:
        row = _dict(reuse.get(key))
        lines.append(
            "| "
            f"`{_md(key)}` | {str(bool(row.get('enabled', False))).lower()} | "
            f"{_md(row.get('evidence') or 'none')} |"
        )
    lines.extend(
        [
            "",
            "## Repair Memory",
            "",
            "| Field | Value |",
            "| --- | --- |",
            f"| Failed Patch Fingerprints | {_md(', '.join(_list(repair_layer.get('failed_patch_fingerprints'))) or 'none')} |",
            f"| Successful Strategies | {_md(', '.join(_list(repair_layer.get('successful_repair_strategies'))) or 'none')} |",
            f"| Requested Strategies | {_md(', '.join(_list(repair_layer.get('strategy_preferences'))) or 'none')} |",
            f"| Latest Failure Category | `{_md(repair_layer.get('latest_failure_category') or 'none')}` |",
            f"| Active Constraints | {_md(', '.join(_list(session_layer.get('constraints'))) or 'none')} |",
            "",
            "## Pattern Memory",
            "",
            "| Pattern | Count | Evidence |",
            "| --- | ---: | --- |",
        ]
    )
    patterns = _list(pattern_layer.get("verified_patterns"))
    if patterns:
        for item_value in patterns:
            item = _dict(item_value)
            lines.append(
                "| "
                f"`{_md(item.get('pattern'))}` | {_int(item.get('count', 0))} | "
                f"{_md(item.get('evidence') or 'none')} |"
            )
    else:
        lines.append("| none | 0 | none |")
    return "\n".join(lines) + "\n"


def build_agent_decision_report_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    summary = _dict(summary)
    controller = _dict(summary.get("agent_controller"))
    execution_trace = _dict(summary.get("agent_execution_trace"))
    decision_timeline = _dict(summary.get("agent_decision_timeline"))
    return _agent_decision_report(
        session_id=str(_dict(summary.get("agent_session")).get("session_id") or ""),
        repo=str(summary.get("repo") or ""),
        controller=controller,
        execution_trace=execution_trace,
        decision_timeline=decision_timeline,
        memory_layers=_dict(_dict(summary.get("agent_memory_report")).get("memory_layers")),
    )


def build_agent_decision_report_from_memory(
    memory: dict[str, Any],
    *,
    session: dict[str, Any],
) -> dict[str, Any]:
    controller_history = _dict(memory.get("agent_controller_history"))
    controller = {
        "control_loop": _list(controller_history.get("control_loop")),
        "selected_action": _dict(controller_history.get("selected_action")),
        "decision_trace": _list(controller_history.get("decision_trace")),
        "auto_trace": _list(controller_history.get("auto_trace")),
        "auto_actions": _list(controller_history.get("auto_actions")),
    }
    return _agent_decision_report(
        session_id=str(session.get("session_id") or memory.get("session_id") or ""),
        repo=str(session.get("repo") or _dict(memory.get("repo_profile")).get("repo") or ""),
        controller=controller,
        execution_trace=_dict(memory.get("execution_trace")),
        decision_timeline=_decision_timeline_from_memory(memory),
        memory_layers=_dict(memory.get("memory_layers")),
    )


def render_agent_decision_report(payload: dict[str, Any]) -> str:
    payload = _dict(payload)
    selected = _dict(payload.get("selected_action"))
    llm = _dict(payload.get("llm_planner"))
    gate = _dict(llm.get("safety_gate"))
    execution = _dict(payload.get("execution"))
    lines = [
        "# Agent Decision Report",
        "",
        f"- Status: `{_md(payload.get('status') or 'none')}`",
        f"- Reason: `{_md(payload.get('reason') or 'none')}`",
        f"- Session ID: `{_md(payload.get('session_id') or 'none')}`",
        f"- Repo: `{_md(payload.get('repo') or 'none')}`",
        f"- Selected Action: `{_md(selected.get('id') or 'none')}`",
        f"- Controller Authority: `{_md(payload.get('controller_authority') or 'rules_and_sandbox_gate_decide')}`",
        f"- LLM Planner Status: `{_md(llm.get('status') or 'none')}`",
        f"- LLM Planner Selected Action: `{_md(llm.get('selected_action') or 'none')}`",
        f"- LLM Safety Gate: `{_md(gate.get('status') or 'none')}` / `{_md(gate.get('reason') or 'none')}`",
        f"- Execution Trace: `{_md(execution.get('status') or 'none')}` with {_int(execution.get('executed_action_count', 0))} executed action(s)",
        "",
        "## Decision Fields",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Why Selected | {_md(selected.get('reason') or 'none')} |",
        f"| Confidence | `{_md(selected.get('confidence') or 'none')}` |",
        f"| Risk | `{_md(selected.get('risk') or 'none')}` |",
        f"| Required Evidence | {_md(', '.join(_list(llm.get('required_evidence'))) or 'none')} |",
        f"| LLM Proposal Source | `{_md(llm.get('proposal_source') or 'none')}` |",
        f"| LLM Memory Used | {_md(', '.join(_list(llm.get('memory_used'))) or 'none')} |",
        f"| LLM Fallback | `{str(bool(llm.get('fallback_to_rule_planner', False))).lower()}` / {_md(llm.get('fallback_reason') or 'none')} |",
        f"| LLM Recommended Action | `{_md(gate.get('recommended_action') or llm.get('selected_action') or 'none')}` |",
        f"| Controller Final Action | `{_md(gate.get('controller_action') or selected.get('id') or 'none')}` |",
        f"| Adopted Action | `{_md(gate.get('adopted_action') or selected.get('id') or 'none')}` |",
        f"| Override Requested | `{str(bool(gate.get('override_requested', False))).lower()}` |",
        f"| Override Allowed | `{str(bool(gate.get('override_allowed', False))).lower()}` |",
        f"| Next Plan | {_md(llm.get('next_plan') or selected.get('next_plan') or 'none')} |",
        "",
        "## Action Execution",
        "",
        "| Iteration | Action | Status | Executed | Verify | Next Action |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    for item_value in _list(payload.get("actions")):
        item = _dict(item_value)
        lines.append(
            "| "
            f"{_int(item.get('iteration', 0))} | "
            f"`{_md(item.get('action_id') or 'none')}` | "
            f"`{_md(item.get('execution_status') or 'none')}` | "
            f"{str(bool(item.get('executed', False))).lower()} | "
            f"{_md(item.get('verify_summary') or 'none')} | "
            f"{_md(item.get('next_action') or 'none')} |"
        )
    if not _list(payload.get("actions")):
        lines.append("| 0 | `none` | `planned` | false | none | none |")
    return "\n".join(lines) + "\n"


def _memory_from_summary(
    summary: dict[str, Any],
    *,
    session: dict[str, Any],
    existing_memory: dict[str, Any],
) -> dict[str, Any]:
    turns = _list(existing_memory.get("turns"))
    intents = _list(existing_memory.get("user_intent_history"))
    constraints = _list(existing_memory.get("constraints"))
    repair_strategy_preferences = _list(
        existing_memory.get("repair_strategy_preferences")
    )
    patch_attempts = _patch_attempts_from_summary(summary)
    memory = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "session_id": session["session_id"],
        "repo_profile": _repo_profile_from_summary(summary),
        "graph_memory": _graph_memory_from_summary(summary),
        "topk_suspicious_functions": _topk_from_summary(summary),
        "test_results": _test_results_from_summary(summary),
        "blocker_evolution": _merge_blockers(
            _list(existing_memory.get("blocker_evolution")),
            _blockers_from_summary(summary),
        ),
        "patch_attempt_history": _merge_patch_attempts(
            _list(existing_memory.get("patch_attempt_history")),
            patch_attempts,
        ),
        "reflection_trace": _reflection_from_summary(summary),
        "agent_controller_history": _controller_history_from_summary(summary),
        "execution_trace": _execution_trace_from_summary(summary),
        "final_report_summary": _final_report_summary_from_summary(summary),
        "user_intent_history": intents,
        "constraints": constraints,
        "repair_strategy_preferences": repair_strategy_preferences,
        "turns": turns,
        "conversation_summary": _dict(existing_memory.get("conversation_summary")),
        "conversation_compaction": _dict(existing_memory.get("conversation_compaction")),
        "evidence_memory": _dict(existing_memory.get("evidence_memory")),
        "latest_memory_retrieval": _dict(existing_memory.get("latest_memory_retrieval")),
        "deleted_memory_ids": _list(existing_memory.get("deleted_memory_ids")),
        "turn_count": total_turn_count(existing_memory) if existing_memory else len(turns),
        "current_status": str(summary.get("status") or ""),
        "created_at": existing_memory.get("created_at") or session.get("created_at"),
        "updated_at": _now(),
    }
    memory["memory_layers"] = _memory_layers_from_memory(memory, session=session)
    return _redact(memory)


def _memory_layers_from_memory(
    memory: dict[str, Any],
    *,
    session: dict[str, Any],
) -> dict[str, Any]:
    repo = _dict(memory.get("repo_profile"))
    graph = _dict(memory.get("graph_memory"))
    tests = _dict(memory.get("test_results"))
    blockers = [_dict(item) for item in _list(memory.get("blocker_evolution"))]
    patches = [_dict(item) for item in _list(memory.get("patch_attempt_history"))]
    failed_patches = [item for item in patches if not _patch_attempt_passed(item)]
    successful_patches = [item for item in patches if _patch_attempt_passed(item)]
    constraints = [str(item) for item in _list(memory.get("constraints"))]
    repair_strategy_preferences = [
        str(item)
        for item in _list(memory.get("repair_strategy_preferences"))
        if str(item or "")
    ]
    intents = [_dict(item) for item in _list(memory.get("user_intent_history"))]
    topk = [_dict(item) for item in _list(memory.get("topk_suspicious_functions"))]
    latest_failure_category = (
        str(tests.get("failure_category") or "")
        or str(_dict(failed_patches[-1]).get("failure_type") or "")
        if failed_patches
        else str(tests.get("failure_category") or "")
    )
    evidence_memory = _dict(memory.get("evidence_memory"))
    evidence_records = [_dict(item) for item in _list(evidence_memory.get("records"))]
    working_records = [
        item
        for item in evidence_records
        if item.get("layer") == "working_memory" and item.get("status") == "active"
    ]
    cross_repo_records = [
        item
        for item in evidence_records
        if item.get("layer") == "cross_repo_pattern_memory"
        and item.get("status") == "active"
        and _dict(item.get("validation")).get("status") == "verified"
        and _dict(item.get("validation")).get("authority") == "sandbox_pytest"
    ]
    working_state = _dict(_dict(working_records[-1]).get("content")) if working_records else {}
    cross_repo_layer = _cross_repo_pattern_layer(cross_repo_records)
    layers = {
        "schema_version": 1,
        "working_memory": {
            "status": "ready",
            "record_count": len(working_records),
            "current_stage": str(
                _dict(session.get("current_state")).get("current_stage")
                or _dict(working_state.get("current_state")).get("current_stage")
                or ""
            ),
            "latest_intent": str(working_state.get("latest_intent") or ""),
            "latest_action": str(working_state.get("latest_action") or ""),
            "latest_verification": str(working_state.get("latest_verification") or ""),
            "latest_replan": str(working_state.get("latest_replan") or ""),
            "retrieved_memory_ids": _list(
                _dict(memory.get("latest_memory_retrieval")).get(
                    "selected_memory_ids"
                )
            ),
        },
        "session_memory": {
            "status": "ready",
            "session_id": str(session.get("session_id") or memory.get("session_id") or ""),
            "turn_count": total_turn_count(memory),
            "retained_turn_count": len(_list(memory.get("turns"))),
            "compacted_turn_count": _int(
                _dict(memory.get("conversation_summary")).get(
                    "compacted_turn_count"
                )
            ),
            "current_status": str(memory.get("current_status") or session.get("status") or ""),
            "active_scope": str(memory.get("active_scope") or ""),
            "execution_stopped": bool(memory.get("execution_stopped")),
            "constraints": constraints,
            "repair_strategy_preferences": repair_strategy_preferences,
            "intent_count": len(intents),
            "last_intent": str(_dict(intents[-1]).get("intent") or "") if intents else "",
            "report_paths": _dict(session.get("report_paths")),
        },
        "repo_memory": {
            "status": "ready" if repo else "missing",
            "repo": str(repo.get("repo") or session.get("repo") or ""),
            "repo_spec": str(repo.get("repo_spec") or session.get("repo_spec") or ""),
            "repository_ref": str(repo.get("repository_ref") or session.get("repository_ref") or ""),
            "layout": str(repo.get("layout") or ""),
            "function_count": _int(repo.get("function_count", 0)),
            "class_count": _int(repo.get("class_count", 0)),
            "loc": _int(repo.get("loc", 0)),
            "test_command": str(tests.get("command") or ""),
            "test_runner": str(tests.get("runner") or ""),
            "test_status": str(tests.get("status") or ""),
            "program_graph_available": bool(graph.get("program_graph_available", False)),
            "program_graph_nodes": _int(graph.get("program_graph_nodes", 0)),
            "program_graph_edges": _int(graph.get("program_graph_edges", 0)),
            "top_suspicious_functions": topk[:10],
        },
        "repair_memory": {
            "status": "ready",
            "patch_attempt_count": len(patches),
            "failed_patch_count": len(failed_patches),
            "successful_patch_count": len(successful_patches),
            "failed_patch_fingerprints": _unique_strings(
                str(item.get("diff_fingerprint") or "")
                for item in failed_patches
            )[:20],
            "avoid_fixed_source_fingerprints": _unique_strings(
                str(item.get("fixed_source_fingerprint") or "")
                for item in failed_patches
            )[:20],
            "successful_repair_strategies": _successful_repair_strategies(
                successful_patches,
                memory,
            ),
            "latest_failure_category": latest_failure_category,
            "latest_failure_signal": str(tests.get("failure_signal") or ""),
            "reflection_trace": _dict(memory.get("reflection_trace")),
            "avoid_repeated_patch_generation": bool(failed_patches),
            "user_constraints": constraints,
            "strategy_preferences": repair_strategy_preferences,
        },
        "cross_repo_pattern_memory": cross_repo_layer,
    }
    # Preserve the V1 report key while making verified cross-repo memory the
    # only source of long-term repair patterns.
    layers["long_term_pattern_memory"] = cross_repo_layer
    return layers


def _cross_repo_pattern_layer(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    source_repositories = {
        str(source.get("repo") or "")
        for record in records
        for source in [_dict(item) for item in _list(record.get("source_versions"))]
        if str(source.get("repo") or "")
    }
    patterns = []
    for record in records[:20]:
        content = _dict(record.get("content"))
        patterns.append(
            {
                "memory_id": str(record.get("memory_id") or ""),
                "pattern": ":".join(
                    item
                    for item in [
                        str(content.get("failure_type") or ""),
                        str(content.get("target_shape") or ""),
                        str(content.get("generator") or ""),
                    ]
                    if item
                )
                or "verified_repair_pattern",
                "count": _int(record.get("evidence_count", 1)),
                "evidence": str(record.get("evidence_path") or ""),
                "confidence": _float(record.get("confidence", 0.0)),
            }
        )
    return {
        "status": "ready",
        "promotion_policy": "sandbox_verified_only",
        "pattern_count": len(patterns),
        "source_repository_count": len(source_repositories),
        "verified_patterns": patterns,
        "bug_patterns": patterns,
        "strategy_hints": _unique_strings(
            str(_dict(record.get("content")).get("generator") or "")
            for record in records
        ),
        "reuse_policy": {
            "scope": "cross_repository",
            "used_by_patch_generation": bool(patterns),
            "used_by_reflection": bool(patterns),
            "used_by_replan": bool(patterns),
            "requires_sandbox_verification": True,
        },
    }


def _memory_reuse_contract(layers: dict[str, Any]) -> dict[str, Any]:
    session_layer = _dict(layers.get("session_memory"))
    repair_layer = _dict(layers.get("repair_memory"))
    repo_layer = _dict(layers.get("repo_memory"))
    pattern_layer = _dict(layers.get("cross_repo_pattern_memory"))
    failed_count = _int(repair_layer.get("failed_patch_count", 0))
    constraints = _list(session_layer.get("constraints"))
    strategy_preferences = _list(repair_layer.get("strategy_preferences"))
    test_command = str(repo_layer.get("test_command") or "")
    pattern_count = _int(pattern_layer.get("pattern_count", 0))
    return {
        "feeds_patch_generation": {
            "enabled": bool(
                failed_count
                or constraints
                or strategy_preferences
                or test_command
            ),
            "evidence": (
                f"failed_patches={failed_count}, constraints={len(constraints)}, "
                f"strategies={len(strategy_preferences)}, "
                f"test_command={test_command or 'none'}"
            ),
        },
        "feeds_reflection": {
            "enabled": bool(failed_count or repair_layer.get("latest_failure_category")),
            "evidence": (
                f"latest_failure={repair_layer.get('latest_failure_category') or 'none'}, "
                f"failed_patches={failed_count}"
            ),
        },
        "feeds_replan": {
            "enabled": True,
            "evidence": (
                f"repo_status={repo_layer.get('status') or 'none'}, "
                f"patterns={pattern_count}"
            ),
        },
        "avoids_repeated_failed_patches": {
            "enabled": failed_count > 0,
            "evidence": (
                f"avoid_diff_fingerprints={len(_list(repair_layer.get('failed_patch_fingerprints')))}"
            ),
        },
        "preserves_user_constraints": {
            "enabled": bool(constraints),
            "evidence": ", ".join(str(item) for item in constraints) or "none",
        },
    }


def _agent_decision_report(
    *,
    session_id: str,
    repo: str,
    controller: dict[str, Any],
    execution_trace: dict[str, Any],
    decision_timeline: dict[str, Any],
    memory_layers: dict[str, Any],
) -> dict[str, Any]:
    selected = _dict(controller.get("selected_action"))
    llm_advisor = _dict(controller.get("llm_replan_advisor"))
    llm_decision = _dict(llm_advisor.get("planner_decision"))
    llm_gate = _dict(llm_advisor.get("safety_gate"))
    actions = [_dict(item) for item in _list(execution_trace.get("actions"))]
    return {
        "schema_version": 1,
        "status": "pass" if selected and execution_trace else "warning",
        "reason": (
            "agent_decision_report_ready"
            if selected and execution_trace
            else "agent_decision_report_incomplete"
        ),
        "session_id": session_id,
        "repo": repo,
        "control_loop": _list(controller.get("control_loop")) or LOOP,
        "controller_authority": "rules_and_sandbox_gate_decide",
        "selected_action": {
            "id": str(selected.get("id") or ""),
            "phase": str(selected.get("phase") or ""),
            "tool": str(selected.get("tool") or ""),
            "reason": str(selected.get("reason") or ""),
            "confidence": selected.get("confidence"),
            "risk": str(selected.get("risk") or ""),
            "command": str(selected.get("command") or ""),
            "next_plan": str(selected.get("next_plan") or ""),
            "executable_now": bool(selected.get("executable_now", False)),
        },
        "llm_planner": {
            "enabled": bool(llm_advisor.get("enabled", False)),
            "status": str(llm_advisor.get("status") or ""),
            "reason": str(llm_advisor.get("reason") or ""),
            "selected_action": str(llm_decision.get("selected_action") or ""),
            "confidence": llm_decision.get("confidence"),
            "risk": str(llm_decision.get("risk") or ""),
            "required_evidence": _list(llm_decision.get("required_evidence")),
            "memory_used": _list(llm_decision.get("memory_used")),
            "proposal_source": str(llm_decision.get("proposal_source") or ""),
            "next_plan": str(llm_decision.get("next_plan") or ""),
            "fallback_to_rule_planner": bool(
                llm_advisor.get("fallback_to_rule_planner", False)
            ),
            "fallback_reason": str(llm_advisor.get("fallback_reason") or ""),
            "safety_gate": llm_gate,
        },
        "execution": {
            "status": str(execution_trace.get("status") or ""),
            "action_count": _int(execution_trace.get("action_count", 0)),
            "executed_action_count": _int(
                execution_trace.get("executed_action_count", 0)
            ),
            "verified_action_count": _int(
                execution_trace.get("verified_action_count", 0)
            ),
            "blocked_action_count": _int(
                execution_trace.get("blocked_action_count", 0)
            ),
            "real_execution_answer": str(
                execution_trace.get("real_execution_answer") or ""
            ),
        },
        "decision_timeline": decision_timeline,
        "memory_layer_statuses": {
            key: str(_dict(memory_layers.get(key)).get("status") or "missing")
            for key in MEMORY_LAYERS
        },
        "actions": actions,
    }


def _decision_timeline_from_memory(memory: dict[str, Any]) -> dict[str, Any]:
    turns = [_dict(item) for item in _list(memory.get("turns"))]
    latest = turns[-1] if turns else {}
    return {
        "source": "agent_memory",
        "step_count": len(turns),
        "complete_step_count": len(turns),
        "latest_loop": _dict(latest.get("loop")),
    }


def _analysis_turn(summary: dict[str, Any], *, raw_argv: list[str] | None) -> dict[str, Any]:
    controller = _dict(summary.get("agent_controller"))
    selected = _dict(controller.get("selected_action"))
    readiness = _dict(summary.get("analysis_readiness"))
    loop = {
        "observe": {
            "status": "complete",
            "evidence": (
                f"repo={summary.get('repo') or ''}; "
                f"status={summary.get('status') or ''}; "
                f"blocker={readiness.get('blocker') or 'none'}"
            ),
        },
        "plan": {
            "status": "complete",
            "evidence": str(selected.get("reason") or "initial repository analysis completed"),
            "selected_action": str(selected.get("id") or ""),
        },
        "act": {
            "status": "complete",
            "action_id": str(selected.get("id") or "run_github_repo_intelligence"),
            "evidence": str(selected.get("command") or "analysis artifacts written"),
        },
        "verify": {
            "status": str(summary.get("status") or "unknown"),
            "evidence": str(_dict(summary.get("acceptance_gate")).get("status") or ""),
        },
        "reflect": {
            "status": "complete",
            "evidence": str(_dict(summary.get("reflection_trace")).get("reason") or ""),
        },
        "replan": {
            "status": "complete",
            "evidence": str(_dict(controller.get("replan")).get("reason") or ""),
            "next_action": str(
                _dict(controller.get("replan")).get("next_action")
                or _dict(summary.get("agent_auto_stop_state")).get("recommended_next_action")
                or summary.get("next_action")
                or ""
            ),
        },
    }
    return {
        "source": "analysis_run",
        "created_at": _now(),
        "intent": "initial_analysis",
        "message": " ".join(raw_argv or []),
        "loop": loop,
        "memory_usage_evidence": {
            "writes_repo_profile": True,
            "writes_topk": bool(_topk_from_summary(summary)),
            "writes_test_result": bool(_test_results_from_summary(summary)),
            "writes_patch_attempts": bool(_patch_attempts_from_summary(summary)),
        },
    }


def _conversation_turn(
    *,
    session: dict[str, Any],
    memory: dict[str, Any],
    intent: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    evidence = _memory_usage_evidence(memory)
    loop = {
        "observe": {
            "status": "complete",
            "evidence": (
                f"loaded_session={session.get('session_id')}; "
                f"repo_profile={evidence['repo_profile_loaded']}; "
                f"turns={evidence['prior_turn_count']}"
            ),
        },
        "plan": {
            "status": "complete",
            "evidence": (
                f"intent={intent.get('intent')}; "
                f"action={decision.get('action_id')}; "
                f"confidence={intent.get('confidence')}; "
                f"source={intent.get('source') or 'unknown'}"
            ),
        },
        "act": {
            "status": "executed" if decision.get("executed") else "prepared",
            "action_id": str(decision.get("action_id") or ""),
            "evidence": str(decision.get("command") or decision.get("answer") or ""),
        },
        "verify": {
            "status": "pass",
            "evidence": "session memory updated and session report regenerated",
        },
        "reflect": {
            "status": "complete",
            "evidence": str(decision.get("reflection") or ""),
        },
        "replan": {
            "status": "complete",
            "evidence": str(decision.get("replan_reason") or ""),
            "next_action": str(decision.get("next_action") or ""),
        },
    }
    return {
        "source": "chat",
        "created_at": _now(),
        "intent": str(intent.get("intent") or ""),
        "intent_source": str(intent.get("source") or "unknown"),
        "intent_router_audit": _redact(_dict(intent.get("router_audit"))),
        "message": str(intent.get("message") or ""),
        "loop": loop,
        "decision": decision,
        "memory_usage_evidence": evidence,
    }


def _decision_for_intent(
    session: dict[str, Any],
    memory: dict[str, Any],
    intent: dict[str, Any],
    *,
    execute: bool,
) -> dict[str, Any]:
    intent_name = str(intent.get("intent") or INTENT_INSPECT_STATUS)
    executable_intents = {
        INTENT_RERUN_TESTS,
        INTENT_NARROW_SCOPE,
        INTENT_CHANGE_REPAIR_STRATEGY,
        INTENT_CONTINUE_REPAIR,
    }
    if (
        execute
        and bool(memory.get("execution_stopped"))
        and intent_name in executable_intents
        and not _explicit_execution_resume(intent)
    ):
        return _execution_stopped_decision(intent_name)
    if intent_name == INTENT_EXPLAIN_FAILURE:
        return _explain_failure_decision(memory)
    if intent_name == INTENT_RERUN_TESTS:
        return _rerun_tests_decision(session, memory, execute=execute)
    if intent_name == INTENT_NARROW_SCOPE:
        return _narrow_scope_decision(session, memory, intent, execute=execute)
    if intent_name == INTENT_CHANGE_CONSTRAINTS:
        return _change_constraints_decision(memory, intent)
    if intent_name == INTENT_CHANGE_REPAIR_STRATEGY:
        return _change_repair_strategy_decision(session, memory, intent, execute=execute)
    if intent_name == INTENT_GENERATE_REPORT:
        return _generate_report_decision(session)
    if intent_name == INTENT_CONTINUE_REPAIR:
        return _continue_repair_decision(session, memory, execute=execute)
    if intent_name == INTENT_INSPECT_FUNCTION:
        return _inspect_function_decision(memory, intent)
    if intent_name == INTENT_EXPLAIN_LOCALIZATION:
        return _explain_localization_decision(memory, intent)
    if intent_name == INTENT_COMPARE_PATCH_CANDIDATES:
        return _compare_patch_candidates_decision(memory, intent)
    if intent_name == INTENT_ROLLBACK_LAST_ACTION:
        return _rollback_last_action_decision(memory)
    if intent_name == INTENT_STOP_EXECUTION:
        return _stop_execution_decision()
    if intent_name == INTENT_ASK_FOR_CLARIFICATION:
        return _ask_for_clarification_decision(intent)
    if intent_name == INTENT_INSPECT_STATUS:
        return _inspect_status_decision(memory)
    return _ask_for_clarification_decision(
        {
            **intent,
            "reason": f"unsupported intent: {intent_name}",
        }
    )


def _inspect_function_decision(
    memory: dict[str, Any],
    intent: dict[str, Any],
) -> dict[str, Any]:
    requested = str(intent.get("function") or "").strip()
    topk = [_dict(item) for item in _list(memory.get("topk_suspicious_functions"))]
    match = next(
        (
            item
            for item in topk
            if str(item.get("function") or "") == requested
            or str(item.get("function") or "").endswith(f".{requested}")
        ),
        {},
    )
    if not match:
        known = ", ".join(
            str(item.get("function") or "") for item in topk[:5] if item.get("function")
        )
        return {
            "action_id": "inspect_function_from_memory",
            "answer": (
                f"Function {requested or '<missing>'} is not present in the stored Top-k. "
                f"Known functions: {known or 'none'}."
            ),
            "executed": False,
            "reflection": "No matching function-level evidence was found in session memory.",
            "next_action": "clarify_function_or_refresh_localization",
            "replan_reason": "Function inspection requires an exact stored function target.",
        }
    answer = (
        f"Function {match.get('function')} is in {match.get('file') or 'an unknown file'}; "
        f"FinalScore={_float(match.get('final_score', 0.0)):.4f}; "
        f"evidence={match.get('why') or 'no explanation stored'}."
    )
    return {
        "action_id": "inspect_function_from_memory",
        "answer": answer,
        "executed": False,
        "function": requested,
        "evidence": match,
        "reflection": "Function evidence was read from persisted Top-k memory.",
        "next_action": "explain_localization_or_continue_repair",
        "replan_reason": "User requested function-level inspection before another action.",
    }


def _explain_localization_decision(
    memory: dict[str, Any],
    intent: dict[str, Any],
) -> dict[str, Any]:
    requested = str(intent.get("function") or "").strip()
    topk = [_dict(item) for item in _list(memory.get("topk_suspicious_functions"))]
    selected = [
        item
        for item in topk
        if not requested
        or str(item.get("function") or "") == requested
        or str(item.get("function") or "").endswith(f".{requested}")
    ][:5]
    if not selected:
        return {
            "action_id": "explain_localization_from_memory",
            "answer": "No matching localization evidence is stored for that function.",
            "executed": False,
            "reflection": "Stored Top-k evidence could not satisfy the requested target.",
            "next_action": "clarify_function_or_refresh_localization",
            "replan_reason": "Localization explanation needs a stored ranking entry.",
        }
    rows = []
    for rank, item in enumerate(selected, start=1):
        rows.append(
            f"#{rank} {item.get('function')}: FinalScore="
            f"{_float(item.get('final_score', 0.0)):.4f}; "
            f"reason={item.get('why') or 'score breakdown unavailable'}"
        )
    return {
        "action_id": "explain_localization_from_memory",
        "answer": "Localization evidence: " + " | ".join(rows),
        "executed": False,
        "evidence": selected,
        "reflection": "Explained the persisted Top-k ranking without recomputing it.",
        "next_action": "inspect_function_or_continue_repair",
        "replan_reason": "User requested ranking evidence before repair.",
    }


def _compare_patch_candidates_decision(
    memory: dict[str, Any],
    intent: dict[str, Any],
) -> dict[str, Any]:
    attempts = [_dict(item) for item in _list(memory.get("patch_attempt_history"))]
    candidate_ids = {
        str(item) for item in _list(intent.get("candidate_ids")) if str(item).strip()
    }
    if candidate_ids:
        attempts = [
            item
            for item in attempts
            if str(item.get("candidate_id") or item.get("id") or "") in candidate_ids
        ]
    if not attempts:
        return {
            "action_id": "compare_patch_candidates_from_memory",
            "answer": "No matching patch candidates are stored in this session.",
            "executed": False,
            "reflection": "Patch comparison requires at least one persisted candidate.",
            "next_action": "generate_patch_candidates",
            "replan_reason": "No candidate evidence was available for comparison.",
        }
    summaries = []
    for index, item in enumerate(attempts[:10], start=1):
        candidate_id = str(item.get("candidate_id") or item.get("id") or f"candidate_{index}")
        status = str(
            item.get("status")
            or item.get("sandbox_status")
            or _dict(item.get("validation")).get("status")
            or "unknown"
        )
        target = str(item.get("target_function") or item.get("function_name") or "unknown")
        summaries.append(f"{candidate_id}: target={target}, status={status}")
    return {
        "action_id": "compare_patch_candidates_from_memory",
        "answer": "Patch comparison: " + " | ".join(summaries),
        "executed": False,
        "candidate_count": len(attempts),
        "reflection": "Compared persisted validation outcomes; no patch was applied.",
        "next_action": "select_candidate_or_generate_alternative",
        "replan_reason": "Candidate evidence should be compared before validation or retry.",
    }


def _rollback_last_action_decision(memory: dict[str, Any]) -> dict[str, Any]:
    reversible = [
        _dict(turn)
        for turn in reversed(_list(memory.get("turns")))
        if _dict(_dict(turn).get("decision")).get("rollback_artifact")
    ]
    if not reversible:
        return {
            "action_id": "rollback_last_action",
            "answer": (
                "Rollback was not executed because this session has no verified "
                "rollback artifact. Repository changes must not be guessed or reversed blindly."
            ),
            "executed": False,
            "requires_confirmation": True,
            "blocked": True,
            "reflection": "No reversible transaction was recorded.",
            "next_action": "inspect_git_diff_and_request_confirmation",
            "replan_reason": "Rollback is destructive and requires an auditable restore point.",
        }
    return {
        "action_id": "rollback_last_action",
        "answer": "A rollback artifact exists, but explicit confirmation is required before use.",
        "executed": False,
        "requires_confirmation": True,
        "rollback_artifact": _dict(reversible[0].get("decision")).get("rollback_artifact"),
        "reflection": "Rollback remains prepared but unexecuted.",
        "next_action": "confirm_rollback_artifact",
        "replan_reason": "Destructive state restoration requires confirmation.",
    }


def _stop_execution_decision() -> dict[str, Any]:
    return {
        "action_id": "stop_execution",
        "answer": "Execution is stopped for this session. Read-only inspection remains available.",
        "executed": False,
        "execution_stopped": True,
        "reflection": "The stop request was persisted without launching another command.",
        "next_action": "wait_for_explicit_execution_request",
        "replan_reason": "User requested that executable actions stop.",
    }


def _execution_stopped_decision(intent_name: str) -> dict[str, Any]:
    return {
        "action_id": "execution_stopped_gate",
        "answer": (
            "This session is in stopped-execution state, so the requested command "
            "was not started. Ask to resume execution explicitly before retrying it."
        ),
        "requested_intent": intent_name,
        "executed": False,
        "blocked": True,
        "reflection": "The persisted stop flag prevented process execution.",
        "next_action": "await_explicit_execution_resume",
        "replan_reason": "Execution remains stopped by a prior user request.",
    }


def _ask_for_clarification_decision(intent: dict[str, Any]) -> dict[str, Any]:
    reason = str(intent.get("reason") or "the requested action is ambiguous")
    return {
        "action_id": "ask_for_clarification",
        "answer": (
            "I need one specific next action before continuing. "
            f"Reason: {reason}. Examples: inspect status, explain localization, "
            "rerun tests, continue repair, or generate report."
        ),
        "executed": False,
        "reflection": "No action was executed because the intent was ambiguous or incomplete.",
        "next_action": "await_clarified_user_intent",
        "replan_reason": reason,
    }


def _explain_failure_decision(memory: dict[str, Any]) -> dict[str, Any]:
    blockers = _list(memory.get("blocker_evolution"))
    test_result = _dict(memory.get("test_results"))
    latest = _dict(blockers[-1]) if blockers else {}
    reason = (
        latest.get("blocker")
        or test_result.get("failure_category")
        or test_result.get("status")
        or "no failure recorded"
    )
    answer = (
        "Latest failure/blocker: "
        f"{reason}. Next action: {latest.get('next_action') or 'inspect session report'}."
    )
    return {
        "action_id": "explain_failure_from_memory",
        "answer": answer,
        "executed": True,
        "reflection": "Explained from blocker and test-result memory.",
        "next_action": latest.get("next_action") or "continue_repair_or_rerun_tests",
        "replan_reason": "User asked for failure explanation before further action.",
    }


def _rerun_tests_decision(
    session: dict[str, Any],
    memory: dict[str, Any],
    *,
    execute: bool,
) -> dict[str, Any]:
    action_id = "rerun_repository_tests_from_session"
    test_result = _dict(memory.get("test_results"))
    command = str(test_result.get("command") or "python -m pytest")
    cwd = str(test_result.get("execution_cwd") or session.get("output_dir") or "")
    registry_gate = _action_registry_gate(action_id, command=command)
    execution_result: dict[str, Any] = {}
    answer = f"Prepared repository test rerun: {command}"
    if execute:
        timeout = max(10, _int(_dict(session.get("run_config")).get("repository_test_timeout", 60)))
        execution_result = _execute_registered_command(
            action_id,
            command,
            cwd=cwd,
            timeout=timeout,
        )
        answer = (
            f"Executed repository test rerun: {command}; "
            f"status={execution_result.get('status')}; "
            f"returncode={execution_result.get('returncode')}."
        )
    return {
        "action_id": action_id,
        "canonical_action_id": registry_gate.get("canonical_action_id", ""),
        "action_registry_gate": registry_gate,
        "answer": answer,
        "command": command,
        "working_dir": cwd,
        "executed": bool(execute),
        "execution_result": execution_result,
        "reflection": "Uses stored repository test command instead of rediscovering from zero.",
        "next_action": "verify_test_result_then_replan",
        "replan_reason": "Test rerun should refresh dynamic evidence before repair.",
    }


def _narrow_scope_decision(
    session: dict[str, Any],
    memory: dict[str, Any],
    intent: dict[str, Any],
    *,
    execute: bool = False,
) -> dict[str, Any]:
    action_id = "narrow_repository_scope"
    scope = str(intent.get("scope") or "")
    command = _base_agent_command(session)
    if scope:
        command = f"{command} --include {scope}"
    registry_gate = _action_registry_gate(action_id, command=command)
    execution_result: dict[str, Any] = {}
    answer = f"Prepared scoped analysis for {scope or 'requested scope'}."
    if execute:
        timeout = max(60, _int(_dict(session.get("run_config")).get("repository_test_timeout", 60)))
        execution_result = _execute_registered_command(
            action_id,
            command,
            cwd="",
            timeout=timeout,
        )
        answer = (
            f"Executed scoped analysis for {scope or 'requested scope'}; "
            f"status={execution_result.get('status')}; "
            f"returncode={execution_result.get('returncode')}."
        )
    return {
        "action_id": action_id,
        "canonical_action_id": registry_gate.get("canonical_action_id", ""),
        "action_registry_gate": registry_gate,
        "answer": answer,
        "command": command,
        "executed": bool(execute),
        "execution_result": execution_result,
        "reflection": "Scope constraint stored for future planning.",
        "next_action": "rerun_scoped_analysis",
        "replan_reason": "Future Observe step should respect the stored scope constraint.",
    }


def _change_constraints_decision(
    memory: dict[str, Any],
    intent: dict[str, Any],
) -> dict[str, Any]:
    constraints = _list(intent.get("constraints"))
    return {
        "action_id": "update_user_constraints",
        "answer": (
            "Updated constraints: "
            + (", ".join(str(item) for item in constraints) if constraints else "none")
        ),
        "executed": True,
        "reflection": "Constraints will be loaded by future repair planning.",
        "next_action": "continue_with_constraints",
        "replan_reason": "User changed repair constraints.",
    }


def _change_repair_strategy_decision(
    session: dict[str, Any],
    memory: dict[str, Any],
    intent: dict[str, Any],
    *,
    execute: bool,
) -> dict[str, Any]:
    action_id = "change_repair_strategy"
    strategy = str(intent.get("strategy") or "use an alternative repair strategy")
    memory_path = str(session.get("memory_path") or "")
    command = (
        f"{_base_agent_command(session)} --repository-patch-generation-mode hybrid "
        "--auto-controller-actions --auto-controller-max-actions 4"
    )
    environment = {
        "CIA_AGENT_PATCH_MEMORY": memory_path,
        "CIA_AGENT_REPAIR_STRATEGY": strategy,
    }
    registry_gate = _action_registry_gate(action_id, command=command)
    execution_result: dict[str, Any] = {}
    answer = (
        f"Prepared alternative repair strategy: {strategy}. "
        "Future patch generation will read the stored failed patch memory and "
        "avoid repeating prior diffs."
    )
    if execute:
        timeout = max(
            60,
            _int(_dict(session.get("run_config")).get("repository_test_timeout", 60)) * 5,
        )
        execution_result = _execute_registered_command(
            action_id,
            command,
            cwd="",
            timeout=timeout,
            env=environment,
        )
        answer = (
            f"Executed alternative repair strategy: {strategy}; "
            f"status={execution_result.get('status')}; "
            f"returncode={execution_result.get('returncode')}."
        )
    return {
        "action_id": action_id,
        "canonical_action_id": registry_gate.get("canonical_action_id", ""),
        "action_registry_gate": registry_gate,
        "answer": answer,
        "command": command,
        "environment": environment,
        "strategy": strategy,
        "executed": bool(execute),
        "execution_result": execution_result,
        "reflection": "User requested a different repair approach.",
        "next_action": "generate_alternative_patch_candidate",
        "replan_reason": "Repair planning should avoid previous failed strategy shape.",
    }


def _generate_report_decision(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "action_id": "generate_session_report",
        "answer": f"Session report is available at {session.get('session_report_path')}.",
        "executed": True,
        "reflection": "Report regenerated from persisted memory.",
        "next_action": "inspect_or_continue_repair",
        "replan_reason": "User requested an auditable report.",
    }


def _continue_repair_decision(
    session: dict[str, Any],
    memory: dict[str, Any],
    *,
    execute: bool,
) -> dict[str, Any]:
    action_id = "continue_repair_with_patch_memory"
    topk = _list(memory.get("topk_suspicious_functions"))
    target = _dict(topk[0]).get("function") if topk else "top-ranked function"
    memory_path = str(session.get("memory_path") or "")
    command = (
        f"{_base_agent_command(session)} --repository-patch-generation-mode hybrid "
        "--auto-controller-actions --auto-controller-max-actions 4"
    )
    attempts = len(_list(memory.get("patch_attempt_history")))
    environment = {
        "CIA_AGENT_PATCH_MEMORY": memory_path,
    }
    registry_gate = _action_registry_gate(action_id, command=command)
    execution_result: dict[str, Any] = {}
    answer = (
        f"Prepared repair continuation for {target}; "
        f"{attempts} previous patch attempts will be avoided via "
        "CIA_AGENT_PATCH_MEMORY."
    )
    if execute:
        timeout = max(60, _int(_dict(session.get("run_config")).get("repository_test_timeout", 60)) * 5)
        execution_result = _execute_registered_command(
            action_id,
            command,
            cwd="",
            timeout=timeout,
            env=environment,
        )
        answer = (
            f"Executed repair continuation for {target}; "
            f"status={execution_result.get('status')}; "
            f"returncode={execution_result.get('returncode')}."
        )
    return {
        "action_id": action_id,
        "canonical_action_id": registry_gate.get("canonical_action_id", ""),
        "action_registry_gate": registry_gate,
        "answer": answer,
        "command": command,
        "environment": environment,
        "executed": bool(execute),
        "execution_result": execution_result,
        "reflection": "Reads failed patch memory before generating a new repair plan.",
        "next_action": "generate_or_validate_next_patch",
        "replan_reason": "Continue repair using Top-k, blocker, and patch-attempt memory.",
    }


def _inspect_status_decision(memory: dict[str, Any]) -> dict[str, Any]:
    profile = _dict(memory.get("repo_profile"))
    blockers = _list(memory.get("blocker_evolution"))
    latest = _dict(blockers[-1]) if blockers else {}
    return {
        "action_id": "inspect_session_status",
        "answer": (
            f"Session status: {profile.get('status') or 'unknown'}; "
            f"latest blocker: {latest.get('blocker') or 'none'}."
        ),
        "executed": True,
        "reflection": "Status summarized from persisted memory.",
        "next_action": latest.get("next_action") or "ask_user_for_next_intent",
        "replan_reason": "No specific repair/test/report intent was detected.",
    }


def _resume_answer(session: dict[str, Any], memory: dict[str, Any]) -> str:
    profile = _dict(memory.get("repo_profile"))
    topk = _list(memory.get("topk_suspicious_functions"))
    test_result = _dict(memory.get("test_results"))
    constraints = [str(item) for item in _list(memory.get("constraints"))]
    return (
        f"Resumed session {session.get('session_id')} for {session.get('repo')}. "
        f"Status={profile.get('status') or session.get('status')}; "
        f"topk={len(topk)}; tests={test_result.get('status') or 'unknown'}; "
        f"constraints={len(constraints)}; "
        f"execution_stopped={str(bool(memory.get('execution_stopped'))).lower()}."
    )


def _apply_intent_to_memory(memory: dict[str, Any], intent: dict[str, Any]) -> None:
    history = _list(memory.get("user_intent_history"))
    history.append(_redact(intent))
    memory["user_intent_history"] = history
    if intent.get("intent") == INTENT_CHANGE_CONSTRAINTS:
        constraints = _list(memory.get("constraints"))
        for item in _list(intent.get("constraints")):
            if item not in constraints:
                constraints.append(item)
        memory["constraints"] = constraints
    if intent.get("intent") == INTENT_NARROW_SCOPE and intent.get("scope"):
        memory["active_scope"] = str(intent.get("scope") or "")
    if intent.get("intent") == INTENT_CHANGE_REPAIR_STRATEGY:
        preferences = _list(memory.get("repair_strategy_preferences"))
        strategy = str(intent.get("strategy") or intent.get("message") or "").strip()
        if strategy and strategy not in preferences:
            preferences.append(strategy)
        memory["repair_strategy_preferences"] = preferences
    if intent.get("intent") == INTENT_INSPECT_FUNCTION and intent.get("function"):
        memory["active_function"] = str(intent.get("function") or "")
    if intent.get("intent") == INTENT_STOP_EXECUTION:
        memory["execution_stopped"] = True
    elif _explicit_execution_resume(intent):
        memory["execution_stopped"] = False


def _intent_router_context(
    session: dict[str, Any],
    memory: dict[str, Any],
) -> dict[str, Any]:
    blockers = _list(memory.get("blocker_evolution"))
    latest_blocker = _dict(blockers[-1]) if blockers else {}
    topk = [_dict(item) for item in _list(memory.get("topk_suspicious_functions"))]
    history = [_dict(item) for item in _list(memory.get("user_intent_history"))]
    return {
        "repo": str(session.get("repo") or session.get("repo_spec") or ""),
        "current_status": str(
            memory.get("current_status")
            or _dict(memory.get("repo_profile")).get("status")
            or session.get("status")
            or ""
        ),
        "active_scope": str(memory.get("active_scope") or ""),
        "latest_blocker": str(latest_blocker.get("blocker") or ""),
        "test_status": str(_dict(memory.get("test_results")).get("status") or ""),
        "topk_functions": [
            str(item.get("function") or "") for item in topk[:10] if item.get("function")
        ],
        "constraints": [str(item) for item in _list(memory.get("constraints"))[:10]],
        "repair_strategy_preferences": [
            str(item)
            for item in _list(memory.get("repair_strategy_preferences"))[:10]
        ],
        "recent_intents": [
            str(item.get("intent") or "") for item in history[-10:] if item.get("intent")
        ],
        "execution_stopped": bool(memory.get("execution_stopped")),
    }


def _explicit_execution_resume(intent: dict[str, Any]) -> bool:
    message = str(intent.get("message") or "").lower()
    return any(
        phrase in message
        for phrase in ["恢复执行", "继续执行", "resume execution", "resume running"]
    )


def _memory_usage_evidence(memory: dict[str, Any]) -> dict[str, Any]:
    retrieval = _dict(memory.get("latest_memory_retrieval"))
    return {
        "repo_profile_loaded": bool(memory.get("repo_profile")),
        "topk_loaded": len(_list(memory.get("topk_suspicious_functions"))),
        "test_result_loaded": bool(memory.get("test_results")),
        "patch_attempt_memory_loaded": len(_list(memory.get("patch_attempt_history"))),
        "blocker_memory_loaded": len(_list(memory.get("blocker_evolution"))),
        "repair_strategy_preference_loaded": len(
            _list(memory.get("repair_strategy_preferences"))
        ),
        "constraint_count": len(_list(memory.get("constraints"))),
        "execution_stopped": bool(memory.get("execution_stopped")),
        "prior_turn_count": total_turn_count(memory),
        "retained_turn_count": len(_list(memory.get("turns"))),
        "compacted_turn_count": _int(
            _dict(memory.get("conversation_summary")).get("compacted_turn_count")
        ),
        "retrieval_status": str(retrieval.get("status") or "missing"),
        "retrieved_memory_count": _int(retrieval.get("selected_count", 0)),
        "retrieved_memory_ids": _list(retrieval.get("selected_memory_ids")),
        "retrieved_memory_layers": _list(retrieval.get("selected_layers")),
        "memory_decision_use_counts": _dict(retrieval.get("decision_use_counts")),
        "memory_conflict_group_count": _int(
            _dict(retrieval.get("conflicts")).get("group_count", 0)
        ),
        "memory_conflict_record_count": _int(
            _dict(retrieval.get("conflicts")).get("record_count", 0)
        ),
        "stale_memory_discard_count": _int(
            _dict(retrieval.get("discarded_counts")).get(
                "stale_repository_version",
                0,
            )
        ),
    }


def _session_public_summary(
    session: dict[str, Any],
    memory: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SESSION_SCHEMA_VERSION,
        "session_id": session.get("session_id", ""),
        "repo": session.get("repo", ""),
        "repo_spec": session.get("repo_spec", ""),
        "repository_ref": session.get("repository_ref", ""),
        "output_dir": session.get("output_dir", ""),
        "status": session.get("status", ""),
        "turn_count": total_turn_count(memory),
        "memory_path": session.get("memory_path", ""),
        "session_path": session.get("session_path", ""),
        "session_report_path": session.get("session_report_path", ""),
        "agent_memory_report_json": session.get("agent_memory_report_json", ""),
        "agent_memory_report_path": session.get("agent_memory_report_path", ""),
        "agent_decision_report_json": session.get("agent_decision_report_json", ""),
        "agent_decision_report_path": session.get("agent_decision_report_path", ""),
        "evidence_memory_path": session.get("evidence_memory_path", ""),
        "memory_retrieval_path": session.get("memory_retrieval_path", ""),
        "memory_usage_evidence": _memory_usage_evidence(memory),
    }


def _session_id_for_summary(summary: dict[str, Any]) -> str:
    repo = str(summary.get("repo") or summary.get("repo_spec") or "repo")
    output_dir = str(summary.get("output_dir") or "")
    ref = str(summary.get("repository_ref") or "")
    digest = hashlib.sha256(f"{repo}|{output_dir}|{ref}".encode("utf-8")).hexdigest()
    return f"{_safe_slug(repo)}-{digest[:12]}"


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip().lower()).strip("-")
    return slug[:80] or "session"


def _current_state_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    readiness = _dict(summary.get("analysis_readiness"))
    stop_state = _dict(summary.get("agent_auto_stop_state"))
    return {
        "current_stage": readiness.get("current_stage", ""),
        "next_stage": readiness.get("next_stage", ""),
        "primary_blocker": readiness.get("blocker") or stop_state.get("blocker") or "",
        "next_action": (
            stop_state.get("recommended_next_action")
            or summary.get("next_action")
            or readiness.get("next_action")
            or ""
        ),
    }


def _current_state_from_memory(memory: dict[str, Any]) -> dict[str, Any]:
    blockers = _list(memory.get("blocker_evolution"))
    latest = _dict(blockers[-1]) if blockers else {}
    return {
        "current_stage": _dict(memory.get("repo_profile")).get("static_intelligence_status", ""),
        "next_stage": "",
        "primary_blocker": latest.get("blocker", ""),
        "next_action": latest.get("next_action", ""),
    }


def _run_config_from_summary(
    summary: dict[str, Any],
    *,
    raw_argv: list[str] | None,
) -> dict[str, Any]:
    invocation = _dict(summary.get("agent_invocation"))
    safe_invocation = {
        key: value
        for key, value in invocation.items()
        if key not in _VOLATILE_PATH_KEYS
    }
    return _redact(
        {
            "effective_execution_profile": invocation.get("effective_execution_profile", ""),
            "agent_mode": invocation.get("agent_mode", False),
            "agent_shortcut": invocation.get("agent_shortcut", False),
            "auto_controller_actions": invocation.get("auto_controller_actions", False),
            "auto_controller_max_actions": invocation.get("auto_controller_max_actions", 0),
            "repository_patch_generation_mode": invocation.get(
                "repository_patch_generation_mode", ""
            ),
            "repository_test_timeout": invocation.get("repository_test_timeout", 0),
            "raw_argv": _redact(raw_argv or []),
            "invocation": safe_invocation,
        }
    )


def _report_paths_from_summary(summary: dict[str, Any]) -> dict[str, str]:
    keys = [
        "github_repo_intelligence_json",
        "github_repo_intelligence_markdown",
        "agent_controller_json",
        "agent_controller_markdown",
        "agent_policy_trace_json",
        "agent_policy_trace_markdown",
        "agent_execution_trace_json",
        "agent_execution_trace_markdown",
        "agent_memory_report_json",
        "agent_memory_report_markdown",
        "agent_decision_report_json",
        "agent_decision_report_markdown",
        "repository_test_execution_result_json",
        "repository_test_patch_candidates_json",
        "repository_test_patch_validation_json",
        "reflection_trace_json",
        "fault_localization_json",
    ]
    return {key: str(summary.get(key) or "") for key in keys}


def _repo_profile_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    structure = _dict(summary.get("repository_structure"))
    return {
        "repo": summary.get("repo", ""),
        "repo_spec": summary.get("repo_spec", ""),
        "repository_ref": summary.get("repository_ref", ""),
        "status": summary.get("status", ""),
        "static_intelligence_status": summary.get("static_intelligence_status", ""),
        "static_intelligence_level": summary.get("static_intelligence_level", ""),
        "selected_source_count": _int(summary.get("selected_source_count", 0)),
        "selected_signal_count": _int(summary.get("selected_signal_count", 0)),
        "function_count": _int(structure.get("function_count", 0)),
        "class_count": _int(structure.get("class_count", 0)),
        "loc": _int(structure.get("loc", structure.get("line_count", 0))),
        "layout": structure.get("layout", ""),
        "top_directories": _dict(structure.get("directory_file_counts")),
    }


def _graph_memory_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    structure = _dict(summary.get("repository_structure"))
    repo_graph = _dict(structure.get("repo_graph"))
    program_graph = _dict(repo_graph.get("program_graph"))
    return {
        "program_graph_available": bool(program_graph.get("available", False)),
        "program_graph_nodes": _int(program_graph.get("node_count", 0)),
        "program_graph_edges": _int(program_graph.get("edge_count", 0)),
        "data_flow_edges": _int(program_graph.get("data_flow_edge_count", 0)),
        "cross_function_data_flow_edges": _int(
            program_graph.get("cross_function_data_flow_edge_count", 0)
        ),
        "cfg_edges": _int(program_graph.get("cfg_edge_count", 0)),
        "top_function_nodes": _list(repo_graph.get("top_function_nodes"))[:10],
    }


def _topk_from_summary(summary: dict[str, Any]) -> list[dict[str, Any]]:
    answers = _dict(summary.get("agent_answers"))
    top = [_dict(item) for item in _list(answers.get("top_suspicious_functions"))]
    if not top:
        fault = _dict(summary.get("fault_localization"))
        top = [_dict(item) for item in _list(fault.get("rankings"))[:10]]
    result: list[dict[str, Any]] = []
    for item in top[:10]:
        result.append(
            _redact(
                {
                    "function": item.get("function")
                    or item.get("function_name")
                    or item.get("qualified_name")
                    or "",
                    "function_id": item.get("function_id") or item.get("id") or "",
                    "file": item.get("file") or item.get("path") or "",
                    "final_score": _float(item.get("final_score", item.get("score", 0.0))),
                    "why": item.get("why") or item.get("reason") or "",
                    "source_role": item.get("source_role", ""),
                }
            )
        )
    return result


def _test_results_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    plan_path = str(summary.get("repository_test_execution_plan_json") or "")
    plan = _dict(_read_json(plan_path)) if plan_path else {}
    return {
        "status": summary.get("planned_repository_test_result_status", "")
        or summary.get("repository_test_execution_result_status", ""),
        "command": summary.get("planned_repository_test_command", "")
        or plan.get("recommended_execution_command", ""),
        "runner": summary.get("planned_repository_test_runner", ""),
        "execution_cwd": plan.get("recommended_execution_cwd", ""),
        "working_dir": plan.get("recommended_working_dir", ""),
        "passed": _int(summary.get("planned_repository_test_result_passed", 0)),
        "failed": _int(summary.get("planned_repository_test_result_failed", 0)),
        "errors": _int(summary.get("planned_repository_test_result_errors", 0)),
        "skipped": _int(summary.get("planned_repository_test_result_skipped", 0)),
        "test_count": _int(summary.get("planned_repository_test_result_test_count", 0)),
        "failure_category": summary.get("planned_repository_test_failure_category", ""),
        "failure_signal": summary.get("planned_repository_test_failure_signal", ""),
    }


def _action_registry_gate(
    action_id: str,
    *,
    command: str = "",
) -> dict[str, Any]:
    canonical = canonical_action_id(action_id)
    spec = action_spec_for(action_id)
    registered = bool(spec)
    command_safe, command_reason = _command_safety_check(canonical, command)
    passed = registered and command_safe
    return {
        "status": "pass" if passed else "blocked",
        "requested_action_id": action_id,
        "canonical_action_id": canonical,
        "registered": registered,
        "command_safe": command_safe,
        "command_safety_reason": command_reason,
        "tool": str(spec.get("tool") or ""),
        "module": str(spec.get("module") or ""),
        "blocker": (
            ""
            if passed
            else "unregistered_action"
            if not registered
            else "unsafe_or_mismatched_command"
        ),
    }


def _execute_registered_command(
    action_id: str,
    command: str,
    *,
    cwd: str,
    timeout: int,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    gate = _action_registry_gate(action_id, command=command)
    if gate["status"] != "pass":
        return {
            "status": "blocked",
            "returncode": -1,
            "timeout": timeout,
            "stdout_tail": "",
            "stderr_tail": "Action Registry rejected an unregistered action.",
            "action_registry_gate": gate,
        }
    result = _execute_command(command, cwd=cwd, timeout=timeout, env=env)
    result["action_registry_gate"] = gate
    return result


def _command_safety_check(canonical_action: str, command: str) -> tuple[bool, str]:
    normalized = " ".join(str(command or "").strip().split())
    if not normalized:
        return False, "missing_command"
    if re.search(r"[\r\n;&|<>`]", str(command)):
        return False, "shell_control_character"
    lowered = normalized.lower()
    if canonical_action == "run_repository_tests":
        parts = lowered.split()
        runner = parts[0] if parts else ""
        direct_runner = runner in {
            "python",
            "python.exe",
            "python3",
            "py",
            "pytest",
            "pytest.exe",
            "tox",
            "tox.exe",
            "nox",
            "nox.exe",
        }
        managed_runner = len(parts) >= 2 and parts[:2] in [
            ["uv", "run"],
            ["poetry", "run"],
        ]
        return (
            (True, "registered_test_runner_prefix")
            if direct_runner or managed_runner
            else (False, "unexpected_test_runner_prefix")
        )
    if canonical_action in {
        "discover_repository_structure",
        "generate_hybrid_patch_candidates",
    }:
        expected = "python -m code_intelligence_agent agent "
        return (
            (True, "registered_agent_entrypoint")
            if lowered.startswith(expected)
            else (False, "unexpected_agent_entrypoint")
        )
    return False, "action_has_no_session_command_policy"


def _execute_command(
    command: str,
    *,
    cwd: str,
    timeout: int,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    process_env = os.environ.copy()
    if env:
        process_env.update({str(key): str(value) for key, value in env.items()})
    try:
        completed = subprocess.run(
            command,
            cwd=cwd or None,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=process_env,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "returncode": -1,
            "timeout": timeout,
            "stdout_tail": _truncate(exc.stdout or "", 2000),
            "stderr_tail": _truncate(exc.stderr or "", 2000),
        }
    except OSError as exc:
        return {
            "status": "error",
            "returncode": -1,
            "error": str(exc),
            "stdout_tail": "",
            "stderr_tail": "",
        }
    return {
        "status": "pass" if completed.returncode == 0 else "fail",
        "returncode": completed.returncode,
        "timeout": timeout,
        "stdout_tail": _truncate(completed.stdout or "", 2000),
        "stderr_tail": _truncate(completed.stderr or "", 2000),
    }


def _blockers_from_summary(summary: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    readiness = _dict(summary.get("analysis_readiness"))
    stop_state = _dict(summary.get("agent_auto_stop_state"))
    for source, blocker, next_action in [
        (
            "analysis_readiness",
            readiness.get("blocker", ""),
            readiness.get("next_action", ""),
        ),
        (
            "agent_auto_stop_state",
            stop_state.get("blocker", ""),
            stop_state.get("recommended_next_action", ""),
        ),
        (
            "repository_test_setup_doctor",
            summary.get("repository_test_setup_doctor_blocker", ""),
            summary.get("repository_test_setup_doctor_next_action", ""),
        ),
        (
            "patch_validation",
            summary.get("repository_test_patch_validation_reason", ""),
            "inspect reflection_trace and patch validation results",
        ),
    ]:
        if blocker:
            records.append(
                {
                    "created_at": _now(),
                    "source": source,
                    "blocker": str(blocker),
                    "next_action": str(next_action or ""),
                }
            )
    return records


def _merge_blockers(
    existing: list[Any],
    new_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    merged: list[dict[str, Any]] = []
    for item_value in [*existing, *new_items]:
        item = _dict(item_value)
        key = (str(item.get("source") or ""), str(item.get("blocker") or ""))
        if key in seen or not key[1]:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _patch_attempts_from_summary(summary: dict[str, Any]) -> list[dict[str, Any]]:
    validation_path = str(summary.get("repository_test_patch_validation_json") or "")
    validation = _dict(_read_json(validation_path)) if validation_path else {}
    results = _list(validation.get("results"))
    attempts: list[dict[str, Any]] = []
    for idx, item_value in enumerate(results, start=1):
        item = _dict(item_value)
        candidate = _dict(item.get("candidate"))
        validation_result = _dict(item.get("validation") or item.get("result"))
        diff = str(
            candidate.get("diff")
            or item.get("diff")
            or candidate.get("unified_diff")
            or ""
        )
        attempts.append(
            _redact(
                {
                    "candidate_id": str(
                        candidate.get("candidate_id")
                        or candidate.get("id")
                        or item.get("candidate_id")
                        or f"candidate_{idx}"
                    ),
                    "target_function": str(
                        candidate.get("function_name")
                        or candidate.get("target_function")
                        or item.get("function_name")
                        or ""
                    ),
                    "status": str(
                        item.get("status")
                        or validation_result.get("status")
                        or ("pass" if item.get("success") else "fail")
                    ),
                    "sandbox_status": str(
                        validation_result.get("sandbox_status")
                        or validation_result.get("status")
                        or item.get("sandbox_status")
                        or ""
                    ),
                    "failure_type": str(
                        item.get("failure_type")
                        or validation_result.get("failure_type")
                        or ""
                    ),
                    "generator": str(
                        candidate.get("generator")
                        or _dict(candidate.get("metadata")).get("generator")
                        or item.get("generator")
                        or ""
                    ),
                    "strategy": str(
                        candidate.get("strategy")
                        or item.get("strategy")
                        or ""
                    ),
                    "passed": bool(
                        item.get("passed", item.get("success", validation_result.get("success", False)))
                    ),
                    "diff_fingerprint": _fingerprint(diff),
                    "fixed_source_fingerprint": str(
                        candidate.get("fixed_source_fingerprint")
                        or item.get("fixed_source_fingerprint")
                        or ""
                    ),
                    "diff": _truncate(diff, 3000),
                }
            )
        )
    return attempts


def _merge_patch_attempts(
    existing: list[Any],
    new_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for item_value in [*existing, *new_items]:
        item = _dict(item_value)
        key = str(item.get("diff_fingerprint") or item.get("candidate_id") or "")
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _reflection_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    reflection = _dict(summary.get("reflection_trace"))
    trace_path = str(summary.get("reflection_trace_json") or "")
    trace_payload = _dict(_read_json(trace_path)) if trace_path else {}
    return {
        "status": reflection.get("status", ""),
        "reason": reflection.get("reason", ""),
        "available": bool(reflection.get("available", False)),
        "trace_path": trace_path,
        "strategy_count": _int(trace_payload.get("strategy_count", 0)),
        "attempt_count": len(_list(trace_payload.get("attempts"))),
    }


def _controller_history_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    controller = _dict(summary.get("agent_controller"))
    return {
        "control_loop": _list(controller.get("control_loop")),
        "selected_action": _dict(controller.get("selected_action")),
        "decision_trace": _list(controller.get("decision_trace")),
        "auto_trace": _list(summary.get("agent_auto_trace")),
        "auto_actions": _list(summary.get("agent_auto_actions")),
    }


def _execution_trace_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    trace = _dict(summary.get("agent_execution_trace"))
    actions = []
    for item_value in _list(trace.get("actions"))[:10]:
        item = _dict(item_value)
        actions.append(
            {
                "iteration": _int(item.get("iteration", 0)),
                "action_id": str(item.get("action_id") or ""),
                "execution_status": str(item.get("execution_status") or ""),
                "executed": bool(item.get("executed", False)),
                "verified": bool(item.get("verified", False)),
                "blocked": bool(item.get("blocked", False)),
                "failed": bool(item.get("failed", False)),
                "command": str(item.get("command") or ""),
                "returncode": item.get("returncode"),
                "verify_summary": str(item.get("verify_summary") or ""),
                "next_action": str(item.get("next_action") or ""),
            }
        )
    return {
        "status": str(trace.get("status") or ""),
        "source": str(trace.get("source") or ""),
        "action_count": _int(trace.get("action_count", 0)),
        "executed_action_count": _int(trace.get("executed_action_count", 0)),
        "verified_action_count": _int(trace.get("verified_action_count", 0)),
        "blocked_action_count": _int(trace.get("blocked_action_count", 0)),
        "failed_action_count": _int(trace.get("failed_action_count", 0)),
        "skipped_action_count": _int(trace.get("skipped_action_count", 0)),
        "real_execution_answer": str(trace.get("real_execution_answer") or ""),
        "trace_path": str(summary.get("agent_execution_trace_json") or ""),
        "actions": actions,
    }


def _final_report_summary_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    gate = _dict(summary.get("acceptance_gate"))
    readiness = _dict(summary.get("agent_goal_readiness"))
    return {
        "status": summary.get("status", ""),
        "status_reason": summary.get("status_reason", ""),
        "acceptance_gate_status": gate.get("status", ""),
        "acceptance_gate_passed": gate.get("passed_check_count", 0),
        "acceptance_gate_total": gate.get("check_count", 0),
        "agent_goal_readiness_status": readiness.get("status", ""),
        "agent_goal_readiness_passed": readiness.get("passed_criteria_count", 0),
        "agent_goal_readiness_total": readiness.get("criteria_count", 0),
        "next_action": summary.get("next_action", ""),
    }


def _base_agent_command(session: dict[str, Any]) -> str:
    repo_spec = str(session.get("repo_spec") or session.get("repo") or "")
    output_dir = str(session.get("output_dir") or "")
    return f"python -m code_intelligence_agent agent {repo_spec} {output_dir}".strip()


def _memory_layer_row(name: str, layer: dict[str, Any], evidence: str) -> str:
    return (
        "| "
        f"{_md(name)} | `{_md(layer.get('status') or 'missing')}` | "
        f"{_md(evidence)} |"
    )


def _patch_attempt_passed(item: dict[str, Any]) -> bool:
    status = str(item.get("status") or item.get("sandbox_status") or "").lower()
    return bool(
        item.get("passed", False)
        or item.get("success", False)
        or status in {"pass", "passed", "success", "verified"}
    )


def _successful_repair_strategies(
    successful_patches: list[dict[str, Any]],
    memory: dict[str, Any],
) -> list[str]:
    strategies = []
    for item in successful_patches:
        strategy = str(
            item.get("strategy")
            or item.get("repair_strategy")
            or item.get("generator")
            or ""
        )
        if strategy:
            strategies.append(strategy)
    reflection = _dict(memory.get("reflection_trace"))
    if successful_patches and reflection.get("reason"):
        strategies.append(str(reflection.get("reason") or ""))
    return _unique_strings(str(item) for item in strategies)[:10]


def _unique_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "")
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _compatible_external_memory(
    summary: dict[str, Any],
    *,
    session: dict[str, Any],
) -> dict[str, Any]:
    path = str(os.environ.get("CIA_AGENT_PATCH_MEMORY") or "").strip()
    if not path:
        return {}
    external = _dict(_read_json(path))
    if not external:
        return {}
    external_repo = str(_dict(external.get("repo_profile")).get("repo") or "")
    current_repo = str(session.get("repo") or session.get("repo_spec") or "")
    if external_repo and current_repo and external_repo != current_repo:
        return {}
    return external


def _merge_external_memory(
    memory: dict[str, Any],
    external: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(memory)
    current_ref = str(_dict(memory.get("repo_profile")).get("repository_ref") or "")
    external_ref = str(_dict(external.get("repo_profile")).get("repository_ref") or "")
    same_version = not current_ref or not external_ref or current_ref == external_ref
    merged["constraints"] = _unique_strings(
        [*_list(external.get("constraints")), *_list(memory.get("constraints"))]
    )
    merged["repair_strategy_preferences"] = _unique_strings(
        [
            *_list(external.get("repair_strategy_preferences")),
            *_list(memory.get("repair_strategy_preferences")),
        ]
    )
    merged["active_scope"] = str(
        memory.get("active_scope") or external.get("active_scope") or ""
    )
    merged["conversation_summary"] = _dict(
        external.get("conversation_summary")
    )
    merged["evidence_memory"] = _dict(external.get("evidence_memory"))
    merged["deleted_memory_ids"] = _list(external.get("deleted_memory_ids"))
    if same_version:
        merged["patch_attempt_history"] = _merge_patch_attempts(
            _list(external.get("patch_attempt_history")),
            _list(memory.get("patch_attempt_history")),
        )
        merged["blocker_evolution"] = _merge_blockers(
            _list(external.get("blocker_evolution")),
            _list(memory.get("blocker_evolution")),
        )
    return merged


def _default_memory_query(
    memory: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any]:
    tests = _dict(memory.get("test_results"))
    blockers = _list(memory.get("blocker_evolution"))
    topk = _list(memory.get("topk_suspicious_functions"))
    latest_turn = _dict(_list(memory.get("turns"))[-1]) if _list(memory.get("turns")) else {}
    return {
        "user_goal": str(session.get("user_goal") or memory.get("user_goal") or ""),
        "current_state": _dict(session.get("current_state")),
        "latest_intent": str(latest_turn.get("intent") or ""),
        "failure_category": str(tests.get("failure_category") or ""),
        "failure_signal": str(tests.get("failure_signal") or ""),
        "blocker": str(_dict(blockers[-1]).get("blocker") or "") if blockers else "",
        "top_function": str(_dict(topk[0]).get("function") or "") if topk else "",
        "constraints": _list(memory.get("constraints")),
        "repair_strategy_preferences": _list(
            memory.get("repair_strategy_preferences")
        ),
    }


def _evidence_memory_summary(evidence: dict[str, Any]) -> dict[str, Any]:
    records = [_dict(item) for item in _list(evidence.get("records"))]
    return {
        "schema_version": _int(evidence.get("schema_version", 0)),
        "retrieval_algorithm": str(evidence.get("retrieval_algorithm") or ""),
        "record_count": _int(evidence.get("record_count", len(records))),
        "active_record_count": _int(
            evidence.get(
                "active_record_count",
                sum(1 for item in records if item.get("status") == "active"),
            )
        ),
        "stale_record_count": _int(
            evidence.get(
                "stale_record_count",
                sum(1 for item in records if item.get("status") == "stale"),
            )
        ),
        "layers": sorted(
            {
                str(item.get("layer") or "")
                for item in records
                if str(item.get("layer") or "")
            }
        ),
        "required_record_fields": [
            "memory_id",
            "layer",
            "kind",
            "source",
            "created_at",
            "repo",
            "repository_ref",
            "evidence_path",
            "confidence",
            "validation",
        ],
    }


def _read_json(path: str | Path) -> Any:
    if not path:
        return {}
    candidate = Path(path)
    if not candidate.exists():
        return {}
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_redact(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if key_text in _VOLATILE_PATH_KEYS:
                continue
            if any(part in lowered for part in _SENSITIVE_KEY_PARTS) and not isinstance(
                item,
                bool,
            ):
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE_PATTERN.sub("[REDACTED]", value)
    return value


def _fingerprint(text: str) -> str:
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")
