from __future__ import annotations

import argparse
import json
import tempfile
import time
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from code_intelligence_agent.agents.llm_client import SequenceLLMClient
from code_intelligence_agent.agents.llm_patch_generator import LLMPatchGenerator
from code_intelligence_agent.core.repo_parser import RepoParser
from code_intelligence_agent.evaluation.repository_test_patch_candidates import (
    build_repository_test_patch_candidates,
)
from code_intelligence_agent.evaluation.repository_test_patch_validation import (
    build_repository_test_patch_validation,
)


PATCH_MODES = ("rule", "llm", "hybrid")


def evaluate_patch_strategies(dataset_path: str | Path) -> dict[str, Any]:
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    cases = [_dict(item) for item in _list(dataset.get("cases"))]
    runs = [
        _evaluate_case_mode(case, mode=mode)
        for case in cases
        for mode in PATCH_MODES
    ]
    strategies = {
        mode: _aggregate_runs([row for row in runs if row["patch_mode"] == mode])
        for mode in PATCH_MODES
    }
    passed = bool(cases) and all(bool(row["expectation_matched"]) for row in runs)
    return {
        "schema_version": 1,
        "suite_name": str(dataset.get("suite_name") or ""),
        "status": "pass" if passed else "fail",
        "reason": (
            "all_controlled_patch_expectations_met"
            if passed
            else "controlled_patch_expectation_mismatch"
        ),
        "case_count": len(cases),
        "run_count": len(runs),
        "strategies": strategies,
        "runs": runs,
        "success_authority": "sandbox_targeted_and_full_regression_tests",
        "attribution_policy": (
            "winning generator comes from candidate metadata; rule success is never "
            "credited to LLM"
        ),
        "limitations": [
            "The LLM responses in this Phase 5 suite are deterministic offline fixtures.",
            "The suite validates orchestration, attribution, safety, reflection, and sandbox contracts; it does not measure live-model repair quality.",
            "The three controlled cases are contract tests, not a real-world GitHub repair-rate estimate.",
        ],
    }


def evaluate_patch_case(
    case: dict[str, Any],
    *,
    mode: str,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = {**case, **_dict(overrides)}
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="cia_patch_strategy_") as tmp_dir:
        root = Path(tmp_dir)
        _materialize_case(root, case)
        parsed = RepoParser().parse(root)
        target_name = str(case.get("target_function") or "")
        target = next(
            (
                function
                for function in parsed.functions
                if function.name == target_name
                or str(function.metadata.get("qualified_name") or "") == target_name
            ),
            None,
        )
        if target is None:
            return _failed_run(case, mode=mode, reason="target_function_not_found")
        localization = _localization_payload(case, parsed.functions, target)
        generator = (
            _controlled_llm_generator(
                case,
                top_k_functions=int(settings.get("top_k_functions", 5)),
            )
            if mode != "rule"
            else None
        )
        candidates = build_repository_test_patch_candidates(
            localization,
            repository_root=root,
            candidate_limit=int(settings.get("candidate_limit", 3)),
            patch_generation_mode=mode,
            llm_generator=generator,
        )
        validation = build_repository_test_patch_validation(
            candidates,
            repository_root=root,
            validation_limit=int(settings.get("validation_limit", 3)),
            timeout=int(settings.get("timeout_seconds", 10)),
            reflection_mode="llm" if mode != "rule" else "rule",
            reflection_rounds=int(settings.get("reflection_rounds", 1)),
            reflection_width=int(settings.get("reflection_width", 1)),
            refiner=generator,
            regression_pytest_args=[
                str(item) for item in _list(settings.get("regression_pytest_args"))
            ],
        )
    runtime_ms = round((time.perf_counter() - started) * 1000, 4)
    candidate_rows = [_dict(item) for item in _list(candidates.get("candidates"))]
    best_patch = _dict(validation.get("best_patch"))
    best_generator = str(best_patch.get("generator") or "")
    best_generator_family = str(best_patch.get("generator_family") or "")
    expected = _dict(_dict(case.get("expectations")).get(mode))
    actual = {
        "candidate_generated": int(candidates.get("candidate_count", 0)) > 0,
        "targeted_test_passed": int(validation.get("success_count", 0)) > 0,
        "verified_repair": bool(validation.get("verified_repair", False)),
        "reflection_recovered": (
            bool(validation.get("verified_repair", False))
            and int(validation.get("successful_reflection_candidate_count", 0)) > 0
        ),
    }
    expectation_matched = all(
        actual.get(key) == bool(value)
        for key, value in expected.items()
        if key in actual
    )
    attribution_consistent = _attribution_consistent(best_patch)
    if actual["verified_repair"] and not attribution_consistent:
        expectation_matched = False
    return {
        "case": str(case.get("name") or ""),
        "category": str(case.get("category") or ""),
        "patch_mode": mode,
        "status": str(validation.get("status") or candidates.get("status") or ""),
        "reason": str(validation.get("reason") or candidates.get("reason") or ""),
        "candidate_count": int(candidates.get("candidate_count", 0)),
        "candidate_generated": actual["candidate_generated"],
        "ast_valid_candidate_count": sum(
            bool(_dict(_dict(row.get("metadata")).get("safety_gate")).get("ast_valid"))
            for row in candidate_rows
        ),
        "safety_pass_candidate_count": sum(
            str(_dict(_dict(row.get("metadata")).get("safety_gate")).get("status"))
            == "pass"
            for row in candidate_rows
        ),
        "generator_counts": _dict(candidates.get("generator_counts")),
        "generation_strategy": str(
            _dict(candidates.get("generation_plan")).get("strategy") or ""
        ),
        "generation_order": [
            str(item)
            for item in _list(
                _dict(candidates.get("generation_plan")).get("generation_order")
            )
        ],
        "targeted_test_passed": actual["targeted_test_passed"],
        "full_regression_status": str(
            _dict(validation.get("regression_validation")).get("status") or ""
        ),
        "verified_repair": actual["verified_repair"],
        "verification_claim": str(validation.get("verification_claim") or ""),
        "reflection_recovered": actual["reflection_recovered"],
        "reflection_candidate_count": int(
            validation.get("reflection_candidate_count", 0)
        ),
        "best_candidate_id": _portable_candidate_id(best_patch),
        "best_rule_id": str(best_patch.get("rule_id") or ""),
        "best_generator": best_generator,
        "best_generator_family": best_generator_family,
        "attribution_consistent": attribution_consistent,
        "expected": expected,
        "expectation_matched": expectation_matched,
        "experiment_overrides": _dict(overrides),
        "top_k_functions": int(settings.get("top_k_functions", 5)),
        "candidate_limit": int(settings.get("candidate_limit", 3)),
        "reflection_rounds": int(settings.get("reflection_rounds", 1)),
        "runtime_ms": runtime_ms,
    }


def _evaluate_case_mode(case: dict[str, Any], *, mode: str) -> dict[str, Any]:
    return evaluate_patch_case(case, mode=mode)


def _materialize_case(root: Path, case: dict[str, Any]) -> None:
    files = _dict(case.get("files"))
    for relative_path, content in files.items():
        path = _safe_case_path(root, str(relative_path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content), encoding="utf-8")


def _safe_case_path(root: Path, relative_path: str) -> Path:
    pure = PurePosixPath(relative_path.replace("\\", "/"))
    if not relative_path or pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"Unsafe controlled case path: {relative_path}")
    return root / Path(*pure.parts)


def _localization_payload(
    case: dict[str, Any],
    functions: list[Any],
    target: Any,
) -> dict[str, Any]:
    nodeid = str(case.get("failing_nodeid") or "")
    rankings = _controlled_localization_rankings(case, functions, target)
    return {
        "status": "pass",
        "top_function": str(rankings[0]["function_name"]),
        "top_score": float(rankings[0]["score"]),
        "dynamic_evidence_level": "failing_tests",
        "recommended_validation_command": f"python -m pytest -q {nodeid}",
        "dynamic_evidence_nodeids": {"controlled_failure": nodeid},
        "matched_failing_tests": [{"nodeid": nodeid}],
        "rankings": rankings,
    }


def _controlled_localization_rankings(
    case: dict[str, Any],
    functions: list[Any],
    target: Any,
) -> list[dict[str, Any]]:
    configured = [_dict(item) for item in _list(case.get("localization_rankings"))]
    if not configured:
        configured = [
            {
                "function": str(target.metadata.get("qualified_name") or target.name),
                "score": 0.95,
                "signals": _dict(case.get("signals")),
            }
        ]
    function_by_name: dict[str, Any] = {}
    for function in functions:
        function_by_name[function.name] = function
        function_by_name[
            str(function.metadata.get("qualified_name") or function.name)
        ] = function
    rankings: list[dict[str, Any]] = []
    for index, item in enumerate(configured, start=1):
        function_name = str(item.get("function") or item.get("function_name") or "")
        function = function_by_name.get(function_name)
        if function is None:
            raise ValueError(
                f"Controlled localization function not found: {function_name}"
            )
        rankings.append(
            {
                "function_id": function.id,
                "function_name": str(
                    function.metadata.get("qualified_name") or function.name
                ),
                "file_path": function.file_path,
                "start_line": function.start_line,
                "end_line": function.end_line,
                "score": float(item.get("score", max(0.0, 1.0 - index * 0.05))),
                "rank": index,
                "signals": {
                    str(key): float(value)
                    for key, value in _dict(item.get("signals") or case.get("signals")).items()
                },
                "reason": str(item.get("reason") or "controlled failing-test evidence"),
            }
        )
    return rankings


def _controlled_llm_generator(
    case: dict[str, Any],
    *,
    top_k_functions: int = 5,
) -> LLMPatchGenerator:
    generation_sources = [
        str(item) for item in _list(case.get("llm_generation_sources"))
    ]
    initial_sources = [str(item) for item in _list(case.get("llm_initial_sources"))]
    if generation_sources:
        responses = [
            json.dumps({"fixed_source": source}) for source in generation_sources
        ]
    elif initial_sources:
        responses = [json.dumps({"fixed_sources": initial_sources})]
    else:
        responses = [
            json.dumps({"fixed_source": str(case.get("llm_initial_source") or "")})
        ]
    reflection_source = str(case.get("llm_reflection_source") or "")
    if reflection_source:
        responses.append(json.dumps({"fixed_source": reflection_source}))
    return LLMPatchGenerator(
        SequenceLLMClient(responses),
        top_k_functions=max(1, int(top_k_functions)),
    )


def _attribution_consistent(best_patch: dict[str, Any]) -> bool:
    if not best_patch:
        return True
    generator = str(best_patch.get("generator_family") or "")
    rule_id = str(best_patch.get("rule_id") or "").lower()
    if generator == "llm":
        return "llm" in rule_id
    if generator == "rule":
        return "llm" not in rule_id
    return False


def _portable_candidate_id(best_patch: dict[str, Any]) -> str:
    candidate_id = str(best_patch.get("candidate_id") or "")
    relative_path = str(best_patch.get("relative_file_path") or "")
    if not candidate_id or not relative_path or "::" not in candidate_id:
        return candidate_id
    _, suffix = candidate_id.split("::", 1)
    return f"{relative_path}::{suffix}"


def _aggregate_runs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    run_count = len(rows)
    candidate_count = sum(int(row["candidate_count"]) for row in rows)
    generator_wins = Counter(
        str(row["best_generator_family"])
        for row in rows
        if str(row["best_generator_family"])
    )
    return {
        "run_count": run_count,
        "candidate_generation_success_rate": _ratio(
            sum(bool(row["candidate_generated"]) for row in rows), run_count
        ),
        "candidate_count": candidate_count,
        "ast_valid_patch_rate": _ratio(
            sum(int(row["ast_valid_candidate_count"]) for row in rows),
            candidate_count,
        ),
        "safety_gate_pass_rate": _ratio(
            sum(int(row["safety_pass_candidate_count"]) for row in rows),
            candidate_count,
        ),
        "targeted_test_pass_rate": _ratio(
            sum(bool(row["targeted_test_passed"]) for row in rows), run_count
        ),
        "regression_safe_patch_rate": _ratio(
            sum(row["full_regression_status"] == "pass" for row in rows),
            run_count,
        ),
        "verified_repair_rate": _ratio(
            sum(bool(row["verified_repair"]) for row in rows), run_count
        ),
        "reflection_recovery_rate": _ratio(
            sum(bool(row["reflection_recovered"]) for row in rows), run_count
        ),
        "attribution_consistency_rate": _ratio(
            sum(bool(row["attribution_consistent"]) for row in rows), run_count
        ),
        "generator_wins": dict(sorted(generator_wins.items())),
        "average_runtime_ms": round(
            sum(float(row["runtime_ms"]) for row in rows) / run_count,
            4,
        ) if run_count else 0.0,
    }


def _failed_run(case: dict[str, Any], *, mode: str, reason: str) -> dict[str, Any]:
    return {
        "case": str(case.get("name") or ""),
        "category": str(case.get("category") or ""),
        "patch_mode": mode,
        "status": "fail",
        "reason": reason,
        "candidate_count": 0,
        "candidate_generated": False,
        "ast_valid_candidate_count": 0,
        "safety_pass_candidate_count": 0,
        "generator_counts": {},
        "generation_strategy": "",
        "generation_order": [],
        "targeted_test_passed": False,
        "full_regression_status": "",
        "verified_repair": False,
        "verification_claim": "none",
        "reflection_recovered": False,
        "reflection_candidate_count": 0,
        "best_candidate_id": "",
        "best_rule_id": "",
        "best_generator": "",
        "best_generator_family": "",
        "attribution_consistent": True,
        "expected": _dict(_dict(case.get("expectations")).get(mode)),
        "expectation_matched": False,
        "experiment_overrides": {},
        "top_k_functions": 0,
        "candidate_limit": 0,
        "reflection_rounds": 0,
        "runtime_ms": 0.0,
    }


def render_patch_strategy_evaluation_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Phase 5 Patch Strategy Evaluation",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Reason: `{payload.get('reason')}`",
        f"- Cases: {payload.get('case_count', 0)}",
        f"- Runs: {payload.get('run_count', 0)}",
        f"- Success Authority: `{payload.get('success_authority')}`",
        "",
        "## Strategy Metrics",
        "",
        "| Mode | Candidate Success | AST Valid | Safety Pass | Target Pass | Regression Safe | Verified Repair | Reflection Recovery | Attribution | Avg Runtime (ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode in PATCH_MODES:
        metrics = _dict(_dict(payload.get("strategies")).get(mode))
        lines.append(
            "| "
            + " | ".join(
                [
                    mode,
                    _rate(metrics, "candidate_generation_success_rate"),
                    _rate(metrics, "ast_valid_patch_rate"),
                    _rate(metrics, "safety_gate_pass_rate"),
                    _rate(metrics, "targeted_test_pass_rate"),
                    _rate(metrics, "regression_safe_patch_rate"),
                    _rate(metrics, "verified_repair_rate"),
                    _rate(metrics, "reflection_recovery_rate"),
                    _rate(metrics, "attribution_consistency_rate"),
                    f"{float(metrics.get('average_runtime_ms', 0.0)):.4f}",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Runs", ""])
    lines.extend(
        [
            "| Case | Mode | Strategy | Candidates | Target | Regression | Verified | Reflection | Winner | Expected |",
            "| --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row_value in _list(payload.get("runs")):
        row = _dict(row_value)
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("case") or ""),
                    str(row.get("patch_mode") or ""),
                    str(row.get("generation_strategy") or ""),
                    str(row.get("candidate_count", 0)),
                    str(bool(row.get("targeted_test_passed"))).lower(),
                    str(row.get("full_regression_status") or ""),
                    str(bool(row.get("verified_repair"))).lower(),
                    str(bool(row.get("reflection_recovered"))).lower(),
                    str(row.get("best_generator") or "none"),
                    str(bool(row.get("expectation_matched"))).lower(),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in _list(payload.get("limitations")))
    return "\n".join(lines) + "\n"


def write_patch_strategy_evaluation(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "patch_strategy_evaluation.json"
    markdown_path = root / "patch_strategy_evaluation.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_patch_strategy_evaluation_markdown(payload),
        encoding="utf-8",
    )
    return {"json": str(json_path), "markdown": str(markdown_path)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate Rule/LLM/Hybrid patch strategies.")
    parser.add_argument("dataset")
    parser.add_argument("output_dir")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args(argv)
    payload = evaluate_patch_strategies(args.dataset)
    paths = write_patch_strategy_evaluation(payload, args.output_dir)
    print(Path(paths[args.format]).read_text(encoding="utf-8"))
    if args.require_pass and payload.get("status") != "pass":
        raise SystemExit(1)


def _rate(payload: dict[str, Any], key: str) -> str:
    return f"{float(payload.get(key, 0.0)):.4f}"


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


if __name__ == "__main__":
    main()
