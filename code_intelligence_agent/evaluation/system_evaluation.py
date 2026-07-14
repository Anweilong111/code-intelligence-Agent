from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import random
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from code_intelligence_agent.agents.controller import _llm_replan_prompt
from code_intelligence_agent.agents.llm_patch_generator import (
    build_patch_prompt,
    build_reflection_prompt,
)
from code_intelligence_agent.evaluation.budget_ablation_evaluation import (
    evaluate_budget_ablations,
    render_budget_ablation_markdown,
    write_budget_ablation,
)
from code_intelligence_agent.evaluation.localization_split_evaluation import (
    LocalizationSplitEvaluator,
)
from code_intelligence_agent.evaluation.memory_ablation_evaluation import (
    evaluate_memory_ablation,
    render_memory_ablation_markdown,
    write_memory_ablation,
)
from code_intelligence_agent.evaluation.patch_strategy_evaluation import (
    evaluate_patch_strategies,
    render_patch_strategy_evaluation_markdown,
    write_patch_strategy_evaluation,
)
from code_intelligence_agent.evaluation.planner_strategy_evaluation import (
    evaluate_planner_strategies,
    render_planner_strategy_evaluation_markdown,
    write_planner_strategy_evaluation,
)


COMPONENT_ARTIFACTS = {
    "planner": (
        "planner_strategy_evaluation.json",
        "planner_strategy_evaluation.md",
    ),
    "memory": (
        "memory_ablation_evaluation.json",
        "memory_ablation_evaluation.md",
    ),
    "localization": (
        "localization_split_evaluation.json",
        "localization_split_evaluation.md",
    ),
    "patch": (
        "patch_strategy_evaluation.json",
        "patch_strategy_evaluation.md",
    ),
    "budgets": (
        "budget_ablation_evaluation.json",
        "budget_ablation_evaluation.md",
    ),
}


def run_phase7_system_evaluation(
    repository_root: str | Path,
    work_dir: str | Path,
    *,
    publication_dir: str | Path | None = None,
) -> dict[str, Any]:
    repo_root = Path(repository_root).resolve()
    work_root = Path(work_dir).resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    paths = _evaluation_paths(repo_root)

    planner = evaluate_planner_strategies(paths["planner_dataset"])
    planner_paths = write_planner_strategy_evaluation(
        planner, work_root / "planner"
    )
    memory_dataset = _load_json(paths["memory_dataset"])
    memory = evaluate_memory_ablation(memory_dataset)
    memory_paths = write_memory_ablation(memory, work_root / "memory")
    localization_report = LocalizationSplitEvaluator().run_protocol(
        paths["localization_protocol"], work_root / "localization"
    )
    localization = localization_report.to_dict()
    localization_paths = {
        "json": localization_report.artifacts["json"],
        "markdown": localization_report.artifacts["markdown"],
    }
    patch = evaluate_patch_strategies(paths["patch_dataset"])
    patch_paths = write_patch_strategy_evaluation(patch, work_root / "patch")
    budgets = evaluate_budget_ablations(
        paths["system_dataset"], paths["patch_dataset"]
    )
    budget_paths = write_budget_ablation(budgets, work_root / "budgets")
    baseline = _load_json(paths["baseline_metrics"])
    phase6 = _load_json(paths["phase6_metrics"])

    publication_root = (
        Path(publication_dir).resolve() if publication_dir else work_root
    )
    artifact_manifest = _artifact_manifest(publication_root, repo_root)
    report = build_phase7_system_report(
        repository_root=repo_root,
        paths=paths,
        planner=planner,
        memory=memory,
        localization=localization,
        patch=patch,
        budgets=budgets,
        baseline=baseline,
        phase6=phase6,
        artifact_manifest=artifact_manifest,
    )
    component_paths = {
        "planner": _normalize_component_paths(planner_paths),
        "memory": _normalize_component_paths(memory_paths),
        "localization": localization_paths,
        "patch": _normalize_component_paths(patch_paths),
        "budgets": _normalize_component_paths(budget_paths),
    }
    write_phase7_system_report(report, work_root)
    if publication_dir:
        publish_phase7_artifacts(
            report,
            component_paths=component_paths,
            publication_dir=publication_root,
            repository_root=repo_root,
            work_dir=work_root,
        )
    return report


def build_phase7_system_report(
    *,
    repository_root: Path,
    paths: dict[str, Path],
    planner: dict[str, Any],
    memory: dict[str, Any],
    localization: dict[str, Any],
    patch: dict[str, Any],
    budgets: dict[str, Any],
    baseline: dict[str, Any],
    phase6: dict[str, Any],
    artifact_manifest: dict[str, Any],
) -> dict[str, Any]:
    localization_ablations = {
        str(_dict(item).get("profile") or ""): _dict(item)
        for item in _list(localization.get("ablation_results"))
    }
    dimensions = _dict(budgets.get("dimensions"))
    comparisons = {
        "patch_strategy": {
            "label": "Rule Patch vs LLM Patch vs Hybrid Patch",
            "results": _dict(patch.get("strategies")),
            "artifact": artifact_manifest["patch"]["json"],
        },
        "planner_strategy": {
            "label": "Rule Planner vs LLM Planner vs Hybrid Planner",
            "results": _dict(planner.get("strategies")),
            "artifact": artifact_manifest["planner"]["json"],
        },
        "graph": {
            "label": "With Graph vs Without Graph",
            "with_graph": localization_ablations.get("fusion", {}),
            "without_graph": localization_ablations.get("without_graph", {}),
            "artifact": artifact_manifest["localization"]["json"],
        },
        "dynamic_evidence": {
            "label": "With Dynamic Evidence vs Without Dynamic Evidence",
            "with_dynamic": localization_ablations.get("fusion", {}),
            "without_dynamic": localization_ablations.get("without_dynamic", {}),
            "artifact": artifact_manifest["localization"]["json"],
        },
        "memory": {
            "label": "With Memory vs Without Memory",
            "results": _dict(memory.get("metrics")),
            "artifact": artifact_manifest["memory"]["json"],
        },
        "reflection": {
            "label": "With Reflection vs Without Reflection",
            "results": _list(_dict(dimensions.get("reflection")).get("runs")),
            "artifact": artifact_manifest["budgets"]["json"],
        },
        "top_k_context": {
            "label": "Top-k Context Sizes",
            "results": _list(_dict(dimensions.get("top_k_context")).get("runs")),
            "artifact": artifact_manifest["budgets"]["json"],
        },
        "action_and_candidate_budget": {
            "label": "Action Budget and Candidate Budget",
            "action_results": _list(
                _dict(dimensions.get("action_budget")).get("runs")
            ),
            "candidate_results": _list(
                _dict(dimensions.get("candidate_budget")).get("runs")
            ),
            "artifact": artifact_manifest["budgets"]["json"],
        },
    }
    comparison_complete = {
        name: _comparison_has_results(name, payload)
        for name, payload in comparisons.items()
    }
    component_status = {
        "planner": str(planner.get("status") or "") == "pass",
        "memory": str(memory.get("status") or "") == "pass",
        "localization": bool(localization.get("non_regression_passed")),
        "patch": str(patch.get("status") or "") == "pass",
        "budgets": str(budgets.get("status") or "") == "pass",
        "phase6": bool(_nested(phase6, "outcome_evaluation", "passed"))
        and int(_nested(phase6, "verification", "failure_count") or 0) == 0,
    }
    v1_v2 = _v1_v2_comparison(localization, baseline)
    uncertainty = _uncertainty_report(planner, memory, patch)
    gates = {
        "all_required_comparisons_present": all(comparison_complete.values()),
        "all_component_evaluations_passed": all(component_status.values()),
        "localization_uses_validation_test_blind_split": set(
            _dict(localization.get("split_results"))
        )
        == {"validation", "test", "blind"},
        "fixed_dataset_hashes_recorded": all(
            bool(item.get("sha256"))
            for item in _list(_protocol_metadata(repository_root, paths).get("datasets"))
        ),
        "v1_v2_quantified_comparison_present": bool(v1_v2.get("rows")),
        "raw_artifacts_linked": all(
            bool(_dict(item).get("json"))
            for item in artifact_manifest.values()
            if isinstance(item, dict)
        ),
    }
    passed = all(gates.values())
    return {
        "schema_version": 1,
        "phase": "phase7_system_evaluation",
        "status": "pass" if passed else "fail",
        "reason": (
            "all_phase7_evaluation_contracts_met"
            if passed
            else "phase7_evaluation_contract_failed"
        ),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "protocol": _protocol_metadata(repository_root, paths),
        "component_status": component_status,
        "required_comparisons": comparisons,
        "comparison_complete": comparison_complete,
        "core_metrics": _core_metrics(
            planner=planner,
            memory=memory,
            localization=localization,
            patch=patch,
            budgets=budgets,
            phase6=phase6,
        ),
        "uncertainty": uncertainty,
        "v1_v2_comparison": v1_v2,
        "failure_accounting": _failure_accounting(
            planner=planner,
            memory=memory,
            patch=patch,
            budgets=budgets,
            phase6=phase6,
        ),
        "acceptance_gates": gates,
        "artifacts": artifact_manifest,
        "claim_boundaries": [
            "Controlled LLM fixtures measure orchestration, schema, safety, attribution, reflection, and budget behavior; they do not estimate live-model quality.",
            "Localization uses mutation cases with rule-detectable faults; equal fusion and rule-only scores do not prove fusion superiority.",
            "Phase 6 success means static analysis completed and an authorized test process started and terminated; it does not mean tests passed or a repair succeeded.",
            "Patch success is counted only when targeted and full regression pytest pass; LLM Judge is never success authority.",
            "V1 and V2 repair metrics with different datasets are marked non-comparable rather than reported as uplift.",
        ],
    }


def _core_metrics(
    *,
    planner: dict[str, Any],
    memory: dict[str, Any],
    localization: dict[str, Any],
    patch: dict[str, Any],
    budgets: dict[str, Any],
    phase6: dict[str, Any],
) -> dict[str, Any]:
    fusion = next(
        (
            _dict(item)
            for item in _list(localization.get("ablation_results"))
            if str(_dict(item).get("profile") or "") == "fusion"
        ),
        {},
    )
    return {
        "localization": {
            key: fusion.get(key)
            for key in (
                "case_count",
                "top1",
                "top3",
                "top5",
                "mrr",
                "map",
                "mean_localization_latency_ms",
            )
        },
        "patch": _dict(patch.get("strategies")),
        "planner": _dict(planner.get("strategies")),
        "memory": _dict(memory.get("metrics")),
        "budgets": _dict(budgets.get("dimensions")),
        "unfamiliar_repositories": {
            "repository_count": _nested(phase6, "dataset", "repository_count"),
            "structured_report_rate": _nested(
                phase6, "outcome_evaluation", "structured_report_rate"
            ),
            "static_analysis_completion_rate": _nested(
                phase6, "outcome_evaluation", "static_analysis_success_rate"
            ),
            "test_process_start_rate": _nested(
                phase6, "outcome_evaluation", "test_start_rate"
            ),
            "blocker_classification_rate": _nested(
                phase6, "outcome_evaluation", "blocker_classification_rate"
            ),
        },
    }


def _v1_v2_comparison(
    localization: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, Any]:
    rows = []
    for split, split_value in _dict(localization.get("split_results")).items():
        item = _dict(split_value)
        v1 = _dict(item.get("v1"))
        v2 = _dict(item.get("v2"))
        for metric in ("top1", "top3", "top5", "mrr", "map"):
            v1_value = float(v1.get(metric, 0.0))
            v2_value = float(v2.get(metric, 0.0))
            rows.append(
                {
                    "scope": f"localization_{split}",
                    "metric": metric,
                    "v1": v1_value,
                    "v2": v2_value,
                    "delta": round(v2_value - v1_value, 6),
                    "comparable": True,
                    "reason": "same_cases_and_frozen_split_protocol",
                }
            )
    baseline_patch = {
        str(item.get("metric_id") or ""): item
        for item in _list(baseline.get("metrics"))
    }
    non_comparable = [
        {
            "scope": "repair",
            "metric": "v1_pass_at_1_vs_v2_controlled_verified_repair",
            "v1": _dict(baseline_patch.get("pass_at_1")).get("value"),
            "v2": None,
            "delta": None,
            "comparable": False,
            "reason": "different_case_sets_and_protocols",
        },
        {
            "scope": "reflection",
            "metric": "v1_reflection_uplift_vs_v2_controlled_reflection",
            "v1": _dict(baseline_patch.get("reflection_uplift")).get("value"),
            "v2": None,
            "delta": None,
            "comparable": False,
            "reason": "different_case_sets_and_reflection_authority",
        },
    ]
    return {
        "baseline_ref": str(baseline.get("baseline_ref") or ""),
        "rows": rows,
        "non_comparable_rows": non_comparable,
        "interpretation": (
            "V2 preserves V1 localization metrics on the same split protocol; "
            "repair uplift is not claimed across incompatible benchmarks."
        ),
    }


def _uncertainty_report(
    planner: dict[str, Any], memory: dict[str, Any], patch: dict[str, Any]
) -> dict[str, Any]:
    return {
        "method": "percentile_bootstrap",
        "confidence_level": 0.95,
        "bootstrap_samples": 1000,
        "seed": 1729,
        "planner_task_completion": _grouped_intervals(
            _list(planner.get("runs")), "planner_mode", "task_completed"
        ),
        "planner_blocker_accuracy": _grouped_intervals(
            _list(planner.get("runs")),
            "planner_mode",
            "blocker_classification_correct",
        ),
        "patch_verified_repair": _grouped_intervals(
            _list(patch.get("runs")), "patch_mode", "verified_repair"
        ),
        "patch_regression_safe": _grouped_intervals(
            _list(patch.get("runs")),
            "patch_mode",
            lambda row: str(row.get("full_regression_status") or "") == "pass",
        ),
        "memory_task_completion": _grouped_intervals(
            _list(memory.get("runs")), "mode", "task_completed"
        ),
    }


def _grouped_intervals(
    rows: list[Any],
    group_key: str,
    value: str | Callable[[dict[str, Any]], Any],
) -> dict[str, Any]:
    grouped: dict[str, list[float]] = {}
    for row_value in rows:
        row = _dict(row_value)
        group = str(row.get(group_key) or "")
        raw = value(row) if callable(value) else row.get(value)
        grouped.setdefault(group, []).append(1.0 if bool(raw) else 0.0)
    return {
        group: _bootstrap_interval(values, seed=1729 + index)
        for index, (group, values) in enumerate(sorted(grouped.items()))
    }


def _bootstrap_interval(
    values: list[float], *, seed: int, samples: int = 1000
) -> dict[str, Any]:
    if not values:
        return {"mean": 0.0, "lower": 0.0, "upper": 0.0, "sample_count": 0}
    mean = sum(values) / len(values)
    if len(values) == 1:
        lower = upper = mean
    else:
        rng = random.Random(seed)
        means = []
        for _ in range(samples):
            sample = [values[rng.randrange(len(values))] for _ in values]
            means.append(sum(sample) / len(sample))
        means.sort()
        lower = means[int(0.025 * (len(means) - 1))]
        upper = means[int(0.975 * (len(means) - 1))]
    return {
        "mean": round(mean, 6),
        "lower": round(lower, 6),
        "upper": round(upper, 6),
        "sample_count": len(values),
    }


def _failure_accounting(
    *,
    planner: dict[str, Any],
    memory: dict[str, Any],
    patch: dict[str, Any],
    budgets: dict[str, Any],
    phase6: dict[str, Any],
) -> dict[str, Any]:
    budget_dimensions = _dict(budgets.get("dimensions"))
    budget_rows = [
        row
        for dimension in budget_dimensions.values()
        for row in _list(_dict(dimension).get("runs"))
    ]
    return {
        "planner_expectation_failures": sum(
            not bool(_dict(row).get("task_completed"))
            for row in _list(planner.get("runs"))
        ),
        "memory_task_failures": sum(
            not bool(_dict(row).get("task_completed"))
            for row in _list(memory.get("runs"))
        ),
        "patch_unverified_runs": sum(
            not bool(_dict(row).get("verified_repair"))
            for row in _list(patch.get("runs"))
        ),
        "budget_expected_non_success_runs": sum(
            not bool(
                _dict(row).get("verified_repair")
                or _dict(row).get("task_completed")
            )
            for row in budget_rows
        ),
        "phase6_partial_repositories": _nested(
            phase6, "outcome_evaluation", "outcome_counts", "partial"
        ),
        "phase6_failed_test_processes": _nested(
            phase6,
            "outcome_evaluation",
            "started_test_failure_layer_counts",
            "environment",
        ),
        "policy": "Failures remain in raw artifacts and are included in denominators.",
    }


def _protocol_metadata(
    repository_root: Path, paths: dict[str, Path]
) -> dict[str, Any]:
    localization_protocol = _load_json(paths["localization_protocol"])
    localization_template = _load_json(paths["localization_template"])
    phase6_dataset = _load_json(paths["phase6_dataset"])
    return {
        "evaluation_code_commit": _git_revision(repository_root),
        "working_tree_clean": _git_clean(repository_root),
        "dataset_split_policy": {
            "localization": _dict(localization_protocol.get("splits")),
            "weight_selection": "validation_only",
            "evaluation": ["test", "blind"],
            "unfamiliar_repositories": "20_repository_holdout_not_used_in_phases_1_to_5",
        },
        "datasets": [
            _dataset_record(path, repository_root) for path in paths.values()
            if path.is_file()
        ],
        "localization_source_refs": _localization_source_refs(
            localization_template
        ),
        "unfamiliar_repository_commits": [
            {
                "repo": str(_dict(item).get("repo") or ""),
                "commit": str(_dict(item).get("ref") or ""),
            }
            for item in _list(phase6_dataset.get("runs"))
        ],
        "models": {
            "planner": {
                "provider": "controlled",
                "model": "planner-evaluation",
                "temperature": 0,
                "usage": "deterministic schema and safety contract",
            },
            "patch": {
                "provider": "controlled",
                "model": "sequence-fixture-v1",
                "temperature": 0,
                "usage": "deterministic generation and reflection contract",
            },
            "live_provider": {
                "executed": False,
                "reason": "live_model_quality_outside_deterministic_phase7_acceptance",
            },
        },
        "prompt_contracts": {
            "planner": _source_contract(_llm_replan_prompt),
            "patch_generation": _source_contract(build_patch_prompt),
            "patch_reflection": _source_contract(build_reflection_prompt),
        },
        "run_date_utc": datetime.now(timezone.utc).date().isoformat(),
    }


def _evaluation_paths(root: Path) -> dict[str, Path]:
    return {
        "planner_dataset": root
        / "datasets/planner_evaluation/v2_planner_controlled_cases.json",
        "memory_dataset": root
        / "datasets/memory_evaluation/v2_memory_ablation_cases.json",
        "localization_protocol": root
        / "datasets/localization_v2/split_protocol.json",
        "localization_template": root
        / "datasets/github_cases/mutation_templates.example.json",
        "patch_dataset": root
        / "datasets/patch_evaluation/v2_patch_strategy_controlled_cases.json",
        "system_dataset": root
        / "datasets/system_evaluation/v2_system_ablation_cases.json",
        "phase6_dataset": root
        / "datasets/github_cases/v2_unfamiliar_python_repositories_20.json",
        "baseline_metrics": root / "docs/baseline/baseline_metrics.json",
        "phase6_metrics": root
        / "docs/v2/phase6_unfamiliar_repository_metrics.json",
    }


def _artifact_manifest(publication_root: Path, repo_root: Path) -> dict[str, Any]:
    base = _relative_path(publication_root, repo_root)
    manifest = {
        name: {
            "json": f"{base}/{filenames[0]}",
            "markdown": f"{base}/{filenames[1]}",
        }
        for name, filenames in COMPONENT_ARTIFACTS.items()
    }
    manifest["system"] = {
        "json": f"{base}/phase7_system_metrics.json",
        "markdown": f"{base}/phase7_system_evaluation.md",
    }
    return manifest


def write_phase7_system_report(
    payload: dict[str, Any], output_dir: str | Path
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "phase7_system_metrics.json"
    markdown_path = root / "phase7_system_evaluation.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    markdown_path.write_text(
        render_phase7_system_evaluation(payload), encoding="utf-8"
    )
    return {"json": str(json_path), "markdown": str(markdown_path)}


def publish_phase7_artifacts(
    payload: dict[str, Any],
    *,
    component_paths: dict[str, dict[str, str]],
    publication_dir: str | Path,
    repository_root: str | Path,
    work_dir: str | Path,
) -> dict[str, str]:
    publication_root = Path(publication_dir)
    publication_root.mkdir(parents=True, exist_ok=True)
    replacements = [Path(repository_root).resolve(), Path(work_dir).resolve()]
    for name, paths in component_paths.items():
        json_name, markdown_name = COMPONENT_ARTIFACTS[name]
        source_json = _load_json(paths["json"])
        portable = _portable_value(source_json, replacements)
        (publication_root / json_name).write_text(
            json.dumps(portable, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        markdown = Path(paths["markdown"]).read_text(encoding="utf-8")
        (publication_root / markdown_name).write_text(
            _portable_text(markdown, replacements), encoding="utf-8"
        )
    return write_phase7_system_report(payload, publication_root)


def render_phase7_system_evaluation(payload: dict[str, Any]) -> str:
    metrics = _dict(payload.get("core_metrics"))
    localization = _dict(metrics.get("localization"))
    lines = [
        "# Phase 7 System Evaluation and Ablation",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Reason: `{payload.get('reason')}`",
        f"- Evaluation commit: `{_nested(payload, 'protocol', 'evaluation_code_commit')}`",
        f"- Working tree clean: `{str(bool(_nested(payload, 'protocol', 'working_tree_clean'))).lower()}`",
        "",
        "## Required Comparisons",
        "",
        "| Comparison | Complete | Raw Artifact |",
        "| --- | --- | --- |",
    ]
    completeness = _dict(payload.get("comparison_complete"))
    for name, comparison_value in _dict(payload.get("required_comparisons")).items():
        comparison = _dict(comparison_value)
        lines.append(
            f"| {comparison.get('label', name)} | "
            f"{'pass' if completeness.get(name) else 'fail'} | "
            f"[{name}]({_artifact_markdown_link(comparison.get('artifact', ''))}) |"
        )
    lines.extend(
        [
            "",
            "## Localization Metrics",
            "",
            "| Cases | Top-1 | Top-3 | Top-5 | MRR | MAP | Mean Latency ms |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            "| "
            + " | ".join(
                [
                    str(localization.get("case_count", 0)),
                    _number(localization.get("top1")),
                    _number(localization.get("top3")),
                    _number(localization.get("top5")),
                    _number(localization.get("mrr")),
                    _number(localization.get("map")),
                    _number(localization.get("mean_localization_latency_ms")),
                ]
            )
            + " |",
            "",
            "## Patch Strategies",
            "",
            "| Mode | Candidate Success | AST Valid | Safety Pass | Test Pass | Regression Safe | Verified | Reflection Recovery | Runtime ms |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for mode, item_value in _dict(metrics.get("patch")).items():
        item = _dict(item_value)
        lines.append(
            "| "
            + " | ".join(
                [
                    mode,
                    _number(item.get("candidate_generation_success_rate")),
                    _number(item.get("ast_valid_patch_rate")),
                    _number(item.get("safety_gate_pass_rate")),
                    _number(item.get("targeted_test_pass_rate")),
                    _number(item.get("regression_safe_patch_rate")),
                    _number(item.get("verified_repair_rate")),
                    _number(item.get("reflection_recovery_rate")),
                    _number(item.get("average_runtime_ms")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Planner Strategies",
            "",
            "| Mode | Completion | Invalid Actions | Blocker Accuracy | Avg Actions | Runtime ms | Tokens | Cost USD | Repeated Action |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for mode, item_value in _dict(metrics.get("planner")).items():
        item = _dict(item_value)
        lines.append(
            "| "
            + " | ".join(
                [
                    mode,
                    _number(item.get("task_completion_rate")),
                    str(item.get("invalid_action_count", 0)),
                    _number(item.get("blocker_identification_accuracy")),
                    _number(item.get("average_action_count")),
                    _number(item.get("average_runtime_ms")),
                    str(item.get("llm_total_tokens", 0)),
                    f"{float(item.get('llm_estimated_cost_usd', 0.0)):.8f}",
                    _number(item.get("repeated_action_rate", 0.0)),
                ]
            )
            + " |"
        )
    lines.extend(["", "## V1 vs V2 Comparable Metrics", ""])
    lines.extend(
        [
            "| Scope | Metric | V1 | V2 | Delta |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
    )
    for row_value in _list(_dict(payload.get("v1_v2_comparison")).get("rows")):
        row = _dict(row_value)
        lines.append(
            f"| {row.get('scope')} | {row.get('metric')} | "
            f"{_number(row.get('v1'))} | {_number(row.get('v2'))} | "
            f"{_number(row.get('delta'))} |"
        )
    lines.extend(["", "## Failure Accounting", ""])
    for key, value in _dict(payload.get("failure_accounting")).items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Claim Boundaries", ""])
    lines.extend(f"- {item}" for item in _list(payload.get("claim_boundaries")))
    lines.extend(["", "## Raw Artifacts", ""])
    for name, item_value in _dict(payload.get("artifacts")).items():
        item = _dict(item_value)
        lines.append(
            f"- {name}: [JSON]({_artifact_markdown_link(item.get('json', ''))}), "
            f"[Markdown]({_artifact_markdown_link(item.get('markdown', ''))})"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run Phase 7 system evaluation and required ablations."
    )
    parser.add_argument("output_dir")
    parser.add_argument("--repository-root", default=".")
    parser.add_argument("--publication-dir")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args(argv)
    report = run_phase7_system_evaluation(
        args.repository_root,
        args.output_dir,
        publication_dir=args.publication_dir,
    )
    publication_root = Path(args.publication_dir or args.output_dir)
    path = publication_root / (
        "phase7_system_metrics.json"
        if args.format == "json"
        else "phase7_system_evaluation.md"
    )
    print(path.read_text(encoding="utf-8"))
    if args.require_pass and report.get("status") != "pass":
        raise SystemExit(1)


def _comparison_has_results(name: str, payload: dict[str, Any]) -> bool:
    if name in {"patch_strategy", "planner_strategy", "memory"}:
        return bool(_dict(payload.get("results")))
    if name == "graph":
        return bool(_dict(payload.get("with_graph"))) and bool(
            _dict(payload.get("without_graph"))
        )
    if name == "dynamic_evidence":
        return bool(_dict(payload.get("with_dynamic"))) and bool(
            _dict(payload.get("without_dynamic"))
        )
    if name in {"reflection", "top_k_context"}:
        return len(_list(payload.get("results"))) >= 2
    if name == "action_and_candidate_budget":
        return len(_list(payload.get("action_results"))) >= 2 and len(
            _list(payload.get("candidate_results"))
        ) >= 2
    return False


def _localization_source_refs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    refs = {}
    for case_value in _list(payload.get("cases")):
        for source_value in _list(_dict(case_value).get("sources")):
            source = _dict(source_value)
            key = (
                str(source.get("owner") or ""),
                str(source.get("repo") or ""),
                str(source.get("ref") or ""),
            )
            refs[key] = {
                "repo": "/".join(key[:2]),
                "ref": key[2],
                "content_sha256_verified": bool(source.get("sha256")),
            }
    return [refs[key] for key in sorted(refs)]


def _source_contract(function: Callable[..., Any]) -> dict[str, str]:
    source = inspect.getsource(function).encode("utf-8")
    return {
        "callable": f"{function.__module__}.{function.__name__}",
        "sha256": hashlib.sha256(source).hexdigest(),
    }


def _dataset_record(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": _relative_path(path, root),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def _git_revision(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def _git_clean(root: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and not result.stdout.strip()


def _normalize_component_paths(paths: dict[str, str]) -> dict[str, str]:
    values = list(paths.values())
    return {
        "json": next(value for value in values if value.endswith(".json")),
        "markdown": next(value for value in values if value.endswith(".md")),
    }


def _portable_value(value: Any, replacements: list[Path]) -> Any:
    if isinstance(value, dict):
        return {key: _portable_value(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_portable_value(item, replacements) for item in value]
    if isinstance(value, str):
        return _portable_text(value, replacements)
    return value


def _portable_text(value: str, replacements: list[Path]) -> str:
    text = value
    for path in replacements:
        variants = {str(path), path.as_posix()}
        for variant in sorted(variants, key=len, reverse=True):
            text = text.replace(variant, ".")
    return text.replace("\\", "/")


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _artifact_markdown_link(path: Any) -> str:
    return Path(str(path or "")).name


def _load_json(path: str | Path) -> dict[str, Any]:
    return _dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _number(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "0.0000"


def _nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        current = _dict(current).get(key)
    return current


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


if __name__ == "__main__":
    main()
