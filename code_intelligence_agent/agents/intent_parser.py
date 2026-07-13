from __future__ import annotations

import re
from typing import Any


INTENT_CONTINUE_REPAIR = "continue_repair"
INTENT_EXPLAIN_FAILURE = "explain_failure"
INTENT_RERUN_TESTS = "rerun_tests"
INTENT_NARROW_SCOPE = "narrow_scope"
INTENT_GENERATE_REPORT = "generate_report"
INTENT_CHANGE_CONSTRAINTS = "change_constraints"
INTENT_CHANGE_REPAIR_STRATEGY = "change_repair_strategy"
INTENT_INSPECT_STATUS = "inspect_status"


def parse_user_intent(message: str) -> dict[str, Any]:
    text = " ".join(str(message or "").strip().split())
    lowered = text.lower()
    scope = _extract_scope(text)
    constraints = _extract_constraints(text)

    if _contains_any(lowered, ["失败", "原因", "为什么", "why", "explain", "failure"]):
        intent = INTENT_EXPLAIN_FAILURE
    elif _contains_any(lowered, ["重跑", "重新运行", "rerun", "run tests", "pytest"]):
        intent = INTENT_RERUN_TESTS
    elif scope and _contains_any(
        lowered,
        ["只分析", "只看", "限定", "目录", "文件", "scope", "only", "include"],
    ):
        intent = INTENT_NARROW_SCOPE
    elif constraints and _contains_any(
        lowered,
        ["不要", "不能", "避免", "禁止", "约束", "constraint", "do not", "don't", "avoid"],
    ):
        intent = INTENT_CHANGE_CONSTRAINTS
    elif _contains_any(
        lowered,
        ["换一种", "换个", "另一种", "不同", "alternate", "alternative", "different", "another"],
    ) and _contains_any(lowered, ["修复", "补丁", "repair", "patch", "fix", "strategy"]):
        intent = INTENT_CHANGE_REPAIR_STRATEGY
    elif _contains_any(lowered, ["报告", "总结", "generate report", "final report", "summary"]):
        intent = INTENT_GENERATE_REPORT
    elif _contains_any(lowered, ["继续", "修复", "补丁", "top-1", "top1", "repair", "patch", "fix"]):
        intent = INTENT_CONTINUE_REPAIR
    else:
        intent = INTENT_INSPECT_STATUS

    return {
        "intent": intent,
        "message": text,
        "scope": scope,
        "constraints": constraints,
        "strategy": _extract_strategy(text),
        "confidence": _intent_confidence(intent, text, scope, constraints),
    }


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


def _extract_scope(text: str) -> str:
    patterns = [
        r"(?:只分析|只看|限定到|限定|scope|only|include)\s*[:：]?\s*`?([A-Za-z0-9_./\\-]+)`?",
        r"`([A-Za-z0-9_./\\-]+)`\s*(?:目录|文件|path|module)?",
        r"([A-Za-z0-9_./\\-]+)\s*(?:目录|文件|path|module)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip("`'\" ")
            if value and value not in {".", "./", "\\"}:
                return value.replace("\\", "/")
    return ""


def _extract_constraints(text: str) -> list[str]:
    constraints: list[str] = []
    patterns = [
        r"(不要[^。.!；;]+)",
        r"(不能[^。.!；;]+)",
        r"(避免[^。.!；;]+)",
        r"(禁止[^。.!；;]+)",
        r"(do not [^.]+)",
        r"(don't [^.]+)",
        r"(avoid [^.]+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            item = " ".join(match.group(1).strip().split())
            if item and item not in constraints:
                constraints.append(item)
    return constraints


def _extract_strategy(text: str) -> str:
    patterns = [
        r"(?:换一种|换个|另一种|different|another|alternative)\s*([^。.!；;]*)",
        r"(?:strategy|方案)\s*[:：]?\s*([^。.!；;]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = " ".join(match.group(1).strip().split())
            if value:
                return value
    return ""


def _intent_confidence(
    intent: str,
    text: str,
    scope: str,
    constraints: list[str],
) -> float:
    if not text:
        return 0.2
    if intent == INTENT_NARROW_SCOPE and scope:
        return 0.9
    if intent == INTENT_CHANGE_CONSTRAINTS and constraints:
        return 0.9
    if intent == INTENT_INSPECT_STATUS:
        return 0.55
    return 0.8
