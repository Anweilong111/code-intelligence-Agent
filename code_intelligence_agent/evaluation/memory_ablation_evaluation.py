from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from code_intelligence_agent.agents.evidence_memory import (
    memory_policy_hints,
    normalize_memory_record,
    retrieve_evidence_memories,
)


def evaluate_memory_ablation(dataset: dict[str, Any]) -> dict[str, Any]:
    cases = [_dict(item) for item in _list(dataset.get("cases"))]
    runs = []
    for case in cases:
        for mode, enabled in (("without_memory", False), ("with_memory", True)):
            runs.append(_run_case(case, mode=mode, enabled=enabled))
    metrics = {
        mode: _aggregate_mode([item for item in runs if item["mode"] == mode])
        for mode in ("without_memory", "with_memory")
    }
    with_memory = metrics["with_memory"]
    without_memory = metrics["without_memory"]
    gates = {
        "memory_completes_all_controlled_tasks": (
            with_memory["task_completion_rate"] == 1.0
        ),
        "memory_improves_task_completion": (
            with_memory["task_completion_rate"]
            > without_memory["task_completion_rate"]
        ),
        "memory_preserves_all_controlled_constraints": (
            with_memory["constraint_preservation_rate"] == 1.0
        ),
        "memory_avoids_all_known_failed_patches": (
            with_memory["failed_patch_avoidance_rate"] == 1.0
        ),
        "memory_does_not_reuse_stale_repo_evidence": (
            with_memory["stale_memory_reuse_rate"] == 0.0
        ),
        "disabled_ablation_retrieves_no_records": (
            without_memory["average_retrieved_record_count"] == 0.0
        ),
    }
    passed = all(gates.values())
    return {
        "schema_version": 1,
        "suite_name": str(dataset.get("suite_name") or "memory_ablation"),
        "status": "pass" if passed else "fail",
        "reason": (
            "all_memory_ablation_expectations_met"
            if passed
            else "memory_ablation_expectation_failed"
        ),
        "case_count": len(cases),
        "run_count": len(runs),
        "metrics": metrics,
        "acceptance_gates": gates,
        "runs": runs,
        "limitations": [
            "The suite measures structured retrieval and policy-hint utility, not live-model reasoning quality.",
            "Cross-repo patterns are eligible only when their source validation authority is sandbox pytest.",
        ],
    }


def render_memory_ablation_markdown(payload: dict[str, Any]) -> str:
    metrics = _dict(payload.get("metrics"))
    lines = [
        "# Evidence Memory Ablation",
        "",
        f"- Status: `{payload.get('status', 'none')}`",
        f"- Reason: `{payload.get('reason', 'none')}`",
        f"- Cases: {_int(payload.get('case_count', 0))}",
        f"- Runs: {_int(payload.get('run_count', 0))}",
        "",
        "## With vs Without Memory",
        "",
        "| Mode | Completion | Fact Recall | Constraint Preservation | Failed Patch Avoidance | Repeated Patch Rate | Stale Reuse | Avg Retrieved | Avg Prompt Chars | Avg Runtime (ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode in ("without_memory", "with_memory"):
        item = _dict(metrics.get(mode))
        lines.append(
            "| "
            f"{mode} | {_float(item.get('task_completion_rate')):.4f} | "
            f"{_float(item.get('expected_memory_recall')):.4f} | "
            f"{_float(item.get('constraint_preservation_rate')):.4f} | "
            f"{_float(item.get('failed_patch_avoidance_rate')):.4f} | "
            f"{_float(item.get('repeated_failed_patch_rate')):.4f} | "
            f"{_float(item.get('stale_memory_reuse_rate')):.4f} | "
            f"{_float(item.get('average_retrieved_record_count')):.4f} | "
            f"{_float(item.get('average_prompt_chars')):.2f} | "
            f"{_float(item.get('average_runtime_ms')):.4f} |"
        )
    lines.extend(["", "## Acceptance Gates", ""])
    for name, passed in _dict(payload.get("acceptance_gates")).items():
        lines.append(f"- `{name}`: {'pass' if passed else 'fail'}")
    lines.extend(["", "## Limitations", ""])
    for item in _list(payload.get("limitations")):
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def write_memory_ablation(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "memory_ablation_evaluation.json"
    markdown_path = root / "memory_ablation_evaluation.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_memory_ablation_markdown(payload),
        encoding="utf-8",
    )
    return {
        "memory_ablation_json": str(json_path),
        "memory_ablation_markdown": str(markdown_path),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Compare structured Agent behavior with and without evidence memory."
    )
    parser.add_argument("dataset")
    parser.add_argument("output_dir")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args(argv)
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    payload = evaluate_memory_ablation(dataset)
    write_memory_ablation(payload, args.output_dir)
    print(
        json.dumps(payload, indent=2, ensure_ascii=False)
        if args.format == "json"
        else render_memory_ablation_markdown(payload)
    )
    if args.require_pass and payload["status"] != "pass":
        raise SystemExit(1)


def _run_case(
    case: dict[str, Any],
    *,
    mode: str,
    enabled: bool,
) -> dict[str, Any]:
    records = [normalize_memory_record(_dict(item)) for item in _list(case.get("records"))]
    evidence = {"schema_version": 1, "records": records}
    started = time.perf_counter()
    retrieval = retrieve_evidence_memories(
        evidence,
        case.get("query") or "",
        repo=str(case.get("repo") or ""),
        repository_ref=str(case.get("repository_ref") or ""),
        session_id=str(case.get("session_id") or ""),
        top_k=_int(case.get("top_k", 8)),
        enabled=enabled,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    hints = memory_policy_hints(retrieval)
    selected_ids = set(str(item) for item in _list(retrieval.get("selected_memory_ids")))
    expected_ids = set(str(item) for item in _list(case.get("expected_memory_ids")))
    excluded_ids = set(str(item) for item in _list(case.get("expected_excluded_ids")))
    expected_hints = _dict(case.get("expected_hints"))
    hint_checks = {
        key: set(str(item) for item in _list(expected_values)).issubset(
            set(str(item) for item in _list(hints.get(key)))
        )
        for key, expected_values in expected_hints.items()
    }
    expected_selected = expected_ids.issubset(selected_ids)
    stale_reused = len(selected_ids & excluded_ids)
    task_completed = bool(
        expected_selected
        and not stale_reused
        and all(hint_checks.values())
    )
    required_fact_count = len(expected_ids) + sum(
        len(_list(value)) for value in expected_hints.values()
    )
    recalled_fact_count = len(expected_ids & selected_ids) + sum(
        len(_list(expected_hints[key])) if passed else 0
        for key, passed in hint_checks.items()
    )
    expected_failed = _list(expected_hints.get("failed_patch_fingerprints"))
    expected_constraints = _list(expected_hints.get("constraints"))
    return {
        "case": str(case.get("id") or ""),
        "mode": mode,
        "task_completed": task_completed,
        "selected_memory_ids": sorted(selected_ids),
        "expected_memory_ids": sorted(expected_ids),
        "expected_excluded_ids": sorted(excluded_ids),
        "hint_checks": hint_checks,
        "policy_hints": hints,
        "required_fact_count": required_fact_count,
        "recalled_fact_count": recalled_fact_count,
        "constraint_case": bool(expected_constraints),
        "constraint_preserved": bool(expected_constraints and hint_checks.get("constraints")),
        "failed_patch_case": bool(expected_failed),
        "failed_patch_avoided": bool(
            expected_failed and hint_checks.get("failed_patch_fingerprints")
        ),
        "repeated_failed_patch": bool(
            expected_failed and not hint_checks.get("failed_patch_fingerprints")
        ),
        "stale_memory_reuse_count": stale_reused,
        "retrieved_record_count": _int(retrieval.get("selected_count", 0)),
        "prompt_chars": len(
            json.dumps(_list(retrieval.get("records")), ensure_ascii=False)
        ),
        "runtime_ms": round(elapsed_ms, 4),
        "discarded_counts": _dict(retrieval.get("discarded_counts")),
    }


def _aggregate_mode(runs: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(runs)
    constraint_runs = [item for item in runs if item["constraint_case"]]
    failed_patch_runs = [item for item in runs if item["failed_patch_case"]]
    required = sum(_int(item.get("required_fact_count")) for item in runs)
    recalled = sum(_int(item.get("recalled_fact_count")) for item in runs)
    excluded_total = sum(len(_list(item.get("expected_excluded_ids"))) for item in runs)
    return {
        "run_count": count,
        "task_completion_rate": _ratio(
            sum(1 for item in runs if item["task_completed"]), count
        ),
        "expected_memory_recall": _ratio(recalled, required),
        "constraint_preservation_rate": _ratio(
            sum(1 for item in constraint_runs if item["constraint_preserved"]),
            len(constraint_runs),
        ),
        "failed_patch_avoidance_rate": _ratio(
            sum(1 for item in failed_patch_runs if item["failed_patch_avoided"]),
            len(failed_patch_runs),
        ),
        "repeated_failed_patch_rate": _ratio(
            sum(1 for item in failed_patch_runs if item["repeated_failed_patch"]),
            len(failed_patch_runs),
        ),
        "stale_memory_reuse_rate": _ratio(
            sum(_int(item.get("stale_memory_reuse_count")) for item in runs),
            excluded_total,
        ),
        "average_retrieved_record_count": _ratio(
            sum(_int(item.get("retrieved_record_count")) for item in runs), count
        ),
        "average_prompt_chars": _ratio(
            sum(_int(item.get("prompt_chars")) for item in runs), count
        ),
        "average_runtime_ms": _ratio(
            sum(_float(item.get("runtime_ms")) for item in runs), count
        ),
    }


def _ratio(numerator: float, denominator: float) -> float:
    return round(float(numerator) / float(denominator), 6) if denominator else 0.0


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


if __name__ == "__main__":
    main()
