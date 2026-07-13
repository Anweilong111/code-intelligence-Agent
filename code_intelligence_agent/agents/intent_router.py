from __future__ import annotations

import json
import os
import re
from typing import Any

from code_intelligence_agent.agents.intent_parser import (
    INTENT_ASK_FOR_CLARIFICATION,
    INTENT_CHANGE_CONSTRAINTS,
    INTENT_CHANGE_REPAIR_STRATEGY,
    INTENT_INSPECT_FUNCTION,
    INTENT_NARROW_SCOPE,
    SUPPORTED_INTENTS,
    parse_user_intent,
)
from code_intelligence_agent.agents.llm_client import (
    LLMClient,
    LLMRequestError,
    create_intent_client,
    llm_config_audit,
)


MIN_LLM_INTENT_CONFIDENCE = 0.65
ARGUMENT_KEYS = {
    "scope",
    "constraints",
    "strategy",
    "function",
    "candidate_ids",
}
PAYLOAD_KEYS = {
    "intent",
    "arguments",
    "confidence",
    "reason",
    "required_context",
}
INTENT_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "route_agent_intent",
        "description": (
            "Map one natural-language request to one safe code-intelligence "
            "Agent intent. Use ask_for_clarification for ambiguous requests."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "intent": {"type": "string", "enum": SUPPORTED_INTENTS},
                "arguments": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "scope": {"type": "string"},
                        "constraints": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "strategy": {"type": "string"},
                        "function": {"type": "string"},
                        "candidate_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {"type": "string"},
                "required_context": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "intent",
                "arguments",
                "confidence",
                "reason",
                "required_context",
            ],
        },
    },
}


def route_user_intent(
    message: str,
    *,
    context: dict[str, Any] | None = None,
    client: LLMClient | None = None,
    llm_enabled: bool | None = None,
) -> dict[str, Any]:
    fallback = parse_user_intent(message)
    audit = llm_config_audit("intent", enabled=True)
    enabled = _llm_routing_enabled(
        explicit=llm_enabled,
        client_supplied=client is not None,
        api_key_present=audit.api_key_present,
    )
    if not enabled:
        return _fallback_intent(
            fallback,
            reason="llm_intent_routing_disabled_or_unconfigured",
            config=audit.to_dict(),
        )
    active_client = client
    if active_client is None:
        try:
            active_client = create_intent_client()
        except ValueError:
            return _fallback_intent(
                fallback,
                reason="missing_intent_llm_api_key",
                config=audit.to_dict(),
            )
    prompt = build_intent_router_prompt(message, context=context)
    try:
        response, transport = _complete_intent_request(active_client, prompt)
    except LLMRequestError as exc:
        return _fallback_intent(
            fallback,
            reason=f"llm_request_error:{exc.reason}",
            config=audit.to_dict(),
            llm_metadata=_safe_llm_metadata(exc.metadata),
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        return _fallback_intent(
            fallback,
            reason=f"llm_router_error:{type(exc).__name__}",
            config=audit.to_dict(),
        )
    payload, parse_reason = parse_intent_tool_payload(response.text)
    if not payload:
        return _fallback_intent(
            fallback,
            reason=f"invalid_llm_intent_payload:{parse_reason}",
            config=audit.to_dict(),
            llm_metadata=_safe_llm_metadata(response.metadata),
            transport=transport,
        )
    tool_call_value = _safe_llm_metadata(response.metadata).get("tool_call", {})
    tool_call = tool_call_value if isinstance(tool_call_value, dict) else {}
    if tool_call and str(tool_call.get("name") or "") != "route_agent_intent":
        return _fallback_intent(
            fallback,
            reason="unexpected_llm_tool_name",
            config=audit.to_dict(),
            llm_metadata=_safe_llm_metadata(response.metadata),
            transport=transport,
        )
    intent, validation_errors = validate_llm_intent(payload, message=message)
    if validation_errors:
        return _fallback_intent(
            fallback,
            reason="invalid_llm_intent_schema:" + ",".join(validation_errors),
            config=audit.to_dict(),
            llm_metadata=_safe_llm_metadata(response.metadata),
            transport=transport,
        )
    intent["router_audit"] = {
        "status": "pass",
        "source": "llm",
        "transport": transport,
        "schema_valid": True,
        "fallback_used": False,
        "config": audit.to_dict(),
        "llm_metadata": _safe_llm_metadata(response.metadata),
    }
    return intent


def build_intent_router_prompt(
    message: str,
    *,
    context: dict[str, Any] | None = None,
) -> str:
    compact_context = _compact_context(context or {})
    return "\n".join(
        [
            "Route exactly one user turn for a code-intelligence Agent.",
            "Do not create or execute shell commands.",
            "Choose only an intent in the tool schema.",
            "Use ask_for_clarification when multiple actions are requested, the target is missing, or confidence is below 0.65.",
            "Paths must be repository-relative. Function names must be Python identifiers or dotted qualified names.",
            "Session context:",
            json.dumps(compact_context, ensure_ascii=False, sort_keys=True),
            "User message:",
            str(message or "").strip(),
        ]
    )


def parse_intent_tool_payload(text: str) -> tuple[dict[str, Any], str]:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return {}, "invalid_json"
    if not isinstance(payload, dict):
        return {}, "payload_not_object"
    if isinstance(payload.get("arguments"), str) and payload.get("name"):
        try:
            nested = json.loads(str(payload["arguments"]))
        except json.JSONDecodeError:
            return {}, "invalid_nested_arguments"
        payload = nested if isinstance(nested, dict) else {}
    return payload, "parsed"


def validate_llm_intent(
    payload: dict[str, Any],
    *,
    message: str,
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    if set(payload) - PAYLOAD_KEYS:
        errors.append("unknown_payload_fields")
    intent_name = str(payload.get("intent") or "")
    if intent_name not in SUPPORTED_INTENTS:
        errors.append("unsupported_intent")
    arguments_value = payload.get("arguments")
    arguments = arguments_value if isinstance(arguments_value, dict) else {}
    if not isinstance(arguments_value, dict):
        errors.append("arguments_not_object")
    unknown_arguments = sorted(set(arguments) - ARGUMENT_KEYS)
    if unknown_arguments:
        errors.append("unknown_arguments")
    confidence = _confidence(payload.get("confidence"))
    if confidence is None:
        errors.append("invalid_confidence")
        confidence = 0.0
    reason = _safe_text(payload.get("reason"), limit=500)
    if not reason:
        errors.append("missing_reason")
    required_context_value = payload.get("required_context")
    if not isinstance(required_context_value, list):
        errors.append("required_context_not_list")
        required_context_value = []
    required_context = [
        item
        for item in (
            _safe_identifier(value, allow_dots=False)
            for value in required_context_value[:20]
        )
        if item
    ]
    normalized_arguments: dict[str, Any] = {}
    scope = _safe_scope(arguments.get("scope"))
    if arguments.get("scope") and not scope:
        errors.append("unsafe_scope")
    if scope:
        normalized_arguments["scope"] = scope
    constraints_value = arguments.get("constraints", [])
    if constraints_value and not isinstance(constraints_value, list):
        errors.append("constraints_not_list")
    constraints = [
        item
        for item in (
            _safe_text(value, limit=500)
            for value in (
                constraints_value[:20]
                if isinstance(constraints_value, list)
                else []
            )
        )
        if item
    ]
    if constraints:
        normalized_arguments["constraints"] = constraints
    strategy = _safe_text(arguments.get("strategy"), limit=300)
    if strategy:
        normalized_arguments["strategy"] = strategy
    function_name = _safe_identifier(arguments.get("function"), allow_dots=True)
    if arguments.get("function") and not function_name:
        errors.append("unsafe_function")
    if function_name:
        normalized_arguments["function"] = function_name
    candidate_values = arguments.get("candidate_ids", [])
    if candidate_values and not isinstance(candidate_values, list):
        errors.append("candidate_ids_not_list")
    candidate_ids = [
        item
        for item in (
            _safe_candidate_id(value)
            for value in (
                candidate_values[:20] if isinstance(candidate_values, list) else []
            )
        )
        if item
    ]
    if candidate_ids:
        normalized_arguments["candidate_ids"] = candidate_ids
    missing_target = (
        intent_name == INTENT_NARROW_SCOPE
        and not normalized_arguments.get("scope")
    ) or (
        intent_name == INTENT_CHANGE_CONSTRAINTS
        and not normalized_arguments.get("constraints")
    ) or (
        intent_name == INTENT_CHANGE_REPAIR_STRATEGY
        and not normalized_arguments.get("strategy")
    ) or (
        intent_name == INTENT_INSPECT_FUNCTION
        and not normalized_arguments.get("function")
    )
    if missing_target:
        intent_name = INTENT_ASK_FOR_CLARIFICATION
        reason = "The requested action is missing a required target or argument."
        confidence = min(confidence, 0.5)
    if confidence < MIN_LLM_INTENT_CONFIDENCE:
        intent_name = INTENT_ASK_FOR_CLARIFICATION
        reason = reason or "Intent confidence is below the execution threshold."
    intent = {
        "intent": intent_name,
        "message": " ".join(str(message or "").strip().split()),
        "arguments": normalized_arguments,
        "scope": str(normalized_arguments.get("scope") or ""),
        "constraints": list(normalized_arguments.get("constraints") or []),
        "strategy": str(normalized_arguments.get("strategy") or ""),
        "function": str(normalized_arguments.get("function") or ""),
        "candidate_ids": list(normalized_arguments.get("candidate_ids") or []),
        "confidence": confidence,
        "reason": reason,
        "required_context": required_context,
        "source": "llm",
    }
    return intent, errors


def _complete_intent_request(
    client: LLMClient,
    prompt: str,
) -> tuple[Any, str]:
    tool_method = getattr(client, "complete_with_tools", None)
    if callable(tool_method):
        response = tool_method(
            prompt,
            [INTENT_TOOL_DEFINITION],
            tool_choice={
                "type": "function",
                "function": {"name": "route_agent_intent"},
            },
        )
        return response, "function_call"
    return client.complete(
        prompt
        + "\nReturn only the JSON arguments required by route_agent_intent."
    ), "json_fallback"


def _fallback_intent(
    fallback: dict[str, Any],
    *,
    reason: str,
    config: dict[str, Any],
    llm_metadata: dict[str, Any] | None = None,
    transport: str = "none",
) -> dict[str, Any]:
    result = dict(fallback)
    result["source"] = "rule_fallback"
    result["router_audit"] = {
        "status": "fallback",
        "source": "rule_fallback",
        "transport": transport,
        "schema_valid": False,
        "fallback_used": True,
        "fallback_reason": reason,
        "config": config,
        "llm_metadata": llm_metadata or {},
    }
    return result


def _llm_routing_enabled(
    *,
    explicit: bool | None,
    client_supplied: bool,
    api_key_present: bool,
) -> bool:
    if explicit is not None:
        return bool(explicit)
    env_value = str(os.environ.get("CIA_INTENT_LLM_ENABLED") or "").strip().lower()
    if env_value in {"0", "false", "no", "off"}:
        return False
    if env_value in {"1", "true", "yes", "on"}:
        return True
    return bool(client_supplied or api_key_present)


def _compact_context(context: dict[str, Any]) -> dict[str, Any]:
    allowed = [
        "repo",
        "current_status",
        "active_scope",
        "latest_blocker",
        "test_status",
        "topk_functions",
        "constraints",
        "repair_strategy_preferences",
        "recent_intents",
        "execution_stopped",
    ]
    compact: dict[str, Any] = {}
    for key in allowed:
        value = context.get(key)
        if isinstance(value, list):
            compact[key] = value[:10]
        elif isinstance(value, (str, int, float, bool)) or value is None:
            compact[key] = value
    return compact


def _safe_llm_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    source = metadata if isinstance(metadata, dict) else {}
    return {
        key: source.get(key)
        for key in [
            "status",
            "provider",
            "model",
            "latency_ms",
            "usage",
            "cost_estimate",
            "api_key_present",
            "api_key_fingerprint",
            "tool_call",
            "error_type",
            "error_reason",
        ]
        if key in source
    }


def _safe_scope(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or len(text) > 300:
        return ""
    if text.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", text):
        return ""
    if ".." in text.split("/"):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", text):
        return ""
    return text.strip("/")


def _safe_identifier(value: Any, *, allow_dots: bool) -> str:
    text = str(value or "").strip()
    pattern = (
        r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
        if allow_dots
        else r"[A-Za-z_][A-Za-z0-9_]*"
    )
    return text if re.fullmatch(pattern, text) else ""


def _safe_candidate_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", text) else ""


def _safe_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text or len(text) > limit or any(ord(char) < 32 for char in text):
        return ""
    return text


def _confidence(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number > 1:
        return None
    return round(number, 4)
