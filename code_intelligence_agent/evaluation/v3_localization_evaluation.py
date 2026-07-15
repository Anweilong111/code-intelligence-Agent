from __future__ import annotations

import argparse
import hashlib
import json
import time
import warnings
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from code_intelligence_agent.agents.bug_detector import RuleBasedBugDetector
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.fault_localizer import (
    FaultLocalizer,
    ScoreWeights,
    evidence_v2_localization_config,
    score_contributions,
    score_with_weights,
)
from code_intelligence_agent.core.git_change_history import GitChangeHistoryAnalyzer
from code_intelligence_agent.core.models import TestExecutionSummary
from code_intelligence_agent.core.program_graph import build_program_graph
from code_intelligence_agent.core.repo_parser import is_default_excluded_repo_path
from code_intelligence_agent.evaluation.metrics import (
    LocalizationRun,
    mean_average_precision,
    mean_exam_score,
    mean_ndcg,
    mean_reciprocal_rank,
    top_k_accuracy,
)
from code_intelligence_agent.evaluation.repository_test_dynamic_evidence import (
    build_repository_test_dynamic_evidence,
)
from code_intelligence_agent.evaluation.repository_test_fault_localization import (
    dynamic_evidence_to_test_summary,
)
from code_intelligence_agent.evaluation.v3_localization_ground_truth import (
    resolve_v3_localization_ground_truth,
)
from code_intelligence_agent.evaluation.v3_repair_evaluation import (
    audit_v3_reproduction_seed,
    resolve_v3_case_runtime,
)
from code_intelligence_agent.evaluation.v3_repair_scope import (
    select_v3_analysis_scope,
)
from code_intelligence_agent.evaluation.v3_repair_trial import parse_v3_source_scope
from code_intelligence_agent.tools.coverage_runner import CoverageRunner


SCHEMA_VERSION = "v3_localization_evaluation_v1"
SIGNAL_EXTRACTION_VERSION = "v3_localization_signals_1.3.0"
PRIMARY_SIGNALS = (
    "sbfl",
    "graph",
    "static",
    "semantic",
    "llm",
    "risk",
    "test_failure",
    "traceback",
    "complexity",
    "change_history",
)
EVALUATED_VARIANTS = (
    "rule_only",
    "graph_only",
    "dynamic_only",
    "semantic_only",
    "fusion",
    "without_rule",
    "without_graph",
    "without_dynamic",
    "without_semantic",
    "without_auxiliary",
)
DIFFICULTY_SUBSETS = (
    "static_negative",
    "cross_function",
    "data_flow",
    "separated_failure_site",
    "high_similarity_candidates",
    "multi_file",
)
WEIGHT_SEARCH_OBJECTIVE = (
    "0.25*MAP + 0.25*MRR + 0.20*nDCG@3 + 0.15*Top1 + "
    "0.10*Top3 + 0.05*(1-EXAM)"
)


def run_v3_localization_evaluation(
    *,
    project_root: str | Path,
    catalog_path: str | Path,
    environment_profiles_path: str | Path,
    reproduction_root: str | Path,
    output_dir: str | Path,
    case_ids: list[str] | None = None,
    max_cases: int = 0,
    coverage_timeout: int = 120,
    use_runtime_coverage: bool = True,
    resume: bool = True,
    ranking_artifact_limit: int = 50,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    catalog = _read_json(catalog_path)
    profiles = _read_json(environment_profiles_path)
    profile_by_id = {
        str(_dict(item).get("profile_id") or ""): _dict(item)
        for item in _list(profiles.get("profiles"))
    }
    requested = {str(value) for value in (case_ids or []) if str(value)}
    cases = [
        _dict(case)
        for case in _list(catalog.get("cases"))
        if str(_dict(case).get("status") or "") == "accepted"
        and (
            not requested
            or str(_dict(case).get("case_id") or "") in requested
        )
    ]
    cases.sort(key=lambda case: str(case.get("case_id") or ""))
    if max_cases > 0:
        cases = cases[:max_cases]

    started = _utc_timestamp()
    prepared_cases: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case.get("case_id") or "")
        case_output = output / "cases" / case_id
        case_output.mkdir(parents=True, exist_ok=True)
        try:
            prepared = _prepare_localization_case(
                project_root=root,
                case=case,
                profile_by_id=profile_by_id,
                reproduction_root=Path(reproduction_root),
                output_dir=case_output,
                coverage_timeout=max(1, coverage_timeout),
                use_runtime_coverage=use_runtime_coverage,
                resume=resume,
            )
        except Exception as exc:  # Preserve the benchmark denominator and failure type.
            blocker = {
                "case_id": case_id,
                "split": str(case.get("benchmark_split") or ""),
                "status": "blocker",
                "reason": f"{type(exc).__name__}:{exc}",
            }
            blockers.append(blocker)
            prepared_cases.append(blocker)
            continue
        prepared_cases.append(prepared)

    ready = [case for case in prepared_cases if case.get("status") == "ready"]
    validation_cases = [
        case for case in ready if case.get("split") == "validation"
    ]
    test_cases = [case for case in ready if case.get("split") == "test"]
    development_cases = [
        case for case in ready if case.get("split") == "development"
    ]
    selection = select_v3_localization_profile(
        validation_cases,
        test_case_ids=[str(case.get("case_id") or "") for case in test_cases],
    )
    selected_weights = _weights_from_dict(selection["selected_profile"]["weights"])
    variants = build_v3_localization_variants(selected_weights)

    metrics_by_split: dict[str, dict[str, Any]] = {}
    case_results: list[dict[str, Any]] = []
    for split, split_cases in (
        ("development", development_cases),
        ("validation", validation_cases),
        ("test", test_cases),
    ):
        split_metrics: dict[str, Any] = {}
        for variant_name, weights in variants.items():
            rankings, rerank_latency = _rank_cases(split_cases, weights)
            split_metrics[variant_name] = build_v3_localization_metrics(
                split_cases,
                rankings,
                rerank_latency_ms=rerank_latency,
            )
        split_metrics["llm_only"] = _not_applicable_llm_metrics(len(split_cases))
        metrics_by_split[split] = split_metrics

    for case in ready:
        variant_rows: dict[str, Any] = {}
        full_rankings: dict[str, list[dict[str, Any]]] = {}
        for variant_name, weights in variants.items():
            ranked = rerank_v3_signal_rows(case["raw_rows"], weights)
            full_rankings[variant_name] = ranked
            variant_rows[variant_name] = {
                "weights": weights.to_dict(),
                "ranking_count": len(ranked),
                "top_rankings": ranked[: max(1, ranking_artifact_limit)],
                "score_reconstruction_pass": all(
                    abs(float(row["score_reconstruction_error"])) <= 1e-9
                    for row in ranked
                ),
                "case_metrics": _single_case_metrics(case, ranked),
            }
        ranking_payload = {
            "schema_version": SCHEMA_VERSION,
            "signal_extraction_version": SIGNAL_EXTRACTION_VERSION,
            "case_id": case["case_id"],
            "ranking_snapshot_sha256": case["ranking_snapshot_sha256"],
            "selected_profile_sha256": selection["selected_profile_sha256"],
            "artifact_top_k": max(1, ranking_artifact_limit),
            "variants": variant_rows,
            "llm_only": {
                "status": "not_applicable",
                "reason": "no_live_llm_localization_scorer_configured",
                "semantic_only_is_not_llm_only": True,
            },
        }
        ranking_path = Path(case["case_output_dir"]) / "variant_rankings.json"
        _write_json(ranking_path, ranking_payload)
        fusion_ranking = full_rankings["fusion"]
        case_results.append(
            {
                "case_id": case["case_id"],
                "split": case["split"],
                "repository": case["repository"],
                "difficulty_tags": case["difficulty_tags"],
                "function_rankable": case["ground_truth"]["function_rankable"],
                "function_ground_truth_count": case["ground_truth"][
                    "function_ground_truth_count"
                ],
                "file_ground_truth_count": case["ground_truth"][
                    "file_ground_truth_count"
                ],
                "coverage": case["coverage_summary"],
                "analysis_scope": case["analysis_scope"],
                "signal_extraction_latency_ms": case[
                    "signal_extraction_latency_ms"
                ],
                "score_reconstruction_pass": all(
                    bool(value.get("score_reconstruction_pass"))
                    for value in variant_rows.values()
                ),
                "fusion_metrics": _single_case_metrics(case, fusion_ranking),
                "artifacts": {
                    "raw_signal_matrix": case["raw_signal_matrix_path"],
                    "ground_truth": case["ground_truth_path"],
                    "variant_rankings": ranking_path.as_posix(),
                },
            }
        )

    test_rankings, test_rerank_latency = _rank_cases(test_cases, selected_weights)
    subset_metrics = {
        tag: build_v3_localization_metrics(
            [case for case in test_cases if tag in case["difficulty_tags"]],
            {
                case_id: ranking
                for case_id, ranking in test_rankings.items()
                if tag
                in next(
                    case["difficulty_tags"]
                    for case in test_cases
                    if case["case_id"] == case_id
                )
            },
            rerank_latency_ms=test_rerank_latency,
        )
        for tag in DIFFICULTY_SUBSETS
    }
    test_metrics = metrics_by_split.get("test", {})
    ablation_analysis = build_v3_ablation_analysis(
        test_metrics,
        selected_weights=selected_weights,
    )
    failure_analysis = _build_failure_analysis(
        ready_cases=ready,
        case_results=case_results,
        test_subset_metrics=subset_metrics,
        selected_weights=selected_weights,
        ablation_analysis=ablation_analysis,
    )
    coverage_summary = _aggregate_coverage(ready)
    artifact_audit = audit_v3_localization_artifacts(
        ready_cases=ready,
        case_results=case_results,
        selection=selection,
        runtime_coverage_required=use_runtime_coverage,
    )
    status = (
        "pass"
        if ready
        and validation_cases
        and test_cases
        and not blockers
        and artifact_audit["status"] == "pass"
        else "warning"
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "signal_extraction_version": SIGNAL_EXTRACTION_VERSION,
        "status": status,
        "started_at": started,
        "completed_at": _utc_timestamp(),
        "case_count": len(cases),
        "ready_case_count": len(ready),
        "blocker_count": len(blockers),
        "split_case_counts": {
            "development": len(development_cases),
            "validation": len(validation_cases),
            "test": len(test_cases),
        },
        "catalog_path": Path(catalog_path).resolve().as_posix(),
        "catalog_sha256": str(catalog.get("catalog_sha256") or ""),
        "selection": selection,
        "variants": {
            name: {"status": "evaluated", "weights": weights.to_dict()}
            for name, weights in variants.items()
        }
        | {
            "llm_only": {
                "status": "not_applicable",
                "reason": "no_live_llm_localization_scorer_configured",
            }
        },
        "metrics_by_split": metrics_by_split,
        "test_difficulty_subset_metrics": subset_metrics,
        "ablation_analysis": ablation_analysis,
        "failure_analysis": failure_analysis,
        "coverage_summary": coverage_summary,
        "artifact_audit": artifact_audit,
        "case_results": case_results,
        "blockers": blockers,
        "protocol_audit": {
            "weight_selection_scope": "validation_only",
            "test_weights_frozen": True,
            "test_ground_truth_accessed_during_weight_search": False,
            "ground_truth_used_for_signal_extraction": False,
            "ground_truth_resolved_after_ranking_snapshot": True,
            "runtime_coverage_requested": use_runtime_coverage,
            "semantic_signal_kind": "deterministic_lexical_similarity",
            "llm_signal_available": False,
            "weight_search_objective": WEIGHT_SEARCH_OBJECTIVE,
        },
        "boundary": (
            "This evaluation measures fault localization only. Semantic-only is a "
            "deterministic lexical signal and is not reported as an LLM result."
        ),
    }
    write_v3_localization_evaluation_artifacts(result, output)
    return result


def _prepare_localization_case(
    *,
    project_root: Path,
    case: dict[str, Any],
    profile_by_id: dict[str, dict[str, Any]],
    reproduction_root: Path,
    output_dir: Path,
    coverage_timeout: int,
    use_runtime_coverage: bool,
    resume: bool,
) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "")
    reproduction_dir = reproduction_root / case_id
    seed_audit = audit_v3_reproduction_seed(
        case,
        reproduction_dir=reproduction_dir,
    )
    if seed_audit["status"] != "pass":
        raise ValueError("reproduction_seed_audit_failed:" + ",".join(seed_audit["errors"]))
    runtime = resolve_v3_case_runtime(
        project_root,
        case,
        profile_by_id=profile_by_id,
    )
    if runtime["status"] != "pass":
        raise ValueError("runtime_resolution_failed:" + str(runtime.get("reason") or ""))
    reproduction = _read_json(seed_audit["reproduction_artifact"])
    baseline_execution = _first_baseline_execution(reproduction)
    if not baseline_execution:
        raise ValueError("validated_bug_targeted_execution_missing")
    seed = Path(seed_audit["seed_repository"]).resolve()
    fix = (reproduction_dir / "fix" / "repository_checkout").resolve()
    if not fix.is_dir() or fix.is_symlink():
        raise ValueError("fix_repository_missing_or_unsafe")
    fingerprint = _case_signal_fingerprint(
        case=case,
        seed=seed,
        seed_audit=seed_audit,
        runtime=runtime,
        use_runtime_coverage=use_runtime_coverage,
        coverage_timeout=coverage_timeout,
    )
    raw_path = output_dir / "raw_signal_matrix.json"
    cached = {}
    if resume and raw_path.is_file():
        try:
            cached = _read_json(raw_path)
        except (OSError, ValueError, json.JSONDecodeError):
            cached = {}
    if str(cached.get("input_fingerprint") or "") == fingerprint:
        raw_payload = cached
    else:
        raw_payload = _extract_case_signal_matrix(
            case=case,
            seed=seed,
            runtime=runtime,
            baseline_execution=baseline_execution,
            coverage_timeout=coverage_timeout,
            use_runtime_coverage=use_runtime_coverage,
            input_fingerprint=fingerprint,
        )
        _write_json(raw_path, raw_payload)
    ranking_snapshot_sha256 = str(raw_payload.get("ranking_snapshot_sha256") or "")
    if not ranking_snapshot_sha256:
        raise ValueError("raw_signal_matrix_missing_ranking_snapshot")
    ground_truth = resolve_v3_localization_ground_truth(
        case_id=case_id,
        bug_repository=seed,
        fix_repository=fix,
        source_files=_list(_dict(case.get("ground_truth")).get("source_files")),
        ranking_snapshot_sha256=ranking_snapshot_sha256,
    )
    ground_truth_path = output_dir / "ground_truth.json"
    _write_json(ground_truth_path, ground_truth)
    return {
        "status": "ready",
        "case_id": case_id,
        "split": str(case.get("benchmark_split") or ""),
        "repository": str(_dict(case.get("repository")).get("owner_repo") or ""),
        "difficulty_tags": [str(value) for value in _list(case.get("difficulty_tags"))],
        "raw_rows": _list(raw_payload.get("rows")),
        "ground_truth": ground_truth,
        "coverage_summary": _dict(raw_payload.get("coverage_summary")),
        "analysis_scope": _dict(raw_payload.get("analysis_scope")),
        "signal_extraction_latency_ms": _float(
            raw_payload.get("signal_extraction_latency_ms"), 0.0
        ),
        "ranking_snapshot_sha256": ranking_snapshot_sha256,
        "raw_signal_matrix_path": raw_path.as_posix(),
        "ground_truth_path": ground_truth_path.as_posix(),
        "case_output_dir": output_dir.as_posix(),
    }


def _extract_case_signal_matrix(
    *,
    case: dict[str, Any],
    seed: Path,
    runtime: dict[str, Any],
    baseline_execution: dict[str, Any],
    coverage_timeout: int,
    use_runtime_coverage: bool,
    input_fingerprint: str,
) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return _extract_case_signal_matrix_impl(
            case=case,
            seed=seed,
            runtime=runtime,
            baseline_execution=baseline_execution,
            coverage_timeout=coverage_timeout,
            use_runtime_coverage=use_runtime_coverage,
            input_fingerprint=input_fingerprint,
        )


def _extract_case_signal_matrix_impl(
    *,
    case: dict[str, Any],
    seed: Path,
    runtime: dict[str, Any],
    baseline_execution: dict[str, Any],
    coverage_timeout: int,
    use_runtime_coverage: bool,
    input_fingerprint: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    dynamic = build_repository_test_dynamic_evidence(baseline_execution)
    analysis_scope = select_v3_analysis_scope(
        seed,
        case=case,
        dynamic_evidence=dynamic,
    )
    analysis_paths = analysis_scope.get("analysis_paths")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        parsed = parse_v3_source_scope(seed, analysis_paths=analysis_paths)
    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    findings = RuleBasedBugDetector().detect(parsed.functions)
    baseline_summary, dynamic_metadata = dynamic_evidence_to_test_summary(
        program_graph,
        dynamic,
    )
    coverage_results: list[dict[str, Any]] = []
    runtime_results = []
    coverage_started = time.perf_counter()
    if use_runtime_coverage:
        runner = CoverageRunner(
            timeout=coverage_timeout,
            python_executable=str(runtime.get("python_executable") or ""),
            environment={
                str(key): str(value)
                for key, value in _dict(case.get("test_environment")).items()
            },
        )
        for index, raw_command in enumerate(
            _list(case.get("targeted_test_commands")),
            start=1,
        ):
            command = [
                str(runtime.get("python_executable") or "")
                if str(part) == "{python}"
                else str(part)
                for part in _list(raw_command)
            ]
            test_id = f"runtime::{case.get('case_id')}::command-{index}"
            label = _test_command_label(command, index)
            try:
                result = runner.run_command_coverage(
                    seed,
                    parsed.functions,
                    command,
                    test_name=label,
                    test_id=test_id,
                )
            except (OSError, ValueError) as exc:
                coverage_results.append(
                    {
                        "test_id": test_id,
                        "command": command,
                        "status": "blocker",
                        "reason": f"{type(exc).__name__}:{exc}",
                    }
                )
                continue
            runtime_results.append(result)
            coverage_results.append(_coverage_result_payload(result, command))
    coverage_latency_ms = round((time.perf_counter() - coverage_started) * 1000, 4)
    test_summary = _merge_runtime_coverage(baseline_summary, runtime_results)
    history = GitChangeHistoryAnalyzer(max_files=12, timeout_seconds=2.0).analyze(
        seed,
        parsed.functions,
    )
    localizer = FaultLocalizer(evidence_v2_localization_config())
    ranking_started = time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        ranked = localizer.rank(
            program_graph,
            findings,
            test_summary,
            change_history_scores=history.scores,
        )
    ranking_latency_ms = round((time.perf_counter() - ranking_started) * 1000, 4)
    rows = [_portable_raw_signal_row(item.to_dict(), seed) for item in ranked]
    rows = [row for row in rows if row]
    snapshot = {
        "case_id": str(case.get("case_id") or ""),
        "analysis_scope": analysis_scope,
        "rows": rows,
        "ground_truth_included": False,
    }
    ranking_snapshot_sha256 = _sha256_json(snapshot)
    real_failed_coverage = [
        result
        for result in runtime_results
        if not result.success and result.covered_function_ids
    ]
    real_any_coverage = [
        result for result in runtime_results if result.covered_function_ids
    ]
    return {
        "schema_version": "v3_localization_raw_signal_matrix_v1",
        "signal_extraction_version": SIGNAL_EXTRACTION_VERSION,
        "case_id": str(case.get("case_id") or ""),
        "split": str(case.get("benchmark_split") or ""),
        "input_fingerprint": input_fingerprint,
        "ranking_snapshot_sha256": ranking_snapshot_sha256,
        "ground_truth_included": False,
        "ground_truth_used_for_scope": False,
        "ground_truth_used_for_signal_extraction": False,
        "analysis_scope": analysis_scope,
        "dynamic_evidence_metadata": dynamic_metadata,
        "coverage_summary": {
            "requested": use_runtime_coverage,
            "command_count": len(_list(case.get("targeted_test_commands"))),
            "executed_command_count": len(runtime_results),
            "covered_command_count": len(real_any_coverage),
            "failing_covered_command_count": len(real_failed_coverage),
            "real_runtime_coverage_available": bool(real_any_coverage),
            "real_failing_runtime_coverage_available": bool(real_failed_coverage),
            "coverage_kind": "sys_settrace_line_and_call_events",
            "branch_and_path_kind": "inferred_from_line_and_call_events",
            "latency_ms": coverage_latency_ms,
            "commands": coverage_results,
        },
        "parsed_function_count": len(parsed.functions),
        "candidate_function_count": len(rows),
        "static_finding_count": len(findings),
        "ranking_latency_ms": ranking_latency_ms,
        "signal_extraction_latency_ms": round(
            (time.perf_counter() - started) * 1000,
            4,
        ),
        "raw_signal_contract": list(PRIMARY_SIGNALS),
        "llm_signal_available": False,
        "rows": rows,
    }


def build_v3_localization_weight_profiles(
    *,
    units: int = 4,
) -> list[dict[str, Any]]:
    """Build a deterministic coarse simplex over signal families.

    The five searched families are static, graph, dynamic, semantic, and
    auxiliary complexity/history. LLM is fixed to zero because no live
    localization scorer participates in this experiment.
    """
    if units < 1:
        raise ValueError("units must be positive")
    profiles: list[dict[str, Any]] = []
    seen: set[tuple[float, ...]] = set()
    for static_units in range(units + 1):
        for graph_units in range(units - static_units + 1):
            for dynamic_units in range(units - static_units - graph_units + 1):
                for semantic_units in range(
                    units - static_units - graph_units - dynamic_units + 1
                ):
                    auxiliary_units = (
                        units
                        - static_units
                        - graph_units
                        - dynamic_units
                        - semantic_units
                    )
                    allocations = (
                        static_units / units,
                        graph_units / units,
                        dynamic_units / units,
                        semantic_units / units,
                        auxiliary_units / units,
                    )
                    for risk in (0.0, 0.05):
                        static, graph, dynamic, semantic, auxiliary = allocations
                        weights = ScoreWeights(
                            sbfl=round(dynamic * 0.45, 6),
                            graph=round(graph, 6),
                            static=round(static, 6),
                            semantic=round(semantic, 6),
                            llm=0.0,
                            risk=risk,
                            test_failure=round(dynamic * 0.35, 6),
                            traceback=round(dynamic * 0.20, 6),
                            complexity=round(auxiliary * 0.50, 6),
                            change_history=round(auxiliary * 0.50, 6),
                        )
                        key = _weights_key(weights)
                        if key in seen:
                            continue
                        seen.add(key)
                        profiles.append(
                            {
                                "name": f"simplex-{len(profiles) + 1:03d}",
                                "weights": weights,
                            }
                        )
    default = replace(evidence_v2_localization_config().coverage_weights, llm=0.0)
    if _weights_key(default) not in seen:
        profiles.append({"name": "evidence-v2-default-no-llm", "weights": default})
    return profiles


def select_v3_localization_profile(
    validation_cases: list[dict[str, Any]],
    *,
    test_case_ids: list[str] | None = None,
    profiles: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if any(str(case.get("split") or "") != "validation" for case in validation_cases):
        raise ValueError("Weight search accepts validation cases only")
    rankable_cases = [
        case
        for case in validation_cases
        if _dict(case.get("ground_truth")).get("function_rankable") is True
    ]
    if not rankable_cases:
        raise ValueError("No function-rankable validation case is available")
    candidate_profiles = list(profiles or build_v3_localization_weight_profiles())
    results = []
    for profile in candidate_profiles:
        weights = profile.get("weights")
        if not isinstance(weights, ScoreWeights):
            raise ValueError("Weight profile must contain ScoreWeights")
        rankings, latency = _rank_cases(rankable_cases, weights)
        metrics = build_v3_localization_metrics(
            rankable_cases,
            rankings,
            rerank_latency_ms=latency,
        )
        function_metrics = _dict(metrics.get("function"))
        global_score = _objective(function_metrics)
        group_scores = []
        for repository in sorted({str(case.get("repository") or "") for case in rankable_cases}):
            group_cases = [
                case for case in rankable_cases if case.get("repository") == repository
            ]
            group_metrics = build_v3_localization_metrics(
                group_cases,
                {case["case_id"]: rankings[case["case_id"]] for case in group_cases},
            )
            group_scores.append(
                {
                    "repository": repository,
                    "case_count": len(group_cases),
                    "objective": _objective(_dict(group_metrics.get("function"))),
                }
            )
        robust_score = min(
            [global_score, *[float(group["objective"]) for group in group_scores]]
        )
        results.append(
            {
                "name": str(profile.get("name") or ""),
                "weights": weights.to_dict(),
                "validation_objective": round(global_score, 8),
                "robust_validation_objective": round(robust_score, 8),
                "function_metrics": function_metrics,
                "repository_groups": group_scores,
                "nonzero_weight_count": sum(
                    1 for value in weights.to_dict().values() if float(value) != 0.0
                ),
            }
        )
    results.sort(
        key=lambda item: (
            -float(item["robust_validation_objective"]),
            -float(item["validation_objective"]),
            -float(_dict(item["function_metrics"]).get("map", 0.0)),
            -float(_dict(item["function_metrics"]).get("mrr", 0.0)),
            -float(_dict(item["function_metrics"]).get("top1", 0.0)),
            int(item["nonzero_weight_count"]),
            str(item["name"]),
        )
    )
    selected = results[0]
    selected_hash = _sha256_json(
        {
            "name": selected["name"],
            "weights": selected["weights"],
            "validation_case_ids": sorted(case["case_id"] for case in rankable_cases),
            "objective": WEIGHT_SEARCH_OBJECTIVE,
        }
    )
    return {
        "scope": "validation_only",
        "candidate_profile_count": len(results),
        "validation_case_count": len(validation_cases),
        "function_rankable_validation_case_count": len(rankable_cases),
        "validation_case_ids": sorted(case["case_id"] for case in validation_cases),
        "excluded_unrankable_validation_case_ids": sorted(
            case["case_id"] for case in validation_cases if case not in rankable_cases
        ),
        "test_case_ids_not_supplied_to_search": sorted(test_case_ids or []),
        "test_ground_truth_accessed": False,
        "objective": WEIGHT_SEARCH_OBJECTIVE,
        "tie_break_policy": (
            "robust_objective,global_objective,MAP,MRR,Top1,sparser_profile,name"
        ),
        "selected_profile": selected,
        "selected_profile_sha256": selected_hash,
        "top_candidates": results[:20],
    }


def build_v3_localization_variants(
    selected_weights: ScoreWeights,
) -> dict[str, ScoreWeights]:
    zero = ScoreWeights(
        sbfl=0.0,
        graph=0.0,
        static=0.0,
        semantic=0.0,
        llm=0.0,
        risk=0.0,
        test_failure=0.0,
        traceback=0.0,
        complexity=0.0,
        change_history=0.0,
    )
    return {
        "rule_only": replace(zero, static=1.0),
        "graph_only": replace(zero, graph=1.0),
        "dynamic_only": replace(
            zero,
            sbfl=0.45,
            test_failure=0.35,
            traceback=0.20,
        ),
        "semantic_only": replace(zero, semantic=1.0),
        "fusion": selected_weights,
        "without_rule": replace(selected_weights, static=0.0),
        "without_graph": replace(selected_weights, graph=0.0),
        "without_dynamic": replace(
            selected_weights,
            sbfl=0.0,
            test_failure=0.0,
            traceback=0.0,
        ),
        "without_semantic": replace(selected_weights, semantic=0.0),
        "without_auxiliary": replace(
            selected_weights,
            complexity=0.0,
            change_history=0.0,
        ),
    }


def build_v3_ablation_analysis(
    test_metrics: dict[str, Any],
    *,
    selected_weights: ScoreWeights,
) -> dict[str, Any]:
    fusion = _dict(test_metrics.get("fusion"))
    fusion_function = _dict(fusion.get("function"))
    fusion_file = _dict(fusion.get("file"))
    variants = {
        "rule": "without_rule",
        "graph": "without_graph",
        "dynamic": "without_dynamic",
        "semantic": "without_semantic",
        "auxiliary": "without_auxiliary",
    }
    family_weights = {
        "rule": selected_weights.static,
        "graph": selected_weights.graph,
        "dynamic": (
            selected_weights.sbfl
            + selected_weights.test_failure
            + selected_weights.traceback
        ),
        "semantic": selected_weights.semantic,
        "auxiliary": (
            selected_weights.complexity + selected_weights.change_history
        ),
    }
    rows = {}
    for family, variant in variants.items():
        ablated = _dict(test_metrics.get(variant))
        function = _dict(ablated.get("function"))
        file_metrics = _dict(ablated.get("file"))
        deltas = {
            name: round(
                _float(fusion_function.get(name), 0.0)
                - _float(function.get(name), 0.0),
                6,
            )
            for name in ("top1", "top3", "top5", "mrr", "map", "ndcg_at_3")
        }
        deltas["exam_improvement"] = round(
            _float(function.get("exam"), 0.0)
            - _float(fusion_function.get("exam"), 0.0),
            6,
        )
        deltas["file_top1"] = round(
            _float(fusion_file.get("top1"), 0.0)
            - _float(file_metrics.get("top1"), 0.0),
            6,
        )
        active_weight = round(float(family_weights[family]), 6)
        positive = any(
            deltas[name] > 1e-9 for name in ("top1", "top3", "top5", "mrr", "map")
        )
        negative = any(
            deltas[name] < -1e-9 for name in ("top1", "top3", "top5", "mrr", "map")
        )
        conclusion = (
            "inactive_by_validation_weight_selection"
            if active_weight == 0.0
            else "positive_test_contribution"
            if positive and not negative
            else "mixed_test_contribution"
            if positive and negative
            else "negative_test_contribution"
            if negative
            else "no_observed_test_metric_change"
        )
        rows[family] = {
            "ablation_variant": variant,
            "selected_family_weight": active_weight,
            "fusion_minus_ablation": deltas,
            "conclusion": conclusion,
        }
    return {
        "reference_variant": "fusion",
        "delta_direction": (
            "Positive values mean Fusion outperformed the ablated variant; for "
            "EXAM, positive means the ablated EXAM was worse."
        ),
        "families": rows,
    }


def rerank_v3_signal_rows(
    raw_rows: list[dict[str, Any]],
    weights: ScoreWeights,
) -> list[dict[str, Any]]:
    scored = []
    for raw in raw_rows:
        signals = {
            name: _float(_dict(raw.get("signals")).get(name), 0.0)
            for name in PRIMARY_SIGNALS
        }
        contributions = score_contributions(signals, weights)
        contribution_sum = sum(contributions.values())
        score = score_with_weights(signals, weights)
        reconstructed = min(1.0, max(0.0, contribution_sum))
        scored.append(
            {
                "function_key": str(raw.get("function_key") or ""),
                "file_key": str(raw.get("file_key") or ""),
                "function_id": str(raw.get("function_id") or ""),
                "function_name": str(raw.get("function_name") or ""),
                "start_line": _int(raw.get("start_line"), 0),
                "end_line": _int(raw.get("end_line"), 0),
                "score": round(score, 8),
                "raw_signals": signals,
                "weights": weights.to_dict(),
                "contributions": {
                    name: round(value, 8) for name, value in contributions.items()
                },
                "contribution_sum_before_clamp": round(contribution_sum, 8),
                "contribution_clamp_adjustment": round(score - contribution_sum, 8),
                "score_reconstruction": round(reconstructed, 8),
                "score_reconstruction_error": round(score - reconstructed, 12),
            }
        )
    scored.sort(key=lambda row: (-float(row["score"]), str(row["function_key"])))
    for index, row in enumerate(scored, start=1):
        row["rank"] = index
    return scored


def build_v3_localization_metrics(
    cases: list[dict[str, Any]],
    rankings_by_case: dict[str, list[dict[str, Any]]],
    *,
    rerank_latency_ms: float = 0.0,
) -> dict[str, Any]:
    function_runs: list[LocalizationRun] = []
    file_runs: list[LocalizationRun] = []
    rankable_case_ids: list[str] = []
    unrankable_case_ids: list[str] = []
    for case in cases:
        case_id = str(case.get("case_id") or "")
        ranking = rankings_by_case.get(case_id, [])
        ground_truth = _dict(case.get("ground_truth"))
        file_runs.append(
            LocalizationRun(
                ranked=_file_ranking(ranking),
                ground_truth={str(value) for value in _list(ground_truth.get("file_keys"))},
            )
        )
        if ground_truth.get("function_rankable") is True:
            rankable_case_ids.append(case_id)
            function_runs.append(
                LocalizationRun(
                    ranked=[str(row.get("function_key") or "") for row in ranking],
                    ground_truth={
                        str(value)
                        for value in _list(ground_truth.get("function_keys"))
                    },
                )
            )
        else:
            unrankable_case_ids.append(case_id)
    extraction_latencies = [
        _float(case.get("signal_extraction_latency_ms"), 0.0) for case in cases
    ]
    return {
        "case_count": len(cases),
        "function_rankable_case_count": len(function_runs),
        "function_unrankable_case_count": len(unrankable_case_ids),
        "function_rankable_case_ids": rankable_case_ids,
        "function_unrankable_case_ids": unrankable_case_ids,
        "function": _metrics_for_runs(function_runs),
        "file": _metrics_for_runs(file_runs),
        "mean_signal_extraction_latency_ms": round(_mean(extraction_latencies), 4),
        "rerank_latency_ms": round(rerank_latency_ms, 4),
        "metric_denominator_policy": (
            "Function metrics exclude only cases with no changed function present in "
            "the bug revision; file metrics retain every case."
        ),
    }


def audit_v3_localization_artifacts(
    *,
    ready_cases: list[dict[str, Any]],
    case_results: list[dict[str, Any]],
    selection: dict[str, Any],
    runtime_coverage_required: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    repositories_by_split = {
        split: {
            str(case.get("repository") or "")
            for case in ready_cases
            if case.get("split") == split
        }
        for split in ("development", "validation", "test")
    }
    overlap = sorted(
        (repositories_by_split["development"] & repositories_by_split["validation"])
        | (repositories_by_split["development"] & repositories_by_split["test"])
        | (repositories_by_split["validation"] & repositories_by_split["test"])
    )
    if overlap:
        errors.append("repository_overlap_across_splits")
    unresolved = [
        case["case_id"]
        for case in ready_cases
        if _dict(case.get("ground_truth")).get("status") != "resolved"
    ]
    if unresolved:
        errors.append("ground_truth_not_fully_resolved")
    snapshot_mismatches = [
        case["case_id"]
        for case in ready_cases
        if str(_dict(case.get("ground_truth")).get("ranking_snapshot_sha256") or "")
        != str(case.get("ranking_snapshot_sha256") or "")
    ]
    if snapshot_mismatches:
        errors.append("ground_truth_ranking_snapshot_mismatch")
    reconstruction_failures = [
        str(case.get("case_id") or "")
        for case in case_results
        if case.get("score_reconstruction_pass") is not True
    ]
    if reconstruction_failures:
        errors.append("score_reconstruction_failed")
    missing_coverage = [
        case["case_id"]
        for case in ready_cases
        if _dict(case.get("coverage_summary")).get(
            "real_failing_runtime_coverage_available"
        )
        is not True
    ]
    if runtime_coverage_required and missing_coverage:
        errors.append("required_failing_runtime_coverage_missing")
    if selection.get("scope") != "validation_only":
        errors.append("weight_selection_scope_not_validation_only")
    if selection.get("test_ground_truth_accessed") is not False:
        errors.append("test_ground_truth_accessed_during_weight_search")
    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "repository_disjoint_splits": not overlap,
        "repositories_by_split": {
            split: sorted(values) for split, values in repositories_by_split.items()
        },
        "overlapping_repositories": overlap,
        "resolved_ground_truth_case_count": len(ready_cases) - len(unresolved),
        "unresolved_ground_truth_case_ids": unresolved,
        "ranking_snapshot_match_case_count": len(ready_cases)
        - len(snapshot_mismatches),
        "ranking_snapshot_mismatch_case_ids": snapshot_mismatches,
        "score_reconstruction_pass_case_count": len(case_results)
        - len(reconstruction_failures),
        "score_reconstruction_failure_case_ids": reconstruction_failures,
        "real_failing_runtime_coverage_case_count": len(ready_cases)
        - len(missing_coverage),
        "missing_real_failing_runtime_coverage_case_ids": missing_coverage,
        "validation_only_weight_selection": selection.get("scope")
        == "validation_only",
        "test_ground_truth_accessed_during_weight_search": selection.get(
            "test_ground_truth_accessed"
        ),
    }


def write_v3_localization_evaluation_artifacts(
    result: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "v3_localization_evaluation.json"
    markdown_path = root / "v3_localization_evaluation.md"
    _write_json(json_path, result)
    _write_text(
        markdown_path,
        render_v3_localization_evaluation_markdown(result),
    )
    return {"json": json_path.as_posix(), "markdown": markdown_path.as_posix()}


def write_v3_localization_release_artifacts(
    result: dict[str, Any],
    *,
    source_output_dir: str | Path,
    docs_dir: str | Path,
) -> dict[str, str]:
    source_root = Path(source_output_dir).resolve()
    docs_root = Path(docs_dir).resolve()
    docs_root.mkdir(parents=True, exist_ok=True)
    source_json = source_root / "v3_localization_evaluation.json"
    if not source_json.is_file():
        raise ValueError("V3 localization source evaluation artifact is missing")

    attribution_cases = []
    portable_cases = []
    for value in _list(result.get("case_results")):
        case = _dict(value)
        coverage = _dict(case.get("coverage"))
        portable_cases.append(
            {
                "case_id": str(case.get("case_id") or ""),
                "split": str(case.get("split") or ""),
                "repository": str(case.get("repository") or ""),
                "difficulty_tags": _list(case.get("difficulty_tags")),
                "function_rankable": bool(case.get("function_rankable")),
                "fusion_metrics": _dict(case.get("fusion_metrics")),
                "real_failing_runtime_coverage_available": bool(
                    coverage.get("real_failing_runtime_coverage_available")
                ),
                "score_reconstruction_pass": bool(
                    case.get("score_reconstruction_pass")
                ),
            }
        )
        if case.get("split") != "test":
            continue
        ranking_path = Path(
            str(_dict(case.get("artifacts")).get("variant_rankings") or "")
        )
        ranking_payload = _read_json(ranking_path)
        fusion = _dict(_dict(ranking_payload.get("variants")).get("fusion"))
        attribution_cases.append(
            {
                "case_id": str(case.get("case_id") or ""),
                "ranking_snapshot_sha256": str(
                    ranking_payload.get("ranking_snapshot_sha256") or ""
                ),
                "selected_profile_sha256": str(
                    ranking_payload.get("selected_profile_sha256") or ""
                ),
                "ground_truth_summary": {
                    "function_rankable": bool(case.get("function_rankable")),
                    "function_first_rank": _dict(case.get("fusion_metrics")).get(
                        "function_first_rank"
                    ),
                    "file_first_rank": _dict(case.get("fusion_metrics")).get(
                        "file_first_rank"
                    ),
                },
                "fusion_top5": [
                    _portable_release_ranking_row(row)
                    for row in _list(fusion.get("top_rankings"))[:5]
                ],
                "score_reconstruction_pass": bool(
                    fusion.get("score_reconstruction_pass")
                ),
            }
        )
    attribution = {
        "schema_version": "v3_localization_test_top5_attribution_v1",
        "signal_extraction_version": str(
            result.get("signal_extraction_version") or SIGNAL_EXTRACTION_VERSION
        ),
        "case_count": len(attribution_cases),
        "variant": "fusion",
        "top_k": 5,
        "contract": (
            "Each row retains raw signals, frozen weights, contributions, clamp "
            "adjustment, reconstructed score, and reconstruction error."
        ),
        "cases": attribution_cases,
    }
    attribution_path = docs_root / "phase4_test_top5_attribution.json"
    _write_json(attribution_path, attribution)
    attribution_sha256 = _sha256_file(attribution_path)

    release_summary = {
        "schema_version": "v3_phase4_localization_metrics_v1",
        "signal_extraction_version": str(
            result.get("signal_extraction_version") or SIGNAL_EXTRACTION_VERSION
        ),
        "recorded_at": _utc_timestamp()[:10],
        "status": str(result.get("status") or ""),
        "dataset": {
            "accepted_case_count": _int(result.get("case_count"), 0),
            "ready_case_count": _int(result.get("ready_case_count"), 0),
            "split_case_counts": _dict(result.get("split_case_counts")),
            "function_rankable_case_count": sum(
                bool(case.get("function_rankable")) for case in portable_cases
            ),
            "function_unrankable_case_count": sum(
                not bool(case.get("function_rankable")) for case in portable_cases
            ),
        },
        "selection": _dict(result.get("selection")),
        "frozen_test_metrics": _dict(
            _dict(result.get("metrics_by_split")).get("test")
        ),
        "test_difficulty_subset_metrics": _dict(
            result.get("test_difficulty_subset_metrics")
        ),
        "ablation_analysis": _dict(result.get("ablation_analysis")),
        "failure_analysis": _dict(result.get("failure_analysis")),
        "coverage_summary": _dict(result.get("coverage_summary")),
        "artifact_audit": _dict(result.get("artifact_audit")),
        "case_results": portable_cases,
        "evidence": {
            "authoritative_local_artifact": _portable_path(source_json),
            "authoritative_local_artifact_sha256": _sha256_file(source_json),
            "committed_test_top5_attribution": _portable_path(attribution_path),
            "committed_test_top5_attribution_sha256": attribution_sha256,
            "reproduction_command": (
                "python -m code_intelligence_agent v3-localization-eval "
                "outputs_v3/localization_phase4 --coverage-timeout 180 "
                "--release-docs-dir docs/v3"
            ),
        },
        "claim_boundaries": [
            "Metrics are fault-localization results, not patch repair rates.",
            "Semantic-only is deterministic lexical similarity, not an LLM result.",
            "Rule and Graph received zero validation-selected Fusion weight and therefore receive no Fusion success attribution.",
            "The repository-disjoint test split contains five cases from one repository; broader confidence intervals remain future work.",
            "LLM-only localization is not applicable until a real localization scorer is configured.",
        ],
    }
    metrics_path = docs_root / "phase4_localization_metrics.json"
    _write_json(metrics_path, release_summary)
    markdown_path = docs_root / "phase4_difficult_localization.md"
    markdown = render_v3_localization_evaluation_markdown(result)
    markdown += (
        "\n## Reproduction Evidence\n\n"
        f"- Command: `{release_summary['evidence']['reproduction_command']}`\n"
        f"- Local evaluation SHA-256: `{release_summary['evidence']['authoritative_local_artifact_sha256']}`\n"
        f"- Committed Top-5 attribution SHA-256: `{attribution_sha256}`\n"
        "- Machine-readable metrics: `docs/v3/phase4_localization_metrics.json`\n"
        "- Test Top-5 attribution: `docs/v3/phase4_test_top5_attribution.json`\n"
    )
    _write_text(markdown_path, markdown)
    return {
        "metrics_json": metrics_path.as_posix(),
        "markdown": markdown_path.as_posix(),
        "test_top5_attribution_json": attribution_path.as_posix(),
    }


def render_v3_localization_evaluation_markdown(result: dict[str, Any]) -> str:
    selection = _dict(result.get("selection"))
    selected = _dict(selection.get("selected_profile"))
    counts = _dict(result.get("split_case_counts"))
    coverage = _dict(result.get("coverage_summary"))
    artifact_audit = _dict(result.get("artifact_audit"))
    lines = [
        "# V3 Real-Bug Fault Localization Evaluation",
        "",
        f"- Status: `{result.get('status', '')}`",
        f"- Ready / Total Cases: {result.get('ready_case_count', 0)} / {result.get('case_count', 0)}",
        f"- Blockers: {result.get('blocker_count', 0)}",
        (
            "- Split Counts: "
            f"development={counts.get('development', 0)}, "
            f"validation={counts.get('validation', 0)}, test={counts.get('test', 0)}"
        ),
        f"- Weight Selection Scope: `{selection.get('scope', '')}`",
        f"- Selected Profile: `{selected.get('name', '')}`",
        f"- Selected Profile SHA256: `{selection.get('selected_profile_sha256', '')}`",
        f"- Candidate Weight Profiles: {selection.get('candidate_profile_count', 0)}",
        f"- Runtime Coverage Available: {coverage.get('real_runtime_coverage_case_count', 0)} / {coverage.get('case_count', 0)}",
        f"- Failing Runtime Coverage Available: {coverage.get('real_failing_runtime_coverage_case_count', 0)} / {coverage.get('case_count', 0)}",
        f"- Artifact Audit: `{artifact_audit.get('status', '')}`",
        "",
        "## Frozen Test Metrics",
        "",
        "| Variant | Function Cases | Top-1 | Top-3 | Top-5 | MRR | MAP | EXAM | File Top-1 | File MRR | Status |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    test_metrics = _dict(_dict(result.get("metrics_by_split")).get("test"))
    for variant in (*EVALUATED_VARIANTS, "llm_only"):
        metrics = _dict(test_metrics.get(variant))
        function = _dict(metrics.get("function"))
        file_metrics = _dict(metrics.get("file"))
        status = str(metrics.get("status") or "evaluated")
        lines.append(
            "| "
            f"{variant} | {metrics.get('function_rankable_case_count', 0)} | "
            f"{_metric(function, 'top1')} | {_metric(function, 'top3')} | "
            f"{_metric(function, 'top5')} | {_metric(function, 'mrr')} | "
            f"{_metric(function, 'map')} | {_metric(function, 'exam')} | "
            f"{_metric(file_metrics, 'top1')} | {_metric(file_metrics, 'mrr')} | "
            f"{status} |"
        )
    lines.extend(
        [
            "",
            "## Fusion On Difficult Test Subsets",
            "",
            "| Subset | Cases | Function Cases | Top-1 | Top-3 | MRR | MAP | File Top-1 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for tag in DIFFICULTY_SUBSETS:
        metrics = _dict(_dict(result.get("test_difficulty_subset_metrics")).get(tag))
        function = _dict(metrics.get("function"))
        file_metrics = _dict(metrics.get("file"))
        lines.append(
            "| "
            f"{tag} | {metrics.get('case_count', 0)} | "
            f"{metrics.get('function_rankable_case_count', 0)} | "
            f"{_metric(function, 'top1')} | {_metric(function, 'top3')} | "
            f"{_metric(function, 'mrr')} | {_metric(function, 'map')} | "
            f"{_metric(file_metrics, 'top1')} |"
        )
    lines.extend(
        [
            "",
            "## Per-Case Fusion Results",
            "",
            "| Case | Split | Tags | Function Rankable | Function First Rank | File First Rank | Real Failing Coverage |",
            "| --- | --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for case in _list(result.get("case_results")):
        row = _dict(case)
        metrics = _dict(row.get("fusion_metrics"))
        case_coverage = _dict(row.get("coverage"))
        lines.append(
            "| "
            f"{row.get('case_id', '')} | {row.get('split', '')} | "
            f"{', '.join(str(value) for value in _list(row.get('difficulty_tags')))} | "
            f"{str(bool(row.get('function_rankable'))).lower()} | "
            f"{_rank_cell(metrics.get('function_first_rank'))} | "
            f"{_rank_cell(metrics.get('file_first_rank'))} | "
            f"{str(bool(case_coverage.get('real_failing_runtime_coverage_available'))).lower()} |"
        )
    if not _list(result.get("case_results")):
        lines.append("| none | none | none | false | n/a | n/a | false |")
    lines.extend(
        [
            "",
            "## Ablation Interpretation",
            "",
            "| Signal Family | Selected Weight | Top-1 Delta | Top-3 Delta | MRR Delta | MAP Delta | EXAM Improvement | Conclusion |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    families = _dict(_dict(result.get("ablation_analysis")).get("families"))
    for family in ("rule", "graph", "dynamic", "semantic", "auxiliary"):
        row = _dict(families.get(family))
        deltas = _dict(row.get("fusion_minus_ablation"))
        lines.append(
            "| "
            f"{family} | {_float(row.get('selected_family_weight'), 0.0):.4f} | "
            f"{_float(deltas.get('top1'), 0.0):.4f} | "
            f"{_float(deltas.get('top3'), 0.0):.4f} | "
            f"{_float(deltas.get('mrr'), 0.0):.4f} | "
            f"{_float(deltas.get('map'), 0.0):.4f} | "
            f"{_float(deltas.get('exam_improvement'), 0.0):.4f} | "
            f"{row.get('conclusion', '')} |"
        )
    failure = _dict(result.get("failure_analysis"))
    lines.extend(
        [
            "",
            "## Failure Analysis",
            "",
            f"- Test Top-1 Misses: {failure.get('test_top1_miss_count', 0)}",
            f"- Test Top-5 Misses: {failure.get('test_top5_miss_count', 0)}",
            (
                "- Inactive Signal Families: `"
                + ", ".join(str(value) for value in _list(failure.get("inactive_signal_families")))
                + "`"
            ),
            (
                "- Empty Test Difficulty Subsets: `"
                + ", ".join(str(value) for value in _list(failure.get("empty_test_difficulty_subsets")))
                + "`"
            ),
            f"- Test Repository Count: {failure.get('test_repository_count', 0)}",
        ]
    )
    for miss in _list(failure.get("test_top1_misses")):
        row = _dict(miss)
        lines.append(
            f"- `{row.get('case_id', '')}` first appears at function rank "
            f"{_rank_cell(row.get('function_first_rank'))}."
        )
    lines.extend(
        [
            "",
            "## Protocol And Attribution Contract",
            "",
            "- Candidate scope and all raw signals are frozen before fix-side ground truth is read.",
            "- Weight search receives validation cases only; the selected profile hash is frozen before test evaluation.",
            "- Every stored Top-k row contains raw signals, active weights, per-signal contributions, clamp adjustment, and a reconstructed score.",
            "- Function-unrankable cases remain in file-level metrics and are listed explicitly instead of being counted as artificial function misses.",
            "- Runtime line coverage is collected with the case-pinned Python interpreter. Branch/path evidence is inferred from line and call events, not claimed as native branch coverage.",
            "- `semantic_only` is deterministic lexical similarity. `llm_only` is not applicable until a real localization scorer is configured.",
            "- No global uplift is assumed; zero or negative ablation differences remain in the artifact.",
            "",
            "## Boundary",
            "",
            f"{result.get('boundary', '')}",
        ]
    )
    blockers = _list(result.get("blockers"))
    if blockers:
        lines.extend(["", "## Blockers", ""])
        for blocker in blockers:
            row = _dict(blocker)
            lines.append(f"- `{row.get('case_id', '')}`: {row.get('reason', '')}")
    return "\n".join(lines) + "\n"


def _merge_runtime_coverage(
    baseline: TestExecutionSummary,
    runtime_results: list[Any],
) -> TestExecutionSummary:
    runtime_authoritative = bool(runtime_results)
    failed = set() if runtime_authoritative else set(baseline.failed_tests)
    passed = set() if runtime_authoritative else set(baseline.passed_tests)
    coverage: dict[str, set[str]] = {}
    line_coverage: dict[str, dict[str, float]] = {}
    covered_lines: dict[str, dict[str, set[int]]] = {}
    branch_coverage: dict[str, dict[str, set[str]]] = {}
    path_coverage: dict[str, dict[str, set[str]]] = {}
    test_names = dict(baseline.test_names)
    failure_messages = dict(baseline.failure_messages)
    dynamic_test_ids = (
        set() if runtime_authoritative else set(baseline.dynamic_evidence_test_ids)
    )
    nodeids = dict(baseline.dynamic_evidence_nodeids)
    runtime_failure_observed = any(not result.success for result in runtime_results)
    if runtime_failure_observed:
        dynamic_test_ids.update(baseline.dynamic_evidence_test_ids)
    for result in runtime_results:
        test_id = str(result.test_id)
        if result.success:
            passed.add(test_id)
        else:
            failed.add(test_id)
            dynamic_test_ids.add(test_id)
            failure_messages[test_id] = _bounded_text(
                "\n".join(part for part in (result.stdout, result.stderr) if part),
                8_000,
            )
        coverage[test_id] = set(result.covered_function_ids)
        line_coverage[test_id] = dict(result.function_line_coverage)
        covered_lines[test_id] = {
            function_id: set(lines)
            for function_id, lines in result.covered_function_lines.items()
        }
        branch_coverage[test_id] = {
            function_id: set(values)
            for function_id, values in result.covered_branch_outcomes.items()
        }
        path_coverage[test_id] = {
            function_id: set(values)
            for function_id, values in result.covered_path_fragments.items()
        }
        test_names[test_id] = str(result.test_name)
    return TestExecutionSummary(
        failed_tests=failed,
        passed_tests=passed,
        coverage=coverage,
        line_coverage=line_coverage,
        covered_lines=covered_lines,
        branch_coverage=branch_coverage,
        path_coverage=path_coverage,
        traceback_function_ids=set(baseline.traceback_function_ids),
        dynamic_traceback_function_ids=set(
            baseline.dynamic_traceback_function_ids
            or baseline.traceback_function_ids
        ),
        test_names=test_names,
        failure_messages=failure_messages,
        dynamic_evidence_test_ids=dynamic_test_ids,
        dynamic_evidence_nodeids=nodeids,
        dynamic_evidence_unmatched_nodeids=set(
            baseline.dynamic_evidence_unmatched_nodeids
        ),
    )


def _portable_raw_signal_row(payload: dict[str, Any], root: Path) -> dict[str, Any]:
    file_path = Path(str(payload.get("file_path") or "")).resolve()
    try:
        relative = file_path.relative_to(root).as_posix()
    except ValueError:
        return {}
    function_name = str(payload.get("function_name") or "")
    signals = {
        str(key): _float(value, 0.0)
        for key, value in _dict(payload.get("signals")).items()
        if not str(key).startswith("weight_")
        and not str(key).startswith("contribution_")
        and str(key) not in {"score_reconstruction"}
    }
    return {
        "function_key": f"{relative}::{function_name}",
        "file_key": relative,
        "function_id": str(payload.get("function_id") or ""),
        "function_name": function_name,
        "start_line": _int(payload.get("start_line"), 0),
        "end_line": _int(payload.get("end_line"), 0),
        "signals": signals,
    }


def _coverage_result_payload(result: Any, command: list[str]) -> dict[str, Any]:
    return {
        "test_id": str(result.test_id),
        "test_name": str(result.test_name),
        "command": command,
        "status": "pass" if result.success else "fail",
        "returncode": int(result.returncode),
        "covered_function_count": len(result.covered_function_ids),
        "covered_line_count": sum(result.covered_function_line_counts.values()),
        "stdout_sha256": _sha256_text(str(result.stdout)),
        "stderr_sha256": _sha256_text(str(result.stderr)),
        "stdout_preview": _bounded_text(str(result.stdout), 1_000),
        "stderr_preview": _bounded_text(str(result.stderr), 2_000),
    }


def _case_signal_fingerprint(
    *,
    case: dict[str, Any],
    seed: Path,
    seed_audit: dict[str, Any],
    runtime: dict[str, Any],
    use_runtime_coverage: bool,
    coverage_timeout: int,
) -> str:
    return _sha256_json(
        {
            "schema_version": SCHEMA_VERSION,
            "case_id": str(case.get("case_id") or ""),
            "bug_commit_sha": str(case.get("bug_commit_sha") or ""),
            "targeted_test_commands": _list(case.get("targeted_test_commands")),
            "test_environment": _dict(case.get("test_environment")),
            "reproduction_artifact_sha256": str(
                seed_audit.get("reproduction_artifact_sha256") or ""
            ),
            "runtime_profile_id": str(runtime.get("profile_id") or ""),
            "runtime_python": str(runtime.get("python_executable") or ""),
            "runtime_coverage": use_runtime_coverage,
            "coverage_timeout_seconds": coverage_timeout,
            "bug_python_source_sha256": _repository_python_source_fingerprint(seed),
            "signal_extraction_version": SIGNAL_EXTRACTION_VERSION,
        }
    )


def _repository_python_source_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if (
            not path.is_file()
            or path.is_symlink()
            or is_default_excluded_repo_path(relative.parts)
        ):
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    return digest.hexdigest()


def _rank_cases(
    cases: list[dict[str, Any]],
    weights: ScoreWeights,
) -> tuple[dict[str, list[dict[str, Any]]], float]:
    started = time.perf_counter()
    rankings = {
        str(case.get("case_id") or ""): rerank_v3_signal_rows(
            _list(case.get("raw_rows")),
            weights,
        )
        for case in cases
    }
    return rankings, round((time.perf_counter() - started) * 1000, 4)


def _single_case_metrics(
    case: dict[str, Any],
    ranking: list[dict[str, Any]],
) -> dict[str, Any]:
    ground_truth = _dict(case.get("ground_truth"))
    function_truth = {
        str(value) for value in _list(ground_truth.get("function_keys"))
    }
    file_truth = {str(value) for value in _list(ground_truth.get("file_keys"))}
    function_ranking = [str(row.get("function_key") or "") for row in ranking]
    file_ranking = _file_ranking(ranking)
    return {
        "function_rankable": ground_truth.get("function_rankable") is True,
        "function_first_rank": (
            _first_relevant_rank(function_ranking, function_truth)
            if ground_truth.get("function_rankable") is True
            else None
        ),
        "file_first_rank": _first_relevant_rank(file_ranking, file_truth),
        "function_top1": bool(function_ranking[:1] and function_truth.intersection(function_ranking[:1])),
        "function_top3": bool(function_truth.intersection(function_ranking[:3])),
        "function_top5": bool(function_truth.intersection(function_ranking[:5])),
        "file_top1": bool(file_ranking[:1] and file_truth.intersection(file_ranking[:1])),
        "file_top3": bool(file_truth.intersection(file_ranking[:3])),
        "file_top5": bool(file_truth.intersection(file_ranking[:5])),
    }


def _metrics_for_runs(runs: list[LocalizationRun]) -> dict[str, Any]:
    return {
        "case_count": len(runs),
        "top1": round(top_k_accuracy(runs, 1), 6),
        "top3": round(top_k_accuracy(runs, 3), 6),
        "top5": round(top_k_accuracy(runs, 5), 6),
        "mrr": round(mean_reciprocal_rank(runs), 6),
        "map": round(mean_average_precision(runs), 6),
        "ndcg_at_3": round(mean_ndcg(runs, 3), 6),
        "exam": round(mean_exam_score(runs), 6),
    }


def _objective(metrics: dict[str, Any]) -> float:
    return (
        0.25 * _float(metrics.get("map"), 0.0)
        + 0.25 * _float(metrics.get("mrr"), 0.0)
        + 0.20 * _float(metrics.get("ndcg_at_3"), 0.0)
        + 0.15 * _float(metrics.get("top1"), 0.0)
        + 0.10 * _float(metrics.get("top3"), 0.0)
        + 0.05 * (1.0 - _float(metrics.get("exam"), 1.0))
    )


def _file_ranking(ranking: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    output = []
    for row in ranking:
        file_key = str(row.get("file_key") or "")
        if file_key and file_key not in seen:
            seen.add(file_key)
            output.append(file_key)
    return output


def _first_relevant_rank(ranking: list[str], ground_truth: set[str]) -> int | None:
    for index, value in enumerate(ranking, start=1):
        if value in ground_truth:
            return index
    return None


def _aggregate_coverage(cases: list[dict[str, Any]]) -> dict[str, Any]:
    available = [
        case
        for case in cases
        if _dict(case.get("coverage_summary")).get("real_runtime_coverage_available")
        is True
    ]
    failing = [
        case
        for case in cases
        if _dict(case.get("coverage_summary")).get(
            "real_failing_runtime_coverage_available"
        )
        is True
    ]
    return {
        "case_count": len(cases),
        "real_runtime_coverage_case_count": len(available),
        "real_failing_runtime_coverage_case_count": len(failing),
        "real_runtime_coverage_rate": _ratio(len(available), len(cases)),
        "real_failing_runtime_coverage_rate": _ratio(len(failing), len(cases)),
        "missing_real_runtime_coverage_case_ids": [
            case["case_id"] for case in cases if case not in available
        ],
        "missing_real_failing_runtime_coverage_case_ids": [
            case["case_id"] for case in cases if case not in failing
        ],
    }


def _build_failure_analysis(
    *,
    ready_cases: list[dict[str, Any]],
    case_results: list[dict[str, Any]],
    test_subset_metrics: dict[str, Any],
    selected_weights: ScoreWeights,
    ablation_analysis: dict[str, Any],
) -> dict[str, Any]:
    test_cases = [case for case in ready_cases if case.get("split") == "test"]
    validation_cases = [
        case for case in ready_cases if case.get("split") == "validation"
    ]
    test_case_results = [
        case for case in case_results if case.get("split") == "test"
    ]
    top1_misses = [
        {
            "case_id": case["case_id"],
            "function_first_rank": _dict(case.get("fusion_metrics")).get(
                "function_first_rank"
            ),
            "file_first_rank": _dict(case.get("fusion_metrics")).get(
                "file_first_rank"
            ),
            "difficulty_tags": case.get("difficulty_tags", []),
        }
        for case in test_case_results
        if _dict(case.get("fusion_metrics")).get("function_rankable") is True
        and _dict(case.get("fusion_metrics")).get("function_first_rank") != 1
    ]
    top5_misses = [
        case
        for case in top1_misses
        if _int(case.get("function_first_rank"), 10**9) > 5
    ]
    empty_test_subsets = [
        tag
        for tag, metrics in test_subset_metrics.items()
        if _int(_dict(metrics).get("case_count"), 0) == 0
    ]
    families = _dict(ablation_analysis.get("families"))
    inactive_families = [
        family
        for family, row in families.items()
        if _dict(row).get("conclusion") == "inactive_by_validation_weight_selection"
    ]
    no_change_families = [
        family
        for family, row in families.items()
        if _dict(row).get("conclusion") == "no_observed_test_metric_change"
    ]
    return {
        "test_top1_miss_count": len(top1_misses),
        "test_top1_misses": top1_misses,
        "test_top5_miss_count": len(top5_misses),
        "test_top5_misses": top5_misses,
        "function_unrankable_case_ids": [
            case["case_id"]
            for case in ready_cases
            if _dict(case.get("ground_truth")).get("function_rankable") is not True
        ],
        "inactive_signal_families": inactive_families,
        "no_observed_change_signal_families": no_change_families,
        "selected_weights": selected_weights.to_dict(),
        "test_repository_count": len(
            {str(case.get("repository") or "") for case in test_cases}
        ),
        "test_repositories": sorted(
            {str(case.get("repository") or "") for case in test_cases}
        ),
        "validation_repository_count": len(
            {str(case.get("repository") or "") for case in validation_cases}
        ),
        "empty_test_difficulty_subsets": empty_test_subsets,
        "limitations": [
            "The test split is repository-disjoint but contains one repository, so confidence intervals and broader repository coverage remain future work.",
            "Signal families assigned zero validation weight cannot receive Fusion success attribution on the frozen test split.",
            "LLM-only localization is not evaluated because no live localization scorer is configured.",
            "A function introduced only by the fix revision has no rankable bug-side function and is evaluated at file level only.",
        ],
    }


def _not_applicable_llm_metrics(case_count: int) -> dict[str, Any]:
    return {
        "status": "not_applicable",
        "reason": "no_live_llm_localization_scorer_configured",
        "case_count": case_count,
        "function_rankable_case_count": 0,
        "function": {},
        "file": {},
    }


def _test_command_label(command: list[str], index: int) -> str:
    try:
        module_index = command.index("-m") + 2
    except ValueError:
        return f"targeted-command-{index}"
    args = command[module_index:]
    return " ".join(args) if args else f"targeted-command-{index}"


def _first_baseline_execution(reproduction: dict[str, Any]) -> dict[str, Any]:
    results = _list(_dict(reproduction.get("bug_targeted")).get("results"))
    return _dict(results[0]) if results else {}


def _weights_from_dict(value: dict[str, Any]) -> ScoreWeights:
    return ScoreWeights(
        **{name: _float(value.get(name), 0.0) for name in PRIMARY_SIGNALS}
    )


def _weights_key(weights: ScoreWeights) -> tuple[float, ...]:
    values = weights.to_dict()
    return tuple(round(float(values[name]), 8) for name in PRIMARY_SIGNALS)


def _metric(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    return "n/a" if value is None else f"{_float(value, 0.0):.4f}"


def _rank_cell(value: Any) -> str:
    return "n/a" if value is None else str(_int(value, 0))


def _bounded_text(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit] + "\n...[truncated]"


def _portable_path(path: str | Path) -> str:
    target = Path(path).resolve()
    try:
        return target.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return target.name


def _portable_release_ranking_row(value: Any) -> dict[str, Any]:
    row = dict(_dict(value))
    row["function_id"] = str(row.get("function_key") or "")
    return row


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_text(
        target,
        json.dumps(value, indent=2, ensure_ascii=False),
    )


def _write_text(path: str | Path, value: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(value)


def _sha256_json(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(serialized)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _utc_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate V3 real-bug fault localization with validation-only weight "
            "selection and frozen test metrics."
        )
    )
    parser.add_argument("output_dir")
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--catalog",
        default="docs/v3/phase1_real_bug_catalog.json",
    )
    parser.add_argument(
        "--environment-profiles",
        default="datasets/v3_real_bugs/environment_profile_sources.json",
    )
    parser.add_argument("--reproduction-root", default="outputs_v3/reproduction")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--coverage-timeout", type=int, default=120)
    parser.add_argument("--no-runtime-coverage", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--ranking-artifact-limit", type=int, default=50)
    parser.add_argument(
        "--release-docs-dir",
        default="",
        help="Optionally write portable Phase 4 release artifacts to this directory.",
    )
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--require-pass", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    root = Path(args.root).resolve()
    result = run_v3_localization_evaluation(
        project_root=root,
        catalog_path=root / args.catalog,
        environment_profiles_path=root / args.environment_profiles,
        reproduction_root=root / args.reproduction_root,
        output_dir=args.output_dir,
        case_ids=args.case_id,
        max_cases=max(0, args.max_cases),
        coverage_timeout=max(1, args.coverage_timeout),
        use_runtime_coverage=not args.no_runtime_coverage,
        resume=not args.no_resume,
        ranking_artifact_limit=max(1, args.ranking_artifact_limit),
    )
    if args.release_docs_dir:
        write_v3_localization_release_artifacts(
            result,
            source_output_dir=args.output_dir,
            docs_dir=args.release_docs_dir,
        )
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(render_v3_localization_evaluation_markdown(result))
    if args.require_pass and result.get("status") != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
