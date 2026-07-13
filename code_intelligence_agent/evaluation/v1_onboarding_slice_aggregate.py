from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any


def build_v1_onboarding_slice_aggregate(
    manifest: dict[str, Any],
    suite_reports: list[dict[str, Any]],
    *,
    manifest_path: str = "",
    suite_paths: list[str] | None = None,
) -> dict[str, Any]:
    runs = [_dict(item) for item in _list(manifest.get("runs"))]
    paths = suite_paths or []
    evidence_by_name = _evidence_by_name(suite_reports, paths)
    rows: list[dict[str, Any]] = []
    scenario_counts: Counter[str] = Counter()
    completed_elapsed_ms = 0
    for index, run in enumerate(runs):
        name = str(run.get("name") or index)
        evidence = _dict(evidence_by_name.get(name))
        status = "missing"
        if evidence:
            status = "pass" if bool(evidence.get("passed")) else "failed"
            scenario_counts.update(
                str(tag) for tag in _list(run.get("scenario_tags")) if str(tag)
            )
            completed_elapsed_ms += _int(evidence.get("elapsed_ms"))
        rows.append(
            {
                "manifest_index": index,
                "name": name,
                "repo": str(run.get("repo") or ""),
                "scenario_tags": _list(run.get("scenario_tags")),
                "expected_status": str(run.get("expected_status") or "pass"),
                "evidence_status": status,
                "suite_path": str(evidence.get("suite_path") or ""),
                "run_output_dir": str(evidence.get("output_dir") or ""),
                "status": str(evidence.get("status") or ""),
                "passed": bool(evidence.get("passed", False)),
                "elapsed_ms": _int(evidence.get("elapsed_ms")),
                "error": str(evidence.get("error") or ""),
            }
        )
    completed = [row for row in rows if row["evidence_status"] != "missing"]
    passed = [row for row in rows if row["evidence_status"] == "pass"]
    failed = [row for row in rows if row["evidence_status"] == "failed"]
    missing = [row for row in rows if row["evidence_status"] == "missing"]
    complete = len(completed) == len(rows) and not failed
    status = "complete" if complete else "partial" if completed else "missing"
    return {
        "status": status,
        "reason": _reason(status, failed, missing),
        "manifest_path": manifest_path,
        "suite_paths": paths,
        "summary": {
            "run_count": len(completed),
            "manifest_run_count": len(rows),
            "suite_slice_applied": bool(missing),
            "suite_slice_start_index": 0,
            "suite_slice_limit": None,
            "suite_slice_run_count": len(completed),
            "completed_count": len(completed),
            "missing_count": len(missing),
            "failed_count": len(failed),
            "agent_passed_count": len(passed),
            "agent_failed_count": len(failed),
            "command_failed_count": sum(1 for row in failed if row.get("error")),
            "expectation_failed_count": len(failed),
            "objective_compliance_pass_count": len(passed),
            "agent_controller_loop_complete_count": len(passed),
            "scenario_tag_counts": dict(sorted(scenario_counts.items())),
            "scenario_tag_kind_count": len(scenario_counts),
            "suite_run_elapsed_ms_total": completed_elapsed_ms,
            "suite_run_elapsed_ms_average": (
                round(completed_elapsed_ms / len(completed), 2)
                if completed
                else 0.0
            ),
            "next_missing_start_index": (
                _int(missing[0]["manifest_index"]) if missing else None
            ),
        },
        "rows": rows,
        "missing_runs": [
            {"manifest_index": row["manifest_index"], "name": row["name"], "repo": row["repo"]}
            for row in missing
        ],
        "failed_runs": [
            {
                "manifest_index": row["manifest_index"],
                "name": row["name"],
                "repo": row["repo"],
                "status": row["status"],
                "error": row["error"],
            }
            for row in failed
        ],
        "next_actions": _next_actions(rows, failed, missing),
    }


def write_v1_onboarding_slice_aggregate_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "v1_onboarding_slice_aggregate.json"
    markdown_path = root / "v1_onboarding_slice_aggregate.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_v1_onboarding_slice_aggregate_markdown(payload),
        encoding="utf-8",
    )
    return {
        "v1_onboarding_slice_aggregate_json": str(json_path),
        "v1_onboarding_slice_aggregate_markdown": str(markdown_path),
    }


def render_v1_onboarding_slice_aggregate_markdown(payload: dict[str, Any]) -> str:
    summary = _dict(payload.get("summary"))
    lines = [
        "# V1 Onboarding Slice Aggregate",
        "",
        f"- Status: `{_markdown_cell(payload.get('status') or 'unknown')}`",
        f"- Reason: `{_markdown_cell(payload.get('reason') or 'none')}`",
        f"- Completed: {_int(summary.get('completed_count'))}/{_int(summary.get('manifest_run_count'))}",
        f"- Passed: {_int(summary.get('agent_passed_count'))}",
        f"- Failed: {_int(summary.get('failed_count'))}",
        f"- Missing: {_int(summary.get('missing_count'))}",
        f"- Average Runtime ms: {_float(summary.get('suite_run_elapsed_ms_average'))}",
        f"- Next Missing Start Index: {_markdown_cell(summary.get('next_missing_start_index'))}",
        "",
        "## Runs",
        "",
        "| Index | Name | Repo | Evidence | Elapsed ms | Suite |",
        "| ---: | --- | --- | --- | ---: | --- |",
    ]
    for row_value in _list(payload.get("rows")):
        row = _dict(row_value)
        lines.append(
            "| "
            f"{_int(row.get('manifest_index'))} | "
            f"{_markdown_cell(row.get('name'))} | "
            f"{_markdown_cell(row.get('repo'))} | "
            f"`{_markdown_cell(row.get('evidence_status'))}` | "
            f"{_int(row.get('elapsed_ms'))} | "
            f"{_markdown_cell(row.get('suite_path'))} |"
        )
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {action}")
    if not _list(payload.get("next_actions")):
        lines.append("- Full v1 onboarding evidence is complete.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate V1 onboarding suite slices into one progress report."
    )
    parser.add_argument("manifest")
    parser.add_argument("output_dir")
    parser.add_argument("suite_json", nargs="+")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Exit with status 1 unless all manifest runs have passing evidence.",
    )
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    suite_paths = [str(path) for path in args.suite_json]
    payload = build_v1_onboarding_slice_aggregate(
        _load_json(manifest_path),
        [_load_json(Path(path)) for path in suite_paths],
        manifest_path=str(manifest_path),
        suite_paths=suite_paths,
    )
    write_v1_onboarding_slice_aggregate_artifacts(payload, args.output_dir)
    if args.format == "markdown":
        print(render_v1_onboarding_slice_aggregate_markdown(payload), end="")
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    if args.require_complete and payload["status"] != "complete":
        raise SystemExit(1)


def _evidence_by_name(
    suite_reports: list[dict[str, Any]],
    suite_paths: list[str],
) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for index, suite in enumerate(suite_reports):
        path = suite_paths[index] if index < len(suite_paths) else ""
        for run in _list(suite.get("runs")):
            row = _dict(run)
            name = str(row.get("name") or "")
            if not name:
                continue
            evidence[name] = {**row, "suite_path": path}
    return evidence


def _reason(status: str, failed: list[dict[str, Any]], missing: list[dict[str, Any]]) -> str:
    if status == "complete":
        return "v1_onboarding_all_manifest_runs_passed"
    if failed:
        return "v1_onboarding_slice_failures_present"
    if missing:
        return "v1_onboarding_missing_manifest_runs"
    return "v1_onboarding_no_slice_evidence"


def _next_actions(
    rows: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    missing: list[dict[str, Any]],
) -> list[str]:
    actions: list[str] = []
    if failed:
        actions.append(
            "Inspect failed slice runs and rerun those manifest indexes before final aggregation."
        )
    if missing:
        start = _int(missing[0].get("manifest_index"))
        remaining = len(missing)
        limit = min(5, remaining)
        actions.append(
            f"Run next onboarding slice with --start-index {start} --limit-runs {limit}."
        )
    if rows and not failed and not missing:
        actions.append(
            "Use this aggregate as the onboarding-suite input to v1_evaluation_summary."
        )
    return actions


def _load_json(path: Path) -> dict[str, Any]:
    return _dict(json.loads(path.read_text(encoding="utf-8")))


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


def _markdown_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":  # pragma: no cover
    main()
