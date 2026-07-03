from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from code_intelligence_agent.core.models import ExecutionResult, PatchCandidate
from code_intelligence_agent.search.failure_taxonomy import (
    classify_execution_result,
    summarize_failure_reason,
)


@dataclass(frozen=True)
class ExecutionFeedback:
    failure_type: str
    failure_stage: str
    recoverability: str
    reason: str
    score: float
    passed_ratio: float
    target_traceback_hit: bool
    reasons: list[str]
    refinement_hints: list[str]
    prompt_summary: str

    def to_dict(self) -> dict:
        return asdict(self)


def analyze_execution_feedback(
    candidate: PatchCandidate,
    result: ExecutionResult,
) -> ExecutionFeedback:
    failure_type = classify_execution_result(result)
    passed_ratio = _passed_ratio(result)
    target_hit = _target_traceback_hit(candidate, result)
    score = _base_score(failure_type, passed_ratio)
    failure_stage = _failure_stage(failure_type)
    recoverability = _recoverability(failure_type, passed_ratio, target_hit)
    refinement_hints = _refinement_hints(failure_type, passed_ratio, target_hit)
    reasons = [failure_type]
    if passed_ratio:
        reasons.append(f"passed_ratio={passed_ratio:.2f}")
    if target_hit:
        score += 0.05
        reasons.append("target_traceback_hit")
    score = _clamp(score)
    return ExecutionFeedback(
        failure_type=failure_type,
        failure_stage=failure_stage,
        recoverability=recoverability,
        reason=summarize_failure_reason(result),
        score=round(score, 4),
        passed_ratio=round(passed_ratio, 4),
        target_traceback_hit=target_hit,
        reasons=reasons,
        refinement_hints=refinement_hints,
        prompt_summary=_prompt_summary(
            failure_type=failure_type,
            failure_stage=failure_stage,
            recoverability=recoverability,
            passed_ratio=passed_ratio,
            target_hit=target_hit,
        ),
    )


def annotate_execution_feedback(
    candidate: PatchCandidate,
    result: ExecutionResult,
) -> PatchCandidate:
    feedback = analyze_execution_feedback(candidate, result)
    return replace(
        candidate,
        metadata={
            **candidate.metadata,
            "execution_feedback": feedback.to_dict(),
        },
    )


def execution_feedback_score(candidate: PatchCandidate) -> float:
    feedback = candidate.metadata.get("execution_feedback", {})
    if isinstance(feedback, dict):
        return float(feedback.get("score", 0.0))
    return 0.0


def _base_score(failure_type: str, passed_ratio: float) -> float:
    if failure_type == "success":
        return 1.0
    if failure_type == "test_failure":
        return 0.55 + 0.25 * passed_ratio
    if failure_type in {"type_error", "attribute_error", "runtime_error"}:
        return 0.35 + 0.15 * passed_ratio
    if failure_type in {"patch_apply_error", "syntax_error", "import_error"}:
        return 0.10
    if failure_type == "timeout":
        return 0.05
    if failure_type == "execution_error":
        return 0.12
    return 0.20


def _failure_stage(failure_type: str) -> str:
    if failure_type == "success":
        return "success"
    if failure_type in {"patch_apply_error"}:
        return "patch_application"
    if failure_type in {"syntax_error", "import_error"}:
        return "static_validation"
    if failure_type == "timeout":
        return "performance"
    if failure_type in {"type_error", "attribute_error", "runtime_error"}:
        return "runtime_execution"
    if failure_type == "test_failure":
        return "test_assertion"
    if failure_type == "execution_error":
        return "sandbox_execution"
    return "unknown"


def _recoverability(
    failure_type: str,
    passed_ratio: float,
    target_hit: bool,
) -> str:
    if failure_type == "success":
        return "complete"
    if failure_type == "test_failure":
        return "high" if passed_ratio or target_hit else "medium"
    if failure_type in {"type_error", "attribute_error", "runtime_error"}:
        return "medium" if target_hit or passed_ratio else "low"
    if failure_type in {"syntax_error", "import_error", "patch_apply_error", "timeout"}:
        return "low"
    if failure_type == "execution_error":
        return "low"
    return "unknown"


def _refinement_hints(
    failure_type: str,
    passed_ratio: float,
    target_hit: bool,
) -> list[str]:
    if failure_type == "success":
        return ["Keep the current patch unchanged."]
    if failure_type == "test_failure":
        hints = [
            "Compare failed assertions against the previous diff before changing logic.",
            "Prefer a minimal semantic correction over broad rewrites.",
        ]
        if passed_ratio:
            hints.append("Preserve behavior covered by tests that already passed.")
        if target_hit:
            hints.append("Prioritize the target function because it appears in feedback.")
        return hints
    if failure_type in {"type_error", "attribute_error", "runtime_error"}:
        hints = [
            "Use traceback context to identify the invalid value or API usage.",
            "Keep the patch scoped to the original function unless signature change is allowed.",
        ]
        if target_hit:
            hints.append("The target function appears in traceback, so refine its local logic first.")
        return hints
    if failure_type == "syntax_error":
        return [
            "Return syntactically valid Python for exactly the original function.",
            "Check indentation, parentheses, strings, and decorators before changing behavior.",
        ]
    if failure_type == "import_error":
        return [
            "Avoid introducing new imports unless they already exist in the project context.",
            "Prefer using symbols available inside the original function scope.",
        ]
    if failure_type == "patch_apply_error":
        return [
            "Keep the original function name and surrounding structure intact.",
            "Return only the corrected function body expected by fixed_source.",
        ]
    if failure_type == "timeout":
        return [
            "Inspect loop bounds, recursion termination, and algorithmic complexity.",
            "Prefer a local guard or corrected termination condition.",
        ]
    if failure_type == "execution_error":
        return [
            "Use stderr and traceback to identify sandbox execution failure before changing logic.",
        ]
    return ["Use stdout, stderr, and traceback to refine the smallest failing behavior."]


def _prompt_summary(
    *,
    failure_type: str,
    failure_stage: str,
    recoverability: str,
    passed_ratio: float,
    target_hit: bool,
) -> str:
    parts = [
        f"failure_type={failure_type}",
        f"stage={failure_stage}",
        f"recoverability={recoverability}",
        f"passed_ratio={passed_ratio:.2f}",
    ]
    if target_hit:
        parts.append("target_traceback_hit=true")
    return "; ".join(parts)


def _passed_ratio(result: ExecutionResult) -> float:
    total = result.passed + result.failed
    if total:
        return result.passed / total
    return 1.0 if result.success else 0.0


def _target_traceback_hit(
    candidate: PatchCandidate,
    result: ExecutionResult,
) -> bool:
    text = "\n".join(
        part
        for part in [result.traceback, result.stderr, result.stdout]
        if part
    ).lower()
    if not text:
        return False
    names = {
        candidate.target_function_name,
        candidate.target_function_name.split(".")[-1],
    }
    return any(name.lower() in text for name in names if name)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
