from __future__ import annotations

import hashlib
import json
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from code_intelligence_agent.agents.llm_client import (
    LLMClient,
    LLMRequestError,
)
from code_intelligence_agent.core.models import CodeEntity
from code_intelligence_agent.core.git_change_history import GitChangeHistoryAnalyzer
from code_intelligence_agent.evaluation.repository_test_dynamic_evidence import (
    build_repository_test_dynamic_evidence,
)
from code_intelligence_agent.evaluation.repository_test_fault_localization import (
    build_repository_test_fault_localization,
)
from code_intelligence_agent.evaluation.repository_test_patch_candidates import (
    build_repository_test_patch_candidates,
)
from code_intelligence_agent.evaluation.v3_repair_execution import (
    build_v3_run_record,
    create_v3_repair_client,
    execute_v3_patch_candidate,
    provider_blocker_execution,
    utc_now,
    write_v3_candidate_artifacts,
)
from code_intelligence_agent.evaluation.v3_repair_scope import (
    select_v3_analysis_scope,
)
from code_intelligence_agent.evaluation.v3_repair_trial import (
    EditableRegion,
    audit_v3_model_context,
    build_v3_editable_regions,
    build_v3_model_context,
    parse_v3_patch_response,
    parse_v3_source_scope,
    render_v3_model_prompt,
    sanitize_v3_untrusted_text,
)
from code_intelligence_agent.tools.patch_validation import (
    allow_signature_change_for_rules,
)


@dataclass(frozen=True)
class PreparedV3RepairCase:
    case: dict[str, Any]
    seed_repository: Path
    dynamic_evidence: dict[str, Any]
    analysis_scope: dict[str, Any]
    analysis_scope_ground_truth_audit: dict[str, Any]
    localization: dict[str, Any]
    editable_regions: list[EditableRegion]
    model_context: dict[str, Any]
    model_context_audit: dict[str, Any]
    model_context_artifact: str
    preparation_artifacts: dict[str, str]


def prepare_v3_repair_case(
    case: dict[str, Any],
    *,
    seed_repository: str | Path,
    baseline_execution: dict[str, Any],
    output_dir: str | Path,
    localization_top_k: int = 50,
    editable_region_limit: int = 24,
) -> PreparedV3RepairCase:
    seed = Path(seed_repository).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    dynamic = build_repository_test_dynamic_evidence(baseline_execution)
    analysis_scope = select_v3_analysis_scope(
        seed,
        case=case,
        dynamic_evidence=dynamic,
    )
    analysis_paths = analysis_scope.get("analysis_paths")
    history_analyzer = (
        GitChangeHistoryAnalyzer(max_files=12, timeout_seconds=2.0)
        if analysis_paths
        else None
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        localization = build_repository_test_fault_localization(
            dynamic,
            repository_root=seed,
            top_k=localization_top_k,
            analysis_paths=analysis_paths,
            history_analyzer=history_analyzer,
        )
        regions, skipped = build_v3_editable_regions(
            seed,
            localization,
            top_k=editable_region_limit,
            analysis_paths=analysis_paths,
        )
    context = build_v3_model_context(
        case,
        repository_root=seed,
        dynamic_evidence=dynamic,
        localization=localization,
        editable_regions=regions,
        skipped_regions=skipped,
        analysis_scope=analysis_scope,
    )
    context_audit = audit_v3_model_context(
        context,
        case=case,
        repository_root=seed,
    )
    scope_ground_truth_audit = audit_v3_analysis_scope_ground_truth(
        case,
        analysis_scope=analysis_scope,
        editable_regions=regions,
    )
    context_path = output / "model_context.json"
    context_audit_path = output / "model_context_audit.json"
    dynamic_path = output / "dynamic_evidence.json"
    localization_path = output / "fault_localization.json"
    region_path = output / "editable_regions.json"
    analysis_scope_path = output / "analysis_scope.json"
    scope_ground_truth_audit_path = output / "analysis_scope_ground_truth_audit.json"
    context_path.write_text(
        json.dumps(context, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    context_audit_path.write_text(
        json.dumps(context_audit, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    dynamic_path.write_text(
        json.dumps(dynamic, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    localization_path.write_text(
        json.dumps(localization, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    analysis_scope_path.write_text(
        json.dumps(analysis_scope, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    scope_ground_truth_audit_path.write_text(
        json.dumps(scope_ground_truth_audit, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    region_path.write_text(
        json.dumps(
            {
                "regions": [region.to_audit_dict() for region in regions],
                "skipped": skipped,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return PreparedV3RepairCase(
        case=case,
        seed_repository=seed,
        dynamic_evidence=dynamic,
        analysis_scope=analysis_scope,
        analysis_scope_ground_truth_audit=scope_ground_truth_audit,
        localization=localization,
        editable_regions=regions,
        model_context=context,
        model_context_audit=context_audit,
        model_context_artifact=context_path.as_posix(),
        preparation_artifacts={
            "model_context": context_path.as_posix(),
            "model_context_audit": context_audit_path.as_posix(),
            "dynamic_evidence": dynamic_path.as_posix(),
            "analysis_scope": analysis_scope_path.as_posix(),
            "analysis_scope_ground_truth_audit": (
                scope_ground_truth_audit_path.as_posix()
            ),
            "fault_localization": localization_path.as_posix(),
            "editable_regions": region_path.as_posix(),
        },
    )


def audit_v3_analysis_scope_ground_truth(
    case: dict[str, Any],
    *,
    analysis_scope: dict[str, Any],
    editable_regions: list[EditableRegion],
) -> dict[str, Any]:
    ground_truth_files = [
        normalized
        for value in _list(_dict(case.get("ground_truth")).get("source_files"))
        if (normalized := _normalized_audit_path(str(value or "")))
    ]
    scoped_paths = {
        normalized
        for value in _list(analysis_scope.get("analysis_paths"))
        if (normalized := _normalized_audit_path(str(value or "")))
    }
    full_repository = str(analysis_scope.get("mode") or "") == "full_repository"
    editable_paths = {region.path for region in editable_regions}
    module_paths = {
        region.path for region in editable_regions if region.region_kind == "module"
    }
    rows = [
        {
            "path": path,
            "in_analysis_scope": full_repository or path in scoped_paths,
            "has_editable_region": path in editable_paths,
            "has_module_region": path in module_paths,
        }
        for path in ground_truth_files
    ]
    analysis_hits = sum(bool(row["in_analysis_scope"]) for row in rows)
    editable_hits = sum(bool(row["has_editable_region"]) for row in rows)
    denominator = len(rows)
    status = (
        "pass"
        if denominator > 0
        and analysis_hits == denominator
        and editable_hits == denominator
        else "fail"
    )
    selection_snapshot = {
        "analysis_scope": analysis_scope,
        "editable_regions": [region.to_audit_dict() for region in editable_regions],
    }
    return {
        "status": status,
        "case_id": str(case.get("case_id") or ""),
        "selection_frozen_before_ground_truth_audit": True,
        "ground_truth_used_for_selection": False,
        "ground_truth_visible_to_model": False,
        "ground_truth_file_count": denominator,
        "analysis_scope_file_hits": analysis_hits,
        "analysis_scope_file_recall": _ratio(analysis_hits, denominator),
        "editable_file_hits": editable_hits,
        "editable_file_recall": _ratio(editable_hits, denominator),
        "files": rows,
        "selection_snapshot_sha256": _sha256_text(
            json.dumps(selection_snapshot, sort_keys=True, ensure_ascii=False)
        ),
    }


def run_v3_repair_trial(
    protocol: dict[str, Any],
    prepared: PreparedV3RepairCase,
    *,
    project_root: str | Path,
    output_dir: str | Path,
    strategy_mode: str,
    trial_index: int,
    python_executable: str | Path,
    llm_client: LLMClient | None = None,
    reflection_client: LLMClient | None = None,
    rule_candidate_limit: int = 5,
    targeted_timeout: int = 120,
    regression_timeout: int = 900,
) -> dict[str, Any]:
    mode = str(strategy_mode or "").lower()
    if mode not in {"rule", "llm", "hybrid"}:
        raise ValueError(f"Unsupported V3 repair strategy: {strategy_mode}")
    if trial_index < 1:
        raise ValueError("trial_index must be positive")
    if mode == "rule" and trial_index != 1:
        raise ValueError("rule strategy has exactly one trial")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    trial_id = str(uuid.uuid4())
    records: list[dict[str, Any]] = []
    candidate_summaries: list[dict[str, Any]] = []
    failed_diff_fingerprints: set[str] = set()
    failed_source_fingerprints: set[str] = set()
    candidate_index = 0

    if mode in {"rule", "hybrid"}:
        rule_candidates = build_v3_rule_candidates(
            prepared,
            limit=rule_candidate_limit,
        )
        if not rule_candidates:
            candidate_index += 1
            candidate_id = _candidate_id(
                prepared.case,
                mode=mode,
                trial_index=trial_index,
                candidate_index=candidate_index,
                label="rule-none",
            )
            execution = _generation_failure_execution(
                "no_rule_patch_candidates_generated"
            )
            record = _record_candidate(
                protocol,
                prepared=prepared,
                mode=mode,
                trial_index=trial_index,
                trial_id=trial_id,
                candidate_index=candidate_index,
                candidate_id=candidate_id,
                generator_family="rule",
                generator_id="rule_based:no_candidate",
                reflection_round=0,
                parent_candidate_id="",
                prompt_id="",
                llm_metadata={},
                execution=execution,
                output_dir=output,
            )
            records.append(record)
            candidate_summaries.append(_record_summary(record))
        for rule_item in rule_candidates:
            candidate_index += 1
            candidate_id = _candidate_id(
                prepared.case,
                mode=mode,
                trial_index=trial_index,
                candidate_index=candidate_index,
                label="rule",
            )
            workspace = output / "workspaces" / f"candidate-{candidate_index}"
            execution = execute_v3_patch_candidate(
                _dict(rule_item.get("candidate")),
                editable_regions=[
                    *prepared.editable_regions,
                    rule_item["editable_region"],
                ],
                seed_repository=prepared.seed_repository,
                trial_workspace=workspace,
                case=prepared.case,
                python_executable=python_executable,
                targeted_timeout=targeted_timeout,
                regression_timeout=regression_timeout,
                failed_diff_fingerprints=failed_diff_fingerprints,
                failed_source_fingerprints=failed_source_fingerprints,
            )
            record = _record_candidate(
                protocol,
                prepared=prepared,
                mode=mode,
                trial_index=trial_index,
                trial_id=trial_id,
                candidate_index=candidate_index,
                candidate_id=candidate_id,
                generator_family="rule",
                generator_id=str(rule_item.get("generator_id") or "rule_based"),
                reflection_round=0,
                parent_candidate_id="",
                prompt_id="",
                llm_metadata={},
                execution=execution,
                output_dir=output,
            )
            records.append(record)
            candidate_summaries.append(_record_summary(record))
            _remember_failed_candidate(
                execution,
                failed_diff_fingerprints=failed_diff_fingerprints,
                failed_source_fingerprints=failed_source_fingerprints,
            )
            if record["outcome"]["status"] == "verified_repair":
                return _trial_result(
                    mode=mode,
                    trial_index=trial_index,
                    trial_id=trial_id,
                    records=records,
                    candidates=candidate_summaries,
                )
        if mode == "rule":
            return _trial_result(
                mode=mode,
                trial_index=trial_index,
                trial_id=trial_id,
                records=records,
                candidates=candidate_summaries,
            )

    if not prepared.editable_regions:
        candidate_index += 1
        candidate_id = _candidate_id(
            prepared.case,
            mode=mode,
            trial_index=trial_index,
            candidate_index=candidate_index,
            label="llm-no-scope",
        )
        execution = _generation_failure_execution("editable_regions_missing")
        record = _record_candidate(
            protocol,
            prepared=prepared,
            mode=mode,
            trial_index=trial_index,
            trial_id=trial_id,
            candidate_index=candidate_index,
            candidate_id=candidate_id,
            generator_family="llm",
            generator_id="llm_direct",
            reflection_round=0,
            parent_candidate_id="",
            prompt_id="patch_generation_v3",
            llm_metadata={},
            execution=execution,
            output_dir=output,
        )
        records.append(record)
        candidate_summaries.append(_record_summary(record))
        return _trial_result(
            mode=mode,
            trial_index=trial_index,
            trial_id=trial_id,
            records=records,
            candidates=candidate_summaries,
        )

    direct_client, client_error = _resolve_client(
        llm_client,
        protocol=protocol,
        project_root=project_root,
        prompt_id="patch_generation_v3",
    )
    candidate_index += 1
    candidate_id = _candidate_id(
        prepared.case,
        mode=mode,
        trial_index=trial_index,
        candidate_index=candidate_index,
        label="llm-direct",
    )
    if client_error is not None:
        execution = provider_blocker_execution(client_error)
        record = _record_candidate(
            protocol,
            prepared=prepared,
            mode=mode,
            trial_index=trial_index,
            trial_id=trial_id,
            candidate_index=candidate_index,
            candidate_id=candidate_id,
            generator_family="llm",
            generator_id="llm_direct",
            reflection_round=0,
            parent_candidate_id="",
            prompt_id="patch_generation_v3",
            llm_metadata=client_error.metadata,
            execution=execution,
            output_dir=output,
        )
        records.append(record)
        candidate_summaries.append(_record_summary(record))
        return _trial_result(
            mode=mode,
            trial_index=trial_index,
            trial_id=trial_id,
            records=records,
            candidates=candidate_summaries,
        )

    prompt = render_v3_model_prompt(prepared.model_context)
    response, request_error = _call_model(direct_client, prompt)
    if request_error is not None:
        execution = provider_blocker_execution(request_error)
        record = _record_candidate(
            protocol,
            prepared=prepared,
            mode=mode,
            trial_index=trial_index,
            trial_id=trial_id,
            candidate_index=candidate_index,
            candidate_id=candidate_id,
            generator_family="llm",
            generator_id="llm_direct",
            reflection_round=0,
            parent_candidate_id="",
            prompt_id="patch_generation_v3",
            llm_metadata=request_error.metadata,
            execution=execution,
            output_dir=output,
        )
        records.append(record)
        candidate_summaries.append(_record_summary(record))
        return _trial_result(
            mode=mode,
            trial_index=trial_index,
            trial_id=trial_id,
            records=records,
            candidates=candidate_summaries,
        )

    parsed = parse_v3_patch_response(
        response.text,
        editable_regions=prepared.editable_regions,
    )
    if parsed["status"] != "pass":
        execution = _generation_failure_execution(str(parsed.get("reason") or ""))
        record = _record_candidate(
            protocol,
            prepared=prepared,
            mode=mode,
            trial_index=trial_index,
            trial_id=trial_id,
            candidate_index=candidate_index,
            candidate_id=candidate_id,
            generator_family="llm",
            generator_id="llm_direct",
            reflection_round=0,
            parent_candidate_id="",
            prompt_id="patch_generation_v3",
            llm_metadata=response.metadata,
            execution=execution,
            output_dir=output,
        )
        records.append(record)
        candidate_summaries.append(_record_summary(record))
        return _trial_result(
            mode=mode,
            trial_index=trial_index,
            trial_id=trial_id,
            records=records,
            candidates=candidate_summaries,
        )

    active_candidate = _dict(parsed.get("candidate"))
    workspace = output / "workspaces" / f"candidate-{candidate_index}"
    execution = execute_v3_patch_candidate(
        active_candidate,
        editable_regions=prepared.editable_regions,
        seed_repository=prepared.seed_repository,
        trial_workspace=workspace,
        case=prepared.case,
        python_executable=python_executable,
        targeted_timeout=targeted_timeout,
        regression_timeout=regression_timeout,
        failed_diff_fingerprints=failed_diff_fingerprints,
        failed_source_fingerprints=failed_source_fingerprints,
    )
    record = _record_candidate(
        protocol,
        prepared=prepared,
        mode=mode,
        trial_index=trial_index,
        trial_id=trial_id,
        candidate_index=candidate_index,
        candidate_id=candidate_id,
        generator_family="llm",
        generator_id="llm_direct",
        reflection_round=0,
        parent_candidate_id="",
        prompt_id="patch_generation_v3",
        llm_metadata=response.metadata,
        execution=execution,
        output_dir=output,
    )
    records.append(record)
    candidate_summaries.append(_record_summary(record))
    if record["outcome"]["status"] == "verified_repair":
        return _trial_result(
            mode=mode,
            trial_index=trial_index,
            trial_id=trial_id,
            records=records,
            candidates=candidate_summaries,
        )
    _remember_failed_candidate(
        execution,
        failed_diff_fingerprints=failed_diff_fingerprints,
        failed_source_fingerprints=failed_source_fingerprints,
    )

    maximum_rounds = _int(
        _dict(protocol.get("randomness")).get("maximum_reflection_rounds"),
        0,
    )
    active_parent_id = candidate_id
    active_execution = execution
    active_candidate_payload = active_candidate
    for reflection_round in range(1, maximum_rounds + 1):
        reflection_context = _build_reflection_context(
            prepared,
            parent_candidate_id=active_parent_id,
            parent_candidate=active_candidate_payload,
            parent_execution=active_execution,
            failed_diff_fingerprints=failed_diff_fingerprints,
        )
        reflection_context_path = (
            output / "contexts" / f"reflection-{reflection_round}.json"
        )
        reflection_context_path.parent.mkdir(parents=True, exist_ok=True)
        reflection_context_path.write_text(
            json.dumps(reflection_context, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        active_reflection_client, reflection_client_error = _resolve_client(
            reflection_client,
            protocol=protocol,
            project_root=project_root,
            prompt_id="reflection_v3",
        )
        candidate_index += 1
        reflection_candidate_id = _candidate_id(
            prepared.case,
            mode=mode,
            trial_index=trial_index,
            candidate_index=candidate_index,
            label=f"llm-reflection-{reflection_round}",
        )
        if reflection_client_error is not None:
            reflection_execution = provider_blocker_execution(
                reflection_client_error
            )
            reflection_record = _record_candidate(
                protocol,
                prepared=prepared,
                mode=mode,
                trial_index=trial_index,
                trial_id=trial_id,
                candidate_index=candidate_index,
                candidate_id=reflection_candidate_id,
                generator_family="llm",
                generator_id="llm_reflection",
                reflection_round=reflection_round,
                parent_candidate_id=active_parent_id,
                prompt_id="reflection_v3",
                llm_metadata=reflection_client_error.metadata,
                execution=reflection_execution,
                output_dir=output,
                model_context_artifact=reflection_context_path.as_posix(),
            )
            records.append(reflection_record)
            candidate_summaries.append(_record_summary(reflection_record))
            break
        reflection_response, reflection_request_error = _call_model(
            active_reflection_client,
            render_v3_model_prompt(reflection_context),
        )
        if reflection_request_error is not None:
            reflection_execution = provider_blocker_execution(
                reflection_request_error
            )
            reflection_record = _record_candidate(
                protocol,
                prepared=prepared,
                mode=mode,
                trial_index=trial_index,
                trial_id=trial_id,
                candidate_index=candidate_index,
                candidate_id=reflection_candidate_id,
                generator_family="llm",
                generator_id="llm_reflection",
                reflection_round=reflection_round,
                parent_candidate_id=active_parent_id,
                prompt_id="reflection_v3",
                llm_metadata=reflection_request_error.metadata,
                execution=reflection_execution,
                output_dir=output,
                model_context_artifact=reflection_context_path.as_posix(),
            )
            records.append(reflection_record)
            candidate_summaries.append(_record_summary(reflection_record))
            break
        reflection_parsed = parse_v3_patch_response(
            reflection_response.text,
            editable_regions=prepared.editable_regions,
        )
        if reflection_parsed["status"] != "pass":
            reflection_execution = _generation_failure_execution(
                str(reflection_parsed.get("reason") or "")
            )
            reflection_record = _record_candidate(
                protocol,
                prepared=prepared,
                mode=mode,
                trial_index=trial_index,
                trial_id=trial_id,
                candidate_index=candidate_index,
                candidate_id=reflection_candidate_id,
                generator_family="llm",
                generator_id="llm_reflection",
                reflection_round=reflection_round,
                parent_candidate_id=active_parent_id,
                prompt_id="reflection_v3",
                llm_metadata=reflection_response.metadata,
                execution=reflection_execution,
                output_dir=output,
                model_context_artifact=reflection_context_path.as_posix(),
            )
            records.append(reflection_record)
            candidate_summaries.append(_record_summary(reflection_record))
            break
        reflected_candidate = _dict(reflection_parsed.get("candidate"))
        reflection_workspace = (
            output / "workspaces" / f"candidate-{candidate_index}"
        )
        reflection_execution = execute_v3_patch_candidate(
            reflected_candidate,
            editable_regions=prepared.editable_regions,
            seed_repository=prepared.seed_repository,
            trial_workspace=reflection_workspace,
            case=prepared.case,
            python_executable=python_executable,
            targeted_timeout=targeted_timeout,
            regression_timeout=regression_timeout,
            failed_diff_fingerprints=failed_diff_fingerprints,
            failed_source_fingerprints=failed_source_fingerprints,
        )
        reflection_record = _record_candidate(
            protocol,
            prepared=prepared,
            mode=mode,
            trial_index=trial_index,
            trial_id=trial_id,
            candidate_index=candidate_index,
            candidate_id=reflection_candidate_id,
            generator_family="llm",
            generator_id="llm_reflection",
            reflection_round=reflection_round,
            parent_candidate_id=active_parent_id,
            prompt_id="reflection_v3",
            llm_metadata=reflection_response.metadata,
            execution=reflection_execution,
            output_dir=output,
            model_context_artifact=reflection_context_path.as_posix(),
        )
        records.append(reflection_record)
        candidate_summaries.append(_record_summary(reflection_record))
        if reflection_record["outcome"]["status"] == "verified_repair":
            break
        _remember_failed_candidate(
            reflection_execution,
            failed_diff_fingerprints=failed_diff_fingerprints,
            failed_source_fingerprints=failed_source_fingerprints,
        )
        active_parent_id = reflection_candidate_id
        active_execution = reflection_execution
        active_candidate_payload = reflected_candidate

    return _trial_result(
        mode=mode,
        trial_index=trial_index,
        trial_id=trial_id,
        records=records,
        candidates=candidate_summaries,
    )


def build_v3_rule_candidates(
    prepared: PreparedV3RepairCase,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    parsed = parse_v3_source_scope(
        prepared.seed_repository,
        analysis_paths=prepared.analysis_scope.get("analysis_paths"),
    )
    functions_by_id = {function.id: function for function in parsed.functions}
    rule_localization = dict(prepared.localization)
    rule_localization["rankings"] = [
        row
        for value in _list(prepared.localization.get("rankings"))
        if (row := _dict(value))
        and not _ranking_targets_test_code(
            prepared.seed_repository,
            row,
            functions_by_id=functions_by_id,
        )
    ]
    payload = build_repository_test_patch_candidates(
        rule_localization,
        repository_root=prepared.seed_repository,
        candidate_limit=max(0, limit),
        patch_generation_mode="rule",
        analysis_paths=prepared.analysis_scope.get("analysis_paths"),
    )
    results: list[dict[str, Any]] = []
    for value in _list(payload.get("candidates"))[: max(0, limit)]:
        row = _dict(value)
        function = functions_by_id.get(str(row.get("target_function_id") or ""))
        if function is None:
            function = _match_rule_function(
                prepared.seed_repository,
                parsed.functions,
                row,
            )
        if function is None:
            continue
        relative_path = _relative_path(
            prepared.seed_repository,
            str(row.get("target_file") or row.get("relative_file_path") or ""),
        )
        if _function_is_test_code(function, relative_path=relative_path):
            continue
        old_source = str(row.get("old_source") or function.source)
        if old_source != function.source:
            continue
        region = EditableRegion(
            path=relative_path,
            function_id=(
                f"{relative_path}::"
                f"{function.metadata.get('qualified_name') or function.name}"
            ),
            function_name=str(
                function.metadata.get("qualified_name") or function.name
            ),
            start_line=function.start_line,
            end_line=function.end_line,
            rank=_rule_rank(prepared.localization, function.id),
            score=_rule_score(prepared.localization, function.id),
            original_sha256=_sha256_text(old_source),
            source=old_source,
        )
        metadata = _dict(row.get("metadata"))
        candidate = {
            "files": [
                {
                    "path": relative_path,
                    "original_sha256": region.original_sha256,
                    "replacement": str(row.get("new_source") or ""),
                    "function_id": region.function_id,
                    "function_name": region.function_name,
                    "start_line": region.start_line,
                    "end_line": region.end_line,
                }
            ],
            "risk": "low",
            "analysis_sha256": _sha256_text(str(row.get("description") or "")),
            "analysis_chars": len(str(row.get("description") or "")),
            "assumption_count": 0,
            "response_sha256": "",
            "allow_signature_change": allow_signature_change_for_rules(
                [str(row.get("rule_id") or "")]
            ),
        }
        results.append(
            {
                "candidate": candidate,
                "editable_region": region,
                "generator_id": (
                    f"rule_based:{row.get('rule_id') or 'unknown'}:"
                    f"{metadata.get('variant') or 'default'}"
                ),
            }
        )
    return results


def _ranking_targets_test_code(
    root: Path,
    ranking: dict[str, Any],
    *,
    functions_by_id: dict[str, CodeEntity],
) -> bool:
    function = functions_by_id.get(str(ranking.get("function_id") or ""))
    relative_path = _relative_path(root, str(ranking.get("file_path") or ""))
    return _function_is_test_code(function, relative_path=relative_path)


def _function_is_test_code(
    function: CodeEntity | None,
    *,
    relative_path: str,
) -> bool:
    if function is not None and (
        function.metadata.get("is_test") or function.metadata.get("is_test_file")
    ):
        return True
    path = PurePosixPath(str(relative_path or "").replace("\\", "/"))
    lowered_parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    return bool(
        lowered_parts.intersection({"test", "tests", "testing", "testdata"})
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "tests.py"
    )


def _record_candidate(
    protocol: dict[str, Any],
    *,
    prepared: PreparedV3RepairCase,
    mode: str,
    trial_index: int,
    trial_id: str,
    candidate_index: int,
    candidate_id: str,
    generator_family: str,
    generator_id: str,
    reflection_round: int,
    parent_candidate_id: str,
    prompt_id: str,
    llm_metadata: dict[str, Any],
    execution: dict[str, Any],
    output_dir: Path,
    model_context_artifact: str = "",
) -> dict[str, Any]:
    started_at = utc_now()
    artifacts = write_v3_candidate_artifacts(
        output_dir / "artifacts",
        candidate_id=candidate_id,
        safety=_dict(execution.get("safety")),
        execution=execution,
    )
    completed_at = utc_now()
    return build_v3_run_record(
        protocol,
        case=prepared.case,
        strategy_mode=mode,
        trial_index=trial_index,
        trial_id=trial_id,
        candidate_index=candidate_index,
        candidate_id=candidate_id,
        generator_family=generator_family,
        generator_id=generator_id,
        reflection_round=reflection_round,
        parent_candidate_id=parent_candidate_id,
        prompt_id=prompt_id,
        llm_metadata=llm_metadata,
        execution=execution,
        model_context_artifact=(
            model_context_artifact or prepared.model_context_artifact
        ),
        artifacts=artifacts,
        started_at=started_at,
        completed_at=completed_at,
    )


def _resolve_client(
    client: LLMClient | None,
    *,
    protocol: dict[str, Any],
    project_root: str | Path,
    prompt_id: str,
) -> tuple[LLMClient | None, LLMRequestError | None]:
    if client is not None:
        return client, None
    try:
        return (
            create_v3_repair_client(
                protocol,
                root=project_root,
                prompt_id=prompt_id,
            ),
            None,
        )
    except ValueError as exc:
        return None, LLMRequestError(
            "configuration_error",
            "V3 model client configuration is incomplete.",
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "error_reason": "missing_or_invalid_model_configuration",
                "provider_retry_count": 0,
                "provider_retry_reasons": [],
            },
        )


def _call_model(
    client: LLMClient | None,
    prompt: str,
) -> tuple[Any | None, LLMRequestError | None]:
    if client is None:
        return None, LLMRequestError(
            "configuration_error",
            "V3 model client is unavailable.",
            {
                "status": "error",
                "error_reason": "model_client_unavailable",
                "provider_retry_count": 0,
                "provider_retry_reasons": [],
            },
        )
    try:
        return client.complete(prompt), None
    except LLMRequestError as exc:
        return None, exc


def _build_reflection_context(
    prepared: PreparedV3RepairCase,
    *,
    parent_candidate_id: str,
    parent_candidate: dict[str, Any],
    parent_execution: dict[str, Any],
    failed_diff_fingerprints: set[str],
) -> dict[str, Any]:
    context = json.loads(json.dumps(prepared.model_context))
    workspace = str(_dict(parent_execution.get("workspace")).get("workspace") or "")
    context["task"] = "revise_failed_python_source_repair"
    context["reflection"] = {
        "parent_candidate_id": parent_candidate_id,
        "parent_patch": {
            "files": [
                {
                    "path": str(_dict(item).get("path") or ""),
                    "original_sha256": str(
                        _dict(item).get("original_sha256") or ""
                    ),
                    "replacement": str(_dict(item).get("replacement") or ""),
                }
                for item in _list(parent_candidate.get("files"))
            ]
        },
        "validation": _dict(parent_execution.get("validation")),
        "safety_reasons": [
            str(item)
            for item in _list(
                _dict(parent_execution.get("safety")).get("reasons")
            )
        ],
        "targeted_test": _compact_test_group(
            _dict(parent_execution.get("targeted")),
            roots=[prepared.seed_repository, Path(workspace) if workspace else None],
        ),
        "full_regression": _compact_test_group(
            _dict(parent_execution.get("regression")),
            roots=[prepared.seed_repository, Path(workspace) if workspace else None],
        ),
        "failed_diff_fingerprints": sorted(failed_diff_fingerprints),
        "history_scope": "current_trial_only",
    }
    audit = audit_v3_model_context(
        context,
        case=prepared.case,
        repository_root=prepared.seed_repository,
    )
    if audit["status"] != "pass":
        raise ValueError("Unsafe V3 reflection context: " + ",".join(audit["errors"]))
    return context


def _compact_test_group(
    group: dict[str, Any],
    *,
    roots: list[Path | None],
) -> dict[str, Any]:
    rows = []
    for value in _list(group.get("results"))[:3]:
        row = _dict(value)
        rows.append(
            {
                "status": str(row.get("status") or ""),
                "returncode": row.get("returncode"),
                "failure_category": str(row.get("failure_category") or ""),
                "failure_signal": sanitize_v3_untrusted_text(
                    str(row.get("failure_signal") or ""),
                    repository_roots=roots,
                    limit=2_000,
                ),
                "diagnostic_summary": sanitize_v3_untrusted_text(
                    str(row.get("diagnostic_summary") or ""),
                    repository_roots=roots,
                    limit=4_000,
                ),
                "failure_context": sanitize_v3_untrusted_text(
                    str(row.get("failure_context") or ""),
                    repository_roots=roots,
                    limit=12_000,
                ),
            }
        )
    return {
        "status": str(group.get("status") or ""),
        "reason": str(group.get("reason") or ""),
        "environment_blocker": bool(group.get("environment_blocker", False)),
        "results": rows,
    }


def _generation_failure_execution(reason: str) -> dict[str, Any]:
    normalized_reason = reason or "candidate_generation_failed"
    return {
        "validation": {
            "ast_valid": None,
            "safety_gate": "not_run",
            "targeted_tests": "not_run",
            "full_regression": "not_run",
            "semantic_validation": "not_run",
            "semantic_justification": "No executable patch candidate was generated.",
        },
        "outcome_status": "failed",
        "failure_layer": "generation",
        "failure_category": normalized_reason,
        "failure_reason": normalized_reason,
        "validation_latency_ms": 0.0,
        "safety": {},
        "application": {"status": "not_run", "reason": normalized_reason},
        "targeted": {"status": "not_run", "reason": normalized_reason},
        "regression": {"status": "not_run", "reason": normalized_reason},
    }


def _remember_failed_candidate(
    execution: dict[str, Any],
    *,
    failed_diff_fingerprints: set[str],
    failed_source_fingerprints: set[str],
) -> None:
    safety = _dict(execution.get("safety"))
    fingerprint = str(safety.get("combined_diff_fingerprint") or "")
    if fingerprint:
        failed_diff_fingerprints.add(fingerprint)
    for value in _list(safety.get("files")):
        row = _dict(value)
        source_fingerprint = str(row.get("source_fingerprint") or "")
        if source_fingerprint:
            failed_source_fingerprints.add(source_fingerprint)


def _trial_result(
    *,
    mode: str,
    trial_index: int,
    trial_id: str,
    records: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    verified_records = [
        record
        for record in records
        if _dict(record.get("outcome")).get("status") == "verified_repair"
    ]
    return {
        "schema_version": "3.0",
        "strategy_mode": mode,
        "trial_index": trial_index,
        "trial_id": trial_id,
        "status": "pass" if verified_records else "fail",
        "verified_repair": bool(verified_records),
        "winning_run_id": (
            str(verified_records[0].get("run_id") or "") if verified_records else ""
        ),
        "record_count": len(records),
        "records": records,
        "candidates": candidates,
    }


def _record_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(record.get("run_id") or ""),
        "candidate_id": str(_dict(record.get("candidate")).get("candidate_id") or ""),
        "generator_family": str(
            _dict(record.get("candidate")).get("generator_family") or ""
        ),
        "generator_id": str(
            _dict(record.get("candidate")).get("generator_id") or ""
        ),
        "reflection_round": _int(
            _dict(record.get("candidate")).get("reflection_round"), 0
        ),
        "outcome": str(_dict(record.get("outcome")).get("status") or ""),
        "failure_layer": str(_dict(record.get("failure")).get("layer") or ""),
        "failure_category": str(
            _dict(record.get("failure")).get("category") or ""
        ),
    }


def _candidate_id(
    case: dict[str, Any],
    *,
    mode: str,
    trial_index: int,
    candidate_index: int,
    label: str,
) -> str:
    return (
        f"{case.get('case_id')}-{mode}-t{trial_index}-"
        f"c{candidate_index}-{label}"
    )


def _match_rule_function(
    root: Path,
    functions: list[CodeEntity],
    candidate: dict[str, Any],
) -> CodeEntity | None:
    relative = _relative_path(
        root,
        str(candidate.get("target_file") or candidate.get("relative_file_path") or ""),
    )
    target_name = str(candidate.get("target_function_name") or "")
    old_source = str(candidate.get("old_source") or "")
    for function in functions:
        function_relative = _relative_path(root, function.file_path)
        qualified_name = str(function.metadata.get("qualified_name") or function.name)
        if (
            function_relative == relative
            and qualified_name == target_name
            and function.source == old_source
        ):
            return function
    return None


def _rule_rank(localization: dict[str, Any], function_id: str) -> int:
    for value in _list(localization.get("rankings")):
        row = _dict(value)
        if str(row.get("function_id") or "") == function_id:
            return _int(row.get("rank"), 0)
    return 0


def _rule_score(localization: dict[str, Any], function_id: str) -> float:
    for value in _list(localization.get("rankings")):
        row = _dict(value)
        if str(row.get("function_id") or "") == function_id:
            return _float(row.get("score"), 0.0)
    return 0.0


def _relative_path(root: Path, value: str) -> str:
    path = Path(value)
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return PurePosixPath(value.replace("\\", "/")).as_posix()


def _normalized_audit_path(value: str) -> str:
    path = PurePosixPath(str(value or "").replace("\\", "/"))
    if not value or path.is_absolute() or ".." in path.parts:
        return ""
    return path.as_posix()


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator > 0 else 0.0


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
