from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from code_intelligence_agent import main as cli_module
from code_intelligence_agent.agents.intent_parser import parse_user_intent
from code_intelligence_agent.agents.llm_client import LLMResponse
from code_intelligence_agent.agents.session_memory import (
    LOOP,
    chat_with_session,
    create_or_update_session_from_summary,
    delete_session_memory,
    inspect_session_memory,
    reset_session_memory,
    resume_session,
)
from code_intelligence_agent.evaluation import github_repo_intelligence


def test_intent_parser_handles_chinese_agent_commands():
    assert parse_user_intent("继续修复 Top-1 函数")["intent"] == "continue_repair"
    assert parse_user_intent("解释上一次失败原因")["intent"] == "explain_failure"
    assert parse_user_intent("重新运行 pytest")["intent"] == "rerun_tests"
    scoped = parse_user_intent("只分析 tests 目录")
    assert scoped["intent"] == "narrow_scope"
    assert scoped["scope"] == "tests"
    constrained = parse_user_intent("不要修改公共 API")
    assert constrained["intent"] == "change_constraints"
    changed = parse_user_intent("use alternative repair strategy")
    assert changed["intent"] == "change_repair_strategy"
    assert changed["strategy"] == "repair strategy"
    assert constrained["constraints"] == ["不要修改公共 API"]


def test_session_memory_persists_compact_redacted_repo_state(tmp_path):
    summary = _sample_summary(tmp_path)
    memory_root = tmp_path / "memory"
    fake_secret = "sk-" + "secretvalue123456"

    session = create_or_update_session_from_summary(
        summary,
        raw_argv=[
            "https://github.com/example/project",
            "--api-key",
            fake_secret,
        ],
        memory_root=memory_root,
    )

    assert session["session_id"]
    assert Path(session["session_path"]).exists()
    assert Path(session["memory_path"]).exists()
    assert Path(session["session_report_path"]).exists()
    assert Path(session["agent_memory_report_json"]).exists()
    assert Path(session["agent_memory_report_path"]).exists()
    assert Path(session["agent_decision_report_json"]).exists()
    assert Path(session["agent_decision_report_path"]).exists()
    assert Path(session["evidence_memory_path"]).exists()
    assert Path(session["memory_retrieval_path"]).exists()
    memory_text = Path(session["memory_path"]).read_text(encoding="utf-8")
    assert fake_secret not in memory_text
    assert "source_cache" not in memory_text

    memory = json.loads(memory_text)
    assert memory["repo_profile"]["repo"] == "example/project"
    assert memory["graph_memory"]["program_graph_available"] is True
    assert memory["topk_suspicious_functions"][0]["function"] == "pkg.core.load_user"
    assert memory["test_results"]["command"] == "python -m pytest tests"
    assert memory["patch_attempt_history"][0]["target_function"] == "pkg.core.load_user"
    layers = memory["memory_layers"]
    assert layers["session_memory"]["status"] == "ready"
    assert layers["repo_memory"]["test_command"] == "python -m pytest tests"
    assert layers["repair_memory"]["failed_patch_count"] == 1
    assert layers["repair_memory"]["failed_patch_fingerprints"]
    assert layers["long_term_pattern_memory"]["status"] == "ready"
    memory_report = json.loads(
        Path(session["agent_memory_report_json"]).read_text(encoding="utf-8")
    )
    decision_report = json.loads(
        Path(session["agent_decision_report_json"]).read_text(encoding="utf-8")
    )
    decision_report_text = Path(session["agent_decision_report_path"]).read_text(
        encoding="utf-8"
    )
    memory_report_text = Path(session["agent_memory_report_path"]).read_text(
        encoding="utf-8"
    )
    assert memory_report["status"] == "pass"
    assert memory_report["ready_layer_count"] == 5
    assert memory_report["layer_count"] == 5
    assert memory_report["retrieval"]["selected_memory_ids"]
    assert memory_report["evidence_memory"]["retrieval_algorithm"] == (
        "structured_relevance_v2"
    )
    assert "Decision Use: execution_hint=" in memory_report_text
    assert "Conflicts: status=" in memory_report_text
    assert decision_report["selected_action"]["id"] == "run_llm_patch_reflection_loop"
    assert "LLM Recommended Action" in decision_report_text
    assert "Controller Final Action" in decision_report_text
    assert "Adopted Action" in decision_report_text
    assert "run_llm_patch_reflection_loop" in decision_report_text
    assert memory["turns"][0]["intent"] == "initial_analysis"


def test_chat_records_three_turns_and_reads_existing_memory(tmp_path):
    summary = _sample_summary(tmp_path)
    memory_root = tmp_path / "memory"
    session = create_or_update_session_from_summary(
        summary,
        raw_argv=["https://github.com/example/project", "--agent"],
        memory_root=memory_root,
    )

    first = chat_with_session(
        session["session_id"],
        "解释上一次失败原因",
        memory_root=memory_root,
    )
    second = chat_with_session(
        session["session_id"],
        "不要修改公共 API",
        memory_root=memory_root,
    )
    third = chat_with_session(
        session["session_id"],
        "继续修复 Top-1 函数",
        memory_root=memory_root,
    )

    assert first["intent"]["intent"] == "explain_failure"
    assert first["memory_usage_evidence"]["repo_profile_loaded"] is True
    assert second["intent"]["intent"] == "change_constraints"
    assert third["decision"]["action_id"] == "continue_repair_with_patch_memory"
    assert "previous patch attempts" in third["answer"]
    assert third["decision"]["environment"]["CIA_AGENT_PATCH_MEMORY"] == (
        third["session"]["memory_path"]
    )

    memory = json.loads(Path(third["session"]["memory_path"]).read_text(encoding="utf-8"))
    assert [item["intent"] for item in memory["user_intent_history"]] == [
        "explain_failure",
        "change_constraints",
        "continue_repair",
    ]
    assert memory["constraints"] == ["不要修改公共 API"]
    assert memory["turn_count"] == 4
    latest_loop = memory["turns"][-1]["loop"]
    assert list(latest_loop) == LOOP
    assert latest_loop["observe"]["status"] == "complete"
    assert latest_loop["replan"]["next_action"] == "generate_or_validate_next_patch"


def test_chat_routes_llm_function_intent_into_real_session_decision(tmp_path):
    class IntentClient:
        def complete_with_tools(self, prompt, tools, *, tool_choice):
            assert "pkg.core.load_user" in prompt
            assert tools[0]["function"]["name"] == "route_agent_intent"
            assert tool_choice["function"]["name"] == "route_agent_intent"
            return LLMResponse(
                text=json.dumps(
                    {
                        "intent": "inspect_function",
                        "arguments": {"function": "pkg.core.load_user"},
                        "confidence": 0.96,
                        "reason": "explicit function inspection",
                        "required_context": ["topk_suspicious_functions"],
                    }
                ),
                metadata={
                    "status": "pass",
                    "provider": "test",
                    "model": "intent-test",
                    "tool_call": {
                        "id": "call_1",
                        "type": "function",
                        "name": "route_agent_intent",
                    },
                },
            )

    summary = _sample_summary(tmp_path)
    memory_root = tmp_path / "memory"
    session = create_or_update_session_from_summary(
        summary,
        raw_argv=["https://github.com/example/project", "--agent"],
        memory_root=memory_root,
    )

    result = chat_with_session(
        session["session_id"],
        "请详细分析 pkg.core.load_user",
        memory_root=memory_root,
        intent_client=IntentClient(),
    )

    assert result["intent"]["source"] == "llm"
    assert result["turn"]["intent_source"] == "llm"
    assert result["intent"]["intent"] == "inspect_function"
    assert result["decision"]["action_id"] == "inspect_function_from_memory"
    assert "FinalScore=0.9100" in result["answer"]
    memory = json.loads(Path(result["session"]["memory_path"]).read_text(encoding="utf-8"))
    assert memory["active_function"] == "pkg.core.load_user"


def test_stop_execution_persists_and_blocks_later_process_start(tmp_path):
    summary = _sample_summary(tmp_path)
    memory_root = tmp_path / "memory"
    session = create_or_update_session_from_summary(
        summary,
        raw_argv=["https://github.com/example/project", "--agent"],
        memory_root=memory_root,
    )

    stopped = chat_with_session(
        session["session_id"],
        "停止执行",
        memory_root=memory_root,
        llm_intent_enabled=False,
    )
    blocked = chat_with_session(
        session["session_id"],
        "重新运行 pytest",
        memory_root=memory_root,
        execute=True,
        llm_intent_enabled=False,
    )

    assert stopped["decision"]["action_id"] == "stop_execution"
    assert blocked["decision"]["action_id"] == "execution_stopped_gate"
    assert blocked["decision"]["executed"] is False
    assert blocked["decision"]["blocked"] is True
    resumed = resume_session(session["session_id"], memory_root=memory_root)
    assert "execution_stopped=true" in resumed["answer"]
    assert resumed["turn"]["intent_source"] == "explicit_command"
    assert resumed["memory_usage_evidence"]["execution_stopped"] is True


def test_ten_turn_conversation_and_resume_preserve_session_constraints(tmp_path):
    summary = _sample_summary(tmp_path)
    memory_root = tmp_path / "memory"
    session = create_or_update_session_from_summary(
        summary,
        raw_argv=["https://github.com/example/project", "--agent"],
        memory_root=memory_root,
    )
    messages = [
        "查看当前状态",
        "解释上一次失败原因",
        "查看函数 pkg.core.load_user",
        "解释定位结果",
        "比较补丁候选",
        "不要修改公共 API",
        "只分析 pkg 目录",
        "use alternative repair strategy",
        "生成最终报告",
        "继续修复 Top-1 函数",
    ]

    result = None
    for message in messages:
        result = chat_with_session(
            session["session_id"],
            message,
            memory_root=memory_root,
            llm_intent_enabled=False,
        )

    assert result is not None
    assert result["session"]["turn_count"] == 11
    resumed = resume_session(session["session_id"], memory_root=memory_root)
    memory = json.loads(Path(resumed["session"]["memory_path"]).read_text(encoding="utf-8"))
    assert resumed["session"]["turn_count"] == 12
    assert len(memory["user_intent_history"]) == 10
    assert memory["constraints"] == ["不要修改公共 API"]
    assert memory["active_scope"] == "pkg"
    assert memory["repair_strategy_preferences"] == ["repair strategy"]
    assert memory["turns"][-1]["decision"]["action_id"] == "resume_session_from_memory"


def test_session_command_execution_is_gated_by_action_registry(tmp_path):
    summary = _sample_summary(tmp_path)
    plan = Path(summary["output_dir"]) / "repository_test_execution_plan.json"
    plan.write_text(
        json.dumps(
            {
                "recommended_execution_command": "python -c \"print('registry-ok')\"",
                "recommended_execution_cwd": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )
    summary["planned_repository_test_command"] = "python -c \"print('registry-ok')\""
    summary["repository_test_execution_plan_json"] = str(plan)
    memory_root = tmp_path / "memory"
    session = create_or_update_session_from_summary(
        summary,
        raw_argv=["https://github.com/example/project", "--agent"],
        memory_root=memory_root,
    )

    result = chat_with_session(
        session["session_id"],
        "重新运行 pytest",
        memory_root=memory_root,
        execute=True,
        llm_intent_enabled=False,
    )

    decision = result["decision"]
    assert decision["canonical_action_id"] == "run_repository_tests"
    assert decision["action_registry_gate"]["status"] == "pass"
    assert decision["execution_result"]["action_registry_gate"]["registered"] is True


def test_session_command_gate_rejects_shell_control_characters(tmp_path):
    summary = _sample_summary(tmp_path)
    plan = Path(summary["output_dir"]) / "repository_test_execution_plan.json"
    plan.write_text(
        json.dumps(
            {
                "recommended_execution_command": "python -m pytest & whoami",
                "recommended_execution_cwd": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )
    summary["planned_repository_test_command"] = "python -m pytest & whoami"
    summary["repository_test_execution_plan_json"] = str(plan)
    memory_root = tmp_path / "memory"
    session = create_or_update_session_from_summary(
        summary,
        raw_argv=["https://github.com/example/project", "--agent"],
        memory_root=memory_root,
    )

    result = chat_with_session(
        session["session_id"],
        "重新运行 pytest",
        memory_root=memory_root,
        execute=True,
        llm_intent_enabled=False,
    )

    execution = result["decision"]["execution_result"]
    assert execution["status"] == "blocked"
    assert execution["action_registry_gate"]["registered"] is True
    assert execution["action_registry_gate"]["command_safe"] is False
    assert (
        execution["action_registry_gate"]["command_safety_reason"]
        == "shell_control_character"
    )


def test_chat_records_alternative_repair_strategy_in_layered_memory(tmp_path):
    summary = _sample_summary(tmp_path)
    memory_root = tmp_path / "memory"
    session = create_or_update_session_from_summary(
        summary,
        raw_argv=["https://github.com/example/project", "--agent"],
        memory_root=memory_root,
    )

    result = chat_with_session(
        session["session_id"],
        "use alternative repair strategy",
        memory_root=memory_root,
    )

    assert result["intent"]["intent"] == "change_repair_strategy"
    assert result["decision"]["action_id"] == "change_repair_strategy"
    assert result["decision"]["environment"]["CIA_AGENT_PATCH_MEMORY"] == (
        result["session"]["memory_path"]
    )
    assert (
        result["decision"]["environment"]["CIA_AGENT_REPAIR_STRATEGY"]
        == "repair strategy"
    )

    memory = json.loads(Path(result["session"]["memory_path"]).read_text(encoding="utf-8"))
    assert memory["repair_strategy_preferences"] == ["repair strategy"]
    assert memory["memory_layers"]["session_memory"][
        "repair_strategy_preferences"
    ] == ["repair strategy"]
    assert memory["memory_layers"]["repair_memory"]["strategy_preferences"] == [
        "repair strategy"
    ]
    assert (
        memory["turns"][-1]["loop"]["replan"]["next_action"]
        == "generate_alternative_patch_candidate"
    )

    report = json.loads(
        Path(result["session"]["agent_memory_report_json"]).read_text(
            encoding="utf-8"
        )
    )
    assert report["reuse_contract"]["feeds_patch_generation"]["enabled"] is True
    assert report["memory_layers"]["repair_memory"]["strategy_preferences"] == [
        "repair strategy"
    ]


def test_resume_session_uses_memory_without_new_analysis(tmp_path):
    summary = _sample_summary(tmp_path)
    memory_root = tmp_path / "memory"
    session = create_or_update_session_from_summary(
        summary,
        raw_argv=["https://github.com/example/project", "--agent"],
        memory_root=memory_root,
    )
    chat_with_session(session["session_id"], "重新运行 pytest", memory_root=memory_root)

    resumed = resume_session(session["session_id"], memory_root=memory_root)

    assert resumed["decision"]["action_id"] == "resume_session_from_memory"
    assert resumed["memory_usage_evidence"]["prior_turn_count"] == 2
    assert "Resumed session" in resumed["answer"]
    memory = json.loads(Path(resumed["session"]["memory_path"]).read_text(encoding="utf-8"))
    assert memory["turn_count"] == 3


def test_chat_execute_rerun_tests_runs_stored_command(tmp_path):
    summary = _sample_summary(tmp_path)
    plan = Path(summary["output_dir"]) / "repository_test_execution_plan.json"
    plan.write_text(
        json.dumps(
            {
                "recommended_execution_command": "python -c \"print('session-ok')\"",
                "recommended_execution_cwd": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )
    summary["planned_repository_test_command"] = "python -c \"print('session-ok')\""
    summary["repository_test_execution_plan_json"] = str(plan)
    memory_root = tmp_path / "memory"
    session = create_or_update_session_from_summary(
        summary,
        raw_argv=["https://github.com/example/project", "--agent"],
        memory_root=memory_root,
    )

    result = chat_with_session(
        session["session_id"],
        "重新运行 pytest",
        memory_root=memory_root,
        execute=True,
    )

    execution = result["decision"]["execution_result"]
    assert result["decision"]["executed"] is True
    assert execution["status"] == "pass"
    assert "session-ok" in execution["stdout_tail"]
    memory = json.loads(Path(result["session"]["memory_path"]).read_text(encoding="utf-8"))
    assert memory["turns"][-1]["decision"]["execution_result"]["status"] == "pass"


def test_chat_supports_narrow_scope_and_generate_report(tmp_path):
    summary = _sample_summary(tmp_path)
    memory_root = tmp_path / "memory"
    session = create_or_update_session_from_summary(
        summary,
        raw_argv=["https://github.com/example/project", "--agent"],
        memory_root=memory_root,
    )

    scoped = chat_with_session(
        session["session_id"],
        "只分析 tests 目录",
        memory_root=memory_root,
    )
    report = chat_with_session(
        session["session_id"],
        "生成最终报告",
        memory_root=memory_root,
    )

    assert scoped["intent"]["intent"] == "narrow_scope"
    assert scoped["intent"]["scope"] == "tests"
    assert "--include tests" in scoped["decision"]["command"]
    assert report["decision"]["action_id"] == "generate_session_report"
    assert Path(report["session"]["session_report_path"]).exists()
    memory = json.loads(Path(report["session"]["memory_path"]).read_text(encoding="utf-8"))
    assert memory["active_scope"] == "tests"
    assert [item["intent"] for item in memory["user_intent_history"]] == [
        "narrow_scope",
        "generate_report",
    ]


def test_top_level_cli_supports_chat_and_resume(tmp_path, capsys):
    summary = _sample_summary(tmp_path)
    memory_root = tmp_path / "memory"
    session = create_or_update_session_from_summary(
        summary,
        raw_argv=["https://github.com/example/project", "--agent"],
        memory_root=memory_root,
    )

    cli_module.main(
        [
            "chat",
            "--session",
            session["session_id"],
            "--memory-root",
            str(memory_root),
            "--message",
            "重新运行 pytest",
            "--format",
            "json",
        ]
    )
    chat_payload = json.loads(capsys.readouterr().out)
    assert chat_payload["intent"]["intent"] == "rerun_tests"
    assert chat_payload["decision"]["command"] == "python -m pytest tests"

    cli_module.main(
        [
            "resume",
            "--session",
            session["session_id"],
            "--memory-root",
            str(memory_root),
            "--format",
            "json",
        ]
    )
    resume_payload = json.loads(capsys.readouterr().out)
    assert resume_payload["decision"]["action_id"] == "resume_session_from_memory"


def test_top_level_cli_supports_memory_show_delete_and_reset(tmp_path, capsys):
    summary = _sample_summary(tmp_path)
    memory_root = tmp_path / "memory"
    session = create_or_update_session_from_summary(
        summary,
        raw_argv=["https://github.com/example/project", "--agent"],
        memory_root=memory_root,
    )

    cli_module.main(
        [
            "memory-show",
            "--session",
            session["session_id"],
            "--memory-root",
            str(memory_root),
            "--query",
            "failed patch",
            "--top-k",
            "10",
            "--format",
            "json",
        ]
    )
    shown = json.loads(capsys.readouterr().out)
    memory_id = shown["retrieval"]["selected_memory_ids"][0]
    assert shown["retrieval"]["algorithm"] == "structured_relevance_v2"

    cli_module.main(
        [
            "memory-delete",
            "--session",
            session["session_id"],
            "--memory-root",
            str(memory_root),
            "--memory-id",
            memory_id,
            "--yes",
            "--format",
            "json",
        ]
    )
    deleted = json.loads(capsys.readouterr().out)
    assert deleted["status"] == "pass"

    cli_module.main(
        [
            "memory-reset",
            "--session",
            session["session_id"],
            "--memory-root",
            str(memory_root),
            "--scope",
            "repair",
            "--yes",
            "--format",
            "json",
        ]
    )
    reset = json.loads(capsys.readouterr().out)
    assert reset["status"] == "pass"
    assert reset["scope"] == "repair"


def test_top_level_cli_supports_terminal_chat_ui_loop(
    tmp_path,
    monkeypatch,
    capsys,
):
    summary = _sample_summary(tmp_path)
    memory_root = tmp_path / "memory"
    session = create_or_update_session_from_summary(
        summary,
        raw_argv=["https://github.com/example/project", "--agent"],
        memory_root=memory_root,
    )
    messages = iter(
        [
            ":help",
            "use alternative repair strategy",
            ":execute on",
            ":execute off",
            ":resume",
            "exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(messages))
    monkeypatch.setenv("CIA_INTENT_LLM_ENABLED", "0")

    cli_module.main(
        [
            "chat-ui",
            "--session",
            session["session_id"],
            "--memory-root",
            str(memory_root),
            "--format",
            "markdown",
        ]
    )

    output = capsys.readouterr().out
    assert "Code Intelligence Agent Chat" in output
    assert "Chat UI Commands" in output
    assert "Agent Session Turn" in output
    assert "change_repair_strategy" in output
    assert "[chat-ui] Execute Mode: on" in output
    assert "[chat-ui] Execute Mode: off" in output
    assert "resume_session_from_memory" in output
    assert "[chat-ui] bye." in output


def test_terminal_chat_ui_handles_ten_natural_language_turns(
    tmp_path,
    monkeypatch,
    capsys,
):
    summary = _sample_summary(tmp_path)
    memory_root = tmp_path / "memory"
    session = create_or_update_session_from_summary(
        summary,
        raw_argv=["https://github.com/example/project", "--agent"],
        memory_root=memory_root,
    )
    messages = iter(
        [
            "查看当前状态",
            "解释上一次失败原因",
            "查看函数 pkg.core.load_user",
            "解释定位结果",
            "比较补丁候选",
            "不要修改公共 API",
            "只分析 pkg 目录",
            "use alternative repair strategy",
            "生成最终报告",
            "继续修复 Top-1 函数",
            "exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(messages))
    monkeypatch.setenv("CIA_INTENT_LLM_ENABLED", "0")

    cli_module.main(
        [
            "chat-ui",
            "--session",
            session["session_id"],
            "--memory-root",
            str(memory_root),
            "--format",
            "markdown",
        ]
    )

    output = capsys.readouterr().out
    assert output.count("# Agent Session Turn") == 10
    loaded = json.loads(Path(session["memory_path"]).read_text(encoding="utf-8"))
    assert loaded["turn_count"] == 12
    assert len(loaded["user_intent_history"]) == 10
    assert all(turn.get("intent_source") for turn in loaded["turns"][1:])
    assert loaded["constraints"] == ["不要修改公共 API"]


def test_repo_intelligence_cli_auto_creates_agent_session(
    tmp_path,
    monkeypatch,
    capsys,
):
    summary = _sample_summary(tmp_path)
    summary["static_intelligence_status"] = "analysis_ready"
    report = SimpleNamespace(
        summary=summary,
        output_dir=summary["output_dir"],
    )
    writes: list[dict] = []

    monkeypatch.setenv("CIA_AGENT_MEMORY_ROOT", str(tmp_path / "memory"))
    monkeypatch.setattr(
        github_repo_intelligence,
        "run_github_repo_intelligence",
        lambda *args, **kwargs: report,
    )
    monkeypatch.setattr(
        github_repo_intelligence,
        "github_repo_intelligence_summary",
        lambda report: dict(summary),
    )
    monkeypatch.setattr(
        github_repo_intelligence,
        "write_github_repo_intelligence_artifacts",
        lambda report, payload: writes.append(dict(payload)) or {},
    )
    monkeypatch.setattr(
        github_repo_intelligence,
        "_render_github_repo_intelligence_payload",
        lambda payload: "rendered",
    )

    with pytest.raises(SystemExit) as raised:
        github_repo_intelligence.main(
            [
                "https://github.com/example/project",
                summary["output_dir"],
                "--agent",
                "--format",
                "json",
            ]
        )

    assert raised.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agent_session"]["session_id"]
    assert Path(payload["agent_session"]["session_path"]).exists()
    assert Path(payload["agent_session"]["memory_path"]).exists()
    assert len(writes) == 2
    assert "agent_session" in writes[-1]


def test_session_memory_can_be_inspected_deleted_and_reset(tmp_path):
    summary = _sample_summary(tmp_path)
    memory_root = tmp_path / "memory"
    session = create_or_update_session_from_summary(
        summary,
        raw_argv=["https://github.com/example/project", "--agent"],
        memory_root=memory_root,
    )
    chat_with_session(
        session["session_id"],
        "不要修改公共 API",
        memory_root=memory_root,
        llm_intent_enabled=False,
    )

    inspected = inspect_session_memory(
        session["session_id"],
        memory_root=memory_root,
        query="用户约束 public API",
        layer="session_memory",
        top_k=10,
    )
    constraint = next(
        item
        for item in inspected["retrieval"]["records"]
        if item["kind"] == "user_constraint"
    )
    deleted = delete_session_memory(
        session["session_id"],
        constraint["memory_id"],
        memory_root=memory_root,
    )
    after_delete = inspect_session_memory(
        session["session_id"],
        memory_root=memory_root,
        query="用户约束 public API",
        top_k=20,
    )

    assert deleted["status"] == "pass"
    assert constraint["memory_id"] not in after_delete["retrieval"][
        "selected_memory_ids"
    ]
    reset = reset_session_memory(
        session["session_id"],
        memory_root=memory_root,
        scope="session",
    )
    loaded = json.loads(Path(reset["session"]["memory_path"]).read_text(encoding="utf-8"))
    assert reset["status"] == "pass"
    assert loaded["constraints"] == []
    assert loaded["turns"] == []
    assert reset["session"]["turn_count"] == 0


def test_session_store_compacts_long_conversation_without_losing_constraints(tmp_path):
    summary = _sample_summary(tmp_path)
    memory_root = tmp_path / "memory"
    session = create_or_update_session_from_summary(
        summary,
        raw_argv=["https://github.com/example/project", "--agent"],
        memory_root=memory_root,
    )
    chat_with_session(
        session["session_id"],
        "不要修改公共 API",
        memory_root=memory_root,
        llm_intent_enabled=False,
    )
    for _ in range(42):
        chat_with_session(
            session["session_id"],
            "查看当前状态",
            memory_root=memory_root,
            llm_intent_enabled=False,
        )

    resumed = resume_session(session["session_id"], memory_root=memory_root)
    memory = json.loads(Path(resumed["session"]["memory_path"]).read_text(encoding="utf-8"))

    assert resumed["session"]["turn_count"] == 45
    assert len(memory["turns"]) < resumed["session"]["turn_count"]
    assert memory["conversation_summary"]["compacted_turn_count"] > 0
    assert memory["constraints"] == ["不要修改公共 API"]
    assert resumed["memory_usage_evidence"]["compacted_turn_count"] > 0


def test_cross_repo_patterns_only_persist_sandbox_verified_repairs(tmp_path):
    first = _sample_summary(tmp_path)
    validation_path = Path(first["repository_test_patch_validation_json"])
    validation_path.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "candidate": {
                            "candidate_id": "patch_verified",
                            "function_name": "pkg.core.load_user",
                            "generator": "hybrid",
                            "diff": "--- a/pkg/core.py\n+++ b/pkg/core.py\n@@\n- old\n+ new\n",
                        },
                        "validation": {"status": "pass"},
                        "status": "pass",
                        "success": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    memory_root = tmp_path / "memory"
    first_session = create_or_update_session_from_summary(
        first,
        memory_root=memory_root,
    )
    first_memory = json.loads(
        Path(first_session["memory_path"]).read_text(encoding="utf-8")
    )

    assert first_memory["memory_layers"]["cross_repo_pattern_memory"][
        "pattern_count"
    ] == 1
    pattern_store = json.loads(
        (memory_root / "cross_repo_pattern_memory.json").read_text(encoding="utf-8")
    )
    assert pattern_store["record_count"] == 1
    assert pattern_store["records"][0]["validation"]["authority"] == "sandbox_pytest"

    second_root = tmp_path / "second-analysis"
    second_root.mkdir()
    second = dict(first)
    second["repo"] = "another/project"
    second["repo_spec"] = "https://github.com/another/project"
    second["repository_ref"] = "def456"
    second["output_dir"] = str(second_root)
    second["repository_test_patch_validation_json"] = ""
    second_session = create_or_update_session_from_summary(
        second,
        memory_root=memory_root,
    )
    second_memory = json.loads(
        Path(second_session["memory_path"]).read_text(encoding="utf-8")
    )
    assert second_memory["memory_layers"]["cross_repo_pattern_memory"][
        "pattern_count"
    ] == 1


def _sample_summary(tmp_path: Path) -> dict:
    output_dir = tmp_path / "analysis"
    output_dir.mkdir()
    patch_validation = output_dir / "repository_test_patch_validation.json"
    patch_validation.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "candidate": {
                            "candidate_id": "patch_1",
                            "function_name": "pkg.core.load_user",
                            "diff": "--- a/pkg/core.py\n+++ b/pkg/core.py\n@@\n- return data[name]\n+ return data.get(name)\n",
                        },
                        "validation": {
                            "status": "fail",
                            "failure_type": "assertion_failure",
                        },
                        "success": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return {
        "repo": "example/project",
        "repo_spec": "https://github.com/example/project",
        "repository_ref": "abc123",
        "output_dir": str(output_dir),
        "status": "pass",
        "status_reason": "report_ready",
        "source_cache_dir": str(output_dir / "source_cache"),
        "agent_invocation": {
            "effective_execution_profile": "agent-auto",
            "agent_mode": True,
            "agent_shortcut": True,
            "auto_controller_actions": True,
            "auto_controller_max_actions": 4,
            "repository_patch_generation_mode": "hybrid",
            "repository_test_timeout": 20,
            "source_cache_dir": str(output_dir / "source_cache"),
        },
        "repository_structure": {
            "function_count": 12,
            "class_count": 2,
            "loc": 300,
            "layout": "src_layout",
            "directory_file_counts": {"pkg": 3, "tests": 2},
            "repo_graph": {
                "program_graph": {
                    "available": True,
                    "node_count": 30,
                    "edge_count": 44,
                    "data_flow_edge_count": 8,
                    "cross_function_data_flow_edge_count": 2,
                    "cfg_edge_count": 14,
                },
                "top_function_nodes": [
                    {"function": "pkg.core.load_user", "degree": 5}
                ],
            },
        },
        "agent_answers": {
            "top_suspicious_functions": [
                {
                    "function": "pkg.core.load_user",
                    "file": "pkg/core.py",
                    "final_score": 0.91,
                    "why": "failing test mentions missing key",
                    "source_role": "application",
                }
            ]
        },
        "planned_repository_test_command": "python -m pytest tests",
        "planned_repository_test_runner": "pytest",
        "planned_repository_test_result_status": "fail",
        "planned_repository_test_result_passed": 8,
        "planned_repository_test_result_failed": 1,
        "planned_repository_test_result_errors": 0,
        "planned_repository_test_result_skipped": 0,
        "planned_repository_test_result_test_count": 9,
        "planned_repository_test_failure_category": "assertion_failure",
        "planned_repository_test_failure_signal": "KeyError: name",
        "analysis_readiness": {
            "current_stage": "phase3_repository_test_execution",
            "next_stage": "phase3_patch_generation",
            "blocker": "patch_validation_failed",
            "next_action": "continue repair with patch memory",
        },
        "agent_auto_stop_state": {
            "blocker": "patch_validation_failed",
            "recommended_next_action": "generate next patch candidate",
        },
        "repository_test_setup_doctor_blocker": "",
        "repository_test_patch_validation_json": str(patch_validation),
        "repository_test_patch_validation_reason": "candidate_failed_tests",
        "reflection_trace": {
            "status": "complete",
            "available": True,
            "reason": "first patch still failed assertion",
        },
        "agent_controller": {
            "control_loop": LOOP,
            "selected_action": {
                "id": "run_llm_patch_reflection_loop",
                "reason": "patch failed and reflection can refine it",
                "command": "python -m code_intelligence_agent agent example/project",
            },
            "replan": {
                "reason": "avoid failed diff fingerprint",
                "next_action": "generate next patch candidate",
            },
            "decision_trace": [],
        },
        "acceptance_gate": {
            "status": "partial",
            "passed_check_count": 5,
            "check_count": 7,
        },
        "agent_goal_readiness": {
            "status": "partial",
            "passed_criteria_count": 4,
            "criteria_count": 6,
        },
        "next_action": "continue repair with patch memory",
    }
