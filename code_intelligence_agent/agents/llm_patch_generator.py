from __future__ import annotations

import json
import re
from pathlib import Path

from code_intelligence_agent.agents.llm_client import LLMClient
from code_intelligence_agent.agents.patch_generator import _relative_file
from code_intelligence_agent.core.models import (
    CodeEntity,
    ExecutionResult,
    FaultLocalizationResult,
    PatchCandidate,
)
from code_intelligence_agent.tools.diff_utils import render_unified_diff
from code_intelligence_agent.tools.patch_validation import (
    allow_signature_change_for_rules,
    validate_function_patch,
)
from code_intelligence_agent.search.candidate_diversity import (
    candidate_diversity,
    stable_source_fingerprint,
)
from code_intelligence_agent.search.execution_feedback import analyze_execution_feedback


class LLMPatchGenerator:
    def __init__(self, client: LLMClient, top_k_functions: int = 5) -> None:
        self.client = client
        self.top_k_functions = top_k_functions

    def generate(
        self,
        repo_path: str | Path,
        functions: list[CodeEntity],
        ranked: list[FaultLocalizationResult],
        limit: int = 1,
        repair_context: dict | None = None,
    ) -> list[PatchCandidate]:
        function_map = {function.id: function for function in functions}
        candidates: list[PatchCandidate] = []
        top_k = max(1, self.top_k_functions)
        for result in ranked[:top_k]:
            function = function_map.get(result.function_id)
            if function is None:
                continue
            prompt = build_patch_prompt(
                function,
                result,
                top_k_functions=top_k,
                repair_context=repair_context,
            )
            response = self.client.complete(prompt)
            fixed_source = parse_fixed_source(response.text)
            if not fixed_source or fixed_source == function.source:
                continue
            rule_ids = [finding.rule_id for finding in result.findings]
            validation = validate_function_patch(
                function.source,
                fixed_source,
                allow_signature_change=allow_signature_change_for_rules(rule_ids),
            )
            if not validation.valid:
                continue
            relative = _relative_file(repo_path, function.file_path)
            diff = render_unified_diff(function.source, fixed_source, relative)
            candidates.append(
                PatchCandidate(
                    id=f"{function.id}::llm::{len(candidates)}",
                    target_file=function.file_path,
                    relative_file_path=relative,
                    target_function_id=function.id,
                    target_function_name=function.metadata.get(
                        "qualified_name", function.name
                    ),
                    rule_id="llm_patch",
                    description="LLM-generated patch candidate.",
                    old_source=function.source,
                    new_source=fixed_source,
                    diff=diff,
                    metadata={
                        "generator": "llm",
                        "localization_score": result.score,
                        "suspicious_rank": result.rank,
                        "suspicious_top_k": top_k,
                        "constraint": "top_k_suspicious_minimal_diff",
                        "static_rule_ids": rule_ids,
                        "validation": validation.to_dict(),
                        "llm_metadata": response.metadata,
                    },
                )
            )
            if len(candidates) >= limit:
                break
        return candidates

    def refine(
        self,
        repo_path: str | Path,
        previous_patch: PatchCandidate,
        execution_result: ExecutionResult,
        round_index: int,
    ) -> PatchCandidate | None:
        candidates = self.refine_many(
            repo_path=repo_path,
            previous_patch=previous_patch,
            execution_result=execution_result,
            round_index=round_index,
            limit=1,
        )
        return candidates[0] if candidates else None

    def refine_many(
        self,
        repo_path: str | Path,
        previous_patch: PatchCandidate,
        execution_result: ExecutionResult,
        round_index: int,
        limit: int = 1,
    ) -> list[PatchCandidate]:
        limit = max(1, limit)
        execution_feedback = _execution_feedback(previous_patch, execution_result)
        failed_patch_memory = _failed_patch_memory(previous_patch, execution_feedback)
        cross_file_context = _cross_file_context(previous_patch)
        prompt = build_reflection_prompt(
            previous_patch,
            execution_result,
            round_index,
            candidate_count=limit,
            execution_feedback=execution_feedback,
            failed_patch_memory=failed_patch_memory,
            cross_file_context=cross_file_context,
        )
        response = self.client.complete(prompt)
        candidates: list[PatchCandidate] = []
        seen_sources = {previous_patch.old_source, previous_patch.new_source}
        accepted_sources: list[str] = []
        for fixed_source in parse_fixed_sources(response.text):
            if not fixed_source or fixed_source in seen_sources:
                continue
            seen_sources.add(fixed_source)
            if _source_fingerprint(fixed_source) in failed_patch_memory[
                "avoid_fixed_source_fingerprints"
            ]:
                continue
            validation = validate_function_patch(
                previous_patch.old_source,
                fixed_source,
                allow_signature_change=_allows_signature_change(previous_patch),
            )
            if not validation.valid:
                continue
            diversity = candidate_diversity(
                old_source=previous_patch.old_source,
                new_source=fixed_source,
                failed_sources=[previous_patch.new_source],
                accepted_sources=accepted_sources,
            )
            if not diversity.accepted:
                continue
            accepted_sources.append(fixed_source)
            relative = _relative_file(repo_path, previous_patch.target_file)
            diff = render_unified_diff(
                previous_patch.old_source,
                fixed_source,
                relative,
            )
            child_index = len(candidates)
            candidate_id = f"{previous_patch.target_function_id}::llm_refine::{round_index}"
            if limit > 1:
                candidate_id = f"{candidate_id}::{child_index}"
            candidates.append(
                PatchCandidate(
                    id=candidate_id,
                    target_file=previous_patch.target_file,
                    relative_file_path=relative,
                    target_function_id=previous_patch.target_function_id,
                    target_function_name=previous_patch.target_function_name,
                    rule_id="llm_reflection_patch",
                    description="LLM-refined patch candidate from execution feedback.",
                    old_source=previous_patch.old_source,
                    new_source=fixed_source,
                    diff=diff,
                    metadata={
                        "generator": "llm_reflection",
                        "parent_patch_id": previous_patch.id,
                        "round_index": round_index,
                        "reflection_child_index": child_index,
                        "reflection_candidate_count_requested": limit,
                        "parent_execution_feedback": execution_feedback,
                        "failure_analysis": _failure_analysis(execution_feedback),
                        "refinement_context": cross_file_context,
                        "failed_source_fingerprints": failed_patch_memory[
                            "avoid_fixed_source_fingerprints"
                        ],
                        "source_fingerprint": diversity.source_fingerprint,
                        "edit_fingerprint": diversity.edit_fingerprint,
                        "candidate_diversity": diversity.to_dict(),
                        "validation": validation.to_dict(),
                        "llm_metadata": response.metadata,
                    },
                )
            )
            if len(candidates) >= limit:
                break
        return candidates


def build_patch_prompt(
    function: CodeEntity,
    result: FaultLocalizationResult,
    top_k_functions: int = 5,
    repair_context: dict | None = None,
) -> str:
    findings = [
        {
            "rule_id": finding.rule_id,
            "bug_type": finding.bug_type,
            "message": finding.message,
            "line": finding.line,
            "confidence": finding.confidence,
        }
        for finding in result.findings
    ]
    payload = {
        "task": "Return a minimal corrected version of fixed_source for this function.",
        "constraints": [
            "Return only JSON.",
            "Do not include markdown.",
            "Keep the same function name and public signature unless required.",
            "Do not remove tests.",
            "Prefer the smallest behavior-preserving patch.",
            (
                "When dynamic_oracle is present, treat it as the repair oracle. "
                "Fix the behavior that makes the dynamic test fail; do not "
                "preserve old exception behavior just because it appears in "
                "comments or doctests."
            ),
            (
                "If dynamic_oracle.overlay_case_context.expected_exception is "
                "set, that exception names the legacy failure to avoid, not the "
                "desired new exception."
            ),
            (
                "When dynamic_oracle.public_api_evidence.public_call_args or "
                "trigger_expression are present, the patch must be safe for "
                "those exact runtime arguments; do not introduce AttributeError "
                "or TypeError while avoiding the legacy failure."
            ),
            (
                "For boundary-guard findings, place the guard before operations "
                "on values that the dynamic oracle passes as None, empty lists, "
                "empty strings, or other boundary inputs."
            ),
            (
                "Only patch this function because it is within the top-"
                f"{top_k_functions} suspicious functions."
            ),
        ],
        "function": {
            "id": function.id,
            "name": function.metadata.get("qualified_name", function.name),
            "file_path": function.file_path,
            "start_line": function.start_line,
            "end_line": function.end_line,
            "source": function.source,
        },
        "localization": {
            "score": result.score,
            "rank": result.rank,
            "signals": result.signals,
            "reason": result.reason,
            "findings": findings,
        },
        "required_schema": {"fixed_source": "string"},
    }
    dynamic_oracle = _dict(repair_context)
    if dynamic_oracle:
        payload["dynamic_oracle"] = dynamic_oracle
    return json.dumps(payload, indent=2)


def build_reflection_prompt(
    previous_patch: PatchCandidate,
    execution_result: ExecutionResult,
    round_index: int,
    candidate_count: int = 1,
    execution_feedback: dict | None = None,
    failed_patch_memory: dict | None = None,
    cross_file_context: dict | None = None,
) -> str:
    candidate_count = max(1, candidate_count)
    execution_feedback = execution_feedback or _execution_feedback(
        previous_patch,
        execution_result,
    )
    failed_patch_memory = failed_patch_memory or _failed_patch_memory(
        previous_patch,
        execution_feedback,
    )
    cross_file_context = cross_file_context or _cross_file_context(previous_patch)
    payload = {
        "task": (
            (
                "The previous patch failed in sandbox execution. Return a refined "
                "minimal fixed_source for the same function."
            )
            if candidate_count == 1
            else (
                "The previous patch failed in sandbox execution. Return up to "
                f"{candidate_count} distinct minimal fixed_source alternatives "
                "for the same function."
            )
        ),
        "constraints": [
            "Return only JSON.",
            "Do not include markdown.",
            "Do not remove tests.",
            "Keep the same function name and public signature unless required.",
            "Use stdout, stderr, and traceback as feedback.",
            "Use structured execution_feedback to prioritize likely fixable failures.",
            "Use failure_analysis.refinement_hints before making broad rewrites.",
            (
                "Use cross_file_context callers, callees, module dependencies, and "
                "data-flow neighbors to preserve the target function's contract."
            ),
            "Return only the corrected source for the target function, not related files.",
            (
                "Do not return a fixed_source whose normalized fingerprint appears "
                "in failed_patch_memory.avoid_fixed_source_fingerprints."
            ),
        ],
        "round_index": round_index,
        "candidate_count": candidate_count,
        "function": {
            "id": previous_patch.target_function_id,
            "name": previous_patch.target_function_name,
            "file_path": previous_patch.target_file,
            "original_source": previous_patch.old_source,
            "previous_fixed_source": previous_patch.new_source,
            "previous_diff": previous_patch.diff,
        },
        "execution_result": {
            "success": execution_result.success,
            "returncode": execution_result.returncode,
            "passed": execution_result.passed,
            "failed": execution_result.failed,
            "timeout": execution_result.timeout,
            "stdout": _truncate(execution_result.stdout),
            "stderr": _truncate(execution_result.stderr),
            "traceback": _truncate(execution_result.traceback),
        },
        "execution_feedback": execution_feedback,
        "failure_analysis": _failure_analysis(execution_feedback),
        "cross_file_context": cross_file_context,
        "failed_patch_memory": failed_patch_memory,
        "diversity_requirements": _diversity_requirements(
            failed_patch_memory,
            candidate_count,
        ),
        "required_schema": (
            {"fixed_source": "string"}
            if candidate_count == 1
            else {"fixed_sources": ["string"]}
        ),
    }
    if candidate_count > 1:
        payload["constraints"].append(
            "Return distinct alternatives ordered from most likely to pass tests."
        )
    return json.dumps(payload, indent=2)


def parse_fixed_source(text: str) -> str | None:
    sources = parse_fixed_sources(text)
    return sources[0] if sources else None


def parse_fixed_sources(text: str) -> list[str]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = _strip_code_fence(candidate)
    try:
        data = json.loads(candidate)
        fixed_sources = data.get("fixed_sources")
        if isinstance(fixed_sources, list):
            return [item for item in fixed_sources if isinstance(item, str)]
        fixed = data.get("fixed_source")
        return [fixed] if isinstance(fixed, str) else []
    except json.JSONDecodeError:
        pass

    match = re.search(
        r'"fixed_source"\s*:\s*"(?P<source>(?:\\.|[^"\\])*)"',
        candidate,
        flags=re.DOTALL,
    )
    if match:
        return [json.loads(f'"{match.group("source")}"')]
    return []


def _strip_code_fence(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _execution_feedback(
    previous_patch: PatchCandidate,
    execution_result: ExecutionResult | None = None,
) -> dict:
    feedback = previous_patch.metadata.get("execution_feedback")
    if isinstance(feedback, dict):
        if execution_result is not None and (
            "failure_stage" not in feedback or "refinement_hints" not in feedback
        ):
            enriched = analyze_execution_feedback(
                previous_patch,
                execution_result,
            ).to_dict()
            return {**enriched, **feedback}
        return feedback
    if execution_result is not None:
        return analyze_execution_feedback(previous_patch, execution_result).to_dict()
    return {}


def _failure_analysis(feedback: dict) -> dict:
    if not feedback:
        return {}
    return {
        "failure_type": feedback.get("failure_type", "unknown_failure"),
        "failure_stage": feedback.get("failure_stage", "unknown"),
        "recoverability": feedback.get("recoverability", "unknown"),
        "prompt_summary": feedback.get("prompt_summary", ""),
        "refinement_hints": feedback.get("refinement_hints", []),
    }


def _cross_file_context(previous_patch: PatchCandidate) -> dict:
    context = previous_patch.metadata.get("refinement_context")
    return context if isinstance(context, dict) else {}


def _failed_patch_memory(
    previous_patch: PatchCandidate,
    execution_feedback: dict,
) -> dict:
    previous_fixed_source_fingerprint = _source_fingerprint(
        previous_patch.new_source,
    )
    avoid_fingerprints = _failed_source_fingerprints(
        previous_patch,
        previous_fixed_source_fingerprint,
    )
    return {
        "previous_patch_id": previous_patch.id,
        "previous_fixed_source_fingerprint": previous_fixed_source_fingerprint,
        "previous_diff_fingerprint": _source_fingerprint(previous_patch.diff),
        "avoid_fixed_source_fingerprints": avoid_fingerprints,
        "failure_type": execution_feedback.get("failure_type", "unknown_failure"),
        "failure_stage": execution_feedback.get("failure_stage", "unknown"),
    }


def _diversity_requirements(
    failed_patch_memory: dict,
    candidate_count: int,
) -> dict:
    return {
        "enabled": candidate_count > 1,
        "avoid_fixed_source_fingerprints": failed_patch_memory[
            "avoid_fixed_source_fingerprints"
        ],
        "prefer_distinct_fix_strategies": candidate_count > 1,
        "dimensions": [
            "boundary condition",
            "guard placement",
            "loop bound",
            "return semantics",
            "exception behavior",
            "state update order",
        ],
        "selection_rule": (
            "When returning fixed_sources, avoid near-duplicate edits and order "
            "alternatives by likelihood to pass tests."
        ),
    }


def _failed_source_fingerprints(
    previous_patch: PatchCandidate,
    current_fingerprint: str,
) -> list[str]:
    fingerprints: list[str] = []
    inherited = previous_patch.metadata.get("failed_source_fingerprints")
    if isinstance(inherited, list):
        fingerprints.extend(str(item) for item in inherited if item)
    fingerprints.append(current_fingerprint)
    return list(dict.fromkeys(fingerprints))


def _source_fingerprint(source: str) -> str:
    return stable_source_fingerprint(source)


def _allows_signature_change(previous_patch: PatchCandidate) -> bool:
    validation = previous_patch.metadata.get("validation")
    if isinstance(validation, dict) and validation.get("signature_change_allowed"):
        return True
    rule_ids = [previous_patch.rule_id]
    static_rule_ids = previous_patch.metadata.get("static_rule_ids")
    if isinstance(static_rule_ids, list):
        rule_ids.extend(str(rule_id) for rule_id in static_rule_ids)
    return allow_signature_change_for_rules(rule_ids)
