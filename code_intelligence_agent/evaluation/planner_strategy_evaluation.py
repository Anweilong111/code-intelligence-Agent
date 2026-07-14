from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from code_intelligence_agent.agents.action_registry import action_execution_policy
from code_intelligence_agent.agents.controller import build_agent_controller_plan
from code_intelligence_agent.agents.llm_client import (
    LLMRequestError,
    LLMResponse,
)


PLANNER_MODES = ["rule", "llm", "hybrid"]


class _ControlledPlannerClient:
    def __init__(self, case: dict[str, Any]) -> None:
        self.case = case

    def complete(self, prompt: str) -> LLMResponse:
        del prompt
        mode = str(self.case.get("client_mode") or "pass")
        metadata = {
            "status": "pass",
            "provider": "controlled",
            "model": "planner-evaluation",
            "latency_ms": 1,
            "usage": {
                "prompt_tokens": 80,
                "completion_tokens": 40,
                "total_tokens": 120,
            },
            "cost_estimate": {
                "available": True,
                "estimated_cost_usd": 0.0012,
            },
        }
        if mode == "network_error":
            raise LLMRequestError(
                "url_error",
                "controlled network error",
                {
                    **metadata,
                    "status": "error",
                    "error_reason": "network unavailable",
                },
            )
        if mode == "invalid_json":
            return LLMResponse(text="not-json", metadata=metadata)
        if mode == "schema_error":
            return LLMResponse(
                text=json.dumps(
                    {
                        "selected_action": "generate_hybrid_patch_candidates",
                        "reason": "The response intentionally omits required fields.",
                        "next_plan": "Attempt candidate generation.",
                    }
                ),
                metadata=metadata,
            )
        proposal = {
            "selected_action": "generate_llm_patch_candidates",
            "arguments": {},
            "reason": "Controlled planner proposal.",
            "confidence": 0.9,
            "risk": "medium",
            "required_evidence": ["repository_test_fault_localization.json"],
            "expected_outcome": "A registered action produces verifiable evidence.",
            "fallback_action": "generate_llm_patch_candidates",
            "termination_condition": "Stop on verified success, blocker, or budget exhaustion.",
            "memory_used": ["repo_memory"],
            "next_plan": "Execute one action, observe evidence, and replan.",
        }
        proposal.update(_dict(self.case.get("proposal")))
        return LLMResponse(text=json.dumps(proposal), metadata=metadata)


def evaluate_planner_strategies(dataset_path: str | Path) -> dict[str, Any]:
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    cases = [_dict(item) for item in _list(dataset.get("cases"))]
    rows = []
    for case in cases:
        for mode in PLANNER_MODES:
            summary = _controlled_summary(case)
            client = None if mode == "rule" else _ControlledPlannerClient(case)
            started = time.perf_counter()
            controller = build_agent_controller_plan(
                summary,
                llm_replan_client=client,
                planner_mode=mode,
            )
            runtime_ms = round((time.perf_counter() - started) * 1000, 4)
            selected = str(_dict(controller.get("selected_action")).get("id") or "")
            expected = str(_dict(case.get("expected_actions")).get(mode) or "")
            classification = _planner_classification(controller)
            expected_classification = str(
                _dict(case.get("expected_classification")).get(mode) or "none"
            )
            metrics = _dict(controller.get("planner_metrics"))
            repeated_action = _selected_action_repeated(summary, selected)
            rows.append(
                {
                    "case": str(case.get("name") or ""),
                    "planner_mode": mode,
                    "selected_action": selected,
                    "expected_action": expected,
                    "task_completed": selected == expected,
                    "classification": classification,
                    "expected_classification": expected_classification,
                    "blocker_classification_correct": (
                        classification == expected_classification
                    ),
                    "invalid_action_count": int(
                        metrics.get("invalid_action_count", 0)
                    ),
                    "selected_action_registered": bool(
                        action_execution_policy(selected).get("registered")
                    ),
                    "repeated_action": repeated_action,
                    "action_count": 1,
                    "runtime_ms": runtime_ms,
                    "llm_total_tokens": int(metrics.get("llm_total_tokens", 0)),
                    "llm_estimated_cost_usd": float(
                        metrics.get("llm_estimated_cost_usd", 0.0)
                    ),
                    "safety_gate_rejection_count": int(
                        metrics.get("safety_gate_rejection_count", 0)
                    ),
                    "fallback_count": int(metrics.get("fallback_count", 0)),
                    "planner_resolution": _dict(
                        controller.get("planner_resolution")
                    ),
                }
            )
    strategies = {
        mode: _aggregate_mode([row for row in rows if row["planner_mode"] == mode])
        for mode in PLANNER_MODES
    }
    passed = all(
        row["task_completed"] and row["blocker_classification_correct"]
        for row in rows
    )
    return {
        "schema_version": 1,
        "suite_name": str(dataset.get("suite_name") or ""),
        "status": "pass" if passed else "fail",
        "reason": (
            "all_controlled_planner_expectations_met"
            if passed
            else "planner_expectation_mismatch"
        ),
        "case_count": len(cases),
        "run_count": len(rows),
        "strategies": strategies,
        "runs": rows,
        "limitations": [
            "This Phase 2 suite evaluates planning and safety behavior, not repository repair success.",
            "LLM responses are deterministic offline fixtures; live-provider quality is evaluated separately.",
        ],
    }


def _controlled_summary(case: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "repo": "controlled/planner-case",
        "repo_spec": "controlled/planner-case",
        "repository_patch_generation_mode": "llm",
        "analysis_readiness": {
            "current_stage": "phase2_dynamic_fault_localization",
            "next_stage": "phase3_patch_validation",
            "blocker": "",
            "can_attempt_patch_repair": True,
        },
        "fault_localization": {
            "mode": "dynamic",
            "status": "pass",
            "top_function": "pkg.core.load_user",
            "rankings": [
                {"function": "pkg.core.load_user", "final_score": 0.91}
            ],
        },
        "repository_llm_patch_generation_audit": {
            "status": "ready",
            "provider": "controlled",
            "model": "planner-evaluation",
        },
    }
    summary_mode = str(case.get("summary_mode") or "")
    if summary_mode == "budget_exhausted":
        summary["agent_invocation"] = {
            "auto_controller_max_actions": 1,
            "planner_mode": "hybrid",
        }
        summary["agent_auto_action_count"] = 1
    if summary_mode == "repeated_action":
        summary["agent_auto_trace"] = [
            {
                "observe_stage": "phase2_dynamic_fault_localization",
                "observe_blocker": "",
                "plan_selected_action": "generate_hybrid_patch_candidates",
            }
        ]
    if summary_mode == "environment_diagnosis":
        summary["analysis_readiness"] = {
            "current_stage": "phase2_static_graph_fault_localization",
            "next_stage": "phase3_repository_test_execution",
            "blocker": "test_execution_failed",
            "dynamic_evidence_level": "collection_failure",
            "planned_repository_test_result_status": "fail",
            "planned_repository_test_failure_category": "collection_failure",
            "planned_repository_test_result_errors": 1,
        }
        summary["fault_localization"] = {
            "mode": "static_fallback",
            "status": "pass",
            "top_function": "pkg.core.load_user",
        }
    return summary


def _planner_classification(controller: dict[str, Any]) -> str:
    advisor = _dict(controller.get("llm_replan_advisor"))
    provider_class = str(advisor.get("provider_failure_class") or "")
    if provider_class:
        return provider_class
    gate_status = str(_dict(advisor.get("safety_gate")).get("status") or "")
    if gate_status in {"blocked", "requires_confirmation", "advisory_only"}:
        return "safety_gate"
    return "none"


def _aggregate_mode(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    return {
        "run_count": count,
        "task_completion_rate": _ratio(
            sum(bool(row["task_completed"]) for row in rows), count
        ),
        "invalid_action_count": sum(row["invalid_action_count"] for row in rows),
        "valid_action_rate": _ratio(
            sum(bool(row["selected_action_registered"]) for row in rows), count
        ),
        "repeated_action_rate": _ratio(
            sum(bool(row["repeated_action"]) for row in rows), count
        ),
        "average_action_count": round(
            sum(row["action_count"] for row in rows) / count, 4
        ) if count else 0.0,
        "blocker_identification_accuracy": _ratio(
            sum(bool(row["blocker_classification_correct"]) for row in rows),
            count,
        ),
        "average_runtime_ms": round(
            sum(row["runtime_ms"] for row in rows) / count, 4
        ) if count else 0.0,
        "llm_total_tokens": sum(row["llm_total_tokens"] for row in rows),
        "llm_estimated_cost_usd": round(
            sum(row["llm_estimated_cost_usd"] for row in rows), 8
        ),
        "safety_gate_rejection_count": sum(
            row["safety_gate_rejection_count"] for row in rows
        ),
        "fallback_count": sum(row["fallback_count"] for row in rows),
    }


def _selected_action_repeated(summary: dict[str, Any], selected_action: str) -> bool:
    current_stage = str(_dict(summary.get("analysis_readiness")).get("current_stage") or "")
    for item_value in reversed(_list(summary.get("agent_auto_trace"))):
        item = _dict(item_value)
        if str(item.get("plan_selected_action") or "") != selected_action:
            continue
        previous_stage = str(item.get("observe_stage") or "")
        return not previous_stage or previous_stage == current_stage
    return False


def render_planner_strategy_evaluation_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Planner Strategy Evaluation",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Reason: `{payload.get('reason')}`",
        f"- Cases: {payload.get('case_count', 0)}",
        f"- Runs: {payload.get('run_count', 0)}",
        "",
        "## Strategy Metrics",
        "",
        "| Planner | Completion | Valid Action | Invalid Proposals | Repeated Action | Avg Actions | Blocker Accuracy | Avg Runtime (ms) | Tokens | Cost (USD) | Safety Rejects | Fallbacks |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode in PLANNER_MODES:
        metrics = _dict(_dict(payload.get("strategies")).get(mode))
        lines.append(
            "| "
            + " | ".join(
                [
                    mode,
                    f"{float(metrics.get('task_completion_rate', 0.0)):.4f}",
                    f"{float(metrics.get('valid_action_rate', 0.0)):.4f}",
                    str(metrics.get("invalid_action_count", 0)),
                    f"{float(metrics.get('repeated_action_rate', 0.0)):.4f}",
                    f"{float(metrics.get('average_action_count', 0.0)):.4f}",
                    f"{float(metrics.get('blocker_identification_accuracy', 0.0)):.4f}",
                    f"{float(metrics.get('average_runtime_ms', 0.0)):.4f}",
                    str(metrics.get("llm_total_tokens", 0)),
                    f"{float(metrics.get('llm_estimated_cost_usd', 0.0)):.8f}",
                    str(metrics.get("safety_gate_rejection_count", 0)),
                    str(metrics.get("fallback_count", 0)),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Limitations", ""])
    for item in _list(payload.get("limitations")):
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def write_planner_strategy_evaluation(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "planner_strategy_evaluation.json"
    markdown_path = root / "planner_strategy_evaluation.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_planner_strategy_evaluation_markdown(payload),
        encoding="utf-8",
    )
    return {"json": str(json_path), "markdown": str(markdown_path)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate Agent planner strategies.")
    parser.add_argument("dataset")
    parser.add_argument("output_dir")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args(argv)
    payload = evaluate_planner_strategies(args.dataset)
    write_planner_strategy_evaluation(payload, args.output_dir)
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_planner_strategy_evaluation_markdown(payload))
    if args.require_pass and payload["status"] != "pass":
        raise SystemExit(1)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


if __name__ == "__main__":
    main()
