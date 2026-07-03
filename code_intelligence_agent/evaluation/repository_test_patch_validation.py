from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from code_intelligence_agent.agents.llm_client import (
    create_patch_client,
    llm_config_audit,
)
from code_intelligence_agent.agents.llm_patch_generator import LLMPatchGenerator
from code_intelligence_agent.agents.patch_generator import PatchGenerator
from code_intelligence_agent.core.models import ExecutionResult, PatchCandidate
from code_intelligence_agent.search.beam_patch_search import (
    BeamPatchNode,
    BeamPatchSearch,
)
from code_intelligence_agent.search.failure_taxonomy import (
    classify_execution_result,
    summarize_failure_reason,
)
from code_intelligence_agent.search.execution_feedback import (
    annotate_execution_feedback,
    execution_feedback_score,
)
from code_intelligence_agent.search.patch_search import PatchSearch
from code_intelligence_agent.search.patch_judge import PatchJudge, build_patch_judge
from code_intelligence_agent.tools.sandbox import Sandbox
from code_intelligence_agent.evaluation.repository_test_reflection_trace import (
    build_repository_test_reflection_trace,
    write_repository_test_reflection_trace_artifacts,
)


def build_repository_test_patch_validation(
    patch_candidates: dict[str, Any] | None,
    *,
    repository_root: str | Path | None,
    validation_limit: int = 5,
    timeout: int = 10,
    reflection_mode: str = "rule",
    reflection_rounds: int = 1,
    reflection_width: int = 1,
    refiner: Any | None = None,
    patch_search: PatchSearch | None = None,
    patch_judge_mode: str = "none",
    patch_judge: PatchJudge | None = None,
    patch_judge_weight: float = 0.08,
    regression_pytest_args: list[str] | None = None,
    regression_validation_command: str = "",
) -> dict[str, Any]:
    payload = _dict(patch_candidates)
    if not payload:
        return _skipped(
            reason="patch_candidates_missing",
            message="Repository test patch candidates are not available.",
            reflection_mode=reflection_mode,
        )
    if str(payload.get("status") or "") != "pass":
        return _skipped(
            reason="patch_candidates_not_ready",
            message="Patch validation requires generated patch candidates.",
            patch_candidates=payload,
            reflection_mode=reflection_mode,
        )
    if repository_root is None:
        return _skipped(
            reason="repository_root_missing",
            message="Patch validation requires a local repository checkout.",
            patch_candidates=payload,
            reflection_mode=reflection_mode,
        )
    root = Path(repository_root)
    if not root.exists() or not root.is_dir():
        return _skipped(
            reason="repository_root_missing",
            message="Repository test root does not exist or is not a directory.",
            patch_candidates=payload,
            repository_root=str(root),
            reflection_mode=reflection_mode,
        )

    test_args = [str(arg) for arg in _list(payload.get("recommended_pytest_args"))]
    if not test_args:
        return _skipped(
            reason="validation_args_missing",
            message=(
                "Patch validation requires narrow pytest args from failing "
                "repository tests."
            ),
            patch_candidates=payload,
            repository_root=str(root),
            reflection_mode=reflection_mode,
        )

    input_candidates = [
        candidate
        for candidate in (
            _patch_candidate_from_dict(row)
            for row in _list(payload.get("candidates"))
        )
        if candidate is not None
    ]
    blocked_candidates = [
        candidate for candidate in input_candidates if _candidate_blocked_by_safety(candidate)
    ]
    candidates = [
        candidate
        for candidate in input_candidates
        if not _candidate_blocked_by_safety(candidate)
    ]
    if not candidates:
        return _skipped(
            reason=(
                "all_candidates_blocked_by_safety_gate"
                if blocked_candidates
                else "candidate_payload_empty"
            ),
            message=(
                "Patch candidate safety gate blocked every candidate before sandbox validation."
                if blocked_candidates
                else "Patch candidate payload did not contain valid candidates."
            ),
            patch_candidates=payload,
            repository_root=str(root),
            test_args=test_args,
            reflection_mode=reflection_mode,
            input_candidate_count=len(input_candidates),
            safety_blocked_candidates=blocked_candidates,
        )

    limit = max(0, min(validation_limit, len(candidates)))
    if limit <= 0:
        return _skipped(
            reason="validation_limit_zero",
            message="Patch validation limit is zero.",
            patch_candidates=payload,
            repository_root=str(root),
            test_args=test_args,
            reflection_mode=reflection_mode,
        )

    localization_scores = _localization_scores(payload)
    search_output = _run_patch_validation_search(
        root,
        candidates,
        localization_scores=localization_scores,
        test_args=test_args,
        timeout=timeout,
        limit=limit,
        reflection_mode=reflection_mode,
        reflection_rounds=reflection_rounds,
        reflection_width=reflection_width,
        refiner=refiner,
        patch_search=patch_search,
        patch_judge_mode=patch_judge_mode,
        patch_judge=patch_judge,
        patch_judge_weight=patch_judge_weight,
    )
    search_metadata = _dict(search_output.get("metadata"))
    raw_results = _list(search_output.get("results"))
    result_rows = [_validation_result_to_dict(result) for result in raw_results]
    best_index = _best_result_index(result_rows)
    best_result = (
        raw_results[best_index] if 0 <= best_index < len(raw_results) else None
    )
    best = (
        result_rows[best_index]
        if 0 <= best_index < len(result_rows)
        else (result_rows[0] if result_rows else {})
    )
    initial_regression_validation = _validate_best_patch_regression(
        root,
        best_result,
        regression_pytest_args=regression_pytest_args,
        narrow_pytest_args=test_args,
        timeout=timeout,
        validation_command=regression_validation_command,
    )
    regression_reflection_output = _run_regression_reflection(
        root,
        best_result,
        initial_regression_validation,
        regression_pytest_args=regression_pytest_args,
        narrow_pytest_args=test_args,
        timeout=timeout,
        reflection_mode=reflection_mode,
        reflection_rounds=reflection_rounds,
        reflection_width=reflection_width,
        refiner=refiner,
    )
    regression_reflection_results = _list(
        regression_reflection_output.get("results")
    )
    if regression_reflection_results:
        raw_results = [*raw_results, *regression_reflection_results]
        result_rows = [
            _validation_result_to_dict(result) for result in raw_results
        ]
    success_rows = [row for row in result_rows if bool(row.get("success", False))]
    depth0_success_rows = [
        row for row in success_rows if _int(row.get("depth", 0)) == 0
    ]
    reflection_rows = [
        row for row in result_rows if _int(row.get("depth", 0)) > 0
    ]
    reflection_success_rows = [
        row for row in reflection_rows if bool(row.get("success", False))
    ]
    best = success_rows[0] if success_rows else (result_rows[0] if result_rows else {})
    failure_counts = Counter(
        str(row.get("failure_type") or "unknown") for row in result_rows
    )
    patch_judge_rows = [
        row for row in result_rows if _dict(row.get("patch_judgment"))
    ]
    patch_judge_verdict_counts = Counter(
        str(_dict(row.get("patch_judgment")).get("verdict") or "unknown")
        for row in patch_judge_rows
    )
    patch_judge_agreement_counts = Counter(
        str(_dict(row.get("patch_judgment")).get("agreement") or "unknown")
        for row in patch_judge_rows
    )
    best_index = _best_result_index(
        result_rows,
        prefer_deep_success=any(
            bool(getattr(result, "success", False))
            for result in regression_reflection_results
        ),
    )
    best_result = (
        raw_results[best_index] if 0 <= best_index < len(raw_results) else None
    )
    best = (
        result_rows[best_index]
        if 0 <= best_index < len(result_rows)
        else (result_rows[0] if result_rows else {})
    )
    best_success = bool(best.get("success", False))
    regression_validation = _validate_best_patch_regression(
        root,
        best_result,
        regression_pytest_args=regression_pytest_args,
        narrow_pytest_args=test_args,
        timeout=timeout,
        validation_command=regression_validation_command,
    )
    regression_status = str(regression_validation.get("status") or "")
    regression_failed = regression_status == "fail"
    status = "pass" if success_rows else "fail"
    reason = _validation_reason(
        success_count=len(success_rows),
        depth0_success_count=len(depth0_success_rows),
        reflection_success_count=len(reflection_success_rows),
    )
    return {
        "status": status,
        "reason": reason,
        "message": (
            "Repository test patch candidates were executed in sandboxed "
            "temporary checkouts."
        ),
        "repository_root": str(root),
        "input_candidate_count": len(input_candidates),
        "candidate_count": len(candidates),
        "safety_gate": _dict(payload.get("safety_gate")),
        "safety_blocked_candidate_count": len(blocked_candidates),
        "safety_blocked_candidates": [
            _safety_blocked_candidate_summary(candidate)
            for candidate in blocked_candidates
        ],
        "validation_limit": limit,
        "executed_count": len(result_rows),
        "depth0_executed_count": len(result_rows) - len(reflection_rows),
        "success_count": len(success_rows),
        "failed_count": len(result_rows) - len(success_rows),
        "repair_ready": bool(best_success and not regression_failed),
        "repair_validation_scope": _repair_validation_scope(
            best_success=best_success,
            regression_status=regression_status,
        ),
        "regression_ready": regression_status == "pass",
        "regression_validation": regression_validation,
        "reflection_enabled": bool(search_metadata.get("reflection_enabled", False)),
        "reflection_mode": str(search_metadata.get("reflection_mode") or ""),
        "reflection_refiner_status": str(
            search_metadata.get("reflection_refiner_status") or ""
        ),
        "reflection_refiner_reason": str(
            search_metadata.get("reflection_refiner_reason") or ""
        ),
        "patch_judge_enabled": bool(
            search_metadata.get("patch_judge_enabled", False)
        ),
        "patch_judge_mode": str(search_metadata.get("patch_judge_mode") or "none"),
        "patch_judge_status": str(
            search_metadata.get("patch_judge_status") or "disabled"
        ),
        "patch_judge_reason": str(
            search_metadata.get("patch_judge_reason") or "patch_judge_mode_none"
        ),
        "patch_judge_weight": _float(search_metadata.get("patch_judge_weight", 0.0)),
        "patch_judge_config_audit": _dict(
            search_metadata.get("patch_judge_config_audit")
        ),
        "patch_judge_candidate_count": len(patch_judge_rows),
        "patch_judge_verdict_counts": dict(sorted(patch_judge_verdict_counts.items())),
        "patch_judge_agreement_counts": dict(
            sorted(patch_judge_agreement_counts.items())
        ),
        "patch_judge_authority": "sandbox_pytest_decides_success",
        "reflection_rounds": max(0, reflection_rounds),
        "reflection_width": max(1, reflection_width),
        "reflection_candidate_count": len(reflection_rows),
        "successful_reflection_candidate_count": len(reflection_success_rows),
        "regression_reflection_candidate_count": len(
            regression_reflection_results
        ),
        "successful_regression_reflection_candidate_count": sum(
            1
            for result in regression_reflection_results
            if bool(getattr(result, "success", False))
        ),
        "max_depth_executed": max(
            [_int(row.get("depth", 0)) for row in result_rows] or [0]
        ),
        "recommended_validation_command": str(
            payload.get("recommended_validation_command") or ""
        ),
        "recommended_pytest_args": test_args,
        "best_candidate_id": str(best.get("candidate_id") or ""),
        "best_candidate_rule_id": str(best.get("rule_id") or ""),
        "best_candidate_variant": str(best.get("variant") or ""),
        "best_candidate_score": _float(best.get("score", 0.0)),
        "best_candidate_success": best_success,
        "best_patch": (
            _best_patch_summary(best_result)
            if best_success
            else {}
        ),
        "failure_type_counts": dict(sorted(failure_counts.items())),
        "llm_reflection_config_audit": _dict(
            search_metadata.get("llm_reflection_config_audit")
        ),
        "successful_candidates": [
            _candidate_success_summary(row) for row in success_rows
        ],
        "results": result_rows,
        "next_actions": _next_actions(
            success_count=len(success_rows),
            executed_count=len(result_rows),
            successful_reflection_count=len(reflection_success_rows),
            regression_status=regression_status,
        ),
    }


def render_repository_test_patch_validation_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Repository Test Patch Validation",
        "",
        f"- Status: `{_markdown_cell(payload.get('status', ''))}`",
        f"- Reason: `{_markdown_cell(payload.get('reason', ''))}`",
        f"- Repository Root: `{_markdown_cell(payload.get('repository_root') or 'none')}`",
        f"- Input Candidates: {_int(payload.get('input_candidate_count', payload.get('candidate_count', 0)))}",
        f"- Candidate Count: {_int(payload.get('candidate_count', 0))}",
        f"- Safety Blocked Candidates: {_int(payload.get('safety_blocked_candidate_count', 0))}",
        f"- Validation Limit: {_int(payload.get('validation_limit', 0))}",
        f"- Executed Candidates: {_int(payload.get('executed_count', 0))}",
        f"- Successful Candidates: {_int(payload.get('success_count', 0))}",
        f"- Repair Ready: {str(bool(payload.get('repair_ready', False))).lower()}",
        f"- Repair Validation Scope: `{_markdown_cell(payload.get('repair_validation_scope') or 'none')}`",
        f"- Regression Ready: {str(bool(payload.get('regression_ready', False))).lower()}",
        (
            "- Reflection: "
            f"enabled={str(bool(payload.get('reflection_enabled', False))).lower()}, "
            f"mode={_markdown_cell(payload.get('reflection_mode') or 'none')}, "
            f"status={_markdown_cell(payload.get('reflection_refiner_status') or 'none')}, "
            f"rounds={_int(payload.get('reflection_rounds', 0))}, "
            f"generated={_int(payload.get('reflection_candidate_count', 0))}, "
            f"successful={_int(payload.get('successful_reflection_candidate_count', 0))}"
        ),
        (
            "- Patch Judge: "
            f"enabled={str(bool(payload.get('patch_judge_enabled', False))).lower()}, "
            f"mode={_markdown_cell(payload.get('patch_judge_mode') or 'none')}, "
            f"status={_markdown_cell(payload.get('patch_judge_status') or 'disabled')}, "
            f"judged={_int(payload.get('patch_judge_candidate_count', 0))}, "
            f"authority={_markdown_cell(payload.get('patch_judge_authority') or 'sandbox_pytest_decides_success')}"
        ),
        (
            "- Regression Reflection: "
            f"generated={_int(payload.get('regression_reflection_candidate_count', 0))}, "
            f"successful={_int(payload.get('successful_regression_reflection_candidate_count', 0))}"
        ),
        (
            "- Reflection Refiner Reason: "
            f"`{_markdown_cell(payload.get('reflection_refiner_reason') or 'none')}`"
        ),
        f"- Best Candidate: `{_markdown_cell(payload.get('best_candidate_id') or 'none')}`",
        (
            "- Recommended Validation Command: "
            f"`{_markdown_cell(payload.get('recommended_validation_command') or 'none')}`"
        ),
        (
            "- Recommended Pytest Args: "
            f"`{_markdown_cell(' '.join(str(item) for item in _list(payload.get('recommended_pytest_args'))) or 'none')}`"
        ),
        "",
        "## Results",
        "",
        "| Rank | Depth | Candidate | Parent | Rule | Variant | Success | Failure Type | Score | Passed | Failed |",
        "| ---: | ---: | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: |",
    ]
    for index, item in enumerate(_list(payload.get("results")), start=1):
        row = _dict(item)
        lines.append(
            "| "
            f"{index} | "
            f"{_int(row.get('depth', 0))} | "
            f"`{_markdown_cell(row.get('candidate_id', ''))}` | "
            f"`{_markdown_cell(row.get('parent_candidate_id') or 'none')}` | "
            f"`{_markdown_cell(row.get('rule_id', ''))}` | "
            f"`{_markdown_cell(row.get('variant') or 'none')}` | "
            f"{str(bool(row.get('success', False))).lower()} | "
            f"`{_markdown_cell(row.get('failure_type') or 'none')}` | "
            f"{_float(row.get('score', 0.0)):.4f} | "
            f"{_int(row.get('passed', 0))} | "
            f"{_int(row.get('failed', 0))} |"
        )
    if not _list(payload.get("results")):
        lines.append("| 0 | 0 | none | none | none | none | false | none | 0.0000 | 0 | 0 |")
    lines.extend(["", "## Failure Type Counts", ""])
    counts = _dict(payload.get("failure_type_counts"))
    for failure_type, count in sorted(counts.items()):
        lines.append(f"- `{_markdown_cell(failure_type)}`: {_int(count)}")
    if not counts:
        lines.append("- none")
    lines.extend(["", "## Patch Judge", ""])
    patch_judge_audit = _dict(payload.get("patch_judge_config_audit"))
    lines.extend(
        [
            f"- Mode: `{_markdown_cell(payload.get('patch_judge_mode') or 'none')}`",
            f"- Status: `{_markdown_cell(payload.get('patch_judge_status') or 'disabled')}`",
            f"- Reason: `{_markdown_cell(payload.get('patch_judge_reason') or 'none')}`",
            f"- Authority: `{_markdown_cell(payload.get('patch_judge_authority') or 'sandbox_pytest_decides_success')}`",
            f"- Provider: `{_markdown_cell(patch_judge_audit.get('provider') or 'none')}`",
            f"- Model: `{_markdown_cell(patch_judge_audit.get('model') or 'none')}`",
            f"- API Key Present: {str(bool(patch_judge_audit.get('api_key_present', False))).lower()}",
            f"- Judged Candidates: {_int(payload.get('patch_judge_candidate_count', 0))}",
            f"- Verdict Counts: `{_markdown_cell(_format_counts(_dict(payload.get('patch_judge_verdict_counts'))))}`",
            f"- Agreement Counts: `{_markdown_cell(_format_counts(_dict(payload.get('patch_judge_agreement_counts'))))}`",
        ]
    )
    blocked = _list(payload.get("safety_blocked_candidates"))
    lines.extend(["", "## Safety Blocked Candidates", ""])
    if blocked:
        lines.append("| Candidate | Rule | Reason |")
        lines.append("| --- | --- | --- |")
        for item in blocked:
            row = _dict(item)
            lines.append(
                "| "
                f"`{_markdown_cell(row.get('candidate_id') or '')}` | "
                f"`{_markdown_cell(row.get('rule_id') or '')}` | "
                f"`{_markdown_cell(_format_list(_list(row.get('reasons'))))}` |"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Successful Candidates", ""])
    for item in _list(payload.get("successful_candidates")):
        row = _dict(item)
        lines.append(
            "- "
            f"`{_markdown_cell(row.get('candidate_id', ''))}` "
            f"rule=`{_markdown_cell(row.get('rule_id', ''))}` "
            f"variant=`{_markdown_cell(row.get('variant') or 'none')}` "
            f"score={_float(row.get('score', 0.0)):.4f}"
        )
    if not _list(payload.get("successful_candidates")):
        lines.append("- none")
    best_patch = _dict(payload.get("best_patch"))
    lines.extend(["", "## Best Patch", ""])
    if best_patch:
        lines.append(
            "- "
            f"`{_markdown_cell(best_patch.get('candidate_id', ''))}` "
            f"file=`{_markdown_cell(best_patch.get('relative_file_path') or 'none')}` "
            f"rule=`{_markdown_cell(best_patch.get('rule_id') or 'none')}` "
            f"variant=`{_markdown_cell(best_patch.get('variant') or 'none')}` "
            f"depth={_int(best_patch.get('depth', 0))}"
        )
        patch_diff = str(best_patch.get("diff") or "").rstrip()
        if patch_diff:
            lines.extend(["", "```diff", patch_diff, "```"])
    else:
        lines.append("- none")
    regression = _dict(payload.get("regression_validation"))
    lines.extend(["", "## Regression Validation", ""])
    if regression:
        lines.extend(
            [
                f"- Status: `{_markdown_cell(regression.get('status') or 'none')}`",
                f"- Reason: `{_markdown_cell(regression.get('reason') or 'none')}`",
                (
                    "- Validation Command: "
                    f"`{_markdown_cell(regression.get('validation_command') or 'none')}`"
                ),
                (
                    "- Pytest Args: "
                    f"`{_markdown_cell(' '.join(str(item) for item in _list(regression.get('pytest_args'))) or 'none')}`"
                ),
                f"- Success: {str(bool(regression.get('success', False))).lower()}",
                f"- Passed: {_int(regression.get('passed', 0))}",
                f"- Failed: {_int(regression.get('failed', 0))}",
                f"- Baseline Status: `{_markdown_cell(regression.get('baseline_status') or 'none')}`",
                (
                    "- Baseline Failed Unchanged: "
                    f"{str(bool(regression.get('baseline_failed_unchanged', False))).lower()}"
                ),
                (
                    "- Baseline Failure Type: "
                    f"`{_markdown_cell(regression.get('baseline_failure_type') or 'none')}`"
                ),
            ]
        )
    else:
        lines.append("- none")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    return "\n".join(lines)


def write_repository_test_patch_validation_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_patch_validation.json"
    markdown_path = root / "repository_test_patch_validation.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_patch_validation_markdown(payload),
        encoding="utf-8",
    )
    paths = {
        "repository_test_patch_validation_json": str(json_path),
        "repository_test_patch_validation_markdown": str(markdown_path),
    }
    reflection_trace = build_repository_test_reflection_trace(payload)
    paths.update(
        write_repository_test_reflection_trace_artifacts(
            reflection_trace,
            root,
        )
    )
    best_patch = _dict(payload.get("best_patch"))
    patch_diff = str(best_patch.get("diff") or "")
    if bool(payload.get("repair_ready", False)) and patch_diff:
        patch_path = root / "repository_test_repair.patch"
        patch_path.write_text(patch_diff, encoding="utf-8")
        paths["repository_test_repair_patch"] = str(patch_path)
    return paths


def _run_patch_validation_search(
    repository_root: Path,
    candidates: list[PatchCandidate],
    *,
    localization_scores: dict[str, float],
    test_args: list[str],
    timeout: int,
    limit: int,
    reflection_mode: str,
    reflection_rounds: int,
    reflection_width: int,
    refiner: Any | None,
    patch_search: PatchSearch | None,
    patch_judge_mode: str,
    patch_judge: PatchJudge | None,
    patch_judge_weight: float,
) -> dict[str, Any]:
    patch_judge_result = _build_patch_judge(
        mode=patch_judge_mode,
        patch_judge=patch_judge,
        weight=patch_judge_weight,
    )
    if patch_search is not None:
        patch_search.beam_width = limit
        return {
            "results": patch_search.search(
                repository_root,
                candidates,
                localization_scores=localization_scores,
                test_args=test_args,
            ),
            "metadata": {
                "reflection_enabled": False,
                "reflection_mode": "custom_patch_search",
                "reflection_refiner_status": "not_applicable",
                "reflection_refiner_reason": "custom_patch_search_provided",
                "llm_reflection_config_audit": {},
                **{
                    key: value
                    for key, value in patch_judge_result.items()
                    if key != "patch_judge"
                },
            },
        }
    refiner_result = _build_reflection_refiner(
        reflection_mode=reflection_mode,
        reflection_rounds=reflection_rounds,
        refiner=refiner,
    )
    search = BeamPatchSearch(
        sandbox=Sandbox(timeout=timeout),
        refiner=refiner_result["refiner"],
        beam_width=limit,
        max_depth=max(0, reflection_rounds),
        candidate_pool_size=limit,
        refinement_width=max(1, reflection_width),
        patch_judge=patch_judge_result["patch_judge"],
        patch_judge_weight=_float(patch_judge_result["patch_judge_weight"]),
    )
    return {
        "results": search.search(
            repository_root,
            candidates,
            localization_scores=localization_scores,
            test_args=test_args,
        ),
        "metadata": {
            **{key: value for key, value in refiner_result.items() if key != "refiner"},
            **{
                key: value
                for key, value in patch_judge_result.items()
                if key != "patch_judge"
            },
        },
    }


def _build_patch_judge(
    *,
    mode: str,
    patch_judge: PatchJudge | None,
    weight: float,
) -> dict[str, Any]:
    normalized = _normalize_patch_judge_mode(mode)
    safe_weight = max(0.0, min(1.0, _float(weight)))
    if patch_judge is not None:
        return {
            "patch_judge": patch_judge,
            "patch_judge_enabled": True,
            "patch_judge_mode": "custom",
            "patch_judge_status": "ready",
            "patch_judge_reason": "custom_patch_judge_provided",
            "patch_judge_weight": safe_weight,
            "patch_judge_config_audit": {},
        }
    if normalized == "none":
        return {
            "patch_judge": None,
            "patch_judge_enabled": False,
            "patch_judge_mode": "none",
            "patch_judge_status": "disabled",
            "patch_judge_reason": "patch_judge_mode_none",
            "patch_judge_weight": 0.0,
            "patch_judge_config_audit": {},
        }
    if normalized == "llm":
        audit = llm_config_audit("patch_judge", enabled=True)
        audit_payload = audit.to_dict()
        if not audit.api_key_present:
            return {
                "patch_judge": None,
                "patch_judge_enabled": False,
                "patch_judge_mode": "llm",
                "patch_judge_status": "unavailable",
                "patch_judge_reason": f"missing_api_key:{audit.api_key_env}",
                "patch_judge_weight": 0.0,
                "patch_judge_config_audit": audit_payload,
            }
        return {
            "patch_judge": build_patch_judge("llm"),
            "patch_judge_enabled": True,
            "patch_judge_mode": "llm",
            "patch_judge_status": "ready",
            "patch_judge_reason": "llm_patch_judge_ready",
            "patch_judge_weight": safe_weight,
            "patch_judge_config_audit": audit_payload,
        }
    raise ValueError(f"Unsupported patch judge mode: {mode}")


def _build_reflection_refiner(
    *,
    reflection_mode: str,
    reflection_rounds: int,
    refiner: Any | None,
) -> dict[str, Any]:
    mode = _normalize_reflection_mode(reflection_mode)
    if reflection_rounds <= 0 or mode == "none":
        return {
            "refiner": None,
            "reflection_enabled": False,
            "reflection_mode": mode,
            "reflection_refiner_status": "disabled",
            "reflection_refiner_reason": "reflection_rounds_zero_or_mode_none",
            "llm_reflection_config_audit": {},
        }
    if refiner is not None:
        return {
            "refiner": refiner,
            "reflection_enabled": True,
            "reflection_mode": "custom",
            "reflection_refiner_status": "ready",
            "reflection_refiner_reason": "custom_refiner_provided",
            "llm_reflection_config_audit": {},
        }
    if mode == "rule":
        return {
            "refiner": PatchGenerator(),
            "reflection_enabled": True,
            "reflection_mode": "rule",
            "reflection_refiner_status": "ready",
            "reflection_refiner_reason": "rule_refiner",
            "llm_reflection_config_audit": {},
        }
    if mode == "llm":
        audit = llm_config_audit("patch_generation", enabled=True)
        audit_payload = audit.to_dict()
        if not audit.api_key_present:
            return {
                "refiner": None,
                "reflection_enabled": False,
                "reflection_mode": "llm",
                "reflection_refiner_status": "unavailable",
                "reflection_refiner_reason": f"missing_api_key:{audit.api_key_env}",
                "llm_reflection_config_audit": audit_payload,
            }
        return {
            "refiner": LLMPatchGenerator(create_patch_client()),
            "reflection_enabled": True,
            "reflection_mode": "llm",
            "reflection_refiner_status": "ready",
            "reflection_refiner_reason": "llm_refiner",
            "llm_reflection_config_audit": audit_payload,
        }
    return {
        "refiner": None,
        "reflection_enabled": False,
        "reflection_mode": mode,
        "reflection_refiner_status": "unsupported",
        "reflection_refiner_reason": f"unsupported_reflection_mode:{mode}",
        "llm_reflection_config_audit": {},
    }


def _run_regression_reflection(
    repository_root: Path,
    best_result: Any | None,
    regression_validation: dict[str, Any],
    *,
    regression_pytest_args: list[str] | None,
    narrow_pytest_args: list[str],
    timeout: int,
    reflection_mode: str,
    reflection_rounds: int,
    reflection_width: int,
    refiner: Any | None,
) -> dict[str, Any]:
    if str(regression_validation.get("status") or "") != "fail":
        return {"results": []}
    if best_result is None or not bool(getattr(best_result, "success", False)):
        return {"results": []}
    best_depth = _int(getattr(best_result, "depth", 0))
    if reflection_rounds <= best_depth:
        return {"results": []}
    refiner_result = _build_reflection_refiner(
        reflection_mode=reflection_mode,
        reflection_rounds=reflection_rounds,
        refiner=refiner,
    )
    reflection_refiner = refiner_result.get("refiner")
    if reflection_refiner is None:
        return {"results": [], "metadata": refiner_result}

    candidate = getattr(best_result, "candidate", None)
    if candidate is None:
        return {"results": [], "metadata": refiner_result}
    regression_execution = _execution_result_from_regression_validation(
        regression_validation
    )
    refined_candidates = _refine_from_regression_feedback(
        reflection_refiner,
        repo_path=repository_root,
        previous_patch=candidate,
        execution_result=regression_execution,
        round_index=best_depth + 1,
        limit=max(1, reflection_width),
    )
    if not refined_candidates:
        return {"results": [], "metadata": refiner_result}

    validation_args = _merge_pytest_args(
        narrow_pytest_args,
        [str(arg) for arg in _list(regression_pytest_args)],
    )
    sandbox = Sandbox(timeout=timeout)
    parent_id = str(getattr(candidate, "id", "") or "")
    nodes: list[BeamPatchNode] = []
    for child_index, refined in enumerate(refined_candidates):
        refined = _with_regression_reflection_metadata(
            refined,
            parent_id=parent_id,
            child_index=child_index,
            sibling_count=len(refined_candidates),
        )
        execution = sandbox.apply_patch_and_test(
            repository_root,
            refined,
            test_args=validation_args,
        )
        refined = annotate_execution_feedback(refined, execution)
        nodes.append(
            BeamPatchNode(
                candidate=refined,
                execution_result=execution,
                score=1.0 if execution.success else 0.0,
                depth=best_depth + 1,
                feedback_score=execution_feedback_score(refined),
                retained=True,
                retention_bucket="regression_reflection",
                retention_reason="generated_from_regression_failure",
                parent_id=parent_id,
                trace=[f"{parent_id}:regression_failed"],
            )
        )
    return {"results": nodes, "metadata": refiner_result}


def _refine_from_regression_feedback(
    reflection_refiner: Any,
    *,
    repo_path: Path,
    previous_patch: PatchCandidate,
    execution_result: ExecutionResult,
    round_index: int,
    limit: int,
) -> list[PatchCandidate]:
    refine_many = getattr(reflection_refiner, "refine_many", None)
    if callable(refine_many):
        return list(
            refine_many(
                repo_path=repo_path,
                previous_patch=previous_patch,
                execution_result=execution_result,
                round_index=round_index,
                limit=limit,
            )
        )[:limit]
    refine = getattr(reflection_refiner, "refine", None)
    if callable(refine):
        refined = refine(
            repo_path=repo_path,
            previous_patch=previous_patch,
            execution_result=execution_result,
            round_index=round_index,
        )
        return [refined] if refined is not None else []
    return []


def _with_regression_reflection_metadata(
    candidate: PatchCandidate,
    *,
    parent_id: str,
    child_index: int,
    sibling_count: int,
) -> PatchCandidate:
    return replace(
        candidate,
        metadata={
            **candidate.metadata,
            "regression_reflection": True,
            "regression_reflection_parent_id": parent_id,
            "repair_loop_parent_id": parent_id,
            "beam_parent_id": parent_id,
            "reflection_child_index": child_index,
            "reflection_sibling_count": sibling_count,
            "search_profile_role": "regression_reflection_refined_candidate",
        },
    )


def _execution_result_from_regression_validation(
    regression_validation: dict[str, Any],
) -> ExecutionResult:
    return ExecutionResult(
        success=bool(regression_validation.get("success", False)),
        returncode=_int(regression_validation.get("returncode", 0)),
        stdout=str(regression_validation.get("stdout_preview") or ""),
        stderr=str(regression_validation.get("stderr_preview") or ""),
        traceback=str(regression_validation.get("traceback_preview") or ""),
        passed=_int(regression_validation.get("passed", 0)),
        failed=_int(regression_validation.get("failed", 0)),
        timeout=bool(regression_validation.get("timeout", False)),
        command=[str(item) for item in _list(regression_validation.get("command"))],
    )


def _merge_pytest_args(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for arg in group:
            value = str(arg)
            if value in seen:
                continue
            seen.add(value)
            merged.append(value)
    return merged


def _best_result_index(
    rows: list[dict[str, Any]],
    *,
    prefer_deep_success: bool = False,
) -> int:
    if prefer_deep_success:
        successful = [
            (index, row)
            for index, row in enumerate(rows)
            if bool(row.get("success", False))
        ]
        if successful:
            return max(
                successful,
                key=lambda item: (_int(item[1].get("depth", 0)), -item[0]),
            )[0]
    for index, row in enumerate(rows):
        if bool(row.get("success", False)):
            return index
    return 0 if rows else -1


def _best_patch_summary(result: Any | None) -> dict[str, Any]:
    if result is None:
        return {}
    candidate = getattr(result, "candidate", None)
    execution = getattr(result, "execution_result", None)
    if candidate is None:
        return {}
    metadata = _dict(getattr(candidate, "metadata", {}))
    return {
        "candidate_id": str(getattr(candidate, "id", "")),
        "target_file": str(getattr(candidate, "target_file", "")),
        "relative_file_path": str(getattr(candidate, "relative_file_path", "")),
        "target_function_id": str(getattr(candidate, "target_function_id", "")),
        "target_function_name": str(
            getattr(candidate, "target_function_name", "")
        ),
        "rule_id": str(getattr(candidate, "rule_id", "")),
        "variant": str(metadata.get("variant") or ""),
        "description": str(getattr(candidate, "description", "")),
        "depth": _int(getattr(result, "depth", 0)),
        "parent_candidate_id": str(getattr(result, "parent_id", None) or ""),
        "score": _float(getattr(result, "score", 0.0)),
        "feedback_score": _float(getattr(result, "feedback_score", 0.0)),
        "passed": _int(getattr(execution, "passed", 0)),
        "failed": _int(getattr(execution, "failed", 0)),
        "old_source": str(getattr(candidate, "old_source", "")),
        "new_source": str(getattr(candidate, "new_source", "")),
        "diff": str(getattr(candidate, "diff", "")),
        "apply_strategy": "replace_old_source_once",
    }


def _validate_best_patch_regression(
    repository_root: Path,
    best_result: Any | None,
    *,
    regression_pytest_args: list[str] | None,
    narrow_pytest_args: list[str],
    timeout: int,
    validation_command: str,
) -> dict[str, Any]:
    args = [str(arg) for arg in _list(regression_pytest_args)]
    if best_result is None or not bool(getattr(best_result, "success", False)):
        return _regression_validation_skipped(
            reason="no_successful_patch",
            validation_command=validation_command,
            pytest_args=args,
        )
    if not args:
        return _regression_validation_skipped(
            reason="regression_args_missing",
            validation_command=validation_command,
            pytest_args=args,
        )
    if args == [str(arg) for arg in _list(narrow_pytest_args)]:
        return _regression_validation_skipped(
            reason="duplicate_narrow_scope",
            validation_command=validation_command,
            pytest_args=args,
        )
    candidate = getattr(best_result, "candidate", None)
    if candidate is None:
        return _regression_validation_skipped(
            reason="best_patch_missing",
            validation_command=validation_command,
            pytest_args=args,
        )
    baseline_execution = Sandbox(timeout=timeout).run_tests(
        repository_root,
        test_args=args,
    )
    execution = Sandbox(timeout=timeout).apply_patch_and_test(
        repository_root,
        candidate,
        test_args=args,
    )
    baseline_signature = _failure_signature(
        baseline_execution,
        repository_root=repository_root,
    )
    patched_signature = _failure_signature(execution)
    baseline_failed_unchanged = (
        not baseline_execution.success
        and not execution.success
        and bool(baseline_signature)
        and baseline_signature == patched_signature
    )
    status = (
        "pass"
        if execution.success
        else "baseline_failed_unchanged"
        if baseline_failed_unchanged
        else "fail"
    )
    return {
        "status": status,
        "reason": (
            "regression_tests_passed"
            if execution.success
            else "regression_baseline_failed_unchanged"
            if baseline_failed_unchanged
            else "regression_tests_failed"
        ),
        "validation_command": validation_command,
        "pytest_args": args,
        "success": bool(execution.success),
        "baseline_status": "pass" if baseline_execution.success else "fail",
        "baseline_success": bool(baseline_execution.success),
        "baseline_returncode": baseline_execution.returncode,
        "baseline_passed": baseline_execution.passed,
        "baseline_failed": baseline_execution.failed,
        "baseline_failure_type": classify_execution_result(baseline_execution),
        "baseline_failure_reason": summarize_failure_reason(baseline_execution),
        "baseline_failure_signature": baseline_signature,
        "patched_failure_signature": patched_signature,
        "baseline_failed_unchanged": baseline_failed_unchanged,
        "returncode": execution.returncode,
        "passed": execution.passed,
        "failed": execution.failed,
        "timeout": execution.timeout,
        "command": list(execution.command),
        "failure_type": classify_execution_result(execution),
        "failure_reason": summarize_failure_reason(execution),
        "stdout_preview": _preview(execution.stdout),
        "stderr_preview": _preview(execution.stderr),
        "traceback_preview": _preview(execution.traceback),
        "baseline_stdout_preview": _preview(baseline_execution.stdout),
        "baseline_stderr_preview": _preview(baseline_execution.stderr),
        "baseline_traceback_preview": _preview(baseline_execution.traceback),
    }


def _regression_validation_skipped(
    *,
    reason: str,
    validation_command: str,
    pytest_args: list[str],
) -> dict[str, Any]:
    return {
        "status": "skipped",
        "reason": reason,
        "validation_command": validation_command,
        "pytest_args": list(pytest_args),
        "success": False,
        "returncode": 0,
        "passed": 0,
        "failed": 0,
        "timeout": False,
        "command": [],
        "failure_type": "",
        "failure_reason": "",
        "baseline_status": "skipped",
        "baseline_success": False,
        "baseline_returncode": 0,
        "baseline_passed": 0,
        "baseline_failed": 0,
        "baseline_failure_type": "",
        "baseline_failure_reason": "",
        "baseline_failure_signature": "",
        "patched_failure_signature": "",
        "baseline_failed_unchanged": False,
        "stdout_preview": "",
        "stderr_preview": "",
        "traceback_preview": "",
    }


def _repair_validation_scope(
    *,
    best_success: bool,
    regression_status: str,
) -> str:
    if not best_success:
        return "none"
    if regression_status == "pass":
        return "narrow_and_regression"
    if regression_status == "baseline_failed_unchanged":
        return "narrow_and_unchanged_regression_baseline"
    if regression_status == "fail":
        return "regression_failed"
    return "narrow_only"


def _validation_result_to_dict(result) -> dict[str, Any]:
    candidate = result.candidate
    execution = result.execution_result
    metadata = _dict(candidate.metadata)
    feedback = _dict(metadata.get("execution_feedback"))
    depth = _int(getattr(result, "depth", 0))
    parent_id = getattr(result, "parent_id", None)
    failure_type = str(
        feedback.get("failure_type")
        or classify_execution_result(execution)
        or "unknown"
    )
    return {
        "candidate_id": candidate.id,
        "target_function_id": candidate.target_function_id,
        "target_function_name": candidate.target_function_name,
        "relative_file_path": candidate.relative_file_path,
        "rule_id": candidate.rule_id,
        "variant": str(metadata.get("variant") or ""),
        "depth": depth,
        "parent_candidate_id": str(parent_id or ""),
        "retained": bool(getattr(result, "retained", True)),
        "retention_bucket": str(getattr(result, "retention_bucket", "") or ""),
        "retention_reason": str(getattr(result, "retention_reason", "") or ""),
        "success": bool(execution.success),
        "returncode": execution.returncode,
        "passed": execution.passed,
        "failed": execution.failed,
        "timeout": execution.timeout,
        "command": list(execution.command),
        "failure_type": failure_type,
        "failure_reason": summarize_failure_reason(execution),
        "score": result.score,
        "feedback_score": result.feedback_score,
        "search_prior_score": _float(metadata.get("search_prior_score", 0.0)),
        "search_diversity": _dict(metadata.get("search_diversity")),
        "search_deduplication": _dict(metadata.get("search_deduplication")),
        "beam_retention": _dict(metadata.get("beam_retention")),
        "patch_judgment": _dict(metadata.get("patch_judgment")),
        "patch_judge_weight": _float(metadata.get("patch_judge_weight", 0.0)),
        "repair_loop_parent_id": str(metadata.get("repair_loop_parent_id") or ""),
        "beam_parent_id": str(metadata.get("beam_parent_id") or ""),
        "regression_reflection": bool(
            metadata.get("regression_reflection", False)
        ),
        "regression_reflection_parent_id": str(
            metadata.get("regression_reflection_parent_id") or ""
        ),
        "execution_feedback": feedback,
        "safety_gate": _dict(metadata.get("safety_gate")),
        "stdout_preview": _preview(execution.stdout),
        "stderr_preview": _preview(execution.stderr),
        "traceback_preview": _preview(execution.traceback),
    }


def _patch_candidate_from_dict(row: Any) -> PatchCandidate | None:
    data = _dict(row)
    if not data:
        return None
    relative_file_path = str(data.get("relative_file_path") or "")
    old_source = data.get("old_source")
    new_source = data.get("new_source")
    if not relative_file_path or old_source is None or new_source is None:
        return None
    return PatchCandidate(
        id=str(data.get("id") or relative_file_path),
        target_file=str(data.get("target_file") or relative_file_path),
        relative_file_path=relative_file_path,
        target_function_id=str(data.get("target_function_id") or ""),
        target_function_name=str(data.get("target_function_name") or ""),
        rule_id=str(data.get("rule_id") or ""),
        description=str(data.get("description") or ""),
        old_source=str(old_source),
        new_source=str(new_source),
        diff=str(data.get("diff") or ""),
        metadata=dict(_dict(data.get("metadata"))),
    )


def _candidate_blocked_by_safety(candidate: PatchCandidate) -> bool:
    safety = _dict(candidate.metadata.get("safety_gate"))
    return str(safety.get("status") or "") == "blocked"


def _safety_blocked_candidate_summary(candidate: PatchCandidate) -> dict[str, Any]:
    safety = _dict(candidate.metadata.get("safety_gate"))
    return {
        "candidate_id": candidate.id,
        "target_function_name": candidate.target_function_name,
        "relative_file_path": candidate.relative_file_path,
        "rule_id": candidate.rule_id,
        "variant": str(candidate.metadata.get("variant") or ""),
        "reasons": [str(item) for item in _list(safety.get("reasons"))],
        "ast_valid": bool(safety.get("ast_valid", False)),
        "scope_limited": bool(safety.get("scope_limited", False)),
        "minimal_diff": bool(safety.get("minimal_diff", False)),
    }


def _localization_scores(payload: dict[str, Any]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for item in _list(payload.get("targets")):
        row = _dict(item)
        function_id = str(row.get("function_id") or "")
        if function_id:
            scores[function_id] = _float(row.get("score", 0.0))
    return scores


def _candidate_success_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": row.get("candidate_id", ""),
        "target_function_name": row.get("target_function_name", ""),
        "relative_file_path": row.get("relative_file_path", ""),
        "rule_id": row.get("rule_id", ""),
        "variant": row.get("variant", ""),
        "depth": _int(row.get("depth", 0)),
        "parent_candidate_id": row.get("parent_candidate_id", ""),
        "score": _float(row.get("score", 0.0)),
        "passed": _int(row.get("passed", 0)),
        "failed": _int(row.get("failed", 0)),
    }


def _validation_reason(
    *,
    success_count: int,
    depth0_success_count: int,
    reflection_success_count: int,
) -> str:
    if success_count <= 0:
        return "no_candidate_passed_repository_tests"
    if reflection_success_count > 0:
        return "patch_validation_reflection_success"
    if depth0_success_count > 0:
        return "patch_validation_success"
    return "patch_validation_success"


def _next_actions(
    *,
    success_count: int,
    executed_count: int,
    successful_reflection_count: int,
    regression_status: str = "",
) -> list[str]:
    if success_count > 0:
        if regression_status == "fail":
            return [
                "Inspect regression_validation before promoting the repair patch.",
                "Use execution feedback to refine the successful narrow-scope patch.",
            ]
        if regression_status == "baseline_failed_unchanged":
            return [
                "Promote the narrow-scope repair with baseline-regression caveat.",
                "Fix or narrow the pre-existing regression command before claiming full-suite green status.",
            ]
        actions = [
            "Promote the best successful candidate as the repository repair result.",
            "Run broader regression tests before committing the patch.",
        ]
        if successful_reflection_count > 0:
            actions.append(
                "Preserve reflection parent/child evidence for the repair report."
            )
        return actions
    if executed_count > 0:
        return [
            "Use execution_feedback and failure_type_counts to drive the reflection loop.",
            "Generate refined candidates for recoverable test_failure or runtime_error results.",
        ]
    return ["Generate valid patch candidates before running validation."]


def _skipped(
    *,
    reason: str,
    message: str,
    patch_candidates: dict[str, Any] | None = None,
    repository_root: str = "",
    test_args: list[str] | None = None,
    reflection_mode: str = "rule",
    input_candidate_count: int | None = None,
    safety_blocked_candidates: list[PatchCandidate] | None = None,
) -> dict[str, Any]:
    payload = _dict(patch_candidates)
    blocked_candidates = list(safety_blocked_candidates or [])
    return {
        "status": "skipped",
        "reason": reason,
        "message": message,
        "repository_root": repository_root,
        "input_candidate_count": (
            input_candidate_count
            if input_candidate_count is not None
            else _int(payload.get("candidate_count", 0))
        ),
        "candidate_count": (
            0
            if blocked_candidates
            else _int(payload.get("candidate_count", 0))
        ),
        "safety_gate": _dict(payload.get("safety_gate")),
        "safety_blocked_candidate_count": len(blocked_candidates),
        "safety_blocked_candidates": [
            _safety_blocked_candidate_summary(candidate)
            for candidate in blocked_candidates
        ],
        "validation_limit": 0,
        "executed_count": 0,
        "depth0_executed_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "repair_ready": False,
        "repair_validation_scope": "none",
        "regression_ready": False,
        "regression_validation": _regression_validation_skipped(
            reason="patch_validation_not_run",
            validation_command="",
            pytest_args=[],
        ),
        "reflection_enabled": False,
        "reflection_mode": _normalize_reflection_mode(reflection_mode),
        "reflection_refiner_status": "not_started",
        "reflection_refiner_reason": reason,
        "reflection_rounds": 0,
        "reflection_width": 1,
        "reflection_candidate_count": 0,
        "successful_reflection_candidate_count": 0,
        "max_depth_executed": 0,
        "recommended_validation_command": str(
            payload.get("recommended_validation_command") or ""
        ),
        "recommended_pytest_args": list(
            test_args if test_args is not None else _list(payload.get("recommended_pytest_args"))
        ),
        "best_candidate_id": "",
        "best_candidate_rule_id": "",
        "best_candidate_variant": "",
        "best_candidate_score": 0.0,
        "best_candidate_success": False,
        "best_patch": {},
        "failure_type_counts": {},
        "llm_reflection_config_audit": {},
        "patch_judge_enabled": False,
        "patch_judge_mode": "none",
        "patch_judge_status": "disabled",
        "patch_judge_reason": "patch_validation_not_executed",
        "patch_judge_weight": 0.0,
        "patch_judge_config_audit": {},
        "patch_judge_candidate_count": 0,
        "patch_judge_verdict_counts": {},
        "patch_judge_agreement_counts": {},
        "patch_judge_authority": "sandbox_pytest_decides_success",
        "successful_candidates": [],
        "results": [],
        "next_actions": _skipped_next_actions(reason),
    }


def _skipped_next_actions(reason: str) -> list[str]:
    if reason == "patch_candidates_not_ready":
        return ["Generate repository_test_patch_candidates before validation."]
    if reason == "validation_args_missing":
        return [
            "Run repository tests until failing nodeids are available, then validate candidates against those nodeids."
        ]
    if reason == "repository_root_missing":
        return ["Provide --repository-test-root or enable --checkout-repository-tests."]
    if reason == "candidate_payload_empty":
        return ["Inspect repository_test_patch_candidates.json for malformed candidates."]
    if reason == "all_candidates_blocked_by_safety_gate":
        return [
            "Inspect safety_blocked_candidates before sandbox validation.",
            "Generate smaller AST-valid, scope-limited patch candidates.",
        ]
    if reason == "patch_candidates_missing":
        return ["Run repository test patch candidate generation before validation."]
    return []


def _normalize_reflection_mode(value: Any) -> str:
    mode = str(value or "rule").strip().lower().replace("_", "-")
    if mode in {"off", "disabled", "disable", "false", "0"}:
        return "none"
    if mode in {"deepseek", "llm-reflection", "llm"}:
        return "llm"
    if mode in {"rule", "rules", "rule-based", "rule-reflection"}:
        return "rule"
    if mode in {"none", "no-reflection"}:
        return "none"
    return mode


def _normalize_patch_judge_mode(value: Any) -> str:
    mode = str(value or "none").strip().lower().replace("_", "-")
    if mode in {"off", "disabled", "disable", "false", "0", "none", "no-judge"}:
        return "none"
    if mode in {"deepseek", "llm-judge", "patch-llm", "llm"}:
        return "llm"
    return mode


def _preview(value: str, limit: int = 600) -> str:
    text = value or ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _failure_signature(
    result: ExecutionResult,
    *,
    repository_root: Path | None = None,
) -> str:
    if result.success:
        return ""
    text = "\n".join(
        part
        for part in [result.stdout, result.stderr, result.traceback]
        if part
    )
    signature_lines: list[str] = []
    for raw_line in text.splitlines():
        line = _normalize_failure_line(
            raw_line,
            repository_root=repository_root,
        )
        if not line:
            continue
        if (
            line.startswith(("FAILED ", "ERROR "))
            or " ERROR collecting " in line
            or line.startswith(("SyntaxError:", "ImportError:", "ModuleNotFoundError:"))
            or line.startswith(("AssertionError", "E   "))
        ):
            signature_lines.append(line)
        if len(signature_lines) >= 8:
            break
    if not signature_lines:
        reason = summarize_failure_reason(result)
        signature_lines = [
            _normalize_failure_line(reason, repository_root=repository_root)
        ] if reason else []
    failure_type = classify_execution_result(result)
    return "\n".join([failure_type, *signature_lines]).strip()


def _normalize_failure_line(
    value: str,
    *,
    repository_root: Path | None = None,
) -> str:
    text = value.strip()
    text = text.replace("\\", "/")
    cwd = str(Path.cwd()).replace("\\", "/")
    if cwd:
        text = text.replace(f"{cwd}/", "")
    if repository_root is not None:
        root = str(Path(repository_root)).replace("\\", "/")
        if root:
            text = text.replace(f"{root}/", "")
    text = text.replace("C:/Users/86257/AppData/Local/Temp/", "<temp>/")
    return _normalize_failure_file_paths(text)


def _normalize_failure_file_paths(value: str) -> str:
    def replace_match(match: re.Match[str]) -> str:
        path = _repo_relative_failure_path(match.group("path"))
        return f'{match.group("prefix")}{path}{match.group("suffix")}'

    return re.sub(
        r'(?P<prefix>File ")(?P<path>[^"]+)(?P<suffix>", line \d+)',
        replace_match,
        value,
    )


def _repo_relative_failure_path(value: str) -> str:
    path = value.replace("\\", "/")
    for marker in (
        "repository_test_failure_overlay_checkout/",
        "repository_checkout/",
        "/repo/",
    ):
        if marker in path:
            return path.split(marker, 1)[1]
    return path


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _format_list(values: list[Any]) -> str:
    rows = [str(value) for value in values if str(value)]
    return ", ".join(rows) if rows else "none"


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(
        f"{key}={_int(value)}" for key, value in sorted(counts.items())
    )


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")
