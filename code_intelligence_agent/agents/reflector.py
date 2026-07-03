from __future__ import annotations

from dataclasses import dataclass

from code_intelligence_agent.core.models import ExecutionResult, PatchCandidate


@dataclass(frozen=True)
class ReflectionDecision:
    should_retry: bool
    error_type: str
    reason: str
    patch: PatchCandidate | None = None


class ReflectionAgent:
    def classify_error(self, result: ExecutionResult) -> str:
        combined = f"{result.stdout}\n{result.stderr}\n{result.traceback}"
        if result.timeout:
            return "TimeoutError"
        for error_type in [
            "SyntaxError",
            "ImportError",
            "AttributeError",
            "AssertionError",
            "TypeError",
        ]:
            if error_type in combined:
                return error_type
        if result.failed > 0:
            return "TestFailure"
        return "Unknown"

    def reflect(
        self,
        patch: PatchCandidate | None,
        result: ExecutionResult,
        round_index: int,
        max_rounds: int = 3,
    ) -> ReflectionDecision:
        error_type = self.classify_error(result)
        should_retry = (not result.success) and round_index < max_rounds
        return ReflectionDecision(
            should_retry=should_retry,
            error_type=error_type,
            reason=_reason(error_type, should_retry),
            patch=patch if should_retry else None,
        )


def _reason(error_type: str, should_retry: bool) -> str:
    if not should_retry:
        return "No retry selected."
    if error_type == "SyntaxError":
        return "Regenerate patch with stricter syntax constraints."
    if error_type == "AssertionError":
        return "Compare expected and actual values, then refine logic patch."
    if error_type == "TimeoutError":
        return "Inspect loop bounds and recursion termination."
    return "Retry patch generation with execution feedback."
