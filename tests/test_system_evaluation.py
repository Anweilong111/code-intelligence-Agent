import json
from pathlib import Path

from code_intelligence_agent.evaluation.system_evaluation import (
    _artifact_manifest,
    _evaluation_paths,
    build_phase7_system_report,
    render_phase7_system_evaluation,
    write_phase7_system_report,
)


def test_phase7_system_report_requires_all_comparisons_and_preserves_boundaries(
    tmp_path,
):
    root = Path(__file__).resolve().parents[1]
    paths = _evaluation_paths(root)
    artifact_manifest = _artifact_manifest(
        root / "docs/v2/phase7_artifacts", root
    )
    planner = {
        "status": "pass",
        "strategies": {
            mode: {
                "task_completion_rate": 1.0,
                "valid_action_rate": 1.0,
                "invalid_action_count": 0,
                "repeated_action_rate": 0.0,
                "average_action_count": 1.0,
                "blocker_identification_accuracy": 1.0,
                "average_runtime_ms": 1.0,
                "llm_total_tokens": 0 if mode == "rule" else 120,
                "llm_estimated_cost_usd": 0.0 if mode == "rule" else 0.0012,
            }
            for mode in ("rule", "llm", "hybrid")
        },
        "runs": [
            {
                "planner_mode": mode,
                "task_completed": True,
                "blocker_classification_correct": True,
            }
            for mode in ("rule", "llm", "hybrid")
        ],
    }
    memory = {
        "status": "pass",
        "metrics": {
            "without_memory": {"task_completion_rate": 0.0},
            "with_memory": {"task_completion_rate": 1.0},
        },
        "runs": [
            {"mode": "without_memory", "task_completed": False},
            {"mode": "with_memory", "task_completed": True},
        ],
    }
    fusion = _localization_metrics("fusion", 1.0)
    localization = {
        "non_regression_passed": True,
        "split_results": {
            split: {
                "v1": _localization_metrics("legacy_v1", 1.0),
                "v2": _localization_metrics("fusion", 1.0),
            }
            for split in ("validation", "test", "blind")
        },
        "ablation_results": [
            fusion,
            _localization_metrics("without_graph", 0.8),
            _localization_metrics("without_dynamic", 0.7),
        ],
    }
    patch = {
        "status": "pass",
        "strategies": {
            mode: {
                "candidate_generation_success_rate": 1.0,
                "ast_valid_patch_rate": 1.0,
                "safety_gate_pass_rate": 1.0,
                "targeted_test_pass_rate": 1.0,
                "regression_safe_patch_rate": 1.0,
                "verified_repair_rate": 1.0,
                "reflection_recovery_rate": 0.0,
                "average_runtime_ms": 10.0,
            }
            for mode in ("rule", "llm", "hybrid")
        },
        "runs": [
            {
                "patch_mode": mode,
                "verified_repair": True,
                "full_regression_status": "pass",
            }
            for mode in ("rule", "llm", "hybrid")
        ],
    }
    budgets = {
        "status": "pass",
        "dimensions": {
            "reflection": {"runs": _budget_rows()},
            "top_k_context": {"runs": _budget_rows()},
            "candidate_budget": {"runs": _budget_rows()},
            "action_budget": {
                "runs": [
                    {"action_budget": 1, "task_completed": False},
                    {"action_budget": 3, "task_completed": True},
                ]
            },
        },
    }
    baseline = json.loads(paths["baseline_metrics"].read_text(encoding="utf-8"))
    phase6 = json.loads(paths["phase6_metrics"].read_text(encoding="utf-8"))

    report = build_phase7_system_report(
        repository_root=root,
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

    assert report["status"] == "pass"
    assert all(report["comparison_complete"].values())
    assert len(report["v1_v2_comparison"]["rows"]) == 15
    assert all(
        row["comparable"] for row in report["v1_v2_comparison"]["rows"]
    )
    assert all(
        not row["comparable"]
        for row in report["v1_v2_comparison"]["non_comparable_rows"]
    )
    assert report["protocol"]["prompt_contracts"]["planner"]["sha256"]
    assert len(report["protocol"]["unfamiliar_repository_commits"]) == 20
    assert "Failures remain" in report["failure_accounting"]["policy"]

    output_paths = write_phase7_system_report(report, tmp_path)
    assert Path(output_paths["json"]).is_file()
    assert Path(output_paths["markdown"]).is_file()
    markdown = render_phase7_system_evaluation(report)
    assert "Rule Patch vs LLM Patch vs Hybrid Patch" in markdown
    assert "V1 vs V2 Comparable Metrics" in markdown
    assert "Controlled LLM fixtures" in markdown


def _localization_metrics(profile, value):
    return {
        "profile": profile,
        "case_count": 20,
        "top1": value,
        "top3": value,
        "top5": value,
        "mrr": value,
        "map": value,
        "mean_localization_latency_ms": 2.0,
    }


def _budget_rows():
    return [
        {"value": 1, "verified_repair": False},
        {"value": 3, "verified_repair": True},
    ]
