from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from code_intelligence_agent.agents.llm_client import (
    LLMClient,
    create_localization_client,
)
from code_intelligence_agent.core.models import BugFinding, TestExecutionSummary
from code_intelligence_agent.core.program_graph import ProgramGraph


@dataclass(frozen=True)
class LLMFunctionScore:
    function_id: str
    score: float
    reason: str = ""


class LLMFaultScorer:
    """LLM-backed scorer for function-level fault-localization candidates."""

    def __init__(self, client: LLMClient, max_candidates: int = 8) -> None:
        self.client = client
        self.max_candidates = max_candidates

    def score(
        self,
        *,
        program_graph: ProgramGraph,
        findings: list[BugFinding],
        test_summary: TestExecutionSummary,
        candidate_function_ids: list[str],
    ) -> dict[str, float]:
        selected_ids = [
            function_id
            for function_id in candidate_function_ids[: self.max_candidates]
            if function_id in program_graph.functions
        ]
        if not selected_ids:
            return {}
        prompt = _score_prompt(
            program_graph=program_graph,
            findings=findings,
            test_summary=test_summary,
            candidate_function_ids=selected_ids,
        )
        response = self.client.complete(prompt)
        parsed = parse_llm_function_scores(response.text)
        selected = set(selected_ids)
        return {
            item.function_id: item.score
            for item in parsed
            if item.function_id in selected
        }


def build_llm_fault_scorer(mode: str) -> LLMFaultScorer | None:
    if mode == "none":
        return None
    if mode == "llm":
        return LLMFaultScorer(create_localization_client())
    raise ValueError(f"Unsupported LLM score mode: {mode}")


def parse_llm_function_scores(text: str) -> list[LLMFunctionScore]:
    payload = _load_json_object(text)
    raw_scores = payload.get("scores", payload.get("functions", []))
    if not isinstance(raw_scores, list):
        raise ValueError("LLM fault scorer response must contain a scores list.")
    scores = []
    for item in raw_scores:
        if not isinstance(item, dict):
            continue
        function_id = item.get("function_id") or item.get("id")
        if not function_id:
            continue
        try:
            score = _clamp_score(float(item.get("score", 0.0)))
        except (TypeError, ValueError):
            score = 0.0
        scores.append(
            LLMFunctionScore(
                function_id=str(function_id),
                score=score,
                reason=str(item.get("reason", "")).strip(),
            )
        )
    return scores


def _score_prompt(
    *,
    program_graph: ProgramGraph,
    findings: list[BugFinding],
    test_summary: TestExecutionSummary,
    candidate_function_ids: list[str],
) -> str:
    findings_by_function: dict[str, list[BugFinding]] = {}
    for finding in findings:
        findings_by_function.setdefault(finding.function_id, []).append(finding)
    payload = {
        "failure_context": _failure_context(test_summary, program_graph),
        "candidates": [
            _candidate_payload(
                program_graph=program_graph,
                function_id=function_id,
                findings=findings_by_function.get(function_id, []),
            )
            for function_id in candidate_function_ids
        ],
    }
    return (
        "Score each candidate function for likelihood of being the root cause "
        "of the failing tests. Use the failure context, static rule evidence, "
        "function name, file path, line range, and source excerpt. Return only "
        "JSON in this schema:\n"
        '{"scores":[{"function_id":"...","score":0.0,"reason":"..."}]}\n'
        "Scores must be between 0 and 1. Do not include Markdown.\n\n"
        f"LOCALIZATION_INPUT:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _failure_context(
    test_summary: TestExecutionSummary,
    program_graph: ProgramGraph,
) -> list[dict[str, str]]:
    context = []
    for test_id in sorted(test_summary.failed_tests):
        test_function = program_graph.functions.get(test_id)
        context.append(
            {
                "test_id": test_id,
                "test_name": (
                    test_summary.test_names.get(test_id)
                    or (test_function.name if test_function is not None else test_id)
                ),
                "message": _truncate(test_summary.failure_messages.get(test_id, "")),
            }
        )
    return context


def _candidate_payload(
    *,
    program_graph: ProgramGraph,
    function_id: str,
    findings: list[BugFinding],
) -> dict[str, Any]:
    function = program_graph.functions[function_id]
    return {
        "function_id": function_id,
        "function_name": function.metadata.get("qualified_name", function.name),
        "file_path": function.file_path,
        "line_range": [function.start_line, function.end_line],
        "source_excerpt": _truncate(function.source, limit=2500),
        "static_findings": [
            {
                "rule_id": finding.rule_id,
                "bug_type": finding.bug_type,
                "message": finding.message,
                "confidence": finding.confidence,
                "line": finding.line,
            }
            for finding in findings
        ],
    }


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
        raise ValueError("LLM fault scorer response must be a JSON object.")
    return payload


def _strip_code_fence(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _truncate(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...<truncated>..."


def _clamp_score(score: float) -> float:
    return max(0.0, min(1.0, round(score, 4)))
