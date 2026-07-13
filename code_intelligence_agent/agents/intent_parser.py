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
INTENT_INSPECT_FUNCTION = "inspect_function"
INTENT_EXPLAIN_LOCALIZATION = "explain_localization"
INTENT_COMPARE_PATCH_CANDIDATES = "compare_patch_candidates"
INTENT_ROLLBACK_LAST_ACTION = "rollback_last_action"
INTENT_STOP_EXECUTION = "stop_execution"
INTENT_ASK_FOR_CLARIFICATION = "ask_for_clarification"

SUPPORTED_INTENTS = [
    INTENT_INSPECT_STATUS,
    INTENT_EXPLAIN_FAILURE,
    INTENT_CONTINUE_REPAIR,
    INTENT_RERUN_TESTS,
    INTENT_NARROW_SCOPE,
    INTENT_CHANGE_CONSTRAINTS,
    INTENT_CHANGE_REPAIR_STRATEGY,
    INTENT_GENERATE_REPORT,
    INTENT_INSPECT_FUNCTION,
    INTENT_EXPLAIN_LOCALIZATION,
    INTENT_COMPARE_PATCH_CANDIDATES,
    INTENT_ROLLBACK_LAST_ACTION,
    INTENT_STOP_EXECUTION,
    INTENT_ASK_FOR_CLARIFICATION,
]


def parse_user_intent(message: str) -> dict[str, Any]:
    text = " ".join(str(message or "").strip().split())
    lowered = text.lower()
    scope = _extract_scope(text)
    constraints = _extract_constraints(text)
    strategy = _extract_strategy(text)
    function_name = _extract_function(text)
    matches = _matched_intents(
        lowered,
        scope=scope,
        constraints=constraints,
        strategy=strategy,
        function_name=function_name,
    )
    if len(matches) == 1:
        intent = matches[0]
        reason = f"rule matched {intent}"
    elif len(matches) > 1:
        intent = INTENT_ASK_FOR_CLARIFICATION
        reason = "multiple intents detected: " + ", ".join(matches)
    else:
        intent = INTENT_ASK_FOR_CLARIFICATION
        reason = "no supported task intent was detected"

    arguments: dict[str, Any] = {}
    if scope:
        arguments["scope"] = scope
    if constraints:
        arguments["constraints"] = constraints
    if strategy:
        arguments["strategy"] = strategy
    if function_name:
        arguments["function"] = function_name

    return {
        "intent": intent,
        "message": text,
        "arguments": arguments,
        "scope": scope,
        "constraints": constraints,
        "strategy": strategy,
        "function": function_name,
        "confidence": _intent_confidence(intent, text, scope, constraints),
        "reason": reason,
        "required_context": _required_context(intent),
        "source": "rule",
    }


def _matched_intents(
    text: str,
    *,
    scope: str,
    constraints: list[str],
    strategy: str,
    function_name: str,
) -> list[str]:
    matches: list[str] = []
    failure_terms = [
        "失败",
        "报错",
        "异常",
        "blocker",
        "failure",
        "failed",
        "fail",
        "error",
    ]
    explanation_terms = ["原因", "为什么", "解释", "why", "explain", "reason"]
    if _contains_any(text, failure_terms) and _contains_any(text, explanation_terms):
        matches.append(INTENT_EXPLAIN_FAILURE)
    if _contains_any(text, ["重跑", "重新运行", "rerun", "run tests", "run pytest"]):
        matches.append(INTENT_RERUN_TESTS)
    if scope and _contains_any(
        text,
        ["只分析", "只看", "限定", "目录", "文件", "scope", "only", "include"],
    ):
        matches.append(INTENT_NARROW_SCOPE)
    if constraints and _contains_any(
        text,
        ["不要", "不能", "避免", "禁止", "约束", "constraint", "do not", "don't", "avoid"],
    ):
        matches.append(INTENT_CHANGE_CONSTRAINTS)
    strategy_requested = bool(strategy) and _contains_any(
        text,
        ["换一种", "换个", "另一种", "不同", "alternate", "alternative", "different", "another", "strategy", "方案"],
    )
    if strategy_requested and _contains_any(
        text, ["修复", "补丁", "repair", "patch", "fix", "strategy", "方案"]
    ):
        matches.append(INTENT_CHANGE_REPAIR_STRATEGY)
    if _contains_any(
        text,
        [
            "比较补丁",
            "对比补丁",
            "比较候选",
            "compare patches",
            "compare candidates",
            "compare patch candidates",
        ],
    ):
        matches.append(INTENT_COMPARE_PATCH_CANDIDATES)
    if _contains_any(
        text,
        [
            "定位结果",
            "定位得分",
            "为什么可疑",
            "为什么排",
            "可疑",
            "排在",
            "explain localization",
            "why suspicious",
            "suspicious",
            "ranking score",
        ],
    ):
        matches.append(INTENT_EXPLAIN_LOCALIZATION)
    if function_name and _contains_any(
        text,
        ["查看函数", "检查函数", "分析函数", "函数详情", "inspect function", "show function", "analyze function"],
    ):
        matches.append(INTENT_INSPECT_FUNCTION)
    if _contains_any(
        text,
        ["回滚", "撤销上一步", "撤销刚才", "rollback", "undo last"],
    ):
        matches.append(INTENT_ROLLBACK_LAST_ACTION)
    if _contains_any(
        text,
        ["停止执行", "终止执行", "取消执行", "stop execution", "cancel execution", "abort"],
    ):
        matches.append(INTENT_STOP_EXECUTION)
    if _contains_any(
        text,
        ["生成报告", "最终报告", "导出报告", "generate report", "final report", "export report"],
    ):
        matches.append(INTENT_GENERATE_REPORT)
    if not strategy_requested and _contains_any(
        text,
        ["继续修复", "生成补丁", "修复 top-1", "repair top-1", "continue repair", "generate patch", "fix top-1"],
    ):
        matches.append(INTENT_CONTINUE_REPAIR)
    if _contains_any(
        text,
        ["当前状态", "查看状态", "当前进度", "项目进度", "inspect status", "show status", "current status", "progress"],
    ):
        matches.append(INTENT_INSPECT_STATUS)
    return list(dict.fromkeys(matches))


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
            normalized = value.replace("\\", "/")
            if (
                normalized
                and normalized not in {".", "./", "/"}
                and not normalized.startswith(("/", "~"))
                and not re.match(r"^[A-Za-z]:", normalized)
                and ".." not in normalized.split("/")
            ):
                return normalized
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


def _extract_function(text: str) -> str:
    patterns = [
        r"(?:查看函数|检查函数|分析函数|函数详情|inspect function|show function|analyze function)\s*[:：]?\s*`?([A-Za-z_][A-Za-z0-9_.]*)`?",
        r"`([A-Za-z_][A-Za-z0-9_.]*)`\s*(?:函数|function)",
        r"([A-Za-z_][A-Za-z0-9_.]*)\s*(?:函数|function)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _required_context(intent: str) -> list[str]:
    mapping = {
        INTENT_EXPLAIN_FAILURE: ["test_results", "blocker_evolution"],
        INTENT_CONTINUE_REPAIR: ["topk_suspicious_functions", "patch_attempt_history"],
        INTENT_RERUN_TESTS: ["test_command"],
        INTENT_INSPECT_FUNCTION: ["topk_suspicious_functions", "repo_memory"],
        INTENT_EXPLAIN_LOCALIZATION: ["topk_suspicious_functions", "score_breakdown"],
        INTENT_COMPARE_PATCH_CANDIDATES: ["patch_attempt_history"],
        INTENT_ROLLBACK_LAST_ACTION: ["action_history", "rollback_capability"],
    }
    return list(mapping.get(intent, []))


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
    if intent == INTENT_ASK_FOR_CLARIFICATION:
        return 0.25
    if intent == INTENT_INSPECT_STATUS:
        return 0.55
    return 0.8
