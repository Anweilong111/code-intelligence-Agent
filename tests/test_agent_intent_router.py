from __future__ import annotations

import json

from code_intelligence_agent.agents.intent_router import (
    INTENT_TOOL_DEFINITION,
    parse_intent_tool_payload,
    route_user_intent,
    validate_llm_intent,
)
from code_intelligence_agent.agents.llm_client import (
    LLMRequestError,
    LLMResponse,
)


class _ToolClient:
    def __init__(self, payload: dict, *, tool_name: str = "route_agent_intent") -> None:
        self.payload = payload
        self.tool_name = tool_name
        self.calls: list[dict] = []

    def complete_with_tools(self, prompt, tools, *, tool_choice):
        self.calls.append(
            {
                "prompt": prompt,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        return LLMResponse(
            text=json.dumps(self.payload),
            metadata={
                "status": "pass",
                "provider": "test",
                "model": "intent-test",
                "tool_call": {
                    "id": "call_1",
                    "type": "function",
                    "name": self.tool_name,
                },
            },
        )


class _FailingClient:
    def complete_with_tools(self, prompt, tools, *, tool_choice):
        del prompt, tools, tool_choice
        raise LLMRequestError(
            "url_error",
            "provider unavailable",
            {
                "status": "error",
                "provider": "test",
                "model": "intent-test",
                "error_reason": "network unavailable",
            },
        )


class _TextClient:
    def __init__(self, text: str) -> None:
        self.text = text

    def complete(self, prompt):
        assert "Return only the JSON arguments" in prompt
        return LLMResponse(
            text=self.text,
            metadata={"status": "pass", "provider": "test", "model": "legacy"},
        )


class _InvalidClient:
    def complete_with_tools(self, prompt, tools, *, tool_choice):
        del prompt, tools, tool_choice
        raise ValueError("invalid local client state")


def _payload(intent: str, **arguments) -> dict:
    return {
        "intent": intent,
        "arguments": arguments,
        "confidence": 0.94,
        "reason": "The user explicitly requested this action.",
        "required_context": [],
    }


def test_llm_intent_router_uses_forced_function_call_and_context():
    client = _ToolClient(_payload("inspect_function", function="pkg.core.load_user"))

    result = route_user_intent(
        "请分析函数 pkg.core.load_user",
        context={
            "repo": "example/project",
            "topk_functions": ["pkg.core.load_user"],
            "ignored_secret": "must-not-enter-prompt",
        },
        client=client,
    )

    assert result["intent"] == "inspect_function"
    assert result["function"] == "pkg.core.load_user"
    assert result["source"] == "llm"
    assert result["router_audit"]["transport"] == "function_call"
    assert result["router_audit"]["fallback_used"] is False
    assert client.calls[0]["tools"] == [INTENT_TOOL_DEFINITION]
    assert client.calls[0]["tool_choice"]["function"]["name"] == "route_agent_intent"
    assert "example/project" in client.calls[0]["prompt"]
    assert "must-not-enter-prompt" not in client.calls[0]["prompt"]


def test_low_confidence_llm_intent_becomes_clarification_without_execution():
    payload = _payload("continue_repair")
    payload["confidence"] = 0.41
    client = _ToolClient(payload)

    result = route_user_intent("也许继续处理一下", client=client)

    assert result["intent"] == "ask_for_clarification"
    assert result["source"] == "llm"
    assert result["confidence"] == 0.41
    assert result["router_audit"]["schema_valid"] is True


def test_unsafe_scope_is_rejected_and_rule_fallback_remains_safe():
    client = _ToolClient(_payload("narrow_scope", scope="../../outside"))

    result = route_user_intent("只分析 tests 目录", client=client)

    assert result["intent"] == "narrow_scope"
    assert result["scope"] == "tests"
    assert result["source"] == "rule_fallback"
    assert "unsafe_scope" in result["router_audit"]["fallback_reason"]


def test_rule_fallback_rejects_path_traversal_scope():
    result = route_user_intent("只分析 ../../ 目录", llm_enabled=False)

    assert result["intent"] == "ask_for_clarification"
    assert result["scope"] == ""
    assert result["source"] == "rule_fallback"


def test_unknown_fields_and_wrong_tool_name_use_rule_fallback():
    payload = _payload("rerun_tests")
    payload["shell_command"] = "pytest"
    invalid_schema = route_user_intent(
        "重新运行 pytest",
        client=_ToolClient(payload),
    )
    wrong_tool = route_user_intent(
        "重新运行 pytest",
        client=_ToolClient(_payload("rerun_tests"), tool_name="run_shell"),
    )

    assert invalid_schema["intent"] == "rerun_tests"
    assert invalid_schema["source"] == "rule_fallback"
    assert "unknown_payload_fields" in invalid_schema["router_audit"]["fallback_reason"]
    assert wrong_tool["intent"] == "rerun_tests"
    assert wrong_tool["source"] == "rule_fallback"
    assert wrong_tool["router_audit"]["fallback_reason"] == "unexpected_llm_tool_name"


def test_provider_failure_is_auditable_and_does_not_break_rule_routing():
    result = route_user_intent("生成最终报告", client=_FailingClient())

    assert result["intent"] == "generate_report"
    assert result["source"] == "rule_fallback"
    audit = result["router_audit"]
    assert audit["fallback_reason"] == "llm_request_error:url_error"
    assert audit["llm_metadata"]["error_reason"] == "network unavailable"


def test_unconfigured_router_uses_rule_fallback(monkeypatch):
    for name in [
        "CIA_INTENT_LLM_ENABLED",
        "CIA_INTENT_LLM_API_KEY",
        "CIA_LLM_API_KEY",
        "CIA_REPLAN_LLM_API_KEY",
        "CIA_JUDGE_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "DASHSCOPE_API_KEY",
    ]:
        monkeypatch.delenv(name, raising=False)

    result = route_user_intent("查看当前状态")

    assert result["intent"] == "inspect_status"
    assert result["source"] == "rule_fallback"
    assert result["router_audit"]["fallback_used"] is True


def test_forced_llm_routing_without_any_key_falls_back(monkeypatch):
    for name in [
        "CIA_INTENT_LLM_API_KEY",
        "CIA_LLM_API_KEY",
        "CIA_JUDGE_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "DASHSCOPE_API_KEY",
    ]:
        monkeypatch.delenv(name, raising=False)

    result = route_user_intent("查看当前状态", llm_enabled=True)

    assert result["intent"] == "inspect_status"
    assert result["source"] == "rule_fallback"
    assert result["router_audit"]["fallback_reason"] == "missing_intent_llm_api_key"


def test_invalid_payload_and_local_client_error_use_rule_fallback():
    invalid_payload = route_user_intent(
        "生成最终报告",
        client=_TextClient("not-json"),
    )
    local_error = route_user_intent(
        "生成最终报告",
        client=_InvalidClient(),
    )

    assert invalid_payload["intent"] == "generate_report"
    assert invalid_payload["router_audit"]["transport"] == "json_fallback"
    assert invalid_payload["router_audit"]["fallback_reason"].endswith("invalid_json")
    assert local_error["intent"] == "generate_report"
    assert local_error["router_audit"]["fallback_reason"] == "llm_router_error:ValueError"


def test_payload_parser_handles_fences_wrappers_and_invalid_shapes():
    fenced, reason = parse_intent_tool_payload(
        "```json\n" + json.dumps(_payload("inspect_status")) + "\n```"
    )
    wrapped, wrapped_reason = parse_intent_tool_payload(
        json.dumps(
            {
                "name": "route_agent_intent",
                "arguments": json.dumps(_payload("inspect_status")),
            }
        )
    )
    invalid_nested, invalid_reason = parse_intent_tool_payload(
        json.dumps({"name": "route_agent_intent", "arguments": "{"})
    )
    non_object, non_object_reason = parse_intent_tool_payload("[]")

    assert reason == "parsed"
    assert fenced["intent"] == "inspect_status"
    assert wrapped_reason == "parsed"
    assert wrapped["intent"] == "inspect_status"
    assert invalid_nested == {}
    assert invalid_reason == "invalid_nested_arguments"
    assert non_object == {}
    assert non_object_reason == "payload_not_object"


def test_schema_validator_normalizes_all_optional_argument_types():
    payload = {
        "intent": "compare_patch_candidates",
        "arguments": {
            "scope": "src/pkg",
            "constraints": ["do not change public API", ""],
            "strategy": "minimal diff",
            "function": "pkg.core.load_user",
            "candidate_ids": ["patch:1", "invalid candidate"],
        },
        "confidence": 0.91,
        "reason": "compare the requested candidates",
        "required_context": ["patch_attempt_history", "invalid context"],
    }

    result, errors = validate_llm_intent(payload, message="compare patches")

    assert errors == []
    assert result["scope"] == "src/pkg"
    assert result["constraints"] == ["do not change public API"]
    assert result["strategy"] == "minimal diff"
    assert result["function"] == "pkg.core.load_user"
    assert result["candidate_ids"] == ["patch:1"]
    assert result["required_context"] == ["patch_attempt_history"]


def test_schema_validator_reports_malformed_required_values():
    payload = {
        "intent": "unsupported",
        "arguments": "bad",
        "confidence": "not-a-number",
        "reason": "",
        "required_context": "bad",
    }

    result, errors = validate_llm_intent(payload, message="unknown")

    assert result["intent"] == "ask_for_clarification"
    assert {
        "unsupported_intent",
        "arguments_not_object",
        "invalid_confidence",
        "missing_reason",
        "required_context_not_list",
    }.issubset(set(errors))


def test_schema_validator_requires_safe_function_and_required_fields():
    payload = _payload("inspect_function", function="pkg.core.load_user; rm")

    result, errors = validate_llm_intent(payload, message="inspect it")

    assert result["intent"] == "ask_for_clarification"
    assert "unsafe_function" in errors
    assert result["function"] == ""
