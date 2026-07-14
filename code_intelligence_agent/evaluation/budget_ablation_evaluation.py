from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from code_intelligence_agent.agents.action_registry import action_execution_policy
from code_intelligence_agent.agents.controller import build_agent_controller_plan
from code_intelligence_agent.evaluation.patch_strategy_evaluation import (
    evaluate_patch_case,
)


def evaluate_budget_ablations(
    system_dataset_path: str | Path,
    patch_dataset_path: str | Path,
) -> dict[str, Any]:
    system_dataset = _load_json(system_dataset_path)
    patch_dataset = _load_json(patch_dataset_path)
    reflection_case_name = str(system_dataset.get("reflection_case") or "")
    reflection_case = next(
        (
            _dict(item)
            for item in _list(patch_dataset.get("cases"))
            if str(_dict(item).get("name") or "") == reflection_case_name
        ),
        {},
    )
    if not reflection_case:
        raise ValueError(
            f"Reflection ablation case not found: {reflection_case_name}"
        )

    reflection_runs = [
        _patch_budget_row(
            "reflection_rounds",
            rounds,
            evaluate_patch_case(
                reflection_case,
                mode="llm",
                overrides={"reflection_rounds": rounds},
            ),
        )
        for rounds in _positive_or_zero_ints(system_dataset.get("reflection_rounds"))
    ]
    candidate_case = _dict(system_dataset.get("candidate_budget_case"))
    candidate_runs = [
        _patch_budget_row(
            "candidate_budget",
            budget,
            evaluate_patch_case(
                candidate_case,
                mode="llm",
                overrides={
                    "candidate_limit": budget,
                    "validation_limit": budget,
                    "reflection_rounds": 0,
                },
            ),
        )
        for budget in _positive_ints(system_dataset.get("candidate_budgets"))
    ]
    top_k_case = _dict(system_dataset.get("top_k_context_case"))
    top_k_runs = [
        _patch_budget_row(
            "top_k_context",
            top_k,
            evaluate_patch_case(
                top_k_case,
                mode="llm",
                overrides={
                    "top_k_functions": top_k,
                    "candidate_limit": 1,
                    "validation_limit": 1,
                    "reflection_rounds": 0,
                },
            ),
        )
        for top_k in _positive_ints(system_dataset.get("top_k_context_sizes"))
    ]
    action_case = _dict(system_dataset.get("action_budget_case"))
    action_runs = [
        _evaluate_action_budget(action_case, budget=budget)
        for budget in _positive_ints(system_dataset.get("action_budgets"))
    ]

    gates = {
        "reflection_changes_outcome": _budget_outcome_changes(reflection_runs),
        "candidate_budget_changes_outcome": _budget_outcome_changes(candidate_runs),
        "top_k_context_changes_outcome": _budget_outcome_changes(top_k_runs),
        "action_budget_changes_outcome": _action_outcome_changes(action_runs),
        "all_controller_actions_registered": all(
            bool(row.get("all_actions_registered")) for row in action_runs
        ),
        "no_repeated_controller_action": all(
            int(row.get("repeated_action_count", 0)) == 0 for row in action_runs
        ),
    }
    status = "pass" if all(gates.values()) else "fail"
    return {
        "schema_version": 1,
        "suite_name": str(system_dataset.get("suite_name") or ""),
        "status": status,
        "reason": (
            "all_budget_ablation_expectations_met"
            if status == "pass"
            else "budget_ablation_expectation_failed"
        ),
        "protocol": _dict(system_dataset.get("protocol")),
        "dimensions": {
            "reflection": {
                "independent_variable": "reflection_rounds",
                "runs": reflection_runs,
            },
            "candidate_budget": {
                "independent_variable": "candidate_limit",
                "runs": candidate_runs,
            },
            "top_k_context": {
                "independent_variable": "top_k_functions",
                "runs": top_k_runs,
            },
            "action_budget": {
                "independent_variable": "max_actions",
                "runs": action_runs,
            },
        },
        "acceptance_gates": gates,
        "limitations": [
            "Patch outcomes use deterministic offline LLM responses and real pytest sandbox validation.",
            "Action-budget runs use the production controller and Action Registry with deterministic state-transition tool outcomes.",
            "The controlled action harness measures budget sensitivity; it is not a GitHub repair-success estimate.",
        ],
    }


def _patch_budget_row(dimension: str, value: int, run: dict[str, Any]) -> dict[str, Any]:
    candidate_count = int(run.get("candidate_count", 0))
    return {
        "dimension": dimension,
        "value": value,
        "case": str(run.get("case") or ""),
        "candidate_count": candidate_count,
        "candidate_generation_success": bool(run.get("candidate_generated")),
        "ast_valid_patch_rate": _ratio(
            int(run.get("ast_valid_candidate_count", 0)), candidate_count
        ),
        "safety_gate_pass_rate": _ratio(
            int(run.get("safety_pass_candidate_count", 0)), candidate_count
        ),
        "targeted_test_passed": bool(run.get("targeted_test_passed")),
        "regression_safe": str(run.get("full_regression_status") or "") == "pass",
        "verified_repair": bool(run.get("verified_repair")),
        "reflection_recovered": bool(run.get("reflection_recovered")),
        "generator": str(run.get("best_generator_family") or ""),
        "runtime_ms": float(run.get("runtime_ms", 0.0)),
        "raw_run": run,
    }


def _evaluate_action_budget(case: dict[str, Any], *, budget: int) -> dict[str, Any]:
    expected_sequence = [
        str(item) for item in _list(case.get("required_action_sequence"))
    ]
    states = _controlled_action_states()
    if len(states) != len(expected_sequence):
        raise ValueError("Controlled action states do not match required action sequence.")
    started = time.perf_counter()
    trace: list[dict[str, Any]] = []
    selected_actions: list[str] = []
    repeated_count = 0
    invalid_count = 0
    total_tokens = 0
    total_cost = 0.0
    stop_reason = ""

    for index, expected_action in enumerate(expected_sequence):
        if len(selected_actions) >= budget:
            stop_reason = "action_budget_exhausted"
            break
        summary = states[index]
        controller = build_agent_controller_plan(
            summary,
            planner_mode=str(case.get("planner_mode") or "rule"),
        )
        selected = _dict(controller.get("selected_action"))
        action_id = str(selected.get("id") or "")
        policy = action_execution_policy(action_id)
        metrics = _dict(controller.get("planner_metrics"))
        total_tokens += int(metrics.get("llm_total_tokens", 0))
        total_cost += float(metrics.get("llm_estimated_cost_usd", 0.0))
        registered = bool(policy.get("registered"))
        executable = bool(selected.get("executable_now", False))
        valid = registered and executable and action_id == expected_action
        if action_id in selected_actions:
            repeated_count += 1
        if not valid:
            invalid_count += 1
        selected_actions.append(action_id)
        next_stage = (
            str(states[index + 1]["analysis_readiness"]["current_stage"])
            if index + 1 < len(states)
            else "phase4_evaluation_complete"
        )
        trace.append(
            {
                "iteration": index + 1,
                "observe": {
                    "stage": str(
                        _dict(summary.get("analysis_readiness")).get(
                            "current_stage"
                        )
                        or ""
                    ),
                    "blocker": str(
                        _dict(summary.get("analysis_readiness")).get("blocker")
                        or ""
                    ),
                },
                "plan": {
                    "selected_action": action_id,
                    "expected_action": expected_action,
                    "reason": str(selected.get("reason") or ""),
                },
                "act": {
                    "registered": registered,
                    "executable_now": executable,
                    "execution_mode": str(case.get("execution_mode") or ""),
                    "controlled_tool_outcome": "completed",
                },
                "verify": {
                    "valid_action": valid,
                    "next_stage": next_stage,
                    "progress": valid,
                },
                "reflect": {
                    "status": "verified_progress" if valid else "invalid_action",
                    "replan": index + 1 < len(expected_sequence),
                },
            }
        )
        if not valid:
            stop_reason = "invalid_or_unexpected_action"
            break

    completed = selected_actions == expected_sequence
    if completed:
        stop_reason = "task_completed"
    elif not stop_reason:
        stop_reason = "action_budget_exhausted"
    elapsed_ms = round((time.perf_counter() - started) * 1000, 4)
    action_count = len(selected_actions)
    return {
        "case": str(case.get("name") or ""),
        "action_budget": budget,
        "task_completed": completed,
        "selected_actions": selected_actions,
        "required_action_sequence": expected_sequence,
        "action_count": action_count,
        "valid_action_rate": _ratio(action_count - invalid_count, action_count),
        "invalid_action_count": invalid_count,
        "repeated_action_count": repeated_count,
        "repeated_action_rate": _ratio(repeated_count, action_count),
        "all_actions_registered": all(
            bool(_dict(item.get("act")).get("registered")) for item in trace
        ),
        "stop_reason": stop_reason,
        "runtime_ms": elapsed_ms,
        "llm_total_tokens": total_tokens,
        "llm_estimated_cost_usd": round(total_cost, 8),
        "trace": trace,
    }


def _controlled_action_states() -> list[dict[str, Any]]:
    base = {
        "repo": "controlled/action-budget",
        "repo_spec": "controlled/action-budget",
        "output_dir": "outputs/controlled-action-budget",
        "repository_patch_generation_mode": "llm",
        "repository_llm_patch_generation_audit": {
            "status": "ready",
            "provider": "controlled",
            "model": "planner-evaluation",
        },
        "fault_localization": {
            "mode": "dynamic",
            "status": "pass",
            "top_function": "pkg.core.load_user",
            "rankings": [
                {"function": "pkg.core.load_user", "final_score": 0.91}
            ],
        },
    }
    return [
        {
            **base,
            "analysis_readiness": {
                "current_stage": "phase2_dynamic_fault_localization",
                "next_stage": "phase3_patch_validation",
                "blocker": "",
                "can_attempt_patch_repair": True,
            },
        },
        {
            **base,
            "analysis_readiness": {
                "current_stage": "phase3_patch_validation",
                "next_stage": "phase3_patch_validation",
                "blocker": "patch_validation_not_repair_ready:no_candidate_passed",
                "can_attempt_patch_repair": True,
                "patch_validation_status": "fail",
                "patch_candidates_status": "pass",
                "repair_ready": False,
            },
            "reflection_summary": {
                "reflection_candidate_count": 0,
                "max_depth_executed": 0,
            },
        },
        {
            **base,
            "analysis_readiness": {
                "current_stage": "phase3_patch_validation",
                "next_stage": "phase4_evaluation",
                "blocker": "",
                "can_attempt_patch_repair": True,
                "patch_validation_status": "pass",
                "patch_candidates_status": "pass",
                "repair_ready": True,
            },
            "reflection_summary": {
                "reflection_candidate_count": 1,
                "max_depth_executed": 1,
            },
        },
    ]


def render_budget_ablation_markdown(payload: dict[str, Any]) -> str:
    dimensions = _dict(payload.get("dimensions"))
    lines = [
        "# Phase 7 Budget Ablation Evaluation",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Reason: `{payload.get('reason')}`",
        "- Patch success authority: targeted pytest plus full regression pytest.",
        "- Action harness: production controller and Action Registry with controlled tool outcomes.",
    ]
    for name in ("reflection", "candidate_budget", "top_k_context"):
        lines.extend(
            [
                "",
                f"## {name.replace('_', ' ').title()}",
                "",
                "| Value | Candidates | AST Valid | Safety Pass | Target Pass | Regression Safe | Verified | Reflection | Runtime ms |",
                "| ---: | ---: | ---: | ---: | --- | --- | --- | --- | ---: |",
            ]
        )
        for row_value in _list(_dict(dimensions.get(name)).get("runs")):
            row = _dict(row_value)
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(row.get("value", 0)),
                        str(row.get("candidate_count", 0)),
                        f"{float(row.get('ast_valid_patch_rate', 0.0)):.4f}",
                        f"{float(row.get('safety_gate_pass_rate', 0.0)):.4f}",
                        str(bool(row.get("targeted_test_passed"))).lower(),
                        str(bool(row.get("regression_safe"))).lower(),
                        str(bool(row.get("verified_repair"))).lower(),
                        str(bool(row.get("reflection_recovered"))).lower(),
                        f"{float(row.get('runtime_ms', 0.0)):.4f}",
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Action Budget",
            "",
            "| Budget | Completed | Actions | Valid Action Rate | Repeated Rate | Stop Reason | Runtime ms |",
            "| ---: | --- | ---: | ---: | ---: | --- | ---: |",
        ]
    )
    for row_value in _list(_dict(dimensions.get("action_budget")).get("runs")):
        row = _dict(row_value)
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("action_budget", 0)),
                    str(bool(row.get("task_completed"))).lower(),
                    str(row.get("action_count", 0)),
                    f"{float(row.get('valid_action_rate', 0.0)):.4f}",
                    f"{float(row.get('repeated_action_rate', 0.0)):.4f}",
                    str(row.get("stop_reason") or ""),
                    f"{float(row.get('runtime_ms', 0.0)):.4f}",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Acceptance Gates", ""])
    for name, passed in _dict(payload.get("acceptance_gates")).items():
        lines.append(f"- `{name}`: {'pass' if passed else 'fail'}")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in _list(payload.get("limitations")))
    return "\n".join(lines) + "\n"


def write_budget_ablation(
    payload: dict[str, Any], output_dir: str | Path
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "budget_ablation_evaluation.json"
    markdown_path = root / "budget_ablation_evaluation.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    markdown_path.write_text(
        render_budget_ablation_markdown(payload), encoding="utf-8"
    )
    return {"json": str(json_path), "markdown": str(markdown_path)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate reflection, Top-k, action, and candidate budgets."
    )
    parser.add_argument("system_dataset")
    parser.add_argument("patch_dataset")
    parser.add_argument("output_dir")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args(argv)
    payload = evaluate_budget_ablations(args.system_dataset, args.patch_dataset)
    paths = write_budget_ablation(payload, args.output_dir)
    print(Path(paths[args.format]).read_text(encoding="utf-8"))
    if args.require_pass and payload.get("status") != "pass":
        raise SystemExit(1)


def _budget_outcome_changes(rows: list[dict[str, Any]]) -> bool:
    outcomes = {bool(row.get("verified_repair")) for row in rows}
    return outcomes == {False, True}


def _action_outcome_changes(rows: list[dict[str, Any]]) -> bool:
    outcomes = {bool(row.get("task_completed")) for row in rows}
    return outcomes == {False, True}


def _load_json(path: str | Path) -> dict[str, Any]:
    return _dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _positive_ints(value: Any) -> list[int]:
    return [number for number in _positive_or_zero_ints(value) if number > 0]


def _positive_or_zero_ints(value: Any) -> list[int]:
    return [max(0, int(item)) for item in _list(value)]


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


if __name__ == "__main__":
    main()
