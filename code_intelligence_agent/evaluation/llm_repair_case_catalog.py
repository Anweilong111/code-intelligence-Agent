from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.llm_repair_showcase_matrix import (
    P6_EVALUATION_TARGETS,
)
from code_intelligence_agent.evaluation.p6_readiness_audit import SANDBOX_AUTHORITY


CATALOG_LOOP = "Observe -> Plan -> Act -> Verify -> Reflect -> Replan"


def build_llm_repair_case_catalog_audit(
    catalog: dict[str, Any],
    matrix: dict[str, Any] | None = None,
    *,
    catalog_path: str = "",
    matrix_path: str = "",
    targets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target_values = {
        **P6_EVALUATION_TARGETS,
        "agent_loop_trace_complete": P6_EVALUATION_TARGETS["case_count"],
        **_dict(targets),
        **_dict(catalog.get("targets")),
    }
    cases = [_case_record(item) for item in _list(catalog.get("cases"))]
    matrix_payload = _dict(matrix)
    rows = [_dict(row) for row in _list(matrix_payload.get("matrix"))]
    row_by_key = _index_matrix_rows(rows)
    source_report_records = _source_report_records(
        catalog,
        catalog_path=catalog_path,
    )
    case_records = [
        _audit_case(
            case,
            row_by_key=row_by_key,
        )
        for case in cases
    ]
    counts = _audit_counts(
        case_records,
        rows=rows,
        source_report_records=source_report_records,
    )
    target_checks = _target_checks(counts, target_values)
    missing = [str(check.get("name") or "") for check in target_checks if not check.get("passed")]
    status = "pass" if target_checks and not missing else "incomplete"
    return {
        "status": status,
        "reason": (
            "llm_repair_case_catalog_targets_met"
            if status == "pass"
            else "llm_repair_case_catalog_targets_not_met"
        ),
        "catalog_name": str(catalog.get("name") or ""),
        "catalog_path": catalog_path,
        "matrix_path": matrix_path,
        "targets": target_values,
        "summary": {
            "declared_case_count": counts["declared_case_count"],
            "matched_case_count": counts["matched_case_count"],
            "missing_case_count": counts["missing_case_count"],
            "class_mismatch_count": counts["class_mismatch_count"],
            "source_report_count": counts["source_report_count"],
            "missing_source_report_count": counts["missing_source_report_count"],
            "matrix_case_count": counts["matrix_case_count"],
            "matrix_exists": _path_exists(matrix_path),
            "target_check_count": len(target_checks),
            "passed_target_check_count": sum(1 for item in target_checks if item.get("passed")),
            "failed_target_check_count": len(missing),
            "sandbox_authority": SANDBOX_AUTHORITY,
            "agent_loop": CATALOG_LOOP,
        },
        "counts": counts,
        "source_reports": source_report_records,
        "cases": case_records,
        "target_checks": target_checks,
        "missing": missing,
        "next_actions": _next_actions(missing, counts),
        "sandbox_authority": SANDBOX_AUTHORITY,
        "agent_loop": CATALOG_LOOP,
    }


def write_llm_repair_case_catalog_audit_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "llm_repair_case_catalog_audit.json"
    markdown_path = root / "llm_repair_case_catalog_audit.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_llm_repair_case_catalog_audit_markdown(payload),
        encoding="utf-8",
    )
    return {
        "llm_repair_case_catalog_audit_json": str(json_path),
        "llm_repair_case_catalog_audit_markdown": str(markdown_path),
    }


def render_llm_repair_case_catalog_audit_markdown(payload: dict[str, Any]) -> str:
    summary = _dict(payload.get("summary"))
    counts = _dict(payload.get("counts"))
    lines = [
        "# LLM Repair Case Catalog Audit",
        "",
        f"- Status: `{_markdown_cell(payload.get('status') or 'unknown')}`",
        f"- Reason: `{_markdown_cell(payload.get('reason') or 'none')}`",
        f"- Declared Cases: {_int(summary.get('declared_case_count'))}",
        f"- Matched Cases: {_int(summary.get('matched_case_count'))}",
        f"- Matrix Cases: {_int(summary.get('matrix_case_count'))}",
        f"- Missing Source Reports: {_int(summary.get('missing_source_report_count'))}",
        f"- Sandbox Authority: `{_markdown_cell(payload.get('sandbox_authority') or SANDBOX_AUTHORITY)}`",
        f"- Agent Loop: `{_markdown_cell(payload.get('agent_loop') or CATALOG_LOOP)}`",
        "",
        "## Target Checks",
        "",
        "| Target | Actual | Expected | Passed |",
        "| --- | ---: | ---: | ---: |",
    ]
    for check_value in _list(payload.get("target_checks")):
        check = _dict(check_value)
        lines.append(
            "| "
            f"{_markdown_cell(check.get('name'))} | "
            f"{_markdown_cell(check.get('actual'))} | "
            f"{_markdown_cell(check.get('expected'))} | "
            f"{str(bool(check.get('passed'))).lower()} |"
        )
    if not _list(payload.get("target_checks")):
        lines.append("| none | 0 | 0 | false |")
    lines.extend(
        [
            "",
            "## Counts",
            "",
            f"- Declared Classes: {_format_counts(_dict(counts.get('declared_class_counts')))}",
            f"- Matched Classes: {_format_counts(_dict(counts.get('matched_class_counts')))}",
            f"- Matrix Classes: {_format_counts(_dict(counts.get('matrix_class_counts')))}",
            f"- Blocker Categories: {_format_counts(_dict(counts.get('matched_blocker_category_counts')))}",
            "",
            "## Cases",
            "",
            (
                "| Case | Repo | Expected Class | Matrix Class | Matched | Evidence | "
                "Agent Loop | Blocker Category | Source Report | Matrix Report | Notes |"
            ),
            "| --- | --- | --- | --- | ---: | --- | ---: | --- | --- | --- | --- |",
        ]
    )
    for case_value in _list(payload.get("cases")):
        case = _dict(case_value)
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(case.get("case_id")),
                    _markdown_cell(case.get("repo")),
                    _markdown_cell(case.get("expected_class")),
                    _markdown_cell(case.get("matrix_class")),
                    str(bool(case.get("matched"))).lower(),
                    _markdown_cell(case.get("evidence_status")),
                    str(bool(case.get("agent_loop_complete"))).lower(),
                    _markdown_cell(case.get("blocker_category")),
                    _markdown_cell(case.get("source_report_path")),
                    _markdown_cell(case.get("matrix_report_path")),
                    _markdown_cell(_format_list(_list(case.get("notes")))),
                ]
            )
            + " |"
        )
    if not _list(payload.get("cases")):
        lines.append("| none | none | none | none | false | none | false | none | none | none | none |")
    lines.extend(["", "## Source Reports", ""])
    lines.extend(
        [
            "| Path | Exists | Referenced By |",
            "| --- | ---: | --- |",
        ]
    )
    for report_value in _list(payload.get("source_reports")):
        report = _dict(report_value)
        lines.append(
            "| "
            f"{_markdown_cell(report.get('path'))} | "
            f"{str(bool(report.get('exists'))).lower()} | "
            f"{_markdown_cell(_format_list(_list(report.get('referenced_by'))))} |"
        )
    if not _list(payload.get("source_reports")):
        lines.append("| none | false | none |")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {action}")
    if not _list(payload.get("next_actions")):
        lines.append("- Catalog audit targets are met.")
    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit a declared LLM repair case catalog against an evaluation matrix."
        )
    )
    parser.add_argument("catalog")
    parser.add_argument("output_dir")
    parser.add_argument(
        "--matrix",
        help=(
            "Path to llm_repair_evaluation_matrix.json. Defaults to the matrix "
            "path declared in the catalog."
        ),
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
    )
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="Exit non-zero unless the catalog audit reaches pass status.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    catalog_path = Path(args.catalog)
    catalog = _load_json(catalog_path)
    matrix_path = _resolve_matrix_path(
        catalog,
        explicit_path=args.matrix,
        catalog_path=catalog_path,
    )
    matrix = _load_json(matrix_path) if matrix_path and matrix_path.exists() else {}
    payload = build_llm_repair_case_catalog_audit(
        catalog,
        matrix,
        catalog_path=str(catalog_path),
        matrix_path=str(matrix_path) if matrix_path else "",
    )
    write_llm_repair_case_catalog_audit_artifacts(payload, args.output_dir)
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_llm_repair_case_catalog_audit_markdown(payload))
    if args.require_pass and payload["status"] != "pass":
        raise SystemExit(1)


def _audit_case(
    case: dict[str, Any],
    *,
    row_by_key: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "")
    row = row_by_key.get(_case_match_key(case))
    row_data = _dict(row)
    matched = bool(row)
    expected_class = str(case.get("expected_class") or "")
    matrix_class = str(row_data.get("class") or "") if row else ""
    evidence_status = str(row_data.get("evidence_status") or "") if row else "missing"
    agent_loop_complete = _agent_loop_trace_complete(_dict(row_data.get("agent_loop_evidence"))) if row else False
    blocker_category = str(row_data.get("blocker_category") or case.get("expected_blocker_category") or "")
    patch_judge_outcomes = _dict(row_data.get("patch_judge_outcome_counts")) if row else {}
    source_report_path = str(
        case.get("source_report_path")
        or case.get("suite_report_path")
        or case.get("artifact_path")
        or ""
    )
    notes: list[str] = []
    if not matched:
        notes.append("matrix_row_missing")
    if matched and expected_class and expected_class != matrix_class:
        notes.append("class_mismatch")
    expected_blocker = str(case.get("expected_blocker_category") or "")
    if matched and expected_blocker and expected_blocker != blocker_category:
        notes.append("blocker_category_mismatch")
    if matched and evidence_status != "complete":
        notes.append("evidence_incomplete")
    if matched and not agent_loop_complete:
        notes.append("agent_loop_incomplete")
    return {
        **case,
        "case_id": case_id,
        "repo": str(case.get("repo") or ""),
        "expected_class": expected_class,
        "expected_blocker_category": expected_blocker,
        "source_report_path": source_report_path,
        "matched": matched,
        "matrix_class": matrix_class,
        "class_matches": matched and (not expected_class or expected_class == matrix_class),
        "blocker_category": blocker_category,
        "blocker_category_matches": (
            matched and (not expected_blocker or expected_blocker == blocker_category)
        ),
        "evidence_status": evidence_status,
        "agent_loop_complete": agent_loop_complete,
        "patch_judge_mode": str(row_data.get("patch_judge_mode") or "") if row else "",
        "patch_judge_status": str(row_data.get("patch_judge_status") or "") if row else "",
        "patch_judge_candidate_count": _int(row_data.get("patch_judge_candidate_count")) if row else 0,
        "patch_judge_outcome_counts": patch_judge_outcomes,
        "matrix_report_path": str(row_data.get("report_path") or "") if row else "",
        "notes": notes,
    }


def _audit_counts(
    cases: list[dict[str, Any]],
    *,
    rows: list[dict[str, Any]],
    source_report_records: list[dict[str, Any]],
) -> dict[str, Any]:
    matched_cases = [case for case in cases if bool(case.get("matched"))]
    matched_direct = [
        case for case in matched_cases if case.get("matrix_class") == "llm_direct_success"
    ]
    matched_reflection = [
        case
        for case in matched_cases
        if case.get("matrix_class") == "llm_reflection_success"
    ]
    matched_blockers = [
        case for case in matched_cases if case.get("matrix_class") == "llm_blocker"
    ]
    source_report_count = len(source_report_records)
    missing_source_report_count = sum(
        1 for item in source_report_records if not bool(item.get("exists"))
    )
    return {
        "declared_case_count": len(cases),
        "matched_case_count": len(matched_cases),
        "missing_case_count": len(cases) - len(matched_cases),
        "class_mismatch_count": sum(
            1
            for case in matched_cases
            if str(case.get("expected_class") or "")
            and str(case.get("expected_class") or "") != str(case.get("matrix_class") or "")
        ),
        "blocker_category_mismatch_count": sum(
            1
            for case in matched_cases
            if str(case.get("expected_blocker_category") or "")
            and str(case.get("expected_blocker_category") or "") != str(case.get("blocker_category") or "")
        ),
        "source_report_count": source_report_count,
        "missing_source_report_count": missing_source_report_count,
        "declared_class_counts": dict(
            sorted(Counter(str(case.get("expected_class") or "unknown") for case in cases).items())
        ),
        "matched_class_counts": dict(
            sorted(Counter(str(case.get("matrix_class") or "unknown") for case in matched_cases).items())
        ),
        "matrix_class_counts": dict(
            sorted(Counter(str(row.get("class") or "unknown") for row in rows).items())
        ),
        "matched_blocker_category_counts": dict(
            sorted(
                Counter(
                    str(case.get("blocker_category") or "unknown")
                    for case in matched_blockers
                ).items()
            )
        ),
        "matrix_case_count": len(rows),
        "llm_direct_success_count": len(matched_direct),
        "llm_reflection_success_count": len(matched_reflection),
        "llm_blocker_count": len(matched_blockers),
        "llm_direct_evidence_complete_count": sum(
            1 for case in matched_direct if case.get("evidence_status") == "complete"
        ),
        "llm_reflection_evidence_complete_count": sum(
            1
            for case in matched_reflection
            if case.get("evidence_status") == "complete"
        ),
        "llm_blocker_evidence_complete_count": sum(
            1 for case in matched_blockers if case.get("evidence_status") == "complete"
        ),
        "agent_loop_trace_complete_count": sum(
            1 for case in matched_cases if bool(case.get("agent_loop_complete"))
        ),
        "patch_judge_llm_ready_case_count": sum(
            1
            for case in matched_cases
            if str(case.get("patch_judge_mode") or "").lower() == "llm"
            and str(case.get("patch_judge_status") or "").lower() == "ready"
            and _int(case.get("patch_judge_candidate_count")) > 0
        ),
        "patch_judge_accept_success_count": sum(
            _int(_dict(case.get("patch_judge_outcome_counts")).get("accept_success"))
            for case in matched_cases
        ),
        "patch_judge_reject_failure_count": sum(
            _int(_dict(case.get("patch_judge_outcome_counts")).get("reject_failure"))
            for case in matched_cases
        ),
        "llm_failed_blocker_count": sum(
            1
            for case in matched_blockers
            if case.get("blocker_category") == "llm_failed_blocker"
        ),
        "environment_blocker_count": sum(
            1
            for case in matched_blockers
            if case.get("blocker_category") == "environment_blocker"
        ),
        "no_test_oracle_blocker_count": sum(
            1
            for case in matched_blockers
            if case.get("blocker_category") == "no_test_oracle_blocker"
        ),
        "safety_gate_blocker_count": sum(
            1
            for case in matched_blockers
            if case.get("blocker_category") == "safety_gate_blocker"
        ),
    }


def _case_record(item: Any) -> dict[str, Any]:
    case = _dict(item)
    case_id = str(
        case.get("case_id")
        or case.get("name")
        or case.get("matrix_name")
        or ""
    )
    return {
        **case,
        "case_id": case_id,
        "expected_class": str(
            case.get("expected_class")
            or case.get("class")
            or ""
        ),
        "expected_blocker_category": str(
            case.get("expected_blocker_category")
            or case.get("blocker_category")
            or ""
        ),
    }


def _case_match_key(case: dict[str, Any]) -> str:
    return str(
        case.get("matrix_name")
        or case.get("matrix_case_id")
        or case.get("case_id")
        or case.get("name")
        or ""
    )


def _index_matrix_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        for key in (
            row.get("case_id"),
            row.get("name"),
            row.get("matrix_name"),
        ):
            key_text = str(key or "")
            if key_text and key_text not in indexed:
                indexed[key_text] = row
    return indexed


def _source_report_records(
    catalog: dict[str, Any],
    *,
    catalog_path: str,
) -> list[dict[str, Any]]:
    root = Path(catalog_path).resolve().parent if catalog_path else Path.cwd()
    referenced: dict[str, set[str]] = {}
    for value in _list(catalog.get("source_reports")) + _list(
        catalog.get("source_report_paths")
    ):
        path = str(value)
        if path:
            referenced.setdefault(path, set())
    for case_value in _list(catalog.get("cases")):
        case = _case_record(case_value)
        path = str(
            case.get("source_report_path")
            or case.get("suite_report_path")
            or case.get("artifact_path")
            or ""
        )
        if path:
            referenced.setdefault(path, set()).add(str(case.get("case_id") or ""))
    records = []
    for path, case_ids in sorted(referenced.items()):
        resolved = _resolve_existing_path(path, root=root)
        records.append(
            {
                "path": path,
                "resolved_path": str(resolved),
                "exists": resolved.exists(),
                "referenced_by": sorted(case_id for case_id in case_ids if case_id),
            }
        )
    return records


def _target_checks(
    counts: dict[str, Any],
    targets: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _min_check(
            "declared_repair_case_count",
            counts.get("declared_case_count"),
            targets.get("case_count"),
        ),
        _min_check(
            "matched_repair_case_count",
            counts.get("matched_case_count"),
            targets.get("case_count"),
        ),
        _equals_check(
            "all_declared_cases_matched",
            counts.get("missing_case_count"),
            0,
        ),
        _equals_check(
            "class_mismatch_count",
            counts.get("class_mismatch_count"),
            0,
        ),
        _equals_check(
            "blocker_category_mismatch_count",
            counts.get("blocker_category_mismatch_count"),
            0,
        ),
        _equals_check(
            "missing_source_report_count",
            counts.get("missing_source_report_count"),
            0,
        ),
        _min_check(
            "llm_direct_success",
            counts.get("llm_direct_success_count"),
            targets.get("llm_direct_success"),
        ),
        _min_check(
            "llm_reflection_success",
            counts.get("llm_reflection_success_count"),
            targets.get("llm_reflection_success"),
        ),
        _min_check(
            "llm_blocker",
            counts.get("llm_blocker_count"),
            targets.get("llm_blocker"),
        ),
        _min_check(
            "llm_direct_evidence_complete",
            counts.get("llm_direct_evidence_complete_count"),
            targets.get("llm_direct_evidence_complete"),
        ),
        _min_check(
            "llm_reflection_evidence_complete",
            counts.get("llm_reflection_evidence_complete_count"),
            targets.get("llm_reflection_evidence_complete"),
        ),
        _min_check(
            "llm_blocker_evidence_complete",
            counts.get("llm_blocker_evidence_complete_count"),
            targets.get("llm_blocker_evidence_complete"),
        ),
        _min_check(
            "llm_patch_judge_ready",
            counts.get("patch_judge_llm_ready_case_count"),
            targets.get("llm_patch_judge_ready"),
        ),
        _min_check(
            "llm_patch_judge_accept_success",
            counts.get("patch_judge_accept_success_count"),
            targets.get("llm_patch_judge_accept_success"),
        ),
        _min_check(
            "llm_patch_judge_reject_failure",
            counts.get("patch_judge_reject_failure_count"),
            targets.get("llm_patch_judge_reject_failure"),
        ),
        _min_check(
            "llm_failed_blocker",
            counts.get("llm_failed_blocker_count"),
            targets.get("llm_failed_blocker"),
        ),
        _min_check(
            "environment_blocker",
            counts.get("environment_blocker_count"),
            targets.get("environment_blocker"),
        ),
        _min_check(
            "no_test_oracle_blocker",
            counts.get("no_test_oracle_blocker_count"),
            targets.get("no_test_oracle_blocker"),
        ),
        _min_check(
            "safety_gate_blocker",
            counts.get("safety_gate_blocker_count"),
            targets.get("safety_gate_blocker"),
        ),
        _min_check(
            "agent_loop_trace_complete",
            counts.get("agent_loop_trace_complete_count"),
            targets.get("agent_loop_trace_complete"),
        ),
    ]


def _next_actions(
    missing: list[str],
    counts: dict[str, Any],
) -> list[str]:
    actions: list[str] = []
    missing_set = set(missing)
    if {
        "declared_repair_case_count",
        "matched_repair_case_count",
        "all_declared_cases_matched",
    } & missing_set:
        actions.append(
            "Add or run repair cases until the catalog has 20 matched matrix rows."
        )
    if "missing_source_report_count" in missing_set:
        actions.append(
            "Refresh source suite reports or correct catalog paths before using the catalog as P6 evidence."
        )
    if "class_mismatch_count" in missing_set or "blocker_category_mismatch_count" in missing_set:
        actions.append(
            "Align expected catalog classes and blocker categories with the measured matrix rows."
        )
    if {
        "llm_direct_success",
        "llm_reflection_success",
        "llm_blocker",
        "llm_direct_evidence_complete",
        "llm_reflection_evidence_complete",
        "llm_blocker_evidence_complete",
    } & missing_set:
        actions.append(
            "Run additional LLM repair suites with real sandbox validation and complete artifact evidence."
        )
    if {
        "llm_patch_judge_ready",
        "llm_patch_judge_accept_success",
        "llm_patch_judge_reject_failure",
    } & missing_set:
        actions.append(
            "Refresh LLM patch judge evidence while preserving sandbox pytest as the success authority."
        )
    if {
        "llm_failed_blocker",
        "environment_blocker",
        "no_test_oracle_blocker",
        "safety_gate_blocker",
    } & missing_set:
        actions.append(
            "Add blocker cases for missing LLM configuration, environment failure, no test oracle, and safety gate rejection."
        )
    if "agent_loop_trace_complete" in missing_set:
        actions.append(
            "Regenerate AgentController traces so every matched repair case records Observe, Plan, Act, Verify, Reflect, and Replan."
        )
    if not actions and _int(counts.get("declared_case_count")) == 0:
        actions.append("Declare repair cases before running the catalog audit.")
    return _unique(actions)


def _resolve_matrix_path(
    catalog: dict[str, Any],
    *,
    explicit_path: str | None,
    catalog_path: Path,
) -> Path | None:
    raw = str(
        explicit_path
        or catalog.get("matrix_path")
        or catalog.get("llm_repair_evaluation_matrix_path")
        or ""
    )
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return path
    return catalog_path.resolve().parent / path


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON payload must be an object: {path}")
    return payload


def _resolve_existing_path(path: str, *, root: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return root / candidate


def _path_exists(path: str) -> bool:
    return bool(path) and Path(path).exists()


def _agent_loop_trace_complete(loop: dict[str, Any]) -> bool:
    return all(
        bool(str(loop.get(step) or "").strip())
        for step in ("observe", "plan", "act", "verify", "reflect", "replan")
    )


def _min_check(name: str, actual: Any, expected: Any) -> dict[str, Any]:
    actual_value = _int(actual)
    expected_value = _int(expected)
    return {
        "name": name,
        "actual": actual_value,
        "expected": expected_value,
        "passed": actual_value >= expected_value,
    }


def _equals_check(name: str, actual: Any, expected: Any) -> dict[str, Any]:
    actual_value = _int(actual)
    expected_value = _int(expected)
    return {
        "name": name,
        "actual": actual_value,
        "expected": expected_value,
        "passed": actual_value == expected_value,
    }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={_int(value)}" for key, value in sorted(counts.items()))


def _format_list(values: list[Any]) -> str:
    items = [str(value) for value in values if str(value)]
    return ", ".join(items) if items else "none"


def _markdown_cell(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", " ")


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


if __name__ == "__main__":
    main()
