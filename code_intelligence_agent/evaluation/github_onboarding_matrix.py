from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from code_intelligence_agent.agents.controller import (
    build_agent_controller_plan,
    write_agent_controller_artifacts,
)
from code_intelligence_agent.evaluation.github_repository_profile import (
    render_github_repository_profile_markdown,
)
from code_intelligence_agent.evaluation.repository_test_environment import (
    plan_repository_test_environment,
    write_repository_test_environment_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_execution_plan import (
    plan_repository_test_execution,
    write_repository_test_execution_plan_artifacts,
)


REQUIRED_ONBOARDING_ARTIFACTS: tuple[tuple[str, str, str], ...] = (
    ("repository_profile", "repository_profile_json", "repository_profile.json"),
    (
        "repository_profile_markdown",
        "repository_profile_markdown",
        "repository_profile.md",
    ),
    ("repository_structure", "repository_structure_json", "repository_structure.json"),
    (
        "repository_structure_markdown",
        "repository_structure_markdown",
        "repository_structure.md",
    ),
    (
        "repository_test_discovery",
        "repository_test_discovery_json",
        "repository_test_discovery.json",
    ),
    (
        "repository_test_discovery_markdown",
        "repository_test_discovery_markdown",
        "repository_test_discovery.md",
    ),
    (
        "repository_test_environment",
        "repository_test_environment_json",
        "repository_test_environment.json",
    ),
    (
        "repository_test_environment_markdown",
        "repository_test_environment_markdown",
        "repository_test_environment.md",
    ),
    (
        "repository_test_execution_plan",
        "repository_test_execution_plan_json",
        "repository_test_execution_plan.json",
    ),
    (
        "repository_test_execution_plan_markdown",
        "repository_test_execution_plan_markdown",
        "repository_test_execution_plan.md",
    ),
    ("agent_policy_trace", "agent_policy_trace_json", "agent_policy_trace.json"),
    (
        "agent_policy_trace_markdown",
        "agent_policy_trace_markdown",
        "agent_policy_trace.md",
    ),
)

REQUIRED_SCENARIOS: tuple[tuple[str, str], ...] = (
    ("pytest_project", "pytest project"),
    ("src_layout_project", "src-layout project"),
    ("pyproject_project", "pyproject project"),
    ("requirements_project", "requirements project"),
    ("tox_or_nox_project", "tox/nox project"),
    ("no_python_sources", "no Python sources"),
    ("no_tests", "no tests"),
    ("dependency_missing", "missing test dependency"),
    ("timeout", "test timeout"),
    ("failing_test_evidence", "failing-test evidence"),
)


def build_github_onboarding_matrix(
    report_paths: list[str | Path],
    *,
    required_case_count: int = 10,
) -> dict[str, Any]:
    rows = [_build_onboarding_row(path) for path in report_paths]
    scenario_coverage = _scenario_coverage(rows)
    artifact_coverage = _artifact_coverage(rows)
    blocker_distribution = Counter()
    for row in rows:
        for blocker in _list(row.get("blockers")):
            blocker_distribution[str(blocker)] += 1
    checks = _matrix_checks(
        rows,
        scenario_coverage=scenario_coverage,
        required_case_count=required_case_count,
    )
    status = "pass" if checks and all(bool(check.get("passed")) for check in checks) else "incomplete"
    return {
        "status": status,
        "reason": (
            "p6_onboarding_matrix_complete"
            if status == "pass"
            else "p6_onboarding_matrix_incomplete"
        ),
        "required_case_count": required_case_count,
        "case_count": len(rows),
        "passed_check_count": sum(1 for check in checks if bool(check.get("passed"))),
        "check_count": len(checks),
        "checks": checks,
        "required_scenarios": [
            {"id": key, "description": description}
            for key, description in REQUIRED_SCENARIOS
        ],
        "scenario_coverage": scenario_coverage,
        "artifact_coverage": artifact_coverage,
        "blocker_distribution": dict(sorted(blocker_distribution.items())),
        "rows": rows,
    }


def backfill_github_onboarding_artifacts(
    report_paths: list[str | Path],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    rows = [
        _backfill_onboarding_artifacts_for_report(Path(path), dry_run=dry_run)
        for path in report_paths
    ]
    status_counts = Counter(str(row.get("status") or "unknown") for row in rows)
    return {
        "status": "pass"
        if rows and not any(str(row.get("status")) == "error" for row in rows)
        else "error",
        "dry_run": dry_run,
        "report_count": len(rows),
        "status_counts": dict(sorted(status_counts.items())),
        "rows": rows,
    }


def _backfill_onboarding_artifacts_for_report(
    report_path: Path,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    resolved = _resolve_report_path(report_path)
    payload = _read_json(resolved)
    if not payload:
        return {
            "report_path": str(resolved),
            "status": "error",
            "reason": "report_not_readable",
            "written_artifacts": [],
            "skipped_artifacts": [],
        }
    report_dir = resolved.parent
    summary = _dict(payload.get("summary")) if "summary" in payload else payload
    output_paths = {
        **_dict(payload.get("output_paths")),
        **_dict(summary.get("output_paths")),
    }
    output_dir = _report_output_dir(payload, summary, report_dir)
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    skipped: list[str] = []

    profile = (
        _artifact_payload(
            summary,
            output_paths,
            output_dir,
            "repository_profile_json",
            "repository_profile.json",
            report_dir=report_dir,
        )
        or _dict(summary.get("repository_profile"))
        or _dict(_dict(payload.get("onboarding_report")).get("repository_profile"))
        or _derive_profile_from_structure(_dict(summary.get("repository_structure")))
    )
    structure = (
        _artifact_payload(
            summary,
            output_paths,
            output_dir,
            "repository_structure_json",
            "repository_structure.json",
            report_dir=report_dir,
        )
        or _dict(summary.get("repository_structure"))
    )
    test_discovery = (
        _artifact_payload(
            summary,
            output_paths,
            output_dir,
            "repository_test_discovery_json",
            "repository_test_discovery.json",
            report_dir=report_dir,
        )
        or _dict(summary.get("repository_test_discovery"))
        or _derive_test_discovery(profile, structure)
    )
    environment = _artifact_payload(
        summary,
        output_paths,
        output_dir,
        "repository_test_environment_json",
        "repository_test_environment.json",
        report_dir=report_dir,
    ) or plan_repository_test_environment(
        profile,
        repository_root=_repository_checkout_root(output_dir),
    )
    execution_plan = _artifact_payload(
        summary,
        output_paths,
        output_dir,
        "repository_test_execution_plan_json",
        "repository_test_execution_plan.json",
        report_dir=report_dir,
    ) or plan_repository_test_execution(
        profile,
        repository_test_environment=environment,
        repository_root=_repository_checkout_root(output_dir),
    )

    _write_missing_artifact(
        output_dir / "repository_profile.json",
        output_dir / "repository_profile.md",
        profile,
        render_github_repository_profile_markdown(profile),
        dry_run=dry_run,
        written=written,
        skipped=skipped,
    )
    _write_missing_artifact(
        output_dir / "repository_structure.json",
        output_dir / "repository_structure.md",
        structure,
        _render_simple_artifact_markdown("Repository Structure", structure),
        dry_run=dry_run,
        written=written,
        skipped=skipped,
    )
    _write_missing_artifact(
        output_dir / "repository_test_discovery.json",
        output_dir / "repository_test_discovery.md",
        test_discovery,
        _render_test_discovery_markdown(test_discovery),
        dry_run=dry_run,
        written=written,
        skipped=skipped,
    )
    if _artifact_missing(output_dir / "repository_test_environment.json") or _artifact_missing(
        output_dir / "repository_test_environment.md"
    ):
        if dry_run:
            skipped.append("repository_test_environment")
        else:
            paths = write_repository_test_environment_artifacts(environment, output_dir)
            written.extend(sorted(Path(path).name for path in paths.values()))
    else:
        skipped.append("repository_test_environment")
    if _artifact_missing(output_dir / "repository_test_execution_plan.json") or _artifact_missing(
        output_dir / "repository_test_execution_plan.md"
    ):
        if dry_run:
            skipped.append("repository_test_execution_plan")
        else:
            paths = write_repository_test_execution_plan_artifacts(
                execution_plan,
                output_dir,
            )
            written.extend(sorted(Path(path).name for path in paths.values()))
    else:
        skipped.append("repository_test_execution_plan")

    if _artifact_missing(output_dir / "agent_policy_trace.json") or _artifact_missing(
        output_dir / "agent_policy_trace.md"
    ):
        if dry_run:
            skipped.append("agent_policy_trace")
        else:
            controller = _dict(summary.get("agent_controller")) or build_agent_controller_plan(
                summary
            )
            paths = write_agent_controller_artifacts(controller, output_dir)
            written.extend(
                sorted(
                    Path(path).name
                    for key, path in paths.items()
                    if key.startswith("agent_policy_trace")
                    or key.startswith("agent_action_registry")
                    or key.startswith("agent_controller")
                )
            )
    else:
        skipped.append("agent_policy_trace")

    return {
        "report_path": str(resolved),
        "output_dir": str(output_dir),
        "status": "pass",
        "reason": "derived_artifacts_backfilled",
        "dry_run": dry_run,
        "written_artifacts": sorted(dict.fromkeys(written)),
        "skipped_artifacts": sorted(dict.fromkeys(skipped)),
    }


def write_github_onboarding_matrix_artifacts(
    matrix: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "github_onboarding_matrix.json"
    markdown_path = root / "github_onboarding_matrix.md"
    json_path.write_text(
        json.dumps(matrix, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_github_onboarding_matrix_markdown(matrix),
        encoding="utf-8",
    )
    return {
        "github_onboarding_matrix_json": str(json_path),
        "github_onboarding_matrix_markdown": str(markdown_path),
    }


def render_github_onboarding_matrix_markdown(matrix: dict[str, Any]) -> str:
    lines = [
        "# GitHub Onboarding Matrix",
        "",
        f"- Status: `{_markdown_cell(matrix.get('status') or 'unknown')}`",
        f"- Reason: `{_markdown_cell(matrix.get('reason') or 'none')}`",
        f"- Cases: {_int(matrix.get('case_count', 0))}/{_int(matrix.get('required_case_count', 10))}",
        (
            "- Checks: "
            f"{_int(matrix.get('passed_check_count', 0))}/"
            f"{_int(matrix.get('check_count', 0))} passed"
        ),
        "",
        "## Checks",
        "",
        "| Check | Passed | Expected | Actual | Missing |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check_value in _list(matrix.get("checks")):
        check = _dict(check_value)
        lines.append(
            "| "
            f"{_markdown_cell(check.get('name') or '')} | "
            f"{str(bool(check.get('passed', False))).lower()} | "
            f"{_markdown_cell(check.get('expected') or '')} | "
            f"{_markdown_cell(check.get('actual') or '')} | "
            f"{_markdown_cell(', '.join(str(item) for item in _list(check.get('missing'))))} |"
        )
    if not _list(matrix.get("checks")):
        lines.append("| none | false | none | none | none |")
    lines.extend(
        [
            "",
            "## Scenario Coverage",
            "",
            "| Scenario | Count | Runs |",
            "| --- | ---: | --- |",
        ]
    )
    coverage = _dict(matrix.get("scenario_coverage"))
    for key, description in REQUIRED_SCENARIOS:
        row = _dict(coverage.get(key))
        lines.append(
            "| "
            f"{_markdown_cell(description)} | "
            f"{_int(row.get('count', 0))} | "
            f"{_markdown_cell(', '.join(str(item) for item in _list(row.get('runs'))))} |"
        )
    lines.extend(
        [
            "",
            "## Repositories",
            "",
            (
                "| Name | Repo | Status | Scenarios | Missing Artifacts | "
                "Policy Action | Blockers |"
            ),
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row_value in _list(matrix.get("rows")):
        row = _dict(row_value)
        policy = _dict(row.get("policy_trace"))
        lines.append(
            "| "
            f"{_markdown_cell(row.get('name') or '')} | "
            f"{_markdown_cell(row.get('repo') or '')} | "
            f"{_markdown_cell(row.get('status') or '')} | "
            f"{_markdown_cell(', '.join(str(item) for item in _list(row.get('scenario_tags'))))} | "
            f"{_markdown_cell(', '.join(str(item) for item in _list(row.get('missing_required_artifacts'))))} | "
            f"{_markdown_cell(policy.get('canonical_action') or policy.get('selected_action') or 'none')} | "
            f"{_markdown_cell(', '.join(str(item) for item in _list(row.get('blockers'))))} |"
        )
    if not _list(matrix.get("rows")):
        lines.append("| none | none | missing | none | all | none | none |")
    lines.extend(
        [
            "",
            "## Artifact Coverage",
            "",
            "| Artifact | Present | Missing |",
            "| --- | ---: | ---: |",
        ]
    )
    for name, counts in sorted(_dict(matrix.get("artifact_coverage")).items()):
        row = _dict(counts)
        lines.append(
            "| "
            f"{_markdown_cell(name)} | "
            f"{_int(row.get('present', 0))} | "
            f"{_int(row.get('missing', 0))} |"
        )
    return "\n".join(lines) + "\n"


def _build_onboarding_row(report_path: str | Path) -> dict[str, Any]:
    resolved = _resolve_report_path(report_path)
    payload = _read_json(resolved)
    report_dir = resolved.parent
    summary = _dict(payload.get("summary")) if "summary" in payload else payload
    output_paths = {
        **_dict(payload.get("output_paths")),
        **_dict(summary.get("output_paths")),
    }
    output_dir = _report_output_dir(payload, summary, report_dir)
    profile = (
        _artifact_payload(
            summary,
            output_paths,
            output_dir,
            "repository_profile_json",
            "repository_profile.json",
            report_dir=report_dir,
        )
        or _dict(summary.get("repository_profile"))
        or _dict(_dict(payload.get("onboarding_report")).get("repository_profile"))
        or _derive_profile_from_structure(_dict(summary.get("repository_structure")))
    )
    structure = (
        _artifact_payload(
            summary,
            output_paths,
            output_dir,
            "repository_structure_json",
            "repository_structure.json",
            report_dir=report_dir,
        )
        or _dict(summary.get("repository_structure"))
    )
    test_discovery = (
        _artifact_payload(
            summary,
            output_paths,
            output_dir,
            "repository_test_discovery_json",
            "repository_test_discovery.json",
            report_dir=report_dir,
        )
        or _dict(summary.get("repository_test_discovery"))
        or _derive_test_discovery(profile, structure)
    )
    environment = _artifact_payload(
        summary,
        output_paths,
        output_dir,
        "repository_test_environment_json",
        "repository_test_environment.json",
        report_dir=report_dir,
    )
    execution_plan = _artifact_payload(
        summary,
        output_paths,
        output_dir,
        "repository_test_execution_plan_json",
        "repository_test_execution_plan.json",
        report_dir=report_dir,
    )
    execution_result = _artifact_payload(
        summary,
        output_paths,
        output_dir,
        "repository_test_execution_result_json",
        "repository_test_execution_result.json",
        report_dir=report_dir,
    )
    policy_trace = _artifact_payload(
        summary,
        output_paths,
        output_dir,
        "agent_policy_trace_json",
        "agent_policy_trace.json",
        report_dir=report_dir,
    )
    artifact_status = _required_artifact_status(
        summary,
        output_paths,
        output_dir,
        report_dir=report_dir,
    )
    project_config_files = _project_config_files(profile, structure, test_discovery)
    runners = _test_runners(profile, structure, test_discovery, execution_plan)
    scenario_tags = _scenario_tags(
        profile=profile,
        structure=structure,
        test_discovery=test_discovery,
        environment=environment,
        execution_plan=execution_plan,
        execution_result=execution_result,
        summary=summary,
        project_config_files=project_config_files,
        runners=runners,
    )
    blockers = _blockers(summary, test_discovery, environment, execution_plan, execution_result)
    policy_summary = _policy_trace_summary(policy_trace)
    row_status = (
        "pass"
        if not artifact_status["missing_required_artifacts"]
        and bool(policy_summary.get("present", False))
        else "incomplete"
    )
    return {
        "name": _run_name(payload, summary, resolved),
        "repo": _repo_name(payload, summary),
        "repo_spec": str(payload.get("repo_spec") or summary.get("repo_spec") or ""),
        "input_kind": _input_kind(str(payload.get("repo_spec") or summary.get("repo_spec") or "")),
        "report_path": str(resolved),
        "output_dir": str(output_dir),
        "status": row_status,
        "upstream_status": str(payload.get("status") or summary.get("status") or ""),
        "upstream_passed": bool(payload.get("passed", summary.get("passed", False))),
        "source": _source_summary(profile, structure),
        "layout": _layout_summary(profile, structure),
        "project_config": {
            "files": project_config_files,
            "has_pyproject": "pyproject.toml" in project_config_files,
            "has_requirements": any(
                Path(item).name.startswith("requirements")
                for item in project_config_files
            ),
            "has_setup_py": "setup.py" in project_config_files,
            "has_setup_cfg": "setup.cfg" in project_config_files,
            "has_tox": "tox.ini" in project_config_files,
            "has_nox": "noxfile.py" in project_config_files,
            "has_pytest_ini": "pytest.ini" in project_config_files,
        },
        "tests": _tests_summary(profile, structure, test_discovery, runners),
        "environment": _environment_summary(environment, summary),
        "execution": _execution_summary(execution_plan, execution_result, summary),
        "policy_trace": policy_summary,
        "artifacts": artifact_status["artifacts"],
        "missing_required_artifacts": artifact_status["missing_required_artifacts"],
        "scenario_tags": scenario_tags,
        "blockers": blockers,
    }


def _required_artifact_status(
    summary: dict[str, Any],
    output_paths: dict[str, Any],
    output_dir: Path,
    *,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    missing: list[str] = []
    for artifact_id, path_key, filename in REQUIRED_ONBOARDING_ARTIFACTS:
        path = _artifact_path(
            summary,
            output_paths,
            output_dir,
            path_key,
            filename,
            report_dir=report_dir,
        )
        exists = bool(path and path.is_file() and path.stat().st_size > 0)
        row = {
            "id": artifact_id,
            "filename": filename,
            "path": str(path) if path else "",
            "present": exists,
        }
        artifacts.append(row)
        if not exists:
            missing.append(filename)
    return {
        "artifacts": artifacts,
        "present_count": sum(1 for item in artifacts if bool(item.get("present"))),
        "required_count": len(artifacts),
        "missing_required_artifacts": missing,
    }


def _scenario_tags(
    *,
    profile: dict[str, Any],
    structure: dict[str, Any],
    test_discovery: dict[str, Any],
    environment: dict[str, Any],
    execution_plan: dict[str, Any],
    execution_result: dict[str, Any],
    summary: dict[str, Any],
    project_config_files: list[str],
    runners: list[str],
) -> list[str]:
    tags: list[str] = []
    source = _source_summary(profile, structure)
    tests = _tests_summary(profile, structure, test_discovery, runners)
    env_text = _combined_text(environment, summary)
    execution_text = _combined_text(execution_plan, execution_result, summary)
    if "pytest" in runners or "pytest" in _combined_text(test_discovery, profile):
        tags.append("pytest_project")
    if _layout_summary(profile, structure)["src_layout"]:
        tags.append("src_layout_project")
    if "pyproject.toml" in project_config_files:
        tags.append("pyproject_project")
    if any(Path(item).name.startswith("requirements") for item in project_config_files):
        tags.append("requirements_project")
    if bool({"tox.ini", "noxfile.py"} & set(project_config_files)) or bool(
        {"tox", "nox"} & set(runners)
    ):
        tags.append("tox_or_nox_project")
    if not bool(source.get("has_python_sources", False)):
        tags.append("no_python_sources")
    if not bool(tests.get("has_tests", False)):
        tags.append("no_tests")
    if "missing_dependency" in env_text or "test_tool_missing" in env_text:
        tags.append("dependency_missing")
    if "timeout" in execution_text:
        tags.append("timeout")
    if (
        "test_assertion_failure" in execution_text
        or "failing_tests" in execution_text
        or bool(summary.get("repository_test_dynamic_usable_for_localization", False))
    ):
        tags.append("failing_test_evidence")
    return sorted(dict.fromkeys(tags))


def _matrix_checks(
    rows: list[dict[str, Any]],
    *,
    scenario_coverage: dict[str, Any],
    required_case_count: int,
) -> list[dict[str, Any]]:
    missing_artifact_runs = [
        str(row.get("name") or row.get("repo") or row.get("report_path"))
        for row in rows
        if _list(row.get("missing_required_artifacts"))
    ]
    checks = [
        {
            "name": "minimum_repository_count",
            "passed": len(rows) >= required_case_count,
            "expected": f">={required_case_count}",
            "actual": str(len(rows)),
            "missing": [],
        },
        {
            "name": "required_artifacts_per_repository",
            "passed": not missing_artifact_runs and bool(rows),
            "expected": "all required onboarding artifacts present",
            "actual": f"missing_runs={len(missing_artifact_runs)}",
            "missing": missing_artifact_runs,
        },
    ]
    for scenario_id, description in REQUIRED_SCENARIOS:
        coverage = _dict(scenario_coverage.get(scenario_id))
        checks.append(
            {
                "name": f"scenario:{scenario_id}",
                "passed": _int(coverage.get("count", 0)) > 0,
                "expected": description,
                "actual": f"count={_int(coverage.get('count', 0))}",
                "missing": [] if _int(coverage.get("count", 0)) > 0 else [scenario_id],
            }
        )
    return checks


def _scenario_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    coverage: dict[str, Any] = {}
    for scenario_id, _description in REQUIRED_SCENARIOS:
        runs = [
            str(row.get("name") or row.get("repo") or "")
            for row in rows
            if scenario_id in set(str(item) for item in _list(row.get("scenario_tags")))
        ]
        coverage[scenario_id] = {"count": len(runs), "runs": runs}
    return coverage


def _artifact_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = {}
    for _artifact_id, _path_key, filename in REQUIRED_ONBOARDING_ARTIFACTS:
        counts[filename] = {"present": 0, "missing": 0}
    for row in rows:
        for artifact_value in _list(row.get("artifacts")):
            artifact = _dict(artifact_value)
            filename = str(artifact.get("filename") or "")
            if filename not in counts:
                counts[filename] = {"present": 0, "missing": 0}
            if bool(artifact.get("present", False)):
                counts[filename]["present"] += 1
            else:
                counts[filename]["missing"] += 1
    return counts


def _artifact_payload(
    summary: dict[str, Any],
    output_paths: dict[str, Any],
    output_dir: Path,
    path_key: str,
    filename: str,
    *,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    path = _artifact_path(
        summary,
        output_paths,
        output_dir,
        path_key,
        filename,
        report_dir=report_dir,
    )
    return _read_json(path) if path else {}


def _artifact_path(
    summary: dict[str, Any],
    output_paths: dict[str, Any],
    output_dir: Path,
    path_key: str,
    filename: str,
    *,
    report_dir: Path | None = None,
) -> Path | None:
    raw = str(summary.get(path_key) or output_paths.get(path_key) or "")
    candidates: list[Path] = []
    if raw:
        raw_path = Path(raw)
        candidates.append(raw_path)
        if not raw_path.is_absolute():
            candidates.append(output_dir / raw_path)
            if report_dir is not None:
                candidates.append(report_dir / raw_path)
    candidates.append(output_dir / filename)
    if report_dir is not None:
        candidates.append(report_dir / filename)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0] if candidates else None


def _resolve_report_path(path: str | Path) -> Path:
    value = Path(path)
    if value.is_dir():
        for filename in ("github_repo_intelligence.json", "github_repo_agent.json"):
            candidate = value / filename
            if candidate.is_file():
                return candidate
    return value


def _report_output_dir(
    payload: dict[str, Any],
    summary: dict[str, Any],
    report_dir: Path,
) -> Path:
    raw = str(payload.get("output_dir") or summary.get("output_dir") or "")
    if not raw:
        return report_dir
    value = Path(raw)
    if value.is_absolute() and value.exists():
        return value
    if value.exists():
        return value
    # Older suite outputs can store a path relative to the suite output root
    # while the report itself lives in that directory. Prefer the report
    # location when the recorded path is stale or no longer rooted correctly.
    if (report_dir / "github_repo_intelligence.json").is_file() or (
        report_dir / "github_repo_agent.json"
    ).is_file():
        return report_dir
    return value


def _repository_checkout_root(output_dir: Path) -> Path | None:
    checkout = output_dir / "repository_checkout"
    return checkout if checkout.is_dir() else None


def _artifact_missing(path: Path) -> bool:
    return not path.is_file() or path.stat().st_size <= 0


def _write_missing_artifact(
    json_path: Path,
    markdown_path: Path,
    payload: dict[str, Any],
    markdown: str,
    *,
    dry_run: bool,
    written: list[str],
    skipped: list[str],
) -> None:
    if not payload:
        skipped.append(json_path.stem)
        return
    missing_json = _artifact_missing(json_path)
    missing_markdown = _artifact_missing(markdown_path)
    if not missing_json and not missing_markdown:
        skipped.append(json_path.stem)
        return
    if dry_run:
        skipped.append(json_path.stem)
        return
    if missing_json:
        json_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        written.append(json_path.name)
    if missing_markdown:
        markdown_path.write_text(markdown, encoding="utf-8")
        written.append(markdown_path.name)


def _derive_test_discovery(
    profile: dict[str, Any],
    structure: dict[str, Any],
) -> dict[str, Any]:
    test_structure = _dict(structure.get("test_structure"))
    return {
        "status": str(profile.get("doctor_status") or ""),
        "blocker": str(profile.get("doctor_blocker") or ""),
        "test_source_count": _int(
            profile.get("test_source_count", test_structure.get("test_source_count", 0))
        ),
        "test_source_paths": _list(
            profile.get("test_source_paths", test_structure.get("test_source_paths"))
        ),
        "test_directories": _list(test_structure.get("test_directories")),
        "test_framework_signals": _list(
            profile.get(
                "test_framework_signals",
                test_structure.get("test_framework_signals"),
            )
        ),
        "recommended_test_command": str(
            profile.get(
                "recommended_test_command",
                test_structure.get("recommended_test_command") or "",
            )
        ),
        "test_command_candidates": _list(
            profile.get(
                "test_command_candidates",
                test_structure.get("test_command_candidates"),
            )
        ),
    }


def _derive_profile_from_structure(structure: dict[str, Any]) -> dict[str, Any]:
    if not structure:
        return {}
    package_structure = _dict(structure.get("package_structure"))
    test_structure = _dict(structure.get("test_structure"))
    project_config = _dict(structure.get("project_config"))
    return {
        "discovery_item_count": _int(structure.get("source_entry_count", 0)),
        "imported_source_count": _int(
            structure.get("source_entry_count", structure.get("analyzed_file_count", 0))
        ),
        "python_source_ratio": 1.0
        if _int(structure.get("analyzed_file_count", 0)) > 0
        else 0.0,
        "test_source_count": _int(test_structure.get("test_source_count", 0)),
        "test_source_paths": _list(test_structure.get("test_source_paths")),
        "package_roots": _list(package_structure.get("package_roots")),
        "src_layout_packages": _list(package_structure.get("src_layout_packages")),
        "project_config_files": _list(project_config.get("project_config_files")),
        "project_config_count": _int(
            project_config.get(
                "project_config_count",
                len(_list(project_config.get("project_config_files"))),
            )
        ),
        "test_framework_signals": _list(
            test_structure.get("test_framework_signals")
        ),
        "test_command_candidates": _list(
            test_structure.get("test_command_candidates")
        ),
        "test_command_candidate_count": _int(
            test_structure.get(
                "test_command_candidate_count",
                len(_list(test_structure.get("test_command_candidates"))),
            )
        ),
        "recommended_test_command": str(
            test_structure.get("recommended_test_command") or ""
        ),
        "recommended_target_prefix": str(
            package_structure.get("recommended_target_prefix") or ""
        ),
        "dependency_tool_signals": _list(project_config.get("dependency_tool_signals")),
    }


def _render_test_discovery_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Repository Test Discovery",
        "",
        f"- Status: `{_markdown_cell(payload.get('status') or 'unknown')}`",
        f"- Reason: `{_markdown_cell(payload.get('reason') or 'none')}`",
        f"- Blocker: `{_markdown_cell(payload.get('blocker') or 'none')}`",
        f"- Test Sources: {_int(payload.get('test_source_count', 0))}",
        (
            "- Recommended Test Command: "
            f"`{_markdown_cell(payload.get('recommended_test_command') or 'none')}`"
        ),
        (
            "- Test Framework Signals: "
            f"{_markdown_cell(', '.join(str(item) for item in _list(payload.get('test_framework_signals'))) or 'none')}"
        ),
        "",
        "## Test Directories",
        "",
    ]
    for value in _list(payload.get("test_directories")):
        lines.append(f"- `{_markdown_cell(value)}`")
    if not _list(payload.get("test_directories")):
        lines.append("- none")
    lines.extend(["", "## Test Command Candidates", ""])
    candidates = _list(payload.get("test_command_candidates"))
    for candidate_value in candidates:
        candidate = _dict(candidate_value)
        lines.append(
            "- "
            f"`{_markdown_cell(candidate.get('command') or '')}` "
            f"runner=`{_markdown_cell(candidate.get('runner') or 'unknown')}` "
            f"reason=`{_markdown_cell(candidate.get('reason') or 'none')}`"
        )
    if not candidates:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _render_simple_artifact_markdown(title: str, payload: dict[str, Any]) -> str:
    lines = [f"# {title}", "", "| Field | Value |", "| --- | --- |"]
    for key, value in payload.items():
        rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        if len(rendered) > 240:
            rendered = rendered[:237] + "..."
        lines.append(f"| {_markdown_cell(key)} | {_markdown_cell(rendered)} |")
    if not payload:
        lines.append("| none | none |")
    return "\n".join(lines) + "\n"


def _source_summary(profile: dict[str, Any], structure: dict[str, Any]) -> dict[str, Any]:
    imported = _int(profile.get("imported_source_count", 0))
    analyzed = _int(structure.get("analyzed_file_count", imported))
    return {
        "imported_source_count": imported,
        "analyzed_file_count": analyzed,
        "has_python_sources": imported > 0 or analyzed > 0,
        "python_source_ratio": _float(profile.get("python_source_ratio", 0.0)),
    }


def _layout_summary(profile: dict[str, Any], structure: dict[str, Any]) -> dict[str, Any]:
    package_structure = _dict(structure.get("package_structure"))
    package_roots = [
        str(item)
        for item in _list(profile.get("package_roots", package_structure.get("package_roots")))
    ]
    src_layout_packages = [
        str(item)
        for item in _list(
            profile.get(
                "src_layout_packages",
                package_structure.get("src_layout_packages"),
            )
        )
    ]
    return {
        "package_roots": package_roots,
        "src_layout_packages": src_layout_packages,
        "src_layout": bool(src_layout_packages),
        "recommended_target_prefix": str(
            profile.get(
                "recommended_target_prefix",
                package_structure.get("recommended_target_prefix") or "",
            )
        ),
    }


def _tests_summary(
    profile: dict[str, Any],
    structure: dict[str, Any],
    test_discovery: dict[str, Any],
    runners: list[str],
) -> dict[str, Any]:
    test_structure = _dict(structure.get("test_structure"))
    test_source_count = _int(
        test_discovery.get(
            "test_source_count",
            profile.get("test_source_count", test_structure.get("test_source_count", 0)),
        )
    )
    return {
        "test_source_count": test_source_count,
        "has_tests": test_source_count > 0,
        "test_framework_signals": [
            str(item)
            for item in _list(
                test_discovery.get(
                    "test_framework_signals",
                    profile.get("test_framework_signals", test_structure.get("test_framework_signals")),
                )
            )
        ],
        "recommended_test_command": str(
            test_discovery.get(
                "recommended_test_command",
                profile.get("recommended_test_command", test_structure.get("recommended_test_command") or ""),
            )
        ),
        "runners": runners,
        "test_command_candidate_count": len(runners),
    }


def _environment_summary(environment: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": str(
            environment.get("status")
            or summary.get("repository_test_environment_status")
            or ""
        ),
        "reason": str(
            environment.get("reason")
            or summary.get("repository_test_environment_reason")
            or ""
        ),
        "tool_available": environment.get(
            "tool_available",
            summary.get("repository_test_tool_available"),
        ),
        "recommended_install_command": str(
            environment.get("recommended_install_command")
            or summary.get("recommended_install_command")
            or ""
        ),
    }


def _execution_summary(
    execution_plan: dict[str, Any],
    execution_result: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "planned_command": str(
            execution_plan.get("command")
            or summary.get("planned_repository_test_command")
            or ""
        ),
        "runner": str(
            execution_plan.get("runner")
            or summary.get("planned_repository_test_runner")
            or ""
        ),
        "status": str(
            execution_result.get("status")
            or summary.get("planned_repository_test_result_status")
            or ""
        ),
        "failure_category": str(
            execution_result.get("failure_category")
            or summary.get("planned_repository_test_failure_category")
            or ""
        ),
        "failure_signal": str(
            execution_result.get("failure_signal")
            or summary.get("planned_repository_test_failure_signal")
            or ""
        ),
    }


def _policy_trace_summary(policy_trace: dict[str, Any]) -> dict[str, Any]:
    selected = _dict(policy_trace.get("selected_action"))
    canonical = _dict(policy_trace.get("canonical_action"))
    return {
        "present": bool(policy_trace),
        "status": str(policy_trace.get("status") or policy_trace.get("policy_status") or ""),
        "selected_action": str(
            selected.get("id")
            or selected.get("action_id")
            or policy_trace.get("selected_action_id")
            or ""
        ),
        "canonical_action": str(
            canonical.get("id")
            or canonical.get("action_id")
            or policy_trace.get("canonical_action_id")
            or ""
        ),
        "loop": [
            str(item)
            for item in _list(policy_trace.get("loop"))
        ],
    }


def _project_config_files(
    profile: dict[str, Any],
    structure: dict[str, Any],
    test_discovery: dict[str, Any],
) -> list[str]:
    project_config = _dict(structure.get("project_config"))
    values = _list(profile.get("project_config_files"))
    if not values:
        values = _list(project_config.get("project_config_files"))
    if not values:
        values = _list(test_discovery.get("project_config_files"))
    return sorted({Path(str(item)).name for item in values if str(item)})


def _test_runners(
    profile: dict[str, Any],
    structure: dict[str, Any],
    test_discovery: dict[str, Any],
    execution_plan: dict[str, Any],
) -> list[str]:
    candidates = []
    test_structure = _dict(structure.get("test_structure"))
    for source in (
        test_discovery.get("test_command_candidates"),
        profile.get("test_command_candidates"),
        test_structure.get("test_command_candidates"),
        execution_plan.get("candidate_commands"),
    ):
        for item in _list(source):
            row = _dict(item)
            runner = str(row.get("runner") or row.get("command_runner") or "")
            command = str(row.get("command") or "")
            if not runner and command:
                runner = _runner_from_command(command)
            if runner:
                candidates.append(runner)
    planned_runner = str(execution_plan.get("runner") or "")
    if planned_runner:
        candidates.append(planned_runner)
    return sorted(dict.fromkeys(candidates))


def _runner_from_command(command: str) -> str:
    lowered = command.lower()
    for runner in ("pytest", "unittest", "tox", "nox"):
        if runner in lowered:
            return runner
    return ""


def _blockers(
    summary: dict[str, Any],
    test_discovery: dict[str, Any],
    environment: dict[str, Any],
    execution_plan: dict[str, Any],
    execution_result: dict[str, Any],
) -> list[str]:
    blockers = []
    for source in (summary, test_discovery, environment, execution_plan, execution_result):
        for key in ("blocker", "doctor_blocker", "repository_test_setup_doctor_blocker"):
            value = str(_dict(source).get(key) or "")
            if value:
                blockers.append(value)
    if not blockers and not _int(test_discovery.get("test_source_count", 0)):
        blockers.append("oracle:no_tests")
    return sorted(dict.fromkeys(blockers))


def _run_name(payload: dict[str, Any], summary: dict[str, Any], report_path: Path) -> str:
    return str(
        payload.get("name")
        or summary.get("name")
        or summary.get("run_name")
        or report_path.parent.name
    )


def _repo_name(payload: dict[str, Any], summary: dict[str, Any]) -> str:
    repo = str(summary.get("repo") or "")
    if repo:
        return repo
    owner = str(payload.get("owner") or "")
    name = str(payload.get("repo") or "")
    if owner and name:
        return f"{owner}/{name}"
    return str(payload.get("repo_spec") or summary.get("repo_spec") or "")


def _input_kind(repo_spec: str) -> str:
    if repo_spec.startswith("http://") or repo_spec.startswith("https://"):
        return "github_url"
    if "/" in repo_spec and "\\" not in repo_spec and not repo_spec.startswith("."):
        return "owner_repo"
    if repo_spec:
        return "local_path"
    return "unknown"


def _combined_text(*values: Any) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True).lower()


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        value = Path(path)
        if not value.is_file():
            return {}
        return _dict(json.loads(value.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _markdown_cell(value: Any) -> str:
    text = str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a P6 GitHub onboarding matrix from repo intelligence reports.",
    )
    parser.add_argument("reports", nargs="+", help="Report JSON files or output directories.")
    parser.add_argument("--output-dir", default="", help="Write matrix artifacts here.")
    parser.add_argument("--required-case-count", type=int, default=10)
    parser.add_argument(
        "--backfill-derived-artifacts",
        action="store_true",
        help=(
            "Write missing onboarding audit artifacts that can be derived from "
            "existing repo intelligence reports before building the matrix."
        ),
    )
    parser.add_argument(
        "--dry-run-backfill",
        action="store_true",
        help="Plan derived artifact backfill without writing files.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="Console output format.",
    )
    parser.add_argument("--require-success", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    backfill = {}
    if args.backfill_derived_artifacts:
        backfill = backfill_github_onboarding_artifacts(
            [Path(item) for item in args.reports],
            dry_run=bool(args.dry_run_backfill),
        )
    matrix = build_github_onboarding_matrix(
        [Path(item) for item in args.reports],
        required_case_count=args.required_case_count,
    )
    if backfill:
        matrix["backfill"] = backfill
    if args.output_dir:
        write_github_onboarding_matrix_artifacts(matrix, args.output_dir)
    if args.format == "markdown":
        print(render_github_onboarding_matrix_markdown(matrix))
    else:
        print(json.dumps(matrix, indent=2, ensure_ascii=False))
    raise SystemExit(0 if matrix["status"] == "pass" or not args.require_success else 1)


if __name__ == "__main__":  # pragma: no cover
    main()
