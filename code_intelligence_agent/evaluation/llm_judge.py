from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from code_intelligence_agent.agents.llm_client import (
    LLMClient,
    create_judge_client,
)


@dataclass(frozen=True)
class LLMJudgment:
    score: float
    verdict: str
    reason: str
    model: str | None = None

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "verdict": self.verdict,
            "reason": self.reason,
            "model": self.model,
        }


class LLMJudge:
    """LLM-as-judge wrapper for benchmark result summaries."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def judge_case(self, case_payload: dict[str, Any]) -> LLMJudgment:
        response = self.client.complete(_judge_prompt(case_payload))
        model = response.metadata.get("model")
        model_name = str(model) if model else None
        try:
            judgment = parse_judgment(response.text)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            return LLMJudgment(
                score=0.0,
                verdict="fail",
                reason=f"Invalid LLM judge response: {exc}",
                model=model_name,
            )
        return LLMJudgment(
            score=judgment.score,
            verdict=judgment.verdict,
            reason=judgment.reason,
            model=model_name or judgment.model,
        )


def build_judge(mode: str) -> LLMJudge | None:
    if mode == "none":
        return None
    if mode == "llm":
        return LLMJudge(create_judge_client())
    raise ValueError(f"Unsupported judge mode: {mode}")


def parse_judgment(text: str) -> LLMJudgment:
    payload = _load_json_object(text)
    score = _clamp_score(float(payload.get("score", 0.0)))
    verdict = _normalize_verdict(str(payload.get("verdict", "fail")))
    reason = str(payload.get("reason", "")).strip()
    model = payload.get("model")
    return LLMJudgment(
        score=score,
        verdict=verdict,
        reason=reason,
        model=str(model) if model else None,
    )


def _judge_prompt(case_payload: dict[str, Any]) -> str:
    compact_payload = json.dumps(case_payload, ensure_ascii=False, indent=2)
    return (
        "Evaluate this code-intelligence benchmark case result.\n"
        "Judge whether the system correctly localized the bug, detected the "
        "expected rule, generated a low-risk patch, and passed sandbox tests.\n"
        "Use this rubric: score 1.0 for correct Top-1 localization plus "
        "successful patch validation; 0.7-0.9 for correct localization with "
        "partial repair evidence; 0.4-0.6 for useful but incomplete evidence; "
        "0.0-0.3 for failed localization or unverified repair.\n"
        "Return only JSON in this schema:\n"
        '{"score": 0.0, "verdict": "pass|partial|fail", "reason": "..."}\n'
        "Do not include Markdown.\n\n"
        f"CASE_RESULT:\n{compact_payload}"
    )


def _load_json_object(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _strip_code_fence(stripped)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if match is None:
            raise
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("LLM judge response must be a JSON object.")
    return payload


def _strip_code_fence(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _normalize_verdict(verdict: str) -> str:
    normalized = verdict.strip().lower()
    if normalized in {"pass", "partial", "fail"}:
        return normalized
    return "fail"


def _clamp_score(score: float) -> float:
    return max(0.0, min(1.0, round(score, 4)))
