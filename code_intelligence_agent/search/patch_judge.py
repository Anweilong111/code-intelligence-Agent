from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Protocol

from code_intelligence_agent.agents.llm_client import (
    LLMClient,
    create_judge_client,
)
from code_intelligence_agent.core.models import ExecutionResult, PatchCandidate
from code_intelligence_agent.search.scoring import diff_size


@dataclass(frozen=True)
class PatchJudgment:
    score: float
    verdict: str
    reason: str
    model: str | None = None
    calibrated_score: float | None = None
    agreement: str = ""
    calibration_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class PatchJudge(Protocol):
    def judge_patch(
        self,
        *,
        candidate: PatchCandidate,
        execution_result: ExecutionResult,
        localization_confidence: float = 0.0,
        patch_risk: float = 0.0,
    ) -> PatchJudgment:
        ...


class LLMPatchJudge:
    """LLM judge for patch candidates using structured evidence only."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def judge_patch(
        self,
        *,
        candidate: PatchCandidate,
        execution_result: ExecutionResult,
        localization_confidence: float = 0.0,
        patch_risk: float = 0.0,
    ) -> PatchJudgment:
        payload = patch_judge_payload(
            candidate=candidate,
            execution_result=execution_result,
            localization_confidence=localization_confidence,
            patch_risk=patch_risk,
        )
        response = self.client.complete(_patch_judge_prompt(payload))
        model = response.metadata.get("model")
        model_name = str(model) if model else None
        try:
            judgment = parse_patch_judgment(response.text)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            return PatchJudgment(
                score=0.0,
                verdict="reject",
                reason=f"Invalid LLM patch judge response: {exc}",
                model=model_name,
            )
        return PatchJudgment(
            score=judgment.score,
            verdict=judgment.verdict,
            reason=judgment.reason,
            model=model_name or judgment.model,
        )


def build_patch_judge(mode: str) -> PatchJudge | None:
    if mode == "none":
        return None
    if mode == "llm":
        return LLMPatchJudge(create_judge_client())
    raise ValueError(f"Unsupported patch judge mode: {mode}")


def parse_patch_judgment(text: str) -> PatchJudgment:
    payload = _load_json_object(text)
    score = _clamp_score(float(payload.get("score", 0.0)))
    verdict = _normalize_verdict(str(payload.get("verdict", "reject")))
    reason = str(payload.get("reason", "")).strip()
    model = payload.get("model")
    return PatchJudgment(
        score=score,
        verdict=verdict,
        reason=reason,
        model=str(model) if model else None,
    )


def patch_judge_payload(
    *,
    candidate: PatchCandidate,
    execution_result: ExecutionResult,
    localization_confidence: float = 0.0,
    patch_risk: float = 0.0,
) -> dict[str, Any]:
    metadata = candidate.metadata
    return {
        "candidate_id": candidate.id,
        "target_function_id": candidate.target_function_id,
        "target_function_name": candidate.target_function_name,
        "target_file": candidate.relative_file_path or candidate.target_file,
        "rule_id": candidate.rule_id,
        "variant": metadata.get("variant", ""),
        "localization_confidence": round(float(localization_confidence), 4),
        "patch_risk": round(float(patch_risk), 4),
        "diff_size": diff_size(candidate.diff),
        "execution_result": {
            "success": execution_result.success,
            "returncode": execution_result.returncode,
            "passed": execution_result.passed,
            "failed": execution_result.failed,
            "timeout": execution_result.timeout,
        },
        "validation": _dict_metadata(metadata.get("validation")),
        "execution_feedback": _dict_metadata(metadata.get("execution_feedback")),
        "risk": _dict_metadata(metadata.get("risk")),
        "candidate_diversity": _dict_metadata(metadata.get("candidate_diversity")),
        "refinement_context": _context_summary(metadata.get("refinement_context")),
        "source_fingerprint": str(metadata.get("source_fingerprint", "")),
        "edit_fingerprint": str(metadata.get("edit_fingerprint", "")),
    }


def apply_patch_judgment_score(
    base_score: float,
    judgment: PatchJudgment | None,
    weight: float,
) -> float:
    if judgment is None or weight <= 0:
        return base_score
    weight = max(0.0, min(1.0, weight))
    judge_score = (
        judgment.calibrated_score
        if judgment.calibrated_score is not None
        else judgment.score
    )
    score = (1.0 - weight) * base_score + weight * judge_score
    return round(max(0.0, min(1.0, score)), 4)


def calibrate_patch_judgment(
    judgment: PatchJudgment,
    *,
    candidate: PatchCandidate,
    execution_result: ExecutionResult,
    patch_risk: float = 0.0,
) -> PatchJudgment:
    evidence_score, reasons = _patch_evidence_score(
        candidate=candidate,
        execution_result=execution_result,
        patch_risk=patch_risk,
    )
    calibrated = 0.65 * judgment.score + 0.35 * evidence_score
    cap = _calibration_cap(candidate, execution_result)
    floor = 0.55 if execution_result.success else 0.0
    if calibrated > cap:
        reasons.append(f"capped_by_execution_evidence={cap:.2f}")
    if execution_result.success and calibrated < floor:
        reasons.append(f"raised_by_sandbox_success_floor={floor:.2f}")
    calibrated = max(floor, min(cap, calibrated))
    return replace(
        judgment,
        calibrated_score=round(max(0.0, min(1.0, calibrated)), 4),
        agreement=_judge_evidence_agreement(judgment.score, evidence_score),
        calibration_reasons=reasons,
    )


def _patch_judge_prompt(payload: dict[str, Any]) -> str:
    evidence = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        "Evaluate this patch candidate using only structured evidence.\n"
        "Do not assume access to full repository source code; old_source, "
        "new_source, and raw diff are intentionally omitted.\n"
        "Prefer candidates with sandbox success, high passed ratio, low risk, "
        "valid AST/scope checks, useful execution feedback, and coherent graph "
        "context. Penalize syntax/import failures, timeouts, high-risk broad "
        "edits, and low-diversity duplicate candidates.\n"
        "Return only JSON in this schema:\n"
        '{"score": 0.0, "verdict": "prefer|accept|reject", "reason": "..."}\n'
        "Do not include Markdown.\n\n"
        f"PATCH_CANDIDATE_EVIDENCE:\n{evidence}"
    )


def _patch_evidence_score(
    *,
    candidate: PatchCandidate,
    execution_result: ExecutionResult,
    patch_risk: float,
) -> tuple[float, list[str]]:
    total_tests = execution_result.passed + execution_result.failed
    if total_tests:
        passed_ratio = execution_result.passed / total_tests
    else:
        passed_ratio = 1.0 if execution_result.success else 0.0
    metadata = candidate.metadata
    validation = _dict_metadata(metadata.get("validation"))
    feedback = _dict_metadata(metadata.get("execution_feedback"))
    validation_score = 1.0 if validation.get("valid", False) else 0.0
    feedback_score = float(feedback.get("score", 0.0) or 0.0)
    evidence_score = (
        0.50 * passed_ratio
        + 0.20 * max(0.0, 1.0 - patch_risk)
        + 0.15 * validation_score
        + 0.15 * feedback_score
    )
    reasons = [
        f"passed_ratio={passed_ratio:.2f}",
        f"patch_risk={patch_risk:.2f}",
        f"validation={validation_score:.1f}",
        f"feedback_score={feedback_score:.2f}",
    ]
    if execution_result.success:
        reasons.append("sandbox_success")
    if execution_result.timeout:
        reasons.append("timeout")
    failure_type = str(feedback.get("failure_type", ""))
    if failure_type:
        reasons.append(f"failure_type={failure_type}")
    return round(max(0.0, min(1.0, evidence_score)), 4), reasons


def _calibration_cap(
    candidate: PatchCandidate,
    execution_result: ExecutionResult,
) -> float:
    if execution_result.success:
        return 1.0
    feedback = _dict_metadata(candidate.metadata.get("execution_feedback"))
    failure_type = str(feedback.get("failure_type", ""))
    if execution_result.timeout:
        return 0.35
    if failure_type in {
        "syntax_error",
        "import_error",
        "patch_apply_error",
        "timeout",
    }:
        return 0.40
    total_tests = execution_result.passed + execution_result.failed
    if total_tests and execution_result.passed > 0:
        return 0.75
    return 0.65


def _judge_evidence_agreement(judge_score: float, evidence_score: float) -> str:
    delta = judge_score - evidence_score
    if abs(delta) <= 0.20:
        return "aligned"
    if delta > 0:
        return "judge_more_optimistic"
    return "judge_more_conservative"


def _dict_metadata(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _context_summary(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    return {
        "available": bool(value.get("available", False)),
        "caller_count": len(value.get("callers", []) or []),
        "callee_count": len(value.get("callees", []) or []),
        "module_dependency_count": len(value.get("module_dependencies", []) or []),
        "data_flow_neighbor_count": len(value.get("data_flow_neighbors", []) or []),
        "cross_file_callers": _count_cross_file(value.get("callers", [])),
        "cross_file_dependencies": _count_cross_file(
            value.get("module_dependencies", [])
        ),
    }


def _count_cross_file(items: Any) -> int:
    if not isinstance(items, list):
        return 0
    return sum(
        1
        for item in items
        if isinstance(item, dict) and item.get("is_cross_file", False)
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
        raise ValueError("LLM patch judge response must be a JSON object.")
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
    if normalized in {"prefer", "accept", "reject"}:
        return normalized
    if normalized == "pass":
        return "prefer"
    if normalized == "partial":
        return "accept"
    if normalized == "fail":
        return "reject"
    return "reject"


def _clamp_score(score: float) -> float:
    return max(0.0, min(1.0, round(score, 4)))
