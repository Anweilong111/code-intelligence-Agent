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
        self.last_generation_audit: list[dict] = []
        self.last_reflection_audit: list[dict] = []

    def generate(
        self,
        repo_path: str | Path,
        functions: list[CodeEntity],
        ranked: list[FaultLocalizationResult],
        limit: int = 1,
        repair_context: dict | None = None,
    ) -> list[PatchCandidate]:
        self.last_generation_audit = []
        function_map = {function.id: function for function in functions}
        candidates: list[PatchCandidate] = []
        top_k = max(1, self.top_k_functions)
        ranked_context = _top_k_context(ranked[:top_k], function_map)
        for result in ranked[:top_k]:
            remaining = max(0, limit - len(candidates))
            if remaining <= 0:
                break
            requested_count = min(remaining, 5)
            function = function_map.get(result.function_id)
            if function is None:
                continue
            prompt = build_patch_prompt(
                function,
                result,
                top_k_functions=top_k,
                candidate_count=requested_count,
                repair_context=repair_context,
                top_k_context=ranked_context,
            )
            prompt_payload = _json_prompt_payload(prompt)
            prompt_context_audit = _prompt_context_audit(prompt_payload)
            response = self.client.complete(prompt)
            fixed_sources = parse_fixed_sources(response.text)
            response_parse = _response_parse_audit(response.text, fixed_sources)
            attempt_audit = {
                "target_function_id": function.id,
                "target_function_name": function.metadata.get(
                    "qualified_name", function.name
                ),
                "suspicious_rank": result.rank,
                "requested_candidate_count": requested_count,
                "parsed_candidate_count": len(fixed_sources),
                "prompt_context_audit": prompt_context_audit,
                "response_parse": response_parse,
                "accepted_candidate_count": 0,
                "rejected_candidate_count": 0,
                "rejection_counts": {},
            }
            if not fixed_sources:
                _record_rejection(attempt_audit, response_parse["status"])
            rule_ids = [finding.rule_id for finding in result.findings]
            for llm_candidate_index, fixed_source in enumerate(fixed_sources):
                if len(candidates) >= limit:
                    _record_rejection(attempt_audit, "candidate_limit_reached")
                    break
                if not fixed_source:
                    _record_rejection(attempt_audit, "empty_fixed_source")
                    continue
                if fixed_source == function.source:
                    _record_rejection(attempt_audit, "unchanged_source")
                    continue
                validation = validate_function_patch(
                    function.source,
                    fixed_source,
                    allow_signature_change=allow_signature_change_for_rules(rule_ids),
                )
                if not validation.valid:
                    reasons = validation.reasons or ["invalid_patch"]
                    for reason in reasons:
                        _record_rejection(attempt_audit, str(reason))
                    continue
                relative = _relative_file(repo_path, function.file_path)
                diff = render_unified_diff(function.source, fixed_source, relative)
                candidate_id = f"{function.id}::llm::{len(candidates)}"
                candidates.append(
                    PatchCandidate(
                        id=candidate_id,
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
                            "candidate_id": candidate_id,
                            "llm_candidate_index": llm_candidate_index,
                            "llm_candidate_count_requested": requested_count,
                            "localization_score": result.score,
                            "suspicious_rank": result.rank,
                            "suspicious_top_k": top_k,
                            "constraint": "top_k_suspicious_minimal_diff",
                            "static_rule_ids": rule_ids,
                            "prompt_context_audit": prompt_context_audit,
                            "response_parse": response_parse,
                            "validation": validation.to_dict(),
                            "llm_metadata": response.metadata,
                        },
                    )
                )
                attempt_audit["accepted_candidate_count"] += 1
            self.last_generation_audit.append(attempt_audit)
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
        self.last_reflection_audit = []
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
        prompt_payload = _json_prompt_payload(prompt)
        prompt_context_audit = _reflection_prompt_context_audit(prompt_payload)
        response = self.client.complete(prompt)
        fixed_sources = parse_fixed_sources(response.text)
        response_parse = _response_parse_audit(response.text, fixed_sources)
        attempt_audit = {
            "parent_patch_id": previous_patch.id,
            "target_function_id": previous_patch.target_function_id,
            "target_function_name": previous_patch.target_function_name,
            "round_index": round_index,
            "requested_candidate_count": limit,
            "parsed_candidate_count": len(fixed_sources),
            "prompt_context_audit": prompt_context_audit,
            "response_parse": response_parse,
            "accepted_candidate_count": 0,
            "rejected_candidate_count": 0,
            "rejection_counts": {},
        }
        if not fixed_sources:
            _record_rejection(attempt_audit, "invalid_json_or_schema")
        candidates: list[PatchCandidate] = []
        seen_sources = {previous_patch.old_source, previous_patch.new_source}
        accepted_sources: list[str] = []
        for fixed_source in fixed_sources:
            if not fixed_source or fixed_source in seen_sources:
                _record_rejection(attempt_audit, "duplicate_or_empty_source")
                continue
            seen_sources.add(fixed_source)
            if _source_fingerprint(fixed_source) in failed_patch_memory[
                "avoid_fixed_source_fingerprints"
            ]:
                _record_rejection(attempt_audit, "previous_failed_fingerprint")
                continue
            validation = validate_function_patch(
                previous_patch.old_source,
                fixed_source,
                allow_signature_change=_allows_signature_change(previous_patch),
            )
            if not validation.valid:
                _record_rejection(attempt_audit, "invalid_function_patch")
                continue
            diversity = candidate_diversity(
                old_source=previous_patch.old_source,
                new_source=fixed_source,
                failed_sources=[previous_patch.new_source],
                accepted_sources=accepted_sources,
            )
            if not diversity.accepted:
                _record_rejection(attempt_audit, "low_diversity")
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
                        "reflection_strategy": _reflection_strategy(
                            execution_feedback
                        ),
                        "reflection_prompt_context_audit": prompt_context_audit,
                        "prompt_context_audit": prompt_context_audit,
                        "response_parse": response_parse,
                        "source_fingerprint": diversity.source_fingerprint,
                        "edit_fingerprint": diversity.edit_fingerprint,
                        "candidate_diversity": diversity.to_dict(),
                        "validation": validation.to_dict(),
                        "llm_metadata": response.metadata,
                    },
                )
            )
            attempt_audit["accepted_candidate_count"] += 1
            if len(candidates) >= limit:
                break
        self.last_reflection_audit.append(attempt_audit)
        return candidates


def build_patch_prompt(
    function: CodeEntity,
    result: FaultLocalizationResult,
    top_k_functions: int = 5,
    candidate_count: int = 1,
    repair_context: dict | None = None,
    top_k_context: list[dict] | None = None,
) -> str:
    candidate_count = max(1, candidate_count)
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
    dynamic_oracle = _dict(repair_context)
    payload = {
        "task": (
            "Return a minimal corrected version of fixed_source for this function."
            if candidate_count == 1
            else (
                "Return up to "
                f"{candidate_count} distinct minimal fixed_source alternatives "
                "for this function."
            )
        ),
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
        "candidate_count": candidate_count,
        "top_k_suspicious_functions": top_k_context or [
            {
                "rank": result.rank,
                "function_id": result.function_id,
                "function_name": result.function_name,
                "file_path": result.file_path,
                "score": result.score,
                "reason": result.reason,
            }
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
        "failing_test_nodeids": _failing_test_nodeids(dynamic_oracle),
        "failure_evidence": _failure_evidence(dynamic_oracle),
        "public_api_evidence": _public_api_evidence(dynamic_oracle),
        "call_graph_context": _call_graph_context(dynamic_oracle),
        "previous_failed_patch_fingerprints": _previous_failed_patch_fingerprints(
            dynamic_oracle
        ),
        "required_schema": (
            {"fixed_source": "string"}
            if candidate_count == 1
            else {"fixed_sources": ["string"]}
        ),
    }
    if dynamic_oracle:
        payload["dynamic_oracle"] = dynamic_oracle
    if candidate_count > 1:
        payload["constraints"].append(
            "Return distinct alternatives ordered from most likely to pass tests."
        )
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
    reflection_strategy = _reflection_strategy(execution_feedback)
    judge_feedback = _judge_feedback(previous_patch)
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
        "parent_candidate": {
            "id": previous_patch.id,
            "rule_id": previous_patch.rule_id,
            "variant": str(previous_patch.metadata.get("variant") or ""),
            "generator": str(previous_patch.metadata.get("generator") or ""),
            "target_function_id": previous_patch.target_function_id,
        },
        "previous_patch": {
            "diff": previous_patch.diff,
            "diff_fingerprint": _source_fingerprint(previous_patch.diff),
            "fixed_source_fingerprint": _source_fingerprint(
                previous_patch.new_source
            ),
            "failed_source_fingerprints": failed_patch_memory[
                "avoid_fixed_source_fingerprints"
            ],
        },
        "target_function_source": previous_patch.old_source,
        "reflection_strategy": reflection_strategy,
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
        "failure_evidence": {
            "failure_type": execution_feedback.get("failure_type", ""),
            "failure_stage": execution_feedback.get("failure_stage", ""),
            "pytest_stdout": _truncate(execution_result.stdout),
            "pytest_stderr": _truncate(execution_result.stderr),
            "traceback": _truncate(execution_result.traceback),
            "failed_patch_fingerprint": failed_patch_memory[
                "previous_fixed_source_fingerprint"
            ],
            "previous_diff_fingerprint": failed_patch_memory[
                "previous_diff_fingerprint"
            ],
        },
        "execution_feedback": execution_feedback,
        "failure_analysis": _failure_analysis(execution_feedback),
        "cross_file_context": cross_file_context,
        "related_caller_callee_context": {
            "callers": _list(cross_file_context.get("callers")),
            "callees": _list(cross_file_context.get("callees")),
            "module_dependencies": _list(
                cross_file_context.get("module_dependencies")
            ),
            "data_flow_neighbors": _list(
                cross_file_context.get("data_flow_neighbors")
            ),
        },
        "judge_feedback": judge_feedback,
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


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _json_prompt_payload(prompt: str) -> dict:
    try:
        return _dict(json.loads(prompt))
    except json.JSONDecodeError:
        return {}


def _top_k_context(
    ranked: list[FaultLocalizationResult],
    function_map: dict[str, CodeEntity],
) -> list[dict]:
    rows: list[dict] = []
    for result in ranked:
        function = function_map.get(result.function_id)
        rows.append(
            {
                "rank": result.rank,
                "function_id": result.function_id,
                "function_name": result.function_name,
                "file_path": result.file_path,
                "score": result.score,
                "reason": result.reason,
                "start_line": function.start_line if function else result.start_line,
                "end_line": function.end_line if function else result.end_line,
                "source": function.source if function else "",
            }
        )
    return rows


def _failing_test_nodeids(context: dict) -> list[str]:
    nodeids: list[str] = []
    for value in _dict(context.get("dynamic_evidence_nodeids")).values():
        if str(value or "").strip():
            nodeids.append(str(value).strip())
    for key in ("matched_failing_tests", "unmatched_failing_tests"):
        for item_value in _list(context.get(key)):
            item = _dict(item_value)
            nodeid = str(item.get("nodeid") or "").strip()
            if nodeid:
                nodeids.append(nodeid)
    command = str(context.get("recommended_validation_command") or "").strip()
    if "::" in command:
        nodeids.append(command.split()[-1])
    return list(dict.fromkeys(nodeids))


def _failure_evidence(context: dict) -> dict:
    direct = {
        key: _truncate(str(context.get(key) or ""))
        for key in ("stdout", "stderr", "traceback")
        if str(context.get(key) or "")
    }
    execution = _dict(context.get("execution_result"))
    for key in ("stdout", "stderr", "traceback"):
        if key not in direct and str(execution.get(key) or ""):
            direct[key] = _truncate(str(execution.get(key) or ""))
    for key in ("matched_failing_tests", "unmatched_failing_tests"):
        rows = _list(context.get(key))
        if rows:
            direct[key] = rows[:3]
    return direct


def _public_api_evidence(context: dict) -> dict:
    direct = _dict(context.get("public_api_evidence"))
    if direct:
        return direct
    overlay = _dict(context.get("overlay_case_context"))
    return _dict(overlay.get("public_api_evidence"))


def _call_graph_context(context: dict) -> dict:
    for key in ("call_graph_context", "graph_context", "refinement_context"):
        value = _dict(context.get(key))
        if value:
            return value
    return {}


def _previous_failed_patch_fingerprints(context: dict) -> list[str]:
    for key in (
        "previous_failed_patch_fingerprints",
        "failed_source_fingerprints",
        "avoid_fixed_source_fingerprints",
    ):
        values = [str(item) for item in _list(context.get(key)) if str(item)]
        if values:
            return list(dict.fromkeys(values))
    return []


def _prompt_context_audit(payload: dict) -> dict:
    fields = {
        "top_k_suspicious_functions": bool(
            _list(payload.get("top_k_suspicious_functions"))
        ),
        "target_function_source": bool(
            _dict(payload.get("function")).get("source")
        ),
        "failing_test_nodeid": bool(_list(payload.get("failing_test_nodeids"))),
        "traceback_or_output_summary": bool(_dict(payload.get("failure_evidence"))),
        "public_api_evidence": bool(_dict(payload.get("public_api_evidence"))),
        "dynamic_oracle": bool(_dict(payload.get("dynamic_oracle"))),
        "call_graph_context": bool(_dict(payload.get("call_graph_context"))),
        "previous_failed_patch_fingerprint": bool(
            _list(payload.get("previous_failed_patch_fingerprints"))
        ),
    }
    return {
        "required_fields": fields,
        "present_count": sum(1 for value in fields.values() if value),
        "missing_fields": [
            key for key, value in fields.items() if not bool(value)
        ],
        "candidate_count_requested": _int(payload.get("candidate_count", 1)),
        "required_schema": _dict(payload.get("required_schema")),
    }


def _response_parse_audit(text: str, fixed_sources: list[str]) -> dict:
    schema = "fixed_sources" if '"fixed_sources"' in text else "fixed_source"
    return {
        "status": "pass" if fixed_sources else "invalid_json_or_schema",
        "schema": schema,
        "parsed_candidate_count": len(fixed_sources),
    }


def _record_rejection(attempt_audit: dict, reason: str) -> None:
    attempt_audit["rejected_candidate_count"] = (
        int(attempt_audit.get("rejected_candidate_count", 0)) + 1
    )
    counts = _dict(attempt_audit.get("rejection_counts"))
    counts[reason] = int(counts.get(reason, 0)) + 1
    attempt_audit["rejection_counts"] = dict(sorted(counts.items()))


def _reflection_prompt_context_audit(payload: dict) -> dict:
    parent = _dict(payload.get("parent_candidate"))
    previous_patch = _dict(payload.get("previous_patch"))
    failure = _dict(payload.get("failure_evidence"))
    execution = _dict(payload.get("execution_result"))
    related = _dict(payload.get("related_caller_callee_context"))
    judge = _dict(payload.get("judge_feedback"))
    has_related_context = any(
        _list(related.get(key))
        for key in (
            "callers",
            "callees",
            "module_dependencies",
            "data_flow_neighbors",
        )
    )
    fields = {
        "parent_candidate_id": bool(parent.get("id")),
        "previous_diff": bool(previous_patch.get("diff")),
        "failure_type": bool(failure.get("failure_type")),
        "pytest_stdout_or_stderr_or_traceback": bool(
            execution.get("stdout")
            or execution.get("stderr")
            or execution.get("traceback")
        ),
        "failed_patch_fingerprint": bool(
            failure.get("failed_patch_fingerprint")
        ),
        "target_function_source": bool(payload.get("target_function_source")),
        "reflection_strategy": bool(_dict(payload.get("reflection_strategy")).get("id")),
        "related_caller_callee_context": has_related_context,
    }
    return {
        "status": "pass" if all(fields.values()) else "review",
        "fields": fields,
        "missing_fields": [
            key for key, value in fields.items() if not bool(value)
        ],
        "judge_feedback_available": bool(judge.get("available", False)),
        "candidate_count_requested": _int(payload.get("candidate_count", 1)),
        "required_schema": _dict(payload.get("required_schema")),
    }


def _reflection_strategy(feedback: dict) -> dict:
    failure_type = str(feedback.get("failure_type") or "").strip().lower()
    strategies = {
        "syntax_error": {
            "id": "syntax_repair",
            "action": "Regenerate syntactically valid Python for exactly the target function.",
        },
        "attribute_error": {
            "id": "null_or_type_guard",
            "action": "Use traceback receiver evidence to add a minimal null/type guard or contract fix.",
        },
        "type_error": {
            "id": "null_or_type_guard",
            "action": "Use traceback argument evidence to repair the local type contract.",
        },
        "test_failure": {
            "id": "semantic_repair",
            "action": "Compare the failed assertion against the previous diff and refine localized logic.",
        },
        "timeout": {
            "id": "loop_or_recursion_guard",
            "action": "Repair nontermination risk by narrowing loop or recursion termination logic.",
        },
        "safety_gate_blocked": {
            "id": "smaller_scoped_patch",
            "action": "Regenerate a smaller AST-valid patch scoped to the original function.",
        },
        "patch_apply_error": {
            "id": "applicable_diff_repair",
            "action": "Realign the returned fixed source with the current target function source.",
        },
    }
    return {
        "failure_type": failure_type or "unknown_failure",
        **strategies.get(
            failure_type,
            {
                "id": "execution_feedback_repair",
                "action": "Use stdout, stderr, traceback, and execution feedback to choose a minimal repair.",
            },
        ),
    }


def _judge_feedback(previous_patch: PatchCandidate) -> dict:
    judgment = _dict(previous_patch.metadata.get("patch_judgment"))
    if not judgment:
        return {"available": False}
    return {
        "available": True,
        "score": judgment.get("score"),
        "verdict": str(judgment.get("verdict") or ""),
        "reason": str(judgment.get("reason") or ""),
        "risk": str(judgment.get("risk") or ""),
        "confidence": judgment.get("confidence"),
        "agreement": str(judgment.get("agreement") or ""),
    }


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
