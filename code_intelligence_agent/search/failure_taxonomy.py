from __future__ import annotations

from code_intelligence_agent.core.models import ExecutionResult


def classify_execution_result(result: ExecutionResult) -> str:
    if result.success:
        return "success"
    combined = "\n".join(
        part for part in [result.stdout, result.stderr, result.traceback] if part
    )
    normalized = combined.lower()
    if result.timeout:
        return "timeout"
    if "safety_gate_blocked" in normalized or "blocked by safety gate" in normalized:
        return "safety_gate_blocked"
    if "patch target does not exist" in normalized:
        return "patch_apply_error"
    if "original source block not found" in normalized:
        return "patch_apply_error"
    if "syntaxerror" in normalized:
        return "syntax_error"
    if "importerror" in normalized or "modulenotfounderror" in normalized:
        return "import_error"
    if "attributeerror" in normalized:
        return "attribute_error"
    if "typeerror" in normalized:
        return "type_error"
    if "assertionerror" in normalized or result.failed > 0:
        return "test_failure"
    if "traceback" in normalized or result.returncode not in {0, -1}:
        return "runtime_error"
    if result.returncode == -1:
        return "execution_error"
    return "unknown_failure"


def summarize_failure_reason(result: ExecutionResult, limit: int = 120) -> str:
    if result.success:
        return ""
    text = "\n".join(
        part.strip()
        for part in [result.traceback, result.stderr, result.stdout]
        if part and part.strip()
    )
    if not text:
        return classify_execution_result(result)
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if len(first_line) <= limit:
        return first_line
    return first_line[: limit - 3] + "..."
