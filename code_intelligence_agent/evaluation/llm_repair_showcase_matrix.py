from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any


SHOWCASE_CLASSES = [
    "llm_direct_success",
    "llm_reflection_success",
    "llm_blocker",
]


def build_llm_repair_showcase_matrix(
    suite_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for suite in suite_reports:
        suite_name = str(suite.get("suite_name") or "")
        suite_path = str(suite.get("suite_report_path") or "")
        for run in _list(suite.get("runs")):
            rows.append(
                _showcase_row(
                    _dict(run),
                    suite_name=suite_name,
                    suite_report_path=suite_path,
                )
            )
    class_counts = Counter(str(row.get("class") or "") for row in rows)
    requirement_status = {
        "llm_direct_success": class_counts["llm_direct_success"] >= 1,
        "llm_reflection_success": class_counts["llm_reflection_success"] >= 1,
        "llm_blocker": class_counts["llm_blocker"] >= 1,
    }
    return {
        "status": "pass" if all(requirement_status.values()) else "incomplete",
        "reason": (
            "all_llm_repair_showcase_classes_present"
            if all(requirement_status.values())
            else "missing_required_llm_repair_showcase_classes"
        ),
        "required_classes": SHOWCASE_CLASSES,
        "requirement_status": requirement_status,
        "class_counts": dict(sorted(class_counts.items())),
        "case_count": len(rows),
        "matrix": rows,
    }


def render_llm_repair_showcase_matrix_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LLM Repair Showcase Matrix",
        "",
        f"- Status: `{_markdown_cell(payload.get('status'))}`",
        f"- Reason: `{_markdown_cell(payload.get('reason'))}`",
        f"- Case Count: {_int(payload.get('case_count', 0))}",
        f"- Class Counts: {_format_counts(_dict(payload.get('class_counts')))}",
        "",
        "## Requirement Status",
        "",
        "| Required Class | Present |",
        "| --- | ---: |",
    ]
    requirement_status = _dict(payload.get("requirement_status"))
    for class_name in SHOWCASE_CLASSES:
        lines.append(
            "| "
            f"`{_markdown_cell(class_name)}` | "
            f"{str(bool(requirement_status.get(class_name, False))).lower()} |"
        )
    lines.extend(
        [
            "",
            "## Cases",
            "",
            (
                "| Case | Suite | Repo | Class | Status | Patch Mode | LLM Patch | "
                "Provider | Model | LLM Candidates | Validation Successes | "
                "Reflection Successes | Repair Action | Reflection Action | "
                "Blocker | Report |"
            ),
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |",
        ]
    )
    for row in _list(payload.get("matrix")):
        item = _dict(row)
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(item.get("name")),
                    _markdown_cell(item.get("suite_name")),
                    _markdown_cell(item.get("repo")),
                    _markdown_cell(item.get("class")),
                    _markdown_cell(item.get("status")),
                    _markdown_cell(item.get("patch_generation_mode")),
                    _markdown_cell(item.get("llm_patch_status")),
                    _markdown_cell(item.get("llm_provider")),
                    _markdown_cell(item.get("llm_model")),
                    str(_int(item.get("llm_candidate_count", 0))),
                    str(_int(item.get("patch_validation_success_count", 0))),
                    str(_int(item.get("successful_reflection_count", 0))),
                    _markdown_cell(item.get("repair_action_id")),
                    _markdown_cell(item.get("reflection_action_id")),
                    _markdown_cell(item.get("blocker")),
                    _markdown_cell(item.get("report_path")),
                ]
            )
            + " |"
        )
    if not _list(payload.get("matrix")):
        lines.append("| none | none | none | none | none | none | none | none | none | 0 | 0 | 0 | none | none | none | none |")
    lines.extend(["", "## Agent Loop Evidence", ""])
    for row in _list(payload.get("matrix")):
        item = _dict(row)
        loop = _dict(item.get("agent_loop_evidence"))
        lines.extend(
            [
                f"### {_markdown_cell(item.get('name'))}",
                "",
                f"- Observe: {_markdown_cell(loop.get('observe'))}",
                f"- Plan: {_markdown_cell(loop.get('plan'))}",
                f"- Act: {_markdown_cell(loop.get('act'))}",
                f"- Verify: {_markdown_cell(loop.get('verify'))}",
                f"- Reflect: {_markdown_cell(loop.get('reflect'))}",
                f"- Replan: {_markdown_cell(loop.get('replan'))}",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def write_llm_repair_showcase_matrix_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "llm_repair_showcase_matrix.json"
    markdown_path = root / "llm_repair_showcase_matrix.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_llm_repair_showcase_matrix_markdown(payload),
        encoding="utf-8",
    )
    return {
        "llm_repair_showcase_matrix_json": str(json_path),
        "llm_repair_showcase_matrix_markdown": str(markdown_path),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a three-class LLM repair showcase matrix from GitHub repo "
            "intelligence suite artifacts."
        )
    )
    parser.add_argument(
        "suite_report",
        nargs="+",
        help=(
            "Path to github_repo_intelligence_suite.json, or a directory "
            "containing that file."
        ),
    )
    parser.add_argument("output_dir")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Exit non-zero unless direct-success, reflection-success, and blocker cases are all present.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    suite_reports = [_load_suite_report(Path(path)) for path in args.suite_report]
    matrix = build_llm_repair_showcase_matrix(suite_reports)
    write_llm_repair_showcase_matrix_artifacts(matrix, args.output_dir)
    if args.format == "json":
        print(json.dumps(matrix, indent=2, ensure_ascii=False))
    else:
        print(render_llm_repair_showcase_matrix_markdown(matrix))
    if args.require_complete and matrix["status"] != "pass":
        raise SystemExit(1)


def _load_suite_report(path: Path) -> dict[str, Any]:
    report_path = (
        path / "github_repo_intelligence_suite.json"
        if path.is_dir()
        else path
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Suite report must be a JSON object: {report_path}")
    payload["suite_report_path"] = str(report_path)
    return payload


def _showcase_row(
    run: dict[str, Any],
    *,
    suite_name: str,
    suite_report_path: str,
) -> dict[str, Any]:
    metrics = _dict(run.get("metrics"))
    llm_candidate_count = _first_int(
        metrics.get("repository_patch_generator_llm_candidate_count"),
        metrics.get("repository_patch_generator_llm_count"),
        metrics.get("repository_test_patch_generator_llm_candidate_count"),
    )
    validation_success_count = _int(
        metrics.get("repository_test_patch_validation_success_count", 0)
    )
    successful_reflection_count = _first_int(
        metrics.get("repository_test_patch_validation_successful_reflection_count"),
        metrics.get(
            "repository_test_patch_validation_successful_regression_reflection_count"
        ),
    )
    blocker = str(
        metrics.get("blocker")
        or metrics.get("repository_llm_reflection_blocker")
        or (
            metrics.get("repository_llm_patch_generation_reason")
            if str(metrics.get("repository_llm_patch_generation_status") or "").lower()
            in {"blocked", "unavailable", "failed"}
            else ""
        )
        or ""
    )
    patch_mode = str(metrics.get("repository_patch_generation_mode") or "")
    llm_patch_status = str(
        metrics.get("repository_llm_patch_generation_status") or ""
    )
    repair_action_id = _repair_action_id(
        patch_mode=patch_mode,
        llm_patch_status=llm_patch_status,
    )
    reflection_action_id = _reflection_action_id(metrics)
    row_class = _classify_case(
        metrics,
        llm_candidate_count=llm_candidate_count,
        validation_success_count=validation_success_count,
        successful_reflection_count=successful_reflection_count,
        blocker=blocker,
    )
    return {
        "name": str(run.get("name") or ""),
        "suite_name": suite_name,
        "suite_report_path": suite_report_path,
        "repo": str(run.get("repo") or ""),
        "output_dir": str(run.get("output_dir") or ""),
        "report_path": str(run.get("report_path") or ""),
        "status": str(run.get("status") or metrics.get("status") or ""),
        "passed": bool(run.get("passed", False)),
        "class": row_class,
        "repair_action_id": repair_action_id,
        "reflection_action_id": reflection_action_id,
        "class_reason": _class_reason(
            row_class,
            llm_candidate_count=llm_candidate_count,
            validation_success_count=validation_success_count,
            successful_reflection_count=successful_reflection_count,
            blocker=blocker,
        ),
        "patch_generation_mode": patch_mode,
        "llm_patch_status": llm_patch_status,
        "llm_provider": str(metrics.get("repository_llm_patch_provider") or ""),
        "llm_model": str(metrics.get("repository_llm_patch_model") or ""),
        "llm_api_key_present": bool(
            metrics.get("repository_llm_patch_api_key_present", False)
        ),
        "llm_candidate_count": llm_candidate_count,
        "patch_validation_status": str(
            metrics.get("repository_test_patch_validation_status") or ""
        ),
        "patch_validation_success_count": validation_success_count,
        "patch_judge_mode": str(
            metrics.get("repository_test_patch_judge_mode") or ""
        ),
        "patch_judge_status": str(
            metrics.get("repository_test_patch_judge_status") or ""
        ),
        "reflection_mode": str(
            metrics.get("repository_test_patch_validation_reflection_mode")
            or ""
        ),
        "reflection_candidate_count": _int(
            metrics.get("repository_test_patch_validation_reflection_candidate_count", 0)
        ),
        "successful_reflection_count": successful_reflection_count,
        "blocker": blocker,
        "next_action": str(
            metrics.get("agent_answers_next_action")
            or metrics.get("analysis_next_action")
            or metrics.get("next_action")
            or ""
        ),
        "artifact_paths": _artifact_paths(run, metrics),
        "agent_loop_evidence": _agent_loop_evidence(
            metrics,
            row_class=row_class,
            llm_candidate_count=llm_candidate_count,
            validation_success_count=validation_success_count,
            successful_reflection_count=successful_reflection_count,
            blocker=blocker,
            repair_action_id=repair_action_id,
            reflection_action_id=reflection_action_id,
        ),
    }


def _classify_case(
    metrics: dict[str, Any],
    *,
    llm_candidate_count: int,
    validation_success_count: int,
    successful_reflection_count: int,
    blocker: str,
) -> str:
    patch_mode = str(metrics.get("repository_patch_generation_mode") or "").lower()
    llm_status = str(
        metrics.get("repository_llm_patch_generation_status") or ""
    ).lower()
    if successful_reflection_count > 0:
        return "llm_reflection_success"
    if (
        patch_mode in {"llm", "hybrid"}
        and llm_status == "pass"
        and llm_candidate_count > 0
        and validation_success_count > 0
    ):
        return "llm_direct_success"
    if blocker or llm_status in {"blocked", "unavailable", "failed"}:
        return "llm_blocker"
    if validation_success_count <= 0:
        return "llm_blocker"
    return "llm_blocker"


def _class_reason(
    row_class: str,
    *,
    llm_candidate_count: int,
    validation_success_count: int,
    successful_reflection_count: int,
    blocker: str,
) -> str:
    if row_class == "llm_reflection_success":
        return (
            "A refined LLM reflection candidate passed sandbox validation "
            f"({successful_reflection_count} successes)."
        )
    if row_class == "llm_direct_success":
        return (
            "An LLM-generated candidate passed sandbox validation without a "
            "recorded reflection success."
        )
    if blocker:
        return f"Agent reported blocker: {blocker}."
    return (
        "No validated LLM repair success was recorded "
        f"(llm_candidates={llm_candidate_count}, "
        f"validation_successes={validation_success_count})."
    )


def _agent_loop_evidence(
    metrics: dict[str, Any],
    *,
    row_class: str,
    llm_candidate_count: int,
    validation_success_count: int,
    successful_reflection_count: int,
    blocker: str,
    repair_action_id: str,
    reflection_action_id: str,
) -> dict[str, str]:
    patch_mode = str(metrics.get("repository_patch_generation_mode") or "none")
    llm_status = str(
        metrics.get("repository_llm_patch_generation_status") or "none"
    )
    provider = str(metrics.get("repository_llm_patch_provider") or "none")
    model = str(metrics.get("repository_llm_patch_model") or "none")
    action = str(
        metrics.get("controller_action_id")
        or metrics.get("agent_auto_last_action_id")
        or metrics.get("agent_auto_action_id")
        or "none"
    )
    patch_validation_status = str(
        metrics.get("repository_test_patch_validation_status") or "none"
    )
    reflection_mode = _reflection_mode_label(metrics)
    next_action = str(
        metrics.get("agent_answers_next_action")
        or metrics.get("analysis_next_action")
        or metrics.get("next_action")
        or "none"
    )
    return {
        "observe": (
            f"patch_mode={patch_mode}, llm_status={llm_status}, "
            f"provider={provider}, model={model}, blocker={blocker or 'none'}"
        ),
        "plan": (
            f"classify as {row_class}; repair_action={repair_action_id or 'none'}; "
            f"reflection_action={reflection_action_id or 'none'}; "
            f"controller_next_action={action}; LLM repair remains gated by "
            "explicit provider/model/key audit"
        ),
        "act": (
            f"llm_candidates={llm_candidate_count}; "
            f"patch_generation_status={llm_status}; "
            f"rule_candidates={_int(metrics.get('repository_patch_generator_rule_count') or 0)}"
        ),
        "verify": (
            f"sandbox_validation_status={patch_validation_status}; "
            f"successes={validation_success_count}; "
            f"patch_judge={metrics.get('repository_test_patch_judge_mode') or 'none'}/"
            f"{metrics.get('repository_test_patch_judge_status') or 'none'}; "
            "authority=sandbox_pytest_decides_success"
        ),
        "reflect": (
            f"reflection_mode={reflection_mode}; "
            f"successful_reflections={successful_reflection_count}"
        ),
        "replan": next_action,
    }


def _repair_action_id(*, patch_mode: str, llm_patch_status: str) -> str:
    mode = str(patch_mode or "").lower()
    status = str(llm_patch_status or "").lower()
    if mode == "llm":
        return (
            "configure_llm_patch_api_key"
            if status in {"blocked", "unavailable", "failed"}
            else "generate_llm_patch_candidates"
        )
    if mode == "hybrid":
        return "generate_hybrid_patch_candidates"
    return ""


def _reflection_action_id(metrics: dict[str, Any]) -> str:
    reflection_mode = _reflection_mode_label(metrics).lower()
    reflection_count = _first_int(
        metrics.get("repository_test_patch_validation_reflection_candidate_count"),
        metrics.get(
            "repository_test_patch_validation_successful_reflection_count"
        ),
    )
    if reflection_mode == "llm" and reflection_count > 0:
        return "run_llm_patch_reflection_loop"
    return ""


def _reflection_mode_label(metrics: dict[str, Any]) -> str:
    explicit = str(
        metrics.get("repository_test_patch_validation_reflection_mode") or ""
    ).lower()
    if explicit:
        return explicit
    status = str(metrics.get("repository_llm_reflection_status") or "").lower()
    provider = str(metrics.get("repository_llm_reflection_provider") or "")
    reflection_count = _first_int(
        metrics.get("repository_test_patch_validation_reflection_candidate_count"),
        metrics.get(
            "repository_test_patch_validation_successful_reflection_count"
        ),
    )
    if reflection_count > 0 and (provider or status in {"ready", "pass"}):
        return "llm"
    return status or "none"


def _artifact_paths(run: dict[str, Any], metrics: dict[str, Any]) -> dict[str, str]:
    keys = [
        "agent_controller_json",
        "agent_controller_markdown",
        "controller_report_path",
        "repository_test_patch_candidates_json",
        "repository_test_patch_candidates_markdown",
        "repository_test_patch_validation_json",
        "repository_test_patch_validation_markdown",
        "reflection_trace_json",
        "reflection_trace_markdown",
    ]
    paths = {
        "suite_run_report": str(run.get("report_path") or ""),
        "output_dir": str(run.get("output_dir") or ""),
    }
    for key in keys:
        value = str(metrics.get(key) or "")
        if value:
            paths[key] = value
    return paths


def _first_int(*values: Any) -> int:
    for value in values:
        if value is not None:
            return _int(value)
    return 0


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(
        f"{key}={_int(value)}" for key, value in sorted(counts.items())
    )


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


if __name__ == "__main__":
    main()
