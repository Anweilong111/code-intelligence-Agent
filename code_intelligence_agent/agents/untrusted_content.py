from __future__ import annotations

import hashlib
import re
from typing import Any


UNTRUSTED_CONTENT_POLICY_VERSION = 1
_MAX_DEPTH = 8
_MAX_ITEMS = 80
_MAX_STRING_CHARS = 2000
_INJECTION_PATTERNS = (
    (
        "instruction_override",
        re.compile(
            r"\b(?:ignore|disregard|override|forget)\b.{0,80}\b(?:previous|prior|system|developer|instructions?|rules?)\b",
            flags=re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "role_impersonation",
        re.compile(
            r"\b(?:system|developer|assistant)\s*(?:message|prompt|instruction)|\byou are (?:chatgpt|an? ai|the system)\b",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "secret_exfiltration",
        re.compile(
            r"\b(?:reveal|print|dump|send|exfiltrate|upload)\b.{0,100}\b(?:api[_ -]?key|token|password|secret|credential|environment variables?)\b",
            flags=re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "tool_or_shell_directive",
        re.compile(
            r"\b(?:call|invoke|use|run|execute)\b.{0,80}\b(?:tool|shell|terminal|powershell|cmd|curl|wget)\b",
            flags=re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "safety_bypass",
        re.compile(
            r"\b(?:bypass|disable|evade|skip)\b.{0,80}\b(?:safety|sandbox|policy|guard|validation|approval)\b",
            flags=re.IGNORECASE | re.DOTALL,
        ),
    ),
)


def sanitize_untrusted_content(
    value: Any,
    *,
    source: str,
) -> dict[str, Any]:
    signals: list[dict[str, str]] = []
    truncated_paths: list[str] = []

    def sanitize(item: Any, path: str, depth: int) -> Any:
        if depth > _MAX_DEPTH:
            truncated_paths.append(path)
            return "[TRUNCATED_UNTRUSTED_CONTENT depth_limit]"
        if isinstance(item, dict):
            output: dict[str, Any] = {}
            for index, (key, child) in enumerate(item.items()):
                if index >= _MAX_ITEMS:
                    truncated_paths.append(path)
                    break
                safe_key = str(key)[:160]
                output[safe_key] = sanitize(
                    child,
                    f"{path}.{safe_key}" if path else safe_key,
                    depth + 1,
                )
            return output
        if isinstance(item, list):
            output = []
            for index, child in enumerate(item[:_MAX_ITEMS]):
                output.append(sanitize(child, f"{path}[{index}]", depth + 1))
            if len(item) > _MAX_ITEMS:
                truncated_paths.append(path)
            return output
        if isinstance(item, tuple):
            return sanitize(list(item), path, depth)
        if not isinstance(item, str):
            return item
        categories = detect_prompt_injection(item)
        fingerprint = hashlib.sha256(item.encode("utf-8", errors="replace")).hexdigest()
        if categories:
            signals.append(
                {
                    "path": path or "$",
                    "categories": ",".join(categories),
                    "fingerprint": fingerprint,
                }
            )
            return (
                "[QUARANTINED_REPOSITORY_CONTENT "
                f"sha256={fingerprint} categories={','.join(categories)}]"
            )
        if len(item) > _MAX_STRING_CHARS:
            truncated_paths.append(path or "$")
            return item[:_MAX_STRING_CHARS] + "[TRUNCATED]"
        return item

    sanitized = sanitize(value, "$", 0)
    return {
        "value": sanitized,
        "audit": {
            "schema_version": UNTRUSTED_CONTENT_POLICY_VERSION,
            "status": "quarantined" if signals else "clear",
            "reason": (
                "repository_prompt_injection_signals_quarantined"
                if signals
                else "no_repository_prompt_injection_signal"
            ),
            "source": str(source or "repository_evidence"),
            "trust_class": "untrusted_repository_data",
            "instruction_authority": "none",
            "signal_count": len(signals),
            "signals": signals,
            "truncated_path_count": len(set(truncated_paths)),
            "truncated_paths": sorted(set(truncated_paths))[:40],
            "raw_flagged_content_included": False,
        },
    }


def detect_prompt_injection(text: str) -> list[str]:
    value = str(text or "")
    return [name for name, pattern in _INJECTION_PATTERNS if pattern.search(value)]
